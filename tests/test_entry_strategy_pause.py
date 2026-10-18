"""A manual setup pause blocks new entries, never existing TP/SL execution."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.candidate_selection import select_qualified_candidate
from app.strategy import StrategyCandidate, StrategyDecision

FIB = "Fib 61.8 Confluence Buy Zone"
BREAKOUT = "Breakout + Retest"


def candidate(name=FIB):
    return StrategyCandidate('TEST', name, .7, 100, 99, 101, 95, 110, 120, 3)


def alternative(name=BREAKOUT):
    return dict(setup_type=name, professional_adjusted_score=.6,
                buy_zone_low=99, buy_zone_high=101, stop_loss=95,
                target_1=110, target_2=120, risk_reward=3)


def evaluate(c, action='BUY_SIMULATED'):
    return (StrategyDecision(action, 'before pause', quantity=10, cash_out_ils=1000, risk_ils=50),
            dict(final_action=action, reason='before pause', position_size=10, risk_amount=50,
                 adjusted_position_size=10, adjusted_cash_out=1000, adjusted_risk_amount=50,
                 portfolio_exposure_before=5000, portfolio_exposure_after=6000,
                 portfolio_heat_before=100, portfolio_heat_after=150,
                 entry_gate_blockers=[], setup_alert_evidence=dict(eligible=True),
                 setup_candidates=deepcopy(c.setup_candidates), net_rr_1=2, net_rr=3))


@pytest.fixture(autouse=True)
def clean_policy(monkeypatch):
    # Exercise the independent manual-pause switch against the legacy detector.
    monkeypatch.setenv('MARKET_LENS_FIBONACCI_ENTRIES_ENABLED', 'true')
    monkeypatch.delenv('MARKET_LENS_DISABLED_ENTRY_SETUPS', raising=False)


def test_unconfigured_policy_preserves_buy():
    _, decision, evidence = select_qualified_candidate(candidate(), evaluate)
    assert decision.action == evidence['final_action'] == 'BUY_SIMULATED'
    assert 'entry_strategy_policy' not in evidence


@pytest.mark.parametrize('action', ['BUY_SIMULATED', 'WATCH', 'WATCH_READY', 'SKIP'])
def test_paused_setup_cannot_enter_or_alert(monkeypatch, action):
    monkeypatch.setenv('MARKET_LENS_DISABLED_ENTRY_SETUPS', FIB)
    _, decision, evidence = select_qualified_candidate(candidate(), lambda c: evaluate(c, action))
    assert decision.action == evidence['final_action'] == 'SKIP'
    assert decision.quantity == evidence['position_size'] == 0
    assert decision.cash_out_ils == evidence['adjusted_cash_out'] == 0
    assert decision.risk_ils == evidence['risk_amount'] == 0
    assert evidence['portfolio_exposure_after'] == evidence['portfolio_exposure_before']
    assert evidence['portfolio_heat_after'] == evidence['portfolio_heat_before']
    assert evidence['entry_strategy_policy']['pre_pause_final_action'] == action
    assert evidence['entry_eligibility_status'] == 'ENTRY_GATES_BLOCKED'
    assert evidence['qualified_setup_alert'] is None
    assert 'paused' in evidence['entry_gate_blockers'][-1]


def test_unpaused_qualified_alternative_still_buys(monkeypatch):
    monkeypatch.setenv('MARKET_LENS_DISABLED_ENTRY_SETUPS', FIB)
    c = replace(candidate(), setup_candidates=[alternative()])
    selected, decision, evidence = select_qualified_candidate(c, evaluate)
    assert selected.setup_type == BREAKOUT
    assert decision.action == evidence['final_action'] == 'BUY_SIMULATED'
    assert next(row for row in evidence['selection_candidates'] if row['setup_type'] == FIB)['entry_paused']


def test_existing_fib_position_is_not_closed_or_resized(monkeypatch):
    monkeypatch.setenv('MARKET_LENS_DISABLED_ENTRY_SETUPS', FIB)
    selected, decision, evidence = select_qualified_candidate(candidate(), lambda c: evaluate(c, 'HOLD'), existing=True)
    assert selected.setup_type == FIB
    assert decision.action == evidence['final_action'] == 'HOLD'
    assert decision.quantity == evidence['position_size'] == 10
    assert evidence['qualified_setup_alert'] is None
    assert 'strategy_version' not in evidence


def test_callback_evidence_and_detector_payloads_are_preserved(monkeypatch):
    monkeypatch.setenv('MARKET_LENS_DISABLED_ENTRY_SETUPS', FIB)
    c = replace(candidate(), setup_candidates=[alternative(FIB)])
    original = deepcopy(c)
    decision, evidence = evaluate(c)
    original_evidence, original_decision = deepcopy(evidence), deepcopy(decision)
    _, blocked, saved = select_qualified_candidate(c, lambda _: (decision, evidence))
    assert c == original and decision == original_decision and evidence == original_evidence
    assert saved['setup_candidates'] == original.setup_candidates
    assert blocked is not decision


def test_pause_is_reversible_without_changing_gates(monkeypatch):
    monkeypatch.setenv('MARKET_LENS_DISABLED_ENTRY_SETUPS', FIB)
    assert select_qualified_candidate(candidate(), evaluate)[2]['final_action'] == 'SKIP'
    monkeypatch.setenv('MARKET_LENS_DISABLED_ENTRY_SETUPS', '')
    assert select_qualified_candidate(candidate(), evaluate)[2]['final_action'] == 'BUY_SIMULATED'


def test_setup_names_are_case_insensitive_and_trimmed(monkeypatch):
    monkeypatch.setenv('MARKET_LENS_DISABLED_ENTRY_SETUPS', f'  {FIB.lower()} , {BREAKOUT}  ')
    assert select_qualified_candidate(candidate(), evaluate)[2]['final_action'] == 'SKIP'


@pytest.mark.parametrize('name', [BREAKOUT, 'Liquidity Trap Buy Zone', 'VWAP Reclaim Setup',
                                  'Swing Low + Volume Support Buy Zone'])
def test_other_setups_do_not_inherit_the_fib_pause(monkeypatch, name):
    monkeypatch.setenv('MARKET_LENS_DISABLED_ENTRY_SETUPS', FIB)
    assert select_qualified_candidate(candidate(name), evaluate)[2]['final_action'] == 'BUY_SIMULATED'


def test_agent_notification_builder_has_no_paused_entry_message(monkeypatch):
    from agent.market_lens_ui_agent import build_buy_notification_records

    monkeypatch.setenv('MARKET_LENS_DISABLED_ENTRY_SETUPS', FIB)
    selected, decision, evidence = select_qualified_candidate(candidate(), evaluate)
    decision.decision_json = evidence
    records = build_buy_notification_records([(selected, decision)], open_positions={},
        settings=SimpleNamespace(url='https://example.invalid'), run_id='isolated',
        timestamp='2026-10-18T12:00:00Z')
    assert records == []


def test_user_scan_keeps_cash_available_for_other_setups(monkeypatch):
    from app import strategy

    monkeypatch.setenv('MARKET_LENS_DISABLED_ENTRY_SETUPS', FIB)
    monkeypatch.setattr(strategy, 'base_universe', lambda: {'TEST': 'Tech', 'NEXT': 'Tech'})
    monkeypatch.setattr(strategy, 'build_agent_run_context', lambda **kwargs: SimpleNamespace(
        sector_health={'Tech': {'label': 'Strong'}},
        market_regime=SimpleNamespace(max_total_exposure=40000)))
    cash_seen = []

    def risk(**kwargs):
        cash_seen.append(kwargs['cash_available'])
        _, evidence = evaluate(kwargs['result'])
        evidence.update(position_size=kwargs['quantity'], adjusted_cash_out=kwargs['cash_out'],
                        adjusted_risk_amount=kwargs['risk_amount'], entry_execution_price=100)
        return evidence

    monkeypatch.setattr(strategy, 'evaluate_agent_candidate', risk)
    first = candidate()
    second = replace(candidate(BREAKOUT), ticker='NEXT', score=.6)
    results = strategy.apply_strategy_decisions([first, second], analysis_period='6mo', min_rr=2)
    assert [row.strategy_action for row in results] == ['SKIP', 'BUY_SIMULATED']
    assert cash_seen == [100000, 100000]


def test_paused_off_hours_setup_is_not_still_watch_ready(monkeypatch):
    from app.agent_dashboard import build_decision_diagnostics
    from app.performance_summary import is_watch_ready_candidate

    monkeypatch.setenv('MARKET_LENS_DISABLED_ENTRY_SETUPS', FIB)
    decision, evidence = evaluate(candidate(), 'WATCH_READY')
    evidence.update(watch_status='WATCH_READY', off_hours_candidate=True,
                    regular_session_confirmation_required=True,
                    warnings=['WATCH_READY: staged outside regular hours', 'Unrelated data warning'])
    _, _, paused = select_qualified_candidate(candidate(), lambda _: (decision, evidence))
    assert not is_watch_ready_candidate(paused)
    assert paused['warnings'] == ['Unrelated data warning']
    assert paused['entry_strategy_policy']['pre_pause_staging_warnings'] == [evidence['warnings'][0]]
    assert paused['entry_strategy_policy']['pre_pause_staging']['off_hours_candidate']
    diagnostics = build_decision_diagnostics([dict(ticker='TEST', action='SKIP', setup_type=FIB,
                                                  score=.7, reason=paused['reason'], decision_json=paused)])
    assert diagnostics['watch_ready_count'] == 0
    assert 'WATCH_READY' not in diagnostics['drilldowns']


def test_paused_sizing_has_zero_added_sector_and_factor_exposure(monkeypatch):
    monkeypatch.setenv('MARKET_LENS_DISABLED_ENTRY_SETUPS', FIB)
    decision, evidence = evaluate(candidate())
    evidence.update(sector_exposure_before=1000, sector_exposure_after=2000,
                    factor_exposure_before={'Growth': 1500}, factor_exposure_after={'Growth': 2500},
                    position_sizing_explanation='Position size accepted.', technical_entry_gates_passed=True)
    _, _, paused = select_qualified_candidate(candidate(), lambda _: (decision, evidence))
    assert paused['sector_exposure_after'] == 1000
    assert paused['factor_exposure_after'] == {'Growth': 1500}
    assert 'No new position' in paused['position_sizing_explanation']
    assert not paused['technical_entry_gates_passed']
    saved = paused['entry_strategy_policy']['pre_pause_sizing']
    assert saved['sector_exposure_after'] == 2000
    assert saved['factor_exposure_after'] == {'Growth': 2500}
    assert saved['position_sizing_explanation'] == 'Position size accepted.'


def test_dashboard_explains_pause_without_near_miss_or_high_readiness(monkeypatch):
    from app.agent_dashboard import build_decision_diagnostics

    monkeypatch.setenv('MARKET_LENS_DISABLED_ENTRY_SETUPS', FIB)
    decision, evidence = evaluate(candidate())
    evidence.update(setup_score=.7, minimum_setup_score_required=.45, minimum_net_rr_required=2,
                    entry_confirmation_passed=True, sector_regime='STRONG', target_feasibility_status='OK')
    _, _, paused = select_qualified_candidate(candidate(), lambda _: (decision, evidence))
    diagnostics = build_decision_diagnostics([dict(ticker='TEST', action='SKIP', setup_type=FIB,
                                                  score=.7, reason=paused['reason'], decision_json=paused)])
    assert diagnostics['blockers']['Strategy paused'] == 1
    item = diagnostics['drilldowns']['STRATEGY_PAUSED'][0]
    assert item['entry_readiness_score'] == 0
    assert any(row['key'] == 'strategy_paused' for row in item['missing_conditions'])
    assert diagnostics['near_misses'] == diagnostics['closest_to_entry'] == []
