import copy
import json
import sys

import pytest

from agent import market_lens_ui_agent as scanner, position_monitor, send_buy_notifications
from app import telegram_notifications as telegram


def settings(tmp_path):
    return scanner.Settings('https://example.com', 'dummy@example.com', 'dummy',
                            tmp_path / 'tracker.xlsx', 'smart-universe', [], '6mo', 2.0, True, 60)


def position(quantity=10):
    return dict(ticker='TEST', entry_date='2026-10-09T14:00:00', entry_price=100.0,
                current_price=100.0, quantity=quantity, stop_loss=95.0, target_1=110.0,
                target_2=120.0, partial_taken=False, exposure_ils=quantity * 100.0, risk_ils=quantity * 5.0,
                chart_url='agent_results/charts/test.png', decision_json=json.dumps({'trade_id': 'entry-test-1'}))


def exit_decision(price, quantity=10):
    positions = {'TEST': position(quantity)}
    original = copy.deepcopy(positions['TEST'])
    # A scanner may report NO_TRADE after entry: exit levels belong to the lot.
    result = scanner.SetupResult('TEST', 'No Trade', 0, price, None, None, None, None, None, 0, '', '')
    decision = scanner.decide(result, open_positions=positions, cash=90000, exposure=1000, usd_ils=1,
                              max_position=10000, max_total_exposure=40000, max_risk=1000, min_rr=2,
                              sector_map={}, sector_health={})
    scanner.capture_position_exit_plan(decision.decision_json, positions['TEST'])
    scanner.apply_exit_decision(positions, result, decision, 1)
    return original, positions, result, decision


@pytest.mark.parametrize('price,action,title,count', [
    (94, 'EXIT_STOP', 'STOP | POSITION CLOSED', 1),
    (111, 'TAKE_PARTIAL_PROFIT', 'TP1 | PARTIAL SOLD', 2),
    (121, 'TAKE_PROFIT', 'TP2 | POSITION CLOSED', 1),
])
def test_real_scanner_exit_path_produces_alert_after_position_mutation(tmp_path, price, action, title, count):
    original, positions, result, decision = exit_decision(price)
    before = copy.deepcopy((positions, result, decision))
    records = scanner.build_buy_notification_records([(result, decision)], open_positions=positions,
             settings=settings(tmp_path), run_id='exit-test-1', timestamp='2026-10-09T15:00:00')
    assert len(records) == count
    assert records[0]['notification_type'] == action
    assert title in records[0]['message']
    assert 'SL: $95.00 (-5.00%)' in records[0]['message']
    assert 'TP1: $110.00 (+10.00%)' in records[0]['message']
    assert 'Observed quote:' in records[0]['message'] and 'Bar H/L/C' not in records[0]['message']
    assert records[0]['chart_ref'] == original['chart_url']
    event = position_monitor.PositionEvent('TEST', action, '', decision.execution_price, 0, 0, price,
                                          decision.quantity, decision.cash_in_ils, '')
    assert records[0]['dedupe_key'] == position_monitor.position_event_dedupe_key(original, event)
    assert '|entry-test-1|' in records[0]['dedupe_key']
    if action == 'TAKE_PARTIAL_PROFIT':
        assert positions['TEST']['stop_loss'] == 100
        assert records[1]['notification_type'] == 'STOP_TO_ENTRY'
        assert 'Remaining qty: 5' in records[1]['message']
    else:
        assert positions == {}
    assert (positions, result, decision) == before


def test_one_share_tp1_closes_lot_without_claiming_stop_was_moved(tmp_path):
    _, positions, result, decision = exit_decision(111, quantity=1)
    records = scanner.build_buy_notification_records([(result, decision)], open_positions=positions,
              settings=settings(tmp_path), run_id='r', timestamp='2026-10-09T15:00:00')
    assert positions == {} and len(records) == 1
    assert records[0]['notification_type'] == 'TAKE_PARTIAL_PROFIT'
    assert 'no remaining stop' in records[0]['message']
    assert 'stop moves to entry' not in records[0]['message']


class Response:
    status = 200
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        return False


def fake_transport(monkeypatch, tmp_path, calls, failing=False):
    monkeypatch.setenv('MARKET_LENS_TELEGRAM_DEDUP_LOG', str(tmp_path / 'receipts.jsonl'))
    def opener(request, timeout):
        calls.append(json.loads(request.data))
        if failing:
            raise OSError('simulated delivery failure')
        return Response()
    def send(message, **kwargs):
        return telegram.send_telegram_message(message, settings=telegram.TelegramSettings('fake', 'fake-chat'),
                    opener=opener, dedupe_key=kwargs['dedupe_key'])
    monkeypatch.setattr(scanner, 'send_telegram_notification', send)
    monkeypatch.setattr(scanner, 'send_telegram_message', send)


def test_scanner_outbox_is_deferred_retry_safe_and_shared_with_monitor_dedupe(monkeypatch, tmp_path):
    original, positions, result, decision = exit_decision(94)
    calls = []
    fake_transport(monkeypatch, tmp_path, calls)
    outbox = tmp_path / 'scanner.json'
    scanner.write_buy_notification_outbox(outbox, [(result, decision)], open_positions=positions,
            settings=settings(tmp_path), run_id='r', timestamp='2026-10-09T15:00:00')
    assert calls == []
    assert scanner.send_buy_notification_outbox(outbox)[0].status == 'sent'
    assert scanner.send_buy_notification_outbox(outbox)[0].status == 'duplicate'
    records = scanner.build_buy_notification_records([(result, decision)], open_positions=positions,
              settings=settings(tmp_path), run_id='another-run', timestamp='2026-10-10T15:00:00')
    assert scanner.send_buy_notification_records(records)[0].status == 'duplicate'
    event = position_monitor.PositionEvent('TEST', 'EXIT_STOP', '', decision.execution_price,
                                          100, 94, 94, decision.quantity, decision.cash_in_ils, '')
    key = position_monitor.position_event_dedupe_key(original, event)
    assert telegram.telegram_dedupe_seen(key) and len(calls) == 1


def test_failed_cli_delivery_preserves_outbox_for_retry(monkeypatch, tmp_path):
    _, positions, result, decision = exit_decision(94)
    calls = []
    fake_transport(monkeypatch, tmp_path, calls, failing=True)
    outbox = tmp_path / 'scanner.json'
    scanner.write_buy_notification_outbox(outbox, [(result, decision)], open_positions=positions,
            settings=settings(tmp_path), run_id='r', timestamp='2026-10-09T15:00:00')
    monkeypatch.setattr(sys, 'argv', ['send_buy_notifications', str(outbox)])
    with pytest.raises(SystemExit, match='scanner notifications failed'):
        send_buy_notifications.main()
    assert outbox.exists()
    assert not (tmp_path / 'receipts.jsonl').exists()
    fake_transport(monkeypatch, tmp_path, calls)
    send_buy_notifications.main()
    assert not outbox.exists() and (tmp_path / 'receipts.jsonl').exists()


def test_partial_retry_does_not_resend_tp1_when_only_stop_alert_failed(monkeypatch, tmp_path):
    _, positions, result, decision = exit_decision(111)
    calls = []
    fake_transport(monkeypatch, tmp_path, calls)
    actual_send = scanner.send_telegram_message
    monkeypatch.setattr(scanner, 'send_telegram_message', lambda *_a, **_k: telegram.TelegramSendResult(False, 'failed'))
    outbox = tmp_path / 'scanner.json'
    scanner.write_buy_notification_outbox(outbox, [(result, decision)], open_positions=positions,
            settings=settings(tmp_path), run_id='r', timestamp='2026-10-09T15:00:00')
    assert [item.status for item in scanner.send_buy_notification_outbox(outbox)] == ['sent', 'failed']
    monkeypatch.setattr(scanner, 'send_telegram_message', actual_send)
    assert [item.status for item in scanner.send_buy_notification_outbox(outbox)] == ['duplicate', 'sent']
    assert len(calls) == 2


def test_exit_without_pre_exit_evidence_fails_explicitly(tmp_path):
    _, positions, result, decision = exit_decision(94)
    decision.decision_json.pop('position_exit_plan')
    with pytest.raises(ValueError, match='Missing persisted position exit evidence'):
        scanner.build_buy_notification_records([(result, decision)], open_positions=positions,
              settings=settings(tmp_path), run_id='r', timestamp='2026-10-09T15:00:00')
