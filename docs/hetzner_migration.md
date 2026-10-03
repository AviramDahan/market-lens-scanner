# Hetzner migration: staged cutover

Market Lens currently serves the API/UI on Render. GitHub Actions runs the UI
agent, position monitor, health check, and the one-time weak-sector review.
The paper portfolio workbook and `agent_results/` are committed to GitHub.
These are separate responsibilities and must not be moved in one switch.

## Phase 1: isolated web preview

`deploy/hetzner/compose.staging.yaml` runs only the existing API/UI image. It
binds to loopback port 18081, has no production secrets, does not dispatch
Actions, and has no host data mounts. SQLite and generated charts are temporary.
The preview is not a source of portfolio truth and must not be exposed to the
public or used for trading. It uses a separate Compose project from AI Trader.

Before starting it, check host memory, disk, existing Docker services, and
port 18081. Build off market hours. Validate `/health`, `/agent`, and
`/agent/data` through SSH port forwarding; compare memory and CPU at idle and
during read-only page loads. Stop it if AI Trader service health degrades.

## Phase 2: persistent state and workers

- Preserve a versioned snapshot of the current workbook, `agent_results/`,
  notification receipts, and relevant server configuration. Verify restore
  without overwriting production state.
- Create one persistent local source of truth for the Market Lens workbook and
  results. The web server, scanner, and monitor must read the same state.
- Add a lock across scanner and monitor writes. Do not let two workers update
  the workbook concurrently. A failed scan must not advance portfolio state.
- Run scanner and monitor in separate bounded workers with Chromium installed
  only where needed. Never put their jobs in the API process.
- Mirror successful state transitions to GitHub for analysis and backup.
  Backup failure must alert and retry without pretending the portfolio was
  persisted remotely. Keep historical data and existing archives.
- Move the one-time weak-sector Telegram reminder only with an atomic receipt
  check. Until then, leave its GitHub workflow enabled.

## Phase 3: parallel verification and cutover

Run the new workers in observation mode first. Compare scans, result cards,
decision JSON, position checks, TP/SL ordering, dashboard state, Telegram
deduplication, and GitHub backups against the existing deployment. A dry run
must never send real alerts or change paper positions. Test restart, storage
restore, network failures, and missed schedules. Only then switch scheduling
and the public URL; retain Render and Actions as rollback until the new path
has passed live-session observation. Never run two active portfolio writers.

The GitHub repository remains the code and historical backup destination, not
the runtime database. Supabase, market-data providers, and Telegram remain
external services where used. No strategy or risk-rule change is part of this
migration.
