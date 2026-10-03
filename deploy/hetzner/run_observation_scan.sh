#!/bin/sh
set -eu

# The image's tracker is a frozen copy; this run never touches the live portfolio.
cp /app/agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx /tmp/observation-tracker.xlsx
export MARKET_LENS_EXCEL_PATH=/tmp/observation-tracker.xlsx
export MARKET_LENS_RUN_DIR=/tmp/observation-results
export MARKET_LENS_AGENT_NOTIFICATION_OUTBOX=/tmp/observation-outbox.json
export MARKET_LENS_URL=http://market-lens-staging-web-1:8000/
export MARKET_LENS_EMAIL=observation@example.invalid
export MARKET_LENS_PASSWORD=observation-only
export MARKET_LENS_UNIVERSE=manual
export MARKET_LENS_TICKERS=AAPL,MSFT,NVDA
export MARKET_LENS_AGENT_TIMEOUT_SECONDS=300
export MARKET_LENS_AGENT_SCAN_BATCH_SIZE=3
export MARKET_LENS_HEADLESS=true
export MARKET_LENS_DASHBOARD_SNAPSHOT_SYNC_ENABLED=false
export MARKET_LENS_RESULTS_SYNC_ENABLED=false
export MARKET_LENS_RENDER_SHADOW_MONITOR_ENABLED=false

python agent/market_lens_ui_agent.py

python - <<'PY'
import json
from pathlib import Path

files = sorted(Path('/tmp/observation-results/runtime').glob('market_lens_agent_*.json'))
if not files:
    raise SystemExit('No observation runtime record')
record = json.loads(files[-1].read_text())
print(json.dumps({key: record.get(key) for key in (
    'run_status', 'login_status', 'tickers_requested', 'result_cards_read',
    'scan_seconds', 'total_seconds', 'errors',
)}, sort_keys=True))
if record.get('run_status') not in {'COMPLETE', 'PARTIAL_OK'}:
    raise SystemExit('Observation scan did not complete')
if not record.get('result_cards_read'):
    raise SystemExit('Observation scan returned no result cards')
PY
