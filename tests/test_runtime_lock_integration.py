"""Real Linux flock tests; no Docker, network, portfolio, or Telegram access."""
import subprocess
import sys
import time
from pathlib import Path

import pytest

from deploy.hetzner import run_runtime as runtime


pytestmark = pytest.mark.skipif(runtime.fcntl is None, reason="Production flock requires Linux")
ROOT = Path(__file__).resolve().parents[1]


def start(state, label, priority, timeout=4):
    script = """import sys,time
from pathlib import Path
from deploy.hetzner import run_runtime as r
r.STATE=Path(sys.argv[1])
try:
    with r.writer_lock(timeout=float(sys.argv[4]), priority=sys.argv[3]=='true'):
        with (r.STATE/'order').open('a') as f:
            f.write(sys.argv[2]+'\\n')
        time.sleep(.15)
except RuntimeError:
    sys.exit(9)
"""
    return subprocess.Popen([sys.executable, "-c", script, str(state), label,
                             str(priority).lower(), str(timeout)], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def stop(process):
    if process.poll() is None:
        process.kill()
    process.communicate(timeout=5)


def test_lock_timeout_cannot_enter_another_writers_critical_section(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "STATE", tmp_path)
    child = None
    try:
        with runtime.writer_lock():
            child = start(tmp_path, "not-allowed", True, timeout=.1)
            child.communicate(timeout=5)
            assert child.returncode == 9
            assert not (tmp_path / "order").exists()
    finally:
        if child:
            stop(child)
    with runtime.writer_lock(priority=True):
        pass  # The failed waiter did not leak either lock.


def test_waiting_monitor_has_next_turn_before_new_scanner(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "STATE", tmp_path)
    children = []
    try:
        with runtime.writer_lock():
            children.append(start(tmp_path, "monitor", True))
            deadline = time.monotonic() + 3
            with (tmp_path / "monitor-priority.lock").open("a") as probe:
                while True:
                    try:
                        runtime.fcntl.flock(probe, runtime.fcntl.LOCK_EX | runtime.fcntl.LOCK_NB)
                        runtime.fcntl.flock(probe, runtime.fcntl.LOCK_UN)
                    except BlockingIOError:
                        break
                    assert time.monotonic() < deadline, "Monitor never reserved priority"
                    time.sleep(.02)
            children.append(start(tmp_path, "scanner", False))
        for process in children:
            out, err = process.communicate(timeout=6)
            assert process.returncode == 0, out + err
        assert (tmp_path / "order").read_text().splitlines() == ["monitor", "scanner"]
    finally:
        for process in children:
            stop(process)


def test_owner_death_releases_kernel_lock_without_trusting_old_metadata(tmp_path):
    code = """import sys,time
from pathlib import Path
from deploy.hetzner import run_runtime as r
r.STATE=Path(sys.argv[1])
with r.writer_lock():
    (r.STATE/'entered').touch()
    time.sleep(10)
"""
    owner = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], cwd=ROOT,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    waiter = None
    try:
        deadline = time.monotonic() + 4
        while not (tmp_path / "entered").exists():
            assert time.monotonic() < deadline
            time.sleep(.02)
        assert (tmp_path / "coordination/writer.json").exists()
        owner.kill()
        owner.communicate(timeout=5)
        waiter = start(tmp_path, "monitor-after-owner-death", True, timeout=2)
        out, err = waiter.communicate(timeout=5)
        assert waiter.returncode == 0, out + err
        assert (tmp_path / "order").read_text().strip() == "monitor-after-owner-death"
        assert not (tmp_path / "coordination/writer.json").exists()
    finally:
        stop(owner)
        if waiter:
            stop(waiter)
