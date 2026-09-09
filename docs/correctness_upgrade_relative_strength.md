# Relative Strength Correctness

## Active calculation

`compute_relative_strength` now uses:

`1 + (stock_return - benchmark_return) / abs(benchmark_return)`

This is benchmark-move-normalized excess return, not a wealth ratio or an
empirically calibrated probability. Parity is 1. On a positive benchmark it
matches the old return ratio when calculated over the same observations.
On a negative benchmark it fixes the reversed ranking. For example, against
-5%, a stock returning -2% scores 1.6 and a stock returning -10% scores 0.

The existing setup-score bonus/penalty thresholds of 1.3 and 0.7 are unchanged.
Their empirical predictive value has not been established by this repair.
The normalization remains sensitive to small nonzero benchmark moves; later
calibration should assess this rather than silently introduce a new scale.

## Time alignment and fallback

Both returns now cover the same n intervals using n+1 stock closes and the
benchmark returns on the matching end dates. The old stock leg covered n-1
intervals while the benchmark could cover n. Missing intervals, invalid prices,
duplicate indices, and a numerically flat benchmark return neutral 1. This is a
neutral score contribution, not a claim of valid or equal-quality source data.
Future benchmark observations are not included in the selected stock window.

## Verification

Tests cover ranking/parity in both market directions, positive-ratio
compatibility, flat/missing benchmark data, exact interval count, first-interval
alignment, and exclusion of later benchmark observations. The original strict
xfail regression is now a passing regression test. Full suite: 291 passed.

No Universe, capital configuration, entry-confirmation gate, target policy, or
monitor behavior changed in this package. Scoring can change for affected
windows by design; this is not evidence of improved realized performance.
