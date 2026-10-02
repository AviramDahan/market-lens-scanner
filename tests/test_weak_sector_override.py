from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pandas as pd

from app.weak_sector_override import (
    build_measurement_summary,
    evaluate_weak_sector_override_v1,
    load_observations,
    measurement_summary_markdown,
    persist_first_observations,
)


SECTOR_BLOCKER = "WATCH: Sector regime is WEAK; do not auto-buy weak sector setups."


def decision(*, ticker: str = "TEST", sector: str = "WEAK") -> dict:
    final_action = "WATCH" if sector == "WEAK" else "BUY_SIMULATED"
    return {
        "timestamp": "2026-10-02T10:15:00-04:00",
        "market_session_timestamp": "2026-10-02T10:15:00-04:00",
        "market_session_phase": "REGULAR",
        "market_session_can_open_new_buy": True,
        "ticker": ticker,
        "company_name": "Test Inc.",
        "setup_type": "Breakout + Retest",
        "setup_score": 0.62,
        "setup_score_bucket": "0.60-0.69",
        "entry_mode": "standard",
        "initial_action": "BUY_SIMULATED",
        "final_action": final_action,
        "reason": "Active decision remains authoritative.",
        "market_regime": "BULL",
        "market_regime_allows_new_buys": True,
        "sector": "Technology",
        "sector_regime": sector,
        "sector_score": 72.0,
        "minimum_setup_score_required": 0.45,
        "minimum_primary_net_rr_required": 0.80,
        "minimum_net_rr_required": 2.0,
        "normalized_quality_score": 63.0,
        "net_rr_1": 1.25,
        "net_rr_2": 4.0,
        "weighted_net_rr": 2.4,
        "net_rr": 2.4,
        "target_feasibility_status": "OK",
        "entry_confirmation_passed": True,
        "confirmation_freshness_status": "FRESH_SAME_SESSION",
        "earnings_blackout": False,
        "earnings_date": "2026-11-15",
        "days_to_earnings": 30,
        "cooldown_active": False,
        "cooldown_exception_used": False,
        "correlation_warning": False,
        "sector_exposure_limit_exceeded": False,
        "sector_exposure_before": 2_000.0,
        "sector_exposure_after": 3_001.0,
        "sector_exposure_cap": 20_000.0,
        "factor_exposure_limit_exceeded": False,
        "factor_tags": ["Mega Cap Tech"],
        "factor_exposure_before": {"Mega Cap Tech": 2_000.0},
        "factor_exposure_after": {"Mega Cap Tech": 3_001.0},
        "factor_exposure_cap": 25_000.0,
        "entry_gate_blockers": [SECTOR_BLOCKER] if sector == "WEAK" else [],
        "capital_blockers": [],
        "adjusted_position_size": 10,
        "adjusted_cash_out": 1001.0,
        "adjusted_risk_amount": 102.0,
        "cash_available": 90_000.0,
        "portfolio_exposure_before": 10_000.0,
        "dynamic_exposure_limit": 60_000.0,
        "portfolio_heat_before": 400.0,
        "portfolio_heat_cap": 2_500.0,
        "strategy_version": "qualified_selection_v1",
        "execution_model_version": "execution_v1",
        "entry_signal_price": 100.0,
        "entry_execution_price": 100.1,
        "stop_loss": 90.0,
        "target_1": 112.8,
        "target_2": 140.5,
        "execution_cost_policy": {
            "half_spread_per_share": 0.01,
            "slippage_per_share": 0.04,
            "fee_per_share": 0.0,
        },
        "estimated_spread": 0.02,
        "estimated_slippage": 0.08,
        "estimated_fees": 0.0,
    }


def test_qualifying_signal_is_recorded_once(tmp_path: Path) -> None:
    first = decision()
    repeated = deepcopy(first)
    repeated["timestamp"] = "2026-10-02T11:15:00-04:00"
    repeated["market_session_timestamp"] = repeated["timestamp"]
    output = tmp_path / "observations.jsonl"

    result = persist_first_observations([repeated, first], output)
    second_result = persist_first_observations([first], output)
    rows = load_observations(output)

    assert result["signals_added"] == 1
    assert second_result["added"] == 0
    assert len(rows) == 1
    assert rows[0]["signal_eligible"] is True
    assert rows[0]["timestamp"] == first["timestamp"]


def test_additional_blocker_prevents_signal() -> None:
    record = decision()
    record["entry_gate_blockers"].append("WATCH: Entry confirmation failed.")

    result = evaluate_weak_sector_override_v1(record)

    assert result["signal_eligible"] is False
    assert result["status"] == "INELIGIBLE"
    assert "FAIL:no_other_persisted_entry_blocker" in result["ineligibility_reasons"]


def test_missing_gate_is_unassessable_not_pass() -> None:
    record = decision()
    record.pop("adjusted_position_size")

    result = evaluate_weak_sector_override_v1(record)

    assert result["signal_eligible"] is False
    assert result["status"] == "UNASSESSABLE"
    assert "UNASSESSABLE:position_sizing" in result["ineligibility_reasons"]


def test_experiment_does_not_mutate_active_decision_or_portfolio_fields() -> None:
    record = decision()
    before = deepcopy(record)

    result = evaluate_weak_sector_override_v1(record)

    assert record == before
    assert record["final_action"] == "WATCH"
    assert result["active_decision_changed"] is False
    assert result["portfolio_mutation_allowed"] is False


def test_standard_and_pilot_paths_are_not_mixed() -> None:
    record = decision()
    record["entry_mode"] = "neutral_pilot"
    record["neutral_pilot_enabled"] = True
    record["neutral_pilot_min_setup_score"] = 0.45
    record["neutral_pilot_min_net_rr"] = 2.0
    record["neutral_pilot_trades_today"] = 2
    record["neutral_pilot_max_trades_per_day"] = 2

    result = evaluate_weak_sector_override_v1(record)

    assert result["signal_eligible"] is False
    assert "FAIL:pilot_daily_limit" in result["ineligibility_reasons"]
    assert result["entry_path"] == "NEUTRAL_PILOT"


def test_summary_reports_zero_and_reasons_without_loosening() -> None:
    blocked = decision()
    blocked["normalized_quality_score"] = 20.0
    blocked["entry_gate_blockers"].append("WATCH: Normalized quality is too low.")
    observation = evaluate_weak_sector_override_v1(blocked)

    summary = build_measurement_summary([observation])
    markdown = measurement_summary_markdown(summary)

    assert summary["qualifying_signal_count"] == 0
    assert summary["review_ready"] is False
    assert summary["weak_ineligibility_reasons"]["FAIL:normalized_quality"] == 1
    assert "Qualifying signals: 0" in markdown


def test_no_trade_observation_is_not_persisted_or_counted(tmp_path: Path) -> None:
    no_trade = decision()
    no_trade["setup_type"] = "No Trade"
    no_trade["initial_action"] = "SKIP"
    no_trade["final_action"] = "SKIP"
    output = tmp_path / "observations.jsonl"

    persisted = persist_first_observations([no_trade], output)
    historical = evaluate_weak_sector_override_v1(no_trade)
    historical.pop("applicable")
    summary = build_measurement_summary([historical])

    assert persisted["considered"] == 0
    assert persisted["added"] == 0
    assert output.exists() is False
    assert summary["observation_count"] == 1
    assert summary["applicable_observation_count"] == 0
    assert summary["qualifying_signal_count"] == 0


def test_strong_control_requires_same_explicit_gates() -> None:
    result = evaluate_weak_sector_override_v1(decision(ticker="CTRL", sector="STRONG"))

    assert result["control_eligible"] is True
    assert result["signal_eligible"] is False
    assert result["cohort"] == "STRONG_CONTROL"


def test_summary_keeps_signal_and_matching_control_outcomes_separate() -> None:
    signal = evaluate_weak_sector_override_v1(decision())
    control = evaluate_weak_sector_override_v1(decision(ticker="CTRL", sector="STRONG"))
    index = pd.DatetimeIndex(
        ["2026-10-02T14:20:00+00:00", "2026-10-02T14:25:00+00:00"]
    )
    bars = pd.DataFrame(
        [
            {"Open": 100.0, "High": 113.0, "Low": 99.0, "Close": 112.0},
            {"Open": 113.0, "High": 141.0, "Low": 101.0, "Close": 140.0},
        ],
        index=index,
    )

    summary = build_measurement_summary([signal, control], {"TEST": bars, "CTRL": bars})

    assert summary["qualifying_signal_count"] == 1
    assert summary["matched_control_count"] == 1
    assert summary["signal_results"]["closed"] == 1
    assert summary["control_results"]["closed"] == 1
    assert summary["portfolio_return_claimed"] is False
    assert summary["signal_results"]["average_mfe_r"] is not None
