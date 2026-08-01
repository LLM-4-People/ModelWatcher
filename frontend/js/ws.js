// WebSocket connection + message routing. Auto-reconnect with exponential
// backoff (3s→60s); backend-down backoff clamped to min 15s. Staleness detection
// force-reconnects if no message in 5 min. Routes result/audit/probe/notification
// messages to the appropriate state mutations and UI scheduling.
import { state, setMetrics, adjustCount, applyConfig } from './state.js';
import { isOK, logError, logInfo, logWarn, logDebug, cap, logTag, stripEphemeral } from './utils.js';
import { api, fetchProviderMetrics, recoverBackend, probeBackend, trackFail } from './api.js';
import { refreshModelList, renderSchedule } from './dom.js';
import { renderHelpLegends } from './help.js';
import { handleNotification, refreshNotifHistory, syncNotifSettingsUI } from './notifications.js';
import { syncWSPrefs } from './prefs.js';
import { scheduleUI } from './frame.js';
import { clearModelChartCache, getCardView } from './chart.js';
import { cacheSet } from './cache.js';

const _STALE_MS = 5 * 60 * 1000;
const _STALE_CHECK_MS = 60 * 1000;
const _CARD_BUCKET_DEBOUNCE_MS = 5000;
const _CARD_BUCKET_MIN_MS = 30000;
let _cardBucketDebounce = null;
let _lastCardBucketRefresh = 0;

function _tsEpoch(record) {
  return record.timestamp ? new Date(record.timestamp).getTime() / 1000 : Date.now() / 1000;
}

export function refreshCardBuckets(force) {
  if (!force && Date.now() - _lastCardBucketRefresh < _CARD_BUCKET_MIN_MS) return;
  _lastCardBucketRefresh = Date.now();
  fetchProviderMetrics(state.providerOrder, { cardBuckets: true }).then(metrics => {
    if (!metrics) return;
    const updated = [];
    for (const [key, data] of Object.entries(metrics)) {
      if (data.card_buckets && state.metrics[key]) {
        state.metrics[key].card_buckets = data.card_buckets;
        clearModelChartCache(key);
        updated.push(key);
      }
    }
    if (updated.length) scheduleUI({ models: updated });
  }).catch(e => logError(logTag('API', '←', 'Error', 'CardBucketRefresh'), e));
}

function scheduleCardBucketRefresh() {
  if (_cardBucketDebounce) return;
  _cardBucketDebounce = setTimeout(() => { _cardBucketDebounce = null; refreshCardBuckets(); }, _CARD_BUCKET_DEBOUNCE_MS);
}

export function updateWSStatus(s) {
  const el = document.getElementById('ws-status');
  if (!el) return;
  const c = { connected: 'bg-success-400', disconnected: 'bg-danger-400', error: 'bg-danger-400', connecting: 'ws-dot-connecting', restarting: 'bg-warn-400 ws-dot-pulse', down: 'bg-danger-400' };
  el.className = `ml-2 inline-block w-2.5 h-2.5 rounded-full ${c[s] || c.connecting}`;
  el.setAttribute('data-tip', 'ws_' + s);
  el.setAttribute('aria-label', s);
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
  for (const f of ['supports_vision', 'supports_tools', 'supports_structured_output', 'supports_cache']) {
    if (pr[f] != null) caps[f] = pr[f];
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
      clearModelChartCache(msg.model, getCardView());
      scheduleUI({ models: [msg.model], summary: true, providers: true, modal });
      if (!isHealth) scheduleCardBucketRefresh();
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
      handleWS({ type: 'result', model: mk, record: rmsg.record, uptime_pct: rmsg.uptime_pct,
        test_type: rmsg.test_type, status: rmsg.status, degraded_source: rmsg.degraded_source ?? null, scores: rmsg.scores, trends: rmsg.trends });
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
    api('/api/config').then(cfg => {
      if (!cfg) return;
      cacheSet('config', cfg, 3600);
      applyConfig(cfg);
      if (cfg.color_thresholds) { renderHelpLegends(); syncNotifSettingsUI(); }
      renderSchedule();
    }).catch(e => logError(logTag('API', '←', 'Error', 'Config'), e));
    refreshNotifHistory();
    refreshModelList().then(providers => {
      if (providers) cacheSet('providers_full', providers, 3600);
      fetchProviderMetrics(state.providerOrder, { detailProviders: [...state.fetchedProviders], cardBuckets: true }).then(metrics => {
        if (!metrics) return;
        cacheSet('metrics_initial', stripEphemeral(metrics), 300);
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
    updateWSStatus('restarting');
  }
}

let _staleCheckId = null;

function _checkStale() {
  if (!state._wsConnected) return;
  const stale = Date.now() - state._lastWsMsg;
  if (stale > _STALE_MS) {
    logWarn(logTag('WS', '←', 'Stale', `${Math.round(stale / 1000)}s`));
    if (state.ws) state.ws.close(4000, 'stale');
  }
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') return;
  _wsBackoff = 3000;
  if (state._wsConnected) {
    fetch('/health').catch(() => {
      logWarn(logTag('WS', '→', 'Reconnect', 'Foreground'));
      if (state.ws) state.ws.close(4000, 'stale');
    });
  } else if (state._backendDown) {
    probeBackend().then(up => { if (up) connectWS(); });
  } else if (_wsConnectTimer) {
    clearTimeout(_wsConnectTimer);
    _wsConnectTimer = null;
    connectWS();
  }
});

let _wsFirstOpen = true;
let _wsBackoff = 3000;
let _wsConnectTimer = null;

export function connectWS() {
  if (_wsConnectTimer) { clearTimeout(_wsConnectTimer); _wsConnectTimer = null; }
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    _wsBackoff = 3000; recoverBackend(); state._wsConnected = true; state._lastWsMsg = Date.now();
    updateWSStatus('connected'); logInfo(logTag('WS', '\u2190', 'Open')); syncWSPrefs();
    if (_wsFirstOpen) { _wsFirstOpen = false; return; }
    refreshNotifHistory();
    if (!state.fetchedProviders.size) return;
    fetchProviderMetrics([...state.fetchedProviders], { cardBuckets: true }).then(m => { if (!m) return; cacheSet('metrics_initial', stripEphemeral(m), 300); setMetrics(m); for (const k of Object.keys(m)) clearModelChartCache(k); const now = Date.now(); for (const p of state.fetchedProviders) state._providerDataAt[p] = now; scheduleUI({ models: Object.keys(m), summary: true, providers: true }); }).catch(e => logError(logTag('API', '\u2190', 'Error', 'ReconnectMetrics'), e));
  };
  ws.onclose = (event) => {
    state._wsConnected = false;
    if (state.ws !== ws) return;
    const isRestart = state._wsRestarting || event.code === 1012;
    state._wsRestarting = false;
    if (!isRestart) trackFail();
    const isDown = state._backendDown;
    if (isDown) { _wsBackoff = Math.max(_wsBackoff, 15000); }
    const status = isDown ? 'down' : (isRestart ? 'restarting' : 'disconnected');
    updateWSStatus(status);
    logInfo(logTag('WS', '←', 'Close', String(event.code), isRestart ? 'Restarting' : isDown ? 'Down' : 'Reconnect'));
    const delay = isRestart ? 1000 : _wsBackoff;
    _wsBackoff = Math.min(_wsBackoff * 2, 60000);
    _wsConnectTimer = setTimeout(connectWS, delay);
  };
  ws.onerror = () => { logWarn(logTag('WS', '←', 'Error')); updateWSStatus('error'); };
  ws.onmessage = (e) => {
    state._lastWsMsg = Date.now();
    try { const msg = JSON.parse(e.data); logDebug(_wsLogTag(msg)); handleWS(msg); } catch (err) { logError(logTag('WS', '←', 'Error', 'Parse'), err); }
  };
  if (_staleCheckId) clearInterval(_staleCheckId);
  _staleCheckId = setInterval(_checkStale, _STALE_CHECK_MS);
  state.ws = ws;
}
