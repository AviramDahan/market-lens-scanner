"""Persist read-only parity evidence for the Render shadow position sensor.

The collector runs inside the existing position-monitor workflow. It never
dispatches workflows, sends notifications, or mutates the paper portfolio.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


DEFAULT_OUTPUT_DIR = Path("agent_results/monitor_parity")
DEFAULT_PENDING_HORIZON_SECONDS = 30 * 60


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def event_identity(payload: dict[str, Any], *, prefix: str) -> str:
    identity = {
        "position_id": str(payload.get("position_id") or payload.get("trade_id") or ""),
        "ticker": str(payload.get("ticker") or "").upper(),
        "event_type": str(payload.get("event_type") or payload.get("action") or ""),
        "threshold": round(float(payload.get("threshold") or payload.get("trigger_price") or 0), 4),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"{prefix}_{digest[:24]}"


def read_notification_outbox(path: Path | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if path is None or not path.is_file():
        return {}, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, []
    events = []
    for item in payload.get("events") or []:
        event = item.get("event") if isinstance(item, dict) else None
        if isinstance(event, dict):
            events.append(dict(event))
    return payload, events


def load_records(output_dir: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if not output_dir.exists():
        return records
    for path in sorted(output_dir.glob("render_shadow_parity_*.jsonl"))[-10:]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_id = str(record.get("event_id") or "")
            if event_id:
                records[event_id] = record
    return records


def shadow_matches_active(shadow: dict[str, Any], active: dict[str, Any]) -> bool:
    if str(shadow.get("ticker") or "").upper() != str(active.get("ticker") or "").upper():
        return False
    if str(shadow.get("event_type") or "") != str(active.get("action") or ""):
        return False
    shadow_position = str(shadow.get("position_id") or "")
    active_position = str(active.get("trade_id") or "")
    if shadow_position and active_position and shadow_position != active_position:
        return False
    shadow_threshold = float(shadow.get("threshold") or 0)
    active_threshold = float(active.get("trigger_price") or 0)
    tolerance = max(0.02, abs(shadow_threshold) * 0.001)
    return (
        not shadow_threshold
        or not active_threshold
        or abs(shadow_threshold - active_threshold) <= tolerance
    )


def trigger_matches_shadow(shadow: dict[str, Any], trigger: dict[str, str]) -> bool:
    return (
        bool(trigger.get("ticker"))
        and str(shadow.get("ticker") or "").upper() == trigger.get("ticker", "").upper()
        and str(shadow.get("event_type") or "") == trigger.get("event", "")
    )


def collect_parity(
    *,
    shadow_status: dict[str, Any],
    outbox_payload: dict[str, Any],
    active_events: list[dict[str, Any]],
    output_dir: Path,
    trigger: dict[str, str],
    now: datetime,
    pending_horizon_seconds: int = DEFAULT_PENDING_HORIZON_SECONDS,
) -> dict[str, Any]:
    if (
        shadow_status.get("mode") != "shadow"
        or shadow_status.get("side_effects_enabled") is not False
    ):
        raise ValueError("Unsafe or malformed Render shadow status payload")

    records = load_records(output_dir)
    before = json.dumps(records, sort_keys=True, separators=(",", ":"))
    shadow_events = [
        dict(item) for item in shadow_status.get("event_journal") or [] if isinstance(item, dict)
    ]

    for shadow in shadow_events:
        event_id = str(shadow.get("event_id") or event_identity(shadow, prefix="shadow"))
        if event_id not in records:
            first_seen = str(shadow.get("first_seen_at") or now.isoformat(timespec="seconds"))
            records[event_id] = {
                "schema_version": 1,
                "event_id": event_id,
                "classification": "SHADOW_ONLY_PENDING",
                "shadow": {
                    key: shadow.get(key)
                    for key in (
                        "ticker", "event_type", "position_id", "threshold", "live_price",
                        "live_high", "live_low", "observed_at", "first_seen_at", "reason",
                    )
                },
                "first_seen_at": first_seen,
                "active_trigger": None,
                "active_persistence": None,
            }
        record = records[event_id]
        if trigger_matches_shadow(shadow, trigger) and not record.get("active_trigger"):
            record["active_trigger"] = {
                "run_id": trigger.get("run_id", ""),
                "source": trigger.get("source", ""),
                "ticker": trigger.get("ticker", ""),
                "event": trigger.get("event", ""),
                "observed_at": now.isoformat(timespec="seconds"),
            }

    unmatched_active = []
    for active in active_events:
        matched_id = next(
            (
                event_id
                for event_id, record in records.items()
                if shadow_matches_active(record.get("shadow") or {}, active)
            ),
            "",
        )
        if not matched_id:
            unmatched_active.append(active)
            matched_id = event_identity(active, prefix="active")
            records.setdefault(
                matched_id,
                {
                    "schema_version": 1,
                    "event_id": matched_id,
                    "classification": "ACTIVE_ONLY",
                    "shadow": None,
                    "first_seen_at": str(
                        active.get("triggered_at") or now.isoformat(timespec="seconds")
                    ),
                    "active_trigger": None,
                    "active_persistence": None,
                },
            )
        record = records[matched_id]
        if not record.get("active_persistence"):
            persisted_at = str(outbox_payload.get("timestamp") or now.isoformat(timespec="seconds"))
            record["active_persistence"] = {
                "run_id": str(outbox_payload.get("run_id") or trigger.get("run_id") or ""),
                "ticker": str(active.get("ticker") or "").upper(),
                "action": str(active.get("action") or ""),
                "trade_id": str(active.get("trade_id") or ""),
                "triggered_at": str(active.get("triggered_at") or ""),
                "trigger_price": active.get("trigger_price"),
                "persisted_at": persisted_at,
            }
            first_seen_at = parse_timestamp(record.get("first_seen_at"))
            persisted = parse_timestamp(persisted_at)
            record["persistence_lag_seconds"] = (
                max(0, round((persisted - first_seen_at).total_seconds()))
                if first_seen_at and persisted
                else None
            )
        record["classification"] = "MATCHED" if record.get("shadow") else "ACTIVE_ONLY"

    for record in records.values():
        if record.get("classification") != "SHADOW_ONLY_PENDING":
            continue
        first_seen = parse_timestamp(record.get("first_seen_at"))
        if first_seen and (now - first_seen).total_seconds() >= pending_horizon_seconds:
            record["classification"] = "SHADOW_ONLY_EXPIRED"

    after = json.dumps(records, sort_keys=True, separators=(",", ":"))
    changed = before != after
    if changed:
        write_records(records, output_dir)
    classifications: dict[str, int] = {}
    for record in records.values():
        name = str(record.get("classification") or "UNKNOWN")
        classifications[name] = classifications.get(name, 0) + 1
    return {
        "changed": changed,
        "records": len(records),
        "shadow_events_seen": len(shadow_events),
        "active_events_seen": len(active_events),
        "unmatched_active_events": len(unmatched_active),
        "classifications": classifications,
    }


def write_records(records: dict[str, dict[str, Any]], output_dir: Path) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records.values():
        timestamp = parse_timestamp(record.get("first_seen_at")) or datetime.now(timezone.utc)
        grouped.setdefault(timestamp.date().isoformat(), []).append(record)
    output_dir.mkdir(parents=True, exist_ok=True)
    for date, items in grouped.items():
        path = output_dir / f"render_shadow_parity_{date}.jsonl"
        content = "\n".join(
            json.dumps(item, sort_keys=True, separators=(",", ":"))
            for item in sorted(items, key=lambda value: str(value.get("first_seen_at") or ""))
        )
        temporary = path.with_suffix(".tmp")
        temporary.write_text(content + "\n", encoding="utf-8")
        temporary.replace(path)


def fetch_shadow_status(url: str, timeout_seconds: float = 12.0) -> dict[str, Any]:
    response = httpx.get(url, timeout=timeout_seconds, follow_redirects=True)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Render shadow status must be a JSON object")
    return payload


def main() -> int:
    base_url = os.getenv("MARKET_LENS_PUBLIC_URL", "https://market-lens-scanner-fb63.onrender.com")
    status_url = os.getenv(
        "MARKET_LENS_SHADOW_STATUS_URL",
        f"{base_url.rstrip('/')}/agent/monitor-shadow-status",
    )
    outbox_raw = os.getenv("MARKET_LENS_MONITOR_NOTIFICATION_OUTBOX", "").strip()
    output_dir = Path(os.getenv("MARKET_LENS_MONITOR_PARITY_DIR", str(DEFAULT_OUTPUT_DIR)))
    trigger = {
        "run_id": os.getenv("MARKET_LENS_MONITOR_WORKFLOW_RUN_ID", ""),
        "ticker": os.getenv("MARKET_LENS_MONITOR_TRIGGER_TICKER", ""),
        "event": os.getenv("MARKET_LENS_MONITOR_TRIGGER_EVENT", ""),
        "source": os.getenv("MARKET_LENS_MONITOR_TRIGGER_SOURCE", ""),
    }
    try:
        shadow_status = fetch_shadow_status(status_url)
        outbox_payload, active_events = read_notification_outbox(
            Path(outbox_raw) if outbox_raw else None
        )
        result = collect_parity(
            shadow_status=shadow_status,
            outbox_payload=outbox_payload,
            active_events=active_events,
            output_dir=output_dir,
            trigger=trigger,
            now=datetime.now(timezone.utc),
            pending_horizon_seconds=max(
                300,
                int(os.getenv("MARKET_LENS_MONITOR_PARITY_PENDING_SECONDS", "1800")),
            ),
        )
    except Exception as exc:
        # Parity telemetry must never block the active portfolio monitor.
        print(f"::warning::Render shadow parity collection skipped ({type(exc).__name__}).")
        return 0
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
