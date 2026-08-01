// Modal history table: sortable columns, day separators, load-more pagination,
// and mobile accordion view. Benchmark and health tabs share rendering but
// use different column sets (BENCH_COLS vs HEALTH_COLS).
import { esc, setHTML, logError, logTag } from './utils.js';
import { tpsColor, ttftColor, stallColor, p99ItlColor, tailColor, batchingColor, fmtLatency, fmtTps, fmtTail, fmtBatching, fmtMsCompact, STATUS_TEXT, recordErrorText, degradedDescHTML } from './format.js';
import { registerTip } from './tooltips.js';
import { fetchHistory, HISTORY_PAGE_SIZE } from './api.js';
import { _localDateISO, _updateHistRangeLabel, getHistSince, getHistUntil, setOpenModelKey as _setOpenModelKey } from './modal-ranges.js';

let _sortCol = 'time';
let _sortDir = 'desc';
let _benchmarkHistory = [];
let _healthTableRows = null;
let _hasMoreBenchmark = false;
let _hasMoreHealth = false;
let _loadingMore = false;
let _loadMoreObserver = null;
let _historyTab = 'benchmark';
let _theadRO = null;
let _okTipSeq = 0;
const _collapsedDays = new Set();
let _openModelKey = null;

export function setOpenModelKey(key) { _openModelKey = key; _setOpenModelKey(key); }

export function _sortIndicator(sort) {
  if (sort !== _sortCol) return '\u21c5';
  return _sortDir === 'desc' ? ' \u25bc' : ' \u25b2';
}

function _okTdCls(h) {
  if (h.retry_attempt) return STATUS_TEXT.degraded;
  if (h.degraded) return STATUS_TEXT.degraded;
  return h.success ? STATUS_TEXT.online : STATUS_TEXT.error;
}

function _okCell(h) {
  if (h.retry_attempt) return _okTipCell(h, `\u21bb ${h.retry_attempt}/${h.retry_total || '?'}`, esc(recordErrorText(h) || 'Retry'), true);
  if (h.degraded) return _okTipCell(h, '\u26a0', degradedDescHTML(h), true);
  if (h.success) return '\u2713';
  return _okTipCell(h, '\u2717', esc(recordErrorText(h) || 'Failed'), true);
}

function _okTipCell(h, symbol, tipContent, copyable) {
  const id = `ok-${++_okTipSeq}`;
  const rid = h.request_id ? `<br><span class="text-text-muted/60">req: ${esc(h.request_id)}</span>` : '';
  registerTip(id, tipContent + rid);
  const copyAttr = copyable ? ' data-copy-tip' : '';
  return `<span data-tip-id="${id}" tabindex="0"${copyAttr}>${symbol}</span>`;
}

function _accMessageHTML(h) {
  const rid = h.request_id ? `<div class="mt-0.5 text-text-muted/60">req: ${esc(h.request_id)}</div>` : '';
  if (h.retry_attempt) {
    const msg = esc(recordErrorText(h) || 'Retry');
    return `<div class="mt-1 text-status-degraded">${msg}</div>${rid}`;
  }
  if (h.degraded) {
    return `<div class="mt-1 text-status-degraded">\u26a0 ${degradedDescHTML(h)}</div>${rid}`;
  }
  if (!h.success) {
    const msg = esc(recordErrorText(h) || 'Failed');
    return `<div class="mt-1 text-status-error">\u2717 ${msg}</div>${rid}`;
  }
  return '';
}

export const BENCH_COLS = [
  {
    id: 'time', label: 'Time', sort: 'time',
    tdCls: () => 'text-text-muted',
    cell(h) { return h.timestamp ? new Date(h.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '--'; },
  },
  {
    id: 'ttft', label: 'TTFT', sort: 'ttft', tip: 'ttft',
    tdCls: h => ttftColor(h.ttft_ms),
    cell(h) { return fmtLatency(h.ttft_ms); },
  },
  {
    id: 'tps', label: 'TPS', sort: 'tps', tip: 'tps',
    tdCls: h => tpsColor(h.tps),
    cell(h) { return fmtTps(h.tps); },
  },
  {
    id: 'stalls', label: 'Stalls', sort: 'stalls', tip: 'stall',
    tdCls: h => stallColor(h.stall_count),
    cell(h) { return h.stall_count != null ? h.stall_count : '--'; },
  },
  {
    id: 'p99', label: 'P99\u00a0ITL (raw)', sort: 'p99', tier2: true, tip: 'p99Itl',
    tdCls: h => p99ItlColor(h.raw_p99_itl_ms),
    cell(h) { return h.raw_p99_itl_ms != null ? fmtLatency(h.raw_p99_itl_ms) : '--'; },
  },
  {
    id: 'batch', label: 'Batch', sort: 'batch', tier2: true, tip: 'batching',
    tdCls: h => batchingColor(h.chunk_token_ratio),
    cell(h) { return fmtBatching(h.chunk_token_ratio); },
  },
  {
    id: 'tail', label: 'Tail (eff.)', sort: 'tail', tier2: true, tip: 'itlTailRatio',
    tdCls: h => tailColor(h.effective_itl_tail_ratio),
    cell(h) { return h.effective_itl_tail_ratio != null ? fmtTail(h.effective_itl_tail_ratio) : '--'; },
  },

  {
    id: 'jitter', label: 'Jitter', tier2: true, tip: 'networkJitter',
    cell(h) { return h.network_jitter_ms != null ? fmtMsCompact(h.network_jitter_ms) : '--'; },
  },

  {
    id: 'ok', label: 'OK', tip: 'ok',
    tdCls: _okTdCls,
    cell: _okCell,
  },

];

export const HEALTH_COLS = [
  {
    id: 'time', label: 'Time', sort: 'time',
    tdCls: () => 'text-text-muted',
    cell(h) { return h.timestamp ? new Date(h.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '--'; },
  },
  {
    id: 'ttft', label: 'TTFT', sort: 'ttft', tip: 'ttft',
    tdCls: h => ttftColor(h.ttft_ms),
    cell(h) { return fmtLatency(h.ttft_ms); },
  },
  {
    id: 'ok', label: 'OK', tip: 'ok',
    tdCls: _okTdCls,
    cell: _okCell,
  },
];

const _BENCH_ACC_SUMMARY = new Set(BENCH_COLS.filter(c => !c.tier2).map(c => c.id));
const _BENCH_ACC_DETAIL = BENCH_COLS.filter(c => c.tier2);
const _HEALTH_ACC_SUMMARY = new Set(HEALTH_COLS.filter(c => !c.tier2).map(c => c.id));
const _HEALTH_ACC_DETAIL = HEALTH_COLS.filter(c => c.tier2);

export function _showTier2() { return _historyTab === 'health' || localStorage.getItem('mw_table_cols') === '1'; }
function _activeCols() {
  const cols = _historyTab === 'health' ? HEALTH_COLS : BENCH_COLS;
  return _showTier2() ? cols : cols.filter(c => !c.tier2);
}
function _colSpan() { return _activeCols().length; }

function _hdrHTML(c) {
  const cls = [];
  if (c.sort) cls.push('sortable');
  if (c.tip) cls.push('tip-label');
  const attrs = [];
  if (c.sort) attrs.push(`data-sort="${c.sort}"`, `data-sort-label="${c.label.replace(/\u00a0/g, ' ')}"`);
  if (c.tip) attrs.push(`data-tip="${c.tip}"`, 'tabindex="0"');
  const indicator = c.sort ? `<span class="sort-ind" aria-hidden="true">${_sortIndicator(c.sort)}</span>` : '';
  return `<th class="${cls.join(' ')}"${attrs.length ? ' ' + attrs.join(' ') : ''}>${c.label}${indicator}</th>`;
}

function _cellHTML(c, h) {
  const cls = [];
  if (c.id !== 'ok') cls.push('font-mono');
  const dyn = c.tdCls ? c.tdCls(h) : '';
  if (dyn) cls.push(dyn);
  return `<td class="${cls.join(' ')}">${c.cell(h)}</td>`;
}

export function _headerHTML() {
  const cols = _activeCols();
  return `<thead><tr>${cols.map(_hdrHTML).join('')}</tr></thead>`;
}

function _dayLabel(ts) {
  const d = new Date(ts);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today); yesterday.setDate(yesterday.getDate() - 1);
  const rowDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  if (rowDay.getTime() === today.getTime()) return 'Today';
  if (rowDay.getTime() === yesterday.getTime()) return 'Yesterday';
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function _rowDayKey(ts) {
  const d = new Date(ts);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

export function _historyRowsHTML(history) {
  let prevDay = '';
  const parts = [];
  const cols = _activeCols();
  const span = _colSpan();
  for (const h of history) {
    const dayKey = h.timestamp ? _rowDayKey(h.timestamp) : '';
    if (dayKey && dayKey !== prevDay) {
      prevDay = dayKey;
      const iso = _localDateISO(h.timestamp);
      parts.push(`<tr class="hist-day" data-date="${esc(iso)}"><td colspan="${span}"><div class="hist-day-inner"><span class="day-label">${_dayLabel(h.timestamp)}</span><span class="day-chevron">▸</span></div></td></tr>`);
    }
    let rowCls = '';
    if (h.retry_attempt) { rowCls = 'row-retry'; }
    else if (h.degraded) { rowCls = 'row-degraded'; }
    else if (!h.success) { rowCls = 'row-error'; }
    parts.push(`<tr${rowCls ? ` class="${rowCls}"` : ''}>${cols.map(c => _cellHTML(c, h)).join('')}</tr>`);
  }
  return parts.join('');
}

export function _accordionItems(history) {
  let prevDay = '';
  const isHealth = _historyTab === 'health';
  const cols = _activeCols();
  const accSummary = isHealth ? _HEALTH_ACC_SUMMARY : _BENCH_ACC_SUMMARY;
  const accDetail = isHealth ? _HEALTH_ACC_DETAIL : _BENCH_ACC_DETAIL;
  return history.map(h => {
    let html = '';
    const dayKey = h.timestamp ? _rowDayKey(h.timestamp) : '';
    if (dayKey && dayKey !== prevDay) {
      prevDay = dayKey;
      html += `<div class="day-sep-acc" data-date="${esc(_localDateISO(h.timestamp))}"><span class="day-chevron">▸</span>${_dayLabel(h.timestamp)}</div>`;
    }
    const borderCls = h.retry_attempt ? 'border-l-2 border-l-status-degraded border-border-default' : h.degraded ? 'border-l-2 border-l-status-degraded border-border-default' : !h.success ? 'border-l-2 border-l-status-error border-border-default' : 'border-border-default';
    const detailHTML = accDetail.map(c => {
      const val = c.cell(h);
      if (!val && c.id === 'error') return '';
      const dyn = c.tdCls ? c.tdCls(h) : '';
      const cls = dyn ? ` ${dyn}` : '';
      const label = c.label.replace(/\u00a0/g, ' ');
      return `<div>${label} <span class="font-mono${cls}">${val}</span></div>`;
    }).join('');
    const msgHTML = _accMessageHTML(h);
      const _ACC_COL_WEIGHTS = { time: 1.2, ttft: 1.4, tps: 1.6, stalls: 0.9, p99: 1.3, batch: 1.1, tail: 1.1, jitter: 1.1, ok: 0.7 };
      const _stripHtml = s => typeof s === 'string' ? s.replace(/<[^>]+>/g, '') : String(s ?? '');
      const summaryColDefs = cols.filter(c => accSummary.has(c.id));
      const weights = summaryColDefs.map(c => _ACC_COL_WEIGHTS[c.id] || 1);
      const gridCols = `grid-template-columns:${weights.join('fr ')}fr`;
      const summaryCols = summaryColDefs.map(c => {
        const dyn = c.tdCls ? c.tdCls(h) : '';
        const cls = ['font-mono', dyn].filter(Boolean).join(' ');
        return `<span class="${cls}">${_stripHtml(c.cell(h))}</span>`;
      }).join('');
    const hasDetail = msgHTML.length > 0 || detailHTML.length > 0;
    const chevron = hasDetail ? '<svg class="w-3 h-3 text-text-faint transition-transform" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd"/></svg>' : '';
    return html + `
    <div class="acc-item ${borderCls}" data-acc-row>
      <button class="w-full flex items-center justify-between px-2 py-2 text-left"${hasDetail ? ' data-acc-toggle aria-expanded="false"' : ''}>
        <div class="grid items-center text-xs" style="${gridCols};flex:1;min-width:0">
          ${summaryCols}
        </div>
        ${chevron}
      </button>
      ${hasDetail ? `<div class="hidden px-2 pb-2 text-[11px]" data-acc-detail>` : '<div class="hidden">'}
        ${detailHTML ? `<div class="grid grid-cols-2 gap-x-4 gap-y-1">${detailHTML}</div>` : ''}
        ${msgHTML}
      </div>
    </div>`;
  }).join('');
}

export function _bindDaySepClicks() {
  document.querySelectorAll('.hist-day, .day-sep-acc').forEach(el => {
    el.addEventListener('click', () => _toggleDayCollapse(el));
  });
}

function _toggleDayCollapse(dayEl) {
  const date = dayEl.dataset.date;
  let collapsed;
  if (date) {
    if (_collapsedDays.has(date)) { _collapsedDays.delete(date); collapsed = false; }
    else { _collapsedDays.add(date); collapsed = true; }
  } else {
    collapsed = dayEl.classList.toggle('day-collapsed');
  }
  dayEl.classList.toggle('day-collapsed', collapsed);
  const chevron = dayEl.querySelector('.day-chevron');
  if (chevron) chevron.textContent = collapsed ? '▾' : '▸';
  const isAcc = dayEl.classList.contains('day-sep-acc');
  const dayEnd = isAcc ? 'day-sep-acc' : 'hist-day';
  let sibling = dayEl.nextElementSibling;
  while (sibling && !sibling.classList.contains(dayEnd)) {
    if (!sibling.classList.contains('load-more-row'))
      sibling.classList.toggle('day-row-hidden', collapsed);
    sibling = sibling.nextElementSibling;
  }
  _reobserveLoadMore();
}

export function _reobserveLoadMore() {
  if (!_loadMoreObserver) return;
  const sentinel = document.querySelector('#history-grid .load-more-row');
  if (sentinel) {
    _loadMoreObserver.disconnect();
    _loadMoreObserver.observe(sentinel);
  }
}

function _restoreCollapsedDays() {
  if (!_collapsedDays.size) return;
  document.querySelectorAll('.hist-day, .day-sep-acc').forEach(el => {
    const date = el.dataset.date;
    if (!date || !_collapsedDays.has(date)) return;
    el.classList.add('day-collapsed');
    const chevron = el.querySelector('.day-chevron');
    if (chevron) chevron.textContent = '▾';
    const isAcc = el.classList.contains('day-sep-acc');
    const dayEnd = isAcc ? 'day-sep-acc' : 'hist-day';
    let sibling = el.nextElementSibling;
    while (sibling && !sibling.classList.contains(dayEnd)) {
      if (!sibling.classList.contains('load-more-row'))
        sibling.classList.add('day-row-hidden');
      sibling = sibling.nextElementSibling;
    }
  });
}

export function _measureTheadHeight() {
  const th = document.querySelector('.hist-table thead');
  if (!th) return;
  if (_theadRO) { _theadRO.disconnect(); _theadRO = null; }
  const ms = document.getElementById('modal-scroll');
  if (!ms) return;
  _theadRO = new ResizeObserver(entries => {
    if (!ms.isConnected) return;
    const h = entries[0]?.contentBoxSize?.[0]?.blockSize ?? entries[0]?.contentRect?.height;
    if (h != null) ms.style.setProperty('--thead-h', h + 'px');
  });
  _theadRO.observe(th);
}

export function _sortParam() {
  const prefix = _sortDir === 'desc' ? '-' : '';
  return `${prefix}${_sortCol}`;
}

export function _activeHistory() {
  return _historyTab === 'health' ? (_healthTableRows || []) : _benchmarkHistory;
}

export function getHistoryTab() { return _historyTab; }
export function _setHistoryTab(tab) { _historyTab = tab; }
export function getBenchmarkHistory() { return _benchmarkHistory; }
export function getHealthTableRows() { return _healthTableRows; }

export function _renderSortedData(preserveScroll = false) {
  const ms = document.getElementById('modal-scroll');
  const savedScroll = preserveScroll && ms ? ms.scrollTop : 0;
  const history = _activeHistory();
  setHTML(document.getElementById('history-grid'), _headerHTML() + `<tbody>${_historyRowsHTML(history)}${_loadMoreHTML('tr')}</tbody>`);
  setHTML(document.getElementById('history-accordion'), _accordionItems(history) + _loadMoreHTML('div'));
  _restoreCollapsedDays();
  _bindAccordionToggles();
  _bindDaySepClicks();
  _updateSortHeaders();
  _bindSortHeaders();
  _bindLoadMore();
  _measureTheadHeight();
  if (preserveScroll && ms && savedScroll > 0) ms.scrollTop = savedScroll;
  _updateHistRangeLabel(history);
}

export function _loadMoreHTML(tag) {
  if (_sortParam() !== '-time') return '';
  const hasMore = _historyTab === 'health' ? _hasMoreHealth : _hasMoreBenchmark;
  if (!hasMore) return '';
  const btn = `<button class="chart-view-pill load-more-btn"${_loadingMore ? ' disabled' : ''}>Load more</button>`;
  if (tag === 'tr') return `<tr class="load-more-row"><td colspan="${_colSpan()}" class="text-center py-3">${btn}</td></tr>`;
  return `<div class="load-more-row text-center py-3">${btn}</div>`;
}

export function _bindLoadMore() {
  if (_loadMoreObserver) { _loadMoreObserver.disconnect(); _loadMoreObserver = null; }
  const ms = document.getElementById('modal-scroll');
  const desktopSentinel = document.querySelector('#history-grid .load-more-row');
  if (desktopSentinel && ms) {
    _loadMoreObserver = new IntersectionObserver(entries => {
      if (entries.some(e => e.isIntersecting) && !_loadingMore) _loadMoreHistory();
    }, { root: ms, rootMargin: '200px' });
    _loadMoreObserver.observe(desktopSentinel);
  }
  document.querySelectorAll('.load-more-btn').forEach(btn => {
    btn.addEventListener('click', _loadMoreHistory);
  });
}

export function _bindSortHeaders() {
  document.querySelectorAll('#history-grid th[data-sort]').forEach(el => {
    el.addEventListener('click', () => {
      const col = el.dataset.sort;
      let newDir;
      if (_sortCol === col) newDir = _sortDir === 'desc' ? 'asc' : 'desc';
      else { _sortCol = col; newDir = 'desc'; }
      _sortDir = newDir;
      _fetchInitialHistory(_openModelKey);
    });
  });
}

export function _updateSortHeaders() {
  document.querySelectorAll('#history-grid th[data-sort]').forEach(el => {
    const col = el.dataset.sort;
    const ind = el.querySelector('.sort-ind');
    if (ind) ind.textContent = _sortIndicator(col);
    if (col === _sortCol) {
      el.setAttribute('aria-sort', _sortDir === 'desc' ? 'descending' : 'ascending');
    } else {
      el.removeAttribute('aria-sort');
    }
  });
}

export function _bindAccordionToggles() {
  document.querySelectorAll('[data-acc-toggle]').forEach(btn => {
    btn.addEventListener('click', () => {
      const expanded = btn.getAttribute('aria-expanded') === 'true';
      btn.setAttribute('aria-expanded', String(!expanded));
      const detail = btn.nextElementSibling;
      if (detail) detail.classList.toggle('hidden', expanded);
      const svg = btn.querySelector('svg');
      if (svg) svg.classList.toggle('rotate-180', !expanded);
    });
  });
}

let _histSeq = 0;

export function _fetchInitialHistory(modelId) {
  const seq = ++_histSeq;
  const isHealth = _historyTab === 'health';
  if (isHealth) _hasMoreHealth = false;
  else _hasMoreBenchmark = false;
  const testType = isHealth ? 'health' : 'benchmark';
  const sort = _sortParam();
  const nonDefaultSort = sort !== '-time';
  const limit = nonDefaultSort ? HISTORY_PAGE_SIZE * 4 : HISTORY_PAGE_SIZE;
  fetchHistory(modelId, null, limit, testType, getHistSince(), getHistUntil(), sort).then(data => {
    if (!data || _openModelKey !== modelId || _histSeq !== seq) return;
    if (isHealth) {
      _healthTableRows = data.history || [];
      _hasMoreHealth = data.has_more || false;
    } else {
      _benchmarkHistory = data.history || [];
      _hasMoreBenchmark = data.has_more || false;
    }
    if (!nonDefaultSort && getHistSince() != null) {
      if (isHealth) _hasMoreHealth = true;
      else _hasMoreBenchmark = true;
    }
    _renderSortedData(false);
  }).catch(e => logError(logTag('Modal', '\u2190', 'Error', 'HistoryFetch'), e));
}

export function _loadMoreHistory() {
  if (_loadingMore) return;
  const modelId = _openModelKey;
  if (!modelId || _sortParam() !== '-time') return;
  const isHealth = _historyTab === 'health';
  if (!(isHealth ? _hasMoreHealth : _hasMoreBenchmark)) return;
  const history = _activeHistory();
  const before = history.length ? history[history.length - 1].ts_epoch : null;
  if (before == null) return;
  _loadingMore = true;
  const testType = isHealth ? 'health' : 'benchmark';
  fetchHistory(modelId, before, HISTORY_PAGE_SIZE, testType, null, null, null).then(data => {
    if (!data || _openModelKey !== modelId) return;
    const newRows = data.history || [];
    if (isHealth) {
      _hasMoreHealth = data.has_more || false;
      _healthTableRows = (_healthTableRows || []).concat(newRows);
    } else {
      _hasMoreBenchmark = data.has_more || false;
      _benchmarkHistory = _benchmarkHistory.concat(newRows);
    }
    _loadingMore = false;
    _renderSortedData(true);
  }).catch(e => { _loadingMore = false; logError(logTag('Modal', '\u2190', 'Error', 'LoadMore'), e); });
}

export function prependHistoryRecord(record, isHealth, cap) {
  if (isHealth) {
    if (_healthTableRows) {
      _healthTableRows.unshift(record);
      if (_healthTableRows.length > cap) _healthTableRows.length = cap;
      if (_historyTab === 'health') _renderSortedData(true);
    }
  } else {
    _benchmarkHistory.unshift(record);
    if (_benchmarkHistory.length > cap) _benchmarkHistory.length = cap;
    if (_historyTab === 'benchmark') _renderSortedData(true);
  }
}

export function resetHistoryState() {
  if (_loadMoreObserver) { _loadMoreObserver.disconnect(); _loadMoreObserver = null; }
  if (_theadRO) { _theadRO.disconnect(); _theadRO = null; }
  _healthTableRows = null;
  _benchmarkHistory = [];
  _hasMoreBenchmark = false;
  _hasMoreHealth = false;
  _loadingMore = false;
  _historyTab = 'benchmark';
  _okTipSeq = 0;
  _collapsedDays.clear();
  _sortCol = 'time';
  _sortDir = 'desc';
  _histSeq = 0;
}

export function resetHistoryForOpen() {
  _healthTableRows = null;
  _benchmarkHistory = [];
  _hasMoreBenchmark = false;
  _hasMoreHealth = false;
  _loadingMore = false;
  _historyTab = 'benchmark';
}
