import copy

import pandas as pd
import pytest

from app import setups
from app.models import ScanResult, VolumeProfile
from app.performance_summary import setup_candidate_metrics
from app.strategy import normalize_strategy_candidate
from agent.market_lens_ui_agent import parse_result


def candidate(name, score):
    return ScanResult(ticker="TEST", setup_type=name, score=score, current_price=100,
                      buy_zone=(99, 101), stop_loss=95, target_1=110, target_2=120,
                      risk_reward=2.5, reason="isolated candidate")


def test_candidate_policy_metrics_follow_saved_decisions():
    rows = [{"active_setup_selection_policy": "QUALIFIED_PROFESSIONAL_SCORE_V1",
             "setup_type": "Breakout + Retest", "setup_candidates": [{"setup_type": "Breakout + Retest"}]}]
    original = copy.deepcopy(rows)
    metrics = setup_candidate_metrics(rows)
    assert metrics["active_policy"] == "QUALIFIED_PROFESSIONAL_SCORE_V1"
    assert metrics["active_policy_counts"] == {"QUALIFIED_PROFESSIONAL_SCORE_V1": 1}
    assert metrics["shadow_only"] is False
    assert metrics["comparison_shadow_only"] is True
    assert rows == original


def test_missing_and_mixed_policy_are_not_mislabeled_legacy():
    assert setup_candidate_metrics([{}])["active_policy"] == "UNKNOWN"
    assert setup_candidate_metrics([{}])["shadow_only"] is None
    rows = [{"active_setup_selection_policy": "FIRST_MATCH_LEGACY"},
            {"active_setup_selection_policy": "QUALIFIED_PROFESSIONAL_SCORE_V1"}]
    assert setup_candidate_metrics(rows)["active_policy"] == "MIXED"
    assert setup_candidate_metrics(rows)["shadow_only"] is None
    assert setup_candidate_metrics(rows[:1])["shadow_only"] is True


@pytest.mark.parametrize("survives", [True, False])
@pytest.mark.parametrize("detector", ["_try_breakout_retest", "_try_swing_volume",
                                     "_try_liquidity_trap", "_try_vwap_reclaim"])
def test_detector_failure_is_explicit_but_keeps_other_candidates(monkeypatch, survives, detector):
    def broken(*args, **kwargs):
        raise ValueError("sensitive provider text must not escape")
    names = ["_try_breakout_retest", "_try_swing_volume", "_try_liquidity_trap", "_try_vwap_reclaim"]
    for name in names:
        monkeypatch.setattr(setups, name, lambda *a, **kw: None)
    survivor = "_try_vwap_reclaim" if detector != "_try_vwap_reclaim" else "_try_breakout_retest"
    expected = setups.SETUP_VWAP_RECLAIM if survivor == "_try_vwap_reclaim" else setups.SETUP_BREAKOUT_RETEST
    monkeypatch.setattr(setups, survivor, lambda *a, **kw: candidate(expected, .58) if survives else None)
    monkeypatch.setattr(setups, detector, broken)
    result = setups.detect_setup(
        ticker="TEST", current_price=100, atr=2, vwap=99,
        volume_profile=VolumeProfile(poc=99, vah=104, val=95), fib_info=None,
        hourly_closes=pd.Series([98, 99, 100]), hourly_lows=pd.Series([97, 98, 99]),
        hourly_volume=pd.Series([1000, 1100, 1200]), ema_20=98, vsl=None)
    assert result.setup_type == (expected if survives else setups.SETUP_NO_TRADE)
    if survives:
        assert result.score == .58 and result.stop_loss == 95 and result.target_1 == 110
    assert result.detector_diagnostics == [{"detector": "broken", "status": "FAILED", "error_type": "ValueError"}]
    assert "sensitive" not in result.model_dump_json()
    assert normalize_strategy_candidate(result).detector_diagnostics == result.detector_diagnostics
    metrics = setup_candidate_metrics([result.model_dump()])
    assert metrics["partial_detection_count"] == 1
    assert metrics["detector_failure_counts"] == {"broken:ValueError": 1}
    parsed = parse_result({"ticker": "TEST", "detector_diagnostics": result.detector_diagnostics})
    assert parsed.detector_diagnostics == result.detector_diagnostics


def test_no_match_is_not_a_detector_failure():
    diagnostics = []
    assert setups._safe_setup_detector(lambda: None, _diagnostics=diagnostics) is None
    assert diagnostics == []
