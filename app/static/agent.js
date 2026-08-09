const state = {
  data: null,
  selectedDate: "",
  actions: [],
  actionTotal: 0,
  actionHasMore: false,
  actionsExpanded: false,
  actionsLoading: false,
  trades: [],
  tradeTotal: 0,
  closedTradeTotal: 0,
  tradeHasMore: false,
  tradesExpanded: false,
  tradesLoading: false,
  chartSections: {
    equity: false,
    nearMisses: false,
    positionCharts: false,
  },
  liveTimer: null,
  scheduleTimer: null,
  nextLiveSyncAt: null,
  monitorTriggerCooldowns: {},
  lastMonitorTrigger: null,
  diagnostic: {
    key: "",
    label: "",
    items: [],
    total: 0,
    hasMore: false,
    loading: false,
    facets: {},
    filters: {
      sector: "",
      setupType: "",
      chartFilter: "all",
      confirmation: "all",
      sort: "closest",
    },
  },
};

const ACTIONS_PAGE_SIZE = 10;
const TRADES_PAGE_SIZE = 10;
const DIAGNOSTICS_PAGE_SIZE = 20;
const MONITOR_TRIGGER_COOLDOWN_MS = 5 * 60 * 1000;
const NEW_YORK_TZ = "America/New_York";
const ISRAEL_TZ = "Asia/Jerusalem";
const WEEKDAY_SCAN_TIMES = [
  "06:30",
  "08:30",
  "09:10",
  "09:45",
  "10:30",
  "11:30",
  "13:30",
  "14:30",
  "15:30",
  "16:15",
  "16:20",
  "18:30",
  "20:15",
  "22:30",
];
const MARKET_CONFIRMATION_TIMES = new Set(["09:45", "10:30", "11:30", "13:30", "14:30", "15:30"]);
const CLOSE_REVIEW_TIMES = new Set(["16:15"]);
const SATURDAY_SCAN_TIMES = ["11:00"];
const SUNDAY_SCAN_TIMES = ["18:30", "22:00"];

let money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

function percentFromEntry(level, entry) {
  const numericLevel = Number(level || 0);
  const numericEntry = Number(entry || 0);
  if (!numericLevel || !numericEntry) return "";
  const change = ((numericLevel - numericEntry) / numericEntry) * 100;
  const sign = change > 0 ? "+" : "";
  return `${sign}${change.toFixed(2)}%`;
}

function formatLevelWithPercent(level, entry) {
  const price = usd.format(Number(level || 0));
  const percent = percentFromEntry(level, entry);
  return percent ? `${price} (${percent})` : price;
}

document.addEventListener("DOMContentLoaded", () => {
  const params = new URLSearchParams(window.location.search);
  const selectedDate = params.get("date") || "";
  const historyDate = document.getElementById("historyDate");
  historyDate.value = selectedDate;
  historyDate.addEventListener("change", () => loadDashboard(historyDate.value));
  document.getElementById("latestSnapshot").addEventListener("click", () => {
    historyDate.value = "";
    loadDashboard("");
  });
  document.getElementById("toggleActions").addEventListener("click", () => {
    state.actionsExpanded = !state.actionsExpanded;
    renderActions();
    if (state.actionsExpanded && !state.actions.length) loadActionsPage(true);
  });
  document.getElementById("loadMoreActions").addEventListener("click", () => {
    loadActionsPage(false);
  });
  document.getElementById("toggleTrades").addEventListener("click", () => {
    state.tradesExpanded = !state.tradesExpanded;
    renderTrades();
    if (state.tradesExpanded && !state.trades.length) loadTradesPage(true);
  });
  document.getElementById("loadMoreTrades").addEventListener("click", () => {
    loadTradesPage(false);
  });
  document.getElementById("openWatchReadyDiagnostics").addEventListener("click", () => {
    openDiagnosticModal("WATCH_READY", "WATCH_READY");
  });
  setupCollapsibleSections();
  setupMediaModal();
  setupDiagnosticModal();
  loadDashboard(selectedDate);
  updateScheduleIndicators();
  state.scheduleTimer = window.setInterval(updateScheduleIndicators, 1000);
});

async function loadDashboard(selectedDate = "") {
  try {
    const params = new URLSearchParams();
    if (selectedDate) params.set("date", selectedDate);
    params.set("limit", String(ACTIONS_PAGE_SIZE));
    const response = await fetch(`/agent/data${params.toString() ? `?${params}` : ""}`);
    if (!response.ok) {
      throw new Error(`Agent data failed: ${response.status}`);
    }
    state.data = await response.json();
    state.selectedDate = selectedDate;
    resetPagedCollections(state.data);
    state.actionsExpanded = false;
    state.tradesExpanded = false;
    syncDateUrl(selectedDate);
    renderDashboard(state.data);
  } catch (error) {
    document.getElementById("runStatus").textContent = "Error";
    document.getElementById("summaryText").textContent = error.message;
  }
}

function resetPagedCollections(data) {
  const pagination = data.pagination || {};
  const actionPage = pagination.actions || {};
  const tradePage = pagination.trades || {};
  state.actions = data.latest_setups || [];
  state.actionTotal = Number(actionPage.total ?? state.actions.length);
  state.actionHasMore = Boolean(actionPage.has_more ?? state.actions.length < state.actionTotal);
  state.trades = data.recent_trades || [];
  state.tradeTotal = Number(tradePage.total ?? state.trades.length);
  state.closedTradeTotal = Number(tradePage.closed_total ?? (data.closed_trades || []).length);
  state.tradeHasMore = Boolean(tradePage.has_more ?? state.trades.length < state.tradeTotal);
}

async function loadActionsPage(reset = false) {
  if (state.actionsLoading) return;
  state.actionsLoading = true;
  renderActions();
  try {
    const params = new URLSearchParams({
      section: "actions",
      offset: String(reset ? 0 : state.actions.length),
      limit: String(ACTIONS_PAGE_SIZE),
    });
    if (state.selectedDate) params.set("date", state.selectedDate);
    const response = await fetch(`/agent/data?${params}`);
    if (!response.ok) throw new Error(`Actions failed: ${response.status}`);
    const page = await response.json();
    if (page.status !== "ok") throw new Error(page.error || "Actions unavailable");
    state.actions = reset ? page.items || [] : state.actions.concat(page.items || []);
    state.actionTotal = Number(page.total || state.actions.length);
    state.actionHasMore = Boolean(page.has_more);
  } catch (error) {
    setInlineLoadError("actionsList", error.message || "Actions unavailable");
  } finally {
    state.actionsLoading = false;
    renderActions();
  }
}

async function loadTradesPage(reset = false) {
  if (state.tradesLoading) return;
  state.tradesLoading = true;
  renderTrades();
  try {
    const params = new URLSearchParams({
      section: "trades",
      offset: String(reset ? 0 : state.trades.length),
      limit: String(TRADES_PAGE_SIZE),
    });
    if (state.selectedDate) params.set("date", state.selectedDate);
    const response = await fetch(`/agent/data?${params}`);
    if (!response.ok) throw new Error(`Trades failed: ${response.status}`);
    const page = await response.json();
    if (page.status !== "ok") throw new Error(page.error || "Trades unavailable");
    state.trades = reset ? page.items || [] : state.trades.concat(page.items || []);
    state.tradeTotal = Number(page.total || state.trades.length);
    state.tradeHasMore = Boolean(page.has_more);
  } catch (error) {
    setInlineLoadError("tradeList", error.message || "Trades unavailable");
  } finally {
    state.tradesLoading = false;
    renderTrades();
  }
}

function setInlineLoadError(elementId, message) {
  const element = document.getElementById(elementId);
  if (element) element.innerHTML = `<div class="empty-state compact">${escapeHtml(message)}</div>`;
}

function renderDashboard(data) {
  if (data.status !== "ok") {
    document.getElementById("runStatus").textContent = "Missing data";
    document.getElementById("summaryText").textContent = data.error || "Agent tracker is not available.";
    return;
  }

  money = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: data.summary.currency || "USD",
    maximumFractionDigits: 0,
  });
  const historyDate = document.getElementById("historyDate");
  const snapshot = data.snapshot || { selected_date: "", available_dates: [] };
  historyDate.value = snapshot.selected_date || "";
  if (snapshot.available_dates.length) {
    historyDate.max = snapshot.available_dates[snapshot.available_dates.length - 1];
  }
  document.getElementById("latestSnapshot").disabled = !snapshot.selected_date;
  document.getElementById("trackerLink").href = data.tracker_url;
  document.getElementById("lastRun").textContent = formatDate(data.latest_run.timestamp);
  document.getElementById("runStatus").textContent = "OK";
  document.getElementById("tickerCount").textContent = data.latest_run.tickers.length;
  document.getElementById("validSetups").textContent = data.latest_run.valid_setups;
  document.getElementById("tradeReadySetups").textContent = data.latest_run.trade_ready_setups ?? countTradeReady(data.latest_setups);

  renderMetrics(data.summary);
  renderSystemHealth(data.system_health || {}, data.results_sync || {}, data.payload || {});
  renderDiagnostics(data.decision_diagnostics || {}, data.daily_summary || {}, data.weekly_summary || {});
  renderWatchReadyPanel(data.decision_diagnostics || {});
  renderEquity(data.equity_curve, data.summary);
  renderPositionsOverview(data.open_positions);
  renderPositions(data.open_positions);
  renderPositionCharts(data.open_positions);
  renderActions();
  renderTrades();
  renderCalibration(data.score_calibration || []);
  renderSummary(data.latest_run);
  syncCollapsibleSections();
  startLivePrices(snapshot.selected_date || "");

  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function setupCollapsibleSections() {
  document.querySelectorAll("[data-collapse-target]").forEach((button) => {
    const key = button.dataset.collapseKey || button.dataset.collapseTarget;
    button.addEventListener("click", () => {
      setCollapsibleSection(key, !state.chartSections[key]);
    });
  });
  syncCollapsibleSections();
}

function syncCollapsibleSections() {
  Object.entries(state.chartSections).forEach(([key, expanded]) => {
    setCollapsibleSection(key, expanded, { skipChartRender: true });
  });
}

function setCollapsibleSection(key, expanded, options = {}) {
  state.chartSections[key] = Boolean(expanded);
  document.querySelectorAll(`[data-collapse-key="${key}"]`).forEach((button) => {
    const target = document.getElementById(button.dataset.collapseTarget);
    if (target) target.hidden = !state.chartSections[key];
    button.setAttribute("aria-expanded", String(state.chartSections[key]));
    button.innerHTML = `
      <i data-lucide="${state.chartSections[key] ? "chevron-up" : "chevron-down"}"></i>
      <span>${state.chartSections[key] ? "Hide" : "Show"}</span>
    `;
  });
  if (key === "equity" && state.chartSections[key] && state.data && !options.skipChartRender) {
    window.requestAnimationFrame(() => renderEquity(state.data.equity_curve, state.data.summary));
  }
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function startLivePrices(selectedDate) {
  stopLivePrices();
  if (selectedDate || !state.data?.open_positions?.length) {
    state.nextLiveSyncAt = null;
    updateScheduleIndicators();
    return;
  }
  refreshLivePrices();
  state.nextLiveSyncAt = Date.now() + 60_000;
  updateScheduleIndicators();
  state.liveTimer = window.setInterval(async () => {
    await refreshLivePrices();
    state.nextLiveSyncAt = Date.now() + 60_000;
    updateScheduleIndicators();
  }, 60_000);
}

function stopLivePrices() {
  if (state.liveTimer) {
    window.clearInterval(state.liveTimer);
    state.liveTimer = null;
  }
  state.nextLiveSyncAt = null;
}

async function refreshLivePrices() {
  try {
    const response = await fetch(`/agent/live-prices?v=${Date.now()}`);
    if (!response.ok) return;
    const live = await response.json();
    if (live.status !== "ok" || !state.data) return;
    state.data.summary = live.summary || state.data.summary;
    state.data.open_positions = live.open_positions || state.data.open_positions;
    renderMetrics(state.data.summary);
    renderPositionsOverview(state.data.open_positions, live.updated_at);
    renderPositions(state.data.open_positions, live.updated_at);
    renderPositionCharts(state.data.open_positions);
    detectAndTriggerMonitorEvents(state.data.open_positions);
  } catch (_error) {
    // Live refresh is best-effort; the committed tracker remains the fallback.
  }
}

function updateScheduleIndicators() {
  const nextScanEl = document.getElementById("nextScan");
  const nextScanMetaEl = document.getElementById("nextScanMeta");
  const nextSyncEl = document.getElementById("nextPriceSync");
  const nextSyncMetaEl = document.getElementById("nextPriceSyncMeta");

  if (nextScanEl && nextScanMetaEl) {
    const nextScan = findNextAgentScan(new Date());
    nextScanEl.textContent = formatIsraelDateTime(nextScan.date);
    nextScanMetaEl.textContent = `${nextScan.label} - ${formatCountdown(nextScan.date.getTime() - Date.now())}`;
  }

  if (!nextSyncEl || !nextSyncMetaEl) return;
  if (state.nextLiveSyncAt) {
    nextSyncEl.textContent = formatCountdown(state.nextLiveSyncAt - Date.now());
    nextSyncMetaEl.textContent = "Open-position prices";
  } else if (document.getElementById("historyDate")?.value) {
    nextSyncEl.textContent = "Paused";
    nextSyncMetaEl.textContent = "Historical snapshot";
  } else if (!state.data?.open_positions?.length) {
    nextSyncEl.textContent = "Paused";
    nextSyncMetaEl.textContent = "No open positions";
  } else {
    nextSyncEl.textContent = "Checking";
    nextSyncMetaEl.textContent = "Live positions";
  }
  renderMonitorTriggerStatus();
}

function detectAndTriggerMonitorEvents(positions) {
  (positions || []).forEach((position) => {
    const event = touchedPositionEvent(position);
    if (!event) return;
    const key = `${position.ticker}:${event.eventType}`;
    if ((state.monitorTriggerCooldowns[key] || 0) > Date.now()) return;
    state.monitorTriggerCooldowns[key] = Date.now() + MONITOR_TRIGGER_COOLDOWN_MS;
    triggerPositionMonitor(position, event);
  });
}

function touchedPositionEvent(position) {
  const price = Number(position.current_price_usd || 0);
  const stop = Number(position.stop_loss || 0);
  const target1 = Number(position.target_1 || 0);
  const target2 = Number(position.target_2 || 0);
  const partialTaken = Boolean(position.partial_taken) || String(position.notes || "").toLowerCase().includes("partial");
  if (!price || !position.ticker) return null;
  if (stop > 0 && price <= stop) {
    return { eventType: "EXIT_STOP", label: "Stop touched", threshold: stop, price };
  }
  if (target2 > 0 && price >= target2) {
    return { eventType: "TAKE_PROFIT", label: "Target 2 touched", threshold: target2, price };
  }
  if (target1 > 0 && price >= target1 && !partialTaken) {
    return { eventType: "TAKE_PARTIAL_PROFIT", label: "Target 1 touched", threshold: target1, price };
  }
  return null;
}

async function triggerPositionMonitor(position, event) {
  setMonitorTriggerStatus("Triggering", `${position.ticker} ${event.label}`);
  try {
    const response = await fetch("/agent/trigger-monitor", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ticker: position.ticker,
        event_type: event.eventType,
        live_price: event.price,
        source: "agent-ui-live-price",
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      setMonitorTriggerStatus("Error", payload.detail || `Trigger failed (${response.status})`);
      return;
    }
    if (payload.triggered) {
      setMonitorTriggerStatus("Triggered", `${payload.ticker} ${payload.event_type}`);
      return;
    }
    setMonitorTriggerStatus(payload.status || "Skipped", payload.reason || "Monitor was not triggered");
  } catch (error) {
    setMonitorTriggerStatus("Error", error.message || "Trigger request failed");
  }
}

function setMonitorTriggerStatus(status, detail) {
  state.lastMonitorTrigger = {
    status,
    detail,
    timestamp: Date.now(),
  };
  renderMonitorTriggerStatus();
}

function renderMonitorTriggerStatus() {
  const statusEl = document.getElementById("monitorTriggerStatus");
  const metaEl = document.getElementById("monitorTriggerMeta");
  if (!statusEl || !metaEl) return;
  if (!state.lastMonitorTrigger) {
    statusEl.textContent = "Idle";
    metaEl.textContent = "Live TP/SL sensor";
    return;
  }
  statusEl.textContent = state.lastMonitorTrigger.status;
  metaEl.textContent = `${state.lastMonitorTrigger.detail} - ${formatElapsed(Date.now() - state.lastMonitorTrigger.timestamp)} ago`;
}

function findNextAgentScan(now) {
  const nyNow = getZonedParts(now, NEW_YORK_TZ);
  const baseDate = Date.UTC(nyNow.year, nyNow.month - 1, nyNow.day);
  for (let dayOffset = 0; dayOffset < 14; dayOffset += 1) {
    const date = new Date(baseDate + dayOffset * 24 * 60 * 60 * 1000);
    const day = date.getUTCDay();
    const times = scanTimesForNyDay(day);
    for (const time of times) {
      const [hour, minute] = time.split(":").map(Number);
      const candidate = zonedTimeToDate(NEW_YORK_TZ, {
        year: date.getUTCFullYear(),
        month: date.getUTCMonth() + 1,
        day: date.getUTCDate(),
        hour,
        minute,
      });
      if (candidate.getTime() > now.getTime() + 15_000) {
        return { date: candidate, label: scanLabelFor(time, day) };
      }
    }
  }
  return { date: now, label: "Schedule unavailable" };
}

function scanTimesForNyDay(day) {
  if (day >= 1 && day <= 5) return WEEKDAY_SCAN_TIMES;
  if (day === 6) return SATURDAY_SCAN_TIMES;
  return SUNDAY_SCAN_TIMES;
}

function scanLabelFor(time, day) {
  if (MARKET_CONFIRMATION_TIMES.has(time)) return "Market confirmation";
  if (CLOSE_REVIEW_TIMES.has(time)) return "Close review";
  if (day === 0 || day === 6) return "Weekend staging";
  return "Off-hours staging";
}

function zonedTimeToDate(timeZone, parts) {
  const utcGuess = new Date(Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, 0));
  const zoneParts = getZonedParts(utcGuess, timeZone);
  const zoneAsUtc = Date.UTC(
    zoneParts.year,
    zoneParts.month - 1,
    zoneParts.day,
    zoneParts.hour,
    zoneParts.minute,
    zoneParts.second || 0,
  );
  return new Date(utcGuess.getTime() - (zoneAsUtc - utcGuess.getTime()));
}

function getZonedParts(date, timeZone) {
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  const raw = Object.fromEntries(formatter.formatToParts(date).map((part) => [part.type, part.value]));
  return {
    year: Number(raw.year),
    month: Number(raw.month),
    day: Number(raw.day),
    hour: Number(raw.hour),
    minute: Number(raw.minute),
    second: Number(raw.second || 0),
  };
}

function formatIsraelDateTime(date) {
  return new Intl.DateTimeFormat("en-IL", {
    timeZone: ISRAEL_TZ,
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatCountdown(ms) {
  const totalSeconds = Math.max(0, Math.round(ms / 1000));
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (days > 0) return `in ${days}d ${hours}h`;
  if (hours > 0) return `in ${hours}h ${minutes}m`;
  if (minutes > 0) return `in ${minutes}m ${seconds}s`;
  return `in ${seconds}s`;
}

function formatElapsed(ms) {
  const totalSeconds = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60);
    return `${hours}h ${minutes % 60}m`;
  }
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function formatAgeMinutes(minutes) {
  const value = Math.max(0, Number(minutes || 0));
  if (value >= 1440) return `${Math.floor(value / 1440)}d ${Math.floor((value % 1440) / 60)}h`;
  if (value >= 60) return `${Math.floor(value / 60)}h ${value % 60}m`;
  return `${value}m`;
}

function renderMetrics(summary) {
  const metrics = [
    {
      label: "Equity",
      value: money.format(summary.equity_ils),
      detail: `${formatPct(summary.total_pnl_pct)} total P/L`,
      tone: summary.total_pnl_ils >= 0 ? "good" : "bad",
    },
    {
      label: "Total P/L",
      value: formatSignedMoney(summary.total_pnl_ils),
      detail: `${formatSignedMoney(summary.realized_pnl_ils)} realized`,
      tone: summary.total_pnl_ils >= 0 ? "good" : "bad",
    },
    {
      label: "Cash",
      value: money.format(summary.cash_ils),
      detail: "Available paper budget",
      tone: "",
    },
    {
      label: "Exposure",
      value: money.format(summary.exposure_ils),
      detail: `${summary.open_positions} open positions`,
      tone: "",
    },
    {
      label: "Open Risk",
      value: money.format(summary.open_risk_ils),
      detail: "Risk to active stops",
      tone: summary.open_risk_ils > 0 ? "warn" : "",
    },
    {
      label: "Win Rate",
      value: `${summary.win_rate.toFixed(1)}%`,
      detail: `${summary.wins} wins / ${summary.losses} losses`,
      tone: "",
    },
  ];

  document.getElementById("metricGrid").innerHTML = metrics
    .map(
      (metric) => `
        <div class="metric-card ${metric.tone}">
          <span>${escapeHtml(metric.label)}</span>
          <strong>${escapeHtml(metric.value)}</strong>
          <small>${escapeHtml(metric.detail)}</small>
        </div>
      `,
    )
    .join("");
}

function renderSystemHealth(health, resultsSync, payload) {
  const grid = document.getElementById("systemHealthGrid");
  const meta = document.getElementById("systemHealthMeta");
  if (!grid || !meta) return;

  const notes = health.notes || [];
  meta.textContent = notes.length ? notes[0] : "Scanner, monitor and dashboard sync status";
  const syncStatus = resultsSync?.enabled === false
    ? "Local"
    : resultsSync?.downloaded || resultsSync?.asset_sync?.downloaded
      ? "Synced"
      : "Ready";
  const cards = [
    {
      label: "Payload",
      value: payload.mode === "compact" ? "Compact" : "Full",
      detail: payload.mode === "compact" ? "Actions/trades load on demand" : "Full dashboard payload",
      tone: payload.mode === "compact" ? "good" : "warn",
    },
    {
      label: "Last Scan",
      value: health.latest_scan_at ? formatDate(health.latest_scan_at) : "Unknown",
      detail: health.latest_scan_age_minutes != null ? `${formatAgeMinutes(health.latest_scan_age_minutes)} ago` : "No scan timestamp",
      tone: health.latest_scan_at ? "good" : "warn",
    },
    {
      label: "Last Monitor",
      value: health.latest_monitor_at ? formatDate(health.latest_monitor_at) : "No events",
      detail: health.latest_monitor_age_minutes != null ? `${formatAgeMinutes(health.latest_monitor_age_minutes)} ago` : "Waiting for monitor update",
      tone: health.latest_monitor_at ? "good" : "",
    },
    {
      label: "Data Sync",
      value: syncStatus,
      detail: resultsSync?.reason || resultsSync?.asset_sync?.reason || "Dashboard assets checked",
      tone: resultsSync?.error ? "bad" : "",
    },
  ];

  grid.innerHTML = cards
    .map(
      (card) => `
        <div class="health-card ${card.tone || ""}">
          <span>${escapeHtml(card.label)}</span>
          <strong>${escapeHtml(card.value)}</strong>
          <small>${escapeHtml(card.detail || "")}</small>
        </div>
      `,
    )
    .join("");
}

function renderDiagnostics(diagnostics, dailySummary, weeklySummary) {
  const grid = document.getElementById("diagnosticsGrid");
  const list = document.getElementById("nearMissList");
  const meta = document.getElementById("nearMissMeta");
  if (!grid || !list || !meta) return;

  const blockers = diagnostics.blockers || {};
  const actionCounts = diagnostics.action_counts || {};
  const cards = [
    { key: "BUY", label: "BUY", value: actionCounts.BUY_SIMULATED || dailySummary.BUY_SIMULATED_count || 0, detail: "Actual simulated entries" },
    { key: "WATCH_READY", label: "WATCH_READY", value: diagnostics.watch_ready_count || dailySummary.WATCH_READY_count || 0, detail: "Closest staged candidates" },
    { key: "RR_BLOCKED", label: "R/R Blocked", value: blockers["R/R below gate"] || 0, detail: "Failed weighted/net reward" },
    { key: "SCORE_BLOCKED", label: "Score Blocked", value: blockers["Setup score below gate"] || 0, detail: "Below regime threshold" },
    { key: "CONFIRM_BLOCKED", label: "Confirm/Session", value: blockers["Entry confirmation missing"] || 0, detail: "Needs entry or regular-session confirmation" },
    { key: "WEAK_EARNINGS", label: "Weak/Earnings", value: (blockers["Weak sector"] || 0) + (blockers["Earnings blackout"] || 0), detail: "Sector or earnings risk" },
  ];

  grid.innerHTML = cards
    .map(
      (card) => `
        <button class="diagnostic-card" type="button" data-diagnostic-key="${escapeHtml(card.key)}" data-diagnostic-label="${escapeHtml(card.label)}">
          <span>${escapeHtml(card.label)}</span>
          <strong>${escapeHtml(card.value)}</strong>
          <small>${escapeHtml(card.detail)}</small>
          <em>View setups</em>
        </button>
      `,
    )
    .join("");
  grid.querySelectorAll("[data-diagnostic-key]").forEach((button) => {
    button.addEventListener("click", () => {
      openDiagnosticModal(button.dataset.diagnosticKey || "", button.dataset.diagnosticLabel || "Setups");
    });
  });

  const nearMisses = diagnostics.near_misses || [];
  meta.textContent = `${nearMisses.length} shown / ${diagnostics.total_results || 0} scan results`;
  if (!nearMisses.length) {
    list.innerHTML = '<div class="empty-state">No near-miss setups in the latest scan.</div>';
    return;
  }
  list.innerHTML = nearMisses
    .map((item) => {
      const rr = Number(item.weighted_net_rr || item.net_rr || 0);
      const score = Number(item.setup_score || 0);
      const confirmed = item.entry_confirmation_passed ? "Confirmed" : "No confirmation";
      return `
        <article class="near-miss-card">
          <div>
            <strong>${escapeHtml(tickerLabel(item))}</strong>
            <span class="${actionBadgeClass(item.action)}">${escapeHtml(item.action)}</span>
          </div>
          <p>${escapeHtml(item.setup_type || "Setup")}</p>
          <small>Score ${score.toFixed(2)} / weighted R/R ${rr.toFixed(2)}x / ${escapeHtml(confirmed)}</small>
          <em>${escapeHtml(item.reason || "No reason provided")}</em>
        </article>
      `;
    })
    .join("");

  const weeklyRecommendation = (weeklySummary.recommendations_for_next_week || [])[0];
  if (weeklyRecommendation) {
    meta.textContent += ` - Weekly: ${weeklyRecommendation}`;
  }
}

function setupDiagnosticModal() {
  const modal = document.getElementById("diagnosticModal");
  const close = document.getElementById("diagnosticModalClose");
  if (!modal || !close) return;
  close.addEventListener("click", closeDiagnosticModal);
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeDiagnosticModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !event.marketLensMediaHandled) closeDiagnosticModal();
  });
  document.getElementById("loadMoreDiagnostics").addEventListener("click", () => loadDiagnosticPage(false));
  document.getElementById("diagnosticResetFilters").addEventListener("click", () => {
    state.diagnostic.filters = defaultDiagnosticFilters();
    syncDiagnosticFilterControls();
    loadDiagnosticPage(true);
  });
  [
    "diagnosticSectorFilter",
    "diagnosticSetupFilter",
    "diagnosticChartFilter",
    "diagnosticConfirmationFilter",
    "diagnosticSort",
  ].forEach((id) => {
    document.getElementById(id).addEventListener("change", () => {
      state.diagnostic.filters = {
        sector: document.getElementById("diagnosticSectorFilter").value,
        setupType: document.getElementById("diagnosticSetupFilter").value,
        chartFilter: document.getElementById("diagnosticChartFilter").value,
        confirmation: document.getElementById("diagnosticConfirmationFilter").value,
        sort: document.getElementById("diagnosticSort").value,
      };
      loadDiagnosticPage(true);
    });
  });
}

function openDiagnosticModal(key, label) {
  const modal = document.getElementById("diagnosticModal");
  const title = document.getElementById("diagnosticModalTitle");
  const meta = document.getElementById("diagnosticModalMeta");
  const body = document.getElementById("diagnosticModalBody");
  if (!modal || !title || !meta || !body) return;

  state.diagnostic = {
    key,
    label,
    items: [],
    total: 0,
    hasMore: false,
    loading: false,
    facets: {},
    filters: defaultDiagnosticFilters(),
  };
  title.textContent = label;
  meta.textContent = "Loading setups";
  body.innerHTML = '<div class="empty-state compact">Loading diagnostic setups...</div>';
  document.getElementById("diagnosticLoadMoreRow").hidden = true;
  syncDiagnosticFilterControls();
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
  loadDiagnosticPage(true);
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function closeDiagnosticModal() {
  const modal = document.getElementById("diagnosticModal");
  if (!modal) return;
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
}

function defaultDiagnosticFilters() {
  return {
    sector: "",
    setupType: "",
    chartFilter: "all",
    confirmation: "all",
    sort: "closest",
  };
}

async function loadDiagnosticPage(reset = false) {
  if (!state.diagnostic.key || state.diagnostic.loading) return;
  state.diagnostic.loading = true;
  renderDiagnosticModalContent();
  try {
    const filters = state.diagnostic.filters;
    const params = new URLSearchParams({
      section: "diagnostics",
      diagnostic_key: state.diagnostic.key,
      offset: String(reset ? 0 : state.diagnostic.items.length),
      limit: String(DIAGNOSTICS_PAGE_SIZE),
      chart_filter: filters.chartFilter,
      confirmation: filters.confirmation,
      sort: filters.sort,
    });
    if (state.selectedDate) params.set("date", state.selectedDate);
    if (filters.sector) params.set("sector", filters.sector);
    if (filters.setupType) params.set("setup_type", filters.setupType);
    const response = await fetch(`/agent/data?${params}`);
    if (!response.ok) throw new Error(`Diagnostics failed: ${response.status}`);
    const page = await response.json();
    if (page.status !== "ok") throw new Error(page.error || "Diagnostics unavailable");
    state.diagnostic.items = reset ? page.items || [] : state.diagnostic.items.concat(page.items || []);
    state.diagnostic.total = Number(page.total || state.diagnostic.items.length);
    state.diagnostic.hasMore = Boolean(page.has_more);
    state.diagnostic.facets = page.facets || {};
    syncDiagnosticFilterControls();
  } catch (error) {
    const fallback = localDiagnosticItems(state.diagnostic.key);
    if (reset && fallback.length) {
      state.diagnostic.items = fallback;
      state.diagnostic.total = fallback.length;
      state.diagnostic.hasMore = false;
      state.diagnostic.facets = {};
    } else {
      document.getElementById("diagnosticModalBody").innerHTML =
        `<div class="empty-state compact">${escapeHtml(error.message || "Diagnostics unavailable")}</div>`;
    }
  } finally {
    state.diagnostic.loading = false;
    renderDiagnosticModalContent();
  }
}

function localDiagnosticItems(key) {
  const groups = state.data?.decision_diagnostics?.drilldowns || {};
  return groups[key] || [];
}

function syncDiagnosticFilterControls() {
  const filters = state.diagnostic.filters;
  fillDiagnosticSelect("diagnosticSectorFilter", state.diagnostic.facets.sectors || [], "All sectors", filters.sector);
  fillDiagnosticSelect("diagnosticSetupFilter", state.diagnostic.facets.setup_types || [], "All setups", filters.setupType);
  document.getElementById("diagnosticChartFilter").value = filters.chartFilter;
  document.getElementById("diagnosticConfirmationFilter").value = filters.confirmation;
  document.getElementById("diagnosticSort").value = filters.sort;
}

function fillDiagnosticSelect(id, options, allLabel, selectedValue) {
  const select = document.getElementById(id);
  const current = selectedValue || "";
  const choices = [`<option value="">${escapeHtml(allLabel)}</option>`].concat(
    (options || []).map((option) => {
      const value = option.value || "";
      const count = Number(option.count || 0);
      const selected = value === current ? " selected" : "";
      return `<option value="${escapeHtml(value)}"${selected}>${escapeHtml(value)} (${count})</option>`;
    }),
  );
  select.innerHTML = choices.join("");
  select.value = current;
}

function renderDiagnosticModalContent() {
  const title = document.getElementById("diagnosticModalTitle");
  const meta = document.getElementById("diagnosticModalMeta");
  const body = document.getElementById("diagnosticModalBody");
  const loadMoreRow = document.getElementById("diagnosticLoadMoreRow");
  const loadMore = document.getElementById("loadMoreDiagnostics");
  if (!title || !meta || !body || !loadMoreRow || !loadMore) return;
  title.textContent = state.diagnostic.label || "Setup details";
  const shown = Math.min(state.diagnostic.items.length, state.diagnostic.total);
  meta.textContent = state.diagnostic.loading && !shown
    ? "Loading setups"
    : `${shown} loaded / ${state.diagnostic.total} matching setup${state.diagnostic.total === 1 ? "" : "s"}`;
  body.innerHTML = state.diagnostic.loading && !state.diagnostic.items.length
    ? '<div class="empty-state compact">Loading diagnostic setups...</div>'
    : renderDiagnosticItems(state.diagnostic.items);
  attachDiagnosticItemListeners();
  loadMoreRow.hidden = !state.diagnostic.hasMore;
  if (!loadMoreRow.hidden) {
    const nextCount = Math.min(DIAGNOSTICS_PAGE_SIZE, Math.max(0, state.diagnostic.total - state.diagnostic.items.length));
    loadMore.disabled = state.diagnostic.loading;
    loadMore.querySelector("span").textContent = state.diagnostic.loading ? "Loading" : `Load ${nextCount} more`;
  }
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function renderDiagnosticItems(items) {
  if (!items.length) {
    return '<div class="empty-state">No setups match this diagnostic bucket.</div>';
  }
  return items
    .map((item) => {
      const score = Number(item.setup_score || 0);
      const net = Number(item.weighted_net_rr || item.net_rr || 0);
      const rr1 = Number(item.net_rr_1 || 0);
      const rr2 = Number(item.net_rr_2 || 0);
      const chart = item.chart_url
        ? `<button class="diagnostic-chart-button" type="button" data-full-src="${escapeHtml(item.chart_url)}?v=${Date.now()}">
            <img src="${escapeHtml(item.chart_url)}?v=${Date.now()}" alt="${escapeHtml(item.ticker)} chart" />
          </button>`
        : `<div class="diagnostic-chart-missing">
            <span>No chart saved</span>
            <button class="icon-button secondary generate-diagnostic-chart" type="button" data-ticker="${escapeHtml(item.ticker || "")}">
              <i data-lucide="image-plus"></i>
              <span>Generate chart</span>
            </button>
          </div>`;
      return `
        <article class="diagnostic-detail-card">
          ${chart}
          <div class="diagnostic-detail-copy">
            <div class="diagnostic-detail-head">
              <div class="ticker-cell">
                <strong>${escapeHtml(tickerLabel(item))}</strong>
                <span class="meta">${escapeHtml(tickerMeta(item, item.setup_type || "Setup"))}</span>
              </div>
              <span class="${actionBadgeClass(item.action)}">${escapeHtml(item.action || "UNKNOWN")}</span>
            </div>
            <div class="diagnostic-metrics">
              <span><b>Score</b>${score.toFixed(2)}</span>
              <span><b>Weighted R/R</b>${net.toFixed(2)}x</span>
              <span><b>T1/T2 R/R</b>${rr1.toFixed(2)}x / ${rr2.toFixed(2)}x</span>
              <span><b>Market</b>${escapeHtml(item.market_regime || "Unknown")}</span>
              <span><b>Sector</b>${escapeHtml(item.sector_regime || "Unknown")}</span>
              <span><b>Confirm</b>${item.entry_confirmation_passed ? "Passed" : "Missing"}</span>
            </div>
            <div class="diagnostic-levels">
              <span><b>Price</b>${diagnosticPrice(item.current_price_usd)}</span>
              <span><b>Buy zone</b>${diagnosticPrice(item.buy_zone_low)} - ${diagnosticPrice(item.buy_zone_high)}</span>
              <span><b>Stop</b>${diagnosticPrice(item.stop_loss)}</span>
              <span><b>Targets</b>${diagnosticPrice(item.target_1)} / ${diagnosticPrice(item.target_2)}</span>
            </div>
            ${selectionLines(item)}
            <p>${escapeHtml(item.reason || "No reason provided")}</p>
          </div>
        </article>
      `;
    })
    .join("");
}

function attachDiagnosticItemListeners() {
  const body = document.getElementById("diagnosticModalBody");
  body.querySelectorAll(".diagnostic-chart-button").forEach((button) => {
    button.addEventListener("click", () => openMediaModal(button.dataset.fullSrc || ""));
  });
  body.querySelectorAll(".generate-diagnostic-chart").forEach((button) => {
    button.addEventListener("click", () => generateDiagnosticChart(button));
  });
}

async function generateDiagnosticChart(button) {
  const ticker = button.dataset.ticker || "";
  if (!ticker || button.disabled) return;
  button.disabled = true;
  button.querySelector("span").textContent = "Generating";
  try {
    const params = new URLSearchParams({
      ticker,
      diagnostic_key: state.diagnostic.key || "WATCH_READY",
    });
    const response = await fetch(`/agent/diagnostic-chart?${params}`, { method: "POST" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.status !== "ok") {
      throw new Error(payload.detail || `Chart failed (${response.status})`);
    }
    updateDiagnosticChart(ticker, payload.chart_url);
    renderDiagnosticModalContent();
  } catch (error) {
    button.disabled = false;
    button.querySelector("span").textContent = "Retry chart";
    button.title = error.message || "Chart generation failed";
  }
}

function updateDiagnosticChart(ticker, chartUrl) {
  state.diagnostic.items.forEach((item) => {
    if (String(item.ticker || "").toUpperCase() === String(ticker || "").toUpperCase()) {
      item.chart_url = chartUrl;
    }
  });
  const groups = state.data?.decision_diagnostics?.drilldowns || {};
  Object.values(groups).forEach((items) => {
    (items || []).forEach((item) => {
      if (String(item.ticker || "").toUpperCase() === String(ticker || "").toUpperCase()) {
        item.chart_url = chartUrl;
      }
    });
  });
  renderWatchReadyPanel(state.data?.decision_diagnostics || {});
}

function diagnosticPrice(value) {
  const number = Number(value || 0);
  return number ? usd.format(number) : "N/A";
}

function renderWatchReadyPanel(diagnostics) {
  const list = document.getElementById("watchReadyList");
  const meta = document.getElementById("watchReadyMeta");
  const button = document.getElementById("openWatchReadyDiagnostics");
  if (!list || !meta || !button) return;
  const items = diagnostics?.drilldowns?.WATCH_READY || [];
  const total = Number(diagnostics?.watch_ready_count || items.length || 0);
  button.disabled = total === 0;
  meta.textContent = total
    ? `${total} candidate${total === 1 ? "" : "s"} staged for regular-session confirmation`
    : "No WATCH_READY candidates in the latest scan";
  if (!items.length) {
    list.innerHTML = '<div class="empty-state compact">No WATCH_READY candidates right now.</div>';
    return;
  }
  const topItems = items.slice(0, 6);
  list.innerHTML = topItems
    .map((item) => {
      const score = Number(item.setup_score || 0);
      const rr = Number(item.weighted_net_rr || item.net_rr || 0);
      const chart = item.chart_url
        ? `<img src="${escapeHtml(item.chart_url)}?v=${Date.now()}" alt="${escapeHtml(item.ticker)} chart" />`
        : `<span>No chart</span>`;
      return `
        <button class="watch-ready-card" type="button" data-watch-ready-open="true">
          <span class="watch-ready-thumb">${chart}</span>
          <strong>${escapeHtml(tickerLabel(item))}</strong>
          <small>${escapeHtml(tickerMeta(item, item.setup_type || "Setup"))}</small>
          <em>Score ${score.toFixed(2)} / R/R ${rr.toFixed(2)}x</em>
        </button>
      `;
    })
    .join("");
  list.querySelectorAll("[data-watch-ready-open]").forEach((card) => {
    card.addEventListener("click", () => openDiagnosticModal("WATCH_READY", "WATCH_READY"));
  });
}

function renderEquity(curve, summary) {
  const pnlBadge = document.getElementById("pnlBadge");
  pnlBadge.textContent = formatPct(summary.total_pnl_pct);
  pnlBadge.className = `badge ${summary.total_pnl_ils >= 0 ? "good" : "bad"}`;
  const runCount = Math.max(0, (curve || []).length - 1);
  document.getElementById("equityMeta").textContent = `${runCount} tracked ${runCount === 1 ? "run" : "runs"}`;

  const panel = document.getElementById("equityChartPanel");
  if (panel?.hidden) return;

  const canvas = document.getElementById("equityChart");
  const context = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  const chartHeight = 280;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(chartHeight * dpr));
  context.scale(dpr, dpr);
  context.clearRect(0, 0, rect.width, chartHeight);

  const points = curve.length ? curve : [{ equity_ils: summary.starting_capital_ils, pnl_ils: 0 }];
  const values = points.map((point) => Number(point.equity_ils || 0));
  const domain = equityDomain(values, Number(summary.starting_capital_ils || 0));
  const pad = { left: 76, right: 24, top: 24, bottom: 36 };
  const width = rect.width - pad.left - pad.right;
  const height = chartHeight - pad.top - pad.bottom;
  const yFor = (value) => pad.top + height - ((value - domain.min) / domain.range) * height;

  context.strokeStyle = "#dfe5ee";
  context.lineWidth = 1;
  context.fillStyle = "#667085";
  context.font = "12px Segoe UI, Arial";
  context.textAlign = "right";
  context.textBaseline = "middle";
  domain.ticks.forEach((value) => {
    const y = yFor(value);
    context.beginPath();
    context.moveTo(pad.left, y);
    context.lineTo(pad.left + width, y);
    context.stroke();
    context.fillText(formatAxisMoney(value, domain.range), pad.left - 12, y);
  });

  const baseline = Number(summary.starting_capital_ils || 0);
  if (baseline >= domain.min && baseline <= domain.max) {
    const y = yFor(baseline);
    context.save();
    context.setLineDash([5, 5]);
    context.strokeStyle = "#a8b3c2";
    context.beginPath();
    context.moveTo(pad.left, y);
    context.lineTo(pad.left + width, y);
    context.stroke();
    context.restore();
  }

  const coords = points.map((point, index) => {
    const x = pad.left + (points.length === 1 ? width : (width / (points.length - 1)) * index);
    const y = yFor(Number(point.equity_ils || 0));
    return { x, y };
  });

  context.strokeStyle = summary.total_pnl_ils >= 0 ? "#14795c" : "#bd3d35";
  context.lineWidth = 3;
  context.lineCap = "round";
  context.lineJoin = "round";
  context.beginPath();
  coords.forEach((point, index) => {
    if (index === 0) context.moveTo(point.x, point.y);
    else context.lineTo(point.x, point.y);
  });
  context.stroke();

  context.fillStyle = context.strokeStyle;
  coords.forEach((point) => {
    context.beginPath();
    context.arc(point.x, point.y, 4, 0, Math.PI * 2);
    context.fill();
  });

}

function renderPositionsOverview(positions, liveUpdatedAt = "") {
  const panel = document.getElementById("positionsOverviewPanel");
  const grid = document.getElementById("positionsOverview");
  document.getElementById("positionsOverviewMeta").textContent = liveUpdatedAt
    ? `${positions.length} open positions - live ${formatDate(liveUpdatedAt)}`
    : `${positions.length} open positions`;
  if (!positions.length) {
    grid.innerHTML = '<div class="empty-state compact">No open positions</div>';
    panel.classList.add("is-empty");
    return;
  }
  panel.classList.remove("is-empty");
  grid.innerHTML = positions
    .map((position) => {
      const pnlClass = position.unrealized_pnl_ils >= 0 ? "money-pos" : "money-neg";
      return `
        <article class="position-mini-card">
          <div class="position-mini-head">
            <div class="ticker-cell">
              <strong>${escapeHtml(tickerLabel(position))}</strong>
              <span class="meta">${escapeHtml(tickerMeta(position, `${position.quantity} shares`))}</span>
            </div>
            <span class="badge neutral">${escapeHtml(position.status)}</span>
          </div>
          <div class="position-mini-grid">
            <span><b>Entry</b>${usd.format(position.entry_price_usd)}</span>
            <span><b>Now</b>${usd.format(position.current_price_usd)}</span>
            <span><b>Stop</b>${formatLevelWithPercent(position.stop_loss, position.entry_price_usd)}</span>
            <span><b>TP</b>${formatLevelWithPercent(position.target_1, position.entry_price_usd)} / ${formatLevelWithPercent(position.target_2, position.entry_price_usd)}</span>
            <span><b>Exposure</b>${money.format(position.exposure_ils)}</span>
            <span><b>P/L</b><em class="${pnlClass}">${formatSignedMoney(position.unrealized_pnl_ils)}</em></span>
          </div>
          <div class="progress" title="${position.progress_to_target_1}% to target 1">
            <span style="width:${Math.max(0, Math.min(100, position.progress_to_target_1))}%"></span>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderPositions(positions, liveUpdatedAt = "") {
  const meta = document.getElementById("positionMeta");
  const body = document.getElementById("positionsBody");
  if (!meta || !body) return;

  meta.textContent = liveUpdatedAt
    ? `${positions.length} open positions - live ${formatDate(liveUpdatedAt)}`
    : `${positions.length} open positions`;
  if (!positions.length) {
    body.innerHTML = `<tr><td colspan="12" class="empty-state">No open positions</td></tr>`;
    return;
  }

  body.innerHTML = positions
    .map(
      (position) => `
        <tr>
          <td>
            <div class="ticker-cell">
              <strong>${escapeHtml(tickerLabel(position))}</strong>
              <span class="meta">${escapeHtml(tickerMeta(position, formatDate(position.entry_date)))}</span>
            </div>
          </td>
          <td><span class="badge neutral">${escapeHtml(position.status)}</span></td>
          <td>${position.quantity}</td>
          <td>${usd.format(position.entry_price_usd)}</td>
          <td>${usd.format(position.current_price_usd)}</td>
          <td>${formatLevelWithPercent(position.stop_loss, position.entry_price_usd)}</td>
          <td>${formatLevelWithPercent(position.target_1, position.entry_price_usd)} / ${formatLevelWithPercent(position.target_2, position.entry_price_usd)}</td>
          <td class="${position.unrealized_pnl_ils >= 0 ? "money-pos" : "money-neg"}">${formatSignedMoney(position.unrealized_pnl_ils)}</td>
          <td>${money.format(position.exposure_ils)}</td>
          <td>${money.format(position.open_risk_ils)}</td>
          <td>${renderPotential(position)}</td>
          <td>
            <div class="progress" title="${position.progress_to_target_1}% to target 1">
              <span style="width:${Math.max(0, Math.min(100, position.progress_to_target_1))}%"></span>
            </div>
          </td>
        </tr>
      `,
    )
    .join("");
}

function renderPositionCharts(positions) {
  const grid = document.getElementById("positionChartsGrid");
  const withCharts = positions.filter((position) => position.chart_url);
  document.getElementById("positionChartsMeta").textContent = `${withCharts.length} chart${withCharts.length === 1 ? "" : "s"} available`;
  if (!positions.length) {
    grid.innerHTML = '<div class="empty-state">No open positions to chart</div>';
    return;
  }

  grid.innerHTML = positions
    .map((position) => {
      const chart = position.chart_url
        ? `<button class="position-chart-media clickable-media" type="button" data-full-src="${escapeHtml(position.chart_url)}?v=${Date.now()}">
            <img src="${escapeHtml(position.chart_url)}?v=${Date.now()}" alt="${escapeHtml(position.ticker)} chart" />
          </button>`
        : `<div class="position-chart-media missing"><span>No chart saved</span></div>`;
      return `
        <article class="position-chart-card">
          ${chart}
          <div class="position-chart-copy">
            <div class="ticker-cell">
              <strong>${escapeHtml(tickerLabel(position))}</strong>
              <span class="meta">${escapeHtml(tickerMeta(position, formatDate(position.entry_date)))}</span>
            </div>
            ${selectionLines(position)}
          </div>
        </article>
      `;
    })
    .join("");
  grid.querySelectorAll(".clickable-media").forEach((button) => {
    button.addEventListener("click", () => openMediaModal(button.dataset.fullSrc || ""));
  });
}

function renderActions() {
  const setups = state.actions || [];
  document.getElementById("actionMeta").textContent = `${Math.min(setups.length, state.actionTotal)} loaded / ${state.actionTotal} latest setup decisions`;
  const list = document.getElementById("actionsList");
  const toggle = document.getElementById("toggleActions");
  const loadMoreRow = document.getElementById("actionsLoadMoreRow");
  const loadMore = document.getElementById("loadMoreActions");
  toggle.setAttribute("aria-expanded", String(state.actionsExpanded));
  toggle.innerHTML = `
    <i data-lucide="${state.actionsExpanded ? "chevron-up" : "chevron-down"}"></i>
    <span>${state.actionsExpanded ? "Hide" : "Show"}</span>
  `;
  if (!setups.length) {
    list.innerHTML = '<div class="empty-state">No setup decisions</div>';
    loadMoreRow.hidden = true;
    toggle.disabled = true;
    return;
  }
  toggle.disabled = false;
  if (!state.actionsExpanded) {
    list.innerHTML = '<div class="empty-state compact">Actions are collapsed. Open to load the latest 10 decisions.</div>';
    loadMoreRow.hidden = true;
    return;
  }
  if (state.actionsLoading && !setups.length) {
    list.innerHTML = '<div class="empty-state compact">Loading latest actions...</div>';
    loadMoreRow.hidden = true;
    return;
  }

  list.innerHTML = setups
    .map(
      (setup) => `
        <div class="action-row">
          ${setup.chart_url
            ? `<button class="action-chart-button" type="button" data-full-src="${escapeHtml(setup.chart_url)}?v=${Date.now()}">
                <img class="action-chart" src="${escapeHtml(setup.chart_url)}?v=${Date.now()}" alt="${escapeHtml(setup.ticker)} chart" />
              </button>`
            : `<div class="action-chart missing"></div>`}
          <div class="ticker-cell">
            <strong>${escapeHtml(tickerLabel(setup))}</strong>
            <span class="meta">${escapeHtml(decisionMeta(setup) || setup.sector || "Unknown")}</span>
          </div>
          <span class="${actionBadgeClass(setup.action)}">${escapeHtml(setup.action || "UNKNOWN")}</span>
          <div>
            <div class="setup-title-line">
              <strong>${escapeHtml(setup.setup_type || "")}</strong>
              ${entryChecklist(setup)}
            </div>
            <p>${escapeHtml(selectionText(setup))}</p>
            ${riskCheckPills(setup)}
          </div>
        </div>
      `,
    )
    .join("");
  loadMoreRow.hidden = !state.actionHasMore;
  if (!loadMoreRow.hidden) {
    const nextCount = Math.min(ACTIONS_PAGE_SIZE, Math.max(0, state.actionTotal - setups.length));
    loadMore.disabled = state.actionsLoading;
    loadMore.querySelector("span").textContent = state.actionsLoading ? "Loading" : `Load ${nextCount} more`;
  }
  list.querySelectorAll(".action-chart-button").forEach((button) => {
    button.addEventListener("click", () => openMediaModal(button.dataset.fullSrc || ""));
  });
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function setupMediaModal() {
  const modal = document.getElementById("mediaModal");
  const close = document.getElementById("mediaModalClose");
  close.addEventListener("click", closeMediaModal);
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeMediaModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isMediaModalOpen()) {
      event.marketLensMediaHandled = true;
      closeMediaModal();
    }
  });
}

function openMediaModal(src) {
  if (!src) return;
  const modal = document.getElementById("mediaModal");
  const image = document.getElementById("mediaModalImage");
  image.src = src;
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
}

function closeMediaModal() {
  const modal = document.getElementById("mediaModal");
  const image = document.getElementById("mediaModalImage");
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
  image.removeAttribute("src");
}

function isMediaModalOpen() {
  return Boolean(document.getElementById("mediaModal")?.classList.contains("open"));
}

function renderCalibration(rows) {
  const list = document.getElementById("calibrationList");
  const total = rows.reduce((sum, row) => sum + Number(row.trades || 0), 0);
  document.getElementById("calibrationMeta").textContent = total
    ? `${total} closed trades tracked`
    : "Waiting for closed trades";
  if (!rows.length) {
    list.innerHTML = '<div class="empty-state">No calibration data yet</div>';
    return;
  }
  list.innerHTML = rows
    .map(
      (row) => `
        <div class="calibration-row">
          <strong>${escapeHtml(row.bucket)}</strong>
          <span>${Number(row.trades || 0)} trades</span>
          <span>${Number(row.win_rate || 0).toFixed(1)}% win</span>
          <span class="${Number(row.pnl_ils || 0) >= 0 ? "money-pos" : "money-neg"}">${formatSignedMoney(row.pnl_ils || 0)}</span>
        </div>
      `,
    )
    .join("");
}

function renderTrades() {
  const trades = state.trades || [];
  document.getElementById("tradeMeta").textContent = `${state.closedTradeTotal} closed / ${Math.min(trades.length, state.tradeTotal)} of ${state.tradeTotal} logged`;
  const list = document.getElementById("tradeList");
  const toggle = document.getElementById("toggleTrades");
  const loadMoreRow = document.getElementById("tradesLoadMoreRow");
  const loadMore = document.getElementById("loadMoreTrades");
  toggle.setAttribute("aria-expanded", String(state.tradesExpanded));
  toggle.innerHTML = `
    <i data-lucide="${state.tradesExpanded ? "chevron-up" : "chevron-down"}"></i>
    <span>${state.tradesExpanded ? "Hide" : "Show"}</span>
  `;
  if (!trades.length) {
    list.innerHTML = '<div class="empty-state">No trades logged</div>';
    loadMoreRow.hidden = true;
    toggle.disabled = true;
    return;
  }
  toggle.disabled = false;
  if (!state.tradesExpanded) {
    list.innerHTML = '<div class="empty-state compact">Trade log is collapsed. Open to load the latest 10 trades.</div>';
    loadMoreRow.hidden = true;
    return;
  }
  if (state.tradesLoading && !trades.length) {
    list.innerHTML = '<div class="empty-state compact">Loading latest trades...</div>';
    loadMoreRow.hidden = true;
    return;
  }
  list.innerHTML = trades
    .map((trade) => {
      const cash = trade.action === "BUY_SIMULATED" ? trade.cash_out_ils : trade.cash_in_ils;
      return `
        <div class="trade-row">
          <div class="ticker-cell">
            <strong>${escapeHtml(tickerLabel(trade))}</strong>
            <span class="meta">${escapeHtml(tickerMeta(trade, formatDate(trade.timestamp)))}</span>
          </div>
          <span class="${actionBadgeClass(trade.action)}">${escapeHtml(trade.action)}</span>
          <div>
            <strong>${trade.quantity} shares / ${money.format(cash)}</strong>
            <p>${escapeHtml(tradePotentialText(trade))}</p>
          </div>
        </div>
      `;
    })
    .join("");
  loadMoreRow.hidden = !state.tradeHasMore;
  if (!loadMoreRow.hidden) {
    const nextCount = Math.min(TRADES_PAGE_SIZE, Math.max(0, state.tradeTotal - trades.length));
    loadMore.disabled = state.tradesLoading;
    loadMore.querySelector("span").textContent = state.tradesLoading ? "Loading" : `Load ${nextCount} more`;
  }
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function renderSummary(run) {
  document.getElementById("summaryText").textContent = run.summary_text || "No summary available.";
  document.getElementById("summaryMeta").textContent = run.run_id ? `Run ${run.run_id}` : "Agent written update";
}

function syncDateUrl(selectedDate) {
  const url = new URL(window.location.href);
  if (selectedDate) url.searchParams.set("date", selectedDate);
  else url.searchParams.delete("date");
  window.history.replaceState({}, "", url);
}

function tickerLabel(item) {
  const ticker = item.ticker || "";
  const name = item.company_name || ticker;
  return name && name !== ticker ? `${ticker} (${name})` : ticker;
}

function tickerMeta(item, fallback = "") {
  return [item.sector || "Unknown", fallback].filter(Boolean).join(" - ");
}

function selectionText(item) {
  return item.selection_context || item.decision_json?.reason || item.feedback || item.notes || item.reason || "";
}

function selectionLines(item) {
  const lines = selectionText(item)
    .split("|")
    .map((line) => line.trim())
    .filter(Boolean);
  if (!lines.length) return '<p class="selection-lines empty">No selection context</p>';
  return `<div class="selection-lines">${lines.map((line) => `<span>${escapeHtml(line)}</span>`).join("")}</div>`;
}

function decisionMeta(item) {
  const decision = item.decision_json || {};
  const parts = [];
  if (decision.market_regime) parts.push(`Market ${decision.market_regime}`);
  if (decision.sector_regime) parts.push(`Sector ${decision.sector_regime}`);
  if (decision.net_rr !== undefined && decision.net_rr !== null && decision.net_rr !== "") {
    parts.push(`Net R/R ${Number(decision.net_rr || 0).toFixed(2)}x`);
  }
  return parts.join(" - ");
}

function riskCheckPills(item) {
  const d = item.decision_json || {};
  if (!Object.keys(d).length) return "";
  const checks = [];
  if (d.gross_rr_1 !== undefined) checks.push(["RR1", `${Number(d.gross_rr_1 || 0).toFixed(2)}x`]);
  if (d.gross_rr_2 !== undefined) checks.push(["RR2", `${Number(d.gross_rr_2 || 0).toFixed(2)}x`]);
  if (d.net_rr !== undefined) checks.push(["Net", `${Number(d.net_rr || 0).toFixed(2)}x`]);
  if (d.earnings_blackout) checks.push(["Earnings", "Blackout"]);
  else if (d.earnings_date) checks.push(["Earnings", `${d.earnings_date}`]);
  else checks.push(["Earnings", "N/A"]);
  if (d.sector_exposure_after !== undefined && d.sector_exposure_cap !== undefined) {
    checks.push(["Sector exp", `${money.format(d.sector_exposure_after)} / ${money.format(d.sector_exposure_cap)}`]);
  }
  if (d.factor_exposure_limit_exceeded) checks.push(["Factor", "Limit"]);
  else if (Array.isArray(d.factor_tags) && d.factor_tags.length) checks.push(["Factor", d.factor_tags.slice(0, 2).join(", ")]);
  if (d.correlation_warning) {
    checks.push(["Corr", `${d.highest_correlation_ticker || ""} ${Number(d.highest_correlation_value || 0).toFixed(2)}`]);
  } else {
    checks.push(["Corr", "OK"]);
  }
  if (d.target_feasibility_status) checks.push(["Targets", d.target_feasibility_status]);
  if (d.position_size_adjusted) checks.push(["Size", `Adjusted to ${d.adjusted_position_size || 0}`]);
  return `<div class="risk-checks">${checks
    .map(([label, value]) => `<span><b>${escapeHtml(label)}</b> ${escapeHtml(value)}</span>`)
    .join("")}</div>`;
}

function entryChecklist(item) {
  const d = item.decision_json || {};
  const action = String(item.action || d.final_action || "").toUpperCase();
  const setupType = String(item.setup_type || d.setup_type || "");
  if (!Object.keys(d).length || setupType === "No Trade" || !["WATCH", "WATCH_READY", "BUY_SIMULATED"].includes(action)) {
    return "";
  }

  const items = buildEntryChecklistItems(item);
  const passed = items.filter((check) => check.status === "pass").length;
  const missing = items.filter((check) => check.status !== "pass").length;
  const summary = action === "BUY_SIMULATED"
    ? "Entry checklist passed"
    : `${missing} condition${missing === 1 ? "" : "s"} still missing`;

  return `
    <span class="entry-checklist">
      <button class="entry-check-button" type="button" aria-label="Entry checklist for ${escapeHtml(item.ticker || "")}">
        <i data-lucide="${missing ? "circle-alert" : "circle-check"}"></i>
      </button>
      <span class="entry-check-popover" role="tooltip">
        <strong>${escapeHtml(summary)}</strong>
        <span class="entry-check-subtitle">${escapeHtml(checklistSubtitle(d))}</span>
        <span class="entry-check-items">
          ${items.map(renderChecklistItem).join("")}
        </span>
      </span>
    </span>
  `;
}

function buildEntryChecklistItems(item) {
  const d = item.decision_json || {};
  const minNetRr = Number(d.minimum_net_rr_required ?? (d.market_regime === "NEUTRAL" ? 2.5 : d.market_regime === "BULL" ? 2.0 : Infinity));
  const minSetupScore = Number(d.minimum_setup_score_required ?? (d.market_regime === "NEUTRAL" ? 0.55 : d.market_regime === "BULL" ? 0.45 : 0));
  const hasSetup = String(item.setup_type || d.setup_type || "") !== "No Trade";
  const setupScore = Number(d.setup_score ?? item.score ?? 0);
  const netRr = Number(d.net_rr ?? 0);
  const rr1 = Number(d.net_rr_1 ?? d.gross_rr_1 ?? 0);
  const rr2 = Number(d.net_rr_2 ?? d.gross_rr_2 ?? 0);
  const targetStatus = String(d.target_feasibility_status || "");

  const checks = [
    {
      status: hasSetup ? "pass" : "fail",
      label: "Technical setup",
      detail: hasSetup ? `${item.setup_type} detected` : "No clean setup detected",
    },
    {
      status: d.market_regime === "BEAR" ? "fail" : "pass",
      label: "Market regime",
      detail: d.market_regime === "BEAR" ? "Bear regime blocks new buys" : `${d.market_regime || "Unknown"} allows review`,
    },
    {
      status: d.sector_regime === "WEAK" ? "fail" : d.sector_regime === "NEUTRAL" ? "warn" : "pass",
      label: "Sector regime",
      detail: d.sector_regime === "WEAK"
        ? "Weak sector blocks auto-buy"
        : d.sector_regime === "NEUTRAL"
          ? "Neutral sector needs cleaner score"
          : `${d.sector_regime || "Unknown"} sector`,
    },
    {
      status: Number.isFinite(minNetRr) && netRr >= minNetRr ? "pass" : "fail",
      label: "Net R/R",
      detail: Number.isFinite(minNetRr)
        ? `${netRr.toFixed(2)}x / needs ${minNetRr.toFixed(2)}x`
        : "Bear market blocks new buys",
    },
    {
      status: rr1 >= 1.2 && rr2 >= 2.0 ? "pass" : "warn",
      label: "Targets R/R",
      detail: `T1 ${rr1.toFixed(2)}x, T2 ${rr2.toFixed(2)}x`,
    },
    {
      status: targetStatus === "OK" ? "pass" : "warn",
      label: "Target quality",
      detail: targetStatus === "OK" ? "ATR distance is acceptable" : humanizeStatus(targetStatus || "Needs cleaner target distance"),
    },
    {
      status: setupScore >= minSetupScore ? "pass" : "warn",
      label: "Setup score",
      detail: minSetupScore ? `${setupScore.toFixed(2)} / needs ${minSetupScore.toFixed(2)}` : `${setupScore.toFixed(2)}`,
    },
    {
      status: d.earnings_blackout ? "fail" : "pass",
      label: "Earnings",
      detail: d.earnings_blackout ? "Blackout active" : d.earnings_date ? `Next: ${d.earnings_date}` : "No blackout",
    },
    {
      status: d.sector_exposure_limit_exceeded ? "fail" : "pass",
      label: "Sector exposure",
      detail: d.sector_exposure_cap !== undefined
        ? `${money.format(d.sector_exposure_after || 0)} / ${money.format(d.sector_exposure_cap || 0)}`
        : "Within limit",
    },
    {
      status: d.factor_exposure_limit_exceeded ? "fail" : "pass",
      label: "Factor exposure",
      detail: d.factor_exposure_limit_exceeded ? "Factor limit exceeded" : "Within factor limit",
    },
    {
      status: d.correlation_warning ? "warn" : "pass",
      label: "Correlation",
      detail: d.correlation_warning
        ? `${d.highest_correlation_ticker || "Position"} ${Number(d.highest_correlation_value || 0).toFixed(2)}`
        : "Acceptable",
    },
  ];

  const reason = String(d.reason || item.feedback || "");
  const missingReason = reason.replace(/^(WATCH_READY|WATCH|SKIP|BUY_SIMULATED):\s*/i, "");
  if (["WATCH", "WATCH_READY"].includes(String(item.action || "").toUpperCase()) && missingReason) {
    checks.push({
      status: "need",
      label: "What must improve",
      detail: missingReason,
    });
  }
  return checks;
}

function renderChecklistItem(check) {
  const icon = check.status === "pass" ? "check" : check.status === "fail" ? "x" : "alert-triangle";
  return `
    <span class="entry-check-item ${escapeHtml(check.status)}">
      <i data-lucide="${icon}"></i>
      <span>
        <b>${escapeHtml(check.label)}</b>
        <small>${escapeHtml(check.detail)}</small>
      </span>
    </span>
  `;
}

function checklistSubtitle(decision) {
  const action = decision.final_action || "";
  const regime = decision.market_regime || "Unknown market";
  const sector = decision.sector_regime || "Unknown sector";
  const rr = decision.net_rr !== undefined ? `Net R/R ${Number(decision.net_rr || 0).toFixed(2)}x` : "Net R/R unavailable";
  return `${action} - ${regime} market - ${sector} sector - ${rr}`;
}

function humanizeStatus(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function renderPotential(item) {
  const plan = Number(item.potential_profit_plan_ils || 0);
  const t1 = Number(item.potential_profit_t1_ils || 0);
  const t2 = Number(item.potential_profit_t2_ils || 0);
  const rr = Number(item.reward_to_risk_plan || 0);
  if (!plan && !t1 && !t2) return '<span class="meta">No upside</span>';
  return `
    <div class="potential-cell">
      <strong class="money-pos">${money.format(plan)}</strong>
      <span class="meta">T1 ${money.format(t1)} / T2 ${money.format(t2)}</span>
      <small>${rr ? `${rr.toFixed(2)}x plan R/R` : "Plan: 50% T1 / 50% T2"}</small>
    </div>
  `;
}

function tradePotentialText(trade) {
  if (trade.action !== "BUY_SIMULATED") {
    const parts = [formatDate(trade.timestamp)];
    if (trade.pnl_ils !== undefined && trade.pnl_ils !== null) parts.push(`P/L ${formatSignedMoney(trade.pnl_ils)}`);
    if (trade.r_multiple !== undefined && trade.r_multiple !== null && trade.r_multiple !== "") {
      parts.push(`${Number(trade.r_multiple || 0).toFixed(2)}R`);
    }
    if (trade.price_usd) parts.push(`at ${usd.format(trade.price_usd)}`);
    return parts.join(" - ");
  }
  return `${formatDate(trade.timestamp)} - potential ${money.format(trade.potential_profit_plan_ils || 0)}`;
}

function actionBadgeClass(action) {
  const value = String(action || "").toUpperCase();
  if (["BUY_SIMULATED", "TAKE_PROFIT", "TAKE_PARTIAL_PROFIT"].includes(value)) return "badge good";
  if (["EXIT_STOP"].includes(value)) return "badge bad";
  if (["WATCH_READY"].includes(value)) return "badge neutral";
  if (["WATCH", "SKIP"].includes(value)) return "badge warn";
  return "badge neutral";
}

function countTradeReady(setups) {
  return (setups || []).filter((setup) => {
    const action = String(setup.action || setup.decision_json?.final_action || "").toUpperCase();
    return action === "BUY_SIMULATED" || action === "WATCH_READY";
  }).length;
}

function formatSignedMoney(value) {
  const number = Number(value || 0);
  const formatted = money.format(Math.abs(number));
  return number < 0 ? `-${formatted}` : `+${formatted}`;
}

function formatPct(value) {
  const number = Number(value || 0);
  const sign = number > 0 ? "+" : "";
  return `${sign}${number.toFixed(2)}%`;
}

function equityDomain(values, baseline) {
  const validValues = values.filter((value) => Number.isFinite(value));
  const rawMin = Math.min(...validValues, baseline);
  const rawMax = Math.max(...validValues, baseline);
  const rawRange = Math.max(0, rawMax - rawMin);
  const minVisibleRange = Math.max(1000, Math.abs(baseline || rawMax || 1) * 0.02);
  const visibleRange = Math.max(rawRange * 1.3, minVisibleRange);
  const center = rawRange > 0 ? (rawMin + rawMax) / 2 : baseline || rawMax || 0;
  const min = center - visibleRange / 2;
  const max = center + visibleRange / 2;
  const range = Math.max(1, max - min);
  const ticks = Array.from({ length: 5 }, (_, index) => min + (range / 4) * index);
  return { min, max, range, ticks };
}

function formatAxisMoney(value, range) {
  const number = Number(value || 0);
  if (Math.abs(number) >= 1000) {
    const decimals = range < 10000 ? 1 : 0;
    return `$${(number / 1000).toFixed(decimals)}k`;
  }
  return `$${Math.round(number)}`;
}

function formatDate(value) {
  if (!value) return "Not available";
  const normalized = String(value).includes("T") ? String(value) : String(value).replace(" ", "T");
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
