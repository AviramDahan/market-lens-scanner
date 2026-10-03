from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_staging_web_is_isolated_and_bounded():
    config = yaml.safe_load((ROOT / "deploy/hetzner/compose.staging.yaml").read_text())
    assert config["name"] == "market-lens-staging"
    assert set(config["services"]) == {"web"}
    web = config["services"]["web"]
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
