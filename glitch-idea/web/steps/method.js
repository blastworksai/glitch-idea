// Explicit human Method choice. Four equal cards; the agent never recommends, selects or fills a method.
// The terminal conversation can only report what your Glitch remembers (memory), shown as one line.
import {METHOD_LABELS, validMethod, conversationStatus, disabledAcceptReason} from '../folds.js';

const LINES = {
  'bounded-plan': 'Agree the scope and acceptance criteria now, then build it.',
  'adaptive-slices': 'Fix the outcome and the next useful slice; learn as you go.',
  'appetite-led': 'Set how much you will invest; the scope flexes inside it.',
  'experiment-led': 'Start from one uncertainty and the evidence that settles it.',
};
const METHODS = Object.entries(METHOD_LABELS).map(([key, label]) => [key, label, LINES[key]]);
// Plain-language "Tell me more" text, paraphrased from references/methods.md.
const MORE = {
  'bounded-plan': 'You fix a clear scope and its acceptance criteria now, and the next step is earned by verifying the bounded change. It fits a change you already understand. It can still deliver a slice or an experiment inside another method.',
  'adaptive-slices': 'You fix the outcome and the next useful increment, not the whole route. What you learn from building and using each slice reshapes the next one. It fits evolving requirements or larger work.',
  'appetite-led': 'You set an investment cap and the outcome boundaries, and the scope flexes inside that cap. The next step is earned by a completed bet or an explicit stop or reshape. It fits work where the scope can vary within the cap you choose. You give the cap in Exploration.',
  'experiment-led': 'You fix a question, the evidence that would settle it and a bounded experiment. The evidence decides whether to proceed, change course or stop. It fits work whose value or feasibility is uncertain. You give the question and evidence in Exploration.',
};
const APIV_NOTE = 'All four methods run inside the same loop: Align, Plan, Implement, Verify (APIV).';
const REASONS = {
  wrong_generation: 'This memory result belongs to an earlier agent session.',
  wrong_session: 'This memory result belongs to another session.',
  agent_unavailable: 'No agent is connected. You can choose a method by hand, or reinvoke /glitch-idea in your terminal to be guided.',
  stale_revision: 'The accepted idea changed. Reload this page to ask your terminal again.',
  stale_source: 'Saved inputs changed. Reload this page to ask your terminal again.',
  proposal_timeout: 'The wait ended. Reload this page to ask your terminal again.',
  projection_omitted: 'This memory result is saved in Markdown; its body is omitted from this view.',
};

const title = value => METHODS.find(([key]) => key === value)?.[1] ?? 'Not chosen';
// One line per memory status; no other memory prose is ever shown.
export function memoryLine(memory) {
  switch (memory?.status) {
    case 'found': return 'Usually: ' + title(memory.preferred_method) + '.';
    case 'varied': return 'You use all four. Pick freely.';
    case 'searched_no_preference': return 'Learning your preference — a few more ideas.';
    default: return 'Memory offline.';
  }
}

export function render({body, foot, flow, element, button, field, connected, edited, handle, proposalInventory}) {
  const active = flow.state?.idea_status === 'active';
  if (!active) {
    const notice = element('p', flow.state?.idea_status === 'archived' ?
      'This idea is archived. Reactivate it before confirming a new method. Archived history is kept; your draft remains here.' :
      'The idea status is unavailable. Reload current state before confirming a method. Your draft remains here.', 'notice');
    notice.id = 'method-idea-status'; notice.setAttribute('role', 'status'); body.append(notice);
  }
  const talk = element('p', conversationStatus(flow, 'method'), 'notice'); talk.id = 'method-conversation-status'; talk.setAttribute('role', 'status'); body.append(talk);
  const current = () => ({selection: null, reason: '',
    ...flow.buffers.method, memory: {status: 'unavailable', sources: [], rationale: null, ...(flow.buffers.method.memory ?? {})}});
  const blocked = () => !connected || flow.inputLocked() || (Boolean(flow.pending) && !flow.draftFlight);
  const change = (key, value) => edited('method', {...current(), [key]: value});
  const proposals = flow.proposals('method');

  // The memory notice leads the step, as drawn: what your Glitch remembers, one line.
  const memory = element('section', '', 'memory-line'); memory.id = 'method-memory';
  memory.append(element('h2', 'Your Glitch remembers'), element('p', memoryLine(current().memory)));
  body.append(memory);

  const cards = element('fieldset'); cards.append(element('legend', 'Your methodology'));
  const choices = element('div', '', 'method-cards'); choices.id = 'method-cards';
  for (const [key, label, line] of METHODS) {
    const choice = button('', () => {
      const fields = current();
      if (fields.selection === key) return;
      edited('method', {...fields, selection: key});
    });
    choice.id = 'method-choice-' + key;
    choice.setAttribute('aria-pressed', String(current().selection === key));
    choice.disabled = blocked();
    choice.className = 'g-choice method-card';
    choice.append(element('strong', label, 'method-card-name'), element('span', line, 'method-card-commit'));
    choices.append(choice);
  }
  cards.append(choices);
  const more = element('div', '', 'method-more');
  for (const [key, label] of METHODS) {
    const details = element('details'); details.id = 'method-more-' + key;
    details.append(element('summary', 'Tell me more about ' + label), element('p', MORE[key]));
    more.append(details);
  }
  more.append(element('p', APIV_NOTE, 'help'));
  cards.append(more); body.append(cards);
  body.append(element('p', 'Your choice: ' + title(current().selection), 'notice'));

  const reason = field(body, 'Why this method? Optional', 'method-reason', current().reason, value => change('reason', value), true);
  body.append(element('p', 'Helps /glitch-plan understand the choice, and teaches your Glitch your preference over time.', 'help'));

  const assistance = element('section'); assistance.setAttribute('aria-label', 'Memory results');
  const pending = flow.proposalPending?.key === 'method' ? flow.proposalPending : null;
  if (pending) {
    const status = element('p', pending.phase === 'sending' ? 'Sending your memory request…' : pending.phase === 'waiting' ?
      'Waiting for the initiating agent. Your choice remains editable.' : pending.ambiguous ?
        'The request may have reached the agent. Retry the same request to check it.' : 'The memory request failed. Your answers remain.');
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
    // A method reply is memory only: its one memory line, never a selection.
    if (item.proposal !== null) box.append(element('p', memoryLine(item.proposal.memory)));
    if (!item.acceptance_eligible) box.append(element('p', REASONS[item.acceptance_reason] ?? 'This memory result is unavailable for acceptance.'));
    box.append(element('p', 'Saved result: ' + item.evidence.path));
    const use = button('Use this memory', handle(() => flow.useProposal('method', item.proposal_id)));
    use.id = 'method-use-proposal-' + item.proposal_id;
    use.disabled = blocked() || flow.paused || flow.state?.agent_status !== 'connected' || !item.acceptance_eligible || item.content_omitted;
    box.append(use); assistance.append(box);
  }
  body.append(assistance); proposalInventory(body, 'method');
  const selected = flow.selectedProposals.method;
  if (selected) {
    const manual = button('Use my answers without linking a memory result', handle(() => flow.clearProposal('method')));
    manual.id = 'method-manual'; manual.disabled = blocked(); body.append(manual);
  }
  const eligible = !selected || proposals.some(item => item.proposal_id === selected && item.acceptance_eligible && !item.content_omitted);
  const confirm = button(flow.busy ? 'Saving…' : 'Confirm method', handle(() => {
    if (flow.state?.idea_status !== 'active') return false;
    flow.edit('method', current()); return flow.save('method');
  }), 'primary');
  confirm.id = 'method-accept'; confirm.disabled = !active || blocked() || flow.busy || flow.paused || !validMethod(current()) || !eligible;
  foot.append(confirm);
  if (confirm.disabled) {
    const text = disabledAcceptReason('method', current(), flow, {connected, eligible});
    const why = element('p', text, 'foot-message accept-reason'); why.id = 'method-accept-reason';
    confirm.setAttribute('aria-describedby', why.id); confirm.title = text; foot.append(why);
  }
  if (selected && !eligible) {
    // The human path stays one explicit click away when a used memory result stops being acceptable.
    const own = button('Confirm as my own answers', handle(() => {
      if (flow.state?.idea_status !== 'active' || !flow.clearProposal('method')) return false;
      flow.edit('method', current()); return flow.save('method');
    }));
    own.id = 'method-accept-own'; own.disabled = !active || blocked() || flow.busy || flow.paused || !validMethod(current());
    const why = element('p', 'The memory result you used can no longer be accepted. Your answers are kept: confirm them as your own, which unlinks the result.', 'foot-message');
    why.id = 'method-accept-own-reason'; foot.append(own, why);
  }
}
