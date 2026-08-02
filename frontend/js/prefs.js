// Shared notification prefs builder + WS prefs sync. Imported by both
// notifications.js and ws.js to break the near-circular dependency.
import { state } from './state.js';
import { logError, logTag } from './utils.js';

// Maps client-side _notifSettings to the server prefs dict (14 fields).
export function buildNotifPrefs() {
  const s = state._notifSettings;
  return {
    enabled: s.enabled,
    offline: s.offline,
    recovered: !!(s.recovered_offline || s.recovered_degraded),
    recovered_offline: s.recovered_offline,
    recovered_degraded: s.recovered_degraded,
    degraded: s.degraded,
    degraded_tps: s.degraded_tps,
    degraded_tps_tier: s.degraded_tps_tier,
    recovered_tps: s.recovered_tps,
    degraded_ttft: s.degraded_ttft,
    degraded_ttft_tier: s.degraded_ttft_tier,
    recovered_ttft: s.recovered_ttft,
    provider_changed: s.provider_changed,
    model_changed: s.model_changed,
    providers: s.providers || [],
    enabled_at: state._notifEnabledAt || null,
  };
}

export function syncWSPrefs() {
  if (state.ws && state.ws.readyState === 1) {
    try { state.ws.send(JSON.stringify({ type: 'sync_prefs', prefs: buildNotifPrefs() })); } catch (e) { logError(logTag('Prefs', '→', 'Error', 'Sync'), e); }
  }
}
