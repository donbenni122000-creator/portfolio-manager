"""Markets home page (world indices, commodities, crypto, currencies, rates) and watchlists.

Everything is read through the market-data provider, so it shows live FMP data when a key is set and the
simulated market otherwise. Symbols your FMP plan does not cover are returned as unavailable rather than
filled with made-up numbers. The board is memoised briefly so auto-refresh doesn't burn API calls.
"""
from __future__ import annotations

import math
import threading
from concurrent.futures import ThreadPoolExecutor
import time
from datetime import date, timedelta

import pandas as pd

from . import config, db
from .market_data import DataError, get_provider

GROUPS = [
    {"key": "us", "title": "United States", "items": [
        ("^GSPC", "S&P 500"), ("^DJI", "Dow Jones"), ("^IXIC", "Nasdaq Composite"), ("^NDX", "Nasdaq 100"),
        ("^RUT", "Russell 2000"), ("^VIX", "VIX (volatility)")]},
    {"key": "india", "title": "India", "items": [("^NSEI", "Nifty 50"), ("^BSESN", "Sensex"), ("^NSEBANK", "Nifty Bank")]},
    {"key": "europe", "title": "Europe", "items": [
        ("^FTSE", "FTSE 100"), ("^GDAXI", "DAX"), ("^FCHI", "CAC 40"), ("^STOXX50E", "Euro Stoxx 50")]},
    {"key": "asia", "title": "Asia-Pacific", "items": [
        ("^N225", "Nikkei 225"), ("^HSI", "Hang Seng"), ("000001.SS", "Shanghai Composite"), ("^AXJO", "ASX 200")]},
    {"key": "commodities", "title": "Commodities", "items": [
        ("GCUSD", "Gold"), ("SIUSD", "Silver"), ("PLUSD", "Platinum"), ("CLUSD", "Crude oil (WTI)"),
        ("BZUSD", "Brent crude"), ("NGUSD", "Natural gas"), ("HGUSD", "Copper")]},
    {"key": "crypto", "title": "Crypto", "items": [("BTCUSD", "Bitcoin"), ("ETHUSD", "Ethereum"), ("SOLUSD", "Solana")]},
    {"key": "fx", "title": "Currencies & rates", "items": [
        ("EURUSD", "EUR / USD"), ("GBPUSD", "GBP / USD"), ("USDJPY", "USD / JPY"), ("USDINR", "USD / INR"),
        ("^TNX", "US 10-yr yield")]},
]
TAPE = ["^GSPC", "^DJI", "^IXIC", "^RUT", "^NSEI", "^BSESN", "^FTSE", "^N225", "GCUSD", "CLUSD", "BTCUSD", "EURUSD", "^TNX"]
NAMES = {s: n for g in GROUPS for s, n in g["items"]}
KIND = {}
for g in GROUPS:
    for s, _ in g["items"]:
        KIND[s] = {"commodities": "Commodity", "crypto": "Crypto"}.get(g["key"], "Rate" if s == "^TNX" else
                                                                         "Currency" if g["key"] == "fx" else "Index")
RANGES = {"1M": 31, "3M": 92, "6M": 183, "1Y": 366, "3Y": 1096, "5Y": 1827}
COMPARE = [("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq"), ("^NSEI", "Nifty 50")]

_memo: dict = {}
_lock = threading.Lock()


def _ttl() -> float:
    return {"sim": 20.0, "live": 60.0}.get(config.DATA_MODE, 120.0)


def _memoised(key, fn):
    with _lock:
        hit = _memo.get(key)
        if hit and time.time() - hit[0] < _ttl():
            return hit[1]
    val = fn()
    with _lock:
        _memo[key] = (time.time(), val)
    return val


def _range_days(rng: str) -> int:
    rng = (rng or "1M").upper()
    if rng == "YTD":
        return max(7, (date.today() - date(date.today().year, 1, 1)).days + 1)
    return RANGES.get(rng, 31)


def _hist(symbol: str, days: int) -> pd.Series | None:
    try:
        h = get_provider().history(symbol, days)
        return h if len(h) else None
    except (DataError, Exception):
        return None


def _ret(h: pd.Series, days: int):
    if h is None or len(h) < 2:
        return None
    cutoff = h.index[-1] - pd.Timedelta(days=days)
    base = h[h.index <= cutoff]
    if not len(base):
        return None
    return float(h.iloc[-1] / base.iloc[-1] - 1)


def _ytd(h: pd.Series):
    if h is None or not len(h):
        return None
    prev = h[h.index < pd.Timestamp(date(date.today().year, 1, 1))]
    return float(h.iloc[-1] / prev.iloc[-1] - 1) if len(prev) else None


def _spark(h: pd.Series | None, n: int = 30) -> list[float]:
    if h is None:
        return []
    return [round(float(x), 4) for x in h.iloc[-n:].tolist()]


def _row(symbol: str, q: dict | None, name: str | None = None, kind: str | None = None) -> dict:
    """Quote + 1M/YTD/1Y returns + sparkline for one symbol (None fields if unavailable)."""
    h = _hist(symbol, 400)
    if q is None:
        return {"symbol": symbol, "name": name or NAMES.get(symbol, symbol), "kind": kind or KIND.get(symbol),
                "available": False}
    price = q.get("price")
    # when the provider's last close lags the quote, add today's price to the sparkline
    spark = _spark(h)
    if price and spark and abs(spark[-1] - price) / max(abs(price), 1e-9) > 1e-6:
        spark = spark[1:] + [price]
    return {
        "symbol": symbol, "name": name or NAMES.get(symbol) or q.get("name") or symbol,
        "kind": kind or KIND.get(symbol), "available": price is not None,
        "price": price, "change": q.get("change") if q.get("change") is not None else
        (price - q["prev_close"] if price is not None and q.get("prev_close") else None),
        "change_pct": (q.get("change_pct") or 0) / 100, "prev_close": q.get("prev_close"),
        "day_low": q.get("day_low"), "day_high": q.get("day_high"), "open": q.get("open"),
        "year_low": min(x for x in (q.get("year_low"), price) if x is not None) if price is not None else q.get("year_low"),
        "year_high": max(x for x in (q.get("year_high"), price) if x is not None) if price is not None else q.get("year_high"),
        "volume": q.get("volume"),
        "market_cap": q.get("market_cap"),
        "r1m": _ret(h, 30), "ytd": _ytd(h), "r1y": _ret(h, 365), "spark": spark,
    }


def _quotes(symbols: list[str]) -> dict[str, dict]:
    p = get_provider()
    out = {}
    for s in symbols:
        try:
            out[s] = p.quote(s)
        except (DataError, Exception):
            out[s] = None
    return out


def _pmap(fn, items: list) -> list:
    """Fetch in parallel - live data means one HTTP call per symbol, so this keeps pages fast."""
    if len(items) <= 1 or config.DATA_MODE == "sim":
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(fn, items))


# ------------------------------------------------------------------ markets board
def board() -> dict:
    def build():
        items = [(s, n) for g in GROUPS for s, n in g["items"]]
        built = dict(zip([s for s, _ in items], _pmap(lambda it: _row(it[0], _quotes([it[0]])[it[0]], it[1]), items)))
        groups = [{"key": g["key"], "title": g["title"], "rows": [built[s] for s, _ in g["items"]]} for g in GROUPS]
        rows = {r["symbol"]: r for g in groups for r in g["rows"]}
        tape = [rows[s] for s in TAPE if s in rows]
        movers = sorted([r for r in rows.values() if r.get("available") and r["kind"] != "Rate"],
                        key=lambda r: -abs(r["change_pct"] or 0))[:5]
        avail = sum(1 for r in rows.values() if r.get("available"))
        return {"groups": groups, "tape": tape, "movers": movers, "data_mode": config.DATA_MODE,
                "available": avail, "total": len(rows), "as_of": db.now_iso()}
    return _memoised("board", build)


def detail(symbol: str, rng: str = "1Y") -> dict:
    symbol = symbol.upper()
    q = _quotes([symbol])[symbol]
    h = _hist(symbol, _range_days(rng) + 5)
    cutoff = pd.Timestamp(date.today() - timedelta(days=_range_days(rng)))
    series = [] if h is None else [{"x": d.date().isoformat(), "y": round(float(v), 4)} for d, v in h[h.index >= cutoff].items()]
    row = _row(symbol, q)
    ret = (series[-1]["y"] / series[0]["y"] - 1) if len(series) > 1 else None
    return {**row, "range": rng.upper(), "series": series, "range_return": ret}


# ------------------------------------------------------------------ watchlists
DEFAULT_SYMBOLS = ["^GSPC", "^NSEI", "AAPL", "MSFT", "NVDA", "GCUSD", "BTCUSD"]


def seed() -> None:
    if not db.query_one("SELECT id FROM watchlists LIMIT 1"):
        wid = create("My watchlist")["id"]
        for s in DEFAULT_SYMBOLS:
            add_symbol(wid, s)


def list_watchlists() -> list[dict]:
    return db.query("""SELECT w.id, w.name, w.created_at, COUNT(i.symbol) AS count FROM watchlists w
                       LEFT JOIN watchlist_items i ON i.watchlist_id = w.id GROUP BY w.id ORDER BY w.id""")


def create(name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("Give the watchlist a name")
    with db.tx() as c:
        wid = c.execute("INSERT INTO watchlists(name, created_at) VALUES(?,?)", (name[:60], db.now_iso())).lastrowid
    return {"id": wid, "name": name[:60]}


def rename(wid: int, name: str) -> dict:
    _get(wid)
    if not (name or "").strip():
        raise ValueError("Give the watchlist a name")
    with db.tx() as c:
        c.execute("UPDATE watchlists SET name=? WHERE id=?", (name.strip()[:60], wid))
    return _get(wid)


def delete(wid: int) -> None:
    _get(wid)
    if len(list_watchlists()) <= 1:
        raise ValueError("Keep at least one watchlist")
    with db.tx() as c:
        c.execute("DELETE FROM watchlist_items WHERE watchlist_id=?", (wid,))
        c.execute("DELETE FROM watchlists WHERE id=?", (wid,))


def _get(wid: int) -> dict:
    w = db.query_one("SELECT * FROM watchlists WHERE id=?", (wid,))
    if not w:
        raise KeyError(f"Watchlist {wid} not found")
    return w


def symbols(wid: int) -> list[str]:
    return [r["symbol"] for r in db.query("SELECT symbol FROM watchlist_items WHERE watchlist_id=? ORDER BY position, added_at", (wid,))]


def add_symbol(wid: int, symbol: str) -> dict:
    _get(wid)
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("Enter a symbol")
    if symbol in symbols(wid):
        raise ValueError(f"{symbol} is already on this watchlist")
    try:
        q = get_provider().quote(symbol)
    except DataError as e:
        raise ValueError(f"Couldn't find a price for {symbol}: {e}")
    if q.get("price") is None:
        raise ValueError(f"No price available for {symbol}")
    with db.tx() as c:
        pos = c.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM watchlist_items WHERE watchlist_id=?", (wid,)).fetchone()[0]
        c.execute("INSERT INTO watchlist_items(watchlist_id, symbol, position, added_at) VALUES(?,?,?,?)",
                  (wid, symbol, pos, db.now_iso()))
    _memo.pop(("wl", wid), None)
    return {"watchlist_id": wid, "symbol": symbol, "name": q.get("name")}


def remove_symbol(wid: int, symbol: str) -> None:
    _get(wid)
    with db.tx() as c:
        c.execute("DELETE FROM watchlist_items WHERE watchlist_id=? AND symbol=?", (wid, symbol.upper()))


def _kind(symbol: str, profile: dict | None, q: dict | None = None) -> str:
    if symbol in KIND:
        return KIND[symbol]
    if q and q.get("kind"):
        return q["kind"]
    if profile and profile.get("is_etf"):
        return "ETF"
    return "Stock"


def _rating(symbol: str) -> dict | None:
    """Our model's last Buy/Hold/Sell call for this symbol (from Stock research), if it has been analysed."""
    r = db.query_one("SELECT rating, fair_value, updated_at FROM research_cache WHERE symbol=?", (symbol,))
    return dict(r) if r and r.get("rating") else None


def view(wid: int, rng: str = "1M") -> dict:
    w = _get(wid)
    syms = symbols(wid)
    p = get_provider()

    def one(s):
        q = _quotes([s])[s]
        prof = None
        if s not in KIND:
            try:
                prof = p.profile(s)
            except (DataError, Exception):
                prof = None
        r = _row(s, q, name=(prof or {}).get("name"), kind=_kind(s, prof, q))
        if prof:
            r["beta"] = prof.get("beta")
            r["sector"] = prof.get("sector")
            if r.get("market_cap") is None:
                r["market_cap"] = prof.get("market_cap")
        r["rating"] = _rating(s)
        return r

    rows = _pmap(one, syms)

    # equal-weight performance of the list over the range, vs benchmarks
    days = _range_days(rng)
    perf = _equal_weight([r["symbol"] for r in rows if r.get("available")], days)
    compare = []
    for sym, label in COMPARE:
        b = _equal_weight([sym], days)
        if perf["series"] and b["series"]:
            compare.append({"symbol": sym, "name": label, "return": b["return"], "diff": perf["return"] - b["return"]})
    avail = [r for r in rows if r.get("available")]
    mix: dict[str, int] = {}
    for r in rows:
        mix[r["kind"] or "Other"] = mix.get(r["kind"] or "Other", 0) + 1
    return {
        "id": w["id"], "name": w["name"], "range": rng.upper(), "rows": rows,
        "day_return": (sum(r["change_pct"] for r in avail) / len(avail)) if avail else None,
        "year_return": _avg([r["r1y"] for r in avail]),
        "performance": perf, "compare": compare,
        "mix": [{"kind": k, "count": v, "weight": v / len(rows)} for k, v in sorted(mix.items(), key=lambda kv: -kv[1])] if rows else [],
        "gainers": sum(1 for r in avail if r["change_pct"] > 0), "losers": sum(1 for r in avail if r["change_pct"] < 0),
    }


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _equal_weight(syms: list[str], days: int) -> dict:
    series = {}
    for s in syms:
        h = _hist(s, days + 5)
        if h is not None:
            h = h[h.index >= pd.Timestamp(date.today() - timedelta(days=days))]
            if len(h) > 1:
                series[s] = h
    if not series:
        return {"series": [], "return": None}
    df = pd.DataFrame(series).sort_index().ffill().dropna(how="all")
    df = df.bfill()
    norm = df / df.iloc[0]
    ew = norm.mean(axis=1) - 1
    # thin long series to ~150 points
    step = max(1, math.ceil(len(ew) / 150))
    pts = ew.iloc[::step]
    if pts.index[-1] != ew.index[-1]:
        pts = pd.concat([pts, ew.iloc[-1:]])
    return {"series": [{"x": d.date().isoformat(), "y": round(float(v), 5)} for d, v in pts.items()],
            "return": float(ew.iloc[-1])}


# ------------------------------------------------------------------ OHLC candles for the pro chart
def candles(symbol: str, interval: str = "1d") -> dict:
    from .market_data import CANDLE_INTERVALS, INTRADAY
    symbol = symbol.upper()
    if interval not in CANDLE_INTERVALS:
        raise ValueError(f"interval must be one of {', '.join(CANDLE_INTERVALS)}")
    p = get_provider()
    try:
        rows = p.candles(symbol, interval)
    except DataError as e:
        raise ValueError(f"No {interval} price data for {symbol}: {e}")
    q = _quotes([symbol])[symbol] or {}
    return {"symbol": symbol, "interval": interval, "intraday": interval in INTRADAY,
            "name": NAMES.get(symbol) or q.get("name") or symbol, "kind": KIND.get(symbol) or q.get("kind"),
            "price": q.get("price"), "change": q.get("change"), "change_pct": (q.get("change_pct") or 0) / 100,
            "prev_close": q.get("prev_close"), "currency": q.get("currency"), "exchange": q.get("exchange"),
            "year_high": q.get("year_high"), "year_low": q.get("year_low"), "day_high": q.get("day_high"),
            "day_low": q.get("day_low"), "volume": q.get("volume"),
            "candles": [{k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()} for r in rows]}
