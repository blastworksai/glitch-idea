// Exploration step view: field order, alternatives row, sketch cards, method inputs, locks, Accept reason.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web = new URL('../../glitch-idea/web/', import.meta.url);
const moduleUrl = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const foldsUrl = moduleUrl(await readFile(new URL('folds.js', web), 'utf8'));
const {Flow, STEPS} = await import(foldsUrl);
const source = (await readFile(new URL('steps/exploration.js', web), 'utf8')).replace("'../folds.js'", JSON.stringify(foldsUrl));
const {render} = await import(moduleUrl(source));
const IDEA = 'idea_' + '1'.repeat(32), GEN = 'agent_' + '3'.repeat(32);

class Node {
  constructor(tag, text = '', className = '') {this.tag = tag; this.textContent = text; this.className = className; this.children = []; this.listeners = {}; this.disabled = false;}
  append(...children) {this.children.push(...children);}
  setAttribute(key, value) {this[key] = value;}
  addEventListener(key, fn) {this.listeners[key] = fn;}
  all() {return [this, ...this.children.flatMap(child => child.all())];}
  text() {return this.textContent + this.children.map(child => child.text()).join('');}
}
const element = (tag, text = '', className = '') => new Node(tag, text, className);
const button = (text, action, className = '') => {const node = element('button', text, className); node.addEventListener('click', action); return node;};

function draw({agent = 'connected', method = 'adaptive-slices', buffer = {}, hand = false, edits = []} = {}) {
  const done = new Set(['capture', 'priorities', 'method', 'discovery']);
  const state = {ok: true, code: 'ok', session_id: 'session_' + '2'.repeat(32), idea_id: IDEA, idea_status: 'active', revision: 3, draft_version: 0, backlog_revision: 0,
    current_step: 'exploration', agent_status: agent, agent_generation: agent === 'connected' ? GEN : null,
    steps: Object.fromEntries(STEPS.map(({key}) => [key, done.has(key) ? {status: 'saved', accepted_revision: 2, evidence_id: 'e-' + key} :
      {status: key === 'exploration' ? 'current' : 'todo', accepted_revision: null, evidence_id: null}])),
    accepted: {capture: {raw_text: 'Words', workspace: {name: 'F', path: '/f', confirmed: true}}, priorities: {urgency: 6, importance: 7},
      method: {selection: method, reason: '', memory: {status: 'unavailable', sources: [], rationale: null, preferred_method: null}}, discovery: null, exploration: null},
    drafts: {}, draft: null, proposal_sources: {}, proposals: [],
    proposal_inventory: {total: 0, projected: 0, omitted: 0, content_omitted: 0, index_path: IDEA + '.md'}};
  if (hand) state.hand = {discovery: false, exploration: true};
  const flow = new Flow({state: async () => state, write: async () => ({ok: true})}, () => 'r'); flow.load(state);
  flow.buffers.exploration = {...flow.buffers.exploration, ...buffer};
  const body = element('div'), foot = element('footer');
  const field = (target, label, id, value, changed, area) => {
    const wrap = element('div'), lab = element('label', label), input = element(area ? 'textarea' : 'input');
    input.id = id; input.value = value ?? ''; input.changed = changed; lab.htmlFor = id; wrap.append(lab, input); target.append(wrap); return input;
  };
  render({body, foot, flow, element, button, field, connected: true, edited: (key, fields) => {edits.push({key, fields}); flow.buffers[key] = fields;},
    handle: a => a, proposalInventory: () => {}});
  return {body, foot, flow, edits};
}
const byId = (root, id) => root.all().find(node => node.id === id);
const FILLED = {outcome: 'O', alternatives: [{route: 'R', reason: 'W'}], assumptions: ['A'], scope: 'small-change', scope_reason: 'S',
  next_slice: 'N', learning: ['L'], sketch: [{title: 'T', why_next: 'Y', done_when: 'D', method: null}]};
const items = n => Array.from({length: n}, (_, i) => ({title: 'T' + i, why_next: '', done_when: 'D' + i, method: null}));

test('the eight groups sit in the ruled order', () => {
  const {body} = draw({agent: 'disconnected', method: 'appetite-led', buffer: {...FILLED, investment: {cap: 2, unit: 'days', boundary: 'b'}}});
  const order = ['exploration-outcome', 'exploration-alternatives', 'exploration-assumptions', 'exploration-scope', 'exploration-scope-reason',
    'exploration-next-slice', 'exploration-learning', 'exploration-investment', 'exploration-sketch'];
  const ids = body.all().map(node => node.id).filter(id => order.includes(id));
  assert.deepEqual(ids, order);
  const legends = body.all().filter(node => node.tag === 'legend').map(node => node.textContent);
  assert.deepEqual(legends, ['Alternatives, including a simpler route', 'Uncertainty and risk', 'Scope', 'Learning for the next slice', 'Investment', 'Sketch: the first few items']);
});

test('empty alternatives show one open row with Add route', () => {
  const {body} = draw({agent: 'disconnected'});
  assert.ok(byId(body, 'exploration-alternative-0-route'));
  assert.ok(byId(body, 'exploration-alternative-0-reason'));
  assert.equal(byId(body, 'exploration-alternative-1-route'), undefined);
  assert.equal(byId(body, 'exploration-add-alternative').disabled, false);
});

test('editing the empty row stores a real alternative', () => {
  const edits = [], {body} = draw({agent: 'disconnected', edits});
  byId(body, 'exploration-alternative-0-route').changed('Do less');
  assert.deepEqual(edits.at(-1).fields.alternatives, [{route: 'Do less', reason: ''}]);
});

test('five sketch cards disable Add item and say why', () => {
  const four = draw({agent: 'disconnected', buffer: {sketch: items(4)}});
  assert.equal(byId(four.body, 'exploration-add-sketch').disabled, false);
  assert.equal(four.body.text().includes("Up to 5 items."), false);
  const five = draw({agent: 'disconnected', buffer: {sketch: items(5)}});
  assert.equal(byId(five.body, 'exploration-add-sketch').disabled, true);
  assert.match(five.body.text(), /Up to 5 items\. Breaking down the work is \/glitch-plan's job\./);
  const list = five.body.all().find(node => node.tag === 'ol');
  assert.match(list.className, /sketch-cards/);
  assert.equal(list.children.length, 5);
  assert.deepEqual(list.children.map(card => card.children[0].children[0].textContent), ['1', '2', '3', '4', '5']);
});

test('the chip defaults to overall method and an inner method updates it and the buffer', () => {
  const edits = [], first = draw({agent: 'disconnected', buffer: {sketch: items(1)}, edits});
  const chip = first.body.all().find(node => /\bmethod-chip\b/.test(node.className));
  assert.equal(chip.textContent, 'overall method'); assert.equal(chip['data-overall'], 'true');
  const select = byId(first.body, 'exploration-sketch-0-method');
  assert.deepEqual(select.children.map(option => option.textContent),
    ['Overall method', 'Full Plan Up Front', 'Vertical Slicing (Agile)', 'Fixed Budget, Build what Fits', 'Experiment First']);
  select.listeners.change({target: {value: 'experiment-led'}});
  assert.equal(edits.at(-1).fields.sketch[0].method, 'experiment-led');
  const second = draw({agent: 'disconnected', buffer: {sketch: [{...items(1)[0], method: 'experiment-led'}]}});
  const picked = second.body.all().find(node => /\bmethod-chip\b/.test(node.className));
  assert.equal(picked.textContent, 'Experiment First'); assert.equal(picked['data-overall'], undefined);
  select.listeners.change({target: {value: ''}});
  assert.equal(edits.at(-1).fields.sketch[0].method, null);
});

test('method inputs follow the accepted method', () => {
  const ids = method => {
    const {body} = draw({agent: 'disconnected', method});
    return ['exploration-investment', 'exploration-experiment'].filter(id => byId(body, id));
  };
  assert.deepEqual(ids('appetite-led'), ['exploration-investment']);
  assert.deepEqual(ids('experiment-led'), ['exploration-experiment']);
  assert.deepEqual(ids('adaptive-slices'), []);
  assert.deepEqual(ids('bounded-plan'), []);
  const {body} = draw({agent: 'disconnected', method: 'appetite-led'});
  for (const id of ['cap', 'unit', 'boundary']) assert.ok(byId(body, 'exploration-investment-' + id), id);
  const exp = draw({agent: 'disconnected', method: 'experiment-led'}).body;
  for (const id of ['question', 'evidence', 'success-criterion', 'stop-rule']) assert.ok(byId(exp, 'exploration-experiment-' + id), id);
});

test('the cap accepts a comma decimal and rejects zero', () => {
  const edits = [], {body} = draw({agent: 'disconnected', method: 'appetite-led', edits});
  byId(body, 'exploration-investment-cap').changed('2,5');
  assert.equal(edits.at(-1).fields.investment.cap, 2.5);
  byId(body, 'exploration-investment-cap').changed('0');
  assert.equal(edits.at(-1).fields.investment.cap, null);
});

test('locks wrap every field, say who they wait for, and hand-fill releases', () => {
  const {body, flow} = draw();
  const locked = body.all().filter(node => /\bfield-locked\b/.test(node.className));
  assert.ok(locked.length >= 8, 'locked wrappers: ' + locked.length);
  const notesIn = node => node.all().filter(item => /\block-note\b/.test(item.className));
  assert.ok(locked.every(node => notesIn(node).length === 1 && notesIn(node)[0].textContent === 'Waiting for your terminal'));
  // A group's note leads its wrapper, so it never trails against the next field's label.
  assert.ok(locked.filter(node => node.children.some(child => child.tag === 'fieldset')).every(node => /\block-note\b/.test(node.children[0].className)));
  assert.equal(byId(body, 'exploration-outcome').disabled, true);
  assert.equal(byId(body, 'exploration-add-sketch').disabled, true);
  assert.equal(byId(body, 'exploration-scope-capability').disabled, true);
  assert.match(byId(body, 'exploration-conversation-status').textContent, /^Your terminal is exploring this through Vertical Slicing \(Agile\)/);
  const hand = byId(body, 'exploration-hand-fill');
  assert.equal(hand.textContent, 'Fill this step by hand'); assert.match(hand.className, /hand-fill/);
  assert.equal(typeof hand.listeners.click, 'function');
  assert.equal(flow.fieldLocked('exploration', 'outcome'), true);
});

test('the waiting sentence never calls locked answers editable', async () => {
  const {waitingLine} = await import('../../glitch-idea/web/folds.js');
  const lockedFlow = {fieldLocked: () => true}, openFlow = {fieldLocked: () => false};
  assert.equal(waitingLine(lockedFlow, 'exploration'), 'Waiting for your terminal. Use "Fill this step by hand" to answer yourself.');
  assert.equal(waitingLine(lockedFlow, 'discovery'), 'Waiting for your terminal. Use "Fill this step by hand" to answer yourself.');
  assert.equal(waitingLine(openFlow, 'exploration'), 'Waiting for the initiating agent. Your answers remain editable.');
  assert.equal(waitingLine(lockedFlow, 'method'), 'Waiting for the initiating agent. Your answers remain editable.');
});

test('with no terminal nothing is locked and the notice says so', () => {
  const {body} = draw({agent: 'disconnected'});
  assert.equal(body.all().some(node => /\bfield-locked\b/.test(node.className)), false);
  assert.equal(byId(body, 'exploration-hand-fill'), undefined);
  assert.equal(byId(body, 'exploration-outcome').disabled, false);
  assert.equal(byId(body, 'exploration-conversation-status').textContent, 'No terminal is connected, so this step is yours to fill.');
});

test('a hand-released step is unlocked', () => {
  const {body} = draw({hand: true});
  assert.equal(body.all().some(node => /\bfield-locked\b/.test(node.className)), false);
  assert.equal(byId(body, 'exploration-hand-fill'), undefined);
});

test('Accept names what is missing while disabled and is silent when valid', () => {
  const bad = draw({agent: 'disconnected'});
  const accept = byId(bad.foot, 'exploration-accept'), why = byId(bad.foot, 'exploration-accept-reason');
  assert.equal(accept.disabled, true);
  assert.ok(why); assert.match(why.className, /accept-reason/); assert.match(why.textContent, /Add the desired result\./);
  assert.equal(accept['aria-describedby'], 'exploration-accept-reason'); assert.equal(accept.title, why.textContent);
  const good = draw({agent: 'disconnected', buffer: FILLED});
  const ok = byId(good.foot, 'exploration-accept');
  assert.equal(ok.disabled, false);
  assert.equal(byId(good.foot, 'exploration-accept-reason'), undefined);
  assert.equal(ok.title, undefined);
});

test('an appetite-led method keeps Accept off until the investment is set', () => {
  const blank = draw({agent: 'disconnected', method: 'appetite-led', buffer: FILLED});
  assert.equal(byId(blank.foot, 'exploration-accept').disabled, true);
  assert.match(byId(blank.foot, 'exploration-accept-reason').textContent, /Set the investment/);
  const set = draw({agent: 'disconnected', method: 'appetite-led', buffer: {...FILLED, investment: {cap: 3, unit: 'days', boundary: 'the core'}}});
  assert.equal(byId(set.foot, 'exploration-accept').disabled, false);
});
