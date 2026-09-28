"""Advisory fee billing and high-yield cash interest.

Fees
  * Schedules are *marginal* tiers (like tax brackets), e.g. 1.00% on the first $1M, 0.75% on the next $4M...
  * Billed in arrears on the average daily portfolio value (from daily snapshots) for the period,
    pro-rated by days (actual/365), and deducted from cash. If cash is short the invoice stays unpaid
    and an alert asks you to raise cash.
Cash interest
  * Uninvested cash earns CASH_INTEREST_RATE (annual), accrued daily and compounded; posted to the ledger.
  * Interest and fees are performance items (not deposits), so returns are shown net of fees.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import config, db, portfolio

BUILTIN_SCHEDULES = [
    ("Standard 1.00%", [[None, 0.0100]], "Flat 1.00% per year on all assets"),
    ("Tiered", [[1_000_000, 0.0100], [5_000_000, 0.0075], [None, 0.0050]],
     "1.00% on the first $1M, 0.75% up to $5M, 0.50% above"),
    ("Low-cost 0.50%", [[None, 0.0050]], "Flat 0.50% per year"),
    ("No fee", [[None, 0.0]], "Household / family accounts"),
]


def seed() -> None:
    if db.query_one("SELECT COUNT(*) AS n FROM fee_schedules")["n"]:
        return
    with db.tx() as c:
        for name, tiers, desc in BUILTIN_SCHEDULES:
            c.execute("INSERT INTO fee_schedules(name,tiers,description,builtin) VALUES(?,?,?,1)", (name, json.dumps(tiers), desc))


def schedules() -> list[dict]:
    seed()
    out = []
    for r in db.query("SELECT * FROM fee_schedules ORDER BY builtin DESC, id"):
        r["tiers"] = json.loads(r["tiers"])
        r["clients"] = db.query_one("SELECT COUNT(*) AS n FROM clients WHERE fee_schedule_id=?", (r["id"],))["n"]
        out.append(r)
    return out


def save_schedule(name: str, tiers: list, description: str = "", schedule_id: int | None = None) -> dict:
    tiers = [[None if t[0] in (None, "", 0) else float(t[0]), float(t[1])] for t in tiers]
    if not tiers or any(t[1] < 0 or t[1] > 0.05 for t in tiers):
        raise ValueError("Each tier needs an annual rate between 0% and 5%")
    bounds = [t[0] for t in tiers[:-1]]
    if any(b is None for b in bounds) or bounds != sorted(bounds) or tiers[-1][0] is not None:
        raise ValueError("Tier upper bounds must increase, and the last tier must be open-ended")
    with db.tx() as c:
        if schedule_id:
            row = db.query_one("SELECT builtin FROM fee_schedules WHERE id=?", (schedule_id,))
            if not row:
                raise KeyError("Fee schedule not found")
            if row["builtin"]:
                raise ValueError("Built-in schedules can't be edited - create a new one")
            c.execute("UPDATE fee_schedules SET name=?, tiers=?, description=? WHERE id=?",
                      (name, json.dumps(tiers), description, schedule_id))
        else:
            schedule_id = c.execute("INSERT INTO fee_schedules(name,tiers,description,builtin) VALUES(?,?,?,0)",
                                    (name, json.dumps(tiers), description)).lastrowid
    return next(s for s in schedules() if s["id"] == schedule_id)


def default_schedule_id() -> int | None:
    seed()
    r = db.query_one("SELECT id FROM fee_schedules WHERE name=?", (config.DEFAULT_FEE_SCHEDULE,))
    return r["id"] if r else None


def client_schedule(client: dict) -> dict:
    seed()
    sid = client.get("fee_schedule_id") or default_schedule_id()
    r = db.query_one("SELECT * FROM fee_schedules WHERE id=?", (sid,))
    r["tiers"] = json.loads(r["tiers"])
    return r


def annual_fee(aum: float, tiers: list) -> float:
    fee, lower = 0.0, 0.0
    for upper, rate in tiers:
        top = aum if upper is None else min(aum, upper)
        if top > lower:
            fee += (top - lower) * rate
        if upper is None or aum <= upper:
            break
        lower = upper
    return fee


# ------------------------------------------------------------------ periods
def quarter_bounds(d: date) -> tuple[date, date]:
    q0 = date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)
    q1 = date(q0.year + (q0.month + 2) // 12, (q0.month + 2) % 12 + 1, 1) - timedelta(days=1)
    return q0, q1


def default_period() -> tuple[date, date]:
    """Previous full quarter if we're in its first days, otherwise quarter-to-date."""
    today = date.today()
    q0, _ = quarter_bounds(today)
    if (today - q0).days < 5:
        p1 = q0 - timedelta(days=1)
        return quarter_bounds(p1)[0], p1
    return q0, today


def compute_fee(client_id: int, start: date, end: date) -> dict:
    client = portfolio.get_client(client_id)
    sched = client_schedule(client)
    opened = date.fromisoformat(client["created_at"][:10])
    last = db.query_one("SELECT MAX(period_end) AS e FROM invoices WHERE client_id=?", (client_id,))["e"]
    # never bill the same day twice: start after the last invoiced period
    s = max(start, opened, date.fromisoformat(last) + timedelta(days=1) if last else start)
    if s > end:
        return {"client_id": client_id, "name": client["name"], "days": 0, "avg_aum": 0, "fee": 0, "annual_fee": 0,
                "effective_rate": 0, "schedule": sched["name"], "period_start": start.isoformat(), "period_end": end.isoformat(),
                "already_billed": True}
    snaps = db.query("SELECT equity FROM snapshots WHERE client_id=? AND date BETWEEN ? AND ?",
                     (client_id, s.isoformat(), end.isoformat()))
    avg = sum(r["equity"] for r in snaps) / len(snaps) if snaps else portfolio.valuation(client_id)["equity"]
    days = (end - s).days + 1
    yearly = annual_fee(avg, sched["tiers"])
    fee = round(yearly * days / 365, 2)
    return {"client_id": client_id, "name": client["name"], "period_start": s.isoformat(), "period_end": end.isoformat(),
            "days": days, "avg_aum": round(avg, 2), "annual_fee": round(yearly, 2),
            "effective_rate": yearly / avg if avg else 0, "fee": fee, "schedule": sched["name"],
            "already_billed": False}


def preview(start: date | None = None, end: date | None = None) -> dict:
    if not start or not end:
        start, end = default_period()
    rows = [compute_fee(c["id"], start, end) for c in portfolio.list_clients()]
    return {"period_start": start.isoformat(), "period_end": end.isoformat(), "rows": rows,
            "total_fee": round(sum(r["fee"] for r in rows if not r["already_billed"]), 2)}


def run_billing(start: date | None = None, end: date | None = None, client_ids: list[int] | None = None) -> dict:
    if not start or not end:
        start, end = default_period()
    results = []
    for c in portfolio.list_clients():
        if client_ids and c["id"] not in client_ids:
            continue
        f = compute_fee(c["id"], start, end)
        if f["already_billed"] or f["days"] == 0:
            results.append({**f, "status": "skipped"})
            continue
        client = portfolio.get_client(c["id"])
        paid = client["cash"] + 1e-6 >= f["fee"]
        with db.tx() as con:
            con.execute("""INSERT INTO invoices(client_id,period_start,period_end,days,avg_aum,effective_rate,fee,status,schedule,created_at,paid_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (c["id"], f["period_start"], f["period_end"], f["days"], f["avg_aum"], f["effective_rate"], f["fee"],
                         "paid" if paid else "unpaid", f["schedule"], db.now_iso(), db.now_iso() if paid else None))
            if paid and f["fee"] > 0:
                con.execute("UPDATE clients SET cash = cash - ? WHERE id=?", (f["fee"], c["id"]))
                con.execute("INSERT INTO ledger(client_id,kind,amount,note,created_at) VALUES(?,?,?,?,?)",
                            (c["id"], "fee", -f["fee"], f"Advisory fee {f['period_start']} to {f['period_end']}", db.now_iso()))
        results.append({**f, "status": "paid" if paid else "unpaid"})
    return {"period_start": start.isoformat(), "period_end": end.isoformat(), "results": results}


def pay_invoice(invoice_id: int) -> dict:
    inv = db.query_one("SELECT * FROM invoices WHERE id=?", (invoice_id,))
    if not inv:
        raise KeyError("Invoice not found")
    if inv["status"] != "unpaid":
        raise ValueError(f"Invoice is already {inv['status']}")
    client = portfolio.get_client(inv["client_id"])
    if client["cash"] + 1e-6 < inv["fee"]:
        raise ValueError(f"Not enough cash (${client['cash']:,.2f}) - sell something to raise ${inv['fee']:,.2f} first")
    with db.tx() as c:
        c.execute("UPDATE clients SET cash = cash - ? WHERE id=?", (inv["fee"], inv["client_id"]))
        c.execute("UPDATE invoices SET status='paid', paid_at=? WHERE id=?", (db.now_iso(), invoice_id))
        c.execute("INSERT INTO ledger(client_id,kind,amount,note,created_at) VALUES(?,?,?,?,?)",
                  (inv["client_id"], "fee", -inv["fee"], f"Advisory fee {inv['period_start']} to {inv['period_end']}", db.now_iso()))
    return db.query_one("SELECT * FROM invoices WHERE id=?", (invoice_id,))


def waive_invoice(invoice_id: int) -> dict:
    with db.tx() as c:
        c.execute("UPDATE invoices SET status='waived' WHERE id=? AND status='unpaid'", (invoice_id,))
    return db.query_one("SELECT * FROM invoices WHERE id=?", (invoice_id,))


def invoices(client_id: int | None = None) -> list[dict]:
    if client_id:
        return db.query("SELECT * FROM invoices WHERE client_id=? ORDER BY period_end DESC, id DESC", (client_id,))
    return db.query("""SELECT i.*, c.name FROM invoices i JOIN clients c ON c.id=i.client_id
                       ORDER BY i.period_end DESC, i.id DESC LIMIT 500""")


def firm_summary() -> dict:
    rows = []
    for c in portfolio.list_clients():
        client = portfolio.get_client(c["id"])
        eq = portfolio.valuation(c["id"])["equity"]
        sched = client_schedule(client)
        yearly = annual_fee(eq, sched["tiers"])
        rows.append({"client_id": c["id"], "name": c["name"], "aum": eq, "schedule": sched["name"],
                     "annual_fee": yearly, "effective_rate": yearly / eq if eq else 0})
    aum = sum(r["aum"] for r in rows)
    rev = sum(r["annual_fee"] for r in rows)
    collected = db.query_one("SELECT COALESCE(SUM(fee),0) AS s FROM invoices WHERE status='paid'")["s"]
    unpaid = db.query_one("SELECT COALESCE(SUM(fee),0) AS s FROM invoices WHERE status='unpaid'")["s"]
    return {"total_aum": aum, "projected_annual_revenue": rev, "blended_rate": rev / aum if aum else 0,
            "collected": collected, "outstanding": unpaid, "clients": rows}


# ------------------------------------------------------------------ cash interest
def accrue_interest(client_id: int, today: date | None = None) -> float:
    today = today or date.today()
    client = portfolio.get_client(client_id)
    last = date.fromisoformat((client.get("last_interest_date") or today.isoformat())[:10])
    days = (today - last).days
    if days <= 0:
        return 0.0
    interest = 0.0
    if client["cash"] > 0 and config.CASH_INTEREST_RATE > 0:
        interest = round(client["cash"] * ((1 + config.CASH_INTEREST_RATE) ** (days / 365) - 1), 2)
    with db.tx() as c:
        from . import lending
        lending.accrue(c, client, days)       # margin interest, line-of-credit interest, securities-lending income
        if interest > 0:
            c.execute("UPDATE clients SET cash = cash + ? WHERE id=?", (interest, client_id))
            c.execute("INSERT INTO ledger(client_id,kind,amount,note,created_at) VALUES(?,?,?,?,?)",
                      (client_id, "interest", interest,
                       f"Cash interest {days} day(s) @ {config.CASH_INTEREST_RATE:.2%} APY", db.now_iso()))
        c.execute("UPDATE clients SET last_interest_date=? WHERE id=?", (today.isoformat(), client_id))
    return interest


def accrue_all() -> None:
    for c in portfolio.list_clients():
        accrue_interest(c["id"])


def activity(client_id: int, limit: int = 200) -> list[dict]:
    """Unified cash activity: deposits/withdrawals, interest, fees, trades."""
    rows = [{"date": r["created_at"], "type": "deposit" if r["amount"] >= 0 else "withdrawal", "amount": r["amount"],
             "description": r["note"] or ""} for r in db.query("SELECT * FROM cash_flows WHERE client_id=?", (client_id,))]
    rows += [{"date": r["created_at"], "type": r["kind"], "amount": r["amount"], "description": r["note"] or ""}
             for r in db.query("SELECT * FROM ledger WHERE client_id=?", (client_id,))]
    rows += [{"date": r["filled_at"], "type": r["side"], "amount": (-1 if r["side"] == "buy" else 1) * r["qty"] * r["fill_price"] - (r["commission"] or 0),
              "description": f"{r['side'].upper()} {r['qty']:g} {r['symbol']} @ {r['fill_price']:,.2f}"}
             for r in db.query("SELECT * FROM orders WHERE client_id=? AND status='filled'", (client_id,))]
    rows.sort(key=lambda r: r["date"] or "", reverse=True)
    return rows[:limit]


def interest_summary(client_id: int) -> dict:
    ytd = db.query_one("""SELECT COALESCE(SUM(amount),0) AS s FROM ledger WHERE client_id=? AND kind='interest'
                          AND substr(created_at,1,4)=?""", (client_id, str(date.today().year)))["s"]
    fees = db.query_one("""SELECT COALESCE(SUM(-amount),0) AS s FROM ledger WHERE client_id=? AND kind='fee'
                           AND substr(created_at,1,4)=?""", (client_id, str(date.today().year)))["s"]
    return {"apy": config.CASH_INTEREST_RATE, "interest_ytd": ytd, "fees_ytd": fees}
