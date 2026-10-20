"""Linux parent-death regression with real locks and an offline archive store."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
THIS_FILE = Path(__file__).resolve()
WAIT_SECONDS = 20
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Requires Linux fork and flock")

if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))


class FakeStore:
    repository = "qa/fictional-checkpoint-processes"

    def __init__(self, root: Path, on_verified=None):
        self.root = root
        self.on_verified = on_verified

    def require_private(self):
        pass

    def release(self, tag, create=False):
        return tag.removeprefix("host-v1-")

    def put(self, release, name, path):
        destination = self.root / release / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        body = path.read_bytes()
        if destination.exists():
            assert destination.read_bytes() == body, "Fake immutable asset changed"
        else:
            destination.write_bytes(body)

    def get(self, generation, name, output, *, expected_sha=None, expected_bytes=None):
        body = (self.root / generation / name).read_bytes()
        if expected_sha is not None:
            assert hashlib.sha256(body).hexdigest() == expected_sha
        if expected_bytes is not None:
            assert len(body) == expected_bytes
        output.write_bytes(body)
        if name == "verified.json" and self.on_verified is not None:
            self.on_verified(generation)


def _deny_network(event, _args):
    if event.startswith("socket."):
        raise AssertionError("The checkpoint process regression must stay offline")


def _write_json(path: Path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


def _wait_for_file(path: Path, timeout=WAIT_SECONDS):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for {path.name}")
        time.sleep(0.02)


def _wait_pid(pid: int, timeout=WAIT_SECONDS):
    deadline = time.monotonic() + timeout
    while True:
        finished, status = os.waitpid(pid, os.WNOHANG)
        if finished:
            return os.waitstatus_to_exitcode(status)
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for child {pid}")
        time.sleep(0.02)


def _assert_locked(path: Path):
    import fcntl

    with path.open("a") as probe:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        fcntl.flock(probe, fcntl.LOCK_UN)
    raise AssertionError(f"Parent A did not hold {path.name}")


def _make_source(root: Path):
    from deploy.hetzner import archive_checkpoint as checkpoint
    from openpyxl import Workbook

    repo, state = root / "repo", root / "state"
    (repo / "agent_results").mkdir(parents=True)
    (repo / "agent_tracker").mkdir()
    (state / "outbox").mkdir(parents=True)
    (state / "deployed_revision").write_text("qa-fictional-revision", encoding="utf-8")
    (repo / "agent_results/dashboard_snapshot.json").write_text('{"status":"ok"}', encoding="utf-8")
    (repo / "agent_results/telegram_notifications.jsonl").write_text("", encoding="utf-8")
    workbook = Workbook()
    try:
        workbook.active.title = "Open Positions"
        workbook.active.append(["ticker", "quantity"])
        workbook.active.append(["QA_FIXTURE", 2])
        workbook.create_sheet("Trade Log").append(["ticker", "action"])
        workbook.save(repo / checkpoint.TRACKER.removeprefix("repo/"))
    finally:
        workbook.close()
    return repo, state


def _publisher_child(root: Path, arguments: list[str]):
    from deploy.hetzner import archive_checkpoint as checkpoint

    scratch = Path(arguments[arguments.index("--scratch") + 1])

    def pause_before_proof_return(generation):
        _write_json(root / "publisher-ready.json", {
            "pid": os.getpid(), "parent": os.getppid(),
            "generation": generation, "scratch": str(scratch),
        })
        _wait_for_file(root / "resume-publisher", timeout=60)

    store = FakeStore(root / "fake-archive", pause_before_proof_return)
    original = checkpoint.Checkpoints
    checkpoint.Checkpoints = lambda repo, state: original(repo, state, store=store)
    checkpoint.REPO, checkpoint.STATE = root / "repo", root / "state"
    sys.argv = [str(THIS_FILE), *arguments]
    return checkpoint.main()


def _parent_a(root: Path):
    from deploy.hetzner import archive_checkpoint as checkpoint
    from deploy.hetzner.github_archive import GitHubArchive

    class ChildTransportStore(FakeStore, GitHubArchive):
        """Select child publication without constructing a network client."""

    def offline_child(command, **kwargs):
        assert command[1:4] == ["-m", "deploy.hetzner.archive_checkpoint", "publish-frozen"]
        redirected = [sys.executable, str(THIS_FILE), "--publisher-child", str(root), *command[3:]]
        try:
            return subprocess.run(redirected, **kwargs)
        except subprocess.CalledProcessError as error:
            print(error.stderr, file=sys.stderr)
            raise

    # Only the transport is redirected; save, pending state, locks and cursor checks are real.
    checkpoint.subprocess = SimpleNamespace(run=offline_child,
        TimeoutExpired=subprocess.TimeoutExpired, CalledProcessError=subprocess.CalledProcessError)
    state = root / "state"
    with checkpoint.exclusive(state / "writer.lock"):
        _write_json(state / "outbox/scanner.json", {
            "schema_version": 1,
            "records": [{"ticker": "QA_FIXTURE", "message": "Never send this fixture",
                         "dedupe_key": "qa-process-alert"}],
        })
        checkpoints = checkpoint.Checkpoints(root / "repo", state,
                                             store=ChildTransportStore(root / "fake-archive"))
        checkpoints.save("qa-parent-a")


def _scenario(root: Path):
    from deploy.hetzner import archive_checkpoint as checkpoint

    # This dedicated supervisor adopts/reaps the orphan instead of relying on PID 1.
    libc = ctypes.CDLL(None, use_errno=True)
    assert libc.prctl(36, 1, 0, 0, 0) == 0, f"PR_SET_CHILD_SUBREAPER failed: {ctypes.get_errno()}"
    children = []
    try:
        checkpoint.require_headroom = lambda *_args: None
        repo, state = _make_source(root)
        checkpoints = checkpoint.Checkpoints(repo, state, store=FakeStore(root / "fake-archive"))
        cursor = checkpoints.root / "last_verified.json"
        tracker = repo / checkpoint.TRACKER.removeprefix("repo/")
        tracker_before = tracker.read_bytes()
        with checkpoint.exclusive(state / "writer.lock"):
            baseline = checkpoints.save("qa-baseline", bootstrap=True)

        parent = os.fork()
        if parent == 0:
            try:
                _parent_a(root)
            except BaseException:
                traceback.print_exc()
                os._exit(1)
            os._exit(0)
        children.append(parent)
        ready = root / "publisher-ready.json"
        _wait_for_file(ready)
        publisher = json.loads(ready.read_text(encoding="utf-8"))
        children.append(publisher["pid"])
        assert publisher["parent"] == parent
        assert publisher["generation"] != baseline["manifest"]["generation"]
        _assert_locked(state / "writer.lock")
        _assert_locked(checkpoints.root / "publisher.lock")
        assert json.loads(cursor.read_text())["manifest"] == baseline["manifest"]

        os.kill(parent, signal.SIGKILL)  # Kill only the lock owner, never its publisher.
        assert _wait_pid(parent) == -signal.SIGKILL
        children.remove(parent)
        os.kill(publisher["pid"], 0)
        assert not (Path(publisher["scratch"]) / "verified-result.json").exists()

        with checkpoint.exclusive(state / "writer.lock"):
            recovered = checkpoints.recover()
            assert recovered["manifest"]["generation"] == publisher["generation"]
            assert recovered["proof"]["delivery_state"] == "VERIFIED_PENDING_DELIVERY"
            # Simulate the existing consumer's receipt/unlink without calling Telegram.
            (repo / checkpoint.RECEIPTS.removeprefix("repo/")).write_text(
                '{"key":"qa-process-alert","sent_at":"2026-10-20T00:00:00Z"}\n', encoding="utf-8")
            (state / "outbox/scanner.json").unlink()
            authoritative = checkpoints.save("qa-authoritative-b")
        assert authoritative["manifest"]["generation"] != publisher["generation"]
        assert authoritative["proof"]["delivery_state"] == "COMPLETE"
        assert not (checkpoints.root / "pending.json").exists()
        cursor_before_resume = cursor.read_bytes()

        (root / "resume-publisher").touch()
        assert _wait_pid(publisher["pid"]) == 0
        children.remove(publisher["pid"])
        result = Path(publisher["scratch"]) / "verified-result.json"
        assert json.loads(result.read_text())["manifest"]["generation"] == publisher["generation"]
        assert cursor.read_bytes() == cursor_before_resume, "Orphan A rolled the authoritative cursor back"
        assert checkpoints.receipt()["manifest"] == authoritative["manifest"]
        assert not (state / "outbox/scanner.json").exists()
        assert tracker.read_bytes() == tracker_before
    finally:
        for pid in children:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for pid in children:
            try:
                _wait_pid(pid, timeout=5)
            except ChildProcessError:
                pass


def test_orphaned_publisher_cannot_replace_newer_verified_cursor(tmp_path):
    process = subprocess.Popen(
        [sys.executable, str(THIS_FILE), "--scenario", str(tmp_path)], cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    try:
        try:
            stdout, stderr = process.communicate(timeout=90)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate(timeout=5)
            pytest.fail(f"Checkpoint process scenario timed out\n{stdout}\n{stderr}")
        assert process.returncode == 0, f"Checkpoint process scenario failed\n{stdout}\n{stderr}"
    finally:
        # Also stop a child that failed before announcing its PID to the supervisor.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            process.communicate(timeout=5)


if __name__ == "__main__":
    sys.addaudithook(_deny_network)
    try:
        if sys.argv[1] == "--scenario":
            _scenario(Path(sys.argv[2]))
            raise SystemExit(0)
        if sys.argv[1] == "--publisher-child":
            raise SystemExit(_publisher_child(Path(sys.argv[2]), sys.argv[3:]))
        raise ValueError("Unknown isolated process role")
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
