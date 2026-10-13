import copy
import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from agent.audit_stale_scanner_exits import build_review
from app.agent_dashboard import annotate_historical_execution_review


def fixture():
    metadata=dict(trade_id='lot',strategy_version='qualified_selection_v1',entry_execution_price=100.,
                  stop_loss=95.,market_regime='NEUTRAL',setup_type='Fib',confirmation_closed_age_minutes=64.)
    buy=dict(action='BUY_SIMULATED',trade_id='lot',ticker='TEST',quantity=10,timestamp='2026-10-08T19:00:00Z',
             entry_price_usd=100.,cash_out_ils=1000.,usd_ils=1.,stop_loss=95.,target_1=110.,target_2=120.,decision_json=metadata)
    stop=dict(action='EXIT_STOP',trade_id='lot',ticker='TEST',quantity=10,timestamp='2026-10-09T00:20:00Z',
              exit_price_usd=90.,cash_in_ils=900.,usd_ils=1.,decision_json=dict(exit_reference_price=90.))
    sources=[dict(trade_id='lot',ticker='TEST',previous_session='2026-10-07',previous_session_close=90.,
                  archived_quote_price=101.,source_chart='original.png')]
    return [buy,stop],sources


def test_review_never_restates_cash_or_treats_open_counterfactual_as_closed():
    events,sources=fixture()
    original=copy.deepcopy(events)
    frame=pd.DataFrame(dict(Open=[100.,101.],High=[102.,103.],Low=[99.,100.],Close=[101.,102.]),
                       index=pd.to_datetime(['2026-10-08T19:00:00Z','2026-10-08T23:59:00Z']))
    report=build_review(events,sources,{'TEST':frame},observed_at=datetime(2026,10,9,0,21,tzinfo=timezone.utc))
    assert report['portfolio_restatement'] is False and events == original
    assert report['affected_recorded_pnl'] == -100
    row=report['annotations'][0]
    assert row['status']=='CONFIRMED_STALE_PRICE_SOURCE'
    assert row['later_counterfactual']['event'] is None and row['later_counterfactual']['status']=='HOLD'


def test_missing_bars_are_not_claimed_as_zero_stop_activity():
    events,sources=fixture()
    report=build_review(events,sources,{},observed_at=datetime(2026,10,9,0,21,tzinfo=timezone.utc))
    row=report['annotations'][0]
    assert row['status']=='ARCHIVED_PRICE_MISMATCH' and row['before_recorded_exit'] is None


def test_contradictory_stop_bar_blocks_false_stop_classification():
    events,sources=fixture()
    frame=pd.DataFrame(dict(Open=[100.,98.],High=[102.,99.],Low=[99.,94.],Close=[101.,94.]),
                       index=pd.to_datetime(['2026-10-08T19:00:00Z','2026-10-08T19:59:00Z']))
    with pytest.raises(ValueError,match='contradicts'):
        build_review(events,sources,{'TEST':frame},observed_at=datetime(2026,10,9,0,21,tzinfo=timezone.utc))


def test_dashboard_annotations_preserve_financial_fields_and_match_trade_identity(tmp_path):
    folder=tmp_path/'audits'
    folder.mkdir()
    row=dict(trade_id='lot',ticker='TEST',timestamp='2026-10-09T00:20:00Z',status='CONFIRMED_STALE_PRICE_SOURCE',
             reason='Stale source',source_chart='original.png',recorded_pnl=-100.)
    (folder/'position_execution_review_20261013.json').write_text(json.dumps(dict(schema_version=1,portfolio_restatement=False,annotations=[row])))
    events,_=fixture()
    unrelated=dict(events[-1],trade_id='different-lot')
    events.append(unrelated)
    before=copy.deepcopy(events)
    performance={'closed':[dict(trade_id='lot',pnl_ils=-100.)]}
    summary=annotate_historical_execution_review(events,performance,tmp_path)
    assert summary['flagged_exits']==1 and summary['affected_recorded_pnl']==-100
    assert events[0] == before[0] and events[2] == before[2]
    assert {k:v for k,v in events[1].items() if k!='execution_data_quality'} == before[1]
    assert performance['closed'][0]['pnl_ils']==-100


def test_malformed_report_is_not_healthy_zero(tmp_path):
    folder=tmp_path/'audits'
    folder.mkdir()
    (folder/'position_execution_review_20261013.json').write_text('{broken')
    result=annotate_historical_execution_review([],{},tmp_path)
    assert result['status']=='REPORT_ERROR' and result['flagged_exits'] is None
