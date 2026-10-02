from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from app.execution import EXECUTION_VERSION, sell_fill
from app.setup_selection_replay import exchange_session_identity, parse_timestamp
from app.shadow_strategies import evaluate_shadow_strategies


UNCHANGED_PLAN_SHADOWS = {
    "BREAKOUT_CONTINUATION",
    "TREND_PULLBACK_RECLAIM",
    "VWAP_RECLAIM",
    "RELATIVE_STRENGTH_LEADER",
}


def unique_weak_sector_opportunities(
    records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Keep the first regular-session active setup per ticker/setup/session date."""
    selected: dict[tuple[str, str, str], dict[str, Any]] = {}
    ordered = sorted(records, key=lambda item: parse_timestamp(item.get("timestamp")))
    for record in ordered:
        if str(record.get("sector_regime") or "").upper() != "WEAK":
            continue
        setup_type = str(record.get("setup_type") or "")
        if not setup_type or setup_type.lower() == "no trade":
            continue
        session_date, session_group = exchange_session_identity(record)
        if session_group != "REGULAR":
            continue
        ticker = str(record.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        selected.setdefault((session_date, ticker, setup_type), record)
    return list(selected.values())


def entry_path_assessment(record: dict[str, Any], *, path: str) -> dict[str, Any]:
    """Assess persisted gate evidence without treating missing evaluation as a pass."""
    path = path.upper()
    if path not in {"STANDARD", "NEUTRAL_PILOT"}:
        raise ValueError(f"Unsupported entry path: {path}")

    checks: list[dict[str, str]] = []

    def check(name: str, passed: bool | None, detail: str) -> None:
        checks.append(
            {
                "name": name,
                "status": "NOT_EVALUATED"
                if passed is None
                else "PASS"
                if passed
                else "FAIL",
                "detail": detail,
            }
        )

    phase = str(record.get("market_session_phase") or "").upper()
    can_open = record.get("market_session_can_open_new_buy")
    check(
        "regular_session",
        None
        if not phase and can_open is None
        else phase == "REGULAR" and can_open is not False,
        f"phase={phase or 'missing'}; can_open={can_open}",
    )

    regime = str(record.get("market_regime") or "").upper()
    regime_allows = record.get("market_regime_allows_new_buys")
    check(
        "market_regime",
        None if not regime else regime != "BEAR" and regime_allows is not False,
        f"regime={regime or 'missing'}; allows_new_buys={regime_allows}",
    )

    score = optional_float(record.get("setup_score"))
    score_floor = optional_float(
        record.get("neutral_pilot_min_setup_score")
        if path == "NEUTRAL_PILOT"
        else record.get("minimum_setup_score_required")
    )
    check(
        "setup_score",
        None if score is None or score_floor is None else score >= score_floor,
        f"score={score}; floor={score_floor}",
    )

    quality = optional_float(record.get("normalized_quality_score"))
    quality_floor = 45.0 if regime == "NEUTRAL" else 35.0
    check(
        "normalized_quality",
        None if quality is None else quality >= quality_floor,
        f"quality={quality}; floor={quality_floor}",
    )

    primary_rr = optional_float(record.get("net_rr_1"))
    check(
        "primary_rr",
        None if primary_rr is None else primary_rr >= 0.8,
        f"net_rr_1={primary_rr}; floor=0.8",
    )

    weighted_rr = optional_float(record.get("weighted_net_rr") or record.get("net_rr"))
    rr_floor = optional_float(
        record.get("neutral_pilot_min_net_rr")
        if path == "NEUTRAL_PILOT"
        else record.get("minimum_net_rr_required")
    )
    check(
        "weighted_net_rr",
        None if weighted_rr is None or rr_floor is None else weighted_rr >= rr_floor,
        f"weighted_net_rr={weighted_rr}; floor={rr_floor}",
    )

    confirmation = record.get("entry_confirmation_passed")
    check(
        "entry_confirmation",
        None if confirmation is None else bool(confirmation),
        f"passed={confirmation}",
    )
    freshness = str(record.get("confirmation_freshness_status") or "").upper()
    check(
        "confirmation_freshness",
        None if not freshness else freshness == "FRESH_SAME_SESSION",
        f"freshness={freshness or 'missing'}",
    )

    earnings = record.get("earnings_blackout")
    check(
        "earnings",
        None if earnings is None else not bool(earnings),
        f"blackout={earnings}",
    )
    target_status = str(record.get("target_feasibility_status") or "").upper()
    check(
        "target_quality",
        None if not target_status else target_status in {"OK", "UNKNOWN"},
        f"status={target_status or 'missing'}",
    )

    cooldown = record.get("cooldown_active")
    cooldown_exception = bool(record.get("cooldown_exception_used"))
    check(
        "cooldown",
        None if cooldown is None else not bool(cooldown) or cooldown_exception,
        f"active={cooldown}; exception={cooldown_exception}",
    )
    correlation = record.get("correlation_warning")
    check(
        "correlation",
        None if correlation is None else not bool(correlation),
        f"warning={correlation}",
    )

    if path == "NEUTRAL_PILOT":
        enabled = record.get("neutral_pilot_enabled")
        trades_today = optional_int(record.get("neutral_pilot_trades_today"))
        daily_limit = optional_int(record.get("neutral_pilot_max_trades_per_day"))
        check(
            "pilot_enabled",
            None if enabled is None else bool(enabled),
            f"enabled={enabled}",
        )
        check(
            "pilot_daily_limit",
            None
            if trades_today is None or daily_limit is None
            else daily_limit <= 0 or trades_today < daily_limit,
            f"trades_today={trades_today}; limit={daily_limit}",
        )

    check(
        "sector_regime",
        False,
        f"recorded={str(record.get('sector_regime') or 'missing').upper()}; counterfactual varies only this check",
    )

    sizing_evaluated = (
        record.get("entry_gate_evaluated") is True
        or int(optional_int(record.get("pre_cap_position_size")) or 0) > 0
    )
    impossible_reasons = capital_impossibility_reasons(record)
    check(
        "position_sizing",
        False if impossible_reasons else True if sizing_evaluated else None,
        "; ".join(impossible_reasons)
        if impossible_reasons
        else f"evaluated={sizing_evaluated}",
    )

    failed_non_sector = [
        item["name"]
        for item in checks
        if item["name"] != "sector_regime" and item["status"] == "FAIL"
    ]
    unknown_non_sector = [
        item["name"]
        for item in checks
        if item["name"] != "sector_regime" and item["status"] == "NOT_EVALUATED"
    ]
    classification = (
        "MULTI_FAIL"
        if failed_non_sector
        else "SECTOR_ONLY_UNASSESSABLE"
        if unknown_non_sector
        else "SECTOR_ONLY_FULLY_EVALUATED"
    )
    return {
        "path": path,
        "classification": classification,
        "checks": checks,
        "failed_non_sector": failed_non_sector,
        "not_evaluated_non_sector": unknown_non_sector,
    }


def capital_impossibility_reasons(record: dict[str, Any]) -> list[str]:
    reasons = []
    comparisons = (
        ("portfolio exposure", "portfolio_exposure_before", "dynamic_exposure_limit"),
        ("sector exposure", "sector_exposure_before", "sector_exposure_cap"),
        ("portfolio heat", "portfolio_heat_before", "portfolio_heat_cap"),
    )
    for label, value_key, cap_key in comparisons:
        value = optional_float(record.get(value_key))
        cap = optional_float(record.get(cap_key))
        if value is not None and cap is not None and cap > 0 and value >= cap:
            reasons.append(f"{label} already at/above cap ({value:.2f} >= {cap:.2f})")
    factor_cap = optional_float(record.get("factor_exposure_cap"))
    factor_before = record.get("factor_exposure_before")
    tags = record.get("factor_tags") or []
    if factor_cap is not None and factor_cap > 0 and isinstance(factor_before, dict):
        for tag in tags:
            value = optional_float(factor_before.get(tag))
            if value is not None and value >= factor_cap:
                reasons.append(
                    f"factor {tag} already at/above cap ({value:.2f} >= {factor_cap:.2f})"
                )
    return reasons


def sector_only_shadow_flips(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run the existing shadow evaluators twice, changing only WEAK to NEUTRAL."""
    output = []
    for record in unique_weak_sector_opportunities(records):
        result = SimpleNamespace(
            setup_type=record.get("setup_type"),
            score=record.get("setup_score"),
        )
        before = {
            item["name"]: item
            for item in evaluate_shadow_strategies(result, deepcopy(record))
        }
        changed = deepcopy(record)
        changed["sector_regime"] = "NEUTRAL"
        after = {
            item["name"]: item for item in evaluate_shadow_strategies(result, changed)
        }
        flipped = sorted(
            name
            for name in UNCHANGED_PLAN_SHADOWS
            if not before[name]["would_buy"] and after[name]["would_buy"]
        )
        if not flipped:
            continue
        session_date, _session_group = exchange_session_identity(record)
        output.append(
            {
                "signal_id": "|".join(
                    [
                        session_date,
                        str(record.get("ticker") or "").upper(),
                        str(record.get("setup_type") or ""),
                    ]
                ),
                "session_date": session_date,
                "ticker": str(record.get("ticker") or "").upper(),
                "setup_type": record.get("setup_type"),
                "timestamp": record.get("timestamp"),
                "market_session_timestamp": record.get("market_session_timestamp"),
                "shadow_strategies": flipped,
                "entry_price": after[flipped[0]]["entry_price"],
                "stop_loss": after[flipped[0]]["stop_loss"],
                "target_1": after[flipped[0]]["target_1"],
                "target_2": after[flipped[0]]["target_2"],
                "setup_score": record.get("setup_score"),
                "net_rr_1": record.get("net_rr_1"),
                "weighted_net_rr": record.get("weighted_net_rr")
                or record.get("net_rr"),
                "active_standard": entry_path_assessment(record, path="STANDARD"),
                "active_neutral_pilot": entry_path_assessment(
                    record, path="NEUTRAL_PILOT"
                ),
                "decision": record,
            }
        )
    return output


def simulate_fixed_plan(signal: dict[str, Any], bars: Any) -> dict[str, Any]:
    """Apply the production stop-first/TP1/TP2 policy to later regular-session bars.

    The result is an independent signal outcome, never a portfolio return.
    """
    record = signal.get("decision") or signal
    entry_reference = optional_float(
        signal.get("entry_price") or record.get("executable_entry")
    )
    stop = optional_float(signal.get("stop_loss") or record.get("stop_loss"))
    target_1 = optional_float(signal.get("target_1") or record.get("target_1"))
    target_2 = optional_float(signal.get("target_2") or record.get("target_2"))
    if None in {entry_reference, stop, target_1, target_2}:
        return {
            "status": "UNASSESSABLE",
            "reason": "Entry, stop, or targets are missing.",
        }

    policy = record.get("execution_cost_policy")
    if not isinstance(policy, dict):
        return {
            "status": "UNASSESSABLE",
            "reason": "Frozen execution-cost policy is missing.",
        }
    entry_fill = optional_float(record.get("entry_execution_price"))
    if entry_fill is None:
        entry_fill = entry_reference + float(record.get("entry_cost_per_share") or 0)
    position = {
        "decision_json": {
            "execution_model_version": EXECUTION_VERSION,
            "execution_cost_policy": policy,
        }
    }
    raw_signal_time = (
        signal.get("market_session_timestamp")
        or signal.get("timestamp")
        or record.get("market_session_timestamp")
        or record.get("timestamp")
    )
    try:
        signal_time = datetime.fromisoformat(
            str(raw_signal_time or "").strip().replace("Z", "+00:00")
        )
    except ValueError:
        return {"status": "UNASSESSABLE", "reason": "Signal timestamp is invalid."}
    if signal_time.tzinfo is None:
        return {"status": "UNASSESSABLE", "reason": "Signal timestamp has no timezone."}

    frame = bars.copy()
    frame = frame[frame.index > signal_time]
    frame = frame[
        (
            frame.index.tz_convert("America/New_York").time
            >= datetime.strptime("09:30", "%H:%M").time()
        )
        & (
            frame.index.tz_convert("America/New_York").time
            < datetime.strptime("16:00", "%H:%M").time()
        )
    ]
    if frame.empty:
        return {
            "status": "UNASSESSABLE",
            "reason": "No later regular-session bars are available.",
        }

    partial = False
    remaining = 1.0
    realized = 0.0
    events = []
    active_stop = stop
    highest_price = entry_fill
    lowest_price = entry_fill
    for index, row in frame.iterrows():
        high = float(row["High"])
        low = float(row["Low"])
        open_price = float(row["Open"])
        hit_stop = low <= active_stop
        hit_t2 = high >= target_2
        hit_t1 = not partial and high >= target_1
        if hit_stop:
            # The replay policy is stop-first inside an ambiguous bar. Do not
            # count a later unknown high/low as excursion after the exit.
            highest_price = max(highest_price, open_price)
            lowest_price = min(lowest_price, open_price, active_stop)
            fill, details = sell_fill(position, "EXIT_STOP", active_stop, open_price)
            realized += remaining * (fill - entry_fill)
            events.append(
                {"action": "EXIT_STOP", "timestamp": index.isoformat(), "fill": fill}
            )
            remaining = 0.0
            break
        if hit_t2:
            highest_price = max(highest_price, target_2)
            lowest_price = min(lowest_price, open_price)
            fill, details = sell_fill(position, "TAKE_PROFIT", target_2)
            realized += remaining * (fill - entry_fill)
            events.append(
                {"action": "TAKE_PROFIT", "timestamp": index.isoformat(), "fill": fill}
            )
            remaining = 0.0
            break
        if hit_t1:
            highest_price = max(highest_price, high)
            lowest_price = min(lowest_price, low)
            fill, details = sell_fill(position, "TAKE_PARTIAL_PROFIT", target_1)
            realized += 0.5 * (fill - entry_fill)
            remaining = 0.5
            partial = True
            active_stop = entry_fill
            events.append(
                {
                    "action": "TAKE_PARTIAL_PROFIT",
                    "timestamp": index.isoformat(),
                    "fill": fill,
                }
            )
        else:
            highest_price = max(highest_price, high)
            lowest_price = min(lowest_price, low)

    last_close = float(frame["Close"].iloc[-1])
    risk_per_share = entry_fill - sell_fill(position, "EXIT_STOP", stop)[0]
    marked_pnl = realized + remaining * (last_close - entry_fill)
    status = "CLOSED" if remaining == 0 else "CENSORED_OPEN"
    return {
        "status": status,
        "entry_fill": round(entry_fill, 4),
        "initial_stop": round(stop, 4),
        "target_1": round(target_1, 4),
        "target_2": round(target_2, 4),
        "events": events,
        "realized_per_share": round(realized, 4),
        "marked_per_share": round(marked_pnl, 4),
        "realized_r": round(realized / risk_per_share, 4)
        if risk_per_share > 0
        else None,
        "marked_r": round(marked_pnl / risk_per_share, 4)
        if risk_per_share > 0
        else None,
        "mfe_r": round((highest_price - entry_fill) / risk_per_share, 4)
        if risk_per_share > 0
        else None,
        "mae_r": round((entry_fill - lowest_price) / risk_per_share, 4)
        if risk_per_share > 0
        else None,
        "remaining_fraction": remaining,
        "last_bar": frame.index[-1].isoformat(),
        "last_close": round(last_close, 4),
        "cost_model": EXECUTION_VERSION,
    }


def optional_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def optional_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
