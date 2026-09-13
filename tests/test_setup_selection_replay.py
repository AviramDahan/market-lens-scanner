from __future__ import annotations

from copy import deepcopy

from app.setup_selection_replay import assess_candidate, build_setup_selection_replay, candidate_rr


def decision() -> dict:
    return {
        "timestamp": "2026-09-01T14:00:00",
        "ticker": "AAA",
        "price": 100.0,
        "setup_type": "Fib 61.8 Confluence Buy Zone",
        "setup_score": 0.60,
        "minimum_setup_score_required": 0.55,
        "minimum_net_rr_required": 2.0,
        "market_regime": "BULL",
        "sector_regime": "STRONG",
        "market_session_can_open_new_buy": True,
        "market_session_reason": "Regular session is open.",
        "earnings_blackout": False,
        "correlation_warning": False,
        "cooldown_active": False,
        "entry_confirmation_passed": True,
        "confirmation_reason": "Completed candle reclaimed support.",
        "target_feasibility_status": "OK",
        "estimated_spread": 0.04,
        "estimated_slippage": 0.20,
        "estimated_fees": 0.0,
        "capital_blockers": [],
        "final_action": "BUY_SIMULATED",
        "reason": "Active buy.",
        "setup_candidates": [
            {
                "setup_type": "Fib 61.8 Confluence Buy Zone",
                "legacy_score": 0.60,
                "shadow_setup_normalized_score": 0.72,
                "buy_zone_low": 98,
                "buy_zone_high": 101,
                "stop_loss": 95,
                "target_1": 106,
                "target_2": 125,
            },
            {
                "setup_type": "VWAP Reclaim Setup",
                "legacy_score": 0.65,
                "shadow_setup_normalized_score": 0.80,
                "buy_zone_low": 99,
                "buy_zone_high": 102,
                "stop_loss": 96,
                "target_1": 106,
                "target_2": 125,
            },
        ],
    }


def test_candidate_rr_uses_executable_price_and_recorded_costs() -> None:
    record = decision()
    value = candidate_rr(record, record["setup_candidates"][1])

    assert value["theoretical_entry"] == 99
    assert value["executable_entry"] == 100
    assert value["net_rr_1"] < (106 - 100) / (100 - 96)
    assert value["execution_cost_source"] == "same_ticker_same_run_observed_costs"


def test_alternative_never_borrows_active_confirmation_or_score() -> None:
    record = decision()
    value = assess_candidate(record, record["setup_candidates"][1], active=False)
    statuses = {item["name"]: item["status"] for item in value["gate_evidence"]}

    assert statuses["professional_setup_score"] == "UNASSESSABLE"
    assert statuses["entry_confirmation"] == "UNASSESSABLE"
    assert statuses["target_feasibility"] == "UNASSESSABLE"
    assert value["counterfactual_entry_eligibility"] == "UNASSESSABLE"


def test_replay_is_read_only_and_deduplicates_first_daily_signal() -> None:
    first = decision()
    repeated = deepcopy(first)
    repeated["timestamp"] = "2026-09-01T15:00:00"
    repeated["final_action"] = "WATCH"
    later = deepcopy(first)
    later["timestamp"] = "2026-09-02T15:00:00"
    later["price"] = 104
    later["setup_candidates"] = []
    records = [repeated, later, first]
    before = deepcopy(records)

    report = build_setup_selection_replay(records)

    assert records == before
    assert report["active_trading_logic_changed"] is False
    assert report["sample"]["deduplicated_candidate_signals"] == 2
    assert {item["timestamp"] for item in report["signals"]} == {"2026-09-01T14:00:00"}
    assert all(item["observed_price_outcomes"]["return_after_1_scan_days_pct"] == 4.0 for item in report["signals"])


def test_failed_market_gate_remains_fail_even_with_high_shadow_score() -> None:
    record = decision()
    record["market_regime"] = "BEAR"
    alternative = record["setup_candidates"][1]

    value = assess_candidate(record, alternative, active=False)

    assert value["counterfactual_entry_eligibility"] == "FAIL"
    assert next(item for item in value["gate_evidence"] if item["name"] == "market_regime")["status"] == "FAIL"
