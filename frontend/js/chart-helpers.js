// Chart dataset builders, axis configuration, tooltip rendering, and bucket
// transforms. Card charts normalize 0-1; modal charts use raw values with dual
// Y-axes. Bucket transforms handle both dedup (shared) and legacy array formats.
import { state, CC } from './state.js';
import { esc, BP_SM, isTouchDevice } from './utils.js';
import { fmtMsCompactPlain } from './format.js';
import { showTip, hideTip } from './tooltips.js';
import { chartColors as _chartColors } from './theme.js';
import { _hexToRgba, _ZONE_TIERS, _SCORE_THRESHOLDS } from './chart-plugins.js';


export const _TIME_SCALE = {
  displayFormats: { minute: 'HH:mm', hour: 'HH:mm', day: 'M/d' },
  tooltipFormat: 'M/d HH:mm',
};

const _GRADIENT_ALPHA = 0.35;
const _CARD_GRADIENT_ALPHA = 0.15;

export const _LINE_COLORS = {
  tps: CC.tps, ttft: CC.ttft, p99: CC.tails, batch: CC.batching, health: CC.batching,
  cs: CC.scoreC, ss: CC.scoreS, rs: CC.scoreR,
};

export const _KEY_CFG_METRIC = {
  tps: 'tps', ttft: 'ttft', p99: 'raw_p99_itl_ms', batch: 'chunk_token_ratio',
  health: 'ttft', cs: '__scores', ss: '__scores', rs: '__scores',
};

function _tierHexForValue(cfgKey, value) {
  if (value == null) return null;
  const isScores = cfgKey === '__scores';
  const cfg = isScores ? _SCORE_THRESHOLDS : state.colorThresholds?.[cfgKey];
  const tiers = state.colorThresholds?.tiers;
  if (!cfg?.thresholds || !tiers) return null;
  const ts = cfg.thresholds;
  const ge = cfg.higher_is_better;
  let idx = -1;
  for (let i = 0; i < ts.length; i++) {
    if (ge ? value >= ts[i] : value < ts[i]) { idx = i; break; }
  }
  if (idx < 0) idx = ts.length - 1;
  if (idx >= tiers.length) return null;
  const tierCfg = _ZONE_TIERS[tiers[idx].color];
  if (!tierCfg) return null;
  const cc = _chartColors();
  const hex = (cc[tierCfg.base] || '').trim();
  return /^#[0-9a-fA-F]{6}$/.test(hex) ? hex : null;
}

function _endpointDotColor(view, key, buckets) {
  const mKey = key === 'health' ? 'ttft' : key;
  const last = buckets.length - 1;
  if (last < 0) return _LINE_COLORS[key] || CC.tps;
  const value = buckets[last][mKey];
  const cfgKey = _KEY_CFG_METRIC[key] || key;
  return _tierHexForValue(cfgKey, value) || _LINE_COLORS[key] || CC.tps;
}

function _endpointArrays(data, dotColor) {
  const last = data.length - 1;
  return {
    pointRadius: data.map((_, i) => i === last ? 2.5 : 0),
    pointBackgroundColor: data.map((_, i) => i === last ? dotColor : 'transparent'),
  };
}

function _normalizeValues(values) {
  let min = Infinity, max = -Infinity;
  for (let i = 0; i < values.length; i++) {
    if (values[i] != null) { if (values[i] < min) min = values[i]; if (values[i] > max) max = values[i]; }
  }
  if (min === Infinity) return values.map(() => null);
  if (min === max) return values.map(v => v != null ? 0.5 : null);
  const range = max - min;
  return values.map(v => v != null ? 0.08 + ((v - min) / range) * 0.84 : null);
}

function _lineDS(key, buckets, opts) {
  const { normalized, yAxisID, dash, tension, pointRadius, borderWidth, pointHoverRadius, spanGaps, gradient, view, full, dataOverride } = opts;
  const mKey = key === 'health' ? 'ttft' : key;
  const rawValues = buckets.map(b => b[mKey]);
  const data = dataOverride || (normalized ? _normalizeValues(rawValues) : rawValues);
  const color = _LINE_COLORS[key] || CC.tps;
  const prScalar = typeof pointRadius === 'number' ? pointRadius : 0;
  const dotColor = _endpointDotColor(view, key, buckets);
  const ep = _endpointArrays(data, dotColor);
  const wantGradient = gradient ?? true;
  const gradAlpha = full ? _GRADIENT_ALPHA : _CARD_GRADIENT_ALPHA;
  return {
    label: opts.label,
    data,
    borderColor: color,
    backgroundColor: 'transparent',
    pointBackgroundColor: ep.pointBackgroundColor,
    pointHoverBackgroundColor: color,
    pointHoverRadius: pointHoverRadius ?? (prScalar ? prScalar + 2 : 4),
    borderDash: dash || [],
    tension: tension ?? 0.3,
    cubicInterpolationMode: 'monotone',
    borderCapStyle: 'round',
    borderJoinStyle: 'round',
    pointRadius: ep.pointRadius,
    borderWidth: borderWidth ?? 2,
    fill: wantGradient ? 'origin' : false,
    yAxisID,
    ...(spanGaps != null ? { spanGaps } : {}),
    ...(wantGradient ? { _gradientColor: _hexToRgba(color, gradAlpha), _glow: true } : {}),
  };
}

function _markerDS(buckets, type, yID, full) {
  const countKey = type === 'failure' ? 'failure_count' : 'degraded_count';
  const label = type === 'failure' ? 'Failure' : 'Degraded';
  const maxMarkers = full ? 20 : 8;
  const markerIdx = [];
  for (let i = 0; i < buckets.length; i++) {
    if (buckets[i][countKey] > 0) markerIdx.push(i);
  }
  let minGap = full ? 2 : 1;
  if (markerIdx.length > maxMarkers) {
    minGap = Math.max(minGap, Math.floor(buckets.length / maxMarkers));
  }
  const keep = new Set();
  let last = -minGap - 1;
  for (const i of markerIdx) {
    if (i - last >= minGap) { keep.add(i); last = i; }
  }
  const yVal = type === 'degraded' ? 0.82 : 0.95;
  const data = buckets.map((b, i) => keep.has(i) && b[countKey] > 0 ? yVal : null);
  const radius = buckets.map((b, i) => keep.has(i) && b[countKey] > 0 ? Math.min(2 + Math.log2(1 + b[countKey]), 6) : 0);
  const cc = _chartColors();
  const color = type === 'degraded' ? cc.degraded : cc.failure;
  const pointStyle = type === 'degraded' ? 'rectRot' : undefined;
  return {
    label,
    data, borderColor: color, backgroundColor: color,
    pointRadius: radius, pointHoverRadius: 5, showLine: false, yAxisID: yID,
    _isMarker: true,
    ...(pointStyle ? { pointStyle } : {}),
  };
}

export function _buildDatasets(buckets, full, view) {
  const normalized = !full;
  const isConsistency = view === 'consistency';
  const isHealth = view === 'health';
  const isScores = view === 'scores';
  const yLeft = full ? 'y-left' : 'y';
  const yRight = full ? (isHealth || isScores ? 'y-left' : 'y-right') : 'y';
  const yMarkers = full ? 'y-markers' : 'y';
  const pr = 0;
  const phr = 5;
  const bw = 2;
  const tens = 0.3;

  if (isScores) {
    const scoreCfg = [
      { key: 'cs', label: 'Consistency', dash: [] },
      { key: 'ss', label: 'Speed', dash: [6, 3] },
      { key: 'rs', label: 'Reliability', dash: [2, 4] },
    ];
    const datasets = [];
    for (const sc of scoreCfg) {
      const rawScores = buckets.map(b => b[sc.key]);
      const dataOverride = normalized ? rawScores.map(v => v != null ? v / 100 : null) : null;
      datasets.push(_lineDS(sc.key, buckets, {
        normalized, yAxisID: yLeft, label: sc.label, view, full,
        dash: sc.dash, tension: tens, pointRadius: pr, pointHoverRadius: phr,
        borderWidth: bw, spanGaps: true, dataOverride,
      }));
    }
    return datasets;
  }

  const datasets = [];

  const leftKey = isHealth ? 'health' : (isConsistency ? 'p99' : 'tps');
  const leftLabel = isHealth ? 'TTFT' : (isConsistency ? 'Tails' : 'TPS');
  datasets.push(_lineDS(leftKey, buckets, {
    normalized, yAxisID: yLeft, label: leftLabel, view, full,
    tension: tens, pointRadius: pr, pointHoverRadius: phr, borderWidth: bw,
  }));

  if (!isHealth) {
    const rightKey = isConsistency ? 'batch' : 'ttft';
    const rightLabel = isConsistency ? 'Batching' : 'TTFT';
    datasets.push(_lineDS(rightKey, buckets, {
      normalized, yAxisID: yRight, label: rightLabel, view, full,
      dash: [], tension: tens, pointRadius: pr, pointHoverRadius: phr, borderWidth: bw,
    }));
  }

  if (isHealth) {
    datasets.push(_markerDS(buckets, 'failure', yMarkers, full));
    datasets.push(_markerDS(buckets, 'degraded', yMarkers, full));
  }

  return datasets;
}

export function formatTooltipItem(ctx) {
  const dsLabel = ctx.dataset.label;
  const idx = ctx.dataIndex;
  const chart = ctx.chart;
  const buckets = chart._buckets;
  const bucketed = chart._bucketed;
  const b = buckets?.[idx];

  const _MARKER_CFG = {
    'Failure': { key: 'failure_count', icon: '\u2717', colorVar: '--color-notif-offline', noun: 'failure', nounPlural: 'failures' },
    'Degraded': { key: 'degraded_count', icon: '\u26a0', colorVar: '--color-notif-degraded', noun: 'degraded', nounPlural: 'degraded' },
  };
  const mc = _MARKER_CFG[dsLabel];
  if (mc) {
    if (!b) return `<b style="color:var(${mc.colorVar})">${mc.icon}</b> ${dsLabel}`;
    const cnt = b[mc.key] ?? 0;
    const prefix = `<b style="color:var(${mc.colorVar})">${mc.icon}</b>`;
    if (cnt <= 0) return `${prefix} ${dsLabel}`;
    return `${prefix} ${cnt} ${cnt === 1 ? mc.noun : mc.nounPlural}`;
  }

  if (dsLabel.startsWith('_')) return null;

  const metricKeys = { 'TPS': 'tps', 'TTFT': 'ttft', 'Tails': 'p99', 'Batching': 'batch', 'Consistency': 'cs', 'Speed': 'ss', 'Reliability': 'rs' };
  const mk = metricKeys[dsLabel];
  if (!mk || !b) return `${esc(dsLabel)}: --`;

  const val = b[mk];
  if (val == null) return `${esc(dsLabel)}: --`;

  if (mk === 'cs' || mk === 'ss' || mk === 'rs') {
    return `${esc(dsLabel)}: ${Math.round(val)}`;
  }

  if (mk === 'batch') {
    const est = b.batch_estimated;
    const ePfx = est ? '~' : '';
    if (bucketed && b[mk + '_lo'] != null && b[mk + '_lo'] !== b[mk + '_hi']) {
      return `${esc(dsLabel)}: ${ePfx}${val.toFixed(1)}\u00d7 (P10\u2013P90: ${ePfx}${b[mk + '_lo'].toFixed(1)}\u2013${ePfx}${b[mk + '_hi'].toFixed(1)}\u00d7)`;
    }
    return `${esc(dsLabel)}: ${ePfx}${val.toFixed(1)}\u00d7`;
  }
  if (mk === 'tps') {
    if (bucketed && b.tps_lo != null && b.tps_lo !== b.tps_hi) {
      return `${esc(dsLabel)}: ${val.toFixed(1)} (P10\u2013P90: ${b.tps_lo.toFixed(1)}\u2013${b.tps_hi.toFixed(1)})`;
    }
    return `${esc(dsLabel)}: ${val.toFixed(1)}`;
  }
  const estMs = mk === 'ttft' ? b.ttft_estimated : mk === 'p99' ? b.p99_estimated : false;
  const ePfx = estMs ? '~' : '';
  const showRange = mk === 'ttft' ? (b[mk + '_lo'] != null && b[mk + '_lo'] !== b[mk + '_hi']) : (bucketed && b[mk + '_lo'] != null && b[mk + '_lo'] !== b[mk + '_hi']);
  if (showRange) {
    return `${esc(dsLabel)}: ${ePfx}${fmtMsCompactPlain(val, 2)} (P10\u2013P90: ${ePfx}${fmtMsCompactPlain(b[mk + '_lo'], 1)}\u2013${ePfx}${fmtMsCompactPlain(b[mk + '_hi'], 1)})`;
  }
  return `${esc(dsLabel)}: ${ePfx}${fmtMsCompactPlain(val, 2)}`;
}

export const _SQRT2 = Math.sqrt(2);
export const _SQRT10 = Math.sqrt(10);
export const _SQRT50 = Math.sqrt(50);

export function _niceStep(range, maxTicks) {
  if (range <= 0 || maxTicks <= 0) return 0;
  const raw = range / maxTicks;
  if (raw <= 0) return 0;
  const power = Math.floor(Math.log10(raw));
  const frac = raw / Math.pow(10, power);
  const nice = frac >= _SQRT50 ? 10 : frac >= _SQRT10 ? 5 : frac >= _SQRT2 ? 2 : 1;
  return nice * Math.pow(10, power);
}

export function _ensureDistinctStep(step, min, max, formatFn) {
  if (!formatFn || !step || min == null) return step;
  for (let tries = 0; tries < 8; tries++) {
    let ok = true;
    for (let v = min; v + step <= max; v += step) {
      if (formatFn(v) === formatFn(v + step)) { ok = false; break; }
    }
    if (ok) return step;
    step *= 2;
  }
  return step;
}

export function _snapToStep(range, step) {
  if (step <= 0) return range;
  return { ...range, min: Math.max(0, Math.floor(range.min / step) * step), max: Math.ceil(range.max / step) * step, stepSize: step };
}

export function _logAxisRange(buckets, mKey, includeSpread) {
  const vals = [];
  for (const b of buckets) {
    const v = b[mKey];
    if (v != null && v > 0) {
      vals.push(v);
      if (includeSpread) {
        const lo = b[mKey + '_lo'];
        const hi = b[mKey + '_hi'];
        if (lo != null && lo > 0) vals.push(lo);
        if (hi != null && hi > 0) vals.push(hi);
      }
    }
  }
  if (vals.length === 0) return {};
  vals.sort((a, b) => a - b);
  let lo = vals[0], hi = vals[vals.length - 1];
  if (lo === hi) { lo /= 1.5; hi *= 1.5; }
  const logRange = Math.log10(hi / lo) || 1;
  const pad = Math.pow(10, Math.max(0.03, logRange * 0.05));
  return { min: lo / pad, max: hi * pad };
}

export function _axisRange(buckets, mKey, includeSpread, formatFn) {
  const vals = [];
  for (const b of buckets) {
    const v = b[mKey];
    if (v != null && v >= 0) {
      vals.push(v);
      if (includeSpread) {
        const lo = b[mKey + '_lo'];
        const hi = b[mKey + '_hi'];
        if (lo != null && lo >= 0) vals.push(lo);
        if (hi != null && hi >= 0) vals.push(hi);
      }
    }
  }
  if (vals.length === 0) return {};
  vals.sort((a, b) => a - b);
  let lo = vals[0], hi = vals[vals.length - 1];
  if (lo === hi) {
    const pad = Math.max(Math.abs(lo) * 0.1, 1);
    lo -= pad;
    hi += pad;
  }
  let rangeLo = lo, rangeHi = hi;
  const range = rangeHi - rangeLo || 1;
  rangeLo -= range * 0.05;
  rangeHi += range * 0.05;
  let step = _niceStep(rangeHi - rangeLo, 12);
  if (step <= 0) return { min: Math.max(0, rangeLo), max: rangeHi };
  step = _ensureDistinctStep(step, rangeLo, rangeHi, formatFn);
  const min = Math.max(0, Math.floor(rangeLo / step) * step);
  const max = Math.ceil(rangeHi / step) * step;
  return { min, max, stepSize: step };
}

export function _dataRange(buckets, view = 'speed') {
  let min = Infinity, max = -Infinity;
  const key = view === 'speed' ? 'tps' : view === 'consistency' ? 'p99' : view === 'scores' ? 'cs' : 'ttft';
  for (const b of buckets) {
    if (b.timestamp && b[key] != null) {
      const t = new Date(b.timestamp).getTime();
      if (isFinite(t)) { if (t < min) min = t; if (t > max) max = t; }
    }
  }
  if (!isFinite(min) || !isFinite(max)) return {};
  if (max - min < 1000) {
    const center = (min + max) / 2;
    min = center - 500;
    max = center + 500;
  }
  return { min, max };
}

export function externalTooltip(context) {
  const { chart, tooltip } = context;
  if (tooltip.opacity === 0) { hideTip(); return; }

  const canvasRect = chart.canvas.getBoundingClientRect();
  const area = chart.chartArea;
  let caretY = tooltip.caretY;
  if (area) {
    if (caretY < area.top) caretY = area.top;
    if (caretY > area.bottom) caretY = area.bottom;
  }
  const x = canvasRect.left + tooltip.caretX;
  const y = canvasRect.top + caretY;
  const anchorRect = { left: x, right: x, top: y, bottom: y, width: 0, height: 0 };

  const titleLines = tooltip.title || [];
  const items = tooltip.body ? tooltip.body.map((b, i) => {
    const ctx = tooltip.dataPoints?.[i];
    const label = ctx ? formatTooltipItem(ctx) : esc(b.lines?.[0] || '');
    if (label == null) return null;
    const dsLabel = ctx?.dataset?.label || '';
    const isMarker = ctx?.dataset?._isMarker === true;
    const color = tooltip.labelColors?.[i];
    const dot = (!isMarker && color) ? `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color.borderColor};margin-right:6px;vertical-align:middle"></span>` : '';
    return { key: dsLabel, html: `${dot}${label}` };
  }).filter(Boolean) : [];

  const chart_ = chart;
  const idx = tooltip.dataPoints?.[0]?.dataIndex;
  const bucket = chart_._buckets?.[idx];
  let bucketInfo = '';
  if (chart_._bucketed && bucket && bucket.count > 1) {
    bucketInfo = `<div style="color:var(--color-chart-tooltip-info);font-size:10px;margin-bottom:2px">${bucket.count} tests in period</div>`;
  }

  const view = chart_._view;
  const cc = _chartColors();
  const metricPairs = {
    speed: [
      { key: 'TPS', mk: 'tps', color: cc.tps },
      { key: 'TTFT', mk: 'ttft', color: cc.ttft },
    ],
    consistency: [
      { key: 'Tails', mk: 'p99', color: cc.tails },
      { key: 'Batching', mk: 'batch', color: cc.batching },
    ],
    health: [
      { key: 'TTFT', mk: 'ttft', color: cc.ttft },
    ],
  };
  const pairMetrics = metricPairs[view] || [];
  const seenKeys = new Set(items.map(it => it.key));
  for (const m of pairMetrics) {
    if (seenKeys.has(m.key)) continue;
    if (!bucket) continue;
    const val = bucket[m.mk];
    const lo = bucket[m.mk + '_lo'];
    const hi = bucket[m.mk + '_hi'];
    const est = bucket[m.mk + '_estimated'] ? '~' : '';
    let text;
    if (val == null) {
      text = `${m.key}: --`;
    } else if (m.mk === 'batch') {
      text = lo != null && lo !== hi ? `${m.key}: ${est}${val.toFixed(1)}\u00d7 (P10\u2013P90: ${est}${lo.toFixed(1)}\u2013${est}${hi.toFixed(1)}\u00d7)` : `${m.key}: ${est}${val.toFixed(1)}\u00d7`;
    } else if (m.mk === 'tps') {
      text = lo != null && lo !== hi ? `${m.key}: ${est}${val.toFixed(1)} (P10\u2013P90: ${est}${lo.toFixed(1)}\u2013${est}${hi.toFixed(1)})` : `${m.key}: ${est}${val.toFixed(1)}`;
    } else {
      text = lo != null && lo !== hi ? `${m.key}: ${est}${fmtMsCompactPlain(val, 2)} (P10\u2013P90: ${est}${fmtMsCompactPlain(lo, 1)}\u2013${est}${fmtMsCompactPlain(hi, 1)})` : `${m.key}: ${est}${fmtMsCompactPlain(val, 2)}`;
    }
    const dot = `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${m.color};margin-right:6px;vertical-align:middle"></span>`;
    items.push({ key: m.key, html: `${dot}${text}` });
  }

  const html = (titleLines.length ? `<div style="color:var(--color-chart-tooltip-title);font-weight:600;margin-bottom:4px">${esc(titleLines[0])}</div>` : '') +
    bucketInfo +
    items.map(i => `<div style="padding:1px 0;color:var(--color-chart-tooltip-item)">${i.html}</div>`).join('');

  showTip(html, anchorRect);
}

export const _TOOLTIP_FILTER = (item) => {
  if (item.dataset.showLine === false) return item.raw != null;
  return true;
};

export function chartOptions(full, view, axisRanges, expanded, chartW, chartH) {
  const isMobile = window.innerWidth < BP_SM;
  const ranges = axisRanges || {};
  const baseH = 300, baseW = 900;
  const h = chartH || (full ? 300 : 100);
  const w = chartW || (full ? 900 : 100);
  const yTicks = expanded
    ? Math.max(8, Math.round(12 + (h - baseH) / 80))
    : (isMobile ? 4 : 8);
  const xTicks = expanded
    ? Math.max(8, Math.round(12 + (w - baseW) / 150))
    : (isMobile ? 5 : 12);
  const logTicks = Math.max(11, Math.round(11 + (h - baseH) / 50));
  function _dedupCb(format) {
    return (v, i, ticks) => {
      const s = format(v);
      if (i > 0 && ticks[i - 1] != null && format(ticks[i - 1].value) === s) return '';
      return s;
    };
  }

  function tickOpts(range) {
    if (range.stepSize && range.max != null && range.min != null) {
      const rangeSpan = range.max - range.min;
      const count = Math.ceil(rangeSpan / range.stepSize) + 1;
      return { stepSize: range.stepSize, maxTicksLimit: Math.max(count, 2) };
    }
    return { maxTicksLimit: yTicks };
  }

  if (!full) {
    return {
      responsive: false, maintainAspectRatio: false, devicePixelRatio: 1,
      layout: { padding: { left: 0, right: 4, top: 0, bottom: 0 } },
      events: isTouchDevice ? [] : undefined,
      interaction: { mode: 'index', intersect: false },
      spanGaps: true,
      plugins: {
        legend: { display: false },
        tooltip: {
          enabled: false,
          external: isTouchDevice ? null : externalTooltip,
          callbacks: { label: (ctx) => formatTooltipItem(ctx), labelColor: (ctx) => ({ borderColor: ctx.dataset.borderColor, backgroundColor: ctx.dataset.borderColor }) },
          filter: _TOOLTIP_FILTER,
        },
        decimation: { enabled: false },
      },
      scales: {
        x: { type: 'time', bounds: 'data', time: _TIME_SCALE, ticks: { color: _chartColors().tick, font: { size: 9 }, maxTicksLimit: 5, maxRotation: 0, padding: 0 }, grid: { display: false }, afterFit(scale) { if (scale.height < 28) scale.height = 28; scale.paddingLeft = Math.max(scale.paddingLeft, 8); scale.paddingRight = Math.max(scale.paddingRight, 8); } },
        y: { display: false, min: 0, max: 1 },
      },
      animation: false,
      elements: { point: { radius: 0 } },
    };
  }

  const isConsistency = view === 'consistency';
  const isHealth = view === 'health';
  const isScores = view === 'scores';
  const leftIsLog = isConsistency || isHealth;
  const rightIsLog = !isConsistency && !isHealth && !isScores;
  const cc = _chartColors();
  const leftColor = isScores ? CC.scoreS : (isConsistency ? CC.tails : (isHealth ? CC.batching : CC.tps));
  const rightColor = isConsistency ? CC.batching : CC.ttft;
  const leftRange = ranges.left || {};
  const rightRange = ranges.right || {};

  const _logAfterBuildTicks = (scale) => {
    const aMin = scale.min, aMax = scale.max;
    if (!isFinite(aMin) || !isFinite(aMax) || aMin <= 0 || aMax <= aMin) return;
    const n = Math.max(2, scale.options.ticks.maxTicksLimit || 11);
    const logMin = Math.log10(aMin), logMax = Math.log10(aMax);
    const ticks = [];
    for (let i = 0; i < n; i++) {
      ticks.push({ value: Math.pow(10, logMin + (logMax - logMin) * i / (n - 1)) });
    }
    scale.ticks = ticks;
  };

  return {
    responsive: false, maintainAspectRatio: false, devicePixelRatio: 1,
    layout: { padding: { left: 0, right: 0, top: 0, bottom: 0 } },
    interaction: { mode: 'index', intersect: false },
    spanGaps: true,
    plugins: {
      legend: {
        display: true,
        labels: {
          color: cc.legend, font: { size: isMobile ? 9 : 11 }, boxWidth: isMobile ? 8 : 12, padding: isMobile ? 4 : 8,
          usePointStyle: true,
          generateLabels(chart) {
            return chart.data.datasets
              .map((ds, i) => ({ text: ds.label, datasetIndex: i, fillStyle: ds.borderColor, strokeStyle: ds.borderColor, lineWidth: ds.showLine === false ? 0 : (ds.borderWidth || 2), lineDash: ds.borderDash || [], hidden: !chart.isDatasetVisible(i), fontColor: cc.legend, pointStyle: ds.showLine === false ? (ds.pointStyle || 'circle') : 'line' }));
          },
        },
      },
      tooltip: {
        enabled: false,
        external: externalTooltip,
        callbacks: { label: (ctx) => formatTooltipItem(ctx), labelColor: (ctx) => ({ borderColor: ctx.dataset.borderColor, backgroundColor: ctx.dataset.borderColor }) },
        filter: _TOOLTIP_FILTER,
      },
      decimation: { enabled: false },
    },
    scales: {
      x: { type: 'time', bounds: 'data', time: _TIME_SCALE, ticks: { color: cc.tick, font: { size: isMobile ? 9 : 10 }, maxTicksLimit: xTicks, maxRotation: 0 }, grid: { color: cc.grid }, afterFit(scale) { if (scale.height < (isMobile ? 32 : 44)) scale.height = isMobile ? 32 : 44; scale.paddingLeft = Math.min(scale.paddingLeft, 10); scale.paddingRight = Math.min(scale.paddingRight, 10); } },
      'y-left': {
        type: leftIsLog ? 'logarithmic' : 'linear', position: 'left',
        ...(leftIsLog ? { afterBuildTicks: _logAfterBuildTicks } : {}),
        ...(leftIsLog ? { min: leftRange.min, max: leftRange.max } : { min: isScores ? 0 : leftRange.min, max: isScores ? 100 : leftRange.max }),
        ticks: { color: leftColor, font: { size: isMobile ? 9 : 10 }, callback: _dedupCb(v => isScores ? Math.round(v) : ((isConsistency || isHealth) ? fmtMsCompactPlain(v, 1) : Math.round(v))), ...(isScores ? { stepSize: 25, maxTicksLimit: 5 } : (leftIsLog ? { maxTicksLimit: logTicks, autoSkip: false } : tickOpts(leftRange))) },
        grid: { color: cc.gridSubtle, drawOnChartArea: true },
        title: { display: !isMobile, text: isScores ? 'Score' : (isConsistency ? 'Tails' : (isHealth ? 'TTFT' : 'TPS')), color: leftColor, font: { size: 11 } },
      },
      'y-right': {
        type: rightIsLog ? 'logarithmic' : 'linear', position: 'right',
        display: !(isHealth || isScores),
        ...(rightIsLog ? { afterBuildTicks: _logAfterBuildTicks } : {}),
        ...(rightIsLog ? { min: rightRange.min, max: rightRange.max } : { min: rightRange.min, max: rightRange.max }),
        ticks: { color: rightColor, font: { size: isMobile ? 9 : 10 }, callback: _dedupCb(v => isConsistency ? (v != null ? Math.round(v * 10) / 10 + '\u00d7' : '') : fmtMsCompactPlain(v, 1)), ...(rightIsLog ? { maxTicksLimit: logTicks, autoSkip: false } : tickOpts(rightRange)) },
        grid: { drawOnChartArea: false },
        title: { display: !isMobile, text: isConsistency ? 'Batching' : 'TTFT', color: rightColor, font: { size: 11 } },
      },
      'y-markers': {
        type: 'linear', position: 'right', display: false,
        min: 0, max: 1,
        grid: { drawOnChartArea: false },
      },
    },
    animation: false,
    elements: { point: { radius: 0 } },
  };
}

export function _axisConfig(buckets, full, view, expanded) {
  const isHealth = view === 'health';
  const isConsistency = view === 'consistency';
  const isScores = view === 'scores';
  const leftMKey = isScores ? 'cs' : (isHealth ? 'ttft' : (isConsistency ? 'p99' : 'tps'));
  const rightMKey = isScores ? null : (isHealth ? null : (isConsistency ? 'batch' : 'ttft'));
  const leftIsLog = isConsistency || isHealth;
  const rightIsLog = !isConsistency && !isHealth && !isScores && rightMKey != null;
  const _FMT = {
    tps: v => v,
    ttft: v => fmtMsCompactPlain(v, 1),
    p99: v => fmtMsCompactPlain(v, 1),
    batch: v => v != null ? v.toFixed(1) + '\u00d7' : '',
    cs: v => v != null ? Math.round(v) : '',
    ss: v => v != null ? Math.round(v) : '',
    rs: v => v != null ? Math.round(v) : '',
  };
  const axisRanges = isScores
    ? { left: { min: 0, max: 100, stepSize: 25 }, right: {} }
    : (full ? { left: leftIsLog ? _logAxisRange(buckets, leftMKey, expanded) : _axisRange(buckets, leftMKey, expanded, _FMT[leftMKey]), right: rightMKey ? (rightIsLog ? _logAxisRange(buckets, rightMKey, expanded) : _axisRange(buckets, rightMKey, expanded, _FMT[rightMKey])) : {} } : {});

  if (expanded && axisRanges.left) {
    for (const side of ['left', 'right']) {
      const r = axisRanges[side];
      if (r && r.max != null && r.stepSize) {
        const fmtFn = side === 'left' ? _FMT[leftMKey] : (rightMKey ? _FMT[rightMKey] : null);
        let step = _niceStep(r.max - r.min, 14);
        step = _ensureDistinctStep(step, r.min, r.max, fmtFn);
        if (step > 0) {
          Object.assign(r, _snapToStep(r, step));
        }
      }
    }
  }
  return axisRanges;
}

export function _transformCardBuckets(serverBuckets, view = 'speed') {
  const isDedup = serverBuckets && !Array.isArray(serverBuckets) && serverBuckets.shared;
  if (isDedup) {
    const shared = serverBuckets.shared;
    const viewData = serverBuckets[view] || [];
    return shared.map((s, i) => {
      const v = viewData[i] || {};
      const hasData = s.count > 0;
      const ar = s.available_rate;
      const failureCount = hasData ? (ar != null ? Math.round((1 - ar) * s.count) : 0) : 0;
      const degradedCount = hasData && s.degraded_rate != null ? Math.round(s.degraded_rate * s.count) : 0;
      const base = {
        _ts: s.ts,
        timestamp: new Date(s.ts * 1000).toISOString(),
        ttft_estimated: !!v.ttft_ms_estimated,
        p99_estimated: !!v.raw_p99_itl_ms_estimated,
        batch_estimated: !!v.chunk_token_ratio_estimated,
        failure_count: failureCount,
        degraded_count: degradedCount,
        count: s.count,
      };
      if (view === 'speed') {
        base.tps = v.tps; base.tps_lo = v.tps_p10 ?? v.tps; base.tps_hi = v.tps_p90 ?? v.tps;
        base.ttft = v.ttft_ms; base.ttft_lo = v.ttft_ms_p10 ?? v.ttft_ms; base.ttft_hi = v.ttft_ms_p90 ?? v.ttft_ms;
      } else if (view === 'consistency') {
        base.p99 = v.raw_p99_itl_ms; base.p99_lo = v.raw_p99_itl_ms_p10 ?? v.raw_p99_itl_ms; base.p99_hi = v.raw_p99_itl_ms_p90 ?? v.raw_p99_itl_ms;
        base.batch = v.chunk_token_ratio; base.batch_lo = v.chunk_token_ratio_p10 ?? v.chunk_token_ratio; base.batch_hi = v.chunk_token_ratio_p90 ?? v.chunk_token_ratio;
      } else if (view === 'scores') {
        base.cs = v.consistency_score;
        base.ss = v.speed_score;
        base.rs = v.reliability_score;
      } else if (view === 'health') {
        base.ttft = v.ttft_ms; base.ttft_lo = v.ttft_ms_p10 ?? v.ttft_ms; base.ttft_hi = v.ttft_ms_p90 ?? v.ttft_ms;
      }
      return base;
    });
  }
  return (serverBuckets || []).map(b => {
    const hasData = b.count > 0;
    const failureCount = hasData
      ? (b.available_rate != null
          ? Math.round((1 - b.available_rate) * b.count)
          : (b.available === false ? 1 : 0))
      : 0;
    const degradedCount = hasData && b.degraded_rate != null
      ? Math.round(b.degraded_rate * b.count)
      : 0;
    const base = {
      _ts: b.ts,
      timestamp: new Date(b.ts * 1000).toISOString(),
      ttft_estimated: !!b.ttft_ms_estimated,
      p99_estimated: !!b.raw_p99_itl_ms_estimated,
      batch_estimated: !!b.chunk_token_ratio_estimated,
      failure_count: failureCount,
      degraded_count: degradedCount,
      count: b.count,
    };
    if (view === 'speed') {
      base.tps = b.tps; base.tps_lo = b.tps_p10 ?? b.tps; base.tps_hi = b.tps_p90 ?? b.tps;
      base.ttft = b.ttft_ms; base.ttft_lo = b.ttft_ms_p10 ?? b.ttft_ms; base.ttft_hi = b.ttft_ms_p90 ?? b.ttft_ms;
    } else if (view === 'consistency') {
      base.p99 = b.raw_p99_itl_ms; base.p99_lo = b.raw_p99_itl_ms_p10 ?? b.raw_p99_itl_ms; base.p99_hi = b.raw_p99_itl_ms_p90 ?? b.raw_p99_itl_ms;
      base.batch = b.chunk_token_ratio; base.batch_lo = b.chunk_token_ratio_p10 ?? b.chunk_token_ratio; base.batch_hi = b.chunk_token_ratio_p90 ?? b.chunk_token_ratio;
    } else if (view === 'scores') {
      base.cs = b.consistency_score;
      base.ss = b.speed_score;
      base.rs = b.reliability_score;
    } else if (view === 'health') {
      base.ttft = b.ttft_ms; base.ttft_lo = b.ttft_ms_p10 ?? b.ttft_ms; base.ttft_hi = b.ttft_ms_p90 ?? b.ttft_ms;
    }
    return base;
  });
}

export function _transformModalBuckets(serverBuckets) {
  return (serverBuckets || []).map(b => {
    const tps = b.tps?.avg ?? b.tps;
    const ttft = b.ttft_ms?.avg ?? b.ttft_ms;
    const p99 = b.raw_p99_itl_ms?.avg ?? b.raw_p99_itl_ms;
    const batch = b.chunk_token_ratio?.avg ?? b.chunk_token_ratio;
    const hasData = b.count > 0;
    const failureCount = hasData && b.available_rate != null
      ? Math.round((1 - b.available_rate) * b.count)
      : (hasData && b.available === false ? 1 : 0);
    const degradedCount = hasData && b.degraded_rate != null
      ? Math.round(b.degraded_rate * b.count)
      : 0;
    return {
      _ts: b.ts,
      timestamp: new Date(b.ts * 1000).toISOString(),
      tps, tps_lo: b.tps?.p10 ?? b.tps, tps_hi: b.tps?.p90 ?? b.tps,
      ttft, ttft_lo: b.ttft_ms?.p10 ?? b.ttft_ms, ttft_hi: b.ttft_ms?.p90 ?? b.ttft_ms,
      p99, p99_lo: b.raw_p99_itl_ms?.p10 ?? b.raw_p99_itl_ms, p99_hi: b.raw_p99_itl_ms?.p90 ?? b.raw_p99_itl_ms,
      batch, batch_lo: b.chunk_token_ratio?.p10 ?? b.chunk_token_ratio, batch_hi: b.chunk_token_ratio?.p90 ?? b.chunk_token_ratio,
      cs: b.consistency_score?.avg ?? b.consistency_score,
      ss: b.speed_score?.avg ?? b.speed_score,
      rs: b.reliability_score?.avg ?? b.reliability_score,
      ttft_estimated: !!b.ttft_ms_estimated,
      p99_estimated: !!b.raw_p99_itl_ms_estimated,
      batch_estimated: !!b.chunk_token_ratio_estimated,
      failure_count: failureCount,
      degraded_count: degradedCount,
      count: b.count,
    };
  });
}

export function _calculateBuckets(chartType, force = false) {
  const key = `mw_buckets2_${chartType}`;
  if (!force) {
    const cached = localStorage.getItem(key);
    if (cached) return parseInt(cached);
  }
  const width = chartType === 'card'
    ? Math.min(window.innerWidth / 3, 300)
    : Math.min(window.innerWidth * 0.8, 800);
  const pxPerPoint = chartType === 'card' ? 8 : 12;
  const buckets = Math.max(10, Math.floor(width / pxPerPoint));
  localStorage.setItem(key, buckets);
  return buckets;
}

export function _hasCardViewData(cb, view) {
  if (!cb) return false;
  if (Array.isArray(cb)) return view === 'speed' && cb.length > 0;
  if (!cb.shared?.length) return false;
  const vd = cb[view];
  if (!vd) return false;
  return vd.length > 0;
}

export function _readParentSize(canvas) {
  const parent = canvas?.parentElement;
  if (!parent) return { w: 0, h: 0 };
  return { w: parent.clientWidth, h: parent.clientHeight };
}

export function _phId(canvasId) {
  return 'chart-ph-' + canvasId.replace(/^chart-/, '');
}

export function chartPhHTML(canvasId, text = 'No data') {
  return `<div id="${_phId(canvasId)}" class="absolute inset-0 flex items-center justify-center text-surface-500/40 text-xs select-none pointer-events-none">${text}</div>`;
}
