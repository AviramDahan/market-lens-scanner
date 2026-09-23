# Market Lens E2E Improvement Plan

This plan is executed one upgrade at a time. Each upgrade has its own QA gate.
The next upgrade starts only after the previous gate passes and the owner approves
continuation.

## Upgrade status

| # | Upgrade | Status | QA gate |
| --- | --- | --- | --- |
| 1 | Live price provenance and consistency | COMPLETE - APPROVED | API contract, 496 tests, desktop/mobile UI, production smoke |
| 2 | Explicit partial-scan status taxonomy | COMPLETE - APPROVED | Runtime/API/UI status tests and partial-provider simulation |
| 3 | Workbook update performance | COMPLETE - APPROVED | Output-equivalence test, tracker integrity, measured runtime comparison |
| 4 | Market-regime freshness and fallback | COMPLETE - PENDING OWNER APPROVAL | stale/missing/provider-session fixtures and conservative behavior verification |
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

Acceptance criteria:

- Runtime records use `COMPLETE`, `PARTIAL_OK`, `FAILED` or `AUTH_FAILED`.
- A partial scan with usable cards still updates decisions and the paper tracker.
- A zero-card scan, authentication failure or terminal UI failure cannot appear successful.
- `/agent/data` exposes requested, received, missing and coverage percentage.
- Historical `OK` / `ISSUES` summaries are normalized without rewriting old files.
- Health checks accept `PARTIAL_OK` while reporting its coverage and missing count.

QA result (2026-09-23):

- Full suite passed: 509 tests.
- A simulated 136/139 provider response normalized historical `OK` to `PARTIAL_OK`
  and retained all 136 usable result cards.
- Production API reported `PARTIAL_OK`, `scan_complete=false`, 136/139 results and
  three unavailable symbols for run `20260923_113131`.
- Production desktop (1440px) and mobile (390px) rendered the status and coverage
  without horizontal overflow or console errors.
- Dashboard workbook reads now close their read-only file handle after extraction.
- Scanner strategy, trade gates, position sizing, TP/SL monitor and paper positions
  were not changed.

## 3. Workbook update performance

Profile the current workbook path, then reduce repeated workbook reads, writes and
recalculation while preserving every sheet and accounting result. Compare generated
workbooks cell-for-cell on representative runs before activation.

Acceptance criteria:

- Cash, cooldown, neutral-pilot and trade-performance calculations remain identical.
- Trade analytics reuse the Smart Universe sector map already loaded by the run.
- Cash and entry-control state are read from Trade Log in one pass.
- Runtime metrics separate workbook load/save from market context, candidate
  evaluation, chart retention, outcome backfill, analytics and summaries.
- No tracker sheet, row, historical record, strategy gate or monitor behavior changes.

QA result (2026-09-23):

- Full suite passed: 511 tests.
- The production tracker retained identical values in every cell after the new
  read paths were exercised.
- Cash, recent stop cooldowns, neutral-pilot count, realized P/L ($591.82) and
  39 completed trade lifecycles matched the previous calculations.
- The one-pass ledger snapshot reduced the three initial Trade Log passes to one;
  measured state-read time fell from 9.3ms to 3.8ms on the current tracker. The
  post-write cash calculation now shares the analytics read instead of scanning
  the sheet separately.
- The analytics reader completed in 14.6ms without refreshing external universe
  data. The tracker still requires roughly 9-10 seconds to load and 13 seconds to
  save locally; those costs are reported rather than hidden.
- Production timing validation remains the deployment gate: the next scheduled
  scan must publish `workbook_phase_seconds` and complete without accounting or
  tracker-integrity errors.

Production validation (2026-09-24):

- Eight consecutive instrumented scanner runs completed successfully with no
  runtime or accounting errors. All published `workbook_phase_seconds`.
- Instrumented workbook-update time ranged from 105.0s to 138.4s (median
  124.2s). The two immediately preceding runs measured 119.6s and 169.7s, so the
  sample does not justify claiming a precise percentage speedup.
- The latest representative run spent 73.2s evaluating candidates and 29.5s
  applying chart retention, versus 6.3s loading, 8.7s saving and 0.014s on trade
  analytics. Candidate and chart work, not ledger parsing, dominate this phase.
- A normal post-deployment paper entry opened APP and persisted its trade ID,
  quantity and cash debit. Five positions remained readable through `/agent/data`.
- Production `/health`, `/agent` and `/agent/data` returned healthy responses.
- Result: the safe read consolidation and timing instrumentation are accepted by
  QA. This upgrade is a modest persistence optimization plus a material
  observability improvement, not a claim that the full scan became dramatically
  faster.

## 4. Market-regime freshness and fallback

Record freshness coverage for every regime input. Use the latest completed exchange
session and a bounded last-known-good cache when the provider misses a bar. Mark the
regime `DEGRADED` when evidence is incomplete and retain conservative entry behavior.

Implemented behavior:

- Regime arithmetic excludes the current unfinished daily candle and any future
  provider session. After the exchange close, that day's completed candle becomes
  eligible.
- SPY, QQQ, IWM, VIX, US10Y and DXY each record expected session, effective session,
  provider freshness, effective source and fallback age.
- The last valid completed-session benchmark state is persisted under
  `agent_results/regime/market_regime_lkg.json` and may be reused for at most three
  completed NYSE sessions.
- A fallback or missing input produces `market_regime_data_status=DEGRADED` without
  overwriting the directional `BULL/NEUTRAL/BEAR` evidence.
- Missing SPY or QQQ evidence after fallback blocks new buys. A missing/fallback core
  volatility input cannot leave an otherwise bullish calculation in BULL; it uses
  conservative NEUTRAL thresholds and exposure. Missing optional macro inputs make a
  zero contribution but do not invent a trade blocker.
- Healthy completed-session data retains the existing contribution weights, regime
  thresholds, entry gates and dynamic-exposure calculation.
- Decision JSON, run summaries and the per-ticker checklist expose the data-quality
  status and human-readable reason.

Source QA result (2026-09-24):

- Full suite passed: 521 tests.
- Fixtures verified regular-session candle exclusion, post-close inclusion, valid
  fallback, expired fallback rejection, future-session rejection, missing-core entry
  blocking, VIX degradation and malformed-cache recovery.
- A live provider smoke test returned all six inputs from the latest completed NYSE
  session, `data_status=HEALTHY`, `allows_new_buys=true` and no warnings.
- Python compilation and `git diff --check` passed. Production deployment and one
  persisted scanner-run validation remain the release gate.

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
