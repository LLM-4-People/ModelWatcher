// Modal time ranges as pure functions of the configured ranges (DOM-free, so node --test covers it).
// A range persists as its key; only a custom range persists its bounds. The start of a keyed
// window is derived from the key each time the modal opens and never stored, so it cannot creep
// (finding F55: history pagination moved a stored start back 12 days while the key said 3d).
import { state, LS } from './state.js';
import { isPhone } from './utils.js';

export const CUSTOM = 'custom';
// "All time": always offered, needs no data span
export const ALL_TIME = 'max';

const _STORE = {
  chart: () => ({ range: LS.CHART_RANGE, since: LS.CHART_SINCE, until: LS.CHART_UNTIL }),
  history: () => ({ range: LS.HIST_RANGE, since: LS.HIST_SINCE, until: LS.HIST_UNTIL }),
};

const _num = v => (v == null || v === '' ? null : Number(v));

export function isRangeEligible(r, availableRanges) {
  return r.key === ALL_TIME || availableRanges.includes(r.key);
}

// The range a modal target opens with by default on this screen (app.yaml ui.default_ranges, F85)
export function defaultRangeKey(target, width) {
  return state.ui.default_ranges[target][isPhone(width) ? 'phone' : 'desktop'];
}

// [since, until] of a range at `now` (epoch seconds); a keyed range ends now
export function rangeWindow(key, ranges, now = Date.now() / 1000, custom = null) {
  if (key === CUSTOM) return [custom?.since ?? null, custom?.until ?? null];
  const range = ranges.find(r => r.key === key);
  if (!range || !range.seconds) return [null, null];
  return [now - range.seconds, null];
}

export function storeRange(storage, target, key, custom = null) {
  const k = _STORE[target]();
  storage.setItem(k.range, key);
  for (const [name, value] of [['since', custom?.since], ['until', custom?.until]]) {
    if (key === CUSTOM && value != null) storage.setItem(k[name], String(value));
    else storage.removeItem(k[name]);
  }
}

export function storedRange(storage, target) {
  const k = _STORE[target]();
  const key = storage.getItem(k.range);
  if (key !== CUSTOM) return { key, custom: null };
  return { key, custom: { since: _num(storage.getItem(k.since)), until: _num(storage.getItem(k.until)) } };
}

// The key a target opens with: the stored one while the model has data for it, else the default,
// else the eligible range nearest the stored one (shorter first), else the shortest, else all time
export function pickRangeKey(stored, target, ranges, availableRanges, width) {
  const eligible = ranges.filter(r => r.key !== ALL_TIME && isRangeEligible(r, availableRanges));
  if (stored.key === CUSTOM && stored.custom?.since != null) return CUSTOM;
  if (stored.key === ALL_TIME) return ALL_TIME;
  if (eligible.some(r => r.key === stored.key)) return stored.key;
  const def = defaultRangeKey(target, width);
  if (!stored.key && eligible.some(r => r.key === def)) return def;
  const storedSec = ranges.find(r => r.key === stored.key)?.seconds ?? 0;
  const bySize = [...eligible].sort((a, b) => a.seconds - b.seconds);
  const under = bySize.filter(r => r.seconds <= storedSec).pop();
  const over = bySize.find(r => r.seconds > storedSec);
  return (under || over)?.key ?? ALL_TIME;
}

// The start a history label shows: the window's, or the oldest loaded row's when scrolling has
// loaded rows older than the window. A display value only; the window itself does not move.
export function displaySince(windowSince, rows) {
  const oldest = Math.min(...rows.map(r => r.ts_epoch).filter(Boolean));
  if (!Number.isFinite(oldest)) return windowSince;
  return windowSince == null ? null : Math.min(windowSince, oldest);
}
