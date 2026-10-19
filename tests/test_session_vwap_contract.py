from types import SimpleNamespace

import pandas as pd
import pytest

from app.indicators import compute_vwap, session_vwap
from app.agent_risk import calculate_entry_confirmation_from_frame, rolling_vwap


def frame():
    index = pd.to_datetime(["2026-10-15T19:00Z", "2026-10-15T19:30Z",
                            "2026-10-16T13:30Z", "2026-10-16T14:00Z", "2026-10-16T14:30Z"])
    return pd.DataFrame({"Open": [110, 110, 100, 100, 90], "High": [110, 110, 101, 102, 92],
                         "Low": [110, 110, 99, 100, 89], "Close": [110, 110, 100, 102, 91],
                         "Volume": [100]*5}, index=index)


def test_scanner_and_confirmation_share_session_reset_on_identical_bars():
    bars = frame().iloc[:-1]
    assert compute_vwap(bars) == pytest.approx(100.6666667)
    assert rolling_vwap(bars) == compute_vwap(bars)
    assert rolling_vwap(bars, lookback=1) == compute_vwap(bars)


def test_confirmation_excludes_live_and_future_bars_from_session_vwap():
    result = calculate_entry_confirmation_from_frame(
        result=SimpleNamespace(setup_type="VWAP Reclaim Setup"), frame=frame(),
        buy_low=99, buy_high=103, timeframe="30m_completed", drop_last=False,
        now=pd.Timestamp("2026-10-16T14:35Z").to_pydatetime())
    assert result["entry_confirmation_passed"] is True
    assert result["confirmation_vwap_reference"] == "REGULAR_SESSION_VWAP"
    assert result["confirmation_vwap_price"] == pytest.approx(100.6667)
    assert result["confirmation_candle_timestamp"] == "2026-10-16T14:00:00+00:00"


@pytest.mark.parametrize("bad", ["volume", "timestamp", "nan"])
def test_invalid_vwap_evidence_does_not_become_confirmation(bad):
    bars = frame()
    if bad == "volume":
        bars["Volume"] = 0
    elif bad == "timestamp":
        bars.index = bars.index.tz_localize(None)
    else:
        bars.iloc[-1, bars.columns.get_loc("Close")] = float("nan")
    assert rolling_vwap(bars) is None


def test_daily_bars_cannot_masquerade_as_intraday_vwap_confirmation():
    bars = frame().iloc[:3].copy()
    bars.index = pd.to_datetime(["2026-10-13T04:00Z", "2026-10-14T04:00Z", "2026-10-15T04:00Z"])
    result = calculate_entry_confirmation_from_frame(
        result=SimpleNamespace(setup_type="VWAP Reclaim Setup"), frame=bars,
        buy_low=99, buy_high=103, timeframe="1d_completed", drop_last=False,
        now=pd.Timestamp("2026-10-16T14:35Z").to_pydatetime())
    assert result["entry_confirmation_passed"] is False
    assert result["confirmation_vwap_price"] is None
