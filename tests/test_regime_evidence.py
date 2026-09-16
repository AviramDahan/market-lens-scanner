from datetime import datetime, timezone

import pandas as pd
import pytest

from app.agent_risk import AgentRiskConfig, assess_agent_market_regime
from app.data import _fetch_frame
from app.regime_evidence import daily_regime_evidence


def frame(day):
    return pd.DataFrame({"Close": [100.]}, index=pd.DatetimeIndex([day]))


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
    result = assess_agent_market_regime(AgentRiskConfig(100000, 40000, 10000))
    assert result.label == "BEAR"
    assert result.risk_points == -4.25
    assert result.max_total_exposure == 0
    assert result.minimum_net_rr == 999
    assert {k: v["risk_point_contribution"] for k, v in result.indicators.items()} == {
        "SPY": -2, "QQQ": -2, "IWM": -1, "VIX": 1, "US10Y": -.5, "DXY": .25,
    }
