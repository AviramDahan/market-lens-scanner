"""Explicit host checkpoint backend; never restores files over the live portfolio."""
from __future__ import annotations

import json
import os
import math
import shutil
import hashlib
import tempfile
import zipfile
import subprocess
import sys
import argparse
from pathlib import Path

from .github_archive import GitHubArchive, incremental_restore_manifest, install_verified_receipt, publish_verified
from .host_archive import encoded, freeze, recovery_files, sha256, sync_directory, validate_manifest, validate_outbox
from .host_archive_runner import exclusive
from .notify_ops import env_value


TRACKER = "repo/agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx"
RECEIPTS = "repo/agent_results/telegram_notifications.jsonl"
REPO = Path("/home/trader/market-lens-runtime")
STATE = Path("/home/trader/market-lens-runtime-state")


def require_headroom(root: Path, extra_bytes: int) -> None:
    reserve = float(env_value("MARKET_LENS_ARCHIVE_MIN_FREE_GIB") or "12")
    if not math.isfinite(reserve) or reserve < 12:
        raise ValueError("Archive reserve must be at least 12 GiB")
    if shutil.disk_usage(root).free < reserve * 1024**3 + extra_bytes + 256 * 1024**2:
        raise RuntimeError("Insufficient checkpoint headroom; no success recorded")


def enabled() -> bool:
    backend = env_value("MARKET_LENS_HOST_BACKUP_BACKEND") or "legacy_git"
    if backend not in {"legacy_git", "verified_archive"}:
        raise RuntimeError("Unknown host backup backend; refusing an implicit fallback")
    return backend == "verified_archive"


class Checkpoints:
    def __init__(self, repo: Path, state: Path, *, store=None):
        self.repo, self.state = repo, state
        self.root = state / "checkpoints"
        self.store = store or GitHubArchive(env_value("MARKET_LENS_ARCHIVE_REPOSITORY"),
                                          env_value("MARKET_LENS_ARCHIVE_TOKEN"))

    def receipt(self) -> dict:
        result = json.loads((self.root / "last_verified.json").read_text())
        return self.validate_receipt(result)

    def validate_receipt(self, result: dict) -> dict:
        validate_manifest(result["manifest"])
        if result.get("repository") != self.store.repository:
            raise RuntimeError("Checkpoint repository differs from configured private archive")
        proof = result.get("proof", {})
        if (proof.get("generation") != result["manifest"]["generation"]
                or proof.get("repository") != self.store.repository
                or proof.get("manifest_sha256") != hashlib.sha256(encoded(result["manifest"])).hexdigest()
                or proof.get("workbook_verified") is not True
                or not (proof.get("all_hashes_verified") is True or proof.get("chain_verified") is True)):
            raise RuntimeError("Checkpoint has no verified restore proof")
        return result

    def matching_tracker(self, manifest: dict) -> None:
        entry = next(row for row in manifest["files"] if row["path"] == TRACKER)
        if sha256(self.repo / TRACKER.removeprefix("repo/")) != entry["sha256"]:
            raise RuntimeError("Live tracker differs from checkpoint; manual recovery required")

    def recover(self, *, bootstrap: bool = False) -> dict:
        """Caller holds writer.lock. Recover a frozen pending upload, never re-freeze it."""
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with exclusive(self.root / "publisher.lock"):
            receipt = (None if bootstrap and not (self.root / "last_verified.json").exists()
                       else self.receipt())
            pending = self.root / "pending.json"
            if pending.exists():
                metadata = json.loads(pending.read_text())
                generation = metadata["generation"]
                if len(generation) != 64 or any(c not in "0123456789abcdef" for c in generation):
                    raise ValueError("Invalid pending checkpoint generation")
                bundle = self.root / "generations" / generation
                manifest = json.loads((bundle / "manifest.json").read_text())
                validate_manifest(manifest)
                if manifest["generation"] != generation or metadata["repository"] != self.store.repository:
                    raise ValueError("Pending checkpoint identity changed")
                self.matching_tracker(manifest)
                # A crash may occur after receipt fsync but before pending unlink.
                if receipt is None or receipt["manifest"]["generation"] != generation:
                    require_headroom(self.root, self.restore_bytes(manifest, receipt))
                    receipt = self.publish(bundle, receipt)
                pending.unlink()
                sync_directory(self.root)
                self.cache_verified_generation(bundle, receipt)
            if receipt is None:
                raise RuntimeError("Bootstrap checkpoint has not been published")
            self.matching_tracker(receipt["manifest"])
            verified = {row["path"]: row for row in receipt["manifest"]["files"]}
            # Consumers remove an outbox before receipt backup. Rebuild only the
            # exact VERIFIED envelope, never cash/workbook, so existing durable
            # Telegram keys can reconcile even after a crash in that gap.
            for name, entry in verified.items():
                if name.startswith("runtime/outbox/") and not (self.state / name.removeprefix("runtime/")).exists():
                    self.restore_verified_outbox(receipt["manifest"], entry)
            for path in (self.state / "outbox").glob("*.json"):
                entry = verified.get("runtime/outbox/" + path.name)
                if not entry or sha256(path) != entry["sha256"]:
                    raise RuntimeError("Notification outbox is not backed by the verified checkpoint")
            return receipt

    def restore_verified_outbox(self, manifest: dict, entry: dict) -> None:
        generation = manifest["generation"] if entry["generation"] == "self" else entry["generation"]
        with tempfile.TemporaryDirectory(prefix="outbox-recovery-", dir=self.root) as temporary:
            asset = Path(temporary) / "verified.zip"
            local = self.root / "generations" / generation / entry["asset"]
            if local.exists() and sha256(local) == entry["asset_sha256"]:
                shutil.copyfile(local, asset)
            else:
                self.store.get(generation, entry["asset"], asset,
                               expected_sha=entry["asset_sha256"], expected_bytes=entry["asset_bytes"])
            if asset.stat().st_size != entry["asset_bytes"] or sha256(asset) != entry["asset_sha256"]:
                raise ValueError("Outbox recovery asset integrity failed")
            with zipfile.ZipFile(asset) as source:
                info = source.getinfo(entry["path"])
                if info.file_size != entry["bytes"] or info.file_size > 8 * 1024**2:
                    raise ValueError("Outbox recovery member is invalid")
                body = source.read(info)
            if hashlib.sha256(body).hexdigest() != entry["sha256"]:
                raise ValueError("Outbox recovery member hash failed")
            name = Path(entry["path"]).name
            validate_outbox(name, json.loads(body))
            target = self.state / "outbox" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise RuntimeError("Refusing to overwrite a live notification envelope")
            temporary_path = target.with_suffix(".recovering")
            with temporary_path.open("wb") as destination:
                destination.write(body)
                destination.flush()
                os.fsync(destination.fileno())
            temporary_path.replace(target)
            sync_directory(target.parent)

    def save(self, revision: str, *, bootstrap: bool = False) -> dict:
        """Caller holds writer.lock; successful remote read-back precedes notification."""
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with exclusive(self.root / "publisher.lock"):
            if (self.root / "pending.json").exists():
                raise RuntimeError("Pending checkpoint must be recovered before another write")
            previous = None if bootstrap else self.receipt()
            if bootstrap and (self.root / "last_verified.json").exists():
                raise RuntimeError("Bootstrap must not replace an existing checkpoint chain")
            self.store.require_private()
            prior_files = {row["path"]: row for row in (previous or {}).get("manifest", {}).get("files", [])}
            changed_bytes = sum(path.stat().st_size for name, path in recovery_files(self.repo, self.state).items()
                                if prior_files.get(name, {}).get("sha256") != sha256(path))
            # Freeze and restore are separate phases: reserve space for the
            # actual changed inputs first, then for the real downloaded parts.
            require_headroom(self.root, changed_bytes)
            bundle = freeze(self.repo, self.state, self.root / "generations",
                            source_revision=revision, base=(previous or {}).get("manifest"),
                            parse_source_workbook=False)
            pending = self.root / "pending.json"
            temporary = pending.with_suffix(".tmp")
            with temporary.open("wb") as target:
                target.write(encoded({"generation": bundle.name, "repository": self.store.repository, "state": "FROZEN"}))
                target.flush()
                os.fsync(target.fileno())
            temporary.replace(pending)
            sync_directory(self.root)
            manifest = json.loads((bundle / "manifest.json").read_text())
            require_headroom(self.root, self.restore_bytes(manifest, previous))
            receipt = self.publish(bundle, previous)
            pending.unlink()
            sync_directory(self.root)
            self.cache_verified_generation(bundle, receipt)
            return receipt

    def cache_verified_generation(self, bundle: Path, receipt: dict) -> None:
        try:
            self.record_verified_cache(bundle, receipt)
            self.prune_verified_cache()
        except Exception as error:
            # Remote portfolio durability and delivery recovery must not depend
            # on successful housekeeping of redundant local cache copies.
            print(f"Verified checkpoint cache cleanup deferred: {type(error).__name__}", file=sys.stderr)

    def record_verified_cache(self, bundle: Path, receipt: dict) -> None:
        # Mark only a remotely verified generation. Remote history is permanent;
        # this index owns redundant LOCAL compressed cache copies only.
        marker = bundle / "remote-verified.json"
        metadata = {"generation": bundle.name, "repository": self.store.repository,
                    "manifest_sha256": receipt["proof"]["manifest_sha256"],
                    "verified_at": receipt["verified_at"]}
        temporary = marker.with_suffix(".tmp")
        with temporary.open("wb") as target:
            target.write(encoded(metadata))
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(marker)
        index = self.root / "cache-index.json"
        try:
            items = json.loads(index.read_text()) if index.exists() else []
            if (not isinstance(items, list)
                    or any(not isinstance(item, str) or len(item) != 64
                           or any(c not in "0123456789abcdef" for c in item) for item in items)
                    or len(set(items)) != len(items)):
                raise ValueError("Invalid cache index")
        except (ValueError, OSError):
            # Rebuild only an inventory, not permission to delete. Each candidate
            # must still pass full marker/manifest/part verification below.
            items = sorted(path.name for path in (self.root / "generations").iterdir()
                           if path.is_dir() and not path.is_symlink()
                           and len(path.name) == 64 and all(c in "0123456789abcdef" for c in path.name)
                           and (path / "remote-verified.json").is_file())
            if bundle.name in items:
                items.remove(bundle.name)
        if bundle.name not in items:
            items.append(bundle.name)
        temporary = index.with_suffix(".tmp")
        with temporary.open("wb") as target:
            target.write(encoded(items))
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(index)
        sync_directory(self.root)

    def prune_verified_cache(self, keep: int = 2) -> None:
        if (self.root / "pending.json").exists():
            return
        index = self.root / "cache-index.json"
        items = json.loads(index.read_text()) if index.exists() else []
        if not isinstance(items, list) or len(set(items)) != len(items):
            raise ValueError("Invalid verified cache index")
        protected = set(items[-max(2, keep):])
        current = self.receipt()
        protected.add(current["manifest"]["generation"])
        for row in current["manifest"]["files"]:
            if row["path"].startswith("runtime/outbox/") or Path(row["path"]).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                protected.add(current["manifest"]["generation"] if row["generation"] == "self" else row["generation"])
        remaining = []
        for generation in items:
            if len(generation) != 64 or any(c not in "0123456789abcdef" for c in generation):
                raise ValueError("Invalid cache generation")
            if generation in protected:
                remaining.append(generation)
                continue
            try:
                self.prune_cached_generation(generation)
            except Exception as error:
                remaining.append(generation)
                print(f"Checkpoint cache evidence retained: {generation} ({type(error).__name__})", file=sys.stderr)
        temporary = index.with_suffix(".tmp")
        with temporary.open("wb") as target:
            target.write(encoded(remaining))
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(index)
        sync_directory(self.root)

    def prune_cached_generation(self, generation: str) -> None:
        bundle = self.root / "generations" / generation
        if not bundle.exists():
            return  # A prior prune may have finished before index fsync.
        if bundle.is_symlink() or bundle.resolve().parent != (self.root / "generations").resolve():
            raise ValueError("Unsafe checkpoint cache path")
        marker_path = bundle / "remote-verified.json"
        manifest_path = bundle / "manifest.json"
        if marker_path.is_symlink() or manifest_path.is_symlink():
            raise ValueError("Unsafe checkpoint cache metadata")
        marker = json.loads(marker_path.read_text())
        manifest = json.loads(manifest_path.read_text())
        validate_manifest(manifest)
        if (marker["generation"] != generation or marker["repository"] != self.store.repository
                or marker["manifest_sha256"] != sha256(manifest_path)):
            raise ValueError("Cache lacks matching remote verification")
        parts = {row["asset"]: row for row in manifest["files"] if row["generation"] == "self"}
        if {path.name for path in bundle.iterdir()} - (set(parts) | {"manifest.json", "remote-verified.json"}):
            raise ValueError("Unknown local evidence in cache generation; retaining it")
        for name, row in parts.items():
            path = bundle / name
            if path.exists():
                if (path.is_symlink() or path.resolve().parent != bundle.resolve()
                        or path.stat().st_size != row["asset_bytes"] or sha256(path) != row["asset_sha256"]):
                    raise ValueError("Refusing cleanup of an altered/unverified cache copy")
                path.unlink()  # Remote-verified duplicate, never live/unique history.
        manifest_path.unlink()
        marker_path.unlink()
        bundle.rmdir()

    def publish(self, bundle: Path, previous: dict | None) -> dict:
        if not isinstance(self.store, GitHubArchive):
            return publish_verified(bundle, self.root, self.store, previous=previous)
        # A stalled provider must not hold the portfolio writer indefinitely.
        # The parent owns both locks and preserves the frozen bundle on timeout.
        # Only disposable download/restore copies live in this attempt directory.
        with tempfile.TemporaryDirectory(prefix="publish-attempt-", dir=self.root) as scratch:
            try:
                subprocess.run([
                    sys.executable, "-m", "deploy.hetzner.archive_checkpoint", "publish-frozen",
                    "--generation", bundle.name, "--base",
                    previous["manifest"]["generation"] if previous else "bootstrap",
                    "--scratch", scratch,
                ], cwd=self.repo, check=True, capture_output=True, text=True,
                   timeout=30 if previous else 600)
            except subprocess.TimeoutExpired:
                raise RuntimeError("Checkpoint publication timed out; frozen data retained for retry") from None
            except subprocess.CalledProcessError:
                raise RuntimeError("Checkpoint publication failed; frozen data retained for retry") from None
            receipt = self.validate_receipt(json.loads((Path(scratch) / "verified-result.json").read_text()))
            if receipt["manifest"]["generation"] != bundle.name:
                raise RuntimeError("Publisher did not verify the requested checkpoint generation")
            pending = json.loads((self.root / "pending.json").read_text())
            current = self.receipt() if (self.root / "last_verified.json").exists() else None
            if (pending["generation"] != bundle.name
                    or (current or {}).get("manifest", {}).get("generation") != (previous or {}).get("manifest", {}).get("generation")):
                raise RuntimeError("Checkpoint cursor or pending generation changed; refusing stale proof")
            # Upload children may outlive a killed parent. They have no authority
            # to install a cursor; only this still-lock-owning parent can do so.
            install_verified_receipt(self.root, receipt)
            return receipt

    def restore_bytes(self, manifest: dict, previous: dict | None) -> int:
        checked = (incremental_restore_manifest(manifest, previous, self.store.repository)
                   if previous else manifest)
        return sum(row["bytes"] for row in checked["files"]) + max(row["asset_bytes"] for row in checked["files"])

    def receipts_changed(self, receipt: dict) -> bool:
        entry = next(row for row in receipt["manifest"]["files"] if row["path"] == RECEIPTS)
        return sha256(self.repo / RECEIPTS.removeprefix("repo/")) != entry["sha256"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("bootstrap", "recover-bootstrap", "status", "publish-frozen"))
    parser.add_argument("--generation")
    parser.add_argument("--base")
    parser.add_argument("--scratch", type=Path)
    args = parser.parse_args()
    checkpoints = Checkpoints(REPO, STATE)
    if args.mode == "publish-frozen":
        if (not args.generation or len(args.generation) != 64
                or any(c not in "0123456789abcdef" for c in args.generation)
                or args.scratch is None or args.scratch.resolve().parent != checkpoints.root.resolve()):
            raise ValueError("Invalid internal checkpoint publication request")
        previous = None if args.base == "bootstrap" else checkpoints.receipt()
        if previous and previous["manifest"]["generation"] != args.base:
            raise ValueError("Checkpoint cursor changed before publication")
        bundle = checkpoints.root / "generations" / args.generation
        manifest = json.loads((bundle / "manifest.json").read_text())
        checkpoints.matching_tracker(manifest)
        receipt = publish_verified(bundle, checkpoints.root, checkpoints.store, previous=previous,
                                   scratch_root=args.scratch, install_cursor=False)
        result = args.scratch / "verified-result.json"
        with result.open("wb") as destination:
            destination.write(encoded(receipt))
            destination.flush()
            os.fsync(destination.fileno())
    elif args.mode == "status":
        receipt = checkpoints.receipt()
        print(json.dumps({"backend": "verified_archive" if enabled() else "legacy_git",
                          "verified_at": receipt["verified_at"], "proof": receipt["proof"]}))
    else:
        with exclusive(STATE / "writer.lock"):
            if args.mode == "recover-bootstrap":
                receipt = checkpoints.recover(bootstrap=True)
            else:
                revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO,
                                                   text=True, timeout=30).strip()
                receipt = checkpoints.save(revision, bootstrap=True)
        print(json.dumps({"verified_at": receipt["verified_at"], "proof": receipt["proof"]}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Checkpoint failed: {type(error).__name__}; no success claimed", file=sys.stderr)
        raise SystemExit(1)
