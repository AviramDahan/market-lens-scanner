# Market Lens

Market Lens is a local and web-based swing-trade scanner for US stock tickers.
It downloads market data, evaluates objective technical setups, and returns
ranked results with annotated chart images.

It focuses on technical confluence only: volume profile levels, Fibonacci
retracements, VWAP, EMA, market structure, relative strength, risk/reward, and
liquidity sweeps.

## Features

- FastAPI web UI for scanning ticker lists
- CLI scanner for terminal use
- Annotated PNG charts for each ticker
- Analysis range selector for 3 months, 6 months, 1 year, or 2 years
- Shared saved setup watchlist with automatic and manual saves
- Lightweight local user identity with personal saved setups
- Global setups tab for trade setups discovered by public scans
- Setup status tracking against target 1, target 2, and stop loss
- Curated market universes for quality sector-based scans
- Buy zone, stop, targets, risk/reward, score, and setup reason
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

Useful endpoints:

- `GET /` - web UI
- `GET /health` - health check
- `GET /config` - active config
- `POST /scan` - JSON scanner API
- `POST /ui/scan` - scanner API with chart image generation
- `GET /setups` - shared saved setup watchlist
- `POST /setups` - manually save a setup
- `POST /setups/{setup_id}/refresh` - refresh setup status against live price
- `GET /watchlists` - curated market universes for the UI scanner

## Saved Setups

When the UI scan finds a trade setup, Market Lens automatically records it in
`Global Setups`. Users can also press `Save setup` on a result card to save it
to their own `My Setups` tab. The app uses a local browser session id and an
optional display name for lightweight user separation. Saved setups are stored
in SQLite under `data/setups.sqlite` by default.

The saved setup status is:

- `OPEN` - price has not reached the first target or stop
- `TARGET1` - price reached target 1
- `TARGET2` - price reached target 2
- `STOPPED` - price touched or moved below stop loss

For production hosting, set `MARKET_LENS_DATA_DIR` or `MARKET_LENS_DB_PATH` to a
persistent disk or database-backed volume. On ephemeral hosting plans, saved
setups may reset after a restart or deploy.

## Supabase

The production auth/database schema is in `supabase_schema.sql`. It creates:

- `profiles` - authenticated user profile rows
- `global_setups` - setups discovered by scans
- `user_saved_setups` - setups saved by each user

The schema enables Row Level Security for user-owned rows and allows public
read access to global setups.

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
3. Render will build the Dockerfile and serve the app from a public URL.
4. The service uses `/health` as its health check.

The included Render plan is `starter` for an always-on shared URL. Change the
plan in `render.yaml` before deploying if you prefer another option.

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
  indicators.py    ATR, VWAP, EMA, volume profile, relative strength
  fibonacci.py     Swing and Fibonacci detection
  setups.py        Setup detection and scoring
  static/          Browser UI
config.yaml        Default ticker watchlist
Dockerfile         Docker API/UI server
render.yaml        Render deployment blueprint
```

## Disclaimer

Market Lens is a research and screening tool. It is not financial advice.
