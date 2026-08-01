// Notification UI, toasts, push init, and settings panel. Server-side prefs
// are the single enforcement point; client-side handleNotification applies
// partial defense-in-depth (master toggle + popups + recovery grounding).
import { state, _NOTIF_OPTS, LS } from './state.js';
import { esc, logError, logWarn, logInfo, logDebug, logTag, cap, parseModelKey, initSheetDrag, BP_SM, setText, setHTML, collapsibleHTML, toggleCollapsible } from './utils.js';
import { TIER_TEXT, TIER_BG, timeAgo } from './format.js';
import { api } from './api.js';
import { cacheGet, cacheSet } from './cache.js';
import { openModal } from './modal-loader.js';
import { buildNotifPrefs, syncWSPrefs } from './prefs.js';

const _MAX_TOASTS = 4;
const _DEFAULT_TOAST_MS = 5000;
const _TOAST_REMOVE_FALLBACK_MS = 300;
const _MAX_DROPDOWN_ITEMS = 20;
const _MAX_READ_IDS = 200;
const _REFRESH_INTERVAL_MS = 30 * 1000;
const _PUSH_TEST_RESET_MS = 2500;
const _DEFAULT_HISTORY_CAP = 50;

let _closeHelpPanelFn = null;
export function setCloseHelpPanel(fn) { _closeHelpPanelFn = fn; }

const _degradedOpt = _NOTIF_OPTS.find(o => o.key === 'degraded');
const _degradedChildren = _degradedOpt?.childrenKeys || [];
const _metricChildren = (_degradedOpt?.children || []).filter(c => c.metric);
const _degradedMetricEvts = new Set(_metricChildren.flatMap(c => [c.down?.key, c.up?.key].filter(Boolean)));

// ── Settings category definitions (single source of truth for grouping) ──────
// Mirrors HELP_CATEGORIES in help.js - each entry drives one collapsible section
const SETTINGS_CATEGORIES = [
  { id: 'notifications', label: 'Notifications', open: true, build: _buildNotifBody },
];

// --- Alert key helpers (DRY: single toggle that writes down+up in lockstep) ---

// Returns [downKey, upKey] for an alert opt, or null if the key isn't an alert.
function _alertKeys(key) {
  for (const o of _NOTIF_OPTS) {
    if (o.alert && o.key === key) return [o.down.key, o.up.key];
    if (o.children) for (const c of o.children) {
      if (c.alert && c.key === key) return [c.down.key, c.up.key];
    }
  }
  return null;
}

// True if either sub-toggle of an alert is on (legacy out-of-lockstep reads as on).
function _alertOn(key) {
  const pair = _alertKeys(key);
  if (!pair) return false;
  const s = state._notifSettings;
  return s[pair[0]] !== false || s[pair[1]] !== false;
}

// Flattens _NOTIF_OPTS into a list including children - used for toggle wiring + sync.
function _iterOptsFlat() {
  const result = [];
  for (const opt of _NOTIF_OPTS) {
    result.push(opt);
    if (opt.children) for (const c of opt.children) result.push(c);
  }
  return result;
}

// --- localStorage helpers ---

function _loadNotifSettings() {
  try {
    const s = JSON.parse(localStorage.getItem('mw_notif_settings'));
    if (s) {
      // Clean up legacy `recovered` field - derived in prefs.js from recovered_offline||recovered_degraded
      delete s.recovered;
      Object.assign(state._notifSettings, s);
    }
    const enabledAt = localStorage.getItem('mw_notif_enabled_at');
    if (enabledAt) state._notifEnabledAt = enabledAt;
    if (localStorage.getItem('mw_notif_local') === '1' && typeof Notification !== 'undefined' && Notification.permission === 'granted') state._notifyLocal = true;
  } catch (e) { logError(logTag('Notif', '←', 'Error', 'SettingsLoad'), e); }
}

function _saveNotifSettings() {
  localStorage.setItem('mw_notif_settings', JSON.stringify(state._notifSettings));
  syncWSPrefs();
  _syncPushPrefs().catch(e => { logError(logTag('Push', '←', 'Error', 'PrefsSync'), e); });
}

function _saveNotifyLocal() {
  localStorage.setItem('mw_notif_local', state._notifyLocal ? '1' : '0');
}

function _saveNotifHistory() {
  try {
    localStorage.setItem('mw_notif_history', JSON.stringify(state._notifHistory));
  } catch (e) { logError(logTag('Notif', '←', 'Error', 'HistorySave'), e); }
}

function _loadNotifHistory() {
  try {
    const stored = JSON.parse(localStorage.getItem('mw_notif_history'));
    if (Array.isArray(stored) && stored.length) {
      const enabledAt = state._notifEnabledAt;
      state._notifHistory = enabledAt ? stored.filter(n => n.timestamp > enabledAt) : stored;
      state._notifUnread = state._notifHistory.length;
      updateNotifBadge();
      renderNotifDropdownList();
    }
  } catch (e) { logError(logTag('Notif', '←', 'Error', 'HistoryLoad'), e); }
}

function _getClientId() {
  let id = localStorage.getItem(LS.CLIENT_ID);
  if (!id) {
    const bytes = new Uint8Array(4);
    crypto.getRandomValues(bytes);
    id = 'c_' + Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
    localStorage.setItem(LS.CLIENT_ID, id);
  }
  return id;
}

// --- Shared post-mutation sync ---

let _saveHistoryRaf = 0;

function _syncNotifUI() {
  updateNotifBadge();
  const panel = document.getElementById('notif-panel');
  if (panel?.classList.contains('open')) {
    const activeView = document.getElementById('notif-view-list');
    if (activeView?.classList.contains('notif-view-active')) renderNotifDropdownList();
  }
  if (!_saveHistoryRaf) _saveHistoryRaf = requestAnimationFrame(() => { _saveHistoryRaf = 0; _saveNotifHistory(); });
}

function _clearNotifHistory() {
  logInfo(logTag('Notif', '→', 'Clear'));
  state._notifHistory = [];
  state._notifUnread = 0;
  _syncNotifUI();
}

// --- Shared read-ID management ---

function _markNotifRead(id) {
  if (!id) return;
  try {
    const ids = JSON.parse(localStorage.getItem('mw_notif_read_ids') || '[]');
    if (!ids.includes(id)) {
      ids.push(id);
      if (ids.length > _MAX_READ_IDS) ids.splice(0, ids.length - _MAX_READ_IDS);
      localStorage.setItem('mw_notif_read_ids', JSON.stringify(ids));
    }
  } catch (e) { logError(logTag('Notif', '←', 'Error', 'ReadIdsSave'), e); }
}

// --- Shared notification filter ---

function _notifMatchesPrefs(notif) {
  if (!notif || !notif.event_type || !notif.model_key) return false;
  const s = state._notifSettings;
  if (!s.enabled) return false;
  const evt = notif.event_type;
  if (evt === 'recovered') {
    if (s.recovered_offline === false && s.recovered_degraded === false) return false;
  } else {
    const mapped = { recovered_offline: 'recovered_offline', recovered_degraded: 'recovered_degraded', partially_recovered: 'recovered_degraded' }[evt];
    if (mapped) { if (s[mapped] === false) return false; }
    else if (s[evt] === false) return false;
  }
  if (_degradedChildren.includes(evt) && !s.degraded) return false;
  if (!_notifProviderEnabled(notif.model_key)) return false;
  if (_degradedMetricEvts.has(evt)) {
    const metricKey = evt.startsWith('recovered_') ? evt.replace('recovered_', 'degraded_') : evt;
    const userTier = s[`${metricKey}_tier`];
    if (userTier !== null && typeof notif.current_tier === 'number') {
      if (evt.startsWith('degraded') && notif.current_tier < userTier) return false;
      if (evt.startsWith('recovered') && notif.current_tier >= userTier) return false;
      if (evt.startsWith('recovered') && typeof notif.prev_tier === 'number' && notif.prev_tier < userTier) return false;
    }
  }
  return true;
}

function _isRecoveryGrounded(notif) {
  const evt = notif.event_type;
  if (evt !== 'recovered' && evt !== 'recovered_offline' && evt !== 'recovered_degraded' && evt !== 'partially_recovered' && evt !== 'recovered_tps' && evt !== 'recovered_ttft') return true;
  const enabledAt = state._notifEnabledAt;
  if (enabledAt && notif.degraded_since) {
    const enabledEpoch = new Date(enabledAt).getTime() / 1000;
    if (!isNaN(enabledEpoch) && enabledEpoch > notif.degraded_since) return false;
  }
  return true;
}

function _notifProviderEnabled(modelKey) {
  if (!modelKey) return true;
  const providers = state._notifSettings.providers;
  if (!providers || providers.length === 0) return true;
  const { provider } = parseModelKey(modelKey);
  return !provider || providers.includes(provider);
}

// --- Push prefs sync ---

async function _syncPushPrefs() {
  if (!window._pushSub) return;
  const cid = _getClientId();
  try {
    const body = { prefs: buildNotifPrefs(), client_id: cid };
    if (window._pushSub) body.endpoint = window._pushSub.endpoint;
    const res = await api('/api/push/preferences', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (res === null) logWarn(logTag('Push', '←', 'Warn', 'PrefsSyncFailed'));
    else if (res?.updated > 0) logDebug(logTag('Push', '←', 'Sync', `${res.updated} sub(s) updated`));
  } catch (e) { logError(logTag('Push', '←', 'Error', 'PrefsSync'), e); }
}

// --- Notification type helpers ---

function _notifTypeLabel(evt, action) {
  if (evt === 'provider_changed') return action === 'removed' ? 'Provider Removed' : 'Provider Added';
  if (evt === 'model_changed') return action === 'removed' ? 'Model Removed' : 'Model Added';
  return state.eventLabels[evt] || evt;
}

function _notifSeverity(evt, action) {
  if (evt === 'offline') return 'offline';
  if (evt.startsWith('degraded') || evt === 'partially_recovered') return 'degraded';
  if (evt.startsWith('recovered')) return 'recovered';
  if (evt === 'provider_changed' || evt === 'model_changed') return action === 'removed' ? 'removed' : 'added';
  return 'recovered';
}

function _notifIconInfo(evt, action) {
  const sev = _notifSeverity(evt, action);
  return {
    icon: sev === 'added' ? '+' : sev === 'removed' ? '−' : sev === 'recovered' ? '✓' : sev === 'degraded' ? '⚠' : '✕',
    className: evt,
    severity: sev,
  };
}

// --- Core notification handler ---

function _displayName(modelKey) {
  const entry = state._modelMap[modelKey];
  if (entry?.name) return entry.name;
  const { provider, model } = parseModelKey(modelKey);
  return model || provider;
}

export function handleNotification(notif) {
  if (!notif || !notif.model_key) return;
  logDebug(logTag('Notif', '\u2190', cap(notif.event_type), notif.model_key));
  if (!state._notifSettings.enabled) return;
  if (!_isRecoveryGrounded(notif)) return;
  const evt = notif.event_type;
  if (_degradedChildren.includes(evt) && !state._notifSettings.degraded) return;

  state._notifHistory.unshift(notif);
  const historyCap = state._notifServerConfig?.in_app?.history_size || _DEFAULT_HISTORY_CAP;
  if (state._notifHistory.length > historyCap) state._notifHistory.length = historyCap;
  state._notifUnread++;
  _syncNotifUI();

  if (state._notifSettings.popups !== false) showToast(notif);

  // Browser notification - same tag deduplicates with server push if it arrives
  if (state._notifSettings.popups !== false && Notification.permission === 'granted') {
    try {
      const { provider } = parseModelKey(notif.model_key);
      const model = _displayName(notif.model_key);
      const title = [provider, model, _notifTypeLabel(notif.event_type, notif.action)].filter(Boolean).join(' - ');
      const tag = 'mw-' + notif.model_key + (notif.action ? `-${notif.action}` : '');
      new Notification(title, { body: notif.body || '', icon: (window.__STATIC_PREFIX__ || '/frontend') + '/icon-192.png', tag });
    } catch (e) { logError(logTag('Notif', '\u2190', 'Error', 'BrowserNotify'), e); }
  }
}

// --- Toast popups ---

function showToast(notif) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const { icon, className, severity } = _notifIconInfo(notif.event_type, notif.action);
  const { provider } = parseModelKey(notif.model_key);
  const model = _displayName(notif.model_key);
  const el = document.createElement('div');
  el.className = 'toast';
  // Per-toast ARIA live region is NOT needed - #toast-container has role="status" aria-live="polite" aria-atomic="false"
  // (Roselli: live region must exist in DOM statically, not on dynamic children)
  const ariaLabel = [provider, model, _notifTypeLabel(notif.event_type, notif.action)].filter(Boolean).join(' - ');
  el.setAttribute('aria-label', ariaLabel);
  el.innerHTML = `<div class="toast-icon ${esc(className)} sev-${severity}">${icon}</div><div class="toast-body"><div class="toast-header"><span class="toast-type t-${severity}">${esc(_notifTypeLabel(notif.event_type, notif.action))}</span><button class="toast-dismiss" aria-label="Dismiss">&times;</button></div>${provider ? `<div class="toast-provider">${esc(provider)}</div>` : ''}<div class="toast-model">${esc(model)}</div>${notif.body ? `<div class="toast-detail">${esc(notif.body)}</div>` : ''}</div>`;
  el.addEventListener('click', (e) => {
    if (e.target.classList.contains('toast-dismiss')) { dismissToast(el); return; }
    dismissToast(el);
    if (!notif.model_key.endsWith('::')) openModal(notif.model_key);
  });
  container.appendChild(el);
  const maxToasts = _MAX_TOASTS;
  while (container.children.length > _MAX_TOASTS) {
    const oldest = container.children[0];
    if (oldest._dismissTimer) { clearTimeout(oldest._dismissTimer); oldest._dismissTimer = null; }
    oldest.remove();
  }
  const duration = state._notifServerConfig?.in_app?.toast_duration_ms || _DEFAULT_TOAST_MS;
  const timer = setTimeout(() => dismissToast(el), duration);
  el._dismissTimer = timer;
  const pause = () => { if (el._dismissTimer) { clearTimeout(el._dismissTimer); el._dismissTimer = null; } };
  const resume = () => { if (!el._dismissTimer && !el._dismissed) el._dismissTimer = setTimeout(() => dismissToast(el), duration); };
  el.addEventListener('mouseenter', pause);
  el.addEventListener('mouseleave', resume);
  el.addEventListener('focusin', pause);
  el.addEventListener('focusout', resume);
}

function dismissToast(el) {
  if (el._dismissTimer) { clearTimeout(el._dismissTimer); el._dismissTimer = null; }
  if (el._dismissed) return;
  el._dismissed = true;
  el.classList.add('toast-out');
  el.addEventListener('animationend', () => el.remove(), { once: true });
  setTimeout(() => { if (el.parentNode) el.remove(); }, _TOAST_REMOVE_FALLBACK_MS);
}

// --- Badge ---

function updateNotifBadge() {
  const badge = document.getElementById('notif-badge');
  if (!badge) return;
  if (state._notifUnread > 0) {
    setText(badge, state._notifUnread > 99 ? '99+' : String(state._notifUnread));
    badge.classList.add('visible');
  } else {
    setText(badge, '');
    badge.classList.remove('visible');
  }
}

// --- Empty state ---

const _BELL_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>';

function _emptyStateHTML() {
  const enabled = state._notifSettings.enabled;
  const title = enabled ? "You're all caught up" : 'Notifications are off';
  const body = enabled
    ? "We'll let you know when models go offline, degrade, or recover."
    : 'Enable notifications to receive alerts when models change.';
  const cta = enabled ? 'Notification settings' : 'Open settings';
  return `<div class="notif-empty"><div class="notif-empty-icon">${_BELL_SVG}</div><div class="notif-empty-title">${esc(title)}</div><div class="notif-empty-body">${esc(body)}</div><button type="button" class="notif-empty-cta" data-action="switch-settings">${esc(cta)}</button></div>`;
}

// --- Dropdown list ---

function _activateNotifItem(item) {
  const modelKey = item.dataset.model;
  const notifId = item.dataset.notifId;
  if (notifId) {
    _markNotifRead(notifId);
    if (state._notifUnread > 0) state._notifUnread--;
    state._notifHistory = state._notifHistory.filter(n => n.id !== notifId);
    _syncNotifUI();
  }
  closeNotifPanel();
  if (modelKey && !modelKey.endsWith('::')) openModal(modelKey);
}

function renderNotifDropdownList() {
  const list = document.getElementById('notif-dropdown-list');
  if (!list) return;
  if (state._notifHistory.length === 0) {
    setHTML(list, _emptyStateHTML());
    const cta = list.querySelector('[data-action="switch-settings"]');
    if (cta) cta.addEventListener('click', () => _switchPanelView('settings'));
    return;
  }
  setHTML(list, state._notifHistory.slice().sort((a, b) => Date.parse(b.timestamp || 0) - Date.parse(a.timestamp || 0)).slice(0, _MAX_DROPDOWN_ITEMS).map(n => {
    const { icon, className, severity } = _notifIconInfo(n.event_type, n.action);
    const { provider } = parseModelKey(n.model_key);
    const model = _displayName(n.model_key);
    return `<div class="notif-dropdown-item unread ni-${severity}" role="listitem" tabindex="0" data-model="${esc(n.model_key)}" data-notif-id="${esc(n.id || '')}"><div class="notif-list-icon ${className} sev-${severity}" aria-hidden="true">${icon}</div><div class="notif-item-content"><div class="notif-item-row"><span class="notif-item-type t-${severity}">${esc(_notifTypeLabel(n.event_type, n.action))}</span><span class="notif-item-time" data-ts="${n.timestamp || ''}">${timeAgo(n.timestamp)}</span></div>${provider ? `<div class="notif-item-provider">${esc(provider)}</div>` : ''}<div class="notif-item-model">${esc(model)}</div>${n.body ? `<div class="notif-item-body">${esc(n.body)}</div>` : ''}</div></div>`;
  }).join(''));
  list.querySelectorAll('.notif-dropdown-item').forEach(item => {
    item.addEventListener('click', () => _activateNotifItem(item));
    item.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _activateNotifItem(item); }
      else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const items = [...list.querySelectorAll('.notif-dropdown-item')];
        const idx = items.indexOf(item);
        const next = e.key === 'ArrowDown' ? (idx + 1) % items.length : (idx - 1 + items.length) % items.length;
        items[next]?.focus();
      }
    });
  });
}

// --- Panel open/close ---

function _toggleNotifPanel(forceView, triggerId) {
  const panel = document.getElementById('notif-panel');
  if (!panel) return;
  if (panel.classList.contains('open')) {
    if (forceView && _panelView !== forceView) {
      _switchPanelView(forceView);
    } else {
      if (triggerId) _panelOpenTrigger = triggerId;
      closeNotifPanel();
    }
  } else {
    _openNotifPanel(forceView, triggerId);
  }
}

let _lastRefreshAt = 0;
let _panelView = 'list';

function _setPanelExpanded(open) {
  for (const id of ['notify-btn', 'notif-settings-btn']) {
    const el = document.getElementById(id);
    if (el) el.setAttribute('aria-expanded', String(open));
  }
}

let _panelOpenTrigger = 'notify-btn';

function _openNotifPanel(forceView, triggerId) {
  _panelOpenTrigger = triggerId || 'notify-btn';
  const panel = document.getElementById('notif-panel');
  const backdrop = document.getElementById('notif-backdrop');
  if (!panel) return;
  if (_closeHelpPanelFn) _closeHelpPanelFn();
  if (window.innerWidth >= BP_SM) {
    const btn = document.getElementById(_panelOpenTrigger) || document.getElementById('notify-btn');
    if (btn) {
      const r = btn.getBoundingClientRect();
      panel.style.top = (r.bottom + 8) + 'px';
      panel.style.left = 'auto';
      panel.style.right = (window.innerWidth - r.right) + 'px';
      panel.style.bottom = 'auto';
    }
  }
  panel.classList.add('open');
  _setPanelExpanded(true);
  if (backdrop && window.innerWidth < BP_SM) backdrop.classList.add('open');
  const view = forceView || (state._notifSettings.enabled ? 'list' : 'settings');
  _switchPanelView(view);
  renderNotifDropdownList();
  if (Date.now() - _lastRefreshAt > _REFRESH_INTERVAL_MS) refreshNotifHistory();
  // Focus the first actionable element in the active view header (accessibility)
  requestAnimationFrame(() => {
    const activeHeader = document.querySelector('.notif-panel-header:not(.hidden)');
    activeHeader?.querySelector('button')?.focus();
  });
}

export function closeNotifPanel() {
  const panel = document.getElementById('notif-panel');
  const backdrop = document.getElementById('notif-backdrop');
  if (!panel) return;
  const wasOpen = panel.classList.contains('open');
  panel.classList.remove('open');
  _setPanelExpanded(false);
  panel.style.top = '';
  panel.style.right = '';
  panel.style.left = '';
  panel.style.bottom = '';
  panel.style.transform = '';
  panel.style.transition = '';
  if (backdrop) backdrop.classList.remove('open');
  if (wasOpen) {
    const trigger = document.getElementById(_panelOpenTrigger) || document.getElementById('notify-btn');
    if (trigger) { try { trigger.focus({ preventScroll: true }); } catch (e) { logWarn(logTag('Notif', '\u2190', 'Focus'), e); } }
  }
}

function _switchPanelView(view) {
  _panelView = view;
  const listView = document.getElementById('notif-view-list');
  const settingsView = document.getElementById('notif-view-settings');
  const listHeader = document.querySelector('.notif-panel-header-list');
  const settingsHeader = document.querySelector('.notif-panel-header-back');
  if (view === 'settings') {
    listView?.classList.remove('notif-view-active');
    settingsView?.classList.add('notif-view-active');
    listHeader?.classList.add('hidden');
    settingsHeader?.classList.remove('hidden');
    syncNotifSettingsUI();
    renderProviderFilters();
    renderServerInfo();
    if (!state._notifPushInited) {
      state._notifPushInited = true;
      const statusLabel = document.getElementById('push-status-label');
      if (statusLabel) setText(statusLabel, 'Checking\u2026');
      logDebug(logTag('Push', '→', 'PanelCheck', `pushInitPromise=${typeof window._pushInitPromise}`));
      (async () => {
        try {
          await window._pushInitPromise;
          logDebug(logTag('Push', '←', 'PanelCheck', `initDone pushSub=${!!window._pushSub} isPushActive=${typeof window._isPushActive}`));
          if (window._isPushActive) await window._isPushActive();
          syncNotifSettingsUI();
        } catch (e) { logError(logTag('Push', '←', 'Error', 'StatusCheck'), e); }
      })();
    }
  } else {
    settingsView?.classList.remove('notif-view-active');
    listView?.classList.add('notif-view-active');
    settingsHeader?.classList.add('hidden');
    listHeader?.classList.remove('hidden');
    renderNotifDropdownList();
  }
}

// --- Settings UI sync ---

export function syncNotifSettingsUI() {
  const s = state._notifSettings;
  // Update each toggle from state (single toggle per alert: derive on from down+up pair)
  for (const opt of _iterOptsFlat()) {
    const on = opt.alert ? _alertOn(opt.key) : (s[opt.key] !== false);
    _setToggle(`toggle-${opt.key}`, on);
  }
  // Show/hide degraded children based on parent toggle state
  const degradedChildrenEl = document.getElementById('degraded-children');
  if (degradedChildrenEl) degradedChildrenEl.classList.toggle('hidden', !_alertOn('degraded'));
  // Re-render tier pickers + show/hide tier rows based on parent child toggle state
  for (const child of (_degradedOpt?.children || [])) {
    if (!child.tier_picker) continue;
    const tierKey = child.tier_picker;
    const tierVal = s[tierKey] ?? state._notifServerConfig?.[tierKey] ?? 2;
    _renderTierPicker(`${tierKey}-tiers`, tierVal, tierKey, child.metric);
    const tierRow = document.getElementById(`${tierKey}-tier-row`);
    if (tierRow) tierRow.classList.toggle('hidden', !_alertOn(child.key));
  }
  // Show/hide inapp-options based on master toggle
  const inAppOpts = document.getElementById('notif-inapp-options');
  if (inAppOpts) inAppOpts.classList.toggle('hidden', !s.enabled);
  // Push section
  const pushOn = !!window._pushSub || state._notifyLocal;
  _setToggle('toggle-push', pushOn);
  const pushLabel = document.getElementById('push-status-label');
  if (pushLabel) {
    if (Notification.permission === 'denied') setText(pushLabel, 'Push notifications (blocked)');
    else if (window._pushSub) setText(pushLabel, 'Push notifications (server-delivered)');
    else if (state._notifyLocal) setText(pushLabel, 'Push notifications (tab-only)');
    else if (state._pushExpired) setText(pushLabel, 'Push notifications (expired - re-enable)');
    else setText(pushLabel, 'Push notifications');
  }
  const pushTestBtn = document.getElementById('push-test-btn');
  if (pushTestBtn) pushTestBtn.classList.toggle('hidden', !pushOn);
  _updateBellIcon();
}

const BELL_STATES = {
  off:     'notify-off',
  on:      'notify-on',
  active:  'notify-active',
  blocked: 'notify-blocked',
  failed:  'notify-failed',
};
const BELL_STATE_CLASSES = {
  [BELL_STATES.off]:     'text-text-muted fill-none',
  [BELL_STATES.on]:      'text-tier-accent fill-none',
  [BELL_STATES.active]:  'text-tier-accent fill-current',
  [BELL_STATES.blocked]: 'text-notif-offline fill-none',
  [BELL_STATES.failed]:  'text-notif-offline fill-none',
};
const _BELL_STATE_KEY = 'data-bell-state';

function _updateBellIcon() {
  const btn = document.getElementById('notify-btn');
  if (!btn) return;
  const bellSvg = document.getElementById('notify-icon-bell');
  const slashSvg = document.getElementById('notify-icon-slash');
  const s = state._notifSettings;
  const blocked = s.enabled && typeof Notification !== 'undefined' && Notification.permission === 'denied';
  const active = s.enabled && !blocked && (window._pushSub || state._notifyLocal);
  const failed = s.enabled && !blocked && !active && state._pushExpired;
  let st, showSlash = false, label;
  if (!s.enabled) {
    st = BELL_STATES.off; showSlash = false; label = 'Notifications off';
  } else if (blocked) {
    st = BELL_STATES.blocked; showSlash = true; label = 'Notifications blocked - enable in browser settings';
  } else if (failed) {
    st = BELL_STATES.failed; showSlash = true; label = 'Push notification expired - click to re-enable';
  } else if (active) {
    st = BELL_STATES.active; showSlash = false;
    label = window._pushSub ? 'Notifications on (push)' : 'Notifications on (tab-only)';
  } else {
    st = BELL_STATES.on; showSlash = false; label = 'Notifications on';
  }
  const prev = btn.getAttribute(_BELL_STATE_KEY);
  if (prev) { const prevCls = BELL_STATE_CLASSES[prev]; if (prevCls) prevCls.split(' ').forEach(c => btn.classList.remove(c)); }
  BELL_STATE_CLASSES[st].split(' ').forEach(c => btn.classList.add(c));
  btn.setAttribute(_BELL_STATE_KEY, st);
  const countSuffix = state._notifUnread > 0 ? `, ${state._notifUnread > 99 ? '99+' : state._notifUnread} unread` : '';
  btn.setAttribute('aria-label', label + countSuffix);
  if (bellSvg) bellSvg.setAttribute('fill', st === BELL_STATES.active ? 'currentColor' : 'none');
  if (bellSvg) bellSvg.classList.toggle('hidden', showSlash);
  if (slashSvg) slashSvg.classList.toggle('hidden', !showSlash);
}

function _setToggle(id, on) {
  const el = document.getElementById(id);
  if (el) { el.classList.toggle('on', !!on); el.setAttribute('aria-checked', String(!!on)); }
}

// --- Tier picker (segmented control: role=radiogroup) ---

function _tierLabel(metric, mTh, mHib, i) {
  if (i >= mTh.length) return '';
  const th = mTh[i];
  const prev = i > 0 ? mTh[i - 1] : null;
  if (metric === 'tps') {
    if (i === mTh.length - 1) return `<${prev}`;
    return mHib ? `≥${th}` : `<${th}`;
  }
  if (i === mTh.length - 1) return `≥${prev >= 1000 ? `${(prev/1000).toFixed(0)}s` : `${prev}ms`}`;
  const v = th >= 1000 ? `${(th/1000).toFixed(0)}s` : `${th}ms`;
  return mHib ? `≥${v}` : `<${v}`;
}

function _renderTierPicker(containerId, activeIdx, settingKey, metric, focusActive = false) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const tiers = state.colorThresholds?.tiers;
  if (!tiers?.length) return;
  const mCfg = state.colorThresholds?.[metric];
  const mTh = mCfg?.thresholds || [];
  const mHib = mCfg?.higher_is_better !== false;
  setHTML(container, tiers.map((t, i) => {
    const on = i === activeIdx;
    const range = _tierLabel(metric, mTh, mHib, i);
    const label = range ? `${esc(t.label)} (${range})` : esc(t.label);
    const cls = on
      ? `tier-seg-opt active ${TIER_TEXT[t.color] || ''} ${TIER_BG[t.color] || ''}`
      : 'tier-seg-opt';
    return `<button type="button" class="${cls}" role="radio" aria-checked="${on}" tabindex="${on ? '0' : '-1'}" data-idx="${i}">${label}</button>`;
  }).join(''));
  if (focusActive) container.querySelector(`[data-idx="${activeIdx}"]`)?.focus();
  const opts = container.querySelectorAll('.tier-seg-opt');
  opts.forEach((btn, idx) => {
    btn.addEventListener('click', () => {
      const newIdx = parseInt(btn.dataset.idx, 10);
      state._notifSettings[settingKey] = newIdx;
      _saveNotifSettings();
      _renderTierPicker(containerId, newIdx, settingKey, metric, true);
    });
    btn.addEventListener('keydown', (e) => {
      let next = -1;
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (idx + 1) % opts.length;
      else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (idx - 1 + opts.length) % opts.length;
      else if (e.key === 'Home') next = 0;
      else if (e.key === 'End') next = opts.length - 1;
      else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); btn.click(); return; }
      if (next >= 0) {
        e.preventDefault();
        const newIdx = parseInt(opts[next].dataset.idx, 10);
        state._notifSettings[settingKey] = newIdx;
        _saveNotifSettings();
        _renderTierPicker(containerId, newIdx, settingKey, metric, true);
      }
    });
  });
}

// --- Provider filters ---

function renderProviderFilters() {
  const container = document.getElementById('notif-provider-filters');
  if (!container) return;
  const providers = state.providerOrder || [];
  if (providers.length === 0) {
    setHTML(container, '<span class="text-xs text-text-faint">No providers yet</span>');
    return;
  }
  const selected = state._notifSettings.providers || [];
  setHTML(container, providers.map(p => {
    const on = selected.length === 0 || selected.includes(p);
    return `<button class="notif-chip${on ? ' on' : ''}" data-provider="${esc(p)}">${esc(p)}</button>`;
  }).join(''));
  container.querySelectorAll('.notif-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      btn.classList.toggle('on');
      const allChips = [...container.querySelectorAll('.notif-chip')];
      const onVals = allChips.filter(c => c.classList.contains('on')).map(c => c.dataset.provider);
      state._notifSettings.providers = onVals.length === providers.length ? [] : onVals;
      _saveNotifSettings();
    });
  });
}

// --- Server info ---

function renderServerInfo() {
  const el = document.getElementById('notif-server-info');
  if (!el || !state._notifServerConfig) return;
  const cfg = state._notifServerConfig;
  const parts = [];
  if (cfg.enabled) parts.push('Notifications enabled');
  if (cfg.in_app?.enabled) parts.push('In-app toasts enabled');
  parts.push('Configure in config/app.yaml');
  setHTML(el, parts.map(p => `<div>${esc(p)}</div>`).join(''));
}

// --- Settings panel builder ---

function _toggleRowHTML(opt) {
  if (!opt) return '';
  const toggleId = `toggle-${opt.key}`;
  const tipAttr = opt.help ? ` data-tip="${esc(opt.help)}"` : '';
  const isAlert = !!opt.alert;
  const on = isAlert ? _alertOn(opt.key) : (state._notifSettings[opt.key] !== false);
  const checkedCls = on ? ' on' : '';
  const ariaChecked = String(on);
  const toggleHtml = `<button type="button" id="${toggleId}" class="notif-toggle${checkedCls}" role="switch" aria-checked="${ariaChecked}" tabindex="0"${tipAttr}></button>`;
  const labelText = opt.label || opt.key;
  const descHTML = opt.desc ? `<div class="notif-setting-desc">${esc(opt.desc)}</div>` : '';
  return `<div class="notif-setting-row"><div><div class="notif-setting-label">${esc(labelText)}</div>${descHTML}</div>${toggleHtml}</div>`;
}

function _buildNotifBody() {
  const s = state._notifSettings;
  const masterOpt = _NOTIF_OPTS.find(o => o.key === 'enabled');
  const offlineOpt = _NOTIF_OPTS.find(o => o.key === 'offline');
  const providerChangedOpt = _NOTIF_OPTS.find(o => o.key === 'provider_changed');
  const modelChangedOpt = _NOTIF_OPTS.find(o => o.key === 'model_changed');
  const popupsOpt = _NOTIF_OPTS.find(o => o.key === 'popups');
  const degradedChildren = _degradedOpt?.children || [];

  // Status changes group
  const statusRows = [offlineOpt, providerChangedOpt, modelChangedOpt].map(_toggleRowHTML).join('');

  // Performance group: degraded parent + children (each may have a tier picker)
  const perfChildrenHTML = degradedChildren.map(child => {
    const row = _toggleRowHTML(child);
    const tierRow = child.tier_picker
      ? `<div id="${child.tier_picker}-tier-row" class="notif-tier-row"><span class="notif-tier-label">Alert threshold</span><div id="${child.tier_picker}-tiers" class="tier-seg" role="radiogroup" aria-label="${esc(child.label)} threshold"></div></div>`
      : '';
    return row + tierRow;
  }).join('');
  const perfRows = _toggleRowHTML(_degradedOpt) + `<div id="degraded-children" class="notif-setting-children">${perfChildrenHTML}</div>`;

  // Delivery group: popups + push toggle + test button
  const deliveryRows = _toggleRowHTML(popupsOpt) +
    `<div class="notif-push-section">
       <div class="notif-setting-row">
         <span class="notif-setting-label" id="push-status-label" aria-live="polite">Push notifications</span>
         <button type="button" id="toggle-push" class="notif-toggle" role="switch" aria-checked="false" tabindex="0"></button>
       </div>
       <button type="button" id="push-test-btn" class="notif-link-btn hidden">Send test push</button>
     </div>`;

  // Providers group
  const providersRows = '<div class="text-xs text-text-muted">All if none selected</div>' +
    '<div id="notif-provider-filters" class="flex flex-wrap gap-2"></div>';

  // Server info group
  const serverRows = '<div id="notif-server-info" class="space-y-1"></div>';

  return (
    _toggleRowHTML(masterOpt) +
    `<div id="notif-inapp-options" class="${s.enabled ? '' : 'hidden'}">` +
      collapsibleHTML({ id: 'notif-group-status', title: 'Status changes', bodyHTML: statusRows, open: true }) +
      collapsibleHTML({ id: 'notif-group-performance', title: 'Performance', bodyHTML: perfRows, open: true }) +
      collapsibleHTML({ id: 'notif-group-delivery', title: 'Delivery', bodyHTML: deliveryRows, open: true }) +
      collapsibleHTML({ id: 'notif-group-providers', title: 'Providers', bodyHTML: providersRows, open: false }) +
      collapsibleHTML({ id: 'notif-group-server', title: 'Server', bodyHTML: serverRows, open: false }) +
    '</div>'
  );
}

// Builds all settings categories - mirrors _renderGlossary() in help.js
function _buildSettingsHTML() {
  return SETTINGS_CATEGORIES.map(cat =>
    collapsibleHTML({ id: `settings-${cat.id}`, title: cat.label, bodyHTML: cat.build(), open: cat.open })
  ).join('');
}
function _wireToggleHandlers() {
  for (const opt of _iterOptsFlat()) {
    const el = document.getElementById(`toggle-${opt.key}`);
    if (!el || el.dataset.wired === '1') continue;
    el.dataset.wired = '1';
    const handler = (e) => { e.preventDefault(); _handleToggleClick(opt, el); };
    el.addEventListener('click', handler);
    el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handler(e); } });
  }
}

async function _handleToggleClick(opt, el) {
  if (el.classList.contains('loading')) return;
  const s = state._notifSettings;
  const isAlert = !!opt.alert;
  const curVal = isAlert ? _alertOn(opt.key) : (s[opt.key] !== false);
  const newVal = !curVal;
  logInfo(logTag('Notif', '\u2192', 'Setting', opt.key, newVal ? 'On' : 'Off'));

  const isMaster = !!opt.master;
  const isSlow = isMaster && !newVal;
  if (isSlow) { el.classList.add('loading'); el.setAttribute('aria-busy', 'true'); }

  const prevEnabledAt = state._notifEnabledAt;
  // Save previous state for rollback
  const prevAlert = isAlert ? [s[opt.down.key], s[opt.up.key]] : null;
  const prevVal = s[opt.key];

  // Write state (alert: write down+up in lockstep; simple: write the key)
  if (isAlert) { s[opt.down.key] = newVal; s[opt.up.key] = newVal; }
  else { s[opt.key] = newVal; }

  if (isMaster) {
    if (newVal) {
      state._notifEnabledAt = new Date().toISOString();
      localStorage.setItem('mw_notif_enabled_at', state._notifEnabledAt);
    } else {
      state._notifEnabledAt = null;
      localStorage.removeItem('mw_notif_enabled_at');
    }
  }
  _setToggle(el.id, newVal);

  try {
    if (isSlow) { await window._pushInitPromise; await window._disablePush?.(); }
    if (isMaster && newVal && opt.onFirstEnable && !localStorage.getItem('mw_notif_enabled_once')) {
      for (const k of opt.onFirstEnable) state._notifSettings[k] = true;
      localStorage.setItem('mw_notif_enabled_once', '1');
    }
    _saveNotifSettings();
    syncNotifSettingsUI();
    if (isMaster && !newVal) {
      _clearNotifHistory();
    } else if (isMaster && newVal) {
      refreshNotifHistory();
      if (Notification.permission !== 'denied' && !window._pushSub && !localStorage.getItem('mw_push_opt_out')) {
        logInfo(logTag('Push', '→', 'AutoEnable', 'master toggle on - requesting push permission'));
        window._pushInitPromise.then(() => window._requestPushPermission?.()).then(() => syncNotifSettingsUI()).catch(e => logError(logTag('Push', '←', 'Error', 'PermissionRequest'), e));
      } else {
        logDebug(logTag('Push', '→', 'AutoEnable', `skipped - perm=${Notification.permission} hasSub=${!!window._pushSub} optOut=${localStorage.getItem('mw_push_opt_out') === '1'}`));
      }
    }
  } catch (e) {
    // Roll back
    if (isAlert && prevAlert) { s[opt.down.key] = prevAlert[0]; s[opt.up.key] = prevAlert[1]; }
    else { s[opt.key] = prevVal; }
    if (isMaster) {
      state._notifEnabledAt = prevEnabledAt;
      if (prevEnabledAt) localStorage.setItem('mw_notif_enabled_at', prevEnabledAt);
      else localStorage.removeItem('mw_notif_enabled_at');
    }
    _setToggle(el.id, !newVal);
    logError(logTag('Notif', '←', 'Error', 'Toggle'), e);
  } finally {
    el.classList.remove('loading');
    el.removeAttribute('aria-busy');
  }
}

function buildSettings() {
  const body = document.getElementById('notif-settings-body');
  if (!body) return;
  setHTML(body, _buildSettingsHTML());

  // Wire all toggle handlers (master, popups, offline, degraded, degraded_tps, degraded_ttft, provider_changed, model_changed)
  _wireToggleHandlers();

  // Render tier pickers
  for (const child of (_degradedOpt?.children || [])) {
    if (!child.tier_picker) continue;
    const tierKey = child.tier_picker;
    const tierVal = state._notifSettings[tierKey] ?? state._notifServerConfig?.[tierKey] ?? 2;
    _renderTierPicker(`${tierKey}-tiers`, tierVal, tierKey, child.metric);
  }
  // Render provider filters + server info (populated into the topic groups)
  renderProviderFilters();
  renderServerInfo();

  // Wire accordion buttons (delegated + idempotent)
  body.querySelectorAll('.acc-btn').forEach(btn => {
    if (btn.dataset.accWired === '1') return;
    btn.dataset.accWired = '1';
    btn.addEventListener('click', () => toggleCollapsible(btn));
    btn.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleCollapsible(btn); } });
  });
}

// --- Server history fetch ---

export function refreshNotifHistory() {
  logDebug(logTag('Notif', '→', 'Refresh'));
  if (!state._notifSettings.enabled) return;
  _lastRefreshAt = Date.now();
  const params = new URLSearchParams();
  const cid = _getClientId();
  params.set('client_id', cid);
  if (state._notifEnabledAt) params.set('since', state._notifEnabledAt);
  const notifUrl = '/api/notifications' + (params.toString() ? '?' + params.toString() : '');
  api(notifUrl).then(cfg => {
    if (!cfg) return;
    state._notifServerConfig = cfg;
    const serverHistory = cfg.history || [];
    const readIds = new Set(JSON.parse(localStorage.getItem('mw_notif_read_ids') || '[]'));
    const serverItems = serverHistory.filter(n => {
      if (!n || !n.id) return false;
      if (readIds.has(n.id)) return false;
      try { return _notifMatchesPrefs(n); } catch (e) { logError(logTag('Notif', '←', 'Error', 'PrefsMatch'), e); return false; }
    });
    const serverIds = new Set(serverItems.map(n => n.id));
    const localItems = state._notifHistory.filter(n => n.id && !serverIds.has(n.id));
    const merged = [...serverItems, ...localItems];
    merged.sort((a, b) => Date.parse(b.timestamp || 0) - Date.parse(a.timestamp || 0));
    const grounded = merged.filter(n => _isRecoveryGrounded(n));
    const historyCap = cfg.in_app?.history_size || _DEFAULT_HISTORY_CAP;
    if (grounded.length > historyCap) grounded.length = historyCap;
    state._notifHistory = grounded;
    state._notifUnread = grounded.length;
    _syncNotifUI();
  }).catch(e => logError(logTag('API', '←', 'Error', 'Notifications'), e));
}

// --- Init ---

export function initNotifSystem() {
  logInfo(logTag('Notif', '→', 'Init'));
  _loadNotifSettings();
  if (state._notifSettings.enabled) _loadNotifHistory();
  syncWSPrefs();

  const btn = document.getElementById('notify-btn');
  const backdrop = document.getElementById('notif-backdrop');
  const markRead = document.getElementById('notif-mark-read');
  const settingsBtn = document.getElementById('notif-settings-btn');

  if (btn) {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      _toggleNotifPanel(undefined, 'notify-btn');
    });
  }

  if (settingsBtn) {
    settingsBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      _toggleNotifPanel('settings', 'notif-settings-btn');
    });
  }

  // All close buttons (one per header)
  document.querySelectorAll('.notif-close-btn').forEach(b => b.addEventListener('click', closeNotifPanel));
  if (backdrop) backdrop.addEventListener('click', closeNotifPanel);

  const panel = document.getElementById('notif-panel');
  if (panel) panel.addEventListener('click', (e) => e.stopPropagation());

  document.addEventListener('click', (e) => {
    if (panel && panel.classList.contains('open')
        && e.target.id !== 'notify-btn' && !btn?.contains(e.target)
        && e.target.id !== 'notif-settings-btn' && !settingsBtn?.contains(e.target)) {
      closeNotifPanel();
    }
  });

  initSheetDrag({ handleSelector: '.notif-drag-handle', panelId: 'notif-panel', closeFn: closeNotifPanel });

  if (markRead) {
    markRead.addEventListener('click', () => {
      for (const n of state._notifHistory) _markNotifRead(n.id);
      _clearNotifHistory();
    });
  }

  buildSettings();

  // Wire push toggle (custom - calls window._push* APIs rather than writing prefs directly)
  const pushToggle = document.getElementById('toggle-push');
  if (pushToggle) {
    const pushToggleHandler = async () => {
      if (pushToggle.classList.contains('loading')) return;
      const wasActive = !!(window._pushSub || state._notifyLocal);
      const wantActive = !wasActive;
      pushToggle.classList.add('loading');
      pushToggle.setAttribute('aria-busy', 'true');
      _setToggle('toggle-push', wantActive);
      try {
        await window._pushInitPromise;
        if (wasActive) {
          await window._disablePush?.();
        } else if (Notification.permission === 'denied') {
          _setToggle('toggle-push', false);
        } else {
          if (!window._requestPushPermission) { _setToggle('toggle-push', false); }
          else { const result = await window._requestPushPermission(); if (result === false) _setToggle('toggle-push', false); }
        }
        syncNotifSettingsUI();
      } catch (e) {
        _setToggle('toggle-push', wasActive);
        logError(logTag('Push', '←', 'Error', 'Toggle'), e);
      } finally {
        pushToggle.classList.remove('loading');
        pushToggle.removeAttribute('aria-busy');
      }
    };
    pushToggle.addEventListener('click', (e) => { e.preventDefault(); pushToggleHandler(); });
    pushToggle.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pushToggleHandler(); } });
  }

  const pushTestBtn = document.getElementById('push-test-btn');
  if (pushTestBtn) {
    pushTestBtn.addEventListener('click', async () => {
      try {
        setText(pushTestBtn, 'Sending...');
        pushTestBtn.disabled = true;
        const isActive = await window._isPushActive?.();
        if (isActive) {
          try {
            const endpoint = window._pushSub?.endpoint;
            const res = await api('/api/push/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ endpoint }) });
            if (!res || res.error) {
              setText(pushTestBtn, res?.error || 'Failed');
            } else {
              setText(pushTestBtn, 'Sent!');
            }
          } catch (e) { setText(pushTestBtn, 'Error'); logError(logTag('Push', '←', 'Error', 'TestSend'), e); }
        } else if (Notification.permission === 'granted') {
          try {
            new Notification((window.__APP_NAME__ || 'ModelWatcher') + ' test', { body: 'Notifications are working!', tag: 'mw-test' });
            setText(pushTestBtn, 'Sent! (local)');
          } catch (e) { setText(pushTestBtn, 'Failed'); logError(logTag('Push', '←', 'Error', 'LocalTest'), e); }
        } else {
          setText(pushTestBtn, 'Not subscribed');
        }
        pushTestBtn.disabled = false;
        setTimeout(() => { setText(pushTestBtn, 'Send test push'); }, _PUSH_TEST_RESET_MS);
      } catch (e) { setText(pushTestBtn, 'Error'); pushTestBtn.disabled = false; logError(logTag('Push', '←', 'Error', 'Test'), e); }
    });
  }

  refreshNotifHistory();

  _updateBellIcon();
}

// --- Push init ---

export async function initPush() {
  const btn = document.getElementById('notify-btn');
  if (!btn) { logWarn(logTag('Push', '→', 'Init', 'no notify-btn')); return; }
  const setLocal = (on) => { state._notifyLocal = on; _updateBellIcon(); _saveNotifyLocal(); };
  if (!('Notification' in window)) { logInfo(logTag('Push', '←', 'Unavailable')); document.documentElement.classList.remove('bell-fouc-on', 'bell-fouc-active'); _updateBellIcon(); return; }
  const perm = Notification.permission;
  const canPush = 'serviceWorker' in navigator && 'PushManager' in window;
  const optOut = localStorage.getItem('mw_push_opt_out') === '1';
  logInfo(logTag('Push', '→', 'Init', `perm=${perm} canPush=${canPush} optOut=${optOut} clientId=${_getClientId()}`));
  if (perm === 'denied') {
    logInfo(logTag('Push', '←', 'Denied'));
    if (state._notifSettings.enabled) _updateBellIcon();
  }
  let sub, reg, vapidKey;
  if (canPush) {
    let keyResp = await cacheGet('vapid_key');
    if (keyResp) logDebug(logTag('Push', '←', 'VAPID', 'cache hit'));
    else { keyResp = await api('/api/vapid-key'); if (keyResp) { cacheSet('vapid_key', keyResp, 86400); logDebug(logTag('Push', '←', 'VAPID', 'fetched from API')); } }
    if (keyResp?.public_key) {
      vapidKey = Uint8Array.from(atob(keyResp.public_key.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
      try {
        reg = await navigator.serviceWorker.register('/sw.js', { updateViaCache: 'none' });
        await navigator.serviceWorker.ready;
        logDebug(logTag('Push', '←', 'SW', 'registered'));
        if (!localStorage.getItem('mw_sw_cleanup_done')) {
          const oldRegs = await navigator.serviceWorker.getRegistrations();
          for (const r of oldRegs) { if (r.scope.includes('/static/') || r.scope.includes(window.__STATIC_PREFIX__ + '/')) await r.unregister(); }
          localStorage.setItem('mw_sw_cleanup_done', '1');
        }
        try { sub = await reg.pushManager.getSubscription(); } catch (e) { logError(logTag('Push', '←', 'Error', 'GetSubscription'), e); }
        logDebug(logTag('Push', '←', 'GetSub', sub ? `found ${sub.endpoint.slice(0, 40)}…` : 'null (no subscription)'));
      } catch (e) { logError(logTag('Push', '←', 'Error', 'SWRegistration'), e); reg = null; }
      if (sub) {
        let shouldPost = false;
        try {
          const v = await api(`/api/push/validate?endpoint=${encodeURIComponent(sub.endpoint)}&client_id=${encodeURIComponent(_getClientId())}`);
          if (v?.valid) {
            logDebug(logTag('Push', '←', 'Validate', 'valid - resyncing'));
            shouldPost = true;
          } else if (v?.reason === 'client_mismatch') {
            logInfo(logTag('Push', '←', 'Reclaim', 'client_id mismatch - reclaiming via resubscribe'));
            shouldPost = true;
          } else {
            logError(logTag('Push', '←', 'Stale'), new Error(v?.reason || 'not found on server'));
            state._pushExpired = true;
            const deadEndpoint = sub.endpoint;
            try { await sub.unsubscribe(); } catch (e) { logError(logTag('Push', '←', 'Error', 'Unsubscribe'), e); }
            sub = null;
            window._pushSub = null;
            if (deadEndpoint) try { await api('/api/push/subscribe', { method: 'DELETE', body: JSON.stringify({ endpoint: deadEndpoint }) }); } catch (e) { logDebug(logTag('Push', '←', 'DeleteDead'), e); }
            _updateBellIcon();
          }
        } catch (e) { logError(logTag('Push', '←', 'Error', 'Validation'), e); }
        if (shouldPost && sub) {
          const subJSON = sub.toJSON();
          try {
            await fetch('/api/push/subscribe', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...subJSON, client_id: _getClientId(), prefs: buildNotifPrefs() }) });
          } catch (e) { logDebug(logTag('Push', '←', 'Resync'), e); }
        }
      }
      window._pushSub = sub;
      if (sub) { setLocal(false); syncNotifSettingsUI(); _syncPushPrefs(); logInfo(logTag('Push', '←', 'Active')); }
    } else {
      logWarn(logTag('Push', '←', 'VAPID', 'no public_key - push disabled'));
    }
  } else {
    logInfo(logTag('Push', '←', 'NoPushAPI'));
  }

  async function _refreshSub() {
    if (!reg) return null;
    try {
      sub = await reg.pushManager.getSubscription();
      window._pushSub = sub;
    } catch (e) { logError(logTag('Push', '←', 'Error', 'Refresh'), e); }
    return sub;
  }

  if (!sub) {
    const savedLocal = localStorage.getItem('mw_notif_local');
    logDebug(logTag('Push', '←', 'NoSub', `local=${savedLocal} perm=${Notification.permission} optOut=${optOut}`));
    if (savedLocal === '1' && Notification.permission === 'granted') {
      state._notifyLocal = true;
      logInfo(logTag('Push', '←', 'Local', 'tab-only notifications active'));
      _updateBellIcon();
      syncNotifSettingsUI();
    } else if (Notification.permission === 'granted' && !optOut && canPush && reg && vapidKey) {
      logInfo(logTag('Push', '→', 'AutoResub', 'permission granted but no sub - resubscribing'));
      try {
        sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: vapidKey });
        const saveResRaw = await fetch('/api/push/subscribe', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...sub.toJSON(), client_id: _getClientId(), prefs: buildNotifPrefs() }) });
        if (saveResRaw.ok) {
          window._pushSub = sub;
          localStorage.removeItem('mw_push_opt_out');
          setLocal(false);
          syncNotifSettingsUI();
          _syncPushPrefs();
          logInfo(logTag('Push', '←', 'Active', 'auto-resubscribed'));
        } else {
          logWarn(logTag('Push', '←', 'AutoResub', `subscribe POST failed status=${saveResRaw.status}`));
          try { await sub.unsubscribe(); } catch (e) { logError(logTag('Push', '←', 'AutoResub', 'unsubscribe failed'), e); }
          sub = null;
        }
      } catch (e) { logError(logTag('Push', '←', 'Error', 'AutoResub'), e); sub = null; }
    }
  }
  logInfo(logTag('Push', '←', 'Done', `pushSub=${!!window._pushSub} notifyLocal=${!!state._notifyLocal} pushExpired=${!!state._pushExpired}`));
  document.documentElement.classList.remove('bell-fouc-on', 'bell-fouc-active');
  _updateBellIcon();
  window._requestPushPermission = async () => {
    logInfo(logTag('Push', '→', 'RequestPerm', `perm=${Notification.permission} hasSub=${!!sub} canPush=${canPush} hasReg=${!!reg} hasVapid=${!!vapidKey}`));
    if (Notification.permission === 'denied') return false;
    try {
      const perm = await Notification.requestPermission();
      logDebug(logTag('Push', '←', 'RequestPerm', `result=${perm}`));
      if (perm !== 'granted') return false;
    } catch (e) { logError(logTag('Push', '←', 'Error', 'Permission'), e); return false; }
    state._pushExpired = false;
    if (sub) { localStorage.removeItem('mw_push_opt_out'); setLocal(false); syncNotifSettingsUI(); _syncPushPrefs(); return true; }
    if (canPush && reg && vapidKey) {
      try {
        sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: vapidKey });
        const saveResRaw = await fetch('/api/push/subscribe', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...sub.toJSON(), client_id: _getClientId(), prefs: buildNotifPrefs() }) });
        let saveRes = null;
        try { if (saveResRaw.ok) saveRes = await saveResRaw.json(); } catch (e) { logDebug(logTag('Push', '\u2190', 'Parse', 'subscribe-response'), e); }
        if (!saveRes || saveRes.error) {
          if (saveResRaw.status === 409) logWarn(logTag('Push', '←', 'Conflict', 'Subscribe', 'Endpoint already registered'));
          await sub.unsubscribe(); sub = null;
        } else {
          state._pushExpired = false;
          setLocal(false);
          window._pushSub = sub;
          localStorage.removeItem('mw_push_opt_out');
          syncNotifSettingsUI();
          return true;
        }
      } catch (e) { logError(logTag('Push', '←', 'Error', 'Subscribe'), e); sub = null; }
    }
    setLocal(true);
    syncNotifSettingsUI();
    return true;
  };
  window._disablePush = async () => {
    logInfo(logTag('Push', '→', 'Disable', `hasSub=${!!sub} hasPushSub=${!!window._pushSub}`));
    localStorage.setItem('mw_push_opt_out', '1');
    if (!sub && !window._pushSub) {
      setLocal(false);
      syncNotifSettingsUI();
      return;
    }
    let delEndpoint = null;
    try {
    if (!sub) await _refreshSub();
    if (sub) {
      delEndpoint = sub.endpoint;
      await sub.unsubscribe();
      sub = null;
      window._pushSub = null;
    }
    // Delete ALL server-side subscriptions for this client (not just current endpoint)
    const cid = _getClientId();
    const delBody = { client_id: cid };
    if (delEndpoint) delBody.endpoint = delEndpoint;
    if (delBody.client_id || delBody.endpoint) {
      logInfo(logTag('Push', '→', 'Unsubscribe', cid ? `client=${cid}` : `endpoint`));
      const res = await api('/api/push/subscribe', { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(delBody) });
      if (res) logInfo(logTag('Push', '←', 'Unsubscribe', `deleted=${res.deleted}`));
      else logWarn(logTag('Push', '←', 'Warn', 'UnsubscribeFailed'));
    }
    setLocal(false);
    syncNotifSettingsUI();
    } catch (e) { logError(logTag('Push', '←', 'Error', 'Disable'), e); }
  };
  window._isPushActive = async () => {
    try {
    if (Notification.permission !== 'granted') { logDebug(logTag('Push', '←', 'ActiveCheck', `perm=${Notification.permission}`)); return false; }
    if (!canPush || !reg) { logDebug(logTag('Push', '←', 'ActiveCheck', `canPush=${canPush} reg=${!!reg}`)); return false; }
    if (!sub) await _refreshSub();
    if (!sub) { logDebug(logTag('Push', '←', 'ActiveCheck', 'no sub after refresh')); return false; }
    try {
      const v = await api(`/api/push/validate?endpoint=${encodeURIComponent(sub.endpoint)}&client_id=${encodeURIComponent(_getClientId())}`);
      if (!v || !v.valid) {
        if (v?.reason === 'client_mismatch') {
          logInfo(logTag('Push', '←', 'Reclaim', 'activeCheck client_id mismatch - reclaiming'));
          const subJSON = sub.toJSON();
          try {
            await fetch('/api/push/subscribe', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...subJSON, client_id: _getClientId(), prefs: buildNotifPrefs() }) });
          } catch (e) { logDebug(logTag('Push', '←', 'Resync'), e); }
          return true;
        }
        state._pushExpired = true;
        const deadEndpoint = sub.endpoint;
        try { await sub.unsubscribe(); } catch (e) { logError(logTag('Push', '←', 'Error', 'Unsubscribe'), e); }
        sub = null; window._pushSub = null;
        if (deadEndpoint) try { await api('/api/push/subscribe', { method: 'DELETE', body: JSON.stringify({ endpoint: deadEndpoint }) }); } catch (e) { logDebug(logTag('Push', '←', 'DeleteDead'), e); }
        syncNotifSettingsUI();
        _updateBellIcon();
        return false;
      }
      const subJSON = sub.toJSON();
      try {
        await fetch('/api/push/subscribe', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...subJSON, client_id: _getClientId(), prefs: buildNotifPrefs() }) });
      } catch (e) { logDebug(logTag('Push', '←', 'Resync'), e); }
    } catch (e) { logError(logTag('Push', '←', 'Error', 'Validation'), e); return false; }
    return true;
    } catch (e) { logError(logTag('Push', '←', 'Error', 'ActiveCheck'), e); return false; }
  };
}
