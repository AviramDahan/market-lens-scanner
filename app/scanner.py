import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from app.data import (
    fetch_daily_frame,
    fetch_extended_hours_quote,
    fetch_next_earnings_date,
    fetch_spy_returns,
    fetch_ticker,
)
from app.fibonacci import detect_swing_low_confluence, get_best_fib
from app.indicators import (
    classify_volume_price_scenario,
    compute_atr,
    compute_ema,
    compute_ma200_weekly,
    compute_market_structure,
    compute_relative_strength,
    compute_volume_profile,
    compute_vwap,
    filter_hvn,
)
from app.models import ExtendedHoursInfo, ScanResult, VolumeProfile
from app.professional import enrich_professional_context
from app.setups import detect_setup

logger = logging.getLogger(__name__)
DEFAULT_SCAN_WORKERS = int(os.getenv("MARKET_LENS_SCAN_WORKERS", "10"))


@dataclass
class ScanDetail:
    result: ScanResult
    daily: "pd.DataFrame"
    hourly: "pd.DataFrame"
    analysis_period: str
    atr: float
    vwap: float
    ema_20: float
    volume_profile: VolumeProfile
    vp_from_date: str
    vp_to_date: str
    ma200_weekly: float
    market_structure: str
    vp_scenario: str
    relative_strength: float
    benchmarks: dict[str, "pd.DataFrame"]


def scan_ticker(
    ticker: str,
    min_rr: float = 2.0,
    analysis_period: str = "6mo",
) -> ScanResult:
    return scan_ticker_detail(ticker, min_rr, analysis_period=analysis_period).result


def scan_ticker_detail(
    ticker: str,
    min_rr: float = 2.0,
    analysis_period: str = "6mo",
    spy_returns: "pd.Series | None" = None,
    benchmarks: dict[str, "pd.DataFrame"] | None = None,
) -> ScanDetail:
    import pandas as pd

    data = fetch_ticker(ticker, analysis_period=analysis_period)

    current_price = float(data.daily["Close"].iloc[-1])
    atr = compute_atr(data.daily)
    vwap = compute_vwap(data.hourly)
    ema_20 = compute_ema(data.daily)
    ma200_weekly = compute_ma200_weekly(data.weekly)
    price_above_ma200 = current_price > ma200_weekly
    market_structure = compute_market_structure(data.daily)
    vp_scenario = classify_volume_price_scenario(data.daily)
    vp = filter_hvn(compute_volume_profile(data.hourly), atr)
    fib_info = get_best_fib(data.daily, atr, current_price)

    if spy_returns is None:
        try:
            spy_returns = fetch_spy_returns(period=analysis_period)
        except Exception:
            spy_returns = pd.Series(dtype=float)
    relative_strength = compute_relative_strength(data.daily, spy_returns)
    if benchmarks is None:
        benchmarks = fetch_benchmarks(analysis_period)

    hourly_closes = data.hourly["Close"].iloc[-5:]
    hourly_lows = data.hourly["Low"].iloc[-5:]
    hourly_volume = data.hourly["Volume"].iloc[-5:]

    vsl = detect_swing_low_confluence(
        daily=data.daily,
        atr=atr,
        current_price=current_price,
        vp=vp,
        hourly_lows=hourly_lows,
        hourly_closes=hourly_closes,
    )

    result = detect_setup(
        ticker=ticker,
        current_price=current_price,
        atr=atr,
        vwap=vwap,
        volume_profile=vp,
        fib_info=fib_info,
        hourly_closes=hourly_closes,
        hourly_lows=hourly_lows,
        hourly_volume=hourly_volume,
        ema_20=ema_20,
        vsl=vsl,
        min_rr=min_rr,
        price_above_ma200=price_above_ma200,
        market_structure=market_structure,
        vp_scenario=vp_scenario,
        relative_strength=relative_strength,
        daily=data.daily,
    )
    earnings_date = fetch_next_earnings_date(ticker) if result.setup_type != "No Trade" else None
    result = enrich_professional_context(
        result,
        daily=data.daily,
        benchmarks=benchmarks,
        earnings_date=earnings_date,
    )
    result = attach_extended_hours(result, ticker)
    vp_from_date = data.hourly.index[0].strftime("%Y-%m-%d")
    vp_to_date = data.hourly.index[-1].strftime("%Y-%m-%d")
    return ScanDetail(
        result=result,
        daily=data.daily,
        hourly=data.hourly,
        analysis_period=analysis_period,
        atr=atr,
        vwap=vwap,
        ema_20=ema_20,
        volume_profile=vp,
        vp_from_date=vp_from_date,
        vp_to_date=vp_to_date,
        ma200_weekly=ma200_weekly,
        market_structure=market_structure,
        vp_scenario=vp_scenario,
        relative_strength=relative_strength,
        benchmarks=benchmarks,
    )


def attach_extended_hours(result: ScanResult, ticker: str) -> ScanResult:
    try:
        quote = fetch_extended_hours_quote(ticker)
        info = ExtendedHoursInfo(
            phase=quote.phase,
            label=quote.label,
            price=quote.price,
            timestamp=quote.timestamp,
            regular_close=quote.regular_close,
            change=quote.change,
            change_pct=quote.change_pct,
            is_extended=quote.is_extended,
            source=quote.source,
            note=quote.note,
        )
    except Exception as exc:
        logger.info("Extended-hours quote unavailable for %s: %s", ticker, exc)
        info = ExtendedHoursInfo(
            phase="UNAVAILABLE",
            label="Extended unavailable",
            note="Extended-hours quote unavailable from data provider.",
        )
    updated = result.model_copy(update={"extended_hours": info})
    return updated.model_copy(update={"extended_hours_impact": calculate_extended_hours_impact(updated)})


def calculate_extended_hours_impact(result: ScanResult) -> dict:
    quote = result.extended_hours
    if quote is None or quote.price is None or quote.price <= 0:
        return {
            "status": "UNAVAILABLE",
            "informational_only": True,
            "hint": "Extended-hours quote is unavailable.",
        }

    price = float(quote.price)
    setup_price = float(result.current_price or 0)
    impact = {
        "status": "INFO_ONLY",
        "phase": quote.phase,
        "label": quote.label,
        "quote_price": round(price, 4),
        "setup_price": round(setup_price, 4),
        "price_delta": round(price - setup_price, 4),
        "price_delta_pct": round(((price - setup_price) / setup_price * 100.0), 4) if setup_price else 0.0,
        "informational_only": True,
        "regular_confirmation_required": True,
        "hint": "Extended-hours quote is informational; re-scan during regular session for entry confirmation.",
    }

    if result.setup_type == "No Trade":
        impact["status"] = "NO_ACTIVE_SETUP"
        impact["hint"] = "No active setup was detected on completed regular-session candles."
        return impact

    buy_low, buy_high = result.buy_zone
    stop = float(result.stop_loss or 0)
    target_1 = float(result.target_1 or 0)
    target_2 = float(result.target_2 or 0)

    impact.update(
        {
            "buy_zone_low": round(float(buy_low), 4),
            "buy_zone_high": round(float(buy_high), 4),
            "stop_loss": round(stop, 4),
            "target_1": round(target_1, 4),
            "target_2": round(target_2, 4),
            "inside_buy_zone": bool(buy_low <= price <= buy_high),
            "below_buy_zone": bool(price < buy_low),
            "above_buy_zone": bool(price > buy_high),
            "stop_touched_by_extended": bool(stop > 0 and price <= stop),
            "target_1_touched_by_extended": bool(target_1 > 0 and price >= target_1),
            "target_2_touched_by_extended": bool(target_2 > 0 and price >= target_2),
        }
    )

    if stop > 0 and price > stop:
        risk = price - stop
        rr1 = (target_1 - price) / risk if target_1 > price else 0.0
        rr2 = (target_2 - price) / risk if target_2 > price else 0.0
        weighted_rr = (0.80 * rr1) + (0.20 * rr2)
        impact.update(
            {
                "extended_rr_1": round(rr1, 4),
                "extended_rr_2": round(rr2, 4),
                "extended_weighted_rr": round(weighted_rr, 4),
            }
        )

    if impact["stop_touched_by_extended"]:
        impact["status"] = "SETUP_INVALIDATED_BY_EXTENDED"
        impact["hint"] = "Extended-hours price touched or crossed the stop area; re-scan before trusting this setup."
    elif impact["target_2_touched_by_extended"]:
        impact["status"] = "TARGET_2_TOUCHED_BY_EXTENDED"
        impact["hint"] = "Extended-hours price already touched Target 2; the original entry plan may be stale."
    elif impact["target_1_touched_by_extended"]:
        impact["status"] = "TARGET_1_TOUCHED_BY_EXTENDED"
        impact["hint"] = "Extended-hours price already touched Target 1; wait for regular-session structure."
    elif impact["inside_buy_zone"]:
        impact["status"] = "INSIDE_BUY_ZONE"
        impact["hint"] = "Extended-hours price is inside the buy zone; wait for regular-session confirmation before entry."
    elif impact["below_buy_zone"]:
        impact["status"] = "BELOW_BUY_ZONE"
        impact["distance_to_buy_zone_pct"] = round((buy_low - price) / price * 100.0, 4) if price else 0.0
        impact["hint"] = "Extended-hours price is below the buy zone; the setup may need a reclaim during regular session."
    else:
        impact["status"] = "ABOVE_BUY_ZONE"
        impact["distance_to_buy_zone_pct"] = round((price - buy_high) / price * 100.0, 4) if price else 0.0
        impact["hint"] = "Extended-hours price is above the buy zone; avoid chasing until regular-session confirmation."
    return impact


def fetch_benchmarks(analysis_period: str = "6mo") -> dict[str, "pd.DataFrame"]:
    benchmarks = {}
    for symbol in ("SPY", "QQQ", "IWM"):
        try:
            benchmarks[symbol] = fetch_daily_frame(symbol, period=analysis_period)
        except Exception:
            continue
    return benchmarks


def scan_tickers(
    tickers: list[str],
    min_rr: float = 2.0,
    analysis_period: str = "6mo",
    verbose: bool = False,
) -> tuple[list[ScanResult], dict[str, str], list[ScanDetail]]:
    import pandas as pd

    results: list[ScanResult] = []
    details: list[ScanDetail] = []
    errors: dict[str, str] = {}

    try:
        spy_returns: pd.Series = fetch_spy_returns(period=analysis_period)
    except Exception:
        spy_returns = pd.Series(dtype=float)
    benchmarks = fetch_benchmarks(analysis_period)

    max_workers = max(1, min(DEFAULT_SCAN_WORKERS, len(tickers) or 1))
    detail_by_ticker: dict[str, ScanDetail] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_by_ticker = {
            executor.submit(
                scan_ticker_detail,
                ticker,
                min_rr,
                analysis_period=analysis_period,
                spy_returns=spy_returns,
                benchmarks=benchmarks,
            ): ticker
            for ticker in tickers
        }
        for future in as_completed(future_by_ticker):
            ticker = future_by_ticker[future]
            try:
                detail = future.result()
                detail_by_ticker[ticker] = detail
                logger.info("Scanned %s -> %s", ticker, detail.result.setup_type)
            except Exception as e:
                logger.warning("Skipping %s: %s", ticker, e)
                errors[ticker] = str(e)

    for ticker in tickers:
        detail = detail_by_ticker.get(ticker)
        if detail is None:
            continue
        results.append(detail.result)
        details.append(detail)

    return results, errors, details
