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

- `MARKET_LENS_UNIVERSE=technology`
- `MARKET_LENS_TICKERS=AAPL MSFT NVDA`
- `MARKET_LENS_ANALYSIS_PERIOD=6mo`
- `MARKET_LENS_MIN_RR=2`
- `MARKET_LENS_HEADLESS=true`

Install dependencies and browser runtime:

```powershell
python -m pip install -e .
python -m playwright install chromium
```

## Run

```powershell
python agent\market_lens_ui_agent.py
```

On Windows you can also run:

```powershell
.\agent\run_agent.ps1
```

Outputs:

- Updated Excel tracker
- `agent_runs/screenshots/*.png`
- `agent_runs/summaries/*.md`

## Conservative Decision Rules

- `No Trade` results are skipped.
- Risk/reward below the configured minimum is skipped.
- Missing buy zone, stop, or targets are skipped.
- Valid setups outside the buy zone are placed on watch.
- Valid setups inside the buy zone can open a simulated buy if portfolio limits allow it.
- Existing positions can be held, partially closed, closed at target, or exited at stop.

The agent manages a simulated 100,000 ILS paper portfolio using the limits in
the Excel `Settings` sheet.
