# Hetzner migration: staged cutover

Market Lens currently serves the API/UI on Render. GitHub Actions runs the UI
agent, position monitor, health check, and the one-time weak-sector review.
The paper portfolio workbook and `agent_results/` are committed to GitHub.
These are separate responsibilities and must not be moved in one switch.

## Phase 1: isolated web preview

`deploy/hetzner/compose.staging.yaml` runs only the existing API/UI image. It
binds to loopback port 18081, has no production secrets, and does not dispatch
Actions. It mounts a versioned copy of `agent_tracker/` and `agent_results/`
read-only from `/home/trader/market-lens-staging-data`. SQLite and newly
generated charts are temporary. The preview is not a source of portfolio truth
and must not be exposed to the public or used for trading. It uses a separate
Compose project from AI Trader.

On 2026-10-03, the preview was started on the shared host. The first boot
failed because yfinance needs a writable cache; the staging-only Compose file
now mounts that cache in tmpfs. After the fix, `/health`, `/agent`, and
`/agent/data` returned HTTP 200, the container was healthy at about 139 MiB
idle memory, and the existing AI Trader services remained healthy. This is
only a web-startup smoke test, not scanner/monitor or peak-load validation.
The initial Docker build sent a 535 MB context. A staging-specific Dockerfile
and ignore file reduced the next build context to about 270 kB without changing
the Render image's input set.

`deploy/hetzner/compose.observation.yaml` is a separate one-shot scanner test.
It joins only the staging web network, copies the image's frozen tracker into
tmpfs, runs three explicitly chosen tickers, and writes results/outbox only in
tmpfs. It has no Telegram credentials, GitHub push, production tracker mount,
or restart policy. It tests Chromium and the UI scan path, not live trading,
portfolio persistence, or scheduled execution. Do not run it against the
public production app or mistake its output for live positions.

The 2026-10-03 observation run returned `COMPLETE`, open-access login, and
3/3 result cards (AAPL, MSFT, NVDA) in 23.337 seconds total, 3.97 seconds of
which was the UI scan. It wrote only to a tmpfs copy of the workbook. This
does not prove that a full smart-universe run, TP/SL execution, remote backup,
public TLS, or cutover scheduling works.

On the same day, a monitor observation used a separate tmpfs copy of the
current `main` tracker. It checked one open position, reported `MONITOR_OK`,
wrote a heartbeat, and produced no event/outbox. This was on a Saturday; it
does not validate live-session TP/SL touches or Telegram delivery.

The current `main` portfolio snapshot at commit
`45db36b36155827ada539ecfdf775e7a8bb79eeb` was archived on the server as
`/home/trader/market-lens-backups/portfolio-45db36b36155827ada539ecfdf775e7a8bb79eeb.tar.gz`.
The gzip archive and embedded XLSX ZIP passed integrity checks. It contains
2,142 entries and is owner-readable only. The staging web reads an extracted
copy from `/home/trader/market-lens-staging-data/` through read-only mounts;
`/health`, `/agent`, and `/agent/data` returned HTTP 200, the dashboard reported
`status=ok` and one open position, and all existing AI Trader containers stayed
healthy. This extracted copy is not auto-refreshed and must not be used as a
live portfolio source.

A repo-scoped GitHub deploy key named `market-lens-hetzner-backup-2026-10` is
installed on the host at `/home/trader/.ssh/market_lens_deploy`. GitHub's SSH
host key was verified against its published fingerprint. Repository read and
`git push --dry-run` succeeded. No real push from the server has occurred.

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
