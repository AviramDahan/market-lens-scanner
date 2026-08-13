from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from agent import health_check


def test_int_from_scan_status_reads_completed_results() -> None:
    assert health_check.int_from_scan_status("completed: 147 results") == 147
    assert health_check.int_from_scan_status("failed") == 0


def test_dashboard_payload_flags_fake_zero_scan() -> None:
    payload = {
        "latest_run": {
            "timestamp": datetime.now(health_check.NEW_YORK_TZ).isoformat(timespec="seconds"),
            "summary_text": "Run status: OK\nScan status: completed: 0 results\n",
        }
    }

    checks = health_check.check_dashboard_payload(payload, max_scan_age_minutes=10, min_result_cards=1)

    by_name = {check.name: check for check in checks}
    assert by_name["Fake zero setup guard"].ok is False
    assert by_name["Latest scan breadth"].ok is False


def test_latest_runtime_metrics_passes_for_recent_nonzero_file(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / "agent_results" / "runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "market_lens_agent_20260813_120000.json").write_text(
        json.dumps({"total_seconds": 420.0, "result_cards_read": 150}),
        encoding="utf-8",
    )
    monkeypatch.setattr(health_check, "ROOT", tmp_path)

    result = health_check.check_latest_runtime_metrics(max_runtime_seconds=1260)

    assert result.ok is True
    assert "result_cards=150" in result.detail


def test_latest_runtime_metrics_fails_when_scan_runtime_is_too_high(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / "agent_results" / "runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "market_lens_agent_20260813_120000.json").write_text(
        json.dumps({"total_seconds": 1300.0, "result_cards_read": 150}),
        encoding="utf-8",
    )
    monkeypatch.setattr(health_check, "ROOT", tmp_path)

    result = health_check.check_latest_runtime_metrics(max_runtime_seconds=1260)

    assert result.ok is False
    assert "total_seconds=1300.0" in result.detail


def test_health_message_marks_failures() -> None:
    message = health_check.format_health_message(
        public_url="https://example.test",
        checks=[
            health_check.HealthCheck("Render /health", True, "HTTP 200"),
            health_check.HealthCheck("Latest scan breadth", False, "result_cards=80"),
        ],
    )

    assert "Market Lens Health Alert" in message
    assert "Status: FAIL" in message
    assert "Latest scan breadth" in message
