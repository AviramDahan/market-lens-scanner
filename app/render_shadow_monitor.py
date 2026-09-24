"""Bounded in-process shadow polling for Render.

The shadow monitor measures whether Render can detect live position events
without relying on an open browser or an external cron request. It never
dispatches workflows or mutates portfolio state.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any


Cycle = Callable[[], Awaitable[dict[str, Any]]]


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


class RenderShadowMonitor:
    """Own one bounded asyncio task and expose non-sensitive runtime evidence."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._state: dict[str, Any] = self._initial_state()

    @staticmethod
    def _initial_state() -> dict[str, Any]:
        return {
            "mode": "shadow",
            "side_effects_enabled": False,
            "enabled": False,
            "running": False,
            "status": "disabled",
            "started_at": "",
            "last_poll_at": "",
            "next_poll_at": "",
            "last_duration_ms": 0,
            "last_session_phase": "",
            "last_positions_checked": 0,
            "last_event_count": 0,
            "last_events": [],
            "event_journal": [],
            "last_warnings": {},
            "total_polls": 0,
            "total_events_detected": 0,
            "total_unique_events": 0,
            "consecutive_failures": 0,
            "last_error": "",
        }

    def config(self) -> dict[str, Any]:
        return {
            "enabled": _env_bool("MARKET_LENS_RENDER_SHADOW_MONITOR_ENABLED", False),
            "interval_seconds": _env_int(
                "MARKET_LENS_RENDER_SHADOW_MONITOR_INTERVAL_SECONDS", 60, 30, 900
            ),
            "startup_delay_seconds": _env_int(
                "MARKET_LENS_RENDER_SHADOW_MONITOR_STARTUP_DELAY_SECONDS", 20, 0, 300
            ),
            "timeout_seconds": _env_int(
                "MARKET_LENS_RENDER_SHADOW_MONITOR_TIMEOUT_SECONDS", 45, 5, 55
            ),
            "regular_session_only": _env_bool(
                "MARKET_LENS_RENDER_SHADOW_MONITOR_REGULAR_SESSION_ONLY", True
            ),
            "event_journal_limit": _env_int(
                "MARKET_LENS_RENDER_SHADOW_MONITOR_EVENT_JOURNAL_LIMIT", 100, 20, 500
            ),
        }

    def start(self, cycle: Cycle) -> bool:
        config = self.config()
        self._state.update(
            {
                "enabled": config["enabled"],
                "interval_seconds": config["interval_seconds"],
                "timeout_seconds": config["timeout_seconds"],
                "regular_session_only": config["regular_session_only"],
                "event_journal_limit": config["event_journal_limit"],
            }
        )
        if not config["enabled"]:
            self._state.update({"running": False, "status": "disabled"})
            return False
        if self._task and not self._task.done():
            return True
        self._state.update(
            {
                "running": True,
                "status": "starting",
                "started_at": _utc_now(),
                "last_error": "",
            }
        )
        self._task = asyncio.create_task(self._loop(cycle, config))
        return True

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._state.update({"running": False, "next_poll_at": ""})
        if self._state.get("enabled"):
            self._state["status"] = "stopped"

    async def run_once(self, cycle: Cycle, *, timeout_seconds: int | None = None) -> dict[str, Any]:
        timeout = timeout_seconds or int(self.config()["timeout_seconds"])
        started = time.monotonic()
        polled_at = _utc_now()
        try:
            result = await asyncio.wait_for(cycle(), timeout=timeout)
            if not isinstance(result, dict):
                raise TypeError("Shadow monitor cycle must return a dictionary.")
        except Exception as exc:
            self._state.update(
                {
                    "status": "error",
                    "last_poll_at": polled_at,
                    "last_duration_ms": round((time.monotonic() - started) * 1000),
                    "last_positions_checked": 0,
                    "last_event_count": 0,
                    "last_events": [],
                    "last_warnings": {},
                    "total_polls": int(self._state.get("total_polls") or 0) + 1,
                    "consecutive_failures": int(self._state.get("consecutive_failures") or 0) + 1,
                    "last_error": type(exc).__name__,
                }
            )
            return self.snapshot()

        events = result.get("events") if isinstance(result.get("events"), list) else []
        warnings = result.get("warnings") if isinstance(result.get("warnings"), dict) else {}
        unique_added = self._record_events(events, polled_at)
        self._state.update(
            {
                "status": str(result.get("status") or "ok"),
                "last_poll_at": polled_at,
                "last_duration_ms": round((time.monotonic() - started) * 1000),
                "last_session_phase": str(result.get("session_phase") or ""),
                "last_positions_checked": int(result.get("positions_checked") or 0),
                "last_event_count": len(events),
                "last_events": [dict(item) for item in events[:20] if isinstance(item, dict)],
                "last_warnings": {str(key): str(value)[:160] for key, value in warnings.items()},
                "total_polls": int(self._state.get("total_polls") or 0) + 1,
                "total_events_detected": int(self._state.get("total_events_detected") or 0)
                + len(events),
                "total_unique_events": int(self._state.get("total_unique_events") or 0)
                + unique_added,
                "consecutive_failures": 0,
                "last_error": "",
            }
        )
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        snapshot = dict(self._state)
        snapshot["last_events"] = [dict(item) for item in self._state.get("last_events", [])]
        snapshot["event_journal"] = [
            dict(item) for item in self._state.get("event_journal", [])
        ]
        snapshot["last_warnings"] = dict(self._state.get("last_warnings", {}))
        return snapshot

    def _record_events(self, events: list[Any], polled_at: str) -> int:
        journal = [dict(item) for item in self._state.get("event_journal", [])]
        by_id = {str(item.get("event_id") or ""): item for item in journal}
        unique_added = 0
        for raw_event in events:
            if not isinstance(raw_event, dict):
                continue
            event = dict(raw_event)
            event_id = str(event.get("event_id") or _shadow_event_id(event))
            existing = by_id.get(event_id)
            if existing is None:
                existing = {
                    **event,
                    "event_id": event_id,
                    "first_seen_at": polled_at,
                    "last_seen_at": polled_at,
                    "detection_count": 1,
                }
                journal.append(existing)
                by_id[event_id] = existing
                unique_added += 1
            else:
                existing.update(event)
                existing["event_id"] = event_id
                existing["last_seen_at"] = polled_at
                existing["detection_count"] = int(existing.get("detection_count") or 0) + 1

        limit = int(self.config()["event_journal_limit"])
        self._state["event_journal"] = journal[-limit:]
        return unique_added

    async def _loop(self, cycle: Cycle, config: dict[str, Any]) -> None:
        try:
            startup_delay = int(config["startup_delay_seconds"])
            if startup_delay:
                self._state["next_poll_at"] = _utc_after(startup_delay)
                await asyncio.sleep(startup_delay)
            while True:
                await self.run_once(cycle, timeout_seconds=int(config["timeout_seconds"]))
                interval = int(config["interval_seconds"])
                self._state["next_poll_at"] = _utc_after(interval)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        finally:
            self._state["running"] = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _utc_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _shadow_event_id(event: dict[str, Any]) -> str:
    identity = {
        "position_id": str(event.get("position_id") or ""),
        "ticker": str(event.get("ticker") or "").upper(),
        "event_type": str(event.get("event_type") or ""),
        "threshold": round(float(event.get("threshold") or 0), 4),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"shadow_{digest[:24]}"


render_shadow_monitor = RenderShadowMonitor()
