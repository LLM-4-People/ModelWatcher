// Connection status and reconnect policy (frontend/js/conn.js).
// Catches finding F5: an accepted-then-closed socket counted as connected (backoff reset, so a
// retry every 3s forever), server-sent closes counted as HTTP failures (a false "Server unreachable"),
// and one close code stood for both "never" and "later".
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CONN_STATES, connStatus, wsCloseKind, wsReconnectPlan } from '../../frontend/js/conn.js';
import { HELP } from '../../frontend/js/utils.js';

// Same shape as backend/websocket.py connection_config(); values chosen to make the arithmetic obvious
const CONN = {
  reconnect: { min_delay: 1, max_delay: 16 },
  unreachable: { after_failures: 3, retry_interval: 5 },
  close_codes: { normal: 1000, going_away: 1001, restart: 1012, policy: 1008, try_again: 1013, too_big: 1009, internal: 1011, stale: 4000 },
};
const CODES = CONN.close_codes;
const ABNORMAL = 1006;

const kind = (code, { wasClean = true, hello = false, restarting = false } = {}) =>
  wsCloseKind({ code, wasClean, hello, restarting }, CODES);

test('only a socket that never reached the server is a failure', () => {
  assert.equal(kind(ABNORMAL, { wasClean: false }), 'failed');
  assert.equal(kind(ABNORMAL, { wasClean: false, hello: true }), 'lost');
  assert.equal(kind(CODES.stale, { hello: true }), 'lost');
  assert.equal(kind(CODES.internal, { hello: true }), 'lost');
  assert.equal(kind(CODES.too_big, { hello: true }), 'lost');
});

test('server-sent closes are classified by code, never as failures', () => {
  assert.equal(kind(CODES.policy), 'rejected');
  assert.equal(kind(CODES.try_again), 'busy');
  assert.equal(kind(CODES.restart), 'restart');
  assert.equal(kind(ABNORMAL, { wasClean: false, restarting: true }), 'restart');
  for (const code of [CODES.policy, CODES.try_again, CODES.restart]) assert.notEqual(kind(code), 'failed');
});

test('backoff grows across attempts that never get a hello', () => {
  let backoff = CONN.reconnect.min_delay * 1000;
  const delays = [];
  for (let i = 0; i < 7; i++) {
    const plan = wsReconnectPlan('failed', backoff, false, CONN);
    delays.push(plan.delayMs);
    backoff = plan.nextBackoffMs;
  }
  assert.deepEqual(delays, [1000, 2000, 4000, 8000, 16000, 16000, 16000]);
});

test('a busy server is retried on the growing backoff', () => {
  const plan = wsReconnectPlan('busy', 4000, false, CONN);
  assert.deepEqual([plan.status, plan.delayMs, plan.nextBackoffMs], ['busy', 4000, 8000]);
});

test('an origin rejection retries at the slowest pace', () => {
  const plan = wsReconnectPlan('rejected', 1000, false, CONN);
  assert.deepEqual([plan.status, plan.delayMs, plan.nextBackoffMs], ['rejected', 16000, 16000]);
});

test('a restart reconnects promptly', () => {
  const plan = wsReconnectPlan('restart', 16000, false, CONN);
  assert.deepEqual([plan.status, plan.delayMs], ['restarting', 1000]);
});

test('while the server is unreachable no attempt comes faster than retry_interval', () => {
  const plan = wsReconnectPlan('failed', 1000, true, CONN);
  assert.equal(plan.delayMs, 5000);
  assert.equal(plan.nextBackoffMs, 10000);
});

test('the dot shows down whenever the backend is down, else the socket state', () => {
  assert.equal(connStatus('connected', true), 'down');
  assert.equal(connStatus('rejected', false), 'rejected');
});

test('every connection state has a help tip', () => {
  for (const s of CONN_STATES) assert.ok(HELP[`ws_${s}`], `HELP.ws_${s} missing`);
  const planStatuses = ['failed', 'lost', 'busy', 'rejected', 'restart'].map(k => wsReconnectPlan(k, 1000, false, CONN).status);
  for (const s of planStatuses) assert.ok(CONN_STATES.includes(s), `${s} missing from CONN_STATES`);
});
