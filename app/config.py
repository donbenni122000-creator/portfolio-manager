"""Central configuration. Values come from environment variables or a local .env file."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader (KEY=VALUE per line) so no extra dependency is needed."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

APP_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip() if (ROOT / "VERSION").exists() else "dev"


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


FMP_API_KEY = os.environ.get("FMP_API_KEY", "").strip()
# "live" = real prices from Yahoo Finance (free) + FMP financials when a key exists (default)
# "fmp"  = everything from Financial Modeling Prep,  "sim" = simulated offline market
_mode = os.environ.get("DATA_MODE", "auto").lower()
DATA_MODE = "live" if _mode in ("auto", "", "live", "yahoo") else _mode

# Simulated and live accounts are kept in separate databases so fake fills never mix with real prices.
_default_db = ROOT / "data" / ("portfolio.db" if DATA_MODE == "sim" else "portfolio-live.db")
DB_PATH = Path(os.environ.get("DB_PATH", _default_db))
SIM_DB_PATH = ROOT / "data" / "portfolio.db"

# Branding / personalisation (shown on the welcome briefing and in reports)
BRAND_NAME = os.environ.get("BRAND_NAME", "Redline").strip() or "Redline"
OWNER_NAME = os.environ.get("OWNER_NAME", "Don").strip() or "Don"

# Market / valuation assumptions (editable)
RISK_FREE_RATE = _f("RISK_FREE_RATE", 0.0425)      # ~10Y UST
EQUITY_RISK_PREMIUM = _f("EQUITY_RISK_PREMIUM", 0.050)
# Local-currency 10-year government yields used as the risk-free rate for stocks priced in that currency
# (USD uses the live 10-year Treasury yield, ^TNX, when available). Approximate defaults - override in .env.
RISK_FREE_BY_CCY = {"INR": _f("RISK_FREE_INR", 0.065), "EUR": _f("RISK_FREE_EUR", 0.027), "GBP": _f("RISK_FREE_GBP", 0.045),
                    "GBp": _f("RISK_FREE_GBP", 0.045), "JPY": _f("RISK_FREE_JPY", 0.015), "CAD": _f("RISK_FREE_CAD", 0.033),
                    "AUD": _f("RISK_FREE_AUD", 0.043), "CHF": _f("RISK_FREE_CHF", 0.007), "HKD": _f("RISK_FREE_HKD", 0.035),
                    "CNY": _f("RISK_FREE_CNY", 0.018), "TWD": _f("RISK_FREE_TWD", 0.016), "KRW": _f("RISK_FREE_KRW", 0.030)}
# nominal long-run growth differs by currency (inflation): used for the DCF terminal growth
TERMINAL_GROWTH_BY_CCY = {"INR": _f("TERMINAL_GROWTH_INR", 0.045), "JPY": 0.010, "CHF": 0.010, "EUR": 0.020, "CNY": 0.030,
                          "BRL": 0.040}
# extra equity risk premium for emerging-market listings (country risk premium)
COUNTRY_RISK_BY_CCY = {"INR": _f("COUNTRY_RISK_INR", 0.022), "CNY": 0.010, "HKD": 0.006, "TWD": 0.008, "KRW": 0.007, "BRL": 0.030}
TERMINAL_GROWTH = _f("TERMINAL_GROWTH", 0.025)
BENCHMARK = os.environ.get("BENCHMARK", "SPY")

# Paper-trading frictions
SLIPPAGE_BPS = _f("SLIPPAGE_BPS", 5)                # applied against you on market orders
COMMISSION_PER_TRADE = _f("COMMISSION_PER_TRADE", 0.0)
ALLOW_FRACTIONAL = os.environ.get("ALLOW_FRACTIONAL", "true").lower() == "true"
MIN_TRADE_VALUE = _f("MIN_TRADE_VALUE", 25.0)

# High-yield cash sweep: annual rate paid on uninvested cash (accrued daily, compounded)
CASH_INTEREST_RATE = _f("CASH_INTEREST_RATE", 0.04)
# Default fee schedule for new clients (name must match a schedule in the Billing page)
DEFAULT_FEE_SCHEDULE = os.environ.get("DEFAULT_FEE_SCHEDULE", "Standard 1.00%")

# Background monitor loop (seconds). 0 disables.
MONITOR_INTERVAL = int(_f("MONITOR_INTERVAL", 60))
