const form = document.querySelector("#scanForm");
const authStatus = document.querySelector("#authStatus");
const authEmailInput = document.querySelector("#authEmail");
const authPasswordInput = document.querySelector("#authPassword");
const signInButton = document.querySelector("#signInButton");
const signUpButton = document.querySelector("#signUpButton");
const signOutButton = document.querySelector("#signOutButton");
const universeInput = document.querySelector("#universe");
const universeMeta = document.querySelector("#universeMeta");
const tickerPicker = document.querySelector("#tickerPicker");
const addTickersButton = document.querySelector("#addTickersButton");
const selectAllTickersButton = document.querySelector("#selectAllTickersButton");
const clearTickersButton = document.querySelector("#clearTickersButton");
const manualTickerInput = document.querySelector("#manualTicker");
const addManualTickerButton = document.querySelector("#addManualTickerButton");
const clearBasketButton = document.querySelector("#clearBasketButton");
const tickerBasket = document.querySelector("#tickerBasket");
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
let watchlists = [];
let supabaseClient = null;
let authSession = null;
let authUser = null;
let authConfigured = false;
const sessionId = getSessionId();

signInButton.addEventListener("click", signIn);
signUpButton.addEventListener("click", signUp);
signOutButton.addEventListener("click", signOut);

universeInput.addEventListener("change", () => {
  const selected = watchlists.find((watchlist) => watchlist.id === universeInput.value);
  if (!selected) {
    resetTickerPicker();
    universeMeta.textContent = "Choose a universe, select companies, then add them.";
    return;
  }
  renderTickerPicker(selected);
  universeMeta.textContent = `${selected.count} liquid names - ${selected.description}`;
});

addTickersButton.addEventListener("click", () => {
  const selectedTickers = getCheckedTickerInputs().map((input) => input.value);
  addTickersToBasket(selectedTickers);
  universeMeta.textContent = selectedTickers.length
    ? `Added ${selectedTickers.length} ticker${selectedTickers.length === 1 ? "" : "s"}.`
    : "Select one or more companies to add.";
});

addManualTickerButton.addEventListener("click", addManualTickers);

manualTickerInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    addManualTickers();
  }
});

clearBasketButton.addEventListener("click", () => {
  setBasketTickers([]);
  universeMeta.textContent = "Ticker list cleared.";
});

tickerBasket.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-ticker]");
  if (!button) return;
  const ticker = button.dataset.removeTicker;
  setBasketTickers(parseTickers(tickersInput.value).filter((item) => item !== ticker));
});

selectAllTickersButton.addEventListener("click", () => {
  tickerPicker.querySelectorAll("input[type='checkbox']").forEach((input) => {
    input.checked = true;
  });
  updatePickerCount();
});

clearTickersButton.addEventListener("click", () => {
  tickerPicker.querySelectorAll("input[type='checkbox']").forEach((input) => {
    input.checked = false;
  });
  updatePickerCount();
});

document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", async () => {
    currentView = button.dataset.view;
    document.querySelectorAll("[data-view]").forEach((viewButton) => {
      viewButton.classList.toggle("active", viewButton.dataset.view === currentView);
    });
    filterEl.classList.toggle("hidden", currentView !== "scan");
    savedStatusEl.classList.toggle("hidden", currentView !== "my" && currentView !== "global");
    if (isLocked()) {
      renderLockedState();
      return;
    }
    if (currentView === "my" || currentView === "global") {
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
  if (isLocked()) {
    renderLockedState();
    return;
  }
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
      headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      body: JSON.stringify({
        tickers,
        min_rr: minRr,
        analysis_period: analysisPeriod,
        user_label: currentUserLabel(),
        session_id: sessionId,
      }),
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

async function initAuth() {
  try {
    const response = await fetch("/auth/config");
    const config = await response.json();
    authConfigured = Boolean(config.enabled && window.supabase);
    if (!authConfigured) {
      updateAuthUi("Auth not configured");
      return;
    }

    supabaseClient = window.supabase.createClient(config.supabase_url, config.publishable_key);
    const sessionPayload = await supabaseClient.auth.getSession();
    authSession = sessionPayload.data.session;
    authUser = authSession?.user || null;
    updateAuthUi();

    supabaseClient.auth.onAuthStateChange((_event, session) => {
      authSession = session;
      authUser = session?.user || null;
      updateAuthUi();
      if (currentView === "my") {
        loadSavedSetups();
      }
    });
  } catch {
    updateAuthUi("Auth unavailable");
  }
}

async function signIn() {
  if (!supabaseClient) {
    showMessage("Account login is not configured yet.");
    return;
  }
  const email = authEmailInput.value.trim();
  const password = authPasswordInput.value;
  if (!email || !password) {
    showMessage("Enter email and password.");
    return;
  }
  setAuthBusy(true);
  try {
    const { error } = await supabaseClient.auth.signInWithPassword({ email, password });
    if (error) throw error;
    showMessage("");
  } catch (error) {
    showMessage(error.message || "Sign in failed.");
  } finally {
    setAuthBusy(false);
  }
}

async function signUp() {
  if (!supabaseClient) {
    showMessage("Account signup is not configured yet.");
    return;
  }
  const email = authEmailInput.value.trim();
  const password = authPasswordInput.value;
  if (!email || password.length < 6) {
    showMessage("Use an email and a password with at least 6 characters.");
    return;
  }
  setAuthBusy(true);
  try {
    const { error } = await supabaseClient.auth.signUp({ email, password });
    if (error) throw error;
    showMessage("Account created. Check your email if confirmation is required.");
  } catch (error) {
    showMessage(error.message || "Sign up failed.");
  } finally {
    setAuthBusy(false);
  }
}

async function signOut() {
  if (!supabaseClient) return;
  setAuthBusy(true);
  try {
    await supabaseClient.auth.signOut();
    showMessage("");
  } finally {
    setAuthBusy(false);
  }
}

function updateAuthUi(fallbackText = "") {
  const signedIn = Boolean(authUser);
  authStatus.textContent = signedIn ? authUser.email : (fallbackText || "Sign in required");
  authStatus.classList.toggle("signed-in", signedIn);
  authEmailInput.classList.toggle("hidden", signedIn);
  authPasswordInput.classList.toggle("hidden", signedIn);
  signInButton.classList.toggle("hidden", signedIn);
  signUpButton.classList.toggle("hidden", signedIn);
  signOutButton.classList.toggle("hidden", !signedIn);
  applyProductGate();
}

function setAuthBusy(isBusy) {
  signInButton.disabled = isBusy;
  signUpButton.disabled = isBusy;
  signOutButton.disabled = isBusy;
}

function getAuthHeaders() {
  return authSession?.access_token ? { Authorization: `Bearer ${authSession.access_token}` } : {};
}

function currentUserLabel() {
  return authUser?.email || "";
}

function isLocked() {
  return authConfigured && !authUser;
}

function applyProductGate() {
  const locked = isLocked();
  form.classList.toggle("locked", locked);
  scanButton.disabled = locked;
  scanButton.textContent = locked ? "Sign in to scan" : "Scan";
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.disabled = locked && button.dataset.view !== "scan";
  });
  if (locked) {
    renderLockedState();
  } else if (!lastPayload.results.length && currentView === "scan") {
    runMeta.textContent = "Ready";
    resultsEl.className = "results empty";
    resultsEl.innerHTML = `<div class="empty-state">Run a scan to see ranked setups and annotated charts.</div>`;
  }
}

function renderLockedState() {
  scanCount.textContent = "0";
  setupCount.textContent = "0";
  errorCount.textContent = "0";
  runMeta.textContent = "Sign in required";
  showMessage("Create an account or sign in to use Market Lens.");
  resultsEl.className = "results empty";
  resultsEl.innerHTML = `<div class="empty-state">Sign in to scan tickers, save setups, and view shared setups.</div>`;
}

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

async function loadWatchlists() {
  try {
    const response = await fetch("/watchlists");
    if (!response.ok) throw new Error("Watchlists unavailable");
    const payload = await response.json();
    watchlists = payload.watchlists || [];
    universeInput.innerHTML = [
      `<option value="custom">Custom tickers</option>`,
      ...watchlists.map((watchlist) => {
        return `<option value="${escapeHtml(watchlist.id)}">${escapeHtml(watchlist.name)} (${watchlist.count})</option>`;
      }),
    ].join("");
  } catch {
    universeMeta.textContent = "Curated lists are unavailable right now.";
  }
}

function renderTickerPicker(watchlist) {
  tickerPicker.classList.remove("disabled");
  addTickersButton.disabled = false;
  selectAllTickersButton.disabled = false;
  clearTickersButton.disabled = false;
  const companies = watchlist.companies || watchlist.tickers.map((ticker) => ({ ticker, name: ticker }));
  tickerPicker.innerHTML = companies.map((company) => {
    return `
      <label class="ticker-choice">
        <input type="checkbox" value="${escapeHtml(company.ticker)}" />
        <span>${escapeHtml(company.ticker)} (${escapeHtml(company.name)})</span>
      </label>
    `;
  }).join("");
  tickerPicker.querySelectorAll("input[type='checkbox']").forEach((input) => {
    input.addEventListener("change", updatePickerCount);
  });
  updatePickerCount();
}

function resetTickerPicker() {
  tickerPicker.classList.add("disabled");
  addTickersButton.disabled = true;
  selectAllTickersButton.disabled = true;
  clearTickersButton.disabled = true;
  tickerPicker.innerHTML = `<div class="picker-empty">Select a market universe first</div>`;
}

function getCheckedTickerInputs() {
  return Array.from(tickerPicker.querySelectorAll("input[type='checkbox']:checked"));
}

function updatePickerCount() {
  const count = getCheckedTickerInputs().length;
  addTickersButton.textContent = count ? `Add selected (${count})` : "Add selected";
}

async function loadSavedSetups() {
  setLoadingSaved(true);
  showMessage("");
  if (isLocked()) {
    renderLockedState();
    setLoadingSaved(false);
    return;
  }
  const status = savedStatusEl.value;
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (currentView === "my") {
    params.set("source", "manual");
    if (!supabaseClient) {
      params.set("session_id", sessionId);
    }
  }
  if (currentView === "global") {
    params.set("source", "auto");
  }
  const query = params.toString() ? `?${params.toString()}` : "";
  try {
    const response = await fetch(`/setups${query}`, { headers: getAuthHeaders() });
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

function addManualTickers() {
  const manualTickers = parseTickers(manualTickerInput.value);
  addTickersToBasket(manualTickers);
  manualTickerInput.value = "";
  universeMeta.textContent = manualTickers.length
    ? `Added ${manualTickers.length} manual ticker${manualTickers.length === 1 ? "" : "s"}.`
    : "Type one or more tickers to add.";
}

function addTickersToBasket(tickers) {
  const currentTickers = parseTickers(tickersInput.value);
  const merged = [...new Set([...currentTickers, ...tickers])];
  setBasketTickers(merged);
}

function setBasketTickers(tickers) {
  tickersInput.value = tickers.join(" ");
  renderTickerBasket(tickers);
}

function renderTickerBasket(tickers) {
  if (!tickers.length) {
    tickerBasket.className = "ticker-basket empty";
    tickerBasket.innerHTML = `<span class="basket-empty">No tickers selected</span>`;
    return;
  }
  tickerBasket.className = "ticker-basket";
  tickerBasket.innerHTML = tickers.map((ticker) => {
    return `
      <span class="ticker-chip">
        ${escapeHtml(ticker)}
        <button type="button" aria-label="Remove ${escapeHtml(ticker)}" data-remove-ticker="${escapeHtml(ticker)}">x</button>
      </span>
    `;
  }).join("");
}

function setLoading(isLoading) {
  scanButton.disabled = isLoading || isLocked();
  scanButton.textContent = isLocked() ? "Sign in to scan" : (isLoading ? "Scanning..." : "Scan");
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
  savedStatusEl.classList.toggle("hidden", currentView !== "my" && currentView !== "global");
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
    showMessage(`${saved.length} setup${saved.length === 1 ? "" : "s"} added to Global Setups.`);
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
  const label = currentView === "my" ? "my setups" : "global setups";
  runMeta.textContent = `${setups.length} ${label} - ${openCount} open - ${targetCount} target hit`;

  if (!setups.length) {
    resultsEl.className = "results empty";
    resultsEl.innerHTML = `<div class="empty-state">${currentView === "my" ? "No personal setups saved yet." : "No global setups found yet."}</div>`;
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
            <span>${escapeHtml(setup.source === "manual" ? "saved" : "found")}</span>
            ${setup.user_label ? `<span>${escapeHtml(setup.user_label)}</span>` : ""}
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
      if (supabaseClient && !authUser) {
        renderLockedState();
        return;
      }
      const ticker = button.dataset.save;
      const result = results.find((item) => item.ticker === ticker);
      if (!result) return;
      button.disabled = true;
      button.textContent = "Saving...";
      try {
        const response = await fetch("/setups", {
          method: "POST",
          headers: { "Content-Type": "application/json", ...getAuthHeaders() },
          body: JSON.stringify({
            result,
            analysis_period: analysisPeriod,
            chart_url: charts[result.ticker],
            user_label: currentUserLabel(),
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
        const response = await fetch(`/setups/${button.dataset.refresh}/refresh`, {
          method: "POST",
          headers: getAuthHeaders(),
        });
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

initAuth();
loadWatchlists();
checkHealth();
