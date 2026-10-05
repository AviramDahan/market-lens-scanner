import json
import os

import pytest

from deploy.hetzner import run_runtime as runtime


def test_writer_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MARKET_LENS_HETZNER_WRITER_ENABLED", raising=False)
    with pytest.raises(RuntimeError, match="disabled"):
        runtime.require_live_preflight()


def test_legacy_writer_must_be_disabled(monkeypatch):
    monkeypatch.setenv("MARKET_LENS_HETZNER_WRITER_ENABLED", "true")
    monkeypatch.delenv("MARKET_LENS_OLD_WRITERS_DISABLED", raising=False)
    with pytest.raises(RuntimeError, match="Legacy"):
        runtime.require_live_preflight()


def test_worker_uses_service_docker_group_without_sudo(monkeypatch):
    commands = []
    monkeypatch.setattr(runtime, "run", lambda *args, **kwargs: commands.append((args, kwargs)))

    runtime.worker(["python", "-c", "pass"], timeout=30)

    args, kwargs = commands[0]
    assert args[0:4] == ("docker", "compose", "-f", str(runtime.COMPOSE))
    assert args[-4:] == ("worker", "python", "-c", "pass")
    assert kwargs["timeout"] == 30


@pytest.mark.parametrize("changed,blocked", [
    ("agent/ops_health_check.py", False),
    ("agent/ops_health_check.py\nagent/position_monitor.py", True),
])
def test_host_only_checker_does_not_require_worker_rebuild(tmp_path, monkeypatch, changed, blocked):
    monkeypatch.setenv("MARKET_LENS_HETZNER_WRITER_ENABLED", "true")
    monkeypatch.setenv("MARKET_LENS_OLD_WRITERS_DISABLED", "true")
    monkeypatch.setattr(runtime, "STATE", tmp_path)
    (tmp_path / "deployed_revision").write_text("old-revision")
    tracker = tmp_path / "tracker.xlsx"
    tracker.write_bytes(b"fixture")
    monkeypatch.setattr(runtime, "TRACKER", tracker)
    monkeypatch.setattr(runtime, "run", lambda *_args, **_kwargs: None)

    def git_output(*args, **_kwargs):
        if args[1] == "status":
            return ""
        if args[1] == "rev-parse":
            return "same-revision"
        if args[1] == "diff":
            return changed
        raise AssertionError(args)

    monkeypatch.setattr(runtime, "output", git_output)
    if blocked:
        with pytest.raises(RuntimeError, match="agent/position_monitor.py"):
            runtime.require_live_preflight()
    else:
        runtime.require_live_preflight()


def test_pending_notification_blocks_next_trade_run(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_LENS_HETZNER_WRITER_ENABLED", "true")
    monkeypatch.setenv("MARKET_LENS_OLD_WRITERS_DISABLED", "true")
    monkeypatch.setattr(runtime, "STATE", tmp_path)
    tracker = tmp_path / "tracker.xlsx"
    tracker.write_bytes(b"fixture")
    monkeypatch.setattr(runtime, "TRACKER", tracker)
    (tmp_path / "outbox").mkdir()
    (tmp_path / "outbox/scanner.json").write_text("{}")
    with pytest.raises(RuntimeError, match="undelivered"):
        runtime.require_live_preflight()


def test_monitor_noop_stays_local(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "REPO", tmp_path)
    monkeypatch.setattr(runtime, "STATE", tmp_path / "state")
    tracker = tmp_path / "tracker.xlsx"
    tracker.write_bytes(b"unchanged")
    monkeypatch.setattr(runtime, "TRACKER", tracker)
    heartbeat = runtime.STATE / "monitor/latest_status.json"
    heartbeat.parent.mkdir(parents=True)
    heartbeat.write_text(json.dumps({"status": "MONITOR_OK", "event_count": 0}))
    calls = []
    monkeypatch.setattr(runtime, "require_live_preflight", lambda: None)
    def fake_worker(*args, **_kwargs):
        calls.append(args)
        os.utime(heartbeat, ns=(heartbeat.stat().st_atime_ns, heartbeat.stat().st_mtime_ns + 1_000_000_000))

    monkeypatch.setattr(runtime, "worker", fake_worker)
    monkeypatch.setattr(runtime, "persist", lambda *_: pytest.fail("No-op monitor persisted"))
    monkeypatch.setattr(runtime, "deliver", lambda *_: pytest.fail("No-op monitor notified"))

    runtime.execute("monitor")

    assert len(calls) == 1
    assert calls[0][0] == ["python", "agent/position_monitor.py"]


def test_scanner_cannot_notify_if_persistence_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "REPO", tmp_path)
    monkeypatch.setattr(runtime, "STATE", tmp_path / "state")
    tracker = tmp_path / "tracker.xlsx"
    tracker.write_bytes(b"fixture")
    monkeypatch.setattr(runtime, "TRACKER", tracker)
    results = tmp_path / "agent_results/runtime"
    results.mkdir(parents=True)
    monkeypatch.setattr(runtime, "require_live_preflight", lambda: None)

    def fake_worker(command, **_kwargs):
        if command == ["python", "agent/market_lens_ui_agent.py"]:
            (results / "market_lens_agent_20261003_120000.json").write_text(
                json.dumps({"run_status": "COMPLETE", "result_cards_read": 3})
            )

    monkeypatch.setattr(runtime, "worker", fake_worker)
    monkeypatch.setattr(runtime, "persist", lambda *_: (_ for _ in ()).throw(RuntimeError("push failed")))
    monkeypatch.setattr(runtime, "deliver", lambda *_: pytest.fail("Notification before persistence"))

    with pytest.raises(RuntimeError, match="push failed"):
        runtime.execute("scanner")


def test_scanner_rejects_missing_or_failed_scan_record(tmp_path, monkeypatch):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setattr(runtime, "REPO", tmp_path)

    with pytest.raises(RuntimeError, match="Expected one new scanner record"):
        runtime.latest_scan_record(set())

    (runtime_dir / "market_lens_agent_20261003_120000.json").write_text(
        json.dumps({"run_status": "AUTH_FAILED", "result_cards_read": 0})
    )
    (tmp_path / "agent_results").mkdir()
    runtime_dir.rename(tmp_path / "agent_results/runtime")
    with pytest.raises(RuntimeError, match="successful, nonempty scan"):
        runtime.latest_scan_record(set())


def test_monitor_event_without_tracker_change_is_not_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "REPO", tmp_path)
    monkeypatch.setattr(runtime, "STATE", tmp_path / "state")
    tracker = tmp_path / "tracker.xlsx"
    tracker.write_bytes(b"unchanged")
    monkeypatch.setattr(runtime, "TRACKER", tracker)
    heartbeat = runtime.STATE / "monitor/latest_status.json"
    heartbeat.parent.mkdir(parents=True)
    heartbeat.write_text(json.dumps({"status": "MONITOR_OK", "event_count": 1}))
    monkeypatch.setattr(runtime, "require_live_preflight", lambda: None)
    def fake_worker(*_args, **_kwargs):
        os.utime(heartbeat, ns=(heartbeat.stat().st_atime_ns, heartbeat.stat().st_mtime_ns + 1_000_000_000))

    monkeypatch.setattr(runtime, "worker", fake_worker)
    monkeypatch.setattr(runtime, "persist", lambda *_: pytest.fail("Unsaved event persisted"))

    with pytest.raises(RuntimeError, match="without a saved portfolio change"):
        runtime.execute("monitor")


@pytest.mark.parametrize("delivery_fails", [False, True])
def test_monitor_receipt_requires_persistence_and_delivery(tmp_path, monkeypatch, delivery_fails):
    monkeypatch.setattr(runtime, "REPO", tmp_path)
    monkeypatch.setattr(runtime, "STATE", tmp_path / "state")
    tracker = tmp_path / "tracker.xlsx"
    tracker.write_bytes(b"before")
    monkeypatch.setattr(runtime, "TRACKER", tracker)
    heartbeat = runtime.STATE / "monitor/latest_status.json"
    heartbeat.parent.mkdir(parents=True)
    heartbeat.write_text(json.dumps({"status": "MONITOR_OK", "event_count": 0}))
    monkeypatch.setattr(runtime, "require_live_preflight", lambda: None)

    def fake_worker(command, **_kwargs):
        if command == ["python", "agent/position_monitor.py"]:
            heartbeat.write_text(json.dumps({
                "run_id": "monitor-test", "status": "MONITOR_OK", "event_count": 1,
            }))
            tracker.write_bytes(b"tp1-saved")

    def fake_deliver(_kind):
        assert not (runtime.STATE / "monitor/latest_persisted.json").exists()
        if delivery_fails:
            raise runtime.RuntimeDeliveryFailure("delivery failed")

    monkeypatch.setattr(runtime, "worker", fake_worker)
    monkeypatch.setattr(runtime, "persist", lambda *_: True)
    monkeypatch.setattr(runtime, "deliver", fake_deliver)
    if delivery_fails:
        with pytest.raises(runtime.RuntimeDeliveryFailure):
            runtime.execute("monitor")
        assert not (runtime.STATE / "monitor/latest_persisted.json").exists()
    else:
        runtime.execute("monitor")
        receipt = json.loads((runtime.STATE / "monitor/latest_persisted.json").read_text())
        assert receipt == {
            "run_id": "monitor-test", "event_count": 1,
            "status": "PERSISTED_AND_DELIVERED",
        }


def test_monitor_rejects_stale_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "REPO", tmp_path)
    monkeypatch.setattr(runtime, "STATE", tmp_path / "state")
    tracker = tmp_path / "tracker.xlsx"
    tracker.write_bytes(b"unchanged")
    monkeypatch.setattr(runtime, "TRACKER", tracker)
    heartbeat = runtime.STATE / "monitor/latest_status.json"
    heartbeat.parent.mkdir(parents=True)
    heartbeat.write_text(json.dumps({"status": "MONITOR_OK", "event_count": 0}))
    monkeypatch.setattr(runtime, "require_live_preflight", lambda: None)
    monkeypatch.setattr(runtime, "worker", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "persist", lambda *_: pytest.fail("Stale monitor persisted"))

    with pytest.raises(RuntimeError, match="fresh heartbeat"):
        runtime.execute("monitor")
