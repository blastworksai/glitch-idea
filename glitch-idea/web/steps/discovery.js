// Discovery step: five answers, what already exists, and at least one challenge. A connected terminal fills them; the person can take the step by hand.
import {validDiscovery, conversationStatus, filledNote, disabledAcceptReason, waitingLine, FILL_KEYS, MAX_PRIOR_ART} from '../folds.js';

const ANSWERS = [['problem', 'What is the problem?', 'discovery-problem'], ['audience', 'Who has it?', 'discovery-audience'],
  ['workaround', 'How do they handle it today?', 'discovery-workaround'], ['evidence', 'What evidence says it is needed?', 'discovery-evidence'],
  ['kill_criteria', 'What would make you stop?', 'discovery-kill-criteria']];
const TERMINAL_ASKING = 'Your terminal is asking the big questions';
const TERMINAL_ASKING_MORE = ' Answers appear here as you settle them there. Edit any of them before you accept.';
const NO_TERMINAL = 'No terminal is connected, so this step is yours to fill.';
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
    const notice = element('p', 'This idea is archived. To accept new answers, first explicitly redo and accept Exploration to reactivate it. Archived history is kept; your draft remains here.', 'notice');
    notice.id = 'discovery-reactivation-notice'; notice.setAttribute('role', 'status'); body.append(notice);
  }
  const current = () => {
    const buffer = flow.buffers.discovery ?? {};
    // An answer accepted before CP6p has no prior-art fields: they read as empty here.
    return {problem: '', audience: '', workaround: '', evidence: '', kill_criteria: '', ...buffer, challenges: buffer.challenges ?? [],
      prior_art: buffer.prior_art ?? [], prior_art_none: buffer.prior_art_none === true, prior_art_searched: buffer.prior_art_searched ?? ''};
  };
  const agent = flow.state?.agent_status === 'connected';
  const released = flow.handReleased('discovery');
  const blocked = () => !connected || flow.inputLocked() || (Boolean(flow.pending) && !flow.draftFlight);
  const locked = name => flow.fieldLocked('discovery', name);
  const anyLocked = FILL_KEYS.discovery.some(locked);
  const change = (key, value) => edited('discovery', {...current(), [key]: value});
  const mark = (name, id, target, input) => filledNote({flow, key: 'discovery', name, id, element, target, input});

  let line;
  if (!agent) line = NO_TERMINAL;
  else if (released) line = 'You took this step by hand. Your terminal will not fill it.';
  else {
    const asking = conversationStatus(flow, 'discovery');
    line = asking.startsWith('Asking') ? asking : TERMINAL_ASKING;
  }
  // The terminal notice leads the step; the hand-fill button sits inside it when a field is still waiting.
  const lead = element('div', '', 'bw-notice');
  const talk = element('div', line); talk.id = 'discovery-conversation-status'; talk.setAttribute('role', 'status');
  const words = element('div'); words.append(talk);
  if (line === TERMINAL_ASKING) words.append(element('div', TERMINAL_ASKING_MORE.trim(), 'help'));
  lead.append(words); body.append(lead);

  // A locked input sits in .field-locked with a waiting note; the helper appends into whatever target it is given.
  const lockNote = target => target.append(element('p', 'Waiting for your terminal', 'lock-note'));
  // The note sits directly under the field's own label, between label and input.
  const noteUnder = (target, input) => {
    const note = element('p', 'Waiting for your terminal', 'lock-note'); note.id = input.id + '-lock';
    input.setAttribute?.('aria-describedby', note.id);
    if (input.parentNode?.insertBefore) input.parentNode.insertBefore(note, input); else target.append(note);
  };
  // "Does it already exist?": comparable products, each with a source link and licence, or an honest "none found".
  function priorArt() {
    const sectionLocked = locked('prior_art');
    const rows = current().prior_art, none = current().prior_art_none;
    const section = element('fieldset'); section.id = 'discovery-prior-art';
    section.append(element('legend', 'Does it already exist?'));
    section.append(element('p', 'Products or projects that already do something like this, how we differ from each, and the licence each one holds.', 'help'));
    const list = element('ol', '', 'challenge-list prior-art-list');
    const updateRow = (index, key, value) => change('prior_art', rows.map((entry, offset) => offset === index ? {...entry, [key]: value} : entry));
    rows.forEach((item, index) => {
      const row = element('li', '', 'challenge-row prior-art-row');
      for (const [key, label, area] of [['name', 'Name', false], ['link', 'Link', false], ['does', 'What it does', true],
        ['differs', 'How we differ', true], ['licence', 'Licence', false]]) {
        const input = field(row, label, 'discovery-prior-art-' + index + '-' + key, item?.[key], value => updateRow(index, key, value), area);
        if (sectionLocked) { input.disabled = true; input.readOnly = true; }
      }
      const remove = button('Remove', () => change('prior_art', rows.filter((_, offset) => offset !== index)), 'bw-btn--ghost');
      remove.setAttribute('aria-label', 'Remove product ' + (index + 1));
      remove.id = 'discovery-remove-prior-art-' + index; remove.disabled = blocked() || sectionLocked;
      row.append(remove); list.append(row);
    });
    section.append(list);
    const add = button('Add a product', () => change('prior_art', [...rows, {name: '', link: '', does: '', differs: '', licence: ''}]));
    add.id = 'discovery-add-prior-art';
    add.disabled = blocked() || sectionLocked || none || rows.length >= MAX_PRIOR_ART;
    section.append(add);
    if (none) section.append(element('p', 'Untick Nothing comparable found to add a product.', 'help'));
    else if (rows.length >= MAX_PRIOR_ART) section.append(element('p', 'Eight products is the limit. Remove one to add another.', 'help'));
    const label = element('label', '', 'check-label g-check');
    const check = element('input'); check.type = 'checkbox'; check.id = 'discovery-prior-art-none'; check.checked = none;
    check.disabled = blocked() || sectionLocked || rows.length > 0;
    check.addEventListener('change', event => change('prior_art_none', event.target.checked));
    label.append(check, element('span', 'Nothing comparable found'));
    section.append(label);
    if (rows.length > 0) {
      const why = element('p', 'Remove the products above to tick this.', 'help'); why.id = 'discovery-prior-art-none-reason';
      check.setAttribute('aria-describedby', why.id); check.title = why.textContent; section.append(why);
    }
    if (none) {
      const input = field(section, 'Where did you look?', 'discovery-prior-art-searched', current().prior_art_searched,
        value => change('prior_art_searched', value), true);
      if (sectionLocked) { input.disabled = true; input.readOnly = true; }
    }
    if (sectionLocked) {
      const holder = element('div', '', 'field-locked'); lockNote(holder); holder.append(section); body.append(holder);
    } else {
      body.append(section);
      mark('prior_art', 'discovery-prior-art', section);
      mark('prior_art_none', 'discovery-prior-art-none', section);
      mark('prior_art_searched', 'discovery-prior-art-searched', section);
    }
  }
  let cols = element('div', '', 'g-cols'); body.append(cols);
  for (const [key, label, id] of ANSWERS) {
    const isLocked = locked(key);
    const target = isLocked ? element('div', '', 'field-locked') : cols;
    const input = field(target, label, id, current()[key], value => change(key, value), true);
    input.required = true;
    if (isLocked) { input.disabled = true; input.readOnly = true; noteUnder(target, input); cols.append(target); }
    else mark(key, id, cols, input);
    if (key === 'workaround') { priorArt(); cols = element('div', '', 'g-cols'); body.append(cols); }
  }

  const groupLocked = locked('challenges');
  const challenges = element('fieldset');
  challenges.append(element('legend', 'Challenges'));
  challenges.append(element('p', 'At least one real challenge to how you framed the idea, with your answer to it.', 'help'));
  const rows = element('ol', '', 'challenge-list');
  const updateRow = (index, key, value) => change('challenges', current().challenges.map((entry, offset) => offset === index ? {...entry, [key]: value} : entry));
  current().challenges.forEach((item, index) => {
    const row = element('li', '', 'challenge-row');
    for (const [key, label] of [['challenge', 'Challenge'], ['response', 'Your response']]) {
      const id = 'discovery-challenge-' + index + '-' + key;
      const input = field(row, label, id, item?.[key], value => updateRow(index, key, value), true);
      if (groupLocked) { input.disabled = true; input.readOnly = true; }
    }
    const remove = button('Remove', () => change('challenges', current().challenges.filter((_, offset) => offset !== index)), 'bw-btn--ghost');
    remove.setAttribute('aria-label', 'Remove challenge ' + (index + 1));
    remove.id = 'discovery-remove-challenge-' + index; remove.disabled = blocked() || groupLocked;
    row.append(remove); rows.append(row);
  });
  challenges.append(rows);
  const add = button('Add challenge', () => change('challenges', [...current().challenges, {challenge: '', response: ''}]));
  add.id = 'discovery-add-challenge'; add.disabled = blocked() || groupLocked || current().challenges.length >= 1000;
  challenges.append(add);
  if (groupLocked) {
    const holder = element('div', '', 'field-locked'); lockNote(holder); holder.append(challenges); body.append(holder);
  } else { body.append(challenges); mark('challenges', 'discovery-challenges', challenges); }

  if (anyLocked) {
    const hand = button('Fill this step by hand', handle(() => flow.releaseStep('discovery')), 'hand-fill');
    hand.id = 'discovery-hand-fill'; hand.disabled = blocked() || flow.paused; lead.append(hand);
  }

  const assistance = element('section'); assistance.setAttribute('aria-label', 'Discovery suggestions');
  const pending = flow.proposalPending?.key === 'discovery' ? flow.proposalPending : null;
  if (pending) {
    const status = element('p', pending.phase === 'sending' ? 'Sending your suggestion request…' : pending.phase === 'waiting' ?
      waitingLine(flow, 'discovery') : pending.ambiguous ?
        'The request may have reached the agent. Retry the same request to check it.' : 'The suggestion request failed. Your answers remain.');
    status.id = 'discovery-proposal-status'; status.setAttribute('role', 'status'); assistance.append(status);
    if (pending.phase === 'failed') {
      const retry = button('Retry same request', handle(() => flow.retryProposal())); retry.id = 'discovery-retry'; retry.disabled = blocked(); assistance.append(retry);
    }
    const stop = button('Stop waiting', handle(() => {flow.cancelProposal('user_cancelled'); flow.onChange();}));
    stop.id = 'discovery-stop-waiting'; stop.disabled = blocked(); assistance.append(stop);
  }
  if (flow.proposalError) {
    const error = element('p', REASONS[flow.proposalError.code] ?? 'AI assistance could not complete this request. Your answers remain.', 'notice');
    error.id = 'discovery-proposal-error'; error.setAttribute('role', 'status'); assistance.append(error);
  }
  for (const item of flow.proposals('discovery')) {
    const suggestion = element('section', '', 'notice');
    suggestion.id = 'discovery-proposal-' + item.proposal_id;
    suggestion.append(element('h2', 'Proposed Discovery'));
    if (item.proposal !== null) {
      const fields = item.proposal;
      for (const [key, label] of ANSWERS) suggestion.append(element('p', label + ' ' + fields[key]));
      for (const c of fields.challenges ?? []) suggestion.append(element('p', 'Challenge: ' + c.challenge + ' — ' + c.response));
      if (fields.prior_art_none === true) suggestion.append(element('p', 'Nothing comparable found (looked: ' + (fields.prior_art_searched ?? '') + ')'));
      for (const r of fields.prior_art ?? []) suggestion.append(element('p', 'Already exists: ' + r.name + ' — ' + [r.differs && 'we differ: ' + r.differs, r.licence && 'licence: ' + r.licence].filter(Boolean).join(' · ')));
    }
    if (!item.acceptance_eligible) suggestion.append(element('p', REASONS[item.acceptance_reason] ?? 'This suggestion is unavailable for acceptance.'));
    suggestion.append(element('p', 'Saved suggestion: ' + item.evidence.path));
    const use = button('Use suggestion', handle(() => flow.useProposal('discovery', item.proposal_id)));
    use.id = 'discovery-use-proposal-' + item.proposal_id;
    use.disabled = blocked() || flow.paused || !agent || !item.acceptance_eligible || item.content_omitted;
    suggestion.append(use); assistance.append(suggestion);
  }
  body.append(assistance); proposalInventory(body, 'discovery');

  const selected = flow.selectedProposals.discovery;
  if (selected) {
    body.append(element('p', 'Your edited Discovery is linked to the suggestion you chose. Accept your answers explicitly.', 'notice'));
    const manual = button('Use my answers without linking a suggestion', handle(() => flow.clearProposal('discovery')));
    manual.id = 'discovery-manual'; manual.disabled = blocked(); body.append(manual);
  }
  const eligible = !selected || flow.proposals('discovery').some(item => item.proposal_id === selected && item.acceptance_eligible && !item.content_omitted);
  const accept = button(flow.busy ? 'Saving…' : 'Accept and continue',
    handle(() => {flow.edit('discovery', current()); return flow.save('discovery');}), 'primary');
  accept.id = 'discovery-accept';
  const valid = validDiscovery(current()) && !anyLocked;
  accept.disabled = blocked() || flow.busy || flow.paused || !valid || !eligible || archived;
  const reason = accept.disabled ? disabledAcceptReason('discovery', current(), flow, {connected, eligible}) : '';
  if (reason) {
    const why = element('p', reason, 'foot-message accept-reason'); why.id = 'discovery-accept-reason';
    accept.setAttribute('aria-describedby', why.id); accept.title = reason; foot.append(accept, why);
  } else foot.append(accept);
  if (selected && !eligible) {
    // The human path stays one explicit click away when a used suggestion stops being acceptable.
    const own = button('Accept as my own answers', handle(() => {
      if (!flow.clearProposal('discovery')) return false;
      flow.edit('discovery', current()); return flow.save('discovery');
    }));
    own.id = 'discovery-accept-own'; own.disabled = blocked() || flow.busy || flow.paused || !valid;
    const why = element('p', 'The suggestion you used can no longer be accepted. Your edited answers are kept: accept them as your own, which unlinks the suggestion.', 'foot-message'); why.id = 'discovery-accept-own-reason';
    foot.append(own, why);
  }
}
