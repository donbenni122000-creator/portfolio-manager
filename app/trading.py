"""Paper-trading engine: market, limit and stop orders against live (or simulated) quotes.
No real money moves. Cash, positions, average cost and realized P&L are tracked in SQLite."""
from __future__ import annotations

import math

from . import config, db, portfolio, tax
from .market_data import DataError, get_provider

SIDES = {"buy", "sell"}
TYPES = {"market", "limit", "stop"}


class OrderError(ValueError):
    pass


def _round_qty(q: float) -> float:
    return math.floor(q * 10000) / 10000 if config.ALLOW_FRACTIONAL else float(math.floor(q))


def _position(client_id: int, symbol: str):
    return db.query_one("SELECT * FROM positions WHERE client_id=? AND symbol=?", (client_id, symbol))


def _reserved_sell_qty(client_id: int, symbol: str) -> float:
    r = db.query_one("SELECT COALESCE(SUM(qty),0) AS q FROM orders WHERE client_id=? AND symbol=? AND side='sell' AND status='open'",
                     (client_id, symbol))
    return r["q"]


def _reserved_buy_cash(client_id: int) -> float:
    r = db.query_one("""SELECT COALESCE(SUM(qty * COALESCE(limit_price, stop_price, 0)),0) AS c
                        FROM orders WHERE client_id=? AND side='buy' AND status='open'""", (client_id,))
    return r["c"]


def place_order(client_id: int, symbol: str, side: str, qty: float | None = None, notional: float | None = None,
                order_type: str = "market", limit_price: float | None = None, stop_price: float | None = None,
                source: str = "manual", reason: str | None = None, lot_method: str | None = None) -> dict:
    client = portfolio.get_client(client_id)
    symbol, side, order_type = symbol.upper().strip(), side.lower(), order_type.lower()
    if side not in SIDES:
        raise OrderError("side must be 'buy' or 'sell'")
    if order_type not in TYPES:
        raise OrderError("order_type must be market, limit or stop")
    if order_type == "limit" and not (limit_price and limit_price > 0):
        raise OrderError("Limit orders need a positive limit_price")
    if order_type == "stop" and not (stop_price and stop_price > 0):
        raise OrderError("Stop orders need a positive stop_price")
    try:
        quote = get_provider().quote(symbol)
    except DataError as e:
        raise OrderError(f"Cannot trade {symbol}: {e}") from e
    price = quote["price"]
    if not price or price <= 0:
        raise OrderError(f"No valid price for {symbol}")

    if qty is None:
        if not notional or notional <= 0:
            raise OrderError("Provide qty or a positive notional ($ amount)")
        ref = limit_price or stop_price or price
        qty = notional / ref
    qty = _round_qty(float(qty))
    if qty <= 0:
        raise OrderError("Quantity rounds to zero" + ("" if config.ALLOW_FRACTIONAL else " (fractional shares disabled)"))

    from . import lending, options
    if side == "sell":
        pos = _position(client_id, symbol)
        covered = options.covered_shares(client_id, symbol)
        available = (pos["qty"] if pos else 0) - _reserved_sell_qty(client_id, symbol) - covered
        if qty > available + 1e-9:
            extra = f" ({covered:g} shares are covering short calls)" if covered else ""
            raise OrderError(f"Cannot sell {qty} {symbol}: only {max(0, available):.4f} available (no short selling){extra}")
    else:
        est_price = limit_price if order_type == "limit" else (stop_price if order_type == "stop" else price * (1 + config.SLIPPAGE_BPS / 1e4))
        est_cost = qty * est_price + config.COMMISSION_PER_TRADE
        free_cash = lending.buying_power(client) - _reserved_buy_cash(client_id) - options.csp_reserve(client_id)
        if est_cost > free_cash + 1e-6:
            raise OrderError(f"Insufficient buying power: need ${est_cost:,.2f}, have ${free_cash:,.2f}")

    with db.tx() as c:
        if lot_method and lot_method.upper() not in tax.METHODS:
            raise OrderError(f"lot_method must be one of {', '.join(tax.METHODS)}")
        cur = c.execute("""INSERT INTO orders(client_id,symbol,side,qty,order_type,limit_price,stop_price,status,source,reason,created_at,lot_method)
                           VALUES(?,?,?,?,?,?,?,'open',?,?,?,?)""",
                        (client_id, symbol, side, qty, order_type, limit_price, stop_price, source, reason, db.now_iso(),
                         lot_method.upper() if lot_method else None))
        oid = cur.lastrowid
    order = get_order(oid)
    _try_fill(order, price)
    return get_order(oid)


def _try_fill(order: dict, price: float) -> bool:
    t, side = order["order_type"], order["side"]
    slip = config.SLIPPAGE_BPS / 1e4
    fill = None
    if t == "market":
        fill = price * (1 + slip) if side == "buy" else price * (1 - slip)
    elif t == "limit":
        if side == "buy" and price <= order["limit_price"]:
            fill = min(price, order["limit_price"])
        elif side == "sell" and price >= order["limit_price"]:
            fill = max(price, order["limit_price"])
    elif t == "stop":
        if side == "sell" and price <= order["stop_price"]:
            fill = price * (1 - slip)
        elif side == "buy" and price >= order["stop_price"]:
            fill = price * (1 + slip)
    if fill is None:
        return False
    _execute_fill(order, round(fill, 4))
    return True


def _execute_fill(order: dict, fill: float, allow_margin: bool = False) -> None:
    cid, sym, qty = order["client_id"], order["symbol"], order["qty"]
    fee = config.COMMISSION_PER_TRADE
    client = portfolio.get_client(cid)
    pos = _position(cid, sym)
    with db.tx() as c:
        if order["side"] == "buy":
            cost = qty * fill + fee
            from . import lending
            limit = client["cash"] if not (client.get("margin_enabled") or allow_margin) else \
                (lending.buying_power(client) if not allow_margin else float("inf"))
            if cost > limit + 1e-6:
                c.execute("UPDATE orders SET status='rejected', reason=? WHERE id=?",
                          ("Insufficient cash at fill time", order["id"]))
                return
            if pos:
                new_qty = pos["qty"] + qty
                new_avg = (pos["qty"] * pos["avg_cost"] + qty * fill) / new_qty if new_qty > 0 else fill
                c.execute("UPDATE positions SET qty=?, avg_cost=? WHERE client_id=? AND symbol=?", (new_qty, new_avg, cid, sym))
            else:
                c.execute("INSERT INTO positions(client_id,symbol,qty,avg_cost,realized_pnl,opened_at) VALUES(?,?,?,?,0,?)",
                          (cid, sym, qty, fill, db.now_iso()))
            c.execute("UPDATE clients SET cash = cash - ? WHERE id=?", (cost, cid))
            tax.open_lot(c, cid, sym, qty, fill, order["id"])
            realized = None
        else:
            if not pos or pos["qty"] + 1e-9 < qty:
                c.execute("UPDATE orders SET status='rejected', reason=? WHERE id=?", ("Position no longer available", order["id"]))
                return
            method = (order.get("lot_method") or client.get("lot_method") or "FIFO").upper()
            realized = tax.relieve_lots(c, cid, sym, qty, fill, fee, method, order["id"])
            new_qty = pos["qty"] - qty
            if new_qty < 1e-9:
                new_qty = 0.0
            c.execute("UPDATE positions SET qty=?, realized_pnl = realized_pnl + ? WHERE client_id=? AND symbol=?",
                      (new_qty, realized, cid, sym))
            c.execute("UPDATE clients SET cash = cash + ? WHERE id=?", (qty * fill - fee, cid))
        c.execute("UPDATE orders SET status='filled', fill_price=?, commission=?, realized_pnl=?, filled_at=? WHERE id=?",
                  (fill, fee, realized, db.now_iso(), order["id"]))


def process_open_orders(client_id: int | None = None) -> int:
    sql = "SELECT * FROM orders WHERE status='open'" + (" AND client_id=?" if client_id else "") + " ORDER BY id"
    orders = db.query(sql, (client_id,) if client_id else ())
    filled = 0
    for o in orders:
        try:
            q = get_provider().quote(o["symbol"])
        except DataError:
            continue
        if _try_fill(o, q["price"]):
            filled += 1
    return filled


def cancel_order(client_id: int, order_id: int) -> dict:
    o = get_order(order_id)
    if not o or o["client_id"] != client_id:
        raise OrderError("Order not found")
    if o["status"] != "open":
        raise OrderError(f"Order is already {o['status']}")
    with db.tx() as c:
        c.execute("UPDATE orders SET status='cancelled' WHERE id=?", (order_id,))
    return get_order(order_id)


def get_order(order_id: int):
    return db.query_one("SELECT * FROM orders WHERE id=?", (order_id,))


def list_orders(client_id: int, status: str | None = None, limit: int = 200) -> list[dict]:
    if status:
        return db.query("SELECT * FROM orders WHERE client_id=? AND status=? ORDER BY id DESC LIMIT ?", (client_id, status, limit))
    return db.query("SELECT * FROM orders WHERE client_id=? ORDER BY id DESC LIMIT ?", (client_id, limit))
