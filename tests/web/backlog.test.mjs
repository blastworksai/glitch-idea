// Backlog view: top-bar nav, cards (title, method, status text, resume), read-only Markdown, human re-ranking.
// Real app.js/ideas.js with a fake DOM and a fake API; no server or browser qualification is claimed here.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web = process.env.BACKLOG_WEB_DIR ? new URL('file://' + process.env.BACKLOG_WEB_DIR.replace(/\/?$/, '/')) : new URL('../../glitch-idea/web/', import.meta.url);
const source = async name => await readFile(new URL(name, web), 'utf8');
const dataUrl = value => 'data:text/javascript;base64,' + Buffer.from(value).toString('base64');
const apiUrl = dataUrl(await source('api.js'));
const foldsUrl = dataUrl(await source('folds.js'));
const {STEPS} = await import(foldsUrl);
const appSource = (await source('app.js')).replace("'./api.js'", JSON.stringify(apiUrl)).replace("'./folds.js'", JSON.stringify(foldsUrl));
const {startApp, registerIdeas} = await import(dataUrl(appSource));
const ideasModule = await import(new URL('ideas.js', web).href);
const A = 'binding_' + 'a'.repeat(32), SA = 'session_' + 'a'.repeat(32);
const id = n => 'idea_' + String(n).repeat(32);
const ROWS = [
  ['1', 'ready-to-plan', 'bounded-plan', 'review', 8], ['2', 'in-progress', 'adaptive-slices', 'method', 2],
  ['3', 'review-needed', null, 'assess', 5], ['4', 'archived', 'experiment-led', 'review', 8],
].map(([n, status, method, current_step, completed_steps], index) => ({idea_id: id(n), revision: 2, position: index + 1, title: 'Idea ' + n + ' title',
  status, method, updated: '2026-10-01T10:00:00+02:00', detail_path: '/x/' + n + '.md', current_step, completed_steps}));

async function backlogApp(run, {markdown = async () => '# Heading\n', rerank, stateExtra = {}} = {}) {
  const names = ['document', 'location', 'history', 'addEventListener', 'setTimeout', 'clearTimeout'];
  const saved = new Map(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  let doc, currentUrl = new URL('http://127.0.0.1:1234/?binding=' + A), timerId = 0;
  class Node {
    constructor(tag = 'div') { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.dataset = {}; this.disabled = false; this.value = ''; this.textContent = ''; this.hidden = false; this.scrollTop = 0; this.scrollLeft = 0; }
    append(...nodes) { this.children.push(...nodes); }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    replaceChildren(...nodes) { if (this.children.some(child => child.contains(doc.activeElement))) doc.activeElement = doc.body; this.children = nodes; }
    setAttribute(name, value) { this[name] = value; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    focus() { if (!this.disabled) doc.activeElement = this; }
    setSelectionRange() {}
    async click() { if (!this.disabled) return this.listeners.click?.({target: this}); }
    all() { return [this, ...this.children.flatMap(child => child.all())]; }
    text() { return this.all().map(node => node.textContent).join(' '); }
    set innerHTML(value) { throw new Error('HTML injection refused'); }
  }
  const roots = new Map(['announcement', 'identity', 'progress', 'save-status', 'agent-status', 'compact-nav', 'columns', 'connection', 'nav-ideas'].map(key => { const node = new Node(); node.id = key; return [key, node]; }));
  const find = (node, key) => node.id === key ? node : node.children.map(child => find(child, key)).find(Boolean);
  doc = {body: new Node('body'), activeElement: null, hidden: true, createElement: tag => new Node(tag), getElementById: key => [...roots.values()].map(node => find(node, key)).find(Boolean) ?? null};
  doc.activeElement = doc.body; globalThis.document = doc;
  Object.defineProperty(globalThis, 'location', {configurable: true, get: () => currentUrl});
  globalThis.history = {replaceState: (_s, _t, url) => { currentUrl = new URL(url, currentUrl); }};
  globalThis.addEventListener = () => {};
  globalThis.setTimeout = (callback, delay) => ({id: ++timerId, unref() {}});
  globalThis.clearTimeout = () => {};
  const state = {ok: true, code: 'ok', session_id: SA, idea_id: null, idea_status: 'active', revision: 0, draft_version: 0, backlog_revision: 0, current_step: 'capture', agent_status: 'disconnected',
    steps: Object.fromEntries(STEPS.map(({key}, index) => [key, {status: index === 0 ? 'current' : 'todo', accepted_revision: null, evidence_id: null}])), accepted: {}, drafts: {}, draft: null, ...stateExtra};
  const calls = {rerank: [], markdown: [], ideas: 0};
  const board = {rows: structuredClone(ROWS), revision: 9};
  const api = {bindingId: A, csrf: 'csrf', pinBinding() {}, session: async () => ({ok: true, code: 'ok', binding_id: A, session_id: SA, csrf_token: 'csrf'}),
    state: async () => structuredClone(state),
    ideas: async () => { calls.ideas++; return {ok: true, code: 'ok', backlog_revision: board.revision, total: board.rows.length, ideas: structuredClone(board.rows)}; },
    ideaMarkdown: async ideaId => { calls.markdown.push(ideaId); return markdown(ideaId); },
    rerank: async payload => { calls.rerank.push(structuredClone(payload)); if (rerank) return rerank(payload, board); return {ok: true}; },
    write: async () => ({ok: true, code: 'ok'}), reconcile: async () => ({result: null, state: structuredClone(state)})};
  let flow;
  try {
    registerIdeas(ideasModule.render);
    flow = startApp(api); await new Promise(resolve => setImmediate(resolve));
    const get = key => doc.getElementById(key);
    const settle = () => new Promise(resolve => setImmediate(resolve));
    await run({flow, get, calls, board, settle, text: () => roots.get('columns').text(), cards: () => roots.get('columns').all().filter(n => n.className === 'backlog-card')});
  } finally {
    flow?.dispose();
    for (const [name, descriptor] of saved) if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name];
  }
}
const openBacklog = async h => { await h.get('nav-ideas').click(); await h.settle(); };
const choose = async (h, n) => { await h.get('backlog-select-' + id(n)).click(); await h.settle(); };

test('the top-bar Ideas button exists and opens the backlog', async () => {
  await backlogApp(async h => {
    assert.match(await source('index.html'), /<header class="bw-appbar">[\s\S]*<nav id="app-nav" class="bw-tabs"[\s\S]*<button id="nav-ideas"[^>]*>Ideas<\/button>[\s\S]*<\/nav>[\s\S]*<\/header>/);
    assert.equal(h.flow.view, 'workflow'); assert.ok(h.get('nav-ideas').listeners.click, 'the app wires the nav button');
    await openBacklog(h);
    assert.equal(h.flow.view, 'ideas'); assert.equal(h.cards().length, 4);
  });
});

test('table rows show title, plain method label, status text; the selected idea shows Step N of 8 · Resume', async () => {
  await backlogApp(async h => {
    await openBacklog(h);
    const [ready, progress, review, archived] = h.cards().map(card => card.text());
    assert.match(ready, /Idea 1 title/); assert.match(ready, /Full Plan Up Front/); assert.match(ready, /Ready to plan/);
    assert.match(progress, /Vertical Slicing \(Agile\)/); assert.match(progress, /Ideation ongoing/); assert.doesNotMatch(progress, /In progress/); assert.match(progress, /2\/8/);
    assert.match(review, /Not chosen/); assert.match(review, /Review needed/); assert.match(review, /5\/8/);
    assert.match(archived, /Experiment First/); assert.match(archived, /Archived/);
    assert.equal(h.get('ideas-open-' + id(1)).textContent, 'Open', 'the first idea is selected and ready to plan: plain Open');
    await choose(h, 3); assert.equal(h.get('ideas-open-' + id(3)).textContent, 'Step 7 of 8 · Resume');
    assert.equal(h.get('ideas-open-' + id(2)), null, 'only the selected idea has panel actions');
    await choose(h, 4); assert.equal(h.get('ideas-open-' + id(4)).textContent, 'Open');
    await choose(h, 2);
    assert.equal(h.get('ideas-open-' + id(2)).textContent, 'Step 3 of 8 · Resume');
  });
});

test('Open Markdown shows the text literally in a <pre>, and Close removes it', async () => {
  const hostile = '# T\n<script>alert(1)</script> & <b>x</b>\n';
  await backlogApp(async h => {
    await openBacklog(h); await choose(h, 2);
    await h.get('backlog-md-' + id(2)).click(); await h.settle();
    const pre = h.get('backlog-md-text'); assert.equal(pre.tagName, 'PRE'); assert.equal(pre.textContent, hostile);
    assert.deepEqual(h.calls.markdown, [id(2)]);
    await h.get('backlog-md-close').click(); await h.settle();
    assert.equal(h.get('backlog-md-text'), null);
  }, {markdown: async () => hostile});
});

test('Open Markdown says so when the idea has no file yet', async () => {
  await backlogApp(async h => {
    await openBacklog(h);
    await h.get('backlog-md-' + id(1)).click(); await h.settle();
    assert.match(h.text(), /This idea has no Markdown file yet\./); assert.equal(h.get('backlog-md-text'), null);
  }, {markdown: async () => { throw Object.assign(new Error('nf'), {code: 'not_found'}); }});
});

test('Move up, Move down and Move to position call rerank with position, revision and a fresh request id', async () => {
  await backlogApp(async h => {
    await openBacklog(h);
    await h.get('backlog-up-' + id(3)).click(); await h.settle();
    await h.get('backlog-down-' + id(2)).click(); await h.settle();
    const input = h.get('backlog-pos-' + id(1)); input.value = '3';
    await h.get('backlog-move-' + id(1)).click(); await h.settle();
    const [up, down, to] = h.calls.rerank;
    assert.deepEqual([up.idea_id, up.position, up.expected_backlog_revision, up.reason], [id(3), 2, 9, null]);
    assert.deepEqual([down.idea_id, down.position, down.expected_backlog_revision], [id(2), 3, 9]);
    assert.deepEqual([to.idea_id, to.position], [id(1), 3]);
    assert.equal(new Set([up.request_id, down.request_id, to.request_id]).size, 3);
    assert.ok(h.calls.ideas >= 4, 'the list is re-read after each move');
    assert.match(h.get('backlog-notice').textContent, /^Moved to position 3\. If this idea was already assessed, re-accept its Assess step/);
  });
});

test('Move buttons are disabled at the ends and for archived ideas; bad positions never reach the API', async () => {
  await backlogApp(async h => {
    await openBacklog(h);
    assert.equal(h.get('backlog-up-' + id(1)).disabled, true); assert.equal(h.get('backlog-up-' + id(2)).disabled, false);
    assert.equal(h.get('backlog-down-' + id(4)).disabled, true); assert.equal(h.get('backlog-down-' + id(3)).disabled, false);
    for (const key of ['up', 'down']) assert.equal(h.get('backlog-' + key + '-' + id(4)).disabled, true, key + ' on archived');
    await choose(h, 4); assert.equal(h.get('backlog-pos-' + id(4)).disabled, true);
    for (const key of ['move']) assert.equal(h.get('backlog-' + key + '-' + id(4)).disabled, true, key + ' on archived');
    await choose(h, 2); h.get('backlog-pos-' + id(2)).value = '99'; await h.get('backlog-move-' + id(2)).click(); await h.settle();
    assert.equal(h.calls.rerank.length, 0); assert.match(h.get('backlog-notice').textContent, /from 1 to 4/);
  });
});

test('stale_backlog reloads the list and tells the human to check the order', async () => {
  await backlogApp(async h => {
    await openBacklog(h); const before = h.calls.ideas;
    await h.get('backlog-up-' + id(3)).click(); await h.settle();
    assert.ok(h.calls.ideas > before); assert.equal(h.get('backlog-notice').textContent, 'The backlog changed; check the order and move again.');
  }, {rerank: async () => { throw Object.assign(new Error('stale'), {code: 'stale_backlog', status: 409}); }});
});

test('method labels and the step list each have one owner, folds.js', async () => {
  const text = await source('ideas.js');
  assert.match(text, /import \{STEPS, METHOD_LABELS\} from '\.\/folds\.js'/);
  for (const old of ['Bounded plan', 'Adaptive vertical slices', 'Appetite-led shaping', 'Experiment-led discovery']) assert.ok(!text.includes(old), old);
  assert.ok(!/const METHOD_LABELS/.test(text));
  assert.ok(!text.includes("'priorities'"));
  assert.equal(STEPS.length, 8);
});

// ---- moved and delivered ideas: read-only pointers to a file in a workspace ----
const lifeRow = (n, lifecycle, extra = {}) => ({...ROWS[0], idea_id: id(n), position: 5, title: 'Idea ' + n + ' title', status: lifecycle, lifecycle,
  home: {workspace_name: 'Shop Site', workspace_path: '/work/shop', file_path: '/work/shop/ideas/' + n + '.md'}, delivered_ref: null, read_only: true,
  detail_path: '/work/shop/ideas/' + n + '.md', ...extra});
const withPointers = h => {
  h.board.rows = h.board.rows.map(row => ({...row, lifecycle: 'active', home: null, delivered_ref: null, read_only: false}));
  h.board.rows.push(lifeRow(5, 'moved'), lifeRow(6, 'moved'), lifeRow(7, 'delivered', {delivered_ref: 'PLAN-42'}));
};
const rowOf = (h, n) => h.cards().find(card => card.dataset.ideaId === id(n));

test('moved and delivered rows are hidden by default; their chips are off and show counts', async () => {
  await backlogApp(async h => {
    withPointers(h); await openBacklog(h);
    for (const n of [5, 6, 7]) assert.equal(rowOf(h, n).hidden, true, 'row ' + n);
    for (const n of [1, 2, 3, 4]) assert.equal(rowOf(h, n).hidden, false, 'row ' + n);
    assert.equal(h.get('ideas-filter-moved')['aria-pressed'], 'false'); assert.equal(h.get('ideas-filter-delivered')['aria-pressed'], 'false');
    assert.equal(h.get('ideas-filter-moved')['aria-label'], 'In progress (2)'); assert.equal(h.get('ideas-filter-delivered')['aria-label'], 'Delivered (1)');
    assert.match(h.get('ideas-filter-moved').text(), /In progress\s+2/);
    for (const key of ['ready-to-plan', 'review-needed', 'in-progress', 'archived']) assert.equal(h.get('ideas-filter-' + key)['aria-pressed'], 'true', key);
    assert.match(h.get('ideas-filter-in-progress').text(), /Ideation ongoing\s+1/);
  });
});

test('toggling the In progress and Delivered chips shows their rows with the right labels', async () => {
  await backlogApp(async h => {
    withPointers(h); await openBacklog(h);
    await h.get('ideas-filter-moved').click();
    assert.equal(rowOf(h, 5).hidden, false); assert.equal(rowOf(h, 6).hidden, false); assert.equal(rowOf(h, 7).hidden, true);
    assert.match(rowOf(h, 5).text(), /In progress/); assert.doesNotMatch(rowOf(h, 5).text(), /Ideation ongoing/);
    await h.get('ideas-filter-delivered').click();
    assert.equal(rowOf(h, 7).hidden, false); assert.match(rowOf(h, 7).text(), /Delivered · PLAN-42/);
    await h.get('ideas-filter-moved').click(); assert.equal(rowOf(h, 5).hidden, true);
    await h.get('ideas-filter-archived').click(); assert.equal(rowOf(h, 4).hidden, true, 'the existing chips keep working');
  });
});

test('search finds moved rows even while their chip is off', async () => {
  await backlogApp(async h => {
    withPointers(h); await openBacklog(h);
    const search = h.get('ideas-search'); search.value = 'Idea 6'; await search.listeners.input();
    assert.equal(rowOf(h, 6).hidden, false); assert.equal(rowOf(h, 5).hidden, true); assert.equal(rowOf(h, 1).hidden, true);
  });
});

test('a moved idea panel is read-only: names its workspace and file, no Resume, Open, Markdown, Move or reorder', async () => {
  await backlogApp(async h => {
    withPointers(h); await openBacklog(h);
    await h.get('ideas-filter-moved').click(); await h.get('ideas-filter-delivered').click();
    await choose(h, 5);
    const panel = h.get('backlog-detail');
    assert.equal(h.get('backlog-moved-note').textContent, 'Moved to Shop Site');
    assert.equal(h.get('backlog-moved-path').textContent, '/work/shop/ideas/5.md');
    for (const key of ['ideas-open-', 'ideas-copy-again-', 'backlog-md-', 'backlog-pos-', 'backlog-move-']) assert.equal(h.get(key + id(5)), null, key);
    assert.doesNotMatch(panel.text(), /Resume|Open Markdown|Move to /);
    assert.equal(h.get('backlog-up-' + id(5)), null); assert.equal(h.get('backlog-down-' + id(5)), null);
    await choose(h, 7); assert.match(h.get('backlog-detail').text(), /Delivered\s+PLAN-42/); assert.equal(h.get('backlog-moved-note').textContent, 'Moved to Shop Site');
    await choose(h, 1); assert.ok(h.get('ideas-open-' + id(1)), 'active rows keep their actions'); assert.equal(h.get('backlog-moved-note'), null);
  });
});

test('Capture prefills the default workspace once, keeps it editable and never overwrites typed text', async () => {
  const dflt = {name: 'Shop Site', path: '/work/shop'};
  await backlogApp(async h => {
    h.flow.view = 'workflow'; h.flow.current = 'capture'; h.flow.onChange(); await h.settle();
    assert.equal(h.get('workspace-name').value, 'Shop Site'); assert.equal(h.get('workspace-path').value, '/work/shop');
    assert.equal(h.flow.dirty.size, 0, 'a prefill is not an unsaved edit');
    h.get('workspace-name').value = 'Mine'; await h.get('workspace-name').listeners.input({target: h.get('workspace-name')}); await h.settle();
    assert.equal(h.flow.buffers.capture.workspace.name, 'Mine'); assert.equal(h.flow.buffers.capture.workspace.path, '/work/shop');
    h.flow.onChange(); await h.settle(); assert.equal(h.get('workspace-name').value, 'Mine');
  }, {stateExtra: {default_workspace: dflt}});
  await backlogApp(async h => {
    h.flow.buffers.capture = {raw_text: '', workspace: {name: 'Typed', path: '', confirmed: false}};
    h.flow.onChange(); await h.settle();
    assert.equal(h.get('workspace-name').value, 'Typed'); assert.equal(h.get('workspace-path').value, '', 'typed text is never overwritten, even partly');
  }, {stateExtra: {default_workspace: dflt}});
  await backlogApp(async h => {
    h.flow.onChange(); await h.settle();
    assert.equal(h.get('workspace-name').value, ''); assert.equal(h.get('workspace-path').value, '');
  }, {stateExtra: {default_workspace: null}});
});

test('a refused write on a moved idea names its new home, never "Could not save"', async () => {
  await backlogApp(async h => {
    h.flow.error = Object.assign(new Error('idea_moved'), {code: 'idea_moved', status: 409, data: {ok: false, code: 'idea_moved', write_state: 'not_applied',
      home: {workspace_name: 'Shop Site', workspace_path: '/work/shop', file_path: '/work/shop/ideas/5.md'}}});
    h.flow.onChange(); await h.settle();
    assert.match(h.text(), /moved to Shop Site/); assert.match(h.text(), /\/work\/shop\/ideas\/5\.md/); assert.doesNotMatch(h.text(), /Could not save/);
    assert.equal(h.get('retry-save'), null);
  });
});

test('moving an idea that has since moved says where it went', async () => {
  await backlogApp(async h => {
    await openBacklog(h);
    await h.get('backlog-up-' + id(3)).click(); await h.settle();
    assert.match(h.get('backlog-notice').textContent, /moved to Shop Site/); assert.doesNotMatch(h.get('backlog-notice').textContent, /Could not move/);
  }, {rerank: async () => { throw Object.assign(new Error('idea_moved'), {code: 'idea_moved', status: 409, data: {home: {workspace_name: 'Shop Site', workspace_path: '/w', file_path: '/w/x.md'}}}); }});
});
