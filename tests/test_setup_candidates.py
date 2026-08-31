from __future__ import annotations

import pandas as pd

import app.setups as setups
from app.models import ScanResult, VolumeProfile


def candidate(setup_type: str, score: float) -> ScanResult:
    return ScanResult(
        ticker="TEST",
        setup_type=setup_type,
        score=score,
        current_price=100,
        buy_zone=(98, 101),
        stop_loss=95,
        target_1=106,
        target_2=112,
        risk_reward=2.4,
        risk_reward_primary=1.2,
        risk_reward_stretch=3.4,
        reason=f"{setup_type} matched",
    )


def test_all_matching_setups_are_logged_while_legacy_first_match_stays_active(monkeypatch) -> None:
    breakout = candidate(setups.SETUP_BREAKOUT_RETEST, 0.55)
    swing = candidate(setups.SETUP_SWING_VOLUME, 0.62)
    vwap = candidate(setups.SETUP_VWAP_RECLAIM, 0.58)
    monkeypatch.setattr(setups, "_try_breakout_retest", lambda *_args, **_kwargs: breakout)
    monkeypatch.setattr(setups, "_try_swing_volume", lambda *_args, **_kwargs: swing)
    monkeypatch.setattr(setups, "_try_liquidity_trap", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(setups, "_try_vwap_reclaim", lambda *_args, **_kwargs: vwap)

    result = setups.detect_setup(
        ticker="TEST",
        current_price=100,
        atr=2,
        vwap=99,
        volume_profile=VolumeProfile(poc=99, vah=104, val=95),
        fib_info=None,
        hourly_closes=pd.Series([98, 99, 100]),
        hourly_lows=pd.Series([97, 98, 99]),
        hourly_volume=pd.Series([1000, 1100, 1200]),
        ema_20=98,
        vsl=None,
    )

    assert result.setup_type == setups.SETUP_BREAKOUT_RETEST
    assert [item["setup_type"] for item in result.setup_candidates] == [
        setups.SETUP_BREAKOUT_RETEST,
        setups.SETUP_SWING_VOLUME,
        setups.SETUP_VWAP_RECLAIM,
    ]
    assert result.setup_candidates[0]["legacy_score"] == 0.55
    assert result.setup_candidates[1]["legacy_score"] == 0.62
    assert result.setup_candidates[1]["shadow_setup_normalized_score"] > result.setup_candidates[1]["legacy_score"]


def test_no_match_preserves_no_trade_behavior(monkeypatch) -> None:
    for name in ("_try_breakout_retest", "_try_swing_volume", "_try_liquidity_trap", "_try_vwap_reclaim"):
        monkeypatch.setattr(setups, name, lambda *_args, **_kwargs: None)

    result = setups.detect_setup(
        ticker="TEST",
        current_price=100,
        atr=2,
        vwap=99,
        volume_profile=VolumeProfile(poc=99, vah=104, val=95),
        fib_info=None,
        hourly_closes=pd.Series([98, 99, 100]),
        hourly_lows=pd.Series([97, 98, 99]),
        hourly_volume=pd.Series([1000, 1100, 1200]),
        ema_20=98,
        vsl=None,
    )

    assert result.setup_type == setups.SETUP_NO_TRADE
    assert result.setup_candidates == []


def test_later_detector_failure_does_not_discard_legacy_winner(monkeypatch) -> None:
    breakout = candidate(setups.SETUP_BREAKOUT_RETEST, 0.55)
    monkeypatch.setattr(setups, "_try_breakout_retest", lambda *_args, **_kwargs: breakout)
    monkeypatch.setattr(setups, "_try_swing_volume", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(setups, "_try_liquidity_trap", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        setups,
        "_try_vwap_reclaim",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("optional detector data unavailable")),
    )

    result = setups.detect_setup(
        ticker="TEST",
        current_price=100,
        atr=2,
        vwap=99,
        volume_profile=VolumeProfile(poc=99, vah=104, val=95),
        fib_info=None,
        hourly_closes=pd.Series([98, 99, 100]),
        hourly_lows=pd.Series([97, 98, 99]),
        hourly_volume=pd.Series([1000, 1100, 1200]),
        ema_20=98,
        vsl=None,
    )

    assert result.setup_type == setups.SETUP_BREAKOUT_RETEST
    assert [item["setup_type"] for item in result.setup_candidates] == [setups.SETUP_BREAKOUT_RETEST]
