import json
import os
from contextlib import nullcontext
from pathlib import Path

import pytest
import yaml

from deploy.hetzner import run_runtime as runtime
from deploy.hetzner.staged_scanner import prepare_stage, promote_stage


def fixture_repo(tmp_path):
    repo = tmp_path / "repo"
    tracker_dir = repo / "agent_tracker"
    results_dir = repo / "agent_results"
    tracker_dir.mkdir(parents=True)
    (results_dir / "runtime").mkdir(parents=True)
    tracker = tracker_dir / "market_lens_agent_portfolio_budget_100k.xlsx"
    tracker.write_bytes(b"original-portfolio")
    (results_dir / "summaries").mkdir()
    (results_dir / "summaries/existing.md").write_text("historical result")
    state = tmp_path / "state"
    (state / "outbox").mkdir(parents=True)
    return repo, state, tracker


def test_staged_results_are_invisible_until_promotion(tmp_path):
    repo, state, tracker = fixture_repo(tmp_path)
    stage = prepare_stage(repo, state, runtime.file_hash(tracker))
    (stage.tracker / tracker.name).write_bytes(b"new-portfolio")
    (stage.results / "decisions").mkdir()
    (stage.results / "decisions/run.jsonl").write_text('{"final_action":"WATCH"}\n')
    (stage.results / "runtime/run.json").write_text('{"run_status":"COMPLETE"}')
    (stage.runtime / "outbox/scanner.json").write_text('{"events":[]}')

    assert tracker.read_bytes() == b"original-portfolio"
    assert not (repo / "agent_results/decisions/run.jsonl").exists()
    promote_stage(stage, repo, state)

    assert tracker.read_bytes() == b"new-portfolio"
    assert (repo / "agent_results/decisions/run.jsonl").is_file()
    assert (repo / "agent_results/runtime/run.json").is_file()
    assert (repo / "agent_results/summaries/existing.md").read_text() == "historical result"
    assert (state / "outbox/scanner.json").is_file()


def test_failed_promotion_restores_live_files(tmp_path, monkeypatch):
    repo, state, tracker = fixture_repo(tmp_path)
    stage = prepare_stage(repo, state, runtime.file_hash(tracker))
    (stage.tracker / tracker.name).write_bytes(b"new-portfolio")
    (stage.results / "summaries/existing.md").write_text("new summary")
    real_replace = os.replace
    failed = False

    def fail_tracker_once(source, target):
        nonlocal failed
        if Path(target) == tracker and not failed:
            failed = True
            raise OSError("simulated promotion interruption")
        return real_replace(source, target)

    monkeypatch.setattr("deploy.hetzner.staged_scanner.os.replace", fail_tracker_once)
    with pytest.raises(OSError, match="promotion interruption"):
        promote_stage(stage, repo, state)
    assert tracker.read_bytes() == b"original-portfolio"
    assert (repo / "agent_results/summaries/existing.md").read_text() == "historical result"


def test_stale_scanner_cannot_overwrite_monitor_event(tmp_path, monkeypatch):
    repo, state, tracker = fixture_repo(tmp_path)
    monkeypatch.setattr(runtime, "REPO", repo)
    monkeypatch.setattr(runtime, "STATE", state)
    monkeypatch.setattr(runtime, "TRACKER", tracker)
    monkeypatch.setattr(runtime, "writer_lock", lambda: nullcontext())
    monkeypatch.setattr(runtime, "require_live_preflight", lambda: None)
    monkeypatch.setattr(runtime, "persist", lambda *_: pytest.fail("Stale scan persisted"))
    monkeypatch.setattr(runtime, "deliver", lambda *_: pytest.fail("Stale scan notified"))

    def fake_worker(command, *, timeout, stage=None):
        assert stage is not None
        (stage.tracker / tracker.name).write_bytes(b"stale-scanner-buy")
        record = stage.results / "runtime/market_lens_agent_20261005_140000.json"
        record.write_text(json.dumps({"run_status": "COMPLETE", "result_cards_read": 10}))
        (stage.runtime / "outbox/scanner.json").write_text('{"events":["BUY"]}')
        tracker.write_bytes(b"monitor-tp1-and-stop-to-entry")

    monkeypatch.setattr(runtime, "worker", fake_worker)
    with pytest.raises(runtime.ScannerSnapshotStale, match="Portfolio changed"):
        runtime.execute_staged_scanner()

    assert tracker.read_bytes() == b"monitor-tp1-and-stop-to-entry"
    assert not (state / "outbox/scanner.json").exists()
    assert not list((repo / "agent_results/runtime").glob("market_lens_agent_*.json"))
    retained = list((state / "scanner-staging").iterdir())
    assert len(retained) == 1
    assert not (retained[0] / "agent_results/summaries/existing.md").exists()
    assert (retained[0] / "agent_results/runtime/market_lens_agent_20261005_140000.json").is_file()


def test_scanner_promotes_then_persists_before_notification(tmp_path, monkeypatch):
    repo, state, tracker = fixture_repo(tmp_path)
    monkeypatch.setattr(runtime, "REPO", repo)
    monkeypatch.setattr(runtime, "STATE", state)
    monkeypatch.setattr(runtime, "TRACKER", tracker)
    monkeypatch.setattr(runtime, "writer_lock", lambda: nullcontext())
    monkeypatch.setattr(runtime, "require_live_preflight", lambda: None)
    calls = []

    def fake_worker(command, *, timeout, stage=None):
        if stage is None:
            assert command == ["python", "deploy/hetzner/postprocess.py"]
            calls.append("postprocess")
            return
        (stage.tracker / tracker.name).write_bytes(b"valid-scan")
        (stage.results / "runtime/market_lens_agent_20261005_140000.json").write_text(
            json.dumps({"run_status": "COMPLETE", "result_cards_read": 10})
        )
        (stage.runtime / "outbox/scanner.json").write_text('{"events":[]}')

    def fake_persist(kind):
        assert kind == "scanner"
        assert tracker.read_bytes() == b"valid-scan"
        assert (state / "outbox/scanner.json").is_file()
        calls.append("persist")
        return True

    monkeypatch.setattr(runtime, "worker", fake_worker)
    monkeypatch.setattr(runtime, "persist", fake_persist)
    monkeypatch.setattr(runtime, "deliver", lambda kind: calls.append("deliver"))
    runtime.execute_staged_scanner()

    assert calls == ["postprocess", "persist", "deliver"]
    assert not list((state / "scanner-staging").iterdir())


def test_separate_systemd_units_keep_monitor_minute_timer():
    root = Path(__file__).resolve().parents[1]
    deploy = root / "deploy/hetzner"
    monitor = (deploy / "market-lens.service").read_text()
    scanner = (deploy / "market-lens-scanner.service").read_text()
    scanner_timer = (deploy / "market-lens-scanner.timer").read_text()
    assert "schedule.py --kind monitor" in monitor
    assert "schedule.py --kind scanner" in scanner
    assert "SupplementaryGroups=docker" in monitor and "SupplementaryGroups=docker" in scanner
    assert "NoNewPrivileges=true" in monitor and "NoNewPrivileges=true" in scanner
    assert "OnCalendar=*-*-* *:*:00" in scanner_timer
    assert "Unit=market-lens-scanner.service" in scanner_timer
    override = yaml.safe_load((deploy / "compose.stage.yaml").read_text())
    mounts = override["services"]["worker"]["volumes"]
    assert len(mounts) == 3
    assert any("/app/agent_tracker:rw" in item for item in mounts)
    assert any("/app/agent_results:rw" in item for item in mounts)
    assert any("/app/runtime:rw" in item for item in mounts)
