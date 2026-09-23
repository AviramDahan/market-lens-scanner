from __future__ import annotations

from typing import Any, Iterable


COMPLETE = "COMPLETE"
PARTIAL_OK = "PARTIAL_OK"
FAILED = "FAILED"
AUTH_FAILED = "AUTH_FAILED"
SUCCESSFUL_RUN_STATUSES = {COMPLETE, PARTIAL_OK}
TERMINAL_FAILURE_STATUSES = {FAILED, AUTH_FAILED}


def build_scan_coverage(
    *,
    requested: int,
    received: int,
    missing_tickers: Iterable[str] | None = None,
) -> dict[str, Any]:
    missing = sorted({str(ticker).strip().upper() for ticker in (missing_tickers or []) if str(ticker).strip()})
    requested_count = max(0, int(requested or 0))
    received_count = max(0, int(received or 0))
    if requested_count == 0:
        coverage_pct = 100.0 if received_count == 0 else 0.0
    else:
        coverage_pct = min(100.0, round(received_count / requested_count * 100, 2))
    return {
        "requested": requested_count,
        "received": received_count,
        "missing": len(missing),
        "missing_tickers": missing,
        "coverage_pct": coverage_pct,
    }


def classify_run_status(
    *,
    auth_failed: bool,
    scan_completed: bool,
    requested: int,
    received: int,
    missing_tickers: Iterable[str] | None = None,
    nonfatal_errors: Iterable[str] | None = None,
) -> str:
    if auth_failed:
        return AUTH_FAILED
    if not scan_completed or (int(requested or 0) > 0 and int(received or 0) == 0):
        return FAILED
    if list(missing_tickers or []) or list(nonfatal_errors or []):
        return PARTIAL_OK
    return COMPLETE


def normalize_run_status(
    value: Any,
    *,
    scan_complete: bool | None = None,
    scan_status: str = "",
    received: int = 0,
    requested: int = 0,
    missing_tickers: Iterable[str] | None = None,
) -> str:
    """Map historical and current run metadata to the canonical taxonomy."""
    raw = str(value or "").strip().upper()
    status_text = str(scan_status or "").strip().lower()
    if raw == AUTH_FAILED or "auth_failed" in status_text:
        return AUTH_FAILED
    if raw in {FAILED, "RUN_FAILED"} or status_text == "failed":
        return FAILED
    if status_text.startswith("completed:") and int(received or 0) == 0:
        return FAILED

    missing = list(missing_tickers or [])
    is_partial = scan_complete is False or bool(missing) or "unavailable" in status_text or raw in {PARTIAL_OK, "ISSUES"}
    has_results = int(received or 0) > 0
    scan_succeeded = (
        status_text.startswith("completed:")
        or raw in {COMPLETE, PARTIAL_OK, "OK", "ISSUES"}
        or (not raw and not status_text and has_results)
    )
    if is_partial and has_results:
        return PARTIAL_OK
    if scan_succeeded and (has_results or int(requested or 0) == 0):
        return COMPLETE
    return FAILED
