"""FastAPI application: JSON API + the single-page web UI."""
from __future__ import annotations

import json

import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from datetime import date as _date

from . import (accounts, analytics, assistant, billing, briefing, carryover, config, db, direct_index, frontier, lending, markets, model_library, news, monitor, options,
               planning, portfolio, rebalance, research, risk_profile, tax, trading)
from .market_data import DataError, get_provider
from .mcp_server import build_server, remote_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"


# Remote MCP endpoint for ChatGPT (and other remote AI clients), protected by a secret path.
MCP_TOKEN = remote_token()
MCP_PREFIX = f"/connect/{MCP_TOKEN}"
_remote_mcp = build_server(remote=True)


class _MCPMount:
    """ASGI shim so the MCP session manager can be (re)created on each app start-up."""
    app = None

    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)


_remote_mcp_app = _MCPMount()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.get_conn()
    billing.seed()
    model_library.seed()
    try:
        carryover.run()
    except Exception as e:                     # never block start-up
        logging.getLogger(__name__).warning("carry-over failed: %s", e)
    markets.seed()
    _remote_mcp._session_manager = None          # a session manager can only run once
    _remote_mcp_app.app = _remote_mcp.streamable_http_app()
    async with _remote_mcp.session_manager.run():
        task = _start_monitor()
        yield
        if task:
            task.cancel()


def _start_monitor():
    task = None
    if config.MONITOR_INTERVAL > 0:
        async def loop():
            while True:
                await asyncio.sleep(config.MONITOR_INTERVAL)
                await asyncio.to_thread(monitor.run_cycle)
        task = asyncio.create_task(loop())
    return task


app = FastAPI(title="Portfolio Management Platform", version=config.APP_VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount(MCP_PREFIX, _remote_mcp_app)


@app.middleware("http")
async def tunnel_guard(request: Request, call_next):
    """Anything arriving through a tunnel/proxy (Cloudflare adds cf-connecting-ip, most proxies add
    x-forwarded-for) may only reach the secret MCP endpoint - never the web UI or the JSON API."""
    proxied = "cf-connecting-ip" in request.headers or "x-forwarded-for" in request.headers
    if proxied and not request.url.path.startswith(MCP_PREFIX):
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"      # always revalidate, so updates show immediately
    return response


@app.get("/api/connect-info")
def connect_info(request: Request):
    """Connection details for AI assistants (served only to local, non-proxied requests)."""
    return {"mcp_path": MCP_PREFIX + "/mcp", "stdio_script": str(config.ROOT / "mcp_server.py"),
            "python": str(config.ROOT / ".venv" / "Scripts" / "python.exe")}


# ------------------------------------------------------------------ error mapping
@app.exception_handler(KeyError)
async def _not_found(_: Request, e: KeyError):
    return JSONResponse(status_code=404, content={"detail": str(e).strip("'\"")})


@app.exception_handler(ValueError)
async def _bad_request(_: Request, e: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(e)})


@app.exception_handler(assistant.AssistantError)
async def _assistant_error(_: Request, e: assistant.AssistantError):
    return JSONResponse(status_code=400, content={"detail": str(e)})


@app.exception_handler(DataError)
async def _data_error(_: Request, e: DataError):
    return JSONResponse(status_code=502, content={"detail": f"Market data error: {e}"})


# ------------------------------------------------------------------ models
class ClientIn(BaseModel):
    name: str = Field(min_length=1)
    email: str | None = None
    answers: dict[str, int]
    starting_cash: float = 100_000
    profile_override: str | None = None
    account_type: str | None = None
    beneficiaries: list[dict] | None = None
    phone: str | None = None
    objective: str | None = None
    model_id: int | None = None


class ClientPatch(BaseModel):
    account_type: str | None = None
    beneficiaries: list[dict] | None = None
    phone: str | None = None
    objective: str | None = None
    options_level: int | None = None
    margin_rate: float | None = None
    sbloc_rate: float | None = None
    fee_schedule_id: int | None = None
    lot_method: str | None = None
    st_tax_rate: float | None = None
    lt_tax_rate: float | None = None
    name: str | None = None
    email: str | None = None
    risk_profile: str | None = None
    drift_abs_band: float | None = None
    drift_rel_band: float | None = None
    cash_target: float | None = None
    stop_loss_pct: float | None = None
    max_position: float | None = None
    notes: str | None = None
    reset_targets_to_model: bool = False


class OrderIn(BaseModel):
    symbol: str
    side: str
    qty: float | None = None
    notional: float | None = None
    order_type: str = "market"
    limit_price: float | None = None
    stop_price: float | None = None


class TargetsIn(BaseModel):
    targets: dict[str, float]


class AddStockIn(BaseModel):
    symbol: str
    weight: float


class CashIn(BaseModel):
    amount: float
    note: str | None = None


class RebalanceIn(BaseModel):
    mode: str = "full"


class FeeScheduleIn(BaseModel):
    name: str = Field(min_length=1)
    tiers: list[list[float | None]]
    description: str = ""


class BillingRunIn(BaseModel):
    period_start: str | None = None
    period_end: str | None = None
    client_ids: list[int] | None = None


class HarvestIn(BaseModel):
    symbol: str
    replacement: str | None = None


class ModelIn(BaseModel):
    name: str = Field(min_length=1)
    holdings: dict[str, float]
    category: str = "Custom"
    description: str = ""


class GoalIn(BaseModel):
    name: str = Field(min_length=1)
    target_amount: float
    target_date: str
    monthly_contribution: float = 0
    allocation_pct: float = 1.0
    inflation_adjust: bool = True


class DirectIndexIn(BaseModel):
    sleeve_weight: float
    top_n: int = 50
    excluded_sectors: list[str] = []
    excluded_symbols: list[str] = []


class ToggleIn(BaseModel):
    enabled: bool
    rate: float | None = None


class AmountIn(BaseModel):
    amount: float
    from_cash: bool = True


class OptionOrderIn(BaseModel):
    underlying: str
    type: str
    strike: float
    expiry: str
    action: str
    qty: int = 1


class LevelIn(BaseModel):
    level: int


class AssistantSettingsIn(BaseModel):
    provider: str | None = None
    model_claude: str | None = None
    model_openai: str | None = None
    claude_key: str | None = None
    openai_key: str | None = None


class ChatIn(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: str | None = None
    client_id: int | None = None


class DecisionIn(BaseModel):
    conversation_id: str
    approve: bool


# ------------------------------------------------------------------ pages
@app.get("/")
def index():
    # Version-stamp the asset URLs so the app window never shows a stale UI after an update.
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    v = config.APP_VERSION
    for asset in ("styles.css", "app.js", "markets.js", "prochart.js", "news.js", "frontier.js", "vendor/lightweight-charts.js", "studio.js", "story.js", "motion.js", "scenes.js", "chart.umd.js"):
        html = html.replace(f"/static/{asset}\"", f"/static/{asset}?v={v}\"")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/api/config")
def get_config():
    return {"version": config.APP_VERSION, "data_mode": config.DATA_MODE, "fmp_key": bool(config.FMP_API_KEY), "brand": config.BRAND_NAME, "owner": config.OWNER_NAME, "benchmark": config.BENCHMARK, "fractional": config.ALLOW_FRACTIONAL,
            "slippage_bps": config.SLIPPAGE_BPS, "commission": config.COMMISSION_PER_TRADE,
            "cash_interest_rate": config.CASH_INTEREST_RATE, "asset_classes": risk_profile.ASSET_CLASS,
            "account_types": accounts.ACCOUNT_TYPES, "options_levels": accounts.OPTIONS_LEVELS,
            "risk_free_rate": config.RISK_FREE_RATE, "erp": config.EQUITY_RISK_PREMIUM,
            "profiles": risk_profile.PROFILES, "model_portfolios": risk_profile.MODEL_PORTFOLIOS}


@app.post("/api/shutdown")
def shutdown():
    """Stop the local server (used by the Quit button in the desktop app)."""
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return {"ok": True}


# ------------------------------------------------------------------ risk profiling
@app.get("/api/questionnaire")
def questionnaire():
    return risk_profile.QUESTIONNAIRE


@app.post("/api/risk/score")
def risk_score(answers: dict[str, int]):
    res = risk_profile.score_questionnaire(answers)
    res["model"] = risk_profile.model_targets(res["profile"])
    return res


# ------------------------------------------------------------------ clients
@app.get("/api/clients")
def clients():
    out = []
    for c in portfolio.list_clients():
        try:
            v = portfolio.valuation(c["id"])
            c.update(equity=v["equity"], total_return=v["total_return"], day_change_pct=v["day_change_pct"])
        except DataError:
            c.update(equity=None, total_return=None, day_change_pct=None)
        out.append(c)
    return out


@app.post("/api/clients")
def create_client(body: ClientIn):
    return portfolio.create_client(body.name, body.email, body.answers, body.starting_cash, body.profile_override,
                                   body.account_type, body.beneficiaries, body.phone, body.objective, body.model_id)


@app.get("/api/clients/{cid}")
def client(cid: int):
    return portfolio.get_client(cid)


@app.patch("/api/clients/{cid}")
def patch_client(cid: int, body: ClientPatch):
    c = portfolio.update_client(cid, body.model_dump(exclude={"reset_targets_to_model"}))
    if body.reset_targets_to_model or body.risk_profile:
        model_library.assign(cid, model_library.core_model_id(c["risk_profile"]))
        c = portfolio.get_client(cid)
    return c


@app.delete("/api/clients/{cid}")
def delete_client(cid: int):
    portfolio.get_client(cid)
    portfolio.delete_client(cid)
    return {"ok": True}


@app.post("/api/clients/{cid}/cash")
def cash(cid: int, body: CashIn):
    return portfolio.deposit(cid, body.amount, body.note or "")


_metrics_cache: dict = {}


def _metrics_for(cid: int, val: dict) -> dict:
    weights = {h["symbol"]: h["weight"] for h in val["holdings"] if h["qty"] > 0}
    key = (cid, tuple(sorted((k, round(v, 3)) for k, v in weights.items())))
    if key not in _metrics_cache:
        _metrics_cache.clear() if len(_metrics_cache) > 200 else None
        _metrics_cache[key] = analytics.portfolio_metrics(weights, val["cash_weight"])
    return _metrics_cache[key]


@app.get("/api/clients/{cid}/dashboard")
def dashboard(cid: int):
    client = portfolio.get_client(cid)
    trading.process_open_orders(cid)
    options.process_expirations(cid)
    billing.accrue_interest(cid)
    client = portfolio.get_client(cid)
    val = portfolio.valuation(cid)
    portfolio.record_snapshot(cid, val)
    metrics = _metrics_for(cid, val)
    prof = risk_profile.profile_by_name(client["risk_profile"])
    ratings = research.cached_ratings([h["symbol"] for h in val["holdings"]])
    for h in val["holdings"]:
        r = ratings.get(h["symbol"])
        h["rating"] = r["rating"] if r else None
    return {"client": client, "profile": prof, "valuation": val, "metrics": metrics,
            "alerts": monitor.alerts(cid, val, metrics), "drift": rebalance.drift_report(cid, val),
            "performance": portfolio.time_weighted_index(cid)}


@app.get("/api/clients/{cid}/risk")
def risk(cid: int):
    val = portfolio.valuation(cid)
    targets = {s: w * (1 - portfolio.get_client(cid)["cash_target"]) for s, w in portfolio.get_targets(cid).items()}
    return {"current": _metrics_for(cid, val),
            "target_model": analytics.portfolio_metrics(targets, portfolio.get_client(cid)["cash_target"])}


# ------------------------------------------------------------------ targets
@app.get("/api/clients/{cid}/targets")
def targets(cid: int):
    return [{"symbol": s, "weight": w, "asset_class": risk_profile.asset_class(s)} for s, w in portfolio.get_targets(cid).items()]


@app.put("/api/clients/{cid}/targets")
def put_targets(cid: int, body: TargetsIn):
    portfolio.get_client(cid)
    return portfolio.set_targets(cid, body.targets)


@app.post("/api/clients/{cid}/targets/add-stock")
def add_stock(cid: int, body: AddStockIn):
    c = portfolio.get_client(cid)
    get_provider().quote(body.symbol)            # validates the ticker
    t = risk_profile.add_stock_to_targets(portfolio.get_targets(cid), body.symbol, body.weight, c["risk_profile"])
    return portfolio.set_targets(cid, t)


@app.post("/api/clients/{cid}/targets/remove-stock/{symbol}")
def remove_stock(cid: int, symbol: str):
    c = portfolio.get_client(cid)
    t = portfolio.get_targets(cid)
    symbol = symbol.upper()
    w = t.pop(symbol, 0)
    if w and "VTI" in t:
        t["VTI"] += w
    elif w:
        t = risk_profile.normalize(t)
    return portfolio.set_targets(cid, t if t else risk_profile.model_targets(c["risk_profile"]))


# ------------------------------------------------------------------ rebalancing
@app.get("/api/clients/{cid}/rebalance")
def rebalance_preview(cid: int, mode: str = "full"):
    return rebalance.preview(cid, mode)


@app.post("/api/clients/{cid}/rebalance")
def rebalance_execute(cid: int, body: RebalanceIn):
    return rebalance.execute(cid, body.mode)


# ------------------------------------------------------------------ trading
@app.post("/api/clients/{cid}/orders")
def order(cid: int, body: OrderIn):
    o = trading.place_order(cid, body.symbol, body.side, body.qty, body.notional, body.order_type,
                            body.limit_price, body.stop_price)
    portfolio.record_snapshot(cid)
    return o


@app.get("/api/clients/{cid}/orders")
def orders(cid: int, status: str | None = None):
    return trading.list_orders(cid, status)


@app.delete("/api/clients/{cid}/orders/{oid}")
def cancel(cid: int, oid: int):
    return trading.cancel_order(cid, oid)


# ------------------------------------------------------------------ billing & cash
def _period(a, b):
    return (_date.fromisoformat(a), _date.fromisoformat(b)) if a and b else (None, None)


@app.get("/api/billing/schedules")
def fee_schedules():
    return billing.schedules()


@app.post("/api/billing/schedules")
def fee_schedule_create(body: FeeScheduleIn):
    return billing.save_schedule(body.name, body.tiers, body.description)


@app.put("/api/billing/schedules/{sid}")
def fee_schedule_update(sid: int, body: FeeScheduleIn):
    return billing.save_schedule(body.name, body.tiers, body.description, sid)


@app.get("/api/billing/summary")
def billing_summary():
    return billing.firm_summary()


@app.get("/api/billing/preview")
def billing_preview(period_start: str | None = None, period_end: str | None = None):
    return billing.preview(*_period(period_start, period_end))


@app.post("/api/billing/run")
def billing_run(body: BillingRunIn):
    return billing.run_billing(*_period(body.period_start, body.period_end), body.client_ids)


@app.get("/api/billing/invoices")
def billing_invoices(client_id: int | None = None):
    return billing.invoices(client_id)


@app.post("/api/billing/invoices/{iid}/pay")
def invoice_pay(iid: int):
    return billing.pay_invoice(iid)


@app.post("/api/billing/invoices/{iid}/waive")
def invoice_waive(iid: int):
    return billing.waive_invoice(iid)


@app.get("/api/clients/{cid}/billing")
def client_billing(cid: int):
    client = portfolio.get_client(cid)
    billing.accrue_interest(cid)
    s, e = billing.default_period()
    return {"schedule": billing.client_schedule(client), "schedules": billing.schedules(),
            "current_period": billing.compute_fee(cid, s, e), "invoices": billing.invoices(cid),
            "interest": billing.interest_summary(cid), "activity": billing.activity(cid)}


# ------------------------------------------------------------------ tax
@app.get("/api/clients/{cid}/tax")
def client_tax(cid: int, year: int | None = None):
    return tax.report(cid, year)


@app.post("/api/clients/{cid}/tax/harvest")
def client_harvest(cid: int, body: HarvestIn):
    return tax.harvest(cid, body.symbol, body.replacement)


# ------------------------------------------------------------------ model marketplace
class FrontierIn(BaseModel):
    symbols: list[str]
    lookback: str = "3y"
    method: str = "blend"
    max_weight: float = 0.40
    shrink: bool = True
    my_weights: dict[str, float] | None = None
    risk_free: float | None = None


class FrontierSaveIn(BaseModel):
    name: str
    weights: dict[str, float]
    description: str = ""


@app.post("/api/frontier")
def frontier_run(body: FrontierIn):
    return frontier.analyze(body.symbols, body.lookback, body.method, body.max_weight, body.shrink,
                            body.my_weights, risk_free=body.risk_free)


class ClientLogIn(BaseModel):
    kind: str = "error"
    message: str = ""
    where: str = ""
    extra: dict | None = None


_client_log_count = {"n": 0}


@app.post("/api/clientlog")
def client_log(body: ClientLogIn):
    """Browser-side errors are written to data/server.log so they can be diagnosed."""
    _client_log_count["n"] += 1
    if _client_log_count["n"] <= 500:
        print(f"[client {body.kind}] {body.where} :: {body.message[:800]} {json.dumps(body.extra)[:1500] if body.extra else ''}", flush=True)
    return {"ok": True}


class FrontierPrefetchIn(BaseModel):
    symbols: list[str]
    lookback: str = "3y"


@app.post("/api/frontier/prefetch")
def frontier_prefetch(body: FrontierPrefetchIn):
    return frontier.prefetch(body.symbols, body.lookback)


@app.post("/api/frontier/save")
def frontier_save(body: FrontierSaveIn):
    return model_library.save_model(body.name.strip() or "Efficient portfolio", frontier.to_model_weights(body.weights),
                                    "Efficient frontier", body.description)


@app.get("/api/models")
def models():
    return model_library.list_models()


@app.get("/api/models/{mid}")
def model(mid: int):
    m = model_library.get_model(mid)
    m["history"] = analytics.portfolio_metrics(m["holdings"])
    return m


@app.post("/api/models")
def model_create(body: ModelIn):
    return model_library.save_model(body.name, body.holdings, body.category, body.description)


@app.put("/api/models/{mid}")
def model_update(mid: int, body: ModelIn):
    return model_library.save_model(body.name, body.holdings, body.category, body.description, mid)


@app.delete("/api/models/{mid}")
def model_delete(mid: int):
    model_library.delete_model(mid)
    return {"ok": True}


@app.post("/api/models/{mid}/duplicate")
def model_duplicate(mid: int):
    return model_library.duplicate(mid)


@app.post("/api/models/{mid}/push")
def model_push(mid: int):
    return model_library.push(mid)


@app.get("/api/models/{mid}/rebalance")
def model_rebalance_preview(mid: int, mode: str = "full"):
    return model_library.bulk_rebalance(mid, False, mode)


@app.post("/api/models/{mid}/rebalance")
def model_rebalance(mid: int, body: RebalanceIn):
    return model_library.bulk_rebalance(mid, True, body.mode)


@app.post("/api/clients/{cid}/model/{mid}")
def model_assign(cid: int, mid: int):
    return model_library.assign(cid, mid)


# ------------------------------------------------------------------ financial planning
@app.get("/api/clients/{cid}/plan")
def client_plan(cid: int):
    return planning.plan(cid)


@app.post("/api/clients/{cid}/goals")
def goal_create(cid: int, body: GoalIn):
    return planning.save_goal(cid, **body.model_dump())


@app.put("/api/clients/{cid}/goals/{gid}")
def goal_update(cid: int, gid: int, body: GoalIn):
    return planning.save_goal(cid, goal_id=gid, **body.model_dump())


@app.delete("/api/clients/{cid}/goals/{gid}")
def goal_delete(cid: int, gid: int):
    planning.delete_goal(cid, gid)
    return {"ok": True}


# ------------------------------------------------------------------ reports (printable HTML -> PDF)
@app.get("/report/ips/{cid}", response_class=HTMLResponse)
def report_ips(cid: int):
    from . import reports
    return reports.ips(cid)


@app.get("/report/statement/{cid}", response_class=HTMLResponse)
def report_statement(cid: int, start: str | None = None, end: str | None = None):
    from . import reports
    return reports.statement(cid, _date.fromisoformat(start) if start else None, _date.fromisoformat(end) if end else None)


# ------------------------------------------------------------------ direct indexing
@app.get("/api/clients/{cid}/direct-index")
def di_status(cid: int):
    return direct_index.status(cid)


@app.post("/api/clients/{cid}/direct-index/preview")
def di_preview(cid: int, body: DirectIndexIn):
    return direct_index.preview(cid, body.sleeve_weight, body.top_n, body.excluded_sectors, body.excluded_symbols)


@app.put("/api/clients/{cid}/direct-index")
def di_apply(cid: int, body: DirectIndexIn):
    return direct_index.apply(cid, body.sleeve_weight, body.top_n, body.excluded_sectors, body.excluded_symbols)


@app.delete("/api/clients/{cid}/direct-index")
def di_remove(cid: int):
    return direct_index.remove(cid)


# ------------------------------------------------------------------ lending
@app.get("/api/clients/{cid}/lending")
def lending_all(cid: int):
    billing.accrue_interest(cid)
    return {"margin": lending.margin_status(cid), "sbloc": lending.sbloc_status(cid), "sec_lending": lending.lending_status(cid)}


@app.post("/api/clients/{cid}/margin")
def margin_toggle(cid: int, body: ToggleIn):
    return lending.set_margin(cid, body.enabled, body.rate)


@app.post("/api/clients/{cid}/sbloc/draw")
def sbloc_draw(cid: int, body: AmountIn):
    return lending.sbloc_draw(cid, body.amount)


@app.post("/api/clients/{cid}/sbloc/repay")
def sbloc_repay(cid: int, body: AmountIn):
    return lending.sbloc_repay(cid, body.amount, body.from_cash)


@app.post("/api/clients/{cid}/sec-lending")
def sec_lending_toggle(cid: int, body: ToggleIn):
    return lending.set_lending(cid, body.enabled)


# ------------------------------------------------------------------ options
@app.get("/api/options/chain/{symbol}")
def option_chain(symbol: str, expiry: str | None = None):
    return options.chain(symbol, expiry)


@app.get("/api/clients/{cid}/options")
def client_options(cid: int):
    c = portfolio.get_client(cid)
    options.process_expirations(cid)
    return {"level": c.get("options_level") or 0, "levels": accounts.OPTIONS_LEVELS, "positions": options.positions(cid),
            "trades": options.trades(cid), "csp_reserve": options.csp_reserve(cid),
            "buying_power": lending.buying_power(portfolio.get_client(cid)) - options.csp_reserve(cid)}


@app.post("/api/clients/{cid}/options/order")
def option_order(cid: int, body: OptionOrderIn):
    return options.place(cid, body.underlying, body.type, body.strike, body.expiry, body.action, body.qty)


@app.post("/api/clients/{cid}/options/level")
def option_level(cid: int, body: LevelIn):
    return options.set_level(cid, body.level)


# ------------------------------------------------------------------ AI assistant
@app.get("/api/assistant/settings")
def assistant_settings():
    return assistant.public_settings()


@app.put("/api/assistant/settings")
def assistant_save(body: AssistantSettingsIn):
    return assistant.save_settings(body.model_dump())


@app.post("/api/assistant/chat")
def assistant_chat(body: ChatIn):
    return assistant.chat(body.message, body.conversation_id, body.client_id)


@app.post("/api/assistant/decision")
def assistant_decision(body: DecisionIn):
    return assistant.decide(body.conversation_id, body.approve)


@app.delete("/api/assistant/{conversation_id}")
def assistant_reset(conversation_id: str):
    assistant.reset(conversation_id)
    return {"ok": True}


# ------------------------------------------------------------------ research & market data
@app.get("/api/research/{symbol}")
def research_symbol(symbol: str, client_id: int | None = None):
    return research.analyze(symbol, client_id)


@app.get("/api/quote/{symbol}")
def quote(symbol: str):
    return get_provider().quote(symbol)


@app.get("/api/search")
def search(q: str):
    q = q.strip()
    extra = [{"symbol": s, "name": n, "exchange": markets.KIND.get(s, "")} for s, n in markets.NAMES.items()
             if q.upper() in s or q.lower() in n.lower()][:5] if q else []
    try:
        found = get_provider().search(q) if q else []
    except DataError:
        found = []
    seen = {e["symbol"] for e in extra}
    return extra + [f for f in found if f.get("symbol") not in seen][:10]


# ------------------------------------------------------------------ markets & watchlists
class WatchlistIn(BaseModel):
    name: str


class SymbolIn(BaseModel):
    symbol: str


@app.get("/api/news")
def news_desk(section: str = "top"):
    if section != "top" and section not in news.SECTIONS:
        raise HTTPException(400, f"Unknown news section '{section}'")
    return news.desk(section)


@app.get("/api/news/ticker/{symbol}")
def news_ticker(symbol: str):
    try:
        return {"symbol": symbol.upper(), "items": news.ticker(symbol)}
    except news.NewsError as e:
        return {"symbol": symbol.upper(), "items": [], "error": str(e)}


@app.get("/api/news/watchlist")
def news_watchlist(watchlist_id: int | None = None):
    lists = markets.list_watchlists()
    wid = watchlist_id or (lists[0]["id"] if lists else None)
    syms = [s for s in (markets.symbols(wid) if wid else []) if not s.startswith("^")]
    return {"symbols": syms, "items": news.watchlist_news(syms)}


@app.get("/api/briefing")
def get_briefing():
    return briefing.build()


@app.get("/api/markets")
def markets_board():
    return markets.board()


@app.get("/api/markets/{symbol}/candles")
def markets_candles(symbol: str, interval: str = "1d"):
    return markets.candles(symbol, interval)


@app.get("/api/markets/{symbol}")
def markets_detail(symbol: str, range: str = "1Y"):
    return markets.detail(symbol, range)


@app.get("/api/watchlists")
def watchlists():
    return markets.list_watchlists()


@app.post("/api/watchlists")
def watchlist_create(body: WatchlistIn):
    return markets.create(body.name)


@app.get("/api/watchlists/{wid}")
def watchlist_view(wid: int, range: str = "1M"):
    return markets.view(wid, range)


@app.patch("/api/watchlists/{wid}")
def watchlist_rename(wid: int, body: WatchlistIn):
    return markets.rename(wid, body.name)


@app.delete("/api/watchlists/{wid}")
def watchlist_delete(wid: int):
    markets.delete(wid)
    return {"deleted": wid}


@app.post("/api/watchlists/{wid}/symbols")
def watchlist_add(wid: int, body: SymbolIn):
    return markets.add_symbol(wid, body.symbol)


@app.delete("/api/watchlists/{wid}/symbols/{symbol}")
def watchlist_remove(wid: int, symbol: str):
    markets.remove_symbol(wid, symbol)
    return {"removed": symbol.upper()}
