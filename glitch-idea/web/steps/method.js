// Explicit human Method choice; memory is supporting evidence only.
import {validMethod, conversationStatus, filledNote} from '../folds.js';

// APIV is a tentative label for the bounded-plan route; change it here only.
export const APIV_LABEL = 'APIV';
const METHODS = [
  ['bounded-plan', 'Bounded plan (' + APIV_LABEL + ')', 'A sufficiently understood change and its acceptance criteria.'],
  ['adaptive-slices', 'Adaptive vertical slices', 'The outcome and the next useful increment.'],
  ['appetite-led', 'Appetite-led shaping', 'An investment boundary you choose; scope varies inside it.'],
  ['experiment-led', 'Experiment-led discovery', 'One uncertainty and the evidence that decides the next move.'],
];
// Plain-language "Tell me more" text, paraphrased from references/methods.md.
const MORE = {
  'bounded-plan': 'This is the plan-then-build route, the one called ' + APIV_LABEL + '. You fix a clear scope and its acceptance criteria now, and the next step is earned by verifying the bounded change. It fits a change you already understand. It can still deliver a slice or an experiment inside another method.',
  'adaptive-slices': 'You fix the outcome and the next useful increment, not the whole route. What you learn from building and using each slice reshapes the next one. It fits evolving requirements or larger work.',
  'appetite-led': 'You set an investment cap and the outcome boundaries, and the scope flexes inside that cap. The next step is earned by a completed bet or an explicit stop or reshape. It fits work where the scope can vary within the cap you choose.',
  'experiment-led': 'You fix a question, the evidence that would settle it and a bounded experiment. The evidence decides whether to proceed, change course or stop. It fits work whose value or feasibility is uncertain.',
};
const MEMORY = {
  found: 'A saved preference was reported by the initiating agent.',
  searched_no_preference: 'No saved preference found in the reported search.',
  unavailable: 'Memory is unavailable. No preference is claimed.',
  error: 'Memory retrieval failed. No preference is claimed.',
};
const REASONS = {
  wrong_generation: 'This recommendation belongs to an earlier agent session.',
  wrong_session: 'This recommendation belongs to another session.',
  agent_unavailable: 'No agent is connected. You can fill this step by hand, or reinvoke /glitch-idea in your terminal to be guided.',
  stale_revision: 'The accepted idea changed. Reload this page to ask your terminal again.',
  stale_source: 'Saved inputs changed. Reload this page to ask your terminal again.',
  proposal_timeout: 'The recommendation wait ended. Reload this page to ask your terminal again.',
  projection_omitted: 'This recommendation is saved in Markdown; its body is omitted from this view.',
};
// UI-only raw edits never enter the numeric workflow schema. Each Flow and
// idea owns its own edit; authoritative numeric changes reset the displayed text.
const capEdits = new WeakMap();
function capEdit(flow, canonical) {
  canonical ??= null;
  const idea = flow.state?.idea_id ?? null;
  let edit = capEdits.get(flow);
  if (!edit || edit.idea !== idea || edit.canonical !== canonical) {
    edit = {idea, canonical, raw: canonical === null ? '' : String(canonical)};
    capEdits.set(flow, edit);
  }
  return edit;
}
function decimalCap(raw) {
  // A comma is explicitly supported as a decimal separator, never grouping.
  if (!/^(?:[0-9]+(?:[.,][0-9]+)?|[.,][0-9]+)$/.test(raw)) return null;
  const number = Number(raw.replace(',', '.'));
  return Number.isFinite(number) && number > 0 && number <= 1e12 ? number : null;
}

const title = value => METHODS.find(([key]) => key === value)?.[1] ?? 'Not chosen';

export function render({body, foot, flow, element, button, field, connected, edited, handle, proposalInventory}) {
  const active = flow.state?.idea_status === 'active';
  if (!active) {
    const notice = element('p', flow.state?.idea_status === 'archived' ?
      'This idea is archived. To confirm a new method, first explicitly redo and accept Shape to reactivate it. Archived history is kept; your draft remains here.' :
      'The idea status is unavailable. Reload current state before confirming a method. Your draft remains here.', 'notice');
    notice.id = 'method-idea-status'; notice.setAttribute('role', 'status'); body.append(notice);
  }
  const talk = element('p', conversationStatus(flow, 'method'), 'notice'); talk.id = 'method-conversation-status'; talk.setAttribute('role', 'status'); body.append(talk);
  const current = () => ({selection: null, reason: '', investment: null, experiment: null,
    ...flow.buffers.method, memory: {status: 'unavailable', sources: [], rationale: null, ...(flow.buffers.method.memory ?? {})}});
  if (current().selection !== 'appetite-led') capEdits.delete(flow);
  const blocked = () => !connected || flow.inputLocked() || (Boolean(flow.pending) && !flow.draftFlight);
  const change = (key, value) => edited('method', {...current(), [key]: value});
  const proposals = flow.proposals('method');
  const recommended = proposals.find(item => item.acceptance_eligible && !item.content_omitted && item.proposal !== null);
  const cards = element('fieldset'); cards.append(element('legend', 'Your methodology'));
  const choices = element('div', '', 'method-cards'); choices.id = 'method-cards';
  for (const [key, label, commitment] of METHODS) {
    const choice = button('', () => {
      const fields = current();
      if (fields.selection === key) return;
      capEdits.delete(flow);
      edited('method', {...fields, selection: key,
        investment: null, experiment: null});
    });
    choice.id = 'method-choice-' + key;
    choice.setAttribute('aria-pressed', String(current().selection === key));
    choice.disabled = blocked();
    choice.className = 'method-card';
    choice.append(element('strong', label, 'method-card-name'), element('span', 'Commit to: ' + commitment, 'method-card-commit'));
    if (recommended?.proposal.selection === key) choice.append(element('span', 'Recommended', 'method-card-flag'));
    choices.append(choice);
  }
  cards.append(choices);
  const more = element('div', '', 'method-more');
  for (const [key, label] of METHODS) {
    const details = element('details'); details.id = 'method-more-' + key;
    details.append(element('summary', 'Tell me more about ' + label), element('p', MORE[key]));
    more.append(details);
  }
  cards.append(more); body.append(cards);
  body.append(element('p', 'Your choice: ' + title(current().selection), 'notice'));
  const reason = field(body, 'Why this method?', 'method-reason', current().reason, value => change('reason', value), true);
  reason.required = true; filledNote({flow, key: 'method', name: 'reason', id: 'method-reason', element, target: body, input: reason});
  if (current().selection === 'appetite-led') {
    const group = element('fieldset'); group.append(element('legend', 'Your investment boundary'));
    const investment = () => ({cap: null, unit: '', boundary: '', ...(current().investment ?? {})});
    const update = (key, value) => change('investment', {...investment(), [key]: value});
    const edit = capEdit(flow, investment().cap);
    const cap = field(group, 'Investment cap', 'method-investment-cap', edit.raw, value => {
      if (current().selection !== 'appetite-led') return;
      const active = capEdit(flow, investment().cap);
      active.raw = value;
      active.canonical = decimalCap(value);
      update('cap', active.canonical);
    });
    cap.type = 'text'; cap.inputMode = 'decimal'; cap.required = true;
    group.append(element('p', 'Enter a positive decimal cap. A dot or comma may separate the decimal part.', 'help'));
    field(group, 'Unit', 'method-investment-unit', investment().unit, value => update('unit', value)).required = true;
    field(group, 'Boundary', 'method-investment-boundary', investment().boundary, value => update('boundary', value), true).required = true;
    body.append(group);
  }
  if (current().selection === 'experiment-led') {
    const group = element('fieldset'); group.append(element('legend', 'Your experiment'));
    const experiment = () => ({question: '', evidence: '', success_criterion: '', stop_rule: '', ...(current().experiment ?? {})});
    for (const [key, label] of [['question', 'Question'], ['evidence', 'Evidence to collect'], ['success_criterion', 'Success criterion'], ['stop_rule', 'Stop rule']]) {
      field(group, label, 'method-experiment-' + key.replaceAll('_', '-'), experiment()[key],
        value => change('experiment', {...experiment(), [key]: value}), true).required = true;
    }
    body.append(group);
  }

  const memory = current().memory;
  const supporting = element('section', '', 'notice'); supporting.id = 'method-memory';
  supporting.append(element('h2', 'Supporting memory'), element('p', MEMORY[memory.status] ?? 'Memory result is incomplete. No preference is claimed.'));
  if (memory.rationale) supporting.append(element('p', memory.rationale));
  for (const reference of memory.sources ?? []) supporting.append(element('p', 'Source: ' + reference));
  supporting.append(element('p', 'Memory informs your choice; it does not select or submit a methodology.', 'help'));
  if (memory.status !== 'unavailable') {
    const unavailable = button('Continue without a memory claim', handle(() => {
      flow.clearProposal('method');
      change('memory', {status: 'unavailable', sources: [], rationale: null});
    }));
    unavailable.id = 'method-memory-unavailable'; unavailable.disabled = blocked(); supporting.append(unavailable);
  }
  body.append(supporting);

  const assistance = element('section'); assistance.setAttribute('aria-label', 'Method recommendations');
  const pending = flow.proposalPending?.key === 'method' ? flow.proposalPending : null;
  if (pending) {
    const status = element('p', pending.phase === 'sending' ? 'Sending your recommendation request…' : pending.phase === 'waiting' ?
      'Waiting for the initiating agent. Your choice remains editable.' : pending.ambiguous ?
        'The request may have reached the agent. Retry the same request to check it.' : 'The recommendation request failed. Your answers remain.');
    status.id = 'method-proposal-status'; status.setAttribute('role', 'status'); assistance.append(status);
    if (pending.phase === 'failed') {
      const retry = button('Retry same request', handle(() => flow.retryProposal())); retry.id = 'method-retry'; retry.disabled = blocked(); assistance.append(retry);
    }
    const stop = button('Stop waiting', handle(() => {flow.cancelProposal('user_cancelled'); flow.onChange();}));
    stop.id = 'method-stop-waiting'; stop.disabled = blocked(); assistance.append(stop);
  }
  if (flow.proposalError) {
    const error = element('p', REASONS[flow.proposalError.code] ?? 'AI assistance could not complete this request. Your answers remain.', 'notice');
    error.id = 'method-proposal-error'; error.setAttribute('role', 'status'); assistance.append(error);
  }
  for (const item of proposals) {
    const box = element('section', '', 'notice'); box.id = 'method-proposal-' + item.proposal_id;
    box.append(element('h2', 'AI recommendation'));
    if (item.proposal !== null) {
      box.append(element('strong', title(item.proposal.selection)), element('p', item.proposal.reason));
      if (item.proposal.investment) box.append(element('p', 'Proposed investment: ' + item.proposal.investment.cap + ' ' +
        item.proposal.investment.unit + ' — ' + item.proposal.investment.boundary));
      if (item.proposal.experiment) for (const [key, label] of [['question', 'Question'], ['evidence', 'Evidence'],
        ['success_criterion', 'Success criterion'], ['stop_rule', 'Stop rule']]) box.append(element('p', label + ': ' + item.proposal.experiment[key]));
      const reported = item.proposal.memory;
      box.append(element('p', MEMORY[reported.status]));
      if (reported.rationale) box.append(element('p', reported.rationale));
      for (const reference of reported.sources) box.append(element('p', 'Source: ' + reference));
      if (current().selection && current().selection !== item.proposal.selection) box.append(element('p',
        'You chose differently from this recommendation. Yours is the method that acceptance records.'));
    }
    if (!item.acceptance_eligible) box.append(element('p', REASONS[item.acceptance_reason] ?? 'This recommendation is unavailable for acceptance.'));
    box.append(element('p', 'Saved recommendation: ' + item.evidence.path));
    const use = button('Use recommendation reason and memory', handle(() => flow.useProposal('method', item.proposal_id)));
    use.id = 'method-use-proposal-' + item.proposal_id;
    use.disabled = blocked() || flow.paused || flow.state?.agent_status !== 'connected' || !item.acceptance_eligible || item.content_omitted;
    box.append(use); assistance.append(box);
  }
  body.append(assistance); proposalInventory(body, 'method');
  const selected = flow.selectedProposals.method;
  if (selected) {
    const manual = button('Use my answers without linking a recommendation', handle(() => flow.clearProposal('method')));
    manual.id = 'method-manual'; manual.disabled = blocked(); body.append(manual);
  }
  const eligible = !selected || proposals.some(item => item.proposal_id === selected && item.acceptance_eligible && !item.content_omitted);
  const confirm = button(flow.busy ? 'Saving…' : 'Confirm method', handle(() => {
    if (flow.state?.idea_status !== 'active') return false;
    flow.edit('method', current()); return flow.save('method');
  }), 'primary');
  confirm.id = 'method-accept'; confirm.disabled = !active || blocked() || flow.busy || flow.paused || !validMethod(current()) || !eligible;
  foot.append(confirm);
  if (selected && !eligible) {
    // The human path stays one explicit click away when a used recommendation stops being acceptable.
    const own = button('Confirm as my own answers', handle(() => {
      if (flow.state?.idea_status !== 'active' || !flow.clearProposal('method')) return false;
      flow.edit('method', current()); return flow.save('method');
    }));
    own.id = 'method-accept-own'; own.disabled = !active || blocked() || flow.busy || flow.paused || !validMethod(current());
    const why = element('p', 'The recommendation you used can no longer be accepted. Your edited answers are kept: confirm them as your own, which unlinks the recommendation.', 'foot-message');
    why.id = 'method-accept-own-reason'; foot.append(own, why);
  }
  foot.append(element('p', 'Grilling and wayfinding can help you choose; neither replaces a delivery method.', 'help'));
}
