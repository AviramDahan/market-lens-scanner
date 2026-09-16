import json
from dataclasses import replace

import pytest

from app.candidate_selection import select_qualified_candidate
from app.execution import EXECUTION_VERSION, STRATEGY_VERSION, sell_fill
from app.strategy import StrategyCandidate, StrategyDecision, apply_strategy_exit, refresh_strategy_position


def candidate():
    return StrategyCandidate('TEST', 'original', .6, 100, 99, 101, 95, 110, 120, 3)


def alternative(name, score):
    return dict(setup_type=name, professional_adjusted_score=score, buy_zone_low=99,
                buy_zone_high=101, stop_loss=95, target_1=110, target_2=120, risk_reward=3)


def test_only_fully_qualified_candidate_can_win():
    result = replace(candidate(), setup_candidates=[alternative('blocked', .95), alternative('winner', .8)])
    calls = []
    def evaluate(c):
        calls.append(c.setup_type)
        return StrategyDecision('BUY_SIMULATED', ''), dict(final_action='WATCH' if c.setup_type == 'blocked' else 'BUY_SIMULATED', net_rr_1=2)
    selected, _, evidence = select_qualified_candidate(result, evaluate)
    assert selected.setup_type == 'winner'
    assert calls == ['original', 'blocked', 'winner']
    assert evidence['strategy_version'] == STRATEGY_VERSION
    assert sum(item['selected'] for item in evidence['selection_candidates']) == 1


@pytest.mark.parametrize('action', ['WATCH', 'WATCH_READY', 'SKIP', 'HOLD'])
def test_no_promotion_when_no_candidate_passes(action):
    c = replace(candidate(), setup_candidates=[alternative('other', .9)])
    selected, _, evidence = select_qualified_candidate(c, lambda _: (None, dict(final_action=action)))
    assert selected.setup_type == 'original'
    assert evidence['final_action'] == action


def test_existing_position_never_switches_setup_or_version():
    c = replace(candidate(), setup_candidates=[alternative('other', .9)])
    calls = []
    def evaluate(c):
        calls.append(c.setup_type)
        return None, dict(final_action='HOLD')
    _, _, evidence = select_qualified_candidate(c, evaluate, existing=True)
    assert calls == ['original', 'other']  # Alternatives are assessed for alerts only.
    assert 'strategy_version' not in evidence


def test_equal_scores_use_tp1_net_rr_with_same_score_precision():
    c = replace(candidate(), score=.81, setup_candidates=[alternative('original', .805), alternative('other', .805)])
    selected, _, _ = select_qualified_candidate(c, lambda c: (None, dict(final_action='BUY_SIMULATED', net_rr_1=3 if c.setup_type == 'other' else 2)))
    assert selected.setup_type == 'other'


def position():
    return dict(ticker='TEST', entry_price=100.4, quantity=10, stop_loss=95, target_1=110,
                target_2=120, exposure_ils=1000, decision_json=json.dumps(dict(
                    strategy_version=STRATEGY_VERSION, execution_model_version=EXECUTION_VERSION,
                    execution_cost_policy=dict(half_spread_per_share=.1, slippage_per_share=.2, fee_per_share=.1))))


@pytest.mark.parametrize('action,trigger,observed,expected', [
    ('EXIT_STOP', 95, 90, 89.6), ('EXIT_STOP', 95, 97, 94.6),
    ('TAKE_PARTIAL_PROFIT', 110, None, 109.7), ('TAKE_PROFIT', 120, None, 119.7),
])
def test_fills_include_costs_and_stop_gaps(action, trigger, observed, expected):
    price, details = sell_fill(position(), action, trigger, observed)
    assert price == expected
    assert details['exit_execution_price'] == expected
    assert sell_fill({}, action, trigger, observed) == (trigger, {})


def test_partial_restart_remaining_stop_risk_and_replay():
    p = position()
    positions = {'TEST': p}
    c = replace(candidate(), current_price=110)
    price, details = sell_fill(p, 'TAKE_PARTIAL_PROFIT', 110)
    d = StrategyDecision('TAKE_PARTIAL_PROFIT', '', quantity=5, cash_in_ils=5*price, execution_price=price, decision_json=details)
    cash, _ = apply_strategy_exit(positions, c, d, 1)
    assert cash == 548.5
    assert p['quantity'] == 5 and p['stop_loss'] == 100.4
    restored = json.loads(json.dumps(p))
    refresh_strategy_position(restored, 105, 1)
    assert restored['risk_ils'] == 2
    assert sell_fill(restored, 'EXIT_STOP', 100.4, 98)[0] == 97.6
    with pytest.raises(ValueError):
        apply_strategy_exit(positions, c, d, 1)


@pytest.mark.parametrize('bad', [-1, float('nan'), float('inf')])
def test_invalid_cost_policy_fails_visibly(bad):
    p = position()
    meta = json.loads(p['decision_json'])
    meta['execution_cost_policy']['fee_per_share'] = bad
    p['decision_json'] = meta
    with pytest.raises(ValueError):
        sell_fill(p, 'EXIT_STOP', 95, 94)


def test_cohorts_net_pnl_and_costs_are_not_double_counted():
    from app.agent_dashboard import compute_full_trade_performance
    meta = json.loads(position()['decision_json'])
    meta['entry_cost_per_share'] = .4
    trades = [dict(timestamp='2026-09-01T14:00:00Z', action='BUY_SIMULATED', ticker='TEST',
                   quantity=10, entry_price_usd=100.4, cash_out_ils=1004, stop_loss=95,
                   usd_ils=1, decision_json=meta),
              dict(timestamp='2026-09-02T14:00:00Z', action='TAKE_PARTIAL_PROFIT', ticker='TEST',
                   quantity=5, exit_price_usd=109.7, cash_in_ils=548.5, usd_ils=1,
                   decision_json={'exit_cost_per_share': .3}),
              dict(timestamp='2026-09-03T14:00:00Z', action='TAKE_PROFIT', ticker='TEST',
                   quantity=5, exit_price_usd=119.7, cash_in_ils=598.5, usd_ils=1,
                   decision_json={'exit_cost_per_share': .3}),
              dict(timestamp='2026-09-01T14:00:00Z', action='BUY_SIMULATED', ticker='OLD',
                   quantity=1, entry_price_usd=100, cash_out_ils=100, stop_loss=95)]
    cohorts = compute_full_trade_performance(trades)['strategy_cohorts']
    assert cohorts[STRATEGY_VERSION] == dict(closed_count=1, open_count=0, closed_pnl=143, modeled_costs=7)
    assert cohorts['legacy'] == dict(closed_count=0, open_count=1, closed_pnl=0, modeled_costs=0)


def test_dashboard_and_monitor_agree_on_costed_stop_risk():
    from agent.position_monitor import refresh_position
    from app.agent_dashboard import with_position_calculations
    p = position()
    p['stop_loss'] = p['entry_price']
    refresh_position(p, 105, 1)
    dashboard = with_position_calculations(dict(p, entry_price_usd=p['entry_price'], current_price_usd=105))
    assert p['risk_ils'] == dashboard['open_risk_ils'] == 4
