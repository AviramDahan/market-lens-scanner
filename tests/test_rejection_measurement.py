from copy import deepcopy

from app.performance_summary import reason_counter, rejection_diagnostics


def test_decimal_reasons_are_not_truncated_or_merged():
    reasons = ["WATCH: Net R/R 1.20 below 2.50.", "WATCH: Net R/R 1.95 below 2.50."]
    records = [{"final_action": "WATCH", "reason": r} for r in reasons]
    assert reason_counter(records) == dict.fromkeys(reasons, 1)


def test_complete_reason_including_late_capital_explanation_is_preserved():
    reason = "WATCH: " + "Entry confirmation failed; " * 10 + "Also: exposure limit 28750.00."
    assert reason in reason_counter([{"final_action": "WATCH", "reason": reason}])


def test_blockers_are_observations_not_unique_opportunities_and_read_only():
    record = dict(ticker="MSFT", final_action="SKIP", entry_gate_blockers=["score", "score"],
                  capital_blockers=["exposure"])
    records = [deepcopy(record), deepcopy(record), dict(ticker="OLD", final_action="SKIP"),
               dict(ticker="BUY", final_action="BUY_SIMULATED", capital_blockers=["ignored"])]
    before = deepcopy(records)
    summary = rejection_diagnostics(records)
    assert summary["rejected_observations"] == 3
    assert summary["unique_rejected_tickers"] == 2
    assert summary["observations_without_recorded_blockers"] == 1
    assert summary["capital_blockers"] == [{"name": "exposure", "count": 2}]
    assert summary["entry_gate_blockers"] == [{"name": "score", "count": 2}]
    assert records == before


def test_empty_or_malformed_blockers_do_not_imply_pass():
    records = [dict(final_action="WATCH", capital_blockers="bad", entry_gate_blockers=[None, ""])]
    assert rejection_diagnostics(records)["observations_without_recorded_blockers"] == 1
    assert rejection_diagnostics([])["rejected_observations"] == 0
