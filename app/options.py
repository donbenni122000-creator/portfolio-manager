"""Simulated options trading.

Prices are THEORETICAL: Black-Scholes using the underlying's 1-year realised volatility (with a small
smile), the risk-free rate, and a bid/ask spread. Good for learning and paper strategies; real option
markets will differ.

Allowed (no naked short options):
  Level 1  covered calls (STO call needs 100 shares per contract) and cash-secured puts (STO put reserves
           strike x 100 cash per contract); closing trades.
  Level 2  Level 1 + buying calls and puts (BTO).
At expiry: out-of-the-money options expire worthless; in-the-money long options are cash-settled at
intrinsic value; short calls are assigned (shares sold at the strike); short puts are assigned (shares
bought at the strike).
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from . import accounts, analytics, config, db, portfolio
from .market_data import DataError, get_provider

CONTRACT = 100
ACTIONS = {"BTO": "Buy to open", "STC": "Sell to close", "STO": "Sell to open", "BTC": "Buy to close"}


# ------------------------------------------------------------------ pricing
def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _npdf(x: float) -> float:
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def black_scholes(S: float, K: float, T: float, r: float, sigma: float, typ: str) -> dict:
    if T <= 0 or sigma <= 0:
        intrinsic = max(0.0, S - K) if typ == "C" else max(0.0, K - S)
        return {"price": intrinsic, "delta": (1.0 if S > K else 0.0) if typ == "C" else (-1.0 if S < K else 0.0),
                "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    d1 = (math.log(S / K) + (r + sigma ** 2 / 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if typ == "C":
        price = S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
        delta = _ncdf(d1)
        theta = (-S * _npdf(d1) * sigma / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * _ncdf(d2)) / 365
    else:
        price = K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)
        delta = _ncdf(d1) - 1
        theta = (-S * _npdf(d1) * sigma / (2 * math.sqrt(T)) + r * K * math.exp(-r * T) * _ncdf(-d2)) / 365
    return {"price": price, "delta": delta, "gamma": _npdf(d1) / (S * sigma * math.sqrt(T)),
            "theta": theta, "vega": S * _npdf(d1) * math.sqrt(T) / 100}


_vol_cache: dict[str, tuple[float, float]] = {}


def base_vol(symbol: str) -> float:
    import time
    hit = _vol_cache.get(symbol)
    if hit and time.time() - hit[1] < 3600:
        return hit[0]
    try:
        v = analytics.stock_stats(symbol)["vol_1y"]
    except (DataError, KeyError, ValueError):
        v = 0.30
    v = min(1.5, max(0.12, v))
    _vol_cache[symbol] = (v, time.time())
    return v


def _years(expiry: str) -> float:
    # options expire at the close (16:00 ET ~ 20:00 UTC) on the expiry date
    exp = datetime.fromisoformat(expiry + "T20:00:00")
    return max(0.0, (exp - datetime.utcnow()).total_seconds() / (365 * 24 * 3600))


def quote_option(symbol: str, typ: str, strike: float, expiry: str, spot: float | None = None) -> dict:
    symbol, typ = symbol.upper(), typ.upper()[0]
    S = spot or get_provider().quote(symbol)["price"]
    sigma0 = base_vol(symbol)
    iv = sigma0 * (1 + 0.15 * abs(math.log(strike / S)))          # simple volatility smile
    g = black_scholes(S, strike, _years(expiry), config.RISK_FREE_RATE, iv, typ)
    mid = max(0.01, g["price"])
    half = max(0.01, 0.02 * mid + 0.01)
    return {"underlying": symbol, "type": typ, "strike": strike, "expiry": expiry, "spot": S, "iv": iv,
            "mid": round(mid, 2), "bid": round(max(0.0, mid - half), 2), "ask": round(mid + half, 2),
            "delta": g["delta"], "gamma": g["gamma"], "theta": g["theta"], "vega": g["vega"],
            "intrinsic": max(0.0, S - strike) if typ == "C" else max(0.0, strike - S)}


def expiries(n_weekly: int = 4, n_monthly: int = 6) -> list[str]:
    """Next few weekly Friday expiries plus the next monthly (third-Friday) expiries."""
    today = date.today()
    first_fri = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
    out = {(first_fri + timedelta(weeks=i)).isoformat() for i in range(n_weekly)}
    y, m, added = today.year, today.month, 0
    while added < n_monthly:
        first = date(y, m, 1)
        third_fri = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
        if third_fri > today:
            out.add(third_fri.isoformat())
            added += 1
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return sorted(out)


def _step(S: float) -> float:
    return 1 if S < 25 else 2.5 if S < 100 else 5 if S < 250 else 10 if S < 600 else 20


def chain(symbol: str, expiry: str | None = None) -> dict:
    symbol = symbol.upper()
    S = get_provider().quote(symbol)["price"]
    exps = expiries()
    expiry = expiry or exps[min(2, len(exps) - 1)]
    step = _step(S)
    center = round(S / step) * step
    strikes = [round(center + i * step, 2) for i in range(-10, 11) if center + i * step > 0]
    rows = []
    for k in strikes:
        c = quote_option(symbol, "C", k, expiry, S)
        p = quote_option(symbol, "P", k, expiry, S)
        rows.append({"strike": k, "call": c, "put": p, "itm_call": S > k})
    return {"underlying": symbol, "spot": S, "expiry": expiry, "expiries": exps, "days": max(0, (date.fromisoformat(expiry) - date.today()).days),
            "base_vol": base_vol(symbol), "rows": rows,
            "note": "Theoretical Black-Scholes prices from historical volatility - not live exchange quotes."}


# ------------------------------------------------------------------ positions & orders
def positions(client_id: int) -> list[dict]:
    rows = db.query("SELECT * FROM option_positions WHERE client_id=? AND qty != 0 ORDER BY expiry, underlying", (client_id,))
    for r in rows:
        try:
            q = quote_option(r["underlying"], r["opt_type"], r["strike"], r["expiry"])
        except DataError:
            q = {"mid": r["avg_price"], "delta": 0, "theta": 0, "spot": None, "iv": None}
        r["mark"] = q["mid"]
        r["market_value"] = r["qty"] * q["mid"] * CONTRACT
        r["cost"] = r["qty"] * r["avg_price"] * CONTRACT
        r["unrealized"] = r["market_value"] - r["cost"]
        r["delta"] = q["delta"] * r["qty"] * CONTRACT          # share-equivalent delta
        r["theta_day"] = q["theta"] * r["qty"] * CONTRACT
        r["spot"] = q.get("spot")
        r["days_left"] = (date.fromisoformat(r["expiry"]) - date.today()).days
        r["label"] = f"{r['underlying']} {r['expiry']} {r['strike']:g}{r['opt_type']}"
        r["strategy"] = ("Covered call" if r["qty"] < 0 and r["opt_type"] == "C" else
                         "Cash-secured put" if r["qty"] < 0 else "Long call" if r["opt_type"] == "C" else "Long put")
    return rows


def market_value(client_id: int) -> float:
    return sum(p["market_value"] for p in positions(client_id))


def csp_reserve(client_id: int) -> float:
    r = db.query_one("SELECT COALESCE(SUM(-qty * strike * 100),0) AS s FROM option_positions WHERE client_id=? AND qty<0 AND opt_type='P'",
                     (client_id,))
    return r["s"] if r else 0.0


def covered_shares(client_id: int, symbol: str) -> float:
    r = db.query_one("SELECT COALESCE(SUM(-qty*100),0) AS s FROM option_positions WHERE client_id=? AND underlying=? AND qty<0 AND opt_type='C'",
                     (client_id, symbol))
    return r["s"] if r else 0.0


def place(client_id: int, underlying: str, opt_type: str, strike: float, expiry: str, action: str, qty: int) -> dict:
    from . import lending, trading
    c = portfolio.get_client(client_id)
    underlying, opt_type, action = underlying.upper(), opt_type.upper()[0], action.upper()
    qty = int(qty)
    if action not in ACTIONS:
        raise ValueError("action must be BTO, STC, STO or BTC")
    if qty <= 0:
        raise ValueError("Quantity must be at least 1 contract")
    if opt_type not in ("C", "P"):
        raise ValueError("Type must be C (call) or P (put)")
    if date.fromisoformat(expiry) < date.today():
        raise ValueError("That contract has expired")
    level = c.get("options_level") or 0
    if level == 0:
        raise ValueError("Options aren't enabled for this account - choose an options level first")
    if action == "BTO" and level < 2:
        raise ValueError("Buying options needs Level 2 approval")
    q = quote_option(underlying, opt_type, strike, expiry)
    price = q["ask"] if action in ("BTO", "BTC") else q["bid"]
    if price <= 0:
        raise ValueError("No bid for this contract (worthless) - pick another strike")
    pos = db.query_one("SELECT * FROM option_positions WHERE client_id=? AND underlying=? AND opt_type=? AND strike=? AND expiry=?",
                       (client_id, underlying, opt_type, strike, expiry))
    cur = pos["qty"] if pos else 0
    fee = config.COMMISSION_PER_TRADE
    cash_effect = (-1 if action in ("BTO", "BTC") else 1) * price * CONTRACT * qty - fee
    free_cash = lending.buying_power(c) - trading._reserved_buy_cash(client_id) - csp_reserve(client_id)
    if action == "STC" and cur < qty:
        raise ValueError(f"You hold {max(cur, 0)} long contract(s) to sell")
    if action == "BTC" and -cur < qty:
        raise ValueError(f"You are short {max(-cur, 0)} contract(s) to buy back")
    if action in ("BTO", "BTC") and -cash_effect > free_cash + 1e-6:
        raise ValueError(f"Not enough cash: need ${-cash_effect:,.2f}, have ${free_cash:,.2f}")
    if action == "STO":
        if cur > 0:
            raise ValueError("Close the long position before selling this contract")
        if opt_type == "C":
            p = db.query_one("SELECT qty FROM positions WHERE client_id=? AND symbol=?", (client_id, underlying))
            owned = p["qty"] if p else 0
            need = covered_shares(client_id, underlying) + qty * CONTRACT
            if owned + 1e-9 < need:
                raise ValueError(f"Covered calls need {need:g} shares of {underlying}; you own {owned:g}")
        else:
            reserve = strike * CONTRACT * qty
            if reserve > free_cash + 1e-6:
                raise ValueError(f"A cash-secured put needs ${reserve:,.0f} of free cash; you have ${free_cash:,.0f}")
    sign = 1 if action in ("BTO", "BTC") else -1           # effect on qty
    new_qty = cur + sign * qty
    realized = None
    with db.tx() as con:
        if action in ("STC", "BTC"):
            realized = (price - pos["avg_price"]) * CONTRACT * qty * (1 if action == "STC" else -1) - fee
            con.execute("UPDATE option_positions SET qty=?, realized_pnl = realized_pnl + ? WHERE id=?", (new_qty, realized, pos["id"]))
        elif pos:
            avg = (abs(cur) * pos["avg_price"] + qty * price) / (abs(cur) + qty)
            con.execute("UPDATE option_positions SET qty=?, avg_price=? WHERE id=?", (new_qty, avg, pos["id"]))
        else:
            con.execute("""INSERT INTO option_positions(client_id,underlying,opt_type,strike,expiry,qty,avg_price,opened_at)
                           VALUES(?,?,?,?,?,?,?,?)""", (client_id, underlying, opt_type, strike, expiry, new_qty, price, db.now_iso()))
        con.execute("UPDATE clients SET cash = cash + ? WHERE id=?", (cash_effect, client_id))
        con.execute("""INSERT INTO option_trades(client_id,underlying,opt_type,strike,expiry,action,qty,price,cash_effect,realized_pnl,note,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (client_id, underlying, opt_type, strike, expiry, action, qty, price, cash_effect, realized,
                     ACTIONS[action], db.now_iso()))
    portfolio.record_snapshot(client_id)
    return {"action": action, "contract": f"{underlying} {expiry} {strike:g}{opt_type}", "qty": qty, "price": price,
            "cash_effect": cash_effect, "realized_pnl": realized, "position_qty": new_qty}


def trades(client_id: int, limit: int = 100) -> list[dict]:
    return db.query("SELECT * FROM option_trades WHERE client_id=? ORDER BY id DESC LIMIT ?", (client_id, limit))


def process_expirations(client_id: int | None = None, today: date | None = None) -> list[dict]:
    """Settle contracts whose expiry date has passed."""
    from . import trading
    today = today or date.today()
    sql = "SELECT * FROM option_positions WHERE qty != 0 AND expiry < ?" + (" AND client_id=?" if client_id else "")
    rows = db.query(sql, (today.isoformat(), client_id) if client_id else (today.isoformat(),))
    events = []
    for r in rows:
        try:
            S = get_provider().quote(r["underlying"])["price"]
        except DataError:
            continue
        intrinsic = max(0.0, S - r["strike"]) if r["opt_type"] == "C" else max(0.0, r["strike"] - S)
        q, cid = r["qty"], r["client_id"]
        shares = abs(q) * CONTRACT
        note, cash_effect, action = "Expired worthless", 0.0, "EXPIRE"
        if intrinsic > 0 and q > 0:                        # long ITM -> cash-settle intrinsic
            cash_effect, action, note = intrinsic * shares, "EXERCISE", "In the money: cash-settled at intrinsic value"
        elif intrinsic > 0 and q < 0 and r["opt_type"] == "C":   # covered call assigned -> shares called away
            action, note = "ASSIGN", f"Assigned: {shares:g} shares sold at {r['strike']:g}"
            _assign(cid, r["underlying"], "sell", shares, r["strike"])
        elif intrinsic > 0 and q < 0:                      # cash-secured put assigned -> buy shares
            action, note = "ASSIGN", f"Assigned: {shares:g} shares bought at {r['strike']:g}"
            _assign(cid, r["underlying"], "buy", shares, r["strike"])
        realized = (cash_effect if q > 0 else 0) - q * r["avg_price"] * CONTRACT
        if action == "ASSIGN":
            realized = -q * r["avg_price"] * CONTRACT      # short keeps the premium; stock P&L is in the lots
        with db.tx() as con:
            con.execute("UPDATE option_positions SET qty=0, realized_pnl = realized_pnl + ? WHERE id=?", (realized, r["id"]))
            if cash_effect:
                con.execute("UPDATE clients SET cash = cash + ? WHERE id=?", (cash_effect, cid))
            con.execute("""INSERT INTO option_trades(client_id,underlying,opt_type,strike,expiry,action,qty,price,cash_effect,realized_pnl,note,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (cid, r["underlying"], r["opt_type"], r["strike"], r["expiry"], action, abs(q), intrinsic,
                         cash_effect, realized, note, db.now_iso()))
        events.append({"client_id": cid, "contract": f"{r['underlying']} {r['expiry']} {r['strike']:g}{r['opt_type']}",
                       "action": action, "note": note})
    return events


def _assign(client_id: int, symbol: str, side: str, shares: float, strike: float) -> None:
    from . import trading
    with db.tx() as con:
        oid = con.execute("""INSERT INTO orders(client_id,symbol,side,qty,order_type,status,source,reason,created_at)
                             VALUES(?,?,?,?,'market','open','option-assignment',?,?)""",
                          (client_id, symbol, side, shares, f"Option assignment at {strike:g}", db.now_iso())).lastrowid
    order = trading.get_order(oid)
    trading._execute_fill(order, strike, allow_margin=True)


def set_level(client_id: int, level: int) -> dict:
    if level not in accounts.OPTIONS_LEVELS:
        raise ValueError("Options level must be 0, 1 or 2")
    with db.tx() as con:
        con.execute("UPDATE clients SET options_level=? WHERE id=?", (level, client_id))
    return {"options_level": level, "description": accounts.OPTIONS_LEVELS[level]}
