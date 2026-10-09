from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

from app.decision_quality_analysis import simulate_fixed_plan
from app.setup_selection_replay import exchange_session_identity, parse_timestamp


EXPERIMENT_NAME = "WEAK_SECTOR_OVERRIDE_V1"
EXPERIMENT_VERSION = "weak_sector_override_v1"
REVIEW_REQUIREMENTS = {
    "signals": 50,
    "closed_outcomes": 30,
    "trading_days": 20,
    "sectors": 3,
}
SECTOR_BLOCKER_TEXT = "Sector regime is WEAK; do not auto-buy weak sector setups."
COUNTERFACTUAL_MEASUREMENT_VERSION = "sector_gate_counterfactual_v2"


def _seal_observation(observation: dict[str, Any]) -> dict[str, Any]:
    observation.pop("snapshot_sha256", None)
    observation["snapshot_sha256"] = hashlib.sha256(
        json.dumps(observation, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return observation


def measure_sector_gate_counterfactual(
    *, result: Any, active_record: dict[str, Any], open_positions: dict[str, Any],
    cash_available: float, portfolio_exposure_before: float, max_position: float,
    max_total_exposure: float, max_risk: float, base_min_rr: float, currency_rate: float,
    sector_map: dict[str, str], sector_health: dict[str, Any], run_context: Any,
    portfolio_open_risk_before: float, recent_stop_events: dict[str, Any],
    neutral_pilot_trades_today: int,
) -> dict[str, Any]:
    """Measure the selected setup without executing or changing the active decision.

    Only the preliminary sector short-circuit is bypassed. The full risk layer
    still sees WEAK and retains its sector blocker and every other policy.
    """
    if (active_record.get("sector_regime") != "WEAK"
            or active_record.get("market_session_phase") != "REGULAR"
            or result.ticker in open_positions
            or str(result.setup_type).lower() == "no trade"):
        return evaluate_weak_sector_override_v1(active_record)

    from app.agent_risk import evaluate_agent_candidate
    from app.strategy import decide_strategy_candidate, normalize_strategy_candidate

    try:
        preliminary_health = deepcopy(sector_health)
        sector = sector_map.get(result.ticker, "Unknown")
        preliminary_health.setdefault(sector, {})["label"] = "Neutral"
        portfolio = deepcopy(open_positions)
        context = deepcopy(run_context)
        base = decide_strategy_candidate(
            normalize_strategy_candidate(result), open_positions=portfolio,
            cash=cash_available, exposure=portfolio_exposure_before,
            currency_rate=currency_rate, max_position=max_position,
            max_total_exposure=max_total_exposure, max_risk=max_risk,
            min_rr=base_min_rr, sector_map=sector_map, sector_health=preliminary_health,
        )
        measured = evaluate_agent_candidate(
            timestamp=active_record["timestamp"], result=deepcopy(result),
            initial_action=base.action, initial_reason=base.feedback,
            quantity=base.quantity, cash_out=base.cash_out_ils, risk_amount=base.risk_ils,
            cash_available=cash_available, portfolio_exposure_before=portfolio_exposure_before,
            open_positions=portfolio, sector_map=sector_map, run_context=context,
            portfolio_open_risk_before=portfolio_open_risk_before,
            recent_stop_events=deepcopy(recent_stop_events),
            neutral_pilot_trades_today=neutral_pilot_trades_today,
            currency_rate=currency_rate, base_min_rr=base_min_rr,
        )
        measured["weighted_net_rr"] = measured.get("net_rr")
        measured["setup_score_bucket"] = active_record.get("setup_score_bucket")
        observation = evaluate_weak_sector_override_v1(measured)
        observation["counterfactual_decision_snapshot"] = observation["active_decision_snapshot"]
    except Exception as exc:
        observation = evaluate_weak_sector_override_v1(active_record)
        observation.update(status="UNASSESSABLE", signal_eligible=False, control_eligible=False)
        observation["ineligibility_reasons"].append(
            f"UNASSESSABLE:counterfactual_evaluation:{type(exc).__name__}"
        )
    observation["measurement_version"] = COUNTERFACTUAL_MEASUREMENT_VERSION
    observation["active_decision_snapshot"] = {
        key: active_record.get(key) for key in ("initial_action", "final_action", "reason")
    }
    observation["preliminary_sector_gate_bypassed"] = True
    observation["risk_sector_gate_retained"] = True
    return _seal_observation(observation)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _check(name: str, passed: bool | None, detail: str) -> dict[str, str]:
    return {
        "name": name,
        "status": "UNASSESSABLE" if passed is None else "PASS" if passed else "FAIL",
        "detail": detail,
    }


def _score_bucket(record: dict[str, Any]) -> str:
    persisted = str(record.get("setup_score_bucket") or "").strip()
    if persisted:
        return persisted
    score = _number(record.get("setup_score"))
    if score is None:
        return "UNKNOWN"
    lower = math.floor(score * 10) / 10
    return f"{lower:.1f}-{lower + 0.09:.2f}"


def _list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    return [str(item) for item in value]


def _explicit_gate_evidence(record: dict[str, Any], *, path: str) -> list[dict[str, str]]:
    regime = str(record.get("market_regime") or "").upper()
    sector_regime = str(record.get("sector_regime") or "").upper()
    phase = str(record.get("market_session_phase") or "").upper()
    can_open = record.get("market_session_can_open_new_buy")
    allows_buys = record.get("market_regime_allows_new_buys")
    score = _number(record.get("setup_score"))
    score_floor = _number(
        record.get("neutral_pilot_min_setup_score")
        if path == "NEUTRAL_PILOT"
        else record.get("minimum_setup_score_required")
    )
    quality = _number(record.get("normalized_quality_score"))
    quality_floor = 45.0 if regime == "NEUTRAL" else 35.0
    primary_rr = _number(record.get("net_rr_1"))
    primary_floor = _number(record.get("minimum_primary_net_rr_required"))
    weighted_rr = _number(record.get("weighted_net_rr"))
    if weighted_rr is None:
        weighted_rr = _number(record.get("net_rr"))
    weighted_floor = _number(
        record.get("neutral_pilot_min_net_rr")
        if path == "NEUTRAL_PILOT"
        else record.get("minimum_net_rr_required")
    )
    target_status = str(record.get("target_feasibility_status") or "").upper()
    confirmation = record.get("entry_confirmation_passed")
    freshness = str(record.get("confirmation_freshness_status") or "").upper()
    earnings = record.get("earnings_blackout")
    earnings_date = str(record.get("earnings_date") or "").strip()
    days_to_earnings = _integer(record.get("days_to_earnings"))
    cooldown = record.get("cooldown_active")
    cooldown_exception = record.get("cooldown_exception_used")
    correlation = record.get("correlation_warning")
    sector_exposure = record.get("sector_exposure_limit_exceeded")
    factor_exposure = record.get("factor_exposure_limit_exceeded")
    sector_before = _number(record.get("sector_exposure_before"))
    sector_after = _number(record.get("sector_exposure_after"))
    sector_cap = _number(record.get("sector_exposure_cap"))
    factor_before = record.get("factor_exposure_before")
    factor_after = record.get("factor_exposure_after")
    factor_cap = _number(record.get("factor_exposure_cap"))
    factor_tags = record.get("factor_tags")
    entry_blockers = _list(record.get("entry_gate_blockers"))
    capital_blockers = _list(record.get("capital_blockers"))
    quantity = _integer(record.get("adjusted_position_size"))
    cash_out = _number(record.get("adjusted_cash_out"))
    risk_amount = _number(record.get("adjusted_risk_amount"))
    cash_available = _number(record.get("cash_available"))
    exposure_before = _number(record.get("portfolio_exposure_before"))
    exposure_limit = _number(record.get("dynamic_exposure_limit"))
    heat_before = _number(record.get("portfolio_heat_before"))
    heat_cap = _number(record.get("portfolio_heat_cap"))
    entry = _number(record.get("entry_execution_price"))
    stop = _number(record.get("stop_loss"))
    target_1 = _number(record.get("target_1"))
    target_2 = _number(record.get("target_2"))
    cost_policy = record.get("execution_cost_policy")
    versions_present = bool(record.get("strategy_version") and record.get("execution_model_version"))

    checks = [
        _check(
            "base_candidate",
            None if record.get("initial_action") is None else record.get("initial_action") == "BUY_SIMULATED",
            f"initial_action={record.get('initial_action') or 'missing'}",
        ),
        _check(
            "regular_session",
            None if not phase or can_open is None else phase == "REGULAR" and can_open is True,
            f"phase={phase or 'missing'}; can_open={can_open}",
        ),
        _check(
            "market_regime",
            None if not regime or allows_buys is None else regime != "BEAR" and allows_buys is True,
            f"regime={regime or 'missing'}; allows_new_buys={allows_buys}",
        ),
        _check(
            "setup_score",
            None if score is None or score_floor is None else score >= score_floor,
            f"score={score}; floor={score_floor}; path={path}",
        ),
        _check(
            "normalized_quality",
            None if quality is None or not regime else quality >= quality_floor,
            f"quality={quality}; floor={quality_floor}",
        ),
        _check(
            "primary_rr",
            None if primary_rr is None or primary_floor is None else primary_rr >= primary_floor,
            f"net_rr_1={primary_rr}; floor={primary_floor}",
        ),
        _check(
            "weighted_net_rr",
            None if weighted_rr is None or weighted_floor is None else weighted_rr >= weighted_floor,
            f"weighted_net_rr={weighted_rr}; floor={weighted_floor}; path={path}",
        ),
        _check(
            "target_quality",
            None if not target_status else target_status == "OK",
            f"status={target_status or 'missing'}",
        ),
        _check(
            "entry_confirmation",
            None if confirmation is None else confirmation is True,
            f"passed={confirmation}",
        ),
        _check(
            "confirmation_freshness",
            None if not freshness else freshness == "FRESH_SAME_SESSION",
            f"freshness={freshness or 'missing'}",
        ),
        _check(
            "earnings",
            None
            if earnings is None or not earnings_date or days_to_earnings is None
            else earnings is False,
            f"blackout={earnings}; date={earnings_date or 'missing'}; days={days_to_earnings}",
        ),
        _check(
            "cooldown",
            None
            if cooldown is None
            else cooldown is False or cooldown_exception is True,
            f"active={cooldown}; exception={cooldown_exception}",
        ),
        _check(
            "correlation",
            None if correlation is None else correlation is False,
            f"warning={correlation}",
        ),
        _check(
            "sector_exposure",
            None
            if sector_exposure is None or None in {sector_before, sector_after, sector_cap}
            else sector_exposure is False,
            f"before={sector_before}; after={sector_after}; cap={sector_cap}; limit_exceeded={sector_exposure}",
        ),
        _check(
            "factor_exposure",
            None
            if factor_exposure is None
            or not isinstance(factor_before, dict)
            or not isinstance(factor_after, dict)
            or factor_cap is None
            or not isinstance(factor_tags, list)
            else factor_exposure is False,
            f"tags={factor_tags}; cap={factor_cap}; limit_exceeded={factor_exposure}",
        ),
        _check(
            "frozen_trade_plan",
            None
            if None in {entry, stop, target_1, target_2}
            or not isinstance(cost_policy, dict)
            or not {"half_spread_per_share", "slippage_per_share", "fee_per_share"}.issubset(cost_policy)
            else stop < entry < target_1 <= target_2,
            f"entry={entry}; stop={stop}; target_1={target_1}; target_2={target_2}; costs_recorded={isinstance(cost_policy, dict)}",
        ),
        _check(
            "policy_versions",
            versions_present,
            f"strategy={record.get('strategy_version')}; execution={record.get('execution_model_version')}",
        ),
        _check(
            "position_sizing",
            None
            if quantity is None or cash_out is None or risk_amount is None
            else quantity > 0 and cash_out > 0 and risk_amount > 0,
            f"quantity={quantity}; cash_out={cash_out}; risk={risk_amount}",
        ),
        _check(
            "cash_capacity",
            None if cash_available is None or cash_out is None else cash_available >= cash_out > 0,
            f"cash_available={cash_available}; cash_out={cash_out}",
        ),
        _check(
            "portfolio_exposure",
            None
            if exposure_before is None or exposure_limit is None or cash_out is None
            else exposure_before + cash_out <= exposure_limit,
            f"before={exposure_before}; trade={cash_out}; limit={exposure_limit}",
        ),
        _check(
            "portfolio_heat",
            None
            if heat_before is None or heat_cap is None or risk_amount is None
            else heat_before + risk_amount <= heat_cap,
            f"before={heat_before}; trade={risk_amount}; cap={heat_cap}",
        ),
    ]

    if path == "NEUTRAL_PILOT":
        pilot_enabled = record.get("neutral_pilot_enabled")
        trades_today = _integer(record.get("neutral_pilot_trades_today"))
        trade_limit = _integer(record.get("neutral_pilot_max_trades_per_day"))
        checks.extend(
            [
                _check(
                    "pilot_enabled",
                    None if pilot_enabled is None else pilot_enabled is True,
                    f"enabled={pilot_enabled}",
                ),
                _check(
                    "pilot_market_regime",
                    None if not regime else regime == "NEUTRAL",
                    f"regime={regime or 'missing'}",
                ),
                _check(
                    "pilot_daily_limit",
                    None
                    if trades_today is None or trade_limit is None
                    else trade_limit <= 0 or trades_today < trade_limit,
                    f"trades_today={trades_today}; limit={trade_limit}",
                ),
            ]
        )

    expected_entry_blockers = (
        [item for item in entry_blockers or [] if SECTOR_BLOCKER_TEXT not in item]
        if entry_blockers is not None
        else None
    )
    checks.append(
        _check(
            "no_other_persisted_entry_blocker",
            None if expected_entry_blockers is None else not expected_entry_blockers,
            "; ".join(expected_entry_blockers or []) or "No non-sector entry blocker persisted.",
        )
    )
    checks.append(
        _check(
            "no_capital_blocker",
            None if capital_blockers is None else not capital_blockers,
            "; ".join(capital_blockers or []) or "No capital blocker persisted.",
        )
    )
    checks.append(
        _check(
            "sector_regime",
            None if not sector_regime else sector_regime != "WEAK",
            f"recorded={sector_regime or 'missing'}",
        )
    )
    return checks


def evaluate_weak_sector_override_v1(record: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a read-only sector counterfactual from persisted active-path evidence."""
    original_action = record.get("final_action")
    original_reason = record.get("reason")
    session_date, session_group = exchange_session_identity(record)
    ticker = str(record.get("ticker") or "").upper().strip()
    setup_type = str(record.get("setup_type") or "").strip()
    path = str(record.get("entry_mode") or "").upper()
    if path not in {"STANDARD", "NEUTRAL_PILOT"}:
        path = "UNKNOWN"
    signal_id = "|".join([session_date, ticker, setup_type, path])
    applicable = bool(
        ticker
        and setup_type
        and setup_type.lower() != "no trade"
        and session_group == "REGULAR"
        and str(record.get("sector_regime") or "").upper() in {"WEAK", "STRONG"}
    )
    checks = _explicit_gate_evidence(record, path=path) if path != "UNKNOWN" else []
    sector_check = next((item for item in checks if item["name"] == "sector_regime"), None)
    non_sector = [item for item in checks if item["name"] != "sector_regime"]
    failed = [item["name"] for item in non_sector if item["status"] == "FAIL"]
    unassessable = [item["name"] for item in non_sector if item["status"] == "UNASSESSABLE"]
    sector = str(record.get("sector_regime") or "").upper()
    signal_eligible = bool(
        applicable
        and sector == "WEAK"
        and sector_check
        and sector_check["status"] == "FAIL"
        and not failed
        and not unassessable
    )
    control_eligible = bool(
        applicable
        and sector == "STRONG"
        and sector_check
        and sector_check["status"] == "PASS"
        and not failed
        and not unassessable
        and record.get("final_action") == "BUY_SIMULATED"
    )
    status = (
        "QUALIFYING_SIGNAL"
        if signal_eligible
        else "QUALIFYING_CONTROL"
        if control_eligible
        else "UNASSESSABLE"
        if unassessable or path == "UNKNOWN"
        else "INELIGIBLE"
    )
    reasons = []
    if not applicable:
        reasons.append("Observation is outside the regular-session WEAK/STRONG active-setup cohort.")
    if path == "UNKNOWN":
        reasons.append("Active entry path is missing or unsupported.")
    reasons.extend(f"FAIL:{name}" for name in failed)
    reasons.extend(f"UNASSESSABLE:{name}" for name in unassessable)
    if sector not in {"WEAK", "STRONG"}:
        reasons.append(f"Sector regime {sector or 'missing'} is not part of the experiment.")
    entry_blockers = _list(record.get("entry_gate_blockers"))
    if sector == "WEAK" and entry_blockers:
        if not any(SECTOR_BLOCKER_TEXT in item for item in entry_blockers):
            reasons.append("The persisted active decision did not identify the WEAK-sector gate.")
            signal_eligible = False
            status = "INELIGIBLE"
    observation = {
        "experiment": EXPERIMENT_NAME,
        "version": EXPERIMENT_VERSION,
        "measurement_version": "active_path_v1",
        "signal_id": signal_id,
        "timestamp": record.get("timestamp"),
        "market_session_timestamp": record.get("market_session_timestamp"),
        "session_date": session_date,
        "session_group": session_group,
        "ticker": ticker,
        "company_name": record.get("company_name"),
        "setup_type": setup_type,
        "entry_path": path,
        "cohort": "WEAK_SIGNAL" if sector == "WEAK" else "STRONG_CONTROL" if sector == "STRONG" else "OUT_OF_SCOPE",
        "applicable": applicable,
        "status": status,
        "signal_eligible": signal_eligible,
        "control_eligible": control_eligible,
        "ineligibility_reasons": reasons,
        "gate_evidence": checks,
        "comparison_stratum": {
            "setup_type": setup_type,
            "market_regime": record.get("market_regime"),
            "setup_score_bucket": _score_bucket(record),
        },
        "policy_snapshot": {
            "strategy_version": record.get("strategy_version"),
            "execution_model_version": record.get("execution_model_version"),
            "entry_path": path,
            "minimum_setup_score_required": record.get("minimum_setup_score_required"),
            "minimum_primary_net_rr_required": record.get("minimum_primary_net_rr_required"),
            "minimum_net_rr_required": record.get("minimum_net_rr_required"),
            "normalized_quality_floor": 45.0 if str(record.get("market_regime") or "").upper() == "NEUTRAL" else 35.0,
        },
        "trade_plan_snapshot": {
            "entry_signal_price": record.get("entry_signal_price"),
            "entry_execution_price": record.get("entry_execution_price"),
            "stop_loss": record.get("stop_loss"),
            "target_1": record.get("target_1"),
            "target_2": record.get("target_2"),
            "net_rr_1": record.get("net_rr_1"),
            "net_rr_2": record.get("net_rr_2"),
            "weighted_net_rr": record.get("weighted_net_rr") or record.get("net_rr"),
            "execution_cost_policy": record.get("execution_cost_policy"),
            "estimated_spread": record.get("estimated_spread"),
            "estimated_slippage": record.get("estimated_slippage"),
            "estimated_fees": record.get("estimated_fees"),
        },
        "sizing_snapshot": {
            "quantity": record.get("adjusted_position_size"),
            "cash_out": record.get("adjusted_cash_out"),
            "risk_amount": record.get("adjusted_risk_amount"),
            "cash_available": record.get("cash_available"),
            "portfolio_exposure_before": record.get("portfolio_exposure_before"),
            "portfolio_heat_before": record.get("portfolio_heat_before"),
        },
        "context_snapshot": {
            "market_regime": record.get("market_regime"),
            "sector": record.get("sector"),
            "sector_regime": record.get("sector_regime"),
            "sector_score": record.get("sector_score"),
            "setup_score": record.get("setup_score"),
            "normalized_quality_score": record.get("normalized_quality_score"),
        },
        "active_decision_snapshot": {
            "initial_action": record.get("initial_action"),
            "final_action": original_action,
            "reason": original_reason,
        },
        "measurement_only": True,
        "active_decision_changed": False,
        "portfolio_mutation_allowed": False,
    }
    _seal_observation(observation)
    assert record.get("final_action") == original_action
    assert record.get("reason") == original_reason
    return observation


def _record_observation(record: dict[str, Any]) -> dict[str, Any]:
    stored = record.get("weak_sector_override_v1")
    if isinstance(stored, dict):
        expected = deepcopy(stored)
        digest = expected.get("snapshot_sha256")
        valid_digest = _seal_observation(expected)["snapshot_sha256"] == digest
        if (valid_digest and stored.get("experiment") == EXPERIMENT_NAME
                and stored.get("ticker") == str(record.get("ticker") or "").upper().strip()
                and stored.get("timestamp") == record.get("timestamp")
                and stored.get("setup_type") == str(record.get("setup_type") or "").strip()
                and stored.get("active_decision_snapshot") == {
                    key: record.get(key) for key in ("initial_action", "final_action", "reason")
                }):
            return expected
    return evaluate_weak_sector_override_v1(record)


def persist_first_observations(
    records: list[dict[str, Any]], output_path: Path
) -> dict[str, int]:
    """Persist the first observation for each date/ticker/setup/path atomically."""
    existing: list[dict[str, Any]] = []
    known: set[tuple[str, str]] = set()
    if output_path.exists():
        for line_number, line in enumerate(output_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Malformed experiment JSONL at line {line_number}") from exc
            if not isinstance(item, dict) or not item.get("signal_id"):
                raise ValueError(f"Invalid experiment observation at line {line_number}")
            existing.append(item)
            known.add((str(item["signal_id"]), item.get("measurement_version", "active_path_v1")))

    evaluated = sorted(
        (_record_observation(item) for item in records),
        key=lambda item: parse_timestamp(item.get("timestamp")),
    )
    considered = [
        item
        for item in evaluated
        if item["applicable"] is True
    ]
    added = []
    for item in considered:
        identity = (item["signal_id"], item.get("measurement_version", "active_path_v1"))
        if identity in known:
            continue
        known.add(identity)
        added.append(item)
    if added:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(output_path.suffix + ".tmp")
        rows = existing + added
        temporary.write_text(
            "".join(json.dumps(item, sort_keys=True, default=str) + "\n" for item in rows),
            encoding="utf-8",
        )
        temporary.replace(output_path)
    return {
        "evaluated": len(evaluated),
        "considered": len(considered),
        "added": len(added),
        "signals_added": sum(item["signal_eligible"] for item in added),
        "controls_added": sum(item["control_eligible"] for item in added),
        "deduplicated": len(considered) - len(added),
    }


def load_observations(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    observations = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            if isinstance(item, dict):
                observations.append(item)
    return observations


def _outcome_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = [item.get("outcome") or {} for item in rows]
    closed = [item for item in outcomes if item.get("status") == "CLOSED"]
    censored = [item for item in outcomes if item.get("status") == "CENSORED_OPEN"]
    closed_r = [float(item["realized_r"]) for item in closed if item.get("realized_r") is not None]
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in closed_r:
        cumulative += value
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)
    return {
        "total": len(rows),
        "closed": len(closed),
        "censored_open": len(censored),
        "unassessable": sum(item.get("status") == "UNASSESSABLE" for item in outcomes),
        "closed_expectancy_r": round(mean(closed_r), 4) if closed_r else None,
        "closed_median_r": round(median(closed_r), 4) if closed_r else None,
        "closed_cumulative_r": round(sum(closed_r), 4) if closed_r else None,
        "closed_sequence_max_drawdown_r": round(max_drawdown, 4) if closed_r else None,
        "tp1_hit_rate": round(
            sum(any(event.get("action") == "TAKE_PARTIAL_PROFIT" for event in item.get("events") or []) for item in outcomes)
            / len(outcomes),
            4,
        )
        if outcomes
        else None,
        "stop_rate": round(
            sum(any(event.get("action") == "EXIT_STOP" for event in item.get("events") or []) for item in outcomes)
            / len(outcomes),
            4,
        )
        if outcomes
        else None,
        "average_mfe_r": round(mean(float(item["mfe_r"]) for item in outcomes if item.get("mfe_r") is not None), 4)
        if any(item.get("mfe_r") is not None for item in outcomes)
        else None,
        "average_mae_r": round(mean(float(item["mae_r"]) for item in outcomes if item.get("mae_r") is not None), 4)
        if any(item.get("mae_r") is not None for item in outcomes)
        else None,
    }


def build_measurement_summary(
    observations: list[dict[str, Any]], bars_by_ticker: dict[str, Any] | None = None
) -> dict[str, Any]:
    bars_by_ticker = bars_by_ticker or {}
    applicable_rows = [item for item in observations if _observation_applicable(item)]
    # A corrected measurement may follow an old blocked record for the same
    # opportunity. Keep history, but count that opportunity once in comparisons.
    latest = {}
    for item in sorted(applicable_rows, key=lambda row: (
        row.get("measurement_version") == COUNTERFACTUAL_MEASUREMENT_VERSION,
        parse_timestamp(row.get("timestamp")),
    )):
        latest[item.get("signal_id") or str(id(item))] = item
    applicable_observations = list(latest.values())
    signals = [item for item in applicable_observations if item.get("signal_eligible") is True]
    all_controls = [item for item in applicable_observations if item.get("control_eligible") is True]
    strata = {
        json.dumps(item.get("comparison_stratum") or {}, sort_keys=True)
        for item in signals
    }
    controls = [
        item
        for item in all_controls
        if json.dumps(item.get("comparison_stratum") or {}, sort_keys=True) in strata
    ]
    outcome_rows = []
    for item in sorted(signals + controls, key=lambda row: parse_timestamp(row.get("timestamp"))):
        plan = item.get("trade_plan_snapshot") or {}
        replay_record = {
            **item,
            **plan,
            "executable_entry": plan.get("entry_execution_price"),
            "execution_cost_policy": plan.get("execution_cost_policy"),
        }
        frame = bars_by_ticker.get(item.get("ticker"))
        outcome = (
            simulate_fixed_plan(replay_record, frame)
            if frame is not None
            else {"status": "UNASSESSABLE", "reason": "Market bars were not provided."}
        )
        outcome_rows.append({**item, "outcome": outcome})
    signal_rows = [item for item in outcome_rows if item.get("signal_eligible") is True]
    control_rows = [item for item in outcome_rows if item.get("control_eligible") is True]
    ineligible = Counter(
        reason
        for item in applicable_observations
        if item.get("cohort") == "WEAK_SIGNAL" and not item.get("signal_eligible")
        for reason in item.get("ineligibility_reasons") or [item.get("status") or "UNKNOWN"]
    )
    trading_days = len({item.get("session_date") for item in signals if item.get("session_date")})
    sectors = len(
        {
            (item.get("context_snapshot") or {}).get("sector")
            for item in signals
            if (item.get("context_snapshot") or {}).get("sector")
        }
    )
    closed_count = sum((item.get("outcome") or {}).get("status") == "CLOSED" for item in signal_rows)
    progress = {
        "signals": len(signals),
        "closed_outcomes": closed_count,
        "trading_days": trading_days,
        "sectors": sectors,
    }
    review_ready = all(progress[key] >= value for key, value in REVIEW_REQUIREMENTS.items())
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment": EXPERIMENT_NAME,
        "version": EXPERIMENT_VERSION,
        "mode": "READ_ONLY_SIGNAL_MEASUREMENT",
        "active_trading_logic_changed": False,
        "portfolio_return_claimed": False,
        "methodology": {
            "deduplication": "First regular-session observation per New York date/ticker/setup/active-entry-path.",
            "eligibility": "Every non-sector gate must explicitly PASS; missing or unchecked evidence is UNASSESSABLE.",
            "plan": "Entry, stop, targets, sizing, policy version and execution costs are frozen at signal time.",
            "outcomes": "Independent signal replay with stop-first same-bar ordering; results are not portfolio returns.",
            "comparison": "All eligible STRONG-sector controls with the same setup, market regime and pre-recorded setup-score bucket.",
            "measurement_revisions": "Corrected v2 measures preliminary sizing on an isolated copy while retaining the WEAK risk blocker. It supersedes an old measurement of the same opportunity, without rewriting history.",
        },
        "data_quality_warnings": [
            "Legacy weak-sector observations could stop before sizing and other gates were evaluated; their zero eligible signals are not evidence that no opportunity existed."
        ] if any(
            item.get("cohort") == "WEAK_SIGNAL"
            and item.get("measurement_version") != COUNTERFACTUAL_MEASUREMENT_VERSION
            for item in applicable_observations
        ) else [],
        "observation_count": len(observations),
        "applicable_observation_count": len(applicable_observations),
        "measurement_version_counts": dict(Counter(
            item.get("measurement_version", "active_path_v1") for item in applicable_rows
        )),
        "qualifying_signal_count": len(signals),
        "matched_control_count": len(controls),
        "weak_ineligibility_reasons": dict(ineligible.most_common()),
        "signal_results": _outcome_summary(signal_rows),
        "control_results": _outcome_summary(control_rows),
        "review_requirements": REVIEW_REQUIREMENTS,
        "review_progress": progress,
        "review_ready": review_ready,
        "review_interpretation": (
            "Minimum sample gates reached; this permits review only and does not prove edge or authorize activation."
            if review_ready
            else "Minimum sample gates have not been reached. Keep the active sector gate unchanged."
        ),
        "signals": signal_rows,
        "controls": control_rows,
    }


def _observation_applicable(item: dict[str, Any]) -> bool:
    """Accept explicit current evidence and conservatively infer legacy v1 rows."""
    if item.get("applicable") is not None:
        return item.get("applicable") is True
    setup_type = str(item.get("setup_type") or "").strip()
    return bool(
        setup_type
        and setup_type.lower() != "no trade"
        and str(item.get("session_group") or "").upper() == "REGULAR"
        and str(item.get("cohort") or "") in {"WEAK_SIGNAL", "STRONG_CONTROL"}
    )


def measurement_summary_markdown(summary: dict[str, Any]) -> str:
    progress = summary["review_progress"]
    requirements = summary["review_requirements"]
    return "\n".join(
        [
            "# WEAK_SECTOR_OVERRIDE_V1 Measurement",
            "",
            "Read-only signal experiment. It does not modify active decisions or the paper portfolio.",
            "",
            f"- Qualifying signals: {summary['qualifying_signal_count']}",
            f"- Matched STRONG-sector controls: {summary['matched_control_count']}",
            f"- Closed signal outcomes: {progress['closed_outcomes']}",
            f"- Trading days: {progress['trading_days']}",
            f"- Sectors: {progress['sectors']}",
            f"- Review ready: {summary['review_ready']}",
            "",
            "## Review gates",
            "",
            f"Signals {progress['signals']}/{requirements['signals']}; closed outcomes "
            f"{progress['closed_outcomes']}/{requirements['closed_outcomes']}; trading days "
            f"{progress['trading_days']}/{requirements['trading_days']}; sectors "
            f"{progress['sectors']}/{requirements['sectors']}.",
            "",
            "## Ineligibility",
            "",
            json.dumps(summary["weak_ineligibility_reasons"], sort_keys=True),
            "",
            "## Data quality",
            "",
            json.dumps(summary.get("data_quality_warnings", [])),
            "",
            "Signal outcomes and controls are independent measurements, not portfolio returns.",
            "Review readiness never activates the strategy automatically.",
            "",
        ]
    )


def write_measurement_summary(summary: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "weak_sector_override_v1_summary.json"
    markdown_path = output_dir / "weak_sector_override_v1_summary.md"
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    markdown_path.write_text(measurement_summary_markdown(summary), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}
