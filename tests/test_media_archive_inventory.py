import subprocess

from agent.media_archive_inventory import inventory


def test_inventory_uses_committed_blobs_without_touching_local_files(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()
    git("init", "-q")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.invalid")
    media = tmp_path / "agent_results/charts/a space.png"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"fixture")
    (media.parent / "notes.json").write_text("{}")
    git("add", ".")
    git("commit", "-qm", "fixture")
    original = git("rev-parse", "HEAD:agent_results/charts/a space.png")
    media.write_bytes(b"unsaved new bytes")
    report = inventory(tmp_path)
    assert report["file_count"] == 1
    assert report["total_bytes"] is None
    assert report["files"][0]["git_blob_oid"] == original
    assert report["archive_verified"] is False
    assert media.read_bytes() == b"unsaved new bytes"
    assert git("diff", "--cached", "--name-only") == ""
