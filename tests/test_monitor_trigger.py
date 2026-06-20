import sys
import types

from fastapi.testclient import TestClient

if "psycopg" not in sys.modules:
    psycopg_stub = types.ModuleType("psycopg")
    psycopg_rows_stub = types.ModuleType("psycopg.rows")
    psycopg_types_stub = types.ModuleType("psycopg.types")
    psycopg_json_stub = types.ModuleType("psycopg.types.json")
    psycopg_rows_stub.dict_row = object()
    psycopg_json_stub.Jsonb = dict
    psycopg_stub.Connection = object
    psycopg_stub.connect = lambda *_args, **_kwargs: None
    psycopg_types_stub.json = psycopg_json_stub
    psycopg_stub.rows = psycopg_rows_stub
    psycopg_stub.types = psycopg_types_stub
    sys.modules["psycopg"] = psycopg_stub
    sys.modules["psycopg.rows"] = psycopg_rows_stub
    sys.modules["psycopg.types"] = psycopg_types_stub
    sys.modules["psycopg.types.json"] = psycopg_json_stub

import app.main as main
import app.monitor_trigger as monitor_trigger
from app.monitor_trigger import detect_live_monitor_event


def reset_rate_limits() -> None:
    monitor_trigger._GLOBAL_TRIGGER_AT = 0.0
    monitor_trigger._EVENT_TRIGGER_AT.clear()


def test_detect_live_monitor_event_prefers_stop_first() -> None:
    position = {"ticker": "TEST", "stop_loss": 95, "target_1": 105, "target_2": 112}
    event = detect_live_monitor_event(position, 94.8)
    assert event is not None
    assert event.event_type == "EXIT_STOP"
    assert event.threshold == 95


def test_detect_live_monitor_event_target_2_before_target_1() -> None:
    position = {"ticker": "TEST", "stop_loss": 95, "target_1": 105, "target_2": 112}
    event = detect_live_monitor_event(position, 113)
    assert event is not None
    assert event.event_type == "TAKE_PROFIT"
    assert event.threshold == 112


def test_trigger_monitor_endpoint_skips_without_open_position(monkeypatch) -> None:
    reset_rate_limits()
    monkeypatch.setattr(main, "build_agent_dashboard", lambda *_args, **_kwargs: {"status": "ok", "open_positions": []})
    client = TestClient(main.app)
    response = client.post("/agent/trigger-monitor", json={"ticker": "TEST", "live_price": 105})
    assert response.status_code == 200
    payload = response.json()
    assert payload["triggered"] is False
    assert payload["status"] == "skipped"


def test_trigger_monitor_endpoint_dispatches_when_live_price_touches_target(monkeypatch) -> None:
    reset_rate_limits()
    monkeypatch.setenv("GITHUB_ACTIONS_TRIGGER_TOKEN", "test-token")
    monkeypatch.setattr(
        main,
        "build_agent_dashboard",
        lambda *_args, **_kwargs: {
            "status": "ok",
            "open_positions": [
                {
                    "ticker": "TEST",
                    "current_price_usd": 106,
                    "stop_loss": 95,
                    "target_1": 105,
                    "target_2": 112,
                }
            ],
        },
    )
    monkeypatch.setattr(main, "fetch_live_price", lambda ticker: (106.0, "2026-06-18T14:00:00Z"))

    async def fake_dispatch(event):
        return {"github_status": 204, "workflow": "market-lens-position-monitor.yml", "ticker": event.ticker}

    monkeypatch.setattr(main, "dispatch_position_monitor", fake_dispatch)

    client = TestClient(main.app)
    response = client.post(
        "/agent/trigger-monitor",
        json={"ticker": "TEST", "event_type": "TAKE_PARTIAL_PROFIT", "live_price": 106},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "triggered"
    assert payload["triggered"] is True
    assert payload["event_type"] == "TAKE_PARTIAL_PROFIT"
