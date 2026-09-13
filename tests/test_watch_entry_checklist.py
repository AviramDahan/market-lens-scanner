from app.agent_dashboard import watch_entry_checklist, diagnostic_drilldown_item


def test_absent_evidence_never_passes():
    assert not any(row['status'] == 'pass' for row in watch_entry_checklist({}))


def test_candle_confirmation_does_not_override_score_or_capital_blockers():
    decision = dict(entry_confirmation_passed=True, setup_score=0.41,
                    minimum_setup_score_required=0.55, net_rr=1.49,
                    minimum_net_rr_required=2.20,
                    capital_blockers=['Sector cap exceeded'])
    rows = {row['label']: row for row in watch_entry_checklist(decision)}
    assert rows['Completed candle confirmation']['status'] == 'pass'
    assert rows['Setup score']['status'] == 'fail'
    assert '0.41' in rows['Setup score']['detail']
    assert '0.55' in rows['Setup score']['detail']
    assert rows['Net R/R']['status'] == 'fail'
    assert rows['Portfolio blocker']['status'] == 'fail'


def test_checklist_is_specific_to_ticker_and_preserves_cooldown_exception():
    item = diagnostic_drilldown_item({'ticker': 'FORM', 'run_date': '2026-09-12'},
                                    {'cooldown_active': True, 'cooldown_exception_used': True},
                                    'WATCH', 'Fib', '')
    assert item['ticker'] == 'FORM'
    assert item['decision_timestamp'] == '2026-09-12'
    assert next(row for row in item['entry_checklist'] if row['label'] == 'Stop-loss cooldown')['status'] == 'pass'
