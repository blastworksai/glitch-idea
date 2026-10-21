// SKILLS-96 glossary: the page speaks plain words, never the store's vocabulary.
// Every sentence-like string literal in the files the Capture files card,
// Visualize and Review render from is read and checked. Code-only literals
// (ids, kinds, codes) have no space, so they are not member-facing text.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const web = new URL('../../glitch-idea/web/', import.meta.url);
const FILES = ['app.js', 'folds.js', 'steps/visualize.js', 'steps/review.js'];
// The store's own names; the member never sees them.
const BANNED = /membership|intent|disposition|retained|asset/i;

// A small JavaScript tokenizer: enough to tell strings from comments and
// regular-expression literals, which a plain quote search mistakes for text.
function stringLiterals(source) {
  const out = [];
  let i = 0, last = '';
  const regexCanStart = () => last === '' || /[(,=:[!&|?{};+\-*%<>~^]$/.test(last) || /\b(return|typeof|case|in|of)$/.test(last);
  while (i < source.length) {
    const c = source[i], next = source[i + 1];
    if (c === '/' && next === '/') { i = source.indexOf('\n', i); if (i < 0) break; continue; }
    if (c === '/' && next === '*') { i = source.indexOf('*/', i + 2) + 2; if (i < 2) break; continue; }
    if (c === "'" || c === '"' || c === '`') {
      let j = i + 1, text = '';
      while (j < source.length && source[j] !== c) {
        if (source[j] === '\\') { text += source[j + 1]; j += 2; continue; }
        if (c === '`' && source[j] === '$' && source[j + 1] === '{') {
          let depth = 1; j += 2;
          while (j < source.length && depth) { if (source[j] === '{') depth++; else if (source[j] === '}') depth--; j++; }
          text += '${}'; continue;
        }
        text += source[j]; j++;
      }
      out.push({text, line: source.slice(0, i).split('\n').length});
      i = j + 1; last = 'x'; continue;
    }
    if (c === '/' && regexCanStart()) {
      let j = i + 1, inClass = false;
      while (j < source.length && source[j] !== '\n') {
        if (source[j] === '\\') { j += 2; continue; }
        if (source[j] === '[') inClass = true; else if (source[j] === ']') inClass = false;
        else if (source[j] === '/' && !inClass) break;
        j++;
      }
      i = j + 1; while (/[a-z]/i.test(source[i] ?? '')) i++;
      last = 'x'; continue;
    }
    if (!/\s/.test(c)) last = (last + c).slice(-12);
    i++;
  }
  return out;
}

const sentences = source => stringLiterals(source).filter(item => item.text.includes(' '));

test('the tokenizer catches a banned word and skips comments and regular expressions', () => {
  const sample = [
    "// a comment's apostrophe and the word membership",
    "const re = /[\"']membership/g, n = 4 / 2;",
    "/* block 'disposition' */",
    "const shown = 'Your upload intents are kept';",
    "const code = 'upload-intent';",
    "const t = `Accepted ${count} design assets`;",
  ].join('\n');
  const found = sentences(sample).filter(item => BANNED.test(item.text)).map(item => item.text);
  assert.deepEqual(found, ['Your upload intents are kept', 'Accepted ${} design assets']);
});

for (const name of FILES) {
  test('no member-facing string in ' + name + ' uses the store\'s vocabulary', async () => {
    const source = await readFile(new URL(name, web), 'utf8');
    const all = sentences(source);
    assert.ok(all.length > 20, name + ': the tokenizer found too few strings to be reading the file');
    const bad = all.filter(item => BANNED.test(item.text)).map(item => name + ':' + item.line + ' ' + JSON.stringify(item.text));
    assert.deepEqual(bad, []);
  });
}

test('the old store-worded lines named in SKILLS-96 are gone from the page', async () => {
  const sources = (await Promise.all(FILES.map(name => readFile(new URL(name, web), 'utf8')))).join('\n');
  for (const old of ['Create a new set from selected files', 'Original files stay separate', 'Files stay separate from accepted',
    'Unfinished file selections live only in this tab', 'retained incomplete upload intents', 'explicitly selected files',
    'Selected set: ', 'Retained asset capacity', 'Pause saves your decision draft']) assert.ok(!sources.includes(old), old);
});

test('the three Visualize card titles stay exact, because the skill names them', async () => {
  const source = await readFile(new URL('steps/visualize.js', web), 'utf8');
  assert.match(source, /CARD_CLAUDE = 'Visualize in Claude Design and import it back', CARD_PROTOTYPE = 'Prototype Here', CARD_SKIP = 'Skip visualization'/);
});
