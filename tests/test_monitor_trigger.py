import sys
import types
from datetime import datetime

import pandas as pd
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
import app.scan_trigger as scan_trigger
from app.monitor_trigger import detect_live_monitor_event
from app.scan_trigger import ScanScheduleDecision


def reset_rate_limits() -> None:
    monitor_trigger._GLOBAL_TRIGGER_AT = 0.0
    monitor_trigger._EVENT_TRIGGER_AT.clear()
    scan_trigger._DISPATCHED_SCAN_KEYS.clear()
    main._LIVE_PRICE_CACHE.clear()


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


def test_fetch_live_price_prefers_extended_hours_intraday(monkeypatch) -> None:
    reset_rate_limits()
    calls = []
    frame = pd.DataFrame(
        {"Close": [220.84]},
        index=pd.DatetimeIndex(["2026-06-22T23:15:00Z"]),
    )

    def fake_fetch_intraday_frame(ticker, period="5d", interval="1m", include_prepost=False):
        calls.append((ticker, period, interval, include_prepost))
        return frame

    monkeypatch.setattr(main, "fetch_intraday_frame", fake_fetch_intraday_frame)

    price, source_time = main.fetch_live_price("ba")

    assert price == 220.84
    assert source_time == "2026-06-22T23:15:00+00:00"
    assert calls == [("BA", "5d", "1m", True)]


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

    async def fake_dispatch(event, source="agent-ui-live-price"):
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


def test_monitor_live_endpoint_requires_cron_secret_when_configured(monkeypatch) -> None:
    reset_rate_limits()
    monkeypatch.setenv("MARKET_LENS_MONITOR_CRON_SECRET", "secret-value")
    monkeypatch.setattr(main, "build_agent_dashboard", lambda *_args, **_kwargs: {"status": "ok", "open_positions": []})

    client = TestClient(main.app)
    response = client.get("/agent/monitor-live")
    assert response.status_code == 401

    response = client.get("/agent/monitor-live", headers={"X-Market-Lens-Cron-Secret": "secret-value"})
    assert response.status_code == 200
    assert response.json()["protected"] is True


def test_trigger_scan_endpoint_skips_without_dispatch_outside_scan_time(monkeypatch) -> None:
    reset_rate_limits()
    monkeypatch.delenv("MARKET_LENS_AGENT_CRON_SECRET", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS_TRIGGER_TOKEN", "test-token")
    monkeypatch.setattr(
        main,
        "scan_schedule_decision",
        lambda force=False: ScanScheduleDecision(
            should_run=False,
            local_time="02:45",
            local_date="2026-06-23",
            local_weekday=2,
            scan_key="2026-06-23T02:45",
            reason="Outside configured New York scan times; GitHub Action dispatch skipped.",
            next_scan_at="2026-06-23T06:30:00-04:00",
        ),
    )

    async def fail_dispatch(*_args, **_kwargs):
        raise AssertionError("dispatch should not be called")

    monkeypatch.setattr(main, "dispatch_agent_scan", fail_dispatch)

    client = TestClient(main.app)
    response = client.get("/agent/trigger-scan")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "skipped"
    assert payload["triggered"] is False
    assert payload["scan_key"] == "2026-06-23T02:45"


def test_trigger_scan_endpoint_dispatches_once_at_scan_time(monkeypatch) -> None:
    reset_rate_limits()
    monkeypatch.setenv("GITHUB_ACTIONS_TRIGGER_TOKEN", "test-token")
    decision = ScanScheduleDecision(
        should_run=True,
        local_time="09:45",
        local_date="2026-06-23",
        local_weekday=2,
        scan_key="2026-06-23T09:45",
        reason="Current New York time matches a configured agent scan slot.",
        next_scan_at="2026-06-23T10:30:00-04:00",
    )
    monkeypatch.setattr(main, "scan_schedule_decision", lambda force=False: decision)
    dispatches = []

    async def fake_dispatch(source="agent-server-scan-scheduler"):
        dispatches.append(source)
        return {"github_status": 204, "workflow": "market-lens-agent.yml"}

    monkeypatch.setattr(main, "dispatch_agent_scan", fake_dispatch)

    client = TestClient(main.app)
    first = client.post("/agent/trigger-scan")
    second = client.post("/agent/trigger-scan")

    assert first.status_code == 200
    assert first.json()["status"] == "triggered"
    assert first.json()["triggered"] is True
    assert second.status_code == 200
    assert second.json()["status"] == "skipped"
    assert second.json()["triggered"] is False
    assert dispatches == ["agent-server-scan-scheduler"]


def test_trigger_scan_endpoint_returns_compact_dispatch_payload(monkeypatch) -> None:
    reset_rate_limits()
    monkeypatch.setenv("GITHUB_ACTIONS_TRIGGER_TOKEN", "test-token")
    decision = ScanScheduleDecision(
        should_run=True,
        local_time="09:45",
        local_date="2026-06-23",
        local_weekday=2,
        scan_key="2026-06-23T09:45",
        reason="Current New York time matches a configured agent scan slot.",
        next_scan_at="2026-06-23T10:30:00-04:00",
    )
    monkeypatch.setattr(main, "scan_schedule_decision", lambda force=False: decision)

    async def fake_dispatch(source="agent-server-scan-scheduler"):
        return {
            "github_status": 204,
            "workflow": "market-lens-agent.yml",
            "ref": "main",
            "repo": "AviramDahan/market-lens-scanner",
            "source": source,
            "raw_response": "x" * 100_000,
        }

    monkeypatch.setattr(main, "dispatch_agent_scan", fake_dispatch)

    client = TestClient(main.app)
    response = client.post("/agent/trigger-scan")
    payload = response.json()

    assert response.status_code == 200
    assert len(response.content) < 1_000
    assert payload["status"] == "triggered"
    assert payload["dispatch"] == {
        "github_status": 204,
        "workflow": "market-lens-agent.yml",
        "ref": "main",
        "repo": "AviramDahan/market-lens-scanner",
        "source": "agent-server-scan-scheduler",
    }


def test_trigger_scan_force_query_is_ignored_by_default(monkeypatch) -> None:
    reset_rate_limits()
    monkeypatch.delenv("MARKET_LENS_ALLOW_TRIGGER_SCAN_FORCE", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS_TRIGGER_TOKEN", "test-token")
    force_values = []

    def fake_decision(force=False):
        force_values.append(force)
        return ScanScheduleDecision(
            should_run=False,
            local_time="02:45",
            local_date="2026-06-23",
            local_weekday=2,
            scan_key="2026-06-23T02:45",
            reason="Outside configured New York scan times; GitHub Action dispatch skipped.",
            next_scan_at="2026-06-23T06:30:00-04:00",
        )

    async def fail_dispatch(*_args, **_kwargs):
        raise AssertionError("dispatch should not be called")

    monkeypatch.setattr(main, "scan_schedule_decision", fake_decision)
    monkeypatch.setattr(main, "dispatch_agent_scan", fail_dispatch)

    client = TestClient(main.app)
    response = client.get("/agent/trigger-scan?force=true")
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "skipped"
    assert payload["force_requested"] is True
    assert payload["force_applied"] is False
    assert force_values == [False]


def test_trigger_scan_endpoint_requires_agent_cron_secret_when_configured(monkeypatch) -> None:
    reset_rate_limits()
    monkeypatch.setenv("MARKET_LENS_AGENT_CRON_SECRET", "scan-secret")
    client = TestClient(main.app)

    response = client.get("/agent/trigger-scan")
    assert response.status_code == 401

    response = client.get("/agent/trigger-scan", headers={"X-Market-Lens-Cron-Secret": "scan-secret"})
    assert response.status_code == 200
    assert response.json()["protected"] is True


def test_scan_schedule_allows_short_cold_start_window(monkeypatch) -> None:
    monkeypatch.setenv("MARKET_LENS_AGENT_TRIGGER_WINDOW_MINUTES", "4")

    decision = scan_trigger.scan_schedule_decision(
        now=datetime.fromisoformat("2026-06-23T09:47:00-04:00"),
    )

    assert decision.should_run is True
    assert decision.scan_key == "2026-06-23T09:45"


def test_monitor_live_endpoint_dispatches_once_when_any_position_touches_target(monkeypatch) -> None:
    reset_rate_limits()
    monkeypatch.delenv("MARKET_LENS_MONITOR_CRON_SECRET", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS_TRIGGER_TOKEN", "test-token")
    monkeypatch.setattr(
        main,
        "build_agent_dashboard",
        lambda *_args, **_kwargs: {
            "status": "ok",
            "open_positions": [
                {"ticker": "AAA", "stop_loss": 95, "target_1": 105, "target_2": 112},
                {"ticker": "BBB", "stop_loss": 45, "target_1": 55, "target_2": 60},
            ],
        },
    )

    def fake_live_price(ticker):
        prices = {
            "AAA": (100.0, "2026-06-18T14:00:00Z"),
            "BBB": (55.5, "2026-06-18T14:00:00Z"),
        }
        return prices[ticker]

    monkeypatch.setattr(main, "fetch_live_price", fake_live_price)
    dispatches = []

    async def fake_dispatch(event, source="agent-ui-live-price"):
        dispatches.append((event.ticker, event.event_type, source))
        return {"github_status": 204, "workflow": "market-lens-position-monitor.yml", "ticker": event.ticker}

    monkeypatch.setattr(main, "dispatch_position_monitor", fake_dispatch)

    client = TestClient(main.app)
    response = client.post("/agent/monitor-live")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "triggered"
    assert payload["triggered"] is True
    assert payload["positions_checked"] == 2
    assert payload["dispatched_event"]["ticker"] == "BBB"
    assert dispatches == [("BBB", "TAKE_PARTIAL_PROFIT", "agent-server-live-monitor")]
