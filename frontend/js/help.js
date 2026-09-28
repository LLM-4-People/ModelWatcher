// Help/reference panel with Legend + Glossary tabs. Uses callback pattern
// (setCloseNotifPanel) instead of importing notifications.js to avoid a cycle.
import { esc, initSheetDrag, isPhone, collapsibleHTML, setHTML, dotHTML, pushLayer, trapFocus } from './utils.js';
import { tierScaleHTML, FRESHNESS_TIERS, TIER_DOT_BG, STATUS_DOT, helpText, isHelpKey, statusLabel, metricLabel, chartViewLabel, criticalMarkHTML, TESTING_LABEL } from './format.js';
import { state, _NOTIF_OPTS } from './state.js';
import { CONN_STATES } from './conn.js';

let _closeNotifPanelFn = null;
export function setCloseNotifPanel(fn) { _closeNotifPanelFn = fn; }

// ── Glossary category definitions (single source of truth for grouping) ────

const HELP_CATEGORIES = [
  { id: 'status', label: 'Status & Health', keys: ['models', 'online', 'testing', 'error', 'degraded', 'unknown', 'degraded_critical_tier', 'degraded_stream_error', 'degraded_insufficient_output'] },
  { id: 'core', label: 'Core Metrics', keys: ['ttft', 'tps', 'uptime', 'p99Itl', 'itlReliable', 'scores'] },
  { id: 'stream', label: 'Stream Quality', keys: ['stall', 'consistency', 'batching', 'medianItl', 'avgItl', 'maxItl', 'hiccups', 'effectiveItl', 'itlTailRatio'] },
  { id: 'output', label: 'Output & Timing', keys: ['ok', 'testType', 'completionTokens', 'chunksObserved', 'reasoning', 'maxChunk', 'finishReason', 'chunkCv', 'tpot', 'totalLatency', 'thinkingDuration', 'errorMsg', 'retry'] },
  { id: 'network', label: 'Network & Stalls', keys: ['networkJitter', 'burstArrivals', 'burstArrival', 'frameBatch', 'shrinkage', 'stallFirst', 'stallLast', 'stallClusters', 'stallRatio'] },
  { id: 'charts', label: 'Charts', keys: ['chart_speed', 'chart_consistency', 'chart_scores', 'chart_health'] },
  { id: 'connection', label: 'Connection', keys: CONN_STATES.map(s => `ws_${s}`) },
  { id: 'notifications', label: 'Notifications', notifOpts: true },
];

// Glossary entries named after a metric, a status or a chart view take that label from the
// backend (METRIC_LABELS, STATUS_LABELS, CHART_VIEW_LABELS); the rest are named here
const _GLOSSARY_METRIC = {
  ttft: 'ttft', tps: 'tps', uptime: 'uptime', stall: 'stall_count', consistency: 'consistency_score',
  p99Itl: 'raw_p99_itl_ms', medianItl: 'raw_median_itl_ms', maxItl: 'raw_max_itl_ms', avgItl: 'raw_avg_itl_ms',
  itlTailRatio: 'effective_itl_tail_ratio', batching: 'chunk_token_ratio', chunkCv: 'chunk_token_cv',
  tpot: 'tpot_ms', networkJitter: 'network_jitter_ms', burstArrival: 'burst_arrival_pct',
};

const HELP_LABELS = {
  models: 'Models', testing: TESTING_LABEL,
  degraded_critical_tier: 'Critical tier', degraded_stream_error: 'Stream error',
  degraded_insufficient_output: 'Insufficient output',
  scores: 'Scores', itlReliable: 'ITL reliable', hiccups: 'Hiccups', effectiveItl: 'Effective ITL',
  ok: 'OK column', testType: 'Test type', reasoning: 'Thinking tokens',
  completionTokens: 'Output tokens', chunksObserved: 'Chunks observed', maxChunk: 'Max chunk',
  finishReason: 'Finish reason', totalLatency: 'Total latency', thinkingDuration: 'Thinking duration',
  errorMsg: 'Error message', retry: 'Retry attempt',
  stallFirst: 'First stall', stallLast: 'Last stall', stallClusters: 'Stall clusters', stallRatio: 'Stall ratio',
  burstArrivals: 'Burst arrivals', frameBatch: 'Frame batch', shrinkage: 'Shrinkage',
  ws_connecting: 'Connecting', ws_connected: 'Connected', ws_restarting: 'Restarting',
  ws_busy: 'Server busy', ws_rejected: 'Origin rejected', ws_disconnected: 'Disconnected', ws_down: 'Server down',
  ws_unconfigured: 'Page not configured',
};

function _glossaryLabel(key) {
  if (_GLOSSARY_METRIC[key]) return metricLabel(_GLOSSARY_METRIC[key]);
  if (state.statusValues.includes(key)) return statusLabel(key);
  if (key.startsWith('chart_')) return chartViewLabel(key.slice('chart_'.length));
  return HELP_LABELS[key] || key;
}

// ── Legend item HTML builders ───────────────────────────────────────────────

// labelHTML is markup the caller built from escaped text (the Critical marker needs a span)
function _legendDotItem(dotCls, labelHTML, count) {
  const countHTML = count != null ? `<span class="legend-count">${count}</span>` : '';
  return `<span class="flex items-center gap-1.5 text-[10px]">${dotHTML(dotCls)}<span class="text-text-muted">${labelHTML}</span>${countHTML}</span>`;
}

// Every status with its model count (state._counts, kept by recalcCounts), then models testing
// now (finding F51: the tip promised counts the legend never showed)
function _legendStatusHTML() {
  const counts = state._counts;
  const items = state.statusValues.map(s => _legendDotItem(STATUS_DOT[s], esc(statusLabel(s)), counts[s]));
  items.push(_legendDotItem(STATUS_DOT.testing, esc(_glossaryLabel('testing')), counts.testing));
  return items.join('');
}

function _legendPerformanceHTML() {
  const tiers = state.colorThresholds?.tiers;
  if (!tiers) return '';
  const criticalIdx = tiers.length - 1;
  return tiers.map((t, i) => {
    const label = i === criticalIdx ? criticalMarkHTML(esc(t.label)) : esc(t.label);
    return _legendDotItem(TIER_DOT_BG[t.color] || 'bg-text-faint', label);
  }).join('');
}

function _legendFreshnessHTML() {
  return FRESHNESS_TIERS.map(t => _legendDotItem(t.dot, esc(t.label))).join('');
}

// ── Glossary rendering (dynamic, reads the Help texts) ──────────────────────

function _glossarySectionHTML(cat) {
  const items = cat.notifOpts
    ? _NOTIF_OPTS.map(o => ({ label: o.label, html: esc(o.help) }))
    : cat.keys.filter(k => isHelpKey(k) && helpText(k)).map(k => ({ label: _glossaryLabel(k), html: helpText(k) + tierScaleHTML(k) }));
  if (!items.length) return '';
  const bodyHTML = items.map(it => `<div class="help-item"><span class="help-item-label">${esc(it.label)}</span><span class="help-item-desc">${it.html}</span></div>`).join('');
  return collapsibleHTML({ id: cat.id, title: cat.label, bodyHTML, open: _expandedSection === cat.id });
}

// ── Panel state ────────────────────────────────────────────────────────────

let _open = false;
let _activeTab = 'legend';
let _expandedSection = null; // only one section open at a time
let _popLayer = null;
let _releaseFocus = null;

// ── Panel open / close ─────────────────────────────────────────────────────

// Closed, the drawer is inert: off screen only by transform, it used to keep its controls in
// the tab order and the accessibility tree (finding F73)
function openHelpPanel() {
  if (_open) return;
  const panel = document.getElementById('help-panel');
  const backdrop = document.getElementById('help-backdrop');
  if (!panel) return;
  _open = true;
  if (_closeNotifPanelFn) _closeNotifPanelFn();
  _renderTab();
  panel.inert = false;
  panel.classList.add('open');
  if (backdrop && isPhone()) backdrop.classList.add('open');
  _popLayer = pushLayer(closeHelpPanel);
  _releaseFocus = trapFocus(panel, { focus: isPhone() ? panel.querySelector('.help-tab.active') : panel.querySelector('.help-close-btn'), keep: [backdrop] });
}

export function closeHelpPanel() {
  if (!_open) return;
  _open = false;
  const panel = document.getElementById('help-panel');
  const backdrop = document.getElementById('help-backdrop');
  if (panel) { panel.classList.remove('open'); panel.inert = true; }
  if (backdrop) backdrop.classList.remove('open');
  _popLayer?.();
  _popLayer = null;
  _releaseFocus?.();
  _releaseFocus = null;
}

function toggleHelpPanel() {
  _open ? closeHelpPanel() : openHelpPanel();
}

export function isHelpPanelOpen() { return _open; }

// ── Tab switching ──────────────────────────────────────────────────────────

function _switchTab(tab) {
  _activeTab = tab;
  _expandedSection = null; // collapse all when switching tabs
  const panel = document.getElementById('help-panel');
  if (!panel) return;
  panel.querySelectorAll('.help-tab').forEach(b => {
    const active = b.dataset.tab === tab;
    b.classList.toggle('active', active);
    b.setAttribute('aria-selected', String(active));
  });
  panel.querySelectorAll('.help-tab-content').forEach(c => c.classList.toggle('active', c.id === `help-tab-${tab}`));
  if (tab === 'glossary') _renderGlossary();
}

// ── Content rendering ──────────────────────────────────────────────────────

export function renderHelpLegends() {
  const el = document.getElementById('help-legend-content');
  if (!el) return;
  const sec = (sectionId, title, html, tip) => collapsibleHTML({
    id: sectionId, title, bodyHTML: `<div class="flex flex-col gap-1 px-4 py-2 legend-inner">${html}</div>`,
    open: _expandedSection === sectionId, tipKey: tip,
  });
  el.innerHTML =
    sec('legend-status', 'Status', _legendStatusHTML(), 'statusLegend') +
    sec('legend-performance', 'Performance', _legendPerformanceHTML(), 'performanceLegend') +
    sec('legend-freshness', 'Freshness', _legendFreshnessHTML(), 'freshnessLegend');
}

export function updateStatusLegend() {
  const el = document.getElementById('help-legend-content');
  if (!el) return;
  const statusSection = el.querySelector('[data-section="legend-status"]');
  if (!statusSection) return;
  const inner = statusSection.querySelector('.acc-body .legend-inner');
  if (inner) setHTML(inner, _legendStatusHTML());
}

function _renderGlossary() {
  const el = document.getElementById('help-glossary-content');
  if (!el) return;
  el.innerHTML = HELP_CATEGORIES.map(cat => _glossarySectionHTML(cat)).join('');
}

function _renderTab() {
  if (_activeTab === 'legend') renderHelpLegends();
  else _renderGlossary();
}

// ── Section accordion ─────────────────────────────────────────────────────

function _toggleSection(sectionId) {
  _expandedSection = _expandedSection === sectionId ? null : sectionId;
  const panel = document.getElementById('help-panel');
  if (!panel) return;
  panel.querySelectorAll('.acc-section').forEach(sec => {
    const id = sec.dataset.section;
    if (!id) return;
    const isOpen = id === _expandedSection;
    const btn = sec.querySelector('.acc-btn');
    const body = sec.querySelector('.acc-body');
    if (btn) { btn.dataset.state = isOpen ? 'open' : 'closed'; btn.setAttribute('aria-expanded', String(isOpen)); }
    if (body) body.dataset.state = isOpen ? 'open' : 'closed';
  });
}

// ── Event wiring ───────────────────────────────────────────────────────────

export function initHelpPanel() {
  const panel = document.getElementById('help-panel');
  const backdrop = document.getElementById('help-backdrop');
  const btn = document.getElementById('help-btn');

  if (btn) btn.addEventListener('click', toggleHelpPanel);
  if (backdrop) backdrop.addEventListener('click', closeHelpPanel);

  if (panel) {
    panel.querySelectorAll('.help-tab').forEach(b => {
      b.addEventListener('click', () => _switchTab(b.dataset.tab));
    });
    panel.addEventListener('click', e => {
      const secBtn = e.target.closest('.acc-btn');
      if (secBtn) {
        const sec = secBtn.closest('.acc-section');
        if (sec) _toggleSection(sec.dataset.section);
        return;
      }
      if (e.target.closest('.help-close-btn')) { closeHelpPanel(); return; }
    });
    panel.addEventListener('click', e => e.stopPropagation());
  }

  document.addEventListener('click', e => {
    if (!_open) return;
    if (e.target.closest('#help-panel')) return;
    if (e.target.closest('#help-btn')) return;
    closeHelpPanel();
  });

  initSheetDrag({ handleSelector: '.help-drag-handle', panelId: 'help-panel', closeFn: closeHelpPanel, snapMs: 200 });
  renderHelpLegends();
}
