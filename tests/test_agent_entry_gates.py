import json
from types import SimpleNamespace

import pandas as pd

from agent.market_lens_ui_agent import (
    ChartRetentionSettings,
    Decision,
    SetupResult,
    build_agent_scan_tickers,
    decide,
    fetch_smart_universe_tickers,
    is_auth_failure,
    limited_carry_forward_tickers,
    parse_result,
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
    evaluate_agent_candidate,
    market_session_status,
    validate_targets,
)
from app.charts import _quote_line_for_chart
from app.data import DATA_CACHE_TTL_SECONDS, EXTENDED_HOURS_CACHE_TTL_SECONDS, _frame_cache_ttl
from app.models import ExtendedHoursInfo, ScanResult
from app.scanner import calculate_extended_hours_impact
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


def test_agent_universe_overfetches_to_replace_excluded_tickers(monkeypatch) -> None:
    calls: list[int] = []

    def fake_fetch(_settings: SimpleNamespace, limit: int) -> list[str]:
        calls.append(limit)
        return [f"S{i}" for i in range(1, 31)]

    monkeypatch.setenv("MARKET_LENS_AGENT_UNIVERSE_TARGET", "5")
    monkeypatch.setenv("MARKET_LENS_AGENT_UNIVERSE_POOL", "5")
    monkeypatch.setenv("MARKET_LENS_AGENT_UNIVERSE_MAX_POOL", "20")
    monkeypatch.setattr(
        "agent.market_lens_ui_agent.fetch_smart_universe_tickers",
        fake_fetch,
    )

    settings = SimpleNamespace(universe="smart-universe", analysis_period="6mo", url="https://example.test")
    selected = build_agent_scan_tickers(
        settings,
        carry_forward_tickers=["S1", "S2"],
        skipped_tickers=["S3", "S4", "S5"],
    )

    assert calls == [10]
    assert selected == ["S6", "S7", "S8", "S9", "S10", "S1", "S2"]


def test_agent_universe_uses_recent_skip_as_fallback_when_fresh_pool_is_short(monkeypatch) -> None:
    def fake_fetch(_settings: SimpleNamespace, _limit: int) -> list[str]:
        return [f"S{i}" for i in range(1, 9)]

    monkeypatch.setenv("MARKET_LENS_AGENT_UNIVERSE_TARGET", "5")
    monkeypatch.setenv("MARKET_LENS_AGENT_UNIVERSE_POOL", "5")
    monkeypatch.setenv("MARKET_LENS_AGENT_UNIVERSE_MAX_POOL", "8")
    monkeypatch.setenv("MARKET_LENS_AGENT_RECENT_SKIP_FALLBACK", "true")
    monkeypatch.setattr(
        "agent.market_lens_ui_agent.fetch_smart_universe_tickers",
        fake_fetch,
    )

    settings = SimpleNamespace(
        universe="smart-universe",
        analysis_period="6mo",
        url="https://example.test",
    )
    selected = build_agent_scan_tickers(
        settings,
        carry_forward_tickers=["S1"],
        skipped_tickers=["S2", "S3", "S4", "S5", "S6", "S7", "S8"],
    )

    assert selected == ["S2", "S3", "S4", "S5", "S6", "S1"]


def test_smart_universe_fetch_prefers_full_companies_payload(monkeypatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            payload = {
                "companies": [{"ticker": f"C{i}"} for i in range(1, 121)],
                "ranked": [{"ticker": f"R{i}"} for i in range(1, 4)],
            }
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(
        "agent.market_lens_ui_agent.urlopen",
        lambda *_args, **_kwargs: FakeResponse(),
    )

    settings = SimpleNamespace(url="https://example.test", analysis_period="6mo")
    selected = fetch_smart_universe_tickers(settings, 120)

    assert selected[:3] == ["C1", "C2", "C3"]
    assert "C120" in selected


def test_smart_universe_fetch_uses_local_fallback_on_api_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.market_lens_ui_agent.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("502")),
    )
    monkeypatch.setattr(
        "agent.market_lens_ui_agent.local_smart_universe_tickers",
        lambda _settings, limit: [f"LOCAL{limit}"],
    )

    settings = SimpleNamespace(url="https://example.test", analysis_period="6mo")

    assert fetch_smart_universe_tickers(settings, 50) == ["LOCAL50"]


def test_carry_forward_limit_keeps_open_positions_and_caps_watch(monkeypatch) -> None:
    monkeypatch.setenv("MARKET_LENS_AGENT_CARRY_FORWARD_LIMIT", "4")

    selected = limited_carry_forward_tickers(
        open_tickers=["OPEN1", "OPEN2"],
        watch_tickers=["WATCH1", "WATCH2", "WATCH3", "WATCH4"],
    )

    assert selected == ["OPEN1", "OPEN2", "WATCH1", "WATCH2"]


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
        market_session={
            "phase": "REGULAR",
            "timestamp": "2026-06-18T10:30-04:00",
            "can_open_new_buy": True,
            "regular_session_open": True,
            "reason": "Regular market session is open.",
        },
    )


def reasons(blockers: list[dict[str, str]]) -> str:
    return " ".join(item["reason"] for item in blockers)


def test_bull_setup_score_below_floor_must_not_buy() -> None:
    assert "BULL market requires setup score" in reasons(blockers_for(score=0.44, regime="BULL"))


def test_neutral_setup_score_below_floor_must_not_buy() -> None:
    assert "NEUTRAL market requires setup score" in reasons(blockers_for(score=0.54, regime="NEUTRAL", net_rr=3.0))


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


def test_off_hours_candidate_is_staged_not_bought() -> None:
    run_context = context("BULL")
    blockers = buy_blockers(
        result=result(score=0.70),
        run_context=run_context,
        sector_info={"regime": "STRONG", "etf": "XLK", "score": 80},
        net_rr_info={"net_rr": 2.50, "net_rr_1": 1.20, "net_rr_2": 6.0},
        earnings_info={"earnings_blackout": False},
        target_info={"status": "OK"},
        confirmation={"entry_confirmation_passed": True, "confirmation_reason": "confirmed"},
        cooldown={"cooldown_active": False, "cooldown_exception_used": False},
        sector_exposure={"limit_exceeded": False},
        factor_exposure={"limit_exceeded": False},
        correlation={"correlation_warning": False},
        normalized_quality_score=80,
        portfolio_exposure_after=5_000,
        sizing={"blocked": False, "reason": ""},
        minimum_net_rr=run_context.market_regime.minimum_net_rr,
        market_session={
            "phase": "PRE_MARKET",
            "timestamp": "2026-06-18T08:30-04:00",
            "can_open_new_buy": False,
            "regular_session_open": False,
            "reason": "Outside regular market hours.",
        },
    )
    assert blockers[0]["action"] == "WATCH_READY"
    assert "outside regular market hours" in blockers[0]["reason"].lower()


def test_market_session_regular_allows_new_buy() -> None:
    session = market_session_status(
        now=pd.Timestamp("2026-06-18T10:30:00-04:00").to_pydatetime(),
    )
    assert session["phase"] == "REGULAR"
    assert session["can_open_new_buy"] is True


def test_market_session_weekend_stages_new_buy() -> None:
    session = market_session_status(
        now=pd.Timestamp("2026-06-20T10:30:00-04:00").to_pydatetime(),
    )
    assert session["phase"] == "WEEKEND"
    assert session["can_open_new_buy"] is False


def test_off_hours_candidate_is_staged_and_requires_regular_confirmation(monkeypatch) -> None:
    _patch_risk_dependencies(monkeypatch, net_rr=2.20, confirmation_passed=True)
    monkeypatch.setattr(
        "app.agent_risk.market_session_status",
        lambda allow_off_hours_buys=False: {
            "phase": "AFTER_HOURS",
            "timestamp": "2026-07-08T18:30-04:00",
            "can_open_new_buy": False,
            "regular_session_open": False,
            "reason": "Outside regular market hours.",
        },
    )
    run_context = context("BULL")
    run_context.sector_health = {
        "Technology": {"label": "Strong", "score": 80, "etf": "XLK", "reason": "test strong sector"}
    }

    decision = evaluate_agent_candidate(
        timestamp="2026-07-08T18:30:00",
        result=result(score=0.60),
        initial_action="BUY_SIMULATED",
        initial_reason="base buy",
        quantity=100,
        cash_out=10_000,
        risk_amount=500,
        cash_available=100_000,
        portfolio_exposure_before=0,
        open_positions={},
        sector_map={"TEST": "Technology"},
        run_context=run_context,
        recent_stop_events={},
    )

    assert decision["final_action"] == "WATCH_READY"
    assert decision["position_size"] == 0
    assert decision["market_session_phase"] == "AFTER_HOURS"
    assert decision["off_hours_entry_policy"] == "STAGE_ONLY"
    assert decision["off_hours_candidate"] is True
    assert decision["regular_session_confirmation_required"] is True
    assert "blocked until a regular-session confirmation scan" in decision["off_hours_staging_reason"]


def test_stop_loss_cooldown_blocks_reentry() -> None:
    assert "Stop-loss cooldown active" in reasons(blockers_for(cooldown_active=True))


def _net_rr_payload(net_rr: float = 2.05) -> dict[str, object]:
    return {
        "gross_rr": 2.40,
        "gross_rr_1": 1.80,
        "gross_rr_2": 4.80,
        "gross_rr_decision": 2.40,
        "theoretical_entry": 100.0,
        "executable_entry": 100.0,
        "gross_risk_per_share": 5.0,
        "gross_reward_1_per_share": 9.0,
        "gross_reward_2_per_share": 24.0,
        "estimated_spread": 0.05,
        "estimated_slippage": 0.10,
        "estimated_fees": 0.0,
        "spread_source": "test",
        "execution_liquidity_bucket": "large_liquid",
        "net_entry": 100.1,
        "net_stop_assumption": 94.9,
        "net_target_1_assumption": 109.0,
        "net_target_2_assumption": 124.0,
        "net_risk_per_share": 5.2,
        "net_reward_1_per_share": 8.9,
        "net_reward_2_per_share": 23.9,
        "net_rr_1": 1.71,
        "net_rr_2": 4.59,
        "net_rr": net_rr,
        "warnings": [],
    }


def _patch_risk_dependencies(monkeypatch, *, net_rr: float = 2.05, confirmation_passed: bool = True) -> None:
    monkeypatch.setattr(
        "app.agent_risk.build_candidate_snapshot",
        lambda ticker, price, config: CandidateMarketSnapshot(
            ticker=ticker,
            price=price,
            atr=2.0,
            atr_pct=2.0,
            avg_dollar_volume=600_000_000,
            market_cap=50_000_000_000,
            market_cap_bucket="Large Cap",
            normalized_momentum_score=80,
            normalized_atr_score=70,
            normalized_liquidity_score=85,
        ),
    )
    monkeypatch.setattr("app.agent_risk.calculate_net_rr", lambda result, snapshot, config: _net_rr_payload(net_rr))
    monkeypatch.setattr(
        "app.agent_risk.calculate_earnings_blackout",
        lambda ticker, config: {
            "earnings_date": "",
            "days_to_earnings": None,
            "earnings_blackout": False,
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        "app.agent_risk.validate_targets",
        lambda result, snapshot, config: {
            "target_1_atr_distance": 2.0,
            "target_2_atr_distance": 4.0,
            "status": "OK",
            "market_structure_status": "OK",
            "previous_resistance": 0.0,
            "prior_high_20": 0.0,
            "prior_high_63": 0.0,
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        "app.agent_risk.calculate_entry_confirmation",
        lambda result, snapshot, config: {
            "confirmation_status": "PASSED" if confirmation_passed else "FAILED",
            "confirmation_reason": "test confirmation",
            "trigger_level": 100.0,
            "close_above_trigger": confirmation_passed,
            "vwap_reclaimed": False,
            "retest_held": confirmation_passed,
            "entry_confirmation_passed": confirmation_passed,
            "confirmation_timeframe": "30m_completed",
            "confirmation_candle_timestamp": "2026-07-08T10:30:00-04:00",
            "confirmation_window_used": 1,
            "warnings": [] if confirmation_passed else ["test confirmation failed"],
        },
    )
    monkeypatch.setattr(
        "app.agent_risk.market_session_status",
        lambda allow_off_hours_buys=False: {
            "phase": "REGULAR",
            "timestamp": "2026-07-08T10:30-04:00",
            "can_open_new_buy": True,
            "regular_session_open": True,
            "reason": "Regular market session is open.",
        },
    )
    monkeypatch.setattr(
        "app.agent_risk.correlation_check",
        lambda **kwargs: {
            "correlation_warning": False,
            "highest_correlation_ticker": "",
            "highest_correlation_value": None,
            "warnings": [],
        },
    )


def test_neutral_pilot_can_buy_half_size_when_only_strict_neutral_score_blocks(monkeypatch) -> None:
    _patch_risk_dependencies(monkeypatch, net_rr=2.05, confirmation_passed=True)
    run_context = context("NEUTRAL")
    run_context.sector_health = {
        "Technology": {"label": "Strong", "score": 80, "etf": "XLK", "reason": "test strong sector"}
    }

    decision = evaluate_agent_candidate(
        timestamp="2026-07-08T10:30:00",
        result=result(score=0.49),
        initial_action="BUY_SIMULATED",
        initial_reason="base buy",
        quantity=100,
        cash_out=10_000,
        risk_amount=500,
        cash_available=100_000,
        portfolio_exposure_before=0,
        open_positions={},
        sector_map={"TEST": "Technology"},
        run_context=run_context,
        recent_stop_events={},
        neutral_pilot_trades_today=0,
    )

    assert decision["final_action"] == "BUY_SIMULATED"
    assert decision["entry_mode"] == "neutral_pilot"
    assert decision["position_size"] == 50
    assert decision["adjusted_cash_out"] == 5_000
    assert decision["minimum_setup_score_required"] == 0.45
    assert decision["minimum_net_rr_required"] == 2.0


def test_neutral_pilot_daily_limit_blocks_second_pilot_trade(monkeypatch) -> None:
    _patch_risk_dependencies(monkeypatch, net_rr=2.05, confirmation_passed=True)
    run_context = context("NEUTRAL")
    run_context.sector_health = {
        "Technology": {"label": "Strong", "score": 80, "etf": "XLK", "reason": "test strong sector"}
    }

    decision = evaluate_agent_candidate(
        timestamp="2026-07-08T10:30:00",
        result=result(score=0.49),
        initial_action="BUY_SIMULATED",
        initial_reason="base buy",
        quantity=100,
        cash_out=10_000,
        risk_amount=500,
        cash_available=100_000,
        portfolio_exposure_before=0,
        open_positions={},
        sector_map={"TEST": "Technology"},
        run_context=run_context,
        recent_stop_events={},
        neutral_pilot_trades_today=1,
    )

    assert decision["final_action"] == "WATCH"
    assert decision["entry_mode"] == "standard"
    assert decision["position_size"] == 0
    assert "daily limit reached" in " ".join(decision["warnings"])


def test_auth_failure_is_classified_without_fake_scan() -> None:
    assert is_auth_failure("Login did not complete. Status: Auth not configured.")


def test_agent_parse_result_accepts_setup_price_label() -> None:
    parsed = parse_result(
        {
            "ticker": "TEST",
            "setup_type": "Breakout + Retest",
            "score": "0.62",
            "setup_price": "101.25",
            "buy_zone": "100.00 - 102.00",
            "stop_loss": "95.00",
            "targets": "110.00 / 120.00",
            "risk_reward": "2.40x",
            "reason": "test",
            "raw_text": "test",
        }
    )
    assert parsed.current_price == 101.25


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


def test_entry_confirmation_prefers_completed_intraday_candle_not_live_candle() -> None:
    intraday = pd.DataFrame(
        [
            {"Open": 97, "High": 99, "Low": 96, "Close": 98, "Volume": 1000},
            {"Open": 99, "High": 104, "Low": 99, "Close": 102, "Volume": 1200},
            {"Open": 90, "High": 91, "Low": 89, "Close": 89, "Volume": 2000},
        ],
        index=pd.to_datetime(
            [
                "2026-01-03 10:00:00-05:00",
                "2026-01-03 10:30:00-05:00",
                "2026-01-03 11:00:00-05:00",
            ]
        ),
    )

    confirmation = calculate_entry_confirmation(
        result(),
        CandidateMarketSnapshot(ticker="TEST", intraday=intraday),
        config(),
    )

    assert confirmation["entry_confirmation_passed"] is True
    assert confirmation["confirmation_timeframe"] == "30m_completed"
    assert "10:30:00" in confirmation["confirmation_candle_timestamp"]


def test_entry_confirmation_falls_back_to_daily_when_intraday_unavailable() -> None:
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
    assert confirmation["confirmation_timeframe"] == "1d_completed"


def test_fib_entry_confirmation_blocks_weak_inside_zone_candle() -> None:
    intraday = pd.DataFrame(
        [
            {"Open": 97.0, "High": 99.0, "Low": 96.0, "Close": 98.0, "Volume": 1000},
            {"Open": 98.3, "High": 100.4, "Low": 98.2, "Close": 98.8, "Volume": 1200},
            {"Open": 101.0, "High": 102.0, "Low": 100.0, "Close": 101.5, "Volume": 2000},
        ],
        index=pd.to_datetime(
            [
                "2026-01-03 10:00:00-05:00",
                "2026-01-03 10:30:00-05:00",
                "2026-01-03 11:00:00-05:00",
            ]
        ),
    )

    confirmation = calculate_entry_confirmation(
        result(setup_type="Fib 61.8 Confluence Buy Zone"),
        CandidateMarketSnapshot(ticker="TEST", intraday=intraday),
        config(),
    )

    assert confirmation["entry_confirmation_passed"] is False
    assert "strong bullish reclaim" in confirmation["confirmation_reason"]


def test_fib_entry_confirmation_accepts_clear_zone_reclaim() -> None:
    intraday = pd.DataFrame(
        [
            {"Open": 97.0, "High": 99.0, "Low": 96.0, "Close": 98.0, "Volume": 1000},
            {"Open": 98.3, "High": 100.5, "Low": 98.2, "Close": 100.2, "Volume": 1200},
            {"Open": 99.0, "High": 99.5, "Low": 98.5, "Close": 99.1, "Volume": 2000},
        ],
        index=pd.to_datetime(
            [
                "2026-01-03 10:00:00-05:00",
                "2026-01-03 10:30:00-05:00",
                "2026-01-03 11:00:00-05:00",
            ]
        ),
    )

    confirmation = calculate_entry_confirmation(
        result(setup_type="Fib 61.8 Confluence Buy Zone"),
        CandidateMarketSnapshot(ticker="TEST", intraday=intraday),
        config(),
    )

    assert confirmation["entry_confirmation_passed"] is True
    assert "buy zone" in confirmation["confirmation_reason"]


def test_entry_confirmation_uses_recent_completed_window_when_latest_stays_relevant() -> None:
    intraday = pd.DataFrame(
        [
            {"Open": 97.0, "High": 99.0, "Low": 96.0, "Close": 98.0, "Volume": 1000},
            {"Open": 98.5, "High": 100.8, "Low": 98.3, "Close": 100.3, "Volume": 1400},
            {"Open": 100.5, "High": 100.6, "Low": 99.2, "Close": 99.4, "Volume": 1200},
            {"Open": 90.0, "High": 91.0, "Low": 89.0, "Close": 89.5, "Volume": 2000},
        ],
        index=pd.to_datetime(
            [
                "2026-01-03 10:00:00-05:00",
                "2026-01-03 10:30:00-05:00",
                "2026-01-03 11:00:00-05:00",
                "2026-01-03 11:30:00-05:00",
            ]
        ),
    )

    confirmation = calculate_entry_confirmation(
        result(setup_type="Fib 61.8 Confluence Buy Zone"),
        CandidateMarketSnapshot(ticker="TEST", intraday=intraday),
        config(),
    )

    assert confirmation["entry_confirmation_passed"] is True
    assert confirmation["confirmation_window_used"] == 2
    assert "10:30:00" in confirmation["confirmation_candle_timestamp"]


def test_entry_confirmation_window_rejects_stale_confirmation_after_zone_break() -> None:
    intraday = pd.DataFrame(
        [
            {"Open": 97.0, "High": 99.0, "Low": 96.0, "Close": 98.0, "Volume": 1000},
            {"Open": 98.5, "High": 100.8, "Low": 98.3, "Close": 100.3, "Volume": 1400},
            {"Open": 100.5, "High": 100.6, "Low": 96.8, "Close": 97.4, "Volume": 1200},
            {"Open": 90.0, "High": 91.0, "Low": 89.0, "Close": 89.5, "Volume": 2000},
        ],
        index=pd.to_datetime(
            [
                "2026-01-03 10:00:00-05:00",
                "2026-01-03 10:30:00-05:00",
                "2026-01-03 11:00:00-05:00",
                "2026-01-03 11:30:00-05:00",
            ]
        ),
    )

    confirmation = calculate_entry_confirmation(
        result(setup_type="Fib 61.8 Confluence Buy Zone"),
        CandidateMarketSnapshot(ticker="TEST", intraday=intraday),
        config(),
    )

    assert confirmation["entry_confirmation_passed"] is False
    assert "last 2 candles" in " ".join(confirmation["warnings"])


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


def test_scan_chart_marks_premarket_quote_separately() -> None:
    setup = SimpleNamespace(
        current_price=100.0,
        extended_hours=SimpleNamespace(phase="PRE_MARKET", label="Pre-market", price=101.0),
    )
    assert _quote_line_for_chart(setup) == ("Pre-market quote", 101.0)


def test_extended_hours_quote_cache_uses_short_ttl() -> None:
    assert _frame_cache_ttl("1m", True) == EXTENDED_HOURS_CACHE_TTL_SECONDS
    assert EXTENDED_HOURS_CACHE_TTL_SECONDS < DATA_CACHE_TTL_SECONDS
    assert _frame_cache_ttl("1d", False) == DATA_CACHE_TTL_SECONDS


def scan_result_with_extended(price: float, setup_type: str = "Breakout + Retest") -> ScanResult:
    return ScanResult(
        ticker="TEST",
        setup_type=setup_type,
        score=0.50 if setup_type != "No Trade" else 0,
        current_price=100.0,
        buy_zone=(98.0, 102.0) if setup_type != "No Trade" else (0.0, 0.0),
        stop_loss=95.0 if setup_type != "No Trade" else 0.0,
        target_1=110.0 if setup_type != "No Trade" else 0.0,
        target_2=120.0 if setup_type != "No Trade" else 0.0,
        risk_reward=2.0 if setup_type != "No Trade" else 0.0,
        reason="test",
        extended_hours=ExtendedHoursInfo(
            phase="PRE_MARKET",
            label="Pre-market",
            price=price,
            regular_close=100.0,
            is_extended=True,
        ),
    )


def test_extended_hours_impact_marks_inside_buy_zone_without_entry() -> None:
    impact = calculate_extended_hours_impact(scan_result_with_extended(101.0))
    assert impact["status"] == "INSIDE_BUY_ZONE"
    assert impact["inside_buy_zone"] is True
    assert impact["regular_confirmation_required"] is True
    assert impact["informational_only"] is True
    assert impact["extended_weighted_rr"] > 0


def test_extended_hours_impact_marks_stop_touch_as_invalidated() -> None:
    impact = calculate_extended_hours_impact(scan_result_with_extended(94.5))
    assert impact["status"] == "SETUP_INVALIDATED_BY_EXTENDED"
    assert impact["stop_touched_by_extended"] is True


def test_extended_hours_impact_keeps_no_trade_informational() -> None:
    impact = calculate_extended_hours_impact(scan_result_with_extended(101.0, setup_type="No Trade"))
    assert impact["status"] == "NO_ACTIVE_SETUP"
    assert impact["regular_confirmation_required"] is True


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
