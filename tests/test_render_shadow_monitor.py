from __future__ import annotations

import asyncio
from pathlib import Path

import yaml

import app.main as main
from app.render_shadow_monitor import RenderShadowMonitor


ROOT = Path(__file__).resolve().parents[1]


def test_shadow_monitor_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("MARKET_LENS_RENDER_SHADOW_MONITOR_ENABLED", raising=False)
    monitor = RenderShadowMonitor()

    assert monitor.config()["enabled"] is False
    assert monitor.start(lambda: asyncio.sleep(0, result={})) is False
    assert monitor.snapshot()["status"] == "disabled"


def test_shadow_monitor_records_bounded_read_only_evidence() -> None:
    monitor = RenderShadowMonitor()

    async def cycle():
        return {
            "status": "events_detected",
            "session_phase": "REGULAR",
            "positions_checked": 2,
            "events": [
                {
                    "ticker": "AAA",
                    "event_type": "TAKE_PARTIAL_PROFIT",
                    "threshold": 105,
                }
            ],
            "warnings": {"BBB": "PriceUnavailable"},
        }

    snapshot = asyncio.run(monitor.run_once(cycle, timeout_seconds=5))

    assert snapshot["mode"] == "shadow"
    assert snapshot["side_effects_enabled"] is False
    assert snapshot["status"] == "events_detected"
    assert snapshot["last_positions_checked"] == 2
    assert snapshot["last_event_count"] == 1
    assert snapshot["last_events"][0]["ticker"] == "AAA"
    assert snapshot["last_warnings"] == {"BBB": "PriceUnavailable"}
    assert snapshot["total_polls"] == 1
    assert snapshot["total_events_detected"] == 1


def test_shadow_monitor_failure_is_contained() -> None:
    monitor = RenderShadowMonitor()

    async def cycle():
        raise RuntimeError("provider failed")

    snapshot = asyncio.run(monitor.run_once(cycle, timeout_seconds=5))

    assert snapshot["status"] == "error"
    assert snapshot["consecutive_failures"] == 1
    assert snapshot["last_error"] == "RuntimeError"
    assert "provider failed" not in str(snapshot)


def test_render_shadow_cycle_never_dispatches_or_sends_alerts(monkeypatch) -> None:
    monkeypatch.setenv("MARKET_LENS_RENDER_SHADOW_MONITOR_REGULAR_SESSION_ONLY", "true")
    monkeypatch.setattr(
        main,
        "session_status",
        lambda: {"phase": "REGULAR", "regular_session_open": True},
    )
    monkeypatch.setattr(
        main,
        "monitor_agent_dashboard",
        lambda: {
            "status": "ok",
            "open_positions": [
                {"ticker": "AAA", "stop_loss": 95, "target_1": 105, "target_2": 112}
            ],
        },
    )
    monkeypatch.setattr(
        main,
        "fetch_live_quote",
        lambda _ticker: (104.0, "2026-09-24T14:00:00+00:00", 105.5, 103.5),
    )

    async def forbidden_dispatch(*_args, **_kwargs):
        raise AssertionError("shadow monitor must not dispatch GitHub Actions")

    def forbidden_alert(*_args, **_kwargs):
        raise AssertionError("shadow monitor must not send Telegram alerts")

    monkeypatch.setattr(main, "dispatch_position_monitor", forbidden_dispatch)
    monkeypatch.setattr(main, "send_position_attention_alert", forbidden_alert)

    result = asyncio.run(main.run_render_shadow_monitor_cycle())

    assert result["status"] == "events_detected"
    assert result["positions_checked"] == 1
    assert result["events"][0]["event_type"] == "TAKE_PARTIAL_PROFIT"


def test_render_shadow_cycle_skips_outside_regular_session(monkeypatch) -> None:
    monkeypatch.setenv("MARKET_LENS_RENDER_SHADOW_MONITOR_REGULAR_SESSION_ONLY", "true")
    monkeypatch.setattr(
        main,
        "session_status",
        lambda: {"phase": "AFTER_HOURS", "regular_session_open": False},
    )

    def forbidden_dashboard():
        raise AssertionError("off-hours shadow cycle must not load dashboard data")

    monkeypatch.setattr(main, "monitor_agent_dashboard", forbidden_dashboard)

    result = asyncio.run(main.run_render_shadow_monitor_cycle())

    assert result == {
        "status": "outside_regular_session",
        "session_phase": "AFTER_HOURS",
        "positions_checked": 0,
        "events": [],
        "warnings": {},
    }


def test_render_blueprint_enables_shadow_on_existing_starter_only() -> None:
    payload = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))

    assert len(payload["services"]) == 1
    service = payload["services"][0]
    assert service["type"] == "web"
    assert service["name"] == "market-lens-scanner"
    assert service["plan"] == "starter"
    env = {item["key"]: item["value"] for item in service["envVars"]}
    assert env["MARKET_LENS_RENDER_SHADOW_MONITOR_ENABLED"] == "true"
    assert env["MARKET_LENS_RENDER_SHADOW_MONITOR_REGULAR_SESSION_ONLY"] == "true"
