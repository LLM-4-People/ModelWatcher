// DOM rendering: cards, provider sections, badges, status decorations.
// buildCardDOM constructs full innerHTML; updateCardDOM does targeted element
// updates on WS messages. Metric tiles render from last_test only (no fallback).
import { state, recalcCounts, setMetrics, LS } from './state.js';
import { slug, esc, logError, logDebug, logTag, chevronSVG, setHTML, setClass, setText } from './utils.js';
import { tpsColor, ttftColor, uptimeColor, p99ItlColor, scoreColor, trendArrow, trendColor, trendDelta, fmtTps, fmtTTFT, fmtUptime, fmtSeconds, fmtMsCompact, fmtNum, timeAgo, fmtContext, fmtCritical, STATUS_TEXT, degradedDescHTML, recordErrorText, freshnessTextCls, fmtEventTime, fmtSince, metricCellHTML } from './format.js';
import { updateStatusLegend } from './help.js';
import { observeChart, unobserveChartsInContainer, initPendingChartsInContainer, disconnectLazyChartObserver, CHART_VIEWS, getCardView, switchCardView, _fetchMetaClear, chartPhHTML } from './chart.js';
import { fetchProviderMetrics, fetchProviders, fetchModelInfoCapabilities } from './api.js';
import { registerTip } from './tooltips.js';
import { applyFilter, invalidateFilterCache } from './filter.js';

let _scheduleUIFn = null;
export function setScheduleUI(fn) { _scheduleUIFn = fn; }

function _scheduleUI(opts) { if (_scheduleUIFn) _scheduleUIFn(opts); }

let _provTipSeq = 0;
let _scrollObserver = null;
let _pendingFetches = new Set();

function visibleModels(providerSlug) {
  return state.models.filter(e =>
    !providerSlug || slug(e.provider) === providerSlug
  );
}

export function statusDecorState(data) {
  if (!data) data = {};
  const lt = data.last_test || {};
  return {
    isD: data.status === 'degraded' || (lt.degraded && data.status !== 'error'),
    isE: data.status === 'error',
    isUnknown: !data.status || data.status === 'unknown',
    isArchived: !!data.archived,
    isBenchmarkTesting: !!(data.testing && data.testing_type !== 'health'),
  };
}

const _decorCache = new WeakMap();

export function applyStatusDecor(el, data) {
  if (!el) return;
  const { isD, isE, isUnknown, isArchived, isBenchmarkTesting } = statusDecorState(data);
  const glow = isArchived ? 'archived-glow' : isD ? 'degraded-glow' : isE ? 'error-glow' : (!isUnknown ? 'online-glow' : '');
  const pulse = isBenchmarkTesting;
  const prev = _decorCache.get(el);
  if (prev && prev.glow === glow && prev.pulse === pulse) return;
  el.classList.remove('online-glow', 'degraded-glow', 'error-glow', 'archived-glow');
  if (glow) el.classList.add(glow);
  if (pulse) el.classList.add('testing-pulse');
  else el.classList.remove('testing-pulse');
  _decorCache.set(el, { glow, pulse });
}

export function providerName(name, url, extraClasses = '', logoSrc = '', title = '') {
  const img = logoSrc ? `<img src="${esc(logoSrc)}" alt="${esc(name)} logo" class="provider-logo" loading="lazy">` : '';
  const cls = `provider-link transition-colors ${extraClasses}`.trim();
  const text = esc(name);
  let tipAttr = '';
  if (title) {
    const id = `prov-${++_provTipSeq}`;
    registerTip(id, esc(title));
    tipAttr = ` data-tip-id="${id}"`;
  }
  const inner = url
    ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer" class="${cls}"${tipAttr}>${img}${text}</a>`
    : `${img}<span class="${extraClasses}"${tipAttr}>${text}</span>`;
  return inner;
}

function deferredCardsHTML(count) {
  return Array.from({ length: count }, () =>
    '<div class="skeleton-card"></div>'
  ).join('');
}

const _STATUS_ORDER = ['online', 'degraded', 'error', 'testing'];

function _providerScoreBadges(provider) {
  const ps = state.providerSummaries[provider];
  if (!ps) return '';
  const scores = ps.scores;
  if (!scores) return '';
  const trends = ps.trends || {};
  const items = [];
  const tipLines = [];
  const total = ps.total || 0;
  const c = scores.consistency, s = scores.speed, r = scores.reliability;
  const hasAny = c != null || s != null;
  for (const [label, score, trendKey] of [['C', c, 'consistency_score'], ['S', s, 'speed_score'], ['R', r, 'reliability_score']]) {
    const trend = trends[trendKey];
    const item = _scoreItem(label, score, trend, label === 'R' && score == null && hasAny);
    if (item) { items.push(item.html); tipLines.push(item.tipLine); }
  }
  if (!items.length) return '';
  let sinceLine = '';
  if (ps.since_ts != null) {
    sinceLine = `<span class="block text-center w-full text-text-faint">Since ${fmtSince(ps.since_ts * 1000)}</span>`;
  }
  const groupHTML = _scoreGroupHTML(items, tipLines, 'pscores', sinceLine);
  return groupHTML || '';
}

function providerCountBadges(counts, total, slugStr, providerName) {
  const idAttr = slugStr ? ` id="phealth-${slugStr}"` : '';
  const hasAny = _STATUS_ORDER.some(k => counts[k] > 0);
  const scoreHTML = providerName ? _providerScoreBadges(providerName) : '';
  const sep = scoreHTML ? '<span class="provider-score-sep"></span>' : '';
  const archivedCount = providerName ? state.models.filter(e => e.provider === providerName && e.archived).length : 0;
  const archivedBadge = archivedCount > 0 ? `<span class="text-text-faint">${archivedCount}</span>` : '';
  if (!hasAny) {
    if (total === 0 && !scoreHTML && archivedCount === 0) return '';
    const tail = total || '';
    return `<span class="text-xs ml-auto flex items-center gap-2"${idAttr}>${scoreHTML}${tail ? sep : ''}${tail}${archivedBadge}</span>`;
  }
  const badges = _STATUS_ORDER
    .filter(k => counts[k] > 0)
    .map(k => `<span class="${STATUS_TEXT[k]}">${counts[k]}</span>`)
    .join('');
  const ariaParts = _STATUS_ORDER.filter(k => counts[k] > 0).map(k => `${counts[k]} ${k}`);
  if (archivedCount > 0) ariaParts.push(`${archivedCount} archived`);
  const aria = ariaParts.join(', ');
  return `<span class="text-xs ml-auto flex items-center gap-2" aria-label="${aria}"${idAttr}>${scoreHTML}${sep}${badges}${archivedBadge}</span>`;
}

export function _healthErrorIfNewer(data, lt) {
  if (!data?.health_error) return undefined;
  if (data.health_ts_epoch != null && lt.timestamp) {
    return data.health_ts_epoch > new Date(lt.timestamp).getTime() / 1000 ? data.health_error : undefined;
  }
  return data.health_error;
}

export function _eventTimestamp(data, lt) {
  const healthNewer = data?.health_error && data.health_ts_epoch != null && lt.timestamp &&
    data.health_ts_epoch > new Date(lt.timestamp).getTime() / 1000;
  if (healthNewer) return data.health_ts_epoch * 1000;
  return lt.timestamp || null;
}

export function _statusMessage(data, lt) {
  const isRetry = data.retry_attempt && data.retry_total;
  if (!isRetry && data.status !== 'error' && data.status !== 'degraded' && !lt?.degraded) return '';
  const retry = isRetry ? `↻ Retry ${data.retry_attempt}/${data.retry_total}` : '';
  const msg = _healthErrorIfNewer(data, lt) || recordErrorText(lt) ||
    (data.status === 'error' ? 'Health check failed' : (data.status === 'degraded' || lt?.degraded ? 'Performance degraded' : ''));
  return [retry, msg].filter(Boolean).join(' \u2014 ');
}

let _tipIdCounter = 0;

function _scoreItem(label, score, trend, placeholder = false) {
  const fullName = label === 'C' ? 'Consistency' : label === 'S' ? 'Speed' : 'Reliability';
  if (score == null && !placeholder) return null;
  if (placeholder) {
    const html = `<span class="text-text-muted">${label}</span><span class="text-text-faint">--%</span>`;
    const tipLine = `<span class="text-text-muted">${fullName}</span> <span class="text-text-muted">\u00b7</span> <span class="text-text-faint">--%</span>`;
    return { html, tipLine };
  }
  const color = scoreColor(score);
  const arrow = trendArrow(trend);
  const arrowCls = trend ? trendColor(trend) : '';
  const arrowSpan = arrow ? `<span class="score-trend ${arrowCls}">${arrow}</span>` : '';
  const html = `<span class="text-text-muted">${label}</span><span class="${color}">${fmtNum(score, 0)}</span><span class="text-text-muted">%</span>${arrowSpan}`;
  let trendHTML = '';
  if (trend?.direction === 'improving') {
    trendHTML = ` <span class="text-success-400">${trendDelta(trend)}</span>`;
  } else if (trend?.direction === 'degrading') {
    trendHTML = ` <span class="text-danger-400">${trendDelta(trend)}</span>`;
  } else if (trend?.direction === 'stable' && trend?.unit) {
    trendHTML = ` <span class="text-text-muted">${trendDelta(trend)}</span>`;
  }
  const tipLine = `<span class="text-text-muted">${fullName}</span> <span class="text-text-muted">\u00b7</span> <span class="${color}">${fmtNum(score, 0)}%</span>${trendHTML ? ' <span class="text-text-muted">\u00b7</span>' : ''}${trendHTML}`;
  return { html, tipLine };
}

const _SEP = '<span class="score-sep"></span>';

function _scoreGroupHTML(items, tipLines, tipPrefix, headerLine) {
  if (!items.length) return '';
  const tipId = `${tipPrefix}-${++_tipIdCounter}`;
  const content = (headerLine ? headerLine + '<div class="mb-0.5"></div>' : '') + tipLines.join('<br>');
  registerTip(tipId, content);
  return `<span class="score-group" data-tip-id="${tipId}" tabindex="0">${items.join(_SEP)}</span>`;
}

function _trendSinceLine(data) {
  const rangeStart = data.range_start;
  if (rangeStart) {
    return `<span class="block text-center w-full text-text-faint">Since ${fmtSince(rangeStart * 1000)}</span>`;
  }
  const sinceTs = (data.trends || {}).since_ts;
  if (!sinceTs) return '';
  return `<span class="block text-center w-full text-text-faint">Since ${fmtSince(sinceTs * 1000)}</span>`;
}

function scoreBadges(data) {
  const scores = data.scores;
  if (!scores) return '';
  const trends = data.trends || {};
  const items = [];
  const tipLines = [];
  const c = scores.consistency, s = scores.speed, r = scores.reliability;
  const hasAny = c != null || s != null;
  for (const [label, score, trendKey] of [['C', c, 'consistency_score'], ['S', s, 'speed_score'], ['R', r, 'reliability_score']]) {
    const trend = trends[trendKey];
    const item = _scoreItem(label, score, trend, label === 'R' && score == null && hasAny);
    if (item) { items.push(item.html); tipLines.push(item.tipLine); }
  }
  return _scoreGroupHTML(items, tipLines, 'scores', _trendSinceLine(data));
}


function capabilitiesBadge(entry) {
  const allCaps = [
    { key: 'thinking', label: 'Thinking', desc: 'chain-of-thought reasoning' },
    { key: 'supports_vision', label: 'Vision', desc: 'image understanding' },
    { key: 'supports_tools', label: 'Tools', desc: 'function/tool calling' },
    { key: 'supports_cache', label: 'Cache', desc: 'prompt caching' },
    { key: 'supports_structured_output', label: 'JSON', desc: 'structured output' },
  ];
  const caps = allCaps.filter(c => entry[c.key]);
  if (!caps.length) return '';
  const tipId = `caps-${++_tipIdCounter}`;
  const tipHTML = `Model capabilities:<br>` + caps.map(c => `\u2022 ${c.label} \u2014 ${c.desc}`).join('<br>');
  registerTip(tipId, tipHTML);
  return `<span class="badge-chip badge-caps" data-tip-id="${tipId}" tabindex="0"><span class="text-text-secondary">${caps.map(c => c.label).join(', ')}</span></span>`;
}


function offlineBadge(lt, status, data) {
  if (status !== 'error') return '';
  const tipId = `off-${++_tipIdCounter}`;
  const errText = _statusMessage(data, lt) || 'Endpoint unreachable';
  const eventTs = _eventTimestamp(data, lt);
  const tsStr = eventTs ? fmtEventTime(eventTs) : '';
  const tipText = tsStr ? `${tsStr} · ${errText}` : errText;
  registerTip(tipId, esc(tipText));
  return `<span class="badge-chip" data-tip="offline" data-tip-id="${tipId}" tabindex="0"><span class="text-text-muted">\u2717</span><span class="${STATUS_TEXT.error}">Offline</span></span>`;
}

function degradedBadge(lt, status) {
  if (!lt.degraded && status !== 'degraded') return '';
  const tipId = `deg-${++_tipIdCounter}`;
  const desc = degradedDescHTML(lt);
  const tsStr = lt.timestamp ? fmtEventTime(lt.timestamp) : '';
  const tip = tsStr ? `<span class="opacity-60">${esc(tsStr)}</span><br>${desc}` : desc;
  registerTip(tipId, tip);
  return `<span class="badge-chip" data-tip="degraded" data-tip-id="${tipId}" tabindex="0"><span class="text-text-muted">\u26a0</span><span class="${STATUS_TEXT.degraded}">Degraded</span></span>`;
}

function archivedBadge(entry) {
  if (!entry.archived) return '';
  return `<span class="badge-chip badge-archived" data-tip="archived" tabindex="0"><span class="text-text-muted">\u2139</span><span class="text-text-faint">Archived</span></span>`;
}

function topBadges(lt, status, data, entry) {
  const statusBadge = status === 'error' ? offlineBadge(lt, status, data) : degradedBadge(lt, status);
  const caps = capabilitiesBadge(entry || {});
  const archived = archivedBadge(entry || {});
  if (statusBadge) return statusBadge + caps + archived;
  if (archived) return archived + caps;
  return caps;
}

export { topBadges as cardBadges };


export function reliableIndicator(reliable, compact, lt, tipKey = 'itlReliable') {
  if (!reliable) return '';
  if (lt && (lt.degraded || lt.success === false)) return '';
  if (lt && lt.burst_arrival_pct != null && lt.burst_arrival_pct >= 30) return '';
  const cls = compact ? 'text-[10px]' : 'text-xs';
  return `<span class="${cls} text-success-400" data-tip="${tipKey}" tabindex="0"><span class="tip-label">✓</span></span>`;
}

// ── Check status line (single chip with inline last-OK) ─────────────────

const _CHK_SYM = {
  ok:       { ch: '●', cls: 'text-success-400' },
  degraded: { ch: '▲', cls: 'text-warn-400' },
  failed:   { ch: '✗', cls: 'text-danger-400' },
  unknown:  { ch: '●', cls: 'text-text-faint' },
};

function _healthSym(success) {
  if (success === true) return _CHK_SYM.ok;
  if (success === false) return _CHK_SYM.failed;
  return _CHK_SYM.unknown;
}

function _benchSym(lt) {
  if (!lt || lt.success == null) return _CHK_SYM.unknown;
  if (lt.success === false) return _CHK_SYM.failed;
  if (lt.degraded) return _CHK_SYM.degraded;
  return _CHK_SYM.ok;
}

function _checkSlot(sym, label, age, interval, lastOkEpoch) {
  const hasData = age != null && interval > 0;
  const timeText = hasData ? fmtSeconds(age) : '-';
  const timeCls = hasData ? freshnessTextCls(age, interval) : 'text-text-faint';
  const failed = sym === _CHK_SYM.failed;
  let html = `<span class="${sym.cls} text-xs leading-none">${sym.ch}</span>`;
  html += `<span class="text-text-muted text-xs">${label}</span>`;
  html += `<span class="${timeCls} text-xs font-medium">${timeText}</span>`;
  if (failed && lastOkEpoch != null) {
    const okAge = Date.now() / 1000 - lastOkEpoch;
    const okCls = freshnessTextCls(okAge, interval);
    html += `<span class="last-ok"><span class="text-text-faint text-xs">·</span>`;
    html += `<span class="text-text-muted text-xs">OK</span>`;
    html += `<span class="${okCls} text-xs font-medium">${fmtSeconds(okAge)}</span></span>`;
  }
  return html;
}

export function modalCheckLineHTML(data) {
  const lt = data.last_test || {};
  const now = Date.now() / 1000;
  const hInterval = state.healthInterval || 60;
  const bInterval = state.benchmarkInterval || 3600;

  const hAge = data.health_ts_epoch != null ? (now - data.health_ts_epoch) : null;
  const hSym = _healthSym(data.health_success);

  const bAge = data.last_benchmark_epoch != null ? (now - data.last_benchmark_epoch) : null;
  const bSym = _benchSym(lt);

  let slots = '';
  if (state.healthEnabled) {
    slots += _checkSlot(hSym, '<span class="chk-label-full">Health</span><span class="chk-label-short">HC</span>', hAge, hInterval, data.health_success_epoch);
    slots += '<span class="score-sep"></span>';
  }
  slots += _checkSlot(bSym, '<span class="chk-label-full">Bench</span><span class="chk-label-short">BM</span>', bAge, bInterval, data.last_success_epoch);
  if (state.auditEnabled && (data.last_audit_result != null || data.last_audit_epoch != null)) {
    const aInterval = state.auditInterval || 21600;
    const aAge = data.last_audit_epoch != null ? (now - data.last_audit_epoch) : null;
    const ar = data.last_audit_result;
    const aTotal = ar?.total;
    const aPassRate = ar?.pass_rate;
    const aSym = !ar ? _CHK_SYM.unknown : aPassRate == null ? _CHK_SYM.unknown : aTotal === 0 ? _CHK_SYM.degraded : aPassRate >= 1 ? _CHK_SYM.ok : aPassRate > 0 ? _CHK_SYM.degraded : _CHK_SYM.failed;
    slots += '<span class="score-sep"></span>';
    slots += _checkSlot(aSym, '<span class="chk-label-full">Audit</span><span class="chk-label-short">AU</span>', aAge, aInterval, null);
  }

  return `<span class="badge-chip test-line">${slots}</span>`;
}

export function updateTimeAgoLabels() {
  const modalEl = document.getElementById('modal-chk');
  if (modalEl && modalEl.closest('#modal:not(.hidden)')) {
    setHTML(modalEl, modalCheckLineHTML(state.metrics[modalEl.dataset.mwModel] || {}));
  }
  document.querySelectorAll('.notif-item-time[data-ts]').forEach(el => {
    setText(el, timeAgo(el.dataset.ts));
  });
}

function _latestTTFT(data, displayLt, isOffline) {
  if (isOffline) return displayLt.ttft_ms;
  const benchEpoch = data.last_benchmark_epoch || 0;
  const healthEpoch = data.health_ts_epoch || 0;
  if (healthEpoch > benchEpoch && data.health_ttft_ms != null) return data.health_ttft_ms;
  return displayLt.ttft_ms;
}

function _modelInfoLine(entry, safeId) {
  const parts = [];
  const ctxIn = entry.context_window ? fmtContext(entry.context_window) : '';
  const ctxOut = entry.output_context ? fmtContext(entry.output_context) : '';
  if (ctxIn || ctxOut) {
    if (ctxIn && ctxOut && ctxOut !== ctxIn) parts.push(`<span class="text-text-muted">${ctxIn} in</span><span class="text-text-faint/40 mx-0.5">/</span><span class="text-text-muted">${ctxOut} out</span>`);
    else if (ctxIn) parts.push(`<span class="text-text-muted">${ctxIn} ctx</span>`);
    else parts.push(`<span class="text-text-muted">${ctxOut} out</span>`);
  }
  if (entry.quantization) parts.push(`<span class="text-text-muted">${esc(entry.quantization)}</span>`);
  if (entry.param_count && entry.param_count !== '0') parts.push(`<span class="text-text-muted">${esc(entry.param_count)}</span>`);
  if (entry.num_experts) parts.push(`<span class="text-text-muted">MoE</span>`);
  if (!parts.length) return safeId ? `<div id="mi-${safeId}" class="hidden"></div>` : '';
  return `<div id="mi-${safeId}" class="mt-1 text-[10px] text-text-faint flex items-center gap-1 truncate">${parts.join('<span class="text-text-faint/40 mx-0.5">·</span>')}</div>`;
}

function buildCardDOM(entry, data) {
  const lt = data.last_test || {};
  const safeId = slug(entry.id);
  const { isD, isE, isUnknown, isArchived, isBenchmarkTesting } = statusDecorState(data);
  const glowCls = (isBenchmarkTesting ? ' testing-pulse' : '') + (isArchived ? ' archived-glow' : isD ? ' degraded-glow' : isE ? ' error-glow' : isUnknown ? '' : ' online-glow');
  const nameTag = `<span class="font-semibold text-sm cursor-pointer transition-colors truncate">${esc(entry.name)}</span>`;
  const ttftVal = _latestTTFT(data, lt);
  const p99Val = lt.raw_p99_itl_ms;
  const scoreHTML = scoreBadges(data);
  if (entry.description) registerTip(`mi-${safeId}`, esc(entry.description));
  const archivedCls = entry.archived ? ' archived-card' : '';
  return `
  <div id="card-${safeId}" class="min-w-0 bg-raised rounded-xl card-hover fade-in-once cursor-pointer px-3 pt-3 pb-0${glowCls}${archivedCls}" data-model-key="${safeId}" role="button" tabindex="0" aria-label="View details for ${esc(entry.name)}">
     <div class="flex items-start justify-between shrink-0">
      <div class="flex flex-col min-w-0 overflow-hidden"${entry.description ? ` data-tip-id="mi-${safeId}" tabindex="0"` : ''}>
        <div class="flex items-center gap-2">
          ${nameTag}
          <span id="testing-label-${safeId}" class="testing-dots ${isBenchmarkTesting ? 'inline-flex' : 'hidden'}" data-tip="testing" tabindex="0"><span></span><span></span><span></span></span>
        </div>
         <div class="text-xs font-mono text-text-muted ml-0 truncate">${esc(entry.model_id)}</div>
        ${_modelInfoLine(entry, safeId)}
      </div>
      <div id="badges-${safeId}" class="flex flex-col gap-1 shrink-0 items-end">
        ${topBadges(lt, data.status, data, entry)}
      </div>
    </div>
    <div id="scores-${safeId}" class="${scoreHTML ? 'flex justify-center mb-1 mt-1' : 'mb-1'}">${scoreHTML || ''}</div>
    <div class="grid grid-cols-4 gap-2 text-center">
      ${metricCellHTML({ label: 'TTFT', tipKey: 'ttft', colorVar: 'ttft', valueCls: ttftVal != null ? ttftColor(ttftVal) : '', valueHTML: ttftVal != null ? fmtCritical('ttft', ttftVal, fmtTTFT(ttftVal)) : '-', id: `ttft-${safeId}`, wrapperCls: ttftVal == null ? 'hidden' : '' })}
      ${metricCellHTML({ label: 'TPS', tipKey: 'tps', colorVar: 'tps', valueCls: lt.tps != null ? tpsColor(lt.tps) : '', valueHTML: lt.tps != null ? fmtCritical('tps', lt.tps, fmtTps(lt.tps)) : '-', id: `tps-${safeId}`, wrapperCls: lt.tps == null ? 'hidden' : '' })}
      ${metricCellHTML({ label: 'P99 ITL', tipKey: 'p99Itl', colorVar: 'tails', valueCls: p99Val != null ? p99ItlColor(p99Val) : '', valueHTML: p99Val != null ? fmtCritical('raw_p99_itl_ms', p99Val, fmtMsCompact(p99Val)) : '-', id: `p99-${safeId}`, wrapperCls: p99Val == null ? 'hidden' : '' })}
      ${metricCellHTML({ label: 'Uptime', tipKey: 'uptime', colorVar: 'uptime', valueCls: data.uptime_pct != null ? uptimeColor(data.uptime_pct) : '', valueHTML: data.uptime_pct != null ? fmtCritical('uptime', data.uptime_pct, fmtUptime(data.uptime_pct)) : '-', id: `up-${safeId}`, wrapperCls: data.uptime_pct == null ? 'hidden' : '' })}
    </div>
      <div class="h-36 relative mb-3"><canvas id="chart-${safeId}" class="w-full h-full" width="300" height="144"></canvas>${chartPhHTML('chart-' + safeId, isArchived ? 'No data' : (data.data_start_epoch ? 'No data' : 'No data yet'))}</div>
  </div>`;
}

export function updateCardDOM(modelId) {
  const entry = state._modelMap[modelId];
  if (!entry) return;
  const data = state.metrics[modelId] || {};
  const lt = data.last_test || {};
  const safeId = slug(entry.id);
  const card = document.getElementById(`card-${safeId}`);
  if (!card) return;

  const { isBenchmarkTesting } = statusDecorState(data);

  applyStatusDecor(card, data);

  const testingLabel = document.getElementById(`testing-label-${safeId}`);
  if (testingLabel) { testingLabel.classList.toggle('hidden', !isBenchmarkTesting); testingLabel.classList.toggle('inline-flex', isBenchmarkTesting); }

  setHTML(document.getElementById(`badges-${safeId}`), topBadges(lt, data.status, data, entry));

  const scoresEl = document.getElementById(`scores-${safeId}`);
  if (scoresEl) {
    const sg = scoreBadges(data);
    scoresEl.className = sg ? 'flex justify-center mb-1 mt-1' : 'mb-1';
    setHTML(scoresEl, sg || '');
  }

  const isE = data.status === 'error';

  const ttftVal = _latestTTFT(data, lt, isE);
  const ttftEl = document.getElementById(`ttft-${safeId}`);
  if (ttftEl) { const w = ttftEl.parentElement; if (ttftVal != null) { if (w) w.classList.remove('hidden'); setClass(ttftEl, `text-base font-bold ${ttftColor(ttftVal)}`); setHTML(ttftEl, fmtCritical('ttft', ttftVal, fmtTTFT(ttftVal))); } else { if (w) w.classList.add('hidden'); } }

  const tpsEl = document.getElementById(`tps-${safeId}`);
  if (tpsEl) { const w = tpsEl.parentElement; if (lt.tps != null) { if (w) w.classList.remove('hidden'); setClass(tpsEl, `text-base font-bold ${tpsColor(lt.tps)}`); setHTML(tpsEl, fmtCritical('tps', lt.tps, fmtTps(lt.tps))); } else { if (w) w.classList.add('hidden'); } }

  const upEl = document.getElementById(`up-${safeId}`);
  if (upEl) { const w = upEl.parentElement; if (data.uptime_pct != null) { if (w) w.classList.remove('hidden'); setClass(upEl, `text-base font-bold ${uptimeColor(data.uptime_pct)}`); setHTML(upEl, fmtCritical('uptime', data.uptime_pct, fmtUptime(data.uptime_pct))); } else { if (w) w.classList.add('hidden'); } }

  const p99Val = lt.raw_p99_itl_ms;
  const p99El = document.getElementById(`p99-${safeId}`);
  if (p99El) { const w = p99El.parentElement; if (p99Val != null) { if (w) w.classList.remove('hidden'); setClass(p99El, `text-base font-bold ${p99ItlColor(p99Val)}`); setHTML(p99El, fmtCritical('raw_p99_itl_ms', p99Val, fmtMsCompact(p99Val))); } else { if (w) w.classList.add('hidden'); } }

  const miEl = document.getElementById(`mi-${safeId}`);
  if (miEl) {
    if (entry.description) registerTip(`mi-${safeId}`, esc(entry.description));
    const miHTML = _modelInfoLine(entry, safeId);
    miEl.outerHTML = miHTML;
  }
}


export function renderSchedule() {
  const el = document.getElementById('schedule-info');
  if (!el) return;
  const parts = [];
  if (state.healthEnabled && state.healthInterval) parts.push(`\u23f1 Health: ${fmtSeconds(state.healthInterval)}`);
  parts.push(`Bench: ${fmtSeconds(state.benchmarkInterval || 3600)}`);
  if (state.auditEnabled && state.auditInterval) parts.push(`Audit: ${fmtSeconds(state.auditInterval)}`);
  setHTML(el, parts.join(' \u00b7 '));
}

function _collapsedProviders() {
  try { return JSON.parse(localStorage.getItem(LS.COLLAPSED) || '[]'); } catch (e) { logError(logTag('DOM', '←', 'Error', 'CollapsedState'), e); return []; }
}
function _saveCollapsed(arr) {
  localStorage.setItem(LS.COLLAPSED, JSON.stringify(arr));
}

function _deferProviderCards(providerSlug) {
  const content = document.getElementById(`content-${providerSlug}`);
  if (!content) return;
  const grid = content.querySelector('.grid');
  if (!grid) return;
  const cardCount = grid.querySelectorAll('[data-model-key]').length;
  if (cardCount === 0) return;
  unobserveChartsInContainer(grid);
  for (const [id, chart] of Object.entries(state.charts)) {
    const canvas = chart.canvas;
    if (canvas && grid.contains(canvas)) {
      chart.destroy();
      delete state.charts[id];
    }
  }
  const providerName = state.providerOrder.find(p => slug(p) === providerSlug);
  if (providerName) state.fetchedProviders.delete(providerName);
  grid.dataset.deferred = cardCount;
  grid.innerHTML = deferredCardsHTML(cardCount);
}

const _STALE_THRESHOLD = 5 * 60 * 1000;

async function _fetchAndRenderProvider(providerName, providerSlug, contentEl) {
  if (_pendingFetches.has(providerName)) return;
  if (state.fetchedProviders.has(providerName)) return;

  const providerModels = state.models.filter(e => e.provider === providerName);
  const hasMetrics = providerModels.some(e => state.metrics[e.id]?.status);
  const dataAge = Date.now() - (state._providerDataAt[providerName] || 0);
  if (hasMetrics && state._modelCaps && dataAge < _STALE_THRESHOLD) {
    logDebug(logTag('DOM', '→', 'LazyRender', 'Provider', providerName));
    state.fetchedProviders.add(providerName);
    _renderProviderCards(providerSlug, contentEl);
    initPendingChartsInContainer(contentEl);
    _scheduleUI({ models: providerModels.map(e => e.id), providers: true });
    return;
  }

  _pendingFetches.add(providerName);
  logDebug(logTag('DOM', '→', 'LazyFetch', 'Provider', providerName));
  const fetches = [fetchProviderMetrics([providerName], { cardBuckets: true })];
  if (!state._modelCaps) fetches.push(fetchModelInfoCapabilities());
  const [metricsData, capsData] = await Promise.all(fetches);
  _pendingFetches.delete(providerName);
  if (capsData) mergeModelInfo(capsData);
  if (!metricsData) return;
  setMetrics(metricsData);
  state.fetchedProviders.add(providerName);
  state._providerDataAt[providerName] = Date.now();
  _renderProviderCards(providerSlug, contentEl);
  initPendingChartsInContainer(contentEl);
  _scheduleUI({ models: Object.keys(metricsData), providers: true });
}

export function initScrollObserver() {
  if (_scrollObserver) _scrollObserver.disconnect();
  const collapsed = _collapsedProviders();
  _scrollObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      const sec = entry.target.closest('.provider-section');
      if (!sec) continue;
      const providerSlug = sec.dataset.providerSlug;
      const providerName = state.providerOrder.find(p => slug(p) === providerSlug);
      if (!providerName) continue;
      const content = sec.querySelector('.provider-content');
      _fetchAndRenderProvider(providerName, providerSlug, content);
      _scrollObserver.unobserve(sec);
    }
  }, { rootMargin: '400px 0px' });
  document.querySelectorAll('.provider-section').forEach(sec => {
    const providerSlug = sec.dataset.providerSlug;
    const providerName = state.providerOrder.find(p => slug(p) === providerSlug);
    if (providerName && !state.fetchedProviders.has(providerName) && !collapsed.includes(providerSlug)) {
      _scrollObserver.observe(sec);
    }
  });
}

function _renderProviderCards(providerSlug, contentEl) {
  const grid = contentEl?.querySelector('.grid');
  if (!grid) return;
  delete grid.dataset.deferred;
  _rebuildGridCards(grid, providerSlug);
  // Honor any active filter on the freshly rendered cards (hidden state, count).
  applyFilter();
}

function _rebuildGridCards(grid, providerSlug) {
  unobserveChartsInContainer(grid);
  for (const [id, chart] of Object.entries(state.charts)) {
    if (chart.canvas && grid.contains(chart.canvas)) {
      chart.destroy();
      delete state.charts[id];
    }
  }
  const entries = visibleModels(providerSlug);
  grid.innerHTML = entries.map(entry => buildCardDOM(entry, state.metrics[entry.id] || {})).join('');
  _fetchMetaClear();
  for (const entry of entries) observeChart(`chart-${slug(entry.id)}`, entry.id);
}

export function toggleProvider(providerSlug) {
  logDebug(logTag('DOM', '→', 'Toggle', 'Provider', providerSlug));
  let collapsed = _collapsedProviders();
  const idx = collapsed.indexOf(providerSlug);
  if (idx === -1) collapsed.push(providerSlug); else collapsed.splice(idx, 1);
  _saveCollapsed(collapsed);
  applyProviderCollapse();
}

export function toggleAllProviders(action) {
  if (action === 'expand-all') {
    _saveCollapsed([]);
    const unfetched = state.providerOrder.filter(p => !state.fetchedProviders.has(p));
    if (!state._modelCaps) {
      fetchModelInfoCapabilities().then(d => {
        if (d) mergeModelInfo(d);
      }).catch(e => logError(logTag('DOM', '←', 'Error', 'ModelInfoCaps'), e));
    }
    if (unfetched.length > 0) {
        fetchProviderMetrics(unfetched, { cardBuckets: true }).then(data => {
          if (!data) return;
          setMetrics(data);
          const now = Date.now();
          for (const p of unfetched) { state.fetchedProviders.add(p); state._providerDataAt[p] = now; }
        applyProviderCollapse();
        _scheduleUI({ models: Object.keys(data), providers: true });
      }).catch(e => logError(logTag('DOM', '←', 'Error', 'ExpandAllFetch'), e));
    }
  } else if (action === 'collapse-all') {
    _saveCollapsed([...document.querySelectorAll('.provider-toggle[aria-controls]')].map(b => b.getAttribute('aria-controls').replace('content-', '')));
  }
  applyProviderCollapse();
}

function applyProviderCollapse() {
  const collapsed = _collapsedProviders();
  document.querySelectorAll('.provider-section').forEach(sec => {
    const providerSlug = sec.dataset.providerSlug;
    const btn = sec.querySelector('.provider-toggle');
    const content = sec.querySelector('.provider-content');
    const isCollapsed = collapsed.includes(providerSlug);
    if (btn) btn.setAttribute('aria-expanded', String(!isCollapsed));
    if (content) {
      const wasCollapsed = content.classList.contains('collapsed');
      if (isCollapsed) {
        if (!wasCollapsed) {
          content.classList.add('collapsed');
          setTimeout(() => { if (content.classList.contains('collapsed')) _deferProviderCards(providerSlug); }, 200);
        }
      } else {
        if (wasCollapsed) {
          content.classList.remove('collapsed');
          const providerName = state.providerOrder.find(p => slug(p) === providerSlug);
          const hasDeferred = content.querySelector('.grid[data-deferred]');
          if (providerName && !state.fetchedProviders.has(providerName)) {
            _fetchAndRenderProvider(providerName, providerSlug, content);
          } else if (hasDeferred) {
            _renderProviderCards(providerSlug, content);
            initPendingChartsInContainer(content);
          } else {
            initPendingChartsInContainer(content);
          }
        }
      }
    }
  });
  const el = document.getElementById('provider-toggles');
  if (el) {
    const total = document.querySelectorAll('.provider-section').length;
    if (total < 2) { el.innerHTML = ''; }
    else if (collapsed.length >= total) { el.innerHTML = '<button class="provider-toggle-all" data-action="expand-all">Expand all</button>'; }
    else if (collapsed.length === 0) { el.innerHTML = '<button class="provider-toggle-all" data-action="collapse-all">Collapse all</button>'; }
    else { el.innerHTML = '<button class="provider-toggle-all" data-action="expand-all">Expand all</button><button class="provider-toggle-all" data-action="collapse-all">Collapse all</button>'; }
  }
}

function renderChartViewPills() {
  const el = document.getElementById('chart-view-pills');
  if (!el) return;
  const current = getCardView();
  el.innerHTML = CHART_VIEWS.map(v =>
    `<button class="chart-view-pill${v.key === current ? ' active' : ''}" data-card-view="${v.key}" data-tip="${v.tip}" tabindex="0">${v.label}</button>`
  ).join('');
  el.querySelectorAll('[data-card-view]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const view = btn.dataset.cardView;
      if (view) switchCardView(view);
    });
  });
}

function _providerSectionHTML(provider, entries, m, collapsed) {
  const providerSlug = slug(provider);
  const isCollapsed = collapsed.includes(providerSlug);
  const isFetched = state.fetchedProviders.has(provider);
  const isDeferred = isCollapsed || !isFetched;
  const ps = state.providerSummaries[provider];
  const counts = ps?.counts || { online: 0, degraded: 0, error: 0, testing: 0 };
  const total = ps?.total ?? entries.filter(e => !e.archived).length;
  const gridContent = isDeferred
    ? deferredCardsHTML(entries.length)
    : entries.map(entry => buildCardDOM(entry, m[entry.id] || {})).join('');
  const gridAttr = isDeferred ? ` data-deferred="${entries.length}"` : '';
  const url = state.providerUrls[provider];
  return `
  <div class="mb-2 provider-section rounded-xl" data-provider-slug="${providerSlug}" id="section-${providerSlug}">
    <div class="provider-header" id="header-${providerSlug}" data-provider-slug="${providerSlug}">
      <button class="provider-toggle" aria-expanded="${!isCollapsed}" aria-controls="content-${providerSlug}" aria-label="Toggle ${esc(provider)} models" tabindex="0">
        ${chevronSVG('provider-chevron', 16)}
      </button>
      ${providerName(provider, url, 'text-sm font-semibold text-text-secondary uppercase tracking-wider', state.providerLogos[provider], state.providerTitles[provider])}
      ${providerCountBadges(counts, total, providerSlug, provider)}
    </div>
    <div id="content-${providerSlug}" class="provider-content${isCollapsed ? ' collapsed' : ''}" role="region" aria-labelledby="header-${providerSlug}">
      <div class="provider-inner">
        <div class="model-grid grid gap-2.5 pt-2 pl-2 pr-1.5" style="grid-template-columns:repeat(auto-fill,minmax(340px,1fr))"${gridAttr}>
          ${gridContent}
        </div>
      </div>
    </div>
  </div>`;
}

export function buildProviderSections() {
  for (const key in _phealthCache) delete _phealthCache[key];
  for (const key in state.charts) {
    if (state.charts[key]) state.charts[key].destroy();
  }
  state.charts = {};
  disconnectLazyChartObserver();
  const container = document.getElementById('provider-sections');
  if (!container) return;
  const m = state.metrics;
  const grouped = {};
  for (const entry of visibleModels()) {
    if (!grouped[entry.provider]) grouped[entry.provider] = [];
    grouped[entry.provider].push(entry);
  }
  const order = state.providerOrder.length ? state.providerOrder : Object.keys(grouped);
  const collapsed = _collapsedProviders();

  container.innerHTML = order.map(provider => {
    const entries = grouped[provider];
    return entries ? _providerSectionHTML(provider, entries, m, collapsed) : '';
  }).join('');

  applyProviderCollapse();

  for (const entry of visibleModels()) {
    const providerSlug = slug(entry.provider);
    if (collapsed.includes(providerSlug)) continue;
    if (!state.fetchedProviders.has(entry.provider)) continue;
    observeChart(`chart-${slug(entry.id)}`, entry.id);
  }

  const skel = document.getElementById('skeleton');
  if (skel) skel.remove();
  renderChartViewPills();
  applyFilter();
}

const _phealthCache = {};

export function updateProviderCounts(changedModelId) {
  const providers = changedModelId
    ? [state._modelMap[changedModelId]?.provider].filter(Boolean)
    : Object.keys(state.providerSummaries);
  for (const provider of providers) {
    const ps = state.providerSummaries[provider];
    if (!ps) continue;
    const counts = ps.counts || { online: 0, degraded: 0, error: 0, testing: 0 };
    const providerSlug = slug(provider);
    const html = providerCountBadges(counts, ps.total || 0, providerSlug, provider);
    if (!html) { const el = document.getElementById(`phealth-${providerSlug}`); el?.remove(); delete _phealthCache[providerSlug]; continue; }
    if (html !== _phealthCache[providerSlug]) {
      _phealthCache[providerSlug] = html;
      const el = document.getElementById(`phealth-${providerSlug}`);
      if (el) {
        const tmp = document.createElement('span');
        tmp.innerHTML = html;
        const replacement = tmp.firstElementChild;
        if (replacement) el.replaceWith(replacement);
      }
    }
  }
}

const _BASE_MODEL_KEYS = new Set(['id', 'provider', 'model_id', 'name', 'hf_id', 'api_url']);
// Delivered by /api/providers on every fetch - absence means "not set", so never carry stale values forward
const _REFRESHED_MODEL_KEYS = new Set(['archived']);

export function applyProvidersData(providers) {
  const oldMap = state._modelMap;
  state.providerOrder = Object.keys(providers || {}).sort((a, b) => a.localeCompare(b));
  state.providerUrls = {};
  state.providerLogos = {};
  state.providerTitles = {};
  state.models = [];
  state._modelMap = {};
  if (providers) for (const name of state.providerOrder) {
    const p = providers[name];
    state.providerUrls[name] = p.api_url;
    state.providerLogos[name] = p.logo;
    state.providerTitles[name] = p.title;
    if (p.models) for (const m of p.models) {
      const old = oldMap?.[m.id];
      if (old) for (const k of Object.keys(old)) { if (!_BASE_MODEL_KEYS.has(k) && !_REFRESHED_MODEL_KEYS.has(k) && m[k] === undefined) m[k] = old[k]; }
      state.models.push(m);
      state._modelMap[m.id] = m;
    }
  }
  state.models.sort((a, b) => a.name.localeCompare(b.name));
  invalidateFilterCache(); // names/provider/model_id may have changed → drop search haystacks
  recalcCounts();
  updateStatusLegend();
}

export function mergeModelInfo(caps) {
  if (!caps) return;
  if (!state._modelCaps) state._modelCaps = {};
  Object.assign(state._modelCaps, caps);
  for (const [mk, fields] of Object.entries(caps)) {
    const existing = state._modelMap[mk];
    if (existing) Object.assign(existing, fields);
  }
  // Spec fields (context_window, param_count, capabilities) may have changed,
  // so a previously filtered-out card could now match (or vice-versa).
  applyFilter();
}

export function modelKeys() {
  return new Set(state.models.map(m => m.id));
}

export async function refreshModelList({ rebuild = true } = {}) {
  try {
  const [providers, caps] = await Promise.all([
    fetchProviders(null),
    fetchModelInfoCapabilities(),
  ]);
  if (!providers) return null;
  applyProvidersData(providers);
  if (caps) mergeModelInfo(caps);
  if (rebuild) buildProviderSections();
  const warnEl = document.getElementById('config-warning');
  if (warnEl) warnEl.classList.add('hidden');
  return providers;
  } catch (e) { logError(logTag('DOM', '←', 'Error', 'ModelList'), e); return null; }
}
