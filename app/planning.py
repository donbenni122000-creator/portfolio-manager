"""Goal-based financial planning with Monte Carlo simulation.

Expected return / volatility come from long-run Capital Market Assumptions (CMA) per asset class -
more stable than a few years of history - combined with a simple correlation structure.
Each goal is simulated monthly (lognormal returns + monthly contributions). Targets can be
inflation-adjusted (entered in today's dollars). Outputs: probability of success, percentile
paths, and the monthly saving needed for an 80% chance.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np

from . import config, db, portfolio, risk_profile

INFLATION = 0.025
N_SIMS = 4000
TARGET_PROB = 0.80

# asset class -> (expected annual return, annual volatility, correlation group)
CMA = {
    "US Equity": (0.070, 0.160, "eq"), "US Equity (Growth)": (0.075, 0.200, "eq"),
    "US Equity (Dividend)": (0.068, 0.140, "eq"), "US Equity (Factor)": (0.071, 0.150, "eq"),
    "US Equity (ESG)": (0.069, 0.160, "eq"), "Sector Equity": (0.070, 0.200, "eq"),
    "Intl Equity": (0.075, 0.170, "eq"), "EM Equity": (0.080, 0.210, "eq"),
    "Core Bonds": (0.045, 0.055, "bond"), "Corporate Bonds": (0.050, 0.065, "bond"),
    "Long Treasury Bonds": (0.045, 0.140, "bond"), "Intermediate Treasury Bonds": (0.042, 0.060, "bond"),
    "Inflation-Linked Bonds": (0.043, 0.055, "bond"), "Cash / T-Bills": (0.035, 0.005, "cash"),
    "Real Estate": (0.065, 0.190, "reit"), "Gold": (0.040, 0.150, "gold"), "Commodities": (0.040, 0.180, "cmdty"),
    "Stock": (0.075, 0.250, "stk"),
}
CORR = {("eq", "eq"): 0.85, ("bond", "bond"): 0.80, ("eq", "bond"): 0.15, ("eq", "reit"): 0.70, ("bond", "reit"): 0.25,
        ("reit", "reit"): 1.0, ("eq", "cmdty"): 0.35, ("bond", "cmdty"): 0.0, ("reit", "cmdty"): 0.25,
        ("eq", "gold"): 0.05, ("bond", "gold"): 0.20, ("reit", "gold"): 0.05, ("gold", "cmdty"): 0.40,
        ("gold", "gold"): 1.0, ("cmdty", "cmdty"): 1.0,
        # individual stocks: moderately correlated with each other, strongly with the market
        ("stk", "stk"): 0.35, ("eq", "stk"): 0.65, ("bond", "stk"): 0.10, ("reit", "stk"): 0.50, ("gold", "stk"): 0.05,
        ("cmdty", "stk"): 0.30}


def _cma(symbol: str):
    ac = risk_profile.asset_class(symbol)
    if ac.startswith(("Stock", "Individual")):
        return CMA["Stock"]
    return CMA.get(ac, CMA["US Equity"])


def portfolio_cma(weights: dict[str, float], cash_weight: float = 0.0) -> dict:
    """Expected return and volatility for a set of weights (cash earns the cash rate, no risk)."""
    syms = [s for s, w in weights.items() if w > 0]
    w = np.array([weights[s] for s in syms], dtype=float)
    mu = np.array([_cma(s)[0] for s in syms])
    sd = np.array([_cma(s)[1] for s in syms])
    grp = [_cma(s)[2] for s in syms]
    n = len(syms)
    cov = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i == j:
                rho = 1.0
            elif syms[i] == syms[j]:
                rho = 1.0
            else:
                a, b = sorted((grp[i], grp[j]))
                rho = 0.0 if "cash" in (a, b) else CORR.get((a, b), CORR.get((b, a), 0.3))
            cov[i, j] = rho * sd[i] * sd[j]
    exp_ret = float(w @ mu) + cash_weight * config.CASH_INTEREST_RATE
    vol = float(math.sqrt(max(0.0, w @ cov @ w)))
    return {"expected_return": exp_ret, "volatility": vol}


def fitting_profiles(vol: float) -> list[str]:
    return [p["name"] for p in risk_profile.PROFILES if p["vol_band"][0] - 0.01 <= vol <= p["vol_band"][1] + 0.01]


def client_assumptions(client_id: int) -> dict:
    c = portfolio.get_client(client_id)
    ct = c["cash_target"]
    t = {s: w * (1 - ct) for s, w in portfolio.get_targets(client_id).items()}
    return portfolio_cma(t, ct)


# ------------------------------------------------------------------ goals
def list_goals(client_id: int) -> list[dict]:
    return db.query("SELECT * FROM goals WHERE client_id=? ORDER BY target_date", (client_id,))


def save_goal(client_id: int, name: str, target_amount: float, target_date: str, monthly_contribution: float = 0,
              allocation_pct: float = 1.0, inflation_adjust: bool = True, goal_id: int | None = None) -> dict:
    portfolio.get_client(client_id)
    td = date.fromisoformat(target_date[:10])
    if td <= date.today():
        raise ValueError("Target date must be in the future")
    if target_amount <= 0 or not 0 < allocation_pct <= 1 or monthly_contribution < 0:
        raise ValueError("Check the amounts: target > 0, allocation 1-100%, contribution >= 0")
    vals = (name, target_amount, td.isoformat(), monthly_contribution, allocation_pct, int(bool(inflation_adjust)))
    with db.tx() as c:
        if goal_id:
            c.execute("""UPDATE goals SET name=?, target_amount=?, target_date=?, monthly_contribution=?, allocation_pct=?,
                         inflation_adjust=? WHERE id=? AND client_id=?""", (*vals, goal_id, client_id))
        else:
            goal_id = c.execute("""INSERT INTO goals(name,target_amount,target_date,monthly_contribution,allocation_pct,
                                   inflation_adjust,client_id,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                                (*vals, client_id, db.now_iso())).lastrowid
    return db.query_one("SELECT * FROM goals WHERE id=?", (goal_id,))


def delete_goal(client_id: int, goal_id: int) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM goals WHERE id=? AND client_id=?", (goal_id, client_id))


# ------------------------------------------------------------------ simulation
def _simulate(start: float, monthly: float, months: int, mu: float, vol: float, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    m_mu = math.log(1 + mu) / 12 - 0.5 * (vol ** 2) / 12
    m_sd = vol / math.sqrt(12)
    growth = np.exp(rng.normal(m_mu, m_sd, (N_SIMS, months)))
    vals = np.empty((N_SIMS, months + 1))
    vals[:, 0] = start
    for t in range(months):
        vals[:, t + 1] = vals[:, t] * growth[:, t] + monthly
    return vals


def run_goal(goal: dict, start_value: float, mu: float, vol: float) -> dict:
    td = date.fromisoformat(goal["target_date"][:10])
    months = max(1, (td.year - date.today().year) * 12 + (td.month - date.today().month))
    years = months / 12
    target = goal["target_amount"] * ((1 + INFLATION) ** years if goal["inflation_adjust"] else 1)
    start = start_value * goal["allocation_pct"]
    vals = _simulate(start, goal["monthly_contribution"], months, mu, vol)
    end = vals[:, -1]
    prob = float((end >= target).mean())
    step = max(1, months // 40)
    idx = list(range(0, months + 1, step)) + ([months] if months % step else [])
    pct = {p: np.percentile(vals[:, idx], p, axis=0) for p in (10, 25, 50, 75, 90)}
    path = [{"month": int(i), "date": _add_months(date.today(), int(i)).isoformat(),
             **{f"p{p}": round(float(pct[p][k]), 0) for p in pct}} for k, i in enumerate(idx)]

    # monthly saving needed for TARGET_PROB (bisection on the same random draws)
    def prob_at(c):
        return float((_simulate(start, c, months, mu, vol)[:, -1] >= target).mean())
    lo, hi = 0.0, max(100.0, target / months)
    if prob_at(0) >= TARGET_PROB:
        needed = 0.0
    else:
        while prob_at(hi) < TARGET_PROB and hi < 1e7:
            hi *= 2
        for _ in range(22):
            mid = (lo + hi) / 2
            lo, hi = (lo, mid) if prob_at(mid) >= TARGET_PROB else (mid, hi)
        needed = hi
    return {
        "goal": goal, "months": months, "target_nominal": target, "start_value": start,
        "expected_return": mu, "volatility": vol, "probability": prob,
        "status": "on track" if prob >= TARGET_PROB else "at risk" if prob >= 0.5 else "off track",
        "median_end": float(np.median(end)), "p10_end": float(np.percentile(end, 10)), "p90_end": float(np.percentile(end, 90)),
        "required_monthly_for_80pct": needed, "extra_monthly_needed": max(0.0, needed - goal["monthly_contribution"]),
        "path": path,
    }


def _add_months(d: date, m: int) -> date:
    y, mo = divmod(d.month - 1 + m, 12)
    return date(d.year + y, mo + 1, min(d.day, 28))


def plan(client_id: int) -> dict:
    """Run every goal for a client, plus a 'what if' across the five risk profiles for each goal."""
    val = portfolio.valuation(client_id)
    a = client_assumptions(client_id)
    from . import model_library
    results = []
    for g in list_goals(client_id):
        r = run_goal(g, val["equity"], a["expected_return"], a["volatility"])
        what_if = []
        for p in risk_profile.PROFILES:
            m = model_library.get_model(model_library.core_model_id(p["name"]))
            ca = portfolio_cma(m["holdings"], 0.02)
            prob = float((_simulate(r["start_value"], g["monthly_contribution"], r["months"], ca["expected_return"],
                                    ca["volatility"])[:, -1] >= r["target_nominal"]).mean())
            what_if.append({"profile": p["name"], "expected_return": ca["expected_return"], "volatility": ca["volatility"],
                            "probability": prob})
        r["what_if"] = what_if
        results.append(r)
    return {"portfolio_value": val["equity"], "assumptions": a, "inflation": INFLATION, "simulations": N_SIMS,
            "goals": results}
