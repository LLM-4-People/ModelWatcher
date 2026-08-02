// Entry point. Uses cache-then-network pattern: IndexedDB cache renders
// instantly on revisit (Phase 1), then fresh data overwrites (Phase 2).
// initNotifSystem MUST run before connectWS so prefs load before the WS
// onopen sync - otherwise the server filters out all notifications.
import { state, setMetrics, applyConfig, LS } from './state.js';
import { logError, logInfo, logTag, reportClientError, stripEphemeral } from './utils.js';
import { api, probeBackend, fetchProviderMetrics, fetchProviders, fetchModelInfoCapabilities } from './api.js';
import { initTooltips } from './tooltips.js';
import { _resizeCharts, invalidateBucketCache, _fetchMetaClear } from './chart.js';
import { renderSchedule, toggleProvider, toggleAllProviders, initScrollObserver, applyProvidersData, modelKeys, buildProviderSections, setScheduleUI, mergeModelInfo } from './dom.js';
import { openModal, closeModal } from './modal-loader.js';
import { closeNotifPanel, initNotifSystem, initPush, setCloseHelpPanel, syncNotifSettingsUI } from './notifications.js';
import { buildNotifPrefs } from './prefs.js';
import { closeHelpPanel, initHelpPanel, isHelpPanelOpen, setCloseNotifPanel, renderHelpLegends } from './help.js';
import { connectWS, updateWSStatus, refreshCardBuckets } from './ws.js';
import { initTheme } from './theme.js';
import { scheduleUI } from './frame.js';
import { cacheGet, cacheSet } from './cache.js';
import { initFilter } from './filter.js';

function _applyCfg(cfg) {
  applyConfig(cfg);
  if (cfg.color_thresholds) { renderHelpLegends(); syncNotifSettingsUI(); }
  renderSchedule();
}

window.onerror = (msg, src, line, col, err) => { logError(logTag('App', 'Err'), err || new Error(msg)); reportClientError({ message: String(msg), source: src || '', line, col, stack: err?.stack || '', type: 'error', url: location.href, ua: navigator.userAgent }); return true; };
window.addEventListener('unhandledrejection', e => { e.preventDefault(); const r = e.reason; logError(logTag('App', 'Rej'), r); reportClientError({ message: String(r?.message || r), stack: r?.stack || '', type: 'rejection', url: location.href, ua: navigator.userAgent }); });

let _resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(_resizeTimer);
  _resizeTimer = setTimeout(() => {
    invalidateBucketCache();
    _fetchMetaClear();
    _resizeCharts();
  }, 150);
});

document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if (isHelpPanelOpen()) { closeHelpPanel(); return; }
  const panel = document.getElementById('notif-panel');
  if (panel && panel.classList.contains('open')) { closeNotifPanel(); return; }
  closeModal();
});

document.addEventListener('click', e => {
  const modal = document.getElementById('modal');
  if (e.target === modal) closeModal();
  const closeBtn = document.getElementById('modal-close');
  if (e.target === closeBtn) closeModal();

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

  const card = e.target.closest('[data-model-key]');
  if (card) {
    if (e.target.closest('.provider-link')) return;
    openModal(card.dataset.modelKey);
  }
});

document.addEventListener('keydown', e => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const card = e.target.closest('[data-model-key]');
  if (!card) return;
  e.preventDefault();
  openModal(card.dataset.modelKey);
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

function _measureClientRTTFresh() {
  const t0 = performance.now();
  fetch(`${location.origin}/health?_rtt=${Date.now()}`, { cache: 'no-store' })
    .then(r => { if (!r.ok) return; const ms = Math.round(performance.now() - t0); if (ms > 1) state.clientRTT = Math.round(ms / 2); })
    .catch(() => {});
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

async function init() {
  logInfo(logTag('App', '→', 'Init', 'Loading'));
  setScheduleUI(scheduleUI);
  initTooltips();
  initTheme();
  updateWSStatus('connecting');
  initNotifSystem();
  connectWS();

  try { state.collapsedProviders = JSON.parse(localStorage.getItem(LS.COLLAPSED) || '[]'); } catch (e) { state.collapsedProviders = []; }
  localStorage.removeItem('mw_show_archived'); // setting removed; cleanup stale key

  // Phase 1: render from cache (instant on revisit)
  const [cachedProviders, cachedConfig, cachedMetrics, cachedCaps] = await Promise.all([
    cacheGet('providers_full'), cacheGet('config'), cacheGet('metrics_initial'), cacheGet('model_info_caps'),
  ]);
  let renderedFromCache = false;
  if (cachedConfig) { _applyCfg(cachedConfig); }
  if (cachedProviders) {
    applyProvidersData(cachedProviders);
    if (cachedCaps) mergeModelInfo(cachedCaps);
    if (cachedMetrics) {
      setMetrics(cachedMetrics);
      for (const p of state.providerOrder) state.fetchedProviders.add(p);
    }
    buildProviderSections();
    if (cachedMetrics) {
      scheduleUI({ models: Object.keys(cachedMetrics), summary: true, providers: true });
    }
    renderedFromCache = true;
    initFilter();
    logInfo(logTag('App', '←', 'Cache', 'Rendered', `${Object.keys(cachedMetrics || {}).length} models`));
  }

  // Phase 2: fetch fresh data (parallel) - providers + model capabilities
  const [cfg, providersData, capsData] = await Promise.all([
    api('/api/config'),
    fetchProviders(null),
    fetchModelInfoCapabilities(),
  ]);
  if (cfg) { cacheSet('config', cfg, 3600); _applyCfg(cfg); }
  if (providersData) {
    applyProvidersData(providersData);
    cacheSet('providers_full', providersData, 3600);
  }
  if (capsData) {
    mergeModelInfo(capsData);
    cacheSet('model_info_caps', capsData, 3600);
  }

  // Fire deploy-version check in background - not on critical path
  api('/api/deploy-version').then(d => {
    if (d?.version) state._deployVersion = d.version;
  }).catch(e => logError(logTag('App', '←', 'Error', 'DeployVersion'), e));

  const prevKeys = renderedFromCache ? modelKeys() : null;

  const metricsData = await fetchProviderMetrics(state.providerOrder, { cardBuckets: true });
  if (metricsData) {
    cacheSet('metrics_initial', stripEphemeral(metricsData), 300);
    setMetrics(metricsData);
    const now = Date.now();
    for (const p of state.providerOrder) {
      state._providerDataAt[p] = now;
      state.fetchedProviders.add(p);
    }
  }

  // Build sections AFTER data is available - renders real cards directly, no skeleton→real CLS
  // Also rebuild if previously rendered without metrics (partial cache)
  const needRebuild = !renderedFromCache || !cachedMetrics;
  if (prevKeys) {
    const newKeys = modelKeys();
    let changed = newKeys.size !== prevKeys.size;
    if (!changed) for (const k of newKeys) { if (!prevKeys.has(k)) { changed = true; break; } }
    if (changed || needRebuild) buildProviderSections();
  } else {
    buildProviderSections();
  }
  initFilter();

  if (metricsData) {
    scheduleUI({ models: Object.keys(metricsData), summary: true, providers: true });
  }

  _measureClientRTT();
  initScrollObserver();

  setInterval(() => scheduleUI({ checkLines: true }), 30000);

  setInterval(() => {
    if (state._wsConnected) return;
    const toFetch = state.providerOrder;
    fetchProviderMetrics(toFetch, { detailProviders: [...state.fetchedProviders] }).then(metrics => {
      if (!metrics) return;
      cacheSet('metrics_initial', stripEphemeral(metrics), 300);
      setMetrics(metrics);
      const now = Date.now();
      for (const p of state.providerOrder) { state._providerDataAt[p] = now; }
      scheduleUI({ models: Object.keys(metrics), summary: true, providers: true });
    }).catch(e => logError(logTag('App', '←', 'Error', 'MetricsPoll'), e));
  }, 30000);

  setInterval(() => refreshCardBuckets(), 5 * 60 * 1000);

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
  }, 60000);

  // Defer non-critical UI initialization until browser is idle
  // initNotifSystem MUST run before connectWS (loads prefs before WS sync)
  const _ric = window.requestIdleCallback || (cb => setTimeout(cb, 1));
  _ric(() => {
    initHelpPanel();
    setCloseHelpPanel(closeHelpPanel);
    setCloseNotifPanel(closeNotifPanel);
    window._pushInitPromise = initPush().catch(e => logError(logTag('Push', '←', 'Error', 'Init'), e));
  });

  setInterval(() => {
    if (!state._backendDown) return;
    probeBackend().then(up => {
      if (!up) return;
      logInfo(logTag('App', '←', 'Recovered', 'Backend up'));
      updateWSStatus('connecting');
      connectWS();
      fetchProviderMetrics(state.providerOrder, { detailProviders: [...state.fetchedProviders] }).then(m => { if (m) { setMetrics(m); scheduleUI({ models: Object.keys(m), summary: true, providers: true }); } }).catch(e => logError(logTag('App', '\u2190', 'Error', 'RecoveryMetrics'), e));
      api('/api/config').then(c => _applyCfg(c)).catch(e => logError(logTag('App', '\u2190', 'Error', 'RecoveryConfig'), e));
    });
  }, 15000);

  logInfo(logTag('App', '→', 'Init', 'Complete', `${Object.keys(state.metrics).length} models`));
}

init().catch(e => logError(logTag('App', '←', 'Error', 'Init'), e));
