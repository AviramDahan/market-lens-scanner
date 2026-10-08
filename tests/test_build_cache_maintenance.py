import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from deploy.hetzner import build_cache_maintenance as maintenance
from deploy.hetzner.schedule import NY, WEEKDAY_SCANS


@pytest.mark.parametrize("value,expected", [
    ("30.89GB", 30_890_000_000),
    ("512MiB", 512 * 1024**2),
    ("0B", 0),
])
def test_parse_docker_size(value, expected):
    assert maintenance.parse_size(value) == expected


def test_cache_size_reads_only_build_cache(monkeypatch):
    class Completed:
        stdout = '\n'.join((
            json.dumps({"Type": "Images", "Size": "4GB"}),
            json.dumps({"Type": "Build Cache", "Size": "30.89GB"}),
        ))

    monkeypatch.setattr(maintenance.subprocess, "run", lambda *args, **kwargs: Completed())
    assert maintenance.cache_bytes() == 30_890_000_000


def test_below_threshold_does_not_prune(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, "STATE", tmp_path)
    monkeypatch.setattr(maintenance, "free_bytes", lambda: 21 * 1024**3)
    monkeypatch.setattr(maintenance, "cache_bytes", lambda: 20 * 1000**3)
    monkeypatch.setattr(maintenance, "busy_reason", lambda: None)
    monkeypatch.setattr(maintenance, "prune_cache", lambda: pytest.fail("Unneeded prune"))
    result = maintenance.maintenance_once()
    assert result["status"] == "skipped_below_threshold"
    assert json.loads((tmp_path / "build-cache-last.json").read_text()) == result


def test_active_scanner_skips_even_with_low_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, "STATE", tmp_path)
    monkeypatch.setattr(maintenance, "free_bytes", lambda: 11 * 1024**3)
    monkeypatch.setattr(maintenance, "cache_bytes", lambda: 35 * 1000**3)
    monkeypatch.setattr(maintenance, "busy_reason", lambda: "market-lens-scanner.service is running")
    monkeypatch.setattr(maintenance, "prune_cache", lambda: pytest.fail("Pruned during scan"))
    alerts = []
    monkeypatch.setattr(maintenance, "send_runtime_alert", lambda *args: alerts.append(args))
    assert maintenance.maintenance_once()["status"] == "skipped_busy"
    assert alerts == [("maintenance", "DISK_LOW")]


def test_threshold_prunes_only_build_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, "STATE", tmp_path)
    free = iter((18 * 1024**3, 23 * 1024**3))
    cache = iter((32 * 1000**3, 16 * 1000**3))
    monkeypatch.setattr(maintenance, "free_bytes", lambda: next(free))
    monkeypatch.setattr(maintenance, "cache_bytes", lambda: next(cache))
    monkeypatch.setattr(maintenance, "busy_reason", lambda: None)
    commands = []
    monkeypatch.setattr(maintenance.subprocess, "run", lambda args, **kwargs: commands.append(args))
    result = maintenance.maintenance_once()
    assert result["status"] == "pruned"
    assert commands == [["docker", "buildx", "prune", "--filter", "until=168h",
                         "--max-used-space", "16gb", "--reserved-space", "12gb", "--force"]]


def test_ineffective_low_disk_prune_alerts(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, "STATE", tmp_path)
    monkeypatch.setattr(maintenance, "free_bytes", lambda: 18 * 1024**3)
    monkeypatch.setattr(maintenance, "cache_bytes", lambda: 32 * 1000**3)
    monkeypatch.setattr(maintenance, "busy_reason", lambda: None)
    monkeypatch.setattr(maintenance, "prune_cache", lambda: None)
    alerts = []
    monkeypatch.setattr(maintenance, "send_runtime_alert", lambda *args: alerts.append(args))
    assert maintenance.maintenance_once()["status"] == "pruned"
    assert alerts == [("maintenance", "MAINTENANCE_INEFFECTIVE")]


def test_failed_prune_records_failure_and_alerts(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, "STATE", tmp_path)
    monkeypatch.setattr(maintenance, "fcntl", SimpleNamespace(flock=lambda *args: None,
                                                              LOCK_EX=1, LOCK_NB=4))
    monkeypatch.setattr(maintenance, "maintenance_once", lambda: (_ for _ in ()).throw(RuntimeError("failed")))
    alerts = []
    monkeypatch.setattr(maintenance, "send_runtime_alert", lambda *args: alerts.append(args))
    assert maintenance.main() == 1
    assert json.loads((tmp_path / "build-cache-last.json").read_text())["status"] == "failed"
    assert alerts == [("maintenance", "MAINTENANCE_FAILED")]


def test_unknown_service_state_prevents_prune(monkeypatch):
    class Completed:
        stdout = "unknown\n"

    monkeypatch.setattr(maintenance.subprocess, "run", lambda *args, **kwargs: Completed())
    assert maintenance.busy_reason() == "market-lens-scanner.service activity could not be confirmed"


def test_weekly_timer_is_bounded_and_does_not_touch_trade_timers():
    deploy = Path(__file__).resolve().parents[1] / "deploy/hetzner"
    timer = (deploy / "market-lens-build-cache.timer").read_text()
    service = (deploy / "market-lens-build-cache.service").read_text()
    assert "Thu *-*-* 16:45:00 America/New_York" in timer
    assert "Thu *-*-* 16:55:00 America/New_York" in timer
    for month, day in ((1, 8), (7, 9)):
        for minute in (45, 55):
            local = datetime(2026, month, day, 16, minute, tzinfo=NY)
            assert local.strftime("%H:%M") not in WEEKDAY_SCANS
    assert "build_cache_maintenance.py" in service
    assert "SupplementaryGroups=docker" in service
