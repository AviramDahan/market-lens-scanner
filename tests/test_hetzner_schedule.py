from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import yaml

from deploy.hetzner import schedule
from deploy.hetzner.schedule import due_jobs


ROOT = Path(__file__).resolve().parents[1]


def at_utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def test_regular_session_monitor_precedes_scanner():
    # 2026-10-05 09:45 New York (EDT).
    assert due_jobs(at_utc("2026-10-05T13:45:00")) == ["monitor", "scanner"]


def test_separate_schedule_services_dispatch_only_their_job(monkeypatch):
    calls = []
    monkeypatch.setenv("MARKET_LENS_HETZNER_WRITER_ENABLED", "true")
    monkeypatch.setattr(schedule, "due_jobs", lambda _now: ["monitor", "scanner"])
    monkeypatch.setattr(schedule, "dispatch_scanner", lambda _now: calls.append(("scanner", True)))
    monkeypatch.setattr(schedule, "subprocess", SimpleNamespace(
        run=lambda args, check: calls.append((args[-1], check))
    ))
    monkeypatch.setattr(schedule.sys, "argv", ["schedule.py", "--kind", "monitor"])
    schedule.main()
    assert calls == [("monitor", True)]
    calls.clear()
    monkeypatch.setattr(schedule.sys, "argv", ["schedule.py", "--kind", "scanner"])
    schedule.main()
    assert calls == [("scanner", True)]


def test_scanner_catches_slot_missed_by_long_previous_run(tmp_path, monkeypatch):
    import json
    path = tmp_path / "schedule.json"
    path.write_text(json.dumps({"slots": {"2026-10-19_16:15": {"status": "COMPLETE", "attempts": 1}}}))
    calls = []
    monkeypatch.setattr(schedule.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    schedule.dispatch_scanner_locked(at_utc("2026-10-19T20:23:00"), path)
    record = json.loads(path.read_text())
    assert record["slots"]["2026-10-19_16:20"]["status"] == "COMPLETE"
    schedule.dispatch_scanner_locked(at_utc("2026-10-19T20:24:00"), path)
    assert len(calls) == 1
    assert calls[0][1] == {"check": True}


def test_scanner_retry_is_bounded_and_crash_can_recover(tmp_path, monkeypatch):
    import json
    import subprocess
    import pytest
    path = tmp_path / "schedule.json"
    path.write_text(json.dumps({"slots": {"2026-10-19_16:20": {
        "status": "RUNNING", "attempts": 1, "started_at": "2026-10-19T20:20:00+00:00",
        "scheduled_at": "2026-10-19T16:20:00-04:00"}}}))
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(schedule.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        schedule.dispatch_scanner_locked(at_utc("2026-10-19T20:24:00"), path)
    schedule.dispatch_scanner_locked(at_utc("2026-10-19T20:25:00"), path)
    assert len(calls) == 1
    assert json.loads(path.read_text())["slots"]["2026-10-19_16:20"]["status"] == "FAILED"


def test_scheduler_coalesces_backlog_but_does_not_replay_stale_slots(tmp_path, monkeypatch):
    import json
    calls = []
    monkeypatch.setattr(schedule.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    path = tmp_path / "schedule.json"
    schedule.dispatch_scanner_locked(at_utc("2026-10-19T20:23:00"), path)
    slots = json.loads(path.read_text())["slots"]
    assert slots["2026-10-19_15:55"]["status"] == "COALESCED"
    assert slots["2026-10-19_16:15"]["status"] == "COALESCED"
    assert len(calls) == 1
    assert schedule.scanner_slots(at_utc("2026-10-19T21:10:00")) == []


def test_long_run_failure_remains_retryable_after_original_slot_expires(tmp_path, monkeypatch):
    import json
    path = tmp_path / "schedule.json"
    path.write_text(json.dumps({"slots": {"2026-10-19_16:20": {
        "status": "FAILED", "attempts": 1, "started_at": "2026-10-19T20:20:00+00:00",
        "finished_at": "2026-10-19T20:50:00+00:00", "scheduled_at": "2026-10-19T16:20:00-04:00"}}}))
    calls = []
    monkeypatch.setattr(schedule.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    schedule.dispatch_scanner_locked(at_utc("2026-10-19T20:51:00"), path)
    assert len(calls) == 1
    assert json.loads(path.read_text())["slots"]["2026-10-19_16:20"]["attempts"] == 2


def test_monitor_boundaries_and_after_hours_scans():
    assert due_jobs(at_utc("2026-10-05T13:34:00")) == []
    assert due_jobs(at_utc("2026-10-05T13:35:00")) == ["monitor", "scanner"]
    assert due_jobs(at_utc("2026-10-05T20:05:00")) == ["monitor"]
    assert due_jobs(at_utc("2026-10-05T20:15:00")) == ["scanner"]


def test_weekend_scans_and_standard_time():
    assert due_jobs(at_utc("2026-10-03T15:00:00")) == ["scanner"]
    assert due_jobs(at_utc("2026-10-04T22:30:00")) == ["scanner"]
    # After DST ends, 09:35 EST moves to 14:35 UTC.
    assert due_jobs(at_utc("2026-11-02T14:35:00")) == ["monitor", "scanner"]


def test_runtime_env_matches_active_workflow_thresholds():
    defaults = {}
    for line in (ROOT / "deploy/hetzner/runtime.defaults.env").read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            defaults[key] = value
    workflow_steps = []
    for name, job in (("market-lens-agent.yml", "run-agent"),
                      ("market-lens-position-monitor.yml", "monitor-positions")):
        workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
        workflow_steps.extend(workflow["jobs"][job]["steps"])
    for step in workflow_steps:
        if step.get("name") not in {"Run Market Lens UI agent", "Run position monitor"}:
            continue
        for key, value in step.get("env", {}).items():
            if key in defaults:
                assert defaults[key] == str(value).lower() if isinstance(value, bool) else defaults[key] == str(value)


def test_runtime_compose_does_not_publish_worker_or_auto_start_it():
    config = yaml.safe_load((ROOT / "deploy/hetzner/compose.runtime.yaml").read_text())
    web = config["services"]["web"]
    worker = config["services"]["worker"]
    assert web["ports"] == ["127.0.0.1:18082:8000"]
    assert web["build"]["dockerfile"] == "deploy/hetzner/Dockerfile.web.runtime"
    assert "agent_results" in (ROOT / "deploy/hetzner/Dockerfile.web.runtime.dockerignore").read_text()
    assert all(mount.endswith(":ro") for mount in web["volumes"])
    assert "worker" in worker["profiles"]
    assert "ports" not in worker
    assert worker["restart"] == "no"
    assert worker["shm_size"] == "512m"
    assert "/tmp:size=1024m" in worker["tmpfs"]
    assert not any(volume.split(":")[1] == "/tmp" for volume in worker["volumes"])
    assert config["services"]["web"]["build"]["args"]["SOURCE_REVISION"]
    assert worker["build"]["args"]["SOURCE_REVISION"]


def test_shared_caddy_preview_preserves_existing_route_and_blocks_writes():
    config = (ROOT / "deploy/hetzner/Caddyfile.shared-preview").read_text()
    assert "{$PUBLIC_API_HOST}" in config
    assert "reverse_proxy api:8000" in config
    assert "market-lens.2.28.100.77.sslip.io" in config
    assert "respond @writes" in config
    assert "reverse_proxy market-lens-runtime-web:8000" in config


def test_shared_caddy_production_preserves_existing_route_and_allows_app_writes():
    config = (ROOT / "deploy/hetzner/Caddyfile.shared-production").read_text()
    assert "{$PUBLIC_API_HOST}" in config
    assert "reverse_proxy api:8000" in config
    assert "market-lens.2.28.100.77.sslip.io" in config
    assert "reverse_proxy market-lens-runtime-web:8000" in config
    assert "respond @writes" not in config
