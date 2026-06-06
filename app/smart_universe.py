from __future__ import annotations

import math
import os
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

import httpx

from app.data import fetch_daily_frame
from app.indicators import compute_atr
from app.watchlists import COMPANY_NAMES, WATCHLISTS


DEFAULT_LIMIT = int(os.getenv("MARKET_LENS_SMART_LIMIT", "35"))
DEFAULT_MAX_PER_SECTOR = int(os.getenv("MARKET_LENS_SMART_MAX_PER_SECTOR", "5"))
DEFAULT_WORKERS = int(os.getenv("MARKET_LENS_SMART_WORKERS", "8"))
DEFAULT_SOURCE = os.getenv("MARKET_LENS_SMART_SOURCE", "sp500")
SOURCE_URL = os.getenv(
    "MARKET_LENS_SMART_SOURCE_URL",
    "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
)
SOURCE_CACHE_TTL_SECONDS = int(os.getenv("MARKET_LENS_SOURCE_CACHE_TTL", "86400"))
MAX_SOURCE_PER_SECTOR = int(os.getenv("MARKET_LENS_SMART_SOURCE_PER_SECTOR", "20"))
MIN_PRICE = float(os.getenv("MARKET_LENS_SMART_MIN_PRICE", "10"))
MIN_DOLLAR_VOLUME = float(os.getenv("MARKET_LENS_SMART_MIN_DOLLAR_VOLUME", "100000000"))
MIN_ATR_PCT = float(os.getenv("MARKET_LENS_SMART_MIN_ATR_PCT", "1.2"))
MAX_ATR_PCT = float(os.getenv("MARKET_LENS_SMART_MAX_ATR_PCT", "8.0"))
MIN_SECTOR_HEALTH_SCORE = float(os.getenv("MARKET_LENS_MIN_SECTOR_HEALTH", "42"))
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

SECTOR_ETFS = {
    "Communication Services": "XLC",
    "Consumer": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Financials": "XLF",
    "Healthcare": "XLV",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Semiconductors": "SMH",
    "Technology": "XLK",
    "Utilities": "XLU",
    "Utilities / Real Assets": "XLU",
    "Quality Core": "SPY",
}

_SMART_CACHE: dict[tuple[str, int, int], tuple[float, dict[str, Any]]] = {}
_SOURCE_CACHE: tuple[float, dict[str, dict[str, str]], dict[str, str], str] | None = None
_SECTOR_HEALTH_CACHE: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}
_SOURCE_COMPANY_NAMES: dict[str, str] = {}
_LAST_SOURCE_NAME = "curated fallback"


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
    sector_health_score: float
    sector_health_label: str
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

    source_universe = base_universe()
    source_name = _LAST_SOURCE_NAME
    sector_health = build_sector_health(analysis_period)
    base = candidate_pool(source_universe, sector_health)
    benchmarks = fetch_benchmark_frames(analysis_period)
    candidates: list[SmartCandidate] = []
    errors: dict[str, str] = {}
    max_workers = max(1, min(DEFAULT_WORKERS, len(base)))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                score_ticker,
                ticker,
                sector,
                analysis_period,
                benchmarks,
                sector_health,
            ): ticker
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
            "stock quality plus sector regime strength."
        ),
        "analysis_period": analysis_period,
        "limit": limit,
        "max_per_sector": max_per_sector,
        "source": "sp500" if source_name == "S&P 500 constituents" else "curated",
        "source_name": source_name,
        "source_url": SOURCE_URL if DEFAULT_SOURCE == "sp500" else "",
        "base_count": len(source_universe),
        "scored_base_count": len(base),
        "eligible_count": len(ranked),
        "count": len(selected),
        "tickers": [item.ticker for item in selected],
        "companies": [candidate_to_company(item) for item in selected],
        "ranked": [candidate_to_company(item) for item in ranked[:100]],
        "sector_counts": sector_counts(selected),
        "sector_health": sector_health,
        "errors": errors,
        "generated_at": int(time.time()),
    }
    _SMART_CACHE[cache_key] = (now, payload)
    return payload


def base_universe() -> dict[str, str]:
    source = external_source_universe() if DEFAULT_SOURCE == "sp500" else {}
    if source:
        return {ticker: item["sector"] for ticker, item in source.items()}

    return curated_universe()


def curated_universe() -> dict[str, str]:
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


def external_source_universe() -> dict[str, dict[str, str]]:
    global _LAST_SOURCE_NAME, _SOURCE_CACHE
    now = time.monotonic()
    if _SOURCE_CACHE and now - _SOURCE_CACHE[0] < SOURCE_CACHE_TTL_SECONDS:
        _LAST_SOURCE_NAME = _SOURCE_CACHE[3]
        return _SOURCE_CACHE[1]

    try:
        universe = fetch_sp500_universe()
        source_name = "S&P 500 constituents"
    except Exception:
        universe = {}
        source_name = "curated fallback"

    if universe:
        _SOURCE_COMPANY_NAMES.update({ticker: item["name"] for ticker, item in universe.items()})
        _SOURCE_CACHE = (now, universe, dict(_SOURCE_COMPANY_NAMES), source_name)
        _LAST_SOURCE_NAME = source_name
    else:
        _LAST_SOURCE_NAME = source_name
    return universe


def fetch_sp500_universe() -> dict[str, dict[str, str]]:
    response = httpx.get(
        SOURCE_URL,
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "MarketLensScanner/1.0 (market universe research)"},
    )
    response.raise_for_status()
    rows = SP500TableParser.parse(response.text)
    universe: dict[str, dict[str, str]] = {}
    for row in rows:
        raw_symbol = row.get("Symbol", "")
        raw_name = row.get("Security", "")
        raw_sector = row.get("GICS Sector", "")
        ticker = normalize_ticker(raw_symbol)
        sector = normalize_sector(raw_sector)
        if not ticker or not raw_name or not sector:
            continue
        universe[ticker] = {"name": raw_name, "sector": sector}
    return dict(sorted(universe.items()))


class SP500TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_table = False
        self.in_cell = False
        self.current_cell: list[str] = []
        self.current_row: list[str] = []
        self.rows: list[list[str]] = []
        self.table_depth = 0

    @classmethod
    def parse(cls, html: str) -> list[dict[str, str]]:
        parser = cls()
        parser.feed(html)
        if not parser.rows:
            return []
        headers = [clean_cell(value) for value in parser.rows[0]]
        parsed = []
        for row in parser.rows[1:]:
            values = [clean_cell(value) for value in row]
            if len(values) < len(headers):
                continue
            parsed.append(dict(zip(headers, values, strict=False)))
        return parsed

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "table" and "wikitable" in str(attrs_dict.get("class", "")) and not self.in_table:
            self.in_table = True
            self.table_depth = 1
            return
        if self.in_table and tag == "table":
            self.table_depth += 1
        if self.in_table and tag == "tr":
            self.current_row = []
        if self.in_table and tag in {"th", "td"}:
            self.in_cell = True
            self.current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if not self.in_table:
            return
        if tag in {"th", "td"} and self.in_cell:
            self.current_row.append("".join(self.current_cell))
            self.current_cell = []
            self.in_cell = False
        elif tag == "tr" and self.current_row:
            self.rows.append(self.current_row)
            self.current_row = []
        elif tag == "table":
            self.table_depth -= 1
            if self.table_depth <= 0:
                self.in_table = False

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.current_cell.append(data)


def clean_cell(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def normalize_ticker(value: str) -> str:
    return clean_cell(value).upper().replace(".", "-")


def normalize_sector(value: str) -> str:
    sector = clean_cell(value)
    mapping = {
        "Communication Services": "Communication Services",
        "Consumer Discretionary": "Consumer",
        "Consumer Staples": "Consumer Defensive",
        "Energy": "Energy",
        "Financials": "Financials",
        "Health Care": "Healthcare",
        "Industrials": "Industrials",
        "Information Technology": "Technology",
        "Materials": "Materials",
        "Real Estate": "Real Estate",
        "Utilities": "Utilities",
    }
    return mapping.get(sector, sector)


def build_sector_health(analysis_period: str) -> dict[str, dict[str, Any]]:
    cached = _SECTOR_HEALTH_CACHE.get(analysis_period)
    now = time.monotonic()
    if cached and now - cached[0] < SMART_CACHE_TTL_SECONDS:
        return cached[1]

    health: dict[str, dict[str, Any]] = {}
    spy = safe_daily_frame("SPY", analysis_period)
    for sector, etf in SECTOR_ETFS.items():
        frame = safe_daily_frame(etf, analysis_period)
        if frame is None:
            health[sector] = fallback_sector_health(sector, etf)
            continue
        health[sector] = sector_health_from_frame(sector, etf, frame, spy)

    _SECTOR_HEALTH_CACHE[analysis_period] = (now, health)
    return health


def safe_daily_frame(ticker: str, analysis_period: str) -> Any | None:
    try:
        return fetch_daily_frame(ticker, period=analysis_period)
    except Exception:
        return None


def fallback_sector_health(sector: str, etf: str) -> dict[str, Any]:
    return {
        "sector": sector,
        "etf": etf,
        "label": "Unknown",
        "score": 55.0,
        "return_1m": 0.0,
        "return_3m": 0.0,
        "relative_strength": 0.0,
        "trend_score": 50.0,
        "reason": "sector ETF data unavailable; using neutral fallback",
    }


def sector_health_from_frame(
    sector: str,
    etf: str,
    frame: Any,
    benchmark: Any | None,
) -> dict[str, Any]:
    close = frame["Close"]
    price = float(close.iloc[-1])
    ret_1m = pct_return(close, min(21, len(close) - 1))
    ret_3m = pct_return(close, min(63, len(close) - 1))
    benchmark_ret = benchmark_return(benchmark, min(63, len(close) - 1)) if benchmark is not None else 0
    relative_strength = ret_3m - benchmark_ret

    ema_20 = float(close.ewm(span=min(20, len(close)), adjust=False).mean().iloc[-1])
    ema_50 = float(close.ewm(span=min(50, len(close)), adjust=False).mean().iloc[-1])
    ema_50_prev = float(close.ewm(span=min(50, len(close)), adjust=False).mean().iloc[max(0, len(close) - 10)])

    trend_score = 0.0
    trend_score += 35 if price > ema_20 else -12
    trend_score += 35 if price > ema_50 else -18
    trend_score += 20 if ema_20 > ema_50 else -8
    trend_score += 10 if ema_50 > ema_50_prev else -8
    trend_score = clamp(trend_score, 0, 100)

    rs_score = clamp(relative_strength * 260 + 50, 0, 100)
    momentum_score = clamp((ret_1m * 170) + (ret_3m * 90) + 50, 0, 100)
    score = round(trend_score * 0.45 + rs_score * 0.35 + momentum_score * 0.20, 2)
    label = "Strong" if score >= 68 else "Neutral" if score >= MIN_SECTOR_HEALTH_SCORE else "Weak"
    return {
        "sector": sector,
        "etf": etf,
        "label": label,
        "score": score,
        "return_1m": round(ret_1m * 100, 2),
        "return_3m": round(ret_3m * 100, 2),
        "relative_strength": round(relative_strength * 100, 2),
        "trend_score": round(trend_score, 2),
        "reason": (
            f"{etf} sector regime is {label.lower()}: "
            f"{ret_3m * 100:.1f}% 3m return, {relative_strength * 100:.1f}% vs SPY"
        ),
    }


def candidate_pool(
    source_universe: dict[str, str],
    sector_health: dict[str, dict[str, Any]],
) -> dict[str, str]:
    grouped: dict[str, list[str]] = {}
    for ticker, sector in source_universe.items():
        grouped.setdefault(sector, []).append(ticker)

    selected: dict[str, str] = {}
    today_key = time.strftime("%Y%m%d")
    curated = curated_universe()
    for sector, tickers in sorted(grouped.items()):
        health = sector_health.get(sector, fallback_sector_health(sector, SECTOR_ETFS.get(sector, "SPY")))
        if health["label"] == "Weak":
            continue
        quota = MAX_SOURCE_PER_SECTOR if health["label"] == "Strong" else max(8, MAX_SOURCE_PER_SECTOR // 2)
        ranked = sorted(tickers, key=lambda ticker: (ticker not in curated, rotation_key(ticker, today_key)))
        for ticker in ranked[:quota]:
            selected[ticker] = sector

    if len(selected) < DEFAULT_LIMIT:
        for ticker, sector in curated.items():
            health = sector_health.get(sector, fallback_sector_health(sector, SECTOR_ETFS.get(sector, "SPY")))
            if health["label"] != "Weak":
                selected.setdefault(ticker, sector)

    return dict(sorted(selected.items()))


def rotation_key(ticker: str, today_key: str) -> int:
    digest = hashlib.sha256(f"{today_key}:{ticker}".encode("utf-8")).hexdigest()
    return int(digest[:10], 16)


def company_name_for(ticker: str) -> str:
    normalized = normalize_ticker(ticker)
    return _SOURCE_COMPANY_NAMES.get(normalized) or COMPANY_NAMES.get(normalized, normalized)


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
    sector_health: dict[str, dict[str, Any]],
) -> SmartCandidate | None:
    health = sector_health.get(sector, fallback_sector_health(sector, SECTOR_ETFS.get(sector, "SPY")))
    if health["label"] == "Weak" or float(health["score"]) < MIN_SECTOR_HEALTH_SCORE:
        return None

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

    stock_score = (
        rs_score * 0.32
        + clamp(trend_score, 0, 100) * 0.30
        + momentum_score * 0.18
        + volume_score * 0.12
        + volatility_score * 0.08
    )
    score = stock_score * 0.78 + float(health["score"]) * 0.22
    reason = build_reason(relative_strength, trend_score, avg_dollar_volume, atr_pct, health)
    return SmartCandidate(
        ticker=ticker,
        name=company_name_for(ticker),
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
        sector_health_score=round(float(health["score"]), 2),
        sector_health_label=str(health["label"]),
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
    selected_tickers: set[str] = set()
    for candidate in ranked:
        if len(selected) >= limit:
            break
        if counts.get(candidate.sector, 0) >= max_per_sector:
            continue
        selected.append(candidate)
        selected_tickers.add(candidate.ticker)
        counts[candidate.sector] = counts.get(candidate.sector, 0) + 1
    if len(selected) >= limit:
        return selected

    remaining = [candidate for candidate in ranked if candidate.ticker not in selected_tickers]
    while len(selected) < limit and remaining:
        remaining.sort(key=lambda item: (counts.get(item.sector, 0), -item.score))
        candidate = remaining.pop(0)
        if len(selected) >= limit:
            break
        selected.append(candidate)
        selected_tickers.add(candidate.ticker)
        counts[candidate.sector] = counts.get(candidate.sector, 0) + 1
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
        "sector_health_score": round(candidate.sector_health_score, 2),
        "sector_health_label": candidate.sector_health_label,
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


def build_reason(
    relative_strength: float,
    trend_score: float,
    avg_dollar_volume: float,
    atr_pct: float,
    sector_health: dict[str, Any],
) -> str:
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
    parts.append(
        f"{sector_health['sector']} sector {str(sector_health['label']).lower()} "
        f"({float(sector_health['score']):.0f}/100)"
    )
    return ", ".join(parts)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))
