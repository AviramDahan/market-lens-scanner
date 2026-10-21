"""Durable provenance for local writes before an archive pending bundle exists.

Never restores a workbook. Unknown/partial financial state remains fail-closed.
The caller must own writer.lock throughout each operation.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .archive_checkpoint import TRACKER, require_headroom
from .host_archive import MEDIA, encoded, notification_media_names, recovery_files, safe_name, sha256, sync_directory
from .staged_scanner import promotion_targets


def _path(checkpoints) -> Path:
    return checkpoints.root / "local_write.json"


def _resolve(checkpoints, name: str) -> Path:
    safe_name(name)
    prefix, relative = name.split("/", 1)
    root = checkpoints.repo if prefix == "repo" else checkpoints.state
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Unsafe local-write target")
    return path


def _key(checkpoints, path: Path) -> str:
    for prefix, root in (("repo", checkpoints.repo), ("runtime", checkpoints.state)):
        if path.is_relative_to(root):
            name = prefix + "/" + path.relative_to(root).as_posix()
            _resolve(checkpoints, name)
            return name
    raise ValueError("Local-write target outside recovery scope")


def _snapshot(checkpoints) -> dict[str, str]:
    files = recovery_files(checkpoints.repo, checkpoints.state)
    result = {name: sha256(path) for name, path in files.items()}
    if set(recovery_files(checkpoints.repo, checkpoints.state)) != set(result):
        raise RuntimeError("Local-write inventory changed while recording intent")
    return result


def _store(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as output:
        output.write(encoded(value))
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)
    sync_directory(path.parent)


def _load(checkpoints) -> dict | None:
    path = _path(checkpoints)
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError("Unsafe local-write intent")
    value = json.loads(path.read_text())
    if (value.get("version") != 1 or value.get("kind") not in {"scanner", "monitor"}
            or len(value.get("id", "")) != 32
            or any(c not in "0123456789abcdef" for c in value["id"])):
        raise ValueError("Invalid local-write intent")
    for name in value["before_files"]:
        _resolve(checkpoints, name)
    for target in value.get("files", []):
        _resolve(checkpoints, target["path"])
    for name in value.get("dependencies", {}):
        _resolve(checkpoints, name)
    if value.get("before_tracker_sha256") != value["before_files"].get(TRACKER):
        raise ValueError("Local-write original tracker identity differs")
    if len({row["path"] for row in value.get("files", [])}) != len(value.get("files", [])):
        raise ValueError("Duplicate local-write targets")
    return value


def _begin(checkpoints, revision: str, kind: str) -> dict:
    if _path(checkpoints).exists() or (checkpoints.root / "pending.json").exists():
        raise RuntimeError("Previous local write must be recovered before another write")
    receipt = checkpoints.receipt()
    checkpoints.matching_tracker(receipt["manifest"])
    before = _snapshot(checkpoints)
    return {"version": 1, "id": uuid.uuid4().hex, "kind": kind, "phase": "COMPUTING",
            "revision": revision, "base_generation": receipt["manifest"]["generation"],
            "before_files": before, "before_tracker_sha256": before[TRACKER], "files": []}


def begin_scanner(checkpoints, stage, revision: str) -> None:
    targets = promotion_targets(stage, checkpoints.repo, checkpoints.state)
    # Include rollback, compressed freeze and downloaded restore working space.
    require_headroom(checkpoints.state, 4 * sum(source.stat().st_size for source, _ in targets))
    value = _begin(checkpoints, revision, "scanner")
    value["files"] = [{"path": _key(checkpoints, target),
                       "before": sha256(target) if target.is_file() else None,
                       "after": sha256(source)} for source, target in targets]
    value["after_tracker_sha256"] = next(row["after"] for row in value["files"] if row["path"] == TRACKER)
    sources = {_key(checkpoints, target): source for source, target in targets}
    dependencies = {}
    for source, target in targets:
        if _key(checkpoints, target).startswith("runtime/outbox/"):
            for name in notification_media_names(json.loads(source.read_text(encoding="utf-8"))):
                path = sources.get(name, _resolve(checkpoints, name))
                if not path.is_file() or path.is_symlink():
                    raise ValueError("Notification dependency is missing or unsafe")
                dependencies[name] = sha256(path)
    value["dependencies"] = dependencies
    value["phase"] = "PREPARED"
    _store(_path(checkpoints), value)


def begin_monitor(checkpoints, revision: str) -> None:
    tracker = _resolve(checkpoints, TRACKER)
    require_headroom(checkpoints.state, 6 * tracker.stat().st_size + 128 * 1024**2)
    _store(_path(checkpoints), _begin(checkpoints, revision, "monitor"))


def _after_matches(checkpoints, value: dict) -> bool:
    expected = dict(value["before_files"])
    expected.update(value.get("dependencies", {}))
    expected.update({row["path"]: row["after"] for row in value["files"]})
    actual = _snapshot(checkpoints)
    if not set(value["before_files"]).issubset(actual) or set(actual) - set(expected):
        return False
    if any(expected[name] != digest for name, digest in actual.items()):
        return False
    return all(_resolve(checkpoints, row["path"]).is_file()
               and sha256(_resolve(checkpoints, row["path"])) == row["after"] for row in value["files"])


def seal_scanner(checkpoints) -> None:
    value = _load(checkpoints)
    if value is None or value["kind"] != "scanner" or not _after_matches(checkpoints, value):
        raise RuntimeError("Scanner promotion is incomplete or changed; no checkpoint authorized")
    value["phase"] = "PROMOTED"
    _store(_path(checkpoints), value)


def seal_monitor(checkpoints) -> None:
    value = _load(checkpoints)
    if value is None or value["kind"] != "monitor" or value["phase"] != "COMPUTING":
        raise RuntimeError("Monitor has no matching local-write intent")
    after = _snapshot(checkpoints)
    if set(value["before_files"]) - set(after):
        raise RuntimeError("Monitor removed recovery evidence; review required")
    value["files"] = [{"path": name, "before": value["before_files"].get(name), "after": digest}
                      for name, digest in after.items() if value["before_files"].get(name) != digest]
    value["after_tracker_sha256"] = after[TRACKER]
    value["phase"] = "PROMOTED"
    _store(_path(checkpoints), value)


def _retire(checkpoints, value: dict, status: str) -> None:
    evidence = checkpoints.state / "coordination/write-fence-history" / (value["id"] + ".json")
    record = {**value, "resolution": status, "resolved_at": datetime.now(timezone.utc).isoformat()}
    if status in {"NO_FINANCIAL_WRITE", "NOT_APPLIED"}:
        # No-event polls must not duplicate the entire recovery inventory daily.
        record = {key: record[key] for key in (
            "version", "id", "kind", "revision", "resolution", "resolved_at",
            "base_generation", "before_tracker_sha256")}
        record.update(evidence_version=1,
                      after_tracker_sha256=value.get("after_tracker_sha256", value["before_tracker_sha256"]),
                      inventory_count=len(value["before_files"]),
                      inventory_sha256=hashlib.sha256(encoded(value["before_files"])).hexdigest(),
                      intent_sha256=hashlib.sha256(encoded(value)).hexdigest())
    else:
        receipt = checkpoints.receipt()
        record["verified_generation"] = receipt["manifest"]["generation"]
        record["verified_manifest_sha256"] = receipt["proof"]["manifest_sha256"]
    _store(evidence, record)
    _path(checkpoints).unlink()
    sync_directory(checkpoints.root)


def cancel_no_event_monitor(checkpoints, before_tracker_hash: str) -> None:
    value = _load(checkpoints)
    if (value is None or value["kind"] != "monitor" or value["before_tracker_sha256"] != before_tracker_hash
            or sha256(_resolve(checkpoints, TRACKER)) != before_tracker_hash
            or list((checkpoints.state / "outbox").glob("*.json"))):
        raise RuntimeError("Cannot classify monitor write as having no financial effect")
    if not _monitor_metadata_only(value["before_files"], _snapshot(checkpoints)):
        raise RuntimeError("Unexpected structured change during a no-event monitor")
    _retire(checkpoints, value, "NO_FINANCIAL_WRITE")


def _monitor_metadata_only(before: dict, after: dict) -> bool:
    for name in set(before) | set(after):
        if before.get(name) == after.get(name):
            continue
        path = Path(name)
        allowed_summary = (name.startswith("repo/agent_results/position_monitor/")
                           and (path.name == "latest_status.json"
                                or path.name.startswith("position_monitor_") and path.suffix == ".md"))
        if not name.startswith("runtime/monitor/") and not allowed_summary:
            return False
    return True


def finish_local_write(checkpoints) -> None:
    value = _load(checkpoints)
    if value is None:
        return
    if value["phase"] != "PROMOTED" or not _after_matches(checkpoints, value):
        raise RuntimeError("Cannot finish an incomplete local write")
    receipt = checkpoints.receipt()
    verified = {row["path"]: row["sha256"] for row in receipt["manifest"]["files"]}
    if (verified.get(TRACKER) != value["after_tracker_sha256"]
            or any(verified.get(row["path"]) != row["after"] for row in value["files"]
                   if Path(row["path"]).suffix.lower() not in MEDIA)):
        raise RuntimeError("Local write is not covered by a verified checkpoint")
    _retire(checkpoints, value, "CHECKPOINT_VERIFIED")


def recover_local_write(checkpoints, revision: str) -> None:
    value = _load(checkpoints)
    if value is None:
        return
    if value["revision"] != revision:
        raise RuntimeError("Local write belongs to another source revision; review required")
    actual = _snapshot(checkpoints)
    targets_before = all(
        (sha256(_resolve(checkpoints, row["path"])) if _resolve(checkpoints, row["path"]).is_file() else None)
        == row["before"] for row in value["files"])
    if actual == value["before_files"] and targets_before:
        _retire(checkpoints, value, "NOT_APPLIED")
        return
    if value["kind"] == "monitor" and value["phase"] == "COMPUTING":
        if (actual.get(TRACKER) == value["before_tracker_sha256"]
                and not list((checkpoints.state / "outbox").glob("*.json"))
                and _monitor_metadata_only(value["before_files"], actual)):
            _retire(checkpoints, value, "NO_FINANCIAL_WRITE")
            return
        raise RuntimeError("Monitor write was interrupted before validation; review required")
    if not _after_matches(checkpoints, value):
        raise RuntimeError("Incomplete or unexpected local promotion; review required")
    if value["phase"] != "PROMOTED":
        value["phase"] = "PROMOTED"
        _store(_path(checkpoints), value)
    if (checkpoints.root / "pending.json").exists():
        checkpoints.recover()
    else:
        receipt = checkpoints.receipt()
        if receipt["manifest"]["generation"] == value["base_generation"]:
            checkpoints.save(revision)
        elif next(row["sha256"] for row in receipt["manifest"]["files"] if row["path"] == TRACKER) != value["after_tracker_sha256"]:
            raise RuntimeError("Local-write base checkpoint changed; review required")
    finish_local_write(checkpoints)
