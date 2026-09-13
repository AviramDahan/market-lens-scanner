# Capital diagnostics and persistence safety

## 2026-09-10

Preliminary sizing can return SKIP before the final risk evaluator runs its
buy-blocker collection. This left capital_blockers empty and hid quality
failures behind a generic sizing reason. Such candidates now collect the
existing blocker checks diagnostically. Their action remains SKIP and size zero.
Quality failures take precedence in the explanation, with the sizing failure
also retained. If no observed entry gate fails, CAPITAL_BLOCKED_UNASSESSED is
used: zero-size sector/factor checks are not proof of eligibility at a positive
allocation. No risk limits, scoring thresholds or entry eligibility were relaxed.

Both scanner and monitor workflows now compare committed agent_tracker and
agent_results trees against the original run commit before either reset/overlay
attempt. Source-only commits may proceed; changed state or unresolved refs fail
closed. Existing recovery artifact steps retain generated output on failure.
Normal Git push rejection still guards changes after the comparison. This is
not a transaction with Telegram or other external side effects; reconcile a
conflict before retrying, rather than blindly re-running notifications.

Tests: 338 passed, including zero-size quality/capital diagnostics and real
temporary Git repositories verifying state conflicts versus source-only edits.

Media retention deletions are now carried through reset/overlay by the generated
state bundle manifest, including explicit deleted paths. Structured history was
archived before the active checkout was reduced; see
`agent_results_retention_2026-09-12.md`. Historical execution evidence still
requires manual reconciliation; realized-PnL scope reconciliation is explicit
and read-only.

## Rejection summary measurement

Daily/weekly summaries previously cut reasons at the first period, including
decimal points: 1.20 and 1.95 became the same fragment. Full reasons are now
counted unchanged. The additive rejection_diagnostics object counts recorded
entry and capital blockers independently and labels counts as decision
observations, not independent opportunities. Duplicate reasons in one decision
count once; repeated scans remain distinct observations. Missing/empty legacy
blockers are reported as missing evidence, never as proof that gates passed.
Existing decisions and historical reports are not rewritten. No UI schema field
was removed. Full suite: 342 passed.
