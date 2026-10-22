import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../app/static/agent.js', import.meta.url), 'utf8');
const answer = (value) => ({ ok: true, json: async () => value });
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};

test('runtime failures are visible even with an old successful scan and no positions', () => {
  const h = harness();
  h.run('renderRuntimeHealth({runtime_components:{scanner:{status:"FAILED",failure_code:"CHECKPOINT_HEADROOM_INSUFFICIENT"}}},{run_status:"COMPLETE",run_id:"old"})');
  assert.equal(h.elements.get('runStatus').textContent, 'SCANNER FAILED');
  assert.equal(h.elements.get('runStatus').dataset.status, 'FAILED');
  assert.match(h.elements.get('runtimeHealthAlert').textContent, /CHECKPOINT HEADROOM/);
  assert.equal(h.elements.get('runtimeHealthAlert').hidden, false);
  assert.equal(h.elements.get('runStatusMeta').textContent, 'Saved scan: old');
});

test('runtime recovery clears the warning only after actual success', () => {
  const h = harness();
  h.run('renderRuntimeHealth({runtime_components:{scanner:{status:"FAILED"}}},{run_status:"COMPLETE"})');
  h.run('renderRuntimeHealth({runtime_components:{scanner:{status:"RUNNING"}}},{run_status:"COMPLETE"})');
  assert.equal(h.elements.get('runStatus').textContent, 'SCANNER RUNNING');
  h.run('renderRuntimeHealth({runtime_components:{scanner:{status:"SUCCEEDED"}}},{run_status:"COMPLETE"})');
  assert.equal(h.elements.get('runStatus').textContent, 'COMPLETE');
  assert.equal(h.elements.get('runtimeHealthAlert').hidden, true);
});

test('legacy scan UTC timestamps display in the viewer timezone without a three-hour shift', () => {
  const h = harness();
  for (const value of ['2026-10-21T19:06:08', '2026-10-21 19:06:08', '2026-10-21T19:06:08.123']) {
    h.context.value = value;
    h.context.zoned = value.replace(' ', 'T') + 'Z';
    assert.equal(h.run('formatScanDate(value)'), h.run('formatDate(zoned)'));
  }
});

test('scan dates preserve explicit offsets and missing or invalid values', () => {
  const h = harness();
  for (const value of ['2026-10-21T19:06:08Z', '2026-10-21T22:06:08+03:00', '', null, 'unavailable']) {
    h.context.value = value;
    assert.equal(h.run('formatScanDate(value)'), h.run('formatDate(value)'));
  }
});

test('unknown correlation is never rendered as OK or a passed checklist', () => {
  const h = harness();
  for (const status of ['UNKNOWN', 'PARTIAL', 'LEGACY_UNRECORDED']) {
    h.context.item = {decision_json: {correlation_status: status, correlation_warning: false,
      correlation_positions_checked: 0, correlation_positions_expected: 1}};
    assert.match(h.run('riskCheckPills(item)'), /Not verified/);
    assert.doesNotMatch(h.run('riskCheckPills(item)'), /> OK</);
    assert.equal(h.run('buildEntryChecklistItems(item).find(x=>x.label==="Correlation").status'), 'unknown');
  }
});

test('known correlation failures survive incomplete coverage', () => {
  const h = harness();
  h.context.evidence = {correlation_status: 'PARTIAL', correlation_warning: true,
    highest_correlation_ticker: 'NVDA', highest_correlation_value: .92};
  assert.equal(h.run('correlationCheck(evidence).status'), 'warn');
  assert.match(h.run('correlationCheck(evidence).detail'), /NVDA 0.92/);
});

test('no peers is not required, while missing evidence is unknown', () => {
  const h = harness();
  assert.equal(h.run('correlationCheck({}).status'), 'unknown');
  assert.equal(h.run('correlationCheck({correlation_status:"NOT_REQUIRED",correlation_warning:false}).status'), 'pass');
});
function dashboard(version = 'v1', run = 'scan-a') {
  return {
    status: 'ok', dashboard_refresh_version: version,
    latest_run: { run_id: run, latest_scan_run_id: run, run_status: 'COMPLETE' },
    open_positions: [{ ticker: 'TEST', current_price_usd: 100 }], summary: { cash_ils: 9000 },
    system_health: { execution_sensor_status: 'CURRENT', price_sensor_status: 'CURRENT' },
    latest_setups: Array.from({ length: 10 }, (_, i) => ({ ticker: `A${i}` })),
    recent_trades: Array.from({ length: 10 }, (_, i) => ({ ticker: `T${i}`, timestamp: `stamp-${i}`, action: 'EXIT_STOP' })),
    pagination: { actions: { total: 20, has_more: true }, trades: { total: 20, has_more: true } },
  };
}
function harness() {
  const elements = new Map();
  const intervals = new Map();
  const timeouts = new Map();
  const calls = [];
  let sequence = 0;
  let now = Date.parse('2026-10-13T15:00:00Z');
  class FakeDate extends Date { static now() { return now; } }
  const context = vm.createContext({
    Date: FakeDate, Intl, URLSearchParams, AbortController,
    document: { hidden: false, addEventListener() {}, getElementById(id) {
      if (!elements.has(id)) elements.set(id, { textContent: '', innerHTML: '', dataset: {} });
      return elements.get(id);
    } },
    window: {
      setInterval(fn, delay) { const id = ++sequence; intervals.set(id, { fn, delay }); return id; },
      clearInterval(id) { intervals.delete(id); },
      setTimeout(fn, delay) { const id = ++sequence; timeouts.set(id, { fn, at: now + delay }); return id; },
      clearTimeout(id) { timeouts.delete(id); },
    },
    fetch: async () => answer(dashboard()), calls,
  });
  vm.runInContext(source, context);
  for (const name of ['renderMetrics', 'renderRiskDashboard', 'renderPositionsOverview',
    'renderPositionAttention', 'renderPositionTimeline', 'renderPositions', 'renderPositionCharts',
    'detectAndTriggerMonitorEvents', 'updateScheduleIndicators', 'syncDateUrl', 'renderActions', 'renderTrades']) {
    vm.runInContext(`${name} = (...args) => calls.push({name:${JSON.stringify(name)},args});`, context);
  }
  vm.runInContext('renderDashboard = data => { calls.push({name:"renderDashboard",args:[data]}); startLivePrices(state.selectedDate); };', context);
  const run = code => vm.runInContext(code, context);
  context.initial = dashboard();
  run('state.data=initial; resetPagedCollections(initial);');
  return { context, run, calls, elements, intervals, timeouts,
    async advance(ms) {
      now += ms;
      for (const [id, timer] of [...timeouts]) if (timer.at <= now) { timeouts.delete(id); timer.fn(); }
      await Promise.resolve();
    } };
}

test('health changes on a normal quote poll without reloading the dashboard', async () => {
  const h = harness();
  const live = dashboard();
  live.system_health.execution_sensor_status = 'DEGRADED';
  live.system_health.price_sensor_status = 'STALE';
  h.context.fetch = async (_url, options) => { assert.equal(options.cache, 'no-store'); return answer(live); };
  await h.run('refreshLivePrices()');
  assert.match(h.elements.get('positionExecutionHealth').innerHTML, /DEGRADED/);
  assert.match(h.elements.get('positionExecutionHealth').innerHTML, /STALE/);
  assert.equal(h.calls.filter(row => row.name === 'renderDashboard').length, 0);
});

test('empty portfolios keep polling and discover a persisted new position', async () => {
  const h = harness();
  const empty = dashboard();
  empty.open_positions = [];
  h.context.fetch = async () => answer(empty);
  h.run('state.data.open_positions=[]; startLivePrices("");');
  for (let i = 0; i < 20 && h.run('state.liveRefreshBusy'); i++) await Promise.resolve();
  assert.equal(h.run('state.liveRefreshBusy'), false);
  assert.equal(h.intervals.size, 1);
  h.context.fetch = async url => answer(url.startsWith('/agent/data') ? dashboard('v2', 'scan-b') : dashboard('v2'));
  await h.run('refreshLivePrices()');
  assert.equal(h.run('state.data.latest_run.run_id'), 'scan-b');
  assert.equal(h.run('state.data.open_positions.length'), 1);
});

test('background refresh preserves expanded views and unchanged loaded trade pages', async () => {
  const h = harness();
  h.context.more = Array.from({ length: 10 }, (_, i) => ({ ticker: `T${i + 10}`, timestamp: `stamp-${i + 10}`, action: 'EXIT_STOP' }));
  h.run('state.trades.push(...more); state.tradesExpanded=true; state.actionsExpanded=true; state.chartSections.equity=true;');
  h.context.fetch = async () => answer(dashboard('v2'));
  await h.run('refreshLivePrices()');
  assert.equal(h.run('state.trades.length'), 20);
  assert.equal(h.run('state.tradeHasMore'), false);
  assert.equal(h.run('state.tradesExpanded && state.actionsExpanded && state.chartSections.equity'), true);
});

test('a new scan replaces old action pages rather than mixing candidates', async () => {
  const h = harness();
  const next = dashboard('v2', 'scan-b');
  next.latest_setups = [{ ticker: 'NEW' }];
  h.context.fetch = async () => answer(next);
  h.run('state.actionsExpanded=true;');
  await h.run('refreshLivePrices()');
  assert.equal(h.run('state.actions.length'), 1);
  assert.equal(h.run('state.actions[0].ticker'), 'NEW');
  assert.equal(h.run('state.actionsExpanded'), true);
});

test('a delayed live response cannot overwrite a selected historical snapshot', async () => {
  const h = harness();
  const pending = deferred();
  h.context.fetch = async url => url.startsWith('/agent/live') ? pending.promise : answer(dashboard('history', 'historical'));
  const request = h.run('refreshLivePrices()');
  await h.run('loadDashboard("2026-10-12")');
  pending.resolve(answer(dashboard('v2', 'new-live')));
  await request;
  assert.equal(h.run('state.data.latest_run.run_id'), 'historical');
  assert.equal(h.run('state.selectedDate'), '2026-10-12');
  assert.equal(h.intervals.size, 0);
});

test('overlapping polls are suppressed and a timeout allows retry', async () => {
  const h = harness();
  let requests = 0;
  h.context.fetch = (_url, options) => {
    requests++;
    return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('timeout'))));
  };
  const first = h.run('refreshLivePrices()');
  await h.run('refreshLivePrices()');
  assert.equal(requests, 1);
  await h.advance(20000);
  await first;
  assert.equal(h.run('state.liveRefreshBusy'), false);
  assert.match(h.elements.get('positionExecutionHealth').innerHTML, /STATUS SYNC UNAVAILABLE/);
  h.context.fetch = async () => answer(dashboard());
  await h.run('refreshLivePrices()');
  assert.equal(h.run('state.lastLiveSyncError'), false);
  assert.match(h.elements.get('positionExecutionHealth').innerHTML, /CURRENT/);
});

test('failed automatic data reload is visibly unavailable, not a fake scan failure', async () => {
  const h = harness();
  h.context.fetch = async url => url.startsWith('/agent/data') ? { ok: false, status: 503 } : answer(dashboard('v2'));
  await h.run('refreshLivePrices()');
  assert.equal(h.run('state.lastLiveSyncError'), true);
  assert.equal(h.run('state.data.latest_run.run_status'), 'COMPLETE');
  assert.match(h.elements.get('positionExecutionHealth').innerHTML, /STATUS SYNC UNAVAILABLE/);
});

test('old pagination cannot append records into a newer historical view', async () => {
  const h = harness();
  const pending = deferred();
  h.context.fetch = async url => url.includes('section=trades') ? pending.promise : answer(dashboard('history', 'historical'));
  const page = h.run('loadTradesPage()');
  await h.run('loadDashboard("2026-10-12")');
  pending.resolve(answer({ status: 'ok', items: [{ ticker: 'OBSOLETE' }], total: 21 }));
  await page;
  assert.equal(h.run('state.trades.some(row => row.ticker === "OBSOLETE")'), false);
});
