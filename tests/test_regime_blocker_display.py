from copy import deepcopy

from app.agent_dashboard import build_decision_diagnostics, watch_entry_checklist


def candidate(**changes):
    decision = dict(market_regime="BEAR", setup_score=.48, minimum_setup_score_required=1.,
                    net_rr=3.33, weighted_net_rr=3.33, minimum_net_rr_required=999.,
                    net_rr_1=2.96, entry_confirmation_passed=True, sector_regime="STRONG")
    decision.update(changes)
    return dict(ticker="GILD", action="WATCH", setup_type="Fib", score=.48,
                reason="Technical setup. Market regime BEAR; sector STRONG; net R/R 3.33.",
                decision_json=decision)


def test_bear_is_prominent_without_prose_match_and_sentinels_are_not_score_failures():
    setup = candidate()
    original = deepcopy(setup)
    result = build_decision_diagnostics([setup])
    assert result["why_no_buys"][0]["label"] == "BEAR blocks new buys"
    assert result["blockers"] == {"BEAR blocks new buys": 1}
    assert result["drilldowns"]["MARKET_BLOCKED"][0]["ticker"] == "GILD"
    assert setup == original
    rows = {r["label"]: r for r in watch_entry_checklist(setup["decision_json"])}
    assert rows["Market entry policy"]["status"] == "fail"
    assert rows["Setup score"]["status"] == rows["Net R/R"]["status"] == "info"
    assert "999" not in rows["Net R/R"]["detail"]


def test_bear_stays_first_even_when_other_rejections_are_more_frequent():
    setups = [candidate()]
    setups += [dict(candidate(market_regime="BULL", minimum_setup_score_required=.45,
                              minimum_net_rr_required=2., entry_confirmation_passed=False),
                    ticker=f"TEST{i}") for i in range(5)]
    result = build_decision_diagnostics(setups)
    assert result["why_no_buys"][0]["label"] == "BEAR blocks new buys"
    assert result["blockers"]["Entry confirmation missing"] == 5


def test_descriptive_rr_and_successful_confirmation_are_not_blockers():
    setup = candidate(market_regime="BULL", minimum_setup_score_required=.45,
                      minimum_net_rr_required=2.)
    setup["reason"] = "Entry confirmation passed; weighted net R/R 3.33."
    result = build_decision_diagnostics([setup])
    assert "R/R below gate" not in result["blockers"]
    assert "Entry confirmation missing" not in result["blockers"]


def test_real_quality_failures_remain_visible_and_held_positions_are_excluded():
    setup = candidate(market_regime="NEUTRAL", minimum_setup_score_required=.55,
                      minimum_net_rr_required=2.5, weighted_net_rr=1.2,
                      entry_confirmation_passed=False)
    held = dict(candidate(), ticker="HELD", action="HOLD")
    result = build_decision_diagnostics([setup, held])
    assert result["blockers"] == {
        "R/R below gate": 1, "Setup score below gate": 1, "Entry confirmation missing": 1,
    }
    assert "BEAR blocks new buys" not in result["blockers"]


def test_no_trade_does_not_masquerade_as_missing_confirmation():
    setup = candidate(entry_confirmation_passed=False)
    setup.update(setup_type="No Trade", reason="No Trade result; entry confirmation unavailable; net R/R 0.")
    result = build_decision_diagnostics([setup])
    assert result["blockers"] == {"BEAR blocks new buys": 1, "No Trade": 1}


def test_benchmark_evidence_is_present_and_legacy_timestamps_are_unknown():
    rows = watch_entry_checklist({"market_regime_indicators": {"SPY": {"price": 757.315}}})
    row = next(r for r in rows if r["label"] == "Regime input: SPY")
    assert "last bar session: Not recorded" in row["detail"]
    assert "757.315" in row["detail"]


def test_existing_snapshot_diagnostics_are_upgraded_without_changing_positions(monkeypatch):
    from app.main import enrich_agent_dashboard_snapshot
    setup = candidate()
    snapshot = {"status": "ok", "latest_setups": [setup], "open_positions": [],
                "summary": {}, "recent_trades": [], "recent_runs": [],
                "decision_diagnostics": {"why_no_buys": [{"label": "R/R below gate"}]}}
    result = enrich_agent_dashboard_snapshot(snapshot)
    assert result["decision_diagnostics"]["blocker_schema_version"] == 2
    assert result["decision_diagnostics"]["why_no_buys"][0]["label"] == "BEAR blocks new buys"
    assert result["latest_setups"] == [setup]
    assert result["open_positions"] == []
