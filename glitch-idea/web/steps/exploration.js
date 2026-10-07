// Human Exploration editor; Flow owns proposal sources and acceptance.
import {METHOD_LABELS, validExploration, conversationStatus, filledNote, disabledAcceptReason, waitingLine, FILL_KEYS} from '../folds.js';

const KEY = 'exploration';
const SCOPES = [['small-change', 'Small change'], ['capability', 'New capability'], ['project', 'Project'], ['epic', 'Epic']];
const MAX_SKETCH = 5;
const EXPERIMENT = [['question', 'Question'], ['evidence', 'Evidence to collect'], ['success_criterion', 'Success criterion'], ['stop_rule', 'Stop rule']];
const REASONS = {
  wrong_generation: 'This suggestion belongs to an earlier agent session.',
  wrong_session: 'This suggestion belongs to another session.',
  agent_unavailable: 'No agent is connected. You can fill this step by hand, or reinvoke /glitch-idea in your terminal to be guided.',
  stale_revision: 'The accepted idea changed. Reload this page to ask your terminal again.',
  stale_source: 'Saved inputs changed. Reload this page to ask your terminal again.',
  proposal_timeout: 'The suggestion wait ended. Reload this page to ask your terminal again.',
  projection_omitted: 'This suggestion is saved in Markdown; its body is omitted from this view.',
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
const methodLabel = value => METHOD_LABELS[value] ?? null;
// Buttons sit in a row of their own so a form column never stretches them full width.
const actions = (element, ...buttons) => { const row = element('div', '', 'setup-actions'); row.append(...buttons); return row; };

export function render({body, foot, flow, element, button, field, connected, edited, handle, proposalInventory}) {
  const archived = flow.state?.idea_status === 'archived';
  if (archived) {
    const notice = element('p', 'This idea is archived. Accepting a redone Exploration reactivates the idea. The archived plan evidence remains unchanged.', 'notice');
    notice.id = 'exploration-reactivation-notice'; notice.setAttribute('role', 'status'); body.append(notice);
  }
  const method = flow.state?.accepted?.method?.selection ?? null;
  if (method !== 'appetite-led') capEdits.delete(flow);
  // Partial durable drafts may omit empty containers; these are editor state, never invented content.
  const current = () => {
    const buffer = flow.buffers.exploration ?? {};
    return {outcome: '', scope: null, scope_reason: '', next_slice: '', investment: null, experiment: null, ...buffer,
      alternatives: buffer.alternatives ?? [], assumptions: buffer.assumptions ?? [], learning: buffer.learning ?? [], sketch: buffer.sketch ?? []};
  };
  const mark = (name, id, target, input) => filledNote({flow, key: KEY, name, id, element, target, input});
  const blocked = () => !connected || flow.inputLocked() || (Boolean(flow.pending) && !flow.draftFlight);
  const change = (key, value) => edited(KEY, {...current(), [key]: value});
  const locked = name => flow.fieldLocked(KEY, name);
  const shown = FILL_KEYS.exploration.filter(name => !(name === 'investment' && method !== 'appetite-led') && !(name === 'experiment' && method !== 'experiment-led'));
  const anyLocked = shown.some(locked);
  const lockNote = () => element('p', 'Waiting for your terminal', 'lock-note');
  // A locked group is a `.field-locked` wrapper holding the controls and the note.
  // Group notes lead the wrapper; a single field's note goes between its label and its input.
  const lockWrap = (target, name, inline = false) => {
    if (!locked(name)) return {host: target, finish() {}};
    const host = element('div', '', 'field-locked');
    if (!inline) host.append(lockNote());
    return {host, finish() { target.append(host); }};
  };
  const textField = (target, label, id, value, changed, area = true, name = null) => {
    const wrap = name ? lockWrap(target, name, true) : {host: target, finish() {}};
    const input = field(wrap.host, label, id, value, changed, area);
    if (name && locked(name)) {
      input.disabled = true; input.readOnly = true;
      const note = lockNote(); note.id = id + '-lock'; input.setAttribute?.('aria-describedby', note.id);
      if (input.parentNode?.insertBefore) input.parentNode.insertBefore(note, input); else wrap.host.append(note);
    }
    wrap.finish();
    return input;
  };
  const gate = (control, name) => { control.disabled = blocked() || locked(name); return control; };

  const handReleased = flow.handReleased(KEY);
  const agent = flow.state?.agent_status === 'connected';
  const talk = element('p', !agent ? 'No terminal is connected, so this step is yours to fill.' : handReleased ?
    'You took this step by hand. Your terminal will not fill it.' : method !== null && methodLabel(method) ?
      'Your terminal is exploring this through ' + methodLabel(method) : conversationStatus(flow, KEY), 'notice');
  talk.id = 'exploration-conversation-status'; talk.setAttribute('role', 'status'); body.append(talk);
  if (anyLocked) {
    const hand = button('Fill this step by hand', handle(() => flow.releaseStep(KEY)), 'hand-fill');
    hand.id = 'exploration-hand-fill'; hand.disabled = !connected || Boolean(flow.releasing); body.append(hand);
  }

  // 1. Desired result
  const outcome = textField(body, 'Desired result', 'exploration-outcome', current().outcome, value => change('outcome', value), true, 'outcome');
  outcome.required = true; mark('outcome', 'exploration-outcome', body, outcome);

  // 2. Alternatives (an empty list shows one empty row until it is edited)
  {
    const wrap = lockWrap(body, 'alternatives');
    const alternatives = element('fieldset'); alternatives.id = 'exploration-alternatives';
    alternatives.append(element('legend', 'Alternatives, including a simpler route'));
    const stored = current().alternatives;
    const rows = stored.length ? stored : [{route: '', reason: ''}];
    rows.forEach((item, index) => {
      const row = element('div', '', 'g-card'), pair = element('div', '', 'g-cols');
      for (const [key, label] of [['route', 'Route'], ['reason', 'Why consider it?']]) {
        const input = field(pair, label, 'exploration-alternative-' + index + '-' + key, item[key], value => {
          const base = current().alternatives.length ? current().alternatives : [{route: '', reason: ''}];
          change('alternatives', base.map((entry, offset) => offset === index ? {...entry, [key]: value} : entry));
        }, true);
        if (locked('alternatives')) { input.disabled = true; input.readOnly = true; }
      }
      row.append(pair);
      if (stored.length) {
        const remove = gate(button('Remove route', () => change('alternatives', current().alternatives.filter((_, offset) => offset !== index)), 'bw-btn bw-btn--ghost'), 'alternatives');
        remove.id = 'exploration-remove-alternative-' + index; row.append(actions(element, remove));
      }
      alternatives.append(row);
    });
    const add = gate(button('Add route', () => change('alternatives', [...(stored.length ? stored : [{route: '', reason: ''}]), {route: '', reason: ''}])), 'alternatives');
    add.id = 'exploration-add-alternative'; add.disabled = add.disabled || stored.length >= 1000;
    alternatives.append(actions(element, add)); wrap.host.append(alternatives); wrap.finish(); mark('alternatives', 'exploration-alternatives', body, alternatives);
  }

  const listGroup = (key, title, label, help) => {
    const wrap = lockWrap(body, key);
    const group = element('fieldset'); group.id = 'exploration-' + key; group.append(element('legend', title));
    if (help) group.append(element('p', help, 'help'));
    current()[key].forEach((value, index) => {
      const row = element('div', '', 'field');
      const input = field(row, label, 'exploration-' + key + '-' + index, value, next => change(key, current()[key].map((item, offset) => offset === index ? next : item)), true);
      if (locked(key)) { input.disabled = true; input.readOnly = true; }
      const remove = gate(button('Remove ' + label.toLowerCase(), () => change(key, current()[key].filter((_, offset) => offset !== index)), 'bw-btn bw-btn--ghost'), key);
      remove.id = 'exploration-remove-' + key + '-' + index; row.append(actions(element, remove)); group.append(row);
    });
    const add = gate(button('Add ' + label.toLowerCase(), () => change(key, [...current()[key], ''])), key);
    add.id = 'exploration-add-' + key; add.disabled = add.disabled || current()[key].length >= 1000;
    group.append(actions(element, add)); wrap.host.append(group); wrap.finish(); mark(key, 'exploration-' + key, body, group);
  };
  // 3. Uncertainty and risk
  listGroup('assumptions', 'Uncertainty and risk', 'Assumption or risk', 'Recorded apart from scope. A risky small change is still a small change.');

  // 4. Scope and its reason
  const scopeCols = element('div', '', 'g-cols'); body.append(scopeCols);
  {
    const wrap = lockWrap(scopeCols, 'scope');
    const scope = element('fieldset'); scope.id = 'exploration-scope'; scope.append(element('legend', 'Scope'));
    const choices = element('div', '', 'setup-actions');
    for (const [value, label] of SCOPES) {
      const choice = gate(button(label, () => change('scope', value)), 'scope');
      choice.id = 'exploration-scope-' + value;
      choice.setAttribute('aria-pressed', String(current().scope === value));
      choices.append(choice);
    }
    scope.append(choices);
    wrap.host.append(scope); wrap.finish(); mark('scope', 'exploration-scope', body, scope);
  }
  const scopeReason = textField(scopeCols, 'Why this scope?', 'exploration-scope-reason', current().scope_reason, value => change('scope_reason', value), true, 'scope_reason');
  scopeReason.required = true; mark('scope_reason', 'exploration-scope-reason', body, scopeReason);

  // 5. Next slice
  const nextSlice = textField(body, 'Next slice', 'exploration-next-slice', current().next_slice, value => change('next_slice', value), true, 'next_slice');
  nextSlice.required = true; mark('next_slice', 'exploration-next-slice', body, nextSlice);

  // 6. Learning for the next slice
  listGroup('learning', 'Learning for the next slice', 'Learning');

  // 7. The accepted method's inputs
  if (method === 'appetite-led') {
    const wrap = lockWrap(body, 'investment');
    const group = element('fieldset'); group.id = 'exploration-investment'; group.append(element('legend', 'Investment'));
    const investment = () => ({cap: null, unit: '', boundary: '', ...(current().investment ?? {})});
    const update = (key, value) => change('investment', {...investment(), [key]: value});
    const edit = capEdit(flow, investment().cap);
    const lock = input => { if (locked('investment')) { input.disabled = true; input.readOnly = true; } return input; };
    const pairing = element('div', '', 'g-cols'); group.append(pairing);
    const cap = lock(field(pairing, 'Investment cap', 'exploration-investment-cap', edit.raw, value => {
      const active = capEdit(flow, investment().cap);
      active.raw = value; active.canonical = decimalCap(value);
      update('cap', active.canonical);
    }));
    cap.type = 'text'; cap.inputMode = 'decimal'; cap.required = true;
    group.append(element('p', 'Enter a positive decimal cap. A dot or comma may separate the decimal part.', 'help'));
    lock(field(pairing, 'Unit', 'exploration-investment-unit', investment().unit, value => update('unit', value), false)).required = true;
    lock(field(group, 'Boundary', 'exploration-investment-boundary', investment().boundary, value => update('boundary', value), true)).required = true;
    wrap.host.append(group); wrap.finish(); mark('investment', 'exploration-investment', body, group);
  } else if (current().investment != null) {
    const clear = button('Clear investment (it only applies to a fixed budget)', () => change('investment', null)); clear.disabled = blocked();
    clear.id = 'exploration-clear-investment'; body.append(clear);
  }
  if (method === 'experiment-led') {
    const wrap = lockWrap(body, 'experiment');
    const group = element('fieldset'); group.id = 'exploration-experiment'; group.append(element('legend', 'Experiment'));
    const experiment = () => ({question: '', evidence: '', success_criterion: '', stop_rule: '', ...(current().experiment ?? {})});
    const grid = element('div', '', 'g-cols'); group.append(grid);
    for (const [key, label] of EXPERIMENT) {
      const input = field(grid, label, 'exploration-experiment-' + key.replaceAll('_', '-'), experiment()[key],
        value => change('experiment', {...experiment(), [key]: value}), true);
      input.required = true;
      if (locked('experiment')) { input.disabled = true; input.readOnly = true; }
    }
    wrap.host.append(group); wrap.finish(); mark('experiment', 'exploration-experiment', body, group);
  } else if (current().experiment != null) {
    const clear = button('Clear experiment (it only applies to Experiment First)', () => change('experiment', null)); clear.disabled = blocked();
    clear.id = 'exploration-clear-experiment'; body.append(clear);
  }

  // 8. The sketch
  {
    const wrap = lockWrap(body, 'sketch');
    const group = element('fieldset'); group.id = 'exploration-sketch'; group.append(element('legend', 'Sketch: the first few items'));
    const items = current().sketch;
    const list = element('ol', '', 'sketch-cards');
    const setItem = (index, patch) => change('sketch', current().sketch.map((entry, offset) => offset === index ? {title: '', why_next: '', done_when: '', method: null, ...entry, ...patch} : entry));
    items.forEach((item, index) => {
      const card = element('li', '', 'g-card sketch-card');
      const head = element('div', '', 'sketch-head');
      const num = element('span', String(index + 1), 'sketch-num');
      head.append(num);
      const lockInput = input => { if (locked('sketch')) { input.disabled = true; input.readOnly = true; } return input; };
      lockInput(field(head, 'Title', 'exploration-sketch-' + index + '-title', item.title, value => setItem(index, {title: value}), false)).required = true;
      const chip = element('span', methodLabel(item.method) ?? 'overall method', 'bw-chip method-chip');
      if (item.method == null) chip.setAttribute('data-overall', 'true');
      head.append(chip); card.append(head);
      const lines = element('div', '', 'g-cols');
      for (const [key, label, id] of [['why_next', 'Why next:', 'why-next'], ['done_when', 'Done when:', 'done-when']]) {
        const line = element('div', '', 'sketch-line');
        const input = lockInput(field(line, label, 'exploration-sketch-' + index + '-' + id, item[key], value => setItem(index, {[key]: value}), true));
        if (key === 'done_when') input.required = true;
        lines.append(line);
      }
      card.append(lines);
      const pick = element('div', '', 'field');
      const label = element('label', 'Method for this item'); label.htmlFor = 'exploration-sketch-' + index + '-method';
      const select = element('select'); select.id = 'exploration-sketch-' + index + '-method';
      for (const [value, text] of [['', 'Overall method'], ...Object.entries(METHOD_LABELS)]) {
        const option = element('option', text); option.value = value;
        if ((item.method ?? '') === value) option.selected = true;
        select.append(option);
      }
      select.value = item.method ?? '';
      select.disabled = blocked() || locked('sketch');
      select.addEventListener('change', event => setItem(index, {method: event.target.value || null}));
      pick.append(label, select); card.append(pick);
      const remove = gate(button('Remove item', () => change('sketch', current().sketch.filter((_, offset) => offset !== index)), 'bw-btn bw-btn--ghost'), 'sketch');
      remove.id = 'exploration-remove-sketch-' + index; card.append(actions(element, remove));
      list.append(card);
    });
    group.append(list);
    const full = items.length >= MAX_SKETCH;
    const add = gate(button('Add item', () => change('sketch', [...current().sketch, {title: '', why_next: '', done_when: '', method: null}])), 'sketch');
    add.id = 'exploration-add-sketch'; add.disabled = add.disabled || full;
    group.append(actions(element, add));
    if (full) group.append(element('p', "Up to 5 items. Breaking down the work is /glitch-plan's job.", 'help'));
    wrap.host.append(group); wrap.finish(); mark('sketch', 'exploration-sketch', body, group);
  }

  const assistance = element('section'); assistance.setAttribute('aria-label', 'Exploration suggestions');
  const pending = flow.proposalPending?.key === KEY ? flow.proposalPending : null;
  if (pending) {
    const status = element('p', pending.phase === 'sending' ? 'Sending your suggestion request…' : pending.phase === 'waiting' ?
      waitingLine(flow, KEY) : pending.ambiguous ?
        'The request may have reached the agent. Retry the same request to check it.' : 'The suggestion request failed. Your answers remain.');
    status.id = 'exploration-proposal-status'; status.setAttribute('role', 'status'); assistance.append(status);
    if (pending.phase === 'failed') {
      const retry = button('Retry same request', handle(() => flow.retryProposal())); retry.id = 'exploration-retry'; retry.disabled = blocked(); assistance.append(retry);
    }
    const stop = button('Stop waiting', handle(() => {flow.cancelProposal('user_cancelled'); flow.onChange();}));
    stop.id = 'exploration-stop-waiting'; stop.disabled = blocked(); assistance.append(stop);
  }
  if (flow.proposalError) {
    const error = element('p', REASONS[flow.proposalError.code] ?? 'AI assistance could not complete this request. Your answers remain.', 'notice');
    error.id = 'exploration-proposal-error'; error.setAttribute('role', 'status'); assistance.append(error);
  }
  for (const item of flow.proposals(KEY)) {
    const suggestion = element('section', '', 'notice');
    suggestion.id = 'exploration-proposal-' + item.proposal_id;
    suggestion.append(element('h2', 'Proposed Exploration'));
    if (item.proposal !== null) {
      const fields = item.proposal;
      suggestion.append(element('p', 'Desired result: ' + fields.outcome), element('p', 'Scope: ' + (SCOPES.find(([value]) => value === fields.scope)?.[1] ?? fields.scope) + ' — ' + fields.scope_reason));
      for (const route of fields.alternatives) suggestion.append(element('p', 'Alternative: ' + route.route + ' — ' + route.reason));
      for (const assumption of fields.assumptions) suggestion.append(element('p', 'Uncertainty or risk: ' + assumption));
      suggestion.append(element('p', 'Next slice: ' + fields.next_slice));
      for (const learning of fields.learning) suggestion.append(element('p', 'Learning: ' + learning));
      if (fields.investment) suggestion.append(element('p', 'Investment: ' + fields.investment.cap + ' ' + fields.investment.unit + ' — ' + fields.investment.boundary));
      if (fields.experiment) suggestion.append(element('p', 'Experiment: ' + fields.experiment.question + ' — success: ' + fields.experiment.success_criterion + '; stop: ' + fields.experiment.stop_rule));
      (fields.sketch ?? []).forEach((entry, index) => suggestion.append(element('p', 'Sketch ' + (index + 1) + ': ' + entry.title + ' — done when: ' + entry.done_when)));
    }
    if (!item.acceptance_eligible) suggestion.append(element('p', REASONS[item.acceptance_reason] ?? 'This suggestion is unavailable for acceptance.'));
    suggestion.append(element('p', 'Saved suggestion: ' + item.evidence.path));
    const use = button('Use suggestion', handle(() => flow.useProposal(KEY, item.proposal_id)));
    use.id = 'exploration-use-proposal-' + item.proposal_id;
    use.disabled = blocked() || flow.paused || flow.state?.agent_status !== 'connected' || !item.acceptance_eligible || item.content_omitted;
    suggestion.append(use); assistance.append(suggestion);
  }
  body.append(assistance); proposalInventory(body, KEY);
  const selected = flow.selectedProposals.exploration;
  if (selected) {
    body.append(element('p', 'Your edited Exploration is linked to the suggestion you chose. Accept your answers explicitly.', 'notice'));
    const manual = button('Use my answers without linking a suggestion', handle(() => flow.clearProposal(KEY)));
    manual.id = 'exploration-manual'; manual.disabled = blocked(); body.append(manual);
  }
  const eligible = !selected || flow.proposals(KEY).some(item => item.proposal_id === selected && item.acceptance_eligible && !item.content_omitted);
  const valid = validExploration(current(), method) && !anyLocked;
  const accept = button(flow.busy ? 'Saving…' : archived ? 'Reactivate idea and accept Exploration' : 'Accept and continue', handle(() => {flow.edit(KEY, current()); return flow.save(KEY);}), 'primary');
  accept.id = 'exploration-accept';
  accept.disabled = blocked() || flow.busy || flow.paused || !valid || !eligible;
  if (accept.disabled) {
    const reason = disabledAcceptReason(KEY, current(), flow, {connected, eligible});
    const why = element('p', reason, 'foot-message accept-reason'); why.id = 'exploration-accept-reason';
    accept.setAttribute('aria-describedby', why.id); accept.title = reason; foot.append(accept, why);
  } else foot.append(accept);
  if (selected && !eligible) {
    // The human path stays one explicit click away when a used suggestion stops being acceptable.
    const own = button('Accept as my own answers', handle(() => {
      if (!flow.clearProposal(KEY)) return false;
      flow.edit(KEY, current()); return flow.save(KEY);
    }));
    own.id = 'exploration-accept-own'; own.disabled = blocked() || flow.busy || flow.paused || !valid;
    const why = element('p', 'The suggestion you used can no longer be accepted. Your edited answers are kept: accept them as your own, which unlinks the suggestion.', 'foot-message'); why.id = 'exploration-accept-own-reason';
    foot.append(own, why);
  }
}
