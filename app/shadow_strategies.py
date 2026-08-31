from __future__ import annotations

import os
from typing import Any, Callable

VERSION = "shadow_v2"
STRATEGY_NAMES = (
    "BREAKOUT_CONTINUATION",
    "TREND_PULLBACK_RECLAIM",
    "VWAP_RECLAIM",
    "RELATIVE_STRENGTH_LEADER",
    "STOP_RECLAIM_REENTRY",
    "FIB_STOP_075_ATR",
    "FIB_STOP_100_ATR",
    "FIB_STRUCTURE_STOP",
)


def evaluate_shadow_strategies(result: Any, decision_json: dict[str, Any]) -> list[dict[str, Any]]:
    strategies: list[tuple[str, Callable[[Any, dict[str, Any]], dict[str, Any]]]] = [
        ("BREAKOUT_CONTINUATION", breakout_continuation),
        ("TREND_PULLBACK_RECLAIM", trend_pullback_reclaim),
        ("VWAP_RECLAIM", vwap_reclaim),
        ("RELATIVE_STRENGTH_LEADER", relative_strength_leader),
        ("STOP_RECLAIM_REENTRY", stop_reclaim_reentry),
        ("FIB_STOP_075_ATR", lambda result, decision: fib_stop_variant(result, decision, 0.75)),
        ("FIB_STOP_100_ATR", lambda result, decision: fib_stop_variant(result, decision, 1.00)),
        ("FIB_STRUCTURE_STOP", fib_structure_stop),
    ]
    records = []
    for name, evaluator in strategies:
        try:
            record = evaluator(result, decision_json)
        except Exception as exc:
            record = base_record(name)
            record["reason"] = f"Shadow strategy failed safely: {exc}"
            record["warnings"].append("Shadow strategy exception; active decision was not affected.")
        records.append(apply_shadow_context(normalize_record(name, record), decision_json))
    return records


def breakout_continuation(result: Any, decision: dict[str, Any]) -> dict[str, Any]:
    record = base_record("BREAKOUT_CONTINUATION")
    setup_type = text(decision.get("setup_type") or getattr(result, "setup_type", ""))
    checks = [
        ("regular session is open", regular_session_ok(decision)),
        ("market regime is not BEAR", market_ok(decision)),
        ("sector regime is not WEAK", sector_ok(decision)),
        ("breakout/retest setup detected", "BREAKOUT" in setup_type.upper()),
        ("entry confirmation passed", bool(decision.get("entry_confirmation_passed"))),
        ("confirmation is fresh in the same session", confirmation_fresh(decision)),
        ("primary net R/R is acceptable", number(decision.get("net_rr_1")) >= 0.8),
        ("earnings blackout is not active", not bool(decision.get("earnings_blackout"))),
    ]
    return finalize_record(record, decision, checks, "Breakout continuation")


def trend_pullback_reclaim(result: Any, decision: dict[str, Any]) -> dict[str, Any]:
    record = base_record("TREND_PULLBACK_RECLAIM")
    setup_type = text(decision.get("setup_type") or getattr(result, "setup_type", ""))
    pullback_setup = any(token in setup_type.upper() for token in ("FIB", "SWING", "PULLBACK", "SUPPORT"))
    confirmation = bool(decision.get("entry_confirmation_passed") or decision.get("vwap_reclaimed") or decision.get("close_above_trigger"))
    checks = [
        ("regular session is open", regular_session_ok(decision)),
        ("market regime is not BEAR", market_ok(decision)),
        ("sector regime is not WEAK", sector_ok(decision)),
        ("pullback/reclaim setup detected", pullback_setup),
        ("setup score is at least 0.40", number(decision.get("setup_score")) >= 0.40),
        ("reclaim or completed confirmation exists", confirmation),
        ("confirmation is fresh in the same session", confirmation_fresh(decision)),
        ("primary net R/R is acceptable", number(decision.get("net_rr_1")) >= 0.8),
        ("earnings blackout is not active", not bool(decision.get("earnings_blackout"))),
    ]
    return finalize_record(record, decision, checks, "Trend pullback reclaim")


def vwap_reclaim(result: Any, decision: dict[str, Any]) -> dict[str, Any]:
    record = base_record("VWAP_RECLAIM")
    setup_type = text(decision.get("setup_type") or getattr(result, "setup_type", ""))
    checks = [
        ("regular session is open", regular_session_ok(decision)),
        ("market regime is not BEAR", market_ok(decision)),
        ("sector regime is not WEAK", sector_ok(decision)),
        ("VWAP setup detected", "VWAP" in setup_type.upper()),
        ("VWAP reclaim or entry confirmation passed", bool(decision.get("vwap_reclaimed") or decision.get("entry_confirmation_passed"))),
        ("confirmation is fresh in the same session", confirmation_fresh(decision)),
        ("primary net R/R is acceptable", number(decision.get("net_rr_1")) >= 0.8),
        ("target 1 is feasible versus ATR", target_feasible(decision)),
    ]
    return finalize_record(record, decision, checks, "VWAP reclaim")


def relative_strength_leader(result: Any, decision: dict[str, Any]) -> dict[str, Any]:
    record = base_record("RELATIVE_STRENGTH_LEADER")
    setup_type = text(decision.get("setup_type") or getattr(result, "setup_type", ""))
    sector_score = number(decision.get("sector_score"))
    quality_score = number(decision.get("normalized_quality_score"))
    momentum_score = number(decision.get("normalized_momentum_score"))
    checks = [
        ("regular session is open", regular_session_ok(decision)),
        ("market regime is not BEAR", market_ok(decision)),
        ("sector regime is STRONG", text(decision.get("sector_regime")).upper() == "STRONG" and sector_score >= 65),
        ("relative quality and momentum are strong", quality_score >= 65 and momentum_score >= 65),
        ("technical setup exists", setup_type and setup_type.upper() != "NO TRADE"),
        ("setup score is at least 0.45", number(decision.get("setup_score")) >= 0.45),
        ("fresh completed entry confirmation passed", bool(decision.get("entry_confirmation_passed")) and confirmation_fresh(decision)),
        ("not blocked by earnings blackout", not bool(decision.get("earnings_blackout"))),
        ("primary net R/R is at least 1.00", number(decision.get("net_rr_1")) >= 1.0),
        ("weighted net R/R meets the active threshold", number(decision.get("net_rr")) >= number(decision.get("minimum_net_rr_required"))),
    ]
    return finalize_record(record, decision, checks, "Relative strength leader")


def stop_reclaim_reentry(result: Any, decision: dict[str, Any]) -> dict[str, Any]:
    """Observe disciplined re-entry candidates after a recent stopped trade.

    This evaluator is intentionally shadow-only. It records whether a completed
    reclaim now passes the normal quality context, but never changes the active
    action or bypasses the production stop-loss cooldown.
    """
    record = base_record("STOP_RECLAIM_REENTRY")
    last_stop_date = text(decision.get("last_stop_date"))
    trigger_level = first_number(decision, "trigger_level", "executable_entry", "buy_zone_high")
    price = first_number(decision, "price", "executable_entry")
    reclaimed = bool(
        decision.get("entry_confirmation_passed")
        and decision.get("close_above_trigger")
        and price is not None
        and trigger_level is not None
        and price >= trigger_level
    )
    minimum_score = float(os.getenv("MARKET_LENS_SHADOW_REENTRY_MIN_SCORE", "0.50"))
    checks = [
        ("a recent stop-loss event exists", bool(last_stop_date)),
        ("regular session is open", regular_session_ok(decision)),
        ("market regime is not BEAR", market_ok(decision)),
        ("sector regime is STRONG", text(decision.get("sector_regime")).upper() == "STRONG"),
        ("completed candle reclaimed the trigger", reclaimed),
        ("confirmation is fresh in the same session", confirmation_fresh(decision)),
        (f"setup score is at least {minimum_score:.2f}", number(decision.get("setup_score")) >= minimum_score),
        ("primary net R/R is at least 1.20", number(decision.get("net_rr_1")) >= 1.20),
        ("earnings blackout is not active", not bool(decision.get("earnings_blackout"))),
    ]
    record = finalize_record(record, decision, checks, "Stop reclaim re-entry")
    record["last_stop_date"] = last_stop_date or None
    record["trigger_level"] = trigger_level
    record["active_cooldown_preserved"] = bool(decision.get("cooldown_active"))
    record["position_size_multiplier"] = 0.5
    record["max_reentries_per_stopped_trade"] = 1
    record["shadow_reentry_min_score"] = minimum_score
    return record


def fib_stop_variant(result: Any, decision: dict[str, Any], atr_multiple: float) -> dict[str, Any]:
    name = f"FIB_STOP_{int(round(atr_multiple * 100)):03d}_ATR"
    record = base_record(name)
    setup_type = text(decision.get("setup_type") or getattr(result, "setup_type", ""))
    entry = first_number(decision, "executable_entry", "price")
    target_1 = first_number(decision, "target_1")
    target_2 = first_number(decision, "target_2")
    atr = inferred_daily_atr(decision)
    stop = round(entry - atr_multiple * atr, 4) if entry and atr else None
    return finalize_fib_stop_record(
        record=record,
        decision=decision,
        setup_type=setup_type,
        entry=entry,
        stop=stop,
        target_1=target_1,
        target_2=target_2,
        label=f"Fib stop at {atr_multiple:.2f} ATR",
    )


def fib_structure_stop(result: Any, decision: dict[str, Any]) -> dict[str, Any]:
    record = base_record("FIB_STRUCTURE_STOP")
    setup_type = text(decision.get("setup_type") or getattr(result, "setup_type", ""))
    entry = first_number(decision, "executable_entry", "price")
    target_1 = first_number(decision, "target_1")
    target_2 = first_number(decision, "target_2")
    atr = inferred_daily_atr(decision)
    buy_zone_low = first_number(decision, "buy_zone_low")
    current_stop = first_number(decision, "stop_loss")
    structure_stop = round(buy_zone_low - 0.25 * atr, 4) if buy_zone_low and atr else current_stop
    stop = min(value for value in (current_stop, structure_stop) if value is not None) if any(
        value is not None for value in (current_stop, structure_stop)
    ) else None
    return finalize_fib_stop_record(
        record=record,
        decision=decision,
        setup_type=setup_type,
        entry=entry,
        stop=stop,
        target_1=target_1,
        target_2=target_2,
        label="Fib structure-buffer stop",
    )


def finalize_fib_stop_record(
    *,
    record: dict[str, Any],
    decision: dict[str, Any],
    setup_type: str,
    entry: float | None,
    stop: float | None,
    target_1: float | None,
    target_2: float | None,
    label: str,
) -> dict[str, Any]:
    risk = (entry - stop) if entry and stop else 0.0
    rr1 = (target_1 - entry) / risk if risk > 0 and target_1 and target_1 > entry else 0.0
    rr2 = (target_2 - entry) / risk if risk > 0 and target_2 and target_2 > entry else 0.0
    weighted_rr = 0.80 * rr1 + 0.20 * rr2
    checks = [
        ("Fib setup detected", "FIB" in setup_type.upper()),
        ("regular session is open", regular_session_ok(decision)),
        ("market regime is not BEAR", market_ok(decision)),
        ("sector regime is not WEAK", sector_ok(decision)),
        ("fresh completed entry confirmation passed", bool(decision.get("entry_confirmation_passed")) and confirmation_fresh(decision)),
        ("alternative stop can be calculated", risk > 0),
        ("alternative Target 1 R/R is at least 0.80", rr1 >= 0.80),
        ("alternative weighted R/R meets the active threshold", weighted_rr >= number(decision.get("minimum_net_rr_required"))),
        ("earnings blackout is not active", not bool(decision.get("earnings_blackout"))),
    ]
    record = finalize_record(record, decision, checks, label)
    record.update(
        {
            "entry_price": entry,
            "stop_loss": stop,
            "target_1": target_1,
            "target_2": target_2,
            "rr_to_target_1": round(rr1, 4),
            "rr_to_target_2": round(rr2, 4),
            "weighted_rr": round(weighted_rr, 4),
            "active_stop_loss": first_number(decision, "stop_loss"),
            "experiment_type": "STOP_DISTANCE_ONLY",
        }
    )
    active_entry = first_number(decision, "executable_entry", "price")
    active_stop = first_number(decision, "stop_loss")
    active_risk = (active_entry - active_stop) if active_entry and active_stop else 0.0
    record["position_size_multiplier"] = round(min(1.0, active_risk / risk), 4) if risk > 0 and active_risk > 0 else 0.0
    return record


def inferred_daily_atr(decision: dict[str, Any]) -> float | None:
    entry = first_number(decision, "executable_entry", "price")
    target_1 = first_number(decision, "target_1")
    distance = number(decision.get("target_1_atr_distance"))
    if entry is None or target_1 is None or distance <= 0 or target_1 <= entry:
        return None
    return round((target_1 - entry) / distance, 4)


def finalize_record(record: dict[str, Any], decision: dict[str, Any], checks: list[tuple[str, bool]], label: str) -> dict[str, Any]:
    passed = [name for name, ok in checks if ok]
    failed = [name for name, ok in checks if not ok]
    confidence = len(passed) / len(checks) if checks else 0.0
    record.update(plan_fields(decision))
    record["confidence"] = round(confidence, 4)
    record["would_buy"] = not failed
    if failed:
        record["reason"] = f"{label}: shadow buy blocked because {failed[0]}."
        record["warnings"] = [f"Missing: {item}" for item in failed[:4]]
    else:
        record["reason"] = f"{label}: all shadow conditions passed. Logged only; active decision unchanged."
    return record


def plan_fields(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_price": first_number(decision, "executable_entry", "entry", "price"),
        "stop_loss": first_number(decision, "stop_loss"),
        "target_1": first_number(decision, "target_1"),
        "target_2": first_number(decision, "target_2"),
        "rr_to_target_1": first_number(decision, "net_rr_1", "gross_rr_1"),
        "rr_to_target_2": first_number(decision, "net_rr_2", "gross_rr_2"),
    }


def base_record(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "version": VERSION,
        "would_buy": False,
        "confidence": 0.0,
        "entry_price": None,
        "stop_loss": None,
        "target_1": None,
        "target_2": None,
        "rr_to_target_1": None,
        "rr_to_target_2": None,
        "position_size_multiplier": 1.0,
        "reason": "",
        "warnings": [],
    }


def normalize_record(name: str, record: dict[str, Any]) -> dict[str, Any]:
    base = base_record(name)
    base.update(record or {})
    base["name"] = name
    base["version"] = str(base.get("version") or VERSION)
    base["would_buy"] = bool(base.get("would_buy"))
    base["confidence"] = round(max(0.0, min(1.0, number(base.get("confidence")))), 4)
    base["warnings"] = [str(item) for item in (base.get("warnings") or [])]
    base["reason"] = str(base.get("reason") or "No shadow reason provided.")
    return base


def apply_shadow_context(record: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    if record.get("would_buy") and text(decision.get("sector_regime")).upper() == "NEUTRAL":
        record["position_size_multiplier"] = 0.5
        record["warnings"].append("Shadow sizing suggestion: use 50% size in a NEUTRAL sector.")
        record["sector_policy_experiment"] = "NEUTRAL_HALF_SIZE"
    elif record.get("would_buy") and text(decision.get("sector_regime")).upper() == "STRONG":
        record["sector_policy_experiment"] = "STRONG_FULL_SIZE"
    return record


def regular_session_ok(decision: dict[str, Any]) -> bool:
    return text(decision.get("market_session_phase")).upper() == "REGULAR"


def confirmation_fresh(decision: dict[str, Any]) -> bool:
    return text(decision.get("confirmation_freshness_status")).upper() == "FRESH_SAME_SESSION"


def market_ok(decision: dict[str, Any]) -> bool:
    return text(decision.get("market_regime")).upper() != "BEAR"


def sector_ok(decision: dict[str, Any]) -> bool:
    return text(decision.get("sector_regime")).upper() != "WEAK"


def target_feasible(decision: dict[str, Any]) -> bool:
    status = text(decision.get("target_feasibility_status")).upper()
    distance = number(decision.get("target_1_atr_distance"))
    return status not in {"FAILED", "TOO_CLOSE"} and (distance == 0.0 or distance >= 0.75)


def first_number(source: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = source.get(key)
        if value is None or value == "":
            continue
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            continue
    return None


def number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def text(value: Any) -> str:
    return str(value or "").strip()
