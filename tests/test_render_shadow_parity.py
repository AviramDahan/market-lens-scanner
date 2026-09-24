from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from agent.render_shadow_parity import collect_parity


NOW = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)


def shadow_status(*events):
    return {
        "mode": "shadow",
        "side_effects_enabled": False,
        "event_journal": list(events),
    }


def shadow_event(**overrides):
    event = {
        "event_id": "shadow_fixture",
        "ticker": "MSFT",
        "event_type": "TAKE_PARTIAL_PROFIT",
        "position_id": "trade-1",
        "threshold": 110.0,
        "live_price": 110.1,
        "live_high": 110.2,
        "live_low": 109.8,
        "observed_at": "2026-09-24T13:59:00+00:00",
        "first_seen_at": "2026-09-24T14:00:00+00:00",
        "reason": "Target 1 touched.",
    }
    event.update(overrides)
    return event


def active_event(**overrides):
    event = {
        "ticker": "MSFT",
        "action": "TAKE_PARTIAL_PROFIT",
        "trade_id": "trade-1",
        "trigger_price": 110.0,
        "triggered_at": "2026-09-24T13:59:00+00:00",
    }
    event.update(overrides)
    return event


def read_records(output_dir):
    paths = list(output_dir.glob("*.jsonl"))
    assert len(paths) == 1
    return [json.loads(line) for line in paths[0].read_text(encoding="utf-8").splitlines()]


def test_shadow_event_is_persisted_once_without_repeated_write(tmp_path) -> None:
    event = shadow_event()
    first = collect_parity(
        shadow_status=shadow_status(event),
        outbox_payload={},
        active_events=[],
        output_dir=tmp_path,
        trigger={},
        now=NOW,
    )
    second = collect_parity(
        shadow_status=shadow_status({**event, "detection_count": 20}),
        outbox_payload={},
        active_events=[],
        output_dir=tmp_path,
        trigger={},
        now=NOW + timedelta(minutes=5),
    )

    assert first["changed"] is True
    assert second["changed"] is False
    assert read_records(tmp_path)[0]["classification"] == "SHADOW_ONLY_PENDING"


def test_active_persisted_event_matches_shadow_and_records_lag(tmp_path) -> None:
    result = collect_parity(
        shadow_status=shadow_status(shadow_event()),
        outbox_payload={"run_id": "monitor-123", "timestamp": "2026-09-24T14:01:00+00:00"},
        active_events=[active_event()],
        output_dir=tmp_path,
        trigger={
            "run_id": "123",
            "ticker": "MSFT",
            "event": "TAKE_PARTIAL_PROFIT",
            "source": "agent-server-live-monitor",
        },
        now=NOW + timedelta(minutes=1),
    )

    record = read_records(tmp_path)[0]
    assert result["classifications"] == {"MATCHED": 1}
    assert record["classification"] == "MATCHED"
    assert record["persistence_lag_seconds"] == 60
    assert record["active_trigger"]["source"] == "agent-server-live-monitor"
    assert record["active_persistence"]["run_id"] == "monitor-123"


def test_active_event_without_shadow_is_classified_active_only(tmp_path) -> None:
    result = collect_parity(
        shadow_status=shadow_status(),
        outbox_payload={"run_id": "monitor-124", "timestamp": NOW.isoformat()},
        active_events=[active_event()],
        output_dir=tmp_path,
        trigger={},
        now=NOW,
    )

    assert result["classifications"] == {"ACTIVE_ONLY": 1}
    assert read_records(tmp_path)[0]["shadow"] is None


def test_pending_shadow_event_expires_only_after_horizon(tmp_path) -> None:
    event = shadow_event(first_seen_at=(NOW - timedelta(minutes=31)).isoformat())
    result = collect_parity(
        shadow_status=shadow_status(event),
        outbox_payload={},
        active_events=[],
        output_dir=tmp_path,
        trigger={},
        now=NOW,
        pending_horizon_seconds=1800,
    )

    assert result["classifications"] == {"SHADOW_ONLY_EXPIRED": 1}


def test_malformed_shadow_payload_cannot_be_persisted(tmp_path) -> None:
    try:
        collect_parity(
            shadow_status={"mode": "active", "side_effects_enabled": True},
            outbox_payload={},
            active_events=[],
            output_dir=tmp_path,
            trigger={},
            now=NOW,
        )
    except ValueError as exc:
        assert "Unsafe" in str(exc)
    else:
        raise AssertionError("unsafe shadow payload must be rejected")
    assert not list(tmp_path.glob("*.jsonl"))
