# Stage 2: Exit Identity and Historical Reconciliation

## Released code

Commit `13423635` binds monitor events to the current trade, rejects events
before entry, filters legacy cursors against current entry time, and persists
the trade ID in Position Events. Scanner exits use stored position targets,
and their Trade Log rows capture the stored exit plan before position mutation.
Excursion measurement excludes bars after the selected exit bar.

Local verification: 265 passed, 1 expected failure. The remaining expected
failure concerns negative-benchmark relative strength, not exit identity.
No signal thresholds, Universe selection, or capital settings changed.

## CHTR incident

Affected trade: `20260904_140242-CHTR`, entered September 4 at 14:06:53 UTC,
26 shares at $150.36. Stored stop: $146.49. Targets: $159.44 and $180.62.

The September 8 monitor run `20260908_132607` incorrectly recorded EXIT_STOP
using the September 1 14:11 UTC bar. The bar predates entry. The recorded
cash inflow was $3,808.74 and realized loss was $100.62.

The original workbook is backed up outside the repository at
`outputs/stage2_reconciliation/before_164d9d05e60846ae7fbff3d43cb0980c74cdb485.xlsx`.
SHA256: `4104db0b9fbbc1747d6f3c8159efae9ce102d4681f74f05203579757a7bd541d`.

## New evidence; do not restore the position blindly

A September 8 query through the existing intraday provider returned 615 bars
from September 4 14:07 UTC through September 8 18:48 UTC. Within the returned
post-entry bars, the first stop touch was September 8 at 15:03 UTC:

| Open | High | Low | Close |
| --- | --- | --- | --- |
| 146.710007 | 146.710007 | 146.389999 | 146.449997 |

No returned bar reached TP1 before that stop. Under the existing stop-price
fill convention, this later valid event produces the same financial loss.
This is evidence from the returned provider data, not a guarantee that every
minute of historical market data is complete or an executable fill estimate.

Therefore the repair must reconcile event time and supporting evidence rather
than reopen CHTR or add another cash movement. Preserve original records in an
audit trail, correct trade duration/excursion analytics, and mark historical
snapshots as superseded where appropriate. Check the latest workbook and any
subsequent CHTR trades under serialized repository persistence before applying.

**Ledger correction has not yet been applied.** No workbook or financial
result was changed by this release. Historical capital decisions between the
invalid recorded exit and the valid later touch are not retroactively repaired.

## Remaining limitations

A read-only check of all 37 Position Events in the frozen backup found five
pre-entry events, including CHTR. Four belong to two later GILD trades, verified
against the Trade Log trade IDs and quantities:

| Trade ID | Recorded actions | Trigger bars before entry | Recorded net profit |
| --- | --- | --- | --- |
| 20260721_150256-GILD | July 21 partial and full profit | July 16 19:56 / July 17 13:30 UTC | $205.39 |
| 20260805_173144-GILD | August 7 partial and full profit | August 4 19:55 / August 5 13:30 UTC | $442.32 |

The combined $647.71 is profit whose recorded execution evidence is invalid,
not a claim that the corrected profit must be zero. Historical post-entry bars
must be recovered before reconstructing a defensible outcome. These records
must not be used as validated strategy evidence or silently removed from cash.
This audit covers monitor events present in the backup, not every possible
scanner exit or missing event. Historical reconciliation remains open.

- Same-minute stop/target ordering remains ambiguous; the existing conservative
  stop-first convention is unchanged.
- The event bar itself remains the finest excursion resolution; its high/low
  can include movement after the exact intrabar fill.
- One event per position per monitor run is unchanged.
- This release is not a complete redesign of scanner/monitor reconciliation.

## Cloud validation

Monitor workflow validation run: `34265207679`, on commit `13423635`, completed
successfully. Its monitor log reports completion with no portfolio events.
Render deployment ID `6334694586` also completed successfully. Post-deploy
checks returned HTTP 200 for `/health`, `/agent`, and `/agent/data`.

These checks validate release and monitor execution, not repaired historical
accounting. The latter remains incomplete as described above.
