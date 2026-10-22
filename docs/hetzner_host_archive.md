# Verified host recovery mirror

## Scope and activation status

This is the prerequisite mirror for the archive transition, not its cutover.
`run_runtime.py`, Git persistence before Telegram, scanner, monitor, portfolio,
and all trading rules remain unchanged. Installing these files does not stop
generated-data commits or reclaim Git history. No original history is deleted.

Activation requires an approved PRIVATE archive repository and a dedicated
fine-grained token restricted to that repository, Contents: read/write only.
Put `MARKET_LENS_ARCHIVE_TOKEN` and `MARKET_LENS_ARCHIVE_REPOSITORY` in the
owner-readable host.env (mode 600). Never use the laptop's broad CLI token.
No token is passed in a command argument, receipt, manifest, or archive.

## Format and recovery

Each generation includes a full hash inventory of the workbook, structured
agent_results, monitor heartbeat/persistence state, pending outboxes, deployment
revision, and images specifically referenced by pending outboxes. Other derived
media, credentials, host configuration, Docker/Git caches, scanner staging, and
locks are excluded. Existing media/history are preserved in their current places.
This restores structured portfolio/execution state, not the server's secrets,
Docker images, all historical charts, or operating-system configuration.

Unchanged files refer directly to their earlier verified generation/asset.
Changed files go into ZIP parts (128 MiB raw target, under 1 GiB per asset).
Individual files above 512 MiB fail explicitly rather than truncate. Every
generation has a SHA-256 identity. Referenced older Releases must not be deleted.
No unbounded chain traversal or historical repository clone is required.

Only freezing uses the portfolio writer lock. Upload/download/restore use a
separate nonblocking publisher lock outside the writer lock. The live source is
never a restore target. The runner reserves 12 GiB plus conservative space for
ZIPs, downloads and an isolated restore; busy/low-space checks do not claim a
successful backup. A failed or incomplete generation is kept for retry.

Uploads are append-only. Incomplete GitHub starter assets are not overwritten
or deleted; retry uses a new asset name. A downloaded manifest and every file
are checked against SHA-256, the workbook is ZIP-validated with required sheets,
including actual relationships and worksheet XML, and pending notification
schemas are checked against their consumers in an isolated restore. Only then are
verified.json and a durable local last_verified.json written. No Telegram call,
trade execution, mutation of the portfolio, or live restore is performed.
Every worksheet is decoded with openpyxl with declared dimensions reset, not
just inspected as XML. The two portfolio sheets are mandatory. On the minimal
host Python the check uses the already deployed worker image
in an offline, read-only, resource-limited container, mounting only the workbook
and a small parser script. It receives no environment secrets and runs no Agent
or notification code. Missing/failing parser is a failed check, never success.

## Commands

From the host checkout:

```sh
python3 deploy/hetzner/host_archive_runner.py local-check
python3 deploy/hetzner/host_archive_runner.py publish
python3 deploy/hetzner/host_archive_runner.py status
```

The unit grants the trader service account the Docker group for its offline
Excel parser. For manual checks when the SSH login lacks that group, use
`sudo -u trader -g docker python3 deploy/hetzner/host_archive_runner.py local-check`.

local-check requires no token and does not upload. It keeps the frozen pending
generation, so publish retries that exact generation rather than silently
substituting newer data. Metadata reports its actual collection time.
Destination identity and disk headroom are checked again on pending retries.
Invalid live source is rejected before freezing. If a frozen copy is damaged or
needs operator review, use `quarantine-pending --reason operator-review` explicitly;
the pending metadata is moved aside and its bundle is retained, never silently
deleted. Investigate/preserve the quarantined generation before any cutover.

The supplied service/timer are NOT installed/enabled automatically. After a
successful private-remote restore proof, the daily mirror can run at 22:05
America/New_York, with a 30-minute service timeout and bounded CPU/memory.
Exit 3 means deferred/busy, not verified backup. Do not schedule repeated mirror
snapshots without a verified-copy retention policy; frozen copies currently
remain local for rollout evidence. The mirror alone is not the storage fix.

## Remaining cutover gates

1. Verify one private remote generation and isolated financial/outbox parity.
2. Prove bounded upload/runtime, failure/retry and concurrent monitor/scanner
   behavior before changing the portfolio's durability contract.
3. Replace generated-data push with a per-event verified backup checkpoint,
   preserving backup-before-delivery and exact source-code/deployment guards.
4. Freeze new generated-data Git blobs forward-only; retain legacy refs/history.
5. Enable safe retention of redundant verified temporary bundles and offload
   cold live data only with verified remote recovery and a tested rollback.

Do not disable legacy backup, rewrite history, or report migration complete
before these gates pass. A missing token or a public target is a blocked
activation, not a successful/empty archive. Private storage can affect the
archive repository's Actions allowance; no billing/visibility change is made
by this implementation.

## Opt-in Cold Backup Retention

`MARKET_LENS_COLD_BACKUP_ARCHIVE_ENABLED=true` enables capacity maintenance of
`/home/trader/market-lens-backups` only. It reuses the existing dedicated private
archive repository/token, with separate `cold-backup-v1-*` Releases that are NOT
financial checkpoint generations. No live data, Git history, images, volumes or
AI Trader directories are selected.

Unchanged rejected archives are recorded by file signature and validator version
so every half-hour check does not repeatedly decompress the same unsafe backup.
They stay local; a changed file is inspected again before any transfer.

The half-hourly idle maintenance check offloads at most 2 GiB per invocation when
free space is below 20 GiB. The newest two archives and every archive younger
than seven days stay local. Cold transfers are deferred between 09:00 and 16:30
New York on weekdays. Files at or above 1 GiB, unknown/sensitive contents, links,
and unsafe paths stay local and require separate review. This does not lower the
12 GiB execution reserve or change any trading rules.

Each eligible tar is inspected without extraction. Its complete original bytes
and deterministic metadata are uploaded append-only, downloaded by streaming,
and SHA-256 verified. Only then is a durable `.archive.json` pointer installed
beside the old path and the redundant local archive removed. Partial uploads,
verification failures, interrupted cleanup and concurrent invocations cannot
retire an unverified original. Receipts and remote assets must be retained.

To restore an old archive to a NEW, non-live destination, from the host checkout:

```sh
python3 -m deploy.hetzner.cold_backups \
  --restore-receipt /home/trader/market-lens-backups/OLDER_BACKUP.tar.gz.archive.json \
  --destination /home/trader/recovery/OLDER_BACKUP.tar.gz
```

This restores an archive only, never applies it to the live portfolio, and refuses
to overwrite an existing file. Cold backup health and transferred bytes appear
in the existing maintenance status; financial checkpoint receipts remain separate.
