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
