"""Completed-session evidence and bounded fallback storage for market regime inputs."""

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app.trading_clock import NY_TZ, session_bounds


REGIME_CACHE_VERSION = 1


def expected_completed_session(now: datetime) -> date | None:
    """Return the latest NYSE session whose close is not later than ``now``."""
    if now.tzinfo is None:
        return None
    local_day = now.astimezone(NY_TZ).date()
    for offset in range(15):
        day = local_day - timedelta(days=offset)
        bounds = session_bounds(day)
        if bounds and bounds[1] <= now:
            return day
    return None


def session_date(value: Any) -> date:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("Missing daily session timestamp")
    return stamp.tz_convert(NY_TZ).date() if stamp.tzinfo is not None else stamp.date()


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
        expected = expected_completed_session(now)
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


def completed_regime_frame(frame: pd.DataFrame, now: datetime) -> tuple[pd.DataFrame, dict]:
    """Remove unfinished/future daily bars before regime arithmetic.

    Daily provider timestamps are session labels. During regular trading hours a
    provider may already expose today's unfinished daily candle, which must never
    influence the market-regime decision.
    """
    raw_evidence = daily_regime_evidence(frame, now)
    expected = expected_completed_session(now)
    if (
        expected is None
        or now.tzinfo is None
        or not isinstance(frame.index, pd.DatetimeIndex)
        or frame.empty
        or frame.index.hasnans
        or not frame.index.is_unique
        or not frame.index.is_monotonic_increasing
    ):
        return frame.iloc[0:0].copy(), raw_evidence

    keep = [session_date(stamp) <= expected for stamp in frame.index]
    completed = frame.loc[keep].copy()
    completed.attrs.update(frame.attrs)
    evidence = daily_regime_evidence(completed, now)
    evidence["provider_last_bar_timestamp"] = raw_evidence.get("last_bar_timestamp")
    evidence["provider_last_bar_session"] = raw_evidence.get("last_bar_session")
    evidence["provider_freshness_status"] = raw_evidence.get("freshness_status")
    evidence["unfinished_bars_excluded"] = int(len(frame) - len(completed))
    return completed, evidence


def regime_cache_path() -> Path | None:
    configured = str(os.getenv("MARKET_LENS_REGIME_CACHE_PATH") or "").strip()
    if configured:
        return Path(configured)
    run_dir = str(os.getenv("MARKET_LENS_RUN_DIR") or "").strip()
    return Path(run_dir) / "regime" / "market_regime_lkg.json" if run_dir else None


def load_regime_cache(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"version": REGIME_CACHE_VERSION, "benchmarks": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != REGIME_CACHE_VERSION or not isinstance(payload.get("benchmarks"), dict):
            raise ValueError("Unsupported regime cache schema")
        return payload
    except Exception:
        return {"version": REGIME_CACHE_VERSION, "benchmarks": {}}


def save_regime_cache(path: Path | None, cache: dict[str, Any]) -> bool:
    if path is None:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": REGIME_CACHE_VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "benchmarks": cache.get("benchmarks", {}),
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
        return True
    except Exception:
        return False


def completed_session_age(cached_session: str, expected_session: str) -> int | None:
    """Count completed NYSE sessions after a cached session through expected."""
    try:
        cached = date.fromisoformat(cached_session)
        expected = date.fromisoformat(expected_session)
    except (TypeError, ValueError):
        return None
    if cached > expected:
        return None
    count = 0
    cursor = cached + timedelta(days=1)
    while cursor <= expected:
        if session_bounds(cursor):
            count += 1
        cursor += timedelta(days=1)
        if count > 30:
            break
    return count


def cached_regime_state(
    cache: dict[str, Any],
    label: str,
    expected_session: str | None,
    *,
    max_session_age: int,
) -> tuple[dict[str, Any] | None, int | None]:
    item = (cache.get("benchmarks") or {}).get(label)
    if not isinstance(item, dict) or not expected_session:
        return None, None
    state = item.get("state")
    age = completed_session_age(str(item.get("session") or ""), expected_session)
    if not isinstance(state, dict) or age is None or age > max(0, int(max_session_age)):
        return None, age
    result = dict(state)
    result["cached_session"] = item.get("session")
    result["cache_saved_at"] = item.get("saved_at")
    return result, age


def update_regime_cache(
    cache: dict[str, Any], label: str, state: dict[str, Any], session: str, symbol: str
) -> None:
    cache.setdefault("benchmarks", {})[label] = {
        "symbol": symbol,
        "session": session,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "state": dict(state),
    }
