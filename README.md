# Portfolio Management Platform

A local web app for managing multiple client portfolios with paper trading.

- **Risk profiling.** An 8-question questionnaire scores capacity and willingness. The result maps to one of five profiles (Conservative to Aggressive), each with its own model ETF portfolio, volatility band, drawdown tolerance and individual-stock limits.
- **Portfolio construction and monitoring.** The dashboard shows live valuation, time-weighted performance and look-through risk (volatility, Sharpe, Sortino, max drawdown, beta, VaR/CVaR, risk contribution, correlations). Alerts cover drift, concentration, stop-loss, drawdown, risk-vs-profile mismatch and research downgrades.
- **Stock research.** Each stock gets a 10-year FCF DCF (CAPM WACC, Gordon terminal value, bull/bear cases, sensitivity grid), peer-multiple valuation, a Piotroski F-score, ROIC and momentum/technicals. These combine into a **BUY / HOLD / SELL** rating with a fair value. The rating then becomes a client-specific action, sized within that client's profile limits.
- **Rebalancing.** Tolerance-band drift detection (absolute and relative bands). A trade list is generated in either *full* or *breached-only* mode, with sells first, a cash buffer and an estimate of realized gains. It can be executed as paper trades in one click.
- **Paper trading.** Market, limit and stop orders run against live quotes, with slippage and commission settings. Fractional shares are supported. Short selling is blocked. The app tracks average cost, realized and unrealized P&L and the full order history. Working orders are re-checked every 60 seconds.

> Paper trading only. No broker connection exists, so no real orders are ever sent.

## Desktop app (Windows)

1. Double-click **`Install Desktop Shortcut.vbs`** once. It puts a **Portfolio Manager** icon on your Desktop and in the Start menu.
2. Click the icon. The server starts silently in the background, and the app opens in its own window (Edge/Chrome app mode, no browser tabs).
3. To stop it, use **Quit Portfolio Manager** at the bottom of the sidebar, or run `Stop Portfolio Manager.vbs`.

On the first launch the icon runs `run.bat` visibly so you can watch the one-time package install. Server output goes to `data\server.log`. When the app is updated (the version is in the `VERSION` file), the icon restarts the server automatically.

## Quick start (Windows, manual)

1. Install Python 3.10+ (Anaconda works).
2. Prices are **live and free** out of the box (Yahoo Finance, no key; some exchanges ~15 min delayed).
   For the DCF stock research, copy `.env.example` to `.env` and add a free Financial Modeling Prep key in `FMP_API_KEY`.
   Set `DATA_MODE=sim` to use the offline **simulated market** instead (it keeps its own database).
3. Double-click **`run.bat`**. It creates a virtual environment, installs the requirements and starts the server.
4. Open http://localhost:8000.

macOS/Linux: `./run.sh`

Manual start:
```
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

## Wealth-management features (v1.5)

| Feature | Where | What it does |
|---|---|---|
| **Fee billing** | Billing page, client → Billing | Flat or tiered fee schedules (marginal tiers). Fees are billed in arrears on average daily assets, pro-rated by days, and deducted from cash. Also covers invoices (pay/waive), revenue projection, and never billing the same day twice. |
| **High-yield cash** | client → Billing | Uninvested cash earns `CASH_INTEREST_RATE` (default 4.00% APY), accrued daily. Interest and fees count as performance, not deposits. |
| **Tax management** | client → Tax | Tax lots (FIFO/HIFO/LIFO per client), realized short- and long-term gains, wash-sale flags, estimated tax after netting, and a lot inventory. |
| **Tax-loss harvesting** | client → Tax | Finds lots down at least 5% and at least $200, estimates the tax savings, then sells and buys a similar replacement (e.g. VTI→SCHB). It also swaps the target so rebalancing doesn't undo the harvest. |
| **Model marketplace** | Model marketplace page | 13 built-in models (5 Core, 60/40, All-Weather, Dividend Income, ESG, Multi-Factor, Tech Growth, Global Equity, Capital Preservation) plus a custom builder. Also covers profile-fit checks, assigning a model to many clients, pushing updates, and bulk rebalancing. |
| **Financial planning** | client → Planning | Goals with Monte Carlo simulation (4,000 paths, capital-market assumptions, inflation). Shows success odds, a percentile fan chart, the saving needed for 80% odds, and "what if" results by risk profile. |

### v1.6 additions

| Feature | Where | What it does |
|---|---|---|
| **Digital onboarding** | New client | Account types (Individual, Joint, Trust, Corporate, Traditional IRA, Roth IRA), contact details, objective, primary/contingent beneficiaries (validated to total 100%), and a model choice showing which models fit the risk profile. Retirement accounts automatically block margin, SBLOC and lending and skip tax-loss harvesting. |
| **Reports** | client → Reports | A printable **Investment Policy Statement** (objectives, risk, allocation with permitted ranges, rebalancing policy, constraints, tax, fees, signature lines) and **client statements** for any period (value, time-weighted return, allocation vs policy, holdings, options, activity, fees, income, gains, goals). Use Print → Save as PDF. |
| **Personalized (direct) indexing** | client → Direct index | Own the S&P 500's top N stocks directly, weighted like the index, with sector or ticker exclusions. The sleeve replaces part of core US equity. It shows sector tilts and tracking error vs SPY, and harvests losses into same-sector peer stocks. Uses FMP SPY holdings with live data, or a 50-stock index offline. |
| **Margin** | client → Lending | Reg T 2:1 buying power, daily margin interest on the debit balance, a 30% maintenance requirement, and margin-call alerts. |
| **Line of credit (SBLOC)** | client → Lending | Borrow against the portfolio (advance rates of 85% for bonds, 70% for ETFs and 50% for stocks). Interest compounds daily, repay from cash, with collateral-call alerts. |
| **Securities lending** | client → Lending | Earn 50% of the lending fee on holdings, paid to cash daily. Hard-to-borrow stocks earn more. |
| **Options (simulated)** | client → Options | Black-Scholes chains priced from historical volatility, with Greeks. Level 1 allows covered calls and cash-secured puts; Level 2 adds buying calls and puts. Covered shares and put collateral are reserved, and expiry is settled automatically (worthless, cash-settled or assigned). |

All of these are also available to the AI assistant and to Claude, through the tools `get_tax_report`, `harvest_tax_loss`, `list_models`, `assign_model`, `get_financial_plan`, `add_goal`, `get_billing` and `run_billing`.

## AI Assistant (built into the app)

Open **AI Assistant** in the sidebar, or click **Ask about ...** on any client, and chat in plain English. The assistant can:
- Read portfolios and alerts
- Run the research model
- Preview and execute rebalances
- Place paper orders
- Create clients

Anything that changes an account (orders, rebalances, new clients) shows an **Approve / Decline** card first.

- **Providers:** Claude (Anthropic API) or ChatGPT (OpenAI Responses API). Switch between them in the assistant's **Settings**.
- **API keys:** you need a pay-as-you-go API key, which is separate from a ChatGPT Plus or Claude subscription. Get one at console.anthropic.com or platform.openai.com. Keys are stored only in `data\\assistant.json` on this computer. The environment variables `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` also work.
- **Default models:** `claude-sonnet-5` and `gpt-6-luna`, both editable. Stronger options are `claude-opus-5-5` and `gpt-6-astra`.

## Connect to Claude / ChatGPT (external apps)

The app includes an **MCP server**, so AI assistants can use it through these tools:
- Look up clients and portfolios
- Research stocks
- Preview or execute rebalances
- Place paper orders
- Create clients

Example requests:
- *"How is my Growth client doing?"*
- *"Research NVDA for client 1."*
- *"Rebalance the demo client."*

**Claude Desktop (local and private).** Add this to `%APPDATA%\Claude\claude_desktop_config.json`, then restart Claude:
```json
{ "mcpServers": { "portfolio-manager": {
    "command": "C:\\Users\\<you>\\PycharmProjects\\portfolio-platform\\.venv\\Scripts\\python.exe",
    "args": ["C:\\Users\\<you>\\PycharmProjects\\portfolio-platform\\mcp_server.py"] } } }
```

**ChatGPT (remote).**
1. Double-click `Connect to ChatGPT.bat`. It opens a Cloudflare tunnel and copies a secret connector URL to your clipboard.
2. In ChatGPT, turn on **Developer mode** under Settings → Apps → Advanced settings.
3. Create an app with that URL.

Only the secret `/connect/<token>/mcp` path is reachable through the tunnel; the web UI and API reject proxied requests. The tunnel address changes each run.

Plan limits: write actions (orders, rebalances) require a ChatGPT Business, Enterprise or Edu plan. Pro accounts are read-only.

## Data

**Prices (free):** quotes, daily history and symbol search come from Yahoo Finance's public chart/search endpoints —
stocks and ETFs worldwide, indices (S&P 500, Russell 2000, Nifty 50, Sensex, FTSE, DAX, Nikkei…), commodities
(front-month futures: GC=F gold, CL=F WTI…), crypto, currencies and US yields. App symbols like `GCUSD`, `BTCUSD`, `EURUSD` are
mapped automatically. It's an unofficial feed: quotes are cached 60 s and history 30 min, and failures show as "n/a" (never fake numbers).
Live and simulated accounts use separate databases (`data/portfolio-live.db` vs `data/portfolio.db`); on the first live start,
clients and watchlists are copied from the simulated database with fresh funding (simulated positions are not carried over).

**Company financials (FMP):**

The app uses FMP's `stable` API: quote, dividend-adjusted EOD history, profile, income statement, balance sheet, cash-flow statement, stock peers, key-metrics-TTM, ratios-TTM and search. Responses are cached in SQLite: quotes for 30 seconds, prices for 6 hours and fundamentals for 24 hours. This keeps you inside free or starter plan limits. Some endpoints (for example peers and TTM metrics) may require a paid FMP tier. If they fail, the research engine falls back to DCF-only valuation.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `FMP_API_KEY` | – | Your FMP key |
| `DATA_MODE` | `auto` | `auto`/`live` (Yahoo prices + FMP financials if a key is set), `fmp`, or `sim` |
| `RISK_FREE_RATE` | 0.0425 | Used for CAPM, Sharpe and the cash return |
| `EQUITY_RISK_PREMIUM` | 0.05 | CAPM ERP |
| `TERMINAL_GROWTH` | 0.025 | DCF terminal growth |
| `SLIPPAGE_BPS` | 5 | Adverse slippage on market/stop fills |
| `COMMISSION_PER_TRADE` | 0 | Flat $ per fill |
| `ALLOW_FRACTIONAL` | true | Fractional shares |
| `MONITOR_INTERVAL` | 60 | Seconds between background order checks and snapshots (0 = off) |
| `DB_PATH` | `data/portfolio.db` | SQLite file |

## How the recommendation works

| Pillar | Weight | Inputs |
|---|---|---|
| Valuation | 50% | Fair value = 60% DCF + 40% peer-multiple implied price (P/E, EV/EBITDA, P/FCF). Upside is mapped from −40%…+40% to a 0–100 score |
| Quality | 30% | Piotroski F-score (9 checks), ROIC, operating-margin stability |
| Momentum | 20% | 12-1 month return, price vs 50/200-day SMA, RSI(14) |

- **BUY:** upside ≥ 15% and composite ≥ 55.
- **SELL:** upside ≤ −15%, composite < 35, or a weak F-score with negative upside.
- **HOLD:** everything else.

DCF details:
- Years 1–3 of revenue come from **Street consensus estimates** (FMP analyst-estimates). A year is used only if at least 3 analysts cover it; the limits are set by `CONSENSUS_YEARS` and `MIN_ANALYSTS` in `research.py`.
- After the consensus years, growth fades linearly to terminal growth by year 10. If no estimates are available, the path starts from the historical CAGR, clipped to between −5% and 25%.
- The Street consensus price target, forward P/E and consensus EPS growth appear beside the model's fair value for comparison. They do not feed into the rating.
- FCF margin is the 3-year median. When capex is more than 1.5× D&A (heavy growth investment), the margin is assumed to normalize halfway toward owner earnings (CFO − D&A).
- Banks and other financials skip the DCF and use multiples only.

Every assumption is shown in the UI, and you can change the thresholds at the top of `app/research.py`.

## Project layout

```
app/
  main.py          FastAPI routes + static UI
  config.py        settings / .env
  db.py            SQLite schema
  market_data.py   Live (Yahoo) provider, FMP provider, simulated provider
  markets.py       Markets home page + watchlists
  carryover.py     one-time copy of clients from the simulated to the live database
  risk_profile.py  questionnaire, profiles, model portfolios
  portfolio.py     clients, targets, valuation, snapshots, time-weighted return
  analytics.py     risk/return metrics, technicals
  research.py      financial model and rating
  trading.py       paper order engine
  rebalance.py     drift bands and trade generation
  monitor.py       alerts + background cycle
  static/          single-page UI (vanilla JS + Chart.js, served locally)
tests/             pytest suite (runs on the simulated market)
```

API docs are auto-generated at http://localhost:8000/docs.

Run the tests: `python -m pytest -q`

## Ideas for next steps

- Add a mean-variance or risk-parity optimizer for the stock sleeve
- Add tax-lot tracking (FIFO/HIFO) and tax-loss harvesting suggestions
- Export client reports (PDF) and schedule a weekly monitoring email
- Connect a broker paper account (for example Alpaca paper) behind the same `trading.py` interface
