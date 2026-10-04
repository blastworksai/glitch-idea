// Backlog re-ranking (redesign R7): one strict, CSRF-carrying write with an exact reply.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = await readFile(new URL('../../glitch-idea/web/api.js', import.meta.url), 'utf8');
const {IdeaApi, ApiError} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const binding = 'binding_' + 'a'.repeat(32), session = 'session_' + 'b'.repeat(32), idea = 'idea_' + 'c'.repeat(32);
const payload = {request_id: 'rerank-1', idea_id: idea, expected_backlog_revision: 4, position: 3, reason: null};
const good = {ok: true, code: 'ok', request_id: 'rerank-1', write_state: 'applied', idea_id: idea, position: 3, backlog_revision: 5, revision: 8, draft_version: 1};
function api(body, status = 200) {
  const calls = [];
  const value = new IdeaApi(async (url, options) => { calls.push({url, options}); return {status, ok: status < 300, json: async () => body}; }, 1000, binding);
  value.csrf = 'test-only-csrf'; value.sessionId = session;
  return {value, calls};
}

test('a move posts once with CSRF to the rerank route and returns the exact receipt', async () => {
  const h = api(good);
  assert.deepEqual(await h.value.rerank(payload), good);
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0].url, '/api/v1/rerank');
  assert.equal(h.calls[0].options.method, 'POST'); assert.equal(h.calls[0].options.headers['X-CSRF-Token'], 'test-only-csrf');
  assert.deepEqual(JSON.parse(h.calls[0].options.body), payload);
});

test('a malformed move is refused before any request, and an inexact reply is uncertain', async () => {
  for (const change of [{position: 0}, {position: 1.5}, {reason: '  '}, {idea_id: '../x'}, {extra: 1}]) {
    const h = api(good);
    await assert.rejects(h.value.rerank({...payload, ...change}), error => error instanceof ApiError && error.code === 'invalid_input');
    assert.equal(h.calls.length, 0);
  }
  for (const reply of [{...good, position: 2}, {...good, write_state: 'not_applied'}, {...good, idea_id: 'idea_' + 'd'.repeat(32)}, {...good, extra: 1}]) {
    await assert.rejects(api(reply).value.rerank(payload), error => error instanceof ApiError && error.code === 'invalid_response' && error.uncertain === true);
  }
  await assert.rejects(api({ok: false, code: 'stale_backlog'}, 409).value.rerank(payload), error => error.code === 'stale_backlog');
});
