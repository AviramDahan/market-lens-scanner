from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, datetime
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
    )
    year, week, _ = run_date.isocalendar()
    weekly = build_period_summary(
        period="weekly",
        target_date=run_date,
        decision_dir=decision_dir,
        portfolio=portfolio,
        current_decision_path=current_decision_path,
        run_id=run_id,
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
) -> dict[str, Any]:
    records, files = collect_records(decision_dir, period=period, target_date=target_date)
    if not records and current_decision_path.exists():
        records = read_jsonl(current_decision_path)
        files = [current_decision_path]

    actions = Counter(str(record.get("final_action") or "UNKNOWN") for record in records)
    setups = Counter(str(record.get("setup_type") or "UNKNOWN") for record in records)
    sectors = group_average(records, "sector", "net_rr")
    setup_scores = [to_float(record.get("setup_score")) for record in records if record.get("setup_score") is not None]
    rr1_values = [to_float(record.get("net_rr_1")) for record in records if record.get("net_rr_1") is not None]
    rr2_values = [to_float(record.get("net_rr_2")) for record in records if record.get("net_rr_2") is not None]
    shadow = shadow_metrics(records)
    r_values = [to_float(record.get("r_multiple")) for record in records if record.get("r_multiple") not in (None, "")]
    winners = [value for value in r_values if value > 0]
    losers = [value for value in r_values if value < 0]
    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    week_start, week_end = iso_week_bounds(target_date)
    positions_opened = actions.get("BUY_SIMULATED", 0)
    positions_closed = actions.get("TAKE_PROFIT", 0) + actions.get("EXIT_STOP", 0)
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
        "total_trading_days": len({record_date(record) for record in records if record_date(record)}) if period == "weekly" else None,
        "total_scans": len(files),
        "total_tickers_scanned": len(records),
        "total_result_cards_read": len(records),
        "BUY_SIMULATED_count": actions.get("BUY_SIMULATED", 0),
        "WATCH_READY_count": actions.get("WATCH_READY", 0),
        "WATCH_count": actions.get("WATCH", 0),
        "SKIP_count": actions.get("SKIP", 0),
        "NO_TRADE_count": setups.get("No Trade", 0),
        "open_positions_start": open_positions_start,
        "open_positions_end": open_positions_end,
        "positions_opened_today": positions_opened,
        "positions_closed_today": positions_closed,
        "TP1_hits": actions.get("TAKE_PARTIAL_PROFIT", 0),
        "TP2_hits": actions.get("TAKE_PROFIT", 0),
        "SL_hits": actions.get("EXIT_STOP", 0),
        "partial_exits": actions.get("TAKE_PARTIAL_PROFIT", 0),
        "realized_pnl": portfolio.get("realized_pnl"),
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
        "total_BUY_SIMULATED": actions.get("BUY_SIMULATED", 0),
        "total_WATCH_READY": actions.get("WATCH_READY", 0),
        "total_closed_trades": actions.get("TAKE_PROFIT", 0) + actions.get("EXIT_STOP", 0),
        "win_rate": round(len(winners) / len(r_values) * 100, 2) if r_values else None,
        "average_R": rounded_mean(r_values),
        "median_R": round(median(r_values), 4) if r_values else None,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "average_winner": rounded_mean(winners),
        "average_loser": rounded_mean(losers),
        "max_drawdown": None,
        "best_setup_type": best_counter(setups),
        "worst_setup_type": None,
        "best_shadow_strategy": best_shadow_strategy(shadow),
        "worst_shadow_strategy": worst_shadow_strategy(shadow),
        "performance_by_market_regime": group_counts(records, "market_regime"),
        "performance_by_sector_regime": group_counts(records, "sector_regime"),
        "performance_by_setup_score_bucket": group_counts(records, "setup_score_bucket"),
        "performance_by_shadow_strategy": shadow["would_buy_count_by_strategy"],
        "WATCH_READY_conversion_rate": None,
        "common_missed_opportunities": shadow["would_buy_but_active_skipped"],
        "common_false_positives": [],
        "recommendations_for_next_week": recommendations(actions, shadow),
    }
    return summary


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
    return [
        {
            "ticker": record.get("ticker", ""),
            "final_action": record.get("final_action", ""),
            "setup_score": record.get("setup_score", 0),
            "net_rr": record.get("net_rr", 0),
            "reason": record.get("reason", ""),
        }
        for record in rejected[:10]
    ]


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


def recommendations(actions: Counter[str], shadow: dict[str, Any]) -> list[str]:
    items = []
    if actions.get("WATCH_READY", 0):
        items.append("Track WATCH_READY conversion during regular-session confirmation scans.")
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
