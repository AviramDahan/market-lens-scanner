from __future__ import annotations

import json
from datetime import datetime

from openpyxl import Workbook

from agent import market_lens_ui_agent as agent


def make_settings(tmp_path):
    return agent.Settings(
        url="http://127.0.0.1:8000/?v=test",
        email="agent@example.com",
        password="secret",
        excel_path=tmp_path / "tracker.xlsx",
        universe="smart-universe",
        tickers=[],
        analysis_period="6mo",
        min_rr=2.0,
        headless=True,
        timeout_seconds=300,
    )


def test_agent_scan_selection_preserves_carry_forward_when_total_cap_applies(monkeypatch, tmp_path) -> None:
    settings = make_settings(tmp_path)
    candidates = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "WATCH1", "NEAR1", "SKIP1"]

    monkeypatch.setenv("MARKET_LENS_AGENT_UNIVERSE_TARGET", "5")
    monkeypatch.setenv("MARKET_LENS_AGENT_UNIVERSE_POOL", "9")
    monkeypatch.setenv("MARKET_LENS_AGENT_UNIVERSE_MAX_POOL", "9")
    monkeypatch.setenv("MARKET_LENS_AGENT_TOTAL_SCAN_LIMIT", "6")
    monkeypatch.setattr(agent, "fetch_smart_universe_tickers", lambda _settings, _limit: candidates)

    tickers = agent.build_agent_scan_tickers(
        settings,
        carry_forward_tickers=["WATCH1", "NEAR1"],
        skipped_tickers=["SKIP1", "NEAR1"],
    )

    assert len(tickers) == 6
    assert "WATCH1" in tickers
    assert "NEAR1" in tickers
    assert "SKIP1" not in tickers
    assert len(tickers) == len(set(tickers))


def test_off_hours_discovery_can_expand_fresh_target(monkeypatch, tmp_path) -> None:
    settings = make_settings(tmp_path)
    candidates = [f"T{i:03d}" for i in range(20)]

    monkeypatch.setenv("MARKET_LENS_AGENT_UNIVERSE_TARGET", "5")
    monkeypatch.setenv("MARKET_LENS_AGENT_UNIVERSE_POOL", "20")
    monkeypatch.setenv("MARKET_LENS_AGENT_UNIVERSE_MAX_POOL", "20")
    monkeypatch.setenv("MARKET_LENS_AGENT_TOTAL_SCAN_LIMIT", "0")
    monkeypatch.setenv("MARKET_LENS_AGENT_OFF_HOURS_DISCOVERY_ENABLED", "true")
    monkeypatch.setenv("MARKET_LENS_AGENT_OFF_HOURS_EXTRA_TARGET", "3")
    monkeypatch.setattr(agent, "agent_market_session", lambda: "overnight")
    monkeypatch.setattr(agent, "fetch_smart_universe_tickers", lambda _settings, _limit: candidates)

    tickers = agent.build_agent_scan_tickers(settings, carry_forward_tickers=[], skipped_tickers=[])

    assert tickers == candidates[:8]


def test_smart_universe_fetch_supplements_short_payload_from_curated_universe(monkeypatch, tmp_path) -> None:
    settings = make_settings(tmp_path)
    payload = {"companies": [{"ticker": "AAA"}, {"ticker": "BBB"}], "ranked": [{"ticker": "AAA"}]}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setenv("MARKET_LENS_AGENT_SUPPLEMENT_CURATED_UNIVERSE", "true")
    monkeypatch.setattr(agent, "urlopen", lambda *_args, **_kwargs: FakeResponse())
    monkeypatch.setattr(agent, "curated_universe", lambda: {"CCC": "Tech", "DDD": "Energy", "EEE": "Finance"})

    tickers = agent.fetch_smart_universe_tickers(settings, limit=5)

    assert tickers == ["AAA", "BBB", "CCC", "DDD", "EEE"]


def test_read_recent_near_miss_tickers_ignores_ordinary_skip_and_no_trade(monkeypatch, tmp_path) -> None:
    tracker = tmp_path / "tracker.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Setup Watchlist"
    ws.append([f"Col {index}" for index in range(1, 19)])
    timestamp = datetime.now().isoformat(timespec="seconds")

    def append_setup(ticker: str, setup_type: str, action: str, decision: dict) -> None:
        row = ["" for _ in range(18)]
        row[0] = timestamp
        row[1] = ticker
        row[2] = setup_type
        row[12] = action
        row[17] = json.dumps(decision)
        ws.append(row)

    append_setup("NEAR", "Breakout + Retest", "SKIP", {"setup_type": "Breakout + Retest", "setup_score": 0.36})
    append_setup("RRNEAR", "Fib 61.8", "SKIP", {"setup_type": "Fib 61.8", "setup_score": 0.20, "net_rr": 1.60})
    append_setup("FAR", "Fib 61.8", "SKIP", {"setup_type": "Fib 61.8", "setup_score": 0.20, "net_rr": 1.10})
    append_setup("NOTRADE", "No Trade", "SKIP", {"setup_type": "No Trade", "setup_score": 0.90, "net_rr": 4.0})
    append_setup("WATCH", "Breakout + Retest", "WATCH", {"setup_type": "Breakout + Retest", "setup_score": 0.80})
    wb.save(tracker)

    monkeypatch.setenv("MARKET_LENS_AGENT_NEAR_MISS_MIN_SETUP_SCORE", "0.35")
    monkeypatch.setenv("MARKET_LENS_AGENT_NEAR_MISS_MIN_NET_RR", "1.50")

    tickers = agent.read_recent_near_miss_tickers(tracker, days=5)

    assert tickers == ["NEAR", "RRNEAR"]
