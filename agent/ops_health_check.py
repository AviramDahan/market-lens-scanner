"""Read-only production checks with alerts routed to the operations chat."""
from __future__ import annotations

import argparse
import html
import json
import os
from dataclasses import dataclass
from datetime import datetime, time, timezone
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from app.telegram_notifications import TelegramSettings, send_telegram_message


NY = ZoneInfo("America/New_York")
DEFAULT_URL = "https://market-lens.2.28.100.77.sslip.io"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def fetch(url: str) -> tuple[bool, object]:
    try:
        request = Request(url, headers={"User-Agent": "market-lens-ops-health/1.0"})
        with urlopen(request, timeout=20) as response:
            if response.status != 200:
                return False, f"HTTP {response.status}"
            if url.endswith("/agent"):
                return True, response.read(100_000).decode("utf-8", errors="replace")
            return True, json.load(response)
    except Exception as exc:
        return False, exc.__class__.__name__


def minutes_old(value: object, now: datetime) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds() / 60)
    except (TypeError, ValueError):
        return None


def as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def as_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def evaluate(base_url: str, now: datetime, read=fetch) -> list[Check]:
    checks: list[Check] = []
    ok, health = read(f"{base_url}/health")
    checks.append(Check("Web health", ok and isinstance(health, dict) and health.get("status") == "ok", "reachable" if ok else str(health)))
    ok, page = read(f"{base_url}/agent")
    checks.append(Check("Agent page", ok and isinstance(page, str) and len(page) > 1000, "reachable" if ok else str(page)))
    ok, data = read(f"{base_url}/agent/data")
    if not ok or not isinstance(data, dict):
        checks.append(Check("Agent data", False, str(data) if not ok else "invalid response"))
        return checks

    checks.append(Check("Agent data", data.get("status") == "ok", str(data.get("status") or "unknown")))
    latest = as_dict(data.get("latest_run"))
    coverage = as_dict(latest.get("scan_coverage"))
    received = as_int(coverage.get("received"))
    requested = as_int(coverage.get("requested"))
    run_status = str(latest.get("run_status") or "unknown")
    scan_ok = run_status in {"COMPLETE", "PARTIAL_OK"} and received > 0 and (not requested or received / requested >= 0.95)
    checks.append(Check("Scanner result", scan_ok, f"{run_status}; {received}/{requested} cards"))

    local = now.astimezone(NY)
    regular = local.weekday() < 5 and time(9, 30) <= local.time() <= time(16, 10)
    if regular and local.time() >= time(10, 15):
        age = minutes_old(latest.get("timestamp"), now)
        checks.append(Check("Scanner freshness", age is not None and age <= 90, f"age={round(age) if age is not None else 'unknown'}m"))

    positions = data.get("open_positions")
    positions = positions if isinstance(positions, list) else []
    if regular and local.time() >= time(9, 55) and positions:
        monitor = as_dict(data.get("system_health"))
        age = minutes_old(monitor.get("latest_monitor_at"), now)
        status = str(monitor.get("latest_monitor_status") or "unknown")
        checked = as_int(monitor.get("latest_monitor_positions_checked"))
        failed = as_int(monitor.get("latest_monitor_positions_failed"))
        monitor_ok = age is not None and age <= 20 and status == "MONITOR_OK" and checked >= len(positions) and failed == 0
        checks.append(Check("Position monitor", monitor_ok, f"{status}; age={round(age) if age is not None else 'unknown'}m; checked={checked}/{len(positions)}; failed={failed}"))
    return checks


def send(text: str) -> bool:
    settings = TelegramSettings(
        bot_token=os.getenv("MARKET_LENS_TELEGRAM_BOT_TOKEN", "").strip(),
        chat_id=os.getenv("MARKET_LENS_TELEGRAM_OPS_CHAT_ID", "").strip(),
    )
    result = send_telegram_message(text, settings=settings)
    print(f"Operations Telegram delivery: {result.status}")
    return result.sent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-telegram", action="store_true")
    args = parser.parse_args()
    if args.test_telegram:
        return 0 if send("TEST ONLY | Market Lens operations alerts are connected. No trade was changed.") else 2

    base_url = os.getenv("MARKET_LENS_PUBLIC_URL", DEFAULT_URL).rstrip("/")
    checks = evaluate(base_url, datetime.now(timezone.utc))
    failed = [check for check in checks if not check.ok]
    for check in checks:
        print(f"[{'FAIL' if not check.ok else 'OK'}] {check.name}: {check.detail}")
    if not failed:
        return 0
    lines = ["Market Lens | SYSTEM ALERT", "Production health check failed:"]
    lines.extend(f"- {html.escape(check.name)}: {html.escape(check.detail)}" for check in failed)
    lines.append(f"Dashboard: {base_url}/agent")
    if not send("\n".join(lines)):
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
