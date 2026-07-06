from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main
from agent.market_lens_ui_agent import auth_config_is_open


def scan_result_payload() -> dict:
    return {
        "ticker": "MSFT",
        "setup_type": "Breakout + Retest",
        "score": 0.75,
        "current_price": 100.0,
        "buy_zone": [99.0, 101.0],
        "stop_loss": 95.0,
        "target_1": 110.0,
        "target_2": 120.0,
        "risk_reward": 2.5,
        "reason": "test setup",
    }


def test_auth_config_defaults_to_open_even_when_supabase_env_exists(monkeypatch) -> None:
    monkeypatch.setenv("MARKET_LENS_AUTH_MODE", "open")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "publishable")

    client = TestClient(main.app)
    response = client.get("/auth/config")
    payload = response.json()

    assert response.status_code == 200
    assert payload["enabled"] is False
    assert payload["mode"] == "open"


def test_agent_treats_open_auth_config_as_open_access() -> None:
    assert auth_config_is_open({"enabled": False, "mode": "open"})
    assert auth_config_is_open({"enabled": False})
    assert auth_config_is_open({"enabled": True, "mode": "disabled"})
    assert not auth_config_is_open({"enabled": True, "mode": "supabase"})
    assert not auth_config_is_open(None)


def test_scan_endpoint_allows_anonymous_open_access(monkeypatch) -> None:
    monkeypatch.setenv("MARKET_LENS_AUTH_MODE", "open")
    monkeypatch.setattr(main, "scan_tickers", lambda *_args, **_kwargs: ([], {}, []))
    monkeypatch.setattr(main, "apply_strategy_decisions", lambda results, **_kwargs: results)

    client = TestClient(main.app)
    response = client.post("/scan", json={"tickers": ["MSFT"], "include_charts": False})

    assert response.status_code == 200
    assert response.json() == {"results": [], "errors": {}}


def test_manual_save_uses_global_storage_in_open_access(monkeypatch) -> None:
    monkeypatch.setenv("MARKET_LENS_AUTH_MODE", "open")
    monkeypatch.setattr(main, "using_external_storage", lambda: True)
    captured = {}

    def fake_save_setup(result, **kwargs):
        captured.update(kwargs)
        return {"ticker": result.ticker, "source": kwargs["source"]}

    monkeypatch.setattr(main, "save_setup", fake_save_setup)

    client = TestClient(main.app)
    response = client.post(
        "/setups",
        json={
            "result": scan_result_payload(),
            "analysis_period": "6mo",
            "chart_url": "/charts/test.png",
            "session_id": "browser-session",
        },
    )

    assert response.status_code == 200
    assert response.json()["setup"]["source"] == "auto"
    assert captured["source"] == "auto"
    assert captured["user_label"] == "open-access"
