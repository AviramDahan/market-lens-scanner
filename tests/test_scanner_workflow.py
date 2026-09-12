from pathlib import Path

import yaml


WORKFLOW = Path(".github/workflows/market-lens-agent.yml")


def test_scanner_workflow_is_valid_yaml() -> None:
    payload = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert payload["jobs"]["run-agent"]["timeout-minutes"] == 30


def test_scanner_refreshes_queued_workflow_to_latest_portfolio_before_scan() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    refresh_index = text.index("- name: Refresh portfolio baseline")
    scan_index = text.index("- name: Run Market Lens UI agent")
    assert refresh_index < scan_index
    assert "git fetch origin main" in text[refresh_index:scan_index]
    assert "git reset --hard origin/main" in text[refresh_index:scan_index]


def test_buy_notifications_are_sent_only_after_persistence() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    run_index = text.index("- name: Run Market Lens UI agent")
    persist_index = text.index("- name: Commit agent results safely")
    notify_index = text.index("- name: Send persisted BUY notifications")
    assert run_index < persist_index < notify_index
    run_block = text[run_index:persist_index]
    assert "MARKET_LENS_AGENT_NOTIFICATION_OUTBOX" in run_block
    assert "MARKET_LENS_TELEGRAM_BOT_TOKEN" not in run_block
    assert "steps.persist.outputs.persisted == 'true'" in text[notify_index:]


def test_scanner_persists_generated_state_with_manifest_bundle() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    persist_index = text.index("- name: Commit agent results safely")
    block = text[persist_index:]

    assert "python -m agent.generated_state_bundle save" in block
    assert "python -m agent.generated_state_bundle apply" in block
    assert "python -m agent.generated_state_bundle stage" in block
    assert 'git add "$EXCEL_FILE" "$RESULTS_DIR"' not in block
