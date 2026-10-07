// R2: the page side of the terminal conversation. Actual Flow + packaged step renders, fixed DOM/API fixtures.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web = new URL('../../glitch-idea/web/', import.meta.url);
const moduleUrl = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const foldsUrl = moduleUrl(await readFile(new URL('folds.js', web), 'utf8'));
const {Flow, STEPS} = await import(foldsUrl);
const load = async name => (await import(moduleUrl((await readFile(new URL('steps/' + name + '.js', web), 'utf8')).replace("'../folds.js'", JSON.stringify(foldsUrl))))).render;
const RENDER = {discovery: await load('discovery'), exploration: await load('exploration'), method: await load('method'), assess: await load('assess')};
const copy = value => structuredClone(value);
const IDEA = 'idea_' + '1'.repeat(32), OTHER = 'idea_' + '2'.repeat(32), THIRD = 'idea_' + '3'.repeat(32);
const SID = 'session_' + '4'.repeat(32), GEN = 'agent_' + '5'.repeat(32);
const CAPTURE = {raw_text: 'Fixture words', workspace: {name: 'Fixture', path: '/fixture', confirmed: true}};
const DISC = {problem: 'Accepted problem', audience: 'Accepted audience', workaround: 'Accepted workaround', evidence: 'Accepted evidence', kill_criteria: 'Accepted stop rule', challenges: [{challenge: 'Accepted challenge', response: 'Accepted response'}]};
const EXPL = {outcome: 'Accepted outcome', alternatives: [{route: 'Keep it', reason: 'Simpler'}], assumptions: [], scope: 'small-change', scope_reason: 'One change', next_slice: 'Next', learning: [],
  investment: null, experiment: null, sketch: [{title: 'First item', why_next: 'Cheapest', done_when: 'It works', method: null}]};
const METHOD = {selection: 'bounded-plan', reason: 'Accepted reason', memory: {status: 'unavailable', sources: [], rationale: null, preferred_method: null}};
// The two terminal-guided steps share one set of flows; each row names its own fields, fills and untouched defaults.
const F = {
  discovery: {name: 'problem', id: 'discovery-problem', accepted: DISC.problem, third: 'workaround',
    fills: [{problem: 'Terminal problem'}, {challenges: [{challenge: 'Why now?', response: 'Deadline'}], audience: 'Terminal audience'}, {workaround: 'Terminal workaround'}],
    untouched: [['evidence', ''], ['kill_criteria', '']], marks: [['problem', 'discovery-problem'], ['audience', 'discovery-audience']], two: ['audience', 'challenges', 'problem']},
  exploration: {name: 'outcome', id: 'exploration-outcome', accepted: EXPL.outcome, third: 'next_slice',
    fills: [{outcome: 'Terminal outcome'}, {alternatives: [{route: 'Route A', reason: 'Because A'}], assumptions: ['Risk one']}, {next_slice: 'Terminal slice'}],
    untouched: [['next_slice', ''], ['scope', null]], marks: [['outcome', 'exploration-outcome'], ['next_slice', 'exploration-next-slice']], two: ['alternatives', 'assumptions', 'outcome']},
};
const GUIDED = ['discovery', 'exploration'];
const ACCEPTED = {method: METHOD, discovery: DISC, exploration: EXPL};
const ORDER = ['capture', 'priorities', 'method', 'discovery', 'exploration', 'visualize', 'assess'];
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
function harness(key = 'exploration', {propose} = {}) {
  const prior = ORDER.slice(0, ORDER.indexOf(key));
  const state = {ok: true, code: 'ok', session_id: SID, idea_id: IDEA, idea_status: 'active', revision: 4, draft_version: 0, backlog_revision: 9, current_step: key,
    steps: Object.fromEntries(STEPS.map(({key: step}) => [step, {status: prior.includes(step) ? 'saved' : step === key ? 'current' : 'todo', accepted_revision: prior.includes(step) ? 4 : null, evidence_id: prior.includes(step) ? 'e-' + step : null}])),
    accepted: {capture: copy(CAPTURE), priorities: {urgency: 6, importance: 7}, ...Object.fromEntries(Object.entries(ACCEPTED).filter(([step]) => prior.includes(step)).map(([step, fields]) => [step, copy(fields)]))},
    drafts: {}, draft: null, agent_status: 'connected', agent_generation: GEN, human_ratings: copy(rating), assessment_summary: null,
    backlog_status: {available: true, code: 'ok'}, backlog: {revision: 9, order: [IDEA, OTHER, THIRD], comparisons: [IDEA, OTHER, THIRD].map(idea_id => ({idea_id, revision: idea_id === IDEA ? 4 : 1, status: 'active', ratings: idea_id === IDEA ? copy(rating) : null, assessment: null}))},
    proposal_sources: {}, proposals: [], proposal_inventory: {total: 0, projected: 0, omitted: 0, content_omitted: 0, index_path: IDEA + '.md'}};
  const writes = [];
  const sources = () => {
    const connected = state.agent_status === 'connected', op = key === 'assess' ? 'assessment' : key;
    const data = key === 'assess' ? {steps: {capture: copy(CAPTURE), priorities: copy(state.accepted.priorities), discovery: copy(DISC), exploration: copy(EXPL)}, backlog: copy(state.backlog), target: copy(state.drafts.assess ?? null)} :
      {capture: copy(CAPTURE), ...(state.drafts[key] ? {[key]: copy(state.drafts[key])} : {})};
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


const MEMORY_FILL = {status: 'varied', sources: ['fixture-note'], rationale: 'Mixed past choices', preferred_method: null};
// Types into one guided field of a step as a person would (the step is already unlocked by a fill or a hand release).
const typed = (h, key, value) => h.flow.edit(key, {...h.flow.buffers[key], [F[key].name]: value});

test('autoConverse sends exactly one propose on step entry, none on redraw or a second call', async () => {
  for (const key of ['discovery', 'exploration', 'method', 'assess']) {
    const h = harness(key);
    assert.equal(await h.flow.autoConverse(key), true); assert.equal(h.proposeWrites().length, 1);
    assert.equal(h.proposeWrites()[0].payload.operation, key === 'assess' ? 'assessment' : key);
    h.draw(); await h.flow.autoConverse(key); assert.equal(h.proposeWrites().length, 1);
  }
});
test('autoConverse does nothing for other steps, no agent, a saved step or an adopted conversation', async () => {
  for (const key of GUIDED) {
    const h = harness(key); for (const other of ['capture', 'priorities', 'visualize', 'review']) await h.flow.autoConverse(other); assert.equal(h.proposeWrites().length, 0);
    const off = harness(key); off.state.agent_status = 'disconnected'; off.state.agent_generation = null; off.sources(); off.flow.load(copy(off.state), true);
    await off.flow.autoConverse(key); assert.equal(off.proposeWrites().length, 0);
    const saved = harness(key); saved.state.steps[key] = {status: 'saved', accepted_revision: 4, evidence_id: 'e'}; saved.state.accepted[key] = copy(ACCEPTED[key]); saved.flow.load(copy(saved.state), true);
    await saved.flow.autoConverse(key); assert.equal(saved.proposeWrites().length, 0);
    const open = harness(key); open.talk([F[key].fills[0]]); await open.sync(); await open.flow.autoConverse(key); assert.equal(open.proposeWrites().length, 0);
  }
});
test('a refused automatic request is not retried automatically', async () => {
  for (const key of GUIDED) {
    const h = harness(key, {propose: async () => {throw REFUSED;}});
    assert.equal(await h.flow.autoConverse(key), false); assert.equal(h.proposeWrites().length, 1);
    assert.equal(h.flow.proposalPending, null);
    await h.flow.autoConverse(key); await h.flow.autoConverse(key); assert.equal(h.proposeWrites().length, 1);
  }
});
test('fills apply once and in order to the named Discovery and Exploration fields only', async () => {
  for (const key of GUIDED) {
    const {name, id, fills, untouched, two} = F[key], third = F[key].third;
    const h = harness(key);
    h.talk([fills[0], fills[1]]); await h.sync();
    for (const [field, value] of Object.entries({...fills[0], ...fills[1]})) assert.deepEqual(h.flow.buffers[key][field], value, key + ' ' + field);
    for (const [field, value] of untouched) assert.equal(h.flow.buffers[key][field], value, key + ' ' + field + ' stays at its default');
    assert.deepEqual([...h.flow.filled[key]].sort(), two);
    assert.equal(h.get(id).value, fills[0][name]);
    assert.equal(h.writes.filter(w => w.operation === 'draft').length, 1); assert.equal(h.state.drafts[key][name], fills[0][name]);
    // The human edits, the same state arrives again: the fill is not applied a second time.
    h.get(id).input('Human words'); await h.sync(); await h.sync();
    assert.equal(h.flow.buffers[key][name], 'Human words');
    // A new fill (sequence 3) applies after the earlier ones.
    h.talk([fills[0], fills[1], fills[2]]); await h.sync();
    assert.equal(h.flow.buffers[key][third], fills[2][third]); assert.equal(h.flow.buffers[key][name], 'Human words');
    // Never accepted by the page on its own.
    assert.equal(h.writes.filter(w => w.operation === 'accept').length, 0);
  }
});
test('Method fill sets the memory result only; the human keeps the method choice and the reason', async () => {
  const h = harness('method'); h.flow.edit('method', {...h.flow.buffers.method, reason: 'My own reason'});
  h.talk([{memory: MEMORY_FILL}]); await h.sync();
  assert.deepEqual(h.flow.buffers.method.memory, MEMORY_FILL);
  assert.equal(h.flow.buffers.method.selection, null); assert.equal(h.flow.buffers.method.reason, 'My own reason');
  assert.deepEqual([...h.flow.filled.method], ['memory']);
  assert.ok(h.get('method-memory').all().some(node => node.textContent === 'You use all four. Pick freely.'));
  assert.equal(h.writes.filter(w => w.operation === 'accept').length, 0);
});
test('a conversation that tries to fill the method selection or reason is refused outright', async () => {
  for (const fields of [{reason: 'Terminal reason'}, {selection: 'bounded-plan'}, {memory: MEMORY_FILL, selection: 'bounded-plan'}]) {
    const h = harness('method'); h.talk([fields]);
    assert.throws(() => h.flow.load(copy(h.state), true), /Invalid agent conversation/);
    assert.equal(h.flow.buffers.method.selection, null); assert.equal(h.flow.buffers.method.reason, '');
  }
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
  for (const key of GUIDED) {
    const {name, fills, accepted} = F[key];
    const h = harness(key); h.state.steps[key] = {status: 'saved', accepted_revision: 4, evidence_id: 'e'}; h.state.accepted[key] = copy(ACCEPTED[key]);
    h.talk([fills[0]]); await h.sync(); assert.equal(h.flow.buffers[key][name], accepted); assert.equal(h.flow.filled[key]?.size ?? 0, 0);
    const stale = harness(key); stale.talk([fills[0]], {accepted_revision: 3});
    assert.throws(() => stale.flow.load(copy(stale.state), true)); assert.equal(stale.flow.buffers[key][name], '');
    const other = harness(key); other.talk([fills[0]], {idea_id: OTHER});
    assert.throws(() => other.flow.load(copy(other.state), true)); assert.equal(other.flow.buffers[key][name], '');
  }
});
test('a conversation for one step is refused when it names fields another step owns', async () => {
  // Discovery and Exploration each accept only their own fields: a cross-step fill never reaches a buffer.
  const crossed = {discovery: F.exploration.fills[0], exploration: F.discovery.fills[0]};
  for (const key of GUIDED) {
    const h = harness(key); h.talk([crossed[key]]);
    assert.throws(() => h.flow.load(copy(h.state), true), /Invalid agent conversation/);
    assert.equal(h.flow.buffers[key][F[key].name], '');
  }
});
test('filled markers show under the field and disappear when the human edits it', async () => {
  for (const key of GUIDED) {
    const {fills, marks, id} = F[key];
    const h = harness(key); assert.equal(h.get(id + '-filled'), undefined);
    h.talk([{...fills[0], ...(key === 'discovery' ? {audience: 'Terminal audience'} : {next_slice: 'Terminal slice'})}]); await h.sync();
    const note = h.get(marks[0][1] + '-filled'); assert.equal(note.textContent, 'From your terminal conversation'); assert.equal(note.className, 'filled-note');
    assert.ok(h.get(marks[1][1] + '-filled'), key + ' second field marked');
    h.get(id).input('Mine'); assert.equal(h.get(id + '-filled'), undefined); assert.ok(h.get(marks[1][1] + '-filled'));
  }
});
test('each step shows a conversation status line in plain words', async () => {
  const guiding = 'Your terminal is guiding this step. Answer there; agreed answers appear here as you go.';
  const counted = new RegExp('^' + guiding.replaceAll('.', '\\.') + ' \\d answers? filled from your terminal\\.$');
  const fills = {method: [{memory: MEMORY_FILL}], assess: [{assessment: assessment(), proposed_position: 1}]};
  // Method and Assess show the shared conversation line, with the filled-answer count.
  for (const key of ['method', 'assess']) {
    const asking = key === 'method' ? 'Checking what your Glitch remembers…' : 'Asking your terminal to start this step…';
    const h = harness(key), line = () => h.get(key + '-conversation-status');
    assert.equal(line().role, 'status'); assert.equal(line().textContent, guiding);
    await h.flow.autoConverse(key); assert.equal(line().textContent, asking);
    h.flow.cancelProposal('user_cancelled'); h.flow.proposalError = null; h.flow.onChange();
    h.talk(fills[key]); await h.sync(); assert.match(line().textContent, counted);
    const off = harness(key); off.state.agent_status = 'disconnected'; off.state.agent_generation = null; off.sources(); off.flow.load(copy(off.state), true);
    assert.equal(off.get(key + '-conversation-status').textContent, 'No agent is connected. You can fill this step by hand, or reinvoke /glitch-idea in your terminal to be guided.');
    assert.ok(!off.body.all().some(node => /initiating agent is unavailable|AI assistance is unavailable/.test(node.textContent)));
  }
  // Discovery has its own line; asking while the request is out, and the plain no-terminal line without an agent.
  {
    const h = harness('discovery'), line = () => h.get('discovery-conversation-status');
    assert.equal(line().role, 'status'); assert.equal(line().textContent, 'Your terminal is asking the big questions');
    await h.flow.autoConverse('discovery'); assert.equal(line().textContent, 'Asking your terminal to start this step…');
    const off = harness('discovery'); off.state.agent_status = 'disconnected'; off.state.agent_generation = null; off.sources(); off.flow.load(copy(off.state), true);
    assert.equal(off.get('discovery-conversation-status').textContent, 'No terminal is connected, so this step is yours to fill.');
    assert.ok(!off.body.all().some(node => /initiating agent is unavailable|AI assistance is unavailable/.test(node.textContent)));
  }
  // Exploration names the accepted method; with no accepted method it shows the shared line and its count.
  {
    const h = harness('exploration'), line = () => h.get('exploration-conversation-status');
    assert.equal(line().role, 'status'); assert.equal(line().textContent, 'Your terminal is exploring this through Full Plan Up Front');
    const open = harness('exploration'); delete open.state.accepted.method; open.state.steps.method = {status: 'todo', accepted_revision: null, evidence_id: null}; open.flow.load(copy(open.state), true);
    const free = () => open.get('exploration-conversation-status');
    assert.equal(free().textContent, guiding);
    await open.flow.autoConverse('exploration'); assert.equal(free().textContent, 'Asking your terminal to start this step…');
    open.flow.cancelProposal('user_cancelled'); open.flow.proposalError = null; open.flow.onChange();
    open.talk([F.exploration.fills[0], F.exploration.fills[2]]); await open.sync(); assert.match(free().textContent, counted);
    const off = harness('exploration'); off.state.agent_status = 'disconnected'; off.state.agent_generation = null; off.sources(); off.flow.load(copy(off.state), true);
    assert.equal(off.get('exploration-conversation-status').textContent, 'No terminal is connected, so this step is yours to fill.');
    assert.ok(!off.body.all().some(node => /initiating agent is unavailable|AI assistance is unavailable/.test(node.textContent)));
  }
});
test('no request buttons exist on the conversation steps', () => {
  for (const key of ['discovery', 'exploration', 'method', 'assess']) assert.equal(harness(key).get(key + '-request'), undefined);
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
  for (const key of GUIDED) {
    const h = harness(key); await h.flow.autoConverse(key); assert.ok(h.flow.proposalPending);
    typed(h, key, 'Human typing'); h.state.draft_version = 5; h.sources();
    h.talk([F[key].fills[0]], {request_id: h.proposeWrites()[0].payload.request_id}); await h.sync();
    assert.equal(h.flow.proposalPending, null); assert.equal(h.flow.proposalError, null);
    assert.equal(h.get(key + '-proposal-error'), undefined);
  }
});

test('typing while the automatic request is in flight is kept and saved, never dropped', async () => {
  // R2 known edge (builder-measured in a real browser): the first typed field came back empty.
  for (const key of GUIDED) {
    const {name} = F[key];
    let release;
    const gate = new Promise(resolve => {release = resolve;});
    const h = harness(key, {propose: async payload => {await gate;
      return {ok: true, code: 'ok', status: 'pending', write_state: 'not_applied', request_id: payload.request_id, session_id: SID, idea_id: IDEA,
        operation: payload.operation, accepted_revision: payload.expected_revision, draft_version: payload.expected_draft_version, source_digest: payload.source_digest};}});
    const flight = h.flow.autoConverse(key);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.flow.busy, true, 'the request is in flight');
    assert.equal(h.flow.inputLocked(), false, 'and the form is not locked');
    typed(h, key, 'Typed during the request');
    assert.equal(h.flow.buffers[key][name], 'Typed during the request');
    release(); await flight;
    assert.ok(h.flow.dirty.has(key), 'kept as an unsaved edit');
    assert.equal(await h.flow.save(key, true), true);
    assert.equal(h.writes.filter(item => item.operation === 'draft').at(-1).payload.fields[name], 'Typed during the request');
  }
});

test('a reload keeps applied fills applied; a conversation this tab never sent is adopted, not replayed', async () => {
  // Review ui-r1 blocker: an in-memory record reset on reload replayed old fills over later edits.
  for (const key of GUIDED) {
    const {name, fills} = F[key];
    const memory = new Map(), storage = {getItem: k => memory.get(k) ?? null, setItem: (k, v) => memory.set(k, v), removeItem: k => memory.delete(k)};
    const h = harness(key);
    h.flow.fillStorage = storage; h.flow.rememberFill('request-1', 0);
    h.talk([fills[0]]); await h.sync();
    assert.equal(h.flow.buffers[key][name], fills[0][name]);
    typed(h, key, 'My later edit'); await h.flow.save(key, true);
    // A new page in the same tab: same storage, fresh memory.
    const reloaded = new Flow(h.flow.api, () => 'request-x', () => 0, undefined, storage);
    reloaded.load(copy(h.state));
    assert.equal(reloaded.buffers[key][name], 'My later edit', 'the old fill is not replayed');
    // A tab that never saw the request adopts what is there without applying it.
    const other = new Flow(h.flow.api, () => 'request-y', () => 0, undefined, {getItem: () => null, setItem() {}, removeItem() {}});
    other.load(copy(h.state));
    assert.equal(other.buffers[key][name], 'My later edit');
  }
});

test('a fill can only touch the fields a conversation may fill, and never the human-chosen actual position', async () => {
  for (const key of GUIDED) {
    const {name, fills} = F[key], owned = key === 'discovery' ? 'kill_criteria' : 'scope', human = key === 'discovery' ? 'My stop rule' : 'capability';
    const h = harness(key);
    h.flow.edit(key, {...h.flow.buffers[key], [owned]: human});
    h.flow.rememberFill('request-1', 0);
    // A field no conversation may fill, even one a step owns: drive applyFills directly with an unknown field to prove the
    // page itself drops what the allowed list excludes (a validator refusal alone proves nothing about applyFills).
    h.flow.state = {...h.flow.state, conversation: {request_id: 'request-1', operation: key, idea_id: IDEA,
      accepted_revision: h.flow.state.revision, fills: [{sequence: 1, fields: {...fills[0], sneaky: 'dropped'}}]}};
    const saves = h.writes.filter(item => item.operation === 'draft').length;
    assert.equal(h.flow.applyFills(), true);
    assert.equal(h.flow.buffers[key][name], fills[0][name], 'the allowed field applies');
    assert.equal(Object.hasOwn(h.flow.buffers[key], 'sneaky'), false, 'the unknown field is dropped');
    assert.equal(h.flow.buffers[key][owned], human, 'a field the human set stays');
    await h.flow.fillSave;
    assert.equal(h.writes.filter(item => item.operation === 'draft').length, saves + 1, 'one save for the applied fills');
  }
  const method = harness('method');
  method.flow.edit('method', {...method.flow.buffers.method, selection: 'adaptive-slices', reason: 'Mine'});
  // The server and the page validator refuse a method conversation that names a selection; drive applyFills directly to
  // prove the page still drops a human-owned field (Review ui-r3: a validator refusal alone proved nothing here).
  method.flow.rememberFill('request-1', 0);
  method.flow.state = {...method.flow.state, conversation: {request_id: 'request-1', operation: 'method', idea_id: IDEA,
    accepted_revision: method.flow.state.revision, fills: [{sequence: 1, fields: {memory: MEMORY_FILL, selection: 'bounded-plan', reason: 'Agent reason'}}, {sequence: 2, fields: {selection: 'experiment-led'}}]}};
  const saves = method.writes.filter(item => item.operation === 'draft').length;
  assert.equal(method.flow.applyFills(), true);
  assert.deepEqual(method.flow.buffers.method.memory, MEMORY_FILL, 'the allowed field applies');
  assert.equal(method.flow.buffers.method.selection, 'adaptive-slices', 'the human-owned selection is dropped');
  assert.equal(method.flow.buffers.method.reason, 'Mine', 'the human-owned reason is dropped');
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
  for (const key of GUIDED) {
    const {name, fills} = F[key];
    const memory = new Map(), storage = {getItem: k => memory.get(k) ?? null, setItem: (k, v) => memory.set(k, v), removeItem: k => memory.delete(k)};
    const h = harness(key);
    h.flow.fillStorage = storage; h.flow.rememberFill('request-1', 0);
    const realWrite = h.flow.api.write;
    h.flow.api.write = async (operation, payload) => {
      if (operation === 'draft') throw Object.assign(new Error('connection_lost'), {code: 'connection_lost', status: 0});
      return realWrite(operation, payload);
    };
    h.talk([fills[0]]); await h.sync();
    assert.notEqual(JSON.parse(memory.get('glitch-idea-fills'))['request-1'], 1, 'not recorded as applied while unsaved');
    const reloaded = new Flow(h.flow.api, () => 'request-r', () => 0, undefined, storage);
    h.flow.api.write = realWrite; reloaded.load(copy(h.state)); await reloaded.fillSave;
    assert.equal(reloaded.buffers[key][name], fills[0][name], 'applied again after the reload');
    // Mid-write: the page is busy when the next fill arrives.
    const g = harness(key); g.flow.rememberFill('request-1', 0);
    g.flow.busy = true;
    g.talk([fills[0]]); g.flow.load(copy(g.state), true);
    const before = g.writes.filter(item => item.operation === 'draft').length;
    g.flow.busy = false; await g.flow.fillSave;
    assert.equal(g.writes.filter(item => item.operation === 'draft').length, before + 1, 'saved once the write finished');
    assert.equal(g.writes.filter(item => item.operation === 'draft').at(-1).payload.fields[name], fills[0][name]);
  }
});

// Review ui-r4: real in-flight writes (a held draft write), not a boolean stand-in.
function holdDrafts(h) {
  const real = h.flow.api.write, held = [];
  h.flow.api.write = (operation, payload) => operation !== 'draft' ? real(operation, payload) :
    new Promise(resolve => held.push(() => resolve(real(operation, payload))));
  return {release: async () => { while (held.length) { held.shift()(); await new Promise(r => setTimeout(r, 0)); } }, held, restore: () => { h.flow.api.write = real; }};
}

test('typing during a draft save never clears the write in flight, and an undo during it stays unsaved', async () => {
  for (const key of GUIDED) {
    const {name} = F[key];
    const h = harness(key);
    typed(h, key, 'A'); assert.equal(await h.flow.save(key, true), true);
    const hold2 = holdDrafts(h);
    typed(h, key, 'B');
    const saving = h.flow.save(key, true);
    await new Promise(r => setTimeout(r, 0));
    assert.ok(h.flow.pending, 'a draft write is in flight');
    typed(h, key, 'A');   // the human undoes to the saved text during the write
    assert.ok(h.flow.pending, 'the in-flight write is never cleared by typing');
    assert.ok(h.flow.dirty.has(key));
    await hold2.release(); await saving;
    assert.equal(h.flow.buffers[key][name], 'A', 'the undo survives the reload after B was saved');
    assert.ok(h.flow.dirty.has(key), 'and stays unsaved, so it will be saved');
  }
});

test('a fill applied during a real in-flight save is saved after it, applied once, and its cursor never moves back', async () => {
  for (const key of GUIDED) {
    const {fills, third} = F[key];
    const memory = new Map(), storage = {getItem: k => memory.get(k) ?? null, setItem: (k, v) => memory.set(k, v), removeItem: k => memory.delete(k)};
    const h = harness(key); h.flow.fillStorage = storage; h.flow.rememberFill('request-1', 0);
    const hold = holdDrafts(h);
    h.talk([fills[0]]); h.flow.load(copy(h.state), true);   // fill 1 applied, its save is held
    await new Promise(r => setTimeout(r, 0));
    h.talk([fills[0], fills[2]]); h.flow.load(copy(h.state), true);   // fill 2 lands mid-write
    await hold.release(); await new Promise(r => setTimeout(r, 250)); await hold.release(); await h.flow.fillSave;
    const drafts = h.writes.filter(item => item.operation === 'draft');
    assert.equal(drafts.at(-1).payload.fields[third], fills[2][third], 'fill two was saved after the first write');
    assert.equal(JSON.parse(memory.get('glitch-idea-fills'))['request-1'], 2, 'durable cursor at 2, never rolled back to 1');
    h.flow.edit(key, {...h.flow.buffers[key], [third]: 'My edit'});
    h.flow.load(copy(h.state), true);
    assert.equal(h.flow.buffers[key][third], 'My edit', 'fill two is never applied again over the edit');
  }
});

test('a deferred fill save never saves another idea, and Assess fields stay editable during a draft save', async () => {
  for (const key of GUIDED) {
    const h = harness(key); h.flow.rememberFill('request-1', 0);
    h.flow.busy = true; h.talk([F[key].fills[0]]); h.flow.load(copy(h.state), true);
    h.flow.state = {...h.flow.state, idea_id: OTHER};   // the page moved to another idea before the wait ended
    h.flow.busy = false;
    assert.equal(await h.flow.fillSave, false);
    assert.equal(h.writes.filter(item => item.operation === 'draft').length, 0);
  }
  const a = harness('assess'); await a.get('assess-method-wsjf').click(); const hold = holdDrafts(a);
  const saving = a.flow.save('assess', true);
  await new Promise(r => setTimeout(r, 0)); a.draw();
  const input = a.get('assess-input-value');
  assert.equal(input.disabled, false, 'Assess inputs are not locked by a draft save');
  await hold.release(); await saving;
});

test('a fill identical to what is already saved still records its cursor, so a reload never replays it', async () => {
  // Review final review: the save it waited for made the buffer clean, and the cursor was never persisted.
  for (const key of GUIDED) {
    const {name, fills} = F[key];
    const memory = new Map(), storage = {getItem: k => memory.get(k) ?? null, setItem: (k, v) => memory.set(k, v), removeItem: k => memory.delete(k)};
    const h = harness(key); h.flow.fillStorage = storage; h.flow.rememberFill('request-1', 0);
    typed(h, key, fills[0][name]); assert.equal(await h.flow.save(key, true), true);
    h.flow.busy = true; h.talk([fills[0]]); h.flow.load(copy(h.state), true); h.flow.busy = false;
    await h.flow.fillSave;
    assert.equal(JSON.parse(memory.get('glitch-idea-fills'))['request-1'], 1);
  }
});

test('a cursor is recorded only when the authoritative saved draft holds the fill', async () => {
  // Review confirmation: another tab changed the draft before the reread; the fill must not be marked applied.
  for (const key of GUIDED) {
    const {name, fills} = F[key];
    const memory = new Map(), storage = {getItem: k => memory.get(k) ?? null, setItem: (k, v) => memory.set(k, v), removeItem: k => memory.delete(k)};
    const h = harness(key); h.flow.fillStorage = storage; h.flow.rememberFill('request-1', 0);
    h.flow.busy = true;   // a write is in flight when the fill lands, so its save waits
    h.talk([fills[0]]); h.flow.load(copy(h.state), true);
    h.state.drafts[key] = {...h.flow.buffers[key], [name]: 'Another tab wrote this'};   // the authority moved on
    h.flow.dirty.delete(key); h.flow.state = {...h.flow.state, drafts: copy(h.state.drafts)};
    h.flow.busy = false; await h.flow.fillSave;
    assert.notEqual(JSON.parse(memory.get('glitch-idea-fills') ?? '{}')['request-1'], 1);
  }
});
