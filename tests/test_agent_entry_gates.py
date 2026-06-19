from types import SimpleNamespace

import pandas as pd

from agent.market_lens_ui_agent import is_auth_failure
from app.agent_risk import (
    AgentRiskConfig,
    AgentRunRiskContext,
    CandidateMarketSnapshot,
    MarketRegime,
    buy_blockers,
    calculate_entry_confirmation,
    cooldown_check,
    validate_targets,
)


def config() -> AgentRiskConfig:
    return AgentRiskConfig(
        starting_capital=100_000,
        default_max_total_exposure=40_000,
        max_position=10_000,
    )


def context(label: str = "BULL") -> AgentRunRiskContext:
    cfg = config()
    if label == "BULL":
        regime = MarketRegime(label, 0.82, 40_000, 2.0, cfg.bull_min_setup_score, "bull")
    elif label == "NEUTRAL":
        regime = MarketRegime(label, 0.52, 20_000, 2.5, cfg.neutral_min_setup_score, "neutral")
    else:
        regime = MarketRegime(label, 0.22, 0, 999.0, 1.0, "bear")
    return AgentRunRiskContext(config=cfg, market_regime=regime, sector_health={})


def result(score: float = 0.60, setup_type: str = "Breakout + Retest") -> SimpleNamespace:
    return SimpleNamespace(
        ticker="TEST",
        score=score,
        setup_type=setup_type,
        current_price=100.0,
        buy_zone_low=98.0,
        buy_zone_high=100.0,
        stop_loss=95.0,
        target_1=105.0,
        target_2=115.0,
    )


def blockers_for(
    *,
    score: float = 0.60,
    regime: str = "BULL",
    net_rr_1: float = 1.20,
    net_rr: float = 2.50,
    target_status: str = "OK",
    confirmation_passed: bool = True,
    cooldown_active: bool = False,
) -> list[dict[str, str]]:
    run_context = context(regime)
    confirmation = {
        "entry_confirmation_passed": confirmation_passed,
        "confirmation_reason": "test confirmation",
    }
    cooldown = {
        "cooldown_active": cooldown_active,
        "cooldown_exception_used": False,
        "cooldown_reason": "Stop-loss cooldown active.",
    }
    return buy_blockers(
        result=result(score),
        run_context=run_context,
        sector_info={"regime": "STRONG", "etf": "XLK", "score": 80},
        net_rr_info={"net_rr": net_rr, "net_rr_1": net_rr_1, "net_rr_2": 6.0},
        earnings_info={"earnings_blackout": False},
        target_info={"status": target_status},
        confirmation=confirmation,
        cooldown=cooldown,
        sector_exposure={"limit_exceeded": False},
        factor_exposure={"limit_exceeded": False},
        correlation={"correlation_warning": False},
        normalized_quality_score=80,
        portfolio_exposure_after=5_000,
        sizing={"blocked": False, "reason": ""},
        minimum_net_rr=run_context.market_regime.minimum_net_rr,
    )


def reasons(blockers: list[dict[str, str]]) -> str:
    return " ".join(item["reason"] for item in blockers)


def test_bull_setup_score_below_floor_must_not_buy() -> None:
    assert "BULL market requires setup score" in reasons(blockers_for(score=0.39, regime="BULL"))


def test_neutral_setup_score_below_floor_must_not_buy() -> None:
    assert "NEUTRAL market requires setup score" in reasons(blockers_for(score=0.49, regime="NEUTRAL", net_rr=3.0))


def test_bear_blocks_all_new_buys() -> None:
    blockers = blockers_for(score=0.90, regime="BEAR", net_rr=999.0)
    assert any(item["action"] == "SKIP" for item in blockers)


def test_primary_net_rr_below_threshold_blocks_even_when_target2_is_strong() -> None:
    text = reasons(blockers_for(net_rr_1=0.79, net_rr=3.0))
    assert "Target 2 cannot justify the entry alone" in text


def test_target1_too_close_blocks_buy() -> None:
    assert "Target 1 is too close" in reasons(blockers_for(target_status="LOW_REWARD_DISTANCE"))


def test_missing_entry_confirmation_blocks_buy() -> None:
    assert "Entry confirmation failed" in reasons(blockers_for(confirmation_passed=False))


def test_stop_loss_cooldown_blocks_reentry() -> None:
    assert "Stop-loss cooldown active" in reasons(blockers_for(cooldown_active=True))


def test_auth_failure_is_classified_without_fake_scan() -> None:
    assert is_auth_failure("Login did not complete. Status: Auth not configured.")


def test_entry_confirmation_uses_completed_candle_not_live_candle() -> None:
    daily = pd.DataFrame(
        [
            {"Open": 95, "High": 99, "Low": 94, "Close": 96, "Volume": 1000},
            {"Open": 98, "High": 103, "Low": 98, "Close": 102, "Volume": 1000},
            {"Open": 90, "High": 91, "Low": 89, "Close": 89, "Volume": 1000},
        ],
        index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
    )
    confirmation = calculate_entry_confirmation(
        result(),
        CandidateMarketSnapshot(ticker="TEST", daily=daily),
        config(),
    )
    assert confirmation["entry_confirmation_passed"] is True
    assert confirmation["confirmation_candle_timestamp"].startswith("2026-01-02")


def test_target1_atr_distance_uses_configured_threshold() -> None:
    close_enough = result()
    close_enough.target_1 = 103.0
    target = validate_targets(
        close_enough,
        CandidateMarketSnapshot(ticker="TEST", price=100, atr=5, daily=None),
        config(),
    )
    assert target["status"] == "LOW_REWARD_DISTANCE"


def test_cooldown_exception_requires_different_setup() -> None:
    strong = result(score=0.70, setup_type="Breakout + Retest")
    cooldown = cooldown_check(
        ticker="TEST",
        result=strong,
        net_rr_info={"net_rr_1": 1.30},
        confirmation={"entry_confirmation_passed": True},
        recent_stop_events={
            "TEST": {
                "last_stop_date": "2026-01-01T10:00:00",
                "days_remaining": 2,
                "setup_type": "Breakout + Retest",
            }
        },
        config=config(),
    )
    assert cooldown["cooldown_active"] is True
    assert cooldown["cooldown_exception_used"] is False
