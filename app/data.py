import logging
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
    try:
        calendar = yf.Ticker(ticker).calendar
    except Exception:
        return None
    if calendar is None:
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
            return None

    for candidate in candidates:
        if candidate is None:
            continue
        try:
            return pd.Timestamp(candidate).isoformat()
        except Exception:
            continue
    return None


def fetch_last_price(ticker: str) -> float:
    df = _fetch_frame(ticker, "1d", "5d")
    return float(df["Close"].iloc[-1])


def _fetch_frame(ticker: str, interval: str, period: str) -> pd.DataFrame:
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

    return df


def _validate_frame(df: pd.DataFrame, ticker: str, interval: str, min_rows: int) -> pd.DataFrame:
    if len(df) < min_rows:
        raise ValueError(
            f"{ticker}: only {len(df)} rows for interval={interval}, need at least {min_rows}"
        )
    return df
