from pathlib import Path

from app import results_sync


def reset_results_sync_cache() -> None:
    results_sync._LAST_SYNC_AT = 0.0
    results_sync._LAST_SYNC_RESULT = {"enabled": False, "reason": "test reset"}


def test_results_sync_disabled_inside_github_actions_by_default(monkeypatch, tmp_path: Path) -> None:
    reset_results_sync_cache()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_ACTIONS_TRIGGER_TOKEN", "token")
    monkeypatch.delenv("MARKET_LENS_RESULTS_SYNC_ENABLED", raising=False)

    result = results_sync.sync_agent_results_if_enabled(tmp_path)

    assert result["enabled"] is False
    assert "GitHub Actions" in result["reason"]


def test_results_sync_downloads_tracker_and_recent_results(monkeypatch, tmp_path: Path) -> None:
    reset_results_sync_cache()
    monkeypatch.setenv("MARKET_LENS_RESULTS_SYNC_ENABLED", "true")
    monkeypatch.setenv("GITHUB_ACTIONS_TRIGGER_TOKEN", "token")
    monkeypatch.setenv("MARKET_LENS_RESULTS_SYNC_TTL_SECONDS", "0")

    def fake_limited_directory_file_metadata(repo, ref, directory, limit, suffixes):
        if directory == "agent_results/decisions":
            path = "agent_results/decisions/market_lens_agent_latest.jsonl"
            return [{"type": "file", "path": path, "sha": f"sha-{path}", "download_url": f"https://example.test/{path}"}]
        if directory == "agent_results/summaries":
            path = "agent_results/summaries/daily_summary_latest.json"
            return [{"type": "file", "path": path, "sha": f"sha-{path}", "download_url": f"https://example.test/{path}"}]
        return []

    def fake_metadata(repo, ref, path):
        return {"type": "file", "sha": f"sha-{path}", "download_url": f"https://example.test/{path}"}

    def fake_download(url, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"downloaded {url}", encoding="utf-8")

    monkeypatch.setattr(results_sync, "limited_directory_file_metadata", fake_limited_directory_file_metadata)
    monkeypatch.setattr(results_sync, "github_file_metadata", fake_metadata)
    monkeypatch.setattr(results_sync, "download_to_path", fake_download)

    result = results_sync.sync_agent_results_if_enabled(tmp_path)

    assert result["enabled"] is True
    assert result["downloaded"] == 3
    assert (tmp_path / "agent_tracker" / results_sync.TRACKER_NAME).exists()
    assert (tmp_path / "agent_results" / "decisions" / "market_lens_agent_latest.jsonl").exists()
    assert (tmp_path / "agent_results" / "summaries" / "daily_summary_latest.json").exists()


def test_results_sync_skips_unchanged_files(monkeypatch, tmp_path: Path) -> None:
    reset_results_sync_cache()
    monkeypatch.setenv("MARKET_LENS_RESULTS_SYNC_ENABLED", "true")
    monkeypatch.setenv("GITHUB_ACTIONS_TRIGGER_TOKEN", "token")
    monkeypatch.setenv("MARKET_LENS_RESULTS_SYNC_TTL_SECONDS", "0")
    monkeypatch.setattr(results_sync, "limited_directory_file_metadata", lambda *_args, **_kwargs: [])

    tracker = tmp_path / "agent_tracker" / results_sync.TRACKER_NAME
    tracker.parent.mkdir(parents=True)
    tracker.write_text("existing", encoding="utf-8")
    results_sync.save_state(tmp_path, {f"agent_tracker/{results_sync.TRACKER_NAME}": "same-sha"})

    monkeypatch.setattr(
        results_sync,
        "github_file_metadata",
        lambda *_args, **_kwargs: {
            "type": "file",
            "sha": "same-sha",
            "download_url": "https://example.test/tracker.xlsx",
        },
    )

    def fail_download(*_args, **_kwargs):
        raise AssertionError("unchanged files should not be downloaded")

    monkeypatch.setattr(results_sync, "download_to_path", fail_download)

    result = results_sync.sync_agent_results_if_enabled(tmp_path)

    assert result["downloaded"] == 0
    assert result["skipped"] == 1
