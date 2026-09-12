"""Persist only generated state files that actually changed.

GitHub Actions runs generate portfolio and result files, then rebase those
outputs onto the latest main before committing. Copying the whole agent_results
tree is slow and can accidentally re-stage old bulk. This helper snapshots only
the paths reported by git status, reapplies just those files, and stages just
those paths.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable


DEFAULT_MAX_FILES = 900
DEFAULT_MAX_BYTES = 120 * 1024 * 1024


def run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        check=True,
    )


def parse_status(output: bytes) -> list[dict[str, str]]:
    parts = [part for part in output.split(b"\0") if part]
    records: list[dict[str, str]] = []
    index = 0
    while index < len(parts):
        raw = parts[index].decode("utf-8", errors="replace")
        status = raw[:2]
        path = raw[3:]
        if status.startswith("R") or status.startswith("C"):
            index += 1
            if index >= len(parts):
                raise RuntimeError("Malformed git status rename/copy record")
            old_path = parts[index].decode("utf-8", errors="replace")
            records.append({"status": status, "path": path, "old_path": old_path})
        else:
            records.append({"status": status, "path": path})
        index += 1
    return records


def changed_records(paths: Iterable[str], *, cwd: Path) -> list[dict[str, str]]:
    result = run_git(["status", "--porcelain", "-z", "--", *paths], cwd=cwd)
    return parse_status(result.stdout)


def should_copy(status: str) -> bool:
    return status.strip() not in {"D"} and not status.startswith("D")


def enforce_limits(records: list[dict[str, str]], *, cwd: Path, max_files: int, max_bytes: int) -> int:
    if len(records) > max_files:
        raise RuntimeError(f"Generated state guard failed: {len(records)} changed files exceeds {max_files}")
    total = 0
    for record in records:
        if not should_copy(record["status"]):
            continue
        path = cwd / record["path"]
        if path.is_file():
            total += path.stat().st_size
    if total > max_bytes:
        raise RuntimeError(f"Generated state guard failed: {total} bytes exceeds {max_bytes}")
    return total


def save_bundle(bundle: Path, paths: list[str], *, cwd: Path, max_files: int, max_bytes: int) -> None:
    records = changed_records(paths, cwd=cwd)
    total_bytes = enforce_limits(records, cwd=cwd, max_files=max_files, max_bytes=max_bytes)

    if bundle.exists():
        shutil.rmtree(bundle)
    files_root = bundle / "files"
    files_root.mkdir(parents=True, exist_ok=True)

    for record in records:
        if not should_copy(record["status"]):
            continue
        source = cwd / record["path"]
        if not source.is_file():
            continue
        destination = files_root / record["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    manifest = {
        "version": "generated_state_bundle_v1",
        "records": records,
        "file_count": len(records),
        "copied_bytes": total_bytes,
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(
        "Generated state bundle saved: "
        f"files={manifest['file_count']} copied_bytes={manifest['copied_bytes']}"
    )


def load_manifest(bundle: Path) -> dict:
    manifest_path = bundle / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"Generated state bundle manifest not found: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def apply_bundle(bundle: Path, *, cwd: Path) -> None:
    manifest = load_manifest(bundle)
    files_root = bundle / "files"
    for record in manifest.get("records", []):
        relative = Path(record["path"])
        destination = cwd / relative
        if not should_copy(record["status"]):
            if destination.exists():
                if destination.is_dir():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()
            continue
        source = files_root / relative
        if not source.is_file():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    print(f"Generated state bundle applied: files={manifest.get('file_count', 0)}")


def stage_bundle(bundle: Path, *, cwd: Path) -> None:
    manifest = load_manifest(bundle)
    paths = [record["path"] for record in manifest.get("records", [])]
    for start in range(0, len(paths), 80):
        chunk = paths[start:start + 80]
        if chunk:
            run_git(["add", "--", *chunk], cwd=cwd)
    print(f"Generated state bundle staged: files={len(paths)}")


def bundle_changed(bundle: Path) -> bool:
    manifest = load_manifest(bundle)
    return bool(manifest.get("records"))


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    return int(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["save", "apply", "stage", "changed"])
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--cwd", type=Path, default=Path("."))
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--max-files", type=int, default=env_int("MARKET_LENS_PERSIST_MAX_FILES", DEFAULT_MAX_FILES))
    parser.add_argument("--max-bytes", type=int, default=env_int("MARKET_LENS_PERSIST_MAX_BYTES", DEFAULT_MAX_BYTES))
    args = parser.parse_args()

    cwd = args.cwd.resolve()
    if args.command == "save":
        if not args.path:
            raise RuntimeError("At least one --path is required for save")
        save_bundle(args.bundle, args.path, cwd=cwd, max_files=args.max_files, max_bytes=args.max_bytes)
    elif args.command == "apply":
        apply_bundle(args.bundle, cwd=cwd)
    elif args.command == "stage":
        stage_bundle(args.bundle, cwd=cwd)
    elif args.command == "changed":
        return 0 if bundle_changed(args.bundle) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
