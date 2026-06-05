const form = document.querySelector("#scanForm");
const tickersInput = document.querySelector("#tickers");
const minRrInput = document.querySelector("#minRr");
const analysisPeriodInput = document.querySelector("#analysisPeriod");
const scanButton = document.querySelector("#scanButton");
const resultsEl = document.querySelector("#results");
const messageEl = document.querySelector("#message");
const runMeta = document.querySelector("#runMeta");
const filterEl = document.querySelector("#filter");
const savedStatusEl = document.querySelector("#savedStatus");
const healthEl = document.querySelector("#health");
const scanCount = document.querySelector("#scanCount");
const setupCount = document.querySelector("#setupCount");
const errorCount = document.querySelector("#errorCount");
const dialog = document.querySelector("#chartDialog");
const dialogImage = document.querySelector("#dialogImage");
const dialogTitle = document.querySelector("#dialogTitle");
const closeDialog = document.querySelector("#closeDialog");

let lastPayload = { results: [], errors: {}, charts: {}, saved_setups: [] };
let currentView = "scan";
let savedSetups = [];
const sessionId = getSessionId();

document.querySelectorAll("[data-preset]").forEach((button) => {
  button.addEventListener("click", () => {
    tickersInput.value = button.dataset.preset;
  });
});

document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", async () => {
    currentView = button.dataset.view;
    document.querySelectorAll("[data-view]").forEach((viewButton) => {
      viewButton.classList.toggle("active", viewButton.dataset.view === currentView);
    });
    filterEl.classList.toggle("hidden", currentView !== "scan");
    savedStatusEl.classList.toggle("hidden", currentView !== "saved");
    if (currentView === "saved") {
      await loadSavedSetups();
    } else {
      render(lastPayload);
    }
  });
});

filterEl.addEventListener("change", () => render(lastPayload));
savedStatusEl.addEventListener("change", loadSavedSetups);
closeDialog.addEventListener("click", () => dialog.close());

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const tickers = parseTickers(tickersInput.value);
  const minRr = Number(minRrInput.value || 2);
  const analysisPeriod = analysisPeriodInput.value || "6mo";

  if (tickers.length === 0) {
    showMessage("Enter at least one ticker.");
    return;
  }

  currentView = "scan";
  syncViewControls();
  setLoading(true);
  showMessage("");
  runMeta.textContent = `Scanning ${tickers.join(", ")} over ${periodLabel(analysisPeriod)}...`;

  try {
    const response = await fetch("/ui/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tickers, min_rr: minRr, analysis_period: analysisPeriod }),
    });

    if (!response.ok) {
      throw new Error(`Scan failed (${response.status})`);
    }

    lastPayload = await response.json();
    render(lastPayload);
  } catch (error) {
    showMessage(error.message || "Scan failed.");
    runMeta.textContent = "Scan failed";
  } finally {
    setLoading(false);
  }
});

async function checkHealth() {
  try {
    const response = await fetch("/health");
    const data = await response.json();
    healthEl.textContent = data.status === "ok" ? "online" : "offline";
    healthEl.classList.toggle("ok", data.status === "ok");
  } catch {
    healthEl.textContent = "offline";
    healthEl.classList.remove("ok");
  }
}

async function loadSavedSetups() {
  setLoadingSaved(true);
  showMessage("");
  const status = savedStatusEl.value;
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  try {
    const response = await fetch(`/setups${query}`);
    if (!response.ok) throw new Error(`Saved setups failed (${response.status})`);
    const payload = await response.json();
    savedSetups = payload.setups || [];
    renderSavedSetups();
  } catch (error) {
    showMessage(error.message || "Saved setups failed.");
    runMeta.textContent = "Saved setups failed";
  } finally {
    setLoadingSaved(false);
  }
}

function parseTickers(value) {
  return [...new Set(value.toUpperCase().split(/[\s,]+/).map((ticker) => ticker.trim()).filter(Boolean))];
}

function setLoading(isLoading) {
  scanButton.disabled = isLoading;
  scanButton.textContent = isLoading ? "Scanning..." : "Scan";
}

function setLoadingSaved(isLoading) {
  runMeta.textContent = isLoading ? "Loading saved setups..." : runMeta.textContent;
}

function showMessage(text) {
  messageEl.textContent = text;
  messageEl.classList.toggle("hidden", !text);
}

function syncViewControls() {
  document.querySelectorAll("[data-view]").forEach((viewButton) => {
    viewButton.classList.toggle("active", viewButton.dataset.view === currentView);
  });
  filterEl.classList.toggle("hidden", currentView !== "scan");
  savedStatusEl.classList.toggle("hidden", currentView !== "saved");
}

function render(payload) {
  currentView = "scan";
  syncViewControls();
  const results = [...payload.results].sort((a, b) => {
    const aTrade = a.setup_type === "No Trade" ? 1 : 0;
    const bTrade = b.setup_type === "No Trade" ? 1 : 0;
    return aTrade - bTrade || b.score - a.score;
  });
  const errors = payload.errors || {};
  const charts = payload.charts || {};
  const saved = payload.saved_setups || [];
  const analysisPeriod = payload.analysis_period || analysisPeriodInput.value || "6mo";
  const mode = filterEl.value;
  const filtered = results.filter((result) => {
    if (mode === "setups") return result.setup_type !== "No Trade";
    if (mode === "no-trade") return result.setup_type === "No Trade";
    return true;
  });

  const setups = results.filter((result) => result.setup_type !== "No Trade").length;
  scanCount.textContent = String(results.length);
  setupCount.textContent = String(setups);
  errorCount.textContent = String(Object.keys(errors).length);
  runMeta.textContent = results.length
    ? `${results.length} result${results.length === 1 ? "" : "s"} - ${setups} setup${setups === 1 ? "" : "s"} - ${periodLabel(analysisPeriod)}`
    : "No completed scans";

  showMessage("");
  if (saved.length) {
    showMessage(`${saved.length} setup${saved.length === 1 ? "" : "s"} saved to the shared watchlist.`);
  }
  if (Object.keys(errors).length) {
    showMessage(Object.entries(errors).map(([ticker, error]) => `${ticker}: ${error}`).join(" | "));
  }

  if (!filtered.length) {
    resultsEl.className = "results empty";
    resultsEl.innerHTML = `<div class="empty-state">No results match the current filter.</div>`;
    return;
  }

  resultsEl.className = "results";
  resultsEl.innerHTML = filtered.map((result) => renderCard(result, charts[result.ticker], analysisPeriod)).join("");
  bindResultActions(results, charts, analysisPeriod);
}

function renderSavedSetups() {
  const setups = [...savedSetups].sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
  const openCount = setups.filter((setup) => setup.status === "OPEN").length;
  const targetCount = setups.filter((setup) => setup.status === "TARGET1" || setup.status === "TARGET2").length;
  const stoppedCount = setups.filter((setup) => setup.status === "STOPPED").length;

  scanCount.textContent = String(setups.length);
  setupCount.textContent = String(openCount);
  errorCount.textContent = String(stoppedCount);
  runMeta.textContent = `${setups.length} saved - ${openCount} open - ${targetCount} target hit`;

  if (!setups.length) {
    resultsEl.className = "results empty";
    resultsEl.innerHTML = `<div class="empty-state">No saved setups yet.</div>`;
    return;
  }

  resultsEl.className = "results";
  resultsEl.innerHTML = setups.map(renderSavedCard).join("");
  bindSavedActions();
}

function renderCard(result, chartUrl, analysisPeriod) {
  const hasTrade = result.setup_type !== "No Trade";
  const chart = chartUrl
    ? `<div class="chart-wrap">
        <img class="chart-preview" src="${chartUrl}?v=${Date.now()}" alt="${result.ticker} annotated chart" data-chart="${chartUrl}" data-title="${result.ticker} chart" />
        <div class="chart-caption">Click chart to open full size</div>
      </div>`
    : `<div class="chart-missing">No chart generated</div>`;
  const saveButton = hasTrade
    ? `<button class="quiet" type="button" data-save="${escapeHtml(result.ticker)}" data-period="${escapeHtml(analysisPeriod)}">Save setup</button>`
    : "";

  return `
    <article class="result-card">
      <header class="result-head">
        <div class="ticker">
          <strong>${escapeHtml(result.ticker)}</strong>
          <span class="badge ${hasTrade ? "trade" : ""}">${escapeHtml(result.setup_type)}</span>
        </div>
        <div class="score-box">
          <strong>${formatNumber(result.score, 2)}</strong>
          <span>score</span>
        </div>
      </header>
      <div class="result-body">
        <div class="decision">
          <div class="primary-stats">
            ${stat("Price", result.current_price)}
            ${stat("R/R", `${formatNumber(result.risk_reward, 2)}x`)}
            ${stat("Buy Zone", zone(result.buy_zone))}
            ${stat("Stop", result.stop_loss, "stop")}
            ${stat("Targets", `${formatNumber(result.target_1, 2)} / ${formatNumber(result.target_2, 2)}`, "target")}
          </div>
          <p class="reason">${escapeHtml(result.reason)}</p>
          <div class="levels">
            ${detailLevel("Fib 61.8", result.fibonacci?.fib_618)}
            ${detailLevel("Fib Zone", result.fibonacci ? zone(result.fibonacci.zone) : null)}
            ${detailLevel("Swing Low", result.fibonacci?.swing_low)}
            ${detailLevel("Swing High", result.fibonacci?.swing_high)}
          </div>
          ${saveButton}
        </div>
        ${chart}
      </div>
    </article>
  `;
}

function renderSavedCard(setup) {
  const result = setup.result;
  const chart = setup.chart_url
    ? `<div class="chart-wrap">
        <img class="chart-preview" src="${setup.chart_url}?v=${Date.now()}" alt="${result.ticker} saved chart" data-chart="${setup.chart_url}" data-title="${result.ticker} saved chart" />
        <div class="chart-caption">${escapeHtml(formatDate(setup.created_at))}</div>
      </div>`
    : `<div class="chart-missing">No chart saved</div>`;

  return `
    <article class="result-card">
      <header class="result-head">
        <div class="ticker">
          <strong>${escapeHtml(setup.ticker)}</strong>
          <span class="badge trade">${escapeHtml(setup.setup_type)}</span>
          <span class="badge status-${setup.status.toLowerCase()}">${statusLabel(setup.status)}</span>
        </div>
        <div class="score-box">
          <strong>${formatNumber(setup.score, 2)}</strong>
          <span>score</span>
        </div>
      </header>
      <div class="result-body">
        <div class="decision">
          <div class="primary-stats">
            ${stat("Saved", setup.saved_price)}
            ${stat("Current", setup.current_price)}
            ${stat("Stop", setup.stop_loss, "stop")}
            ${stat("Targets", `${formatNumber(setup.target_1, 2)} / ${formatNumber(setup.target_2, 2)}`, "target")}
          </div>
          <p class="reason">${escapeHtml(result.reason)}</p>
          <div class="saved-meta">
            <span>${escapeHtml(periodLabel(setup.analysis_period))}</span>
            <span>${escapeHtml(setup.source)}</span>
            <span>${setup.scan_count} scan${setup.scan_count === 1 ? "" : "s"}</span>
          </div>
          <button class="quiet" type="button" data-refresh="${setup.id}">Refresh status</button>
        </div>
        ${chart}
      </div>
    </article>
  `;
}

function bindResultActions(results, charts, analysisPeriod) {
  resultsEl.querySelectorAll("[data-chart]").forEach(bindChartOpen);
  resultsEl.querySelectorAll("[data-save]").forEach((button) => {
    button.addEventListener("click", async () => {
      const ticker = button.dataset.save;
      const result = results.find((item) => item.ticker === ticker);
      if (!result) return;
      button.disabled = true;
      button.textContent = "Saving...";
      try {
        const response = await fetch("/setups", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            result,
            analysis_period: analysisPeriod,
            chart_url: charts[result.ticker],
            session_id: sessionId,
          }),
        });
        if (!response.ok) throw new Error(`Save failed (${response.status})`);
        button.textContent = "Saved";
      } catch (error) {
        button.disabled = false;
        button.textContent = "Save setup";
        showMessage(error.message || "Save failed.");
      }
    });
  });
}

function bindSavedActions() {
  resultsEl.querySelectorAll("[data-chart]").forEach(bindChartOpen);
  resultsEl.querySelectorAll("[data-refresh]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      button.textContent = "Refreshing...";
      try {
        const response = await fetch(`/setups/${button.dataset.refresh}/refresh`, { method: "POST" });
        if (!response.ok) throw new Error(`Refresh failed (${response.status})`);
        await loadSavedSetups();
      } catch (error) {
        showMessage(error.message || "Refresh failed.");
        button.disabled = false;
        button.textContent = "Refresh status";
      }
    });
  });
}

function bindChartOpen(image) {
  image.addEventListener("click", () => {
    dialogTitle.textContent = image.dataset.title;
    dialogImage.src = image.dataset.chart;
    dialog.showModal();
  });
}

function periodLabel(period) {
  return {
    "3mo": "3 months",
    "6mo": "6 months",
    "1y": "1 year",
    "2y": "2 years",
  }[period] || period;
}

function statusLabel(status) {
  return {
    OPEN: "Open",
    TARGET1: "Target 1",
    TARGET2: "Target 2",
    STOPPED: "Stopped",
  }[status] || status;
}

function stat(label, value, tone = "") {
  return `<div class="stat ${tone}"><span>${label}</span><strong>${valueText(value)}</strong></div>`;
}

function detailLevel(label, value) {
  return `<div class="level"><span>${label}</span><strong>${valueText(value)}</strong></div>`;
}

function valueText(value) {
  if (value === null || value === undefined || value === "0.00 / 0.00") return "-";
  if (typeof value === "number") return formatNumber(value, 2);
  return escapeHtml(String(value));
}

function zone(values) {
  if (!values || values.length !== 2 || (values[0] === 0 && values[1] === 0)) return "-";
  return `${formatNumber(values[0], 2)} - ${formatNumber(values[1], 2)}`;
}

function formatNumber(value, digits) {
  if (typeof value !== "number" || Number.isNaN(value)) return value;
  return value.toFixed(digits);
}

function formatDate(value) {
  if (!value) return "";
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function getSessionId() {
  const key = "marketLensSessionId";
  let value = localStorage.getItem(key);
  if (!value) {
    value = crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
    localStorage.setItem(key, value);
  }
  return value;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

checkHealth();
