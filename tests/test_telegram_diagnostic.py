from types import SimpleNamespace
from pathlib import Path

import pytest

import agent.send_telegram_diagnostic as diagnostic


WORKFLOW = Path(".github/workflows/market-lens-telegram-diagnostic.yml")


def test_diagnostic_workflow_runs_agent_as_module() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "python -m agent.send_telegram_diagnostic" in text
    assert "python agent/send_telegram_diagnostic.py" not in text


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
        "cd20dfd5d89af2652515219bab0ea996efea7bc2:"
        "agent_results/charts/market_lens_agent_20260903_140255_gs.png"
    )
