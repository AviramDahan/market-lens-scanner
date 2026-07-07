from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.monitor_trigger import github_actions_token

NEW_YORK_TZ = ZoneInfo("America/New_York")

DEFAULT_WEEKDAY_SCAN_TIMES = {
    "06:30",
    "07:30",
    "08:30",
    "09:10",
    "09:35",
    "09:45",
    "10:00",
    "10:30",
    "11:00",
    "11:30",
    "12:00",
    "12:30",
    "13:00",
    "13:30",
    "14:00",
    "14:30",
    "15:00",
    "15:30",
    "15:55",
    "16:15",
    "16:20",
    "18:30",
    "20:15",
    "22:30",
}
DEFAULT_SATURDAY_SCAN_TIMES = {"11:00"}
DEFAULT_SUNDAY_SCAN_TIMES = {"18:30", "22:00"}

_DISPATCHED_SCAN_KEYS: set[str] = set()


@dataclass(frozen=True)
class ScanScheduleDecision:
    should_run: bool
    local_time: str
    local_date: str
    local_weekday: int
    scan_key: str
    reason: str
    next_scan_at: str


def scan_trigger_configured() -> bool:
    return bool(github_actions_token())


def scan_schedule_decision(now: datetime | None = None, force: bool = False) -> ScanScheduleDecision:
    current = (now or datetime.now(tz=NEW_YORK_TZ)).astimezone(NEW_YORK_TZ)
    local_time = current.strftime("%H:%M")
    local_date = current.strftime("%Y-%m-%d")
    local_weekday = current.isoweekday()
    next_scan_at = next_scan_time(current).isoformat()

    if force:
        scan_key = f"{local_date}T{local_time}"
        return ScanScheduleDecision(
            should_run=True,
            local_time=local_time,
            local_date=local_date,
            local_weekday=local_weekday,
            scan_key=scan_key,
            reason="Force trigger requested.",
            next_scan_at=next_scan_at,
        )

    active_slot = current_scan_slot(current)
    if active_slot is not None:
        scan_key = active_slot.strftime("%Y-%m-%dT%H:%M")
        return ScanScheduleDecision(
            should_run=True,
            local_time=local_time,
            local_date=local_date,
            local_weekday=local_weekday,
            scan_key=scan_key,
            reason=f"Current New York time is inside the {active_slot.strftime('%H:%M')} scan dispatch window.",
            next_scan_at=next_scan_at,
        )

    scan_key = f"{local_date}T{local_time}"
    return ScanScheduleDecision(
        should_run=False,
        local_time=local_time,
        local_date=local_date,
        local_weekday=local_weekday,
        scan_key=scan_key,
        reason="Outside configured New York scan times; GitHub Action dispatch skipped.",
        next_scan_at=next_scan_at,
    )


def scan_times_for_weekday(weekday: int) -> set[str]:
    if 1 <= weekday <= 5:
        return configured_scan_times(
            "MARKET_LENS_AGENT_WEEKDAY_SCAN_TIMES",
            DEFAULT_WEEKDAY_SCAN_TIMES,
        )
    if weekday == 6:
        return configured_scan_times(
            "MARKET_LENS_AGENT_SATURDAY_SCAN_TIMES",
            DEFAULT_SATURDAY_SCAN_TIMES,
        )
    if weekday == 7:
        return configured_scan_times(
            "MARKET_LENS_AGENT_SUNDAY_SCAN_TIMES",
            DEFAULT_SUNDAY_SCAN_TIMES,
        )
    return set()


def configured_scan_times(env_name: str, defaults: set[str]) -> set[str]:
    raw_value = os.getenv(env_name, "").strip()
    if not raw_value:
        return defaults
    parsed = {value.strip() for value in raw_value.replace(";", ",").split(",") if value.strip()}
    valid = {value for value in parsed if is_scan_time_value(value)}
    return valid or defaults


def is_scan_time_value(value: str) -> bool:
    if len(value) != 5 or value[2] != ":":
        return False
    hour_text, minute_text = value.split(":", maxsplit=1)
    if not hour_text.isdigit() or not minute_text.isdigit():
        return False
    hour = int(hour_text)
    minute = int(minute_text)
    return 0 <= hour <= 23 and 0 <= minute <= 59


def current_scan_slot(now: datetime) -> datetime | None:
    current = now.astimezone(NEW_YORK_TZ)
    window_minutes = int(os.getenv("MARKET_LENS_AGENT_TRIGGER_WINDOW_MINUTES", "4"))
    midnight = datetime.combine(current.date(), datetime.min.time(), tzinfo=NEW_YORK_TZ)
    for value in sorted(scan_times_for_weekday(current.isoweekday())):
        hour, minute = [int(part) for part in value.split(":", maxsplit=1)]
        slot = midnight.replace(hour=hour, minute=minute)
        elapsed = (current - slot).total_seconds()
        if 0 <= elapsed <= window_minutes * 60:
            return slot
    return None


def next_scan_time(now: datetime | None = None) -> datetime:
    current = (now or datetime.now(tz=NEW_YORK_TZ)).astimezone(NEW_YORK_TZ)
    for day_offset in range(8):
        candidate_day = current.date() + timedelta(days=day_offset)
        midnight = datetime.combine(candidate_day, datetime.min.time(), tzinfo=NEW_YORK_TZ)
        times = sorted(scan_times_for_weekday(midnight.isoweekday()))
        for value in times:
            hour, minute = [int(part) for part in value.split(":", maxsplit=1)]
            candidate = midnight.replace(hour=hour, minute=minute)
            if candidate > current:
                return candidate
    return current + timedelta(days=1)


def mark_scan_dispatched(scan_key: str) -> None:
    _DISPATCHED_SCAN_KEYS.add(scan_key)


def scan_already_dispatched(scan_key: str) -> bool:
    return scan_key in _DISPATCHED_SCAN_KEYS


async def dispatch_agent_scan(source: str = "agent-server-scan-scheduler") -> dict[str, Any]:
    token = github_actions_token()
    if not token:
        raise RuntimeError("GitHub Actions trigger token is not configured.")

    repo = os.getenv("GITHUB_ACTIONS_REPOSITORY", "AviramDahan/market-lens-scanner")
    workflow = os.getenv("GITHUB_AGENT_WORKFLOW", "market-lens-agent.yml")
    ref = os.getenv("GITHUB_ACTIONS_REF", "main")
    api_url = os.getenv(
        "GITHUB_AGENT_DISPATCH_URL",
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
    )
    payload = {
        "ref": ref,
        "inputs": {
            "force": "true",
        },
    }
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(api_url, headers=headers, json=payload)
    if response.status_code >= 400:
        raise RuntimeError(f"GitHub agent dispatch failed with status {response.status_code}.")
    return {
        "repo": repo,
        "workflow": workflow,
        "ref": ref,
        "source": source,
        "github_status": response.status_code,
    }
