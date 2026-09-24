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
| 4 | Market-regime freshness and fallback | COMPLETE - APPROVED | stale/missing/provider-session fixtures and conservative behavior verification |
| 5 | Focused recovery for unavailable tickers | COMPLETE - APPROVED | focused retry, provider normalization, bounded runtime and structured outcomes |
| 6 | Automated production smoke workflow | COMPLETE - APPROVED | automatic source-QA chain and verified read-only production contract |
| 7 | Move suitable live services from Actions to Render | SHADOW ACTIVE - PARITY PENDING | parallel shadow run, failover test, cost/runtime comparison |

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
- Python compilation and `git diff --check` passed.

Production QA result (2026-09-24):

- GitHub Actions run `35930298904` completed successfully in 5m59s on source commit
  `ce2ce3337`; the scan itself took 80.2 seconds.
- Run `20260923_224929` persisted 136/139 result cards (`PARTIAL_OK`, 97.84%
  coverage) with no runtime, authentication or workbook errors.
- All 136 Decision JSON records contain the new market-regime quality contract.
  Every benchmark used `CURRENT_COMPLETED_SESSION` for 2026-09-23, the run was
  `HEALTHY`, new buys were permitted by data quality, and fallback was not needed.
- The bounded cache persisted all six benchmark states under `agent_results/regime`.
- The after-hours run opened no new position: five existing positions remained
  `HOLD`; no off-hours `BUY_SIMULATED` was created.
- Production `/health` and `/agent/data` returned HTTP 200. The per-ticker production
  checklist rendered data status, completed session, source and fallback age for all
  six inputs.

## 5. Focused recovery for unavailable tickers

Retry only missing result cards after the main batch. Normalize provider symbols and
record a structured `DATA_UNAVAILABLE` reason after bounded retries. Never restart a
successful full basket because a few symbols are unavailable.

Implementation result (2026-09-24):

- The normal batched scan remains unchanged. A second phase receives only the final
  missing-symbol set; successful symbols are never placed in a recovery basket.
- Provider aliases such as `BRK.B`, `BRK/B` and `BRK-B` share one canonical key.
- Recovery is bounded to 12 symbols, two rounds, four symbols in the first request
  and singleton requests in the final round. It stops when fewer than 90 seconds
  remain in the Agent deadline.
- A failed recovery request is isolated from the successful main scan. Every initially
  missing symbol records `RECOVERED` or `DATA_UNAVAILABLE`, attempt count and a bounded
  provider/failure reason in runtime diagnostics.
- The dashboard run strip reports recovered count and exposes the recovered or still
  unavailable symbols through its tooltip. Diagnostic snapshots retain the same data.
- Local QA passed 527 tests, Python compilation and whitespace validation. Dedicated
  fixtures cover partial recovery, repeated provider failure, alias normalization,
  disabled recovery and deadline preservation.

Production QA result (2026-09-24):

- GitHub Actions run `35957488505` completed successfully in 4m33s on source commit
  `ddf3d5494`; no authentication, Agent, workbook or persistence step failed.
- Run `20260924_045343` requested 136 symbols and received 133 unique result cards.
  Only `ARM`, `MMC` and `RDDT` entered focused recovery; each received two attempts
  and a provider-specific `DATA_UNAVAILABLE` outcome.
- Recovery issued four requests (one grouped request plus three singleton requests)
  and added 3.007 seconds. The full scan took 56.975 seconds, remained far below the
  30-minute workflow limit and did not rescan any successful symbol.
- The persisted Decision JSON contains 133 records and 133 unique tickers. The run
  remained valid `PARTIAL_OK`, workbook updates happened after the valid scan, and
  no false zero-result or duplicate decision was produced.
- Production `/health` and `/agent/data` returned `ok`. Manual `/agent` verification
  showed the canonical status and 133/136 coverage; its recovery tooltip listed the
  three unavailable symbols and confirmed that focused recovery completed. Browser
  console inspection found no warnings or errors.

## 6. Automated production smoke workflow

Create a short post-change workflow covering production endpoints, one ticker scan,
chart rendering, Decision JSON, tracker readability and a no-event monitor check.
Keep it separate from scheduled scans to avoid unnecessary Actions usage.

Implementation result (2026-09-24):

- Added a separate `Market Lens Production Smoke` workflow. It runs manually or
  automatically only after `Market Lens Source QA` succeeds on `main`; it has no
  schedule and therefore does not add recurring idle Actions usage.
- The workflow has read-only repository permission, an eight-minute hard timeout,
  concurrency cancellation and a 14-day JSON report artifact.
- Render `/health` now exposes the deployed commit revision. The smoke waits for the
  exact source revision before testing, avoiding false passes against an old deploy.
- The smoke verifies `/health`, `/agent`, `/agent/data`, latest Decision JSONL schema
  and uniqueness, tracker XLSX readability and required sheets, and latest position
  monitor status with zero failed positions.
- It performs one real `MSFT` UI scan with chart generation through a new backwards-
  compatible `persist_setups=false` request flag. This path cannot save a setup,
  change portfolio state, dispatch the monitor or send Telegram notifications.
- Existing UI behavior remains unchanged because `persist_setups` defaults to true.
  Local QA passed 540 tests, Python compilation, workflow YAML validation and
  whitespace validation.

Production QA result (2026-09-24):

- Source QA run `35997776639` completed successfully in 1m21s and automatically
  dispatched Production Smoke run `35997925981`; no manual chaining was required.
- The smoke matched Render revision `ae766fee3930` before testing and completed in
  1m04s, including deployment wait, dependency setup and artifact upload.
- Production `/agent` and `/agent/data` loaded successfully. The latest run was
  `PARTIAL_OK` with 133 results, and its Decision JSONL contained 133 valid unique
  records.
- The 22,094,665-byte tracker opened successfully with all nine worksheets and all
  required operational sheets.
- The read-only `MSFT` scan generated a valid 175,347-byte PNG in 17.1 seconds and
  returned zero persisted setups.
- The latest position monitor was `MONITOR_OK`: five positions checked, zero failed,
  zero events and no dispatch attempt. The JSON smoke report was uploaded with a
  14-day retention period.

## 7. Move suitable live services from Actions to Render

Evaluate running price polling, the TP/SL sensor, cache and heartbeat on the paid
Render service. Activate only after a shadow period proves event parity and GitHub
Actions remains available as a low-frequency fallback.

Shadow implementation result (2026-09-24):

- Added one bounded asyncio task to the existing `market-lens-scanner` Starter web
  service. No Worker, datastore, additional instance or paid Render resource was
  created; the projected Render charge remains $7/month.
- The task is explicitly observation-only. It reads the lightweight dashboard
  snapshot, reuses the existing one-minute quote cache and TP/SL event detector,
  and records only bounded in-memory evidence. It cannot call GitHub dispatch,
  Telegram notification or portfolio/workbook persistence code.
- Polling is limited to the regular NYSE session, starts after a 20-second service
  warm-up, runs every 60 seconds and has a 45-second cycle timeout. Failures are
  contained and reported by type without exposing provider or account secrets.
- `/agent/monitor-shadow-status` exposes enabled/running state, last session,
  duration, positions checked, event count, warnings and failure count. The payload
  always declares `mode=shadow` and `side_effects_enabled=false`.
- Local QA exercised the real background lifecycle against five paper positions.
  It detected an event candidate without dispatching GitHub, sending Telegram or
  changing portfolio state. The normal `/agent/monitor-live` endpoint remained
  independent and returned HTTP 200.
- Full source QA passed 546 tests, Python compilation and whitespace validation.
  Dedicated fixtures cover disabled-by-default behavior, bounded evidence,
  contained failures, no-dispatch/no-alert guarantees, off-hours skipping and the
  single-service Starter Blueprint contract.
- GitHub Source QA run `36004870224` and automatic Production Smoke run
  `36005038976` both passed on source commit `4d3d628b1`.
- Production deployed the expected revision and reported the Shadow task enabled
  and running. Its first pre-market cycle returned `outside_regular_session`, zero
  quote checks, zero events and zero failures, proving the server-side session guard.
- The first regular-session cycle checked all five open positions in 260ms with no
  quote warnings or runtime failures. It detected one ORCL stop candidate from the
  completed 1m observation and still created no GitHub Actions run, notification or
  portfolio update, proving the production Shadow path has no execution side effect.
- The active legacy path independently dispatched monitor run `36006394672`, which
  persisted one ORCL `EXIT_STOP` and reduced the open portfolio from five positions
  to four. The next Shadow cycle immediately observed the four-position snapshot and
  zero events, with no duplicate dispatch or repeated portfolio action.
- An independent legacy UI-driven ORCL monitor dispatch occurred during production
  validation with source `agent-ui-live-price`. It was not emitted by the Shadow
  task and completed with no portfolio event. This path remains part of the parity
  study and must be retired or session-guarded only after the Shadow evidence is
  sufficient for cutover.

Remaining acceptance work:

- Collect several regular-session days of Shadow detections and compare them with
  current `/agent/monitor-live`, UI-trigger and persisted monitor outcomes.
- Prove no missed TP1, TP2 or stop event, no duplicate persisted action and stable
  provider/runtime behavior before enabling any Render-side dispatch.
- Keep GitHub Actions as the only portfolio mutation path and preserve the current
  external monitor as fallback until an explicitly approved cutover.

Durable parity evidence (2026-09-24):

- The Render Shadow task now keeps a bounded 100-event journal with a stable event
  identity, first/last observation time and detection count. Repeated one-minute
  observations of the same position threshold remain one event rather than creating
  duplicate records.
- The existing position-monitor workflow fetches the journal once per monitor run
  and persists compact daily JSONL evidence under `agent_results/monitor_parity/`.
  No workflow, schedule, Render instance, datastore or paid resource was added.
- A Shadow event is first classified `SHADOW_ONLY_PENDING`. It becomes `MATCHED`
  only when the active monitor produced a persisted notification outbox event for
  the same trade, ticker, action and threshold. An active event without matching
  Shadow evidence is `ACTIVE_ONLY`; an unmatched Shadow event expires after a
  configurable 30-minute observation horizon.
- Parity collection is fail-open for telemetry only: provider, endpoint or malformed
  payload failures emit a GitHub warning but can never block the active position
  monitor, persistence or Telegram delivery.
- The collector is read-only with respect to the workbook and trading lifecycle. It
  cannot dispatch workflows, send Telegram messages or mutate a paper position.

Parity acceptance window:

- Collect at least five regular trading sessions before any cutover.
- Require every persisted TP1, TP2 and stop event to be `MATCHED`, zero duplicate
  portfolio actions, zero Shadow side effects, stable Render CPU/RAM, and fewer than
  2% quote failures.
- Review `persistence_lag_seconds`; the cutover target is at most 90 seconds. During
  Shadow mode, the 15-minute active fallback can legitimately produce a longer lag
  and is measured rather than hidden.

Durable parity production QA (2026-09-24):

- Local regression QA passed 555 tests, Python compilation, workflow YAML parsing
  and whitespace validation. A FastAPI lifecycle check confirmed the task was
  enabled/running while `side_effects_enabled=false`.
- Source QA run `36010678662` passed. Production Smoke run `36010854812` passed
  against Render revision `8fd93423548b`, including the Agent HTML, 134 Decision
  JSONL records, the 22MB tracker, a read-only chart scan, position-monitor state
  and the new bounded Shadow journal contract.
- The first smoke run exposed a pre-existing dashboard synchronization defect: the
  scan snapshot could retain a monitor timestamp while omitting the independently
  stored `latest_status.json` fields. Render now synchronizes that heartbeat beside
  the snapshot and overlays the newer monitor status during snapshot enrichment.
  Regression coverage protects both the download and overlay behavior.
- Real position-monitor workflow run `36011085076` passed end to end. It evaluated
  four open paper positions with zero failures/events, ran the parity collector,
  produced no parity mutation because no Shadow event existed, and sent no Telegram
  notification. Production then reported `MONITOR_OK`, four positions checked,
  zero failed, zero events and a fresh heartbeat.
- Production Shadow reported regular-session polling, four positions checked,
  zero quote warnings, zero consecutive failures and no side effects. The event
  journal was empty at validation time, which is the correct no-touch baseline.
- A one-time follow-up review is scheduled for 2026-10-02 at 10:00 Asia/Jerusalem,
  after five complete trading sessions. It may recommend a cutover but cannot
  perform one or change trading behavior automatically.
