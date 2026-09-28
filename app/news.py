"""News desk: Wall Street Journal section headlines (Dow Jones' public RSS feeds) plus per-ticker headlines
(Yahoo Finance RSS). Only headlines, summaries and links are shown; the full article opens on wsj.com (or the
publisher's site), where the user's own WSJ subscription/login applies. Nothing here logs into WSJ.
"""
from __future__ import annotations

import html
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime

import requests

from . import config
from .market_data import _cache_get, _cache_put

WSJ = "https://feeds.content.dowjones.io/public/rss/"
SECTIONS = {
    "markets": ("Markets", "RSSMarketsMain"),
    "business": ("Business", "WSJcomUSBusiness"),
    "economy": ("Economy", "socialeconomyfeed"),
    "world": ("World", "RSSWorldNews"),
    "tech": ("Technology", "RSSWSJD"),
    "personal-finance": ("Personal Finance", "RSSPersonalFinance"),
    "us": ("U.S.", "RSSUSnews"),
    "politics": ("Politics", "socialpoliticsfeed"),
    "opinion": ("Opinion", "RSSOpinion"),
}
DEFAULT_MIX = ("markets", "business", "economy", "world", "tech")
TTL = 10 * 60
MEDIA = "{http://search.yahoo.com/mrss/}"
DC = "{http://purl.org/dc/elements/1.1/}"
_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                       "Chrome/126.0 Safari/537.36", "Accept": "application/rss+xml, application/xml, text/xml"})
_lock = threading.Lock()


class NewsError(Exception):
    pass


def _clean(text: str | None) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def parse_rss(xml_text: str, source: str, section: str | None = None) -> list[dict]:
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError as e:
        raise NewsError(f"Could not read the {source} feed: {e}")
    items = []
    for it in root.iter("item"):
        title = _clean(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        if not title or not link.startswith("http"):
            continue
        ts = None
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
        except (TypeError, ValueError):
            pass
        img = None
        mc = it.find(MEDIA + "content")
        if mc is None:
            mc = it.find(MEDIA + "thumbnail")
        if mc is not None and mc.get("url"):
            img = mc.get("url")
        authors = [_clean(a.text) for a in it.findall(DC + "creator") if a.text]
        items.append({"title": title, "summary": _clean(it.findtext("description"))[:320], "url": link, "ts": ts,
                      "image": img, "authors": authors, "source": source, "section": section,
                      "publisher": _publisher(link, source)})
    return items


def _publisher(url: str, fallback: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/]+)", url)
    host = m.group(1) if m else ""
    known = {"wsj.com": "WSJ", "barrons.com": "Barron's", "marketwatch.com": "MarketWatch", "finance.yahoo.com": "Yahoo Finance",
             "barchart.com": "Barchart", "fool.com": "Motley Fool", "investors.com": "IBD", "reuters.com": "Reuters",
             "bloomberg.com": "Bloomberg", "cnbc.com": "CNBC", "zacks.com": "Zacks", "benzinga.com": "Benzinga",
             "thestreet.com": "TheStreet", "seekingalpha.com": "Seeking Alpha", "investing.com": "Investing.com"}
    for k, v in known.items():
        if host.endswith(k):
            return v
    return fallback


def _fetch(url: str, source: str, section: str | None = None) -> list[dict]:
    key = "news:" + url
    cached = _cache_get(key, TTL)
    if cached is not None:
        return cached
    try:
        r = _session.get(url, timeout=12)
    except requests.RequestException as e:
        raise NewsError(f"Couldn't reach {source} ({e.__class__.__name__}). Check your internet connection.")
    if r.status_code >= 400:
        raise NewsError(f"{source} feed returned {r.status_code}")
    items = parse_rss(r.text, source, section)
    _cache_put(key, items)
    return items


def wsj(section: str) -> list[dict]:
    if section not in SECTIONS:
        raise ValueError(f"Unknown section '{section}'. Choose: {', '.join(SECTIONS)}")
    label, feed = SECTIONS[section]
    return _fetch(WSJ + feed, "WSJ", label)


def ticker(symbol: str, limit: int = 12) -> list[dict]:
    from .market_data import to_yahoo
    ys = to_yahoo(symbol.upper())
    items = _fetch(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={requests.utils.quote(ys)}&region=US&lang=en-US",
                   "Yahoo Finance", symbol.upper())
    return _sorted(items)[:limit]


def _sorted(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in sorted(items, key=lambda x: -(x.get("ts") or 0)):
        k = re.sub(r"\W+", "", it["title"].lower())[:80]
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def desk(section: str = "top", limit: int = 60) -> dict:
    """WSJ headlines for one section, or a 'top' mix of the main business sections."""
    names = DEFAULT_MIX if section == "top" else (section,)
    errors, items = [], []
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(lambda s: _safe(wsj, s), names))
    for s, (rows, err) in zip(names, results):
        items += rows
        if err:
            errors.append(f"{SECTIONS[s][0]}: {err}")
    items = _sorted(items)[:limit]
    return {"section": section, "sections": [{"key": "top", "label": "Top stories"}] + [{"key": k, "label": v[0]} for k, v in SECTIONS.items()],
            "items": items, "errors": errors, "as_of": time.time(),
            "note": "Headlines from WSJ's public feeds. Full articles open on wsj.com — sign in there with your WSJ subscription."}


def watchlist_news(symbols: list[str], per_symbol: int = 4, limit: int = 30) -> list[dict]:
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(lambda s: _safe(ticker, s, per_symbol), symbols[:12]))
    rows = []
    for s, (items, _err) in zip(symbols, results):
        for it in items:
            rows.append({**it, "symbol": s})
    return _sorted(rows)[:limit]


def _safe(fn, *a):
    try:
        return fn(*a), None
    except (NewsError, ValueError, requests.RequestException) as e:
        return [], str(e)
