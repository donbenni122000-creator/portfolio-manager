"""Tax lots, realized gains, and tax-loss harvesting.

* Every buy opens a tax lot; every sell relieves lots using the client's method (FIFO, LIFO or HIFO -
  highest cost first, which minimises gains) or an order-level override.
* Realized gains are split short-term (held <= 1 year) / long-term.
* Wash sales: a realized loss is flagged if the same security was bought within 30 days before or after.
* Tax-loss harvesting sells lots at a loss and buys a similar-but-not-identical replacement so market
  exposure is kept; the client's target model is switched to the replacement so rebalancing won't undo it.
Estimates only - not tax advice.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import db, portfolio, risk_profile
from .market_data import DataError, get_provider

METHODS = ("FIFO", "LIFO", "HIFO")
LOSS_MIN_DOLLARS = 200.0
LOSS_MIN_PCT = 0.05

# Similar exposure, different index/issuer (to avoid "substantially identical")
REPLACEMENTS = {
    "VTI": "SCHB", "ITOT": "VTI", "SCHB": "ITOT", "SPY": "IVV", "IVV": "SPLG", "SPLG": "SPY", "VOO": "IVV",
    "VXUS": "IXUS", "IXUS": "VXUS", "VEA": "IEFA", "VWO": "IEMG", "IEMG": "VWO", "QQQ": "VGT", "VGT": "QQQ",
    "BND": "AGG", "AGG": "BND", "TIP": "SCHP", "SCHP": "TIP", "VNQ": "SCHH", "SCHH": "VNQ", "GLD": "IAU",
    "IAU": "GLD", "SHV": "BIL", "BIL": "SHV", "SCHD": "VYM", "VYM": "SCHD", "VIG": "DGRO", "DGRO": "VIG",
    "TLT": "VGLT", "IEF": "VGIT", "VCIT": "IGIB", "ESGU": "SUSA", "ESGD": "SUSL", "SMH": "SOXX", "SOXX": "SMH",
}
SECTOR_ETF = {"Technology": "XLK", "Financial Services": "XLF", "Healthcare": "XLV", "Consumer Cyclical": "XLY",
              "Consumer Defensive": "XLP", "Energy": "XLE", "Communication Services": "XLC", "Industrials": "XLI",
              "Utilities": "XLU", "Real Estate": "XLRE", "Basic Materials": "XLB"}


def _d(s: str) -> date:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).date() if "T" in s else date.fromisoformat(s[:10])


# ------------------------------------------------------------------ lot bookkeeping (called inside a transaction)
def open_lot(c, client_id: int, symbol: str, qty: float, price: float, order_id: int | None, when: str | None = None):
    c.execute("INSERT INTO lots(client_id,symbol,qty_open,qty_orig,cost_per_share,acquired_at,order_id) VALUES(?,?,?,?,?,?,?)",
              (client_id, symbol, qty, qty, price, when or db.now_iso(), order_id))


def relieve_lots(c, client_id: int, symbol: str, qty: float, price: float, commission: float,
                 method: str, order_id: int | None) -> float:
    """Consume open lots for a sale; records realized rows; returns total realized gain."""
    order = {"FIFO": "acquired_at ASC, id ASC", "LIFO": "acquired_at DESC, id DESC",
             "HIFO": "cost_per_share DESC, id ASC"}.get(method, "acquired_at ASC, id ASC")
    lots = c.execute(f"SELECT * FROM lots WHERE client_id=? AND symbol=? AND qty_open > 1e-9 ORDER BY {order}",
                     (client_id, symbol)).fetchall()
    remaining, total_gain, now = qty, 0.0, db.now_iso()
    for lot in lots:
        if remaining <= 1e-9:
            break
        take = min(remaining, lot["qty_open"])
        fee_share = commission * take / qty if qty else 0
        proceeds = take * price - fee_share
        cost = take * lot["cost_per_share"]
        held = (date.today() - _d(lot["acquired_at"])).days
        c.execute("""INSERT INTO realized(client_id,symbol,qty,proceeds,cost,gain,acquired_at,sold_at,term,lot_id,order_id)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                  (client_id, symbol, take, proceeds, cost, proceeds - cost, lot["acquired_at"], now,
                   "LT" if held > 365 else "ST", lot["id"], order_id))
        c.execute("UPDATE lots SET qty_open = qty_open - ? WHERE id=?", (take, lot["id"]))
        total_gain += proceeds - cost
        remaining -= take
    if remaining > 1e-6:      # legacy shares without lots: treat at zero-gain
        pass
    return total_gain


# ------------------------------------------------------------------ reporting
def open_lots(client_id: int) -> list[dict]:
    lots = db.query("SELECT * FROM lots WHERE client_id=? AND qty_open > 1e-9 ORDER BY symbol, acquired_at", (client_id,))
    quotes = get_provider().quotes(list({l["symbol"] for l in lots})) if lots else {}
    today = date.today()
    for l in lots:
        q = quotes.get(l["symbol"])
        px = q["price"] if q else l["cost_per_share"]
        held = (today - _d(l["acquired_at"])).days
        l.update(price=px, value=px * l["qty_open"], cost=l["cost_per_share"] * l["qty_open"],
                 unrealized=(px - l["cost_per_share"]) * l["qty_open"],
                 unrealized_pct=px / l["cost_per_share"] - 1 if l["cost_per_share"] else 0,
                 days_held=held, term="LT" if held > 365 else "ST", days_to_lt=max(0, 366 - held))
    return lots


def _wash_flags(client_id: int, rows: list[dict]) -> None:
    buys = db.query("SELECT symbol, acquired_at, id, qty_open FROM lots WHERE client_id=?", (client_id,))
    for r in rows:
        r["wash_sale"] = False
        if r["gain"] >= 0:
            continue
        sold = _d(r["sold_at"])
        for b in buys:
            # a purchase within +/-30 days that is still held (or bought after the sale) is a replacement
            bought = _d(b["acquired_at"])
            still_held = b["qty_open"] > 1e-9 or bought > sold
            if (b["symbol"] == r["symbol"] and b["id"] != r["lot_id"] and still_held
                    and abs((bought - sold).days) <= 30):
                r["wash_sale"] = True
                break


def report(client_id: int, year: int | None = None) -> dict:
    client = portfolio.get_client(client_id)
    year = year or date.today().year
    rows = db.query("SELECT * FROM realized WHERE client_id=? AND substr(sold_at,1,4)=? ORDER BY sold_at DESC",
                    (client_id, str(year)))
    _wash_flags(client_id, rows)
    st = sum(r["gain"] for r in rows if r["term"] == "ST" and not (r["wash_sale"] and r["gain"] < 0))
    lt = sum(r["gain"] for r in rows if r["term"] == "LT" and not (r["wash_sale"] and r["gain"] < 0))
    disallowed = -sum(r["gain"] for r in rows if r["wash_sale"] and r["gain"] < 0)
    # netting: losses in one bucket offset gains in the other
    net_st, net_lt = st, lt
    if (st < 0 < lt) or (lt < 0 < st):
        total = st + lt                       # opposite signs: net them against each other
        if total >= 0:
            net_st, net_lt = (0.0, total) if lt > 0 else (total, 0.0)
        else:
            net_st, net_lt = (total, 0.0) if st < 0 else (0.0, total)
    est_tax = max(0.0, net_st) * client["st_tax_rate"] + max(0.0, net_lt) * client["lt_tax_rate"]
    net_loss = min(0.0, net_st) + min(0.0, net_lt)
    from . import accounts
    taxable = accounts.is_taxable(client)
    if not taxable:
        est_tax = 0.0
    lots = open_lots(client_id)
    unreal_st = sum(l["unrealized"] for l in lots if l["term"] == "ST")
    unreal_lt = sum(l["unrealized"] for l in lots if l["term"] == "LT")
    return {
        "year": year, "taxable": taxable, "account_type": client["account_type"],
        "tax_status": accounts.rules(client)["tax"], "lot_method": client["lot_method"], "st_rate": client["st_tax_rate"], "lt_rate": client["lt_tax_rate"],
        "realized": rows, "short_term": st, "long_term": lt, "wash_sale_disallowed": disallowed,
        "estimated_tax": est_tax, "deductible_loss": max(net_loss, -3000.0), "loss_carryforward": min(0.0, net_loss + 3000.0),
        "unrealized_short_term": unreal_st, "unrealized_long_term": unreal_lt, "lots": lots,
        "harvest": harvest_candidates(client_id, lots) if taxable else [],
    }


def _replacement(symbol: str, client_id: int | None = None) -> str | None:
    if client_id:
        from . import direct_index
        peer = direct_index.peer_replacement(client_id, symbol)
        if peer:
            return peer
    if symbol in REPLACEMENTS:
        return REPLACEMENTS[symbol]
    try:
        prof = get_provider().profile(symbol)
        return SECTOR_ETF.get(prof.get("sector") or "")
    except DataError:
        return None


def harvest_candidates(client_id: int, lots: list[dict] | None = None) -> list[dict]:
    client = portfolio.get_client(client_id)
    from . import accounts
    if not accounts.is_taxable(client):
        return []
    lots = lots if lots is not None else open_lots(client_id)
    recent = date.today() - timedelta(days=30)
    by_sym: dict[str, list] = {}
    for l in lots:
        if l["unrealized"] < 0:
            by_sym.setdefault(l["symbol"], []).append(l)
    out = []
    for sym, ls in by_sym.items():
        loss_lots = [l for l in ls if l["unrealized_pct"] <= -LOSS_MIN_PCT]
        loss = sum(l["unrealized"] for l in loss_lots)
        if -loss < LOSS_MIN_DOLLARS:
            continue
        st_loss = sum(l["unrealized"] for l in loss_lots if l["term"] == "ST")
        savings = -st_loss * client["st_tax_rate"] + -(loss - st_loss) * client["lt_tax_rate"]
        # replacement shares = recently bought lots of the same security that we would NOT be selling
        ids = [l["id"] for l in loss_lots]
        recent_buy = db.query_one(
            f"SELECT 1 FROM lots WHERE client_id=? AND symbol=? AND acquired_at>=? AND qty_open>1e-9 "
            f"AND id NOT IN ({','.join('?' * len(ids))})", (client_id, sym, recent.isoformat(), *ids))
        out.append({"symbol": sym, "qty": sum(l["qty_open"] for l in loss_lots), "loss": loss,
                    "lots": [l["id"] for l in loss_lots], "est_tax_savings": savings,
                    "replacement": _replacement(sym, client_id), "wash_sale_risk": bool(recent_buy),
                    "note": "Bought within the last 30 days - harvesting now may be a wash sale" if recent_buy else ""})
    return sorted(out, key=lambda r: r["loss"])


def harvest(client_id: int, symbol: str, replacement: str | None = None) -> dict:
    """Sell the loss lots of `symbol` (HIFO) and buy the replacement with the proceeds."""
    from . import trading
    symbol = symbol.upper()
    cand = next((c for c in harvest_candidates(client_id) if c["symbol"] == symbol), None)
    if not cand:
        raise ValueError(f"No harvestable loss in {symbol} right now")
    repl = (replacement or cand["replacement"] or "").upper()
    if not repl or repl == symbol:
        raise ValueError("Choose a replacement security")
    get_provider().quote(repl)
    sell = trading.place_order(client_id, symbol, "sell", qty=cand["qty"], source="tax-harvest",
                               reason=f"Tax-loss harvest into {repl}", lot_method="HIFO")
    if sell["status"] != "filled":
        raise ValueError(f"Harvest sell did not fill: {sell.get('reason')}")
    proceeds = sell["qty"] * sell["fill_price"]
    buy = trading.place_order(client_id, repl, "buy", notional=proceeds * 0.998, source="tax-harvest",
                              reason=f"Replacement for harvested {symbol}")
    # keep the model consistent so rebalancing doesn't buy the old security back
    t = portfolio.get_targets(client_id)
    swapped = symbol in t
    if swapped:
        w = t.pop(symbol)
        t[repl] = t.get(repl, 0) + w
        portfolio.set_targets(client_id, t)
    portfolio.record_snapshot(client_id)
    return {"sold": sell, "bought": buy, "realized_loss": sell.get("realized_pnl"),
            "est_tax_savings": cand["est_tax_savings"], "targets_swapped": swapped}
