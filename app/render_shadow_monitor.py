"""Bounded in-process shadow polling for Render.

The shadow monitor measures whether Render can detect live position events
without relying on an open browser or an external cron request. It never
dispatches workflows or mutates portfolio state.
"""

from __future__ import annotations

import asyncio
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
            "last_warnings": {},
            "total_polls": 0,
            "total_events_detected": 0,
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
        }

    def start(self, cycle: Cycle) -> bool:
        config = self.config()
        self._state.update(
            {
                "enabled": config["enabled"],
                "interval_seconds": config["interval_seconds"],
                "timeout_seconds": config["timeout_seconds"],
                "regular_session_only": config["regular_session_only"],
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
                "consecutive_failures": 0,
                "last_error": "",
            }
        )
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        snapshot = dict(self._state)
        snapshot["last_events"] = [dict(item) for item in self._state.get("last_events", [])]
        snapshot["last_warnings"] = dict(self._state.get("last_warnings", {}))
        return snapshot

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


render_shadow_monitor = RenderShadowMonitor()
