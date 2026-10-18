"""Shared entry-policy flags and the promoted non-Fibonacci breakout profile."""
from __future__ import annotations

import math
import os
from typing import Any

BREAKOUT_SETUP = "Breakout + Retest"


def fibonacci_entries_enabled() -> bool:
    return os.getenv("MARKET_LENS_FIBONACCI_ENTRIES_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def breakout_continuation_profile(
    *, setup_type: str, market_regime: str, sector_regime: str,
    session: dict[str, Any], confirmation: dict[str, Any], freshness: dict[str, Any],
    net_rr_1: Any, earnings_blackout: bool, minimum_primary_net_rr: float,
) -> dict[str, Any]:
    enabled = os.getenv("MARKET_LENS_BREAKOUT_CONTINUATION_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    applicable = enabled and setup_type == BREAKOUT_SETUP
    if not applicable:
        return {"name": "BREAKOUT_CONTINUATION", "version": "active_v1", "applicable": False}
    try:
        primary = float(net_rr_1)
    except (TypeError, ValueError):
        primary = float('nan')
    checks = [
        ("regular_session", session.get("phase") == "REGULAR" and session.get("can_open_new_buy") is True),
        ("market_regime", market_regime in {"BULL", "NEUTRAL"}),
        ("sector_regime", sector_regime in {"STRONG", "NEUTRAL"}),
        ("completed_confirmation", confirmation.get("entry_confirmation_passed") is True),
        ("confirmation_freshness", freshness.get("status") == "FRESH_SAME_SESSION"),
        ("primary_net_rr", math.isfinite(primary) and primary >= max(.8, minimum_primary_net_rr)),
        ("earnings_blackout", earnings_blackout is False),
    ]
    failed = [name for name, passed in checks if not passed]
    staging_only = (
        bool(failed)
        and set(failed) <= {"regular_session", "confirmation_freshness"}
        and session.get("phase") in {"PRE_MARKET", "AFTER_HOURS", "CLOSED", "WEEKEND", "HOLIDAY"}
    )
    return {"name": "BREAKOUT_CONTINUATION", "version": "active_v1", "applicable": True,
            "passed": not failed, "source_setup_type": BREAKOUT_SETUP,
            "checks": [{"name": name, "passed": bool(passed)} for name, passed in checks],
            "blockers": failed, "staging_only": staging_only, "replaces_existing_gates": False,
            "reason": "Breakout continuation confirmed; all existing entry and capital gates still apply."
            if not failed else "Breakout continuation requires: " + ", ".join(failed) + "."}
