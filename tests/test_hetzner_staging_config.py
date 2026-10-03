from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_staging_web_is_isolated_and_bounded():
    config = yaml.safe_load((ROOT / "deploy/hetzner/compose.staging.yaml").read_text())
    assert config["name"] == "market-lens-staging"
    assert set(config["services"]) == {"web"}
    web = config["services"]["web"]
    assert web["build"]["dockerfile"] == "deploy/hetzner/Dockerfile.staging"
    ignore = ROOT / "deploy/hetzner/Dockerfile.staging.dockerignore"
    assert ".git" in ignore.read_text().splitlines()
    assert web["ports"] == ["127.0.0.1:18081:8000"]
    assert web["read_only"] is True
    assert web["mem_limit"] == "1g"
    assert float(web["cpus"]) <= 1
    assert any(value.startswith("/app/.yfinance-cache:") for value in web["tmpfs"])
    assert "volumes" not in web
    assert "secrets" not in web
    env = web["environment"]
    assert env["MARKET_LENS_RENDER_SHADOW_MONITOR_ENABLED"] == "false"
    assert env["MARKET_LENS_DASHBOARD_SNAPSHOT_SYNC_ENABLED"] == "false"
    assert env["MARKET_LENS_RESULTS_SYNC_ENABLED"] == "false"
    assert not any(key.startswith("GITHUB_") or "TELEGRAM" in key for key in env)


def test_observation_scan_has_no_live_portfolio_or_notification_access():
    config = yaml.safe_load((ROOT / "deploy/hetzner/compose.observation.yaml").read_text())
    scanner = config["services"]["scanner"]
    assert scanner["read_only"] is True
    assert scanner["restart"] == "no"
    assert "volumes" not in scanner
    assert "ports" not in scanner
    assert "environment" not in scanner
    assert set(scanner["networks"]) == {"staging_web"}
    script = (ROOT / "deploy/hetzner/run_observation_scan.sh").read_text()
    assert "cp /app/agent_tracker/" in script
    assert "MARKET_LENS_EXCEL_PATH=/tmp/observation-tracker.xlsx" in script
    assert "MARKET_LENS_AGENT_NOTIFICATION_OUTBOX=/tmp/observation-outbox.json" in script
    assert "send_buy_notifications" not in script
    assert "git push" not in script
