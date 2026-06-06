import logging
import os
import threading
import time
import warnings
from dataclasses import dataclass
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
EARNINGS_CACHE_TTL_SECONDS = int(os.getenv("MARKET_LENS_EARNINGS_CACHE_TTL", "21600"))
_FRAME_CACHE: dict[tuple[str, str, str], tuple[float, pd.DataFrame]] = {}
_EARNINGS_CACHE: dict[str, tuple[float, str | None]] = {}
_CACHE_LOCK = threading.RLock()


@dataclass
class TickerData:
    ticker: str
    daily: pd.DataFrame
    hourly: pd.DataFrame
    weekly: pd.DataFrame


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


def _fetch_frame(ticker: str, interval: str, period: str) -> pd.DataFrame:
    cache_key = (ticker.upper(), interval, period)
    cached = _get_frame_cache(cache_key)
    if cached is not None:
        return cached

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)

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


def _validate_frame(df: pd.DataFrame, ticker: str, interval: str, min_rows: int) -> pd.DataFrame:
    if len(df) < min_rows:
        raise ValueError(
            f"{ticker}: only {len(df)} rows for interval={interval}, need at least {min_rows}"
        )
    return df


_CACHE_MISS = object()


def _get_frame_cache(key: tuple[str, str, str]) -> pd.DataFrame | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _FRAME_CACHE.get(key)
        if not cached:
            return None
        stored_at, frame = cached
        if now - stored_at > DATA_CACHE_TTL_SECONDS:
            _FRAME_CACHE.pop(key, None)
            return None
        return frame.copy()


def _set_frame_cache(key: tuple[str, str, str], frame: pd.DataFrame) -> None:
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
