# Market Lens QA Release Gate

Every source change must preserve the active paper-trading baseline. A change is not complete merely because its new unit test passes.

## Required For Every Source Change

1. Run `python -m pytest -q` and record the result.
2. Run `git diff --check` and review the exact source diff.
3. Confirm no credentials, tokens, passwords, or chat IDs were added to source, logs, screenshots, workbooks, or JSON.
4. Confirm the change does not alter active strategy thresholds unless that behavior was explicitly approved.
5. Confirm the production endpoints `/health`, `/agent`, and `/agent/data` return HTTP 200 after deployment.

The `Market Lens Source QA` workflow enforces the source regression suite for changes under `agent/`, `app/`, `tests/`, workflow files, and `pyproject.toml`. Generated `agent_results/` commits do not start this workflow.

## Trading And Portfolio Changes

Changes to entry decisions, exits, sizing, exposure, cash, heat, TP1, TP2, or stop logic require isolated scenario tests for:

- BUY, WATCH, WATCH_READY, SKIP, and HOLD compatibility.
- TP1 partial quantity and cash accounting.
- Stop movement to entry after TP1.
- TP2 and stop closure of the remaining quantity only.
- Same-bar ambiguity and chronological bar processing.
- Replay/idempotency: the same bar or event cannot be applied twice.
- Portfolio, workbook, Decision JSON, dashboard, and Telegram agreement.
- Persistence conflict behavior without stale portfolio overwrite.

Run the real scanner or position-monitor workflow with a bounded timeout after the regression suite. Never manufacture an event in the production portfolio for QA.

## Scanner Changes

Verify login, scan completion, requested and returned ticker counts, result-card parsing, Decision JSONL, summaries, Excel update, chart retention, and absence of `AUTH_FAILED`, `RUN_FAILED`, or a fake zero-setup result. Off-hours QA must not create `BUY_SIMULATED`.

## Monitor Changes

Verify every open position is checked, malformed or missing prices fail visibly, no event is produced without a real bar touch, successful no-event runs update the monitor heartbeat, and notifications are sent only after portfolio persistence succeeds.

## UI Changes

Test production at desktop and 390px mobile widths. Check console errors, horizontal overflow, broken images, all changed controls, collapsed sections, modal open/close behavior, chart enlargement, and incremental pagination. Compare visible position values with `/agent/data`.

## Release Evidence

The completion note must include:

- Source commit.
- Test count and result.
- Real workflow run ID when scanner or monitor behavior changed.
- Production endpoint status.
- UI viewports and interactions checked when UI changed.
- Any untested edge case or remaining risk.

If any required check fails, stop the upgrade sequence, fix the regression, and repeat the complete applicable gate.
