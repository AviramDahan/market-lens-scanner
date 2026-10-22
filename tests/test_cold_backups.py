import io
import json
import os
import tarfile
from datetime import datetime, timedelta, timezone

import pytest

from deploy.hetzner import cold_backups as cold
from deploy.hetzner.github_archive import ArchiveAPIError

NOW = datetime(2026, 10, 22, 1, tzinfo=timezone.utc)


class Store:
    repository = 'owner/private-archive'
    base = 'https://api.github.com/repos/owner/private-archive'

    def __init__(self, *, private=True, corrupt=False):
        self.private = private
        self.corrupt = corrupt
        self.release = None
        self.files = {}
        self.uploads = 0

    def require_private(self):
        if not self.private:
            raise ValueError('Not private')

    def json(self, url, *, payload=None):
        if payload:
            self.release = {'id': 1, **payload}
        if not self.release:
            raise ArchiveAPIError(404)
        return self.release

    def assets(self, release):
        return [{'id': index, 'name': name, 'size': len(data), 'state': 'uploaded'}
                for index, (name, data) in enumerate(self.files.items(), 1)]

    def put(self, release, name, path):
        self.uploads += 1
        self.files[name] = path.read_bytes()

    def request(self, url, **kwargs):
        value = list(self.files.values())[int(url.rsplit('/', 1)[-1])-1]
        return io.BytesIO(value[:-1] if self.corrupt else value)


def backup(root, name='portfolio.tar.gz', *, member='market-lens-runtime/agent_tracker/portfolio.xlsx', body=b'paper ledger'):
    path = root/name
    with tarfile.open(path, 'w:gz') as archive:
        info = tarfile.TarInfo(member); info.size = len(body)
        archive.addfile(info, io.BytesIO(body))
    return path


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(cold, 'env_value', lambda name: 'true' if name == 'MARKET_LENS_COLD_BACKUP_ARCHIVE_ENABLED' else '')


def test_private_round_trip_keeps_complete_remote_original_and_durable_pointer(tmp_path):
    original = backup(tmp_path)
    data = original.read_bytes(); store = Store()
    receipt = cold.offload_file(tmp_path, original, store, now=NOW)
    assert not original.exists()
    assert store.files['backup.tar'] == data
    pointer = json.loads((tmp_path/'portfolio.tar.gz.archive.json').read_text())
    assert pointer == receipt and pointer['remote_verified'] is True
    assert pointer['repository'] == store.repository
    destination = tmp_path/'restored.tar.gz'
    cold.restore_receipt(receipt, destination, store)
    assert destination.read_bytes() == data


@pytest.mark.parametrize('private,corrupt', [(False, False), (True, True)])
def test_failed_verification_never_retires_original(tmp_path, private, corrupt):
    original = backup(tmp_path); before = original.read_bytes()
    with pytest.raises(ValueError):
        cold.offload_file(tmp_path, original, Store(private=private, corrupt=corrupt), now=NOW)
    assert original.read_bytes() == before
    assert not list(tmp_path.glob('*.archive.json'))


@pytest.mark.parametrize('member,body', [
    ('market-lens-runtime/.env', b'credentials'),
    ('../outside', b'bad'),
    ('market-lens-runtime/app/code.py', b'non-financial'),
    ('market-lens-runtime/agent_results/a.json', b'{"password":"not-a-real-secret"}'),
    ('market-lens-runtime/agent_results/a.json', b'ghp_'+b'x'*40),
])
def test_sensitive_or_non_portfolio_files_are_never_uploaded(tmp_path, member, body):
    path = backup(tmp_path, member=member, body=body); store = Store()
    with pytest.raises(ValueError):
        cold.offload_file(tmp_path, path, store, now=NOW)
    assert path.exists() and store.uploads == 0


def test_interrupted_retirement_can_retry_without_duplicate_uploads(tmp_path, monkeypatch):
    original = backup(tmp_path); store = Store()
    real_atomic = cold.atomic_json
    monkeypatch.setattr(cold, 'atomic_json', lambda *args: (_ for _ in ()).throw(OSError('simulated disk error')))
    with pytest.raises(OSError):
        cold.offload_file(tmp_path, original, store, now=NOW)
    assert original.exists() and store.uploads == 2
    monkeypatch.setattr(cold, 'atomic_json', real_atomic)
    cold.offload_file(tmp_path, original, store, now=NOW+timedelta(minutes=5))
    assert not original.exists() and store.uploads == 2


def test_age_keep_last_two_and_byte_budget_are_respected(tmp_path):
    old = backup(tmp_path, 'old.tar.gz')
    middle = backup(tmp_path, 'middle.tar.gz')
    newest = backup(tmp_path, 'new.tar.gz')
    for index, path in enumerate((old, middle, newest)):
        stamp = (NOW-timedelta(days=10-index)).timestamp()
        os.utime(path, (stamp, stamp))
    result = cold._archive_once(root=tmp_path, store=Store(), now=NOW, free_bytes=lambda: 0, target_free=1)
    assert result['status'] == 'ARCHIVED' and result['archived_bytes'] > 0
    assert not old.exists() and middle.exists() and newest.exists()


def test_recent_files_and_sufficient_capacity_remain_local(tmp_path):
    for name in ('a.tar.gz', 'b.tar.gz', 'c.tar.gz'):
        backup(tmp_path, name)
    result = cold._archive_once(root=tmp_path, store=Store(), now=NOW, free_bytes=lambda: 0)
    assert result['status'] == 'NO_OFFLOAD_NEEDED'
    assert len(list(tmp_path.glob('*.tar.gz'))) == 3


def test_restore_cannot_overwrite_or_target_live_portfolio(tmp_path, monkeypatch):
    original = backup(tmp_path); store = Store()
    receipt = cold.offload_file(tmp_path, original, store, now=NOW)
    destination = tmp_path/'existing'; destination.write_bytes(b'keep')
    with pytest.raises(ValueError):
        cold.restore_receipt(receipt, destination, store)
    monkeypatch.setattr(cold, 'LIVE', tmp_path/'live')
    (tmp_path/'live').mkdir()
    with pytest.raises(ValueError):
        cold.restore_receipt(receipt, tmp_path/'live/portfolio', store)
    assert destination.read_bytes() == b'keep'


def test_corrupt_restore_leaves_no_destination_or_temporary_file(tmp_path):
    original = backup(tmp_path); store = Store()
    receipt = cold.offload_file(tmp_path, original, store, now=NOW)
    store.corrupt = True
    with pytest.raises(ValueError):
        cold.restore_receipt(receipt, tmp_path/'restored.tar.gz', store)
    assert not (tmp_path/'restored.tar.gz').exists()
    assert not list(tmp_path.glob('cold-restore-*'))


def test_changed_source_is_not_uploaded_or_retired(tmp_path, monkeypatch):
    original = backup(tmp_path); store = Store()
    validate = cold.validate_backup
    def changing(path):
        validate(path)
        with path.open('ab') as stream:
            stream.write(b'changed')
    monkeypatch.setattr(cold, 'validate_backup', changing)
    with pytest.raises(ValueError):
        cold.offload_file(tmp_path, original, store, now=NOW)
    assert original.exists() and store.uploads == 0


def test_automatic_archival_is_deferred_during_trading_window(tmp_path):
    during = datetime(2026, 10, 22, 14, tzinfo=timezone.utc)
    assert cold.archive_once(state=tmp_path, now=during)['status'] == 'DEFERRED_MARKET_WINDOW'


@pytest.mark.skipif(cold.fcntl is None, reason='Linux flock')
def test_concurrent_automatic_archivers_are_serialized(tmp_path):
    lock_path = tmp_path/'cold-backups.lock'
    with lock_path.open('a') as lock:
        cold.fcntl.flock(lock, cold.fcntl.LOCK_EX | cold.fcntl.LOCK_NB)
        assert cold.archive_once(state=tmp_path, now=NOW)['status'] == 'SKIPPED_BUSY'
