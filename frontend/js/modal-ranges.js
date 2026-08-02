// Modal time-range state + date-range picker. Manages chart and history range
// independently, persists to localStorage, and provides the shared fetch
// functions (_fetchForRange, _fetchHealthForRange) with a race-guard counter.
import { state } from './state.js';
import { logError, logTag } from './utils.js';
import { fetchChartData } from './api.js';
import { _transformModalBuckets, _calculateBuckets, initChart, updateChartView } from './chart.js';

let _fetchSeq = 0;
let _chartSince = null;
let _chartUntil = null;
let _histSince = null;
let _histUntil = null;
let _histRangeKey = null;
let _timeRange = null;
let _dateRangePopover = null;
let _dateRangeClickHandler = null;
let _healthBuckets = null;
let _openModelKey = null;

let _histFetchFn = null;
let _modalBucketsFn = null;
let _modalInfoHTMLFn = null;

export function setHistFetchFn(fn) { _histFetchFn = fn; }
export function setModalBucketsFn(fn) { _modalBucketsFn = fn; }
export function setModalInfoHTMLFn(fn) { _modalInfoHTMLFn = fn; }
export function setOpenModelKey(key) { _openModelKey = key; }
export function setHealthBuckets(b) { _healthBuckets = b; }
export function getHealthBuckets() { return _healthBuckets; }
export function getHistSince() { return _histSince; }
export function getHistUntil() { return _histUntil; }
export function getChartSince() { return _chartSince; }
export function getTimeRange() { return _timeRange; }
export function getFetchSeq() { return _fetchSeq; }

export function _localDateISO(val) {
  const d = typeof val === 'number' ? new Date(val * 1000) : new Date(val);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function _isRangeEligible(r, availableRanges) {
  if (r.key === 'max') return true;
  return availableRanges.includes(r.key);
}

export function _sinceForRange(rangeKey) {
  if (rangeKey === 'max' || !rangeKey) return null;
  const range = state.timeRanges.find(r => r.key === rangeKey);
  if (!range || !range.seconds) return null;
  return (Date.now() / 1000) - range.seconds;
}

export function _rangeSinceUntil(rangeKey) {
  if (rangeKey === 'max' || !rangeKey || rangeKey === 'custom') return [null, null];
  const range = state.timeRanges.find(r => r.key === rangeKey);
  if (!range || !range.seconds) return [null, null];
  return [(Date.now() / 1000) - range.seconds, null];
}

export function _rangeLabel(since, until) {
  if (since == null && until == null) return 'All time';
  const fmt = (epoch) => {
    const d = new Date(epoch * 1000);
    return d.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
  };
  const from = since != null ? fmt(since) : '\u2212';
  const to = until != null ? fmt(until) : 'now';
  return `${from} \u2192 ${to}`;
}

export function _updateHistRangeLabel(history) {
  const label = document.getElementById('hist-range-label');
  if (!label) return;
  if (history && history.length) {
    const epochs = history.filter(h => h.ts_epoch).map(h => h.ts_epoch);
    if (epochs.length) {
      const earliest = Math.min(...epochs);
      if (_histSince == null || earliest < _histSince) {
        _histSince = earliest;
        _lsSetOrRemove('mw_hist_since', _histSince);
      }
    }
  }
  label.textContent = _rangeLabel(_histSince, _histUntil);
}

export function _rangePillsHTML(ranges, activeKey, availableRanges, attrName, showCustom = true) {
  const isEligible = (r) => _isRangeEligible(r, availableRanges);
  let html = ranges.map(r => {
    const disabled = r.key !== 'max' && !isEligible(r);
    const active = r.key === activeKey && !disabled;
    if (disabled) return '';
    return `<button class="chart-view-pill${active ? ' active' : ''}" data-${attrName}="${r.key}">${r.label || r.key}</button>`;
  }).join('');
  if (showCustom) {
    html += `<button class="chart-view-pill${activeKey === 'custom' ? ' active' : ''}" data-${attrName}="custom" data-tip="customDateRange"><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="1.5" y="3" width="13" height="11" rx="2"/><path d="M1.5 7h13M5 1v4M11 1v4"/></svg></button>`;
  }
  return html;
}

export function _updatePillUI(attrName, activeKey) {
  document.querySelectorAll(`[data-${attrName}]`).forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute(`data-${attrName}`) === activeKey);
  });
  const active = document.querySelector(`[data-${attrName}="${activeKey}"]`);
  if (active) {
    active.classList.remove('range-changed');
    for (const a of active.getAnimations()) { a.cancel(); a.play(); }
  }
}

export function _updateRangeUI(activeKey) {
  _updatePillUI('range', activeKey);
}

export function _lsSetOrRemove(key, value) {
  if (value != null) localStorage.setItem(key, String(value));
  else localStorage.removeItem(key);
}

export function _applyChartRange(rangeKey) {
  if (rangeKey === _timeRange && rangeKey !== 'custom') return;
  _timeRange = rangeKey;
  const [since] = _rangeSinceUntil(rangeKey);
  _chartSince = since;
  _chartUntil = null;
  if (rangeKey !== 'custom') {
    localStorage.setItem('mw_chart_range', rangeKey);
    localStorage.removeItem('mw_chart_since');
    localStorage.removeItem('mw_chart_until');
  }
  _updatePillUI('range', rangeKey);
  _fetchForRange(_openModelKey, rangeKey, state.charts['modal-chart']?._view);
}

export function _applyHistRange(rangeKey) {
  if (rangeKey === _histRangeKey) return;
  _histRangeKey = rangeKey;
  localStorage.setItem('mw_hist_range', rangeKey);
  _histSince = null;
  _histUntil = null;
  if (rangeKey !== 'max') {
    const [since] = _rangeSinceUntil(rangeKey);
    _histSince = since;
  }
  _lsSetOrRemove('mw_hist_since', _histSince);
  _lsSetOrRemove('mw_hist_until', _histUntil);
  const label = document.getElementById('hist-range-label');
  if (label) label.textContent = _rangeLabel(_histSince, _histUntil);
  const histBtn = document.getElementById('hist-custom-range');
  if (histBtn) histBtn.classList.remove('active');
  if (_histFetchFn) _histFetchFn(_openModelKey);
}

export function _applyCustomRange(since, until, target = 'chart') {
  const curS = target === 'history' ? _histSince : _chartSince;
  const curU = target === 'history' ? _histUntil : _chartUntil;
  const sinceSame = (since == null && curS == null) || (since != null && curS != null && _localDateISO(since) === _localDateISO(curS));
  const untilSame = (until == null && curU == null) || (until != null && curU != null && Math.abs(until - curU) < 1);
  if (sinceSame && untilSame && (target === 'history' || _timeRange === 'custom')) return;
  if (target === 'chart') {
    _chartSince = since;
    _chartUntil = until;
    _timeRange = 'custom';
    localStorage.setItem('mw_chart_range', 'custom');
    _lsSetOrRemove('mw_chart_since', since);
    _lsSetOrRemove('mw_chart_until', until);
    _updatePillUI('range', 'custom');
    _fetchForRange(_openModelKey, 'custom', state.charts['modal-chart']?._view);
  }
  if (target === 'history') {
    _histRangeKey = 'custom';
    localStorage.setItem('mw_hist_range', 'custom');
    _histSince = since;
    _histUntil = until;
    _lsSetOrRemove('mw_hist_since', since);
    _lsSetOrRemove('mw_hist_until', until);
    const label = document.getElementById('hist-range-label');
    if (label) label.textContent = _rangeLabel(_histSince, _histUntil);
    const histBtn = document.getElementById('hist-custom-range');
    if (histBtn) histBtn.classList.add('active');
    if (_histFetchFn) _histFetchFn(_openModelKey);
  }
}

export function _fetchForRange(modelId, rangeKey, view) {
  const seq = ++_fetchSeq;
  if (state._backendDown) return;
  let since, until;
  if (rangeKey === 'custom') {
    since = _chartSince;
    until = _chartUntil;
  } else {
    [since, until] = _rangeSinceUntil(rangeKey);
  }
  _healthBuckets = null;
  const effectiveView = view || state.charts['modal-chart']?._view || 'speed';
  _setRangeLoading(true);

  fetchChartData(modelId, since, _calculateBuckets('modal'), 'modal', 'benchmark', effectiveView, until).then(data => {
    if (!data || _openModelKey !== modelId || _fetchSeq !== seq) return;
    const buckets = _transformModalBuckets(data.buckets);
    if (_modalBucketsFn) _modalBucketsFn(buckets);
    const curView = localStorage.getItem('mw_chart_view') || 'speed';
    const hasChart = !!state.charts['modal-chart'];
    const targetView = (hasChart && curView !== 'health') ? curView : effectiveView;
    if (targetView !== 'health') {
      if (!updateChartView('modal-chart', buckets, true, targetView)) initChart('modal-chart', buckets, true, targetView, '', false);
    }
    const infoEl = document.getElementById('modal-info');
    if (infoEl && _modalInfoHTMLFn) {
      const entry = state._modelMap[modelId];
      if (entry) infoEl.innerHTML = _modalInfoHTMLFn(entry, state.metrics[modelId] || {});
    }
  }).catch(e => logError(logTag('Modal', '\u2190', 'Error', 'RangeFetch'), e))
  .finally(() => { if (_fetchSeq === seq) _setRangeLoading(false); });

  if (effectiveView === 'health') _fetchHealthForRange(modelId, since, seq, true);
}

export function _fetchHealthForRange(modelId, since, seq, switchChart = true) {
  const until = _chartUntil;
  fetchChartData(modelId, since, _calculateBuckets('modal'), 'modal', 'health', 'health', until).then(data => {
    if (!data || _openModelKey !== modelId || _fetchSeq !== seq) return;
    _healthBuckets = _transformModalBuckets(data.buckets);
    if (switchChart) {
      if (!updateChartView('modal-chart', _healthBuckets, true, 'health')) initChart('modal-chart', _healthBuckets, true, 'health', '', false);
    }
  }).catch(e => logError(logTag('Modal', '\u2190', 'Error', 'HealthFetch'), e));
}

export function _updateChartViewUI(view) {
  const speedBtn = document.getElementById('chart-view-speed');
  const consistencyBtn = document.getElementById('chart-view-consistency');
  const scoresBtn = document.getElementById('chart-view-scores');
  const healthBtn = document.getElementById('chart-view-health');
  if (speedBtn) speedBtn.classList.toggle('active', view === 'speed');
  if (consistencyBtn) consistencyBtn.classList.toggle('active', view === 'consistency');
  if (scoresBtn) scoresBtn.classList.toggle('active', view === 'scores');
  if (healthBtn) healthBtn.classList.toggle('active', view === 'health');
}

export function _setRangeLoading(loading) {
  document.querySelectorAll('[data-range]').forEach(btn => {
    btn.classList.toggle('loading', loading && !btn.classList.contains('active'));
  });
}

export function _closeDateRangePopover() {
  if (_dateRangePopover) { _dateRangePopover.remove(); _dateRangePopover = null; }
  if (_dateRangeClickHandler) { document.removeEventListener('click', _dateRangeClickHandler); _dateRangeClickHandler = null; }
  document.removeEventListener('keydown', _dateRangeEscHandler);
}

export function _dateRangeEscHandler(e) { if (e.key === 'Escape') { _closeDateRangePopover(); e.stopPropagation(); } }

export function _openDateRangePicker(anchorEl, dataStartEpoch, target = 'chart') {
  _closeDateRangePopover();

  const now = new Date();
  const todayISO = _localDateISO(now.getTime() / 1000);
  const minISO = dataStartEpoch ? _localDateISO(dataStartEpoch) : '';

  const currentSince = target === 'history' ? _histSince : _chartSince;
  const currentUntil = target === 'history' ? _histUntil : _chartUntil;
  let selStart = currentSince ? _localDateISO(currentSince) : '';
  let selEnd = currentUntil ? _localDateISO(currentUntil) : '';

  let viewYear = selStart ? parseInt(selStart.substring(0, 4), 10) : now.getFullYear();
  let viewMonth = selStart ? parseInt(selStart.substring(5, 7), 10) - 1 : now.getMonth();

  const pop = document.createElement('div');
  pop.className = 'date-range-popover';
  pop.setAttribute('role', 'dialog');
  pop.setAttribute('aria-label', 'Select date range');

  const monthNames = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  const dayNames = ['Su','Mo','Tu','We','Th','Fr','Sa'];

  const fmtDate = (iso) => {
    if (!iso) return '';
    const d = new Date(iso + 'T00:00:00');
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  };

  function render() {
    const firstDay = new Date(viewYear, viewMonth, 1).getDay();
    const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
    const selStartMs = selStart ? new Date(selStart + 'T00:00:00').getTime() : 0;
    const selEndMs = selEnd ? new Date(selEnd + 'T00:00:00').getTime() : 0;

    let grid = '';
    dayNames.forEach(d => { grid += `<span class="cal-dow">${d}</span>`; });
    for (let i = 0; i < firstDay; i++) grid += '<span></span>';
    for (let d = 1; d <= daysInMonth; d++) {
      const iso = `${viewYear}-${String(viewMonth + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
      const isToday = iso === todayISO;
      const isOutOfRange = (minISO && iso < minISO) || iso > todayISO;
      const dayMs = new Date(iso + 'T00:00:00').getTime();
      const isStart = iso === selStart;
      const isEnd = iso === selEnd;
      const isInRange = selStart && selEnd && dayMs > selStartMs && dayMs < selEndMs;

      let cls = 'cal-day';
      if (isToday) cls += ' cal-today';
      if (isOutOfRange) cls += ' cal-empty';
      if (isStart) cls += ' cal-range-start';
      if (isEnd) cls += ' cal-range-end';
      if (isInRange) cls += ' cal-in-range';

      grid += `<button class="${cls}" data-drp-date="${iso}" ${isOutOfRange ? 'disabled' : ''}>${d}</button>`;
    }

    const statusText = !selStart ? 'Select start date'
      : !selEnd ? `${fmtDate(selStart)} \u2192 now`
      : `${fmtDate(selStart)} \u2192 ${fmtDate(selEnd)}`;

    pop.innerHTML = `
      <div class="cal-header">
        <button class="cal-nav" data-cal-dir="-1" aria-label="Previous month">&#8249;</button>
        <span class="cal-month">${monthNames[viewMonth]} ${viewYear}</span>
        <button class="cal-nav" data-cal-dir="1" aria-label="Next month">&#8250;</button>
      </div>
      <div class="drp-status${selEnd ? ' drp-status-complete' : ''}">${statusText}</div>
      <div class="cal-grid">${grid}</div>
      <div class="drp-actions">
        <button class="chart-view-pill drp-clear" type="button">Clear</button>
        <button class="chart-view-pill active drp-apply" type="button" ${!selStart ? 'disabled' : ''}>Apply</button>
      </div>
    `;

    pop.querySelector('[data-cal-dir="-1"]')?.addEventListener('click', (e) => {
      e.stopPropagation();
      viewMonth--;
      if (viewMonth < 0) { viewMonth = 11; viewYear--; }
      render();
    });
    pop.querySelector('[data-cal-dir="1"]')?.addEventListener('click', (e) => {
      e.stopPropagation();
      viewMonth++;
      if (viewMonth > 11) { viewMonth = 0; viewYear++; }
      render();
    });

    pop.querySelectorAll('[data-drp-date]:not([disabled])').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const iso = btn.dataset.drpDate;
        if (!selStart || selEnd) {
          selStart = iso;
          selEnd = '';
        } else if (iso < selStart) {
          selEnd = selStart;
          selStart = iso;
        } else {
          selEnd = iso;
        }
        render();
      });
    });

    pop.querySelector('.drp-apply')?.addEventListener('click', (e) => {
      e.stopPropagation();
      if (!selStart) { _closeDateRangePopover(); return; }
      const [sy, sm, sd] = selStart.split('-').map(Number);
      const since = new Date(sy, sm - 1, sd).getTime() / 1000;
      let until = null;
      if (selEnd) {
        const [ey, em, ed] = selEnd.split('-').map(Number);
        until = new Date(ey, em - 1, ed, 23, 59, 59).getTime() / 1000;
      }
      _applyCustomRange(since, until, target);
      _closeDateRangePopover();
    });

    pop.querySelector('.drp-clear')?.addEventListener('click', (e) => {
      e.stopPropagation();
      const _isMobile = window.innerWidth < 640;
      if (target === 'history') _applyHistRange(_isMobile ? '4h' : '3d');
      else _applyChartRange(_isMobile ? '24h' : '7d');
      _closeDateRangePopover();
    });
  }

  render();

  const rect = anchorEl.getBoundingClientRect();
  const popW = 268;
  const isMobile = window.innerWidth < 640;
  if (isMobile) {
    pop.style.cssText = 'position:fixed;bottom:0;left:0;right:0;';
  } else {
    pop.style.cssText = `position:fixed;top:-9999px;left:0;width:${popW}px;`;
  }

  document.body.appendChild(pop);

  if (!isMobile) {
    const popH = pop.offsetHeight;
    let top = rect.bottom + 6;
    let left = rect.left + rect.width / 2 - popW / 2;
    if (top + popH > window.innerHeight - 8) top = rect.top - popH - 6;
    if (top < 8) top = 8;
    if (left + popW > window.innerWidth - 8) left = window.innerWidth - popW - 8;
    if (left < 8) left = 8;
    pop.style.top = `${top}px`;
    pop.style.left = `${left}px`;
  }
  _dateRangePopover = pop;

  _dateRangeClickHandler = (e) => {
    if (_dateRangePopover && !_dateRangePopover.contains(e.target) && e.target !== anchorEl) _closeDateRangePopover();
  };
  setTimeout(() => {
    document.addEventListener('click', _dateRangeClickHandler, { once: true });
  }, 0);
  document.addEventListener('keydown', _dateRangeEscHandler);
}

export function resetRangeState() {
  _closeDateRangePopover();
  _chartSince = null;
  _chartUntil = null;
  _histSince = null;
  _histUntil = null;
  _histRangeKey = null;
  _timeRange = null;
  _fetchSeq = 0;
  _healthBuckets = null;
}

export function initRangeStateForOpen(rangeKey, ranges, availableRanges, isEligible, DEFAULT_HIST_RANGE) {
  _timeRange = rangeKey;
  if (rangeKey === 'custom') {
    const savedSince = localStorage.getItem('mw_chart_since');
    const savedUntil = localStorage.getItem('mw_chart_until');
    _chartSince = savedSince ? Number(savedSince) : null;
    _chartUntil = savedUntil ? Number(savedUntil) : null;
  } else {
    [_chartSince, _chartUntil] = _rangeSinceUntil(rangeKey);
  }
  const savedHistRange = localStorage.getItem('mw_hist_range');
  const savedHistSince = localStorage.getItem('mw_hist_since');
  const savedHistUntil = localStorage.getItem('mw_hist_until');
  _histSince = savedHistSince ? Number(savedHistSince) : null;
  _histUntil = savedHistUntil ? Number(savedHistUntil) : null;
  if (!_histSince && !_histUntil) {
    let histRange = DEFAULT_HIST_RANGE;
    const histR = ranges.find(r => r.key === histRange);
    if (histR && !isEligible(histR)) {
      const candidates = ranges.filter(r => isEligible(r) && r.key !== 'max').sort((a, b) => a.seconds - b.seconds);
      histRange = candidates.length > 0 ? candidates[0].key : 'max';
    }
    _histRangeKey = histRange;
    localStorage.setItem('mw_hist_range', histRange);
    const [histSince, histUntil] = _rangeSinceUntil(histRange);
    _histSince = histSince;
    _histUntil = histUntil;
    _lsSetOrRemove('mw_hist_since', _histSince);
    _lsSetOrRemove('mw_hist_until', _histUntil);
  } else {
    _histRangeKey = savedHistRange || 'custom';
  }
  return { chartSince: _chartSince, chartUntil: _chartUntil, histSince: _histSince, histUntil: _histUntil, histRangeKey: _histRangeKey };
}
