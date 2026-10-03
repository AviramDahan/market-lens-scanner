from datetime import datetime, timezone
from pathlib import Path

import yaml

from deploy.hetzner.schedule import due_jobs


ROOT = Path(__file__).resolve().parents[1]


def at_utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def test_regular_session_monitor_precedes_scanner():
    # 2026-10-05 09:45 New York (EDT).
    assert due_jobs(at_utc("2026-10-05T13:45:00")) == ["monitor", "scanner"]


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
    assert config["services"]["web"]["build"]["args"]["SOURCE_REVISION"]
    assert worker["build"]["args"]["SOURCE_REVISION"]


def test_shared_caddy_preview_preserves_existing_route_and_blocks_writes():
    config = (ROOT / "deploy/hetzner/Caddyfile.shared-preview").read_text()
    assert "{$PUBLIC_API_HOST}" in config
    assert "reverse_proxy api:8000" in config
    assert "market-lens.2.28.100.77.sslip.io" in config
    assert "respond @writes" in config
    assert "reverse_proxy market-lens-runtime-web:8000" in config
