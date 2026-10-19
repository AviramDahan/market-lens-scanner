from dataclasses import replace
import json
from datetime import date

from app.agent_risk import AgentRiskConfig
from app.policy_evidence import decision_policy_evidence, policy_manifest, write_policy_manifest
from app.performance_summary import policy_setup_score_performance
from app.agent_dashboard import compute_full_trade_performance


def config():
    return AgentRiskConfig(100000, 40000, 10000)


def test_policy_is_deterministic_and_excludes_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "never-include-this")
    monkeypatch.setenv("MARKET_LENS_PASSWORD", "not-a-policy-field")
    cfg = config()
    first = policy_manifest(cfg)
    assert first == policy_manifest(cfg)
    assert "never-include-this" not in json.dumps(first)
    assert "not-a-policy-field" not in json.dumps(first)
    path = write_policy_manifest(tmp_path, cfg)
    before = path.read_bytes()
    assert write_policy_manifest(tmp_path, cfg) == path
    assert path.read_bytes() == before
    assert decision_policy_evidence(cfg)["decision_policy_fingerprint"] == first["fingerprint"]


def test_config_change_starts_new_measurement_cohort_without_mutation():
    cfg = config()
    assert policy_manifest(cfg)["fingerprint"] != policy_manifest(replace(cfg, minimum_primary_net_rr=1.2))["fingerprint"]
    assert cfg.minimum_primary_net_rr == .8


def test_effective_api_workbook_gates_and_currency_change_policy_identity():
    cfg = config()
    inputs = dict(base_min_rr=2, preliminary_max_risk=1000, currency_rate=1)
    baseline = policy_manifest(cfg, **inputs)
    assert baseline["effective_entry_inputs_complete"] is True
    for key, value in [("base_min_rr", 4), ("preliminary_max_risk", 100), ("currency_rate", 3.7)]:
        assert policy_manifest(cfg, **{**inputs, key: value})["fingerprint"] != baseline["fingerprint"]
    assert policy_manifest(cfg)["effective_entry_inputs_complete"] is False


def test_calibration_never_pools_different_policies_or_missing_history():
    trades = [dict(decision_policy_fingerprint=fp, setup_type="VWAP", setup_score_bucket="0.50-0.59",
                   score_definition_version="v2", pnl_ils=pnl) for fp, pnl in [("a", 10), ("b", -10)]]
    trades.append(dict(setup_type="VWAP", pnl_ils=2))
    before = json.dumps(trades, sort_keys=True)
    groups = policy_setup_score_performance(trades)
    assert len(groups) == 3
    assert sorted(row["realized_pnl"] for row in groups.values()) == [-10, 2, 10]
    assert before == json.dumps(trades, sort_keys=True)


def test_closed_trade_keeps_entry_policy_not_later_monitor_or_scan_policy():
    trades = [dict(ticker="QA", action="BUY_SIMULATED", quantity=10, timestamp="2026-10-01T14:00:00Z",
                   entry_price_usd=100, cash_out_ils=1000, stop_loss=95, trade_id="isolated",
                   decision_json=dict(decision_policy_fingerprint="entry", decision_policy_version="v1", score_definition_version="score1")),
              dict(ticker="QA", action="TAKE_PROFIT", quantity=10, timestamp="2026-10-02T14:00:00Z",
                   cash_in_ils=1100, trade_id="isolated", decision_json=dict(decision_policy_fingerprint="later"))]
    before = json.dumps(trades, sort_keys=True)
    trade = compute_full_trade_performance(trades)["closed"][0]
    assert trade["decision_policy_fingerprint"] == "entry"
    assert trade["score_definition_version"] == "score1"
    assert trade["pnl_ils"] == 100
    assert before == json.dumps(trades, sort_keys=True)


def test_invalid_monitor_metadata_does_not_break_scanner_policy_evidence(monkeypatch):
    monkeypatch.setenv("MARKET_LENS_MONITOR_EXTENDED_HOURS_TARGET_CONFIRMATION_BARS", "invalid")
    assert policy_manifest(config())["extended_hours_target_confirmation_bars"] is None
    assert decision_policy_evidence(config())["decision_policy_fingerprint"]


def test_new_calibration_never_treats_exit_events_as_complete_trades(tmp_path):
    from app.performance_summary import build_period_summary
    path = tmp_path / "market_lens_agent_20261019_140000.jsonl"
    path.write_text('{"timestamp":"2026-10-19T14:00:00","ticker":"QA","final_action":"WATCH"}\n')
    summary = build_period_summary(period="daily", target_date=date(2026, 10, 19), decision_dir=tmp_path,
                                   portfolio={}, current_decision_path=path, run_id="isolated", completed_trades=None)
    assert summary["performance_by_policy_setup_score"] is None
    assert summary["policy_calibration_status"] == "MISSING_COMPLETED_TRADE_LIFECYCLES"
