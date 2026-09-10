from pathlib import Path
import subprocess

import pytest

from agent.persistence_guard import assert_state_unchanged


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    (tmp_path / "agent_tracker").mkdir()
    (tmp_path / "agent_tracker/state.xlsx").write_bytes(b"original")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "baseline")
    return tmp_path, git(tmp_path, "rev-parse", "HEAD")


def commit_file(root, name, contents):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    git(root, "add", name)
    git(root, "commit", "-qm", "remote change")


def test_source_only_change_and_local_generated_state_are_allowed(repo):
    root, base = repo
    commit_file(root, "app/source.py", "new source")
    tracker = root / "agent_tracker/state.xlsx"
    tracker.write_bytes(b"local generated portfolio")
    assert_state_unchanged(base, "HEAD", cwd=root)
    assert tracker.read_bytes() == b"local generated portfolio"


@pytest.mark.parametrize("path", ["agent_tracker/state.xlsx", "agent_results/dashboard_snapshot.json",
                                      "agent_results/telegram_notifications.jsonl"])
def test_remote_state_change_blocks_without_modifying_worktree(repo, path):
    root, base = repo
    commit_file(root, path, "remote update")
    before = git(root, "rev-parse", "HEAD")
    with pytest.raises(RuntimeError, match="PERSISTENCE_CONFLICT"):
        assert_state_unchanged(base, "HEAD", cwd=root)
    assert git(root, "rev-parse", "HEAD") == before
    assert (root / path).read_text() == "remote update"


def test_missing_ref_fails_closed(repo):
    root, base = repo
    with pytest.raises(RuntimeError, match="PERSISTENCE_CHECK_FAILED"):
        assert_state_unchanged(base, "missing-ref", cwd=root)


def test_retry_compares_original_baseline_not_local_result_commit(repo):
    root, base = repo
    commit_file(root, "agent_tracker/state.xlsx", "intervening monitor exit")
    with pytest.raises(RuntimeError, match="PERSISTENCE_CONFLICT"):
        assert_state_unchanged(base, "HEAD", cwd=root)


@pytest.mark.parametrize("name", ["market-lens-agent.yml", "market-lens-position-monitor.yml"])
def test_every_workflow_reset_has_guard(name):
    path = Path(__file__).resolve().parents[1] / ".github/workflows" / name
    lines = path.read_text().splitlines()
    assert sum('BASE_REV="$(git rev-parse HEAD)"' in line for line in lines) == 1
    resets = [i for i, line in enumerate(lines) if "git reset --hard origin/main" in line]
    assert len(resets) == 2
    for index in resets:
        assert 'python -m agent.persistence_guard "$BASE_REV" origin/main' in lines[index - 1]
