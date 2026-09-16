"""Versioned paper fills. Legacy lots retain their original accounting contract."""
from __future__ import annotations

import json
import math
from typing import Any

STRATEGY_VERSION = "qualified_selection_v1"
EXECUTION_VERSION = "costed_paper_v1"


def position_metadata(position: dict[str, Any]) -> dict[str, Any]:
    value = position.get("decision_json") or {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def sell_fill(position: dict[str, Any], action: str, trigger: float,
              observed_price: float | None = None) -> tuple[float, dict[str, Any]]:
    """All-in proceeds/share; targets are market-on-touch, not guaranteed limits.

    For bar stops observed_price is the open; quote-based stops use the quote.
    Cost assumptions are frozen on entry so restart cannot change a lot's model.
    """
    metadata = position_metadata(position)
    if metadata.get("execution_model_version") != EXECUTION_VERSION:
        return trigger, {}
    policy = metadata["execution_cost_policy"]
    if any(not math.isfinite(float(policy[key])) or float(policy[key]) < 0
           for key in ("slippage_per_share", "half_spread_per_share", "fee_per_share")):
        raise ValueError("Invalid simulated execution costs")
    if observed_price is not None and (not math.isfinite(observed_price) or observed_price <= 0):
        raise ValueError("Invalid observed exit price")
    reference = min(trigger, observed_price) if action == "EXIT_STOP" and observed_price is not None else trigger
    slip = float(policy["slippage_per_share"]) * (1.0 if action == "EXIT_STOP" else 0.5)
    cost = (float(policy["half_spread_per_share"]) + slip) + float(policy["fee_per_share"])
    price = round(reference - cost, 4)
    if not all(math.isfinite(v) and v > 0 for v in (trigger, reference, price)):
        raise ValueError("Invalid simulated exit fill")
    return price, {"execution_model_version": EXECUTION_VERSION,
                   "exit_trigger_price": trigger, "exit_reference_price": reference,
                   "exit_execution_price": price,
                   "exit_cost_per_share": round(reference - price, 4)}
