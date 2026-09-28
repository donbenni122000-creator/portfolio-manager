"""Personalized (direct) indexing.

Instead of owning an S&P 500 ETF, the client owns the largest index stocks directly, weighted like the
index, with personal exclusions (sectors or tickers). Benefits: customisation + far more tax-loss
harvesting opportunities (each stock can be harvested individually and swapped for a same-sector peer).

The direct-index sleeve replaces part of the client's core US equity ETF allocation, so total equity
exposure is unchanged. Tracking error vs the S&P 500 (SPY) is estimated from one year of daily prices.
"""
from __future__ import annotations

import json
import math

import numpy as np

from . import analytics, db, portfolio, risk_profile
from .market_data import DataError, get_provider

US_CORE = ("VTI", "ITOT", "SCHB", "IVV", "SPY", "VOO", "SPLG", "ESGU", "SUSA")
SECTORS = ["Technology", "Communication Services", "Consumer Cyclical", "Consumer Defensive", "Energy",
           "Financial Services", "Healthcare", "Industrials", "Basic Materials", "Real Estate", "Utilities"]


def constituents() -> list[dict]:
    """[{symbol, name, sector, weight}] sorted by weight (weights sum to 1)."""
    rows = get_provider().index_constituents()
    tot = sum(r["weight"] for r in rows) or 1
    return sorted([{**r, "weight": r["weight"] / tot} for r in rows], key=lambda r: -r["weight"])


def build(top_n: int = 50, excluded_sectors=(), excluded_symbols=()) -> dict:
    cons = constituents()
    ex_sec = {s for s in excluded_sectors}
    ex_sym = {s.upper() for s in excluded_symbols}
    eligible = [c for c in cons if c["sector"] not in ex_sec and c["symbol"] not in ex_sym]
    chosen = eligible[: max(5, int(top_n))]
    tot = sum(c["weight"] for c in chosen)
    holdings = {c["symbol"]: c["weight"] / tot for c in chosen}
    sec_idx, sec_port = {}, {}
    for c in cons:
        sec_idx[c["sector"]] = sec_idx.get(c["sector"], 0) + c["weight"]
    for c in chosen:
        sec_port[c["sector"]] = sec_port.get(c["sector"], 0) + c["weight"] / tot
    sectors = [{"sector": s, "index": sec_idx.get(s, 0), "portfolio": sec_port.get(s, 0),
                "active": sec_port.get(s, 0) - sec_idx.get(s, 0)} for s in sorted(set(sec_idx) | set(sec_port), key=lambda k: -sec_idx.get(k, 0))]
    return {"holdings": holdings, "names": {c["symbol"]: c for c in chosen}, "count": len(chosen),
            "index_coverage": tot, "sectors": sectors, "universe": len(cons),
            "excluded_count": len(cons) - len(eligible)}


def tracking_error(holdings: dict[str, float], max_names: int = 60) -> dict:
    top = dict(sorted(holdings.items(), key=lambda kv: -kv[1])[:max_names])
    tot = sum(top.values())
    w = {k: v / tot for k, v in top.items()}
    prices = analytics.price_frame(sorted(set(w) | {"SPY"}), 400)
    if len(prices) < 60 or "SPY" not in prices:
        return {"available": False}
    rets = prices.pct_change().dropna().iloc[-252:]
    cols = [s for s in w if s in rets.columns]
    wv = np.array([w[s] for s in cols]) / sum(w[s] for s in cols)
    diff = rets[cols].values @ wv - rets["SPY"].values
    port = rets[cols].values @ wv
    return {"available": True, "tracking_error": float(diff.std() * math.sqrt(252)),
            "excess_return_1y": float((1 + port).prod() - (1 + rets["SPY"]).prod()),
            "correlation": float(np.corrcoef(port, rets["SPY"].values)[0, 1])}


def get_config(client_id: int) -> dict | None:
    r = db.query_one("SELECT * FROM direct_index WHERE client_id=?", (client_id,))
    if r:
        for k in ("excluded_sectors", "excluded_symbols", "holdings"):
            r[k] = json.loads(r[k])
    return r


def symbols(client_id: int) -> set[str]:
    cfg = get_config(client_id)
    return set(cfg["holdings"]) if cfg else set()


def preview(client_id: int, sleeve_weight: float, top_n: int = 50, excluded_sectors=(), excluded_symbols=()) -> dict:
    portfolio.get_client(client_id)
    b = build(top_n, excluded_sectors, excluded_symbols)
    te = tracking_error(b["holdings"])
    current = portfolio.get_targets(client_id)
    old = get_config(client_id)
    base = _strip(current, old)
    core = sum(w for s, w in base.items() if s in US_CORE)
    return {**b, "tracking": te, "sleeve_weight": sleeve_weight, "us_core_available": core,
            "holdings_list": [{"symbol": s, "name": b["names"][s]["name"], "sector": b["names"][s]["sector"],
                               "index_weight": b["names"][s]["weight"], "sleeve_weight": w, "portfolio_weight": w * sleeve_weight}
                              for s, w in b["holdings"].items()]}


def _strip(targets: dict, cfg: dict | None) -> dict:
    """Remove a previous direct-index sleeve from targets and give its weight back to core US equity."""
    t = dict(targets)
    if not cfg:
        return t
    freed = 0.0
    for s, w in cfg["holdings"].items():
        if s in t:
            take = min(t[s], w)
            t[s] -= take
            freed += take
            if t[s] <= 1e-6:
                t.pop(s)
    core = next((s for s in US_CORE if s in t), "VTI")
    t[core] = t.get(core, 0) + freed
    return t


def apply(client_id: int, sleeve_weight: float, top_n: int = 50, excluded_sectors=(), excluded_symbols=()) -> dict:
    client = portfolio.get_client(client_id)
    if not 0.05 <= sleeve_weight <= 0.95:
        raise ValueError("Direct-index sleeve must be between 5% and 95% of invested assets")
    cfg = get_config(client_id)
    base = _strip(portfolio.get_targets(client_id), cfg)
    core = {s: w for s, w in base.items() if s in US_CORE}
    pool = sum(core.values())
    if pool + 1e-9 < sleeve_weight:
        raise ValueError(f"The model only has {pool:.0%} in core US equity ETFs to replace - lower the sleeve to {pool:.0%} or less")
    for s, w in core.items():
        base[s] = w - sleeve_weight * w / pool
        if base[s] <= 1e-6:
            base.pop(s)
    b = build(top_n, excluded_sectors, excluded_symbols)
    applied = {s: w * sleeve_weight for s, w in b["holdings"].items()}
    for s, w in applied.items():
        base[s] = base.get(s, 0) + w
    portfolio.set_targets(client_id, risk_profile.normalize(base))
    now = db.now_iso()
    with db.tx() as c:
        c.execute("""INSERT INTO direct_index(client_id,sleeve_weight,top_n,excluded_sectors,excluded_symbols,holdings,created_at,updated_at)
                     VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(client_id) DO UPDATE SET sleeve_weight=excluded.sleeve_weight,
                     top_n=excluded.top_n, excluded_sectors=excluded.excluded_sectors, excluded_symbols=excluded.excluded_symbols,
                     holdings=excluded.holdings, updated_at=excluded.updated_at""",
                  (client_id, sleeve_weight, int(top_n), json.dumps(list(excluded_sectors)),
                   json.dumps([s.upper() for s in excluded_symbols]), json.dumps(applied), now, now))
    return {"client": client["name"], "names": len(applied), "sleeve_weight": sleeve_weight,
            "targets": portfolio.get_targets(client_id)}


def remove(client_id: int) -> dict:
    cfg = get_config(client_id)
    if not cfg:
        raise ValueError("No direct index on this account")
    portfolio.set_targets(client_id, risk_profile.normalize(_strip(portfolio.get_targets(client_id), cfg)))
    with db.tx() as c:
        c.execute("DELETE FROM direct_index WHERE client_id=?", (client_id,))
    return {"removed": True, "targets": portfolio.get_targets(client_id)}


def status(client_id: int) -> dict:
    cfg = get_config(client_id)
    if not cfg:
        return {"active": False, "sectors": SECTORS}
    val = portfolio.valuation(client_id)
    held = {h["symbol"]: h for h in val["holdings"] if h["symbol"] in cfg["holdings"]}
    value = sum(h["value"] for h in held.values())
    unreal = sum(h["unrealized"] for h in held.values())
    losers = sorted([h for h in held.values() if h["qty"] > 0 and h["unrealized"] < 0], key=lambda h: h["unrealized"])
    return {"active": True, "config": cfg, "value": value, "unrealized": unreal, "names": len(cfg["holdings"]),
            "held_names": sum(1 for h in held.values() if h["qty"] > 0), "losers": losers[:10], "sectors": SECTORS}


def peer_replacement(client_id: int, symbol: str) -> str | None:
    """Same-sector index stock not already held or excluded - a harvest replacement that keeps sector exposure."""
    cfg = get_config(client_id)
    if not cfg or symbol not in cfg["holdings"]:
        return None
    cons = constituents()
    sector = next((c["sector"] for c in cons if c["symbol"] == symbol), None)
    held = {p["symbol"] for p in portfolio.get_positions(client_id)}
    for c in cons:
        if (c["sector"] == sector and c["symbol"] != symbol and c["symbol"] not in held
                and c["symbol"] not in cfg["excluded_symbols"]):
            return c["symbol"]
    return None
