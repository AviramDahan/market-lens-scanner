from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from app.data import fetch_daily_frame, fetch_intraday_frame, fetch_next_earnings_date
from app.indicators import compute_atr
from app.smart_universe import SECTOR_ETFS, build_sector_health, company_name_for


MARKET_REGIME_SYMBOLS = {
    "SPY": "SPY",
    "QQQ": "QQQ",
    "IWM": "IWM",
    "VIX": "^VIX",
    "US10Y": "^TNX",
    "DXY": "DX-Y.NYB",
}
NY_TZ = ZoneInfo("America/New_York")


@dataclass
class MarketRegime:
    label: str
    score: float
    max_total_exposure: float
    minimum_net_rr: float
    min_setup_score: float
    reason: str
    indicators: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class AgentRiskConfig:
    starting_capital: float
    default_max_total_exposure: float
    max_position: float
    analysis_period: str = "6mo"
    bull_max_exposure: float | None = None
    neutral_max_exposure: float | None = None
    bear_max_exposure: float = 0.0
    bull_min_net_rr: float = 2.0
    neutral_min_net_rr: float = 2.5
    bear_min_net_rr: float = 999.0
    neutral_strong_sector_min_net_rr: float = 2.2
    bull_min_setup_score: float = 0.45
    neutral_min_setup_score: float = 0.55
    sector_exposure_bull_pct: float = 0.40
    sector_exposure_neutral_pct: float = 0.30
    factor_exposure_bull_pct: float = 0.50
    factor_exposure_neutral_pct: float = 0.35
    correlation_block_threshold: float = 0.85
    primary_rr_weight: float = 0.80
    stretch_rr_weight: float = 0.20
    minimum_primary_net_rr: float = 0.80
    preferred_primary_net_rr: float = 1.00
    minimum_target1_atr_distance: float = 0.75
    require_entry_confirmation: bool = True
    entry_confirmation_intraday_enabled: bool = True
    entry_confirmation_intraday_interval: str = "30m"
    entry_confirmation_intraday_period: str = "5d"
    stop_cooldown_days: int = 3
    cooldown_exception_setup_score: float = 0.60
    cooldown_exception_net_rr1: float = 1.20
    earnings_blackout_before_days: int = 5
    earnings_blackout_after_days: int = 1
    block_unknown_earnings: bool = False
    fees_per_share: float = 0.0
    allow_off_hours_buys: bool = False

    def __post_init__(self) -> None:
        if self.bull_max_exposure is None:
            self.bull_max_exposure = min(self.default_max_total_exposure, self.starting_capital * 0.40)
        if self.neutral_max_exposure is None:
            self.neutral_max_exposure = min(self.default_max_total_exposure, self.starting_capital * 0.20)
        total_rr_weight = self.primary_rr_weight + self.stretch_rr_weight
        if total_rr_weight <= 0:
            self.primary_rr_weight = 0.80
            self.stretch_rr_weight = 0.20


@dataclass
class AgentRunRiskContext:
    config: AgentRiskConfig
    market_regime: MarketRegime
    sector_health: dict[str, dict[str, Any]]
    market_data_warnings: list[str] = field(default_factory=list)


@dataclass
class CandidateMarketSnapshot:
    ticker: str
    price: float = 0.0
    atr: float = 0.0
    atr_pct: float = 0.0
    avg_dollar_volume: float = 0.0
    return_1m: float = 0.0
    return_3m: float = 0.0
    market_cap: float = 0.0
    market_cap_bucket: str = "Unknown"
    bid: float = 0.0
    ask: float = 0.0
    normalized_momentum_score: float = 50.0
    normalized_atr_score: float = 50.0
    normalized_liquidity_score: float = 50.0
    normalized_quality_score: float = 50.0
    daily: pd.DataFrame | None = None
    intraday: pd.DataFrame | None = None
    warnings: list[str] = field(default_factory=list)


def build_agent_run_context(
    *,
    analysis_period: str,
    starting_capital: float,
    default_max_total_exposure: float,
    max_position: float,
) -> AgentRunRiskContext:
    config = AgentRiskConfig(
        starting_capital=starting_capital,
        default_max_total_exposure=default_max_total_exposure,
        max_position=max_position,
        analysis_period=analysis_period,
        bull_min_setup_score=float(os.getenv("MARKET_LENS_BULL_MIN_SETUP_SCORE", "0.45")),
        neutral_min_setup_score=float(os.getenv("MARKET_LENS_NEUTRAL_MIN_SETUP_SCORE", "0.55")),
        primary_rr_weight=float(os.getenv("MARKET_LENS_PRIMARY_RR_WEIGHT", "0.80")),
        stretch_rr_weight=float(os.getenv("MARKET_LENS_STRETCH_RR_WEIGHT", "0.20")),
        minimum_primary_net_rr=float(os.getenv("MARKET_LENS_MIN_PRIMARY_NET_RR", "0.80")),
        preferred_primary_net_rr=float(os.getenv("MARKET_LENS_PREFERRED_PRIMARY_NET_RR", "1.00")),
        minimum_target1_atr_distance=float(os.getenv("MARKET_LENS_MIN_TARGET1_ATR_DISTANCE", "0.75")),
        require_entry_confirmation=os.getenv("MARKET_LENS_REQUIRE_ENTRY_CONFIRMATION", "true").lower()
        in {"1", "true", "yes"},
        entry_confirmation_intraday_enabled=os.getenv(
            "MARKET_LENS_ENTRY_CONFIRMATION_INTRADAY_ENABLED", "true"
        ).lower()
        in {"1", "true", "yes"},
        entry_confirmation_intraday_interval=os.getenv("MARKET_LENS_ENTRY_CONFIRMATION_INTRADAY_INTERVAL", "30m"),
        entry_confirmation_intraday_period=os.getenv("MARKET_LENS_ENTRY_CONFIRMATION_INTRADAY_PERIOD", "5d"),
        stop_cooldown_days=int(os.getenv("MARKET_LENS_STOP_COOLDOWN_DAYS", "3")),
        cooldown_exception_setup_score=float(
            os.getenv("MARKET_LENS_COOLDOWN_EXCEPTION_SETUP_SCORE", "0.60")
        ),
        cooldown_exception_net_rr1=float(os.getenv("MARKET_LENS_COOLDOWN_EXCEPTION_NET_RR1", "1.20")),
        block_unknown_earnings=os.getenv("MARKET_LENS_BLOCK_UNKNOWN_EARNINGS", "false").lower()
        in {"1", "true", "yes"},
        fees_per_share=float(os.getenv("MARKET_LENS_AGENT_FEES_PER_SHARE", "0")),
        allow_off_hours_buys=os.getenv("MARKET_LENS_ALLOW_BUY_OUTSIDE_REGULAR_HOURS", "false").lower()
        in {"1", "true", "yes"},
    )
    market_regime = assess_agent_market_regime(config)
    try:
        sector_health = build_sector_health(analysis_period)
    except Exception:
        sector_health = {}
        market_regime.warnings.append("Sector health unavailable; using neutral fallback.")
    return AgentRunRiskContext(config=config, market_regime=market_regime, sector_health=sector_health)


def assess_agent_market_regime(config: AgentRiskConfig) -> MarketRegime:
    indicators: dict[str, Any] = {}
    warnings: list[str] = []

    for label, symbol in MARKET_REGIME_SYMBOLS.items():
        try:
            frame = fetch_daily_frame(symbol, period=config.analysis_period)
            indicators[label] = benchmark_state(frame, is_vix=label == "VIX")
        except Exception as exc:
            warnings.append(f"{label} data unavailable: {exc}")

    spy = indicators.get("SPY", {})
    qqq = indicators.get("QQQ", {})
    iwm = indicators.get("IWM", {})
    vix = indicators.get("VIX", {})
    us10y = indicators.get("US10Y", {})
    dxy = indicators.get("DXY", {})

    risk_points = 0.0
    risk_points += 2 if trend_is_bullish(spy) else -2 if trend_is_bearish(spy) else 0
    risk_points += 2 if trend_is_bullish(qqq) else -2 if trend_is_bearish(qqq) else 0
    risk_points += 1 if not trend_is_bearish(iwm) else -1
    risk_points += 1 if vix_is_calm(vix) else -2 if vix_is_stressed(vix) else 0
    risk_points += -0.5 if trend_is_bullish(us10y) else 0.25 if trend_is_bearish(us10y) else 0
    risk_points += -0.25 if trend_is_bullish(dxy) else 0.25 if trend_is_bearish(dxy) else 0

    if risk_points >= 4:
        label = "BULL"
        score = 0.82
        reason = "SPY/QQQ trend is constructive and volatility is acceptable."
        max_exposure = float(config.bull_max_exposure or config.default_max_total_exposure)
        min_rr = config.bull_min_net_rr
        min_setup_score = config.bull_min_setup_score
    elif risk_points <= -2:
        label = "BEAR"
        score = 0.22
        reason = "Major-index trend or volatility profile is risk-off."
        max_exposure = config.bear_max_exposure
        min_rr = config.bear_min_net_rr
        min_setup_score = 1.0
    else:
        label = "NEUTRAL"
        score = 0.52
        reason = "Market regime is mixed; use lower exposure and higher net R/R."
        max_exposure = float(config.neutral_max_exposure or config.default_max_total_exposure)
        min_rr = config.neutral_min_net_rr
        min_setup_score = config.neutral_min_setup_score

    return MarketRegime(
        label=label,
        score=score,
        max_total_exposure=max_exposure,
        minimum_net_rr=min_rr,
        min_setup_score=min_setup_score,
        reason=reason,
        indicators=indicators,
        warnings=warnings,
    )


def benchmark_state(frame: pd.DataFrame, *, is_vix: bool = False) -> dict[str, Any]:
    close = frame["Close"]
    price = float(close.iloc[-1])
    ema20 = float(close.ewm(span=min(20, len(close)), adjust=False).mean().iloc[-1])
    ema50 = float(close.ewm(span=min(50, len(close)), adjust=False).mean().iloc[-1])
    ema200 = float(close.ewm(span=min(200, len(close)), adjust=False).mean().iloc[-1])
    ret_1m = pct_return(close, 21)
    ret_3m = pct_return(close, 63)
    trend = "bullish" if price > ema20 > ema50 and price > ema200 else "bearish" if price < ema50 else "mixed"
    if is_vix:
        trend = "calm" if price < 20 and price <= ema20 else "stressed" if price >= 25 or price > ema20 > ema50 else "mixed"
    return {
        "price": round(price, 4),
        "ema20": round(ema20, 4),
        "ema50": round(ema50, 4),
        "ema200": round(ema200, 4),
        "return_1m": round(ret_1m * 100, 2),
        "return_3m": round(ret_3m * 100, 2),
        "trend": trend,
    }


def evaluate_agent_candidate(
    *,
    timestamp: str,
    result: Any,
    initial_action: str,
    initial_reason: str,
    quantity: int,
    cash_out: float,
    risk_amount: float,
    cash_available: float,
    portfolio_exposure_before: float,
    open_positions: dict[str, dict[str, Any]],
    sector_map: dict[str, str],
    run_context: AgentRunRiskContext,
    recent_stop_events: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    config = run_context.config
    ticker = str(result.ticker).upper()
    sector = sector_map.get(ticker, "Unknown")
    sector_info = sector_regime_for(sector, run_context.sector_health)
    snapshot = build_candidate_snapshot(ticker, result.current_price, config)
    factor_tags = factor_tags_for(ticker, sector, snapshot.market_cap_bucket)
    net_rr_info = calculate_net_rr(result, snapshot, config)
    earnings_info = calculate_earnings_blackout(ticker, config)
    target_info = validate_targets(result, snapshot, config)
    confirmation = calculate_entry_confirmation(result, snapshot, config)
    market_session = market_session_status(allow_off_hours_buys=config.allow_off_hours_buys)
    cooldown = cooldown_check(
        ticker=ticker,
        result=result,
        net_rr_info=net_rr_info,
        confirmation=confirmation,
        recent_stop_events=recent_stop_events or {},
        config=config,
    )
    normalized_quality_score = round(
        (
            snapshot.normalized_momentum_score * 0.45
            + snapshot.normalized_atr_score * 0.25
            + snapshot.normalized_liquidity_score * 0.30
        ),
        2,
    )
    sizing = adjust_candidate_position_size(
        ticker=ticker,
        sector=sector,
        factor_tags=factor_tags,
        initial_action=initial_action,
        quantity=quantity,
        cash_out=cash_out,
        risk_amount=risk_amount,
        cash_available=cash_available,
        portfolio_exposure_before=portfolio_exposure_before,
        open_positions=open_positions,
        sector_map=sector_map,
        run_context=run_context,
    )
    effective_quantity = sizing["quantity"]
    effective_cash_out = sizing["cash_out"]
    effective_risk_amount = sizing["risk_amount"]
    portfolio_exposure_after_if_buy = (
        portfolio_exposure_before + effective_cash_out
        if initial_action == "BUY_SIMULATED" and effective_quantity > 0
        else portfolio_exposure_before
    )
    sector_exposure = sector_exposure_check(
        ticker=ticker,
        sector=sector,
        initial_action=initial_action,
        trade_cash_out=effective_cash_out,
        open_positions=open_positions,
        sector_map=sector_map,
        run_context=run_context,
    )
    factor_exposure = factor_exposure_check(
        ticker=ticker,
        factor_tags=factor_tags,
        initial_action=initial_action,
        trade_cash_out=effective_cash_out,
        open_positions=open_positions,
        sector_map=sector_map,
        run_context=run_context,
    )
    correlation = correlation_check(
        ticker=ticker,
        candidate_daily=snapshot.daily,
        open_positions=open_positions,
        analysis_period=config.analysis_period,
    )

    warnings = []
    warnings.extend(run_context.market_regime.warnings)
    warnings.extend(snapshot.warnings)
    warnings.extend(earnings_info["warnings"])
    warnings.extend(target_info["warnings"])
    warnings.extend(confirmation["warnings"])
    warnings.extend(cooldown["warnings"])
    warnings.extend(net_rr_info["warnings"])
    warnings.extend(correlation["warnings"])
    warnings.extend(sector_exposure["warnings"])
    warnings.extend(factor_exposure["warnings"])
    if sizing["adjusted"]:
        warnings.append(sizing["reason"])
    if earnings_info["earnings_blackout"]:
        warnings.append("Earnings blackout active.")

    final_action = initial_action
    final_reason = initial_reason
    if initial_action == "BUY_SIMULATED":
        minimum_net_rr = minimum_net_rr_for(run_context.market_regime, sector_info, config)
        blockers = buy_blockers(
            result=result,
            run_context=run_context,
            sector_info=sector_info,
            net_rr_info=net_rr_info,
            earnings_info=earnings_info,
            target_info=target_info,
            confirmation=confirmation,
            cooldown=cooldown,
            sector_exposure=sector_exposure,
            factor_exposure=factor_exposure,
            correlation=correlation,
            normalized_quality_score=normalized_quality_score,
            portfolio_exposure_after=portfolio_exposure_after_if_buy,
            sizing=sizing,
            minimum_net_rr=minimum_net_rr,
            market_session=market_session,
        )
        if blockers:
            final_action = downgrade_action_from_blockers(blockers)
            final_reason = blockers[0]["reason"]
            warnings.extend(blocker["reason"] for blocker in blockers[1:])
        else:
            final_reason = (
                f"BUY_SIMULATED: {run_context.market_regime.label} market regime, "
                f"{sector_info['regime']} sector, valid setup, net R/R {net_rr_info['net_rr']:.2f}, "
                "no earnings blackout, correlation acceptable, and risk limits allow entry."
            )
            if sizing["adjusted"]:
                final_reason += f" Position size reduced to {effective_quantity} shares to fit exposure caps."
    elif initial_action == "WATCH":
        final_reason = enrich_non_buy_reason("WATCH", initial_reason, run_context, sector_info, net_rr_info)
    elif initial_action == "SKIP":
        final_reason = enrich_non_buy_reason("SKIP", initial_reason, run_context, sector_info, net_rr_info)
    elif initial_action == "HOLD":
        if earnings_info["earnings_blackout"]:
            final_reason = (
                "HOLD: Existing simulated position remains open, but earnings blackout is active; "
                "review the position before holding through earnings."
            )
        else:
            final_reason = f"HOLD: Existing simulated position remains open. {run_context.market_regime.label} regime recorded."

    portfolio_exposure_after = portfolio_exposure_after_if_buy if final_action == "BUY_SIMULATED" else portfolio_exposure_before
    decision = {
        "timestamp": timestamp,
        "ticker": ticker,
        "company_name": company_name_for(ticker),
        "price": round(float(result.current_price or 0), 4),
        "market_regime": run_context.market_regime.label,
        "market_regime_score": run_context.market_regime.score,
        "market_regime_reason": run_context.market_regime.reason,
        "minimum_net_rr_required": minimum_net_rr_for(run_context.market_regime, sector_info, config),
        "market_regime_indicators": run_context.market_regime.indicators,
        "market_session_phase": market_session["phase"],
        "market_session_timestamp": market_session["timestamp"],
        "market_session_can_open_new_buy": market_session["can_open_new_buy"],
        "market_session_reason": market_session["reason"],
        "sector": sector,
        "sector_etf": sector_info["etf"],
        "sector_regime": sector_info["regime"],
        "sector_score": sector_info["score"],
        "market_cap": snapshot.market_cap,
        "market_cap_bucket": snapshot.market_cap_bucket,
        "bid": snapshot.bid,
        "ask": snapshot.ask,
        "stock_score": round(float(result.score or 0), 4),
        "normalized_momentum_score": snapshot.normalized_momentum_score,
        "normalized_atr_score": snapshot.normalized_atr_score,
        "normalized_liquidity_score": snapshot.normalized_liquidity_score,
        "normalized_quality_score": normalized_quality_score,
        "setup_type": result.setup_type,
        "setup_score": round(float(result.score or 0), 4),
        "buy_zone_low": result.buy_zone_low,
        "buy_zone_high": result.buy_zone_high,
        "stop_loss": result.stop_loss,
        "target_1": result.target_1,
        "target_2": result.target_2,
        "target_1_atr_distance": target_info["target_1_atr_distance"],
        "target_2_atr_distance": target_info["target_2_atr_distance"],
        "target_feasibility_status": target_info["status"],
        "market_structure_status": target_info["market_structure_status"],
        "previous_resistance": target_info["previous_resistance"],
        "prior_high_20": target_info["prior_high_20"],
        "prior_high_63": target_info["prior_high_63"],
        "confirmation_status": confirmation["confirmation_status"],
        "confirmation_reason": confirmation["confirmation_reason"],
        "trigger_level": confirmation["trigger_level"],
        "close_above_trigger": confirmation["close_above_trigger"],
        "vwap_reclaimed": confirmation["vwap_reclaimed"],
        "retest_held": confirmation["retest_held"],
        "entry_confirmation_passed": confirmation["entry_confirmation_passed"],
        "confirmation_timeframe": confirmation["confirmation_timeframe"],
        "confirmation_candle_timestamp": confirmation["confirmation_candle_timestamp"],
        "gross_rr": net_rr_info["gross_rr"],
        "gross_rr_1": net_rr_info["gross_rr_1"],
        "gross_rr_2": net_rr_info["gross_rr_2"],
        "gross_rr_decision": net_rr_info["gross_rr_decision"],
        "theoretical_entry": net_rr_info["theoretical_entry"],
        "executable_entry": net_rr_info["executable_entry"],
        "gross_risk_per_share": net_rr_info["gross_risk_per_share"],
        "gross_reward_1_per_share": net_rr_info["gross_reward_1_per_share"],
        "gross_reward_2_per_share": net_rr_info["gross_reward_2_per_share"],
        "estimated_spread": net_rr_info["estimated_spread"],
        "estimated_slippage": net_rr_info["estimated_slippage"],
        "estimated_fees": net_rr_info["estimated_fees"],
        "spread_source": net_rr_info["spread_source"],
        "execution_liquidity_bucket": net_rr_info["execution_liquidity_bucket"],
        "net_entry": net_rr_info["net_entry"],
        "net_stop_assumption": net_rr_info["net_stop_assumption"],
        "net_target_1_assumption": net_rr_info["net_target_1_assumption"],
        "net_target_2_assumption": net_rr_info["net_target_2_assumption"],
        "net_risk_per_share": net_rr_info["net_risk_per_share"],
        "net_reward_1_per_share": net_rr_info["net_reward_1_per_share"],
        "net_reward_2_per_share": net_rr_info["net_reward_2_per_share"],
        "net_rr_1": net_rr_info["net_rr_1"],
        "net_rr_2": net_rr_info["net_rr_2"],
        "net_rr": net_rr_info["net_rr"],
        "earnings_date": earnings_info["earnings_date"],
        "days_to_earnings": earnings_info["days_to_earnings"],
        "earnings_blackout": earnings_info["earnings_blackout"],
        "sector_exposure_before": sector_exposure["before"],
        "sector_exposure_after": sector_exposure["after"],
        "sector_exposure_cap": sector_exposure["cap"],
        "sector_exposure_limit_exceeded": sector_exposure["limit_exceeded"],
        "factor_tags": factor_tags,
        "factor_exposure_before": factor_exposure["before"],
        "factor_exposure_after": factor_exposure["after"],
        "factor_exposure_cap": factor_exposure["cap"],
        "factor_exposure_limit_exceeded": factor_exposure["limit_exceeded"],
        "correlation_warning": correlation["correlation_warning"],
        "highest_correlation_ticker": correlation["highest_correlation_ticker"],
        "highest_correlation_value": correlation["highest_correlation_value"],
        "cooldown_active": cooldown["cooldown_active"],
        "last_stop_date": cooldown["last_stop_date"],
        "cooldown_days_remaining": cooldown["cooldown_days_remaining"],
        "cooldown_exception_used": cooldown["cooldown_exception_used"],
        "cooldown_reason": cooldown["cooldown_reason"],
        "position_size": effective_quantity if final_action == "BUY_SIMULATED" else 0,
        "risk_amount": round(effective_risk_amount if final_action == "BUY_SIMULATED" else 0.0, 2),
        "cash_available": round(cash_available, 2),
        "portfolio_exposure_before": round(portfolio_exposure_before, 2),
        "portfolio_exposure_after": round(portfolio_exposure_after, 2),
        "original_position_size": quantity,
        "original_cash_out": round(cash_out, 2),
        "original_risk_amount": round(risk_amount, 2),
        "position_size_adjusted": sizing["adjusted"],
        "adjusted_position_size": effective_quantity,
        "adjusted_cash_out": round(effective_cash_out, 2),
        "adjusted_risk_amount": round(effective_risk_amount, 2),
        "sizing_adjustment_reason": sizing["reason"],
        "position_sizing_explanation": position_sizing_explanation(
            initial_action=initial_action,
            final_action=final_action,
            quantity=effective_quantity,
            cash_out=effective_cash_out,
            risk_amount=effective_risk_amount,
            cash_available=cash_available,
            portfolio_exposure_after=portfolio_exposure_after,
            market_regime=run_context.market_regime,
            sizing=sizing,
        ),
        "initial_action": initial_action,
        "final_action": final_action,
        "reason": final_reason,
        "warnings": unique_strings(warnings),
    }
    return decision


def buy_blockers(
    *,
    result: Any,
    run_context: AgentRunRiskContext,
    sector_info: dict[str, Any],
    net_rr_info: dict[str, Any],
    earnings_info: dict[str, Any],
    target_info: dict[str, Any],
    confirmation: dict[str, Any],
    cooldown: dict[str, Any],
    sector_exposure: dict[str, Any],
    factor_exposure: dict[str, Any],
    correlation: dict[str, Any],
    normalized_quality_score: float,
    portfolio_exposure_after: float,
    sizing: dict[str, Any],
    minimum_net_rr: float,
    market_session: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    regime = run_context.market_regime
    session = market_session or market_session_status(
        allow_off_hours_buys=run_context.config.allow_off_hours_buys
    )
    if sizing["blocked"]:
        blockers.append({"action": "WATCH", "reason": f"WATCH: {sizing['reason']}"})
    if not session["can_open_new_buy"]:
        blockers.append(
            {
                "action": "WATCH_READY",
                "reason": (
                    "WATCH_READY: Setup is staged outside regular market hours; "
                    "re-scan after the regular session opens for entry confirmation."
                ),
            }
        )
    if regime.label == "BEAR":
        blockers.append({"action": "SKIP", "reason": "SKIP: Bear market regime blocks new simulated buys."})
    if portfolio_exposure_after > regime.max_total_exposure:
        blockers.append(
            {
                "action": "WATCH",
                "reason": (
                    f"WATCH: Market regime exposure limit would be exceeded "
                    f"({portfolio_exposure_after:.2f} > {regime.max_total_exposure:.2f})."
                ),
            }
        )
    if regime.label in {"BULL", "NEUTRAL"} and float(result.score or 0) < regime.min_setup_score:
        blockers.append(
            {
                "action": "WATCH",
                "reason": (
                    f"WATCH: {regime.label} market requires setup score "
                    f"({float(result.score or 0):.2f} < {regime.min_setup_score:.2f})."
                ),
            }
        )
    if normalized_quality_score < 35:
        blockers.append(
            {
                "action": "WATCH",
                "reason": (
                    f"WATCH: Normalized quality score is too low for a new entry "
                    f"({normalized_quality_score:.2f}/100)."
                ),
            }
        )
    if regime.label == "NEUTRAL" and normalized_quality_score < 45:
        blockers.append(
            {
                "action": "WATCH",
                "reason": (
                    f"WATCH: Neutral market requires stronger normalized quality "
                    f"({normalized_quality_score:.2f}/100)."
                ),
            }
        )
    if sector_info["regime"] == "WEAK":
        blockers.append(
            {"action": "WATCH", "reason": "WATCH: Sector regime is WEAK; do not auto-buy weak sector setups."}
        )
    if sector_info["regime"] == "NEUTRAL" and float(result.score or 0) < 0.40:
        blockers.append({"action": "WATCH", "reason": "WATCH: Neutral sector requires a cleaner setup score."})
    if net_rr_info["net_rr_1"] < run_context.config.minimum_primary_net_rr:
        blockers.append(
            {
                "action": "WATCH",
                "reason": (
                    f"WATCH: Target 1 net R/R {net_rr_info['net_rr_1']:.2f} "
                    f"is below minimum {run_context.config.minimum_primary_net_rr:.2f}; "
                    "Target 2 cannot justify the entry alone."
                ),
            }
        )
    if net_rr_info["net_rr"] < minimum_net_rr:
        blockers.append(
            {
                "action": "WATCH_READY" if near_ready_net_rr(net_rr_info["net_rr"], minimum_net_rr) else "WATCH",
                "reason": (
                    f"Gross R/R is valid, but Net R/R {net_rr_info['net_rr']:.2f} "
                    f"failed minimum {minimum_net_rr:.2f} after slippage/spread adjustment."
                ),
            }
        )
    if earnings_info["earnings_blackout"]:
        blockers.append({"action": "SKIP", "reason": "SKIP: Earnings blackout active."})
    if target_info["status"] == "INVALID":
        blockers.append({"action": "SKIP", "reason": "SKIP: Target validation failed; target is not above price."})
    if target_info["status"] == "EXTENDED":
        blockers.append({"action": "WATCH", "reason": "WATCH: Target distance is extended versus daily ATR or market structure."})
    if target_info["status"] == "LOW_REWARD_DISTANCE":
        blockers.append({"action": "WATCH_READY", "reason": "Target 1 is too close versus daily ATR."})
    if run_context.config.require_entry_confirmation and not confirmation["entry_confirmation_passed"]:
        blockers.append(
            {
                "action": "WATCH",
                "reason": f"WATCH: Entry confirmation failed - {confirmation['confirmation_reason']}",
            }
        )
    if cooldown["cooldown_active"] and not cooldown["cooldown_exception_used"]:
        blockers.append({"action": "WATCH", "reason": f"WATCH: {cooldown['cooldown_reason']}"})
    if sector_exposure["limit_exceeded"]:
        blockers.append(
            {"action": "WATCH", "reason": "WATCH: Sector exposure limit would be exceeded by this trade."}
        )
    if factor_exposure["limit_exceeded"]:
        blockers.append(
            {"action": "WATCH", "reason": "WATCH: Factor/theme exposure limit would be exceeded by this trade."}
        )
    if correlation["correlation_warning"]:
        blockers.append(
            {
                "action": "WATCH",
                "reason": (
                    f"WATCH: High correlation with existing position "
                    f"{correlation['highest_correlation_ticker']}, "
                    f"correlation {correlation['highest_correlation_value']:.2f}."
                ),
            }
        )
    return blockers


def market_session_status(
    now: datetime | None = None,
    *,
    allow_off_hours_buys: bool = False,
) -> dict[str, Any]:
    current = now.astimezone(NY_TZ) if now else datetime.now(NY_TZ)
    minutes = current.hour * 60 + current.minute
    if current.weekday() >= 5:
        phase = "WEEKEND"
        is_regular = False
    elif minutes < 4 * 60:
        phase = "CLOSED"
        is_regular = False
    elif minutes < 9 * 60 + 30:
        phase = "PRE_MARKET"
        is_regular = False
    elif minutes <= 16 * 60:
        phase = "REGULAR"
        is_regular = True
    elif minutes <= 20 * 60:
        phase = "AFTER_HOURS"
        is_regular = False
    else:
        phase = "CLOSED"
        is_regular = False

    can_open = is_regular or allow_off_hours_buys
    reason = (
        "Regular market session is open."
        if is_regular
        else "Outside regular market hours; new simulated buys are staged for the next regular-session confirmation."
    )
    if allow_off_hours_buys and not is_regular:
        reason = "Off-hours buys are enabled by config."
    return {
        "phase": phase,
        "timestamp": current.isoformat(timespec="minutes"),
        "can_open_new_buy": bool(can_open),
        "regular_session_open": bool(is_regular),
        "reason": reason,
    }


def minimum_net_rr_for(
    market_regime: MarketRegime,
    sector_info: dict[str, Any],
    config: AgentRiskConfig,
) -> float:
    if market_regime.label == "NEUTRAL" and sector_info.get("regime") == "STRONG":
        return config.neutral_strong_sector_min_net_rr
    return market_regime.minimum_net_rr


def target_one_warning_only(*, net_rr_info: dict[str, Any], minimum_net_rr: float) -> bool:
    return (
        net_rr_info.get("net_rr", 0.0) >= minimum_net_rr
        and net_rr_info.get("net_rr_2", 0.0) >= 2.0
    )


def near_ready_net_rr(net_rr: float, minimum_net_rr: float) -> bool:
    return net_rr >= max(1.8, minimum_net_rr - 0.30)


def downgrade_action_from_blockers(blockers: list[dict[str, str]]) -> str:
    if any(blocker["action"] == "SKIP" for blocker in blockers):
        return "SKIP"
    if all(blocker["action"] == "WATCH_READY" for blocker in blockers):
        return "WATCH_READY"
    return "WATCH"


def enrich_non_buy_reason(
    action: str,
    initial_reason: str,
    run_context: AgentRunRiskContext,
    sector_info: dict[str, Any],
    net_rr_info: dict[str, Any],
) -> str:
    return (
        f"{action}: {initial_reason} Market regime {run_context.market_regime.label}; "
        f"sector {sector_info['regime']}; net R/R {net_rr_info['net_rr']:.2f}."
    )


def position_sizing_explanation(
    *,
    initial_action: str,
    final_action: str,
    quantity: int,
    cash_out: float,
    risk_amount: float,
    cash_available: float,
    portfolio_exposure_after: float,
    market_regime: MarketRegime,
    sizing: dict[str, Any] | None = None,
) -> str:
    if initial_action != "BUY_SIMULATED":
        return "No new position size was calculated because the base decision did not open a simulated buy."
    if final_action != "BUY_SIMULATED":
        return (
            "Base position sizing produced a candidate trade, but the risk transparency layer "
            f"blocked the new buy. Candidate size: {quantity} shares, cash {cash_out:.2f}, "
            f"risk {risk_amount:.2f}."
        )
    if sizing and sizing.get("adjusted"):
        return (
            f"Position size reduced from {sizing.get('original_quantity')} to {quantity} shares. "
            f"Accepted cash out {cash_out:.2f}, risk {risk_amount:.2f}. "
            f"Reason: {sizing.get('reason')}"
        )
    return (
        f"Position size accepted: {quantity} shares, cash out {cash_out:.2f}, "
        f"risk {risk_amount:.2f}, cash available {cash_available:.2f}, "
        f"portfolio exposure after trade {portfolio_exposure_after:.2f}, "
        f"market-regime exposure cap {market_regime.max_total_exposure:.2f}."
    )


def sector_regime_for(sector: str, sector_health: dict[str, dict[str, Any]]) -> dict[str, Any]:
    item = sector_health.get(sector, {})
    label = str(item.get("label") or "Neutral").upper()
    if label not in {"STRONG", "NEUTRAL", "WEAK"}:
        label = "NEUTRAL"
    score = float(item.get("score", 55.0))
    return {
        "sector": sector,
        "etf": item.get("etf") or SECTOR_ETFS.get(sector, "SPY"),
        "regime": label,
        "score": round(score, 2),
        "reason": item.get("reason", "Sector regime fallback used."),
    }


def adjust_candidate_position_size(
    *,
    ticker: str,
    sector: str,
    factor_tags: list[str],
    initial_action: str,
    quantity: int,
    cash_out: float,
    risk_amount: float,
    cash_available: float,
    portfolio_exposure_before: float,
    open_positions: dict[str, dict[str, Any]],
    sector_map: dict[str, str],
    run_context: AgentRunRiskContext,
) -> dict[str, Any]:
    original = {
        "quantity": quantity,
        "cash_out": cash_out,
        "risk_amount": risk_amount,
        "original_quantity": quantity,
        "original_cash_out": cash_out,
        "original_risk_amount": risk_amount,
        "adjusted": False,
        "blocked": False,
        "reason": "",
    }
    if initial_action != "BUY_SIMULATED" or quantity <= 0 or cash_out <= 0:
        return original

    per_share_cash = cash_out / quantity
    per_share_risk = risk_amount / quantity if quantity else 0.0
    caps = [
        ("candidate size", cash_out),
        ("max position allocation", run_context.config.max_position),
        ("cash available", cash_available),
        ("market regime exposure", max(0.0, run_context.market_regime.max_total_exposure - portfolio_exposure_before)),
    ]

    sector_before = current_sector_exposure(ticker, sector, open_positions, sector_map)
    sector_cap = sector_exposure_cap(run_context)
    caps.append((f"{sector} sector exposure", max(0.0, sector_cap - sector_before)))

    factor_before = current_factor_exposure(ticker, open_positions, sector_map)
    factor_cap = factor_exposure_cap(run_context)
    for tag in factor_tags:
        caps.append((f"{tag} factor exposure", max(0.0, factor_cap - factor_before.get(tag, 0.0))))

    limiting_name, limiting_cash = min(caps, key=lambda item: item[1])
    adjusted_quantity = math.floor(max(0.0, limiting_cash) / per_share_cash) if per_share_cash > 0 else 0
    if adjusted_quantity <= 0:
        return {
            **original,
            "quantity": 0,
            "cash_out": 0.0,
            "risk_amount": 0.0,
            "adjusted": True,
            "blocked": True,
            "reason": f"Position cannot be opened because {limiting_name} cap leaves no executable size.",
        }
    if adjusted_quantity >= quantity:
        return original

    adjusted_cash = adjusted_quantity * per_share_cash
    adjusted_risk = adjusted_quantity * per_share_risk
    return {
        **original,
        "quantity": adjusted_quantity,
        "cash_out": round(adjusted_cash, 2),
        "risk_amount": round(adjusted_risk, 2),
        "adjusted": True,
        "blocked": False,
        "reason": (
            f"Position size reduced by {limiting_name} cap "
            f"({quantity} -> {adjusted_quantity} shares)."
        ),
    }


def build_candidate_snapshot(ticker: str, price: float, config: AgentRiskConfig) -> CandidateMarketSnapshot:
    warnings: list[str] = []
    daily = None
    intraday = None
    atr = 0.0
    atr_pct = 0.0
    avg_dollar_volume = 0.0
    ret_1m = 0.0
    ret_3m = 0.0
    try:
        daily = fetch_daily_frame(ticker, period=config.analysis_period)
        close = daily["Close"]
        price = float(close.iloc[-1]) if len(close) else price
        atr = compute_atr(daily)
        atr_pct = atr / price * 100 if price else 0.0
        recent = daily.tail(20)
        avg_dollar_volume = float((recent["Close"] * recent["Volume"]).mean())
        ret_1m = pct_return(close, 21)
        ret_3m = pct_return(close, 63)
    except Exception as exc:
        warnings.append(f"Candidate market data unavailable: {exc}")
    if config.require_entry_confirmation and config.entry_confirmation_intraday_enabled:
        try:
            intraday = fetch_intraday_frame(
                ticker,
                period=config.entry_confirmation_intraday_period,
                interval=config.entry_confirmation_intraday_interval,
                include_prepost=False,
            )
        except Exception as exc:
            warnings.append(f"Intraday entry confirmation data unavailable: {exc}")

    market_profile = fetch_market_profile(ticker, warnings)
    bucket = market_cap_bucket(market_profile["market_cap"])
    return CandidateMarketSnapshot(
        ticker=ticker,
        price=round(price, 4),
        atr=round(atr, 4),
        atr_pct=round(atr_pct, 4),
        avg_dollar_volume=round(avg_dollar_volume, 2),
        return_1m=round(ret_1m, 4),
        return_3m=round(ret_3m, 4),
        market_cap=round(market_profile["market_cap"], 2),
        market_cap_bucket=bucket,
        bid=round(market_profile["bid"], 4),
        ask=round(market_profile["ask"], 4),
        normalized_momentum_score=normalized_momentum_score(ret_1m, ret_3m, bucket),
        normalized_atr_score=normalized_atr_score(atr_pct, bucket),
        normalized_liquidity_score=normalized_liquidity_score(avg_dollar_volume, bucket),
        normalized_quality_score=50.0,
        daily=daily,
        intraday=intraday,
        warnings=warnings,
    )


def fetch_market_profile(ticker: str, warnings: list[str]) -> dict[str, float]:
    try:
        import yfinance as yf

        fast_info = yf.Ticker(ticker).fast_info
        market_cap = getattr(fast_info, "market_cap", None)
        bid = getattr(fast_info, "bid", None)
        ask = getattr(fast_info, "ask", None)
        if market_cap is None and isinstance(fast_info, dict):
            market_cap = fast_info.get("marketCap") or fast_info.get("market_cap")
            bid = fast_info.get("bid")
            ask = fast_info.get("ask")
        return {
            "market_cap": float(market_cap or 0.0),
            "bid": float(bid or 0.0),
            "ask": float(ask or 0.0),
        }
    except Exception as exc:
        warnings.append(f"Market profile unavailable: {exc}")
        return {"market_cap": 0.0, "bid": 0.0, "ask": 0.0}


def calculate_net_rr(result: Any, snapshot: CandidateMarketSnapshot, config: AgentRiskConfig) -> dict[str, Any]:
    price = float(result.current_price or snapshot.price or 0.0)
    entry_info = resolve_entry_info(result, price)
    entry = entry_info["executable_entry"]
    stop = float(result.stop_loss or 0.0)
    target_1 = float(result.target_1 or 0.0)
    target_2 = float(result.target_2 or 0.0)
    gross_risk = max(0.0, entry - stop)
    gross_reward_1 = max(0.0, target_1 - entry)
    gross_reward_2 = max(0.0, target_2 - entry)
    gross_rr_1 = gross_reward_1 / gross_risk if gross_risk > 0 else 0.0
    gross_rr_2 = gross_reward_2 / gross_risk if gross_risk > 0 else 0.0
    gross_rr_decision = (gross_rr_1 * config.primary_rr_weight) + (gross_rr_2 * config.stretch_rr_weight)

    spread_pct, slippage_pct, liquidity_bucket = execution_cost_pct(
        snapshot.avg_dollar_volume,
        snapshot.atr_pct,
        result.setup_type,
    )
    warnings: list[str] = []
    fallback_spread = entry * spread_pct
    quoted_spread = snapshot.ask - snapshot.bid if snapshot.ask > 0 and snapshot.bid > 0 and snapshot.ask >= snapshot.bid else 0.0
    max_quoted_spread = entry * max(spread_pct * 2.5, 0.0005)
    if quoted_spread > 0 and quoted_spread <= max_quoted_spread:
        estimated_spread = quoted_spread
        spread_source = "bid_ask"
    elif quoted_spread > max_quoted_spread:
        estimated_spread = fallback_spread
        spread_source = "liquidity_fallback_capped_quote"
        warnings.append("Bid/ask spread looked stale or too wide; liquidity fallback used.")
    else:
        estimated_spread = fallback_spread
        spread_source = "liquidity_fallback"

    entry_slippage = entry * slippage_pct
    stop_slippage = entry * slippage_pct
    target_slippage = entry * (slippage_pct * 0.50)
    estimated_slippage = entry_slippage + stop_slippage
    estimated_fees = config.fees_per_share
    half_spread = estimated_spread / 2.0

    net_entry = entry + half_spread + entry_slippage + estimated_fees
    net_stop = stop - half_spread - stop_slippage
    net_target_1 = target_1 - half_spread - target_slippage - estimated_fees
    net_target_2 = target_2 - half_spread - target_slippage - estimated_fees
    net_risk = max(0.0, net_entry - net_stop)
    net_reward_1 = max(0.0, net_target_1 - net_entry)
    net_reward_2 = max(0.0, net_target_2 - net_entry)
    net_rr_1 = net_reward_1 / net_risk if net_risk > 0 else 0.0
    net_rr_2 = net_reward_2 / net_risk if net_risk > 0 else 0.0
    net_rr = (net_rr_1 * config.primary_rr_weight) + (net_rr_2 * config.stretch_rr_weight)
    return {
        "gross_rr": round(gross_rr_decision, 4),
        "gross_rr_1": round(gross_rr_1, 4),
        "gross_rr_2": round(gross_rr_2, 4),
        "gross_rr_decision": round(gross_rr_decision, 4),
        "theoretical_entry": round(entry_info["theoretical_entry"], 4),
        "executable_entry": round(entry, 4),
        "gross_risk_per_share": round(gross_risk, 4),
        "gross_reward_1_per_share": round(gross_reward_1, 4),
        "gross_reward_2_per_share": round(gross_reward_2, 4),
        "estimated_spread": round(estimated_spread, 4),
        "estimated_slippage": round(estimated_slippage, 4),
        "estimated_fees": round(estimated_fees, 4),
        "spread_source": spread_source,
        "execution_liquidity_bucket": liquidity_bucket,
        "net_entry": round(net_entry, 4),
        "net_stop_assumption": round(net_stop, 4),
        "net_target_1_assumption": round(net_target_1, 4),
        "net_target_2_assumption": round(net_target_2, 4),
        "net_risk_per_share": round(net_risk, 4),
        "net_reward_1_per_share": round(net_reward_1, 4),
        "net_reward_2_per_share": round(net_reward_2, 4),
        "net_rr_1": round(net_rr_1, 4),
        "net_rr_2": round(net_rr_2, 4),
        "net_rr": round(net_rr, 4),
        "warnings": warnings,
    }


def resolve_entry_info(result: Any, fallback_price: float) -> dict[str, float]:
    current_price = float(getattr(result, "current_price", fallback_price) or fallback_price or 0.0)
    theoretical = getattr(result, "theoretical_entry", None)
    executable = getattr(result, "executable_entry", None)
    if theoretical in (None, ""):
        theoretical = getattr(result, "buy_zone_low", None)
    if theoretical in (None, ""):
        buy_zone = getattr(result, "buy_zone", None)
        if buy_zone and len(buy_zone) >= 1:
            theoretical = buy_zone[0]
    theoretical = float(theoretical or current_price)

    if executable in (None, ""):
        buy_low = getattr(result, "buy_zone_low", None)
        buy_high = getattr(result, "buy_zone_high", None)
        buy_zone = getattr(result, "buy_zone", None)
        if (buy_low in (None, "") or buy_high in (None, "")) and buy_zone and len(buy_zone) >= 2:
            buy_low, buy_high = buy_zone[0], buy_zone[1]
        if buy_low not in (None, "") and buy_high not in (None, "") and float(buy_low) <= current_price <= float(buy_high):
            executable = current_price
        elif current_price > theoretical:
            executable = current_price
        else:
            executable = theoretical
    return {"theoretical_entry": float(theoretical), "executable_entry": float(executable or current_price)}


def execution_cost_pct(avg_dollar_volume: float, atr_pct: float, setup_type: str) -> tuple[float, float, str]:
    if avg_dollar_volume >= 500_000_000:
        spread_pct, slippage_pct, bucket = 0.0003, 0.0007, "large_liquid"
    elif avg_dollar_volume >= 100_000_000:
        spread_pct, slippage_pct, bucket = 0.0008, 0.0015, "mid_liquid"
    elif avg_dollar_volume >= 25_000_000:
        spread_pct, slippage_pct, bucket = 0.0015, 0.0030, "lower_liquidity"
    else:
        spread_pct, slippage_pct, bucket = 0.0025, 0.0060, "low_liquidity"
    if "breakout" in str(setup_type).lower():
        slippage_pct += 0.0010 if avg_dollar_volume >= 100_000_000 else 0.0020
    if atr_pct >= 6:
        slippage_pct *= 1.15
    return spread_pct, min(slippage_pct, 0.0075), bucket


def calculate_earnings_blackout(ticker: str, config: AgentRiskConfig) -> dict[str, Any]:
    warnings: list[str] = []
    try:
        earnings_date = fetch_next_earnings_date(ticker)
    except Exception as exc:
        earnings_date = None
        warnings.append(f"Earnings data unavailable: {exc}")
    if not earnings_date:
        warnings.append("Earnings data unavailable.")
        return {
            "earnings_date": "",
            "days_to_earnings": None,
            "earnings_blackout": bool(config.block_unknown_earnings),
            "warnings": warnings,
        }
    days = business_days_until(earnings_date)
    blackout = days is not None and -config.earnings_blackout_after_days <= days <= config.earnings_blackout_before_days
    return {
        "earnings_date": earnings_date,
        "days_to_earnings": days,
        "earnings_blackout": bool(blackout),
        "warnings": warnings,
    }


def calculate_entry_confirmation(
    result: Any,
    snapshot: CandidateMarketSnapshot,
    config: AgentRiskConfig,
) -> dict[str, Any]:
    if not config.require_entry_confirmation:
        return {
            "confirmation_status": "DISABLED",
            "confirmation_reason": "Entry confirmation requirement disabled by config.",
            "trigger_level": 0.0,
            "close_above_trigger": True,
            "vwap_reclaimed": False,
            "retest_held": True,
            "entry_confirmation_passed": True,
            "confirmation_timeframe": "disabled",
            "confirmation_candle_timestamp": "",
            "warnings": [],
        }

    base = {
        "confirmation_status": "UNKNOWN",
        "confirmation_reason": "Entry confirmation unavailable.",
        "trigger_level": 0.0,
        "close_above_trigger": False,
        "vwap_reclaimed": False,
        "retest_held": False,
        "entry_confirmation_passed": False,
        "confirmation_timeframe": "1d_completed",
        "confirmation_candle_timestamp": "",
        "warnings": [],
    }
    buy_low = getattr(result, "buy_zone_low", None)
    buy_high = getattr(result, "buy_zone_high", None)
    if buy_low in (None, "") or buy_high in (None, ""):
        base["warnings"] = ["Entry confirmation data unavailable; blocking auto-buy."]
        return base

    if config.entry_confirmation_intraday_enabled:
        intraday_confirmation = calculate_entry_confirmation_from_frame(
            result=result,
            frame=snapshot.intraday,
            buy_low=float(buy_low),
            buy_high=float(buy_high),
            timeframe=f"{config.entry_confirmation_intraday_interval}_completed",
            drop_last=True,
        )
        if intraday_confirmation["confirmation_status"] != "UNKNOWN":
            return intraday_confirmation
        base["warnings"].extend(intraday_confirmation.get("warnings", []))

    daily = snapshot.daily
    if daily is None or daily.empty or len(daily) < 3 or buy_low in (None, "") or buy_high in (None, ""):
        if not base["warnings"]:
            base["warnings"] = ["Entry confirmation data unavailable; blocking auto-buy."]
        return base

    daily_confirmation = calculate_entry_confirmation_from_frame(
        result=result,
        frame=daily,
        buy_low=float(buy_low),
        buy_high=float(buy_high),
        timeframe="1d_completed",
        drop_last=True,
    )
    if daily_confirmation.get("warnings") and base["warnings"]:
        daily_confirmation["warnings"] = [*base["warnings"], *daily_confirmation["warnings"]]
    return daily_confirmation


def calculate_entry_confirmation_from_frame(
    *,
    result: Any,
    frame: pd.DataFrame | None,
    buy_low: float,
    buy_high: float,
    timeframe: str,
    drop_last: bool,
) -> dict[str, Any]:
    base = {
        "confirmation_status": "UNKNOWN",
        "confirmation_reason": "Entry confirmation unavailable.",
        "trigger_level": 0.0,
        "close_above_trigger": False,
        "vwap_reclaimed": False,
        "retest_held": False,
        "entry_confirmation_passed": False,
        "confirmation_timeframe": timeframe,
        "confirmation_candle_timestamp": "",
        "warnings": [],
    }
    if frame is None or frame.empty or len(frame) < 3:
        base["warnings"] = [f"Not enough {timeframe} candles for entry confirmation."]
        return base

    completed = frame.iloc[:-1] if drop_last else frame
    if len(completed) < 2:
        base["warnings"] = [f"Not enough completed {timeframe} candles for entry confirmation."]
        return base

    last = completed.iloc[-1]
    prev = completed.iloc[-2]
    close = float(last["Close"])
    open_ = float(last["Open"])
    low = float(last["Low"])
    high = float(last["High"])
    prev_close = float(prev["Close"])
    setup_type = str(getattr(result, "setup_type", "") or "").lower()
    candle_time = completed.index[-1].isoformat() if hasattr(completed.index[-1], "isoformat") else str(completed.index[-1])
    bullish_or_reclaim = close >= open_ and close >= prev_close
    falling_into_zone = close < open_ and close < prev_close

    trigger_level = buy_high
    close_above_trigger = close >= trigger_level
    retest_held = low >= buy_low and close >= buy_low
    vwap_reclaimed = False

    if "breakout" in setup_type:
        passed = close_above_trigger and retest_held and not falling_into_zone
        reason = (
            "Completed candle closed above retest trigger and held the zone."
            if passed
            else "Breakout/retest confirmation requires completed close above trigger, held retest, and no falling candle."
        )
    elif "vwap" in setup_type:
        vwap = rolling_vwap(completed)
        vwap_reclaimed = bool(vwap and close >= vwap and close >= open_ and close >= prev_close)
        trigger_level = float(vwap or trigger_level)
        close_above_trigger = bool(vwap and close >= vwap)
        retest_held = close >= buy_low and not falling_into_zone
        passed = vwap_reclaimed and retest_held
        reason = (
            "Completed candle reclaimed VWAP proxy and held the setup zone."
            if passed
            else "VWAP reclaim requires completed close above VWAP proxy with hold/follow-through."
        )
    else:
        zone_width = max(0.0, buy_high - buy_low)
        zone_mid = buy_low + (zone_width * 0.50)
        candle_range = max(high - low, 0.0001)
        close_position = (close - low) / candle_range
        touched_zone = low <= buy_high and high >= buy_low
        strong_bullish_reclaim = (
            touched_zone
            and close >= max(buy_low, zone_mid)
            and close > open_
            and close >= prev_close
            and close_position >= 0.60
        )
        retest_held = touched_zone and close >= buy_low
        close_above_trigger = close >= buy_high
        passed = retest_held and not falling_into_zone and (close_above_trigger or strong_bullish_reclaim)
        reason = (
            "Completed candle closed above the buy zone or showed a strong bullish reclaim from the zone."
            if passed
            else (
                "Support/Fib setup requires completed close above the buy zone or a strong bullish "
                "reclaim from the zone; weak or falling candles are blocked."
            )
        )

    return {
        "confirmation_status": "PASSED" if passed else "FAILED",
        "confirmation_reason": reason,
        "trigger_level": round(float(trigger_level or 0.0), 4),
        "close_above_trigger": bool(close_above_trigger),
        "vwap_reclaimed": bool(vwap_reclaimed),
        "retest_held": bool(retest_held),
        "entry_confirmation_passed": bool(passed),
        "confirmation_timeframe": timeframe,
        "confirmation_candle_timestamp": candle_time,
        "warnings": [] if passed else [reason],
    }


def rolling_vwap(frame: pd.DataFrame, lookback: int = 20) -> float | None:
    if frame is None or frame.empty or not {"High", "Low", "Close", "Volume"}.issubset(frame.columns):
        return None
    window = frame.tail(min(lookback, len(frame)))
    volume = window["Volume"].astype(float)
    total_volume = float(volume.sum())
    if total_volume <= 0:
        return None
    typical = (window["High"].astype(float) + window["Low"].astype(float) + window["Close"].astype(float)) / 3.0
    return float((typical * volume).sum() / total_volume)


def cooldown_check(
    *,
    ticker: str,
    result: Any,
    net_rr_info: dict[str, Any],
    confirmation: dict[str, Any],
    recent_stop_events: dict[str, dict[str, Any]],
    config: AgentRiskConfig,
) -> dict[str, Any]:
    event = recent_stop_events.get(ticker.upper())
    if not event:
        return {
            "cooldown_active": False,
            "last_stop_date": "",
            "cooldown_days_remaining": 0,
            "cooldown_exception_used": False,
            "cooldown_reason": "",
            "warnings": [],
        }

    days_remaining = int(event.get("days_remaining") or 0)
    if days_remaining <= 0:
        return {
            "cooldown_active": False,
            "last_stop_date": str(event.get("last_stop_date") or ""),
            "cooldown_days_remaining": 0,
            "cooldown_exception_used": False,
            "cooldown_reason": "",
            "warnings": [],
        }

    current_setup = str(getattr(result, "setup_type", "") or "")
    last_setup = str(event.get("setup_type") or "")
    exception_used = (
        float(getattr(result, "score", 0) or 0) >= config.cooldown_exception_setup_score
        and float(net_rr_info.get("net_rr_1") or 0) >= config.cooldown_exception_net_rr1
        and bool(confirmation.get("entry_confirmation_passed"))
        and current_setup
        and last_setup
        and current_setup != last_setup
    )
    reason = (
        f"Stop-loss cooldown active after {event.get('last_stop_date')}; "
        f"{days_remaining} trading days remaining."
    )
    warnings = [] if exception_used else [reason]
    return {
        "cooldown_active": True,
        "last_stop_date": str(event.get("last_stop_date") or ""),
        "cooldown_days_remaining": days_remaining,
        "cooldown_exception_used": bool(exception_used),
        "cooldown_reason": reason if not exception_used else "Cooldown exception used for stronger, meaningfully new setup.",
        "warnings": warnings,
    }


def validate_targets(
    result: Any,
    snapshot: CandidateMarketSnapshot,
    config: AgentRiskConfig | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    price = float(result.current_price or 0.0)
    atr = snapshot.atr
    t1 = float(result.target_1 or 0.0)
    t2 = float(result.target_2 or 0.0)
    if atr <= 0 or price <= 0 or t1 <= 0 or t2 <= 0:
        return {
            "target_1_atr_distance": 0.0,
            "target_2_atr_distance": 0.0,
            "status": "UNKNOWN",
            "market_structure_status": "UNKNOWN",
            "previous_resistance": 0.0,
            "prior_high_20": 0.0,
            "prior_high_63": 0.0,
            "warnings": ["Target ATR feasibility unavailable."],
        }
    structure = assess_target_market_structure(snapshot.daily, price, atr, t1, t2, result.setup_type)
    warnings.extend(structure["warnings"])
    d1 = (t1 - price) / atr
    d2 = (t2 - price) / atr
    min_target1_distance = config.minimum_target1_atr_distance if config else 0.75
    if d1 <= 0 or d2 <= 0:
        status = "INVALID"
        warnings.append("Target is not above current price.")
    elif d1 > 7 or d2 > 14:
        status = "EXTENDED"
        warnings.append("Target distance is extended versus daily ATR.")
    elif structure["status"] == "EXTENDED_ABOVE_STRUCTURE":
        status = "EXTENDED"
        warnings.append("Target distance is extended versus recent market structure.")
    elif d1 > 5 or d2 > 10:
        status = "AGGRESSIVE"
        warnings.append("Target distance is aggressive versus daily ATR.")
    elif d1 < min_target1_distance:
        status = "LOW_REWARD_DISTANCE"
        warnings.append("Target 1 is close versus daily ATR.")
    else:
        status = "OK"
    return {
        "target_1_atr_distance": round(d1, 4),
        "target_2_atr_distance": round(d2, 4),
        "status": status,
        "market_structure_status": structure["status"],
        "previous_resistance": structure["previous_resistance"],
        "prior_high_20": structure["prior_high_20"],
        "prior_high_63": structure["prior_high_63"],
        "warnings": warnings,
    }


def assess_target_market_structure(
    daily: pd.DataFrame | None,
    price: float,
    atr: float,
    target_1: float,
    target_2: float,
    setup_type: str,
) -> dict[str, Any]:
    if daily is None or daily.empty or "High" not in daily:
        return {
            "status": "UNKNOWN",
            "previous_resistance": 0.0,
            "prior_high_20": 0.0,
            "prior_high_63": 0.0,
            "warnings": ["Market structure target validation unavailable."],
        }
    history = daily.iloc[:-1] if len(daily) > 1 else daily
    if history.empty:
        return {
            "status": "UNKNOWN",
            "previous_resistance": 0.0,
            "prior_high_20": 0.0,
            "prior_high_63": 0.0,
            "warnings": ["Market structure target validation unavailable."],
        }
    high_20 = float(history["High"].tail(20).max())
    high_63 = float(history["High"].tail(63).max())
    previous_resistance = high_20 if high_20 >= price else high_63
    breakout = "breakout" in str(setup_type).lower()
    warnings: list[str] = []

    if target_1 <= high_20 * 1.02:
        status = "SUPPORTED_BY_20D_RESISTANCE"
    elif target_1 <= high_63 * 1.03:
        status = "SUPPORTED_BY_63D_RESISTANCE"
    elif breakout and target_1 <= price + (atr * 5) and target_2 <= price + (atr * 10):
        status = "BREAKOUT_MEASURED_MOVE"
    elif target_1 > high_63 + (atr * 4) or target_2 > high_63 + (atr * 8):
        status = "EXTENDED_ABOVE_STRUCTURE"
        warnings.append("Targets are extended above recent resistance structure.")
    else:
        status = "MIXED_STRUCTURE"

    return {
        "status": status,
        "previous_resistance": round(previous_resistance, 4),
        "prior_high_20": round(high_20, 4),
        "prior_high_63": round(high_63, 4),
        "warnings": warnings,
    }


def sector_exposure_check(
    *,
    ticker: str,
    sector: str,
    initial_action: str,
    trade_cash_out: float,
    open_positions: dict[str, dict[str, Any]],
    sector_map: dict[str, str],
    run_context: AgentRunRiskContext,
) -> dict[str, Any]:
    before = current_sector_exposure(ticker, sector, open_positions, sector_map)
    warnings = []
    after = before + (trade_cash_out if initial_action == "BUY_SIMULATED" else 0.0)
    cap = sector_exposure_cap(run_context)
    limit_exceeded = initial_action == "BUY_SIMULATED" and after > cap
    if limit_exceeded:
        warnings.append("Sector exposure limit would be exceeded.")
    return {"before": round(before, 2), "after": round(after, 2), "cap": round(cap, 2), "limit_exceeded": limit_exceeded, "warnings": warnings}


def current_sector_exposure(
    ticker: str,
    sector: str,
    open_positions: dict[str, dict[str, Any]],
    sector_map: dict[str, str],
) -> float:
    before = 0.0
    for open_ticker, pos in open_positions.items():
        if str(open_ticker).upper() == ticker:
            continue
        if sector_map.get(str(open_ticker).upper(), "Unknown") == sector:
            before += float(pos.get("exposure_ils") or 0)
    return before


def sector_exposure_cap(run_context: AgentRunRiskContext) -> float:
    pct = (
        run_context.config.sector_exposure_bull_pct
        if run_context.market_regime.label == "BULL"
        else run_context.config.sector_exposure_neutral_pct
    )
    return max(0.0, run_context.market_regime.max_total_exposure * pct)


def factor_exposure_check(
    *,
    ticker: str,
    factor_tags: list[str],
    initial_action: str,
    trade_cash_out: float,
    open_positions: dict[str, dict[str, Any]],
    sector_map: dict[str, str],
    run_context: AgentRunRiskContext,
) -> dict[str, Any]:
    before = current_factor_exposure(ticker, open_positions, sector_map)
    after = dict(before)
    if initial_action == "BUY_SIMULATED":
        for tag in factor_tags:
            after[tag] = after.get(tag, 0.0) + trade_cash_out
    cap = factor_exposure_cap(run_context)
    limit_exceeded = initial_action == "BUY_SIMULATED" and any(after.get(tag, 0.0) > cap for tag in factor_tags)
    warnings = ["Factor/theme exposure limit would be exceeded."] if limit_exceeded else []
    return {
        "before": {key: round(value, 2) for key, value in sorted(before.items())},
        "after": {key: round(value, 2) for key, value in sorted(after.items())},
        "cap": round(cap, 2),
        "limit_exceeded": limit_exceeded,
        "warnings": warnings,
    }


def current_factor_exposure(
    ticker: str,
    open_positions: dict[str, dict[str, Any]],
    sector_map: dict[str, str],
) -> dict[str, float]:
    before: dict[str, float] = {}
    for open_ticker, pos in open_positions.items():
        open_ticker = str(open_ticker).upper()
        if open_ticker == ticker:
            continue
        tags = factor_tags_for(open_ticker, sector_map.get(open_ticker, "Unknown"), "Unknown")
        for tag in tags:
            before[tag] = before.get(tag, 0.0) + float(pos.get("exposure_ils") or 0)
    return before


def factor_exposure_cap(run_context: AgentRunRiskContext) -> float:
    pct = (
        run_context.config.factor_exposure_bull_pct
        if run_context.market_regime.label == "BULL"
        else run_context.config.factor_exposure_neutral_pct
    )
    return max(0.0, run_context.market_regime.max_total_exposure * pct)


def correlation_check(
    *,
    ticker: str,
    candidate_daily: pd.DataFrame | None,
    open_positions: dict[str, dict[str, Any]],
    analysis_period: str,
) -> dict[str, Any]:
    warnings = []
    best_ticker = ""
    best_corr: float | None = None
    if candidate_daily is None or candidate_daily.empty or not open_positions:
        return {
            "correlation_warning": False,
            "highest_correlation_ticker": "",
            "highest_correlation_value": None,
            "warnings": warnings,
        }
    candidate_returns = candidate_daily["Close"].pct_change().dropna().tail(90)
    if len(candidate_returns) < 40:
        warnings.append("Insufficient candidate history for correlation check.")
        return {
            "correlation_warning": False,
            "highest_correlation_ticker": "",
            "highest_correlation_value": None,
            "warnings": warnings,
        }
    for open_ticker in open_positions:
        open_ticker = str(open_ticker).upper()
        if open_ticker == ticker:
            continue
        try:
            open_daily = fetch_daily_frame(open_ticker, period=analysis_period)
            open_returns = open_daily["Close"].pct_change().dropna().tail(90)
            aligned = pd.concat([candidate_returns, open_returns], axis=1, join="inner").dropna()
            if len(aligned) < 40:
                continue
            corr = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
            if math.isnan(corr):
                continue
            if best_corr is None or corr > best_corr:
                best_corr = corr
                best_ticker = open_ticker
        except Exception as exc:
            warnings.append(f"Correlation data unavailable for {open_ticker}: {exc}")
    warning = best_corr is not None and best_corr >= float(os.getenv("MARKET_LENS_CORRELATION_BLOCK_THRESHOLD", "0.85"))
    return {
        "correlation_warning": bool(warning),
        "highest_correlation_ticker": best_ticker,
        "highest_correlation_value": round(best_corr, 4) if best_corr is not None else None,
        "warnings": warnings,
    }


def factor_tags_for(ticker: str, sector: str, market_cap_bucket_value: str) -> list[str]:
    tags = {sector} if sector and sector != "Unknown" else set()
    semis = {"NVDA", "AMD", "AVGO", "ASML", "AMAT", "LRCX", "KLAC", "TSM", "MU", "QCOM", "INTC", "TXN", "SMCI"}
    mega_tech = {"AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "AVGO"}
    high_beta = {"CRWD", "SNOW", "NET", "PLTR", "DDOG", "SMCI", "TSLA", "AMD", "NVDA", "PANW", "NOW"}
    if ticker in semis or sector == "Semiconductors":
        tags.add("AI / Semiconductors")
    if ticker in mega_tech:
        tags.add("Mega Cap Tech")
        tags.add("Rates-sensitive Growth")
    if ticker in high_beta:
        tags.add("High Beta Growth")
    if sector in {"Healthcare", "Utilities", "Consumer Defensive"}:
        tags.add("Defensive")
    if sector == "Financials":
        tags.add("Financials")
    if sector == "Energy":
        tags.add("Energy")
    if sector == "Consumer":
        tags.add("Consumer Cyclical")
    if market_cap_bucket_value == "Small Cap":
        tags.add("Small Cap / Risk-On")
    if sector in {"Utilities", "Consumer Defensive"}:
        tags.add("Low Volatility")
    return sorted(tags)


def pct_return(close: Any, lookback: int) -> float:
    if len(close) <= lookback:
        lookback = len(close) - 1
    if lookback <= 0:
        return 0.0
    start = float(close.iloc[-lookback])
    end = float(close.iloc[-1])
    return end / start - 1 if start > 0 else 0.0


def trend_is_bullish(state: dict[str, Any]) -> bool:
    return state.get("trend") == "bullish"


def trend_is_bearish(state: dict[str, Any]) -> bool:
    return state.get("trend") == "bearish"


def vix_is_calm(state: dict[str, Any]) -> bool:
    return state.get("trend") == "calm" or float(state.get("price") or 99) < 20


def vix_is_stressed(state: dict[str, Any]) -> bool:
    return state.get("trend") == "stressed" or float(state.get("price") or 0) >= 25


def market_cap_bucket(market_cap: float) -> str:
    if market_cap >= 200_000_000_000:
        return "Mega Cap"
    if market_cap >= 10_000_000_000:
        return "Large Cap"
    if market_cap >= 2_000_000_000:
        return "Mid Cap"
    if market_cap > 0:
        return "Small Cap"
    return "Unknown"


def normalized_momentum_score(ret_1m: float, ret_3m: float, bucket: str) -> float:
    multiplier = {"Mega Cap": 360, "Large Cap": 320, "Mid Cap": 270, "Small Cap": 220}.get(bucket, 280)
    return round(clamp(50 + ret_1m * 160 + ret_3m * multiplier, 0, 100), 2)


def normalized_atr_score(atr_pct: float, bucket: str) -> float:
    ideal = {"Mega Cap": (1.0, 4.8), "Large Cap": (1.2, 5.5), "Mid Cap": (1.6, 6.5), "Small Cap": (2.0, 7.5)}.get(
        bucket,
        (1.2, 6.0),
    )
    low, high = ideal
    if low <= atr_pct <= high:
        return 100.0
    if atr_pct < low:
        return round(clamp(45 + (atr_pct / max(low, 0.1)) * 55, 0, 100), 2)
    return round(clamp(100 - ((atr_pct - high) / max(high, 0.1)) * 50, 20, 100), 2)


def normalized_liquidity_score(avg_dollar_volume: float, bucket: str) -> float:
    target = {
        "Mega Cap": 500_000_000,
        "Large Cap": 150_000_000,
        "Mid Cap": 50_000_000,
        "Small Cap": 20_000_000,
    }.get(bucket, 100_000_000)
    if avg_dollar_volume <= 0:
        return 35.0
    return round(clamp(math.log10(avg_dollar_volume / target + 1) * 75, 0, 100), 2)


def business_days_until(date_text: str) -> int | None:
    try:
        target = pd.Timestamp(date_text).date()
    except Exception:
        return None
    today = datetime.now(timezone.utc).date()
    if target >= today:
        return max(0, len(pd.bdate_range(today, target)) - 1)
    return -max(1, len(pd.bdate_range(target, today)) - 1)


def unique_strings(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))
