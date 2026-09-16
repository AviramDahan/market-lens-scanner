# Regime and entry-blocker audit — 2026-09-15

## Observed production evidence

The read-only `/agent/data` snapshot for run `20260915_170140` recorded BEAR
with risk points -4.25. Recomputing the existing rules from its persisted
benchmark values gives SPY -2, QQQ -2, IWM -1, VIX +1, US10Y -0.5,
and DXY +0.25. VIX was 17.56: the existing calm predicate accepts a value
below 20 even when the descriptive trend is mixed. Total: -4.25.

The BEAR threshold is <= -2. The zero exposure limit and disabled entries
therefore agree with the recorded inputs. This checks arithmetic, not the
timeliness or correctness of the provider's underlying prices.

Earlier snapshots contain 501–503 history rows and fixed EMA200 spans, but
no provider retrieval timestamp or last daily session label. Independent
yfinance retrieval from the audit environment was rate-limited (HTTP 429),
so current provider freshness could not be independently verified here.
The existing missing-benchmark fallback computes NEUTRAL with warnings;
it must not be interpreted as a validated market assessment. That fallback
and all trading thresholds remain unchanged in this observability patch.

## Changes

- Persist provider retrieval time on frames and retain it across cache hits.
- Record each benchmark's last daily bar/session, expected completed NYSE
  session, evaluation time, freshness status, and exact risk-point contribution.
- Respect weekends, holidays and early closes in session evidence. Daily bar
  labels are not live quote timestamps; non-equity benchmarks explicitly use
  NYSE as an informational reference. Unknown timestamps stay unknown.
- Derive dashboard rejection counts from conditions instead of generic words
  such as `net R/R` in descriptive text. Exclude held positions and avoid
  reporting unavailable No Trade confirmations as rejected entry signals.
- Prioritize the BEAR block, add a Market Blocked drilldown, and show the
  market policy separately from its internal score/RR sentinel values.
- Rebuild older snapshot diagnostics on Render using the new display schema.

No entry/exit thresholds, market labels, position sizing, portfolio files,
live trades, notification destinations, or strategy selection were changed.

## Verification

- Full regression suite: 442 passed, with existing deprecation warnings.
- `git diff --check`: passed.
- New tests cover the recorded -4.25 calculation, cache timestamp preservation,
  missing/duplicate/future timestamps, current-session bars, weekend/holiday
  boundaries, early closes, BEAR priority, genuine score/RR failures, and
  legacy snapshot regeneration without portfolio changes.
- Local browser preview was unavailable, including the synthetic-data preview.
  Desktop/mobile visual verification remains pending; no visual pass is claimed.
- Production needs a normal post-deployment scanner run to populate the newly
  added evidence. Old snapshots must continue to say `Not recorded`.

Sources: [production snapshot](https://market-lens-scanner-fb63.onrender.com/agent/data),
[successful scanner run](https://github.com/AviramDahan/market-lens-scanner/actions/runs/34998504517),
[prior partial-scan fix](https://github.com/AviramDahan/market-lens-scanner/commit/8b7ce7b470ce31967a3dd6d26a5e53e537c2df62).
