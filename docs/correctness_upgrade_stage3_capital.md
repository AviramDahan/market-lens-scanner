# Stage 3: Align Preliminary and Final Exposure Ceilings

## Defect and change

Both `app.strategy.apply_strategy_decisions` and the UI agent's
`update_workbook` built a risk context with an effective market-regime exposure
ceiling, but passed the legacy workbook/default ceiling to preliminary sizing.
With the existing dynamic policy allowing $60,000 and exposure at $45,000,
preliminary sizing could return zero shares because its ceiling was $40,000.
The candidate could be rejected before the final risk layer evaluated it.

Both entry points now pass `run_context.market_regime.max_total_exposure` to
preliminary sizing. The workbook/default value still initializes the risk
configuration. No risk configuration, threshold, score, or Universe was changed.
This removes an inconsistent earlier ceiling; it does not authorize a purchase
unless all final risk gates approve it. The effective policy can also be more
restrictive than the legacy value, including a zero exposure ceiling.

## Verification scope

`tests/test_capital_ceiling_alignment.py` checks $0/$20k/$40k/$60k through both
real orchestration entry points, without writing a workbook or sending messages.
It also exercises real preliminary sizing at $45k exposure: a $60k policy permits
sizing, while $40k/$20k/$0 policies do not. A final WATCH gate still prevents BUY.

Historical accounting corrections remain separate. Do not use contaminated GILD
results as evidence for capital-policy effectiveness. This change is a consistency
repair, not a claim of improved profitability or a full portfolio replay.

## Remaining work

- Historical event reconciliation and provenance annotations.
- Relative-strength correctness and threshold-scale compatibility.
- Further cash/heat/exit accounting and performance measurement review.
- Production verification after this package is released.
