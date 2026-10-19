# Risk/data consistency release candidate

## Scope and current status

Approved follow-up to the October 19 read-only research. Work is isolated on
`codex/risk-data-consistency-20261019`, based on deployed source `91648c4e0`.
This document describes a release candidate, not a production deployment.
No portfolio, historical workbook, Decision JSONL, trade, or Telegram receipt
has been rewritten. No real Telegram notification or synthetic production trade
is part of QA. Fibonacci and trading Shadow remain retired.

## Implemented items

| Item | Implementation | Verification |
| --- | --- | --- |
| Relative strength | Professional scoring uses the scanner's aligned 20-return-interval, sign-safe excess-return contract. A zero strength stays in the benchmark average rather than disappearing. | Falling/positive benchmarks, parity, missing intervals, future provider rows. |
| Correlation evidence | Explicit NOT_REQUIRED/PASS/HIGH/PARTIAL/UNKNOWN, expected/checked counts, missing tickers and warnings. Configured correlation threshold is passed through. | Missing/short/constant histories, partial coverage with a known high correlation, custom threshold, no peers. |
| Factor exposure | Preserve frozen factor tags and cap bucket from position metadata; legacy positions retain the sector/ticker fallback. | Dict/JSON metadata, small-cap/theme preservation, no double counting and self exclusion. |
| VIX | A stressed trend or extreme level cannot simultaneously be calm. Existing levels, point weights and regime thresholds are unchanged. | Stress at 19.99, contradictory calm at 25, mixed and missing states, regime contribution. |
| VWAP | Scanner and entry confirmation use the same New York session-reset OHLCV definition. Confirmation only supplies completed regular-session intraday bars. Daily candles cannot substitute for intraday VWAP. | Cross-session reset, unfinished/future candle exclusion, invalid/zero-volume input, daily fallback blocked for VWAP only. |
| Processing time | Read one compact workbook projection for all discovery queues. Reuse market snapshots only within one ticker's candidate-selection pass in both user and Agent paths. | Exact queue parity, one workbook open, immutable input file, cached/uncached decision equality, cash limits recalculated. |
| Measurement | Stable secret-free policy fingerprint, immutable per-policy manifest, entry-policy attribution on completed lifecycles, policy/setup/score calibration groups, explicit drawdown/fill assumptions. | Fingerprint determinism, changes in config, missing legacy provenance, full-lifecycle attribution, no synthetic complete trades from exit-event fallback. |
| UI evidence | UNKNOWN is not a green correlation check in the WATCH_READY modal, entry checklist or Latest Actions. Static JS cache version refreshed. | Independent browser interaction on desktop and 390x844 mobile; 11 deterministic JS regressions. |

The UNKNOWN correlation policy remains **warn-only**, matching existing entry
behavior. This release does not silently add another entry gate. Missing
coverage is now visible and cannot be called a passed measurement. A known
high correlation still blocks through the existing rule. A stricter UNKNOWN
entry policy requires a separate explicit decision.

RS, VIX and VWAP corrections can change decisions for the same prices even
though no numeric trading threshold is lowered or changed. They require a new
measurement cohort and deployment QA, not a claim of better profitability.
Both VWAPs are OHLCV approximations: hourly scanner and completed 30-minute
confirmation bars can differ numerically because of resolution/cutoff. Shared
session semantics do not imply tick-level equality or a live-candle trigger.

## Measurements and QA evidence

- Initial baseline: 830 tests passed.
- Release-candidate full regression after independent QA: 867 tests passed.
  The independent review found and verified fixes for stored Unknown-sector
  fallback, unknown-correlation replay reduction and effective-policy inputs.
- Frozen workbook: SHA-256
  `306c9294ce724a65d15d9f884dde47afff9ddbaea175420cfb9ac7cdf4b8d2fb`.
- Follow-up selection benchmark on that same read-only workbook:
  old repeated reads 45.563 seconds; one-pass projection 14.356 seconds.
  Queue contents and ordering matched exactly. SHA-256 was unchanged.
  This saves 31.207 seconds in that phase; it is not a whole-scan speed promise.
- Snapshot caching does not reuse final actions, sizing, correlations, current
  cash, exposure, heat or stop cooldown across portfolio decisions.
- Manual local UI: open/scroll/close WATCH_READY checklist, inspect UNKNOWN,
  expand Latest Actions and verify Not verified rather than Corr OK. No console
  errors; no horizontal overflow at 390x844. Fixture is explicitly isolated,
  has no writable tracker and rejects non-read-only HTTP methods.
- Policy calibration is read-only. No denominator, score weights, RR weights,
  buy-zone/target/stop formulas, capital limits or execution rules are optimized.
- Existing TP1 partial, stop-to-entry, TP2, stop-first, cash, identity, persistence
  and notification regression tests remain mandatory in the full suite.

## Execution and historical measurement limitations

No change to `agent/position_monitor.py` is included. The existing model closes
the full remaining quantity at TP2 when both targets first touch in one candle,
uses stop-first when an old stop and target share a candle, and does not infer
the newly advanced breakeven stop's order inside the TP1 candle. These are now
explicit measurement assumptions, not verified market fills.

The fingerprint includes the effective API/workbook minimum R/R, preliminary
trade-risk ceiling and currency rate, not just AgentRiskConfig. Agent JSONL
records whether its manifest was verified locally; missing/failed manifests
are counted explicitly in summaries. A manifest write failure is a reported
measurement error, not permission to drop or fabricate portfolio results.

`max_drawdown` remains realized closed-lifecycle currency drawdown, not daily
marked equity drawdown. Missing lifecycle data yields unavailable new policy
calibration, not zero performance. Legacy records remain explicitly unversioned
and are not retroactively assigned today's policy fingerprint. No historical
return percentage is claimed to establish the new policy's edge.

## Storage: verified prerequisite, cutover still separate

PR #46 prepares a verified host recovery mirror. Its source CI succeeded.
The separate archive suite was rerun locally: 63 passed, 2 platform skips.
An existing host receipt records a remote restored generation verified on
2026-10-16, with 2,985 files and 2,207,554,429 bytes, all hashes and workbook
validated, in `AviramDahan/market-lens-data-archive`.
This is an older recovery proof, not a new checkpoint of today's portfolio.
Archive repository/token configuration is present; credentials were not printed.

Do not merge the old archive implementation into this trading-data correction
without a separate review. It is a mirror prerequisite, not the final quota fix.
The live Git checkout/backup-before-notification contract remains unchanged.
No images, structured results, Git history, backup refs or failed-run evidence
are deleted by this release. Source QA does not prove safe storage cutover.

Next storage release gates, in order:

1. Fresh remote generation and isolated restore including current tracker,
   pending outboxes and receipts, with financial and identity parity.
2. Bounded space/runtime proof for incremental publication, retries and
   concurrent monitor activity on the shared server.
3. Per-event durable backup checkpoint before Telegram, replacing generated
   Git pushes only after failure/restart tests pass.
4. Stop new generated Git blobs forward-only, preserving all legacy refs and
   remote generations. Never restore an old tracker over newer trades.
5. Reclaim only redundant temporary copies after verified recovery and tested
   rollback. Preserve the immutable historic source data; do not touch AI Trader.

## Release checklist

Before deployment: exact diff review, full pytest/JS suite, independent QA,
CI success, consistent portfolio backup, source rollback image reference and
explicit merge/deploy approval. Deploy matching web and worker revisions.

After deployment: verify image labels and `/health`, `/agent`, `/agent/data`;
observe one bounded normal scanner run for coverage, JSON/Excel persistence,
policy manifest/fingerprint, retired strategy flags and chart retention.
Observe a regular-session monitor heartbeat and persisted outcome as available.
No open positions or no natural TP/SL event means that live execution route is
NOT_OBSERVED, not proved by HTTP 200 or isolated scenario tests.
