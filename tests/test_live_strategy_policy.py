"""Retire live research without touching history or position execution."""
from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

from app.entry_policy import breakout_continuation_profile
from app.shadow_strategies import STRATEGY_NAMES, evaluate_shadow_strategies
from app.strategy import StrategyCandidate, StrategyDecision


def profile(**overrides):
    values = dict(
        setup_type="Breakout + Retest", market_regime="BULL", sector_regime="STRONG",
        session={"phase": "REGULAR", "can_open_new_buy": True},
        confirmation={"entry_confirmation_passed": True},
        freshness={"status": "FRESH_SAME_SESSION"}, net_rr_1=1.4,
        earnings_blackout=False, minimum_primary_net_rr=.8,
    )
    values.update(overrides)
    return breakout_continuation_profile(**values)


def test_shadow_is_retired_by_default_without_mutating_decisions(monkeypatch):
    import app.shadow_strategies as shadow

    monkeypatch.delenv("MARKET_LENS_SHADOW_STRATEGIES_ENABLED", raising=False)
    monkeypatch.setattr(shadow, "breakout_continuation", lambda *_: pytest.fail("Live shadow ran"))
    decision = {"final_action": "WATCH", "position_size": 0, "historical_id": "preserve"}
    before = deepcopy(decision)
    assert evaluate_shadow_strategies(SimpleNamespace(), decision) == []
    assert decision == before


def test_retired_registry_remains_available_for_offline_history(monkeypatch):
    monkeypatch.setenv("MARKET_LENS_SHADOW_STRATEGIES_ENABLED", "false")
    records = evaluate_shadow_strategies(SimpleNamespace(), {"final_action": "SKIP"}, enabled=True)
    assert [record["name"] for record in records] == list(STRATEGY_NAMES)
    assert all(not record["would_buy"] for record in records)


def test_promoted_breakout_is_additive_not_a_gate_bypass(monkeypatch):
    monkeypatch.setenv("MARKET_LENS_BREAKOUT_CONTINUATION_ENABLED", "true")
    saved = profile()
    assert saved["passed"] and saved["applicable"]
    assert saved["version"] == "active_v1"
    assert saved["source_setup_type"] == "Breakout + Retest"
    assert saved["replaces_existing_gates"] is False


@pytest.mark.parametrize("overrides,blocker", [
    ({"market_regime": "BEAR"}, "market_regime"),
    ({"market_regime": "UNKNOWN"}, "market_regime"),
    ({"sector_regime": "WEAK"}, "sector_regime"),
    ({"sector_regime": "UNKNOWN"}, "sector_regime"),
    ({"session": {"phase": "PRE_MARKET", "can_open_new_buy": True}}, "regular_session"),
    ({"session": {}}, "regular_session"),
    ({"confirmation": {}}, "completed_confirmation"),
    ({"confirmation": {"entry_confirmation_passed": False}}, "completed_confirmation"),
    ({"freshness": {"status": "STALE_INTRADAY"}}, "confirmation_freshness"),
    ({"freshness": {}}, "confirmation_freshness"),
    ({"net_rr_1": None}, "primary_net_rr"),
    ({"net_rr_1": float("nan")}, "primary_net_rr"),
    ({"net_rr_1": .79}, "primary_net_rr"),
    ({"net_rr_1": 1.1, "minimum_primary_net_rr": 1.2}, "primary_net_rr"),
    ({"earnings_blackout": True}, "earnings_blackout"),
])
def test_promoted_breakout_fails_closed(monkeypatch, overrides, blocker):
    monkeypatch.setenv("MARKET_LENS_BREAKOUT_CONTINUATION_ENABLED", "true")
    saved = profile(**overrides)
    assert saved["passed"] is False
    assert blocker in saved["blockers"]


def test_no_fib_or_pullback_alias_is_promoted(monkeypatch):
    monkeypatch.setenv("MARKET_LENS_BREAKOUT_CONTINUATION_ENABLED", "true")
    for name in ("Fib 61.8 Confluence Buy Zone", "FIB_STRUCTURE_STOP", "TREND_PULLBACK_RECLAIM",
                 "RELATIVE_STRENGTH_LEADER", "VWAP Reclaim Setup"):
        assert profile(setup_type=name)["applicable"] is False
    monkeypatch.setenv("MARKET_LENS_BREAKOUT_CONTINUATION_ENABLED", "false")
    assert profile()["applicable"] is False


def test_python_defaults_match_production_policy(monkeypatch):
    from app.entry_policy import fibonacci_entries_enabled

    monkeypatch.delenv("MARKET_LENS_FIBONACCI_ENTRIES_ENABLED", raising=False)
    monkeypatch.delenv("MARKET_LENS_BREAKOUT_CONTINUATION_ENABLED", raising=False)
    assert not fibonacci_entries_enabled()
    assert profile()["applicable"] and profile()["passed"]


@pytest.mark.parametrize("phase", ["PRE_MARKET", "AFTER_HOURS", "CLOSED", "WEEKEND", "HOLIDAY"])
def test_off_hours_timing_only_failure_keeps_staging_not_entry(phase):
    saved = profile(session={"phase": phase, "can_open_new_buy": False},
                    freshness={"status": "OFF_HOURS_REFERENCE"})
    assert not saved["passed"] and saved["staging_only"]
    assert profile(freshness={"status": "STALE_INTRADAY"})["staging_only"] is False
    assert profile(session={"phase": "CLOSED", "can_open_new_buy": False},
                   net_rr_1=.2)["staging_only"] is False


def detection_args():
    from app.models import VolumeProfile

    return dict(ticker="TEST", current_price=100, atr=2, vwap=99,
                volume_profile=VolumeProfile(poc=99, vah=104, val=95),
                fib_info=object(), hourly_closes=pd.Series([98., 99., 100.]),
                hourly_lows=pd.Series([97., 98., 99.]),
                hourly_volume=pd.Series([1000., 1100., 1200.]), ema_20=98, vsl=None)


def test_fib_disabled_removes_all_cross_strategy_fib_inputs(monkeypatch):
    import app.setups as setups

    monkeypatch.setenv("MARKET_LENS_FIBONACCI_ENTRIES_ENABLED", "false")
    monkeypatch.setattr(setups, "_try_fib_confluence", lambda *_a, **_k: pytest.fail("Fib ran"))
    calls = []

    def detector(*args, **kwargs):
        calls.append(args[5])
        return None

    for name in ("_try_breakout_retest", "_try_swing_volume", "_try_liquidity_trap", "_try_vwap_reclaim"):
        monkeypatch.setattr(setups, name, detector)
    result = setups.detect_setup(**detection_args())
    assert calls == [None, None, None, None]
    assert result.setup_type == "No Trade" and result.fibonacci is None


def test_scanner_does_not_compute_fib_when_retired(monkeypatch):
    import app.scanner as scanner
    from app.models import ScanResult, VolumeProfile

    monkeypatch.setenv("MARKET_LENS_FIBONACCI_ENTRIES_ENABLED", "false")
    frame = pd.DataFrame(dict(Close=[100.]*220, High=[102.]*220, Low=[98.]*220, Volume=[1000.]*220),
                         index=pd.date_range("2026-01-01", periods=220, tz="UTC"))
    monkeypatch.setattr(scanner, "fetch_ticker", lambda *_a, **_k: SimpleNamespace(
        daily=frame, hourly=frame, weekly=frame))
    monkeypatch.setattr(scanner, "get_best_fib", lambda *_a, **_k: pytest.fail("Fib computed"))
    monkeypatch.setattr(scanner, "compute_volume_profile", lambda *_: VolumeProfile(poc=99, vah=104, val=95))
    monkeypatch.setattr(scanner, "filter_hvn", lambda vp, *_: vp)
    monkeypatch.setattr(scanner, "detect_swing_low_confluence", lambda **_: None)
    monkeypatch.setattr(scanner, "enrich_professional_context", lambda result, **_: result)
    monkeypatch.setattr(scanner, "enrich_setup_candidate_measurements", lambda result, **_: result)
    monkeypatch.setattr(scanner, "attach_extended_hours", lambda result, *_: result)
    received = []

    def detect(**kwargs):
        received.append(kwargs["fib_info"])
        return ScanResult(ticker="TEST", setup_type="No Trade", current_price=100, score=0,
                          buy_zone=(0, 0), stop_loss=0, target_1=0, target_2=0,
                          risk_reward=0, reason="No match")

    monkeypatch.setattr(scanner, "detect_setup", detect)
    assert scanner.scan_ticker_detail("TEST", spy_returns=pd.Series(dtype=float), benchmarks={}).result.fibonacci is None
    assert received == [None]


@pytest.mark.parametrize("name", ["Fib 61.8 Confluence Buy Zone", "FIB_STOP_075_ATR", "Fibonacci Pullback"])
def test_cached_fib_variants_cannot_enter_or_alert(monkeypatch, name):
    from app.candidate_selection import select_qualified_candidate

    monkeypatch.setenv("MARKET_LENS_FIBONACCI_ENTRIES_ENABLED", "false")
    monkeypatch.delenv("MARKET_LENS_DISABLED_ENTRY_SETUPS", raising=False)
    candidate = StrategyCandidate("TEST", name, .7, 100, 99, 101, 95, 110, 120, 3)
    decision = StrategyDecision("BUY_SIMULATED", "eligible", quantity=10, cash_out_ils=1000, risk_ils=50)
    evidence = dict(final_action="BUY_SIMULATED", position_size=10, risk_amount=50,
                    adjusted_cash_out=1000, entry_gate_blockers=[])
    _, blocked, saved = select_qualified_candidate(candidate, lambda _: (decision, evidence))
    assert blocked.action == saved["final_action"] == "SKIP"
    assert blocked.quantity == saved["position_size"] == 0
    assert saved["qualified_setup_alert"] is None


@pytest.mark.parametrize("action", ["HOLD", "TAKE_PARTIAL_PROFIT", "TAKE_PROFIT", "EXIT_STOP"])
def test_fib_retirement_never_changes_existing_position_lifecycle(monkeypatch, action):
    from app.candidate_selection import select_qualified_candidate

    monkeypatch.setenv("MARKET_LENS_FIBONACCI_ENTRIES_ENABLED", "false")
    candidate = StrategyCandidate("TEST", "Fib 61.8 Confluence Buy Zone", .7, 100, 99, 101, 95, 110, 120, 3)
    decision = StrategyDecision(action, "existing lifecycle", quantity=10, cash_out_ils=1000, risk_ils=50)
    saved = dict(final_action=action, position_size=10, risk_amount=50, stop_loss=100,
                 partial_taken=True, target_1=110, target_2=120, position_id="historical")
    before = deepcopy(saved)
    _, actual, evidence = select_qualified_candidate(candidate, lambda _: (decision, saved), existing=True)
    assert actual == decision
    for key, value in before.items():
        assert evidence[key] == value


def test_production_web_and_worker_share_approved_policy():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((root / "deploy/hetzner/compose.runtime.yaml").read_text())
    for name, default in (("SHADOW_STRATEGIES", "false"), ("FIBONACCI_ENTRIES", "false"),
                          ("BREAKOUT_CONTINUATION", "true")):
        key = f"MARKET_LENS_{name}_ENABLED"
        expected = "${" + key + ":-" + default + "}"
        assert compose["services"]["web"]["environment"][key] == expected
        assert compose["services"]["worker"]["environment"][key] == expected
