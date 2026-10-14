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


def test_open_positions_explain_live_quote_provenance() -> None:
    assert "function positionQuoteMeta(position)" in JS
    assert 'position.live_price_session_label' in JS
    assert 'position.live_price_updated_at' in JS
    assert 'position.persisted_price_usd' in JS
    assert 'position.live_price_source ? "Latest quote" : "Saved mark"' in JS
    assert ".position-price-meta" in CSS


def test_open_positions_distinguish_price_trigger_and_persistence_health() -> None:
    assert 'id="positionExecutionHealth"' in HTML
    assert "function renderPositionExecutionHealth" in JS
    assert 'label: "Price feed"' in JS
    assert 'label: "TP/SL trigger"' in JS
    assert 'label: "Portfolio update"' in JS
    assert "execution_sensor_status" in JS
    assert "executor_persistence_status" in JS
    assert ".position-execution-health" in CSS


def test_run_strip_shows_canonical_status_and_scan_coverage() -> None:
    assert 'id="scanCoverageMeta"' in HTML
    assert 'data.latest_run.run_status || "FAILED"' in JS
    assert '`${received}/${requested} results' in JS
    assert "data.latest_run.ticker_recovery || {}" in JS
    assert "recoveredTickers.length" in JS
    assert "Focused recovery completed; no missing cards recovered" in JS
    assert 'data-status="PARTIAL_OK"' in CSS


def test_trade_log_separates_identity_status_and_financial_details() -> None:
    trade_css = CSS[CSS.index(".trade-row {"):CSS.index(".action-chart {")]
    assert "grid-template-columns: minmax(0, 1fr) minmax(0, 170px)" in trade_css
    assert ".trade-row .ticker-cell" in trade_css
    assert "min-width: 0" in trade_css
    assert ".trade-status" in trade_css
    assert "white-space: normal" in trade_css
    assert ".trade-details" in trade_css
    assert "grid-column: 1 / -1" in trade_css
    trade_js = JS[JS.index("function renderTrades()"):JS.index("function renderSummary")]
    assert 'class="trade-details"' in trade_js
    assert 'data-action="${escapeHtml(trade.action)}"' in trade_js
    assert '.replaceAll("_", " ")' in trade_js
    assert "TRADES_PAGE_SIZE" in trade_js
    assert "trade.execution_data_quality" in trade_js


def test_actions_keep_chart_identity_and_details_in_shrinkable_tracks() -> None:
    action_css = CSS[CSS.index(".action-row {"):CSS.index(".trade-row {")]
    assert "grid-template-columns: 92px minmax(0, 1fr)" in action_css
    assert ".action-identity .ticker-cell" in action_css
    assert "min-width: 0" in action_css
    assert "flex-wrap: wrap" in action_css
    assert ".action-details" in action_css
    assert "grid-column: 1 / -1" in action_css
    action_js = JS[JS.index("function renderActions()"):JS.index("function setupMediaModal")]
    assert 'class="action-identity"' in action_js
    assert 'class="action-details"' in action_js
    assert "ACTIONS_PAGE_SIZE" in action_js


def test_chart_modal_fits_its_padded_track_and_covers_mobile_navigation() -> None:
    media_css = CSS[CSS.index(".media-modal {"):CSS.index(".media-close {")]
    diagnostic_css = CSS[CSS.index(".diagnostic-modal {"):CSS.index(".diagnostic-modal-panel {")]
    assert "grid-template-columns: minmax(0, 1fr)" in media_css
    assert "grid-template-rows: minmax(0, 1fr)" in media_css
    assert "max-width: min(1200px, 100%)" in media_css
    assert "max-height: min(90vh, 100%)" in media_css
    assert "z-index: 80" in media_css
    assert "z-index: 65" in diagnostic_css
    assert "z-index: 60" in CSS[CSS.index("  .dashboard-nav {"):]


def test_log_layout_release_refreshes_both_static_assets() -> None:
    assert '/static/agent.css?v=20261014-log-modal-qa' in HTML
    assert '/static/agent.js?v=20261014-log-modal-qa' in HTML
    assert 'Target 1 must pass the active minimum net R/R and ATR-distance gates.' in HTML
    assert 'A close Target 1 is a warning, not an automatic block' not in HTML
