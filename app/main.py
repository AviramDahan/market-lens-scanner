import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.auth import get_current_user_optional
from app.charts import write_scan_chart
from app.config import load_config
from app.models import SaveSetupRequest, ScanRequest, ScanResponse
from app.scanner import scan_tickers
from app.storage import init_storage, list_setups, refresh_setup, save_setup, using_external_storage
from app.watchlists import list_watchlists

app = FastAPI(title="Market Lens", version="0.1.0", description="Swing trade scanner")

PROJECT_ROOT = Path(__file__).parent.parent
STATIC_DIR = Path(__file__).parent / "static"
CHART_DIR = PROJECT_ROOT / "charts"
CHART_DIR.mkdir(exist_ok=True)
init_storage()

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/charts", StaticFiles(directory=CHART_DIR), name="charts")


@app.get("/", include_in_schema=False)
async def ui() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/scan", response_model=ScanResponse)
async def scan(request: ScanRequest) -> ScanResponse:
    tickers = [t.upper() for t in request.tickers]
    results, errors, _ = scan_tickers(
        tickers,
        min_rr=request.min_rr,
        analysis_period=request.analysis_period,
    )
    return ScanResponse(results=results, errors=errors)


@app.post("/ui/scan")
async def scan_with_charts(
    request: ScanRequest,
    user: dict | None = Depends(get_current_user_optional),
) -> dict:
    tickers = [t.upper() for t in request.tickers]
    results, errors, details = scan_tickers(
        tickers,
        min_rr=request.min_rr,
        analysis_period=request.analysis_period,
    )
    charts = {}
    saved = []
    user_id = user.get("id") if user else None
    user_label = user.get("email") if user else request.user_label
    for detail in details:
        path = write_scan_chart(detail, CHART_DIR)
        charts[detail.result.ticker] = f"/charts/{path.name}"
        saved_setup = save_setup(
            detail.result,
            analysis_period=request.analysis_period,
            chart_url=charts[detail.result.ticker],
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
    user: dict | None = Depends(get_current_user_optional),
) -> dict:
    user_id = user.get("id") if user else None
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
    user: dict | None = Depends(get_current_user_optional),
) -> dict:
    if using_external_storage() and user is None:
        raise HTTPException(status_code=401, detail="Sign in required to save setups.")

    saved = save_setup(
        request.result,
        analysis_period=request.analysis_period,
        chart_url=request.chart_url,
        source="manual",
        user_label=user.get("email") if user else request.user_label,
        session_id=request.session_id,
        user_id=user.get("id") if user else None,
    )
    if saved is None:
        raise HTTPException(status_code=400, detail="Only trade setups can be saved.")
    return {"setup": saved}


@app.post("/setups/{setup_id}/refresh")
async def refresh_saved_setup(
    setup_id: str,
    user: dict | None = Depends(get_current_user_optional),
) -> dict:
    try:
        return {"setup": refresh_setup(setup_id, user_id=user.get("id") if user else None)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Saved setup not found") from None


@app.get("/config")
async def get_config() -> dict:
    return load_config()


@app.get("/auth/config")
async def get_auth_config() -> dict:
    return {
        "supabase_url": os.getenv("SUPABASE_URL", ""),
        "publishable_key": os.getenv("SUPABASE_PUBLISHABLE_KEY", ""),
        "enabled": bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_PUBLISHABLE_KEY")),
    }


@app.get("/watchlists")
async def get_watchlists() -> dict:
    return {"watchlists": list_watchlists()}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
