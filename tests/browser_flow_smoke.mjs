#!/usr/bin/env node
// Source-only browser-flow smoke harness. Node 22 stdlib only: no dependency,
// no network beyond loopback. It drives headless Chrome over the DevTools
// Protocol. Every user action is a CDP-level Input.* event at coordinates read
// from the DOM; page scripts are evaluated READ-ONLY. These events prove page
// event handling in headless Chrome; they are NOT an attended human
// qualification (native Windows/macOS/Orca rows remain separate).
//
// Usage:
//   node tests/browser_flow_smoke.mjs [--python /abs/python3] [--helper /abs/idea.py]
//        [--chrome /abs/google-chrome] [--evidence /abs/dir] [--agent-reply-delay SECONDS]
// --agent-reply-delay makes the fixture agent sit on the Shape request that long before
// replying, as a composing model would (answer window check; over 300 s it must drop).
import {spawn, spawnSync} from 'node:child_process';
import {mkdtempSync, mkdirSync, rmSync, rmdirSync, writeFileSync, readFileSync, existsSync, chmodSync} from 'node:fs';
import {tmpdir, homedir, userInfo} from 'node:os';
import {join, resolve, dirname} from 'node:path';
import {randomBytes} from 'node:crypto';
import {fileURLToPath} from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..');
const argv = process.argv.slice(2);
const opt = (name, fallback) => { const i = argv.indexOf('--' + name); return i >= 0 ? argv[i + 1] : fallback; };
const PYTHON = opt('python', 'python3');
const HELPER = opt('helper', join(ROOT, 'glitch-idea/scripts/idea.py'));
const CHROME = opt('chrome', existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : '/usr/bin/chromium');
const STAMP = new Date().toISOString().replace(/[:.]/g, '-');
const EVIDENCE = resolve(opt('evidence', join(ROOT, 'evidence/browser-smoke', STAMP)));
const ME = userInfo().username;
const REPLY_DELAY_S = Number(opt('agent-reply-delay', '0'));
if (!Number.isFinite(REPLY_DELAY_S) || REPLY_DELAY_S < 0 || REPLY_DELAY_S > 600) throw new Error('--agent-reply-delay must be 0..600 seconds');

const summary = {
  harness: 'browser_flow_smoke', started: new Date().toISOString(), node: process.version,
  input_model: 'CDP Input.dispatchMouseEvent/dispatchKeyEvent/insertText at DOM-read coordinates; Runtime.evaluate is read-only. ' +
    'Proves page event handling in headless Chrome only; NOT an attended human qualification.',
  agent_path: 'human-edit path for the main journey; agent Resume and agent proposals (Shape, Method, Assess) are exercised with a fixed fixture agent that drives the documented native events/respond verbs with synthetic replies (not a model); the browser side is real CDP input',
  python: PYTHON, helper: HELPER, agent_reply_delay_s: REPLY_DELAY_S, steps: [], required_failures: 0,
};
const RUN_LIMIT_MS = 300000 + REPLY_DELAY_S * 1000;  // whole-run deadline: every wait below is bounded, and this is the backstop
const secrets = new Set();
const redact = value => {
  let text = typeof value === 'string' ? value : JSON.stringify(value);
  for (const secret of secrets) if (secret) text = text.split(secret).join('<redacted>');
  return text;
};
const record = (id, ok, observation = '', required = true) => {
  summary.steps.push({step: id, result: ok ? 'pass' : 'fail', required, observation: redact(observation)});
  if (!ok && required) summary.required_failures++;
  process.stderr.write((ok ? 'PASS ' : 'FAIL ') + id + (observation ? ' - ' + redact(observation).slice(0, 160) : '') + '\n');
};
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---- resources and cleanup -------------------------------------------------
let tmp = null, runtimeRoot = null, chrome = null, cleaned = false, createdStateDir = null;
function serviceProcesses() {
  if (!runtimeRoot) return [];
  const out = spawnSync('ps', ['-u', ME, '-o', 'pid=,args='], {encoding: 'utf8'}).stdout ?? '';
  return out.split('\n').filter(line => line.includes(runtimeRoot) && line.includes('idea_launch'))
    .map(line => Number(line.trim().split(/\s+/)[0])).filter(Number.isInteger);
}
function cleanup() {
  if (cleaned) return; cleaned = true;
  try { cdp?.close(); } catch {}
  try { if (chrome?.pid) process.kill(-chrome.pid, 'SIGKILL'); } catch {}
  try { chrome?.kill('SIGKILL'); } catch {}
  for (const pid of serviceProcesses()) { try { process.kill(pid, 'SIGTERM'); } catch {} }
  spawnSync('sleep', ['1']);
  for (const pid of serviceProcesses()) { try { process.kill(pid, 'SIGKILL'); } catch {} }
  for (const dir of [tmp, runtimeRoot]) { try { if (dir) rmSync(dir, {recursive: true, force: true}); } catch {} }
  // Remove only the (now empty) parents this run created for the runtime root, innermost first.
  if (createdStateDir && runtimeRoot) {
    for (let dir = dirname(runtimeRoot); dir.startsWith(createdStateDir); dir = dirname(dir)) {
      try { rmdirSync(dir); } catch { break; }
      if (dir === createdStateDir) break;
    }
  }
}
process.on('exit', cleanup);
for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(signal, () => { cleanup(); process.exit(130); });

// ---- helper CLI ------------------------------------------------------------
function idea(args, {expectOk = true} = {}) {
  const result = spawnSync(PYTHON, [HELPER, '--store', join(tmp, 'store'), ...args], {encoding: 'utf8', timeout: 60000});
  let json = null; try { json = JSON.parse(result.stdout); } catch {}
  if (expectOk && (result.status !== 0 || json?.ok === false)) throw new Error('helper ' + args[0] + ' failed: ' + redact((result.stdout || result.stderr).slice(0, 300)));
  return {status: result.status, json};
}
function ideaAsync(args, timeout = 60000) {
  return new Promise(resolveRun => {
    const child = spawn(PYTHON, [HELPER, '--store', join(tmp, 'store'), ...args], {stdio: ['ignore', 'pipe', 'pipe']});
    let out = ''; child.stdout.on('data', d => out += d);
    child.stderr.on('data', () => {});  // drain, so a full pipe cannot block the writer
    const timer = setTimeout(() => { try { child.kill('SIGKILL'); } catch {} }, timeout);
    child.on('close', status => { clearTimeout(timer); let json = null; try { json = JSON.parse(out); } catch {} resolveRun({status, json}); });
  });
}
const getJson = async (url, init = {}) => (await fetch(url, {...init, signal: AbortSignal.timeout(10000)})).json();

// ---- minimal CDP client ----------------------------------------------------
class Cdp {
  constructor(url) { this.id = 0; this.pending = new Map(); this.events = []; this.ws = new WebSocket(url); }
  open() {
    return new Promise((ok, bad) => {
      const timer = setTimeout(() => bad(new Error('cdp_ws_open_timeout')), 15000);
      this.ws.addEventListener('open', () => { clearTimeout(timer); ok(); });
      this.ws.addEventListener('error', () => { clearTimeout(timer); bad(new Error('cdp_ws_error')); });
      this.ws.addEventListener('message', ({data}) => {
        const msg = JSON.parse(data);
        if (msg.id && this.pending.has(msg.id)) {
          const {resolve: res, reject} = this.pending.get(msg.id); this.pending.delete(msg.id);
          msg.error ? reject(new Error(msg.error.message)) : res(msg.result);
        } else if (msg.method) this.events.push(msg);
      });
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((res, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('cdp_timeout ' + method)); }, 30000);
      this.pending.set(id, {resolve: v => { clearTimeout(timer); res(v); }, reject: e => { clearTimeout(timer); reject(e); }});
      this.ws.send(JSON.stringify({id, method, params}));
    });
  }
  close() { try { this.ws.close(); } catch {} }
}
let cdp = null;
const read = async expression => {
  const r = await cdp.send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
  if (r.exceptionDetails) throw new Error('eval_failed: ' + expression.slice(0, 80));
  return r.result.value;
};
async function waitFor(expression, label, timeout = 15000) {
  const end = Date.now() + timeout; let last;
  while (Date.now() < end) { try { last = await read(expression); if (last) return last; } catch {} await sleep(120); }
  throw new Error('timeout waiting for ' + label);
}
const sel = id => '#' + id;
async function center(selector) {
  const {root} = await cdp.send('DOM.getDocument', {depth: 0});
  const {nodeId} = await cdp.send('DOM.querySelector', {nodeId: root.nodeId, selector});
  if (!nodeId) throw new Error('missing element ' + selector);
  await cdp.send('DOM.scrollIntoViewIfNeeded', {nodeId}).catch(() => {});
  const {model} = await cdp.send('DOM.getBoxModel', {nodeId});
  const q = model.content;
  return [(q[0] + q[2] + q[4] + q[6]) / 4, (q[1] + q[3] + q[5] + q[7]) / 4];
}
async function click(selector) {
  await waitFor(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});return !!e&&!e.disabled})()`, 'enabled ' + selector);
  const [x, y] = await center(selector);
  await cdp.send('Input.dispatchMouseEvent', {type: 'mouseMoved', x, y});
  await cdp.send('Input.dispatchMouseEvent', {type: 'mousePressed', x, y, button: 'left', clickCount: 1});
  await cdp.send('Input.dispatchMouseEvent', {type: 'mouseReleased', x, y, button: 'left', clickCount: 1});
  await sleep(80);
}
async function typeInto(selector, text) {
  await click(selector);
  const focused = await read(`document.activeElement&&document.activeElement.matches(${JSON.stringify(selector)})`);
  if (!focused) throw new Error('focus did not land on ' + selector);
  await cdp.send('Input.insertText', {text});
  await sleep(60);
}
async function replaceText(selector, text) {
  await click(selector);
  const mods = {key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2};
  await cdp.send('Input.dispatchKeyEvent', {type: 'rawKeyDown', ...mods, commands: ['selectAll']});
  await cdp.send('Input.dispatchKeyEvent', {type: 'keyUp', ...mods});
  await cdp.send('Input.insertText', {text});
  await sleep(60);
  const value = await read(`document.querySelector(${JSON.stringify(selector)}).value`);
  if (value !== text) throw new Error('replace did not land on ' + selector + ': ' + String(value).slice(0, 80));
}
const KEYS = {
  Tab: {key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9},
  Enter: {key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r'},
  Space: {key: ' ', code: 'Space', windowsVirtualKeyCode: 32, text: ' '},
};
async function press(name) {
  const k = KEYS[name];
  await cdp.send('Input.dispatchKeyEvent', {type: k.text ? 'keyDown' : 'rawKeyDown', ...k});
  await cdp.send('Input.dispatchKeyEvent', {type: 'keyUp', key: k.key, code: k.code, windowsVirtualKeyCode: k.windowsVirtualKeyCode});
  await sleep(60);
}
async function typeKeys(text) {
  for (const ch of text) {
    await cdp.send('Input.dispatchKeyEvent', {type: 'keyDown', key: ch, text: ch, unmodifiedText: ch});
    await cdp.send('Input.dispatchKeyEvent', {type: 'keyUp', key: ch});
  }
  await sleep(60);
}
async function tabTo(id, max = 120) {
  for (let i = 0; i < max; i++) {
    if (await read(`document.activeElement&&document.activeElement.id===${JSON.stringify(id)}`)) return i;
    await press('Tab');
  }
  throw new Error('tab never reached ' + id);
}
async function shot(name) {
  const {data} = await cdp.send('Page.captureScreenshot', {format: 'png'});
  writeFileSync(join(EVIDENCE, name + '.png'), Buffer.from(data, 'base64'));
}
const setViewport = (width, height = 900) => cdp.send('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: false});
const statusOf = key => read(`document.getElementById('compact-${key}')?.dataset.status ?? null`);
const currentStep = () => read(`document.querySelector('#compact-nav [aria-current="step"]')?.id?.replace('compact-','') ?? null`);
async function ensureStep(key) {
  if (await currentStep() !== key) await click(sel('strip-' + key));
  await waitFor(`document.querySelector('#compact-nav [aria-current="step"]')?.id==='compact-${key}'`, 'step ' + key);
}
// Entering Shape, Method or Assess with an agent connected starts the terminal conversation by itself;
// while that request is being sent the fields are briefly read-only. Wait it out before hand-typing.
async function settle(key) {
  await sleep(200);
  await waitFor(`!/^Sending/.test(document.getElementById('${key}-proposal-status')?.textContent ?? '')`, key + ' automatic request sent');
  await sleep(200);
}
async function accept(key, buttonId, okStates = ['saved']) {
  await click(sel(buttonId));
  await waitFor(`${JSON.stringify(okStates)}.includes(document.getElementById('compact-${key}')?.dataset.status)`, key + ' saved status');
}
async function step(id, fn, required = true) {
  try { const observation = await fn(); record(id, true, observation ?? '', required); return true; }
  catch (error) { record(id, false, String(error.message ?? error), required); return false; }
}

// ---- fixture agent ---------------------------------------------------------
// The agent side is a fixed fixture, not a model: it drives the documented native CLI verbs
// (events, respond) and answers with synthetic text only. The browser side stays real input.
function agentReply(sid, event, proposal) {
  const file = join(tmp, 'reply-' + event.operation + '-' + event.sequence + '.json');
  writeFileSync(file, JSON.stringify({request_id: event.request_id, session_id: event.session_id, idea_id: event.idea_id,
    accepted_revision: event.accepted_revision, draft_version: event.draft_version, operation: event.operation,
    source_digest: event.source_digest, proposal}));
  const result = idea(['respond', '--session', sid, '--request', event.request_id, '--payload', file, '--runtime-root', runtimeRoot]).json;
  if (result?.ok !== true) throw new Error('respond was not acknowledged ok: ' + redact(JSON.stringify(result)).slice(0, 160));
  return true;
}
function agentFill(sid, event, fields, n) {
  const file = join(tmp, 'fill-' + event.operation + '-' + n + '.json');
  writeFileSync(file, JSON.stringify({request_id: event.request_id, session_id: event.session_id, idea_id: event.idea_id,
    accepted_revision: event.accepted_revision, draft_version: event.draft_version, operation: event.operation,
    source_digest: event.source_digest, fields}));
  const result = idea(['fill', '--session', sid, '--request', event.request_id, '--payload', file, '--runtime-root', runtimeRoot]).json;
  if (result?.ok !== true) throw new Error('fill was not acknowledged ok: ' + redact(JSON.stringify(result)).slice(0, 160));
  return result.fill_sequence;
}
async function serveAgent(sid, ideaId, operation, cursor, build) {
  const log = {memory_event: false, memory_ok: null, operation, event: false, reply_ok: null};
  for (let attempt = 0; attempt < 6; attempt++) {
    const batch = idea(['events', '--session', sid, '--after', String(cursor.n), '--timeout', '5', '--runtime-root', runtimeRoot]).json;
    cursor.n = batch.sequence;
    for (const event of batch.events) {
      if (event.idea_id !== ideaId) continue;
      if (event.operation === 'memory') {
        log.memory_event = true;
        log.memory_ok = agentReply(sid, event, {status: 'unavailable', sources: [], rationale: 'Fixture agent has no memory capability.'});
      } else if (event.operation === operation) {
        log.event = true;
        if (REPLY_DELAY_S && operation === 'shape') { log.reply_delay_s = REPLY_DELAY_S; await sleep(REPLY_DELAY_S * 1000); }
        log.reply_ok = agentReply(sid, event, build(event));
        return log;
      }
    }
  }
  throw new Error('no ' + operation + ' event within 6 bounded waits');
}

// ---- journey ---------------------------------------------------------------
const SYNTH_TEXT = 'Synthetic smoke idea <script>window.__pwn=1</script> <img src=x onerror="window.__pwn=2"> & "quotes" ' +
  'LONGTOKEN' + 'x'.repeat(600) + ' end.';
const SYNTH_WORDS = 'Synthetic keyboard-only smoke idea.';

async function main() {
  mkdirSync(EVIDENCE, {recursive: true});
  tmp = mkdtempSync(join(tmpdir(), 'glitch-idea-smoke-'));
  const stateDir = join(homedir(), '.local/state');
  createdStateDir = mkdirSync(stateDir, {recursive: true}) ?? null;  // first directory this run created, if any
  runtimeRoot = join(stateDir, 'glitch-idea-smoke-' + randomBytes(4).toString('hex'));
  mkdirSync(runtimeRoot, {mode: 0o700}); chmodSync(runtimeRoot, 0o700);
  const workspace = join(tmp, 'workspace'); mkdirSync(workspace);

  // Fresh store + service.
  const opened = idea(['session-open', '--runtime-root', runtimeRoot]).json;
  secrets.add(opened.pairing_code);
  const origin = opened.origin, bindingId = opened.binding_id;
  record('service_session_open', Boolean(origin && bindingId && opened.pairing_code), 'fresh store and 0700 runtime root; origin and binding received');

  // Chrome.
  const profile = join(tmp, 'chrome-profile');
  chrome = spawn(CHROME, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + profile, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--disable-extensions', 'about:blank'], {stdio: 'ignore', detached: true});
  const portFile = join(profile, 'DevToolsActivePort');
  const t0 = Date.now(); let port = null;
  while (Date.now() - t0 < 20000 && !port) { if (existsSync(portFile)) port = readFileSync(portFile, 'utf8').split('\n')[0]; else await sleep(100); }
  if (!port) throw new Error('chrome did not publish a debugging port');
  const version = await getJson(`http://127.0.0.1:${port}/json/version`);
  const target = await getJson(`http://127.0.0.1:${port}/json/new?about:blank`, {method: 'PUT'});
  cdp = new Cdp(target.webSocketDebuggerUrl); await cdp.open();
  for (const domain of ['Page', 'Runtime', 'DOM', 'Accessibility']) await cdp.send(domain + '.enable');
  record('chrome_cdp', true, redact(version.Browser));
  await setViewport(1280);
  await cdp.send('Page.navigate', {url: origin});

  // Pair (real key input into the password field).
  await step('pair', async () => {
    await waitFor(`!!document.getElementById('pairing-code')`, 'pairing form');
    await click('#pairing-code');
    await typeKeys(opened.pairing_code);  // per-character key events, never echoed
    await press('Enter');
    await waitFor(`document.getElementById('save-status')?.textContent==='Saved state loaded'`, 'paired and loaded');
    await shot('01-wide-paired');
    return 'paired with per-character key events; saved state loaded';
  });

  // Capture, mouse path, with long untrusted text.
  await step('capture_mouse', async () => {
    await ensureStep('capture');
    await typeInto('#idea-text', SYNTH_TEXT);
    await typeInto('#workspace-name', 'smoke-workspace');
    await typeInto('#workspace-path', workspace);
    await click('#workspace-confirmed');
    await shot('02-wide-capture-filled');
    await accept('capture', 'capture-accept');
    const check = await read(`({pwn: window.__pwn ?? null, injected: !!document.querySelector('#columns script, #columns img, #columns [onerror]'),
      overflow: document.documentElement.scrollWidth > innerWidth})`);
    if (check.pwn !== null || check.injected) throw new Error('untrusted text executed or injected: ' + JSON.stringify(check));
    if (check.overflow) throw new Error('horizontal overflow with long text');
    return 'capture saved; HTML-like text inert, no horizontal overflow';
  });

  await step('priorities', async () => {
    await ensureStep('priorities');
    await click('#urgency-7'); await click('#importance-8');
    await accept('priorities', 'priorities-accept');
    return 'urgency 7 and importance 8 set by pointer; saved';
  });

  await step('shape_human_edit', async () => {
    await ensureStep('shape'); await settle('shape');
    await typeInto('#shape-outcome', 'Synthetic desired result.');
    await click('#shape-scope-capability');
    await typeInto('#shape-scope-reason', 'Synthetic scope reason.');
    await click('#shape-add-alternative');
    await typeInto('#shape-alternative-0-route', 'Simpler synthetic route');
    await typeInto('#shape-alternative-0-reason', 'Smallest first.');
    await typeInto('#shape-next-slice', 'Synthetic next slice.');
    await accept('shape', 'shape-accept');
    return 'human-edit path (no agent proposal)';
  });

  await step('method_human_edit', async () => {
    await ensureStep('method'); await settle('method');
    const memoryText = await read(`document.getElementById('method-memory')?.textContent ?? ''`);
    await click('#method-choice-bounded-plan');
    await typeInto('#method-reason', 'Synthetic method reason.');
    await accept('method', 'method-accept');
    summary.missing_memory_observation = memoryText.slice(0, 200);
    // A fresh store has no memory: the panel must say so and claim no preference.
    if (!memoryText.includes('Memory is unavailable') || !memoryText.includes('No preference is claimed')) throw new Error('missing-memory notice absent: ' + memoryText.slice(0, 160));
    return 'human-edit path; missing memory shown honestly: ' + memoryText.slice(0, 120);
  });

  await step('visualize_skip_optional', async () => {
    await ensureStep('visualize');
    await click('#visualize-skipped');
    await typeInto('#visualize-reason', 'Synthetic: not needed for this idea.');
    await accept('visualize', 'visualize-accept', ['skipped', 'not-applicable']);
    return 'optional design skipped with a reason; status ' + await statusOf('visualize');
  });

  await step('assess_human_edit', async () => {
    await ensureStep('assess'); await settle('assess');
    await click('#assess-method-wsjf');
    await typeInto('#assess-version', 'smoke-1');
    await typeInto('#assess-basis', 'Synthetic evidence.');
    await typeInto('#assess-provenance', 'Synthetic harness.');
    await click('#assess-confidence-medium');
    for (const [key, value] of [['value', '5'], ['time_criticality', '3'], ['enablement', '2'], ['effort', '2']]) await typeInto('#assess-input-' + key, value);
    await typeInto('#assess-proposed-position-input', '1');
    await typeInto('#assess-actual-position', '1');
    await accept('assess', 'assess-accept');
    return 'human-edit path (WSJF, position 1)';
  });

  let promptText = '';
  await step('review_generate_prompt', async () => {
    await ensureStep('review');
    await click('#review-generate');
    await waitFor(`!!document.getElementById('review-prompt')`, 'generated prompt');
    promptText = await read(`document.getElementById('review-prompt').value`);
    if (!promptText.startsWith('/glitch-plan')) throw new Error('prompt does not begin with /glitch-plan');
    if (!promptText.includes('## Idea trace')) throw new Error('prompt lacks ## Idea trace');
    const literal = await read(`document.getElementById('review-summary').textContent.includes('<script>window.__pwn=1</script>')&&!document.querySelector('#review-summary script,#review-summary img')`);
    if (!literal) throw new Error('untrusted text not rendered as literal text in review');
    await shot('03-wide-review-prompt');
    await read(`(document.querySelector('.panel-body').scrollTop=0, document.getElementById('review-summary').scrollIntoView({block:'start'}), true)`);
    await shot('03b-review-summary');
    return `prompt read-only: starts with /glitch-plan, has ## Idea trace, ${promptText.length} chars; untrusted text literal in summary`;
  });

  await step('saved_check_and_progress', async () => {
    const states = await read(`[...document.querySelectorAll('#compact-nav button')].map(b=>b.dataset.status)`);
    const progress = await read(`document.getElementById('progress').textContent`);
    // Six folded strips beside the open panel; each strip's glyph sits in its top band, above its midpoint.
    const strips = await read(`[...document.querySelectorAll('#columns .strip')].map(b=>{const r=b.getBoundingClientRect(),g=b.querySelector('.status-glyph').getBoundingClientRect();return {off:g.top-r.top,mid:(g.top+g.height/2)<(r.top+r.height/2)}})`);
    const ok = states.filter(s => ['saved', 'skipped', 'not-applicable'].includes(s)).length;
    if (ok !== 7) throw new Error('not all steps complete: ' + states.join(','));
    if (progress !== '6 of 7 saved, 1 skipped or not applicable') throw new Error('progress text wrong: ' + progress);
    if (strips.length !== 6 || !strips.every(g => g.off >= 0 && g.off < 40 && g.mid)) throw new Error('status glyph is not at the top of each wide strip: ' + JSON.stringify(strips));
    return `states ${states.join(',')}; progress "${progress}"; glyph at top of strips`;
  });

  await step('reload_persists', async () => {
    await cdp.send('Page.reload');
    await waitFor(`document.getElementById('save-status')?.textContent==='Saved state loaded'`, 'reload loaded');
    const states = await read(`[...document.querySelectorAll('#compact-nav button')].map(b=>b.dataset.status)`);
    const progress = await read(`document.getElementById('progress').textContent`);
    if (states.filter(s => ['saved', 'skipped', 'not-applicable'].includes(s)).length !== 7) throw new Error('state lost: ' + states.join(','));
    // The saved text itself survives, not only the badges.
    if (progress !== '6 of 7 saved, 1 skipped or not applicable') throw new Error('progress text wrong after reload: ' + progress);
    await waitFor(`(document.getElementById('review-summary')?.textContent||'').includes('Synthetic smoke idea')`, 'saved capture text after reload');
    return `after reload ${states.join(',')}; "${progress}"; saved capture text read back`;
  });

  await step('wide_layout_seven_columns', async () => {
    const info = await read(`({compact:getComputedStyle(document.getElementById('compact-nav')).display,
      strips:document.querySelectorAll('#columns .strip').length, panels:document.querySelectorAll('#columns #step-panel').length,
      overflow:document.documentElement.scrollWidth>innerWidth,
      boxes:[...document.querySelectorAll('#columns .strip, #columns #step-panel')].map(b=>{const r=b.getBoundingClientRect();return [Math.round(r.left),Math.round(r.top)]})})`);
    // Seven side-by-side columns: strictly increasing left edges on one shared top line.
    const sideBySide = info.boxes.length === 7 && info.boxes.every((b, i) => i === 0 || (b[0] > info.boxes[i - 1][0] && Math.abs(b[1] - info.boxes[0][1]) <= 2));
    if (info.compact !== 'none' || info.strips + info.panels !== 7 || info.overflow || !sideBySide) throw new Error(JSON.stringify(info));
    return `1280px: ${info.strips} strips + ${info.panels} open panel = 7 columns; compact nav hidden`;
  });

  await step('viewport_440_compact_nav', async () => {
    await setViewport(440, 900); await sleep(300);
    const info = await read(`({compact:getComputedStyle(document.getElementById('compact-nav')).display,
      stripsVisible:[...document.querySelectorAll('#columns .strip')].filter(b=>getComputedStyle(b).display!=='none').length,
      compactButtons:document.querySelectorAll('#compact-nav button').length,
      overflow:document.documentElement.scrollWidth>innerWidth,
      glyphTop:[...document.querySelectorAll('#compact-nav button')].every(b=>{const g=b.querySelector('.status-glyph').getBoundingClientRect();const o=g.top-b.getBoundingClientRect().top;return o>=0&&o<30}),
      saved:[...document.querySelectorAll('#compact-nav button')].filter(b=>['saved','skipped','not-applicable'].includes(b.dataset.status)).length})`);
    if (info.compact === 'none' || info.stripsVisible !== 0 || info.compactButtons !== 7 || info.overflow || !info.glyphTop || info.saved !== 7) throw new Error(JSON.stringify(info));
    await shot('04-440-compact-nav');
    return '440px: compact navigation shown (7 buttons), wide columns hidden, no overflow, saved glyph at top';
  });

  async function axEvidence(width) {
    await setViewport(width, 900); await sleep(300);
    const {nodes} = await cdp.send('Accessibility.getFullAXTree');
    const byId = new Map(nodes.map(n => [n.nodeId, n]));
    const labels = nodes.filter(n => n.role?.value === 'button' && /^Step \d/.test(n.name?.value ?? '')).map(n => n.name.value);
    const statuses = nodes.filter(n => n.role?.value === 'status').map(n => (n.childIds ?? []).map(c => byId.get(c)?.name?.value ?? '').join('').trim()).filter(Boolean);
    const nav = nodes.some(n => n.role?.value === 'navigation' && n.name?.value === 'Idea steps');
    const ax = {step_buttons: labels, status_regions: statuses.slice(0, 8), idea_steps_navigation: nav};
    summary['ax_' + width] = ax;
    // 440px names all seven compact buttons; wide names the six folded strips beside the open panel.
    if (labels.length < (width <= 720 ? 7 : 6) || !statuses.length) throw new Error('AX evidence incomplete at ' + width + ': ' + JSON.stringify(ax));
    return ax;
  }
  await step('accessibility_labels_status_440', async () => {
    const ax = await axEvidence(440);
    if (!ax.idea_steps_navigation) throw new Error('no "Idea steps" navigation at 440px');
    return `${ax.step_buttons.length} step buttons named; navigation "Idea steps"; ${ax.status_regions.length} status regions`;
  });
  await step('accessibility_labels_status_wide', async () => {
    const ax = await axEvidence(1280);
    await setViewport(440, 900);
    return `1280px: ${ax.step_buttons.length} step buttons named; ${ax.status_regions.length} status regions`;
  });

  await step('zoom_200_equivalent', async () => {
    await setViewport(220, 900); await sleep(300);
    const info = await read(`({compact:getComputedStyle(document.getElementById('compact-nav')).display, overflowPx:document.documentElement.scrollWidth-innerWidth})`);
    await shot('05-zoom200-440-equivalent');
    await setViewport(440, 900);
    // 200% zoom of a 440px window is a 220 CSS px viewport; documented as an emulation, not browser zoom.
    summary.zoom_note = '200% emulated as a 220 CSS px viewport; browser zoom and OS zoom remain attended rows';
    if (info.compact === 'none') throw new Error('compact nav hidden at 220px');
    return `220px viewport (200% of 440): compact nav shown; horizontal overflow ${info.overflowPx}px (informational)`;
  }, false);

  await step('agent_status_region', async () => {
    await setViewport(1280, 900); await sleep(200);
    const text = await read(`document.getElementById('agent-status').textContent`);
    if (text !== 'Agent connected') throw new Error('agent status is not connected: ' + text);
    return 'agent-status region reads: ' + text;
  });

  // Complete handoff: copy the current prompt with real input, register a real plan, confirm the archive.
  await step('complete_handoff', async () => {
    const handoffId = /idea_id:\s*(idea_[0-9a-f]{32})/.exec(promptText)?.[1];
    if (!handoffId) throw new Error('generated prompt carries no idea_id');
    const target = {idea_id: handoffId};
    await click('#show-ideas');
    await click(`#ideas-open-${target.idea_id}`);
    await waitFor(`document.getElementById('identity').textContent.startsWith(${JSON.stringify(target.idea_id)})`, 'handoff idea reopened');
    await ensureStep('review');
    await cdp.send('Browser.grantPermissions', {origin: new URL(origin).origin, permissions: ['clipboardReadWrite', 'clipboardSanitizedWrite']});
    try { await waitFor(`!!document.getElementById('review-copy')&&!document.getElementById('review-copy').disabled`, 'copy enabled'); }
    catch { throw new Error('copy disabled; packet status: "' + await read(`document.getElementById('review-packet-status')?.textContent ?? '(none)'`) +
      '"; save status: "' + await read(`document.getElementById('save-status')?.textContent ?? ''`) + '"'); }
    await click('#review-copy');
    await waitFor(`!!document.getElementById('new-idea')`, 'returned to Ideas after copy', 20000);
    const copied = await read(`navigator.clipboard.readText()`);
    if (copied !== promptText) throw new Error('clipboard does not hold the generated prompt');
    const trace = /idea_id:\s*(idea_[0-9a-f]{32})[\s\S]*?idea_revision:\s*(\d+)/.exec(copied);
    if (!trace || trace[1] !== target.idea_id) throw new Error('copied prompt lacks this idea\'s trace');
    const plan = join(tmp, 'saved-plan.md');
    writeFileSync(plan, ['# Synthetic smoke plan', '', '## Idea trace', 'idea_id: ' + trace[1], 'idea_revision: ' + trace[2], '',
      '## Goal', 'Prove the browser handoff reaches a registered plan.', '', '## Tasks', '- Register this plan.', '',
      '## Validation', '- doctor stays healthy.', ''].join('\n'));
    const registered = idea(['register-plan', trace[1], '--path', plan, '--expected-revision', trace[2], '--actor', 'operator']).json;
    const shown = idea(['show', trace[1]]).json.idea;
    const archived = existsSync(join(tmp, 'store', 'archive', trace[1], 'r' + trace[2] + '.json'));
    const doctor = idea(['doctor'], {expectOk: false});
    if (shown.status !== 'archived' || !archived) throw new Error(`not archived: status ${shown.status}, snapshot ${archived}`);
    if (doctor.status !== 0) throw new Error('doctor unhealthy after registration');
    return `copied by pointer into a granted clipboard (matches the generated prompt); register-plan ${registered.plan?.plan_id ?? 'ok'}; revision ${trace[2]} archived; doctor healthy`;
  });

  // Agent disconnect: revoke the agent, reload, and confirm the browser says so and still saves by hand.
  await step('agent_disconnect_and_human_path', async () => {
    const closed = idea(['session-close', '--session', opened.session_id, '--runtime-root', runtimeRoot]).json;
    if (closed.agent_status !== 'disconnected') throw new Error('session-close did not disconnect: ' + JSON.stringify(closed));
    await cdp.send('Page.reload');
    try { await waitFor(`(document.getElementById('agent-status')?.textContent||'').startsWith('Agent disconnected.')`, 'disconnected agent status', 20000); }
    catch { throw new Error('page still reads "' + await read(`document.getElementById('agent-status')?.textContent`) + '" 20 s after session-close returned disconnected'); }
    const text = await read(`document.getElementById('agent-status').textContent`);
    await tabTo('show-ideas'); await press('Enter');
    await waitFor(`!!document.getElementById('new-idea')&&!document.getElementById('new-idea').disabled`, 'ideas view');
    await click('#new-idea');
    await waitFor(`document.getElementById('identity').textContent==='New idea'`, 'new idea');
    await typeInto('#idea-text', 'Synthetic idea captured with the agent disconnected.');
    await typeInto('#workspace-name', 'offline-workspace');
    await typeInto('#workspace-path', workspace);
    await click('#workspace-confirmed');
    await accept('capture', 'capture-accept');
    return 'agent status after session-close: "' + text.slice(0, 90) + '"; a new capture still saved by hand';
  });

  // Keyboard-only Capture on a new idea.
  await step('keyboard_only_capture', async () => {
    await cdp.send('Page.reload');
    await waitFor(`document.getElementById('save-status')?.textContent==='Saved state loaded'`, 'reload for keyboard run');
    await tabTo('show-ideas'); await press('Enter');
    await waitFor(`!!document.getElementById('new-idea')&&!document.getElementById('new-idea').disabled`, 'ideas view');
    await tabTo('new-idea'); await press('Enter');
    await waitFor(`!!document.getElementById('idea-text')&&document.getElementById('identity').textContent==='New idea'`, 'new idea capture');
    await tabTo('idea-text'); await typeKeys(SYNTH_WORDS);
    await tabTo('workspace-name'); await typeKeys('kb-workspace');
    await tabTo('workspace-path'); await typeKeys(workspace);
    await tabTo('workspace-confirmed'); await press('Space');
    await tabTo('capture-accept'); await press('Enter');
    await waitFor(`document.getElementById('compact-capture')?.dataset.status==='saved'`, 'keyboard capture saved');
    await shot('07-wide-keyboard-capture-saved');
    return 'Tab/typing/Space/Enter only; second idea captured and marked saved';
  });

  // Save conflict: another writer changes the idea, then the browser saves a stale edit.
  await step('save_conflict_stale_revision', async () => {
    // The conflict runs on the keyboard-captured idea, so the handoff idea's packet stays current.
    const handoffId = /idea_id:\s*(idea_[0-9a-f]{32})/.exec(promptText)?.[1];
    const target = idea(['list']).json.ideas.find(i => i.idea_id !== handoffId);
    if (!target) throw new Error('no second idea for the conflict');
    await click('#show-ideas');
    await click(`#ideas-open-${target.idea_id}`);
    await waitFor(`document.getElementById('identity').textContent.startsWith(${JSON.stringify(target.idea_id)})`, 'second idea reopened');
    await ensureStep('priorities');
    const revision = idea(['show', target.idea_id]).json.idea.revision;
    idea(['rate', target.idea_id, '--urgency', '3', '--importance', '3', '--expected-revision', String(revision), '--actor', 'operator']);
    await click('#urgency-2'); await click('#importance-2');
    await click('#priorities-accept');
    await waitFor(`!!document.querySelector('#step-body .error-box')`, 'conflict error box', 20000);
    const message = await read(`document.querySelector('#step-body .error-box').textContent`);
    const still = await read(`document.getElementById('urgency-2').getAttribute('aria-pressed')`);
    await shot('06-wide-save-conflict');
    if (still !== 'true') throw new Error('answers were not preserved in the buffer');
    // The stale-revision message specifically, not any error box (connection loss, validation...).
    if (!message.includes('Another change was saved')) throw new Error('not the stale-revision message: ' + message.slice(0, 140));
    return 'stale-revision conflict surfaced and answer preserved: ' + message.slice(0, 140);
  });

  // Agent resume: the agent was disconnected by session-close; resume the same binding and re-pair the page.
  let agentSession = null;
  const resumeOk = await step('agent_resume', async () => {
    const started = Date.now();
    const resumed = idea(['session-open', '--resume', bindingId, '--runtime-root', runtimeRoot], {expectOk: false});
    if (resumed.json?.pairing_code) secrets.add(resumed.json.pairing_code);
    if (resumed.status !== 0 || resumed.json?.ok === false) throw new Error('session-open --resume failed after ' + (Date.now() - started) + ' ms: code ' + (resumed.json?.error?.code ?? resumed.json?.code ?? 'unknown') + '; ' + redact(JSON.stringify(resumed.json)).slice(0, 200));
    if (resumed.json.binding_id !== bindingId) throw new Error('binding changed on agent resume');
    agentSession = resumed.json.session_id;
    if (!/^session_[0-9a-f]{32}$/.test(agentSession ?? '')) throw new Error('resume returned no session_id');
    // By design a resume keeps the durable session id and rotates the credentials (fresh pairing code).
    if (agentSession !== opened.session_id) throw new Error('resume changed the durable session_id');
    if (!resumed.json.pairing_code || resumed.json.pairing_code === opened.pairing_code) throw new Error('resume did not rotate the pairing credential');
    // The save-conflict step leaves unsaved answers, so the page asks before leaving (beforeunload): choose Leave.
    const seen = cdp.events.length;
    const leaving = (new URL(resumed.json.origin).origin !== new URL(origin).origin ? cdp.send('Page.navigate', {url: resumed.json.origin}) : cdp.send('Page.reload')).catch(() => {});
    let dialog = null;
    for (let i = 0; i < 40 && !dialog; i++) { dialog = cdp.events.slice(seen).find(e => e.method === 'Page.javascriptDialogOpening'); if (!dialog) await sleep(100); }
    if (dialog) await cdp.send('Page.handleJavaScriptDialog', {accept: true});
    await leaving;
    try { await waitFor(`!!document.getElementById('pairing-code')||document.getElementById('save-status')?.textContent==='Saved state loaded'`, 'pairing form or saved state', 20000); }
    catch {
      const probe = async expr => { try { return String(await read(expr)).slice(0, 160); } catch (error) { return 'eval error: ' + String(error.message).slice(0, 80); } };
      const origin2 = new URL(resumed.json.origin).origin;
      const ping = await fetch(resumed.json.origin, {signal: AbortSignal.timeout(8000)}).then(r => 'origin answers ' + r.status, e => 'origin error ' + e.name).catch(() => '?');
      throw new Error(`page shows neither a pairing form nor saved state 20 s after resume; origin ${origin2 === new URL(origin).origin ? 'unchanged' : 'changed'}; ${ping}; href ${await probe('location.href')}; ready ${await probe('document.readyState')}; body "${redact(await probe('document.body?.innerText.slice(0, 120)'))}"; console errors ${cdp.events.filter(e => e.method === 'Runtime.exceptionThrown').length}`);
    }
    let repaired = false;
    if (await read(`!!document.getElementById('pairing-code')`)) {
      await click('#pairing-code'); await typeKeys(resumed.json.pairing_code); await press('Enter'); repaired = true;
    }
    await waitFor(`document.getElementById('save-status')?.textContent==='Saved state loaded'`, 'saved state after resume', 20000);
    await waitFor(`document.getElementById('agent-status')?.textContent==='Agent connected'`, 'agent status connected', 20000);
    return `session-open --resume kept the binding and durable session, rotated the pairing credential; page ${repaired ? 're-paired with per-character key events' : 'reloaded without re-pairing'}; agent status reads "Agent connected" (${Date.now() - started} ms)${dialog ? '; leave-page prompt accepted' : ''}`;
  });

  // Agent proposals for Shape, Method and Assess on a NEW idea: real browser input, fixed fixture agent.
  const cursor = {n: 0};
  let proposalIdea = null;
  const savedOf = () => idea(['show', proposalIdea]).json.idea;
  // The current accepted value of a step field, from the workflow state only (never drafts or history).
  const acceptedAt = (stepKey, value) => {
    const paths = [];
    const walk = (node, path) => {
      if (node === value) paths.push(path.join('.'));
      else if (node && typeof node === 'object') for (const [k, v] of Object.entries(node)) walk(v, [...path, k]);
    };
    walk(savedOf(), []);
    const prefix = `workflow.steps.${stepKey}.fields.`;
    const hit = paths.find(path => path.startsWith(prefix));
    if (!hit) throw new Error(`edited value is not the accepted ${stepKey} field; found at: ${paths.join(', ') || 'nowhere'}`);
    return hit;
  };
  // Reaching the step starts the terminal conversation by itself: no request button exists.
  const requestAndUse = async (key, wait, expectShown, beforeUse = null) => {
    await ensureStep(key);
    if (await read(`!!document.getElementById(${JSON.stringify(key + '-request')})`)) throw new Error(key + ' still has a request button');
    await waitFor(`!!document.getElementById(${JSON.stringify(key + '-proposal-status')})`, key + ' waiting panel (automatic request)', 20000);
    const waiting = await read(`document.getElementById(${JSON.stringify(key + '-proposal-status')}).textContent`);
    const log = await wait();
    await waitFor(`!!document.querySelector('[id^="${key}-use-proposal-"]')`, key + ' suggestion shown', 30000);
    // The card beside the Use control must show this fixture's own suggestion text.
    const shown = await read(`(document.querySelector('[id^="${key}-use-proposal-"]')?.closest('section,article,div,li')?.textContent ?? '')`);
    if (!shown.includes(expectShown)) throw new Error(`${key} suggestion card does not show the fixture text "${expectShown}": ` + shown.slice(0, 120));
    if (beforeUse) await beforeUse();
    await click(`[id^="${key}-use-proposal-"]`);
    return {log, waiting, shown};
  };
  const agentObs = (log, extra) => `event operation ${log.operation} received${log.reply_delay_s ? ', replied after a ' + log.reply_delay_s + ' s compose delay' : ''}${log.memory_event ? ' (after a memory event answered unavailable, ok ' + log.memory_ok + ')' : ''}; reply accepted by helper: ${log.reply_ok}; ${extra}`;
  if (resumeOk) {
    await step('agent_proposals_new_idea_setup', async () => {
      await click('#show-ideas');
      await waitFor(`!!document.getElementById('new-idea')&&!document.getElementById('new-idea').disabled`, 'ideas view');
      await click('#new-idea');
      await waitFor(`document.getElementById('identity').textContent==='New idea'`, 'new idea');
      await typeInto('#idea-text', 'Synthetic idea for the fixture agent proposals.');
      await typeInto('#workspace-name', 'agent-workspace');
      await typeInto('#workspace-path', workspace);
      await click('#workspace-confirmed');
      await accept('capture', 'capture-accept');
      await ensureStep('priorities');
      await click('#urgency-6'); await click('#importance-6');
      await accept('priorities', 'priorities-accept');
      proposalIdea = (await read(`document.getElementById('identity').textContent`)).match(/idea_[0-9a-f]{32}/)?.[0];
      if (!proposalIdea) throw new Error('new idea id not shown');
      return 'new idea captured and both priorities set by pointer';
    });
  }
  if (resumeOk && proposalIdea) {
    await step('agent_proposal_shape', async () => {
      const raw = 'Fixture suggested desired result.', edited = 'Human edited desired result.';
      const {log, waiting, shown} = await requestAndUse('shape', () => serveAgent(agentSession, proposalIdea, 'shape', cursor, () => ({
        outcome: raw, scope: 'small-change', scope_reason: 'Fixture scope reason.',
        alternatives: [{route: 'Fixture route', reason: 'Fixture reason.'}], assumptions: ['Fixture risk.'], next_slice: 'Fixture next slice.', learning: [],
      })), raw);
      const filled = await read(`document.getElementById('shape-outcome').value`);
      if (filled !== raw) throw new Error('use did not copy the suggestion into the draft: ' + filled.slice(0, 80));
      await replaceText('#shape-outcome', edited);
      await accept('shape', 'shape-accept');
      const shapeAt = acceptedAt('shape', edited);
      if (shapeAt !== 'workflow.steps.shape.fields.outcome') throw new Error('edited value accepted in the wrong Shape field: ' + shapeAt);
      await shot('08-agent-shape-saved');
      return agentObs(log, `waiting panel "${waiting.slice(0, 50)}"; suggestion shown "${shown.slice(0, 60)}"; field edited: desired result; accepted at ${shapeAt}`);
    });
    await step('agent_proposal_method', async () => {
      const raw = 'Fixture recommended reason.', edited = 'Human edited method reason.';
      await ensureStep('method');
      const {log, waiting, shown} = await requestAndUse('method', () => serveAgent(agentSession, proposalIdea, 'method', cursor, () => ({
        selection: 'bounded-plan', reason: raw, investment: null, experiment: null, memory: {status: 'unavailable', sources: [], rationale: null},
      })), raw, async () => {
        // The human chooses a method different from the fixture's recommendation; using the
        // recommendation must never change that choice (only the human selects a method).
        await click('#method-choice-adaptive-slices');
        const choiceBefore = await read(`document.querySelector('[id^="method-choice-"][aria-pressed="true"]')?.id ?? null`);
        if (choiceBefore !== 'method-choice-adaptive-slices') throw new Error('the human method choice did not register before Use: ' + choiceBefore);
        await shot('09-method-selected');
      });
      const filled = await read(`document.getElementById('method-reason').value`);
      if (filled !== raw) throw new Error('use did not copy the recommendation reason: ' + filled.slice(0, 80));
      const choiceAfterUse = await read(`document.querySelector('[id^="method-choice-"][aria-pressed="true"]')?.id ?? null`);
      if (choiceAfterUse !== 'method-choice-adaptive-slices') throw new Error('using the recommendation changed the human method choice to ' + choiceAfterUse);
      await replaceText('#method-reason', edited);
      await accept('method', 'method-accept');
      const methodReason = acceptedAt('method', edited);
      if (methodReason !== 'workflow.steps.method.fields.reason') throw new Error('edited value accepted in the wrong Method field: ' + methodReason);
      const selection = savedOf().workflow?.steps?.method?.fields?.selection;
      if (selection !== 'adaptive-slices') throw new Error('accepted method is not the human choice: ' + selection);
      return agentObs(log, `waiting panel "${waiting.slice(0, 50)}"; recommendation shown "${shown.slice(0, 60)}"; field edited: method reason; saved at ${methodReason}; human choice adaptive-slices kept over the bounded-plan recommendation`);
    });
    await step('agent_proposal_assess', async () => {
      const edited = 'Human edited assessment basis.';
      // Assess unlocks after the optional design step is decided; skip it by hand with a reason.
      await ensureStep('visualize');
      await click('#visualize-skipped');
      await typeInto('#visualize-reason', 'Synthetic: not needed for the agent proposal idea.');
      await accept('visualize', 'visualize-accept', ['skipped', 'not-applicable']);
      const {log, waiting, shown} = await requestAndUse('assess', () => serveAgent(agentSession, proposalIdea, 'assessment', cursor, event => {
        const order = event.data?.backlog?.order ?? [];
        const rest = order.filter(id => id !== proposalIdea);
        return {assessment: {method: 'wsjf', version: 'fixture-v1', inputs: {value: 3, time_criticality: null, enablement: 2, effort: 1},
          basis: 'Fixture estimates.', assumptions: [], confidence: 'low', provenance: 'Fixture agent'},
          position: {proposed_position: 1, actual_position: 1, neighbors: {before: null, after: rest[0] ?? null}, override_reason: null}};
      }), 'Fixture estimates.');
      const filled = await read(`document.getElementById('assess-basis').value`);
      if (filled !== 'Fixture estimates.') throw new Error('use did not copy the assessment: ' + filled.slice(0, 80));
      await shot('10-assess-selected');
      await read(`document.getElementById('assess-method-help')?.scrollIntoView({block: 'start'})`);
      await sleep(150);
      await shot('10c-assess-help');
      await replaceText('#assess-basis', edited);
      await accept('assess', 'assess-accept');
      const assessAt = acceptedAt('assess', edited);
      if (assessAt !== 'workflow.steps.assess.fields.assessment.basis') throw new Error('edited value accepted in the wrong Assess field: ' + assessAt);
      return agentObs(log, `waiting panel "${waiting.slice(0, 50)}"; fixture suggestion shown; field edited: assessment basis; accepted at ${assessAt}`);
    });
  }

  if (resumeOk && proposalIdea) {
    // Redesign R2: reaching Shape starts the terminal conversation by itself; the terminal writes each
    // agreed field to the page with the native `fill` verb; the human still decides and presses Accept.
    await step('agent_conversation_fills_shape', async () => {
      await click('#show-ideas');
      await waitFor(`!!document.getElementById('new-idea')&&!document.getElementById('new-idea').disabled`, 'ideas view');
      await click('#new-idea');
      await waitFor(`document.getElementById('identity').textContent==='New idea'`, 'new idea');
      await typeInto('#idea-text', 'Synthetic idea for the terminal conversation fills.');
      await typeInto('#workspace-name', 'talk-workspace');
      await typeInto('#workspace-path', workspace);
      await click('#workspace-confirmed');
      await accept('capture', 'capture-accept');
      await ensureStep('priorities');
      await click('#urgency-5'); await click('#importance-5');
      await accept('priorities', 'priorities-accept');
      const talkIdea = (await read(`document.getElementById('identity').textContent`)).match(/idea_[0-9a-f]{32}/)?.[0];
      if (!talkIdea) throw new Error('new idea id not shown');
      proposalIdea = talkIdea;
      await ensureStep('shape');
      await waitFor(`!!document.getElementById('shape-proposal-status')||/Your terminal is guiding/.test(document.getElementById('shape-conversation-status')?.textContent??'')`, 'shape conversation started', 20000);
      const first = 'Terminal agreed desired result.', route = 'Terminal agreed route', why = 'Terminal agreed reason.', risk = 'Terminal agreed risk.';
      let event = null;
      for (let attempt = 0; attempt < 6 && !event; attempt++) {
        const batch = idea(['events', '--session', agentSession, '--after', String(cursor.n), '--timeout', '5', '--runtime-root', runtimeRoot]).json;
        cursor.n = batch.sequence;
        event = batch.events.find(item => item.idea_id === talkIdea && item.operation === 'shape') ?? null;
      }
      if (!event) throw new Error('the automatic Shape request never reached the agent');
      const seq1 = agentFill(agentSession, event, {outcome: first}, 1);
      await waitFor(`document.getElementById('shape-outcome')?.value===${JSON.stringify(first)}`, 'first fill shown in the outcome field', 20000);
      const seq2 = agentFill(agentSession, event, {alternatives: [{route, reason: why}], assumptions: [risk]}, 2);
      await waitFor(`document.getElementById('shape-alternative-0-route')?.value===${JSON.stringify(route)}&&document.getElementById('shape-assumptions-0')?.value===${JSON.stringify(risk)}`, 'second fill shown in alternatives and assumptions', 20000);
      const notes = await read(`JSON.stringify(['shape-outcome-filled','shape-alternatives-filled','shape-assumptions-filled'].map(id => document.getElementById(id)?.textContent ?? null))`);
      if (JSON.parse(notes).some(text => text !== 'From your terminal conversation')) throw new Error('filled notes missing: ' + notes);
      const line = await read(`document.getElementById('shape-conversation-status').textContent`);
      if (!/3 answers filled from your terminal/.test(line)) throw new Error('status line does not count the fills: ' + line);
      await shot('10b-agent-conversation-fills-shape');
      // The human owns the rest: scope, its reason and the next slice, by real input; then Accept.
      await click('#shape-scope-small-change');
      if (!await read(`document.getElementById('shape-scope-reason').value===''`)) throw new Error('scope reason unexpectedly pre-filled');
      await typeInto('#shape-scope-reason', 'My own scope reason.');
      await typeInto('#shape-next-slice', 'My own next slice.');
      await accept('shape', 'shape-accept');
      const at = acceptedAt('shape', first);
      if (at !== 'workflow.steps.shape.fields.outcome') throw new Error('the filled outcome was not accepted as the Shape outcome: ' + at);
      return `agent got the automatic shape event; fill ${seq1} {outcome} and fill ${seq2} {alternatives, assumptions} appeared in the page with the note "From your terminal conversation"; status "${line.slice(0, 90)}"; the human chose scope and typed the rest, pressed Accept; accepted at ${at}`;
    });
  }

  if (resumeOk && proposalIdea) {
    // Demo finding: once a Shape suggestion was in use and the agent dropped, the human could
    // not finish Shape with their own answers ("I cant go back to the human path").
    await step('agent_drop_human_path_shape', async () => {
      await click('#show-ideas');
      await waitFor(`!!document.getElementById('new-idea')&&!document.getElementById('new-idea').disabled`, 'ideas view');
      await click('#new-idea');
      await waitFor(`document.getElementById('identity').textContent==='New idea'`, 'new idea');
      await typeInto('#idea-text', 'Synthetic idea for the agent-drop human path.');
      await typeInto('#workspace-name', 'drop-workspace');
      await typeInto('#workspace-path', workspace);
      await click('#workspace-confirmed');
      await accept('capture', 'capture-accept');
      await ensureStep('priorities');
      await click('#urgency-4'); await click('#importance-5');
      await accept('priorities', 'priorities-accept');
      const dropIdea = (await read(`document.getElementById('identity').textContent`)).match(/idea_[0-9a-f]{32}/)?.[0];
      if (!dropIdea) throw new Error('new idea id not shown');
      proposalIdea = dropIdea;  // acceptedAt/savedOf read this idea from here on (last step that uses them)
      const raw = 'Fixture result before the agent dropped.', mine = 'My own desired result after the drop.';
      await requestAndUse('shape', () => serveAgent(agentSession, dropIdea, 'shape', cursor, () => ({
        outcome: raw, scope: 'small-change', scope_reason: 'Fixture scope reason.',
        alternatives: [{route: 'Fixture route', reason: 'Fixture reason.'}], assumptions: [], next_slice: 'Fixture next slice.', learning: [],
      })), raw);
      const closed = idea(['session-close', '--session', agentSession, '--runtime-root', runtimeRoot]).json;
      if (closed.agent_status !== 'disconnected') throw new Error('session-close did not disconnect: ' + JSON.stringify(closed));
      await waitFor(`document.getElementById('agent-status')?.textContent!=='Agent connected'`, 'agent shown disconnected', 20000);
      await replaceText('#shape-outcome', mine);
      const before = await read(`JSON.stringify({accept_disabled: document.getElementById('shape-accept')?.disabled ?? null, own: document.getElementById('shape-accept-own') ? (document.getElementById('shape-accept-own').disabled ? 'disabled' : 'enabled') : 'absent'})`);
      await shot('11-agent-drop-shape');
      // The way back sits in the step's foot beside the disabled Accept, with its reason.
      if (!await read(`document.getElementById('shape-accept').disabled`)) throw new Error('Accept should be disabled while the dropped suggestion is linked: ' + before);
      if (!await read(`!!document.getElementById('shape-accept-own')&&!document.getElementById('shape-accept-own').disabled`))
        throw new Error('after the agent dropped, no enabled "accept as my own answers" beside Accept: ' + before);
      const reason = await read(`document.getElementById('shape-accept-own-reason')?.textContent ?? ''`);
      if (!/can no longer be accepted/.test(reason)) throw new Error('the own-answers door does not say why: ' + reason.slice(0, 80));
      await accept('shape', 'shape-accept-own');
      const shapeAt = acceptedAt('shape', mine);
      if (shapeAt !== 'workflow.steps.shape.fields.outcome') throw new Error('my own answer was not accepted as the Shape outcome: ' + shapeAt);
      const linked = JSON.stringify(savedOf().workflow?.steps?.shape?.acceptance ?? {});
      if (/proposal_[0-9a-f]{32}/.test(linked)) throw new Error('the own-answers accept still carries a suggestion link: ' + linked.slice(0, 160));
      return `after the drop the page offered ${before}; my own Shape was accepted at ${shapeAt}`;
    });
  }

  // Backlog: top-bar nav, colour-coded cards with Resume, read-only Markdown, human re-ranking (real clicks).
  await step('backlog_cards_markdown_rerank', async () => {
    await click('#nav-ideas');
    await waitFor(`document.querySelectorAll('#ideas-list .backlog-card').length>0&&!!document.getElementById('new-idea')`, 'backlog cards');
    const rows = idea(['list']).json.ideas;
    const cards = await read(`[...document.querySelectorAll('#ideas-list .backlog-card')].map(c=>({id:c.dataset.ideaId,status:c.dataset.status,chip:c.querySelector('.status-chip')?.textContent,title:c.querySelector('h2')?.textContent,text:c.textContent}))`);
    if (cards.length !== rows.length) throw new Error(`cards ${cards.length} != ideas ${rows.length}`);
    if (!cards.every(c => c.chip && c.chip.length > 3)) throw new Error('a card has no status chip text');
    const resumable = cards.filter(c => /Step \d of 7 · Resume/.test(c.text));
    if (!resumable.length) throw new Error('no card shows "Step N of 7 · Resume"');
    const chips = [...new Set(cards.map(c => c.chip))].join(' / ');
    // Markdown, read-only in the page.
    const target = cards.find(c => c.status !== 'archived') ?? cards[0];
    target.idea_id = target.id;
    await click(`#backlog-md-${target.idea_id}`);
    await waitFor(`!!document.getElementById('backlog-md-text')||/Markdown file yet/.test(document.getElementById('backlog-md-panel')?.textContent??'')`, 'markdown panel');
    const md = await read(`document.getElementById('backlog-md-text')?.tagName==='PRE'?document.getElementById('backlog-md-text').textContent:null`);
    const word = target.title.split(/\s+/).find(w => w.length > 3) ?? target.title;
    if (md === null || !md.includes(word)) throw new Error('the Markdown <pre> does not contain the title word "' + word + '"');
    await click('#backlog-md-close');
    await waitFor(`!document.getElementById('backlog-md-text')`, 'markdown closed');
    // Re-rank: move the last movable idea up by one with a real click, then read the order back from the CLI.
    const movable = cards.map((c, i) => ({idea_id: c.id, status: c.status, position: i + 1})).filter(c => c.status !== 'archived');
    const mover = movable[movable.length - 1];
    if (!mover || mover.position < 2) throw new Error('need a movable idea below position 1');
    const before = rows.map(r => r.idea_id);
    if (before.join() !== cards.map(c => c.id).join()) throw new Error('cards do not follow the CLI order');
    await click(`#backlog-up-${mover.idea_id}`);
    await waitFor(`/^Moved to position ${mover.position - 1}\\./.test(document.getElementById('backlog-notice')?.textContent??'')`, 'moved notice');
    const after = idea(['list']).json.ideas.map(r => r.idea_id);
    if (after.indexOf(mover.idea_id) !== before.indexOf(mover.idea_id) - 1) throw new Error('order did not change: ' + after.join(','));
    const shown = await read(`[...document.querySelectorAll('#ideas-list .backlog-card')].map(c=>c.dataset.ideaId)`);
    if (shown.join() !== after.join()) throw new Error('cards do not follow the accepted order after the move');
    await read(`(document.getElementById('step-body').scrollTop=0,true)`);
    await shot('12-backlog');
    return `${cards.length} cards (${chips}); ${resumable.length} with Step N of 7 · Resume; Markdown read-only in <pre> (contains "${word}"); moved one idea up ${mover.position}->${mover.position - 1}, CLI order and cards agree`;
  });

  // Setup pane: Glitch native is the default store; with nothing configured, Test connection says so plainly.
  await step('setup_pane', async () => {
    await click('#nav-setup');
    await waitFor(`!!document.getElementById('setup-native')&&!!document.getElementById('setup-test')`, 'setup pane');
    const pressed = await read(`({native:document.getElementById('setup-native').getAttribute('aria-checked'),api:document.getElementById('setup-api').getAttribute('aria-checked'),status:document.getElementById('setup-status')?.textContent,current:document.getElementById('nav-setup').getAttribute('aria-current')})`);
    if (pressed.native !== 'true' || pressed.api !== 'false') throw new Error('Glitch native is not the pressed choice: ' + JSON.stringify(pressed));
    if (!/^Ideas are saved in Glitch\./.test(pressed.status ?? '') || pressed.current !== 'page') throw new Error('status or nav state wrong: ' + JSON.stringify(pressed));
    await click('#setup-test');
    await waitFor(`/address and a key/.test(document.getElementById('setup-test-result')?.textContent??'')`, 'api_not_configured message');
    const leaked = await read(`document.body.innerText.includes('Authorization')||document.getElementById('setup-key')!==null`);
    if (leaked) throw new Error('an API key field is present while native is chosen');
    await shot('13-setup');
    await click('#setup-api');
    await waitFor(`!!document.getElementById('setup-url')&&document.getElementById('setup-key')?.type==='password'`, 'API fields');
    await shot('13b-setup-api-fields');
    await click('#setup-return');
    await waitFor(`!document.getElementById('setup-native')`, 'returned to the workflow');
    return 'Setup pane opens; Glitch native pressed; status says ideas are saved in Glitch; Test connection says an address and a key are needed (api_not_configured); returned to the idea';
  });

  summary.console_exceptions = cdp.events.filter(e => e.method === 'Runtime.exceptionThrown').length;
  record('no_page_exceptions', summary.console_exceptions === 0, summary.console_exceptions + ' uncaught page exceptions');
  cdp.close();
  try { process.kill(-chrome.pid, 'SIGKILL'); } catch {}

  // ---- outside the browser ----
  await step('crash_restart_resume', async () => {
    const before = idea(['list']).json.ideas.length;
    const pids = serviceProcesses();
    if (!pids.length) throw new Error('service process not found');
    for (const pid of pids) process.kill(pid, 'SIGKILL');
    await sleep(800);
    if (serviceProcesses().length) throw new Error('service survived kill');
    const resumed = idea(['session-open', '--resume', bindingId, '--runtime-root', runtimeRoot]).json;
    secrets.add(resumed.pairing_code);
    if (resumed.binding_id !== bindingId) throw new Error('binding changed on resume');
    const after = idea(['list']).json.ideas.length;
    if (after !== before) throw new Error(`idea count ${before} -> ${after}`);
    const ping = await fetch(resumed.origin, {signal: AbortSignal.timeout(10000)});
    if (!ping.ok) throw new Error('resumed origin did not answer: ' + ping.status);
    return `killed ${pids.length} service process(es); resume kept binding; ${after} ideas intact; new origin serves`;
  });

  await step('concurrent_cli_writers', async () => {
    const before = idea(['list']).json.ideas.length;
    const files = [0, 1, 2].map(i => { const f = join(tmp, `cw${i}.txt`); writeFileSync(f, 'Synthetic concurrent writer ' + i); return f; });
    const results = await Promise.all(files.map(f => ideaAsync(['capture', '--text-file', f, '--actor', 'operator'])));
    if (!results.every(r => r.status === 0 && r.json?.ok !== false)) throw new Error('a writer failed: ' + redact(results.map(r => r.status)));
    const after = idea(['list']).json.ideas.length;
    const doctor = idea(['doctor'], {expectOk: false});
    if (after !== before + 3) throw new Error(`expected ${before + 3} ideas, got ${after}`);
    if (doctor.status !== 0) throw new Error('doctor unhealthy');
    return '3 parallel capture writers all landed; doctor healthy';
  });
}

let fatal = null;
const watchdog = setTimeout(() => {
  record('harness_watchdog', false, `run exceeded ${RUN_LIMIT_MS / 1000}s`);
  cleanup();
  try { writeFileSync(join(EVIDENCE, 'summary.json'), redact(summary) + '\n'); } catch {}
  process.stdout.write(redact(summary) + '\n');
  process.exit(1);
}, RUN_LIMIT_MS);
try { await main(); }
catch (error) { fatal = String(error.message ?? error); record('harness_fatal', false, fatal); }
finally {
  summary.finished = new Date().toISOString();
  summary.not_covered = [
    'successful design-set upload/accept (file chooser is not real pointer/keyboard input)',
    'a real model agent\'s proposals and any memory capability (the fixture agent is fixed and answers Memory as unavailable); the attended SSH rows cover a real agent (recipe section 6 step 7); a real memory capability counts only with a found or searched_no_preference answer',
    'screen reader (NVDA/VoiceOver) output; only the Chromium accessibility tree is captured',
    'browser/OS zoom; clipboard deny with manual copy under a real permission prompt',
    'attended human pointer/keyboard qualification, Windows, macOS, Orca and SSH rows',
  ];
  cleanup();
  summary.cleanup = {runtime_root_removed: !runtimeRoot || !existsSync(runtimeRoot), temp_removed: !tmp || !existsSync(tmp)};
  try { writeFileSync(join(EVIDENCE, 'summary.json'), redact(summary) + '\n'); } catch {}
  const out = JSON.parse(redact(summary));
  process.stdout.write(JSON.stringify(out, null, 2) + '\n');
  clearTimeout(watchdog);
  process.exitCode = summary.required_failures || fatal ? 1 : 0;
}
