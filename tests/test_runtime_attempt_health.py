import copy
import json
import sys
from datetime import datetime, timedelta, timezone

import pytest

from app.runtime_health import merge_runtime_health, record_attempt
from deploy.hetzner import run_runtime

NOW = datetime(2026, 10, 22, 13, 1, tzinfo=timezone.utc)


@pytest.fixture
def health_root(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_LENS_HOST_MONITOR_ENABLED", "true")
    monkeypatch.setenv("MARKET_LENS_HOST_STATUS_DIR", str(tmp_path))
    return tmp_path


def dashboard(stamp=None):
    return {"latest_run": {"run_status": "COMPLETE", "run_id": "old-scan"},
            "system_health": {"status": "ok", "notes": [],
                              "latest_scan_at": stamp or (NOW - timedelta(days=1)).isoformat()},
            "open_positions": [], "summary": {"cash": 100532.90}}


def test_failed_attempt_overrides_false_ok_even_without_positions(health_root):
    record_attempt(health_root, "scanner", "FAILED", started_at=NOW.isoformat(),
                   failure_code="CHECKPOINT_HEADROOM_INSUFFICIENT", now=NOW)
    data = dashboard(NOW.isoformat())
    before = copy.deepcopy(data)
    merge_runtime_health(data, now=NOW)
    assert data["system_health"]["status"] == "attention"
    assert data["system_health"]["runtime_components"]["scanner"]["status"] == "FAILED"
    assert "CHECKPOINT_HEADROOM" in data["system_health"]["notes"][0]
    for key in ("latest_run", "summary", "open_positions"):
        assert data[key] == before[key]


def test_missing_scheduled_scan_is_detected_before_36_hours(health_root):
    data = dashboard()
    merge_runtime_health(data, now=NOW)
    assert data["system_health"]["runtime_components"]["scanner"]["status"] == "MISSED_SCHEDULE"


def test_fresh_success_has_no_warning(health_root):
    record_attempt(health_root, "scanner", "SUCCEEDED", started_at=NOW.isoformat(), now=NOW)
    data = dashboard(NOW.isoformat())
    merge_runtime_health(data, now=NOW)
    assert data["system_health"]["status"] == "ok"
    assert data["system_health"]["notes"] == []


def test_running_attempt_has_bounded_grace(health_root):
    record_attempt(health_root, "scanner", "RUNNING", started_at=NOW.isoformat(), now=NOW)
    data = dashboard()
    merge_runtime_health(data, now=NOW + timedelta(minutes=49))
    assert data["system_health"]["runtime_components"]["scanner"]["status"] == "RUNNING"
    merge_runtime_health(data, now=NOW + timedelta(minutes=51))
    assert data["system_health"]["runtime_components"]["scanner"]["status"] == "STALE_RUNNING"


@pytest.mark.parametrize("contents", ['secret-token-invalid-json', '[1,2]', '{"status":"BAD"}'])
def test_corrupt_status_is_visible_without_exposing_contents(health_root, contents):
    (health_root / "scanner.json").write_text(contents)
    data = dashboard()
    merge_runtime_health(data, now=NOW)
    assert data["system_health"]["runtime_components"]["scanner"]["status"] == "UNREADABLE"
    assert "secret-token" not in json.dumps(data)


def test_status_updates_do_not_rewrite_old_snapshots_or_portfolio(health_root):
    record_attempt(health_root, "scanner", "FAILED", started_at=NOW.isoformat(), now=NOW)
    data = dashboard()
    merge_runtime_health(data, now=NOW)
    record_attempt(health_root, "scanner", "SUCCEEDED", started_at=NOW.isoformat(), now=NOW)
    data["system_health"]["latest_scan_at"] = NOW.isoformat()
    merge_runtime_health(data, now=NOW)
    assert data["system_health"]["notes"] == []
    assert data["latest_run"]["run_id"] == "old-scan"
    assert not list(health_root.glob('*.tmp'))


@pytest.mark.parametrize("failure", [None, RuntimeError("Insufficient checkpoint headroom; no success recorded"),
                                    ValueError("secret-value-should-never-be-persisted")])
def test_runtime_cli_writes_real_outcome_with_safe_diagnostics(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(run_runtime, "STATE", tmp_path)
    monkeypatch.setattr(sys, "argv", ["run_runtime.py", "scanner"])
    monkeypatch.setattr(run_runtime, "report_runtime_alert", lambda *_: None)
    def execute():
        current = json.loads((tmp_path / 'health/scanner.json').read_text())
        assert current["status"] == "RUNNING"
        if failure:
            raise failure
    monkeypatch.setattr(run_runtime, "main", execute)
    assert run_runtime.cli() == (1 if failure else 0)
    text = (tmp_path / 'health/scanner.json').read_text()
    assert json.loads(text)["status"] == ("FAILED" if failure else "SUCCEEDED")
    assert 'secret-value' not in text


def test_non_host_deployments_are_unchanged(monkeypatch):
    monkeypatch.delenv("MARKET_LENS_HOST_MONITOR_ENABLED", raising=False)
    data = dashboard()
    original = copy.deepcopy(data)
    merge_runtime_health(data, now=NOW)
    assert data == original
