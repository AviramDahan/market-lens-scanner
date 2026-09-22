import gzip
import json
from datetime import date

from agent.cleanup_agent_results import prune_directory
from app.performance_summary import collect_records, build_period_summary


def test_retention_archives_decisions_and_weekly_summary_reads_them(tmp_path):
    folder = tmp_path / "decisions"
    folder.mkdir()
    for day in (14, 15, 16):
        record = {"timestamp": f"2026-09-{day}T15:00:00", "ticker": "TEST", "final_action": "WATCH"}
        (folder / f"market_lens_agent_202609{day}_150000.jsonl").write_text(json.dumps(record))
    result = prune_directory(folder, max_files=1, dry_run=False, project_root=tmp_path,
                             suffixes={".jsonl"}, archive=True)
    assert result["archived"] == 2
    assert result["permanently_deleted"] == 0
    records, files = collect_records(folder, period="weekly", target_date=date(2026, 9, 16))
    assert len(records) == len(files) == 3
    assert len(list(folder.glob("*.jsonl"))) == 1
    assert len(list((folder / "archive").glob("*.gz"))) == 2


def test_daily_weekly_summaries_never_evicted_by_filename(tmp_path):
    for name in ("daily_summary_2026-09-21.json", "weekly_summary_2026-W39.md",
                 "market_lens_agent_20260921_150000.md"):
        (tmp_path / name).write_text("original")
    prune_directory(tmp_path, max_files=0, dry_run=False, project_root=tmp_path,
                    suffixes={".json", ".md"}, archive=True)
    assert (tmp_path / "daily_summary_2026-09-21.json").read_text() == "original"
    assert (tmp_path / "weekly_summary_2026-W39.md").exists()
    archived = list((tmp_path / "archive").glob("*.gz"))
    assert len(archived) == 1
    assert gzip.decompress(archived[0].read_bytes()) == b"original"


def test_active_backfill_overrides_archive_without_duplicate_counts(tmp_path):
    path = tmp_path / "market_lens_agent_20260921_150000.jsonl"
    old = {"timestamp": "2026-09-21T15:00:00", "ticker": "TEST", "final_action": "WATCH"}
    path.write_text(json.dumps(old))
    prune_directory(tmp_path, max_files=0, dry_run=False, project_root=tmp_path,
                    suffixes={".jsonl"}, archive=True)
    new = {**old, "outcome_after_1d": 1.25}
    path.write_text(json.dumps(new))
    records, files = collect_records(tmp_path, period="daily", target_date=date(2026, 9, 21))
    assert records == [new]
    assert files == [path]


def test_archive_dry_run_never_moves_files(tmp_path):
    path = tmp_path / "market_lens_agent_20260921_150000.jsonl"
    path.write_text("{}")
    prune_directory(tmp_path, max_files=0, dry_run=True, project_root=tmp_path,
                    suffixes={".jsonl"}, archive=True)
    assert path.exists()
    assert not (tmp_path / "archive").exists()


def test_summary_exposes_cohorts_and_does_not_claim_complete_history(tmp_path):
    path = tmp_path / "market_lens_agent_20260921_150000.jsonl"
    records = [{"timestamp": "2026-09-21T15:00:00", "ticker": "TEST", "final_action": "WATCH",
                "active_setup_selection_policy": policy} for policy in ("FIRST_MATCH_LEGACY", "QUALIFIED_PROFESSIONAL_SCORE_V1")]
    path.write_text("\n".join(json.dumps(record) for record in records))
    summary = build_period_summary(period="daily", target_date=date(2026, 9, 21), decision_dir=tmp_path,
                                   portfolio={}, current_decision_path=path, run_id="test", completed_trades=[])
    assert summary["decision_cohorts"] == {"FIRST_MATCH_LEGACY": 1, "QUALIFIED_PROFESSIONAL_SCORE_V1": 1}
    assert summary["history_coverage_status"] == "AVAILABLE_FILES_ONLY_NOT_INDEPENDENTLY_VERIFIED"
    assert summary["BUY_SIMULATED_count"] == 0


def test_archive_revision_selection_uses_index_not_checkout_mtime(tmp_path):
    path = tmp_path / "market_lens_agent_20260921_150000.jsonl"
    for value in (1, 2):
        path.write_text(json.dumps({"timestamp": "2026-09-21T15:00:00", "revision": value}))
        prune_directory(tmp_path, max_files=0, dry_run=False, project_root=tmp_path,
                        suffixes={".jsonl"}, archive=True)
    records, files = collect_records(tmp_path, period="daily", target_date=date(2026, 9, 21))
    assert len(files) == 1
    assert records[0]["revision"] == 2
    assert len(list((tmp_path / "archive").glob("*.gz"))) == 2


def test_recovery_reads_latest_committed_version_without_changing_git(tmp_path):
    import subprocess
    from agent.recover_measurement_history import recover

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.invalid")
    path = tmp_path / "agent_results" / "decisions" / "market_lens_agent_20260921_150000.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"timestamp":"2026-09-21T15:00:00","revision":1}')
    git("add", ".")
    git("commit", "-qm", "record")
    path.write_text('{"timestamp":"2026-09-21T15:00:00","revision":2}')
    git("add", ".")
    git("commit", "-qm", "backfill")
    before = git("rev-parse", "HEAD")
    output = tmp_path / "recovered"
    manifest = recover(tmp_path, output, "2000-01-01")
    assert len(manifest["files"]) == 1
    records, _ = collect_records(output / "decisions", period="daily", target_date=date(2026, 9, 21))
    assert records[0]["revision"] == 2
    assert git("rev-parse", "HEAD") == before
