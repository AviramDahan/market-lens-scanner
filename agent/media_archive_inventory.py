"""Inventory committed media for archive planning; never deletes or stages files."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

MEDIA_DIRS = ("agent_results/charts", "agent_results/screenshots")


def inventory(root: Path, ref: str = "HEAD") -> dict:
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        cwd=root, timeout=30, text=True,
    ).strip()
    raw = subprocess.check_output(
        # Do not ask for sizes (-l): partial clones may download every blob.
        ["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
         "ls-tree", "-r", "-z", commit, "--", *MEDIA_DIRS],
        cwd=root, timeout=60,
    )
    files = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, name = record.split(b"\t", 1)
        mode, kind, oid = metadata.split()
        if kind != b"blob" or mode not in {b"100644", b"100755"}:
            continue
        path = name.decode("utf-8")
        if Path(path).suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            continue
        files.append({"path": path, "git_blob_oid": oid.decode("ascii")})
    return {
        "version": "media_archive_inventory_v1", "commit": commit,
        "read_only": True, "archive_verified": False,
        "file_count": len(files), "total_bytes": None,
        "files": files,
        "limitations": [
            "An inventory is not an independent durable backup.",
            "Keep this commit reachable; verify external archive and historical URL recovery before deleting media.",
            "Sizes are omitted to avoid downloading media blobs from a partial clone.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    output = args.output.resolve()
    # Reports belong outside the checkout; never overwrite tracked app data.
    if output == root or root in output.parents:
        parser.error("Write the inventory outside the repository checkout")
    if output.exists():
        parser.error("Output exists; use a new inventory path")
    report = inventory(root, args.ref)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps({key: report[key] for key in ("commit", "file_count", "total_bytes", "archive_verified")}))


if __name__ == "__main__":
    main()
