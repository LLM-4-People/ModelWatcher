// Browser checks of the header connection dot and the "Server unreachable" banner.
// Catches finding F5 end to end: the dashboard's own socket was rejected (red dot), the client
// retried every 3s forever, WebSocket closes and a 503 readiness check produced a false
// "Server unreachable" that never cleared, and the banner showed whenever the stylesheet was missing.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startServer, launchBrowser, pageConn, waitForDot } from './harness.mjs';

let server;
let browser;

before(async () => {
  server = await startServer();
  browser = await launchBrowser();
});

after(async () => {
  await browser?.close();
  await server?.stop();
});

async function openPage(setup = async () => {}) {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const events = { opens: [], frames: [], console: [] };
  page.on('websocket', ws => {
    events.opens.push(Date.now());
    ws.on('framereceived', f => events.frames.push(String(f.payload)));
  });
  page.on('console', m => events.console.push(m.text()));
  await setup(page);
  await page.goto(server.url + '/');
  return { page, events };
}

const bannerHidden = page => page.evaluate(() => {
  const b = document.getElementById('backend-down-banner');
  return b.hidden && getComputedStyle(b).display === 'none';
});

// Accepted mocked sockets that the "server" closes at once with one code; records each attempt
function closingSocket(code, attempts) {
  return page => page.routeWebSocket(/\/ws$/, ws => {
    attempts.push(Date.now());
    ws.close({ code, reason: 'test' });
  });
}

const gaps = times => times.slice(1).map((t, i) => t - times[i]);

test('the page holds one live socket on a server with tests disabled', async () => {
  const { page, events } = await openPage();
  const conn = await pageConn(page);
  await waitForDot(page, 'connected', 5000);
  // Several heartbeat periods and stale windows: a quiet server must not look dead
  await page.waitForTimeout(conn.stale_after * 3 * 1000);
  assert.equal(await page.evaluate(() => document.getElementById('ws-status').dataset.state), 'connected');
  assert.equal(events.opens.length, 1, 'the socket was reopened');
  assert.ok(events.frames.filter(f => f.includes('"heartbeat"')).length >= 3, 'no heartbeats received');
  assert.ok(!events.console.some(t => t.includes('Stale')), 'a healthy socket was declared stale');
  assert.ok(await bannerHidden(page));
  await page.close();
});

test('the banner stays hidden without the stylesheet', async () => {
  const { page } = await openPage(p => p.route(/tailwind\.min\.css/, r => r.abort()));
  await waitForDot(page, 'connected', 5000);
  assert.ok(await bannerHidden(page));
  await page.close();
});

test('a busy server is retried on a growing backoff, never marked unreachable', async () => {
  const attempts = [];
  const { page } = await openPage(closingSocket(1013, attempts));
  const conn = await pageConn(page);
  assert.equal(conn.close_codes.try_again, 1013);
  await waitForDot(page, 'busy', 5000);
  const window = (conn.reconnect.min_delay + 2 * conn.reconnect.min_delay + 4 * conn.reconnect.min_delay) * 1000;
  await page.waitForTimeout(window + 500);
  const g = gaps(attempts);
  assert.ok(g.length >= 3, `only ${attempts.length} attempts`);
  assert.ok(g[1] > g[0] * 1.5 && g[2] > g[1] * 1.5, `backoff did not grow: ${g}`);
  assert.ok(await bannerHidden(page), 'server-sent closes marked the server unreachable');
  await page.close();
});

test('an origin rejection shows as rejected and retries at the slowest pace', async () => {
  const attempts = [];
  const { page } = await openPage(closingSocket(1008, attempts));
  const conn = await pageConn(page);
  assert.equal(conn.close_codes.policy, 1008);
  await waitForDot(page, 'rejected', 5000);
  await page.waitForTimeout(conn.reconnect.max_delay * 2 * 1000 + 500);
  const g = gaps(attempts);
  assert.ok(g.length >= 2, `only ${attempts.length} attempts`);
  for (const gap of g) assert.ok(gap >= conn.reconnect.max_delay * 1000 * 0.9, `retried after ${gap} ms`);
  assert.equal(await page.evaluate(() => document.getElementById('ws-status').dataset.tip), 'ws_rejected');
  assert.ok(await bannerHidden(page));
  await page.close();
});

test('a burst of proxy errors shows the banner, which clears through liveness while /health is 503', async () => {
  let failures = 3;
  const sockets = [];
  const { page } = await openPage(async p => {
    // A silent socket: no hello, so only the liveness probe can end the unreachable state
    await p.routeWebSocket(/\/ws$/, ws => sockets.push(ws));
    await p.route(/\/api\//, route => (failures-- > 0 ? route.fulfill({ status: 502, body: '' }) : route.continue()));
  });
  const conn = await pageConn(page);
  await page.waitForFunction(() => !document.getElementById('backend-down-banner').hidden, null, { timeout: 5000 });
  assert.equal(await page.evaluate(() => document.getElementById('ws-status').dataset.state), 'down');
  const readiness = await page.evaluate(() => fetch('/health').then(r => r.status));
  assert.equal(readiness, 503, 'readiness must be failing for this check to mean anything');
  await page.waitForFunction(() => document.getElementById('backend-down-banner').hidden, null,
    { timeout: (conn.unreachable.retry_interval * 2 + 1) * 1000 });
  assert.notEqual(await page.evaluate(() => document.getElementById('ws-status').dataset.state), 'down');
  // Recovery reconnects only when no socket is live; it used to open a second one next to it
  assert.equal(sockets.length, 1);
  await page.close();
});
