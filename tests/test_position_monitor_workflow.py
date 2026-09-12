from pathlib import Path

import yaml


WORKFLOW = Path(".github/workflows/market-lens-position-monitor.yml")


def test_monitor_workflow_is_valid_yaml_and_has_scheduled_fallback() -> None:
    payload = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = payload.get("on") or payload.get(True)

    assert triggers["schedule"] == [{"cron": "*/15 13-21 * * 1-5"}]
    assert "workflow_dispatch" in triggers


def test_monitor_notifications_are_sent_only_after_persistence() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    run_index = text.index("- name: Run position monitor")
    persist_index = text.index("- name: Commit monitor results safely")
    notify_index = text.index("- name: Send persisted position notifications")
    assert run_index < persist_index < notify_index
    run_block = text[run_index:persist_index]
    assert "MARKET_LENS_MONITOR_NOTIFICATION_OUTBOX" in run_block
    assert "MARKET_LENS_TELEGRAM_BOT_TOKEN" not in run_block
    assert "steps.persist.outputs.persisted == 'true'" in text[notify_index:]


def test_monitor_conflict_path_recalculates_instead_of_overlaying_stale_portfolio() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "Portfolio changed during monitor run; recalculating once from latest main." in text
    assert "Push rejected because main changed. Recalculating once on top of latest main." in text
    assert text.count("run_monitor_pipeline") >= 3


def test_monitor_persists_generated_state_with_manifest_bundle() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    persist_index = text.index("- name: Commit monitor results safely")
    block = text[persist_index:]

    assert "python -m agent.generated_state_bundle save" in block
    assert "python -m agent.generated_state_bundle apply" in block
    assert "python -m agent.generated_state_bundle stage" in block
    assert 'git add "$EXCEL_FILE" "$RESULTS_DIR"' not in block


def test_monitor_compacts_large_workbook_before_github_warning_size() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert 'MARKET_LENS_WORKBOOK_WATCHLIST_MAX_ROWS: "20000"' in text
    assert 'MARKET_LENS_WORKBOOK_REWRITE_BYTES: "50000000"' in text
    assert 'MARKET_LENS_WORKBOOK_WATCHLIST_MAX_ROWS: "80000"' not in text
    assert 'MARKET_LENS_WORKBOOK_REWRITE_BYTES: "75000000"' not in text
