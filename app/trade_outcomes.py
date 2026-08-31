from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from app.data import fetch_daily_frame


def backfill_trade_outcomes(
    wb: Any,
    *,
    max_tickers: int = 8,
    frame_fetcher: Callable[..., pd.DataFrame] = fetch_daily_frame,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    """Backfill lifecycle analytics incrementally without changing trade actions."""
    if "Trade Log" not in wb.sheetnames:
        return {"updated_rows": 0, "fetched_tickers": [], "errors": []}

    ws = wb["Trade Log"]
    closed = reconstruct_closed_trades(ws)
    current_date = as_of_date or datetime.now(ZoneInfo("America/New_York")).date()
    checked_date = current_date.isoformat()
    daily_limit = max(0, int(max_tickers or 0))
    checked_today = sum(1 for trade in closed if trade_checked_on(ws, trade, checked_date))
    remaining_daily_capacity = max(0, daily_limit - checked_today)
    pending = [trade for trade in closed if trade_needs_backfill(ws, trade, checked_date)]
    selected_pending = pending[:remaining_daily_capacity]
    ticker_limit = max(0, int(max_tickers or 0))
    tickers: list[str] = []
    if ticker_limit:
        for trade in selected_pending:
            ticker = trade["ticker"]
            if ticker not in tickers:
                tickers.append(ticker)
            if len(tickers) >= ticker_limit:
                break
    selected_trade_ids = {
        trade["trade_id"] for trade in selected_pending if trade["ticker"] in tickers
    }

    frames: dict[str, pd.DataFrame] = {}
    errors: list[str] = []
    for ticker in tickers:
        try:
            frames[ticker] = frame_fetcher(ticker, period="6mo")
        except Exception as exc:
            errors.append(f"{ticker}: {exc}")

    updated_rows = 0
    for trade in closed:
        if write_trade_identity(ws, trade):
            updated_rows += 1
        if trade["trade_id"] not in selected_trade_ids:
            continue
        frame = frames.get(trade["ticker"])
        analytics = calculate_trade_outcomes(trade, frame)
        if frame is not None:
            analytics["outcome_backfill_checked_date"] = checked_date
        if analytics and write_trade_analytics(ws, trade, analytics):
            updated_rows += 1
    return {
        "updated_rows": updated_rows,
        "pending_trades": len(pending),
        "checked_today": checked_today + len(selected_trade_ids),
        "daily_limit": daily_limit,
        "fetched_tickers": sorted(frames),
        "errors": errors,
    }


def reconstruct_closed_trades(ws: Any) -> list[dict[str, Any]]:
    open_trades: dict[str, dict[str, Any]] = {}
    closed: list[dict[str, Any]] = []
    for row_index in range(2, ws.max_row + 1):
        action = str(ws.cell(row_index, 2).value or "").upper()
        ticker = str(ws.cell(row_index, 3).value or "").upper().strip()
        quantity = int(ws.cell(row_index, 6).value or 0)
        if not ticker or quantity <= 0:
            continue
        if action == "BUY_SIMULATED":
            timestamp = ws.cell(row_index, 1).value
            entry = float(ws.cell(row_index, 4).value or 0)
            decision = parse_json(ws.cell(row_index, 20).value)
            trade_id = str(
                ws.cell(row_index, 21).value
                or decision.get("trade_id")
                or f"{ticker}|{timestamp}|{entry}|{quantity}"
            )
            open_trades[ticker] = {
                "trade_id": trade_id,
                "ticker": ticker,
                "entry_row": row_index,
                "entry_timestamp": timestamp,
                "entry_price": entry,
                "stop_loss": float(ws.cell(row_index, 12).value or 0),
                "target_1": float(ws.cell(row_index, 13).value or 0),
                "target_2": float(ws.cell(row_index, 14).value or 0),
                "initial_quantity": quantity,
                "remaining_quantity": quantity,
                "realized_pnl": 0.0,
                "exit_rows": [],
                "decision": decision,
            }
            continue
        if action not in {"TAKE_PARTIAL_PROFIT", "TAKE_PROFIT", "EXIT_STOP"} or ticker not in open_trades:
            continue

        trade = open_trades[ticker]
        used = min(quantity, int(trade["remaining_quantity"]))
        exit_price = float(ws.cell(row_index, 5).value or 0)
        currency_rate = float(ws.cell(row_index, 7).value or 1)
        trade["realized_pnl"] += (exit_price - float(trade["entry_price"])) * used * currency_rate
        trade["remaining_quantity"] -= used
        trade["exit_rows"].append(row_index)
        if action in {"TAKE_PROFIT", "EXIT_STOP"} or trade["remaining_quantity"] <= 0:
            trade["exit_row"] = row_index
            trade["exit_timestamp"] = ws.cell(row_index, 1).value
            trade["exit_price"] = exit_price
            trade["exit_action"] = action
            closed.append(dict(trade))
            del open_trades[ticker]
    return closed


def trade_checked_on(ws: Any, trade: dict[str, Any], checked_date: str) -> bool:
    decision = parse_json(ws.cell(int(trade["exit_row"]), 20).value)
    return str(decision.get("outcome_backfill_checked_date") or "") == checked_date


def trade_needs_backfill(ws: Any, trade: dict[str, Any], checked_date: str = "") -> bool:
    row = int(trade["exit_row"])
    if checked_date and trade_checked_on(ws, trade, checked_date):
        return False
    # Setup bucket and confirmation can legitimately be absent on historical
    # trades. They must not keep an otherwise complete trade pending forever.
    analytics_columns = (21, 24, 25, 26, 27, 28, 29, 30, 31, 32)
    return any(ws.cell(row, column).value in (None, "") for column in analytics_columns)


def calculate_trade_outcomes(trade: dict[str, Any], frame: pd.DataFrame | None) -> dict[str, Any]:
    if frame is None or frame.empty:
        return {}
    entry_date = timestamp_date(trade.get("entry_timestamp"))
    exit_date = timestamp_date(trade.get("exit_timestamp"))
    if entry_date is None or exit_date is None:
        return {}

    local_dates = pd.Index(frame.index).date
    holding = frame[(local_dates >= entry_date) & (local_dates <= exit_date)]
    entry = float(trade.get("entry_price") or 0)
    stop = float(trade.get("stop_loss") or 0)
    quantity = int(trade.get("initial_quantity") or 0)
    risk_per_share = max(0.0, entry - stop)
    analytics: dict[str, Any] = {
        "trade_id": trade.get("trade_id"),
        "full_trade_pnl": round(float(trade.get("realized_pnl") or 0), 2),
        "full_trade_r_multiple": round(float(trade.get("realized_pnl") or 0) / (risk_per_share * quantity), 4)
        if risk_per_share > 0 and quantity > 0
        else None,
        "duration": duration_text(trade.get("entry_timestamp"), trade.get("exit_timestamp")),
        "exit_reason": trade.get("exit_action"),
        "analytics_source": "daily_bar_approximation",
    }
    if not holding.empty and entry > 0:
        mfe_per_share = max(0.0, float(holding["High"].max()) - entry)
        mae_per_share = max(0.0, entry - float(holding["Low"].min()))
        analytics.update(
            {
                "mfe": round(mfe_per_share * quantity, 2),
                "mae": round(mae_per_share * quantity, 2),
                "mfe_per_share": round(mfe_per_share, 4),
                "mae_per_share": round(mae_per_share, 4),
                "mfe_r": round(mfe_per_share / risk_per_share, 4) if risk_per_share else None,
                "mae_r": round(mae_per_share / risk_per_share, 4) if risk_per_share else None,
            }
        )

    future = frame[local_dates > exit_date]
    exit_price = float(trade.get("exit_price") or 0)
    for horizon in (1, 3, 5, 10):
        key = f"outcome_after_{horizon}d"
        analytics[key] = (
            round((float(future["Close"].iloc[horizon - 1]) / exit_price - 1.0) * 100.0, 4)
            if exit_price > 0 and len(future) >= horizon
            else None
        )
    return analytics


def write_trade_identity(ws: Any, trade: dict[str, Any]) -> bool:
    changed = False
    for row in [trade["entry_row"], *trade["exit_rows"]]:
        if ws.cell(row, 21).value in (None, ""):
            ws.cell(row, 21, trade["trade_id"])
            changed = True
        decision = parse_json(ws.cell(row, 20).value)
        if decision.get("trade_id") != trade["trade_id"]:
            decision["trade_id"] = trade["trade_id"]
            ws.cell(row, 20, json.dumps(decision, ensure_ascii=False, sort_keys=True, default=str))
            changed = True
    return changed


def write_trade_analytics(ws: Any, trade: dict[str, Any], analytics: dict[str, Any]) -> bool:
    row = int(trade["exit_row"])
    decision = parse_json(ws.cell(row, 20).value)
    changed = False
    preserve_if_present = {"mfe", "mae", "mfe_per_share", "mae_per_share", "mfe_r", "mae_r"}
    for key, value in analytics.items():
        if key in preserve_if_present and decision.get(key) not in (None, ""):
            continue
        if value is not None and decision.get(key) != value:
            decision[key] = value
            changed = True

    columns = {
        21: analytics.get("trade_id"),
        24: analytics.get("mfe"),
        25: analytics.get("mae"),
        26: analytics.get("full_trade_r_multiple"),
        27: analytics.get("duration"),
        28: analytics.get("exit_reason"),
        29: analytics.get("outcome_after_1d"),
        30: analytics.get("outcome_after_3d"),
        31: analytics.get("outcome_after_5d"),
        32: analytics.get("outcome_after_10d"),
    }
    for column, value in columns.items():
        if column in {24, 25} and ws.cell(row, column).value not in (None, ""):
            continue
        if value is not None and ws.cell(row, column).value != value:
            ws.cell(row, column, value)
            changed = True
    if changed:
        ws.cell(row, 20, json.dumps(decision, ensure_ascii=False, sort_keys=True, default=str))
    return changed


def parse_json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def timestamp_date(value: Any):
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def duration_text(start_value: Any, end_value: Any) -> str | None:
    try:
        start = datetime.fromisoformat(str(start_value).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(end_value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    hours = max(0.0, (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() / 3600.0)
    return f"{hours:.2f} hours"
