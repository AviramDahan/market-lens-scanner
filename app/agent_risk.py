from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.data import fetch_daily_frame, fetch_next_earnings_date
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
    neutral_min_setup_score: float = 0.45
    sector_exposure_bull_pct: float = 0.40
    sector_exposure_neutral_pct: float = 0.30
    factor_exposure_bull_pct: float = 0.50
    factor_exposure_neutral_pct: float = 0.35
    correlation_block_threshold: float = 0.85
    earnings_blackout_before_days: int = 5
    earnings_blackout_after_days: int = 1
    block_unknown_earnings: bool = False
    fees_per_share: float = 0.0

    def __post_init__(self) -> None:
        if self.bull_max_exposure is None:
            self.bull_max_exposure = min(self.default_max_total_exposure, self.starting_capital * 0.40)
        if self.neutral_max_exposure is None:
            self.neutral_max_exposure = min(self.default_max_total_exposure, self.starting_capital * 0.20)


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
        block_unknown_earnings=os.getenv("MARKET_LENS_BLOCK_UNKNOWN_EARNINGS", "false").lower()
        in {"1", "true", "yes"},
        fees_per_share=float(os.getenv("MARKET_LENS_AGENT_FEES_PER_SHARE", "0")),
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
        min_setup_score = 0.0
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
) -> dict[str, Any]:
    config = run_context.config
    ticker = str(result.ticker).upper()
    sector = sector_map.get(ticker, "Unknown")
    sector_info = sector_regime_for(sector, run_context.sector_health)
    snapshot = build_candidate_snapshot(ticker, result.current_price, config.analysis_period)
    factor_tags = factor_tags_for(ticker, sector, snapshot.market_cap_bucket)
    net_rr_info = calculate_net_rr(result, snapshot, config)
    earnings_info = calculate_earnings_blackout(ticker, config)
    target_info = validate_targets(result, snapshot)
    normalized_quality_score = round(
        (
            snapshot.normalized_momentum_score * 0.45
            + snapshot.normalized_atr_score * 0.25
            + snapshot.normalized_liquidity_score * 0.30
        ),
        2,
    )
    portfolio_exposure_after_if_buy = (
        portfolio_exposure_before + cash_out if initial_action == "BUY_SIMULATED" else portfolio_exposure_before
    )
    sector_exposure = sector_exposure_check(
        ticker=ticker,
        sector=sector,
        initial_action=initial_action,
        trade_cash_out=cash_out,
        open_positions=open_positions,
        sector_map=sector_map,
        run_context=run_context,
    )
    factor_exposure = factor_exposure_check(
        ticker=ticker,
        factor_tags=factor_tags,
        initial_action=initial_action,
        trade_cash_out=cash_out,
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
    warnings.extend(correlation["warnings"])
    warnings.extend(sector_exposure["warnings"])
    warnings.extend(factor_exposure["warnings"])
    if earnings_info["earnings_blackout"]:
        warnings.append("Earnings blackout active.")

    final_action = initial_action
    final_reason = initial_reason
    if initial_action == "BUY_SIMULATED":
        blockers = buy_blockers(
            result=result,
            run_context=run_context,
            sector_info=sector_info,
            net_rr_info=net_rr_info,
            earnings_info=earnings_info,
            target_info=target_info,
            sector_exposure=sector_exposure,
            factor_exposure=factor_exposure,
            correlation=correlation,
            normalized_quality_score=normalized_quality_score,
            portfolio_exposure_after=portfolio_exposure_after_if_buy,
        )
        if blockers:
            final_action = "WATCH" if any(blocker["action"] == "WATCH" for blocker in blockers) else "SKIP"
            final_reason = blockers[0]["reason"]
            warnings.extend(blocker["reason"] for blocker in blockers[1:])
        else:
            final_reason = (
                f"BUY_SIMULATED: {run_context.market_regime.label} market regime, "
                f"{sector_info['regime']} sector, valid setup, net R/R {net_rr_info['net_rr']:.2f}, "
                "no earnings blackout, correlation acceptable, and risk limits allow entry."
            )
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
        "market_regime_indicators": run_context.market_regime.indicators,
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
        "gross_rr": net_rr_info["gross_rr"],
        "estimated_spread": net_rr_info["estimated_spread"],
        "estimated_slippage": net_rr_info["estimated_slippage"],
        "estimated_fees": net_rr_info["estimated_fees"],
        "spread_source": net_rr_info["spread_source"],
        "net_rr": net_rr_info["net_rr"],
        "earnings_date": earnings_info["earnings_date"],
        "days_to_earnings": earnings_info["days_to_earnings"],
        "earnings_blackout": earnings_info["earnings_blackout"],
        "sector_exposure_before": sector_exposure["before"],
        "sector_exposure_after": sector_exposure["after"],
        "factor_tags": factor_tags,
        "factor_exposure_before": factor_exposure["before"],
        "factor_exposure_after": factor_exposure["after"],
        "correlation_warning": correlation["correlation_warning"],
        "highest_correlation_ticker": correlation["highest_correlation_ticker"],
        "highest_correlation_value": correlation["highest_correlation_value"],
        "position_size": quantity if final_action == "BUY_SIMULATED" else 0,
        "risk_amount": round(risk_amount if final_action == "BUY_SIMULATED" else 0.0, 2),
        "cash_available": round(cash_available, 2),
        "portfolio_exposure_before": round(portfolio_exposure_before, 2),
        "portfolio_exposure_after": round(portfolio_exposure_after, 2),
        "position_sizing_explanation": position_sizing_explanation(
            initial_action=initial_action,
            final_action=final_action,
            quantity=quantity,
            cash_out=cash_out,
            risk_amount=risk_amount,
            cash_available=cash_available,
            portfolio_exposure_after=portfolio_exposure_after,
            market_regime=run_context.market_regime,
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
    sector_exposure: dict[str, Any],
    factor_exposure: dict[str, Any],
    correlation: dict[str, Any],
    normalized_quality_score: float,
    portfolio_exposure_after: float,
) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    regime = run_context.market_regime
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
    if regime.label == "NEUTRAL" and float(result.score or 0) < regime.min_setup_score:
        blockers.append(
            {
                "action": "WATCH",
                "reason": (
                    f"WATCH: Neutral market requires stronger setup score "
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
    if net_rr_info["net_rr"] < regime.minimum_net_rr:
        blockers.append(
            {
                "action": "WATCH",
                "reason": (
                    f"WATCH: Gross R/R is valid, but Net R/R {net_rr_info['net_rr']:.2f} "
                    f"failed after slippage/spread adjustment."
                ),
            }
        )
    if earnings_info["earnings_blackout"]:
        blockers.append({"action": "SKIP", "reason": "SKIP: Earnings blackout active."})
    if target_info["status"] == "INVALID":
        blockers.append({"action": "SKIP", "reason": "SKIP: Target validation failed; target is not above price."})
    if target_info["status"] == "EXTENDED":
        blockers.append({"action": "WATCH", "reason": "WATCH: Target distance is extended versus daily ATR or market structure."})
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
) -> str:
    if initial_action != "BUY_SIMULATED":
        return "No new position size was calculated because the base decision did not open a simulated buy."
    if final_action != "BUY_SIMULATED":
        return (
            "Base position sizing produced a candidate trade, but the risk transparency layer "
            f"blocked the new buy. Candidate size: {quantity} shares, cash {cash_out:.2f}, "
            f"risk {risk_amount:.2f}."
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


def build_candidate_snapshot(ticker: str, price: float, analysis_period: str) -> CandidateMarketSnapshot:
    warnings: list[str] = []
    daily = None
    atr = 0.0
    atr_pct = 0.0
    avg_dollar_volume = 0.0
    ret_1m = 0.0
    ret_3m = 0.0
    try:
        daily = fetch_daily_frame(ticker, period=analysis_period)
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
    stop = float(result.stop_loss or 0.0)
    target_1 = float(result.target_1 or 0.0)
    target_2 = float(result.target_2 or 0.0)
    gross_rr = float(result.risk_reward or 0.0)
    spread_pct, slippage_pct = execution_cost_pct(snapshot.avg_dollar_volume, snapshot.atr_pct, result.setup_type)
    fallback_spread = price * spread_pct
    quoted_spread = snapshot.ask - snapshot.bid if snapshot.ask > 0 and snapshot.bid > 0 and snapshot.ask >= snapshot.bid else 0.0
    estimated_spread = quoted_spread if quoted_spread > 0 else fallback_spread
    spread_source = "bid_ask" if quoted_spread > 0 else "liquidity_fallback"
    estimated_slippage = price * slippage_pct
    estimated_fees = config.fees_per_share
    friction = estimated_spread + estimated_slippage + estimated_fees

    risk = max(0.0, price - stop)
    if target_1 > 0 and target_2 > 0:
        planned_reward = ((target_1 - price) * 0.5) + ((target_2 - price) * 0.5)
    elif target_1 > 0:
        planned_reward = target_1 - price
    else:
        planned_reward = 0.0
    net_reward = max(0.0, planned_reward - friction)
    net_risk = risk + friction
    net_rr = net_reward / net_risk if net_risk > 0 else 0.0
    return {
        "gross_rr": round(gross_rr, 4),
        "estimated_spread": round(estimated_spread, 4),
        "estimated_slippage": round(estimated_slippage, 4),
        "estimated_fees": round(estimated_fees, 4),
        "spread_source": spread_source,
        "net_rr": round(net_rr, 4),
    }


def execution_cost_pct(avg_dollar_volume: float, atr_pct: float, setup_type: str) -> tuple[float, float]:
    if avg_dollar_volume >= 500_000_000:
        spread_pct, slippage_pct = 0.0005, 0.0007
    elif avg_dollar_volume >= 100_000_000:
        spread_pct, slippage_pct = 0.0010, 0.0015
    elif avg_dollar_volume >= 25_000_000:
        spread_pct, slippage_pct = 0.0025, 0.0035
    else:
        spread_pct, slippage_pct = 0.0045, 0.0060
    if "breakout" in str(setup_type).lower():
        slippage_pct *= 1.5
    if atr_pct >= 6:
        slippage_pct *= 1.25
    return spread_pct, slippage_pct


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


def validate_targets(result: Any, snapshot: CandidateMarketSnapshot) -> dict[str, Any]:
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
    elif d1 < 0.75:
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
    before = 0.0
    warnings = []
    for open_ticker, pos in open_positions.items():
        if open_ticker == ticker:
            continue
        if sector_map.get(str(open_ticker).upper(), "Unknown") == sector:
            before += float(pos.get("exposure_ils") or 0)
    after = before + (trade_cash_out if initial_action == "BUY_SIMULATED" else 0.0)
    pct = (
        run_context.config.sector_exposure_bull_pct
        if run_context.market_regime.label == "BULL"
        else run_context.config.sector_exposure_neutral_pct
    )
    cap = max(0.0, run_context.market_regime.max_total_exposure * pct)
    limit_exceeded = initial_action == "BUY_SIMULATED" and after > cap
    if limit_exceeded:
        warnings.append("Sector exposure limit would be exceeded.")
    return {"before": round(before, 2), "after": round(after, 2), "cap": round(cap, 2), "limit_exceeded": limit_exceeded, "warnings": warnings}


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
    before: dict[str, float] = {}
    for open_ticker, pos in open_positions.items():
        if open_ticker == ticker:
            continue
        tags = factor_tags_for(str(open_ticker).upper(), sector_map.get(str(open_ticker).upper(), "Unknown"), "Unknown")
        for tag in tags:
            before[tag] = before.get(tag, 0.0) + float(pos.get("exposure_ils") or 0)
    after = dict(before)
    if initial_action == "BUY_SIMULATED":
        for tag in factor_tags:
            after[tag] = after.get(tag, 0.0) + trade_cash_out
    pct = (
        run_context.config.factor_exposure_bull_pct
        if run_context.market_regime.label == "BULL"
        else run_context.config.factor_exposure_neutral_pct
    )
    cap = max(0.0, run_context.market_regime.max_total_exposure * pct)
    limit_exceeded = initial_action == "BUY_SIMULATED" and any(after.get(tag, 0.0) > cap for tag in factor_tags)
    warnings = ["Factor/theme exposure limit would be exceeded."] if limit_exceeded else []
    return {
        "before": {key: round(value, 2) for key, value in sorted(before.items())},
        "after": {key: round(value, 2) for key, value in sorted(after.items())},
        "cap": round(cap, 2),
        "limit_exceeded": limit_exceeded,
        "warnings": warnings,
    }


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
