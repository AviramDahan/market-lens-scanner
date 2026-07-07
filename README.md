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

## How The Agent Works

For detailed strategy guides explaining the stock-selection pipeline, setup
analysis, entry rules, portfolio rules, and monitor behavior, see:

- [`docs/market_lens_agent_rules_en.md`](docs/market_lens_agent_rules_en.md)
- [`docs/market_lens_agent_rules_he.md`](docs/market_lens_agent_rules_he.md)

Market Lens uses one shared strategy engine for manual user scans and for the
paper-trading agent. The user scan and the agent scan both run through the same
scanner, setup detection, and strategy decision layer. The difference is that
the agent also has portfolio state: cash, open positions, recent stop events,
Excel tracking, GitHub Actions automation, Telegram alerts, and the position
monitor.

The system is paper trading only. It does not connect to a broker and does not
place real orders.

### Stock Selection Pipeline

The agent does not scan a small fixed list by default. It builds a Smart
Universe, then chooses a diversified scan basket from that universe.

Default cloud scan configuration:

- `MARKET_LENS_AGENT_UNIVERSE_TARGET=100`
- `MARKET_LENS_AGENT_UNIVERSE_POOL=100`
- `MARKET_LENS_AGENT_UNIVERSE_MAX_POOL=300`
- `MARKET_LENS_AGENT_MAX_PER_SECTOR=15`
- `MARKET_LENS_AGENT_SCAN_BATCH_SIZE=15`
- `MARKET_LENS_AGENT_RECENT_SKIP_FALLBACK=true`
- `MARKET_LENS_AGENT_CARRY_FORWARD_LIMIT=30`
- `MARKET_LENS_WATCH_CARRY_FORWARD_DAYS=14`
- `MARKET_LENS_SKIP_COOLDOWN_HOURS=8`

Recent `WATCH` tickers are carried forward outside the fresh-universe quota.
Recent `SKIP` tickers are excluded first, but the agent can use them as a
fallback when the fresh candidate pool is too small. This keeps the scan near
the configured target count instead of shrinking to a small basket after several
runs in the same day.

The carry-forward list is capped by `MARKET_LENS_AGENT_CARRY_FORWARD_LIMIT` so
the scanner does not grow without bound during active days. If a transient
Render/yfinance error such as 502/503/504 occurs, the agent retries and can
split the failed batch into smaller chunks.

The broad source universe is assembled from:

- S&P 500
- Nasdaq-100
- Russell 1000
- Russell 3000
- the curated sector dropdown lists in the app
- a broad Nasdaq screener fallback if Russell holdings are unavailable

The source list is filtered before scoring:

- common US equities only
- excludes ETFs, warrants, rights, units, preferred shares, notes, bonds,
  acquisition/blank-check vehicles, and non-common instruments
- minimum price: `$10`
- minimum average dollar volume: `$100M`
- ATR% between `1.2%` and `8.0%`
- minimum broad-market market cap: `$1B`
- minimum broad-market volume: `250,000` shares
- weak sectors are not used for new scan candidates

Each remaining ticker is scored by:

- relative strength versus benchmarks
- one-month, three-month, and six-month momentum
- trend quality
- average dollar volume
- ATR% tradability
- sector health and sector regime

The final basket is diversified by sector. Strong sectors can receive more
slots, neutral sectors receive fewer, and weak sectors receive zero new
candidates. Open positions are always kept in the scan/monitoring flow even if
they are not selected by the current Smart Universe run. Recent `WATCH` and
`WATCH_READY` names are carried forward for up to 14 days and do not consume the
normal new-candidate quota.

### Market And Sector Regime

Before deciding whether a setup may become a simulated buy, the agent calculates
a market regime:

- `BULL`
- `NEUTRAL`
- `BEAR`

The market regime uses:

- SPY trend
- QQQ trend
- IWM trend
- VIX level/trend
- US10Y trend
- DXY trend

Regime rules:

- `BULL`: max total exposure `$40,000`, minimum net R/R `2.0`, minimum setup
  score `0.45`
- `NEUTRAL`: max total exposure `$20,000`, minimum net R/R `2.5`, minimum setup
  score `0.55`
- `NEUTRAL` plus strong sector: minimum net R/R can be relaxed to `2.2`
- `BEAR`: no new `BUY_SIMULATED`

Sector regime is calculated from representative ETFs such as XLK, SMH, XLF,
XLV, XLI, XLE, XLY, XLC, XLU, XLRE, and XLB. It looks at ETF trend,
one-month return, three-month return, relative strength versus SPY, and sector
momentum.

Sector outcomes:

- `STRONG`: eligible for new buys if all other gates pass
- `NEUTRAL`: eligible only with stronger setup quality
- `WEAK`: no automatic new buy; at most watch/review

### Setup Detection

The scanner first detects the technical setup using completed market data and
then computes buy zone, stop, targets, and risk/reward. Setup types include
breakout/retest, VWAP reclaim, Fib/support, liquidity trap style structures,
and `No Trade`.

`No Trade` does not mean the company is bad. It means the current chart does not
meet the setup rules at that moment.

Extended-hours prices are displayed as live context, but strategy confirmation
uses completed regular-session candles. Off-hours scans can stage a candidate
as `WATCH_READY`; they cannot open a new `BUY_SIMULATED` while
`MARKET_LENS_ALLOW_BUY_OUTSIDE_REGULAR_HOURS=false`.

### Entry Gates

A setup may become `BUY_SIMULATED` only if all required gates pass:

- setup is not `No Trade`
- market regime is not `BEAR`
- scan is in regular market session, unless off-hours buys are explicitly
  enabled
- setup score is at least `0.45` in `BULL`
- setup score is at least `0.55` in `NEUTRAL`
- sector is not `WEAK`
- normalized quality score is acceptable
- buy zone exists
- stop loss exists
- target 1 and target 2 exist
- price is inside the buy zone
- executable entry is used instead of an ideal theoretical entry
- target 1 net R/R is at least `0.80`
- target 2 cannot justify a trade by itself
- weighted net R/R passes the regime threshold
- target 1 is not too close versus ATR when ATR data is available
- entry confirmation passes on a completed candle
- no earnings blackout is active
- no stop-loss cooldown blocks re-entry
- sector exposure cap is not exceeded after position-size adjustment
- factor/theme exposure cap is not exceeded after position-size adjustment
- correlation with open positions is below the block threshold
- position size fits cash, exposure, and max-risk limits

Entry confirmation is setup-specific:

- Breakout/retest requires a completed close or reclaim above the trigger, held
  retest, and no falling candle into the zone.
- VWAP reclaim requires an actual VWAP reclaim and completed close above VWAP,
  not just a touch.
- Fib/support requires a completed close above the buy zone or a strong bullish
  reclaim from the zone. A falling candle entering the zone is blocked.
- If confirmation cannot be calculated reliably, the system does not buy; it
  marks the setup as `WATCH`.

### Risk/Reward Calculation

The scanner keeps the original theoretical R/R, but the agent decision uses
slippage-adjusted net R/R.

The decision stores:

- theoretical entry
- executable entry
- stop assumption
- target 1 assumption
- target 2 assumption
- gross risk per share
- gross reward per share
- estimated spread
- estimated slippage
- estimated fees
- net entry
- net risk per share
- net reward to target 1
- net reward to target 2
- net R/R to target 1
- net R/R to target 2
- weighted decision R/R

The weighted decision R/R is configured as:

- primary target weight: `80%`
- stretch target weight: `20%`

This prevents a far target 2 from making a weak trade look acceptable when
target 1 is poor.

### Portfolio Rules

The agent manages a simulated `$100,000` paper portfolio.

Default risk limits:

- maximum position allocation: `10%` of portfolio, up to `$10,000`
- maximum risk per trade: `1%` of portfolio, up to `$1,000`
- maximum total exposure in `BULL`: `$40,000`
- maximum total exposure in `NEUTRAL`: `$20,000`
- no new buys in `BEAR`
- sector exposure cap in `BULL`: `40%` of invested capital
- sector exposure cap in `NEUTRAL`: `30%` of invested capital
- factor exposure cap in `BULL`: `50%`
- factor exposure cap in `NEUTRAL`: `35%`

If a candidate is valid but too large for a sector/factor cap, the agent first
tries to reduce share count. It downgrades to `WATCH` only if a safe reduced
position cannot fit the limits.

Position size is based on the smaller of:

- available cash
- max allocation per ticker
- max portfolio exposure allowed by the current market regime
- max risk per trade based on entry minus stop
- sector/factor exposure caps

### Trade Lifecycle

Possible actions:

- `BUY_SIMULATED`: open a new paper position
- `WATCH_READY`: setup is close or staged, usually outside regular hours or
  near passing thresholds
- `WATCH`: setup exists but one or more gates are missing
- `SKIP`: setup is invalid or blocked
- `HOLD`: existing paper position remains open
- `TAKE_PARTIAL_PROFIT`: target 1 touched
- `TAKE_PROFIT`: target 2 touched
- `EXIT_STOP`: stop loss touched

When target 1 is touched:

- the monitor sells 50% of the current simulated quantity
- the remaining position stays open
- stop loss is moved to the original entry price
- the trade log records realized P/L and the event

When target 2 is touched:

- the remaining simulated position is closed
- realized P/L is recorded

When stop loss is touched:

- the simulated position is closed
- realized P/L is recorded
- a stop-loss cooldown blocks new entries in that ticker for 3 trading days
  unless the new setup is materially stronger

The position monitor uses one-minute intraday high/low candles for TP/SL checks.
If target and stop are touched in the same one-minute candle, the tracker uses a
conservative stop-first rule because the exact intraminute sequence is unknown.

### Automation And Monitoring

The scanner is triggered through `/agent/trigger-scan`, usually by
cron-job.org. The server decides whether the current time is an allowed New York
scan slot and dispatches GitHub Actions with `force=true` only when appropriate.

Regular-session confirmation scans:

- 09:45 New York
- 10:30 New York
- 11:30 New York
- 13:30 New York
- 14:30 New York
- 15:30 New York
- 16:15 New York

Off-hours staging scans:

- weekdays: 06:30, 08:30, 09:10, 16:20, 18:30, 20:15, 22:30 New York
- Saturday: 11:00 New York
- Sunday: 18:30 and 22:00 New York

The position monitor is separate from the scanner. `/agent/monitor-live` checks
open positions during market hours and dispatches the monitor workflow only
when a target or stop is touched. The recommended cron-job.org monitor schedule
is Monday-Friday, every minute, 09:35-16:05 New York.

### Data Written Per Run

Each successful agent run writes:

- Excel tracker: `agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx`
- run summary: `agent_results/summaries/*.md`
- structured decision JSONL: `agent_results/decisions/*.jsonl`
- screenshot: `agent_results/screenshots/*.png`
- retained charts under `agent_results/charts/`

Charts are intentionally limited. The agent saves charts for buys/open
positions, `WATCH_READY`, and only the closest rejected candidates. It does not
save charts for every rejected ticker.

Every scanned ticker receives a Decision JSON object with the final action,
human-readable reason, warnings, market regime, sector regime, setup data,
R/R breakdown, earnings state, exposure checks, factor tags, correlation check,
position sizing, and portfolio exposure before/after.

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
builds this list from broad US equity sources plus the curated dropdown sector
lists, filters for price, dollar volume, ATR%, relative strength, trend quality,
and sector health, then limits concentration to keep the final scan diversified
across sectors. The current cloud target is 100 scanned tickers per run.
Sector scan quota is dynamic: strong sectors can receive more names, neutral
sectors stay tighter, and weak sectors are excluded from new candidates.

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
- `Market Lens Position Monitor` is the official portfolio updater for open positions.
- `/agent/trigger-scan` is the server-side scan scheduler gate. cron-job.org should call this endpoint instead of calling GitHub Actions directly; the server dispatches `Market Lens Paper Agent` with `force=true` only inside a configured New York scan slot.
- `/agent/monitor-live` is a lightweight server-side TP/SL sensor that can be called by cron-job.org every minute during market hours. It checks only open positions and dispatches the monitor workflow only when a stop or target is touched.

The position monitor does not open new trades. It reads the current open
positions from the Excel tracker, downloads one-minute intraday candles, and
uses each candle's high/low to check whether target 1, target 2, or stop loss
was touched. If target and stop are touched in the same one-minute candle, the
paper tracker applies a conservative stop-first rule because the exact sequence
inside that candle is unknown.

The `/agent` dashboard also includes an optional live TP/SL sensor while it is
open in a browser, but automation does not depend on the browser being open.
For full automation, configure cron-job.org to call the server-side
`/agent/monitor-live` endpoint during market hours. The server verifies open
positions and live prices, rate-limits repeated events, and dispatches the
`Market Lens Position Monitor` GitHub Action with `force=true` only when a real
target/stop touch is detected. The frontend never receives the GitHub token.
Configure the Render service with:

```text
GITHUB_ACTIONS_TRIGGER_TOKEN=...
GITHUB_ACTIONS_REPOSITORY=AviramDahan/market-lens-scanner
GITHUB_AGENT_WORKFLOW=market-lens-agent.yml
GITHUB_POSITION_MONITOR_WORKFLOW=market-lens-position-monitor.yml
GITHUB_ACTIONS_REF=main
MARKET_LENS_AGENT_CRON_SECRET=...
MARKET_LENS_AGENT_TRIGGER_WINDOW_MINUTES=4
MARKET_LENS_ALLOW_TRIGGER_SCAN_FORCE=false
MARKET_LENS_MONITOR_CRON_SECRET=...
MARKET_LENS_MONITOR_TRIGGER_GLOBAL_COOLDOWN_SECONDS=60
MARKET_LENS_MONITOR_TRIGGER_EVENT_COOLDOWN_SECONDS=300
```

Recommended cron-job.org scanner request:

```text
GET https://market-lens-scanner-fb63.onrender.com/agent/trigger-scan?secret=<MARKET_LENS_AGENT_CRON_SECRET>&compact=1
Timezone: America/New_York
Schedule: every 5 minutes, all days
```

The scanner endpoint returns `skipped` outside the configured scan slots without
creating a GitHub Actions run. This prevents short 6-9 second GitHub runs that
only execute the in-workflow schedule guard and then skip all agent tasks.
Do not add `force=true` to the cron-job.org scanner URL. The server dispatches
the workflow with `force=true` internally only when the current New York time is
inside a configured scan slot. Query-string force is ignored by default unless
`MARKET_LENS_ALLOW_TRIGGER_SCAN_FORCE=true` is explicitly configured for manual
debugging.

Default New York weekday scan slots:

```text
06:30, 07:30, 08:30, 09:10,
09:35, 09:45, 10:00, 10:30, 11:00, 11:30,
12:00, 12:30, 13:00, 13:30, 14:00, 14:30,
15:00, 15:30, 15:55, 16:15, 16:20,
18:30, 20:15, 22:30
```

Override with `MARKET_LENS_AGENT_WEEKDAY_SCAN_TIMES` on Render if a narrower or
wider schedule is needed.

Recommended cron-job.org monitor request:

```text
GET https://market-lens-scanner-fb63.onrender.com/agent/monitor-live?secret=<MARKET_LENS_MONITOR_CRON_SECRET>&compact=1
Timezone: America/New_York
Schedule: every 1 minute, Monday-Friday, 09:35-16:05
```

The `compact=1` query parameter keeps cron-job.org responses intentionally
small so successful skip/no-op checks are not disabled as "output too large".

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
