from types import SimpleNamespace

import pytest

import agent.send_telegram_diagnostic as diagnostic


def test_diagnostic_sends_message_without_chart(monkeypatch) -> None:
    calls = []
    monkeypatch.setenv("MARKET_LENS_TELEGRAM_DIAGNOSTIC_MESSAGE", "<b>TEST</b>")
    monkeypatch.setenv("MARKET_LENS_TELEGRAM_DIAGNOSTIC_DEDUPE_KEY", "test-key")
    monkeypatch.delenv("MARKET_LENS_TELEGRAM_DIAGNOSTIC_CHART_OBJECT", raising=False)
    monkeypatch.setattr(
        diagnostic,
        "send_telegram_message",
        lambda message, *, dedupe_key: calls.append((message, dedupe_key))
        or SimpleNamespace(sent=True, status="sent"),
    )

    diagnostic.main()

    assert calls == [("<b>TEST</b>", "test-key")]


def test_diagnostic_rejects_unsafe_chart_object() -> None:
    with pytest.raises(SystemExit, match="invalid"):
        diagnostic.send_chart_from_git(
            "HEAD:chart.png;echo-bad",
            ticker="TEST",
            dedupe_key="test-key|chart",
        )


def test_diagnostic_accepts_expected_historical_chart_object() -> None:
    assert diagnostic.GIT_OBJECT_RE.fullmatch(
        "5815f71c^:agent_results/charts/market_lens_agent_20260903_140255_gs.png"
    )
