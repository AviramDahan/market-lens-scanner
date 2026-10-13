# GitHub storage sustainability plan

Status: PLAN ONLY. No archive migration, history rewrite, deletion, repository
creation, visibility change or paid storage purchase is part of the live-status
UI repair. Existing trading data and strategy remain unchanged.

## Measured baseline

Read-only observations on 13 October 2026:

- GitHub reports `market-lens-scanner.size = 79,462,293` KB, approximately
  75.8 GiB using 1024-byte units. This API metadata is not a billing figure or
  a measurement of a universal hard repository quota. Pushes currently succeed
  but emit `Repository is approaching its size quota.`
- `market-lens-data-archive.size = 16` KB. Its heavy data is stored as Release
  assets rather than Git blobs. Both repositories currently report public
  visibility; changing access is a separate approval decision.
- The latest inspected structured-data Release contains a 166,718,964-byte ZIP
  and a per-file manifest with 2,648 records and SHA-256 hashes. This proves an
  archive exists, not that every historical file or current run is recoverable.
  Validate manifest timestamps/source commits, not the tag date alone.
- The server checkout is shallow. Local Git storage is about 5.85 GiB
  (`size + size-pack` from `git count-objects -v`), not the full remote history.
- Current generated files occupy about 2.32 GiB, excluding Git history:

| Checkout category | Files | Bytes |
| --- | ---: | ---: |
| Decision JSONL | 604 | 1,091,662,294 |
| Charts | 3,289 | 616,083,435 |
| Diagnostics | 605 | 539,600,875 |
| Screenshots | 299 | 170,667,386 |
| Summaries | 753 | 31,918,197 |
| Current Excel tracker | 1 | 29,679,124 |
| Current dashboard snapshot | 1 | 12,748,711 |

Removing media from the working directory is not a solution to existing Git
history size. JSONL and diagnostics are also large. Repeated workbook/snapshot
versions add binary history even when the current checkout looks manageable.

Evidence: host QA `storage_inventory_20261013.json`, GitHub repository/Release
API responses, and `archive_manifest.json` downloaded without modifying data.

## Why this is operationally important

`deploy/hetzner/run_runtime.py::persist` stages generated outputs and calls
`push_backup`; delivery follows successful persistence. A rejected Git push can
therefore delay outbox delivery and make the runtime recovery/preflight fail.
Do not bypass that guard or claim durable backup merely because a local commit
exists. Replace its persistence contract deliberately, with verified receipts.

## Recommended sequence

### 1. Complete the preservation inventory

Enumerate existing structured data, media, workbook generations, notification
receipts and runtime state. Compare them against archive manifests by path and
SHA-256. Identify missing files and coverage gaps before any cutover.

Upload missing historical files to the existing archive without overwriting
prior Release assets. Preserve the original Git repository and reachable refs.
An inventory is not a backup: independently download samples and a full latest
recovery set, verify every hash, and restore into an isolated directory.

Acceptance: current open/closed positions, cash, trade events, TP1 flags, stops,
event cursors and notification receipts reproduce exactly. No trade is created,
reopened, erased or restated. No history is rewritten or garbage-collected.

### 2. Add an immutable per-run archive contract

Use `market-lens-data-archive` Release assets for compressed run bundles, not
normal commits of large ZIPs or an endlessly growing data branch.

Each bundle includes run ID, source commit, data generation, Decision JSONL,
runtime/diagnostics/summaries, a consistent tracker snapshot, new event records,
notification receipts and a per-file SHA-256 manifest. Media uses content-hash
deduplication while preserving every existing unique file. New chart generation
keeps its current policy; this plan does not increase or decrease scan coverage.

Capture the immutable recovery set under a short writer lock, then release it
before compression/upload. Network backup must not hold the TP/SL writer lock.
Use conservative asset chunks below 512 MiB and maintain a small archive index.
Verify a uploaded bundle by independent download/hash comparison before writing
its archive receipt. Interrupted or competing retries reuse the run identity and
cannot overwrite a successful receipt or silently discard a pending bundle.

Acceptance: retry, partial upload, timeout, concurrent upload and corrupted
download tests pass. A failed upload remains visibly pending, not successful.

### 3. Validate dual backup and recovery

Keep the current writer as the sole writer while testing the new archive path
for a bounded period. Compare the same immutable generations, not a later live
workbook against an earlier archive. Test recovery with open positions, a TP1
partial plus breakeven stop, a fully closed trade and pending/delivered alerts.
Use isolated fixtures for intentional lifecycle cases; no real test alerts.

Acceptance: exact financial/state parity, no duplicate writer, no stale overwrite
and no duplicate Telegram delivery. Explicit rollback and cutover prerequisites
are recorded. Dual writes are temporary: the old Git data growth must stop.

### 4. Separate code from runtime data without deleting history

Recommended: create a clean code-only repository (proposed name
`market-lens-core`) after approval. Preserve `market-lens-scanner` as the original
historical repository; do not delete it or rewrite its refs.

The clean repository keeps app/agent code, tests, workflows, docs and small
archive manifests. Production data lives in existing persistent host storage
and verified Release bundles, outside the new Git checkout. The dashboard URL
and historical chart/download routes remain unchanged and must resolve through
the data mount/archive index. Retain provenance links to original code commits.

A feature branch in the existing repository does not remove its shared large
object history. A clean code repository avoids importing that history into each
new checkout while leaving the original evidence reachable. If complete
independent Git-history mirroring is desired, provision a separate destination
with enough capacity; do not download approximately 76 GiB onto the live server
without a capacity review. Leaving old refs reachable is not an independent
disaster-recovery copy.

Acceptance: dependency-frozen code equivalence, one writer, scanners/monitor,
live UI, old chart URLs, Excel download, archives, receipts and Telegram all pass
E2E before old data commits stop. AI Trader services remain untouched.

### 5. Switch persistence safely

Replace `persist/push_backup` success with the approved immutable-archive receipt
contract, retaining local atomic portfolio saves and persistent outboxes. Never
mark a failed backup as success to keep the service green. Explicitly decide and
test the ordering between local durable save, remote archive acknowledgment and
notification delivery; this is not a casual change to `deliver` or preflight.

Old GitHub scanner/monitor workflows remain disabled. The host remains the only
portfolio writer. Code rollback must not restore an older workbook over newer
trades. The original repository and all previously archived files remain intact.

Acceptance: recovery from network/permission/size failures and process restarts
does not lose positions, events, receipts or duplicate notifications.

### 6. Add bounded maintenance and alerts

Proposed operational thresholds for the clean code repository, not trading
gates: warning at 1 GiB, critical at 2 GiB, growth warning at 100 MiB/day for two
days. Review thresholds after measuring the clean baseline. Daily inventory
tracks code Git size, archive coverage, pending bundles, upload age, failed
verification, host free disk and restore-test date.

Use daily immutable run/delta bundles and periodic full recovery snapshots to
avoid uploading the entire structured history repeatedly. A compact manifest
must identify the full snapshot and ordered deltas needed for a restore.
Thursday maintenance checks coverage and recovery, not just Docker cache.
No time-based purge of historical trading data is authorized.

Acceptance: missed backup or failed verification creates a clear operations
alert using the existing target; no new public endpoint exposes secrets.

## Constraints and costs

GitHub recommends keeping `.git` below 10 GB and generated outputs outside Git.
That recommendation is not the exact hard quota implied by this repository's
push warning. If the warning persists or a push is rejected, obtain GitHub's
repository-specific diagnosis rather than guessing a remaining allowance.

Releases currently allow up to 1,000 assets per release and files below 2 GiB,
with no documented total release-size or bandwidth limit. This is not a storage
SLA or a promise of unlimited supportability. Recheck policy before migration
and use periodic, software-associated data bundles instead of treating GitHub
as a high-frequency database.

Do not automatically move generated outputs into Git LFS: free storage/download
quotas and per-version binary storage can create a new bill or write blocker.
No paid GitHub feature or extra server resource is approved in this plan.

Sources verified on 13 October 2026:

- [Repository limits](https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits)
- [Release asset limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)
- [Git LFS billing](https://docs.github.com/en/billing/concepts/product-billing/git-lfs)
