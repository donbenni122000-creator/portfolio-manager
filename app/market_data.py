"""Market data providers.

FMPProvider  - live data from Financial Modeling Prep (https://site.financialmodelingprep.com), "stable" API.
SimProvider  - deterministic simulated market used when no API key is set (demo / offline / tests).

Both return the same normalized shapes so the rest of the app never cares which one is active.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

import numpy as np
import pandas as pd
import requests

from . import config, db


class DataError(Exception):
    pass


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
def _num(d: dict, *keys, default=None):
    """Return the first present, non-null numeric value among keys."""
    for k in keys:
        v = d.get(k) if d else None
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return default


def _raw(v):
    """Yahoo wraps numbers as {"raw": 1.2, "fmt": "1.2"}."""
    if isinstance(v, dict):
        v = v.get("raw")
    return float(v) if isinstance(v, (int, float)) else None


def _cache_get(key: str, ttl: float):
    row = db.query_one("SELECT value, ts FROM http_cache WHERE key=?", (key,))
    if row and time.time() - row["ts"] < ttl:
        return json.loads(row["value"])
    return None


def _cache_put(key: str, value) -> None:
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO http_cache(key,value,ts) VALUES(?,?,?)",
                  (key, json.dumps(value), time.time()))


# --------------------------------------------------------------------------------------
# FMP
# --------------------------------------------------------------------------------------
class FMPProvider:
    name = "fmp"
    BASE = "https://financialmodelingprep.com/stable"
    TTL_QUOTE = 30
    TTL_HISTORY = 6 * 3600
    TTL_FUNDAMENTALS = 24 * 3600

    def __init__(self, api_key: str):
        if not api_key:
            raise DataError("FMP_API_KEY is not set")
        self.api_key = api_key
        self.session = requests.Session()

    def _get(self, path: str, ttl: float, **params):
        key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        cached = _cache_get(key, ttl)
        if cached is not None:
            return cached
        params["apikey"] = self.api_key
        try:
            r = self.session.get(f"{self.BASE}/{path}", params=params, timeout=20)
        except requests.RequestException as e:
            raise DataError(f"Network error calling FMP {path}: {e}") from e
        if r.status_code in (401, 403):
            raise DataError(f"FMP rejected {path} ({r.status_code}). Check your API key / plan: {r.text[:200]}")
        if r.status_code == 429:
            raise DataError("FMP rate limit hit — wait a minute and retry.")
        if r.status_code >= 400:
            raise DataError(f"FMP error {r.status_code} on {path}: {r.text[:200]}")
        data = r.json()
        if isinstance(data, dict) and data.get("Error Message"):
            raise DataError(data["Error Message"])
        _cache_put(key, data)
        return data

    # ---- prices ----
    def quote(self, symbol: str) -> dict:
        data = self._get("quote", self.TTL_QUOTE, symbol=symbol.upper())
        if not data:
            raise DataError(f"No quote for {symbol}")
        q = data[0]
        return {
            "symbol": q.get("symbol", symbol.upper()),
            "name": q.get("name") or symbol.upper(),
            "price": _num(q, "price"),
            "change_pct": _num(q, "changePercentage", "changesPercentage", default=0.0),
            "prev_close": _num(q, "previousClose"),
            "market_cap": _num(q, "marketCap"),
            "year_high": _num(q, "yearHigh"),
            "year_low": _num(q, "yearLow"),
            "change": _num(q, "change"), "open": _num(q, "open"), "day_low": _num(q, "dayLow"),
            "day_high": _num(q, "dayHigh"), "volume": _num(q, "volume"),
        }

    def quotes(self, symbols: list[str]) -> dict[str, dict]:
        out = {}
        for s in sorted(set(x.upper() for x in symbols)):
            try:
                out[s] = self.quote(s)
            except DataError:
                continue
        return out

    def history(self, symbol: str, days: int = 5 * 365) -> pd.Series:
        start = (date.today() - timedelta(days=days)).isoformat()
        data = self._get("historical-price-eod/dividend-adjusted", self.TTL_HISTORY,
                         symbol=symbol.upper(), **{"from": start})
        if not data:
            data = self._get("historical-price-eod/full", self.TTL_HISTORY, symbol=symbol.upper(), **{"from": start})
        rows = data.get("historical", data) if isinstance(data, dict) else data
        if not rows:
            raise DataError(f"No price history for {symbol}")
        s = pd.Series(
            {pd.Timestamp(r["date"]): _num(r, "adjClose", "close", "price") for r in rows}
        ).dropna().sort_index()
        return s

    def candles(self, symbol: str, interval: str = "1d") -> list[dict]:
        if interval in INTRADAY:
            path = {"5m": "historical-chart/5min", "15m": "historical-chart/15min", "60m": "historical-chart/1hour"}[interval]
            rows = self._get(path, 60, symbol=symbol.upper()) or []
            out = [{"t": int(pd.Timestamp(r["date"]).timestamp()), "o": r["open"], "h": r["high"], "l": r["low"], "c": r["close"], "v": r.get("volume", 0)} for r in rows]
            return sorted(out, key=lambda r: r["t"])
        start = (date.today() - timedelta(days=5 * 365 if interval == "1d" else 20 * 365)).isoformat()
        data = self._get("historical-price-eod/full", self.TTL_HISTORY, symbol=symbol.upper(), **{"from": start})
        rows = data.get("historical", data) if isinstance(data, dict) else data
        daily = sorted([{"t": r["date"][:10], "o": _num(r, "open"), "h": _num(r, "high"), "l": _num(r, "low"), "c": _num(r, "close"),
                         "v": _num(r, "volume", default=0.0)} for r in rows or [] if _num(r, "close")], key=lambda r: r["t"])
        if not daily:
            raise DataError(f"No candles for {symbol}")
        return daily if interval == "1d" else resample_candles(daily, "W" if interval == "1wk" else "M")

    # ---- fundamentals ----
    def profile(self, symbol: str) -> dict:
        data = self._get("profile", self.TTL_FUNDAMENTALS, symbol=symbol.upper())
        if not data:
            raise DataError(f"No profile for {symbol}")
        p = data[0]
        return {
            "symbol": p.get("symbol", symbol.upper()),
            "name": p.get("companyName") or symbol.upper(),
            "sector": p.get("sector") or ("ETF" if p.get("isEtf") else "Unknown"),
            "industry": p.get("industry") or "",
            "beta": _num(p, "beta", default=1.0),
            "market_cap": _num(p, "marketCap", "mktCap"),
            "is_etf": bool(p.get("isEtf")),
            "description": (p.get("description") or "")[:600],
        }

    def income(self, symbol: str, limit: int = 5) -> list[dict]:
        return self._get("income-statement", self.TTL_FUNDAMENTALS, symbol=symbol.upper(), limit=limit) or []

    def balance(self, symbol: str, limit: int = 5) -> list[dict]:
        return self._get("balance-sheet-statement", self.TTL_FUNDAMENTALS, symbol=symbol.upper(), limit=limit) or []

    def cashflow(self, symbol: str, limit: int = 5) -> list[dict]:
        return self._get("cash-flow-statement", self.TTL_FUNDAMENTALS, symbol=symbol.upper(), limit=limit) or []

    def peers(self, symbol: str) -> list[str]:
        try:
            data = self._get("stock-peers", self.TTL_FUNDAMENTALS, symbol=symbol.upper()) or []
        except DataError:
            return []
        out = []
        for row in data:
            if isinstance(row, dict) and row.get("symbol"):
                out.append(row["symbol"])
            elif isinstance(row, dict) and row.get("peersList"):
                out.extend(row["peersList"])
        return [s for s in out if s.upper() != symbol.upper()][:8]

    def multiples(self, symbol: str) -> dict:
        """Trailing multiples used for peer comparison."""
        km, rt = {}, {}
        try:
            km = (self._get("key-metrics-ttm", self.TTL_FUNDAMENTALS, symbol=symbol.upper()) or [{}])[0]
        except DataError:
            pass
        try:
            rt = (self._get("ratios-ttm", self.TTL_FUNDAMENTALS, symbol=symbol.upper()) or [{}])[0]
        except DataError:
            pass
        return {
            "symbol": symbol.upper(),
            "pe": _num(rt, "priceToEarningsRatioTTM", "peRatioTTM"),
            "ev_ebitda": _num(km, "evToEBITDATTM", "enterpriseValueOverEBITDATTM"),
            "ps": _num(rt, "priceToSalesRatioTTM"),
            "pfcf": _num(rt, "priceToFreeCashFlowRatioTTM"),
        }

    def estimates(self, symbol: str) -> list[dict]:
        """Annual consensus estimates, oldest first: [{date, revenue, eps, ebitda, net_income, n_analysts}]."""
        try:
            data = self._get("analyst-estimates", self.TTL_FUNDAMENTALS, symbol=symbol.upper(), period="annual", limit=10) or []
        except DataError:
            return []
        out = [{"date": d.get("date"), "revenue": _num(d, "revenueAvg", "estimatedRevenueAvg"),
                "eps": _num(d, "epsAvg", "estimatedEpsAvg"), "ebitda": _num(d, "ebitdaAvg", "estimatedEbitdaAvg"),
                "net_income": _num(d, "netIncomeAvg", "estimatedNetIncomeAvg"),
                "n_analysts": int(_num(d, "numAnalystsRevenue", "numberAnalystEstimatedRevenue", default=0))}
               for d in data if d.get("date")]
        return sorted(out, key=lambda r: r["date"])

    def price_target(self, symbol: str) -> dict | None:
        try:
            data = self._get("price-target-consensus", self.TTL_FUNDAMENTALS, symbol=symbol.upper()) or []
        except DataError:
            return None
        if not data:
            return None
        d = data[0]
        return {"consensus": _num(d, "targetConsensus"), "median": _num(d, "targetMedian"),
                "high": _num(d, "targetHigh"), "low": _num(d, "targetLow")}

    def index_constituents(self) -> list[dict]:
        """S&P 500 members with index weights (SPY holdings), sectors from the constituent list."""
        try:
            cons = self._get("sp500-constituent", self.TTL_FUNDAMENTALS) or []
        except DataError:
            cons = []
        sector = {c.get("symbol"): c.get("sector") or "Unknown" for c in cons}
        names = {c.get("symbol"): c.get("name") or c.get("symbol") for c in cons}
        try:
            hold = self._get("etf/holdings", self.TTL_FUNDAMENTALS, symbol="SPY") or []
            rows = [{"symbol": h.get("asset") or h.get("symbol"), "name": h.get("name") or names.get(h.get("asset"), ""),
                     "sector": sector.get(h.get("asset") or h.get("symbol"), "Unknown"),
                     "weight": _num(h, "weightPercentage", "weight", default=0)} for h in hold]
            rows = [r for r in rows if r["symbol"] and r["weight"] and r["weight"] > 0]
            if len(rows) > 50:
                return rows
        except DataError:
            pass
        if not cons:
            raise DataError("Could not load S&P 500 constituents from FMP (your plan may not include this endpoint)")
        return [{"symbol": c["symbol"], "name": names[c["symbol"]], "sector": sector[c["symbol"]], "weight": 1.0}
                for c in cons if c.get("symbol")]

    def search(self, q: str) -> list[dict]:
        data = self._get("search-symbol", 3600, query=q, limit=10) or []
        return [{"symbol": d.get("symbol"), "name": d.get("name"), "exchange": d.get("exchange", "")} for d in data]


# --------------------------------------------------------------------------------------
# Simulated market
# --------------------------------------------------------------------------------------
# symbol: (name, sector, base_price, beta, idio_vol, fundamentals: rev_bn, rev_growth, ebit_margin, fcf_margin, net_debt_bn, shares_bn)
SIM_UNIVERSE = {
    # ETFs used by model portfolios
    "SPY": ("SPDR S&P 500 ETF", "ETF", 560, 1.00, 0.00, None),
    "VTI": ("Vanguard Total Stock Market ETF", "ETF", 285, 1.02, 0.02, None),
    "VXUS": ("Vanguard Total International Stock ETF", "ETF", 64, 0.85, 0.08, None),
    "VWO": ("Vanguard Emerging Markets ETF", "ETF", 46, 0.90, 0.13, None),
    "QQQ": ("Invesco QQQ Trust", "ETF", 480, 1.20, 0.06, None),
    "BND": ("Vanguard Total Bond Market ETF", "ETF", 73, 0.05, 0.055, None),
    "TIP": ("iShares TIPS Bond ETF", "ETF", 109, 0.05, 0.05, None),
    "SHV": ("iShares Short Treasury Bond ETF", "ETF", 110, 0.00, 0.004, None),
    "VNQ": ("Vanguard Real Estate ETF", "ETF", 90, 0.90, 0.15, None),
    "GLD": ("SPDR Gold Shares", "ETF", 240, 0.10, 0.14, None),
    # additional ETFs (model library, tax-loss-harvest replacements, sectors)
    "ITOT": ("iShares Core S&P Total US Stock Market ETF", "ETF", 125, 1.02, 0.02, None),
    "SCHB": ("Schwab US Broad Market ETF", "ETF", 23, 1.02, 0.02, None),
    "IVV": ("iShares Core S&P 500 ETF", "ETF", 565, 1.00, 0.01, None),
    "SPLG": ("SPDR Portfolio S&P 500 ETF", "ETF", 66, 1.00, 0.01, None),
    "VOO": ("Vanguard S&P 500 ETF", "ETF", 515, 1.00, 0.01, None),
    "IXUS": ("iShares Core MSCI Total Intl Stock ETF", "ETF", 72, 0.85, 0.08, None),
    "VEA": ("Vanguard FTSE Developed Markets ETF", "ETF", 51, 0.85, 0.08, None),
    "IEFA": ("iShares Core MSCI EAFE ETF", "ETF", 78, 0.85, 0.08, None),
    "IEMG": ("iShares Core MSCI Emerging Markets ETF", "ETF", 57, 0.90, 0.13, None),
    "VGT": ("Vanguard Information Technology ETF", "ETF", 590, 1.25, 0.08, None),
    "SMH": ("VanEck Semiconductor ETF", "ETF", 240, 1.55, 0.18, None),
    "SOXX": ("iShares Semiconductor ETF", "ETF", 225, 1.55, 0.18, None),
    "SCHD": ("Schwab US Dividend Equity ETF", "ETF", 28, 0.80, 0.07, None),
    "VYM": ("Vanguard High Dividend Yield ETF", "ETF", 128, 0.80, 0.07, None),
    "VIG": ("Vanguard Dividend Appreciation ETF", "ETF", 196, 0.90, 0.05, None),
    "DGRO": ("iShares Core Dividend Growth ETF", "ETF", 62, 0.90, 0.05, None),
    "VLUE": ("iShares MSCI USA Value Factor ETF", "ETF", 108, 0.95, 0.08, None),
    "QUAL": ("iShares MSCI USA Quality Factor ETF", "ETF", 178, 1.00, 0.05, None),
    "MTUM": ("iShares MSCI USA Momentum Factor ETF", "ETF", 205, 1.10, 0.10, None),
    "USMV": ("iShares MSCI USA Min Vol Factor ETF", "ETF", 90, 0.70, 0.06, None),
    "ESGU": ("iShares ESG Aware MSCI USA ETF", "ETF", 125, 1.00, 0.02, None),
    "SUSA": ("iShares MSCI USA ESG Select ETF", "ETF", 118, 1.00, 0.03, None),
    "ESGD": ("iShares ESG Aware MSCI EAFE ETF", "ETF", 82, 0.85, 0.08, None),
    "SUSL": ("iShares ESG MSCI USA Leaders ETF", "ETF", 101, 1.00, 0.03, None),
    "AGG": ("iShares Core US Aggregate Bond ETF", "ETF", 100, 0.05, 0.055, None),
    "EAGG": ("iShares ESG Aware US Aggregate Bond ETF", "ETF", 47, 0.05, 0.055, None),
    "VCIT": ("Vanguard Intermediate-Term Corporate Bond ETF", "ETF", 82, 0.10, 0.065, None),
    "IGIB": ("iShares 5-10 Year IG Corporate Bond ETF", "ETF", 52, 0.10, 0.065, None),
    "TLT": ("iShares 20+ Year Treasury Bond ETF", "ETF", 92, -0.10, 0.15, None),
    "VGLT": ("Vanguard Long-Term Treasury ETF", "ETF", 58, -0.10, 0.15, None),
    "IEF": ("iShares 7-10 Year Treasury Bond ETF", "ETF", 95, -0.05, 0.07, None),
    "VGIT": ("Vanguard Intermediate-Term Treasury ETF", "ETF", 59, -0.05, 0.05, None),
    "SCHP": ("Schwab US TIPS ETF", "ETF", 26, 0.05, 0.05, None),
    "BIL": ("SPDR Bloomberg 1-3 Month T-Bill ETF", "ETF", 91.5, 0.00, 0.004, None),
    "SCHH": ("Schwab US REIT ETF", "ETF", 22, 0.90, 0.15, None),
    "XLRE": ("Real Estate Select Sector SPDR", "ETF", 42, 0.90, 0.15, None),
    "IAU": ("iShares Gold Trust", "ETF", 46, 0.10, 0.14, None),
    "DBC": ("Invesco DB Commodity Index Fund", "ETF", 23, 0.40, 0.18, None),
    "XLK": ("Technology Select Sector SPDR", "ETF", 230, 1.20, 0.08, None),
    "XLF": ("Financial Select Sector SPDR", "ETF", 46, 1.05, 0.10, None),
    "XLV": ("Health Care Select Sector SPDR", "ETF", 150, 0.70, 0.09, None),
    "XLY": ("Consumer Discretionary Select Sector SPDR", "ETF", 200, 1.15, 0.10, None),
    "XLP": ("Consumer Staples Select Sector SPDR", "ETF", 80, 0.55, 0.08, None),
    "XLE": ("Energy Select Sector SPDR", "ETF", 92, 0.85, 0.20, None),
    "XLC": ("Communication Services Select Sector SPDR", "ETF", 95, 1.05, 0.11, None),
    "XLI": ("Industrial Select Sector SPDR", "ETF", 135, 1.05, 0.08, None),
    "XLU": ("Utilities Select Sector SPDR", "ETF", 78, 0.45, 0.12, None),
    "XLB": ("Materials Select Sector SPDR", "ETF", 92, 1.00, 0.11, None),
    # Stocks
    "AAPL": ("Apple Inc.", "Technology", 230, 1.15, 0.20, (410, 0.06, 0.31, 0.25, -30, 15.2)),
    "MSFT": ("Microsoft Corporation", "Technology", 430, 1.05, 0.17, (270, 0.13, 0.45, 0.28, -40, 7.45)),
    "NVDA": ("NVIDIA Corporation", "Technology", 125, 1.70, 0.40, (130, 0.45, 0.60, 0.46, -35, 24.5)),
    "GOOGL": ("Alphabet Inc.", "Communication Services", 170, 1.05, 0.22, (350, 0.12, 0.32, 0.21, -80, 12.3)),
    "AMZN": ("Amazon.com Inc.", "Consumer Cyclical", 190, 1.20, 0.24, (640, 0.10, 0.11, 0.06, 20, 10.6)),
    "META": ("Meta Platforms Inc.", "Communication Services", 560, 1.25, 0.28, (165, 0.18, 0.41, 0.30, -30, 2.55)),
    "JPM": ("JPMorgan Chase & Co.", "Financial Services", 220, 1.05, 0.18, (170, 0.05, 0.38, 0.30, 0, 2.85)),
    "JNJ": ("Johnson & Johnson", "Healthcare", 160, 0.55, 0.14, (89, 0.04, 0.26, 0.20, 10, 2.41)),
    "PG": ("Procter & Gamble Co.", "Consumer Defensive", 170, 0.45, 0.13, (84, 0.03, 0.24, 0.18, 25, 2.36)),
    "KO": ("Coca-Cola Co.", "Consumer Defensive", 70, 0.55, 0.13, (47, 0.04, 0.29, 0.21, 35, 4.31)),
    "XOM": ("Exxon Mobil Corp.", "Energy", 115, 0.85, 0.22, (340, 0.01, 0.14, 0.10, 15, 4.4)),
    "TSLA": ("Tesla Inc.", "Consumer Cyclical", 250, 2.00, 0.50, (98, 0.05, 0.07, 0.04, -20, 3.5)),
    "V": ("Visa Inc.", "Financial Services", 285, 0.95, 0.16, (36, 0.10, 0.67, 0.52, 5, 1.95)),
    "UNH": ("UnitedHealth Group", "Healthcare", 520, 0.70, 0.22, (400, 0.07, 0.08, 0.06, 55, 0.92)),
    "HD": ("Home Depot Inc.", "Consumer Cyclical", 390, 1.00, 0.18, (159, 0.03, 0.14, 0.10, 50, 0.99)),
    "INTC": ("Intel Corporation", "Technology", 22, 1.10, 0.38, (53, -0.05, -0.02, -0.15, 30, 4.3)),
}
# Simulated large-cap index (approximate market caps in $bn) for direct indexing in offline mode
SIM_INDEX = [
    ("MSFT", "Microsoft", "Technology", 3800), ("NVDA", "NVIDIA", "Technology", 3600), ("AAPL", "Apple", "Technology", 3500),
    ("AMZN", "Amazon", "Consumer Cyclical", 2200), ("GOOGL", "Alphabet", "Communication Services", 2100),
    ("META", "Meta Platforms", "Communication Services", 1500), ("AVGO", "Broadcom", "Technology", 1100),
    ("TSLA", "Tesla", "Consumer Cyclical", 900), ("JPM", "JPMorgan Chase", "Financial Services", 700),
    ("LLY", "Eli Lilly", "Healthcare", 700), ("V", "Visa", "Financial Services", 600), ("UNH", "UnitedHealth", "Healthcare", 480),
    ("XOM", "Exxon Mobil", "Energy", 470), ("MA", "Mastercard", "Financial Services", 460), ("COST", "Costco", "Consumer Defensive", 420),
    ("WMT", "Walmart", "Consumer Defensive", 700), ("HD", "Home Depot", "Consumer Cyclical", 390), ("PG", "Procter & Gamble", "Consumer Defensive", 400),
    ("JNJ", "Johnson & Johnson", "Healthcare", 380), ("ORCL", "Oracle", "Technology", 450), ("NFLX", "Netflix", "Communication Services", 380),
    ("ABBV", "AbbVie", "Healthcare", 330), ("BAC", "Bank of America", "Financial Services", 320), ("CRM", "Salesforce", "Technology", 280),
    ("KO", "Coca-Cola", "Consumer Defensive", 300), ("CVX", "Chevron", "Energy", 280), ("MRK", "Merck", "Healthcare", 260),
    ("AMD", "AMD", "Technology", 250), ("PEP", "PepsiCo", "Consumer Defensive", 220), ("ADBE", "Adobe", "Technology", 220),
    ("TMO", "Thermo Fisher", "Healthcare", 210), ("LIN", "Linde", "Basic Materials", 210), ("MCD", "McDonald's", "Consumer Cyclical", 210),
    ("CSCO", "Cisco", "Technology", 230), ("ACN", "Accenture", "Technology", 200), ("ABT", "Abbott", "Healthcare", 200),
    ("WFC", "Wells Fargo", "Financial Services", 230), ("DIS", "Disney", "Communication Services", 190), ("IBM", "IBM", "Technology", 210),
    ("QCOM", "Qualcomm", "Technology", 180), ("GE", "GE Aerospace", "Industrials", 250), ("CAT", "Caterpillar", "Industrials", 180),
    ("TXN", "Texas Instruments", "Technology", 170), ("AMGN", "Amgen", "Healthcare", 160), ("INTU", "Intuit", "Technology", 180),
    ("PFE", "Pfizer", "Healthcare", 150), ("NEE", "NextEra Energy", "Utilities", 150), ("GS", "Goldman Sachs", "Financial Services", 170),
    ("PLD", "Prologis", "Real Estate", 100), ("INTC", "Intel", "Technology", 100),
]
_SIM_SECTOR = {sym: sec for sym, _n, sec, _c in SIM_INDEX}

_SIM_DAYS = 5 * 252

# World indices, commodities, crypto, currencies and rates for the Markets home page (simulated levels).
# symbol: (name, kind, level, beta to US market, idiosyncratic vol)
SIM_MARKETS = {
    "^GSPC": ("S&P 500", "Index", 7050, 1.00, 0.02), "^DJI": ("Dow Jones Industrial Average", "Index", 51800, 0.92, 0.04),
    "^IXIC": ("Nasdaq Composite", "Index", 23800, 1.20, 0.05), "^NDX": ("Nasdaq 100", "Index", 25900, 1.22, 0.05),
    "^RUT": ("Russell 2000", "Index", 2480, 1.15, 0.10), "^VIX": ("CBOE Volatility Index", "Index", 16.5, -3.0, 0.70),
    "^NSEI": ("Nifty 50", "Index", 23140, 0.35, 0.13), "^BSESN": ("BSE Sensex", "Index", 73900, 0.35, 0.13),
    "^NSEBANK": ("Nifty Bank", "Index", 50800, 0.40, 0.17),
    "^FTSE": ("FTSE 100", "Index", 9450, 0.55, 0.10), "^GDAXI": ("DAX", "Index", 24300, 0.70, 0.12),
    "^FCHI": ("CAC 40", "Index", 7950, 0.65, 0.12), "^STOXX50E": ("Euro Stoxx 50", "Index", 5550, 0.70, 0.11),
    "^N225": ("Nikkei 225", "Index", 44800, 0.60, 0.16), "^HSI": ("Hang Seng", "Index", 25900, 0.50, 0.20),
    "000001.SS": ("Shanghai Composite", "Index", 3850, 0.25, 0.17), "^AXJO": ("S&P/ASX 200", "Index", 8900, 0.55, 0.10),
    "GCUSD": ("Gold", "Commodity", 3950, 0.05, 0.16), "SIUSD": ("Silver", "Commodity", 47.5, 0.25, 0.28),
    "CLUSD": ("Crude Oil (WTI)", "Commodity", 66.8, 0.40, 0.32), "BZUSD": ("Brent Crude", "Commodity", 70.4, 0.40, 0.30),
    "NGUSD": ("Natural Gas", "Commodity", 3.15, 0.10, 0.55), "HGUSD": ("Copper", "Commodity", 4.85, 0.45, 0.22),
    "PLUSD": ("Platinum", "Commodity", 1420, 0.30, 0.26),
    "BTCUSD": ("Bitcoin", "Crypto", 91500, 1.40, 0.45), "ETHUSD": ("Ethereum", "Crypto", 2920, 1.60, 0.60),
    "SOLUSD": ("Solana", "Crypto", 205, 1.80, 0.75),
    "EURUSD": ("Euro / US Dollar", "Currency", 1.163, 0.05, 0.07), "GBPUSD": ("British Pound / US Dollar", "Currency", 1.342, 0.05, 0.08),
    "USDJPY": ("US Dollar / Japanese Yen", "Currency", 148.2, 0.05, 0.09), "USDINR": ("US Dollar / Indian Rupee", "Currency", 88.30, -0.02, 0.04),
    "^TNX": ("US 10-Year Treasury Yield", "Rate", 4.18, 0.20, 0.25),
}
SIM_UNIVERSE.update({k: (n, kind, lvl, beta, vol, None) for k, (n, kind, lvl, beta, vol) in SIM_MARKETS.items()})
_SIM_BONDS = {"BND", "TIP", "AGG", "EAGG", "VCIT", "IGIB", "TLT", "VGLT", "IEF", "VGIT", "SCHP"}


def _seed(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)


class SimProvider:
    name = "sim"

    def _meta(self, symbol: str):
        s = symbol.upper()
        if s in SIM_UNIVERSE:
            return SIM_UNIVERSE[s]
        # Unknown ticker: make up a plausible mid-cap stock so the app still works offline.
        rng = np.random.default_rng(_seed(s))
        rev = float(rng.uniform(2, 60))
        return (next((n for sym, n, _s, _c in SIM_INDEX if sym == s), f"{s} Corp.") + " (simulated)",
                _SIM_SECTOR.get(s, "Industrials"), float(rng.uniform(20, 300)),
                float(rng.uniform(0.7, 1.5)), float(rng.uniform(0.18, 0.35)),
                (rev, float(rng.uniform(-0.02, 0.18)), float(rng.uniform(0.05, 0.25)),
                 float(rng.uniform(0.03, 0.18)), float(rng.uniform(-2, 10)), float(rng.uniform(0.1, 1.5))))

    @staticmethod
    @lru_cache(maxsize=1)
    def _market_returns() -> np.ndarray:
        rng = np.random.default_rng(42)
        return rng.normal(0.09 / 252, 0.16 / math.sqrt(252), _SIM_DAYS)

    @lru_cache(maxsize=256)
    def _path(self, symbol: str) -> pd.Series:
        name, sector, base, beta, ivol, _ = self._meta(symbol)
        rng = np.random.default_rng(_seed(symbol.upper()))
        mkt = self._market_returns()
        if symbol.upper() in _SIM_BONDS:
            rets = rng.normal(0.035 / 252, ivol / math.sqrt(252), _SIM_DAYS) + beta * (mkt - mkt.mean())
        elif symbol.upper() in ("SHV", "BIL"):
            rets = rng.normal(0.045 / 252, ivol / math.sqrt(252), _SIM_DAYS)
        else:
            alpha = rng.normal(0.0, 0.03) / 252
            rets = beta * mkt + alpha + rng.normal(0, ivol / math.sqrt(252), _SIM_DAYS)
        levels = np.exp(np.cumsum(rets))
        levels = levels / levels[-1] * base          # anchor today's close near base price
        end = pd.Timestamp(date.today()) - pd.tseries.offsets.BDay(1)
        idx = pd.bdate_range(end=end, periods=_SIM_DAYS)
        return pd.Series(levels, index=idx)

    def quote(self, symbol: str) -> dict:
        s = symbol.upper()
        hist = self._path(s)
        last = float(hist.iloc[-1])
        # intraday drift that changes every minute, so the paper account "moves"
        minute = int(time.time() // 60)
        vol = self._meta(s)[4] + 0.1 * self._meta(s)[3]
        rng = np.random.default_rng(_seed(f"{s}:{minute // 5}"))
        dp = 4 if last < 20 else 2
        price = round(last * math.exp(rng.normal(0, vol / math.sqrt(252) * 0.6)), dp)
        return {
            "symbol": s, "name": self._meta(s)[0], "price": price,
            "change_pct": round((price / last - 1) * 100, 3), "prev_close": round(last, dp),
            "market_cap": None, "year_high": float(hist.iloc[-252:].max()), "year_low": float(hist.iloc[-252:].min()),
            "change": round(price - last, 4), "open": round(last * math.exp(rng.normal(0, 0.002)), 4),
            "day_low": round(min(price, last) * (1 - abs(rng.normal(0, 0.004))), 4),
            "day_high": round(max(price, last) * (1 + abs(rng.normal(0, 0.004))), 4),
            "volume": None if s in SIM_MARKETS and SIM_MARKETS[s][1] in ("Currency", "Rate", "Index")
            else float(int(rng.uniform(2e6, 6e7))),
        }

    def quotes(self, symbols):
        return {s.upper(): self.quote(s) for s in set(symbols)}

    def history(self, symbol: str, days: int = 5 * 365) -> pd.Series:
        h = self._path(symbol.upper())
        cutoff = pd.Timestamp(date.today() - timedelta(days=days))
        return h[h.index >= cutoff].copy()

    def candles(self, symbol: str, interval: str = "1d") -> list[dict]:
        s = symbol.upper()
        path = self._path(s)
        vol = self._meta(s)[4] + 0.1 * abs(self._meta(s)[3])
        rng = np.random.default_rng(_seed("ohlc" + s))
        closes = path.values
        daily = []
        for i, (d, c) in enumerate(path.items()):
            prev = closes[i - 1] if i else c
            o = prev * (1 + rng.normal(0, vol / math.sqrt(252) * 0.3))
            wig = abs(rng.normal(0, vol / math.sqrt(252) * 0.5))
            daily.append({"t": d.date().isoformat(), "o": float(o), "h": float(max(o, c) * (1 + wig)), "l": float(min(o, c) * (1 - wig)),
                          "c": float(c), "v": float(int(rng.uniform(1e6, 4e7)))})
        if interval == "1d":
            return daily
        if interval in ("1wk", "1mo"):
            return resample_candles(daily, "W" if interval == "1wk" else "M")
        # intraday: a random walk from yesterday's close to the current simulated quote
        q = self.quote(s)
        steps, days = {"5m": (78, 1), "15m": (26, 5), "60m": (7, 22)}[interval]
        mins = {"5m": 5, "15m": 15, "60m": 60}[interval]
        out = []
        day_list = [d for d in path.index[-days:]]
        for k, d in enumerate(day_list):
            start_px = closes[-days - 1 + k] if len(closes) > days else closes[0]
            end_px = q["price"] if k == len(day_list) - 1 else closes[-days + k]
            walk = np.cumsum(rng.normal(0, vol / math.sqrt(252 * steps), steps))
            walk = walk - np.linspace(0, walk[-1], steps)                     # bridge: pin the end
            lvl = start_px * np.exp(np.linspace(0, math.log(end_px / start_px), steps) + walk)
            base = int(pd.Timestamp(d.date()).timestamp()) + 9 * 3600 + 1800
            p0 = start_px
            for j in range(steps):
                c = float(lvl[j]); w = abs(rng.normal(0, vol / math.sqrt(252 * steps)))
                out.append({"t": base + j * mins * 60, "o": float(p0), "h": float(max(p0, c) * (1 + w)), "l": float(min(p0, c) * (1 - w)),
                            "c": c, "v": float(int(rng.uniform(2e4, 6e5)))})
                p0 = c
        return out

    def profile(self, symbol: str) -> dict:
        name, sector, base, beta, ivol, f = self._meta(symbol)
        return {"symbol": symbol.upper(), "name": name, "sector": sector, "industry": sector,
                "beta": beta, "market_cap": (f[5] * 1e9 * base) if f else None,
                "is_etf": f is None, "description": "Simulated security (no FMP API key configured)."}

    def _fund_years(self, symbol: str, limit: int):
        name, sector, base, beta, ivol, f = self._meta(symbol)
        if f is None:
            return []
        rev_bn, g, ebit_m, fcf_m, net_debt_bn, shares_bn = f
        rng = np.random.default_rng(_seed("fund" + symbol.upper()))
        years = []
        this_year = date.today().year - 1
        rev = rev_bn * 1e9
        for i in range(limit + 1):
            noise = rng.normal(0, 0.015)
            years.append({
                "year": this_year - i,
                "revenue": rev,
                "ebit_m": max(-0.3, ebit_m + noise),
                "fcf_m": fcf_m + rng.normal(0, 0.01),
                "net_debt": net_debt_bn * 1e9 * (1 + 0.05 * i),
                "shares": shares_bn * 1e9 * (1 + 0.01 * i),
            })
            rev = rev / (1 + g + rng.normal(0, 0.02))
        return years

    def income(self, symbol: str, limit: int = 5):
        out = []
        for y in self._fund_years(symbol, limit)[:limit]:
            ebit = y["revenue"] * y["ebit_m"]
            interest = max(0.0, y["net_debt"]) * 0.045 + y["revenue"] * 0.002
            pretax = ebit - interest
            tax = max(0.0, pretax) * 0.19
            ni = pretax - tax
            out.append({
                "date": f"{y['year']}-12-31", "fiscalYear": str(y["year"]),
                "revenue": y["revenue"], "costOfRevenue": y["revenue"] * (1 - y["ebit_m"] - 0.18),
                "grossProfit": y["revenue"] * (y["ebit_m"] + 0.18), "operatingIncome": ebit,
                "ebitda": ebit + y["revenue"] * 0.04, "interestExpense": interest,
                "incomeBeforeTax": pretax, "incomeTaxExpense": tax, "netIncome": ni,
                "epsDiluted": ni / y["shares"], "weightedAverageShsOutDil": y["shares"],
            })
        return out

    def balance(self, symbol: str, limit: int = 5):
        out = []
        for y in self._fund_years(symbol, limit)[:limit]:
            assets = y["revenue"] * 1.3
            debt = max(0.0, y["net_debt"]) + y["revenue"] * 0.15
            cash = debt - y["net_debt"]
            equity = assets * 0.45
            out.append({
                "date": f"{y['year']}-12-31", "totalAssets": assets, "totalCurrentAssets": assets * 0.35,
                "totalCurrentLiabilities": assets * 0.25, "totalDebt": debt, "longTermDebt": debt * 0.85,
                "cashAndCashEquivalents": cash, "cashAndShortTermInvestments": cash,
                "totalStockholdersEquity": equity, "retainedEarnings": equity * 0.6,
                "totalLiabilities": assets - equity, "netDebt": y["net_debt"],
            })
        return out

    def cashflow(self, symbol: str, limit: int = 5):
        out = []
        for y in self._fund_years(symbol, limit)[:limit]:
            fcf = y["revenue"] * y["fcf_m"]
            capex = -y["revenue"] * 0.05
            out.append({"date": f"{y['year']}-12-31", "operatingCashFlow": fcf - capex,
                        "capitalExpenditure": capex, "freeCashFlow": fcf,
                        "depreciationAndAmortization": y["revenue"] * 0.04,
                        "stockBasedCompensation": y["revenue"] * 0.01})
        return out

    def peers(self, symbol: str):
        sector = self._meta(symbol)[1]
        return [s for s, m in SIM_UNIVERSE.items() if m[1] == sector and s != symbol.upper()][:6]

    def multiples(self, symbol: str) -> dict:
        inc, bal = self.income(symbol, 1), self.balance(symbol, 1)
        q = self.quote(symbol)
        if not inc:
            return {"symbol": symbol.upper(), "pe": None, "ev_ebitda": None, "ps": None, "pfcf": None}
        shares = inc[0]["weightedAverageShsOutDil"]
        mcap = q["price"] * shares
        ev = mcap + bal[0]["netDebt"]
        cf = self.cashflow(symbol, 1)[0]
        return {"symbol": symbol.upper(),
                "pe": mcap / inc[0]["netIncome"] if inc[0]["netIncome"] > 0 else None,
                "ev_ebitda": ev / inc[0]["ebitda"] if inc[0]["ebitda"] > 0 else None,
                "ps": mcap / inc[0]["revenue"],
                "pfcf": mcap / cf["freeCashFlow"] if cf["freeCashFlow"] > 0 else None}

    def estimates(self, symbol: str) -> list[dict]:
        inc = self.income(symbol, 1)
        if not inc:
            return []
        g = self._meta(symbol)[5][1]
        rng = np.random.default_rng(_seed("est" + symbol.upper()))
        rev, eps, year = inc[0]["revenue"], inc[0]["epsDiluted"], int(inc[0]["fiscalYear"])
        out = []
        for i in range(1, 6):
            gi = g + rng.normal(0.01, 0.02)
            rev, eps = rev * (1 + gi), eps * (1 + gi + rng.normal(0.01, 0.02))
            out.append({"date": f"{year + i}-12-31", "revenue": rev, "eps": eps, "ebitda": None, "net_income": None,
                        "n_analysts": max(2, 30 - 6 * i)})
        return out

    def price_target(self, symbol: str) -> dict | None:
        if self._meta(symbol)[5] is None:
            return None
        rng = np.random.default_rng(_seed("pt" + symbol.upper()))
        base = float(self._path(symbol.upper()).iloc[-1])
        c = base * (1 + rng.normal(0.10, 0.08))
        return {"consensus": c, "median": c * 0.99, "high": c * 1.25, "low": c * 0.75}

    def index_constituents(self) -> list[dict]:
        return [{"symbol": sym, "name": name, "sector": sec, "weight": cap} for sym, name, sec, cap in SIM_INDEX]

    def search(self, q: str):
        q = q.upper()
        return [{"symbol": s, "name": m[0], "exchange": "SIM"} for s, m in SIM_UNIVERSE.items()
                if q in s or q in m[0].upper()][:10]


# --------------------------------------------------------------------------------------
# Live prices from Yahoo Finance (free, no key) + FMP fundamentals when a key is present
# --------------------------------------------------------------------------------------
NEED_FMP = ("Company financials (DCF research) need a free FMP key. Sign up at financialmodelingprep.com, "
            "put it in the .env file as FMP_API_KEY=..., then restart Portfolio Manager.")

# app symbol -> Yahoo symbol (commodities trade as front-month futures on Yahoo)
YAHOO_MAP = {"GCUSD": "GC=F", "SIUSD": "SI=F", "PLUSD": "PL=F", "CLUSD": "CL=F", "BZUSD": "BZ=F", "NGUSD": "NG=F",
             "HGUSD": "HG=F", "PAUSD": "PA=F"}
_CRYPTO = {"BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "BNB", "LTC", "DOT", "AVAX", "LINK", "MATIC", "TRX", "SHIB", "BCH"}
_FX = {"USD", "EUR", "GBP", "JPY", "INR", "CHF", "CAD", "AUD", "NZD", "CNY", "HKD", "SGD", "SEK", "NOK", "MXN", "BRL",
       "ZAR", "KRW", "AED"}
_YKIND = {"EQUITY": "Stock", "ETF": "ETF", "INDEX": "Index", "FUTURE": "Commodity", "CRYPTOCURRENCY": "Crypto",
          "CURRENCY": "Currency", "MUTUALFUND": "Fund"}


def to_yahoo(symbol: str) -> str:
    s = symbol.strip().upper()
    if s in YAHOO_MAP:
        return YAHOO_MAP[s]
    if len(s) >= 6 and s.endswith("USD") and s[:-3] in _CRYPTO:
        return s[:-3] + "-USD"
    if len(s) == 6 and s[:3] in _FX and s[3:] in _FX:
        return s + "=X"
    if "." in s and len(s.split(".")[-1]) == 1:          # class shares: BRK.B -> BRK-B
        return s.replace(".", "-")
    return s


class LiveProvider:
    """Prices, history, search AND company fundamentals from Yahoo Finance's public endpoints (free, no key).
    When FMP_API_KEY is set, FMP is tried first for statements/estimates/peers and Yahoo is the fallback."""
    name = "live"
    HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
    TTL_QUOTE = 60
    TTL_HISTORY = 30 * 60

    def __init__(self, fmp: "FMPProvider | None" = None, session=None):
        self.fmp = fmp
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                     "(KHTML, like Gecko) Chrome/126.0 Safari/537.36", "Accept": "application/json"})

    # ---- HTTP ----
    def _yget(self, path: str, ttl: float, **params):
        key = "yahoo:" + path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        cached = _cache_get(key, ttl)
        if cached is not None:
            return cached
        last = None
        for host in self.HOSTS:
            try:
                r = self.session.get(host + path, params=params, timeout=12)
            except requests.RequestException as e:
                last = f"network error: {e}"
                continue
            if r.status_code == 404:
                raise DataError(f"Symbol not found on Yahoo Finance ({path.rsplit('/', 1)[-1]})")
            if r.status_code == 429:
                last = "Yahoo Finance rate limit - try again in a minute"
                continue
            if r.status_code >= 400:
                last = f"Yahoo Finance error {r.status_code}"
                continue
            data = r.json()
            _cache_put(key, data)
            return data
        raise DataError(f"Couldn't reach Yahoo Finance ({last}). Check your internet connection.")

    def _chart(self, symbol: str, ttl: float, **params) -> dict:
        ys = to_yahoo(symbol)
        data = self._yget(f"/v8/finance/chart/{requests.utils.quote(ys, safe='')}", ttl, **params)
        chart = (data or {}).get("chart") or {}
        if chart.get("error") or not chart.get("result"):
            err = (chart.get("error") or {}).get("description") or "no data"
            raise DataError(f"No price data for {symbol}: {err}")
        return chart["result"][0]

    # ---- prices ----
    def quote(self, symbol: str) -> dict:
        res = self._chart(symbol, self.TTL_QUOTE, range="5d", interval="1d")
        m = res.get("meta", {})
        price = m.get("regularMarketPrice")
        if price is None:
            raise DataError(f"No price for {symbol}")
        closes = [c for c in ((res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []) if c is not None]
        prev = None
        if m.get("fulldayChange") is not None:
            prev = price - m["fulldayChange"]
        elif m.get("regularMarketChangePercent") is not None:
            prev = price / (1 + m["regularMarketChangePercent"] / 100)
        elif len(closes) >= 2:
            prev = closes[-2] if abs(closes[-1] - price) < 1e-9 * max(1, price) else closes[-1]
        chg_pct = (price / prev - 1) * 100 if prev else 0.0
        return {
            "symbol": symbol.upper(), "name": m.get("longName") or m.get("shortName") or symbol.upper(),
            "price": float(price), "change_pct": float(chg_pct), "prev_close": float(prev) if prev else None,
            "change": float(price - prev) if prev else None, "market_cap": None,
            "year_high": m.get("fiftyTwoWeekHigh"), "year_low": m.get("fiftyTwoWeekLow"),
            "day_high": m.get("regularMarketDayHigh"), "day_low": m.get("regularMarketDayLow"),
            "open": None, "volume": m.get("regularMarketVolume") or None,
            "currency": m.get("currency"), "exchange": m.get("fullExchangeName") or m.get("exchangeName"),
            "kind": _YKIND.get(m.get("instrumentType", ""), None),
            "as_of": datetime.fromtimestamp(m["regularMarketTime"], tz=timezone.utc).isoformat() if m.get("regularMarketTime") else None,
            "source": "Yahoo Finance",
        }

    def quotes(self, symbols: list[str]) -> dict[str, dict]:
        out = {}
        for s in sorted(set(x.upper() for x in symbols)):
            try:
                out[s] = self.quote(s)
            except DataError:
                continue
        return out

    def history(self, symbol: str, days: int = 5 * 365) -> pd.Series:
        # fetch one of two fixed windows (anchored to today) and slice, so every caller shares the same cached call
        want = days
        days = 400 if days <= 400 else 1830
        start = int(datetime.combine(date.today() - timedelta(days=days), datetime.min.time(), tzinfo=timezone.utc).timestamp())
        res = self._chart(symbol, self.TTL_HISTORY, period1=start, period2=int(time.time() // 3600 * 3600 + 3600),
                          interval="1d", events="div,split")
        ts = res.get("timestamp") or []
        ind = res.get("indicators", {})
        adj = (ind.get("adjclose") or [{}])[0].get("adjclose")
        close = (ind.get("quote") or [{}])[0].get("close") or []
        vals = adj if adj and len(adj) == len(ts) else close
        off = res.get("meta", {}).get("gmtoffset", 0) or 0
        s = pd.Series({pd.Timestamp(datetime.fromtimestamp(t + off, tz=timezone.utc).date()): v
                       for t, v in zip(ts, vals) if v is not None}, dtype=float)
        s = s[~s.index.duplicated(keep="last")].sort_index()
        s = s[s.index >= pd.Timestamp(date.today() - timedelta(days=want))]
        if s.empty:
            raise DataError(f"No price history for {symbol}")
        return s

    def candles(self, symbol: str, interval: str = "1d") -> list[dict]:
        if interval not in CANDLE_INTERVALS:
            raise DataError(f"Unsupported interval {interval}")
        if interval in INTRADAY:
            rng, ttl = {"5m": ("1d", 60), "15m": ("5d", 120), "60m": ("1mo", 600)}[interval]
            params = {"range": rng}
        else:
            # explicit dates: Yahoo silently turns range=max monthly data into quarterly bars
            years, ttl = {"1d": (5, 1800), "1wk": (10, 6 * 3600), "1mo": (30, 6 * 3600)}[interval]
            start = datetime.combine(date.today() - timedelta(days=int(years * 365.25)), datetime.min.time(), tzinfo=timezone.utc)
            params = {"period1": int(start.timestamp()), "period2": int(time.time() // 3600 * 3600 + 3600)}
        res = self._chart(symbol, ttl, interval=interval, includePrePost="false", events="div,split", **params)
        ts = res.get("timestamp") or []
        q = (res.get("indicators", {}).get("quote") or [{}])[0]
        adj = (res.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")
        off = res.get("meta", {}).get("gmtoffset", 0) or 0
        n = len(ts)
        col = lambda k: q.get(k) or [None] * n
        O, H, L, C, V = col("open"), col("high"), col("low"), col("close"), col("volume")
        out = []
        for i, t in enumerate(ts):
            o, h, l, c = O[i], H[i], L[i], C[i]
            if None in (o, h, l, c) or c <= 0:
                continue
            f = (adj[i] / c) if (adj and i < len(adj) and adj[i]) else 1.0          # split/dividend adjust
            day = datetime.fromtimestamp(t + off, tz=timezone.utc).date()
            if interval == "1wk":
                key = (day - timedelta(days=day.weekday())).isoformat()            # Monday of that week
            elif interval == "1mo":
                key = day.replace(day=1).isoformat()
            else:
                key = int(t + off) if interval in INTRADAY else day.isoformat()
            bar = {"t": key, "o": o * f, "h": h * f, "l": l * f, "c": c * f, "v": float(V[i] or 0)}
            if out and out[-1]["t"] == key:                                    # Yahoo appends a live partial bar
                prev = out[-1]
                prev.update(h=max(prev["h"], bar["h"]), l=min(prev["l"], bar["l"]), c=bar["c"], v=max(prev["v"], bar["v"]))
            else:
                out.append(bar)
        if not out:
            raise DataError(f"No {interval} candles for {symbol}")
        return out

    # ---- reference data ----
    def search(self, q: str) -> list[dict]:
        data = self._yget("/v1/finance/search", 3600, q=q, quotesCount=10, newsCount=0, listsCount=0)
        out = []
        for d in (data or {}).get("quotes", []):
            if not d.get("symbol") or not d.get("isYahooFinance", True):
                continue
            out.append({"symbol": d["symbol"], "name": d.get("longname") or d.get("shortname") or d["symbol"],
                        "exchange": d.get("exchDisp") or d.get("exchange", ""), "type": d.get("typeDisp", "")})
        return out

    def profile(self, symbol: str) -> dict:
        if self.fmp:
            try:
                return self.fmp.profile(symbol)
            except DataError:
                pass
        q = self.quote(symbol)
        info = {}
        try:
            for d in self._yget("/v1/finance/search", 24 * 3600, q=to_yahoo(symbol), quotesCount=5, newsCount=0).get("quotes", []):
                if d.get("symbol", "").upper() == to_yahoo(symbol):
                    info = d
                    break
        except DataError:
            pass
        sm = self._summary(symbol)
        sp, ks, pr = sm.get("summaryProfile") or {}, sm.get("defaultKeyStatistics") or {}, sm.get("price") or {}
        is_etf = q.get("kind") in ("ETF", "Fund") or (info.get("quoteType") in ("ETF", "MUTUALFUND"))
        beta = _raw(ks.get("beta"))
        if beta is None and not is_etf:
            beta = self._calc_beta(symbol)
        return {"symbol": symbol.upper(), "name": q["name"],
                "sector": sp.get("sector") or info.get("sector") or ("ETF" if is_etf else q.get("kind") or "Other"),
                "industry": sp.get("industry") or info.get("industry") or "", "beta": beta,
                "market_cap": _raw(pr.get("marketCap")), "is_etf": is_etf,
                "description": (sp.get("longBusinessSummary") or f"{q.get('exchange') or ''} · data from Yahoo Finance")[:600]}

    def _calc_beta(self, symbol: str) -> float | None:
        """2-year weekly beta vs the S&P 500 (fallback when Yahoo has no published beta)."""
        try:
            a = self.history(symbol, 730).resample("W").last().pct_change()
            m = self.history("^GSPC", 730).resample("W").last().pct_change()
            df = pd.concat([a, m], axis=1).dropna()
            if len(df) < 30 or df.iloc[:, 1].var() == 0:
                return None
            return float(df.iloc[:, 0].cov(df.iloc[:, 1]) / df.iloc[:, 1].var())
        except Exception:
            return None

    def index_constituents(self) -> list[dict]:
        if self.fmp:
            try:
                return self.fmp.index_constituents()
            except DataError:
                pass
        return SimProvider().index_constituents()       # static list of the 50 largest S&P 500 names

    # ---- fundamentals: FMP when a key is set, otherwise Yahoo Finance (free) ----
    TTL_FUND = 12 * 3600
    # Yahoo fundamentals-timeseries field -> FMP-style key used by research.py
    Y_INCOME = {"TotalRevenue": "revenue", "GrossProfit": "grossProfit", "OperatingIncome": "operatingIncome",
                "EBIT": "ebit", "EBITDA": "ebitda", "InterestExpense": "interestExpense", "PretaxIncome": "incomeBeforeTax",
                "TaxProvision": "incomeTaxExpense", "NetIncomeCommonStockholders": "netIncome", "NetIncome": "_netIncome",
                "DilutedEPS": "epsDiluted", "DilutedAverageShares": "weightedAverageShsOutDil"}
    Y_BALANCE = {"TotalAssets": "totalAssets", "CurrentAssets": "totalCurrentAssets", "CurrentLiabilities": "totalCurrentLiabilities",
                 "TotalDebt": "totalDebt", "LongTermDebt": "longTermDebt", "CashAndCashEquivalents": "cashAndCashEquivalents",
                 "CashCashEquivalentsAndShortTermInvestments": "cashAndShortTermInvestments",
                 "StockholdersEquity": "totalStockholdersEquity", "RetainedEarnings": "retainedEarnings"}
    Y_CASH = {"OperatingCashFlow": "operatingCashFlow", "CapitalExpenditure": "capitalExpenditure", "FreeCashFlow": "freeCashFlow",
              "DepreciationAndAmortization": "depreciationAndAmortization", "DepreciationAmortizationDepletion": "_dna",
              "StockBasedCompensation": "stockBasedCompensation"}
    Y_VALUATION = ("trailingPeRatio", "trailingEnterprisesValueEBITDARatio", "trailingPsRatio", "trailingMarketCap")
    NOT_MONEY = {"weightedAverageShsOutDil"}

    def _fmp_or(self, name, *a):
        if self.fmp:
            try:
                out = getattr(self.fmp, name)(*a)
                if out:
                    return out
            except DataError:
                pass
        return None

    summary_error: str | None = None

    def _ysession(self):
        """Session for Yahoo's cookie+crumb endpoints. Yahoo rejects plain python-requests there, so use curl_cffi's
        browser impersonation when it is installed; fall back to the normal session otherwise."""
        if getattr(self, "_ys", None) is None:
            self._ys = self.session
            if not hasattr(self.session, "calls"):          # keep the injected fake session in tests
                try:
                    from curl_cffi import requests as cffi_requests
                    self._ys = cffi_requests.Session(impersonate="chrome")
                except Exception:
                    pass
        return self._ys

    def _crumb(self, fresh: bool = False) -> str | None:
        now = time.time()
        if not fresh and getattr(self, "_crumb_val", None) and now - self._crumb_at < 3600:
            return self._crumb_val
        if not fresh and getattr(self, "_crumb_fail_at", 0) and now - self._crumb_fail_at < 900:
            return None                                      # failed recently - don't slow every request down
        self._crumb_val, self._crumb_at = None, now
        ses = self._ysession()
        try:
            ses.get("https://fc.yahoo.com", timeout=8)       # sets Yahoo's session cookie (the page itself 404s)
        except Exception:
            pass
        why = "no response"
        for host in self.HOSTS:
            try:
                r = ses.get(host + "/v1/test/getcrumb", timeout=8)
            except Exception as e:
                why = f"network error ({e.__class__.__name__})"
                continue
            txt = (getattr(r, "text", "") or "").strip()
            if r.status_code == 200 and txt and "<" not in txt and len(txt) < 40 and " " not in txt:
                self._crumb_val, self._crumb_fail_at, self.summary_error = txt, 0, None
                return txt
            why = f"HTTP {r.status_code}"
        self._crumb_fail_at = now
        self.summary_error = f"Yahoo Finance analyst data unavailable ({why})"
        return None

    def _summary(self, symbol: str) -> dict:
        """Yahoo quoteSummary (profile, beta, analyst targets, estimates). Best effort: {} when unavailable."""
        ys = to_yahoo(symbol)
        key = "yahoo:summary:" + ys
        cached = _cache_get(key, self.TTL_FUND)
        if cached is not None:
            return cached
        mods = "summaryProfile,defaultKeyStatistics,financialData,earningsTrend,price"
        ses = self._ysession()
        for attempt in range(2):
            crumb = self._crumb(fresh=attempt > 0)
            if not crumb:
                return {}
            for host in self.HOSTS:
                try:
                    r = ses.get(f"{host}/v10/finance/quoteSummary/{requests.utils.quote(ys, safe='')}",
                                params={"modules": mods, "crumb": crumb}, timeout=12)
                except Exception:
                    continue
                if r.status_code in (401, 403):
                    break                                   # stale crumb -> refresh once
                if r.status_code == 404:
                    _cache_put(key, {})
                    return {}
                if r.status_code != 200:
                    continue
                res = ((r.json() or {}).get("quoteSummary") or {}).get("result") or []
                out = res[0] if res else {}
                _cache_put(key, out)
                return out
        return {}

    def _series(self, symbol: str) -> dict:
        """All annual statement lines + trailing valuation ratios from Yahoo's fundamentals-timeseries endpoint."""
        ys = to_yahoo(symbol)
        types = ",".join(["annual" + k for k in (*self.Y_INCOME, *self.Y_BALANCE, *self.Y_CASH)] + list(self.Y_VALUATION))
        now = int(time.time() // 86400 * 86400)
        data = self._yget(f"/ws/fundamentals-timeseries/v1/finance/timeseries/{requests.utils.quote(ys, safe='')}",
                          self.TTL_FUND, type=types, period1=now - 11 * 365 * 86400, period2=now + 86400)
        out = {}
        for r in ((data or {}).get("timeseries") or {}).get("result") or []:
            t = ((r.get("meta") or {}).get("type") or [None])[0]
            rows = [x for x in (r.get(t) or []) if x and (x.get("reportedValue") or {}).get("raw") is not None]
            if t and rows:
                out[t] = sorted(rows, key=lambda x: x.get("asOfDate") or "")
        return out

    def _fx(self, symbol: str, fin_ccy: str | None) -> float:
        """Factor converting reported (financial) currency into the currency the stock trades in (ADRs, GBp, ...)."""
        try:
            px = self.quote(symbol).get("currency")
        except DataError:
            return 1.0
        if not fin_ccy or not px:
            return 1.0
        sub = {"GBp": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ILA": ("ILS", 100.0)}
        base, mult = sub.get(px, (px.upper(), 1.0))
        if fin_ccy.upper() == base:
            return mult
        try:
            return float(self._chart(f"{fin_ccy.upper()}{base}=X", 3600, range="5d", interval="1d")["meta"]["regularMarketPrice"]) * mult
        except (DataError, KeyError, TypeError, ValueError):
            return 1.0

    def _statement(self, symbol: str, mapping: dict, limit: int) -> list[dict]:
        ser = self._series(symbol)
        by_date: dict[str, dict] = {}
        ccy = None
        for yk, fk in mapping.items():
            for x in ser.get("annual" + yk, []):
                d = by_date.setdefault(x["asOfDate"], {"date": x["asOfDate"], "fiscalYear": x["asOfDate"][:4]})
                d[fk] = x["reportedValue"]["raw"]
                ccy = ccy or x.get("currencyCode")
        if not by_date:
            return []
        fx = self._fx(symbol, ccy)
        rows = sorted(by_date.values(), key=lambda d: d["date"], reverse=True)[:limit]
        for d in rows:
            if "_netIncome" in d:
                d.setdefault("netIncome", d["_netIncome"])
            if "_dna" in d:
                d.setdefault("depreciationAndAmortization", d["_dna"])
            if d.get("operatingIncome") is None and d.get("ebit") is not None:
                d["operatingIncome"] = d["ebit"]
            if d.get("freeCashFlow") is None and d.get("operatingCashFlow") is not None and d.get("capitalExpenditure") is not None:
                d["freeCashFlow"] = d["operatingCashFlow"] + d["capitalExpenditure"]
            if fx != 1.0:
                for k, v in list(d.items()):
                    if isinstance(v, (int, float)) and k not in self.NOT_MONEY:
                        d[k] = v * fx
        return rows

    def income(self, symbol, limit=5):
        rows = self._fmp_or("income", symbol, limit)
        return rows if rows is not None else self._statement(symbol, self.Y_INCOME, limit)

    def balance(self, symbol, limit=5):
        rows = self._fmp_or("balance", symbol, limit)
        return rows if rows is not None else self._statement(symbol, self.Y_BALANCE, limit)

    def cashflow(self, symbol, limit=5):
        rows = self._fmp_or("cashflow", symbol, limit)
        return rows if rows is not None else self._statement(symbol, self.Y_CASH, limit)

    def peers(self, symbol):
        rows = self._fmp_or("peers", symbol)
        if rows is not None:
            return rows
        try:
            data = self._yget(f"/v6/finance/recommendationsbysymbol/{requests.utils.quote(to_yahoo(symbol), safe='')}", self.TTL_FUND)
        except DataError:
            return []
        res = ((data or {}).get("finance") or {}).get("result") or []
        syms = [d.get("symbol") for d in (res[0].get("recommendedSymbols") or [])] if res else []
        return [s for s in syms if s and s.upper() != to_yahoo(symbol).upper()][:8]

    def multiples(self, symbol):
        m = self._fmp_or("multiples", symbol)
        if m and any(m.get(k) for k in ("pe", "ev_ebitda", "ps", "pfcf")):
            return m
        try:
            ser = self._series(symbol)
        except DataError:
            ser = {}
        last = lambda t: ser[t][-1]["reportedValue"]["raw"] if ser.get(t) else None
        mcap = last("trailingMarketCap")
        fcf = None
        try:
            cf = self._statement(symbol, self.Y_CASH, 1)
            fcf = cf[0].get("freeCashFlow") if cf else None
        except DataError:
            pass
        return {"symbol": symbol.upper(), "pe": last("trailingPeRatio"), "ev_ebitda": last("trailingEnterprisesValueEBITDARatio"),
                "ps": last("trailingPsRatio"), "pfcf": mcap / fcf if mcap and fcf and fcf > 0 else None}

    def estimates(self, symbol):
        rows = self._fmp_or("estimates", symbol)
        if rows is not None:
            return rows
        trend = ((self._summary(symbol).get("earningsTrend") or {}).get("trend")) or []
        fd = self._summary(symbol).get("financialData") or {}
        fx = self._fx(symbol, fd.get("financialCurrency")) if trend else 1.0
        out = []
        for t in trend:
            if not str(t.get("period", "")).endswith("y") or not t.get("endDate"):
                continue
            rev, eps = _raw((t.get("revenueEstimate") or {}).get("avg")), _raw((t.get("earningsEstimate") or {}).get("avg"))
            out.append({"date": t["endDate"], "revenue": rev * fx if rev else None, "eps": eps,
                        "ebitda": None, "net_income": None,
                        "n_analysts": int(_raw((t.get("revenueEstimate") or {}).get("numberOfAnalysts")) or 0)})
        return sorted(out, key=lambda r: r["date"])

    def price_target(self, symbol):
        pt = self._fmp_or("price_target", symbol)
        if pt:
            return pt
        fd = self._summary(symbol).get("financialData") or {}
        if _raw(fd.get("targetMeanPrice")) is None:
            return None
        return {"consensus": _raw(fd.get("targetMeanPrice")), "median": _raw(fd.get("targetMedianPrice")),
                "high": _raw(fd.get("targetHighPrice")), "low": _raw(fd.get("targetLowPrice")),
                "n_analysts": _raw(fd.get("numberOfAnalystOpinions")), "recommendation": fd.get("recommendationKey")}


# --------------------------------------------------------------------------------------
# OHLC candles (for the candlestick chart). interval: 5m, 15m, 60m (intraday) or 1d, 1wk, 1mo.
# Daily-type candles use "YYYY-MM-DD" times; intraday candles use exchange-local unix seconds.
# --------------------------------------------------------------------------------------
INTRADAY = ("5m", "15m", "60m")
CANDLE_INTERVALS = INTRADAY + ("1d", "1wk", "1mo")


def resample_candles(daily: list[dict], rule: str) -> list[dict]:
    """Aggregate daily candles into weekly ('W') or monthly ('M') candles."""
    if not daily:
        return []
    df = pd.DataFrame(daily)
    df.index = pd.to_datetime(df["t"])
    freq = "W-FRI" if rule == "W" else "ME"
    try:
        g = df.resample(freq)
    except ValueError:                                # older pandas uses "M"
        g = df.resample("M")
    out = pd.DataFrame({"o": g["o"].first(), "h": g["h"].max(), "l": g["l"].min(), "c": g["c"].last(), "v": g["v"].sum()}).dropna(subset=["c"])
    first_day = g["t"].first()
    return [{"t": first_day[i], "o": float(r.o), "h": float(r.h), "l": float(r.l), "c": float(r.c), "v": float(r.v or 0)}
            for i, r in out.iterrows()]

_provider = None


def get_provider():
    global _provider
    if _provider is None:
        if config.DATA_MODE == "fmp":
            _provider = FMPProvider(config.FMP_API_KEY)
        elif config.DATA_MODE == "live":
            _provider = LiveProvider(FMPProvider(config.FMP_API_KEY) if config.FMP_API_KEY else None)
        else:
            _provider = SimProvider()
    return _provider


def set_provider(p) -> None:
    global _provider
    _provider = p
