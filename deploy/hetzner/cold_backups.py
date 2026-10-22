"""Offload closed Market Lens backups only after a private, byte-exact round trip."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
from datetime import datetime, timedelta, timezone, time as wall_time
from pathlib import Path, PurePosixPath
from urllib.parse import quote
from zoneinfo import ZoneInfo

try:
    import fcntl
except ImportError:
    fcntl = None

from .github_archive import ArchiveAPIError, GitHubArchive
from .host_archive import MAX_ASSET_BYTES, encoded, sha256, sync_directory
from .notify_ops import env_value

BACKUPS = Path('/home/trader/market-lens-backups')
STATE = Path('/home/trader/market-lens-runtime-state/maintenance')
LIVE = Path('/home/trader/market-lens-runtime')
SECRET = re.compile(rb'(?:\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}|'
                    rb'\b[0-9]{7,12}:[A-Za-z0-9_-]{30,50}\b|'
                    rb'"(?:password|service_role_key|private_key)"\s*:\s*"[^"\s][^"]*")', re.I)


class UnsafeBackup(ValueError):
    pass


def signature(path: Path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


def safe_file(root: Path, path: Path) -> None:
    if root.is_symlink() or path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Unsafe cold backup path')
    if any(parent.is_symlink() for parent in path.parents if parent != root.parent):
        raise ValueError('Symlink in cold backup path')


def validate_backup(path: Path) -> None:
    """No extraction; reject secrets, links, traversal and non-portfolio content."""
    count = total = 0
    with tarfile.open(path, 'r|*') as archive:
        for member in archive:
            count += 1
            name = PurePosixPath(member.name)
            parts = name.parts
            if name.is_absolute() or '..' in parts or member.issym() or member.islnk():
                raise UnsafeBackup('Unsafe archive member')
            if not member.isfile() and not member.isdir():
                raise UnsafeBackup('Unsupported archive member')
            lower = [part.lower() for part in parts]
            if any('.env' in part or part in {'.git', '.ssh', '.config', 'id_ed25519', 'id_rsa'}
                   or part.endswith(('.pem', '.key', '.p12')) for part in lower):
                raise UnsafeBackup('Backup contains sensitive paths; retaining it locally')
            if member.isdir():
                continue
            state = lower.index('market-lens-runtime-state') + 1 if 'market-lens-runtime-state' in lower else -1
            allowed_state = state >= 0 and state < len(lower) and lower[state] in {
                'monitor', 'outbox', 'deployed_revision', 'checkpoints'}
            if not ({'agent_tracker', 'agent_results'} & set(lower) or allowed_state):
                raise UnsafeBackup('Backup contains non-portfolio files; retaining it locally')
            total += member.size
            if count > 250_000 or total > 50 * 1024**3:
                raise UnsafeBackup('Backup inventory exceeds validation bound')
            if name.suffix.lower() in {'.json', '.jsonl', '.txt', '.md', '.log', '.yaml', '.yml', '.py', '.sh'}:
                previous = b''
                stream = archive.extractfile(member)
                while block := stream.read(1024**2):
                    if SECRET.search(previous + block):
                        raise UnsafeBackup('Backup contains sensitive content; retaining it locally')
                    previous = block[-512:]


def release_for(store, metadata: dict):
    tag = 'cold-backup-v1-' + hashlib.sha256(encoded(metadata)).hexdigest()
    url = store.base + '/releases/tags/' + quote(tag, safe='')
    try:
        return tag, store.json(url)
    except ArchiveAPIError as exc:
        if exc.status != 404:
            raise
    try:
        return tag, store.json(store.base + '/releases', payload={
            'tag_name': tag, 'name': tag, 'draft': False, 'prerelease': True,
            'make_latest': 'false', 'body': 'Immutable cold portfolio backup; not a live checkpoint. No credentials.'})
    except ArchiveAPIError as exc:
        if exc.status != 422:
            raise
        return tag, store.json(url)


def verify_asset(store, asset: dict, expected_sha: str, expected_bytes: int, *, destination=None) -> None:
    if asset.get('state') != 'uploaded' or asset.get('size') != expected_bytes:
        raise ValueError('Cold backup asset incomplete')
    total, digest = 0, hashlib.sha256()
    deadline = time.monotonic() + 600
    with store.request(f"{store.base}/releases/assets/{int(asset['id'])}",
                       accept='application/octet-stream') as response:
        while block := response.read(1024**2):
            total += len(block)
            if total > expected_bytes or time.monotonic() > deadline:
                raise ValueError('Cold backup verification exceeded its bound')
            digest.update(block)
            if destination is not None:
                destination.write(block)
    if total != expected_bytes or digest.hexdigest() != expected_sha:
        raise ValueError('Cold backup round-trip hash mismatch')


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(encoded(value)); stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)
    sync_directory(path.parent)


def put_missing(store, release, name, path):
    # Retries verify existing assets below by streaming, not a second large local download.
    if not any((row['name'] == name or row['name'].startswith(name+'.retry-'))
               and row.get('state') == 'uploaded' for row in store.assets(release)):
        store.put(release, name, path)


def offload_file(root: Path, path: Path, store, *, now=None) -> dict:
    safe_file(root, path)
    before = signature(path)
    if not 0 < before[0] < MAX_ASSET_BYTES:
        raise ValueError('Backup size requires a separate multipart migration')
    validate_backup(path)
    digest = sha256(path)
    if signature(path) != before:
        raise ValueError('Backup changed during validation; retaining it locally')
    metadata = {'schema_version': 1, 'kind': 'COLD_PORTFOLIO_BACKUP',
                'relative_path': path.relative_to(root).as_posix(), 'bytes': before[0], 'sha256': digest}
    store.require_private()
    tag, release = release_for(store, metadata)
    put_missing(store, release, 'backup.tar', path)
    with tempfile.TemporaryDirectory(prefix='cold-backup-metadata-') as directory:
        index = Path(directory)/'metadata.json'; index.write_bytes(encoded(metadata))
        put_missing(store, release, 'cold-backup-metadata.json', index)
    assets = store.assets(release)
    selected = {}
    for name, expected, size in [('backup.tar', digest, before[0]),
                                  ('cold-backup-metadata.json', hashlib.sha256(encoded(metadata)).hexdigest(), len(encoded(metadata)))]:
        matches = [item for item in assets if (item['name'] == name or item['name'].startswith(name+'.retry-'))
                   and item.get('state') == 'uploaded']
        if not matches:
            raise ValueError('Cold backup asset missing')
        for asset in matches:
            verify_asset(store, asset, expected, size)
        selected[name] = matches[0]['id']
    safe_file(root, path)
    if signature(path) != before or sha256(path) != digest:
        raise ValueError('Original backup changed; retaining it locally')
    store.require_private()
    receipt = {**metadata, 'repository': store.repository, 'release_tag': tag,
               'asset_id': selected['backup.tar'], 'metadata_asset_id': selected['cold-backup-metadata.json'],
               'verified_at': (now or datetime.now(timezone.utc)).isoformat(), 'remote_verified': True}
    pointer = path.with_name(path.name+'.archive.json')
    if pointer.exists() and json.loads(pointer.read_text()).get('sha256') != digest:
        raise ValueError('Existing cold backup receipt differs')
    atomic_json(pointer, receipt)
    safe_file(root, path)
    if signature(path) != before:
        raise ValueError('Original backup changed before retirement')
    path.unlink()  # Verified duplicate only; the complete original lives in private GitHub Releases.
    sync_directory(path.parent)
    return receipt


def archive_once(*, state=STATE, now=None, **kwargs) -> dict:
    if env_value('MARKET_LENS_COLD_BACKUP_ARCHIVE_ENABLED').lower() != 'true':
        return {'status': 'DISABLED', 'archived_bytes': 0}
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo('America/New_York'))
    if local.weekday() < 5 and wall_time(9) <= local.time() <= wall_time(16, 30):
        return {'status': 'DEFERRED_MARKET_WINDOW', 'archived_bytes': 0}
    if fcntl is None:
        return {'status': 'UNSUPPORTED_HOST', 'archived_bytes': 0}
    state.mkdir(parents=True, exist_ok=True)
    with (state/'cold-backups.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'SKIPPED_BUSY', 'archived_bytes': 0}
        return _archive_once(now=now, **kwargs)


def _archive_once(*, root=BACKUPS, store=None, now=None, target_free=20*1024**3,
                 max_bytes=2*1024**3, free_bytes=None) -> dict:
    if env_value('MARKET_LENS_COLD_BACKUP_ARCHIVE_ENABLED').lower() != 'true':
        return {'status': 'DISABLED', 'archived_bytes': 0}
    if not root.is_dir() or root.is_symlink():
        return {'status': 'NO_BACKUP_DIRECTORY', 'archived_bytes': 0}
    now = now or datetime.now(timezone.utc)
    free_bytes = free_bytes or (lambda: shutil.disk_usage(root).free)
    paths = sorted((p for p in root.rglob('*') if p.is_file() and not p.is_symlink()
                    and (p.name.endswith('.tar.gz') or p.suffix == '.tar')),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    protected = set(paths[:2])
    candidates = [p for p in reversed(paths) if p not in protected
                  and datetime.fromtimestamp(p.stat().st_mtime, timezone.utc) <= now-timedelta(days=7)
                  and 0 < p.stat().st_size < MAX_ASSET_BYTES]
    if not candidates or free_bytes() >= target_free:
        return {'status': 'NO_OFFLOAD_NEEDED', 'archived_bytes': 0}
    store = store or GitHubArchive(env_value('MARKET_LENS_ARCHIVE_REPOSITORY'), env_value('MARKET_LENS_ARCHIVE_TOKEN'))
    receipts, skipped, moved = [], [], 0
    deadline = time.monotonic()+600
    for path in candidates:
        if free_bytes() >= target_free or time.monotonic() >= deadline:
            break
        if moved+path.stat().st_size > max_bytes:
            continue
        try:
            receipt = offload_file(root, path, store, now=now)
        except (UnsafeBackup, tarfile.TarError):
            skipped.append(path.relative_to(root).as_posix())
            continue  # Sensitive/unknown formats stay local, never partially uploaded/deleted.
        moved += receipt['bytes']; receipts.append(receipt)
    return {'status': 'ARCHIVED' if receipts else 'RETAINED_NO_SAFE_CANDIDATES',
            'archived_bytes': moved, 'receipts': receipts, 'retained_files': skipped}


def restore_receipt(receipt: dict, destination: Path, store) -> None:
    if (receipt.get('remote_verified') is not True or receipt.get('repository') != store.repository
            or not 0 < receipt.get('bytes', 0) < MAX_ASSET_BYTES
            or not re.fullmatch('[a-f0-9]{64}', str(receipt.get('sha256') or ''))
            or destination.exists() or destination.resolve().is_relative_to(LIVE.resolve())):
        raise ValueError('Restore requires a matching receipt and a new non-live destination')
    store.require_private()
    descriptor, name = tempfile.mkstemp(prefix='cold-restore-', dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            verify_asset(store, {'id': receipt['asset_id'], 'size': receipt['bytes'], 'state': 'uploaded'},
                         receipt['sha256'], receipt['bytes'], destination=stream)
            stream.flush(); os.fsync(stream.fileno())
        validate_backup(temporary)
        if destination.exists():
            raise ValueError('Restore destination appeared')
        os.link(temporary, destination); sync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--restore-receipt', type=Path)
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args()
    if args.restore_receipt:
        if args.destination is None:
            parser.error('Explicit new non-live destination required')
        store = GitHubArchive(env_value('MARKET_LENS_ARCHIVE_REPOSITORY'), env_value('MARKET_LENS_ARCHIVE_TOKEN'))
        restore_receipt(json.loads(args.restore_receipt.read_text()), args.destination, store)
        print('Cold backup restored; no live portfolio was modified.')
    else:
        print(json.dumps(archive_once()), flush=True)
