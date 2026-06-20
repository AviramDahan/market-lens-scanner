# Market Lens

Market Lens is an authenticated web-based swing-trade scanner for US stock
tickers. It downloads market data, evaluates objective technical setups, and
returns ranked results with annotated chart images and a professional trade
quality assessment.

It focuses on technical confluence and trade quality: volume profile levels,
Fibonacci retracements, VWAP, EMA, market structure, relative strength,
risk/reward, liquidity sweeps, market regime, volume confirmation, liquidity
quality, event risk, and entry/invalidation planning.

Production access is gated by Supabase authentication. Users must sign in before
they can scan tickers, view global setups, or save personal setups.

## Features

- Authenticated FastAPI web UI for scanning ticker lists
- CLI scanner for terminal use
- Annotated PNG charts for each ticker
- Analysis range selector for 3 months, 6 months, 1 year, or 2 years
- Supabase email/password authentication
- Global setups tab for trade setups discovered by authenticated scans
- My Setups tab for user-specific saved setups
- Setup status tracking against target 1, target 2, and stop loss
- Curated market universes for quality sector-based scans
- Smart Universe for diversified daily ticker selection across sectors
- Buy zone, stop, targets, risk/reward, score, and setup reason
- Professional scan analysis: market regime, relative strength, liquidity,
  trend quality, volume confirmation, event risk, and trade plan
- Grade-based quality assessment with strengths and warnings
- UI trading agent for one-month paper portfolio tracking in Excel
- Position monitor for intraday target/stop checks on open paper trades
- Agent dashboard at `/agent` for portfolio, actions, screenshots, and P/L tracking
- Docker-ready deployment
- Render blueprint for public hosting

## Local Setup

Requirements: Python 3.11+

```bash
git clone <repo-url>
cd market-lens-scanner

python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

## Run The Web UI

```bash
uvicorn app.main:app --reload --port 8000
```

Then open:

```text
http://127.0.0.1:8000
```

Local development can run without Supabase environment variables. When Supabase
variables are configured, product endpoints require a valid authenticated user.

On Windows, you can also use:

```powershell
.\market-lens-api.ps1 -Port 8000
```

## CLI Usage

```bash
python -m app scan AAPL MSFT NVDA
python -m app scan AAPL --verbose
python -m app scan AAPL --json
python -m app scan AAPL MSFT --charts
python -m app scan AAPL --period 1y
```

On Windows:

```powershell
.\market-lens-scan.ps1 AAPL MSFT NVDA --verbose --charts
```

Chart images are saved under `charts/`.

## Configuration

Edit `config.yaml` to set a default watchlist and minimum risk/reward:

```yaml
tickers:
  - AAPL
  - MSFT
  - NVDA

min_rr: 2.0
analysis_period: 6mo
```

CLI arguments and UI inputs override this file.

## API

```bash
curl -X POST http://localhost:8000/scan \
  -H "Content-Type: application/json" \
  -d '{"tickers": ["AAPL", "MSFT"], "min_rr": 2.0, "analysis_period": "6mo"}'
```

In production, scanner and setup endpoints require a Supabase access token:

```bash
curl -X POST https://market-lens-scanner-fb63.onrender.com/ui/scan \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <supabase-access-token>" \
  -d '{"tickers": ["AAPL", "MSFT"], "min_rr": 2.0, "analysis_period": "6mo"}'
```

Useful endpoints:

- `GET /` - web UI
- `GET /health` - health check
- `GET /config` - active config
- `GET /auth/config` - Supabase client configuration for the UI
- `GET /agent` - paper-trading agent dashboard
- `GET /agent/data` - agent dashboard JSON from the Excel tracker
- `GET /agent/tracker` - download the current agent Excel tracker
- `POST /scan` - JSON scanner API, authenticated in production
- `POST /ui/scan` - scanner API with chart image generation, authenticated in production
- `GET /setups` - global or user saved setups, authenticated in production
- `POST /setups` - manually save a setup, authenticated in production
- `POST /setups/{setup_id}/refresh` - refresh setup status against live price, authenticated in production
- `GET /watchlists` - curated market universes for the UI scanner
- `GET /smart-universe` - dynamic diversified ticker selection for the UI and agent

## Professional Scan Analysis

Every scan result is enriched with an additional professional context layer:

- `market_regime` - SPY, QQQ, and IWM trend context with Risk-on, Mixed, or Risk-off label
- `relative_strength_info` - ticker strength versus SPY, QQQ, and IWM
- `liquidity` - average volume, average dollar volume, and tradability label
- `trend_quality` - MA20, MA50, MA200 alignment and trend slope
- `volume_confirmation` - accumulation/distribution, volume ratio, and pullback volume behavior
- `event_risk` - near earnings and recent large gap warnings when available
- `trade_plan` - entry trigger, invalidation, stop, and targets
- `professional_assessment` - A/B/C/D grade, quality score, decision, warnings, and strengths

This layer adjusts the displayed score for trade setups so a technically valid
pattern is ranked higher only when the broader context is supportive.

## Saved Setups

When the UI scan finds a trade setup, Market Lens automatically records it in
`Global Setups`. Authenticated users can also press `Save setup` on a result
card to save it to their own `My Setups` tab.

With Supabase configured, saved setups are stored in Postgres:

- `global_setups` stores setups found by scans
- `user_saved_setups` stores setups saved by each authenticated user

Without Supabase configured, local development falls back to SQLite under
`data/setups.sqlite`.

The saved setup status is:

- `OPEN` - price has not reached the first target or stop
- `TARGET1` - price reached target 1
- `TARGET2` - price reached target 2
- `STOPPED` - price touched or moved below stop loss

For production hosting, use Supabase/Postgres instead of SQLite because Render
free instances use ephemeral storage.

## UI Trading Agent

The `agent/` folder contains a paper-trading UI agent that uses the Market Lens
website like a real user. It logs in through the UI, scans a configured ticker
universe, reads the visible result cards, updates the Excel tracker, saves a
screenshot, and writes a run summary.

By default the cloud agent uses `Smart Universe`, not a fixed sector. The app
builds this list from quality large/liquid names, filters for price, dollar
volume, ATR%, relative strength, and trend quality, then limits concentration to
keep the final scan diversified across sectors. The default daily scan size is
35 tickers by default. Sector scan quota is dynamic: strong sectors can receive
up to roughly 8-10 tickers, neutral sectors stay tighter, and weak sectors are
excluded from new candidates.

The agent is paper trading only. It never places real trades or connects to a
broker.

Agent outputs:

- Updated Excel tracker
- `agent_results/screenshots/*.png`
- `agent_results/summaries/*.md`
- `agent_results/decisions/*.jsonl`
- `agent_results/position_monitor/*.md`

Before a new `BUY_SIMULATED` is accepted, the agent now adds a risk and
transparency layer on top of the existing scanner decision. The original setup
detection still runs first, then the agent records a structured decision for
each ticker with:

- market regime: `BULL`, `NEUTRAL`, or `BEAR`
- sector regime: `STRONG`, `NEUTRAL`, or `WEAK`
- dynamic market, sector, and factor exposure checks
- primary/target-2 R/R plus weighted decision R/R
- executable entry versus theoretical entry
- full gross/net R/R breakdown: entry, stop, targets, risk, reward, spread, slippage, and fees
- earnings blackout status
- target feasibility versus ATR
- normalized quality scores by market-cap/liquidity bucket
- correlation warning versus open positions
- reduced position sizing when a candidate exceeds sector/factor exposure caps
- score calibration buckets for closed trades in the `/agent` dashboard
- a plain-English final reason for `BUY_SIMULATED`, `WATCH`, `SKIP`, or `HOLD`

The deployed app also exposes a read-only agent dashboard:

```text
https://market-lens-scanner-fb63.onrender.com/agent
```

It reads `agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx` and
`agent_results/` from the repository, so each successful GitHub Actions run can
publish the latest paper portfolio state back into the public app.

The cloud setup has two separate workflows:

- `Market Lens Paper Agent` runs regular-session confirmation scans at 09:45, 10:30, 11:30, 13:30, 14:30, 15:30, and 16:15 New York time.
- The same agent can also run off-hours staging scans at 06:30, 08:30, 09:10, 16:20, 18:30, 20:15, and 22:30 on weekdays, plus Saturday 11:00 and Sunday 18:30/22:00 New York time.
- Off-hours scans can save candidates as `WATCH_READY`, but `MARKET_LENS_ALLOW_BUY_OUTSIDE_REGULAR_HOURS=false` prevents new `BUY_SIMULATED` entries until a regular-session confirmation scan runs.
- `Market Lens Position Monitor` checks existing open positions every five minutes from 09:35 through 16:00 New York time.

The position monitor does not open new trades. It reads the current open
positions from the Excel tracker, downloads one-minute intraday candles, and
uses each candle's high/low to check whether target 1, target 2, or stop loss
was touched. If target and stop are touched in the same one-minute candle, the
paper tracker applies a conservative stop-first rule because the exact sequence
inside that candle is unknown.

The `/agent` dashboard also acts as a lightweight live TP/SL sensor while it is
open in a browser. Its live price sync checks open positions every 60 seconds.
If a live price touches target 1, target 2, or stop loss, the browser calls the
server-side `/agent/trigger-monitor` endpoint. The server verifies the open
position and live price again, rate-limits repeated events, and then dispatches
the `Market Lens Position Monitor` GitHub Action with `force=true`. The frontend
never receives the GitHub token. Configure the Render service with:

```text
GITHUB_ACTIONS_TRIGGER_TOKEN=...
GITHUB_ACTIONS_REPOSITORY=AviramDahan/market-lens-scanner
GITHUB_POSITION_MONITOR_WORKFLOW=market-lens-position-monitor.yml
GITHUB_ACTIONS_REF=main
MARKET_LENS_MONITOR_TRIGGER_GLOBAL_COOLDOWN_SECONDS=60
MARKET_LENS_MONITOR_TRIGGER_EVENT_COOLDOWN_SECONDS=300
```

The cloud monitor uses `MARKET_LENS_MONITOR_SAVE_NOOP=true` so the public
`/agent` dashboard keeps open-position prices, exposure, and unrealized P/L
fresh even when no target or stop was touched.

Configure it with `.env` based on `.env.example`, then run:

```powershell
python agent\market_lens_ui_agent.py
python agent\position_monitor.py
```

When the app UI changes, update both the app selectors and the agent parser so
the agent continues to read the visible UI reliably.

## Supabase

The production auth/database schema is in `supabase_schema.sql`. It creates:

- `profiles` - authenticated user profile rows
- `global_setups` - setups discovered by scans
- `user_saved_setups` - setups saved by each user

The schema enables Row Level Security for user-owned rows. The application API
requires authentication before exposing scanner or setup data in production.

Required production environment variables:

```text
SUPABASE_URL=<your Supabase project URL>
SUPABASE_PUBLISHABLE_KEY=<your Supabase publishable key>
DATABASE_URL=<your Supabase Postgres or pooler connection string>
```

Do not commit `DATABASE_URL` or any database password to the repository.

## Docker

```bash
docker build -t market-lens-scanner .
docker run --rm -p 8000:8000 market-lens-scanner
```

## Deploy

This repo includes `render.yaml` for deploying the API/UI as a Docker web
service on Render.

1. Push this repo to GitHub.
2. In Render, create a new Blueprint from the repository.
3. Add the Supabase environment variables listed above.
4. Render will build the Dockerfile and serve the app from a public URL.
5. The service uses `/health` as its health check.

The included Render plan is `free`. Free instances can spin down after
inactivity, so the first request after idle time may be slow.

## Project Structure

```text
app/
  __main__.py      CLI entrypoint
  main.py          FastAPI app and web UI routes
  charts.py        Annotated chart generation
  models.py        Pydantic response/request models
  config.py        config.yaml loader
  scanner.py       Scanner orchestrator
  data.py          yfinance data loading
  professional.py  Market regime, quality, event risk, and trade plan enrichment
  indicators.py    ATR, VWAP, EMA, volume profile, relative strength
  fibonacci.py     Swing and Fibonacci detection
  setups.py        Setup detection and scoring
  static/          Browser UI
agent/
  market_lens_ui_agent.py  UI paper-trading agent and Excel updater
config.yaml        Default ticker watchlist
Dockerfile         Docker API/UI server
render.yaml        Render deployment blueprint
```

## Disclaimer

Market Lens is a research and screening tool. It is not financial advice.
