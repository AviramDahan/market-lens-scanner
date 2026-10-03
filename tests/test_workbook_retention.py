from pathlib import Path
import zipfile

import pytest
from openpyxl import Workbook, load_workbook

from app.workbook_retention import (
    DEFAULT_TRACKER_REWRITE_BYTES,
    DEFAULT_WATCHLIST_MAX_ROWS,
    compact_setup_watchlist,
    enforce_tracker_size,
    save_workbook_atomically,
)


def test_workbook_retention_defaults_keep_operational_tracker_below_github_warning() -> None:
    assert DEFAULT_WATCHLIST_MAX_ROWS == 20_000
    assert DEFAULT_TRACKER_REWRITE_BYTES == 50_000_000


def test_compact_setup_watchlist_keeps_header_and_latest_rows() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Setup Watchlist"
    worksheet.append(["timestamp", "ticker"])
    for index in range(1, 8):
        worksheet.append([f"2026-08-{index:02d}", f"T{index}"])

    result = compact_setup_watchlist(workbook, max_rows=3)

    assert result == {
        "enabled": True,
        "rows_before": 7,
        "rows_after": 3,
        "rows_removed": 4,
        "max_rows": 3,
    }
    assert [row[1].value for row in worksheet.iter_rows(min_row=2)] == ["T5", "T6", "T7"]


def test_compact_setup_watchlist_can_be_disabled() -> None:
    workbook = Workbook()
    workbook.active.title = "Setup Watchlist"
    workbook["Setup Watchlist"].append(["timestamp"])
    workbook["Setup Watchlist"].append(["2026-08-01"])

    result = compact_setup_watchlist(workbook, max_rows=0)

    assert result["enabled"] is False
    assert workbook["Setup Watchlist"].max_row == 2


def test_enforce_tracker_size_rewrites_and_keeps_latest_rows(tmp_path) -> None:
    tracker = tmp_path / "tracker.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Setup Watchlist"
    worksheet.append(["timestamp", "ticker"])
    for index in range(1, 8):
        worksheet.append([f"2026-08-{index:02d}", f"T{index}"])
    workbook.save(tracker)
    workbook.close()

    result = enforce_tracker_size(
        tracker,
        rewrite_bytes=1,
        hard_limit_bytes=1_000_000,
        max_rows=3,
    )

    assert result["rewritten"] is True
    loaded = load_workbook(tracker, read_only=True)
    try:
        assert [row[1].value for row in loaded["Setup Watchlist"].iter_rows(min_row=2)] == [
            "T5",
            "T6",
            "T7",
        ]
    finally:
        loaded.close()


def test_atomic_save_keeps_previous_tracker_when_serialization_fails(tmp_path) -> None:
    tracker = tmp_path / "tracker.xlsx"
    tracker.write_bytes(b"previous valid tracker")

    class FailingWorkbook:
        def save(self, path: Path) -> None:
            path.write_bytes(b"partial workbook")
            raise OSError(28, "No space left on device")

    with pytest.raises(OSError, match="No space left"):
        save_workbook_atomically(FailingWorkbook(), tracker)

    assert tracker.read_bytes() == b"previous valid tracker"
    assert list(tmp_path.glob("*.tmp.xlsx")) == []


def test_atomic_save_rejects_corrupt_zip_without_replacing_tracker(tmp_path) -> None:
    tracker = tmp_path / "tracker.xlsx"
    tracker.write_bytes(b"previous valid tracker")

    class CorruptWorkbook:
        def save(self, path: Path) -> None:
            path.write_bytes(b"not an XLSX archive")

    with pytest.raises(zipfile.BadZipFile):
        save_workbook_atomically(CorruptWorkbook(), tracker)

    assert tracker.read_bytes() == b"previous valid tracker"


def test_atomic_save_writes_valid_tracker(tmp_path) -> None:
    tracker = tmp_path / "tracker.xlsx"
    workbook = Workbook()
    workbook.active["A1"] = "before"
    workbook.save(tracker)
    workbook.active["A1"] = "after"

    save_workbook_atomically(workbook, tracker)
    workbook.close()

    loaded = load_workbook(tracker, read_only=True)
    try:
        assert loaded.active["A1"].value == "after"
    finally:
        loaded.close()
