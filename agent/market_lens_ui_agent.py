from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openpyxl import load_workbook
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = Path(os.getenv("MARKET_LENS_RUN_DIR", ROOT / "agent_runs"))
SCREENSHOT_DIR = RUN_DIR / "screenshots"
SUMMARY_DIR = RUN_DIR / "summaries"


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


@dataclass
class Decision:
    action: str
    feedback: str
    quantity: int = 0
    cash_out_ils: float = 0.0
    cash_in_ils: float = 0.0
    risk_ils: float = 0.0


def main() -> None:
    settings = load_settings()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    screenshot_path = SCREENSHOT_DIR / f"market_lens_agent_{run_id}.png"
    summary_path = SUMMARY_DIR / f"market_lens_agent_{run_id}.md"

    errors: list[str] = []
    results: list[SetupResult] = []
    decisions: dict[str, Decision] = {}
    login_status = "not_started"
    scan_status = "not_started"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=settings.headless)
        page = browser.new_page(viewport={"width": 1440, "height": 1200})
        try:
            page.goto(settings.url, wait_until="networkidle", timeout=90_000)
            login_status = login(page, settings)
            configure_scan(page, settings)
            results = run_scan(page)
            scan_status = f"completed: {len(results)} results"
            page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception as exc:
            errors.append(str(exc))
            scan_status = "failed"
            try:
                page.screenshot(path=str(screenshot_path), full_page=True)
            except Exception:
                pass
        finally:
            browser.close()

    workbook_context = update_workbook(
        settings=settings,
        run_id=run_id,
        results=results,
        screenshot_path=screenshot_path,
        summary_path=summary_path,
        errors=errors,
    )
    decisions = workbook_context["decisions"]
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
    return Settings(url, email, password, excel_path, universe, tickers, analysis_period, min_rr, headless)


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def login(page: Page, settings: Settings) -> str:
    page.wait_for_selector('[data-testid="auth-email"], #authStatus', timeout=30_000)
    auth_status = page.locator("#authStatus").inner_text(timeout=10_000)
    if settings.email.lower() in auth_status.lower():
        return "already signed in"
    page.locator('[data-testid="auth-email"]').fill(settings.email)
    page.locator('[data-testid="auth-password"]').fill(settings.password)
    page.locator('[data-testid="sign-in-button"]').click()
    page.wait_for_function(
        "email => document.querySelector('#authStatus')?.textContent?.toLowerCase().includes(email.toLowerCase())",
        arg=settings.email,
        timeout=45_000,
    )
    return "signed in"


def configure_scan(page: Page, settings: Settings) -> None:
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
        timeout=240_000,
    )
    page.locator('[data-testid="select-all-tickers"]').click()
    page.locator('[data-testid="add-selected-tickers"]').click()
    if open_tickers:
        page.locator('[data-testid="manual-ticker-input"]').fill(" ".join(open_tickers))
        page.locator('[data-testid="add-manual-ticker"]').click()


def run_scan(page: Page) -> list[SetupResult]:
    page.locator('[data-testid="scan-button"]').click()
    page.wait_for_function(
        "() => !document.querySelector('[data-testid=\"scan-button\"]')?.disabled",
        timeout=600_000,
    )
    if "failed" in page.locator("#runMeta").inner_text(timeout=10_000).lower():
        raise RuntimeError(page.locator("#message").inner_text(timeout=10_000))
    page.wait_for_selector('[data-testid="result-card"]', timeout=30_000)
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
              raw_text: card.innerText,
            };
          })"""
    )
    return [parse_result(item) for item in extracted]


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
    )


def update_workbook(
    *,
    settings: Settings,
    run_id: str,
    results: list[SetupResult],
    screenshot_path: Path,
    summary_path: Path,
    errors: list[str],
) -> dict[str, Any]:
    wb = load_workbook(settings.excel_path)
    settings_values = read_settings(wb)
    usd_ils = float(settings_values.get("usd_ils_rate", 3.7))
    starting_capital = float(settings_values.get("starting_capital_ils", 100_000))
    max_position = starting_capital * float(settings_values.get("max_position_allocation_pct", 0.1))
    max_total_exposure = starting_capital * float(settings_values.get("max_total_exposure_pct", 0.4))
    max_risk = starting_capital * float(settings_values.get("max_risk_per_trade_pct", 0.01))
    min_rr = float(settings_values.get("min_risk_reward", settings.min_rr))

    open_positions = read_open_positions(wb)
    cash = compute_cash(wb, starting_capital)
    exposure = sum(pos["exposure_ils"] for pos in open_positions.values())
    decisions: dict[str, Decision] = {}
    timestamp = datetime.now().isoformat(timespec="seconds")

    for result in results:
        decision = decide(
            result,
            open_positions=open_positions,
            cash=cash,
            exposure=exposure,
            usd_ils=usd_ils,
            max_position=max_position,
            max_total_exposure=max_total_exposure,
            max_risk=max_risk,
            min_rr=min_rr,
        )
        decisions[result.ticker] = decision
        append_watchlist_row(wb, timestamp, result, decision)
        if decision.action == "BUY_SIMULATED":
            append_trade_log_row(wb, timestamp, result, decision, usd_ils, screenshot_path)
            open_positions[result.ticker] = position_from_buy(result, decision, usd_ils, timestamp, screenshot_path)
            cash -= decision.cash_out_ils
            exposure += decision.cash_out_ils
        elif decision.action in {"TAKE_PARTIAL_PROFIT", "TAKE_PROFIT", "EXIT_STOP"}:
            append_trade_log_row(wb, timestamp, result, decision, usd_ils, screenshot_path)
            apply_exit_decision(open_positions, result, decision, usd_ils)
        elif result.ticker in open_positions:
            refresh_open_position(open_positions[result.ticker], result, usd_ils)

    write_open_positions(wb, open_positions)
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
    if result.risk_reward < min_rr:
        return Decision("SKIP", f"Risk/reward {result.risk_reward:.2f} is below minimum {min_rr:.2f}.")
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


def read_settings(wb: Any) -> dict[str, Any]:
    ws = wb["Settings"]
    values = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0]:
            values[str(row[0])] = row[1]
    return values


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
        refresh_open_position(pos, result, usd_ils)
        return
    open_positions.pop(result.ticker, None)


def refresh_open_position(pos: dict[str, Any], result: SetupResult, usd_ils: float) -> None:
    qty = int(pos.get("quantity") or 0)
    entry = float(pos.get("entry_price") or 0)
    current = result.current_price
    stop = float(pos.get("stop_loss") or 0)
    pos["current_price"] = current
    pos["unrealized_usd"] = round((current - entry) * qty, 2)
    pos["unrealized_ils"] = round((current - entry) * qty * usd_ils, 2)
    pos["exposure_ils"] = round(current * qty * usd_ils, 2)
    pos["risk_ils"] = round(max(0.0, current - stop) * qty * usd_ils, 2)


def write_open_positions(wb: Any, positions: dict[str, dict[str, Any]]) -> None:
    ws = wb["Open Positions"]
    if ws.max_row > 1:
        ws.delete_rows(2, ws.max_row - 1)
    for row_idx, pos in enumerate(positions.values(), start=2):
        values = [
            pos["ticker"], pos["entry_date"], pos["entry_price"], pos["current_price"], pos["quantity"],
            pos["stop_loss"], pos["target_1"], pos["target_2"], pos["status"], pos["unrealized_usd"],
            pos["unrealized_ils"], pos["exposure_ils"], pos["risk_ils"], pos.get("notes", ""), pos.get("screenshot", ""),
        ]
        for col_idx, value in enumerate(values, start=1):
            ws.cell(row_idx, col_idx, value)


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
    lines = [
        "Market Lens Agent Update",
        "",
        f"Date: {datetime.now().isoformat(timespec='seconds')}",
        f"Run status: {'OK' if not errors else 'ISSUES'}",
        f"Login status: {login_status}",
        f"Scan status: {scan_status}",
        f"Tickers scanned: {' '.join(result.ticker for result in results)}",
        f"Valid setups found: {len(valid)}",
        f"Actions taken: {', '.join(f'{ticker}:{decision.action}' for ticker, decision in decisions.items())}",
        f"New simulated buys: {', '.join(buys) or 'None'}",
        f"Positions on watch: {', '.join(watch) or 'None'}",
        f"Positions closed: {', '.join(closed) or 'None'}",
        f"Cash remaining: {workbook_context['cash']:.2f} ILS",
        f"Current exposure: {workbook_context['exposure']:.2f} ILS",
        f"Remaining available budget: {workbook_context['remaining_budget']:.2f} ILS",
        f"Total open risk: {workbook_context['open_risk']:.2f} ILS",
        f"Excel updated: {settings.excel_path}",
        f"Screenshot saved: {screenshot_path}",
        f"Errors: {'; '.join(errors) if errors else 'None'}",
        "Agent feedback:",
    ]
    for result in results:
        decision = decisions.get(result.ticker)
        if decision:
            lines.append(f"- {result.ticker}: {decision.action} - {decision.feedback}")
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
