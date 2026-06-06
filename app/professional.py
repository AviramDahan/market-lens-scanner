from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pandas as pd

from app.models import (
    EventRiskInfo,
    LiquidityInfo,
    MarketRegimeInfo,
    ProfessionalAssessment,
    RelativeStrengthInfo,
    ScanResult,
    TradePlanInfo,
    TrendQualityInfo,
    VolumeConfirmationInfo,
)


def enrich_professional_context(
    result: ScanResult,
    *,
    daily: pd.DataFrame,
    benchmarks: dict[str, pd.DataFrame],
    earnings_date: str | None = None,
) -> ScanResult:
    market_regime = assess_market_regime(benchmarks)
    relative_strength = assess_relative_strength(daily, benchmarks)
    liquidity = assess_liquidity(daily)
    trend_quality = assess_trend_quality(daily)
    volume_confirmation = assess_volume_confirmation(daily)
    event_risk = assess_event_risk(daily, earnings_date)
    trade_plan = build_trade_plan(result, daily)
    professional_assessment = assess_quality(
        result=result,
        market_regime=market_regime,
        relative_strength=relative_strength,
        liquidity=liquidity,
        trend_quality=trend_quality,
        volume_confirmation=volume_confirmation,
        event_risk=event_risk,
    )

    updated = result.model_copy(
        update={
            "market_regime": market_regime,
            "relative_strength_info": relative_strength,
            "liquidity": liquidity,
            "trend_quality": trend_quality,
            "volume_confirmation": volume_confirmation,
            "event_risk": event_risk,
            "trade_plan": trade_plan,
            "professional_assessment": professional_assessment,
        }
    )
    if updated.setup_type != "No Trade":
        adjusted_score = (updated.score * 0.55) + (professional_assessment.quality_score * 0.45)
        updated = updated.model_copy(update={"score": round(max(0.0, min(1.0, adjusted_score)), 3)})
    return updated


def assess_market_regime(benchmarks: dict[str, pd.DataFrame]) -> MarketRegimeInfo:
    trends = {symbol: _benchmark_trend(frame) for symbol, frame in benchmarks.items()}
    bullish = sum(1 for trend in trends.values() if trend == "bullish")
    caution = sum(1 for trend in trends.values() if trend == "caution")
    score = (bullish + 0.5 * caution) / 3 if trends else 0.5

    if score >= 0.72:
        label = "Risk-on"
        note = "Broad market supports long setups."
    elif score >= 0.45:
        label = "Mixed"
        note = "Use cleaner entries and smaller size."
    else:
        label = "Risk-off"
        note = "Long setups need exceptional relative strength."

    return MarketRegimeInfo(
        label=label,
        score=round(score, 3),
        spy_trend=trends.get("SPY", "unknown"),
        qqq_trend=trends.get("QQQ", "unknown"),
        iwm_trend=trends.get("IWM", "unknown"),
        risk_note=note,
    )


def assess_relative_strength(daily: pd.DataFrame, benchmarks: dict[str, pd.DataFrame]) -> RelativeStrengthInfo:
    ratios = {
        symbol.lower(): _relative_return_ratio(daily, frame)
        for symbol, frame in benchmarks.items()
    }
    values = [value for value in ratios.values() if value > 0]
    average_ratio = sum(values) / len(values) if values else 1.0
    score = max(0.0, min(1.0, average_ratio / 1.8))

    if average_ratio >= 1.25:
        label = "Leadership"
    elif average_ratio >= 0.85:
        label = "In line"
    else:
        label = "Lagging"

    return RelativeStrengthInfo(
        score=round(score, 3),
        vs_spy=round(ratios.get("spy", 1.0), 3),
        vs_qqq=round(ratios.get("qqq", 1.0), 3),
        vs_iwm=round(ratios.get("iwm", 1.0), 3),
        label=label,
    )


def assess_liquidity(daily: pd.DataFrame) -> LiquidityInfo:
    recent = daily.tail(20)
    price = float(daily["Close"].iloc[-1])
    avg_volume = float(recent["Volume"].mean())
    avg_dollar_volume = float((recent["Close"] * recent["Volume"]).mean())
    price_ok = price >= 10
    volume_ok = avg_dollar_volume >= 20_000_000 and avg_volume >= 300_000
    score = (0.45 if price_ok else 0.0) + (0.55 if volume_ok else min(0.55, avg_dollar_volume / 20_000_000 * 0.55))
    label = "Institutional liquidity" if score >= 0.85 else "Tradable" if score >= 0.6 else "Thin"
    return LiquidityInfo(
        score=round(score, 3),
        avg_volume_20d=round(avg_volume, 0),
        avg_dollar_volume_20d=round(avg_dollar_volume, 0),
        price_ok=price_ok,
        volume_ok=volume_ok,
        label=label,
    )


def assess_trend_quality(daily: pd.DataFrame) -> TrendQualityInfo:
    close = daily["Close"]
    price = float(close.iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else ma50
    ma20_prev = float(close.rolling(20).mean().iloc[-11]) if len(close) >= 31 else ma20
    slope = (ma20 / ma20_prev - 1) if ma20_prev else 0.0

    above_ma20 = price > ma20
    above_ma50 = price > ma50
    above_ma200 = price > ma200
    ma20_above_ma50 = ma20 > ma50
    ma50_above_ma200 = ma50 > ma200

    score = sum([
        0.18 if above_ma20 else 0.0,
        0.2 if above_ma50 else 0.0,
        0.2 if above_ma200 else 0.0,
        0.18 if ma20_above_ma50 else 0.0,
        0.14 if ma50_above_ma200 else 0.0,
        0.1 if slope > 0 else 0.0,
    ])
    label = "Clean uptrend" if score >= 0.78 else "Constructive" if score >= 0.55 else "Messy"
    return TrendQualityInfo(
        score=round(score, 3),
        label=label,
        above_ma20=above_ma20,
        above_ma50=above_ma50,
        above_ma200=above_ma200,
        ma20_above_ma50=ma20_above_ma50,
        ma50_above_ma200=ma50_above_ma200,
        slope_20d=round(slope, 4),
    )


def assess_volume_confirmation(daily: pd.DataFrame) -> VolumeConfirmationInfo:
    recent = daily.tail(20).copy()
    if len(recent) < 10:
        return VolumeConfirmationInfo(
            score=0.5,
            label="Limited volume history",
            recent_volume_ratio=1.0,
            pullback_volume_contracting=False,
            accumulation_days_20d=0,
            distribution_days_20d=0,
        )

    avg20 = float(recent["Volume"].mean())
    recent_ratio = float(recent["Volume"].iloc[-1] / avg20) if avg20 else 1.0
    close = recent["Close"]
    returns = close.pct_change()
    volume = recent["Volume"]
    accumulation = int(((returns > 0.005) & (volume > avg20)).sum())
    distribution = int(((returns < -0.005) & (volume > avg20)).sum())
    pullback = close.iloc[-1] < close.iloc[-5]
    pullback_volume_contracting = bool(pullback and volume.tail(5).mean() < volume.head(15).mean())

    score = 0.45
    score += 0.2 if recent_ratio >= 1.1 else 0.0
    score += 0.2 if accumulation >= distribution else -0.15
    score += 0.15 if pullback_volume_contracting else 0.0
    score = max(0.0, min(1.0, score))
    label = "Confirmed" if score >= 0.72 else "Neutral" if score >= 0.48 else "Distribution risk"
    return VolumeConfirmationInfo(
        score=round(score, 3),
        label=label,
        recent_volume_ratio=round(recent_ratio, 2),
        pullback_volume_contracting=pullback_volume_contracting,
        accumulation_days_20d=accumulation,
        distribution_days_20d=distribution,
    )


def assess_event_risk(daily: pd.DataFrame, earnings_date: str | None) -> EventRiskInfo:
    gap_pct = _largest_recent_gap(daily)
    days_to_earnings = _days_until(earnings_date)
    near_earnings = days_to_earnings is not None and 0 <= days_to_earnings <= 10
    if near_earnings:
        label = "Earnings risk"
    elif abs(gap_pct) >= 8:
        label = "Recent gap risk"
    else:
        label = "No major event flag"
    return EventRiskInfo(
        label=label,
        earnings_date=earnings_date,
        days_to_earnings=days_to_earnings,
        has_near_earnings=near_earnings,
        recent_gap_pct=round(gap_pct, 2),
    )


def build_trade_plan(result: ScanResult, daily: pd.DataFrame) -> TradePlanInfo | None:
    if result.setup_type == "No Trade":
        return None
    prev_high = float(daily["High"].iloc[-2]) if len(daily) >= 2 else result.current_price
    trigger_price = max(result.current_price, prev_high)
    risk_per_share = max(0.0, trigger_price - result.stop_loss)
    shares = int(1000 / risk_per_share) if risk_per_share > 0 else 0
    if "Breakout" in result.setup_type:
        trigger = f"Close above {trigger_price:.2f} or reclaim of broken resistance."
    elif "VWAP" in result.setup_type:
        trigger = f"Hold VWAP and break above {trigger_price:.2f}."
    else:
        trigger = f"Break above prior-day high at {trigger_price:.2f} after holding buy zone."
    return TradePlanInfo(
        entry_trigger=trigger,
        trigger_price=round(trigger_price, 4),
        invalidation=f"Exit if price closes below {result.stop_loss:.2f} or setup support fails.",
        stop_loss=result.stop_loss,
        target_1=result.target_1,
        target_2=result.target_2,
        risk_per_share=round(risk_per_share, 4),
        shares_for_1000_risk=shares,
    )


def assess_quality(
    *,
    result: ScanResult,
    market_regime: MarketRegimeInfo,
    relative_strength: RelativeStrengthInfo,
    liquidity: LiquidityInfo,
    trend_quality: TrendQualityInfo,
    volume_confirmation: VolumeConfirmationInfo,
    event_risk: EventRiskInfo,
) -> ProfessionalAssessment:
    if result.setup_type == "No Trade":
        return ProfessionalAssessment(
            quality_score=0,
            grade="No Trade",
            decision="Skip",
            warnings=["No actionable setup detected."],
            strengths=[],
        )

    quality = (
        market_regime.score * 0.18
        + relative_strength.score * 0.22
        + liquidity.score * 0.16
        + trend_quality.score * 0.22
        + volume_confirmation.score * 0.14
        + min(1.0, result.risk_reward / 3.0) * 0.08
    )
    warnings = []
    strengths = []

    if market_regime.label == "Risk-off":
        warnings.append("Market regime is risk-off.")
    else:
        strengths.append(f"Market regime: {market_regime.label}.")
    if relative_strength.label == "Lagging":
        warnings.append("Ticker is lagging major indices.")
    else:
        strengths.append(f"Relative strength: {relative_strength.label}.")
    if liquidity.label == "Thin":
        warnings.append("Liquidity may be too thin for clean execution.")
    if trend_quality.label == "Messy":
        warnings.append("Trend quality is not clean.")
    if volume_confirmation.label == "Distribution risk":
        warnings.append("Recent volume shows distribution risk.")
    if event_risk.has_near_earnings:
        quality -= 0.18
        warnings.append("Earnings are close; reduce size or wait.")
    if abs(event_risk.recent_gap_pct) >= 8:
        quality -= 0.08
        warnings.append("Recent large gap increases volatility risk.")

    quality = round(max(0.0, min(1.0, quality)), 3)
    if quality >= 0.78:
        grade = "A"
        decision = "High-quality watchlist candidate"
    elif quality >= 0.65:
        grade = "B"
        decision = "Tradable if trigger confirms"
    elif quality >= 0.5:
        grade = "C"
        decision = "Watch only; needs confirmation"
    else:
        grade = "D"
        decision = "Low-quality setup"

    return ProfessionalAssessment(
        quality_score=quality,
        grade=grade,
        decision=decision,
        warnings=warnings,
        strengths=strengths,
    )


def _benchmark_trend(daily: pd.DataFrame) -> str:
    if daily.empty or len(daily) < 50:
        return "unknown"
    close = daily["Close"]
    price = float(close.iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else ma50
    if price > ma20 > ma50 and price > ma200:
        return "bullish"
    if price > ma50:
        return "caution"
    return "bearish"


def _relative_return_ratio(daily: pd.DataFrame, benchmark: pd.DataFrame, n_days: int = 20) -> float:
    if len(daily) < n_days or len(benchmark) < n_days:
        return 1.0
    ticker_ret = float(daily["Close"].iloc[-1] / daily["Close"].iloc[-n_days] - 1)
    benchmark_ret = float(benchmark["Close"].iloc[-1] / benchmark["Close"].iloc[-n_days] - 1)
    if benchmark_ret == 0:
        return 1.0 + ticker_ret
    if ticker_ret >= 0 and benchmark_ret < 0:
        return 1.8
    return max(0.0, min(2.5, ticker_ret / benchmark_ret))


def _largest_recent_gap(daily: pd.DataFrame, lookback: int = 20) -> float:
    recent = daily.tail(lookback)
    if len(recent) < 2:
        return 0.0
    prev_close = recent["Close"].shift(1)
    gaps = ((recent["Open"] - prev_close) / prev_close * 100).dropna()
    if gaps.empty:
        return 0.0
    idx = gaps.abs().idxmax()
    return float(gaps.loc[idx])


def _days_until(date_text: str | None) -> int | None:
    if not date_text:
        return None
    try:
        target = datetime.fromisoformat(date_text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    return (target.date() - datetime.now(UTC).date()).days

