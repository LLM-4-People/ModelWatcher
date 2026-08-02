// Custom Chart.js plugins: evenTimeTicks (overrides native tick generation),
// dayBoundary (midnight separators), threshold (dashed TPS tier lines),
// cardZones (colored tier backgrounds), gradientFill, and glow.
import { state } from './state.js';
import { fmtMsCompactPlain } from './format.js';
import { chartColors as _chartColors } from './theme.js';


export function _hexToRgba(hex, a) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${Math.min(1, Math.max(0, a))})`;
}

function _zoneGradient(ctx, hex, baseAlpha, yTop, yBot) {
  if (yBot - yTop < 4) return _hexToRgba(hex, baseAlpha);
  const grad = ctx.createLinearGradient(0, yTop, 0, yBot);
  grad.addColorStop(0, _hexToRgba(hex, baseAlpha * 1.25));
  grad.addColorStop(1, _hexToRgba(hex, baseAlpha * 0.75));
  return grad;
}

function _fillZoneRect(ctx, x, y, w, h, radii) {
  const maxR = Math.min(w / 2, h / 2);
  const clamped = radii.map(r => Math.min(r, maxR));
  if (clamped.some(r => r > 0) && typeof ctx.roundRect === 'function') {
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, clamped);
    ctx.fill();
  } else {
    ctx.fillRect(x, y, w, h);
  }
}

export const _ZONE_TIERS = {
  'accent-400': { base: 'zoneBaseAccent',     fill: 0.14, border: 0.30 },
  'success-400': { base: 'zoneBaseSuccess',   fill: 0.12, border: 0.30 },
  'warn-400':    { base: 'zoneBaseWarn',      fill: 0.16, border: 0.35 },
  'danger-400':  { base: 'zoneBaseDanger',    fill: 0.20, border: 0.35 },
  'danger-700':  { base: 'zoneBaseDangerDark', fill: 0.26, border: 0.45 },
  'teal-400':    { base: 'zoneBaseTeal',      fill: 0.12, border: 0.30 },
};

export const _ZONE_METRIC_MAP = {
  speed: { cfg: 'tps', bucket: 'tps' },
  consistency: { cfg: 'raw_p99_itl_ms', bucket: 'p99' },
  health: { cfg: 'ttft', bucket: 'ttft' },
  scores: { cfg: '__scores', bucket: 'cs', normFn: v => v / 100 },
};

export const _SCORE_THRESHOLDS = { higher_is_better: true, thresholds: [80, 60, 40, 20, 0] };

export function evenTimeTicks(scale) {
  const maxTicks = scale.options.ticks.maxTicksLimit || 11;
  if (maxTicks < 2) return;
  const min = scale.min;
  const max = scale.max;
  if (!isFinite(min) || !isFinite(max) || max <= min) return;
  const spanMs = max - min;
  const step = spanMs / (maxTicks - 1);
  const ticks = [];
  const startDate = new Date(min);
  const endDate = new Date(max);
  const crossesMidnight = startDate.getDate() !== endDate.getDate() ||
    startDate.getMonth() !== endDate.getMonth() ||
    startDate.getFullYear() !== endDate.getFullYear();
  if (!crossesMidnight) {
    for (let i = 0; i < maxTicks; i++) {
      const ms = min + step * i;
      const d = new Date(ms);
      ticks.push({ value: ms, label: String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0') });
    }
  } else {
    let prevDay = '';
    for (let i = 0; i < maxTicks; i++) {
      const ms = min + step * i;
      const d = new Date(ms);
      const day = d.getFullYear() + '-' + d.getMonth() + '-' + d.getDate();
      const hh = String(d.getHours()).padStart(2, '0');
      const mi = String(d.getMinutes()).padStart(2, '0');
      if (day !== prevDay) {
        ticks.push({ value: ms, label: [(d.getMonth() + 1) + '/' + d.getDate(), hh + ':' + mi] });
        prevDay = day;
      } else {
        ticks.push({ value: ms, label: ['', hh + ':' + mi] });
      }
    }
  }
  scale.ticks = ticks;
}

export const dayBoundaryPlugin = {
  id: 'dayBoundary',
  beforeDatasetsDraw(chart) {
    if (!chart._full) return;
    const xScale = chart.scales.x;
    if (!xScale || xScale.type !== 'time') return;
    const min = xScale.min;
    const max = xScale.max;
    if (!isFinite(min) || !isFinite(max)) return;
    const start = new Date(min);
    const firstMidnight = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 1, 0, 0, 0).getTime();
    if (firstMidnight >= max) return;
    const ctx = chart.ctx;
    const { left, right, top, bottom } = chart.chartArea;
    const isFull = chart._full;
    ctx.save();
    const cc = _chartColors();
    ctx.strokeStyle = isFull ? cc.dayBoundary : cc.dayBoundarySubtle;
    ctx.lineWidth = 1;
    ctx.setLineDash(isFull ? [8, 4] : [4, 4]);
    for (let t = firstMidnight; t < max; t += 86400000) {
      const x = xScale.getPixelForValue(t);
      if (x >= left && x <= right) {
        ctx.beginPath();
        ctx.moveTo(x, top);
        ctx.lineTo(x, bottom);
        ctx.stroke();
      }
    }
    ctx.restore();
  },
};

export const evenTimeTicksPlugin = {
  id: 'evenTimeTicks',
  afterBuildTicks(chart) {
    const xScale = chart.scales.x;
    if (xScale && xScale.type === 'time') evenTimeTicks(xScale);
  },
};

export const thresholdPlugin = {
  id: 'threshold',
  beforeDatasetsDraw(chart) {
    if (!chart._full) return;
    const view = chart._view;
    if (view !== 'speed') return;
    const metric = 'tps';
    const cfg = state.colorThresholds?.[metric];
    const tiers = state.colorThresholds?.tiers;
    if (!cfg?.thresholds || !tiers) return;
    const scale = chart.scales['y-left'];
    if (!scale) return;
    const ctx = chart.ctx;
    const { left, right, top, bottom } = chart.chartArea;
    const ts = cfg.thresholds;
    const ge = cfg.higher_is_better;
    const cc = _chartColors();
    const borders = {};
    for (const [tier, cfg] of Object.entries(_ZONE_TIERS)) {
      const hex = (cc[cfg.base] || '').trim();
      borders[tier] = /^#[0-9a-fA-F]{6}$/.test(hex) ? _hexToRgba(hex, cfg.border) : 'rgba(100,100,100,0.30)';
    }
    const labels = [];

    for (let i = 0; i < ts.length && i < tiers.length; i++) {
      if (ge && i === 0) continue;
      const boundary = ts[i];
      if (!isFinite(boundary)) continue;
      const yVal = scale.getPixelForValue(boundary);
      if (yVal < top || yVal > bottom) continue;
      const color = borders[tiers[i].color] || cc.zoneLabel;
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.setLineDash([6, 4]);
      ctx.beginPath();
      ctx.moveTo(left, yVal);
      ctx.lineTo(right, yVal);
      ctx.stroke();
      ctx.restore();
      const labelText = metric === 'tps' ? String(boundary) : fmtMsCompactPlain(boundary, 1);
      labels.push({ y: yVal, text: labelText, color });
    }

    if (labels.length === 0) return;
    ctx.save();
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    ctx.font = '10px ui-monospace, monospace';
    for (const lbl of labels) {
      ctx.fillStyle = lbl.color;
      ctx.fillText(lbl.text, right - 4, lbl.y - 6);
    }
    ctx.restore();
  },
};

export const gradientPlugin = {
  id: 'gradientFill',
  beforeDatasetsDraw(chart) {
    const ctx = chart.ctx;
    const area = chart.chartArea;
    if (!area) return;
    chart.data.datasets.forEach((ds, i) => {
      if (!ds._gradientColor || ds.showLine === false) return;
      const meta = chart.getDatasetMeta(i);
      const scale = chart.scales[ds.yAxisID] || chart.scales['y-left'];
      let topY = area.top;
      if (scale && meta.data) {
        let minPix = Infinity;
        for (const pt of meta.data) {
          if (pt && isFinite(pt.y) && pt.y < minPix) minPix = pt.y;
        }
        if (isFinite(minPix)) topY = Math.max(area.top, minPix);
      }
      const g = ctx.createLinearGradient(0, topY, 0, area.bottom);
      g.addColorStop(0, ds._gradientColor);
      g.addColorStop(1, 'rgba(0,0,0,0)');
      ds.backgroundColor = g;
      if (meta.dataset) meta.dataset.options.backgroundColor = g;
    });
  },
};

export const glowPlugin = {
  id: 'glow',
  beforeDatasetDraw(chart, args) {
    const ds = chart.data.datasets[args.index];
    if (!ds._glow) return;
    const ctx = chart.ctx;
    ctx.save();
    ctx.shadowColor = ds.borderColor;
    ctx.shadowBlur = chart._full ? 6 : 4;
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = 0;
  },
  afterDatasetDraw(chart, args) {
    const ds = chart.data.datasets[args.index];
    if (!ds._glow) return;
    chart.ctx.restore();
  },
};

export const cardZonesPlugin = {
  id: 'cardZones',
  beforeDatasetsDraw(chart) {
    const view = chart._view;
    const mapping = _ZONE_METRIC_MAP[view];
    if (!mapping) return;
    const isScores = mapping.cfg === '__scores';
    const cfg = isScores ? _SCORE_THRESHOLDS : state.colorThresholds?.[mapping.cfg];
    const tiers = state.colorThresholds?.tiers;
    if (!cfg?.thresholds || !tiers) return;
    const buckets = chart._buckets;
    if (!buckets || buckets.length === 0) return;
    const full = chart._full;
    const scale = full ? chart.scales['y-left'] : chart.scales.y;
    if (!scale) return;
    const { left, right, top, bottom } = chart.chartArea;
    const ctx = chart.ctx;
    let pixelFor;
    if (full) {
      pixelFor = (v) => scale.getPixelForValue(v);
    } else {
      let dMin = Infinity, dMax = -Infinity;
      for (const b of buckets) {
        const v = b[mapping.bucket];
        if (v != null) {
          if (v < dMin) dMin = v;
          if (v > dMax) dMax = v;
        }
      }
      if (dMin === Infinity) return;
      if (dMin === dMax) { dMin -= 1; dMax += 1; }
      const norm = mapping.normFn || (v => 0.05 + ((v - dMin) / (dMax - dMin)) * 0.9);
      pixelFor = (v) => scale.getPixelForValue(norm(v));
    }
    const ts = cfg.thresholds;
    const ge = cfg.higher_is_better;
    const zones = [];
    if (ge) {
      for (let i = 0; i < ts.length && i < tiers.length; i++) {
        const upper = i === 0 ? Infinity : ts[i - 1];
        const lower = i === ts.length - 1 ? -Infinity : ts[i];
        zones.push({ upper, lower, color: tiers[i].color });
      }
    } else {
      for (let i = 0; i < ts.length && i < tiers.length; i++) {
        const lower = i === 0 ? -Infinity : ts[i - 1];
        const upper = i === ts.length - 1 ? Infinity : ts[i];
        zones.push({ lower, upper, color: tiers[i].color });
      }
    }
    ctx.save();
    ctx.beginPath();
    ctx.rect(left, top, right - left, bottom - top);
    ctx.clip();
    const zx = left;
    const zw = right - left;
    const radius = Math.min(6, zw / 2);
    const rects = [];
    const cc = _chartColors();
    for (const z of zones) {
      const yUpper = z.upper === Infinity ? top : (z.upper === -Infinity ? bottom : pixelFor(z.upper));
      const yLower = z.lower === -Infinity ? bottom : (z.lower === Infinity ? top : pixelFor(z.lower));
      const yt = Math.max(Math.min(yUpper, yLower), top);
      const yb = Math.min(Math.max(yUpper, yLower), bottom);
      if (yt >= yb) continue;
      const tierCfg = _ZONE_TIERS[z.color];
      const hex = tierCfg ? (cc[tierCfg.base] || '').trim() : '';
      const alpha = tierCfg?.fill || 0.07;
      const fill = /^#[0-9a-fA-F]{6}$/.test(hex) ? _zoneGradient(ctx, hex, alpha, yt, yb) : 'rgba(100,100,100,0.07)';
      rects.push({ y: yt, h: yb - yt, fill });
    }
    for (let i = 0; i < rects.length; i++) {
      const r = rects[i];
      const isFirst = i === 0;
      const isLast = i === rects.length - 1;
      const tl = isFirst ? radius : 0;
      const tr = isFirst ? radius : 0;
      const br = isLast ? radius : 0;
      const bl = isLast ? radius : 0;
      ctx.fillStyle = r.fill;
      _fillZoneRect(ctx, zx, r.y, zw, r.h, [tl, tr, br, bl]);
    }
    ctx.restore();
  },
};
