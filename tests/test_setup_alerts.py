from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import json
import subprocess

import pytest

from app.agent_risk import evaluate_setup_alert_quality, evaluate_agent_candidate
from app.candidate_selection import select_qualified_candidate
from app.strategy import StrategyCandidate, StrategyDecision
from agent.market_lens_ui_agent import build_buy_notification_records
from agent.persist_notification_receipts import merge_receipts, persist_receipts
from test_agent_entry_gates import context, result, _patch_risk_dependencies


def quality_args(regime='BEAR'):
    candidate = result()
    candidate.risk_reward = 3
    return dict(result=candidate, run_context=context(regime), sector_info={'regime':'STRONG'},
                net_rr_info={'net_rr':2.6,'net_rr_1':1.2,'net_rr_2':4},
                earnings_info={'earnings_blackout':False}, target_info={'status':'OK'},
                confirmation={'entry_confirmation_passed':True,'confirmation_reason':'passed',
                              'confirmation_timeframe':'30m_completed',
                              'confirmation_candle_timestamp':'2026-07-08T10:00:00-04:00'},
                market_session={'phase':'REGULAR','timestamp':'2026-07-08T10:30:00-04:00',
                                'regular_session_open':True,'can_open_new_buy':True,'reason':'open'},
                normalized_quality_score=80)


def test_bear_uses_normal_quality_floors_without_mutating_actual_regime():
    args = quality_args()
    evidence = evaluate_setup_alert_quality(**args)
    assert evidence['eligible'] and evidence['market_regime_ignored']
    assert evidence['minimum_setup_score'] == .45 and evidence['minimum_net_rr'] == 2
    assert args['run_context'].market_regime.label == 'BEAR'
    assert args['run_context'].market_regime.minimum_net_rr == 999


@pytest.mark.parametrize('fault', ['score','rr','tp1_rr','zone','stop','target','earnings','sector',
                                  'confirmation','stale','future','session','quality','nan','no_setup'])
def test_non_portfolio_failures_never_alert(fault):
    args = quality_args()
    if fault=='score': args['result'].score=.44
    if fault=='rr': args['net_rr_info']['net_rr']=1.9
    if fault=='tp1_rr': args['net_rr_info']['net_rr_1']=.79
    if fault=='zone': args['result'].current_price=101
    if fault=='stop': args['result'].stop_loss=101
    if fault=='target': args['target_info']['status']='EXTENDED'
    if fault=='earnings': args['earnings_info']['earnings_blackout']=True
    if fault=='sector': args['sector_info']['regime']='WEAK'
    if fault=='confirmation': args['confirmation']['entry_confirmation_passed']=False
    if fault=='stale': args['confirmation']['confirmation_candle_timestamp']='2026-07-07T10:00:00-04:00'
    if fault=='future': args['confirmation']['confirmation_candle_timestamp']='2026-07-08T11:00:00-04:00'
    if fault=='session': args['market_session'].update(regular_session_open=False,can_open_new_buy=False,phase='AFTER_HOURS')
    if fault=='quality': args['normalized_quality_score']=34
    if fault=='nan': args['net_rr_info']['net_rr']=float('nan')
    if fault=='no_setup': args['result'].setup_type='No Trade'
    assert not evaluate_setup_alert_quality(**args)['eligible']


@pytest.mark.parametrize('zero_size', [False, True])
def test_real_risk_pipeline_alert_does_not_promote_bear_to_buy(monkeypatch, zero_size):
    _patch_risk_dependencies(monkeypatch, net_rr=2.6, confirmation_passed=True)
    ctx=context('BEAR')
    ctx.sector_health={'Technology':{'label':'Strong','score':80,'etf':'XLK','reason':'test'}}
    candidate=result(); candidate.risk_reward=3
    payload=evaluate_agent_candidate(timestamp='2026-07-08T14:30:00Z',result=candidate,
        initial_action='SKIP' if zero_size else 'BUY_SIMULATED',
        initial_reason='Position size blocked by cash, exposure, or risk limits.' if zero_size else 'base buy',
        quantity=0 if zero_size else 10,cash_out=0 if zero_size else 1000,risk_amount=0 if zero_size else 50,
        cash_available=0 if zero_size else 10000,portfolio_exposure_before=40000,
        portfolio_open_risk_before=2500,open_positions={},sector_map={'TEST':'Technology'},
        run_context=ctx,neutral_pilot_trades_today=10)
    assert payload['final_action']!='BUY_SIMULATED'
    assert payload['setup_alert_evidence']['eligible'] is True
    assert payload['market_regime']=='BEAR'


def candidate():
    return StrategyCandidate('TEST','original',.6,100,98,100,95,105,115,3,
        setup_candidates=[dict(setup_type='alternative',professional_adjusted_score=.8,
            buy_zone_low=98,buy_zone_high=100,stop_loss=94,target_1=108,target_2=120,risk_reward=3)])


def evaluate(c):
    return StrategyDecision('SKIP','BEAR'),dict(final_action='SKIP',reason='BEAR blocks entry',
        market_regime='BEAR',sector_regime='STRONG',net_rr=2.6,net_rr_1=1.2,net_rr_2=4,net_entry=100.1,
        setup_alert_evidence=dict(eligible=c.setup_type=='alternative',blockers=[],
            market_regime_ignored=True,minimum_setup_score=.45,minimum_net_rr=2))


def test_alternative_alert_keeps_original_trade_decision_and_uses_its_own_levels(monkeypatch):
    selected, decision, evidence=select_qualified_candidate(candidate(),evaluate)
    assert selected.setup_type=='original' and decision.action=='SKIP'
    assert evidence['qualified_setup_alert']['stop_loss']==94
    decision.decision_json=evidence
    settings=SimpleNamespace(url='https://example.com')
    before=deepcopy(evidence)
    records=build_buy_notification_records([(selected,decision)],open_positions={},settings=settings,
        run_id='one',timestamp='2026-07-08T14:30:00Z')
    assert len(records)==1 and records[0]['notification_type']=='QUALIFIED_SETUP'
    assert 'QUALIFIED SETUP' in records[0]['message'] and '$94.00' in records[0]['message']
    assert 'BEAR' in records[0]['message'] and 'Proposed qty' not in records[0]['message']
    assert all(word not in records[0]['message'] for word in ['Agent', 'cash', 'portfolio', 'Dashboard', 'existing position'])
    assert records[0]['chart_ref']=='' and evidence==before
    repeat=build_buy_notification_records([(selected,decision)],open_positions={},settings=settings,
        run_id='two',timestamp='2026-07-08T15:00:00Z')
    assert records[0]['dedupe_key']==repeat[0]['dedupe_key']
    monkeypatch.setenv('MARKET_LENS_TELEGRAM_QUALIFIED_BLOCKED_ENABLED','false')
    assert build_buy_notification_records([(selected,decision)],open_positions={},settings=settings,
        run_id='three',timestamp='2026-07-08T15:00:00Z')==[]


def test_existing_position_stays_hold_with_independent_alternative_alert():
    def held(c):
        _, payload=evaluate(c);payload['final_action']='HOLD'
        return StrategyDecision('HOLD','existing'),payload
    selected,decision,payload=select_qualified_candidate(candidate(),held,existing=True)
    assert selected.setup_type=='original' and decision.action=='HOLD'
    assert payload['qualified_setup_alert']['existing_position'] is True
    assert 'strategy_version' not in payload


def test_actual_buy_suppresses_extra_setup_alert():
    def buy(c):
        _, payload=evaluate(c);payload['final_action']='BUY_SIMULATED'
        return StrategyDecision('BUY_SIMULATED','buy'),payload
    _, _, payload=select_qualified_candidate(candidate(),buy)
    assert payload['qualified_setup_alert'] is None


def test_receipt_merge_keeps_latest_and_strips_unexpected_fields():
    old=json.dumps(dict(key='same',sent_at='2026-07-08T14:00:00Z',extra='ignored'))
    new=json.dumps(dict(key='same',sent_at='2026-07-08T15:00:00Z'))
    assert json.loads(merge_receipts(old,new))==dict(key='same',sent_at='2026-07-08T15:00:00Z')


def test_receipt_persistence_preserves_remote_portfolio_and_survives_restart(tmp_path):
    def git(*args,cwd=None):
        return subprocess.run(['git',*args],cwd=cwd,check=True,capture_output=True,text=True).stdout
    remote=tmp_path/'remote.git';root=tmp_path/'runner';other=tmp_path/'other'
    git('init','--bare',str(remote));git('clone',str(remote),str(root))
    for key,value in [('user.name','Test'),('user.email','test@example.com')]:git('config',key,value,cwd=root)
    git('checkout','-b','main',cwd=root)
    (root/'portfolio.txt').write_text('old')
    git('add','.',cwd=root);git('commit','-m','base',cwd=root);git('push','origin','main',cwd=root)
    git('clone','-b','main',str(remote),str(other))
    for key,value in [('user.name','Test'),('user.email','test@example.com')]:git('config',key,value,cwd=other)
    (other/'portfolio.txt').write_text('newer portfolio')
    git('add','.',cwd=other);git('commit','-m','advance',cwd=other);git('push','origin','main',cwd=other)
    receipts=root/'agent_results/telegram_notifications.jsonl';receipts.parent.mkdir()
    receipts.write_text(json.dumps(dict(key='QUALIFIED_SETUP|TEST|2026-07-08',sent_at='2026-07-08T14:30:00Z'))+'\n')
    persist_receipts(root)
    assert git('show','origin/main:portfolio.txt',cwd=root)=='newer portfolio'
    first=git('rev-parse','origin/main',cwd=root)
    persist_receipts(root)
    assert git('rev-parse','origin/main',cwd=root)==first
    assert 'QUALIFIED_SETUP' in git('show','origin/main:agent_results/telegram_notifications.jsonl',cwd=root)
