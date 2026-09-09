import pytest
from types import SimpleNamespace
from unittest.mock import Mock

from agent.position_monitor import MonitorResult, classify_monitor_results


@pytest.mark.parametrize("status", ["ERROR", "NO_DATA", "DATA_ERROR"])
def test_all_failed_is_not_a_successful_empty_run(status):
    result = classify_monitor_results([MonitorResult(ticker="TEST", status=status, current_price=0)])
    assert result["status"] == "MONITOR_FAILED"
    assert result["positions_failed"] == 1


def test_empty_portfolio_is_not_a_failure():
    assert classify_monitor_results([])["status"] == "MONITOR_OK"


def test_hold_and_no_new_bars_are_successful_evaluations():
    rows = [MonitorResult(ticker="A", status="HOLD", current_price=100),
            MonitorResult(ticker="B", status="NO_NEW_BARS", current_price=100)]
    assert classify_monitor_results(rows)["status"] == "MONITOR_OK"


def test_partial_failure_does_not_discard_valid_events_or_leak_provider_error():
    rows = [MonitorResult(ticker="A", status="EVENT", current_price=100),
            MonitorResult(ticker="B", status="ERROR", current_price=0, error="sensitive provider detail")]
    result = classify_monitor_results(rows)
    assert result["status"] == "MONITOR_DEGRADED"
    assert result["positions_failed"] == 1
    assert "sensitive" not in str(result)


def test_main_exits_nonzero_without_saving_when_all_prices_fail(monkeypatch):
    from agent import position_monitor as monitor

    wb = Mock()
    monkeypatch.setattr(monitor, "load_settings", lambda: SimpleNamespace(excel_path="unused", save_noop=True))
    monkeypatch.setattr(monitor, "load_workbook", lambda _: wb)
    monkeypatch.setattr(monitor, "ensure_agent_columns", lambda _: None)
    monkeypatch.setattr(monitor, "ensure_position_events_sheet", lambda _: None)
    monkeypatch.setattr(monitor, "read_settings", lambda _: {})
    monkeypatch.setattr(monitor, "read_open_positions", lambda _: {"TEST": {"ticker": "TEST"}})
    monkeypatch.setattr(monitor, "read_last_event_times", lambda *_: {})
    monkeypatch.setattr(monitor, "monitor_position", lambda *args, **kwargs:
                        MonitorResult(ticker="TEST", status="NO_DATA", current_price=0))
    with pytest.raises(SystemExit) as error:
        monitor.main()
    assert error.value.code == 1
    wb.save.assert_not_called()
    wb.close.assert_called_once()


def test_saved_summary_reports_missing_data_even_without_exception(tmp_path):
    from agent.position_monitor import write_summary

    rows = [MonitorResult(ticker="A", status="HOLD", current_price=100),
            MonitorResult(ticker="B", status="NO_DATA", current_price=0)]
    path = write_summary(settings=SimpleNamespace(run_dir=tmp_path, excel_path="fixture.xlsx"),
                         run_id="fixture", timestamp="2026-09-09T14:00:00Z", results=rows,
                         cash=1000, exposure=100, open_risk=5, currency="USD")
    content = path.read_text(encoding="utf-8")
    assert "Run status: ISSUES" in content
    assert "Evaluation status: MONITOR_DEGRADED" in content
    assert "B: NO_DATA" in content
