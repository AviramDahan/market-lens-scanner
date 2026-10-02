# TP/SL Execution Reliability Audit

Date: 2026-10-02

## Scope

This audit verifies the complete paper-trading position-management chain. It
does not change entry scoring, gates, sizing, exit rules, active portfolio
state, or historical data.

## Execution map

1. `app/render_shadow_monitor.py` polls prices and detects possible events in
   read-only Shadow mode. It cannot dispatch, update Excel, or mutate the
   portfolio.
2. `/agent/monitor-live` in `app/main.py` is the active sensor called by the
   external scheduler. It reads 1-minute high/low data and dispatches the
   GitHub monitor workflow only when TP1, TP2, or Stop is touched.
3. `app/monitor_trigger.py` detects the event and sends `workflow_dispatch`.
4. `.github/workflows/market-lens-position-monitor.yml` runs
   `agent/position_monitor.py`, serializes repository writes, rebases on the
   latest portfolio state, persists the workbook/results, and sends Telegram
   notifications only after persistence succeeds.
5. `agent/position_monitor.py` is the executor. TP1 closes half, moves the
   remaining stop to entry, and removes open risk. TP2 or Stop closes the
   remaining quantity. Trade identity and event cursors prevent replay.

## Root cause

The observed reliability gap was not a TP/SL calculation or workbook-write
bug. The persisted executor heartbeat was stale because the scheduled GitHub
workflow had not executed during the inspected window. GitHub's quarter-hour
schedule was historically sparse and delayed. At the same time, the API
reported the healthy Render Shadow poller under `live_monitor_*`, which could
be mistaken for proof that the side-effecting executor was healthy.

The external `/agent/monitor-live` path also left no runtime evidence when it
found no event. Therefore the system could prove that prices were being read,
but could not prove that the active event sensor was being called.

## Missed-event audit

The open CME trade was checked against available Yahoo 1-minute bars from the
last persisted heartbeat (`2026-10-01T18:43:14Z`) through
`2026-10-02T18:06:00Z`:

- 382 bars inspected, including extended hours.
- Maximum high: `268.8645`.
- Minimum low: `260.0599976`.
- Remaining TP2: `291.37`.
- Stop after TP1: `254.30` (entry/breakeven).
- Result: no TP2 or Stop touch occurred in the missing-heartbeat window.

This is verified against the available Yahoo feed, not a broker-grade trade
feed. No active or historical portfolio records were changed by the audit.

## Targeted fix

- Added an in-process heartbeat for the active `/agent/monitor-live` sensor.
- Added a non-sensitive `/agent/monitor-execution-status` endpoint.
- Separated health evidence into:
  - `price_sensor_*`: read-only Shadow price freshness.
  - `execution_sensor_*`: active cron sensor and dispatch readiness.
  - `executor_persistence_*`: evidence that a dispatched workflow persisted.
- Added compact Open Positions indicators for Price feed, TP/SL trigger, and
  Portfolio update.
- Moved the 15-minute GitHub fallback away from overloaded quarter-hour
  boundaries without changing frequency or the New York market-hours guard.
- Updated production smoke validation so a healthy Shadow sensor cannot hide
  an inactive active sensor, and a successful dispatch must gain matching
  persistence evidence.

## Production evidence before deployment

- `/health`, `/agent`, and `/agent/data`: HTTP 200.
- Render revision: `80343f7692e66eb9071776b3a4954fc88865db97`.
- Latest scan: `PARTIAL_OK`, 122/124 results, 39 technical setups, 0 trade-ready.
- One open CME position; TP1 already persisted, remaining stop at entry.
- GitHub Position Monitor run `37045965896`: success, heartbeat
  `2026-10-02T18:14:26`.
- Render Shadow sensor: running, 5,971 polls, zero consecutive failures,
  side effects disabled.

## Deployment status

The fix is review-ready on `codex/tp-sl-execution-reliability-release`. It has not been
pushed, merged, or deployed. Production is healthy at the time above, but the
new observability and scheduling hardening are not yet proven in production.

## Exact post-deploy verification

1. Confirm `/health` reports the deployed commit.
2. Call `/agent/monitor-live` once with the configured secret during the due
   window; a no-event response is acceptable.
3. Confirm `/agent/monitor-execution-status` shows a fresh `last_check_at` and
   no secret/token fields.
4. Confirm `/agent/data` shows `price_sensor_status=CURRENT` and
   `execution_sensor_status=CURRENT` while a position is open in regular hours.
5. Use an isolated fixture only to trigger TP1 and verify GitHub dispatch,
   half-quantity persistence, stop moved to entry, one Position Event, one
   Trade Log row, and one post-persistence notification.
6. Replay the same fixture/event and verify no duplicate event, trade row, or
   notification.
7. Run `python -m agent.production_smoke` with a temporary report path.
