from __future__ import annotations

from agent.position_monitor import PositionEvent, event_r_multiple, position_duration


def test_exit_analytics_preserve_original_risk_after_stop_moves_to_entry() -> None:
    position = {"entry_price": 100, "stop_loss": 100}
    event = PositionEvent(
        ticker="TEST",
        action="EXIT_STOP",
        triggered_at="2026-08-21T15:00:00+00:00",
        trigger_price=100,
        high=101,
        low=99.9,
        close=100,
        quantity=5,
        cash_in=500,
        note="Breakeven stop touched.",
    )

    assert event_r_multiple(position, event, {"stop_loss": 95}) == 0.0
    assert position_duration("2026-08-20T15:00:00+00:00", event.triggered_at) == "24.00 hours"
