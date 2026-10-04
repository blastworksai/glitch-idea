// Setup pane: api methods validate replies exactly and never carry the key into an error;
// the view renders the two stores, saves, removes the key, tests the connection.
// Real api.js and setup.js with a tiny fake DOM and fake fetch; no server or browser is claimed here.
import test from 'node:test';
import assert from 'node:assert/strict';
const web = new URL('../../glitch-idea/web/', import.meta.url);
const {IdeaApi, ApiError} = await import(new URL('api.js', web).href);
const loadSetup = () => import(new URL('setup.js', web).href);
const A = 'binding_' + 'a'.repeat(32), SA = 'session_' + 'a'.repeat(32);
const KEY = 'sk-test-0123456789-SECRET';

function pinned(handler) {
  const calls = [];
  const fetcher = async (url, init) => {
    calls.push({url, init, body: init.body === undefined ? undefined : JSON.parse(init.body)});
    const {status = 200, body} = await handler(url, init, calls.at(-1).body);
    return {status, ok: status >= 200 && status < 300, json: async () => structuredClone(body)};
  };
  const api = new IdeaApi(fetcher, 1000, A);
  api.acceptSession({binding_id: A, session_id: SA, csrf_token: 'csrf'});
  return {api, calls};
}
const PUBLIC = (where = 'native', base_url = null, key_set = false) => ({ok: true, code: 'ok', where, api: {base_url, key_set}});

test('api.settings reads the public settings and rejects any other shape', async () => {
  const {api, calls} = pinned(async () => ({body: PUBLIC('api', 'https://x.example/v1', true)}));
  assert.deepEqual(await api.settings(), PUBLIC('api', 'https://x.example/v1', true));
  assert.equal(calls[0].url, '/api/v1/settings'); assert.equal(calls[0].init.method, 'GET');
  for (const bad of [{...PUBLIC(), extra: 1}, {...PUBLIC(), where: 'cloud'}, {...PUBLIC(), api: {base_url: null}},
    {...PUBLIC(), api: {base_url: null, key_set: 'yes'}}, {...PUBLIC(), api: {base_url: 5, key_set: false}},
    {...PUBLIC(), api: {base_url: null, key_set: false, key: KEY}}]) {
    const {api: other} = pinned(async () => ({body: bad}));
    await assert.rejects(() => other.settings(), error => error.code === 'invalid_response' && !JSON.stringify(error.data).includes(KEY));
  }
});

test('api.saveSettings sends exactly where, base_url and key with CSRF, and returns the public shape', async () => {
  const {api, calls} = pinned(async () => ({body: PUBLIC('api', 'https://x.example', true)}));
  const reply = await api.saveSettings({where: 'api', base_url: 'https://x.example', key: KEY});
  assert.deepEqual(reply, PUBLIC('api', 'https://x.example', true));
  assert.equal(calls[0].url, '/api/v1/settings/save'); assert.equal(calls[0].init.method, 'POST');
  assert.equal(calls[0].init.headers['X-CSRF-Token'], 'csrf');
  assert.deepEqual(calls[0].body, {where: 'api', base_url: 'https://x.example', key: KEY});
  await api.saveSettings({where: 'native', base_url: null, key: null});
  await api.saveSettings({where: 'native', base_url: null, key: ''});
  assert.deepEqual(calls.slice(1).map(call => call.body.key), [null, '']);
});

test('api.saveSettings refuses a malformed payload before any request, without echoing the key', async () => {
  const {api, calls} = pinned(async () => ({body: PUBLIC()}));
  for (const bad of [{where: 'api', base_url: null}, {where: 'cloud', base_url: null, key: null},
    {where: 'api', base_url: null, key: 'short'}, {where: 'api', base_url: null, key: 'has space in it ' + KEY},
    {where: 'api', base_url: 7, key: null}, {where: 'api', base_url: null, key: KEY, extra: 1}]) {
    await assert.rejects(() => api.saveSettings(bad), error => error instanceof ApiError &&
      !String(error.message).includes(KEY) && !JSON.stringify(error.data).includes(KEY));
  }
  assert.equal(calls.length, 0);
});

test('api.saveSettings: a server refusal keeps its code and never contains the key; a malformed receipt is invalid_response', async () => {
  const refuse = pinned(async () => ({status: 400, body: {ok: false, code: 'insecure_url'}}));
  await assert.rejects(() => refuse.api.saveSettings({where: 'api', base_url: 'http://a.example', key: KEY}),
    error => error.code === 'insecure_url' && !JSON.stringify(error).includes(KEY) && !String(error.stack).includes(KEY));
  const wrong = pinned(async () => ({body: {...PUBLIC(), key: KEY}}));
  await assert.rejects(() => wrong.api.saveSettings({where: 'native', base_url: null, key: null}),
    error => error.code === 'invalid_response' && !JSON.stringify(error.data).includes(KEY));
});

test('api.testSettings posts an empty body and validates the reply exactly', async () => {
  const good = {ok: true, code: 'ok', reachable: true, reason: null, service: 'My workflow'};
  const {api, calls} = pinned(async () => ({body: good}));
  assert.deepEqual(await api.testSettings(), good);
  assert.equal(calls[0].url, '/api/v1/settings/test'); assert.deepEqual(calls[0].body, {});
  const down = {ok: true, code: 'ok', reachable: false, reason: 'unauthorized', service: null};
  assert.deepEqual(await pinned(async () => ({body: down})).api.testSettings(), down);
  for (const bad of [{...good, extra: 1}, {...good, reachable: 'yes'}, {...good, reason: 7}, {...good, service: 7}, {ok: true, code: 'ok'}]) {
    await assert.rejects(() => pinned(async () => ({body: bad})).api.testSettings(), error => error.code === 'invalid_response');
  }
});

// ---- view ------------------------------------------------------------------
class Node {
  constructor(tag = 'div') { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.dataset = {}; this.disabled = false; this.value = ''; this.textContent = ''; this.hidden = false; this.className = ''; }
  append(...nodes) { this.children.push(...nodes); }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  all() { return [this, ...this.children.flatMap(child => child.all())]; }
  text() { return this.all().map(node => node.textContent).join(' '); }
  async click() { if (!this.disabled) return this.listeners.click?.({target: this}); }
  type_(value) { this.value = value; this.listeners.input?.({target: this}); }
}
const element = (tag, text = '', className = '') => { const node = new Node(tag); node.textContent = text; if (className) node.className = className; return node; };
const button = (text, action, className = '') => { const node = element('button', text, className); node.type = 'button'; node.addEventListener('click', action); return node; };
const settle = () => new Promise(resolve => setImmediate(resolve));

async function mounted(handler, run) {
  const {render} = await loadSetup();
  const {api, calls} = pinned(handler);
  const flow = {api, disposed: false, busy: false, view: 'setup', message: '', onChange: () => mount()};
  let body, foot;
  const mount = () => {
    body = new Node('div'); foot = new Node('footer');
    render({body, foot, flow, api, element, button, connected: true, isConnected: () => true, handle: action => async () => { try { await action(); } catch (error) { flow.error = error; mount(); } }});
  };
  mount(); await settle(); await settle();
  const find = id => body.all().concat(foot.all()).find(node => node.id === id) ?? null;
  const text = () => body.text() + ' ' + foot.text();
  await run({flow, calls, find, text, body: () => body, foot: () => foot, remount: mount});
}
const store = {where: 'native', base_url: null, key_set: false};
const settingsHandler = (state = {...store}, tested = {ok: true, code: 'ok', reachable: true, reason: null, service: 'Team ideas'}) => async (url, init, body) => {
  if (url.endsWith('/settings/test')) return {body: tested};
  if (url.endsWith('/settings/save')) {
    if (state.fail) return {status: 400, body: {ok: false, code: state.fail}};
    state.where = body.where; state.base_url = body.base_url; if (body.key === '') state.key_set = false; else if (body.key !== null) state.key_set = true;
  }
  return {body: PUBLIC(state.where, state.base_url, state.key_set)};
};

test('the view renders the heading, the question and the two stores as one radio group', async () => {
  await mounted(settingsHandler(), async h => {
    assert.match(h.text(), /Where do your ideas live\?/);
    const native = h.find('setup-native'), own = h.find('setup-api');
    assert.match(native.textContent, /Glitch native — Markdown files in this Glitch store/);
    assert.match(own.textContent, /Your own workflow — through its API/);
    assert.equal(native.role, 'radio'); assert.equal(native['aria-checked'], 'true'); assert.equal(own['aria-checked'], 'false');
    assert.equal(h.find('setup-url'), null, 'no API fields while native is chosen');
    await own.click(); await settle();
    assert.equal(h.find('setup-api')['aria-checked'], 'true'); assert.equal(h.find('setup-native')['aria-checked'], 'false');
    assert.ok(h.find('setup-url')); assert.equal(h.find('setup-key').type, 'password'); assert.equal(h.find('setup-key').autocomplete, 'off');
    assert.equal(h.find('setup-key').placeholder, 'Paste the key your system minted for you');
    assert.equal(h.find('setup-remove-key'), null);
  });
});

test('the status line says ideas are saved in Glitch for native', async () => {
  await mounted(settingsHandler(), async h => assert.match(h.find('setup-status').textContent, /^Ideas are saved in Glitch\.$/));
});

test('the status line says saving to the workflow is not switched on yet when api is stored', async () => {
  await mounted(settingsHandler({where: 'api', base_url: 'https://w.example', key_set: true}), async h => {
    assert.match(h.find('setup-status').textContent, /not switched on yet/);
    assert.match(h.find('setup-status').textContent, /still saved in Glitch/);
    assert.equal(h.find('setup-url').value, 'https://w.example');
    assert.equal(h.find('setup-key').value, '', 'the key field is never filled from the server');
    assert.equal(h.find('setup-key').placeholder, 'A key is saved — leave empty to keep it');
    assert.ok(h.find('setup-remove-key'));
  });
});

test('Save sends key null with an empty key field and the typed key otherwise', async () => {
  await mounted(settingsHandler({where: 'api', base_url: 'https://w.example', key_set: true}), async h => {
    await h.find('setup-save').click(); await settle();
    assert.deepEqual(h.calls.at(-1).body, {where: 'api', base_url: 'https://w.example', key: null});
    h.find('setup-url').type_('https://other.example/api'); h.find('setup-key').type_(KEY);
    await h.find('setup-save').click(); await settle();
    assert.deepEqual(h.calls.at(-1).body, {where: 'api', base_url: 'https://other.example/api', key: KEY});
  });
});

test('after Save the key field is empty and no text in the page contains the typed key', async () => {
  await mounted(settingsHandler(), async h => {
    await h.find('setup-api').click(); await settle();
    h.find('setup-url').type_('https://w.example'); h.find('setup-key').type_(KEY);
    await h.find('setup-save').click(); await settle();
    assert.equal(h.find('setup-key').value, '');
    for (const node of h.body().all().concat(h.foot().all())) {
      assert.ok(!String(node.textContent).includes(KEY)); assert.ok(!String(node.value).includes(KEY)); assert.ok(!String(node.placeholder).includes(KEY));
    }
    assert.match(h.text(), /Settings saved/); assert.ok(h.find('setup-remove-key'));
  });
});

test('a refused Save shows a plain sentence, clears the key field and never shows the key', async () => {
  const state = {...store, fail: 'insecure_url'};
  await mounted(settingsHandler(state), async h => {
    await h.find('setup-api').click(); await settle();
    h.find('setup-url').type_('http://w.example'); h.find('setup-key').type_(KEY);
    await h.find('setup-save').click(); await settle();
    assert.match(h.find('setup-notice').textContent, /Plain http is only allowed to this computer/);
    assert.equal(h.find('setup-key').value, ''); assert.ok(!h.text().includes(KEY));
    for (const [code, pattern] of [['invalid_url', /address/i], ['invalid_key', /key/i], ['api_needs_url_and_key', /address and a key/i], ['settings_not_private', /private/i]]) {
      state.fail = code; await h.find('setup-save').click(); await settle();
      assert.match(h.find('setup-notice').textContent, pattern, code);
    }
  });
});

test('Remove the saved key sends key empty string', async () => {
  await mounted(settingsHandler({where: 'native', base_url: 'https://w.example', key_set: true}), async h => {
    await h.find('setup-api').click(); await settle();
    await h.find('setup-remove-key').click(); await settle();
    assert.deepEqual(h.calls.at(-1).body, {where: 'native', base_url: 'https://w.example', key: ''}, 'removing a key never changes the store');
    assert.equal(h.find('setup-remove-key'), null);
  });
});

test('a key being typed survives a redraw and is what Save sends; Save then clears it', async () => {
  await mounted(settingsHandler({where: 'api', base_url: 'https://w.example', key_set: true}), async h => {
    h.find('setup-key').type_(KEY);
    h.remount();
    assert.equal(h.find('setup-key').value, KEY, 'the typed key is restored after a re-render');
    await h.find('setup-save').click(); await settle();
    assert.equal(h.calls.at(-1).body.key, KEY);
    assert.equal(h.find('setup-key').value, '');
    h.remount();
    assert.equal(h.find('setup-key').value, '', 'a saved key is not restored');
    assert.ok(!h.text().includes(KEY));
  });
});

test('Remove the saved key works while the API store is chosen', async () => {
  await mounted(settingsHandler({where: 'api', base_url: 'https://w.example', key_set: true}), async h => {
    await h.find('setup-remove-key').click(); await settle();
    assert.deepEqual(h.calls.at(-1).body, {where: 'api', base_url: 'https://w.example', key: ''});
  });
});

test('Test connection shows the service name, or a plain message per reason', async () => {
  await mounted(settingsHandler(), async h => {
    await h.find('setup-test').click(); await settle();
    assert.match(h.find('setup-test-result').textContent, /^Connected to Team ideas/);
  });
  const reasons = {api_not_configured: /address and a key/i, unauthorized: /key was not accepted/i, unreachable: /could not be reached/i,
    redirect_refused: /redirect/i, invalid_response: /unexpected/i, schema_mismatch: /not speak/i, tls_failed: /secure connection/i, refused: /refused/i};
  for (const [reason, pattern] of Object.entries(reasons)) {
    await mounted(settingsHandler(undefined, {ok: true, code: 'ok', reachable: false, reason, service: null}), async h => {
      await h.find('setup-test').click(); await settle();
      assert.match(h.find('setup-test-result').textContent, pattern, reason);
      assert.ok(!/Connected to/.test(h.text()));
    });
  }
});

test('Return to current idea goes back to the workflow', async () => {
  await mounted(settingsHandler(), async h => {
    await h.find('setup-return').click();
    assert.equal(h.flow.view, 'workflow');
  });
});

// Review final review.
test('Test connection is disabled while a typed key or address is unsaved, so it never wipes a typed key', async () => {
  await mounted(settingsHandler({where: 'api', base_url: 'http://127.0.0.1:9', key_set: true}), async h => {
    const key = h.find('setup-key'); key.type_('typed-key-0123456789');
    assert.equal(h.find('setup-test').disabled, true); assert.equal(h.find('setup-test-unsaved').hidden, false);
    assert.equal(key.value, 'typed-key-0123456789', 'the typed key is still there');
  });
});

test('the status line never claims where ideas live before the choice is read; a refused local check reads plainly', async () => {
  const {render} = await loadSetup();
  const {api} = pinned(async () => new Promise(() => {}));   // settings never answer
  const flow = {api, disposed: false, busy: false, view: 'setup', onChange() {}};
  const body = new Node('div'), foot = new Node('footer');
  render({body, foot, flow, api, element, button, connected: true, isConnected: () => true, handle: a => a});
  assert.match(body.text(), /Reading your Setup choice/); assert.doesNotMatch(body.text(), /Ideas are saved in Glitch/);
  await mounted(settingsHandler(), async h => {
    await h.find('setup-api').click(); await settle();
    h.find('setup-url').type_('https:' + '//board.example'); h.find('setup-key').type_('has spaces in it');
    await h.find('setup-save').click(); await settle();
    assert.match(h.text(), /a key is 8 to 512 characters with no spaces/); assert.doesNotMatch(h.text(), /invalid_input/);
  });
});

// Review confirmation review.
test('while a test runs the address and key fields are read-only, and arrow keys move the radio choice', async () => {
  let release;
  const handler = settingsHandler({where: 'api', base_url: 'http://127.0.0.1:9', key_set: true});
  await mounted(async (url, init, body) => url.endsWith('/settings/test') ? new Promise(r => { release = () => r({body: {ok: true, code: 'ok', reachable: true, reason: null, service: 'X'}}); }) : handler(url, init, body), async h => {
    const testing = h.find('setup-test').click(); await settle();
    assert.equal(h.find('setup-key').readOnly, true); assert.equal(h.find('setup-url').readOnly, true);
    release(); await testing; await settle();
    assert.equal(h.find('setup-key').readOnly, false);
    assert.equal(h.find('setup-api').tabIndex, 0); assert.equal(h.find('setup-native').tabIndex, -1);
    h.find('setup-api').listeners.keydown({key: 'ArrowLeft', preventDefault() {}}); await settle();
    assert.equal(h.find('setup-native')['aria-checked'], 'true');
  });
});
