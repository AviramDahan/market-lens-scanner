from __future__ import annotations

import os
from typing import Any


DEFAULT_WATCHLIST_MAX_ROWS = 80_000


def compact_setup_watchlist(
    workbook: Any,
    *,
    max_rows: int | None = None,
) -> dict[str, int | bool]:
    """Keep the operational Excel tracker below GitHub's file-size ceiling.

    Full scan history remains available in decision JSONL and performance summaries.
    The workbook only needs a rolling window large enough for carry-forward selection,
    dashboard diagnostics, and recent manual review.
    """
    configured_max = max_rows
    if configured_max is None:
        configured_max = _env_int(
            "MARKET_LENS_WORKBOOK_WATCHLIST_MAX_ROWS",
            DEFAULT_WATCHLIST_MAX_ROWS,
        )

    if configured_max <= 0 or "Setup Watchlist" not in workbook.sheetnames:
        return {
            "enabled": False,
            "rows_before": 0,
            "rows_after": 0,
            "rows_removed": 0,
            "max_rows": configured_max,
        }

    worksheet = workbook["Setup Watchlist"]
    rows_before = max(0, worksheet.max_row - 1)
    rows_to_remove = max(0, rows_before - configured_max)
    if rows_to_remove:
        worksheet.delete_rows(2, rows_to_remove)

    return {
        "enabled": True,
        "rows_before": rows_before,
        "rows_after": rows_before - rows_to_remove,
        "rows_removed": rows_to_remove,
        "max_rows": configured_max,
    }


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
