from openpyxl import Workbook

from agent.audit_exit_history import audit_workbook


def workbook(trigger="2026-09-01T14:00:00Z", identity="trade"):
    wb = Workbook()
    wb.active.title = "Trade Log"
    wb.active.append(["header"] * 21)
    buy = [None] * 21
    buy[0:3] = ["2026-09-04T14:00:00Z", "BUY_SIMULATED", "TEST"]
    buy[20] = "trade"
    wb.active.append(buy)
    exit_row = list(buy)
    exit_row[0:2] = ["2026-09-08T14:00:00Z", "EXIT_STOP"]
    wb.active.append(exit_row)
    events = wb.create_sheet("Position Events")
    events.append(["header"] * 16)
    row = [None] * 16
    row[0:5] = [exit_row[0], "run", "TEST", "EXIT_STOP", trigger]
    row[15] = identity
    events.append(row)
    return wb


def test_pre_entry_event_is_reported_without_mutation():
    wb = workbook()
    before = {ws.title: list(ws.values) for ws in wb}
    report = audit_workbook(wb)
    assert report["findings"][0]["status"] == "PRE_ENTRY_EVENT"
    assert report["affected_trade_ids"] == ["trade"]
    assert before == {ws.title: list(ws.values) for ws in wb}


def test_valid_event_has_no_finding():
    assert not audit_workbook(workbook("2026-09-08T13:00:00Z"))["findings"]


def test_explicit_identity_mismatch_is_not_ignored():
    assert audit_workbook(workbook(identity="old-trade"))["findings"][0]["status"] == "TRADE_ID_MISMATCH"


def test_ambiguous_exit_is_not_guessed():
    wb = workbook()
    wb["Trade Log"].append(list(wb["Trade Log"].values)[-1])
    assert audit_workbook(wb)["findings"][0]["status"] == "UNRESOLVED_EXIT_IDENTITY"


def test_no_events_is_supported():
    wb = workbook()
    del wb["Position Events"]
    assert audit_workbook(wb)["events_checked"] == 0


def test_provenance_quarantines_identity_issue_without_correcting_finances():
    p = audit_workbook(workbook())["measurement_provenance"]
    assert p["trades"][0]["status"] == "NEEDS_RECONCILIATION"
    assert p["cash_or_pnl_corrected"] is False
    assert p["downstream_capital_effects_replayed"] is False


def test_passing_identity_is_not_claimed_as_validated_execution():
    p = audit_workbook(workbook("2026-09-08T13:00:00Z"))["measurement_provenance"]
    assert p["trades"][0]["status"] == "IDENTITY_CHECK_ONLY"
    assert p["trades"][0]["fill_prices_validated"] is False


def test_unmatched_findings_and_no_event_trades_are_explicit():
    wb = workbook()
    wb["Trade Log"].append(list(wb["Trade Log"].values)[-1])
    p = audit_workbook(wb)["measurement_provenance"]
    assert p["unattributed_findings"] == 1
    assert p["trades"][0]["status"] == "NO_MATCHED_MONITOR_EVIDENCE"


def test_accounting_reconciliation_separates_open_partial_profit():
    wb = workbook("2026-09-08T13:00:00Z")
    trade_log = wb["Trade Log"]
    buy = [None] * 21
    buy[0:16] = [
        "2026-09-09T14:00:00Z", "BUY_SIMULATED", "OPEN", 100, None, 10, 1,
        1000, 0, 1000, 0, 95, 110, 120, 50, "entry",
    ]
    buy[20] = "open-trade"
    trade_log.append(buy)
    partial = [None] * 21
    partial[0:16] = [
        "2026-09-10T14:00:00Z", "TAKE_PARTIAL_PROFIT", "OPEN", 100, 110, 5, 1,
        0, 550, 0, 550, 95, 110, 120, 0, "partial",
    ]
    partial[20] = "open-trade"
    trade_log.append(partial)

    reconciliation = audit_workbook(wb)["accounting_reconciliation"]

    assert reconciliation["closed_trade_realized_pnl_ils"] == 0
    assert reconciliation["open_lot_partial_realized_pnl_ils"] == 50
    assert reconciliation["all_lifecycle_realized_pnl_ils"] == 50
    assert reconciliation["exit_event_realized_pnl_ils"] == 50
    assert reconciliation["reconciliation_delta_ils"] == 0
    assert reconciliation["reconciled"] is True
    assert reconciliation["cash_or_pnl_corrected"] is False
