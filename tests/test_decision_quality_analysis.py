from __future__ import annotations

import pandas as pd

from app.decision_quality_analysis import (
    entry_path_assessment,
    sector_only_shadow_flips,
    simulate_fixed_plan,
    unique_weak_sector_opportunities,
)


def decision(**overrides):
    value = {
        "timestamp": "2026-09-17T15:00:00+00:00",
        "market_session_timestamp": "2026-09-17T11:00:00-04:00",
        "ticker": "TEST",
        "setup_type": "VWAP Reclaim Setup",
        "sector_regime": "WEAK",
        "market_regime": "NEUTRAL",
        "market_regime_allows_new_buys": True,
        "market_session_phase": "REGULAR",
        "market_session_can_open_new_buy": True,
        "setup_score": 0.60,
        "minimum_setup_score_required": 0.55,
        "neutral_pilot_min_setup_score": 0.45,
        "normalized_quality_score": 70,
        "net_rr_1": 1.2,
        "weighted_net_rr": 2.8,
        "net_rr": 2.8,
        "minimum_net_rr_required": 2.5,
        "neutral_pilot_min_net_rr": 2.0,
        "entry_confirmation_passed": True,
        "confirmation_freshness_status": "FRESH_SAME_SESSION",
        "earnings_blackout": False,
        "target_feasibility_status": "OK",
        "cooldown_active": False,
        "correlation_warning": False,
        "neutral_pilot_enabled": True,
        "neutral_pilot_trades_today": 0,
        "neutral_pilot_max_trades_per_day": 2,
        "entry_gate_evaluated": False,
        "pre_cap_position_size": 0,
        "executable_entry": 100.0,
        "stop_loss": 95.0,
        "target_1": 106.0,
        "target_2": 112.0,
        "entry_cost_per_share": 0.2,
        "execution_cost_policy": {
            "half_spread_per_share": 0.05,
            "slippage_per_share": 0.10,
            "fee_per_share": 0.0,
        },
    }
    value.update(overrides)
    return value


def test_unique_weak_sector_opportunities_keeps_first_regular_observation() -> None:
    first = decision()
    later = decision(timestamp="2026-09-17T15:30:00+00:00", setup_score=0.9)
    off_hours = decision(
        ticker="OFF",
        timestamp="2026-09-17T12:00:00+00:00",
        market_session_timestamp="2026-09-17T08:00:00-04:00",
        market_session_phase="PRE_MARKET",
    )

    selected = unique_weak_sector_opportunities([later, off_hours, first])

    assert selected == [first]


def test_missing_sizing_is_not_treated_as_sector_only_pass() -> None:
    result = entry_path_assessment(decision(), path="STANDARD")

    assert result["classification"] == "SECTOR_ONLY_UNASSESSABLE"
    assert result["failed_non_sector"] == []
    assert result["not_evaluated_non_sector"] == ["position_sizing"]


def test_score_failure_is_multi_fail_even_when_sector_is_weak() -> None:
    result = entry_path_assessment(decision(setup_score=0.40), path="STANDARD")

    assert result["classification"] == "MULTI_FAIL"
    assert "setup_score" in result["failed_non_sector"]


def test_shadow_flip_changes_only_sector_and_preserves_active_record() -> None:
    source = decision()
    original = dict(source)

    flips = sector_only_shadow_flips([source])

    assert len(flips) == 1
    assert flips[0]["shadow_strategies"] == ["VWAP_RECLAIM"]
    assert source == original


def test_fixed_plan_uses_stop_first_when_same_bar_touches_stop_and_target() -> None:
    index = pd.DatetimeIndex(["2026-09-17T15:05:00+00:00"])
    bars = pd.DataFrame(
        [{"Open": 99.0, "High": 107.0, "Low": 94.0, "Close": 100.0}],
        index=index,
    )

    outcome = simulate_fixed_plan({"decision": decision()}, bars)

    assert outcome["status"] == "CLOSED"
    assert [event["action"] for event in outcome["events"]] == ["EXIT_STOP"]
    assert outcome["realized_r"] <= -1


def test_fixed_plan_takes_partial_then_moves_stop_to_entry() -> None:
    index = pd.DatetimeIndex(["2026-09-17T15:05:00+00:00", "2026-09-17T15:10:00+00:00"])
    bars = pd.DataFrame(
        [
            {"Open": 101.0, "High": 106.5, "Low": 100.5, "Close": 106.0},
            {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0},
        ],
        index=index,
    )

    outcome = simulate_fixed_plan({"decision": decision()}, bars)

    assert outcome["status"] == "CLOSED"
    assert [event["action"] for event in outcome["events"]] == [
        "TAKE_PARTIAL_PROFIT",
        "EXIT_STOP",
    ]
    assert outcome["realized_per_share"] > 0
