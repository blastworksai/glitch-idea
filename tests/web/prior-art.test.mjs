// CP6p: "Does it already exist?" in Discovery: section, rows, none box, locks, Accept blockers and Review.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web = new URL('../../glitch-idea/web/', import.meta.url);
const moduleUrl = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const foldsUrl = moduleUrl(await readFile(new URL('folds.js', web), 'utf8'));
const folds = await import(foldsUrl);
const {Flow, STEPS, acceptBlockers, validDiscovery, validDiscoveryAccepted, FILL_KEYS} = folds;
const load = async name => import(moduleUrl((await readFile(new URL('steps/' + name, web), 'utf8')).replace("'../folds.js'", JSON.stringify(foldsUrl))));
const discovery = await load('discovery.js'), review = await load('review.js');
const IDEA = 'idea_' + '1'.repeat(32);
const ROW = {name: 'Tapeo', link: 'https://example.test/tapeo', does: 'Seals lids', differs: 'Ours is reusable', licence: 'MIT'};
const BASE = {problem: 'Lids leak', audience: 'Cooks', workaround: 'Tape', evidence: 'Five complaints', kill_criteria: 'No repeat use',
  challenges: [{challenge: 'Is it real?', response: 'Yes'}]};

class Node {
  constructor(tag, text = '', className = '') {this.tag = tag; this.tagName = tag.toUpperCase(); this.textContent = text; this.className = className;
    this.children = []; this.listeners = {}; this.disabled = false; this.value = ''; this.dataset = {};}
  append(...children) {this.children.push(...children);}
  setAttribute(key, value) {this[key] = value;}
  addEventListener(key, fn) {this.listeners[key] = fn;}
  all() {return [this, ...this.children.flatMap(child => child.all())];}
  text() {return this.textContent + this.children.map(child => child.text()).join('');}
}
const element = (tag, text = '', className = '') => new Node(tag, text, className);
const button = (text, action, className = '') => {const node = element('button', text, className); node.addEventListener('click', action); return node;};

function draw({agent = 'disconnected', draft = null, hand = false} = {}) {
  const state = {ok: true, code: 'ok', session_id: 'session_' + '2'.repeat(32), idea_id: IDEA, idea_status: 'active', revision: 2, draft_version: 0, backlog_revision: 1,
    current_step: 'discovery', steps: Object.fromEntries(STEPS.map(({key}) => [key, ['capture', 'priorities', 'method'].includes(key) ?
      {status: 'saved', accepted_revision: 2, evidence_id: 'e-' + key} : {status: key === 'discovery' ? 'current' : 'todo', accepted_revision: null, evidence_id: null}])),
    accepted: {capture: {raw_text: 'Words', workspace: {name: 'F', path: '/f', confirmed: true}}, priorities: {urgency: 6, importance: 7}, method: {selection: 'adaptive-slices'}},
    drafts: draft ? {discovery: draft} : {}, draft: null, agent_status: agent, agent_generation: agent === 'connected' ? 'agent_' + '3'.repeat(32) : null,
    proposal_sources: {}, proposals: [], proposal_inventory: {total: 0, projected: 0, omitted: 0, content_omitted: 0, index_path: IDEA + '.md'}};
  if (hand) state.hand = {discovery: true, exploration: false};
  const flow = new Flow({state: async () => state, write: async () => ({ok: true})}, () => 'r'); flow.load(state);
  const edits = [];
  const body = element('div'), foot = element('footer');
  const field = (target, label, id, value, changed) => {const input = element('textarea'); input.id = id; input.label = label; input.value = value; input.changed = changed; target.append(input); return input;};
  const edited = (key, value) => {edits.push({key, value}); flow.edit(key, value);};
  discovery.render({body, foot, flow, element, button, field, connected: true, edited, handle: a => a, proposalInventory: () => {}});
  const all = [...body.all(), ...foot.all()];
  return {flow, body, foot, all, edits, byId: id => all.find(node => node.id === id)};
}

test('the section follows "How do they handle it today?" and precedes the evidence answer', () => {
  const ids = draw().all.map(node => node.id ?? '');
  assert.ok(ids.indexOf('discovery-workaround') < ids.indexOf('discovery-prior-art'));
  assert.ok(ids.indexOf('discovery-prior-art') < ids.indexOf('discovery-evidence'));
  const view = draw();
  assert.ok(view.all.some(node => node.tag === 'legend' && node.textContent === 'Does it already exist?'));
  assert.equal(view.byId('discovery-add-prior-art').textContent, 'Add a product');
  assert.equal(view.byId('discovery-prior-art-none').checked, false);
  assert.equal(view.byId('discovery-prior-art-searched'), undefined, 'Where did you look? stays hidden until the box is ticked');
});

test('rows carry Name, Link, What it does, How we differ, Licence and edit the buffer', () => {
  const view = draw({draft: {...BASE, prior_art: [ROW]}});
  const labels = ['name', 'link', 'does', 'differs', 'licence'].map(key => view.byId('discovery-prior-art-0-' + key).label);
  assert.deepEqual(labels, ['Name', 'Link', 'What it does', 'How we differ', 'Licence']);
  view.byId('discovery-prior-art-0-licence').changed('Apache-2.0');
  assert.deepEqual(view.edits.at(-1).value.prior_art, [{...ROW, licence: 'Apache-2.0'}]);
  view.byId('discovery-remove-prior-art-0').listeners.click();
  assert.deepEqual(view.edits.at(-1).value.prior_art, []);
  const empty = draw();
  empty.byId('discovery-add-prior-art').listeners.click();
  assert.deepEqual(empty.edits.at(-1).value.prior_art, [{name: '', link: '', does: '', differs: '', licence: ''}]);
  assert.equal(empty.all.filter(node => node.className === 'challenge-row').length, 0, 'product rows are not challenge rows');
});

test('Add stops at eight products', () => {
  const view = draw({draft: {...BASE, prior_art: Array.from({length: 8}, () => ROW)}});
  assert.equal(view.byId('discovery-add-prior-art').disabled, true);
});

test('the none box is refused with a plain reason while rows exist', () => {
  const view = draw({draft: {...BASE, prior_art: [ROW]}});
  const box = view.byId('discovery-prior-art-none');
  assert.equal(box.disabled, true);
  assert.match(view.byId('discovery-prior-art-none-reason').textContent, /Remove the products above/);
  assert.equal(box['aria-describedby'], 'discovery-prior-art-none-reason');
});

test('ticking none reveals Where did you look? and disables Add', () => {
  const view = draw();
  view.byId('discovery-prior-art-none').listeners.change({target: {checked: true}});
  assert.equal(view.edits.at(-1).value.prior_art_none, true);
  const ticked = draw({draft: {...BASE, prior_art_none: true, prior_art_searched: 'Web and GitHub'}});
  const where = ticked.byId('discovery-prior-art-searched');
  assert.equal(where.label, 'Where did you look?'); assert.equal(where.value, 'Web and GitHub');
  assert.equal(ticked.byId('discovery-add-prior-art').disabled, true);
  where.changed('Elsewhere'); assert.equal(ticked.edits.at(-1).value.prior_art_searched, 'Elsewhere');
});

test('Accept follows the contract: rows with name, differs and licence, or none with a place looked', () => {
  assert.equal(draw({draft: {...BASE, prior_art: [ROW]}}).byId('discovery-accept').disabled, false);
  assert.equal(draw({draft: {...BASE, prior_art_none: true, prior_art_searched: 'Web'}}).byId('discovery-accept').disabled, false);
  assert.equal(draw({draft: BASE}).byId('discovery-accept').disabled, true);
  assert.equal(draw({draft: {...BASE, prior_art_none: true}}).byId('discovery-accept').disabled, true);
  assert.equal(draw({draft: {...BASE, prior_art: [{...ROW, licence: ' '}]}}).byId('discovery-accept').disabled, true);
  // Link and what-it-does may stay empty; "Not stated" counts as a licence.
  assert.equal(draw({draft: {...BASE, prior_art: [{...ROW, link: '', does: '', licence: 'Not stated'}]}}).byId('discovery-accept').disabled, false);
  assert.equal(validDiscovery({...BASE, prior_art: [ROW], prior_art_none: true, prior_art_searched: 'x'}), false, 'none and rows together');
  assert.equal(validDiscovery({...BASE, prior_art: Array.from({length: 9}, () => ROW), prior_art_none: false}), false, 'nine rows');
});

test('blockers are plain sentences', () => {
  const full = {...BASE, prior_art: [], prior_art_none: false, prior_art_searched: ''};
  assert.deepEqual(acceptBlockers('discovery', full), ['Add a product that already exists, or tick Nothing comparable found and say where you looked.']);
  assert.deepEqual(acceptBlockers('discovery', {...full, prior_art: [{...ROW, name: '', differs: '', licence: ''}]}), ['Give product 1 a name, how we differ and its licence.']);
  assert.deepEqual(acceptBlockers('discovery', {...full, prior_art: [ROW, {...ROW, licence: ''}]}), ['Give product 2 its licence.']);
  assert.deepEqual(acceptBlockers('discovery', {...full, prior_art_none: true}), ['Say where you looked for something comparable.']);
  assert.deepEqual(acceptBlockers('discovery', {...full, prior_art: [ROW], prior_art_none: true, prior_art_searched: 'x'}),
    ['Remove the products you listed, or untick Nothing comparable found.']);
  assert.deepEqual(acceptBlockers('discovery', {...full, prior_art: [ROW]}), []);
});

test('an answer accepted before CP6p stays valid to read and renders as empty', () => {
  assert.equal(validDiscoveryAccepted(BASE), true);
  assert.equal(validDiscovery(BASE), false);
  const view = draw({draft: BASE});
  assert.equal(view.byId('discovery-prior-art-none').checked, false);
});

test('connected terminal: one lock on the section, released by the hand button; filling any field opens it', async () => {
  assert.deepEqual(FILL_KEYS.discovery.slice(-3), ['prior_art', 'prior_art_none', 'prior_art_searched']);
  const view = draw({agent: 'connected'});
  assert.equal(view.byId('discovery-add-prior-art').disabled, true);
  assert.equal(view.byId('discovery-prior-art-none').disabled, true);
  assert.equal(view.flow.fieldLocked('discovery', 'prior_art'), true);
  assert.equal(view.flow.fieldLocked('discovery', 'prior_art_none'), false);
  assert.equal(view.flow.fieldLocked('discovery', 'prior_art_searched'), false);
  assert.ok(view.all.some(node => node.className === 'field-locked' && node.all().some(n => n.id === 'discovery-prior-art')));
  const filled = draw({agent: 'connected', draft: {prior_art_none: true, prior_art_searched: 'Web'}});
  assert.equal(filled.flow.fieldLocked('discovery', 'prior_art'), false);
  const rows = draw({agent: 'connected', draft: {prior_art: [ROW]}});
  assert.equal(rows.flow.fieldLocked('discovery', 'prior_art'), false);
  assert.equal(rows.byId('discovery-prior-art-0-name').disabled, false);
  const by = draw({agent: 'connected', hand: true});
  assert.equal(by.byId('discovery-add-prior-art').disabled, false);
});

// ---- Review ----
function reviewCard(discoveryFields) {
  const accepted = {discovery: discoveryFields};
  const flow = {state: {accepted, handoff: null}, busy: false, refreshUnauthorized: false, api: {csrf: 'x'},
    canGenerateHandoff: () => false, canCopyHandoff: () => false};
  const body = element('div'), foot = element('footer');
  review.render({flow, body, foot, element, button, handle: action => action, connected: true, isConnected: () => true});
  return body.all().find(n => n.dataset?.step === 'discovery');
}
const dd = (card, label) => {const dts = card.all().filter(n => n.tagName === 'DT'); const at = dts.findIndex(n => n.textContent === label);
  return at < 0 ? null : card.all().filter(n => n.tagName === 'DD')[at];};

test('Review shows a small table of the products', () => {
  const card = reviewCard({...BASE, prior_art: [ROW, {...ROW, name: 'Seal-It', licence: 'Proprietary'}], prior_art_none: false, prior_art_searched: ''});
  const cell = dd(card, 'Does it already exist?');
  assert.ok(cell);
  const table = cell.all().find(n => n.tag === 'table');
  assert.ok(table);
  assert.deepEqual(table.children[0].children.map(n => n.textContent), ['Name', 'Link', 'What it does', 'How we differ', 'Licence']);
  assert.deepEqual(table.children[2].children.map(n => n.textContent), ['Seal-It', ROW.link, ROW.does, ROW.differs, 'Proprietary']);
});

test('Review says Nothing comparable found with where we looked, and Not checked for an older Discovery', () => {
  assert.equal(dd(reviewCard({...BASE, prior_art: [], prior_art_none: true, prior_art_searched: 'Web and GitHub'}), 'Does it already exist?').textContent,
    'Nothing comparable found (looked: Web and GitHub)');
  assert.equal(dd(reviewCard(BASE), 'Does it already exist?').textContent, 'Not checked');
});

test('the page reads prior art exactly as the service does (review cp6p-r1)', () => {
  // The legacy allowance applies only when all three fields are absent, as in idea_workflow LEGACY_ABSENT_OK.
  assert.equal(validDiscoveryAccepted({...BASE, prior_art_none: true}), false);
  assert.equal(validDiscoveryAccepted({...BASE, prior_art: [], prior_art_none: true, prior_art_searched: ''}), false);
  assert.equal(validDiscoveryAccepted({...BASE, prior_art: [ROW], prior_art_none: false, prior_art_searched: ''}), true);
  // A row carries all five keys, as the service's _prior_art requires.
  const {link, ...noLink} = ROW;
  assert.equal(validDiscovery({...BASE, prior_art: [noLink], prior_art_none: false, prior_art_searched: ''}), false);
  assert.equal(validDiscoveryAccepted({...BASE, prior_art: [noLink], prior_art_none: false, prior_art_searched: ''}), false);
});
