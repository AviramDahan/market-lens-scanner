import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main


def fixture_dashboard(health=None):
    return {
        "status": "ok", "summary": {"cash_ils": 10000, "starting_capital_ils": 10000},
        "open_positions": [], "latest_run": {"run_id": "scan-a"},
        "system_health": health or {"execution_sensor_status": "CURRENT"},
    }


def test_refresh_version_tracks_portfolio_and_snapshot_not_poll_time(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot.json"
    tracker = tmp_path / main.TRACKER_NAME
    snapshot.write_text("{}")
    tracker.write_bytes(b"fixture")
    monkeypatch.setattr(main, "DASHBOARD_SNAPSHOT_PATH", snapshot)
    monkeypatch.setattr(main, "AGENT_TRACKER_DIR", tmp_path)
    original = main.dashboard_refresh_version()
    assert main.dashboard_refresh_version() == original
    stat = tracker.stat()
    os.utime(tracker, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000))
    updated = main.dashboard_refresh_version()
    assert updated != original
    snapshot.write_text('{"run_id":"scan-b"}')
    assert main.dashboard_refresh_version() != updated


def test_missing_refresh_files_have_stable_version(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DASHBOARD_SNAPSHOT_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(main, "AGENT_TRACKER_DIR", tmp_path)
    assert main.dashboard_refresh_version() == "0:0:0:0"


def test_live_refresh_includes_new_health_without_quote_fetch_for_empty_portfolio(monkeypatch):
    dashboard = fixture_dashboard()
    monkeypatch.setattr(main, "current_agent_dashboard", lambda: dashboard)
    monkeypatch.setattr(main, "dashboard_refresh_version", lambda: "same-version")
    monkeypatch.setattr(main, "fetch_live_price", lambda _: pytest.fail("No open position needs a quote"))
    client = TestClient(main.app)
    first = client.get("/agent/live-prices").json()
    dashboard["system_health"] = {"execution_sensor_status": "DEGRADED", "latest_monitor_run_id": "new"}
    second = client.get("/agent/live-prices").json()
    assert first["system_health"]["execution_sensor_status"] == "CURRENT"
    assert second["system_health"]["execution_sensor_status"] == "DEGRADED"
    assert second["dashboard_refresh_version"] == "same-version"
    assert second["summary"]["cash_ils"] == 10000
    assert dashboard["open_positions"] == []


def test_compact_data_and_live_response_share_persisted_version(monkeypatch):
    monkeypatch.setattr(main, "current_agent_dashboard", fixture_dashboard)
    monkeypatch.setattr(main, "dashboard_refresh_version", lambda: "snapshot-v2")
    client = TestClient(main.app)
    assert client.get("/agent/data").json()["dashboard_refresh_version"] == "snapshot-v2"
    assert client.get("/agent/live-prices").json()["dashboard_refresh_version"] == "snapshot-v2"


def test_history_has_no_live_refresh_version(monkeypatch):
    monkeypatch.setattr(main, "cached_agent_dashboard", lambda date: fixture_dashboard())
    monkeypatch.setattr(main, "dashboard_refresh_version", lambda: pytest.fail("History is not live"))
    assert TestClient(main.app).get("/agent/data?date=2026-10-12").json()["dashboard_refresh_version"] == ""


def test_javascript_live_refresh_regressions():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the deterministic frontend regression suite")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, "--test", "tests/js/dashboard_live_refresh.mjs"], cwd=root,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
