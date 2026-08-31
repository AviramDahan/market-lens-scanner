from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook

from app.capital_replay import (
    DEFAULT_SCENARIOS,
    analyze_capital_blocked_candidates,
    build_capital_replay_report,
    load_closed_trades,
    replay_scenario,
)


def make_workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Trade Log"
    sheet.append([f"Column {index}" for index in range(1, 33)])
    decision = {
        "trade_id": "trade-1",
        "market_regime": "BULL",
        "market_regime_score": 0.82,
        "market_regime_indicators": {
            "SPY": {"trend": "bullish"},
            "QQQ": {"trend": "bullish"},
            "IWM": {"trend": "mixed"},
            "VIX": {"trend": "calm", "price": 16},
            "US10Y": {"trend": "mixed"},
            "DXY": {"trend": "mixed"},
        },
        "sector": "Technology",
        "sector_regime": "STRONG",
        "setup_score": 0.60,
        "entry_confirmation_passed": True,
        "factor_tags": ["Technology"],
        "final_action": "BUY_SIMULATED",
    }
    buy = [None] * 32
    buy[0] = "2026-07-01T14:00:00"
    buy[1] = "BUY_SIMULATED"
    buy[2] = "AAA"
    buy[3] = 100.0
    buy[5] = 50
    buy[6] = 1.0
    buy[11] = 95.0
    buy[12] = 110.0
    buy[13] = 120.0
    buy[19] = json.dumps(decision)
    buy[20] = "trade-1"
    sheet.append(buy)

    partial = [None] * 32
    partial[0] = "2026-07-03T14:00:00"
    partial[1] = "TAKE_PARTIAL_PROFIT"
    partial[2] = "AAA"
    partial[4] = 110.0
    partial[5] = 25
    partial[6] = 1.0
    sheet.append(partial)

    final = [None] * 32
    final[0] = "2026-07-08T14:00:00"
    final[1] = "TAKE_PROFIT"
    final[2] = "AAA"
    final[4] = 120.0
    final[5] = 25
    final[6] = 1.0
    sheet.append(final)
    workbook.save(path)


def test_actual_replay_preserves_recorded_trade_pnl(tmp_path: Path) -> None:
    workbook_path = tmp_path / "tracker.xlsx"
    make_workbook(workbook_path)
    trades = load_closed_trades(workbook_path)

    result = replay_scenario(trades, DEFAULT_SCENARIOS[0], idle_cash_annual_yield=0)

    assert len(trades) == 1
    assert result["completed_trades"] == 1
    assert result["trading_pnl"] == 750.0
    assert result["ending_equity"] == 100750.0
    assert result["trades"][0]["quantity"] == 50


def test_dynamic_replay_respects_position_and_risk_constraints(tmp_path: Path) -> None:
    workbook_path = tmp_path / "tracker.xlsx"
    make_workbook(workbook_path)
    trades = load_closed_trades(workbook_path)

    result = replay_scenario(trades, DEFAULT_SCENARIOS[2], idle_cash_annual_yield=0)

    assert result["completed_trades"] == 1
    assert result["trades"][0]["quantity"] <= 100
    assert result["max_exposure_pct"] <= 10.0
    assert result["configuration"]["name"] == "BALANCED_DYNAMIC"


def test_capital_blocked_proxy_deduplicates_and_uses_later_prices(tmp_path: Path) -> None:
    decision_dir = tmp_path / "decisions"
    decision_dir.mkdir()
    records = [
        {
            "timestamp": "2026-07-01T15:00:00",
            "ticker": "AAA",
            "price": 100,
            "executable_entry": 100,
            "setup_type": "Breakout + Retest",
            "setup_score": 0.55,
            "net_rr": 2.5,
            "net_rr_1": 1.1,
            "initial_action": "BUY_SIMULATED",
            "final_action": "WATCH",
            "entry_confirmation_passed": True,
            "market_session_can_open_new_buy": True,
            "sector_exposure_limit_exceeded": True,
            "reason": "Skipped because sector exposure limit would be exceeded.",
        },
        {
            "timestamp": "2026-07-01T16:00:00",
            "ticker": "AAA",
            "price": 101,
            "setup_type": "Breakout + Retest",
            "setup_score": 0.50,
            "net_rr": 2.5,
            "net_rr_1": 1.1,
            "initial_action": "BUY_SIMULATED",
            "final_action": "WATCH",
            "entry_confirmation_passed": True,
            "market_session_can_open_new_buy": True,
            "sector_exposure_limit_exceeded": True,
            "reason": "Skipped because sector exposure limit would be exceeded.",
        },
    ]
    for day, price in enumerate((102, 103, 104, 105, 110), start=2):
        records.append(
            {
                "timestamp": f"2026-07-{day:02d}T16:00:00",
                "ticker": "AAA",
                "price": price,
                "initial_action": "SKIP",
                "final_action": "SKIP",
            }
        )
    (decision_dir / "run.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )

    result = analyze_capital_blocked_candidates(decision_dir)

    assert result["unique_capital_blocked_candidates"] == 1
    assert result["blocker_categories"] == {"sector_exposure": 1}
    assert result["outcomes"]["5_scan_days"]["average_pct"] == 10.0


def test_report_is_read_only_and_does_not_mutate_workbook(tmp_path: Path) -> None:
    workbook_path = tmp_path / "tracker.xlsx"
    decision_dir = tmp_path / "decisions"
    decision_dir.mkdir()
    make_workbook(workbook_path)
    before = workbook_path.read_bytes()

    report = build_capital_replay_report(
        workbook_path=workbook_path,
        decision_dir=decision_dir,
        include_candidate_analysis=False,
    )

    assert report["mode"] == "READ_ONLY_CAPITAL_REPLAY"
    assert report["active_trading_logic_changed"] is False
    assert workbook_path.read_bytes() == before
    assert len(report["scenarios"]) == 4
