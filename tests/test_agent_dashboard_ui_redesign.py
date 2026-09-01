import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "agent.html").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "static" / "agent.css").read_text(encoding="utf-8")
JS = (ROOT / "app" / "static" / "agent.js").read_text(encoding="utf-8")


def test_dashboard_prioritizes_positions_before_risk_and_setups() -> None:
    assert HTML.index('id="positionsOverviewPanel"') < HTML.index('id="metricGrid"')
    assert HTML.index('id="positionsOverviewPanel"') < HTML.index('id="riskDashboardPanel"')
    assert HTML.index('id="setupsSection"') < HTML.index('class="panel diagnostics-panel"')


def test_dashboard_has_mobile_section_navigation() -> None:
    for target in ("positionsOverviewPanel", "setupsSection", "performanceSection"):
        assert f'href="#{target}"' in HTML
    assert "systemSection" not in HTML
    assert ".dashboard-nav" in CSS
    assert "position: fixed" in CSS
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in CSS


def test_dashboard_hides_sector_exposure_and_system_health() -> None:
    assert "System Health" not in HTML
    assert 'exposureListCard("Sector Exposure"' not in JS
    assert 'exposureListCard("Factor Exposure"' in JS


def test_high_density_sections_start_collapsed() -> None:
    for target in (
        "positionTimelineContent",
        "diagnosticsDetailPanel",
        "equityChartPanel",
        "positionChartsPanel",
        "calibrationPanel",
        "latestSummaryPanel",
    ):
        assert re.search(rf'id="{target}" class="[^"]*collapsible-content[^"]*" hidden', HTML)
    assert ".diagnostic-load-more-row[hidden]" in CSS
    assert ".load-more-row[hidden]" in CSS


def test_watch_ready_preview_is_compact_and_does_not_eager_load_charts() -> None:
    render_start = JS.index("function renderWatchReadyPanel")
    render_end = JS.index("function renderEquity", render_start)
    render_source = JS[render_start:render_end]
    assert "items.slice(0, 4)" in render_source
    assert "candidate-status" in render_source
    assert "<img" not in render_source


def test_portfolio_heat_has_legacy_snapshot_fallback() -> None:
    assert "summary.starting_capital_ils || 100000" in JS
    assert "risk.portfolio_heat_cap || startingCapital * 0.025" in JS
    assert "Math.max(0, heatCap - openRisk)" in JS
