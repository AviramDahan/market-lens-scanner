"""Read-only archive mirror; deliberately does not replace the active backup contract."""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

if __package__:
    from .github_archive import GitHubArchive, publish_verified
    from .host_archive import encoded, freeze, recovery_files, restore, sync_directory, validate_manifest
    from .notify_ops import env_value
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from deploy.hetzner.github_archive import GitHubArchive, publish_verified
    from deploy.hetzner.host_archive import encoded, freeze, recovery_files, restore, sync_directory, validate_manifest
    from deploy.hetzner.notify_ops import env_value

try:
    import fcntl
except ImportError:
    fcntl = None

REPO = Path("/home/trader/market-lens-runtime")
STATE = Path("/home/trader/market-lens-runtime-state")


@contextmanager
def exclusive(path: Path):
    if fcntl is None:
        raise RuntimeError("Archive runner requires Linux flock")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def pending_bundle(archive: Path) -> Path:
    pending = archive / "pending.json"
    spool = archive / "generations"
    configured = env_value("MARKET_LENS_ARCHIVE_REPOSITORY")
    base = None
    verified = archive / "last_verified.json"
    previous_repository = None
    if verified.exists():
        receipt = json.loads(verified.read_text())
        base = receipt["manifest"]
        validate_manifest(base)
        previous_repository = receipt.get("repository")
        if configured and previous_repository != configured:
            raise ValueError("Archive destination changed; do not reuse old references")
    if pending.exists():
        metadata = json.loads(pending.read_text())
        generation = metadata["generation"]
        if len(generation) != 64 or any(c not in "0123456789abcdef" for c in generation):
            raise ValueError("Invalid pending generation")
        bundle = spool / generation
        manifest = json.loads((bundle / "manifest.json").read_text())
        validate_manifest(manifest)
        if manifest["generation"] != generation:
            raise ValueError("Pending generation mismatch")
        target = metadata.get("repository")
        if configured and target and target != configured:
            raise ValueError("Pending archive destination changed")
        if any(row["generation"] != "self" for row in manifest["files"]) and not target:
            raise ValueError("Inherited pending assets have no repository identity")
        require_headroom(sum(row["bytes"] for row in manifest["files"]))
        return bundle
    with exclusive(STATE / "writer.lock"):
        from_size = sum(path.stat().st_size for path in recovery_files(REPO, STATE).values())
        require_headroom(from_size)
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True, timeout=30).strip()
        bundle = freeze(REPO, STATE, spool, source_revision=revision, base=base)
    temporary = pending.with_suffix(".tmp")
    with temporary.open("wb") as target:
        target.write(encoded({"generation": bundle.name, "repository": configured or previous_repository}))
        target.flush()
        os.fsync(target.fileno())
    temporary.replace(pending)
    sync_directory(archive)
    return bundle


def require_headroom(source_bytes: int) -> None:
    minimum = float(env_value("MARKET_LENS_ARCHIVE_MIN_FREE_GIB") or "12")
    if not math.isfinite(minimum) or minimum < 12:
        raise ValueError("Archive free-space reserve must be at least 12 GiB")
    if shutil.disk_usage(STATE).free < minimum * 1024**3 + source_bytes * 2.2 + 256 * 1024**2:
        raise RuntimeError("Insufficient archive/restore disk headroom")


def quarantine_pending(archive: Path, reason: str) -> None:
    pending = archive / "pending.json"
    if not pending.exists():
        raise ValueError("No pending generation to quarantine")
    quarantine = archive / "quarantine"
    quarantine.mkdir(exist_ok=True, mode=0o700)
    name = uuid.uuid4().hex
    (quarantine / f"{name}.reason.json").write_bytes(encoded({"reason": reason}))
    pending.rename(quarantine / f"{name}.pending.json")
    sync_directory(quarantine)
    sync_directory(archive)
    # The immutable bundle is retained. This is an explicit operator action,
    # never automatic replacement of a failed backup with a newer generation.


def local_restore(bundle: Path, spool: Path) -> dict:
    manifest = json.loads((bundle / "manifest.json").read_text())

    def local(generation, name, output):
        source = spool / generation / name
        if source.is_symlink():
            raise ValueError("Symlink local archive asset")
        shutil.copyfile(source, output)

    with tempfile.TemporaryDirectory(prefix="local-restore-", dir=spool.parent) as temporary:
        return restore(manifest, Path(temporary) / "restored", local)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("local-check", "publish", "status", "quarantine-pending"))
    parser.add_argument("--reason", choices=("invalid-source", "operator-review"))
    args = parser.parse_args(argv)
    archive = STATE / "archive"
    if args.mode == "status":
        path = archive / "last_verified.json"
        receipt = json.loads(path.read_text()) if path.exists() else {}
        print(json.dumps({"active_backup_backend": "legacy_git_unchanged",
                          "verified_at": receipt.get("verified_at"),
                          "proof": receipt.get("proof"), "pending": (archive / "pending.json").exists()}))
        return 0
    if args.mode == "quarantine-pending":
        if not args.reason:
            parser.error("quarantine-pending requires --reason")
        with exclusive(archive / "publisher.lock"):
            quarantine_pending(archive, args.reason)
        print("Pending generation quarantined; no bundle or historical data deleted")
        return 0
    store = None
    if args.mode == "publish":
        store = GitHubArchive(env_value("MARKET_LENS_ARCHIVE_REPOSITORY"), env_value("MARKET_LENS_ARCHIVE_TOKEN"))
        store.require_private()  # Refuse public storage before freezing/uploading host-only state.
    with exclusive(archive / "publisher.lock"):
        bundle = pending_bundle(archive)
        if store is None:
            proof = local_restore(bundle, archive / "generations")
            print(json.dumps({"status": "LOCAL_RESTORE_VERIFIED_ONLY", **proof}))
        else:
            receipt = publish_verified(bundle, archive, store)
            (archive / "pending.json").unlink()
            print(json.dumps({"status": "REMOTE_RESTORE_VERIFIED", **receipt["proof"]}))
    return 0


def cli() -> int:
    try:
        return main()
    except BlockingIOError:
        print("Archive check deferred: publisher or portfolio writer is busy")
        return 3
    except Exception as error:
        # Transport exceptions may contain signed URLs; emit only the error type.
        print(f"Archive check failed: {type(error).__name__}; no success receipt written", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
