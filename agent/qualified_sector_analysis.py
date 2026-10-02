from __future__ import annotations

import argparse
import contextlib
import io
import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf
from openpyxl import load_workbook

from app.agent_dashboard import compute_full_trade_performance, read_trades
from app.decision_quality_analysis import (
    entry_path_assessment,
    sector_only_shadow_flips,
    simulate_fixed_plan,
    unique_weak_sector_opportunities,
)
from app.performance_summary import collect_records
from app.setup_selection_replay import exchange_session_identity


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only qualified-selection and sector-gate audit"
    )
    parser.add_argument("--decision-dir", type=Path, required=True)
    parser.add_argument("--tracker", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 9, 17))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 29))
    parser.add_argument(
        "--outcome-end", type=date.fromisoformat, default=date(2026, 10, 2)
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-market-data", action="store_true")
    return parser.parse_args()


def period_records(decision_dir: Path, start: date, end: date) -> list[dict[str, Any]]:
    weeks: set[tuple[int, int]] = set()
    records = []
    cursor = start
    while cursor <= end:
        year, week, _weekday = cursor.isocalendar()
        if (year, week) not in weeks:
            weekly, _files = collect_records(
                decision_dir, period="weekly", target_date=cursor
            )
            records.extend(weekly)
            weeks.add((year, week))
        cursor += timedelta(days=1)
    selected = []
    for record in records:
        session_date, _phase = exchange_session_identity(record)
        try:
            current = date.fromisoformat(session_date)
        except ValueError:
            continue
        if start <= current <= end:
            selected.append(record)
    return selected


def qualified_trades(
    tracker: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
        io.StringIO()
    ):
        workbook = load_workbook(tracker, read_only=True, data_only=True)
        try:
            events = read_trades(workbook)
        finally:
            workbook.close()
    completed = compute_full_trade_performance(events)["closed"]
    selected = [
        trade
        for trade in completed
        if trade.get("strategy_version") == "qualified_selection_v1"
    ]
    ids = {str(trade.get("trade_id") or "") for trade in selected}
    entries = [
        event
        for event in events
        if event.get("action") == "BUY_SIMULATED"
        and str(event.get("trade_id") or "") in ids
    ]
    return selected, entries


def download_bars(
    tickers: list[str], start: date, end: date
) -> tuple[dict[str, Any], dict[str, str]]:
    frames = {}
    errors = {}
    for ticker in sorted(set(tickers)):
        try:
            frame = yf.download(
                ticker,
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),
                interval="5m",
                auto_adjust=False,
                prepost=False,
                progress=False,
                threads=False,
            )
            if isinstance(frame.columns, pd.MultiIndex):
                if ticker in frame.columns.get_level_values(-1):
                    frame = frame.xs(ticker, axis=1, level=-1)
                else:
                    frame.columns = frame.columns.get_level_values(0)
            required = {"Open", "High", "Low", "Close"}
            if frame.empty or not required.issubset(frame.columns):
                errors[ticker] = "No usable 5-minute OHLC bars"
                continue
            frames[ticker] = frame[list(required)].sort_index()
        except Exception as exc:
            errors[ticker] = str(exc)
    return frames, errors


def compact_trade(trade: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    decision = (
        entry.get("decision_json")
        if isinstance(entry.get("decision_json"), dict)
        else {}
    )
    return {
        **{
            key: trade.get(key)
            for key in (
                "trade_id",
                "ticker",
                "entry_timestamp",
                "exit_timestamp",
                "initial_quantity",
                "entry_price_usd",
                "stop_loss",
                "setup_type",
                "setup_score",
                "market_regime",
                "sector",
                "sector_regime",
                "net_rr_1",
                "net_rr_2",
                "weighted_net_rr",
                "entry_confirmation_status",
                "modeled_costs",
                "pnl_ils",
                "pnl_pct",
                "r_multiple",
                "mfe_r",
                "mae_r",
                "exit_actions",
                "exit_events",
            )
        },
        "target_1": entry.get("target_1"),
        "target_2": entry.get("target_2"),
        "entry_mode": decision.get("entry_mode"),
        "selection_reason": decision.get("selection_reason"),
        "confirmation_reason": decision.get("confirmation_reason"),
        "confirmation_candle_close_timestamp": decision.get(
            "confirmation_candle_close_timestamp"
        ),
        "target_feasibility_status": decision.get("target_feasibility_status"),
        "target_1_atr_distance": decision.get("target_1_atr_distance"),
        "target_2_atr_distance": decision.get("target_2_atr_distance"),
        "initial_risk": entry.get("risk_ils"),
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    records = period_records(args.decision_dir, args.start, args.end)
    closed, entries = qualified_trades(args.tracker)
    entry_by_id = {str(item.get("trade_id") or ""): item for item in entries}
    trades = [
        compact_trade(item, entry_by_id.get(str(item.get("trade_id") or ""), {}))
        for item in closed
    ]
    weak = unique_weak_sector_opportunities(records)
    standard = [entry_path_assessment(item, path="STANDARD") for item in weak]
    pilot = [entry_path_assessment(item, path="NEUTRAL_PILOT") for item in weak]
    shadow = sector_only_shadow_flips(records)

    market_data_errors = {}
    if not args.skip_market_data:
        frames, market_data_errors = download_bars(
            [item["ticker"] for item in shadow], args.start, args.outcome_end
        )
        for item in shadow:
            frame = frames.get(item["ticker"])
            item["outcome"] = (
                simulate_fixed_plan(item, frame)
                if frame is not None
                else {
                    "status": "UNASSESSABLE",
                    "reason": market_data_errors.get(item["ticker"], "Missing bars"),
                }
            )
    else:
        for item in shadow:
            item["outcome"] = {"status": "NOT_REQUESTED"}

    shadow_rows = []
    for item in shadow:
        shadow_rows.append(
            {
                key: item.get(key)
                for key in (
                    "signal_id",
                    "session_date",
                    "ticker",
                    "setup_type",
                    "timestamp",
                    "shadow_strategies",
                    "entry_price",
                    "stop_loss",
                    "target_1",
                    "target_2",
                    "setup_score",
                    "net_rr_1",
                    "weighted_net_rr",
                    "active_standard",
                    "active_neutral_pilot",
                    "outcome",
                )
            }
        )

    closed_outcomes = [
        item["outcome"]
        for item in shadow_rows
        if item["outcome"].get("status") == "CLOSED"
    ]
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "mode": "READ_ONLY_DECISION_QUALITY_AUDIT",
        "active_trading_logic_changed": False,
        "period": {
            "start": args.start.isoformat(),
            "end": args.end.isoformat(),
            "outcome_end": args.outcome_end.isoformat(),
        },
        "qualified_selection_v1": {
            "closed_trade_count": len(trades),
            "net_pnl": round(
                sum(float(item.get("pnl_ils") or 0) for item in trades), 2
            ),
            "modeled_costs": round(
                sum(float(item.get("modeled_costs") or 0) for item in trades), 2
            ),
            "trades": trades,
        },
        "weak_sector": {
            "decision_records": len(records),
            "unique_regular_session_opportunities": len(weak),
            "standard_classifications": dict(
                Counter(item["classification"] for item in standard)
            ),
            "standard_failed_gate_counts": dict(
                Counter(name for item in standard for name in item["failed_non_sector"])
            ),
            "standard_not_evaluated_gate_counts": dict(
                Counter(
                    name
                    for item in standard
                    for name in item["not_evaluated_non_sector"]
                )
            ),
            "neutral_pilot_classifications": dict(
                Counter(item["classification"] for item in pilot)
            ),
            "neutral_pilot_failed_gate_counts": dict(
                Counter(name for item in pilot for name in item["failed_non_sector"])
            ),
            "neutral_pilot_not_evaluated_gate_counts": dict(
                Counter(
                    name for item in pilot for name in item["not_evaluated_non_sector"]
                )
            ),
            "shadow_sector_only_signal_count": len(shadow_rows),
            "shadow_outcome_counts": dict(
                Counter(item["outcome"].get("status") for item in shadow_rows)
            ),
            "closed_positive": sum(
                float(item.get("realized_per_share") or 0) > 0
                for item in closed_outcomes
            ),
            "closed_negative": sum(
                float(item.get("realized_per_share") or 0) < 0
                for item in closed_outcomes
            ),
            "signals": shadow_rows,
            "market_data_errors": market_data_errors,
        },
        "methodology": {
            "deduplication": "First regular-session active setup per New York date/ticker/setup; no later same-day best-case selection.",
            "active_gate_evidence": "Missing sizing or gate evidence is NOT_EVALUATED, never PASS.",
            "sector_counterfactual": "Existing shadow evaluators are run twice; only sector_regime changes from WEAK to NEUTRAL. Stop-changing shadow variants are excluded.",
            "outcomes": "Independent one-signal 5-minute OHLC replay with frozen cost policy, regular-session bars, stop-first same-bar ordering, TP1 half exit, stop-to-entry, then TP2.",
        },
        "limitations": [
            "Shadow signals are not portfolio returns and can overlap by ticker/date.",
            "Five-minute bars cannot resolve event order inside a bar; stop-first is conservative.",
            "Signals near the observation cutoff may remain censored open.",
            "Historical weak-sector decisions did not invoke executable sizing; active-path sector-only eligibility is therefore generally unassessable.",
            "Yahoo OHLC is retrieved after the fact and is not an archived broker feed.",
        ],
    }


def main() -> None:
    args = arguments()
    report = build_report(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "qualified_trades": report["qualified_selection_v1"][
                    "closed_trade_count"
                ],
                "qualified_net_pnl": report["qualified_selection_v1"]["net_pnl"],
                "weak_opportunities": report["weak_sector"][
                    "unique_regular_session_opportunities"
                ],
                "shadow_sector_only_signals": report["weak_sector"][
                    "shadow_sector_only_signal_count"
                ],
                "shadow_outcomes": report["weak_sector"]["shadow_outcome_counts"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
