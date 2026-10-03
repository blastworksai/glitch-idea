// Explicit Assess editor; Flow/server own provenance and placement CAS.
import {validAssess, assessmentFields, assessmentScore, insertionNeighbors, conversationStatus, filledNote} from '../folds.js';

const METHODS = [['wsjf', 'WSJF'], ['rice', 'RICE'], ['kano', 'Kano']];
const INPUTS = {wsjf: [['value', 'Value'], ['time_criticality', 'Time criticality'], ['enablement', 'Enablement'], ['effort', 'Effort']],
  rice: [['reach', 'Reach'], ['impact', 'Impact'], ['confidence', 'RICE confidence (0–1)'], ['effort', 'Effort']]};
const CATEGORIES = ['must-be', 'performance', 'delighter', 'indifferent', 'reverse', 'questionable'];
const REASONS = {stale_source: 'Saved inputs or order changed. Reload this page to ask your terminal again.',
  stale_backlog: 'The backlog order changed. Review the current order and reload this page to ask your terminal again.',
  wrong_generation: 'This assessment belongs to an earlier agent session.', wrong_session: 'This assessment belongs to another session.',
  agent_unavailable: 'No agent is connected. You can fill this step by hand, or reinvoke /glitch-idea in your terminal to be guided.',
  proposal_timeout: 'The assessment wait ended. Reload this page to ask your terminal again.',
  projection_omitted: 'This assessment is saved in Markdown; its body is omitted from this view.',
  proposal_mismatch: 'Keep the original proposed position or explicitly unlink the suggestion.'};
// Plain-words explanations. Source: references/methods.md, "Human ratings and brain assessments".
const BLANK = " Leave blank if you don't know; the score stays Unknown.";
const METHOD_INFO = {
  wsjf: ['WSJF = (value + time criticality + enablement) / effort. The first three are relative delay-cost inputs and effort is a relative size proxy, all on one shared scale and cohort.',
    'value, time criticality, enablement and effort as relative numbers on one shared scale.'],
  rice: ['RICE = reach * impact * confidence / effort. Name the reach period, the impact scale and the effort unit; confidence is a fraction from 0 to 1.',
    'reach for a period you name, impact on a scale you name, a confidence fraction 0–1, and effort in a unit you name.'],
  kano: ['Kano gives a category plus a hypothesis flag. It has no numeric score. An AI classification is a hypothesis, not customer research.',
    'a category and whether that category is only a hypothesis; no numbers.']};
const INPUT_HELP = {
  wsjf: {value: 'How much value this delivers, as a relative number on one scale shared by every idea you compare (for example 1–10).',
    time_criticality: 'How much waiting costs, as a relative number on the same shared scale (for example 1–10).',
    enablement: 'How much this makes possible for other work, as a relative number on the same shared scale (for example 1–10).',
    effort: 'A relative size proxy for the work on the same shared scale and cohort (for example 1–10). Must be above 0.'},
  rice: {reach: 'How many people or events this reaches in a period you name in the basis (for example 500 users per quarter).',
    impact: 'How much it moves each of them, on a scale you name in the basis.',
    confidence: 'A fraction from 0–1 (0.8 means 80%). This is the RICE input, not the whole-assessment confidence below.',
    effort: 'The work needed, in a unit you name in the basis (person-days, points…). Must be above 0.'}};
const FIELD_HELP = {
  method: 'Pick the method whose inputs you can actually estimate. WSJF is not required.',
  version: 'A short label for this estimate, for example "v1", so a later re-estimate can be told apart.',
  basis: 'Where the numbers came from (data, an estimate, a conversation) and the units: reach period, impact scale, effort unit.',
  provenance: 'Who or what produced this assessment: you, the terminal agent, a document.',
  confidence: 'How sure you are about the whole assessment: low, medium or high. This is not the RICE confidence fraction.',
  assumptions: 'Anything you took for granted to fill the inputs, one per line. Add one for each proxy or guess.',
  category: 'Pick the Kano category that classifies this idea. A category is a label, not a score.',
  hypothesis: 'Choose Hypothesis when the category is a guess from you or an AI rather than customer research.',
  proposed: 'The place in the backlog order the terminal suggests. It is a suggestion only.',
  actual: 'The place in the backlog order you choose; 1 is the top. You decide, and scores do not reorder the list.'};
function missingScore(assessment) {
  if (assessment.method === 'kano') return assessment.inputs.category == null ? ['Category'] : [];
  return (INPUTS[assessment.method] ?? []).filter(([key]) => assessment.inputs[key] == null).map(([, label]) => label.replace(/ \(0–1\)$/, ''));
}
function scoreLine(assessment) {
  if (!assessment.method) return 'Score: choose a method first.';
  if (assessment.method === 'kano') return missingScore(assessment).length ? 'Kano: no category chosen yet. Kano has no numeric score.' : 'Kano has no numeric score; the category is the result.';
  const missing = missingScore(assessment);
  if (missing.length) return 'Score: Unknown — missing: ' + missing.join(', ') + '. Fill them in, or leave them blank if you do not know.';
  const i = assessment.inputs;  // display only: the saved score is still calculated by the server
  const shown = assessment.method === 'wsjf' ? '(' + i.value + ' + ' + i.time_criticality + ' + ' + i.enablement + ') / ' + i.effort :
    i.reach + ' × ' + i.impact + ' × ' + i.confidence + ' / ' + i.effort;
  const score = assessment.method === 'wsjf' ? (i.value + i.time_criticality + i.enablement) / i.effort : i.reach * i.impact * i.confidence / i.effort;
  return !Number.isFinite(score) ? 'Score: Unknown — check the values (effort must be above 0).' : 'Score: ' + score + ' = ' + shown;
}
// Per-Flow raw numeric edits preserve decimal typing without persisting invalid
// text into the typed draft. Canonical changes/idea/method changes reset the text.
const rawEdits = new WeakMap();
function edits(flow, method) {
  const scope = (flow.state?.idea_id ?? '') + ':' + (method ?? '');
  let entry = rawEdits.get(flow);
  if (!entry || entry.scope !== scope) { entry = {scope, values: new Map()}; rawEdits.set(flow, entry); }
  return entry.values;
}
function editNumber(flow, method, key, canonical) {
  canonical ??= null;
  const values = edits(flow, method); let entry = values.get(key);
  if (!entry || entry.canonical !== canonical) {
    entry = {canonical, raw: canonical === null ? '' : String(canonical), valid: true}; values.set(key, entry);
  }
  return entry;
}
function decimal(raw, key, maximum = 1e12, whole = false) {
  if (raw === '') return {value: null, valid: true};
  if (!(whole ? /^[0-9]+$/ : /^(?:[0-9]+(?:[.,][0-9]+)?|[.,][0-9]+)$/).test(raw)) return {value: null, valid: false};
  const value = Number(raw.replace(',', '.'));
  return {value: Number.isFinite(value) && value >= 0 && value <= maximum &&
    (key !== 'effort' || value > 0) && (!whole || (Number.isSafeInteger(value) && value > 0)) ? value : null,
    valid: Number.isFinite(value) && value >= 0 && value <= maximum &&
      (key !== 'effort' || value > 0) && (!whole || (Number.isSafeInteger(value) && value > 0))};
}
const literal = value => typeof value === 'string' ? value : JSON.stringify(value);
function summary(value) {
  if (!value || !assessmentFields(value)) return 'Assessment incomplete';
  if (value.method === 'kano') return 'Kano: ' + value.inputs.category + ' · ' + (value.inputs.hypothesis ? 'hypothesis' : 'not a hypothesis') + ' · no numeric score';
  const score = assessmentScore(value);
  return value.method.toUpperCase() + ': ' + (score === null ? 'Unknown' : String(score));
}

export function render({body, foot, flow, element, button, field, connected, edited, handle, proposalInventory}) {
  const active = flow.state?.idea_status === 'active';
  if (!active) {
    const notice = element('p', flow.state?.idea_status === 'archived' ?
      'This idea is archived. Assess acceptance is unavailable. To reactivate it, explicitly redo and accept Shape. Archived history is kept; your assessment draft remains here.' :
      'The idea status is unavailable. Reload current state before accepting an assessment. Your draft remains here.', 'notice');
    notice.id = 'assess-idea-status'; notice.setAttribute('role', 'status'); body.append(notice);
  }
  const talk = element('p', conversationStatus(flow, 'assess'), 'notice'); talk.id = 'assess-conversation-status'; talk.setAttribute('role', 'status'); body.append(talk);
  const current = () => {
    const saved = flow.buffers.assess ?? {}, assessment = saved.assessment ?? {}, method = assessment.method ?? null;
    const inputKeys = INPUTS[method]?.map(([key]) => key) ?? (method === 'kano' ? ['category', 'hypothesis'] : []);
    return {...saved, assessment: {method: null, version: '', basis: '', assumptions: [], confidence: null, provenance: '', ...assessment,
      assumptions: assessment.assumptions ?? [], inputs: {...Object.fromEntries(inputKeys.map(key => [key, null])), ...(assessment.inputs ?? {})}},
      position: {proposed_position: null, actual_position: null, neighbors: {before: null, after: null}, override_reason: null, ...(saved.position ?? {})}};
  };
  const blocked = () => !connected || flow.inputLocked() || (Boolean(flow.pending) && !flow.draftFlight) || flow.paused;
  const changeAssessment = (key, value) => edited('assess', {...current(), assessment: {...current().assessment, [key]: value}});
  const changePosition = (key, value) => {
    const position = {...current().position, [key]: value};
    if (key === 'actual_position') {
      position.neighbors = insertionNeighbors(flow.state?.backlog?.order, flow.state?.idea_id, value) ?? {before: null, after: null};
    }
    edited('assess', {...current(), position});
  };
  const textField = (target, label, id, value, change, textarea = true) => {
    const input = field(target, label, id, value, change, textarea); input.disabled = blocked(); return input;
  };
  // A help line sits directly under its label and is linked to the control with aria-describedby.
  const help = (id, text) => { const node = element('p', text, 'help'); node.id = id; return node; };
  const describe = (control, ...ids) => control.setAttribute('aria-describedby', ids.join(' '));
  const helped = (target, input, id, text) => {  // target's last child is the field wrapper holding the label and input
    const wrap = target.lastElementChild ?? target.children.at(-1), label = wrap.children[0], line = help(id, text);
    if (label.after) label.after(line); else wrap.children.splice(1, 0, line);
    describe(input, id); return input;
  };
  const groupHelp = (group, id, text) => { group.append(help(id, text)); describe(group, id); };
  const human = element('section', '', 'notice'); human.id = 'assess-human-ratings'; human.setAttribute('aria-label', 'Human priorities');
  human.append(element('h2', 'Your priorities'));
  const ratings = flow.state?.human_ratings;
  human.append(element('p', 'Urgency: ' + (ratings ? ratings.urgency + ' of 10' : 'Unknown')),
    element('p', 'Importance: ' + (ratings ? ratings.importance + ' of 10' : 'Unknown')));
  if (ratings) human.append(element('p', 'Recorded by: ' + ratings.actor + (ratings.timestamp ? ' · ' + ratings.timestamp : '')));
  human.append(element('p', 'Assessment inputs do not change your urgency or importance.', 'help')); body.append(human);

  const methodGroup = element('fieldset'); methodGroup.append(element('legend', 'Assessment method'));
  filledNote({flow, key: 'assess', name: 'assessment', id: 'assess-assessment', element, target: methodGroup});
  groupHelp(methodGroup, 'assess-method-intro', FIELD_HELP.method);
  for (const [value, title] of METHODS) {
    const choice = button(title, () => {
      if (current().assessment.method === value) return;
      rawEdits.delete(flow);
      edited('assess', {...current(), assessment: {...current().assessment, method: value,
        inputs: Object.fromEntries((INPUTS[value]?.map(([key]) => key) ?? ['category', 'hypothesis']).map(key => [key, null]))}});
    });
    choice.id = 'assess-method-' + value; choice.setAttribute('aria-pressed', String(current().assessment.method === value));
    choice.disabled = blocked(); methodGroup.append(choice);
  }
  const chosen = current().assessment.method, about = element('div'); about.id = 'assess-method-help'; about.setAttribute('role', 'note');
  if (chosen) about.append(element('p', METHOD_INFO[chosen][0], 'help'));
  const when = element('ul', '', 'help'); when.id = 'assess-method-when';
  // methods.md gives no rule for when to use a method, only "choose an assessment with usable inputs":
  // so say what each one needs, and let the human pick the one whose inputs they can estimate.
  for (const [value, title] of METHODS) when.append(element('li', title + ' needs: ' + METHOD_INFO[value][1]));
  about.append(when); methodGroup.append(about); methodGroup.setAttribute('aria-describedby', 'assess-method-intro assess-method-help');
  body.append(methodGroup);
  helped(body, textField(body, 'Assessment version', 'assess-version', current().assessment.version, value => changeAssessment('version', value), false), 'assess-version-help', FIELD_HELP.version).required = true;
  for (const [key, title] of [['basis', 'Evidence and rationale'], ['provenance', 'Recorded provenance']]) {
    const value = current().assessment[key];
    if (value && typeof value === 'object') {
      const group = element('fieldset'); group.append(element('legend', title)); groupHelp(group, 'assess-' + key + '-help', FIELD_HELP[key]);
      Object.entries(value).forEach(([name, item], index) => textField(group, name, 'assess-' + key + '-' + index, item,
        next => changeAssessment(key, {...current().assessment[key], [name]: next})).required = true);
      body.append(group);
    } else helped(body, textField(body, title, 'assess-' + key, value, next => changeAssessment(key, next)), 'assess-' + key + '-help', FIELD_HELP[key]).required = true;
  }
  const confidence = element('fieldset'); confidence.append(element('legend', 'Assessment confidence')); groupHelp(confidence, 'assess-confidence-help', FIELD_HELP.confidence);
  for (const value of ['low', 'medium', 'high']) {
    const choice = button(value, () => changeAssessment('confidence', value)); choice.id = 'assess-confidence-' + value;
    choice.setAttribute('aria-pressed', String(current().assessment.confidence === value)); choice.disabled = blocked(); confidence.append(choice);
  }
  body.append(confidence);
  const numericField = (target, label, id, canonical, key, change, maximum, whole = false, helpText = null) => {
    const method = current().assessment.method, raw = editNumber(flow, method, id, canonical);
    const input = textField(target, label, id, raw.raw, value => {
      const active = editNumber(flow, current().assessment.method, id, canonical), parsed = decimal(value, key, maximum, whole);
      active.raw = value; active.valid = parsed.valid; active.canonical = parsed.value; change(parsed.value);
    }, false);
    input.type = 'text'; input.inputMode = whole ? 'numeric' : 'decimal'; input.setAttribute('aria-invalid', String(!raw.valid));
    if (helpText) helped(target, input, id + '-help', helpText);
    if (!raw.valid) { const error = element('p', 'Enter a valid ' + (whole ? 'position' : 'nonnegative decimal') + ' or leave blank for Unknown.', 'notice');
      error.id = id + '-error'; error.setAttribute('role', 'status'); describe(input, ...(helpText ? [id + '-help'] : []), error.id); target.append(error); }
    return input;
  };
  const inputs = element('fieldset'); inputs.append(element('legend', 'Assessment inputs'));
  if (current().assessment.method === 'kano') groupHelp(inputs, 'assess-category-help', FIELD_HELP.category);
  else if (!current().assessment.method) inputs.append(element('p', 'Choose a method above to see its inputs.', 'help'));
  const method = current().assessment.method;
  for (const [key, label] of INPUTS[method] ?? []) {
    numericField(inputs, label, 'assess-input-' + key, current().assessment.inputs[key], key,
      value => changeAssessment('inputs', {...current().assessment.inputs, [key]: value}), key === 'confidence' ? 1 : 1e12, false,
      INPUT_HELP[method][key] + BLANK);
    const readout = element('p', current().assessment.inputs[key] === null ? 'Unknown' : String(current().assessment.inputs[key]), 'help');
    readout.id = 'assess-input-' + key + '-status'; inputs.append(readout);
  }
  if (method === 'kano') {
    for (const category of CATEGORIES) { const choice = button(category, () => changeAssessment('inputs', {...current().assessment.inputs, category}));
      choice.id = 'assess-kano-' + category; choice.setAttribute('aria-pressed', String(current().assessment.inputs.category === category)); choice.disabled = blocked(); inputs.append(choice); }
    const hypothesis = element('fieldset'); hypothesis.append(element('legend', 'Category hypothesis')); groupHelp(hypothesis, 'assess-hypothesis-help', FIELD_HELP.hypothesis);
    for (const [value, title] of [[true, 'Hypothesis'], [false, 'Not a hypothesis']]) { const choice = button(title, () => changeAssessment('inputs', {...current().assessment.inputs, hypothesis: value}));
      choice.id = 'assess-kano-hypothesis-' + value; choice.setAttribute('aria-pressed', String(current().assessment.inputs.hypothesis === value)); choice.disabled = blocked(); hypothesis.append(choice); }
    inputs.append(hypothesis, element('p', 'Kano remains categorical. It has no numeric ranking score.', 'help'));
  } else inputs.append(element('p', 'Leave an unknown value blank. Use a dot or comma for decimals. Effort must be positive; RICE confidence is between 0 and 1.', 'help'));
  body.append(inputs);
  const assumptions = element('fieldset'); assumptions.append(element('legend', 'Assumptions')); groupHelp(assumptions, 'assess-assumptions-help', FIELD_HELP.assumptions);
  current().assessment.assumptions.forEach((value, index) => {
    textField(assumptions, 'Assumption ' + (index + 1), 'assess-assumption-' + index, value,
      next => changeAssessment('assumptions', current().assessment.assumptions.map((item, offset) => offset === index ? next : item)));
    const remove = button('Remove assumption ' + (index + 1), () => changeAssessment('assumptions', current().assessment.assumptions.filter((_, offset) => offset !== index)));
    remove.id = 'assess-remove-assumption-' + index; remove.disabled = blocked(); assumptions.append(remove);
  });
  const add = button('Add assumption', () => changeAssessment('assumptions', [...current().assessment.assumptions, '']));
  add.id = 'assess-add-assumption'; add.disabled = blocked() || current().assessment.assumptions.length >= 1000; assumptions.append(add); body.append(assumptions);
  const scoreStatus = element('p', scoreLine(current().assessment), 'notice'); scoreStatus.id = 'assess-score-status'; scoreStatus.setAttribute('role', 'status'); body.append(scoreStatus);
  const preview = element('p', 'Draft preview: ' + summary(current().assessment), 'notice'); preview.id = 'assess-preview'; preview.setAttribute('role', 'status'); body.append(preview);
  if (flow.state?.assessment_summary) { const {score: ignored, assessment_id, actor, timestamp, ...core} = flow.state.assessment_summary;
    const saved = element('section', '', 'notice'); saved.id = 'assess-saved-summary';
    saved.append(element('h2', 'Latest saved assessment'), element('p', summary(core)));
    if (actor) saved.append(element('p', 'Recorded by: ' + actor + (timestamp ? ' · ' + timestamp : ''))); body.append(saved); }

  const backlog = flow.state?.backlog, available = flow.state?.backlog_status?.available === true;
  const order = element('section'); order.id = 'assess-backlog'; order.setAttribute('aria-label', 'Actual backlog order');
  order.append(element('h2', 'Actual backlog order'));
  if (available) { const list = element('ol');
    backlog.comparisons.forEach(item => { const row = element('li'); row.id = 'assess-backlog-' + item.idea_id;
      row.append(element('span', item.idea_id + ' · ' + item.status), element('p', 'Human urgency/importance: ' + (item.ratings ? item.ratings.urgency + '/' + item.ratings.importance : 'Unknown')));
      if (item.assessment) { const {score: ignored, assessment_id, actor, timestamp, ...core} = item.assessment; row.append(element('p', summary(core))); }
      else row.append(element('p', 'Assessment: Unknown')); list.append(row); });
    order.append(list, element('p', 'Observed backlog revision: ' + backlog.revision + '. Scores do not reorder this list.', 'help'));
  } else order.append(element('p', 'The complete current backlog is unavailable. Placement cannot be accepted; your draft remains.', 'notice'));
  body.append(order);
  const placement = element('fieldset'); placement.append(element('legend', 'Your placement'));
  const selected = flow.selectedProposals.assess;
  const selectedItem = flow.proposals('assess').find(item => item.proposal_id === selected);
  if (selected) { const original = element('p', 'Original proposed position: ' + (selectedItem?.proposal?.position.proposed_position ?? 'Unavailable')); original.id = 'assess-proposed-position'; placement.append(original); }
  else if (flow.filled.assess?.has('proposed_position')) {
    // The agent's proposal is shown as read-only text; the human decides the actual position below.
    const proposed = element('p', 'AI proposed position: ' + (current().position.proposed_position ?? 'Unknown')); proposed.id = 'assess-proposed-position';
    placement.append(proposed); filledNote({flow, key: 'assess', name: 'proposed_position', id: 'assess-proposed-position', element, target: placement});
  } else numericField(placement, 'AI proposed position', 'assess-proposed-position-input', current().position.proposed_position, 'position',
    value => changePosition('proposed_position', value), available ? backlog.order.length : 1e12, true, FIELD_HELP.proposed).disabled = blocked() || !available;
  numericField(placement, 'Actual position (you decide)', 'assess-actual-position', current().position.actual_position, 'position',
    value => changePosition('actual_position', value), available ? backlog.order.length : 1e12, true, FIELD_HELP.actual).disabled = blocked() || !available;
  const position = current().position, override = position.actual_position !== position.proposed_position;
  textField(placement, 'Why override the proposed position?', 'assess-override-reason', position.override_reason,
    value => changePosition('override_reason', value)).required = override;
  placement.append(element('p', 'Before: ' + (position.neighbors?.before ?? 'Start') + ' · After: ' + (position.neighbors?.after ?? 'End'), 'help'));
  const expectedNeighbors = available ? insertionNeighbors(backlog.order, flow.state.idea_id, position.actual_position) : null;
  const neighborsCurrent = expectedNeighbors !== null && position.neighbors?.before === expectedNeighbors.before && position.neighbors?.after === expectedNeighbors.after;
  if (position.actual_position !== null && !neighborsCurrent) placement.append(element('p', 'Placement neighbors changed. Review the order and enter your actual position again.', 'notice'));
  body.append(placement);

  const assistance = element('section'); assistance.setAttribute('aria-label', 'Assessment suggestions');
  const pending = flow.proposalPending?.key === 'assess' ? flow.proposalPending : null;
  if (pending) { const status = element('p', pending.phase === 'sending' ? 'Sending your assessment request…' : pending.phase === 'waiting' ? 'Waiting for the initiating agent. Your draft remains editable.' :
    pending.ambiguous ? 'The request may have reached the agent. Explicitly retry the same request to check it.' : 'The assessment request failed. Your draft remains.');
    status.id = 'assess-proposal-status'; status.setAttribute('role', 'status'); assistance.append(status);
    if (pending.phase === 'failed') { const retry = button('Retry same request', handle(() => flow.retryProposal())); retry.id = 'assess-retry'; retry.disabled = blocked(); assistance.append(retry); }
    const stop = button('Stop waiting', handle(() => {flow.cancelProposal('user_cancelled'); flow.onChange();})); stop.id = 'assess-stop-waiting'; stop.disabled = blocked(); assistance.append(stop); }
  if (flow.proposalError) { const error = element('p', REASONS[flow.proposalError.code] ?? 'AI assessment could not complete this request. Your draft remains.', 'notice');
    error.id = 'assess-proposal-error'; error.setAttribute('role', 'status'); assistance.append(error); }
  for (const item of flow.proposals('assess')) { const card = element('section', '', 'notice'); card.id = 'assess-proposal-' + item.proposal_id;
    card.append(element('h2', 'Proposed assessment'));
    if (item.proposal) { const a = item.proposal.assessment; card.append(element('p', summary(a)), element('p', 'Version: ' + a.version),
      element('p', 'Evidence and rationale: ' + literal(a.basis)), element('p', 'Confidence: ' + a.confidence), element('p', 'Provenance: ' + literal(a.provenance)),
      element('p', 'Proposed position: ' + item.proposal.position.proposed_position));
      for (const [key, label] of INPUTS[a.method] ?? []) card.append(element('p', label + ': ' + (a.inputs[key] === null ? 'Unknown' : String(a.inputs[key]))));
      for (const assumption of a.assumptions) card.append(element('p', 'Assumption: ' + assumption)); }
    if (!item.acceptance_eligible) card.append(element('p', REASONS[item.acceptance_reason] ?? 'This suggestion is unavailable for acceptance.'));
    card.append(element('p', 'Saved suggestion: ' + item.evidence.path));
    const use = button('Use assessment suggestion', handle(() => {rawEdits.delete(flow); return flow.useProposal('assess', item.proposal_id);}));
    use.id = 'assess-use-proposal-' + item.proposal_id; use.disabled = blocked() || flow.state?.agent_status !== 'connected' || !item.acceptance_eligible || item.content_omitted; card.append(use); assistance.append(card); }
  body.append(assistance); proposalInventory(body, 'assess');
  if (selected) { body.append(element('p', 'Your edited assessment remains linked to the suggestion you chose. Acceptance is explicit.', 'notice'));
    const manual = button('Use my answers without linking a suggestion', handle(() => flow.clearProposal('assess'))); manual.id = 'assess-manual'; manual.disabled = blocked(); body.append(manual); }
  const eligible = !selected || (selectedItem?.acceptance_eligible && !selectedItem.content_omitted &&
    position.proposed_position === selectedItem.proposal.position.proposed_position);
  const validRaw = [...edits(flow, method).values()].every(value => value.valid);
  const accept = button(flow.busy ? 'Saving…' : 'Accept assessment and position', handle(() => {
    if (flow.state?.idea_status !== 'active') return false;
    flow.edit('assess', current()); return flow.save('assess');
  }), 'primary');
  accept.id = 'assess-accept'; accept.disabled = !active || blocked() || flow.busy || !validAssess(current()) || !validRaw || !available || !neighborsCurrent || !eligible ||
    position.proposed_position > backlog.order.length;
  foot.append(accept);
  if (selected && !eligible) {
    // The human path stays one explicit click away when a used suggestion stops being acceptable.
    // Unlinking only: it reveals the proposed position for editing, then the human accepts.
    const own = button('Use as my own answers', handle(() => flow.clearProposal('assess')));
    own.id = 'assess-accept-own'; own.disabled = !active || blocked() || flow.busy;
    const why = element('p', 'The suggestion you used can no longer be accepted. Your edited answers are kept: use them as your own, which unlinks the suggestion and lets you edit the proposed position, then accept.', 'foot-message'); why.id = 'assess-accept-own-reason';
    foot.append(own, why);
  }
}
