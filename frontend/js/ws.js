// WebSocket connection + message routing. A socket counts as connected only once the
// server's hello arrives (an accept-then-close must not look like a success, finding F5);
// reconnects back off per conn.js, with pacing and close codes from the server (state.conn).
// Staleness: no frame (the server heartbeats) within stale_after closes and reconnects.
// Routes result/audit/probe/notification messages to state mutations and UI scheduling.
import { state, setMetrics, adjustCount, applyConfig } from './state.js';
import { isOK, logError, logInfo, logWarn, logDebug, cap, logTag, stripEphemeral } from './utils.js';
import { api, fetchProviderMetrics, fetchLive, recoverBackend, probeBackend, trackFail } from './api.js';
import { setWSStatus, wsCloseKind, wsReconnectPlan } from './conn.js';
import { refreshModelList, renderSchedule } from './dom.js';
import { renderHelpLegends } from './help.js';
import { handleNotification, refreshNotifHistory, syncNotifSettingsUI } from './notifications.js';
import { syncWSPrefs } from './prefs.js';
import { scheduleUI } from './frame.js';
import { clearModelChartCache, getCardView } from './chart.js';
import { cacheSet } from './cache.js';
import { renderFilterOptions } from './filter.js';

function _tsEpoch(record) {
  return record.timestamp ? new Date(record.timestamp).getTime() / 1000 : Date.now() / 1000;
}

// Applies a /api/config answer and redraws what depends on it (start, reload, reconnect)
export function applyConfigAndRender(cfg) {
  applyConfig(cfg);
  if (cfg.color_thresholds) { renderHelpLegends(); syncNotifSettingsUI(); }
  renderFilterOptions();
  renderSchedule();
}

function _refetchConfig() {
  api('/api/config').then(cfg => {
    if (!cfg) return;
    cacheSet('config', cfg, state.ui.cache_ttl.config);
    applyConfigAndRender(cfg);
  }).catch(e => logError(logTag('API', '←', 'Error', 'Config'), e));
}

function _wsLogTag(msg) {
  const type = cap(msg.type);
  const tt = msg.test_type || msg.record?.test_type;
  const subtype = tt ? cap(tt) : null;
  const model = msg.model || null;
  let detail = null;
  if (msg.type === 'testing') detail = msg.testing ? 'Start' : 'End';
  else if (msg.type === 'result' && msg.record) {
    if (msg.record.retry_attempt) detail = `Retry ${msg.record.retry_attempt}/${msg.record.retry_total || '?'}`;
    else if (msg.record.degraded) detail = 'Degraded';
    else if (isOK(msg.record)) detail = 'OK';
    else detail = 'Fail';
  } else if (msg.type === "result_batch" && msg.results) detail = `${Object.keys(msg.results).length} models`;
  else if (msg.type === "notification" && msg.notification) detail = cap(msg.notification.event_type);
  else if (msg.type === 'server_shutdown') detail = cap(msg.reason);
  return logTag('WS', '←', type, subtype, model, detail);
}

function _probeToCapabilities(pr) {
  const caps = {};
  // Capability keys come from /api/config (backend/state.py CAPABILITIES, finding F76); thinking
  // arrives as a mode name or a flag
  for (const { key } of state.capabilities) {
    if (key !== 'thinking' && pr[key] != null) caps[key] = pr[key];
  }
  if (pr.thinking) caps.thinking = typeof pr.thinking === 'string' ? pr.thinking : 'enabled';
  for (const f of ['served_by', 'quantization', 'engine_version', 'served_model', 'fp_server', 'fp_features']) {
    if (pr[f] != null) caps[f] = pr[f];
  }
  if (pr.tensor_parallel != null) caps.tensor_parallel = pr.tensor_parallel;
  return caps;
}


function handleWS(msg) {
  const needsModel = msg.type === 'testing' || msg.type === 'result';
  if (needsModel && !msg.model) return;
  if (msg.model && !state.metrics[msg.model]) state.metrics[msg.model] = {};
  if (msg.type === 'testing') {
    const prev = state.metrics[msg.model].status;
    const prevTesting = !!state.metrics[msg.model].testing;
    const testType = msg.test_type || 'benchmark';
    state.metrics[msg.model].testing = msg.testing;
    state.metrics[msg.model].testing_type = msg.testing ? testType : null;
    const isBenchmarkTest = msg.testing && testType !== 'health';
    adjustCount(msg.model, prev, prev, prevTesting, isBenchmarkTest);
    if (testType !== 'health') scheduleUI({ models: [msg.model], summary: true, providers: true });
  }
  if (msg.type === 'result') {
    const prev = state.metrics[msg.model];
    const prevStatus = prev?.status || 'unknown';
    const prevTesting = !!prev?.testing;
    const isRetry = !!msg.record.retry_attempt;
    const testType = msg.test_type || msg.record?.test_type || 'benchmark';
    const isHealth = testType === 'health';
    const modal = { modelId: msg.model, record: msg.record, testType };

    if (isRetry) {
      if (!isHealth) {
        state.metrics[msg.model].retry_attempt = msg.record.retry_attempt;
        state.metrics[msg.model].retry_total = msg.record.retry_total;
      }
      state.metrics[msg.model].status = msg.status || 'unknown';
      state.metrics[msg.model].degraded_source = msg.degraded_source ?? null;
      state.metrics[msg.model].uptime_pct = msg.uptime_pct;
      adjustCount(msg.model, prevStatus, msg.status || 'unknown', prevTesting, state.metrics[msg.model].testing_type === 'benchmark');
      scheduleUI({ models: [msg.model], summary: true, providers: true, modal });
    } else {
      const nextStatus = msg.status || 'unknown';
      if (nextStatus !== prevStatus) logInfo(logTag('Model', '←', 'Status', msg.model, `${prevStatus} → ${nextStatus}`));
      const nowOk = isOK(msg.record);
      if (isHealth) {
        state.metrics[msg.model].status = nextStatus;
        state.metrics[msg.model].degraded_source = msg.degraded_source ?? null;
        state.metrics[msg.model].uptime_pct = msg.uptime_pct;
        if (state.metrics[msg.model].testing_type !== 'benchmark') {
          state.metrics[msg.model].testing = false;
          state.metrics[msg.model].testing_type = null;
          state.metrics[msg.model].retry_attempt = null;
          state.metrics[msg.model].retry_total = null;
        }
        state.metrics[msg.model].health_ts_epoch = _tsEpoch(msg.record);
        state.metrics[msg.model].health_success = nowOk;
        state.metrics[msg.model].health_error = nowOk ? null : (msg.record.error || null);
        state.metrics[msg.model].health_ttft_ms = msg.record.ttft_ms ?? null;
        state.metrics[msg.model].health_request_id = msg.record.request_id ?? null;
        if (nowOk) state.metrics[msg.model].health_success_epoch = _tsEpoch(msg.record);
        adjustCount(msg.model, prevStatus, nextStatus, prevTesting, state.metrics[msg.model].testing_type === 'benchmark');
      } else {
        Object.assign(state.metrics[msg.model], {
          last_test: msg.record,
          status: nextStatus,
          degraded_source: msg.degraded_source ?? null,
          uptime_pct: msg.uptime_pct,
        });
        if (nowOk && !msg.record.degraded) {
          state.metrics[msg.model].last_success_epoch = _tsEpoch(msg.record);
          state.metrics[msg.model].last_success_test = msg.record;
        }
        state.metrics[msg.model].testing = false;
        state.metrics[msg.model].testing_type = null;
        state.metrics[msg.model].retry_attempt = null;
        state.metrics[msg.model].retry_total = null;
        state.metrics[msg.model].health_ts_epoch = _tsEpoch(msg.record);
        state.metrics[msg.model].health_success = nowOk;
        state.metrics[msg.model].health_error = nowOk ? null : (msg.record.error || null);
        state.metrics[msg.model].health_request_id = msg.record.request_id ?? null;
        state.metrics[msg.model].last_benchmark_epoch = _tsEpoch(msg.record);
        adjustCount(msg.model, prevStatus, nextStatus, prevTesting, false);
      }
      if (msg.scores != null) state.metrics[msg.model].scores = msg.scores;
      if (msg.trends != null) state.metrics[msg.model].trends = msg.trends;
      // A final benchmark brings its model's card buckets (finding F88: every provider's used
      // to be refetched after any result)
      if (msg.card_buckets != null) {
        state.metrics[msg.model].card_buckets = msg.card_buckets;
        clearModelChartCache(msg.model);
      } else {
        clearModelChartCache(msg.model, getCardView());
      }
      scheduleUI({ models: [msg.model], summary: true, providers: true, modal });
    }
  }
  if (msg.type === 'result_batch') {
    // Batched result messages - process each model's result individually
    const results = msg.results;
    if (!results) return;
    const models = Object.keys(results);
    for (const mk of models) {
      const rmsg = results[mk];
      // Reconstruct as individual result message and process
      handleWS({ ...rmsg, type: 'result', model: mk });
    }
    return;
  }
  if (msg.type === 'notification') {
    handleNotification(msg.notification);
  }
  if (msg.type === 'audit_result') {
    if (!msg.model || !state.metrics[msg.model]) return;
    const ar = msg.result;
    if (ar) {
      state.metrics[msg.model].last_audit_result = ar;
      state.metrics[msg.model].last_audit_epoch = ar.ts_epoch;
      state.metrics[msg.model].testing_audit = false;
      scheduleUI({ models: [msg.model], modal: { modelId: msg.model } });
    } else {
      state.metrics[msg.model].testing_audit = false;
      state.metrics[msg.model].last_audit_epoch = Date.now() / 1000;
    }
  }
  if (msg.type === 'probe_result') {
    if (!msg.model || !state.metrics[msg.model]) return;
    const pr = msg.result;
    if (pr) {
      state.metrics[msg.model].last_probe_result = pr;
      state.metrics[msg.model].last_probe_epoch = pr.ts_epoch;
      state.metrics[msg.model].testing_probe = false;
      const caps = _probeToCapabilities(pr);
      if (state._modelCaps) {
        state._modelCaps[msg.model] = caps;
      }
      const entry = state._modelMap[msg.model];
      if (entry) Object.assign(entry, caps);
    } else {
      state.metrics[msg.model].testing_probe = false;
      state.metrics[msg.model].last_probe_epoch = Date.now() / 1000;
    }
    scheduleUI({ models: [msg.model], modal: { modelId: msg.model } });
  }
  if (msg.type === 'config_updated') {
    logInfo(logTag('WS', '←', 'Config', 'Updated'));
    _refetchConfig();
    refreshNotifHistory();
    refreshModelList().then(providers => {
      if (providers) cacheSet('providers_full', providers, state.ui.cache_ttl.providers);
      fetchProviderMetrics(state.providerOrder, { detailProviders: [...state.fetchedProviders], cardBuckets: true }).then(metrics => {
        if (!metrics) return;
        cacheSet('metrics_initial', stripEphemeral(metrics), state.ui.cache_ttl.metrics);
        setMetrics(metrics);
        for (const k of Object.keys(metrics)) clearModelChartCache(k);
        const now = Date.now();
        for (const p of state.providerOrder) state._providerDataAt[p] = now;
        scheduleUI({ models: Object.keys(metrics), summary: true, providers: true });
      }).catch(e => logError(logTag('API', '←', 'Error', 'MetricsRefresh'), e));
    }).catch(e => logError(logTag('API', '←', 'Error', 'ModelList'), e));
  }
  if (msg.type === 'server_shutdown') {
    logWarn(logTag('WS', '←', 'Shutdown', msg.reason || null));
    state._wsRestarting = true;
    setWSStatus('restarting');
  }
}

let _staleTimer = null;
let _helloTimedOut = false;
let _wsFirstHello = true;
let _wsBackoffMs = null;
let _wsConnectTimer = null;

function _resetBackoff() { _wsBackoffMs = state.conn.reconnect.min_delay * 1000; }

// Armed when the socket opens and re-armed by every frame; the server sends its hello at once and
// heartbeats well inside stale_after, so firing means the socket is dead. A socket that opened but
// never got its hello (a proxy that upgrades and forwards nothing) is a failed connection, not a
// quiet one (finding F25: it stayed "connecting" forever).
function _armStale(ws, hello) {
  clearTimeout(_staleTimer);
  _staleTimer = setTimeout(() => {
    _helloTimedOut = !hello();
    logWarn(logTag('WS', '←', _helloTimedOut ? 'NoHello' : 'Stale', `${state.conn.stale_after}s`));
    ws.close(state.conn.close_codes.stale, 'stale');
  }, state.conn.stale_after * 1000);
}

function _onHello(msg) {
  state.conn = msg.config;
  state.scheduler = msg.scheduler;
  renderSchedule();
  _resetBackoff();
  state._wsConnected = true;
  recoverBackend();
  setWSStatus('connected');
  logInfo(logTag('WS', '←', 'Hello'));
  syncWSPrefs();
  if (_wsFirstHello) { _wsFirstHello = false; return; }
  // A reconnect may follow a server restart with another config
  _refetchConfig();
  refreshNotifHistory();
  if (!state.fetchedProviders.size) return;
  fetchProviderMetrics([...state.fetchedProviders], { cardBuckets: true }).then(m => { if (!m) return; cacheSet('metrics_initial', stripEphemeral(m), state.ui.cache_ttl.metrics); setMetrics(m); for (const k of Object.keys(m)) clearModelChartCache(k); const now = Date.now(); for (const p of state.fetchedProviders) state._providerDataAt[p] = now; scheduleUI({ models: Object.keys(m), summary: true, providers: true }); }).catch(e => logError(logTag('API', '\u2190', 'Error', 'ReconnectMetrics'), e));
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible' || !state.conn) return;
  _resetBackoff();
  if (state._wsConnected) {
    fetchLive().then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); }).catch(e => {
      logWarn(logTag('WS', '→', 'Reconnect', 'Foreground', e?.message));
      if (state.ws) state.ws.close(state.conn.close_codes.stale, 'stale');
    });
  } else if (state._backendDown) {
    probeBackend().then(up => { if (up) connectWS(); }).catch(e => logError(logTag('WS', '→', 'Error', 'ForegroundProbe'), e));
  } else if (_wsConnectTimer) {
    connectWS();
  }
});

export function connectWS() {
  if (_wsConnectTimer) { clearTimeout(_wsConnectTimer); _wsConnectTimer = null; }
  // Callers (recovery probe, foreground) may run while a socket is still live; never open a second one
  if (state.ws && state.ws.readyState <= WebSocket.OPEN) return;
  if (_wsBackoffMs == null) _resetBackoff();
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}${state.conn.ws_path}`);
  let hello = false;
  const gotHello = () => hello;
  ws.onopen = () => { logDebug(logTag('WS', '←', 'Open')); _armStale(ws, gotHello); };
  ws.onmessage = (e) => {
    _armStale(ws, gotHello);
    try {
      const msg = JSON.parse(e.data);
      logDebug(_wsLogTag(msg));
      if (msg.type === 'hello') { hello = true; _onHello(msg); } else handleWS(msg);
    } catch (err) { logError(logTag('WS', '←', 'Error', 'Parse'), err); }
  };
  // onclose always follows and decides the status; an error alone says nothing more
  ws.onerror = () => logWarn(logTag('WS', '←', 'Error'));
  ws.onclose = (event) => {
    if (state.ws !== ws) return;
    clearTimeout(_staleTimer);
    state._wsConnected = false;
    const kind = wsCloseKind({ code: event.code, wasClean: event.wasClean, hello, restarting: state._wsRestarting, helloTimedOut: _helloTimedOut }, state.conn.close_codes);
    state._wsRestarting = false;
    _helloTimedOut = false;
    if (kind === 'failed') trackFail();
    const plan = wsReconnectPlan(kind, _wsBackoffMs, state._backendDown, state.conn);
    _wsBackoffMs = plan.nextBackoffMs;
    setWSStatus(plan.status);
    logInfo(logTag('WS', '←', 'Close', String(event.code), plan.status, `retry in ${Math.round(plan.delayMs / 1000)}s`));
    _wsConnectTimer = setTimeout(connectWS, plan.delayMs);
  };
  state.ws = ws;
}
