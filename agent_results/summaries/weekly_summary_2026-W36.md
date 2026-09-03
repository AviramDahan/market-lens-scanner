Weekly Performance Summary

Date: 2026-08-31
Total scans: 115
Tickers scanned: 17712
BUY_SIMULATED: 0
WATCH_READY: 140
WATCH_READY unique tickers: 20
WATCH_REVIEW: 3230
WATCH_REVIEW unique tickers: 132
WATCH_READY session split: regular=0, off_hours=140, unknown=0
WATCH_READY conversion: 0 converted / 20 staged
WATCH: 3371
SKIP: 14059
NO_TRADE: 10610
Realized PnL: -320.01
Unrealized PnL: 572.26
Portfolio value: 101357.27
Trade metric source: COMPLETED_TRADE_LIFECYCLE
Best ticker: APP
Worst ticker: ALB
Best shadow strategy: TREND_PULLBACK_RECLAIM
Worst shadow strategy: FIB_STOP_100_ATR
Shadow would-buy counts:
- BREAKOUT_CONTINUATION: 18
- FIB_STOP_075_ATR: 74
- FIB_STOP_100_ATR: 9
- FIB_STRUCTURE_STOP: 28
- RELATIVE_STRENGTH_LEADER: 90
- TREND_PULLBACK_RECLAIM: 83
- VWAP_RECLAIM: 40

Top rejected candidates:
- MP: WATCH score=0.58 reason=WATCH: Technical setup detected, but weighted risk/reward 1.49 is below minimum 2.00. Market regime NEUTRAL; sector STRONG; net R/R 1.12.
- GS: WATCH score=0.56 reason=QUALIFIED_CAPITAL_BLOCKED: All active entry-quality gates passed, but Position cannot be opened because Financials sector exposure cap leaves no executable size.
- LLY: WATCH score=0.56 reason=Gross R/R is valid, but Net R/R 1.77 failed minimum 2.00 after slippage/spread adjustment.
- KMI: WATCH score=0.56 reason=WATCH: Technical setup detected, but weighted risk/reward 1.45 is below minimum 2.00. Market regime BEAR; sector STRONG; net R/R 1.00.
- NVDA: WATCH score=0.55 reason=WATCH: Position cannot be opened because market regime exposure cap leaves no executable size.
- ETN: SKIP score=0.53 reason=SKIP: Industrials sector regime is weak (23/100); skip new entry. Market regime NEUTRAL; sector WEAK; net R/R 5.70.
- HAL: WATCH score=0.53 reason=WATCH: NEUTRAL market requires setup score (0.53 < 0.55).
- WMB: WATCH score=0.52 reason=WATCH: Technical setup detected, but weighted risk/reward 1.28 is below minimum 2.00. Market regime BULL; sector STRONG; net R/R 0.90.
- ECHO: WATCH score=0.51 reason=WATCH: Normalized quality score is too low for a new entry (34.44/100).
- CF: WATCH score=0.51 reason=WATCH: Entry confirmation failed - Breakout/retest confirmation requires completed close above trigger, held retest, and no falling candle.

Recommendations:
- Track WATCH_READY conversion during regular-session confirmation scans.
- After 2-3 full regular-session days, review whether entry confirmation is too restrictive.
- Review shadow would-buy candidates that active gates skipped before changing thresholds.