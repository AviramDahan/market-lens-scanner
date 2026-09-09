# Separate Daily and Cumulative Recorded-Equity Changes

## Observed defect

The agent passed change from initial capital as `daily_return_pct`. The recorded
September 7/8 summaries showed equity of $101,560.42 / $101,586.53, but September
8 reported a daily return of 1.5865%. That is cumulative from $100,000. The
change between those recorded day snapshots is approximately 0.0257%.

## New summary calculation

The writer computes daily change against the immediately preceding calendar
day's saved equity, using the existing report-date grouping. It ignores the
legacy lifetime value supplied under the daily field and stores cumulative
change separately as `cumulative_return_pct` when starting capital is known.

Metadata records calculation basis `recorded_equity_calendar_day_v1`, reference
date/equity and status. Missing, malformed, mismatched-date or nonpositive prior
equity gives null daily return, not a fabricated zero or multi-day daily return.
An intraday rerun uses the same prior-day reference, not its earlier same-day run.
Unchanged equity produces zero. A zero current equity correctly produces -100%.

The writer copies input portfolio data; it does not modify decisions, positions,
cash, gates, prior-day files, or historical summaries. Existing normal current-day
and weekly summary writes continue. JSON and Markdown distinguish daily/cumulative
values. Compatibility tests cover the summary writer and missing-data behavior.

## Explicit limits

This is a recorded-equity snapshot change, not a validated strategy return,
deposit-adjusted time-weighted return, or exchange-close-only measurement. Existing
calendar-date grouping remains unchanged; migration to New York trading-session
boundaries is separate work. A missing prior calendar day remains unknown rather
than silently using an older trading day. Baseline equity can inherit historical
ledger errors; this does not validate or reconcile the GILD/CHTR records.
