const state = {
  data: null,
};

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
  loadDashboard(selectedDate);
});

async function loadDashboard(selectedDate = "") {
  try {
    const params = new URLSearchParams();
    if (selectedDate) params.set("date", selectedDate);
    const response = await fetch(`/agent/data${params.toString() ? `?${params}` : ""}`);
    if (!response.ok) {
      throw new Error(`Agent data failed: ${response.status}`);
    }
    state.data = await response.json();
    syncDateUrl(selectedDate);
    renderDashboard(state.data);
  } catch (error) {
    document.getElementById("runStatus").textContent = "Error";
    document.getElementById("summaryText").textContent = error.message;
  }
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

  renderMetrics(data.summary);
  renderEquity(data.equity_curve, data.summary);
  renderScreenshot(data.latest_run);
  renderPositions(data.open_positions);
  renderPositionCharts(data.open_positions);
  renderActions(data.latest_setups);
  renderTrades(data.recent_trades, data.closed_trades);
  renderCalibration(data.score_calibration || []);
  renderSummary(data.latest_run);

  if (window.lucide) {
    window.lucide.createIcons();
  }
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

function renderEquity(curve, summary) {
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

  const pnlBadge = document.getElementById("pnlBadge");
  pnlBadge.textContent = formatPct(summary.total_pnl_pct);
  pnlBadge.className = `badge ${summary.total_pnl_ils >= 0 ? "good" : "bad"}`;
  const runCount = Math.max(0, points.length - 1);
  document.getElementById("equityMeta").textContent = `${runCount} tracked ${runCount === 1 ? "run" : "runs"}`;
}

function renderScreenshot(run) {
  const frame = document.getElementById("screenshotFrame");
  const link = document.getElementById("screenshotLink");
  if (!run.screenshot_url) {
    frame.innerHTML = '<span class="empty-media">No screenshot</span>';
    link.removeAttribute("href");
    return;
  }
  frame.innerHTML = `<img src="${escapeHtml(run.screenshot_url)}" alt="Latest agent screenshot" />`;
  link.href = run.screenshot_url;
  document.getElementById("screenshotMeta").textContent = formatDate(run.timestamp);
}

function renderPositions(positions) {
  document.getElementById("positionMeta").textContent = `${positions.length} open positions`;
  const body = document.getElementById("positionsBody");
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
          <td>${usd.format(position.stop_loss)}</td>
          <td>${usd.format(position.target_1)} / ${usd.format(position.target_2)}</td>
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
        ? `<a class="position-chart-media" href="${escapeHtml(position.chart_url)}" target="_blank" rel="noreferrer">
            <img src="${escapeHtml(position.chart_url)}?v=${Date.now()}" alt="${escapeHtml(position.ticker)} chart" />
          </a>`
        : `<div class="position-chart-media missing"><span>No chart saved</span></div>`;
      return `
        <article class="position-chart-card">
          ${chart}
          <div class="position-chart-copy">
            <div class="ticker-cell">
              <strong>${escapeHtml(tickerLabel(position))}</strong>
              <span class="meta">${escapeHtml(tickerMeta(position, formatDate(position.entry_date)))}</span>
            </div>
            <p>${escapeHtml(selectionText(position))}</p>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderActions(setups) {
  document.getElementById("actionMeta").textContent = `${setups.length} latest setup decisions`;
  const list = document.getElementById("actionsList");
  if (!setups.length) {
    list.innerHTML = '<div class="empty-state">No setup decisions</div>';
    return;
  }
  list.innerHTML = setups
    .map(
      (setup) => `
        <div class="action-row">
          ${setup.chart_url
            ? `<img class="action-chart" src="${escapeHtml(setup.chart_url)}?v=${Date.now()}" alt="${escapeHtml(setup.ticker)} chart" />`
            : `<div class="action-chart missing"></div>`}
          <div class="ticker-cell">
            <strong>${escapeHtml(tickerLabel(setup))}</strong>
            <span class="meta">${escapeHtml(decisionMeta(setup) || setup.sector || "Unknown")}</span>
          </div>
          <span class="${actionBadgeClass(setup.action)}">${escapeHtml(setup.action || "UNKNOWN")}</span>
          <div>
            <strong>${escapeHtml(setup.setup_type || "")}</strong>
            <p>${escapeHtml(selectionText(setup))}</p>
            ${riskCheckPills(setup)}
          </div>
        </div>
      `,
    )
    .join("");
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

function renderTrades(trades, closedTrades) {
  document.getElementById("tradeMeta").textContent = `${closedTrades.length} closed / ${trades.length} logged`;
  const list = document.getElementById("tradeList");
  if (!trades.length) {
    list.innerHTML = '<div class="empty-state">No trades logged</div>';
    return;
  }
  list.innerHTML = trades
    .slice()
    .reverse()
    .slice(0, 12)
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
  if (trade.action !== "BUY_SIMULATED") return formatDate(trade.timestamp);
  return `${formatDate(trade.timestamp)} - potential ${money.format(trade.potential_profit_plan_ils || 0)}`;
}

function actionBadgeClass(action) {
  const value = String(action || "").toUpperCase();
  if (["BUY_SIMULATED", "TAKE_PROFIT", "TAKE_PARTIAL_PROFIT"].includes(value)) return "badge good";
  if (["EXIT_STOP"].includes(value)) return "badge bad";
  if (["WATCH", "SKIP"].includes(value)) return "badge warn";
  return "badge neutral";
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
