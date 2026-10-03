// Method step: four equal cards, the APIV label, and a collapsible "Tell me more" per method.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web = new URL('../../glitch-idea/web/', import.meta.url);
const moduleUrl = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const foldsUrl = moduleUrl(await readFile(new URL('folds.js', web), 'utf8'));
const {Flow, STEPS} = await import(foldsUrl);
const methodSource = (await readFile(new URL('steps/method.js', web), 'utf8')).replace("'../folds.js'", JSON.stringify(foldsUrl));
const {render, APIV_LABEL} = await import(moduleUrl(methodSource));
const css = await readFile(new URL('styles.css', web), 'utf8');
const KEYS = ['bounded-plan', 'adaptive-slices', 'appetite-led', 'experiment-led'];

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

function draw() {
  const state = {ok: true, code: 'ok', session_id: 'session_' + '2'.repeat(32), idea_id: 'idea_' + '1'.repeat(32), idea_status: 'active', revision: 2, draft_version: 0, backlog_revision: 1,
    current_step: 'method', steps: Object.fromEntries(STEPS.map(({key}) => [key, ['capture', 'priorities', 'shape'].includes(key) ?
      {status: 'saved', accepted_revision: 2, evidence_id: 'e-' + key} : {status: key === 'method' ? 'current' : 'todo', accepted_revision: null, evidence_id: null}])),
    accepted: {capture: {raw_text: 'Words', workspace: {name: 'F', path: '/f', confirmed: true}}, priorities: {urgency: 6, importance: 7},
      shape: {outcome: 'O', scope: 'small-change', scope_reason: 'R', alternatives: [{route: 'A', reason: 'B'}], assumptions: [], next_slice: 'N', learning: []}},
    drafts: {}, draft: null, agent_status: 'connected', agent_generation: 'agent_' + '3'.repeat(32),
    capabilities: {agent: true, memory: true, uploads: false, handoff: false}, resume: {required: false, reason: null},
    proposal_sources: {method: {available: false, code: 'agent_unavailable', source: null}}, proposals: [], proposal_inventory: {total: 0, projected: 0, omitted: 0, content_omitted: 0, index_path: 'idea_' + '1'.repeat(32) + '.md'}};
  const flow = new Flow({state: async () => state, write: async () => ({ok: true})}, () => 'r'); flow.load(state);
  const body = element('div'), foot = element('footer');
  const field = (target, label, id, value) => {const input = element('input'); input.id = id; target.append(input); return input;};
  render({body, foot, flow, element, button, field, connected: true, edited: () => {}, handle: a => a, proposalInventory: () => {}});
  return body;
}

test('four method cards sit in one grid container', () => {
  const body = draw();
  const container = body.all().find(node => node.id === 'method-cards');
  assert.ok(container, 'method-cards container');
  assert.match(container.className, /\bmethod-cards\b/);
  const buttons = container.children;
  assert.deepEqual(buttons.map(node => node.id), KEYS.map(key => 'method-choice-' + key));
  for (const node of buttons) assert.equal(node['aria-pressed'], 'false');
  assert.equal(buttons[0].children[0].tag, 'strong');
  assert.match(buttons[0].text(), /Bounded plan \(APIV\)/);
  assert.match(buttons[1].text(), /Commit to: /);
  assert.equal(APIV_LABEL, 'APIV');
});

test('each method has a native collapsible Tell me more', () => {
  const body = draw();
  for (const key of KEYS) {
    const more = body.all().find(node => node.id === 'method-more-' + key);
    assert.ok(more, key); assert.equal(more.tag, 'details');
    assert.equal(more.children[0].tag, 'summary');
    assert.match(more.children[0].text(), /Tell me more about /);
    assert.ok(more.text().length > 120);
  }
  const bounded = body.all().find(node => node.id === 'method-more-bounded-plan');
  assert.match(bounded.text(), /APIV/);
});

test('the method grid gives every card the same size', () => {
  const rule = css.match(/\.method-cards\s*\{([^}]*)\}/);
  assert.ok(rule, '.method-cards rule exists');
  assert.match(rule[1], /display:\s*grid/);
  assert.match(rule[1], /grid-auto-rows:\s*1fr/);
  assert.match(rule[1], /repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
});
