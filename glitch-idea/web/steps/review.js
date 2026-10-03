// Derived packet Review; copying never accepts, plans or archives.
const copies = new WeakMap();
const identity = packet => packet && JSON.stringify([packet.handoff_id, packet.sha256,
  packet.source_digest, packet.source_revision, packet.path]);
function local(flow) {
  const idea = flow.state?.idea_id, packet = identity(flow.state?.handoff);
  let value = copies.get(flow);
  if (!value || value.idea !== idea || value.packet !== packet) {
    value = {idea, packet, copying: false, manual: null, message: ''}; copies.set(flow, value);
  }
  return value;
}
const connected = ctx => (ctx.isConnected ? ctx.isConnected() : ctx.connected) &&
  !ctx.flow.refreshUnauthorized && typeof ctx.flow.api.csrf === 'string' && Boolean(ctx.flow.api.csrf);
const current = (ctx, idea, packet) => connected(ctx) && !ctx.flow.disposed &&
  ctx.flow.state?.idea_id === idea && ctx.flow.canCopyHandoff() &&
  identity(ctx.flow.state.handoff) === identity(packet);

// Shared by Review and an explicitly opened Ideas row. Every asynchronous door
// rechecks the pinned packet and local guards; there is no handoff POST here.
export async function copyCurrent(ctx, packet = ctx.flow.state?.handoff, manual = false) {
  const {flow} = ctx, idea = flow.state?.idea_id, value = local(flow);
  if (value.copying || !current(ctx, idea, packet) || (manual && identity(value.manual) !== identity(packet))) return false;
  value.copying = true; value.message = ''; flow.onChange();
  try {
    const verified = await flow.currentCopy(packet);
    if (!verified || !current(ctx, idea, verified)) return false;
    if (!manual) {
      try {
        const clipboard = ctx.clipboard ?? globalThis.navigator?.clipboard;
        if (!clipboard?.writeText) throw new Error('clipboard_unavailable');
        await clipboard.writeText(verified.prompt);
      } catch {
        if (!current(ctx, idea, verified)) return false;
        value.manual = verified;
        value.message = 'Clipboard unavailable. Select and copy the full prompt, then explicitly confirm below.';
        return false;
      }
      if (!current(ctx, idea, verified)) return false;
    }
    const checked = await flow.currentCopy(verified);
    if (!checked || !current(ctx, idea, checked)) return false;
    const completed = manual ? 'Copy confirmed.' : 'Prompt copied.';
    value.manual = null; value.message = completed; flow.message = completed;
    flow.onChange();
    // Read Ideas before switching view so an edit while this read is pending
    // cannot briefly return to Ideas as if copying had completed successfully.
    const listed = await flow.loadIdeas();
    if (!current(ctx, idea, checked)) return false;
    if (!listed) {
      const code = flow.ideasError?.code ?? (flow.ideasLoading ? 'ideas_loading' : 'ideas_unavailable');
      value.message = completed + ' The Ideas list could not be read: ' + code + '. Use Ideas to reload the list explicitly.';
      flow.message = value.message;
      return false;
    }
    const final = await flow.currentCopy(checked);
    if (!final || !current(ctx, idea, final)) return false;
    value.message = completed;
    flow.view = 'ideas'; flow.message = value.message; flow.onChange();
    return true;
  } finally {
    value.copying = false;
    if (!flow.disposed && flow.state?.idea_id === idea && local(flow) === value) flow.onChange();
  }
}

// Review summary: one card per step, the decision first, then compact labelled details.
// All text goes in through textContent via ctx.element; nothing here parses markup.
// The method names are plain text on purpose: this module is loaded on its own in tests, so it imports nothing.
const METHOD_NAMES = {'bounded-plan': 'Bounded plan (APIV)', 'adaptive-slices': 'Adaptive vertical slices',
  'appetite-led': 'Appetite-led shaping', 'experiment-led': 'Experiment-led discovery'};
const SCOPE_NAMES = {'small-change': 'Small change', capability: 'New capability', project: 'Project', epic: 'Epic'};
const MEMORY_NAMES = {found: 'Found in memory', searched_no_preference: 'Searched, no preference found',
  unavailable: 'Memory was not available', error: 'Memory could not be read'};
const CARDS = [['capture', 'Capture'], ['priorities', 'Your priorities'], ['shape', 'Shape the outcome'],
  ['method', 'Choose a methodology'], ['visualize', 'Visualize'], ['assess', 'Assess and position']];
const str = value => typeof value === 'string' && value.trim() ? value : '';
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const list = value => Array.isArray(value) ? value.map(str).filter(Boolean) : [];
const human = key => { const words = String(key).replaceAll('_', ' '); return words.charAt(0).toUpperCase() + words.slice(1); };
const number = value => typeof value === 'number' && Number.isFinite(value) ? String(Math.round(value * 100) / 100) : 'Unknown';
function shorten(value, limit = 200) {
  const letters = Array.from(value);
  return letters.length <= limit ? value : letters.slice(0, limit).join('').trimEnd() + '…';
}
// Display only: the server calculates the saved score. Mirrors the Assess step's readout.
function scoreWords(assessment) {
  if (!isObject(assessment)) return 'Unknown (missing inputs)';
  const i = isObject(assessment.inputs) ? assessment.inputs : {}, name = String(assessment.method ?? '').toUpperCase();
  if (assessment.method === 'kano') return 'Kano: ' + (str(i.category) ? human(i.category) : 'no category yet') + ' (no numeric score)';
  const keys = assessment.method === 'wsjf' ? ['value', 'time_criticality', 'enablement', 'effort'] :
    assessment.method === 'rice' ? ['reach', 'impact', 'confidence', 'effort'] : null;
  if (!keys) return 'Unknown (missing inputs)';
  if (keys.some(key => typeof i[key] !== 'number' || !Number.isFinite(i[key]))) return name + ' Unknown (missing inputs)';
  const score = assessment.method === 'wsjf' ? (i.value + i.time_criticality + i.enablement) / i.effort : i.reach * i.impact * i.confidence / i.effort;
  return Number.isFinite(score) ? name + ' ' + number(score) : name + ' Unknown (check the values)';
}
function summaryCards(ctx, accepted) {
  const {element} = ctx, cards = [];
  for (const [key, title] of CARDS) {
    const fields = accepted[key], card = element('section', '', 'review-card'); card.dataset.step = key;
    card.append(element('h3', title));
    const rows = [];   // [label, string | string[]]
    const row = (label, value) => { if (Array.isArray(value) ? value.length : value) rows.push([label, value]); };
    let decision = '', more = null;
    if (!isObject(fields)) decision = 'Not answered yet.';
    else if (key === 'capture') {
      const text = str(fields.raw_text); decision = text ? shorten(text) : 'No idea text saved.';
      if (text && decision !== text) { more = element('details', '', 'review-full'); more.append(element('summary', 'Show the full text'), element('p', text)); }
      if (isObject(fields.workspace)) { row('Workspace', str(fields.workspace.name)); row('Path', str(fields.workspace.path)); }
    } else if (key === 'priorities') {
      decision = 'Urgency ' + number(fields.urgency) + ' · Importance ' + number(fields.importance) + ' (your ratings)';
    } else if (key === 'shape') {
      decision = (str(fields.outcome) || 'No desired result saved.') + (SCOPE_NAMES[fields.scope] ? ' Scope: ' + SCOPE_NAMES[fields.scope] + '.' : '');
      row('Why this scope', str(fields.scope_reason));
      row('Alternatives considered', Array.isArray(fields.alternatives) ? fields.alternatives.filter(isObject)
        .map(a => [str(a.route), str(a.reason)].filter(Boolean).join(' — ')).filter(Boolean) : []);
      row('Assumptions and risks', list(fields.assumptions)); row('Next slice', str(fields.next_slice)); row('What we expect to learn', list(fields.learning));
    } else if (key === 'method') {
      const name = METHOD_NAMES[fields.selection] ?? 'No method chosen';
      decision = str(fields.reason) ? name + ' — ' + fields.reason : name;
      const inv = isObject(fields.investment) ? fields.investment : null;
      if (inv) { row('Investment', [typeof inv.cap === 'number' ? number(inv.cap) : '', str(inv.unit)].filter(Boolean).join(' ')); row('Boundary', str(inv.boundary)); }
      const exp = isObject(fields.experiment) ? fields.experiment : null;
      if (exp) { row('Experiment question', str(exp.question)); row('Evidence we will look for', str(exp.evidence));
        row('Success looks like', str(exp.success_criterion)); row('Stop rule', str(exp.stop_rule)); }
      if (isObject(fields.memory)) { row('Memory check', MEMORY_NAMES[fields.memory.status] ?? ''); row('Memory sources', list(fields.memory.sources)); row('Memory rationale', str(fields.memory.rationale)); }
    } else if (key === 'visualize') {
      const reason = str(fields.reason);
      if (str(fields.design_set_id)) row('Design set', str(fields.design_set_id));
      decision = fields.disposition === 'accepted_set' ? 'Design set accepted.' + (reason ? ' ' + reason : '') :
        fields.disposition === 'skipped' ? 'Skipped' + (reason ? ' — ' + reason : '') :
        fields.disposition === 'not-applicable' ? 'Not applicable' + (reason ? ' — ' + reason : '') : 'No decision saved.';
    } else if (key === 'assess') {
      const pos = isObject(fields.position) ? fields.position : {}, a = isObject(fields.assessment) ? fields.assessment : null;
      decision = (Number.isInteger(pos.actual_position) ? 'Position ' + pos.actual_position + ' in your backlog' : 'No position saved') + ' · ' + scoreWords(a);
      if (Number.isInteger(pos.proposed_position) && pos.proposed_position !== pos.actual_position) row('Proposed position', 'Position ' + pos.proposed_position);
      row('Override reason', str(pos.override_reason));
      if (a) {
        row('Confidence', str(a.confidence) ? human(a.confidence) : ''); row('Assumptions', list(a.assumptions));
        row('Assessment version', str(a.version));
        if (a.method === 'kano' && isObject(a.inputs) && typeof a.inputs.hypothesis === 'boolean')
          row('Hypothesis', a.inputs.hypothesis ? 'Yes — a hypothesis, not customer research' : 'No — backed by evidence');
        const lines = value => typeof value === 'string' ? str(value) : isObject(value) ? Object.entries(value).map(([k, v]) => human(k) + ': ' + String(v)) : '';
        row('Assessment basis', lines(a.basis)); row('Provenance', lines(a.provenance));
        if (isObject(a.inputs) && a.method !== 'kano') row('Inputs', Object.entries(a.inputs).map(([k, v]) => human(k) + ': ' + (v === null ? 'Unknown' : number(v))));
      }
    }
    card.append(element('p', decision, 'review-decision'));
    if (more) card.append(more);
    if (rows.length) {
      const dl = element('dl', '', 'review-details');
      for (const [label, value] of rows) {
        const dd = element('dd');
        if (Array.isArray(value)) { const ul = element('ul'); for (const item of value) ul.append(element('li', item)); dd.append(ul); }
        else dd.textContent = value;
        dl.append(element('dt', label), dd);
      }
      card.append(dl);
    }
    cards.push(card);
  }
  return cards;
}

export function render(ctx) {
  const {body, foot, flow, element, button, handle} = ctx, value = local(flow);
  const accepted = flow.state?.accepted ?? {}, packet = flow.state?.handoff;
  const summary = element('section'); summary.id = 'review-summary';
  summary.append(element('h2', 'Saved idea summary'), ...summaryCards(ctx, accepted));
  body.append(summary);
  const generate = button(flow.busy ? 'Preparing…' : 'Generate planning prompt', handle(() => {
    if (!connected(ctx) || value.copying || !flow.canGenerateHandoff()) return false;
    return flow.generateHandoff();
  }), 'primary');
  generate.id = 'review-generate'; generate.disabled = !connected(ctx) || !flow.canGenerateHandoff() || value.copying;
  foot.append(generate);
  if (!packet) body.append(element('p', 'Accept the required steps, then explicitly generate a planning prompt.', 'notice'));
  else {
    const status = element('p', flow.canCopyHandoff() ? 'Verified current planning packet.' :
      'This saved packet is historical or its source needs review. Copy is unavailable; review the steps and explicitly generate a current prompt.', 'notice');
    status.id = 'review-packet-status'; status.setAttribute('role', 'status'); body.append(status);
    body.append(element('p', 'Packet: ' + packet.handoff_id + ' · source revision ' + packet.source_revision),
      element('p', 'Immutable packet: ' + packet.path), element('p', 'Packet SHA-256: ' + packet.sha256));
    for (const [name, file] of Object.entries(packet.source_files)) body.append(element('p', name + ': ' + file.path + ' · SHA-256 ' + file.sha256));
    if (packet.design_set) {
      body.append(element('p', 'Accepted design set: ' + packet.design_set.set_id));
      for (const member of packet.design_set.members) body.append(element('p', member.name + ' · ' + member.path + ' · SHA-256 ' + member.sha256));
    } else body.append(element('p', 'No accepted design assets.'));
    const label = element('label', 'Full planning prompt'); label.htmlFor = 'review-prompt';
    const prompt = element('textarea'); prompt.id = 'review-prompt'; prompt.value = packet.prompt; prompt.readOnly = true;
    body.append(label, prompt, element('p', 'Paste this into a NEW window or pane. Copying does not start planning or archive your idea.', 'help'));
    const copy = button('Copy planning prompt', handle(() => copyCurrent(ctx, packet)), 'primary');
    copy.id = 'review-copy'; copy.disabled = !connected(ctx) || !flow.canCopyHandoff() || value.copying; foot.append(copy);
    if (identity(value.manual) === identity(packet)) {
      const confirm = button('I copied the full prompt', handle(() => copyCurrent(ctx, packet, true)));
      confirm.id = 'review-manual-copy'; confirm.disabled = !connected(ctx) || !flow.canCopyHandoff() || value.copying; foot.append(confirm);
    }
  }
  if (value.message) { const message = element('p', value.message, 'notice'); message.id = 'review-copy-status'; message.setAttribute('role', 'status'); body.append(message); }
}
