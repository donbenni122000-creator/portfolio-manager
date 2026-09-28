"""Drift detection and rebalancing.

Tolerance-band ("threshold") rebalancing: a holding is in breach when
    |actual - target| >= absolute band (default 5 pp)   OR
    |actual - target| / target >= relative band (default 25%) and the gap is at least 1 pp.
Holdings that are not in the model at all are always flagged.

Modes
  full      - when anything is in breach, bring every holding back to target
  breached  - only trade the holdings that are in breach (lower turnover)
"""
from __future__ import annotations

import math

from . import config, portfolio, trading


def drift_report(client_id: int, val: dict | None = None) -> dict:
    client = portfolio.get_client(client_id)
    val = val or portfolio.valuation(client_id)
    abs_band, rel_band = client["drift_abs_band"], client["drift_rel_band"]
    rows = []
    for h in val["holdings"]:
        tw, w = h["target_weight"], h["weight"]
        d = w - tw
        if tw == 0 and h["value"] > 0:
            breach, why = True, "Not in model portfolio"
        elif abs(d) >= abs_band:
            breach, why = True, f"Drift {d:+.1%} exceeds ±{abs_band:.0%} band"
        elif tw > 0 and abs(d) / tw >= rel_band and abs(d) >= 0.01:
            breach, why = True, f"Drift is {abs(d) / tw:.0%} of target (> {rel_band:.0%})"
        else:
            breach, why = False, ""
        rows.append({"symbol": h["symbol"], "asset_class": h["asset_class"], "weight": w, "target": tw,
                     "drift": d, "value": h["value"], "price": h["price"], "breach": breach, "why": why})
    cash_drift = val["cash_weight"] - client["cash_target"]
    cash_breach = cash_drift >= abs_band
    rows.append({"symbol": "CASH", "asset_class": "Cash", "weight": val["cash_weight"], "target": client["cash_target"],
                 "drift": cash_drift, "value": val["cash"], "price": 1, "breach": cash_breach,
                 "why": "Uninvested cash above band" if cash_breach else ""})
    return {"rows": rows, "needs_rebalance": any(r["breach"] for r in rows),
            "breaches": sum(r["breach"] for r in rows), "abs_band": abs_band, "rel_band": rel_band,
            "max_abs_drift": max((abs(r["drift"]) for r in rows), default=0)}


def _qty(x: float) -> float:
    return math.floor(x * 10000) / 10000 if config.ALLOW_FRACTIONAL else float(math.floor(x))


def preview(client_id: int, mode: str = "full") -> dict:
    if mode not in ("full", "breached"):
        raise ValueError("mode must be 'full' or 'breached'")
    client = portfolio.get_client(client_id)
    val = portfolio.valuation(client_id)
    report = drift_report(client_id, val)
    equity = val["equity"]
    positions = {p["symbol"]: p for p in portfolio.get_positions(client_id)}
    by_sym = {h["symbol"]: h for h in val["holdings"]}

    candidates = [r for r in report["rows"] if r["symbol"] != "CASH"]
    if mode == "breached":
        cash_row = report["rows"][-1]
        candidates = [r for r in candidates if r["breach"] or (cash_row["breach"] and r["drift"] < 0)]

    sells, buys = [], []
    for r in candidates:
        price = r["price"]
        if not price:
            continue
        delta = r["target"] * equity - r["value"]
        if abs(delta) < config.MIN_TRADE_VALUE:
            continue
        if delta < 0:
            held = positions.get(r["symbol"], {}).get("qty", 0)
            qty = min(held, _qty(-delta / price))
            if r["target"] == 0:
                qty = held                              # exit non-model holdings completely
            if qty <= 0:
                continue
            avg = positions[r["symbol"]]["avg_cost"]
            sells.append({"symbol": r["symbol"], "side": "sell", "qty": qty, "price": price, "value": qty * price,
                          "est_realized_gain": (price - avg) * qty, "from_weight": r["weight"], "to_weight": r["target"]})
        else:
            buys.append({"symbol": r["symbol"], "side": "buy", "want_value": delta, "price": price,
                         "from_weight": r["weight"], "to_weight": r["target"]})

    slip = 1 + config.SLIPPAGE_BPS / 1e4

    def budget_now():
        return (val["cash"] + sum(s["value"] for s in sells) / slip - client["cash_target"] * equity
                - config.COMMISSION_PER_TRADE * (len(sells) + len(buys)))

    want = sum(b["want_value"] for b in buys)
    if mode == "breached" and want > budget_now():
        # fund underweight breaches by trimming the most overweight (non-breached) holdings
        sold = {s["symbol"] for s in sells}
        over = sorted([r for r in report["rows"] if r["symbol"] not in sold and r["symbol"] != "CASH"
                       and r["drift"] > 0 and r["price"]], key=lambda r: -r["drift"])
        for r in over:
            shortfall = want - budget_now()
            if shortfall < config.MIN_TRADE_VALUE:
                break
            excess = r["value"] - r["target"] * equity
            qty = min(positions[r["symbol"]]["qty"], _qty(min(excess, shortfall * slip) / r["price"]))
            if qty * r["price"] < config.MIN_TRADE_VALUE:
                continue
            avg = positions[r["symbol"]]["avg_cost"]
            sells.append({"symbol": r["symbol"], "side": "sell", "qty": qty, "price": r["price"], "value": qty * r["price"],
                          "est_realized_gain": (r["price"] - avg) * qty, "from_weight": r["weight"],
                          "to_weight": r["weight"] - qty * r["price"] / equity})

    budget = budget_now()
    scale = min(1.0, max(0.0, budget) / want) if want > 0 else 1.0
    final_buys = []
    for b in buys:
        qty = _qty(b["want_value"] * scale / (b["price"] * slip))
        if qty <= 0 or qty * b["price"] < config.MIN_TRADE_VALUE:
            continue
        final_buys.append({"symbol": b["symbol"], "side": "buy", "qty": qty, "price": b["price"],
                           "value": qty * b["price"], "est_realized_gain": 0,
                           "from_weight": b["from_weight"], "to_weight": b["to_weight"]})

    trades = sells + final_buys
    turnover = sum(t["value"] for t in trades) / equity if equity else 0
    return {
        "mode": mode, "needs_rebalance": report["needs_rebalance"], "drift": report, "trades": trades,
        "summary": {"n_trades": len(trades), "sell_value": sum(s["value"] for s in sells),
                    "buy_value": sum(b["value"] for b in final_buys), "turnover": turnover,
                    "est_realized_gains": sum(s["est_realized_gain"] for s in sells),
                    "buy_scale": scale, "equity": equity,
                    "cash_after_est": val["cash"] + sum(x["value"] for x in sells) / slip - sum(b["value"] for b in final_buys) * slip},
    }


def execute(client_id: int, mode: str = "full") -> dict:
    plan = preview(client_id, mode)
    results = []
    for t in sorted(plan["trades"], key=lambda t: t["side"] != "sell"):   # sells first to raise cash
        try:
            o = trading.place_order(client_id, t["symbol"], t["side"], qty=t["qty"], source="rebalance",
                                    reason=f"Rebalance {t['from_weight']:.1%} -> {t['to_weight']:.1%}")
            results.append({"symbol": t["symbol"], "side": t["side"], "status": o["status"], "qty": o["qty"],
                            "fill_price": o["fill_price"], "order_id": o["id"]})
        except trading.OrderError as e:
            results.append({"symbol": t["symbol"], "side": t["side"], "status": "error", "error": str(e)})
    val = portfolio.valuation(client_id)
    portfolio.record_snapshot(client_id, val)
    return {"executed": results, "after": drift_report(client_id, val)}
