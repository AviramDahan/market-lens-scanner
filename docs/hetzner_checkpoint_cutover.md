# Verified Host Checkpoint Cutover

Default: `MARKET_LENS_HOST_BACKUP_BACKEND=legacy_git`. Merging code does not
activate the new backend or change trading behavior.

## Contract

`verified_archive` replaces generated-data and Telegram-receipt Git commits
with immutable Release checkpoints in the configured PRIVATE data archive.
Source history and existing data remain intact. Files keep their host paths;
cutover never deletes or restores a live workbook or result directory.

Legacy tracked data may intentionally be dirty after activation. Source
preflight excludes only `agent_results` and `agent_tracker` from cleanliness.
Every incoming commit is checked for generated-data changes before merging
its exact fetched SHA. Unexpected source edits or incoming data changes fail
closed instead of overwriting the live portfolio.

The workbook/results on `main` become frozen evidence, not the latest backup.
New analysis uses archive manifests/assets. Structured decision and measurement
history is included. The existing media policy includes pending-notification
images, not every derived/debug chart. Other charts stay on the host; legacy
Git copies remain preserved.

## Recovery States

- `FROZEN`: pending owns an immutable generation. Retry publishes that exact
  snapshot and cannot substitute newer live state.
- Bootstrap performs full remote restore: every hash, workbook decoding,
  receipt, cursor and notification envelope is checked.
- Later checkpoints restore all changed and critical files remotely. Inherited
  references must match previously verified same-repo manifests. Proofs clearly
  distinguish full restore from transitive incremental verification.
- `VERIFIED_PENDING_DELIVERY`: a verified manifest contains an outbox. Existing
  consumers, receipt keys and Telegram destinations remain unchanged.
- A removed outbox whose receipts were not yet backed up is reconstructed from
  its exact verified envelope, never by restoring cash/workbook. Existing local
  receipt keys reconcile sends before another writer is allowed.
- `COMPLETE`: a later verified checkpoint contains receipts and no outbox.
  Failed send/upload is never success. Telegram acceptance followed by a crash
  before local receipt fsync can still duplicate on retry; exactly-once delivery
  is not claimed.
- Publication has a bounded child: 30 seconds incremental, 600 for bootstrap.
  Timeout retains frozen data and cleans only disposable download/restore copies.
- A 12 GiB reserve and separate freeze/restore headroom checks remain in place.
  No historical results, images or volumes are pruned.
- Local compressed checkpoint caches keep the latest two generations and any
  pending-envelope dependencies. Older duplicate ZIP/manifest caches are removed
  only after hash/size and remote-verification checks. Source history, all remote
  releases/assets and unknown local evidence remain untouched. Failed cleanup
  cannot turn a durably persisted event into failure.
- Full workbook decoding happens on remote-restored content, not twice on source
  and restored copies. Identical already-verified workbook bytes may inherit that
  proof; a changed workbook must decode fully again. Proofs record which occurred.
- Standalone mirror publication is disabled while the active checkpoint backend
  owns the monotonic cursor.

## Activation Gate

1. Green Linux CI and isolated crash/retry/concurrency QA.
2. Pause both timers, drain writers, save a consistent rollback point, and verify
   the last legacy Git checkpoint was pushed.
3. Deploy matching source/images; keep writers paused.
4. As service user with Docker access run
   `python3 -m deploy.hetzner.archive_checkpoint bootstrap`.
   Retry interruption with `recover-bootstrap`, never replace pending data.
5. Verify private remote restore and matching portfolio/envelope/receipt hashes.
6. Set the host backend to `verified_archive`, verify recovery and resume timers.
7. Observe a new normal scan, heartbeat, remote checkpoint, unchanged accounting
   and healthy endpoints, with no generated-data Git commit.

Rollback cannot restore old portfolio state over newer events. Reconcile the
latest checkpoint first; do not blindly revert the backend after archive-only
events have accumulated.
