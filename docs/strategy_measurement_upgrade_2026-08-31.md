# Strategy Measurement Upgrade - 2026-08-31

This release improves strategy measurement and shadow experimentation. It does
not change active `BUY_SIMULATED` decisions, active entry gates, position
sizing, portfolio exposure limits, Smart Universe ranking, or position-monitor
execution rules. Market Lens remains paper trading only.

## Why This Upgrade Exists

The historical sample was useful, but several measurements mixed exit events
with complete trades, old entry metadata could be replaced by a later HOLD
observation, and `WATCH_READY` included candidates that still had material
blockers. Those issues made strategy conclusions less reliable than the raw
headline numbers suggested.

The upgrade creates a cleaner forward-test baseline before any active strategy
change is considered.

## Implemented Measurement Changes

1. A complete trade is now reconstructed from its original
   `BUY_SIMULATED` row through partial exits and final exit.
2. Win rate, profit factor, average R, drawdown, and setup/regime performance
   prefer complete-trade lifecycle results instead of counting every exit event
   as a separate trade.
3. The original `trade_id`, setup, score bucket, regime, R/R, and confirmation
   metadata remain attached to the position. Later HOLD scans are stored under
   `latest_position_observation` instead of replacing entry context.
4. MFE and MAE are tracked from intraday monitor bars. Missing historical
   fields are backfilled incrementally from daily bars and marked
   `daily_bar_approximation`.
5. Closed trades receive 1, 3, 5, and 10 trading-day post-exit outcomes when
   enough later daily data exists. This is intended to show whether stops and
   exits were too early; it does not alter past decisions.
6. Historical backfill is capped by
   `MARKET_LENS_TRADE_OUTCOME_BACKFILL_LIMIT` per New York day. The default is 8
   trades per New York trading day. An incomplete trade is checked at most once
   per day, which avoids repeating the same 1/3/5/10-day data request on every
   intraday scan.

## WATCH Status Semantics

- `WATCH_READY`: the candidate passed the material gates and is staged for a
  specific remaining confirmation, commonly the next regular session.
- `WATCH_REVIEW`: the candidate is still interesting but has one or more
  material quality, R/R, confirmation, or risk blockers.
- `SKIP`: the candidate has a hard blocker and is not counted as ready.

Historical files without `watch_status` still use the legacy fallback parser.
New decisions record the explicit status so future conversion metrics are not
inflated by ordinary WATCH records.

## Setup Detection Measurement

All matching setup detectors are now recorded in `setup_candidates`, while the
active setup remains the unchanged first match from the legacy detector order.
The decision also records:

- `active_setup_selection_policy: FIRST_MATCH_LEGACY`
- legacy setup score
- per-setup shadow-normalized score
- percentile within the same setup type in the current run

This lets future analysis test whether another matching setup or a normalized
score predicts outcomes better. It does not change the active setup or action.

## Shadow Strategy Version 2

Every shadow record uses `shadow_v2`. A shadow `would_buy=true` is logging only
and cannot change `final_action`.

The existing shadow strategies now require a regular market session and a fresh
completed confirmation candle from the same session:

- `BREAKOUT_CONTINUATION`
- `TREND_PULLBACK_RECLAIM`
- `VWAP_RECLAIM`
- `RELATIVE_STRENGTH_LEADER`
- `STOP_RECLAIM_REENTRY`

`RELATIVE_STRENGTH_LEADER` additionally requires a STRONG sector, both quality
and momentum scores of at least 65, setup score of at least 0.45, primary net
R/R of at least 1.00, and weighted net R/R above the active regime threshold.

`STOP_RECLAIM_REENTRY` remains shadow-only, preserves the active cooldown,
requires a fresh completed reclaim in a STRONG sector, uses a configurable
minimum score (default 0.50), and proposes half size with at most one re-entry
per stopped trade.

## Stop-Distance Experiments

Three Fib stop experiments are recorded only for comparison:

- `FIB_STOP_075_ATR`
- `FIB_STOP_100_ATR`
- `FIB_STRUCTURE_STOP`

Each record recalculates Target 1 R/R, Target 2 R/R, weighted R/R, and an
equal-risk position-size multiplier. These experiments do not move an active
stop and do not open a trade.

## Confirmation Freshness

Decisions now record:

- `confirmation_freshness_status`
- `confirmation_age_minutes`
- `confirmation_same_session`
- `confirmation_freshness_reason`
- `confirmation_freshness_shadow_only: true`

Freshness currently affects Shadow v2 only. It is deliberately not an active
BUY blocker in this release so the production baseline is not changed without
forward evidence.

## Review Rules

Do not promote a shadow strategy or alter an active threshold from one good
day. Reviews should compare at least:

- complete-trade count, money win rate, profit factor, average R, and drawdown
- data completeness for trade IDs, MFE, MAE, R, and future outcomes
- `WATCH_READY` reviewed conversion, not raw record count
- results by setup, setup score bucket, market regime, sector regime, and sector
- shadow outcomes by both strategy name and `strategy@version`
- stale versus fresh confirmation outcomes
- Fib stop variants using equal-risk sizing

Any future active change requires a separate explicit approval.
