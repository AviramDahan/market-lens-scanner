"""Position marks are completed intraday observations, never setup prices."""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.data import fetch_intraday_frame
from app.trading_clock import INTERVAL_MINUTES, session_status


def validated_position_frame(frame: pd.DataFrame, *, interval: str, now: datetime) -> pd.DataFrame:
    if now.tzinfo is None or not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("Position bars require timezone-aware observation timestamps")
    if frame.empty or frame.index.tz is None or not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("Position bars must be nonempty, ordered, unique and timezone-aware")
    minutes = INTERVAL_MINUTES.get(interval)
    if minutes is None:
        raise ValueError("Unsupported position bar interval")
    # Ignore an unfinished candle before examining its still-changing OHLC.
    completed = frame.loc[frame.index + timedelta(minutes=minutes) <= now]
    if completed.empty:
        raise ValueError("No completed position bars available")
    prices = completed[["Open", "High", "Low", "Close"]].astype(float)
    if (not all(math.isfinite(v) and v > 0 for v in prices.to_numpy().flat)
            or (prices["Low"] > prices["High"]).any()
            or (prices["Close"] < prices["Low"]).any()
            or (prices["Close"] > prices["High"]).any()
            or (prices["Open"] < prices["Low"]).any()
            or (prices["Open"] > prices["High"]).any()):
        raise ValueError("Position bars have invalid OHLC ranges")
    # Extended-hours bars use the same duration, without filtering their session.
    if session_status(now)["regular_session_open"]:
        max_age = max(60, int(os.getenv("MARKET_LENS_POSITION_BAR_MAX_AGE_SECONDS", "300")))
        age = (now - (completed.index[-1].to_pydatetime() + timedelta(minutes=minutes))).total_seconds()
        if age > max_age:
            raise ValueError(f"Position bars are stale during regular session ({age:.0f} seconds old)")
    return completed


def fetch_position_mark(ticker: str, *, now: datetime | None = None) -> dict:
    observed_at = now or datetime.now(timezone.utc)
    interval = os.getenv("MARKET_LENS_MONITOR_INTERVAL", "1m")
    frame = fetch_intraday_frame(ticker, period="5d", interval=interval, include_prepost=True)
    completed = validated_position_frame(frame, interval=interval, now=observed_at)
    return {
        "position_mark_price": round(float(completed["Close"].iloc[-1]), 4),
        "position_mark_timestamp": completed.index[-1].isoformat(),
        "position_mark_source": f"yfinance_completed_{interval}",
        "position_market_data_status": "VALIDATED",
        "position_execution_owner": "POSITION_MONITOR_COMPLETED_BARS",
    }
