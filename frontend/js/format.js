// Tier system + formatting helpers. All metric value rendering flows through
// here: tier colors (explicit class maps for Tailwind v4 scanning), formatted
// HTML with styled unit spans, freshness tiers, and score/trend display.
import { state } from './state.js';
import { esc, SEP_TEXT, TIER_DOT, HELP, STATUS_GLYPH } from './utils.js';

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

// Ratios of age to check interval from app.yaml ui.freshness (finding F20)
function freshnessTier(ageSeconds, intervalSeconds) {
  if (ageSeconds == null || !intervalSeconds || intervalSeconds <= 0) return -1;
  if (ageSeconds < 0) return 0;
  const ratio = ageSeconds / intervalSeconds;
  const { aging_ratio: aging, stale_ratio: stale } = state.ui.freshness;
  if (ratio <= aging) return 0;
  if (ratio <= stale) return 1;
  return 2;
}

// Display names from /api/config (backend/state.py); empty until config arrives, never guessed

export function testTypeLabel(type, form = 'full') {
  return state.testTypeLabels[type]?.[form] ?? '';
}

export function statusLabel(status) { return state.statusLabels[status] ?? ''; }

// Models running a benchmark right now: an activity next to the statuses, labelled here once
export const TESTING_LABEL = 'Testing';

// form 'short' where space is tight (card tiles, table columns): METRIC_SHORT_LABELS
export function metricLabel(metric, form = 'full') {
  return (form === 'short' ? state.metricShortLabels : state.metricLabels)[metric] ?? '';
}

export function chartViewLabel(view) { return state.chartViewLabels[view] ?? ''; }

// The chart views in order, with their label and Help tip key (finding F80)
export function chartViews() {
  return state.chartViews.map(key => ({ key, label: chartViewLabel(key), tip: `chart_${key}` }));
}

// Capabilities a model has, in the backend's order (backend/state.py CAPABILITIES, finding F76)
export function modelCapabilities(entry) {
  return state.capabilities.filter(cap => entry?.[cap.key]);
}

export function capabilityLinesHTML(caps) {
  return caps.map(cap => `\u2022 ${esc(cap.label)}: ${esc(cap.desc)}`).join('<br>');
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
  stall: 'stall_count',
  p99Itl: 'raw_p99_itl_ms', medianItl: 'raw_median_itl_ms', maxItl: 'raw_max_itl_ms',
  itlTailRatio: 'effective_itl_tail_ratio', batching: 'chunk_token_ratio',
  burstArrival: 'burst_arrival_pct', chunkCv: 'chunk_token_cv',
};

function _tierDotHTML(colorCls) { return `<span class="${colorCls}">${TIER_DOT}</span>`; }

// A threshold in its metric's unit, as tier tips and the score filter print it
export function fmtThreshold(metric, v) {
  if (metric === 'stall_count' || metric === 'tps' || metric === 'chunk_token_cv') return String(v);
  if (metric === 'uptime' || metric === 'burst_arrival_pct' || metric === 'scores') return `${v}%`;
  if (metric === 'effective_itl_tail_ratio' || metric === 'chunk_token_ratio') return `${v}\u00d7`;
  return v >= 1000 ? `${v / 1000}s` : `${v}ms`;
}

// The value range of each tier of a metric, best first: "\u226580%", "60-80%", ..., "<20%"
export function tierRangeLabels(metric) {
  const cfg = state.colorThresholds[metric];
  if (!cfg?.thresholds) return [];
  const ts = cfg.thresholds;
  const f = v => fmtThreshold(metric, v);
  // "60-80%" rather than "60%-80%" when both ends share a unit
  const range = (lo, hi) => {
    const a = f(lo), b = f(hi), unit = b.replace(/^[\d.]+/, '');
    return unit && a.endsWith(unit) ? `${a.slice(0, -unit.length)}-${b}` : `${a}-${b}`;
  };
  if (cfg.higher_is_better) {
    return ts.map((t, i) => i === 0 ? `\u2265${f(t)}` : i === ts.length - 1 ? `<${f(ts[i - 1])}` : range(t, ts[i - 1]));
  }
  return ts.map((t, i) => i === 0 ? `<${f(t)}` : i === ts.length - 1 ? `\u2265${f(ts[i - 1])}` : range(ts[i - 1], t));
}

export function tierScaleHTML(tipKey) {
  const metric = _TIP_TO_METRIC[tipKey];
  if (!metric) return '';
  const tiers = state.colorThresholds.tiers;
  const labels = tierRangeLabels(metric);
  if (!tiers || !labels.length) return '';
  return '<br>' + labels.map((label, i) => `${_tierDotHTML(TIER_TEXT[tiers[i]?.color] || 'text-text-secondary')} ${label}`).join(' ');
}

export function tpsColor(t) { return _tierColor('tps', t); }
export function ttftColor(ms) { return _tierColor('ttft', ms); }
export function uptimeColor(pct) { return _tierColor('uptime', pct); }

export function p99ItlColor(ms) { return _tierColor('raw_p99_itl_ms', ms); }
export function tailColor(r) { return _tierColor('effective_itl_tail_ratio', r); }
export function batchingColor(r) { return _tierColor('chunk_token_ratio', r); }
export function stallColor(n) { return _tierColor('stall_count', n); }

// Composite scores use the color_thresholds.scores tiers from app.yaml (finding F81)
export function scoreTierIdx(score) { return _tierIdx('scores', score); }

export function scoreColor(score) {
  return score == null ? 'text-text-muted' : _tierColor('scores', score);
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

// The one Critical marker: values, notifications and the Help legend (finding F50)
export function criticalMarkHTML(html) { return `<span class="critical-mark">${html}</span>`; }

export function fmtCritical(metric, value, formattedText) {
  if (value == null || formattedText == null) return formattedText;
  const tiers = state.colorThresholds?.tiers;
  if (!tiers || _tierIdx(metric, value) !== tiers.length - 1) return formattedText;
  return criticalMarkHTML(formattedText);
}

export function fmtNum(n, dec = 1) { return n != null ? Number(n).toFixed(dec) : '--'; }

export function moeDetail(e) {
  if (!e || !e.num_experts) return '';
  const parts = [e.num_experts + ' routed'];
  if (e.num_shared_experts) parts.push(e.num_shared_experts + ' shared');
  if (e.num_experts_per_tok) parts.push(e.num_experts_per_tok + '/tok');
  return parts.join(SEP_TEXT);
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
      const dot = colorCls ? _tierDotHTML(colorCls) : '';
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
  return parts.join(SEP_TEXT);
}

// Metric tiles, one definition for card and modal: same value, label and colours for the same
// metric everywhere (finding F58: the card showed health-check TTFT under the benchmark's label).
// value(data) reads the model's summary; metric is the METRIC_LABELS and color_thresholds key.
export const METRIC_TILES = {
  uptime: { metric: 'uptime', tip: 'uptime', colorVar: 'uptime', value: d => d.uptime_pct, color: v => uptimeColor(v), fmt: v => fmtUptime(v), fromBenchmark: false },
  tps: { metric: 'tps', tip: 'tps', colorVar: 'tps', value: d => d.last_test?.tps, color: v => tpsColor(v), fmt: v => fmtTps(v), fromBenchmark: true },
  ttft: { metric: 'ttft', tip: 'ttft', colorVar: 'ttft', value: d => d.last_test?.ttft_ms, color: v => ttftColor(v), fmt: v => fmtTTFT(v), fromBenchmark: true },
  p99: { metric: 'raw_p99_itl_ms', tip: 'p99Itl', colorVar: 'tails', value: d => d.last_test?.raw_p99_itl_ms, color: v => p99ItlColor(v), fmt: v => fmtMsCompact(v), fromBenchmark: true },
  stalls: { metric: 'stall_count', tip: 'stall', value: d => d.last_test?.stall_count || null, color: v => stallColor(v), fmt: v => String(v), fromBenchmark: true },
  tail: { metric: 'effective_itl_tail_ratio', tip: 'itlTailRatio', value: d => d.last_test?.effective_itl_tail_ratio, color: v => tailColor(v), fmt: v => fmtTail(v), fromBenchmark: true },
  batch: { metric: 'chunk_token_ratio', tip: 'batching', value: d => d.last_test?.chunk_token_ratio, color: v => batchingColor(v), fmt: v => fmtBatching(v), fromBenchmark: true },
  jitter: { metric: 'network_jitter_ms', tip: 'networkJitter', value: d => d.last_test?.network_jitter_ms, color: () => 'text-text-primary', fmt: v => fmtMsCompact(v), fromBenchmark: true },
};

// The model's id under its name, only when it says something the name does not (finding F92:
// a display name that defaults to the id printed it twice)
export function secondaryModelId(entry) {
  return entry.model_id && entry.model_id !== entry.name ? entry.model_id : '';
}

// A benchmark metric has no value because the last benchmark failed: say so instead of hiding
// the tile (finding F59)
export function lastBenchmarkFailed(data) { return data.last_test?.success === false; }

export function metricTileHTML(key, data, { mode = 'card', form = 'full', id = '', extra = '' } = {}) {
  const t = METRIC_TILES[key];
  const v = t.value(data);
  const failed = v == null && t.fromBenchmark && lastBenchmarkFailed(data);
  const valueHTML = v != null ? fmtCritical(t.metric, v, t.fmt(v)) : failed ? `<span class="text-text-muted">${STATUS_GLYPH.failed}</span>` : '-';
  return metricCellHTML({
    label: esc(metricLabel(t.metric, form)), tipKey: failed ? 'lastBenchmarkFailed' : t.tip, colorVar: t.colorVar,
    valueCls: v != null ? t.color(v) : '', valueHTML, id, mode, extra,
    wrapperCls: v == null && !failed ? 'hidden' : '',
  });
}

export function metricCellHTML({ label, tipKey, colorVar, valueCls, valueHTML, id, mode = 'card', extra = '', wrapperCls: wrapperOverride = '' }) {
  const isCard = mode === 'card';
  const _baseWrapperCls = isCard ? '' : 'bg-overlay rounded-lg p-2';
  const wrapperCls = wrapperOverride || _baseWrapperCls;
  const labelCls = 'text-[10px] uppercase tracking-wider mb-0.5 tip-label';
  const colorStyle = colorVar ? ` style="color:var(--chart-label-cc-${colorVar})"` : '';
  const idAttr = id ? ` id="${id}"` : '';
  // Card tiles are no tab stops of their own: the card's button opens the details (finding F74)
  const focus = isCard ? '' : ' tabindex="0"';
  return `<div class="${wrapperCls}" data-tip="${tipKey}"${focus}>
    <div class="${labelCls}"${colorStyle}>${label}${extra}</div>
    <div${idAttr} class="text-base font-bold ${valueCls}">${valueHTML}</div>
  </div>`;
}

// ── Help texts that state configured values (finding F78) ───────────────────
// Everything else is in utils.js HELP; these read /api/config, so the numbers are the configured ones.

function _weightsList(weights) {
  return Object.keys(weights).map(k => esc(metricLabel(k === 'ttft_ms' ? 'ttft' : k))).join(', ');
}

const _HELP_FROM_CONFIG = {
  stall: () => state.stalls && `Pause longer than ${state.stalls.visible_threshold_ms}ms between chunks (plus the provider's network jitter, or half its round trip when jitter is unknown).`,
  hiccups: () => state.stalls && `Inter-chunk gaps longer than ${state.stalls.hiccup_multiplier}\u00d7 the median ITL (adaptive threshold).<br>Less severe than stalls but indicate uneven delivery.`,
  consistency: () => state.scoreWeights && `Consistency score: output smoothness from ${_weightsList(state.scoreWeights.consistency)}, each scored by its tier.`,
  scores: () => state.scoreWeights && `Composite scores (0-100) of recent tests, each metric scored by its tier:<br>C = Consistency (${_weightsList(state.scoreWeights.consistency)})<br>S = Speed (${_weightsList(state.scoreWeights.speed)})<br>R = Reliability (uptime, scaled by the share of benchmarks that were not degraded)<br>\u2191\u2193 = trend: the last part of the window against the part before it.`,
  degraded: () => state.degradedCriticalMetrics != null && `Model is degraded: performance below acceptable thresholds.<br>Causes:<br>\u2022 Critical tier: ${state.degradedCriticalMetrics} or more metrics at the worst tier<br>\u2022 Stream error: stream interrupted after tokens<br>\u2022 Insufficient output: too few tokens for reliable metrics<br>\u2022 Last benchmark failed while health checks pass`,
  degraded_critical_tier: () => state.degradedCriticalMetrics != null && `${state.degradedCriticalMetrics} or more metrics reached the Critical (worst) tier, indicating severely degraded performance.<br>Critical values are underlined.`,
  capabilities: () => state.capabilities.length > 0 && `Model capabilities:<br>${capabilityLinesHTML(state.capabilities)}`,
  lastBenchmarkFailed: () => 'The last benchmark failed, so this metric has no current value.<br>Open the card for the error.',
};

// A Help text by key: the configured ones above, else the static HELP entry; '' when neither has it
export function helpText(key) {
  const fromConfig = _HELP_FROM_CONFIG[key];
  if (fromConfig) return fromConfig() || '';
  return HELP[key] ?? '';
}

export function isHelpKey(key) { return key in _HELP_FROM_CONFIG || key in HELP; }

