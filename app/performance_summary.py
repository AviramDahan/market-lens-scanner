from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any


def write_performance_summaries(
    *,
    summary_dir: Path,
    decision_dir: Path,
    current_decision_path: Path,
    run_id: str,
    timestamp: str,
    portfolio: dict[str, Any],
    trade_events: list[dict[str, Any]] | None = None,
    completed_trades: list[dict[str, Any]] | None = None,
) -> dict[str, Path]:
    summary_dir.mkdir(parents=True, exist_ok=True)
    run_date = parse_date(timestamp)
    daily = build_period_summary(
        period="daily",
        target_date=run_date,
        decision_dir=decision_dir,
        portfolio=portfolio,
        current_decision_path=current_decision_path,
        run_id=run_id,
        trade_events=trade_events,
        completed_trades=completed_trades,
    )
    year, week, _ = run_date.isocalendar()
    weekly = build_period_summary(
        period="weekly",
        target_date=run_date,
        decision_dir=decision_dir,
        portfolio=portfolio,
        current_decision_path=current_decision_path,
        run_id=run_id,
        trade_events=trade_events,
        completed_trades=completed_trades,
    )

    daily_json = summary_dir / f"daily_summary_{run_date.isoformat()}.json"
    daily_md = summary_dir / f"daily_summary_{run_date.isoformat()}.md"
    weekly_json = summary_dir / f"weekly_summary_{year}-W{week:02d}.json"
    weekly_md = summary_dir / f"weekly_summary_{year}-W{week:02d}.md"
    write_json(daily_json, daily)
    write_markdown(daily_md, "Daily Performance Summary", daily)
    write_json(weekly_json, weekly)
    write_markdown(weekly_md, "Weekly Performance Summary", weekly)
    return {
        "daily_summary_json": daily_json,
        "daily_summary_md": daily_md,
        "weekly_summary_json": weekly_json,
        "weekly_summary_md": weekly_md,
    }


def build_period_summary(
    *,
    period: str,
    target_date: date,
    decision_dir: Path,
    portfolio: dict[str, Any],
    current_decision_path: Path,
    run_id: str,
    trade_events: list[dict[str, Any]] | None = None,
    completed_trades: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    records, files = collect_records(decision_dir, period=period, target_date=target_date)
    if not records and current_decision_path.exists():
        records = read_jsonl(current_decision_path)
        files = [current_decision_path]

    period_trade_events = [
        event for event in trade_events or [] if in_period(event, period, target_date)
    ]
    period_completed_trades = [
        trade
        for trade in completed_trades or []
        if in_period({"timestamp": trade.get("exit_timestamp")}, period, target_date)
    ]
    actions = Counter(str(record.get("final_action") or "UNKNOWN") for record in records)
    trade_actions = Counter(str(event.get("action") or "UNKNOWN") for event in period_trade_events)
    watch_ready_count = sum(1 for record in records if is_watch_ready_candidate(record))
    setups = Counter(str(record.get("setup_type") or "UNKNOWN") for record in records)
    actionable_setups = Counter(
        str(record.get("setup_type") or "UNKNOWN")
        for record in records
        if str(record.get("setup_type") or "").lower() != "no trade"
    )
    sectors = group_average(records, "sector", "net_rr")
    setup_scores = [to_float(record.get("setup_score")) for record in records if record.get("setup_score") is not None]
    rr1_values = [to_float(record.get("net_rr_1")) for record in records if record.get("net_rr_1") is not None]
    rr2_values = [to_float(record.get("net_rr_2")) for record in records if record.get("net_rr_2") is not None]
    shadow = shadow_metrics(records)
    shadow_outcomes = shadow_outcome_metrics(records)
    setup_candidate_summary = setup_candidate_metrics(records)
    shadow["outcome_metrics_by_strategy"] = shadow_outcomes["by_strategy"]
    watch_ready_metrics = watch_ready_analytics(records)
    watch_review_records = [record for record in records if is_watch_review_candidate(record)]
    watch_review_tickers = {
        str(record.get("ticker") or "").upper()
        for record in watch_review_records
        if record.get("ticker")
    }
    exit_event_r_values = [
        to_float(event.get("r_multiple"))
        for event in period_trade_events
        if event.get("action") in {"TAKE_PARTIAL_PROFIT", "TAKE_PROFIT", "EXIT_STOP"}
        and event.get("r_multiple") not in (None, "")
    ]
    decision_r_values = [to_float(record.get("r_multiple")) for record in records if record.get("r_multiple") not in (None, "")]
    full_trade_r_values = [
        to_float(trade.get("r_multiple"))
        for trade in period_completed_trades
        if trade.get("r_multiple") not in (None, "")
    ]
    r_values = full_trade_r_values if completed_trades is not None else (exit_event_r_values or decision_r_values)
    full_trade_pnl_values = [to_float(trade.get("pnl_ils")) for trade in period_completed_trades]
    if completed_trades is not None:
        winning_trades = [trade for trade in period_completed_trades if to_float(trade.get("pnl_ils")) > 0]
        losing_trades = [trade for trade in period_completed_trades if to_float(trade.get("pnl_ils")) < 0]
        winners = [to_float(trade.get("r_multiple")) for trade in winning_trades if trade.get("r_multiple") is not None]
        losers = [to_float(trade.get("r_multiple")) for trade in losing_trades if trade.get("r_multiple") is not None]
        gross_profit = sum(value for value in full_trade_pnl_values if value > 0)
        gross_loss = abs(sum(value for value in full_trade_pnl_values if value < 0))
    else:
        winning_trades = []
        losing_trades = []
        winners = [value for value in r_values if value > 0]
        losers = [value for value in r_values if value < 0]
        gross_profit = sum(winners)
        gross_loss = abs(sum(losers))
    week_start, week_end = iso_week_bounds(target_date)
    positions_opened = trade_actions.get("BUY_SIMULATED", actions.get("BUY_SIMULATED", 0))
    event_positions_closed = trade_actions.get("TAKE_PROFIT", actions.get("TAKE_PROFIT", 0)) + trade_actions.get(
        "EXIT_STOP", actions.get("EXIT_STOP", 0)
    )
    positions_closed = len(period_completed_trades) if completed_trades is not None else event_positions_closed
    open_positions_end = portfolio.get("open_positions_end")
    open_positions_start = infer_open_positions_start(
        open_positions_end=open_positions_end,
        positions_opened=positions_opened,
        positions_closed=positions_closed,
        fallback=portfolio.get("open_positions_start"),
    )
    period_realized_pnl = realized_pnl_from_events(period_trade_events)
    if period_realized_pnl is None:
        period_realized_pnl = 0.0 if trade_events is not None else portfolio.get("realized_pnl")
    trade_performance = trade_performance_metrics(period_trade_events)
    performance_records = period_completed_trades if completed_trades is not None else trade_performance["closed_events"]
    setup_performance = trade_performance_by(performance_records, "setup_type")
    market_regime_performance = trade_performance_by(performance_records, "market_regime")
    sector_regime_performance = trade_performance_by(performance_records, "sector_regime")
    score_bucket_performance = trade_performance_by(performance_records, "setup_score_bucket")

    summary = {
        "summary_type": period,
        "date": target_date.isoformat() if period == "daily" else None,
        "week_start": week_start.isoformat() if period == "weekly" else None,
        "week_end": week_end.isoformat() if period == "weekly" else None,
        "market_session": portfolio.get("market_session", ""),
        "total_trading_days": count_trading_days(records, period) if period == "weekly" else None,
        "total_scans": len(files),
        "total_tickers_scanned": len(records),
        "total_result_cards_read": len(records),
        "BUY_SIMULATED_count": trade_actions.get("BUY_SIMULATED", actions.get("BUY_SIMULATED", 0)),
        "WATCH_READY_count": watch_ready_count,
        "WATCH_READY_unique_count": watch_ready_metrics["unique_count"],
        "WATCH_READY_regular_session_count": watch_ready_metrics["session_breakdown"]["regular"]["records"],
        "WATCH_READY_off_hours_count": watch_ready_metrics["session_breakdown"]["off_hours"]["records"],
        "WATCH_READY_unknown_session_count": watch_ready_metrics["session_breakdown"]["unknown"]["records"],
        "WATCH_READY_unique_regular_session_count": watch_ready_metrics["session_breakdown"]["regular"]["unique_tickers"],
        "WATCH_READY_unique_off_hours_count": watch_ready_metrics["session_breakdown"]["off_hours"]["unique_tickers"],
        "WATCH_READY_unique_unknown_session_count": watch_ready_metrics["session_breakdown"]["unknown"]["unique_tickers"],
        "WATCH_READY_session_breakdown": watch_ready_metrics["session_breakdown"],
        "WATCH_READY_conversion": watch_ready_metrics["conversion"],
        "WATCH_REVIEW_count": len(watch_review_records),
        "WATCH_REVIEW_unique_count": len(watch_review_tickers),
        "WATCH_count": actions.get("WATCH", 0),
        "SKIP_count": actions.get("SKIP", 0),
        "NO_TRADE_count": setups.get("No Trade", 0),
        "open_positions_start": open_positions_start,
        "open_positions_end": open_positions_end,
        "positions_opened_today": positions_opened,
        "positions_closed_today": positions_closed,
        "TP1_hits": trade_actions.get("TAKE_PARTIAL_PROFIT", actions.get("TAKE_PARTIAL_PROFIT", 0)),
        "TP2_hits": trade_actions.get("TAKE_PROFIT", actions.get("TAKE_PROFIT", 0)),
        "SL_hits": trade_actions.get("EXIT_STOP", actions.get("EXIT_STOP", 0)),
        "partial_exits": trade_actions.get("TAKE_PARTIAL_PROFIT", actions.get("TAKE_PARTIAL_PROFIT", 0)),
        "realized_pnl": period_realized_pnl,
        "period_realized_pnl": period_realized_pnl,
        "portfolio_realized_pnl": portfolio.get("realized_pnl"),
        "unrealized_pnl": portfolio.get("unrealized_pnl"),
        "total_portfolio_value": portfolio.get("total_portfolio_value"),
        "daily_return_pct": portfolio.get("daily_return_pct") if period == "daily" else None,
        "max_intraday_drawdown": None,
        "best_ticker": best_record(records),
        "worst_ticker": worst_record(records),
        "best_sector": best_group(sectors),
        "worst_sector": worst_group(sectors),
        "top_rejected_candidates": top_rejected(records),
        "most_common_rejection_reasons": counter_items(reason_counter(records)),
        "most_common_warnings": counter_items(warning_counter(records)),
        "average_setup_score": rounded_mean(setup_scores),
        "setup_candidate_metrics": setup_candidate_summary,
        "average_rr_to_target_1": rounded_mean(rr1_values),
        "average_rr_to_target_2": rounded_mean(rr2_values),
        "average_confidence_by_shadow_strategy": shadow["average_confidence_by_strategy"],
        "average_confidence_by_shadow_strategy_version": shadow["average_confidence_by_strategy_version"],
        "shadow_outcome_metrics_by_strategy": shadow_outcomes["by_strategy"],
        "shadow_outcome_metrics_by_strategy_version": shadow_outcomes["by_strategy_version"],
        "shadow_outcome_source": shadow_outcomes["source"],
        "shadow_strategies_would_buy_count_by_strategy": shadow["would_buy_count_by_strategy"],
        "shadow_strategies_would_buy_count_by_strategy_version": shadow["would_buy_count_by_strategy_version"],
        "shadow_strategies_top_candidates": shadow["top_candidates"],
        "shadow_strategies_that_would_buy_but_active_agent_skipped": shadow["would_buy_but_active_skipped"],
        "shadow_strategies_that_agreed_with_active_agent": shadow["agreed_with_active_agent"],
        "runtime_metrics": {
            "run_id": run_id,
            "decision_files": [str(path) for path in files],
            "records": len(records),
        },
        "errors_retries_timeouts": [],
        "data_quality_issues": counter_items(warning_counter(records)),
        "data_completeness": data_completeness(records, period_trade_events, period_completed_trades),
        "trade_metric_source": "COMPLETED_TRADE_LIFECYCLE" if completed_trades is not None else "EXIT_EVENTS",
        "exit_event_metrics": {
            "closed_events": event_positions_closed,
            "r_values": len(exit_event_r_values),
            "average_R": rounded_mean(exit_event_r_values),
        },
        "total_BUY_SIMULATED": trade_actions.get("BUY_SIMULATED", actions.get("BUY_SIMULATED", 0)),
        "total_WATCH_READY": watch_ready_count,
        "total_closed_trades": positions_closed,
        "win_rate": (
            round(len(winning_trades) / len(period_completed_trades) * 100, 2)
            if completed_trades is not None and period_completed_trades
            else round(len(winners) / len(r_values) * 100, 2)
            if r_values
            else None
        ),
        "average_R": rounded_mean(r_values),
        "median_R": round(median(r_values), 4) if r_values else None,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "average_winner": (
            rounded_mean([to_float(trade.get("pnl_ils")) for trade in winning_trades])
            if completed_trades is not None
            else rounded_mean(winners)
        ),
        "average_loser": (
            rounded_mean([to_float(trade.get("pnl_ils")) for trade in losing_trades])
            if completed_trades is not None
            else rounded_mean(losers)
        ),
        "average_winner_R": rounded_mean(winners),
        "average_loser_R": rounded_mean(losers),
        "max_drawdown": realized_max_drawdown(full_trade_pnl_values) if completed_trades is not None else None,
        "most_frequent_actionable_setup": best_counter(actionable_setups),
        "best_setup_type": best_performance_group(setup_performance),
        "worst_setup_type": worst_performance_group(setup_performance),
        "highest_confidence_shadow_strategy": best_confidence_shadow_strategy(shadow),
        "best_shadow_strategy": shadow_outcomes["best_strategy"],
        "worst_shadow_strategy": shadow_outcomes["worst_strategy"],
        "performance_by_market_regime": market_regime_performance,
        "performance_by_sector_regime": sector_regime_performance,
        "performance_by_setup_score_bucket": score_bucket_performance,
        "performance_by_setup_type": setup_performance,
        "performance_by_shadow_strategy": shadow_outcomes["by_strategy"],
        "WATCH_READY_conversion_rate": watch_ready_metrics["conversion"]["conversion_rate_pct"],
        "common_missed_opportunities": shadow["would_buy_but_active_skipped"],
        "common_false_positives": [],
        "recommendations_for_next_week": recommendations(actions, shadow, watch_ready_count, watch_ready_metrics),
    }
    return summary


def is_watch_ready_candidate(record: dict[str, Any]) -> bool:
    action = str(record.get("final_action") or "").upper()
    if action == "SKIP":
        return False
    watch_status = str(record.get("watch_status") or "").upper()
    if watch_status:
        return watch_status == "WATCH_READY"
    if action == "WATCH_READY":
        return True
    if record.get("off_hours_candidate") or record.get("regular_session_confirmation_required"):
        return True
    reason = str(record.get("reason") or "").upper()
    if reason.startswith("WATCH_READY:"):
        return True
    return any(str(warning).upper().startswith("WATCH_READY:") for warning in record.get("warnings") or [])


def is_watch_review_candidate(record: dict[str, Any]) -> bool:
    watch_status = str(record.get("watch_status") or "").upper()
    if watch_status:
        return watch_status == "WATCH_REVIEW"
    return str(record.get("final_action") or "").upper() == "WATCH" and not is_watch_ready_candidate(record)


def watch_ready_analytics(records: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(records, key=record_sort_key)
    watch_ready_records = [record for record in ordered if is_watch_ready_candidate(record)]
    watch_ready_tickers = {str(record.get("ticker") or "").upper() for record in watch_ready_records if record.get("ticker")}
    first_watch: dict[str, datetime] = {}
    session_tickers: dict[str, set[str]] = {
        "regular": set(),
        "off_hours": set(),
        "unknown": set(),
    }
    session_records: Counter[str] = Counter()

    for record in watch_ready_records:
        ticker = str(record.get("ticker") or "").upper()
        if not ticker:
            continue
        first_watch.setdefault(ticker, record_sort_key(record))
        session_key = watch_ready_session_key(record)
        session_records[session_key] += 1
        session_tickers[session_key].add(ticker)

    reviewed: set[str] = set()
    converted: set[str] = set()
    for record in ordered:
        ticker = str(record.get("ticker") or "").upper()
        if not ticker or ticker not in first_watch:
            continue
        current_time = record_sort_key(record)
        if current_time < first_watch[ticker]:
            continue
        if is_regular_session_record(record):
            reviewed.add(ticker)
        if str(record.get("final_action") or "").upper() == "BUY_SIMULATED" and current_time >= first_watch[ticker]:
            converted.add(ticker)
            reviewed.add(ticker)

    source_count = len(watch_ready_tickers)
    reviewed_count = len(reviewed)
    converted_count = len(converted)
    pending_review = sorted(watch_ready_tickers - reviewed)
    reviewed_not_converted = sorted(reviewed - converted)

    return {
        "unique_count": source_count,
        "session_breakdown": {
            key: {
                "records": session_records.get(key, 0),
                "unique_tickers": len(session_tickers[key]),
                "tickers": sorted(session_tickers[key])[:20],
            }
            for key in ("regular", "off_hours", "unknown")
        },
        "conversion": {
            "source_unique_count": source_count,
            "reviewed_unique_count": reviewed_count,
            "converted_unique_count": converted_count,
            "pending_review_unique_count": len(pending_review),
            "reviewed_not_converted_unique_count": len(reviewed_not_converted),
            "conversion_rate_pct": round(converted_count / source_count * 100, 2) if source_count else None,
            "reviewed_conversion_rate_pct": round(converted_count / reviewed_count * 100, 2) if reviewed_count else None,
            "converted_tickers": sorted(converted),
            "pending_review_tickers": pending_review[:20],
            "reviewed_not_converted_tickers": reviewed_not_converted[:20],
        },
    }


def watch_ready_session_key(record: dict[str, Any]) -> str:
    if is_regular_session_record(record):
        return "regular"
    phase = str(record.get("market_session_phase") or "").upper()
    if phase in {"PRE_MARKET", "AFTER_HOURS", "CLOSED", "OVERNIGHT", "WEEKEND"}:
        return "off_hours"
    if record.get("off_hours_candidate") or record.get("regular_session_confirmation_required"):
        return "off_hours"
    return "unknown"


def is_regular_session_record(record: dict[str, Any]) -> bool:
    phase = str(record.get("market_session_phase") or "").upper()
    if phase == "REGULAR":
        return True
    return bool(record.get("market_session_can_open_new_buy"))


def infer_open_positions_start(
    *,
    open_positions_end: Any,
    positions_opened: int,
    positions_closed: int,
    fallback: Any,
) -> Any:
    end = to_float(open_positions_end)
    if open_positions_end is None:
        return fallback
    return max(0, int(round(end)) - positions_opened + positions_closed)


def count_trading_days(records: list[dict[str, Any]], period: str) -> int | None:
    if period != "weekly":
        return None
    return len(
        {
            current
            for record in records
            if (current := record_date(record)) is not None and current.isoweekday() <= 5
        }
    )


def realized_pnl_from_events(events: list[dict[str, Any]]) -> float | None:
    values = [
        to_float(event.get("pnl_ils"))
        for event in events
        if event.get("action") in {"TAKE_PARTIAL_PROFIT", "TAKE_PROFIT", "EXIT_STOP"}
        and event.get("pnl_ils") not in (None, "")
    ]
    if not values:
        return None
    return round(sum(values), 2)


def collect_records(decision_dir: Path, *, period: str, target_date: date) -> tuple[list[dict[str, Any]], list[Path]]:
    records: list[dict[str, Any]] = []
    files: list[Path] = []
    for path in sorted(decision_dir.glob("market_lens_agent_*.jsonl")):
        file_records = read_jsonl(path)
        selected = [record for record in file_records if in_period(record, period, target_date)]
        if selected:
            files.append(path)
            records.extend(selected)
    return records, files


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def shadow_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    confidences: dict[str, list[float]] = defaultdict(list)
    version_confidences: dict[str, list[float]] = defaultdict(list)
    would_buy: Counter[str] = Counter()
    version_would_buy: Counter[str] = Counter()
    top_candidates = []
    skipped = []
    agreed = []
    for record in records:
        final_action = str(record.get("final_action") or "")
        ticker = str(record.get("ticker") or "")
        for strategy in record.get("shadow_strategies") or []:
            name = str(strategy.get("name") or "UNKNOWN")
            version = str(strategy.get("version") or "unknown")
            version_key = f"{name}@{version}"
            confidence = to_float(strategy.get("confidence"))
            confidences[name].append(confidence)
            version_confidences[version_key].append(confidence)
            item = {
                "ticker": ticker,
                "strategy": name,
                "confidence": round(confidence, 4),
                "would_buy": bool(strategy.get("would_buy")),
                "active_final_action": final_action,
                "reason": strategy.get("reason", ""),
            }
            top_candidates.append(item)
            if strategy.get("would_buy"):
                would_buy[name] += 1
                version_would_buy[version_key] += 1
                if final_action != "BUY_SIMULATED":
                    skipped.append(item)
                else:
                    agreed.append(item)
            elif final_action != "BUY_SIMULATED":
                agreed.append(item)
    top_candidates.sort(key=lambda item: item["confidence"], reverse=True)
    return {
        "average_confidence_by_strategy": {name: rounded_mean(values) for name, values in sorted(confidences.items())},
        "average_confidence_by_strategy_version": {
            name: rounded_mean(values) for name, values in sorted(version_confidences.items())
        },
        "would_buy_count_by_strategy": dict(sorted(would_buy.items())),
        "would_buy_count_by_strategy_version": dict(sorted(version_would_buy.items())),
        "top_candidates": top_candidates[:10],
        "would_buy_but_active_skipped": skipped[:10],
        "agreed_with_active_agent": agreed[:10],
    }


def shadow_outcome_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Calibrate shadow signals against later observed scan prices.

    Signals are deduplicated by ticker, strategy and signal date. This remains
    read-only and cannot alter the active action or production gates.
    """
    ordered = sorted(records, key=record_sort_key)
    prices: dict[str, dict[date, float]] = defaultdict(dict)
    for record in ordered:
        ticker = str(record.get("ticker") or "").upper()
        current_date = record_date(record)
        price = first_float(record, "price", "current_price", "executable_entry")
        if ticker and current_date and price and price > 0:
            prices[ticker][current_date] = price

    signals: dict[tuple[str, str, str, date], dict[str, Any]] = {}
    for record in ordered:
        ticker = str(record.get("ticker") or "").upper()
        signal_date = record_date(record)
        if not ticker or signal_date is None:
            continue
        for strategy in record.get("shadow_strategies") or []:
            if not strategy.get("would_buy"):
                continue
            name = str(strategy.get("name") or "UNKNOWN")
            version = str(strategy.get("version") or "unknown")
            entry = first_float(strategy, "entry_price") or first_float(
                record, "price", "current_price", "executable_entry"
            )
            if entry and entry > 0:
                signals.setdefault(
                    (ticker, name, version, signal_date),
                    {
                        "ticker": ticker,
                        "strategy": name,
                        "version": version,
                        "strategy_version": f"{name}@{version}",
                        "date": signal_date,
                        "entry": entry,
                    },
                )

    horizons = (1, 3, 5, 10)
    returns: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    version_returns: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    signal_counts: Counter[str] = Counter()
    version_signal_counts: Counter[str] = Counter()
    for signal in signals.values():
        name = signal["strategy"]
        version_key = signal["strategy_version"]
        signal_counts[name] += 1
        version_signal_counts[version_key] += 1
        later_dates = sorted(day for day in prices.get(signal["ticker"], {}) if day > signal["date"])
        for horizon in horizons:
            if len(later_dates) < horizon:
                continue
            future_price = prices[signal["ticker"]][later_dates[horizon - 1]]
            outcome = (future_price / signal["entry"] - 1) * 100
            returns[name][horizon].append(round(outcome, 4))
            version_returns[version_key][horizon].append(round(outcome, 4))

    by_strategy = build_shadow_outcome_table(signal_counts, returns, horizons)
    by_strategy_version = build_shadow_outcome_table(version_signal_counts, version_returns, horizons)

    ranked: list[tuple[str, float]] = []
    for name, item in by_strategy.items():
        for horizon in (5, 3, 1):
            value = item.get(f"average_return_{horizon}d_pct")
            if value is not None and item.get(f"matured_{horizon}d", 0) >= 1:
                ranked.append((name, float(value)))
                break
    return {
        "source": "deduplicated future scan-price observations",
        "by_strategy": by_strategy,
        "by_strategy_version": by_strategy_version,
        "best_strategy": max(ranked, key=lambda item: item[1])[0] if ranked else "INSUFFICIENT_OUTCOMES",
        "worst_strategy": min(ranked, key=lambda item: item[1])[0] if ranked else "INSUFFICIENT_OUTCOMES",
    }


def setup_candidate_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    candidate_counts: Counter[str] = Counter()
    records_with_candidates = 0
    records_with_multiple = 0
    alternative_ranked_first = 0
    for record in records:
        candidates = [item for item in record.get("setup_candidates") or [] if isinstance(item, dict)]
        if not candidates:
            continue
        records_with_candidates += 1
        if len(candidates) > 1:
            records_with_multiple += 1
        for candidate in candidates:
            candidate_counts[str(candidate.get("setup_type") or "UNKNOWN")] += 1
        best = max(
            candidates,
            key=lambda item: to_float(item.get("shadow_setup_normalized_score") or item.get("legacy_score")),
        )
        if str(best.get("setup_type") or "") != str(record.get("setup_type") or ""):
            alternative_ranked_first += 1
    return {
        "records_with_candidates": records_with_candidates,
        "records_with_multiple_candidates": records_with_multiple,
        "alternative_ranked_first_count": alternative_ranked_first,
        "candidate_count_by_setup_type": dict(sorted(candidate_counts.items())),
        "active_policy": "FIRST_MATCH_LEGACY",
        "shadow_only": True,
    }


def build_shadow_outcome_table(
    signal_counts: Counter[str],
    returns: dict[str, dict[int, list[float]]],
    horizons: tuple[int, ...],
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for name in sorted(set(signal_counts) | set(returns)):
        item: dict[str, Any] = {"signals": signal_counts[name]}
        for horizon in horizons:
            values = returns[name].get(horizon, [])
            item[f"matured_{horizon}d"] = len(values)
            item[f"average_return_{horizon}d_pct"] = rounded_mean(values)
            item[f"positive_rate_{horizon}d_pct"] = (
                round(sum(1 for value in values if value > 0) / len(values) * 100, 2) if values else None
            )
        output[name] = item
    return output


def first_float(source: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = source.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def write_markdown(path: Path, title: str, payload: dict[str, Any]) -> None:
    lines = [
        title,
        "",
        f"Date: {payload.get('date') or payload.get('week_start', '')}",
        f"Total scans: {payload.get('total_scans', 0)}",
        f"Tickers scanned: {payload.get('total_tickers_scanned', 0)}",
        f"BUY_SIMULATED: {payload.get('BUY_SIMULATED_count', payload.get('total_BUY_SIMULATED', 0))}",
        f"WATCH_READY: {payload.get('WATCH_READY_count', payload.get('total_WATCH_READY', 0))}",
        f"WATCH_READY unique tickers: {payload.get('WATCH_READY_unique_count', 0)}",
        f"WATCH_REVIEW: {payload.get('WATCH_REVIEW_count', 0)}",
        f"WATCH_REVIEW unique tickers: {payload.get('WATCH_REVIEW_unique_count', 0)}",
        (
            "WATCH_READY session split: "
            f"regular={payload.get('WATCH_READY_regular_session_count', 0)}, "
            f"off_hours={payload.get('WATCH_READY_off_hours_count', 0)}, "
            f"unknown={payload.get('WATCH_READY_unknown_session_count', 0)}"
        ),
        (
            "WATCH_READY conversion: "
            f"{(payload.get('WATCH_READY_conversion') or {}).get('converted_unique_count', 0)} converted / "
            f"{(payload.get('WATCH_READY_conversion') or {}).get('source_unique_count', 0)} staged"
        ),
        f"WATCH: {payload.get('WATCH_count', 0)}",
        f"SKIP: {payload.get('SKIP_count', 0)}",
        f"NO_TRADE: {payload.get('NO_TRADE_count', 0)}",
        f"Realized PnL: {payload.get('realized_pnl')}",
        f"Unrealized PnL: {payload.get('unrealized_pnl')}",
        f"Portfolio value: {payload.get('total_portfolio_value')}",
        f"Trade metric source: {payload.get('trade_metric_source')}",
        f"Best ticker: {payload.get('best_ticker')}",
        f"Worst ticker: {payload.get('worst_ticker')}",
        f"Best shadow strategy: {payload.get('best_shadow_strategy')}",
        f"Worst shadow strategy: {payload.get('worst_shadow_strategy')}",
        "Shadow would-buy counts:",
    ]
    for name, count in (payload.get("shadow_strategies_would_buy_count_by_strategy") or {}).items():
        lines.append(f"- {name}: {count}")
    lines.append("")
    lines.append("Top rejected candidates:")
    for item in payload.get("top_rejected_candidates") or []:
        lines.append(f"- {item.get('ticker')}: {item.get('final_action')} score={item.get('setup_score')} reason={item.get('reason')}")
    lines.append("")
    lines.append("Recommendations:")
    for item in payload.get("recommendations_for_next_week") or []:
        lines.append(f"- {item}")
    path.write_text("\n".join(lines), encoding="utf-8")


def in_period(record: dict[str, Any], period: str, target_date: date) -> bool:
    current = record_date(record)
    if current is None:
        return False
    if period == "daily":
        return current == target_date
    return current.isocalendar()[:2] == target_date.isocalendar()[:2]


def record_date(record: dict[str, Any]) -> date | None:
    timestamp = str(record.get("timestamp") or "")
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def record_sort_key(record: dict[str, Any]) -> datetime:
    timestamp = str(record.get("timestamp") or "")
    if not timestamp:
        return datetime.min
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def parse_date(timestamp: str) -> date:
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()
    except ValueError:
        return datetime.now().date()


def iso_week_bounds(value: date) -> tuple[date, date]:
    start = date.fromisocalendar(value.isocalendar().year, value.isocalendar().week, 1)
    end = date.fromisocalendar(value.isocalendar().year, value.isocalendar().week, 7)
    return start, end


def best_record(records: list[dict[str, Any]]) -> str:
    if not records:
        return ""
    record = max(records, key=lambda item: (to_float(item.get("net_rr")), to_float(item.get("setup_score"))))
    return str(record.get("ticker") or "")


def worst_record(records: list[dict[str, Any]]) -> str:
    scored = [record for record in records if record.get("setup_type") != "No Trade"]
    if not scored:
        return ""
    record = min(scored, key=lambda item: (to_float(item.get("net_rr")), to_float(item.get("setup_score"))))
    return str(record.get("ticker") or "")


def top_rejected(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rejected = [record for record in records if str(record.get("final_action") or "") in {"SKIP", "WATCH", "WATCH_READY"}]
    rejected.sort(key=lambda item: (to_float(item.get("setup_score")), to_float(item.get("net_rr"))), reverse=True)
    unique = []
    seen = set()
    for record in rejected:
        ticker = str(record.get("ticker") or "").upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        unique.append(
            {
                "ticker": record.get("ticker", ""),
                "final_action": record.get("final_action", ""),
                "setup_score": record.get("setup_score", 0),
                "net_rr": record.get("net_rr", 0),
                "reason": record.get("reason", ""),
            }
        )
        if len(unique) >= 10:
            break
    return unique


def reason_counter(records: list[dict[str, Any]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for record in records:
        action = str(record.get("final_action") or "")
        if action not in {"SKIP", "WATCH", "WATCH_READY"}:
            continue
        reason = str(record.get("reason") or "Unknown")
        counter[reason.split(".")[0][:140]] += 1
    return counter


def warning_counter(records: list[dict[str, Any]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for record in records:
        no_trade = str(record.get("setup_type") or "").strip().lower() in {"no trade", "no_trade"}
        for warning in record.get("warnings") or []:
            warning_text = str(warning)
            if no_trade and any(
                token in warning_text.lower()
                for token in ("entry confirmation", "target atr feasibility", "market structure target")
            ):
                continue
            counter[warning_text[:140]] += 1
    return counter


def counter_items(counter: Counter[str]) -> list[dict[str, Any]]:
    return [{"name": name, "count": count} for name, count in counter.most_common(10)]


def group_average(records: list[dict[str, Any]], group_key: str, value_key: str) -> dict[str, float]:
    values: dict[str, list[float]] = defaultdict(list)
    for record in records:
        key = str(record.get(group_key) or "Unknown")
        values[key].append(to_float(record.get(value_key)))
    return {key: rounded_mean(items) for key, items in values.items()}


def group_counts(records: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(Counter(str(record.get(key) or "Unknown") for record in records))


def best_group(values: dict[str, float]) -> str:
    return max(values.items(), key=lambda item: item[1])[0] if values else ""


def worst_group(values: dict[str, float]) -> str:
    return min(values.items(), key=lambda item: item[1])[0] if values else ""


def best_counter(counter: Counter[str]) -> str:
    return counter.most_common(1)[0][0] if counter else ""


def best_confidence_shadow_strategy(metrics: dict[str, Any]) -> str:
    values = metrics.get("average_confidence_by_strategy") or {}
    return max(values.items(), key=lambda item: item[1])[0] if values else ""


def trade_performance_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    closed_events = []
    for event in events:
        if str(event.get("action") or "") not in {"TAKE_PARTIAL_PROFIT", "TAKE_PROFIT", "EXIT_STOP"}:
            continue
        if event.get("pnl_ils") in (None, ""):
            continue
        decision = event.get("decision_json") if isinstance(event.get("decision_json"), dict) else {}
        closed_events.append(
            {
                **event,
                "setup_type": event.get("setup_type") or decision.get("setup_type") or "Unknown",
                "market_regime": event.get("market_regime") or decision.get("market_regime") or "Unknown",
                "sector_regime": event.get("sector_regime") or decision.get("sector_regime") or "Unknown",
                "setup_score_bucket": event.get("setup_score_bucket") or decision.get("setup_score_bucket") or "Unknown",
            }
        )
    return {"closed_events": closed_events}


def trade_performance_by(events: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[str(event.get(key) or "Unknown")].append(event)
    output: dict[str, dict[str, Any]] = {}
    for name, items in sorted(grouped.items()):
        pnl_values = [to_float(item.get("pnl_ils")) for item in items]
        r_values = [to_float(item.get("r_multiple")) for item in items if item.get("r_multiple") not in (None, "")]
        wins = sum(1 for value in pnl_values if value > 0)
        losses = sum(1 for value in pnl_values if value < 0)
        output[name] = {
            "exit_events": len(items),
            "wins": wins,
            "losses": losses,
            "breakeven": len(items) - wins - losses,
            "realized_pnl": round(sum(pnl_values), 2),
            "average_pnl": rounded_mean(pnl_values),
            "average_R": rounded_mean(r_values),
            "win_rate": round(wins / len(items) * 100, 2) if items else None,
        }
    return output


def best_performance_group(values: dict[str, dict[str, Any]]) -> str:
    eligible = [(name, item) for name, item in values.items() if name != "Unknown" and item.get("exit_events", 0)]
    return max(eligible, key=lambda pair: (to_float(pair[1].get("realized_pnl")), to_float(pair[1].get("average_R"))))[0] if eligible else "INSUFFICIENT_DATA"


def worst_performance_group(values: dict[str, dict[str, Any]]) -> str:
    eligible = [(name, item) for name, item in values.items() if name != "Unknown" and item.get("exit_events", 0)]
    return min(eligible, key=lambda pair: (to_float(pair[1].get("realized_pnl")), to_float(pair[1].get("average_R"))))[0] if eligible else "INSUFFICIENT_DATA"


def data_completeness(
    records: list[dict[str, Any]],
    events: list[dict[str, Any]],
    completed_trades: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    closed = [event for event in events if str(event.get("action") or "") in {"TAKE_PARTIAL_PROFIT", "TAKE_PROFIT", "EXIT_STOP"}]

    def coverage(items: list[dict[str, Any]], key: str) -> dict[str, Any]:
        populated = sum(1 for item in items if item.get(key) not in (None, ""))
        return {
            "populated": populated,
            "total": len(items),
            "coverage_pct": round(populated / len(items) * 100, 2) if items else None,
        }

    output = {
        "decision_trade_id": coverage(records, "trade_id"),
        "closed_event_trade_id": coverage(closed, "trade_id"),
        "closed_event_mfe": coverage(closed, "mfe"),
        "closed_event_mae": coverage(closed, "mae"),
        "closed_event_r_multiple": coverage(closed, "r_multiple"),
        "closed_event_duration": coverage(closed, "duration"),
        "closed_event_outcome_after_5d": coverage(closed, "outcome_after_5d"),
    }
    completed = completed_trades or []
    output.update(
        {
            "completed_trade_trade_id": coverage(completed, "trade_id"),
            "completed_trade_mfe": coverage(completed, "mfe"),
            "completed_trade_mae": coverage(completed, "mae"),
            "completed_trade_r_multiple": coverage(completed, "r_multiple"),
        }
    )
    return output


def recommendations(
    actions: Counter[str],
    shadow: dict[str, Any],
    watch_ready_count: int = 0,
    watch_ready_metrics: dict[str, Any] | None = None,
) -> list[str]:
    items = []
    conversion = (watch_ready_metrics or {}).get("conversion") or {}
    if watch_ready_count:
        items.append("Track WATCH_READY conversion during regular-session confirmation scans.")
    if conversion.get("pending_review_unique_count"):
        items.append("Do not judge WATCH_READY quality until pending off-hours candidates receive regular-session review.")
    if conversion.get("reviewed_unique_count") and not conversion.get("converted_unique_count"):
        items.append("After 2-3 full regular-session days, review whether entry confirmation is too restrictive.")
    if shadow.get("would_buy_but_active_skipped"):
        items.append("Review shadow would-buy candidates that active gates skipped before changing thresholds.")
    if not items:
        items.append("Keep collecting shadow data; no strategy changes are recommended from this sample alone.")
    return items


def rounded_mean(values: list[float]) -> float | None:
    return round(mean(values), 4) if values else None


def realized_max_drawdown(pnl_values: list[float]) -> float | None:
    if not pnl_values:
        return None
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in pnl_values:
        equity += float(pnl or 0.0)
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
    return round(max_drawdown, 2)


def to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
