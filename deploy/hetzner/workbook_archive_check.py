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
        for name in REQUIRED_SHEETS:
            for _ in workbook[name].values:
                pass  # Iteration, not just sheet names, validates cell decoding.
    finally:
        workbook.close()
    return {"parser": "openpyxl", "validated_sheets": list(REQUIRED_SHEETS)}


if __name__ == "__main__":
    print(json.dumps(validate_workbook(Path(sys.argv[1]))))
