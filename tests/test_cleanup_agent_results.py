from __future__ import annotations

from pathlib import Path

import pytest

from agent.cleanup_agent_results import collect_preserved_media, prune_directory


def touch_file(path: Path, index: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"file {index}", encoding="utf-8")


def test_prune_directory_keeps_newest_files(tmp_path: Path) -> None:
    media_dir = tmp_path / "agent_results" / "charts"
    for index in range(5):
        touch_file(media_dir / f"market_lens_agent_20260714_12000{index}_abc.png", index)

    result = prune_directory(media_dir, max_files=2, dry_run=False, project_root=tmp_path)
    remaining = sorted(path.name for path in media_dir.glob("*.png"))

    assert result["deleted"] == 3
    assert remaining == [
        "market_lens_agent_20260714_120003_abc.png",
        "market_lens_agent_20260714_120004_abc.png",
    ]


def test_prune_directory_dry_run_keeps_files(tmp_path: Path) -> None:
    media_dir = tmp_path / "agent_results" / "screenshots"
    for index in range(3):
        touch_file(media_dir / f"market_lens_agent_20260714_12000{index}.png", index)

    result = prune_directory(media_dir, max_files=1, dry_run=True, project_root=tmp_path)

    assert result["deleted"] == 2
    assert len(list(media_dir.glob("*.png"))) == 3


def test_prune_directory_keeps_non_media_files(tmp_path: Path) -> None:
    media_dir = tmp_path / "agent_results" / "charts"
    touch_file(media_dir / ".gitkeep", 0)
    touch_file(media_dir / "market_lens_agent_20260714_120000_abc.png", 1)

    result = prune_directory(media_dir, max_files=0, dry_run=False, project_root=tmp_path)

    assert result["deleted"] == 1
    assert (media_dir / ".gitkeep").exists()
    assert not (media_dir / "market_lens_agent_20260714_120000_abc.png").exists()


def test_prune_directory_honors_size_cap(tmp_path: Path) -> None:
    media_dir = tmp_path / "agent_results" / "charts"
    media_dir.mkdir(parents=True)
    for index in range(4):
        path = media_dir / f"market_lens_agent_20260714_12000{index}_abc.png"
        path.write_bytes(b"x" * 1024)

    result = prune_directory(media_dir, max_files=4, max_mb=0.0025, dry_run=False, project_root=tmp_path)
    remaining = sorted(path.name for path in media_dir.glob("*.png"))

    assert result["deleted"] == 2
    assert remaining == [
        "market_lens_agent_20260714_120002_abc.png",
        "market_lens_agent_20260714_120003_abc.png",
    ]
    assert result["kept"] == 2


def test_prune_directory_preserves_dashboard_referenced_media(tmp_path: Path) -> None:
    media_dir = tmp_path / "agent_results" / "charts"
    for index in range(3):
        touch_file(media_dir / f"market_lens_agent_20260714_12000{index}_abc.png", index)
    snapshot_path = tmp_path / "agent_results" / "dashboard_snapshot.json"
    snapshot_path.write_text(
        '{"latest_setups":[{"chart_url":"/agent-results/charts/market_lens_agent_20260714_120000_abc.png"}]}',
        encoding="utf-8",
    )

    preserved = collect_preserved_media(tmp_path)
    result = prune_directory(media_dir, max_files=1, dry_run=False, project_root=tmp_path, preserve_paths=preserved)
    remaining = sorted(path.name for path in media_dir.glob("*.png"))

    assert result["deleted"] == 1
    assert result["preserved"] == 1
    assert remaining == [
        "market_lens_agent_20260714_120000_abc.png",
        "market_lens_agent_20260714_120002_abc.png",
    ]


def test_prune_directory_refuses_outside_project_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}_outside"
    outside.mkdir(exist_ok=True)

    with pytest.raises(RuntimeError, match="outside project root"):
        prune_directory(outside, max_files=0, dry_run=True, project_root=tmp_path)
