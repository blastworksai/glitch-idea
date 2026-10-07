// Original UI; server evidence alone authorizes saved completion.
import {IdeaApi, ApiError} from './api.js';
import {Flow, STEPS, statusLabel, statusGlyph, validCapture, validPriorities, acceptBlockers} from './folds.js';

// Literal packaged module registry. Only a missing HTTP404 means unavailable.
const OPTIONAL_MODULES = Object.freeze([
  ['discovery', './steps/discovery.js'], ['exploration', './steps/exploration.js'], ['method', './steps/method.js'],
  ['visualize', './steps/visualize.js'], ['assess', './steps/assess.js'],
  ['review', './steps/review.js'], ['ideas', './ideas.js'],
]);
export async function loadStepModules(fetcher = globalThis.fetch.bind(globalThis), importer = url => import(url)) {
  captureUploads = null;
  ideasRenderer = null;
  for (const [key, url] of OPTIONAL_MODULES) {
    const response = await fetcher(url, {credentials: 'same-origin', cache: 'no-store', redirect: 'error'});
    if (response.status === 404) continue;
    if (!response.ok) throw new Error('Cannot load packaged step: ' + key);
    const module = await importer(url);
    if (typeof module.render !== 'function') throw new Error('Invalid packaged step render: ' + key);
    if (key === 'ideas') registerIdeas(module.render);
    else registerStep(key, module.render);
    if (key === 'visualize') registerCaptureUploads(module.renderUploads);
  }
}

export function browserSelection(location = globalThis.location) {
  const params = new URLSearchParams(location.search);
  for (const key of params.keys()) if (!['binding', 'idea_id'].includes(key)) throw new ApiError('invalid_query');
  const selected = {};
  for (const [key, expression] of [['binding', /^binding_[0-9a-f]{32}$/], ['idea_id', /^idea_[0-9a-f]{32}$/]]) {
    const values = params.getAll(key);
    if (values.length > 1 || (values.length && !expression.test(values[0]))) throw new ApiError(key === 'binding' ? 'invalid_binding' : 'invalid_idea_id');
    selected[key] = values[0] ?? null;
  }
  return selected;
}
export const selectedIdea = location => browserSelection(location).idea_id;
export const selectedBinding = location => browserSelection(location).binding;

export function rememberSelection(binding, ideaId, location = globalThis.location, history = globalThis.history) {
  const url = new URL(location.href);
  url.search = '';
  if (binding !== null) url.searchParams.set('binding', binding);
  if (ideaId !== null) url.searchParams.set('idea_id', ideaId);
  browserSelection(url); // Reject malformed values before changing browser history.
  history.replaceState(null, '', url.pathname + url.search);
}

const components = new Map();
let captureUploads = null;
let ideasRenderer = null;
// The Setup pane loads on first use, so the packaged-step list above stays the seven steps plus Ideas.
let setupRenderer = null;
// The launcher opens the tab at /#pair=<code>. The code is removed from the address
// before it is redeemed, and is never stored. Any "#pair=" fragment is stripped,
// well-formed or not; only a well-formed one is redeemed.
export async function pairFromFragment({location = globalThis.location, history = globalThis.history, api} = {}) {
  const hash = location.hash ?? '';
  if (!hash.startsWith('#pair=')) return {attempted: false};
  const match = /^#pair=([0-9a-f]{32})$/.exec(hash);
  history.replaceState(null, '', location.pathname + location.search);
  if (!match) return {attempted: false};
  try { return {attempted: true, session: await api.pair(match[1])}; }
  catch (error) { return {attempted: true, error}; }
}

export function registerSetup(renderer) {
  if (typeof renderer !== 'function') throw new Error('Invalid packaged Setup renderer');
  setupRenderer = renderer;
}
export function registerIdeas(renderer) {
  if (typeof renderer !== 'function') throw new Error('Invalid packaged Ideas renderer');
  ideasRenderer = renderer;
}
export function registerStep(key, render) {
  if (!STEPS.some(step => step.key === key) || typeof render !== 'function') throw new Error('Invalid step component');
  components.set(key, render);
}
// Trusted literal packaged-module composition, also exposed to fixed app tests.
// Never selected from browser fields, URLs, stored metadata or agent replies.
export function registerCaptureUploads(renderer) {
  if (typeof renderer !== 'function') throw new Error('Invalid packaged upload renderer');
  captureUploads = renderer;
}

const element = (tag, text = '', className = '') => {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
};
const button = (text, action, className = '') => {
  const node = element('button', text, className);
  node.type = 'button';
  node.addEventListener('click', action);
  return node;
};

// Theme: dark is the default; light is the root attribute data-theme="light". The only thing ever
// stored is this one word under one key, and the page works with no storage at all.
const THEME_KEY = 'glitch-idea-theme';
export function readTheme() {
  try { return globalThis.localStorage?.getItem(THEME_KEY) === 'light' ? 'light' : 'dark'; } catch { return 'dark'; }
}
export function applyTheme(theme) {
  const root = globalThis.document?.documentElement;
  if (!root) return;
  if (!root.dataset) root.dataset = {};
  if (theme === 'light') root.dataset.theme = 'light'; else delete root.dataset.theme;
}
function rememberTheme(theme) {
  try { globalThis.localStorage?.setItem(THEME_KEY, theme); } catch { /* storage is optional */ }
}

// Status glyphs for the step rail, drawn as in the design: a ring, an arc, a dot, bars or a bang.
const SVG_NS = 'http://www.w3.org/2000/svg';
function svgNode(tag, attributes = {}) {
  const node = typeof document.createElementNS === 'function' ? document.createElementNS(SVG_NS, tag) : document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, String(value));
  return node;
}
const ring = () => svgNode('circle', {class: 'trk', cx: 7, cy: 7, r: 5.5});
function statusMark(status) {
  const modifier = {saved: ' bw-status--closed', todo: ' bw-status--queued', skipped: ' bw-status--held',
    'review-needed': ' bw-status--attention'}[status] ?? '';
  const mark = element('span', '', 'bw-status' + modifier);
  const svg = svgNode('svg', {viewBox: '0 0 14 14', 'aria-hidden': 'true'});
  if (status === 'saved') svg.append(svgNode('circle', {class: 'dot', cx: 7, cy: 7, r: 6.5}));
  else if (status === 'todo') svg.append(ring());
  else if (status === 'skipped') svg.append(ring(), svgNode('rect', {class: 'bar', x: 5, y: 4.5, width: 1.5, height: 5, rx: .5}),
    svgNode('rect', {class: 'bar', x: 7.5, y: 4.5, width: 1.5, height: 5, rx: .5}));
  else if (status === 'review-needed') svg.append(ring(), svgNode('rect', {class: 'bang', x: 6.25, y: 3.6, width: 1.5, height: 4.4, rx: .5}),
    svgNode('rect', {class: 'bang', x: 6.25, y: 9, width: 1.5, height: 1.5, rx: .5}));
  else svg.append(ring(), svgNode('circle', {class: 'arc', cx: 7, cy: 7, r: 5.5, 'stroke-dasharray': '17.28 34.56', transform: 'rotate(-90 7 7)'}));
  svg.setAttribute('aria-hidden', 'true');
  mark.append(svg);
  return mark;
}
function lockMark() {
  const svg = svgNode('svg', {viewBox: '0 0 12 12', 'aria-hidden': 'true', class: 'g-step__lock'});
  svg.append(svgNode('rect', {x: 2, y: 5.5, width: 8, height: 5, rx: 1, fill: 'currentColor'}),
    svgNode('path', {d: 'M4 5.5V4a2 2 0 0 1 4 0v1.5', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.2}));
  return svg;
}
// The rail's word for a step's state. 'Unsaved' is the page's own extra word for edited, unsaved answers.
const railWord = status => ({saved: 'Saved', current: 'Current', todo: 'To do', skipped: 'Skipped', 'review-needed': 'Review', unsaved: 'Unsaved'}[status] ?? 'Unavailable');
// Subtitles drawn under the step title where the design has one.
const STEP_SUBTITLES = Object.freeze({
  capture: 'Say what you want to exist, in your own words, and which project it belongs to.',
  method: 'How you want to build this. All four run inside the same Align, Plan, Implement, Verify loop.',
  discovery: 'Five questions that test whether the idea is worth building, and the strongest challenges to how you framed it.',
  review: 'Every decision in one place. When it reads right, generate the planning prompt and take it to /glitch-plan.',
});
// Discovery and Exploration draw their own standing no-terminal line; the long sentence stays in the chip's title.
const OWN_NO_AGENT_NOTICE = Object.freeze(['discovery', 'exploration']);
const AGENT_STEPS = Object.freeze(['discovery', 'exploration', 'method', 'assess']);

// Shared optional-step helper. All file locations are text, never URLs or a
// generic browser file route. Modules call proposalInventory(body, stepKey).
export function renderProposalInventory(body, key, flow, makeElement = element) {
  const inventory = flow.state?.proposal_inventory;
  const sourceCode = flow.state?.proposal_sources?.[key]?.code;
  const oversized = ['source_too_large', 'source_projection_capacity'].includes(sourceCode);
  if (!inventory || (!inventory.omitted && !inventory.content_omitted && !oversized)) return null;
  const box = makeElement('div', '', 'notice');
  box.id = key + '-proposal-inventory';
  box.setAttribute('role', 'status');
  box.append(makeElement('p', 'Saved suggestions across all steps: ' + inventory.total + '. ' +
    inventory.projected + ' listed here; ' + inventory.omitted + ' records and ' + inventory.content_omitted +
    ' listed bodies omitted from this bounded browser view.'));
  if (oversized) box.append(makeElement('p', 'These saved inputs exceed the bounded AI request source. Your idea and drafts remain editable.'));
  if (inventory.index_path !== null) {
    const index = makeElement('p', 'All immutable suggestion links remain in Markdown: ' + inventory.index_path);
    index.id = key + '-proposal-index-path';
    box.append(index);
  }
  for (const item of flow.proposals(key).filter(proposal => proposal.content_omitted)) {
    const location = makeElement('p', 'Omitted suggestion content: ' + item.evidence.path);
    location.id = key + '-proposal-evidence-' + item.proposal_id;
    box.append(location);
  }
  body.append(box);
  return box;
}

export function startApp(api = new IdeaApi()) {
  const flow = new Flow(api);
  let connected = false;
  let session = null;
  let autosaveTimer = null;
  let renderedStep = null;
  let pendingFocus = null;
  const initialIdeaId = selectedIdea();
  const initialBinding = selectedBinding();
  if (initialBinding) api.pinBinding(initialBinding);
  const byId = id => document.getElementById(id);
  const announce = text => { byId('announcement').textContent = text; };
  const handle = action => async () => {
    try { await action(); }
    catch (error) { flow.error = error; flow.message = 'Could not complete the action. Your answers remain.'; render(); }
    if (flow.error?.status === 401) unauthorized(flow.error.code);
  };

  // Dark is the default; the toggle flips the root attribute and remembers the choice if storage allows.
  let theme = readTheme();
  applyTheme(theme);
  const themeButton = byId('theme-toggle');
  const paintTheme = () => {
    if (!themeButton) return;
    themeButton.textContent = theme === 'light' ? 'Dark theme' : 'Light theme';
    themeButton.setAttribute('aria-pressed', String(theme === 'light'));
  };
  paintTheme();
  themeButton?.addEventListener('click', () => {
    theme = theme === 'light' ? 'dark' : 'light';
    applyTheme(theme); rememberTheme(theme); paintTheme();
  });

  // The Idea tab returns to the step workflow from the Ideas or Setup page; it only switches the view.
  byId('nav-idea')?.addEventListener('click', handle(async () => { flow.showWorkflow(); }));
  // Persistent top-bar door to the backlog; the footer Ideas button stays as the in-panel door.
  byId('nav-ideas')?.addEventListener('click', handle(() => flow.showIdeas()));
  byId('nav-setup')?.addEventListener('click', handle(async () => {
    if (!setupRenderer) registerSetup((await import('./setup.js')).render);
    flow.showSetup();
  }));

  // Draft timing (owner ruling): a draft saves when the human leaves a field, or after
  // this much quiet. Never per keystroke. Explicit Save/Accept/Pause are unchanged.
  const AUTOSAVE_PAUSE_MS = 3000;
  // Leaving a field saves after this short settle, so a click straight into the next field
  // and typing there restarts the pause instead of racing a save that locks the form.
  const BLUR_SAVE_MS = 250;
  // While an unsaved field is being edited, one tiny 'still here' ping at most this often.
  const ACTIVITY_PING_MS = 30000;
  let lastPing = -Infinity;
  let editedKey = null;
  let superseded = false;
  let pingTimer = null;
  let pingFailures = 0;

  const pingWanted = () => connected && !superseded && !flow.disposed && flow.dirty.size > 0 && document.hidden !== true;
  function pingFailed(error) {
    pingFailures++;
    console.warn('glitch-idea: activity ping failed (' + (error?.code ?? 'unknown') + '), failure ' + pingFailures);
    if (error?.status === 401 && error.code === 'session_superseded') showSuperseded();
  }
  function pingActivity() {
    if (!connected || flow.dirty.size === 0) return;
    const now = Date.now();
    if (now - lastPing < ACTIVITY_PING_MS) return;
    lastPing = now;
    try { Promise.resolve(api.write('activity', {})).catch(pingFailed); } catch (error) { pingFailed(error); }
  }
  // Keep-alive: while the page is visible with an unsaved field, ping on a timer too, same 30 s floor.
  function syncPing() {
    if (!pingWanted()) { if (pingTimer !== null) { clearTimeout(pingTimer); pingTimer = null; } return; }
    if (pingTimer !== null) return;
    pingTimer = setTimeout(() => {
      pingTimer = null;
      if (pingWanted()) pingActivity();
      syncPing();
    }, ACTIVITY_PING_MS);
    pingTimer?.unref?.();
  }
  globalThis.document?.addEventListener?.('visibilitychange', syncPing);

  const autosaveNow = handle(async () => {
    const key = editedKey;
    if (!key || !flow.state?.idea_id || !flow.dirty.has(key) || flow.selectionUncertain || flow.disposed) return;
    // Another write is in flight: try again once it has had time to settle; never drop the edit.
    if (flow.busy || flow.pending) { clearTimeout(autosaveTimer); autosaveTimer = setTimeout(autosaveNow, AUTOSAVE_PAUSE_MS); return; }
    await flow.save(key, true);
  });

  function saveSoonAfterLeaving() {
    clearTimeout(autosaveTimer);
    autosaveTimer = setTimeout(autosaveNow, BLUR_SAVE_MS);
  }

  function edited(key, fields) {
    if (flow.selectionUncertain || flow.disposed) return;
    flow.edit(key, fields);
    editedKey = key;
    pingActivity();
    syncPing();
    clearTimeout(autosaveTimer);
    if (flow.state?.idea_id) autosaveTimer = setTimeout(autosaveNow, AUTOSAVE_PAUSE_MS);
  }

  function field(body, labelText, id, value, changed, textarea = false) {
    const wrap = element('div', '', 'field');
    const label = element('label', labelText);
    label.htmlFor = id;
    const input = element(textarea ? 'textarea' : 'input');
    if (!textarea) input.type = 'text';
    input.id = id;
    input.value = value ?? '';
    input.disabled = !connected;
    input.readOnly = flow.inputLocked();
    input.addEventListener('input', event => changed(event.target.value));
    input.addEventListener('change', () => { if (editedKey && flow.dirty.has(editedKey)) saveSoonAfterLeaving(); });
    wrap.append(label, input);
    body.append(wrap);
    return input;
  }

  const acceptanceAllowed = allowNew => (allowNew && !flow.state?.idea_id) || flow.state?.idea_status === 'active';
  function acceptanceNotice(body, key, allowNew = false) {
    if (acceptanceAllowed(allowNew)) return;
    const notice = element('p', flow.state?.idea_status === 'archived' ?
      'This idea is archived. To accept new answers, first explicitly redo and accept Exploration to reactivate it. Archived history is kept; your draft remains here.' :
      'The idea status is unavailable. Reload current state before accepting new answers. Your draft remains here.', 'notice');
    notice.id = key + '-idea-status'; notice.setAttribute('role', 'status'); body.append(notice);
  }

  // A disabled Accept always says what is missing; an enabled one carries no reason and no title.
  function acceptReason(foot, save, key, fields) {
    if (!save.disabled) return;
    const text = acceptBlockers(key, fields, flow).join(' ');
    if (!text) return;
    const reason = element('p', text, 'foot-message accept-reason');
    reason.id = key + '-accept-reason';
    foot.append(reason);
    save.setAttribute('aria-describedby', reason.id);
    save.setAttribute('title', text);
  }

  // A new idea starts with the configured default workspace, once; the fields stay editable and typed text is never replaced.
  function prefillWorkspace() {
    if (flow.state?.idea_id) { flow.workspacePrefilled = false; return; }
    const chosen = flow.state?.default_workspace;
    const current = flow.buffers.capture?.workspace;
    if (flow.workspacePrefilled || flow.inputLocked() || !chosen || typeof chosen.name !== 'string' || typeof chosen.path !== 'string' ||
        !chosen.name || !chosen.path || (current && (current.name || current.path))) return;
    flow.workspacePrefilled = true;
    flow.buffers.capture = {...flow.buffers.capture, workspace: {name: chosen.name, path: chosen.path, confirmed: false}};
  }

  registerStep('capture', ({body, foot}) => {
    acceptanceNotice(body, 'capture', true);
    prefillWorkspace();
    const fields = flow.buffers.capture;
    const columns = element('div', '', 'g-cols');
    const words = field(columns, 'Your idea', 'idea-text', fields.raw_text,
      value => edited('capture', {...fields, raw_text: value}), true);
    words.placeholder = "What do you want to exist that doesn't yet?";
    const card = element('section', '', 'g-card');
    card.setAttribute('aria-labelledby', 'workspace-title');
    const heading = element('h2', 'Workspace', 'g-section-title');
    heading.id = 'workspace-title';
    card.append(heading, element('p', 'The project this idea is for, such as the folder you would build it in. It is not where ideas are stored.', 'g-sub'));
    const workspaces = element('div', '', 'workspace-fields');
    const workspace = fields.workspace ?? {name: '', path: '', confirmed: false};
    for (const [key, label] of [['name', 'Name'], ['path', 'Full path on the service host']]) {
      field(workspaces, label, 'workspace-' + key, workspace[key], value => edited('capture',
        {...fields, workspace: {...workspace, [key]: value, confirmed: false}}));
    }
    card.append(workspaces);
    const label = element('label', '', 'check-label g-check');
    const confirmation = element('input');
    confirmation.type = 'checkbox';
    confirmation.id = 'workspace-confirmed';
    confirmation.checked = workspace.confirmed === true;
    confirmation.disabled = flow.inputLocked() || !connected;
    confirmation.addEventListener('change', event => edited('capture',
      {...fields, workspace: {...workspace, confirmed: event.target.checked}}));
    label.append(confirmation, element('span', 'I confirm this workspace name and path'));
    card.append(label, element('p', 'Give its full path on the computer running this service. The service checks the folder exists; the name is only a label.', 'help'));
    columns.append(card);
    body.append(columns);
    const files = element('section', '', 'files-section');
    files.setAttribute('aria-labelledby', 'files-title');
    const filesHeading = element('h2', 'Original files', 'g-section-title');
    filesHeading.id = 'files-title';
    files.append(filesHeading);
    if (!/^idea_[0-9a-f]{32}$/.test(flow.state?.idea_id ?? '')) {
      const notice = element('p', 'Save your idea words and workspace first. You can then reopen Capture to upload original files.', 'notice');
      notice.id = 'capture-attachments'; files.append(notice);
    } else if (captureUploads) {
      const attachments = element('section'); attachments.id = 'capture-attachments';
      attachments.setAttribute('aria-label', 'Original Capture attachments');
      attachments.append(element('p', 'Original files stay separate from accepted design-set membership. Uploading preserves your idea words.', 'help'));
      captureUploads({body: attachments, flow, api, element, button, connected, handle});
      files.append(attachments);
    } else {
      const notice = element('p', 'The packaged file uploader is unavailable. Your idea words remain editable.', 'notice');
      notice.id = 'capture-attachments'; files.append(notice);
    }
    body.append(files);
    const save = button(flow.busy ? 'Saving…' : 'Save and continue', handle(async () => {
      if (!acceptanceAllowed(true)) return false;
      clearTimeout(autosaveTimer);
      await flow.save('capture');
    }), 'primary');
    save.id = 'capture-accept';
    save.disabled = !acceptanceAllowed(true) || !connected || flow.busy || Boolean(flow.pending) || !validCapture(fields);
    acceptReason(foot, save, 'capture', fields);
    foot.append(save, element('p', 'Drafts save when you leave a field.', 'g-foot__msg'));
  });

  registerStep('priorities', ({body, foot}) => {
    acceptanceNotice(body, 'priorities');
    const fields = flow.buffers.priorities;
    const columns = element('div', '', 'g-cols');
    for (const [key, title, anchors] of [
      ['urgency', 'Urgency', '1 = can wait indefinitely · 10 = needed now'],
      ['importance', 'Importance', '1 = nice to have · 10 = essential'],
    ]) {
      const group = element('fieldset', '', 'g-card');
      group.append(element('legend', title));
      group.append(element('p', fields[key] == null ? 'Not rated' : String(fields[key]) + ' of 10', 'rating-readout'));
      const scale = element('div', '', 'scale g-scale');
      for (let value = 1; value <= 10; value++) {
        const choice = button(String(value), () => edited('priorities', {...fields, [key]: value}), 'rating g-rate');
        choice.id = key + '-' + value;
        choice.setAttribute('aria-pressed', String(fields[key] === value));
        choice.setAttribute('aria-label', title + ': ' + value + ' of 10');
        choice.disabled = flow.inputLocked() || !connected;
        scale.append(choice);
      }
      group.append(scale, element('p', anchors, 'help'));
      columns.append(group);
    }
    body.append(columns);
    body.append(element('p', 'These are your ratings. Neither is prefilled or adjusted by an AI assessment.', 'help'));
    const save = button(flow.busy ? 'Saving…' : 'Save ratings', handle(async () => {
      if (!acceptanceAllowed(false)) return false;
      clearTimeout(autosaveTimer);
      await flow.save('priorities');
    }), 'primary');
    save.id = 'priorities-accept';
    save.disabled = !acceptanceAllowed(false) || !connected || flow.busy || Boolean(flow.pending) || !validPriorities(fields);
    acceptReason(foot, save, 'priorities', fields);
    foot.append(save);
  });

  const stepList = () => flow.steps?.length ? flow.steps : STEPS;

  // One rail button per step: number, status glyph, name, state word, and a lock while the steps before it are open.
  function navButton(step, index) {
    const status = flow.status(step.key);
    // Only the open step is called Current; the next step to do that is not open reads To do.
    const shown = status === 'current' && flow.current !== step.key ? 'todo' : status;
    const node = button('', () => {
      if (!flow.open(step.key)) announce('Step ' + (index + 1) + ' opens once the steps before it are accepted.');
      else byId('step-panel').focus();
    }, 'g-step');
    node.id = 'compact-' + step.key;
    node.dataset.status = status;
    node.setAttribute('aria-label', 'Step ' + (index + 1) + ', ' + step.title + ': ' + statusLabel(shown) + '. Open it.');
    node.setAttribute('aria-controls', 'step-panel');
    node.setAttribute('aria-expanded', String(flow.current === step.key));
    if (flow.current === step.key) node.setAttribute('aria-current', 'step');
    node.append(element('span', String(index + 1), 'g-step__n'), statusMark(status),
      element('span', step.title, 'g-step__name'), element('span', railWord(shown), 'g-step__state'));
    if (!flow.reachable(step.key)) node.append(lockMark());
    node.disabled = flow.busy || !connected || flow.paused || flow.selectionUncertain;
    node.addEventListener('keydown', event => {
      const keys = ['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp', 'Home', 'End'];
      if (!keys.includes(event.key)) return;
      event.preventDefault();
      const steps = stepList();
      const target = event.key === 'Home' ? 0 : event.key === 'End' ? steps.length - 1 :
        (index + (event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : steps.length - 1)) % steps.length;
      const targetKey = steps[target].key;
      (byId('compact-' + targetKey) ?? byId('step-panel')).focus();
    });
    return node;
  }

  function movedMessage(error) {
    const home = error?.data?.home;
    const where = typeof home?.workspace_name === 'string' && home.workspace_name ? home.workspace_name : 'a workspace';
    const file = typeof home?.file_path === 'string' && home.file_path ? ' Its file is ' + home.file_path + '.' : '';
    return 'This idea has moved to ' + where + '. Nothing was changed here; edit it there.' + file;
  }

  function errorPanel(body) {
    if (!flow.error) return;
    const box = element('div', '', 'error-box');
    box.setAttribute('role', 'alert');
    const descriptions = {
      stale_revision: 'Another change was saved. Reload current state and review your preserved answers before saving again.',
      stale_draft_version: 'A newer draft exists. Reload current state and review your preserved answers before saving again.',
      stale_source: 'Saved source inputs changed. Reload current state and review your answers or request a fresh suggestion.',
      stale_backlog: 'The backlog order changed. Reload and review the current order, then request a fresh assessment. Your answers have been kept.',
      archived_revision: 'This idea is archived. Assess acceptance is unavailable. To reactivate it, explicitly redo and accept Exploration. Archived history is kept; reload current state and keep your answers.',
      idea_archived: 'This idea is archived. New acceptance and uploads are unavailable until you explicitly redo and accept Exploration to reactivate it. Archived history is kept; reload current state and keep your answers.',
      saved_state_changed: 'This request was saved, but the current saved state differs. Your answers have been kept; reload and review before saving again.',
      invalid_response: 'The response could not be verified. The save may have committed; check its result before any retry.',
      invalid_input: 'Check the required answers. Your buffer has been kept.',
      too_large: 'This input exceeds the allowed size. Your buffer has been kept.',
      durability_uncertain: 'The save may have committed. Check its result before any retry.',
      connection_lost: 'The connection was lost. The save result must be checked before retrying.',
      browser_unauthorized: 'This browser needs pairing again. Your answers have been kept.',
      workspace_unavailable: 'The workspace path must be an existing folder on the computer running this service, written as its full path there (over SSH that is the remote computer, not this one). Your answers have been kept.',
      memory_provenance_missing: 'Your terminal\'s memory answer is no longer open, so it could not be confirmed. Reload this page so your terminal is asked again, then press Accept once it has answered. Your answers have been kept.',
      session_binding_mismatch: 'This tab belongs to a different session than the one that answered. Open the link the agent printed for this session in a new tab. Your answers have been kept.',
    };
    if (flow.error.code === 'idea_moved') descriptions.idea_moved = movedMessage(flow.error);
    else if (flow.error.code === 'not_moved') descriptions.not_moved = 'This idea has not been moved to a workspace, so there is nothing to deliver. Your answers have been kept.';
    else if (flow.error.code === 'delivery_conflict') descriptions.delivery_conflict = 'This idea was already delivered with a different reference. Nothing was changed.';
    box.append(element('p', descriptions[flow.error.code] ?? 'Could not save. Your answers are still here; this step is not marked saved.'));
    const staleCAS = ['stale_revision', 'stale_draft_version', 'stale_backlog', 'stale_source', 'archived_revision', 'idea_archived', 'idea_moved', 'not_moved', 'delivery_conflict', 'memory_provenance_missing'].includes(flow.error.code);
    if (flow.pending && (flow.pending.ambiguous || !staleCAS)) {
      const retry = button(flow.pending.ambiguous ? 'Check save result' : 'Try again', handle(() => flow.retry()));
      retry.id = 'retry-save';
      box.append(retry);
    }
    if (!flow.pending?.ambiguous) {
      const reload = button('Reload current state and keep my answers', handle(async () => {
        // Before any state has loaded, only loadConnected knows the requested URL idea.
        // It clears the error itself, and only when no write is pending.
        if (!connected || flow.state === null) {
          if (!connected) session = await api.session();
          await loadConnected();
        } else await afterLoads(() => flow.reloadKeepingAnswers());
        render();
      }));
      reload.id = 'reload-state';
      reload.disabled = flow.selectionUncertain;
      box.append(reload);
    }
    body.append(box);
  }

  function render() {
    if (superseded || (flow.refreshUnauthorized && [flow.error?.code, flow.proposalError?.code].includes('session_superseded'))) { showSuperseded(); return; }
    if (connected && flow.refreshUnauthorized) { showPairing('browser_unauthorized'); return; }
    if (api.bindingId) rememberSelection(api.bindingId, flow.state === null ? initialIdeaId : flow.state.idea_id);
    const active = document.activeElement;
    const focusId = pendingFocus && active?.id === 'step-panel' ? pendingFocus : active?.id;
    if (active?.id !== 'step-panel') pendingFocus = null;
    const previousBody = byId('step-body');
    const page = flow.view === 'ideas' || flow.view === 'setup'; // panels that replace the seven steps
    const renderKey = page ? flow.view : flow.current;
    const scroll = renderedStep === renderKey && previousBody
      ? [previousBody.scrollTop, previousBody.scrollLeft] : [0, 0];
    const selection = active?.tagName === 'TEXTAREA' || (active?.tagName === 'INPUT' && active.type === 'text')
      ? [active.selectionStart, active.selectionEnd] : null;
    const {saved, skipped} = flow.progress();
    const total = stepList().length;
    const identity = byId('identity');
    identity.textContent = flow.state?.idea_id ? flow.state.idea_id.slice(0, 9) + '…' + flow.state.idea_id.slice(-4) + ' · revision ' + flow.state.revision : 'Not saved yet';
    if (flow.state?.idea_id) identity.setAttribute?.('title', flow.state.idea_id); else identity.removeAttribute?.('title');
    const railTitle = byId('rail-title');
    if (railTitle) {
      const firstLine = (flow.buffers?.capture?.raw_text ?? '').split('\n').map(line => line.trim()).find(Boolean) ?? '';
      railTitle.textContent = firstLine ? (firstLine.length > 60 ? firstLine.slice(0, 59).trimEnd() + '…' : firstLine) : 'New idea';
    }
    const progress = byId('progress');
    const track = element('span', '', 'bw-progress__track');
    const fill = element('span', '', 'bw-progress__fill');
    if (fill.style) fill.style.width = Math.round(100 * saved / total) + '%';
    track.append(fill);
    progress.replaceChildren(track, element('span', saved + ' of ' + total + ' saved' + (skipped ? ', ' + skipped + ' skipped' : '')));
    byId('save-status').textContent = flow.message;
    const tabs = [['nav-idea', 'workflow', () => connected && !flow.disposed && !flow.selectionUncertain],
      ['nav-ideas', 'ideas', () => connected && (!flow.busy || flow.proposalFlight) && !flow.ideasLoading && !flow.disposed && !flow.selectionUncertain],
      ['nav-setup', 'setup', () => connected && !flow.disposed && !flow.selectionUncertain]];
    // A suggestion request sent by itself never disables Ideas: a tab that flips off between pointer-down and pointer-up drops the person's click (showIdeas reads the list safely during one).
    for (const [id, view, enabled] of tabs) {
      const tab = byId(id);
      if (!tab) continue;
      tab.hidden = !connected;
      tab.disabled = !enabled();
      tab.setAttribute('aria-selected', String(flow.view === view));
      if (flow.view === view) tab.setAttribute('aria-current', 'page'); else tab.removeAttribute?.('aria-current');
    }
    const agent = flow.state?.agent_status ?? session?.agent_status;
    const agentLong = !connected ? 'Browser disconnected. Pair this browser to check agent status.' : agent === 'connected' ? 'Agent connected' :
      'Agent ' + (agent ?? 'unavailable') + '. Reinvoke /glitch-idea in the initiating pane to resume AI assistance.';
    const agentShort = !connected ? 'Browser disconnected' : agent === 'connected' ? 'Agent connected' : 'Agent ' + (agent ?? 'unavailable');
    // Capture and Priorities are yours alone: no AI status on screen there.
    const quiet = flow.view === 'workflow' && ['capture', 'priorities'].includes(flow.current);
    const agentLine = byId('agent-status');
    if (quiet) { agentLine.textContent = ''; agentLine.hidden = true; agentLine.setAttribute('title', ''); }
    else {
      agentLine.replaceChildren(element('i'), element('span', agentShort));
      agentLine.className = 'g-agent' + (connected && agent === 'connected' ? '' : ' g-agent--off');
      agentLine.setAttribute('title', agentLong);
      agentLine.hidden = false;
    }
    const rail = byId('step-rail');
    if (rail) rail.hidden = page;
    const compact = byId('compact-nav');
    compact.hidden = page;
    const steps = element('ol', '', 'g-steps');
    stepList().forEach((step, index) => { const item = element('li'); item.append(navButton(step, index)); steps.append(item); });
    compact.replaceChildren(steps);
    const columns = byId('columns');
    columns.replaceChildren();
    const renderSteps = flow.view === 'ideas' ? [{key: 'ideas', title: 'Your ideas'}] : flow.view === 'setup' ? [{key: 'setup', title: 'Setup'}] : stepList().filter(step => step.key === flow.current);
    renderSteps.forEach(step => {
      const index = Math.max(0, stepList().findIndex(candidate => candidate.key === step.key));
      const panel = element('section', '', 'step-panel');
      panel.id = 'step-panel';
      panel.tabIndex = -1;
      panel.setAttribute('aria-labelledby', 'step-title');
      const head = element('header', '', 'panel-head');
      head.append(element('p', step.key === 'ideas' ? 'Saved Markdown ideas' : step.key === 'setup' ? 'Where ideas are kept' : 'Step ' + (index + 1) + ' of ' + stepList().length + ' · ' + statusLabel(flow.status(step.key)), 'panel-caption'));
      const title = element('h1', flow.paused ? 'Paused.' : step.title, flow.paused ? 'paused-title' : '');
      title.id = 'step-title';
      head.append(title);
      if (STEP_SUBTITLES[step.key] && !flow.paused && !flow.selectionUncertain) head.append(element('p', STEP_SUBTITLES[step.key], 'g-sub'));
      const body = element('div', '', 'panel-body');
      body.id = 'step-body';
      const foot = element('footer', '', 'panel-foot');
      if (flow.selectionUncertain) {
        const warning = element('p', 'The selection result is uncertain. Your answers are preserved and editing is paused until you explicitly restore the last verified selection.', 'error-box');
        warning.id = 'selection-uncertain'; warning.setAttribute('role', 'alert'); body.append(warning);
        const label = element('label', 'Preserved answers'); label.htmlFor = 'selection-preserved-answers';
        const answers = element('textarea'); answers.id = 'selection-preserved-answers'; answers.readOnly = true; answers.value = JSON.stringify(flow.buffers, null, 2);
        body.append(label, answers);
        const recover = button(flow.state === null ? 'Reload requested selection' : 'Restore last verified selection',
          handle(() => flow.state === null ? loadConnected() : flow.restoreSelection()), 'primary');
        recover.id = 'selection-recover'; recover.disabled = !connected || flow.busy || flow.disposed; foot.append(recover);
      } else if (step.key === 'ideas') {
        if (ideasRenderer) ideasRenderer({body, foot, flow, api, element, button, connected, isConnected: () => connected, handle});
        else body.append(element('p', 'The packaged Ideas handler is unavailable.', 'notice'));
      } else if (step.key === 'setup') {
        if (setupRenderer) setupRenderer({body, foot, flow, api, element, button, connected, isConnected: () => connected, handle});
        else body.append(element('p', 'The packaged Setup handler is unavailable.', 'notice'));
      } else if (flow.paused) {
        body.append(element('p', 'Paused at step ' + (index + 1) + ' · ' + step.title));
        body.append(element('p', flow.state?.idea_id ? 'Your idea and saved drafts remain available.' : 'This incomplete idea has not been saved. Keep this tab open to retain your answers.'));
        for (const key of ['urgency', 'importance']) if (flow.buffers.priorities[key] == null) body.append(element('p', key + ': not rated'));
        const resume = button('Resume', handle(() => afterLoads(async () => {
          if (!flow.paused) return false; // A queued second Resume must not undo a later Pause.
          const ideaId = flow.state?.idea_id ?? null;
          const sessionId = flow.state?.session_id ?? api.sessionId ?? null;
          const epoch = flow.mutationEpoch;
          session = await api.session();
          if (flow.disposed || epoch !== flow.mutationEpoch) return false;
          const state = await flow.readAuthority(ideaId, sessionId);
          flow.load(state, true);
          flow.paused = false;
          if (!flow.pending) flow.error = null; // A pending write keeps its error and Check door.
          render();
        })), 'primary');
        resume.id = 'resume-workflow';
        foot.append(resume);
      } else {
        if (AGENT_STEPS.includes(step.key) && !OWN_NO_AGENT_NOTICE.includes(step.key) && !(connected && agent === 'connected')) {
          const agentNotice = element('p', agentLong, 'notice'); agentNotice.id = 'agent-notice'; agentNotice.setAttribute('role', 'status'); body.append(agentNotice);
        }
        if (flow.status(step.key) === 'saved') body.append(element('p', step.key === 'review' ? 'Review is derived from the verified current immutable planning packet.' : 'Accepted at revision ' + flow.state.steps[step.key].accepted_revision + '. Changes require acceptance again and mark dependent decisions for review.', 'notice'));
        if (flow.status(step.key) === 'review-needed') body.append(element('p', step.key === 'review' ? 'Saved source inputs changed. Review the prerequisites and explicitly generate a current planning prompt.' : 'Saved source inputs changed. Check this decision and accept it again.', 'notice'));
        errorPanel(body);
        const component = components.get(step.key);
        if (component) component({body, foot, flow, api, element, button, field, connected, isConnected: () => connected, edited, handle,
          proposalInventory: (target, key) => renderProposalInventory(target, key, flow)});
        else body.append(element('p', 'This step’s handler is not available in this intermediate build. It has not been completed.', 'notice'));
        const pause = button('Pause', handle(async () => { clearTimeout(autosaveTimer); await flow.pause(); }), 'bw-btn bw-btn--secondary');
        pause.id = 'pause-workflow';
        pause.disabled = flow.busy || !connected || Boolean(flow.pending);
        foot.append(pause);
      }
      if (step.key !== 'ideas' && step.key !== 'setup' && !flow.selectionUncertain) {
        const ideas = button('Ideas', handle(() => flow.showIdeas()), 'bw-btn bw-btn--ghost'); ideas.id = 'show-ideas';
        ideas.disabled = !connected || flow.busy || flow.ideasLoading || flow.disposed; foot.append(ideas);
      }
      foot.append(element('p', flow.message, 'foot-message'));
      panel.append(head, body, foot);
      columns.append(panel);
    });
    renderedStep = renderKey;
    // Reaching Discovery, Exploration, Method or Assess (or the agent connecting while one is open) starts the terminal
    // conversation. autoConverse makes at most one attempt per idea, revision, step and agent session,
    // so redraws never loop; it runs after this render, not inside it.
    if (connected && flow.view === 'workflow' && ['discovery', 'exploration', 'method', 'assess'].includes(flow.current) && !flow.handReleased(flow.current)) {
      const step = flow.current;
      queueMicrotask(() => { if (flow.current === step && !flow.disposed) Promise.resolve(flow.autoConverse(step)).catch(() => {}); });
    }
    const currentBody = byId('step-body');
    if (currentBody) { currentBody.scrollTop = scroll[0]; currentBody.scrollLeft = scroll[1]; }
    if (focusId) {
      const target = byId(focusId) ?? (focusId === 'pause-workflow' ? byId('resume-workflow') :
        focusId === 'resume-workflow' ? byId('pause-workflow') : null);
      if (target) {
        if (target.disabled) {
          pendingFocus = focusId;
          byId('step-panel').focus({preventScroll: true});
        } else {
          pendingFocus = null;
          target.focus({preventScroll: true});
          if (selection && target.setSelectionRange) target.setSelectionRange(...selection);
        }
      }
    }
  }

  // A superseded session always gets the replaced sentence, live this load or reloaded after a resume (owner, 07/10).
  const unauthorized = code => { if (code === 'session_superseded') showSuperseded(); else showPairing(code); };

  // A newer tab replaced this one: one sentence, no pairing form, no polling.
  function showSuperseded() {
    superseded = true;
    connected = false;
    flow.stopAgentRefresh();
    syncPing();
    api.csrf = null;
    const box = byId('connection');
    box.hidden = false;
    box.className = 'connection g-card';
    const sentence = element('p', 'This tab was replaced by a newer one. Switch to the newest Glitch idea tab.', 'error-box');
    sentence.setAttribute('role', 'alert');
    const warning = element('p', 'ONLY CLICK THIS IF YOU LOST THE TAB', 'superseded-warning');
    const link = document.createElement('a');
    link.textContent = 'Open Glitch idea in a new tab';
    link.href = location.origin + location.pathname;
    link.target = '_blank';
    link.rel = 'noopener';
    box.replaceChildren(sentence, warning, link);
  }

  function showPairing(code = '', {fromLink = false} = {}) {
    connected = false;
    flow.stopAgentRefresh();
    api.csrf = null;
    const box = byId('connection');
    box.hidden = false;
    box.className = 'connection g-card';
    box.replaceChildren(element('h1', 'Connect this browser'));
    const message = code === 'pairing_replay_session_invalidated' ?
      'That code was already redeemed and the browser session was invalidated. Resume the initiating agent explicitly for a new code.' :
      'Get a fresh one-time pairing code from the initiating agent pane. Enter it here; codes expire after 60 seconds.';
    box.append(element('p', message, 'g-sub'));
    if (fromLink && code !== 'pairing_replay_session_invalidated') {
      box.append(element('p', "This link's pairing code has expired. Ask your terminal for a new code (it runs session-open --resume).", 'notice'));
    }
    if (code && code !== 'browser_unauthorized') {
      const warning = element('p', code === 'session_binding_mismatch' ?
        'This tab belongs to a different session than the code you entered. Open the link the agent printed for that session in a new tab, then enter its code there. No automatic retry was made.' :
        'Pairing refused: ' + code + '. No automatic retry was made.', 'error-box');
      warning.setAttribute('role', 'alert');
      box.append(warning);
    }
    const form = element('form');
    const wrap = element('div', '', 'field');
    const label = element('label', 'One-time pairing code');
    label.htmlFor = 'pairing-code';
    const input = element('input');
    input.type = 'password';
    input.id = 'pairing-code';
    input.autocomplete = 'off';
    input.required = true;
    const submit = element('button', 'Connect', 'primary bw-btn bw-btn--primary');
    submit.type = 'submit';
    wrap.append(label, input);
    form.append(wrap, submit);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      submit.disabled = true;
      const pairingCode = input.value;
      input.value = '';
      try { session = await api.pair(pairingCode); }
      catch (error) { showPairing(error.code ?? 'connection_lost'); return; }
      try { await loadConnected(); }
      catch (error) {
        if (error.status === 401) unauthorized(error.code);
        else {
          box.hidden = true;
          flow.error = error;
          flow.message = 'Connected, but saved state could not be loaded. Your answers remain.';
          render();
        }
      }
    });
    box.append(form);
    render();
  }

  // One load at a time: an overlapping query-less load would see the first one's
  // adopted state and freeze it as foreign.
  let loading = null;
  function loadConnected() {
    loading ??= loadConnectedOnce().finally(() => { loading = null; });
    return loading;
  }

  // Resume is its own pinned read: it waits out any load in flight, then takes the slot.
  async function afterLoads(read) {
    while (loading) await loading.catch(() => {});
    loading = read().finally(() => { loading = null; });
    return loading;
  }

  async function loadConnectedOnce() {
    const ideaId = flow.state === null ? initialIdeaId : flow.state.idea_id;
    const sessionId = session?.session_id ?? api.sessionId ?? flow.state?.session_id ?? null;
    const epoch = flow.mutationEpoch;
    await api.write('transport', {host: location.host, origin: location.origin, secure_context: globalThis.isSecureContext === true});
    if (flow.disposed || epoch !== flow.mutationEpoch) return false;
    connected = true; flow.refreshUnauthorized = false;
    byId('connection').hidden = true;
    if (flow.selectionUncertain && flow.state !== null) {
      render(); return false;
    }
    const adoptSelection = () => flow.state === null && initialIdeaId === null && !flow.pending && flow.dirty.size === 0;
    const state = await flow.readAuthority(ideaId, sessionId, {adoptSelection});
    flow.selectionUncertain = false;
    flow.load(state, Boolean(flow.pending) || flow.dirty.size > 0);
    flow.startAgentRefresh({active: () => connected && document.hidden !== true});
    byId('connection').hidden = true;
    if (!flow.pending) flow.error = null; // A pending write keeps its error and Check door.
    flow.message = 'Saved state loaded';
    render();
  }

  flow.onChange = () => { render(); syncPing(); };
  render();
  pairFromFragment({api}).then(result => {
    if (result.error) { showPairing(result.error.code ?? 'connection_lost', {fromLink: true}); return null; }
    return result.session ?? api.session();
  }).then(value => { if (value === null) return; session = value; return loadConnected(); }).catch(error => {
    if (error.status === 401) unauthorized(error.code);
    else { flow.error = error; flow.message = 'Could not load saved state. No empty store was assumed.'; render(); }
  });
  globalThis.addEventListener('pagehide', () => { clearTimeout(autosaveTimer); flow.dispose(); });
  globalThis.addEventListener('beforeunload', event => {
    if (flow.dirty.size || flow.pending) { event.preventDefault(); event.returnValue = ''; }
  });
  return flow;
}

if (typeof document !== 'undefined') {
  applyTheme(readTheme()); // before anything draws
  loadStepModules().then(() => startApp()).catch(error => {
    const box = document.getElementById('connection');
    box.hidden = false;
    box.replaceChildren(element('h1', 'Could not start this workflow'),
      element('p', error.message + '. No step was marked completed.', 'error-box'));
    box.setAttribute('role', 'alert');
    document.getElementById('save-status').textContent = 'Packaged workflow failed to load';
  });
}
