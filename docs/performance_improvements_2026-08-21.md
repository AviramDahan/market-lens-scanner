# Performance Improvements Baseline - 2026-08-21

This release improves measurement and scan coverage without changing the active
paper-trading strategy, entry gates, position sizing, risk limits, or monitor
behavior.

## Baseline

- Portfolio value: $101,582.45
- Total return: 1.582%
- Closed trades: 23
- Financial wins / losses: 10 / 13
- Win rate: 43.48%
- Profit factor: 1.792
- Average winner / loser: $275.26 / -$118.18
- Expectancy: $52.88 per closed trade
- Weekly WATCH_READY conversion sample: 1 / 81 unique candidates (1.23%)
- Weekly scan volume: 130 scans, 20,831 result records

These values are a comparison baseline, not a promise of future performance.

## Implemented

1. Period PnL is separated from cumulative portfolio realized PnL.
2. Best/worst setup and regime performance use realized PnL and R data instead
   of raw occurrence counts. Frequency remains available under an explicit name.
3. Summary data-completeness coverage exposes missing trade IDs, MFE, MAE, R,
   duration, and future outcomes.
4. Expected confirmation/target warnings are ignored for `No Trade` records.
5. Fresh Smart Universe scans rotate away from tickers already reviewed during
   the same day; open positions and carry-forward candidates remain outside the
   fresh quota and can still be rescanned.
6. `STOP_RECLAIM_REENTRY` runs in shadow mode only and preserves the active
   cooldown and final action.
7. Shadow candidates in a neutral sector receive a 50% sizing suggestion only;
   production sizing remains unchanged.
8. Shadow strategies are calibrated from deduplicated future scan-price
   observations instead of confidence alone.
9. Monitor exit rows preserve original-risk R calculations after a stop moves to
   entry and record position duration.

## One-Month Review

Compare the next month with this baseline. Review portfolio return, profit
factor, expectancy, drawdown, trade concentration, WATCH_READY conversion,
unique ticker coverage, repeated-scan ratio, shadow 1/3/5/10-day outcomes,
stop-reclaim signals, runtime, failures, and data completeness. Do not activate a
shadow strategy solely from a small or one-sector sample.
