import copy
import json
import os
import shutil
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main
from app.agent_dashboard import SCANNER_HEALTH_STALE_NOTE, refresh_system_health_ages


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


def test_health_ages_advance_without_mutating_snapshot_or_other_data():
    health = {
        "generated_at": "2026-10-13T22:38:53Z", "status": "ok", "notes": [],
        "latest_scan_at": "2026-10-13T22:35:05", "latest_scan_age_minutes": 3,
        "latest_monitor_at": "2026-10-13T20:05:32Z", "latest_monitor_age_minutes": 153,
        "latest_monitor_status": "MONITOR_OK", "latest_monitor_event_count": 0,
        "executor_persistence_status": "NOT_TRIGGERED",
    }
    original = copy.deepcopy(health)
    now = datetime(2026, 10, 13, 23, 23, 30, tzinfo=timezone.utc)
    first = refresh_system_health_ages(health, now=now)
    later = refresh_system_health_ages(health, now=now + timedelta(minutes=10))
    assert first["latest_scan_age_minutes"] == 48
    assert first["latest_monitor_age_minutes"] == 197
    assert later["latest_scan_age_minutes"] == 58
    assert first["generated_at"] == health["generated_at"]
    assert first["ages_evaluated_at"] == now.isoformat()
    assert health == original
    for key in ("latest_monitor_status", "latest_monitor_event_count", "executor_persistence_status"):
        assert first[key] == health[key]


@pytest.mark.parametrize("stamp", [
    "2026-10-13T22:35:05Z", "2026-10-13T22:35:05", "2026-10-14T01:35:05+03:00",
    datetime(2026, 10, 13, 22, 35, 5, tzinfo=timezone.utc),
])
def test_health_age_normalizes_utc_and_offset_timestamps(stamp):
    health = {"latest_scan_at": stamp}
    assert refresh_system_health_ages(
        health, now=datetime(2026, 10, 14, 2, 23, 30, tzinfo=timezone(timedelta(hours=3))),
    )["latest_scan_age_minutes"] == 48


@pytest.mark.parametrize("stamp", [None, "", "invalid", 0])
def test_missing_or_invalid_timestamp_has_unknown_age_not_fake_zero(stamp):
    health = {"latest_scan_at": stamp, "latest_scan_age_minutes": 0}
    refreshed = refresh_system_health_ages(health, now=datetime(2026, 10, 14))
    assert refreshed["latest_scan_age_minutes"] is None
    assert refreshed["latest_monitor_age_minutes"] is None


def test_future_timestamp_age_is_not_negative():
    health = {"latest_scan_at": "2026-10-14T01:00:00Z"}
    assert refresh_system_health_ages(
        health, now=datetime(2026, 10, 14, tzinfo=timezone.utc),
    )["latest_scan_age_minutes"] == 0


def test_null_notes_and_existing_failure_status_are_safe():
    health = {"notes": None, "status": "degraded", "latest_scan_at": "2026-10-14T00:00:00Z"}
    refreshed = refresh_system_health_ages(health, now=datetime(2026, 10, 14))
    assert refreshed["notes"] == []
    assert refreshed["status"] == "degraded"
    assert health["notes"] is None


@pytest.mark.parametrize("stamp", [None, "", "invalid", 0])
def test_unknown_scan_timestamp_cannot_clear_existing_stale_warning(stamp):
    health = {"status": "attention", "notes": [SCANNER_HEALTH_STALE_NOTE], "latest_scan_at": stamp}
    refreshed = refresh_system_health_ages(health, now=datetime(2026, 10, 14))
    assert refreshed["latest_scan_age_minutes"] is None
    assert refreshed["notes"] == [SCANNER_HEALTH_STALE_NOTE]
    assert refreshed["status"] == "attention"


@pytest.mark.parametrize("stamp", ["2026-10-14T00:00:00Z", "2026-10-12T00:00:00Z"])
def test_scanner_warning_refresh_cannot_clear_independent_failure_status(stamp):
    health = {"status": "degraded", "notes": [SCANNER_HEALTH_STALE_NOTE], "latest_scan_at": stamp}
    assert refresh_system_health_ages(health, now=datetime(2026, 10, 14))["status"] == "degraded"


def test_scanner_staleness_note_refresh_preserves_other_health_warnings():
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    health = {"latest_scan_at": "2026-10-12T11:59:00Z", "status": "attention",
              "notes": ["Host position monitor: DEGRADED"]}
    stale = refresh_system_health_ages(health, now=now)
    assert stale["notes"] == ["Host position monitor: DEGRADED", SCANNER_HEALTH_STALE_NOTE]
    assert stale["status"] == "attention"
    assert refresh_system_health_ages(stale, now=now)["notes"] == stale["notes"]
    fresh = refresh_system_health_ages(
        {**stale, "latest_scan_at": now.isoformat()}, now=now,
    )
    assert fresh["notes"] == ["Host position monitor: DEGRADED"]
    assert fresh["status"] == "attention"
    recovered = refresh_system_health_ages(
        {"latest_scan_at": now.isoformat(), "status": "attention", "notes": [SCANNER_HEALTH_STALE_NOTE]},
        now=now,
    )
    assert recovered["notes"] == []
    assert recovered["status"] == "ok"


@pytest.mark.parametrize("use_snapshot", [True, False])
def test_current_endpoints_refresh_ages_but_not_portfolio_or_history(tmp_path, monkeypatch, use_snapshot):
    dashboard = fixture_dashboard({
        "status": "ok", "notes": [], "generated_at": "2026-10-13T22:38:53Z",
        "latest_scan_at": "2026-10-13T22:35:05", "latest_scan_age_minutes": 3,
        "latest_monitor_at": "2026-10-13T20:05:32Z", "latest_monitor_age_minutes": 153,
    })
    original = copy.deepcopy(dashboard)
    snapshot = tmp_path / "snapshot.json"
    if use_snapshot:
        snapshot.write_text(json.dumps(dashboard), encoding="utf-8")
    monkeypatch.setattr(main, "DASHBOARD_SNAPSHOT_PATH", snapshot)
    monkeypatch.setattr(main, "sync_dashboard_snapshot_if_enabled", lambda _: {})
    monkeypatch.setattr(main, "sync_dashboard_snapshot_assets_if_enabled", lambda *_: {})
    monkeypatch.setattr(main, "enrich_agent_dashboard_snapshot", lambda data: data)
    monkeypatch.setattr(main, "cached_agent_dashboard", lambda *_: dashboard)
    monkeypatch.setattr(main, "merge_host_monitor_health", lambda _: True)
    monkeypatch.setattr(main, "dashboard_refresh_version", lambda: "same-version")
    monkeypatch.setattr(main, "fetch_live_price", lambda _: pytest.fail("No price fetch needed"))
    now = datetime(2026, 10, 13, 23, 23, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(main, "refresh_system_health_ages", lambda health: refresh_system_health_ages(health, now=now))
    client = TestClient(main.app)
    for route in ("/agent/data", "/agent/live-prices"):
        payload = client.get(route).json()
        assert payload["system_health"]["latest_scan_age_minutes"] == 48
        assert payload["system_health"]["latest_monitor_age_minutes"] == 197
        for key, value in dashboard["summary"].items():
            assert payload["summary"][key] == value
        assert payload["open_positions"] == []
        assert payload["dashboard_refresh_version"] == "same-version"
    now += timedelta(minutes=10)
    later = client.get("/agent/live-prices").json()
    assert later["system_health"]["latest_scan_age_minutes"] == 58
    assert later["dashboard_refresh_version"] == "same-version"
    history = client.get("/agent/data?date=2026-10-13").json()
    assert history["system_health"]["latest_scan_age_minutes"] == 3
    assert history["dashboard_refresh_version"] == ""
    assert dashboard == original
    if use_snapshot:
        assert json.loads(snapshot.read_text(encoding="utf-8")) == original


def test_javascript_live_refresh_regressions():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the deterministic frontend regression suite")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, "--test", "tests/js/dashboard_live_refresh.mjs"], cwd=root,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
