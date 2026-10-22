"""Single-writer host runner for the paper portfolio. Disabled until cutover."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path

if __package__:
    from .archive_checkpoint import Checkpoints, enabled as archive_enabled, require_headroom
    from .local_write_fence import (begin_monitor, begin_scanner, cancel_no_event_monitor,
                                    finish_local_write, recover_local_write, seal_monitor, seal_scanner)
    from .notify_ops import send_runtime_alert
    from .staged_scanner import ScannerStage, compact_failed_stage, prepare_stage, promote_stage
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from deploy.hetzner.archive_checkpoint import Checkpoints, enabled as archive_enabled, require_headroom
    from deploy.hetzner.local_write_fence import (begin_monitor, begin_scanner, cancel_no_event_monitor,
                                               finish_local_write, recover_local_write, seal_monitor, seal_scanner)
    from notify_ops import send_runtime_alert
    from staged_scanner import ScannerStage, compact_failed_stage, prepare_stage, promote_stage

try:
    import fcntl
except ImportError:  # Tests also run on Windows; the deployed host is Linux.
    fcntl = None


REPO = Path("/home/trader/market-lens-runtime")
STATE = Path("/home/trader/market-lens-runtime-state")
COMPOSE = REPO / "deploy/hetzner/compose.runtime.yaml"
STAGE_COMPOSE = REPO / "deploy/hetzner/compose.stage.yaml"
TRACKER = REPO / "agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx"
GENERATED = ("agent_tracker", "agent_results")
CODE_PATHS = ("app", "agent", "pyproject.toml", "config.yaml")
HOST_ONLY_AGENT_FILES = {"agent/ops_health_check.py", "agent/production_smoke.py"}
SUCCESS_STATUSES = {"COMPLETE", "PARTIAL_OK"}
PUSH_RETRY_DELAYS = (2, 5)
SCANNER_LOCK_WAIT_SECONDS = 60
MONITOR_LOCK_WAIT_SECONDS = 360
# Includes local freeze/promotion, two bounded publications, notification work
# and worker-stop cleanup. An overrun remains a failure, never a lease renewal.
WRITER_TRANSACTION_SECONDS = 360
WRITER_OWNER = None


class RuntimeDeliveryFailure(RuntimeError):
    """A persisted portfolio event could not reach the trade Telegram group."""


class ScannerSnapshotStale(RuntimeError):
    """A monitor event changed the portfolio while a scanner was running."""


def report_runtime_alert(kind: str, event: str) -> None:
    try:
        status = send_runtime_alert(kind, event)
    except Exception:
        status = "failed"
    print(f"Operations alert: {status}", file=sys.stderr)


def run(*args: str, cwd: Path = REPO, timeout: int = 60,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True, timeout=timeout, env=env)


def output(*args: str, cwd: Path = REPO) -> str:
    # Porcelain status uses leading columns; stripping them hides a recoverable
    # receipt-only change after a delivery succeeded but the receipt push failed.
    return subprocess.check_output(args, cwd=cwd, text=True, timeout=60).rstrip("\r\n")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_disk_capacity() -> None:
    try:
        free_gb = shutil.disk_usage(REPO).free / (1024 ** 3)
    except OSError as exc:
        print(f"Unable to inspect runtime disk capacity: {exc.__class__.__name__}", file=sys.stderr)
        return
    try:
        minimum_gb = max(1.0, float(os.getenv("MARKET_LENS_MIN_FREE_GB", "12")))
    except ValueError:
        minimum_gb = 12.0
    if free_gb < minimum_gb:
        print(f"Runtime disk low: {free_gb:.1f} GiB free (threshold {minimum_gb:.1f} GiB)",
              file=sys.stderr)
        report_runtime_alert("scanner", "DISK_LOW")


def require_live_preflight() -> None:
    if os.getenv("MARKET_LENS_HETZNER_WRITER_ENABLED") != "true":
        raise RuntimeError("Hetzner writer is disabled")
    if os.getenv("MARKET_LENS_OLD_WRITERS_DISABLED") != "true":
        raise RuntimeError("Legacy scanner/monitor writers have not been disabled")
    if not TRACKER.is_file():
        raise RuntimeError("Paper tracker is missing")
    archived = archive_enabled()
    status_args = ("--", ".", ":(exclude)agent_results", ":(exclude)agent_tracker") if archived else ()
    status = output("git", "status", "--porcelain", *status_args)
    if any(line[3:] != "agent_results/telegram_notifications.jsonl"
           for line in status.splitlines()):
        raise RuntimeError("Runtime checkout has unpersisted changes")
    # Archive mode is deployment-pinned. Refreshing source refs is deployment
    # work, not a network dependency of every TP/SL evaluation.
    if not archived:
        run("git", "fetch", "origin", "main", timeout=300)
    local = output("git", "rev-parse", "HEAD")
    remote = output("git", "rev-parse", "origin/main")
    if local != remote:
        common = output("git", "merge-base", "HEAD", "origin/main")
        if common == remote:
            if archived:
                raise RuntimeError("Source checkout has unpublished commits; checkpoint backend will not push them")
            # The previous run committed data locally but exhausted push retries.
            push_backup()
            run("git", "fetch", "origin", "main", timeout=300)
        elif common == local:
            if archived and output("git", "log", "--format=%H", f"HEAD..{remote}", "--", *GENERATED):
                raise RuntimeError("Remote source changed archive-managed data; refusing to overwrite the live portfolio")
            if archived:
                raise RuntimeError("Source revision changed; deploy the pinned revision before resuming writers")
            run("git", "merge", "--ff-only", remote if archived else "origin/main", timeout=60)
        else:
            raise RuntimeError("Runtime and GitHub histories diverged; manual reconciliation required")
    if output("git", "rev-parse", "HEAD") != output("git", "rev-parse", "origin/main"):
        raise RuntimeError("Local portfolio commits are not backed up to main")
    deployed = (STATE / "deployed_revision").read_text(encoding="utf-8").strip()
    if not deployed:
        raise RuntimeError("Deployed code revision is missing")
    changed_code = "\n".join(
        path for path in output("git", "diff", "--name-only", deployed, "HEAD", "--", *CODE_PATHS).splitlines()
        if path not in HOST_ONLY_AGENT_FILES
    )
    if changed_code:
        raise RuntimeError(f"Code changed since deployed image: {changed_code}")
    if archived:
        checkpoints = Checkpoints(REPO, STATE)
        recover_local_write(checkpoints, output("git", "rev-parse", "HEAD"))
        receipt = checkpoints.recover()
        if checkpoints.receipts_changed(receipt):
            persist_notification_receipts()
    elif status:
        persist_notification_receipts()
    for kind in ("scanner", "monitor"):
        if (STATE / "outbox" / f"{kind}.json").exists():
            deliver(kind)
    if output("git", "status", "--porcelain", *status_args):
        raise RuntimeError("Runtime checkout has unpersisted changes after recovery")
    check_disk_capacity()


def worker(command: list[str], *, timeout: int, stage: ScannerStage | None = None) -> None:
    container_name = f"market-lens-worker-{uuid.uuid4().hex[:12]}"
    compose_files = ["-f", str(COMPOSE)]
    env = None
    if stage is not None:
        compose_files += ["-f", str(STAGE_COMPOSE)]
        env = os.environ.copy()
        env.update({
            "MARKET_LENS_STAGE_TRACKER_DIR": str(stage.tracker),
            "MARKET_LENS_STAGE_RESULTS_DIR": str(stage.results),
            "MARKET_LENS_STAGE_RUNTIME_DIR": str(stage.runtime),
        })
    args = [
        "docker", "compose", *compose_files, "run", "--rm",
        "--name", container_name, "worker", *command,
    ]
    try:
        run(*args, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        subprocess.run(("docker", "stop", "--time", "15", container_name),
                       check=False, timeout=35)
        raise RuntimeError(f"Worker timed out after {timeout} seconds") from None


def latest_scan_record(previous: set[Path], results: Path | None = None) -> dict:
    runtime = (results or REPO / "agent_results") / "runtime"
    created = set(runtime.glob("market_lens_agent_*.json")) - previous
    if len(created) != 1:
        raise RuntimeError(f"Expected one new scanner record, got {len(created)}")
    record = json.loads(created.pop().read_text(encoding="utf-8"))
    if record.get("run_status") not in SUCCESS_STATUSES or not record.get("result_cards_read"):
        raise RuntimeError("Scanner did not produce a successful, nonempty scan")
    return record


def push_backup() -> None:
    # A rejected push must not be treated as persistence, even when the commit exists locally.
    for attempt in range(len(PUSH_RETRY_DELAYS) + 1):
        try:
            run("git", "push", "origin", "HEAD:main", timeout=90)
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == len(PUSH_RETRY_DELAYS):
                raise
            print(f"GitHub backup push failed; retrying ({attempt + 1}/{len(PUSH_RETRY_DELAYS) + 1})",
                  file=sys.stderr)
            time.sleep(PUSH_RETRY_DELAYS[attempt])


def persist(kind: str) -> bool:
    writer_phase("CHECKPOINT")
    if archive_enabled():
        checkpoints = Checkpoints(REPO, STATE)
        receipt = checkpoints.save(output("git", "rev-parse", "HEAD"))
        finish_local_write(checkpoints)
        print(f"Archive checkpoint verified: {receipt['manifest']['generation']}", flush=True)
        return True
    run("git", "add", "--", *GENERATED)
    if subprocess.run(("git", "diff", "--cached", "--quiet"), cwd=REPO).returncode == 0:
        if (STATE / "outbox" / f"{kind}.json").exists():
            raise RuntimeError("Notification outbox exists without persisted portfolio changes")
        return False
    run("git", "-c", "user.name=market-lens-agent", "-c",
        "user.email=market-lens-agent@users.noreply.github.com", "commit", "-m",
        f"Update Market Lens {kind} on Hetzner [skip render]")
    push_backup()
    return True


def deliver(kind: str) -> None:
    outbox = STATE / "outbox" / f"{kind}.json"
    if not outbox.exists():
        return
    writer_phase("NOTIFICATION_DELIVERY")
    script = "agent/send_buy_notifications.py" if kind == "scanner" else "agent/send_monitor_notifications.py"
    try:
        worker(["python", script, f"/app/runtime/outbox/{kind}.json"], timeout=120)
    except Exception as exc:
        raise RuntimeDeliveryFailure("Post-persistence trade alert delivery failed") from exc
    persist_notification_receipts()


def persist_notification_receipts() -> None:
    writer_phase("RECEIPT_CHECKPOINT")
    if archive_enabled():
        Checkpoints(REPO, STATE).save(output("git", "rev-parse", "HEAD"))
        return
    receipts = "agent_results/telegram_notifications.jsonl"
    run("git", "add", "--", receipts)
    if subprocess.run(("git", "diff", "--cached", "--quiet"), cwd=REPO).returncode:
        run("git", "-c", "user.name=market-lens-agent", "-c",
            "user.email=market-lens-agent@users.noreply.github.com", "commit", "-m",
            "Persist Market Lens Telegram receipts [skip render]")
        push_backup()


def execute(kind: str) -> None:
    writer_phase("PREFLIGHT")
    require_live_preflight()
    before = set((REPO / "agent_results/runtime").glob("market_lens_agent_*.json"))
    tracker_before = file_hash(TRACKER)
    heartbeat_path = STATE / "monitor/latest_status.json"
    heartbeat_before = (heartbeat_path.stat().st_mtime_ns, file_hash(heartbeat_path)) if heartbeat_path.exists() else None
    checkpoints = Checkpoints(REPO, STATE) if kind == "monitor" and archive_enabled() else None
    if checkpoints is not None:
        begin_monitor(checkpoints, output("git", "rev-parse", "HEAD"))
    script = "agent/market_lens_ui_agent.py" if kind == "scanner" else "agent/position_monitor.py"
    writer_phase("POSITION_EVALUATION" if kind == "monitor" else "SCANNER")
    worker(["python", script], timeout=1500 if kind == "scanner" else 840)
    if kind == "scanner":
        record = latest_scan_record(before)
        print(f"Scanner status={record['run_status']} cards={record['result_cards_read']}")
    else:
        # Filesystems can coalesce timestamps for rapid writes. Changed content
        # is still a new heartbeat; unchanged content and time are not evidence.
        if (not heartbeat_path.exists()
                or (heartbeat_path.stat().st_mtime_ns, file_hash(heartbeat_path)) == heartbeat_before):
            raise RuntimeError("Monitor did not write a fresh heartbeat")
        heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        if heartbeat.get("status") not in {"MONITOR_OK", "MONITOR_DEGRADED"}:
            raise RuntimeError("Monitor heartbeat did not confirm a successful evaluation")
        if heartbeat["status"] == "MONITOR_DEGRADED":
            report_runtime_alert("monitor", "MONITOR_DEGRADED")
        if file_hash(TRACKER) == tracker_before:
            if heartbeat.get("event_count"):
                raise RuntimeError("Monitor reported an event without a saved portfolio change")
            if checkpoints is not None:
                cancel_no_event_monitor(checkpoints, tracker_before)
            print("Monitor evaluated positions with no portfolio event; heartbeat kept local")
            return
        if heartbeat.get("event_count") and not (STATE / "outbox/monitor.json").is_file():
            raise RuntimeError("Monitor event has no notification outbox; delivery cannot be confirmed")
    worker(["python", "deploy/hetzner/postprocess.py"], timeout=180)
    if checkpoints is not None:
        seal_monitor(checkpoints)
    persisted = persist(kind)
    if kind == "monitor" and heartbeat.get("event_count") and not persisted:
        raise RuntimeError("Monitor changed the portfolio but no persisted commit was produced")
    if persisted:
        deliver(kind)
        if kind == "monitor" and heartbeat.get("event_count"):
            receipt = STATE / "monitor/latest_persisted.json"
            temporary = receipt.with_suffix(".tmp")
            temporary.write_text(json.dumps({
                "run_id": heartbeat.get("run_id"),
                "event_count": heartbeat["event_count"],
                "status": "PERSISTED_AND_DELIVERED",
            }), encoding="utf-8")
            temporary.replace(receipt)
    print(f"{kind} completed and persisted")


def coordination_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as target:
        json.dump(record, target, sort_keys=True)
        target.flush()
        os.fsync(target.fileno())
    temporary.replace(path)


def writer_phase(phase: str) -> None:
    if WRITER_OWNER is not None:
        # Coordination is deliberately outside archived monitor/outbox inputs.
        WRITER_OWNER["phase"] = phase
        coordination_record(STATE / "coordination/writer.json", WRITER_OWNER)


def acquire_lock(lock, deadline: float, *, wait=None) -> None:
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if wait is not None:
                deadline = min(deadline, wait(deadline))
            if time.monotonic() >= deadline:
                raise RuntimeError("Portfolio writer lock timed out") from None
            time.sleep(0.25)


@contextmanager
def writer_lock(timeout: float | None = None, *, priority: bool = False):
    global WRITER_OWNER
    STATE.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    budget = timeout if timeout is not None else (MONITOR_LOCK_WAIT_SECONDS if priority else SCANNER_LOCK_WAIT_SECONDS)
    deadline = started + budget
    wait_path = STATE / "coordination/monitor_wait.json"
    last_wait_status = None

    def wait(current_deadline):
        nonlocal last_wait_status
        phase = "UNKNOWN"
        try:
            owner = json.loads((STATE / "coordination/writer.json").read_text())
            phase = owner.get("phase", "UNKNOWN")
            owner_deadline = float(owner["deadline_monotonic"])
            # Never extend the original queue deadline or trust metadata to
            # grant ownership. The kernel flock alone grants access.
            current_deadline = min(current_deadline, owner_deadline)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        elapsed = time.monotonic() - started
        status = ("LOCK_DEADLINE_EXCEEDED" if time.monotonic() >= current_deadline else
                  "DEFERRED_LOCK" if elapsed >= SCANNER_LOCK_WAIT_SECONDS else "WAITING_FOR_WRITER")
        if status != last_wait_status:
            coordination_record(wait_path, {"pid": os.getpid(), "status": status,
                "wait_seconds": round(elapsed, 3), "owner_phase": phase,
                "deadline_monotonic": current_deadline})
            print(f"Monitor writer queue status={status} owner_phase={phase} wait_seconds={elapsed:.3f}", flush=True)
            last_wait_status = status
        return current_deadline
    # Reserve the next writer turn for an already-waiting monitor. This lock
    # never permits a second writer and never interrupts an atomic promotion.
    with (STATE / "monitor-priority.lock").open("a") as reservation:
        acquire_lock(reservation, deadline, wait=wait if priority else None)
        with (STATE / "writer.lock").open("a") as lock:
            acquire_lock(lock, deadline, wait=wait if priority else None)
            if not priority:
                fcntl.flock(reservation, fcntl.LOCK_UN)
            acquired = time.monotonic()
            print(f"Writer lock priority={priority} wait_seconds={acquired - started:.3f}", flush=True)
            WRITER_OWNER = {"pid": os.getpid(), "token": uuid.uuid4().hex, "phase": "ACQUIRED",
                "started_monotonic": acquired, "deadline_monotonic": acquired + WRITER_TRANSACTION_SECONDS}
            try:
                writer_phase("ACQUIRED")
                if priority:
                    coordination_record(wait_path, {"pid": os.getpid(), "status": "ACQUIRED",
                        "wait_seconds": round(acquired - started, 3)})
                yield
            finally:
                try:
                    (STATE / "coordination/writer.json").unlink(missing_ok=True)
                finally:
                    WRITER_OWNER = None
                    print(f"Writer lock held_seconds={time.monotonic() - acquired:.3f}", flush=True)
                    fcntl.flock(lock, fcntl.LOCK_UN)


def execute_staged_scanner() -> None:
    with writer_lock():
        writer_phase("STAGE_PREPARATION")
        require_live_preflight()
        if archive_enabled():
            # Reject capacity before creating another private stage, not after
            # applying its financial workbook to the live portfolio.
            require_headroom(STATE, 6 * TRACKER.stat().st_size + 128 * 1024**2)
        stage = prepare_stage(REPO, STATE, file_hash(TRACKER))
        previous = set((stage.results / "runtime").glob("market_lens_agent_*.json"))
    print(f"Scanner stage prepared: {stage.root.name}")
    try:
        worker(["python", "agent/market_lens_ui_agent.py"], timeout=1500, stage=stage)
        record = latest_scan_record(previous, results=stage.results)
        print(f"Staged scanner status={record['run_status']} cards={record['result_cards_read']}")
        # Dashboard/workbook postprocessing uses this same isolated snapshot.
        # It must not hold the live writer lock while reading historical JSONL.
        worker(["python", "deploy/hetzner/postprocess.py"], timeout=180, stage=stage)

        with writer_lock():
            writer_phase("PREFLIGHT")
            require_live_preflight()
            if file_hash(TRACKER) != stage.original_tracker_hash:
                raise ScannerSnapshotStale(
                    "Portfolio changed during scan; staged decisions were not applied. "
                    f"Stage retained at {stage.root}"
                )
            writer_phase("PROMOTION")
            checkpoints = Checkpoints(REPO, STATE) if archive_enabled() else None
            if checkpoints is not None:
                begin_scanner(checkpoints, stage, output("git", "rev-parse", "HEAD"))
            promote_stage(stage, REPO, STATE)
            if checkpoints is not None:
                seal_scanner(checkpoints)
            if persist("scanner"):
                deliver("scanner")
    except Exception:
        compact_failed_stage(stage)
        raise
    # The stage duplicates durably checkpointed data; failed evidence remains for diagnosis.
    if stage.root.resolve().is_relative_to((STATE / "scanner-staging").resolve()):
        shutil.rmtree(stage.root)
    print("scanner completed and persisted")


def main() -> None:
    if fcntl is None:
        raise RuntimeError("The runtime writer requires Linux flock")
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("scanner", "monitor"))
    args = parser.parse_args()
    if args.kind == "scanner":
        execute_staged_scanner()
    else:
        with writer_lock(priority=True):
            execute("monitor")


def cli() -> int:
    from app.runtime_health import record_attempt

    kind = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in {"scanner", "monitor"} else "scanner"
    started = datetime.now(timezone.utc).isoformat()

    def observed(status: str, code: str = "") -> None:
        try:
            record_attempt(STATE / "health", kind, status, started_at=started, failure_code=code)
        except OSError:
            print("Runtime attempt status could not be persisted", file=sys.stderr)

    observed("RUNNING")
    try:
        main()
    except Exception as exc:
        event = ("TRADE_ALERT_DELIVERY_FAILED" if isinstance(exc, RuntimeDeliveryFailure)
                 else "SCANNER_SNAPSHOT_STALE" if isinstance(exc, ScannerSnapshotStale)
                 else "RUN_FAILED")
        code = "CHECKPOINT_HEADROOM_INSUFFICIENT" if str(exc).startswith("Insufficient checkpoint headroom") else event
        observed("FAILED", code)
        report_runtime_alert(kind, event)
        print(f"Market Lens runtime failed: {exc}", file=sys.stderr)
        return 1
    observed("SUCCEEDED")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
