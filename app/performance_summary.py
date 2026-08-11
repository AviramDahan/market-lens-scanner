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
) -> dict[str, Any]:
    records, files = collect_records(decision_dir, period=period, target_date=target_date)
    if not records and current_decision_path.exists():
        records = read_jsonl(current_decision_path)
        files = [current_decision_path]

    period_trade_events = [
        event for event in trade_events or [] if in_period(event, period, target_date)
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
    watch_ready_metrics = watch_ready_analytics(records)
    trade_r_values = [
        to_float(event.get("r_multiple"))
        for event in period_trade_events
        if event.get("action") in {"TAKE_PARTIAL_PROFIT", "TAKE_PROFIT", "EXIT_STOP"}
        and event.get("r_multiple") not in (None, "")
    ]
    decision_r_values = [to_float(record.get("r_multiple")) for record in records if record.get("r_multiple") not in (None, "")]
    r_values = trade_r_values or decision_r_values
    winners = [value for value in r_values if value > 0]
    losers = [value for value in r_values if value < 0]
    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    week_start, week_end = iso_week_bounds(target_date)
    positions_opened = trade_actions.get("BUY_SIMULATED", actions.get("BUY_SIMULATED", 0))
    positions_closed = trade_actions.get("TAKE_PROFIT", actions.get("TAKE_PROFIT", 0)) + trade_actions.get(
        "EXIT_STOP", actions.get("EXIT_STOP", 0)
    )
    open_positions_end = portfolio.get("open_positions_end")
    open_positions_start = infer_open_positions_start(
        open_positions_end=open_positions_end,
        positions_opened=positions_opened,
        positions_closed=positions_closed,
        fallback=portfolio.get("open_positions_start"),
    )

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
        "realized_pnl": portfolio.get("realized_pnl", realized_pnl_from_events(period_trade_events)),
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
        "average_rr_to_target_1": rounded_mean(rr1_values),
        "average_rr_to_target_2": rounded_mean(rr2_values),
        "average_confidence_by_shadow_strategy": shadow["average_confidence_by_strategy"],
        "shadow_strategies_would_buy_count_by_strategy": shadow["would_buy_count_by_strategy"],
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
        "total_BUY_SIMULATED": trade_actions.get("BUY_SIMULATED", actions.get("BUY_SIMULATED", 0)),
        "total_WATCH_READY": watch_ready_count,
        "total_closed_trades": positions_closed,
        "win_rate": round(len(winners) / len(r_values) * 100, 2) if r_values else None,
        "average_R": rounded_mean(r_values),
        "median_R": round(median(r_values), 4) if r_values else None,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "average_winner": rounded_mean(winners),
        "average_loser": rounded_mean(losers),
        "max_drawdown": None,
        "best_setup_type": best_counter(actionable_setups),
        "worst_setup_type": None,
        "best_shadow_strategy": best_shadow_strategy(shadow),
        "worst_shadow_strategy": worst_shadow_strategy(shadow),
        "performance_by_market_regime": group_counts(records, "market_regime"),
        "performance_by_sector_regime": group_counts(records, "sector_regime"),
        "performance_by_setup_score_bucket": group_counts(records, "setup_score_bucket"),
        "performance_by_shadow_strategy": shadow["would_buy_count_by_strategy"],
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
    if action == "WATCH_READY":
        return True
    if record.get("off_hours_candidate") or record.get("regular_session_confirmation_required"):
        return True
    reason = str(record.get("reason") or "").upper()
    if reason.startswith("WATCH_READY:"):
        return True
    return any(str(warning).upper().startswith("WATCH_READY:") for warning in record.get("warnings") or [])


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
    would_buy: Counter[str] = Counter()
    top_candidates = []
    skipped = []
    agreed = []
    for record in records:
        final_action = str(record.get("final_action") or "")
        ticker = str(record.get("ticker") or "")
        for strategy in record.get("shadow_strategies") or []:
            name = str(strategy.get("name") or "UNKNOWN")
            confidence = to_float(strategy.get("confidence"))
            confidences[name].append(confidence)
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
                if final_action != "BUY_SIMULATED":
                    skipped.append(item)
                else:
                    agreed.append(item)
            elif final_action != "BUY_SIMULATED":
                agreed.append(item)
    top_candidates.sort(key=lambda item: item["confidence"], reverse=True)
    return {
        "average_confidence_by_strategy": {name: rounded_mean(values) for name, values in sorted(confidences.items())},
        "would_buy_count_by_strategy": dict(sorted(would_buy.items())),
        "top_candidates": top_candidates[:10],
        "would_buy_but_active_skipped": skipped[:10],
        "agreed_with_active_agent": agreed[:10],
    }


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
        for warning in record.get("warnings") or []:
            counter[str(warning)[:140]] += 1
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


def best_shadow_strategy(metrics: dict[str, Any]) -> str:
    values = metrics.get("average_confidence_by_strategy") or {}
    return max(values.items(), key=lambda item: item[1])[0] if values else ""


def worst_shadow_strategy(metrics: dict[str, Any]) -> str:
    values = metrics.get("average_confidence_by_strategy") or {}
    return min(values.items(), key=lambda item: item[1])[0] if values else ""


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


def to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
