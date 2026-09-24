import time
from types import SimpleNamespace

import agent.market_lens_ui_agent as ui_agent
from agent.market_lens_ui_agent import (
    missing_scan_tickers,
    normalize_scan_ticker,
    recover_missing_scan_tickers,
)
from app.run_status import PARTIAL_OK, classify_run_status


class FakePage:
    def __init__(self):
        self.waits: list[int] = []

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(milliseconds)


def test_equal_counts_do_not_hide_missing_ticker():
    results = [SimpleNamespace(ticker="GS"), SimpleNamespace(ticker="GS")]
    assert missing_scan_tickers(["GS", "ARM"], results) == ["ARM"]


def test_complete_scan_and_case_normalization():
    assert missing_scan_tickers([" gs ", "FORM"], [SimpleNamespace(ticker="GS"), SimpleNamespace(ticker="FORM")]) == []


def test_empty_results_report_all_missing():
    assert missing_scan_tickers(["RDDT", "ARM", "MMC"], []) == ["ARM", "MMC", "RDDT"]


def test_provider_symbol_aliases_are_normalized_for_coverage():
    assert normalize_scan_ticker(" brk.b ") == "BRK-B"
    assert normalize_scan_ticker("bf/b") == "BF-B"
    assert missing_scan_tickers(
        ["BRK.B", "BF/B"],
        [SimpleNamespace(ticker="BRK-B"), SimpleNamespace(ticker="BF-B")],
    ) == []


def test_focused_recovery_retries_only_missing_and_records_outcomes(monkeypatch):
    page = FakePage()
    baskets: list[list[str]] = []
    calls = 0

    monkeypatch.setenv("MARKET_LENS_AGENT_MISSING_RECOVERY_ENABLED", "true")
    monkeypatch.setenv("MARKET_LENS_AGENT_MISSING_RECOVERY_MAX_TICKERS", "12")
    monkeypatch.setenv("MARKET_LENS_AGENT_MISSING_RECOVERY_ROUNDS", "2")
    monkeypatch.setenv("MARKET_LENS_AGENT_MISSING_RECOVERY_BATCH_SIZE", "4")
    monkeypatch.setenv("MARKET_LENS_AGENT_MISSING_RECOVERY_MIN_REMAINING_SECONDS", "0")
    monkeypatch.setenv("MARKET_LENS_AGENT_MISSING_RECOVERY_PAUSE_MS", "0")
    monkeypatch.setattr(ui_agent, "set_scan_basket", lambda _page, tickers: baskets.append(list(tickers)))
    monkeypatch.setattr(ui_agent, "bounded_scan_feedback", lambda _page: "Provider returned no data")

    def fake_run_scan(_page, _deadline):
        nonlocal calls
        calls += 1
        if calls == 1:
            return [SimpleNamespace(ticker="arm")]
        return []

    monkeypatch.setattr(ui_agent, "run_scan", fake_run_scan)

    recovered_results, diagnostics = recover_missing_scan_tickers(
        page,
        ["GOOD", "ARM", "MMC"],
        [SimpleNamespace(ticker="GOOD")],
        time.monotonic() + 60,
    )

    assert baskets == [["ARM", "MMC"], ["MMC"]]
    assert all("GOOD" not in basket for basket in baskets)
    assert sorted(item.ticker for item in recovered_results) == ["ARM", "GOOD"]
    assert diagnostics["scan_requests"] == 2
    assert diagnostics["attempts"] == {"ARM": 1, "MMC": 2}
    assert diagnostics["recovered_tickers"] == ["ARM"]
    assert diagnostics["unavailable_tickers"] == ["MMC"]
    assert diagnostics["outcomes"] == [
        {
            "ticker": "ARM",
            "status": "RECOVERED",
            "attempts": 1,
            "reason": "Result card recovered by focused retry.",
        },
        {
            "ticker": "MMC",
            "status": "DATA_UNAVAILABLE",
            "attempts": 2,
            "reason": "Provider returned no data",
        },
    ]


def test_focused_recovery_failure_is_isolated_and_bounded(monkeypatch):
    page = FakePage()
    baskets: list[list[str]] = []

    monkeypatch.setenv("MARKET_LENS_AGENT_MISSING_RECOVERY_ENABLED", "true")
    monkeypatch.setenv("MARKET_LENS_AGENT_MISSING_RECOVERY_ROUNDS", "2")
    monkeypatch.setenv("MARKET_LENS_AGENT_MISSING_RECOVERY_BATCH_SIZE", "1")
    monkeypatch.setenv("MARKET_LENS_AGENT_MISSING_RECOVERY_MIN_REMAINING_SECONDS", "0")
    monkeypatch.setenv("MARKET_LENS_AGENT_MISSING_RECOVERY_PAUSE_MS", "0")
    monkeypatch.setattr(ui_agent, "set_scan_basket", lambda _page, tickers: baskets.append(list(tickers)))
    monkeypatch.setattr(ui_agent, "run_scan", lambda _page, _deadline: (_ for _ in ()).throw(RuntimeError("502")))

    recovered_results, diagnostics = recover_missing_scan_tickers(
        page,
        ["GOOD", "RDDT"],
        [SimpleNamespace(ticker="GOOD")],
        time.monotonic() + 60,
    )

    assert [item.ticker for item in recovered_results] == ["GOOD"]
    assert baskets == [["RDDT"], ["RDDT"]]
    assert diagnostics["attempts"]["RDDT"] == 2
    assert diagnostics["outcomes"][0]["status"] == "DATA_UNAVAILABLE"
    assert "502" in diagnostics["outcomes"][0]["reason"]


def test_focused_recovery_respects_deadline_without_scanning(monkeypatch):
    page = FakePage()
    scanned = False

    monkeypatch.setenv("MARKET_LENS_AGENT_MISSING_RECOVERY_ENABLED", "true")
    monkeypatch.setenv("MARKET_LENS_AGENT_MISSING_RECOVERY_MIN_REMAINING_SECONDS", "90")

    def fail_if_scanned(*_args):
        nonlocal scanned
        scanned = True
        raise AssertionError("Recovery must not scan near the Agent deadline")

    monkeypatch.setattr(ui_agent, "set_scan_basket", fail_if_scanned)

    _results, diagnostics = recover_missing_scan_tickers(
        page,
        ["GOOD", "MMC"],
        [SimpleNamespace(ticker="GOOD")],
        time.monotonic() + 5,
    )

    assert scanned is False
    assert diagnostics["scan_requests"] == 0
    assert diagnostics["attempts"]["MMC"] == 0
    assert diagnostics["outcomes"][0]["status"] == "DATA_UNAVAILABLE"
    assert diagnostics["skipped_reason"] == "Focused recovery stopped to preserve the Agent run deadline."


def test_disabled_focused_recovery_records_data_unavailable(monkeypatch):
    monkeypatch.setenv("MARKET_LENS_AGENT_MISSING_RECOVERY_ENABLED", "false")

    results, diagnostics = recover_missing_scan_tickers(
        FakePage(),
        ["GOOD", "MMC"],
        [SimpleNamespace(ticker="GOOD")],
        time.monotonic() + 60,
    )

    assert [item.ticker for item in results] == ["GOOD"]
    assert diagnostics["attempted_tickers"] == []
    assert diagnostics["outcomes"][0]["ticker"] == "MMC"
    assert diagnostics["outcomes"][0]["status"] == "DATA_UNAVAILABLE"
    assert diagnostics["outcomes"][0]["attempts"] == 0


def test_partial_scan_status_remains_eligible_for_processing():
    status = "completed: 143 results; 3 unavailable"
    assert status.startswith("completed:")
    assert classify_run_status(
        auth_failed=False,
        scan_completed=True,
        requested=146,
        received=143,
        missing_tickers=["ARM", "MMC", "RDDT"],
    ) == PARTIAL_OK
