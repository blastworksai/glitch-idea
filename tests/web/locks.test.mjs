// Terminal-guided field locks and hand release: Flow + the real IdeaApi.release over a fake fetch.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web = new URL('../../glitch-idea/web/', import.meta.url);
const moduleFrom = async name => import('data:text/javascript;base64,' + Buffer.from(await readFile(new URL(name, web), 'utf8')).toString('base64'));
const {Flow, STEPS, FILL_KEYS} = await moduleFrom('folds.js');
const {IdeaApi} = await moduleFrom('api.js');
const IDEA = 'idea_' + '1'.repeat(32), SID = 'session_' + '2'.repeat(32), BINDING = 'binding_' + '6'.repeat(32), GEN = 'agent_' + '3'.repeat(32);
const copy = value => structuredClone(value);
const CAPTURE = {raw_text: 'A fixture idea', workspace: {name: 'fixture', path: '/fixture', confirmed: true}};

function state({agent = 'connected', current = 'discovery', hand, conversation, saved = []} = {}) {
  const done = new Set(['capture', 'priorities', 'method', ...saved]);
  const value = {ok: true, code: 'ok', session_id: SID, idea_id: IDEA, idea_status: 'active', revision: 3, draft_version: 0, backlog_revision: 0,
    current_step: current, agent_status: agent, agent_generation: agent === 'connected' ? GEN : null,
    steps: Object.fromEntries(STEPS.map(({key}) => [key, done.has(key) ? {status: 'saved', accepted_revision: 2, evidence_id: key + '-e'} :
      {status: key === current ? 'current' : 'todo', accepted_revision: null, evidence_id: null}])),
    accepted: Object.fromEntries(STEPS.map(({key}) => [key, key === 'capture' ? copy(CAPTURE) : null])),
    drafts: {}, draft: null, proposal_sources: {}, proposals: [],
    proposal_inventory: {total: 0, projected: 0, omitted: 0, content_omitted: 0, index_path: IDEA + '.md'}};
  if (hand) value.hand = hand;
  if (conversation) value.conversation = conversation;
  return value;
}
function harness(initial = state()) {
  const writes = [], calls = [];
  const real = new IdeaApi(async (url, options) => {
    calls.push({url, options});
    const step = JSON.parse(options.body).step;
    return {status: 200, ok: true, json: async () => ({ok: true, code: 'ok', idea_id: IDEA, step, hand: true, released: 1, write_state: 'applied', revision: 3, draft_version: 0})};
  }, 1000, BINDING);
  real.csrf = 'test-only-csrf'; real.sessionId = SID;
  const api = {bindingId: BINDING, state: async () => copy(initial), write: async (operation, payload) => { writes.push({operation, payload: copy(payload)}); throw Object.assign(new Error('no'), {code: 'x'}); },
    release: step => real.release(step)};
  const flow = new Flow(api, () => 'request-1');
  flow.load(initial);
  return {flow, api, real, writes, calls};
}
const conversation = (operation, fills) => ({request_id: 'conv-1', operation, idea_id: IDEA, accepted_revision: 3, fills});

test('locked only for discovery and exploration while an agent is connected', () => {
  const {flow} = harness();
  // The prior-art section has one lock, on its rows; the box and the "where did you look" text open with it.
  const GROUP_FOLLOWERS = ['prior_art_none', 'prior_art_searched'];
  for (const name of FILL_KEYS.discovery.filter(n => !GROUP_FOLLOWERS.includes(n))) assert.equal(flow.fieldLocked('discovery', name), true, name);
  for (const name of GROUP_FOLLOWERS) assert.equal(flow.fieldLocked('discovery', name), false, name);
  for (const name of FILL_KEYS.exploration.filter(n => n !== 'investment' && n !== 'experiment')) assert.equal(flow.fieldLocked('exploration', name), true, name);
  assert.equal(flow.fieldLocked('discovery', 'not-a-field'), false);
});

test('method, visualize, assess, capture and priorities are never locked', () => {
  const {flow} = harness();
  for (const [key, names] of [['method', ['selection', 'reason', 'memory']], ['visualize', ['disposition', 'reason']],
    ['assess', ['assessment', 'position']], ['capture', ['raw_text']], ['priorities', ['urgency']], ['review', ['x']]]) {
    for (const name of names) assert.equal(flow.fieldLocked(key, name), false, key + '.' + name);
  }
});

test('with no agent connected nothing is locked', () => {
  const {flow} = harness(state({agent: 'disconnected'}));
  assert.equal(flow.fieldLocked('discovery', 'problem'), false);
  assert.equal(flow.fieldLocked('exploration', 'outcome'), false);
  const paused = harness(state({agent: 'paused'}));
  assert.equal(paused.flow.fieldLocked('discovery', 'problem'), false);
});

test('a terminal fill unlocks that field only, and a human edit does not lock it again', () => {
  const {flow} = harness();
  flow.load(state({conversation: conversation('discovery', [])}));
  flow.load(state({conversation: conversation('discovery', [{sequence: 1, fields: {problem: 'Lids leak'}}])}), true);
  assert.equal(flow.buffers.discovery.problem, 'Lids leak');
  assert.equal(flow.fieldLocked('discovery', 'problem'), false);
  assert.equal(flow.fieldLocked('discovery', 'audience'), true);
  flow.edit('discovery', {...flow.buffers.discovery, problem: ''});
  assert.equal(flow.fieldLocked('discovery', 'problem'), false);
});

test('a field that already holds an answer is not locked after a reload', () => {
  const initial = state(); initial.drafts.discovery = {problem: 'Saved earlier', audience: '', workaround: '', evidence: '', kill_criteria: '', challenges: []};
  const {flow} = harness(initial);
  assert.equal(flow.fieldLocked('discovery', 'problem'), false);
  assert.equal(flow.fieldLocked('discovery', 'audience'), true);
});

test('hand release and a saved step unlock every field', () => {
  assert.equal(harness(state({hand: {discovery: true, exploration: false}})).flow.fieldLocked('discovery', 'problem'), false);
  const held = harness(state({hand: {discovery: true, exploration: false}})).flow;
  assert.equal(held.fieldLocked('exploration', 'outcome'), true);
  assert.equal(held.handReleased('discovery'), true);
  assert.equal(held.handReleased('exploration'), false);
  assert.equal(harness(state({saved: ['discovery']})).flow.fieldLocked('discovery', 'problem'), false);
});

test('a malformed hand projection is refused; an absent one reads as not released', () => {
  const {flow} = harness();
  assert.equal(flow.handReleased('discovery'), false);
  for (const bad of [{discovery: true}, {discovery: 1, exploration: false}, {discovery: true, exploration: false, method: true}, []]) {
    assert.throws(() => flow.load(state({hand: bad})));
  }
});

test('releaseStep posts {step} with CSRF through the real api and unlocks at once', async () => {
  const {flow, calls} = harness();
  assert.equal(flow.fieldLocked('discovery', 'problem'), true);
  assert.equal(await flow.releaseStep('discovery'), true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/v1/conversation/release');
  assert.equal(calls[0].options.method, 'POST');
  assert.equal(calls[0].options.headers['X-CSRF-Token'], 'test-only-csrf');
  assert.deepEqual(JSON.parse(calls[0].options.body), {step: 'discovery'});
  assert.equal(flow.fieldLocked('discovery', 'problem'), false);
  assert.equal(flow.fieldLocked('exploration', 'outcome'), true);
  assert.equal(flow.state.agent_status, 'connected');
  // After a reload the server projects hand and the step stays unlocked.
  flow.load(state({hand: {discovery: true, exploration: false}}));
  assert.equal(flow.fieldLocked('discovery', 'problem'), false);
});

test('releaseStep is refused for every other step without a request', async () => {
  const {flow, calls} = harness();
  for (const key of ['method', 'assess', 'visualize', 'capture', 'priorities', 'review', 'nope']) assert.equal(await flow.releaseStep(key), false);
  assert.equal(calls.length, 0);
  await assert.rejects(new IdeaApi(async () => { throw new Error('no request'); }, 1000, BINDING).release('method'), error => error.code === 'invalid_input');
});

test('a refused release keeps the step locked and surfaces the typed code', async () => {
  const {flow, api} = harness();
  api.release = async () => { throw Object.assign(new Error('stale'), {code: 'agent_unavailable', status: 409}); };
  assert.equal(await flow.releaseStep('discovery'), false);
  assert.equal(flow.error.code, 'agent_unavailable');
  assert.match(flow.message, /agent_unavailable/);
  assert.equal(flow.fieldLocked('discovery', 'problem'), true);
});

test('a malformed release reply is invalid_response, uncertain', async () => {
  const {real} = harness();
  real.fetcher = async () => ({status: 200, ok: true, json: async () => ({ok: true, code: 'ok', idea_id: IDEA, step: 'exploration', hand: true, released: 0, write_state: 'applied', revision: 3, draft_version: 0})});
  await assert.rejects(real.release('discovery'), error => error.code === 'invalid_response' && error.uncertain === true);
});

test('no automatic request while a step is by hand, and releasing clears its waiting request', async () => {
  const held = harness(state({hand: {discovery: true, exploration: false}}));
  assert.equal(held.flow.canPropose('discovery'), false);
  assert.equal(await held.flow.autoConverse('discovery'), false);
  assert.equal(held.writes.length, 0);
  // Releasing mid-flight clears the waiting request for that step only.
  const {flow} = harness();
  flow.proposalPending = {key: 'discovery', phase: 'waiting'};
  await flow.releaseStep('discovery');
  assert.equal(flow.proposalPending, null);
  assert.equal(await flow.autoConverse('discovery'), false);
});

test('the route the page posts to is the one the bridge serves', async () => {
  const bridge = await readFile(new URL('../../glitch-idea/scripts/idea_bridge.py', import.meta.url), 'utf8');
  const {calls, flow} = harness();
  await flow.releaseStep('discovery');
  assert.ok(bridge.includes("'" + calls[0].url + "'"), 'bridge does not serve ' + calls[0].url);
});
