from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.telegram_notifications import send_telegram_message


NEW_YORK_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class HealthCheck:
    name: str
    ok: bool
    detail: str


def main() -> None:
    checks: list[HealthCheck] = []
    public_url = os.getenv("MARKET_LENS_PUBLIC_URL", "https://market-lens-scanner-fb63.onrender.com").rstrip("/")
    repo = os.getenv("GITHUB_REPOSITORY", "AviramDahan/market-lens-scanner")
    workflow = os.getenv("MARKET_LENS_HEALTH_SCAN_WORKFLOW", "market-lens-agent.yml")
    max_scan_age_minutes = env_int("MARKET_LENS_HEALTH_MAX_SCAN_AGE_MINUTES", 240)
    min_result_cards = env_int("MARKET_LENS_HEALTH_MIN_RESULT_CARDS", 120)
    max_runtime_seconds = env_int("MARKET_LENS_HEALTH_MAX_RUNTIME_SECONDS", 1260)
    max_tracker_bytes = env_int("MARKET_LENS_HEALTH_MAX_TRACKER_BYTES", 75_000_000)

    checks.extend(check_render_endpoints(public_url, max_scan_age_minutes, min_result_cards))
    checks.append(check_latest_agent_workflow(repo, workflow))
    checks.append(check_latest_runtime_metrics(max_runtime_seconds))
    checks.append(check_tracker_size(max_tracker_bytes))

    failed = [check for check in checks if not check.ok]
    should_notify_telegram = health_telegram_enabled()
    should_notify_success = env_bool("MARKET_LENS_HEALTH_NOTIFY_SUCCESS", False)
    if should_notify_telegram and (failed or should_notify_success):
        message = format_health_message(public_url=public_url, checks=checks)
        result = send_telegram_message(message)
        if not result.sent and result.status != "not_configured":
            print(f"Telegram health notification skipped: {result.reason}")

    for check in checks:
        status = "OK" if check.ok else "FAIL"
        print(f"[{status}] {check.name}: {check.detail}")

    if failed:
        raise SystemExit(1)


def check_render_endpoints(public_url: str, max_scan_age_minutes: int, min_result_cards: int) -> list[HealthCheck]:
    checks: list[HealthCheck] = []
    health_payload = fetch_json(f"{public_url}/health", timeout=20)
    checks.append(
        HealthCheck(
            "Render /health",
            bool(health_payload.get("ok") and health_payload.get("data", {}).get("status") == "ok"),
            health_payload.get("detail", "status ok"),
        )
    )

    agent_payload = fetch_text(f"{public_url}/agent", timeout=25)
    checks.append(
        HealthCheck(
            "Render /agent",
            bool(agent_payload.get("ok") and len(str(agent_payload.get("data") or "")) > 1000),
            agent_payload.get("detail", "dashboard html loaded"),
        )
    )

    data_payload = fetch_json(f"{public_url}/agent/data", timeout=30)
    data_ok = bool(data_payload.get("ok") and isinstance(data_payload.get("data"), dict))
    checks.append(
        HealthCheck(
            "Render /agent/data",
            data_ok,
            data_payload.get("detail", "dashboard data loaded"),
        )
    )
    if data_ok:
        checks.extend(check_dashboard_payload(data_payload["data"], max_scan_age_minutes, min_result_cards))
    return checks


def health_telegram_enabled() -> bool:
    return env_bool("MARKET_LENS_HEALTH_TELEGRAM_ENABLED", False)


def check_dashboard_payload(payload: dict[str, Any], max_scan_age_minutes: int, min_result_cards: int) -> list[HealthCheck]:
    checks: list[HealthCheck] = []
    latest_run = payload.get("latest_run") or {}
    summary_text = str(latest_run.get("summary_text") or "")
    run_status = parse_summary_value(summary_text, "Run status") or str(latest_run.get("run_status") or "")
    scan_status = parse_summary_value(summary_text, "Scan status") or str(latest_run.get("scan_status") or "")
    latest_ts = str(latest_run.get("timestamp") or payload.get("snapshot", {}).get("resolved_timestamp") or "")
    age_minutes = timestamp_age_minutes(latest_ts)
    result_cards = int_from_scan_status(scan_status) or len(latest_run.get("tickers") or [])

    checks.append(
        HealthCheck(
            "Latest scan status",
            "AUTH_FAILED" not in summary_text and "RUN_FAILED" not in summary_text and "completed:" in scan_status,
            f"run_status={run_status or 'unknown'}; scan_status={scan_status or 'unknown'}",
        )
    )
    checks.append(
        HealthCheck(
            "Latest scan freshness",
            age_minutes is not None and age_minutes <= max_scan_age_minutes,
            f"latest={latest_ts or 'unknown'}; age_minutes={round(age_minutes, 1) if age_minutes is not None else 'unknown'}",
        )
    )
    checks.append(
        HealthCheck(
            "Latest scan breadth",
            result_cards >= min_result_cards,
            f"result_cards={result_cards}; minimum={min_result_cards}",
        )
    )
    checks.append(
        HealthCheck(
            "Fake zero setup guard",
            not ("completed: 0 results" in scan_status and "AUTH_FAILED" not in summary_text),
            f"scan_status={scan_status or 'unknown'}",
        )
    )
    return checks


def check_latest_agent_workflow(repo: str, workflow: str) -> HealthCheck:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        return HealthCheck("GitHub Actions latest scan", False, "GITHUB_TOKEN is missing.")
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs?per_page=5"
    try:
        request = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "market-lens-health-check/1.0",
            },
        )
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return HealthCheck("GitHub Actions latest scan", False, f"GitHub API check failed: {exc.__class__.__name__}.")

    runs = payload.get("workflow_runs") or []
    if not runs:
        return HealthCheck("GitHub Actions latest scan", False, f"No runs found for {workflow}.")
    latest = runs[0]
    conclusion = str(latest.get("conclusion") or "")
    status = str(latest.get("status") or "")
    run_id = latest.get("database_id") or latest.get("id")
    ok = status in {"queued", "in_progress"} or conclusion in {"success", "skipped"}
    return HealthCheck(
        "GitHub Actions latest scan",
        ok,
        f"workflow={workflow}; run_id={run_id}; status={status}; conclusion={conclusion or 'none'}",
    )


def check_latest_runtime_metrics(max_runtime_seconds: int) -> HealthCheck:
    runtime_dir = ROOT / "agent_results" / "runtime"
    # Checkout gives many tracked files the same mtime, while the timestamped
    # filename remains a stable chronological key.
    files = sorted(runtime_dir.glob("market_lens_agent_*.json"), key=lambda path: path.name, reverse=True)
    if not files:
        return HealthCheck("Latest runtime metrics", False, "No runtime metrics file exists yet.")
    latest = files[0]
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except Exception as exc:
        return HealthCheck("Latest runtime metrics", False, f"Could not read {latest.name}: {exc.__class__.__name__}.")
    total_seconds = float(payload.get("total_seconds") or 0)
    result_cards = int(payload.get("result_cards_read") or 0)
    return HealthCheck(
        "Latest runtime metrics",
        total_seconds <= max_runtime_seconds and result_cards > 0,
        f"{latest.name}; total_seconds={total_seconds:.1f}; result_cards={result_cards}",
    )


def check_tracker_size(max_bytes: int) -> HealthCheck:
    tracker_path = ROOT / "agent_tracker" / "market_lens_agent_portfolio_budget_100k.xlsx"
    if not tracker_path.exists():
        return HealthCheck("Tracker repository size", False, f"Missing tracker: {tracker_path.name}.")
    size_bytes = tracker_path.stat().st_size
    return HealthCheck(
        "Tracker repository size",
        size_bytes < max_bytes,
        f"bytes={size_bytes}; maximum={max_bytes}",
    )


def format_health_message(*, public_url: str, checks: list[HealthCheck]) -> str:
    failed = [check for check in checks if not check.ok]
    title = "Market Lens Health Alert" if failed else "Market Lens Health Check OK"
    status_line = f"Status: {'FAIL' if failed else 'OK'}"
    time_line = f"Time: {datetime.now(tz=NEW_YORK_TZ).strftime('%Y-%m-%d %H:%M')} New York"
    lines = [f"<b>{title}</b>", status_line, time_line, f"Dashboard: {public_url}/agent", ""]
    for check in checks:
        icon = "FAIL" if not check.ok else "OK"
        lines.append(f"{icon}: {escape_line(check.name)} - {escape_line(check.detail)}")
    if failed:
        lines.extend(["", "Action needed: inspect the failed check before increasing scan size further."])
    return "\n".join(lines)


def fetch_json(url: str, timeout: int) -> dict[str, Any]:
    payload = fetch_text(url, timeout=timeout)
    if not payload.get("ok"):
        return payload
    try:
        payload["data"] = json.loads(str(payload.get("data") or ""))
    except Exception as exc:
        payload["ok"] = False
        payload["detail"] = f"JSON parse failed: {exc.__class__.__name__}."
    return payload


def fetch_text(url: str, timeout: int) -> dict[str, Any]:
    try:
        request = Request(url, headers={"User-Agent": "market-lens-health-check/1.0"})
        with urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 0) or response.getcode())
            data = response.read().decode("utf-8", errors="replace")
        return {"ok": 200 <= status < 300, "data": data, "detail": f"HTTP {status}"}
    except Exception as exc:
        return {"ok": False, "data": "", "detail": f"Request failed: {exc.__class__.__name__}."}


def parse_summary_value(summary_text: str, label: str) -> str:
    prefix = f"{label}:"
    for line in summary_text.splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def int_from_scan_status(scan_status: str) -> int:
    marker = "completed:"
    if marker not in scan_status:
        return 0
    tail = scan_status.split(marker, 1)[1]
    digits = "".join(char if char.isdigit() else " " for char in tail).split()
    return int(digits[0]) if digits else 0


def timestamp_age_minutes(value: str) -> float | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        now = datetime.now(timezone.utc)
        if parsed.tzinfo is not None:
            return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds() / 60)
        ages = [
            (now - parsed.replace(tzinfo=timezone.utc)).total_seconds() / 60,
            (now - parsed.replace(tzinfo=NEW_YORK_TZ).astimezone(timezone.utc)).total_seconds() / 60,
        ]
        non_negative = [age for age in ages if age >= 0]
        if non_negative:
            return min(non_negative)
        return max(ages)
    except Exception:
        return None


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def escape_line(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


if __name__ == "__main__":
    main()
