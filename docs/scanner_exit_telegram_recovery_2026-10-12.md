# Scanner exit notifications

## Confirmed incident

The recorded TRGP stop on October 9 and TGTX/PFE stops on October 10 were
scanner-originated exits. The scanner persisted these exits but its outbox
builder handled only BUY and qualified setup notifications. The monitor could
not notify later because the positions had already been removed.

## Focused repair

- Keep the existing scanner outbox schema, post-persistence delivery command,
  destination configuration, retries and durable Telegram receipts.
- Include EXIT_STOP, TAKE_PARTIAL_PROFIT and TAKE_PROFIT in that outbox.
- Capture the original lot quantity and chart before an exit mutates/removes it.
- Reuse monitor event formatting and identity, so a replay cannot create a
  second notification for an event already delivered through the monitor.
- Include the existing stop-to-entry notification only when TP1 leaves shares.
- A scanner quote is not an OHLC candle; do not invent bar high/low values.
- Missing pre-exit evidence fails explicitly instead of silently dropping an alert.

No change to execution, entry/exit policy, sizing, capital or strategy. No
historical trade is edited. Historical recovery must use a verified persisted
trade and its original timestamp, be labelled delayed, and retain the same
event identity/receipt. No synthetic trade or real test notification is needed.

## Validation

`python -m pytest -q tests/test_scanner_exit_notifications.py tests/test_telegram_notifications.py`

Regression cases cover all three actual scanner exits, original levels despite
NO_TRADE, position removal, partial stop movement, one-share TP1, shared monitor
dedupe, delayed outbox delivery, failed delivery retry, partial delivery retry
and incomplete evidence. Telegram is simulated in all tests.
