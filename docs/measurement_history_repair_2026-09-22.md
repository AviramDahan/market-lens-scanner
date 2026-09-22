# Measurement history repair - 2026-09-22

## Scope

Read-only measurement and persistence fixes. No strategy, entry gates, capital
limits, sizing, monitor, Telegram delivery or trading actions are changed.

## Retention

Structured files evicted from the active window are now compressed into an
`archive/` subdirectory before the active copy is removed. SHA-256 filenames,
read-back verification and an explicit revision index prevent overwriting
history or selecting revisions based on checkout modification times. Daily and
weekly summaries are excluded from window eviction. Existing bounded media
retention is unchanged; this fix is for structured research data.

The generated-state bundle enumerates untracked files individually. Otherwise a
new archive directory would appear as one directory and would not be copied.

Daily/weekly decision aggregation reads active and archived records once per
run. Active outcome backfills take precedence. Archive corruption or ambiguous
revisions raise an error instead of silently producing a smaller sample.

## Historical recovery

`python -m agent.recover_measurement_history --since 2026-09-01 --output outputs/recovered_measurements_20260922`

This bounded Git-history recovery stages data separately, with a manifest of
source commits and hashes. It never changes the tracker, source commits, or
production files. Files never committed cannot be recovered. Historical equity
is not inferred from today's equity. The summary explicitly labels history as
not independently verified; archive support alone is not proof of completeness.

Recovery found 1,509 files. The verified ZIP and source manifest are retained at:

https://github.com/AviramDahan/market-lens-data-archive/releases/tag/measurement-history-recovery-2026-09-22

The ZIP contains 1,513 entries (1,509 source files, three revision indexes and
the manifest), passed a full CRC check, and has SHA-256
`5813554a07282eb31780411b98a0dad748695f9f9c3a9cb6c07ce94993ae89cd`.
Recovered decision coverage:

- 2026-W38: 155 decision files, 20,974 decisions, 2 BUY_SIMULATED records.
- 2026-W39 through September 21: 28 files, 3,799 decisions.

These are decision records, not independent signals or completed trades. The
historical ZIP is available for research but is not merged into the production
checkout. The live weekly summary therefore covers retained and subsequently
archived production files; historic weeks require explicit archive import.

The missing September 21 daily summary was recovered as an equity-only record
from `dashboard_snapshot.json` at commit
`cab77125a336fc876006f209a861384ec6d1cb74`. It carries the recorded
end-of-day portfolio value of $101,744.80, source timestamp and coverage label.
It supplies a reference for subsequent daily-return calculation without
claiming recovered scan, exit or trade statistics for that day.

## Cohorts

Candidate telemetry retains the scanner's original legacy identity and records
the currently selected candidate separately. Selection-rank fallback supports
older payloads. This does not change which candidate is bought.

Summaries separate actual `active_setup_selection_policy` values and group
available trade metrics by strategy and execution-model versions. Missing
versions remain Unknown, not retrospectively assigned based on dates.
Capital-schema coverage is separate from strategy cohorts: the September 1
capital upgrade and September 16 selection/execution activation must not be
treated as one intervention. Existing positions remain in their original cohort.

## Validation and remaining limitations

Regression tests cover archival round trips, permanent summary retention, active
backfill precedence, revision-index selection, new-directory persistence,
legacy-versus-selected identity, cohort labels and Git recovery isolation.

The original archive repository is public. An automated scan of the recovered
payload found no email addresses or common credential/token patterns, but that
does not prove the absence of every possible sensitive value. Other daily
equity gaps remain explicit and have not been inferred.
