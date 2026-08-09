// Shared mutable state singleton + constants. The central store every module
// reads from. `state` is a const object (live bindings propagate mutations);
// primitive `let` exports use setter functions (e.g. setChartReady).
import { _EPHEMERAL } from './utils.js';

export const LS = {
  CLIENT_ID: 'mw_client_id',
  COLLAPSED: 'mw_collapsed',
  THEME: 'mw_theme',
  NOTIF_SETTINGS: 'mw_notif_settings',
  NOTIF_HISTORY: 'mw_notif_history',
  NOTIF_READ_IDS: 'mw_notif_read_ids',
  NOTIF_ENABLED_AT: 'mw_notif_enabled_at',
  NOTIF_LOCAL: 'mw_notif_local',
  NOTIF_ENABLED_ONCE: 'mw_notif_enabled_once',
  SW_CLEANUP: 'mw_sw_cleanup_done',
  PUSH_OPT_OUT: 'mw_push_opt_out',
  CHART_RANGE: 'mw_chart_range',
  CARD_VIEW: 'mw_card_view',
  ACC_COLLAPSED: 'mw_acc_collapsed',
};

export const state = {
  models: [],
  metrics: {},
  ws: null,
  charts: {},
  providerOrder: [],
  collapsedProviders: [],
  timeRanges: [],
  clientRTT: null,
  healthInterval: 60,
  healthEnabled: true,
  auditEnabled: false,
  auditInterval: 21600,
  auditSuites: {},
  probeEnabled: false,
  probeInterval: 86400,
  benchmarkInterval: 3600,
  colorThresholds: {},
  eventLabels: {},
  metricLabels: {},
  statusValues: ['online', 'degraded', 'error', 'unknown'],
  testTypes: ['benchmark', 'health', 'audit', 'probe'],
  chartViews: ['speed', 'consistency', 'scores', 'health'],
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
  _apiFailStreak: 0,
  _backendDown: false,
  _suppressDeployReload: false,
  providerSummaries: {},
  _chartColorsDirty: true,
  _counts: { online: 0, degraded: 0, error: 0, testing: 0 },
  fetchedProviders: new Set(),
  _providerDataAt: {},
  _modelCaps: null,
};

export const CC = { tps: '#06b6d4', ttft: '#8b5cf6', uptime: '#f97316', tails: '#ec4899', batching: '#14b8a6', scoreC: '#14b8a6', scoreS: '#3b82f6', scoreR: '#22c55e' };

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
        // archived/archived_by are server-owned: the API omits them when a model is not archived
        if (incoming.archived === undefined) delete prev.archived;
        if (incoming.archived_by === undefined) delete prev.archived_by;
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

function countByStatus(entries, metrics) {
  let online = 0, degraded = 0, error = 0, testing = 0;
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
  }
  return { online, degraded, error, testing };
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

export function adjustCount(modelId, prevStatus, nextStatus, prevTesting, nextTesting) {
  const c = state._counts;
  if (prevStatus !== nextStatus) {
    if (prevStatus === 'online') c.online = Math.max(0, c.online - 1);
    else if (prevStatus === 'degraded') c.degraded = Math.max(0, c.degraded - 1);
    else if (prevStatus === 'error') c.error = Math.max(0, c.error - 1);
    if (nextStatus === 'online') c.online++;
    else if (nextStatus === 'degraded') c.degraded++;
    else if (nextStatus === 'error') c.error++;
    const provider = modelId.split('::')[0];
    const pc = state.providerSummaries[provider]?.counts;
    if (pc) {
      if (prevStatus === 'online') pc.online = Math.max(0, pc.online - 1);
      else if (prevStatus === 'degraded') pc.degraded = Math.max(0, pc.degraded - 1);
      else if (prevStatus === 'error') pc.error = Math.max(0, pc.error - 1);
      if (nextStatus === 'online') pc.online++;
      else if (nextStatus === 'degraded') pc.degraded++;
      else if (nextStatus === 'error') pc.error++;
    }
  }
  const wasTesting = !!prevTesting;
  const isTesting = !!nextTesting;
  if (wasTesting !== isTesting) {
    c.testing = Math.max(0, c.testing + (isTesting ? 1 : -1));
    const provider = modelId.split('::')[0];
    const pc = state.providerSummaries[provider]?.counts;
    if (pc) {
      pc.testing = Math.max(0, (pc.testing || 0) + (isTesting ? 1 : -1));
    }
  }
}
export let _chartReady = null;

export function setChartReady(promise) { _chartReady = promise; }

export function applyConfig(cfg) {
  if (!cfg) return;
  if (cfg.color_thresholds) state.colorThresholds = cfg.color_thresholds;
  if (cfg.event_labels) state.eventLabels = cfg.event_labels;
  if (cfg.metric_labels) state.metricLabels = cfg.metric_labels;
  if (cfg.status_values) state.statusValues = cfg.status_values;
  if (cfg.test_types) state.testTypes = cfg.test_types;
  if (cfg.chart_views) state.chartViews = cfg.chart_views;
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
