import asyncio
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.agent_dashboard import (
    TRACKER_NAME,
    build_agent_dashboard,
    build_decision_diagnostics,
    build_position_attention,
    build_position_timeline,
    build_risk_dashboard,
    build_system_health,
    compact_agent_dashboard_payload,
    dashboard_section_payload,
    load_period_summary,
    parse_timestamp,
    sanitize_dashboard_media_urls,
    to_float,
    with_position_calculations,
)
from app.auth import auth_is_configured, auth_is_open, get_current_user_required, supabase_publishable_key
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
from app.results_sync import (
    sync_agent_results_if_enabled,
    sync_dashboard_snapshot_assets_if_enabled,
    sync_dashboard_snapshot_if_enabled,
)
from app.scanner import scan_ticker_detail, scan_tickers
from app.scan_trigger import (
    dispatch_agent_scan,
    mark_scan_dispatched,
    scan_already_dispatched,
    scan_dispatch_budget_reason,
    scan_schedule_decision,
    scan_trigger_configured,
)
from app.smart_universe import build_curated_universe_fallback, build_smart_universe
from app.storage import init_storage, list_setups, refresh_setup, save_setup, using_external_storage
from app.strategy import apply_strategy_decisions
from app.telegram_notifications import (
    dashboard_url_from_env,
    format_position_attention_message,
    send_telegram_chart_photo,
    send_telegram_message,
    telegram_configured,
)
from app.watchlists import list_watchlists

app = FastAPI(title="Market Lens", version="0.1.0", description="Swing trade scanner")

PROJECT_ROOT = Path(__file__).parent.parent
STATIC_DIR = Path(__file__).parent / "static"
CHART_DIR = PROJECT_ROOT / "charts"
AGENT_RESULTS_DIR = PROJECT_ROOT / "agent_results"
AGENT_TRACKER_DIR = PROJECT_ROOT / "agent_tracker"
DASHBOARD_SNAPSHOT_PATH = AGENT_RESULTS_DIR / "dashboard_snapshot.json"
CHART_DIR.mkdir(exist_ok=True)
AGENT_RESULTS_DIR.mkdir(exist_ok=True)
init_storage()

LIVE_PRICE_CACHE_TTL_SECONDS = int(os.getenv("MARKET_LENS_LIVE_PRICE_CACHE_TTL", "45"))
_LIVE_PRICE_CACHE: dict[str, tuple[float, float, str, float, float]] = {}
DASHBOARD_CACHE_TTL_SECONDS = int(os.getenv("MARKET_LENS_AGENT_DASHBOARD_CACHE_TTL", "120"))
_AGENT_DASHBOARD_CACHE: dict[tuple[str, int, int], tuple[float, dict]] = {}
_POSITION_ATTENTION_ALERT_AT: dict[str, float] = {}

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/charts", StaticFiles(directory=CHART_DIR), name="charts")
app.mount("/agent-results", StaticFiles(directory=AGENT_RESULTS_DIR), name="agent-results")


@app.get("/", include_in_schema=False)
async def ui() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> PlainTextResponse:
    return PlainTextResponse("", status_code=204)


@app.get("/agent", include_in_schema=False)
async def agent_ui() -> FileResponse:
    return FileResponse(STATIC_DIR / "agent.html")


@app.get("/agent/", include_in_schema=False)
async def agent_ui_trailing() -> FileResponse:
    return FileResponse(STATIC_DIR / "agent.html")


def dashboard_cache_key(selected_date: str | None = None) -> tuple[str, int, int]:
    tracker_path = AGENT_TRACKER_DIR / TRACKER_NAME
    try:
        stat = tracker_path.stat()
        return (selected_date or "", stat.st_mtime_ns, stat.st_size)
    except OSError:
        return (selected_date or "", 0, 0)


def cached_agent_dashboard(selected_date: str | None = None) -> dict:
    sync_status = sync_agent_results_if_enabled(PROJECT_ROOT)
    key = dashboard_cache_key(selected_date)
    now = time.time()
    cached = _AGENT_DASHBOARD_CACHE.get(key)
    if cached and now - cached[0] <= DASHBOARD_CACHE_TTL_SECONDS:
        cached[1]["results_sync"] = sync_status
        return cached[1]

    dashboard = build_agent_dashboard(PROJECT_ROOT, selected_date=selected_date)
    dashboard["results_sync"] = sync_status
    _AGENT_DASHBOARD_CACHE.clear()
    _AGENT_DASHBOARD_CACHE[key] = (now, dashboard)
    return dashboard


def current_agent_dashboard() -> dict:
    sync_status = sync_dashboard_snapshot_if_enabled(PROJECT_ROOT)
    if DASHBOARD_SNAPSHOT_PATH.exists():
        try:
            dashboard = json.loads(DASHBOARD_SNAPSHOT_PATH.read_text(encoding="utf-8"))
            if isinstance(dashboard, dict) and dashboard.get("status") == "ok":
                asset_sync_status = sync_dashboard_snapshot_assets_if_enabled(PROJECT_ROOT, dashboard)
                dashboard = enrich_agent_dashboard_snapshot(dashboard)
                dashboard["results_sync"] = {**sync_status, "asset_sync": asset_sync_status}
                return dashboard
        except Exception:
            pass
    return cached_agent_dashboard()


def enrich_agent_dashboard_snapshot(dashboard: dict) -> dict:
    """Backfill lightweight analytics when Render is serving an older snapshot."""
    dashboard = sanitize_dashboard_media_urls(dashboard, PROJECT_ROOT)
    latest_setups = dashboard.get("latest_setups") if isinstance(dashboard.get("latest_setups"), list) else []
    decision_diagnostics = dashboard.get("decision_diagnostics")
    if (
        not isinstance(decision_diagnostics, dict)
        or "drilldowns" not in decision_diagnostics
        or "why_no_buys" not in decision_diagnostics
        or "watch_ready_funnel" not in decision_diagnostics
        or "entry_blockers_summary" not in decision_diagnostics
        or "closest_to_entry" not in decision_diagnostics
    ):
        dashboard["decision_diagnostics"] = build_decision_diagnostics(latest_setups)

    positions = dashboard.get("open_positions") if isinstance(dashboard.get("open_positions"), list) else []
    summary = dashboard.get("summary") if isinstance(dashboard.get("summary"), dict) else {}
    latest_decisions = dashboard.get("latest_decisions") if isinstance(dashboard.get("latest_decisions"), list) else []
    if "risk_dashboard" not in dashboard:
        dashboard["risk_dashboard"] = build_risk_dashboard(positions, summary, latest_decisions)
    if "position_timeline" not in dashboard:
        dashboard["position_timeline"] = build_position_timeline(positions)

    latest_run = dashboard.get("latest_run") if isinstance(dashboard.get("latest_run"), dict) else {}
    latest_dt = parse_timestamp(latest_run.get("timestamp"))
    summary_dir = AGENT_RESULTS_DIR / "summaries"
    if "daily_summary" not in dashboard:
        dashboard["daily_summary"] = load_period_summary(summary_dir, "daily", latest_dt)
    if "weekly_summary" not in dashboard:
        dashboard["weekly_summary"] = load_period_summary(summary_dir, "weekly", latest_dt)
    if "system_health" not in dashboard:
        recent_runs = dashboard.get("recent_runs") if isinstance(dashboard.get("recent_runs"), list) else []
        latest_update = recent_runs[-1] if recent_runs else latest_run
        latest_scan_update = next(
            (
                update
                for update in reversed(recent_runs)
                if not str(update.get("run_id") or "").startswith("monitor_")
            ),
            latest_run,
        )
        latest_monitor_update = next(
            (
                update
                for update in reversed(recent_runs)
                if str(update.get("run_id") or "").startswith("monitor_")
            ),
            {},
        )
        dashboard["system_health"] = build_system_health(
            tracker_path=AGENT_TRACKER_DIR / TRACKER_NAME,
            updates=recent_runs,
            latest_update=latest_update,
            latest_scan_update=latest_scan_update,
            latest_monitor_update=latest_monitor_update,
        )
    return dashboard


def monitor_agent_dashboard() -> dict:
    """Load only the lightweight dashboard snapshot needed for TP/SL checks.

    The cron monitor is called every minute and must stay below cron-job.org's
    30-second timeout. Avoid syncing chart assets or rebuilding the full Excel
    dashboard here; the public `/agent/data` endpoint handles rich UI assets.
    """
    sync_status = sync_dashboard_snapshot_if_enabled(PROJECT_ROOT)
    if DASHBOARD_SNAPSHOT_PATH.exists():
        try:
            dashboard = json.loads(DASHBOARD_SNAPSHOT_PATH.read_text(encoding="utf-8"))
            if isinstance(dashboard, dict) and dashboard.get("status") == "ok":
                dashboard["results_sync"] = sync_status
                return dashboard
        except Exception:
            pass
    return cached_agent_dashboard()


@app.get("/agent/data")
async def get_agent_dashboard(
    date: str | None = Query(default=None),
    compact: bool = Query(default=True),
    section: str | None = Query(default=None, pattern="^(actions|trades|diagnostics)$"),
    diagnostic_key: str | None = Query(default=None, max_length=40),
    sector: str | None = Query(default=None, max_length=80),
    setup_type: str | None = Query(default=None, max_length=80),
    chart_filter: str = Query(default="all", pattern="^(all|with_chart|missing_chart)$"),
    confirmation: str = Query(default="all", pattern="^(all|passed|missing)$"),
    sort: str = Query(default="closest", pattern="^(closest|score|rr|ticker)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=100),
) -> dict:
    dashboard = current_agent_dashboard() if not date else cached_agent_dashboard(date)
    if section:
        return dashboard_section_payload(
            dashboard,
            section=section,
            offset=offset,
            limit=limit,
            diagnostic_key=diagnostic_key,
            sector=sector,
            setup_type=setup_type,
            chart_filter=chart_filter,
            confirmation=confirmation,
            sort=sort,
        )
    if compact:
        return compact_agent_dashboard_payload(dashboard, action_limit=limit, trade_limit=limit)
    if dashboard.get("status") == "ok":
        dashboard["payload"] = {
            "mode": "full",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    return dashboard


@app.post("/agent/diagnostic-chart")
async def create_agent_diagnostic_chart(
    ticker: str = Query(..., min_length=1, max_length=12),
    diagnostic_key: str = Query(default="WATCH_READY", max_length=40),
    analysis_period: str = Query(default="6mo", pattern="^(3mo|6mo|1y|2y)$"),
    min_rr: float = Query(default=2.0, ge=0.1, le=10),
) -> dict:
    normalized_ticker = ticker.upper().strip()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,12}", normalized_ticker):
        raise HTTPException(status_code=400, detail="Invalid ticker.")

    dashboard = current_agent_dashboard()
    if dashboard.get("status") != "ok":
        raise HTTPException(status_code=503, detail="Agent dashboard data unavailable.")

    item = find_diagnostic_item(dashboard, ticker=normalized_ticker, diagnostic_key=diagnostic_key)
    if not item:
        raise HTTPException(status_code=404, detail="Ticker is not in the current diagnostic bucket.")

    existing_url = str(item.get("chart_url") or "")
    existing_path = agent_result_path_from_url(existing_url)
    if existing_path and existing_path.exists():
        return {"status": "ok", "ticker": normalized_ticker, "chart_url": existing_url, "generated": False}

    run_id = sanitize_run_id(str((dashboard.get("latest_run") or {}).get("run_id") or "latest"))
    chart_dir = AGENT_RESULTS_DIR / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    destination = chart_dir / f"market_lens_diagnostic_{run_id}_{normalized_ticker.lower()}.png"
    chart_url = f"/agent-results/charts/{destination.name}"
    if not destination.exists():
        try:
            detail = scan_ticker_detail(normalized_ticker, min_rr=min_rr, analysis_period=analysis_period)
            update: dict[str, object] = {
                "setup_type": item.get("setup_type") or detail.result.setup_type,
                "score": float(item.get("setup_score") or detail.result.score or 0),
                "current_price": float(item.get("current_price_usd") or detail.result.current_price or 0),
                "risk_reward": float(item.get("weighted_net_rr") or item.get("net_rr") or detail.result.risk_reward or 0),
            }
            buy_low = item.get("buy_zone_low")
            buy_high = item.get("buy_zone_high")
            if buy_low and buy_high:
                update["buy_zone"] = (float(buy_low), float(buy_high))
            for source_key, target_key in (
                ("stop_loss", "stop_loss"),
                ("target_1", "target_1"),
                ("target_2", "target_2"),
            ):
                if item.get(source_key):
                    update[target_key] = float(item[source_key])
            detail.result = detail.result.model_copy(update=update)
            generated = write_scan_chart(detail, chart_dir)
            if generated != destination:
                destination.write_bytes(generated.read_bytes())
                try:
                    generated.unlink()
                except OSError:
                    pass
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Chart generation failed: {exc}") from exc

    attach_diagnostic_chart_to_snapshot(normalized_ticker, chart_url)
    return {"status": "ok", "ticker": normalized_ticker, "chart_url": chart_url, "generated": True}


def find_diagnostic_item(dashboard: dict, *, ticker: str, diagnostic_key: str) -> dict | None:
    diagnostics = dashboard.get("decision_diagnostics") if isinstance(dashboard.get("decision_diagnostics"), dict) else {}
    drilldowns = diagnostics.get("drilldowns") if isinstance(diagnostics.get("drilldowns"), dict) else {}
    buckets = []
    if diagnostic_key and isinstance(drilldowns.get(diagnostic_key), list):
        buckets.append(drilldowns.get(diagnostic_key) or [])
    buckets.extend(bucket for bucket in drilldowns.values() if isinstance(bucket, list))
    for bucket in buckets:
        for item in bucket:
            if str(item.get("ticker") or "").upper() == ticker:
                return item
    return None


def agent_result_path_from_url(url: str) -> Path | None:
    if not url.startswith("/agent-results/"):
        return None
    relative = url.split("/agent-results/", 1)[1].split("?", 1)[0].split("#", 1)[0]
    path = (AGENT_RESULTS_DIR / relative).resolve()
    try:
        path.relative_to(AGENT_RESULTS_DIR.resolve())
    except ValueError:
        return None
    return path


def attach_diagnostic_chart_to_snapshot(ticker: str, chart_url: str) -> None:
    if not DASHBOARD_SNAPSHOT_PATH.exists():
        return
    try:
        dashboard = json.loads(DASHBOARD_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(dashboard, dict):
        return
    update_chart_url_for_ticker(dashboard, ticker=ticker, chart_url=chart_url)
    try:
        DASHBOARD_SNAPSHOT_PATH.write_text(json.dumps(dashboard, default=str, separators=(",", ":")), encoding="utf-8")
    except OSError:
        return


def update_chart_url_for_ticker(value: object, *, ticker: str, chart_url: str) -> None:
    if isinstance(value, dict):
        if str(value.get("ticker") or "").upper() == ticker:
            value["chart_url"] = chart_url
        for child in value.values():
            update_chart_url_for_ticker(child, ticker=ticker, chart_url=chart_url)
    elif isinstance(value, list):
        for child in value:
            update_chart_url_for_ticker(child, ticker=ticker, chart_url=chart_url)


def sanitize_run_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "_", value).strip("_")
    return cleaned[:48] or "latest"


@app.get("/agent/live-prices")
async def get_agent_live_prices() -> dict:
    dashboard = current_agent_dashboard()
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
            updated["live_price_source"] = "1m intraday/prepost"
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
        "position_attention": build_position_attention(refreshed),
        "prices": prices,
        "warnings": warnings,
    }


@app.post("/agent/trigger-monitor")
async def trigger_position_monitor(request: MonitorTriggerRequest) -> dict:
    trigger_configured = monitor_trigger_configured()
    dashboard = monitor_agent_dashboard()
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
        live_price, source_time, live_high, live_low = fetch_live_quote(ticker)
    except Exception as exc:
        return {
            "status": "skipped",
            "triggered": False,
            "trigger_configured": trigger_configured,
            "reason": f"Live price unavailable: {exc}",
        }

    event = detect_live_monitor_event(position, live_price, live_high=live_high, live_low=live_low)
    if event is None:
        return {
            "status": "skipped",
            "triggered": False,
            "trigger_configured": trigger_configured,
            "ticker": ticker,
            "live_price": round(live_price, 4),
            "live_high": round(live_high, 4),
            "live_low": round(live_low, 4),
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
            "live_high": round(live_high, 4),
            "live_low": round(live_low, 4),
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
            "live_high": round(live_high, 4),
            "live_low": round(live_low, 4),
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
        "live_high": round(live_high, 4),
        "live_low": round(live_low, 4),
        "live_price_updated_at": source_time,
        "reason": event.reason,
        "dispatch": compact_dispatch_payload(dispatch),
    }


@app.get("/agent/monitor-live")
@app.post("/agent/monitor-live")
async def monitor_live_positions(
    x_monitor_secret: str | None = Header(default=None, alias="X-Market-Lens-Cron-Secret"),
    secret: str | None = Query(default=None, max_length=160),
    compact: bool = Query(default=True),
):
    protection = validate_monitor_cron_secret(x_monitor_secret, secret)
    trigger_configured = monitor_trigger_configured()
    dashboard = monitor_agent_dashboard()
    if dashboard.get("status") != "ok":
        return compact_cron_response({
            "status": "skipped",
            "triggered": False,
            "trigger_configured": trigger_configured,
            "protected": protection["protected"],
            "reason": "Agent dashboard data unavailable.",
        }, compact)

    positions = dashboard.get("open_positions", [])
    if not positions:
        return compact_cron_response({
            "status": "skipped",
            "triggered": False,
            "trigger_configured": trigger_configured,
            "protected": protection["protected"],
            "open_positions": 0,
            "reason": "No open positions to monitor.",
        }, compact)

    checked = []
    detected_events = []
    attention_alerts = []
    warnings = {}
    dispatchable_event = None
    rate_limited = []

    for position in positions:
        ticker = str(position.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        try:
            live_price, source_time, live_high, live_low = fetch_live_quote(ticker)
        except Exception as exc:
            warnings[ticker] = str(exc)
            checked.append({"ticker": ticker, "status": "price_unavailable", "warning": str(exc)})
            continue

        event = detect_live_monitor_event(position, live_price, live_high=live_high, live_low=live_low)
        checked_item = {
            "ticker": ticker,
            "live_price": round(live_price, 4),
            "live_high": round(live_high, 4),
            "live_low": round(live_low, 4),
            "live_price_updated_at": source_time,
            "status": "event_detected" if event else "no_event",
        }
        checked.append(checked_item)
        if not event:
            attention_alert = detect_position_attention_alert(position, live_price, live_high, live_low)
            if attention_alert:
                sent_alert = send_position_attention_alert(position, attention_alert, source_time)
                attention_alerts.append(sent_alert)
                checked_item["status"] = "attention_alert" if sent_alert.get("sent") else "near_threshold"
                checked_item["attention_alert"] = {
                    "event_type": attention_alert.get("event_type"),
                    "threshold": attention_alert.get("threshold"),
                    "distance_pct": attention_alert.get("distance_pct"),
                    "status": sent_alert.get("status"),
                }
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
        return compact_cron_response({
            "status": "ok",
            "triggered": False,
            "trigger_configured": trigger_configured,
            "protected": protection["protected"],
            "open_positions": len(positions),
            "positions_checked": len(checked),
            "checked": checked,
            "attention_alerts": attention_alerts,
            "warnings": warnings,
            "reason": "No open position touched stop loss or targets.",
        }, compact)

    if not trigger_configured:
        return compact_cron_response({
            "status": "not_configured",
            "triggered": False,
            "trigger_configured": False,
            "protected": protection["protected"],
            "open_positions": len(positions),
            "positions_checked": len(checked),
            "detected_events": detected_events,
            "attention_alerts": attention_alerts,
            "warnings": warnings,
            "reason": "GitHub monitor trigger token is not configured on the server.",
        }, compact)

    if dispatchable_event is None:
        return compact_cron_response({
            "status": "rate_limited",
            "triggered": False,
            "trigger_configured": True,
            "protected": protection["protected"],
            "open_positions": len(positions),
            "positions_checked": len(checked),
            "detected_events": detected_events,
            "rate_limited": rate_limited,
            "attention_alerts": attention_alerts,
            "warnings": warnings,
            "reason": "Monitor trigger cooldown is active.",
        }, compact)

    try:
        dispatch = await dispatch_position_monitor(dispatchable_event, source="agent-server-live-monitor")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return compact_cron_response({
        "status": "triggered",
        "triggered": True,
        "trigger_configured": True,
        "protected": protection["protected"],
        "open_positions": len(positions),
        "positions_checked": len(checked),
        "detected_events": detected_events,
        "dispatched_event": live_monitor_event_payload(dispatchable_event),
        "attention_alerts": attention_alerts,
        "warnings": warnings,
        "reason": dispatchable_event.reason,
        "dispatch": compact_dispatch_payload(dispatch),
    }, compact)


@app.get("/agent/trigger-scan")
@app.post("/agent/trigger-scan")
async def trigger_agent_scan(
    x_cron_secret: str | None = Header(default=None, alias="X-Market-Lens-Cron-Secret"),
    secret: str | None = Query(default=None, max_length=160),
    force: bool = Query(default=False),
    compact: bool = Query(default=True),
):
    protection = validate_agent_cron_secret(x_cron_secret, secret)
    force_applied = force and trigger_scan_force_enabled()
    decision = scan_schedule_decision(force=force_applied)
    trigger_configured = scan_trigger_configured()

    base_payload = {
        "triggered": False,
        "trigger_configured": trigger_configured,
        "protected": protection["protected"],
        "force_requested": force,
        "force_applied": force_applied,
        "local_date": decision.local_date,
        "local_time": decision.local_time,
        "local_weekday": decision.local_weekday,
        "scan_key": decision.scan_key,
        "next_scan_at": decision.next_scan_at,
    }

    if not decision.should_run:
        return compact_cron_response({
            **base_payload,
            "status": "skipped",
            "reason": decision.reason,
        }, compact)

    if not force_applied and scan_already_dispatched(decision.scan_key):
        return compact_cron_response({
            **base_payload,
            "status": "skipped",
            "reason": "This scan slot was already dispatched by the server.",
        }, compact)

    budget_reason = scan_dispatch_budget_reason(decision.scan_key)
    if budget_reason:
        return compact_cron_response({
            **base_payload,
            "status": "skipped",
            "reason": budget_reason,
        }, compact)

    if not trigger_configured:
        return compact_cron_response({
            **base_payload,
            "status": "not_configured",
            "reason": "GitHub agent trigger token is not configured on the server.",
        }, compact)

    try:
        dispatch = await dispatch_agent_scan(source="agent-server-scan-scheduler")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    mark_scan_dispatched(decision.scan_key)
    return compact_cron_response({
        **base_payload,
        "status": "triggered",
        "triggered": True,
        "reason": decision.reason,
        "dispatch": compact_dispatch_payload(dispatch),
    }, compact)


def validate_monitor_cron_secret(header_secret: str | None, query_secret: str | None) -> dict:
    expected = os.getenv("MARKET_LENS_MONITOR_CRON_SECRET") or os.getenv("MARKET_LENS_CRON_SECRET") or ""
    if not expected:
        return {"protected": False}
    provided = header_secret or query_secret or ""
    if provided != expected:
        raise HTTPException(status_code=401, detail="Invalid monitor cron secret.")
    return {"protected": True}


def validate_agent_cron_secret(header_secret: str | None, query_secret: str | None) -> dict:
    expected = (
        os.getenv("MARKET_LENS_AGENT_CRON_SECRET")
        or os.getenv("MARKET_LENS_MONITOR_CRON_SECRET")
        or os.getenv("MARKET_LENS_CRON_SECRET")
        or ""
    )
    if not expected:
        return {"protected": False}
    provided = header_secret or query_secret or ""
    if provided != expected:
        raise HTTPException(status_code=401, detail="Invalid agent cron secret.")
    return {"protected": True}


def detect_position_attention_alert(
    position: dict,
    live_price: float,
    live_high: float | None,
    live_low: float | None,
) -> dict | None:
    if not position_attention_alert_enabled() or live_price <= 0:
        return None
    ticker = str(position.get("ticker") or "").upper().strip()
    if not ticker:
        return None

    threshold_pct = position_attention_threshold_pct()
    high = float(live_high or live_price)
    low = float(live_low or live_price)
    entry = to_float(position.get("entry_price_usd") or position.get("entry_price"))
    stop = to_float(position.get("stop_loss"))
    target_1 = to_float(position.get("target_1"))
    target_2 = to_float(position.get("target_2"))
    partial_taken = bool(position.get("partial_taken")) or "partial" in str(position.get("notes") or "").lower()
    candidates = []

    if stop > 0 and low > stop:
        distance_pct = (low - stop) / live_price * 100
        label = "Breakeven stop" if entry > 0 and abs(stop - entry) / entry <= 0.001 else "Stop loss"
        candidates.append(
            attention_alert_payload(
                ticker=ticker,
                event_type="EXIT_STOP",
                label=label,
                threshold=stop,
                distance_pct=distance_pct,
                live_price=live_price,
                live_high=high,
                live_low=low,
                side="below",
            )
        )
    if target_2 > 0 and high < target_2:
        candidates.append(
            attention_alert_payload(
                ticker=ticker,
                event_type="TAKE_PROFIT",
                label="Target 2",
                threshold=target_2,
                distance_pct=(target_2 - high) / live_price * 100,
                live_price=live_price,
                live_high=high,
                live_low=low,
                side="above",
            )
        )
    if target_1 > 0 and high < target_1 and not partial_taken:
        candidates.append(
            attention_alert_payload(
                ticker=ticker,
                event_type="TAKE_PARTIAL_PROFIT",
                label="Target 1",
                threshold=target_1,
                distance_pct=(target_1 - high) / live_price * 100,
                live_price=live_price,
                live_high=high,
                live_low=low,
                side="above",
            )
        )

    eligible = [candidate for candidate in candidates if 0 <= to_float(candidate.get("distance_pct"), 999) <= threshold_pct]
    if not eligible:
        return None
    eligible.sort(key=lambda item: to_float(item.get("distance_pct"), 999))
    return eligible[0]


def attention_alert_payload(
    *,
    ticker: str,
    event_type: str,
    label: str,
    threshold: float,
    distance_pct: float,
    live_price: float,
    live_high: float,
    live_low: float,
    side: str,
) -> dict:
    distance = round(max(0.0, distance_pct), 3)
    direction = "above" if side == "above" else "below"
    return {
        "ticker": ticker,
        "event_type": event_type,
        "label": label,
        "threshold": round(threshold, 4),
        "distance_pct": distance,
        "live_price": round(live_price, 4),
        "live_high": round(live_high, 4),
        "live_low": round(live_low, 4),
        "reason": f"{ticker} is {distance:.2f}% from {label} {threshold:.2f} ({direction} current 1m range).",
    }


def send_position_attention_alert(position: dict, alert: dict, source_time: str) -> dict:
    key = f"{alert.get('ticker')}:{alert.get('event_type')}"
    now = time.monotonic()
    cooldown = position_attention_cooldown_seconds()
    last_sent = _POSITION_ATTENTION_ALERT_AT.get(key, 0.0)
    if cooldown > 0 and now - last_sent < cooldown:
        return {**alert, "sent": False, "status": "cooldown", "reason": f"Telegram attention cooldown active ({cooldown}s)."}

    if not position_attention_alert_enabled():
        return {**alert, "sent": False, "status": "disabled", "reason": "Position attention Telegram alerts are disabled."}
    if not telegram_configured():
        return {**alert, "sent": False, "status": "not_configured", "reason": "Telegram is not configured."}

    dashboard_url = dashboard_url_from_env()
    message = format_position_attention_message(
        position=position,
        alert=alert,
        timestamp=source_time,
        dashboard_url=dashboard_url,
    )
    message_result = send_telegram_message(message)
    chart_result = send_telegram_chart_photo(
        position.get("chart_url") or position.get("screenshot_url") or "",
        ticker=alert.get("ticker") or position.get("ticker"),
        dashboard_url=dashboard_url,
    )
    if message_result.sent:
        _POSITION_ATTENTION_ALERT_AT[key] = now
    return {
        **alert,
        "sent": bool(message_result.sent),
        "status": message_result.status,
        "message_status": message_result.status,
        "chart_status": chart_result.status,
        "reason": alert.get("reason", ""),
    }


def position_attention_alert_enabled() -> bool:
    return os.getenv("MARKET_LENS_POSITION_ATTENTION_TELEGRAM_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def position_attention_threshold_pct() -> float:
    try:
        return max(0.0, float(os.getenv("MARKET_LENS_POSITION_ATTENTION_THRESHOLD_PCT", "1.0")))
    except ValueError:
        return 1.0


def position_attention_cooldown_seconds() -> int:
    try:
        return max(0, int(os.getenv("MARKET_LENS_POSITION_ATTENTION_COOLDOWN_SECONDS", "1800")))
    except ValueError:
        return 1800


def live_monitor_event_payload(event) -> dict:
    return {
        "ticker": event.ticker,
        "event_type": event.event_type,
        "threshold": event.threshold,
        "live_price": round(event.live_price, 4),
        "live_high": round(event.live_high, 4),
        "live_low": round(event.live_low, 4),
        "reason": event.reason,
    }


def fetch_live_price(ticker: str) -> tuple[float, str]:
    price, source_time, _high, _low = fetch_live_quote(ticker)
    return price, source_time


def fetch_live_quote(ticker: str) -> tuple[float, str, float, float]:
    symbol = ticker.upper()
    now = time.monotonic()
    cached = _LIVE_PRICE_CACHE.get(symbol)
    if cached and now - cached[0] < LIVE_PRICE_CACHE_TTL_SECONDS:
        return cached[1], cached[2], cached[3], cached[4]

    errors = []
    for period, include_prepost in (("5d", True), ("1d", False), ("5d", False)):
        try:
            frame = fetch_intraday_frame(
                symbol,
                period=period,
                interval="1m",
                include_prepost=include_prepost,
            )
            break
        except Exception as exc:
            errors.append(str(exc))
    else:
        raise ValueError(f"{symbol}: live intraday data unavailable ({'; '.join(errors)})")

    if frame.empty:
        raise ValueError(f"{symbol}: no live intraday rows returned")
    close = frame["Close"].dropna()
    if close.empty:
        raise ValueError(f"{symbol}: live intraday rows have no close prices")
    latest_row = frame.loc[close.index[-1]]
    price = float(close.iloc[-1])
    high = float(latest_row.get("High") or price)
    low = float(latest_row.get("Low") or price)
    source_time = close.index[-1].isoformat()
    _LIVE_PRICE_CACHE[symbol] = (now, price, source_time, high, low)
    return price, source_time, high, low


def compact_dispatch_payload(dispatch: dict | None) -> dict:
    if not isinstance(dispatch, dict):
        return {}
    allowed = ("github_status", "workflow", "ref", "repo", "source")
    return {key: dispatch[key] for key in allowed if key in dispatch}


def compact_cron_response(payload: dict, compact: bool) -> dict | PlainTextResponse:
    if not compact:
        return payload
    status = str(payload.get("status") or "ok").replace("\n", " ")[:40]
    triggered = "true" if payload.get("triggered") else "false"
    reason = str(payload.get("reason") or "").replace("\n", " ")[:180]
    return PlainTextResponse(f"status={status};triggered={triggered};reason={reason}\n")


def trigger_scan_force_enabled() -> bool:
    return os.getenv("MARKET_LENS_ALLOW_TRIGGER_SCAN_FORCE", "").strip().lower() in {"1", "true", "yes", "on"}


@app.get("/agent/tracker")
async def get_agent_tracker() -> FileResponse:
    sync_agent_results_if_enabled(PROJECT_ROOT)
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

    if details and request.include_charts:
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
    if using_external_storage() and not auth_is_open() and not user.get("id"):
        raise HTTPException(status_code=401, detail="Sign in required to save setups.")

    saved = save_setup(
        request.result,
        analysis_period=request.analysis_period,
        chart_url=request.chart_url,
        source="auto" if auth_is_open() else "manual",
        user_label=user.get("email") or request.user_label or "open-access",
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
        "enabled": auth_is_configured(),
        "mode": "open" if auth_is_open() else "supabase",
    }


@app.get("/watchlists")
async def get_watchlists() -> dict:
    return {"watchlists": list_watchlists()}


@app.get("/smart-universe")
async def get_smart_universe(
    limit: int = Query(default=35, ge=5, le=300),
    max_per_sector: int = Query(default=5, ge=1, le=20),
    analysis_period: str = Query(default="6mo", pattern="^(3mo|6mo|1y|2y)$"),
) -> dict:
    timeout_seconds = float(os.getenv("MARKET_LENS_SMART_UNIVERSE_TIMEOUT_SECONDS", "25"))
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                build_smart_universe,
                analysis_period=analysis_period,
                limit=limit,
                max_per_sector=max_per_sector,
            ),
            timeout=timeout_seconds,
        )
    except Exception as exc:
        return build_curated_universe_fallback(
            analysis_period=analysis_period,
            limit=limit,
            max_per_sector=max_per_sector,
            reason=f"Smart Universe fallback used: {type(exc).__name__}",
        )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
