import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import main
from app.execution_monitor_status import ExecutionMonitorStatus


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


def test_execution_status_uses_fake_clock_and_keeps_dispatch_separate() -> None:
    clock = FakeClock()
    status = ExecutionMonitorStatus(clock)
    status.record_check(
        status="ok",
        positions_expected=1,
        positions_checked=1,
        event_count=0,
        reason="No threshold touch.",
    )
    first = status.snapshot()
    assert first["last_check_at"] == "2026-10-02T14:00:00+00:00"
    assert first["last_dispatch_succeeded_at"] == ""

    clock.now += timedelta(minutes=1)
    status.record_dispatch(ticker="CME", event_type="TAKE_PROFIT", succeeded=True)
    second = status.snapshot()
    assert second["last_dispatch_succeeded_at"] == "2026-10-02T14:01:00+00:00"
    assert second["last_dispatch_ticker"] == "CME"
    assert second["last_dispatch_event"] == "TAKE_PROFIT"


def test_shadow_health_cannot_hide_unobserved_active_sensor(monkeypatch) -> None:
    main.execution_monitor_status.reset()
    monkeypatch.setenv("GITHUB_ACTIONS_TRIGGER_TOKEN", "configured")
    monkeypatch.setattr(
        main,
        "session_status",
        lambda: {"regular_session_open": True, "phase": "REGULAR"},
    )
    dashboard = {
        "system_health": {"status": "ok", "notes": [], "price_sensor_status": "CURRENT"},
        "open_positions": [{"ticker": "CME"}],
    }
    main.merge_execution_monitor_health(dashboard)
    assert dashboard["system_health"]["execution_sensor_status"] == "NOT_OBSERVED"
    assert dashboard["system_health"]["status"] == "attention"


def test_host_health_uses_current_persisted_monitor_heartbeat(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MARKET_LENS_HOST_MONITOR_ENABLED", "true")
    heartbeat_path = tmp_path / "latest_status.json"
    receipt_path = tmp_path / "latest_persisted.json"
    monkeypatch.setenv("MARKET_LENS_MONITOR_HEARTBEAT_PATH", str(heartbeat_path))
    monkeypatch.setenv("MARKET_LENS_HOST_MONITOR_PERSISTED_PATH", str(receipt_path))
    monkeypatch.setattr(main, "session_status", lambda: {"regular_session_open": True})
    now = datetime.now(timezone.utc).replace(microsecond=0)
    heartbeat_path.write_text(json.dumps({
        "schema_version": 1,
        "timestamp": now.isoformat(),
        "run_id": "monitor-1",
        "status": "MONITOR_OK",
        "positions_checked": 1,
        "positions_failed": 0,
        "event_count": 1,
    }))
    dashboard = {
        "system_health": {"status": "attention", "notes": ["Live monitor sensor: DISABLED"]},
        "open_positions": [{"ticker": "CME"}],
    }
    assert main.merge_host_monitor_health(dashboard) is True
    health = dashboard["system_health"]
    assert health["execution_sensor_status"] == "CURRENT"
    assert health["executor_persistence_status"] == "PENDING"
    assert health["status"] == "attention"

    receipt_path.write_text(json.dumps({"run_id": "monitor-1", "status": "PERSISTED_AND_DELIVERED"}))
    main.merge_host_monitor_health(dashboard)
    assert health["executor_persistence_status"] == "CONFIRMED"
    assert health["status"] == "ok"

    heartbeat_path.write_text(json.dumps({
        "schema_version": 1,
        "timestamp": (now - timedelta(minutes=5)).isoformat(),
        "run_id": "monitor-2",
        "status": "MONITOR_OK",
        "positions_checked": 1,
        "event_count": 0,
    }))
    main.merge_host_monitor_health(dashboard)
    assert health["execution_sensor_status"] == "STALE"
    assert health["status"] == "attention"


def test_no_event_endpoint_records_active_sensor_without_executor_claim(monkeypatch, tmp_path) -> None:
    main.execution_monitor_status.reset()
    monkeypatch.delenv("MARKET_LENS_MONITOR_CRON_SECRET", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS_TRIGGER_TOKEN", "configured")
    monkeypatch.setattr(main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(main, "DASHBOARD_SNAPSHOT_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(main, "sync_dashboard_snapshot_if_enabled", lambda _root: {"enabled": False})
    monkeypatch.setattr(
        main,
        "build_agent_dashboard",
        lambda *_args, **_kwargs: {
            "status": "ok",
            "open_positions": [
                {"ticker": "CME", "stop_loss": 95, "target_1": 105, "target_2": 112}
            ],
        },
    )
    monkeypatch.setattr(
        main,
        "fetch_live_quote",
        lambda _ticker: (100.0, "2026-10-02T14:00:00Z", 101.0, 99.0),
    )

    response = TestClient(main.app).get("/agent/monitor-live?compact=false")
    assert response.status_code == 200
    assert response.json()["triggered"] is False
    snapshot = main.execution_monitor_status.snapshot()
    assert snapshot["last_status"] == "ok"
    assert snapshot["last_positions_checked"] == 1
    assert snapshot["last_dispatch_succeeded_at"] == ""


def test_execution_status_endpoint_exposes_no_credentials() -> None:
    payload = TestClient(main.app).get("/agent/monitor-execution-status").json()
    assert payload["mode"] == "active_sensor"
    assert payload["side_effects_enabled"] is True
    assert "token" not in str(payload).lower()
    assert "secret" not in str(payload).lower()


def test_failed_dispatch_is_recorded_without_claiming_success(monkeypatch, tmp_path) -> None:
    main.execution_monitor_status.reset()
    monkeypatch.delenv("MARKET_LENS_MONITOR_CRON_SECRET", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS_TRIGGER_TOKEN", "configured")
    monkeypatch.setattr(main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(main, "DASHBOARD_SNAPSHOT_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(main, "sync_dashboard_snapshot_if_enabled", lambda _root: {"enabled": False})
    monkeypatch.setattr(
        main,
        "build_agent_dashboard",
        lambda *_args, **_kwargs: {
            "status": "ok",
            "open_positions": [
                {"ticker": "CME", "stop_loss": 95, "target_1": 105, "target_2": 112}
            ],
        },
    )
    monkeypatch.setattr(
        main,
        "fetch_live_quote",
        lambda _ticker: (106.0, "2026-10-02T14:00:00Z", 106.0, 100.0),
    )
    monkeypatch.setattr(main, "rate_limit_reason", lambda _event: None)

    async def fail_dispatch(*_args, **_kwargs):
        raise RuntimeError("provider detail must not enter status")

    monkeypatch.setattr(main, "dispatch_position_monitor", fail_dispatch)
    response = TestClient(main.app).get("/agent/monitor-live?compact=false")
    assert response.status_code == 502
    snapshot = main.execution_monitor_status.snapshot()
    assert snapshot["last_dispatch_succeeded_at"] == ""
    assert snapshot["last_dispatch_failed_at"]
    assert snapshot["last_dispatch_ticker"] == "CME"
    assert "provider detail" not in str(snapshot)
