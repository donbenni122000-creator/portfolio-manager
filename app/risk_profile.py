"""Client risk profiling and strategic (model) asset allocation.

Risk score blends *willingness* (attitude questions) and *capacity* (horizon, income, liquidity).
Following common suitability practice the final score is capped by capacity: a client who is eager
for risk but needs the money in two years still gets a conservative portfolio.
"""
from __future__ import annotations

QUESTIONNAIRE = [
    # id, question, kind (capacity/willingness), options [(label, points)]
    {"id": "age", "kind": "capacity", "question": "What is the client's age?",
     "options": [["Under 30", 10], ["30-44", 8], ["45-54", 6], ["55-64", 3], ["65+", 1]]},
    {"id": "horizon", "kind": "capacity", "question": "When will a significant part of this money be needed?",
     "options": [["Less than 2 years", 0], ["2-5 years", 3], ["5-10 years", 7], ["10+ years", 10]]},
    {"id": "income", "kind": "capacity", "question": "How stable is the client's income?",
     "options": [["Very unstable / none", 1], ["Somewhat stable", 5], ["Stable", 8], ["Very stable, growing", 10]]},
    {"id": "liquidity", "kind": "capacity", "question": "What share of total net worth is this portfolio?",
     "options": [["More than 75%", 2], ["50-75%", 4], ["25-50%", 7], ["Less than 25%", 10]]},
    {"id": "goal", "kind": "willingness", "question": "Primary investment objective?",
     "options": [["Preserve capital", 1], ["Income", 3], ["Balanced growth and income", 6], ["Growth", 8], ["Maximum growth", 10]]},
    {"id": "drawdown", "kind": "willingness", "question": "The portfolio falls 25% in a year. What does the client do?",
     "options": [["Sell everything", 0], ["Sell some", 3], ["Hold", 7], ["Buy more", 10]]},
    {"id": "loss_tolerance", "kind": "willingness", "question": "Largest one-year loss the client could accept?",
     "options": [["0-5%", 1], ["5-10%", 3], ["10-20%", 6], ["20-30%", 8], ["More than 30%", 10]]},
    {"id": "experience", "kind": "willingness", "question": "Investment experience?",
     "options": [["None", 2], ["Some (funds / savings)", 5], ["Experienced (stocks)", 8], ["Professional", 10]]},
]

PROFILES = [
    # name, score ceiling, target vol band, max drawdown tolerance, max single stock, stock sleeve cap
    {"name": "Conservative", "max_score": 30, "vol_band": [0.03, 0.07], "max_dd": 0.10, "max_position": 0.03, "stock_sleeve": 0.00,
     "exp_return": 0.045},
    {"name": "Moderately Conservative", "max_score": 45, "vol_band": [0.05, 0.09], "max_dd": 0.15, "max_position": 0.04, "stock_sleeve": 0.10,
     "exp_return": 0.055},
    {"name": "Moderate", "max_score": 60, "vol_band": [0.07, 0.12], "max_dd": 0.22, "max_position": 0.05, "stock_sleeve": 0.20,
     "exp_return": 0.065},
    {"name": "Growth", "max_score": 78, "vol_band": [0.10, 0.16], "max_dd": 0.30, "max_position": 0.08, "stock_sleeve": 0.30,
     "exp_return": 0.075},
    {"name": "Aggressive", "max_score": 100, "vol_band": [0.13, 0.22], "max_dd": 0.40, "max_position": 0.10, "stock_sleeve": 0.40,
     "exp_return": 0.085},
]

ASSET_CLASS = {
    # US equity
    "VTI": "US Equity", "SPY": "US Equity", "ITOT": "US Equity", "SCHB": "US Equity", "IVV": "US Equity",
    "SPLG": "US Equity", "VOO": "US Equity",
    "QQQ": "US Equity (Growth)", "VGT": "US Equity (Growth)", "SMH": "US Equity (Growth)", "SOXX": "US Equity (Growth)",
    "SCHD": "US Equity (Dividend)", "VYM": "US Equity (Dividend)", "VIG": "US Equity (Dividend)", "DGRO": "US Equity (Dividend)",
    "VLUE": "US Equity (Factor)", "QUAL": "US Equity (Factor)", "MTUM": "US Equity (Factor)", "USMV": "US Equity (Factor)",
    "ESGU": "US Equity (ESG)", "SUSA": "US Equity (ESG)",
    "XLK": "Sector Equity", "XLF": "Sector Equity", "XLV": "Sector Equity", "XLY": "Sector Equity", "XLP": "Sector Equity",
    "XLE": "Sector Equity", "XLC": "Sector Equity", "XLI": "Sector Equity", "XLU": "Sector Equity", "XLB": "Sector Equity",
    # international
    "VXUS": "Intl Equity", "IXUS": "Intl Equity", "VEA": "Intl Equity", "IEFA": "Intl Equity", "ESGD": "Intl Equity",
    "SUSL": "Intl Equity", "VWO": "EM Equity", "IEMG": "EM Equity",
    # fixed income & cash
    "BND": "Core Bonds", "AGG": "Core Bonds", "EAGG": "Core Bonds", "VCIT": "Corporate Bonds", "IGIB": "Corporate Bonds",
    "TLT": "Long Treasury Bonds", "VGLT": "Long Treasury Bonds", "IEF": "Intermediate Treasury Bonds",
    "VGIT": "Intermediate Treasury Bonds", "TIP": "Inflation-Linked Bonds", "SCHP": "Inflation-Linked Bonds",
    "SHV": "Cash / T-Bills", "BIL": "Cash / T-Bills",
    # real assets
    "VNQ": "Real Estate", "SCHH": "Real Estate", "XLRE": "Real Estate", "GLD": "Gold", "IAU": "Gold", "DBC": "Commodities",
}

MODEL_PORTFOLIOS = {
    "Conservative":            {"SHV": 0.10, "BND": 0.45, "TIP": 0.10, "VTI": 0.20, "VXUS": 0.08, "VNQ": 0.02, "GLD": 0.05},
    "Moderately Conservative": {"SHV": 0.05, "BND": 0.35, "TIP": 0.08, "VTI": 0.30, "VXUS": 0.12, "VNQ": 0.04, "GLD": 0.06},
    "Moderate":                {"SHV": 0.03, "BND": 0.25, "TIP": 0.05, "VTI": 0.40, "VXUS": 0.17, "VNQ": 0.05, "GLD": 0.05},
    "Growth":                  {"BND": 0.12, "TIP": 0.03, "VTI": 0.50, "VXUS": 0.20, "QQQ": 0.08, "VNQ": 0.04, "GLD": 0.03},
    "Aggressive":              {"BND": 0.05, "VTI": 0.50, "VXUS": 0.20, "QQQ": 0.15, "VWO": 0.05, "VNQ": 0.03, "GLD": 0.02},
}


def score_questionnaire(answers: dict) -> dict:
    """answers: {question_id: option_index}. Returns score (0-100), profile and breakdown."""
    cap_pts, cap_max, will_pts, will_max = 0, 0, 0, 0
    for q in QUESTIONNAIRE:
        idx = answers.get(q["id"])
        if idx is None:
            raise ValueError(f"Missing answer for '{q['id']}'")
        idx = int(idx)
        if not 0 <= idx < len(q["options"]):
            raise ValueError(f"Invalid option for '{q['id']}'")
        pts = q["options"][idx][1]
        top = max(o[1] for o in q["options"])
        if q["kind"] == "capacity":
            cap_pts, cap_max = cap_pts + pts, cap_max + top
        else:
            will_pts, will_max = will_pts + pts, will_max + top
    capacity = 100 * cap_pts / cap_max
    willingness = 100 * will_pts / will_max
    blended = 0.5 * capacity + 0.5 * willingness
    # capacity caps the outcome: never more than 15 points above capacity
    score = round(min(blended, capacity + 15), 1)
    horizon_idx = int(answers["horizon"])
    if horizon_idx == 0:                       # money needed within 2 years
        score = min(score, 30)
    profile = profile_for_score(score)
    return {"score": score, "capacity": round(capacity, 1), "willingness": round(willingness, 1),
            "profile": profile["name"], "profile_detail": profile,
            "capped_by_capacity": score < round(blended, 1)}


def profile_for_score(score: float) -> dict:
    for p in PROFILES:
        if score <= p["max_score"]:
            return p
    return PROFILES[-1]


def profile_by_name(name: str) -> dict:
    for p in PROFILES:
        if p["name"] == name:
            return p
    raise ValueError(f"Unknown profile {name}")


def model_targets(profile_name: str) -> dict[str, float]:
    return dict(MODEL_PORTFOLIOS[profile_name])


def asset_class(symbol: str, sector: str | None = None) -> str:
    return ASSET_CLASS.get(symbol.upper()) or (f"Stock · {sector}" if sector else "Individual Stock")


def add_stock_to_targets(targets: dict[str, float], symbol: str, weight: float, profile_name: str,
                         core_equity=("VTI", "SPY", "QQQ")) -> dict[str, float]:
    """Carve a stock position out of the core equity ETFs so total equity exposure stays the same.
    Enforces the profile's single-stock and stock-sleeve limits."""
    prof = profile_by_name(profile_name)
    symbol = symbol.upper()
    if weight > prof["max_position"] + 1e-9:
        raise ValueError(f"{profile_name} profile allows at most {prof['max_position']:.0%} in a single stock")
    stocks_now = sum(w for s, w in targets.items() if s not in ASSET_CLASS and s != symbol)
    if stocks_now + weight > prof["stock_sleeve"] + 1e-9:
        raise ValueError(f"{profile_name} profile caps individual stocks at {prof['stock_sleeve']:.0%} of the portfolio "
                         f"(currently {stocks_now:.0%} allocated)")
    t = dict(targets)
    t.pop(symbol, None)
    need = weight
    core = [s for s in core_equity if t.get(s, 0) > 0]
    pool = sum(t[s] for s in core)
    if pool < need:
        # fall back to all non-bond ETFs
        core = [s for s in t if s in ASSET_CLASS and "Bond" not in ASSET_CLASS[s] and "Cash" not in ASSET_CLASS[s]]
        pool = sum(t[s] for s in core)
    if pool < need:
        raise ValueError("Not enough equity allocation to fund this stock position")
    for s in core:
        t[s] -= need * t[s] / pool
    t[symbol] = weight
    return normalize({k: v for k, v in t.items() if v > 1e-6})


def normalize(t: dict[str, float]) -> dict[str, float]:
    total = sum(t.values())
    return {k: round(v / total, 6) for k, v in t.items()} if total > 0 else t
