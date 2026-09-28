// Shared mutable state singleton + constants. The central store every module
// reads from. `state` is a const object (live bindings propagate mutations);
// primitive `let` exports use setter functions (e.g. setChartReady).
import { _EPHEMERAL } from './utils.js';

// The page bootstrap (backend/routes.py page_bootstrap, window.__MW_BOOT__): static prefix, app
// name, log level, connection policy, storage keys, model key separator and browser settings.
// app.js stops at start when it is missing (finding F32), so nothing below runs without it.
export const BOOT = globalThis.__MW_BOOT__ ?? null;

// Every localStorage key, from backend/state.py STORAGE_KEYS (finding F19)
export const LS = BOOT?.storage_keys;

// Model keys join provider and model id with backend/state.py MODEL_KEY_SEP (finding F45)
export function makeModelKey(provider, model) { return `${provider}${BOOT.model_key_sep}${model}`; }

export function parseModelKey(mk) {
  const sep = BOOT.model_key_sep;
  const idx = mk.indexOf(sep);
  if (idx < 0) return { provider: '', model: mk };
  return { provider: mk.slice(0, idx), model: mk.slice(idx + sep.length) };
}

// A provider-level key (provider added or removed) names no model
export function isProviderKey(mk) { return parseModelKey(mk).model === ''; }

export const state = {
  models: [],
  metrics: {},
  ws: null,
  charts: {},
  providerOrder: [],
  collapsedProviders: [],
  timeRanges: [],
  clientRTT: null,
  // Connection policy from the page bootstrap, refreshed by every WS hello (backend/websocket.py)
  conn: BOOT?.conn ?? null,
  // Browser settings (app.yaml ui.*) from the page bootstrap, refreshed by /api/config
  ui: BOOT?.ui ?? null,
  // Config-derived values stay null until /api/config arrives: views render an unknown state
  // instead of guessing (a guessed interval colored freshness wrongly, finding F6)
  healthInterval: null,
  healthEnabled: null,
  auditEnabled: null,
  auditInterval: null,
  auditSuites: {},
  probeEnabled: null,
  probeInterval: null,
  benchmarkInterval: null,
  // Labels and rules from /api/config (backend/state.py); empty until it arrives, never guessed (F22)
  colorThresholds: {},
  eventLabels: {},
  metricLabels: {},
  metricShortLabels: {},
  testTypeLabels: {},
  statusLabels: {},
  chartViewLabels: {},
  statusValues: [],
  chartViews: [],
  capabilities: [],
  scheduler: null,
  degradedCriticalMetrics: null,
  stalls: null,
  scoreWeights: null,
  _lastWsMsg: 0,
  _modelMap: {},
  _deployVersion: null,
  _notifyLocal: false,
  _notifHistory: [],
  _notifUnread: 0,
  _notifSettings: {
    enabled: false,
    popups: true,
    offline: true,
    recovered_offline: true,
    recovered_degraded: true,
    degraded: true,
    degraded_tps: true,
    degraded_tps_tier: null,
    recovered_tps: true,
    degraded_ttft: true,
    degraded_ttft_tier: null,
    recovered_ttft: true,
    provider_changed: true,
    model_changed: true,
    providers: [],
  },
  _notifServerConfig: null,
  _notifPushInited: false,
  _notifEnabledAt: null,
  _pushExpired: false,
  _wsRestarting: false,
  _wsConnected: false,
  _wsStatus: 'connecting',
  _apiFailStreak: 0,
  _backendDown: false,
  _suppressDeployReload: false,
  providerSummaries: {},
  _chartColorsDirty: true,
  _counts: { online: 0, degraded: 0, error: 0, unknown: 0, testing: 0 },
  fetchedProviders: new Set(),
  _providerDataAt: {},
  _modelCaps: null,
};

export const _NOTIF_OPTS = [
  { key: 'enabled', label: 'Enable notifications', desc: 'Receive alerts when models go offline, recover, or degrade.', help: 'Enable notifications to receive alerts when models go offline, recover, or degrade.', master: true, onFirstEnable: ['offline', 'recovered_offline', 'recovered_degraded', 'degraded', 'degraded_tps', 'degraded_ttft', 'recovered_tps', 'recovered_ttft', 'provider_changed', 'model_changed'] },
  { key: 'popups', label: 'Toast popups', desc: 'Show popup toasts in-page', help: 'When off, notifications still appear in the bell panel but no popup toasts are shown.' },
  {
    key: 'offline', label: 'Offline', desc: 'Model goes offline or comes back online',
    help: 'Get notified when a model becomes unreachable or recovers from an outage.',
    alert: true,
    down: { key: 'offline', label: '↓ Went offline' },
    up: { key: 'recovered_offline', label: '↑ Came back' },
  },
  {
    key: 'degraded', label: 'Degraded', desc: 'Performance degrades or recovers',
    help: 'Get notified when a model is degraded or recovers from degraded state.',
    alert: true,
    down: { key: 'degraded', label: '↓ Became degraded' },
    up: { key: 'recovered_degraded', label: '↑ Recovered' },
    childrenKeys: ['degraded_tps', 'recovered_tps', 'degraded_ttft', 'recovered_ttft'],
    children: [
      { key: 'degraded_tps', label: 'TPS', desc: 'TPS changes tier', metric: 'tps', tier_picker: 'degraded_tps_tier',
        alert: true,
        down: { key: 'degraded_tps', label: '↓ Worsened' },
        up: { key: 'recovered_tps', label: '↑ Improved' },
      },
      { key: 'degraded_ttft', label: 'TTFT', desc: 'TTFT changes tier', metric: 'ttft', tier_picker: 'degraded_ttft_tier',
        alert: true,
        down: { key: 'degraded_ttft', label: '↓ Worsened' },
        up: { key: 'recovered_ttft', label: '↑ Improved' },
      },
    ],
  },
  { key: 'provider_changed', label: 'Provider changes', desc: 'When a provider is added or removed', help: 'Get notified when a provider is added to or removed from the config.' },
  { key: 'model_changed', label: 'Model changes', desc: 'When a model is added or removed', help: 'Get notified when a model is added to or removed from an existing provider.' },
];

export const _etags = {};

export function setMetrics(metrics) {
  if (!metrics) return;
  const ps = metrics.providers;
  if (ps) {
    delete metrics.providers;
    Object.assign(state.providerSummaries, ps);
  }
  if (state.metrics && state.metrics !== metrics) {
    for (const k of Object.keys(metrics)) {
      const prev = state.metrics[k];
      const incoming = metrics[k];
      if (prev && incoming) {
        for (const e of _EPHEMERAL) {
          if (prev[e] !== undefined && incoming[e] === undefined) incoming[e] = prev[e];
        }
        if (prev.card_buckets !== undefined && incoming.card_buckets === undefined) incoming.card_buckets = prev.card_buckets;
        // archived is server-owned: the API omits the key when a model is not archived
        if (incoming.archived === undefined) delete prev.archived;
      }
    }
    for (const k of Object.keys(metrics)) {
      if (state.metrics[k]) Object.assign(state.metrics[k], metrics[k]);
      else state.metrics[k] = metrics[k];
    }
  } else {
    state.metrics = metrics;
  }
  recalcCounts();
}

export function countByStatus(entries, metrics) {
  let online = 0, degraded = 0, error = 0, unknown = 0, testing = 0;
  for (const e of entries) {
    if (e.archived) continue;
    const m = metrics[e.id];
    if (m?.testing && m.testing_type !== 'health') testing++;
    const s = m?.status;
    const lt = m?.last_test;
    if (s === 'online' && lt?.degraded) { degraded++; }
    else if (s === 'online') online++;
    else if (s === 'degraded') degraded++;
    else if (s === 'error') error++;
    else unknown++;
  }
  return { online, degraded, error, unknown, testing };
}

export function recalcCounts() {
  state._counts = countByStatus(state.models, state.metrics);
  for (const provider of Object.keys(state.providerSummaries)) {
    const entries = state.models.filter(e => e.provider === provider && !e.archived);
    const counted = countByStatus(entries, state.metrics);
    const hasData = entries.some(e => state.metrics[e.id]?.status);
    if (hasData) {
      const ps = state.providerSummaries[provider];
      if (ps) { ps.counts = counted; ps.total = entries.length; }
    }
  }
}

// Move one model between status counts (page-wide and its provider's) and in or out of testing
function _moveCount(counts, prevStatus, nextStatus, testingDelta) {
  if (prevStatus !== nextStatus) {
    if (prevStatus in counts) counts[prevStatus] = Math.max(0, counts[prevStatus] - 1);
    if (nextStatus in counts) counts[nextStatus]++;
  }
  if (testingDelta) counts.testing = Math.max(0, (counts.testing || 0) + testingDelta);
}

export function adjustCount(modelId, prevStatus, nextStatus, prevTesting, nextTesting) {
  const testingDelta = !!nextTesting - !!prevTesting;
  _moveCount(state._counts, prevStatus, nextStatus, testingDelta);
  const pc = state.providerSummaries[parseModelKey(modelId).provider]?.counts;
  if (pc) _moveCount(pc, prevStatus, nextStatus, testingDelta);
}
export let _chartReady = null;

export function setChartReady(promise) { _chartReady = promise; }

export function applyConfig(cfg) {
  if (!cfg) return;
  if (cfg.color_thresholds) state.colorThresholds = cfg.color_thresholds;
  if (cfg.event_labels) state.eventLabels = cfg.event_labels;
  if (cfg.metric_labels) state.metricLabels = cfg.metric_labels;
  if (cfg.metric_short_labels) state.metricShortLabels = cfg.metric_short_labels;
  if (cfg.test_type_labels) state.testTypeLabels = cfg.test_type_labels;
  if (cfg.status_values) state.statusValues = cfg.status_values;
  if (cfg.status_labels) state.statusLabels = cfg.status_labels;
  if (cfg.chart_views) state.chartViews = cfg.chart_views;
  if (cfg.chart_view_labels) state.chartViewLabels = cfg.chart_view_labels;
  if (cfg.capabilities) state.capabilities = cfg.capabilities;
  if (cfg.scheduler) state.scheduler = cfg.scheduler;
  if (cfg.degraded_critical_metrics != null) state.degradedCriticalMetrics = cfg.degraded_critical_metrics;
  if (cfg.stalls) state.stalls = cfg.stalls;
  if (cfg.scores) state.scoreWeights = cfg.scores;
  if (cfg.ui) state.ui = cfg.ui;
  if (cfg.benchmark_interval_seconds != null) state.benchmarkInterval = cfg.benchmark_interval_seconds;
  if (cfg.health_interval_seconds != null) state.healthInterval = cfg.health_interval_seconds;
  if (cfg.health_enabled != null) state.healthEnabled = cfg.health_enabled;
  if (cfg.audit_enabled != null) state.auditEnabled = cfg.audit_enabled;
  if (cfg.audit_interval_seconds != null) state.auditInterval = cfg.audit_interval_seconds;
  if (cfg.probe_enabled != null) state.probeEnabled = cfg.probe_enabled;
  if (cfg.probe_interval_seconds != null) state.probeInterval = cfg.probe_interval_seconds;
  if (cfg.time_ranges) state.timeRanges = cfg.time_ranges;
  if (cfg.audit_suites) state.auditSuites = cfg.audit_suites;
}
