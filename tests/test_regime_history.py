import pandas as pd
import pytest

from app.agent_risk import AgentRiskConfig, assess_agent_market_regime, benchmark_state


@pytest.mark.parametrize("size", [1, 50, 126, 199])
def test_short_history_cannot_masquerade_as_ema200(size):
    with pytest.raises(ValueError, match="EMA200"):
        benchmark_state(pd.DataFrame({"Close": [100.] * size}))


def test_actual_ema200_is_used():
    close = pd.Series([100. + i / 10 for i in range(260)])
    state = benchmark_state(pd.DataFrame({"Close": close}))
    assert state["ema200"] == round(close.ewm(span=200, adjust=False).mean().iloc[-1], 4)
    assert state["history_rows"] == 260


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0, -1])
def test_invalid_close_is_not_silently_skipped(bad):
    with pytest.raises(ValueError, match="EMA200"):
        benchmark_state(pd.DataFrame({"Close": [100.] * 250 + [bad]}))


def test_missing_benchmarks_do_not_add_positive_iwm_point(monkeypatch):
    periods = []
    def fetch(symbol, period):
        periods.append(period)
        raise ValueError("fixture missing history")
    monkeypatch.setattr("app.agent_risk.fetch_daily_frame", fetch)
    cfg = AgentRiskConfig(starting_capital=100000, default_max_total_exposure=40000, max_position=10000)
    state = assess_agent_market_regime(cfg)
    assert state.risk_points == 0
    assert state.label == "NEUTRAL"
    assert state.warnings
    assert periods and set(periods) == {"2y"}
