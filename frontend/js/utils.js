// Pure helpers + shared UI primitives (leaf node - zero imports from other modules).
// Exports HELP dict, logging, HTML escaping, collapsible system, touch detection.
export const HELP = {
  models: 'Total models being monitored.<br>Updated when config changes.',
  online: 'Models responding successfully.<br>Refreshed after each benchmark run.',
  testing: 'Models currently running a benchmark.<br>Tests run sequentially within each provider.',
  errors: 'Models that failed their most recent test.<br>Click a card for error details.',
  offline: 'Model is offline - the most recent test failed to get a response.<br>Hover for the error message.',
  degraded: 'Model is degraded - performance below acceptable thresholds.<br>Causes:<br>\u2022 Critical tier \u2014 one or more metrics at worst level<br>\u2022 Stream error \u2014 stream interrupted after tokens<br>\u2022 Insufficient output \u2014 too few tokens for reliable metrics',
  degraded_critical_tier: 'One or more metrics reached the Critical (worst) tier, indicating severely degraded performance.<br>Look for the underlined values.',
  degraded_stream_error: 'The stream was interrupted by an error after tokens were received.<br>Metrics are computed from the partial output.',
  degraded_insufficient_output: 'The model produced output but below the minimum threshold for reliable metrics.<br>The stream completed but with too few tokens or chunks.',
  ws_connected: 'Connected - receiving live updates.<br>Results appear instantly when tests complete.',
  ws_disconnected: 'Disconnected - will retry automatically.<br>Updates may be delayed until reconnected.',
  ws_connecting: 'Connecting to server...<br>Live updates will begin once connected.',
  ws_error: 'Connection error - will retry automatically.<br>Check your network connection.',
  ws_restarting: 'Server is restarting - reconnecting...<br>Live updates will resume shortly.',
  ws_down: 'Server unreachable - will retry automatically when connection is restored.',
  ttft: 'Delay before the model starts generating.<br>For thinking models, this is time to first reasoning token.',
  tps: 'Wall-clock tokens per second (includes stalls and thinking tokens).',
  itlReliable: 'Raw ITL metrics are trustworthy measurements.<br>✓ = shrinkage OK, low burst, enough samples.',
  uptime: 'Successful test percentage over recent runs.',
  stall: 'Pause over 500ms between words.',
  chunkCv: 'Coefficient of variation of per-chunk token counts.<br>Low CV = uniform chunks (reliable ITL). High CV = uneven chunks (ITL less meaningful).',
  testType: 'Test type: health check (reachability + TTFT only) or benchmark (full streaming).',
  consistency: 'Output smoothness based on stalls, tail ratio, batching, and chunk CV.',
  p99Itl: 'Worst gap you regularly experience (99th percentile).<br>Computed from raw (unnormalized) inter-chunk latencies.',
  medianItl: 'Typical gap between tokens (raw, unnormalized).',
  maxItl: 'Single longest gap between tokens (raw, unnormalized).',
  itlTailRatio: 'How much worse slow tokens are vs typical ones (P99/P50 of effective, token-normalized ITLs).<br>High ratio = inconsistent output delivery.',
  effectiveItl: 'Token-normalized ITL - each inter-chunk gap divided by the token count of the second chunk.<br>Removes batching artifacts, reflecting per-token generation latency.',
  chunksObserved: 'Number of SSE chunks received from the provider.<br>Each chunk may contain one or more tokens.',
  maxChunk: 'Largest token count in a single SSE chunk (via tiktoken).',
  finishReason: 'Why the model stopped generating.<br>"length" = hit token limit. "stop" = model chose to stop.',
  hiccups: 'Inter-chunk gaps exceeding 3× the median ITL (adaptive threshold).<br>Less severe than stalls but indicate uneven delivery.',
  avgItl: 'Mean inter-chunk latency.',
  tpot: 'Time per output token - generation time divided by (tokens − 1).<br>Excludes first token, more reliable for cross-provider comparison than TPS.',
  totalLatency: 'Wall-clock time from request start to last token received.',
  thinkingDuration: 'Time spent in the thinking/reasoning phase before the visible answer.',
  stallFirst: 'Position of the first stall as a percentage through the output (0% = start, 100% = end).',
  stallLast: 'Position of the last stall as a percentage through the output.',
  stallClusters: 'Number of distinct groups of stalls (stalls close together count as one cluster).',
  stallRatio: 'Fraction of total generation time spent in stalls.',
  ok: 'Whether the test request succeeded or failed.<br><span class="text-status-online">✓</span> success<br><span class="text-status-degraded">⚠</span> degraded / retry attempt<br><span class="text-status-error">✗</span> failure',
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
  statusLegend: 'Current health of monitored models. Counts update live as tests complete.',
  performanceLegend: 'Metric tier color scale. Higher tiers (top) = better performance.',
  freshnessLegend: 'How recently the model was tested. Based on time since last check.',
  scores: 'Composite performance scores based on consistency, speed, and reliability trends.<br>C = Consistency (ITL tail, batching, stalls)<br>S = Speed (TPS trend)<br>R = Reliability (uptime trend)<br>↑↓ = direction since the shown timeframe.',
  chartSpeed: 'Speed view - TPS (tokens/second) and TTFT (time to first token) over time.',
  chartConsistency: 'Consistency view - P99 ITL (raw, worst regular gap) and batching ratio over time.',
  chartScores: 'Score view - Consistency, Speed, and Reliability composite scores (0-100) over time.',
  chartHealth: 'Health view - TTFT from lightweight reachability checks over time.',
  jumpToBtn: 'Jump to this date in history.',
  collapseDay: 'Click a day header to collapse or expand its rows.',
  customDateRange: 'Select a custom date range.',
  toggleColumns: 'Show or hide extra columns (P99 ITL (raw), Batch, Tail (eff.), Jitter).',
  modelInfo: 'Model metadata from the provider API. Click the card for full details.',
  capabilities: 'Model capabilities:<br>\u2022 Thinking \u2014 chain-of-thought reasoning<br>\u2022 Vision \u2014 image understanding<br>\u2022 Tools \u2014 function/tool calling<br>\u2022 Cache \u2014 prompt caching<br>\u2022 JSON \u2014 structured output',
  themeToggle: 'Switch between light and dark theme.<br>Sets preference for this browser.',
  notifyToggle: 'Notification settings and recent alerts.<br>Open the panel to view history or configure event filters and push delivery.',
  notifSettings: 'Open settings panel.<br>Configure notification event filters, push delivery, and per-provider alerts.',
  helpToggle: 'Help, glossary, and reference panel.<br>Metric explanations and status legends.',
  archived: 'This model or provider is archived. Archived models are not tested but historical data is preserved.',
};

const _P = () => (window.__APP_NAME__ || 'ModelWatcher') + ':';
const _LL = () => window.__LOG_LEVEL__ ?? 2;  // 0=debug, 1=info, 2=warn, 3=error
const _fmt = (s, a) => { let i = 0; return s.replace(/%[sdfo]/g, () => a[i++] ?? ''); };

export function logDebug(ctx, ...args) { if (_LL() <= 0) console.debug(_P() + ' ' + _fmt(ctx, args)); }
export function logInfo(ctx, ...args)  { if (_LL() <= 1) console.info(_P() + ' ' + _fmt(ctx, args)); }
export function logWarn(ctx, ...args)  { if (_LL() <= 2) console.warn(_P() + ' ' + _fmt(ctx, args)); }
export function logError(ctx, err) {
  console.error(_P() + ' ' + ctx, err instanceof Error ? err : err ?? '');
}

const _errTS = { last: 0, count: 0 };
export function reportClientError(payload) {
  const now = Date.now();
  if (now - _errTS.last < 1000) { if (++_errTS.count > 5) return; } else { _errTS.last = now; _errTS.count = 1; }
  try {
    fetch('/api/client-error', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      keepalive: true,
      credentials: 'same-origin',
    }).catch(() => {});
  } catch (e) { console.warn('[ModelWatcher] client error report failed', e); }
}

export function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ''; }

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

export function logTag(comp, dir, type, ...rest) {
  return `${comp} ${dir} ${type}${rest.filter(Boolean).map(s => ' - ' + s).join('')}`;
}

export function slug(s) { return s.replace(/[^a-zA-Z0-9_-]/g, '_'); }

export function parseModelKey(mk) {
  const idx = mk.indexOf('::');
  if (idx < 0) return { provider: '', model: mk };
  return { provider: mk.slice(0, idx), model: mk.slice(idx + 2) };
}

export function isOK(r) { return r.available != null ? r.available : r.success; }

const _escMap = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const _escRe = /[&<>"']/g;
export function esc(s) {
  if (s == null) return '';
  return String(s).replace(_escRe, c => _escMap[c]);
}

let _recentTouch = false;
let _recentTouchTimer = 0;
let _touchMoved = false;

document.addEventListener('touchstart', () => {
  _recentTouch = true; _touchMoved = false; clearTimeout(_recentTouchTimer);
}, { passive: true });
document.addEventListener('touchmove', () => { _touchMoved = true; }, { passive: true });
document.addEventListener('touchend', () => {
  clearTimeout(_recentTouchTimer);
  _recentTouchTimer = setTimeout(() => { _recentTouch = false; }, 400);
}, { passive: true });

export function isRecentTouch() { return _recentTouch; }
export function wasTouchMove() { return _touchMoved; }

export const isTouchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0;

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

export const BP_SM = 640;

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
  <button class="acc-btn${extraBtnCls}" data-state="${stateAttr}" aria-expanded="${open}" tabindex="0"${tipAttr}>
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

export function kvSep() {
  return '<hr class="kv-sep">';
}

const _SEP = '<hr class="kv-sep">';
export function kvGrids(rows) {
  const groups = [[]];
  for (const r of rows) {
    if (r === _SEP) { groups.push([]); continue; }
    groups[groups.length - 1].push(r);
  }
  return groups.filter(g => g.length)
    .map(g => `<div class="kv-grid">${g.join('')}</div>`)
    .join(_SEP);
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

