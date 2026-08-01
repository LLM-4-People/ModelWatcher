// Light/dark theme toggle. Chart colors are read from CSS custom properties
// so they stay in sync with the active theme without hardcoding hex values.
import { state, CC } from './state.js';

const STORAGE_KEY = 'mw_theme';
const _systemMql = window.matchMedia('(prefers-color-scheme: dark)');
const _CHART_BATCH = 5;

function isDark() {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === 'dark') return true;
  if (stored === 'light') return false;
  return _systemMql.matches;
}

let _ccCache = null;

function _readAllStyles() {
  const s = getComputedStyle(document.documentElement);
  const v = (n) => s.getPropertyValue(n).trim();
  CC.tps = v('--chart-cc-tps');
  CC.ttft = v('--chart-cc-ttft');
  CC.uptime = v('--chart-cc-uptime');
  CC.tails = v('--chart-cc-tails');
  CC.batching = v('--chart-cc-batching');
  CC.scoreC = v('--chart-cc-scoreC');
  CC.scoreS = v('--chart-cc-scoreS');
  CC.scoreR = v('--chart-cc-scoreR');
  const baseColor = v('--color-base');
  _ccCache = {
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
    bandTps: v('--chart-band-tps'),
    bandTtft: v('--chart-band-ttft'),
    bandTails: v('--chart-band-tails'),
    bandBatching: v('--chart-band-batching'),
    bandTpsExp: v('--chart-band-tps-exp'),
    bandTtftExp: v('--chart-band-ttft-exp'),
    bandTailsExp: v('--chart-band-tails-exp'),
    bandBatchingExp: v('--chart-band-batching-exp'),
    failure: v('--color-notif-offline'),
    degraded: v('--color-notif-degraded'),
    baseColor,
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

function applyTheme(batchCharts) {
  const dark = isDark();
  document.documentElement.classList.toggle('dark', dark);
  state._chartColorsDirty = true;
  const btn = document.getElementById('theme-btn');
  if (btn) btn.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
  requestAnimationFrame(() => {
    const cc = _readAllStyles();
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = cc.baseColor || (dark ? '#0c1220' : '#f8fafc');
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

function toggleTheme() {
  localStorage.setItem(STORAGE_KEY, isDark() ? 'light' : 'dark');
  _transitionTheme();
}

function initTheme() {
  applyTheme();

  _systemMql.addEventListener('change', () => {
    if (!localStorage.getItem(STORAGE_KEY)) _transitionTheme();
  });

  const btn = document.getElementById('theme-btn');
  if (btn) btn.addEventListener('click', toggleTheme);
}

export { chartColors, initTheme };
