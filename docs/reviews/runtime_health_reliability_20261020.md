# Runtime health repairs: 20 October 2026

## Release Scope

This release preserves active strategy selection, scoring weights, entry gates,
position sizing, risk limits, exit policy and historical financial records.

- Scanner stages read run-addressed history through a separate read-only mount.
  Mutable summaries, regime cache, experiment state and the workbook remain
  private copies. There are no writable hard links to the live portfolio.
- Promotion refuses replacement of historical links. A failed promotion rolls
  back its writes; a portfolio hash mismatch still rejects stale scanner output.
- Scanner dashboard/workbook postprocessing runs in its isolated stage before
  the live writer lock. Git backup and post-persistence alert delivery retain
  their existing ordering. A waiting monitor reserves the next writer turn.
- Writer-lock waiting and holding durations are logged for operational evidence.
- The scanner scheduler catches a recent missed slot within 30 minutes, keeps
  durable slot state, coalesces older backlog explicitly, and allows at most two
  attempts per slot. It cannot start concurrent scanner scheduler processes.
  Interrupted work may be retried; this is not a financial exactly-once claim.
- Invalid session-volume observations are evicted conditionally from the hourly
  cache, so focused UI retries reach the provider. Newer concurrent valid data
  are never evicted. Missing session VWAP still fails closed.
- Invalid, absent or zero aggregate volume cannot manufacture profile levels,
  including the constant-price shortcut. Weekly minimum history stays at 200;
  a short-lived listing remains unavailable rather than receiving an invented MA.

## Outstanding Separate Work

- Source-repository size warnings require a verified archive/checkpoint cutover.
  Read-only staging reduces transient host disk usage, not Git history size.
  Draft PR 46 is a mirror prerequisite, not an activated replacement for the
  per-event backup contract. Do not stop Git backup or its delivery barrier
  merely because a mirror upload succeeds.
- Historical TRGP/TGTX/PFE exits remain annotated. The 13 October report found
  stale scanner execution prices and retained contradictory evidence. No cash
  credit or rewritten trade has been justified or applied.
- A natural live TP/SL event cannot be proven while the portfolio is empty.
  Linux isolated tests and healthy no-event heartbeats are separate evidence.

## QA

Run `python -m pytest -q`, the dashboard JavaScript regression tests, and
`git diff --check`. Linux CI must execute the real flock and symlink tests that
Windows cannot run without elevated symlink privileges. Then verify a real
normal scan, monitor heartbeat, persistence, endpoints, chart links and disk/
lock metrics before claiming production completion. No test trade or real
Telegram test is part of this release.
