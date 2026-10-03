// The Review step's summary: one card per step, decision first, plain labels.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const url = text => 'data:text/javascript;base64,' + Buffer.from(text).toString('base64');
const {render} = await import(url(await readFile(new URL('../../glitch-idea/web/steps/review.js', import.meta.url), 'utf8')));
class Node {
  constructor(tag = 'div', text = '') {this.tagName = tag.toUpperCase(); this.textContent = text; this.children = []; this.dataset = {}; this.className = '';}
  append(...nodes) {this.children.push(...nodes);}
  setAttribute(key, value) {this[key] = value;}
  addEventListener() {}
  all() {return [this, ...this.children.flatMap(child => child.all())];}
}
const element = (tag, text = '', className = '') => {const node = new Node(tag, text); node.className = className; return node;};
const button = (text, action, className) => element('button', text, className);
const accepted = () => ({
  capture: {raw_text: 'Plain idea', workspace: {name: 'demo space', path: '/absolute/demo', confirmed: true}},
  priorities: {urgency: 7, importance: 8},
  shape: {outcome: 'Faster review', scope: 'capability', scope_reason: 'Touches one screen', next_slice: 'Build the cards',
    alternatives: [{route: 'Do nothing', reason: 'Leaves the page flat'}], assumptions: ['Owner reads it'], learning: ['Does it read well']},
  method: {selection: 'appetite-led', reason: 'Time boxed', investment: {cap: 3, unit: 'days', boundary: 'One screen'}, experiment: null,
    memory: {status: 'searched_no_preference', sources: [], rationale: null}},
  visualize: {disposition: 'skipped', reason: 'Nothing visual', design_set_id: null, brief_evidence_id: null},
  assess: {assessment: {method: 'wsjf', version: '1', inputs: {value: 5, time_criticality: 3, enablement: 2, effort: 2}, basis: 'Owner estimate',
    assumptions: ['Stable scope'], confidence: 'medium', provenance: {source: 'human'}},
    position: {proposed_position: 1, actual_position: 2, neighbors: {before: null, after: null}, override_reason: 'Owner prefers it later'}}});
function draw(acceptedValue = accepted()) {
  const flow = {state: {accepted: acceptedValue, handoff: null}, busy: false, refreshUnauthorized: false, api: {csrf: 'x'},
    canGenerateHandoff: () => false, canCopyHandoff: () => false};
  const body = element('div'), foot = element('footer');
  render({flow, body, foot, element, button, handle: action => action, connected: true, isConnected: () => true});
  const summary = body.all().find(n => n.id === 'review-summary');
  return {summary, cards: summary.children.filter(n => n.tagName === 'SECTION')};
}
const text = node => node.children.length ? [node.textContent, ...node.children.map(text)].join('\n') : node.textContent;
const TITLES = ['Capture', 'Your priorities', 'Shape the outcome', 'Choose a methodology', 'Visualize', 'Assess and position'];

test('one card per step, in order, each with its heading', () => {
  const {cards} = draw();
  assert.deepEqual(cards.map(card => card.children[0].textContent), TITLES);
  assert.ok(cards.every(card => card.children[0].tagName === 'H3'));
});

test('the decision line is the first child after the heading', () => {
  const {cards} = draw();
  assert.ok(cards.every(card => card.children[1].className === 'review-decision'));
  assert.deepEqual(cards.map(card => card.children[1].textContent), [
    'Plain idea', 'Urgency 7 · Importance 8 (your ratings)', 'Faster review Scope: New capability.',
    'Appetite-led shaping — Time boxed', 'Skipped — Nothing visual', 'Position 2 in your backlog · WSJF 5']);
});

test('no raw JSON or snake_case keys appear in the summary', () => {
  const {summary} = draw(), all = text(summary);
  assert.doesNotMatch(all, /[{}\[\]"]/);
  assert.doesNotMatch(all, /\b[a-z]+_[a-z_]+\b/);
  assert.doesNotMatch(all, /\[object|undefined|null/);
});

test('the workspace renders as two plain lines, name then path', () => {
  const card = draw().cards[0], dts = card.all().filter(n => n.tagName === 'DT'), dds = card.all().filter(n => n.tagName === 'DD');
  assert.deepEqual(dts.map(n => n.textContent), ['Workspace', 'Path']);
  assert.deepEqual(dds.map(n => n.textContent), ['demo space', '/absolute/demo']);
});

test('hostile text stays literal and no live element is created', () => {
  const value = accepted(), evil = '<script>window.__pwn=1</script><img src=x onerror=y>';
  value.capture.raw_text = evil; value.capture.workspace.name = '<b>demo</b>';
  const {summary, cards} = draw(value);
  assert.equal(cards[0].children[1].textContent, evil);
  assert.ok(text(summary).includes('<b>demo</b>'));
  assert.equal(summary.all().filter(n => ['SCRIPT', 'IMG', 'IFRAME', 'B'].includes(n.tagName)).length, 0);
});

test('a long idea is shortened with the full text one click away', () => {
  const value = accepted(); value.capture.raw_text = 'word '.repeat(80).trim();
  const card = draw(value).cards[0];
  assert.ok(card.children[1].textContent.length <= 201 && card.children[1].textContent.endsWith('…'));
  const details = card.children[2]; assert.equal(details.tagName, 'DETAILS');
  assert.ok(details.all().some(n => n.textContent === value.capture.raw_text));
});

test('an unanswered step says Not answered yet. and nothing else', () => {
  const value = accepted(); value.visualize = null; delete value.assess;
  const {cards} = draw(value);
  for (const index of [4, 5]) {
    assert.equal(cards[index].children[1].textContent, 'Not answered yet.');
    assert.equal(cards[index].children.length, 2);
  }
  assert.equal(draw({}).cards.filter(card => card.children[1].textContent === 'Not answered yet.').length, 6);
});

test('missing assessment inputs read as Unknown in words', () => {
  const value = accepted(); value.assess.assessment.inputs.effort = null; value.assess.position.proposed_position = 2;
  assert.equal(draw(value).cards[5].children[1].textContent, 'Position 2 in your backlog · WSJF Unknown (missing inputs)');
});
