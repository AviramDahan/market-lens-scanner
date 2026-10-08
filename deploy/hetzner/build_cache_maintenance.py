"""Bound the shared host's unused Docker build cache without touching images or volumes."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # Unit tests also run on Windows.
    fcntl = None

try:
    from .notify_ops import send_runtime_alert
except ImportError:
    from notify_ops import send_runtime_alert


STATE = Path("/home/trader/market-lens-runtime-state/maintenance")
ACTIVE_UNITS = ("market-lens-scanner.service", "market-lens.service")
TRIGGER_CACHE_GB = 30.0
TRIGGER_FREE_GB = 20.0
MIN_FREE_GB = 12.0
MAX_CACHE = "16gb"
RESERVED_CACHE = "12gb"
MIN_AGE = "168h"
SIZE_UNITS = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3,
              "TB": 1000**4, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3}


def parse_size(value: str) -> int:
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)\s*", value)
    if not match or match.group(2).upper() not in SIZE_UNITS:
        raise ValueError(f"Unrecognized Docker size: {value!r}")
    return int(float(match.group(1)) * SIZE_UNITS[match.group(2).upper()])


def cache_bytes() -> int:
    result = subprocess.run(
        ["docker", "system", "df", "--format", "{{json .}}"],
        check=True, capture_output=True, text=True, timeout=45,
    )
    for line in result.stdout.splitlines():
        row = json.loads(line)
        if row.get("Type") == "Build Cache":
            return parse_size(row["Size"])
    raise RuntimeError("Docker build-cache size is unavailable")


def busy_reason() -> str | None:
    for unit in ACTIVE_UNITS:
        result = subprocess.run(
            ["systemctl", "is-active", unit], capture_output=True, text=True, timeout=10,
        )
        if result.stdout.strip() in {"active", "activating", "reloading"}:
            return f"{unit} is running"
        if result.stdout.strip() != "inactive":
            return f"{unit} activity could not be confirmed"
    # The builder is shared with AI Trader. Never prune while either app builds.
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            args = (process / "cmdline").read_bytes().decode("utf-8", "replace").split("\0")
        except (OSError, PermissionError):
            continue
        executable = Path(args[0]).name if args else ""
        if executable in {"docker", "docker-compose", "buildx", "buildctl"} and "build" in args[1:]:
            return "a Docker build is running"
    return None


def free_bytes() -> int:
    return shutil.disk_usage("/").free


def prune_cache() -> None:
    # Do not use docker system prune: that also selects images, networks and containers.
    subprocess.run(
        ["docker", "buildx", "prune", "--filter", f"until={MIN_AGE}",
         "--max-used-space", MAX_CACHE, "--reserved-space", RESERVED_CACHE, "--force"],
        check=True, capture_output=True, text=True, timeout=600,
    )


def record_result(result: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    destination = STATE / "build-cache-last.json"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(destination)


def maintenance_once() -> dict:
    before_free = free_bytes()
    before_cache = cache_bytes()
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "free_bytes_before": before_free,
        "cache_bytes_before": before_cache,
    }
    busy = busy_reason()
    if busy:
        result.update(status="skipped_busy", reason=busy)
    elif before_cache < TRIGGER_CACHE_GB * 1000**3 and before_free >= TRIGGER_FREE_GB * 1024**3:
        result.update(status="skipped_below_threshold")
    else:
        prune_cache()
        result.update(status="pruned")
    result["free_bytes_after"] = free_bytes()
    result["cache_bytes_after"] = cache_bytes()
    record_result(result)
    if result["free_bytes_after"] < MIN_FREE_GB * 1024**3:
        send_runtime_alert("maintenance", "DISK_LOW")
    elif (result["status"] == "pruned"
          and result["free_bytes_after"] < TRIGGER_FREE_GB * 1024**3
          and result["free_bytes_after"] - before_free < 512 * 1024**2):
        send_runtime_alert("maintenance", "MAINTENANCE_INEFFECTIVE")
    return result


def main() -> int:
    if fcntl is None:
        raise RuntimeError("Build-cache maintenance requires Linux flock")
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "build-cache.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Build-cache maintenance already running")
            return 0
        try:
            result = maintenance_once()
        except Exception as exc:
            record_result({"checked_at": datetime.now(timezone.utc).isoformat(),
                           "status": "failed", "error_type": type(exc).__name__})
            send_runtime_alert("maintenance", "MAINTENANCE_FAILED")
            print(f"Build-cache maintenance failed: {type(exc).__name__}", file=sys.stderr)
            return 1
        print(json.dumps(result, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
