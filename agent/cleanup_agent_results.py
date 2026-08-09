from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg"}


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def media_sort_key(path: Path) -> tuple[str, float, str]:
    return (path.stem, path.stat().st_mtime, path.name)


def prune_directory(
    directory: Path,
    *,
    max_files: int,
    max_mb: float | None = None,
    max_age_days: float | None = None,
    dry_run: bool,
    project_root: Path = PROJECT_ROOT,
    preserve_paths: set[Path] | None = None,
) -> dict[str, object]:
    directory = directory.resolve()
    project_root = project_root.resolve()
    if project_root not in [directory, *directory.parents]:
        raise RuntimeError(f"Refusing to prune outside project root: {directory}")

    files = []
    for path in directory.glob("*"):
        if not path.is_file() or path.suffix.lower() not in MEDIA_SUFFIXES:
            continue
        stat = path.stat()
        files.append(
            {
                "path": path,
                "resolved": path.resolve(),
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                "sort_key": (path.stem, stat.st_mtime, path.name),
            }
        )
    files.sort(key=lambda item: item["sort_key"], reverse=True)
    if max_files < 0:
        max_files = 0
    preserve_resolved = {path.resolve() for path in preserve_paths or set()}
    max_bytes = None if max_mb is None or max_mb < 0 else int(max_mb * 1024 * 1024)
    cutoff = None if max_age_days is None or max_age_days < 0 else time.time() - (max_age_days * 86400)
    keep = set()
    kept_bytes = 0
    regular_kept = 0
    for item in files:
        path = item["path"]
        resolved = item["resolved"]
        if resolved in preserve_resolved:
            keep.add(path)
            kept_bytes += item["size"]
            continue
        if cutoff is not None and item["mtime"] < cutoff:
            continue
        if regular_kept >= max_files:
            continue
        size = item["size"]
        if max_bytes is not None and kept_bytes + size > max_bytes:
            continue
        keep.add(path)
        regular_kept += 1
        kept_bytes += size

    deleted = []
    bytes_deleted = 0

    for item in files:
        path = item["path"]
        if path in keep:
            continue
        size = item["size"]
        deleted.append(str(path.relative_to(project_root)))
        bytes_deleted += size
        if not dry_run:
            path.unlink()

    return {
        "directory": str(directory.relative_to(project_root)),
        "kept": len(keep),
        "kept_mb": round(kept_bytes / 1024 / 1024, 2),
        "max_files": max_files,
        "max_mb": max_mb,
        "max_age_days": max_age_days,
        "preserved": len([path for path in keep if path.resolve() in preserve_resolved]),
        "deleted": len(deleted),
        "bytes_deleted": bytes_deleted,
        "deleted_paths": deleted[:25],
    }


def collect_preserved_media(project_root: Path = PROJECT_ROOT) -> set[Path]:
    snapshot_path = project_root / "agent_results" / "dashboard_snapshot.json"
    if not snapshot_path.exists():
        return set()
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()

    preserved: set[Path] = set()
    for asset in preserved_dashboard_assets(snapshot):
        path = (project_root / "agent_results" / asset).resolve()
        if path.suffix.lower() in MEDIA_SUFFIXES:
            preserved.add(path)
    return preserved


def preserved_dashboard_assets(snapshot: dict[str, Any]) -> list[Path]:
    assets: list[Path] = []
    for key in ("latest_run", "open_positions", "latest_setups"):
        assets.extend(agent_result_assets(snapshot.get(key)))
    for key in ("recent_trades", "closed_trades"):
        records = snapshot.get(key)
        if isinstance(records, list):
            assets.extend(agent_result_assets(records[-30:]))
    return assets


def agent_result_assets(value: Any) -> list[Path]:
    assets: list[Path] = []
    if isinstance(value, dict):
        for item in value.values():
            assets.extend(agent_result_assets(item))
        return assets
    if isinstance(value, list):
        for item in value:
            assets.extend(agent_result_assets(item))
        return assets
    if not isinstance(value, str) or "/agent-results/" not in value:
        return assets
    relative = value.split("/agent-results/", 1)[1].split("?", 1)[0].split("#", 1)[0]
    if relative:
        assets.append(Path(relative))
    return assets


def main() -> int:
    enabled = env_bool("MARKET_LENS_AGENT_MEDIA_RETENTION_ENABLED", True)
    dry_run = env_bool("MARKET_LENS_AGENT_MEDIA_RETENTION_DRY_RUN", False)
    chart_limit = env_int("MARKET_LENS_AGENT_CHART_RETENTION_MAX_FILES", 240)
    screenshot_limit = env_int("MARKET_LENS_AGENT_SCREENSHOT_RETENTION_MAX_FILES", 60)
    chart_mb_limit = env_float("MARKET_LENS_AGENT_CHART_RETENTION_MAX_MB", 180.0)
    screenshot_mb_limit = env_float("MARKET_LENS_AGENT_SCREENSHOT_RETENTION_MAX_MB", 80.0)
    chart_age_days = env_float("MARKET_LENS_AGENT_CHART_RETENTION_MAX_AGE_DAYS", 21.0)
    screenshot_age_days = env_float("MARKET_LENS_AGENT_SCREENSHOT_RETENTION_MAX_AGE_DAYS", 14.0)

    if not enabled:
        print("Agent media retention disabled.")
        return 0

    results = []
    preserve_paths = collect_preserved_media(PROJECT_ROOT)
    targets = [
        (PROJECT_ROOT / "agent_results" / "charts", chart_limit, chart_mb_limit, chart_age_days),
        (PROJECT_ROOT / "agent_results" / "screenshots", screenshot_limit, screenshot_mb_limit, screenshot_age_days),
    ]
    for directory, limit, mb_limit, age_days in targets:
        if directory.exists():
            results.append(
                prune_directory(
                    directory,
                    max_files=limit,
                    max_mb=mb_limit,
                    max_age_days=age_days,
                    dry_run=dry_run,
                    preserve_paths=preserve_paths,
                )
            )

    total_deleted = sum(int(item["deleted"]) for item in results)
    total_bytes = sum(int(item["bytes_deleted"]) for item in results)
    mode = "dry-run" if dry_run else "applied"
    print(
        "Agent media retention "
        f"{mode}: deleted {total_deleted} files, "
        f"freed {total_bytes / 1024 / 1024:.2f} MB."
    )
    for item in results:
        print(
            f"- {item['directory']}: kept {item['kept']}, "
            f"kept {item['kept_mb']} MB, preserved {item['preserved']}, "
            f"max age {item['max_age_days']}d, "
            f"deleted {item['deleted']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
