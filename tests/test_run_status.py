from __future__ import annotations

from app.run_status import (
    AUTH_FAILED,
    COMPLETE,
    FAILED,
    PARTIAL_OK,
    build_scan_coverage,
    classify_run_status,
    normalize_run_status,
)


def test_complete_scan_is_complete() -> None:
    assert classify_run_status(
        auth_failed=False,
        scan_completed=True,
        requested=100,
        received=100,
    ) == COMPLETE


def test_partial_scan_keeps_usable_results() -> None:
    assert classify_run_status(
        auth_failed=False,
        scan_completed=True,
        requested=100,
        received=97,
        missing_tickers=["ARM", "MMC", "RDDT"],
    ) == PARTIAL_OK


def test_zero_result_scan_is_failed() -> None:
    assert classify_run_status(
        auth_failed=False,
        scan_completed=True,
        requested=100,
        received=0,
        missing_tickers=["ARM"],
    ) == FAILED


def test_auth_failure_has_its_own_status() -> None:
    assert classify_run_status(
        auth_failed=True,
        scan_completed=False,
        requested=0,
        received=0,
    ) == AUTH_FAILED


def test_historical_ok_plus_unavailable_normalizes_to_partial_ok() -> None:
    assert normalize_run_status(
        "OK",
        scan_complete=False,
        scan_status="completed: 136 results; 3 unavailable",
        requested=139,
        received=136,
    ) == PARTIAL_OK


def test_historical_ok_complete_normalizes_to_complete() -> None:
    assert normalize_run_status(
        "OK",
        scan_complete=True,
        scan_status="completed: 139 results",
        requested=139,
        received=139,
    ) == COMPLETE


def test_historical_fake_zero_normalizes_to_failed() -> None:
    assert normalize_run_status(
        "OK",
        scan_complete=True,
        scan_status="completed: 0 results",
        requested=0,
        received=0,
    ) == FAILED


def test_coverage_is_structured_and_deduplicates_symbols() -> None:
    assert build_scan_coverage(
        requested=139,
        received=136,
        missing_tickers=["arm", "ARM", "MMC", "RDDT"],
    ) == {
        "requested": 139,
        "received": 136,
        "missing": 3,
        "missing_tickers": ["ARM", "MMC", "RDDT"],
        "coverage_pct": 97.84,
    }
