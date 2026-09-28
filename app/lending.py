"""Lending (simulated): margin, securities-based line of credit (SBLOC) and fully paid securities lending.

Margin (taxable accounts only)
  * Reg T style: buying power = 2 x cash + long market value (cash may go negative = margin loan).
  * Maintenance requirement 30% of long value; below that a margin call alert is raised.
  * Interest accrues daily on the debit balance at the client's margin rate.
SBLOC
  * Borrow against the portfolio without selling. Advance rates depend on the collateral
    (bonds 85%, diversified equity ETFs 70%, single stocks 50%...). Draws go to the client's bank, so the
    portfolio is unchanged while a loan balance (with daily compounding interest) is tracked.
  * Collateral call alert if the balance exceeds the borrowing base.
Fully paid securities lending
  * Holdings are lent to borrowers; the client earns 50% of the lending fee, accrued daily as cash.
"""
from __future__ import annotations

import hashlib
from datetime import date

from . import accounts, db, portfolio, risk_profile

INITIAL_MARGIN = 0.50
MAINTENANCE = 0.30
LENDING_SPLIT = 0.50
ADVANCE_RATES = {"bond": 0.85, "cash": 0.95, "etf": 0.70, "stock": 0.50, "real": 0.60}
HARD_TO_BORROW = {"TSLA": 0.012, "INTC": 0.008, "AMD": 0.006, "NVDA": 0.004}


def _kind(symbol: str) -> str:
    ac = risk_profile.asset_class(symbol)
    if ac.startswith(("Stock", "Individual")):
        return "stock"
    if "Bond" in ac:
        return "bond"
    if "Cash" in ac:
        return "cash"
    if ac in ("Real Estate", "Gold", "Commodities"):
        return "real"
    return "etf"


def lending_rate(symbol: str) -> float:
    """Annual lending fee for a security (simulated market rates)."""
    k = _kind(symbol)
    if k in ("bond", "cash"):
        return 0.0
    if k != "stock":
        return 0.0005
    if symbol in HARD_TO_BORROW:
        return HARD_TO_BORROW[symbol]
    h = int(hashlib.sha256(symbol.encode()).hexdigest()[:6], 16) / 0xFFFFFF
    return round(0.001 + 0.004 * h, 5)


# ------------------------------------------------------------------ margin
def long_value(client_id: int, val: dict | None = None) -> float:
    val = val or portfolio.valuation(client_id)
    return sum(h["value"] for h in val["holdings"] if h["qty"] > 0)


def buying_power(client: dict, lv: float | None = None) -> float:
    """Cash available for purchases (plus margin if enabled)."""
    if not client.get("margin_enabled") or not accounts.rules(client)["margin"]:
        return max(0.0, client["cash"])
    lv = long_value(client["id"]) if lv is None else lv
    return max(0.0, 2 * client["cash"] + lv)


def margin_status(client_id: int) -> dict:
    c = portfolio.get_client(client_id)
    val = portfolio.valuation(client_id)
    lv = long_value(client_id, val)
    loan = max(0.0, -c["cash"])
    equity = val["equity"]
    req = MAINTENANCE * lv
    ratio = equity / lv if lv else 1.0
    call = max(0.0, req - equity)
    return {"enabled": bool(c["margin_enabled"]), "allowed": accounts.rules(c)["margin"], "rate": c["margin_rate"],
            "long_value": lv, "loan": loan, "equity": equity, "equity_ratio": ratio,
            "maintenance_requirement": req, "excess_equity": equity - req, "margin_call": call,
            "sell_to_cover": call / (1 - MAINTENANCE) if call else 0.0,
            "buying_power": buying_power(c, lv), "max_loan": INITIAL_MARGIN * lv}


def set_margin(client_id: int, enabled: bool, rate: float | None = None) -> dict:
    c = portfolio.get_client(client_id)
    if enabled and not accounts.rules(c)["margin"]:
        raise ValueError(f"Margin isn't allowed in a {c['account_type']} account")
    if not enabled and c["cash"] < -1e-6:
        raise ValueError("Repay the margin loan (bring cash above $0) before turning margin off")
    with db.tx() as con:
        con.execute("UPDATE clients SET margin_enabled=?" + (", margin_rate=?" if rate is not None else "") + " WHERE id=?",
                    (int(enabled), rate, client_id) if rate is not None else (int(enabled), client_id))
    return margin_status(client_id)


# ------------------------------------------------------------------ SBLOC
def _sbloc(client_id: int):
    return db.query_one("SELECT * FROM loans WHERE client_id=? AND kind='sbloc' AND status='open'", (client_id,))


def borrowing_base(client_id: int, val: dict | None = None) -> float:
    c = portfolio.get_client(client_id)
    val = val or portfolio.valuation(client_id)
    base = sum(h["value"] * ADVANCE_RATES[_kind(h["symbol"])] for h in val["holdings"] if h["qty"] > 0)
    base += max(0.0, c["cash"]) * ADVANCE_RATES["cash"]
    base -= max(0.0, -c["cash"])            # a margin loan uses the same collateral
    return max(0.0, base)


def sbloc_status(client_id: int) -> dict:
    c = portfolio.get_client(client_id)
    loan = _sbloc(client_id)
    base = borrowing_base(client_id)
    bal = loan["balance"] if loan else 0.0
    events = db.query("SELECT * FROM loan_events WHERE client_id=? ORDER BY id DESC LIMIT 50", (client_id,))
    return {"allowed": accounts.rules(c)["sbloc"], "rate": c["sbloc_rate"], "borrowing_base": base, "balance": bal,
            "available": max(0.0, base - bal), "ltv": bal / base if base else 0.0,
            "collateral_call": bal > base + 0.01, "events": events,
            "advance_rates": ADVANCE_RATES}


def sbloc_draw(client_id: int, amount: float) -> dict:
    c = portfolio.get_client(client_id)
    if not accounts.rules(c)["sbloc"]:
        raise ValueError(f"A {c['account_type']} account can't be pledged for a line of credit")
    if amount <= 0:
        raise ValueError("Amount must be positive")
    st = sbloc_status(client_id)
    if amount > st["available"] + 0.01:
        raise ValueError(f"Only ${st['available']:,.2f} available to borrow")
    loan = _sbloc(client_id)
    with db.tx() as con:
        if loan:
            con.execute("UPDATE loans SET balance = balance + ? WHERE id=?", (amount, loan["id"]))
            lid = loan["id"]
        else:
            lid = con.execute("INSERT INTO loans(client_id,kind,balance,rate,last_accrual,opened_at) VALUES(?,?,?,?,?,?)",
                              (client_id, "sbloc", amount, c["sbloc_rate"], date.today().isoformat(), db.now_iso())).lastrowid
        con.execute("INSERT INTO loan_events(loan_id,client_id,kind,amount,created_at) VALUES(?,?,?,?,?)",
                    (lid, client_id, "draw", amount, db.now_iso()))
    return sbloc_status(client_id)


def sbloc_repay(client_id: int, amount: float, from_cash: bool = True) -> dict:
    loan = _sbloc(client_id)
    if not loan:
        raise ValueError("No open line of credit")
    amount = min(amount, loan["balance"])
    if amount <= 0:
        raise ValueError("Amount must be positive")
    c = portfolio.get_client(client_id)
    if from_cash and c["cash"] + 1e-6 < amount:
        raise ValueError(f"Only ${max(0, c['cash']):,.2f} cash available - sell holdings or repay from outside")
    with db.tx() as con:
        con.execute("UPDATE loans SET balance = balance - ? WHERE id=?", (amount, loan["id"]))
        if from_cash:
            con.execute("UPDATE clients SET cash = cash - ? WHERE id=?", (amount, client_id))
            con.execute("INSERT INTO ledger(client_id,kind,amount,note,created_at) VALUES(?,?,?,?,?)",
                        (client_id, "sbloc_repay", -amount, "Line of credit repayment", db.now_iso()))
        con.execute("INSERT INTO loan_events(loan_id,client_id,kind,amount,created_at) VALUES(?,?,?,?,?)",
                    (loan["id"], client_id, "repay", -amount, db.now_iso()))
        if loan["balance"] - amount < 0.005:
            con.execute("UPDATE loans SET status='closed', balance=0 WHERE id=?", (loan["id"],))
    return sbloc_status(client_id)


# ------------------------------------------------------------------ securities lending
def set_lending(client_id: int, enabled: bool) -> dict:
    c = portfolio.get_client(client_id)
    if enabled and not accounts.rules(c)["lending"]:
        raise ValueError(f"Securities lending isn't offered for {c['account_type']} accounts")
    with db.tx() as con:
        con.execute("UPDATE clients SET sec_lending=? WHERE id=?", (int(enabled), client_id))
    return lending_status(client_id)


def lending_status(client_id: int) -> dict:
    c = portfolio.get_client(client_id)
    val = portfolio.valuation(client_id)
    rows = [{"symbol": h["symbol"], "value": h["value"], "rate": lending_rate(h["symbol"]),
             "client_rate": lending_rate(h["symbol"]) * LENDING_SPLIT,
             "annual_income": h["value"] * lending_rate(h["symbol"]) * LENDING_SPLIT}
            for h in val["holdings"] if h["qty"] > 0]
    ytd = db.query_one("""SELECT COALESCE(SUM(amount),0) AS s FROM ledger WHERE client_id=? AND kind='lending_income'
                          AND substr(created_at,1,4)=?""", (client_id, str(date.today().year)))["s"]
    return {"enabled": bool(c["sec_lending"]), "allowed": accounts.rules(c)["lending"], "split": LENDING_SPLIT,
            "holdings": sorted(rows, key=lambda r: -r["annual_income"]),
            "projected_annual_income": sum(r["annual_income"] for r in rows), "income_ytd": ytd}


# ------------------------------------------------------------------ daily accruals (called from billing.accrue_interest)
def accrue(con, client: dict, days: int) -> None:
    """Margin interest, SBLOC interest and securities-lending income for `days` elapsed days."""
    cid, now = client["id"], db.now_iso()
    if client["cash"] < 0:
        charge = round(-client["cash"] * ((1 + client["margin_rate"]) ** (days / 365) - 1), 2)
        if charge > 0:
            con.execute("UPDATE clients SET cash = cash - ? WHERE id=?", (charge, cid))
            con.execute("INSERT INTO ledger(client_id,kind,amount,note,created_at) VALUES(?,?,?,?,?)",
                        (cid, "margin_interest", -charge, f"Margin interest {days} day(s) @ {client['margin_rate']:.2%}", now))
    loan = con.execute("SELECT * FROM loans WHERE client_id=? AND status='open'", (cid,)).fetchone()
    if loan and loan["balance"] > 0:
        interest = round(loan["balance"] * ((1 + loan["rate"]) ** (days / 365) - 1), 2)
        if interest > 0:
            con.execute("UPDATE loans SET balance = balance + ?, last_accrual=? WHERE id=?", (interest, date.today().isoformat(), loan["id"]))
            con.execute("INSERT INTO loan_events(loan_id,client_id,kind,amount,created_at) VALUES(?,?,?,?,?)",
                        (loan["id"], cid, "interest", interest, now))
    if client.get("sec_lending"):
        pos = con.execute("SELECT symbol, qty FROM positions WHERE client_id=? AND qty > 0", (cid,)).fetchall()
        if pos:
            from .market_data import get_provider
            quotes = get_provider().quotes([p["symbol"] for p in pos])
            income = sum(p["qty"] * quotes[p["symbol"]]["price"] * lending_rate(p["symbol"]) * LENDING_SPLIT
                         for p in pos if p["symbol"] in quotes) * days / 365
            income = round(income, 2)
            if income > 0:
                con.execute("UPDATE clients SET cash = cash + ? WHERE id=?", (income, cid))
                con.execute("INSERT INTO ledger(client_id,kind,amount,note,created_at) VALUES(?,?,?,?,?)",
                            (cid, "lending_income", income, f"Securities lending income {days} day(s)", now))
