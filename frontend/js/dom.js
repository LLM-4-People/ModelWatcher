// DOM rendering: cards, provider sections, badges, status decorations.
// buildCardDOM constructs full innerHTML; updateCardDOM does targeted element
// updates on WS messages. Metric tiles come from format.js METRIC_TILES, shared with the modal.
import { state, recalcCounts, setMetrics, LS, countByStatus } from './state.js';
import { slug, esc, logError, logDebug, logTag, chevronSVG, setHTML, setText, STATUS_GLYPH, SEP_TEXT, sepHTML, segmentsHTML } from './utils.js';
import { scoreColor, trendArrow, trendColor, trendDelta, fmtNum, fmtSeconds, timeAgo, fmtContext, STATUS_TEXT, degradedDescHTML, recordErrorText, freshnessTextCls, fmtEventTime, fmtSince, metricTileHTML, testTypeLabel, statusLabel, metricLabel, modelCapabilities, capabilityLinesHTML, chartViews, lastBenchmarkFailed, TESTING_LABEL, secondaryModelId } from './format.js';
import { updateStatusLegend } from './help.js';
import { observeChart, unobserveChartsInContainer, initPendingChartsInContainer, disconnectLazyChartObserver, getCardView, switchCardView, _fetchMetaClear, chartPhHTML } from './chart.js';
import { fetchProviderMetrics, fetchProviders, fetchModelInfoCapabilities } from './api.js';
import { registerTip } from './tooltips.js';
import { applyFilter, invalidateFilterCache, entryVisible, onFilterApplied } from './filter.js';

let _scheduleUIFn = null;
export function setScheduleUI(fn) { _scheduleUIFn = fn; }

function _scheduleUI(opts) { if (_scheduleUIFn) _scheduleUIFn(opts); }

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
    const id = `tip-prov-${slug(name)}`;
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

function _providerScoreBadges(provider) {
  const ps = state.providerSummaries[provider];
  if (!ps) return '';
  const scores = ps.scores;
  if (!scores) return '';
  const trends = ps.trends || {};
  const items = [];
  const tipLines = [];
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
  const groupHTML = _scoreGroupHTML(items, tipLines, `tip-pscores-${slug(provider)}`, sinceLine);
  return groupHTML || '';
}

// The counts a provider header shows: its models on screen, so a filter narrows them like the
// cards (finding F69: they were the server's unfiltered totals)
function providerCounts(provider) {
  const entries = state.models.filter(e => e.provider === provider && !e.archived && entryVisible(e));
  return countByStatus(entries, state.metrics);
}

// Each count next to its label ("4 Online"), one list the header and screen readers read alike
function providerCountBadges(provider) {
  const slugStr = slug(provider);
  const counts = providerCounts(provider);
  const items = [...state.statusValues.map(s => [s, statusLabel(s)]), ['testing', TESTING_LABEL]]
    .filter(([k]) => counts[k] > 0)
    .map(([k, label]) => segmentsHTML([`<span class="${STATUS_TEXT[k]}">${counts[k]}</span>`, `<span class="text-text-muted">${esc(label)}</span>`], { cls: 'provider-count' }));
  const archivedCount = state.models.filter(e => e.provider === provider && e.archived).length;
  if (archivedCount > 0) items.push(segmentsHTML([`<span>${archivedCount}</span>`, '<span>Archived</span>'], { cls: 'provider-count text-text-faint' }));
  const scoreHTML = _providerScoreBadges(provider);
  if (!items.length && !scoreHTML) return '';
  const countsHTML = items.length ? segmentsHTML(items, { sep: 'dot', cls: 'provider-counts' }) : '';
  return `<span class="text-xs ml-auto flex items-center gap-2" id="phealth-${slugStr}">${scoreHTML}${scoreHTML && countsHTML ? sepHTML('rule') : ''}${countsHTML}</span>`;
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
  return [retry, msg].filter(Boolean).join(SEP_TEXT);
}

// Tip ids are stable per model or provider: a redraw replaces the tip instead of adding one

const _SCORE_METRIC = { C: 'consistency_score', S: 'speed_score', R: 'reliability' };

function _scoreItem(label, score, trend, placeholder = false) {
  const fullName = esc(metricLabel(_SCORE_METRIC[label]));
  if (score == null && !placeholder) return null;
  if (placeholder) {
    const html = `<span class="text-text-muted">${label}</span><span class="text-text-faint">--%</span>`;
    const tipLine = `<span class="text-text-muted">${fullName}${SEP_TEXT}</span><span class="text-text-faint">--%</span>`;
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
  const tipLine = `<span class="text-text-muted">${fullName}${SEP_TEXT}</span><span class="${color}">${fmtNum(score, 0)}%</span>${trendHTML ? `<span class="text-text-muted">${SEP_TEXT}</span>` : ''}${trendHTML}`;
  return { html, tipLine };
}

function _scoreGroupHTML(items, tipLines, tipId, headerLine) {
  if (!items.length) return '';
  const content = (headerLine ? headerLine + '<div class="mb-0.5"></div>' : '') + tipLines.join('<br>');
  registerTip(tipId, content);
  return `<span class="score-group" data-tip-id="${tipId}">${items.join(sepHTML('rule'))}</span>`;
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

function scoreBadges(data, modelId) {
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
  return _scoreGroupHTML(items, tipLines, `tip-scores-${slug(modelId)}`, _trendSinceLine(data));
}


// Card and modal badges. Inside a card they are no tab stops (the card's button opens the
// details, finding F74); the modal's copies are focusable for their tips.
const _focusAttr = focusable => (focusable ? ' tabindex="0"' : '');

function capabilitiesBadge(entry, focusable) {
  const caps = modelCapabilities(entry);
  if (!caps.length) return '';
  const tipId = `tip-caps-${slug(entry.id)}`;
  registerTip(tipId, `Model capabilities:<br>${capabilityLinesHTML(caps)}`);
  return `<span class="badge-chip badge-caps" data-tip-id="${tipId}"${_focusAttr(focusable)}><span class="text-text-secondary">${caps.map(c => esc(c.label)).join(', ')}</span></span>`;
}


function offlineBadge(lt, status, data, focusable, modelId) {
  if (status !== 'error') return '';
  const tipId = `tip-off-${slug(modelId)}`;
  const errText = _statusMessage(data, lt) || 'Endpoint unreachable';
  const eventTs = _eventTimestamp(data, lt);
  const tsStr = eventTs ? fmtEventTime(eventTs) : '';
  const tipText = tsStr ? `${tsStr}${SEP_TEXT}${errText}` : errText;
  registerTip(tipId, esc(tipText));
  return `<span class="badge-chip" data-tip="error" data-tip-id="${tipId}"${_focusAttr(focusable)}><span class="text-text-muted">${STATUS_GLYPH.failed}</span><span class="${STATUS_TEXT.error}">${esc(statusLabel('error'))}</span></span>`;
}

// Degraded because of a failed last benchmark (health checks pass): the error is the reason (F59)
function _degradedTipHTML(lt) {
  if (lt.success === false) return `Last benchmark failed:<br>${esc(recordErrorText(lt) || 'no response')}`;
  return degradedDescHTML(lt);
}

function degradedBadge(lt, status, focusable, modelId) {
  if (!lt.degraded && status !== 'degraded') return '';
  const tipId = `tip-deg-${slug(modelId)}`;
  const desc = _degradedTipHTML(lt);
  const tsStr = lt.timestamp ? fmtEventTime(lt.timestamp) : '';
  const tip = tsStr ? `<span class="opacity-60">${esc(tsStr)}</span><br>${desc}` : desc;
  registerTip(tipId, tip);
  return `<span class="badge-chip" data-tip="degraded" data-tip-id="${tipId}"${_focusAttr(focusable)}><span class="text-text-muted">${STATUS_GLYPH.degraded}</span><span class="${STATUS_TEXT.degraded}">${esc(statusLabel('degraded'))}</span></span>`;
}

function archivedBadge(entry, focusable) {
  if (!entry.archived) return '';
  return `<span class="badge-chip badge-archived" data-tip="archived"${_focusAttr(focusable)}><span class="text-text-muted">\u2139</span><span class="text-text-faint">Archived</span></span>`;
}

function topBadges(lt, status, data, entry, { focusable = false } = {}) {
  const statusBadge = status === 'error' ? offlineBadge(lt, status, data, focusable, entry.id) : degradedBadge(lt, status, focusable, entry.id);
  const caps = capabilitiesBadge(entry, focusable);
  const archived = archivedBadge(entry, focusable);
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
  return `<span class="${cls} text-success-400" data-tip="${tipKey}" tabindex="0"><span class="tip-label">${STATUS_GLYPH.ok}</span></span>`;
}

// ── Check status line (single chip with inline last-OK) ─────────────────

const _CHK_SYM = {
  ok:       { ch: STATUS_GLYPH.ok, cls: 'text-success-400' },
  degraded: { ch: STATUS_GLYPH.degraded, cls: 'text-warn-400' },
  failed:   { ch: STATUS_GLYPH.failed, cls: 'text-danger-400' },
  unknown:  { ch: STATUS_GLYPH.unknown, cls: 'text-text-faint' },
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

function _auditSym(ar) {
  if (!ar || ar.pass_rate == null) return _CHK_SYM.unknown;
  if (ar.total === 0) return _CHK_SYM.degraded;
  if (ar.pass_rate >= 1) return _CHK_SYM.ok;
  return ar.pass_rate > 0 ? _CHK_SYM.degraded : _CHK_SYM.failed;
}

// Full label on wide screens, short one on phones (CSS picks which shows)
function _testTypeLabelHTML(type) {
  if (!testTypeLabel(type)) return '';
  return `<span class="text-text-muted"><span class="chk-label-full">${esc(testTypeLabel(type))}</span><span class="chk-label-short">${esc(testTypeLabel(type, 'short'))}</span></span>`;
}

// Age colored by freshness against the check interval; without a known interval the age shows uncolored
function _ageHTML(age, interval) {
  if (age == null) return '<span class="text-text-faint font-medium">-</span>';
  const cls = interval > 0 ? freshnessTextCls(age, interval) : 'text-text-faint';
  return `<span class="${cls} font-medium">${fmtSeconds(age)}</span>`;
}

function _checkSlot(sym, type, age, interval, lastOkEpoch) {
  const parts = [`<span class="${sym.cls} leading-none">${sym.ch}</span>`, _testTypeLabelHTML(type), _ageHTML(age, interval)];
  if (sym === _CHK_SYM.failed && lastOkEpoch != null) {
    // The separator lives inside the group, so hiding .last-ok on phones leaves no orphan dot
    parts.push(segmentsHTML([sepHTML('dot'), '<span class="text-text-muted">OK</span>', _ageHTML(Date.now() / 1000 - lastOkEpoch, interval)], { cls: 'last-ok' }));
  }
  return segmentsHTML(parts);
}

export function modalCheckLineHTML(data) {
  const lt = data.last_test || {};
  const now = Date.now() / 1000;
  const age = epoch => (epoch != null ? now - epoch : null);
  const slots = [];
  if (state.healthEnabled) slots.push(_checkSlot(_healthSym(data.health_success), 'health', age(data.health_ts_epoch), state.healthInterval, data.health_success_epoch));
  slots.push(_checkSlot(_benchSym(lt), 'benchmark', age(data.last_benchmark_epoch), state.benchmarkInterval, data.last_success_epoch));
  if (state.auditEnabled && (data.last_audit_result != null || data.last_audit_epoch != null)) {
    slots.push(_checkSlot(_auditSym(data.last_audit_result), 'audit', age(data.last_audit_epoch), state.auditInterval, null));
  }
  return segmentsHTML(slots, { sep: 'rule', cls: 'badge-chip test-line', attrs: 'data-tip="checkLine" tabindex="0"' });
}

// The only writer of #modal-chk: open, live update and the periodic age refresh all come through here
export function renderModalCheckLine(modelId) {
  const el = document.getElementById('modal-chk');
  if (!el) return;
  el.dataset.mwModel = modelId;
  setHTML(el, modalCheckLineHTML(state.metrics[modelId] || {}));
}

export function updateTimeAgoLabels() {
  const modalEl = document.getElementById('modal-chk');
  if (modalEl && modalEl.closest('#modal:not(.hidden)')) renderModalCheckLine(modalEl.dataset.mwModel);
  document.querySelectorAll('.notif-item-time[data-ts]').forEach(el => {
    setText(el, timeAgo(el.dataset.ts));
  });
}

// The card's four tiles, the same definitions and values as the modal's (finding F58)
const _CARD_TILES = ['ttft', 'tps', 'p99', 'uptime'];

function _cardTilesHTML(data) {
  return _CARD_TILES.map(key => metricTileHTML(key, data, { form: 'short' })).join('');
}

function _modelInfoLine(entry, safeId) {
  const parts = [];
  const ctxIn = entry.context_window ? fmtContext(entry.context_window) : '';
  const ctxOut = entry.output_context ? fmtContext(entry.output_context) : '';
  if (ctxIn || ctxOut) {
    if (ctxIn && ctxOut && ctxOut !== ctxIn) parts.push(segmentsHTML([`<span class="text-text-muted">${ctxIn} in</span>`, `<span class="text-text-muted">${ctxOut} out</span>`], { sep: 'slash' }));
    else if (ctxIn) parts.push(`<span class="text-text-muted">${ctxIn} ctx</span>`);
    else parts.push(`<span class="text-text-muted">${ctxOut} out</span>`);
  }
  if (entry.quantization) parts.push(`<span class="text-text-muted">${esc(entry.quantization)}</span>`);
  if (entry.param_count && entry.param_count !== '0') parts.push(`<span class="text-text-muted">${esc(entry.param_count)}</span>`);
  if (entry.num_experts) parts.push(`<span class="text-text-muted">MoE</span>`);
  if (!parts.length) return safeId ? `<div id="mi-${safeId}" class="hidden"></div>` : '';
  return `<div id="mi-${safeId}" class="mt-1 text-[10px] text-text-faint truncate">${segmentsHTML(parts, { sep: 'dot' })}</div>`;
}

// A card is an article whose heading is the button that opens the model's details; everything
// else in it is hover detail, not a tab stop (finding F74: cards were role=button with nested
// focusables, and nothing below the h1 was a heading)
function buildCardDOM(entry, data) {
  const safeId = slug(entry.id);
  const { isD, isE, isUnknown, isArchived, isBenchmarkTesting } = statusDecorState(data);
  const glowCls = (isBenchmarkTesting ? ' testing-pulse' : '') + (isArchived ? ' archived-glow' : isD ? ' degraded-glow' : isE ? ' error-glow' : isUnknown ? '' : ' online-glow');
  const secondary = secondaryModelId(entry);
  const scoreHTML = scoreBadges(data, entry.id);
  if (entry.description) registerTip(`mi-${safeId}`, esc(entry.description));
  const archivedCls = entry.archived ? ' archived-card' : '';
  return `
  <article id="card-${safeId}" class="model-card min-w-0 bg-raised rounded-xl card-hover fade-in-once cursor-pointer px-3 pt-3 pb-0${glowCls}${archivedCls}" data-model-key="${safeId}" aria-labelledby="card-title-${safeId}">
     <div class="flex items-start justify-between shrink-0">
      <div class="flex flex-col min-w-0 overflow-hidden"${entry.description ? ` data-tip-id="mi-${safeId}"` : ''}>
        <div class="flex items-center gap-2">
          <h3 class="card-title font-semibold text-sm truncate"><button type="button" id="card-title-${safeId}" class="card-open" data-open-model="${safeId}">${esc(entry.name)}</button></h3>
          <span id="testing-label-${safeId}" class="testing-dots ${isBenchmarkTesting ? 'inline-flex' : 'hidden'}" data-tip="testing"><span></span><span></span><span></span></span>
        </div>
        ${secondary ? `<div class="text-xs font-mono text-text-muted ml-0 truncate">${esc(secondary)}</div>` : ''}
        ${_modelInfoLine(entry, safeId)}
      </div>
      <div id="badges-${safeId}" class="flex flex-col gap-1 shrink-0 items-end">
        ${topBadges(data.last_test || {}, data.status, data, entry)}
      </div>
    </div>
    <div id="scores-${safeId}" class="${scoreHTML ? 'flex justify-center mb-1 mt-1' : 'mb-1'}">${scoreHTML || ''}</div>
    <div id="tiles-${safeId}" class="grid grid-cols-4 gap-2 text-center">${_cardTilesHTML(data)}</div>
      <div class="h-36 relative mb-3"><canvas id="chart-${safeId}" class="w-full h-full" width="300" height="144" aria-hidden="true"></canvas>${chartPhHTML('chart-' + safeId, isArchived ? 'No data' : (data.data_start_epoch ? 'No data' : 'No data yet'))}</div>
  </article>`;
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
    const sg = scoreBadges(data, entry.id);
    scoresEl.className = sg ? 'flex justify-center mb-1 mt-1' : 'mb-1';
    setHTML(scoresEl, sg || '');
  }

  setHTML(document.getElementById(`tiles-${safeId}`), _cardTilesHTML(data));

  const miEl = document.getElementById(`mi-${safeId}`);
  if (miEl) {
    if (entry.description) registerTip(`mi-${safeId}`, esc(entry.description));
    const miHTML = _modelInfoLine(entry, safeId);
    miEl.outerHTML = miHTML;
  }
}


const _SCHEDULE_ICON = '<span aria-hidden="true">\u23f1</span>';

// How often each check runs, worded as a frequency, or that testing is paused: the scheduler's
// state comes from /api/config and every WebSocket hello (finding F90)
export function renderSchedule() {
  const el = document.getElementById('schedule-info');
  if (!el) return;
  const sched = state.scheduler;
  if (sched && !sched.running) {
    el.dataset.tip = 'schedulePaused';
    setHTML(el, segmentsHTML([_SCHEDULE_ICON, `<span class="text-warn-400">${sched.paused ? 'Testing paused' : 'Testing stopped'}</span>`]));
    return;
  }
  el.dataset.tip = 'schedule';
  const parts = [[state.healthEnabled, 'health', state.healthInterval], [true, 'benchmark', state.benchmarkInterval], [state.auditEnabled, 'audit', state.auditInterval]]
    .filter(([enabled, type, interval]) => enabled && interval && testTypeLabel(type))
    .map(([, type, interval]) => `<span>${esc(testTypeLabel(type))} every ${fmtSeconds(interval)}</span>`);
  setHTML(el, parts.length ? segmentsHTML([_SCHEDULE_ICON, segmentsHTML(parts, { sep: 'dot' })]) : '');
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

// A provider's cards render from its card buckets: fetched now, unless every model has them
// and they are younger than ui.provider_data_max_age (finding F89: the first load no longer
// fetches every provider, so this is the path most providers take)
function _providerReady(providerName) {
  const models = state.models.filter(e => e.provider === providerName);
  const dataAge = Date.now() - (state._providerDataAt[providerName] || 0);
  return state._modelCaps && dataAge < state.ui.provider_data_max_age * 1000
    && models.every(e => state.metrics[e.id]?.card_buckets !== undefined);
}

async function _fetchAndRenderProvider(providerName, providerSlug, contentEl) {
  if (_pendingFetches.has(providerName)) return;
  if (state.fetchedProviders.has(providerName)) return;

  const providerModels = state.models.filter(e => e.provider === providerName);
  if (_providerReady(providerName)) {
    logDebug(logTag('DOM', '→', 'LazyRender', 'Provider', providerName));
    state.fetchedProviders.add(providerName);
    _renderProviderCards(providerSlug, contentEl);
    initPendingChartsInContainer(contentEl);
    _scheduleUI({ models: providerModels.map(e => e.id), providers: true });
    return;
  }

  _pendingFetches.add(providerName);
  logDebug(logTag('DOM', '→', 'LazyFetch', 'Provider', providerName));
  try {
    const fetches = [fetchProviderMetrics([providerName], { cardBuckets: true })];
    if (!state._modelCaps) fetches.push(fetchModelInfoCapabilities());
    const [metricsData, capsData] = await Promise.all(fetches);
    if (capsData) mergeModelInfo(capsData);
    if (!metricsData) return;
    setMetrics(metricsData);
    state.fetchedProviders.add(providerName);
    state._providerDataAt[providerName] = Date.now();
    _renderProviderCards(providerSlug, contentEl);
    initPendingChartsInContainer(contentEl);
    _scheduleUI({ models: Object.keys(metricsData), providers: true });
  } finally {
    _pendingFetches.delete(providerName);
  }
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
  el.innerHTML = chartViews().map(v =>
    `<button type="button" class="chart-view-pill${v.key === current ? ' active' : ''}" data-card-view="${v.key}" data-tip="${v.tip}" aria-pressed="${v.key === current}">${esc(v.label)}</button>`
  ).join('');
  el.querySelectorAll('[data-card-view]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const view = btn.dataset.cardView;
      if (view) switchCardView(view);
    });
  });
}

// The provider's name is the section heading (finding F74)
function _providerSectionHTML(provider, entries, m, collapsed) {
  const providerSlug = slug(provider);
  const isCollapsed = collapsed.includes(providerSlug);
  const isFetched = state.fetchedProviders.has(provider);
  const isDeferred = isCollapsed || !isFetched;
  const gridContent = isDeferred
    ? deferredCardsHTML(entries.length)
    : entries.map(entry => buildCardDOM(entry, m[entry.id] || {})).join('');
  const gridAttr = isDeferred ? ` data-deferred="${entries.length}"` : '';
  const url = state.providerUrls[provider];
  return `
  <section class="mb-2 provider-section rounded-xl" data-provider-slug="${providerSlug}" id="section-${providerSlug}" aria-labelledby="header-${providerSlug}">
    <div class="provider-header" data-provider-slug="${providerSlug}">
      <button class="provider-toggle" aria-expanded="${!isCollapsed}" aria-controls="content-${providerSlug}" aria-label="Show or hide ${esc(provider)} models">
        ${chevronSVG('provider-chevron', 16)}
      </button>
      <h2 class="provider-name" id="header-${providerSlug}">${providerName(provider, url, 'text-sm font-semibold text-text-secondary uppercase tracking-wider', state.providerLogos[provider], state.providerTitles[provider])}</h2>
      <span id="phealth-${providerSlug}" class="ml-auto"></span>
    </div>
    <div id="content-${providerSlug}" class="provider-content${isCollapsed ? ' collapsed' : ''}">
      <div class="provider-inner">
        <div class="model-grid grid gap-2.5 pt-2 pl-2 pr-1.5" style="grid-template-columns:repeat(auto-fill,minmax(340px,1fr))"${gridAttr}>
          ${gridContent}
        </div>
      </div>
    </div>
  </section>`;
}

export function buildProviderSections() {
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
  container.setAttribute('aria-busy', 'false');

  applyProviderCollapse();

  for (const entry of visibleModels()) {
    const providerSlug = slug(entry.provider);
    if (collapsed.includes(providerSlug)) continue;
    if (!state.fetchedProviders.has(entry.provider)) continue;
    observeChart(`chart-${slug(entry.id)}`, entry.id);
  }

  renderChartViewPills();
  applyFilter();
  updateProviderCounts();
}

// Header scores and counts of every provider (counts follow the filter, see providerCounts)
export function updateProviderCounts() {
  for (const provider of state.providerOrder) {
    const el = document.getElementById(`phealth-${slug(provider)}`);
    if (!el) continue;
    const html = providerCountBadges(provider);
    if (!html) { el.replaceChildren(); continue; }
    const tmp = document.createElement('span');
    tmp.innerHTML = html;
    const next = tmp.firstElementChild;
    if (next && next.outerHTML !== el.outerHTML) el.replaceWith(next);
  }
}

onFilterApplied(updateProviderCounts);

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
  return providers;
  } catch (e) { logError(logTag('DOM', '←', 'Error', 'ModelList'), e); return null; }
}
