// ETag-cached fetch wrapper + backend-down detection. All API calls funnel
// through here: 304 responses return null (callers must handle), consecutive
// failures trip `_backendDown` to short-circuit further calls.
import { state, _etags } from './state.js';
import { logError, logDebug, logWarn, logInfo, logTag } from './utils.js';
import { setBackendDown } from './conn.js';

export function recoverBackend() {
  if (!state._backendDown) return;
  state._apiFailStreak = 0;
  state._suppressDeployReload = true;
  setBackendDown(false);
}

// Liveness, not readiness: /health answers 503 whenever tests are disabled or no model is
// healthy, which says nothing about whether the server is reachable (finding F5).
export function fetchLive() {
  return fetch(state.conn.liveness_path, { cache: 'no-store' });
}

export function probeBackend() {
  return fetchLive().then(r => {
    if (!r.ok) { logDebug(logTag('API', '←', 'Probe', `HTTP ${r.status}`)); return false; }
    recoverBackend();
    return true;
  }).catch(e => { logDebug(logTag('API', '←', 'Probe', 'Unreachable', e?.message)); return false; });
}

function _onApiResult(ok) {
  if (ok) {
    if (state._apiFailStreak > 0) logInfo(logTag('API', '←', 'Recovered', `${state._apiFailStreak} failed`));
    state._apiFailStreak = 0;
    recoverBackend();
  } else {
    state._apiFailStreak++;
    const threshold = state.conn.unreachable.after_failures;
    if (!state._backendDown && state._apiFailStreak >= threshold) {
      logWarn(logTag('API', '←', 'Down', `${threshold}+ consecutive failures`));
      setBackendDown(true);
    }
  }
}

export function trackFail() {
  _onApiResult(false);
}

export async function api(path, opts = {}) {
  if (state._backendDown) return null;
  logDebug(logTag('API', '→', 'Fetch', path));
  const headers = opts.headers || {};
  if (_etags[path]) headers['If-None-Match'] = _etags[path];
  try {
    const res = await fetch(path, { ...opts, headers });
    const etag = res.headers.get('ETag');
    if (etag) _etags[path] = etag;
    if (res.status === 304) { _onApiResult(true); return null; }
    if (!res.ok) {
      if (!state._backendDown) logError(logTag('API', '←', 'Error', String(res.status), path), new Error(`HTTP ${res.status}`));
      _onApiResult(false);
      return null;
    }
    _onApiResult(true);
    return await res.json();
  } catch (err) {
    if (!state._backendDown) logError(logTag('API', '←', 'Error', 'Fetch', path), err);
    _onApiResult(false);
    return null;
  }
}


const _inFlightMetrics = new Map();

export function fetchProviderMetrics(providerNames, options) {
  if (state._backendDown || !providerNames.length) return Promise.resolve(null);
  const dp = options?.detailProviders !== undefined ? `&detail_providers=${options.detailProviders.join(',')}` : '';
  const cb = options?.cardBuckets ? '&card_buckets=1' : '';
  const params = `?providers=${providerNames.join(',')}${dp}${cb}`;
  const existing = _inFlightMetrics.get(params);
  if (existing) return existing;
  const url = `/api/metrics${params}`;
  const promise = (async () => {
    try {
      const headers = {};
      const etagKey = '/api/metrics' + params;
      if (_etags[etagKey]) headers['If-None-Match'] = _etags[etagKey];
      const res = await fetch(url, { headers });
      const etag = res.headers.get('ETag');
      if (etag) _etags[etagKey] = etag;
      if (res.status === 304) { _onApiResult(true); return null; }
      if (!res.ok) { _onApiResult(false); return null; }
      _onApiResult(true);
      return await res.json();
    } catch (err) {
      logError(logTag('API', '\u2190', 'Error', 'ProviderMetrics', providerNames.join(',')), err);
      _onApiResult(false);
      return null;
    } finally {
      _inFlightMetrics.delete(params);
    }
  })();
  _inFlightMetrics.set(params, promise);
  return promise;
}

export async function fetchProviders(providerNames) {
  if (state._backendDown) return null;
  const params = new URLSearchParams();
  if (providerNames?.length) params.set('providers', providerNames.join(','));
  const qs = params.toString();
  try {
    const res = await fetch(`/api/providers${qs ? '?' + qs : ''}`);
    if (!res.ok) { _onApiResult(false); return null; }
    _onApiResult(true);
    return await res.json();
  } catch (err) {
    logError(logTag('API', '\u2190', 'Error', 'Providers', providerNames?.join(',')), err);
    _onApiResult(false);
    return null;
  }
}


export async function fetchModelInfoCapabilities() {
  if (state._backendDown) return null;
  const path = '/api/model-info';
  try {
    const headers = {};
    if (_etags[path]) headers['If-None-Match'] = _etags[path];
    const res = await fetch(path, { headers });
    const etag = res.headers.get('ETag');
    if (etag) _etags[path] = etag;
    if (res.status === 304) { _onApiResult(true); return null; }
    if (!res.ok) { _onApiResult(false); return null; }
    _onApiResult(true);
    return await res.json();
  } catch (err) {
    logError(logTag('API', '\u2190', 'Error', 'ModelInfo', 'capabilities'), err);
    _onApiResult(false);
    return null;
  }
}


export async function fetchModelInfoDetail(modelKey, includeHistory = false) {
  if (state._backendDown) return null;
  const params = new URLSearchParams({model: modelKey});
  if (includeHistory) params.set('history', '1');
  try {
    const res = await fetch(`/api/model-info?${params}`);
    if (!res.ok) { _onApiResult(false); return null; }
    _onApiResult(true);
    return await res.json();
  } catch (err) {
    logError(logTag('API', '\u2190', 'Error', 'ModelInfo', modelKey), err);
    _onApiResult(false);
    return null;
  }
}

export async function fetchChartData(modelKey, since, buckets, type = 'card', testType = 'benchmark', view = 'speed', until = null) {
  if (state._backendDown) return null;
  const params = new URLSearchParams({model: modelKey, type, buckets: String(buckets), test_type: testType, view});
  if (since != null) params.set('since', String(since));
  if (until != null) params.set('until', String(until));
  const url = `/api/metrics?${params}`;
  logDebug(logTag('API', '→', 'ChartData', `${modelKey} ${testType} ${type} ${view}`));
  try {
    const res = await fetch(url);
    if (!res.ok) { _onApiResult(false); return null; }
    _onApiResult(true);
    return await res.json();
  } catch (err) {
    logError(logTag('API', '\u2190', 'Error', 'ChartData', modelKey), err);
    _onApiResult(false);
    return null;
  }
}

export const HISTORY_PAGE_SIZE = 50;

export async function fetchHistory(modelKey, before = null, limit = HISTORY_PAGE_SIZE, testType = 'benchmark', since = null, until = null, sort = null) {
  if (state._backendDown) return null;
  const params = new URLSearchParams({model: modelKey, type: 'history', limit: String(limit), test_type: testType});
  if (before != null) params.set('before', String(before));
  if (since != null) params.set('since', String(since));
  if (until != null) params.set('until', String(until));
  if (sort != null) params.set('sort', sort);
  const url = `/api/metrics?${params}`;
  logDebug(logTag('API', '→', 'History', `${modelKey} ${testType} before=${before} since=${since} until=${until} sort=${sort} limit=${limit}`));
  try {
    const res = await fetch(url);
    if (!res.ok) { _onApiResult(false); return null; }
    _onApiResult(true);
    return await res.json();
  } catch (err) {
    logError(logTag('API', '\u2190', 'Error', 'History', modelKey), err);
    _onApiResult(false);
    return null;
  }
}

