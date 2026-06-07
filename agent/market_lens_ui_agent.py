from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from openpyxl import load_workbook
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from app.agent_risk import build_agent_run_context, evaluate_agent_candidate
from app.smart_universe import base_universe, build_sector_health


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = Path(os.getenv("MARKET_LENS_RUN_DIR", ROOT / "agent_runs"))
SCREENSHOT_DIR = RUN_DIR / "screenshots"
SUMMARY_DIR = RUN_DIR / "summaries"
CHART_DIR = RUN_DIR / "charts"
DECISION_DIR = RUN_DIR / "decisions"
APP_READY_SELECTOR = '[data-testid="auth-email"], #authStatus'


@dataclass
class Settings:
    url: str
    email: str
    password: str
    excel_path: Path
    universe: str
    tickers: list[str]
    analysis_period: str
    min_rr: float
    headless: bool
    timeout_seconds: int


@dataclass
class SetupResult:
    ticker: str
    setup_type: str
    score: float
    current_price: float
    buy_zone_low: float | None
    buy_zone_high: float | None
    stop_loss: float | None
    target_1: float | None
    target_2: float | None
    risk_reward: float
    reason: str
    raw_text: str
    chart_url: str = ""
    selection_context: str = ""


@dataclass
class Decision:
    action: str
    feedback: str
    quantity: int = 0
    cash_out_ils: float = 0.0
    cash_in_ils: float = 0.0
    risk_ils: float = 0.0
    decision_json: dict[str, Any] = field(default_factory=dict)


def main() -> None:
    settings = load_settings()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    log(f"Agent run started: {run_id}")
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    DECISION_DIR.mkdir(parents=True, exist_ok=True)
    screenshot_path = SCREENSHOT_DIR / f"market_lens_agent_{run_id}.png"
    summary_path = SUMMARY_DIR / f"market_lens_agent_{run_id}.md"
    decision_path = DECISION_DIR / f"market_lens_agent_{run_id}.jsonl"

    errors: list[str] = []
    results: list[SetupResult] = []
    decisions: dict[str, Decision] = {}
    login_status = "not_started"
    scan_status = "not_started"
    deadline = time.monotonic() + settings.timeout_seconds

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=settings.headless)
        page = browser.new_page(viewport={"width": 1440, "height": 1200})
        try:
            log("Opening Market Lens UI")
            open_app(page, settings.url, deadline)
            log("Logging in")
            login_status = login(page, settings, deadline)
            log(f"Login status: {login_status}")
            log("Configuring scan")
            configure_scan(page, settings, deadline)
            log("Running UI scan")
            results = run_scan(page, deadline)
            log(f"Scan completed: {len(results)} results")
            log("Persisting chart images")
            persist_chart_images(results, settings.url, run_id, deadline)
            scan_status = f"completed: {len(results)} results"
            log("Saving screenshot")
            page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception as exc:
            errors.append(str(exc))
            scan_status = "failed"
            log(f"Agent UI phase failed: {exc}")
            try:
                page.screenshot(path=str(screenshot_path), full_page=True)
            except Exception:
                pass
        finally:
            browser.close()

    log("Updating workbook")
    workbook_context = update_workbook(
        settings=settings,
        run_id=run_id,
        results=results,
        screenshot_path=screenshot_path,
        summary_path=summary_path,
        decision_path=decision_path,
        errors=errors,
    )
    decisions = workbook_context["decisions"]
    log("Writing summary")
    write_summary(
        settings=settings,
        run_id=run_id,
        login_status=login_status,
        scan_status=scan_status,
        results=results,
        decisions=decisions,
        screenshot_path=screenshot_path,
        summary_path=summary_path,
        workbook_context=workbook_context,
        errors=errors,
    )
    print(f"Agent run complete: {summary_path}")


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    url = os.getenv("MARKET_LENS_URL", "https://market-lens-scanner.onrender.com/?v=latest")
    email = required_env("MARKET_LENS_EMAIL")
    password = required_env("MARKET_LENS_PASSWORD")
    excel_path = Path(required_env("MARKET_LENS_EXCEL_PATH"))
    universe = os.getenv("MARKET_LENS_UNIVERSE", "smart-universe")
    tickers = parse_tickers(os.getenv("MARKET_LENS_TICKERS", ""))
    analysis_period = os.getenv("MARKET_LENS_ANALYSIS_PERIOD", "6mo")
    min_rr = float(os.getenv("MARKET_LENS_MIN_RR", "2"))
    headless = os.getenv("MARKET_LENS_HEADLESS", "true").lower() not in {"0", "false", "no"}
    timeout_seconds = int(os.getenv("MARKET_LENS_AGENT_TIMEOUT_SECONDS", "720"))
    return Settings(
        url,
        email,
        password,
        excel_path,
        universe,
        tickers,
        analysis_period,
        min_rr,
        headless,
        timeout_seconds,
    )


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def open_app(page: Page, url: str, run_deadline: float) -> None:
    deadline = min(time.monotonic() + 180, run_deadline)
    last_error = ""
    while time.monotonic() < deadline:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=remaining_ms(deadline, 90_000))
            page.wait_for_selector(APP_READY_SELECTOR, timeout=remaining_ms(deadline, 30_000))
            return
        except PlaywrightTimeoutError as exc:
            last_error = str(exc)
            page.wait_for_timeout(remaining_ms(deadline, 10_000))
    raise RuntimeError(f"Market Lens app did not become ready after Render wake-up wait. {last_error}")


def login(page: Page, settings: Settings, deadline: float) -> str:
    page.wait_for_selector(APP_READY_SELECTOR, timeout=remaining_ms(deadline, 30_000))
    page.wait_for_function(
        "() => Boolean(window.supabase) && document.querySelector('#authStatus')?.textContent?.trim().length > 0",
        timeout=remaining_ms(deadline, 60_000),
    )
    auth_status = page.locator("#authStatus").inner_text(timeout=remaining_ms(deadline, 10_000))
    if settings.email.lower() in auth_status.lower():
        return "already signed in"

    last_message = ""
    for attempt in range(1, 4):
        page.locator('[data-testid="auth-email"]').fill(settings.email)
        page.locator('[data-testid="auth-password"]').fill(settings.password)
        page.locator('[data-testid="sign-in-button"]').click()
        try:
            page.wait_for_function(
                "email => document.querySelector('#authStatus')?.textContent?.toLowerCase().includes(email.toLowerCase())",
                arg=settings.email,
                timeout=remaining_ms(deadline, 45_000),
            )
            return "signed in"
        except PlaywrightTimeoutError:
            last_message = page.locator("#message").inner_text(timeout=remaining_ms(deadline, 10_000))
            if attempt < 3:
                page.wait_for_timeout(remaining_ms(deadline, 5_000 * attempt))

    auth_status = page.locator("#authStatus").inner_text(timeout=remaining_ms(deadline, 10_000))
    raise RuntimeError(f"Login did not complete. Status: {auth_status}. Message: {last_message}")


def configure_scan(page: Page, settings: Settings, deadline: float) -> None:
    page.locator('[data-testid="min-rr-input"]').fill(str(settings.min_rr))
    page.locator('[data-testid="analysis-period-select"]').select_option(settings.analysis_period)
    open_tickers = read_open_position_tickers(settings.excel_path)
    if settings.tickers:
        page.locator('[data-testid="manual-ticker-input"]').fill(" ".join(unique_tickers(settings.tickers + open_tickers)))
        page.locator('[data-testid="add-manual-ticker"]').click()
        return
    page.locator('[data-testid="universe-select"]').select_option(settings.universe)
    page.wait_for_function(
        "() => !document.querySelector('[data-testid=\"select-all-tickers\"]')?.disabled",
        timeout=remaining_ms(deadline, 240_000),
    )
    page.locator('[data-testid="select-all-tickers"]').click()
    page.locator('[data-testid="add-selected-tickers"]').click()
    if open_tickers:
        page.locator('[data-testid="manual-ticker-input"]').fill(" ".join(open_tickers))
        page.locator('[data-testid="add-manual-ticker"]').click()


def run_scan(page: Page, deadline: float) -> list[SetupResult]:
    last_error = ""
    for attempt in range(1, 4):
        page.locator('[data-testid="scan-button"]').click()
        page.wait_for_function(
            "() => !document.querySelector('[data-testid=\"scan-button\"]')?.disabled",
            timeout=remaining_ms(deadline, 600_000),
        )
        if "failed" not in page.locator("#runMeta").inner_text(timeout=remaining_ms(deadline, 10_000)).lower():
            break

        last_error = page.locator("#message").inner_text(timeout=remaining_ms(deadline, 10_000))
        if attempt < 3 and is_transient_scan_error(last_error):
            page.wait_for_timeout(remaining_ms(deadline, 15_000 * attempt))
            continue
        raise RuntimeError(last_error)
    else:
        raise RuntimeError(last_error or "Scan failed")

    page.wait_for_selector('[data-testid="result-card"]', timeout=remaining_ms(deadline, 30_000))
    cards = page.locator('[data-testid="result-card"]')
    extracted: list[dict[str, Any]] = cards.evaluate_all(
        """cards => cards.map(card => {
            const stat = label => {
              const el = Array.from(card.querySelectorAll('[data-stat-label]'))
                .find(node => node.getAttribute('data-stat-label') === label);
              return el?.querySelector('strong')?.textContent?.trim() || '';
            };
            return {
              ticker: card.querySelector('[data-testid="result-ticker"]')?.textContent?.trim() || '',
              setup_type: card.querySelector('[data-testid="result-setup-type"]')?.textContent?.trim() || '',
              score: card.querySelector('.score-box strong')?.textContent?.trim() || '',
              current_price: stat('Price'),
              buy_zone: stat('Buy Zone'),
              stop_loss: stat('Stop'),
              targets: stat('Targets'),
              risk_reward: stat('R/R'),
              reason: card.querySelector('[data-testid="result-reason"]')?.textContent?.trim() || '',
              chart_url: (card.querySelector('img.chart-preview')?.getAttribute('data-chart')
                || card.querySelector('img.chart-preview')?.getAttribute('src')
                || '').split('?')[0],
              raw_text: card.innerText,
            };
          })"""
    )
    return [parse_result(item) for item in extracted]


def persist_chart_images(results: list[SetupResult], base_url: str, run_id: str, deadline: float) -> None:
    for result in results:
        if not result.chart_url:
            continue
        source_url = urljoin(base_url, result.chart_url)
        destination = CHART_DIR / f"market_lens_agent_{run_id}_{result.ticker.lower()}.png"
        try:
            request = Request(source_url, headers={"User-Agent": "market-lens-agent/1.0"})
            with urlopen(request, timeout=max(1, remaining_ms(deadline, 60_000) // 1000)) as response:
                content_type = response.headers.get("content-type", "")
                data = response.read()
            if not data or "image" not in content_type.lower():
                continue
            destination.write_bytes(data)
            result.chart_url = str(destination)
        except Exception:
            continue


def is_transient_scan_error(message: str) -> bool:
    text = message.lower()
    return any(marker in text for marker in ("502", "503", "504", "timeout", "network"))


def remaining_ms(deadline: float, requested_ms: int) -> int:
    remaining = int((deadline - time.monotonic()) * 1000)
    if remaining <= 0:
        raise RuntimeError("Agent run timeout reached before completion.")
    return max(1_000, min(requested_ms, remaining))


def log(message: str) -> None:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {message}", flush=True)


def parse_result(item: dict[str, Any]) -> SetupResult:
    buy_low, buy_high = parse_range(item.get("buy_zone", ""))
    target_1, target_2 = parse_range(item.get("targets", ""))
    return SetupResult(
        ticker=item.get("ticker", ""),
        setup_type=item.get("setup_type", ""),
        score=parse_float(item.get("score")),
        current_price=parse_float(item.get("current_price")),
        buy_zone_low=buy_low,
        buy_zone_high=buy_high,
        stop_loss=parse_optional_float(item.get("stop_loss")),
        target_1=target_1,
        target_2=target_2,
        risk_reward=parse_float(str(item.get("risk_reward", "")).replace("x", "")),
        reason=item.get("reason", ""),
        raw_text=item.get("raw_text", ""),
        chart_url=str(item.get("chart_url", "")),
    )


def update_workbook(
    *,
    settings: Settings,
    run_id: str,
    results: list[SetupResult],
    screenshot_path: Path,
    summary_path: Path,
    decision_path: Path,
    errors: list[str],
) -> dict[str, Any]:
    wb = load_workbook(settings.excel_path)
    ensure_agent_columns(wb)
    settings_values = read_settings(wb)
    currency = str(settings_values.get("budget_currency") or "USD").upper()
    currency_rate = 1.0 if currency == "USD" else float(settings_values.get("usd_ils_rate", 3.7))
    starting_capital = float(
        settings_values.get("starting_capital_usd")
        or settings_values.get("starting_capital_ils")
        or 100_000
    )
    max_position = starting_capital * float(settings_values.get("max_position_allocation_pct", 0.1))
    max_total_exposure = starting_capital * float(settings_values.get("max_total_exposure_pct", 0.4))
    max_risk = starting_capital * float(settings_values.get("max_risk_per_trade_pct", 0.01))
    min_rr = float(settings_values.get("min_risk_reward", settings.min_rr))
    sector_map = base_universe()
    run_context = build_agent_run_context(
        analysis_period=settings.analysis_period,
        starting_capital=starting_capital,
        default_max_total_exposure=max_total_exposure,
        max_position=max_position,
    )
    sector_health = run_context.sector_health or build_sector_health(settings.analysis_period)

    open_positions = read_open_positions(wb)
    cash = compute_cash(wb, starting_capital)
    exposure = sum(pos["exposure_ils"] for pos in open_positions.values())
    decisions: dict[str, Decision] = {}
    decision_records: list[dict[str, Any]] = []
    timestamp = datetime.now().isoformat(timespec="seconds")

    for result in results:
        decision = decide(
            result,
            open_positions=open_positions,
            cash=cash,
            exposure=exposure,
            usd_ils=currency_rate,
            max_position=max_position,
            max_total_exposure=max_total_exposure,
            max_risk=max_risk,
            min_rr=min_rr,
            sector_map=sector_map,
            sector_health=sector_health,
        )
        decision_json = evaluate_agent_candidate(
            timestamp=timestamp,
            result=result,
            initial_action=decision.action,
            initial_reason=decision.feedback,
            quantity=decision.quantity,
            cash_out=decision.cash_out_ils,
            risk_amount=decision.risk_ils,
            cash_available=cash,
            portfolio_exposure_before=exposure,
            open_positions=open_positions,
            sector_map=sector_map,
            run_context=run_context,
        )
        decision_json["scan_source"] = scan_source_text(settings)
        final_action = str(decision_json.get("final_action") or decision.action)
        final_reason = str(decision_json.get("reason") or decision.feedback)
        if final_action == "BUY_SIMULATED":
            decision.quantity = int(decision_json.get("position_size") or decision.quantity)
            decision.cash_out_ils = float(decision_json.get("adjusted_cash_out") or decision.cash_out_ils)
            decision.risk_ils = float(decision_json.get("adjusted_risk_amount") or decision.risk_ils)
        if final_action != decision.action:
            decision = Decision(final_action, final_reason, decision_json=decision_json)
        else:
            decision.feedback = final_reason
            decision.decision_json = decision_json
        result.selection_context = build_selection_context(
            result,
            decision,
            universe=settings.universe,
            explicit_tickers=bool(settings.tickers),
            sector_map=sector_map,
            sector_health=sector_health,
            decision_json=decision_json,
        )
        decision_records.append(decision_json)
        decisions[result.ticker] = decision
        append_watchlist_row(wb, timestamp, result, decision)
        if decision.action == "BUY_SIMULATED":
            append_trade_log_row(wb, timestamp, result, decision, currency_rate, screenshot_path)
            open_positions[result.ticker] = position_from_buy(result, decision, currency_rate, timestamp, screenshot_path)
            cash -= decision.cash_out_ils
            exposure += decision.cash_out_ils
        elif decision.action in {"TAKE_PARTIAL_PROFIT", "TAKE_PROFIT", "EXIT_STOP"}:
            append_trade_log_row(wb, timestamp, result, decision, currency_rate, screenshot_path)
            apply_exit_decision(open_positions, result, decision, currency_rate)
        elif result.ticker in open_positions:
            refresh_open_position(open_positions[result.ticker], result, currency_rate, decision)

    write_open_positions(wb, open_positions)
    write_decision_jsonl(decision_path, decision_records)
    cash = compute_cash(wb, starting_capital)
    exposure = sum(pos["exposure_ils"] for pos in open_positions.values())
    open_risk = sum(pos["risk_ils"] for pos in open_positions.values())
    append_update_log(
        wb,
        timestamp=timestamp,
        run_id=run_id,
        tickers=[result.ticker for result in results],
        valid_setups=sum(1 for result in results if result.setup_type != "No Trade"),
        decisions=decisions,
        cash=cash,
        exposure=exposure,
        open_risk=open_risk,
        open_positions=len(open_positions),
        summary_path=summary_path,
        screenshot_path=screenshot_path,
        decision_path=decision_path,
    )
    wb.save(settings.excel_path)
    return {
        "decisions": decisions,
        "cash": cash,
        "exposure": exposure,
        "remaining_budget": max(0.0, starting_capital - exposure),
        "open_risk": open_risk,
        "open_positions": open_positions,
        "workbook": settings.excel_path,
        "currency": currency,
        "decision_path": decision_path,
        "market_regime": run_context.market_regime,
    }


def decide(
    result: SetupResult,
    *,
    open_positions: dict[str, dict[str, Any]],
    cash: float,
    exposure: float,
    usd_ils: float,
    max_position: float,
    max_total_exposure: float,
    max_risk: float,
    min_rr: float,
    sector_map: dict[str, str],
    sector_health: dict[str, dict[str, Any]],
) -> Decision:
    existing = open_positions.get(result.ticker)
    if existing:
        quantity = int(existing.get("quantity") or 0)
        cash_in = quantity * result.current_price * usd_ils
        if result.current_price <= float(existing["stop_loss"]):
            return Decision("EXIT_STOP", "Current price reached stop loss.", quantity=quantity, cash_in_ils=round(cash_in, 2))
        if result.target_2 and result.current_price >= result.target_2:
            return Decision("TAKE_PROFIT", "Target 2 reached; close remaining simulated position.", quantity=quantity, cash_in_ils=round(cash_in, 2))
        if result.target_1 and result.current_price >= result.target_1 and not existing.get("partial_taken"):
            partial_qty = max(1, quantity // 2)
            return Decision(
                "TAKE_PARTIAL_PROFIT",
                "Target 1 reached; take partial simulated profit and move stop to breakeven.",
                quantity=partial_qty,
                cash_in_ils=round(partial_qty * result.current_price * usd_ils, 2),
            )
        return Decision("HOLD", "Existing simulated position remains open.")

    if result.setup_type.upper().replace(" ", "_") in {"NO_TRADE", "NO-TRADE"} or result.setup_type == "No Trade":
        return Decision("SKIP", "No Trade result.")
    sector = sector_map.get(result.ticker)
    health = sector_health.get(sector or "")
    if health and health.get("label") == "Weak":
        return Decision(
            "SKIP",
            f"{sector} sector regime is weak ({float(health.get('score', 0)):.0f}/100); skip new entry.",
        )
    if result.risk_reward < min_rr:
        return Decision(
            "WATCH",
            (
                f"Technical setup detected, but weighted risk/reward {result.risk_reward:.2f} "
                f"is below minimum {min_rr:.2f}."
            ),
        )
    if result.buy_zone_low is None or result.buy_zone_high is None:
        return Decision("SKIP", "Buy zone missing.")
    if not result.stop_loss or result.stop_loss <= 0:
        return Decision("SKIP", "Stop loss missing.")
    if not result.target_1 or not result.target_2:
        return Decision("SKIP", "Targets missing.")
    if not (result.buy_zone_low <= result.current_price <= result.buy_zone_high):
        return Decision("WATCH", "Valid setup, but price is not inside the buy zone.")

    risk_per_share_ils = (result.current_price - result.stop_loss) * usd_ils
    if risk_per_share_ils <= 0:
        return Decision("SKIP", "Risk cannot be calculated safely.")
    price_ils = result.current_price * usd_ils
    remaining_exposure = max_total_exposure - exposure
    max_cash_for_trade = min(max_position, max_risk / risk_per_share_ils * price_ils, cash, remaining_exposure)
    quantity = math.floor(max_cash_for_trade / price_ils)
    if quantity <= 0:
        return Decision("SKIP", "Position size blocked by cash, exposure, or risk limits.")
    cash_out = quantity * price_ils
    risk_ils = quantity * risk_per_share_ils
    return Decision(
        "BUY_SIMULATED",
        "Price is inside buy zone, R/R is valid, and simulated risk limits allow entry.",
        quantity=quantity,
        cash_out_ils=round(cash_out, 2),
        risk_ils=round(risk_ils, 2),
    )


def build_selection_context(
    result: SetupResult,
    decision: Decision,
    *,
    universe: str,
    explicit_tickers: bool,
    sector_map: dict[str, str],
    sector_health: dict[str, dict[str, Any]],
    decision_json: dict[str, Any] | None = None,
) -> str:
    sector = sector_map.get(result.ticker, "Unknown")
    health = sector_health.get(sector, {})
    if explicit_tickers:
        source = "Configured manual ticker basket"
    elif universe == "smart-universe":
        source = "Smart Universe: broad liquid US universe, diversified by sector"
    else:
        source = f"Configured universe: {universe}"

    sector_text = f"Sector: {sector}"
    if health:
        sector_text += (
            f" - {health.get('label', 'Unknown')} "
            f"({float(health.get('score', 0)):.0f}/100)"
        )
        if health.get("reason"):
            sector_text += f"; {health['reason']}"

    setup_text = (
        f"Setup: {result.setup_type}; score {result.score:.2f}; "
        f"R/R {result.risk_reward:.2f}x; price {result.current_price:.2f}"
    )
    action_text = f"Agent action: {decision.action} - {decision.feedback}"
    parts = [source, sector_text, setup_text]
    if decision_json:
        risk_bits = [
            f"Market: {decision_json.get('market_regime', 'Unknown')}",
            f"Sector regime: {decision_json.get('sector_regime', 'Unknown')}",
        ]
        if decision_json.get("net_rr") is not None:
            risk_bits.append(f"Net R/R: {float(decision_json.get('net_rr') or 0):.2f}")
        if decision_json.get("factor_tags"):
            risk_bits.append(f"Factors: {', '.join(decision_json.get('factor_tags') or [])}")
        parts.append("; ".join(risk_bits))
    parts.append(action_text)
    return " | ".join(parts)


def scan_source_text(settings: Settings) -> str:
    if settings.tickers:
        return "Manual ticker basket configured for this agent run."
    if settings.universe == "smart-universe":
        return "Smart Universe: broad liquid US universe filtered and diversified by the Market Lens UI."
    return f"Configured UI universe: {settings.universe}."


def read_settings(wb: Any) -> dict[str, Any]:
    ws = wb["Settings"]
    values = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0]:
            values[str(row[0])] = row[1]
    return values


def ensure_agent_columns(wb: Any) -> None:
    headers = {
        "Setup Watchlist": {
            16: "Chart URL",
            17: "Selection Context",
            18: "Decision JSON",
        },
        "Trade Log": {
            18: "Chart URL",
            19: "Selection Context",
            20: "Decision JSON",
        },
        "Open Positions": {
            16: "Chart URL",
            17: "Selection Context",
            18: "Decision JSON",
        },
        "Update Log": {
            12: "Decision JSONL",
        },
    }
    for sheet_name, sheet_headers in headers.items():
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        for col_idx, header in sheet_headers.items():
            ws.cell(1, col_idx, header)


def read_open_positions(wb: Any) -> dict[str, dict[str, Any]]:
    ws = wb["Open Positions"]
    positions = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[0]:
            continue
        positions[str(row[0])] = {
            "ticker": row[0],
            "entry_date": row[1],
            "entry_price": float(row[2] or 0),
            "current_price": float(row[3] or row[2] or 0),
            "quantity": int(row[4] or 0),
            "stop_loss": float(row[5] or 0),
            "target_1": float(row[6] or 0),
            "target_2": float(row[7] or 0),
            "status": row[8],
            "unrealized_usd": float(row[9] or 0),
            "unrealized_ils": float(row[10] or 0),
            "exposure_ils": float(row[11] or 0),
            "risk_ils": float(row[12] or 0),
            "notes": str(row[13] or ""),
            "screenshot": str(row[14] or ""),
            "chart_url": str(row[15] or ""),
            "selection_context": str(row[16] or ""),
            "decision_json": str(row[17] or ""),
            "partial_taken": "partial" in str(row[13] or "").lower(),
        }
    return positions


def compute_cash(wb: Any, starting_capital: float) -> float:
    ws = wb["Trade Log"]
    cash_out = 0.0
    cash_in = 0.0
    for row in ws.iter_rows(min_row=2, values_only=True):
        cash_out += float(row[9] or 0)
        cash_in += float(row[10] or 0)
    return round(starting_capital - cash_out + cash_in, 2)


def read_open_position_tickers(excel_path: Path) -> list[str]:
    try:
        wb = load_workbook(excel_path, read_only=True, data_only=True)
        ws = wb["Open Positions"]
    except Exception:
        return []
    try:
        tickers = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0]:
                tickers.append(str(row[0]).upper())
        return tickers
    finally:
        wb.close()


def append_watchlist_row(wb: Any, timestamp: str, result: SetupResult, decision: Decision) -> None:
    ws = wb["Setup Watchlist"]
    row = next_row(ws)
    ws.cell(row, 1, timestamp)
    ws.cell(row, 2, result.ticker)
    ws.cell(row, 3, result.setup_type)
    ws.cell(row, 4, result.score)
    ws.cell(row, 5, result.current_price)
    ws.cell(row, 6, result.buy_zone_low)
    ws.cell(row, 7, result.buy_zone_high)
    ws.cell(row, 8, result.stop_loss)
    ws.cell(row, 9, result.target_1)
    ws.cell(row, 10, result.target_2)
    ws.cell(row, 11, result.risk_reward)
    ws.cell(row, 12, result.reason)
    ws.cell(row, 13, decision.action)
    ws.cell(row, 14, decision.feedback)
    ws.cell(row, 15, result.raw_text[:1000])
    ws.cell(row, 16, result.chart_url)
    ws.cell(row, 17, result.selection_context)
    ws.cell(row, 18, decision_json_text(decision))


def append_trade_log_row(
    wb: Any,
    timestamp: str,
    result: SetupResult,
    decision: Decision,
    usd_ils: float,
    screenshot_path: Path,
) -> None:
    ws = wb["Trade Log"]
    row = next_row(ws)
    ws.cell(row, 1, timestamp)
    ws.cell(row, 2, decision.action)
    ws.cell(row, 3, result.ticker)
    ws.cell(row, 4, result.current_price if decision.action == "BUY_SIMULATED" else None)
    ws.cell(row, 5, result.current_price if decision.action != "BUY_SIMULATED" else None)
    ws.cell(row, 6, decision.quantity)
    ws.cell(row, 7, usd_ils)
    ws.cell(row, 8, decision.quantity * result.current_price * usd_ils if decision.action == "BUY_SIMULATED" else 0)
    ws.cell(row, 9, decision.cash_in_ils)
    ws.cell(row, 10, decision.cash_out_ils)
    ws.cell(row, 11, decision.cash_in_ils)
    ws.cell(row, 12, result.stop_loss)
    ws.cell(row, 13, result.target_1)
    ws.cell(row, 14, result.target_2)
    ws.cell(row, 15, decision.risk_ils)
    ws.cell(row, 16, decision.feedback)
    ws.cell(row, 17, str(screenshot_path))
    ws.cell(row, 18, result.chart_url)
    ws.cell(row, 19, result.selection_context)
    ws.cell(row, 20, decision_json_text(decision))


def position_from_buy(
    result: SetupResult,
    decision: Decision,
    usd_ils: float,
    timestamp: str,
    screenshot_path: Path,
) -> dict[str, Any]:
    exposure = decision.quantity * result.current_price * usd_ils
    risk = decision.quantity * (result.current_price - float(result.stop_loss or 0)) * usd_ils
    return {
        "ticker": result.ticker,
        "entry_date": timestamp,
        "entry_price": result.current_price,
        "current_price": result.current_price,
        "quantity": decision.quantity,
        "stop_loss": result.stop_loss,
        "target_1": result.target_1,
        "target_2": result.target_2,
        "status": "OPEN",
        "unrealized_usd": 0,
        "unrealized_ils": 0,
        "exposure_ils": round(exposure, 2),
        "risk_ils": round(risk, 2),
        "notes": decision.feedback,
        "screenshot": str(screenshot_path),
        "chart_url": result.chart_url,
        "selection_context": result.selection_context,
        "decision_json": decision_json_text(decision),
    }


def apply_exit_decision(
    open_positions: dict[str, dict[str, Any]],
    result: SetupResult,
    decision: Decision,
    usd_ils: float,
) -> None:
    pos = open_positions.get(result.ticker)
    if not pos:
        return
    if decision.action == "TAKE_PARTIAL_PROFIT":
        closed_qty = min(int(pos["quantity"]), max(1, decision.quantity))
        remaining_qty = int(pos["quantity"]) - closed_qty
        if remaining_qty <= 0:
            open_positions.pop(result.ticker, None)
            return
        pos["quantity"] = remaining_qty
        pos["stop_loss"] = pos["entry_price"]
        pos["notes"] = "Partial profit taken; stop moved to breakeven."
        refresh_open_position(pos, result, usd_ils, decision)
        return
    open_positions.pop(result.ticker, None)


def refresh_open_position(
    pos: dict[str, Any],
    result: SetupResult,
    usd_ils: float,
    decision: Decision | None = None,
) -> None:
    qty = int(pos.get("quantity") or 0)
    entry = float(pos.get("entry_price") or 0)
    current = result.current_price
    stop = float(pos.get("stop_loss") or 0)
    pos["current_price"] = current
    pos["unrealized_usd"] = round((current - entry) * qty, 2)
    pos["unrealized_ils"] = round((current - entry) * qty * usd_ils, 2)
    pos["exposure_ils"] = round(current * qty * usd_ils, 2)
    pos["risk_ils"] = round(max(0.0, current - stop) * qty * usd_ils, 2)
    if result.chart_url:
        pos["chart_url"] = result.chart_url
    if result.selection_context:
        pos["selection_context"] = result.selection_context
    if decision and decision.decision_json:
        # Preserve the latest structured explanation for dashboard drill-down.
        pos["decision_json"] = decision_json_text(decision)


def write_open_positions(wb: Any, positions: dict[str, dict[str, Any]]) -> None:
    ws = wb["Open Positions"]
    if ws.max_row > 1:
        ws.delete_rows(2, ws.max_row - 1)
    for row_idx, pos in enumerate(positions.values(), start=2):
        values = [
            pos["ticker"], pos["entry_date"], pos["entry_price"], pos["current_price"], pos["quantity"],
            pos["stop_loss"], pos["target_1"], pos["target_2"], pos["status"], pos["unrealized_usd"],
            pos["unrealized_ils"], pos["exposure_ils"], pos["risk_ils"], pos.get("notes", ""), pos.get("screenshot", ""),
            pos.get("chart_url", ""), pos.get("selection_context", ""), pos.get("decision_json", ""),
        ]
        for col_idx, value in enumerate(values, start=1):
            ws.cell(row_idx, col_idx, value)


def decision_json_text(decision: Decision) -> str:
    if not decision.decision_json:
        return ""
    return json.dumps(decision.decision_json, ensure_ascii=False, sort_keys=True, default=str)


def write_decision_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) for record in records)
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


def append_update_log(
    wb: Any,
    *,
    timestamp: str,
    run_id: str,
    tickers: list[str],
    valid_setups: int,
    decisions: dict[str, Decision],
    cash: float,
    exposure: float,
    open_risk: float,
    open_positions: int,
    summary_path: Path,
    screenshot_path: Path,
    decision_path: Path,
) -> None:
    ws = wb["Update Log"]
    row = next_row(ws)
    actions = ", ".join(f"{ticker}:{decision.action}" for ticker, decision in decisions.items())
    ws.cell(row, 1, timestamp)
    ws.cell(row, 2, run_id)
    ws.cell(row, 3, " ".join(tickers))
    ws.cell(row, 4, valid_setups)
    ws.cell(row, 5, actions)
    ws.cell(row, 6, cash)
    ws.cell(row, 7, exposure)
    ws.cell(row, 8, open_risk)
    ws.cell(row, 9, open_positions)
    ws.cell(row, 10, str(summary_path))
    ws.cell(row, 11, str(screenshot_path))
    ws.cell(row, 12, str(decision_path))


def write_summary(
    *,
    settings: Settings,
    run_id: str,
    login_status: str,
    scan_status: str,
    results: list[SetupResult],
    decisions: dict[str, Decision],
    screenshot_path: Path,
    summary_path: Path,
    workbook_context: dict[str, Any],
    errors: list[str],
) -> None:
    valid = [result for result in results if result.setup_type != "No Trade"]
    buys = [ticker for ticker, decision in decisions.items() if decision.action == "BUY_SIMULATED"]
    watch = [ticker for ticker, decision in decisions.items() if decision.action == "WATCH"]
    closed = [ticker for ticker, decision in decisions.items() if decision.action in {"TAKE_PROFIT", "EXIT_STOP", "TAKE_PARTIAL_PROFIT"}]
    market_regime = workbook_context.get("market_regime")
    market_text = (
        f"{market_regime.label} ({market_regime.score:.2f}) - {market_regime.reason}"
        if market_regime
        else "Not available"
    )
    lines = [
        "Market Lens Agent Update",
        "",
        f"Date: {datetime.now().isoformat(timespec='seconds')}",
        f"Run status: {'OK' if not errors else 'ISSUES'}",
        f"Login status: {login_status}",
        f"Scan status: {scan_status}",
        f"Tickers scanned: {' '.join(result.ticker for result in results)}",
        f"Valid setups found: {len(valid)}",
        f"Market regime: {market_text}",
        f"Actions taken: {', '.join(f'{ticker}:{decision.action}' for ticker, decision in decisions.items())}",
        f"New simulated buys: {', '.join(buys) or 'None'}",
        f"Positions on watch: {', '.join(watch) or 'None'}",
        f"Positions closed: {', '.join(closed) or 'None'}",
        f"Cash remaining: {workbook_context['cash']:.2f} {workbook_context['currency']}",
        f"Current exposure: {workbook_context['exposure']:.2f} {workbook_context['currency']}",
        f"Remaining available budget: {workbook_context['remaining_budget']:.2f} {workbook_context['currency']}",
        f"Total open risk: {workbook_context['open_risk']:.2f} {workbook_context['currency']}",
        f"Excel updated: {settings.excel_path}",
        f"Screenshot saved: {screenshot_path}",
        f"Decision JSONL saved: {workbook_context.get('decision_path', '')}",
        f"Errors: {'; '.join(errors) if errors else 'None'}",
        "Agent feedback:",
    ]
    for result in results:
        decision = decisions.get(result.ticker)
        if decision:
            lines.append(f"- {result.ticker}: {decision.action} - {decision.feedback}")
            if decision.decision_json:
                warnings = decision.decision_json.get("warnings") or []
                if warnings:
                    lines.append(f"  Warnings: {'; '.join(str(item) for item in warnings[:4])}")
            if result.selection_context:
                lines.append(f"  Context: {result.selection_context}")
    summary_path.write_text("\n".join(lines), encoding="utf-8")


def next_row(ws: Any) -> int:
    for row in range(2, ws.max_row + 2):
        if ws.cell(row, 1).value is None:
            return row
    return ws.max_row + 1


def parse_tickers(value: str) -> list[str]:
    return [item.upper() for item in re.split(r"[\s,]+", value.strip()) if item]


def unique_tickers(tickers: list[str]) -> list[str]:
    seen = set()
    result = []
    for ticker in tickers:
        normalized = ticker.upper()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def parse_float(value: Any) -> float:
    parsed = parse_optional_float(value)
    return parsed if parsed is not None else 0.0


def parse_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if text in {"", "-"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def parse_range(value: Any) -> tuple[float | None, float | None]:
    text = str(value or "").replace(",", "")
    nums = [float(item) for item in re.findall(r"-?\d+(?:\.\d+)?", text)]
    if len(nums) >= 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return nums[0], None
    return None, None


if __name__ == "__main__":
    main()
