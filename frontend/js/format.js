// Tier system + formatting helpers. All metric value rendering flows through
// here: tier colors (explicit class maps for Tailwind v4 scanning), formatted
// HTML with styled unit spans, freshness tiers, and score/trend display.
import { state } from './state.js';
import { esc } from './utils.js';

const TIER_KEYS = ['accent-400', 'success-400', 'warn-400', 'danger-400', 'danger-700', 'teal-400'];

const _TIER_CLASSES = {
  'accent-400':  { text: 'text-tier-accent',        bg: 'bg-tier-accent-bg',        border: 'border-tier-accent-border',        dot: 'bg-tier-accent' },
  'success-400': { text: 'text-tier-success',       bg: 'bg-tier-success-bg',       border: 'border-tier-success-border',       dot: 'bg-tier-success' },
  'warn-400':    { text: 'text-tier-warn',          bg: 'bg-tier-warn-bg',         border: 'border-tier-warn-border',          dot: 'bg-tier-warn' },
  'danger-400':  { text: 'text-tier-danger',        bg: 'bg-tier-danger-bg',        border: 'border-tier-danger-border',        dot: 'bg-tier-danger' },
  'danger-700':  { text: 'text-tier-danger-dark',   bg: 'bg-tier-danger-dark-bg',   border: 'border-tier-danger-dark-border',   dot: 'bg-tier-danger-dark' },
  'teal-400':    { text: 'text-tier-teal',           bg: 'bg-tier-teal-bg',         border: 'border-tier-teal-border',          dot: 'bg-tier-teal' },
};

function _tierMap(prop) {
  const m = {};
  for (const k of TIER_KEYS) m[k] = _TIER_CLASSES[k][prop];
  return m;
}

export const TIER_TEXT    = _tierMap('text');
export const TIER_BG     = _tierMap('bg');
export const TIER_DOT_BG = _tierMap('dot');

export const STATUS_TEXT = {
  online: 'text-status-online',
  degraded: 'text-status-degraded',
  error: 'text-status-error',
  testing: 'text-status-testing',
  unknown: 'text-text-faint',
};

export const STATUS_DOT = {
  online: 'bg-status-online',
  degraded: 'bg-status-degraded',
  error: 'bg-status-error',
  testing: 'bg-status-testing',
  unknown: 'bg-text-faint',
};

// ── Freshness (how recent is data relative to expected interval) ────────────

export const FRESHNESS_TIERS = [
  { label: 'Fresh', dot: 'bg-text-muted', text: 'text-text-secondary' },
  { label: 'Aging', dot: 'bg-warn-400', text: 'text-warn-400' },
  { label: 'Stale', dot: 'bg-danger-400', text: 'text-danger-400' },
];

const FRESHNESS_TEXT = FRESHNESS_TIERS.map(t => t.text);

function freshnessTier(ageSeconds, intervalSeconds) {
  if (ageSeconds == null || !intervalSeconds || intervalSeconds <= 0) return -1;
  if (ageSeconds < 0) return 0;
  const ratio = ageSeconds / intervalSeconds;
  if (ratio <= 1.5) return 0;
  if (ratio <= 3.0) return 1;
  return 2;
}

export function freshnessTextCls(ageSeconds, intervalSeconds) {
  const t = freshnessTier(ageSeconds, intervalSeconds);
  return t < 0 ? 'text-text-muted' : FRESHNESS_TEXT[t];
}

function _tierIdx(metric, value) {
  if (value == null) return -1;
  const cfg = state.colorThresholds[metric];
  if (!cfg || !cfg.thresholds) return -1;
  const ts = cfg.thresholds;
  const tm = cfg.tier_map;
  const ge = cfg.higher_is_better;

  for (let i = 0; i < ts.length; i++) {
    const hit = ge ? (value >= ts[i]) : (value < ts[i]);
    if (hit) return tm ? (tm[i] ?? -1) : i;
  }
  return tm ? (tm[ts.length] ?? -1) : (ts.length > 0 ? ts.length - 1 : -1);
}

export function _tierColor(metric, value) {
  const idx = _tierIdx(metric, value);
  const tiers = state.colorThresholds.tiers;
  if (idx < 0 || !tiers || !tiers[idx]) return 'text-text-secondary';
  return TIER_TEXT[tiers[idx].color] || 'text-text-secondary';
}

const _TIP_TO_METRIC = {
  ttft: 'ttft', tps: 'tps', uptime: 'uptime',
  stall: 'stall_count', consistency: 'effective_itl_tail_ratio',
  p99Itl: 'raw_p99_itl_ms', medianItl: 'raw_median_itl_ms', maxItl: 'raw_max_itl_ms',
  itlTailRatio: 'effective_itl_tail_ratio', batching: 'chunk_token_ratio',
  burstArrival: 'burst_arrival_pct', chunkCv: 'chunk_token_cv',
};

export function tierScaleHTML(tipKey) {
  const metric = _TIP_TO_METRIC[tipKey];
  if (!metric) return '';
  const cfg = state.colorThresholds[metric];
  if (!cfg || !cfg.thresholds) return '';
  const tiers = state.colorThresholds.tiers;
  const ts = cfg.thresholds;
  const ge = cfg.higher_is_better;
  const parts = [];
  const _fmt = (v) => {
    if (metric === 'stall_count') return String(v);
    if (metric === 'uptime') return `${v}%`;
    if (metric === 'tps') return String(v);
    if (metric === 'effective_itl_tail_ratio') return `${v}×`;
    if (metric === 'chunk_token_ratio') return `${v}×`;
    if (metric === 'burst_arrival_pct') return `${v}%`;
    if (metric === 'total_latency_ms') return v >= 1000 ? `${v / 1000}s` : `${v}ms`;
    if (metric === 'ttft') return v >= 1000 ? `${v / 1000}s` : `${v}ms`;
    return `${v}ms`;
  };
  for (let i = 0; i < tiers.length && i < ts.length; i++) {
    const colorCls = TIER_TEXT[tiers[i].color] || 'text-text-secondary';
    const boundary = ts[i];
    const prefix = ge ? '≥' : '<';
    const label = i < ts.length - 1 ? `${prefix}${_fmt(boundary)}` : (i > 0 ? `${ge ? '<' : '≥'}${_fmt(ts[i - 1])}` : '');
    parts.push(`<span class="${colorCls}">●</span> ${label}`);
  }
  return '<br>' + parts.join(' ');
}

export function tpsColor(t) { return _tierColor('tps', t); }
export function ttftColor(ms) { return _tierColor('ttft', ms); }
export function uptimeColor(pct) { return _tierColor('uptime', pct); }

export function p99ItlColor(ms) { return _tierColor('raw_p99_itl_ms', ms); }
export function tailColor(r) { return _tierColor('effective_itl_tail_ratio', r); }
export function batchingColor(r) { return _tierColor('chunk_token_ratio', r); }
export function stallColor(n) { return _tierColor('stall_count', n); }

export const SCORE_TIERS = [
  { min: 80, key: 'accent-400', label: 'Excellent' },
  { min: 60, key: 'success-400', label: 'Good' },
  { min: 40, key: 'warn-400', label: 'OK' },
  { min: 20, key: 'danger-400', label: 'Bad' },
  { min: 0, key: 'danger-700', label: 'Critical' },
];

function scoreTierKey(score) {
  if (score == null) return null;
  for (const t of SCORE_TIERS) if (score >= t.min) return t.key;
  return 'danger-700';
}

export function scoreColor(score) {
  const k = scoreTierKey(score);
  return k ? TIER_TEXT[k] : 'text-text-muted';
}

export function trendArrow(trend) {
  if (!trend?.direction) return '';
  if (trend.direction === 'improving') return '\u2191';
  if (trend.direction === 'degrading') return '\u2193';
  return '\u00b1';
}

export function trendColor(trend) {
  if (!trend?.direction) return '';
  if (trend.direction === 'improving') return 'text-success-400';
  if (trend.direction === 'degrading') return 'text-danger-400';
  return 'text-text-muted';
}

export function trendDelta(trend) {
  if (!trend?.direction) return '';
  const unit = trend.unit || '';
  const val = trend.change || 0;
  const formatted = unit === 'pts' || unit === 'pp' ? fmtNum(val, 0) : fmtNum(val, 1);
  if (trend.direction === 'improving') return `+${formatted} ${unit}`.trim();
  if (trend.direction === 'degrading') return `-${formatted} ${unit}`.trim();
  return `\u00b10.0 ${unit}`.trim();
}

export function fmtCritical(metric, value, formattedText) {
  if (value == null || formattedText == null) return formattedText;
  const tiers = state.colorThresholds?.tiers;
  if (!tiers || _tierIdx(metric, value) !== tiers.length - 1) return formattedText;
  return `<span class="underline">${formattedText}</span>`;
}

export function fmtNum(n, dec = 1) { return n != null ? Number(n).toFixed(dec) : '--'; }

export function moeDetail(e) {
  if (!e || !e.num_experts) return '';
  const parts = [e.num_experts + ' routed'];
  if (e.num_shared_experts) parts.push(e.num_shared_experts + ' shared');
  if (e.num_experts_per_tok) parts.push(e.num_experts_per_tok + '/tok');
  return parts.join(' · ');
}

export function fmtContext(n) {
  if (n == null) return '--';
  if (n >= 999_500) return (n / 1_000_000).toFixed(1).replace(/\.0$/, '') + 'M';
  if (n >= 1000) return (n / 1000).toFixed(0) + 'k';
  return String(n);
}

export function fmtPrice(dollars) {
  if (dollars == null) return '--';
  if (dollars === 0) return '$0';
  if (dollars >= 100) return '$' + Math.round(dollars);
  if (dollars >= 1) return '$' + dollars.toFixed(2);
  if (dollars >= 0.01) return '$' + dollars.toFixed(2);
  if (dollars >= 0.001) return '$' + dollars.toFixed(3);
  return '$' + dollars.toFixed(4);
}

export function fmtPricePair(inputPrice, outputPrice) {
  const i = fmtPrice(inputPrice);
  const o = fmtPrice(outputPrice);
  if (i === '--' && o === '--') return '';
  return `${i}/${o}`;
}

export function fmtTps(val) {
  if (val == null) return '--';
  return `${Number(val).toFixed(1)}<span class="text-xs font-normal text-text-faint">t/s</span>`;
}

function fmtTpsPlain(val) {
  if (val == null) return '--';
  return `${Number(val).toFixed(1)} t/s`;
}

function _fmtMs(ms) {
  if (ms == null) return { n: '--', unit: '' };
  if (ms < 1000) return { n: Math.round(ms), unit: 'ms' };
  return { n: (ms / 1000).toFixed(2), unit: 's' };
}

export function fmtTTFT(ms) {
  const { n, unit } = _fmtMs(ms);
  if (unit === '') return n;
  return `${n}<span class="text-xs font-normal text-text-faint">${unit}</span>`;
}

export function fmtLatency(ms) {
  const { n, unit } = _fmtMs(ms);
  if (unit === '') return n;
  return `${n}${unit}`;
}

export function fmtMsCompact(v, dec = 1) {
  if (v == null) return '--';
  const unit = v >= 1000 ? 's' : 'ms';
  const n = v >= 1000 ? (v / 1000).toFixed(dec) : Math.round(v);
  return `${n}<span class="text-xs font-normal text-text-faint">${unit}</span>`;
}

export function fmtMsCompactPlain(v, dec = 1) {
  if (v == null) return '--';
  return v >= 1000 ? (v / 1000).toFixed(dec) + 's' : Math.round(v) + 'ms';
}

export function fmtUptime(pct) {
  if (pct == null) return '--<span class="text-xs font-normal text-text-faint">%</span>';
  return `${pct.toFixed(1)}<span class="text-xs font-normal text-text-faint">%</span>`;
}

export function fmtBatching(val) {
  if (val == null) return '--';
  return `${val.toFixed(1)}<span class="text-xs font-normal text-text-faint">\u00d7</span>`;
}

export function fmtTail(val) { return fmtBatching(val); }

function fmtCv(val) {
  if (val == null) return '--';
  return val.toFixed(2);
}

function _fmtDuration(s) {
  s = Math.max(0, Math.floor(s));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60), rm = m % 60;
  if (h < 24) return rm ? `${h}h ${rm}m` : `${h}h`;
  const d = Math.floor(h / 24), rh = h % 24;
  if (d < 7) return rh ? `${d}d ${rh}h` : `${d}d`;
  const w = Math.floor(d / 7), rd = d % 7;
  if (w < 4) return rd ? `${w}w ${rd}d` : `${w}w`;
  const mo = Math.floor(w / 4), rw = w % 4;
  return rw ? `${mo}mo ${rw}w` : `${mo}mo`;
}

export function timeAgo(ts) {
  if (!ts) return 'never';
  const diff = (Date.now() - new Date(ts).getTime()) / 1000;
  return `${_fmtDuration(diff)} ago`;
}

export function fmtSince(ts) {
  if (!ts) return '';
  const diff = (Date.now() - new Date(ts).getTime()) / 1000;
  if (diff <= 0) return '';
  return _fmtDuration(diff);
}

export function fmtSeconds(s) {
  if (s == null) return '--';
  return _fmtDuration(s);
}

export function fmtEventTime(ts) {
  if (!ts) return '';
  const d = ts instanceof Date ? ts : new Date(ts);
  if (isNaN(d.getTime())) return '';
  const now = new Date();
  const sameYear = d.getFullYear() === now.getFullYear();
  const dateOpts = sameYear ? { month: 'short', day: 'numeric' } : { month: 'short', day: 'numeric', year: 'numeric' };
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  return `${d.toLocaleDateString([], dateOpts)} ${time}`;
}

function fmtMetricValue(metric, lt) {
  const label = state.metricLabels[metric] || metric;
  let val;
  switch (metric) {
    case 'tps': val = fmtTpsPlain(lt.tps); break;
    case 'ttft': val = fmtLatency(lt.ttft_ms); break;
    case 'stall_count': val = String(lt.stall_count ?? 0); break;
    case 'raw_p99_itl_ms': val = fmtLatency(lt.raw_p99_itl_ms); break;
    case 'raw_median_itl_ms': val = fmtLatency(lt.raw_median_itl_ms); break;
    case 'raw_max_itl_ms': val = fmtLatency(lt.raw_max_itl_ms); break;
    case 'effective_itl_tail_ratio': val = lt.effective_itl_tail_ratio != null ? `${lt.effective_itl_tail_ratio_estimated ? '~' : ''}${lt.effective_itl_tail_ratio.toFixed(1)}\u00d7` : '--'; break;
    case 'chunk_token_ratio': val = lt.chunk_token_ratio != null ? `${lt.chunk_token_ratio.toFixed(1)}\u00d7` : '--'; break;
    case 'chunk_token_cv': val = lt.chunk_token_cv != null ? fmtCv(lt.chunk_token_cv) : '--'; break;
    case 'burst_arrival_pct': val = lt.burst_arrival_pct != null ? `${lt.burst_arrival_pct.toFixed(0)}%` : '--'; break;
    default: val = '--';
  }
  return `${label}: ${esc(val)}`;
}

function degradedDesc(lt) {
  const r = (lt.degraded_reason || '').split(',')[0].trim();
  if (r === 'critical_tier' && lt.critical_metrics?.length) {
    return ['Critical metrics:', ...lt.critical_metrics.map(m => fmtMetricValue(m, lt))];
  }
  if (r === 'stream_error') return ['Stream interrupted after tokens were received.', 'Metrics are computed from partial output.'];
  if (r === 'insufficient_output') return ['Output below minimum threshold for reliable metrics.', 'Too few tokens or chunks received.'];
  if (r === 'test_retry') return ['Test failed, retrying.'];
  return ['Performance below acceptable thresholds.', 'Metrics may be less reliable.'];
}

function degradedDescText(lt) {
  const segs = degradedDesc(lt);
  if (segs.length <= 1) return segs[0] || '';
  const first = segs[0];
  if (first.endsWith(':')) return first + ' ' + segs.slice(1).join('; ');
  return segs.join(' ');
}

function _metricValue(metric, lt) {
  const map = {
    tps: 'tps', ttft: 'ttft_ms', stall_count: 'stall_count',
    raw_p99_itl_ms: 'raw_p99_itl_ms', raw_median_itl_ms: 'raw_median_itl_ms',
    raw_max_itl_ms: 'raw_max_itl_ms', effective_itl_tail_ratio: 'effective_itl_tail_ratio',
    chunk_token_ratio: 'chunk_token_ratio', chunk_token_cv: 'chunk_token_cv',
    burst_arrival_pct: 'burst_arrival_pct',
  };
  return lt[map[metric]] ?? null;
}

export function degradedDescHTML(lt) {
  const r = (lt.degraded_reason || '').split(',')[0].trim();
  if (r === 'critical_tier' && lt.critical_metrics?.length) {
    const lines = lt.critical_metrics.map(m => {
      const colorCls = _tierColor(m, _metricValue(m, lt));
      const dot = colorCls ? `<span class="${colorCls}">●</span>` : '';
      return `${fmtMetricValue(m, lt)} ${dot}`;
    });
    return 'Critical metrics:<br>' + lines.join('<br>');
  }
  return degradedDesc(lt).join('<br>');
}

export function recordErrorText(h) {
  if (h.degraded) return degradedDescText(h);
  const retry = h.retry_attempt ? `↻ Retry ${h.retry_attempt}/${h.retry_total || '?'}` : '';
  const msg = h.error || '';
  const parts = [retry, msg].filter(Boolean);
  return parts.join(' \u2014 ');
}

const _LABEL_COLORS = { tps: '#22d3ee', ttft: '#a78bfa', uptime: '#fb923c', tails: '#f472b6', batching: '#2dd4bf' };

export function metricCellHTML({ label, tipKey, colorVar, valueCls, valueHTML, id, mode = 'card', extra = '', wrapperCls: wrapperOverride = '' }) {
  const isCard = mode === 'card';
  const _baseWrapperCls = isCard ? '' : 'bg-overlay rounded-lg p-2';
  const wrapperCls = wrapperOverride || _baseWrapperCls;
  const valSizeCls = 'text-base';
  const labelSpacing = 'mb-0.5';
  const labelCls = `text-[10px] uppercase tracking-wider ${labelSpacing} tip-label`;
  const colorStyle = colorVar ? `style="color:var(--chart-label-cc-${colorVar}, ${_LABEL_COLORS[colorVar] || ''})"` : '';
  const idAttr = id ? ` id="${id}"` : '';
  return `<div class="${wrapperCls}" data-tip="${tipKey}" tabindex="0">
    <div class="${labelCls}" ${colorStyle}>${label}${extra}</div>
    <div${idAttr} class="${valSizeCls} font-bold ${valueCls}">${valueHTML}</div>
  </div>`;
}

