"""Desired safety contracts, currently known to fail on the audited baseline.

Strict xfail keeps known defects visible without pretending they are fixed.
Use --runxfail to reproduce failures; remove a marker only with its repair.
All data is synthetic and no portfolio or network is used.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from agent.position_monitor import MonitorSettings, monitor_position
from app.agent_risk import market_session_status
from app.indicators import compute_relative_strength
from app.strategy import StrategyCandidate, decide_strategy_candidate


@pytest.mark.parametrize("day", ["2026-09-07", "2026-11-26"])
def test_exchange_holiday_cannot_open_new_position(day):
    now = datetime.fromisoformat(f"{day}T10:00:00").replace(tzinfo=ZoneInfo("America/New_York"))
    assert market_session_status(now)["can_open_new_buy"] is False


@pytest.mark.parametrize("day", ["2026-11-27", "2026-12-24"])
def test_after_exchange_early_close_cannot_open_new_position(day):
    now = datetime.fromisoformat(f"{day}T14:00:00-05:00")
    assert market_session_status(now)["can_open_new_buy"] is False


def test_regular_session_close_is_exclusive():
    now = datetime.fromisoformat("2026-09-08T16:00:00-04:00")
    assert market_session_status(now)["can_open_new_buy"] is False


def test_regular_session_positive_control():
    now = datetime.fromisoformat("2026-09-08T10:00:00-04:00")
    assert market_session_status(now)["can_open_new_buy"] is True


def test_weekend_negative_control():
    now = datetime.fromisoformat("2026-09-06T10:00:00-04:00")
    assert market_session_status(now)["can_open_new_buy"] is False


def test_existing_position_uses_its_own_targets():
    candidate = StrategyCandidate("TEST", "Fib", 0.6, 111, 99, 112, 95, 105, 110, 2.4)
    position = {"quantity": 10, "entry_price": 100, "stop_loss": 95,
                "target_1": 120, "target_2": 130, "partial_taken": False}
    decision = decide_strategy_candidate(
        candidate, open_positions={"TEST": position}, cash=99_000, exposure=1_000,
        currency_rate=1, max_position=10_000, max_total_exposure=40_000,
        max_risk=1_000, min_rr=2, sector_map={}, sector_health={},
    )
    assert decision.action == "HOLD"


@pytest.mark.xfail(strict=True, reason="AUDIT-SIGNAL-01: negative benchmark reverses RS ordering")
def test_larger_loss_is_not_stronger_than_smaller_loss():
    index = pd.date_range("2026-01-01", periods=21, tz="UTC")
    benchmark = pd.Series([100.0] * 20 + [95.0], index=index).pct_change().dropna()
    loser = pd.DataFrame({"Close": [100.0] * 20 + [90.0]}, index=index)
    leader = pd.DataFrame({"Close": [100.0] * 20 + [98.0]}, index=index)
    assert compute_relative_strength(leader, benchmark) > compute_relative_strength(loser, benchmark)


def test_excursion_excludes_prices_after_exit(monkeypatch, tmp_path):
    frame = pd.DataFrame(
        {"Open": [100, 94, 109], "High": [101, 100, 150],
         "Low": [99, 90, 108], "Close": [100, 94, 140]},
        index=pd.to_datetime(["2026-09-04T14:00:00Z", "2026-09-04T14:01:00Z",
                              "2026-09-04T14:02:00Z"]),
    )
    monkeypatch.setattr("agent.position_monitor.fetch_intraday_frame", lambda *args, **kwargs: frame)
    position = dict(ticker="TEST", entry_price=100, quantity=10, stop_loss=95,
                    entry_date="2026-09-04T14:00:30Z",
                    target_1=110, target_2=120, partial_taken=False)
    settings = MonitorSettings(excel_path=Path(tmp_path / "unused.xlsx"), run_dir=tmp_path,
                               period="5d", interval="1m", save_noop=False, dashboard_url="")
    result = monitor_position(position, settings=settings,
                              since=datetime(2026, 9, 4, 14, tzinfo=timezone.utc), currency_rate=1)
    assert result.event.action == "EXIT_STOP"
    assert json.loads(position["decision_json"])["mfe"] == 0
