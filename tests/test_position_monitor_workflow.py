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
