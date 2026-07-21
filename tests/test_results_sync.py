from pathlib import Path

from app import results_sync


def reset_results_sync_cache() -> None:
    results_sync._LAST_SYNC_AT = 0.0
    results_sync._LAST_SYNC_RESULT = {"enabled": False, "reason": "test reset"}
    results_sync._LAST_SNAPSHOT_SYNC_AT = 0.0
    results_sync._LAST_SNAPSHOT_SYNC_RESULT = {"enabled": False, "reason": "test reset"}
    results_sync._LAST_SNAPSHOT_ASSET_SYNC_AT = 0.0
    results_sync._LAST_SNAPSHOT_ASSET_SYNC_RESULT = {"enabled": False, "reason": "test reset"}


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


def test_dashboard_asset_paths_extract_agent_result_urls() -> None:
    dashboard = {
        "latest_run": {
            "screenshot_url": "/agent-results/screenshots/latest.png?v=123",
            "summary_url": "/agent-results/summaries/latest.md",
        },
        "latest_setups": [
            {"chart_url": "/agent-results/charts/latest_aapl.png"},
            {"chart_url": "/agent-results/charts/latest_aapl.png?cache=again"},
            {"chart_url": "https://example.test/not-local.png"},
            {"chart_url": "/agent-results/../secret.png"},
        ],
    }

    assert results_sync.dashboard_asset_paths(dashboard) == [
        "agent_results/screenshots/latest.png",
        "agent_results/summaries/latest.md",
        "agent_results/charts/latest_aapl.png",
    ]


def test_dashboard_snapshot_asset_sync_downloads_missing_assets(monkeypatch, tmp_path: Path) -> None:
    reset_results_sync_cache()
    monkeypatch.setenv("GITHUB_ACTIONS_TRIGGER_TOKEN", "token")
    monkeypatch.setenv("MARKET_LENS_DASHBOARD_SNAPSHOT_SYNC_ENABLED", "true")
    monkeypatch.setenv("MARKET_LENS_DASHBOARD_ASSET_SYNC_TTL_SECONDS", "0")
    monkeypatch.setenv("MARKET_LENS_DASHBOARD_ASSET_SYNC_LIMIT", "10")

    existing = tmp_path / "agent_results" / "charts" / "existing.png"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"already here")

    dashboard = {
        "latest_run": {
            "screenshot_url": "/agent-results/screenshots/latest.png",
        },
        "latest_setups": [
            {"chart_url": "/agent-results/charts/latest_aapl.png"},
            {"chart_url": "/agent-results/charts/existing.png"},
        ],
    }

    def fake_metadata(repo, ref, path):
        return {"type": "file", "sha": f"sha-{path}", "download_url": f"https://example.test/{path}"}

    def fake_download(url, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"downloaded {url}".encode("utf-8"))

    monkeypatch.setattr(results_sync, "github_file_metadata", fake_metadata)
    monkeypatch.setattr(results_sync, "download_to_path", fake_download)

    result = results_sync.sync_dashboard_snapshot_assets_if_enabled(tmp_path, dashboard)

    assert result["enabled"] is True
    assert result["referenced"] == 3
    assert result["missing_before_sync"] == 2
    assert result["downloaded"] == 2
    assert result["skipped"] == 1
    assert (tmp_path / "agent_results" / "screenshots" / "latest.png").exists()
    assert (tmp_path / "agent_results" / "charts" / "latest_aapl.png").exists()
    assert existing.read_bytes() == b"already here"


def test_dashboard_snapshot_sync_downloads_exact_blob(monkeypatch, tmp_path: Path) -> None:
    reset_results_sync_cache()
    monkeypatch.setenv("GITHUB_ACTIONS_TRIGGER_TOKEN", "token")
    monkeypatch.setenv("MARKET_LENS_DASHBOARD_SNAPSHOT_SYNC_ENABLED", "true")
    monkeypatch.setenv("MARKET_LENS_DASHBOARD_SNAPSHOT_SYNC_TTL_SECONDS", "0")

    def fake_metadata(repo, ref, path):
        return {"type": "file", "sha": "snapshot-blob-sha", "download_url": "https://example.test/stale-raw"}

    def fake_blob_download(repo, sha, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"status":"ok","source":"blob"}', encoding="utf-8")

    def fail_raw_download(*_args, **_kwargs):
        raise AssertionError("dashboard snapshot should sync by exact blob sha")

    monkeypatch.setattr(results_sync, "github_file_metadata", fake_metadata)
    monkeypatch.setattr(results_sync, "download_github_blob_to_path", fake_blob_download)
    monkeypatch.setattr(results_sync, "download_to_path", fail_raw_download)

    result = results_sync.sync_dashboard_snapshot_if_enabled(tmp_path)

    assert result["enabled"] is True
    assert result["downloaded"] is True
    assert result["sha"] == "snapshot-blob-sha"
    assert (tmp_path / "agent_results" / "dashboard_snapshot.json").read_text(encoding="utf-8") == (
        '{"status":"ok","source":"blob"}'
    )
