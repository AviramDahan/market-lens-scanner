# Historical accounting reconciliation - 2026-09-13

This is a read-only measurement audit. It does not rewrite the tracker, infer
replacement fills, or change any scanner, monitor, sizing, or portfolio rule.

## Realized PnL scopes

The current workbook contains two valid realized-PnL scopes that were previously
easy to confuse:

- Completed full trades: $63.15.
- Partial profits already realized by positions that remain open: $243.26.
- All recorded realized exit events: $306.41.

The relationship reconciles exactly:

`$63.15 + $243.26 = $306.41`

The open-position partial profits are CME ($81.54) and INTC ($161.72). The
existing `total_pnl_ils` full-trade field keeps its compatibility meaning:
completed trades only. Additive fields now expose completed-trade PnL, partial
realized PnL on open lots, and their all-lifecycle total separately. The `_ils`
suffix is legacy schema naming; the workbook currency setting controls display.

## Execution evidence findings

The identity/timestamp audit checked 39 recorded position events and found six
items requiring reconciliation across three identified trades:

- Four GILD events were recorded with trigger evidence before their linked entry.
- One CHTR event was recorded with trigger evidence before its linked entry.
- One legacy CME TP1 event has unresolved entry identity because its trade ID is blank.

The audit does not remove these rows or claim corrected performance. A clean
identity check also does not prove that a historical fill price was executable.
The report therefore retains evidence labels and explicitly states that cash,
PnL, and downstream capital effects were not corrected or replayed.

## Verification

Run the read-only audit with:

```powershell
python -m agent.audit_exit_history agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx --output outputs/historical_reconciliation_current.json
```

The JSON output includes `measurement_provenance` and
`accounting_reconciliation`. A zero reconciliation delta means the two accounting
scopes add up; it does not validate market data, event timing, or fill quality.
