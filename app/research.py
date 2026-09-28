"""Equity research engine: financial model -> fair value -> Buy / Hold / Sell.

Pillars
  1. Valuation (50%)  - 10-year FCF DCF (CAPM/WACC, Gordon terminal) blended with peer-multiple valuation
  2. Quality   (30%)  - Piotroski F-score, ROIC, margin stability
  3. Momentum  (20%)  - 12-1 month return, trend vs 50/200-day averages, RSI

Everything returned is plain JSON so the UI (or you, in a notebook) can inspect each assumption.
"""
from __future__ import annotations

import statistics as st

from . import analytics, config, db, portfolio, risk_profile
from .market_data import DataError, _num, get_provider

BUY_UPSIDE, SELL_DOWNSIDE = 0.15, -0.15
CONSENSUS_YEARS = 3      # use at most 3 years of Street revenue estimates (beyond that coverage thins out)
MIN_ANALYSTS = 3         # ignore estimate years covered by fewer analysts
DCF_YEARS = 10   # explicit forecast; growth fades linearly to the terminal rate


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def _scale(x, lo, hi):
    """Map x linearly from [lo, hi] to [0, 100]."""
    if x is None:
        return 50.0
    return _clip((x - lo) / (hi - lo) * 100, 0, 100)


def _safe_div(a, b):
    return a / b if a is not None and b not in (None, 0) else None


# ------------------------------------------------------------------ historical table
def build_history(inc, bal, cf) -> list[dict]:
    rows = []
    for i, s in enumerate(inc):
        b = bal[i] if i < len(bal) else {}
        c = cf[i] if i < len(cf) else {}
        rev = _num(s, "revenue")
        ebit = _num(s, "operatingIncome", "ebit")
        ni = _num(s, "netIncome")
        fcf = _num(c, "freeCashFlow")
        if fcf is None and _num(c, "operatingCashFlow") is not None:
            fcf = _num(c, "operatingCashFlow") + (_num(c, "capitalExpenditure", default=0))
        pretax = _num(s, "incomeBeforeTax")
        tax_rate = _clip(_safe_div(_num(s, "incomeTaxExpense"), pretax) or 0.21, 0.0, 0.35) if pretax and pretax > 0 else 0.21
        debt = _num(b, "totalDebt", default=0)
        cash = _num(b, "cashAndShortTermInvestments", "cashAndCashEquivalents", default=0)
        equity = _num(b, "totalStockholdersEquity", default=0)
        invested = equity + debt - cash
        ebitda = _num(s, "ebitda")
        rows.append({
            "year": s.get("fiscalYear") or (s.get("date") or "")[:4],
            "revenue": rev, "gross_margin": _safe_div(_num(s, "grossProfit"), rev),
            "operating_margin": _safe_div(ebit, rev), "net_margin": _safe_div(ni, rev),
            "ebit": ebit, "ebitda": ebitda, "net_income": ni,
            "eps": _num(s, "epsDiluted", "epsdiluted", "eps"),
            "shares": _num(s, "weightedAverageShsOutDil", "weightedAverageShsOut"),
            "fcf": fcf, "fcf_margin": _safe_div(fcf, rev), "cfo": _num(c, "operatingCashFlow"),
            "capex": abs(_num(c, "capitalExpenditure", default=0)), "dna": _num(c, "depreciationAndAmortization", default=0),
            "total_assets": _num(b, "totalAssets"), "total_debt": debt, "cash": cash, "equity": equity,
            "current_ratio": _safe_div(_num(b, "totalCurrentAssets"), _num(b, "totalCurrentLiabilities")),
            "lt_debt": _num(b, "longTermDebt", default=debt), "tax_rate": tax_rate,
            "interest_expense": _num(s, "interestExpense", default=0),
            "roe": _safe_div(ni, equity) if equity and equity > 0 else None,
            "roic": _safe_div(ebit * (1 - tax_rate), invested) if ebit is not None and invested > 0 else None,
            "net_debt_ebitda": _safe_div(debt - cash, ebitda) if ebitda and ebitda > 0 else None,
        })
    for i in range(len(rows) - 1):
        if rows[i]["revenue"] and rows[i + 1]["revenue"]:
            rows[i]["revenue_growth"] = rows[i]["revenue"] / rows[i + 1]["revenue"] - 1
    return rows  # newest first


# ------------------------------------------------------------------ DCF
def market_rates(currency: str | None) -> dict:
    """Risk-free rate and equity risk premium for the currency the stock trades in."""
    ccy = currency or "USD"
    rf, src = config.RISK_FREE_RATE, "default"
    if ccy == "USD":
        try:
            tnx = get_provider().quote("^TNX")["price"]
            if 0.5 < tnx < 15:
                rf, src = tnx / 100, "live US 10-year Treasury"
        except (DataError, KeyError, TypeError):
            pass
    elif ccy in config.RISK_FREE_BY_CCY:
        rf, src = config.RISK_FREE_BY_CCY[ccy], f"{ccy} 10-year government bond (default)"
    erp = config.EQUITY_RISK_PREMIUM + config.COUNTRY_RISK_BY_CCY.get(ccy, 0.0)
    return {"risk_free": rf, "erp": erp, "currency": ccy, "risk_free_source": src}


def wacc_calc(latest: dict, beta: float, market_cap: float, rates: dict | None = None) -> dict:
    rates = rates or {"risk_free": config.RISK_FREE_RATE, "erp": config.EQUITY_RISK_PREMIUM}
    beta = _clip(beta or 1.0, 0.5, 2.5)
    ke = rates["risk_free"] + beta * rates["erp"]
    debt = latest["total_debt"] or 0
    kd_pre = _clip(_safe_div(latest["interest_expense"], debt) or 0.05, 0.03, 0.10) if debt else 0.05
    t = latest["tax_rate"]
    kd = kd_pre * (1 - t)
    e = market_cap or 0
    wd = debt / (debt + e) if (debt + e) > 0 else 0
    wacc = _clip((1 - wd) * ke + wd * kd, 0.06, 0.14)
    return {"beta": beta, "risk_free": rates["risk_free"], "erp": rates["erp"], "risk_free_source": rates.get("risk_free_source"),
            "cost_of_equity": ke, "cost_of_debt_pre_tax": kd_pre, "tax_rate": t, "debt_weight": wd, "wacc": wacc}


def dcf(hist: list[dict], wacc: float, growth0: float, fcf_margin: float, g_term: float,
        net_debt: float, shares: float, margin_terminal: float | None = None,
        consensus: list[float] | None = None, shift: float = 0.0) -> dict:
    """Revenue path: consensus growth rates for the first years (if provided), then growth fades
    linearly from the last consensus rate (or growth0) to g_term by the final year.
    FCF margin moves linearly from fcf_margin (today) to margin_terminal (steady state).
    `shift` adds a constant to every growth rate (used for bull / bear cases)."""
    rev = hist[0]["revenue"]
    m_end = fcf_margin if margin_terminal is None else margin_terminal
    consensus = consensus or []
    k = min(len(consensus), DCF_YEARS - 1)
    g_start = consensus[k - 1] if k else growth0
    rows, pv_sum = [], 0.0
    for t in range(1, DCF_YEARS + 1):
        f = (t - 1) / (DCF_YEARS - 1)
        if t <= k:
            g, src = consensus[t - 1] + shift, "consensus"
        else:
            fade = (t - k) / (DCF_YEARS - k) if k else f
            g, src = g_start + shift * (1 - fade) + (g_term - g_start) * fade, "model"
        m = fcf_margin + (m_end - fcf_margin) * f
        rev = rev * (1 + g)
        fcf = rev * m
        df = 1 / (1 + wacc) ** t
        pv_sum += fcf * df
        rows.append({"year": t, "source": src, "growth": g, "revenue": rev, "fcf_margin": m, "fcf": fcf, "discount_factor": df, "pv_fcf": fcf * df})
    tv = rows[-1]["fcf"] * (1 + g_term) / (wacc - g_term)
    pv_tv = tv / (1 + wacc) ** DCF_YEARS
    ev = pv_sum + pv_tv
    equity = ev - net_debt
    return {"projection": rows, "pv_fcf_sum": pv_sum, "terminal_value": tv, "pv_terminal": pv_tv,
            "enterprise_value": ev, "equity_value": equity, "per_share": equity / shares if shares else None,
            "terminal_share_of_ev": pv_tv / ev if ev else None}


# ------------------------------------------------------------------ quality
def piotroski(h: list[dict]) -> dict:
    if len(h) < 2:
        return {"score": None, "checks": {}}
    c, p = h[0], h[1]
    roa = _safe_div(c["net_income"], c["total_assets"])
    roa_p = _safe_div(p["net_income"], p["total_assets"])
    lev = _safe_div(c["lt_debt"], c["total_assets"])
    lev_p = _safe_div(p["lt_debt"], p["total_assets"])
    turn = _safe_div(c["revenue"], c["total_assets"])
    turn_p = _safe_div(p["revenue"], p["total_assets"])

    def gt(a, b):
        return a is not None and b is not None and a > b

    checks = {
        "Positive ROA": roa is not None and roa > 0,
        "Positive operating cash flow": (c["cfo"] or 0) > 0,
        "ROA improving": gt(roa, roa_p),
        "Cash flow exceeds net income (earnings quality)": gt(c["cfo"], c["net_income"]),
        "Leverage falling": gt(lev_p, lev) or (lev == 0 and lev_p == 0),
        "Current ratio improving": gt(c["current_ratio"], p["current_ratio"]),
        "No share dilution": c["shares"] is not None and p["shares"] is not None and c["shares"] <= p["shares"] * 1.005,
        "Gross margin improving": gt(c["gross_margin"], p["gross_margin"]),
        "Asset turnover improving": gt(turn, turn_p),
    }
    return {"score": sum(checks.values()), "checks": checks}


# ------------------------------------------------------------------ peers
def relative_valuation(symbol: str, latest: dict, net_debt: float, shares: float, price: float) -> dict:
    provider = get_provider()
    own = provider.multiples(symbol)
    peer_syms = provider.peers(symbol)[:6]
    peer_rows = []
    for p in peer_syms:
        try:
            peer_rows.append(provider.multiples(p))
        except DataError:
            continue

    def med(key, cap):
        vals = [r[key] for r in peer_rows if r.get(key) and 0 < r[key] < cap]
        return st.median(vals) if len(vals) >= 2 else None

    medians = {"pe": med("pe", 100), "ev_ebitda": med("ev_ebitda", 60), "ps": med("ps", 40), "pfcf": med("pfcf", 100)}
    implied = {}
    if medians["pe"] and latest["eps"] and latest["eps"] > 0:
        implied["P/E"] = medians["pe"] * latest["eps"]
    if medians["ev_ebitda"] and latest["ebitda"] and latest["ebitda"] > 0 and shares:
        implied["EV/EBITDA"] = (medians["ev_ebitda"] * latest["ebitda"] - net_debt) / shares
    if medians["pfcf"] and latest["fcf"] and latest["fcf"] > 0 and shares:
        implied["P/FCF"] = medians["pfcf"] * latest["fcf"] / shares
    implied = {k: v for k, v in implied.items() if v and v > 0}
    if not implied and medians["ps"] and latest["revenue"] and shares:
        implied["P/S"] = medians["ps"] * latest["revenue"] / shares
    # guard against a single wild multiple: drop values > 4x or < 0.25x the current price
    implied = {k: v for k, v in implied.items() if 0.25 * price < v < 4 * price} or implied
    value = st.mean(implied.values()) if implied else None
    return {"own": own, "peers": peer_rows, "peer_medians": medians, "implied_prices": implied, "value": value}


# ------------------------------------------------------------------ main entry
def analyze(symbol: str, client_id: int | None = None) -> dict:
    symbol = symbol.upper().strip()
    provider = get_provider()
    quote = provider.quote(symbol)
    profile = provider.profile(symbol)
    price = quote["price"]
    stats = analytics.stock_stats(symbol)
    notes: list[str] = []

    momentum = _momentum_score(stats)
    if profile["is_etf"]:
        result = _etf_result(symbol, quote, profile, stats, momentum)
        return _finalize(result, client_id)

    inc, bal, cf = provider.income(symbol, 5), provider.balance(symbol, 5), provider.cashflow(symbol, 5)
    if not inc:
        raise DataError(f"No financial statements available for {symbol}")
    hist = build_history(inc, bal, cf)
    latest = hist[0]
    shares = latest["shares"] or ((quote.get("market_cap") or 0) / price if price else None)
    market_cap = quote.get("market_cap") or (shares * price if shares else None)
    net_debt = (latest["total_debt"] or 0) - (latest["cash"] or 0)

    # ---- growth & margin assumptions
    revs = [h["revenue"] for h in hist if h["revenue"]]
    n = len(revs) - 1
    cagr = (revs[0] / revs[-1]) ** (1 / n) - 1 if n >= 1 and revs[-1] > 0 else 0.03
    growth0 = _clip(cagr, -0.05, 0.25)
    margins = [h["fcf_margin"] for h in hist[:3] if h["fcf_margin"] is not None]
    fcf_margin = st.median(margins) if margins else None
    # Heavy growth capex (capex > 1.5x D&A) depresses today's FCF. Assume the margin converges halfway
    # toward "owner earnings" (CFO - D&A, i.e. maintenance capex ~ D&A) by the end of the forecast.
    margin_terminal = fcf_margin
    if fcf_margin is not None and latest["cfo"] and latest["revenue"] and latest["dna"] and latest["capex"] > 1.5 * latest["dna"]:
        owner_margin = (latest["cfo"] - latest["dna"]) / latest["revenue"]
        if owner_margin > fcf_margin:
            margin_terminal = (fcf_margin + owner_margin) / 2
            notes.append(f"Capex is {latest['capex'] / latest['dna']:.1f}x D&A (growth investment): FCF margin assumed to "
                         f"normalize from {fcf_margin:.1%} to {margin_terminal:.1%} over the forecast.")
    g_term = config.TERMINAL_GROWTH_BY_CCY.get(quote.get("currency") or "USD", config.TERMINAL_GROWTH)

    # ---- Street consensus (analyst estimates) for the first forecast years
    last_fy = (inc[0].get("date") or "")[:10]
    est = [e for e in provider.estimates(symbol) if e["date"] and e["date"][:10] > last_fy
           and e["revenue"] and e["revenue"] > 0 and e["n_analysts"] >= MIN_ANALYSTS][:CONSENSUS_YEARS]
    consensus, prev = [], latest["revenue"]
    for e in est:
        consensus.append(_clip(e["revenue"] / prev - 1, -0.30, 0.60))
        prev = e["revenue"]
    street = {"estimates": est, "revenue_growth": consensus, "price_target": provider.price_target(symbol)}
    if not est and getattr(provider, "summary_error", None):
        notes.append(provider.summary_error + " - growth is based on the company's own history instead of Street consensus.")
    next_eps = est[0]["eps"] if est and est[0].get("eps") else None
    street["forward_pe"] = price / next_eps if next_eps and next_eps > 0 else None
    if len(est) >= 2 and est[0].get("eps") and est[-1].get("eps") and est[0]["eps"] > 0 and est[-1]["eps"] > 0:
        street["eps_cagr"] = (est[-1]["eps"] / (latest["eps"] or est[0]["eps"])) ** (1 / len(est)) - 1 if latest["eps"] and latest["eps"] > 0 else None
    else:
        street["eps_cagr"] = None
    growth_source = (f"Consensus for years 1-{len(consensus)} ({min(e['n_analysts'] for e in est)}+ analysts), then fade"
                     if consensus else "Historical CAGR, fading to terminal")
    w = wacc_calc(latest, profile["beta"], market_cap, market_rates(quote.get("currency")))
    is_financial = "Financial" in (profile["sector"] or "")

    dcf_base = dcf_bull = dcf_bear = sens = None
    if fcf_margin is not None and fcf_margin > 0 and shares and not is_financial:
        dcf_base = dcf(hist, w["wacc"], growth0, fcf_margin, g_term, net_debt, shares, margin_terminal, consensus)
        dcf_bull = dcf(hist, max(0.06, w["wacc"] - 0.005), growth0, fcf_margin * 1.15, g_term, net_debt, shares,
                       margin_terminal * 1.15, consensus, shift=0.03)
        dcf_bear = dcf(hist, min(0.14, w["wacc"] + 0.01), growth0, fcf_margin * 0.85, g_term, net_debt, shares,
                       margin_terminal * 0.85, consensus, shift=-0.03)
        waccs = [w["wacc"] + d for d in (-0.01, -0.005, 0, 0.005, 0.01)]
        gs = [g_term + d for d in (-0.005, 0, 0.005)]
        sens = {"wacc": waccs, "g": gs,
                "values": [[dcf(hist, wc, growth0, fcf_margin, g, net_debt, shares, margin_terminal, consensus)["per_share"] for g in gs] for wc in waccs]}
        if dcf_base["terminal_share_of_ev"] and dcf_base["terminal_share_of_ev"] > 0.8:
            notes.append("Over 80% of DCF value sits in the terminal value — treat the DCF as low-precision.")
    elif is_financial:
        notes.append("Financial company: FCF-based DCF is not meaningful, valuation relies on peer multiples.")
    else:
        notes.append("Free cash flow is negative or unavailable, so the DCF was skipped; valuation relies on peer multiples.")

    rel = relative_valuation(symbol, latest, net_debt, shares, price)

    dcf_val = dcf_base["per_share"] if dcf_base and dcf_base["per_share"] and dcf_base["per_share"] > 0 else None
    if dcf_val and rel["value"]:
        fair = 0.6 * dcf_val + 0.4 * rel["value"]
        method = "60% DCF + 40% peer multiples"
    elif dcf_val:
        fair, method = dcf_val, "DCF only (no usable peer multiples)"
    elif rel["value"]:
        fair, method = rel["value"], "Peer multiples only"
    else:
        fair, method = None, "Insufficient data"
    upside = fair / price - 1 if fair else None

    pio = piotroski(hist)
    roic = latest["roic"]
    op_margins = [h["operating_margin"] for h in hist if h["operating_margin"] is not None]
    margin_stab = st.pstdev(op_margins) if len(op_margins) >= 3 else None
    quality = (
        (pio["score"] / 9 * 60 if pio["score"] is not None else 30)
        + _scale(roic, 0, 0.25) * 0.25
        + (15 - _clip((margin_stab or 0.05) / 0.10 * 15, 0, 15))
    )
    valuation_score = _scale(upside, -0.40, 0.40) if upside is not None else 50
    composite = 0.5 * valuation_score + 0.3 * quality + 0.2 * momentum["score"]

    rating, reasons = _rate(upside, composite, pio["score"], momentum, stats)
    pt = street["price_target"]
    if pt and pt.get("consensus"):
        street_up = pt["consensus"] / price - 1
        view = ("above" if fair and fair > pt["consensus"] * 1.05 else "below" if fair and fair < pt["consensus"] * 0.95 else "in line with")
        reasons.append(f"Street consensus target {pt['consensus']:,.2f} ({street_up:+.1%}); model fair value is {view} the Street.")
    result = {
        "symbol": symbol, "name": profile["name"], "sector": profile["sector"], "industry": profile["industry"],
        "is_etf": False, "price": price, "quote": quote, "description": profile["description"],
        "rating": rating, "fair_value": fair, "upside": upside, "valuation_method": method,
        "bull_value": dcf_bull["per_share"] if dcf_bull else None,
        "bear_value": dcf_bear["per_share"] if dcf_bear else None,
        "scores": {"valuation": round(valuation_score, 1), "quality": round(quality, 1),
                   "momentum": round(momentum["score"], 1), "composite": round(composite, 1)},
        "conviction": "High" if abs(composite - 50) > 20 else "Medium" if abs(composite - 50) > 10 else "Low",
        "reasons": reasons, "notes": notes,
        "assumptions": {"revenue_growth_start": growth0, "historical_cagr": cagr, "fcf_margin": fcf_margin,
                        "fcf_margin_terminal": margin_terminal, "growth_source": growth_source, "terminal_growth": g_term, "net_debt": net_debt, "shares": shares, "market_cap": market_cap,
                        **w},
        "dcf": dcf_base, "sensitivity": sens, "street": street, "relative": rel, "piotroski": pio,
        "history": hist, "technicals": stats, "momentum": momentum,
    }
    return _finalize(result, client_id)


def _momentum_score(stats: dict) -> dict:
    mom = _scale(stats["mom_12_1"] if stats["mom_12_1"] is not None else stats["ret_6m"], -0.30, 0.50)
    trend = 0
    if stats["sma200"]:
        trend += 50 if stats["price"] > stats["sma200"] else 0
        trend += 50 if stats["sma50"] > stats["sma200"] else 0
    else:
        trend = 50
    rsi = stats["rsi14"]
    rsi_s = 100 if 40 <= rsi <= 65 else 60 if 30 <= rsi <= 75 else 20
    return {"score": 0.5 * mom + 0.3 * trend + 0.2 * rsi_s,
            "uptrend": bool(stats["sma200"] and stats["price"] > stats["sma200"]),
            "overbought": rsi > 75, "oversold": rsi < 30}


def _rate(upside, composite, fscore, momentum, stats):
    reasons = []
    if upside is not None:
        reasons.append(f"Fair value implies {upside:+.1%} vs the current price.")
    if fscore is not None:
        reasons.append(f"Piotroski F-score {fscore}/9 ({'strong' if fscore >= 7 else 'average' if fscore >= 4 else 'weak'} fundamentals).")
    reasons.append("Price is above its 200-day average (uptrend)." if momentum["uptrend"]
                   else "Price is below its 200-day average (downtrend).")
    if momentum["overbought"]:
        reasons.append(f"RSI {stats['rsi14']:.0f}: short-term overbought — consider staging entries.")
    if momentum["oversold"]:
        reasons.append(f"RSI {stats['rsi14']:.0f}: short-term oversold.")

    if upside is not None and (upside <= SELL_DOWNSIDE or composite < 35 or (fscore is not None and fscore <= 2 and upside < 0)):
        return "SELL", reasons
    if upside is not None and upside >= BUY_UPSIDE and composite >= 55:
        return "BUY", reasons
    if upside is None and composite >= 65:
        return "BUY", reasons
    return "HOLD", reasons


def _etf_result(symbol, quote, profile, stats, momentum):
    rating = "HOLD"
    if momentum["score"] >= 70:
        rating = "BUY"
    elif momentum["score"] < 30:
        rating = "SELL"
    return {
        "symbol": symbol, "name": profile["name"], "sector": "ETF", "industry": "", "is_etf": True,
        "price": quote["price"], "quote": quote, "description": profile["description"],
        "rating": rating, "fair_value": None, "upside": None, "valuation_method": "Trend / momentum (ETF)",
        "scores": {"valuation": None, "quality": None, "momentum": round(momentum["score"], 1),
                   "composite": round(momentum["score"], 1)},
        "conviction": "Low", "momentum": momentum, "technicals": stats,
        "reasons": ["ETFs are held for asset-class exposure; the signal here is trend/momentum only.",
                    "Price is above its 200-day average." if momentum["uptrend"] else "Price is below its 200-day average."],
        "notes": ["Strategic ETF weights come from the client's model portfolio — use rebalancing rather than this signal to size them."],
    }


def _finalize(result: dict, client_id: int | None) -> dict:
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO research_cache(symbol,rating,fair_value,score,payload,updated_at) VALUES(?,?,?,?,?,?)",
                  (result["symbol"], result["rating"], result.get("fair_value"), result["scores"]["composite"],
                   db.dumps({k: result[k] for k in ("rating", "fair_value", "upside", "scores", "price")}), db.now_iso()))
    if client_id:
        result["client_view"] = client_overlay(result, client_id)
    return result


def client_overlay(r: dict, client_id: int) -> dict:
    """Turn the stock-level rating into an action for a specific client."""
    client = portfolio.get_client(client_id)
    prof = risk_profile.profile_by_name(client["risk_profile"])
    val = portfolio.valuation(client_id)
    held = next((h for h in val["holdings"] if h["symbol"] == r["symbol"] and h["qty"] > 0), None)
    weight = held["weight"] if held else 0.0
    max_pos = min(client["max_position"], prof["max_position"]) if not r["is_etf"] else None
    from . import direct_index
    di = direct_index.symbols(client_id)
    stock_w = sum(h["weight"] for h in val["holdings"] if h["qty"] > 0 and h["asset_class"].startswith(("Stock", "Individual"))
                  and h["symbol"] not in di)
    vol = r["technicals"]["vol_1y"]
    warnings = []
    suitable = True
    if not r["is_etf"]:
        if prof["stock_sleeve"] == 0:
            suitable = False
            warnings.append(f"{prof['name']} profile has no individual-stock allocation.")
        if vol > prof["vol_band"][1] * 3:
            warnings.append(f"Stock volatility {vol:.0%} is high relative to the {prof['name']} risk band "
                            f"({prof['vol_band'][0]:.0%}-{prof['vol_band'][1]:.0%} portfolio vol). Keep size small.")
            if prof["name"] in ("Conservative", "Moderately Conservative"):
                suitable = False
    room = None
    if max_pos is not None:
        room = max(0.0, min(max_pos - weight, prof["stock_sleeve"] - stock_w))
    rating = r["rating"]
    if not suitable and not held:
        action = "AVOID (unsuitable for profile)"
    elif held:
        if rating == "SELL":
            action = "SELL / TRIM"
        elif rating == "BUY" and room and room > 0.005 and suitable:
            action = f"ADD (room for ~{room:.1%} more)"
        elif rating == "BUY":
            action = "HOLD (at position/sleeve limit)"
        else:
            action = "HOLD"
    else:
        if rating == "BUY" and room and room > 0.005:
            action = f"BUY (suggested weight {min(room, max_pos):.1%})"
        elif rating == "BUY":
            action = "WATCHLIST (no room in stock sleeve)"
        elif rating == "SELL":
            action = "AVOID"
        else:
            action = "WATCHLIST"
    suggested = min(room, max_pos) if room and rating == "BUY" and suitable else 0
    return {"client": client["name"], "profile": prof["name"], "held": bool(held), "current_weight": weight,
            "max_position": max_pos, "stock_sleeve_cap": prof["stock_sleeve"], "stock_sleeve_used": stock_w,
            "suitable": suitable, "action": action, "warnings": warnings,
            "suggested_weight": suggested,
            "suggested_shares": (suggested * val["equity"] / r["price"]) if suggested else 0}


def cached_ratings(symbols: list[str]) -> dict:
    if not symbols:
        return {}
    qs = ",".join("?" for _ in symbols)
    return {r["symbol"]: r for r in db.query(f"SELECT symbol, rating, fair_value, score, updated_at FROM research_cache WHERE symbol IN ({qs})", tuple(symbols))}
