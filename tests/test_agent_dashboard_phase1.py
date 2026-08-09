from __future__ import annotations

from app.agent_dashboard import (
    compact_agent_dashboard_payload,
    compute_realized_pnl,
    dashboard_section_payload,
)


def test_compute_realized_pnl_annotates_exit_trade_with_entry_and_r() -> None:
    trades = [
        {
            "timestamp": "2026-08-05T14:00:00",
            "action": "BUY_SIMULATED",
            "ticker": "AAA",
            "entry_price_usd": 100,
            "price_usd": 100,
            "quantity": 10,
            "cash_out_ils": 1000,
            "buy_value_ils": 1000,
            "cash_in_ils": 0,
            "stop_loss": 95,
        },
        {
            "timestamp": "2026-08-06T15:00:00",
            "action": "TAKE_PROFIT",
            "ticker": "AAA",
            "exit_price_usd": 112,
            "price_usd": 112,
            "quantity": 10,
            "cash_out_ils": 0,
            "cash_in_ils": 1120,
            "sell_value_ils": 1120,
            "stop_loss": 95,
        },
    ]

    result = compute_realized_pnl(trades)

    assert result["total"] == 120
    assert result["wins"] == 1
    assert result["losses"] == 0
    assert result["closed"][0]["entry_price_usd"] == 100
    assert result["closed"][0]["pnl_ils"] == 120
    assert result["closed"][0]["pnl_pct"] == 12
    assert result["closed"][0]["r_multiple"] == 2.4
    assert result["trades"][1]["pnl_ils"] == 120


def test_compact_dashboard_payload_trims_heavy_collections_and_keeps_totals() -> None:
    dashboard = {
        "status": "ok",
        "latest_run": {"summary_text": "x" * 30_000},
        "latest_setups": [{"ticker": f"A{index}"} for index in range(25)],
        "latest_decisions": [{"ticker": "heavy"}],
        "recent_trades": [{"ticker": f"T{index}"} for index in range(18)],
        "closed_trades": [{"ticker": f"C{index}"} for index in range(7)],
    }

    compact = compact_agent_dashboard_payload(dashboard, action_limit=10, trade_limit=5)

    assert compact["payload"]["mode"] == "compact"
    assert len(compact["latest_setups"]) == 10
    assert compact["latest_decisions"] == []
    assert len(compact["recent_trades"]) == 5
    assert compact["recent_trades"][0]["ticker"] == "T17"
    assert compact["pagination"]["actions"]["total"] == 25
    assert compact["pagination"]["actions"]["has_more"] is True
    assert compact["pagination"]["trades"]["closed_total"] == 7
    assert "trimmed" in compact["latest_run"]["summary_text"].lower()


def test_dashboard_section_payload_paginates_actions_and_trades() -> None:
    dashboard = {
        "status": "ok",
        "latest_setups": [{"ticker": f"A{index}"} for index in range(15)],
        "recent_trades": [{"ticker": f"T{index}"} for index in range(12)],
    }

    actions = dashboard_section_payload(dashboard, section="actions", offset=10, limit=10)
    trades = dashboard_section_payload(dashboard, section="trades", offset=0, limit=3)

    assert [item["ticker"] for item in actions["items"]] == ["A10", "A11", "A12", "A13", "A14"]
    assert actions["has_more"] is False
    assert [item["ticker"] for item in trades["items"]] == ["T11", "T10", "T9"]
    assert trades["has_more"] is True
