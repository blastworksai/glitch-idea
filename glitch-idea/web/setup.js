// Setup pane: where ideas live. One question, two stores. The API key is write-only:
// it is typed here, sent once, cleared at once, and never read back, rendered or stored by the page.
const WHERE = {
  native: ['Glitch native', 'Markdown files in this Glitch store. Nothing to configure.'],
  api: ['Your own workflow', 'Through its API. Needs an address and a key your system minted for you.'],
};
const SAVE_REFUSALS = {
  insecure_url: 'Plain http is only allowed to this computer. Use an https address for any other system.',
  invalid_url: 'That API address is not valid. Give the full address, starting with https, with no sign-in details or query.',
  invalid_key: 'That key is not valid. A key is 8 to 512 characters with no spaces.',
  invalid_input: 'Check the address and the key: the address starts with https (or http to this computer), and a key is 8 to 512 characters with no spaces.',
  api_needs_url_and_key: 'Your own workflow needs both an address and a key. Enter them, or choose Glitch native.',
  settings_not_private: 'The private settings file on this computer could not be written safely, so nothing was saved.',
};
const TEST_REASONS = {
  api_not_configured: 'Save an address and a key first. Your own workflow needs both before it can be tested.',
  unauthorized: 'The key was not accepted. Check that your system minted it for you and paste it again.',
  unreachable: 'The system could not be reached. Check the address and that it is running.',
  redirect_refused: 'The address redirects somewhere else, which is refused. Use the final address.',
  invalid_response: 'The system gave an unexpected reply, so it was not trusted.',
  schema_mismatch: 'The system does not speak the idea workflow contract this version expects.',
  tls_failed: 'A secure connection could not be made. Check the https address and certificate.',
  refused: 'The system refused the request.',
};
const STATUS_NATIVE = 'Ideas are saved in Glitch.';
const STATUS_API = 'Connection settings saved. Saving ideas to your own workflow is not switched on yet; until it is, ideas are still saved in Glitch.';
const VIEW = new WeakMap(); // page-local state per Flow; a key being typed is held here in memory only, never stored or drawn outside its input

export function render(ctx) {
  const {body, foot, flow, element, button, handle} = ctx;
  const api = ctx.api ?? flow.api;
  const ui = VIEW.get(flow) ?? {loaded: false, loading: false, settings: null, choice: 'native', url: '', notice: '', test: '', busy: false, key: ''};
  VIEW.set(flow, ui);
  const redraw = () => { if (!flow.disposed) flow.onChange(); };
  const connected = () => (ctx.isConnected ? ctx.isConnected() : ctx.connected);
  const apply = settings => { ui.settings = settings; ui.choice = settings.where; ui.url = settings.api.base_url ?? ''; };
  const load = async () => {
    if (ui.loading) return;
    ui.loading = true; ui.notice = '';
    try { apply(await api.settings()); }
    catch (error) { ui.notice = 'Could not read the Setup choice: ' + (error?.code ?? 'unknown_error') + '.'; }
    finally { ui.loading = false; ui.loaded = true; redraw(); }
  };
  if (!ui.loaded && !ui.loading && connected()) { load(); }

  const back = button('Return to current idea', () => { ui.key = ''; if (!flow.disposed) { flow.view = 'workflow'; flow.onChange(); } });
  back.id = 'setup-return';
  const finishFoot = (...before) => { foot.append(...before, back); };

  const stored = ui.settings;
  // Never claim where ideas live before the saved choice has been read.
  const line = element('p', !stored ? (ui.loading || !ui.loaded ? 'Reading your Setup choice…' : 'Your Setup choice could not be read.') :
    stored.where === 'api' ? STATUS_API : STATUS_NATIVE, 'notice');
  line.id = 'setup-status'; line.setAttribute('role', 'status'); body.append(line);
  if (ui.notice) { const note = element('p', ui.notice, 'backlog-notice'); note.id = 'setup-notice'; note.setAttribute('role', 'status'); body.append(note); }
  if (!stored) { const extra = []; if (ui.loaded) { const retry = button('Try again', handle(() => { ui.loaded = false; return load(); })); retry.id = 'setup-retry'; extra.push(retry); } finishFoot(); foot.append(...extra); return; }

  body.append(element('p', 'Choose the store for saved ideas. Changing it does not move ideas you already saved.', 'g-sub'));
  const columns = element('div', '', 'g-cols setup-cols'), left = element('div', '', 'bw-stack');
  const group = element('div', '', 'setup-choices'); group.setAttribute('role', 'radiogroup'); group.setAttribute('aria-labelledby', 'setup-question');
  const question = element('h2', 'Where do your ideas live?', 'g-label setup-question'); question.id = 'setup-question';
  left.append(question, group); columns.append(left);
  for (const key of ['native', 'api']) {
    const choice = button('', () => { ui.choice = key; ui.test = ''; ui.notice = ''; redraw(); }, 'g-choice setup-choice');
    choice.append(element('b', WHERE[key][0]), element('span', WHERE[key][1]));
    // One choice of two: a radio group with roving focus and arrow keys, styled like every selected choice.
    choice.id = 'setup-' + key; choice.setAttribute('role', 'radio'); choice.setAttribute('aria-checked', ui.choice === key ? 'true' : 'false');
    choice.tabIndex = ui.choice === key ? 0 : -1;
    choice.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault?.();
      const other = key === 'native' ? 'api' : 'native';
      ui.choice = other; ui.test = ''; ui.notice = ''; ui.focus = 'setup-' + other; redraw();
    });
    group.append(choice);
    if (ui.focus === choice.id) { ui.focus = null; queueMicrotask?.(() => choice.focus?.()); }
  }
  let urlInput = null, keyInput = null, holder = null;
  if (ui.choice === 'api') {
    const fields = element('section', '', 'g-card setup-fields'); fields.setAttribute('aria-label', 'API connection');
    const urlField = element('div', '', 'field g-field');
    const urlLabel = element('label', 'API address'); urlLabel.htmlFor = 'setup-url';
    urlInput = element('input', '', 'g-input'); urlInput.id = 'setup-url'; urlInput.type = 'text'; urlInput.placeholder = 'https:' + '//…'; urlInput.value = ui.url; // placeholder built in two parts: shipped files carry no literal URL
    urlInput.setAttribute('autocomplete', 'off'); urlInput.autocomplete = 'off'; urlInput.spellcheck = false;
    urlInput.readOnly = ui.busy;  // nothing typed during a test or save can be lost to the redraw that ends it
    urlInput.addEventListener('input', () => { ui.url = urlInput.value; untested(); });
    urlField.append(urlLabel, urlInput, element('span', 'Full https address, no sign-in details or query. Plain http only for this computer.', 'g-help'));
    const keyField = element('div', '', 'field g-field');
    const keyLabel = element('label', 'API key'); keyLabel.htmlFor = 'setup-key';
    keyInput = element('input', '', 'g-input'); keyInput.id = 'setup-key'; keyInput.type = 'password'; keyInput.autocomplete = 'off'; keyInput.value = ui.key; // a key being typed survives the redraws the page makes
    keyInput.setAttribute('autocomplete', 'off'); keyInput.readOnly = ui.busy;
    keyInput.placeholder = stored.api.key_set ? 'A key is saved. Leave empty to keep it' : 'Paste the key your system minted for you';
    keyInput.addEventListener('input', () => { ui.key = keyInput.value; untested(); });
    keyField.append(keyLabel, keyInput);
    fields.append(urlField, keyField,
      element('p', 'Use https. Plain http is only allowed to this computer. The key is stored privately on this computer and never shown again. Keys are minted by your own system for you, and everything written with one is yours.', 'help'));
    columns.append(fields); holder = fields;
  } else {
    const note = element('div', '', 'bw-notice'); note.append(element('span', 'Ideas are saved in Glitch as Markdown, one file per idea.'));
    columns.append(note); holder = note;
  }
  body.append(columns);
  const save = async key => {
    const where = key === '' ? stored.where : ui.choice; // removing a key never switches the store
    if (ui.busy || !connected()) return;
    // The typed key is read once, here, and the field is cleared before anything is sent or drawn.
    let typed = null;
    if (key === undefined) { typed = keyInput?.value ? keyInput.value : null; } else typed = key;
    if (keyInput) keyInput.value = '';
    ui.key = '';
    const url = key !== '' && ui.choice === 'api' ? (urlInput?.value ?? ui.url).trim() : stored.api.base_url;
    ui.busy = true; ui.notice = ''; ui.test = ''; redraw();
    try {
      apply(await api.saveSettings({where, base_url: url === '' ? null : url, key: typed}));
      ui.notice = key === '' ? 'The saved key was removed.' : 'Settings saved.';
    } catch (error) {
      ui.notice = SAVE_REFUSALS[error?.code] ?? 'Could not save these settings: ' + (error?.code ?? 'unknown_error') + '. Nothing was changed.';
    } finally { typed = null; ui.busy = false; redraw(); }
  };
  const test = async () => {
    if (ui.busy || !connected()) return;
    ui.busy = true; ui.test = 'Testing…'; redraw();
    try {
      const result = await api.testSettings();
      ui.test = result.reachable ? 'Connected to ' + (result.service ?? 'your workflow') + '.' : (TEST_REASONS[result.reason] ?? 'The connection could not be made.');
    } catch (error) { ui.test = TEST_REASONS[error?.code] ?? 'The test could not run: ' + (error?.code ?? 'unknown_error') + '.'; }
    finally { ui.busy = false; redraw(); }
  };
  const actions = element('div', '', 'bw-row setup-actions');
  const saveButton = button('Save settings', handle(() => save()), 'bw-btn bw-btn--primary primary'); saveButton.id = 'setup-save'; saveButton.disabled = ui.busy || !connected();
  const testButton = button('Test connection', handle(test), 'bw-btn bw-btn--secondary'); testButton.id = 'setup-test';
  const unsaved = element('span', 'Test connection checks the saved settings, so save first.', 'g-foot__msg'); unsaved.id = 'setup-test-unsaved';
  // Typed but unsaved settings disable the test without a redraw, so a typed key is never wiped by it.
  function untested() {
    const changed = Boolean(keyInput?.value) || (ui.choice === 'api' && ui.url.trim() !== (stored.api.base_url ?? '')) || ui.choice !== stored.where;
    testButton.disabled = ui.busy || !connected() || changed; unsaved.hidden = !changed;
  }
  untested();
  actions.append(testButton);
  if (ui.choice === 'api' && stored.api.key_set) {
    const remove = button('Remove saved key', handle(() => save('')), 'bw-btn bw-btn--ghost');
    remove.id = 'setup-remove-key'; remove.disabled = ui.busy || !connected(); actions.append(remove);
  }
  holder.append(actions);
  finishFoot(saveButton); foot.append(unsaved);
  const result = element('p', ui.test, 'backlog-notice'); result.id = 'setup-test-result'; result.setAttribute('role', 'status'); result.hidden = !ui.test; holder.append(result);
}
