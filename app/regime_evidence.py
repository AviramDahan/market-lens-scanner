"""Observability for daily regime inputs; does not change strategy eligibility."""

from datetime import datetime, timedelta

import pandas as pd

from app.trading_clock import NY_TZ, session_bounds


def daily_regime_evidence(frame: pd.DataFrame, now: datetime) -> dict:
    evidence = {
        "provider": frame.attrs.get("provider", "not_recorded"),
        "provider_fetched_at": frame.attrs.get("provider_fetched_at"),
        "evaluated_at": now.isoformat(),
        "last_bar_timestamp": None,
        "last_bar_session": None,
        "expected_completed_session": None,
        "freshness_status": "UNKNOWN",
        "bar_timestamp_semantics": "Daily session label, not last quote time.",
        "freshness_reference": "NYSE completed sessions; informational for non-equity benchmarks.",
    }
    if now.tzinfo is None or not isinstance(frame.index, pd.DatetimeIndex) or frame.empty:
        return evidence
    if frame.index.hasnans or not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        evidence["freshness_status"] = "INVALID_TIMESTAMPS"
        return evidence
    stamp = frame.index[-1]
    # Provider daily indices are local session labels. Do not UTC-shift naive dates.
    last_day = stamp.date()
    if stamp.tzinfo is not None:
        last_day = stamp.tz_convert(NY_TZ).date()
    evidence.update(last_bar_timestamp=stamp.isoformat(), last_bar_session=last_day.isoformat())
    local_day = now.astimezone(NY_TZ).date()
    try:
        expected = None
        for offset in range(15):
            day = local_day - timedelta(days=offset)
            bounds = session_bounds(day)
            if bounds and bounds[1] <= now:
                expected = day
                break
        if expected is None:
            return evidence
        evidence["expected_completed_session"] = expected.isoformat()
        if last_day > local_day:
            evidence["freshness_status"] = "FUTURE_SESSION"
        elif last_day < expected:
            evidence["freshness_status"] = "STALE_SESSION"
        elif last_day == expected:
            evidence["freshness_status"] = "LATEST_COMPLETED_SESSION"
        else:
            bounds = session_bounds(last_day)
            evidence["freshness_status"] = (
                "CURRENT_SESSION_IN_PROGRESS"
                if bounds and bounds[0] <= now < bounds[1]
                else "UNEXPECTED_SESSION"
            )
    except Exception:
        # A calendar failure must remain unknown, never silently become fresh.
        evidence["freshness_status"] = "UNKNOWN"
    return evidence
