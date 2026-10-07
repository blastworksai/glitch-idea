// Review fixes for the Discovery, Exploration, Method and Visualize views and their Flow helpers.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web = process.env.CP3_WEB ? new URL('file://' + process.env.CP3_WEB.replace(/\/?$/, '/')) : new URL('../../glitch-idea/web/', import.meta.url);
const moduleUrl = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const foldsUrl = moduleUrl(await readFile(new URL('folds.js', web), 'utf8'));
const folds = await import(foldsUrl);
const {Flow, STEPS, acceptBlockers, disabledAcceptReason} = folds;
const stepSource = async name => (await readFile(new URL('steps/' + name + '.js', web), 'utf8'));
const loadStep = async name => (await import(moduleUrl((await stepSource(name)).replace("'../folds.js'", JSON.stringify(foldsUrl))))) ;
const discovery = await loadStep('discovery'), exploration = await loadStep('exploration'), method = await loadStep('method'), visualize = await loadStep('visualize');
const IDEA = 'idea_' + '1'.repeat(32), SID = 'session_' + '2'.repeat(32), GEN = 'agent_' + '3'.repeat(32);
const copy = value => structuredClone(value);
const CAPTURE = {raw_text: 'A fixture idea', workspace: {name: 'fixture', path: '/fixture', confirmed: true}};
const DISCOVERY = {problem: 'P', audience: 'A', workaround: 'W', evidence: 'E', kill_criteria: 'K', challenges: [{challenge: 'C', response: 'R'}], prior_art: [{name: 'Tapeo', link: 'https://example.test/tapeo', does: 'Seals lids', differs: 'Ours is reusable', licence: 'MIT'}], prior_art_none: false, prior_art_searched: ''};
const EXPLORATION = {outcome: 'O', alternatives: [{route: 'R', reason: 'Y'}], assumptions: [], scope: 'small-change', scope_reason: 'S', next_slice: 'N',
  learning: [], investment: null, experiment: null, sketch: [{title: 'T', why_next: 'W', done_when: 'D', method: null}]};

function state({agent = 'connected', current = 'discovery', status = 'active', selection = null, saved = [], accepted = {}, hand, conversation} = {}) {
  const done = new Set(['capture', 'priorities', 'method', ...saved]);
  const value = {ok: true, code: 'ok', session_id: SID, idea_id: IDEA, idea_status: status, revision: 3, draft_version: 0, backlog_revision: 0,
    current_step: current, agent_status: agent, agent_generation: agent === 'connected' ? GEN : null,
    steps: Object.fromEntries(STEPS.map(({key}) => [key, done.has(key) ? {status: 'saved', accepted_revision: 2, evidence_id: key + '-e'} :
      {status: key === current ? 'current' : 'todo', accepted_revision: null, evidence_id: null}])),
    accepted: {...Object.fromEntries(STEPS.map(({key}) => [key, null])), capture: copy(CAPTURE),
      method: {selection, reason: '', memory: {status: 'unavailable', sources: [], rationale: null, preferred_method: null}}, ...accepted},
    drafts: {}, draft: null, proposal_sources: {}, proposals: [],
    proposal_inventory: {total: 0, projected: 0, omitted: 0, content_omitted: 0, index_path: IDEA + '.md'}};
  if (hand) value.hand = hand;
  if (conversation) value.conversation = conversation;
  return value;
}
function flowFor(options, buffers = {}) {
  const api = {state: async () => copy(state(options)), write: async () => { throw Object.assign(new Error('no'), {code: 'x'}); }, release: async () => ({ok: true})};
  const flow = new Flow(api, () => 'request-1'); flow.load(state(options));
  for (const [key, value] of Object.entries(buffers)) flow.buffers[key] = copy(value);
  return flow;
}

class Node {
  constructor(tag, text = '', className = '') {this.tag = tag; this.textContent = text; this.className = className; this.children = []; this.listeners = {}; this.disabled = false;}
  append(...children) {this.children.push(...children);}
  setAttribute(key, value) {this[key] = value;}
  addEventListener(key, fn) {this.listeners[key] = fn;}
  all() {return [this, ...this.children.flatMap(child => child.all())];}
}
const element = (tag, text = '', className = '') => new Node(tag, text, className);
const button = (text, action, className = '') => {const node = element('button', text, className); node.type = 'button'; node.addEventListener('click', action); return node;};
function draw(module, flow, connected = true) {
  const body = element('div'), foot = element('footer');
  const field = (target, label, id, value, changed) => {
    const input = element('textarea'); input.id = id; input.value = value ?? ''; input.addEventListener('input', event => changed(event.target.value));
    target.append(element('label', label), input); return input;
  };
  module.render({body, foot, flow, element, button, field, connected, edited: (key, value) => flow.edit(key, value), handle: action => async () => action(),
    proposalInventory: () => {}});
  const all = [...body.all(), ...foot.all()];
  return {get: id => all.find(node => node.id === id), all};
}

// 1. b4 and M2: the visual brief.
const briefFlow = (reason, accepted = {}) => ({status: () => 'saved', state: {accepted: {capture: CAPTURE, discovery: DISCOVERY,
  exploration: {...EXPLORATION, ...accepted}, method: {selection: 'appetite-led', reason, memory: {}}}}});
test('the visual brief reads investment and experiment from the accepted Exploration', () => {
  const brief = visualize.visualBrief(briefFlow('Because', {investment: {cap: 3, unit: 'weeks', boundary: 'Whole thing'},
    experiment: {question: 'Q1', evidence: 'E1', success_criterion: 'S1', stop_rule: 'R1'}}));
  assert.match(brief, /Investment cap: 3 weeks/); assert.match(brief, /Boundary: Whole thing/);
  assert.match(brief, /Question: Q1/); assert.match(brief, /Stop rule: R1/);
});
test('the visual brief omits the Reason line when the method reason is blank or null', () => {
  for (const reason of ['', '   ', null, undefined]) assert.doesNotMatch(visualize.visualBrief(briefFlow(reason)), /Reason:/, JSON.stringify(reason));
  assert.match(visualize.visualBrief(briefFlow('Because')), /Reason: Because/);
});

// 2. M1 and 7: archived Discovery.
test('archived Discovery points to Exploration and disables Accept with that reason', () => {
  const flow = flowFor({status: 'archived'}, {discovery: DISCOVERY});
  const view = draw(discovery, flow, true);
  assert.match(view.get('discovery-reactivation-notice').textContent, /redo and accept Exploration to reactivate/);
  assert.doesNotMatch(view.all.map(n => n.textContent).join('|'), /Accepting a redone Discovery reactivates/);
  assert.equal(view.get('discovery-accept').textContent, 'Accept and continue');
  assert.equal(view.get('discovery-accept').disabled, true);
  assert.ok(view.get('discovery-accept-reason').textContent.length > 0);
});

// 3. b1: Exploration Accept waits for locks, the count includes optional lists.
test('Exploration Accept is disabled while any field is locked and the count includes the optional lists', () => {
  const flow = flowFor({current: 'exploration', saved: ['discovery']}, {exploration: EXPLORATION});
  assert.equal(flow.fieldLocked('exploration', 'assumptions'), true); assert.equal(flow.fieldLocked('exploration', 'learning'), true);
  const view = draw(exploration, flow, true);
  assert.equal(view.get('exploration-accept').disabled, true);
  assert.ok(view.get('exploration-hand-fill'));
  assert.match(view.get('exploration-accept-reason').textContent, /: 2 fields still waiting\./);
  assert.ok(acceptBlockers('exploration', EXPLORATION, flow).some(s => s.endsWith(': 2 fields still waiting.')));
  // Taking the step by hand opens it; so does having no terminal.
  for (const options of [{current: 'exploration', saved: ['discovery'], hand: {discovery: false, exploration: true}}, {current: 'exploration', saved: ['discovery'], agent: 'disconnected'}]) {
    const open = draw(exploration, flowFor(options, {exploration: EXPLORATION}), true);
    assert.equal(open.get('exploration-accept').disabled, false); assert.equal(open.get('exploration-accept-reason'), undefined);
  }
});
test('the waiting count includes optional lists for Discovery steps too (every locked field)', () => {
  const flow = flowFor({}, {discovery: {...DISCOVERY, challenges: []}});
  assert.ok(acceptBlockers('discovery', {...DISCOVERY, challenges: []}, flow).some(s => s.endsWith(': 1 field still waiting.')));
});

// 4. a4 and b2: method-bound locks, clear buttons never locked.
test('investment locks only for an appetite-led method and experiment only for experiment-led', () => {
  const empty = {...EXPLORATION, investment: {cap: null, unit: '', boundary: ''}, experiment: {question: '', evidence: '', success_criterion: '', stop_rule: ''}};
  const plain = flowFor({current: 'exploration', saved: ['discovery'], selection: 'adaptive-slices'}, {exploration: empty});
  assert.equal(plain.fieldLocked('exploration', 'investment'), false); assert.equal(plain.fieldLocked('exploration', 'experiment'), false);
  const appetite = flowFor({current: 'exploration', saved: ['discovery'], selection: 'appetite-led'}, {exploration: empty});
  assert.equal(appetite.fieldLocked('exploration', 'investment'), true); assert.equal(appetite.fieldLocked('exploration', 'experiment'), false);
  const trial = flowFor({current: 'exploration', saved: ['discovery'], selection: 'experiment-led'}, {exploration: empty});
  assert.equal(trial.fieldLocked('exploration', 'experiment'), true); assert.equal(trial.fieldLocked('exploration', 'investment'), false);
});
test('the Clear investment and Clear experiment buttons are never disabled by a lock', () => {
  const empty = {...EXPLORATION, investment: {cap: null, unit: '', boundary: ''}, experiment: {question: '', evidence: '', success_criterion: '', stop_rule: ''}};
  const view = draw(exploration, flowFor({current: 'exploration', saved: ['discovery'], selection: 'adaptive-slices'}, {exploration: empty}), true);
  assert.equal(view.get('exploration-clear-investment').disabled, false); assert.equal(view.get('exploration-clear-experiment').disabled, false);
  const off = draw(exploration, flowFor({current: 'exploration', saved: ['discovery'], selection: 'adaptive-slices'}, {exploration: empty}), false);
  assert.equal(off.get('exploration-clear-investment').disabled, true, 'blocked() still disables it');
});

// 5. a3: a hand-released step takes no more fills.
test('applyFills changes nothing for a hand-released step', () => {
  const conversation = fills => ({request_id: 'conv-1', operation: 'discovery', idea_id: IDEA, accepted_revision: 3, fills});
  const make = hand => {
    const flow = flowFor({hand, conversation: conversation([])}, {discovery: {...DISCOVERY, problem: ''}});
    flow.state.conversation = conversation([{sequence: 1, fields: {problem: 'From the terminal'}}]);
    return flow;
  };
  const released = make({discovery: true, exploration: false});
  assert.equal(released.applyFills(), false); assert.equal(released.buffers.discovery.problem, '');
  const open = make({discovery: false, exploration: false});
  assert.equal(open.applyFills(), true); assert.equal(open.buffers.discovery.problem, 'From the terminal');
});

// 6. a5: Exploration acceptance reactivates, so it never carries the archived sentence.
test('the archived sentence appears for Discovery but not for Exploration', () => {
  const flow = flowFor({status: 'archived', current: 'exploration', saved: ['discovery']});
  assert.ok(acceptBlockers('discovery', DISCOVERY, flow).some(s => /archived/.test(s)));
  assert.ok(!acceptBlockers('exploration', EXPLORATION, flow).some(s => /archived/.test(s)));
});

// 7. b5: a disabled Accept always has a reason.
test('a disabled Accept names a lost connection, an unusable suggestion, and never stays silent', () => {
  const cases = [['discovery', discovery, 'discovery-accept', {}, {discovery: DISCOVERY}, 'discovery'],
    ['exploration', exploration, 'exploration-accept', {current: 'exploration', saved: ['discovery'], agent: 'disconnected'}, {exploration: EXPLORATION}, 'exploration'],
    ['method', method, 'method-accept', {current: 'method', saved: []}, {method: {selection: 'adaptive-slices', reason: '', memory: {status: 'unavailable', sources: [], rationale: null}}}, 'method']];
  for (const [name, module, id, options, buffers, key] of cases) {
    const opts = name === 'discovery' ? {agent: 'disconnected'} : options;
    const offline = draw(module, flowFor(opts, buffers), false);
    assert.equal(offline.get(id).disabled, true, name);
    assert.match(offline.get(id).title, /Reconnect to the idea service before accepting\./, name);
    assert.match(offline.get(id + '-reason').textContent, /Reconnect to the idea service before accepting\./, name);
    assert.equal(offline.get(id).title, offline.get(id + '-reason').textContent, name);
    const flow = flowFor(opts, buffers); flow.selectedProposals[key] = 'proposal_' + '9'.repeat(32);
    const stale = draw(module, flow, true);
    assert.equal(stale.get(id).disabled, true, name);
    assert.match(stale.get(id + '-reason').textContent, /The suggestion you used can no longer be accepted\. Accept your answers as your own, or unlink it\./, name);
  }
  const fine = flowFor({agent: 'disconnected'}, {discovery: DISCOVERY});
  assert.ok(disabledAcceptReason('discovery', DISCOVERY, fine).length > 0, 'fallback sentence');
});

// 8. b8: the dead filledNote call on the optional method reason is gone.
test('the Method view carries no filledNote call for its optional reason', async () => {
  assert.doesNotMatch(await stepSource('method'), /filledNote/);
});

// Terminal-delivered content counts as filled: Use and explicit fill keys release their locks.
const proposalState = (key, proposal) => {
  const options = key === 'exploration' ? {current: 'exploration', saved: ['discovery']} : {};
  const value = state(options);
  value.proposals = [{proposal_id: 'proposal_' + '4'.repeat(32), request_id: 'request-1', operation: key, accepted_revision: 3, draft_version: 0, source_digest: 'a'.repeat(64),
    proposal: copy(proposal), stale: false, stale_reason: null, acceptance_eligible: true, acceptance_reason: null, content_omitted: false,
    evidence: {path: 'history/' + IDEA + '/metadata/' + 'b'.repeat(64) + '.md', sha256: 'c'.repeat(64)}}];
  value.proposal_inventory = {total: 1, projected: 1, omitted: 0, content_omitted: 0, index_path: IDEA + '.md'};
  return value;
};
test('Use releases every lock of the step and Accept opens on a valid suggestion', () => {
  for (const [key, module, proposal] of [['exploration', exploration, EXPLORATION], ['discovery', discovery, DISCOVERY]]) {
    const s = proposalState(key, proposal);
    const flow = new Flow({state: async () => copy(s), write: async () => {throw new Error('no');}}, () => 'request-1'); flow.load(copy(s));
    assert.ok(FILL_KEYS_OF(key).some(name => flow.fieldLocked(key, name)), key + ' starts locked');
    assert.equal(flow.useProposal(key, 'proposal_' + '4'.repeat(32)), true);
    for (const name of FILL_KEYS_OF(key)) assert.equal(flow.fieldLocked(key, name), false, key + ':' + name);
    const view = draw(module, flow, true);
    assert.equal(view.get(key + '-accept').disabled, false, key); assert.equal(view.get(key + '-hand-fill'), undefined, key);
  }
});
const FILL_KEYS_OF = key => folds.FILL_KEYS[key];
test('a fill carrying an empty list unlocks that field; an omitted key stays locked', () => {
  const conversation = fills => ({request_id: 'conv-2', operation: 'exploration', idea_id: IDEA, accepted_revision: 3, fills});
  const options = {current: 'exploration', saved: ['discovery']};
  const flow = flowFor({...options, conversation: conversation([])});
  flow.state.conversation = conversation([{sequence: 1, fields: {outcome: 'From the terminal', assumptions: [], learning: null}}]);
  assert.equal(flow.applyFills(), true);
  assert.equal(flow.fieldLocked('exploration', 'assumptions'), false);
  assert.equal(flow.fieldLocked('exploration', 'learning'), false);
  assert.equal(flow.fieldLocked('exploration', 'outcome'), false);
  assert.equal(flow.fieldLocked('exploration', 'sketch'), true, 'never sent: still waiting');
  assert.equal(flow.fieldLocked('exploration', 'alternatives'), true);
});

// The investment/experiment lock exemption belongs to the Clear buttons only, never to the other controls.
test('appetite-led and experiment-led inputs are disabled with a lock note while the terminal has not delivered', () => {
  const empty = {...EXPLORATION, investment: {cap: null, unit: '', boundary: ''}, experiment: {question: '', evidence: '', success_criterion: '', stop_rule: ''}};
  const appetite = draw(exploration, flowFor({current: 'exploration', saved: ['discovery'], selection: 'appetite-led'}, {exploration: empty}), true);
  for (const id of ['cap', 'unit', 'boundary']) assert.equal(appetite.get('exploration-investment-' + id).disabled, true, 'investment ' + id);
  assert.ok(appetite.all.some(node => node.className === 'lock-note'), 'appetite-led lock note');
  const trial = draw(exploration, flowFor({current: 'exploration', saved: ['discovery'], selection: 'experiment-led'}, {exploration: empty}), true);
  for (const id of ['question', 'evidence', 'success-criterion', 'stop-rule']) assert.equal(trial.get('exploration-experiment-' + id).disabled, true, 'experiment ' + id);
  assert.ok(trial.all.some(node => node.className === 'lock-note'), 'experiment-led lock note');
});
test('under bounded-plan a leftover investment keeps an enabled Clear button', () => {
  const left = {...EXPLORATION, investment: {cap: 5, unit: 'days', boundary: 'b'}};
  const view = draw(exploration, flowFor({current: 'exploration', saved: ['discovery'], selection: 'bounded-plan'}, {exploration: left}), true);
  assert.equal(view.get('exploration-clear-investment').disabled, false);
});
test('gate checks the lock for every name; the Clear buttons bypass it explicitly', async () => {
  const source = await stepSource('exploration');
  assert.doesNotMatch(source, /name !== 'investment'/); assert.doesNotMatch(source, /name !== 'experiment'/);
  assert.doesNotMatch(source, /gate\(button\('Clear (investment|experiment)/);
});
