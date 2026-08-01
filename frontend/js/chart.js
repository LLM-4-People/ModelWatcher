// Chart.js lifecycle: lazy loading, lazy init via IntersectionObserver, cleanup
// of offscreen charts, and card view switching. Chart instances store _buckets,
// _view, _full, _modelId as the source of truth for updates.
import { state, _chartReady, setChartReady } from './state.js';
import { slug, logError, logDebug, logTag } from './utils.js';
import {
  evenTimeTicksPlugin, dayBoundaryPlugin, thresholdPlugin,
  cardZonesPlugin, gradientPlugin, glowPlugin,
} from './chart-plugins.js';
import {
  _buildDatasets, _axisConfig, chartOptions, _dataRange,
  _transformCardBuckets, _transformModalBuckets, _calculateBuckets,
  _hasCardViewData, _readParentSize, _phId, chartPhHTML,
} from './chart-helpers.js';


const _nextFrame = (cb) => requestAnimationFrame(() => requestAnimationFrame(cb));

let _chartObserver = null;
const _pendingCharts = new Map();
const _visibleCharts = new Set();
let _viewGen = 0;
const _TTL_MS = 5000;
const _lastFetch = new Map();
const _INIT_BATCH = 3;
const _INIT_YIELD_EVERY = 5;
const _INIT_YIELD_MS = 16;
let _initQueue = [];
let _initInFlight = 0;
let _initSinceYield = 0;

export function clearModelChartCache(modelId, view) {
  if (!modelId) return;
  if (view) {
    _lastFetch.delete(modelId + ':' + view);
  } else {
    for (const key of _lastFetch.keys()) {
      if (key.startsWith(modelId + ':')) _lastFetch.delete(key);
    }
  }
}

export function _fetchMetaClear() {
  _lastFetch.clear();
}

export const CHART_VIEWS = [
  { key: 'speed', label: 'TPS + TTFT', tip: 'chartSpeed' },
  { key: 'consistency', label: 'P99 ITL (raw) + Batching', tip: 'chartConsistency' },
  { key: 'scores', label: 'C / S / R', tip: 'chartScores' },
  { key: 'health', label: 'Health + TTFT', tip: 'chartHealth' },
];

export function getCardView() {
  return localStorage.getItem('mw_card_view') || 'speed';
}

function setCardView(view) {
  localStorage.setItem('mw_card_view', view);
}

export function _loadChartJS() {
  if (_chartReady) return _chartReady;
  if (typeof Chart !== 'undefined') { const p = Promise.resolve(); setChartReady(p); return p; }
  const prefix = window.__STATIC_PREFIX__;
  const promise = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = `${prefix}/js/vendor/chart.umd.min.js`;
    s.onload = () => {
      if (typeof Chart !== 'undefined' && Chart.LogarithmicScale) {
        try { Chart.register(Chart.LogarithmicScale); } catch (e) { /* already registered */ }
      }
      const a = document.createElement('script');
      a.src = `${prefix}/js/vendor/chartjs-adapter-date-fns.bundle.min.js`;
      a.onload = resolve;
      a.onerror = reject;
      document.head.appendChild(a);
    };
    s.onerror = reject;
    document.head.appendChild(s);
  });
  setChartReady(promise);
  return promise;
}

export function initChart(canvasId, buckets, full, view = 'speed', modelId = '', expanded = false, size = null) {
  logDebug(logTag('Chart', '\u2192', 'Init', modelId || canvasId, full ? 'Full' : 'Card'));
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === 'undefined') return;
  const phId = _phId(canvasId);
  const phEl = document.getElementById(phId);
  if (buckets.length < 2) {
    if (state.charts[canvasId]) { state.charts[canvasId].destroy(); delete state.charts[canvasId]; }
    const ctx = canvas.getContext('2d');
    if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (phEl && phEl !== canvas) phEl.classList.remove('hidden');
    return;
  }
  const { w: pw, h: ph } = size || _readParentSize(canvas);
  if (state.charts[canvasId]) { state.charts[canvasId].destroy(); delete state.charts[canvasId]; }
  canvas.style.display = 'block';
  canvas.style.boxSizing = 'border-box';
  canvas.style.width = pw + 'px';
  canvas.style.height = ph + 'px';
  canvas.width = pw;
  canvas.height = ph;
  const ctx = canvas.getContext('2d');

  const bucketed = buckets.length > 1 && buckets.some(b => b.count > 1);

  const labels = buckets.map(b => b.timestamp);
  const datasets = _buildDatasets(buckets, full, view);

  const axisRanges = _axisConfig(buckets, full, view, expanded);

  const opts = chartOptions(full, view, axisRanges, expanded, pw, ph);
  const range = _dataRange(buckets, view);
  if (range.min != null) opts.scales.x.min = range.min;
  if (range.max != null) opts.scales.x.max = range.max;

  const chart = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: opts,
    plugins: [evenTimeTicksPlugin, dayBoundaryPlugin, cardZonesPlugin, gradientPlugin, glowPlugin, ...(full ? [thresholdPlugin] : [])],
  });
  chart._buckets = buckets;
  chart._bucketed = bucketed;
  chart._view = view;
  chart._full = full;
  chart._modelId = modelId;
  chart.update('none');
  state.charts[canvasId] = chart;
  if (phEl && phEl !== canvas) phEl.classList.add('hidden');
}

export function updateChartView(canvasId, buckets, full, view, modelId = '', expanded = false) {
  const chart = state.charts[canvasId];
  if (!chart) return false;
  if (chart._full !== full) return false;
  if (chart._view === view && chart._buckets === buckets) return true;
  if (!buckets || buckets.length < 2) return false;
  chart._buckets = buckets;
  chart._bucketed = buckets.length > 1 && buckets.some(b => b.count > 1);
  chart._view = view;
  if (modelId) chart._modelId = modelId;
  chart.data.labels = buckets.map(b => b.timestamp);
  chart.data.datasets = _buildDatasets(buckets, full, view);

  const axisRanges = _axisConfig(buckets, full, view, expanded);

  const canvas = chart.canvas;
  const { w: pw, h: ph } = _readParentSize(canvas);
  const opts = chartOptions(full, view, axisRanges, expanded, pw, ph);
  const range = _dataRange(buckets, view);
  if (range.min != null) opts.scales.x.min = range.min;
  if (range.max != null) opts.scales.x.max = range.max;

  chart.options.scales = opts.scales;
  chart.options.plugins = opts.plugins || chart.options.plugins;
  if (opts.scales.y) chart.options.scales.y = opts.scales.y;
  if (opts.scales.y1) chart.options.scales.y1 = opts.scales.y1;
  else delete chart.options.scales.y1;

  chart.update('none');
  return true;
}

export function updateChartForModel(modelId) {
  const entry = state._modelMap[modelId];
  if (!entry) return;
  const canvasId = `chart-${slug(entry.id)}`;
  const chart = state.charts[canvasId];
  if (!chart || chart._full) return;
  const view = getCardView();
  if (chart._view !== view) {
    _scheduleChartInit(canvasId);
    return;
  }
  const cacheKey = modelId + ':' + view;
  const lastFetchTime = _lastFetch.get(cacheKey);
  if (lastFetchTime && Date.now() - lastFetchTime < _TTL_MS) return;

  const m = state.metrics[modelId];
  const embedded = _hasCardViewData(m?.card_buckets, view);
  if (!embedded) return;
  const buckets = _transformCardBuckets(m.card_buckets, view);
  if (!buckets || buckets.length < 2) return;
  _lastFetch.set(cacheKey, Date.now());
  chart._buckets = buckets;
  chart._bucketed = buckets.length > 1 && buckets.some(b => b.count > 1);
  chart.data.labels = buckets.map(b => b.timestamp);
  const newDS = _buildDatasets(buckets, false, view);
  if (chart.data.datasets.length !== newDS.length) {
    initChart(canvasId, buckets, false, view, chart._modelId);
    return;
  }
  for (let i = 0; i < newDS.length; i++) {
    const target = chart.data.datasets[i];
    const src = newDS[i];
    target.data = src.data;
    target.borderColor = src.borderColor;
    target.backgroundColor = src.backgroundColor;
    target._gradientColor = src._gradientColor;
    target._glow = src._glow;
    target.pointRadius = src.pointRadius;
    target.pointBackgroundColor = src.pointBackgroundColor;
    target.fill = src.fill;
  }
  const range = _dataRange(buckets, view);
  chart.options.scales.x.min = range.min;
  chart.options.scales.x.max = range.max;
  chart.update('none');
}

export function _resizeCharts() {
  const toResize = [];
  const toDestroy = [];
  for (const [id, chart] of Object.entries(state.charts)) {
    const canvas = chart.canvas;
    if (!canvas) continue;
    const { w, h } = _readParentSize(canvas);
    if (w === 0 || h === 0) continue;
    if (typeof Chart !== 'undefined' && !Chart.getChart(canvas)) {
      delete state.charts[id];
      if (chart._modelId && !_pendingCharts.has(id)) _pendingCharts.set(id, { modelId: chart._modelId });
      _scheduleChartInit(id);
      continue;
    }
    if (canvas.width === w && canvas.height === h) continue;
    if (chart._full || _isCanvasNearViewport(canvas)) {
      toResize.push({ chart, w, h });
    } else {
      toDestroy.push(id);
    }
  }
  for (const id of toDestroy) {
    const chart = state.charts[id];
    if (!chart) continue;
    const modelId = chart._modelId;
    chart.destroy();
    delete state.charts[id];
    _offscreenSince.delete(id);
    const phEl = document.getElementById(_phId(id));
    if (phEl) phEl.classList.remove('hidden');
    if (!_pendingCharts.has(id) && modelId) _pendingCharts.set(id, { modelId });
  }
  for (const { chart, w, h } of toResize) {
    if (!state.charts[chart.canvas?.id]) continue;
    chart.resize(w, h);
  }
}

export function invalidateBucketCache() {
  localStorage.removeItem('mw_buckets_card');
  localStorage.removeItem('mw_buckets_modal');
  localStorage.removeItem('mw_buckets2_card');
  localStorage.removeItem('mw_buckets2_modal');
}

let _cleanupTimer = null;
const _CLEANUP_INTERVAL = 60_000;
const _OFFSCREEN_DESTROY_MS = 120_000;
const _OFFSCREEN_MARGIN = 300;
const _offscreenSince = new Map();

function _isCanvasNearViewport(canvas) {
  if (!canvas || !canvas.isConnected) return false;
  if (canvas.closest('[hidden]')) return false;
  const rect = canvas.getBoundingClientRect();
  if (rect.height === 0 || rect.width === 0) {
    const parent = canvas.closest('.provider-content');
    if (parent) {
      const style = getComputedStyle(parent);
      if (style.gridTemplateRows?.startsWith('0') || style.display === 'none') return true;
    }
  }
  return rect.bottom > -_OFFSCREEN_MARGIN && rect.top < window.innerHeight + _OFFSCREEN_MARGIN;
}

function _startCleanupTimer() {
  if (_cleanupTimer) return;
  _cleanupTimer = setInterval(() => {
    const now = Date.now();
    for (const [canvasId, chart] of Object.entries(state.charts)) {
      if (chart._full) continue;
      if (_isCanvasNearViewport(chart.canvas)) {
        _offscreenSince.delete(canvasId);
        continue;
      }
      if (!_offscreenSince.has(canvasId)) _offscreenSince.set(canvasId, now);
      if (now - _offscreenSince.get(canvasId) < _OFFSCREEN_DESTROY_MS) continue;
      chart.destroy();
      delete state.charts[canvasId];
      _offscreenSince.delete(canvasId);
      const phEl = document.getElementById(_phId(canvasId));
      if (phEl) phEl.classList.remove('hidden');
      if (!_pendingCharts.has(canvasId)) {
        const mid = chart._modelId || '';
        if (mid) _pendingCharts.set(canvasId, { modelId: mid });
      }
      const canvas = document.getElementById(canvasId);
      if (canvas && _chartObserver) _chartObserver.observe(canvas);
    }
  }, _CLEANUP_INTERVAL);
}

function _stopCleanupTimer() {
  if (_cleanupTimer) { clearInterval(_cleanupTimer); _cleanupTimer = null; }
}

let _scrollRaf = 0;
function _onScroll() {
  if (_scrollRaf) return;
  _scrollRaf = requestAnimationFrame(() => {
    _scrollRaf = 0;
    if (!_pendingCharts.size) return;
    for (const [canvasId] of _pendingCharts) {
      if (state.charts[canvasId]) continue;
      const canvas = document.getElementById(canvasId);
      if (!canvas || !_isCanvasNearViewport(canvas)) continue;
      _scheduleChartInit(canvasId);
    }
  });
}

export function refreshVisibleCharts() {
  requestAnimationFrame(() => {
    if (!_pendingCharts.size) return;
    for (const [canvasId] of _pendingCharts) {
      if (state.charts[canvasId]) continue;
      const canvas = document.getElementById(canvasId);
      if (!canvas || !_isCanvasNearViewport(canvas)) continue;
      _scheduleChartInit(canvasId);
    }
  });
}

function initLazyChartObserver() {
  if (_chartObserver) return _chartObserver;
  _chartObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      const canvasId = entry.target.id;
      if (!canvasId) continue;
      if (entry.isIntersecting) {
        _visibleCharts.add(canvasId);
        _scheduleChartInit(canvasId);
      } else {
        _visibleCharts.delete(canvasId);
      }
    }
  }, { rootMargin: `${_OFFSCREEN_MARGIN}px 0px`, threshold: 0 });
  window.addEventListener('scroll', _onScroll, { passive: true });
  _startCleanupTimer();
  return _chartObserver;
}

function _scheduleChartInit(canvasId) {
  if (state.charts[canvasId]) {
    const canvas = document.getElementById(canvasId);
    if (typeof Chart !== 'undefined' && canvas && !Chart.getChart(canvas)) {
      delete state.charts[canvasId];
    } else {
      return;
    }
  }
  const pending = _pendingCharts.get(canvasId);
  if (!pending) return;
  if (!_initQueue.includes(canvasId)) _initQueue.push(canvasId);
  _initSinceYield = 0;
  _drainInitQueue();
}

function _scheduleNextDrain() {
  _initSinceYield++;
  if (_initSinceYield >= _INIT_YIELD_EVERY && _initQueue.length > 0) {
    _initSinceYield = 0;
    setTimeout(_drainInitQueue, _INIT_YIELD_MS);
  } else {
    _drainInitQueue();
  }
}

function _drainInitQueue() {
  while (_initInFlight < _INIT_BATCH && _initQueue.length) {
    const canvasId = _initQueue.shift();
    if (state.charts[canvasId] || !_pendingCharts.has(canvasId)) continue;
    _initInFlight++;
    const pending = _pendingCharts.get(canvasId);
    const gen = _viewGen;
    const view = getCardView();
    const modelId = pending.modelId;
    const canvas = document.getElementById(canvasId);
    const size = canvas ? _readParentSize(canvas) : null;

    const m = state.metrics[modelId];
    const embedded = _hasCardViewData(m?.card_buckets, view);

    if (!embedded) { _initInFlight--; _scheduleNextDrain(); continue; }
    const buckets = _transformCardBuckets(m.card_buckets, view);
    if (!buckets || buckets.length < 2) { _initInFlight--; _scheduleNextDrain(); continue; }
    _lastFetch.set(modelId + ':' + view, Date.now());
    _loadChartJS().then(() => {
      if (state.charts[canvasId] || gen !== _viewGen) { _initInFlight--; _scheduleNextDrain(); return; }
      _nextFrame(() => {
        initChart(canvasId, buckets, false, view, modelId, false, size);
        _initInFlight--;
        _scheduleNextDrain();
      });
    }).catch(e => { logError(logTag('Chart', '\u2190', 'Error', 'LazyInit'), e); _initInFlight--; _scheduleNextDrain(); });
  }
}

export function observeChart(canvasId, modelId) {
  const observer = initLazyChartObserver();
  _pendingCharts.set(canvasId, { modelId });
  const canvas = document.getElementById(canvasId);
  if (canvas) observer.observe(canvas);
}

export function disconnectLazyChartObserver() {
  if (_chartObserver) {
    _chartObserver.disconnect();
    _chartObserver = null;
  }
  window.removeEventListener('scroll', _onScroll, { passive: true });
  if (_scrollRaf) { cancelAnimationFrame(_scrollRaf); _scrollRaf = 0; }
  _stopCleanupTimer();
  _pendingCharts.clear();
  _initQueue = [];
  _initInFlight = 0;
  _initSinceYield = 0;
}

export function unobserveChartsInContainer(container) {
  if (!_chartObserver) return;
  const canvases = container.querySelectorAll('canvas');
  for (const canvas of canvases) {
    const canvasId = canvas.id;
    if (!canvasId) continue;
    _chartObserver.unobserve(canvas);
    _pendingCharts.delete(canvasId);
    _visibleCharts.delete(canvasId);
  }
}

export function initPendingChartsInContainer(container) {
  const canvases = container.querySelectorAll('canvas');
  const resizeEntries = [];
  for (const canvas of canvases) {
    const canvasId = canvas.id;
    if (!canvasId) continue;
    if (canvas.closest('[hidden]')) continue;
    if (state.charts[canvasId]) {
      if (typeof Chart !== 'undefined' && !Chart.getChart(canvas)) {
        delete state.charts[canvasId];
      } else {
        resizeEntries.push({ canvas, ..._readParentSize(canvas) });
        continue;
      }
    }
    if (_pendingCharts.has(canvasId)) {
      _scheduleChartInit(canvasId);
    }
  }
  for (const { canvas, w, h } of resizeEntries) {
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    canvas.width = w;
    canvas.height = h;
    const chart = state.charts[canvas.id];
    if (chart) chart.resize();
  }
}

export function switchCardView(view) {
  const prev = getCardView();
  if (view === prev) return;
  setCardView(view);
  _viewGen++;
  _lastFetch.clear();
  for (const [canvasId, chart] of Object.entries(state.charts)) {
    if (chart._full) continue;
    const modelId = chart._modelId;
    if (!modelId) continue;
    const cacheKey = modelId + ':' + view;
    _lastFetch.set(cacheKey, Date.now());

    const m = state.metrics[modelId];
    const embedded = _hasCardViewData(m?.card_buckets, view);
    if (!embedded) continue;
    const buckets = _transformCardBuckets(m.card_buckets, view);
    if (!buckets || buckets.length < 2 || !state.charts[canvasId] || state.charts[canvasId]._full) continue;
    chart._buckets = buckets;
    chart._bucketed = buckets.length > 1 && buckets.some(b => b.count > 1);
    chart._view = view;
    chart.data.labels = buckets.map(b => b.timestamp);
    chart.data.datasets = _buildDatasets(buckets, false, view);
    const range = _dataRange(buckets, view);
    chart.options.scales.x.min = range.min;
    chart.options.scales.x.max = range.max;
    chart.update('none');
  }
  updateCardViewUI(view);
}

function updateCardViewUI(view) {
  document.querySelectorAll('[data-card-view]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.cardView === view);
  });
}

export {
  chartPhHTML,
  _transformModalBuckets,
  _calculateBuckets,
};
