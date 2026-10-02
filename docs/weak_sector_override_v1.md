# WEAK_SECTOR_OVERRIDE_V1

Status: measurement-only infrastructure. It does not modify active trading decisions,
position sizing, portfolio state, alerts, thresholds, or the sector gate.

## Existing foundation

The experiment extends the read-only sector analysis in
`app/decision_quality_analysis.py` and the audit in
`docs/qualified_selection_sector_audit_2026-10-02.md`. The existing conservative
5-minute replay remains authoritative for signal outcomes: regular-session bars,
frozen execution costs, stop-first ordering inside an ambiguous bar, half exit at
TP1, stop moved to entry, then TP2.

## Runtime measurement

Each active decision receives a `weak_sector_override_v1` evidence object. The first
regular-session observation for each New York date, ticker, setup and active entry
path is persisted to:

`agent_results/experiments/weak_sector_override_v1_observations.jsonl`

A WEAK-sector signal is eligible only when the sector gate is the sole failure and
every other gate is an explicit `PASS`, including executable quantity, cash,
exposure and portfolio heat. Missing evidence is `UNASSESSABLE`. Standard and
Neutral Pilot thresholds are evaluated independently from the persisted active path.

The observation freezes the strategy/execution versions, entry, stop, targets,
R/R, cost policy, sizing and market/sector context. It never calls the execution
path and never changes `final_action` or the workbook portfolio.

Eligible STRONG-sector observations are retained as controls. Comparison uses all
controls from the same setup type, market regime and setup-score bucket; examples
are not selected after their outcome is known.

## Summary command

```powershell
python agent/weak_sector_override_summary.py
```

For schema validation without downloading market data:

```powershell
python agent/weak_sector_override_summary.py --skip-market-data
```

Outputs:

- `agent_results/summaries/weak_sector_override_v1_summary.json`
- `agent_results/summaries/weak_sector_override_v1_summary.md`

Closed and censored outcomes are reported separately. Signal results are explicitly
not portfolio returns. When no signal qualifies, the report keeps the count at zero
and lists persisted ineligibility reasons.

## Review gate

Review may begin only after at least 50 signals, 30 closed outcomes, 20 trading days
and three sectors. Meeting these minimums permits a review; it does not prove an edge,
activate the override, or authorize an active policy change.

## Deployment

The branch must be deployed before new runtime observations can accumulate. No merge
or deployment is part of this implementation without explicit approval.
