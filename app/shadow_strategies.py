from __future__ import annotations

from typing import Any, Callable

VERSION = "shadow_v1"
STRATEGY_NAMES = (
    "BREAKOUT_CONTINUATION",
    "TREND_PULLBACK_RECLAIM",
    "VWAP_RECLAIM",
    "RELATIVE_STRENGTH_LEADER",
    "STOP_RECLAIM_REENTRY",
)


def evaluate_shadow_strategies(result: Any, decision_json: dict[str, Any]) -> list[dict[str, Any]]:
    strategies: list[tuple[str, Callable[[Any, dict[str, Any]], dict[str, Any]]]] = [
        ("BREAKOUT_CONTINUATION", breakout_continuation),
        ("TREND_PULLBACK_RECLAIM", trend_pullback_reclaim),
        ("VWAP_RECLAIM", vwap_reclaim),
        ("RELATIVE_STRENGTH_LEADER", relative_strength_leader),
        ("STOP_RECLAIM_REENTRY", stop_reclaim_reentry),
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
        ("market regime is not BEAR", market_ok(decision)),
        ("sector regime is not WEAK", sector_ok(decision)),
        ("breakout/retest setup detected", "BREAKOUT" in setup_type.upper()),
        ("entry confirmation passed", bool(decision.get("entry_confirmation_passed"))),
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
        ("market regime is not BEAR", market_ok(decision)),
        ("sector regime is not WEAK", sector_ok(decision)),
        ("pullback/reclaim setup detected", pullback_setup),
        ("setup score is at least 0.40", number(decision.get("setup_score")) >= 0.40),
        ("reclaim or completed confirmation exists", confirmation),
        ("primary net R/R is acceptable", number(decision.get("net_rr_1")) >= 0.8),
        ("earnings blackout is not active", not bool(decision.get("earnings_blackout"))),
    ]
    return finalize_record(record, decision, checks, "Trend pullback reclaim")


def vwap_reclaim(result: Any, decision: dict[str, Any]) -> dict[str, Any]:
    record = base_record("VWAP_RECLAIM")
    setup_type = text(decision.get("setup_type") or getattr(result, "setup_type", ""))
    checks = [
        ("market regime is not BEAR", market_ok(decision)),
        ("sector regime is not WEAK", sector_ok(decision)),
        ("VWAP setup detected", "VWAP" in setup_type.upper()),
        ("VWAP reclaim or entry confirmation passed", bool(decision.get("vwap_reclaimed") or decision.get("entry_confirmation_passed"))),
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
        ("market regime is not BEAR", market_ok(decision)),
        ("sector is strong or improving", sector_score >= 65 and sector_ok(decision)),
        ("relative quality score is strong", quality_score >= 65 or momentum_score >= 65),
        ("technical setup exists", setup_type and setup_type.upper() != "NO TRADE"),
        ("not blocked by earnings blackout", not bool(decision.get("earnings_blackout"))),
        ("primary net R/R is acceptable", number(decision.get("net_rr_1")) >= 0.8),
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
    checks = [
        ("a recent stop-loss event exists", bool(last_stop_date)),
        ("market regime is not BEAR", market_ok(decision)),
        ("sector regime is not WEAK", sector_ok(decision)),
        ("completed candle reclaimed the trigger", reclaimed),
        ("setup score is at least 0.60", number(decision.get("setup_score")) >= 0.60),
        ("primary net R/R is at least 1.20", number(decision.get("net_rr_1")) >= 1.20),
        ("earnings blackout is not active", not bool(decision.get("earnings_blackout"))),
    ]
    record = finalize_record(record, decision, checks, "Stop reclaim re-entry")
    record["last_stop_date"] = last_stop_date or None
    record["trigger_level"] = trigger_level
    record["active_cooldown_preserved"] = bool(decision.get("cooldown_active"))
    return record


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
    return record


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
