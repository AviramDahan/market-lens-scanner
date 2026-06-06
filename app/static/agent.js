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
  loadDashboard();
});

async function loadDashboard() {
  try {
    const response = await fetch("/agent/data");
    if (!response.ok) {
      throw new Error(`Agent data failed: ${response.status}`);
    }
    state.data = await response.json();
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

  document.getElementById("actionsLink").href = data.github_actions_url;
  money = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: data.summary.currency || "USD",
    maximumFractionDigits: 0,
  });
  document.getElementById("trackerLink").href = data.tracker_url;
  document.getElementById("lastRun").textContent = formatDate(data.latest_run.timestamp);
  document.getElementById("runStatus").textContent = "OK";
  document.getElementById("tickerCount").textContent = data.latest_run.tickers.length;
  document.getElementById("validSetups").textContent = data.latest_run.valid_setups;

  renderMetrics(data.summary);
  renderEquity(data.equity_curve, data.summary);
  renderScreenshot(data.latest_run);
  renderPositions(data.open_positions);
  renderActions(data.latest_setups);
  renderTrades(data.recent_trades, data.closed_trades);
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
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(260 * dpr));
  context.scale(dpr, dpr);
  context.clearRect(0, 0, rect.width, 260);

  const points = curve.length ? curve : [{ equity_ils: summary.starting_capital_ils, pnl_ils: 0 }];
  const values = points.map((point) => Number(point.equity_ils || 0));
  const min = Math.min(...values, summary.starting_capital_ils);
  const max = Math.max(...values, summary.starting_capital_ils);
  const range = Math.max(1, max - min);
  const pad = { left: 54, right: 20, top: 22, bottom: 36 };
  const width = rect.width - pad.left - pad.right;
  const height = 260 - pad.top - pad.bottom;

  context.strokeStyle = "#dfe5ee";
  context.lineWidth = 1;
  context.beginPath();
  for (let i = 0; i <= 4; i += 1) {
    const y = pad.top + (height / 4) * i;
    context.moveTo(pad.left, y);
    context.lineTo(pad.left + width, y);
  }
  context.stroke();

  context.fillStyle = "#667085";
  context.font = "12px Segoe UI, Arial";
  context.textAlign = "right";
  [min, (min + max) / 2, max].forEach((value) => {
    const y = pad.top + height - ((value - min) / range) * height;
    context.fillText(shortMoney(value), pad.left - 10, y + 4);
  });

  const coords = points.map((point, index) => {
    const x = pad.left + (points.length === 1 ? width : (width / (points.length - 1)) * index);
    const y = pad.top + height - ((Number(point.equity_ils || 0) - min) / range) * height;
    return { x, y };
  });

  context.strokeStyle = summary.total_pnl_ils >= 0 ? "#14795c" : "#bd3d35";
  context.lineWidth = 3;
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
  document.getElementById("equityMeta").textContent = `${points.length - 1} tracked runs`;
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
    body.innerHTML = `<tr><td colspan="11" class="empty-state">No open positions</td></tr>`;
    return;
  }

  body.innerHTML = positions
    .map(
      (position) => `
        <tr>
          <td>
            <div class="ticker-cell">
              <strong>${escapeHtml(position.ticker)}</strong>
              <span class="meta">${escapeHtml(formatDate(position.entry_date))}</span>
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
          <strong>${escapeHtml(setup.ticker)}</strong>
          <span class="${actionBadgeClass(setup.action)}">${escapeHtml(setup.action || "UNKNOWN")}</span>
          <div>
            <strong>${escapeHtml(setup.setup_type || "")}</strong>
            <p>${escapeHtml(setup.feedback || setup.reason || "")}</p>
          </div>
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
          <strong>${escapeHtml(trade.ticker)}</strong>
          <span class="${actionBadgeClass(trade.action)}">${escapeHtml(trade.action)}</span>
          <div>
            <strong>${trade.quantity} shares / ${money.format(cash)}</strong>
            <p>${escapeHtml(formatDate(trade.timestamp))}</p>
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

function shortMoney(value) {
  const number = Number(value || 0);
  if (Math.abs(number) >= 1000) {
    return `$${Math.round(number / 1000)}k`;
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
