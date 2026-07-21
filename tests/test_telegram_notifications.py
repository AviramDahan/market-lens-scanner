import json
from types import SimpleNamespace

import agent.market_lens_ui_agent as ui_agent
import agent.position_monitor as position_monitor
from agent.market_lens_ui_agent import Settings, SetupResult, send_new_buy_notifications
from app.telegram_notifications import (
    TelegramSettings,
    TelegramSendResult,
    dashboard_url_from_app_url,
    dashboard_url_from_env,
    format_position_event_message,
    format_position_opened_message,
    send_telegram_message,
    telegram_configured,
)


class FakeTelegramResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self) -> int:
        return self.status


def test_telegram_not_configured_skips_without_network() -> None:
    called = False

    def opener(*_args, **_kwargs):
        nonlocal called
        called = True
        return FakeTelegramResponse()

    result = send_telegram_message(
        "hello",
        settings=TelegramSettings(bot_token="", chat_id=""),
        opener=opener,
    )

    assert result.sent is False
    assert result.status == "not_configured"
    assert called is False


def test_telegram_send_uses_json_payload_without_exposing_secret() -> None:
    captured = {}

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeTelegramResponse()

    result = send_telegram_message(
        "<b>BUY</b>",
        settings=TelegramSettings(bot_token="SECRET_TOKEN", chat_id="-100", timeout_seconds=4),
        opener=opener,
    )

    assert result.sent is True
    assert result.status == "sent"
    assert captured["payload"]["chat_id"] == "-100"
    assert captured["payload"]["text"] == "<b>BUY</b>"
    assert captured["payload"]["parse_mode"] == "HTML"
    assert captured["timeout"] == 4
    assert "SECRET_TOKEN" not in result.reason


def test_position_opened_message_contains_trade_plan() -> None:
    result = SimpleNamespace(
        ticker="NVDA",
        setup_type="Breakout + Retest",
        score=0.61,
        current_price=210.0,
    )
    decision = SimpleNamespace(
        feedback="BUY_SIMULATED: valid setup.",
        decision_json={
            "net_rr": 2.34,
            "market_regime": "BULL",
            "sector": "Semiconductors",
            "sector_regime": "STRONG",
        },
    )
    position = {
        "entry_price": 210,
        "quantity": 12,
        "exposure_ils": 2520,
        "risk_ils": 180,
        "stop_loss": 195,
        "target_1": 225,
        "target_2": 245,
    }

    message = format_position_opened_message(
        result=result,
        decision=decision,
        position=position,
        run_id="run-1",
        timestamp="2026-06-22T10:30:00",
        dashboard_url="https://example.com/agent",
    )

    assert "BUY_SIMULATED opened" in message
    assert "NVDA" in message
    assert "$210.00" in message
    assert "$195.00" in message
    assert "2.34" in message
    assert "https://example.com/agent" in message


def test_dashboard_url_from_app_url() -> None:
    assert dashboard_url_from_app_url("https://market-lens-scanner-fb63.onrender.com/?v=latest") == (
        "https://market-lens-scanner-fb63.onrender.com/agent"
    )


def test_dashboard_url_from_env_prefers_public_url(monkeypatch) -> None:
    monkeypatch.setenv("MARKET_LENS_PUBLIC_URL", "https://market-lens-scanner-fb63.onrender.com")
    assert dashboard_url_from_env("http://127.0.0.1:8000/?v=agent") == (
        "https://market-lens-scanner-fb63.onrender.com/agent"
    )


def test_telegram_configured_requires_token_and_chat() -> None:
    assert telegram_configured(TelegramSettings(bot_token="token", chat_id="-1")) is True
    assert telegram_configured(TelegramSettings(bot_token="token", chat_id="", enabled=True)) is False
    assert telegram_configured(TelegramSettings(bot_token="token", chat_id="-1", enabled=False)) is False


def test_agent_sends_telegram_only_for_new_buy(monkeypatch, tmp_path) -> None:
    sent_messages = []

    def fake_send(message: str):
        sent_messages.append(message)
        return TelegramSendResult(True, "sent")

    monkeypatch.setattr(ui_agent, "send_telegram_message", fake_send)
    settings = Settings(
        url="https://market-lens-scanner-fb63.onrender.com/?v=latest",
        email="test@example.com",
        password="hidden",
        excel_path=tmp_path / "tracker.xlsx",
        universe="smart-universe",
        tickers=[],
        analysis_period="6mo",
        min_rr=2.0,
        headless=True,
        timeout_seconds=60,
    )
    buy_result = SetupResult(
        ticker="BUY",
        setup_type="Breakout + Retest",
        score=0.62,
        current_price=100,
        buy_zone_low=99,
        buy_zone_high=101,
        stop_loss=95,
        target_1=110,
        target_2=120,
        risk_reward=2.5,
        reason="valid",
        raw_text="",
    )
    watch_result = SetupResult(
        ticker="WATCH",
        setup_type="Breakout + Retest",
        score=0.62,
        current_price=100,
        buy_zone_low=99,
        buy_zone_high=101,
        stop_loss=95,
        target_1=110,
        target_2=120,
        risk_reward=2.5,
        reason="valid",
        raw_text="",
    )
    buy_decision = ui_agent.Decision("BUY_SIMULATED", "opened", quantity=10, decision_json={"net_rr": 2.4})
    watch_decision = ui_agent.Decision("WATCH_READY", "staged")

    send_new_buy_notifications(
        [(buy_result, buy_decision), (watch_result, watch_decision)],
        open_positions={
            "BUY": {
                "entry_price": 100,
                "quantity": 10,
                "exposure_ils": 1000,
                "risk_ils": 50,
                "stop_loss": 95,
                "target_1": 110,
                "target_2": 120,
            }
        },
        settings=settings,
        run_id="run-1",
        timestamp="2026-06-22T10:30:00",
    )

    assert len(sent_messages) == 1
    assert "BUY_SIMULATED opened" in sent_messages[0]
    assert "BUY" in sent_messages[0]
    assert "WATCH" not in sent_messages[0]


def test_position_event_message_contains_exit_details() -> None:
    position = {
        "ticker": "BA",
        "entry_price": 200,
        "stop_loss": 190,
        "target_1": 215,
        "target_2": 230,
    }
    event = position_monitor.PositionEvent(
        ticker="BA",
        action="TAKE_PARTIAL_PROFIT",
        triggered_at="2026-06-22T15:31:00+00:00",
        trigger_price=215,
        high=216,
        low=211,
        close=214,
        quantity=5,
        cash_in=1075,
        note="Target 1 touched by intraday high; taking partial profit and moving stop to breakeven.",
    )

    message = format_position_event_message(
        position=position,
        event=event,
        run_id="monitor-1",
        timestamp="2026-06-22T15:32:00+00:00",
        dashboard_url="https://example.com/agent",
    )

    assert "TP1 hit - partial profit" in message
    assert "BA" in message
    assert "$215.00" in message
    assert "+$75.00" in message
    assert "stop moves to breakeven" in message
    assert "https://example.com/agent" in message


def test_position_monitor_sends_telegram_for_position_events(monkeypatch, tmp_path) -> None:
    sent_messages = []

    def fake_send(message: str):
        sent_messages.append(message)
        return TelegramSendResult(True, "sent")

    monkeypatch.setattr(position_monitor, "send_telegram_message", fake_send)
    settings = position_monitor.MonitorSettings(
        excel_path=tmp_path / "tracker.xlsx",
        run_dir=tmp_path / "agent_results",
        period="5d",
        interval="1m",
        save_noop=False,
        dashboard_url="https://example.com/agent",
    )
    event = position_monitor.PositionEvent(
        ticker="MSFT",
        action="EXIT_STOP",
        triggered_at="2026-06-22T15:31:00+00:00",
        trigger_price=95,
        high=101,
        low=94,
        close=96,
        quantity=3,
        cash_in=285,
        note="Stop loss touched by intraday low.",
    )

    position_monitor.send_position_event_notifications(
        [({"ticker": "MSFT", "entry_price": 100, "stop_loss": 95, "target_1": 110, "target_2": 120}, event)],
        settings=settings,
        run_id="monitor-1",
        timestamp="2026-06-22T15:32:00+00:00",
    )

    assert len(sent_messages) == 1
    assert "Stop hit - position closed" in sent_messages[0]
    assert "MSFT" in sent_messages[0]
    assert "-$15.00" in sent_messages[0]
