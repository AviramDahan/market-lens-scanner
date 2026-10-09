from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from agent import send_weak_sector_review as review
from app.telegram_notifications import TelegramSendResult, TelegramSettings, send_telegram_message


def clock(minutes: int = 0) -> datetime:
    return review.DUE + timedelta(minutes=minutes)


def test_not_sent_early_even_when_data_is_missing() -> None:
    calls = []
    status = review.run_review(
        now=clock(-1), observations=None, latest_run=None, receipts="",
        send=lambda message: calls.append(message), persist=lambda: calls.append("persist"),
    )
    assert status == "not_due"
    assert calls == []


def test_zero_signals_still_sends_and_persists_receipt(monkeypatch) -> None:
    rows = [{"session_date": "2026-10-02", "timestamp": "2026-10-02T19:58:37",
             "setup_type": "Fib", "session_group": "REGULAR", "cohort": "WEAK_SIGNAL",
             "applicable": True}]
    summary = {"qualifying_signal_count": 0, "matched_control_count": 0,
               "signal_results": {}, "control_results": {}, "weak_ineligibility_reasons":
               {"FAIL:entry_confirmation": 1}, "review_ready": False}
    monkeypatch.setattr(review, "summarize", lambda _data: (rows, summary))
    messages = []
    committed = []

    def send(message):
        messages.append(message)
        return TelegramSendResult(True, "sent")

    assert review.run_review(
        now=clock(), observations="saved data", latest_run={"run_id": "run-1", "timestamp": "2026-10-02T20:00:00", "run_status": "PARTIAL_OK"}, receipts="", send=send,
        persist=lambda: committed.append(review.KEY),
    ) == "sent"
    assert len(messages) == 1
    assert review.INTRO in messages[0]
    assert "אותות כשירים: 0" in messages[0]
    assert "2026-10-02T19:58:37" in messages[0]
    assert "run-1" in messages[0]
    assert "אין מדגם כשיר" in messages[0]
    assert committed == [review.KEY]

    persisted = json.dumps({"key": review.KEY, "sent_at": clock().isoformat()})
    assert review.run_review(
        now=clock(1), observations="saved data", latest_run=None, receipts=persisted, send=send,
        persist=lambda: committed.append(review.KEY),
    ) == "already_sent"
    assert len(messages) == 1


def test_failed_send_retries_on_next_run(monkeypatch) -> None:
    monkeypatch.setattr(review, "summarize", lambda _data: ([], {
        "qualifying_signal_count": 0, "matched_control_count": 0,
        "weak_ineligibility_reasons": {}, "review_ready": False,
    }))
    attempts = []
    persisted = []

    def send(message):
        attempts.append(message)
        return TelegramSendResult(len(attempts) == 2, "sent" if len(attempts) == 2 else "failed")

    with pytest.raises(RuntimeError, match="delivery failed"):
        review.run_review(now=clock(), observations="saved data", latest_run=None, receipts="", send=send,
                          persist=lambda: persisted.append(True))
    assert persisted == []
    assert review.run_review(now=clock(60), observations="saved data", latest_run=None, receipts="",
                             send=send, persist=lambda: persisted.append(True)) == "sent"
    assert len(attempts) == 2
    assert persisted == [True]


def test_summary_failure_sends_failure_notice_at_due(monkeypatch) -> None:
    def broken(_data):
        raise ValueError("sensitive provider details")

    monkeypatch.setattr(review, "summarize", broken)
    messages = []
    assert review.run_review(
        now=clock(24 * 60), observations="saved data", latest_run=None, receipts="",
        send=lambda message: (messages.append(message), TelegramSendResult(True, "sent"))[1],
        persist=lambda: None,
    ) == "sent"
    assert "סיכום המדידה נכשל" in messages[0]
    assert "ValueError" in messages[0]
    assert "sensitive provider details" not in messages[0]


def test_existing_summary_command_reads_persisted_observations() -> None:
    observation = {
        "signal_id": "2026-10-02|TEST|Fib|STANDARD",
        "timestamp": "2026-10-02T14:30:00",
        "session_date": "2026-10-02",
        "session_group": "REGULAR",
        "ticker": "TEST",
        "setup_type": "Fib",
        "entry_path": "STANDARD",
        "cohort": "WEAK_SIGNAL",
        "applicable": True,
        "status": "INELIGIBLE",
        "signal_eligible": False,
        "control_eligible": False,
        "ineligibility_reasons": ["FAIL:entry_confirmation"],
    }
    rows, summary = review.summarize(json.dumps(observation) + "\n")
    assert rows == [observation]
    assert summary["observation_count"] == 1
    assert summary["applicable_observation_count"] == 1
    assert summary["qualifying_signal_count"] == 0
    assert summary["weak_ineligibility_reasons"] == {"FAIL:entry_confirmation": 1}
    assert summary["data_quality_warnings"]
    message = review.format_review(rows, summary, now=clock(), latest_run=None)
    assert "אינו הוכחה שלא היו הזדמנויות" in message


def test_failure_notice_is_retried_when_telegram_fails() -> None:
    with pytest.raises(RuntimeError, match="delivery failed"):
        review.run_review(
            now=clock(), observations=None, latest_run=None, receipts="",
            send=lambda _message: TelegramSendResult(False, "failed"),
            persist=lambda: pytest.fail("Failed send must not be persisted"),
        )


def test_missing_outcome_fields_are_not_rendered_as_zero(monkeypatch) -> None:
    monkeypatch.setattr(review, "summarize", lambda _data: ([{"session_date": "2026-10-02"}], {
        "qualifying_signal_count": 1,
        "matched_control_count": 0,
        "signal_results": {},
        "weak_ineligibility_reasons": {},
        "review_ready": False,
    }))
    messages = []
    assert review.run_review(
        now=clock(), observations="saved data", latest_run=None, receipts="",
        send=lambda message: (messages.append(message), TelegramSendResult(True, "sent"))[1],
        persist=lambda: None,
    ) == "sent"
    assert "סיכום המדידה נכשל" in messages[0]
    assert "סגורות 0" not in messages[0]


def test_closed_open_and_unassessable_outcomes_are_distinct() -> None:
    rows = [{"signal_id": "one", "session_date": "2026-10-02",
             "timestamp": "2026-10-02T14:30:00", "applicable": True}]
    summary = {
        "qualifying_signal_count": 3,
        "matched_control_count": 2,
        "signal_results": {"closed": 1, "censored_open": 1, "unassessable": 1},
        "control_results": {"closed": 0, "censored_open": 2, "unassessable": 0},
        "weak_ineligibility_reasons": {},
        "review_ready": False,
        "market_data_errors": {"TEST": "unavailable"},
    }
    message = review.format_review(rows, summary, now=clock(), latest_run=None)
    assert "אותות: סגורות 1, פתוחות/מצונזרות 1, ללא נתוני תוצאה מספיקים 1" in message
    assert "ביקורת: סגורות 0, פתוחות/מצונזרות 2" in message
    assert "נתוני שוק חסרים (מספר טיקרים: 1)" in message


def test_persistence_error_does_not_report_success(monkeypatch) -> None:
    monkeypatch.setattr(review, "summarize", lambda _data: ([], {
        "qualifying_signal_count": 0,
        "matched_control_count": 0,
        "weak_ineligibility_reasons": {},
        "review_ready": False,
    }))
    with pytest.raises(RuntimeError, match="receipt push failed"):
        review.run_review(
            now=clock(), observations="saved data", latest_run=None, receipts="",
            send=lambda _message: TelegramSendResult(True, "sent"),
            persist=lambda: (_ for _ in ()).throw(RuntimeError("receipt push failed")),
        )


def test_topic_is_forwarded_only_for_this_message() -> None:
    captured = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def opener(request, timeout):
        captured.append(json.loads(request.data))
        return Response()

    settings = TelegramSettings(bot_token="fake", chat_id="-100", timeout_seconds=2)
    assert send_telegram_message("test", settings=settings, opener=opener,
                                 message_thread_id=42).sent
    assert captured[0]["chat_id"] == "-100"
    assert captured[0]["message_thread_id"] == 42
    assert send_telegram_message("test", settings=settings, opener=opener).sent
    assert "message_thread_id" not in captured[1]


def test_workflow_has_unconditional_due_check_and_serialized_delivery() -> None:
    workflow = (review.ROOT / ".github/workflows/market-lens-weak-sector-review.yml").read_text()
    assert 'cron: "0 7 * * *"' in workflow
    assert "group: market-lens-repo-writes" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "market-lens-agent.yml" not in workflow
    assert "MARKET_LENS_TELEGRAM_CHAT_ID" in workflow
