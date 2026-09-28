"""Morning briefing: markets mood, the advisory book and today's action items, in one payload.

Used by the welcome screen, the pulse line (VIX drives its speed) and the AI tools.
"""
from __future__ import annotations

from datetime import datetime

from . import config, db, markets, monitor, portfolio
from .market_data import DataError

EQUITY_INDICES = ["^GSPC", "^IXIC", "^DJI", "^RUT", "^NSEI", "^BSESN", "^FTSE", "^GDAXI", "^N225", "^HSI"]
HEADLINE = [("^GSPC", "S&P 500"), ("^NSEI", "Nifty 50"), ("^IXIC", "Nasdaq"), ("GCUSD", "Gold"), ("BTCUSD", "Bitcoin")]


def _greeting(hour: int) -> str:
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"


def market_mood(rows: dict) -> dict:
    moves = [rows[s]["change_pct"] for s in EQUITY_INDICES if s in rows and rows[s].get("available")]
    avg = sum(moves) / len(moves) if moves else 0.0
    breadth = (sum(1 for m in moves if m > 0) / len(moves)) if moves else 0.5
    vix = rows.get("^VIX", {}).get("price") if rows.get("^VIX", {}).get("available") else None
    # -1 (risk-off) .. +1 (risk-on): average move scaled, nudged by breadth and the VIX level
    score = max(-1.0, min(1.0, avg / 0.012 + (breadth - 0.5) * 0.8 - ((vix - 18) / 20 if vix else 0)))
    label = "Risk-on" if score > 0.25 else "Risk-off" if score < -0.25 else "Mixed"
    return {"score": score, "label": label, "avg_move": avg, "breadth": breadth, "vix": vix, "n": len(moves)}


def build() -> dict:
    board = markets.board()
    rows = {r["symbol"]: r for g in board["groups"] for r in g["rows"]}
    mood = market_mood(rows)
    headline = [{"symbol": s, "name": n, "price": rows[s].get("price"), "change_pct": rows[s].get("change_pct"),
                 "kind": rows[s].get("kind")} for s, n in HEADLINE if s in rows and rows[s].get("available")]

    clients, actions = [], []
    aum = day_pnl = 0.0
    for c in portfolio.list_clients():
        try:
            v = portfolio.valuation(c["id"])
        except DataError:
            continue
        al = monitor.alerts(c["id"], v)
        aum += v["equity"]
        day_pnl += v.get("day_change", 0.0)
        clients.append({"id": c["id"], "name": c["name"], "equity": v["equity"], "day_change_pct": v["day_change_pct"],
                        "alerts": len(al)})
        for a in al:
            if a["level"] in ("danger", "warning"):
                actions.append({"client_id": c["id"], "client": c["name"], **a})
    open_orders = db.query_one("SELECT COUNT(*) AS n FROM orders WHERE status='open'")["n"]
    rebalances = sum(1 for a in actions if a["title"].startswith("Rebalancing"))

    now = datetime.now()
    sp = rows.get("^GSPC", {})
    nifty = rows.get("^NSEI", {})
    parts = []
    if sp.get("available"):
        parts.append(f"the S&P 500 is {'up' if sp['change_pct'] >= 0 else 'down'} {abs(sp['change_pct']):.1%}")
    if nifty.get("available"):
        parts.append(f"the Nifty {'up' if nifty['change_pct'] >= 0 else 'down'} {abs(nifty['change_pct']):.1%}")
    summary = (("Markets are " + mood["label"].lower() + ": " + " and ".join(parts) + ". ") if parts else "") + (
        f"{rebalances} client{'s' if rebalances != 1 else ''} need{'s' if rebalances == 1 else ''} a rebalance."
        if rebalances else "Every portfolio is inside its bands.")
    return {
        "greeting": _greeting(now.hour), "owner": config.OWNER_NAME, "brand": config.BRAND_NAME,
        "date": now.strftime("%A, %d %B %Y"), "summary": summary, "mood": mood, "headline": headline,
        "book": {"clients": len(clients), "aum": aum, "day_pnl": day_pnl,
                 "day_pct": day_pnl / (aum - day_pnl) if aum - day_pnl else 0.0},
        "clients": clients, "actions": actions[:8], "open_orders": open_orders, "rebalances": rebalances,
        "data_mode": config.DATA_MODE,
    }
