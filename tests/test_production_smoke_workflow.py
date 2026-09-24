from pathlib import Path

import yaml


WORKFLOW = Path(".github/workflows/market-lens-production-smoke.yml")


def test_production_smoke_workflow_is_bounded_and_read_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    payload = yaml.safe_load(text)
    job = payload["jobs"]["production-smoke"]

    assert job["timeout-minutes"] == 8
    assert "Market Lens Source QA" in text
    assert "workflow_run" in text
    assert "python agent/production_smoke.py" in text
    assert "MARKET_LENS_SMOKE_EXPECTED_REVISION" in text
    assert "MARKET_LENS_EMAIL" not in text
    assert "MARKET_LENS_PASSWORD" not in text
    assert "TELEGRAM" not in text
    assert "market-lens-agent.yml" not in text
    assert "market-lens-position-monitor.yml" not in text
    assert payload["permissions"] == {"contents": "read"}


def test_source_qa_validates_new_smoke_workflow() -> None:
    source_qa = Path(".github/workflows/market-lens-qa.yml").read_text(encoding="utf-8")

    assert 'Path(".github/workflows").glob("*.yml")' in source_qa
    assert 'python -m pytest -q' in source_qa
