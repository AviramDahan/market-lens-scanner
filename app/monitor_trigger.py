from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class LiveMonitorEvent:
    ticker: str
    event_type: str
    threshold: float
    live_price: float
    reason: str


_GLOBAL_TRIGGER_AT = 0.0
_EVENT_TRIGGER_AT: dict[str, float] = {}


def detect_live_monitor_event(position: dict[str, Any], live_price: float) -> LiveMonitorEvent | None:
    ticker = str(position.get("ticker") or "").upper()
    if not ticker or live_price <= 0:
        return None

    stop = to_float(position.get("stop_loss"))
    target_1 = to_float(position.get("target_1"))
    target_2 = to_float(position.get("target_2"))

    if stop > 0 and live_price <= stop:
        return LiveMonitorEvent(
            ticker=ticker,
            event_type="EXIT_STOP",
            threshold=stop,
            live_price=live_price,
            reason=f"{ticker} live price {live_price:.2f} touched stop loss {stop:.2f}.",
        )
    if target_2 > 0 and live_price >= target_2:
        return LiveMonitorEvent(
            ticker=ticker,
            event_type="TAKE_PROFIT",
            threshold=target_2,
            live_price=live_price,
            reason=f"{ticker} live price {live_price:.2f} touched target 2 {target_2:.2f}.",
        )
    if target_1 > 0 and live_price >= target_1:
        return LiveMonitorEvent(
            ticker=ticker,
            event_type="TAKE_PARTIAL_PROFIT",
            threshold=target_1,
            live_price=live_price,
            reason=f"{ticker} live price {live_price:.2f} touched target 1 {target_1:.2f}.",
        )
    return None


def monitor_trigger_configured() -> bool:
    return bool(github_actions_token())


def github_actions_token() -> str:
    return os.getenv("GITHUB_ACTIONS_TRIGGER_TOKEN") or os.getenv("MARKET_LENS_GITHUB_ACTIONS_TOKEN") or ""


def rate_limit_reason(event: LiveMonitorEvent) -> str | None:
    now = time.monotonic()
    global_limit = int(os.getenv("MARKET_LENS_MONITOR_TRIGGER_GLOBAL_COOLDOWN_SECONDS", "60"))
    event_limit = int(os.getenv("MARKET_LENS_MONITOR_TRIGGER_EVENT_COOLDOWN_SECONDS", "300"))

    if now - _GLOBAL_TRIGGER_AT < global_limit:
        return f"Global monitor trigger cooldown active ({global_limit}s)."

    key = event_key(event)
    last_event = _EVENT_TRIGGER_AT.get(key, 0.0)
    if now - last_event < event_limit:
        return f"Ticker/event monitor trigger cooldown active ({event_limit}s)."
    return None


def mark_trigger_sent(event: LiveMonitorEvent) -> None:
    global _GLOBAL_TRIGGER_AT
    now = time.monotonic()
    _GLOBAL_TRIGGER_AT = now
    _EVENT_TRIGGER_AT[event_key(event)] = now


def event_key(event: LiveMonitorEvent) -> str:
    return f"{event.ticker}:{event.event_type}"


async def dispatch_position_monitor(event: LiveMonitorEvent) -> dict[str, Any]:
    token = github_actions_token()
    if not token:
        raise RuntimeError("GitHub Actions trigger token is not configured.")

    repo = os.getenv("GITHUB_ACTIONS_REPOSITORY", "AviramDahan/market-lens-scanner")
    workflow = os.getenv("GITHUB_POSITION_MONITOR_WORKFLOW", "market-lens-position-monitor.yml")
    ref = os.getenv("GITHUB_ACTIONS_REF", "main")
    api_url = os.getenv(
        "GITHUB_ACTIONS_DISPATCH_URL",
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
    )
    payload = {
        "ref": ref,
        "inputs": {
            "force": "true",
            "ticker": event.ticker,
            "event": event.event_type,
            "source": "agent-ui-live-price",
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
        raise RuntimeError(f"GitHub dispatch failed with status {response.status_code}.")
    mark_trigger_sent(event)
    return {
        "repo": repo,
        "workflow": workflow,
        "ref": ref,
        "github_status": response.status_code,
    }


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default
