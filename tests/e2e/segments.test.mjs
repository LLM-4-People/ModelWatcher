// Layout checks of every segment row on the page (utils.js segmentsHTML).
// Catches finding F6: the modal check line rendered "Bench 5h 2m ·OK10h 1m" (0px gaps inside a
// nested wrapper), and the same middle dot had five different spacings across the page.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startServer, launchBrowser, pageModule } from './harness.mjs';

const TOLERANCE_PX = 0.5;
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

// Open the first card and put it in the states that render every kind of row: health OK,
// last benchmark failed after an older success (the "OK <age>" group), an error box, card metadata.
async function openForcedModal(width) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  await page.goto(server.url + '/', { waitUntil: 'networkidle' });
  await page.locator('[data-model-key]').first().click();
  await page.waitForSelector('#modal:not(.hidden) #modal-chk .test-line');
  const urls = Object.fromEntries(await Promise.all(['state.js', 'modal.js', 'dom.js'].map(async n => [n, await pageModule(page, n)])));
  await page.evaluate(async urls => {
    const { state } = await import(urls['state.js']);
    const { updateModalIfNeeded } = await import(urls['modal.js']);
    const { updateCardDOM } = await import(urls['dom.js']);
    const id = document.getElementById('modal-chk').dataset.mwModel;
    const now = Date.now() / 1000;
    Object.assign(state.metrics[id], {
      status: 'error', health_success: true, health_ts_epoch: now - 60, health_success_epoch: now - 60,
      last_benchmark_epoch: now - 600, last_success_epoch: now - 7200,
    });
    state.metrics[id].last_test = { ...(state.metrics[id].last_test || {}), success: false, error: 'HTTP 503 upstream overloaded', timestamp: new Date((now - 600) * 1000).toISOString() };
    Object.assign(state._modelMap[id], { context_window: 400000, output_context: 128000, quantization: 'fp8', param_count: '1.8T', num_experts: 64 });
    updateModalIfNeeded(id, {});
    updateCardDOM(id);
  }, urls);
  return page;
}

// Per visible top-level .seg-list row: its gap and the distance between each pair of adjacent
// visible leaf elements on the same line. Leaves, not direct children, so a nested group that
// is not itself a .seg-list cannot hide glued items (it did in F6).
const measureLists = page => page.evaluate(() => {
  const visible = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const rows = [...document.querySelectorAll('.seg-list')].filter(l => visible(l) && !l.parentElement.closest('.seg-list'));
  return rows.map(row => {
    const leaves = [...row.querySelectorAll('*')].filter(el => visible(el) && ![...el.children].some(visible));
    const gap = Math.min(...[row, ...row.querySelectorAll('.seg-list')].map(l => parseFloat(getComputedStyle(l).columnGap)));
    const pairs = [];
    for (let i = 1; i < leaves.length; i++) {
      const a = leaves[i - 1].getBoundingClientRect();
      const b = leaves[i].getBoundingClientRect();
      if (b.top >= a.bottom || a.top >= b.bottom) continue;  // wrapped onto another line
      pairs.push({ from: leaves[i - 1].textContent.trim() || leaves[i - 1].className, to: leaves[i].textContent.trim() || leaves[i].className, px: b.left - a.right });
    }
    return { where: row.closest('[id]')?.id, text: row.textContent.replace(/\s+/g, ' ').trim(), gap, pairs };
  });
});

// Space left and right of every visible separator. A separator may open a nested group (the
// "· OK <age>" group hides whole on phones), so its neighbor is looked up through enclosing lists.
const measureSeps = page => page.evaluate(() => {
  const visible = el => el.getClientRects().length > 0;
  const neighbor = (el, prop) => {
    for (let n = el; n; n = n.parentElement) {
      let sib = n[prop];
      while (sib && !visible(sib)) sib = sib[prop];
      if (sib) return sib;
      if (!n.parentElement?.classList.contains('seg-list')) return null;
    }
    return null;
  };
  return [...document.querySelectorAll('.seg-sep, .seg-rule')].filter(visible).map(s => {
    const r = s.getBoundingClientRect();
    const prev = neighbor(s, 'previousElementSibling')?.getBoundingClientRect();
    const next = neighbor(s, 'nextElementSibling')?.getBoundingClientRect();
    return { kind: s.className, where: s.closest('[id]')?.id, left: prev ? r.left - prev.right : null, right: next ? next.left - r.right : null };
  });
});

test('every segment row keeps its gap between all visible items (desktop)', async () => {
  const page = await openForcedModal(1400);
  const lists = await measureLists(page);
  const where = new Set(lists.map(l => l.where));
  for (const id of ['modal-chk', 'modal-title', 'modal-info', 'schedule-info']) assert.ok(where.has(id), `no segment row in #${id}`);
  assert.ok([...where].some(id => id?.startsWith('mi-')), 'no card info row');
  const touching = lists.flatMap(l => l.pairs.filter(p => p.px < l.gap - TOLERANCE_PX).map(p => `#${l.where}: ${p.from} -> ${p.to} (${p.px.toFixed(1)}px < ${l.gap}px)`));
  assert.deepEqual(touching, []);
  await page.close();
});

test('every separator has the same space on both sides, everywhere', async () => {
  const page = await openForcedModal(1400);
  const seps = (await measureSeps(page)).filter(s => s.kind === 'seg-sep');
  assert.ok(seps.length >= 5, `only ${seps.length} separators rendered`);
  for (const s of seps) assert.ok(s.left != null && Math.abs(s.left - s.right) <= TOLERANCE_PX, `#${s.where}: ${s.left}px / ${s.right}px`);
  const spacings = seps.map(s => s.left);
  assert.ok(Math.max(...spacings) - Math.min(...spacings) <= TOLERANCE_PX, `separator spacing differs across the page: ${spacings}`);
  const chip = await page.locator('#modal-chk .test-line').innerText();
  assert.doesNotMatch(chip, /·\S|\SOK|OK\d/, `glued tokens in "${chip}"`);
  await page.close();
});

test('on phones the last-OK group hides whole, leaving no orphan separator', async () => {
  const page = await openForcedModal(400);
  assert.equal(await page.locator('#modal-chk .last-ok').isVisible(), false);
  const orphans = (await measureSeps(page)).filter(s => s.left == null || s.right == null);
  assert.deepEqual(orphans, [], 'separator with nothing on one side');
  await page.close();
});
