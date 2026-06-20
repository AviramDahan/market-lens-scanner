# Market Lens UI Trading Agent

This agent uses the Market Lens web UI like a real user. It logs in, selects a
configured universe or manual tickers, scans through the visible UI, extracts
the result cards from the page, updates the Excel tracker, saves a screenshot,
and writes a run summary.

It is paper trading only. It does not place real trades, connect to a broker, or
use real money.

## Setup

Copy `.env.example` to `.env` and fill in the local values:

```powershell
Copy-Item .env.example .env
```

Required values:

- `MARKET_LENS_EMAIL`
- `MARKET_LENS_PASSWORD`
- `MARKET_LENS_EXCEL_PATH`

Optional values:

- `MARKET_LENS_UNIVERSE=smart-universe`
- `MARKET_LENS_TICKERS=AAPL MSFT NVDA`
- `MARKET_LENS_ANALYSIS_PERIOD=6mo`
- `MARKET_LENS_MIN_RR=2`
- `MARKET_LENS_HEADLESS=true`
- `MARKET_LENS_MONITOR_PERIOD=5d`
- `MARKET_LENS_MONITOR_INTERVAL=1m`
- `MARKET_LENS_MONITOR_SAVE_NOOP=false`

When `MARKET_LENS_UNIVERSE=smart-universe`, the agent selects the dynamic Smart
Universe through the UI. The app ranks quality large/liquid names by relative
strength, trend quality, liquidity, and ATR%, then caps sector concentration
before the agent scans the selected tickers.

Open simulated positions are always added to the scan basket, even if they are
not selected by the current Smart Universe run. This keeps stop and target
tracking active for existing paper trades.

Install dependencies and browser runtime:

```powershell
python -m pip install -e .
python -m playwright install chromium
```

## Run

```powershell
python agent\market_lens_ui_agent.py
python agent\position_monitor.py
```

On Windows you can also run:

```powershell
.\agent\run_agent.ps1
```

Outputs:

- Updated Excel tracker
- `agent_runs/screenshots/*.png`
- `agent_runs/summaries/*.md`
- `agent_runs/decisions/*.jsonl`
- `agent_results/position_monitor/*.md` in the cloud workflow

## Cloud Schedule on GitHub

The repository includes two agent workflows:

- `.github/workflows/market-lens-agent.yml`
- `.github/workflows/market-lens-position-monitor.yml`

The full UI agent runs in GitHub Actions and commits the updated paper-trading
tracker and run outputs back to the repository:

- tracker: `agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx`
- screenshots and summaries: `agent_results/`
- structured decisions: `agent_results/decisions/*.jsonl`

Add these repository secrets in GitHub:

- `MARKET_LENS_EMAIL`
- `MARKET_LENS_PASSWORD`

Then open GitHub Actions and run `Market Lens Paper Agent` manually once. The
workflow is also scheduled for 09:45, 10:30, 11:30, 13:30, 14:30, 15:30, and
16:15 New York time.

The position monitor is the official portfolio updater for existing open
positions. For full automation without keeping the dashboard open,
cron-job.org should call the deployed app endpoint `/agent/monitor-live` every
minute during the New York market session. That lightweight server-side sensor
checks live prices for open positions and dispatches the GitHub position monitor
only when target 1, target 2, or stop loss is touched. The monitor then downloads
one-minute intraday candles and applies the official Excel/dashboard update. If
target and stop are touched inside the same candle, the tracker applies a
conservative stop-first rule because the exact intraminute sequence is not
available.

In the cloud workflow, `MARKET_LENS_MONITOR_SAVE_NOOP=true` is enabled so every
monitor run publishes a heartbeat and current-price refresh for open positions.

## Conservative Decision Rules

- `No Trade` results are skipped.
- Risk/reward below the configured minimum is skipped.
- Missing buy zone, stop, or targets are skipped.
- Valid setups outside the buy zone are placed on watch.
- Valid setups inside the buy zone can open a simulated buy if portfolio limits allow it.
- Existing positions can be held, partially closed, closed at target, or exited at stop.

The agent manages a simulated 100,000 USD paper portfolio using the limits in
the Excel `Settings` sheet.

## Risk and Transparency Layer

The agent keeps the existing UI scan and setup detection, then applies an
additional decision layer before opening any new simulated buy:

- Market regime controls maximum exposure and required net R/R.
- Sector regime can block or downgrade weak-sector setups.
- Dynamic sector and factor exposure checks prevent concentrated simulated buys.
- If a candidate is valid but too large for a sector/factor cap, the agent first
  tries a reduced share count before downgrading to `WATCH`.
- Correlation is checked against open positions using recent daily returns.
- R/R uses primary target, stretch target, and weighted decision R/R instead of
  relying only on the best target.
- Net R/R records executable entry, net stop/targets, gross and net risk/reward,
  spread source, slippage bucket, and fees.
- Earnings blackout can block new buys around earnings dates.
- Targets are validated against daily ATR distance.
- Momentum, ATR, and liquidity are normalized by market-cap bucket.
- The `/agent` dashboard exposes risk-check pills and score calibration buckets
  for closed trades.

Each scanned ticker receives a Decision JSON object in the tracker and in the
run JSONL file. The object includes the final action, warnings, exposure values,
factor tags, correlation data, and a human-readable reason.
