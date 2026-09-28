// Connection status and reconnect policy. The header dot and the "Server unreachable"
// banner have one writer (renderConnStatus), derived from two inputs: the socket state
// (setWSStatus, ws.js) and the backend-down flag (setBackendDown, api.js). The close
// classification and reconnect pacing are pure so node --test covers them.
// Pacing, close codes and paths come from the server (state.conn, see backend/websocket.py).
import { state } from './state.js';

// Glossary order; each state has a `ws_<state>` HELP tip and a #ws-status[data-state] CSS rule
// 'unconfigured': the page came without its bootstrap (app.js stops at start, finding F32)
export const CONN_STATES = ['connecting', 'connected', 'restarting', 'busy', 'rejected', 'disconnected', 'down', 'unconfigured'];

const _listeners = [];

// Called after every change of the shown connection state (app.js keeps the main area's load state)
export function onConnChange(fn) { _listeners.push(fn); }

export function connStatus(wsStatus, backendDown) {
  return backendDown ? 'down' : wsStatus;
}

export function renderConnStatus() {
  const status = connStatus(state._wsStatus, state._backendDown);
  const dot = document.getElementById('ws-status');
  if (dot) {
    dot.dataset.state = status;
    dot.setAttribute('data-tip', `ws_${status}`);
    dot.setAttribute('aria-label', status);
  }
  // The hidden attribute works without the stylesheet; a CSS class left the banner showing (F5)
  const banner = document.getElementById('backend-down-banner');
  if (banner) banner.hidden = !state._backendDown;
  for (const fn of _listeners) fn(status);
}

export function setWSStatus(status) {
  state._wsStatus = status;
  renderConnStatus();
}

export function setBackendDown(down) {
  state._backendDown = down;
  renderConnStatus();
}

// What a close means. Only a socket that never got its hello and did not close cleanly, or that
// the page closed because no hello came (F25), failed to reach the server; every server-sent
// close proves the server is up.
export function wsCloseKind({ code, wasClean, hello, restarting, helloTimedOut }, codes) {
  if (restarting || code === codes.restart) return 'restart';
  if (code === codes.policy) return 'rejected';
  if (code === codes.try_again) return 'busy';
  if (helloTimedOut || (!hello && !wasClean)) return 'failed';
  return 'lost';
}

const _KIND_STATUS = { restart: 'restarting', rejected: 'rejected', busy: 'busy', failed: 'disconnected', lost: 'disconnected' };

// Delay before the next attempt and the backoff after it. The backoff only resets on a hello
// (ws.js), so a socket the server keeps closing backs off instead of retrying at min_delay.
export function wsReconnectPlan(kind, backoffMs, backendDown, conn) {
  const minMs = conn.reconnect.min_delay * 1000;
  const maxMs = conn.reconnect.max_delay * 1000;
  let delayMs = kind === 'restart' ? minMs : kind === 'rejected' ? maxMs : backoffMs;
  if (backendDown) delayMs = Math.max(delayMs, conn.unreachable.retry_interval * 1000);
  return { status: _KIND_STATUS[kind], delayMs, nextBackoffMs: Math.min(delayMs * 2, maxMs) };
}
