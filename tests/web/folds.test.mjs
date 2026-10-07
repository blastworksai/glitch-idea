// Controller/API evidence only; these tests do not qualify a browser.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web = new URL('../../glitch-idea/web/', import.meta.url);
const moduleFrom = async name => import('data:text/javascript;base64,' + Buffer.from(await readFile(new URL(name, web), 'utf8')).toString('base64'));
const {Flow, STEPS, FILL_KEYS, stepsFor, dependentsFor, discoveryFields, explorationFields, validDiscovery, validExploration, validMethod,
  statusLabel, statusGlyph, validCapture, validPriorities} = await moduleFrom('folds.js');
const {IdeaApi, ApiError} = await moduleFrom('api.js');
const IDEA = 'idea_00000000000000000000000000000001';
const BINDING = 'binding_' + '1'.repeat(32);
const SESSION = 'session_' + '2'.repeat(32);
const CAPTURE = {raw_text: '  A real fixture 💡\n', workspace: {name: 'fixture', path: '/fixture', confirmed: true}};
const copy = value => structuredClone(value);
function state(overrides = {}) {
  return {ok: true, code: 'ok', idea_id: IDEA, revision: 1, draft_version: 0,
    backlog_revision: 0, current_step: 'priorities', agent_status: 'disconnected',
    steps: Object.fromEntries(STEPS.map(({key}) => [key, {status: key === 'capture' ? 'saved' : key === 'priorities' ? 'current' : 'todo',
      accepted_revision: key === 'capture' ? 1 : null, evidence_id: key === 'capture' ? 'capture-receipt' : null}])),
    accepted: Object.fromEntries(STEPS.map(({key}) => [key, key === 'capture' ? copy(CAPTURE) : null])),
    drafts: {}, draft: null, ...overrides};
}
function harness(initial = state()) {
  let current = copy(initial), count = 0;
  const writes = [], reads = [], receipts = new Map();
  const api = {
    state: async () => { reads.push(copy(current)); return copy(current); },
    write: async (operation, payload) => {
      writes.push({operation, payload: copy(payload)});
      const key = operation === 'capture' ? 'capture' : payload.step;
      const fields = operation === 'capture' ? {raw_text: payload.raw_text, workspace: payload.workspace} : payload.fields;
      if (operation === 'capture') { current.idea_id = IDEA; current.current_step = 'priorities'; }
      if (operation === 'navigate') {
        current.current_step = payload.step;
      } else if (operation === 'draft') {
        current.current_step = key;
        current.draft_version++;
        current.drafts[key] = copy(fields);
        current.steps[key] = {status: 'unsaved', accepted_revision: null, evidence_id: null};
      } else {
        current.revision++;
        current.accepted[key] = copy(fields);
        delete current.drafts[key];
        current.steps[key] = {status: 'saved', accepted_revision: current.revision, evidence_id: 'saved-' + key};
      }
      const result = {ok: true, code: 'ok', write_state: 'applied', request_id: payload.request_id,
        idea_id: IDEA, revision: current.revision, draft_version: current.draft_version, backlog_revision: 0};
      receipts.set(payload.request_id, copy(result));
      return result;
    },
    reconcile: async id => ({result: copy(receipts.get(id) ?? null), state: await api.state()}),
  };
  const flow = new Flow(api, () => 'request-' + (++count));
  flow.load(initial);
  return {flow, api, writes, reads, receipts, current};
}

test('fixed eight-step vocabulary and truthful saved/skipped glyphs', () => {
  assert.deepEqual(STEPS.map(step => step.key), ['capture', 'priorities', 'method', 'discovery', 'exploration', 'visualize', 'assess', 'review']);
  assert.deepEqual(STEPS.map(step => step.title), ['Capture', 'Priorities', 'Methods', 'Discovery', 'Exploration', 'Visualize', 'Assess', 'Review']);
  assert.ok(Object.isFrozen(STEPS));
  assert.equal(statusGlyph('saved'), '✓');
  assert.notEqual(statusGlyph('skipped'), '✓');
  assert.equal(statusLabel('review-needed'), 'Review needed');
});

test('capture exact wording requires explicit confirmed workspace', () => {
  assert.equal(validCapture(CAPTURE), true);
  assert.equal(validCapture({...CAPTURE, workspace: {...CAPTURE.workspace, confirmed: false}}), false);
  assert.equal(validCapture({...CAPTURE, workspace: {...CAPTURE.workspace, path: ''}}), false);
  assert.equal(validCapture({...CAPTURE, raw_text: ' \n'}), false);
});

test('independent ratings remain unknown and require both real integers', () => {
  const {flow} = harness();
  assert.deepEqual(flow.buffers.priorities, {urgency: null, importance: null});
  for (const fields of [{urgency: 4, importance: null}, {urgency: true, importance: 6},
    {urgency: 11, importance: 1}, {urgency: 1, importance: '6'}]) assert.equal(validPriorities(fields), false);
  assert.equal(validPriorities({urgency: 4, importance: 6}), true);
});

test('navigation never writes and future todo steps stay gated', () => {
  const {flow, writes} = harness();
  assert.equal(flow.open('capture'), true);
  assert.equal(flow.open('priorities'), true);
  assert.equal(flow.open('method'), false);
  assert.deepEqual(writes, []);
  assert.equal(flow.progress().saved, 1);
});

test('no saved check without server acceptance evidence', () => {
  const bad = state();
  bad.steps.priorities.status = 'saved';
  assert.throws(() => harness(bad), /Invalid step evidence/);
});

test('accepted and partial draft fields hydrate across reload without invented values', () => {
  const {flow} = harness(state({drafts: {priorities: {urgency: 7, importance: null}}}));
  assert.deepEqual(flow.buffers.capture, CAPTURE);
  assert.deepEqual(flow.buffers.priorities, {urgency: 7, importance: null});
});

test('local edits suppress saved check and show transitive review', () => {
  const initial = state();
  for (const key of ['method', 'discovery', 'exploration', 'visualize', 'assess', 'review']) initial.steps[key] = {status: 'saved', accepted_revision: 1, evidence_id: key};
  const {flow} = harness(initial);
  flow.edit('capture', {...CAPTURE, raw_text: 'changed'});
  assert.equal(flow.status('capture'), 'unsaved');
  for (const key of ['method', 'discovery', 'exploration', 'visualize', 'assess', 'review']) assert.equal(flow.status(key), 'review-needed');
  assert.equal(flow.status('priorities'), 'current');
});

test('capture submits exact words and a durable request ID', async () => {
  const initial = state({idea_id: null, revision: 0, current_step: 'capture'});
  initial.steps.capture = {status: 'current', accepted_revision: null, evidence_id: null};
  initial.accepted.capture = null;
  const {flow, writes} = harness(initial);
  flow.edit('capture', CAPTURE);
  assert.equal(await flow.save('capture'), true);
  assert.deepEqual(writes[0], {operation: 'capture', payload: {request_id: 'request-1', ...CAPTURE}});
  assert.equal(flow.current, 'priorities');
});

test('acceptance envelope carries accepted and draft CAS without changing ratings', async () => {
  const {flow, writes} = harness();
  flow.edit('priorities', {urgency: 7, importance: 8});
  assert.equal(await flow.save('priorities'), true);
  assert.deepEqual(writes[0].payload, {request_id: 'request-1', idea_id: IDEA,
    expected_revision: 1, expected_draft_version: 0, step: 'priorities',
    fields: {urgency: 7, importance: 8}, proposal_id: null, expected_backlog_revision: null});
  assert.equal(flow.status('priorities'), 'saved');
  assert.equal(flow.current, 'method');
});

test('pause persists a partial draft with no accepted revision or green check', async () => {
  const {flow, writes} = harness();
  flow.edit('priorities', {urgency: 7, importance: null});
  assert.equal(await flow.pause(), true);
  assert.equal(flow.paused, true);
  assert.equal(writes[0].operation, 'draft');
  assert.equal(writes[0].payload.fields.importance, null);
  assert.equal(flow.state.revision, 1);
  assert.equal(flow.state.draft_version, 1);
  assert.equal(flow.status('priorities'), 'unsaved');
});

test('incomplete capture can pause honestly in memory without a fake save', async () => {
  const initial = state({idea_id: null, revision: 0, current_step: 'capture'});
  initial.steps.capture = {status: 'current', accepted_revision: null, evidence_id: null};
  initial.accepted.capture = null;
  const {flow, writes} = harness(initial);
  flow.edit('capture', {raw_text: 'unfinished', workspace: {name: '', path: '', confirmed: false}});
  assert.equal(await flow.pause(), true);
  assert.match(flow.message, /not saved/);
  assert.equal(flow.dirty.has('capture'), true);
  assert.deepEqual(writes, []);
});

test('failed save preserves buffer and explicit retry reuses exact request envelope', async () => {
  const {flow, api, writes} = harness();
  const write = api.write;
  let rejected = false;
  api.write = async (operation, payload) => {
    if (!rejected) { rejected = true; writes.push({operation, payload: copy(payload)}); throw new ApiError('store_busy', 503); }
    return write(operation, payload);
  };
  flow.edit('priorities', {urgency: 4, importance: 8});
  assert.equal(await flow.save('priorities'), false);
  assert.equal(flow.status('priorities'), 'unsaved');
  assert.deepEqual(flow.buffers.priorities, {urgency: 4, importance: 8});
  assert.equal(await flow.retry(), true);
  assert.deepEqual(writes[0], writes[1]);
});

test('conflict reload updates counters but keeps answers until explicit reapply', async () => {
  const {flow, api, current} = harness();
  const write = api.write;
  api.write = async () => { throw new ApiError('stale_revision', 409, {write_state: 'not_applied'}); };
  flow.edit('priorities', {urgency: 3, importance: 9});
  assert.equal(await flow.save('priorities'), false);
  current.revision = 4;
  await flow.reloadKeepingAnswers();
  assert.deepEqual(flow.buffers.priorities, {urgency: 3, importance: 9});
  assert.equal(flow.state.revision, 4);
  api.write = write;
  assert.equal(await flow.save('priorities'), true);
});

test('lost response is reconciled immediately without a second write', async () => {
  const {flow, api, writes} = harness();
  const write = api.write;
  api.write = async (...args) => { await write(...args); throw new ApiError('connection_lost', 0, {}, true); };
  flow.edit('priorities', {urgency: 4, importance: 8});
  assert.equal(await flow.save('priorities'), true);
  assert.equal(writes.length, 1);
  assert.equal(flow.status('priorities'), 'saved');
  assert.equal(flow.pending, null);
});

test('missing receipt retry rereads state after the new write, not the earlier reconciliation', async () => {
  const {flow, api, writes, reads} = harness();
  const write = api.write;
  let first = true;
  api.write = async (...args) => {
    if (first) { first = false; throw new ApiError('connection_lost', 0, {}, true); }
    return write(...args);
  };
  flow.edit('priorities', {urgency: 5, importance: 8});
  assert.equal(await flow.save('priorities'), false);
  assert.equal(await flow.retry(), true);
  assert.equal(writes.length, 1);
  assert.equal(flow.state.revision, 2);
  assert.equal(reads.at(-1).revision, 2);
});

test('newer edits survive an ambiguous request resolving older fields', async () => {
  const {flow, api, current, receipts} = harness();
  const write = api.write;
  let online = false;
  api.write = async (...args) => { await write(...args); throw new ApiError('connection_lost', 0, {}, true); };
  api.reconcile = async id => {
    if (!online) throw new ApiError('connection_lost');
    return {result: copy(receipts.get(id)), state: copy(current)};
  };
  flow.edit('priorities', {urgency: 4, importance: 8});
  await flow.save('priorities');
  flow.edit('priorities', {urgency: 7, importance: 8});
  online = true;
  assert.equal(await flow.retry(), true);
  assert.deepEqual(flow.buffers.priorities, {urgency: 7, importance: 8});
  assert.equal(flow.status('priorities'), 'unsaved');
  assert.equal(flow.dirty.has('priorities'), true);
  assert.match(flow.message, /Unsaved changes remain/);
  assert.equal(flow.current, 'priorities');
});

test('committed uncertainty never blindly sends another mutation', async () => {
  const {flow, api, writes, current} = harness();
  api.write = async (operation, payload) => { writes.push({operation, payload}); throw new ApiError('durability_uncertain', 500, {committed: true}, true); };
  api.reconcile = async () => ({result: {ok: false, write_state: 'committed_uncertain'}, state: copy(current)});
  flow.edit('priorities', {urgency: 2, importance: 9});
  assert.equal(await flow.save('priorities'), false);
  assert.equal(await flow.retry(), false);
  assert.equal(writes.length, 1);
  assert.equal(flow.status('priorities'), 'unsaved');
});

test('a post-write read failure also forces reconciliation', async () => {
  const {flow, api, writes, current, receipts} = harness();
  api.state = async () => { throw new ApiError('connection_lost'); };
  api.reconcile = async id => ({result: copy(receipts.get(id)), state: copy(current)});
  flow.edit('priorities', {urgency: 2, importance: 9});
  assert.equal(await flow.save('priorities'), true);
  assert.equal(writes.length, 1);
  assert.equal(flow.state.revision, 2);
});

function fakeResponse(data, status = 200) { return {ok: status >= 200 && status < 300, status, json: async () => copy(data)}; }
test('reload session gets CSRF before mutations; same-origin cookies/no-store only', async () => {
  const calls = [];
  const api = new IdeaApi(async (url, options) => {
    calls.push({url, options});
    return fakeResponse(url.endsWith('session') ? {ok: true, code: 'ok', binding_id: BINDING, session_id: SESSION, csrf_token: 'fixture-only'} : {ok: true, code: 'ok'});
  });
  await assert.rejects(api.write('draft', {}), error => error.status === 401);
  api.pinBinding(BINDING);
  await api.session();
  await api.write('draft', {request_id: 'fixture-request'});
  assert.equal(calls[0].url, '/api/v1/session');
  assert.equal(calls[1].options.headers['X-CSRF-Token'], 'fixture-only');
  assert.equal(calls[1].options.credentials, 'same-origin');
  assert.equal(calls[1].options.cache, 'no-store');
});

test('pairing sends exact code once then reads cookie-bound session for CSRF', async () => {
  const calls = [];
  const api = new IdeaApi(async (url, options) => {
    calls.push({url, options});
    return fakeResponse({ok: true, code: 'ok', binding_id: BINDING, session_id: SESSION, csrf_token: 'fixture-only', tab_secret: 'fixture-tab'});
  });
  await api.pair('one-time-fixture');
  assert.deepEqual(calls.map(call => call.url), ['/api/v1/pair', '/api/v1/session']);
  assert.deepEqual(JSON.parse(calls[0].options.body), {code: 'one-time-fixture'});
  assert.equal(calls[0].options.headers['X-CSRF-Token'], undefined);
});

test('pairing replay is not retried and invalidates in-memory credential', async () => {
  let count = 0;
  const api = new IdeaApi(async () => { count++; return fakeResponse({ok: false, code: 'pairing_replay_session_invalidated'}, 401); });
  api.csrf = 'old-fixture';
  await assert.rejects(api.pair('redeemed-fixture'), error => error.code === 'pairing_replay_session_invalidated');
  assert.equal(count, 1);
  assert.equal(api.csrf, null);
});

test('reconciliation reads missing receipt and state with no automatic write', async () => {
  const calls = [];
  const api = new IdeaApi(async url => {
    calls.push(url);
    return url.includes('requests/') ? fakeResponse({ok: false, code: 'request_not_found'}, 404) : fakeResponse(state());
  });
  api.pinBinding(BINDING);
  const resolved = await api.reconcile('fixture/request', IDEA);
  assert.equal(resolved.result, null);
  assert.deepEqual(calls, ['/api/v1/requests/fixture%2Frequest', '/api/v1/state?idea_id=' + IDEA]);
});

test('network failure and committed:true retain ambiguity; malformed responses fail closed', async () => {
  const network = new IdeaApi(async () => { throw new Error('offline'); });
  network.pinBinding(BINDING);
  network.csrf = 'fixture';
  await assert.rejects(network.write('capture', {}), error => error.uncertain === true);
  const uncertain = new IdeaApi(async () => fakeResponse({ok: false, code: 'durability_uncertain', committed: true, write_state: 'committed_uncertain'}, 500));
  uncertain.pinBinding(BINDING);
  uncertain.csrf = 'fixture';
  await assert.rejects(uncertain.write('draft', {}), error => error.uncertain === true);
  const malformed = new IdeaApi(async () => fakeResponse({not: 'an envelope'}));
  malformed.pinBinding(BINDING);
  await assert.rejects(malformed.state(), error => error.code === 'invalid_response');
});

// The static shell may load exactly these things and nothing else. Anything added must be argued into this list.
const SHELL_LINKS=['./assets/bwpm/bundle.css','./styles.css'];
const SHELL_SCRIPTS=['./app.js'];
const SHELL_IMAGES=['./assets/logo.svg'];
function assertStaticShell({app,css,html}) {
  assert.doesNotMatch(app, /innerHTML|sessionStorage|eval\(|document\.write|setAttribute\('style'|\.cssText/);
  // The only storage use is the theme word under its one fixed key.
  const storage=app.split('\n').filter(line=>/localStorage/.test(line));
  assert.ok(storage.every(line=>line.includes('THEME_KEY')), 'localStorage is only for the theme key');
  assert.doesNotMatch(app.split('\n').filter(line=>!/localStorage/.test(line)).join('\n'), /localStorage|agent_token|Authorization/);
  assert.doesNotMatch(css + html, /dc-runtime|tokens\.json|@import|javascript:|srcdoc|<iframe|<object|<embed|<base\b/i);
  assert.doesNotMatch(css + html, /https?:\/\//i);
  assert.deepEqual([...html.matchAll(/<link\b[^>]*\bhref="([^"]*)"/g)].map(m => m[1]), SHELL_LINKS);
  assert.equal([...html.matchAll(/<link\b/g)].length, SHELL_LINKS.length);
  assert.deepEqual([...html.matchAll(/<script\b[^>]*\bsrc="([^"]*)"/g)].map(m => m[1]), SHELL_SCRIPTS);
  assert.equal([...html.matchAll(/<script\b/g)].length, SHELL_SCRIPTS.length, 'no inline script');
  assert.deepEqual([...html.matchAll(/<img\b[^>]*\bsrc="([^"]*)"/g)].map(m => m[1]), SHELL_IMAGES);
  assert.equal([...html.matchAll(/<img\b/g)].length, SHELL_IMAGES.length);
  assert.doesNotMatch(html, /\son[a-z]+\s*=/i, 'no inline event handlers');
  assert.doesNotMatch(html, /\sstyle\s*=|<style\b/i, 'no inline style');
  // Stylesheet urls: only the packaged local fonts.
  for (const [, target] of css.matchAll(/url\(\s*["']?([^"')]+)/g)) assert.match(target, /^\.\/assets\/fonts\/[A-Za-z-]+\.woff2$/);
  assert.match(app, /aria-pressed/);
  assert.match(app, /aria-expanded/);
  assert.match(css, /prefers-reduced-motion/);
  assert.match(css, /min-width: 44px/);
  assert.match(html, /type="module"/);
  // The APIV line stays a list for assistive technology.
  const apiv=/<ol class="g-apiv"([^>]*)>(.*?)<\/ol>/.exec(html);
  assert.ok(apiv, 'APIV is an ol.g-apiv');
  const items=[...apiv[2].matchAll(/<li\b([^>]*)>(.*?)<\/li>/g)];
  assert.equal(items.length, 4);
  assert.match(items[0][1], /aria-current="step"/);
  assert.match(items[0][2].replace(/<[^>]*>/g, ''), /^Align.*\/glitch-plan/);
  assert.doesNotMatch(html, /class="g-apiv"[^>]*role="img"/);
  for (const [, sel, body] of css.matchAll(/([^{}]*\.g-apiv[^{}]*)\{([^}]*)\}/g)) assert.doesNotMatch(body, /display\s*:\s*none/, 'APIV must not be display:none: ' + sel.trim());
}

test('original static shell has no unsafe HTML injection, assets or persisted secrets', async () => {
  const app = await readFile(new URL('app.js', web), 'utf8');
  const css = await readFile(new URL('styles.css', web), 'utf8');
  const html = await readFile(new URL('index.html', web), 'utf8');
  assertStaticShell({app, css, html});
});

test('the static shell check refuses each kind of unsafe or extra asset', async () => {
  const app = await readFile(new URL('app.js', web), 'utf8');
  const css = await readFile(new URL('styles.css', web), 'utf8');
  const html = await readFile(new URL('index.html', web), 'utf8');
  const bad = {
    'a remote stylesheet': {html: html.replace('</head>', '<link rel="stylesheet" href="https://cdn.example/x.css"></head>')},
    'a second local stylesheet': {html: html.replace('</head>', '<link rel="stylesheet" href="./extra.css"></head>')},
    'an inline script': {html: html.replace('</head>', '<script>alert(1)</script></head>')},
    'another script': {html: html.replace('</head>', '<script type="module" src="./other.js"></script></head>')},
    'another image': {html: html.replace('</body>', '<img src="./assets/other.png" alt=""></body>')},
    'an inline handler': {html: html.replace('<body>', '<body onload="x()">')},
    'an inline style attribute': {html: html.replace('<body>', '<body style="color:red">')},
    'an iframe srcdoc': {html: html.replace('</body>', '<iframe srcdoc="<b>x</b>"></iframe></body>')},
    'innerHTML in the app': {app: app + '\nnode.innerHTML = x;'},
    'a style attribute set from the app': {app: app + "\nnode.setAttribute('style', 'x');"},
    'a persisted secret': {app: app + "\nlocalStorage.setItem('agent_token', t);"},
    'sessionStorage in the app': {app: app + '\nsessionStorage.setItem("a","b");'},
    'a remote font': {css: css + '\n@font-face { src: url("https://x.example/f.woff2"); }'},
    'a stylesheet import': {css: '@import "./other.css";\n' + css},
  };
  assert.doesNotThrow(() => assertStaticShell({app, css, html}));
  for (const [name, patch] of Object.entries(bad)) assert.throws(() => assertStaticShell({app, css, html, ...patch}), undefined, name + ' must be refused');
});

test('clean pause persists original selection with navigation envelope and no decision change', async () => {
  const {flow, writes} = harness();
  flow.open('capture');
  assert.equal(await flow.pause(), true);
  assert.equal(flow.paused, true);
  assert.deepEqual(writes, [{operation: 'navigate', payload: {request_id: 'request-1', idea_id: IDEA,
    expected_revision: 1, expected_draft_version: 0, step: 'capture'}}]);
  assert.equal(flow.state.revision, 1);
  assert.equal(flow.state.draft_version, 0);
  const reloaded = harness(flow.state).flow;
  assert.equal(reloaded.current, 'capture');
});

test('pause saves dirty buffers then restores originally selected panel', async () => {
  const {flow, writes} = harness();
  flow.edit('priorities', {urgency: 7, importance: null});
  flow.open('capture');
  assert.equal(await flow.pause(), true);
  assert.deepEqual(writes.map(write => write.operation), ['draft', 'navigate']);
  assert.equal(writes[1].payload.expected_draft_version, 1);
  assert.equal(writes[1].payload.step, 'capture');
  assert.equal(flow.current, 'capture');
  assert.equal(flow.state.current_step, 'capture');
  assert.equal(flow.state.revision, 1);
});

test('failed pause navigation keeps selection and buffers and retries the same key', async () => {
  const {flow, api, writes} = harness();
  flow.open('capture');
  const write = api.write;
  let first = true;
  api.write = async (operation, payload) => {
    if (first) { first = false; writes.push({operation, payload: copy(payload)}); throw new ApiError('store_busy', 503); }
    return write(operation, payload);
  };
  const buffers = copy(flow.buffers);
  assert.equal(await flow.pause(), false);
  assert.equal(flow.paused, false);
  assert.equal(flow.current, 'capture');
  assert.deepEqual(flow.buffers, buffers);
  assert.equal(await flow.retry(), true);
  assert.equal(flow.paused, true);
  assert.deepEqual(writes[0], writes[1]);
});

test('uncertain pause never claims completion and reconciles without a second mutation', async () => {
  const {flow, api, writes, receipts, current} = harness();
  flow.open('capture');
  const write = api.write;
  let online = false;
  api.write = async (...args) => { await write(...args); throw new ApiError('connection_lost', 0, {}, true); };
  api.reconcile = async id => {
    if (!online) throw new ApiError('connection_lost');
    return {result: copy(receipts.get(id)), state: copy(current)};
  };
  assert.equal(await flow.pause(), false);
  assert.equal(flow.paused, false);
  assert.equal(flow.current, 'capture');
  online = true;
  assert.equal(await flow.retry(), true);
  assert.equal(flow.paused, true);
  assert.equal(writes.length, 1);
});

test('navigate is a browser write requiring the existing cookie and CSRF seam', async () => {
  const calls = [];
  const api = new IdeaApi(async (url, options) => { calls.push({url, options}); return fakeResponse({ok: true, code: 'ok'}); });
  await assert.rejects(api.write('navigate', {}), error => error.status === 401);
  api.pinBinding(BINDING);
  api.csrf = 'fixture-only';
  await api.write('navigate', {request_id: 'pause'});
  assert.equal(calls[0].url, '/api/v1/navigate');
  assert.equal(calls[0].options.headers['X-CSRF-Token'], 'fixture-only');
});

test('multiple dirty drafts cannot replace the originally selected pause step', async () => {
  const {flow, writes} = harness();
  flow.edit('capture', {...CAPTURE, raw_text: 'Changed words'});
  flow.edit('priorities', {urgency: 7, importance: null});
  assert.equal(flow.current, 'priorities');
  assert.equal(await flow.pause(), true);
  assert.deepEqual(writes.map(write => write.operation), ['draft', 'draft', 'navigate']);
  assert.equal(writes[2].payload.step, 'priorities');
  assert.equal(writes[2].payload.expected_draft_version, 2);
  assert.equal(flow.current, 'priorities');
  assert.equal(flow.paused, true);
});

test('failed dirty save stops pause before navigation and keeps the original selection', async () => {
  const {flow, api, writes} = harness();
  flow.edit('priorities', {urgency: 3, importance: null});
  flow.open('capture');
  api.write = async (operation, payload) => { writes.push({operation, payload: copy(payload)}); throw new ApiError('stale_draft_version', 409); };
  assert.equal(await flow.pause(), false);
  assert.equal(flow.paused, false);
  assert.equal(flow.current, 'capture');
  assert.deepEqual(writes.map(write => write.operation), ['draft']);
  assert.deepEqual(flow.buffers.priorities, {urgency: 3, importance: null});
  assert.equal(flow.dirty.has('priorities'), true);
});

const SWAPPED = ['capture', 'priorities', 'discovery', 'method', 'exploration', 'visualize', 'assess', 'review'];
test('stepsFor takes the server order, accepts only the Methods/Discovery swap, and falls back otherwise', () => {
  assert.deepEqual(stepsFor(SWAPPED).map(step => step.key), SWAPPED);
  assert.equal(stepsFor(SWAPPED)[2].title, 'Discovery');
  assert.deepEqual(stepsFor(null), [...STEPS]);
  const fallback = [...STEPS];
  for (const bad of [undefined, 'method', [], STEPS.map(s => s.key).slice(1), [...SWAPPED, 'shape'],
    ['priorities', 'capture', ...SWAPPED.slice(2)], [...SWAPPED.slice(0, 4), 'visualize', 'exploration', ...SWAPPED.slice(6)],
    SWAPPED.map(key => key === 'method' ? 'shape' : key), [...SWAPPED.slice(0, 7), 'capture']]) {
    assert.deepEqual(stepsFor(bad), fallback);
  }
});
test('the Flow orders its steps from the state step_order', async () => {
  const swapped = state({step_order: SWAPPED, current_step: 'discovery'});
  const {flow} = harness(swapped);
  assert.deepEqual(flow.steps.map(step => step.key), SWAPPED);
  flow.load(state({step_order: ['nonsense']}));
  assert.deepEqual(flow.steps.map(step => step.key), STEPS.map(step => step.key));
  // canOpen uses the Flow's own order: with Discovery first it opens before Methods.
  const first = harness(state({step_order: SWAPPED, current_step: 'priorities'}));
  first.flow.state.steps.priorities.status = 'saved';
  first.flow.load({...first.flow.state, steps: {...first.flow.state.steps, priorities: {status: 'saved', accepted_revision: 1, evidence_id: 'p'}}});
  assert.equal(first.flow.canOpen('discovery'), true);
  assert.equal(first.flow.canOpen('method'), false);
  const plain = harness(state({current_step: 'priorities'}));
  plain.flow.load({...plain.flow.state, steps: {...plain.flow.state.steps, priorities: {status: 'saved', accepted_revision: 1, evidence_id: 'p'}}});
  assert.equal(plain.flow.canOpen('method'), true);
  assert.equal(plain.flow.canOpen('discovery'), false);
});
test('DEPENDENTS mirror derive_dependencies under both orders', () => {
  const later = ['visualize', 'assess', 'review'];
  assert.deepEqual(dependentsFor(null), {
    capture: ['method', 'discovery', 'exploration', ...later], priorities: ['method', 'discovery', 'exploration', ...later],
    method: ['discovery', 'exploration', ...later], discovery: ['exploration', ...later],
    exploration: later, visualize: ['review'], assess: ['review'], review: []});
  assert.deepEqual(dependentsFor(SWAPPED), {
    capture: ['discovery', 'method', 'exploration', ...later], priorities: ['discovery', 'method', 'exploration', ...later],
    discovery: ['exploration', ...later], method: ['exploration', ...later],
    exploration: later, visualize: ['review'], assess: ['review'], review: []});
  // A changed Method makes Discovery stale in the default order, but not when Discovery comes first.
  const stale = (order, source, target) => {
    const initial = state({step_order: order});
    for (const key of ['method', 'discovery']) initial.steps[key] = {status: 'saved', accepted_revision: 1, evidence_id: key};
    const {flow} = harness(initial);
    flow.edit('method', {...flow.buffers.method, reason: 'changed'});
    return flow.status(target);
  };
  assert.equal(stale(null, 'method', 'discovery'), 'review-needed');
  assert.equal(stale(SWAPPED, 'method', 'discovery'), 'saved');
});

const GOOD_DISCOVERY = {problem: 'p', audience: 'a', workaround: 'w', evidence: 'e', kill_criteria: 'k',
  challenges: [{challenge: 'c', response: 'r'}],
  prior_art: [{name: 'Tapeo', link: 'https://example.test/tapeo', does: 'Seals lids', differs: 'Ours is reusable', licence: 'MIT'}], prior_art_none: false, prior_art_searched: ''};
const SKETCH = {title: 't', why_next: '', done_when: 'd', method: null};
const GOOD_EXPLORATION = {outcome: 'o', alternatives: [{route: 'r', reason: 'x'}], assumptions: [], scope: 'capability',
  scope_reason: 's', next_slice: 'n', learning: [], investment: null, experiment: null, sketch: [SKETCH]};
test('default buffers match the server step fields exactly', () => {
  const {flow} = harness();
  assert.deepEqual(Object.keys(flow.buffers.discovery).sort(), ['audience', 'challenges', 'evidence', 'kill_criteria', 'prior_art', 'prior_art_none', 'prior_art_searched', 'problem', 'workaround']);
  assert.deepEqual(Object.keys(flow.buffers.exploration).sort(), Object.keys(GOOD_EXPLORATION).sort());
  assert.deepEqual(flow.buffers.exploration, {outcome: '', alternatives: [], assumptions: [], scope: null, scope_reason: '', next_slice: '',
    learning: [], investment: null, experiment: null, sketch: []});
  assert.deepEqual(Object.keys(flow.buffers.method).sort(), ['memory', 'reason', 'selection']);
  assert.equal(flow.buffers.shape, undefined);
});
test('discovery acceptance needs five words and one meaningful challenge', () => {
  assert.equal(validDiscovery(GOOD_DISCOVERY), true);
  assert.equal(validDiscovery(flowless(GOOD_DISCOVERY, {problem: '  '})), false);
  assert.equal(validDiscovery(flowless(GOOD_DISCOVERY, {kill_criteria: ''})), false);
  assert.equal(validDiscovery(flowless(GOOD_DISCOVERY, {challenges: []})), false);
  assert.equal(validDiscovery(flowless(GOOD_DISCOVERY, {challenges: [{challenge: 'c', response: ' '}]})), false);
  assert.equal(validDiscovery({...GOOD_DISCOVERY, extra: 1}), false);
  assert.equal(discoveryFields({problem: 'only'}, true), true);
  assert.equal(discoveryFields({problem: 5}, true), false);
});
function flowless(base, change) { return {...copy(base), ...change}; }
test('exploration acceptance: sketch limits and the accepted Method decides investment/experiment', () => {
  assert.equal(validExploration(GOOD_EXPLORATION), true);
  assert.equal(validExploration(GOOD_EXPLORATION, 'bounded-plan'), true);
  assert.equal(validExploration(flowless(GOOD_EXPLORATION, {sketch: []})), false);
  assert.equal(validExploration(flowless(GOOD_EXPLORATION, {sketch: [{...SKETCH, title: ' '}]})), false);
  assert.equal(validExploration(flowless(GOOD_EXPLORATION, {sketch: [{...SKETCH, done_when: ''}]})), false);
  assert.equal(validExploration(flowless(GOOD_EXPLORATION, {sketch: [{...SKETCH, method: 'nope'}]})), false);
  assert.equal(validExploration(flowless(GOOD_EXPLORATION, {sketch: [{...SKETCH, method: 'adaptive-slices'}]})), true);
  assert.equal(validExploration(flowless(GOOD_EXPLORATION, {sketch: Array(5).fill(SKETCH)})), true);
  assert.equal(validExploration(flowless(GOOD_EXPLORATION, {sketch: Array(6).fill(SKETCH)})), false);
  assert.equal(validExploration(flowless(GOOD_EXPLORATION, {scope: 'huge'})), false);
  assert.equal(validExploration(flowless(GOOD_EXPLORATION, {alternatives: []})), false);
  const investment = {cap: 3, unit: 'days', boundary: 'one slice'};
  assert.equal(validExploration(GOOD_EXPLORATION, 'appetite-led'), false);
  assert.equal(validExploration(flowless(GOOD_EXPLORATION, {investment}), 'appetite-led'), true);
  assert.equal(validExploration(flowless(GOOD_EXPLORATION, {investment: {...investment, cap: 0}}), 'appetite-led'), false);
  assert.equal(validExploration(flowless(GOOD_EXPLORATION, {investment: {...investment, unit: ' '}}), 'appetite-led'), false);
  assert.equal(validExploration(flowless(GOOD_EXPLORATION, {investment}), 'bounded-plan'), false);
  const experiment = {question: 'q', evidence: 'e', success_criterion: 's', stop_rule: 'r'};
  assert.equal(validExploration(GOOD_EXPLORATION, 'experiment-led'), false);
  assert.equal(validExploration(flowless(GOOD_EXPLORATION, {experiment}), 'experiment-led'), true);
  assert.equal(validExploration(flowless(GOOD_EXPLORATION, {experiment: {...experiment, stop_rule: ''}}), 'experiment-led'), false);
  assert.equal(validExploration(flowless(GOOD_EXPLORATION, {experiment}), 'appetite-led'), false);
  assert.equal(explorationFields({sketch: Array(6).fill(SKETCH)}, true), false);
  assert.equal(explorationFields({outcome: 'only'}, true), true);
});
test('method acceptance: reason optional, memory varied and preferred_method rule, no investment', () => {
  const memory = {status: 'unavailable', sources: [], rationale: null, preferred_method: null};
  const good = {selection: 'bounded-plan', reason: '', memory};
  assert.equal(validMethod(good), true);
  assert.equal(validMethod({...good, reason: null}), true);
  assert.equal(validMethod({...good, investment: null, experiment: null}), false);
  assert.equal(validMethod({...good, selection: null}), false);
  const found = {status: 'found', sources: ['note'], rationale: 'because', preferred_method: 'adaptive-slices'};
  assert.equal(validMethod({...good, memory: found}), true);
  assert.equal(validMethod({...good, memory: {...found, preferred_method: null}}), false);
  assert.equal(validMethod({...good, memory: {...memory, preferred_method: 'bounded-plan'}}), false);
  assert.equal(validMethod({...good, memory: {status: 'varied', sources: ['note'], rationale: 'mixed', preferred_method: null}}), true);
  assert.equal(validMethod({...good, memory: {status: 'varied', sources: [], rationale: null, preferred_method: 'bounded-plan'}}), false);
  assert.equal(validMethod({...good, memory: {status: 'bogus', sources: [], rationale: null}}), false);
});
test('FILL_KEYS mirror the server: every step field for discovery and exploration, memory only for method', () => {
  assert.deepEqual([...FILL_KEYS.discovery].sort(), Object.keys(GOOD_DISCOVERY).sort());
  assert.deepEqual([...FILL_KEYS.exploration].sort(), Object.keys(GOOD_EXPLORATION).sort());
  assert.deepEqual(FILL_KEYS.method, ['memory']);
  assert.deepEqual(FILL_KEYS.assessment, ['assessment', 'proposed_position']);
  assert.equal(FILL_KEYS.shape, undefined);
});
