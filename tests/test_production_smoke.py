from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

import app.main as main
from agent.production_smoke import (
    SmokeFailure,
    decision_jsonl_url,
    revisions_match,
    validate_chart,
    validate_dashboard,
    validate_decision_jsonl,
    validate_health,
    validate_monitor_state,
    validate_read_only_scan,
    validate_tracker,
)
from app.models import ScanResult


def test_health_exposes_render_revision_without_requiring_it(monkeypatch) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123def456")

    response = TestClient(main.app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "revision": "abc123def456"}


def test_read_only_ui_scan_generates_chart_without_persisting(monkeypatch, tmp_path) -> None:
    result = ScanResult(
        ticker="MSFT",
        setup_type="No Trade",
        score=0.0,
        current_price=100.0,
        buy_zone=(0.0, 0.0),
        stop_loss=0.0,
        target_1=0.0,
        target_2=0.0,
        risk_reward=0.0,
        reason="Smoke fixture",
    )
    detail = SimpleNamespace(result=result)
    monkeypatch.setenv("MARKET_LENS_AUTH_MODE", "open")
    monkeypatch.setattr(main, "CHART_DIR", tmp_path)
    monkeypatch.setattr(main, "scan_tickers", lambda *_args, **_kwargs: ([result], {}, [detail]))
    monkeypatch.setattr(main, "apply_strategy_decisions", lambda results, **_kwargs: results)

    def fake_chart(_detail, output_dir):
        path = output_dir / "msft.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 2_000)
        return path

    monkeypatch.setattr(main, "write_scan_chart", fake_chart)
    monkeypatch.setattr(
        main,
        "save_setup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("read-only smoke must not persist a setup")
        ),
    )

    response = TestClient(main.app).post(
        "/ui/scan",
        json={
            "tickers": ["MSFT"],
            "include_charts": True,
            "persist_setups": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["saved_setups"] == []
    assert payload["charts"] == {"MSFT": "/charts/msft.png"}


def test_ui_scan_keeps_existing_persistence_default(monkeypatch) -> None:
    result = ScanResult(
        ticker="MSFT",
        setup_type="Breakout + Retest",
        score=0.7,
        current_price=100.0,
        buy_zone=(99.0, 101.0),
        stop_loss=95.0,
        target_1=110.0,
        target_2=120.0,
        risk_reward=2.5,
        reason="Persistence fixture",
    )
    detail = SimpleNamespace(result=result)
    saved = []
    monkeypatch.setenv("MARKET_LENS_AUTH_MODE", "open")
    monkeypatch.setattr(main, "scan_tickers", lambda *_args, **_kwargs: ([result], {}, [detail]))
    monkeypatch.setattr(main, "apply_strategy_decisions", lambda results, **_kwargs: results)

    def fake_save_setup(saved_result, **_kwargs):
        saved.append(saved_result.ticker)
        return {"ticker": saved_result.ticker}

    monkeypatch.setattr(main, "save_setup", fake_save_setup)

    response = TestClient(main.app).post(
        "/ui/scan",
        json={"tickers": ["MSFT"], "include_charts": False},
    )

    assert response.status_code == 200
    assert saved == ["MSFT"]
    assert response.json()["saved_setups"] == [{"ticker": "MSFT"}]


def test_revision_matching_accepts_full_and_short_hashes() -> None:
    assert revisions_match("abcdef1234567890", "abcdef123456")
    assert revisions_match("abcdef123456", "abcdef1234567890")
    assert not revisions_match("abcdef123456", "999999123456")


def test_health_validation_waits_for_expected_revision() -> None:
    validate_health({"status": "ok", "revision": "abcdef1234567890"}, "abcdef123456")

    with pytest.raises(SmokeFailure, match="does not match"):
        validate_health({"status": "ok", "revision": "old"}, "abcdef123456")


def test_dashboard_validation_rejects_fake_zero_and_fatal_markers() -> None:
    healthy = {
        "status": "ok",
        "latest_run": {
            "run_id": "run-1",
            "run_status": "PARTIAL_OK",
            "scan_coverage": {"received": 133, "requested": 136},
            "summary_text": "Run status: PARTIAL_OK",
        },
    }
    assert "results=133" in validate_dashboard(healthy)

    complete = json.loads(json.dumps(healthy))
    complete["latest_run"]["run_status"] = "COMPLETE"
    assert "status=COMPLETE" in validate_dashboard(complete)

    zero = json.loads(json.dumps(healthy))
    zero["latest_run"]["scan_coverage"]["received"] = 0
    with pytest.raises(SmokeFailure, match="zero result"):
        validate_dashboard(zero)

    fatal = json.loads(json.dumps(healthy))
    fatal["latest_run"]["summary_text"] = "AUTH_FAILED"
    with pytest.raises(SmokeFailure, match="fatal run marker"):
        validate_dashboard(fatal)


def test_decision_jsonl_validation_requires_unique_complete_records() -> None:
    body = (
        json.dumps({"ticker": "MSFT", "final_action": "SKIP", "reason": "No setup"})
        + "\n"
        + json.dumps({"ticker": "NVDA", "final_action": "WATCH", "reason": "Waiting"})
        + "\n"
    ).encode()
    assert validate_decision_jsonl(body) == 2

    duplicate = body + (
        json.dumps({"ticker": "MSFT", "final_action": "SKIP", "reason": "Again"}) + "\n"
    ).encode()
    with pytest.raises(SmokeFailure, match="duplicate ticker"):
        validate_decision_jsonl(duplicate)


def test_decision_url_uses_safe_explicit_path_or_run_id_fallback() -> None:
    explicit = "/agent-results/decisions/custom.jsonl"
    assert decision_jsonl_url({"decision_jsonl_url": explicit}) == explicit
    assert decision_jsonl_url({"run_id": "20260924_113146"}) == (
        "/agent-results/decisions/market_lens_agent_20260924_113146.jsonl"
    )
    with pytest.raises(SmokeFailure, match="safe Decision"):
        decision_jsonl_url({"run_id": "../../secret"})


def test_tracker_validation_opens_required_sheets() -> None:
    workbook = Workbook()
    workbook.active.title = "Dashboard"
    for title in ("Trade Log", "Open Positions", "Position Events"):
        workbook.create_sheet(title)
    buffer = io.BytesIO()
    workbook.save(buffer)

    sheets = validate_tracker(buffer.getvalue())

    assert set(("Dashboard", "Trade Log", "Open Positions", "Position Events")) <= set(sheets)


def test_read_only_scan_and_chart_contracts() -> None:
    chart_url = validate_read_only_scan(
        {
            "results": [{"ticker": "MSFT"}],
            "errors": {},
            "charts": {"MSFT": "/charts/msft.png"},
            "saved_setups": [],
        },
        "MSFT",
    )
    assert chart_url == "/charts/msft.png"
    validate_chart(
        b"\x89PNG\r\n\x1a\n" + b"x" * 2_000,
        {"content-type": "image/png"},
    )

    with pytest.raises(SmokeFailure, match="persisted"):
        validate_read_only_scan(
            {
                "results": [{"ticker": "MSFT"}],
                "charts": {"MSFT": "/charts/msft.png"},
                "saved_setups": [{"ticker": "MSFT"}],
            },
            "MSFT",
        )


def test_monitor_validation_is_read_only_and_accepts_off_hours_history() -> None:
    payload = {
        "system_health": {
            "latest_monitor_status": "MONITOR_OK",
            "latest_monitor_positions_checked": 5,
            "latest_monitor_positions_failed": 0,
            "latest_monitor_event_count": 0,
            "latest_monitor_age_minutes": 500,
        }
    }
    detail = validate_monitor_state(payload, max_age_minutes=0)
    assert "dispatch_attempted=false" in detail
    assert "events=0" in detail

    with pytest.raises(SmokeFailure, match="failed position"):
        payload["system_health"]["latest_monitor_positions_failed"] = 1
        validate_monitor_state(payload, max_age_minutes=0)
