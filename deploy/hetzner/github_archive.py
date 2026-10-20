"""Append-only GitHub Release storage using a dedicated repository-scoped token."""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .host_archive import MAX_ASSET_BYTES, REQUIRED, encoded, restore, sha256, validate_manifest
import hashlib


class ArchiveAPIError(RuntimeError):
    def __init__(self, status: int | None):
        self.status = status
        super().__init__(f"Archive API request failed (status {status or 'network'})")


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        origin = urlparse(request.full_url)
        target = urlparse(newurl)
        if (target.scheme != "https" or target.username or target.password
                or not (target.hostname == "api.github.com"
                        or (target.hostname or "").endswith(".githubusercontent.com"))):
            raise ArchiveAPIError(None)
        redirected = super().redirect_request(request, fp, code, message, headers, newurl)
        if redirected is not None and (origin.hostname, origin.port) != (target.hostname, target.port):
            redirected.remove_header("Authorization")
        return redirected


class GitHubArchive:
    def __init__(self, repository: str, token: str, *, opener=None):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]+", repository) or not token.strip():
            raise ValueError("Archive repository and dedicated token required")
        self.repository = repository
        self._token = token.strip()
        self.opener = opener or build_opener(SafeRedirect())
        self.base = f"https://api.github.com/repos/{repository}"

    def request(self, url, *, data=None, method="GET", accept="application/vnd.github+json", extra=None):
        parsed = urlparse(url)
        root = "/repos/" + self.repository
        if (parsed.scheme != "https" or parsed.hostname not in {"api.github.com", "uploads.github.com"}
                or parsed.port not in {None, 443} or parsed.username or parsed.password
                or not (parsed.path == root or parsed.path.startswith(root + "/"))):
            raise ValueError("Unexpected archive API origin")
        headers = {"Authorization": "Bearer " + self._token, "Accept": accept,
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "market-lens-host-archive"}
        headers.update(extra or {})
        try:
            return self.opener.open(Request(url, data=data, headers=headers, method=method), timeout=60)
        except HTTPError as error:
            raise ArchiveAPIError(error.code) from None
        except (URLError, OSError):
            raise ArchiveAPIError(None) from None

    def json(self, url, *, payload=None):
        body = encoded(payload) if payload is not None else None
        with self.request(url, data=body, method="POST" if body is not None else "GET",
                          extra={"Content-Type": "application/json"} if body is not None else None) as response:
            data = response.read(8 * 1024**2 + 1)
            if len(data) > 8 * 1024**2:
                raise ValueError("Oversized archive API response")
            return json.loads(data)

    def require_private(self):
        metadata = self.json(self.base)
        if metadata.get("full_name", "").lower() != self.repository.lower() or metadata.get("private") is not True:
            raise ValueError("Host recovery archive requires the configured PRIVATE repository")

    def release(self, tag: str, *, create=False):
        if not re.fullmatch(r"host-v1-[a-f0-9]{64}", tag):
            raise ValueError("Unexpected archive tag")
        try:
            return self.json(self.base + "/releases/tags/" + quote(tag, safe=""))
        except ArchiveAPIError as error:
            if error.status != 404 or not create:
                raise
        try:
            return self.json(self.base + "/releases", payload={
                "tag_name": tag, "name": tag, "draft": False, "prerelease": True,
                "make_latest": "false", "body": "Market Lens host structured recovery. Append-only assets; no credentials."})
        except ArchiveAPIError as error:
            if error.status != 422:
                raise
            return self.release(tag)  # A concurrent/retried creator may have won.

    def assets(self, release):
        rows = []
        for page in range(1, 11):
            batch = self.json(f"{self.base}/releases/{int(release['id'])}/assets?per_page=100&page={page}")
            if not isinstance(batch, list):
                raise ValueError("Invalid asset inventory")
            rows.extend(batch)
            if len(batch) < 100:
                return rows
        raise ValueError("Archive release asset inventory exceeds bound")

    def put(self, release, name: str, path: Path):
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", name) or not 0 < path.stat().st_size < MAX_ASSET_BYTES:
            raise ValueError("Invalid upload asset")
        inventory = self.assets(release)
        completed = [item for item in inventory if (item["name"] == name or item["name"].startswith(name + ".retry-"))
                     and item.get("state") == "uploaded"]
        if completed:
            # Never overwrite a completed asset, even on retries.
            for item in completed:
                if item.get("size") != path.stat().st_size:
                    raise ValueError("Existing immutable asset size differs")
                with tempfile.TemporaryDirectory() as temporary:
                    downloaded = Path(temporary) / "existing"
                    self.download_id(item["id"], downloaded, path.stat().st_size)
                    if sha256(downloaded) != sha256(path):
                        raise ValueError("Existing immutable asset hash differs")
            return
        names = {item["name"] for item in inventory}
        selected = name
        for attempt in range(1, 101):
            if selected not in names:
                break
            selected = f"{name}.retry-{attempt}"
        else:
            raise ValueError("Too many incomplete upload attempts")
        # Failed starter assets remain intact; a retry gets a new name, not DELETE.
        upload = f"https://uploads.github.com/repos/{self.repository}/releases/{int(release['id'])}/assets?name={quote(selected)}"
        with path.open("rb") as source, self.request(upload, data=source, method="POST", extra={
                "Content-Type": "application/octet-stream", "Content-Length": str(path.stat().st_size)}) as response:
            response.read(1024**2)

    def download_id(self, asset_id: int, destination: Path, expected_bytes: int):
        total = 0
        with self.request(f"{self.base}/releases/assets/{int(asset_id)}", accept="application/octet-stream") as response:
            with destination.open("xb") as target:
                while block := response.read(1024**2):
                    total += len(block)
                    if total > expected_bytes or total >= MAX_ASSET_BYTES:
                        raise ValueError("Asset download exceeds expected size")
                    target.write(block)
        if total != expected_bytes:
            raise ValueError("Asset download truncated")

    def get(self, generation: str, name: str, destination: Path, *, expected_sha=None, expected_bytes=None):
        release = self.release("host-v1-" + generation)
        matches = [item for item in self.assets(release)
                   if (item["name"] == name or item["name"].startswith(name + ".retry-")) and item.get("state") == "uploaded"]
        if not matches:
            raise ValueError("Recovery asset unavailable")
        item = matches[0]
        size = item.get("size", 0)
        if not 0 < size < MAX_ASSET_BYTES or (expected_bytes is not None and size != expected_bytes):
            raise ValueError("Recovery asset size differs")
        self.download_id(item["id"], destination, size)
        if expected_sha is not None and sha256(destination) != expected_sha:
            raise ValueError("Recovery asset hash differs")


def incremental_restore_manifest(manifest: dict, previous: dict, repository: str) -> dict:
    """Verify changed/critical assets now, and inherit only previously proven refs."""
    base = previous["manifest"]
    validate_manifest(base)
    proof = previous.get("proof", {})
    if (previous.get("repository") != repository or proof.get("repository") != repository
            or proof.get("manifest_sha256") != hashlib.sha256(encoded(base)).hexdigest()
            or proof.get("generation") != base["generation"]
            or not (proof.get("all_hashes_verified") is True or proof.get("chain_verified") is True)):
        raise ValueError("Incremental checkpoint requires verified same-repository ancestry")
    prior = {entry["path"]: entry for entry in base["files"]}
    selected = []
    for entry in manifest["files"]:
        if entry["generation"] != "self":
            expected = dict(prior.get(entry["path"], {}))
            if expected.get("generation") == "self":
                expected["generation"] = base["generation"]
            if entry != expected:
                raise ValueError("Unverified inherited checkpoint reference")
        if (entry["generation"] == "self" or entry["path"] in REQUIRED
                or entry["path"].startswith("runtime/")
                or Path(entry["path"]).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}):
            copied = dict(entry)
            if copied["generation"] == "self":
                copied["generation"] = manifest["generation"]
            selected.append(copied)
    body = {key: value for key, value in manifest.items() if key != "generation"}
    body["files"] = selected
    body["generation"] = hashlib.sha256(encoded(body)).hexdigest()
    validate_manifest(body)
    return body


def publish_verified(bundle: Path, state: Path, store, *, previous: dict | None = None,
                     scratch_root: Path | None = None, install_cursor: bool = True) -> dict:
    """Caller serializes publishers. Upload and restore OUTSIDE portfolio writer.lock."""
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest(manifest)
    store.require_private()
    generation = manifest["generation"]
    release = store.release("host-v1-" + generation, create=True)
    for name in sorted({entry["asset"] for entry in manifest["files"] if entry["generation"] == "self"}):
        store.put(release, name, bundle / name)
    store.put(release, "manifest.json", manifest_path)
    with tempfile.TemporaryDirectory(prefix="verify-", dir=scratch_root or bundle.parent) as temporary:
        temporary = Path(temporary)
        downloaded = temporary / "manifest.json"
        store.get(generation, "manifest.json", downloaded,
                  expected_sha=sha256(manifest_path), expected_bytes=manifest_path.stat().st_size)
        downloaded_manifest = json.loads(downloaded.read_text())
        checked = (incremental_restore_manifest(downloaded_manifest, previous, store.repository)
                   if previous is not None else downloaded_manifest)
        restored = restore(checked, temporary / "restored", store.get)
        proof = {**restored, "manifest_sha256": sha256(manifest_path), "repository": store.repository}
        proof["delivery_state"] = ("VERIFIED_PENDING_DELIVERY" if any(
            row["path"].startswith("runtime/outbox/") for row in manifest["files"]) else "COMPLETE")
        if previous is not None:
            proof.update(generation=manifest["generation"], all_hashes_verified=False,
                         chain_verified=True, verification_mode="incremental_remote_restore",
                         previous_generation=previous["manifest"]["generation"],
                         inventory_file_count=len(manifest["files"]),
                         verified_now_file_count=len(checked["files"]))
        proof_path = temporary / "verified.json"
        proof_path.write_bytes(encoded(proof))
        store.put(release, "verified.json", proof_path)
        downloaded_proof = temporary / "verified.download.json"
        store.get(generation, "verified.json", downloaded_proof,
                  expected_sha=sha256(proof_path), expected_bytes=proof_path.stat().st_size)
    # A durable success receipt is written only after the remote read-back restore.
    receipt = {"version": "market_lens_host_backup_receipt_v1", "repository": store.repository,
               "verified_at": datetime.now(timezone.utc).isoformat(), "proof": proof, "manifest": manifest}
    if install_cursor:
        install_verified_receipt(state, receipt)
    return receipt


def install_verified_receipt(state: Path, receipt: dict) -> None:
    """Only the process owning the checkpoint locks may install this cursor."""
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = state / "last_verified.tmp"
    with temporary.open("wb") as target:
        target.write(encoded(receipt))
        target.flush()
        os.fsync(target.fileno())
    temporary.replace(state / "last_verified.json")
    if hasattr(os, "O_DIRECTORY"):
        directory = os.open(state, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
