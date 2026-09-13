# FIRST_MATCH_LEGACY shadow review - 2026-09-13

This review is measurement only. It does not change setup selection, scoring,
entry gates, `final_action`, position sizing, or portfolio behavior.

## Current retained sample

The active repository contains 41 decision files for the current retained
window, with 5,501 decision observations:

- 2,466 observations contain at least one setup candidate.
- 501 observations contain multiple matching setup candidates.
- 268 observations rank a different setup first under the shadow-normalized
  candidate score.
- Those 268 observations represent only 20 unique tickers and are heavily
  repeated by intraday rescans.
- Active outcomes for disagreements were 209 `SKIP` and 59 `WATCH`; none were
  `BUY_SIMULATED`.

The most common active/alternative disagreement was an active Swing Low setup
versus a shadow-ranked VWAP Reclaim or Liquidity Trap setup. This is evidence
that detector order materially affects classification, but it is not evidence
that the alternative would produce better trades.

## Why policy must not change yet

The weekly summary reports zero matured 1-session outcomes for every Shadow v2
strategy in the retained window and `best_shadow_strategy` is
`INSUFFICIENT_OUTCOMES`. Candidate-normalized score is also not the same measure
as the final professional setup score: an alternative candidate has not passed
the complete downstream confirmation, net R/R, earnings, exposure, correlation,
and capital pipeline under its own levels.

Raw observations cannot be treated as independent samples because the same
ticker and setup can appear in many scans on one day. Comparing the future
return of two setup labels on the same ticker and timestamp would also give both
labels the same underlying price return and would not validate their different
stops or targets.

## Promotion evidence required

Keep `FIRST_MATCH_LEGACY` active until a shadow-only replay can evaluate each
candidate through the complete downstream pipeline using:

- one deduplicated signal per ticker/setup/session;
- candidate-specific executable entry, stop, TP1 and TP2;
- chronological intraday bars for stop/target path and same-bar ambiguity;
- spread/slippage assumptions identical to the active pipeline;
- exact market and sector regime known at the signal timestamp;
- out-of-sample results by setup type and score bucket;
- enough matured trades to compare expectancy, drawdown and false positives,
  rather than candidate counts alone.

Until then, all candidate ranking remains shadow-only. More disagreements are
not a reason to select the alternative and more trades are not the objective.
