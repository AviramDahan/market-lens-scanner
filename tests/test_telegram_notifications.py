import json
from types import SimpleNamespace

import pytest

import agent.market_lens_ui_agent as ui_agent
import agent.position_monitor as position_monitor
from agent.market_lens_ui_agent import Settings, SetupResult, send_new_buy_notifications
from app.telegram_notifications import (
    TelegramSettings,
    TelegramSendResult,
    chart_photo_source,
    dashboard_url_from_app_url,
    dashboard_url_from_env,
    format_position_attention_message,
    format_position_event_message,
    format_position_opened_message,
    format_qualified_capital_blocked_message,
    format_stop_moved_to_entry_message,
    load_telegram_settings,
    send_telegram_message,
    send_telegram_photo,
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


def test_telegram_message_dedupe_skips_repeated_event(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MARKET_LENS_TELEGRAM_DEDUP_LOG", str(tmp_path / "telegram_notifications.jsonl"))
    calls = []

    def opener(request, timeout):
        calls.append((request.full_url, timeout))
        return FakeTelegramResponse()

    settings = TelegramSettings(bot_token="SECRET_TOKEN", chat_id="-100", timeout_seconds=4)
    first = send_telegram_message(
        "<b>TP1</b>",
        settings=settings,
        opener=opener,
        dedupe_key="POSITION_EVENT|MSFT|TAKE_PARTIAL_PROFIT|2026-06-22T15:31:00Z|110|5",
    )
    second = send_telegram_message(
        "<b>TP1</b>",
        settings=settings,
        opener=opener,
        dedupe_key="POSITION_EVENT|MSFT|TAKE_PARTIAL_PROFIT|2026-06-22T15:31:00Z|110|5",
    )

    assert first.sent is True
    assert first.status == "sent"
    assert second.sent is False
    assert second.status == "duplicate"
    assert len(calls) == 1


def test_telegram_send_photo_uses_json_payload_for_remote_chart() -> None:
    captured = {}

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["content_type"] = request.headers["Content-type"]
        return FakeTelegramResponse()

    result = send_telegram_photo(
        "https://example.com/chart.png",
        caption="<b>NVDA chart</b>",
        settings=TelegramSettings(bot_token="SECRET_TOKEN", chat_id="-100", timeout_seconds=4),
        opener=opener,
    )

    assert result.sent is True
    assert result.status == "sent"
    assert captured["payload"]["chat_id"] == "-100"
    assert captured["payload"]["photo"] == "https://example.com/chart.png"
    assert captured["payload"]["caption"] == "<b>NVDA chart</b>"
    assert captured["payload"]["parse_mode"] == "HTML"
    assert "SECRET_TOKEN" not in result.reason


def test_telegram_send_photo_uploads_local_chart(tmp_path) -> None:
    captured = {}
    chart = tmp_path / "chart.png"
    chart.write_bytes(b"fake-png-data")

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = request.data
        captured["content_type"] = request.headers["Content-type"]
        return FakeTelegramResponse()

    result = send_telegram_photo(
        chart,
        caption="<b>MSFT chart</b>",
        settings=TelegramSettings(bot_token="SECRET_TOKEN", chat_id="-100", timeout_seconds=4),
        opener=opener,
    )

    assert result.sent is True
    assert result.status == "sent"
    assert "multipart/form-data" in captured["content_type"]
    assert b'name="photo"; filename="chart.png"' in captured["body"]
    assert b"fake-png-data" in captured["body"]
    assert b"<b>MSFT chart</b>" in captured["body"]


def test_chart_photo_source_resolves_relative_dashboard_path() -> None:
    assert chart_photo_source("/agent-results/charts/nvda.png", "https://example.com/agent") == (
        "https://example.com/agent-results/charts/nvda.png"
    )


def test_chart_photo_source_resolves_saved_agent_results_path() -> None:
    assert chart_photo_source(r"C:\agent\agent_results\charts\nvda.png", "https://example.com/agent") == (
        "https://example.com/agent-results/charts/nvda.png"
    )


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

    assert "BUY | NVDA" in message
    assert "NVDA" in message
    assert "Time: 2026-06-22 13:30" in message
    assert "Time: 2026-06-22T10:30:00" not in message
    assert "$210.00" in message
    assert "$195.00 (-7.14%)" in message
    assert "TP1: $225.00 (+7.14%)" in message
    assert "TP2: $245.00 (+16.67%)" in message
    assert "2.34" in message
    assert "https://example.com/agent" in message


def test_qualified_capital_blocked_message_is_explicitly_alert_only() -> None:
    result = SimpleNamespace(
        ticker="NVDA",
        setup_type="Breakout + Retest",
        score=0.64,
        current_price=210.0,
        stop_loss=200.0,
        target_1=225.0,
        target_2=245.0,
    )
    decision = SimpleNamespace(
        feedback="QUALIFIED_CAPITAL_BLOCKED: Portfolio heat cap would be exceeded.",
        decision_json={
            "company_name": "NVIDIA Corporation",
            "entry_eligibility_status": "QUALIFIED_CAPITAL_BLOCKED",
            "net_entry": 210.25,
            "stop_loss": 200.0,
            "target_1": 225.0,
            "target_2": 245.0,
            "adjusted_position_size": 12,
            "adjusted_cash_out": 2523.0,
            "adjusted_risk_amount": 123.0,
            "setup_score": 0.64,
            "net_rr": 2.30,
            "net_rr_1": 1.25,
            "net_rr_2": 3.10,
            "market_regime": "BULL",
            "sector_regime": "STRONG",
            "capital_blockers": ["WATCH: Portfolio heat cap would be exceeded."],
        },
    )

    message = format_qualified_capital_blocked_message(
        result=result,
        decision=decision,
        timestamp="2026-09-10T14:32:00+00:00",
        dashboard_url="https://example.com/agent",
    )

    assert "QUALIFIED SETUP | NOT ENTERED | NVDA (NVIDIA Corporation)" in message
    assert "Entry: $210.25" in message
    assert "Proposed qty: 12 | Exposure: $2,523.00 | Risk: $123.00" in message
    assert "TP1: $225.00 (+7.02%)" in message
    assert "Blocked by: WATCH: Portfolio heat cap would be exceeded." in message
    assert "no position was opened" in message


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


def test_telegram_settings_use_market_lens_env_only_by_default(monkeypatch) -> None:
    monkeypatch.delenv("MARKET_LENS_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("MARKET_LENS_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("MARKET_LENS_TELEGRAM_ALLOW_LEGACY_ENV", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "legacy-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-legacy-chat")

    settings = load_telegram_settings()

    assert settings.bot_token == ""
    assert settings.chat_id == ""


def test_telegram_settings_can_opt_in_to_legacy_env(monkeypatch) -> None:
    monkeypatch.delenv("MARKET_LENS_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("MARKET_LENS_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("MARKET_LENS_TELEGRAM_ALLOW_LEGACY_ENV", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "legacy-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-legacy-chat")

    settings = load_telegram_settings()

    assert settings.bot_token == "legacy-token"
    assert settings.chat_id == "-legacy-chat"


def test_agent_sends_telegram_only_for_new_buy(monkeypatch, tmp_path) -> None:
    sent_messages = []
    sent_charts = []

    def fake_send(message: str, **_kwargs):
        sent_messages.append(message)
        return TelegramSendResult(True, "sent")

    def fake_send_chart(chart_ref, *, ticker, dashboard_url, **_kwargs):
        sent_charts.append((chart_ref, ticker, dashboard_url))
        return TelegramSendResult(True, "sent")

    monkeypatch.setattr(ui_agent, "send_telegram_message", fake_send)
    monkeypatch.setattr(ui_agent, "send_telegram_chart_photo", fake_send_chart)
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
                "chart_url": "agent_results/charts/buy.png",
            }
        },
        settings=settings,
        run_id="run-1",
        timestamp="2026-06-22T10:30:00",
    )

    assert len(sent_messages) == 1
    assert "BUY | BUY" in sent_messages[0]
    assert "BUY" in sent_messages[0]
    assert "WATCH" not in sent_messages[0]
    assert sent_charts == [("agent_results/charts/buy.png", "BUY", "https://market-lens-scanner-fb63.onrender.com/agent")]


def test_agent_buy_outbox_defers_notification_until_explicit_delivery(monkeypatch, tmp_path) -> None:
    sent_messages = []

    def fake_send(message: str, **_kwargs):
        sent_messages.append(message)
        return TelegramSendResult(True, "sent")

    monkeypatch.setattr(ui_agent, "send_telegram_message", fake_send)
    monkeypatch.setattr(
        ui_agent,
        "send_telegram_chart_photo",
        lambda *_args, **_kwargs: TelegramSendResult(False, "no_photo"),
    )
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
    result = SetupResult(
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
    decision = ui_agent.Decision("BUY_SIMULATED", "opened", quantity=10, decision_json={"net_rr": 2.4})
    outbox = tmp_path / "buy-notifications.json"

    ui_agent.write_buy_notification_outbox(
        outbox,
        [(result, decision)],
        open_positions={
            "BUY": {
                "entry_price": 100,
                "quantity": 10,
                "stop_loss": 95,
                "target_1": 110,
                "target_2": 120,
            }
        },
        settings=settings,
        run_id="run-1",
        timestamp="2026-09-10T14:32:00+00:00",
    )

    assert outbox.exists()
    assert sent_messages == []
    outcomes = ui_agent.send_buy_notification_outbox(outbox)
    assert [outcome.status for outcome in outcomes] == ["sent", "no_photo"]
    assert len(sent_messages) == 1


def test_agent_builds_alert_for_qualified_capital_block_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MARKET_LENS_TELEGRAM_QUALIFIED_BLOCKED_ENABLED", "true")
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
    result = SetupResult(
        ticker="NVDA",
        setup_type="Breakout + Retest",
        score=0.64,
        current_price=210,
        buy_zone_low=208,
        buy_zone_high=211,
        stop_loss=200,
        target_1=225,
        target_2=245,
        risk_reward=2.5,
        reason="valid",
        raw_text="",
        chart_url="agent_results/charts/nvda.png",
    )
    capital_decision = ui_agent.Decision(
        "WATCH",
        "QUALIFIED_CAPITAL_BLOCKED: Portfolio heat cap would be exceeded.",
        decision_json={
            "entry_eligibility_status": "QUALIFIED_CAPITAL_BLOCKED",
            "entry_qualified_before_capital": True,
            "capital_blocked_only": True,
            "entry_gate_blockers": [],
            "capital_blockers": ["WATCH: Portfolio heat cap would be exceeded."],
            "setup_score": 0.64,
            "net_rr": 2.30,
        },
    )
    technical_decision = ui_agent.Decision(
        "WATCH",
        "Entry confirmation missing.",
        decision_json={
            "entry_eligibility_status": "ENTRY_GATES_BLOCKED",
            "entry_qualified_before_capital": False,
            "capital_blocked_only": False,
            "entry_gate_blockers": ["Entry confirmation missing."],
            "capital_blockers": [],
        },
    )

    records = ui_agent.build_buy_notification_records(
        [(result, capital_decision), (result, technical_decision)],
        open_positions={},
        settings=settings,
        run_id="run-1",
        timestamp="2026-09-10T14:32:00+00:00",
    )

    assert len(records) == 1
    assert records[0]["notification_type"] == "QUALIFIED_CAPITAL_BLOCKED"
    assert "NOT ENTERED" in records[0]["message"]
    assert records[0]["chart_ref"] == "agent_results/charts/nvda.png"
    assert capital_decision.action == "WATCH"
    assert technical_decision.action == "WATCH"


def test_agent_builds_alert_for_zero_size_after_technical_gates_pass(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MARKET_LENS_TELEGRAM_QUALIFIED_BLOCKED_ENABLED", "true")
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
    result = SetupResult(
        ticker="MSFT", setup_type="VWAP Reclaim", score=0.60, current_price=500,
        buy_zone_low=495, buy_zone_high=501, stop_loss=485, target_1=520,
        target_2=545, risk_reward=2.4, reason="valid", raw_text="",
    )
    decision = ui_agent.Decision(
        "SKIP",
        "SKIP: Position size blocked by cash, exposure, or risk limits.",
        decision_json={
            "entry_eligibility_status": "CAPITAL_BLOCKED_UNASSESSED",
            "technical_entry_gates_passed": True,
            "entry_qualified_before_capital": False,
            "capital_blocked_only": False,
            "entry_gate_blockers": [],
            "capital_blockers": ["Position size blocked by cash, exposure, or risk limits."],
            "setup_score": 0.60,
            "net_rr": 2.40,
        },
    )

    records = ui_agent.build_buy_notification_records(
        [(result, decision)], open_positions={}, settings=settings,
        run_id="run-2", timestamp="2026-09-10T15:30:00+00:00",
    )

    assert len(records) == 1
    assert records[0]["notification_type"] == "QUALIFIED_CAPITAL_BLOCKED"
    assert "NOT ENTERED" in records[0]["message"]
    assert "Proposed qty" not in records[0]["message"]
    assert decision.action == "SKIP"


def test_qualified_capital_alert_dedupe_ignores_small_quality_noise() -> None:
    result = SetupResult(
        ticker="NVDA",
        setup_type="Breakout + Retest",
        score=0.61,
        current_price=210,
        buy_zone_low=208,
        buy_zone_high=211,
        stop_loss=200,
        target_1=225,
        target_2=245,
        risk_reward=2.5,
        reason="valid",
        raw_text="",
    )

    first = ui_agent.qualified_capital_blocked_dedupe_key(
        result=result,
        decision_json={"setup_score": 0.61, "net_rr": 2.31},
        timestamp="2026-09-10T14:32:00+00:00",
    )
    noisy_repeat = ui_agent.qualified_capital_blocked_dedupe_key(
        result=result,
        decision_json={"setup_score": 0.62, "net_rr": 2.34},
        timestamp="2026-09-10T15:30:00+00:00",
    )
    material_improvement = ui_agent.qualified_capital_blocked_dedupe_key(
        result=result,
        decision_json={"setup_score": 0.67, "net_rr": 2.58},
        timestamp="2026-09-10T16:30:00+00:00",
    )

    assert noisy_repeat == first
    assert material_improvement != first


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

    assert "TP1 | PARTIAL SOLD" in message
    assert "BA" in message
    assert "Time: 2026-06-22 18:32" in message
    assert "$215.00" in message
    assert "$190.00 (-5.00%)" in message
    assert "TP1: $215.00 (+7.50%)" in message
    assert "TP2: $230.00 (+15.00%)" in message
    assert "+$75.00" in message
    assert "stop moves to entry" in message
    assert "https://example.com/agent" in message


def test_stop_moved_to_entry_message_contains_breakeven_update() -> None:
    position = {
        "ticker": "BA",
        "entry_price": 200,
        "stop_loss": 190,
        "target_1": 215,
        "target_2": 230,
        "quantity": 10,
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

    message = format_stop_moved_to_entry_message(
        position=position,
        event=event,
        run_id="monitor-1",
        timestamp="2026-06-22T15:32:00+00:00",
        dashboard_url="https://example.com/agent",
    )

    assert "STOP TO ENTRY | BA" in message
    assert "BA" in message
    assert "Time: 2026-06-22 18:32" in message
    assert "Old SL: $190.00 (-5.00%)" in message
    assert "New SL: $200.00 (0.00%)" in message
    assert "Remaining qty: 5" in message
    assert "TP1 hit" in message


def test_position_monitor_sends_telegram_for_position_events(monkeypatch, tmp_path) -> None:
    sent_messages = []
    sent_charts = []

    def fake_send(message: str, **_kwargs):
        sent_messages.append(message)
        return TelegramSendResult(True, "sent")

    def fake_send_chart(chart_ref, *, ticker, dashboard_url, **_kwargs):
        sent_charts.append((chart_ref, ticker, dashboard_url))
        return TelegramSendResult(True, "sent")

    monkeypatch.setattr(position_monitor, "send_telegram_message", fake_send)
    monkeypatch.setattr(position_monitor, "send_telegram_chart_photo", fake_send_chart)
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
        [({"ticker": "MSFT", "entry_price": 100, "stop_loss": 95, "target_1": 110, "target_2": 120, "chart_url": "agent_results/charts/msft.png"}, event)],
        settings=settings,
        run_id="monitor-1",
        timestamp="2026-06-22T15:32:00+00:00",
    )

    assert len(sent_messages) == 1
    assert "STOP | POSITION CLOSED" in sent_messages[0]
    assert "MSFT" in sent_messages[0]
    assert "-$15.00" in sent_messages[0]
    assert sent_charts == [("agent_results/charts/msft.png", "MSFT", "https://example.com/agent")]


def test_position_event_dedupe_key_is_stable_across_monitor_runs() -> None:
    position = {
        "ticker": "MSFT",
        "entry_date": "2026-06-22T14:30:00+00:00",
        "entry_price": 100,
        "quantity": 10,
        "stop_loss": 95,
        "target_1": 110,
        "target_2": 120,
        "decision_json": '{"trade_id":"MSFT-20260622-001"}',
    }
    first = position_monitor.PositionEvent(
        ticker="MSFT",
        action="TAKE_PARTIAL_PROFIT",
        triggered_at="2026-06-22T15:31:00+00:00",
        trigger_price=110,
        high=111,
        low=101,
        close=109,
        quantity=5,
        cash_in=550,
        note="Target 1 touched.",
    )
    second = position_monitor.PositionEvent(
        ticker="MSFT",
        action="TAKE_PARTIAL_PROFIT",
        triggered_at="2026-06-22T15:35:00+00:00",
        trigger_price=110,
        high=111,
        low=101,
        close=109,
        quantity=5,
        cash_in=550,
        note="Target 1 touched again.",
    )

    assert position_monitor.position_event_dedupe_key(position, first) == position_monitor.position_event_dedupe_key(position, second)


def test_position_monitor_sends_stop_to_entry_notification_after_tp1(monkeypatch, tmp_path) -> None:
    sent_messages = []

    def fake_send(message: str, **_kwargs):
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
        action="TAKE_PARTIAL_PROFIT",
        triggered_at="2026-06-22T15:31:00+00:00",
        trigger_price=110,
        high=111,
        low=101,
        close=109,
        quantity=5,
        cash_in=550,
        note="Target 1 touched by intraday high; taking partial profit and moving stop to breakeven.",
    )

    position_monitor.send_position_event_notifications(
        [({"ticker": "MSFT", "entry_price": 100, "quantity": 10, "stop_loss": 95, "target_1": 110, "target_2": 120}, event)],
        settings=settings,
        run_id="monitor-1",
        timestamp="2026-06-22T15:32:00+00:00",
    )

    assert len(sent_messages) == 2
    assert "TP1 | PARTIAL SOLD" in sent_messages[0]
    assert "STOP TO ENTRY" in sent_messages[1]
    assert "Old SL: $95.00 (-5.00%)" in sent_messages[1]
    assert "New SL: $100.00 (0.00%)" in sent_messages[1]


def test_monitor_notification_outbox_defers_sending_until_explicit_delivery(monkeypatch, tmp_path) -> None:
    outbox = tmp_path / "monitor-notifications.json"
    monkeypatch.setenv("MARKET_LENS_MONITOR_NOTIFICATION_OUTBOX", str(outbox))
    sent_messages = []

    def fake_send(message: str, **_kwargs):
        sent_messages.append(message)
        return TelegramSendResult(True, "sent")

    monkeypatch.setattr(position_monitor, "send_telegram_message", fake_send)
    monkeypatch.setattr(
        position_monitor,
        "send_telegram_chart_photo",
        lambda *_args, **_kwargs: TelegramSendResult(False, "no_photo"),
    )
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
        triggered_at="2026-09-10T14:31:00+00:00",
        trigger_price=95,
        high=101,
        low=94,
        close=96,
        quantity=3,
        cash_in=285,
        note="Stop loss touched by intraday low.",
        trade_id="trade-1",
    )

    position_monitor.write_notification_outbox(
        [({"ticker": "MSFT", "entry_price": 100, "quantity": 3}, event)],
        settings=settings,
        run_id="monitor-1",
        timestamp="2026-09-10T14:32:00+00:00",
    )

    assert outbox.exists()
    assert sent_messages == []
    outcomes = position_monitor.send_notification_outbox(outbox)
    assert [outcome.status for outcome in outcomes] == ["sent", "no_photo"]
    assert len(sent_messages) == 1
    assert "STOP | POSITION CLOSED" in sent_messages[0]


def test_monitor_notification_outbox_rejects_malformed_payload(monkeypatch, tmp_path) -> None:
    outbox = tmp_path / "monitor-notifications.json"
    outbox.write_text('{"schema_version":999,"events":[]}', encoding="utf-8")

    with pytest.raises(ValueError, match="schema"):
        position_monitor.send_notification_outbox(outbox)


def test_position_attention_message_is_read_only_and_includes_distance() -> None:
    message = format_position_attention_message(
        position={
            "ticker": "BA",
            "entry_price_usd": 200,
            "stop_loss": 190,
            "target_1": 215,
            "target_2": 230,
        },
        alert={
            "ticker": "BA",
            "event_type": "TAKE_PARTIAL_PROFIT",
            "label": "Target 1",
            "threshold": 215,
            "distance_pct": 0.42,
            "live_price": 213.5,
            "live_high": 214.1,
            "live_low": 212.8,
        },
        timestamp="2026-06-22T15:32:00+00:00",
        dashboard_url="https://example.com/agent",
    )

    assert "NEAR TP1 | BA" in message
    assert "BA" in message
    assert "Distance: 0.42%" in message
    assert "$215.00 (+7.50%)" in message
    assert "no portfolio change yet" in message
