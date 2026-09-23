from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import main


def test_live_price_metadata_labels_regular_and_stale_quotes() -> None:
    regular = main.live_price_metadata(
        "2026-09-22T14:00:00+00:00",
        observed_at=datetime(2026, 9, 22, 14, 1, tzinfo=timezone.utc),
    )
    assert regular == {
        "live_price_session": "REGULAR",
        "live_price_session_label": "Regular session",
        "live_price_age_seconds": 60,
        "live_price_freshness": "FRESH",
    }

    stale = main.live_price_metadata(
        "2026-09-22T23:00:00+00:00",
        observed_at=datetime(2026, 9, 23, 5, 0, tzinfo=timezone.utc),
    )
    assert stale["live_price_session"] == "AFTER_HOURS"
    assert stale["live_price_freshness"] == "STALE"
    assert stale["live_price_age_seconds"] == 21600


def test_live_price_endpoint_preserves_tracker_mark_and_quote_provenance(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "current_agent_dashboard",
        lambda: {
            "status": "ok",
            "summary": {"cash_ils": 9000, "starting_capital_ils": 10000},
            "open_positions": [{
                "ticker": "TEST", "entry_price_usd": 100, "current_price_usd": 101,
                "quantity": 10, "stop_loss": 95, "target_1": 110, "target_2": 120,
                "notes": "",
            }],
        },
    )
    monkeypatch.setattr(main, "fetch_live_price", lambda _ticker: (102.5, "2026-09-22T14:00:00+00:00"))
    response = TestClient(main.app).get("/agent/live-prices")
    assert response.status_code == 200
    payload = response.json()
    position = payload["open_positions"][0]
    assert position["current_price_usd"] == 102.5
    assert position["persisted_price_usd"] == 101
    assert position["live_price_source"] == "1m intraday/prepost"
    assert position["live_price_session"] == "REGULAR"
    assert position["price_display_basis"] == "LATEST_AVAILABLE_INTRADAY_QUOTE"
    assert payload["prices"]["TEST"]["persisted_price_usd"] == 101
    assert "one-minute high/low" in payload["quote_contract"]["monitoring"]


def test_live_price_endpoint_labels_tracker_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "current_agent_dashboard",
        lambda: {
            "status": "ok",
            "summary": {"cash_ils": 9000, "starting_capital_ils": 10000},
            "open_positions": [{
                "ticker": "TEST", "entry_price_usd": 100, "current_price_usd": 101,
                "quantity": 10, "stop_loss": 95, "target_1": 110, "target_2": 120,
            }],
        },
    )
    monkeypatch.setattr(main, "fetch_live_price", lambda _ticker: (_ for _ in ()).throw(ValueError("provider unavailable")))
    payload = TestClient(main.app).get("/agent/live-prices").json()
    position = payload["open_positions"][0]
    assert position["current_price_usd"] == 101
    assert position["price_display_basis"] == "PERSISTED_TRACKER_FALLBACK"
    assert position["live_price_session_label"] == "Saved tracker mark"
    assert payload["warnings"] == {"TEST": "provider unavailable"}

