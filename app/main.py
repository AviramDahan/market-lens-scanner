from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.charts import write_scan_chart
from app.config import load_config
from app.models import ScanRequest, ScanResponse
from app.scanner import scan_tickers

app = FastAPI(title="Market Lens", version="0.1.0", description="Swing trade scanner")

PROJECT_ROOT = Path(__file__).parent.parent
STATIC_DIR = Path(__file__).parent / "static"
CHART_DIR = PROJECT_ROOT / "charts"
CHART_DIR.mkdir(exist_ok=True)

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
async def scan_with_charts(request: ScanRequest) -> dict:
    tickers = [t.upper() for t in request.tickers]
    results, errors, details = scan_tickers(
        tickers,
        min_rr=request.min_rr,
        analysis_period=request.analysis_period,
    )
    charts = {}
    for detail in details:
        path = write_scan_chart(detail, CHART_DIR)
        charts[detail.result.ticker] = f"/charts/{path.name}"
    return {
        "results": [result.model_dump() for result in results],
        "errors": errors,
        "charts": charts,
        "analysis_period": request.analysis_period,
    }


@app.get("/config")
async def get_config() -> dict:
    return load_config()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
