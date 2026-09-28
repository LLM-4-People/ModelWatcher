// Theme preference (follow the system, light or dark) and the chart colours read from CSS custom
// properties, so charts follow the active theme without hex values of their own (finding F82).
import { state, BOOT, LS } from './state.js';
import { nextInCycle, cap, setColorResolver, logTag, logWarn } from './utils.js';

const _systemMql = window.matchMedia('(prefers-color-scheme: dark)');
const _CHART_BATCH = 5;
// The stored preference cycles through these; null (no stored value) follows the system (F56)
const _PREFS = () => [null, ...BOOT.themes];
const _DARK = () => BOOT.themes[1];

function _storedPref() {
  const v = localStorage.getItem(LS.THEME);
  return _PREFS().includes(v) ? v : null;
}

function isDark() {
  const pref = _storedPref();
  return pref ? pref === _DARK() : _systemMql.matches;
}

const _prefLabel = pref => (pref ? cap(pref) : 'System');

let _ccCache = null;

// The page's own colour parser for forms utils.js does not read itself (oklch(), hsl(), names):
// one pixel drawn and read back (finding F83)
let _probeCtx = null;
function _canvasColor(css) {
  _probeCtx ??= document.createElement('canvas').getContext('2d', { willReadFrequently: true });
  const ctx = _probeCtx;
  // An invalid value leaves fillStyle unchanged, so two different sentinels tell it apart
  ctx.fillStyle = 'black';
  ctx.fillStyle = css;
  const first = ctx.fillStyle;
  ctx.fillStyle = 'white';
  ctx.fillStyle = css;
  if (ctx.fillStyle !== first) return null;
  ctx.clearRect(0, 0, 1, 1);
  ctx.fillRect(0, 0, 1, 1);
  const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
  return [r, g, b, a / 255];
}

function _readAllStyles() {
  const s = getComputedStyle(document.documentElement);
  const v = (n) => s.getPropertyValue(n).trim();
  _ccCache = {
    tps: v('--chart-cc-tps'),
    ttft: v('--chart-cc-ttft'),
    uptime: v('--chart-cc-uptime'),
    tails: v('--chart-cc-tails'),
    batching: v('--chart-cc-batching'),
    scoreC: v('--chart-cc-scoreC'),
    scoreS: v('--chart-cc-scoreS'),
    scoreR: v('--chart-cc-scoreR'),
    tick: v('--color-chart-tick'),
    legend: v('--color-chart-legend'),
    grid: v('--color-chart-grid'),
    gridSubtle: v('--color-chart-grid-subtle'),
    dayBoundary: v('--color-chart-day-boundary'),
    dayBoundarySubtle: v('--color-chart-day-boundary-subtle'),
    tooltipBg: v('--color-chart-tooltip-bg'),
    tooltipBorder: v('--color-chart-tooltip-border'),
    tooltipTitle: v('--color-chart-tooltip-title'),
    tooltipItem: v('--color-chart-tooltip-item'),
    tooltipInfo: v('--color-chart-tooltip-info'),
    zoneLabel: v('--color-chart-zone-label'),
    zoneBaseAccent: v('--chart-zone-base-accent'),
    zoneBaseSuccess: v('--chart-zone-base-success'),
    zoneBaseWarn: v('--chart-zone-base-warn'),
    zoneBaseDanger: v('--chart-zone-base-danger'),
    zoneBaseDangerDark: v('--chart-zone-base-danger-dark'),
    zoneBaseTeal: v('--chart-zone-base-teal'),
    failure: v('--color-notif-offline'),
    degraded: v('--color-notif-degraded'),
    baseColor: v('--color-base'),
  };
  state._chartColorsDirty = false;
  return _ccCache;
}

function chartColors() {
  if (!state._chartColorsDirty && _ccCache) return _ccCache;
  return _readAllStyles();
}

function _applyChartOpts(cc) {
  for (const [, chart] of Object.entries(state.charts)) {
    if (!chart || !chart.options) continue;
    const opts = chart.options;
    if (opts.scales?.x?.ticks) opts.scales.x.ticks.color = cc.tick;
    if (opts.scales?.x?.grid) opts.scales.x.grid.color = cc.grid;
    const yKeys = ['y', 'yRight', 'y-left', 'y-right'];
    for (const k of yKeys) {
      const s = opts.scales?.[k];
      if (!s) continue;
      if (s.grid) s.grid.color = cc.gridSubtle;
    }
    if (opts.plugins?.legend?.labels) {
      opts.plugins.legend.labels.color = cc.legend;
      if (Array.isArray(opts.plugins.legend.labels.labels)) {
        opts.plugins.legend.labels.labels = opts.plugins.legend.labels.labels.map(l => ({ ...l, fontColor: cc.legend }));
      }
    }
  }
}

function _renderChartBatch(entries, start) {
  const end = Math.min(start + _CHART_BATCH, entries.length);
  for (let i = start; i < end; i++) {
    const chart = entries[i][1];
    if (chart?.update) chart.update('none');
  }
  if (end < entries.length) {
    requestAnimationFrame(() => _renderChartBatch(entries, end));
  }
}

function updateCharts(batched) {
  const cc = chartColors();
  _applyChartOpts(cc);
  const entries = Object.entries(state.charts).filter(([, c]) => c?.update);
  if (batched && entries.length) {
    _renderChartBatch(entries, 0);
  } else {
    for (const [, chart] of entries) chart.update('none');
  }
}

function _labelButton() {
  const btn = document.getElementById('theme-btn');
  if (!btn) return;
  const pref = _storedPref();
  const current = pref ? _prefLabel(pref) : `${_prefLabel(null)} (${isDark() ? 'dark' : 'light'})`;
  btn.dataset.themePref = pref ?? 'system';
  btn.setAttribute('aria-label', `Theme: ${current}. Switch to ${_prefLabel(nextInCycle(_PREFS(), pref))}`);
}

function applyTheme(batchCharts) {
  const dark = isDark();
  document.documentElement.classList.toggle('dark', dark);
  state._chartColorsDirty = true;
  _labelButton();
  requestAnimationFrame(() => {
    const cc = _readAllStyles();
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = cc.baseColor;
    if (batchCharts) updateCharts(true);
  });
}

function _transitionTheme() {
  const el = document.documentElement;
  el.classList.add('theme-transitioning');
  applyTheme(true);
  requestAnimationFrame(() => {
    setTimeout(() => el.classList.remove('theme-transitioning'), 300);
  });
}

// System, light, dark, then back to following the system (finding F56: there was no way back)
function cycleTheme() {
  const next = nextInCycle(_PREFS(), _storedPref());
  if (next) localStorage.setItem(LS.THEME, next);
  else localStorage.removeItem(LS.THEME);
  _transitionTheme();
}

function initTheme() {
  setColorResolver(css => {
    const rgba = _canvasColor(css);
    if (!rgba) logWarn(logTag('Theme', 'Err', 'Color', css));
    return rgba;
  });
  applyTheme();

  _systemMql.addEventListener('change', () => {
    if (!_storedPref()) _transitionTheme();
  });

  const btn = document.getElementById('theme-btn');
  if (btn) btn.addEventListener('click', cycleTheme);
}

export { chartColors, initTheme };
