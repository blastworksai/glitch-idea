// Exploration proposal flow, human path, archived reactivation and cap-text handling, against the real
// Exploration view and Flow with a small fake DOM. Ported from the former Shape step's test file: each
// protective assertion keeps its promise, re-expressed with Exploration ids and the accepted-method inputs.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web = new URL('../../glitch-idea/web/', import.meta.url);
const moduleUrl = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const foldsUrl = moduleUrl(await readFile(new URL('folds.js', web), 'utf8'));
const {Flow, STEPS} = await import(foldsUrl);
const {render} = await import(moduleUrl((await readFile(new URL('steps/exploration.js', web), 'utf8')).replace("'../folds.js'", JSON.stringify(foldsUrl))));
const app = (await readFile(new URL('app.js', web), 'utf8')).replace("'./folds.js'", JSON.stringify(foldsUrl))
  .replace("'./api.js'", JSON.stringify(moduleUrl(await readFile(new URL('api.js', web), 'utf8'))));
const {renderProposalInventory} = await import(moduleUrl(app));
const copy = value => structuredClone(value);
const IDEA = 'idea_' + '1'.repeat(32), SID = 'session_' + '2'.repeat(32), GEN = 'agent_' + '3'.repeat(32), PID = 'proposal_' + '4'.repeat(32);
const KEY = 'exploration';
const EXPLORATION = {outcome: 'Human outcome', alternatives: [{route: 'Keep the original route', reason: 'Its stated reason'}], assumptions: [],
  scope: 'small-change', scope_reason: 'One change', next_slice: 'One next slice', learning: [], investment: null, experiment: null,
  sketch: [{title: 'First item', why_next: 'It unblocks the rest', done_when: 'It runs', method: null}]};
const CAPTURE = {raw_text: 'Actual words', workspace: {name: 'Fixture', path: '/fixture', confirmed: true}};
const INVESTMENT = {cap: 4, unit: 'hours', boundary: 'Proposed boundary'};
const EXPERIMENT = {question: 'Which path?', evidence: 'One observed run', success_criterion: 'Human criterion', stop_rule: 'After one run'};

class Node {
  constructor(tag, text = '', className = '') {this.tag = tag; this.textContent = text; this.className = className; this.children = []; this.listeners = {}; this.disabled = false;}
  append(...children) {this.children.push(...children);}
  setAttribute(key, value) {this[key] = value;}
  addEventListener(key, fn) {this.listeners[key] = fn;}
  async click() {if (!this.disabled) return this.listeners.click?.({target: this});}
  input(value) {if (!this.disabled) {this.value = value; return this.listeners.input?.({target: this});}}
  all() {return [this, ...this.children.flatMap(child => child.all())];}
  text() {return this.textContent + this.children.map(child => child.text()).join('');}
}
const element = (tag, text = '', className = '') => new Node(tag, text, className);
const button = (text, action, className = '') => {const node = element('button', text, className); node.type = 'button'; node.addEventListener('click', action); return node;};

function harness({agent = 'connected', method = 'adaptive-slices'} = {}) {
  const connectedAgent = agent === 'connected';
  const done = new Set(['capture', 'priorities', 'method', 'discovery']);
  const state = {ok: true, code: 'ok', session_id: SID, idea_id: IDEA, idea_status: 'active', revision: 2, draft_version: 0, backlog_revision: 1, current_step: KEY,
    steps: Object.fromEntries(STEPS.map(({key}) => [key, done.has(key) ? {status: 'saved', accepted_revision: 2, evidence_id: 'e-' + key} :
      {status: key === KEY ? 'current' : 'todo', accepted_revision: null, evidence_id: null}])),
    accepted: {capture: copy(CAPTURE), priorities: {urgency: 6, importance: 7},
      method: {selection: method, reason: '', memory: {status: 'unavailable', sources: [], rationale: null, preferred_method: null}}, discovery: null, exploration: null},
    drafts: {}, draft: null, agent_status: connectedAgent ? 'connected' : 'disconnected', agent_generation: connectedAgent ? GEN : null,
    capabilities: {agent: connectedAgent, memory: connectedAgent, uploads: false, handoff: false}, resume: {required: false, reason: null},
    proposal_sources: {}, proposals: [], proposal_inventory: {total: 0, projected: 0, omitted: 0, content_omitted: 0, index_path: IDEA + '.md'}};
  const writes = [], requests = new Map();
  let counter = 0, body, foot, connected = true;
  const sources = () => {
    state.proposal_sources[KEY] = state.agent_status === 'connected' ? {available: true, code: 'ok', source: {accepted_revision: state.revision,
      draft_version: state.draft_version, data: {capture: copy(CAPTURE), ...(state.drafts[KEY] ? {[KEY]: copy(state.drafts[KEY])} : {})}, source_digest: 'a'.repeat(64)}} :
      {available: false, code: 'agent_unavailable', source: null};
  }; sources();
  const api = {state: async () => copy(state),
    release: async step => {state.hand = {discovery: false, exploration: false, ...(state.hand ?? {}), [step]: true}; return {ok: true, code: 'ok', idea_id: IDEA, hand: true};},
    write: async (operation, payload) => {
      writes.push({operation, payload: copy(payload)});
      if (operation === 'propose') {
        if (requests.has(payload.request_id)) assert.deepEqual(requests.get(payload.request_id), payload);
        else requests.set(payload.request_id, copy(payload));
        return {ok: true, code: 'ok', status: 'pending', write_state: 'not_applied', request_id: payload.request_id, session_id: SID, idea_id: IDEA,
          operation: payload.operation, accepted_revision: payload.expected_revision, draft_version: payload.expected_draft_version, source_digest: payload.source_digest};
      }
      if (operation === 'draft') {state.draft_version++; state.drafts[payload.step] = copy(payload.fields);}
      if (operation === 'accept') {
        state.idea_status = 'active'; state.revision++; state.accepted[payload.step] = copy(payload.fields); delete state.drafts[payload.step];
        state.current_step = 'visualize'; state.steps[payload.step] = {status: 'saved', accepted_revision: state.revision, evidence_id: 'accepted-' + payload.step};
      }
      sources();
      return {ok: true, code: 'ok', write_state: 'applied', request_id: payload.request_id, idea_id: IDEA, revision: state.revision,
        draft_version: state.draft_version, backlog_revision: 1, ...(payload.proposal_id ? {proposal_id: payload.proposal_id} : {})};
    }};
  const flow = new Flow(api, () => 'request-' + (++counter)); flow.load(state);
  const draw = () => {
    body = element('div'); foot = element('footer');
    const field = (target, label, id, value, changed, textarea = false) => {
      const wrap = element('div'), heading = element('label', label), input = element(textarea ? 'textarea' : 'input');
      heading.htmlFor = id; input.id = id; input.value = value ?? ''; input.disabled = flow.busy || !connected;
      input.addEventListener('input', event => changed(event.target.value)); wrap.append(heading, input); target.append(wrap); return input;
    };
    render({body, foot, flow, element, button, field, connected, edited: (key, value) => flow.edit(key, value), handle: action => async () => action(),
      proposalInventory: (target, key) => renderProposalInventory(target, key, flow, element)});
  };
  flow.onChange = draw; draw();
  return {flow, api, state, writes, sources, draw, get body() {return body;}, get foot() {return foot;},
    get: id => [...body.all(), ...foot.all()].find(node => node.id === id), connect: value => {connected = value; draw();},
    complete: (changes = {}, proposal = EXPLORATION) => {
      const request = [...requests.values()].at(-1);
      state.proposals = [{proposal_id: PID, request_id: request.request_id, operation: KEY, accepted_revision: request.expected_revision,
        draft_version: request.expected_draft_version, source_digest: request.source_digest, proposal: copy(proposal),
        stale: false, stale_reason: null, acceptance_eligible: true, acceptance_reason: null, content_omitted: false,
        evidence: {path: 'history/' + IDEA + '/metadata/' + 'b'.repeat(64) + '.md', sha256: 'c'.repeat(64)}, ...changes}];
      state.proposal_inventory = {total: 1, projected: 1, omitted: 0, content_omitted: state.proposals[0].content_omitted ? 1 : 0, index_path: IDEA + '.md'};
    }};
}
const BASE = {...EXPLORATION, sketch: copy(EXPLORATION.sketch)};
// Disconnected-agent harness whose buffer already holds a valid Exploration for the given method inputs.
const filled = (method, extra = {}) => {const h = harness({agent: 'disconnected', method}); h.flow.edit(KEY, {...copy(BASE), ...extra}); return h;};
const requested = async (h, changes, proposal) => {await h.flow.requestProposal(KEY); h.complete(changes, proposal); await h.flow.refreshAgent();};

test('empty Exploration has no fabricated routes, scope or sketch; explicit manual edits accept', async () => {
  const h = harness({agent: 'disconnected'}); assert.equal(h.get('exploration-accept').disabled, true);
  assert.equal(h.flow.buffers.exploration.scope, null); assert.deepEqual(h.flow.buffers.exploration.alternatives, []); assert.deepEqual(h.flow.buffers.exploration.sketch, []);
  assert.equal(h.get('exploration-alternative-1-route'), undefined); assert.equal(h.get('exploration-sketch-0-title'), undefined);
  assert.ok(h.body.all().some(node => node.textContent === 'Alternatives, including a simpler route'));
  h.get('exploration-outcome').input('My words'); await h.get('exploration-scope-small-change').click();
  h.get('exploration-scope-reason').input('My scope reason'); h.get('exploration-next-slice').input('My next slice');
  h.get('exploration-alternative-0-route').input('My route'); h.get('exploration-alternative-0-reason').input('My reason');
  assert.equal(h.get('exploration-accept').disabled, true, 'a sketch is still required');
  await h.get('exploration-add-sketch').click(); assert.deepEqual(h.flow.buffers.exploration.sketch, [{title: '', why_next: '', done_when: '', method: null}]);
  h.get('exploration-sketch-0-title').input('My item'); h.get('exploration-sketch-0-done-when').input('My done');
  assert.equal(h.get('exploration-accept').disabled, false); assert.equal(h.writes.length, 0);
  await h.get('exploration-accept').click(); assert.equal(h.writes.at(-1).operation, 'accept'); assert.equal(h.writes.at(-1).payload.step, 'exploration');
  assert.equal(h.writes.at(-1).payload.proposal_id, null); assert.equal(h.writes.at(-1).payload.fields.outcome, 'My words');
  assert.equal(h.flow.status('exploration'), 'saved');
});

test('a connected terminal locks the empty step; taking it by hand opens the human path with no suggestion', async () => {
  const h = harness(); assert.equal(h.get('exploration-outcome').disabled, true);
  assert.ok(h.body.all().some(node => /\bfield-locked\b/.test(node.className)));
  await h.get('exploration-hand-fill').click();
  assert.equal(h.get('exploration-outcome').disabled, false); assert.equal(h.get('exploration-hand-fill'), undefined);
  h.get('exploration-outcome').input('By hand'); assert.equal(h.flow.buffers.exploration.outcome, 'By hand');
  assert.equal(h.writes.length, 0);
});

test('archived Exploration stays reachable and explicitly reactivates only on accepting the redone answers', async () => {
  const h = harness(); assert.equal(h.get('exploration-accept').textContent, 'Accept and continue');
  assert.equal(h.get('exploration-reactivation-notice'), undefined);
  h.state.idea_status = 'archived'; h.state.accepted.exploration = copy(EXPLORATION);
  h.state.steps.exploration = {status: 'saved', accepted_revision: 2, evidence_id: 'immutable-archived-exploration'};
  h.flow.load(copy(h.state));
  assert.equal(h.flow.canOpen('exploration'), true); assert.equal(h.flow.open('exploration'), true);
  assert.equal(h.get('exploration-reactivation-notice').role, 'status');
  assert.match(h.get('exploration-reactivation-notice').textContent, /Accepting a redone Exploration reactivates/);
  assert.match(h.get('exploration-reactivation-notice').textContent, /archived plan evidence remains unchanged/);
  assert.equal(h.get('exploration-accept').textContent, 'Reactivate idea and accept Exploration');
  h.flow.edit(KEY, copy(EXPLORATION)); h.get('exploration-outcome').input('Explicit redone Exploration');
  assert.equal(h.state.idea_status, 'archived'); assert.equal(h.writes.length, 0);
  assert.equal(h.get('exploration-accept').disabled, false);
  await h.get('exploration-accept').click(); assert.equal(h.writes.length, 1);
  assert.equal(h.writes[0].operation, 'accept'); assert.equal(h.writes[0].payload.step, 'exploration');
  assert.equal(h.writes[0].payload.fields.outcome, 'Explicit redone Exploration'); assert.equal(h.writes[0].payload.proposal_id, null);
  assert.equal(h.state.idea_status, 'active'); assert.equal(h.flow.state.idea_status, 'active');
  assert.equal(h.flow.pending, null); assert.equal(h.flow.status('exploration'), 'saved');
  assert.equal(h.get('exploration-reactivation-notice'), undefined); assert.equal(h.get('exploration-accept').textContent, 'Accept and continue');
});

test('archived Exploration reactivation retains offline and paused acceptance refusal', async () => {
  for (const blocked of ['offline', 'paused']) {
    const h = harness(); h.state.idea_status = 'archived'; h.flow.load(copy(h.state)); h.flow.edit(KEY, copy(EXPLORATION));
    if (blocked === 'offline') h.connect(false); else {h.flow.paused = true; h.draw();}
    assert.equal(h.get('exploration-accept').textContent, 'Reactivate idea and accept Exploration');
    assert.equal(h.get('exploration-accept').disabled, true); await h.get('exploration-accept').click();
    assert.equal(h.writes.length, 0); assert.equal(h.state.idea_status, 'archived');
  }
});

test('partial durable draft renders missing containers empty and invents no content', async () => {
  const h = harness({agent: 'disconnected'}); h.state.drafts.exploration = {outcome: 'Unfinished saved thought', assumptions: null, learning: null, sketch: null};
  h.flow.load(h.state);
  assert.equal(h.get('exploration-outcome').value, 'Unfinished saved thought');
  assert.equal(h.get('exploration-assumptions-0'), undefined); assert.equal(h.get('exploration-learning-0'), undefined);
  assert.equal(h.get('exploration-sketch-0-title'), undefined); assert.equal(h.get('exploration-alternative-1-route'), undefined);
  await h.get('exploration-scope-project').click(); h.get('exploration-scope-reason').input('Human reason'); h.get('exploration-next-slice').input('Human next slice');
  h.get('exploration-alternative-0-route').input('Human route'); h.get('exploration-alternative-0-reason').input('Human route reason');
  await h.get('exploration-add-sketch').click(); h.get('exploration-sketch-0-title').input('Human item'); h.get('exploration-sketch-0-done-when').input('Human done');
  assert.deepEqual(h.flow.buffers.exploration.assumptions, []); assert.deepEqual(h.flow.buffers.exploration.learning, []);
  assert.equal(h.get('exploration-accept').disabled, false); assert.equal(h.flow.buffers.exploration.outcome, 'Unfinished saved thought');
});

test('request waits with locked fields; reply stays separate until Use, edited copy accepts', async () => {
  const h = harness(); await h.flow.requestProposal(KEY);
  assert.equal(h.get('exploration-proposal-status').role, 'status'); assert.match(h.get('exploration-proposal-status').textContent, /Waiting/);
  assert.equal(h.get('exploration-outcome').disabled, true, 'terminal-guided fields are locked while the terminal has the step');
  assert.ok(h.get('exploration-hand-fill')); assert.equal(h.flow.status('exploration'), 'current');
  h.flow.edit(KEY, {...h.flow.buffers.exploration, outcome: 'Unsent user edit'}); h.complete(); await h.flow.refreshAgent();
  assert.equal(h.flow.buffers.exploration.outcome, 'Unsent user edit'); assert.equal(h.flow.selectedProposals.exploration, undefined);
  await h.get('exploration-use-proposal-' + PID).click(); assert.equal(h.flow.buffers.exploration.outcome, EXPLORATION.outcome);
  assert.deepEqual(h.flow.buffers.exploration.sketch, EXPLORATION.sketch);
  assert.equal(h.flow.selectedProposals.exploration, PID); assert.equal(h.flow.status('exploration'), 'unsaved');
  assert.ok(h.get('exploration-manual'));
  h.get('exploration-outcome').input('Human revised suggestion'); await h.get('exploration-accept').click();
  assert.equal(h.writes.at(-1).payload.fields.outcome, 'Human revised suggestion'); assert.equal(h.writes.at(-1).payload.proposal_id, PID);
});

test('arrays and hostile text survive editing without an inferred simpler route or HTML', async () => {
  const h = harness({agent: 'disconnected'}); const hostile = '<img src=x onerror=alert(1)>\nOriginal second line';
  h.flow.edit(KEY, {...copy(EXPLORATION), alternatives: [{route: hostile, reason: 'Original reason'}, {route: 'Other route', reason: 'Other reason'}],
    assumptions: ['First risk\ncontinued', 'Second risk'], learning: ['Recorded learning'],
    sketch: [{title: hostile, why_next: 'Why', done_when: 'Done', method: null}]});
  assert.equal(h.get('exploration-alternative-0-route').value, hostile); assert.equal(h.get('exploration-sketch-0-title').value, hostile);
  h.get('exploration-alternative-1-reason').input('Edited second reason'); h.get('exploration-assumptions-1').input('Edited second risk');
  assert.equal(h.flow.buffers.exploration.alternatives[0].route, hostile); assert.equal(h.flow.buffers.exploration.assumptions[0], 'First risk\ncontinued');
  assert.equal(h.flow.buffers.exploration.alternatives[1].reason, 'Edited second reason');
  await h.get('exploration-remove-learning-0').click(); assert.deepEqual(h.flow.buffers.exploration.learning, []);
  await h.get('exploration-add-learning').click(); assert.deepEqual(h.flow.buffers.exploration.learning, ['']);
  assert.equal(h.get('exploration-accept').disabled, true); h.get('exploration-learning-0').input('Human learning');
  assert.equal(h.get('exploration-accept').disabled, false); assert.equal(h.body.all().filter(node => node.tag === 'img').length, 0);
});

test('hostile suggestion text is shown as literal text, never as markup, and Use copies it verbatim', async () => {
  const h = harness(); const hostile = '<img src=x onerror=alert(1)><script>alert(2)</script>\nsecond line';
  const hostileProposal = {...copy(EXPLORATION), outcome: hostile, assumptions: [hostile], learning: [hostile],
    alternatives: [{route: hostile, reason: hostile}], scope_reason: hostile, next_slice: hostile, sketch: [{title: hostile, why_next: hostile, done_when: hostile, method: null}]};
  await requested(h, {}, hostileProposal);
  const shown = h.get('exploration-proposal-' + PID); assert.ok(shown);
  assert.ok(shown.all().some(node => node.tag === 'p' && node.textContent === 'Desired result: ' + hostile));
  assert.ok(shown.all().some(node => node.textContent === 'Uncertainty or risk: ' + hostile));
  assert.ok(shown.all().some(node => node.textContent === 'Alternative: ' + hostile + ' — ' + hostile));
  assert.equal([...h.body.all(), ...h.foot.all()].some(node => ['img', 'script', 'a'].includes(node.tag) || Object.hasOwn(node, 'innerHTML')), false);
  await h.get('exploration-use-proposal-' + PID).click();
  assert.equal(h.get('exploration-outcome').value, hostile); assert.equal(h.get('exploration-sketch-0-title').value, hostile);
  assert.equal(h.flow.buffers.exploration.outcome, hostile);
  assert.equal([...h.body.all()].some(node => ['img', 'script'].includes(node.tag)), false);
});

test('uncertain request exposes explicit identical retry and stop-waiting controls', async () => {
  const h = harness(), original = h.api.write; let first = true;
  h.api.write = async (...args) => {const result = await original(...args); if (first) {first = false; throw Object.assign(new Error('private diagnostic'), {code: 'connection_lost', uncertain: true});} return result;};
  await h.flow.requestProposal(KEY); assert.match(h.get('exploration-proposal-status').textContent, /may have reached/);
  assert.ok(h.get('exploration-retry')); assert.equal(h.writes.length, 1);
  assert.equal(h.body.all().some(node => node.textContent.includes('private diagnostic')), false);
  await h.get('exploration-retry').click(); assert.deepEqual(h.writes[0], h.writes[1]);
  await h.get('exploration-stop-waiting').click(); assert.equal(h.flow.proposalPending, null);
  assert.equal(h.writes.length, 2); assert.equal(h.flow.canPropose(KEY), true);
});

test('generation drift disables linked accept until explicit manual choice; disconnected editor stays usable', async () => {
  const h = harness(); await requested(h);
  await h.get('exploration-use-proposal-' + PID).click(); h.state.agent_generation = 'agent_' + '5'.repeat(32);
  h.state.proposals[0] = {...h.state.proposals[0], stale: true, stale_reason: 'wrong_generation', acceptance_eligible: false, acceptance_reason: 'wrong_generation'};
  await h.flow.refreshAgent(); assert.equal(h.get('exploration-use-proposal-' + PID).disabled, true); assert.equal(h.get('exploration-accept').disabled, true);
  assert.ok(h.body.all().some(node => node.textContent === 'This suggestion belongs to an earlier agent session.'));
  await h.get('exploration-manual').click(); assert.equal(h.get('exploration-accept').disabled, false);
  assert.equal(h.flow.selectedProposals.exploration, undefined);
  h.state.agent_status = 'disconnected'; h.state.capabilities.agent = false; h.state.capabilities.memory = false;
  h.state.resume = {required: true, reason: 'agent_disconnected'}; h.sources(); await h.flow.refreshAgent();
  assert.equal(h.flow.canPropose(KEY), false); assert.equal(h.get('exploration-outcome').disabled, false); assert.equal(h.get('exploration-accept').disabled, false);
  h.connect(false); assert.equal(h.get('exploration-outcome').disabled, true); assert.equal(h.get('exploration-accept').disabled, true);
});

test('agent drop after Use: one explicit foot button accepts my own answers and unlinks the suggestion', async () => {
  const h = harness(); await requested(h); await h.get('exploration-use-proposal-' + PID).click();
  assert.equal(h.get('exploration-accept-own'), undefined, 'no extra door while the suggestion is acceptable');
  h.state.agent_status = 'disconnected'; h.state.capabilities.agent = false; h.state.resume = {required: true, reason: 'agent_disconnected'};
  h.state.proposals[0] = {...h.state.proposals[0], acceptance_eligible: false, acceptance_reason: 'agent_unavailable'}; h.sources(); await h.flow.refreshAgent();
  assert.equal(h.get('exploration-accept').disabled, true);
  const own = h.get('exploration-accept-own'); assert.ok(own, 'the door sits beside Accept'); assert.equal(own.disabled, false);
  assert.match(h.get('exploration-accept-own-reason').textContent, /can no longer be accepted.*kept/);
  await own.click();
  const accepted = h.writes.filter(write => write.operation === 'accept');
  assert.equal(accepted.length, 1); assert.equal(accepted[0].payload.proposal_id, null, 'accepted as my own, not linked');
  assert.equal(accepted[0].payload.step, 'exploration'); assert.deepEqual(accepted[0].payload.fields, EXPLORATION, 'exactly the fields on screen');
  assert.equal(h.flow.selectedProposals.exploration, undefined);
});

test('omitted suggestion bodies expose real Markdown paths as text and cannot be copied', async () => {
  const h = harness(); await h.flow.requestProposal(KEY); h.complete({proposal: null, content_omitted: true, acceptance_eligible: false, acceptance_reason: 'projection_omitted'});
  h.state.proposal_inventory.total = 4; h.state.proposal_inventory.omitted = 3; await h.flow.refreshAgent();
  assert.equal(h.get('exploration-use-proposal-' + PID).disabled, true);
  assert.equal(h.get('exploration-proposal-inventory').role, 'status'); assert.match(h.get('exploration-proposal-index-path').textContent, /All immutable suggestion links/);
  assert.ok(h.get('exploration-proposal-evidence-' + PID).textContent.endsWith(h.state.proposals[0].evidence.path));
  assert.ok(h.body.all().some(node => node.textContent === 'This suggestion is saved in Markdown; its body is omitted from this view.'));
  assert.equal(h.body.all().some(node => node.tag === 'a' || node.href), false);
  await h.get('exploration-use-proposal-' + PID).click();
  assert.equal(h.flow.selectedProposals.exploration, undefined); assert.equal(h.flow.buffers.exploration.outcome, '');
});

test('appetite-led method: Accept needs the investment, validated against the accepted method', async () => {
  const h = filled('appetite-led'); assert.equal(h.get('exploration-investment-cap').value, '');
  assert.equal(h.get('exploration-accept').disabled, true); assert.match(h.get('exploration-accept-reason').textContent, /Set the investment/);
  h.get('exploration-investment-cap').input('0'); h.get('exploration-investment-unit').input('sessions'); h.get('exploration-investment-boundary').input('Human limit');
  assert.equal(h.get('exploration-accept').disabled, true); h.get('exploration-investment-cap').input('3'); assert.equal(h.get('exploration-accept').disabled, false);
  await h.get('exploration-accept').click(); assert.deepEqual(h.writes.at(-1).payload.fields.investment, {cap: 3, unit: 'sessions', boundary: 'Human limit'});
  const other = filled('adaptive-slices'); assert.equal(other.get('exploration-investment-cap'), undefined); assert.equal(other.get('exploration-accept').disabled, false);
});

test('experiment-led method: Accept needs all four experiment answers', async () => {
  const h = filled('experiment-led'); assert.equal(h.get('exploration-accept').disabled, true);
  assert.match(h.get('exploration-accept-reason').textContent, /all four experiment answers/);
  h.get('exploration-experiment-question').input('Which path?'); h.get('exploration-experiment-evidence').input('One observed run');
  h.get('exploration-experiment-success-criterion').input('Human criterion'); assert.equal(h.get('exploration-accept').disabled, true);
  h.get('exploration-experiment-stop-rule').input('After one run'); assert.equal(h.get('exploration-accept').disabled, false);
  await h.get('exploration-accept').click(); assert.deepEqual(h.writes.at(-1).payload.fields.experiment, EXPERIMENT);
});

test('a leftover investment or experiment cannot be accepted for another method and clears only on request', async () => {
  const h = filled('adaptive-slices', {investment: copy(INVESTMENT)});
  assert.equal(h.get('exploration-accept').disabled, true); assert.match(h.get('exploration-accept-reason').textContent, /Clear the investment/);
  assert.equal(h.get('exploration-investment-cap'), undefined);
  await h.get('exploration-clear-investment').click(); assert.equal(h.flow.buffers.exploration.investment, null);
  assert.equal(h.get('exploration-accept').disabled, false); assert.equal(h.get('exploration-clear-investment'), undefined);
  const e = filled('appetite-led', {investment: copy(INVESTMENT), experiment: copy(EXPERIMENT)});
  assert.match(e.get('exploration-accept-reason').textContent, /Clear the experiment/);
  await e.get('exploration-clear-experiment').click(); assert.equal(e.flow.buffers.exploration.experiment, null);
  assert.equal(e.flow.buffers.exploration.investment.cap, 4); assert.equal(e.get('exploration-accept').disabled, false);
});

test('reload and Use keep the human budget until a suggestion is used, and the human edit is what is accepted', async () => {
  const h = harness({method: 'appetite-led'}); h.state.drafts.exploration = {...copy(BASE), investment: {cap: 7, unit: 'weeks', boundary: 'My saved boundary'}};
  h.flow.load(h.state); h.sources();
  assert.equal(h.get('exploration-investment-cap').value, '7'); assert.equal(h.flow.buffers.exploration.investment.cap, 7);
  await h.flow.requestProposal(KEY); h.complete({}, {...copy(BASE), outcome: 'Proposed outcome', investment: copy(INVESTMENT)}); await h.flow.refreshAgent();
  assert.deepEqual(h.flow.buffers.exploration.investment, {cap: 7, unit: 'weeks', boundary: 'My saved boundary'}, 'a reply alone changes nothing');
  assert.equal(h.get('exploration-investment-cap').value, '7');
  await h.get('exploration-use-proposal-' + PID).click(); assert.equal(h.flow.buffers.exploration.outcome, 'Proposed outcome');
  assert.equal(h.get('exploration-investment-cap').value, '4', 'Use copies the proposal as a draft, and the field shows it');
  h.get('exploration-investment-cap').input('7'); h.get('exploration-investment-unit').input('weeks'); h.get('exploration-investment-boundary').input('My saved boundary');
  await h.get('exploration-accept').click();
  assert.deepEqual(h.writes.at(-1).payload.fields.investment, {cap: 7, unit: 'weeks', boundary: 'My saved boundary'}); assert.equal(h.writes.at(-1).payload.proposal_id, PID);
});

const appetite = () => filled('appetite-led', {investment: {cap: null, unit: 'hours', boundary: 'Human boundary'}});

test('cap incremental decimals retain raw text across every render and poll', async () => {
  for (const [pieces, expected] of [[['1', '.', '5'], 1.5], [['0', '.', '5'], 0.5], [['1', ',', '5'], 1.5]]) {
    const h = appetite();
    assert.equal(h.get('exploration-investment-cap').type, 'text'); assert.equal(h.get('exploration-investment-cap').inputMode, 'decimal');
    let raw = '';
    for (const piece of pieces) {
      raw += piece; h.get('exploration-investment-cap').input(raw);
      assert.equal(h.get('exploration-investment-cap').value, raw);
      h.draw(); assert.equal(h.get('exploration-investment-cap').value, raw);
      await h.flow.refreshAgent(); assert.equal(h.get('exploration-investment-cap').value, raw);
      if (raw.endsWith('.') || raw.endsWith(',') || raw === '0') {
        assert.equal(h.flow.buffers.exploration.investment.cap, null); assert.equal(h.get('exploration-accept').disabled, true);
      }
    }
    assert.equal(h.flow.buffers.exploration.investment.cap, expected);
    await h.get('exploration-accept').click(); assert.equal(h.writes.at(-1).payload.fields.investment.cap, expected);
  }
});

test('invalid and partial cap text cannot flip sign, silently validate, or submit invalid numeric draft', async () => {
  const h = appetite();
  for (const raw of ['-', '-5', '', '0', '1.', '0.', '.', '1,,5', '1,000,000', '1.000,5', 'Infinity', '1e3', '1000000000001']) {
    h.get('exploration-investment-cap').input(raw); assert.equal(h.get('exploration-investment-cap').value, raw, 'raw text kept: ' + raw);
    assert.equal(h.flow.buffers.exploration.investment.cap, null, 'cap refused: ' + raw); assert.equal(h.get('exploration-accept').disabled, true);
    await h.flow.save(KEY, true);
    assert.equal(h.writes.at(-1).payload.fields.investment.cap, null); assert.equal(h.get('exploration-investment-cap').value, raw);
  }
  h.get('exploration-investment-cap').input('1000000000000'); assert.equal(h.flow.buffers.exploration.investment.cap, 1e12);
});

test('canonical cap loads synchronize text while idea and Flow affinity prevent stale edits leaking', async () => {
  const h = appetite(); h.get('exploration-investment-cap').input('1.');
  h.state.drafts.exploration = {...copy(BASE), investment: {cap: 2.75, unit: 'hours', boundary: 'Loaded limit'}};
  h.flow.load(h.state); assert.equal(h.get('exploration-investment-cap').value, '2.75');
  h.get('exploration-investment-cap').input('0.');
  const other = copy(h.state); other.idea_id = 'idea_' + '6'.repeat(32); other.proposal_inventory.index_path = other.idea_id + '.md'; other.drafts.exploration.investment.cap = null;
  h.flow.load(other); assert.equal(h.get('exploration-investment-cap').value, '');
  h.get('exploration-investment-cap').input('3.');
  const independent = appetite(); assert.equal(independent.get('exploration-investment-cap').value, '');
  // Leaving appetite-led drops the raw text; coming back to a null cap starts empty again.
  h.state.accepted.method.selection = 'adaptive-slices'; h.flow.load(copy(h.state)); assert.equal(h.get('exploration-investment-cap'), undefined);
  h.state.accepted.method.selection = 'appetite-led'; const back = copy(h.state); back.idea_id = other.idea_id; back.proposal_inventory.index_path = other.idea_id + '.md';
  back.drafts.exploration.investment.cap = null; h.flow.load(back);
  assert.equal(h.get('exploration-investment-cap').value, '');
});
