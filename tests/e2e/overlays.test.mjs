// Overlays, tooltips, focus and keyboard use of the dashboard.
// Catches: F49 (Escape in the date picker closed the whole modal), F52 (a tip stayed over the
// picker or Help section its control had just opened), F53 (on touch a long-pressed tip was never
// dismissed and the long-press also opened the modal), F54 (day headers showed the wrong chevron
// and were pointer-only), F55 (history pagination widened the stored window), F56 (the theme
// toggle never returned to the system setting), F72 (the modal left focus behind and the page
// scrolled under it), F73 (the closed Help drawer kept its controls in the tab order).
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startServer, launchBrowser, pageModule } from './harness.mjs';

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

async function openDashboard(options = {}) {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 }, ...options });
  await page.goto(server.url + '/', { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-model-key]');
  return page;
}

// Opens the first card's modal and waits for its data, after which the range pills are final
async function openModal(page) {
  await page.locator('[data-model-key] .card-open').first().click();
  await page.waitForSelector('#modal:not(.hidden) #modal-chart');
  await page.waitForLoadState('networkidle');
  await page.waitForSelector('#modal .hist-day .day-toggle');
}

const modalOpen = page => page.evaluate(() => !document.getElementById('modal').classList.contains('hidden'));
const tipVisible = page => page.evaluate(() => document.getElementById('help-tip')?.classList.contains('visible') ?? false);
const storageKeys = page => page.evaluate(() => window.__MW_BOOT__.storage_keys);

test('Escape closes the date picker first, then the modal (F49)', async () => {
  const page = await openDashboard();
  await openModal(page);
  await page.locator('[data-range="custom"]').click();
  await page.waitForSelector('.date-range-popover');
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('.date-range-popover').count(), 0, 'the picker closes');
  assert.equal(await modalOpen(page), true, 'the modal stays open');
  await page.keyboard.press('Escape');
  assert.equal(await modalOpen(page), false);
  await page.close();
});

test('a tip hides when its control opens something (F52)', async () => {
  const page = await openDashboard();
  await openModal(page);
  const calendar = page.locator('[data-range="custom"]');
  await calendar.hover();
  await page.waitForFunction(() => document.getElementById('help-tip').classList.contains('visible'));
  await calendar.click();
  await page.waitForSelector('.date-range-popover');
  assert.equal(await tipVisible(page), false, 'the calendar tip covered the picker header');
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');

  await page.locator('#help-btn').click();
  await page.waitForSelector('#help-panel.open');
  const section = page.locator('#help-panel .acc-btn[data-tip]').first();
  await section.hover();
  await page.waitForFunction(() => document.getElementById('help-tip').classList.contains('visible'));
  await section.click();
  assert.equal(await tipVisible(page), false, 'a Help section tip covered the section it expanded');
  await page.close();
});

test('on touch a long-press shows a tip without opening the card, and a tap dismisses it (F53)', async () => {
  const page = await openDashboard({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });
  const tile = page.locator('[data-model-key] [data-tip="ttft"]').first();
  await tile.scrollIntoViewIfNeeded();
  const box = await tile.boundingBox();
  const cdp = await page.context().newCDPSession(page);
  const point = [{ x: box.x + box.width / 2, y: box.y + box.height / 2 }];
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: point });
  await page.waitForTimeout(800);
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await page.waitForTimeout(300);
  assert.equal(await tipVisible(page), true, 'the long-press shows the tip');
  assert.equal(await modalOpen(page), false, 'the long-press must not also open the modal');
  await page.touchscreen.tap(5, 5);
  await page.waitForTimeout(200);
  assert.equal(await tipVisible(page), false, 'a tap anywhere dismisses a long-pressed tip');
  await page.close();
});

test('the modal holds focus and returns it (F72)', async () => {
  const page = await openDashboard();
  const opener = page.locator('[data-model-key] .card-open').first();
  await opener.focus();
  await page.keyboard.press('Enter');
  await page.waitForSelector('#modal:not(.hidden) #modal-chart');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'modal-close');
  assert.equal(await page.evaluate(() => document.documentElement.classList.contains('scroll-locked')), true);
  // Past the last control focus goes to the browser (the page itself is inert), never behind the dialog
  for (let i = 0; i < 40; i++) {
    await page.keyboard.press('Tab');
    assert.ok(await page.evaluate(() => document.activeElement === document.body || document.getElementById('modal').contains(document.activeElement)),
      `Tab ${i + 1} reached ${await page.evaluate(() => document.activeElement.outerHTML.slice(0, 80))}`);
  }
  await page.keyboard.press('Escape');
  assert.equal(await page.evaluate(() => document.activeElement.dataset.openModel), await opener.getAttribute('data-open-model'));
  assert.equal(await page.evaluate(() => document.documentElement.classList.contains('scroll-locked')), false);
  await page.close();
});

test('the closed Help drawer is out of the tab order (F73)', async () => {
  const page = await openDashboard();
  const panel = page.locator('#help-panel');
  assert.equal(await panel.evaluate(el => el.inert), true);
  for (let i = 0; i < 60; i++) {
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => !!document.activeElement.closest('#help-panel')), false, 'focus entered the closed drawer');
  }
  await page.locator('#help-btn').click();
  await page.waitForSelector('#help-panel.open');
  assert.equal(await panel.evaluate(el => el.inert), false);
  assert.ok(await page.evaluate(() => document.getElementById('help-panel').contains(document.activeElement)));
  await page.keyboard.press('Escape');
  assert.equal(await panel.evaluate(el => el.inert), true);
  await page.close();
});

test('history day headers are buttons whose chevron follows their state (F54)', async () => {
  const page = await openDashboard();
  await openModal(page);
  const toggle = page.locator('.hist-day .day-toggle').first();
  await toggle.waitFor();
  const state = () => toggle.evaluate(el => ({
    expanded: el.getAttribute('aria-expanded'),
    // The chevron points down while open, right while collapsed
    angle: Math.round(Math.atan2(...(() => { const m = new DOMMatrix(getComputedStyle(el.querySelector('.day-chevron')).transform); return [m.b, m.a]; })()) * 180 / Math.PI),
  }));
  const open = await state();
  assert.equal(open.expanded, 'true');
  await toggle.focus();
  await page.keyboard.press('Enter');
  await page.waitForTimeout(300);
  const closed = await state();
  assert.equal(closed.expanded, 'false', 'Enter toggles the day');
  assert.equal(Math.abs(open.angle - closed.angle), 90, `open ${open.angle} deg, collapsed ${closed.angle} deg`);
  assert.ok([0, -90].includes(closed.angle), 'a collapsed day points right');
  await page.keyboard.press('Space');
  await page.waitForTimeout(300);
  assert.equal((await state()).expanded, 'true');
  await page.close();
});

test('the history window is stored as its key only and never widens (F55)', async () => {
  const page = await openDashboard();
  const keys = await storageKeys(page);
  const histSince = async () => page.evaluate(async url => (await import(url)).getHistSince(), await pageModule(page, 'modal-ranges.js'));
  await openModal(page);
  const first = await histSince();
  // Infinite scroll may load rows older than the window; the label shows them, the window stays
  for (let i = 0; i < 4; i++) {
    await page.evaluate(() => { const s = document.getElementById('modal-scroll'); s.scrollTop = s.scrollHeight; });
    await page.waitForTimeout(400);
  }
  assert.equal(await page.evaluate(k => localStorage.getItem(k.HIST_SINCE), keys), null, 'a keyed window stores no start that could creep');
  await page.keyboard.press('Escape');
  await openModal(page);
  assert.ok(await histSince() >= first, 'the next modal opened on a wider window than the key says');
  await page.close();
});

test('the theme toggle cycles system, light, dark and back to system (F56)', async () => {
  const page = await openDashboard();
  const keys = await storageKeys(page);
  const pref = () => page.evaluate(k => localStorage.getItem(k.THEME), keys);
  assert.equal(await pref(), null);
  const seen = [];
  for (let i = 0; i < 3; i++) {
    await page.locator('#theme-btn').click();
    seen.push(await pref());
  }
  assert.deepEqual(seen, [...(await page.evaluate(() => window.__MW_BOOT__.themes)), null]);
  await page.close();
});
