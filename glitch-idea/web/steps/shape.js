// Human Shape editor; Flow owns proposal sources and acceptance.
import {validShape, conversationStatus, filledNote} from '../folds.js';

const SCOPES = [['small-change', 'Small change'], ['capability', 'New capability'], ['project', 'Project'], ['epic', 'Epic']];
const REASONS = {
  wrong_generation: 'This suggestion belongs to an earlier agent session.',
  wrong_session: 'This suggestion belongs to another session.',
  agent_unavailable: 'No agent is connected. You can fill this step by hand, or reinvoke /glitch-idea in your terminal to be guided.',
  stale_revision: 'The accepted idea changed. Reload this page to ask your terminal again.',
  stale_source: 'Saved inputs changed. Reload this page to ask your terminal again.',
  proposal_timeout: 'The suggestion wait ended. Reload this page to ask your terminal again.',
  projection_omitted: 'This suggestion is saved in Markdown; its body is omitted from this view.',
};

export function render({body, foot, flow, element, button, field, connected, edited, handle, proposalInventory}) {
  const archived = flow.state?.idea_status === 'archived';
  if (archived) {
    const notice = element('p', 'This idea is archived. Accepting a redone Shape reactivates the idea. The archived plan evidence remains unchanged.', 'notice');
    notice.id = 'shape-reactivation-notice'; notice.setAttribute('role', 'status'); body.append(notice);
  }
  // Partial durable drafts may omit empty containers; these are editor state,
  // never invented routes, risks or learning content.
  const current = () => ({outcome: '', scope: null, scope_reason: '', alternatives: [],
    assumptions: [], next_slice: '', learning: [], ...flow.buffers.shape,
    alternatives: flow.buffers.shape.alternatives ?? [], assumptions: flow.buffers.shape.assumptions ?? [],
    learning: flow.buffers.shape.learning ?? []});
  const mark = (name, id, target, input) => filledNote({flow, key: 'shape', name, id, element, target, input});
  const blocked = () => !connected || flow.inputLocked() || (Boolean(flow.pending) && !flow.draftFlight);
  const change = (key, value) => edited('shape', {...current(), [key]: value});
  const talk = element('p', conversationStatus(flow, 'shape'), 'notice'); talk.id = 'shape-conversation-status'; talk.setAttribute('role', 'status'); body.append(talk);
  const textField = (target, label, id, value, changed) => field(target, label, id, value, changed, true);
  const outcome = textField(body, 'Desired result', 'shape-outcome', current().outcome, value => change('outcome', value));
  outcome.required = true; mark('outcome', 'shape-outcome', body, outcome);

  const scope = element('fieldset');
  scope.append(element('legend', 'Scope'));
  for (const [value, label] of SCOPES) {
    const choice = button(label, () => change('scope', value), 'rating');
    choice.id = 'shape-scope-' + value;
    choice.setAttribute('aria-pressed', String(current().scope === value));
    choice.disabled = blocked();
    scope.append(choice);
  }
  body.append(scope); mark('scope', 'shape-scope', scope);
  const scopeReason = textField(body, 'Why this scope?', 'shape-scope-reason', current().scope_reason, value => change('scope_reason', value));
  scopeReason.required = true; mark('scope_reason', 'shape-scope-reason', body, scopeReason);

  const alternatives = element('fieldset');
  alternatives.append(element('legend', 'Alternatives, including a simpler route'));
  (current().alternatives ?? []).forEach((item, index) => {
    const row = element('div', '', 'field');
    for (const [key, label] of [['route', 'Route'], ['reason', 'Why consider it?']]) {
      textField(row, label, 'shape-alternative-' + index + '-' + key, item[key], value => {
        const items = (current().alternatives ?? []).map((entry, offset) => offset === index ? {...entry, [key]: value} : entry);
        change('alternatives', items);
      });
    }
    const remove = button('Remove route', () => change('alternatives', current().alternatives.filter((_, offset) => offset !== index)));
    remove.id = 'shape-remove-alternative-' + index; remove.disabled = blocked();
    row.append(remove); alternatives.append(row);
  });
  const addRoute = button('Add route', () => change('alternatives', [...(current().alternatives ?? []), {route: '', reason: ''}]));
  addRoute.id = 'shape-add-alternative'; addRoute.disabled = blocked() || (current().alternatives?.length ?? 0) >= 1000;
  alternatives.append(addRoute); body.append(alternatives); mark('alternatives', 'shape-alternatives', alternatives);

  for (const [key, title, label] of [['assumptions', 'Uncertainty and risk', 'Assumption or risk'], ['learning', 'Learning for the next slice', 'Learning']]) {
    const group = element('fieldset'); group.append(element('legend', title));
    if (key === 'assumptions') group.append(element('p', 'Recorded apart from scope. A risky small change is still a small change.', 'help'));
    (current()[key] ?? []).forEach((value, index) => {
      const row = element('div', '', 'field');
      textField(row, label, 'shape-' + key + '-' + index, value, next => change(key, current()[key].map((item, offset) => offset === index ? next : item)));
      const remove = button('Remove ' + label.toLowerCase(), () => change(key, current()[key].filter((_, offset) => offset !== index)));
      remove.id = 'shape-remove-' + key + '-' + index; remove.disabled = blocked(); row.append(remove); group.append(row);
    });
    const add = button('Add ' + label.toLowerCase(), () => change(key, [...(current()[key] ?? []), '']));
    add.id = 'shape-add-' + key; add.disabled = blocked() || (current()[key]?.length ?? 0) >= 1000; group.append(add); body.append(group); mark(key, 'shape-' + key, group);
  }
  const nextSlice = textField(body, 'Next slice', 'shape-next-slice', current().next_slice, value => change('next_slice', value));
  nextSlice.required = true; mark('next_slice', 'shape-next-slice', body, nextSlice);

  const assistance = element('section'); assistance.setAttribute('aria-label', 'Shape suggestions');
  const pending = flow.proposalPending?.key === 'shape' ? flow.proposalPending : null;
  if (pending) {
    const status = element('p', pending.phase === 'sending' ? 'Sending your suggestion request…' : pending.phase === 'waiting' ?
      'Waiting for the initiating agent. Your answers remain editable.' : pending.ambiguous ?
        'The request may have reached the agent. Retry the same request to check it.' : 'The suggestion request failed. Your answers remain.');
    status.id = 'shape-proposal-status'; status.setAttribute('role', 'status'); assistance.append(status);
    if (pending.phase === 'failed') {
      const retry = button('Retry same request', handle(() => flow.retryProposal())); retry.id = 'shape-retry'; retry.disabled = blocked(); assistance.append(retry);
    }
    const stop = button('Stop waiting', handle(() => {flow.cancelProposal('user_cancelled'); flow.onChange();}));
    stop.id = 'shape-stop-waiting'; stop.disabled = blocked(); assistance.append(stop);
  }
  if (flow.proposalError) {
    const error = element('p', REASONS[flow.proposalError.code] ?? 'AI assistance could not complete this request. Your answers remain.', 'notice');
    error.id = 'shape-proposal-error'; error.setAttribute('role', 'status'); assistance.append(error);
  }
  for (const item of flow.proposals('shape')) {
    const suggestion = element('section', '', 'notice');
    suggestion.id = 'shape-proposal-' + item.proposal_id;
    suggestion.append(element('h2', 'Proposed Shape'));
    if (item.proposal !== null) {
      const fields = item.proposal;
      suggestion.append(element('p', 'Desired result: ' + fields.outcome), element('p', 'Scope: ' + (SCOPES.find(([value]) => value === fields.scope)?.[1] ?? fields.scope) + ' — ' + fields.scope_reason));
      for (const route of fields.alternatives) suggestion.append(element('p', 'Alternative: ' + route.route + ' — ' + route.reason));
      for (const assumption of fields.assumptions) suggestion.append(element('p', 'Uncertainty or risk: ' + assumption));
      suggestion.append(element('p', 'Next slice: ' + fields.next_slice));
      for (const learning of fields.learning) suggestion.append(element('p', 'Learning: ' + learning));
    }
    if (!item.acceptance_eligible) suggestion.append(element('p', REASONS[item.acceptance_reason] ?? 'This suggestion is unavailable for acceptance.'));
    suggestion.append(element('p', 'Saved suggestion: ' + item.evidence.path));
    const use = button('Use suggestion', handle(() => flow.useProposal('shape', item.proposal_id)));
    use.id = 'shape-use-proposal-' + item.proposal_id;
    use.disabled = blocked() || flow.paused || flow.state?.agent_status !== 'connected' || !item.acceptance_eligible || item.content_omitted;
    suggestion.append(use); assistance.append(suggestion);
  }
  body.append(assistance); proposalInventory(body, 'shape');
  const selected = flow.selectedProposals.shape;
  if (selected) {
    body.append(element('p', 'Your edited Shape is linked to the suggestion you chose. Accept your answers explicitly.', 'notice'));
    const manual = button('Use my answers without linking a suggestion', handle(() => flow.clearProposal('shape')));
    manual.id = 'shape-manual'; manual.disabled = blocked(); body.append(manual);
  }
  const eligible = !selected || flow.proposals('shape').some(item => item.proposal_id === selected && item.acceptance_eligible && !item.content_omitted);
  const accept = button(flow.busy ? 'Saving…' : archived ? 'Reactivate idea and accept Shape' : 'Accept and continue', handle(() => {flow.edit('shape', current()); return flow.save('shape');}), 'primary');
  accept.id = 'shape-accept'; accept.disabled = blocked() || flow.busy || flow.paused || !validShape(current()) || !eligible;
  foot.append(accept);
  if (selected && !eligible) {
    // The human path stays one explicit click away when a used suggestion stops being acceptable.
    const own = button('Accept as my own answers', handle(() => {
      if (!flow.clearProposal('shape')) return false;
      flow.edit('shape', current()); return flow.save('shape');
    }));
    own.id = 'shape-accept-own'; own.disabled = blocked() || flow.busy || flow.paused || !validShape(current());
    const why = element('p', 'The suggestion you used can no longer be accepted. Your edited answers are kept: accept them as your own, which unlinks the suggestion.', 'foot-message'); why.id = 'shape-accept-own-reason';
    foot.append(own, why);
  }
}
