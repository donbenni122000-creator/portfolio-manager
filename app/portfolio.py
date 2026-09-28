"""Portfolio state: clients, positions, targets and live valuation."""
from __future__ import annotations

import json
from datetime import date

from . import db, risk_profile
from .market_data import DataError, get_provider


def get_client(client_id: int) -> dict:
    c = db.query_one("SELECT * FROM clients WHERE id=?", (client_id,))
    if not c:
        raise KeyError(f"Client {client_id} not found")
    c["questionnaire"] = json.loads(c["questionnaire"]) if c.get("questionnaire") else None
    c["beneficiaries"] = json.loads(c["beneficiaries"]) if isinstance(c.get("beneficiaries"), str) else (c.get("beneficiaries") or [])
    return c


def list_clients() -> list[dict]:
    return db.query("SELECT id, name, email, risk_profile, risk_score, starting_cash, cash, created_at FROM clients ORDER BY id")


def create_client(name: str, email: str | None, answers: dict, starting_cash: float,
                  profile_override: str | None = None, account_type: str | None = None, beneficiaries=None,
                  phone: str | None = None, objective: str | None = None, model_id: int | None = None) -> dict:
    from . import accounts
    account_type, bens = accounts.validate(account_type, beneficiaries)
    if starting_cash <= 0:
        raise ValueError("Starting cash must be positive")
    res = risk_profile.score_questionnaire(answers)
    profile = risk_profile.profile_by_name(profile_override) if profile_override else res["profile_detail"]
    from . import billing, model_library
    fee_id = billing.default_schedule_id()
    core = model_library.core_model_id(profile["name"])
    with db.tx() as c:
        cur = c.execute(
            """INSERT INTO clients(name,email,risk_score,risk_profile,questionnaire,starting_cash,cash,
               max_position,created_at,fee_schedule_id,last_interest_date,model_id,account_type,beneficiaries,phone,objective)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name, email, res["score"], profile["name"], json.dumps(answers), starting_cash, starting_cash,
             profile["max_position"], db.now_iso(), fee_id, date.today().isoformat(), core, account_type, bens, phone, objective))
        cid = cur.lastrowid
        c.execute("INSERT INTO cash_flows(client_id,amount,note,created_at) VALUES(?,?,?,?)",
                  (cid, starting_cash, "Initial paper funding", db.now_iso()))
    set_targets(cid, risk_profile.model_targets(profile["name"]))
    if model_id:
        model_library.assign(cid, model_id)
    return get_client(cid)


def update_client(client_id: int, fields: dict) -> dict:
    allowed = {"name", "email", "risk_profile", "drift_abs_band", "drift_rel_band", "cash_target",
               "stop_loss_pct", "max_position", "notes", "fee_schedule_id", "lot_method", "st_tax_rate",
               "lt_tax_rate", "model_id", "account_type", "beneficiaries", "phone", "objective", "options_level",
               "margin_rate", "sbloc_rate"}
    if "account_type" in fields or "beneficiaries" in fields:
        from . import accounts
        cur = get_client(client_id)
        at, bens = accounts.validate(fields.get("account_type") or cur["account_type"],
                                     fields.get("beneficiaries") if fields.get("beneficiaries") is not None else cur["beneficiaries"])
        fields = {**fields, "account_type": at, "beneficiaries": bens}
        if not accounts.ACCOUNT_TYPES[at]["margin"] and (cur["margin_enabled"] or cur["sec_lending"]):
            raise ValueError("Turn off margin and securities lending before converting to a retirement account")
    if "lot_method" in fields and fields["lot_method"] and fields["lot_method"].upper() not in ("FIFO", "LIFO", "HIFO"):
        raise ValueError("lot_method must be FIFO, LIFO or HIFO")
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "risk_profile" in sets:
        risk_profile.profile_by_name(sets["risk_profile"])
    if sets:
        with db.tx() as c:
            c.execute(f"UPDATE clients SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                      (*sets.values(), client_id))
    return get_client(client_id)


def delete_client(client_id: int) -> None:
    with db.tx() as c:
        for t in ("targets", "positions", "orders", "cash_flows", "snapshots", "invoices", "ledger", "lots",
                  "realized", "goals"):
            c.execute(f"DELETE FROM {t} WHERE client_id=?", (client_id,))
        c.execute("DELETE FROM clients WHERE id=?", (client_id,))


def deposit(client_id: int, amount: float, note: str = "") -> dict:
    client = get_client(client_id)
    if amount < 0 and client["cash"] + amount < -1e-6:
        raise ValueError("Withdrawal exceeds available cash")
    with db.tx() as c:
        c.execute("UPDATE clients SET cash = cash + ? WHERE id=?", (amount, client_id))
        c.execute("INSERT INTO cash_flows(client_id,amount,note,created_at) VALUES(?,?,?,?)",
                  (client_id, amount, note or ("Deposit" if amount > 0 else "Withdrawal"), db.now_iso()))
    return get_client(client_id)


# ---------------------------------------------------------------- targets
def get_targets(client_id: int) -> dict[str, float]:
    return {r["symbol"]: r["weight"] for r in db.query("SELECT symbol, weight FROM targets WHERE client_id=?", (client_id,))}


def set_targets(client_id: int, targets: dict[str, float]) -> dict[str, float]:
    targets = {k.upper(): float(v) for k, v in targets.items() if float(v) > 0}
    total = sum(targets.values())
    if total <= 0:
        raise ValueError("Targets must contain at least one positive weight")
    if abs(total - 1) > 0.005:
        raise ValueError(f"Target weights must sum to 100% (got {total:.1%})")
    targets = risk_profile.normalize(targets)
    with db.tx() as c:
        c.execute("DELETE FROM targets WHERE client_id=?", (client_id,))
        for s, w in targets.items():
            c.execute("INSERT INTO targets(client_id,symbol,weight,asset_class) VALUES(?,?,?,?)",
                      (client_id, s, w, risk_profile.asset_class(s)))
    return targets


# ---------------------------------------------------------------- valuation
def get_positions(client_id: int) -> list[dict]:
    return db.query("SELECT * FROM positions WHERE client_id=? AND qty > 1e-9 ORDER BY symbol", (client_id,))


def valuation(client_id: int) -> dict:
    client = get_client(client_id)
    positions = get_positions(client_id)
    # targets are stored as % of the invested sleeve; the effective weight reserves the cash buffer
    targets = {s: w * (1 - client["cash_target"]) for s, w in get_targets(client_id).items()}
    symbols = sorted({p["symbol"] for p in positions} | set(targets))
    provider = get_provider()
    quotes = provider.quotes(symbols) if symbols else {}
    missing = [s for s in symbols if s not in quotes]

    holdings, invested, day_change = [], 0.0, 0.0
    for p in positions:
        q = quotes.get(p["symbol"])
        price = q["price"] if q else p["avg_cost"]
        value = p["qty"] * price
        invested += value
        if q and q.get("prev_close"):
            day_change += p["qty"] * (price - q["prev_close"])
        holdings.append({
            "symbol": p["symbol"], "name": q["name"] if q else p["symbol"], "qty": p["qty"],
            "avg_cost": p["avg_cost"], "price": price, "value": value,
            "cost_basis": p["qty"] * p["avg_cost"],
            "unrealized": value - p["qty"] * p["avg_cost"],
            "unrealized_pct": (price / p["avg_cost"] - 1) if p["avg_cost"] else 0,
            "day_change_pct": q["change_pct"] / 100 if q else 0,
            "realized_pnl": p["realized_pnl"], "asset_class": risk_profile.asset_class(p["symbol"]),
            "stale_price": q is None,
        })
    from . import options as _opt
    opts = _opt.positions(client_id)
    options_value = sum(o["market_value"] for o in opts)
    equity = client["cash"] + invested + options_value
    for h in holdings:
        h["weight"] = h["value"] / equity if equity else 0
        h["target_weight"] = targets.get(h["symbol"], 0.0)
        h["drift"] = h["weight"] - h["target_weight"]
    held = {h["symbol"] for h in holdings}
    for s, w in targets.items():
        if s not in held:
            q = quotes.get(s)
            holdings.append({"symbol": s, "name": q["name"] if q else s, "qty": 0, "avg_cost": 0,
                             "price": q["price"] if q else None, "value": 0, "cost_basis": 0, "unrealized": 0,
                             "unrealized_pct": 0, "day_change_pct": q["change_pct"] / 100 if q else 0,
                             "realized_pnl": 0, "asset_class": risk_profile.asset_class(s), "weight": 0,
                             "target_weight": w, "drift": -w, "stale_price": q is None})
    holdings.sort(key=lambda h: -h["value"])

    net_flows = db.query_one("SELECT COALESCE(SUM(amount),0) AS s FROM cash_flows WHERE client_id=?", (client_id,))["s"]
    realized = db.query_one("SELECT COALESCE(SUM(realized_pnl),0) AS s FROM positions WHERE client_id=?", (client_id,))["s"]
    commissions = db.query_one("SELECT COALESCE(SUM(commission),0) AS s FROM orders WHERE client_id=? AND status='filled'",
                               (client_id,))["s"]
    total_pnl = equity - net_flows
    return {
        "client_id": client_id, "cash": client["cash"], "cash_weight": client["cash"] / equity if equity else 1,
        "cash_target": client["cash_target"], "invested": invested, "equity": equity,
        "net_deposits": net_flows, "total_pnl": total_pnl,
        "total_return": total_pnl / net_flows if net_flows else 0,
        "unrealized_pnl": sum(h["unrealized"] for h in holdings), "realized_pnl": realized,
        "commissions": commissions, "day_change": day_change,
        "day_change_pct": day_change / (equity - day_change) if equity - day_change else 0,
        "holdings": holdings, "missing_quotes": missing, "options": opts, "options_value": options_value,
        "loan_balance": _loan_balance(client_id), "net_worth": equity - _loan_balance(client_id),
    }


def _loan_balance(client_id: int) -> float:
    r = db.query_one("SELECT COALESCE(SUM(balance),0) AS b FROM loans WHERE client_id=? AND status='open'", (client_id,))
    return r["b"] if r else 0.0


def record_snapshot(client_id: int, val: dict | None = None) -> None:
    val = val or valuation(client_id)
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO snapshots(client_id,date,equity,cash,net_flows) VALUES(?,?,?,?,?)",
                  (client_id, date.today().isoformat(), val["equity"], val["cash"], val["net_deposits"]))


def equity_curve(client_id: int) -> list[dict]:
    return db.query("SELECT date, equity, cash, net_flows FROM snapshots WHERE client_id=? ORDER BY date", (client_id,))


def time_weighted_index(client_id: int) -> list[dict]:
    """Deposit/withdrawal-neutral performance index (starts at 100) from daily snapshots."""
    snaps = equity_curve(client_id)
    out, idx = [], 100.0
    for i, s in enumerate(snaps):
        if i > 0:
            prev = snaps[i - 1]
            flow = s["net_flows"] - prev["net_flows"]
            if prev["equity"] > 0:
                idx *= (s["equity"] - flow) / prev["equity"]
        out.append({"date": s["date"], "index": idx, "equity": s["equity"]})
    return out


def safe_quote(symbol: str):
    try:
        return get_provider().quote(symbol)
    except DataError:
        return None
