"""Model marketplace: a library of model portfolios you can assign to many clients at once.

* Built-in models: the five Core risk-profile models plus strategy models (60/40, All-Weather,
  Dividend Income, ESG, Multi-Factor, Tech Growth, Global Equity, Capital Preservation).
* Custom models: build your own (or duplicate a built-in and edit it).
* Assigning a model sets the client's target weights. Editing a model can push the new weights to
  every client on it; then rebalance them all in one go.
"""
from __future__ import annotations

import json

from . import db, planning, portfolio, rebalance, risk_profile

BUILTIN = [
    # name, category, description, holdings
    *[(f"Core {p}", "Core", f"Diversified ETF allocation for the {p} risk profile.", risk_profile.MODEL_PORTFOLIOS[p])
      for p in risk_profile.MODEL_PORTFOLIOS],
    ("Classic 60/40", "Balanced", "The traditional balanced portfolio: 60% global stocks, 40% bonds.",
     {"VTI": 0.42, "VXUS": 0.18, "BND": 0.40}),
    ("All-Weather", "Risk parity", "Ray Dalio-inspired mix built to hold up across growth and inflation regimes.",
     {"VTI": 0.30, "TLT": 0.40, "IEF": 0.15, "GLD": 0.075, "DBC": 0.075}),
    ("Dividend Income", "Income", "Dividend stocks, corporate bonds and REITs for steady cash flow.",
     {"SCHD": 0.30, "VYM": 0.20, "VIG": 0.10, "VCIT": 0.20, "BND": 0.10, "VNQ": 0.10}),
    ("ESG Core", "Sustainable", "Broad market exposure screened for environmental, social and governance criteria.",
     {"ESGU": 0.50, "ESGD": 0.20, "EAGG": 0.30}),
    ("Multi-Factor", "Factor", "Tilts toward value, quality, momentum and low-volatility factors.",
     {"VLUE": 0.15, "QUAL": 0.20, "MTUM": 0.15, "USMV": 0.15, "VTI": 0.15, "VXUS": 0.20}),
    ("Tech Growth", "Thematic", "Concentrated exposure to technology and semiconductors.",
     {"QQQ": 0.35, "VGT": 0.25, "SMH": 0.15, "VTI": 0.15, "BND": 0.10}),
    ("Global Equity 100", "Equity", "All-equity, globally diversified.",
     {"VTI": 0.55, "VXUS": 0.30, "VWO": 0.10, "VNQ": 0.05}),
    ("Capital Preservation", "Conservative", "T-bills, high-quality bonds and TIPS with a small equity sleeve.",
     {"SHV": 0.30, "BIL": 0.10, "BND": 0.35, "TIP": 0.15, "VTI": 0.10}),
]


def seed() -> None:
    if not db.query_one("SELECT COUNT(*) AS n FROM models WHERE builtin=1")["n"]:
        with db.tx() as c:
            for name, cat, desc, h in BUILTIN:
                c.execute("INSERT INTO models(name,category,description,holdings,builtin,created_at,updated_at) VALUES(?,?,?,?,1,?,?)",
                          (name, cat, desc, json.dumps(h), db.now_iso(), db.now_iso()))
    # clients created before the marketplace existed -> link to their Core model
    for c in db.query("SELECT id, risk_profile FROM clients WHERE model_id IS NULL"):
        mid = db.query_one("SELECT id FROM models WHERE name=? AND builtin=1", (f"Core {c['risk_profile']}",))
        if mid:
            with db.tx() as con:
                con.execute("UPDATE clients SET model_id=? WHERE id=?", (mid["id"], c["id"]))


def core_model_id(profile: str) -> int | None:
    seed()
    r = db.query_one("SELECT id FROM models WHERE name=? AND builtin=1", (f"Core {profile}",))
    return r["id"] if r else None


def _decorate(m: dict) -> dict:
    m["holdings"] = json.loads(m["holdings"]) if isinstance(m["holdings"], str) else m["holdings"]
    a = planning.portfolio_cma(m["holdings"])
    m.update(expected_return=a["expected_return"], volatility=a["volatility"],
             fits_profiles=planning.fitting_profiles(a["volatility"]),
             asset_mix=_mix(m["holdings"]),
             clients=[dict(id=r["id"], name=r["name"]) for r in db.query("SELECT id, name FROM clients WHERE model_id=?", (m["id"],))])
    return m


def _mix(h: dict) -> dict:
    out: dict[str, float] = {}
    for s, w in h.items():
        ac = risk_profile.asset_class(s)
        out[ac] = out.get(ac, 0) + w
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def list_models() -> list[dict]:
    seed()
    return [_decorate(m) for m in db.query("SELECT * FROM models ORDER BY builtin DESC, category, name")]


def get_model(model_id: int) -> dict:
    seed()
    m = db.query_one("SELECT * FROM models WHERE id=?", (model_id,))
    if not m:
        raise KeyError("Model not found")
    return _decorate(m)


def save_model(name: str, holdings: dict[str, float], category: str = "Custom", description: str = "",
               model_id: int | None = None) -> dict:
    from .market_data import get_provider
    h = {k.upper().strip(): float(v) for k, v in holdings.items() if float(v) > 0}
    total = sum(h.values())
    if not h or abs(total - 1) > 0.005:
        raise ValueError(f"Weights must add up to 100% (currently {total:.1%})")
    for s in h:
        get_provider().quote(s)                        # validates every ticker
    h = risk_profile.normalize(h)
    with db.tx() as c:
        if model_id:
            m = db.query_one("SELECT builtin FROM models WHERE id=?", (model_id,))
            if not m:
                raise KeyError("Model not found")
            if m["builtin"]:
                raise ValueError("Built-in models can't be edited - duplicate it first")
            c.execute("UPDATE models SET name=?, category=?, description=?, holdings=?, updated_at=? WHERE id=?",
                      (name, category, description, json.dumps(h), db.now_iso(), model_id))
        else:
            model_id = c.execute("INSERT INTO models(name,category,description,holdings,builtin,created_at,updated_at) VALUES(?,?,?,?,0,?,?)",
                                 (name, category or "Custom", description, json.dumps(h), db.now_iso(), db.now_iso())).lastrowid
    return get_model(model_id)


def duplicate(model_id: int) -> dict:
    m = get_model(model_id)
    return save_model(f"{m['name']} (copy)", m["holdings"], "Custom", m["description"] or "")


def delete_model(model_id: int) -> None:
    m = get_model(model_id)
    if m["builtin"]:
        raise ValueError("Built-in models can't be deleted")
    with db.tx() as c:
        c.execute("UPDATE clients SET model_id=NULL WHERE model_id=?", (model_id,))
        c.execute("DELETE FROM models WHERE id=?", (model_id,))


def assign(client_id: int, model_id: int) -> dict:
    client = portfolio.get_client(client_id)
    m = get_model(model_id)
    prof = risk_profile.profile_by_name(client["risk_profile"])
    portfolio.set_targets(client_id, m["holdings"])
    with db.tx() as c:
        c.execute("UPDATE clients SET model_id=? WHERE id=?", (model_id, client_id))
    warn = None
    lo, hi = prof["vol_band"]
    if not lo - 0.01 <= m["volatility"] <= hi + 0.01:
        warn = (f"'{m['name']}' has expected volatility {m['volatility']:.1%}, outside the {prof['name']} band "
                f"({lo:.0%}-{hi:.0%}). Document the reason or pick a model that fits.")
    return {"client_id": client_id, "model": m["name"], "targets": portfolio.get_targets(client_id), "suitability_warning": warn}


def push(model_id: int) -> dict:
    """Copy the model's current weights to every client assigned to it."""
    m = get_model(model_id)
    for c in m["clients"]:
        portfolio.set_targets(c["id"], m["holdings"])
    return {"model": m["name"], "updated_clients": [c["name"] for c in m["clients"]]}


def bulk_rebalance(model_id: int, execute: bool = False, mode: str = "full") -> dict:
    m = get_model(model_id)
    out = []
    for c in m["clients"]:
        if execute:
            r = rebalance.execute(c["id"], mode)
            out.append({"client_id": c["id"], "name": c["name"],
                        "filled": sum(1 for e in r["executed"] if e["status"] == "filled"),
                        "failed": sum(1 for e in r["executed"] if e["status"] != "filled")})
        else:
            p = rebalance.preview(c["id"], mode)
            out.append({"client_id": c["id"], "name": c["name"], "needs_rebalance": p["needs_rebalance"],
                        "trades": len(p["trades"]), "turnover": p["summary"]["turnover"],
                        "sell_value": p["summary"]["sell_value"], "buy_value": p["summary"]["buy_value"]})
    return {"model": m["name"], "executed": execute, "clients": out}
