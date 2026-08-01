// Filter bar: search, status, score ranges, specs, and capabilities. Uses CSS
// hide/show (preserves chart instances) rather than DOM rebuild. Section
// visibility is derived from the data model, not DOM presence, so collapsed
// providers (skeleton placeholders) are handled correctly.
import { state } from './state.js';
import { slug, esc, logError, logInfo, logTag, dotHTML, setHTML, setText } from './utils.js';
import { refreshVisibleCharts } from './chart.js';
import { STATUS_DOT, TIER_TEXT, TIER_BG, SCORE_TIERS } from './format.js';

// ── Filter state ──────────────────────────────────────────────────────────

const filter = {
  search: '',             // lowercase search query
  status: 'all',          // 'all' | 'online' | 'degraded' | 'offline' | 'archived'
  consistencyRange: 'all', // 'all' | SCORE_TIERS key (e.g. '80', '60', '40', '20', '0')
  speedRange: 'all',
  reliabilityRange: 'all',
  context: 'all',         // 'all' | '200k-1m' | 'gte1m'
  paramSize: 'all',       // 'all' | 'lt70' | '70-235' | '235-500' | 'gt500'
  caps: new Set(),        // empty = no filter; entries are capability keys
};

const STORAGE_KEY = 'mw_filters';
const SEARCH_DEBOUNCE_MS = 150;

// Filter definitions - used for both rendering and chip labels
const STATUS_DEFS = [
  { key: 'all',      label: 'All',      dot: 'bg-text-faint' },
  { key: 'online',   label: 'Online',   dot: STATUS_DOT.online },
  { key: 'degraded', label: 'Degraded', dot: STATUS_DOT.degraded },
  { key: 'offline',  label: 'Offline',  dot: STATUS_DOT.error },
  { key: 'archived', label: 'Archived', dot: 'bg-surface-400' },
];
// Score range options derived from SCORE_TIERS - exact same boundaries as card colors.
// Each option is a range [min, max] matching the card's tier system.
const SCORE_DEFS = [
  { key: 'all', label: 'All', min: null, max: null, tierKey: null },
  ...SCORE_TIERS.map((t, i) => {
    const upper = i > 0 ? SCORE_TIERS[i - 1].min - 1 : 100;
    const label = t.min === 0 ? `\u2264${upper}%` : `${t.min}\u2013${upper}%`;
    return { key: String(t.min), label, min: t.min, max: upper, tierKey: t.key };
  }),
];
const SCORE_METRICS = [
  { key: 'consistencyRange', label: 'Consistency', scoreKey: 'consistency', tabKey: 'consistency' },
  { key: 'speedRange',       label: 'Speed',       scoreKey: 'speed',       tabKey: 'speed' },
  { key: 'reliabilityRange', label: 'Reliability', scoreKey: 'reliability', tabKey: 'reliability' },
];
let _activeScoreTab = 0; // index into SCORE_METRICS
const CONTEXT_DEFS = [
  { key: 'all',      label: 'All' },
  { key: '0-64k',    label: '\u226464K' },
  { key: '64k-262k', label: '64K\u2013262K' },
  { key: '262k-1m',  label: '262K\u20131M' },
  { key: 'gte1m',    label: '>1M' },
];
const PARAM_DEFS = [
  { key: 'all',      label: 'All' },
  { key: 'lt70',     label: '<70B' },
  { key: '70-235',   label: '70\u2013235B' },
  { key: '235-500',  label: '235\u2013500B' },
  { key: 'gt500',    label: '>500B' },
];
// Capability keys match capabilitiesBadge() in dom.js
const CAP_DEFS = [
  { key: 'supports_vision',            label: 'Vision' },
  { key: 'supports_tools',             label: 'Tools' },
  { key: 'thinking',                   label: 'Reasoning' },
  { key: 'supports_cache',             label: 'Cache' },
  { key: 'supports_structured_output', label: 'Structured Output' },
];

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
  if (filter.status === 'archived') return !!entry.archived;
  // Non-archived statuses: exclude archived models
  if (entry.archived) return false;
  const s = metrics?.status || 'unknown';
  if (filter.status === 'offline') return s === 'error' || s === 'unknown';
  return s === filter.status;
}

function _matchesScore(metrics) {
  const sc = metrics?.scores;
  for (const m of SCORE_METRICS) {
    const rangeKey = filter[m.key];
    if (rangeKey === 'all') continue;
    const def = SCORE_DEFS.find(d => d.key === rangeKey);
    if (!def || def.min == null) continue;
    const v = sc?.[m.scoreKey];
    if (v == null || v < def.min || v > def.max) return false;
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

function _matchesSpecs(entry) {
  if (filter.context !== 'all') {
    const ctx = entry.context_window;
    if (ctx == null) return false;
    if (filter.context === '0-64k')    { if (ctx >= 64000) return false; }
    else if (filter.context === '64k-262k') { if (ctx < 64000 || ctx >= 262000) return false; }
    else if (filter.context === '262k-1m')  { if (ctx < 262000 || ctx >= 1000000) return false; }
    else if (filter.context === 'gte1m')    { if (ctx <= 1000000) return false; }
  }
  if (filter.paramSize !== 'all') {
    const p = _parseParamB(entry.param_count);
    if (p == null) return false;
    if (filter.paramSize === 'lt70')    { if (p >= 70) return false; }
    if (filter.paramSize === '70-235')  { if (p < 70 || p >= 235) return false; }
    if (filter.paramSize === '235-500') { if (p < 235 || p >= 500) return false; }
    if (filter.paramSize === 'gt500')   { if (p < 500) return false; }
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

// ── Core: apply filter to dOM (CSS hide/show - preserves charts) ──────────

// Track which provider sections had cards change from hidden→visible,
// so we only trigger chart init when filtering actually reveals cards
// (not on initial render where lazy loading handles it).
const _newlyVisibleProviders = new Set();

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
  _updateDisabledOptions();
}

// ── Persistence ───────────────────────────────────────────────────────────

function _save() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
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

function _load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const s = JSON.parse(raw);
    filter.search    = s.search    || '';
    filter.status    = s.status    || 'all';
    // Migrate old minConsistency/minSpeed/minReliability (numbers) to range keys
    if (s.consistencyRange != null) filter.consistencyRange = s.consistencyRange;
    else if (s.minConsistency > 0) filter.consistencyRange = String(s.minConsistency);
    if (s.speedRange != null) filter.speedRange = s.speedRange;
    else if (s.minSpeed > 0) filter.speedRange = String(s.minSpeed);
    if (s.reliabilityRange != null) filter.reliabilityRange = s.reliabilityRange;
    else if (s.minReliability > 0) filter.reliabilityRange = String(s.minReliability);
    // Migrate old contextMin (number) to new context (string key)
    if (s.context != null) filter.context = s.context;
    else if (s.contextMin >= 1000000) filter.context = 'gte1m';
    else if (s.contextMin >= 200000) filter.context = 'all';
    filter.paramSize = s.paramSize || 'all';
    filter.caps      = new Set(s.caps || []);
  } catch (e) { logError(logTag('Filter', 'Err', 'Load'), e); }
}

// ── Render status + score buttons dynamically (DRY: classes from shared maps) ─

function _renderStatusOptions() {
  const container = document.getElementById('filter-status');
  if (!container) return;
  setHTML(container, STATUS_DEFS.map(d => {
    const isActive = d.key === filter.status;
    return `<button type="button" data-status="${d.key}"${isActive ? ' class="active"' : ''}>${dotHTML(d.dot)}${d.label}</button>`;
  }).join(''));
}

function _renderScoreSegments() {
  const panel = document.getElementById('filter-score-panel');
  if (!panel) return;
  // Metric tabs + single shared range selector (8 elements instead of 18)
  const tabs = SCORE_METRICS.map((m, i) => {
    const hasFilter = filter[m.key] !== 'all';
    const cls = [
      'filter-score-tab',
      i === _activeScoreTab ? 'active' : '',
      hasFilter ? 'has-filter' : '',
    ].filter(Boolean).join(' ');
    return `<button type="button" class="${cls}" data-score-tab="${i}" aria-label="${m.label}${hasFilter ? ', 1 filter active' : ''}">${m.label}</button>`;
  }).join('');
  const current = filter[SCORE_METRICS[_activeScoreTab].key];
  const rangeBtns = SCORE_DEFS.map(d => {
    const isActive = d.key === current;
    const classes = [
      isActive ? 'active' : '',
      d.tierKey ? TIER_TEXT[d.tierKey] : (isActive ? 'text-text-primary' : 'text-text-muted'),
      isActive && d.tierKey ? TIER_BG[d.tierKey] : '',
    ].filter(Boolean).join(' ');
    return `<button type="button" data-score="${d.key}" class="${classes}">${d.label}</button>`;
  }).join('');
  setHTML(panel,
    `<div class="filter-score-tabs" role="group" aria-label="Score metric">${tabs}</div>` +
    `<div id="filter-score-range" class="filter-seg" role="group" aria-label="Filter by ${SCORE_METRICS[_activeScoreTab].label.toLowerCase()} score">${rangeBtns}</div>`);
}

// ── UI sync: button active states from filter state ───────────────────────

function _syncUI() {
  const si = document.getElementById('filter-search');
  if (si && si.value !== filter.search) si.value = filter.search;

  // Status: toggle active on the matching button
  document.querySelectorAll('#filter-status [data-status]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.status === filter.status);
  });

  // Score: sync tab has-filter dots + shared range selector
  document.querySelectorAll('.filter-score-tab').forEach((tab, i) => {
    tab.classList.toggle('has-filter', filter[SCORE_METRICS[i].key] !== 'all');
  });
  const activeMetric = SCORE_METRICS[_activeScoreTab];
  document.querySelectorAll('#filter-score-range [data-score]').forEach(btn => {
    const isActive = btn.dataset.score === filter[activeMetric.key];
    const def = SCORE_DEFS.find(d => d.key === btn.dataset.score);
    btn.classList.toggle('active', isActive);
    if (def?.tierKey) btn.classList.toggle(TIER_BG[def.tierKey], isActive);
  });

  _syncSeg('#filter-ctx [data-ctx]',    'ctx',  k => k === filter.context);
  _syncSeg('#filter-param [data-param]', 'param', k => k === filter.paramSize);

  document.querySelectorAll('#filter-caps [data-cap]').forEach(btn => {
    btn.classList.toggle('on', filter.caps.has(btn.dataset.cap));
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
  });
}

// ── UI: result count + active filter chips ────────────────────────────────

function _updateCount(visible, total) {
  const el = document.getElementById('filter-count');
  const footer = document.getElementById('filter-footer');
  if (!el || !footer) return;
  const active = filterActive();
  setText(el, active ? `${visible} of ${total} models` : '');
  footer.hidden = !active;
}

function _updateChips() {
  const container = document.getElementById('filter-chips');
  if (!container) return;
  const chips = [];
  if (filter.search)         chips.push({ label: `"${filter.search}"`,  clear: 'search' });
  if (filter.status !== 'all') {
    const d = STATUS_DEFS.find(s => s.key === filter.status);
    chips.push({ label: d?.label || filter.status, clear: 'status' });
  }
  for (const m of SCORE_METRICS) {
    if (filter[m.key] !== 'all') {
      const d = SCORE_DEFS.find(s => s.key === filter[m.key]);
      chips.push({ label: `${m.label} ${d?.label || filter[m.key]}`, clear: m.key });
    }
  }
  if (filter.context !== 'all') {
    const d = CONTEXT_DEFS.find(c => c.key === filter.context);
    chips.push({ label: `Ctx ${d?.label || filter.context}`, clear: 'context' });
  }
  if (filter.paramSize !== 'all') {
    const d = PARAM_DEFS.find(p => p.key === filter.paramSize);
    chips.push({ label: `Params ${d?.label || filter.paramSize}`, clear: 'param' });
  }
  for (const cap of filter.caps) {
    const d = CAP_DEFS.find(c => c.key === cap);
    chips.push({ label: d?.label || cap, clear: `cap:${cap}` });
  }

  if (!chips.length) {
    container.hidden = true;
    setHTML(container, '');
    return;
  }
  container.hidden = false;
  setHTML(container, chips.map(c =>
    `<button type="button" class="filter-chip" data-clear="${c.clear}">${esc(c.label)}<span class="filter-chip-x">&times;</span></button>`
  ).join(''));
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
  for (const d of STATUS_DEFS) {
    if (d.key === 'all' || d.key === filter.status) continue;
    _setBtnDisabled(
      document.querySelector(`#filter-status [data-status="${d.key}"]`),
      _countWithOverride('status', d.key) === 0
    );
  }

  // Score: check the active tab's range options
  const activeMetric = SCORE_METRICS[_activeScoreTab];
  for (const d of SCORE_DEFS) {
    if (d.key === 'all' || d.key === filter[activeMetric.key]) continue;
    _setBtnDisabled(
      document.querySelector(`#filter-score-range [data-score="${d.key}"]`),
      _countWithOverride(activeMetric.key, d.key) === 0
    );
  }

  // Context: skip "all" and current selection
  for (const d of CONTEXT_DEFS) {
    if (d.key === 'all' || d.key === filter.context) continue;
    _setBtnDisabled(
      document.querySelector(`#filter-ctx [data-ctx="${d.key}"]`),
      _countWithOverride('context', d.key) === 0
    );
  }

  // Params: skip "all" and current selection
  for (const d of PARAM_DEFS) {
    if (d.key === 'all' || d.key === filter.paramSize) continue;
    _setBtnDisabled(
      document.querySelector(`#filter-param [data-param="${d.key}"]`),
      _countWithOverride('paramSize', d.key) === 0
    );
  }

  // Capabilities: only check unselected ones (selected are always available)
  for (const d of CAP_DEFS) {
    if (filter.caps.has(d.key)) {
      _setBtnDisabled(document.querySelector(`#filter-caps [data-cap="${d.key}"]`), false);
      continue;
    }
    _setBtnDisabled(
      document.querySelector(`#filter-caps [data-cap="${d.key}"]`),
      _countWithCap(d.key) === 0
    );
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
  logInfo(logTag('Filter', '\u2192', 'Clear'));
}

function _clearChipType(type) {
  if (type === 'search')       filter.search = '';
  else if (type === 'status')  filter.status = 'all';
  else if (type in filter)     filter[type] = 'all';  // score ranges + context + paramSize
  else if (type.startsWith('cap:')) filter.caps.delete(type.slice(4));
  _commit();
}

// ── Shared panel toggle (used by score, status, and specs buttons) ─────────

const PANELS = [
  { panel: 'filter-status-panel', btn: 'filter-status-toggle' },
  { panel: 'filter-score-panel',  btn: 'filter-score-toggle' },
  { panel: 'filter-specs',        btn: 'filter-specs-toggle' },
];

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
}

function _togglePanel(panelId) {
  const panel = document.getElementById(panelId);
  if (panel) _setPanel(panelId, panel.hidden);
}

function _closeAllPanels() { for (const { panel } of PANELS) _setPanel(panel, false); }
function _anyPanelOpen() { return PANELS.some(({ panel }) => { const p = document.getElementById(panel); return p && !p.hidden; }); }

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
      // Escape closes an open dropdown first; only clears the search when no panel is open.
      if (e.key === 'Escape' && filter.search && !_anyPanelOpen()) {
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

    const ctxBtn = e.target.closest('[data-ctx]');
    if (ctxBtn) { filter.context = ctxBtn.dataset.ctx; _commit(); return; }

    const paramBtn = e.target.closest('[data-param]');
    if (paramBtn) { filter.paramSize = paramBtn.dataset.param; _commit(); return; }

    const capBtn = e.target.closest('[data-cap]');
    if (capBtn) {
      const cap = capBtn.dataset.cap;
      if (filter.caps.has(cap)) filter.caps.delete(cap);
      else filter.caps.add(cap);
      _commit();
      return;
    }
  });

  // Close open dropdowns when clicking outside the filter bar.
  document.addEventListener('click', e => {
    if (!bar.contains(e.target)) _closeAllPanels();
  });
  // Escape closes any open dropdown panel (the search input handles its own
  // Escape only when no panel is open, so the first Escape closes a panel).
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && _anyPanelOpen()) {
      _closeAllPanels();
      e.preventDefault();
    }
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
  _syncUI();
  _wireEvents();
  bar.hidden = false;
  applyFilter();
  logInfo(logTag('Filter', '\u2192', 'Init'));
}

// Invalidate haystack cache when model list changes (called from dom.js if needed)
export function invalidateFilterCache() {
  _haystacks = null;
}
