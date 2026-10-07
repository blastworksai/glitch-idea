// Discovery step: five labelled answers, challenges, terminal locks, hand release, and Accept reasons.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web = new URL('../../glitch-idea/web/', import.meta.url);
const moduleUrl = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const foldsUrl = moduleUrl(await readFile(new URL('folds.js', web), 'utf8'));
const {Flow, STEPS} = await import(foldsUrl);
const {render} = await import(moduleUrl((await readFile(new URL('steps/discovery.js', web), 'utf8')).replace("'../folds.js'", JSON.stringify(foldsUrl))));
const IDEA = 'idea_' + '1'.repeat(32);

class Node {
  constructor(tag, text = '', className = '') {this.tag = tag; this.textContent = text; this.className = className; this.children = []; this.listeners = {}; this.disabled = false; this.value = '';}
  append(...children) {this.children.push(...children);}
  setAttribute(key, value) {this[key] = value;}
  addEventListener(key, fn) {this.listeners[key] = fn;}
  all() {return [this, ...this.children.flatMap(child => child.all())];}
  text() {return this.textContent + this.children.map(child => child.text()).join('');}
}
const element = (tag, text = '', className = '') => new Node(tag, text, className);
const button = (text, action, className = '') => {const node = element('button', text, className); node.addEventListener('click', action); return node;};
const COMPLETE = {problem: 'Lids leak', audience: 'Cooks', workaround: 'Tape', evidence: 'Five complaints', kill_criteria: 'No repeat use',
  challenges: [{challenge: 'Is it real?', response: 'Yes, seen twice'}],
  prior_art: [{name: 'Tapeo', link: 'https://example.test/tapeo', does: 'Seals lids', differs: 'Ours is reusable', licence: 'MIT'}], prior_art_none: false, prior_art_searched: ''};

function draw({agent = 'connected', draft = null, hand = false} = {}) {
  const state = {ok: true, code: 'ok', session_id: 'session_' + '2'.repeat(32), idea_id: IDEA, idea_status: 'active', revision: 2, draft_version: 0, backlog_revision: 1,
    current_step: 'discovery', steps: Object.fromEntries(STEPS.map(({key}) => [key, ['capture', 'priorities', 'method'].includes(key) ?
      {status: 'saved', accepted_revision: 2, evidence_id: 'e-' + key} : {status: key === 'discovery' ? 'current' : 'todo', accepted_revision: null, evidence_id: null}])),
    accepted: {capture: {raw_text: 'Words', workspace: {name: 'F', path: '/f', confirmed: true}}, priorities: {urgency: 6, importance: 7}, method: {selection: 'adaptive-slices'}},
    drafts: draft ? {discovery: draft} : {}, draft: null, agent_status: agent, agent_generation: agent === 'connected' ? 'agent_' + '3'.repeat(32) : null,
    proposal_sources: {}, proposals: [], proposal_inventory: {total: 0, projected: 0, omitted: 0, content_omitted: 0, index_path: IDEA + '.md'}};
  if (hand) state.hand = {discovery: true};
  const flow = new Flow({state: async () => state, write: async () => ({ok: true})}, () => 'r'); flow.load(state);
  const released = [], edits = [];
  flow.releaseStep = async key => {released.push(key); return true;};
  const body = element('div'), foot = element('footer');
  const field = (target, label, id, value, changed) => {const input = element('textarea'); input.id = id; input.label = label; input.value = value; input.changed = changed; target.append(input); return input;};
  const edited = (key, value) => {edits.push({key, value}); flow.edit(key, value);};
  render({body, foot, flow, element, button, field, connected: true, edited, handle: a => a, proposalInventory: () => {}});
  const all = [...body.all(), ...foot.all()];
  return {flow, body, foot, all, released, edits, byId: id => all.find(node => node.id === id)};
}

test('five answers in order with their labels, then challenges', () => {
  const view = draw({agent: 'disconnected'});
  const inputs = view.all.filter(node => node.tag === 'textarea' && /^discovery-[a-z-]+$/.test(node.id));
  assert.deepEqual(inputs.map(node => node.id), ['discovery-problem', 'discovery-audience', 'discovery-workaround', 'discovery-evidence', 'discovery-kill-criteria']);
  assert.deepEqual(inputs.map(node => node.label), ['What is the problem?', 'Who has it?', 'How do they handle it today?', 'What evidence says it is needed?', 'What would make you stop?']);
  assert.ok(view.byId('discovery-add-challenge'));
  assert.match(view.body.text(), /At least one real challenge to how you framed the idea, with your answer to it/);
});

test('agent connected: fields wait for the terminal and the hand button releases the step', async () => {
  const view = draw();
  assert.match(view.byId('discovery-conversation-status').textContent, /Your terminal is asking the big questions/);
  assert.equal(view.byId('discovery-conversation-status').role, 'status');
  assert.equal(view.byId('discovery-problem').disabled, true);
  assert.ok(view.all.some(node => node.className === 'field-locked'));
  assert.match(view.body.text(), /Waiting for your terminal/);
  const hand = view.byId('discovery-hand-fill');
  assert.equal(hand.textContent, 'Fill this step by hand'); assert.equal(hand.className, 'hand-fill');
  await hand.listeners.click();
  assert.deepEqual(view.released, ['discovery']);
});

test('no agent: unlocked, with the no-terminal notice and no hand button', () => {
  const view = draw({agent: 'disconnected'});
  assert.equal(view.byId('discovery-conversation-status').textContent, 'No terminal is connected, so this step is yours to fill.');
  assert.equal(view.byId('discovery-problem').disabled, false);
  assert.equal(view.byId('discovery-hand-fill'), undefined);
  assert.doesNotMatch(view.body.text(), /Waiting for your terminal/);
});

test('add and remove challenge edit the buffer', () => {
  const view = draw({agent: 'disconnected'});
  view.byId('discovery-add-challenge').listeners.click();
  assert.deepEqual(view.edits.at(-1).value.challenges, [{challenge: '', response: ''}]);
  const again = draw({agent: 'disconnected', draft: {...COMPLETE}});
  assert.ok(again.byId('discovery-challenge-0-challenge') && again.byId('discovery-challenge-0-response'));
  assert.equal(again.all.filter(node => node.className === 'challenge-row').length, 1);
  again.byId('discovery-challenge-0-response').changed('Changed');
  assert.equal(again.edits.at(-1).value.challenges[0].response, 'Changed');
  again.byId('discovery-remove-challenge-0').listeners.click();
  assert.deepEqual(again.edits.at(-1).value.challenges, []);
});

test('Accept is disabled on empty and says why, linked by aria-describedby and title', () => {
  const view = draw({agent: 'disconnected'});
  const accept = view.byId('discovery-accept'), why = view.byId('discovery-accept-reason');
  assert.equal(accept.disabled, true);
  assert.ok(why && why.textContent.length > 0);
  assert.equal(accept['aria-describedby'], 'discovery-accept-reason');
  assert.equal(accept.title, why.textContent);
});

test('Accept is enabled on a complete fixture with no reason', () => {
  const view = draw({agent: 'disconnected', draft: COMPLETE});
  const accept = view.byId('discovery-accept');
  assert.equal(accept.disabled, false);
  assert.equal(view.byId('discovery-accept-reason'), undefined);
  assert.equal(accept['aria-describedby'], undefined);
  assert.equal(accept.title, undefined);
});
