from datetime import datetime

import pandas as pd
import pytest

from agent.market_lens_ui_agent import agent_market_session
from app.agent_risk import calculate_confirmation_freshness, calculate_entry_confirmation_from_frame
from app.setups import _regular_market_is_open
from app.trading_clock import bar_close_time, completed_frame, session_status


def at(value):
    return datetime.fromisoformat(value)


@pytest.mark.parametrize("stamp,regular", [
    ("2026-03-06T14:30:00+00:00", True),
    ("2026-03-09T13:29:59+00:00", False),
    ("2026-03-09T13:30:00+00:00", True),
    ("2026-11-02T14:30:00+00:00", True),
    ("2026-11-27T17:59:59+00:00", True),
    ("2026-11-27T18:00:00+00:00", False),
    ("2026-09-07T14:00:00+00:00", False),
    ("2026-09-08T20:00:00+00:00", False),
])
def test_all_entry_clock_consumers_agree(stamp, regular):
    now = at(stamp)
    assert session_status(now)["regular_session_open"] is regular
    assert _regular_market_is_open(now) is regular
    assert (agent_market_session(now) == "regular") is regular


def test_calendar_failure_blocks_even_with_off_hours_override(monkeypatch):
    def unavailable(_):
        raise RuntimeError("unavailable")
    monkeypatch.setattr("app.trading_clock.session_bounds", unavailable)
    status = session_status(at("2026-09-08T10:00:00-04:00"), allow_off_hours_buys=True)
    assert status["phase"] == "UNKNOWN"
    assert not status["can_open_new_buy"]


def test_naive_observation_is_not_guessed():
    assert not session_status(at("2026-09-08T10:00:00"))["can_open_new_buy"]


@pytest.mark.parametrize("stamp,timeframe,expected", [
    ("2026-11-27T12:30:00-05:00", "60m_completed", "2026-11-27T18:00:00+00:00"),
    ("2026-11-27", "1d_completed", "2026-11-27T18:00:00+00:00"),
    ("2026-09-08T10:00:00-04:00", "30m_completed", "2026-09-08T14:30:00+00:00"),
])
def test_bar_close_contract(stamp, timeframe, expected):
    assert bar_close_time(stamp, timeframe) == at(expected)


def confirmation(stamp, timeframe="30m_completed"):
    return {"confirmation_candle_timestamp": stamp, "confirmation_timeframe": timeframe}


@pytest.mark.parametrize("stamp,expected", [
    ("2026-09-08T10:00:00-04:00", "FRESH_SAME_SESSION"),
    ("2026-09-08T10:30:00-04:00", "INCOMPLETE_OR_FUTURE"),
    ("2026-09-08T11:00:00-04:00", "INCOMPLETE_OR_FUTURE"),
    ("2026-09-04T15:30:00-04:00", "STALE_PREVIOUS_SESSION"),
    ("2026-09-08T08:00:00-04:00", "UNKNOWN"),
    ("2026-09-08T10:00:00", "UNKNOWN"),
    ("bad", "UNKNOWN"),
    ("", "UNKNOWN"),
])
def test_confirmation_freshness_contract(stamp, expected):
    session = session_status(at("2026-09-08T10:30:00-04:00"))
    assert calculate_confirmation_freshness(confirmation(stamp), session)["status"] == expected


def test_stale_same_day_uses_existing_configured_lookback():
    session = session_status(at("2026-09-08T12:00:00-04:00"))
    value = confirmation("2026-09-08T10:00:00-04:00")
    assert calculate_confirmation_freshness(value, session, lookback_candles=3)["status"] == "STALE_INTRADAY"
    assert calculate_confirmation_freshness(value, session, lookback_candles=4)["status"] == "FRESH_SAME_SESSION"


def test_previous_daily_bar_is_off_hours_information_only():
    value = confirmation("2026-09-04", "1d_completed")
    pre = session_status(at("2026-09-08T08:00:00-04:00"))
    regular = session_status(at("2026-09-08T10:00:00-04:00"))
    assert calculate_confirmation_freshness(value, pre)["status"] == "OFF_HOURS_REFERENCE"
    assert calculate_confirmation_freshness(value, regular)["status"] == "STALE_PREVIOUS_SESSION"


def candles():
    return pd.DataFrame([
        {"Open": 97, "High": 99, "Low": 96, "Close": 98, "Volume": 1000},
        {"Open": 99, "High": 103, "Low": 99, "Close": 102, "Volume": 1000},
        {"Open": 99, "High": 104, "Low": 99, "Close": 103, "Volume": 1000},
    ], index=pd.date_range("2026-09-08T09:30:00-04:00", periods=3, freq="30min"))


@pytest.mark.parametrize("drop_last", [True, False])
def test_keep_last_closed_bar_and_reject_live_bar(drop_last):
    from types import SimpleNamespace
    frame = candles()
    args = dict(result=SimpleNamespace(setup_type="Breakout + Retest"), frame=frame,
                buy_low=98, buy_high=100, timeframe="30m_completed", drop_last=drop_last)
    before = calculate_entry_confirmation_from_frame(**args, now=at("2026-09-08T10:45:00-04:00"))
    after = calculate_entry_confirmation_from_frame(**args, now=at("2026-09-08T11:00:00-04:00"))
    assert before["confirmation_candle_timestamp"].startswith("2026-09-08T10:00")
    assert after["confirmation_candle_timestamp"].startswith("2026-09-08T10:30")
    assert before["entry_confirmation_passed"] and after["entry_confirmation_passed"]


def test_opening_bar_cannot_confirm_before_it_closes():
    assert completed_frame(candles(), "30m_completed", at("2026-09-08T09:45:00-04:00")).empty


def test_two_completed_bars_do_not_require_a_live_third_row():
    from types import SimpleNamespace
    value = calculate_entry_confirmation_from_frame(
        result=SimpleNamespace(setup_type="Breakout + Retest"), frame=candles().iloc[:2],
        buy_low=98, buy_high=100, timeframe="30m_completed", drop_last=True,
        now=at("2026-09-08T10:30:00-04:00"),
    )
    assert value["entry_confirmation_passed"]
    assert "10:00:00" in value["confirmation_candle_timestamp"]


def test_ambiguous_order_is_rejected():
    with pytest.raises(ValueError):
        completed_frame(candles().iloc[::-1], "30m_completed", at("2026-09-08T11:00:00-04:00"))


def test_daily_live_bar_waits_for_early_close():
    frame = pd.DataFrame({"Close": [1, 2]}, index=pd.to_datetime(["2026-11-25", "2026-11-27"]))
    assert len(completed_frame(frame, "1d_completed", at("2026-11-27T12:59:59-05:00"))) == 1
    assert len(completed_frame(frame, "1d_completed", at("2026-11-27T13:00:00-05:00"))) == 2
