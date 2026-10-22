"""Execution attempts are separate from successful financial snapshots."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def utc(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def record_attempt(root: Path, component: str, status: str, *, started_at: str,
                   failure_code: str = "", now: datetime | None = None) -> None:
    if component not in {"scanner", "monitor"} or status not in {"RUNNING", "SUCCEEDED", "FAILED"}:
        raise ValueError("Invalid runtime attempt")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{component}.json"
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    record = {"schema_version": 1, "component": component, "status": status,
              "started_at": started_at, "updated_at": (now or datetime.now(timezone.utc)).isoformat(),
              "failure_code": failure_code}
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(record, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    if hasattr(os, "O_DIRECTORY"):
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def read_status(path: Path) -> dict:
    try:
        if path.stat().st_size > 128 * 1024:
            raise ValueError("Runtime status too large")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Invalid runtime status")
        return value
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {"status": "UNREADABLE"}


def latest_expected_scan(now: datetime) -> datetime:
    from app.scan_trigger import (DEFAULT_WEEKDAY_SCAN_TIMES, DEFAULT_SATURDAY_SCAN_TIMES,
                                  DEFAULT_SUNDAY_SCAN_TIMES, NEW_YORK_TZ)
    local = now.astimezone(NEW_YORK_TZ)
    slots = []
    for offset in range(3):
        day = local.date() - timedelta(days=offset)
        times = (DEFAULT_WEEKDAY_SCAN_TIMES if day.weekday() < 5 else
                 DEFAULT_SATURDAY_SCAN_TIMES if day.weekday() == 5 else DEFAULT_SUNDAY_SCAN_TIMES)
        for hhmm in times:
            slot = datetime.fromisoformat(f"{day.isoformat()}T{hhmm}").replace(tzinfo=NEW_YORK_TZ)
            # Normal scans complete well within 20 minutes; a live attempt is
            # separately allowed its existing 50-minute systemd deadline.
            if slot.astimezone(timezone.utc) <= now - timedelta(minutes=20):
                slots.append(slot.astimezone(timezone.utc))
    return max(slots)


def merge_runtime_health(dashboard: dict, *, now: datetime | None = None) -> None:
    if os.getenv("MARKET_LENS_HOST_MONITOR_ENABLED", "false").lower() != "true":
        return
    observed = now or datetime.now(timezone.utc)
    observed = observed.replace(tzinfo=timezone.utc) if observed.tzinfo is None else observed.astimezone(timezone.utc)
    root = Path(os.getenv("MARKET_LENS_HOST_STATUS_DIR", "/app/runtime/health"))
    health = dashboard.setdefault("system_health", {})
    previous_notes = list(health.get("notes") or [])
    notes = [n for n in previous_notes if not str(n).startswith("Host runtime:")]
    components = {}
    for component in ("scanner", "monitor"):
        source = read_status(root / f"{component}.json")
        status = source.get("status", "NOT_OBSERVED")
        if status not in {"RUNNING", "SUCCEEDED", "FAILED", "NOT_OBSERVED", "UNREADABLE"}:
            status = "UNREADABLE"
        stamp = utc(source.get("started_at"))
        if status == "RUNNING" and (stamp is None or (observed - stamp).total_seconds() > 3000):
            status = "STALE_RUNNING"
        if component == "scanner" and status not in {"FAILED", "RUNNING", "STALE_RUNNING", "UNREADABLE"}:
            last_success = utc(health.get("latest_scan_at"))
            if last_success is None or last_success < latest_expected_scan(observed):
                status = "MISSED_SCHEDULE"
        code = str(source.get("failure_code") or "")
        components[component] = {"status": status, "failure_code": code,
                                 "started_at": source.get("started_at"), "updated_at": source.get("updated_at")}
        if status in {"FAILED", "STALE_RUNNING", "MISSED_SCHEDULE", "UNREADABLE"}:
            notes.append(f"Host runtime: {component.upper()} {status}" + (f" ({code})." if code else "."))
    health["runtime_components"] = components
    health["notes"] = notes
    if notes and health.get("status", "ok") == "ok":
        health["status"] = "attention"
    elif not notes and previous_notes and health.get("status") == "attention":
        health["status"] = "ok"

