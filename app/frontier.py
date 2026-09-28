"""Efficient Frontier Lab (Markowitz modern portfolio theory).

Pick any set of stocks/ETFs, estimate expected returns and the covariance matrix from price history, and
trace the long-only efficient frontier (optionally with a max weight per holding). Returns:

* the frontier curve (volatility, return, weights at every point)
* the minimum-variance portfolio and the maximum-Sharpe (tangency) portfolio, plus the Capital Market Line
* each asset on its own, a cloud of random portfolios (what "inefficient" looks like)
* your own mix (optional): where it sits, and the efficient portfolios with the same risk / same return
* correlation matrix and an in-sample growth-of-10k backtest

Expected returns can be historical (sample mean), CAPM/equilibrium (rf + beta x ERP) or a 50/50 blend.
Covariance is the sample covariance of weekly returns, optionally Ledoit-Wolf shrunk toward a
constant-correlation target (more stable weights). Weekly returns align markets with different holidays.
Optimisation is a small projected-gradient QP (numpy only, no scipy needed).
"""
from __future__ import annotations

import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import config
from .market_data import DataError, get_provider

MAX_ASSETS = 25
LOOKBACKS = {"1y": 365, "3y": 3 * 365, "5y": 5 * 365}
METHODS = ("blend", "historical", "capm")
BENCH = "^GSPC"


# ------------------------------------------------------------------ inputs
_WEEKLY: dict[tuple[str, int], tuple[float, object]] = {}      # (symbol, days) -> (time, weekly prices | error)
_RESULTS: dict[str, tuple[float, dict]] = {}                   # request key -> (time, result)
_LOCK = threading.Lock()
TTL_PRICES = 30 * 60
TTL_RESULT = 10 * 60


def _weekly_prices(symbol: str, days: int):
    """Weekly closes for one symbol, memoised in memory (the slow part is the Yahoo download)."""
    key, now = (symbol, days), time.time()
    with _LOCK:
        hit = _WEEKLY.get(key)
    if hit and now - hit[0] < TTL_PRICES:
        return hit[1]
    try:
        h = get_provider().history(symbol, days)
        w = h.resample("W-FRI").last().dropna()
        val = w if len(w) >= 26 else DataError("less than 6 months of history")
    except DataError as e:
        val = e
    with _LOCK:
        _WEEKLY[key] = (now, val)
    return val


def _fetch_all(symbols: list[str], days: int) -> list:
    if len(symbols) <= 1 or config.DATA_MODE == "sim":
        return [_weekly_prices(s, days) for s in symbols]
    with ThreadPoolExecutor(max_workers=12) as ex:                # download in parallel
        return list(ex.map(lambda s: _weekly_prices(s, days), symbols))


def prefetch(symbols: list[str], lookback: str = "3y") -> dict:
    """Warm the price cache in the background so 'Build' is instant."""
    syms = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))[:MAX_ASSETS] + [BENCH]
    res = _fetch_all(syms, LOOKBACKS.get(lookback, LOOKBACKS["3y"]))
    try:
        _risk_free()
    except Exception:
        pass
    return {"ready": [s for s, r in zip(syms, res) if not isinstance(r, Exception)],
            "failed": {s: str(r) for s, r in zip(syms, res) if isinstance(r, Exception)}}


def _weekly_returns(symbols: list[str], days: int) -> tuple[pd.DataFrame, list[str]]:
    series, skipped = {}, []
    for s, r in zip(symbols, _fetch_all(symbols, days)):
        if isinstance(r, Exception):
            skipped.append(f"{s} ({r})")
        else:
            series[s] = r
    if not series:
        return pd.DataFrame(), skipped
    px = pd.DataFrame(series).sort_index().ffill().dropna()
    return px.pct_change().dropna(), skipped


def _risk_free() -> tuple[float, str]:
    try:
        tnx = get_provider().quote("^TNX")["price"]
        if 0.5 < tnx < 15:
            return tnx / 100, "live US 10-year Treasury"
    except (DataError, KeyError, TypeError):
        pass
    return config.RISK_FREE_RATE, "default"


def _shrink_cov(R: np.ndarray) -> tuple[np.ndarray, float]:
    """Ledoit-Wolf shrinkage toward the constant-correlation target. Returns (cov, intensity)."""
    t, n = R.shape
    X = R - R.mean(0)
    S = X.T @ X / t
    sd = np.sqrt(np.diag(S))
    corr = S / np.outer(sd, sd)
    rbar = (corr.sum() - n) / (n * (n - 1)) if n > 1 else 0.0
    F = rbar * np.outer(sd, sd)
    np.fill_diagonal(F, np.diag(S))
    # pi-hat, rho-hat, gamma-hat (Ledoit & Wolf 2004, "Honey, I shrunk the sample covariance matrix")
    Y = X ** 2
    pi_mat = Y.T @ Y / t - S ** 2
    pi_hat = pi_mat.sum()
    theta_ii = (X ** 3).T @ X / t - np.diag(S)[:, None] * S          # theta_{ii,ij}
    rho_off = 0.0
    for i in range(n):
        for j in range(n):
            if i != j:
                rho_off += rbar / 2 * (sd[j] / sd[i] * theta_ii[i, j] + sd[i] / sd[j] * theta_ii[j, i])
    rho_hat = np.trace(pi_mat) + rho_off
    gamma_hat = ((F - S) ** 2).sum()
    kappa = (pi_hat - rho_hat) / gamma_hat if gamma_hat > 0 else 1.0
    delta = float(min(1.0, max(0.0, kappa / t)))
    return delta * F + (1 - delta) * S, delta


# ------------------------------------------------------------------ optimiser
def _project(v: np.ndarray, ub: np.ndarray) -> np.ndarray:
    """Euclidean projection onto {w : sum w = 1, 0 <= w <= ub} (bisection on the shift)."""
    lo, hi = v.min() - ub.max() - 1, v.max() + 1
    for _ in range(80):
        tau = (lo + hi) / 2
        s = np.clip(v - tau, 0, ub).sum()
        if s > 1:
            lo = tau
        else:
            hi = tau
    return np.clip(v - (lo + hi) / 2, 0, ub)


def _solve(mu: np.ndarray, cov: np.ndarray, lam: float, ub: np.ndarray, w0: np.ndarray | None = None,
           iters: int = 200) -> np.ndarray:
    """min  w'Cw - lam * mu'w   s.t. sum w = 1, 0 <= w <= ub.

    Exact primal active-set QP (Nocedal & Wright ch. 16): fast and precise for the <= 25 assets used here."""
    n = len(mu)
    Q, c = 2 * cov, -lam * mu
    w = _project(w0 if w0 is not None else np.full(n, 1 / n), ub)
    tol = 1e-10
    lower = set(np.where(w <= tol)[0])
    upper = set(np.where(w >= ub - tol)[0]) - lower
    for _ in range(iters):
        W = lower | upper
        F = [i for i in range(n) if i not in W]
        fixed = np.zeros(n)
        for i in upper:
            fixed[i] = ub[i]
        g_full = None
        if F:
            Fi = np.array(F)
            rhs_sum = 1 - fixed.sum()
            K = np.zeros((len(F) + 1, len(F) + 1))
            K[:-1, :-1] = Q[np.ix_(Fi, Fi)]
            K[:-1, -1] = 1
            K[-1, :-1] = 1
            rhs = np.concatenate([-(c[Fi] + Q[Fi] @ fixed), [rhs_sum]])
            try:
                sol = np.linalg.solve(K, rhs)
            except np.linalg.LinAlgError:
                sol = np.linalg.lstsq(K, rhs, rcond=None)[0]
            target = fixed.copy()
            target[Fi] = sol[:-1]
            nu = -sol[-1]                                # g_i = nu on the free set
        else:
            target, nu = fixed.copy(), None
        step = target - w
        if np.abs(step).max() > 1e-12:
            # largest feasible step toward the target; add the first blocking bound to the working set
            alpha, block = 1.0, None
            for i in F:
                if step[i] < -1e-15 and w[i] + step[i] < 0:
                    a_i = -w[i] / step[i]
                    if a_i < alpha:
                        alpha, block = a_i, (i, "lower")
                elif step[i] > 1e-15 and w[i] + step[i] > ub[i]:
                    a_i = (ub[i] - w[i]) / step[i]
                    if a_i < alpha:
                        alpha, block = a_i, (i, "upper")
            w = w + alpha * step
            if block:
                i, side = block
                w[i] = 0.0 if side == "lower" else ub[i]
                (lower if side == "lower" else upper).add(i)
                continue
        w = target if np.abs(step).max() > 1e-12 else w
        # optimal on this working set: check multipliers of the active bounds
        g_full = Q @ w + c
        if nu is None:
            nu = float(np.median(g_full))
        worst, drop = 1e-12, None
        for i in lower:                                   # at 0: need g_i >= nu
            if nu - g_full[i] > worst:
                worst, drop = nu - g_full[i], i
        for i in upper:                                   # at cap: need g_i <= nu
            if g_full[i] - nu > worst:
                worst, drop = g_full[i] - nu, i
        if drop is None:
            break
        lower.discard(drop)
        upper.discard(drop)
    w = np.clip(w, 0, ub)
    return w / w.sum()


def _stats(w, mu, cov, rf):
    r = float(w @ mu)
    v = float(math.sqrt(max(0.0, w @ cov @ w)))
    return r, v, (r - rf) / v if v > 0 else None


# ------------------------------------------------------------------ main entry
def _analyze(symbols: list[str], lookback: str = "3y", method: str = "blend", max_weight: float = 0.40,
            shrink: bool = True, my_weights: dict[str, float] | None = None, n_points: int = 40,
            n_random: int = 800, risk_free: float | None = None) -> dict:
    syms = []
    for s in symbols:
        s = s.strip().upper()
        if s and s not in syms:
            syms.append(s)
    if len(syms) < 2:
        raise ValueError("Add at least two stocks or ETFs to build a frontier.")
    if len(syms) > MAX_ASSETS:
        raise ValueError(f"Use at most {MAX_ASSETS} holdings.")
    if method not in METHODS:
        raise ValueError(f"method must be one of {', '.join(METHODS)}")
    days = LOOKBACKS.get(lookback, LOOKBACKS["3y"])

    rets, skipped = _weekly_returns(syms + [BENCH], days)
    if BENCH in rets.columns:
        bench = rets[BENCH]
        rets = rets.drop(columns=[BENCH])
    else:
        bench = None
    syms = [s for s in syms if s in rets.columns]
    if len(syms) < 2:
        raise ValueError("Not enough price history for at least two of these symbols. " + "; ".join(skipped))
    rets = rets[syms]
    R = rets.values
    weeks = len(rets)

    if risk_free is None:
        rf, rf_src = _risk_free()
    else:
        rf, rf_src = float(risk_free), "entered"
    erp = config.EQUITY_RISK_PREMIUM

    # ---- expected returns
    hist_mu = R.mean(0) * 52
    betas = np.ones(len(syms))
    if bench is not None:
        b = bench.reindex(rets.index).fillna(0).values
        vb = b.var()
        if vb > 0:
            betas = np.array([np.cov(R[:, i], b)[0, 1] / vb for i in range(len(syms))])
    capm_mu = rf + betas * erp
    mu = {"historical": hist_mu, "capm": capm_mu, "blend": 0.5 * hist_mu + 0.5 * capm_mu}[method]

    # ---- risk
    if shrink and weeks > len(syms) + 2:
        cov_w, delta = _shrink_cov(R)
    else:
        cov_w, delta = np.cov(R, rowvar=False, bias=True), 0.0
    cov = cov_w * 52
    vols = np.sqrt(np.diag(cov))
    corr = cov / np.outer(vols, vols)

    n = len(syms)
    mw = float(max_weight or 1.0)
    if mw * n < 1:
        mw = 1.0 / n
    ub = np.full(n, min(1.0, mw))

    # ---- min variance, then trace the frontier by increasing risk appetite (lambda)
    w_min = _solve(mu, cov, 0.0, ub)
    r_min, v_min, _ = _stats(w_min, mu, cov, rf)
    w_top = _solve(mu, cov, 1e4, ub, iters=6000)        # ~ max return under the caps
    r_top, _, _ = _stats(w_top, mu, cov, rf)
    pts, w_prev = [], w_min
    for lam in np.concatenate([[0.0], np.geomspace(1e-3, 1e3, 160)]):
        w = _solve(mu, cov, float(lam), ub, w_prev)
        w_prev = w
        r, v, sh = _stats(w, mu, cov, rf)
        if pts and abs(r - pts[-1]["ret"]) < max(1e-4, (r_top - r_min) / (n_points * 1.5)) and lam < 1e3:
            continue
        pts.append({"vol": v, "ret": r, "sharpe": sh, "w": w})
    if abs(pts[-1]["ret"] - r_top) > 1e-4:
        r, v, sh = _stats(w_top, mu, cov, rf)
        pts.append({"vol": v, "ret": r, "sharpe": sh, "w": w_top})
    pts = [p for p in pts if p["ret"] >= r_min - 1e-9]
    pts.sort(key=lambda p: (p["vol"], -p["ret"]))
    eff = []                                            # keep only efficient points: more risk must buy more return
    for p in pts:
        if not eff or p["ret"] > eff[-1]["ret"] + 1e-6:
            eff.append(p)
    pts = eff
    # stop the curve once it has (almost) reached its top return: beyond that, extra risk buys nothing
    top = pts[-1]["ret"]
    cut = next((i for i, p in enumerate(pts) if p["ret"] >= top - max(5e-4, 0.005 * abs(top))), len(pts) - 1)
    pts = pts[:cut + 1]

    # ---- max Sharpe: best frontier point, then refine with golden-section search on lambda
    best = max(pts, key=lambda p: p["sharpe"] if p["sharpe"] is not None else -9)

    def sharpe_at(lam):
        w = _solve(mu, cov, lam, ub, best["w"])
        return _stats(w, mu, cov, rf)[2] or -9, w
    lo, hi = 1e-4, 1e3
    a, b = math.log(lo), math.log(hi)
    g = (math.sqrt(5) - 1) / 2
    c1, c2 = b - g * (b - a), a + g * (b - a)
    f1, f2 = sharpe_at(math.exp(c1))[0], sharpe_at(math.exp(c2))[0]
    for _ in range(40):
        if f1 > f2:
            b, c2, f2 = c2, c1, f1
            c1 = b - g * (b - a)
            f1 = sharpe_at(math.exp(c1))[0]
        else:
            a, c1, f1 = c1, c2, f2
            c2 = a + g * (b - a)
            f2 = sharpe_at(math.exp(c2))[0]
    s_ref, w_ref = sharpe_at(math.exp((a + b) / 2))
    w_tan = w_ref if s_ref >= (best["sharpe"] or -9) else best["w"]
    r_tan, v_tan, s_tan = _stats(w_tan, mu, cov, rf)

    def port(w, label):
        r, v, sh = _stats(w, mu, cov, rf)
        return {"label": label, "ret": r, "vol": v, "sharpe": sh,
                "weights": {syms[i]: round(float(w[i]), 4) for i in range(n) if w[i] >= 0.0005}}

    # ---- random portfolios under the same constraints (every one of them sits on or below the frontier)
    rng = np.random.default_rng(7)
    cloud = []
    for k in range(n_random):
        alpha = 0.35 if k % 2 else 1.0
        w = rng.dirichlet(np.full(n, alpha))
        if w.max() > ub[0] + 1e-9:
            w = _project(w, ub)
        r, v, _ = _stats(w, mu, cov, rf)
        cloud.append([round(v, 5), round(r, 5)])

    # ---- your mix
    mine = None
    if my_weights:
        mw_ = np.array([max(0.0, float(my_weights.get(s, 0) or 0)) for s in syms])
        if mw_.sum() > 0:
            mw_ = mw_ / mw_.sum()
            mine = port(mw_, "Your mix")
            same_risk = max((p for p in pts if p["vol"] <= mine["vol"] + 1e-9), key=lambda p: p["ret"], default=None)
            same_ret = min((p for p in pts if p["ret"] >= mine["ret"] - 1e-9), key=lambda p: p["vol"], default=None)
            mine["same_risk"] = port(same_risk["w"], "Same risk, more return") if same_risk else None
            mine["same_return"] = port(same_ret["w"], "Same return, less risk") if same_ret else None
            mine["efficiency_gap"] = (mine["same_risk"]["ret"] - mine["ret"]) if mine["same_risk"] else None

    # ---- in-sample backtest (growth of 10,000)
    def growth(w):
        vals = (1 + rets.values @ w).cumprod() * 10000
        return [round(float(x), 2) for x in vals]
    dates = [d.strftime("%Y-%m-%d") for d in rets.index]
    backtest = {"dates": dates, "series": {"Max Sharpe": growth(w_tan), "Min variance": growth(w_min),
                                           "Equal weight": growth(np.full(n, 1 / n))}}
    if mine:
        backtest["series"]["Your mix"] = growth(np.array([mine["weights"].get(s, 0) for s in syms]))
    if bench is not None:
        backtest["series"]["S&P 500"] = [round(float(x), 2) for x in (1 + bench.reindex(rets.index).fillna(0).values).cumprod() * 10000]

    assets = [{"symbol": s, "ret": float(mu[i]), "vol": float(vols[i]), "hist_return": float(hist_mu[i]),
               "capm_return": float(capm_mu[i]), "beta": float(betas[i]),
               "sharpe": (float(mu[i]) - rf) / float(vols[i]) if vols[i] > 0 else None} for i, s in enumerate(syms)]

    return {
        "symbols": syms, "skipped": skipped, "lookback": lookback, "weeks": weeks,
        "start": dates[0] if dates else None, "end": dates[-1] if dates else None,
        "method": method, "max_weight": float(ub[0]), "shrinkage": round(delta, 3),
        "risk_free": rf, "risk_free_source": rf_src, "erp": erp,
        "assets": assets, "corr": [[round(float(x), 3) for x in row] for row in corr],
        "frontier": [{"vol": p["vol"], "ret": p["ret"], "sharpe": p["sharpe"],
                      "weights": {syms[i]: round(float(p["w"][i]), 4) for i in range(n) if p["w"][i] >= 0.0005}} for p in pts],
        "min_variance": port(w_min, "Minimum variance"),
        "max_sharpe": port(w_tan, "Maximum Sharpe (tangency)"),
        "equal_weight": port(np.full(n, 1 / n), "Equal weight"),
        "cml": {"slope": s_tan, "points": [[0.0, rf], [v_tan * 1.6, rf + s_tan * v_tan * 1.6]] if s_tan else []},
        "cloud": cloud, "mine": mine, "backtest": backtest,
        "notes": _notes(method, delta, skipped, weeks, lookback),
    }


def analyze(symbols: list[str], lookback: str = "3y", method: str = "blend", max_weight: float = 0.40,
            shrink: bool = True, my_weights: dict[str, float] | None = None, n_points: int = 40,
            n_random: int = 800, risk_free: float | None = None) -> dict:
    """Cached wrapper: the same request within 10 minutes returns instantly."""
    key = json.dumps([sorted(s.strip().upper() for s in symbols), lookback, method, round(float(max_weight or 1), 4), shrink,
                      sorted((k.upper(), round(float(v), 6)) for k, v in (my_weights or {}).items()), n_random, risk_free])
    now = time.time()
    with _LOCK:
        hit = _RESULTS.get(key)
    if hit and now - hit[0] < TTL_RESULT:
        return hit[1]
    t0 = time.time()
    out = _analyze(symbols, lookback, method, max_weight, shrink, my_weights, n_points, n_random, risk_free)
    out["compute_ms"] = int((time.time() - t0) * 1000)
    with _LOCK:
        if len(_RESULTS) > 50:
            _RESULTS.clear()
        _RESULTS[key] = (now, out)
    return out


def _notes(method, delta, skipped, weeks, lookback):
    out = []
    if method == "historical":
        out.append("Historical average returns are very noisy - a few great years can dominate. Try the Blend or CAPM "
                   "method for steadier weights.")
    if delta:
        out.append(f"Covariance shrunk {delta:.0%} toward a constant-correlation target (Ledoit-Wolf) to reduce estimation error.")
    if skipped:
        out.append("Left out: " + "; ".join(skipped))
    out.append(f"Estimated from {weeks} weekly returns ({lookback}). Past behaviour is not a forecast; returns are in each "
               "security's own currency.")
    return out


def to_model_weights(weights: dict[str, float], min_weight: float = 0.005) -> dict[str, float]:
    """Round for saving as a model: drop dust, renormalise to exactly 100%."""
    h = {k: v for k, v in weights.items() if v >= min_weight}
    tot = sum(h.values())
    h = {k: round(v / tot, 4) for k, v in h.items()}
    diff = round(1 - sum(h.values()), 4)
    if h and diff:
        k = max(h, key=h.get)
        h[k] = round(h[k] + diff, 4)
    return h
