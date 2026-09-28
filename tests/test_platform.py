"""End-to-end tests on the simulated market (no API key needed).  Run:  python -m pytest -q"""
import os

os.environ["DATA_MODE"] = "sim"
os.environ["DB_PATH"] = ":memory:"
os.environ["MONITOR_INTERVAL"] = "0"

import pytest
from fastapi.testclient import TestClient

from app import config, db, market_data
from app.main import app

ANSWERS_GROWTH = {"age": 1, "horizon": 3, "income": 2, "liquidity": 2, "goal": 3, "drawdown": 2,
                  "loss_tolerance": 3, "experience": 2}
ANSWERS_CONSERVATIVE = {"age": 4, "horizon": 0, "income": 1, "liquidity": 0, "goal": 0, "drawdown": 0,
                        "loss_tolerance": 0, "experience": 0}


@pytest.fixture()
def api():
    config.DATA_MODE, config.DB_PATH = "sim", ":memory:"
    db.reset_connection()
    market_data.set_provider(market_data.SimProvider())
    with TestClient(app) as c:
        yield c


def new_client(api, answers=ANSWERS_GROWTH, cash=100_000):
    r = api.post("/api/clients", json={"name": "Test", "answers": answers, "starting_cash": cash})
    assert r.status_code == 200, r.text
    return r.json()


def test_risk_scoring_caps_short_horizon(api):
    r = api.post("/api/risk/score", json={**ANSWERS_GROWTH, "horizon": 0}).json()
    assert r["score"] <= 30 and r["profile"] == "Conservative"
    r2 = api.post("/api/risk/score", json=ANSWERS_GROWTH).json()
    assert r2["profile"] in ("Growth", "Moderate", "Aggressive")
    assert abs(sum(r2["model"].values()) - 1) < 1e-9


def test_rebalance_builds_model_portfolio(api):
    c = new_client(api)
    pre = api.get(f"/api/clients/{c['id']}/rebalance").json()
    assert pre["needs_rebalance"] and pre["summary"]["n_trades"] > 0
    ex = api.post(f"/api/clients/{c['id']}/rebalance", json={"mode": "full"}).json()
    assert all(e["status"] == "filled" for e in ex["executed"]), ex
    dash = api.get(f"/api/clients/{c['id']}/dashboard").json()
    v = dash["valuation"]
    assert v["invested"] > 0.95 * v["equity"]
    assert 0 < v["cash_weight"] < 0.05
    assert not dash["drift"]["needs_rebalance"]
    assert dash["metrics"]["available"]


def test_trading_cash_and_pnl(api):
    c = new_client(api, cash=10_000)
    cid = c["id"]
    o = api.post(f"/api/clients/{cid}/orders", json={"symbol": "AAPL", "side": "buy", "qty": 10}).json()
    assert o["status"] == "filled"
    client = api.get(f"/api/clients/{cid}").json()
    assert client["cash"] == pytest.approx(10_000 - 10 * o["fill_price"], rel=1e-9)
    # no short selling
    r = api.post(f"/api/clients/{cid}/orders", json={"symbol": "AAPL", "side": "sell", "qty": 11})
    assert r.status_code == 400
    # insufficient buying power
    r = api.post(f"/api/clients/{cid}/orders", json={"symbol": "MSFT", "side": "buy", "qty": 1000})
    assert r.status_code == 400 and "buying power" in r.json()["detail"]
    s = api.post(f"/api/clients/{cid}/orders", json={"symbol": "AAPL", "side": "sell", "qty": 10}).json()
    assert s["status"] == "filled"
    assert s["realized_pnl"] == pytest.approx((s["fill_price"] - o["fill_price"]) * 10, rel=1e-6)


def test_limit_order_rests_and_cancels(api):
    c = new_client(api)
    q = api.get("/api/quote/MSFT").json()
    o = api.post(f"/api/clients/{c['id']}/orders",
                 json={"symbol": "MSFT", "side": "buy", "qty": 5, "order_type": "limit", "limit_price": round(q["price"] * 0.5, 2)}).json()
    assert o["status"] == "open"
    x = api.delete(f"/api/clients/{c['id']}/orders/{o['id']}").json()
    assert x["status"] == "cancelled"
    # marketable limit fills immediately
    o2 = api.post(f"/api/clients/{c['id']}/orders",
                  json={"symbol": "MSFT", "side": "buy", "qty": 1, "order_type": "limit", "limit_price": round(q["price"] * 1.5, 2)}).json()
    assert o2["status"] == "filled" and o2["fill_price"] <= q["price"] * 1.5


def test_research_outputs_rating(api):
    c = new_client(api)
    r = api.get(f"/api/research/MSFT?client_id={c['id']}").json()
    assert r["rating"] in ("BUY", "HOLD", "SELL")
    assert r["dcf"] and r["dcf"]["per_share"] > 0
    assert len(r["sensitivity"]["values"]) == 5
    assert r["piotroski"]["score"] is not None
    assert "action" in r["client_view"]
    # consensus estimates drive the first DCF years; Street target is reported
    assert r["dcf"]["projection"][0]["source"] == "consensus"
    assert r["dcf"]["projection"][-1]["source"] == "model"
    assert abs(r["dcf"]["projection"][-1]["growth"] - r["assumptions"]["terminal_growth"]) < 1e-9
    assert r["street"]["price_target"]["consensus"] > 0 and r["street"]["forward_pe"] > 0
    etf = api.get("/api/research/VTI").json()
    assert etf["is_etf"]


def test_conservative_client_cannot_add_stock(api):
    c = new_client(api, ANSWERS_CONSERVATIVE)
    assert c["risk_profile"] == "Conservative"
    r = api.post(f"/api/clients/{c['id']}/targets/add-stock", json={"symbol": "AAPL", "weight": 0.02})
    assert r.status_code == 400


def test_add_stock_keeps_weights_normalized_and_drift_detected(api):
    c = new_client(api)
    cid = c["id"]
    api.post(f"/api/clients/{cid}/rebalance", json={"mode": "full"})
    t = api.post(f"/api/clients/{cid}/targets/add-stock", json={"symbol": "MSFT", "weight": 0.05}).json()
    assert abs(sum(t.values()) - 1) < 1e-6 and t["MSFT"] == pytest.approx(0.05, abs=1e-6)
    pre = api.get(f"/api/clients/{cid}/rebalance?mode=breached").json()
    assert pre["needs_rebalance"]
    assert any(tr["symbol"] == "MSFT" and tr["side"] == "buy" for tr in pre["trades"])
    api.post(f"/api/clients/{cid}/rebalance", json={"mode": "full"})
    dash = api.get(f"/api/clients/{cid}/dashboard").json()
    msft = next(h for h in dash["valuation"]["holdings"] if h["symbol"] == "MSFT")
    assert msft["weight"] == pytest.approx(0.05 * (1 - 0.02), abs=0.005)


def test_fmp_provider_normalizes_stable_payloads(api, monkeypatch):
    """FMP 'stable' field names -> internal shape (no network)."""
    canned = {
        "quote": [{"symbol": "AAPL", "name": "Apple Inc.", "price": 340.83, "changePercentage": 1.46,
                   "previousClose": 335.92, "marketCap": 5.0e12, "yearHigh": 345.3, "yearLow": 243.4}],
        "historical-price-eod/dividend-adjusted": [{"symbol": "AAPL", "date": "2026-09-24", "adjClose": 335.9},
                                                    {"symbol": "AAPL", "date": "2026-09-23", "adjClose": 330.1}],
        "profile": [{"symbol": "AAPL", "companyName": "Apple Inc.", "sector": "Technology", "beta": 1.1, "isEtf": False}],
        "stock-peers": [{"symbol": "MSFT"}, {"symbol": "GOOGL"}],
    }
    p = market_data.FMPProvider("dummy")
    monkeypatch.setattr(p, "_get", lambda path, ttl, **kw: canned[path])
    q = p.quote("aapl")
    assert q["price"] == 340.83 and q["change_pct"] == 1.46 and q["prev_close"] == 335.92
    h = p.history("AAPL")
    assert list(h.values) == [330.1, 335.9]
    assert p.profile("AAPL")["sector"] == "Technology"
    assert p.peers("AAPL") == ["MSFT", "GOOGL"]


def test_mcp_tools_local_and_remote(api):
    """The AI-assistant (MCP) tools work, and the remote endpoint is reachable only via its secret path."""
    import asyncio
    from app.main import MCP_PREFIX
    from app.mcp_server import build_server

    srv = build_server()
    names = {t.name for t in asyncio.run(srv.list_tools())}
    assert {"list_clients", "get_portfolio", "research_stock", "preview_rebalance", "execute_rebalance",
            "place_paper_order", "create_client"} <= names

    def call(tool, **args):
        res = asyncio.run(srv.call_tool(tool, args))
        import json
        blocks = res[0] if isinstance(res, tuple) else res
        return json.loads(blocks[0].text)

    c = call("create_client", name="MCP Test", answers=ANSWERS_GROWTH, starting_cash=50000)
    cid = c["client_id"]
    assert call("preview_rebalance", client_id=cid)["needs_rebalance"]
    call("execute_rebalance", client_id=cid)
    p = call("get_portfolio", client_id=cid)
    assert p["summary"]["cash_weight"] < 0.05 and len(p["holdings"]) >= 5
    o = call("place_paper_order", client_id=cid, symbol="VTI", side="sell", quantity=1)
    assert o["status"] == "filled" and o["source"] == "assistant"
    r = call("research_stock", symbol="MSFT", client_id=cid)
    assert r["rating"] in ("BUY", "HOLD", "SELL") and "client_action" in r

    # remote endpoint: secret path answers MCP; everything else is blocked for proxied requests
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
    hdr = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
           "cf-connecting-ip": "1.2.3.4", "host": "example.trycloudflare.com"}
    r = api.post(MCP_PREFIX + "/mcp", json=init, headers=hdr)
    assert r.status_code == 200 and "Portfolio Manager" in r.text
    assert api.get("/api/clients", headers={"cf-connecting-ip": "1.2.3.4"}).status_code == 403
    assert api.post("/api/shutdown", headers={"x-forwarded-for": "1.2.3.4"}).status_code == 403
    assert api.post("/connect/wrong-token/mcp", json=init, headers=hdr).status_code in (403, 404)


class _Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.text = data, status, json_dumps(data)

    def json(self):
        return self._d


def json_dumps(d):
    import json
    return json.dumps(d)


def _fake_claude(calls_log):
    """Scripted Anthropic Messages API: list_clients -> place order (needs approval) -> final text."""
    def post(url, headers=None, json=None, timeout=None):
        assert url.endswith("/v1/messages") and headers["x-api-key"] == "sk-ant-test"
        assert {t["name"] for t in json["tools"]} >= {"get_portfolio", "place_paper_order"}
        calls_log.append(json)
        n_tool_results = sum(1 for m in json["messages"] if m["role"] == "user" and isinstance(m["content"], list))
        if n_tool_results == 0:
            return _Resp({"content": [{"type": "text", "text": "Checking clients."},
                                      {"type": "tool_use", "id": "t1", "name": "list_clients", "input": {}}]})
        if n_tool_results == 1:
            return _Resp({"content": [{"type": "text", "text": "Placing the order."},
                                      {"type": "tool_use", "id": "t2", "name": "place_paper_order",
                                       "input": {"client_id": 1, "symbol": "VTI", "side": "buy", "quantity": 1}}]})
        last = json["messages"][-1]["content"][0]
        return _Resp({"content": [{"type": "text", "text": "Result: " + last["content"][:40]}]})
    return post


def _fake_openai(calls_log):
    """Scripted OpenAI Responses API with a reasoning item + function calls."""
    def post(url, headers=None, json=None, timeout=None):
        assert url.endswith("/v1/responses") and headers["Authorization"] == "Bearer sk-oa-test"
        calls_log.append(json)
        outs = [m for m in json["input"] if m.get("type") == "function_call_output"]
        if not outs:
            return _Resp({"output": [{"type": "reasoning", "id": "rs1", "summary": []},
                                     {"type": "function_call", "call_id": "c1", "name": "get_portfolio",
                                      "arguments": '{"client_id": 1}'}]})
        if len(outs) == 1:
            return _Resp({"output": [{"type": "function_call", "call_id": "c2", "name": "execute_rebalance",
                                      "arguments": '{"client_id": 1, "mode": "full"}'}]})
        return _Resp({"output": [{"type": "message", "content": [{"type": "output_text", "text": "Done: " + outs[-1]["output"][:30]}]}]})
    return post


def test_assistant_claude_and_openai(api, monkeypatch, tmp_path):
    from app import assistant
    monkeypatch.setattr(assistant, "SETTINGS_FILE", tmp_path / "assistant.json")
    new_client(api)

    # no key yet -> friendly error
    r = api.post("/api/assistant/chat", json={"message": "hi"})
    assert r.status_code == 400 and "API key" in r.json()["detail"]

    # ---- Claude: read tool runs, write tool waits for approval, then executes
    s = api.put("/api/assistant/settings", json={"provider": "claude", "claude_key": "sk-ant-test"}).json()
    assert s["has_claude_key"] and s["claude_key_hint"].endswith("test") and "claude_key" not in s
    log = []
    monkeypatch.setattr(assistant.requests, "post", _fake_claude(log))
    r = api.post("/api/assistant/chat", json={"message": "Buy 1 VTI for client 1", "client_id": 1}).json()
    assert r["awaiting_approval"]
    kinds = [e["type"] for e in r["events"]]
    assert kinds[:3] == ["text", "tool", "text"] and kinds[-1] == "approval"
    assert "BUY 1 shares of VTI" in r["events"][-1]["description"]
    assert "Test" in log[0]["system"]                                     # client context in prompt
    before = api.get("/api/clients/1/orders").json()
    assert not before                                                      # nothing traded before approval
    d = api.post("/api/assistant/decision", json={"conversation_id": r["conversation_id"], "approve": True}).json()
    assert not d["awaiting_approval"] and d["events"][0]["status"] == "approved"
    assert d["events"][-1]["text"].startswith("Result:")
    orders = api.get("/api/clients/1/orders").json()
    assert len(orders) == 1 and orders[0]["source"] == "assistant" and orders[0]["status"] == "filled"

    # ---- OpenAI: decline path + reasoning items passed back
    api.put("/api/assistant/settings", json={"provider": "openai", "openai_key": "sk-oa-test"})
    log2 = []
    monkeypatch.setattr(assistant.requests, "post", _fake_openai(log2))
    r = api.post("/api/assistant/chat", json={"message": "Rebalance client 1"}).json()
    assert r["provider"] == "openai" and r["awaiting_approval"] and r["events"][0]["tool"] == "get_portfolio"
    d = api.post("/api/assistant/decision", json={"conversation_id": r["conversation_id"], "approve": False}).json()
    assert d["events"][0]["status"] == "declined" and d["events"][-1]["text"].startswith("Done: The user declined")
    assert any(i.get("type") == "reasoning" for i in log2[1]["input"])      # reasoning item echoed back
    assert len(api.get("/api/clients/1/orders").json()) == 1               # declined -> no new trades

    # keys never leak to the browser
    assert "sk-" not in api.get("/api/assistant/settings").text


# ============================================================ wave 1: billing, cash, tax, models, planning
def test_fee_math_and_quarter_bounds():
    from datetime import date
    from app import billing
    tiers = [[1_000_000, 0.01], [5_000_000, 0.0075], [None, 0.005]]
    assert billing.annual_fee(500_000, tiers) == pytest.approx(5_000)
    assert billing.annual_fee(2_000_000, tiers) == pytest.approx(10_000 + 7_500)
    assert billing.annual_fee(6_000_000, tiers) == pytest.approx(10_000 + 30_000 + 5_000)
    assert billing.quarter_bounds(date(2026, 9, 25)) == (date(2026, 7, 1), date(2026, 9, 30))
    assert billing.quarter_bounds(date(2026, 11, 2)) == (date(2026, 10, 1), date(2026, 12, 31))


def test_billing_run_invoice_and_no_double_billing(api):
    c = new_client(api)
    api.post(f"/api/clients/{c['id']}/rebalance", json={"mode": "full"})
    from datetime import date, timedelta
    s, e = (date.today() - timedelta(days=30)).isoformat(), date.today().isoformat()
    cash0 = api.get(f"/api/clients/{c['id']}").json()["cash"]
    pv = api.get(f"/api/billing/preview?period_start={s}&period_end={e}").json()
    row = pv["rows"][0]
    assert row["schedule"] == "Standard 1.00%" and row["days"] == 1        # client opened today -> pro-rated
    assert row["fee"] == pytest.approx(row["avg_aum"] * 0.01 / 365, abs=0.02)
    r = api.post("/api/billing/run", json={"period_start": s, "period_end": e}).json()
    assert r["results"][0]["status"] == "paid"
    assert api.get(f"/api/clients/{c['id']}").json()["cash"] == pytest.approx(cash0 - row["fee"], abs=0.01)
    again = api.post("/api/billing/run", json={"period_start": s, "period_end": e}).json()
    assert again["results"][0]["status"] == "skipped"                   # no double billing
    b = api.get(f"/api/clients/{c['id']}/billing").json()
    assert b["invoices"][0]["status"] == "paid" and any(a["type"] == "fee" for a in b["activity"])
    summ = api.get("/api/billing/summary").json()
    assert summ["projected_annual_revenue"] == pytest.approx(summ["total_aum"] * 0.01, rel=1e-6)


def test_unpaid_invoice_then_pay(api):
    c = new_client(api, cash=1000)
    api.post(f"/api/clients/{c['id']}/orders", json={"symbol": "VTI", "side": "buy", "notional": 999})
    # custom expensive schedule so the fee exceeds remaining cash
    sch = api.post("/api/billing/schedules", json={"name": "Test 5%", "tiers": [[None, 0.05]]}).json()
    api.patch(f"/api/clients/{c['id']}", json={"fee_schedule_id": sch["id"]})
    from datetime import date
    y = date.today().year
    from app import db
    with db.tx() as con:   # pretend the account opened a year ago
        con.execute("UPDATE clients SET created_at=? WHERE id=?", (f"{y-1}-01-01T00:00:00+00:00", c["id"]))
    r = api.post("/api/billing/run", json={"period_start": f"{y-1}-01-01", "period_end": f"{y-1}-12-31"}).json()
    assert r["results"][0]["status"] == "unpaid"
    dash = api.get(f"/api/clients/{c['id']}/dashboard").json()
    assert any(a["title"] == "Unpaid advisory fee" for a in dash["alerts"])
    inv = api.get(f"/api/billing/invoices?client_id={c['id']}").json()[0]
    assert api.post(f"/api/billing/invoices/{inv['id']}/pay").status_code == 400      # still no cash
    api.post(f"/api/clients/{c['id']}/cash", json={"amount": 200})
    assert api.post(f"/api/billing/invoices/{inv['id']}/pay").json()["status"] == "paid"


def test_cash_interest_accrues(api):
    c = new_client(api, cash=100_000)
    from datetime import date, timedelta
    from app import billing, config, db
    with db.tx() as con:
        con.execute("UPDATE clients SET last_interest_date=? WHERE id=?", ((date.today() - timedelta(days=30)).isoformat(), c["id"]))
    earned = billing.accrue_interest(c["id"])
    assert earned == pytest.approx(100_000 * ((1 + config.CASH_INTEREST_RATE) ** (30 / 365) - 1), abs=0.01)
    assert billing.accrue_interest(c["id"]) == 0                      # idempotent same day
    assert api.get(f"/api/clients/{c['id']}").json()["cash"] == pytest.approx(100_000 + earned, abs=0.01)
    # interest is performance, not a deposit
    v = api.get(f"/api/clients/{c['id']}/dashboard").json()["valuation"]
    assert v["net_deposits"] == pytest.approx(100_000) and v["total_pnl"] > 0


def test_tax_lots_methods_realized_and_harvest(api):
    from app import db
    c = new_client(api, cash=100_000)
    cid = c["id"]
    q = api.get("/api/quote/AAPL").json()["price"]
    api.post(f"/api/clients/{cid}/orders", json={"symbol": "AAPL", "side": "buy", "qty": 10})
    api.post(f"/api/clients/{cid}/orders", json={"symbol": "AAPL", "side": "buy", "qty": 10})
    lots = db.query("SELECT * FROM lots WHERE client_id=? ORDER BY id", (cid,))
    assert len(lots) == 2
    with db.tx() as con:   # make lot 1 old & cheap (LT gain), lot 2 recent & expensive (ST loss)
        con.execute("UPDATE lots SET cost_per_share=?, acquired_at='2023-01-03T00:00:00+00:00' WHERE id=?", (q * 0.5, lots[0]["id"]))
        con.execute("UPDATE lots SET cost_per_share=?, acquired_at='2026-06-01T00:00:00+00:00' WHERE id=?", (q * 1.5, lots[1]["id"]))
    # harvest candidate: lot 2 has a big loss
    rep = api.get(f"/api/clients/{cid}/tax").json()
    h = [x for x in rep["harvest"] if x["symbol"] == "AAPL"]
    assert h and h[0]["replacement"] == "XLK" and h[0]["qty"] == pytest.approx(10)
    # HIFO sale relieves the expensive lot first -> a loss
    o = api.post(f"/api/clients/{cid}/orders", json={"symbol": "AAPL", "side": "sell", "qty": 5}).json()
    api.patch(f"/api/clients/{cid}", json={"lot_method": "HIFO"})
    o = api.post(f"/api/clients/{cid}/orders", json={"symbol": "AAPL", "side": "sell", "qty": 5}).json()
    rep = api.get(f"/api/clients/{cid}/tax").json()
    first, second = sorted(rep["realized"], key=lambda r: r["id"])
    assert first["term"] == "LT" and first["gain"] > 0                  # FIFO took the 2023 lot
    assert second["term"] == "ST" and second["gain"] < 0 and o["realized_pnl"] < 0
    assert rep["long_term"] > 0 and rep["short_term"] < 0
    assert rep["estimated_tax"] >= 0
    # harvest the rest of the loss lot -> sells AAPL, buys replacement
    res = api.post(f"/api/clients/{cid}/tax/harvest", json={"symbol": "AAPL"}).json()
    assert res["sold"]["status"] == "filled" and res["bought"]["symbol"] == "XLK" and res["realized_loss"] < 0
    left = db.query("SELECT SUM(qty_open) AS q FROM lots WHERE client_id=? AND symbol='AAPL'", (cid,))[0]["q"]
    assert left == pytest.approx(5)                                     # the gain lot is kept


def test_harvest_swaps_model_target(api):
    from app import db
    c = new_client(api, cash=100_000)
    cid = c["id"]
    api.post(f"/api/clients/{cid}/rebalance", json={"mode": "full"})
    with db.tx() as con:
        con.execute("UPDATE lots SET cost_per_share = cost_per_share * 1.3 WHERE client_id=? AND symbol='VTI'", (cid,))
    res = api.post(f"/api/clients/{cid}/tax/harvest", json={"symbol": "VTI"}).json()
    assert res["targets_swapped"] and res["bought"]["symbol"] == "SCHB"
    t = {x["symbol"]: x["weight"] for x in api.get(f"/api/clients/{cid}/targets").json()}
    assert "VTI" not in t and t["SCHB"] > 0.4
    assert not api.get(f"/api/clients/{cid}/rebalance").json()["needs_rebalance"]


def test_model_marketplace(api):
    c1, c2 = new_client(api), new_client(api)
    ms = api.get("/api/models").json()
    assert len([m for m in ms if m["builtin"]]) == 13
    core_growth = next(m for m in ms if m["name"] == "Core Growth")
    assert "Growth" in core_growth["fits_profiles"]
    assert api.get(f"/api/clients/{c1['id']}").json()["model_id"] == next(m["id"] for m in ms if m["name"] == f"Core {c1['risk_profile']}")
    assert api.put(f"/api/models/{core_growth['id']}", json={"name": "x", "holdings": {"VTI": 1}}).status_code == 400
    m = api.post("/api/models", json={"name": "My 70/30", "holdings": {"VTI": 0.5, "VXUS": 0.2, "BND": 0.3}}).json()
    assert m["expected_return"] > 0.05 and m["asset_mix"]["Core Bonds"] == pytest.approx(0.3)
    bad = api.post("/api/models", json={"name": "bad", "holdings": {"VTI": 0.5}})
    assert bad.status_code == 400
    for c in (c1, c2):
        a = api.post(f"/api/clients/{c['id']}/model/{m['id']}").json()
        assert a["targets"]["BND"] == pytest.approx(0.3)
    aw = next(x for x in ms if x["name"] == "Capital Preservation")
    warn = api.post(f"/api/clients/{c1['id']}/model/{aw['id']}").json()["suitability_warning"]
    assert warn and "outside" in warn                                 # too conservative for a growth client
    api.post(f"/api/clients/{c1['id']}/model/{m['id']}")
    api.put(f"/api/models/{m['id']}", json={"name": "My 70/30", "holdings": {"VTI": 0.6, "VXUS": 0.1, "BND": 0.3}})
    pushed = api.post(f"/api/models/{m['id']}/push").json()
    assert len(pushed["updated_clients"]) == 2
    pv = api.get(f"/api/models/{m['id']}/rebalance").json()
    assert all(r["needs_rebalance"] for r in pv["clients"])
    ex = api.post(f"/api/models/{m['id']}/rebalance", json={"mode": "full"}).json()
    assert all(r["filled"] > 0 and r["failed"] == 0 for r in ex["clients"])
    detail = api.get(f"/api/models/{m['id']}").json()
    assert detail["history"]["available"]


def test_financial_planning(api):
    c = new_client(api, cash=200_000)
    cid = c["id"]
    from datetime import date
    y = date.today().year
    g = api.post(f"/api/clients/{cid}/goals", json={"name": "Retirement", "target_amount": 1_500_000,
                                                   "target_date": f"{y+25}-06-30", "monthly_contribution": 500}).json()
    assert api.post(f"/api/clients/{cid}/goals", json={"name": "x", "target_amount": 1, "target_date": "2000-01-01"}).status_code == 400
    p = api.get(f"/api/clients/{cid}/plan").json()
    r = p["goals"][0]
    assert 0 <= r["probability"] <= 1 and len(r["what_if"]) == 5 and r["path"][-1]["p90"] >= r["path"][-1]["p10"]
    assert r["target_nominal"] > 1_500_000                              # inflation-adjusted
    need = r["required_monthly_for_80pct"]
    api.put(f"/api/clients/{cid}/goals/{g['id']}", json={**{k: g[k] for k in ("name", "target_amount", "target_date", "allocation_pct")},
                                                          "monthly_contribution": need, "inflation_adjust": True})
    r2 = api.get(f"/api/clients/{cid}/plan").json()["goals"][0]
    assert r2["probability"] >= 0.79 and r2["probability"] > r["probability"] - 1e-9
    probs = [w["probability"] for w in r["what_if"]]
    assert probs[-1] >= probs[0]                                        # more growth -> higher odds on a long horizon


def test_migration_adds_lots_for_old_positions(tmp_path):
    import sqlite3
    from app import config, db
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""CREATE TABLE clients (id INTEGER PRIMARY KEY, name TEXT, email TEXT, risk_score REAL, risk_profile TEXT,
        questionnaire TEXT, starting_cash REAL, cash REAL, drift_abs_band REAL DEFAULT 0.05, drift_rel_band REAL DEFAULT 0.25,
        cash_target REAL DEFAULT 0.02, stop_loss_pct REAL DEFAULT 0.2, max_position REAL DEFAULT 0.1, notes TEXT, created_at TEXT);
        CREATE TABLE positions (client_id INTEGER, symbol TEXT, qty REAL, avg_cost REAL, realized_pnl REAL DEFAULT 0, opened_at TEXT,
        PRIMARY KEY (client_id, symbol));
        CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER, symbol TEXT, side TEXT, qty REAL,
        order_type TEXT, limit_price REAL, stop_price REAL, status TEXT, fill_price REAL, commission REAL, realized_pnl REAL,
        source TEXT, reason TEXT, created_at TEXT, filled_at TEXT);
        INSERT INTO clients(id,name,risk_score,risk_profile,starting_cash,cash,created_at) VALUES(1,'Old',50,'Moderate',1000,100,'2026-01-01');
        INSERT INTO positions VALUES(1,'VTI',3,250,0,'2026-02-01T00:00:00+00:00');""")
    con.commit(); con.close()
    old = config.DB_PATH
    try:
        config.DB_PATH = path
        db.reset_connection()
        lots = db.query("SELECT * FROM lots")
        assert len(lots) == 1 and lots[0]["qty_open"] == 3 and lots[0]["cost_per_share"] == 250
        cols = {r["name"] for r in db.query("PRAGMA table_info(clients)")}
        assert {"fee_schedule_id", "lot_method", "model_id", "last_interest_date"} <= cols
    finally:
        db.reset_connection()
        config.DB_PATH = old


def test_wash_sale_logic(api):
    from app import db
    c = new_client(api, cash=100_000)
    cid = c["id"]
    api.post(f"/api/clients/{cid}/orders", json={"symbol": "MSFT", "side": "buy", "qty": 10})
    with db.tx() as con:
        con.execute("UPDATE lots SET cost_per_share = cost_per_share * 1.3 WHERE client_id=?", (cid,))
    cand = api.get(f"/api/clients/{cid}/tax").json()["harvest"][0]
    assert not cand["wash_sale_risk"]                       # only lot is the one being sold -> no wash sale
    api.post(f"/api/clients/{cid}/orders", json={"symbol": "MSFT", "side": "buy", "qty": 1})   # new replacement shares
    cand = api.get(f"/api/clients/{cid}/tax").json()["harvest"][0]
    assert cand["wash_sale_risk"]
    api.post(f"/api/clients/{cid}/orders", json={"symbol": "MSFT", "side": "sell", "qty": 10})  # FIFO: sells the loss lot
    rep = api.get(f"/api/clients/{cid}/tax").json()
    loss = [r for r in rep["realized"] if r["gain"] < 0][0]
    assert loss["wash_sale"] and rep["wash_sale_disallowed"] > 0


def test_billing_never_overlaps(api):
    from datetime import date, timedelta
    from app import db
    c = new_client(api)
    with db.tx() as con:
        con.execute("UPDATE clients SET created_at=? WHERE id=?", ((date.today() - timedelta(days=60)).isoformat() + "T00:00:00+00:00", c["id"]))
    mid = (date.today() - timedelta(days=20)).isoformat()
    r1 = api.post("/api/billing/run", json={"period_start": (date.today() - timedelta(days=60)).isoformat(), "period_end": mid}).json()
    assert r1["results"][0]["days"] == 41
    pv = api.get(f"/api/billing/preview?period_start={(date.today() - timedelta(days=60)).isoformat()}&period_end={date.today().isoformat()}").json()
    row = pv["rows"][0]
    assert row["days"] == 20 and row["period_start"] == (date.today() - timedelta(days=19)).isoformat()


# ============================================================ wave 2: onboarding, reports, direct index, lending, options
def test_onboarding_account_types_and_reports(api):
    r = api.post("/api/clients", json={"name": "Ira Saver", "answers": ANSWERS_GROWTH, "starting_cash": 50_000,
                                       "account_type": "Roth IRA", "phone": "555-0100", "objective": "Retire early",
                                       "beneficiaries": [{"name": "Spouse", "relationship": "Spouse", "share": 60, "type": "primary"},
                                                         {"name": "Child", "relationship": "Child", "share": 40, "type": "primary"}]})
    c = r.json()
    assert c["account_type"] == "Roth IRA" and len(c["beneficiaries"]) == 2
    bad = api.post("/api/clients", json={"name": "x", "answers": ANSWERS_GROWTH, "beneficiaries": [{"name": "A", "share": 50}]})
    assert bad.status_code == 400                                    # primary shares must total 100%
    assert api.post("/api/clients", json={"name": "x", "answers": ANSWERS_GROWTH, "account_type": "Pension"}).status_code == 400
    # retirement account rules
    assert api.post(f"/api/clients/{c['id']}/margin", json={"enabled": True}).status_code == 400
    assert api.post(f"/api/clients/{c['id']}/sbloc/draw", json={"amount": 100}).status_code == 400
    api.post(f"/api/clients/{c['id']}/rebalance", json={"mode": "full"})
    t = api.get(f"/api/clients/{c['id']}/tax").json()
    assert t["taxable"] is False and t["estimated_tax"] == 0 and t["harvest"] == []
    # model chosen at onboarding
    ms = api.get("/api/models").json()
    aw = next(m for m in ms if m["name"] == "All-Weather")
    c2 = api.post("/api/clients", json={"name": "AW", "answers": ANSWERS_GROWTH, "model_id": aw["id"]}).json()
    assert c2["model_id"] == aw["id"]
    # printable documents
    ips = api.get(f"/report/ips/{c['id']}")
    assert ips.status_code == 200 and "Investment Policy Statement" in ips.text and "Roth IRA" in ips.text and "Spouse" in ips.text
    st = api.get(f"/report/statement/{c['id']}")
    assert st.status_code == 200 and "Client Statement" in st.text and "VTI" in st.text


def test_direct_indexing(api):
    c = new_client(api, cash=500_000)
    cid = c["id"]
    pv = api.post(f"/api/clients/{cid}/direct-index/preview", json={"sleeve_weight": 0.3, "top_n": 30,
                                                                   "excluded_sectors": ["Energy"], "excluded_symbols": ["TSLA"]}).json()
    syms = {h["symbol"] for h in pv["holdings_list"]}
    assert pv["count"] == 30 and "TSLA" not in syms and "XOM" not in syms and "CVX" not in syms
    assert pv["tracking"]["available"] and 0 < pv["tracking"]["tracking_error"] < 0.3
    vti_before = next(t["weight"] for t in api.get(f"/api/clients/{cid}/targets").json() if t["symbol"] == "VTI")
    api.put(f"/api/clients/{cid}/direct-index", json={"sleeve_weight": 0.3, "top_n": 30, "excluded_sectors": ["Energy"], "excluded_symbols": ["TSLA"]})
    t = {x["symbol"]: x["weight"] for x in api.get(f"/api/clients/{cid}/targets").json()}
    assert sum(t.values()) == pytest.approx(1, abs=1e-6)
    assert sum(w for s, w in t.items() if s in syms) == pytest.approx(0.3, abs=1e-3)
    assert t["VTI"] < vti_before
    api.post(f"/api/clients/{cid}/rebalance", json={"mode": "full"})
    dash = api.get(f"/api/clients/{cid}/dashboard").json()
    assert not any("Concentration" in a["title"] or "sleeve over limit" in a["title"] for a in dash["alerts"])
    st = api.get(f"/api/clients/{cid}/direct-index").json()
    assert st["active"] and st["held_names"] == 30
    # harvest replacement is a same-sector index peer, not an ETF
    from app import db, tax
    with db.tx() as con:
        con.execute("UPDATE lots SET cost_per_share = cost_per_share*1.4 WHERE client_id=? AND symbol='MSFT'", (cid,))
    cand = next(h for h in tax.harvest_candidates(cid) if h["symbol"] == "MSFT")
    assert cand["replacement"] not in ("XLK", None) and cand["replacement"] not in syms
    # re-apply with a smaller sleeve, then remove
    api.put(f"/api/clients/{cid}/direct-index", json={"sleeve_weight": 0.1, "top_n": 20})
    t2 = {x["symbol"]: x["weight"] for x in api.get(f"/api/clients/{cid}/targets").json()}
    assert sum(w for s, w in t2.items() if s not in ("VTI", "VXUS", "QQQ", "BND", "TIP", "VNQ", "GLD", "VWO")) == pytest.approx(0.1, abs=1e-3)
    api.delete(f"/api/clients/{cid}/direct-index")
    t3 = {x["symbol"]: x["weight"] for x in api.get(f"/api/clients/{cid}/targets").json()}
    assert t3["VTI"] == pytest.approx(vti_before, abs=1e-4) and "MSFT" not in t3


def test_margin_buying_power_interest_and_call(api):
    from datetime import date, timedelta
    from app import db, lending
    c = new_client(api, cash=10_000)
    cid = c["id"]
    assert api.post(f"/api/clients/{cid}/orders", json={"symbol": "VTI", "side": "buy", "notional": 15_000}).status_code == 400
    api.post(f"/api/clients/{cid}/margin", json={"enabled": True, "rate": 0.10})
    st = api.get(f"/api/clients/{cid}/lending").json()["margin"]
    assert st["buying_power"] == pytest.approx(20_000, rel=1e-6)          # 2:1 Reg T on cash
    o = api.post(f"/api/clients/{cid}/orders", json={"symbol": "VTI", "side": "buy", "notional": 15_000}).json()
    assert o["status"] == "filled"
    cl = api.get(f"/api/clients/{cid}").json()
    assert cl["cash"] < -4_900                                          # borrowed ~5k
    with db.tx() as con:
        con.execute("UPDATE clients SET last_interest_date=? WHERE id=?", ((date.today() - timedelta(days=365)).isoformat(), cid))
    api.get(f"/api/clients/{cid}/lending")
    led = db.query("SELECT * FROM ledger WHERE client_id=? AND kind='margin_interest'", (cid,))
    assert led and led[0]["amount"] == pytest.approx(cl["cash"] * 0.10, rel=0.02)
    assert api.post(f"/api/clients/{cid}/margin", json={"enabled": False}).status_code == 400   # loan outstanding
    # force a margin call: pretend the position collapsed
    with db.tx() as con:
        con.execute("UPDATE clients SET cash = -12500 WHERE id=?", (cid,))   # equity ~17% of holdings < 30%
    ms = lending.margin_status(cid)
    assert ms["margin_call"] > 0
    assert any(a["title"] == "Margin call" for a in api.get(f"/api/clients/{cid}/dashboard").json()["alerts"])


def test_sbloc_and_securities_lending(api):
    from datetime import date, timedelta
    from app import db
    c = new_client(api, cash=200_000)
    cid = c["id"]
    api.post(f"/api/clients/{cid}/rebalance", json={"mode": "full"})
    sb = api.get(f"/api/clients/{cid}/lending").json()["sbloc"]
    assert 0.6 * 200_000 < sb["borrowing_base"] < 0.85 * 200_000
    assert api.post(f"/api/clients/{cid}/sbloc/draw", json={"amount": sb["available"] + 1000}).status_code == 400
    eq0 = api.get(f"/api/clients/{cid}/dashboard").json()["valuation"]["equity"]
    s2 = api.post(f"/api/clients/{cid}/sbloc/draw", json={"amount": 50_000}).json()
    assert s2["balance"] == pytest.approx(50_000)
    v = api.get(f"/api/clients/{cid}/dashboard").json()["valuation"]
    assert v["equity"] == pytest.approx(eq0, rel=0.01) and v["net_worth"] == pytest.approx(v["equity"] - 50_000, rel=1e-6)
    with db.tx() as con:
        con.execute("UPDATE clients SET last_interest_date=? WHERE id=?", ((date.today() - timedelta(days=30)).isoformat(), cid))
    api.post(f"/api/clients/{cid}/sec-lending", json={"enabled": True})
    api.get(f"/api/clients/{cid}/lending")
    s3 = api.get(f"/api/clients/{cid}/lending").json()
    assert s3["sbloc"]["balance"] > 50_000                              # interest capitalised
    assert s3["sec_lending"]["enabled"] and s3["sec_lending"]["income_ytd"] > 0
    api.post(f"/api/clients/{cid}/cash", json={"amount": 60_000})
    s4 = api.post(f"/api/clients/{cid}/sbloc/repay", json={"amount": 1e9}).json()
    assert s4["balance"] == 0


def test_options_trading_rules_and_expiry(api):
    from datetime import date, timedelta
    from app import db, options
    c = new_client(api, cash=100_000)
    cid = c["id"]
    ch = api.get("/api/options/chain/AAPL").json()
    row = ch["rows"][10]
    assert row["call"]["ask"] > row["call"]["bid"] > 0 and 0 < row["call"]["delta"] < 1 and -1 < row["put"]["delta"] < 0
    exp, k = ch["expiry"], row["strike"]
    order = {"underlying": "AAPL", "type": "C", "strike": k, "expiry": exp, "action": "BTO", "qty": 1}
    assert "enabled" in api.post(f"/api/clients/{cid}/options/order", json=order).json()["detail"]
    api.post(f"/api/clients/{cid}/options/level", json={"level": 1})
    assert "Level 2" in api.post(f"/api/clients/{cid}/options/order", json=order).json()["detail"]
    # covered call needs shares
    cc = {**order, "action": "STO"}
    assert "Covered calls need" in api.post(f"/api/clients/{cid}/options/order", json=cc).json()["detail"]
    api.post(f"/api/clients/{cid}/orders", json={"symbol": "AAPL", "side": "buy", "qty": 100})
    cash0 = api.get(f"/api/clients/{cid}").json()["cash"]
    r = api.post(f"/api/clients/{cid}/options/order", json=cc).json()
    assert r["position_qty"] == -1 and r["cash_effect"] > 0
    assert api.get(f"/api/clients/{cid}").json()["cash"] == pytest.approx(cash0 + r["cash_effect"])
    # covered shares can't be sold
    assert api.post(f"/api/clients/{cid}/orders", json={"symbol": "AAPL", "side": "sell", "qty": 50}).status_code == 400
    # cash-secured put reserves cash
    api.post(f"/api/clients/{cid}/options/level", json={"level": 2})
    put = {**order, "type": "P", "action": "STO", "qty": 1}
    api.post(f"/api/clients/{cid}/options/order", json=put)
    assert options.csp_reserve(cid) == pytest.approx(k * 100)
    # long call then sell to close
    lc = api.post(f"/api/clients/{cid}/options/order", json={**order, "strike": ch["rows"][14]["strike"]}).json()
    assert lc["position_qty"] == 1
    stc = api.post(f"/api/clients/{cid}/options/order", json={**order, "strike": ch["rows"][14]["strike"], "action": "STC"}).json()
    assert stc["realized_pnl"] < 0                                      # paid the spread
    # options count in equity
    v = api.get(f"/api/clients/{cid}/dashboard").json()["valuation"]
    assert len(v["options"]) == 2 and v["options_value"] < 0            # two short options
    # expiry: move contracts into the past and settle
    with db.tx() as con:
        con.execute("UPDATE option_positions SET expiry=? WHERE client_id=?", ((date.today() - timedelta(days=1)).isoformat(), cid))
    ev = options.process_expirations(cid)
    assert len(ev) == 2 and all(e["action"] in ("EXPIRE", "ASSIGN") for e in ev)
    assert not options.positions(cid)


def test_markets_board_and_watchlists(api):
    from app import markets
    markets._memo.clear()
    b = api.get("/api/markets").json()
    rows = {r["symbol"]: r for g in b["groups"] for r in g["rows"]}
    for s in ("^GSPC", "^RUT", "^NSEI", "^BSESN", "GCUSD", "CLUSD", "BTCUSD", "EURUSD", "^TNX"):
        assert rows[s]["available"] and rows[s]["price"] > 0 and len(rows[s]["spark"]) >= 20
    assert rows["EURUSD"]["price"] < 2 and rows["^NSEI"]["price"] > 10_000
    d = api.get("/api/markets/%5EGSPC?range=YTD").json()
    assert d["series"] and d["range"] == "YTD"

    wl = api.get("/api/watchlists").json()
    assert len(wl) == 1 and wl[0]["count"] == len(markets.DEFAULT_SYMBOLS)
    wid = wl[0]["id"]
    assert api.post(f"/api/watchlists/{wid}/symbols", json={"symbol": "tsla"}).status_code == 200
    assert api.post(f"/api/watchlists/{wid}/symbols", json={"symbol": "TSLA"}).status_code == 400   # duplicate
    v = api.get(f"/api/watchlists/{wid}?range=3M").json()
    assert "TSLA" in [r["symbol"] for r in v["rows"]] and v["performance"]["series"] and len(v["compare"]) == 3
    assert api.delete(f"/api/watchlists/{wid}/symbols/TSLA").status_code == 200
    assert api.delete(f"/api/watchlists/{wid}").status_code == 400                                 # keep at least one
    new = api.post("/api/watchlists", json={"name": "Commodities"}).json()
    assert api.patch(f"/api/watchlists/{new['id']}", json={"name": "Metals"}).json()["name"] == "Metals"
    assert api.delete(f"/api/watchlists/{new['id']}").status_code == 200
    assert api.get("/api/search?q=nifty").json()[0]["symbol"] == "^NSEI"


# ---------------------------------------------------------------- live (Yahoo) provider, offline with a fake HTTP session
class _FakeResp:
    def __init__(self, data, code=200, text=""):
        self._d, self.status_code, self.text = data, code, text

    def json(self):
        return self._d


class _FakeYahoo:
    """Answers the two Yahoo endpoints the app uses with realistic payloads."""
    def __init__(self):
        import requests
        self.headers = requests.structures.CaseInsensitiveDict()
        self.calls = []

    def get(self, url, params=None, timeout=None):
        import time as _t
        self.calls.append((url, dict(params or {})))
        if "fc.yahoo.com" in url:
            return _FakeResp({}, 404)
        if "/v1/test/getcrumb" in url:
            return _FakeResp({}, 200, text="abcCRUMB")
        if "/ws/fundamentals-timeseries/" in url:
            return _FakeResp(_fake_timeseries(params["type"].split(",")))
        if "/v6/finance/recommendationsbysymbol/" in url:
            return _FakeResp({"finance": {"result": [{"symbol": "AAPL", "recommendedSymbols": [
                {"symbol": s} for s in ("MSFT", "GOOG", "AMZN")]}], "error": None}})
        if "/v10/finance/quoteSummary/" in url:
            assert params.get("crumb") == "abcCRUMB"
            R = lambda v: {"raw": v, "fmt": str(v)}
            return _FakeResp({"quoteSummary": {"result": [{
                "summaryProfile": {"sector": "Technology", "industry": "Consumer Electronics", "longBusinessSummary": "Makes phones."},
                "defaultKeyStatistics": {"beta": R(1.1)}, "price": {"marketCap": R(1.5e12)},
                "financialData": {"targetMeanPrice": R(120.0), "targetMedianPrice": R(118.0), "targetHighPrice": R(150.0),
                                  "targetLowPrice": R(90.0), "numberOfAnalystOpinions": R(30), "recommendationKey": "buy",
                                  "financialCurrency": "USD"},
                "earningsTrend": {"trend": [
                    {"period": "0q", "endDate": "2026-09-30", "revenueEstimate": {"avg": R(3e10), "numberOfAnalysts": R(20)}, "earningsEstimate": {"avg": R(1.2)}},
                    {"period": "0y", "endDate": "2026-09-30", "revenueEstimate": {"avg": R(1.30e11), "numberOfAnalysts": R(30)}, "earningsEstimate": {"avg": R(5.4)}},
                    {"period": "+1y", "endDate": "2027-09-30", "revenueEstimate": {"avg": R(1.40e11), "numberOfAnalysts": R(28)}, "earningsEstimate": {"avg": R(6.0)}}]}}],
                "error": None}})
        if "/v1/finance/search" in url:
            return _FakeResp({"quotes": [{"symbol": "NVDA", "longname": "NVIDIA Corporation", "exchDisp": "NASDAQ",
                                          "typeDisp": "Equity", "quoteType": "EQUITY", "sector": "Technology",
                                          "industry": "Semiconductors", "isYahooFinance": True}]})
        sym = url.rsplit("/", 1)[-1]
        if sym == "NOPE":
            return _FakeResp({"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found"}}}, 404)
        now = int(_t.time())
        ts = [now - 86400 * (300 - i) for i in range(300)]
        base = {"%5ENSEI": 23140.5, "GC%3DF": 4321.2, "EURUSD%3DX": 1.17}.get(sym, 100.0)
        closes = [base * (0.9 + 0.1 * i / 299) for i in range(300)]
        meta = {"symbol": sym, "currency": "INR" if "NSEI" in sym else "USD", "instrumentType": "INDEX" if "%5E" in sym else "FUTURE" if "%3DF" in sym else "EQUITY",
                "regularMarketPrice": base, "fulldayChange": base * 0.00336 / 1.00336, "regularMarketChangePercent": 0.336,
                "fiftyTwoWeekHigh": base * 1.1, "fiftyTwoWeekLow": base * 0.8, "regularMarketDayHigh": base * 1.001,
                "regularMarketDayLow": base * 0.995, "regularMarketVolume": 1000, "longName": "NIFTY 50" if "NSEI" in sym else sym,
                "regularMarketTime": now, "gmtoffset": 19800, "fullExchangeName": "NSE"}
        return _FakeResp({"chart": {"result": [{"meta": meta, "timestamp": ts,
                          "indicators": {"quote": [{"close": closes, "open": [c * 0.998 for c in closes], "high": [c * 1.01 for c in closes],
                                                    "low": [c * 0.99 for c in closes], "volume": [1000] * len(closes)}],
                                         "adjclose": [{"adjclose": closes}]}}], "error": None}})


def _fake_timeseries(types):
    """Four fiscal years of a profitable, growing company (values in USD)."""
    years = ["2022-09-30", "2023-09-30", "2024-09-30", "2025-09-30"]
    base = {"TotalRevenue": 1.0e11, "GrossProfit": 4.4e10, "OperatingIncome": 3.0e10, "EBIT": 3.0e10, "EBITDA": 3.4e10,
            "InterestExpense": 1.0e9, "PretaxIncome": 2.9e10, "TaxProvision": 4.6e9, "NetIncomeCommonStockholders": 2.44e10,
            "NetIncome": 2.44e10, "DilutedEPS": 2.44, "DilutedAverageShares": 1.0e10, "TotalAssets": 9.0e10,
            "CurrentAssets": 3.5e10, "CurrentLiabilities": 3.0e10, "TotalDebt": 2.5e10, "LongTermDebt": 2.0e10,
            "CashAndCashEquivalents": 1.0e10, "CashCashEquivalentsAndShortTermInvestments": 1.5e10,
            "StockholdersEquity": 2.5e10, "OperatingCashFlow": 2.9e10, "CapitalExpenditure": -3.0e9,
            "FreeCashFlow": 2.6e10, "DepreciationAndAmortization": 4.0e9, "StockBasedCompensation": 2.0e9}
    res = []
    for t in types:
        if t.startswith("annual") and t[6:] in base:
            v = base[t[6:]]
            rows = [{"asOfDate": d, "periodType": "12M", "currencyCode": "USD",
                     "reportedValue": {"raw": v * (1.08 ** i) if t[6:] != "DilutedAverageShares" else v * (1 - 0.01 * i)}}
                    for i, d in enumerate(years)]
        elif t in ("trailingPeRatio", "trailingEnterprisesValueEBITDARatio", "trailingPsRatio", "trailingMarketCap"):
            v = {"trailingPeRatio": 35.0, "trailingEnterprisesValueEBITDARatio": 25.0, "trailingPsRatio": 8.0,
                 "trailingMarketCap": 1.0e12}[t]
            rows = [{"asOfDate": "2026-09-20", "periodType": "TTM", "reportedValue": {"raw": v}}]
        else:
            rows = []
        res.append({"meta": {"symbol": ["X"], "type": [t]}, "timestamp": [1] * len(rows), t: rows} if rows
                   else {"meta": {"symbol": ["X"], "type": [t]}})
    return {"timeseries": {"result": res, "error": None}}


def test_yahoo_symbol_mapping():
    from app.market_data import to_yahoo
    assert to_yahoo("GCUSD") == "GC=F" and to_yahoo("CLUSD") == "CL=F"
    assert to_yahoo("BTCUSD") == "BTC-USD" and to_yahoo("ethusd") == "ETH-USD"
    assert to_yahoo("EURUSD") == "EURUSD=X" and to_yahoo("USDINR") == "USDINR=X"
    assert to_yahoo("^NSEI") == "^NSEI" and to_yahoo("BRK.B") == "BRK-B" and to_yahoo("RELIANCE.NS") == "RELIANCE.NS"
    assert to_yahoo("AAPL") == "AAPL"


def test_live_provider_quotes_history_and_free_research(api):
    from app import research
    fake = _FakeYahoo()
    p = market_data.LiveProvider(None, session=fake)
    market_data.set_provider(p)
    q = p.quote("^NSEI")
    assert q["price"] == 23140.5 and abs(q["change_pct"] - 0.336) < 1e-6 and q["kind"] == "Index" and q["currency"] == "INR"
    assert abs(q["prev_close"] - 23140.5 / 1.00336) < 0.01
    assert p.quote("GCUSD")["price"] == 4321.2 and "GC%3DF" in fake.calls[-1][0]
    h = p.history("^NSEI", 30)
    assert 15 <= len(h) <= 31 and h.index.is_monotonic_increasing
    n = len(fake.calls)
    p.history("^NSEI", 365)                      # same cached 400-day window -> no new HTTP call
    assert len(fake.calls) == n
    assert p.search("nvidia")[0]["symbol"] == "NVDA"
    with pytest.raises(market_data.DataError):
        p.quote("NOPE")
    # research works on free Yahoo fundamentals (no FMP key)
    inc = p.income("AAPL", 5)
    assert len(inc) == 4 and inc[0]["date"] == "2025-09-30" and inc[0]["netIncome"] > inc[1]["netIncome"]
    assert p.cashflow("AAPL")[0]["capitalExpenditure"] < 0 and p.balance("AAPL")[0]["totalDebt"] > 0
    assert p.peers("AAPL") == ["MSFT", "GOOG", "AMZN"]
    assert p.multiples("MSFT")["pe"] == 35.0 and p.multiples("MSFT")["pfcf"] > 0
    assert [e["date"] for e in p.estimates("AAPL")] == ["2026-09-30", "2027-09-30"]
    assert p.price_target("AAPL")["consensus"] == 120.0
    prof = p.profile("AAPL")
    assert prof["sector"] == "Technology" and prof["beta"] == 1.1 and not prof["is_etf"]
    r = research.analyze("AAPL")
    assert r["rating"] in ("BUY", "HOLD", "SELL")
    assert r["street"]["price_target"]["consensus"] == 120.0 and r["street"]["revenue_growth"]
    assert p.index_constituents()                # falls back to the static top-50 list
    market_data.set_provider(market_data.SimProvider())


def test_carryover_copies_clients_not_positions(api, tmp_path, monkeypatch):
    import sqlite3
    from app import carryover, portfolio
    # build a small "simulated" database on disk
    sim = tmp_path / "portfolio.db"
    con = sqlite3.connect(sim)
    con.executescript(db.SCHEMA)
    db._migrate(con)
    con.execute("""INSERT INTO clients(name,email,risk_score,risk_profile,questionnaire,starting_cash,cash,created_at,account_type,stop_loss_pct)
                   VALUES('Old Client','o@x.com',60,'Growth',?,250000,1000,'2026-09-25','Individual',0.15)""",
                ('{"age": 1, "horizon": 3, "income": 2, "liquidity": 2, "goal": 3, "drawdown": 2, "loss_tolerance": 3, "experience": 2}',))
    con.execute("INSERT INTO targets(client_id,symbol,weight) VALUES(1,'VTI',0.7),(1,'BND',0.3)")
    con.execute("INSERT INTO positions(client_id,symbol,qty,avg_cost) VALUES(1,'VTI',100,250)")
    con.execute("INSERT INTO watchlists(name,created_at) VALUES('Ideas','2026-09-25')")
    con.execute("INSERT INTO watchlist_items(watchlist_id,symbol,position,added_at) VALUES(1,'TSLA',1,'2026-09-25')")
    con.commit(); con.close()
    monkeypatch.setattr(config, "SIM_DB_PATH", sim)
    monkeypatch.setattr(config, "DATA_MODE", "live")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "portfolio-live.db")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    for c in db.query("SELECT id FROM clients"):
        portfolio.delete_client(c["id"]) if hasattr(portfolio, "delete_client") else None
    db.get_conn().execute("DELETE FROM clients"); db.get_conn().commit()
    r = carryover.run()
    assert r["clients"] == ["Old Client"] and r["watchlists"] == 1
    c = db.query_one("SELECT * FROM clients WHERE name='Old Client'")
    assert c["cash"] == 250000 and abs(c["stop_loss_pct"] - 0.15) < 1e-9          # fresh funding, settings kept
    assert not db.query("SELECT * FROM positions WHERE client_id=?", (c["id"],))     # no simulated positions
    assert portfolio.get_targets(c["id"]) == {"VTI": 0.7, "BND": 0.3}
    assert carryover.run().get("skipped")                                             # runs only once


def test_morning_briefing(api):
    from app import markets
    markets._memo.clear()
    c = new_client(api)
    b = api.get("/api/briefing").json()
    assert b["greeting"].startswith("Good") and b["owner"] and b["brand"]
    assert b["mood"]["label"] in ("Risk-on", "Risk-off", "Mixed") and -1 <= b["mood"]["score"] <= 1
    assert b["book"]["clients"] >= 1 and b["book"]["aum"] > 0
    assert any(a["client_id"] == c["id"] and a["title"].startswith("Rebalancing") for a in b["actions"])   # new, uninvested client
    assert "rebalance" in b["summary"]
    assert api.get("/report/statement/%d" % c["id"]).status_code == 200


def test_candles_for_pro_chart(api):
    # simulated provider: every interval, sorted, valid OHLC
    for iv in ("5m", "15m", "60m", "1d", "1wk", "1mo"):
        d = api.get(f"/api/markets/AAPL/candles?interval={iv}").json()
        cs = d["candles"]
        assert len(cs) > 5 and d["intraday"] == (iv in ("5m", "15m", "60m"))
        assert all(c["l"] <= min(c["o"], c["c"]) + 1e-9 and c["h"] >= max(c["o"], c["c"]) - 1e-9 for c in cs)
        assert [c["t"] for c in cs] == sorted(c["t"] for c in cs)
    assert api.get("/api/markets/AAPL/candles?interval=2d").status_code == 400
    # live provider parses Yahoo OHLC (split-adjusted) with exchange-local timestamps
    p = market_data.LiveProvider(None, session=_FakeYahoo())
    daily = p.candles("^NSEI", "1d")
    assert daily and isinstance(daily[0]["t"], str) and abs(daily[-1]["c"] - 23140.5) < 1e-6 and daily[-1]["h"] > daily[-1]["c"]
    intra = p.candles("^NSEI", "5m")
    assert isinstance(intra[0]["t"], int)
    weekly = market_data.resample_candles(daily, "W")
    assert 0 < len(weekly) < len(daily) and weekly[-1]["c"] == daily[-1]["c"]


WSJ_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>WSJ.com: Markets</title>
<item><title>Stocks Rise as Fed Holds &amp; Yields Ease</title>
<description><![CDATA[<p>Investors cheered the decision.</p>]]></description>
<link>https://www.wsj.com/finance/stocks/stocks-rise-abc123</link>
<pubDate>Fri, 25 Sep 2026 14:05:00 GMT</pubDate>
<media:content url="https://images.wsj.net/im-1234" type="image/jpeg" medium="image"/>
<dc:creator>Jane Doe</dc:creator></item>
<item><title>Oil Slips on Supply Worries</title><link>https://www.wsj.com/finance/commodities/oil-xyz</link>
<pubDate>Fri, 25 Sep 2026 12:00:00 GMT</pubDate></item>
<item><title>No link item</title></item>
</channel></rss>"""


class _FakeNewsSession:
    def __init__(self):
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)

        class R:
            status_code = 200
            text = WSJ_SAMPLE.replace("Stocks Rise", "Stocks Rise") if "dowjones" in url else WSJ_SAMPLE.replace(
                "www.wsj.com", "finance.yahoo.com")
        return R()


def test_news_desk_parses_wsj_and_ticker(api, monkeypatch):
    from app import news
    items = news.parse_rss(WSJ_SAMPLE, "WSJ", "Markets")
    assert len(items) == 2
    top = items[0]
    assert top["title"] == "Stocks Rise as Fed Holds & Yields Ease"
    assert top["summary"] == "Investors cheered the decision."
    assert top["image"] == "https://images.wsj.net/im-1234" and top["authors"] == ["Jane Doe"]
    assert top["publisher"] == "WSJ" and top["ts"] > items[1]["ts"]

    fake = _FakeNewsSession()
    monkeypatch.setattr(news, "_session", fake)
    monkeypatch.setattr(news, "_cache_get", lambda k, ttl: None)
    monkeypatch.setattr(news, "_cache_put", lambda k, v: None)
    d = api.get("/api/news?section=top").json()
    assert len(d["items"]) == 2          # same stories across 5 feeds are de-duplicated
    assert any(s["key"] == "markets" for s in d["sections"]) and not d["errors"]
    assert sum("dowjones" in u for u in fake.urls) == len(news.DEFAULT_MIX)
    assert api.get("/api/news?section=markets").json()["items"][0]["section"] == "Markets"
    assert api.get("/api/news?section=nope").status_code == 400
    t = api.get("/api/news/ticker/AAPL").json()
    assert t["items"][0]["publisher"] == "Yahoo Finance"
    w = api.get("/api/news/watchlist").json()
    assert "items" in w


def test_efficient_frontier_lab(api):
    body = {"symbols": ["AAPL", "MSFT", "NVDA", "JPM", "BND", "GLD"], "lookback": "3y", "method": "blend",
            "max_weight": 0.4, "my_weights": {"AAPL": 0.5, "NVDA": 0.5}}
    r = api.post("/api/frontier", json=body).json()
    f = r["frontier"]
    assert len(f) >= 8
    assert all(b["vol"] >= a["vol"] - 1e-9 and b["ret"] > a["ret"] for a, b in zip(f, f[1:]))   # efficient: up and to the right
    for p in f + [r["max_sharpe"], r["min_variance"]]:
        assert abs(sum(p["weights"].values()) - 1) < 0.01 and max(p["weights"].values()) <= 0.4 + 1e-3
    assert r["min_variance"]["vol"] <= min(p["vol"] for p in f) + 1e-6
    assert r["max_sharpe"]["sharpe"] >= max(p["sharpe"] for p in f) - 1e-4
    assert r["mine"]["efficiency_gap"] >= -1e-6 and r["mine"]["same_risk"]["vol"] <= r["mine"]["vol"] + 1e-6
    assert len(r["corr"]) == 6 and r["cml"]["points"] and "Max Sharpe" in r["backtest"]["series"]
    # the random cloud never beats the frontier's best Sharpe
    rf = r["risk_free"]
    assert max((y - rf) / x for x, y in r["cloud"] if x > 0) <= r["max_sharpe"]["sharpe"] + 0.05
    m = api.post("/api/frontier/save", json={"name": "Test Max Sharpe", "weights": r["max_sharpe"]["weights"]}).json()
    assert m["category"] == "Efficient frontier" and abs(sum(m["holdings"].values()) - 1) < 1e-6
    assert api.post("/api/frontier", json={"symbols": ["AAPL"]}).status_code == 400
