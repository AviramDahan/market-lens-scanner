"""Fake-clock contention checks; no real portfolio, provider or Telegram calls."""
import json

import pytest

from deploy.hetzner import run_runtime as runtime


def fake_contention(tmp_path, monkeypatch, release_at):
    clock = [1000.0]

    class Locks:
        LOCK_EX, LOCK_NB, LOCK_UN = 1, 2, 4

        @staticmethod
        def flock(handle, flags):
            if handle.name.endswith("writer.lock") and flags != Locks.LOCK_UN and clock[0] < release_at:
                raise BlockingIOError

    monkeypatch.setattr(runtime, "STATE", tmp_path)
    monkeypatch.setattr(runtime, "fcntl", Locks)
    monkeypatch.setattr(runtime, "WRITER_OWNER", None)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    return clock


def test_monitor_passes_soft_threshold_without_losing_queued_evaluation(tmp_path, monkeypatch, capsys):
    clock = fake_contention(tmp_path, monkeypatch, 1075)
    evaluated = []
    with runtime.writer_lock(priority=True):
        evaluated.append(clock[0])
    assert evaluated == [1075]
    assert "DEFERRED_LOCK" in capsys.readouterr().out
    status = json.loads((tmp_path / "coordination/monitor_wait.json").read_text())
    assert status == {"pid": runtime.os.getpid(), "status": "ACQUIRED", "wait_seconds": 75.0}
    assert not (tmp_path / "coordination/writer.json").exists()
    assert not (tmp_path / "monitor").exists()  # Waiting is not a successful evaluation heartbeat.
    assert not (tmp_path / "outbox").exists()


def test_monitor_respects_owner_absolute_deadline_without_renewing(tmp_path, monkeypatch):
    clock = fake_contention(tmp_path, monkeypatch, 2000)
    record = {"pid": 123, "phase": "RECEIPT_CHECKPOINT", "deadline_monotonic": 1010}
    runtime.coordination_record(tmp_path / "coordination/writer.json", record)
    with pytest.raises(RuntimeError, match="writer lock timed out"):
        with runtime.writer_lock(priority=True):
            pytest.fail("An overrun must not enter the financial critical section")
    assert clock[0] == 1010
    wait = json.loads((tmp_path / "coordination/monitor_wait.json").read_text())
    assert wait["status"] == "LOCK_DEADLINE_EXCEEDED"
    assert json.loads((tmp_path / "coordination/writer.json").read_text()) == record
    assert not (tmp_path / "monitor").exists()


def test_expired_owner_deadline_is_not_reset_by_new_waiter(tmp_path, monkeypatch):
    clock = fake_contention(tmp_path, monkeypatch, 2000)
    runtime.coordination_record(tmp_path / "coordination/writer.json",
                                {"phase": "CHECKPOINT", "deadline_monotonic": 999})
    with pytest.raises(RuntimeError, match="writer lock timed out"):
        with runtime.writer_lock(priority=True):
            pytest.fail("Expired writer transaction was given a fresh lease")
    assert clock[0] == 1000


def test_monitor_unknown_owner_still_has_a_fixed_hard_bound(tmp_path, monkeypatch):
    clock = fake_contention(tmp_path, monkeypatch, 2000)
    with pytest.raises(RuntimeError, match="writer lock timed out"):
        with runtime.writer_lock(priority=True):
            pytest.fail("Unknown lock owner must not cause unlimited waiting")
    assert clock[0] == 1000 + runtime.MONITOR_LOCK_WAIT_SECONDS


def test_scanner_survives_healthy_monitor_longer_than_soft_threshold(tmp_path, monkeypatch, capsys):
    clock = fake_contention(tmp_path, monkeypatch, 1075)
    with runtime.writer_lock():
        assert clock[0] == 1075
    assert "Scanner writer queue status=DEFERRED_LOCK" in capsys.readouterr().out
    status = json.loads((tmp_path / "coordination/scanner_wait.json").read_text())
    assert status["status"] == "ACQUIRED"
    assert status["wait_seconds"] == 75
    assert not (tmp_path / "coordination/monitor_wait.json").exists()
    assert not (tmp_path / "monitor").exists()
    assert not (tmp_path / "outbox").exists()


@pytest.mark.parametrize("priority", [False, True])
def test_queue_handles_reservation_contention_with_a_hard_deadline(tmp_path, monkeypatch, priority):
    clock = fake_contention(tmp_path, monkeypatch, 1075)
    original = runtime.fcntl.flock

    def reserve(handle, flags):
        if handle.name.endswith("monitor-priority.lock") and flags != runtime.fcntl.LOCK_UN:
            if clock[0] < 1075:
                raise BlockingIOError
        return original(handle, flags)

    monkeypatch.setattr(runtime.fcntl, "flock", reserve)
    with runtime.writer_lock(priority=priority):
        assert clock[0] == 1075
    kind = "monitor" if priority else "scanner"
    assert json.loads((tmp_path / f"coordination/{kind}_wait.json").read_text())["status"] == "ACQUIRED"


def test_scanner_unknown_owner_does_not_wait_forever(tmp_path, monkeypatch):
    clock = fake_contention(tmp_path, monkeypatch, 2000)
    with pytest.raises(RuntimeError, match="writer lock timed out"):
        with runtime.writer_lock():
            pytest.fail("Queue budget must stay bounded")
    assert clock[0] == 1000 + runtime.SCANNER_QUEUE_WAIT_SECONDS
    assert json.loads((tmp_path / "coordination/scanner_wait.json").read_text())["status"] == "LOCK_DEADLINE_EXCEEDED"


def test_scanner_preserves_owner_transaction_deadline(tmp_path, monkeypatch):
    clock = fake_contention(tmp_path, monkeypatch, 2000)
    runtime.coordination_record(tmp_path / "coordination/writer.json",
                                {"phase": "CHECKPOINT", "deadline_monotonic": 1010})
    with pytest.raises(RuntimeError, match="writer lock timed out"):
        with runtime.writer_lock():
            pytest.fail("Do not renew the monitor transaction")
    assert clock[0] == 1010


def test_lock_cannot_be_acquired_after_deadline_when_sleep_wakes_late(tmp_path, monkeypatch):
    clock = fake_contention(tmp_path, monkeypatch, 1001.1)
    monkeypatch.setattr(runtime.time, "sleep", lambda _: clock.__setitem__(0, 1001.2))
    with pytest.raises(RuntimeError, match="writer lock timed out"):
        with runtime.writer_lock(timeout=1):
            pytest.fail("Late wakeup must not grant an expired writer turn")
    assert runtime.WRITER_OWNER is None


def test_explicit_short_timeout_remains_authoritative(tmp_path, monkeypatch):
    clock = fake_contention(tmp_path, monkeypatch, 1075)
    with pytest.raises(RuntimeError):
        with runtime.writer_lock(priority=True, timeout=1):
            pytest.fail("Explicit test/maintenance timeout was ignored")
    assert clock[0] == 1001
