// Pure helpers + shared UI primitives (leaf node - zero imports from other modules).
// Exports HELP dict, logging, HTML escaping, status glyphs, segment/separator markup,
// collapsible system, overlay layers and focus trapping, storage sweep, colour parsing,
// touch detection. Touches no DOM at import, so node --test can load it.

// One glyph per outcome, for every status mark the UI draws (check line, badges, history, notifications)
export const STATUS_GLYPH = { ok: '\u2713', degraded: '\u26a0', failed: '\u2717', unknown: '\u25cb' };
// The coloured bullet before a tier boundary or a critical metric; a colour key, not a status mark
export const TIER_DOT = '\u25cf';

// Separator glyphs. Rendered lists use sepHTML() inside segmentsHTML(); plain text
// (tooltips, aria-label, title, copied text) joins with SEP_TEXT.
export const SEP = { dot: '\u00b7', slash: '/' };
export const SEP_TEXT = ` ${SEP.dot} `;

export function sepHTML(kind) {
  if (kind === 'rule') return '<span class="seg-rule" aria-hidden="true"></span>';
  return `<span class="seg-sep" aria-hidden="true">${SEP[kind]}</span>`;
}

// A row of segments whose spacing comes only from the .seg-list flex gap. A nested group
// must itself be a segmentsHTML() list, never a plain wrapper: a plain wrapper is one flex
// item whose children lay out inline with no gap at all (finding F6, "·OK10h 8m").
// The spaces between items are not rendered by flex layout. They keep the text content (and the
// accessible names built from it) separated; a selection copy of a flex row puts each item on its
// own line instead, as flex items are blocks (finding F31).
export function segmentsHTML(parts, { sep = null, cls = '', attrs = '' } = {}) {
  const items = parts.filter(Boolean);
  if (!items.length) return '';
  const joiner = sep ? ` ${sepHTML(sep)} ` : ' ';
  return `<span class="seg-list${cls ? ' ' + cls : ''}"${attrs ? ' ' + attrs : ''}>${items.join(joiner)}</span>`;
}

export const HELP = {
  models: 'Total models being monitored.<br>Updated when config changes.',
  online: 'Models responding successfully.<br>Refreshed after each benchmark run.',
  testing: 'Models currently running a benchmark.<br>Tests run sequentially within each provider.',
  error: 'Model is offline: the most recent test failed to get a response, or its last benchmark failed.<br>Hover for the error message.',
  unknown: 'Model has no test result yet.',
  degraded_stream_error: 'The stream was interrupted by an error after tokens were received.<br>Metrics are computed from the partial output.',
  degraded_insufficient_output: 'The model produced output but below the minimum threshold for reliable metrics.<br>The stream completed but with too few tokens or chunks.',
  ws_connected: 'Connected - receiving live updates.<br>Results appear instantly when tests complete.',
  ws_disconnected: 'Disconnected - will retry automatically.<br>Updates may be delayed until reconnected.',
  ws_connecting: 'Connecting to server...<br>Live updates will begin once connected.',
  ws_restarting: 'Server is restarting - reconnecting...<br>Live updates will resume shortly.',
  ws_busy: 'Server is at its connection limit - retrying.<br>Data still refreshes periodically meanwhile.',
  ws_rejected: 'Live updates refused: the server does not accept connections from this address (origin).<br>Data still refreshes periodically. Ask the operator to add it to websocket.allowed_origins.',
  ws_down: 'Server unreachable - will retry automatically when connection is restored.',
  ws_unconfigured: 'This page came without its settings from the server (the server may be updating).<br>Reload the page.',
  ttft: 'Delay before the model starts generating.<br>For thinking models, this is time to first reasoning token.',
  tps: 'Wall-clock tokens per second (includes stalls and thinking tokens).',
  itlReliable: `Raw ITL metrics are trustworthy measurements.<br>${STATUS_GLYPH.ok} = shrinkage OK, low burst, enough samples.`,
  uptime: 'Successful test percentage over recent runs.',
  chunkCv: 'Coefficient of variation of per-chunk token counts.<br>Low CV = uniform chunks (reliable ITL). High CV = uneven chunks (ITL less meaningful).',
  testType: 'Test type: health check (reachability + TTFT only) or benchmark (full streaming).',
  p99Itl: 'Worst gap you regularly experience (99th percentile).<br>Computed from raw (unnormalized) inter-chunk latencies.',
  medianItl: 'Typical gap between tokens (raw, unnormalized).',
  maxItl: 'Single longest gap between tokens (raw, unnormalized).',
  itlTailRatio: 'How much worse slow tokens are vs typical ones (P99/P50 of effective, token-normalized ITLs).<br>High ratio = inconsistent output delivery.',
  effectiveItl: 'Token-normalized ITL - each inter-chunk gap divided by the token count of the second chunk.<br>Removes batching artifacts, reflecting per-token generation latency.',
  chunksObserved: 'Number of SSE chunks received from the provider.<br>Each chunk may contain one or more tokens.',
  maxChunk: 'Largest token count in a single SSE chunk (via tiktoken).',
  finishReason: 'Why the model stopped generating.<br>"length" = hit token limit. "stop" = model chose to stop.',
  avgItl: 'Mean inter-chunk latency.',
  tpot: 'Time per output token - generation time divided by (tokens − 1).<br>Excludes first token, more reliable for cross-provider comparison than TPS.',
  totalLatency: 'Wall-clock time from request start to last token received.',
  thinkingDuration: 'Time spent in the thinking/reasoning phase before the visible answer.',
  stallFirst: 'Position of the first stall as a percentage through the output (0% = start, 100% = end).',
  stallLast: 'Position of the last stall as a percentage through the output.',
  stallClusters: 'Number of distinct groups of stalls (stalls close together count as one cluster).',
  stallRatio: 'Fraction of total generation time spent in stalls.',
  ok: `Whether the test request succeeded or failed.<br><span class="text-status-online">${STATUS_GLYPH.ok}</span> success<br><span class="text-status-degraded">${STATUS_GLYPH.degraded}</span> degraded / retry attempt<br><span class="text-status-error">${STATUS_GLYPH.failed}</span> failure`,
  checkLine: `Time since each check last ran, colored by freshness.<br>${STATUS_GLYPH.ok} passed, ${STATUS_GLYPH.degraded} degraded, ${STATUS_GLYPH.failed} failed, ${STATUS_GLYPH.unknown} no result yet.<br>After a failure, "OK" shows the time since the last success.`,
  batching: 'Tokens per SSE delivery from the provider.<br>1× = token-by-token (ideal). Higher = batched delivery.',
  reasoning: 'Thinking tokens spent on chain-of-thought reasoning. Included in total output count. Generated before the visible answer.',
  completionTokens: 'Total output tokens (includes thinking tokens for reasoning models). Provider-reported, or counted via tiktoken.',
  networkJitter: 'Network jitter - variability of round-trip times to this provider.<br>High jitter inflates ITL, stall count, and tail ratio.',
  burstArrivals: 'Chunks that arrived in sub-millisecond gaps (proxy buffering).<br>High burst rate means ITL gaps reflect proxy flush timing, not server generation.',
  burstArrival: 'Percentage of chunks arriving in sub-millisecond gaps.<br>High burst indicates proxy/CDN buffering is coalescing tokens.',
  frameBatch: 'Percentage of tokens in TCP frames containing ≥2 SSE events.<br>Distinguishes server-side batching from network-level coalescing.',
  shrinkage: 'How much ITL extremes were pulled toward the median (0-1).<br>1.0 = no adjustment. 0.0 = fully smoothed (very high jitter).',
  errorMsg: 'Error message for failed test requests.<br>Click to expand full stack trace.',
  retry: 'A retry attempt. Each retry appears as its own history entry with the error that triggered it. Only the final attempt determines the model\'s status.',
  statusLegend: 'Current health of monitored models, with the number of models in each state.<br>Counts update live as tests complete.',
  performanceLegend: 'Metric tier color scale. Higher tiers (top) = better performance.',
  freshnessLegend: 'How recently the model was tested. Based on time since last check.',
  chart_speed: 'Speed view: TPS (tokens/second) and TTFT (time to first token) of benchmarks over time.',
  chart_consistency: 'Consistency view: P99 ITL (raw, worst regular gap) and batching ratio over time.',
  chart_scores: 'Score view: Consistency, Speed, and Reliability composite scores (0-100) over time.',
  chart_health: 'Health view: TTFT from lightweight reachability checks over time, with failed and degraded tests marked.',
  jumpToBtn: 'Jump to this date in history.',
  collapseDay: 'Collapse or expand the rows of this day.',
  customDateRange: 'Select a custom date range.',
  toggleColumns: 'Show or hide extra columns (P99 ITL (raw), Batch, Tail (eff.), Jitter).',
  modelInfo: 'Model metadata from the provider API. Click the card for full details.',
  themeToggle: 'Theme: follow the system, light or dark.<br>Click to switch to the next one; the choice is kept in this browser.',
  notifyToggle: 'Notification settings and recent alerts.<br>Open the panel to view history or configure event filters and push delivery.',
  notifSettings: 'Open settings panel.<br>Configure notification event filters, push delivery, and per-provider alerts.',
  helpToggle: 'Help, glossary, and reference panel.<br>Metric explanations and status legends.',
  archived: 'This model or provider is archived. Archived models are not tested but historical data is preserved.',
  schedule: 'How often each check runs. Ages in the model details are colored against these intervals.',
  schedulePaused: 'The server runs no tests right now (started with MW_DISABLE_TESTS, or its scheduler stopped).<br>The data shown is the last recorded.',
};

// Logging needs no bootstrap: it has to report a page that came without one (finding F32).
// Levels are backend/state.py LOG_LEVELS indexes: 0=debug, 1=info, 2=warn, 3=error.
const _LOG_LEVEL_WITHOUT_BOOT = 2;
const _P = () => { const name = globalThis.__MW_BOOT__?.app_name; return name ? `${name}: ` : ''; };
const _LL = () => globalThis.__MW_BOOT__?.log_level ?? _LOG_LEVEL_WITHOUT_BOOT;
const _fmt = (s, a) => { let i = 0; return s.replace(/%[sdfo]/g, () => a[i++] ?? ''); };

export function logDebug(ctx, ...args) { if (_LL() <= 0) console.debug(_P() + _fmt(ctx, args)); }
export function logInfo(ctx, ...args)  { if (_LL() <= 1) console.info(_P() + _fmt(ctx, args)); }
export function logWarn(ctx, ...args)  { if (_LL() <= 2) console.warn(_P() + _fmt(ctx, args)); }
export function logError(ctx, err) {
  console.error(_P() + ctx, err instanceof Error ? err : err ?? '');
}

const _errTS = { last: 0, count: 0 };
export function reportClientError(payload) {
  const now = Date.now();
  if (now - _errTS.last < 1000) { if (++_errTS.count > 5) return; } else { _errTS.last = now; _errTS.count = 1; }
  // logWarn only writes to the console, so failing to report cannot feed back into this reporter
  try {
    fetch('/api/client-error', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      keepalive: true,
      credentials: 'same-origin',
    }).catch(e => logWarn(logTag('App', '→', 'ClientErrorReport', 'Failed'), e));
  } catch (e) { logWarn(logTag('App', '→', 'ClientErrorReport', 'Failed'), e); }
}

export function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ''; }

// The item after `current` in `list`, wrapping around (the theme button cycles its preferences)
export function nextInCycle(list, current) { return list[(list.indexOf(current) + 1) % list.length]; }

// Shared colored dot HTML - used by help legends, filter status options, etc.
const _DOT_SIZE = { 2: 'w-2 h-2', 2.5: 'w-2.5 h-2.5', 3: 'w-3 h-3' };
export function dotHTML(cls, size = 2) {
  return `<span class="${_DOT_SIZE[size] || _DOT_SIZE[2]} rounded-full ${cls}" aria-hidden="true"></span>`;
}

export function setHTML(el, html) {
  if (el && el.innerHTML !== html) el.innerHTML = html;
}

export function setText(el, text) {
  const t = text ?? '';
  if (el && el.textContent !== t) el.textContent = t;
}

export function setClass(el, cls) {
  if (el && el.className !== cls) el.className = cls;
}

// Log lines only; rendered text joins with SEP_TEXT
const _LOG_TAG_SEP = ' - ';

export function logTag(comp, dir, type, ...rest) {
  return `${comp} ${dir} ${type}${rest.filter(Boolean).map(s => _LOG_TAG_SEP + s).join('')}`;
}

export function slug(s) { return s.replace(/[^a-zA-Z0-9_-]/g, '_'); }

export function isOK(r) { return r.available != null ? r.available : r.success; }

const _escMap = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const _escRe = /[&<>"']/g;
export function esc(s) {
  if (s == null) return '';
  return String(s).replace(_escRe, c => _escMap[c]);
}

export function isTouchDevice() { return 'ontouchstart' in window || navigator.maxTouchPoints > 0; }

export function initSheetDrag({ handleSelector, panelId, closeFn, threshold = 60, snapMs = 200 }) {
  const panel = document.getElementById(panelId);
  if (!panel) return;
  const handle = panel.querySelector(handleSelector);
  if (!handle) return;
  let startY = 0;
  handle.addEventListener('touchstart', e => { startY = e.touches[0].clientY; }, { passive: true });
  handle.addEventListener('touchmove', e => {
    const dy = e.touches[0].clientY - startY;
    if (dy > 0) { panel.style.transition = 'none'; panel.style.transform = `translateY(${dy}px)`; }
  }, { passive: true });
  handle.addEventListener('touchend', e => {
    const dy = e.changedTouches[0].clientY - startY;
    panel.style.transition = snapMs ? `transform ${snapMs}ms ease-out` : '';
    panel.style.transform = '';
    if (dy > threshold) closeFn();
  });
}

// The phone breakpoint, the same as Tailwind's `sm` and every (max-width: 639px) rule in index.html
export const BP_SM = 640;

export function isPhone(width = globalThis.innerWidth) { return width < BP_SM; }

// Overlay layers (the modal, the date picker, filter dropdowns, Help, notifications). The one
// Escape listener (app.js) closes only the top layer (finding F49); listeners learn about a new
// layer so a tooltip never stays above what just opened (F53).
const _layers = [];
const _layerOpenListeners = [];

// Register an open layer; returns a handle that removes it (safe to call more than once)
export function pushLayer(close) {
  const layer = { close };
  _layers.push(layer);
  for (const fn of _layerOpenListeners) fn();
  return () => { const i = _layers.indexOf(layer); if (i >= 0) _layers.splice(i, 1); };
}

// Close the top layer; false when none is open
export function closeTopLayer() {
  const layer = _layers.pop();
  if (!layer) return false;
  layer.close();
  return true;
}

export function openLayerCount() { return _layers.length; }

export function onLayerOpen(fn) { _layerOpenListeners.push(fn); }

// A modal layer takes focus and makes the rest of the page inert and unscrollable; the returned
// release restores all three, focus back to what had it (findings F72, F73)
let _scrollLocks = 0;

export function trapFocus(dialog, { focus = dialog, keep = [], returnTo = document.activeElement } = {}) {
  const trigger = returnTo;
  const others = [...document.body.children].filter(el => el !== dialog && !el.contains(dialog) && !keep.includes(el) && !el.inert);
  for (const el of others) el.inert = true;
  if (_scrollLocks++ === 0) document.documentElement.classList.add('scroll-locked');
  focus.focus({ preventScroll: true });
  let released = false;
  return () => {
    if (released) return;
    released = true;
    for (const el of others) el.inert = false;
    if (--_scrollLocks === 0) document.documentElement.classList.remove('scroll-locked');
    if (trigger?.isConnected && typeof trigger.focus === 'function') trigger.focus({ preventScroll: true });
  };
}

// Remove every key under the prefix that the registry no longer lists: renamed and retired
// settings leave nothing behind, with no list of old names to keep (finding F19)
export function pruneStorage(storage, keys, prefix) {
  const known = new Set(Object.values(keys));
  const stale = [];
  for (let i = 0; i < storage.length; i++) {
    const k = storage.key(i);
    if (k.startsWith(prefix) && !known.has(k)) stale.push(k);
  }
  for (const k of stale) storage.removeItem(k);
  return stale;
}

// CSS colour to [r, g, b, a] (0-255 channels, 0-1 alpha). Hex and rgb()/rgba() are parsed
// here; any other form (oklch(), hsl(), color-mix(), names) goes to the resolver the page
// installs (a canvas round trip in theme.js). A colour nothing can read is logged once and
// gives null, never a silently grey or NaN colour (finding F83).
const _HEX_RE = /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
const _RGB_RE = /^rgba?\(\s*([\d.]+%?)[\s,]+([\d.]+%?)[\s,]+([\d.]+%?)(?:\s*[,/]\s*([\d.]+%?))?\s*\)$/i;
const _colorCache = new Map();
let _colorResolver = null;

export function setColorResolver(fn) { _colorResolver = fn; _colorCache.clear(); }

function _channel(v) { return v.endsWith('%') ? Math.round(parseFloat(v) * 2.55) : Math.round(parseFloat(v)); }
function _alpha(v) { return v == null ? 1 : v.endsWith('%') ? parseFloat(v) / 100 : parseFloat(v); }

function _parseColor(css) {
  const hex = _HEX_RE.exec(css);
  if (hex) {
    let h = hex[1];
    if (h.length <= 4) h = [...h].map(c => c + c).join('');
    const n = [0, 2, 4, 6].map(i => (i < h.length ? parseInt(h.slice(i, i + 2), 16) : 255));
    return [n[0], n[1], n[2], n[3] / 255];
  }
  const rgb = _RGB_RE.exec(css);
  if (rgb) return [_channel(rgb[1]), _channel(rgb[2]), _channel(rgb[3]), _alpha(rgb[4])];
  return _colorResolver ? _colorResolver(css) : null;
}

export function parseCssColor(color) {
  const css = String(color ?? '').trim();
  if (!_colorCache.has(css)) {
    const rgba = css ? _parseColor(css) : null;
    if (!rgba) logWarn(logTag('Color', 'Err', 'Unreadable', css || '(empty)'));
    _colorCache.set(css, rgba);
  }
  return _colorCache.get(css);
}

// The colour with its alpha scaled by `alpha`, as rgba(); null when it cannot be read
export function withAlpha(color, alpha) {
  const rgba = parseCssColor(color);
  if (!rgba) return null;
  const a = Math.min(1, Math.max(0, rgba[3] * alpha));
  return `rgba(${rgba[0]},${rgba[1]},${rgba[2]},${+a.toFixed(3)})`;
}

const _CHEVRON_PATH = 'M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z';

export function chevronSVG(cls = 'acc-chevron', size = 14) {
  return `<svg class="${cls}" style="width:${size}px;height:${size}px" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="${_CHEVRON_PATH}" clip-rule="evenodd"/></svg>`;
}

export function collapsibleHTML({ id, title, bodyHTML, open = false, tipKey, btnCls, wrapperCls }) {
  const tipAttr = tipKey ? ` data-tip="${tipKey}"` : '';
  const stateAttr = open ? 'open' : 'closed';
  const extraBtnCls = btnCls ? ` ${btnCls}` : '';
  const extraWrapCls = wrapperCls ? ` ${wrapperCls}` : '';
  return `<div class="acc-section${extraWrapCls}"${id ? ` data-section="${id}"` : ''}>
  <button type="button"${id ? ` id="acc-${id}"` : ''} class="acc-btn${extraBtnCls}" data-state="${stateAttr}" aria-expanded="${open}"${tipAttr}>
    ${chevronSVG()}<span>${esc(title)}</span>
  </button>
  <div class="acc-body" data-state="${stateAttr}"${id ? ` role="region" aria-labelledby="acc-${id}"` : ''}><div>${bodyHTML}</div></div>
</div>`;
}

export function toggleCollapsible(btn) {
  if (!btn) return;
  const isOpen = btn.dataset.state === 'open';
  const nextState = isOpen ? 'closed' : 'open';
  const expanded = !isOpen;
  btn.dataset.state = nextState;
  btn.setAttribute('aria-expanded', String(expanded));
  const body = btn.nextElementSibling;
  if (body) body.dataset.state = nextState;
  return expanded;
}

export function kvRow(label, valueHTML, { mono } = {}) {
  if (valueHTML == null || valueHTML === '') return '';
  const cls = mono ? ' kv-mono' : '';
  return `<span class="kv-label">${label}</span><span class="kv-value${cls}">${valueHTML}</span>`;
}

const _KV_SEP = '<hr class="kv-sep">';
export function kvSep() { return _KV_SEP; }

export function kvGrids(rows) {
  const groups = [[]];
  for (const r of rows) {
    if (r === _KV_SEP) { groups.push([]); continue; }
    groups[groups.length - 1].push(r);
  }
  return groups.filter(g => g.length)
    .map(g => `<div class="kv-grid">${g.join('')}</div>`)
    .join(_KV_SEP);
}

export const _EPHEMERAL = ['testing', 'testing_type', 'testing_audit', 'retry_attempt', 'retry_total', 'last_audit_result', 'last_audit_epoch', 'testing_probe', 'last_probe_epoch', 'last_probe_result', 'last_success_test', 'last_success_epoch'];

export function stripEphemeral(metrics) {
  const cleaned = {};
  for (const [k, v] of Object.entries(metrics)) {
    const rest = { ...(v || {}) };
    for (const e of _EPHEMERAL) delete rest[e];
    cleaned[k] = rest;
  }
  return cleaned;
}

