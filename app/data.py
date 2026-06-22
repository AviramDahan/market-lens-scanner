import logging
import os
import threading
import time
import warnings
from dataclasses import dataclass
from datetime import time as dt_time
from pathlib import Path

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

YFINANCE_CACHE_DIR = Path(__file__).parent.parent / ".yfinance-cache"
YFINANCE_CACHE_DIR.mkdir(exist_ok=True)
yf.set_tz_cache_location(str(YFINANCE_CACHE_DIR))

WEEKLY_PERIOD = "5y"
MIN_DAILY_ROWS = 50
MIN_HOURLY_ROWS = 100
MIN_WEEKLY_ROWS = 200
HOURLY_PERIOD_BY_ANALYSIS_PERIOD = {
    "3mo": "30d",
    "6mo": "60d",
    "1y": "60d",
    "2y": "60d",
}
DATA_CACHE_TTL_SECONDS = int(os.getenv("MARKET_LENS_DATA_CACHE_TTL", "900"))
EXTENDED_HOURS_CACHE_TTL_SECONDS = int(os.getenv("MARKET_LENS_EXTENDED_HOURS_CACHE_TTL", "45"))
EARNINGS_CACHE_TTL_SECONDS = int(os.getenv("MARKET_LENS_EARNINGS_CACHE_TTL", "21600"))
NY_TZ = "America/New_York"
_FRAME_CACHE: dict[tuple[str, str, str, bool], tuple[float, pd.DataFrame]] = {}
_EARNINGS_CACHE: dict[str, tuple[float, str | None]] = {}
_CACHE_LOCK = threading.RLock()


@dataclass
class TickerData:
    ticker: str
    daily: pd.DataFrame
    hourly: pd.DataFrame
    weekly: pd.DataFrame


@dataclass
class ExtendedHoursQuote:
    phase: str
    label: str
    price: float | None
    timestamp: str | None
    regular_close: float | None
    change: float
    change_pct: float
    is_extended: bool
    source: str
    note: str


def fetch_ticker(ticker: str, analysis_period: str = "6mo") -> TickerData:
    hourly_period = HOURLY_PERIOD_BY_ANALYSIS_PERIOD.get(analysis_period, "60d")

    daily = _fetch_frame(ticker, "1d", analysis_period)
    daily = _validate_frame(daily, ticker, "1d", MIN_DAILY_ROWS)

    hourly = _fetch_frame(ticker, "1h", hourly_period)
    hourly = _validate_frame(hourly, ticker, "1h", MIN_HOURLY_ROWS)

    weekly = _fetch_frame(ticker, "1wk", WEEKLY_PERIOD)
    weekly = _validate_frame(weekly, ticker, "1wk", MIN_WEEKLY_ROWS)

    return TickerData(ticker=ticker, daily=daily, hourly=hourly, weekly=weekly)


def fetch_spy_returns(period: str = "3mo") -> "pd.Series":
    df = _fetch_frame("SPY", "1d", period)
    return df["Close"].pct_change().dropna()


def fetch_daily_frame(ticker: str, period: str = "6mo") -> pd.DataFrame:
    return _validate_frame(_fetch_frame(ticker, "1d", period), ticker, "1d", MIN_DAILY_ROWS)


def fetch_intraday_frame(
    ticker: str,
    period: str = "5d",
    interval: str = "1m",
    include_prepost: bool = False,
) -> pd.DataFrame:
    return _fetch_frame(ticker, interval, period, include_prepost=include_prepost)


def fetch_extended_hours_quote(ticker: str) -> ExtendedHoursQuote:
    frame = fetch_intraday_frame(ticker, period="5d", interval="1m", include_prepost=True)
    if frame.empty:
        raise ValueError(f"{ticker}: no extended-hours data returned")

    close = frame["Close"].dropna()
    if close.empty:
        raise ValueError(f"{ticker}: extended-hours data has no close price")

    last_timestamp = close.index[-1]
    last_local = last_timestamp.tz_convert(NY_TZ)
    price = float(close.iloc[-1])
    phase = _market_phase_for_local_timestamp(last_local)
    current_phase = _market_phase_for_local_timestamp(pd.Timestamp.now(tz=NY_TZ))
    label = _extended_hours_label(phase, current_phase, last_local)
    regular_close = _latest_regular_close(frame)

    change = 0.0
    change_pct = 0.0
    note = "Extended-hours quote is informational; strategy confirmation still uses completed regular-session candles."
    if regular_close and regular_close > 0:
        change = price - regular_close
        change_pct = change / regular_close * 100.0
    else:
        note = "Regular close unavailable; extended-hours change cannot be calculated."

    return ExtendedHoursQuote(
        phase=phase,
        label=label,
        price=round(price, 4),
        timestamp=last_local.isoformat(),
        regular_close=round(regular_close, 4) if regular_close else None,
        change=round(change, 4),
        change_pct=round(change_pct, 4),
        is_extended=phase in {"PRE_MARKET", "AFTER_HOURS"},
        source="yfinance_prepost_1m",
        note=note,
    )


def fetch_next_earnings_date(ticker: str) -> str | None:
    cache_key = ticker.upper()
    cached = _get_earnings_cache(cache_key)
    if cached is not _CACHE_MISS:
        return cached

    try:
        calendar = yf.Ticker(ticker).calendar
    except Exception:
        _set_earnings_cache(cache_key, None)
        return None
    if calendar is None:
        _set_earnings_cache(cache_key, None)
        return None

    candidates = []
    if isinstance(calendar, dict):
        raw = calendar.get("Earnings Date") or calendar.get("EarningsDate")
        if isinstance(raw, list):
            candidates.extend(raw)
        elif raw is not None:
            candidates.append(raw)
    elif hasattr(calendar, "loc"):
        try:
            raw = calendar.loc["Earnings Date"]
            if hasattr(raw, "tolist"):
                candidates.extend(raw.tolist())
            else:
                candidates.append(raw)
        except Exception:
            _set_earnings_cache(cache_key, None)
            return None

    for candidate in candidates:
        if candidate is None:
            continue
        try:
            value = pd.Timestamp(candidate).isoformat()
            _set_earnings_cache(cache_key, value)
            return value
        except Exception:
            continue
    _set_earnings_cache(cache_key, None)
    return None


def fetch_last_price(ticker: str) -> float:
    df = _fetch_frame(ticker, "1d", "5d")
    return float(df["Close"].iloc[-1])


def _fetch_frame(ticker: str, interval: str, period: str, include_prepost: bool = False) -> pd.DataFrame:
    cache_key = (ticker.upper(), interval, period, include_prepost)
    cached = _get_frame_cache(cache_key, _frame_cache_ttl(interval, include_prepost))
    if cached is not None:
        return cached

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = yf.Ticker(ticker).history(
            period=period,
            interval=interval,
            auto_adjust=True,
            prepost=include_prepost,
        )

    if df.empty:
        raise ValueError(f"{ticker}: no data returned for interval={interval}")

    df = df.rename(columns=str.title)
    required = {"Open", "High", "Low", "Close", "Volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{ticker}: missing columns {missing}")

    df = df.dropna(subset=["Close"])

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    _set_frame_cache(cache_key, df)
    return df.copy()


def _frame_cache_ttl(interval: str, include_prepost: bool) -> int:
    if include_prepost and interval in {"1m", "2m", "5m"}:
        return EXTENDED_HOURS_CACHE_TTL_SECONDS
    return DATA_CACHE_TTL_SECONDS


def _market_phase_for_local_timestamp(timestamp: pd.Timestamp) -> str:
    local_time = timestamp.time()
    if timestamp.weekday() >= 5:
        return "CLOSED"
    if dt_time(4, 0) <= local_time < dt_time(9, 30):
        return "PRE_MARKET"
    if dt_time(9, 30) <= local_time < dt_time(16, 0):
        return "REGULAR"
    if dt_time(16, 0) <= local_time <= dt_time(20, 0):
        return "AFTER_HOURS"
    return "CLOSED"


def _extended_hours_label(quote_phase: str, current_phase: str, quote_time: pd.Timestamp) -> str:
    base = {
        "PRE_MARKET": "Pre-market",
        "AFTER_HOURS": "After-hours",
        "REGULAR": "Regular",
        "CLOSED": "Closed",
    }.get(quote_phase, "Extended")
    now_local = pd.Timestamp.now(tz=NY_TZ)
    if quote_time.date() != now_local.date() or current_phase == "CLOSED":
        if quote_phase == "REGULAR":
            return "Last regular"
        if quote_phase in {"PRE_MARKET", "AFTER_HOURS"}:
            return f"Last {base.lower()}"
    return base


def _latest_regular_close(frame: pd.DataFrame) -> float | None:
    if frame.empty:
        return None
    local_index = frame.index.tz_convert(NY_TZ)
    regular_mask = [
        ts.weekday() < 5 and dt_time(9, 30) <= ts.time() < dt_time(16, 0)
        for ts in local_index
    ]
    regular = frame.loc[regular_mask]
    close = regular["Close"].dropna()
    if close.empty:
        return None
    return float(close.iloc[-1])


def _validate_frame(df: pd.DataFrame, ticker: str, interval: str, min_rows: int) -> pd.DataFrame:
    if len(df) < min_rows:
        raise ValueError(
            f"{ticker}: only {len(df)} rows for interval={interval}, need at least {min_rows}"
        )
    return df


_CACHE_MISS = object()


def _get_frame_cache(key: tuple[str, str, str, bool], ttl_seconds: int) -> pd.DataFrame | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _FRAME_CACHE.get(key)
        if not cached:
            return None
        stored_at, frame = cached
        if now - stored_at > ttl_seconds:
            _FRAME_CACHE.pop(key, None)
            return None
        return frame.copy()


def _set_frame_cache(key: tuple[str, str, str, bool], frame: pd.DataFrame) -> None:
    with _CACHE_LOCK:
        _FRAME_CACHE[key] = (time.monotonic(), frame.copy())


def _get_earnings_cache(key: str) -> object:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _EARNINGS_CACHE.get(key)
        if not cached:
            return _CACHE_MISS
        stored_at, value = cached
        if now - stored_at > EARNINGS_CACHE_TTL_SECONDS:
            _EARNINGS_CACHE.pop(key, None)
            return _CACHE_MISS
        return value


def _set_earnings_cache(key: str, value: str | None) -> None:
    with _CACHE_LOCK:
        _EARNINGS_CACHE[key] = (time.monotonic(), value)
