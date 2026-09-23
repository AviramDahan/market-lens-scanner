from datetime import datetime, timezone

import pandas as pd
import pytest

from app.agent_risk import AgentRiskConfig, assess_agent_market_regime
from app.data import _fetch_frame
from app.regime_evidence import (
    cached_regime_state,
    completed_regime_frame,
    daily_regime_evidence,
    load_regime_cache,
)


def frame(day):
    return pd.DataFrame({"Close": [100.]}, index=pd.DatetimeIndex([day]))


def history_ending(day: str, *, rising: bool = True) -> pd.DataFrame:
    index = pd.bdate_range(end=day, periods=260)
    values = [100.0 + step * (0.1 if rising else -0.05) for step in range(260)]
    result = pd.DataFrame({"Close": values}, index=index)
    result.attrs.update(provider="test-provider", provider_fetched_at="2026-09-15T20:01:00+00:00")
    return result


@pytest.mark.parametrize("now,last,expected,status", [
    ("2026-09-15T17:00:00+00:00", "2026-09-15", "2026-09-14", "CURRENT_SESSION_IN_PROGRESS"),
    ("2026-09-15T17:00:00+00:00", "2026-09-14", "2026-09-14", "LATEST_COMPLETED_SESSION"),
    ("2026-09-15T17:00:00+00:00", "2026-09-11", "2026-09-14", "STALE_SESSION"),
    ("2026-09-14T12:00:00+00:00", "2026-09-11", "2026-09-11", "LATEST_COMPLETED_SESSION"),
    ("2026-09-07T17:00:00+00:00", "2026-09-04", "2026-09-04", "LATEST_COMPLETED_SESSION"),
    ("2026-11-27T18:01:00+00:00", "2026-11-25", "2026-11-27", "STALE_SESSION"),
    ("2026-09-15T17:00:00+00:00", "2026-09-16", "2026-09-14", "FUTURE_SESSION"),
    ("2026-09-14T12:00:00+00:00", "2026-09-14", "2026-09-11", "UNEXPECTED_SESSION"),
])
def test_daily_session_evidence(now, last, expected, status):
    result = daily_regime_evidence(frame(last), datetime.fromisoformat(now))
    assert result["freshness_status"] == status
    assert result["expected_completed_session"] == expected
    assert result["provider_fetched_at"] is None


def test_unknown_or_invalid_evidence_never_claims_freshness(monkeypatch):
    now = datetime(2026, 9, 15, 17, tzinfo=timezone.utc)
    assert daily_regime_evidence(pd.DataFrame({"Close": [100]}), now)["freshness_status"] == "UNKNOWN"
    duplicate = pd.concat([frame("2026-09-14"), frame("2026-09-14")])
    assert daily_regime_evidence(duplicate, now)["freshness_status"] == "INVALID_TIMESTAMPS"
    monkeypatch.setattr("app.regime_evidence.session_bounds", lambda day: (_ for _ in ()).throw(ValueError()))
    assert daily_regime_evidence(frame("2026-09-14"), now)["freshness_status"] == "UNKNOWN"


def test_cached_frame_keeps_actual_provider_retrieval_time(monkeypatch):
    calls = []
    class Provider:
        def history(self, **kwargs):
            calls.append(kwargs)
            return pd.DataFrame({k: [100.] for k in ("Open", "High", "Low", "Close", "Volume")},
                                index=pd.date_range("2026-09-15", periods=1, tz="America/New_York"))
    monkeypatch.setattr("app.data.yf.Ticker", lambda ticker: Provider())
    monkeypatch.setattr("app.data._FRAME_CACHE", {})
    first = _fetch_frame("SPY", "1d", "2y")
    second = _fetch_frame("SPY", "1d", "2y")
    assert len(calls) == 1
    assert second.attrs["provider_fetched_at"] == first.attrs["provider_fetched_at"]
    assert second.attrs["provider"] == "yfinance"
    assert second.index[-1].isoformat() == "2026-09-15T04:00:00+00:00"


def test_recorded_september_15_regime_arithmetic_is_unchanged(monkeypatch):
    states = iter([
        {"trend": "bearish"}, {"trend": "bearish"}, {"trend": "bearish"},
        {"trend": "mixed", "price": 17.56}, {"trend": "bullish"}, {"trend": "bearish"},
    ])
    monkeypatch.setattr("app.agent_risk.fetch_daily_frame", lambda *a, **k: frame("2026-09-15"))
    monkeypatch.setattr("app.agent_risk.benchmark_state", lambda *a, **k: next(states))
    result = assess_agent_market_regime(
        AgentRiskConfig(100000, 40000, 10000),
        evaluated_at=datetime.fromisoformat("2026-09-15T21:00:00+00:00"),
    )
    assert result.label == "BEAR"
    assert result.risk_points == -4.25
    assert result.max_total_exposure == 0
    assert result.minimum_net_rr == 999
    assert {k: v["risk_point_contribution"] for k, v in result.indicators.items()} == {
        "SPY": -2, "QQQ": -2, "IWM": -1, "VIX": 1, "US10Y": -.5, "DXY": .25,
    }


def test_unfinished_daily_bar_is_excluded_from_regime_frame():
    source = history_ending("2026-09-15")
    source.iloc[-1, source.columns.get_loc("Close")] = 999.0
    completed, evidence = completed_regime_frame(
        source, datetime.fromisoformat("2026-09-15T17:00:00+00:00")
    )
    assert completed.index[-1].date().isoformat() == "2026-09-14"
    assert float(completed["Close"].iloc[-1]) != 999.0
    assert evidence["unfinished_bars_excluded"] == 1
    assert evidence["freshness_status"] == "LATEST_COMPLETED_SESSION"


def test_current_daily_bar_is_included_after_exchange_close():
    source = history_ending("2026-09-15")
    completed, evidence = completed_regime_frame(
        source, datetime.fromisoformat("2026-09-15T21:00:00+00:00")
    )
    assert completed.index[-1].date().isoformat() == "2026-09-15"
    assert evidence["unfinished_bars_excluded"] == 0
    assert evidence["freshness_status"] == "LATEST_COMPLETED_SESSION"


def test_regime_uses_bounded_last_known_good_cache(monkeypatch, tmp_path):
    cache_path = tmp_path / "regime.json"
    monkeypatch.setenv("MARKET_LENS_REGIME_CACHE_PATH", str(cache_path))
    monkeypatch.setenv("MARKET_LENS_REGIME_LKG_MAX_SESSION_AGE", "3")
    monkeypatch.setattr("app.agent_risk.fetch_daily_frame", lambda *a, **k: history_ending("2026-09-15"))
    cfg = AgentRiskConfig(100000, 40000, 10000)
    fresh = assess_agent_market_regime(
        cfg, evaluated_at=datetime.fromisoformat("2026-09-15T21:00:00+00:00")
    )
    assert fresh.data_status == "HEALTHY"
    assert cache_path.exists()

    monkeypatch.setattr(
        "app.agent_risk.fetch_daily_frame",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("provider outage")),
    )
    fallback = assess_agent_market_regime(
        cfg, evaluated_at=datetime.fromisoformat("2026-09-16T21:00:00+00:00")
    )
    assert fallback.data_status == "DEGRADED"
    assert fallback.fallback_used is True
    assert fallback.allows_new_buys is True
    assert fallback.label == "NEUTRAL"
    assert fallback.indicators["SPY"]["regime_data_source"] == "LAST_KNOWN_GOOD"
    assert fallback.indicators["SPY"]["fallback_session_age"] == 1


def test_expired_regime_cache_blocks_new_buys(monkeypatch, tmp_path):
    cache_path = tmp_path / "regime.json"
    monkeypatch.setenv("MARKET_LENS_REGIME_CACHE_PATH", str(cache_path))
    monkeypatch.setenv("MARKET_LENS_REGIME_LKG_MAX_SESSION_AGE", "1")
    monkeypatch.setattr("app.agent_risk.fetch_daily_frame", lambda *a, **k: history_ending("2026-09-15"))
    cfg = AgentRiskConfig(100000, 40000, 10000)
    assess_agent_market_regime(cfg, evaluated_at=datetime.fromisoformat("2026-09-15T21:00:00+00:00"))
    monkeypatch.setattr(
        "app.agent_risk.fetch_daily_frame",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("provider outage")),
    )
    expired = assess_agent_market_regime(
        cfg, evaluated_at=datetime.fromisoformat("2026-09-18T21:00:00+00:00")
    )
    assert expired.fallback_used is False
    assert expired.allows_new_buys is False
    assert expired.indicators["SPY"]["regime_data_source"] == "UNAVAILABLE"


def test_future_provider_bar_cannot_replace_last_known_good(monkeypatch, tmp_path):
    cache_path = tmp_path / "regime.json"
    monkeypatch.setenv("MARKET_LENS_REGIME_CACHE_PATH", str(cache_path))
    monkeypatch.setattr("app.agent_risk.fetch_daily_frame", lambda *a, **k: history_ending("2026-09-15"))
    cfg = AgentRiskConfig(100000, 40000, 10000)
    assess_agent_market_regime(cfg, evaluated_at=datetime.fromisoformat("2026-09-15T21:00:00+00:00"))

    monkeypatch.setattr("app.agent_risk.fetch_daily_frame", lambda *a, **k: history_ending("2026-09-17"))
    result = assess_agent_market_regime(
        cfg, evaluated_at=datetime.fromisoformat("2026-09-16T21:00:00+00:00")
    )
    stored = load_regime_cache(cache_path)
    assert result.indicators["SPY"]["regime_data_source"] == "LAST_KNOWN_GOOD"
    assert result.indicators["SPY"]["last_bar_session"] == "2026-09-15"
    assert stored["benchmarks"]["SPY"]["session"] == "2026-09-15"


def test_missing_vix_degrades_bull_to_neutral_without_blocking(monkeypatch, tmp_path):
    monkeypatch.setenv("MARKET_LENS_REGIME_CACHE_PATH", str(tmp_path / "empty.json"))

    def fetch(symbol, period):
        if symbol == "^VIX":
            raise RuntimeError("VIX unavailable")
        return history_ending("2026-09-15")

    monkeypatch.setattr("app.agent_risk.fetch_daily_frame", fetch)
    result = assess_agent_market_regime(
        AgentRiskConfig(100000, 40000, 10000),
        evaluated_at=datetime.fromisoformat("2026-09-15T21:00:00+00:00"),
    )
    assert result.data_status == "DEGRADED"
    assert result.allows_new_buys is True
    assert result.label == "NEUTRAL"
    assert result.max_total_exposure <= 30000


def test_malformed_cache_is_ignored_without_crashing(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not-json", encoding="utf-8")
    assert load_regime_cache(path)["benchmarks"] == {}
    state, age = cached_regime_state(
        {"benchmarks": {}}, "SPY", "2026-09-15", max_session_age=3
    )
    assert state is None and age is None
