# Future Improvements Backlog

This backlog is documentation only.

Do not implement these items now. Do not change active trading logic, `BUY_SIMULATED`, `final_action`, entry gates, thresholds, monitor logic, Smart Universe, Excel, Dashboard, broker integration, or real orders based on this file.

The purpose of this document is to keep future ideas organized until enough paper-trading data is collected.

## 1. Run Summary Per Agent Run

**Improvement**  
Create a dedicated summary for each individual run:

```text
agent_results/summaries/run_summary_YYYYMMDD_HHMMSS.json
agent_results/summaries/run_summary_YYYYMMDD_HHMMSS.md
```

**Why It Matters**  
The system already has daily and weekly summaries. A per-run summary would make it easier to inspect exactly what happened during one scan without mixing it with the rest of the day.

**Problem It Solves**  
Daily summaries aggregate multiple scans. When several scans run in one day, it can be hard to isolate:

- Which tickers were scanned in one specific run.
- Which candidates appeared in that run.
- Which warnings were created in that run.
- Whether one run was slower, weaker, or cleaner than another.

**Risk**  
Low. This should be a read-only reporting addition. Main risk is repository noise if too many summary files are created.

**Data Needed Before Implementation**  

- Number of scans per day.
- Average size of current daily summaries.
- Whether individual run inspection is actually needed after Monday's monitoring.

**Priority**  
Medium.

**Timing**  
Suitable after at least one week of paper trading.

## 2. monitor-live Based On Latest 1m Candle High/Low

**Improvement**  
Improve `/agent/monitor-live` so it checks the latest one-minute candle high/low, not only latest price or last close.

Future logic:

```text
if latest_1m_low <= stop_loss:
    trigger monitor

if latest_1m_high >= target_1:
    trigger monitor

if latest_1m_high >= target_2:
    trigger monitor
```

**Why It Matters**  
Targets or stops can be touched intraminute and then price can move away. Checking only the last price can miss a TP/SL touch.

**Problem It Solves**  
Reduces missed target or stop events when price briefly touches a level.

**Risk**  
Medium. If data quality is poor or delayed, high/low checks can trigger false events. Needs conservative handling for incomplete live candles.

**Data Needed Before Implementation**  

- Frequency of open positions.
- Whether current monitor missed any TP/SL events.
- Reliability of 1m high/low data from the selected provider.
- Comparison between latest price and candle high/low during market hours.

**Priority**  
High.

**Timing**  
Suitable after at least one week of paper trading, unless a clear missed TP/SL event is observed earlier.

## 3. Shadow Strategies Analysis After One Week

**Improvement**  
Analyze Shadow Strategy results after enough data is collected.

Metrics:

- `would_buy` count per strategy.
- Which shadow signals later worked.
- False positives.
- Missed opportunities.
- Best-performing shadow strategy.

**Why It Matters**  
Shadow strategies are intentionally inactive. Their value depends on measured outcomes, not assumptions.

**Problem It Solves**  
Prevents activating a new strategy based on theory or one isolated example.

**Risk**  
Low if analysis remains read-only. High if conclusions are drawn from too little data.

**Data Needed Before Implementation**  

- At least several trading days of shadow signals.
- Later price outcomes for each signal.
- Active agent decision for comparison.
- Setup type, market regime, sector regime, and R/R for each signal.

**Priority**  
High.

**Timing**  
After at least one full week of paper trading.

## 4. Relative Strength Leader Calibration

**Improvement**  
Evaluate whether `RELATIVE_STRENGTH_LEADER` is too loose or too strict.

Questions to check:

- How many candidates does it generate?
- Do candidates continue higher after signal?
- Does it identify strong stocks early?
- Does it skip important entry confirmation?
- Does it over-select extended stocks?

**Why It Matters**  
Relative strength can be useful, but it can also chase overextended names if not calibrated.

**Problem It Solves**  
Helps decide whether the strategy needs additional gates such as distance from EMA20, ATR extension, or completed candle confirmation.

**Risk**  
Medium. Overfitting to one week of strong or weak market conditions can produce misleading thresholds.

**Data Needed Before Implementation**  

- Number of `RELATIVE_STRENGTH_LEADER` `would_buy` signals.
- Subsequent 1d, 3d, 5d, and 10d outcomes.
- MFE/MAE after signal.
- Market and sector regime during each signal.

**Priority**  
High.

**Timing**  
After at least one full week of paper trading; preferably after two or more weeks.

## 5. WATCH_READY Conversion Analysis

**Improvement**  
Measure whether `WATCH_READY` setups convert into useful entries.

Metrics:

- Count of `WATCH_READY`.
- Count that later become `BUY_SIMULATED`.
- Count that fail.
- Count that continue without entry.
- Count that become false setups.

**Why It Matters**  
`WATCH_READY` is meant to stage candidates before confirmation. It should lead to better timing, not just a larger watchlist.

**Problem It Solves**  
Identifies whether staging logic is useful or too noisy.

**Risk**  
Low for reporting. Medium if used too early to loosen gates.

**Data Needed Before Implementation**  

- Multiple scan cycles per day.
- WATCH_READY ticker history.
- Later active action.
- Price movement after WATCH_READY.

**Priority**  
High.

**Timing**  
After one week of paper trading.

## 6. Setup Score Bucket Performance

**Improvement**  
Analyze performance by setup score buckets:

```text
<0.40
0.40-0.49
0.50-0.59
0.60-0.69
0.70+
```

**Why It Matters**  
The setup score should predict quality. If higher score buckets do not perform better, scoring weights need review.

**Problem It Solves**  
Validates whether `setup_score` is actually useful or just descriptive.

**Risk**  
Medium. Needs enough samples per bucket. Low sample size can produce false conclusions.

**Data Needed Before Implementation**  

- Closed trades.
- Shadow outcomes.
- WATCH/WATCH_READY outcomes.
- R multiple by bucket.
- Win rate by bucket.

**Priority**  
High.

**Timing**  
After at least one week of paper trading; stronger after several weeks.

## 7. MFE / MAE For Every Position

**Improvement**  
Track:

- MFE: Maximum Favorable Excursion.
- MAE: Maximum Adverse Excursion.

**Why It Matters**  
MFE/MAE helps evaluate whether stop and target placement is reasonable.

**Problem It Solves**  
Answers:

- Are stops too tight?
- Are stops too wide?
- Is TP1 too close?
- Is TP2 too far?
- Do trades move in favor before stopping out?

**Risk**  
Medium. Requires reliable intraday data and careful handling of partial exits.

**Data Needed Before Implementation**  

- Open position intraday high/low history.
- Entry price.
- Stop and target levels.
- Exit timestamps.
- Partial profit events.

**Priority**  
High.

**Timing**  
After one or more weeks of paper trading, or earlier if monitor accuracy is being reviewed.

## 8. Trailing Stop After TP1

**Improvement**  
Consider adding trailing stop logic after TP1 is reached.

Possible future rules:

```text
stop = max(entry, EMA20 - 0.5 ATR)
stop = max(entry, latest swing low - 0.3 ATR)
Chandelier-style trailing stop
```

**Why It Matters**  
After TP1, the system may need a better way to protect gains while allowing winners to continue.

**Problem It Solves**  
Avoids giving back too much profit after TP1, while not exiting too early.

**Risk**  
High. Trailing stops can reduce large winners if too tight, or fail to protect gains if too loose.

**Data Needed Before Implementation**  

- Enough closed positions.
- TP1 hit history.
- Price behavior after TP1.
- MFE/MAE after TP1.
- Comparison of entry stop, breakeven stop, and trailing alternatives.

**Priority**  
Medium.

**Timing**  
Only after enough closed paper positions exist.

## 9. Market Breadth Layer

**Improvement**  
Add a market breadth layer.

Possible metrics:

- Percent of stocks above EMA20.
- Percent of stocks above EMA50.
- Percent of stocks above EMA200.
- Advance/decline approximation.
- Sector participation.

**Why It Matters**  
SPY/QQQ/IWM/VIX can hide weak breadth. A market can look strong because a few mega-cap stocks are carrying the index.

**Problem It Solves**  
Improves market regime quality by checking whether strength is broad or narrow.

**Risk**  
Medium. Breadth data can be expensive or noisy. Poor implementation could slow scans.

**Data Needed Before Implementation**  

- Reliable breadth universe.
- Computation time impact.
- Comparison between current Market Regime and breadth-adjusted regime.
- Outcome by breadth condition.

**Priority**  
Medium.

**Timing**  
After at least one week of paper trading; preferably after baseline Market Regime behavior is reviewed.

## 10. Dashboard Visibility

**Improvement**  
After summaries are stable, expose analytics in `/agent`.

Possible UI sections:

- Daily Summary.
- Weekly Summary.
- Best shadow strategy.
- Top rejection reasons.
- WATCH_READY conversion.
- PnL summary.
- Risk summary.

**Why It Matters**  
JSON/MD files are useful for audit, but dashboard visibility makes the system easier to monitor.

**Problem It Solves**  
Reduces manual file inspection and makes daily review faster.

**Risk**  
Medium. UI changes can create layout issues, especially on mobile.

**Data Needed Before Implementation**  

- Stable JSON summary schema.
- Which summary fields are most useful.
- Mobile layout priorities.
- Dashboard loading performance.

**Priority**  
Medium.

**Timing**  
Only after JSON/MD summaries are stable.

## 11. Strategy Activation Rules

**Improvement**  
Define minimum requirements before any shadow strategy can influence real paper-trading decisions.

Example requirements:

- At least 50 shadow signals.
- At least 20 closed paper trades or simulated outcomes.
- Positive average R.
- Acceptable false positive rate.
- Does not increase drawdown.
- Works on more than one trading day.
- Works across more than one sector.

**Why It Matters**  
Prevents activating a strategy based on one good day.

**Problem It Solves**  
Creates a controlled promotion process from shadow logging to active paper-trading logic.

**Risk**  
Low as documentation. High if activation rules are ignored.

**Data Needed Before Implementation**  

- Shadow signals.
- Outcomes.
- False positive rate.
- Drawdown impact.
- Sector distribution.
- Market regime distribution.

**Priority**  
High.

**Timing**  
After more than one week of paper trading; preferably after several weeks.

## 12. Backtest / Walk-Forward Validation

**Improvement**  
Add historical testing or an adapter to a backtest engine such as LEAN.

Required considerations:

- Slippage.
- Spread.
- Commissions.
- Out-of-sample testing.
- Survivorship bias.
- Walk-forward validation.

**Why It Matters**  
Paper trading is useful, but historical validation can expose whether rules work across different environments.

**Problem It Solves**  
Reduces reliance on live forward testing alone.

**Risk**  
High. Backtests can be misleading if data quality, slippage, survivorship bias, or execution assumptions are wrong.

**Data Needed Before Implementation**  

- Historical OHLCV data.
- Corporate action handling.
- Universe history.
- Spread/slippage assumptions.
- Exact strategy rules frozen by version.

**Priority**  
Medium.

**Timing**  
After paper-trading baseline is stable.

## 13. Performance By Regime

**Improvement**  
Analyze performance by:

- Market regime.
- Sector regime.
- Volatility regime.
- Strong/weak sector.
- Regular session only.

**Why It Matters**  
A strategy may work in Bull markets and fail in Neutral or Bear conditions.

**Problem It Solves**  
Identifies when the system should be active, defensive, or fully inactive.

**Risk**  
Low for reporting. Medium if used too early to adjust thresholds.

**Data Needed Before Implementation**  

- Trades and shadow outcomes across different regimes.
- Market regime at decision time.
- Sector regime at decision time.
- Volatility context.
- Session phase.

**Priority**  
High.

**Timing**  
After at least one week; stronger after several weeks.

## 14. Recommendations Engine

**Improvement**  
Allow weekly summaries to generate read-only recommendations.

Examples:

- A gate may be too strict.
- A shadow strategy may be promising.
- A sector may be problematic.
- Target may be too far.
- Stop may be too wide.

**Why It Matters**  
Recommendations can focus review time and highlight repeated patterns.

**Problem It Solves**  
Turns raw metrics into review prompts.

**Risk**  
Medium. Recommendations can be overconfident if data is limited.

**Data Needed Before Implementation**  

- Multiple days of summaries.
- Shadow strategy outcomes.
- Closed trade outcomes.
- Rejection reasons.
- MFE/MAE.
- Market/sector regime context.

**Priority**  
Medium.

**Timing**  
After at least one week of paper trading.

**Important Constraint**  
Recommendations must remain read-only. They must not automatically change code, thresholds, gates, Smart Universe, monitor behavior, or active trade decisions.
