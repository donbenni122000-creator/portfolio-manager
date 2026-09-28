"""MCP (Model Context Protocol) server: lets AI assistants such as Claude and ChatGPT
use the Portfolio Manager through tools.

Two ways to connect:
  * Claude Desktop (local, private): runs `mcp_server.py` over stdio. See README.
  * ChatGPT (remote): the web app serves the same tools over Streamable HTTP at
    /connect/<secret-token>/mcp. That path is exposed through a tunnel (see connect_chatgpt.bat).

Everything is paper trading. No real orders are ever sent.
"""
from __future__ import annotations

import secrets

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from . import (analytics, billing, briefing, config, news, direct_index, lending, markets, model_library, monitor, options, planning, portfolio,
               rebalance, research, risk_profile, tax, trading)
from .market_data import DataError, get_provider

INSTRUCTIONS = """Portfolio Manager: a paper-trading portfolio management platform.
- Clients each have a risk profile (Conservative, Moderately Conservative, Moderate, Growth, Aggressive), a model portfolio and a paper account.
- Use list_clients first to find a client_id.
- research_stock gives a BUY/HOLD/SELL rating from a DCF + peer-multiple + quality + momentum model. Pass client_id for a client-specific, sized action.
- Always call preview_rebalance and show the trades before execute_rebalance.
- Also available: tax reports and tax-loss harvesting, the model marketplace, Monte Carlo goal planning, fee billing,
  direct indexing, lending (margin, line of credit, securities lending) and simulated options.
- Confirm with the user before placing orders, executing rebalances, harvesting, assigning models or billing.
- All trades are simulated (paper money). Nothing here is investment advice."""

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


def _r(x, n=4):
    return None if x is None else round(float(x), n)


def build_server(remote: bool = False) -> FastMCP:
    kwargs = {}
    if remote:
        # Requests arrive through a tunnel with a public Host header; the secret URL path protects access.
        kwargs = dict(stateless_http=True, streamable_http_path="/mcp",
                      transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    mcp = FastMCP("Portfolio Manager", instructions=INSTRUCTIONS, **kwargs)

    # ------------------------------------------------------------------ read tools
    @mcp.tool(annotations=READ)
    def list_clients() -> list[dict]:
        """List all clients with risk profile, portfolio value and total return."""
        out = []
        for c in portfolio.list_clients():
            try:
                v = portfolio.valuation(c["id"])
                eq, tr = v["equity"], v["total_return"]
            except DataError:
                eq = tr = None
            out.append({"client_id": c["id"], "name": c["name"], "risk_profile": c["risk_profile"],
                        "risk_score": c["risk_score"], "portfolio_value": _r(eq, 2), "total_return": _r(tr)})
        return out

    @mcp.tool(annotations=READ)
    def get_portfolio(client_id: int) -> dict:
        """Full snapshot of a client's paper portfolio: value, P&L, cash, holdings with weights vs targets,
        risk metrics vs the client's profile, and monitoring alerts (drift, stop-loss, concentration...)."""
        client = portfolio.get_client(client_id)
        trading.process_open_orders(client_id)
        val = portfolio.valuation(client_id)
        portfolio.record_snapshot(client_id, val)
        prof = risk_profile.profile_by_name(client["risk_profile"])
        weights = {h["symbol"]: h["weight"] for h in val["holdings"] if h["qty"] > 0}
        m = analytics.portfolio_metrics(weights, val["cash_weight"])
        metrics = {k: _r(m.get(k)) for k in ("annual_return", "annual_vol", "sharpe", "max_drawdown", "beta", "var95_1d")} \
            if m.get("available") else {"note": m.get("reason")}
        return {
            "client": {"id": client_id, "name": client["name"], "risk_profile": prof["name"],
                       "target_vol_band": prof["vol_band"], "max_drawdown_tolerance": prof["max_dd"],
                       "single_stock_limit": prof["max_position"], "stock_sleeve_limit": prof["stock_sleeve"]},
            "summary": {"portfolio_value": _r(val["equity"], 2), "cash": _r(val["cash"], 2),
                        "cash_weight": _r(val["cash_weight"]), "net_deposits": _r(val["net_deposits"], 2),
                        "total_pnl": _r(val["total_pnl"], 2), "total_return": _r(val["total_return"]),
                        "day_change": _r(val["day_change"], 2), "unrealized_pnl": _r(val["unrealized_pnl"], 2),
                        "realized_pnl": _r(val["realized_pnl"], 2)},
            "holdings": [{"symbol": h["symbol"], "asset_class": h["asset_class"], "qty": _r(h["qty"]),
                          "avg_cost": _r(h["avg_cost"], 2), "price": _r(h["price"], 2), "value": _r(h["value"], 2),
                          "weight": _r(h["weight"]), "target_weight": _r(h["target_weight"]), "drift": _r(h["drift"]),
                          "unrealized_pnl": _r(h["unrealized"], 2), "unrealized_pct": _r(h["unrealized_pct"])}
                         for h in val["holdings"]],
            "risk_metrics_3y_lookthrough": metrics,
            "alerts": monitor.alerts(client_id, val, m),
            "data_mode": config.DATA_MODE,
        }

    @mcp.tool(annotations=READ)
    def get_quote(symbol: str) -> dict:
        """Current price quote for a stock or ETF ticker."""
        return get_provider().quote(symbol)

    @mcp.tool(annotations=READ)
    def research_stock(symbol: str, client_id: int | None = None) -> dict:
        """Run the equity research model on a ticker and return BUY/HOLD/SELL with fair value, upside,
        scores (valuation/quality/momentum), reasons, key DCF assumptions and Street consensus.
        With client_id, also returns a suitability-checked, sized action for that client."""
        r = research.analyze(symbol, client_id)
        a = r.get("assumptions") or {}
        st = r.get("street") or {}
        out = {
            "symbol": r["symbol"], "name": r["name"], "sector": r["sector"], "price": _r(r["price"], 2),
            "rating": r["rating"], "conviction": r["conviction"], "fair_value": _r(r.get("fair_value"), 2),
            "upside": _r(r.get("upside")), "bear_value": _r(r.get("bear_value"), 2), "bull_value": _r(r.get("bull_value"), 2),
            "valuation_method": r["valuation_method"], "scores": r["scores"], "reasons": r["reasons"], "notes": r["notes"],
            "key_assumptions": {k: _r(a.get(k)) for k in ("wacc", "cost_of_equity", "beta", "revenue_growth_start",
                                                            "fcf_margin", "fcf_margin_terminal", "terminal_growth")} | (
                {"growth_source": a.get("growth_source")} if a else {}),
            "street": {"price_target": st.get("price_target"), "forward_pe": _r(st.get("forward_pe"), 2),
                       "consensus_eps_cagr": _r(st.get("eps_cagr"))} if st else None,
            "piotroski_f_score": (r.get("piotroski") or {}).get("score"),
            "technicals": {k: _r(r["technicals"].get(k)) for k in ("ret_1m", "ret_6m", "ret_1y", "vol_1y", "rsi14", "sma50", "sma200")},
            "data_mode": config.DATA_MODE,
        }
        if r.get("client_view"):
            out["client_action"] = r["client_view"]
        return out

    @mcp.tool(annotations=READ)
    def preview_rebalance(client_id: int, mode: str = "full") -> dict:
        """Show drift vs target weights and the paper trades a rebalance would make, without executing.
        mode: 'full' (all holdings to target) or 'breached' (only holdings outside tolerance bands)."""
        p = rebalance.preview(client_id, mode)
        return {"needs_rebalance": p["needs_rebalance"], "mode": mode,
                "breaches": [{"symbol": r["symbol"], "weight": _r(r["weight"]), "target": _r(r["target"]),
                              "drift": _r(r["drift"]), "why": r["why"]} for r in p["drift"]["rows"] if r["breach"]],
                "trades": [{"side": t["side"], "symbol": t["symbol"], "qty": _r(t["qty"]), "est_price": _r(t["price"], 2),
                            "value": _r(t["value"], 2), "est_realized_gain": _r(t["est_realized_gain"], 2)} for t in p["trades"]],
                "summary": {k: _r(v, 4) for k, v in p["summary"].items()}}

    @mcp.tool(annotations=READ)
    def list_orders(client_id: int, status: str | None = None) -> list[dict]:
        """List a client's paper orders (status: open, filled, cancelled, rejected; omit for all)."""
        return trading.list_orders(client_id, status, limit=50)

    @mcp.tool(annotations=READ)
    def get_risk_questionnaire() -> list[dict]:
        """The 8-question risk questionnaire. Answers are option indexes (0-based) keyed by question id."""
        return risk_profile.QUESTIONNAIRE

    @mcp.tool(annotations=READ)
    def get_tax_report(client_id: int, year: int | None = None) -> dict:
        """Tax summary for a client: realized short/long-term gains, estimated tax, wash sales, unrealized gains
        by term, and tax-loss-harvesting opportunities (with suggested replacement securities)."""
        r = tax.report(client_id, year)
        return {k: (_r(v, 2) if isinstance(v, float) else v) for k, v in r.items() if k not in ("lots", "realized")} | {
            "realized_trades": [{"symbol": x["symbol"], "qty": _r(x["qty"]), "gain": _r(x["gain"], 2), "term": x["term"],
                                 "sold": x["sold_at"][:10], "wash_sale": x["wash_sale"]} for x in r["realized"][:50]],
            "open_lots": len(r["lots"])}

    @mcp.tool(annotations=READ)
    def list_models() -> list[dict]:
        """Model marketplace: every model portfolio with holdings, expected return/volatility (capital market
        assumptions), which risk profiles it fits, and which clients use it."""
        return [{"model_id": m["id"], "name": m["name"], "category": m["category"], "builtin": bool(m["builtin"]),
                 "holdings": m["holdings"], "expected_return": _r(m["expected_return"]), "volatility": _r(m["volatility"]),
                 "fits_profiles": m["fits_profiles"], "clients": [c["name"] for c in m["clients"]]} for m in model_library.list_models()]

    @mcp.tool(annotations=READ)
    def get_financial_plan(client_id: int) -> dict:
        """Run the Monte Carlo financial plan for a client's goals: probability of success, median and bad-case
        outcomes, monthly saving needed for 80% odds, and success odds under each risk profile."""
        p = planning.plan(client_id)
        return {"portfolio_value": _r(p["portfolio_value"], 2), "assumptions": {k: _r(v) for k, v in p["assumptions"].items()},
                "goals": [{"goal_id": g["goal"]["id"], "name": g["goal"]["name"], "target_today_dollars": g["goal"]["target_amount"],
                           "target_date": g["goal"]["target_date"], "monthly_contribution": g["goal"]["monthly_contribution"],
                           "target_nominal": _r(g["target_nominal"], 0), "probability": _r(g["probability"], 3), "status": g["status"],
                           "median_end": _r(g["median_end"], 0), "p10_end": _r(g["p10_end"], 0),
                           "monthly_needed_for_80pct": _r(g["required_monthly_for_80pct"], 0),
                           "what_if_by_profile": [{"profile": w["profile"], "probability": _r(w["probability"], 3)} for w in g["what_if"]]}
                          for g in p["goals"]]}

    @mcp.tool(annotations=READ)
    def get_billing(client_id: int | None = None) -> dict:
        """Billing: with client_id, that client's fee schedule, accrued fee, invoices and cash interest;
        without it, firm-wide AUM, projected revenue and the fee preview for the current period."""
        if client_id:
            c = portfolio.get_client(client_id)
            s, e = billing.default_period()
            return {"schedule": billing.client_schedule(c)["name"], "current_period": billing.compute_fee(client_id, s, e),
                    "invoices": billing.invoices(client_id)[:20], "interest": billing.interest_summary(client_id)}
        return {"summary": {k: v for k, v in billing.firm_summary().items() if k != "clients"}, "preview": billing.preview()}

    @mcp.tool(annotations=READ)
    def get_option_chain(symbol: str, expiry: str | None = None) -> dict:
        """Theoretical option chain (Black-Scholes on historical volatility) for a ticker: strikes around the price
        with call/put bid, ask and delta, plus available expiries. Not live exchange quotes."""
        ch = options.chain(symbol, expiry)
        return {"underlying": ch["underlying"], "spot": ch["spot"], "expiry": ch["expiry"], "expiries": ch["expiries"],
                "note": ch["note"], "strikes": [{"strike": r["strike"], "call_bid": r["call"]["bid"], "call_ask": r["call"]["ask"],
                                                 "call_delta": _r(r["call"]["delta"], 3), "put_bid": r["put"]["bid"], "put_ask": r["put"]["ask"],
                                                 "put_delta": _r(r["put"]["delta"], 3)} for r in ch["rows"]]}

    @mcp.tool(annotations=READ)
    def get_lending(client_id: int) -> dict:
        """Margin status (loan, buying power, maintenance/margin call), securities-based line of credit
        (borrowing base, balance, available) and securities-lending income for a client."""
        m, s_, l_ = lending.margin_status(client_id), lending.sbloc_status(client_id), lending.lending_status(client_id)
        return {"margin": {k: (_r(v, 2) if isinstance(v, float) else v) for k, v in m.items()},
                "line_of_credit": {k: (_r(v, 2) if isinstance(v, float) else v) for k, v in s_.items() if k not in ("events", "advance_rates")},
                "securities_lending": {"enabled": l_["enabled"], "projected_annual_income": _r(l_["projected_annual_income"], 2),
                                       "income_ytd": _r(l_["income_ytd"], 2)}}

    @mcp.tool(annotations=READ)
    def preview_direct_index(client_id: int, sleeve_weight: float, top_n: int = 50,
                             excluded_sectors: list[str] | None = None, excluded_symbols: list[str] | None = None) -> dict:
        """Preview a personalised S&P 500 direct-index sleeve (share of invested assets, top-N names, exclusions):
        holdings, sector tilts vs the index and estimated tracking error. Doesn't change anything."""
        p = direct_index.preview(client_id, sleeve_weight, top_n, excluded_sectors or [], excluded_symbols or [])
        return {"names": p["count"], "index_coverage": _r(p["index_coverage"]), "tracking": p["tracking"],
                "sector_tilts": [{"sector": x["sector"], "active": _r(x["active"])} for x in p["sectors"]],
                "top_holdings": [{"symbol": h["symbol"], "sleeve_weight": _r(h["sleeve_weight"])} for h in p["holdings_list"][:15]]}

    # ------------------------------------------------------------------ write tools (paper account only)
    @mcp.tool(annotations=WRITE)
    def place_paper_order(client_id: int, symbol: str, side: str, quantity: float | None = None,
                          dollar_amount: float | None = None, order_type: str = "market",
                          limit_price: float | None = None, stop_price: float | None = None) -> dict:
        """Place a PAPER (simulated) order. side: buy/sell. Give quantity (shares) or dollar_amount.
        order_type: market, limit (needs limit_price) or stop (needs stop_price). No short selling."""
        o = trading.place_order(client_id, symbol, side, quantity, dollar_amount, order_type, limit_price, stop_price,
                                source="assistant")
        portfolio.record_snapshot(client_id)
        return o

    @mcp.tool(annotations=WRITE)
    def cancel_order(client_id: int, order_id: int) -> dict:
        """Cancel an open (working) paper order."""
        return trading.cancel_order(client_id, order_id)

    @mcp.tool(annotations=WRITE)
    def execute_rebalance(client_id: int, mode: str = "full") -> dict:
        """Execute the rebalance as paper market orders (sells first). Call preview_rebalance first."""
        res = rebalance.execute(client_id, mode)
        return {"executed": res["executed"], "still_needs_rebalance": res["after"]["needs_rebalance"]}

    @mcp.tool(annotations=WRITE)
    def harvest_tax_loss(client_id: int, symbol: str, replacement: str | None = None) -> dict:
        """Tax-loss harvest: sell the losing lots of `symbol` and buy a similar replacement (default suggested),
        swapping the client's target so rebalancing won't undo it. Paper trades."""
        r = tax.harvest(client_id, symbol, replacement)
        return {"sold": r["sold"], "bought": r["bought"], "realized_loss": _r(r["realized_loss"], 2),
                "est_tax_savings": _r(r["est_tax_savings"], 2)}

    @mcp.tool(annotations=WRITE)
    def assign_model(client_id: int, model_id: int) -> dict:
        """Assign a marketplace model to a client (replaces target weights). Returns a suitability warning if the
        model's risk doesn't fit the client's profile. Rebalance afterwards to implement."""
        return model_library.assign(client_id, model_id)

    @mcp.tool(annotations=WRITE)
    def add_goal(client_id: int, name: str, target_amount: float, target_date: str, monthly_contribution: float = 0,
                 allocation_pct: float = 1.0, inflation_adjust: bool = True) -> dict:
        """Add a financial goal (target_date YYYY-MM-DD; amount in today's dollars if inflation_adjust)."""
        return planning.save_goal(client_id, name, target_amount, target_date, monthly_contribution, allocation_pct, inflation_adjust)

    @mcp.tool(annotations=WRITE)
    def run_billing(period_start: str | None = None, period_end: str | None = None) -> dict:
        """Bill advisory fees for all clients (in arrears, average daily AUM, pro-rated) and deduct from cash.
        Dates YYYY-MM-DD; default is the current quarter to date. Never bills the same day twice."""
        from datetime import date as _d
        s_ = _d.fromisoformat(period_start) if period_start else None
        e_ = _d.fromisoformat(period_end) if period_end else None
        return billing.run_billing(s_, e_)

    @mcp.tool(annotations=WRITE)
    def place_option_order(client_id: int, underlying: str, option_type: str, strike: float, expiry: str,
                           action: str, contracts: int = 1) -> dict:
        """Paper option trade. option_type C/P; action BTO (buy to open), STC, STO (covered call / cash-secured put
        only), BTC. Requires the client's options level (1: covered calls & cash-secured puts, 2: + buying options)."""
        return options.place(client_id, underlying, option_type, strike, expiry, action, contracts)

    @mcp.tool(annotations=WRITE)
    def apply_direct_index(client_id: int, sleeve_weight: float, top_n: int = 50,
                           excluded_sectors: list[str] | None = None, excluded_symbols: list[str] | None = None) -> dict:
        """Replace part of the client's core US equity ETF with a personalised S&P 500 direct index (sets targets;
        rebalance afterwards to buy the stocks)."""
        r = direct_index.apply(client_id, sleeve_weight, top_n, excluded_sectors or [], excluded_symbols or [])
        return {"names": r["names"], "sleeve_weight": r["sleeve_weight"]}

    @mcp.tool(annotations=WRITE)
    def create_client(name: str, answers: dict[str, int], starting_cash: float = 100000,
                      profile_override: str | None = None) -> dict:
        """Create a client from questionnaire answers (see get_risk_questionnaire) with a paper account.
        The model portfolio is set automatically; run preview_rebalance/execute_rebalance to invest the cash."""
        c = portfolio.create_client(name, None, answers, starting_cash, profile_override)
        return {"client_id": c["id"], "name": c["name"], "risk_profile": c["risk_profile"], "risk_score": c["risk_score"],
                "model_portfolio": portfolio.get_targets(c["id"])}

    @mcp.tool(annotations=READ)
    def get_market_overview() -> dict:
        """World markets snapshot: US (S&P 500, Dow, Nasdaq, Russell 2000, VIX), India (Nifty 50, Sensex),
        Europe, Asia-Pacific indices, commodities (gold, silver, oil, gas, copper), crypto, currencies and the
        US 10-year yield - each with price, day change, 1M/YTD/1Y returns."""
        b = markets.board()
        return {"groups": [{"title": g["title"], "rows": [{k: r.get(k) for k in ("symbol", "name", "price", "change_pct",
                 "r1m", "ytd", "r1y", "available")} for r in g["rows"]]} for g in b["groups"]], "data_mode": b["data_mode"]}

    @mcp.tool(annotations=READ)
    def get_watchlist(watchlist_id: int | None = None, range: str = "1M") -> dict:
        """The user's watchlist (default: the first one): each symbol's price, change, 52-week range, model
        rating, plus the list's equal-weighted return over the range (1M,3M,6M,YTD,1Y,3Y,5Y) vs S&P 500/Nasdaq/Nifty."""
        wid = watchlist_id or markets.list_watchlists()[0]["id"]
        v = markets.view(wid, range)
        for r in v["rows"]:
            r.pop("spark", None)
        v["performance"] = {"return": v["performance"]["return"]}
        v["all_watchlists"] = markets.list_watchlists()
        return v

    @mcp.tool(annotations=WRITE)
    def add_to_watchlist(symbol: str, watchlist_id: int | None = None) -> dict:
        """Add a symbol (stock, ETF, index like ^RUT, commodity like GCUSD, crypto like BTCUSD) to a watchlist."""
        wid = watchlist_id or markets.list_watchlists()[0]["id"]
        return markets.add_symbol(wid, symbol)

    @mcp.tool(annotations=READ)
    def get_morning_briefing() -> dict:
        """Morning briefing: market mood (risk-on/off from index moves, breadth and the VIX), headline markets,
        the advisory book (clients, assets, today's P&L) and action items such as rebalances and alerts."""
        return briefing.build()

    @mcp.tool(annotations=READ)
    def get_news(section: str = "top", symbol: str | None = None) -> dict:
        """Latest headlines. With a symbol: Yahoo Finance news for that ticker. Otherwise WSJ section headlines -
        section is one of top, markets, business, economy, world, tech, personal-finance, us, politics, opinion."""
        if symbol:
            return {"symbol": symbol.upper(), "items": news.ticker(symbol)}
        d = news.desk(section)
        return {"section": section, "items": d["items"][:25], "errors": d["errors"]}

    @mcp.tool(annotations=READ)
    def efficient_frontier(symbols: list[str], lookback: str = "3y", method: str = "blend", max_weight: float = 0.4,
                           my_weights: dict[str, float] | None = None) -> dict:
        """Modern portfolio theory for a list of stocks/ETFs: long-only efficient frontier, min-variance and
        max-Sharpe (tangency) portfolios, per-asset stats and correlations. lookback 1y/3y/5y; method blend,
        historical or capm; max_weight caps each holding. Optionally pass my_weights to see how efficient a mix is."""
        from . import frontier
        r = frontier.analyze(symbols, lookback, method, max_weight, True, my_weights, n_random=0)
        return {k: r[k] for k in ("symbols", "skipped", "risk_free", "min_variance", "max_sharpe", "equal_weight",
                                  "mine", "assets", "notes")} | {
            "frontier": [{"vol": round(p["vol"], 4), "ret": round(p["ret"], 4), "sharpe": p["sharpe"]} for p in r["frontier"]]}

    return mcp


# ---------------------------------------------------------------------- remote access token
def remote_token() -> str:
    """Secret used in the ChatGPT connector URL. Created once, stored in data/mcp_token.txt."""
    path = config.DB_PATH.parent / "mcp_token.txt" if str(config.DB_PATH) != ":memory:" else None
    if path and path.exists():
        return path.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(24)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
    return token
