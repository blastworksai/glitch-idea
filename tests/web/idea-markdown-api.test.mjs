// Read-only idea Markdown for the backlog card link (redesign R6): bounded text, never HTML.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = await readFile(new URL('../../glitch-idea/web/api.js', import.meta.url), 'utf8');
const {IdeaApi, ApiError} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const binding = 'binding_' + 'a'.repeat(32), idea = 'idea_' + 'c'.repeat(32);
const bytes = value => new TextEncoder().encode(value);

function reply(text, {type = 'text/plain; charset=utf-8', status = 200, length = null} = {}) {
  const raw = typeof text === 'string' ? bytes(text) : text, chunks = [raw];
  const reader = {async read() { return chunks.length ? {done: false, value: chunks.shift()} : {done: true}; }, async cancel() {}, releaseLock() {}};
  return {status, ok: status >= 200 && status < 300, body: {getReader: () => reader, cancel: async () => {}},
    headers: new Headers({'Content-Length': String(length ?? raw.byteLength), 'Content-Type': type})};
}
const api = response => { const calls = []; const value = new IdeaApi(async (url, options) => { calls.push({url, options}); return response; }, 1000, binding); return {value, calls}; };
const refused = code => error => error instanceof ApiError && error.code === code;

test('an idea Markdown file reads as plain text through one authenticated GET', async () => {
  const h = api(reply('# Lunch box\n\n<script>not run</script> é'));
  assert.equal(await h.value.ideaMarkdown(idea), '# Lunch box\n\n<script>not run</script> é');
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0].url, '/api/v1/ideas/' + idea + '/markdown');
  assert.equal(h.calls[0].options.method, 'GET'); assert.equal(h.calls[0].options.headers['X-Idea-Binding'], binding);
  assert.equal(h.calls[0].options.redirect, 'error');
});

test('only an idea id is ever sent, and only text/plain UTF-8 within the size limit is believed', async () => {
  await assert.rejects(api(reply('x')).value.ideaMarkdown('../IDEAS'), refused('invalid_selection'));
  await assert.rejects(api(reply('<b>x</b>', {type: 'text/html'})).value.ideaMarkdown(idea), refused('invalid_response'));
  await assert.rejects(api(reply(new Uint8Array([0xff, 0xfe]))).value.ideaMarkdown(idea), refused('invalid_response'));
  await assert.rejects(api(reply('abc', {length: 99})).value.ideaMarkdown(idea), refused('invalid_response'));
  await assert.rejects(api(reply('x', {length: 3 * 1024 * 1024})).value.ideaMarkdown(idea), refused('invalid_response'));
});

test('a refusal carries the service code only, never its text', async () => {
  const error = await api(reply('{"ok":false,"code":"not_found"}', {type: 'application/json', status: 404})).value.ideaMarkdown(idea).catch(e => e);
  assert.ok(error instanceof ApiError); assert.equal(error.code, 'not_found'); assert.equal(error.status, 404);
  await assert.rejects(api(reply('{"ok":false,"code":"Not Found /home/x"}', {type: 'application/json', status: 404})).value.ideaMarkdown(idea), refused('invalid_response'));
});
