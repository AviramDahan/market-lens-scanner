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
