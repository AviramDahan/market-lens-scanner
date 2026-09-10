from datetime import date

from app.performance_summary import shadow_horizon_dates, shadow_outcome_metrics


def rows(next_date):
    return [
        {"timestamp": "2026-08-07T15:00:00Z", "ticker": "TEST", "price": 100,
         "shadow_strategies": [{"name": "SHADOW", "version": "v1", "would_buy": True, "entry_price": 100}]},
        {"timestamp": next_date + "T15:00:00Z", "ticker": "TEST", "price": 103},
    ]


def test_horizon_skips_holiday_and_weekend():
    assert shadow_horizon_dates(date(2026, 9, 4))[1] == date(2026, 9, 8)


def test_missing_monday_is_not_replaced_by_tuesday_as_one_day():
    metrics = shadow_outcome_metrics(rows("2026-08-11"))
    table = metrics["by_strategy"]["SHADOW"]
    assert table["matured_1d"] == 0
    assert table["average_return_1d_pct"] is None
    assert metrics["best_strategy"] == "INSUFFICIENT_OUTCOMES"


def test_exact_target_date_counts_observation():
    metrics = shadow_outcome_metrics(rows("2026-08-10"))
    table = metrics["by_strategy"]["SHADOW"]
    assert table["matured_1d"] == 1
    assert table["average_return_1d_pct"] == 3.
    assert metrics["ranking_horizon_sessions"] == 1
