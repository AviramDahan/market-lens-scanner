# Capital Management Replay - 2026-09-01

## Safety Boundary

This work is measurement only. It does not change the active scanner, entry
gates, `BUY_SIMULATED`, position monitor, workbook, dashboard, or portfolio.
The replay reads the existing workbook and decision archive and writes separate
JSON and Markdown reports.

## Command

```bash
python agent/capital_replay.py
```

Optional arguments:

```bash
python agent/capital_replay.py \
  --starting-capital 100000 \
  --idle-cash-yield 0.05 \
  --workbook agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx \
  --decision-dir agent_results/decisions \
  --output-dir agent_results/capital_replay
```

Use `--skip-candidate-analysis` for a fast workbook-only replay.

## Method

The replay has two deliberately separate layers.

1. Exact completed-trade sizing replay

   It reconstructs every completed trade from `Trade Log`, preserves the
   recorded entry, partial exit, final exit, and timestamps, and changes only
   the simulated quantity under each capital scenario. This can answer how the
   same 28 completed trades would have behaved with different sizing rules.

2. Capital-blocked opportunity proxy

   It streams all decision JSONL files, keeps only candidates that passed the
   recorded confirmation and current-style score/RR/target gates, deduplicates
   by ticker/setup/day, and measures later recorded scan prices. This is a
   directional diagnostic, not an executable backtest, because scan prices do
   not prove intraday TP/SL order or fill quality.

## Scenarios

- `ACTUAL_BASELINE`: original quantities, used to verify exact reconstruction.
- `CONSERVATIVE_DYNAMIC`: 30% neutral / 50% bull exposure, 0.40%-0.60% trade
  risk and 2% total portfolio heat.
- `BALANCED_DYNAMIC`: continuous regime exposure up to 60%, 0.50%-0.75% trade
  risk, 2.5% portfolio heat, 10% position cap, sector and factor risk caps.
- `GROWTH_DIAGNOSTIC`: diagnostic-only 70% ceiling and higher risk limits. It
  is not a recommendation for activation.

## Results

Sample: 28 completed trades from 2026-06-22 through 2026-08-31, starting with
$100,000. The cash-yield comparison assumes 5% simple annual yield on idle cash.

| Scenario | Trading PnL | Trading Return | Return With Cash Yield | Avg Exposure | Max Exposure | Profit Factor | Realized Drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| Actual baseline | $996.51 | 1.00% | 1.84% | 11.66% | 36.86% | 1.49 | -$429.47 |
| Conservative dynamic | $1,278.97 | 1.28% | 2.10% | 14.49% | 49.90% | 1.54 | -$735.44 |
| Balanced dynamic | $1,428.89 | 1.43% | 2.24% | 15.68% | 57.82% | 1.57 | -$764.72 |
| Growth diagnostic | $1,478.58 | 1.48% | 2.28% | 16.16% | 59.11% | 1.57 | -$819.92 |

All scenarios kept the same 12 winners and 16 losers. The difference comes
only from sizing. Balanced dynamic added $432.38 of trading PnL versus the
closed-trade baseline, while increasing realized drawdown by $335.25.

## Capital-Blocked Candidates

The archive contains 168,895 decision records and 19,845 repeated pre-risk
`initial_action=BUY_SIMULATED` records. Those are not 19,845 independent trade
opportunities. After current-style gates, regular-session requirements, and
daily ticker/setup deduplication, only two candidates were primarily blocked by
sector/factor capital constraints and had enough later price data.

- Five recorded scan days: average +1.81%, median +1.81%, 100% positive, n=2.
- Ten recorded scan days: average +1.53%, median +1.53%, 50% positive, n=2.

This sample is far too small to justify loosening exposure caps. It does show
why raw `initial_action` counts must not be used to claim missed trades.

## Interpretation

1. Capital sizing is conservative, but capital is not the only bottleneck.
   Current-quality signals blocked only by capital are rare in this archive.
2. Balanced sizing improved return and profit factor on the same trades, but it
   also increased drawdown. The gain is real within the replay and still too
   small a sample for production activation.
3. The 10% per-ticker cap was the most common sizing constraint in the balanced
   scenario. Raising the total exposure cap alone would not dramatically raise
   average utilization without more concurrent valid setups.
4. Applying a simulated cash yield to idle capital materially improves total
   portfolio economics and should be reported separately from trading alpha.

## Recommended Next Step

Run `BALANCED_DYNAMIC` in shadow mode for at least four weeks without changing
active quantities. For every real entry, store actual quantity, shadow quantity,
binding constraint, projected portfolio heat, and projected exposure. Review
only after at least 20 additional closed trades or 50 shadow-sized entries.

Activation criteria should include:

- profit factor at least 1.30;
- realized and mark-to-market drawdown below 3%;
- average exposure above 30% when valid setups exist;
- positive excess return versus the cash benchmark;
- no deterioration in sector/factor concentration.

## Limitations

- Only completed trades are included in the exact comparison. Open-position
  unrealized PnL is excluded.
- Alternative quantities reuse original exits. A larger position could have
  different fills or slippage in real execution.
- Drawdown is realized-equity drawdown, not intraday mark-to-market drawdown.
- Strategy rules changed during the archive period. Candidate analysis applies
  current-style gates where recorded fields allow it, but it is not a full
  walk-forward backtest.
- Results are paper-trading analytics, not financial advice and not real orders.
