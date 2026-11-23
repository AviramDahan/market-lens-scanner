# Host Memory Diagnostics and Writer Queue

Scope: incident items 2 and 3 only. No SQLite migration, data restoration,
strategy, sizing, entry/exit policy, or portfolio changes.

## Memory Diagnostics

The incident probe imported `app.storage` in a second Python process inside
the 1 GiB web cgroup. Application imports consumed additional memory and the
kernel killed uvicorn. A host's available RAM does not prove container headroom.

Run from the host, using the existing Docker-group service identity:

```sh
python3 deploy/hetzner/runtime_diagnostics.py
```

The tool uses standard-library-only, read-only Docker API GET requests and
cgroup v2 counters. It never uses `docker exec`, imports application modules,
opens SQLite, starts workers, or sends Telegram. Output is allowlisted; raw
Docker metadata and exception messages are not logged. Container identity is
checked again after sampling. Changed identities are `NOT_VERIFIED`.

The diagnostic process is bounded to 192 MiB virtual memory, 15 CPU seconds,
64 descriptors, 10-second socket operations, and bounded response/file reads.
These limits apply to the host diagnostic, not the application. Memory usage
at or above 85%, OOM evidence, or unverifiable accounting is not reported green.
Existing web memory/CPU limits are unchanged. Never run heavy diagnostic imports
inside the live web container. No automatic restart or resource increase is added.

This prevents the unsafe probe path; it does not claim to eliminate every
possible application memory leak. Production usage must be measured separately.

## Writer Coordination

Previously scanners failed after 60 seconds even when a healthy monitor
transaction was still within its 360-second transaction budget. Both scanner
critical sections now have a bounded queue budget of 360 seconds. After
60 seconds the scanner records `DEFERRED_LOCK`, not a successful scan or failure.

Existing monitor priority reservation, kernel `flock`, snapshot hash checks,
transaction deadlines, checkpoints, and Telegram outbox flow are retained.
Owner deadlines can only shorten a wait; they are never renewed. Explicit short
maintenance/test timeouts remain authoritative. A waiter waking after its hard
deadline cannot enter the critical section. Real overruns still fail and alert.

Queue metadata is separate for `scanner_wait.json` and `monitor_wait.json`.
It never counts as an evaluation heartbeat or a persisted portfolio event.

## QA and Rollout

- Fake-clock tests cover both lock stages, valid waits beyond 60 seconds,
  hard deadlines, explicit bounds, and delayed wakeups.
- Linux subprocess tests cover real mutual exclusion, monitor priority,
  owner death, and a scanner waiting beyond its soft threshold.
- Memory tests verify read-only, bounded, redacted, host-only collection.
- No test uses real trades, real notifications, or a live portfolio fixture.
- These are host-only changes; do not recreate the web container to install them.
- Keep a copy of the previous host runner and its revision before activation.
- After activation, observe actual scheduled execution, fresh heartbeat,
  successful persistence and queue metadata. No-position/off-hours success is
  not evidence of a live TP/SL event.

The separate SQLite persistence and lost-data recovery issue is intentionally
not addressed by this change and remains open.

## Recorded Validation

- Full local suite: 1080 passed, 13 skipped (platform-specific tests), 40.20s.
- Isolated Linux focused suite: 37 passed, including four real-flock tests.
  No live portfolio, Docker worker, or notification endpoint was used by tests.
- Linux QA needed missing test dependencies/source files added to its private
  directory before the final successful run; no host packages were installed.
- Read-only host probe of the live web cgroup: 319,369,216 / 1,073,741,824 bytes
  (29.74%), zero current-cgroup OOM events, container restart count still 1.
  PID/start identity stayed stable. The probe did not restart the web service.
- No SQLite operation, restore, migration, synthetic trade, or test alert.
- The host runner is not activated by running tests. Production rollout is a
  separate approval-controlled step; live queue recovery remains NOT_VERIFIED
  until the new runner is deployed and observed in normal scheduled execution.
