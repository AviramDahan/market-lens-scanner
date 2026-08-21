from __future__ import annotations

import json
from pathlib import Path

from app.performance_summary import write_performance_summaries


def write_decisions(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


def sample_records() -> list[dict]:
    return [
        {
            "timestamp": "2026-06-24T15:30:00",
            "ticker": "AAA",
            "final_action": "WATCH",
            "setup_type": "Breakout + Retest",
            "setup_score": 0.58,
            "setup_score_bucket": "0.50-0.59",
            "market_regime": "NEUTRAL",
            "sector_regime": "STRONG",
            "sector": "Technology",
            "net_rr": 1.9,
            "net_rr_1": 1.1,
            "net_rr_2": 3.0,
            "reason": "WATCH: Needs confirmation.",
            "warnings": ["Entry confirmation missing"],
            "shadow_strategies": [
                {
                    "name": "BREAKOUT_CONTINUATION",
                    "version": "shadow_v1",
                    "would_buy": True,
                    "confidence": 0.86,
                    "reason": "Shadow pass.",
                    "warnings": [],
                }
            ],
        },
        {
            "timestamp": "2026-06-24T15:30:00",
            "ticker": "BBB",
            "final_action": "SKIP",
            "setup_type": "No Trade",
            "setup_score": 0.0,
            "setup_score_bucket": "<0.40",
            "market_regime": "NEUTRAL",
            "sector_regime": "NEUTRAL",
            "sector": "Healthcare",
            "net_rr": 0.0,
            "net_rr_1": 0.0,
            "net_rr_2": 0.0,
            "reason": "SKIP: No Trade result.",
            "warnings": ["Target ATR feasibility unavailable."],
            "shadow_strategies": [],
        },
    ]


def test_daily_and_weekly_summaries_are_created_without_trades(tmp_path: Path) -> None:
    decision_dir = tmp_path / "decisions"
    summary_dir = tmp_path / "summaries"
    decision_path = decision_dir / "market_lens_agent_20260624_153000.jsonl"
    write_decisions(decision_path, sample_records())

    paths = write_performance_summaries(
        summary_dir=summary_dir,
        decision_dir=decision_dir,
        current_decision_path=decision_path,
        run_id="20260624_153000",
        timestamp="2026-06-24T15:30:00",
        portfolio={
            "open_positions_start": 0,
            "open_positions_end": 0,
            "total_portfolio_value": 100000,
            "daily_return_pct": 0,
        },
    )

    assert paths["daily_summary_json"].exists()
    assert paths["weekly_summary_json"].exists()
    assert paths["daily_summary_md"].exists()
    assert paths["weekly_summary_md"].exists()

    daily = json.loads(paths["daily_summary_json"].read_text(encoding="utf-8"))
    weekly = json.loads(paths["weekly_summary_json"].read_text(encoding="utf-8"))

    assert daily["total_scans"] == 1
    assert daily["total_tickers_scanned"] == 2
    assert daily["BUY_SIMULATED_count"] == 0
    assert daily["shadow_strategies_would_buy_count_by_strategy"]["BREAKOUT_CONTINUATION"] == 1
    assert weekly["total_scans"] == 1
    assert weekly["total_BUY_SIMULATED"] == 0


def test_summary_generation_does_not_mutate_decisions(tmp_path: Path) -> None:
    decision_dir = tmp_path / "decisions"
    summary_dir = tmp_path / "summaries"
    decision_path = decision_dir / "market_lens_agent_20260624_153000.jsonl"
    records = sample_records()
    before = json.loads(json.dumps(records))
    write_decisions(decision_path, records)

    write_performance_summaries(
        summary_dir=summary_dir,
        decision_dir=decision_dir,
        current_decision_path=decision_path,
        run_id="20260624_153000",
        timestamp="2026-06-24T15:30:00",
        portfolio={},
    )

    assert records == before


def test_summary_infers_period_start_positions_from_actions(tmp_path: Path) -> None:
    decision_dir = tmp_path / "decisions"
    summary_dir = tmp_path / "summaries"
    decision_path = decision_dir / "market_lens_agent_20260714_133000.jsonl"
    records = sample_records() + [
        {
            "timestamp": "2026-07-14T13:39:55",
            "ticker": "GILD",
            "final_action": "BUY_SIMULATED",
            "setup_type": "Fib 61.8 Confluence Buy Zone",
            "setup_score": 0.57,
            "sector": "Healthcare",
            "net_rr": 3.67,
            "net_rr_1": 3.4,
            "net_rr_2": 4.77,
            "reason": "BUY_SIMULATED: valid setup.",
            "warnings": [],
            "shadow_strategies": [],
        }
    ]
    write_decisions(decision_path, records)

    paths = write_performance_summaries(
        summary_dir=summary_dir,
        decision_dir=decision_dir,
        current_decision_path=decision_path,
        run_id="20260714_133000",
        timestamp="2026-07-14T13:30:00",
        portfolio={
            "open_positions_start": 3,
            "open_positions_end": 3,
            "total_portfolio_value": 100400,
        },
    )

    daily = json.loads(paths["daily_summary_json"].read_text(encoding="utf-8"))

    assert daily["positions_opened_today"] == 1
    assert daily["open_positions_end"] == 3
    assert daily["open_positions_start"] == 2


def test_summary_counts_staged_watch_ready_without_counting_skips(tmp_path: Path) -> None:
    decision_dir = tmp_path / "decisions"
    summary_dir = tmp_path / "summaries"
    decision_path = decision_dir / "market_lens_agent_20260728_063000.jsonl"
    records = [
        {
            "timestamp": "2026-07-28T06:30:00",
            "ticker": "READY",
            "final_action": "WATCH",
            "setup_type": "Breakout + Retest",
            "setup_score": 0.62,
            "reason": "WATCH_READY: Setup is staged outside regular market hours.",
            "off_hours_candidate": True,
            "regular_session_confirmation_required": True,
            "warnings": [],
            "shadow_strategies": [],
        },
        {
            "timestamp": "2026-07-28T06:30:00",
            "ticker": "BLOCKED",
            "final_action": "SKIP",
            "setup_type": "Breakout + Retest",
            "setup_score": 0.62,
            "reason": "SKIP: Earnings blackout active.",
            "off_hours_candidate": True,
            "regular_session_confirmation_required": True,
            "warnings": ["WATCH_READY: Setup is staged outside regular market hours."],
            "shadow_strategies": [],
        },
    ]
    write_decisions(decision_path, records)

    paths = write_performance_summaries(
        summary_dir=summary_dir,
        decision_dir=decision_dir,
        current_decision_path=decision_path,
        run_id="20260728_063000",
        timestamp="2026-07-28T06:30:00",
        portfolio={},
    )

    daily = json.loads(paths["daily_summary_json"].read_text(encoding="utf-8"))

    assert daily["WATCH_READY_count"] == 1
    assert daily["WATCH_count"] == 1
    assert daily["SKIP_count"] == 1


def test_watch_ready_summary_tracks_unique_session_split_and_conversion(tmp_path: Path) -> None:
    decision_dir = tmp_path / "decisions"
    summary_dir = tmp_path / "summaries"
    decision_path = decision_dir / "market_lens_agent_20260811_103000.jsonl"
    records = [
        {
            "timestamp": "2026-08-11T06:00:00",
            "ticker": "STAGE1",
            "final_action": "WATCH",
            "setup_type": "VWAP Reclaim",
            "setup_score": 0.61,
            "reason": "WATCH_READY: staged outside regular session.",
            "off_hours_candidate": True,
            "regular_session_confirmation_required": True,
            "market_session_phase": "PRE_MARKET",
            "warnings": [],
            "shadow_strategies": [],
        },
        {
            "timestamp": "2026-08-11T09:45:00",
            "ticker": "STAGE1",
            "final_action": "WATCH",
            "setup_type": "VWAP Reclaim",
            "setup_score": 0.62,
            "reason": "WATCH_READY: regular-session confirmation scan.",
            "market_session_phase": "REGULAR",
            "market_session_can_open_new_buy": True,
            "warnings": [],
            "shadow_strategies": [],
        },
        {
            "timestamp": "2026-08-11T10:30:00",
            "ticker": "STAGE1",
            "final_action": "BUY_SIMULATED",
            "setup_type": "VWAP Reclaim",
            "setup_score": 0.68,
            "reason": "BUY_SIMULATED: confirmation passed.",
            "market_session_phase": "REGULAR",
            "market_session_can_open_new_buy": True,
            "warnings": [],
            "shadow_strategies": [],
        },
        {
            "timestamp": "2026-08-11T06:05:00",
            "ticker": "STAGE2",
            "final_action": "WATCH",
            "setup_type": "Breakout + Retest",
            "setup_score": 0.59,
            "reason": "WATCH_READY: staged outside regular session.",
            "off_hours_candidate": True,
            "regular_session_confirmation_required": True,
            "market_session_phase": "CLOSED",
            "warnings": [],
            "shadow_strategies": [],
        },
        {
            "timestamp": "2026-08-11T11:30:00",
            "ticker": "STAGE3",
            "final_action": "WATCH_READY",
            "setup_type": "Trend Pullback",
            "setup_score": 0.57,
            "reason": "WATCH_READY: needs one more confirmation.",
            "market_session_phase": "REGULAR",
            "market_session_can_open_new_buy": True,
            "warnings": [],
            "shadow_strategies": [],
        },
    ]
    write_decisions(decision_path, records)

    paths = write_performance_summaries(
        summary_dir=summary_dir,
        decision_dir=decision_dir,
        current_decision_path=decision_path,
        run_id="20260811_103000",
        timestamp="2026-08-11T10:30:00",
        portfolio={},
    )

    daily = json.loads(paths["daily_summary_json"].read_text(encoding="utf-8"))
    conversion = daily["WATCH_READY_conversion"]

    assert daily["WATCH_READY_count"] == 4
    assert daily["WATCH_READY_unique_count"] == 3
    assert daily["WATCH_READY_regular_session_count"] == 2
    assert daily["WATCH_READY_off_hours_count"] == 2
    assert daily["WATCH_READY_unique_regular_session_count"] == 2
    assert daily["WATCH_READY_unique_off_hours_count"] == 2
    assert conversion["source_unique_count"] == 3
    assert conversion["reviewed_unique_count"] == 2
    assert conversion["converted_unique_count"] == 1
    assert conversion["pending_review_unique_count"] == 1
    assert conversion["reviewed_conversion_rate_pct"] == 50.0
    assert conversion["conversion_rate_pct"] == 33.33
    assert conversion["converted_tickers"] == ["STAGE1"]
    assert conversion["pending_review_tickers"] == ["STAGE2"]


def test_top_rejected_candidates_are_unique_by_ticker(tmp_path: Path) -> None:
    decision_dir = tmp_path / "decisions"
    summary_dir = tmp_path / "summaries"
    decision_path = decision_dir / "market_lens_agent_20260811_113000.jsonl"
    records = [
        {
            "timestamp": "2026-08-11T11:30:00",
            "ticker": "DUP",
            "final_action": "WATCH",
            "setup_type": "VWAP Reclaim",
            "setup_score": 0.80,
            "net_rr": 2.4,
            "reason": "WATCH_READY: high quality but pending.",
            "warnings": [],
            "shadow_strategies": [],
        },
        {
            "timestamp": "2026-08-11T11:31:00",
            "ticker": "DUP",
            "final_action": "WATCH",
            "setup_type": "VWAP Reclaim",
            "setup_score": 0.78,
            "net_rr": 2.2,
            "reason": "WATCH_READY: duplicate later scan.",
            "warnings": [],
            "shadow_strategies": [],
        },
        {
            "timestamp": "2026-08-11T11:32:00",
            "ticker": "NEXT",
            "final_action": "SKIP",
            "setup_type": "Breakout + Retest",
            "setup_score": 0.76,
            "net_rr": 2.1,
            "reason": "SKIP: no confirmation.",
            "warnings": [],
            "shadow_strategies": [],
        },
    ]
    write_decisions(decision_path, records)

    paths = write_performance_summaries(
        summary_dir=summary_dir,
        decision_dir=decision_dir,
        current_decision_path=decision_path,
        run_id="20260811_113000",
        timestamp="2026-08-11T11:30:00",
        portfolio={},
    )

    daily = json.loads(paths["daily_summary_json"].read_text(encoding="utf-8"))
    tickers = [item["ticker"] for item in daily["top_rejected_candidates"]]

    assert tickers == ["DUP", "NEXT"]


def test_summary_uses_trade_events_for_monitor_outcomes(tmp_path: Path) -> None:
    decision_dir = tmp_path / "decisions"
    summary_dir = tmp_path / "summaries"
    decision_path = decision_dir / "market_lens_agent_20260805_153000.jsonl"
    write_decisions(decision_path, sample_records())

    paths = write_performance_summaries(
        summary_dir=summary_dir,
        decision_dir=decision_dir,
        current_decision_path=decision_path,
        run_id="20260805_153000",
        timestamp="2026-08-05T15:30:00",
        portfolio={"open_positions_end": 1, "total_portfolio_value": 100250},
        trade_events=[
            {
                "timestamp": "2026-08-05T13:40:00",
                "action": "BUY_SIMULATED",
                "ticker": "AAA",
            },
            {
                "timestamp": "2026-08-05T15:10:00",
                "action": "TAKE_PARTIAL_PROFIT",
                "ticker": "AAA",
                "pnl_ils": 60,
                "r_multiple": 1.15,
            },
            {
                "timestamp": "2026-08-05T15:20:00",
                "action": "EXIT_STOP",
                "ticker": "BBB",
                "pnl_ils": -40,
                "r_multiple": -1,
            },
        ],
    )

    daily = json.loads(paths["daily_summary_json"].read_text(encoding="utf-8"))
    weekly = json.loads(paths["weekly_summary_json"].read_text(encoding="utf-8"))

    assert daily["BUY_SIMULATED_count"] == 1
    assert daily["TP1_hits"] == 1
    assert daily["SL_hits"] == 1
    assert daily["positions_closed_today"] == 1
    assert daily["realized_pnl"] == 20
    assert weekly["total_closed_trades"] == 1
    assert weekly["average_R"] == 0.075
    assert weekly["best_setup_type"] == "INSUFFICIENT_DATA"
    assert weekly["most_frequent_actionable_setup"] == "Breakout + Retest"


def test_summary_separates_period_and_portfolio_pnl_and_uses_outcome_groups(tmp_path: Path) -> None:
    decision_dir = tmp_path / "decisions"
    summary_dir = tmp_path / "summaries"
    decision_path = decision_dir / "market_lens_agent_20260806_153000.jsonl"
    records = sample_records()
    records[0]["timestamp"] = "2026-08-06T15:30:00"
    records[0]["price"] = 100
    records[1]["timestamp"] = "2026-08-06T15:30:00"
    write_decisions(decision_path, records)

    paths = write_performance_summaries(
        summary_dir=summary_dir,
        decision_dir=decision_dir,
        current_decision_path=decision_path,
        run_id="20260806_153000",
        timestamp="2026-08-06T15:30:00",
        portfolio={"realized_pnl": 1250},
        trade_events=[{
            "timestamp": "2026-08-06T15:00:00", "action": "EXIT_STOP", "ticker": "AAA",
            "pnl_ils": -25, "r_multiple": -0.5, "trade_id": "trade-1",
            "decision_json": {"setup_type": "Breakout + Retest", "market_regime": "NEUTRAL", "sector_regime": "STRONG", "setup_score_bucket": "0.50-0.59"},
        }],
    )
    daily = json.loads(paths["daily_summary_json"].read_text(encoding="utf-8"))

    assert daily["period_realized_pnl"] == -25
    assert daily["portfolio_realized_pnl"] == 1250
    assert daily["best_setup_type"] == "Breakout + Retest"
    assert daily["performance_by_market_regime"]["NEUTRAL"]["realized_pnl"] == -25
    assert daily["data_completeness"]["closed_event_trade_id"]["coverage_pct"] == 100
    assert not any("Target ATR feasibility" in item["name"] for item in daily["most_common_warnings"])


def test_weekly_shadow_calibration_uses_future_scan_prices(tmp_path: Path) -> None:
    decision_dir = tmp_path / "decisions"
    summary_dir = tmp_path / "summaries"
    first_path = decision_dir / "market_lens_agent_20260803_100000.jsonl"
    second_path = decision_dir / "market_lens_agent_20260804_100000.jsonl"
    write_decisions(first_path, [{"timestamp": "2026-08-03T10:00:00", "ticker": "AAA", "price": 100, "final_action": "WATCH", "setup_type": "Breakout + Retest", "warnings": [], "shadow_strategies": [{"name": "BREAKOUT_CONTINUATION", "would_buy": True, "confidence": 0.8, "entry_price": 100}]}])
    write_decisions(second_path, [{"timestamp": "2026-08-04T10:00:00", "ticker": "AAA", "price": 103, "final_action": "WATCH", "setup_type": "Breakout + Retest", "warnings": [], "shadow_strategies": []}])
    paths = write_performance_summaries(summary_dir=summary_dir, decision_dir=decision_dir, current_decision_path=second_path, run_id="20260804_100000", timestamp="2026-08-04T10:00:00", portfolio={})
    weekly = json.loads(paths["weekly_summary_json"].read_text(encoding="utf-8"))

    metrics = weekly["shadow_outcome_metrics_by_strategy"]["BREAKOUT_CONTINUATION"]
    assert metrics["matured_1d"] == 1
    assert metrics["average_return_1d_pct"] == 3.0
    assert weekly["best_shadow_strategy"] == "BREAKOUT_CONTINUATION"
