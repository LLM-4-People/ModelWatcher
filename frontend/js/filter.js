// Filter bar: search, status, score ranges, specs, and capabilities. Uses CSS
// hide/show (preserves chart instances) rather than DOM rebuild. Section
// visibility is derived from the data model, not DOM presence, so collapsed
// providers (skeleton placeholders) are handled correctly.
import { state, LS } from './state.js';
import { slug, esc, logError, logInfo, logTag, dotHTML, setHTML, setText, pushLayer, openLayerCount } from './utils.js';
import { refreshVisibleCharts } from './chart.js';
import { STATUS_DOT, TIER_TEXT, TIER_BG, statusLabel, scoreTierIdx, tierRangeLabels, fmtContext } from './format.js';

// ── Filter state ──────────────────────────────────────────────────────────

const filter = {
  search: '',             // lowercase search query
  status: 'all',          // 'all' | a status value (backend STATUS_VALUES) | 'archived'
  consistencyRange: 'all', // 'all' | score tier index as a string ('0' = best tier)
  speedRange: 'all',
  reliabilityRange: 'all',
  context: 'all',         // 'all' | a CONTEXT_DEFS key
  paramSize: 'all',       // 'all' | a PARAM_DEFS key
  caps: new Set(),        // empty = no filter; entries are capability keys
};

const SEARCH_DEBOUNCE_MS = 150;
const _ALL = { key: 'all', label: 'All' };
const _ARCHIVED = 'archived';

// Filter options, each defined once: the buttons, the chips and the predicates all read these.
// Statuses and their labels come from the backend (STATUS_VALUES/STATUS_LABELS, finding F77);
// Offline matches the error status only, untested models have their own option.
function statusDefs() {
  return [
    { ..._ALL, dot: 'bg-text-faint' },
    ...state.statusValues.map(s => ({ key: s, label: statusLabel(s), dot: STATUS_DOT[s] })),
    { key: _ARCHIVED, label: 'Archived', dot: 'bg-surface-400' },
  ];
}

// Score ranges are the color_thresholds.scores tiers (finding F81): an option matches exactly the
// scores the cards colour with that tier
function scoreDefs() {
  const tiers = state.colorThresholds.tiers || [];
  return [_ALL, ...tierRangeLabels('scores').map((label, i) => ({ key: String(i), label, tierKey: tiers[i]?.color }))];
}

const SCORE_METRICS = [
  { key: 'consistencyRange', metric: 'consistency_score', scoreKey: 'consistency' },
  { key: 'speedRange',       metric: 'speed_score',       scoreKey: 'speed' },
  { key: 'reliabilityRange', metric: 'reliability',       scoreKey: 'reliability' },
];
const _scoreMetricLabel = m => state.metricLabels[m.metric] ?? '';
let _activeScoreTab = 0; // index into SCORE_METRICS

// Spec ranges: [min, max) bounds in one place, labels derived from them (finding F76; the
// options used to be typed in index.html too, and 1M context matched no option)
const _range = (min, max, fmt, unit = '') => min == null ? `<${fmt(max)}${unit}` : max == null ? `\u2265${fmt(min)}${unit}` : `${fmt(min)}-${fmt(max)}${unit}`;
const CONTEXT_DEFS = [
  { key: '0-64k', max: 64_000 },
  { key: '64k-262k', min: 64_000, max: 262_000 },
  { key: '262k-1m', min: 262_000, max: 1_000_000 },
  { key: 'gte1m', min: 1_000_000 },
].map(d => ({ ...d, label: _range(d.min, d.max, fmtContext) }));
const PARAM_DEFS = [
  { key: 'lt70', max: 70 },
  { key: '70-235', min: 70, max: 235 },
  { key: '235-500', min: 235, max: 500 },
  { key: 'gt500', min: 500 },
].map(d => ({ ...d, label: _range(d.min, d.max, String, 'B') }));
const _inRange = (def, v) => (def.min == null || v >= def.min) && (def.max == null || v < def.max);
const SPEC_GROUPS = [
  { key: 'context', container: 'filter-ctx', attr: 'ctx', defs: CONTEXT_DEFS, chip: 'Ctx', clear: 'context' },
  { key: 'paramSize', container: 'filter-param', attr: 'param', defs: PARAM_DEFS, chip: 'Params', clear: 'paramSize' },
];

// Capabilities are the backend's list (backend/state.py CAPABILITIES)
const capDefs = () => state.capabilities;

// ── Search haystacks (precomputed lowercase strings for instant search) ────

let _haystacks = null;
let _haystackCount = -1;

function _buildHaystacks() {
  _haystacks = {};
  for (const e of state.models) {
    _haystacks[e.id] = `${e.name} ${e.provider} ${e.model_id}`.toLowerCase();
  }
  _haystackCount = state.models.length;
}

function _haystack(entryId) {
  if (_haystacks === null || _haystackCount !== state.models.length) _buildHaystacks();
  return _haystacks[entryId];
}

// ── Predicates (pure, composable - dRY) ───────────────────────────────────

function _matchesSearch(entry) {
  if (!filter.search) return true;
  const h = _haystack(entry.id);
  if (!h) return true; // unknown model → show (better visible than hidden)
  return filter.search.split(/\s+/).filter(Boolean).every(t => h.includes(t));
}

function _matchesStatus(entry, metrics) {
  if (filter.status === 'all') return true;
  if (filter.status === _ARCHIVED) return !!entry.archived;
  // Non-archived statuses: exclude archived models
  if (entry.archived) return false;
  return (metrics?.status || 'unknown') === filter.status;
}

function _matchesScore(metrics) {
  const sc = metrics?.scores;
  for (const m of SCORE_METRICS) {
    const rangeKey = filter[m.key];
    if (rangeKey === 'all') continue;
    const v = sc?.[m.scoreKey];
    if (v == null || String(scoreTierIdx(v)) !== rangeKey) return false;
  }
  return true;
}

function _parseParamB(s) {
  if (!s) return null;
  const m = String(s).match(/([\d.]+)\s*([bBmMkKtT]?)/);
  if (!m) return null;
  let n = parseFloat(m[1]);
  const u = m[2].toLowerCase();
  if (u === 't') n *= 1000;       // trillions → billions
  else if (u === 'm') n /= 1000;  // millions → billions
  else if (u === 'k') n /= 1e6;   // thousands → billions
  return n; // in billions
}

const _SPEC_VALUE = { context: e => e.context_window, paramSize: e => _parseParamB(e.param_count) };

function _matchesSpecs(entry) {
  for (const g of SPEC_GROUPS) {
    if (filter[g.key] === 'all') continue;
    const def = g.defs.find(d => d.key === filter[g.key]);
    const v = _SPEC_VALUE[g.key](entry);
    if (!def || v == null || !_inRange(def, v)) return false;
  }
  for (const cap of filter.caps) {
    if (!entry[cap]) return false;
  }
  return true;
}

function _entryMatches(entry) {
  const m = state.metrics[entry.id];
  return _matchesSearch(entry)
    && _matchesStatus(entry, m)
    && _matchesScore(m)
    && _matchesSpecs(entry);
}

// Whether a model passes the active filter: provider header counts use it, so they count the
// cards on screen (finding F69)
export function entryVisible(entry) { return _entryMatches(entry); }

// ── Public: is any filter active? ─────────────────────────────────────────

export function filterActive() {
  return filter.search !== ''
    || filter.status !== 'all'
    || filter.consistencyRange !== 'all'
    || filter.speedRange !== 'all'
    || filter.reliabilityRange !== 'all'
    || filter.context !== 'all'
    || filter.paramSize !== 'all'
    || filter.caps.size > 0;
}

// ── Core: apply filter to the DOM (CSS hide/show - preserves charts) ─────

// Track which provider sections had cards change from hidden→visible,
// so we only trigger chart init when filtering actually reveals cards
// (not on initial render where lazy loading handles it).
const _newlyVisibleProviders = new Set();
const _filterListeners = [];

// Called after every apply, with the per-provider match lists (dom.js redraws the header counts)
export function onFilterApplied(fn) { _filterListeners.push(fn); }

export function applyFilter() {
  const bar = document.getElementById('filter-bar');
  if (!bar || bar.hidden) return; // not initialized yet

  let visible = 0;
  const total = state.models.length;
  const sectionVisible = {};

  _newlyVisibleProviders.clear();

  // Section visibility and the visible-count are derived from the data model,
  // not from whether a card happens to be in the DOM. Collapsed providers defer
  // their cards (skeleton placeholders), so a DOM-only loop would skip their
  // entries entirely - hiding their sections even when the filter is cleared.
  for (const entry of state.models) {
    const match = _entryMatches(entry);
    if (match) {
      visible++;
      sectionVisible[entry.provider] = true;
    }
    const card = document.getElementById('card-' + slug(entry.id));
    if (!card) continue; // deferred/collapsed provider - section visibility already set above
    const wasHidden = card.hidden;
    card.hidden = !match;
    if (match && wasHidden) _newlyVisibleProviders.add(entry.provider);
  }

  for (const provider of state.providerOrder) {
    const section = document.getElementById('section-' + slug(provider));
    if (section) section.hidden = !sectionVisible[provider];
  }

  // After visibility changes, trigger chart init for cards now near viewport.
  // Uses requestAnimationFrame internally so the browser can lay out newly-
  // unhidden cards before we read canvas dimensions. Only inits charts that
  // are actually near the viewport (not all pending charts in the container).
  if (_newlyVisibleProviders.size) refreshVisibleCharts();

  _updateCount(visible, total);
  _updateChips();
  _updateEmptyState(visible);
  _updateDisabledOptions();
  for (const fn of _filterListeners) fn();
}

// ── Persistence ───────────────────────────────────────────────────────────

function _save() {
  try {
    localStorage.setItem(LS.FILTERS, JSON.stringify({
      search: filter.search,
      status: filter.status,
      consistencyRange: filter.consistencyRange,
      speedRange: filter.speedRange,
      reliabilityRange: filter.reliabilityRange,
      context: filter.context,
      paramSize: filter.paramSize,
      caps: [...filter.caps],
    }));
  } catch (e) { logError(logTag('Filter', 'Err', 'Save'), e); }
}

// A stored value that no option has any more (a renamed key, a retired status) reads as 'all'
const _known = (defs, v) => (defs.some(d => d.key === v) ? v : 'all');

function _load() {
  try {
    const raw = localStorage.getItem(LS.FILTERS);
    if (!raw) return;
    const s = JSON.parse(raw);
    filter.search = typeof s.search === 'string' ? s.search : '';
    filter.status = _known(statusDefs(), s.status);
    for (const m of SCORE_METRICS) filter[m.key] = _known(scoreDefs(), s[m.key]);
    for (const g of SPEC_GROUPS) filter[g.key] = _known(g.defs, s[g.key]);
    const caps = new Set(capDefs().map(c => c.key));
    filter.caps = new Set((Array.isArray(s.caps) ? s.caps : []).filter(c => caps.has(c)));
  } catch (e) { logError(logTag('Filter', 'Err', 'Load'), e); }
}

// ── Render the options from their definitions (DRY: classes from shared maps) ─

function _renderStatusOptions() {
  const container = document.getElementById('filter-status');
  if (!container) return;
  setHTML(container, statusDefs().map(d => {
    const isActive = d.key === filter.status;
    return `<button type="button" data-status="${d.key}"${isActive ? ' class="active"' : ''} aria-pressed="${isActive}">${dotHTML(d.dot)}${esc(d.label)}</button>`;
  }).join(''));
}

function _renderSpecOptions() {
  for (const g of SPEC_GROUPS) {
    const container = document.getElementById(g.container);
    if (!container) continue;
    setHTML(container, [_ALL, ...g.defs].map(d =>
      `<button type="button" data-${g.attr}="${d.key}">${esc(d.label)}</button>`).join(''));
  }
  const caps = document.getElementById('filter-caps');
  if (caps) {
    setHTML(caps, capDefs().map(c =>
      `<button type="button" class="notif-chip" data-cap="${c.key}" title="${esc(c.desc)}">${esc(c.label)}</button>`).join(''));
  }
}

function _renderScoreSegments() {
  const panel = document.getElementById('filter-score-panel');
  if (!panel) return;
  // Metric tabs + single shared range selector
  const tabs = SCORE_METRICS.map((m, i) => {
    const hasFilter = filter[m.key] !== 'all';
    const cls = [
      'filter-score-tab',
      i === _activeScoreTab ? 'active' : '',
      hasFilter ? 'has-filter' : '',
    ].filter(Boolean).join(' ');
    const label = esc(_scoreMetricLabel(m));
    return `<button type="button" class="${cls}" data-score-tab="${i}" aria-label="${label}${hasFilter ? ', 1 filter active' : ''}">${label}</button>`;
  }).join('');
  const current = filter[SCORE_METRICS[_activeScoreTab].key];
  const rangeBtns = scoreDefs().map(d => {
    const isActive = d.key === current;
    const classes = [
      isActive ? 'active' : '',
      d.tierKey ? TIER_TEXT[d.tierKey] : (isActive ? 'text-text-primary' : 'text-text-muted'),
      isActive && d.tierKey ? TIER_BG[d.tierKey] : '',
    ].filter(Boolean).join(' ');
    return `<button type="button" data-score="${d.key}" class="${classes}">${esc(d.label)}</button>`;
  }).join('');
  setHTML(panel,
    `<div class="filter-score-tabs" role="group" aria-label="Score metric">${tabs}</div>` +
    `<div id="filter-score-range" class="filter-seg" role="group" aria-label="Filter by ${esc(_scoreMetricLabel(SCORE_METRICS[_activeScoreTab]).toLowerCase())} score">${rangeBtns}</div>`);
}

// Options that depend on config (labels, score tiers, capabilities) re-render when it changes
export function renderFilterOptions() {
  if (!_initialized) return;
  _renderStatusOptions();
  _renderScoreSegments();
  _renderSpecOptions();
  _syncUI();
  applyFilter();
}

// ── UI sync: button active states from filter state ───────────────────────

function _syncUI() {
  const si = document.getElementById('filter-search');
  if (si && si.value !== filter.search) si.value = filter.search;

  document.querySelectorAll('#filter-status [data-status]').forEach(btn => {
    const active = btn.dataset.status === filter.status;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', String(active));
  });

  // Score: sync tab has-filter dots + shared range selector
  document.querySelectorAll('.filter-score-tab').forEach((tab, i) => {
    tab.classList.toggle('has-filter', filter[SCORE_METRICS[i].key] !== 'all');
  });
  const activeMetric = SCORE_METRICS[_activeScoreTab];
  const defs = scoreDefs();
  document.querySelectorAll('#filter-score-range [data-score]').forEach(btn => {
    const isActive = btn.dataset.score === filter[activeMetric.key];
    const def = defs.find(d => d.key === btn.dataset.score);
    btn.classList.toggle('active', isActive);
    if (def?.tierKey) btn.classList.toggle(TIER_BG[def.tierKey], isActive);
  });

  for (const g of SPEC_GROUPS) _syncSeg(`#${g.container} [data-${g.attr}]`, g.attr, k => k === filter[g.key]);

  document.querySelectorAll('#filter-caps [data-cap]').forEach(btn => {
    btn.classList.toggle('on', filter.caps.has(btn.dataset.cap));
    btn.setAttribute('aria-pressed', String(filter.caps.has(btn.dataset.cap)));
  });

  // Count active filters per dropdown and update badge + aria-label
  const statusCount = filter.status !== 'all' ? 1 : 0;
  const scoreCount = [filter.consistencyRange, filter.speedRange, filter.reliabilityRange].filter(v => v !== 'all').length;
  const specsCount = (filter.context !== 'all' ? 1 : 0) + (filter.paramSize !== 'all' ? 1 : 0) + filter.caps.size;

  _updateFilterBadge('filter-status-toggle', statusCount, 'Status');
  _updateFilterBadge('filter-score-toggle', scoreCount, 'Scores');
  _updateFilterBadge('filter-specs-toggle', specsCount, 'Specs');
}

function _updateFilterBadge(btnId, count, label) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  btn.classList.toggle('has-filter', count > 0);
  btn.setAttribute('aria-label', count > 0 ? `${label}, ${count} filter${count > 1 ? 's' : ''} active` : label);
  let badge = btn.querySelector('.filter-badge');
  if (count > 0) {
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'filter-badge';
      btn.appendChild(badge);
    }
    badge.textContent = count;
    badge.setAttribute('aria-hidden', 'true');
  } else if (badge) {
    badge.remove();
  }
}

function _syncSeg(selector, attr, isActive) {
  document.querySelectorAll(selector).forEach(btn => {
    const v = btn.dataset[attr];
    const active = isActive(v);
    btn.classList.toggle('active', active);
    btn.classList.toggle('text-text-primary', active);
    btn.classList.toggle('text-text-muted', !active);
    btn.setAttribute('aria-pressed', String(active));
  });
}

// ── UI: result count + active filter chips + empty state ─────────────────

function _updateCount(visible, total) {
  const el = document.getElementById('filter-count');
  const footer = document.getElementById('filter-footer');
  if (!el || !footer) return;
  const active = filterActive();
  setText(el, active ? `${visible} of ${total} models` : '');
  footer.hidden = !active;
}

function _chips() {
  const chips = [];
  if (filter.search) chips.push({ label: `"${filter.search}"`, clear: 'search' });
  if (filter.status !== 'all') {
    const d = statusDefs().find(s => s.key === filter.status);
    chips.push({ label: d?.label || filter.status, clear: 'status' });
  }
  const sdefs = scoreDefs();
  for (const m of SCORE_METRICS) {
    if (filter[m.key] !== 'all') {
      const d = sdefs.find(s => s.key === filter[m.key]);
      chips.push({ label: `${_scoreMetricLabel(m)} ${d?.label || filter[m.key]}`, clear: m.key });
    }
  }
  for (const g of SPEC_GROUPS) {
    if (filter[g.key] === 'all') continue;
    const d = g.defs.find(x => x.key === filter[g.key]);
    chips.push({ label: `${g.chip} ${d?.label || filter[g.key]}`, clear: g.clear });
  }
  for (const cap of filter.caps) {
    const d = capDefs().find(c => c.key === cap);
    chips.push({ label: d?.label || cap, clear: `cap:${cap}` });
  }
  return chips;
}

function _updateChips() {
  const container = document.getElementById('filter-chips');
  if (!container) return;
  const chips = _chips();
  if (!chips.length) {
    container.hidden = true;
    setHTML(container, '');
    return;
  }
  container.hidden = false;
  setHTML(container, chips.map(c =>
    `<button type="button" class="filter-chip" data-clear="${c.clear}" aria-label="Remove filter ${esc(c.label)}">${esc(c.label)}<span class="filter-chip-x" aria-hidden="true">&times;</span></button>`
  ).join(''));
}

// A filter that matches nothing says so and names what it filters on (finding F67)
function _updateEmptyState(visible) {
  const el = document.getElementById('filter-empty');
  if (!el) return;
  const empty = visible === 0 && filterActive() && state.models.length > 0;
  el.hidden = !empty;
  if (empty) setText(el.querySelector('.filter-empty-detail'), _chips().map(c => c.label).join(', '));
}

// ── Grey out filter options that would show zero models ───────────────────
// For each option, temporarily swap that one filter dimension and count
// matches. O(options × models) - ~27 × 31 = 837 evaluations, sub-millisecond.

function _countWithOverride(key, value) {
  const saved = filter[key];
  filter[key] = value;
  let count = 0;
  for (const entry of state.models) {
    if (_entryMatches(entry)) count++;
  }
  filter[key] = saved;
  return count;
}

function _countWithCap(capKey) {
  filter.caps.add(capKey);
  let count = 0;
  for (const entry of state.models) {
    if (_entryMatches(entry)) count++;
  }
  filter.caps.delete(capKey);
  return count;
}

function _setBtnDisabled(btn, disabled) {
  if (!btn) return;
  btn.classList.toggle('filter-disabled', disabled);
  btn.setAttribute('aria-disabled', String(disabled));
}

function _updateDisabledOptions() {
  // Status: skip "all" (always available) and current selection
  for (const d of statusDefs()) {
    if (d.key === 'all' || d.key === filter.status) continue;
    _setBtnDisabled(
      document.querySelector(`#filter-status [data-status="${d.key}"]`),
      _countWithOverride('status', d.key) === 0
    );
  }

  // Score: check the active tab's range options
  const activeMetric = SCORE_METRICS[_activeScoreTab];
  for (const d of scoreDefs()) {
    if (d.key === 'all' || d.key === filter[activeMetric.key]) continue;
    _setBtnDisabled(
      document.querySelector(`#filter-score-range [data-score="${d.key}"]`),
      _countWithOverride(activeMetric.key, d.key) === 0
    );
  }

  // Context and params: skip "all" and the current selection
  for (const g of SPEC_GROUPS) {
    for (const d of g.defs) {
      if (d.key === filter[g.key]) continue;
      _setBtnDisabled(
        document.querySelector(`#${g.container} [data-${g.attr}="${d.key}"]`),
        _countWithOverride(g.key, d.key) === 0
      );
    }
  }

  // Capabilities: only check unselected ones (selected are always available)
  for (const d of capDefs()) {
    const btn = document.querySelector(`#filter-caps [data-cap="${d.key}"]`);
    _setBtnDisabled(btn, !filter.caps.has(d.key) && _countWithCap(d.key) === 0);
  }
}

// ── Commit: sync UI + apply filter + persist (single call after any change) ─

function _commit() {
  _syncUI();
  applyFilter();
  _save();
}

function _clearAll() {
  filter.search = '';
  filter.status = 'all';
  filter.consistencyRange = 'all';
  filter.speedRange = 'all';
  filter.reliabilityRange = 'all';
  filter.context = 'all';
  filter.paramSize = 'all';
  filter.caps.clear();
  _commit();
  logInfo(logTag('Filter', '→', 'Clear'));
}

function _clearChipType(type) {
  if (type === 'search')       filter.search = '';
  else if (type === 'status')  filter.status = 'all';
  else if (type in filter)     filter[type] = 'all';  // score ranges + context + paramSize
  else if (type.startsWith('cap:')) filter.caps.delete(type.slice(4));
  _commit();
}

// ── Shared panel toggle (used by score, status, and specs buttons) ─────────
// An open panel is a layer: Escape closes the top one only (finding F49)

const PANELS = [
  { panel: 'filter-status-panel', btn: 'filter-status-toggle' },
  { panel: 'filter-score-panel',  btn: 'filter-score-toggle' },
  { panel: 'filter-specs',        btn: 'filter-specs-toggle' },
];
const _panelLayers = new Map();

function _setPanel(panelId, open) {
  const panel = document.getElementById(panelId);
  if (!panel) return;
  const def = PANELS.find(p => p.panel === panelId);
  const btn = def && document.getElementById(def.btn);
  panel.hidden = !open;
  if (btn) {
    btn.classList.toggle('active', open);
    btn.setAttribute('aria-expanded', String(open));
  }
  if (open && !_panelLayers.has(panelId)) {
    _panelLayers.set(panelId, pushLayer(() => _setPanel(panelId, false)));
  } else if (!open && _panelLayers.has(panelId)) {
    _panelLayers.get(panelId)();
    _panelLayers.delete(panelId);
  }
}

function _togglePanel(panelId) {
  const panel = document.getElementById(panelId);
  if (panel) _setPanel(panelId, panel.hidden);
}

function _closeAllPanels() { for (const { panel } of PANELS) _setPanel(panel, false); }

// ── Event wiring ──────────────────────────────────────────────────────────

function _wireEvents() {
  const bar = document.getElementById('filter-bar');
  if (!bar) return;

  // Search input - debounced
  const searchInput = document.getElementById('filter-search');
  if (searchInput) {
    let timer = 0;
    searchInput.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        filter.search = searchInput.value.trim().toLowerCase();
        _commit();
      }, SEARCH_DEBOUNCE_MS);
    });
    searchInput.addEventListener('keydown', e => {
      // With no layer open, Escape clears the search; otherwise it closes the top layer (app.js)
      if (e.key === 'Escape' && filter.search && !openLayerCount()) {
        e.preventDefault();
        filter.search = '';
        searchInput.value = '';
        _commit();
      }
    });
  }

  // Click delegation for all filter controls.
  // Stop propagation so the document-level outside-click handler (below) never
  // treats an inside-click as outside - critical because some handlers (e.g.
  // score tab switching) replace panel.innerHTML, which detaches e.target
  // mid-bubble and would make bar.contains(e.target) return false.
  bar.addEventListener('click', e => {
    e.stopPropagation();
    if (e.target.closest('#filter-status-toggle')) {
      _togglePanel('filter-status-panel');
      return;
    }
    if (e.target.closest('#filter-specs-toggle')) {
      _togglePanel('filter-specs');
      return;
    }
    if (e.target.closest('#filter-score-toggle')) {
      _togglePanel('filter-score-panel');
      return;
    }
    if (e.target.closest('#filter-clear')) { _clearAll(); return; }

    const chip = e.target.closest('[data-clear]');
    if (chip) { _clearChipType(chip.dataset.clear); return; }

    const statusBtn = e.target.closest('[data-status]');
    if (statusBtn) { filter.status = statusBtn.dataset.status; _commit(); return; }

    // Score tab switching
    const tabBtn = e.target.closest('[data-score-tab]');
    if (tabBtn) {
      _activeScoreTab = Number(tabBtn.dataset.scoreTab);
      _renderScoreSegments();
      _syncUI();
      _updateDisabledOptions();
      return;
    }

    // Score range button - applies to the active tab's metric
    const scoreBtn = e.target.closest('[data-score]');
    if (scoreBtn) {
      const metric = SCORE_METRICS[_activeScoreTab];
      filter[metric.key] = scoreBtn.dataset.score;
      _commit();
      return;
    }

    for (const g of SPEC_GROUPS) {
      const btn = e.target.closest(`[data-${g.attr}]`);
      if (btn) { filter[g.key] = btn.dataset[g.attr]; _commit(); return; }
    }

    const capBtn = e.target.closest('[data-cap]');
    if (capBtn) {
      const cap = capBtn.dataset.cap;
      if (filter.caps.has(cap)) filter.caps.delete(cap);
      else filter.caps.add(cap);
      _commit();
      return;
    }
  });

  document.getElementById('filter-empty')?.addEventListener('click', e => {
    if (e.target.closest('[data-clear-all]')) _clearAll();
  });

  // Close open dropdowns when clicking outside the filter bar.
  document.addEventListener('click', e => {
    if (!bar.contains(e.target)) _closeAllPanels();
  });
}

// ── Public: initialize filter bar (idempotent) ────────────────────────────

let _initialized = false;

export function initFilter() {
  if (_initialized) return;
  const bar = document.getElementById('filter-bar');
  if (!bar) return;
  _initialized = true;
  _load();
  _renderStatusOptions();
  _renderScoreSegments();
  _renderSpecOptions();
  _syncUI();
  _wireEvents();
  bar.hidden = false;
  applyFilter();
  logInfo(logTag('Filter', '→', 'Init'));
}

// Invalidate haystack cache when model list changes (called from dom.js if needed)
export function invalidateFilterCache() {
  _haystacks = null;
}
