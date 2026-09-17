"""Select a fully evaluated candidate without mutating portfolio state."""
from __future__ import annotations

from dataclasses import is_dataclass, replace
from math import isfinite
from typing import Any, Callable

from app.execution import STRATEGY_VERSION


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
    evaluated = [(candidate, *evaluate(candidate)) for candidate in candidates]
    eligible = [item for item in evaluated if not existing and item[2].get("final_action") == "BUY_SIMULATED"]
    selected = max(eligible, key=lambda item: (
        float(item[0].score), float(item[2].get("net_rr_1") or 0),
        float(item[2].get("weighted_net_rr") or item[2].get("net_rr") or 0),
        item[0].setup_type,
    )) if eligible else evaluated[0]
    candidate, decision, evidence = selected
    alert_candidates = [item for item in evaluated
                        if item[2].get("setup_alert_evidence", {}).get("eligible") is True]
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
            "selected": item[0] is candidate,
        } for item in evaluated],
    )
    return candidate, decision, evidence
