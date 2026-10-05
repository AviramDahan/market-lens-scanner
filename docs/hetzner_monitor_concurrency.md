# Hetzner Scanner and Position Monitor

The paper portfolio has one writer lock. The minute monitor and the long UI
scanner have separate systemd services and timers. The scanner must never hold
the writer lock while it is collecting or evaluating ticker results.

## Scanner transaction

1. Under the writer lock, verify the runtime revision and notification outbox,
   hash the live tracker, and copy the tracker/results to a private staging
   directory outside the Git checkout.
2. Release the lock. Run the unmodified UI agent in a Docker worker whose three
   writable mounts point to staging. No staged BUY or Telegram record is live.
3. Require a successful, nonempty scan record. Reacquire the writer lock and
   verify that the live tracker hash still matches the snapshot.
4. If the tracker changed, do not apply any staged decision or notification.
   Preserve new run diagnostics and alert operations. The next normal scan can
   evaluate the new portfolio state.
5. Otherwise, promote changed result files and the staged tracker atomically,
   run postprocessing, push the portfolio/results to GitHub, and only then
   deliver trade notifications. Release the lock and discard redundant stage
   copies after success.

The monitor keeps using the live tracker and the same writer lock. Its no-event
heartbeat stays local. For a TP/SL event, a persistence receipt appears only
after the updated portfolio was pushed and notification delivery completed.

## Operational checks

- `market-lens.timer` runs the monitor every minute of regular trading.
- `market-lens-scanner.timer` checks the existing New York scan schedule every
  minute; a scan occupies only its own service.
- During an eight-minute scan, the monitor heartbeat should continue to move
  every one or two minutes. A stale heartbeat with an open position is an
  operational incident, not a healthy scan.
- `/agent/data` uses the host heartbeat for the Price feed and TP/SL trigger
  indicators. An event is not marked persisted from heartbeat alone.
- A scanner stage that failed or became stale remains outside the live checkout
  with new diagnostics; redundant copied history is removed from that stage.

## Deployment and rollback

Back up the current tracker/results and unit files before a cutover. Build both
runtime images for the exact `main` revision, install both service/timer units,
verify the Compose staging override mounts, and start both timers. Do not run
the legacy GitHub or Render writers in parallel. Validate a no-trade scan on an
isolated tracker and a regular-session monitor heartbeat before declaring the
cutover complete.

If the new scanner path fails, disable only its timer while keeping the live
monitor active. Reconcile the current tracker and GitHub commits before
reverting code. Never restore an old tracker over newer TP/SL events.
