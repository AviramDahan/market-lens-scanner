from pathlib import Path
import subprocess

from agent.generated_state_bundle import apply_bundle, save_bundle, stage_bundle


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def init_repo(root: Path) -> None:
    git(root, "init", "-q")
    git(root, "config", "user.name", "test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "agent_tracker").mkdir()
    (root / "agent_results" / "decisions").mkdir(parents=True)
    (root / "agent_tracker" / "tracker.xlsx").write_bytes(b"old tracker")
    (root / "agent_results" / "decisions" / "old.jsonl").write_text("old", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "baseline")


def test_bundle_saves_applies_and_stages_only_changed_state(tmp_path: Path) -> None:
    init_repo(tmp_path)
    bundle = tmp_path.parent / f"{tmp_path.name}_bundle"

    (tmp_path / "agent_tracker" / "tracker.xlsx").write_bytes(b"new tracker")
    (tmp_path / "agent_results" / "decisions" / "new.jsonl").write_text("new", encoding="utf-8")
    (tmp_path / "agent_results" / "decisions" / "old.jsonl").unlink()

    save_bundle(
        bundle,
        ["agent_tracker", "agent_results"],
        cwd=tmp_path,
        max_files=10,
        max_bytes=1024 * 1024,
    )

    git(tmp_path, "reset", "--hard", "HEAD")
    git(tmp_path, "clean", "-fd")
    assert not (tmp_path / "agent_results" / "decisions" / "new.jsonl").exists()
    assert (tmp_path / "agent_results" / "decisions" / "old.jsonl").exists()

    apply_bundle(bundle, cwd=tmp_path)
    stage_bundle(bundle, cwd=tmp_path)

    assert (tmp_path / "agent_tracker" / "tracker.xlsx").read_bytes() == b"new tracker"
    assert (tmp_path / "agent_results" / "decisions" / "new.jsonl").read_text(encoding="utf-8") == "new"
    assert not (tmp_path / "agent_results" / "decisions" / "old.jsonl").exists()
    cached = git(tmp_path, "diff", "--cached", "--name-only")
    assert "agent_tracker/tracker.xlsx" in cached
    assert "agent_results/decisions/new.jsonl" in cached
    assert "agent_results/decisions/old.jsonl" in cached


def test_bundle_limits_fail_closed(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "agent_results" / "big.json").write_bytes(b"x" * 20)

    try:
        save_bundle(
            tmp_path.parent / f"{tmp_path.name}_bundle",
            ["agent_results"],
            cwd=tmp_path,
            max_files=10,
            max_bytes=10,
        )
    except RuntimeError as exc:
        assert "bytes exceeds" in str(exc)
    else:
        raise AssertionError("Expected generated state byte limit to fail closed")
