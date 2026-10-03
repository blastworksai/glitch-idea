// Pure state/controller seam; DOM and server authority stay separate.
export const STEPS = Object.freeze([
  {key: 'capture', title: 'Capture the idea'},
  {key: 'priorities', title: 'Your priorities'},
  {key: 'shape', title: 'Shape the outcome'},
  {key: 'method', title: 'Choose a methodology'},
  {key: 'visualize', title: 'Visualize when useful'},
  {key: 'assess', title: 'Assess and position'},
  {key: 'review', title: 'Review and hand off'},
]);
const KEYS = STEPS.map(step => step.key);
const COMPLETE = new Set(['saved', 'skipped', 'not-applicable']);
const STATUSES = new Set(['todo', 'current', 'saved', 'review-needed', 'unsaved', 'skipped', 'not-applicable']);
const DEPENDENTS = {
  capture: ['shape', 'method', 'visualize', 'assess', 'review'],
  priorities: ['assess', 'review'], shape: ['method', 'visualize', 'assess', 'review'],
  method: ['review'], visualize: ['review'], assess: ['review'], review: [],
};
const clone = value => JSON.parse(JSON.stringify(value));
const canonical = value => Array.isArray(value) ? value.map(canonical) :
  value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
const same = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
export const statusLabel = status => ({todo: 'To do', current: 'Current', saved: 'Saved',
  'review-needed': 'Review needed', unsaved: 'Unsaved', skipped: 'Skipped',
  'not-applicable': 'Not applicable'}[status] ?? 'Unavailable');
export const statusGlyph = status => ({saved: '✓', 'review-needed': '!',
  unsaved: '◉', skipped: '−', 'not-applicable': '−', current: '◐', todo: '○'}[status] ?? '?');

export function validCapture(fields) {
  return typeof fields?.raw_text === 'string' && Boolean(fields.raw_text.trim()) &&
    typeof fields.workspace?.name === 'string' && Boolean(fields.workspace.name.trim()) &&
    typeof fields.workspace?.path === 'string' && Boolean(fields.workspace.path.trim()) &&
    fields.workspace?.confirmed === true;
}
export function validPriorities(fields) {
  return ['urgency', 'importance'].every(key => Number.isInteger(fields?.[key]) && fields[key] >= 1 && fields[key] <= 10);
}

// Fixed public projection schema; no agent credential enters this controller.
const GENERATION = /^agent_[0-9a-f]{32}$/;
const HASH = /^[0-9a-f]{64}$/;
const PROPOSAL = /^proposal_[0-9a-f]{32}$/;
const BINDING = /^binding_[0-9a-f]{32}$/;
const IDEA = /^idea_[0-9a-f]{32}$/;
function tabStorage() {
  try { return globalThis.sessionStorage ?? null; }
  catch { return null; } // Browser privacy settings may deny access.
}
const REQUEST = /^[A-Za-z0-9_.:-]{1,128}$/;
const METHODS = ['bounded-plan', 'adaptive-slices', 'appetite-led', 'experiment-led'];
const SCOPES = ['small-change', 'capability', 'project', 'epic'];
const MEMORY = ['found', 'searched_no_preference', 'unavailable', 'error'];
const REASONS = ['wrong_generation', 'wrong_session', 'agent_unavailable', 'stale_revision', 'stale_source', 'supporting_evidence'];
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const exact = (value, keys) => object(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const subset = (value, keys) => object(value) && Object.keys(value).every(key => keys.includes(key));
const integer = value => Number.isSafeInteger(value) && value >= 0 && value <= 1e12;
const failure = (code, uncertain = false) => Object.assign(new Error(code), {code, uncertain});
const defaultBuffers = () => ({capture: {raw_text: '', workspace: {name: '', path: '', confirmed: false}},
  priorities: {urgency: null, importance: null},
  shape: {outcome: '', scope: null, scope_reason: '', alternatives: [], assumptions: [], next_slice: '', learning: []},
  method: {selection: null, reason: '', investment: null, experiment: null, memory: {status: 'unavailable', sources: [], rationale: null}},
  visualize: {disposition: null, reason: null, design_set_id: null, brief_evidence_id: null},
  assess: {assessment: null, position: {proposed_position: null, actual_position: null, neighbors: {before: null, after: null}, override_reason: null}}});
const packetIdentity = packet => [packet.handoff_id, packet.sha256, packet.source_digest, packet.source_revision, packet.path];
// Match Python's character limits while excluding invalid Unicode scalars.
// Walk UTF-16 pairs directly: no array allocation, and stop at the limit.
function boundedText(value, limit) {
  if (typeof value !== 'string') return false;
  let count = 0;
  for (let index = 0; index < value.length; index++) {
    if (++count > limit) return false;
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(++index);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
  }
  return true;
}
const text = value => boundedText(value, 65536);
const meaningful = value => text(value) && Boolean(value.trim());
const texts = value => Array.isArray(value) && value.length <= 1000 && value.every(text);
const optional = (value, validate) => value == null || validate(value);
function memory(value, partial = false) {
  const keys = ['status', 'sources', 'rationale'];
  return (partial ? subset(value, keys) : exact(value, keys)) &&
    optional(value.status, v => MEMORY.includes(v)) && optional(value.sources, texts) && optional(value.rationale, text) &&
    (partial || (MEMORY.includes(value.status) && Array.isArray(value.sources) && value.sources.every(meaningful) &&
      (value.status !== 'found' || (value.sources.length > 0 && meaningful(value.rationale))) &&
      (!['unavailable', 'error'].includes(value.status) || value.sources.length === 0)));
}
function shapeFields(value, partial = false) {
  const keys = ['outcome', 'scope', 'scope_reason', 'alternatives', 'assumptions', 'next_slice', 'learning'];
  return (partial ? subset(value, keys) : exact(value, keys)) &&
    ['outcome', 'scope_reason', 'next_slice'].every(k => optional(value[k], text)) &&
    optional(value.scope, v => SCOPES.includes(v)) &&
    ['assumptions', 'learning'].every(k => optional(value[k], texts)) &&
    optional(value.alternatives, v => Array.isArray(v) && v.length <= 1000 && v.every(a =>
      subset(a, ['route', 'reason']) && optional(a.route, text) && optional(a.reason, text))) &&
    (partial || (['outcome', 'scope_reason', 'next_slice'].every(k => meaningful(value[k])) && SCOPES.includes(value.scope) &&
      value.alternatives?.length > 0 && value.alternatives.every(a => exact(a, ['route', 'reason']) && meaningful(a.route) && meaningful(a.reason)) &&
      ['assumptions', 'learning'].every(k => Array.isArray(value[k]) && value[k].every(meaningful))));
}
function methodFields(value, partial = false) {
  const keys = ['selection', 'reason', 'investment', 'experiment', 'memory'];
  const investment = v => subset(v, ['cap', 'unit', 'boundary']) && optional(v.cap, n => typeof n === 'number' && Number.isFinite(n) && n > 0 && n <= 1e12) &&
    optional(v.unit, text) && optional(v.boundary, text);
  const experiment = v => subset(v, ['question', 'evidence', 'success_criterion', 'stop_rule']) && Object.values(v).every(item => optional(item, text));
  return (partial ? subset(value, keys) : exact(value, keys)) && optional(value.selection, v => METHODS.includes(v)) && optional(value.reason, text) &&
    optional(value.investment, investment) && optional(value.experiment, experiment) && optional(value.memory, v => memory(v, partial)) &&
    (partial || (METHODS.includes(value.selection) && meaningful(value.reason) && memory(value.memory) &&
      (value.selection === 'appetite-led' ? exact(value.investment, ['cap', 'unit', 'boundary']) && investment(value.investment) && typeof value.investment.cap === 'number' && Number.isFinite(value.investment.cap) && value.investment.cap > 0 && meaningful(value.investment.unit) && meaningful(value.investment.boundary) : value.investment === null) &&
      (value.selection === 'experiment-led' ? exact(value.experiment, ['question', 'evidence', 'success_criterion', 'stop_rule']) && Object.values(value.experiment).every(meaningful) : value.experiment === null)));
}
export const validShape = fields => shapeFields(fields);
export const validMethod = fields => methodFields(fields);
const SET = /^set_[0-9a-f]{32}$/;
const ASSET = /^asset_[0-9a-f]{32}$/;
export function visualizeFields(value, partial = false) {
  const keys = ['disposition', 'reason', 'design_set_id', 'brief_evidence_id'];
  if (!(partial ? subset(value, keys) : exact(value, keys)) ||
      !optional(value.disposition, v => ['accepted_set', 'skipped', 'not-applicable'].includes(v)) ||
      !optional(value.reason, text) || !optional(value.design_set_id, v => typeof v === 'string' && SET.test(v)) ||
      !optional(value.brief_evidence_id, v => meaningful(v) && boundedText(v, 200))) return false;
  return partial || (value.brief_evidence_id === null && ['accepted_set', 'skipped', 'not-applicable'].includes(value.disposition) &&
    (value.reason === null || text(value.reason)) && (value.disposition === 'accepted_set'
      ? value.design_set_id === null || (typeof value.design_set_id === 'string' && SET.test(value.design_set_id))
      : value.design_set_id === null && meaningful(value.reason)));
}
export const validVisualize = fields => visualizeFields(fields);
// Assess mirrors the existing domain schema/formula; scores are never input.
const ASSESS_KEYS = ['method', 'version', 'inputs', 'basis', 'assumptions', 'confidence', 'provenance'];
const ASSESS_METHODS = ['wsjf', 'rice', 'kano'];
const KANO = ['must-be', 'performance', 'delighter', 'indifferent', 'reverse', 'questionable'];
const proposalOperation = key => key === 'assess' ? 'assessment' : key;
const rating = v => v === null || (subset(v, ['urgency', 'importance', 'actor', 'timestamp']) &&
  ['urgency', 'importance', 'actor'].every(k => Object.hasOwn(v, k)) && validPriorities(v) &&
  meaningful(v.actor) && boundedText(v.actor, 200) && (!Object.hasOwn(v, 'timestamp') || (meaningful(v.timestamp) && boundedText(v.timestamp, 100))));
const numeric = (v, key) => typeof v === 'number' && Number.isFinite(v) && v >= 0 &&
  v <= (key === 'confidence' ? 1 : 1e12) && (key !== 'effort' || v > 0);
function assessmentBasis(value, partial) {
  if (!object(value)) return text(value) && (partial || meaningful(value));
  return Object.keys(value).length <= 100 && (partial || Object.keys(value).length > 0) &&
    Object.entries(value).every(([key, item]) => boundedText(key, 100) && text(item) && (partial || (meaningful(key) && meaningful(item))));
}
export function assessmentFields(value, partial = false) {
  if (!(partial ? subset(value, ASSESS_KEYS) : exact(value, ASSESS_KEYS))) return false;
  if (!optional(value.method, v => ASSESS_METHODS.includes(v)) ||
      !optional(value.version, v => boundedText(v, 100)) ||
      !['basis', 'provenance'].every(k => optional(value[k], v => assessmentBasis(v, partial))) ||
      !optional(value.assumptions, texts) || !optional(value.confidence, v => ['low', 'medium', 'high'].includes(v))) return false;
  const keys = value.method === 'wsjf' ? ['value', 'time_criticality', 'enablement', 'effort'] :
    value.method === 'rice' ? ['reach', 'impact', 'confidence', 'effort'] : value.method === 'kano' ? ['category', 'hypothesis'] :
      ['value', 'time_criticality', 'enablement', 'effort', 'reach', 'impact', 'confidence', 'category', 'hypothesis'];
  if (!optional(value.inputs, inputs => (partial ? subset(inputs, keys) : exact(inputs, keys)) &&
      Object.entries(inputs).every(([key, v]) => optional(v, item => key === 'category' ? KANO.includes(item) :
        key === 'hypothesis' ? typeof item === 'boolean' : numeric(item, key))))) return false;
  if (partial) return true;
  if (!ASSESS_METHODS.includes(value.method) || !meaningful(value.version) || !['low', 'medium', 'high'].includes(value.confidence) ||
      !assessmentBasis(value.basis, false) || !assessmentBasis(value.provenance, false) || !Array.isArray(value.assumptions) ||
      !value.assumptions.every(meaningful) || !exact(value.inputs, keys)) return false;
  if (value.method === 'kano') return KANO.includes(value.inputs.category) && typeof value.inputs.hypothesis === 'boolean';
  return Object.entries(value.inputs).every(([key, v]) => v === null || numeric(v, key)) &&
    (Object.values(value.inputs).some(v => v === null) || Number.isFinite(rawAssessmentScore(value)));
}
function rawAssessmentScore(value) {
  const i = value.inputs;
  if (value.method === 'kano' || Object.values(i).some(v => v === null)) return null;
  return value.method === 'wsjf' ? (i.value + i.time_criticality + i.enablement) / i.effort : i.reach * i.impact * i.confidence / i.effort;
}
export function assessmentScore(value) {
  return assessmentFields(value) ? rawAssessmentScore(value) : null;
}
export function positionFields(value, partial = false) {
  const keys = ['proposed_position', 'actual_position', 'neighbors', 'override_reason'];
  if (!(partial ? subset(value, keys) : exact(value, keys))) return false;
  if (!['proposed_position', 'actual_position'].every(k => optional(value[k], n => integer(n) && n > 0)) ||
      !optional(value.override_reason, text) || !optional(value.neighbors, n =>
        (partial ? subset(n, ['before', 'after']) : exact(n, ['before', 'after'])) &&
        Object.values(n).every(id => id === null || (typeof id === 'string' && IDEA.test(id))) &&
        (n.before == null || n.before !== n.after))) return false;
  return partial || (integer(value.proposed_position) && value.proposed_position > 0 && integer(value.actual_position) && value.actual_position > 0 &&
    exact(value.neighbors, ['before', 'after']) && (value.override_reason === null || text(value.override_reason)) && (value.proposed_position === value.actual_position || meaningful(value.override_reason)));
}
export function assessFields(value, partial = false) {
  return (partial ? subset(value, ['assessment', 'position']) : exact(value, ['assessment', 'position'])) &&
    (partial ? optional(value.assessment, v => assessmentFields(v, true)) && optional(value.position, v => positionFields(v, true)) :
      assessmentFields(value.assessment) && positionFields(value.position));
}
export const validAssess = fields => assessFields(fields);
export function insertionNeighbors(order, ideaId, position) {
  if (!Array.isArray(order) || !order.every(id => typeof id === 'string' && IDEA.test(id)) || new Set(order).size !== order.length ||
      !order.includes(ideaId) || !integer(position) || position < 1 || position > order.length) return null;
  const remaining = order.filter(id => id !== ideaId), index = position - 1;
  return {before: index > 0 ? remaining[index - 1] : null, after: index < remaining.length ? remaining[index] : null};
}
function assessmentRecord(value) {
  if (value === null) return true;
  if (!subset(value, [...ASSESS_KEYS, 'score', 'assessment_id', 'actor', 'timestamp']) ||
      ![...ASSESS_KEYS, 'score'].every(k => Object.hasOwn(value, k))) return false;
  const core = Object.fromEntries(ASSESS_KEYS.map(k => [k, value[k]]));
  return assessmentFields(core) && value.score === rawAssessmentScore(core) &&
    (!Object.hasOwn(value, 'assessment_id') || (typeof value.assessment_id === 'string' && /^assessment_[0-9a-f]{32}$/.test(value.assessment_id))) &&
    ['actor', 'timestamp'].every(k => !Object.hasOwn(value, k) || (meaningful(value[k]) && boundedText(value[k], 200)));
}
function boundedAssessmentTree(value) {
  const queue = [[value, 0]]; let nodes = 0;
  while (queue.length) {
    const [item, depth] = queue.pop(); if (++nodes > 20000 || depth > 24) return false;
    if (typeof item === 'string') { if (!boundedText(item, 1024 * 1024)) return false; }
    else if (typeof item === 'number') { if (!Number.isFinite(item)) return false; }
    else if (Array.isArray(item)) { for (const child of item) queue.push([child, depth + 1]); }
    else if (item !== null && typeof item === 'object') { for (const pair of Object.entries(item)) for (const child of pair) queue.push([child, depth + 1]); }
    else if (item !== null && typeof item !== 'boolean') return false;
  }
  return new TextEncoder().encode(JSON.stringify(value)).length <= 1024 * 1024;
}
function backlogFields(value, ideaId) {
  return boundedAssessmentTree(value) && exact(value, ['revision', 'order', 'comparisons']) && integer(value.revision) &&
    Array.isArray(value.order) && value.order.every(id => typeof id === 'string' && IDEA.test(id)) && new Set(value.order).size === value.order.length &&
    value.order.includes(ideaId) && Array.isArray(value.comparisons) && value.comparisons.length === value.order.length &&
    value.comparisons.every((item, index) => exact(item, ['idea_id', 'revision', 'status', 'ratings', 'assessment']) &&
      item.idea_id === value.order[index] && integer(item.revision) && item.revision >= 1 && ['active', 'archived'].includes(item.status) &&
      rating(item.ratings) && assessmentRecord(item.assessment));
}
function validateAssessmentState(state) {
  if (state.accepted?.assess != null && !validAssess(state.accepted.assess)) throw new Error('Invalid assessment fields');
  const keys = ['backlog', 'backlog_status', 'human_ratings', 'assessment_summary'];
  const required = Object.hasOwn(state.proposal_sources ?? {}, 'assessment') ||
    (Array.isArray(state.proposals) && state.proposals.some(p => p?.operation === 'assessment'));
  if (!required && !keys.some(k => Object.hasOwn(state, k))) return;
  if (!keys.every(k => Object.hasOwn(state, k)) || !exact(state.backlog_status, ['available', 'code']) ||
      typeof state.backlog_status.available !== 'boolean' || !rating(state.human_ratings) || !assessmentRecord(state.assessment_summary)) throw new Error('Invalid assessment projection');
  if (state.backlog_status.available) {
    if (state.idea_id === null || state.backlog_status.code !== 'ok' || !backlogFields(state.backlog, state.idea_id) ||
        state.backlog.revision !== state.backlog_revision) throw new Error('Invalid assessment backlog');
    const target = state.backlog.comparisons[state.backlog.order.indexOf(state.idea_id)];
    if (target.revision !== state.revision || !same(target.ratings, state.human_ratings) || !same(target.assessment, state.assessment_summary)) throw new Error('Invalid assessment comparison');
  } else if (state.backlog !== null || state.backlog_status.code !== (state.idea_id === null ? 'no_selection' : 'source_too_large') ||
      (state.idea_id === null && (state.human_ratings !== null || state.assessment_summary !== null))) throw new Error('Invalid assessment availability');
  for (const fields of [state.drafts?.assess, state.draft?.step === 'assess' ? state.draft.fields : null]) {
    if (fields != null && !assessFields(fields, true)) throw new Error('Invalid assessment fields');
  }
}
function assessmentSourceData(data, state) {
  if (!boundedAssessmentTree(data) || !exact(data, ['steps', 'backlog', 'target']) || !exact(data.steps, ['capture', 'priorities', 'shape']) ||
      !exact(data.steps.capture, ['raw_text', 'workspace']) || !exact(data.steps.capture.workspace, ['name', 'path', 'confirmed']) ||
      !validCapture(data.steps.capture) || !boundedText(data.steps.capture.raw_text, 1024 * 1024) ||
      !text(data.steps.capture.workspace.name) || !text(data.steps.capture.workspace.path) ||
      !exact(data.steps.priorities, ['urgency', 'importance']) || !validPriorities(data.steps.priorities) || !validShape(data.steps.shape) ||
      !backlogFields(data.backlog, state.idea_id) || !state.backlog_status?.available || !same(data.backlog, state.backlog) ||
      !(data.target === null || assessFields(data.target, true))) return false;
  if (!['capture', 'priorities', 'shape'].every(k => state.steps[k].status === 'saved' && same(data.steps[k], state.accepted?.[k]))) return false;
  const target = data.backlog.comparisons[data.backlog.order.indexOf(state.idea_id)];
  return target.revision === state.revision && target.ratings !== null &&
    same({urgency: target.ratings.urgency, importance: target.ratings.importance}, data.steps.priorities) &&
    same(data.target, state.drafts?.assess ?? (state.draft?.step === 'assess' ? state.draft.fields : null) ?? state.accepted?.assess ?? null);
}
function assessmentProposal(value, ideaId) {
  return validAssess(value) && value.position.actual_position === value.position.proposed_position && value.position.override_reason === null &&
    !Object.values(value.position.neighbors).includes(ideaId);
}
function sourceData(data) {
  if (!subset(data, ['capture', 'shape', 'method'])) return false;
  for (const [step, fields] of Object.entries(data)) {
    if (step === 'shape' && !shapeFields(fields, true)) return false;
    if (step === 'method' && !methodFields(fields, true)) return false;
    if (step === 'capture' && !(subset(fields, ['raw_text', 'workspace']) &&
      optional(fields.raw_text, v => boundedText(v, 1024*1024)) &&
      optional(fields.workspace, w => subset(w, ['name', 'path', 'confirmed']) && optional(w.name, text) && optional(w.path, text) && optional(w.confirmed, v => typeof v === 'boolean')))) return false;
  }
  return true;
}
// Mirrors idea_proposals.FILL_KEYS: what a terminal conversation may fill (the human owns the rest).
export const FILL_KEYS = Object.freeze({shape: ['outcome', 'scope', 'scope_reason', 'alternatives', 'assumptions', 'next_slice', 'learning'],
  method: ['reason'], assessment: ['assessment', 'proposed_position']});
function validateAgent(state) {
  // Legacy intermediate state has no proposal provider. Fail closed on any
  // partial provider projection rather than inventing connected assistance.
  if (!['agent_generation', 'proposal_sources', 'proposals', 'proposal_inventory'].some(k => Object.hasOwn(state, k))) return;
  if (!['connected', 'paused', 'disconnected'].includes(state.agent_status) ||
      !(state.idea_id === null || (typeof state.idea_id === 'string' && /^idea_[0-9a-f]{32}$/.test(state.idea_id))) ||
      !(state.agent_generation === null || (typeof state.agent_generation === 'string' && GENERATION.test(state.agent_generation))) ||
      (state.agent_status === 'connected' && state.agent_generation === null) ||
      !subset(state.proposal_sources, ['shape', 'method', 'memory', 'assessment']) || !Array.isArray(state.proposals) || state.proposals.length > 128 ||
      (state.idea_id === null && state.proposals.length !== 0)) throw new Error('Invalid agent projection');
  for (const [operation, entry] of Object.entries(state.proposal_sources)) {
    if (!exact(entry, ['available', 'code', 'source']) || typeof entry.available !== 'boolean' || typeof entry.code !== 'string') throw new Error('Invalid proposal source');
    if (!entry.available) {
      if (entry.source !== null || !['not_ready', 'operation_unavailable', 'agent_unavailable', 'source_too_large', 'source_projection_capacity'].includes(entry.code)) throw new Error('Invalid proposal source');
    } else {
      const source = entry.source;
      if (entry.code !== 'ok' || !exact(source, ['accepted_revision', 'draft_version', 'data', 'source_digest']) ||
          !integer(source.accepted_revision) || source.accepted_revision < 1 || source.accepted_revision !== state.revision ||
          !integer(source.draft_version) || source.draft_version !== state.draft_version || !HASH.test(source.source_digest) || typeof source.source_digest !== 'string' || !(operation === 'assessment' ? assessmentSourceData(source.data, state) : sourceData(source.data))) throw new Error('Invalid proposal source');
    }
  }
  const ids = new Set();
  for (const item of state.proposals) {
    if (!exact(item, ['proposal_id', 'request_id', 'operation', 'accepted_revision', 'draft_version', 'source_digest', 'proposal', 'stale', 'stale_reason', 'acceptance_eligible', 'acceptance_reason', 'evidence', 'content_omitted']) ||
        typeof item.proposal_id !== 'string' || !PROPOSAL.test(item.proposal_id) || ids.has(item.proposal_id) ||
        typeof item.request_id !== 'string' || !REQUEST.test(item.request_id) || !['shape', 'method', 'memory', 'assessment'].includes(item.operation) ||
        !integer(item.accepted_revision) || item.accepted_revision < 1 || !integer(item.draft_version) || typeof item.source_digest !== 'string' || !HASH.test(item.source_digest) ||
        typeof item.content_omitted !== 'boolean' || !exact(item.evidence, ['path', 'sha256']) ||
        typeof item.evidence.path !== 'string' || !new RegExp('^history/' + state.idea_id + '/metadata/[0-9a-f]{64}\\.md$').test(item.evidence.path) ||
        typeof item.evidence.sha256 !== 'string' || !HASH.test(item.evidence.sha256) ||
        (item.content_omitted ? item.proposal !== null || item.acceptance_eligible !== false || item.acceptance_reason !== 'projection_omitted' :
          !(item.operation === 'shape' ? validShape(item.proposal) : item.operation === 'method' ? validMethod(item.proposal) : item.operation === 'assessment' ? assessmentProposal(item.proposal, state.idea_id) : memory(item.proposal)))) throw new Error('Invalid proposal summary');
    for (const [flag, reason, positive] of [['stale', 'stale_reason', false], ['acceptance_eligible', 'acceptance_reason', true]]) {
      if (typeof item[flag] !== 'boolean' || (item[flag] === positive ? item[reason] !== null : !(REASONS.includes(item[reason]) || (reason === 'acceptance_reason' && item.content_omitted && item[reason] === 'projection_omitted')))) throw new Error('Invalid proposal eligibility');
    }
    if (state.agent_status !== 'connected' && item.acceptance_eligible) throw new Error('Invalid proposal eligibility');
    ids.add(item.proposal_id);
  }
  // The open terminal conversation: typed fills the page applies to the step buffer.
  const talk = state.conversation, fillKeys = FILL_KEYS[talk?.operation];
  if (talk !== undefined && talk !== null && (!exact(talk, ['request_id', 'operation', 'idea_id', 'accepted_revision', 'fills']) || !fillKeys ||
      state.agent_status !== 'connected' || typeof talk.request_id !== 'string' || !REQUEST.test(talk.request_id) ||
      talk.idea_id !== state.idea_id || talk.accepted_revision !== state.revision || !Array.isArray(talk.fills) || talk.fills.length > 64 ||
      talk.fills.some((item, index) => !exact(item, ['sequence', 'fields']) || item.sequence !== index + 1 || item.fields === null ||
        typeof item.fields !== 'object' || Array.isArray(item.fields) || !Object.keys(item.fields).length ||
        !Object.keys(item.fields).every(key => fillKeys.includes(key))))) throw new Error('Invalid agent conversation');
  const inventory = state.proposal_inventory;
  if (!exact(inventory, ['total', 'projected', 'omitted', 'content_omitted', 'index_path']) ||
      !['total', 'projected', 'omitted', 'content_omitted'].every(key => integer(inventory[key])) ||
      inventory.projected !== state.proposals.length || inventory.total !== inventory.projected + inventory.omitted ||
      inventory.content_omitted !== state.proposals.filter(item => item.content_omitted).length ||
      inventory.index_path !== (state.idea_id === null ? null : state.idea_id + '.md') ||
      (state.idea_id === null && inventory.total !== 0)) throw new Error('Invalid proposal inventory');
}

// Plain-words status line for a conversation step (shape, method, assess).
export function conversationStatus(flow, key) {
  const state = flow.state;
  if (state?.agent_status !== 'connected') return 'No agent is connected. You can fill this step by hand, or reinvoke /glitch-idea in your terminal to be guided.';
  const open = state.conversation?.operation === proposalOperation(key);
  if (!open && ['sending', 'waiting'].includes(flow.proposalPending?.phase) && flow.proposalPending.key === key) return 'Asking your terminal to start this step…';
  const count = flow.filled[key]?.size ?? 0;
  return 'Your terminal is guiding this step. Answer there; agreed answers appear here as you go.' +
    (count > 0 ? ' ' + count + (count === 1 ? ' answer' : ' answers') + ' filled from your terminal.' : '');
}
// Marks one field the terminal conversation filled; the human editing it removes the mark.
export function filledNote({flow, key, name, id, element, target, input}) {
  if (!flow.filled[key]?.has(name)) return null;
  const note = element('p', 'From your terminal conversation', 'filled-note');
  note.id = id + '-filled';
  input?.setAttribute?.('aria-describedby', note.id);
  if (input?.parentNode?.insertBefore) input.parentNode.insertBefore(note, input); else target.append(note);
  return note;
}

export class Flow {
  constructor(api, idFactory = () => globalThis.crypto.randomUUID(), clock = () => Date.now(), storage = tabStorage(), fillStorage = tabStorage()) {
    this.fillStorage = fillStorage;  // applied-fill sequences only, apart from the suggestion links
    this.api = api;
    this.idFactory = idFactory;
    this.clock = clock;
    this.proposalPending = null;
    this.proposalError = null;
    this.selectedProposals = {};
    this.selectionStorage = storage;
    this.selectionScope = null;
    this.selectionBinding = undefined;
    this.selectionIdea = undefined;
    this.selectionStorageAvailable = false;
    this.refreshing = false;
    this.refreshPromise = null;
    this.disposed = false;
    this.pollTimer = null;
    this.pollEpoch = 0;
    this.refreshUnauthorized = false;
    this.mutationEpoch = 0;
    this.view = 'workflow';
    this.ideas = null;
    this.ideasLoading = false;
    this.ideasError = null;
    this.selectionUncertain = false;
    this.selectionPollOptions = null;
    this.polling = false;
    this.pollOptions = null;
    this.state = null;
    this.current = 'capture';
    this.buffers = defaultBuffers();
    this.dirty = new Set();
    // Terminal conversation (R2): fields the agent filled, per step; last fill applied per request; automatic attempts already made.
    this.filled = {};
    this.fillApplied = new Map();
    this.fillIdea = null;
    this.fillSave = null;
    this.converseTried = new Set();
    this.pending = null;
    this.busy = false;
    this.paused = false;
    this.error = null;
    this.message = 'Connect to load your saved idea.';
    this.onChange = () => {};
  }

  validateState(state) {
    if (!state || !state.ok || !KEYS.includes(state.current_step) ||
        !['revision', 'draft_version', 'backlog_revision'].every(key => Number.isInteger(state[key]) && state[key] >= 0)) {
      throw new Error('Invalid state response');
    }
    for (const key of KEYS) {
      const evidence = state.steps?.[key];
      if (!evidence || !STATUSES.has(evidence.status) ||
          (COMPLETE.has(evidence.status) && (!evidence.evidence_id || !Number.isInteger(evidence.accepted_revision)))) {
        throw new Error('Invalid step evidence');
      }
    }
    validateAssessmentState(state);
    validateAgent(state);
    const visualDraft = state.drafts?.visualize ?? (state.draft?.step === 'visualize' ? state.draft.fields : null);
    if ((visualDraft != null && !visualizeFields(visualDraft, true)) ||
        (state.accepted?.visualize != null && (!validVisualize(state.accepted.visualize) ||
          (state.accepted.visualize.disposition === 'accepted_set' && state.accepted.visualize.design_set_id === null)))) {
      throw new Error('Invalid Visualize state');
    }
    if (Object.hasOwn(state, 'handoff') || Object.hasOwn(state, 'handoff_status')) {
      if (!Object.hasOwn(state, 'handoff') || !exact(state.handoff_status, ['available', 'code']) ||
          typeof state.handoff_status.available !== 'boolean' || typeof state.handoff_status.code !== 'string') throw failure('invalid_response');
      const packet = state.handoff === null ? null : this.api.validateHandoff(state.handoff, state.idea_id);
      if ((state.idea_id === null && packet !== null) || (packet && packet.source_revision > state.revision) ||
          (state.handoff_status.available && (!packet || state.handoff_status.code !== 'ok' || packet.source_revision !== state.revision ||
            state.steps.review.status !== 'saved' || !same(state.accepted?.review,
              {handoff_id: packet.handoff_id, source_revision: packet.source_revision})))) throw failure('invalid_response');
    }
  }

  load(state, preserve = false) {
    this.validateState(state);
    this.state = clone(state);
    this.restoreSelections();
    this.reconcileProposal();
    if (!preserve) {
      this.current = state.current_step;
      this.dirty.clear();
    }
    for (const key of KEYS) {
      if (key === 'review') continue; // Derived packet evidence has no editable buffer.
      if (preserve && this.dirty.has(key)) continue;
      const fields = state.drafts?.[key] ?? (state.draft?.step === key ? state.draft.fields : null) ?? state.accepted?.[key];
      if (fields) this.buffers[key] = clone(fields);
    }
    this.applyFills();
    this.onChange();
  }

  storedFill(requestId) {
    try {
      const raw = this.fillStorage?.getItem('glitch-idea-fills');
      const value = raw ? JSON.parse(raw) : {};
      return Number.isSafeInteger(value?.[requestId]) ? value[requestId] : undefined;
    } catch { return undefined; }
  }

  // Cursors only move forward: in memory always; in tab storage when `durable` (the draft holds them).
  rememberFill(requestId, sequence, durable = true) {
    this.fillApplied.set(requestId, Math.max(sequence, this.fillApplied.get(requestId) ?? 0));
    if (!durable) return;
    try {
      const raw = this.fillStorage?.getItem('glitch-idea-fills');
      const value = raw ? JSON.parse(raw) : {};
      const kept = Object.fromEntries(Object.entries(value && typeof value === 'object' ? value : {}).filter(([, n]) => Number.isSafeInteger(n)).slice(-15));
      kept[requestId] = Math.max(sequence, Number.isSafeInteger(kept[requestId]) ? kept[requestId] : 0);
      this.fillStorage?.setItem('glitch-idea-fills', JSON.stringify(kept));
    } catch { /* the in-memory record still holds for this page */ }
  }

  // Save applied fills as the draft; when another write is in flight, wait for it (bounded), then save.
  async saveFills(key, requestId, ideaId) {
    for (let waited = 0; (this.busy || this.pending) && waited < 30000 && !this.disposed; waited += 100) {
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    // Bound to the idea and conversation that applied the fills: never save another idea's buffer.
    if (this.state?.idea_id !== ideaId || this.state?.conversation?.request_id !== requestId) return false;
    if (!this.busy && !this.pending && !this.dirty.has(key)) {
      // Nothing left to save: record the cursor only if the authoritative saved draft holds these fills.
      if (same(this.state?.drafts?.[key] ?? null, this.buffers[key])) this.rememberFill(requestId, this.fillApplied.get(requestId) ?? 0, true);
      return true;
    }
    if (this.busy || this.pending || this.selectionUncertain || this.disposed || !this.dirty.has(key)) return false;
    // The save carries the cursor and persists it on success (finishPending), also after a retry.
    return this.save(key, true).catch(() => false);
  }

  // Apply each not-yet-applied fill of the open terminal conversation, in order, once.
  // The page stays the only writer of the draft; the agent never accepts.
  applyFills() {
    const state = this.state;
    if (this.fillIdea !== (state?.idea_id ?? null)) {
      this.fillIdea = state?.idea_id ?? null; this.filled = {}; this.fillApplied = new Map();
    }
    const talk = state?.conversation;
    if (!talk || talk.idea_id !== state.idea_id || talk.accepted_revision !== state.revision) return false;
    const key = talk.operation === 'assessment' ? 'assess' : talk.operation;
    if (!['shape', 'method', 'assess'].includes(key) || state.steps?.[key]?.status === 'saved') return false;
    // A request this tab sent starts at 0; after a reload the tab's record continues it. A
    // conversation this tab never saw is adopted as already applied: never overwrite later edits.
    let last = this.fillApplied.get(talk.request_id);
    if (last === undefined) last = this.storedFill(talk.request_id);
    if (last === undefined) { last = talk.fills.length ? talk.fills.at(-1).sequence : 0; this.rememberFill(talk.request_id, last); }
    const fresh = talk.fills.filter(item => item.sequence > last).sort((a, b) => a.sequence - b.sequence);
    if (!fresh.length) return false;
    this.filled[key] ??= new Set();
    const allowed = FILL_KEYS[talk.operation] ?? [];
    for (const {fields: sent} of fresh) {
      const fields = Object.fromEntries(Object.entries(sent).filter(([name]) => allowed.includes(name)));
      if (!Object.keys(fields).length) continue;  // nothing this conversation may fill: no change, no save
      if (key === 'assess') {
        const next = clone(this.buffers.assess), position = {proposed_position: null, actual_position: null,
          neighbors: {before: null, after: null}, override_reason: null, ...(next.position ?? {})};
        if (Object.hasOwn(fields, 'assessment')) next.assessment = clone(fields.assessment);
        if (Object.hasOwn(fields, 'proposed_position')) {
          // The human decides the actual position. A fill only starts it when it is empty; after that a
          // new proposal changes the AI proposed position alone, and any difference shows (override reason).
          const follows = position.actual_position === null;
          position.proposed_position = fields.proposed_position;
          if (follows) {
            position.actual_position = fields.proposed_position;
            position.neighbors = insertionNeighbors(state.backlog?.order, state.idea_id, fields.proposed_position) ?? {before: null, after: null};
          }
        }
        next.position = position; this.buffers.assess = next;
      } else this.buffers[key] = {...this.buffers[key], ...clone(fields)};
      for (const name of Object.keys(fields)) this.filled[key].add(name);
    }
    const sequence = fresh.at(-1).sequence;
    // In memory at once (never applied twice on this page); in tab storage only once the draft holds
    // it, so a failed save leaves the fill to be applied again after a reload.
    this.rememberFill(talk.request_id, sequence, false);
    const applied = fresh.reduce((sum, item) => sum + Object.keys(item.fields).filter(name => allowed.includes(name)).length, 0);
    if (!applied) { this.rememberFill(talk.request_id, sequence); return false; }
    this.dirty.add(key);
    this.message = 'Your terminal filled ' + applied + ' answer(s) here.';
    this.fillSave = this.saveFills(key, talk.request_id, state.idea_id);
    return true;
  }

  // Reaching Shape, Method or Assess starts the terminal conversation: one automatic request
  // per idea, revision, step and agent session. A failed or refused attempt is never retried automatically.
  async autoConverse(key) {
    const state = this.state;
    if (!['shape', 'method', 'assess'].includes(key) || !state?.idea_id || state.agent_status !== 'connected' || this.disposed) return false;
    if (state.steps?.[key]?.status === 'saved' || this.proposalPending) return false;
    const operation = proposalOperation(key);
    if (state.conversation?.operation === operation) return false;
    if (this.proposals(key).some(item => !item.stale && item.accepted_revision === state.revision)) return false;
    if (!this.canPropose(key)) return false;
    const tag = [state.idea_id, state.revision, key, state.agent_generation].join('|');
    if (this.converseTried.has(tag)) return false;
    this.converseTried.add(tag);
    return this.requestProposal(key);
  }

  authorityIdentity(ideaId = this.state?.idea_id ?? null) {
    return {idea_id: ideaId, session_id: this.state?.session_id ?? this.api.sessionId ?? null};
  }

  assertAuthority(state, identity) {
    if (state?.idea_id !== identity.idea_id || (state?.session_id ?? null) !== identity.session_id) {
      throw Object.assign(failure('invalid_response', true), {selectionMismatch: true});
    }
    this.validateState(state);
    return state;
  }

  freezeSelection(error) {
    if (this.polling) this.selectionPollOptions = this.pollOptions;
    this.mutationEpoch++;
    this.stopAgentRefresh();
    this.selectionUncertain = true;
    this.error = error;
    this.message = 'The selected idea could not be verified. Your answers remain; restore this selection before saving.';
    if (!this.disposed) this.onChange();
  }

  // adoptSelection: a query-less first read may return the session's private selection
  // (browser-api.md); adopt it only while the predicate still holds after the await.
  async readAuthority(ideaId = this.state?.idea_id ?? null, sessionId = this.state?.session_id ?? this.api.sessionId ?? null, {adoptSelection = null} = {}) {
    const identity = {idea_id: ideaId, session_id: sessionId}, epoch = this.mutationEpoch;
    const state = await this.api.state(ideaId);
    if (this.disposed || epoch !== this.mutationEpoch) throw failure('state_read_invalidated');
    if (ideaId === null && adoptSelection?.() === true && typeof state?.idea_id === 'string') identity.idea_id = state.idea_id;
    try { return this.assertAuthority(state, identity); }
    catch (error) {
      if (error.selectionMismatch) this.freezeSelection(error);
      throw error;
    }
  }

  status(key) {
    if (this.dirty.has(key)) return 'unsaved';
    const persisted = this.state?.steps[key]?.status ?? (key === 'capture' ? 'current' : 'todo');
    if (COMPLETE.has(persisted) && [...this.dirty].some(source => DEPENDENTS[source]?.includes(key))) return 'review-needed';
    return persisted;
  }

  progress() {
    return {saved: KEYS.filter(key => this.status(key) === 'saved').length,
      skipped: KEYS.filter(key => ['skipped', 'not-applicable'].includes(this.status(key))).length};
  }

  canOpen(key) {
    if (!KEYS.includes(key) || this.busy || this.paused) return false;
    if (key === this.current || this.status(key) !== 'todo') return true;
    return KEYS.slice(0, KEYS.indexOf(key)).every(prior => COMPLETE.has(this.status(prior)));
  }

  open(key) {
    if (!this.canOpen(key)) return false;
    this.current = key;
    this.onChange();
    return true;
  }

  // The automatic conversation request still single-flights other writes, but never
  // locks the human's typing: what they type while it is sent is kept and saved after.
  inputLocked() { return this.busy && !this.proposalFlight && !this.draftFlight; }

  edit(key, fields) {
    if (this.inputLocked()) return;
    const marks = this.filled[key];
    if (marks?.size) {
      const value = (buffer, name) => key === 'assess' ? (name === 'assessment' ? buffer?.assessment : buffer?.position?.proposed_position) : buffer?.[name];
      for (const name of [...marks]) if (!same(value(this.buffers[key], name), value(fields, name))) marks.delete(name);
    }
    this.buffers[key] = clone(fields);
    if (this.busy) {
      // Typing during a draft save: keep the words and mark them unsaved, nothing else. The write in
      // flight owns `pending`, and "clean" can only be judged after its reload (finishPending).
      this.dirty.add(key);
      this.message = 'Unsaved changes';
      this.onChange();
      return;
    }
    const saved = this.state?.drafts?.[key] ?? (this.state?.draft?.step === key ? this.state.draft.fields : null) ?? this.state?.accepted?.[key];
    if (saved && same(fields, saved)) this.dirty.delete(key);
    else this.dirty.add(key);
    if (this.pending && !this.pending.ambiguous && !this.isConflict()) this.pending = null;
    this.message = 'Unsaved changes';
    this.onChange();
  }

  isConflict() { return this.error?.status === 409; }

  restoreSelections() {
    const binding = this.api.bindingId, idea = this.state?.idea_id;
    const scope = typeof binding === 'string' && BINDING.test(binding) && typeof idea === 'string' && IDEA.test(idea)
      ? 'idea-proposal-selections:' + binding + ':' + idea : null;
    if (binding === this.selectionBinding && idea === this.selectionIdea) return;
    this.selectionBinding = binding;
    this.selectionIdea = idea;
    this.selectionScope = scope;
    this.selectedProposals = {};
    this.selectionStorageAvailable = false;
    if (!scope || !this.selectionStorage) return;
    try {
      const raw = this.selectionStorage.getItem(scope);
      this.selectionStorageAvailable = true;
      if (raw === null) return;
      if (typeof raw !== 'string' || raw.length > 256) throw new Error('Invalid selection storage');
      const selected = JSON.parse(raw);
      if (!subset(selected, ['shape', 'method', 'assess']) || !Object.values(selected).every(id => typeof id === 'string' && PROPOSAL.test(id))) {
        throw new Error('Invalid selection storage');
      }
      // IDs are associations, never authority. Missing, stale or omitted IDs
      // remain pinned and are refused by save until explicit human unlinking.
      this.selectedProposals = clone(selected);
    } catch {
      this.selectionStorageAvailable = false;
    }
  }

  persistSelections() {
    if (!this.selectionScope || !this.selectionStorage) return this.selectionStorageAvailable = false;
    try {
      const selected = Object.fromEntries(['shape', 'method', 'assess'].filter(key => this.selectedProposals[key])
        .map(key => [key, this.selectedProposals[key]]));
      if (Object.keys(selected).length) this.selectionStorage.setItem(this.selectionScope, JSON.stringify(selected));
      else this.selectionStorage.removeItem(this.selectionScope);
      this.selectionStorageAvailable = true;
    } catch { this.selectionStorageAvailable = false; }
    return this.selectionStorageAvailable;
  }

  selectionWarning() {
    return Object.keys(this.selectedProposals).length && !this.selectionStorageAvailable
      ? ' Browser storage is unavailable. Reload will not reliably retain the suggestion link; use the suggestion again before accepting.' : '';
  }

  envelope(operation, key) {
    if (!this.state) throw new Error('Load the saved state before writing');
    const fields = clone(this.buffers[key]);
    const request_id = this.idFactory();
    if (operation === 'capture') return {request_id, ...fields};
    return {request_id, idea_id: this.state.idea_id, expected_revision: this.state.revision,
      expected_draft_version: this.state.draft_version, step: key, fields,
      ...(operation === 'accept' ? {proposal_id: this.selectedProposals[key] ?? null, expected_backlog_revision: key === 'assess' ? this.state.backlog.revision : null} : {})};
  }

  async save(key, draft = false) {
    if (this.busy || this.selectionUncertain) return false;
    if (this.pending) throw new Error('Resolve or retry the previous request first');
    const operation = !this.state?.idea_id ? 'capture' : draft ? 'draft' : 'accept';
    if (operation === 'capture' && !validCapture(this.buffers.capture)) return false;
    if (!draft && key === 'priorities' && !validPriorities(this.buffers.priorities)) return false;
    if (!draft && key === 'shape' && !validShape(this.buffers.shape)) return false;
    if (!draft && key === 'method' && !validMethod(this.buffers.method)) return false;
    if (!draft && key === 'assess' && (!validAssess(this.buffers.assess) || !this.state?.backlog_status?.available ||
        !same(this.buffers.assess.position.neighbors, insertionNeighbors(this.state.backlog.order, this.state.idea_id, this.buffers.assess.position.actual_position)) ||
        this.buffers.assess.position.proposed_position > this.state.backlog.order.length)) return false;
    if (!draft && this.selectedProposals[key]) {
      const selected = this.proposals(key).find(item => item.proposal_id === this.selectedProposals[key]);
      if (selected?.content_omitted) {
        this.proposalError = {code: 'projection_omitted'};
        this.message = 'This suggestion is saved in Markdown. Its body is omitted from this bounded browser view. Explicitly choose your answers as manual or request a new suggestion before saving.';
        this.onChange(); return false;
      }
      if (this.state.agent_status !== 'connected' || !selected?.acceptance_eligible) {
        this.proposalError = {code: selected?.acceptance_reason ?? 'stale_source'};
        this.message = 'This suggestion is no longer eligible. Request another suggestion or explicitly choose your answers as manual.';
        this.onChange(); return false;
      }
      if (key === 'assess' && this.buffers.assess.position.proposed_position !== selected.proposal.position.proposed_position) {
        this.proposalError = {code: 'proposal_mismatch'};
        this.message = 'The original proposed position changed. Restore it or explicitly choose your answers as manual; your actual position override remains editable.';
        this.onChange(); return false;
      }
    }
    const talk = this.state?.conversation;
    const fillCursor = talk && this.fillApplied.has(talk.request_id) ? {requestId: talk.request_id, sequence: this.fillApplied.get(talk.request_id)} : null;
    this.pending = {operation, key, payload: this.envelope(operation, key), ambiguous: false, fillCursor};
    return this.runPending();
  }

  async saveVisualize(assetIds = null) {
    if (this.busy || this.selectionUncertain) return false;
    if (this.pending) throw new Error('Resolve or retry the previous request first');
    if (typeof this.state?.idea_id !== 'string' || !IDEA.test(this.state.idea_id) || !validVisualize(this.buffers.visualize)) return false;
    const payload = this.envelope('accept', 'visualize');
    let operation = 'visual-disposition';
    if (payload.fields.disposition === 'accepted_set') {
      operation = 'visual-set/accept';
      const setId = payload.fields.design_set_id;
      if (setId === null ? !Array.isArray(assetIds) || assetIds.length < 1 || assetIds.length > 20 ||
          assetIds.some(id => typeof id !== 'string' || !ASSET.test(id)) || new Set(assetIds).size !== assetIds.length : assetIds !== null) return false;
      payload.design_set_id = setId;
      payload.asset_ids = clone(assetIds);
    } else if (assetIds !== null) return false;
    this.pending = {operation, key: 'visualize', payload, ambiguous: false};
    return this.runPending();
  }

  handoffLocal(pending = null) {
    return !this.disposed && !this.paused && !this.selectionUncertain && this.dirty.size === 0 && !this.proposalPending &&
      typeof this.state?.idea_id === 'string' && IDEA.test(this.state.idea_id) && this.state.idea_status === 'active' &&
      this.state.capabilities?.handoff === true && (!pending || this.state.idea_id === pending.payload.idea_id) &&
      KEYS.slice(0, -1).every(key => COMPLETE.has(this.status(key)));
  }

  canGenerateHandoff() { return !this.busy && !this.pending && this.handoffLocal(); }

  canCopyHandoff() {
    if (!this.canGenerateHandoff() || this.state?.handoff_status?.available !== true ||
        ['saved_state_changed', 'stale_source', 'invalid_response'].includes(this.error?.code)) return false;
    try { return this.api.validateHandoff(this.state.handoff, this.state.idea_id).source_revision === this.state.revision; }
    catch { return false; }
  }

  async generateHandoff() {
    if (!this.canGenerateHandoff()) return false;
    this.pending = {operation: 'handoff', key: 'review', ambiguous: false,
      payload: {request_id: this.idFactory(), idea_id: this.state.idea_id, expected_revision: this.state.revision,
        expected_draft_version: this.state.draft_version, expected_backlog_revision: this.state.backlog_revision}};
    return this.runPending();
  }

  finishHandoff(pending, state, result, recovered) {
    const payload = pending.payload;
    if (result?.ok !== true || result.request_id !== payload.request_id || result.idea_id !== payload.idea_id ||
        !['applied', 'no_op'].includes(result.write_state) || result.revision !== payload.expected_revision ||
        result.draft_version !== payload.expected_draft_version || result.backlog_revision !== payload.expected_backlog_revision ||
        typeof result.handoff_current !== 'boolean' || result.code !== (result.handoff_current ? 'ok' : 'historical_handoff') ||
        result.handoff_id !== result.handoff?.handoff_id || result.sha256 !== result.handoff?.sha256) throw failure('invalid_response', true);
    const packet = this.api.validateHandoff(result.handoff, payload.idea_id);
    if (packet.source_revision !== result.revision || state?.idea_id !== payload.idea_id || state.session_id !== this.state?.session_id) throw failure('invalid_response', true);
    this.validateState(state);
    if (this.disposed || this.pending !== pending) return false;
    const local = this.handoffLocal(pending);
    this.load(state, true);
    this.pending = null;
    if (!local || !this.handoffLocal() || !result.handoff_current || state.handoff_status?.available !== true ||
        !state.handoff || !same(packetIdentity(packet), packetIdentity(state.handoff))) {
      this.error = Object.assign(failure('saved_state_changed'), {status: 409});
      this.message = 'The packet was saved, but its current source changed. Review before generating or copying again.';
      return false;
    }
    this.error = null; this.current = 'review';
    this.message = recovered ? 'Planning prompt recovered. Copy it into a new window.' : 'Planning prompt ready. Copy it into a new window.';
    return true;
  }

  async currentCopy(expectedPacket = null) {
    if (!this.canCopyHandoff()) return null;
    const idea = this.state.idea_id, epoch = this.mutationEpoch;
    let pinned;
    try { pinned = this.api.validateHandoff(expectedPacket ?? this.state.handoff, idea); }
    catch (error) { this.error = error; this.onChange(); return null; }
    try {
      const state = await this.readAuthority(idea);
      if (this.disposed || epoch !== this.mutationEpoch || this.state?.idea_id !== idea || !this.canCopyHandoff()) return null;
      if (state?.idea_id !== idea || state.session_id !== this.state.session_id) throw failure('invalid_response');
      this.validateState(state);
      this.load(state, true);
      if (!this.canCopyHandoff() || !same(packetIdentity(pinned), packetIdentity(state.handoff))) throw failure('stale_source');
      this.error = null;
      return this.api.validateHandoff(state.handoff, idea);
    } catch (error) {
      if (!this.disposed && epoch === this.mutationEpoch && this.state?.idea_id === idea) {
        this.error = error; this.message = 'This prompt is no longer verified as current. Review before copying.'; this.onChange();
      }
      return null;
    }
  }

  async loadIdeas() {
    if (this.disposed || this.ideasLoading || this.busy || this.selectionUncertain) return false;
    const epoch = this.mutationEpoch;
    this.ideasLoading = true; this.ideasError = null; this.onChange();
    try {
      const rows = await this.api.ideas();
      if (this.disposed || epoch !== this.mutationEpoch) return false;
      this.ideas = clone(rows); return true;
    } catch (error) {
      if (!this.disposed && epoch === this.mutationEpoch) this.ideasError = error;
      return false;
    } finally { this.ideasLoading = false; if (!this.disposed) this.onChange(); }
  }

  // Setup is a pure page view: nothing is loaded or saved by opening it.
  showSetup() { this.view = 'setup'; this.onChange(); return true; }

  async showIdeas() {
    if (!await this.loadIdeas() || this.disposed) return false;
    this.view = 'ideas'; this.onChange(); return true;
  }

  // A request that is only waiting on the terminal never holds the human in this idea.
  waitingOnTerminal() { return this.proposalPending?.phase === 'waiting'; }

  async selectIdea(ideaId) {
    if (!(ideaId === null || (typeof ideaId === 'string' && IDEA.test(ideaId))) || this.disposed || this.paused ||
        this.busy || this.pending || (this.proposalPending && !this.waitingOnTerminal()) || this.dirty.size) return false;
    // Leaving drops the local wait; the next step's request supersedes the old one on the service.
    if (this.proposalPending) { this.proposalPending = null; this.proposalError = null; }
    return this.selectAuthority(ideaId, false);
  }

  async restoreSelection() {
    if (this.state === null || this.disposed || this.busy) return false;
    // Restore ONLY the already selected identity, preserving drafts and receipts.
    return this.selectAuthority(this.state.idea_id, true);
  }

  async selectAuthority(ideaId, preserve) {
    const identity = this.authorityIdentity(ideaId), previousError = this.error;
    const polling = this.polling || this.selectionPollOptions !== null, options = this.selectionPollOptions ?? this.pollOptions;
    if (polling) this.selectionPollOptions = options;
    this.mutationEpoch++; this.stopAgentRefresh();
    // Invalidate the old flight without letting its finalizer clear a new one.
    this.refreshPromise = null; this.refreshing = false;
    this.busy = true; this.error = null; this.onChange();
    let selected = false;
    try {
      const result = await this.api.selection(ideaId);
      selected = true;
      if (result?.ok !== true || result.code !== 'ok' || result.idea_id !== ideaId || result.session_id !== identity.session_id ||
          !['revision', 'draft_version', 'backlog_revision'].every(key => integer(result[key])) ||
          (ideaId === null && (result.revision !== 0 || result.draft_version !== 0))) throw failure('invalid_response', true);
      const state = await this.readAuthority(ideaId, identity.session_id);
      if (this.disposed) { this.selectionUncertain = true; return false; }
      if (state?.idea_id !== ideaId || state.session_id !== result.session_id ||
          !['revision', 'draft_version', 'backlog_revision'].every(key => state[key] === result[key])) throw failure('invalid_response', true);
      this.validateState(state);
      if (preserve) {
        this.selectionUncertain = false; this.refreshUnauthorized = false; this.selectionPollOptions = null;
        this.load(state, true);
        this.error = this.pending ? previousError ?? failure('durability_uncertain', true) : null;
        this.message = this.pending ? 'Selection restored. Your answers remain; check the original save result.' :
          'Selection restored. Your answers remain; review them before saving.';
        return true;
      }
      // Validation above precedes any buffer or proposal association reset.
      this.buffers = defaultBuffers(); this.dirty.clear(); this.pending = null;
      this.proposalPending = null; this.proposalError = null; this.selectedProposals = {};
      this.selectionScope = null; this.selectionBinding = undefined; this.selectionIdea = undefined;
      this.selectionStorageAvailable = false; this.selectionUncertain = false; this.refreshUnauthorized = false;
      this.selectionPollOptions = null;
      this.ideas = null; this.ideasError = null; this.view = 'workflow';
      this.load(state);
      this.message = ideaId === null ? 'Capture a new idea.' : 'Saved idea opened.';
      return true;
    } catch (error) {
      this.selectionUncertain ||= selected || error.uncertain === true;
      if (!this.disposed) {
        this.error = error;
        this.message = this.selectionUncertain ? 'Selection result uncertain. Your answers remain; explicitly open the intended idea again.' : 'Idea was not opened. Your answers remain.';
      }
      return false;
    } finally {
      this.busy = false;
      if (!this.disposed && polling && !this.selectionUncertain && !this.refreshUnauthorized) this.startAgentRefresh(options);
      if (!this.disposed) this.onChange();
    }
  }

  receiptGuarded(pending) {
    return ['visual-set/accept', 'visual-disposition'].includes(pending.operation) ||
      (pending.operation === 'accept' && pending.key === 'assess');
  }

  validateReceipt(pending, result) {
    if (this.receiptGuarded(pending)) {
      const setWrite = pending.operation === 'visual-set/accept';
      const dispositionWrite = pending.operation === 'visual-disposition';
      if (result?.ok !== true || result.code !== 'ok' || result.request_id !== pending.payload.request_id ||
          result.idea_id !== pending.payload.idea_id || !['applied', 'no_op'].includes(result.write_state) ||
          (setWrite && (typeof result.design_set_id !== 'string' || !SET.test(result.design_set_id) ||
            (pending.payload.design_set_id !== null && result.design_set_id !== pending.payload.design_set_id)
          )) || (dispositionWrite && result.design_set_id != null)) {
        throw Object.assign(new Error('Invalid acceptance receipt'), {code: 'invalid_response', uncertain: true});
      }
    }
  }

  validatePendingState(pending, state) {
    if (this.receiptGuarded(pending) && state?.idea_id !== pending.payload.idea_id) {
      throw Object.assign(new Error('Invalid acceptance state identity'), {code: 'invalid_response', uncertain: true});
    }
  }

  finishPending(pending, state, recovered = false, result = null) {
    if (pending.operation === 'handoff') return this.finishHandoff(pending, state, result, recovered);
    if (this.receiptGuarded(pending)) {
      this.validateReceipt(pending, result);
      this.validatePendingState(pending, state);
      const submitted = pending.payload.fields, key = pending.key;
      const setWrite = pending.operation === 'visual-set/accept';
      const unchanged = same(this.buffers[key], submitted);
      // A valid historical receipt proves a write, not current acceptance.
      // Preserve submitted/newer answers even if they were previously clean.
      this.dirty.add(key);
      this.load(state, true);
      const canonicalFields = {...clone(submitted), ...(setWrite ? {design_set_id: result.design_set_id} : {})};
      const expectedStatus = pending.operation === 'visual-disposition' ? submitted.disposition : 'saved';
      if (!same(state.accepted?.[key], canonicalFields) || state.steps[key].status !== expectedStatus) {
        this.pending = null;
        this.error = Object.assign(new Error('saved_state_changed'), {code: 'saved_state_changed', status: 409});
        this.message = 'This request was saved, but the current saved state changed. Your answers remain; reload and review before saving again.';
        return false;
      }
      if (unchanged) {
        this.buffers[key] = clone(canonicalFields);
        this.dirty.delete(key);
      }
    } else this.load(state, true);
    if (pending.operation === 'navigate') {
      this.current = pending.key;
      this.pending = null;
      this.error = null;
      this.paused = this.dirty.size === 0;
      this.message = this.paused ? 'Paused. Your selected step is saved.' : 'Unsaved changes remain. Pause was not completed.';
      return;
    }
    const submitted = pending.operation === 'capture'
      ? {raw_text: pending.payload.raw_text, workspace: pending.payload.workspace}
      : pending.payload.fields;
    if (same(this.buffers[pending.key], submitted)) this.dirty.delete(pending.key);
    // The fills this write carried are now durable: only now may a reload treat them as applied.
    if (pending.fillCursor && same(state?.drafts?.[pending.key] ?? null, pending.payload.fields))
      this.rememberFill(pending.fillCursor.requestId, pending.fillCursor.sequence, true);
    this.pending = null;
    if (pending.operation === 'accept') {
      delete this.selectedProposals[pending.key];
      this.persistSelections();
    }
    this.error = null;
    this.message = this.dirty.size ? 'Unsaved changes remain in your answers.' :
      pending.operation === 'draft' ? 'Draft saved. Acceptance is still needed.' :
      recovered ? 'Saved result recovered' : 'Saved';
    this.message += this.selectionWarning();
    if (pending.operation !== 'draft' && !this.dirty.has(pending.key)) {
      const next = KEYS.slice(KEYS.indexOf(pending.key) + 1).find(key => !COMPLETE.has(this.status(key)));
      if (next) this.current = next;
    }
  }

  async runPending() {
    const pending = this.pending;
    if (!pending || this.busy || this.selectionUncertain || this.disposed) return false;
    this.busy = true;
    // A draft save (autosave, terminal fills) never locks typing: edits made meanwhile stay dirty and
    // survive the reload that follows (load preserves dirty buffers; only an identical buffer is cleaned).
    this.draftFlight = pending.operation === 'draft';
    this.mutationEpoch++;
    this.error = null;
    this.message = 'Saving…';
    this.onChange();
    const targetIdea = this.receiptGuarded(pending) || pending.operation === 'handoff' ? pending.payload.idea_id : this.state?.idea_id;
    const handoff = pending.operation === 'handoff';
    const identity = this.authorityIdentity(targetIdea);
    const resultIdentity = result => {
      if (pending.operation !== 'capture') return identity;
      if (result === null) return identity;
      if (result?.request_id !== pending.payload.request_id || typeof result.idea_id !== 'string' || !IDEA.test(result.idea_id)) throw failure('invalid_response', true);
      return {...identity, idea_id: result.idea_id};
    };
    const reconcile = async () => {
      let resolved;
      try {
        resolved = await (handoff ? this.api.reconcileHandoff(pending.payload.request_id, pending.payload.idea_id, pending.payload)
          : this.api.reconcile(pending.payload.request_id, targetIdea));
      } catch (error) {
        // The typed helper rejects unverified receipt/state identity internally.
        // Keep the original request and stop scope writes until explicit restore.
        if (handoff && error.code === 'invalid_response' && !this.disposed) this.freezeSelection(error);
        throw error;
      }
      if (this.disposed || this.selectionUncertain) throw failure('state_read_invalidated');
      try { this.assertAuthority(resolved.state, resultIdentity(resolved.result)); }
      catch (error) { if (error.selectionMismatch) this.freezeSelection(error); throw error; }
      return resolved;
    };
    try {
      let result, state;
      if (pending.ambiguous) {
        const resolved = await reconcile();
        result = resolved.result;
        state = resolved.state;
        if (result && (!result.ok || result.write_state === 'committed_uncertain')) {
          throw Object.assign(new Error('durability_uncertain'), {code: 'durability_uncertain', uncertain: true, data: result});
        }
      }
      if (!result) {
        if (handoff && !this.handoffLocal(pending)) {
          if (state) this.load(state, true);
          this.error = failure('durability_uncertain', true);
          this.message = 'No saved packet receipt is visible, so the planning prompt may never have been created. Your newer answers remain. To continue, undo the newer edits so Check save result can resend the original request, or copy your answers and reload the page.';
          return false;
        }
        result = handoff ? await this.api.handoff(pending.payload) : await this.api.write(pending.operation, pending.payload);
        state = null; // The reconciliation read preceded this new write.
      }
      pending.ambiguous = true; // A read failure now must reconcile the write.
      if (!result?.ok || !['applied', 'no_op'].includes(result.write_state)) {
        throw Object.assign(new Error('Invalid mutation result'), {code: 'invalid_response', uncertain: true});
      }
      this.validateReceipt(pending, result);
      // Re-read authority even after an ordinary success; a receipt is not UI evidence.
      if (!state) {
        const expected = resultIdentity(result);
        state = await this.readAuthority(expected.idea_id, expected.session_id);
      }
      return this.finishPending(pending, state, false, result) !== false;
    } catch (error) {
      if (handoff && this.disposed) return false;
      if (this.selectionUncertain) return false;
      this.error = error;
      pending.ambiguous ||= error.uncertain === true;
      if (pending.ambiguous) {
        try {
          const resolved = await reconcile();
          if (resolved.result?.ok && ['applied', 'no_op'].includes(resolved.result.write_state)) {
            return this.finishPending(pending, resolved.state, true, resolved.result) !== false;
          }
          this.validatePendingState(pending, resolved.state);
          if (!handoff || !this.disposed) this.load(resolved.state, true);
        } catch { /* Preserve the buffer and uncertainty; never send another write here. */ }
      }
      if (this.selectionUncertain) return false;
      this.message = pending.ambiguous ? 'Save result uncertain. Check the saved result before retrying.' : 'Not saved. Your answers are still here.';
      return false;
    } finally {
      this.draftFlight = false;
      this.busy = false;
      if (!handoff || !this.disposed) this.onChange();
    }
  }

  async retry() { return this.runPending(); }

  async reloadKeepingAnswers() {
    if (this.busy || this.pending?.ambiguous || this.selectionUncertain) return false;
    try {
      const state = await this.readAuthority();
      this.load(state, true);
      this.pending = null;
      this.error = null;
      this.message = 'Current state loaded. Your answers remain; review them before saving again.';
      this.onChange();
      return true;
    } catch (error) {
      if (!this.disposed && !this.selectionUncertain && error.code !== 'state_read_invalidated') { this.error = error; this.onChange(); }
      return false;
    }
  }

  proposalSource(key) { return this.state?.proposal_sources?.[proposalOperation(key)]?.source ?? null; }

  proposals(key) { return (this.state?.proposals ?? []).filter(item => item.operation === proposalOperation(key)); }

  cancelProposal(reason) {
    if (!this.proposalPending) return;
    this.proposalPending = null;
    this.proposalError = {code: reason};
    this.message = 'AI assistance stopped. Your answers remain; request a new suggestion when ready.';
  }

  reconcileProposal() {
    // Preserve the provenance association when inputs drift. It can only be
    // consumed by successful acceptance or explicitly cleared by the human.
    const pending = this.proposalPending;
    if (!pending) return;
    if (this.state.agent_generation !== pending.generation) return this.cancelProposal('wrong_generation');
    if (this.state.agent_status !== 'connected') return this.cancelProposal('agent_unavailable');
    const completed = this.proposals(pending.key).find(item => item.request_id === pending.payload.request_id);
    if (completed) {
      this.proposalPending = null;
      this.proposalError = completed.acceptance_eligible ? null : {code: completed.acceptance_reason};
      this.message = completed.content_omitted ? 'Suggestion saved in Markdown. Its content is omitted from this bounded browser view.' :
        completed.acceptance_eligible ? 'Suggestion ready. Review it before using or accepting it.' : 'Suggestion inputs changed. Your answers remain.';
      return;
    }
    // The agent opened the conversation for this very request: the wait is over. Fills are applied as they arrive, and
    // the human's own edits (which move the draft) no longer make the request look stale.
    if (this.state.conversation?.request_id === pending.payload.request_id) { this.proposalPending = null; return; }
    if (this.clock() >= pending.deadline) return this.cancelProposal('proposal_timeout');
    // The human's own draft saves move the draft, not the conversation: only a new accepted revision ends it.
    const source = this.proposalSource(pending.key);
    // For Assess the proposed position depends on the backlog, so a backlog change still ends the wait.
    if (pending.payload.idea_id !== this.state.idea_id) return this.cancelProposal('stale_source');
    if (!source || source.accepted_revision !== pending.source.accepted_revision ||
        (pending.key === 'assess' && !same(source.data?.backlog, pending.source.data?.backlog))) this.cancelProposal('stale_source');
  }

  canPropose(key) {
    return ['shape', 'method', 'assess'].includes(key) && !this.disposed && !this.paused && !this.selectionUncertain && !this.busy && !this.pending && !this.proposalPending &&
      this.state?.agent_status === 'connected' && GENERATION.test(this.state?.agent_generation ?? '') && this.state.proposal_sources?.[proposalOperation(key)]?.available === true;
  }

  async requestProposal(key) {
    if (this.refreshPromise && !await this.refreshPromise) {
      if (!this.disposed) {
        this.message = 'Suggestion was not requested. Your answers remain; wait for saving to finish and request again when connected.';
        this.onChange();
      }
      return false;
    }
    if (!this.canPropose(key)) return false;
    // Every consumed client buffer must first become a durable draft. The source
    // subsequently reread from authority includes the confirmed counters/hash.
    for (const dirty of [...this.dirty]) if (!await this.save(dirty, true)) return false;
    if (!await this.refreshAgent() || !this.canPropose(key)) return false;
    const source = clone(this.proposalSource(key));
    this.proposalPending = {key, generation: this.state.agent_generation, source,
      deadline: this.clock() + 125000, phase: 'sending', ambiguous: false,
      payload: {request_id: this.idFactory(), idea_id: this.state.idea_id,
        expected_revision: source.accepted_revision, expected_draft_version: source.draft_version,
        operation: proposalOperation(key), source_digest: source.source_digest}};
    this.rememberFill(this.proposalPending.payload.request_id, 0);  // our own request: apply its fills from the first
    return this.sendProposal();
  }

  async sendProposal() {
    const pending = this.proposalPending;
    if (!pending || this.busy || this.disposed || this.selectionUncertain) return false;
    this.reconcileProposal();
    if (this.proposalPending !== pending) return false;
    this.busy = true; this.proposalFlight = true; this.mutationEpoch++; this.proposalError = null; this.onChange();
    try {
      const result = await this.api.write('propose', pending.payload);
      if (result.ok !== true || result.code !== 'ok' || result.request_id !== pending.payload.request_id || result.operation !== pending.payload.operation ||
          result.session_id !== this.state.session_id || result.idea_id !== pending.payload.idea_id ||
          result.accepted_revision !== pending.source.accepted_revision || result.draft_version !== pending.source.draft_version ||
          result.source_digest !== pending.source.source_digest || !['pending', 'completed'].includes(result.status) ||
          result.write_state !== (result.status === 'pending' ? 'not_applied' : 'applied')) throw Object.assign(new Error('Invalid proposal response'), {code: 'invalid_response', uncertain: true});
      pending.phase = 'waiting'; pending.ambiguous = false;
      this.message = 'Waiting for the initiating agent. Your answers remain editable.';
      return true;
    } catch (error) {
      pending.phase = 'failed'; pending.ambiguous = error.uncertain === true;
      this.proposalError = error;
      if (error.status === 401) this.refreshUnauthorized = true;
      if (error.status === 409 && ['stale_source', 'stale_backlog', 'wrong_generation', 'agent_unavailable'].includes(error.code)) this.cancelProposal(error.code);
      else this.message = pending.ambiguous ? 'Suggestion request result uncertain. Refresh or explicitly retry the same request.' : 'Suggestion request failed. Your answers remain.';
      return false;
    } finally { this.busy = false; this.proposalFlight = false; this.onChange(); }
  }

  async retryProposal() {
    if (!this.proposalPending || this.proposalPending.phase !== 'failed') return false;
    const pending = this.proposalPending;
    if (!await this.refreshAgent() || this.proposalPending !== pending) return false;
    // Explicit retry only: Broker replay owns the identical request envelope.
    return this.sendProposal();
  }

  useProposal(key, id) {
    if (this.busy || this.pending || this.paused || this.selectionUncertain) return false;
    const item = this.proposals(key).find(value => value.proposal_id === id && value.acceptance_eligible && !value.content_omitted && value.proposal !== null);
    if (!item || this.state?.agent_status !== 'connected') return false;
    let fields = clone(item.proposal);
    if (key === 'method') {
      const current = this.buffers.method;
      // A recommendation never preselects the human's methodology or budget.
      fields = {...clone(current), selection: current.selection ?? null,
        reason: fields.reason, memory: fields.memory,
        investment: current.selection === 'appetite-led' ? clone(current.investment ?? null) : null,
        experiment: current.selection === 'experiment-led' ? clone(current.experiment ?? null) : null};
    } else if (!['shape', 'assess'].includes(key)) return false;
    this.selectedProposals[key] = id;
    this.persistSelections();
    this.edit(key, fields);
    this.message = 'Suggestion copied to your draft. Review and accept your answers explicitly.' + this.selectionWarning();
    this.onChange(); return true;
  }

  clearProposal(key) {
    if (this.busy || this.pending || !['shape', 'method', 'assess'].includes(key)) return false;
    delete this.selectedProposals[key];
    this.persistSelections();
    this.proposalError = null;
    this.message = 'You chose to save these fields as your own answers. Review them before acceptance.';
    this.onChange(); return true;
  }

  async refreshAgent() {
    if (this.disposed || this.refreshUnauthorized || this.selectionUncertain) return false;
    if (this.refreshPromise) return this.refreshPromise;
    if (this.busy || this.pending?.ambiguous) return false;
    this.refreshing = true;
    const epoch = this.mutationEpoch;
    const flight = (async () => {
      try {
        const state = await this.readAuthority();
        if (this.disposed) return false;
        // A response begun before a mutation must not race its state/receipt read.
        if (epoch !== this.mutationEpoch || this.busy || this.pending?.ambiguous) return false;
        if (same(state, this.state)) {
          const pending = this.proposalPending;
          this.reconcileProposal();
          if (pending !== this.proposalPending) this.onChange();
        } else this.load(state, true);
        return true;
      } catch (error) {
        if (this.disposed || epoch !== this.mutationEpoch || this.busy || this.pending?.ambiguous) return false;
        this.proposalError = error;
        this.reconcileProposal();
        if (error.status === 401) { this.refreshUnauthorized = true; this.error = error; this.cancelProposal('browser_unauthorized'); }
        this.onChange(); return false;
      }
    })().finally(() => {
      if (this.refreshPromise === flight) { this.refreshing = false; this.refreshPromise = null; }
    });
    this.refreshPromise = flight;
    return this.refreshPromise;
  }

  startAgentRefresh({interval = 2000, active = () => true, setTimer = setTimeout, clearTimer = clearTimeout} = {}) {
    this.stopAgentRefresh();
    this.polling = true;
    this.pollOptions = {interval, active, setTimer, clearTimer};
    this.clearPollTimer = clearTimer;
    const epoch = this.pollEpoch;
    const tick = async () => {
      if (epoch !== this.pollEpoch || this.disposed || this.refreshUnauthorized) return;
      if (active()) await this.refreshAgent();
      if (epoch === this.pollEpoch && !this.disposed && !this.refreshUnauthorized) {
        this.pollTimer = setTimer(tick, interval);
        this.pollTimer?.unref?.();
      }
    };
    this.pollTimer = setTimer(tick, interval);
    this.pollTimer?.unref?.();
  }

  stopAgentRefresh() {
    this.polling = false;
    this.pollEpoch++;
    if (this.pollTimer !== null) (this.clearPollTimer ?? clearTimeout)(this.pollTimer);
    this.pollTimer = null;
  }

  dispose() { this.disposed = true; this.stopAgentRefresh(); }

  async pause() {
    if (this.busy || this.pending || this.selectionUncertain) return false;
    if (!this.state?.idea_id && !validCapture(this.buffers.capture)) {
      this.paused = true;
      this.message = 'Paused locally. This incomplete idea is not saved; keep this tab open.';
      this.onChange();
      return true;
    }
    const selected = this.current;
    try {
      for (const key of [...this.dirty]) {
        if (!await this.save(key, true)) return false;
        this.current = selected;
      }
      this.pending = {operation: 'navigate', key: selected, ambiguous: false,
        payload: {request_id: this.idFactory(), idea_id: this.state.idea_id,
          expected_revision: this.state.revision, expected_draft_version: this.state.draft_version, step: selected}};
      return await this.runPending();
    } finally {
      this.current = selected;
      this.onChange();
    }
  }
}
