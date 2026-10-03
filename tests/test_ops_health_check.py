from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent import ops_health_check


def payload(now: datetime) -> dict:
    return {
        "status": "ok",
        "latest_run": {
            "run_status": "PARTIAL_OK",
            "timestamp": now.isoformat(),
            "scan_coverage": {"requested": 130, "received": 128},
        },
        "open_positions": [{"ticker": "CME"}],
        "system_health": {
            "latest_monitor_at": now.isoformat(),
            "latest_monitor_status": "MONITOR_OK",
            "latest_monitor_positions_checked": 1,
            "latest_monitor_positions_failed": 0,
        },
    }


def reader(data: dict):
    def read(url: str):
        if url.endswith("/health"):
            return True, {"status": "ok"}
        if url.endswith("/agent"):
            return True, "x" * 2000
        return True, data
    return read


def test_partial_scan_with_adequate_coverage_is_healthy():
    now = datetime(2026, 10, 5, 14, 30, tzinfo=timezone.utc)
    checks = ops_health_check.evaluate("https://example.test", now, reader(payload(now)))
    assert all(check.ok for check in checks)


def test_low_scan_coverage_is_an_alert():
    now = datetime(2026, 10, 5, 14, 30, tzinfo=timezone.utc)
    data = payload(now)
    data["latest_run"]["scan_coverage"]["received"] = 90
    checks = ops_health_check.evaluate("https://example.test", now, reader(data))
    assert not next(check for check in checks if check.name == "Scanner result").ok


def test_monitor_staleness_alerts_only_during_regular_session():
    regular = datetime(2026, 10, 5, 16, 0, tzinfo=timezone.utc)
    data = payload(regular)
    data["system_health"]["latest_monitor_at"] = "2026-10-05T14:00:00Z"
    checks = ops_health_check.evaluate("https://example.test", regular, reader(data))
    assert not next(check for check in checks if check.name == "Position monitor").ok

    weekend = datetime(2026, 10, 4, 16, 0, tzinfo=timezone.utc)
    checks = ops_health_check.evaluate("https://example.test", weekend, reader(data))
    assert not any(check.name == "Position monitor" for check in checks)


def test_no_positions_do_not_require_monitor():
    now = datetime(2026, 10, 5, 16, 0, tzinfo=timezone.utc)
    data = payload(now)
    data["open_positions"] = []
    checks = ops_health_check.evaluate("https://example.test", now, reader(data))
    assert not any(check.name == "Position monitor" for check in checks)


def test_failed_endpoint_is_reported_without_crashing():
    now = datetime(2026, 10, 5, 16, 0, tzinfo=timezone.utc)

    def read(url: str):
        if url.endswith("/agent/data"):
            return False, "TimeoutError"
        return reader(payload(now))(url)

    checks = ops_health_check.evaluate("https://example.test", now, read)
    assert not next(check for check in checks if check.name == "Agent data").ok


def test_test_message_uses_only_ops_group(monkeypatch):
    sent = {}

    def fake_send(text, *, settings):
        sent["chat_id"] = settings.chat_id
        sent["text"] = text
        return type("Result", (), {"sent": True, "status": "sent"})()

    monkeypatch.setenv("MARKET_LENS_TELEGRAM_OPS_CHAT_ID", "-100123")
    monkeypatch.setenv("MARKET_LENS_TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("MARKET_LENS_TELEGRAM_CHAT_ID", "-100999")
    monkeypatch.setattr(ops_health_check, "send_telegram_message", fake_send)
    assert ops_health_check.send("TEST ONLY")
    assert sent == {"chat_id": "-100123", "text": "TEST ONLY"}


def test_ops_workflow_does_not_dispatch_trading_jobs():
    path = Path(".github/workflows/market-lens-upgrade-health-check.yml")
    text = path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    assert workflow["permissions"] == {"contents": "read"}
    assert "MARKET_LENS_TELEGRAM_OPS_CHAT_ID" in text
    assert "secrets.MARKET_LENS_TELEGRAM_CHAT_ID" not in text
    assert "agent.ops_health_check" in text
    assert "gh workflow run" not in text
    assert "agent/market_lens_ui_agent.py" not in text
    assert "agent/position_monitor.py" not in text
