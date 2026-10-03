// R2: the page side of the terminal conversation. Actual Flow + packaged step renders, fixed DOM/API fixtures.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web = new URL('../../glitch-idea/web/', import.meta.url);
const moduleUrl = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const foldsUrl = moduleUrl(await readFile(new URL('folds.js', web), 'utf8'));
const {Flow, STEPS} = await import(foldsUrl);
const load = async name => (await import(moduleUrl((await readFile(new URL('steps/' + name + '.js', web), 'utf8')).replace("'../folds.js'", JSON.stringify(foldsUrl))))).render;
const RENDER = {shape: await load('shape'), method: await load('method'), assess: await load('assess')};
const copy = value => structuredClone(value);
const IDEA = 'idea_' + '1'.repeat(32), OTHER = 'idea_' + '2'.repeat(32), THIRD = 'idea_' + '3'.repeat(32);
const SID = 'session_' + '4'.repeat(32), GEN = 'agent_' + '5'.repeat(32);
const CAPTURE = {raw_text: 'Fixture words', workspace: {name: 'Fixture', path: '/fixture', confirmed: true}};
const SHAPE = {outcome: 'Accepted outcome', scope: 'small-change', scope_reason: 'One change', alternatives: [{route: 'Keep it', reason: 'Simpler'}], assumptions: [], next_slice: 'Next', learning: []};
const METHOD = {selection: 'bounded-plan', reason: 'Accepted reason', investment: null, experiment: null, memory: {status: 'unavailable', sources: [], rationale: null}};
const rating = {urgency: 6, importance: 7, actor: 'Fixture', timestamp: 'fixture-only'};
const assessment = () => ({method: 'wsjf', version: 'v1', inputs: {value: 8, time_criticality: 4, enablement: 2, effort: 2}, basis: 'Fixture evidence', assumptions: [], confidence: 'low', provenance: 'Current agent'});
class Node {
  constructor(tag, text = '', className = '') {this.tag = tag; this.textContent = text; this.className = className; this.children = []; this.listeners = {}; this.disabled = false;}
  append(...children) {this.children.push(...children);}
  setAttribute(key, value) {this[key] = value;}
  addEventListener(key, fn) {this.listeners[key] = fn;}
  async click() {if (!this.disabled) return this.listeners.click?.({target: this});}
  input(value) {if (!this.disabled) {this.value = value; return this.listeners.input?.({target: this});}}
  all() {return [this, ...this.children.flatMap(child => child.all())];}
  set innerHTML(value) {throw new Error('HTML injection forbidden');}
}
const element = (tag, text = '', className = '') => new Node(tag, text, className);
const button = (text, action, className = '') => {const node = element('button', text, className); node.type = 'button'; node.addEventListener('click', action); return node;};
function harness(key = 'shape', {propose} = {}) {
  const prior = ['capture', 'priorities', ...(key === 'shape' ? [] : ['shape']), ...(key === 'assess' ? ['method', 'visualize'] : [])];
  const state = {ok: true, code: 'ok', session_id: SID, idea_id: IDEA, idea_status: 'active', revision: 4, draft_version: 0, backlog_revision: 9, current_step: key,
    steps: Object.fromEntries(STEPS.map(({key: step}) => [step, {status: prior.includes(step) ? 'saved' : step === key ? 'current' : 'todo', accepted_revision: prior.includes(step) ? 4 : null, evidence_id: prior.includes(step) ? 'e-' + step : null}])),
    accepted: {capture: copy(CAPTURE), priorities: {urgency: 6, importance: 7}, ...(key === 'shape' ? {} : {shape: copy(SHAPE)}), ...(key === 'assess' ? {method: copy(METHOD)} : {})},
    drafts: {}, draft: null, agent_status: 'connected', agent_generation: GEN, human_ratings: copy(rating), assessment_summary: null,
    backlog_status: {available: true, code: 'ok'}, backlog: {revision: 9, order: [IDEA, OTHER, THIRD], comparisons: [IDEA, OTHER, THIRD].map(idea_id => ({idea_id, revision: idea_id === IDEA ? 4 : 1, status: 'active', ratings: idea_id === IDEA ? copy(rating) : null, assessment: null}))},
    proposal_sources: {}, proposals: [], proposal_inventory: {total: 0, projected: 0, omitted: 0, content_omitted: 0, index_path: IDEA + '.md'}};
  const writes = [];
  const sources = () => {
    const connected = state.agent_status === 'connected', op = key === 'assess' ? 'assessment' : key;
    const data = key === 'assess' ? {steps: {capture: copy(CAPTURE), priorities: copy(state.accepted.priorities), shape: copy(SHAPE)}, backlog: copy(state.backlog), target: copy(state.drafts.assess ?? null)} :
      {capture: copy(CAPTURE), ...(key === 'method' ? {shape: copy(SHAPE)} : {}), ...(state.drafts[key] ? {[key]: copy(state.drafts[key])} : {})};
    state.proposal_sources[op] = connected ? {available: true, code: 'ok', source: {accepted_revision: state.revision, draft_version: state.draft_version, data, source_digest: 'a'.repeat(64)}} : {available: false, code: 'agent_unavailable', source: null};
  };
  sources();
  const api = {state: async () => copy(state), write: async (operation, payload) => {
    writes.push({operation, payload: copy(payload)});
    if (operation === 'propose') {
      if (propose) return propose(payload);
      return {ok: true, code: 'ok', status: 'pending', write_state: 'not_applied', request_id: payload.request_id, session_id: SID, idea_id: IDEA, operation: payload.operation,
        accepted_revision: payload.expected_revision, draft_version: payload.expected_draft_version, source_digest: payload.source_digest};
    }
    if (operation === 'draft') {state.draft_version++; state.drafts[payload.step] = copy(payload.fields);}
    sources(); return {ok: true, code: 'ok', write_state: 'applied', request_id: payload.request_id, idea_id: IDEA, revision: state.revision, draft_version: state.draft_version, backlog_revision: 9};
  }};
  const flow = new Flow(api, () => 'request-' + (++writes.counter), () => 0); writes.counter = 0; flow.load(state);
  let body, foot, connected = true;
  const draw = () => {
    body = element('div'); foot = element('footer');
    const field = (target, label, id, value, changed, textarea = false) => {
      const wrap = element('div'), heading = element('label', label), input = element(textarea ? 'textarea' : 'input');
      heading.htmlFor = id; input.id = id; input.value = value ?? ''; input.disabled = flow.busy || !connected;
      input.addEventListener('input', event => changed(event.target.value)); wrap.append(heading, input); target.append(wrap); return input;
    };
    RENDER[key]({body, foot, flow, element, button, field, connected, edited: (step, value) => flow.edit(step, value), handle: action => async () => action(), proposalInventory: () => {}});
  };
  flow.onChange = draw; draw();
  // By default the conversation answers a request this page sent (as autoConverse would).
  const talk = (fills, extra = {}, own = true) => {if (own && !flow.fillApplied.has(extra.request_id ?? 'request-1')) flow.rememberFill(extra.request_id ?? 'request-1', 0); state.conversation = {request_id: 'request-1', operation: key === 'assess' ? 'assessment' : key, idea_id: IDEA, accepted_revision: state.revision, fills: fills.map((fields, index) => ({sequence: index + 1, fields})), ...extra}; };
  return {flow, state, writes, draw, sources, talk, get: id => [...body.all(), ...foot.all()].find(node => node.id === id), get body() {return body;}, connect: value => {connected = value; draw();},
    proposeWrites: () => writes.filter(item => item.operation === 'propose'), sync: async () => {flow.load(copy(state), true); await flow.fillSave;}};
}
const REFUSED = Object.assign(new Error('agent_unavailable'), {code: 'agent_unavailable', status: 409});

test('autoConverse sends exactly one propose on step entry, none on redraw or a second call', async () => {
  for (const key of ['shape', 'method', 'assess']) {
    const h = harness(key);
    assert.equal(await h.flow.autoConverse(key), true); assert.equal(h.proposeWrites().length, 1);
    assert.equal(h.proposeWrites()[0].payload.operation, key === 'assess' ? 'assessment' : key);
    h.draw(); await h.flow.autoConverse(key); assert.equal(h.proposeWrites().length, 1);
  }
});
test('autoConverse does nothing for other steps, no agent, a saved step or an adopted conversation', async () => {
  const h = harness('shape'); await h.flow.autoConverse('capture'); await h.flow.autoConverse('visualize'); assert.equal(h.proposeWrites().length, 0);
  const off = harness('shape'); off.state.agent_status = 'disconnected'; off.state.agent_generation = null; off.sources(); off.flow.load(copy(off.state), true);
  await off.flow.autoConverse('shape'); assert.equal(off.proposeWrites().length, 0);
  const saved = harness('shape'); saved.state.steps.shape = {status: 'saved', accepted_revision: 4, evidence_id: 'e'}; saved.state.accepted.shape = copy(SHAPE); saved.flow.load(copy(saved.state), true);
  await saved.flow.autoConverse('shape'); assert.equal(saved.proposeWrites().length, 0);
  const open = harness('shape'); open.talk([{outcome: 'From terminal'}]); await open.sync(); await open.flow.autoConverse('shape'); assert.equal(open.proposeWrites().length, 0);
});
test('a refused automatic request is not retried automatically', async () => {
  const h = harness('shape', {propose: async () => {throw REFUSED;}});
  assert.equal(await h.flow.autoConverse('shape'), false); assert.equal(h.proposeWrites().length, 1);
  assert.equal(h.flow.proposalPending, null);
  await h.flow.autoConverse('shape'); await h.flow.autoConverse('shape'); assert.equal(h.proposeWrites().length, 1);
});
test('fills apply once and in order to the named Shape fields only', async () => {
  const h = harness('shape');
  h.talk([{outcome: 'Terminal outcome'}, {alternatives: [{route: 'Route A', reason: 'Because A'}], assumptions: ['Risk one']}]); await h.sync();
  assert.equal(h.flow.buffers.shape.outcome, 'Terminal outcome'); assert.deepEqual(h.flow.buffers.shape.alternatives, [{route: 'Route A', reason: 'Because A'}]);
  assert.deepEqual(h.flow.buffers.shape.assumptions, ['Risk one']); assert.equal(h.flow.buffers.shape.next_slice, ''); assert.equal(h.flow.buffers.shape.scope, null);
  assert.deepEqual([...h.flow.filled.shape].sort(), ['alternatives', 'assumptions', 'outcome']);
  assert.equal(h.get('shape-outcome').value, 'Terminal outcome');
  assert.equal(h.writes.filter(w => w.operation === 'draft').length, 1); assert.equal(h.state.drafts.shape.outcome, 'Terminal outcome');
  // The human edits, the same state arrives again: the fill is not applied a second time.
  h.get('shape-outcome').input('Human outcome'); await h.sync(); await h.sync();
  assert.equal(h.flow.buffers.shape.outcome, 'Human outcome');
  // A new fill (sequence 3) applies after the earlier ones.
  h.talk([{outcome: 'Terminal outcome'}, {alternatives: [{route: 'Route A', reason: 'Because A'}], assumptions: ['Risk one']}, {next_slice: 'Terminal slice'}]); await h.sync();
  assert.equal(h.flow.buffers.shape.next_slice, 'Terminal slice'); assert.equal(h.flow.buffers.shape.outcome, 'Human outcome');
  // Never accepted by the page on its own.
  assert.equal(h.writes.filter(w => w.operation === 'accept').length, 0);
});
test('Method fill sets the reason only; the human keeps the method choice', async () => {
  const h = harness('method'); h.talk([{reason: 'Terminal reason'}]); await h.sync();
  assert.equal(h.flow.buffers.method.reason, 'Terminal reason'); assert.equal(h.flow.buffers.method.selection, null);
  assert.equal(h.get('method-reason').value, 'Terminal reason'); assert.ok(h.get('method-reason-filled'));
});
test('Assess fill sets assessment and the AI proposed position, and actual while the human has not chosen another', async () => {
  const h = harness('assess'); h.talk([{assessment: assessment(), proposed_position: 2}]); await h.sync();
  assert.equal(h.flow.buffers.assess.assessment.version, 'v1'); assert.equal(h.flow.buffers.assess.position.proposed_position, 2);
  assert.equal(h.flow.buffers.assess.position.actual_position, 2); assert.deepEqual(h.flow.buffers.assess.position.neighbors, {before: OTHER, after: THIRD});
  const own = harness('assess'); own.flow.edit('assess', {...own.flow.buffers.assess, position: {...own.flow.buffers.assess.position, proposed_position: 1, actual_position: 3, neighbors: {before: THIRD === 'x' ? null : OTHER, after: null}, override_reason: 'Mine'}});
  own.talk([{assessment: assessment(), proposed_position: 2}]); await own.sync();
  assert.equal(own.flow.buffers.assess.position.proposed_position, 2); assert.equal(own.flow.buffers.assess.position.actual_position, 3);
  assert.equal(own.flow.buffers.assess.position.override_reason, 'Mine');
});
test('a fill is never applied to a saved step, and a conversation for another revision is refused', async () => {
  const h = harness('shape'); h.state.steps.shape = {status: 'saved', accepted_revision: 4, evidence_id: 'e'}; h.state.accepted.shape = copy(SHAPE);
  h.talk([{outcome: 'Late fill'}]); await h.sync(); assert.equal(h.flow.buffers.shape.outcome, 'Accepted outcome'); assert.equal(h.flow.filled.shape?.size ?? 0, 0);
  const stale = harness('shape'); stale.talk([{outcome: 'Wrong revision'}], {accepted_revision: 3});
  assert.throws(() => stale.flow.load(copy(stale.state), true)); assert.equal(stale.flow.buffers.shape.outcome, '');
  const other = harness('shape'); other.talk([{outcome: 'Wrong idea'}], {idea_id: OTHER});
  assert.throws(() => other.flow.load(copy(other.state), true)); assert.equal(other.flow.buffers.shape.outcome, '');
});
test('filled markers show under the field and disappear when the human edits it', async () => {
  const h = harness('shape'); assert.equal(h.get('shape-outcome-filled'), undefined);
  h.talk([{outcome: 'Terminal outcome', next_slice: 'Terminal slice'}]); await h.sync();
  const note = h.get('shape-outcome-filled'); assert.equal(note.textContent, 'From your terminal conversation'); assert.equal(note.className, 'filled-note');
  assert.ok(h.get('shape-next-slice-filled'));
  h.get('shape-outcome').input('Mine'); assert.equal(h.get('shape-outcome-filled'), undefined); assert.ok(h.get('shape-next-slice-filled'));
});
test('each step shows a conversation status line in plain words', async () => {
  for (const key of ['shape', 'method', 'assess']) {
    const h = harness(key), line = () => h.get(key + '-conversation-status');
    assert.equal(line().role, 'status'); assert.equal(line().textContent, 'Your terminal is guiding this step. Answer there; agreed answers appear here as you go.');
    await h.flow.autoConverse(key); assert.equal(line().textContent, 'Asking your terminal to start this step…');
    h.flow.cancelProposal('user_cancelled'); h.flow.proposalError = null; h.flow.onChange();
    h.talk(key === 'shape' ? [{outcome: 'A'}, {next_slice: 'B'}] : key === 'method' ? [{reason: 'A'}] : [{assessment: assessment(), proposed_position: 1}]); await h.sync();
    assert.match(line().textContent, new RegExp('^Your terminal is guiding this step\\. Answer there; agreed answers appear here as you go\\. \\d answers? filled from your terminal\\.$'));
    const off = harness(key); off.state.agent_status = 'disconnected'; off.state.agent_generation = null; off.sources(); off.flow.load(copy(off.state), true);
    assert.equal(off.get(key + '-conversation-status').textContent, 'No agent is connected. You can fill this step by hand, or reinvoke /glitch-idea in your terminal to be guided.');
    assert.ok(!off.body.all().some(node => /initiating agent is unavailable|AI assistance is unavailable/.test(node.textContent)));
  }
});
test('no request buttons exist on the conversation steps', () => {
  for (const key of ['shape', 'method', 'assess']) assert.equal(harness(key).get(key + '-request'), undefined);
});
test('Assess shows the AI proposed position read-only once filled, and an actual position the human decides', async () => {
  const h = harness('assess'); assert.ok(h.get('assess-proposed-position-input'));
  assert.ok(h.body.all().some(node => node.textContent === 'AI proposed position'));
  h.talk([{assessment: assessment(), proposed_position: 2}]); await h.sync();
  assert.equal(h.get('assess-proposed-position').textContent, 'AI proposed position: 2'); assert.equal(h.get('assess-proposed-position-input'), undefined);
  assert.equal(h.get('assess-actual-position').value, '2'); assert.ok(h.body.all().some(node => node.textContent === 'Actual position (you decide)'));
  assert.ok(h.get('assess-proposed-position-filled')); assert.ok(h.get('assess-assessment-filled'));
  h.get('assess-actual-position').input('3'); assert.equal(h.flow.buffers.assess.position.actual_position, 3); assert.equal(h.flow.buffers.assess.position.proposed_position, 2);
});
test('an open conversation for the pending request ends the wait and survives the human editing the draft', async () => {
  const h = harness('shape'); await h.flow.autoConverse('shape'); assert.ok(h.flow.proposalPending);
  h.get('shape-outcome').input('Human typing'); h.state.draft_version = 5; h.sources();
  h.talk([{outcome: 'Terminal outcome'}], {request_id: h.proposeWrites()[0].payload.request_id}); await h.sync();
  assert.equal(h.flow.proposalPending, null); assert.equal(h.flow.proposalError, null);
  assert.equal(h.get('shape-proposal-error'), undefined);
});

test('typing while the automatic request is in flight is kept and saved, never dropped', async () => {
  // R2 known edge (builder-measured in a real browser): the first typed field came back empty.
  let release;
  const gate = new Promise(resolve => {release = resolve;});
  const h = harness('shape', {propose: async payload => {await gate;
    return {ok: true, code: 'ok', status: 'pending', write_state: 'not_applied', request_id: payload.request_id, session_id: SID, idea_id: IDEA,
      operation: payload.operation, accepted_revision: payload.expected_revision, draft_version: payload.expected_draft_version, source_digest: payload.source_digest};}});
  const flight = h.flow.autoConverse('shape');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.flow.busy, true, 'the request is in flight');
  assert.equal(h.flow.inputLocked(), false, 'and the form is not locked');
  h.flow.edit('shape', {...h.flow.buffers.shape, outcome: 'Typed during the request'});
  assert.equal(h.flow.buffers.shape.outcome, 'Typed during the request');
  release(); await flight;
  assert.ok(h.flow.dirty.has('shape'), 'kept as an unsaved edit');
  assert.equal(await h.flow.save('shape', true), true);
  assert.equal(h.writes.filter(item => item.operation === 'draft').at(-1).payload.fields.outcome, 'Typed during the request');
});

test('a reload keeps applied fills applied; a conversation this tab never sent is adopted, not replayed', async () => {
  // Review ui-r1 blocker: an in-memory record reset on reload replayed old fills over later edits.
  const memory = new Map(), storage = {getItem: k => memory.get(k) ?? null, setItem: (k, v) => memory.set(k, v), removeItem: k => memory.delete(k)};
  const h = harness('shape');
  h.flow.fillStorage = storage; h.flow.rememberFill('request-1', 0);
  h.talk([{outcome: 'From terminal'}]); await h.sync();
  assert.equal(h.flow.buffers.shape.outcome, 'From terminal');
  h.flow.edit('shape', {...h.flow.buffers.shape, outcome: 'My later edit'}); await h.flow.save('shape', true);
  // A new page in the same tab: same storage, fresh memory.
  const reloaded = new Flow(h.flow.api, () => 'request-x', () => 0, undefined, storage);
  reloaded.load(copy(h.state));
  assert.equal(reloaded.buffers.shape.outcome, 'My later edit', 'the old fill is not replayed');
  // A tab that never saw the request adopts what is there without applying it.
  const other = new Flow(h.flow.api, () => 'request-y', () => 0, undefined, {getItem: () => null, setItem() {}, removeItem() {}});
  other.load(copy(h.state));
  assert.equal(other.buffers.shape.outcome, 'My later edit');
});

test('a fill can only touch the fields a conversation may fill, and never the human-chosen actual position', async () => {
  const method = harness('method');
  method.flow.edit('method', {...method.flow.buffers.method, selection: 'adaptive-slices'});
  // The server and the page validator refuse such a conversation; drive applyFills directly to prove the
  // page still drops a human-owned field (Review ui-r3: a validator refusal alone proved nothing here).
  method.flow.rememberFill('request-1', 0);
  method.flow.state = {...method.flow.state, conversation: {request_id: 'request-1', operation: 'method', idea_id: IDEA,
    accepted_revision: method.flow.state.revision, fills: [{sequence: 1, fields: {reason: 'Agreed reason', selection: 'bounded-plan'}}, {sequence: 2, fields: {selection: 'experiment-led'}}]}};
  const saves = method.writes.filter(item => item.operation === 'draft').length;
  assert.equal(method.flow.applyFills(), true);
  assert.equal(method.flow.buffers.method.reason, 'Agreed reason', 'the allowed field applies');
  assert.equal(method.flow.buffers.method.selection, 'adaptive-slices', 'the human-owned field is dropped');
  await method.flow.fillSave;
  assert.equal(method.writes.filter(item => item.operation === 'draft').length, saves + 1, 'one save for the applied fills');
  const assess = harness('assess');
  assess.talk([{proposed_position: 2}]); await assess.sync();
  assert.equal(assess.flow.buffers.assess.position.actual_position, 2);
  const memory = new Map(); assess.flow.fillStorage = {getItem: k => memory.get(k) ?? null, setItem: (k, v) => memory.set(k, v), removeItem: k => memory.delete(k)};
  assess.flow.rememberFill('request-1', 1);
  assess.get('assess-actual-position').input('2');   // the human sets actual through the page (to the same value)
  // A reload in this tab: fresh Flow, same tab storage.
  const reloaded = new Flow(assess.flow.api, () => 'request-z', () => 0, undefined, assess.flow.fillStorage);
  reloaded.load(copy(assess.state)); assess.flow = reloaded;
  assess.talk([{proposed_position: 2}, {proposed_position: 3}]); await assess.flow.load(copy(assess.state), true); await assess.flow.fillSave;
  assert.equal(assess.flow.buffers.assess.position.proposed_position, 3);
  assert.equal(assess.flow.buffers.assess.position.actual_position, 2, 'the human choice stays');
});

test('a fill whose draft save fails is applied again after a reload; one that arrives mid-write is saved after it', async () => {
  // Review ui-r3: the durable cursor advanced before the save, and a fill applied while busy was never saved.
  const memory = new Map(), storage = {getItem: k => memory.get(k) ?? null, setItem: (k, v) => memory.set(k, v), removeItem: k => memory.delete(k)};
  const h = harness('shape');
  h.flow.fillStorage = storage; h.flow.rememberFill('request-1', 0);
  const realWrite = h.flow.api.write;
  h.flow.api.write = async (operation, payload) => {
    if (operation === 'draft') throw Object.assign(new Error('connection_lost'), {code: 'connection_lost', status: 0});
    return realWrite(operation, payload);
  };
  h.talk([{outcome: 'Agreed but not saved'}]); await h.sync();
  assert.notEqual(JSON.parse(memory.get('glitch-idea-fills'))['request-1'], 1, 'not recorded as applied while unsaved');
  const reloaded = new Flow(h.flow.api, () => 'request-r', () => 0, undefined, storage);
  h.flow.api.write = realWrite; reloaded.load(copy(h.state)); await reloaded.fillSave;
  assert.equal(reloaded.buffers.shape.outcome, 'Agreed but not saved', 'applied again after the reload');
  // Mid-write: the page is busy when the next fill arrives.
  const g = harness('shape'); g.flow.rememberFill('request-1', 0);
  g.flow.busy = true;
  g.talk([{outcome: 'During another write'}]); g.flow.load(copy(g.state), true);
  const before = g.writes.filter(item => item.operation === 'draft').length;
  g.flow.busy = false; await g.flow.fillSave;
  assert.equal(g.writes.filter(item => item.operation === 'draft').length, before + 1, 'saved once the write finished');
  assert.equal(g.writes.filter(item => item.operation === 'draft').at(-1).payload.fields.outcome, 'During another write');
});

// Review ui-r4: real in-flight writes (a held draft write), not a boolean stand-in.
function holdDrafts(h) {
  const real = h.flow.api.write, held = [];
  h.flow.api.write = (operation, payload) => operation !== 'draft' ? real(operation, payload) :
    new Promise(resolve => held.push(() => resolve(real(operation, payload))));
  return {release: async () => { while (held.length) { held.shift()(); await new Promise(r => setTimeout(r, 0)); } }, held, restore: () => { h.flow.api.write = real; }};
}

test('typing during a draft save never clears the write in flight, and an undo during it stays unsaved', async () => {
  const h = harness('shape');
  h.flow.edit('shape', {...h.flow.buffers.shape, outcome: 'A'}); assert.equal(await h.flow.save('shape', true), true);
  const hold2 = holdDrafts(h);
  h.flow.edit('shape', {...h.flow.buffers.shape, outcome: 'B'});
  const saving = h.flow.save('shape', true);
  await new Promise(r => setTimeout(r, 0));
  assert.ok(h.flow.pending, 'a draft write is in flight');
  h.flow.edit('shape', {...h.flow.buffers.shape, outcome: 'A'});   // the human undoes to the saved text during the write
  assert.ok(h.flow.pending, 'the in-flight write is never cleared by typing');
  assert.ok(h.flow.dirty.has('shape'));
  await hold2.release(); await saving;
  assert.equal(h.flow.buffers.shape.outcome, 'A', 'the undo survives the reload after B was saved');
  assert.ok(h.flow.dirty.has('shape'), 'and stays unsaved, so it will be saved');
});

test('a fill applied during a real in-flight save is saved after it, applied once, and its cursor never moves back', async () => {
  const memory = new Map(), storage = {getItem: k => memory.get(k) ?? null, setItem: (k, v) => memory.set(k, v), removeItem: k => memory.delete(k)};
  const h = harness('shape'); h.flow.fillStorage = storage; h.flow.rememberFill('request-1', 0);
  const hold = holdDrafts(h);
  h.talk([{outcome: 'Fill one'}]); h.flow.load(copy(h.state), true);   // fill 1 applied, its save is held
  await new Promise(r => setTimeout(r, 0));
  h.talk([{outcome: 'Fill one'}, {next_slice: 'Fill two'}]); h.flow.load(copy(h.state), true);   // fill 2 lands mid-write
  await hold.release(); await new Promise(r => setTimeout(r, 250)); await hold.release(); await h.flow.fillSave;
  const drafts = h.writes.filter(item => item.operation === 'draft');
  assert.equal(drafts.at(-1).payload.fields.next_slice, 'Fill two', 'fill two was saved after the first write');
  assert.equal(JSON.parse(memory.get('glitch-idea-fills'))['request-1'], 2, 'durable cursor at 2, never rolled back to 1');
  h.flow.edit('shape', {...h.flow.buffers.shape, next_slice: 'My edit'});
  h.flow.load(copy(h.state), true);
  assert.equal(h.flow.buffers.shape.next_slice, 'My edit', 'fill two is never applied again over the edit');
});

test('a deferred fill save never saves another idea, and Assess fields stay editable during a draft save', async () => {
  const h = harness('shape'); h.flow.rememberFill('request-1', 0);
  h.flow.busy = true; h.talk([{outcome: 'For idea one'}]); h.flow.load(copy(h.state), true);
  h.flow.state = {...h.flow.state, idea_id: OTHER};   // the page moved to another idea before the wait ended
  h.flow.busy = false;
  assert.equal(await h.flow.fillSave, false);
  assert.equal(h.writes.filter(item => item.operation === 'draft').length, 0);
  const a = harness('assess'); await a.get('assess-method-wsjf').click(); const hold = holdDrafts(a);
  const saving = a.flow.save('assess', true);
  await new Promise(r => setTimeout(r, 0)); a.draw();
  const input = a.get('assess-input-value');
  assert.equal(input.disabled, false, 'Assess inputs are not locked by a draft save');
  await hold.release(); await saving;
});

test('a fill identical to what is already saved still records its cursor, so a reload never replays it', async () => {
  // Review final review: the save it waited for made the buffer clean, and the cursor was never persisted.
  const memory = new Map(), storage = {getItem: k => memory.get(k) ?? null, setItem: (k, v) => memory.set(k, v), removeItem: k => memory.delete(k)};
  const h = harness('shape'); h.flow.fillStorage = storage; h.flow.rememberFill('request-1', 0);
  h.flow.edit('shape', {...h.flow.buffers.shape, outcome: 'Same words'}); assert.equal(await h.flow.save('shape', true), true);
  h.flow.busy = true; h.talk([{outcome: 'Same words'}]); h.flow.load(copy(h.state), true); h.flow.busy = false;
  await h.flow.fillSave;
  assert.equal(JSON.parse(memory.get('glitch-idea-fills'))['request-1'], 1);
});

test('a cursor is recorded only when the authoritative saved draft holds the fill', async () => {
  // Review confirmation: another tab changed the draft before the reread; the fill must not be marked applied.
  const memory = new Map(), storage = {getItem: k => memory.get(k) ?? null, setItem: (k, v) => memory.set(k, v), removeItem: k => memory.delete(k)};
  const h = harness('shape'); h.flow.fillStorage = storage; h.flow.rememberFill('request-1', 0);
  h.flow.busy = true;   // a write is in flight when the fill lands, so its save waits
  h.talk([{outcome: 'From the terminal'}]); h.flow.load(copy(h.state), true);
  h.state.drafts.shape = {...h.flow.buffers.shape, outcome: 'Another tab wrote this'};   // the authority moved on
  h.flow.dirty.delete('shape'); h.flow.state = {...h.flow.state, drafts: copy(h.state.drafts)};
  h.flow.busy = false; await h.flow.fillSave;
  assert.notEqual(JSON.parse(memory.get('glitch-idea-fills') ?? '{}')['request-1'], 1);
});
