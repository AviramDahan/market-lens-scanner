"""Persist delivery keys only; never copy a stale portfolio over current main."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

RECEIPTS = Path('agent_results/telegram_notifications.jsonl')


def merge_receipts(*sources: str) -> str:
    records = {}
    for source in sources:
        for line in source.splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item.get('key'), str) or not isinstance(item.get('sent_at'), str):
                raise ValueError('Malformed notification receipt')
            key = item['key']
            if key not in records or item['sent_at'] > records[key]['sent_at']:
                records[key] = {'key': key, 'sent_at': item['sent_at']}
    return ''.join(json.dumps(item, separators=(',', ':')) + '\n'
                   for item in sorted(records.values(), key=lambda x: (x['sent_at'], x['key'])))


def persist_receipts(root: Path) -> None:
    source = root / RECEIPTS
    if not source.exists():
        return
    pending = source.read_text()
    if not pending.strip():
        return
    def git(*args, cwd=root):
        return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True)
    with tempfile.TemporaryDirectory(prefix='market-lens-receipts-') as directory:
        work = Path(directory) / 'checkout'
        git('fetch', 'origin', 'main')
        git('worktree', 'add', '--detach', str(work), 'origin/main')
        try:
            for attempt in range(3):
                if attempt:
                    git('fetch', 'origin', 'main')
                    # Only the disposable receipt checkout is reset.
                    git('reset', '--hard', 'origin/main', cwd=work)
                target = work / RECEIPTS
                previous = target.read_text() if target.exists() else ''
                merged = merge_receipts(previous, pending)
                if merged == previous:
                    return
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(merged)
                git('add', '--', str(RECEIPTS), cwd=work)
                git('commit', '-m', 'Persist Telegram delivery receipts [skip render]', cwd=work)
                try:
                    git('push', 'origin', 'HEAD:main', cwd=work)
                    return
                except subprocess.CalledProcessError:
                    if attempt == 2:
                        raise RuntimeError('Could not persist Telegram receipts after three attempts') from None
        finally:
            git('worktree', 'remove', '--force', str(work))


if __name__ == '__main__':
    persist_receipts(Path(__file__).resolve().parents[1])
