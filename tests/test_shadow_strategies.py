from __future__ import annotations

from types import SimpleNamespace

from app.shadow_strategies import STRATEGY_NAMES, evaluate_shadow_strategies


def base_result() -> SimpleNamespace:
    return SimpleNamespace(
        ticker="TEST",
        setup_type="Breakout + Retest",
        score=0.62,
        current_price=100.0,
        buy_zone_low=99.0,
        buy_zone_high=101.0,
        stop_loss=96.0,
        target_1=106.0,
        target_2=112.0,
        risk_reward=2.5,
    )


def base_decision() -> dict:
    return {
        "ticker": "TEST",
        "final_action": "WATCH",
        "setup_type": "Breakout + Retest",
        "setup_score": 0.62,
        "market_regime": "BULL",
        "sector_regime": "STRONG",
        "sector_score": 80,
        "normalized_quality_score": 82,
        "normalized_momentum_score": 78,
        "entry_confirmation_passed": True,
        "vwap_reclaimed": True,
        "close_above_trigger": True,
        "earnings_blackout": False,
        "executable_entry": 100,
        "stop_loss": 96,
        "target_1": 106,
        "target_2": 112,
        "net_rr_1": 1.5,
        "net_rr_2": 3.0,
        "target_1_atr_distance": 1.2,
        "target_feasibility_status": "OK",
    }


def test_shadow_strategies_return_stable_schema() -> None:
    records = evaluate_shadow_strategies(base_result(), base_decision())

    assert [record["name"] for record in records] == list(STRATEGY_NAMES)
    for record in records:
        assert record["version"] == "shadow_v1"
        assert isinstance(record["would_buy"], bool)
        assert 0 <= record["confidence"] <= 1
        assert "reason" in record
        assert "warnings" in record


def test_shadow_would_buy_does_not_change_active_final_action() -> None:
    decision = base_decision()
    original_action = decision["final_action"]
    records = evaluate_shadow_strategies(base_result(), decision)

    assert any(record["would_buy"] for record in records)
    assert decision["final_action"] == original_action


def test_shadow_missing_data_fails_closed_without_exception() -> None:
    decision = {"ticker": "TEST", "final_action": "SKIP"}
    records = evaluate_shadow_strategies(base_result(), decision)

    assert len(records) == len(STRATEGY_NAMES)
    assert all(record["would_buy"] is False for record in records)
    assert all(record["reason"] for record in records)
