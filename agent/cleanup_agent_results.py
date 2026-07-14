from __future__ import annotations

import os
from pathlib import Path


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


def media_sort_key(path: Path) -> tuple[str, float, str]:
    return (path.stem, path.stat().st_mtime, path.name)


def prune_directory(
    directory: Path,
    *,
    max_files: int,
    dry_run: bool,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, object]:
    directory = directory.resolve()
    project_root = project_root.resolve()
    if project_root not in [directory, *directory.parents]:
        raise RuntimeError(f"Refusing to prune outside project root: {directory}")

    files = sorted(
        [
            path
            for path in directory.glob("*")
            if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES
        ],
        key=media_sort_key,
        reverse=True,
    )
    if max_files < 0:
        max_files = 0
    keep = set(files[:max_files])
    deleted = []
    bytes_deleted = 0

    for path in files:
        if path in keep:
            continue
        size = path.stat().st_size
        deleted.append(str(path.relative_to(project_root)))
        bytes_deleted += size
        if not dry_run:
            path.unlink()

    return {
        "directory": str(directory.relative_to(project_root)),
        "kept": len(keep),
        "deleted": len(deleted),
        "bytes_deleted": bytes_deleted,
        "deleted_paths": deleted[:25],
    }


def main() -> int:
    enabled = env_bool("MARKET_LENS_AGENT_MEDIA_RETENTION_ENABLED", True)
    dry_run = env_bool("MARKET_LENS_AGENT_MEDIA_RETENTION_DRY_RUN", False)
    chart_limit = env_int("MARKET_LENS_AGENT_CHART_RETENTION_MAX_FILES", 240)
    screenshot_limit = env_int("MARKET_LENS_AGENT_SCREENSHOT_RETENTION_MAX_FILES", 60)

    if not enabled:
        print("Agent media retention disabled.")
        return 0

    results = []
    targets = [
        (PROJECT_ROOT / "agent_results" / "charts", chart_limit),
        (PROJECT_ROOT / "agent_results" / "screenshots", screenshot_limit),
    ]
    for directory, limit in targets:
        if directory.exists():
            results.append(prune_directory(directory, max_files=limit, dry_run=dry_run))

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
            f"deleted {item['deleted']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
