"""Versioned, secret-free measurement provenance. Never selects or places a trade."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.entry_policy import fibonacci_entries_enabled
from app.execution import EXECUTION_VERSION

VERSION = "risk_data_consistency_v1"
EXECUTION_ASSUMPTIONS = {
    "version": "intraday_ohlc_existing_v1",
    "same_bar_stop_and_target": "STOP_FIRST",
    "same_bar_first_touch_both_targets": "TP2_FULL_REMAINING",
    "new_breakeven_stop_within_tp1_bar": "NOT_INFERRED",
    "fills": "FROZEN_ENTRY_COST_MODEL_WHEN_PRESENT_ELSE_LEGACY",
    "guaranteed_market_fills": False,
}


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _count(name: str, default: str, *, lower: int, upper: int | None = None) -> int | None:
    try:
        value = max(lower, int(os.getenv(name, default)))
        return min(upper, value) if upper is not None else value
    except ValueError:
        # An invalid monitor setting is unavailable evidence, not a scanner gate.
        return None


def policy_manifest(config: Any, *, base_min_rr: float | None = None,
                    preliminary_max_risk: float | None = None, currency_rate: float | None = None) -> dict[str, Any]:
    # Explicit config schema, never an environment dump or credentials object.
    body = {
        "version": VERSION, "risk_config": asdict(config),
        "effective_base_min_rr": base_min_rr,
        "effective_preliminary_max_risk": preliminary_max_risk,
        "effective_currency_rate": currency_rate,
        "effective_entry_inputs_complete": all(value is not None for value in (base_min_rr, preliminary_max_risk, currency_rate)),
        "fibonacci_entries_enabled": fibonacci_entries_enabled(),
        "breakout_continuation_enabled": _flag("MARKET_LENS_BREAKOUT_CONTINUATION_ENABLED", "true"),
        "disabled_entry_setups": sorted(x.strip().casefold() for x in os.getenv("MARKET_LENS_DISABLED_ENTRY_SETUPS", "").split(",") if x.strip()),
        "score_definition": "legacy_248_professional_55_45_aligned_rs_v2",
        "relative_strength_contract": "aligned_excess_over_absolute_benchmark_v1",
        "vix_contract": "stress_precedes_level_fallback_v1",
        "confirmation_vwap_reference": "REGULAR_SESSION_VWAP",
        "execution_model_version": EXECUTION_VERSION,
        "extended_hours_targets": _flag("MARKET_LENS_MONITOR_EXTENDED_HOURS_TARGETS", "true"),
        "extended_hours_stops": _flag("MARKET_LENS_MONITOR_EXTENDED_HOURS_STOPS", "false"),
        "extended_hours_target_confirmation_bars": _count("MARKET_LENS_MONITOR_EXTENDED_HOURS_TARGET_CONFIRMATION_BARS", "2", lower=1, upper=5),
        "regime_lkg_max_session_age": _count("MARKET_LENS_REGIME_LKG_MAX_SESSION_AGE", "3", lower=0),
        "correlation_unknown_policy": "WARN_ONLY_UNCHANGED_ENTRY_POLICY",
        "execution_assumptions": EXECUTION_ASSUMPTIONS,
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return {**body, "fingerprint": hashlib.sha256(encoded).hexdigest()}


def decision_policy_evidence(config: Any, **effective_inputs: Any) -> dict[str, str]:
    manifest = policy_manifest(config, **effective_inputs)
    return {"decision_policy_version": VERSION,
            "decision_policy_fingerprint": manifest["fingerprint"],
            "decision_policy_manifest_status": "NOT_WRITTEN_BY_EVALUATOR",
            "score_definition_version": manifest["score_definition"],
            "execution_assumptions_version": EXECUTION_ASSUMPTIONS["version"]}


def write_policy_manifest(directory: Path, config: Any, **effective_inputs: Any) -> Path:
    manifest = policy_manifest(config, **effective_inputs)
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        if not directory.is_dir():
            raise
    else:
        # Root workers must not leave host-owned scanner stages unremovable.
        if getattr(os, "geteuid", lambda: None)() == 0 and hasattr(os, "chown"):
            parent = directory.parent.stat()
            os.chown(directory, parent.st_uid, parent.st_gid)
    path = directory / f"{manifest['fingerprint']}.json"
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != manifest:
            raise ValueError("Policy manifest identity mismatch; existing evidence preserved")
        return path
    with path.open("x", encoding="utf-8") as target:
        json.dump(manifest, target, sort_keys=True, indent=2, allow_nan=False)
    return path
