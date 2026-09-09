from types import SimpleNamespace

import pytest

from app import strategy


@pytest.mark.parametrize("ceiling", [0, 20_000, 40_000, 60_000])
def test_preliminary_and_final_stages_receive_same_ceiling(monkeypatch, ceiling):
    context = SimpleNamespace(sector_health={"Technology": {}},
                              market_regime=SimpleNamespace(max_total_exposure=ceiling))
    observed = []
    monkeypatch.setattr(strategy, "build_agent_run_context", lambda **kwargs: context)
    monkeypatch.setattr(strategy, "base_universe", lambda: {})

    def preliminary(candidate, **kwargs):
        observed.append(kwargs["max_total_exposure"])
        return strategy.StrategyDecision("WATCH", "fixture")

    def final(**kwargs):
        observed.append(kwargs["run_context"].market_regime.max_total_exposure)
        return {"final_action": "WATCH", "reason": "fixture"}

    monkeypatch.setattr(strategy, "decide_strategy_candidate", preliminary)
    monkeypatch.setattr(strategy, "evaluate_agent_candidate", final)
    monkeypatch.setattr(strategy, "enrich_result_with_strategy", lambda result, *args: result)
    candidate = strategy.StrategyCandidate("TEST", "Fib", .6, 100, 99, 101, 95, 110, 120, 3)
    strategy.apply_strategy_decisions([candidate], analysis_period="6mo", min_rr=2,
                                      max_total_exposure=40_000)
    assert observed == [ceiling, ceiling]


@pytest.mark.parametrize("ceiling", [0, 20_000, 40_000, 60_000])
def test_agent_workbook_path_uses_final_risk_ceiling(monkeypatch, tmp_path, ceiling):
    from agent import market_lens_ui_agent as agent

    context = SimpleNamespace(sector_health={"Technology": {}},
                              config=SimpleNamespace(stop_cooldown_days=3),
                              market_regime=SimpleNamespace(max_total_exposure=ceiling))
    settings = SimpleNamespace(excel_path=tmp_path / "never-written.xlsx", min_rr=2,
                               analysis_period="6mo")
    candidate = strategy.StrategyCandidate("TEST", "Fib", .6, 100, 99, 101, 95, 110, 120, 3)
    monkeypatch.setattr(agent, "load_workbook", lambda _: object())
    monkeypatch.setattr(agent, "ensure_agent_columns", lambda _: None)
    monkeypatch.setattr(agent, "read_settings", lambda _: {"max_total_exposure_pct": .4})
    monkeypatch.setattr(agent, "build_agent_run_context", lambda **_: context)
    monkeypatch.setattr(agent, "base_universe", lambda: {})
    monkeypatch.setattr(agent, "read_recent_stop_events", lambda *_: {})
    monkeypatch.setattr(agent, "count_neutral_pilot_buys_today", lambda _: 0)
    monkeypatch.setattr(agent, "read_open_positions", lambda _: {})
    monkeypatch.setattr(agent, "compute_cash", lambda *_: 100_000)
    monkeypatch.setattr(agent, "rank_results_for_allocation", lambda rows, _: rows)
    monkeypatch.setattr(agent, "calculate_setup_score_percentiles", lambda _: {})
    observed = []

    class EndOfSizingCheck(Exception):
        pass

    def preliminary(result, **kwargs):
        observed.append(kwargs["max_total_exposure"])
        return agent.Decision("WATCH", "fixture")

    def final(**kwargs):
        observed.append(kwargs["run_context"].market_regime.max_total_exposure)
        # End before any Excel persistence or notifications, after both real call sites.
        raise EndOfSizingCheck

    monkeypatch.setattr(agent, "decide", preliminary)
    monkeypatch.setattr(agent, "evaluate_agent_candidate", final)
    with pytest.raises(EndOfSizingCheck):
        agent.update_workbook(settings=settings, run_id="test", results=[candidate],
                              screenshot_path=tmp_path / "unused.png",
                              summary_path=tmp_path / "unused.md",
                              decision_path=tmp_path / "unused.jsonl", errors=[])
    assert observed == [ceiling, ceiling]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("ceiling,expected_quantity", [(60_000, 100), (40_000, 0), (20_000, 0), (0, 0)])
def test_real_preliminary_sizing_above_legacy_cap(monkeypatch, ceiling, expected_quantity):
    context = SimpleNamespace(sector_health={"Technology": {}},
                              market_regime=SimpleNamespace(max_total_exposure=ceiling))
    monkeypatch.setattr(strategy, "build_agent_run_context", lambda **_: context)
    monkeypatch.setattr(strategy, "base_universe", lambda: {})
    captured = []

    def final(**kwargs):
        captured.append(kwargs["quantity"])
        return {"final_action": "WATCH", "reason": "Final gate still rejects the candidate"}

    monkeypatch.setattr(strategy, "evaluate_agent_candidate", final)
    candidate = strategy.StrategyCandidate("TEST", "Fib", .6, 100, 99, 101, 95, 110, 120, 3)
    result = strategy.apply_strategy_decisions([candidate], analysis_period="6mo", min_rr=2,
                                               exposure=45_000, cash=55_000)
    assert captured == [expected_quantity]
    assert result[0].strategy_action == "WATCH"
