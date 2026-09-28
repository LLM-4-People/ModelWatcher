// Detail modal: full metrics grid, four chart views, fullscreen chart, history
// table with sort/pagination, and audit suites. updateModalIfNeeded does a
// lightweight DOM refresh (no rebuild) when the open model receives a WS result.
import { state, LS } from './state.js';
import { slug, esc, logError, logDebug, logTag, collapsibleHTML, toggleCollapsible, isPhone, kvRow, kvSep, kvGrids, setHTML, STATUS_GLYPH, SEP_TEXT, sepHTML, segmentsHTML, pushLayer, trapFocus } from './utils.js';
import { secondaryModelId, _tierColor, fmtNum, fmtMsCompact, fmtContext, fmtPrice, fmtPricePair, fmtEventTime, timeAgo, STATUS_TEXT, recordErrorText, degradedDescHTML, moeDetail, testTypeLabel, statusLabel, metricLabel, metricTileHTML, modelCapabilities, chartViews } from './format.js';
import { _loadChartJS, initChart, updateChartView, clearModelChartCache, chartPhHTML } from './chart.js';
import { cardBadges, reliableIndicator, renderModalCheckLine, providerName, _statusMessage, _eventTimestamp, _healthErrorIfNewer, statusDecorState, applyStatusDecor } from './dom.js';
import { api, fetchModelInfoDetail } from './api.js';
import { registerTip, clearTips } from './tooltips.js';
import {
  _sinceForRange, _rangeLabel, _updateRangeUI,
  _rangePillsHTML, _applyChartRange,
  _fetchForRange, _fetchHealthForRange, _updateChartViewUI,
  _openDateRangePicker, _closeDateRangePopover,
  setHistFetchFn, setModalBucketsFn, setModalInfoHTMLFn,
  setOpenModelKey as setRangeOpenModelKey, setHealthBuckets,
  getHealthBuckets, getHistSince, getHistUntil, getChartSince, getTimeRange, getFetchSeq,
  resetRangeState, initRangeStateForOpen,
} from './modal-ranges.js';
import { CUSTOM } from './ranges.js';
import {
  _historyRowsHTML, _renderSortedData, _bindSortHeaders, _updateSortHeaders,
  _fetchInitialHistory, _bindAccordionToggles,
  _bindDaySepClicks, _measureTheadHeight,
  _showTier2, _headerHTML, _accordionItems, _activeHistory,
  BENCH_COLS,
  setOpenModelKey as setHistOpenModelKey, prependHistoryRecord,
  resetHistoryState, resetHistoryForOpen,
  getHistoryTab, _setHistoryTab, getBenchmarkHistory, getHealthTableRows,
} from './modal-history.js';
import { HISTORY_PAGE_SIZE } from './api.js';

setHistFetchFn(_fetchInitialHistory);
setModalBucketsFn(b => { _modalBuckets = b; });
setModalInfoHTMLFn((entry, data) => _modalInfoHTML(entry, data));

function _modalTitleHTML(entry) {
  const logo = state.providerLogos[entry.provider] || '';
  const url = state.providerUrls[entry.provider] || '';
  const title = state.providerTitles[entry.provider] || '';
  const pCls = 'text-text-primary';
  let provider;
  if (isPhone() && logo) {
    const imgTag = `<img src="${esc(logo)}" alt="${esc(entry.provider)}" class="provider-logo" loading="lazy">`;
    const linkCls = `provider-link transition-colors ${pCls}`.trim();
    let tipAttr = '';
    if (title) { const id = `tip-prov-${slug(entry.provider)}`; registerTip(id, esc(title)); tipAttr = ` data-tip-id="${id}"`; }
    provider = url
      ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer" class="${linkCls}"${tipAttr}>${imgTag}</a>`
      : `<span class="${pCls}"${tipAttr}>${imgTag}</span>`;
  } else {
    provider = providerName(entry.provider, url, pCls, logo, title);
  }
  // Centred row: the provider link holds a logo, whose baseline is not the text's (finding F28)
  const line = segmentsHTML([provider, `<span class="text-text-secondary">${esc(entry.name)}</span>`], { sep: 'dot', cls: 'whitespace-nowrap seg-center' });
  const secondary = secondaryModelId(entry);
  return `${line}${secondary ? `<div class="text-xs font-mono text-text-muted ml-0 overflow-hidden text-ellipsis whitespace-nowrap">${esc(secondary)}</div>` : ''}`;
}

let _dataAgeSec = 0;
let _popLayer = null;
let _releaseFocus = null;

let _modalBuckets = [];
let _openModelKey = null;
let _auditFetchSeq = 0;
let _modelInfoFetchSeq = 0;
let _modelInfoOpen = null;

function _fetchAuditEvals(modelId) {
  const md = state.metrics[modelId];
  if (!md || !md.last_audit_result) return;
  const suites = md.last_audit_result.suites;
  if (suites && suites.length && suites[0].evals) return;
  const seq = ++_auditFetchSeq;
  api(`/api/audit?model=${encodeURIComponent(modelId)}`).then(data => {
    if (_auditFetchSeq !== seq || _openModelKey !== modelId || !data || !data.latest) return;
    const latest = data.latest;
    if (latest.suites && latest.suites.length && latest.suites[0].evals) {
      md.last_audit_result = latest;
      const infoEl = document.getElementById('modal-info');
      if (infoEl && _openModelKey === modelId) {
        const entry = state._modelMap[modelId] || state.models.find(m => m.id === modelId);
        if (entry) infoEl.innerHTML = _modalInfoHTML(entry, md);
      }
    }
  }).catch(e => logError(logTag('Modal', '←', 'Error', 'AuditEvals'), e));
}

function _fetchModelInfoDetail(modelId) {
  const seq = ++_modelInfoFetchSeq;
  fetchModelInfoDetail(modelId).then(data => {
    if (_modelInfoFetchSeq !== seq || _openModelKey !== modelId || !data || !data.latest) return;
    Object.assign(state._modelMap[modelId] || {}, data.latest);
    const infoEl = document.getElementById('modal-info');
    if (infoEl && _openModelKey === modelId) {
      const entry = state._modelMap[modelId];
      const md = state.metrics[modelId];
      if (entry && md) infoEl.innerHTML = _modalInfoHTML(entry, md);
    }
  }).catch(e => logError(logTag('Modal', '←', 'Error', 'ModelInfoDetail'), e));
}

// ── Last raw benchmark expandable section ─────────────────────────────

function _rawRow(label, value, unit) {
  if (value == null) return '';
  label = esc(label);
  if (unit === 'ms' && value >= 1000) {
    const s = (value / 1000).toFixed(2);
    return kvRow(label + ':', `${s}<span class="kv-unit">s</span>`, { mono: true });
  }
  const formatted = Number.isInteger(value) ? String(value) : fmtNum(value, value >= 100 ? 0 : value >= 10 ? 1 : 2);
  const unitHTML = unit ? `<span class="kv-unit">${unit}</span>` : '';
  return kvRow(label + ':', `${formatted}${unitHTML}`, { mono: true });
}

function _rawSectionHTML(lt) {
  if (!lt.success) return '';
  const output = [];
  if (lt.completion_tokens != null) output.push(_rawRow('Completion tokens', lt.completion_tokens));
  if (lt.token_count != null) output.push(_rawRow('Chunks observed', lt.token_count));
  if (lt.reasoning_tokens != null) output.push(_rawRow('Reasoning tokens', lt.reasoning_tokens));
  if (lt.chunk_token_cv != null) output.push(_rawRow(metricLabel('chunk_token_cv'), lt.chunk_token_cv));
  if (lt.chunk_token_max != null) output.push(_rawRow('Max chunk', lt.chunk_token_max, 'tok'));
  if (lt.finish_reason) output.push(kvRow('Finish reason:', esc(lt.finish_reason)));
  const itl = [];
  if (lt.raw_median_itl_ms != null) itl.push(_rawRow(metricLabel('raw_median_itl_ms'), lt.raw_median_itl_ms, 'ms'));
  if (lt.raw_avg_itl_ms != null) itl.push(_rawRow(metricLabel('raw_avg_itl_ms'), lt.raw_avg_itl_ms, 'ms'));
  if (lt.raw_max_itl_ms != null) itl.push(_rawRow(metricLabel('raw_max_itl_ms'), lt.raw_max_itl_ms, 'ms'));
  if (lt.hiccup_count != null) itl.push(_rawRow('Hiccups', lt.hiccup_count));
  const eff = [];
  if (lt.effective_median_itl_ms != null) eff.push(_rawRow(metricLabel('effective_median_itl_ms'), lt.effective_median_itl_ms, 'ms'));
  if (lt.effective_avg_itl_ms != null) eff.push(_rawRow(metricLabel('effective_avg_itl_ms'), lt.effective_avg_itl_ms, 'ms'));
  if (lt.effective_p99_itl_ms != null) eff.push(_rawRow(metricLabel('effective_p99_itl_ms'), lt.effective_p99_itl_ms, 'ms'));
  if (lt.effective_itl_tail_ratio != null) eff.push(_rawRow(metricLabel('effective_itl_tail_ratio'), lt.effective_itl_tail_ratio, '\u00d7'));
  const timing = [];
  if (lt.tpot_ms != null) timing.push(_rawRow(metricLabel('tpot_ms'), lt.tpot_ms, 'ms'));
  if (lt.total_latency_ms != null) timing.push(_rawRow('Total latency', lt.total_latency_ms, 'ms'));
  if (lt.thinking_duration_ms != null) timing.push(_rawRow('Thinking duration', lt.thinking_duration_ms, 'ms'));
  const network = [];
  if (lt.network_jitter_ms != null) network.push(_rawRow(metricLabel('network_jitter_ms'), lt.network_jitter_ms, 'ms'));
  if (lt.shrinkage_factor != null) network.push(kvRow('Shrinkage:', `${(lt.shrinkage_factor * 100).toFixed(0)}<span class="kv-unit">%</span>`, { mono: true }));
  if (lt.burst_arrivals != null) network.push(_rawRow('Burst arrivals', lt.burst_arrivals));
  if (lt.burst_arrival_pct != null) network.push(kvRow(`${esc(metricLabel('burst_arrival_pct'))}:`, `${lt.burst_arrival_pct.toFixed(0)}<span class="kv-unit">%</span>`, { mono: true }));
  if (lt.frame_batch_pct != null) network.push(kvRow('Frame batch:', `${lt.frame_batch_pct.toFixed(0)}<span class="kv-unit">%</span>`, { mono: true }));
  const stalls = [];
  if (lt.stall_first_pct != null) stalls.push(kvRow('First stall:', `${lt.stall_first_pct.toFixed(0)}<span class="kv-unit">%</span>`, { mono: true }));
  if (lt.stall_last_pct != null) stalls.push(kvRow('Last stall:', `${lt.stall_last_pct.toFixed(0)}<span class="kv-unit">%</span>`, { mono: true }));
  if (lt.stall_clusters != null) stalls.push(_rawRow('Stall clusters', lt.stall_clusters));
  if (lt.stall_ratio != null) stalls.push(_rawRow('Stall ratio', lt.stall_ratio));
  const meta = [];
  if (lt.request_id) meta.push(kvRow('Request ID:', esc(lt.request_id), { mono: true }));
  const parts = [...output];
  if (itl.length) { parts.push(kvSep()); parts.push(...itl); }
  if (eff.length) { parts.push(kvSep()); parts.push(...eff); }
  if (timing.length) { parts.push(kvSep()); parts.push(...timing); }
  if (network.length) { parts.push(kvSep()); parts.push(...network); }
  if (stalls.length) { parts.push(kvSep()); parts.push(...stalls); }
  if (meta.length) { parts.push(kvSep()); parts.push(...meta); }
  if (!parts.length) return '';
  return collapsibleHTML({
    id: 'raw-measurements', title: 'LAST RAW BENCHMARK',
    bodyHTML: kvGrids(parts),
    open: _isAccOpen('raw-measurements', false), wrapperCls: 'bg-overlay rounded-lg mb-4 overflow-hidden',
  });
}

function _auditSuiteSection(s, idx) {
  const suite = s.suite || 'unknown';
  const version = s.suite_version || '';
  const url = s.url || null;
  const suiteLabel = version ? `${esc(suite)} v${esc(version)}` : esc(suite);
  const passed = s.passed ?? 0;
  const total = s.total ?? 0;
  const rate = total > 0 && s.pass_rate != null ? (s.pass_rate * 100).toFixed(1) : '--';
  const rateCls = total > 0 && s.pass_rate != null ? _tierColor('audit_pass_rate', s.pass_rate) : 'text-text-muted';
  const duration = s.duration_ms != null ? fmtMsCompact(s.duration_ms) : '';
  const incomplete = total === 0;

  const rows = [];
  rows.push(kvRow('Tool:', url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer" class="text-accent-400 hover:underline">${esc(suiteLabel)}</a>` : esc(suiteLabel)));

  const params = s.params || {};
  const paramParts = Object.entries(params).map(([k, v]) => {
    if (typeof v === 'boolean') return `${k}=${v}`;
    if (v != null) return `${k}=${esc(String(v))}`;
    return '';
  }).filter(Boolean);
  if (paramParts.length) rows.push(kvRow('Params:', esc(paramParts.join(', '))));

  if (s.ts_epoch) rows.push(kvRow('Tested:', `<span class="text-text-muted">${esc(timeAgo(new Date(s.ts_epoch * 1000).toISOString()))}</span>`));

  if (incomplete && s.error) {
    rows.push(kvRow('Result:', `<span class="text-warn-400">${esc(s.error)}</span>${duration ? `<span class="kv-unit">${duration}</span>` : ''}`));
  } else {
    rows.push(kvRow('Result:', `<span class="${rateCls}">${passed}/${total} passed</span><span class="kv-unit">(${rate}%)</span>${duration ? `<span class="kv-unit">${duration}</span>` : ''}`));
  }

  const evals = s.evals;
  if (evals && evals.length > 0) {
    rows.push(kvSep());
    for (const e of evals) {
      const ok = e.passed === true;
      const dotCls = ok ? 'bg-success-400' : 'bg-danger-400';
      const nameCls = ok ? 'kv-value' : 'kv-value text-danger-400';
      const nm = esc(e.name);
      if (!ok && (e.error || e.response)) {
        const errId = `audit-err-${idx}-${slug(e.name)}`;
        rows.push(`<span class="kv-label">${nm}</span><span class="${nameCls}"><span class="inline-block w-1.5 h-1.5 rounded-full ${dotCls} align-middle"></span> <a href="javascript:void(0)" class="text-danger-400 hover:underline" data-expand-err="${errId}">{...}</a></span>`);
        const detailParts = [];
        if (e.response) {
          const respStr = typeof e.response === 'string' ? e.response : JSON.stringify(e.response, null, 2);
          detailParts.push(`<div class="mb-1"><span class="font-medium text-text-muted">Response:</span></div><pre class="audit-pre">${esc(respStr)}</pre>`);
        }
        if (e.error) detailParts.push(`<div class="mb-1"><span class="font-medium text-text-muted">Error:</span></div><pre class="audit-pre text-danger-400">${esc(e.error)}</pre>`);
        rows.push(`<div id="${errId}" class="hidden text-xs -mt-1 mb-1" style="grid-column:1/-1">${detailParts.join('')}</div>`);
      } else {
        rows.push(`<span class="kv-label">${nm}</span><span class="${nameCls}"><span class="inline-block w-1.5 h-1.5 rounded-full ${dotCls} align-middle"></span></span>`);
      }
    }
  }

  return kvGrids(rows);
}

function _auditSectionHTML(data) {
  if (!state.auditEnabled) return '';
  const ar = data.last_audit_result;
  if (!ar) return '';

  const suites = ar.suites;
  if (!suites || !suites.length) return '';
  const multiSuite = suites.length > 1;

  if (!multiSuite) {
    const s = suites[0];
    const rows = [];
    return collapsibleHTML({
id: 'audit-results', title: 'Last Audits',


      bodyHTML: _auditSuiteSection({ ...s, ts_epoch: s.ts_epoch || ar.ts_epoch }, 0),
      open: _isAccOpen('audit-results', false), wrapperCls: 'bg-overlay rounded-lg mb-4 overflow-hidden',
    });
  }

  const sections = [];
  for (let i = 0; i < suites.length; i++) {
    sections.push(_auditSuiteSection({ ...suites[i], ts_epoch: suites[i].ts_epoch || ar.ts_epoch }, i));
  }

  return collapsibleHTML({
    id: 'audit-results', title: 'Last Audits',
    bodyHTML: sections.join(kvSep()),
    open: _isAccOpen('audit-results', false), wrapperCls: 'bg-overlay rounded-lg mb-4 overflow-hidden',
  });
}

// ── Modal info section (DRY: shared by openModal + updateModalIfNeeded) ───

function _accState() {
  try { return JSON.parse(localStorage.getItem(LS.ACC_COLLAPSED) || '{}'); } catch (e) { logError(logTag('Modal', '←', 'Error', 'AccordionState'), e); return {}; }
}
function _setAccCollapsed(id, closed) {
  const s = _accState();
  s[id] = closed;
  try { localStorage.setItem(LS.ACC_COLLAPSED, JSON.stringify(s)); } catch (e) { logError(logTag('Modal', '→', 'Error', 'AccordionState'), e); }
}
function _isAccOpen(id, fallback) {
  const s = _accState();
  return id in s ? !s[id] : fallback;
}

function _toggleCollapsible(btn) {
  if (!btn) return;
  const section = btn.closest('.acc-section');
  const id = section?.dataset.section;
  const expanded = toggleCollapsible(btn);
  if (id) _setAccCollapsed(id, !expanded);
  if (id === 'model-info') _modelInfoOpen = expanded;
}

function _modelInfoSectionHTML(entry) {
  const e = entry || {};
  const ctx = e.context_window ? fmtContext(e.context_window) : '';
  const price = fmtPricePair(e.input_price, e.output_price);
  const cachePrice = e.cache_price != null ? fmtPrice(e.cache_price) : '';
  const outCtx = e.output_context ? fmtContext(e.output_context) : '';
  const caps = modelCapabilities(e).map(c => c.label);
  const _hasParams = e.param_count && e.param_count !== '0';
  const _hasMoe = !!e.num_experts;
  if (!ctx && !price && !caps.length && !e.description && !e.owner && !e.modalities && !e.tokenizer && !e.license && !e.quantization && !e.served_by && !e.architecture && !_hasParams && !_hasMoe && !e.fingerprint && !e.served_model && !e.fp_server && !e.fp_features) return '';
  const rows = [];
  if (ctx || outCtx) {
    const label = outCtx && ctx ? `${ctx} in / ${outCtx} out` : ctx ? `${ctx}` : `${outCtx} output`;
    rows.push(kvRow('Context:', esc(label), { mono: true }));
  }
  if (price) {
    let priceHTML = `${esc(price)}<span class="kv-unit">/1M tok</span>`;
    const extras = [];
    if (cachePrice) extras.push(`cache: ${esc(cachePrice)}`);
    if (e.reasoning_price != null) extras.push(`reasoning: ${esc(fmtPrice(e.reasoning_price))}`);
    if (e.image_price != null) extras.push(`image: ${esc(fmtPrice(e.image_price))}`);
    if (extras.length) priceHTML += `<span class="kv-unit"> (${extras.join(', ')})</span>`;
    rows.push(kvRow('Price:', priceHTML, { mono: true }));
  }
  const tech = [];
  if (_hasParams) tech.push(kvRow('Parameters:', esc(e.param_count) + (_hasMoe ? ' <span class="kv-unit">MoE</span>' : ''), { mono: true }));
  if (e.architecture) tech.push(kvRow('Architecture:', esc(e.architecture) + (_hasMoe ? `${SEP_TEXT}MoE` : '')));
  if (_hasMoe) tech.push(kvRow('Experts:', esc(moeDetail(e)), { mono: true }));
  if (e.quantization) tech.push(kvRow('Quantization:', esc(e.quantization)));
  if (caps.length) tech.push(kvRow('Capabilities:', esc(caps.join(', '))));
  if (e.modalities) tech.push(kvRow('Modalities:', esc(e.modalities)));
  if (e.tokenizer) tech.push(kvRow('Tokenizer:', esc(e.tokenizer)));
  if (e.served_by) {
    let sb = esc(e.served_by);
    if (e.engine_version) sb += ` <span class="kv-unit">${esc(e.engine_version)}</span>`;
    if (e.tensor_parallel) sb += ` <span class="kv-unit">tp${e.tensor_parallel}</span>`;
    tech.push(kvRow('Served by:', sb));
  }
  if (e.served_model) tech.push(kvRow('Served model:', esc(e.served_model), { mono: true }));
  if (e.fingerprint) tech.push(kvRow('Fingerprint:', esc(e.fingerprint), { mono: true }));
  if (e.fp_server) tech.push(kvRow('Server:', esc(e.fp_server)));
  if (e.fp_features) tech.push(kvRow('Features:', esc(e.fp_features)));
  if (e.owner) tech.push(kvRow('Owner:', esc(e.owner)));
  if (e.license) tech.push(kvRow('License:', esc(e.license)));
  if (tech.length) { rows.push(kvSep()); rows.push(...tech); }
  if (e.description) { rows.push(kvSep()); rows.push(kvRow('About:', esc(e.description))); }
  const open = _modelInfoOpen ?? _isAccOpen('model-info', !isPhone());
  return collapsibleHTML({
    id: 'model-info', title: 'MODEL INFO',
    bodyHTML: kvGrids(rows),
    open, wrapperCls: 'bg-overlay rounded-lg mb-4 overflow-hidden',
  });
}

// The modal's tiles and label forms; the card shows four of the same definitions (finding F58)
const _MODAL_TILES = [['uptime', 'full'], ['tps', 'full'], ['ttft', 'full'], ['p99', 'full'], ['stalls', 'full'], ['tail', 'short'], ['batch', 'short'], ['jitter', 'short']];

function _modalInfoHTML(entry, data) {
  const lt = data.last_test || {};
  const { isD } = statusDecorState(data);
  const isE = data.status === 'error';

  return `
    ${(() => {
      const crt = state.clientRTT;
      const art = lt.network_rtt_ms;
      if (crt == null && art == null) return '';
      const parts = [];
      if (crt != null && crt > 0) parts.push(`Your latency: ${crt}ms`);
      if (art != null) parts.push(`API latency: ${Math.round(art)}ms`);
      return `<div class="text-[10px] text-text-muted mb-4">${segmentsHTML(parts.map(t => `<span>${t}</span>`), { sep: 'dot' })}</div>`;
    })()}
    ${(() => {
      const msg = _statusMessage(data, lt);
      if (!msg) return '';
      const isDeg = isD || !!(data.retry_attempt && data.retry_total);
      const retrying = !!(data.retry_attempt && data.retry_total);
      const eventTs = _eventTimestamp(data, lt);
      const healthNewer = !!_healthErrorIfNewer(data, lt);
      const rid = healthNewer ? (data.health_request_id || lt.request_id) : lt.request_id;
      // Prose, not a flex row: the message wraps at any character, so a long URL cannot push
      // the words out of the box (finding F27); the time and its separator lead inline
      const when = eventTs ? `<span class="text-text-muted whitespace-nowrap">${esc(fmtEventTime(eventTs))}</span>${sepHTML('dot')}` : '';
      return `
    <div class="${isDeg ? 'bg-warn-500/10 border-warn-500/20' : 'bg-danger-500/10 border-danger-500/20'} border rounded-lg p-3 mb-4">
      <div class="text-xs font-medium ${isDeg ? 'text-warn-400' : 'text-danger-400'} mb-1">${isDeg ? `${STATUS_GLYPH.degraded} ${esc(statusLabel('degraded'))}` : 'Error'}</div>
      <div class="border-t border-surface-700/30 mb-2"></div><div class="modal-error-text text-[10px] ${isDeg ? STATUS_TEXT.degraded : STATUS_TEXT.error} font-mono">${when}<span>${isDeg && lt.degraded ? degradedDescHTML(lt) : esc(msg || recordErrorText(lt))}</span></div>
      ${rid ? `<div class="text-[10px] text-text-muted/60 font-mono mt-1.5" title="Provider request ID">req: ${esc(rid)}</div>` : ''}
    </div>`;
    })()}
    ${_modelInfoSectionHTML(entry)}
    <div class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2 mb-5">
      ${_MODAL_TILES.map(([key, form]) => metricTileHTML(key, data, { mode: 'modal', form, extra: key === 'p99' ? reliableIndicator(lt.itl_reliable, true, lt, 'itlReliable') : '' })).join('')}
    </div>

    ${_rawSectionHTML(lt)}
    ${_auditSectionHTML(data)}`;
}

// ── Modal open/close ────────────────────────────────────────────────────────

export function openModal(key) {
  logDebug(logTag('Modal', '→', 'Open', key));
  const entry = state._modelMap[key] || state.models.find(m => slug(m.id) === key);
  if (!entry) return;
  _openModelKey = entry.id;
  setRangeOpenModelKey(entry.id);
  setHistOpenModelKey(entry.id);
  setHealthBuckets(null);
  resetHistoryForOpen();
  const data = state.metrics[entry.id] || {};
  const ranges = state.timeRanges;
  const availableRanges = data.available_ranges || [];
  const rangeKey = initRangeStateForOpen(availableRanges);
  const dataStartEpoch = data.data_start_epoch;
  _dataAgeSec = dataStartEpoch ? (Date.now() / 1000 - dataStartEpoch) : 0;

  const modalEl = document.getElementById('modal');
  const titleEl = document.getElementById('modal-title');
  if (titleEl) titleEl.innerHTML = _modalTitleHTML(entry);
  renderModalCheckLine(entry.id);
  const badgesEl = document.getElementById('modal-badges');
  if (badgesEl) badgesEl.innerHTML = cardBadges(data.last_test || {}, data.status, data, entry, { focusable: true });
  const decor = document.getElementById('modal-decor');
  applyStatusDecor(decor, data);
  if (decor) decor.classList.add('fade-in');
  if (modalEl) { modalEl.classList.remove('hidden'); modalEl.classList.add('flex'); }
  // A layer for Escape and a focus trap: focus moves into the dialog, the page behind is inert and
  // does not scroll, and focus returns to the card on close (findings F49, F72)
  if (!_releaseFocus) {
    _popLayer = pushLayer(closeModal);
    // Opened by a click on the card body, focus goes back to the card's own button
    const active = document.activeElement;
    const returnTo = active && active !== document.body ? active : document.getElementById(`card-title-${slug(entry.id)}`);
    _releaseFocus = trapFocus(modalEl, { focus: document.getElementById('modal-close'), returnTo });
  }

  _modalBuckets = [];
  const history = _activeHistory();

  const savedView = localStorage.getItem(LS.CHART_VIEW);
  const view = state.chartViews.includes(savedView) ? savedView : state.chartViews[0];

  const rangePillsHTML = _rangePillsHTML(ranges, rangeKey, availableRanges, 'range');

  const bodyEl = document.getElementById('modal-body');
  if (bodyEl) bodyEl.innerHTML = `
    <div id="modal-info" class="shrink-0">${_modalInfoHTML(entry, data)}</div>
    <div class="flex items-center gap-1.5 mb-2 shrink-0">
      ${rangePillsHTML}
    </div>
    <div class="flex items-center gap-1.5 mb-2 shrink-0">
      ${chartViews().map(v => `<button type="button" id="chart-view-${v.key}" class="chart-view-pill${view === v.key ? ' active' : ''}" data-view="${v.key}" data-tip="${v.tip}" aria-pressed="${view === v.key}">${esc(v.label)}</button>`).join('')}
    </div>
    <div class="mb-5 relative shrink-0 h-[300px]"><canvas id="modal-chart" class="w-full h-full" role="img" aria-label="Chart of the model's metrics over the selected range"></canvas>${chartPhHTML('modal-chart')}</div>
    <div id="modal-deferred"></div>
  `;

  const openId = entry.id;
  requestAnimationFrame(() => {
    if (_openModelKey !== openId) return;
    const slot = document.getElementById('modal-deferred');
    if (!slot) return;
    slot.outerHTML = `
    <div class="flex items-center gap-2 mb-2 shrink-0">
      <span class="text-xs font-medium text-text-primary">History</span>
      ${['health', 'benchmark'].map(t => `<button type="button" id="hist-tab-${t}" class="chart-view-pill${getHistoryTab() === t ? ' active' : ''}" data-hist-tab="${t}">${esc(testTypeLabel(t))}</button>`).join('')}
        <button type="button" id="toggle-cols" class="chart-view-pill${_showTier2() ? ' active' : ''}${BENCH_COLS.some(c => c.tier2) && getHistoryTab() === 'benchmark' ? '' : ' hidden'}" data-tip="toggleColumns">+ Columns</button>
     </div>
     <div class="flex items-center gap-2 mb-2 shrink-0">
       <button type="button" class="chart-view-pill" id="hist-custom-range" data-tip="customDateRange" aria-label="Custom date range for the history" aria-haspopup="dialog"><svg aria-hidden="true" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="1.5" y="3" width="13" height="11" rx="2"/><path d="M1.5 7h13M5 1v4M11 1v4"/></svg></button>
       <span class="text-[10px] text-text-muted" id="hist-range-label">${_rangeLabel(getHistSince(), getHistUntil())}</span>
      </div>
        <div class="hidden sm:block w-full">
         <div class="hist-wrap">
         <table class="hist-table" id="history-grid">
         ${_headerHTML()}
         <tbody>${_historyRowsHTML(history)}</tbody>
        </table>
         </div>
        </div>
      <div class="sm:hidden" id="history-accordion">
        ${_accordionItems(history)}
      </div>`;

    _measureTheadHeight();

    const toggleBtn = document.getElementById('toggle-cols');
    const histTabBenchmark = document.getElementById('hist-tab-benchmark');
    const histTabHealth = document.getElementById('hist-tab-health');

    if (toggleBtn) {
      toggleBtn.classList.toggle('active', _showTier2());
      toggleBtn.addEventListener('click', () => {
        const show = localStorage.getItem(LS.TABLE_COLS) !== '1';
        localStorage.setItem(LS.TABLE_COLS, show ? '1' : '0');
        _renderSortedData(true);
        toggleBtn.classList.toggle('active', show);
      });
    }
    if (histTabBenchmark) {
      histTabBenchmark.addEventListener('click', () => {
        if (getHistoryTab() === 'benchmark') return;
        _setHistoryTab('benchmark');
        histTabBenchmark.classList.add('active');
        histTabHealth?.classList.remove('active');
        if (toggleBtn) { toggleBtn.classList.remove('hidden'); toggleBtn.classList.toggle('active', _showTier2()); }
        if (getBenchmarkHistory().length === 0) {
          _fetchInitialHistory(openId);
        } else {
          _renderSortedData(false);
        }
      });
    }
    if (histTabHealth) {
      histTabHealth.addEventListener('click', () => {
        if (getHistoryTab() === 'health') return;
        _setHistoryTab('health');
        histTabHealth.classList.add('active');
        histTabBenchmark?.classList.remove('active');
        if (toggleBtn) toggleBtn.classList.add('hidden');
        if (getHealthTableRows() === null || getHealthTableRows().length === 0) {
          _fetchInitialHistory(openId);
        } else {
          _renderSortedData(false);
        }
      });
    }

    const histCustomBtn = document.getElementById('hist-custom-range');
    if (histCustomBtn) {
      histCustomBtn.addEventListener('click', () => {
        _openDateRangePicker(histCustomBtn, dataStartEpoch, 'history');
      });
    }

    _bindAccordionToggles();
    _bindDaySepClicks();
    _updateSortHeaders();
    _bindSortHeaders();
    const ms = document.getElementById('modal-scroll');
    if (ms && !ms._modelInfoBound) { ms.addEventListener('click', e => { const btn = e.target.closest('[data-section="model-info"] > .acc-btn, [data-section="raw-measurements"] > .acc-btn, [data-section="audit-results"] > .acc-btn'); if (btn) _toggleCollapsible(btn); const errLink = e.target.closest('[data-expand-err]'); if (errLink) { const el = document.getElementById(errLink.dataset.expandErr); if (el) el.classList.toggle('hidden'); e.preventDefault(); } }); ms._modelInfoBound = true; }
  });

  _loadChartJS().then(() => {
    if (!_openModelKey || _openModelKey !== openId) return;
    _updateChartViewUI(view);
    _updateRangeUI(rangeKey);

    document.querySelectorAll('[data-range]').forEach(btn => {
      btn.addEventListener('click', () => {
        if (btn.disabled) return;
        const newRange = btn.dataset.range;
        if (newRange === CUSTOM) {
          _openDateRangePicker(btn, dataStartEpoch, 'chart');
          return;
        }
        if (!newRange || newRange === getTimeRange()) return;
        _applyChartRange(newRange);
      });
    });

    document.querySelectorAll('[data-view]').forEach(btn => {
      btn.addEventListener('click', () => {
        const newView = btn.dataset.view;
        if (!newView) return;
        const current = state.charts['modal-chart']?._view ?? view;
        if (newView === current) return;
        localStorage.setItem(LS.CHART_VIEW, newView);
        const since = getTimeRange() === CUSTOM ? getChartSince() : _sinceForRange(getTimeRange());
        if (newView === 'health' && getHealthBuckets() === null) {
          _fetchHealthForRange(openId, since, getFetchSeq());
        } else if (newView === 'health' && getHealthBuckets()) {
          if (!updateChartView('modal-chart', getHealthBuckets(), true, 'health')) initChart('modal-chart', getHealthBuckets(), true, 'health', '', false);
        } else {
          if (!updateChartView('modal-chart', _modalBuckets, true, newView)) initChart('modal-chart', _modalBuckets, true, newView, '', false);
        }
        _updateChartViewUI(newView);
      });
    });
  }).catch(e => logError(logTag('Modal', '←', 'Error', 'ChartInit'), e));

  _fetchForRange(entry.id, rangeKey, view);
  _fetchInitialHistory(entry.id);
  _fetchAuditEvals(entry.id);
  _fetchModelInfoDetail(entry.id);
}

export function closeModal() {
  _popLayer?.();
  _popLayer = null;
  _closeDateRangePopover();
  resetRangeState();
  resetHistoryState();
  setRangeOpenModelKey(null);
  setHistOpenModelKey(null);
  const key = _openModelKey;
  _openModelKey = null;
  _modalBuckets = [];
  clearTips('ok-');
  if (key) clearModelChartCache(key);
  const modalEl = document.getElementById('modal');
  const decor = document.getElementById('modal-decor');
  if (decor) decor.classList.remove('fade-in');
  if (modalEl) { modalEl.classList.add('hidden'); modalEl.classList.remove('flex'); }
  _releaseFocus?.();
  _releaseFocus = null;
  applyStatusDecor(decor, {});
  if (state.charts['modal-chart']) { state.charts['modal-chart'].destroy(); delete state.charts['modal-chart']; }
}

export function updateModalIfNeeded(modelId, { record, testType } = {}) {
  if (!_openModelKey || _openModelKey !== modelId) return;
  const data = state.metrics[modelId] || {};
  const entry = state._modelMap[modelId];
  if (!entry) return;

  setHTML(document.getElementById('modal-title'), _modalTitleHTML(entry));
  renderModalCheckLine(modelId);
  setHTML(document.getElementById('modal-badges'), cardBadges(data.last_test || {}, data.status, data, entry, { focusable: true }));
  applyStatusDecor(document.getElementById('modal-decor'), data);
  setHTML(document.getElementById('modal-info'), _modalInfoHTML(entry, data));

  if (!record) return;

  const isHealth = testType === 'health';
  const cap = HISTORY_PAGE_SIZE * 3;
  if (getHistSince() != null && record.ts_epoch != null && record.ts_epoch < getHistSince()) return;
  if (getHistUntil() != null && record.ts_epoch != null && record.ts_epoch > getHistUntil()) return;
  prependHistoryRecord(record, isHealth, cap);

  clearModelChartCache(modelId);
}
