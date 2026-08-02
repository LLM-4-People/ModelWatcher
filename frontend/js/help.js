// Help/reference panel with Legend + Glossary tabs. Uses callback pattern
// (setCloseNotifPanel) instead of importing notifications.js to avoid a cycle.
import { HELP, esc, initSheetDrag, BP_SM, collapsibleHTML, setHTML, dotHTML } from './utils.js';
import { tierScaleHTML, FRESHNESS_TIERS, TIER_DOT_BG, STATUS_DOT } from './format.js';
import { state, _NOTIF_OPTS } from './state.js';

let _closeNotifPanelFn = null;
export function setCloseNotifPanel(fn) { _closeNotifPanelFn = fn; }

// ── Glossary category definitions (single source of truth for grouping) ────

const HELP_CATEGORIES = [
  { id: 'status', label: 'Status & Health', keys: ['models', 'online', 'testing', 'errors', 'offline', 'degraded', 'degraded_critical_tier', 'degraded_stream_error', 'degraded_insufficient_output'] },
  { id: 'core', label: 'Core Metrics', keys: ['ttft', 'tps', 'uptime', 'p99Itl', 'itlReliable', 'scores'] },
  { id: 'stream', label: 'Stream Quality', keys: ['stall', 'consistency', 'batching', 'medianItl', 'avgItl', 'maxItl', 'hiccups', 'effectiveItl', 'itlTailRatio'] },
  { id: 'output', label: 'Output & Timing', keys: ['ok', 'testType', 'completionTokens', 'chunksObserved', 'reasoning', 'maxChunk', 'finishReason', 'chunkCv', 'tpot', 'totalLatency', 'thinkingDuration', 'errorMsg', 'retry'] },
  { id: 'network', label: 'Network & Stalls', keys: ['networkJitter', 'burstArrivals', 'burstArrival', 'frameBatch', 'shrinkage', 'stallFirst', 'stallLast', 'stallClusters', 'stallRatio'] },
  { id: 'charts', label: 'Charts', keys: ['chartSpeed', 'chartConsistency', 'chartScores', 'chartHealth'] },
  { id: 'connection', label: 'Connection', keys: ['ws_connected', 'ws_disconnected', 'ws_connecting', 'ws_error', 'ws_restarting', 'ws_down'] },
  { id: 'notifications', label: 'Notifications', notifOpts: true },
];

const HELP_LABELS = {
  models: 'Models', online: 'Online', testing: 'Testing', errors: 'Errors',
  offline: 'Offline', degraded: 'Degraded',
  degraded_critical_tier: 'Critical tier', degraded_stream_error: 'Stream error',
  degraded_insufficient_output: 'Insufficient output',
  scores: 'Scores', ttft: 'TTFT', tps: 'TPS', uptime: 'Uptime', stall: 'Stalls', consistency: 'Consistency',
  p99Itl: 'P99 ITL (raw)', medianItl: 'Med ITL (raw)', maxItl: 'Max ITL (raw)', itlReliable: 'ITL reliable',
  avgItl: 'Avg ITL (raw)', hiccups: 'Hiccups', effectiveItl: 'Effective ITL',
  itlTailRatio: 'Tail ratio (eff.)', batching: 'Batching',
  ok: 'OK column', testType: 'Test type', reasoning: 'Thinking tokens',
  completionTokens: 'Output tokens', chunksObserved: 'Chunks observed', maxChunk: 'Max chunk',
  finishReason: 'Finish reason', chunkCv: 'Chunk CV',
  tpot: 'TPOT', totalLatency: 'Total latency', thinkingDuration: 'Thinking duration',
  errorMsg: 'Error message', retry: 'Retry attempt',
  stallFirst: 'First stall', stallLast: 'Last stall', stallClusters: 'Stall clusters', stallRatio: 'Stall ratio',
  networkJitter: 'Net jitter', burstArrivals: 'Burst arrivals', burstArrival: 'Burst %',
  frameBatch: 'Frame batch', shrinkage: 'Shrinkage',
  ws_connected: 'Connected', ws_disconnected: 'Disconnected',
  ws_connecting: 'Connecting', ws_error: 'Connection error',
  ws_restarting: 'Restarting', ws_down: 'Server down',
  chartSpeed: 'Speed chart', chartConsistency: 'Consistency chart',
  chartScores: 'Scores chart', chartHealth: 'Health chart',
};

// ── Legend definitions (used by renderHelpLegends) ──────────────────────────

const _STATUS_ITEMS = [
  { status: 'online', label: 'Online' },
  { status: 'degraded', label: 'Degraded' },
  { status: 'error', label: 'Errors' },
  { status: 'testing', label: 'Testing' },
];

// ── Legend item HTML builders ───────────────────────────────────────────────

function _legendDotItem(dotCls, label, count) {
  const countHTML = count != null ? `<span class="text-text-faint">${count}</span>` : '';
  return `<span class="flex items-center gap-1.5 text-[10px]">${dotHTML(dotCls)}<span class="text-text-muted">${esc(label)}</span>${countHTML}</span>`;
}

function _legendStatusHTML() {
  return _STATUS_ITEMS.map(s => _legendDotItem(STATUS_DOT[s.status], s.label)).join('');
}

function _legendPerformanceHTML() {
  const tiers = state.colorThresholds?.tiers;
  if (!tiers) return '';
  const criticalIdx = tiers.length - 1;
  return tiers.map((t, i) => {
    const label = i === criticalIdx ? `<span class="underline">${esc(t.label)}</span>` : esc(t.label);
    return _legendDotItem(TIER_DOT_BG[t.color] || 'bg-text-faint', label);
  }).join('');
}

function _legendFreshnessHTML() {
  return FRESHNESS_TIERS.map(t => _legendDotItem(t.dot, t.label)).join('');
}

// ── Glossary rendering (dynamic, reads from hELP) ──────────────────────────

function _glossarySectionHTML(cat) {
  const items = cat.notifOpts
    ? _NOTIF_OPTS.map(o => ({ label: o.label, html: esc(o.help) }))
    : cat.keys.filter(k => HELP[k] != null).map(k => {
        const label = HELP_LABELS[k] || k;
        const html = HELP[k] + (tierScaleHTML(k) || '');
        return { label, html };
      });
  if (!items.length) return '';
  const bodyHTML = items.map(it => `<div class="help-item"><span class="help-item-label">${esc(it.label)}</span><span class="help-item-desc">${it.html}</span></div>`).join('');
  return collapsibleHTML({ id: cat.id, title: cat.label, bodyHTML, open: _expandedSection === cat.id });
}

// ── Panel state ────────────────────────────────────────────────────────────

let _open = false;
let _activeTab = 'legend';
let _expandedSection = null; // only one section open at a time

// ── Panel open / close ─────────────────────────────────────────────────────

function openHelpPanel() {
  if (_open) return;
  _open = true;
  const panel = document.getElementById('help-panel');
  const backdrop = document.getElementById('help-backdrop');
  if (!panel) return;
  if (_closeNotifPanelFn) _closeNotifPanelFn();
  _renderTab();
  panel.classList.add('open');
  if (backdrop && window.innerWidth < BP_SM) backdrop.classList.add('open');
  document.body.classList.add('overflow-hidden');
  const closeBtn = panel.querySelector('.help-close-btn');
  if (closeBtn) closeBtn.focus();
}

export function closeHelpPanel() {
  if (!_open) return;
  _open = false;
  const panel = document.getElementById('help-panel');
  const backdrop = document.getElementById('help-backdrop');
  if (panel) panel.classList.remove('open');
  if (backdrop) backdrop.classList.remove('open');
  document.body.classList.remove('overflow-hidden');
  const btn = document.getElementById('help-btn');
  if (btn) btn.focus();
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
  panel.querySelectorAll('.help-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
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
