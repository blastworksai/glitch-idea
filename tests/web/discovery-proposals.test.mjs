// Discovery proposal flow, human path and archived reactivation, against the real Discovery view and Flow
// with a small fake DOM. Equivalents of the proposal-flow cases that used to be proven on the Shape step.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web = new URL('../../glitch-idea/web/', import.meta.url);
const moduleUrl = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const foldsUrl = moduleUrl(await readFile(new URL('folds.js', web), 'utf8'));
const {Flow, STEPS} = await import(foldsUrl);
const {render} = await import(moduleUrl((await readFile(new URL('steps/discovery.js', web), 'utf8')).replace("'../folds.js'", JSON.stringify(foldsUrl))));
const app = (await readFile(new URL('app.js', web), 'utf8')).replace("'./folds.js'", JSON.stringify(foldsUrl))
  .replace("'./api.js'", JSON.stringify(moduleUrl(await readFile(new URL('api.js', web), 'utf8'))));
const {renderProposalInventory} = await import(moduleUrl(app));
const copy = value => structuredClone(value);
const IDEA = 'idea_' + '1'.repeat(32), SID = 'session_' + '2'.repeat(32), GEN = 'agent_' + '3'.repeat(32), PID = 'proposal_' + '4'.repeat(32);
const KEY = 'discovery';
const DISCOVERY = {problem: 'Lids leak', audience: 'Cooks', workaround: 'Tape', evidence: 'Five complaints', kill_criteria: 'No repeat use',
  challenges: [{challenge: 'Is it real?', response: 'Yes, seen twice'}],
  prior_art: [{name: 'Tapeo', link: 'https://example.test/tapeo', does: 'Seals lids', differs: 'Ours is reusable', licence: 'MIT'}], prior_art_none: false, prior_art_searched: ''};
const CAPTURE = {raw_text: 'Actual words', workspace: {name: 'Fixture', path: '/fixture', confirmed: true}};

class Node {
  constructor(tag, text = '', className = '') {this.tag = tag; this.textContent = text; this.className = className; this.children = []; this.listeners = {}; this.disabled = false;}
  append(...children) {this.children.push(...children);}
  setAttribute(key, value) {this[key] = value;}
  addEventListener(key, fn) {this.listeners[key] = fn;}
  async click() {if (!this.disabled) return this.listeners.click?.({target: this});}
  input(value) {if (!this.disabled) {this.value = value; return this.listeners.input?.({target: this});}}
  all() {return [this, ...this.children.flatMap(child => child.all())];}
}
const element = (tag, text = '', className = '') => new Node(tag, text, className);
const button = (text, action, className = '') => {const node = element('button', text, className); node.type = 'button'; node.addEventListener('click', action); return node;};

function harness({agent = 'connected'} = {}) {
  const connectedAgent = agent === 'connected';
  const done = new Set(['capture', 'priorities', 'method']);
  const state = {ok: true, code: 'ok', session_id: SID, idea_id: IDEA, idea_status: 'active', revision: 2, draft_version: 0, backlog_revision: 1, current_step: KEY,
    steps: Object.fromEntries(STEPS.map(({key}) => [key, done.has(key) ? {status: 'saved', accepted_revision: 2, evidence_id: 'e-' + key} :
      {status: key === KEY ? 'current' : 'todo', accepted_revision: null, evidence_id: null}])),
    accepted: {capture: copy(CAPTURE), priorities: {urgency: 6, importance: 7},
      method: {selection: 'adaptive-slices', reason: '', memory: {status: 'unavailable', sources: [], rationale: null, preferred_method: null}}, discovery: null},
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
        state.current_step = 'exploration'; state.steps[payload.step] = {status: 'saved', accepted_revision: state.revision, evidence_id: 'accepted-' + payload.step};
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
    complete: (changes = {}, proposal = DISCOVERY) => {
      const request = [...requests.values()].at(-1);
      state.proposals = [{proposal_id: PID, request_id: request.request_id, operation: KEY, accepted_revision: request.expected_revision,
        draft_version: request.expected_draft_version, source_digest: request.source_digest, proposal: copy(proposal),
        stale: false, stale_reason: null, acceptance_eligible: true, acceptance_reason: null, content_omitted: false,
        evidence: {path: 'history/' + IDEA + '/metadata/' + 'b'.repeat(64) + '.md', sha256: 'c'.repeat(64)}, ...changes}];
      state.proposal_inventory = {total: 1, projected: 1, omitted: 0, content_omitted: state.proposals[0].content_omitted ? 1 : 0, index_path: IDEA + '.md'};
    }};
}
const requested = async (h, changes, proposal) => {await h.flow.requestProposal(KEY); h.complete(changes, proposal); await h.flow.refreshAgent();};
const ANSWER_IDS = [['problem', 'discovery-problem'], ['audience', 'discovery-audience'], ['workaround', 'discovery-workaround'],
  ['evidence', 'discovery-evidence'], ['kill_criteria', 'discovery-kill-criteria']];

test('empty Discovery has no fabricated answers or challenges; explicit manual edits accept', async () => {
  const h = harness({agent: 'disconnected'}); assert.equal(h.get('discovery-accept').disabled, true);
  assert.deepEqual(h.flow.buffers.discovery.challenges, []); assert.equal(h.get('discovery-challenge-0-challenge'), undefined);
  for (const [name, id] of ANSWER_IDS) {assert.equal(h.get(id).value, ''); assert.equal(h.flow.buffers.discovery[name], '');}
  for (const [name, id] of ANSWER_IDS) h.get(id).input('My ' + name);
  assert.equal(h.get('discovery-accept').disabled, true, 'a challenge is still required');
  await h.get('discovery-add-challenge').click(); assert.deepEqual(h.flow.buffers.discovery.challenges, [{challenge: '', response: ''}]);
  assert.equal(h.get('discovery-accept').disabled, true);
  h.get('discovery-challenge-0-challenge').input('My challenge'); h.get('discovery-challenge-0-response').input('My response');
  assert.equal(h.get('discovery-accept').disabled, true, 'what already exists is still required');
  await h.get('discovery-add-prior-art').click();
  h.get('discovery-prior-art-0-name').input('Tapeo'); h.get('discovery-prior-art-0-differs').input('Reusable'); h.get('discovery-prior-art-0-licence').input('MIT');
  assert.equal(h.get('discovery-accept').disabled, false); assert.equal(h.writes.length, 0);
  await h.get('discovery-accept').click(); assert.equal(h.writes.at(-1).operation, 'accept'); assert.equal(h.writes.at(-1).payload.step, 'discovery');
  assert.equal(h.writes.at(-1).payload.proposal_id, null); assert.equal(h.writes.at(-1).payload.fields.problem, 'My problem');
  assert.deepEqual(h.writes.at(-1).payload.fields.challenges, [{challenge: 'My challenge', response: 'My response'}]);
  assert.equal(h.flow.status('discovery'), 'saved');
});

test('a connected terminal locks the empty step; taking it by hand opens the human path with no suggestion', async () => {
  const h = harness(); assert.equal(h.get('discovery-problem').disabled, true);
  assert.ok(h.body.all().some(node => node.className === 'field-locked'));
  await h.get('discovery-hand-fill').click();
  assert.equal(h.get('discovery-problem').disabled, false); assert.equal(h.get('discovery-hand-fill'), undefined);
  h.get('discovery-problem').input('By hand'); assert.equal(h.flow.buffers.discovery.problem, 'By hand');
  assert.equal(h.writes.length, 0);
});

test('archived Discovery stays reachable, points to Exploration for reactivation and never offers a live Accept', async () => {
  const h = harness(); assert.equal(h.get('discovery-accept').textContent, 'Accept and continue');
  assert.equal(h.get('discovery-reactivation-notice'), undefined);
  h.state.idea_status = 'archived'; h.state.accepted.discovery = copy(DISCOVERY);
  h.state.steps.discovery = {status: 'saved', accepted_revision: 2, evidence_id: 'immutable-archived-discovery'};
  h.flow.load(copy(h.state));
  assert.equal(h.flow.canOpen('discovery'), true); assert.equal(h.flow.open('discovery'), true);
  assert.equal(h.get('discovery-reactivation-notice').role, 'status');
  assert.match(h.get('discovery-reactivation-notice').textContent, /redo and accept Exploration to reactivate/);
  assert.doesNotMatch(h.get('discovery-reactivation-notice').textContent, /Accepting a redone Discovery reactivates/);
  assert.equal(h.get('discovery-accept').textContent, 'Accept and continue');
  h.flow.edit(KEY, copy(DISCOVERY)); h.get('discovery-problem').input('Explicit redone Discovery');
  assert.equal(h.get('discovery-accept').disabled, true);
  assert.match(h.get('discovery-accept').title, /archived/);
  assert.equal(h.get('discovery-accept').title, h.get('discovery-accept-reason').textContent);
  await h.get('discovery-accept').click(); assert.equal(h.writes.length, 0); assert.equal(h.state.idea_status, 'archived');
});

test('archived Discovery keeps offline and paused acceptance refusal', async () => {
  for (const blocked of ['offline', 'paused']) {
    const h = harness(); h.state.idea_status = 'archived'; h.flow.load(copy(h.state)); h.flow.edit(KEY, copy(DISCOVERY));
    if (blocked === 'offline') h.connect(false); else {h.flow.paused = true; h.draw();}
    assert.equal(h.get('discovery-accept').textContent, 'Accept and continue');
    assert.equal(h.get('discovery-accept').disabled, true); await h.get('discovery-accept').click();
    assert.equal(h.writes.length, 0); assert.equal(h.state.idea_status, 'archived');
  }
});

test('partial durable draft renders missing containers empty and invents no content', async () => {
  const h = harness({agent: 'disconnected'}); h.state.drafts.discovery = {problem: 'Unfinished saved thought', challenges: null};
  h.flow.load(h.state);
  assert.equal(h.get('discovery-problem').value, 'Unfinished saved thought'); assert.equal(h.get('discovery-audience').value, '');
  assert.equal(h.get('discovery-challenge-0-challenge'), undefined);
  for (const [name, id] of ANSWER_IDS.slice(1)) h.get(id).input('Human ' + name);
  await h.get('discovery-add-challenge').click(); h.get('discovery-challenge-0-challenge').input('Human challenge'); h.get('discovery-challenge-0-response').input('Human response');
  h.get('discovery-prior-art-none').listeners.change({target: {checked: true}}); h.get('discovery-prior-art-searched').input('Searched the web');
  assert.equal(h.get('discovery-accept').disabled, false); assert.equal(h.flow.buffers.discovery.problem, 'Unfinished saved thought');
});

test('request waits with locked fields; reply stays separate until Use, edited copy accepts', async () => {
  const h = harness(); await h.flow.requestProposal(KEY);
  assert.equal(h.get('discovery-conversation-status').role, 'status'); assert.match(h.get('discovery-conversation-status').textContent, /^Asking your terminal/);
  assert.equal(h.get('discovery-problem').disabled, true, 'terminal-guided fields are locked while the terminal has the step');
  assert.ok(h.get('discovery-hand-fill')); assert.equal(h.flow.status('discovery'), 'current');
  h.flow.edit(KEY, {...h.flow.buffers.discovery, problem: 'Unsent user edit'}); h.complete(); await h.flow.refreshAgent();
  assert.equal(h.flow.buffers.discovery.problem, 'Unsent user edit'); assert.equal(h.flow.selectedProposals.discovery, undefined);
  await h.get('discovery-use-proposal-' + PID).click(); assert.equal(h.flow.buffers.discovery.problem, DISCOVERY.problem);
  assert.deepEqual(h.flow.buffers.discovery.challenges, DISCOVERY.challenges);
  assert.equal(h.flow.selectedProposals.discovery, PID); assert.equal(h.flow.status('discovery'), 'unsaved');
  assert.ok(h.get('discovery-manual'));
  h.get('discovery-problem').input('Human revised suggestion'); await h.get('discovery-accept').click();
  assert.equal(h.writes.at(-1).payload.fields.problem, 'Human revised suggestion'); assert.equal(h.writes.at(-1).payload.proposal_id, PID);
});

test('hostile text survives editing as text and challenge rows are never inferred or HTML', async () => {
  const h = harness({agent: 'disconnected'}); const hostile = '<img src=x onerror=alert(1)>\nOriginal second line';
  h.flow.edit(KEY, {...copy(DISCOVERY), problem: hostile, challenges: [{challenge: hostile, response: 'Original response'}, {challenge: 'Other', response: 'Other response'}]});
  assert.equal(h.get('discovery-problem').value, hostile); assert.equal(h.get('discovery-challenge-0-challenge').value, hostile);
  h.get('discovery-challenge-1-response').input('Edited second response');
  assert.equal(h.flow.buffers.discovery.challenges[0].challenge, hostile); assert.equal(h.flow.buffers.discovery.challenges[1].response, 'Edited second response');
  await h.get('discovery-remove-challenge-1').click(); assert.equal(h.flow.buffers.discovery.challenges.length, 1);
  await h.get('discovery-remove-challenge-0').click(); assert.deepEqual(h.flow.buffers.discovery.challenges, []);
  assert.equal(h.get('discovery-accept').disabled, true);
  await h.get('discovery-add-challenge').click(); assert.equal(h.get('discovery-accept').disabled, true); h.get('discovery-challenge-0-challenge').input('Human');
  assert.equal(h.get('discovery-accept').disabled, true); h.get('discovery-challenge-0-response').input('Human response');
  assert.equal(h.get('discovery-accept').disabled, false); assert.equal(h.body.all().filter(node => node.tag === 'img').length, 0);
});

test('hostile suggestion text is shown as literal text, never as markup, and Use copies it verbatim', async () => {
  const h = harness(); const hostile = '<img src=x onerror=alert(1)><script>alert(2)</script>\nsecond line';
  await requested(h, {}, {problem: hostile, audience: hostile, workaround: hostile, evidence: hostile, kill_criteria: hostile, challenges: [{challenge: hostile, response: hostile}],
    prior_art: [{name: hostile, link: '', does: '', differs: hostile, licence: hostile}], prior_art_none: false, prior_art_searched: ''});
  const shown = h.get('discovery-proposal-' + PID); assert.ok(shown);
  assert.ok(shown.all().some(node => node.tag === 'p' && node.textContent === 'What is the problem? ' + hostile));
  assert.ok(shown.all().some(node => node.textContent === 'Challenge: ' + hostile + ' — ' + hostile));
  assert.equal([...h.body.all(), ...h.foot.all()].some(node => ['img', 'script', 'a'].includes(node.tag) || Object.hasOwn(node, 'innerHTML')), false);
  await h.get('discovery-use-proposal-' + PID).click();
  assert.equal(h.get('discovery-problem').value, hostile); assert.equal(h.get('discovery-challenge-0-response').value, hostile);
  assert.equal(h.flow.buffers.discovery.problem, hostile);
  assert.equal([...h.body.all()].some(node => ['img', 'script'].includes(node.tag)), false);
});

const uncertain = () => {
  const h = harness(), original = h.api.write; let first = true;
  h.api.write = async (...args) => {const result = await original(...args); if (first) {first = false; throw Object.assign(new Error('private diagnostic'), {code: 'connection_lost', uncertain: true});} return result;};
  return h;
};

test('uncertain request keeps an identical retry and stop-waiting path in the flow, without leaking diagnostics', async () => {
  const h = uncertain(); await h.flow.requestProposal(KEY);
  assert.equal(h.flow.proposalPending.phase, 'failed'); assert.equal(h.flow.proposalPending.ambiguous, true); assert.equal(h.writes.length, 1);
  assert.equal(h.body.all().some(node => node.textContent.includes('private diagnostic')), false);
  assert.equal(h.get('discovery-problem').disabled, true, 'the step stays locked, not silently opened');
  assert.equal(await h.flow.retryProposal(), true); assert.deepEqual(h.writes[0], h.writes[1]);
  h.flow.cancelProposal('user_cancelled'); assert.equal(h.flow.proposalPending, null);
  assert.equal(h.writes.length, 2); assert.equal(h.flow.canPropose(KEY), true);
});

test('uncertain request exposes explicit identical retry and stop-waiting controls', async () => {
  const h = uncertain(); await h.flow.requestProposal(KEY);
  assert.match(h.get('discovery-proposal-status').textContent, /may have reached/);
  assert.ok(h.get('discovery-retry')); await h.get('discovery-retry').click(); assert.deepEqual(h.writes[0], h.writes[1]);
  await h.get('discovery-stop-waiting').click(); assert.equal(h.flow.proposalPending, null);
});

test('generation drift disables linked accept until explicit manual choice; disconnected editor stays usable', async () => {
  const h = harness(); await requested(h);
  await h.get('discovery-use-proposal-' + PID).click(); h.state.agent_generation = 'agent_' + '5'.repeat(32);
  h.state.proposals[0] = {...h.state.proposals[0], stale: true, stale_reason: 'wrong_generation', acceptance_eligible: false, acceptance_reason: 'wrong_generation'};
  await h.flow.refreshAgent(); assert.equal(h.get('discovery-use-proposal-' + PID).disabled, true); assert.equal(h.get('discovery-accept').disabled, true);
  assert.ok(h.body.all().some(node => node.textContent === 'This suggestion belongs to an earlier agent session.'));
  await h.get('discovery-manual').click(); assert.equal(h.get('discovery-accept').disabled, false);
  assert.equal(h.flow.selectedProposals.discovery, undefined);
  h.state.agent_status = 'disconnected'; h.state.capabilities.agent = false; h.state.capabilities.memory = false;
  h.state.resume = {required: true, reason: 'agent_disconnected'}; h.sources(); await h.flow.refreshAgent();
  assert.equal(h.flow.canPropose(KEY), false); assert.equal(h.get('discovery-problem').disabled, false); assert.equal(h.get('discovery-accept').disabled, false);
  h.connect(false); assert.equal(h.get('discovery-problem').disabled, true); assert.equal(h.get('discovery-accept').disabled, true);
});

test('agent drop after Use: the explicit manual button keeps my own answers and unlinks the suggestion', async () => {
  const h = harness(); await requested(h); await h.get('discovery-use-proposal-' + PID).click();
  h.state.agent_status = 'disconnected'; h.state.capabilities.agent = false; h.state.resume = {required: true, reason: 'agent_disconnected'};
  h.state.proposals[0] = {...h.state.proposals[0], acceptance_eligible: false, acceptance_reason: 'agent_unavailable'}; h.sources(); await h.flow.refreshAgent();
  assert.equal(h.get('discovery-accept').disabled, true); assert.equal(h.get('discovery-use-proposal-' + PID).disabled, true);
  const manual = h.get('discovery-manual'); assert.ok(manual, 'the human path stays one click away'); assert.equal(manual.disabled, false);
  await manual.click(); assert.equal(h.get('discovery-accept').disabled, false);
  await h.get('discovery-accept').click();
  const accepted = h.writes.filter(write => write.operation === 'accept');
  assert.equal(accepted.length, 1); assert.equal(accepted[0].payload.proposal_id, null, 'accepted as my own, not linked');
  assert.equal(accepted[0].payload.step, 'discovery'); assert.deepEqual(accepted[0].payload.fields, DISCOVERY, 'exactly the fields on screen');
  assert.equal(h.flow.selectedProposals.discovery, undefined);
});

test('agent drop after Use: one explicit foot button accepts my own answers and unlinks the suggestion', async () => {
  const h = harness(); await requested(h); await h.get('discovery-use-proposal-' + PID).click();
  assert.equal(h.get('discovery-accept-own'), undefined, 'no extra door while the suggestion is acceptable');
  h.state.agent_status = 'disconnected'; h.state.capabilities.agent = false; h.state.resume = {required: true, reason: 'agent_disconnected'};
  h.state.proposals[0] = {...h.state.proposals[0], acceptance_eligible: false, acceptance_reason: 'agent_unavailable'}; h.sources(); await h.flow.refreshAgent();
  assert.equal(h.get('discovery-accept').disabled, true);
  const own = h.get('discovery-accept-own'); assert.ok(own, 'the door sits beside Accept'); assert.equal(own.disabled, false);
  assert.match(h.get('discovery-accept-own-reason').textContent, /can no longer be accepted.*kept/);
  await own.click();
  const accepted = h.writes.filter(write => write.operation === 'accept');
  assert.equal(accepted.length, 1); assert.equal(accepted[0].payload.proposal_id, null, 'accepted as my own, not linked');
  assert.equal(accepted[0].payload.step, 'discovery'); assert.deepEqual(accepted[0].payload.fields, DISCOVERY, 'exactly the fields on screen');
  assert.equal(h.flow.selectedProposals.discovery, undefined);
});

test('omitted suggestion bodies expose real Markdown paths as text and cannot be copied', async () => {
  const h = harness(); await h.flow.requestProposal(KEY); h.complete({proposal: null, content_omitted: true, acceptance_eligible: false, acceptance_reason: 'projection_omitted'});
  h.state.proposal_inventory.total = 4; h.state.proposal_inventory.omitted = 3; await h.flow.refreshAgent();
  assert.equal(h.get('discovery-use-proposal-' + PID).disabled, true);
  assert.equal(h.get('discovery-proposal-inventory').role, 'status'); assert.match(h.get('discovery-proposal-index-path').textContent, /All immutable suggestion links/);
  assert.ok(h.get('discovery-proposal-evidence-' + PID).textContent.endsWith(h.state.proposals[0].evidence.path));
  assert.ok(h.body.all().some(node => node.textContent === 'This suggestion is saved in Markdown; its body is omitted from this view.'));
  assert.equal(h.body.all().some(node => node.tag === 'a' || node.href), false);
  await h.get('discovery-use-proposal-' + PID).click();
  assert.equal(h.flow.selectedProposals.discovery, undefined); assert.equal(h.flow.buffers.discovery.problem, '');
});
