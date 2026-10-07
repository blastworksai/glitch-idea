// The typed client and the lifecycle keys of the Ideas list: moved and delivered rows are pointers to a file in a workspace.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = await readFile(new URL('../../glitch-idea/web/api.js', import.meta.url), 'utf8');
const {IdeaApi, ApiError} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const binding = 'binding_' + 'a'.repeat(32), session = 'session_' + 'b'.repeat(32);
const idea = 'idea_' + 'c'.repeat(32), other = 'idea_' + 'd'.repeat(32);
const homeOf = id => ({workspace_name: 'Shop Site', workspace_path: '/work/shop', file_path: '/work/shop/ideas/' + id + '.md'});
const home = homeOf(other);
const active = (extra = {}) => ({idea_id: idea, revision: 3, position: 1, title: 'An idea', status: 'in-progress', method: null, updated: '2026-10-02T05:00:00+00:00',
  detail_path: '/store/' + idea + '.md', current_step: 'capture', completed_steps: 0, lifecycle: 'active', home: null, delivered_ref: null, read_only: false, ...extra});
const pointer = (lifecycle, extra = {}) => active({idea_id: other, position: 2, status: lifecycle, lifecycle, home, delivered_ref: lifecycle === 'delivered' ? 'PLAN-42' : null,
  read_only: true, detail_path: home.file_path, ...extra});
const reply = rows => ({ok: true, code: 'ok', backlog_revision: 4, total: rows.length, ideas: rows});
const client = body => {
  const api = new IdeaApi(async () => ({ok: true, status: 200, json: async () => structuredClone(body)}), 1000, binding);
  api.sessionId = session; api.csrf = 'csrf'; return api;
};

test('the typed client accepts active, moved and delivered rows with their four lifecycle keys', async () => {
  const body = reply([active(), pointer('moved')]);
  assert.deepEqual(await client(body).ideas(), body);
  const done = reply([active(), pointer('delivered')]);
  assert.deepEqual(await client(done).ideas(), done);
});

test('the typed client still rejects an unknown key, a wrong type and an inconsistent lifecycle', async () => {
  const bad = [
    {extra: 1}, {read_only: 'no'}, {lifecycle: 'gone'}, {home: home}, {delivered_ref: 'x'}, {read_only: true},
  ].map(delta => reply([active(delta)]));
  bad.push(reply([pointer('moved', {home: {...home, extra: 'x'}})]), reply([pointer('moved', {home: {...home, file_path: 5}})]),
    reply([pointer('moved', {home: null})]), reply([pointer('moved', {status: 'in-progress'})]), reply([pointer('moved', {read_only: false})]),
    reply([pointer('moved', {delivered_ref: 'PLAN-1'})]), reply([pointer('delivered', {delivered_ref: null})]),
    reply([pointer('delivered', {delivered_ref: 7})]), reply([pointer('moved', {detail_path: '/elsewhere/x.md'})]));
  const wrongFile = {...home, file_path: '/work/shop/ideas/' + idea + '.md'};
  bad.push(reply([pointer('moved', {home: wrongFile, detail_path: wrongFile.file_path})]),
    reply([pointer('delivered', {home: wrongFile, detail_path: wrongFile.file_path})]));
  const wrongFolder = {...home, file_path: '/work/shop/notes/' + other + '.md'};
  bad.push(reply([pointer('moved', {home: wrongFolder, detail_path: wrongFolder.file_path})]));
  const wrongWorkspace = {...home, workspace_path: '/work/other'};
  bad.push(reply([pointer('moved', {home: wrongWorkspace})]), reply([pointer('delivered', {home: wrongWorkspace})]));
  for (const ref of ['', '   ', 'x'.repeat(501), 'two\nlines', 'tab\there', 'nul\u0000', 'bell\u0007', 'cr\rx', 'line\u2028sep'])
    bad.push(reply([pointer('delivered', {delivered_ref: ref})]));
  for (const body of bad) body.ideas[0].position = 1; // a single-row reply: position must not be what rejects it
  for (const body of bad) await assert.rejects(client(body).ideas(), error => error instanceof ApiError && error.code === 'invalid_response');
});

test('a pointer row accepts a trailing-slash workspace path and a 500-character one-line ref', async () => {
  const slash = {...home, workspace_path: '/work/shop/'};
  const moved = reply([pointer('moved', {home: slash, position: 1})]);
  assert.deepEqual(await client(moved).ideas(), moved);
  const delivered = reply([pointer('delivered', {delivered_ref: 'R'.repeat(500), position: 1})]);
  assert.deepEqual(await client(delivered).ideas(), delivered);
});

test('a reply with none of the four keys is still read as an active idea', async () => {
  const {lifecycle, home: h, delivered_ref, read_only, ...legacy} = active();
  assert.deepEqual(await client(reply([legacy])).ideas(), reply([legacy]));
  await assert.rejects(client(reply([{...legacy, read_only: false}])).ideas(), error => error.code === 'invalid_response');
});

test('idea_moved, not_moved and delivery_conflict arrive as typed errors carrying the home', async () => {
  for (const code of ['idea_moved', 'not_moved', 'delivery_conflict']) {
    const api = new IdeaApi(async () => ({ok: false, status: 409, json: async () => ({ok: false, code, home, write_state: 'not_applied'})}), 1000, binding);
    api.sessionId = session; api.csrf = 'csrf';
    await assert.rejects(api.ideas(), error => error instanceof ApiError && error.code === code && error.status === 409 && error.data.home.workspace_name === 'Shop Site');
  }
});
