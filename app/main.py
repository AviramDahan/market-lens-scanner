import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agent_dashboard import TRACKER_NAME, build_agent_dashboard, with_position_calculations
from app.auth import get_current_user_required, supabase_publishable_key
from app.charts import write_scan_chart
from app.config import load_config
from app.data import fetch_intraday_frame
from app.models import MonitorTriggerRequest, SaveSetupRequest, ScanRequest, ScanResponse
from app.monitor_trigger import (
    detect_live_monitor_event,
    dispatch_position_monitor,
    monitor_trigger_configured,
    rate_limit_reason,
)
from app.scanner import scan_tickers
from app.smart_universe import build_smart_universe
from app.storage import init_storage, list_setups, refresh_setup, save_setup, using_external_storage
from app.strategy import apply_strategy_decisions
from app.watchlists import list_watchlists

app = FastAPI(title="Market Lens", version="0.1.0", description="Swing trade scanner")

PROJECT_ROOT = Path(__file__).parent.parent
STATIC_DIR = Path(__file__).parent / "static"
CHART_DIR = PROJECT_ROOT / "charts"
AGENT_RESULTS_DIR = PROJECT_ROOT / "agent_results"
AGENT_TRACKER_DIR = PROJECT_ROOT / "agent_tracker"
CHART_DIR.mkdir(exist_ok=True)
AGENT_RESULTS_DIR.mkdir(exist_ok=True)
init_storage()

LIVE_PRICE_CACHE_TTL_SECONDS = int(os.getenv("MARKET_LENS_LIVE_PRICE_CACHE_TTL", "45"))
_LIVE_PRICE_CACHE: dict[str, tuple[float, float, str]] = {}

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/charts", StaticFiles(directory=CHART_DIR), name="charts")
app.mount("/agent-results", StaticFiles(directory=AGENT_RESULTS_DIR), name="agent-results")


@app.get("/", include_in_schema=False)
async def ui() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/agent", include_in_schema=False)
async def agent_ui() -> FileResponse:
    return FileResponse(STATIC_DIR / "agent.html")


@app.get("/agent/", include_in_schema=False)
async def agent_ui_trailing() -> FileResponse:
    return FileResponse(STATIC_DIR / "agent.html")


@app.get("/agent/data")
async def get_agent_dashboard(date: str | None = Query(default=None)) -> dict:
    return build_agent_dashboard(PROJECT_ROOT, selected_date=date)


@app.get("/agent/live-prices")
async def get_agent_live_prices() -> dict:
    dashboard = build_agent_dashboard(PROJECT_ROOT)
    if dashboard.get("status") != "ok":
        return dashboard

    positions = dashboard.get("open_positions", [])
    refreshed = []
    prices = {}
    warnings = {}
    for position in positions:
        ticker = str(position.get("ticker") or "").upper()
        if not ticker:
            refreshed.append(position)
            continue
        try:
            price, source_time = fetch_live_price(ticker)
            updated = dict(position)
            updated["current_price_usd"] = round(price, 2)
            updated["live_price_updated_at"] = source_time
            updated["live_price_source"] = "1m intraday"
            refreshed_position = with_position_calculations(updated)
            refreshed.append(refreshed_position)
            prices[ticker] = {
                "current_price_usd": refreshed_position["current_price_usd"],
                "updated_at": source_time,
            }
        except Exception as exc:
            warnings[ticker] = str(exc)
            fallback = dict(position)
            fallback["live_price_warning"] = str(exc)
            refreshed.append(fallback)

    summary = dict(dashboard.get("summary") or {})
    exposure = round(sum(float(position.get("exposure_ils") or 0) for position in refreshed), 2)
    unrealized = round(sum(float(position.get("unrealized_pnl_ils") or 0) for position in refreshed), 2)
    open_risk = round(sum(float(position.get("open_risk_ils") or 0) for position in refreshed), 2)
    summary.update(
        {
            "exposure_ils": exposure,
            "unrealized_pnl_ils": unrealized,
            "open_risk_ils": open_risk,
            "equity_ils": round(float(summary.get("cash_ils") or 0) + exposure, 2),
        }
    )
    starting = float(summary.get("starting_capital_ils") or 0)
    summary["total_pnl_ils"] = round(summary["equity_ils"] - starting, 2)
    summary["total_pnl_pct"] = round(summary["total_pnl_ils"] / starting * 100, 2) if starting else 0

    return {
        "status": "ok",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": summary,
        "open_positions": refreshed,
        "prices": prices,
        "warnings": warnings,
    }


@app.post("/agent/trigger-monitor")
async def trigger_position_monitor(request: MonitorTriggerRequest) -> dict:
    trigger_configured = monitor_trigger_configured()
    dashboard = build_agent_dashboard(PROJECT_ROOT)
    if dashboard.get("status") != "ok":
        return {
            "status": "skipped",
            "triggered": False,
            "trigger_configured": trigger_configured,
            "reason": "Agent dashboard data unavailable.",
        }

    ticker = request.ticker.upper().strip()
    positions = dashboard.get("open_positions", [])
    position = next((item for item in positions if str(item.get("ticker") or "").upper() == ticker), None)
    if not position:
        return {
            "status": "skipped",
            "triggered": False,
            "trigger_configured": trigger_configured,
            "reason": f"No open position for {ticker}.",
        }

    try:
        live_price, source_time = fetch_live_price(ticker)
    except Exception as exc:
        return {
            "status": "skipped",
            "triggered": False,
            "trigger_configured": trigger_configured,
            "reason": f"Live price unavailable: {exc}",
        }

    event = detect_live_monitor_event(position, live_price)
    if event is None:
        return {
            "status": "skipped",
            "triggered": False,
            "trigger_configured": trigger_configured,
            "ticker": ticker,
            "live_price": round(live_price, 4),
            "live_price_updated_at": source_time,
            "reason": "Live price has not touched stop loss or targets.",
        }

    limit_reason = rate_limit_reason(event)
    if limit_reason:
        return {
            "status": "rate_limited",
            "triggered": False,
            "trigger_configured": trigger_configured,
            "ticker": ticker,
            "event_type": event.event_type,
            "live_price": round(live_price, 4),
            "reason": limit_reason,
        }

    if not trigger_configured:
        return {
            "status": "not_configured",
            "triggered": False,
            "trigger_configured": False,
            "ticker": ticker,
            "event_type": event.event_type,
            "live_price": round(live_price, 4),
            "reason": "GitHub monitor trigger token is not configured on the server.",
        }

    try:
        dispatch = await dispatch_position_monitor(event)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "status": "triggered",
        "triggered": True,
        "trigger_configured": True,
        "ticker": ticker,
        "event_type": event.event_type,
        "threshold": event.threshold,
        "live_price": round(live_price, 4),
        "live_price_updated_at": source_time,
        "reason": event.reason,
        "dispatch": dispatch,
    }


@app.get("/agent/monitor-live")
@app.post("/agent/monitor-live")
async def monitor_live_positions(
    x_monitor_secret: str | None = Header(default=None, alias="X-Market-Lens-Cron-Secret"),
    secret: str | None = Query(default=None, max_length=160),
) -> dict:
    protection = validate_monitor_cron_secret(x_monitor_secret, secret)
    trigger_configured = monitor_trigger_configured()
    dashboard = build_agent_dashboard(PROJECT_ROOT)
    if dashboard.get("status") != "ok":
        return {
            "status": "skipped",
            "triggered": False,
            "trigger_configured": trigger_configured,
            "protected": protection["protected"],
            "reason": "Agent dashboard data unavailable.",
        }

    positions = dashboard.get("open_positions", [])
    if not positions:
        return {
            "status": "skipped",
            "triggered": False,
            "trigger_configured": trigger_configured,
            "protected": protection["protected"],
            "open_positions": 0,
            "reason": "No open positions to monitor.",
        }

    checked = []
    detected_events = []
    warnings = {}
    dispatchable_event = None
    rate_limited = []

    for position in positions:
        ticker = str(position.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        try:
            live_price, source_time = fetch_live_price(ticker)
        except Exception as exc:
            warnings[ticker] = str(exc)
            checked.append({"ticker": ticker, "status": "price_unavailable", "warning": str(exc)})
            continue

        event = detect_live_monitor_event(position, live_price)
        checked_item = {
            "ticker": ticker,
            "live_price": round(live_price, 4),
            "live_price_updated_at": source_time,
            "status": "event_detected" if event else "no_event",
        }
        checked.append(checked_item)
        if not event:
            continue

        event_payload = live_monitor_event_payload(event)
        detected_events.append(event_payload)
        limit_reason = rate_limit_reason(event)
        if limit_reason:
            event_payload["rate_limited"] = True
            event_payload["rate_limit_reason"] = limit_reason
            rate_limited.append(event_payload)
            continue
        if dispatchable_event is None:
            dispatchable_event = event

    if not detected_events:
        return {
            "status": "ok",
            "triggered": False,
            "trigger_configured": trigger_configured,
            "protected": protection["protected"],
            "open_positions": len(positions),
            "positions_checked": len(checked),
            "checked": checked,
            "warnings": warnings,
            "reason": "No open position touched stop loss or targets.",
        }

    if not trigger_configured:
        return {
            "status": "not_configured",
            "triggered": False,
            "trigger_configured": False,
            "protected": protection["protected"],
            "open_positions": len(positions),
            "positions_checked": len(checked),
            "detected_events": detected_events,
            "warnings": warnings,
            "reason": "GitHub monitor trigger token is not configured on the server.",
        }

    if dispatchable_event is None:
        return {
            "status": "rate_limited",
            "triggered": False,
            "trigger_configured": True,
            "protected": protection["protected"],
            "open_positions": len(positions),
            "positions_checked": len(checked),
            "detected_events": detected_events,
            "rate_limited": rate_limited,
            "warnings": warnings,
            "reason": "Monitor trigger cooldown is active.",
        }

    try:
        dispatch = await dispatch_position_monitor(dispatchable_event, source="agent-server-live-monitor")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "status": "triggered",
        "triggered": True,
        "trigger_configured": True,
        "protected": protection["protected"],
        "open_positions": len(positions),
        "positions_checked": len(checked),
        "detected_events": detected_events,
        "dispatched_event": live_monitor_event_payload(dispatchable_event),
        "warnings": warnings,
        "reason": dispatchable_event.reason,
        "dispatch": dispatch,
    }


def validate_monitor_cron_secret(header_secret: str | None, query_secret: str | None) -> dict:
    expected = os.getenv("MARKET_LENS_MONITOR_CRON_SECRET") or os.getenv("MARKET_LENS_CRON_SECRET") or ""
    if not expected:
        return {"protected": False}
    provided = header_secret or query_secret or ""
    if provided != expected:
        raise HTTPException(status_code=401, detail="Invalid monitor cron secret.")
    return {"protected": True}


def live_monitor_event_payload(event) -> dict:
    return {
        "ticker": event.ticker,
        "event_type": event.event_type,
        "threshold": event.threshold,
        "live_price": round(event.live_price, 4),
        "reason": event.reason,
    }


def fetch_live_price(ticker: str) -> tuple[float, str]:
    now = time.monotonic()
    cached = _LIVE_PRICE_CACHE.get(ticker)
    if cached and now - cached[0] < LIVE_PRICE_CACHE_TTL_SECONDS:
        return cached[1], cached[2]

    try:
        frame = fetch_intraday_frame(ticker, period="1d", interval="1m")
    except Exception:
        frame = fetch_intraday_frame(ticker, period="5d", interval="1m")
    if frame.empty:
        raise ValueError(f"{ticker}: no live intraday rows returned")
    price = float(frame["Close"].dropna().iloc[-1])
    source_time = frame.index[-1].isoformat()
    _LIVE_PRICE_CACHE[ticker] = (now, price, source_time)
    return price, source_time


@app.get("/agent/tracker")
async def get_agent_tracker() -> FileResponse:
    tracker_path = AGENT_TRACKER_DIR / TRACKER_NAME
    if not tracker_path.exists():
        raise HTTPException(status_code=404, detail="Agent tracker not found")
    return FileResponse(
        tracker_path,
        filename=TRACKER_NAME,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.post("/scan", response_model=ScanResponse)
async def scan(
    request: ScanRequest,
    user: dict = Depends(get_current_user_required),
) -> ScanResponse:
    tickers = [t.upper() for t in request.tickers]
    results, errors, _ = scan_tickers(
        tickers,
        min_rr=request.min_rr,
        analysis_period=request.analysis_period,
    )
    results = apply_strategy_decisions(
        results,
        analysis_period=request.analysis_period,
        min_rr=request.min_rr,
    )
    return ScanResponse(results=results, errors=errors)


@app.post("/ui/scan")
async def scan_with_charts(
    request: ScanRequest,
    user: dict = Depends(get_current_user_required),
) -> dict:
    tickers = [t.upper() for t in request.tickers]
    results, errors, details = scan_tickers(
        tickers,
        min_rr=request.min_rr,
        analysis_period=request.analysis_period,
    )
    results = apply_strategy_decisions(
        results,
        analysis_period=request.analysis_period,
        min_rr=request.min_rr,
    )
    result_by_ticker = {result.ticker: result for result in results}
    for detail in details:
        detail.result = result_by_ticker.get(detail.result.ticker, detail.result)
    charts = {}
    saved = []
    user_id = user.get("id")
    user_label = user.get("email") or request.user_label

    def build_chart(detail):
        path = write_scan_chart(detail, CHART_DIR)
        return detail.result.ticker, f"/charts/{path.name}"

    if details:
        max_workers = max(1, min(int(os.getenv("MARKET_LENS_CHART_WORKERS", "6")), len(details)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_by_ticker = {executor.submit(build_chart, detail): detail.result.ticker for detail in details}
            for future in as_completed(future_by_ticker):
                ticker, chart_url = future.result()
                charts[ticker] = chart_url

    for detail in details:
        saved_setup = save_setup(
            detail.result,
            analysis_period=request.analysis_period,
            chart_url=charts.get(detail.result.ticker),
            source="auto",
            user_label=user_label,
            session_id=request.session_id,
            user_id=user_id,
        )
        if saved_setup:
            saved.append(saved_setup)
    return {
        "results": [result.model_dump() for result in results],
        "errors": errors,
        "charts": charts,
        "analysis_period": request.analysis_period,
        "saved_setups": saved,
    }


@app.get("/setups")
async def get_saved_setups(
    limit: int = Query(default=80, ge=1, le=200),
    status: str | None = Query(default=None, pattern="^(OPEN|TARGET1|TARGET2|STOPPED)$"),
    source: str | None = Query(default=None, pattern="^(auto|manual)$"),
    session_id: str | None = Query(default=None, max_length=80),
    user: dict = Depends(get_current_user_required),
) -> dict:
    user_id = user.get("id")
    return {
        "setups": list_setups(
            limit=limit,
            status=status,
            source=source,
            session_id=session_id,
            user_id=user_id,
        )
    }


@app.post("/setups")
async def create_saved_setup(
    request: SaveSetupRequest,
    user: dict = Depends(get_current_user_required),
) -> dict:
    if using_external_storage() and not user.get("id"):
        raise HTTPException(status_code=401, detail="Sign in required to save setups.")

    saved = save_setup(
        request.result,
        analysis_period=request.analysis_period,
        chart_url=request.chart_url,
        source="manual",
        user_label=user.get("email") or request.user_label,
        session_id=request.session_id,
        user_id=user.get("id"),
    )
    if saved is None:
        raise HTTPException(status_code=400, detail="Only trade setups can be saved.")
    return {"setup": saved}


@app.post("/setups/{setup_id}/refresh")
async def refresh_saved_setup(
    setup_id: str,
    user: dict = Depends(get_current_user_required),
) -> dict:
    try:
        return {"setup": refresh_setup(setup_id, user_id=user.get("id"))}
    except KeyError:
        raise HTTPException(status_code=404, detail="Saved setup not found") from None


@app.get("/config")
async def get_config() -> dict:
    return load_config()


@app.get("/auth/config")
async def get_auth_config() -> dict:
    publishable_key = supabase_publishable_key()
    return {
        "supabase_url": os.getenv("SUPABASE_URL", ""),
        "publishable_key": publishable_key,
        "enabled": bool(os.getenv("SUPABASE_URL") and publishable_key),
    }


@app.get("/watchlists")
async def get_watchlists() -> dict:
    return {"watchlists": list_watchlists()}


@app.get("/smart-universe")
async def get_smart_universe(
    limit: int = Query(default=35, ge=5, le=100),
    max_per_sector: int = Query(default=5, ge=1, le=20),
    analysis_period: str = Query(default="6mo", pattern="^(3mo|6mo|1y|2y)$"),
) -> dict:
    return build_smart_universe(
        analysis_period=analysis_period,
        limit=limit,
        max_per_sector=max_per_sector,
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
