// Segment and separator primitives (frontend/js/utils.js).
// Catches finding F6: a group nested in a flex row as a plain wrapper lost the row's gap
// ("Bench 5h 2m ·OK10h 1m"). Every group must itself be a .seg-list, and separators
// must never carry their own spacing.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { segmentsHTML, sepHTML, SEP, SEP_TEXT, STATUS_GLYPH, kvSep, kvGrids } from '../../frontend/js/utils.js';

const LIST = /^<span class="seg-list[^"]*"[^>]*>/;

test('a segment list is a .seg-list and drops empty parts', () => {
  assert.equal(segmentsHTML(['', null, undefined]), '');
  const html = segmentsHTML(['<b>a</b>', '', '<b>b</b>']);
  assert.match(html, LIST);
  assert.equal(html.match(/<b>/g).length, 2);
});

test('separators are aria-hidden and have no spacing classes of their own', () => {
  for (const kind of ['dot', 'slash', 'rule']) {
    const html = sepHTML(kind);
    assert.match(html, /aria-hidden="true"/);
    assert.doesNotMatch(html, /\b(m[xlr]?|p[xlr]?)-\S+/, `${kind} separator carries margin or padding`);
  }
  assert.match(sepHTML('dot'), new RegExp(SEP.dot));
});

test('a separator goes only between present parts', () => {
  const html = segmentsHTML(['a', '', 'b', null], { sep: 'dot' });
  assert.equal(html.split('seg-sep').length - 1, 1);
  assert.equal(segmentsHTML(['a'], { sep: 'rule' }).includes('seg-rule'), false);
});

test('a nested group is itself a segment list, so the gap applies inside it too', () => {
  const inner = segmentsHTML([sepHTML('dot'), 'OK', '10h'], { cls: 'last-ok' });
  assert.match(inner, /^<span class="seg-list last-ok">/);
  const outer = segmentsHTML(['x', 'Bench', '5h', inner]);
  assert.equal(outer.match(/class="seg-list/g).length, 2);
});

test('copied text keeps a space around every item', () => {
  const html = segmentsHTML(['<i>8:16 PM</i>', '<i>HTTP 503</i>'], { sep: 'dot' });
  const text = html.replace(/<[^>]+>/g, '');
  assert.equal(text, `8:16 PM ${SEP.dot} HTTP 503`);
  assert.equal(SEP_TEXT, ` ${SEP.dot} `);
});

test('attributes and classes reach the list element', () => {
  const html = segmentsHTML(['a'], { cls: 'badge-chip test-line', attrs: 'data-tip="checkLine"' });
  assert.match(html, /^<span class="seg-list badge-chip test-line" data-tip="checkLine">/);
});

test('each outcome has its own glyph', () => {
  const glyphs = Object.values(STATUS_GLYPH);
  assert.equal(new Set(glyphs).size, glyphs.length);
  assert.deepEqual(Object.keys(STATUS_GLYPH).sort(), ['degraded', 'failed', 'ok', 'unknown']);
});

test('kvGrids splits on the one kvSep marker', () => {
  const html = kvGrids(['<a>', kvSep(), '<b>']);
  assert.equal(html, `<div class="kv-grid"><a></div>${kvSep()}<div class="kv-grid"><b></div>`);
});
