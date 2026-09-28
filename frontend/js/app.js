// Entry point. Uses cache-then-network pattern: IndexedDB cache renders
// instantly on revisit (Phase 1), then fresh data overwrites (Phase 2).
// initNotifSystem MUST run before connectWS so prefs load before the WS
// hello sync - otherwise the server filters out all notifications.
import { state, setMetrics, BOOT, LS } from './state.js';
import { logError, logInfo, logDebug, logTag, reportClientError, stripEphemeral, closeTopLayer, pruneStorage, slug } from './utils.js';
import { api, fetchLive, probeBackend, fetchProviderMetrics, fetchProviders, fetchModelInfoCapabilities } from './api.js';
import { initTooltips } from './tooltips.js';
import { _resizeCharts, _fetchMetaClear } from './chart.js';
import { toggleProvider, toggleAllProviders, initScrollObserver, applyProvidersData, modelKeys, buildProviderSections, setScheduleUI, mergeModelInfo } from './dom.js';
import { openModal, closeModal } from './modal-loader.js';
import { closeNotifPanel, initNotifSystem, initPush, setCloseHelpPanel } from './notifications.js';
import { buildNotifPrefs } from './prefs.js';
import { closeHelpPanel, initHelpPanel, setCloseNotifPanel } from './help.js';
import { connectWS, applyConfigAndRender } from './ws.js';
import { setWSStatus, onConnChange } from './conn.js';
import { initTheme } from './theme.js';
import { scheduleUI } from './frame.js';
import { cacheGet, cacheSet } from './cache.js';
import { initFilter } from './filter.js';

window.onerror = (msg, src, line, col, err) => { logError(logTag('App', 'Err'), err || new Error(msg)); reportClientError({ message: String(msg), source: src || '', line, col, stack: err?.stack || '', type: 'error', url: location.href, ua: navigator.userAgent }); return true; };
window.addEventListener('unhandledrejection', e => { e.preventDefault(); const r = e.reason; logError(logTag('App', 'Rej'), r); reportClientError({ message: String(r?.message || r), stack: r?.stack || '', type: 'rejection', url: location.href, ua: navigator.userAgent }); });

let _resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(_resizeTimer);
  _resizeTimer = setTimeout(() => {
    _fetchMetaClear();
    _resizeCharts();
  }, 150);
});

// The one Escape handler: it closes the top overlay only (finding F49: three listeners closed
// the modal from inside the date picker). A field that used Escape itself prevents the default.
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape' || e.defaultPrevented) return;
  if (closeTopLayer()) e.preventDefault();
});

document.addEventListener('click', e => {
  const modal = document.getElementById('modal');
  if (e.target === modal) closeModal();
  if (e.target.closest('#modal-close')) closeModal();

  const toggleAll = e.target.closest('[data-action]');
  if (toggleAll) {
    toggleAllProviders(toggleAll.dataset.action);
    return;
  }

  const providerHeader = e.target.closest('.provider-header');
  if (providerHeader) {
    if (e.target.closest('.provider-link')) return;
    const sec = providerHeader.closest('.provider-section');
    if (sec) toggleProvider(sec.dataset.providerSlug);
    return;
  }

  // A click anywhere on a card opens it; keyboard users have the card's title button (F74)
  const card = e.target.closest('[data-model-key]');
  if (card) {
    if (e.target.closest('.provider-link')) return;
    openModal(card.dataset.modelKey);
  }
});

document.addEventListener('animationend', e => { if (e.animationName === 'fade-in') e.target.classList.remove('fade-in-once', 'fade-in'); });


function _measureClientRTT() {
  try {
    const nav = performance.getEntriesByType('navigation')[0];
    if (nav && nav.connectEnd > nav.connectStart && nav.connectStart > 0) {
      const tcpRtt = nav.connectEnd - nav.connectStart;
      const tlsRtt = nav.secureConnectionStart > 0
        ? (nav.connectEnd - nav.secureConnectionStart) : 0;
      const rtt = Math.round(tcpRtt + (tlsRtt || 0));
      if (rtt > 0) { state.clientRTT = rtt; return; }
    }
  } catch (e) { logError(logTag('App', '←', 'Error', 'RTT'), e); }
  _measureClientRTTFresh();
}

// A liveness probe that fails is expected while the server is down: debug, not error (F34)
function _measureClientRTTFresh() {
  const t0 = performance.now();
  fetchLive()
    .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); const ms = Math.round(performance.now() - t0); if (ms > 1) state.clientRTT = Math.round(ms / 2); })
    .catch(e => logDebug(logTag('App', '←', 'RTT', 'Unavailable', e?.message)));
}

// While the server looks unreachable, probe liveness every unreachable.retry_interval (read per
// round, so a config change applies); a timeout chain instead of setInterval for that reason.
// The next round is armed in finally, so a failing recovery step cannot end the loop (F33).
function _probeWhileDown() {
  setTimeout(() => {
    if (!state._backendDown) { _probeWhileDown(); return; }
    probeBackend().then(up => {
      if (!up) return;
      logInfo(logTag('App', '←', 'Recovered', 'Backend up'));
      connectWS();
      if (!_loaded) { loadDashboard().catch(e => logError(logTag('App', '←', 'Error', 'RecoveryLoad'), e)); return; }
      fetchProviderMetrics(state.providerOrder, { detailProviders: [...state.fetchedProviders] }).then(m => { if (m) { setMetrics(m); scheduleUI({ models: Object.keys(m), summary: true, providers: true }); } }).catch(e => logError(logTag('App', '←', 'Error', 'RecoveryMetrics'), e));
      api('/api/config').then(c => { if (c) applyConfigAndRender(c); }).catch(e => logError(logTag('App', '←', 'Error', 'RecoveryConfig'), e));
    }).catch(e => logError(logTag('App', '←', 'Error', 'Probe'), e))
      .finally(_probeWhileDown);
  }, state.conn.unreachable.retry_interval * 1000);
}

// ── Main area states (finding F68): placeholders until the first data, an error with a retry
// when it cannot load. #skeleton holds the placeholders; buildProviderSections replaces them.
let _loaded = false;

function _renderLoadState() {
  const err = document.getElementById('load-error');
  if (!err) return;
  err.hidden = _loaded || !state._backendDown;
  const skel = document.getElementById('skeleton');
  if (skel) skel.hidden = !err.hidden;
}

function _showLoadFailure(message) {
  const err = document.getElementById('load-error');
  if (!err) return;
  err.querySelector('.load-error-detail').textContent = message;
  err.hidden = false;
  const skel = document.getElementById('skeleton');
  if (skel) skel.hidden = true;
}

if ('serviceWorker' in navigator) {
  let _swRefreshing = false;
  let wasControlled = Boolean(navigator.serviceWorker.controller);
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (!wasControlled) { wasControlled = true; return; }
    if (_swRefreshing) return;
    _swRefreshing = true;
    location.reload();
  });
  navigator.serviceWorker.addEventListener('message', (e) => {
    if (e.data?.type === 'sw_needs_prefs' && e.ports?.[0]) {
      let cid = localStorage.getItem(LS.CLIENT_ID);
      if (!cid) { cid = 'c_' + Array.from(crypto.getRandomValues(new Uint8Array(4)), b => b.toString(16).padStart(2, '0')).join(''); localStorage.setItem(LS.CLIENT_ID, cid); }
      const prefs = buildNotifPrefs();
      e.ports[0].postMessage({ client_id: cid, prefs });
    }
  });
}

// Fresh card data: the scroll observer renders these providers without fetching again
function _markFresh(providers) {
  const now = Date.now();
  for (const p of providers) state._providerDataAt[p] = now;
}

// The providers the first load renders cards for: the first ui.eager_providers that are not
// collapsed. The rest render when they scroll near the viewport (finding F89: every provider's
// cards and charts used to be built up front).
function _eagerProviders() {
  return state.providerOrder.filter(p => !state.collapsedProviders.includes(slug(p)))
    .slice(0, state.ui.eager_providers);
}

// Phase 2: fresh config, model list and data. Also the retry of a failed first load.
async function loadDashboard() {
  const [cfg, providersData, capsData] = await Promise.all([
    api('/api/config'),
    fetchProviders(null),
    fetchModelInfoCapabilities(),
  ]);
  if (cfg) { cacheSet('config', cfg, state.ui.cache_ttl.config); applyConfigAndRender(cfg); }
  if (providersData) {
    applyProvidersData(providersData);
    cacheSet('providers_full', providersData, state.ui.cache_ttl.providers);
  }
  if (capsData) {
    mergeModelInfo(capsData);
    cacheSet('model_info_caps', capsData, state.ui.cache_ttl.model_info);
  }
  if (!state.providerOrder.length) {
    if (!_loaded) _showLoadFailure(state._backendDown ? 'The server is unreachable.' : 'The server sent no model list.');
    return;
  }

  api('/api/deploy-version').then(d => {
    if (d?.version) state._deployVersion = d.version;
  }).catch(e => logError(logTag('App', '←', 'Error', 'DeployVersion'), e));

  const prevKeys = _loaded ? modelKeys() : null;
  // Statuses and scores of every model (the filter and the counts need them), card buckets only
  // for the eager providers
  const eager = _eagerProviders();
  const [summaries, eagerData] = await Promise.all([
    fetchProviderMetrics(state.providerOrder),
    fetchProviderMetrics(eager, { cardBuckets: true }),
  ]);
  for (const m of [summaries, eagerData]) if (m) setMetrics(m);
  if (eagerData) {
    cacheSet('metrics_initial', stripEphemeral(state.metrics), state.ui.cache_ttl.metrics);
    _markFresh(eager);
  }
  if (!summaries && !eagerData && !_loaded) {
    _showLoadFailure('The dashboard data did not load.');
    return;
  }

  const changed = !prevKeys || prevKeys.size !== state.models.length || state.models.some(m => !prevKeys.has(m.id));
  if (changed || !_loaded) buildProviderSections();
  _loaded = true;
  _renderLoadState();
  initFilter();
  scheduleUI({ models: Object.keys(state.metrics), summary: true, providers: true });
  initScrollObserver();
}

async function init() {
  // One check of the page bootstrap; without it nothing below can work (finding F32)
  if (!BOOT) {
    logError(logTag('App', '→', 'Error', 'Bootstrap'), new Error('window.__MW_BOOT__ missing from the page: the page and the server are out of step'));
    setWSStatus('unconfigured');
    _showLoadFailure('This page came without its settings from the server. Reload the page.');
    return;
  }
  logInfo(logTag('App', '→', 'Init', 'Loading'));
  const stale = pruneStorage(localStorage, LS, BOOT.storage_prefix);
  if (stale.length) logInfo(logTag('App', '→', 'Storage', 'Removed', stale.join(',')));
  setScheduleUI(scheduleUI);
  initTooltips();
  initTheme();
  initNotifSystem();
  onConnChange(_renderLoadState);
  connectWS();

  try { state.collapsedProviders = JSON.parse(localStorage.getItem(LS.COLLAPSED) || '[]'); } catch (e) { logError(logTag('App', '←', 'Error', 'CollapsedState'), e); state.collapsedProviders = []; }

  // Phase 1: render from cache (instant on revisit)
  const [cachedProviders, cachedConfig, cachedMetrics, cachedCaps] = await Promise.all([
    cacheGet('providers_full'), cacheGet('config'), cacheGet('metrics_initial'), cacheGet('model_info_caps'),
  ]);
  if (cachedConfig) applyConfigAndRender(cachedConfig);
  if (cachedProviders) {
    applyProvidersData(cachedProviders);
    if (cachedCaps) mergeModelInfo(cachedCaps);
    if (cachedMetrics) setMetrics(cachedMetrics);
    buildProviderSections();
    if (cachedMetrics) scheduleUI({ models: Object.keys(cachedMetrics), summary: true, providers: true });
    _loaded = true;
    _renderLoadState();
    initFilter();
    logInfo(logTag('App', '←', 'Cache', 'Rendered', `${Object.keys(cachedMetrics || {}).length} models`));
  }

  document.getElementById('load-retry')?.addEventListener('click', () => {
    loadDashboard().catch(e => logError(logTag('App', '←', 'Error', 'Retry'), e));
  });

  await loadDashboard();
  _measureClientRTT();

  const ui = state.ui;
  setInterval(() => scheduleUI({ checkLines: true }), ui.check_line_refresh * 1000);

  setInterval(() => {
    if (state._wsConnected || !_loaded) return;
    fetchProviderMetrics(state.providerOrder, { detailProviders: [...state.fetchedProviders] }).then(metrics => {
      if (!metrics) return;
      setMetrics(metrics);
      const now = Date.now();
      for (const p of state.providerOrder) { state._providerDataAt[p] = now; }
      scheduleUI({ models: Object.keys(metrics), summary: true, providers: true });
    }).catch(e => logError(logTag('App', '←', 'Error', 'MetricsPoll'), e));
  }, ui.metrics_poll * 1000);

  setInterval(() => {
    api('/api/deploy-version').then(d => {
      if (!d || !d.version) return;
      if (state._suppressDeployReload) { state._suppressDeployReload = false; state._deployVersion = d.version; return; }
      if (state._deployVersion && d.version !== state._deployVersion) {
        logInfo(logTag('App', '←', 'Deploy', 'Changed'));
        location.reload();
      }
      state._deployVersion = d.version;
    }).catch(e => logError(logTag('App', '←', 'Error', 'VersionPoll'), e));
  }, ui.deploy_poll * 1000);

  // Defer non-critical UI initialization until browser is idle
  // initNotifSystem MUST run before connectWS (loads prefs before WS sync)
  const _ric = window.requestIdleCallback || (cb => setTimeout(cb, 1));
  _ric(() => {
    initHelpPanel();
    setCloseHelpPanel(closeHelpPanel);
    setCloseNotifPanel(closeNotifPanel);
    window._pushInitPromise = initPush().catch(e => logError(logTag('Push', '←', 'Error', 'Init'), e));
  });

  _probeWhileDown();

  logInfo(logTag('App', '→', 'Init', 'Complete', `${Object.keys(state.metrics).length} models`));
}

init().catch(e => logError(logTag('App', '←', 'Error', 'Init'), e));
