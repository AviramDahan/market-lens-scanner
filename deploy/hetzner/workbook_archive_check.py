"""Validate the actual portfolio worksheets with the application's Excel parser."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REQUIRED_SHEETS = ("Open Positions", "Trade Log")


def validate_workbook(path: Path) -> dict:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        if not set(REQUIRED_SHEETS).issubset({sheet.title for sheet in workbook.worksheets}):
            raise ValueError("Required portfolio worksheets missing")
        for sheet in workbook.worksheets:
            sheet.reset_dimensions()  # Do not trust a stale/truncated declared range.
            for _ in sheet.values:
                pass  # Iteration, not just sheet names, validates cell decoding.
    finally:
        workbook.close()
    return {"parser": "openpyxl", "validated_sheets": list(REQUIRED_SHEETS), "all_worksheets_decoded": True}


if __name__ == "__main__":
    print(json.dumps(validate_workbook(Path(sys.argv[1]))))
