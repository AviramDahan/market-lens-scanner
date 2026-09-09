import pandas as pd
import pytest

from app.indicators import compute_relative_strength


def inputs(stock_return, benchmark_return):
    index = pd.date_range("2026-01-01", periods=21, tz="UTC")
    stock = pd.DataFrame({"Close": [100.] * 20 + [100 * (1 + stock_return)]}, index=index)
    benchmark = pd.Series([0.] * 19 + [benchmark_return], index=index[1:])
    return stock, benchmark


@pytest.mark.parametrize("benchmark_return", [-.05, .05])
def test_ordering_and_parity_in_both_market_directions(benchmark_return):
    assert compute_relative_strength(*inputs(benchmark_return, benchmark_return)) == pytest.approx(1)
    assert compute_relative_strength(*inputs(benchmark_return + .02, benchmark_return)) > 1.3
    assert compute_relative_strength(*inputs(benchmark_return - .02, benchmark_return)) < .7


def test_positive_benchmark_preserves_ratio():
    assert compute_relative_strength(*inputs(.10, .05)) == pytest.approx(2)


def test_flat_benchmark_is_neutral():
    assert compute_relative_strength(*inputs(.10, 0)) == 1


def test_missing_benchmark_interval_is_not_silently_compounded():
    stock, benchmark = inputs(.10, .05)
    assert compute_relative_strength(stock, benchmark.iloc[1:]) == 1


def test_twenty_intervals_need_twenty_one_prices():
    stock, benchmark = inputs(.10, .05)
    assert compute_relative_strength(stock.iloc[1:], benchmark) == 1


def test_extra_future_benchmark_data_is_not_used():
    stock, benchmark = inputs(.10, .05)
    benchmark.loc[benchmark.index[-1] + pd.Timedelta(days=1)] = .90
    assert compute_relative_strength(stock, benchmark) == pytest.approx(2)


def test_first_interval_is_included_for_both_legs():
    stock, benchmark = inputs(.10, 0)
    benchmark.iloc[0] = .05
    assert compute_relative_strength(stock, benchmark) == pytest.approx(2)
