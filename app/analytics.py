"""Risk & return analytics computed from historical prices of the *current* weights
(a standard ex-ante look-through: 'how would today's portfolio have behaved?')."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import config
from .market_data import DataError, get_provider

TRADING_DAYS = 252


def price_frame(symbols: list[str], days: int = 3 * 365) -> pd.DataFrame:
    provider = get_provider()
    series = {}
    for s in symbols:
        try:
            series[s] = provider.history(s, days)
        except DataError:
            continue
    if not series:
        return pd.DataFrame()
    df = pd.DataFrame(series).sort_index().ffill().dropna(how="any")
    return df


def portfolio_metrics(weights: dict[str, float], cash_weight: float = 0.0, days: int = 3 * 365) -> dict:
    """weights: symbol -> weight of total portfolio (cash_weight earns the risk-free rate)."""
    weights = {k: v for k, v in weights.items() if v > 1e-6}
    if not weights:
        return {"available": False, "reason": "No invested positions"}
    bench = config.BENCHMARK
    prices = price_frame(sorted(set(weights) | {bench}), days)
    usable = [s for s in weights if s in prices.columns]
    if len(prices) < 60 or not usable:
        return {"available": False, "reason": "Not enough price history"}
    rets = prices.pct_change().dropna()
    w = np.array([weights[s] for s in usable])
    rf_daily = config.RISK_FREE_RATE / TRADING_DAYS
    port = rets[usable].values @ w + cash_weight * rf_daily
    port = pd.Series(port, index=rets.index)

    ann_ret = (1 + port).prod() ** (TRADING_DAYS / len(port)) - 1
    ann_vol = port.std() * math.sqrt(TRADING_DAYS)
    downside = port[port < 0].std() * math.sqrt(TRADING_DAYS)
    sharpe = (ann_ret - config.RISK_FREE_RATE) / ann_vol if ann_vol > 0 else None
    sortino = (ann_ret - config.RISK_FREE_RATE) / downside if downside > 0 else None
    curve = (1 + port).cumprod()
    max_dd = float((curve / curve.cummax() - 1).min())
    var95 = float(-np.percentile(port, 5))
    cvar95 = float(-port[port <= np.percentile(port, 5)].mean())
    beta = None
    if bench in rets.columns:
        b = rets[bench]
        beta = float(np.cov(port, b)[0, 1] / b.var())

    # risk contribution per holding (share of total variance)
    cov = rets[usable].cov().values * TRADING_DAYS
    port_var = float(w @ cov @ w)
    mrc = cov @ w
    contrib = {s: float(w[i] * mrc[i] / port_var) if port_var > 0 else 0 for i, s in enumerate(usable)}
    corr = rets[usable].corr().round(2)

    return {
        "available": True, "lookback_days": len(port),
        "annual_return": float(ann_ret), "annual_vol": float(ann_vol),
        "sharpe": None if sharpe is None else float(sharpe),
        "sortino": None if sortino is None else float(sortino),
        "max_drawdown": max_dd, "var95_1d": var95, "cvar95_1d": cvar95, "beta": beta,
        "risk_contribution": dict(sorted(contrib.items(), key=lambda kv: -kv[1])),
        "correlation": {"symbols": usable, "matrix": corr.values.tolist()},
        "growth_of_10k": [{"date": d.strftime("%Y-%m-%d"), "value": round(10000 * v, 2)}
                          for d, v in curve.iloc[::5].items()],
        "excluded": [s for s in weights if s not in usable],
    }


def stock_stats(symbol: str) -> dict:
    """Volatility, momentum and technicals for a single security."""
    h = get_provider().history(symbol, 2 * 365)
    if len(h) < 60:
        raise DataError("Not enough price history")
    r = h.pct_change().dropna()
    price = float(h.iloc[-1])
    sma50 = float(h.iloc[-50:].mean())
    sma200 = float(h.iloc[-200:].mean()) if len(h) >= 200 else None
    delta = h.diff().dropna()
    gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
    loss = -delta.clip(upper=0).rolling(14).mean().iloc[-1]
    rsi = float(100 - 100 / (1 + gain / loss)) if loss > 0 else 100.0

    def ret(n):
        return float(h.iloc[-1] / h.iloc[-n - 1] - 1) if len(h) > n else None

    mom_12_1 = float(h.iloc[-22] / h.iloc[-253] - 1) if len(h) > 253 else None
    curve = h / h.cummax() - 1
    return {
        "price": price, "vol_1y": float(r.iloc[-252:].std() * math.sqrt(TRADING_DAYS)),
        "ret_1m": ret(21), "ret_3m": ret(63), "ret_6m": ret(126), "ret_1y": ret(252),
        "mom_12_1": mom_12_1, "sma50": sma50, "sma200": sma200, "rsi14": rsi,
        "max_dd_2y": float(curve.min()),
        "chart": [{"date": d.strftime("%Y-%m-%d"), "close": round(float(v), 2)} for d, v in h.iloc[-504:].items()],
    }
