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
  ['1', 'ready-to-plan', 'bounded-plan', 'review', 7], ['2', 'in-progress', 'adaptive-slices', 'shape', 2],
  ['3', 'review-needed', null, 'assess', 5], ['4', 'archived', 'experiment-led', 'review', 7],
].map(([n, status, method, current_step, completed_steps], index) => ({idea_id: id(n), revision: 2, position: index + 1, title: 'Idea ' + n + ' title',
  status, method, updated: '2026-10-01T10:00:00+02:00', detail_path: '/x/' + n + '.md', current_step, completed_steps}));

async function backlogApp(run, {markdown = async () => '# Heading\n', rerank} = {}) {
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
    steps: Object.fromEntries(STEPS.map(({key}, index) => [key, {status: index === 0 ? 'current' : 'todo', accepted_revision: null, evidence_id: null}])), accepted: {}, drafts: {}, draft: null};
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

test('the top-bar Ideas button exists and opens the backlog', async () => {
  await backlogApp(async h => {
    assert.match(await source('index.html'), /<header class="app-bar">[\s\S]*<button id="nav-ideas"[^>]*>Ideas<\/button>[\s\S]*<\/header>/);
    assert.equal(h.flow.view, 'workflow'); assert.ok(h.get('nav-ideas').listeners.click, 'the app wires the nav button');
    await openBacklog(h);
    assert.equal(h.flow.view, 'ideas'); assert.equal(h.cards().length, 4);
  });
});

test('cards show title, plain method label, status text and Step N of 7 · Resume', async () => {
  await backlogApp(async h => {
    await openBacklog(h);
    const [ready, progress, review, archived] = h.cards().map(card => card.text());
    assert.match(ready, /Idea 1 title/); assert.match(ready, /Method: Bounded plan \(APIV\)/); assert.match(ready, /Ready to plan/);
    assert.match(progress, /Adaptive vertical slices/); assert.match(progress, /In progress/); assert.match(progress, /Step 3 of 7 · Resume/);
    assert.match(review, /Method: Not chosen/); assert.match(review, /Review needed/); assert.match(review, /Step 6 of 7 · Resume/);
    assert.match(archived, /Experiment-led discovery/); assert.match(archived, /Archived/);
    assert.doesNotMatch(ready + archived, /of 7 · Resume/);
    assert.equal(h.get('ideas-open-' + id(2)).textContent, 'Step 3 of 7 · Resume');
  });
});

test('Open Markdown shows the text literally in a <pre>, and Close removes it', async () => {
  const hostile = '# T\n<script>alert(1)</script> & <b>x</b>\n';
  await backlogApp(async h => {
    await openBacklog(h);
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
    for (const key of ['up', 'down', 'move']) assert.equal(h.get('backlog-' + key + '-' + id(4)).disabled, true, key + ' on archived');
    assert.equal(h.get('backlog-pos-' + id(4)).disabled, true);
    h.get('backlog-pos-' + id(2)).value = '99'; await h.get('backlog-move-' + id(2)).click(); await h.settle();
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

test('the duplicated method names and step order match steps/method.js and folds.js', async () => {
  const methodUrl = dataUrl((await source('steps/method.js')).replace("'../folds.js'", JSON.stringify(foldsUrl)));
  const {APIV_LABEL} = await import(methodUrl);
  const text = await source('ideas.js');
  assert.ok(text.includes("'Bounded plan (" + APIV_LABEL + ")'"));
  assert.ok(text.includes(JSON.stringify(STEPS.map(step => step.key)).replaceAll('"', "'").replaceAll(',', ', ')));
});
