"""Portfolio monitoring: turns portfolio state into actionable alerts."""
from __future__ import annotations

import logging

from . import billing, db, direct_index, lending, options, portfolio, rebalance, research, risk_profile, tax, trading

log = logging.getLogger("monitor")


def alerts(client_id: int, val: dict | None = None, metrics: dict | None = None) -> list[dict]:
    client = portfolio.get_client(client_id)
    prof = risk_profile.profile_by_name(client["risk_profile"])
    val = val or portfolio.valuation(client_id)
    out = []

    def add(level, title, detail, symbol=None):
        out.append({"level": level, "title": title, "detail": detail, "symbol": symbol})

    report = rebalance.drift_report(client_id, val)
    if report["needs_rebalance"]:
        worst = sorted([r for r in report["rows"] if r["breach"]], key=lambda r: -abs(r["drift"]))
        add("warning", "Rebalancing needed",
            f"{report['breaches']} holding(s) outside tolerance bands. Largest: {worst[0]['symbol']} "
            f"({worst[0]['weight']:.1%} vs target {worst[0]['target']:.1%}).")

    max_pos = min(client["max_position"], prof["max_position"])
    di = direct_index.symbols(client_id)
    for h in val["holdings"]:
        if h["qty"] <= 0 or h["symbol"] in di:
            continue
        is_stock = not h["asset_class"] or h["asset_class"].startswith(("Stock", "Individual"))
        if is_stock and h["weight"] > max_pos + 0.005:
            add("warning", f"Concentration: {h['symbol']}",
                f"{h['weight']:.1%} of portfolio exceeds the {max_pos:.0%} single-stock limit.", h["symbol"])
        if h["unrealized_pct"] <= -client["stop_loss_pct"]:
            add("danger", f"Stop-loss trigger: {h['symbol']}",
                f"Down {h['unrealized_pct']:.1%} from average cost (limit {client['stop_loss_pct']:.0%}). Review or exit.",
                h["symbol"])

    stock_w = sum(h["weight"] for h in val["holdings"] if h["qty"] > 0 and h["asset_class"].startswith(("Stock", "Individual"))
                  and h["symbol"] not in di)
    if stock_w > prof["stock_sleeve"] + 0.01:
        add("warning", "Individual-stock sleeve over limit",
            f"Stocks are {stock_w:.1%} of the portfolio; {prof['name']} allows {prof['stock_sleeve']:.0%}.")

    twr = portfolio.time_weighted_index(client_id)
    if len(twr) >= 2:
        idx = [p["index"] for p in twr]
        dd = idx[-1] / max(idx) - 1
        if dd <= -prof["max_dd"]:
            add("danger", "Drawdown beyond tolerance",
                f"Portfolio is {dd:.1%} below its peak; {prof['name']} tolerance is {prof['max_dd']:.0%}.")

    if metrics and metrics.get("available"):
        lo, hi = prof["vol_band"]
        v = metrics["annual_vol"]
        if v > hi:
            add("warning", "Risk above profile", f"Estimated volatility {v:.1%} is above the {prof['name']} band ({lo:.0%}-{hi:.0%}).")
        elif v < lo and val["invested"] > 0.5 * val["equity"]:
            add("info", "Risk below profile", f"Estimated volatility {v:.1%} is below the {prof['name']} band ({lo:.0%}-{hi:.0%}).")

    held = [h["symbol"] for h in val["holdings"] if h["qty"] > 0]
    for sym, r in research.cached_ratings(held).items():
        if r["rating"] == "SELL":
            add("warning", f"Research: SELL on {sym}", f"Latest model rating is SELL (score {r['score']:.0f}, as of {r['updated_at'][:10]}).", sym)

    if client["cash"] < -0.01:
        ms = lending.margin_status(client_id)
        if ms["margin_call"] > 0:
            add("danger", "Margin call", f"Equity is {ms['equity_ratio']:.0%} of holdings (minimum {lending.MAINTENANCE:.0%}). "
                f"Deposit ${ms['margin_call']:,.0f} or sell about ${ms['sell_to_cover']:,.0f}.")
        else:
            add("info", "Margin loan outstanding", f"${-client['cash']:,.0f} borrowed at {client['margin_rate']:.2%}.")
    if val.get("loan_balance", 0) > 0:
        sb = lending.sbloc_status(client_id)
        if sb["collateral_call"]:
            add("danger", "Line of credit collateral call",
                f"Loan ${sb['balance']:,.0f} exceeds the borrowing base ${sb['borrowing_base']:,.0f}. Repay or add collateral.")
    for o in val.get("options", []):
        if 0 <= o["days_left"] <= 7:
            add("info", f"Option expiring: {o['label']}", f"{o['strategy']} expires in {o['days_left']} day(s); "
                f"{'in' if (o['opt_type'] == 'C' and (o['spot'] or 0) > o['strike']) or (o['opt_type'] == 'P' and (o['spot'] or 0) < o['strike']) else 'out of'} the money.")
    unpaid = db.query("SELECT fee, period_end FROM invoices WHERE client_id=? AND status='unpaid'", (client_id,))
    if unpaid:
        add("warning", "Unpaid advisory fee", f"{len(unpaid)} invoice(s) totalling ${sum(u['fee'] for u in unpaid):,.2f} "
            "could not be deducted - raise cash, then pay it from the Billing tab.")
    try:
        tlh = tax.harvest_candidates(client_id)
        if tlh:
            save = sum(t["est_tax_savings"] for t in tlh)
            add("info", "Tax-loss harvesting opportunity",
                f"{len(tlh)} position(s) with ${-sum(t['loss'] for t in tlh):,.0f} of harvestable losses "
                f"(est. tax savings ${save:,.0f}). See the Tax tab.")
    except Exception:
        pass
    n_open = len(trading.list_orders(client_id, "open"))
    if n_open:
        add("info", "Open orders", f"{n_open} working limit/stop order(s).")
    if val["missing_quotes"]:
        add("warning", "Missing prices", "No quote for: " + ", ".join(val["missing_quotes"]))
    if val["invested"] == 0:
        add("info", "Portfolio not funded", "All cash. Run a rebalance to build the model portfolio.")
    order = {"danger": 0, "warning": 1, "info": 2}
    return sorted(out, key=lambda a: order[a["level"]])


def run_cycle() -> None:
    """Background job: fill working orders and store a daily equity snapshot for every client."""
    try:
        trading.process_open_orders()
        options.process_expirations()
        billing.accrue_all()
        for c in portfolio.list_clients():
            portfolio.record_snapshot(c["id"])
    except Exception:  # never let the loop die
        log.exception("monitor cycle failed")
