"""Single-writer host runner for the paper portfolio. Disabled until cutover."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

if __package__:
    from .notify_ops import send_runtime_alert
else:
    from notify_ops import send_runtime_alert

try:
    import fcntl
except ImportError:  # Tests also run on Windows; the deployed host is Linux.
    fcntl = None


REPO = Path("/home/trader/market-lens-runtime")
STATE = Path("/home/trader/market-lens-runtime-state")
COMPOSE = REPO / "deploy/hetzner/compose.runtime.yaml"
TRACKER = REPO / "agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx"
GENERATED = ("agent_tracker", "agent_results")
CODE_PATHS = ("app", "agent", "pyproject.toml", "config.yaml")
SUCCESS_STATUSES = {"COMPLETE", "PARTIAL_OK"}


class RuntimeDeliveryFailure(RuntimeError):
    """A persisted portfolio event could not reach the trade Telegram group."""


def report_runtime_alert(kind: str, event: str) -> None:
    try:
        status = send_runtime_alert(kind, event)
    except Exception:
        status = "failed"
    print(f"Operations alert: {status}", file=sys.stderr)


def run(*args: str, cwd: Path = REPO, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True, timeout=timeout)


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
    changed_code = output("git", "diff", "--name-only", deployed, "HEAD", "--", *CODE_PATHS)
    if changed_code:
        raise RuntimeError(f"Code changed since deployed image: {changed_code}")


def worker(command: list[str], *, timeout: int) -> None:
    container_name = f"market-lens-worker-{uuid.uuid4().hex[:12]}"
    args = [
        "sudo", "-n", "docker", "compose", "-f", str(COMPOSE), "run", "--rm",
        "--name", container_name, "worker", *command,
    ]
    try:
        run(*args, timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(("sudo", "-n", "docker", "stop", "--time", "15", container_name),
                       check=False, timeout=35)
        raise RuntimeError(f"Worker timed out after {timeout} seconds") from None


def latest_scan_record(previous: set[Path]) -> dict:
    runtime = REPO / "agent_results/runtime"
    created = set(runtime.glob("market_lens_agent_*.json")) - previous
    if len(created) != 1:
        raise RuntimeError(f"Expected one new scanner record, got {len(created)}")
    record = json.loads(created.pop().read_text(encoding="utf-8"))
    if record.get("run_status") not in SUCCESS_STATUSES or not record.get("result_cards_read"):
        raise RuntimeError("Scanner did not produce a successful, nonempty scan")
    return record


def persist(kind: str) -> bool:
    run("git", "add", "--", *GENERATED)
    if subprocess.run(("git", "diff", "--cached", "--quiet"), cwd=REPO).returncode == 0:
        if (STATE / "outbox" / f"{kind}.json").exists():
            raise RuntimeError("Notification outbox exists without persisted portfolio changes")
        return False
    run("git", "-c", "user.name=market-lens-agent", "-c",
        "user.email=market-lens-agent@users.noreply.github.com", "commit", "-m",
        f"Update Market Lens {kind} on Hetzner [skip render]")
    run("git", "push", "origin", "HEAD:main", timeout=300)
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
        run("git", "push", "origin", "HEAD:main", timeout=300)


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
    worker(["python", "deploy/hetzner/postprocess.py"], timeout=180)
    if persist(kind):
        deliver(kind)
    print(f"{kind} completed and persisted")


def main() -> None:
    if fcntl is None:
        raise RuntimeError("The runtime writer requires Linux flock")
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("scanner", "monitor"))
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "writer.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another Market Lens portfolio writer is active") from None
        execute(args.kind)


def cli() -> int:
    try:
        main()
    except Exception as exc:
        kind = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in {"scanner", "monitor"} else "scanner"
        event = "TRADE_ALERT_DELIVERY_FAILED" if isinstance(exc, RuntimeDeliveryFailure) else "RUN_FAILED"
        report_runtime_alert(kind, event)
        print(f"Market Lens runtime failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
