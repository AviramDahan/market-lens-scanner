"""Select a fully evaluated candidate without mutating portfolio state."""
from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from math import isfinite
import os
from typing import Any, Callable

from app.execution import STRATEGY_VERSION
from app.entry_policy import fibonacci_entries_enabled


def _pause_new_entry(candidate, decision, evidence, *, disabled, existing):
    if str(candidate.setup_type).strip().casefold() not in disabled:
        return decision, evidence
    evidence = dict(evidence)
    reason = (
        f"{candidate.setup_type} entries are paused after historical performance review; "
        "historical records are retained."
    )
    evidence["entry_strategy_policy"] = {
        "version": "manual_entry_pause_v1", "entry_enabled": False,
        "existing_position_management_unchanged": True,
        "pre_pause_final_action": evidence.get("final_action"),
        "pre_pause_reason": evidence.get("reason"), "reason": reason,
    }
    # A pause cannot close holdings, alter TP/SL, or interrupt their monitor.
    if existing or evidence.get("final_action") not in {"BUY_SIMULATED", "WATCH", "WATCH_READY", "SKIP"}:
        return decision, evidence
    evidence["entry_strategy_policy"]["pre_pause_staging"] = {
        name: evidence[name] for name in ("watch_status", "off_hours_candidate",
            "regular_session_confirmation_required", "off_hours_entry_policy", "off_hours_staging_reason")
        if name in evidence
    }
    evidence["entry_strategy_policy"]["pre_pause_sizing"] = {
        name: evidence[name] for name in ("position_size", "risk_amount", "adjusted_position_size",
            "adjusted_cash_out", "adjusted_risk_amount", "sector_exposure_after",
            "factor_exposure_after", "position_sizing_explanation", "technical_entry_gates_passed")
        if name in evidence
    }
    staging_warnings = [warning for warning in evidence.get("warnings") or []
                        if str(warning).upper().startswith("WATCH_READY:")]
    evidence["entry_strategy_policy"]["pre_pause_staging_warnings"] = staging_warnings
    evidence["warnings"] = [warning for warning in evidence.get("warnings") or [] if warning not in staging_warnings]
    evidence.update(
        final_action="SKIP", reason="SKIP: " + reason,
        entry_eligibility_status="ENTRY_GATES_BLOCKED", entry_qualified_before_capital=False,
        technical_entry_gates_passed=False, watch_status="SKIP", off_hours_candidate=False,
        regular_session_confirmation_required=False, off_hours_entry_policy="ENTRY_STRATEGY_PAUSED",
        off_hours_staging_reason="", watch_review_reason="",
        capital_blocked_only=False, position_size=0, risk_amount=0.0,
        adjusted_position_size=0, adjusted_cash_out=0.0, adjusted_risk_amount=0.0,
        position_size_adjusted=True, position_sizing_explanation="No new position: " + reason,
    )
    evidence["entry_gate_blockers"] = [*evidence.get("entry_gate_blockers", []), reason]
    for after, before in (("portfolio_exposure_after", "portfolio_exposure_before"),
                          ("portfolio_heat_after", "portfolio_heat_before"),
                          ("sector_exposure_after", "sector_exposure_before"),
                          ("factor_exposure_after", "factor_exposure_before")):
        if before in evidence:
            evidence[after] = dict(evidence[before]) if isinstance(evidence[before], dict) else evidence[before]
    if is_dataclass(decision):
        values = dict(action="SKIP", feedback="SKIP: " + reason, quantity=0,
                      cash_out_ils=0.0, risk_ils=0.0, decision_json={})
        names = {field.name for field in fields(decision)}
        decision = replace(decision, **{name: value for name, value in values.items() if name in names})
    return decision, evidence


def select_qualified_candidate(result: Any, evaluate: Callable, *, existing: bool = False):
    """evaluate returns (base decision, complete risk decision) and has no ledger writes."""
    candidates = [result]
    seen = set()
    for payload in getattr(result, "setup_candidates", None) or []:
        if not isinstance(payload, dict):
            continue
        name = str(payload.get("setup_type") or "")
        score = payload.get("professional_adjusted_score")
        if existing and name == result.setup_type:
            seen.add(name)
            continue
        if not name or name in seen or score is None:
            continue
        fields = {key: payload.get(key) for key in
                  ("buy_zone_low", "buy_zone_high", "stop_loss", "target_1", "target_2", "risk_reward")}
        values = [score, *fields.values()]
        if any(not isinstance(v, (int, float)) or not isfinite(v) for v in values):
            continue
        fields.update(setup_type=name, score=float(score))
        if hasattr(result, "reason"):
            fields["reason"] = str(payload.get("reason") or "")
        if hasattr(result, "chart_url"):
            fields["chart_url"] = ""  # A legacy chart has different stops and targets.
        if is_dataclass(result):
            candidate = replace(result, **fields)
        else:
            raise TypeError("Candidate selection requires a normalized dataclass")
        if name == result.setup_type:
            candidates[0] = candidate
        else:
            candidates.append(candidate)
        seen.add(name)
    disabled = frozenset(name.strip().casefold() for name in
                         os.getenv("MARKET_LENS_DISABLED_ENTRY_SETUPS", "").split(",") if name.strip())
    if not fibonacci_entries_enabled():
        disabled |= frozenset(str(candidate.setup_type).strip().casefold() for candidate in candidates
                              if "fib" in str(candidate.setup_type).casefold())
    evaluated = []
    for candidate in candidates:
        decision, evidence = evaluate(candidate)
        decision, evidence = _pause_new_entry(candidate, decision, evidence, disabled=disabled, existing=existing)
        evaluated.append((candidate, decision, evidence))
    eligible = [item for item in evaluated if not existing and item[2].get("final_action") == "BUY_SIMULATED"]
    selected = max(eligible, key=lambda item: (
        float(item[0].score), float(item[2].get("net_rr_1") or 0),
        float(item[2].get("weighted_net_rr") or item[2].get("net_rr") or 0),
        item[0].setup_type,
    )) if eligible else evaluated[0]
    candidate, decision, evidence = selected
    alert_candidates = [item for item in evaluated
                        if item[2].get("setup_alert_evidence", {}).get("eligible") is True
                        and str(item[0].setup_type).strip().casefold() not in disabled]
    alert = None
    if not eligible and alert_candidates:
        alert_candidate, _, alert_evidence = max(alert_candidates, key=lambda item: (
            float(item[0].score), float(item[2].get("net_rr_1") or 0),
            float(item[2].get("net_rr") or 0), item[0].setup_type))
        alert = {key: alert_evidence.get(key) for key in (
            "company_name", "net_entry", "net_rr", "net_rr_1", "net_rr_2",
            "market_regime", "sector_regime", "setup_alert_evidence", "reason")}
        alert.update({key: getattr(alert_candidate, key) for key in (
            "setup_type", "score", "current_price", "buy_zone_low", "buy_zone_high",
            "stop_loss", "target_1", "target_2", "risk_reward")})
        alert["setup_score"] = alert_candidate.score
        alert["existing_position"] = existing
        alert["chart_url"] = getattr(result, "chart_url", "") if alert_candidate.setup_type == result.setup_type and not existing else ""
    evidence["qualified_setup_alert"] = alert
    if disabled:
        evidence["disabled_entry_setups"] = sorted(disabled)
    if not existing:
        evidence["strategy_version"] = STRATEGY_VERSION
    evidence.update(
        active_setup_selection_policy="QUALIFIED_PROFESSIONAL_SCORE_V1" if not existing else "EXISTING_POSITION",
        selection_reason=("Highest professional score among fully qualified executable candidates; "
                          "ties use TP1 net R/R, weighted net R/R, then setup name."
                          if eligible else "No executable candidate; original diagnostic retained."),
        selection_candidates=[{
            "setup_type": item[0].setup_type, "professional_score": item[0].score,
            "final_action": item[2].get("final_action"), "reason": item[2].get("reason"),
            "net_rr_1": item[2].get("net_rr_1"), "net_rr": item[2].get("net_rr"),
            "entry_paused": str(item[0].setup_type).strip().casefold() in disabled,
            "selected": item[0] is candidate,
        } for item in evaluated],
    )
    return candidate, decision, evidence
