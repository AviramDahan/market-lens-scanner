"""Minute tick for the existing New York paper-trading schedule."""
from __future__ import annotations

import subprocess
import sys
import os
import argparse
import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


NY = ZoneInfo("America/New_York")
WEEKDAY_SCANS = frozenset(
    "00:30 01:30 02:30 03:30 04:30 05:30 06:30 07:30 08:30 "
    "09:10 09:35 09:45 10:00 10:30 11:00 11:30 12:00 12:30 "
    "13:00 13:30 14:00 14:30 15:00 15:30 15:55 16:15 16:20 "
    "17:30 18:30 19:30 20:15 21:30 22:30 23:30".split()
)
SATURDAY_SCANS = frozenset({"11:00"})
SUNDAY_SCANS = frozenset({"18:30", "22:00"})
RUNNER = Path(__file__).with_name("run_runtime.py")
STATE = Path("/home/trader/market-lens-runtime-state/schedule")


def scanner_slots(instant: datetime, lookback_minutes: int = 30) -> list[tuple[str, datetime]]:
    """Only recent slots are eligible; a restart must not replay days of scans."""
    local = instant.astimezone(NY)
    result = []
    for day in (local.date() - timedelta(days=1), local.date()):
        times = WEEKDAY_SCANS if day.weekday() < 5 else SATURDAY_SCANS if day.weekday() == 5 else SUNDAY_SCANS
        for hhmm in sorted(times):
            slot = datetime.combine(day, time.fromisoformat(hhmm), tzinfo=NY)
            age = (instant.astimezone(timezone.utc) - slot.astimezone(timezone.utc)).total_seconds()
            if 0 <= age <= lookback_minutes * 60:
                # Wall-clock identity also prevents a second 01:30 during DST fallback.
                result.append((f"{day.isoformat()}_{hhmm}", slot))
    return sorted(result, key=lambda item: item[1])


def save_schedule(path: Path, record: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as target:
        json.dump(record, target, sort_keys=True)
        target.flush()
        os.fsync(target.fileno())
    temporary.replace(path)
    if hasattr(os, "O_DIRECTORY"):
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def dispatch_scanner(instant: datetime) -> None:
    # This lock serializes scheduler invocations, NOT the portfolio or monitor.
    # Keep RUNNING on interruption so the next tick can retry the same slot.
    import fcntl

    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "scanner.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        dispatch_scanner_locked(instant, STATE / "scanner.json")


def dispatch_scanner_locked(instant: datetime, path: Path) -> None:
    record = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"slots": {}}
    slots = record["slots"]
    candidates = [(key, slot) for key, slot in scanner_slots(instant)
                  if slots.get(key, {}).get("status") not in {"COMPLETE", "COALESCED"}
                  and slots.get(key, {}).get("attempts", 0) < 2]
    for key, previous in slots.items():
        if (previous.get("status") not in {"RUNNING", "FAILED"}
                or previous.get("attempts", 0) >= 2 or any(item[0] == key for item in candidates)):
            continue
        reference = datetime.fromisoformat(previous.get("finished_at") or previous["started_at"])
        age = (instant - reference).total_seconds()
        grace = 30 * 60 if previous.get("finished_at") else 75 * 60
        if 0 <= age <= grace:
            candidates.append((key, datetime.fromisoformat(previous["scheduled_at"])))
    if not candidates:
        return
    candidates.sort(key=lambda item: item[1])
    key, slot = candidates[-1]
    for older, _ in candidates[:-1]:
        slots[older] = {**slots.get(older, {}), "status": "COALESCED", "covered_by": key}
    slots[key] = {"status": "RUNNING", "attempts": slots.get(key, {}).get("attempts", 0) + 1,
                  "scheduled_at": slot.isoformat(), "started_at": instant.isoformat()}
    save_schedule(path, record)
    print(f"Scanner slot={key} delay_seconds={int((instant - slot).total_seconds())}", flush=True)
    try:
        # The systemd service bounds the complete cgroup at 3000 seconds; each
        # Docker worker has its own timeout and container-stop cleanup.
        subprocess.run([sys.executable, str(RUNNER), "scanner"], check=True)
    except Exception:
        slots[key]["status"] = "FAILED"
        slots[key]["finished_at"] = datetime.now(timezone.utc).isoformat()
        save_schedule(path, record)
        raise
    slots[key]["status"] = "COMPLETE"
    slots[key]["finished_at"] = datetime.now(timezone.utc).isoformat()
    save_schedule(path, record)


def due_jobs(instant: datetime) -> list[str]:
    local = instant.astimezone(NY)
    hhmm = local.strftime("%H:%M")
    jobs = []
    if local.weekday() < 5 and time(9, 35) <= local.time().replace(second=0, microsecond=0) <= time(16, 5):
        jobs.append("monitor")
    if (local.weekday() < 5 and hhmm in WEEKDAY_SCANS
            or local.weekday() == 5 and hhmm in SATURDAY_SCANS
            or local.weekday() == 6 and hhmm in SUNDAY_SCANS):
        jobs.append("scanner")
    return jobs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("monitor", "scanner"))
    args = parser.parse_args()
    if os.getenv("MARKET_LENS_HETZNER_WRITER_ENABLED") != "true":
        return
    instant = datetime.now().astimezone()
    if args.kind == "scanner":
        dispatch_scanner(instant)
        return
    jobs = due_jobs(instant)
    for job in jobs:
        if args.kind and job != args.kind:
            continue
        if job == "scanner":
            dispatch_scanner(instant)
        else:
            subprocess.run([sys.executable, str(RUNNER), job], check=True)


if __name__ == "__main__":
    main()
