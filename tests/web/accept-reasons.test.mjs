// Every disabled Accept says what is missing: a sweep over all eight steps plus exact sentences.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web = new URL('../../glitch-idea/web/', import.meta.url);
const moduleFrom = async name => import('data:text/javascript;base64,' + Buffer.from(await readFile(new URL(name, web), 'utf8')).toString('base64'));
const {Flow, STEPS, FILL_KEYS, acceptBlockers, validCapture, validPriorities, validMethod, validDiscovery, validExploration, validVisualize, validAssess} = await moduleFrom('folds.js');
const IDEA = 'idea_' + '1'.repeat(32), SID = 'session_' + '2'.repeat(32), BINDING = 'binding_' + '6'.repeat(32), GEN = 'agent_' + '3'.repeat(32);
const copy = value => structuredClone(value);
const KEYS = STEPS.map(step => step.key);
const MEMORY = {status: 'unavailable', sources: [], rationale: null, preferred_method: null};

const FIELDS = {
  capture: {raw_text: 'A fixture idea', workspace: {name: 'fixture', path: '/fixture', confirmed: true}},
  priorities: {urgency: 5, importance: 7},
  method: {selection: 'bounded-plan', reason: '', memory: MEMORY},
  discovery: {problem: 'Lids leak', audience: 'Cooks', workaround: 'Tape', evidence: 'Three reports', kill_criteria: 'No leaks reported',
    challenges: [{challenge: 'Is it real?', response: 'Yes, seen twice'}],
    prior_art: [{name: 'Tapeo', link: 'https://example.test/tapeo', does: 'Seals lids', differs: 'Ours is reusable', licence: 'MIT'}], prior_art_none: false, prior_art_searched: ''},
  exploration: {outcome: 'A clean lid', alternatives: [{route: 'Clean it', reason: 'Cheap'}], assumptions: [], scope: 'small-change',
    scope_reason: 'One lid', next_slice: 'Check the seal', learning: [], investment: null, experiment: null,
    sketch: [{title: 'Seal test', done_when: 'No drip for a day'}]},
  visualize: {disposition: 'skipped', reason: 'Nothing to draw', design_set_id: null, brief_evidence_id: null},
  assess: {assessment: {method: 'rice', version: 'v1', inputs: {reach: 100, impact: 2, confidence: 0.8, effort: 4}, basis: 'Fixture basis',
    assumptions: [], confidence: 'high', provenance: 'Fixture source'},
    position: {proposed_position: 1, actual_position: 1, neighbors: {before: null, after: null}, override_reason: null}},
};
const EMPTY = {
  capture: {raw_text: '', workspace: {name: '', path: '', confirmed: false}},
  priorities: {urgency: null, importance: null},
  method: {selection: null, reason: '', memory: MEMORY},
  discovery: {problem: '', audience: '', workaround: '', evidence: '', kill_criteria: '', challenges: [],
    prior_art: [], prior_art_none: false, prior_art_searched: ''},
  exploration: {outcome: '', alternatives: [], assumptions: [], scope: null, scope_reason: '', next_slice: '', learning: [], investment: null, experiment: null, sketch: []},
  visualize: {disposition: null, reason: null, design_set_id: null, brief_evidence_id: null},
  assess: {assessment: null, position: {proposed_position: null, actual_position: null, neighbors: {before: null, after: null}, override_reason: null}},
};
const without = (step, patch) => ({...copy(FIELDS[step]), ...patch});
const PARTIAL = {
  capture: [without('capture', {raw_text: ''}), without('capture', {workspace: {name: 'fixture', path: '/fixture', confirmed: false}})],
  priorities: [{urgency: 5, importance: null}, {urgency: 11, importance: 3}, {urgency: 2.5, importance: 3}],
  method: [without('method', {selection: null}), without('method', {selection: 'nonsense'})],
  discovery: [without('discovery', {problem: ' '}), without('discovery', {challenges: []}), without('discovery', {challenges: [{challenge: 'Is it real?', response: ''}]}),
    without('discovery', {kill_criteria: ''})],
  exploration: [without('exploration', {outcome: ''}), without('exploration', {alternatives: []}), without('exploration', {alternatives: [{route: 'Clean it', reason: ''}]}),
    without('exploration', {alternatives: [{route: '', reason: 'Cheap'}]}), without('exploration', {sketch: []}), without('exploration', {sketch: [{title: 'Seal', done_when: ''}]}),
    without('exploration', {scope: null}), without('exploration', {scope_reason: ''}), without('exploration', {next_slice: ''}), without('exploration', {assumptions: ['']}),
    without('exploration', {sketch: Array.from({length: 6}, () => ({title: 't', done_when: 'd'}))})],
  visualize: [without('visualize', {disposition: null}), without('visualize', {reason: ''}), without('visualize', {reason: null})],
  assess: [{...copy(FIELDS.assess), assessment: null}, {...copy(FIELDS.assess), assessment: {...copy(FIELDS.assess.assessment), inputs: {reach: 100, impact: null, confidence: 0.8, effort: 4}}},
    {...copy(FIELDS.assess), assessment: {...copy(FIELDS.assess.assessment), inputs: {reach: 100, impact: 2, confidence: 0.8, effort: 0}}},
    {...copy(FIELDS.assess), assessment: {...copy(FIELDS.assess.assessment), basis: ''}},
    {...copy(FIELDS.assess), assessment: {...copy(FIELDS.assess.assessment), confidence: null}},
    {...copy(FIELDS.assess), position: {...copy(FIELDS.assess.position), actual_position: 2}},
    {...copy(FIELDS.assess), position: {proposed_position: null, actual_position: null, neighbors: {before: null, after: null}, override_reason: null}}],
};
const APPETITE = {investment: {cap: 4, unit: 'hours', boundary: 'One lid'}};
const EXPERIMENT = {experiment: {question: 'Does tape hold?', evidence: 'A week of use', success_criterion: 'No drip', stop_rule: 'Two drips'}};

function state({step, method = null, agent = 'disconnected', status = {}, idea_status = 'active'} = {}) {
  const prior = new Set(KEYS.slice(0, KEYS.indexOf(step)));
  const value = {ok: true, code: 'ok', session_id: SID, idea_id: IDEA, idea_status, revision: 3, draft_version: 0, backlog_revision: 0,
    current_step: step, agent_status: agent, agent_generation: agent === 'connected' ? GEN : null,
    steps: Object.fromEntries(KEYS.map(key => [key, prior.has(key) ? {status: 'saved', accepted_revision: 2, evidence_id: key + '-e'} :
      {status: key === step ? 'current' : 'todo', accepted_revision: null, evidence_id: null}])),
    accepted: Object.fromEntries(KEYS.map(key => [key, key === 'capture' ? copy(FIELDS.capture) : null])),
    drafts: {}, draft: null, proposal_sources: {}, proposals: [],
    proposal_inventory: {total: 0, projected: 0, omitted: 0, content_omitted: 0, index_path: IDEA + '.md'}};
  if (method) value.accepted.method = {...copy(FIELDS.method), selection: method};
  if (step === 'assess') Object.assign(value, {backlog_status: {available: true, code: 'ok'}, human_ratings: null, assessment_summary: null,
    backlog: {revision: 0, order: [IDEA], comparisons: [{idea_id: IDEA, revision: 3, status: 'active', ratings: null, assessment: null}]}});
  for (const [key, entry] of Object.entries(status)) value.steps[key] = entry;
  return value;
}
function flowFor(options) {
  const flow = new Flow({bindingId: BINDING, state: async () => ({}), write: async () => ({})}, () => 'r');
  flow.load(state(options));
  return flow;
}
const validators = {
  capture: f => validCapture(f), priorities: f => validPriorities(f), method: f => validMethod(f), discovery: f => validDiscovery(f),
  visualize: f => validVisualize(f),
  // The schema tolerates unfilled numbers; a score cannot be computed from them, so Accept needs every input.
  assess: f => validAssess(f) && Object.values(f.assessment.inputs).every(v => v !== null),
};
const valid = (step, fields, method) => step === 'exploration' ? validExploration(fields, method) : validators[step](fields);
const dumb = list => assert.ok(list.every(item => typeof item === 'string' && item.length < 120 && !/_/.test(item)), JSON.stringify(list));

for (const step of ['capture', 'priorities', 'method', 'discovery', 'exploration', 'visualize', 'assess']) {
  test('sweep ' + step + ': invalid content always names something; complete and idle names nothing', () => {
    const variants = step === 'exploration'
      ? [[null, FIELDS.exploration], ['appetite-led', {...FIELDS.exploration, ...APPETITE}], ['experiment-led', {...FIELDS.exploration, ...EXPERIMENT}]]
      : [[null, FIELDS[step]]];
    for (const [method, complete] of variants) {
      const flow = flowFor({step, method});
      const cases = [EMPTY[step], ...PARTIAL[step], complete];
      if (step === 'exploration') {
        // Method-driven inputs: missing, wrong kind, and partial.
        cases.push(...(method === 'appetite-led' ? [FIELDS.exploration, {...complete, investment: {cap: 0, unit: 'h', boundary: 'b'}}, {...complete, investment: {cap: 3, unit: '', boundary: 'b'}}] :
          method === 'experiment-led' ? [FIELDS.exploration, {...complete, experiment: {...complete.experiment, stop_rule: ''}}] :
            [{...FIELDS.exploration, ...APPETITE}, {...FIELDS.exploration, ...EXPERIMENT}]));
      }
      for (const fields of cases) {
        const blockers = acceptBlockers(step, fields, flow);
        dumb(blockers);
        if (!valid(step, fields, method)) assert.ok(blockers.length > 0, step + '/' + method + ' ' + JSON.stringify(fields));
        else assert.deepEqual(blockers, [], step + '/' + method);
      }
    }
  });
}

test('sweep review: nothing to fill, only earlier steps can block', () => {
  assert.deepEqual(acceptBlockers('review', {}, flowFor({step: 'review'})), []);
  const flow = flowFor({step: 'review', status: {discovery: {status: 'todo', accepted_revision: null, evidence_id: null}}});
  assert.deepEqual(acceptBlockers('review', {}, flow), ['Save Discovery first.']);
});

test('exact sentences for the common gaps', () => {
  const flow = flowFor({step: 'exploration'});
  assert.deepEqual(acceptBlockers('exploration', EMPTY.exploration, flow), ['Add the desired result.', 'Choose a scope.', 'Say why you chose that scope.',
    'Say what you will do next.', 'Add at least one alternative route (Route and Why both filled).',
    'Sketch at least one item, each with a title and when it is done.']);
  assert.deepEqual(acceptBlockers('exploration', without('exploration', {alternatives: [{route: 'Clean it', reason: ''}]}), flow), ['Fill both Route and Why on every alternative.']);
  assert.deepEqual(acceptBlockers('priorities', {urgency: 4, importance: null}, flowFor({step: 'priorities'})), ['Choose Importance from 1 to 10.']);
  const rice = copy(FIELDS.assess); rice.assessment.inputs = {reach: null, impact: null, confidence: 0.5, effort: 0};
  assert.deepEqual(acceptBlockers('assess', rice, flowFor({step: 'assess'})), ['Reach, Impact and Effort need numbers; Effort must be above 0 and Confidence between 0 and 1.']);
  assert.deepEqual(acceptBlockers('exploration', {...FIELDS.exploration, investment: null}, flowFor({step: 'exploration', method: 'appetite-led'})),
    ['Set the investment: a cap above zero, its unit, and what it covers.']);
});

test('flow-level reasons: saving, paused, archived, earlier steps unsaved', () => {
  const flow = flowFor({step: 'priorities'});
  flow.busy = true;
  assert.deepEqual(acceptBlockers('priorities', FIELDS.priorities, flow), ['Saving…']);
  flow.busy = false; flow.paused = true;
  assert.deepEqual(acceptBlockers('priorities', FIELDS.priorities, flow), ['This idea is paused. Resume it to continue.']);
  flow.paused = false;
  assert.deepEqual(acceptBlockers('priorities', FIELDS.priorities, flow), []);
  assert.deepEqual(acceptBlockers('priorities', FIELDS.priorities, flowFor({step: 'priorities', idea_status: 'archived'})),
    ['This idea is archived. Reactivate it before accepting new answers.']);
  const todo = flowFor({step: 'discovery', status: {capture: {status: 'todo', accepted_revision: null, evidence_id: null}}});
  assert.deepEqual(acceptBlockers('discovery', FIELDS.discovery, todo), ['Save Capture first.']);
});

test('locked fields add the waiting count; complete content or hand release removes it', () => {
  const connected = flowFor({step: 'discovery', agent: 'connected'});
  const blockers = acceptBlockers('discovery', EMPTY.discovery, connected);
  assert.ok(blockers.includes('Fill this step by hand or let your terminal finish: 7 fields still waiting.'), blockers.join('|'));
  // Content already present is never waiting on the terminal.
  connected.buffers.discovery = copy(FIELDS.discovery);
  assert.deepEqual(acceptBlockers('discovery', FIELDS.discovery, connected), []);
  // One required field left empty: exactly one waiting, singular.
  connected.buffers.discovery = {...copy(FIELDS.discovery), audience: ''};
  assert.ok(acceptBlockers('discovery', connected.buffers.discovery, connected).includes('Fill this step by hand or let your terminal finish: 1 field still waiting.'));
  // Exploration counts every locked field, optional lists included, and the accepted method's input only.
  const plain = flowFor({step: 'exploration', agent: 'connected'});
  assert.ok(acceptBlockers('exploration', EMPTY.exploration, plain).some(s => s.endsWith(': 8 fields still waiting.')));
  const appetite = flowFor({step: 'exploration', agent: 'connected', method: 'appetite-led'});
  assert.ok(acceptBlockers('exploration', EMPTY.exploration, appetite).some(s => s.endsWith(': 9 fields still waiting.')));
  const handed = flowFor({step: 'discovery', agent: 'connected'});
  handed.state.hand = {discovery: true, exploration: false};
  assert.ok(!acceptBlockers('discovery', EMPTY.discovery, handed).some(s => s.includes('still waiting')));
});
