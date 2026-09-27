// Shared setup for the browser tests: a private server seeded with scale-test data, and Chromium.
// Each server gets its own temp data/config dir and a free port, runs with MW_DISABLE_TESTS=1
// (as DEVELOPMENT.md does) and fast connection timings, so the specs never touch a shared
// instance or the checked-out data/ and config/. Run through `npm run test:e2e`.
//
// Environment: python3 on PATH must have the project requirements (activate the virtualenv);
// MW_E2E_CHROMIUM points Playwright at a Chromium binary when its own download is not installed.
import { spawn, spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = fileURLToPath(new URL('../..', import.meta.url));

// Connection timings short enough to watch several heartbeats, stale windows and backoff steps
export const FAST_CONN = {
  'websocket.heartbeat_interval': 0.5,
  'websocket.stale_after': 2,
  'websocket.reconnect.min_delay': 0.4,
  'websocket.reconnect.max_delay': 1.6,
  'websocket.unreachable.retry_interval': 1,
};
const SEED_ARGS = ['--providers', '2', '--models-per', '3', '--months', '0.05'];
const READY_TIMEOUT_MS = 30_000;
const READY_POLL_MS = 200;

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = createServer().listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
    srv.on('error', reject);
  });
}

// Polls the dashboard page itself, so the harness needs no knowledge of server paths
async function waitServing(url, proc, log) {
  const deadline = Date.now() + READY_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (proc.exitCode != null) throw new Error(`server exited with ${proc.exitCode}:\n${log.join('')}`);
    try {
      if ((await fetch(url + '/')).ok) return;
    } catch {
      // Not listening yet; the deadline below reports the server log if it never comes up
    }
    await new Promise(r => setTimeout(r, READY_POLL_MS));
  }
  throw new Error(`server not live after ${READY_TIMEOUT_MS} ms:\n${log.join('')}`);
}

export async function startServer(overrides = FAST_CONN) {
  const dir = mkdtempSync(join(tmpdir(), 'mw-e2e-'));
  // No code reload: the harness owns exactly one server process
  const sets = Object.entries({ 'app.debug': false, ...overrides }).flatMap(([k, v]) => ['--app-set', `${k}=${v}`]);
  const seed = spawnSync('python3', ['-m', 'scripts.util.scale_test_db', ...SEED_ARGS, '--data-dir', dir, '--config-dir', dir,
    '--app-template', join(ROOT, 'config', 'app.yaml.example'), ...sets], { cwd: ROOT, encoding: 'utf8' });
  if (seed.status !== 0) throw new Error(`seeding failed:\n${seed.stderr}`);
  const port = await freePort();
  const env = {
    ...process.env,
    MW_DB_NAME: join(dir, 'metrics-scale-test.db'),
    MW_MODELS_YAML: join(dir, 'models-scale-test.yaml'),
    MW_APP_YAML: join(dir, 'app-scale-test.yaml'),
    MW_SCALE_TEST_KEY: 'dummy',
    MW_DISABLE_TESTS: '1',
    HOST: '127.0.0.1',
    PORT: String(port),
  };
  const log = [];
  // The same entry point as the Dockerfile and the docs
  const proc = spawn('python3', ['-m', 'backend.main'], { cwd: ROOT, env, stdio: ['ignore', 'pipe', 'pipe'] });
  proc.stdout.on('data', d => log.push(String(d)));
  proc.stderr.on('data', d => log.push(String(d)));
  const url = `http://127.0.0.1:${port}`;
  try {
    await waitServing(url, proc, log);
  } catch (e) {
    proc.kill();
    rmSync(dir, { recursive: true, force: true });
    throw e;
  }
  return {
    url,
    log,
    async stop() {
      proc.kill();
      await new Promise(r => (proc.exitCode != null ? r() : proc.once('exit', r)));
      rmSync(dir, { recursive: true, force: true });
    },
  };
}

export function launchBrowser() {
  return chromium.launch({ executablePath: process.env.MW_E2E_CHROMIUM || undefined });
}

// The page's own connection policy, as the server injected it
export function pageConn(page) {
  return page.evaluate(() => window.__MW_CONN__);
}

// Resolve a frontend module with the page's own ?v= URL so the test shares its module instance
export function pageModule(page, name) {
  return page.evaluate(n => performance.getEntriesByType('resource').map(e => e.name)
    .find(u => u.includes(`/js/${n}?`) || u.endsWith(`/js/${n}`)), name);
}

export async function waitForDot(page, state, timeout) {
  await page.waitForFunction(s => document.getElementById('ws-status')?.dataset.state === s, state, { timeout });
}
