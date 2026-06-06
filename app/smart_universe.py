from __future__ import annotations

import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from app.data import fetch_daily_frame
from app.indicators import compute_atr
from app.watchlists import COMPANY_NAMES, WATCHLISTS


DEFAULT_LIMIT = int(os.getenv("MARKET_LENS_SMART_LIMIT", "35"))
DEFAULT_MAX_PER_SECTOR = int(os.getenv("MARKET_LENS_SMART_MAX_PER_SECTOR", "5"))
DEFAULT_WORKERS = int(os.getenv("MARKET_LENS_SMART_WORKERS", "8"))
MIN_PRICE = float(os.getenv("MARKET_LENS_SMART_MIN_PRICE", "10"))
MIN_DOLLAR_VOLUME = float(os.getenv("MARKET_LENS_SMART_MIN_DOLLAR_VOLUME", "100000000"))
MIN_ATR_PCT = float(os.getenv("MARKET_LENS_SMART_MIN_ATR_PCT", "1.2"))
MAX_ATR_PCT = float(os.getenv("MARKET_LENS_SMART_MAX_ATR_PCT", "8.0"))
SMART_CACHE_TTL_SECONDS = int(os.getenv("MARKET_LENS_SMART_CACHE_TTL", "21600"))

SECTOR_WATCHLIST_IDS = {
    "technology": "Technology",
    "ai-semiconductors": "Semiconductors",
    "financials": "Financials",
    "healthcare": "Healthcare",
    "defense-industrials": "Industrials",
    "energy": "Energy",
    "consumer": "Consumer",
    "utilities-real-assets": "Utilities / Real Assets",
}

SECTOR_OVERRIDES = {
    "AMZN": "Consumer",
    "GOOGL": "Communication Services",
    "META": "Communication Services",
    "NFLX": "Communication Services",
    "TSLA": "Consumer",
    "BRK-B": "Financials",
    "LIN": "Materials",
}

_SMART_CACHE: dict[tuple[str, int, int], tuple[float, dict[str, Any]]] = {}


@dataclass(frozen=True)
class SmartCandidate:
    ticker: str
    name: str
    sector: str
    price: float
    avg_dollar_volume: float
    atr_pct: float
    return_1m: float
    return_3m: float
    return_6m: float
    relative_strength: float
    trend_score: float
    volume_score: float
    volatility_score: float
    score: float
    reason: str


def build_smart_universe(
    *,
    analysis_period: str = "6mo",
    limit: int = DEFAULT_LIMIT,
    max_per_sector: int = DEFAULT_MAX_PER_SECTOR,
) -> dict[str, Any]:
    limit = max(5, min(100, int(limit)))
    max_per_sector = max(1, min(20, int(max_per_sector)))
    cache_key = (analysis_period, limit, max_per_sector)
    cached = _SMART_CACHE.get(cache_key)
    now = time.monotonic()
    if cached and now - cached[0] < SMART_CACHE_TTL_SECONDS:
        return cached[1]

    base = base_universe()
    benchmarks = fetch_benchmark_frames(analysis_period)
    candidates: list[SmartCandidate] = []
    errors: dict[str, str] = {}
    max_workers = max(1, min(DEFAULT_WORKERS, len(base)))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(score_ticker, ticker, sector, analysis_period, benchmarks): ticker
            for ticker, sector in base.items()
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                candidate = future.result()
                if candidate:
                    candidates.append(candidate)
            except Exception as exc:
                errors[ticker] = str(exc)

    ranked = sorted(candidates, key=lambda item: item.score, reverse=True)
    selected = diversify(ranked, limit=limit, max_per_sector=max_per_sector)
    payload = {
        "id": "smart-universe",
        "name": "Smart Universe",
        "description": (
            "Daily diversified selection from quality large/liquid names, ranked by "
            "relative strength, trend, liquidity, and volatility."
        ),
        "analysis_period": analysis_period,
        "limit": limit,
        "max_per_sector": max_per_sector,
        "base_count": len(base),
        "eligible_count": len(ranked),
        "count": len(selected),
        "tickers": [item.ticker for item in selected],
        "companies": [candidate_to_company(item) for item in selected],
        "ranked": [candidate_to_company(item) for item in ranked[:100]],
        "sector_counts": sector_counts(selected),
        "errors": errors,
        "generated_at": int(time.time()),
    }
    _SMART_CACHE[cache_key] = (now, payload)
    return payload


def base_universe() -> dict[str, str]:
    sectors: dict[str, str] = {}
    for watchlist in WATCHLISTS:
        sector = SECTOR_WATCHLIST_IDS.get(watchlist.id)
        if not sector:
            continue
        for ticker in watchlist.tickers:
            sectors.setdefault(ticker, sector)

    quality_core = next((item for item in WATCHLISTS if item.id == "quality-core"), None)
    if quality_core:
        for ticker in quality_core.tickers:
            sectors.setdefault(ticker, "Quality Core")

    for ticker, sector in SECTOR_OVERRIDES.items():
        if ticker in COMPANY_NAMES:
            sectors[ticker] = sector
    return dict(sorted(sectors.items()))


def fetch_benchmark_frames(analysis_period: str) -> dict[str, Any]:
    frames = {}
    for symbol in ("SPY", "QQQ"):
        try:
            frames[symbol] = fetch_daily_frame(symbol, period=analysis_period)
        except Exception:
            continue
    return frames


def score_ticker(
    ticker: str,
    sector: str,
    analysis_period: str,
    benchmarks: dict[str, Any],
) -> SmartCandidate | None:
    daily = fetch_daily_frame(ticker, period=analysis_period)
    if len(daily) < 65:
        return None

    close = daily["Close"]
    volume = daily["Volume"]
    price = float(close.iloc[-1])
    if price < MIN_PRICE:
        return None

    avg_dollar_volume = float((close.iloc[-20:] * volume.iloc[-20:]).mean())
    if avg_dollar_volume < MIN_DOLLAR_VOLUME:
        return None

    atr = compute_atr(daily)
    atr_pct = atr / price * 100 if price else 0
    if atr_pct < MIN_ATR_PCT or atr_pct > MAX_ATR_PCT:
        return None

    ret_1m = pct_return(close, 21)
    ret_3m = pct_return(close, 63)
    ret_6m = pct_return(close, min(126, len(close) - 1))
    spy_rs = ret_3m - benchmark_return(benchmarks.get("SPY"), 63)
    qqq_rs = ret_3m - benchmark_return(benchmarks.get("QQQ"), 63)
    relative_strength = (spy_rs * 0.65) + (qqq_rs * 0.35)

    ema_20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    ema_50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
    ema_50_prev = float(close.ewm(span=50, adjust=False).mean().iloc[-10])
    ema_200 = float(close.ewm(span=min(200, len(close)), adjust=False).mean().iloc[-1])

    trend_score = 0.0
    trend_score += 25 if price > ema_20 else -8
    trend_score += 25 if price > ema_50 else -12
    trend_score += 20 if price > ema_200 else -10
    trend_score += 20 if ema_20 > ema_50 else -8
    trend_score += 10 if ema_50 > ema_50_prev else -6

    volume_score = clamp(math.log10(max(avg_dollar_volume, 1) / MIN_DOLLAR_VOLUME) * 35 + 35, 0, 100)
    volatility_score = volatility_quality_score(atr_pct)
    rs_score = clamp(relative_strength * 220 + 50, 0, 100)
    momentum_score = clamp((ret_1m * 160) + (ret_3m * 90) + (ret_6m * 35) + 50, 0, 100)

    score = (
        rs_score * 0.32
        + clamp(trend_score, 0, 100) * 0.30
        + momentum_score * 0.18
        + volume_score * 0.12
        + volatility_score * 0.08
    )
    reason = build_reason(relative_strength, trend_score, avg_dollar_volume, atr_pct)
    return SmartCandidate(
        ticker=ticker,
        name=COMPANY_NAMES.get(ticker, ticker),
        sector=sector,
        price=price,
        avg_dollar_volume=avg_dollar_volume,
        atr_pct=atr_pct,
        return_1m=ret_1m,
        return_3m=ret_3m,
        return_6m=ret_6m,
        relative_strength=relative_strength,
        trend_score=clamp(trend_score, 0, 100),
        volume_score=volume_score,
        volatility_score=volatility_score,
        score=round(score, 2),
        reason=reason,
    )


def diversify(
    ranked: list[SmartCandidate],
    *,
    limit: int,
    max_per_sector: int,
) -> list[SmartCandidate]:
    selected: list[SmartCandidate] = []
    counts: dict[str, int] = {}
    for candidate in ranked:
        if len(selected) >= limit:
            break
        if counts.get(candidate.sector, 0) >= max_per_sector:
            continue
        selected.append(candidate)
        counts[candidate.sector] = counts.get(candidate.sector, 0) + 1
    if len(selected) >= limit:
        return selected

    selected_tickers = {item.ticker for item in selected}
    for candidate in ranked:
        if len(selected) >= limit:
            break
        if candidate.ticker in selected_tickers:
            continue
        selected.append(candidate)
        selected_tickers.add(candidate.ticker)
    return selected


def candidate_to_company(candidate: SmartCandidate) -> dict[str, Any]:
    return {
        "ticker": candidate.ticker,
        "name": candidate.name,
        "sector": candidate.sector,
        "score": candidate.score,
        "price": round(candidate.price, 2),
        "avg_dollar_volume": round(candidate.avg_dollar_volume, 2),
        "atr_pct": round(candidate.atr_pct, 2),
        "return_1m": round(candidate.return_1m * 100, 2),
        "return_3m": round(candidate.return_3m * 100, 2),
        "return_6m": round(candidate.return_6m * 100, 2),
        "relative_strength": round(candidate.relative_strength * 100, 2),
        "trend_score": round(candidate.trend_score, 2),
        "reason": candidate.reason,
    }


def sector_counts(candidates: list[SmartCandidate]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate.sector] = counts.get(candidate.sector, 0) + 1
    return dict(sorted(counts.items()))


def pct_return(close: Any, lookback: int) -> float:
    if len(close) <= lookback:
        lookback = len(close) - 1
    if lookback <= 0:
        return 0.0
    start = float(close.iloc[-lookback])
    end = float(close.iloc[-1])
    if start <= 0:
        return 0.0
    return end / start - 1


def benchmark_return(frame: Any, lookback: int) -> float:
    if frame is None or len(frame) < 2:
        return 0.0
    return pct_return(frame["Close"], min(lookback, len(frame) - 1))


def volatility_quality_score(atr_pct: float) -> float:
    if atr_pct <= 0:
        return 0
    # Best zone for this swing-trading workflow is tradable movement without extreme noise.
    if 2.0 <= atr_pct <= 4.8:
        return 100
    if atr_pct < 2.0:
        return clamp(45 + (atr_pct / 2.0) * 55, 0, 100)
    return clamp(100 - ((atr_pct - 4.8) / max(0.1, MAX_ATR_PCT - 4.8)) * 55, 20, 100)


def build_reason(relative_strength: float, trend_score: float, avg_dollar_volume: float, atr_pct: float) -> str:
    parts = []
    if relative_strength > 0:
        parts.append("outperforming benchmarks")
    else:
        parts.append("acceptable relative strength")
    if trend_score >= 70:
        parts.append("clean trend alignment")
    elif trend_score >= 45:
        parts.append("mixed but tradable trend")
    else:
        parts.append("trend quality passed minimum filter")
    parts.append(f"${avg_dollar_volume / 1_000_000:.0f}M avg dollar volume")
    parts.append(f"{atr_pct:.1f}% ATR")
    return ", ".join(parts)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))
