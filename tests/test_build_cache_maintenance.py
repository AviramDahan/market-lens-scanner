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
    monkeypatch.setattr(maintenance, "cache_bytes", lambda: 4 * 1000**3)
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
    assert commands == [["docker", "buildx", "prune", "--filter", "until=48h",
                         "--min-free-space", "21474836480",
                         "--max-used-space", "6gb", "--reserved-space", "2gb", "--force"]]


def test_low_disk_prunes_cache_below_size_ceiling(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, "STATE", tmp_path)
    free = iter((14 * 1024**3, 21 * 1024**3))
    monkeypatch.setattr(maintenance, "free_bytes", lambda: next(free))
    monkeypatch.setattr(maintenance, "cache_bytes", lambda: 15 * 1000**3)
    monkeypatch.setattr(maintenance, "busy_reason", lambda: None)
    commands = []
    alerts = []
    monkeypatch.setattr(maintenance.subprocess, "run", lambda args, **kwargs: commands.append((args, kwargs)))
    monkeypatch.setattr(maintenance, "send_runtime_alert", lambda *args: alerts.append(args))

    result = maintenance.maintenance_once()

    assert result["status"] == "pruned"
    assert len(commands) == 1
    args, kwargs = commands[0]
    assert args == ["docker", "buildx", "prune", "--filter", "until=48h",
                    "--min-free-space", "21474836480", "--max-used-space", "6gb",
                    "--reserved-space", "2gb", "--force"]
    assert kwargs == {"check": True, "capture_output": True, "text": True, "timeout": 600}
    assert alerts == []
    assert json.loads((tmp_path / "build-cache-last.json").read_text()) == result


def test_prune_free_space_target_uses_same_threshold(monkeypatch):
    monkeypatch.setattr(maintenance, "TRIGGER_FREE_GB", 21.5)
    commands = []
    monkeypatch.setattr(maintenance.subprocess, "run", lambda args, **kwargs: commands.append(args))

    maintenance.prune_cache()

    args = commands[0]
    assert args[args.index("--min-free-space") + 1] == str(int(21.5 * 1024**3))


def test_pressure_prunes_only_old_unused_build_cache_not_images_or_volumes(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, "STATE", tmp_path)
    free = iter((11 * 1024**3, 16 * 1024**3))
    monkeypatch.setattr(maintenance, "free_bytes", lambda: next(free))
    monkeypatch.setattr(maintenance, "cache_bytes", lambda: 5 * 1000**3)
    monkeypatch.setattr(maintenance, "busy_reason", lambda: None)
    commands = []
    monkeypatch.setattr(maintenance.subprocess, "run", lambda args, **kw: commands.append(args))
    monkeypatch.setattr(maintenance, "send_runtime_alert", lambda *_: None)
    result = maintenance.maintenance_once()
    assert result["pressure_policy"] == "UNUSED_CACHE_2H_3GB_1GB_FLOOR"
    assert len(commands) == 1
    assert commands[0] == ["docker", "buildx", "prune", "--filter", "until=2h",
                           "--min-free-space", "21474836480", "--max-used-space", "3gb",
                           "--reserved-space", "1gb", "--force"]
    assert maintenance.MIN_FREE_GB == 12.0


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


def test_opted_in_cold_backups_restore_headroom_after_cache_prune(tmp_path, monkeypatch):
    from deploy.hetzner import cold_backups
    monkeypatch.setattr(maintenance, "STATE", tmp_path)
    free = iter((13*1024**3, 13*1024**3, 21*1024**3))
    monkeypatch.setattr(maintenance, "free_bytes", lambda: next(free))
    monkeypatch.setattr(maintenance, "cache_bytes", lambda: 2*1000**3)
    monkeypatch.setattr(maintenance, "busy_reason", lambda: None)
    monkeypatch.setattr(maintenance, "prune_cache", lambda **kw: None)
    monkeypatch.setattr(maintenance, "env_value", lambda name: 'true')
    monkeypatch.setattr(cold_backups, "archive_once", lambda: {"status": "ARCHIVED", "archived_bytes": 100})
    monkeypatch.setattr(maintenance, "send_runtime_alert", lambda *_: pytest.fail("Recovered capacity must not alert"))
    result = maintenance.maintenance_once()
    assert result["cold_backups"]["status"] == "ARCHIVED"
    assert result["free_bytes_after"] == 21*1024**3


def test_healthy_reserve_with_protected_cold_backups_is_not_a_false_failure(tmp_path, monkeypatch):
    from deploy.hetzner import cold_backups
    monkeypatch.setattr(maintenance, "STATE", tmp_path)
    monkeypatch.setattr(maintenance, "free_bytes", lambda: 18*1024**3)
    monkeypatch.setattr(maintenance, "cache_bytes", lambda: 2*1000**3)
    monkeypatch.setattr(maintenance, "busy_reason", lambda: None)
    monkeypatch.setattr(maintenance, "prune_cache", lambda **kw: None)
    monkeypatch.setattr(maintenance, "env_value", lambda name: 'true')
    monkeypatch.setattr(cold_backups, "archive_once", lambda: {"status": "NO_OFFLOAD_NEEDED", "archived_bytes": 0})
    monkeypatch.setattr(maintenance, "send_runtime_alert", lambda *_: pytest.fail("Not a critical disk failure"))
    result = maintenance.maintenance_once()
    assert result["capacity_target_met"] is False
    assert result["capacity_status"] == "RESERVE_HEALTHY_COLD_POLICY_HELD"


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
    assert "*:12,42:00 UTC" in timer
    for month, day in ((1, 8), (7, 9)):
        for minute in (45, 55):
            local = datetime(2026, month, day, 16, minute, tzinfo=NY)
            assert local.strftime("%H:%M") not in WEEKDAY_SCANS
    assert "build_cache_maintenance.py" in service
    assert "SupplementaryGroups=docker" in service
