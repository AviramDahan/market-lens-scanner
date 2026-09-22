Weekly Performance Summary

Date: 2026-09-14
Total scans: 155
Tickers scanned: 20974
BUY_SIMULATED: 2
WATCH_READY: 0
WATCH_READY unique tickers: 0
WATCH_REVIEW: 2677
WATCH_REVIEW unique tickers: 84
WATCH_READY session split: regular=0, off_hours=0, unknown=0
WATCH_READY conversion: 0 converted / 0 staged
WATCH: 2677
SKIP: 17594
NO_TRADE: 12352
Realized PnL: -14.56
Unrealized PnL: 796.32
Portfolio value: 101088.17
Daily recorded-equity change (%): None
Daily return status: None
Daily reference date: None
Cumulative recorded-equity change (%): 1.0882
Trade metric source: COMPLETED_TRADE_LIFECYCLE
Best ticker: APP
Worst ticker: AR
Best shadow strategy: VWAP_RECLAIM
Worst shadow strategy: FIB_STOP_100_ATR
Shadow would-buy counts:
- BREAKOUT_CONTINUATION: 15
- FIB_STOP_075_ATR: 36
- FIB_STOP_100_ATR: 13
- FIB_STRUCTURE_STOP: 37
- RELATIVE_STRENGTH_LEADER: 6
- TREND_PULLBACK_RECLAIM: 56
- VWAP_RECLAIM: 4

Top rejected candidates:
- JNJ: WATCH score=0.5667 reason=Gross R/R is valid, but Net R/R 1.99 failed minimum 2.50 after slippage/spread adjustment.
- GILD: SKIP score=0.56 reason=SKIP: WATCH: Entry confirmation failed - Support/Fib setup requires completed close above the buy zone or a strong bullish reclaim from the zone; weak or falling candles are blocked. Also: Position size blocked by cash, exposure, or risk limits.
- KMI: WATCH score=0.5574 reason=WATCH: Technical setup detected, but weighted risk/reward 1.45 is below minimum 2.00. Market regime NEUTRAL; sector STRONG; net R/R 0.99.
- DDOG: WATCH score=0.5551 reason=WATCH: Entry confirmation failed - Support/Fib setup requires completed close above the buy zone or a strong bullish reclaim from the zone; weak or falling candles are blocked.
- EQT: SKIP score=0.55 reason=SKIP: SKIP: Bear market regime blocks new simulated buys. Also: Position size blocked by cash, exposure, or risk limits.
- AIG: SKIP score=0.54 reason=SKIP: WATCH_READY: Setup is staged outside regular market hours; re-scan after the regular session opens for entry confirmation. Also: Position size blocked by cash, exposure, or risk limits.
- TSLA: SKIP score=0.5325 reason=SKIP: Consumer sector regime is weak (19/100); skip new entry. Market regime BEAR; sector WEAK; net R/R 1.69.
- HAL: WATCH score=0.5321 reason=WATCH: Entry confirmation timing invalid: Confirmation uses a previous regular session; wait for a new completed confirmation.
- MSFT: WATCH score=0.5309 reason=WATCH: Technical setup detected, but weighted risk/reward 1.78 is below minimum 2.00. Market regime NEUTRAL; sector STRONG; net R/R 1.41.
- ETN: SKIP score=0.529 reason=SKIP: Industrials sector regime is weak (16/100); skip new entry. Market regime NEUTRAL; sector WEAK; net R/R 1.63.

Recommendations:
- Review shadow would-buy candidates that active gates skipped before changing thresholds.

## Historical rebuild

- Decision coverage: 155 verified runs, 20974 records from the Git recovery archive.
- SKIP: 17594; WATCH: 2677; No Trade setups: 12352.
- Opened: 2; fully closed: 3; TP1 partial exits: 1; stop exits: 3.
- Weekly realized PnL: $-14.56; recorded equity change: $-298.49.
- Closed-trade money win rate: 0.00% (3 fully closed trades).
- Opening workbook: `f2a3e73c520d17ec1a9f949f77a9ebbdecd0975d`; closing workbook: `ea81994beacde7c8c9c110840a61a51498647b5e`.
- Recovered decision data: https://github.com/AviramDahan/market-lens-data-archive/releases/tag/measurement-history-recovery-2026-09-22

Runtime/retry details and unrecorded future outcomes are unavailable. The equity change compares recorded snapshots outside regular session.
