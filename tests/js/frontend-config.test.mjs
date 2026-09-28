// Values the frontend takes from the page bootstrap and /api/config instead of keeping copies,
// and the DOM-free rules built on them.
// Catches: F19 (storage keys), F45 (model key separator), F49 (Escape closes one layer), F55 (a
// stored history window that crept), F56 (theme cycle never returned to the system setting),
// F78 (HELP texts with config values typed in), F81 and F105 (score tiers), F83 (colours other
// than 6-digit hex), F85 (breakpoint and default ranges) and F92 (model id printed twice).
import { test } from 'node:test';
import assert from 'node:assert/strict';

// A bootstrap whose values differ from the shipped ones: a test passes only when the code reads it
const KEYS = {
  THEME: 't_theme',
  CHART_RANGE: 't_chart_range', CHART_SINCE: 't_chart_since', CHART_UNTIL: 't_chart_until',
  HIST_RANGE: 't_hist_range', HIST_SINCE: 't_hist_since', HIST_UNTIL: 't_hist_until',
};
globalThis.__MW_BOOT__ = {
  storage_keys: KEYS, storage_prefix: 't_', model_key_sep: '|', themes: ['light', 'dark'], log_level: 3,
  ui: { default_ranges: { chart: { desktop: '14d', phone: '12h' }, history: { desktop: '2d', phone: '1h' } } },
};
const { state, makeModelKey, parseModelKey, isProviderKey } = await import('../../frontend/js/state.js');
const utils = await import('../../frontend/js/utils.js');
const ranges = await import('../../frontend/js/ranges.js');
const format = await import('../../frontend/js/format.js');

class FakeStorage {
  constructor(entries = {}) { this.map = new Map(Object.entries(entries)); }
  get length() { return this.map.size; }
  key(i) { return [...this.map.keys()][i] ?? null; }
  getItem(k) { return this.map.has(k) ? this.map.get(k) : null; }
  setItem(k, v) { this.map.set(k, String(v)); }
  removeItem(k) { this.map.delete(k); }
}

const HOUR = 3600;
const RANGES = [
  { key: '1h', seconds: HOUR }, { key: '12h', seconds: 12 * HOUR }, { key: '2d', seconds: 48 * HOUR },
  { key: '14d', seconds: 336 * HOUR }, { key: 'max', seconds: null },
];

test('model keys use the separator from the bootstrap (F45)', () => {
  assert.equal(makeModelKey('Acme', 'm-1'), 'Acme|m-1');
  assert.deepEqual(parseModelKey('Acme|org/m|x'), { provider: 'Acme', model: 'org/m|x' });
  assert.deepEqual(parseModelKey('Acme::m'), { provider: '', model: 'Acme::m' });
  assert.equal(isProviderKey('Acme|'), true);
});

test('storage pruning removes retired keys under the prefix only (F19)', () => {
  const storage = new FakeStorage({ t_theme: 'dark', t_old_setting: '1', other_app: 'x' });
  assert.deepEqual(utils.pruneStorage(storage, KEYS, 't_'), ['t_old_setting']);
  assert.deepEqual([...storage.map.keys()].sort(), ['other_app', 't_theme']);
});

test('only the top layer closes (F49)', () => {
  const closed = [];
  let opened = 0;
  utils.onLayerOpen(() => { opened += 1; });
  const releaseModal = utils.pushLayer(() => closed.push('modal'));
  utils.pushLayer(() => closed.push('picker'));
  assert.equal(opened, 2, 'listeners hear about every new layer (a tip hides, F53)');
  assert.equal(utils.closeTopLayer(), true);
  assert.deepEqual(closed, ['picker']);
  releaseModal();
  releaseModal();
  assert.equal(utils.openLayerCount(), 0);
  assert.equal(utils.closeTopLayer(), false);
});

test('a keyed range stores its key only and its window never creeps (F55)', () => {
  const storage = new FakeStorage({ t_hist_since: '1', t_hist_until: '2' });
  ranges.storeRange(storage, 'history', '2d');
  assert.deepEqual([...storage.map.entries()], [['t_hist_range', '2d']]);
  const stored = ranges.storedRange(storage, 'history');
  assert.deepEqual(stored, { key: '2d', custom: null });
  const now = 1_800_000_000;
  const [since] = ranges.rangeWindow(stored.key, RANGES, now);
  assert.equal(since, now - 48 * HOUR);
  // Infinite scroll loaded rows 12 days back: the label shows it, the stored window does not move
  const rows = [{ ts_epoch: now - 300 * HOUR }, { ts_epoch: now - HOUR }];
  assert.equal(ranges.displaySince(since, rows), now - 300 * HOUR);
  assert.deepEqual(ranges.storedRange(storage, 'history'), stored);
  assert.deepEqual(ranges.rangeWindow(stored.key, RANGES, now + 60), [now + 60 - 48 * HOUR, null]);
});

test('a custom range keeps its bounds', () => {
  const storage = new FakeStorage();
  ranges.storeRange(storage, 'chart', ranges.CUSTOM, { since: 100, until: 200 });
  const stored = ranges.storedRange(storage, 'chart');
  assert.deepEqual(stored, { key: ranges.CUSTOM, custom: { since: 100, until: 200 } });
  assert.deepEqual(ranges.rangeWindow(stored.key, RANGES, 0, stored.custom), [100, 200]);
});

test('one breakpoint and configured default ranges (F85)', () => {
  assert.equal(utils.isPhone(639), true);
  assert.equal(utils.isPhone(640), false);
  state.ui = globalThis.__MW_BOOT__.ui;
  assert.equal(ranges.defaultRangeKey('chart', 639), '12h');
  assert.equal(ranges.defaultRangeKey('chart', 640), '14d');
  const available = ['1h', '12h', '2d', '14d'];
  assert.equal(ranges.pickRangeKey({ key: null }, 'history', RANGES, available, 390), '1h');
  assert.equal(ranges.pickRangeKey({ key: null }, 'history', RANGES, available, 1400), '2d');
  assert.equal(ranges.pickRangeKey({ key: '14d' }, 'history', RANGES, ['1h', '12h'], 1400), '12h');
});

test('the theme cycle returns to following the system (F56)', () => {
  const prefs = [null, ...globalThis.__MW_BOOT__.themes];
  assert.equal(utils.nextInCycle(prefs, null), 'light');
  assert.equal(utils.nextInCycle(prefs, 'light'), 'dark');
  assert.equal(utils.nextInCycle(prefs, 'dark'), null);
});

test('colours in any CSS form, never NaN (F83)', () => {
  assert.deepEqual(utils.parseCssColor('#abc'), [170, 187, 204, 1]);
  assert.deepEqual(utils.parseCssColor('rgb(1 2 3 / 50%)'), [1, 2, 3, 0.5]);
  assert.equal(utils.withAlpha('#11223380', 0.5), 'rgba(17,34,51,0.251)');
  assert.equal(utils.withAlpha('rgba(10, 20, 30, 0.5)', 0.5), 'rgba(10,20,30,0.25)');
  assert.equal(utils.withAlpha('oklch(70% 0.1 200)', 0.5), null, 'unreadable without a resolver');
  utils.setColorResolver(css => (css.startsWith('oklch(') ? [9, 8, 7, 1] : null));
  assert.equal(utils.withAlpha('oklch(70% 0.1 200)', 0.5), 'rgba(9,8,7,0.5)');
  utils.setColorResolver(null);
});

test('score tiers follow color_thresholds.scores, fractions included (F81, F105)', () => {
  state.colorThresholds = { scores: { higher_is_better: true, thresholds: [90, 75, 50, 25, 0] } };
  assert.equal(format.scoreTierIdx(90), 0);
  assert.equal(format.scoreTierIdx(89.5), 1);
  assert.equal(format.scoreTierIdx(80), 1);
  assert.equal(format.scoreTierIdx(24.9), 4);
  assert.equal(format.scoreTierIdx(null), -1);
  assert.deepEqual(format.tierRangeLabels('scores'), ['≥90%', '75-90%', '50-75%', '25-50%', '<25%']);
});

test('help texts take config values from /api/config (F78)', () => {
  state.stalls = null;
  state.degradedCriticalMetrics = null;
  assert.equal(format.helpText('stall'), '', 'nothing is guessed before config arrives');
  state.stalls = { visible_threshold_ms: 750, hiccup_multiplier: 4 };
  state.degradedCriticalMetrics = 3;
  assert.match(format.helpText('stall'), /\b750ms\b/);
  assert.match(format.helpText('hiccups'), /\b4×/);
  assert.match(format.helpText('degraded'), /\b3 or more metrics\b/);
  assert.match(format.helpText('degraded_critical_tier'), /^3 or more metrics/);
});

test('the model id shows only when it differs from the name (F92)', () => {
  assert.equal(format.secondaryModelId({ name: 'gpt-5.4', model_id: 'gpt-5.4' }), '');
  assert.equal(format.secondaryModelId({ name: 'GPT 5.4', model_id: 'gpt-5.4' }), 'gpt-5.4');
});
