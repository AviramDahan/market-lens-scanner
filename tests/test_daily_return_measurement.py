import json
from datetime import date

import pytest

from app.performance_summary import daily_return_metrics, write_performance_summaries


def prior(tmp_path, equity):
    path = tmp_path / "daily_summary_2026-09-08.json"
    path.write_text(json.dumps({"total_portfolio_value": equity}), encoding="utf-8")
    return path


def test_daily_return_is_not_lifetime_return(tmp_path):
    prior(tmp_path, 101560.42)
    result = daily_return_metrics(tmp_path, date(2026, 9, 9),
                                  {"total_portfolio_value": 101586.53, "starting_capital": 100000})
    assert result["daily_return_pct"] == pytest.approx(.0257)
    assert result["cumulative_return_pct"] == pytest.approx(1.5865)


def test_unchanged_equity_has_zero_daily_return(tmp_path):
    prior(tmp_path, 101500)
    assert daily_return_metrics(tmp_path, date(2026, 9, 9), {"total_portfolio_value": 101500})["daily_return_pct"] == 0


def test_missing_reference_is_unknown_not_zero_or_lifetime(tmp_path):
    result = daily_return_metrics(tmp_path, date(2026, 9, 9),
                                  {"total_portfolio_value": 101500, "daily_return_pct": 1.5})
    assert result["daily_return_pct"] is None
    assert result["daily_return_status"] == "MISSING_PRIOR_DAY_EQUITY"


@pytest.mark.parametrize("equity", [None, -1, float("nan"), float("inf"), "bad", 0])
def test_invalid_reference_does_not_invent_return(tmp_path, equity):
    prior(tmp_path, equity)
    assert daily_return_metrics(tmp_path, date(2026, 9, 9), {"total_portfolio_value": 100000})["daily_return_pct"] is None


def test_zero_current_equity_is_complete_loss(tmp_path):
    prior(tmp_path, 100000)
    assert daily_return_metrics(tmp_path, date(2026, 9, 9), {"total_portfolio_value": 0})["daily_return_pct"] == -100


def test_reference_with_wrong_embedded_date_is_rejected(tmp_path):
    path = prior(tmp_path, 100000)
    path.write_text(json.dumps({"date": "2026-09-10", "total_portfolio_value": 100000}), encoding="utf-8")
    assert daily_return_metrics(tmp_path, date(2026, 9, 9), {"total_portfolio_value": 101000})["daily_return_pct"] is None


def test_writer_preserves_inputs_and_prior_snapshot_and_stable_daily_anchor(tmp_path):
    reference = prior(tmp_path, 100000)
    before = reference.read_bytes()
    portfolio = {"total_portfolio_value": 101000, "daily_return_pct": 50, "starting_capital": 100000}
    paths = write_performance_summaries(summary_dir=tmp_path, decision_dir=tmp_path / "decisions",
                                       current_decision_path=tmp_path / "missing.jsonl", run_id="test",
                                       timestamp="2026-09-09T14:00:00Z", portfolio=portfolio)
    first = json.loads(paths["daily_summary_json"].read_text())
    assert first["daily_return_pct"] == 1
    assert portfolio["daily_return_pct"] == 50
    portfolio["total_portfolio_value"] = 102000
    write_performance_summaries(summary_dir=tmp_path, decision_dir=tmp_path / "decisions",
                                current_decision_path=tmp_path / "missing.jsonl", run_id="test2",
                                timestamp="2026-09-09T15:00:00Z", portfolio=portfolio)
    second = json.loads(paths["daily_summary_json"].read_text())
    assert second["daily_return_pct"] == 2
    assert reference.read_bytes() == before
