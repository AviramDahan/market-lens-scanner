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

## 2026-10-03: read-only public preview

The free hostname `https://market-lens.2.28.100.77.sslip.io` resolves to the
Hetzner server. It is **not** the production cutover. The shared Caddy proxy
routes this hostname to `market-lens-runtime-web` and rejects POST, PUT,
PATCH, and DELETE with HTTP 503. Its original AI Trader route is preserved.
The Caddy source is `deploy/hetzner/Caddyfile.shared-preview`; the host copy is
`/opt/ai-trader-staging/deploy/Caddyfile`. Before applying the preview, the
original Caddyfile was saved to
`/home/trader/market-lens-backups/ai-trader-Caddyfile-before-market-lens-2026-10-03`.
The bind-mounted container file was read-only and still referred to the old
inode after a host-side copy, so the active Caddy configuration was reloaded
from a validated temporary copy. A container recreation will read the updated
host path. Public HTTPS GETs for `/health`, `/agent`, and `/agent/data`
returned 200; a POST to `/agent/trigger-scan` returned 503. All AI Trader
containers remained healthy. The dashboard is a read-only preview of a GitHub
snapshot, not a live portfolio feed.

The host also has a shallow clone at `/home/trader/market-lens-runtime`, a
read-only runtime web service at loopback port 18082, and a worker image with
Chromium. The web service and worker share the same bind-mounted tracker and
results; the worker has no automatic restart. The runtime image was built
without embedding the tracker/results in its Docker context. The host has a
repo-scoped GitHub write deploy key, but only a dry-run push was tested. The
writer remains **disabled** through both host flags in
`/home/trader/.config/market-lens/host.env`; no timer is installed or enabled.
The runtime credentials are placeholders, and no Telegram token is installed.

An isolated smart-universe scan on the same host selected 128 fresh tickers
against a 150-ticker target, read 126 result cards, and finished `PARTIAL_OK`
without runtime errors. Two missing cards (ARM and MMC) were still unavailable
after focused recovery. The UI scan took 130.8 seconds; the full run, including
universe construction and workbook/summary work, took 261.331 seconds. This
was a Saturday test against a temporary workbook with positions cleared and
no notification or GitHub write credentials. The 150-ticker target was not
reached because the current universe supplied only 128 eligible candidates,
not because the host timed out. The host had about 5.2 GiB available memory
and 49 GiB free disk afterward; AI Trader containers remained healthy.
The read-only container emitted Matplotlib/fontconfig cache warnings; writable
tmpfs cache paths were configured afterward and still need a worker recheck.

Build both runtime images with `MARKET_LENS_SOURCE_REVISION` set to the exact
checkout commit; `/health` and image labels then expose that revision for
deployment verification. The host-side `deployed_revision` must match the
built code before either writer is enabled.

### Required cutover gates

1. Test a full smart-universe scan against an isolated workbook copy; compare
   duration, result cards, failures, and memory usage with the current Actions
   path. Run synthetic TP1/TP2/SL tests in isolation. A weekend no-op monitor
   check is not live-session proof.
2. Install the current paper-only credentials and Telegram destination as
   owner-readable host secrets without logging their values. Rotate any bot
   token previously exposed in chat. Verify login without screenshots showing
   credentials.
3. Confirm all old scanner and monitor triggers (cron-job.org, GitHub Actions
   schedules/dispatches, and Render live dispatch) have stopped. Keep the
   separate one-time weak-sector reminder active until its October 13 receipt
   exists, or migrate it with an atomic receipt check. Never enable two writers.
4. Refresh the runtime checkout from the latest `main`, take and verify a new
   tracker/results backup, compare its revision with the built images, and set
   the two host flags only after old writers are confirmed off. Install/enable
   `market-lens.timer` and watch the first scanner and monitor runs. Require
   actual GitHub backup commits, monitor heartbeat and portfolio persistence,
   and Telegram receipts before treating the new path as live.
5. Keep Render and the old Actions configuration available as rollback while
   validating a regular-session position check. Never restore an old workbook
   over newer trades: pause the writer, reconcile GitHub/local versions, and
   use a fresh snapshot before switching back.

Until all gates pass, `https://market-lens-scanner-fb63.onrender.com/` remains
the active app. The sslip.io address is an infrastructure preview only.

## First cutover attempt: rolled back

On 2026-10-03, the legacy scanner and monitor workflows were disabled after a
verified, versioned backup and an idle-writer check. The Hetzner monitor
evaluated one open position (`MONITOR_OK`, zero events) without changing the
portfolio. The first live Hetzner scanner selected 130 tickers and read 128
result cards, but workbook serialization failed with `IO_ENOSPC`: the worker's
256 MiB `/tmp` tmpfs was too small for openpyxl's temporary worksheet XML.
The partially written local tracker was only about 8 KiB. **No portfolio
commit, Telegram notification, or trade was pushed by that failed scan.**

The timer and host writer flags were turned off; Caddy was returned to the
read-only preview route. The full failed local state was archived as
`/home/trader/market-lens-backups/failed-hetzner-cutover-20261003_185943.tar.gz`,
with the untracked chart/decision/screenshot files preserved separately under
`/home/trader/market-lens-backups/failed-hetzner-cutover-files-20261003_185943`.
Only files changed by that failed run were restored from the unchanged GitHub
`main` commit. The tracker SHA-256 again matched GitHub, its XLSX ZIP passed
integrity testing, and the runtime checkout became clean. The two legacy
writer workflows were re-enabled; the separate October 13 reminder stayed
active. This is a rollback, not a completed migration.

The follow-up fix binds the worker's `/tmp` to a dedicated disk-backed
directory under `/home/trader/market-lens-runtime-state/tmp`, and makes normal
scanner and monitor workbook saves atomic with XLSX integrity validation.
Before another writer switch, create the host directory, test serialization of
a full copy of the current workbook inside the worker, run the regression
suite, and repeat the single-writer cutover sequence. Do not treat the healthy
web preview as proof that the worker can persist a large tracker.
