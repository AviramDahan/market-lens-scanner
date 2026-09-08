"""Exchange-session and provider bar-start timestamp contracts for entry decisions."""

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd

NY_TZ = ZoneInfo("America/New_York")
INTERVAL_MINUTES = {
    "1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30,
    "60m": 60, "90m": 90, "1h": 60,
}


@lru_cache(maxsize=16)
def _schedule(year: int) -> pd.DataFrame:
    import pandas_market_calendars as mcal

    return mcal.get_calendar("NYSE").schedule(f"{year}-01-01", f"{year}-12-31")


@lru_cache(maxsize=2048)
def session_bounds(day: date) -> tuple[datetime, datetime] | None:
    schedule = _schedule(day.year)
    key = pd.Timestamp(day)
    if key not in schedule.index:
        return None
    row = schedule.loc[key]
    return row["market_open"].to_pydatetime(), row["market_close"].to_pydatetime()


def session_status(now: datetime | None = None, *, allow_off_hours_buys: bool = False) -> dict:
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None:
        return _unknown_session("Observation timezone unavailable.")
    current = current.astimezone(NY_TZ)
    try:
        bounds = session_bounds(current.date())
    except Exception:
        return _unknown_session("Exchange calendar unavailable; new entries blocked.", current)
    regular = bool(bounds and bounds[0] <= current < bounds[1])
    minutes = current.hour * 60 + current.minute
    if not bounds:
        phase = "WEEKEND" if current.weekday() >= 5 else "HOLIDAY"
    elif regular:
        phase = "REGULAR"
    elif minutes < 240 or minutes >= 1200:
        phase = "CLOSED"
    elif current < bounds[0]:
        phase = "PRE_MARKET"
    else:
        phase = "AFTER_HOURS"
    return {
        "phase": phase,
        "timestamp": current.isoformat(),
        "regular_session_open": regular,
        "can_open_new_buy": regular or bool(
            allow_off_hours_buys and phase in {"PRE_MARKET", "AFTER_HOURS"}
        ),
        "reason": (
            "Regular market session is open." if regular
            else "Outside exchange regular session; stage for regular-session confirmation."
        ),
        "session_open": bounds[0].isoformat() if bounds else None,
        "session_close": bounds[1].isoformat() if bounds else None,
    }


def _unknown_session(reason: str, current: datetime | None = None) -> dict:
    return {
        "phase": "UNKNOWN", "timestamp": current.isoformat() if current else "",
        "regular_session_open": False, "can_open_new_buy": False, "reason": reason,
    }


def bar_close_time(value, timeframe: str) -> datetime:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("Missing candle timestamp")
    interval = timeframe.removesuffix("_completed")
    if interval == "1d":
        # Daily provider indices are session labels, not intraday observation times.
        day = stamp.tz_convert(NY_TZ).date() if stamp.tzinfo else stamp.date()
        bounds = session_bounds(day)
        if not bounds:
            raise ValueError("Daily candle is not an exchange session")
        return bounds[1]
    if stamp.tzinfo is None:
        raise ValueError("Intraday candle timezone unavailable")
    minutes = INTERVAL_MINUTES.get(interval)
    if minutes is None:
        raise ValueError("Unsupported confirmation timeframe")
    start = stamp.to_pydatetime()
    bounds = session_bounds(start.astimezone(NY_TZ).date())
    if not bounds or not bounds[0] <= start < bounds[1]:
        raise ValueError("Candle starts outside regular session")
    return min(start + timedelta(minutes=minutes), bounds[1])


def completed_frame(frame: pd.DataFrame, timeframe: str, now: datetime) -> pd.DataFrame:
    if now.tzinfo is None or not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("Reliable candle and observation timestamps required")
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("Candle timestamps must be unique and chronological")
    keep = []
    for stamp in frame.index:
        try:
            keep.append(bar_close_time(stamp, timeframe) <= now)
        except ValueError:
            keep.append(False)
    return frame.loc[keep]
