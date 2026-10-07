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
from contextlib import contextmanager
from pathlib import Path

if __package__:
    from .notify_ops import send_runtime_alert
    from .staged_scanner import ScannerStage, compact_failed_stage, prepare_stage, promote_stage
else:
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
    return subprocess.check_output(args, cwd=cwd, text=True, timeout=60).strip()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_live_preflight() -> None:
    if os.getenv("MARKET_LENS_HETZNER_WRITER_ENABLED") != "true":
        raise RuntimeError("Hetzner writer is disabled")
    if os.getenv("MARKET_LENS_OLD_WRITERS_DISABLED") != "true":
        raise RuntimeError("Legacy scanner/monitor writers have not been disabled")
    if not TRACKER.is_file():
        raise RuntimeError("Paper tracker is missing")
    if any((STATE / "outbox" / f"{kind}.json").exists() for kind in ("scanner", "monitor")):
        raise RuntimeError("An undelivered notification outbox needs reconciliation")
    if output("git", "status", "--porcelain"):
        raise RuntimeError("Runtime checkout has unpersisted changes")
    run("git", "fetch", "origin", "main", timeout=300)
    run("git", "merge", "--ff-only", "origin/main", timeout=60)
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
    script = "agent/send_buy_notifications.py" if kind == "scanner" else "agent/send_monitor_notifications.py"
    try:
        worker(["python", script, f"/app/runtime/outbox/{kind}.json"], timeout=120)
    except Exception as exc:
        raise RuntimeDeliveryFailure("Post-persistence trade alert delivery failed") from exc
    receipts = "agent_results/telegram_notifications.jsonl"
    run("git", "add", "--", receipts)
    if subprocess.run(("git", "diff", "--cached", "--quiet"), cwd=REPO).returncode:
        run("git", "-c", "user.name=market-lens-agent", "-c",
            "user.email=market-lens-agent@users.noreply.github.com", "commit", "-m",
            "Persist Market Lens Telegram receipts [skip render]")
        push_backup()


def execute(kind: str) -> None:
    require_live_preflight()
    before = set((REPO / "agent_results/runtime").glob("market_lens_agent_*.json"))
    tracker_before = file_hash(TRACKER)
    heartbeat_path = STATE / "monitor/latest_status.json"
    heartbeat_before = heartbeat_path.stat().st_mtime_ns if heartbeat_path.exists() else None
    script = "agent/market_lens_ui_agent.py" if kind == "scanner" else "agent/position_monitor.py"
    worker(["python", script], timeout=1500 if kind == "scanner" else 840)
    if kind == "scanner":
        record = latest_scan_record(before)
        print(f"Scanner status={record['run_status']} cards={record['result_cards_read']}")
    else:
        if not heartbeat_path.exists() or heartbeat_path.stat().st_mtime_ns == heartbeat_before:
            raise RuntimeError("Monitor did not write a fresh heartbeat")
        heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        if heartbeat.get("status") not in {"MONITOR_OK", "MONITOR_DEGRADED"}:
            raise RuntimeError("Monitor heartbeat did not confirm a successful evaluation")
        if heartbeat["status"] == "MONITOR_DEGRADED":
            report_runtime_alert("monitor", "MONITOR_DEGRADED")
        if file_hash(TRACKER) == tracker_before:
            if heartbeat.get("event_count"):
                raise RuntimeError("Monitor reported an event without a saved portfolio change")
            print("Monitor evaluated positions with no portfolio event; heartbeat kept local")
            return
        if heartbeat.get("event_count") and not (STATE / "outbox/monitor.json").is_file():
            raise RuntimeError("Monitor event has no notification outbox; delivery cannot be confirmed")
    worker(["python", "deploy/hetzner/postprocess.py"], timeout=180)
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


@contextmanager
def writer_lock(timeout: float = 60):
    STATE.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with (STATE / "writer.lock").open("w") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Portfolio writer lock timed out") from None
                time.sleep(0.25)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def execute_staged_scanner() -> None:
    with writer_lock():
        require_live_preflight()
        stage = prepare_stage(REPO, STATE, file_hash(TRACKER))
        previous = set((stage.results / "runtime").glob("market_lens_agent_*.json"))
    print(f"Scanner stage prepared: {stage.root.name}")
    try:
        worker(["python", "agent/market_lens_ui_agent.py"], timeout=1500, stage=stage)
        record = latest_scan_record(previous, results=stage.results)
        print(f"Staged scanner status={record['run_status']} cards={record['result_cards_read']}")

        with writer_lock():
            require_live_preflight()
            if file_hash(TRACKER) != stage.original_tracker_hash:
                raise ScannerSnapshotStale(
                    "Portfolio changed during scan; staged decisions were not applied. "
                    f"Stage retained at {stage.root}"
                )
            promote_stage(stage, REPO, STATE)
            worker(["python", "deploy/hetzner/postprocess.py"], timeout=180)
            if persist("scanner"):
                deliver("scanner")
    except Exception:
        compact_failed_stage(stage)
        raise
    # The staged copy duplicates data already persisted in Git; failed stages remain for diagnosis.
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
        with writer_lock():
            execute("monitor")


def cli() -> int:
    try:
        main()
    except Exception as exc:
        kind = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in {"scanner", "monitor"} else "scanner"
        event = ("TRADE_ALERT_DELIVERY_FAILED" if isinstance(exc, RuntimeDeliveryFailure)
                 else "SCANNER_SNAPSHOT_STALE" if isinstance(exc, ScannerSnapshotStale)
                 else "RUN_FAILED")
        report_runtime_alert(kind, event)
        print(f"Market Lens runtime failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
