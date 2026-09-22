"""Recover measurement files from Git without changing the tracker or source history.

Run against a full-history checkout. Output is an independent staging directory;
review its manifest before adding recovered archives to production.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import io
import subprocess
from pathlib import Path


def recover(root: Path, output: Path, since: str) -> dict:
    history = subprocess.run(
        ["git", "log", f"--since={since}", "--diff-filter=AM", "--format=COMMIT:%H", "--name-only", "HEAD", "--",
         "agent_results/decisions", "agent_results/runtime", "agent_results/summaries"],
        cwd=root, check=True, capture_output=True, text=True, timeout=120,
    ).stdout
    latest = {}
    commit = None
    for line in history.splitlines():
        if line.startswith("COMMIT:"):
            commit = line[7:]
        elif line.startswith("agent_results/") and line.endswith((".jsonl", ".json", ".md")) and commit:
            latest.setdefault(line, commit)
    manifest = {"since": since, "source": "latest committed version per path in Git history",
                "coverage": "GIT_RECOVERABLE_ONLY", "files": [],
                "limitations": ["Files never committed cannot be recovered.",
                                "Missing historical equity is not reconstructed or invented."]}
    # One bounded batch avoids spawning a Git process for every historical file.
    requests = sorted(latest.items())
    payload = "".join(f"{commit}:{name}\n" for name, commit in requests).encode()
    batch = subprocess.run(["git", "cat-file", "--batch"], input=payload, cwd=root,
                           check=True, capture_output=True, timeout=180)
    stream = io.BytesIO(batch.stdout)
    indexes = {}
    for name, commit in requests:
        header = stream.readline().split()
        if len(header) != 3 or header[1] != b"blob":
            raise RuntimeError(f"Missing Git blob for {name}; recovery incomplete")
        raw = stream.read(int(header[2]))
        if stream.read(1) != b"\n":
            raise RuntimeError("Invalid Git batch framing")
        digest = hashlib.sha256(raw).hexdigest()
        relative = Path(name).relative_to("agent_results")
        destination = output / relative.parent / "archive" / f"{relative.name}.{digest}.gz"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if gzip.decompress(destination.read_bytes()) != raw:
                raise RuntimeError(f"Conflicting archive: {destination.name}")
        else:
            destination.write_bytes(gzip.compress(raw, mtime=0))
        indexes.setdefault(destination.parent, {})[relative.name] = destination.name
        manifest["files"].append({"path": name, "commit": commit, "sha256": digest,
                                  "bytes": len(raw), "archive": str(destination.relative_to(output))})
    output.mkdir(parents=True, exist_ok=True)
    for directory, index in indexes.items():
        (directory / "index.json").write_text(json.dumps(index, sort_keys=True), encoding="utf-8")
    (output / "recovery_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output == root or (root / "agent_results") == output or (root / "agent_results") in output.parents:
        parser.error("Use a separate staging directory, not production agent_results")
    manifest = recover(root, output, args.since)
    print(json.dumps({"recovered_files": len(manifest["files"]), "coverage": manifest["coverage"]}))


if __name__ == "__main__":
    main()
