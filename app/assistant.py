"""In-app AI assistant (Claude or ChatGPT) that can use the Portfolio Manager tools.

- Tools are the same ones exposed over MCP (app/mcp_server.py), so both stay in sync.
- Read-only tools run automatically. Write tools (orders, rebalances, new clients) pause and
  wait for the user to press Approve in the UI.
- API keys are stored only on this computer (data/assistant.json) and never sent to the browser.
"""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import date
from pathlib import Path

import requests

from . import config, portfolio
from .mcp_server import build_server

SETTINGS_FILE = config.ROOT / "data" / "assistant.json"
DEFAULTS = {"provider": "claude", "model_claude": "claude-sonnet-5", "model_openai": "gpt-6-luna",
            "claude_key": "", "openai_key": ""}
MODEL_SUGGESTIONS = {
    "claude": ["claude-sonnet-5", "claude-opus-5-5", "claude-haiku-4-5-20251001"],
    "openai": ["gpt-6-luna", "gpt-6-astra"],
}
WRITE_TOOLS = {"place_paper_order", "cancel_order", "execute_rebalance", "create_client", "harvest_tax_loss",
               "assign_model", "add_goal", "run_billing", "place_option_order", "apply_direct_index"}
MAX_STEPS = 10
MAX_TOOL_CHARS = 20000

_lock = threading.RLock()
_server = build_server()
_tools_cache: list[dict] | None = None
_conversations: dict[str, dict] = {}


class AssistantError(Exception):
    pass


# ------------------------------------------------------------------ settings
def load_settings() -> dict:
    s = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    import os
    s["claude_key"] = s["claude_key"] or os.environ.get("ANTHROPIC_API_KEY", "")
    s["openai_key"] = s["openai_key"] or os.environ.get("OPENAI_API_KEY", "")
    return s


def public_settings() -> dict:
    s = load_settings()
    mask = lambda k: ("••••" + k[-4:]) if k else ""
    return {"provider": s["provider"], "model_claude": s["model_claude"], "model_openai": s["model_openai"],
            "has_claude_key": bool(s["claude_key"]), "has_openai_key": bool(s["openai_key"]),
            "claude_key_hint": mask(s["claude_key"]), "openai_key_hint": mask(s["openai_key"]),
            "model_suggestions": MODEL_SUGGESTIONS}


def save_settings(changes: dict) -> dict:
    s = load_settings()
    for k in ("provider", "model_claude", "model_openai"):
        if changes.get(k):
            s[k] = str(changes[k]).strip()
    if s["provider"] not in ("claude", "openai"):
        raise ValueError("provider must be 'claude' or 'openai'")
    for k in ("claude_key", "openai_key"):
        if k in changes and changes[k] is not None:        # "" removes the key
            s[k] = str(changes[k]).strip()
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
    return public_settings()


# ------------------------------------------------------------------ tools
def _run(coro):
    return asyncio.run(coro)


def tool_specs() -> list[dict]:
    global _tools_cache
    if _tools_cache is None:
        _tools_cache = [{"name": t.name, "description": t.description or "", "schema": t.inputSchema}
                        for t in _run(_server.list_tools())]
    return _tools_cache


def execute_tool(name: str, args: dict) -> tuple[str, bool]:
    """Returns (result_text, is_error)."""
    try:
        res = _run(_server.call_tool(name, args or {}))
        blocks = res[0] if isinstance(res, tuple) else res
        text = "\n".join(getattr(b, "text", "") for b in blocks) or "{}"
        return (text[:MAX_TOOL_CHARS] + ("\n...[truncated]" if len(text) > MAX_TOOL_CHARS else "")), False
    except Exception as e:  # tool errors go back to the model so it can explain / recover
        return f"Error: {e}", True


def describe_action(name: str, a: dict) -> str:
    who = f"client #{a.get('client_id')}"
    try:
        if a.get("client_id"):
            who = portfolio.get_client(int(a["client_id"]))["name"]
    except Exception:
        pass
    if name == "place_paper_order":
        size = f"{a.get('quantity')} shares" if a.get("quantity") else f"${a.get('dollar_amount'):,}" if a.get("dollar_amount") else "?"
        px = f" @ limit {a['limit_price']}" if a.get("limit_price") else f" stop {a['stop_price']}" if a.get("stop_price") else ""
        return f"{str(a.get('side', '')).upper()} {size} of {str(a.get('symbol', '')).upper()} ({a.get('order_type', 'market')}{px}) for {who}"
    if name == "execute_rebalance":
        return f"Execute {a.get('mode', 'full')} rebalance for {who}"
    if name == "cancel_order":
        return f"Cancel order #{a.get('order_id')} for {who}"
    if name == "place_option_order":
        acts = {"BTO": "Buy to open", "STC": "Sell to close", "STO": "Sell to open", "BTC": "Buy to close"}
        return (f"{acts.get(str(a.get('action', '')).upper(), a.get('action'))} {a.get('contracts', 1)} "
                f"{str(a.get('underlying', '')).upper()} {a.get('expiry')} {a.get('strike')}{str(a.get('option_type', '')).upper()[:1]} for {who}")
    if name == "apply_direct_index":
        return f"Set up a {float(a.get('sleeve_weight', 0)):.0%} direct-index sleeve (top {a.get('top_n', 50)}) for {who}"
    if name == "harvest_tax_loss":
        return f"Tax-loss harvest {str(a.get('symbol', '')).upper()} for {who} (sell losing lots, buy {a.get('replacement') or 'suggested replacement'})"
    if name == "assign_model":
        try:
            from . import model_library
            mname = model_library.get_model(int(a["model_id"]))["name"]
        except Exception:
            mname = f"model #{a.get('model_id')}"
        return f"Assign '{mname}' to {who} (replaces target weights)"
    if name == "add_goal":
        return f"Add goal '{a.get('name')}': ${a.get('target_amount', 0):,.0f} by {a.get('target_date')} for {who}"
    if name == "run_billing":
        return f"Bill advisory fees for all clients ({a.get('period_start') or 'quarter start'} to {a.get('period_end') or 'today'})"
    if name == "create_client":
        return f"Create client '{a.get('name')}' with ${a.get('starting_cash', 100000):,} paper cash"
    return f"{name}({json.dumps(a)})"


def system_prompt(client_id: int | None) -> str:
    ctx = ""
    if client_id:
        try:
            c = portfolio.get_client(client_id)
            ctx = f"\nThe user currently has client #{client_id} ({c['name']}, {c['risk_profile']}) open; assume questions are about this client unless stated otherwise."
        except KeyError:
            pass
    mode = {"fmp": "LIVE market data from Financial Modeling Prep",
            "live": "LIVE prices from Yahoo Finance (may be ~15 min delayed)" + (
                " and company financials from Financial Modeling Prep (Yahoo Finance as backup)" if config.FMP_API_KEY else
                " and company financials, analyst estimates and price targets from Yahoo Finance"),
            }.get(config.DATA_MODE, "SIMULATED market data - mention that figures are simulated when relevant")
    return f"""You are the AI assistant built into Don's Portfolio Manager, a paper-trading portfolio management platform.
Today is {date.today():%A, %B %d, %Y}. The app is using {mode}.{ctx}

How to work:
- Use the tools to get real numbers from the app; never invent prices, holdings or ratings.
- Use list_clients to resolve client names to client_id.
- Read-only tools run immediately. Write tools (orders, rebalances, harvesting, model assignment, goals, billing, new clients) are shown to the user for approval first - before calling one, say in one line what you are about to do. For rebalances, call preview_rebalance and summarise the trades before execute_rebalance.
- All trading is simulated paper money. Frame research as analysis, not personal investment advice.
- Be concise and specific: lead with the answer, then key numbers. Use short bullet points or small markdown tables. Format money like $1,234 and percentages like 12.3%."""


# ------------------------------------------------------------------ providers
def _post(url: str, headers: dict, body: dict) -> dict:
    try:
        r = requests.post(url, headers=headers, json=body, timeout=180)
    except requests.RequestException as e:
        raise AssistantError(f"Could not reach the AI service: {e}") from e
    if r.status_code == 401 or r.status_code == 403:
        raise AssistantError("The API key was rejected. Check it in AI Assistant > Settings.")
    if r.status_code == 404:
        raise AssistantError(f"Model not found - check the model name in Settings. ({r.text[:200]})")
    if r.status_code == 429:
        raise AssistantError("Rate limit or credit limit reached. Check your API billing/credits, then try again.")
    if r.status_code >= 400:
        raise AssistantError(f"AI service error {r.status_code}: {r.text[:300]}")
    return r.json()


class ClaudeProvider:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, key, model):
        self.key, self.model = key, model

    def add_user(self, conv, text):
        conv["messages"].append({"role": "user", "content": text})

    def step(self, conv):
        body = {"model": self.model, "max_tokens": 4096, "system": conv["system"], "messages": conv["messages"],
                "tools": [{"name": t["name"], "description": t["description"], "input_schema": t["schema"]} for t in tool_specs()]}
        data = _post(self.URL, {"x-api-key": self.key, "anthropic-version": "2023-06-01",
                                "content-type": "application/json"}, body)
        content = data.get("content", [])
        conv["messages"].append({"role": "assistant", "content": content})
        texts = [b["text"] for b in content if b.get("type") == "text" and b.get("text", "").strip()]
        calls = [{"id": b["id"], "name": b["name"], "args": b.get("input") or {}} for b in content if b.get("type") == "tool_use"]
        return texts, calls

    def add_results(self, conv, results):
        conv["messages"].append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": cid, "content": text, "is_error": err} for cid, text, err in results]})


class OpenAIProvider:
    URL = "https://api.openai.com/v1/responses"

    def __init__(self, key, model):
        self.key, self.model = key, model

    def add_user(self, conv, text):
        conv["messages"].append({"role": "user", "content": text})

    def step(self, conv):
        body = {"model": self.model, "instructions": conv["system"], "input": conv["messages"],
                "tools": [{"type": "function", "name": t["name"], "description": t["description"],
                           "parameters": t["schema"]} for t in tool_specs()]}
        data = _post(self.URL, {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}, body)
        output = data.get("output", [])
        conv["messages"].extend(output)          # includes reasoning items, which must be passed back
        texts, calls = [], []
        for item in output:
            if item.get("type") == "message":
                texts += [p["text"] for p in item.get("content", []) if p.get("type") in ("output_text", "text") and p.get("text", "").strip()]
            elif item.get("type") == "function_call":
                try:
                    args = json.loads(item.get("arguments") or "{}")
                except ValueError:
                    args = {}
                calls.append({"id": item["call_id"], "name": item["name"], "args": args})
        return texts, calls

    def add_results(self, conv, results):
        for cid, text, _err in results:
            conv["messages"].append({"type": "function_call_output", "call_id": cid, "output": text})


def _provider(conv):
    s = load_settings()
    p = conv["provider"]
    key = s["claude_key"] if p == "claude" else s["openai_key"]
    if not key:
        raise AssistantError(f"No {'Claude (Anthropic)' if p == 'claude' else 'OpenAI'} API key yet. Add one in AI Assistant > Settings.")
    return ClaudeProvider(key, s["model_claude"]) if p == "claude" else OpenAIProvider(key, s["model_openai"])


# ------------------------------------------------------------------ conversation loop
def _new_conversation(client_id):
    s = load_settings()
    cid = uuid.uuid4().hex[:12]
    _conversations[cid] = {"id": cid, "provider": s["provider"], "client_id": client_id,
                           "system": system_prompt(client_id), "messages": [], "pending": None}
    return _conversations[cid]


def _drive(conv, events, prov):
    """Process queued tool calls, then keep stepping the model until it answers or needs approval."""
    steps = 0
    while True:
        pend = conv["pending"]
        if pend:
            while pend["index"] < len(pend["calls"]):
                call = pend["calls"][pend["index"]]
                if call["name"] in WRITE_TOOLS and not call.get("decision"):
                    events.append({"type": "approval", "tool": call["name"], "args": call["args"],
                                   "description": describe_action(call["name"], call["args"])})
                    return events, True
                if call.get("decision") == "declined":
                    text, err = "The user declined this action. Do not retry it unless they ask.", True
                    events.append({"type": "tool", "tool": call["name"], "args": call["args"], "status": "declined"})
                else:
                    text, err = execute_tool(call["name"], call["args"])
                    events.append({"type": "tool", "tool": call["name"], "args": call["args"],
                                   "status": "error" if err else ("approved" if call.get("decision") else "ok")})
                pend["results"].append((call["id"], text, err))
                pend["index"] += 1
            prov.add_results(conv, pend["results"])
            conv["pending"] = None
        if steps >= MAX_STEPS:
            events.append({"type": "text", "text": "_(Stopped after several tool calls - ask me to continue if needed.)_"})
            return events, False
        texts, calls = prov.step(conv)
        steps += 1
        events += [{"type": "text", "text": t} for t in texts]
        if not calls:
            return events, False
        conv["pending"] = {"calls": calls, "index": 0, "results": []}


def chat(message: str, conversation_id: str | None = None, client_id: int | None = None) -> dict:
    with _lock:
        conv = _conversations.get(conversation_id or "")
        if conv is None or conv["provider"] != load_settings()["provider"]:
            conv = _new_conversation(client_id)
        if conv["pending"]:
            raise AssistantError("Please approve or decline the pending action first.")
        prov = _provider(conv)
        prov.add_user(conv, message)
        events, waiting = _drive(conv, [], prov)
        return {"conversation_id": conv["id"], "events": events, "awaiting_approval": waiting,
                "provider": conv["provider"]}


def decide(conversation_id: str, approve: bool) -> dict:
    with _lock:
        conv = _conversations.get(conversation_id)
        if not conv or not conv["pending"]:
            raise AssistantError("Nothing is waiting for approval.")
        pend = conv["pending"]
        pend["calls"][pend["index"]]["decision"] = "approved" if approve else "declined"
        events, waiting = _drive(conv, [], _provider(conv))
        return {"conversation_id": conv["id"], "events": events, "awaiting_approval": waiting,
                "provider": conv["provider"]}


def reset(conversation_id: str) -> None:
    with _lock:
        _conversations.pop(conversation_id, None)
