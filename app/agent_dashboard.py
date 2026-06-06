from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, time
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from app.smart_universe import base_universe
from app.watchlists import COMPANY_NAMES


TRACKER_NAME = "market_lens_agent_portfolio_budget_100k.xlsx"
_SECTOR_MAP: dict[str, str] | None = None


def build_agent_dashboard(project_root: Path, selected_date: str | None = None) -> dict[str, Any]:
    tracker_path = project_root / "agent_tracker" / TRACKER_NAME
    results_dir = project_root / "agent_results"
    screenshot_dir = results_dir / "screenshots"
    summary_dir = results_dir / "summaries"

    if not tracker_path.exists():
        return {
            "status": "missing_tracker",
            "error": f"{tracker_path} was not found.",
            "tracker_url": "/agent/tracker",
        }

    wb = load_workbook(tracker_path, data_only=True)
    settings = read_settings(wb)
    currency = str(settings.get("budget_currency") or "USD").upper()
    starting_capital = to_float(
        settings.get("starting_capital_usd") or settings.get("starting_capital_ils"),
        100_000.0,
    )

    updates = read_updates(wb)
    trades = read_trades(wb)
    setup_rows = read_setup_rows(wb)
    latest_update = select_update(updates, selected_date)
    latest_run_timestamp = latest_update.get("timestamp")
    scoped_updates = filter_records_until(updates, latest_run_timestamp)
    scoped_trades = filter_records_until(trades, latest_run_timestamp)
    current_snapshot = not selected_date and latest_update == (updates[-1] if updates else {})
    open_positions = (
        read_open_positions(wb)
        if current_snapshot
        else reconstruct_open_positions(scoped_trades, setup_rows, latest_run_timestamp)
    )
    realized = compute_realized_pnl(scoped_trades)

    cash = to_float(latest_update.get("cash_ils"), compute_cash(scoped_trades, starting_capital))
    exposure = to_float(
        latest_update.get("exposure_ils"),
        sum(to_float(position.get("exposure_ils")) for position in open_positions),
    )
    open_risk = to_float(
        latest_update.get("open_risk_ils"),
        sum(to_float(position.get("open_risk_ils")) for position in open_positions),
    )
    equity = round(cash + exposure, 2)
    total_pnl = round(equity - starting_capital, 2)
    total_pnl_pct = round(total_pnl / starting_capital * 100, 2) if starting_capital else 0

    latest_summary_path = resolve_record_file(latest_update.get("summary"), summary_dir, ".md")
    if not selected_date and not latest_summary_path:
        latest_summary_path = resolve_latest_file(summary_dir, ".md")
    latest_summary = latest_summary_path.read_text(encoding="utf-8") if latest_summary_path else ""
    screenshot_source = latest_update.get("screenshot")
    if not selected_date and not screenshot_source:
        screenshot_source = resolve_latest_file(screenshot_dir, ".png")
    latest_screenshot = resolve_asset_url(screenshot_source)

    latest_setups = [
        row for row in setup_rows if latest_run_timestamp and row.get("run_date") == latest_run_timestamp
    ]
    if not latest_setups and not selected_date:
        latest_setups = setup_rows[-25:]

    action_counts: dict[str, int] = defaultdict(int)
    for setup in latest_setups:
        action_counts[str(setup.get("action") or "UNKNOWN")] += 1

    return {
        "status": "ok",
        "tracker_url": "/agent/tracker",
        "github_actions_url": "https://github.com/AviramDahan/market-lens-scanner/actions/workflows/market-lens-agent.yml",
        "snapshot": {
            "selected_date": selected_date or "",
            "resolved_timestamp": latest_run_timestamp,
            "is_historical": bool(selected_date),
            "available_dates": available_dates(updates),
        },
        "summary": {
            "starting_capital_ils": starting_capital,
            "currency": currency,
            "cash_ils": cash,
            "exposure_ils": exposure,
            "equity_ils": equity,
            "total_pnl_ils": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "realized_pnl_ils": round(realized["total"], 2),
            "unrealized_pnl_ils": round(
                sum(to_float(position.get("unrealized_pnl_ils")) for position in open_positions),
                2,
            ),
            "open_risk_ils": round(open_risk, 2),
            "open_positions": len(open_positions),
            "closed_trades": len(realized["closed"]),
            "wins": realized["wins"],
            "losses": realized["losses"],
            "win_rate": round(realized["wins"] / len(realized["closed"]) * 100, 2)
            if realized["closed"]
            else 0,
        },
        "latest_run": {
            "timestamp": latest_update.get("timestamp"),
            "run_id": latest_update.get("run_id"),
            "tickers": latest_update.get("tickers", []),
            "valid_setups": latest_update.get("valid_setups", 0),
            "actions_summary": latest_update.get("actions_summary", ""),
            "screenshot_url": latest_screenshot,
            "summary_url": resolve_asset_url(latest_summary_path),
            "summary_text": latest_summary,
            "action_counts": dict(sorted(action_counts.items())),
        },
        "equity_curve": build_equity_curve(scoped_updates, starting_capital),
        "open_positions": open_positions,
        "recent_trades": scoped_trades[-30:],
        "closed_trades": realized["closed"][-30:],
        "latest_setups": latest_setups,
        "recent_runs": scoped_updates[-20:],
    }


def read_settings(wb: Any) -> dict[str, Any]:
    ws = wb["Settings"]
    values = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0]:
            values[str(row[0])] = row[1]
    return values


def read_updates(wb: Any) -> list[dict[str, Any]]:
    rows = []
    ws = wb["Update Log"]
    for row in data_rows(ws):
        rows.append(
            {
                "timestamp": row[0],
                "run_id": row[1],
                "tickers": split_tickers(row[2]),
                "valid_setups": to_int(row[3]),
                "actions_summary": row[4] or "",
                "cash_ils": round(to_float(row[5]), 2),
                "exposure_ils": round(to_float(row[6]), 2),
                "open_risk_ils": round(to_float(row[7]), 2),
                "open_positions": to_int(row[8]),
                "summary": row[9] or "",
                "screenshot": row[10] or "",
            }
        )
    return rows


def read_open_positions(wb: Any) -> list[dict[str, Any]]:
    rows = []
    ws = wb["Open Positions"]
    for row in data_rows(ws):
        entry = to_float(row[2])
        current = to_float(row[3], entry)
        target_1 = to_float(row[6])
        progress = 0.0
        if target_1 > entry:
            progress = max(0.0, min(100.0, (current - entry) / (target_1 - entry) * 100))
        rows.append(
            with_position_calculations(
                with_ticker_meta(
                    {
                        "ticker": row[0],
                        "entry_date": row[1],
                        "entry_price_usd": round(entry, 2),
                        "current_price_usd": round(current, 2),
                        "quantity": to_int(row[4]),
                        "stop_loss": round(to_float(row[5]), 2),
                        "target_1": round(target_1, 2),
                        "target_2": round(to_float(row[7]), 2),
                        "status": row[8] or "OPEN",
                        "unrealized_pnl_usd": round(to_float(row[9]), 2),
                        "unrealized_pnl_ils": round(to_float(row[10]), 2),
                        "exposure_ils": round(to_float(row[11]), 2),
                        "open_risk_ils": round(to_float(row[12]), 2),
                        "notes": row[13] or "",
                        "screenshot_url": resolve_asset_url(row[14]),
                        "progress_to_target_1": round(progress, 2),
                    }
                )
            )
        )
    return rows


def read_trades(wb: Any) -> list[dict[str, Any]]:
    rows = []
    ws = wb["Trade Log"]
    for row in data_rows(ws):
        trade = with_ticker_meta(
            {
                "timestamp": row[0],
                "action": row[1],
                "ticker": row[2],
                "entry_price_usd": round(to_float(row[3]), 2) if row[3] is not None else None,
                "exit_price_usd": round(to_float(row[4]), 2) if row[4] is not None else None,
                "quantity": to_int(row[5]),
                "usd_ils": to_float(row[6], 1.0),
                "buy_value_ils": round(to_float(row[7]), 2),
                "sell_value_ils": round(to_float(row[8]), 2),
                "cash_out_ils": round(to_float(row[9]), 2),
                "cash_in_ils": round(to_float(row[10]), 2),
                "stop_loss": round(to_float(row[11]), 2),
                "target_1": round(to_float(row[12]), 2),
                "target_2": round(to_float(row[13]), 2),
                "risk_ils": round(to_float(row[14]), 2),
                "reason": row[15] or "",
                "screenshot_url": resolve_asset_url(row[16]),
            }
        )
        rows.append(with_trade_potential(trade))
    return rows


def read_setup_rows(wb: Any) -> list[dict[str, Any]]:
    rows = []
    ws = wb["Setup Watchlist"]
    for row in data_rows(ws):
        setup = with_ticker_meta(
            {
                "run_date": row[0],
                "ticker": row[1],
                "setup_type": row[2],
                "score": round(to_float(row[3]), 2),
                "current_price_usd": round(to_float(row[4]), 2),
                "buy_zone_low": round(to_float(row[5]), 2) if row[5] is not None else None,
                "buy_zone_high": round(to_float(row[6]), 2) if row[6] is not None else None,
                "stop_loss": round(to_float(row[7]), 2) if row[7] is not None else None,
                "target_1": round(to_float(row[8]), 2) if row[8] is not None else None,
                "target_2": round(to_float(row[9]), 2) if row[9] is not None else None,
                "risk_reward": round(to_float(row[10]), 2),
                "reason": row[11] or "",
                "action": row[12] or "",
                "feedback": row[13] or "",
            }
        )
        rows.append(with_setup_potential(setup))
    return rows


def select_update(updates: list[dict[str, Any]], selected_date: str | None) -> dict[str, Any]:
    if not updates:
        return {}
    if not selected_date:
        return updates[-1]

    end_of_day = parse_selected_date_end(selected_date)
    if not end_of_day:
        return updates[-1]

    selected = [update for update in updates if parse_timestamp(update.get("timestamp")) <= end_of_day]
    return selected[-1] if selected else {}


def filter_records_until(records: list[dict[str, Any]], cutoff: Any, key: str = "timestamp") -> list[dict[str, Any]]:
    if not cutoff:
        return []
    cutoff_time = parse_timestamp(cutoff)
    return [record for record in records if parse_timestamp(record.get(key)) <= cutoff_time]


def available_dates(updates: list[dict[str, Any]]) -> list[str]:
    dates = []
    seen = set()
    for update in updates:
        parsed = parse_timestamp(update.get("timestamp"))
        text = parsed.date().isoformat()
        if text not in seen:
            seen.add(text)
            dates.append(text)
    return dates


def reconstruct_open_positions(
    trades: list[dict[str, Any]],
    setup_rows: list[dict[str, Any]],
    cutoff: Any,
) -> list[dict[str, Any]]:
    positions: dict[str, dict[str, Any]] = {}
    latest_setup = latest_setup_by_ticker(setup_rows, cutoff)

    for trade in trades:
        ticker = str(trade.get("ticker") or "")
        if not ticker:
            continue
        action = str(trade.get("action") or "")
        quantity = to_int(trade.get("quantity"))
        if quantity <= 0:
            continue

        if action == "BUY_SIMULATED":
            entry = to_float(trade.get("entry_price_usd"))
            current = to_float(latest_setup.get(ticker, {}).get("current_price_usd"), entry)
            positions[ticker] = with_ticker_meta(
                {
                    "ticker": ticker,
                    "entry_date": trade.get("timestamp"),
                    "entry_price_usd": round(entry, 2),
                    "current_price_usd": round(current, 2),
                    "quantity": quantity,
                    "stop_loss": round(to_float(trade.get("stop_loss")), 2),
                    "target_1": round(to_float(trade.get("target_1")), 2),
                    "target_2": round(to_float(trade.get("target_2")), 2),
                    "status": "OPEN",
                    "notes": trade.get("reason") or "",
                    "screenshot_url": trade.get("screenshot_url") or "",
                }
            )
            continue

        if action not in {"TAKE_PARTIAL_PROFIT", "TAKE_PROFIT", "EXIT_STOP"} or ticker not in positions:
            continue

        if action == "TAKE_PARTIAL_PROFIT":
            positions[ticker]["quantity"] = max(0, to_int(positions[ticker].get("quantity")) - quantity)
            positions[ticker]["stop_loss"] = positions[ticker]["entry_price_usd"]
            positions[ticker]["notes"] = "Partial profit taken; stop moved to breakeven."
            if positions[ticker]["quantity"] <= 0:
                positions.pop(ticker, None)
            continue

        positions.pop(ticker, None)

    rebuilt = []
    for ticker, position in positions.items():
        setup = latest_setup.get(ticker, {})
        if setup:
            position["current_price_usd"] = setup.get("current_price_usd") or position["current_price_usd"]
        rebuilt.append(with_position_calculations(position))
    return rebuilt


def latest_setup_by_ticker(setup_rows: list[dict[str, Any]], cutoff: Any) -> dict[str, dict[str, Any]]:
    if not cutoff:
        return {}
    cutoff_time = parse_timestamp(cutoff)
    latest: dict[str, dict[str, Any]] = {}
    for row in setup_rows:
        if parse_timestamp(row.get("run_date")) <= cutoff_time:
            latest[str(row.get("ticker") or "")] = row
    return latest


def with_ticker_meta(record: dict[str, Any]) -> dict[str, Any]:
    ticker = str(record.get("ticker") or "").upper()
    record["ticker"] = ticker
    record["company_name"] = COMPANY_NAMES.get(ticker, ticker)
    record["sector"] = sector_map().get(ticker, "Unknown")
    return record


def sector_map() -> dict[str, str]:
    global _SECTOR_MAP
    if _SECTOR_MAP is None:
        _SECTOR_MAP = base_universe()
    return _SECTOR_MAP


def with_position_calculations(position: dict[str, Any]) -> dict[str, Any]:
    quantity = to_int(position.get("quantity"))
    current = to_float(position.get("current_price_usd"), position.get("entry_price_usd"))
    entry = to_float(position.get("entry_price_usd"), current)
    stop = to_float(position.get("stop_loss"))
    target_1 = to_float(position.get("target_1"))
    target_2 = to_float(position.get("target_2"))

    position["unrealized_pnl_usd"] = round((current - entry) * quantity, 2)
    position["unrealized_pnl_ils"] = position["unrealized_pnl_usd"]
    position["exposure_ils"] = round(current * quantity, 2)
    position["open_risk_ils"] = round(max(0.0, current - stop) * quantity, 2)
    position["potential_profit_t1_ils"] = round(max(0.0, target_1 - current) * quantity, 2)
    position["potential_profit_t2_ils"] = round(max(0.0, target_2 - current) * quantity, 2)
    position["potential_profit_plan_ils"] = weighted_target_profit(
        position["potential_profit_t1_ils"],
        position["potential_profit_t2_ils"],
        bool(target_1),
        bool(target_2),
    )
    position["reward_to_risk_plan"] = (
        round(position["potential_profit_plan_ils"] / position["open_risk_ils"], 2)
        if position["open_risk_ils"] > 0
        else 0
    )
    position["progress_to_target_1"] = progress_to_target(current, entry, target_1)
    return position


def with_trade_potential(trade: dict[str, Any]) -> dict[str, Any]:
    if trade.get("action") != "BUY_SIMULATED":
        trade["potential_profit_plan_ils"] = 0.0
        return trade

    quantity = to_int(trade.get("quantity"))
    entry = to_float(trade.get("entry_price_usd"))
    target_1 = to_float(trade.get("target_1"))
    target_2 = to_float(trade.get("target_2"))
    t1 = round(max(0.0, target_1 - entry) * quantity, 2)
    t2 = round(max(0.0, target_2 - entry) * quantity, 2)
    trade["potential_profit_t1_ils"] = t1
    trade["potential_profit_t2_ils"] = t2
    trade["potential_profit_plan_ils"] = weighted_target_profit(t1, t2, bool(target_1), bool(target_2))
    trade["reward_to_risk_plan"] = (
        round(trade["potential_profit_plan_ils"] / trade["risk_ils"], 2) if trade["risk_ils"] > 0 else 0
    )
    return trade


def with_setup_potential(setup: dict[str, Any]) -> dict[str, Any]:
    current = to_float(setup.get("current_price_usd"))
    target_1 = to_float(setup.get("target_1"))
    target_2 = to_float(setup.get("target_2"))
    t1 = round(max(0.0, target_1 - current), 2)
    t2 = round(max(0.0, target_2 - current), 2)
    setup["potential_profit_t1_per_share"] = t1
    setup["potential_profit_t2_per_share"] = t2
    setup["potential_profit_plan_per_share"] = weighted_target_profit(t1, t2, bool(target_1), bool(target_2))
    return setup


def weighted_target_profit(t1: float, t2: float, has_t1: bool, has_t2: bool) -> float:
    if has_t1 and has_t2:
        return round((t1 * 0.5) + (t2 * 0.5), 2)
    if has_t1:
        return round(t1, 2)
    if has_t2:
        return round(t2, 2)
    return 0.0


def progress_to_target(current: float, entry: float, target_1: float) -> float:
    if target_1 <= entry:
        return 0.0
    return round(max(0.0, min(100.0, (current - entry) / (target_1 - entry) * 100)), 2)


def compute_cash(trades: list[dict[str, Any]], starting_capital: float) -> float:
    cash = starting_capital
    for trade in trades:
        cash -= to_float(trade.get("cash_out_ils"))
        cash += to_float(trade.get("cash_in_ils"))
    return round(cash, 2)


def compute_realized_pnl(trades: list[dict[str, Any]]) -> dict[str, Any]:
    lots: dict[str, deque[dict[str, float]]] = defaultdict(deque)
    closed = []
    total = 0.0
    wins = 0
    losses = 0

    for trade in trades:
        ticker = str(trade.get("ticker") or "")
        quantity = to_int(trade.get("quantity"))
        action = str(trade.get("action") or "")
        if quantity <= 0 or not ticker:
            continue

        if action == "BUY_SIMULATED":
            cost = to_float(trade.get("cash_out_ils"), trade.get("buy_value_ils"))
            lots[ticker].append({"quantity": quantity, "unit_cost": cost / quantity if quantity else 0})
            continue

        if action not in {"TAKE_PARTIAL_PROFIT", "TAKE_PROFIT", "EXIT_STOP"}:
            continue

        remaining = quantity
        cost_basis = 0.0
        while remaining > 0 and lots[ticker]:
            lot = lots[ticker][0]
            used = min(remaining, int(lot["quantity"]))
            cost_basis += used * lot["unit_cost"]
            lot["quantity"] -= used
            remaining -= used
            if lot["quantity"] <= 0:
                lots[ticker].popleft()

        cash_in = to_float(trade.get("cash_in_ils"), trade.get("sell_value_ils"))
        pnl = round(cash_in - cost_basis, 2)
        total += pnl
        wins += 1 if pnl > 0 else 0
        losses += 1 if pnl < 0 else 0
        closed.append({**trade, "cost_basis_ils": round(cost_basis, 2), "pnl_ils": pnl})

    return {"total": total, "closed": closed, "wins": wins, "losses": losses}


def build_equity_curve(updates: list[dict[str, Any]], starting_capital: float) -> list[dict[str, Any]]:
    curve = [{"timestamp": "Start", "equity_ils": round(starting_capital, 2), "pnl_ils": 0.0}]
    for update in updates:
        equity = to_float(update.get("cash_ils")) + to_float(update.get("exposure_ils"))
        curve.append(
            {
                "timestamp": update.get("timestamp"),
                "equity_ils": round(equity, 2),
                "pnl_ils": round(equity - starting_capital, 2),
            }
        )
    return curve


def data_rows(ws: Any) -> list[tuple[Any, ...]]:
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if any(value is not None for value in row):
            rows.append(row)
    return rows


def parse_selected_date_end(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return datetime.combine(parsed, time.max)


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if not value:
        return datetime.min
    text = str(value).strip()
    if not text:
        return datetime.min
    normalized = text.replace("Z", "").replace(" ", "T")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        try:
            return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return datetime.min


def resolve_latest_file(directory: Path, suffix: str) -> Path | None:
    if not directory.exists():
        return None
    files = sorted(
        [path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == suffix],
        key=lambda path: path.stat().st_mtime,
    )
    return files[-1] if files else None


def resolve_record_file(value: Any, directory: Path, suffix: str) -> Path | None:
    if not value:
        return None
    text = str(value).replace("\\", "/")
    path = Path(text)
    if path.exists() and path.suffix.lower() == suffix:
        return path
    candidate = directory / path.name
    if candidate.exists() and candidate.suffix.lower() == suffix:
        return candidate
    return None


def resolve_asset_url(value: Any) -> str:
    if not value:
        return ""
    text = str(value).replace("\\", "/")
    marker = "agent_results/"
    if marker in text:
        return "/agent-results/" + text.split(marker, 1)[1]
    return text


def split_tickers(value: Any) -> list[str]:
    return [ticker for ticker in str(value or "").replace(",", " ").split() if ticker]


def to_float(value: Any, default: Any = 0.0) -> float:
    try:
        if value is None or value == "":
            return float(default or 0.0)
        return float(value)
    except (TypeError, ValueError):
        return float(default or 0.0)


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default
