"""Read-only historical execution review; never restates portfolio cash or trades."""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

import pandas as pd
from openpyxl import load_workbook

from app.agent_dashboard import compute_full_trade_performance, read_trade_analytics
from agent.position_monitor import MonitorSettings, monitor_position, parse_timestamp


def cohort(rows):
    wins = [float(r['pnl_ils']) for r in rows if float(r['pnl_ils']) > 0]
    losses = [float(r['pnl_ils']) for r in rows if float(r['pnl_ils']) < 0]
    return dict(closed_trades=len(rows), unique_tickers=len({r['ticker'] for r in rows}),
                wins=len(wins), losses=len(losses), net_pnl=round(sum(wins + losses), 2),
                modeled_costs=round(sum(float(r.get('modeled_costs') or 0) for r in rows),2),
                profit_factor=round(sum(wins) / -sum(losses), 4) if losses else None)


def build_review(events, observations, frames, *, observed_at):
    closed = compute_full_trade_performance(events)['closed']
    entries = {str(e.get('trade_id')): e for e in events if e['action'] == 'BUY_SIMULATED'}
    exits = {str(e.get('trade_id')): e for e in events if e['action'] == 'EXIT_STOP'}
    full_by_id = {str(r['trade_id']): r for r in closed}
    annotations = []
    for source in observations:
        identity = source['trade_id']
        entry, exit_row = entries.get(identity), exits.get(identity)
        if not entry or not exit_row or identity not in full_by_id:
            raise ValueError(f'Observation does not match a closed ledger trade: {identity}')
        metadata = entry.get('decision_json') or {}
        exit_metadata = exit_row.get('decision_json') or {}
        reported_price = exit_metadata.get('exit_reference_price')
        if reported_price is None or abs(float(reported_price) - float(source['previous_session_close'])) > 0.0001:
            raise ValueError('Stale-price observation does not match the recorded execution reference')
        stop = float(metadata['stop_loss'])
        if not float(reported_price) <= stop < float(source['archived_quote_price']):
            raise ValueError('Archived quote does not corroborate the suspected false stop')
        position = dict(ticker=entry['ticker'],entry_date=entry['timestamp'],entry_price=metadata['entry_execution_price'],
                        quantity=entry['quantity'],stop_loss=stop,target_1=entry['target_1'],target_2=entry['target_2'],
                        partial_taken=False,decision_json=json.dumps(metadata))
        frame = frames.get(entry['ticker'])
        before_exit, later = None, None
        status = 'ARCHIVED_PRICE_MISMATCH'
        coverage = None
        if frame is not None and not frame.empty:
            start, cutoff = parse_timestamp(entry['timestamp']), parse_timestamp(exit_row['timestamp'])
            if frame.index[0].to_pydatetime() > start or frame.index[-1].to_pydatetime() < cutoff - pd.Timedelta(hours=5):
                raise ValueError('Intraday evidence does not cover the relevant holding interval')
            settings = MonitorSettings(Path('NOT_OPENED'),Path('NOT_WRITTEN'),'5d','1m',False,'')
            original_window = frame.loc[frame.index + pd.Timedelta(minutes=1) <= cutoff]
            before = monitor_position(position.copy(),settings=settings,since=None,currency_rate=1,
                       frame_cache={entry['ticker']:original_window},as_of=cutoff)
            if before.error or before.event:
                raise ValueError('Verified-bar replay contradicts a false-stop classification')
            before_exit = asdict(before)
            after = monitor_position(position.copy(),settings=settings,since=None,currency_rate=1,
                      frame_cache={entry['ticker']:frame},as_of=observed_at)
            later = asdict(after)
            status = 'CONFIRMED_STALE_PRICE_SOURCE'
            coverage = dict(first_bar=frame.index[0].isoformat(),last_bar=frame.index[-1].isoformat(),interval='1m')
        annotations.append(dict(trade_id=identity,ticker=entry['ticker'],action='EXIT_STOP',timestamp=exit_row['timestamp'],
             status=status,recorded_pnl=full_by_id[identity]['pnl_ils'],execution_reference=reported_price,
             planned_stop=stop,archived_quote_price=source['archived_quote_price'],source_chart=source['source_chart'],
             previous_session=source['previous_session'],before_recorded_exit=before_exit,later_counterfactual=later,
             coverage=coverage,reason='Technical setup price matched the previous-session close; archived quote was above the stop.'))
    qualified = [r for r in closed if r['strategy_version'] == 'qualified_selection_v1']
    affected = {r['trade_id'] for r in annotations}
    ages = [float(entries[r['trade_id']]['decision_json']['confirmation_closed_age_minutes'])
            for r in qualified if entries.get(r['trade_id'],{}).get('decision_json',{}).get('confirmation_closed_age_minutes') is not None]
    return dict(schema_version=1,generated_at=observed_at.isoformat(),read_only=True,
                portfolio_restatement=False,annotations=annotations,
                affected_recorded_pnl=round(sum(r['recorded_pnl'] for r in annotations),2),
                qualified_cohort=cohort(qualified),
                qualified_cohort_excluding_flagged_for_diagnosis_only=cohort([r for r in qualified if r['trade_id'] not in affected]),
                neutral_pilot_research=dict(sample_status='INCONCLUSIVE_NEEDS_CLEAN_FORWARD_SAMPLE',
                    setup_types=sorted({r['setup_type'] for r in qualified}),
                    regimes=sorted({r['market_regime'] for r in qualified}),
                    confirmation_age_observations=len(ages),confirmation_closed_age_over_60_minutes=sum(a>60 for a in ages),
                    average_confirmation_closed_age_minutes=round(mean(ages),2) if ages else None,
                    recommendation='Fix execution validity first; compare confirmation freshness and Fib stop variants in shadow only.'),
                limitations=['Later counterfactuals are independent trade observations, not portfolio returns.',
                    'No closed outcome is assigned to a still-open counterfactual.',
                    'Missing bar evidence is not interpreted as no TP/SL activity.',
                    'Re-fetched Yahoo bars are corroboration, not an archived broker execution feed.',
                    'Daily-bar MFE/MAE includes entire entry/exit days and cannot validate entry timing.',
                    'The legacy cohort has different periods and execution costs; this is not a causal version comparison.'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tracker',type=Path,required=True)
    parser.add_argument('--observations',type=Path,required=True)
    parser.add_argument('--bar-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--as-of',type=parse_timestamp,required=True)
    args=parser.parse_args()
    before=hashlib.sha256(args.tracker.read_bytes()).hexdigest()
    wb=load_workbook(args.tracker,read_only=True,data_only=True)
    try:
        events=read_trade_analytics(wb)
    finally:
        wb.close()
    observations=json.loads(args.observations.read_text(encoding='utf-8'))
    frames={}
    evidence_hashes={}
    for source in observations:
        ticker=source['ticker']
        path=args.bar_dir / source['bar_file']
        if path.exists():
            evidence_hashes[source['bar_file']]=hashlib.sha256(path.read_bytes()).hexdigest()
            frame=pd.read_csv(path,index_col=0,parse_dates=True)
            frame.index=pd.to_datetime(frame.index,utc=True)
            frames[ticker]=frame
    report=build_review(events,observations,frames,observed_at=args.as_of)
    if hashlib.sha256(args.tracker.read_bytes()).hexdigest()!=before:
        raise ValueError('Tracker changed during read-only audit')
    report.update(source_tracker_sha256=before,source_tracker_unchanged=True,bar_evidence_sha256=evidence_hashes)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,default=str)+'\n',encoding='utf-8')
    print(json.dumps(dict(output=str(args.output),annotations=len(report['annotations']),read_only=True)))


if __name__=='__main__':
    main()
