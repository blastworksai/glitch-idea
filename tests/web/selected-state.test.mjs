// Selected-state evidence: every pressed choice button must look different from an unpressed one,
// with a colour-only change (no layout change). CSS text checks only; this does not qualify a browser.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const cssUrl = process.env.SELECTED_STATE_CSS ? new URL('file://' + process.env.SELECTED_STATE_CSS) : new URL('../../glitch-idea/web/styles.css', import.meta.url);
const css = (await readFile(cssUrl, 'utf8')).replace(/\/\*[\s\S]*?\*\//g, ' ');
const LAYOUT = ['width', 'height', 'min-height', 'min-width', 'max-width', 'max-height', 'padding', 'border-width', 'font-size', 'font-weight', 'margin', 'display'];
const PRESSED = 'button[aria-pressed="true"]';
function rules() {
  const out = [];
  for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const decls = Object.fromEntries(m[2].split(';').filter(p => p.includes(':')).map(p => { const i = p.indexOf(':'); return [p.slice(0, i).trim().toLowerCase(), p.slice(i + 1).trim().toLowerCase()]; }));
    out.push({selectors: m[1].split(',').map(s => s.trim().replace(/\s+/g, ' ')), decls});
  }
  return out;
}
const find = sel => rules().filter(r => r.selectors.includes(sel));
const baseButton = () => find('button').map(r => r.decls.background).find(Boolean);

test('a pressed button has a background different from the base button', () => {
  const pressed = find(PRESSED).map(r => r.decls.background).find(Boolean);
  assert.ok(pressed, 'no ' + PRESSED + ' rule sets a background');
  assert.notEqual(pressed, baseButton());
});

test('a pressed hover rule exists and keeps the button looking selected', () => {
  const hover = find(PRESSED + ':hover');
  assert.ok(hover.length, 'no pressed hover rule');
  const bg = hover.map(r => r.decls.background).find(Boolean);
  assert.ok(bg, 'pressed hover sets no background');
  assert.notEqual(bg, find('button:hover').map(r => r.decls.background).find(Boolean));
});

test('pressed rules change colour only, never layout', () => {
  const pressedRules = rules().filter(r => r.selectors.some(s => s.startsWith(PRESSED) && !s.startsWith('.')));
  assert.ok(pressedRules.length >= 2);
  for (const r of pressedRules) for (const prop of LAYOUT) {
    assert.equal(prop in r.decls, false, r.selectors.join(', ') + ' declares ' + prop);
  }
});
