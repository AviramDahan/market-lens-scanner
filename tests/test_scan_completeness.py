from types import SimpleNamespace

from agent.market_lens_ui_agent import missing_scan_tickers


def test_equal_counts_do_not_hide_missing_ticker():
    results = [SimpleNamespace(ticker="GS"), SimpleNamespace(ticker="GS")]
    assert missing_scan_tickers(["GS", "ARM"], results) == ["ARM"]


def test_complete_scan_and_case_normalization():
    assert missing_scan_tickers([" gs ", "FORM"], [SimpleNamespace(ticker="GS"), SimpleNamespace(ticker="FORM")]) == []


def test_empty_results_report_all_missing():
    assert missing_scan_tickers(["RDDT", "ARM", "MMC"], []) == ["ARM", "MMC", "RDDT"]
