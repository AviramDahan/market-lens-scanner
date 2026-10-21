"""Immutable, incremental host recovery bundles. No trading or notification actions."""
from __future__ import annotations

import hashlib
import json
import math
import os
import posixpath
import shutil
import stat
import subprocess
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

VERSION = "market_lens_host_archive_v1"
SOURCE_REPOSITORY = "AviramDahan/market-lens-scanner"
STRUCTURED = {".json", ".jsonl", ".ndjson", ".md", ".csv", ".tsv", ".txt", ".xlsx", ".gz"}
MEDIA = {".png", ".jpg", ".jpeg", ".webp"}
REQUIRED = {"repo/agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx",
            "repo/agent_results/dashboard_snapshot.json", "repo/agent_results/telegram_notifications.jsonl",
            "runtime/deployed_revision"}
MAX_ASSET_BYTES = 1024**3  # Stay below GitHub's per-asset limit, including ZIP overhead.
MAX_FILE_BYTES = 512 * 1024**2
PART_RAW_BYTES = 128 * 1024**2
CHUNK_BYTES = 1024**2


def encoded(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def sync_directory(path: Path) -> None:
    if hasattr(os, "O_DIRECTORY"):
        directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def safe_name(name: str) -> str:
    path = PurePosixPath(name)
    if (not name or "\\" in name or ":" in name or path.is_absolute()
            or any(part in {"", ".", ".."} for part in name.split("/"))):
        raise ValueError("Unsafe archive path")
    if any(part.startswith(".env") or part.lower() in {"credentials.json", "secrets.json"} for part in path.parts):
        raise ValueError("Credential-like archive path")
    if not (name.startswith(("repo/agent_results/", "repo/agent_tracker/", "runtime/monitor/", "runtime/outbox/"))
            or name == "runtime/deployed_revision"):
        raise ValueError("Unexpected recovery scope")
    return name


def file_signature(path: Path) -> tuple[int, int, int]:
    info = path.stat()
    return info.st_size, info.st_mtime_ns, info.st_ino


def notification_media_names(value) -> set[str]:
    """Extract only recovery-scoped media references; never fetch an external URL."""
    if isinstance(value, dict):
        return set().union(*(notification_media_names(child) for child in value.values()))
    if isinstance(value, list):
        return set().union(*(notification_media_names(child) for child in value))
    if not isinstance(value, str):
        return set()
    normalized = value.replace("\\", "/")
    if normalized.startswith(("http://", "https://")):
        parsed = urlsplit(normalized)
        if parsed.hostname not in {"web", "127.0.0.1", "localhost", "market-lens.2.28.100.77.sslip.io"}:
            return set()
        normalized = unquote(parsed.path)
    if normalized.startswith("/agent-results/"):
        normalized = "agent_results/" + normalized[len("/agent-results/"):]
    elif "agent_results/" in normalized:
        normalized = "agent_results/" + normalized.split("agent_results/", 1)[1]
    else:
        return set()
    name = safe_name("repo/" + normalized)
    return {name} if Path(name).suffix.lower() in MEDIA else set()


def recovery_files(repo: Path, state: Path) -> dict[str, Path]:
    selected = {}
    for prefix, root in (("repo/agent_results", repo / "agent_results"),
                         ("repo/agent_tracker", repo / "agent_tracker"),
                         ("runtime/monitor", state / "monitor"), ("runtime/outbox", state / "outbox")):
        if root.is_symlink():
            raise ValueError("Symlink recovery root")
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("Symlink in recovery source")
            if not path.is_file():
                continue
            name = safe_name(prefix + "/" + path.relative_to(root).as_posix())
            if path.suffix.lower() in STRUCTURED:
                selected[name] = path
    deployed = state / "deployed_revision"
    if deployed.is_symlink():
        raise ValueError("Symlink deployment marker")
    if deployed.is_file():
        selected["runtime/deployed_revision"] = deployed

    # Pending notifications must retain their referenced image even on a new host.
    for name, path in list(selected.items()):
        if name.startswith("runtime/outbox/") and path.suffix == ".json":
            for media in notification_media_names(json.loads(path.read_text(encoding="utf-8"))):
                image = repo / media[len("repo/"):]
                if not image.is_file() or image.is_symlink() or repo.resolve() not in image.resolve().parents:
                    raise ValueError("Pending notification image is missing or unsafe")
                selected[media] = image
    if REQUIRED - selected.keys():
        raise ValueError("Required recovery files missing")
    return dict(sorted(selected.items()))


def validate_manifest(manifest: dict) -> None:
    body = {k: v for k, v in manifest.items() if k != "generation"}
    if (manifest.get("version") != VERSION or manifest.get("source_repository") != SOURCE_REPOSITORY
            or manifest.get("generation") != hashlib.sha256(encoded(body)).hexdigest()):
        raise ValueError("Invalid generation identity")
    if not manifest.get("source_revision") or not manifest.get("deployed_revision"):
        raise ValueError("Missing code provenance")
    files = manifest.get("files", [])
    names = [safe_name(entry["path"]) for entry in files]
    if len(set(names)) != len(names) or REQUIRED - set(names):
        raise ValueError("Invalid recovery inventory")
    for entry in files:
        if not isinstance(entry["bytes"], int) or not 0 <= entry["bytes"] <= MAX_FILE_BYTES:
            raise ValueError("Invalid recovery file size")
        for key in ("sha256", "asset_sha256"):
            if len(entry[key]) != 64 or any(c not in "0123456789abcdef" for c in entry[key]):
                raise ValueError("Invalid recovery hash")
        origin = entry["generation"]
        if origin != "self" and (len(origin) != 64 or any(c not in "0123456789abcdef" for c in origin)):
            raise ValueError("Invalid asset generation")
        if not entry["asset"].startswith("part-") or not entry["asset"].endswith(".zip") or "/" in entry["asset"] or "\\" in entry["asset"]:
            raise ValueError("Invalid asset name")
        if not 0 < entry["asset_bytes"] < MAX_ASSET_BYTES:
            raise ValueError("Invalid asset size")


def freeze(repo: Path, state: Path, spool: Path, *, source_revision: str,
           base: dict | None = None, part_raw_bytes: int = PART_RAW_BYTES,
           created_at: str | None = None, parse_source_workbook: bool = True) -> Path:
    """Caller holds writer.lock. Only frozen compressed copies enter the spool."""
    repo, state, spool = repo.resolve(), state.resolve(), spool.resolve()
    if spool == repo or repo in spool.parents or spool == state:
        raise ValueError("Spool must be outside source directories")
    if base:
        validate_manifest(base)
    previous = {entry["path"]: entry for entry in (base or {}).get("files", [])}
    files = recovery_files(repo, state)
    validate_recovery_paths(repo, state, parse_workbook=parse_source_workbook)
    signatures = {name: file_signature(path) for name, path in files.items()}
    spool.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = spool / ("building-" + uuid.uuid4().hex)
    temporary.mkdir(mode=0o700)
    entries, parts = [], []
    archive = None
    raw_bytes = 0
    current_part = None
    try:
        for name, path in files.items():
            size = signatures[name][0]
            if size > MAX_FILE_BYTES:
                raise ValueError("Recovery file exceeds supported file limit; do not truncate")
            digest = sha256(path)
            old = previous.get(name)
            if old and (old["bytes"], old["sha256"]) == (size, digest):
                entry = dict(old)
                if entry["generation"] == "self":
                    entry["generation"] = base["generation"]
                entries.append(entry)
                continue
            if archive is None or raw_bytes + size > part_raw_bytes:
                if archive is not None:
                    archive.close()
                current_part = f"part-{len(parts) + 1:04d}.zip"
                parts.append(current_part)
                archive = zipfile.ZipFile(temporary / current_part, "x", zipfile.ZIP_DEFLATED, compresslevel=6)
                raw_bytes = 0
            copied = hashlib.sha256()
            with path.open("rb") as source, archive.open(name, "w", force_zip64=True) as target:
                for block in iter(lambda: source.read(CHUNK_BYTES), b""):
                    copied.update(block)
                    target.write(block)
            if copied.hexdigest() != digest:
                raise ValueError("Recovery source changed while freezing")
            raw_bytes += size
            entries.append({"path": name, "bytes": size, "sha256": digest,
                            "generation": "self", "asset": current_part})
        if archive is not None:
            archive.close()
            archive = None
        for name, signature in signatures.items():
            if file_signature(files[name]) != signature:
                raise ValueError("Recovery source changed during freeze")
        if set(recovery_files(repo, state)) != set(files):
            raise ValueError("Recovery inventory changed during freeze")
        part_info = {name: {"sha256": sha256(temporary / name), "bytes": (temporary / name).stat().st_size}
                     for name in parts}
        for entry in entries:
            if entry["generation"] == "self":
                info = part_info[entry["asset"]]
                entry.update(asset_sha256=info["sha256"], asset_bytes=info["bytes"])
        manifest = {"version": VERSION, "source_repository": SOURCE_REPOSITORY,
                    "source_revision": source_revision,
                    "created_at": created_at or datetime.now(timezone.utc).isoformat(),
                    "scope": "host_structured_recovery", "media_policy": "pending_outbox_references_only",
                    "deployed_revision": (state / "deployed_revision").read_text().strip(),
                    "files": entries}
        manifest["generation"] = hashlib.sha256(encoded(manifest)).hexdigest()
        validate_manifest(manifest)
        (temporary / "manifest.json").write_bytes(encoded(manifest))
        for path in temporary.iterdir():
            with path.open("r+b") as source:
                os.fsync(source.fileno())
        sync_directory(temporary)
        destination = spool / manifest["generation"]
        if destination.exists():
            raise ValueError("Generation already frozen; use existing bundle")
        temporary.rename(destination)
        sync_directory(spool)
        return destination
    except Exception:
        if archive is not None:
            archive.close()
        # Only incomplete temporary copies are removed, never source/history.
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def restore(manifest: dict, destination: Path, fetch_asset, *, verified_workbook_sha: str | None = None) -> dict:
    """Restore into a NEW directory only. fetch_asset(generation, name, output)."""
    validate_manifest(manifest)
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("Restore destination must not exist")
    destination.mkdir(parents=True, mode=0o700)
    cache = destination / ".asset-cache"
    cache.mkdir()
    grouped = {}
    for entry in manifest["files"]:
        generation = manifest["generation"] if entry["generation"] == "self" else entry["generation"]
        grouped.setdefault((generation, entry["asset"]), []).append(entry)
    for (generation, name), entries in grouped.items():
        archive_path = cache / f"{generation}-{name}"
        fetch_asset(generation, name, archive_path)
        expected = {(entry["asset_sha256"], entry["asset_bytes"]) for entry in entries}
        if expected != {(sha256(archive_path), archive_path.stat().st_size)}:
            raise ValueError("Downloaded asset integrity failed")
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if len({info.filename for info in members}) != len(members):
                raise ValueError("Duplicate ZIP member")
            for info in members:
                safe_name(info.filename)
                if stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                    raise ValueError("Unsafe ZIP member")
            for entry in entries:
                info = archive.getinfo(entry["path"])
                if info.file_size != entry["bytes"]:
                    raise ValueError("Recovery member size failed")
                output = destination.joinpath(*PurePosixPath(entry["path"]).parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with archive.open(info) as source, output.open("xb") as target:
                    for block in iter(lambda: source.read(CHUNK_BYTES), b""):
                        digest.update(block)
                        target.write(block)
                if digest.hexdigest() != entry["sha256"]:
                    raise ValueError("Recovery member integrity failed")
        archive_path.unlink()  # Download cache only; originals and remote assets remain intact.
    cache.rmdir()
    tracker_entry = next(entry for entry in manifest["files"] if entry["path"] ==
                         "repo/agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx")
    if verified_workbook_sha is not None and verified_workbook_sha != tracker_entry["sha256"]:
        raise ValueError("Previously verified workbook hash does not match restored content")
    validate_recovery(destination, parse_workbook=verified_workbook_sha is None)
    if (destination / "runtime/deployed_revision").read_text().strip() != manifest["deployed_revision"]:
        raise ValueError("Restored deployment identity differs")
    proof = {"generation": manifest["generation"], "file_count": len(manifest["files"]),
             "total_bytes": sum(entry["bytes"] for entry in manifest["files"]),
             "all_hashes_verified": True, "workbook_verified": True}
    proof["workbook_parser_status"] = ("DECODED_CURRENT_CONTENT" if verified_workbook_sha is None
                                        else "INHERITED_IDENTICAL_VERIFIED_CONTENT")
    (destination / "restore-proof.json").write_bytes(encoded(proof))
    return proof


def validate_recovery(destination: Path, *, parse_workbook: bool = True) -> None:
    validate_recovery_paths(destination / "repo", destination / "runtime", parse_workbook=parse_workbook)


def validate_workbook_parser(path: Path) -> None:
    try:
        if __package__:
            from .workbook_archive_check import validate_workbook
        else:
            from workbook_archive_check import validate_workbook
        validate_workbook(path)
        return
    except ImportError:
        # The minimal host Python has no pip; use the already deployed worker's
        # parser offline. No env_file, trading code, or live writable bind mounts.
        checker = Path(__file__).with_name("workbook_archive_check.py").resolve()
        completed = subprocess.run([
            "docker", "run", "--rm", "--read-only", "--network", "none", "--memory", "256m",
            "--cpus", "1.0", "--pids-limit", "64", "--security-opt", "no-new-privileges",
            "--user", f"{os.getuid()}:{os.getgid()}", "--mount", f"type=bind,source={path.resolve()},target=/workbook.xlsx,readonly",
            "--mount", f"type=bind,source={checker},target=/check.py,readonly",
            "--entrypoint", "python", "market-lens-runtime-worker", "/check.py", "/workbook.xlsx",
        ], check=True, text=True, capture_output=True, timeout=120)
        proof = json.loads(completed.stdout)
        if proof != {"parser": "openpyxl", "validated_sheets": ["Open Positions", "Trade Log"], "all_worksheets_decoded": True}:
            raise ValueError("Workbook parser did not confirm validation")


def validate_recovery_paths(root: Path, state: Path, *, parse_workbook: bool = True) -> None:
    dashboard = json.loads((root / "agent_results/dashboard_snapshot.json").read_text(encoding="utf-8"))
    if dashboard.get("status") != "ok":
        raise ValueError("Recovery dashboard is not healthy")
    workbook = root / "agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx"
    with zipfile.ZipFile(workbook) as archive:
        if archive.testzip() is not None:
            raise ValueError("Workbook integrity failed")
        xml = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        sheets = {node.attrib.get("name"): node for node in xml.iter() if node.tag.endswith("}sheet")}
        required = {"Open Positions", "Trade Log"}
        if not required.issubset(sheets):
            raise ValueError("Portfolio worksheets missing")
        relations = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {node.attrib["Id"]: node.attrib for node in relations}
        for name in required:
            relation_id = sheets[name].attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            relation = targets.get(relation_id, {})
            if relation.get("TargetMode") == "External" or not relation.get("Type", "").endswith("/worksheet"):
                raise ValueError("Invalid portfolio worksheet relationship")
            target = relation.get("Target", "")
            member = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
            if not member.startswith("xl/worksheets/") or "../" in member:
                raise ValueError("Unsafe portfolio worksheet relationship")
            try:
                worksheet = ElementTree.fromstring(archive.read(member))
            except KeyError:
                raise ValueError("Portfolio worksheet data missing") from None
            if not worksheet.tag.endswith("}worksheet") or not any(node.tag.endswith("}sheetData") for node in worksheet):
                raise ValueError("Invalid portfolio worksheet XML")
    if parse_workbook:
        validate_workbook_parser(workbook)
    receipts = root / "agent_results/telegram_notifications.jsonl"
    for line in receipts.read_text(encoding="utf-8").splitlines():
        if line.strip():
            receipt = json.loads(line)
            if not isinstance(receipt, dict) or not receipt.get("key") or not receipt.get("sent_at"):
                raise ValueError("Malformed notification receipt")
    if not (state / "deployed_revision").read_text().strip():
        raise ValueError("Empty deployment marker")
    for folder in ("monitor", "outbox"):
        for path in (state / folder).glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Malformed execution state")
            if folder == "outbox":
                validate_outbox(path.name, payload)


def validate_outbox(name: str, payload: dict) -> None:
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported notification outbox schema")
    if name == "scanner.json":
        records = payload.get("records")
        if not isinstance(records, list) or any(not isinstance(row, dict)
                or not all(isinstance(row.get(key), str) and row[key] for key in ("ticker", "message", "dedupe_key")) for row in records):
            raise ValueError("Malformed scanner notification outbox")
    elif name == "monitor.json":
        events = payload.get("events")
        required = {"ticker", "action", "triggered_at", "trigger_price", "high", "low", "close", "quantity", "cash_in", "note"}
        allowed = required | {"trade_id", "execution_details"}
        if not isinstance(events, list):
            raise ValueError("Malformed monitor notification outbox")
        for row in events:
            event = row.get("event") if isinstance(row, dict) else None
            if (not isinstance(row, dict) or not isinstance(row.get("position"), dict) or not isinstance(event, dict)
                    or not required.issubset(event) or set(event) - allowed):
                raise ValueError("Malformed monitor notification event")
            if (not isinstance(event["quantity"], int) or isinstance(event["quantity"], bool) or event["quantity"] < 0
                    or any(not isinstance(event[key], (int, float)) or isinstance(event[key], bool)
                           or not math.isfinite(event[key]) for key in ("trigger_price", "high", "low", "close", "cash_in"))
                    or any(not isinstance(event[key], str) for key in ("ticker", "action", "triggered_at", "note"))
                    or not event["ticker"] or not event["action"]
                    or not isinstance(event.get("execution_details", {}), dict)):
                raise ValueError("Invalid monitor notification values")
            position = row["position"]
            for key in ("entry_price", "stop_loss", "target_1", "target_2", "quantity"):
                value = position.get(key)
                if value is None:
                    continue
                try:
                    numeric = float(value)
                    if isinstance(value, bool) or not math.isfinite(numeric):
                        raise ValueError("Non-finite position value")
                    if key == "quantity" and (int(value) != numeric or numeric < 0):
                        raise ValueError("Invalid position quantity")
                except (TypeError, ValueError, OverflowError):
                    raise ValueError("Invalid monitor notification position values") from None
    else:
        raise ValueError("Unknown notification outbox kind")
