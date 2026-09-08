# Correctness Upgrade: Stage 0

## Status

Baseline and executable defect reproductions only. No application behavior,
strategy configuration, portfolio state, scheduler, or production deployment
was changed. The next implementation package is Stage 1 (session time and
entry-confirmation validity).

## Frozen Baseline

The audit data revision is:

`7f7a3916b446c47c3b47710ea0631b56c1dfed7f`

The local source checkout was behind that revision in generated results.
The audit verified that application code matched; the newer portfolio was
exported from the frozen revision rather than taken from the older checkout.

An independent, local backup lives outside the repository in:

`outputs/baseline_stage0_20260908/` under the parent Codex workspace.

Contents:

- `source.tar`: application, agent, tests, workflows, docs, README and package configuration.
- `tracker.xlsx`: the frozen production tracker, 56,504,584 bytes.
- `dashboard_snapshot.json`: the matching dashboard data.
- `lin_run.jsonl`, `chtr_run.jsonl`: actual decision records demonstrating stale confirmation buys.
- `manifest.json`: revision, original file paths, byte counts and SHA-256 hashes,
  including individual files inside the source archive.

Every exported file hash was verified after writing. No live `.env` or secret
store was exported. These potentially large artifacts must not be committed
with the source changes.

## Defect Reproductions

| ID | Contract | Cases | Repair package |
| --- | --- | ---: | --- |
| AUDIT-CLOCK-01 | Holidays cannot authorize new entries | 2 | Stage 1 |
| AUDIT-CLOCK-02 | Early-close sessions end at the exchange close | 2 | Stage 1 |
| AUDIT-CLOCK-03 | Regular session is closed at the closing timestamp | 1 | Stage 1 |
| AUDIT-ENTRY-01 | A previous-session confirmation cannot authorize an active buy | 1 | Stage 1 |
| AUDIT-EXIT-01 | Existing positions use their persisted targets | 1 | Stage 2 |
| AUDIT-SIGNAL-01 | Losing less is stronger than losing more against a falling benchmark | 1 | Signal correctness |
| AUDIT-METRIC-01 | MFE excludes price observations after the exit | 1 | Exit/measurement correctness |

The new tests use synthetic data and patched providers. They do not place
paper trades, call a broker, fetch market data, or modify a tracker.

The positive regular-session control and negative weekend control pass.
All nine defect cases fail with `--runxfail`, reproducing the audited behavior.

Known failures are marked `xfail(strict=True)`. This is not a claim that the
defects are repaired. An unexpected pass fails the normal suite and requires
review/removal of the marker alongside the corresponding repair. Existing
baseline tests were not weakened or deleted.

## Commands

Reproduce the known failures (expected nonzero exit):

```powershell
python -m pytest -q --runxfail --tb=no tests/test_audit_regressions.py tests/test_agent_entry_gates.py::test_audit_prior_session_confirmation_cannot_buy
```

Run the regression suite and show known failures:

```powershell
python -m pytest -q -rx
```

Baseline before this package: 198 passed. Verified Stage 0 full-suite result:
200 passed, 9 xfailed in 15.15 seconds (exit code 0). The explicit reproduction
run with `--runxfail` produced 9 failed and 2 passed in 1.20 seconds, as intended.
These are nine known defect cases, not nine fixes.

## Stage 1 Boundaries

Implement one tested exchange-session calendar and use it for entry eligibility.
Do not rely on the external scheduler or workflow `force` flag for correctness.
Validate completed bars against their actual close time and observation time.
Treat old confirmations as watch information rather than active entry permission.
Fail conservatively when required timing information cannot be established.

Before deployment, cover holidays, early close, daylight-saving transitions,
opening-session bars, completed last bars, missing timestamps, future timestamps,
and valid same-session confirmations. Update the old measurement-only freshness
test to the new contract; do not merely remove it.

Do not alter sizing, target policy, scoring, strategy selection, historical PnL,
or position-monitor execution in this package. Review any shared calendar
consumer before changing it, since an exit must not silently become disabled.

## Rollback and History

Stage 0 itself needs no production rollback because nothing was deployed.
Later source rollback must restore only the relevant source/config revision,
not overwrite the live tracker with an old backup. Portfolio changes after the
baseline must be reconciled, never erased by a code rollback.

Keep the original history and annotate any future analytics recalculation with
its calculation version. Deploy fixes in small reviewed commits with a distinct
verification result for each stage.
