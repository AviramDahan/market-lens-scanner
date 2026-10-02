"""In-process evidence for the active TP/SL sensor and GitHub dispatch path.

This state is intentionally separate from the Render shadow price poller and
from the persisted position-monitor heartbeat. It proves that the externally
scheduled ``/agent/monitor-live`` endpoint is actually being called; it does
not claim that a dispatched portfolio mutation has already been persisted.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from threading import Lock
from typing import Any


Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


class ExecutionMonitorStatus:
    """Record non-sensitive runtime evidence for the side-effecting path."""

    def __init__(self, clock: Clock = _utc_now) -> None:
        self._clock = clock
        self._lock = Lock()
        self._state = self._initial_state()

    @staticmethod
    def _initial_state() -> dict[str, Any]:
        return {
            "mode": "active_sensor",
            "side_effects_enabled": True,
            "last_check_at": "",
            "last_status": "never_called",
            "last_positions_expected": 0,
            "last_positions_checked": 0,
            "last_event_count": 0,
            "last_warning_count": 0,
            "last_reason": "",
            "total_checks": 0,
            "consecutive_failures": 0,
            "last_error": "",
            "last_dispatch_attempt_at": "",
            "last_dispatch_succeeded_at": "",
            "last_dispatch_failed_at": "",
            "last_dispatch_ticker": "",
            "last_dispatch_event": "",
        }

    def reset(self) -> None:
        with self._lock:
            self._state = self._initial_state()

    def record_check(
        self,
        *,
        status: str,
        positions_expected: int = 0,
        positions_checked: int = 0,
        event_count: int = 0,
        warning_count: int = 0,
        reason: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        now = _timestamp(self._clock())
        failed = bool(error) or status in {"error", "dashboard_unavailable"}
        with self._lock:
            self._state.update(
                {
                    "last_check_at": now,
                    "last_status": str(status or "unknown")[:80],
                    "last_positions_expected": max(0, int(positions_expected or 0)),
                    "last_positions_checked": max(0, int(positions_checked or 0)),
                    "last_event_count": max(0, int(event_count or 0)),
                    "last_warning_count": max(0, int(warning_count or 0)),
                    "last_reason": str(reason or "")[:240],
                    "total_checks": int(self._state.get("total_checks") or 0) + 1,
                    "consecutive_failures": (
                        int(self._state.get("consecutive_failures") or 0) + 1 if failed else 0
                    ),
                    "last_error": type(error).__name__ if isinstance(error, Exception) else str(error or "")[:80],
                }
            )
            return dict(self._state)

    def record_dispatch(
        self,
        *,
        ticker: str,
        event_type: str,
        succeeded: bool,
        error: str = "",
    ) -> dict[str, Any]:
        now = _timestamp(self._clock())
        with self._lock:
            self._state.update(
                {
                    "last_dispatch_attempt_at": now,
                    "last_dispatch_ticker": str(ticker or "").upper()[:16],
                    "last_dispatch_event": str(event_type or "")[:80],
                }
            )
            if succeeded:
                self._state["last_dispatch_succeeded_at"] = now
                self._state["last_error"] = ""
            else:
                self._state["last_dispatch_failed_at"] = now
                self._state["last_error"] = str(error or "dispatch_failed")[:80]
                self._state["consecutive_failures"] = int(
                    self._state.get("consecutive_failures") or 0
                ) + 1
            return dict(self._state)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)


execution_monitor_status = ExecutionMonitorStatus()
