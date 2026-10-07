// Actual renderers/app/Flow/API in a bounded DOM; no browser proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web = new URL('../../glitch-idea/web/', import.meta.url);
const source = name => readFile(new URL(name, web), 'utf8');
const url = text => 'data:text/javascript;base64,' + Buffer.from(text).toString('base64');
const apiUrl = url(await source('api.js')), foldsUrl = url(await source('folds.js'));
const reviewUrl = url((await source('steps/review.js')).replace("'../folds.js'", JSON.stringify(foldsUrl)));
const {render: review, copyCurrent} = await import(reviewUrl);
const {render: ideas} = await import(url((await source('ideas.js')).replace("'./steps/review.js'", JSON.stringify(reviewUrl)).replace("'./folds.js'", JSON.stringify(foldsUrl))));
const {Flow, STEPS} = await import(foldsUrl);
const {IdeaApi} = await import(apiUrl);
const appSource = (await source('app.js')).replace("'./api.js'", JSON.stringify(apiUrl)).replace("'./folds.js'", JSON.stringify(foldsUrl));
const {startApp, registerStep, registerIdeas, loadStepModules} = await import(url(appSource));
const IDEA = 'idea_' + '1'.repeat(32), OTHER = 'idea_' + '2'.repeat(32);
const SESSION = 'session_' + '3'.repeat(32), BINDING = 'binding_' + '4'.repeat(32);
const HASH = 'a'.repeat(64), DIGEST = 'b'.repeat(64);
const clone = value => structuredClone(value);
const deferred = () => {let resolve; const promise = new Promise(yes => {resolve = yes;}); return {promise, resolve};};
const packet = (id = IDEA) => ({handoff_id: 'handoff_' + '5'.repeat(32), source_revision: 6, source_digest: DIGEST,
  path: '/store/history/' + id + '/metadata/' + HASH + '.md', sha256: HASH,
  prompt: '/glitch-plan\nLiteral <script> and 💡 prompt\n' + 'Full prompt line\n'.repeat(300),
  source_files: {detail: {path: '/store/' + id + '.md', sha256: DIGEST}, index: {path: '/store/IDEAS.md', sha256: DIGEST},
    revision: {path: '/store/history/' + id + '/r6.md', sha256: DIGEST}}, design_set: null});
function state(id = IDEA, ready = true) {
  const p = id && ready ? packet(id) : null;
  return {ok: true, code: 'ok', session_id: SESSION, idea_id: id, idea_status: id ? 'active' : null,
    revision: id ? 6 : 0, draft_version: id ? 3 : 0, backlog_revision: 2, current_step: id ? 'review' : 'capture',
    agent_status: 'disconnected', capabilities: {handoff: true}, handoff: p,
    handoff_status: {available: Boolean(p), code: p ? 'ok' : id ? 'not_ready' : 'no_selection'},
    steps: Object.fromEntries(STEPS.map(({key}) => [key, {status: id ? (key === 'review' && !p ? 'current' : 'saved') : key === 'capture' ? 'current' : 'todo',
      accepted_revision: id && (key !== 'review' || p) ? 6 : null, evidence_id: id && (key !== 'review' || p) ? (key === 'review' ? p.handoff_id : 'fixture-' + key) : null}])),
    accepted: {capture: id ? {raw_text: 'Literal <img onerror=x> idea 💡', workspace: {name: 'Fixture', path: '/workspace', confirmed: true}} : null,
      review: p ? {handoff_id: p.handoff_id, source_revision: 6} : null}, drafts: {}, draft: null};
}
const response = (data, status = 200) => ({ok: status < 300, status, json: async () => clone(data)});
function server({ready = true} = {}) {
  let selected = IDEA;
  const saved = new Map([[IDEA, state(IDEA, ready)], [OTHER, state(OTHER)], [null, state(null)]]);
  const calls = [], faults = {};
  const api = new IdeaApi(async (path, options) => {
    const payload = options.body ? JSON.parse(options.body) : null; calls.push({path, payload});
    if (path === '/api/v1/session') return response({ok: true, code: 'ok', binding_id: BINDING, session_id: SESSION, csrf_token: 'fixture-only', agent_status: 'disconnected'});
    if (path === '/api/v1/transport') return response({ok: true, code: 'ok'});
    if (path.startsWith('/api/v1/state')) {
      const id = new URL(path, 'http://fixture').searchParams.get('idea_id') ?? selected;
      return faults.read ? faults.read(clone(saved.get(id))) : response(saved.get(id));
    }
    if (path === '/api/v1/ideas') {
      const data = {ok: true, code: 'ok', backlog_revision: 2, total: 2, ideas: [OTHER, IDEA].map((id, index) => ({idea_id: id,
        revision: 6, position: index + 1, title: id === IDEA ? '<script>Literal idea 💡' : 'First in accepted order',
        status: saved.get(id).handoff_status.available ? 'ready-to-plan' : 'review-needed', method: null, updated: '2026-10-02', detail_path: '/store/' + id + '.md'}))};
      return faults.ideas ? faults.ideas(data) : response(data);
    }
    if (path === '/api/v1/selection') {
      selected = payload.idea_id;
      if (faults.selectionLost) throw new Error('lost');
      const s = saved.get(selected);
      return response({ok: true, code: 'ok', session_id: SESSION, idea_id: selected,
        revision: s.revision, draft_version: s.draft_version, backlog_revision: s.backlog_revision});
    }
    if (path === '/api/v1/handoff') {
      const s = saved.get(selected); s.handoff = packet(selected); s.handoff_status = {available: true, code: 'ok'};
      s.steps.review = {status: 'saved', accepted_revision: 6, evidence_id: s.handoff.handoff_id};
      s.accepted.review = {handoff_id: s.handoff.handoff_id, source_revision: 6};
      return response({ok: true, code: 'ok', write_state: 'applied', request_id: payload.request_id, idea_id: selected,
        revision: 6, draft_version: 3, backlog_revision: 2, handoff_index: 1, handoff_id: s.handoff.handoff_id,
        path: 'history/' + selected + '/metadata/' + HASH + '.md', sha256: HASH, handoff: s.handoff, handoff_current: true});
    }
    throw Error('Unexpected fixture route');
  }, 1000, BINDING);
  api.sessionId = SESSION; api.csrf = 'fixture-only';
  return {api, calls, faults, saved};
}
class Node {
  constructor(tag = 'div', text = '') {this.tagName = tag.toUpperCase(); this.textContent = text; this.children = []; this.dataset = {}; this.listeners = {}; this.disabled = false; this.value = ''; this.scrollTop = 0; this.scrollLeft = 0;}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this.children = nodes;}
  setAttribute(key, value) {this[key] = value;}
  addEventListener(key, action) {this.listeners[key] = action;}
  all() {return [this, ...this.children.flatMap(child => child.all())];}
  async click() {if (!this.disabled) await this.listeners.click?.({target: this});}
  focus() {if (globalThis.document) document.activeElement = this;}
  setSelectionRange(start, end) {this.selectionStart = start; this.selectionEnd = end;}
}
const element = (tag, text = '', className = '') => {const node = new Node(tag, text); node.className = className; return node;};
const button = (text, action, className) => {const node = element('button', text, className); node.addEventListener('click', action); return node;};
function harness(options = {}) {
  const s = server(options), flow = new Flow(s.api, () => 'fixture-handoff', () => 0, null); flow.load(s.saved.get(IDEA));
  let body, foot, view = review;
  const ctx = {flow, api: s.api, element, button, connected: true, handle: action => async () => action(), clipboard: options.clipboard ?? {writeText: async () => {}}};
  ctx.isConnected = () => ctx.connected;
  const draw = () => {body = element('div'); foot = element('footer'); view({...ctx, body, foot});};
  flow.onChange = draw; draw();
  return {...s, flow, ctx, draw, renderIdeas: () => {view = ideas; draw();},
    get: id => [...body.all(), ...foot.all()].find(node => node.id === id), nodes: () => [...body.all(), ...foot.all()]};
}

test('Review renders full literal summary, immutable paths and prompt with labelled selectable text', () => {
  const h = harness(), p = h.flow.state.handoff;
  assert.equal(h.get('review-prompt').value, p.prompt); assert.equal(h.get('review-prompt').readOnly, true);
  assert.ok(h.nodes().some(n => n.tagName === 'LABEL' && n.htmlFor === 'review-prompt'));
  for (const text of [p.path, p.source_files.detail.path, '<img onerror=x>']) assert.ok(h.nodes().some(n => n.textContent.includes(text)));
  assert.equal(h.nodes().filter(n => ['SCRIPT', 'IMG', 'IFRAME'].includes(n.tagName)).length, 0);
  assert.equal(h.get('review-copy').disabled, false); assert.equal(h.get('review-manual-copy'), undefined);
});

test('Review summary reads a confirmed workspace as its name and path, never raw JSON', () => {
  const h = harness();
  h.flow.state.accepted.capture = {...h.flow.state.accepted.capture,
    workspace: {confirmed: true, name: 'demo <b>space</b>', path: '/absolute/demo'}};
  h.draw();
  const texts = h.nodes().map(n => n.textContent);
  assert.ok(texts.includes('demo <b>space</b>'));  // the Review redesign shows name and path as labelled rows
  assert.ok(texts.includes('/absolute/demo'));
  assert.ok(!texts.some(t => t.includes('"confirmed"')));
  assert.equal(h.nodes().filter(n => n.tagName === 'B').length, 0);
});

test('explicit generation uses handoff once; ordinary copy never generates, accepts or archives', async () => {
  const h = harness({ready: false}); assert.equal(h.get('review-copy'), undefined);
  await h.get('review-generate').click(); assert.equal(h.calls.filter(c => c.path === '/api/v1/handoff').length, 1);
  const counters = [h.flow.state.revision, h.flow.state.draft_version, h.flow.state.backlog_revision];
  await h.get('review-copy').click(); assert.equal(h.flow.view, 'ideas');
  assert.deepEqual([h.flow.state.revision, h.flow.state.draft_version, h.flow.state.backlog_revision], counters);
  assert.equal(h.calls.filter(c => c.payload !== null).length, 1); assert.equal(h.flow.state.idea_status, 'active');
});

test('clipboard denial retains the full prompt and returns to Ideas only after current explicit manual confirmation', async () => {
  const h = harness({clipboard: {writeText: async () => {throw Error('denied');}}}), prompt = h.flow.state.handoff.prompt;
  await h.get('review-copy').click(); assert.equal(h.flow.view, 'workflow'); assert.equal(h.get('review-prompt').value, prompt);
  assert.match(h.get('review-copy-status').textContent, /Clipboard unavailable/); assert.ok(h.get('review-manual-copy'));
  await h.get('review-manual-copy').click(); assert.equal(h.flow.view, 'ideas');
  assert.equal(h.calls.filter(c => c.payload !== null).length, 0);
});

test('completed clipboard/manual copy remains visible with named Ideas failure and full prompt, without retry or generation', async () => {
  for (const manual of [false, true]) for (const code of ['ideas_capacity', 'connection_lost', 'ideas_loading']) {
    let copied = 0;
    const h = harness({clipboard: {writeText: async () => {copied++; if (manual) throw Error('denied');}}});
    const original = h.flow.state.handoff.prompt;
    if (manual) await h.get('review-copy').click();
    if (code === 'ideas_loading') h.flow.ideasLoading = true;
    else h.faults.ideas = () => response({ok: false, code}, 503);
    await h.get(manual ? 'review-manual-copy' : 'review-copy').click();
    assert.equal(h.flow.view, 'workflow'); assert.equal(h.get('review-prompt').value, original);
    assert.ok(h.get('review-copy-status').textContent.startsWith(manual ? 'Copy confirmed.' : 'Prompt copied.'));
    assert.ok(h.get('review-copy-status').textContent.includes('Ideas list could not be read: ' + code));
    assert.match(h.get('review-copy-status').textContent, /explicitly/);
    assert.equal(h.get('review-manual-copy'), undefined); assert.equal(copied, 1);
    assert.equal(h.calls.filter(c => c.path === '/api/v1/ideas').length, code === 'ideas_loading' ? 0 : 1);
    assert.equal(h.calls.filter(c => c.payload !== null).length, 0);
  }
});

test('manual confirmation rejects a changed packet and never silently regenerates it', async () => {
  const h = harness({clipboard: {writeText: async () => {throw Error('denied');}}}); await h.get('review-copy').click();
  h.saved.get(IDEA).handoff.source_digest = 'c'.repeat(64);
  await h.get('review-manual-copy').click(); assert.equal(h.flow.view, 'workflow'); assert.equal(h.flow.error.code, 'stale_source');
  assert.equal(h.calls.filter(c => c.payload !== null).length, 0);
});

test('clipboard await rechecks dirty pending proposal paused disposal and selected idea before returning to Ideas', async () => {
  for (const change of [f => f.edit('capture', {raw_text: 'New human words'}), f => f.pending = {ambiguous: true},
    f => f.proposalPending = {key: 'exploration'}, f => f.paused = true, f => f.dispose(), f => f.state.idea_id = OTHER]) {
    const wait = deferred(), entered = deferred(), h = harness({clipboard: {writeText: async () => {entered.resolve(); await wait.promise;}}});
    const copying = h.get('review-copy').click(); await entered.promise; change(h.flow); wait.resolve(); await copying;
    assert.equal(h.flow.view, 'workflow'); assert.equal(h.calls.filter(c => c.payload !== null).length, 0);
  }
});

test('captured connection cannot authorize copy completion after pairing, credential loss or live disconnect', async () => {
  for (const change of [h => h.flow.refreshUnauthorized = true, h => h.api.csrf = null, h => h.ctx.connected = false]) {
    const wait = deferred(), entered = deferred(), h = harness({clipboard: {writeText: async () => {entered.resolve(); await wait.promise;}}});
    const copying = h.get('review-copy').click(); await entered.promise; change(h); wait.resolve(); await copying;
    assert.equal(h.flow.view, 'workflow'); assert.equal(h.calls.some(c => c.path === '/api/v1/ideas'), false);
  }
});

test('Ideas read await rejects local edits and fresh server source drift before successful-copy view switch', async () => {
  for (const mode of ['dirty', 'server']) {
    const entered = deferred(), wait = deferred(), h = harness();
    h.faults.ideas = async data => {entered.resolve(); await wait.promise; return response(data);};
    const copying = h.get('review-copy').click(); await entered.promise;
    if (mode === 'dirty') h.flow.edit('capture', {raw_text: 'Local edit'});
    else {h.saved.get(IDEA).handoff_status = {available: false, code: 'stale_source'}; h.saved.get(IDEA).steps.review.status = 'review-needed'; h.saved.get(IDEA).accepted.review = null;}
    wait.resolve(); await copying; assert.equal(h.flow.view, 'workflow');
  }
});

test('Ideas renders actual order/status/title and literal paths; explicit New and Open use selection only', async () => {
  const h = harness(); await h.flow.showIdeas(); h.renderIdeas();
  assert.deepEqual(h.get('ideas-list').children.map(n => n.dataset.ideaId), [OTHER, IDEA]);
  assert.ok(h.nodes().some(n => n.textContent === '<script>Literal idea 💡'));
  await h.get('ideas-open-' + OTHER).click(); assert.equal(h.flow.state.idea_id, OTHER); assert.equal(h.flow.current, 'review');
  h.renderIdeas(); await h.get('new-idea').click(); assert.equal(h.flow.state.idea_id, null); assert.equal(h.flow.current, 'capture');
  assert.equal(h.flow.buffers.capture.raw_text, ''); assert.deepEqual(h.calls.filter(c => c.payload).map(c => c.payload), [{idea_id: OTHER}, {idea_id: null}]);
});

test('Copy again explicitly opens exact row then rechecks packet and uses Review manual fallback without generation', async () => {
  const h = harness({clipboard: {writeText: async () => {throw Error('denied');}}}); await h.flow.showIdeas(); h.renderIdeas();
  await h.get('ideas-copy-again-' + OTHER).click(); assert.equal(h.flow.state.idea_id, OTHER); assert.equal(h.flow.view, 'workflow');
  review({...h.ctx, body: element('div'), foot: element('footer')});
  assert.deepEqual(h.calls.filter(c => c.payload).map(c => c.payload), [{idea_id: OTHER}]);
  // Switching the harness renderer displays the same per-Flow fallback state.
  const body = element('div'), foot = element('footer'); review({...h.ctx, body, foot});
  const manual = foot.all().find(n => n.id === 'review-manual-copy'); assert.ok(manual); await manual.click(); assert.equal(h.flow.view, 'ideas');
});

test('stale Copy again visibly returns Review and never generates a replacement', async () => {
  const h = harness(); await h.flow.showIdeas(); h.renderIdeas();
  h.saved.get(OTHER).handoff_status = {available: false, code: 'stale_source'};
  h.saved.get(OTHER).steps.review.status = 'review-needed'; h.saved.get(OTHER).accepted.review = null;
  await h.get('ideas-copy-again-' + OTHER).click(); assert.equal(h.flow.current, 'review'); assert.equal(h.flow.view, 'workflow');
  assert.match(h.flow.message, /Copy again is unavailable/); assert.deepEqual(h.calls.filter(c => c.payload).map(c => c.payload), [{idea_id: OTHER}]);
});

test('New and Open refuse dirty pending proposal paused busy or disposed work without clearing answers', async () => {
  for (const change of [f => f.edit('capture', {raw_text: 'Keep words'}), f => f.pending = {}, f => f.proposalPending = {},
    f => f.paused = true, f => f.busy = true, f => f.dispose()]) {
    const h = harness(); await h.flow.showIdeas(); change(h.flow); h.renderIdeas(); const before = clone(h.flow.buffers);
    assert.equal(h.get('new-idea').disabled, true); assert.equal(h.get('ideas-open-' + OTHER).disabled, true);
    await h.get('new-idea').listeners.click(); await h.get('ideas-open-' + OTHER).listeners.click();
    assert.deepEqual(h.flow.buffers, before); assert.equal(h.calls.filter(c => c.payload).length, 0);
  }
});

async function appFixture(run, prepare = () => {}) {
  const names = ['document', 'location', 'history', 'addEventListener'], originals = new Map(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const roots = new Map(['announcement', 'identity', 'progress', 'save-status', 'agent-status', 'compact-nav', 'columns', 'connection'].map(id => {const node = element('div'); node.id = id; return [id, node];}));
  const doc = {body: element('body'), hidden: true, createElement: tag => element(tag), getElementById: id => [...roots.values()].flatMap(n => n.all()).find(n => n.id === id)};
  doc.activeElement = doc.body; globalThis.document = doc;
  Object.defineProperty(globalThis, 'location', {configurable: true, value: new URL('http://127.0.0.1:1234/?binding=' + BINDING + '&idea_id=' + IDEA)});
  const histories = []; globalThis.history = {replaceState: (_, __, path) => {histories.push(path);}}; globalThis.addEventListener = () => {};
  const h = server(); prepare(h); registerStep('review', review); registerIdeas(ideas); let flow;
  try {flow = startApp(h.api); await new Promise(resolve => setImmediate(resolve)); await run({...h, flow, doc, histories});}
  finally {flow?.dispose(); for (const [name, descriptor] of originals) {if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name];}}
}

test('actual app uses literal seven-module loader and rejects failed/malformed packaged modules', async () => {
  const paths = []; await loadStepModules(async path => {paths.push(path); return {ok: true, status: 200};}, async () => ({render: review, renderUploads: () => {}}));
  assert.deepEqual(paths, ['./steps/discovery.js', './steps/exploration.js', './steps/method.js', './steps/visualize.js', './steps/assess.js', './steps/review.js', './ideas.js']);
  await assert.rejects(loadStepModules(async () => ({status: 500, ok: false})), /Cannot load/);
  await assert.rejects(loadStepModules(async () => ({status: 200, ok: true}), async () => ({})), /Invalid packaged/);
});

test('actual app preserves authoritative null in URL and reconnect state reads; Review wording is derived', async () => {
  await appFixture(async ({flow, doc, calls, histories}) => {
    assert.ok(doc.getElementById('step-body').all().some(n => n.textContent.includes('derived from the verified')));
    assert.equal(await flow.selectIdea(null), true); assert.ok(histories.at(-1).includes('binding=')); assert.equal(histories.at(-1).includes('idea_id='), false);
    flow.error = {code: 'connection_lost'}; flow.onChange(); await doc.getElementById('reload-state').click();
    assert.equal(calls.filter(c => c.path.startsWith('/api/v1/state')).at(-1).path, '/api/v1/state');
    assert.equal(flow.state.idea_id, null); assert.equal(doc.getElementById('idea-text').value, '');
    await doc.getElementById('pause-workflow').click(); await doc.getElementById('resume-workflow').click();
    assert.equal(calls.filter(c => c.path.startsWith('/api/v1/state')).at(-1).path, '/api/v1/state');
    assert.equal(flow.state.idea_id, null);
  });
});

test('actual app reconnect never resurrects initial URL selection after authoritative New', async () => {
  await appFixture(async ({flow, doc, api, calls}) => {
    assert.equal(await flow.selectIdea(null), true);
    flow.refreshUnauthorized = true; flow.onChange(); assert.ok(doc.getElementById('pairing-code'));
    api.pair = async () => api.session(); doc.getElementById('pairing-code').value = 'fixture-only';
    const form = doc.getElementById('connection').all().find(n => n.tagName === 'FORM');
    await form.listeners.submit({preventDefault() {}});
    assert.equal(flow.state.idea_id, null); assert.equal(flow.refreshUnauthorized, false);
    assert.equal(calls.filter(c => c.path.startsWith('/api/v1/state')).at(-1).path, '/api/v1/state');
  });
});

test('actual app uncertainty freezes editing/autosave and offers explicit restoration with preserved literal answers', async () => {
  await appFixture(async ({flow, doc, faults}) => {
    const before = clone(flow.buffers); faults.selectionLost = true;
    assert.equal(await flow.selectIdea(null), false); assert.ok(doc.getElementById('selection-uncertain'));
    assert.equal(doc.getElementById('idea-text'), undefined); assert.equal(doc.getElementById('selection-preserved-answers').readOnly, true);
    assert.deepEqual(JSON.parse(doc.getElementById('selection-preserved-answers').value), before);
    faults.selectionLost = false; await doc.getElementById('selection-recover').click();
    assert.equal(flow.selectionUncertain, false); assert.equal(flow.state.idea_id, IDEA); assert.ok(doc.getElementById('review-copy'));
  });
});

test('actual initial connection refuses foreign idea/session before adoption and explicitly retries the requested scope', async () => {
  for (const kind of ['idea', 'session']) {
    await appFixture(async ({flow, doc, faults, calls}) => {
      assert.equal(flow.state, null); assert.equal(flow.selectionUncertain, true);
      assert.ok(doc.getElementById('selection-recover'));
      assert.equal(calls.some(c => c.payload?.idea_id === OTHER), false);
      faults.read = null; await doc.getElementById('selection-recover').click();
      assert.equal(flow.state.idea_id, IDEA); assert.equal(flow.state.session_id, SESSION);
      assert.equal(flow.selectionUncertain, false);
      assert.equal(calls.filter(c => c.path.startsWith('/api/v1/state')).at(-1).path, '/api/v1/state?idea_id=' + IDEA);
    }, h => {h.faults.read = value => response(kind === 'idea' ? state(OTHER) : {...value, session_id: 'session_' + '9'.repeat(32)});});
  }
});

test('actual repaired connection keeps authoritative null and newer Capture answers on foreign reply and same-scope restore', async () => {
  await appFixture(async ({flow, doc, api, faults, calls}) => {
    assert.equal(await flow.selectIdea(null), true);
    flow.edit('capture', {raw_text: 'New unsaved words 💡', workspace: {name: 'Own', path: '/own', confirmed: true}});
    const before = clone(flow.buffers), dirty = [...flow.dirty];
    flow.refreshUnauthorized = true; flow.onChange(); api.pair = async () => api.session();
    faults.read = () => response(state(OTHER));
    const form = doc.getElementById('connection').all().find(n => n.tagName === 'FORM');
    await form.listeners.submit({preventDefault() {}});
    assert.equal(flow.state.idea_id, null); assert.equal(flow.selectionUncertain, true);
    assert.deepEqual(flow.buffers, before); assert.deepEqual([...flow.dirty], dirty);
    assert.equal(doc.getElementById('idea-text'), undefined);
    faults.read = null; await doc.getElementById('selection-recover').click();
    assert.equal(flow.state.idea_id, null); assert.equal(flow.selectionUncertain, false);
    assert.deepEqual(flow.buffers, before); assert.deepEqual([...flow.dirty], dirty);
    assert.equal(doc.getElementById('idea-text').value, 'New unsaved words 💡');
    assert.deepEqual(calls.filter(c => c.path === '/api/v1/selection').map(c => c.payload), [{idea_id: null}, {idea_id: null}]);
    assert.equal(calls.some(c => c.path === '/api/v1/draft'), false);
  });
});

test('actual Resume pins idea including null and SID before adoption, and restore preserves dirty paused answers', async () => {
  for (const id of [IDEA, null]) for (const kind of ['idea', 'session']) {
    await appFixture(async ({flow, doc, faults, calls}) => {
      if (id === null) assert.equal(await flow.selectIdea(null), true);
      flow.edit('capture', {raw_text: 'Preserved paused words', workspace: {name: 'Own', path: '/own', confirmed: true}});
      flow.paused = true; flow.onChange(); const before = clone(flow.buffers), dirty = [...flow.dirty];
      faults.read = value => response(kind === 'idea' ? state(OTHER) : {...value, session_id: 'session_' + '9'.repeat(32)});
      await doc.getElementById('resume-workflow').click();
      assert.equal(flow.state.idea_id, id); assert.equal(flow.state.session_id, SESSION);
      assert.equal(flow.selectionUncertain, true); assert.equal(flow.paused, true);
      assert.deepEqual(flow.buffers, before); assert.deepEqual([...flow.dirty], dirty);
      faults.read = null; await doc.getElementById('selection-recover').click();
      assert.equal(flow.state.idea_id, id); assert.equal(flow.paused, true); assert.equal(flow.selectionUncertain, false);
      assert.deepEqual(flow.buffers, before); assert.deepEqual([...flow.dirty], dirty);
      assert.ok(doc.getElementById('resume-workflow'));
      assert.equal(calls.some(c => c.path === '/api/v1/draft'), false);
    });
  }
});
