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
