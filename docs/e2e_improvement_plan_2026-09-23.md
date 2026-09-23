# Market Lens E2E Improvement Plan

This plan is executed one upgrade at a time. Each upgrade has its own QA gate.
The next upgrade starts only after the previous gate passes and the owner approves
continuation.

## Upgrade status

| # | Upgrade | Status | QA gate |
| --- | --- | --- | --- |
| 1 | Live price provenance and consistency | COMPLETE - PENDING OWNER APPROVAL | API contract, 496 tests, desktop/mobile UI, production smoke |
| 2 | Explicit partial-scan status taxonomy | NOT STARTED | Runtime/API/UI status tests and partial-provider simulation |
| 3 | Workbook update performance | NOT STARTED | Output-equivalence test, tracker integrity, measured runtime comparison |
| 4 | Market-regime freshness and fallback | NOT STARTED | stale/missing/provider-session fixtures and conservative behavior verification |
| 5 | Focused recovery for unavailable tickers | NOT STARTED | retry/normalization fixtures and bounded runtime validation |
| 6 | Automated production smoke workflow | NOT STARTED | intentional pass/failure runs with actionable diagnostics |
| 7 | Move suitable live services from Actions to Render | NOT STARTED | parallel shadow run, failover test, cost/runtime comparison |

## 1. Live price provenance and consistency

Expose the persisted tracker mark separately from the latest available one-minute
quote. Every refreshed position records the quote timestamp, market-session label,
freshness, source and usage contract. The dashboard shows this evidence beside the
price so regular, pre-market, after-hours and stale quotes cannot look identical.

Acceptance criteria:

- `/agent/live-prices` retains both persisted and refreshed prices.
- Every successful quote has source timestamp, market phase and freshness status.
- A failed live fetch is explicitly labelled as a persisted tracker fallback.
- The dashboard identifies the latest quote and its recorded time.
- The endpoint explains that UI refresh is not a persisted fill and that the
  server monitor validates TP/SL events with one-minute high/low data.
- Existing TP/SL detection and portfolio accounting tests remain green.

QA result (2026-09-23):

- 45 focused API, dashboard and monitor tests passed.
- Full suite passed: 496 tests.
- Local live endpoint retained the CME tracker mark of $275.53 while labelling
  the latest $268.00 quote as stale after-hours data.
- Desktop (1440px) and mobile (390px) rendered the evidence without horizontal
  overflow or console errors.
- Existing TP/SL monitor behavior was unchanged.

## 2. Explicit partial-scan status taxonomy

Replace the conflicting `OK` plus `scan_complete=false` presentation with explicit
`COMPLETE`, `PARTIAL_OK`, `FAILED` and `AUTH_FAILED` states. Keep usable partial
results while making missing-symbol coverage visible to health checks and users.

## 3. Workbook update performance

Profile the current workbook path, then reduce repeated workbook reads, writes and
recalculation while preserving every sheet and accounting result. Compare generated
workbooks cell-for-cell on representative runs before activation.

## 4. Market-regime freshness and fallback

Record freshness coverage for every regime input. Use the latest completed exchange
session and a bounded last-known-good cache when the provider misses a bar. Mark the
regime `DEGRADED` when evidence is incomplete and retain conservative entry behavior.

## 5. Focused recovery for unavailable tickers

Retry only missing result cards after the main batch. Normalize provider symbols and
record a structured `DATA_UNAVAILABLE` reason after bounded retries. Never restart a
successful full basket because a few symbols are unavailable.

## 6. Automated production smoke workflow

Create a short post-change workflow covering production endpoints, one ticker scan,
chart rendering, Decision JSON, tracker readability and a no-event monitor check.
Keep it separate from scheduled scans to avoid unnecessary Actions usage.

## 7. Move suitable live services from Actions to Render

Evaluate running price polling, the TP/SL sensor, cache and heartbeat on the paid
Render service. Activate only after a shadow period proves event parity and GitHub
Actions remains available as a low-frequency fallback.
