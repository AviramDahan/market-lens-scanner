const form = document.querySelector("#scanForm");
const tickersInput = document.querySelector("#tickers");
const minRrInput = document.querySelector("#minRr");
const scanButton = document.querySelector("#scanButton");
const resultsEl = document.querySelector("#results");
const messageEl = document.querySelector("#message");
const runMeta = document.querySelector("#runMeta");
const filterEl = document.querySelector("#filter");
const healthEl = document.querySelector("#health");
const scanCount = document.querySelector("#scanCount");
const setupCount = document.querySelector("#setupCount");
const errorCount = document.querySelector("#errorCount");
const dialog = document.querySelector("#chartDialog");
const dialogImage = document.querySelector("#dialogImage");
const dialogTitle = document.querySelector("#dialogTitle");
const closeDialog = document.querySelector("#closeDialog");

let lastPayload = { results: [], errors: {}, charts: {} };

document.querySelectorAll("[data-preset]").forEach((button) => {
  button.addEventListener("click", () => {
    tickersInput.value = button.dataset.preset;
  });
});

filterEl.addEventListener("change", () => render(lastPayload));

closeDialog.addEventListener("click", () => dialog.close());

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const tickers = parseTickers(tickersInput.value);
  const minRr = Number(minRrInput.value || 2);

  if (tickers.length === 0) {
    showMessage("Enter at least one ticker.");
    return;
  }

  setLoading(true);
  showMessage("");
  runMeta.textContent = `Scanning ${tickers.join(", ")}...`;

  try {
    const response = await fetch("/ui/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tickers, min_rr: minRr }),
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

function parseTickers(value) {
  return [...new Set(value.toUpperCase().split(/[\s,]+/).map((ticker) => ticker.trim()).filter(Boolean))];
}

function setLoading(isLoading) {
  scanButton.disabled = isLoading;
  scanButton.textContent = isLoading ? "Scanning..." : "Scan";
}

function showMessage(text) {
  messageEl.textContent = text;
  messageEl.classList.toggle("hidden", !text);
}

function render(payload) {
  const results = [...payload.results].sort((a, b) => {
    const aTrade = a.setup_type === "No Trade" ? 1 : 0;
    const bTrade = b.setup_type === "No Trade" ? 1 : 0;
    return aTrade - bTrade || b.score - a.score;
  });
  const errors = payload.errors || {};
  const charts = payload.charts || {};
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
    ? `${results.length} result${results.length === 1 ? "" : "s"} · ${setups} setup${setups === 1 ? "" : "s"}`
    : "No completed scans";

  if (Object.keys(errors).length) {
    showMessage(Object.entries(errors).map(([ticker, error]) => `${ticker}: ${error}`).join(" | "));
  }

  if (!filtered.length) {
    resultsEl.className = "results empty";
    resultsEl.innerHTML = `<div class="empty-state">No results match the current filter.</div>`;
    return;
  }

  resultsEl.className = "results";
  resultsEl.innerHTML = filtered.map((result) => renderCard(result, charts[result.ticker])).join("");

  resultsEl.querySelectorAll("[data-chart]").forEach((image) => {
    image.addEventListener("click", () => {
      dialogTitle.textContent = image.dataset.title;
      dialogImage.src = image.dataset.chart;
      dialog.showModal();
    });
  });
}

function renderCard(result, chartUrl) {
  const hasTrade = result.setup_type !== "No Trade";
  const chart = chartUrl
    ? `<div class="chart-wrap">
        <img class="chart-preview" src="${chartUrl}?v=${Date.now()}" alt="${result.ticker} annotated chart" data-chart="${chartUrl}" data-title="${result.ticker} chart" />
        <div class="chart-caption">Click chart to open full size</div>
      </div>`
    : `<div class="chart-missing">No chart generated</div>`;

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
        </div>
        ${chart}
      </div>
    </article>
  `;
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

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

checkHealth();
