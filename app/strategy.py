from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.agent_risk import build_agent_run_context, evaluate_agent_candidate
from app.smart_universe import base_universe, build_sector_health


@dataclass
class StrategyDecision:
    action: str
    feedback: str
    quantity: int = 0
    cash_out_ils: float = 0.0
    cash_in_ils: float = 0.0
    risk_ils: float = 0.0
    execution_price: float | None = None
    decision_json: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyCandidate:
    ticker: str
    setup_type: str
    score: float
    current_price: float
    buy_zone_low: float | None
    buy_zone_high: float | None
    stop_loss: float | None
    target_1: float | None
    target_2: float | None
    risk_reward: float


def normalize_strategy_candidate(result: Any) -> StrategyCandidate:
    buy_zone_low = getattr(result, "buy_zone_low", None)
    buy_zone_high = getattr(result, "buy_zone_high", None)
    if buy_zone_low is None and hasattr(result, "buy_zone"):
        buy_zone = getattr(result, "buy_zone") or (None, None)
        if len(buy_zone) >= 2:
            buy_zone_low, buy_zone_high = buy_zone[0], buy_zone[1]

    return StrategyCandidate(
        ticker=str(getattr(result, "ticker", "")).upper(),
        setup_type=str(getattr(result, "setup_type", "") or ""),
        score=float(getattr(result, "score", 0) or 0),
        current_price=float(getattr(result, "current_price", 0) or 0),
        buy_zone_low=to_optional_float(buy_zone_low),
        buy_zone_high=to_optional_float(buy_zone_high),
        stop_loss=to_optional_float(getattr(result, "stop_loss", None)),
        target_1=to_optional_float(getattr(result, "target_1", None)),
        target_2=to_optional_float(getattr(result, "target_2", None)),
        risk_reward=float(getattr(result, "risk_reward", 0) or 0),
    )


def apply_strategy_decisions(
    results: list[Any],
    *,
    analysis_period: str,
    min_rr: float,
    starting_capital: float = 100_000.0,
    max_position: float | None = None,
    max_total_exposure: float | None = None,
    max_risk: float | None = None,
    open_positions: dict[str, dict[str, Any]] | None = None,
    cash: float | None = None,
    exposure: float = 0.0,
    currency_rate: float = 1.0,
    recent_stop_events: dict[str, dict[str, Any]] | None = None,
) -> list[Any]:
    max_position = max_position if max_position is not None else starting_capital * 0.10
    max_total_exposure = max_total_exposure if max_total_exposure is not None else starting_capital * 0.40
    max_risk = max_risk if max_risk is not None else starting_capital * 0.01
    open_positions = open_positions or {}
    cash = starting_capital if cash is None else cash
    sector_map = base_universe()
    run_context = build_agent_run_context(
        analysis_period=analysis_period,
        starting_capital=starting_capital,
        default_max_total_exposure=max_total_exposure,
        max_position=max_position,
    )
    sector_health = run_context.sector_health or build_sector_health(analysis_period)
    # Preliminary sizing must use the same exposure ceiling as the final risk gate.
    max_total_exposure = run_context.market_regime.max_total_exposure
    timestamp = datetime.now().isoformat(timespec="seconds")
    neutral_pilot_trades_today = 0

    enriched = [None] * len(results)
    running_cash = cash
    running_exposure = exposure
    simulated_positions = {ticker: dict(position) for ticker, position in open_positions.items()}
    for result_index in allocation_order_indices(results, simulated_positions):
        result = results[result_index]
        candidate = normalize_strategy_candidate(result)
        decision = decide_strategy_candidate(
            candidate,
            open_positions=simulated_positions,
            cash=running_cash,
            exposure=running_exposure,
            currency_rate=currency_rate,
            max_position=max_position,
            max_total_exposure=max_total_exposure,
            max_risk=max_risk,
            min_rr=min_rr,
            sector_map=sector_map,
            sector_health=sector_health,
        )
        decision_json = evaluate_agent_candidate(
            timestamp=timestamp,
            result=candidate,
            initial_action=decision.action,
            initial_reason=decision.feedback,
            quantity=decision.quantity,
            cash_out=decision.cash_out_ils,
            risk_amount=decision.risk_ils,
            cash_available=running_cash,
            portfolio_exposure_before=running_exposure,
            portfolio_open_risk_before=sum(float(p.get("risk_ils") or 0) for p in simulated_positions.values()),
            open_positions=simulated_positions,
            sector_map=sector_map,
            run_context=run_context,
            recent_stop_events=recent_stop_events or {},
            neutral_pilot_trades_today=neutral_pilot_trades_today,
        )
        final_action = str(decision_json.get("final_action") or decision.action)
        final_reason = str(decision_json.get("reason") or decision.feedback)
        decision.decision_json = decision_json
        decision.action = final_action
        decision.feedback = final_reason
        enriched[result_index] = enrich_result_with_strategy(result, final_action, final_reason, decision_json)
        if final_action == "BUY_SIMULATED":
            if decision_json.get("entry_mode") == "neutral_pilot":
                neutral_pilot_trades_today += 1
            running_cash -= float(decision_json.get("adjusted_cash_out") or decision.cash_out_ils or 0)
            running_exposure += float(decision_json.get("adjusted_cash_out") or decision.cash_out_ils or 0)
            simulated_positions[candidate.ticker] = {
                "ticker": candidate.ticker,
                "entry_price": candidate.current_price,
                "quantity": int(decision_json.get("position_size") or decision.quantity or 0),
                "stop_loss": candidate.stop_loss or 0,
                "target_1": candidate.target_1 or 0,
                "target_2": candidate.target_2 or 0,
                "exposure_ils": float(decision_json.get("adjusted_cash_out") or decision.cash_out_ils or 0),
                "risk_ils": float(decision_json.get("adjusted_risk_amount") or decision.risk_ils or 0),
            }
        elif final_action in {"TAKE_PARTIAL_PROFIT", "TAKE_PROFIT", "EXIT_STOP"}:
            cash_delta, exposure_delta = apply_strategy_exit(
                simulated_positions, candidate, decision, currency_rate
            )
            running_cash += cash_delta
            running_exposure += exposure_delta
        elif candidate.ticker in simulated_positions:
            running_exposure += refresh_strategy_position(
                simulated_positions[candidate.ticker], candidate.current_price, currency_rate
            )
    return enriched


def allocation_order_indices(results: list[Any], open_positions: dict[str, Any]) -> list[int]:
    """Existing agent ordering, shared without changing the caller's display order."""
    def priority(index: int) -> tuple[Any, ...]:
        candidate = results[index]
        return (
            0 if str(candidate.ticker).upper() in open_positions else 1,
            0 if str(candidate.setup_type or "").lower() != "no trade" else 1,
            -float(candidate.score or 0.0),
            -float(candidate.risk_reward or 0.0),
            index,
        )
    return sorted(range(len(results)), key=priority)


def refresh_strategy_position(position: dict[str, Any], price: float, currency_rate: float) -> float:
    """Mark holdings consistently and return the delta to the caller's exposure total."""
    old_exposure = float(position.get("exposure_ils") or 0)
    quantity = int(position.get("quantity") or 0)
    entry = float(position.get("entry_price") or 0)
    stop = float(position.get("stop_loss") or 0)
    if (not all(math.isfinite(v) for v in (price, currency_rate, old_exposure, entry, stop))
            or price <= 0 or currency_rate <= 0 or quantity <= 0 or old_exposure < 0):
        raise ValueError("Cannot mark position from invalid price, quantity, or accounting values")
    exposure = round(price * quantity * currency_rate, 2)
    position.update(current_price=price, exposure_ils=exposure,
                    unrealized_usd=round((price - entry) * quantity, 2),
                    unrealized_ils=round((price - entry) * quantity * currency_rate, 2),
                    risk_ils=round(max(0.0, price - stop) * quantity * currency_rate, 2))
    return exposure - old_exposure


def apply_strategy_exit(
    positions: dict[str, dict[str, Any]],
    result: Any,
    decision: StrategyDecision,
    currency_rate: float,
) -> tuple[float, float]:
    """Apply one exit to in-memory holdings and return cash/exposure deltas."""
    pos = positions.get(result.ticker)
    partial = decision.action == "TAKE_PARTIAL_PROFIT"
    if (not pos or decision.action not in {"TAKE_PARTIAL_PROFIT", "TAKE_PROFIT", "EXIT_STOP"}
            or not 0 < decision.quantity <= int(pos.get("quantity") or 0)
            or (partial and pos.get("partial_taken"))):
        raise ValueError("Exit does not match an open position; no portfolio change applied")
    remaining = int(pos["quantity"]) - decision.quantity
    if not partial and remaining:
        raise ValueError("Full exit must close the remaining quantity")
    old_exposure = float(pos.get("exposure_ils") or 0)
    entry = float(pos.get("entry_price") or 0)
    price = float(result.current_price)
    if (not all(math.isfinite(value) for value in (old_exposure, entry, price, currency_rate, decision.cash_in_ils))
            or old_exposure < 0 or price <= 0 or currency_rate <= 0 or decision.cash_in_ils <= 0
            or (remaining and entry <= 0)):
        raise ValueError("Invalid exit accounting inputs; no portfolio change applied")
    if remaining == 0:
        del positions[result.ticker]
        return decision.cash_in_ils, -old_exposure
    pos["quantity"] = remaining
    pos["partial_taken"] = True
    pos["stop_loss"] = entry
    pos["current_price"] = result.current_price
    pos["notes"] = "Partial profit taken; stop moved to breakeven."
    pos["exposure_ils"] = round(remaining * result.current_price * currency_rate, 2)
    pos["risk_ils"] = round(max(0.0, result.current_price - pos["stop_loss"]) * remaining * currency_rate, 2)
    return decision.cash_in_ils, pos["exposure_ils"] - old_exposure


def decide_strategy_candidate(
    result: StrategyCandidate,
    *,
    open_positions: dict[str, dict[str, Any]],
    cash: float,
    exposure: float,
    currency_rate: float,
    max_position: float,
    max_total_exposure: float,
    max_risk: float,
    min_rr: float,
    sector_map: dict[str, str],
    sector_health: dict[str, dict[str, Any]],
) -> StrategyDecision:
    existing = open_positions.get(result.ticker)
    if existing:
        quantity = int(existing.get("quantity") or 0)
        if result.current_price <= float(existing["stop_loss"]):
            exit_price = float(existing["stop_loss"])
            return StrategyDecision(
                "EXIT_STOP",
                "Current price reached stop loss.",
                quantity=quantity,
                cash_in_ils=round(quantity * exit_price * currency_rate, 2),
                execution_price=exit_price,
            )
        target_1 = float(existing.get("target_1") or 0)
        target_2 = float(existing.get("target_2") or 0)
        if target_2 > 0 and result.current_price >= target_2:
            exit_price = target_2
            return StrategyDecision(
                "TAKE_PROFIT",
                "Target 2 reached; close remaining simulated position.",
                quantity=quantity,
                cash_in_ils=round(quantity * exit_price * currency_rate, 2),
                execution_price=exit_price,
            )
        if target_1 > 0 and result.current_price >= target_1 and not existing.get("partial_taken"):
            partial_qty = max(1, quantity // 2)
            exit_price = target_1
            return StrategyDecision(
                "TAKE_PARTIAL_PROFIT",
                "Target 1 reached; take partial simulated profit and move stop to breakeven.",
                quantity=partial_qty,
                cash_in_ils=round(partial_qty * exit_price * currency_rate, 2),
                execution_price=exit_price,
            )
        return StrategyDecision("HOLD", "Existing simulated position remains open.")

    if result.setup_type.upper().replace(" ", "_") in {"NO_TRADE", "NO-TRADE"} or result.setup_type == "No Trade":
        return StrategyDecision("SKIP", "No Trade result.")
    sector = sector_map.get(result.ticker)
    health = sector_health.get(sector or "")
    if health and health.get("label") == "Weak":
        return StrategyDecision(
            "SKIP",
            f"{sector} sector regime is weak ({float(health.get('score', 0)):.0f}/100); skip new entry.",
        )
    if result.risk_reward < min_rr:
        return StrategyDecision(
            "WATCH",
            (
                f"Technical setup detected, but weighted risk/reward {result.risk_reward:.2f} "
                f"is below minimum {min_rr:.2f}."
            ),
        )
    if result.buy_zone_low is None or result.buy_zone_high is None:
        return StrategyDecision("SKIP", "Buy zone missing.")
    if not result.stop_loss or result.stop_loss <= 0:
        return StrategyDecision("SKIP", "Stop loss missing.")
    if not result.target_1 or not result.target_2:
        return StrategyDecision("SKIP", "Targets missing.")
    if not (result.buy_zone_low <= result.current_price <= result.buy_zone_high):
        return StrategyDecision("WATCH", "Valid setup, but price is not inside the buy zone.")

    risk_per_share = (result.current_price - result.stop_loss) * currency_rate
    if risk_per_share <= 0:
        return StrategyDecision("SKIP", "Risk cannot be calculated safely.")
    price = result.current_price * currency_rate
    remaining_exposure = max_total_exposure - exposure
    max_cash_for_trade = min(max_position, max_risk / risk_per_share * price, cash, remaining_exposure)
    quantity = math.floor(max_cash_for_trade / price)
    if quantity <= 0:
        return StrategyDecision("SKIP", "Position size blocked by cash, exposure, or risk limits.")
    cash_out = quantity * price
    risk_amount = quantity * risk_per_share
    return StrategyDecision(
        "BUY_SIMULATED",
        "Price is inside buy zone, R/R is valid, and simulated risk limits allow entry.",
        quantity=quantity,
        cash_out_ils=round(cash_out, 2),
        risk_ils=round(risk_amount, 2),
    )


def enrich_result_with_strategy(result: Any, action: str, reason: str, decision_json: dict[str, Any]) -> Any:
    update = {
        "strategy_action": action,
        "strategy_reason": reason,
        "strategy_decision": decision_json,
    }
    if hasattr(result, "model_copy"):
        return result.model_copy(update=update)
    for key, value in update.items():
        setattr(result, key, value)
    return result


def to_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
