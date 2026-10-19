import json

import pandas as pd
import pytest

from app.agent_risk import correlation_check, current_factor_exposure
from app.agent_risk import AgentRiskConfig, AgentRunRiskContext, MarketRegime, adjust_candidate_position_size
from app.risk_evidence import correlation_passed


def history(n=95):
    # Nonconstant returns, so correlation is defined.
    return pd.DataFrame({"Close": [100 + i + (i % 3) for i in range(n)]},
                        index=pd.date_range("2026-01-01", periods=n, tz="UTC"))


def test_no_other_positions_requires_no_provider_call(monkeypatch):
    monkeypatch.setattr("app.agent_risk.fetch_daily_frame", lambda *a, **k: pytest.fail("Unneeded fetch"))
    result = correlation_check(ticker="AAA", candidate_daily=None,
                               open_positions={"AAA": {}}, analysis_period="6mo")
    assert result["status"] == "NOT_REQUIRED"
    assert result["positions_expected"] == 0


@pytest.mark.parametrize("peer", [history(25), pd.DataFrame({"Close": [100.] * 95}, index=history().index)])
def test_incomplete_or_undefined_comparison_is_unknown_not_pass(monkeypatch, peer):
    monkeypatch.setattr("app.agent_risk.fetch_daily_frame", lambda *a, **k: peer)
    result = correlation_check(ticker="AAA", candidate_daily=history(),
                               open_positions={"BBB": {}}, analysis_period="6mo")
    assert result["status"] == "UNKNOWN"
    assert result["positions_checked"] == 0
    assert result["missing_tickers"] == ["BBB"]
    assert result["warnings"]
    assert correlation_passed({"correlation_status": result["status"], **result}) is None


def test_partial_coverage_keeps_known_high_correlation_warning(monkeypatch):
    monkeypatch.setattr("app.agent_risk.fetch_daily_frame", lambda symbol, **k: history() if symbol == "BBB" else history(25))
    result = correlation_check(ticker="AAA", candidate_daily=history(),
                               open_positions={"BBB": {}, "CCC": {}}, analysis_period="6mo", threshold=.85)
    assert result["status"] == "PARTIAL"
    assert result["correlation_warning"] is True
    assert result["positions_checked"] == 1
    assert result["positions_expected"] == 2


def test_correlation_uses_explicit_threshold(monkeypatch):
    monkeypatch.setattr("app.agent_risk.fetch_daily_frame", lambda *a, **k: history())
    result = correlation_check(ticker="AAA", candidate_daily=history(), open_positions={"BBB": {}},
                               analysis_period="6mo", threshold=1.01)
    assert result["status"] == "PASS"


@pytest.mark.parametrize("encoded", [False, True])
def test_factor_exposure_preserves_stored_small_cap_and_theme_tags(encoded):
    metadata = {"factor_tags": ["Energy", "Small Cap / Risk-On", "Custom theme"],
                "sector": "Energy", "market_cap_bucket": "Small Cap"}
    positions = {"BBB": {"exposure_ils": 5000, "decision_json": json.dumps(metadata) if encoded else metadata}}
    assert current_factor_exposure("AAA", positions, {}) == {
        "Energy": 5000, "Small Cap / Risk-On": 5000, "Custom theme": 5000}


def test_legacy_position_factor_fallback_and_self_exclusion():
    assert current_factor_exposure("AAA", {"AAA": {"exposure_ils": 999}, "BBB": {"exposure_ils": 2000}},
                                   {"BBB": "Energy"}) == {"Energy": 2000}


def test_unknown_frozen_sector_cannot_hide_known_defensive_exposure():
    positions = {"BBB": {"exposure_ils": 19500, "decision_json": {"sector": "Unknown", "factor_tags": []}}}
    sector_map = {"BBB": "Healthcare", "AAA": "Technology"}
    assert current_factor_exposure("AAA", positions, sector_map)["Defensive"] == 19500
    ctx = AgentRunRiskContext(AgentRiskConfig(100000, 40000, 10000),
                             MarketRegime("BULL", .82, 40000, 2, .45, "test"), {})
    sizing = adjust_candidate_position_size(ticker="AAA", sector="Technology", factor_tags=["Defensive"],
        initial_action="BUY_SIMULATED", quantity=100, cash_out=1000, risk_amount=100,
        cash_available=100000, portfolio_exposure_before=19500, open_positions=positions,
        sector_map=sector_map, run_context=ctx)
    assert sizing["quantity"] == 50
