from types import SimpleNamespace

import pandas as pd

from agent.market_lens_ui_agent import (
    ChartRetentionSettings,
    Decision,
    SetupResult,
    decide,
    is_auth_failure,
    select_chart_tickers,
)
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
from app.strategy import decide_strategy_candidate, normalize_strategy_candidate


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


def chart_candidate(
    ticker: str,
    action: str,
    *,
    score: float = 0.60,
    setup_type: str = "Breakout + Retest",
    sector_regime: str = "STRONG",
    net_rr: float = 2.0,
) -> tuple[SetupResult, Decision]:
    setup = SetupResult(
        ticker=ticker,
        setup_type=setup_type,
        score=score,
        current_price=100.0,
        buy_zone_low=98.0,
        buy_zone_high=100.0,
        stop_loss=95.0,
        target_1=105.0,
        target_2=115.0,
        risk_reward=2.0,
        reason="test",
        raw_text="test",
        chart_url=f"/charts/{ticker}.png",
    )
    decision = Decision(
        action=action,
        feedback="test",
        decision_json={
            "setup_type": setup_type,
            "setup_score": score,
            "sector_regime": sector_regime,
            "net_rr": net_rr,
            "final_display_score": score,
        },
    )
    return setup, decision


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


def test_chart_retention_keeps_actionable_and_limited_debug_charts() -> None:
    selected = select_chart_tickers(
        [
            chart_candidate("BUY", "BUY_SIMULATED", score=0.50),
            chart_candidate("READY", "WATCH_READY", score=0.45),
            chart_candidate("TOP1", "WATCH", score=0.70),
            chart_candidate("TOP2", "SKIP", score=0.65),
            chart_candidate("EXTRA", "WATCH", score=0.60),
            chart_candidate("LOW", "WATCH", score=0.20),
            chart_candidate("WEAK", "WATCH", score=0.80, sector_regime="WEAK"),
            chart_candidate("NOTRADE", "SKIP", score=0.90, setup_type="No Trade"),
            chart_candidate("BADRR", "WATCH", score=0.90, net_rr=0.30),
        ],
        settings=ChartRetentionSettings(
            save_rejected_charts=False,
            rejected_chart_limit=2,
            rejected_chart_min_score=0.40,
        ),
        open_position_tickers=set(),
    )
    assert selected == {"BUY", "READY", "TOP1", "TOP2"}


def test_chart_retention_keeps_open_position_chart() -> None:
    selected = select_chart_tickers(
        [chart_candidate("OPEN", "WATCH", score=0.10, setup_type="No Trade")],
        settings=ChartRetentionSettings(False, 0, 0.40),
        open_position_tickers={"OPEN"},
    )
    assert selected == {"OPEN"}


def test_agent_and_user_strategy_decision_share_same_helper() -> None:
    setup = SetupResult(
        ticker="TEST",
        setup_type="Breakout + Retest",
        score=0.70,
        current_price=100.0,
        buy_zone_low=98.0,
        buy_zone_high=101.0,
        stop_loss=95.0,
        target_1=107.0,
        target_2=115.0,
        risk_reward=2.20,
        reason="test",
        raw_text="test",
    )
    kwargs = {
        "open_positions": {},
        "cash": 100_000.0,
        "exposure": 0.0,
        "usd_ils": 1.0,
        "max_position": 10_000.0,
        "max_total_exposure": 40_000.0,
        "max_risk": 1_000.0,
        "min_rr": 2.0,
        "sector_map": {},
        "sector_health": {},
    }
    agent_decision = decide(setup, **kwargs)
    shared_decision = decide_strategy_candidate(
        normalize_strategy_candidate(setup),
        open_positions=kwargs["open_positions"],
        cash=kwargs["cash"],
        exposure=kwargs["exposure"],
        currency_rate=kwargs["usd_ils"],
        max_position=kwargs["max_position"],
        max_total_exposure=kwargs["max_total_exposure"],
        max_risk=kwargs["max_risk"],
        min_rr=kwargs["min_rr"],
        sector_map=kwargs["sector_map"],
        sector_health=kwargs["sector_health"],
    )
    assert agent_decision.action == shared_decision.action
    assert agent_decision.feedback == shared_decision.feedback
    assert agent_decision.quantity == shared_decision.quantity
