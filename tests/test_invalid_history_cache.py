import pandas as pd
import pytest

from app import data


def frame(rows):
    values = {name: [100.0] * rows for name in ('Open', 'High', 'Low', 'Close', 'Volume')}
    return pd.DataFrame(values, index=pd.date_range('2020-01-01', periods=rows, tz='UTC'))


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    monkeypatch.setattr(data, '_FRAME_CACHE', {})


@pytest.mark.parametrize('interval,period,minimum', [('1d', '6mo', 50), ('1h', '60d', 100), ('1wk', '5y', 200)])
def test_inadequate_response_is_evicted_and_next_retry_fetches_valid_history(monkeypatch, interval, period, minimum):
    calls = []

    class Provider:
        def history(self, **kwargs):
            calls.append(kwargs)
            return frame(1 if len(calls) == 1 else minimum)

    monkeypatch.setattr(data.yf, 'Ticker', lambda _ticker: Provider())
    with pytest.raises(ValueError, match='need at least'):
        data._fetch_validated_frame('TEST', interval, period, minimum)
    assert data._FRAME_CACHE == {}
    assert len(data._fetch_validated_frame('TEST', interval, period, minimum)) == minimum
    assert len(data._fetch_validated_frame('TEST', interval, period, minimum)) == minimum
    assert len(calls) == 2


def test_repeatedly_short_history_is_still_rejected_without_lowering_minimum(monkeypatch):
    class Provider:
        def history(self, **kwargs):
            return frame(199)

    monkeypatch.setattr(data.yf, 'Ticker', lambda _ticker: Provider())
    for _ in range(2):
        with pytest.raises(ValueError, match='need at least 200'):
            data._fetch_validated_frame('TEST', '1wk', '5y', data.MIN_WEEKLY_ROWS)
    assert data._FRAME_CACHE == {}


def test_invalid_validation_does_not_remove_newer_concurrent_good_response(monkeypatch):
    key = ('TEST', '1wk', '5y', False)
    old = frame(1)
    old.attrs['provider_fetched_at'] = 'old'
    data._set_frame_cache(key, old)
    validate = data._validate_frame

    def concurrent_refresh(value, ticker, interval, minimum):
        refreshed = frame(minimum)
        refreshed.attrs['provider_fetched_at'] = 'new'
        data._set_frame_cache(key, refreshed)
        return validate(value, ticker, interval, minimum)

    monkeypatch.setattr(data, '_validate_frame', concurrent_refresh)
    with pytest.raises(ValueError):
        data._fetch_validated_frame('TEST', '1wk', '5y', 200)
    assert len(data._get_frame_cache(key, 900)) == 200


def test_short_intraday_quote_history_remains_cacheable(monkeypatch):
    calls = []

    class Provider:
        def history(self, **kwargs):
            calls.append(kwargs)
            return frame(1)

    monkeypatch.setattr(data.yf, 'Ticker', lambda _ticker: Provider())
    assert len(data.fetch_intraday_frame('TEST')) == 1
    assert len(data.fetch_intraday_frame('TEST')) == 1
    assert len(calls) == 1


def test_rejected_session_volume_is_evicted_before_focused_retry(monkeypatch):
    from types import SimpleNamespace
    from app import scanner
    calls = []
    class Provider:
        def history(self, **kwargs):
            calls.append(kwargs)
            value = frame(220)
            if len(calls) == 1:
                value.loc[value.index[-1], "Volume"] = 0
            return value
    monkeypatch.setattr(data.yf, "Ticker", lambda _ticker: Provider())
    monkeypatch.setattr(scanner, "fetch_ticker", lambda *_a, **_k: SimpleNamespace(
        daily=frame(220), weekly=frame(220),
        hourly=data._fetch_validated_frame("TEST", "1h", "60d", 100)))
    class ReachedValidIndicators(Exception):
        pass
    def next_indicator(_daily):
        raise ReachedValidIndicators()
    monkeypatch.setattr(scanner, "compute_ema", next_indicator)
    with pytest.raises(ValueError, match="Session VWAP volume unavailable"):
        scanner.scan_ticker_detail("TEST")
    assert data._FRAME_CACHE == {}
    with pytest.raises(ReachedValidIndicators):
        scanner.scan_ticker_detail("TEST")
    assert len(calls) == 2


@pytest.mark.parametrize("volumes", [[0, 0], [1, -1], [1, float("nan")], [1, float("inf")]])
@pytest.mark.parametrize("closes", [[100, 100], [100, 101]])
def test_invalid_volume_never_manufactures_profile_levels(volumes, closes):
    from app.indicators import compute_volume_profile
    with pytest.raises(ValueError, match="volume unavailable or invalid"):
        compute_volume_profile(pd.DataFrame({"Close": closes, "Volume": volumes}))


def test_valid_constant_price_still_has_real_volume_profile():
    from app.indicators import compute_volume_profile
    profile = compute_volume_profile(pd.DataFrame({"Close": [100, 100], "Volume": [1, 2]}))
    assert profile.poc == profile.val == profile.vah == 100
