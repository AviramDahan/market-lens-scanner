# Setup Selection Offline Replay

The active scanner still uses `FIRST_MATCH_LEGACY`. This tool is a read-only
counterfactual audit and cannot alter `final_action`, positions, sizing, gates,
or the tracker workbook.

Run it against retained decision JSONL files:

```powershell
python agent/setup_selection_replay.py `
  --decision-dir agent_results/decisions `
  --output-dir agent_results/capital_replay
```

The report deduplicates to the first ticker/setup observation per New York
exchange date and separately groups regular-session and off-hours observations. It
recalculates candidate-specific executable entry and Gross/Net R/R using the
spread, slippage, and fee observations recorded for that ticker in the same
run. It separately records each downstream gate as `PASS`, `FAIL`, or
`UNASSESSABLE`.

Historical alternative candidates do not contain their own professional score,
completed-candle confirmation, target feasibility, or position sizing. The
replay therefore marks those gates `UNASSESSABLE`; it never borrows the active
candidate's evidence. Later recorded scan prices are included only as a
directional diagnostic and cannot establish stop/target order or executable
returns.

A higher shadow-normalized detector score is not evidence that an alternative
would have been an eligible or better trade. Do not replace
`FIRST_MATCH_LEGACY` based on this report alone.
