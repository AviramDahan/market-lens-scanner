import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from deploy.hetzner import notify_ops, run_runtime


class Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_runtime_alert_is_sent_once_and_contains_no_error_details(tmp_path, monkeypatch):
    monkeypatch.setattr(notify_ops, "env_value", lambda name: {
        "MARKET_LENS_TELEGRAM_BOT_TOKEN": "secret-token",
        "MARKET_LENS_TELEGRAM_OPS_CHAT_ID": "-1004259898393",
    }.get(name, ""))
    requests = []

    def fake_open(request, timeout):
        requests.append((request, timeout))
        return Response()

    when = datetime(2026, 10, 4, tzinfo=timezone.utc)
    path = tmp_path / "alert.json"
    assert notify_ops.send_runtime_alert("scanner", "RUN_FAILED", now=when, state=path, opener=fake_open) == "sent"
    assert notify_ops.send_runtime_alert("scanner", "RUN_FAILED", now=when + timedelta(minutes=10), state=path, opener=fake_open) == "duplicate"
    assert len(requests) == 1
    payload = json.loads(requests[0][0].data)
    assert payload["chat_id"] == "-1004259898393"
    assert "secret-token" not in payload["text"]
    assert "RUN_FAILED" in payload["text"]
    assert json.loads(path.read_text())["sent_at"] == when.isoformat()


def test_failed_delivery_remains_retryable(tmp_path, monkeypatch):
    monkeypatch.setattr(notify_ops, "env_value", lambda name: "token" if name.endswith("BOT_TOKEN") else "-1004259898393")
    path = tmp_path / "alert.json"
    when = datetime(2026, 10, 4, tzinfo=timezone.utc)

    def fail(_request, *, timeout):
        raise OSError("network down")

    assert notify_ops.send_runtime_alert("monitor", "RUN_FAILED", now=when, state=path, opener=fail) == "failed"
    assert not path.read_text()
    assert notify_ops.send_runtime_alert("monitor", "RUN_FAILED", now=when, state=path, opener=lambda _request, *, timeout: Response()) == "sent"


def test_missing_configuration_does_not_send(tmp_path, monkeypatch):
    monkeypatch.setattr(notify_ops, "env_value", lambda _name: "")
    assert notify_ops.send_runtime_alert("scanner", "RUN_FAILED", state=tmp_path / "alert.json") == "not_configured"
    assert not (tmp_path / "alert.json").exists()


def test_post_persistence_notification_failure_has_distinct_event(monkeypatch, tmp_path):
    monkeypatch.setattr(run_runtime, "STATE", tmp_path)
    (tmp_path / "outbox").mkdir()
    (tmp_path / "outbox/scanner.json").write_text("{}")
    monkeypatch.setattr(run_runtime, "worker", lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "worker")))
    with pytest.raises(run_runtime.RuntimeDeliveryFailure, match="trade alert delivery failed"):
        run_runtime.deliver("scanner")
    assert (tmp_path / "outbox/scanner.json").exists()


def test_degraded_monitor_reports_issue_without_mutating_portfolio(tmp_path, monkeypatch):
    monkeypatch.setattr(run_runtime, "REPO", tmp_path)
    monkeypatch.setattr(run_runtime, "STATE", tmp_path / "state")
    tracker = tmp_path / "tracker.xlsx"
    tracker.write_bytes(b"unchanged")
    monkeypatch.setattr(run_runtime, "TRACKER", tracker)
    heartbeat = run_runtime.STATE / "monitor/latest_status.json"
    heartbeat.parent.mkdir(parents=True)
    heartbeat.write_text(json.dumps({"status": "MONITOR_DEGRADED", "event_count": 0}))
    monkeypatch.setattr(run_runtime, "require_live_preflight", lambda: None)

    def fake_worker(*_args, **_kwargs):
        stat = heartbeat.stat()
        import os
        os.utime(heartbeat, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    monkeypatch.setattr(run_runtime, "worker", fake_worker)
    alerts = []
    monkeypatch.setattr(run_runtime, "report_runtime_alert", lambda kind, event: alerts.append((kind, event)))
    monkeypatch.setattr(run_runtime, "persist", lambda *_: pytest.fail("No-op monitor persisted"))
    run_runtime.execute("monitor")
    assert alerts == [("monitor", "MONITOR_DEGRADED")]
    assert tracker.read_bytes() == b"unchanged"


@pytest.mark.parametrize("failure,event", [
    (RuntimeError("scan failed"), "RUN_FAILED"),
    (ValueError("malformed monitor heartbeat"), "RUN_FAILED"),
    (run_runtime.RuntimeDeliveryFailure("delivery failed"), "TRADE_ALERT_DELIVERY_FAILED"),
])
def test_runtime_failure_routes_one_operations_alert(monkeypatch, failure, event):
    alerts = []
    monkeypatch.setattr(run_runtime, "main", lambda: (_ for _ in ()).throw(failure))
    monkeypatch.setattr(run_runtime, "report_runtime_alert", lambda kind, code: alerts.append((kind, code)))
    monkeypatch.setattr(sys, "argv", ["run_runtime.py", "monitor"])
    assert run_runtime.cli() == 1
    assert alerts == [("monitor", event)]
