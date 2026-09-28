"""SQLite persistence. One file, no server. Tables are created on first run."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT,
    risk_score REAL NOT NULL,
    risk_profile TEXT NOT NULL,
    questionnaire TEXT,                 -- JSON answers
    starting_cash REAL NOT NULL,
    cash REAL NOT NULL,
    drift_abs_band REAL NOT NULL DEFAULT 0.05,   -- 5 percentage points
    drift_rel_band REAL NOT NULL DEFAULT 0.25,   -- 25% of target weight
    cash_target REAL NOT NULL DEFAULT 0.02,
    stop_loss_pct REAL NOT NULL DEFAULT 0.20,
    max_position REAL NOT NULL DEFAULT 0.10,
    notes TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS targets (
    client_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    weight REAL NOT NULL,
    asset_class TEXT,
    PRIMARY KEY (client_id, symbol)
);
CREATE TABLE IF NOT EXISTS positions (
    client_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    qty REAL NOT NULL,
    avg_cost REAL NOT NULL,
    realized_pnl REAL NOT NULL DEFAULT 0,
    opened_at TEXT,
    PRIMARY KEY (client_id, symbol)
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,                 -- buy / sell
    qty REAL NOT NULL,
    order_type TEXT NOT NULL,           -- market / limit / stop
    limit_price REAL,
    stop_price REAL,
    status TEXT NOT NULL,               -- open / filled / cancelled / rejected
    fill_price REAL,
    commission REAL DEFAULT 0,
    realized_pnl REAL,
    source TEXT DEFAULT 'manual',       -- manual / rebalance / stop-loss
    reason TEXT,
    created_at TEXT NOT NULL,
    filled_at TEXT
);
CREATE TABLE IF NOT EXISTS cash_flows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    client_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    equity REAL NOT NULL,
    cash REAL NOT NULL,
    net_flows REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (client_id, date)
);
CREATE TABLE IF NOT EXISTS research_cache (
    symbol TEXT PRIMARY KEY,
    rating TEXT,
    fair_value REAL,
    score REAL,
    payload TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS fee_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    tiers TEXT NOT NULL,                -- JSON [[upper_bound_or_null, annual_rate], ...] marginal tiers
    description TEXT,
    builtin INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    days INTEGER NOT NULL,
    avg_aum REAL NOT NULL,
    effective_rate REAL NOT NULL,
    fee REAL NOT NULL,
    status TEXT NOT NULL,               -- paid / unpaid / waived
    schedule TEXT,
    created_at TEXT NOT NULL,
    paid_at TEXT,
    UNIQUE (client_id, period_start, period_end)
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    kind TEXT NOT NULL,                 -- interest / fee / margin_interest / lending_income / ...
    amount REAL NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    qty_open REAL NOT NULL,
    qty_orig REAL NOT NULL,
    cost_per_share REAL NOT NULL,
    acquired_at TEXT NOT NULL,
    order_id INTEGER
);
CREATE TABLE IF NOT EXISTS realized (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    qty REAL NOT NULL,
    proceeds REAL NOT NULL,
    cost REAL NOT NULL,
    gain REAL NOT NULL,
    acquired_at TEXT NOT NULL,
    sold_at TEXT NOT NULL,
    term TEXT NOT NULL,                 -- ST / LT
    lot_id INTEGER,
    order_id INTEGER
);
CREATE TABLE IF NOT EXISTS models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT,
    description TEXT,
    holdings TEXT NOT NULL,             -- JSON {symbol: weight}
    builtin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    target_amount REAL NOT NULL,
    target_date TEXT NOT NULL,
    monthly_contribution REAL NOT NULL DEFAULT 0,
    allocation_pct REAL NOT NULL DEFAULT 1.0,
    inflation_adjust INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS direct_index (
    client_id INTEGER PRIMARY KEY,
    index_name TEXT NOT NULL DEFAULT 'S&P 500',
    sleeve_weight REAL NOT NULL,        -- share of invested assets (replaces core US equity)
    top_n INTEGER NOT NULL DEFAULT 50,
    excluded_sectors TEXT NOT NULL DEFAULT '[]',
    excluded_symbols TEXT NOT NULL DEFAULT '[]',
    holdings TEXT NOT NULL DEFAULT '{}',   -- last applied {symbol: weight of portfolio}
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS loans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'sbloc',
    balance REAL NOT NULL,
    rate REAL NOT NULL,
    last_accrual TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS loan_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_id INTEGER NOT NULL,
    client_id INTEGER NOT NULL,
    kind TEXT NOT NULL,                 -- draw / repay / interest
    amount REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS option_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    underlying TEXT NOT NULL,
    opt_type TEXT NOT NULL,             -- C / P
    strike REAL NOT NULL,
    expiry TEXT NOT NULL,
    qty INTEGER NOT NULL,               -- contracts: + long, - short
    avg_price REAL NOT NULL,            -- per share (1 contract = 100 shares)
    realized_pnl REAL NOT NULL DEFAULT 0,
    opened_at TEXT NOT NULL,
    UNIQUE (client_id, underlying, opt_type, strike, expiry)
);
CREATE TABLE IF NOT EXISTS option_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    underlying TEXT NOT NULL,
    opt_type TEXT NOT NULL,
    strike REAL NOT NULL,
    expiry TEXT NOT NULL,
    action TEXT NOT NULL,               -- BTO / STC / STO / BTC / EXPIRE / EXERCISE / ASSIGN
    qty INTEGER NOT NULL,
    price REAL NOT NULL,
    cash_effect REAL NOT NULL,
    realized_pnl REAL,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlist_items (
    watchlist_id INTEGER NOT NULL REFERENCES watchlists(id),
    symbol TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL,
    PRIMARY KEY (watchlist_id, symbol)
);
CREATE TABLE IF NOT EXISTS http_cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    ts REAL NOT NULL
);
"""

_conn = None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            path = config.DB_PATH
            if str(path) != ":memory:":
                path.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(str(path), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL" if str(path) != ":memory:" else "PRAGMA journal_mode=MEMORY")
            _conn.executescript(SCHEMA)
            _migrate(_conn)
            _conn.commit()
        return _conn


# columns added after v1.0 - (table, column, SQL type/default)
_ADDED_COLUMNS = [
    ("clients", "fee_schedule_id", "INTEGER"),
    ("clients", "last_interest_date", "TEXT"),
    ("clients", "lot_method", "TEXT NOT NULL DEFAULT 'FIFO'"),
    ("clients", "st_tax_rate", "REAL NOT NULL DEFAULT 0.32"),
    ("clients", "lt_tax_rate", "REAL NOT NULL DEFAULT 0.15"),
    ("clients", "model_id", "INTEGER"),
    ("orders", "lot_method", "TEXT"),
    ("clients", "account_type", "TEXT NOT NULL DEFAULT 'Individual'"),
    ("clients", "beneficiaries", "TEXT NOT NULL DEFAULT '[]'"),
    ("clients", "phone", "TEXT"),
    ("clients", "objective", "TEXT"),
    ("clients", "margin_enabled", "INTEGER NOT NULL DEFAULT 0"),
    ("clients", "margin_rate", "REAL NOT NULL DEFAULT 0.085"),
    ("clients", "sbloc_rate", "REAL NOT NULL DEFAULT 0.065"),
    ("clients", "sec_lending", "INTEGER NOT NULL DEFAULT 0"),
    ("clients", "options_level", "INTEGER NOT NULL DEFAULT 0"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent upgrades so older databases keep working after an update."""
    for table, col, decl in _ADDED_COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    today = datetime.now(timezone.utc).date().isoformat()
    conn.execute("UPDATE clients SET last_interest_date=? WHERE last_interest_date IS NULL", (today,))
    # positions opened before tax lots existed -> one lot each at average cost
    for p in conn.execute("""SELECT p.client_id, p.symbol, p.qty, p.avg_cost, p.opened_at FROM positions p
                             WHERE p.qty > 1e-9 AND NOT EXISTS (SELECT 1 FROM lots l WHERE l.client_id=p.client_id
                             AND l.symbol=p.symbol)""").fetchall():
        conn.execute("INSERT INTO lots(client_id,symbol,qty_open,qty_orig,cost_per_share,acquired_at) VALUES(?,?,?,?,?,?)",
                     (p[0], p[1], p[2], p[2], p[3], p[4] or now_iso()))


def reset_connection() -> None:
    """Used by tests to point at a fresh database."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


@contextmanager
def tx():
    conn = get_conn()
    with _lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def query(sql: str, params=()) -> list[dict]:
    with _lock:
        return [dict(r) for r in get_conn().execute(sql, params).fetchall()]


def query_one(sql: str, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def dumps(obj) -> str:
    return json.dumps(obj, default=str)
