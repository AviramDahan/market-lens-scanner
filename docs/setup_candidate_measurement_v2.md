# Setup Candidate Measurement v2

## Purpose

The scanner continues to select the first matching detector through
`FIRST_MATCH_LEGACY`. Candidate measurement v2 adds prospective, read-only
evidence for every matching setup so future reviews can compare candidates
without borrowing evidence from the active setup.

This change does not rank candidates, alter `final_action`, open a paper trade,
change a threshold, or change position sizing.

## Persisted candidate evidence

Each item in `setup_candidates` now records:

- Detector order and whether it is the active legacy candidate.
- Legacy and normalized detector scores.
- Candidate-specific professional quality and adjusted scores.
- Candidate-specific professional grade, decision, and warnings.
- Candidate-specific Target 1 and Target 2 ATR distances.
- Candidate-specific target feasibility and market-structure status.
- The informational entry trigger produced for that candidate.

The schema is identified by `candidate_measurement_version=setup_candidate_v2`.

## Agent-side risk evidence

The UI scanner initially stores completed-candle confirmation as
`NOT_EVALUATED`. When the Agent evaluates the active ticker, it reuses the same
already-loaded market snapshot to measure each candidate independently and adds:

- Gross, TP1, TP2, and weighted Net R/R.
- Executable entry.
- Config-aware ATR and market-structure target validation.
- Completed-candle entry confirmation and freshness.

This second schema is identified by
`candidate_risk_measurement_version=setup_candidate_risk_v1`. It is copied into
Decision JSON only; it is not passed into active selection or trade execution.

Executable portfolio sizing is still intentionally not inferred for alternative
candidates. The offline replay treats that gate as `UNASSESSABLE`; it never
copies the active candidate's size or exposure result.

## Safety contract

- Candidate measurements are attached after the active result is selected.
- The active result's setup type, score, levels, and action remain unchanged.
- Historical v1 records remain readable and retain `UNASSESSABLE` evidence.
- A candidate cannot become eligible in replay while any required gate lacks
  candidate-specific evidence.

## Verification

- Unit tests assert that the active result is unchanged.
- Replay tests assert that candidate evidence reduces only the corresponding
  evidence gaps.
- Missing sizing continues to block a counterfactual `PASS`.
- A live single-ticker scan verifies that candidate v2 data survives the real
  scanner path.
