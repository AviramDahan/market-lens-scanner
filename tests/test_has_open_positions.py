from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from agent.has_open_positions import workbook_has_open_positions


def write_workbook(path: Path, ticker: str = "") -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Open Positions"
    ws.append(["Ticker", "Entry Date", "Entry Price USD"])
    if ticker:
        ws.append([ticker, "2026-06-22T18:33:05", 58.02])
    wb.save(path)


def test_workbook_has_open_positions_detects_inline_string_ticker(tmp_path: Path) -> None:
    workbook_path = tmp_path / "portfolio.xlsx"
    write_workbook(workbook_path, "BALL")

    assert workbook_has_open_positions(workbook_path) is True


def test_workbook_has_open_positions_returns_false_when_sheet_is_empty(tmp_path: Path) -> None:
    workbook_path = tmp_path / "portfolio.xlsx"
    write_workbook(workbook_path)

    assert workbook_has_open_positions(workbook_path) is False
