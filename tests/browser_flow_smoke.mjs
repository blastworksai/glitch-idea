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
// --agent-reply-delay makes the fixture agent sit on the Exploration request that long before
// replying, as a composing model would (answer window check; over 300 s it must drop).
import {spawn, spawnSync} from 'node:child_process';
import {mkdtempSync, mkdirSync, rmSync, rmdirSync, writeFileSync, readFileSync, existsSync, chmodSync, readdirSync, cpSync} from 'node:fs';
import {tmpdir, homedir, userInfo} from 'node:os';
import {join, resolve, dirname} from 'node:path';
import {randomBytes, createHash} from 'node:crypto';
import {deflateSync} from 'node:zlib';
import {fileURLToPath} from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..');
const argv = process.argv.slice(2);
const opt = (name, fallback) => { const i = argv.indexOf('--' + name); return i >= 0 ? argv[i + 1] : fallback; };
const PYTHON = opt('python', 'python3');
const HELPER = opt('helper', join(ROOT, 'glitch-idea/scripts/idea.py'));
// The helper reads config.json beside its skill (and one level up). A local default_workspace pre-fills Capture and
// the typed path is appended to it, so the run would fail for a reason that is not the code: refuse it up front.
for (const cfg of [join(dirname(dirname(resolve(HELPER))), 'config.json'), join(dirname(dirname(dirname(resolve(HELPER)))), 'config.json')]) {
  let local = null; try { local = JSON.parse(readFileSync(cfg, 'utf8')); } catch {}
  if (local?.default_workspace) { console.error('smoke refused: ' + cfg + ' sets default_workspace; run from a tree without a local config'); process.exit(2); }
}
const CHROME = opt('chrome',existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : '/usr/bin/chromium');
const STAMP = new Date().toISOString().replace(/[:.]/g, '-');
const EVIDENCE = resolve(opt('evidence', join(ROOT, 'evidence/browser-smoke', STAMP)));
const ME = userInfo().username;
const REPLY_DELAY_S = Number(opt('agent-reply-delay', '0'));
if (!Number.isFinite(REPLY_DELAY_S) || REPLY_DELAY_S < 0 || REPLY_DELAY_S > 600) throw new Error('--agent-reply-delay must be 0..600 seconds');

const summary = {
  harness: 'browser_flow_smoke', started: new Date().toISOString(), node: process.version,
  input_model: 'CDP Input.dispatchMouseEvent/dispatchKeyEvent/insertText at DOM-read coordinates; Runtime.evaluate is read-only. ' +
    'Proves page event handling in headless Chrome only; NOT an attended human qualification.',
  agent_path: 'human-edit path for the main journey; agent Resume and agent proposals (Method memory, Discovery, Exploration, Assess) are exercised with a fixed fixture agent that drives the documented native events/respond verbs with synthetic replies (not a model); the browser side is real CDP input',
  python: PYTHON, helper: HELPER, agent_reply_delay_s: REPLY_DELAY_S, steps: [], required_failures: 0,
};
const RUN_LIMIT_MS = 600000 + REPLY_DELAY_S * 1000;  // whole-run deadline: every wait below is bounded, and this is the backstop
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
  // scrollIntoView honours the page's scroll-padding, so a field is brought clear of the sticky step footer, as a person scrolling would.
  await read(`(document.querySelector(${JSON.stringify(selector)})?.scrollIntoView({block:'nearest',inline:'nearest'}),true)`);
  await sleep(40);
  const {model} = await cdp.send('DOM.getBoxModel', {nodeId});
  const q = model.content;
  return [(q[0] + q[2] + q[4] + q[6]) / 4, (q[1] + q[3] + q[5] + q[7]) / 4];
}
async function click(selector) {
  try { await waitFor(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});return !!e&&!e.disabled})()`, 'enabled ' + selector); }
  catch (error) {
    const why = await read(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});return e?{disabled:e.disabled,hidden:e.hidden,inFieldset:!!e.closest('fieldset[disabled]'),text:(e.textContent||'').slice(0,40)}:'absent'})()`);
    throw new Error(error.message + ' ' + JSON.stringify(why));
  }
  const [x, y] = await center(selector);
  // A real pointer only reaches what is on top at that point: fail loudly when something (for example the sticky footer) covers the target.
  const hit = await read(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});const t=document.elementFromPoint(${x},${y});return !!e&&!!t&&(e===t||e.contains(t)||!!t.closest('label')&&t.closest('label').contains(e))?null:{covered_by:t?(t.id||t.className||t.tagName):null,y:${y},innerH:innerHeight,footTop:Math.round(document.querySelector('.panel-foot')?.getBoundingClientRect().top)}})()`);
  if (hit) { try { await shot('covered-' + selector.replace(/[^a-z0-9]+/gi, '_').slice(0, 40)); } catch {} throw new Error('click target ' + selector + ' is covered: ' + JSON.stringify(hit)); }
  await cdp.send('Input.dispatchMouseEvent', {type: 'mouseMoved', x, y});
  await cdp.send('Input.dispatchMouseEvent', {type: 'mousePressed', x, y, button: 'left', clickCount: 1});
  await cdp.send('Input.dispatchMouseEvent', {type: 'mouseReleased', x, y, button: 'left', clickCount: 1});
  await sleep(80);
}
async function typeInto(selector, text) {
  await click(selector);
  const focused = await read(`document.activeElement&&document.activeElement.matches(${JSON.stringify(selector)})`);
  if (!focused) {
    const why = await read(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)return 'element gone';const r=e.getBoundingClientRect();const top=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
      return {rect:[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)],innerH:innerHeight,topEl:top?(top.id||top.className||top.tagName):null,active:document.activeElement?.id||document.activeElement?.tagName,disabled:e.disabled,readOnly:e.readOnly}})()`);
    throw new Error('focus did not land on ' + selector + ': ' + JSON.stringify(why));
  }
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
  if (await currentStep() !== key) await click(sel('compact-' + key));
  await waitFor(`document.querySelector('#compact-nav [aria-current="step"]')?.id==='compact-${key}'`, 'step ' + key);
}
// Entering Method, Discovery, Exploration or Assess with an agent connected starts the terminal conversation by itself;
// while that request is being sent the fields are briefly read-only. Wait it out before hand-typing.
async function settle(key) {
  await sleep(200);
  await waitFor(`!/^Sending/.test(document.getElementById('${key}-proposal-status')?.textContent ?? '')`, key + ' automatic request sent');
  await sleep(200);
}
// Every step shows the APIV strip, no retired wording, and a disabled Accept always says why (and only then).
async function stepChrome(key) {
  const info = await read(`(()=>{const a=document.getElementById(${JSON.stringify(key + '-accept')});const r=document.getElementById(${JSON.stringify(key + '-accept-reason')});
    const text=document.body.innerText;
    const ol=document.querySelector('ol.g-apiv'), cur=ol?.querySelector('li[aria-current="step"]');
    return {apiv: document.querySelectorAll('.g-apiv').length, items: ol?.querySelectorAll('li').length ?? 0, here: cur?.textContent ?? null, label: ol?.getAttribute('aria-label') ?? null,
      role: ol?.getAttribute('role') ?? null, display: ol ? getComputedStyle(ol).display : null, vis: ol ? getComputedStyle(ol).visibility : null, plan: /glitch-plan/.test(ol?.textContent ?? ''),
      hasAccept: !!a, disabled: a?.disabled ?? null, reason: r?.textContent ?? null, describedby: a?.getAttribute('aria-describedby') ?? null, title: a?.title ?? null,
      shape: /\bshape\b/i.test(text), oldNames: /\bbounded[- ]plan\b|\badaptive[- ]slices\b|\bappetite[- ]led\b|\bexperiment[- ]led\b/i.test(text)}})()`);
  if (info.apiv !== 1 || info.items !== 4 || !/^Align/.test(info.here ?? '') || info.role === 'img' || info.display === 'none' || info.vis === 'hidden' || !info.plan || !/Align, Plan, Implement, Verify/.test(info.label ?? '')) throw new Error(key + ': APIV strip missing or wrong: ' + JSON.stringify(info));
  if (info.shape || info.oldNames) throw new Error(key + ': retired wording on screen: ' + JSON.stringify(info));
  if (info.hasAccept && info.disabled && (!info.reason || info.describedby !== key + '-accept-reason' || info.title !== info.reason))
    throw new Error(key + ': disabled Accept has no reason line: ' + JSON.stringify(info));
  return info;
}
async function accept(key, buttonId, okStates = ['saved']) {
  await waitFor(`(()=>{const e=document.getElementById(${JSON.stringify(buttonId)});return !!e&&!e.disabled})()`, 'enabled ' + buttonId);
  if (buttonId === key + '-accept' && await read(`!!document.getElementById(${JSON.stringify(key + '-accept-reason')})`)) throw new Error(key + ': an enabled Accept still shows a reason line');
  await click(sel(buttonId));
  await waitFor(`${JSON.stringify(okStates)}.includes(document.getElementById('compact-${key}')?.dataset.status)`, key + ' saved status');
}
// The Open / Resume button lives in the selected idea's side panel: select the row, then open it.
async function openFromBacklog(id) {
  await click(`#backlog-select-${id}`);
  await waitFor(`document.getElementById('backlog-detail')?.dataset.ideaId===${JSON.stringify(id)}`, 'idea selected');
  await click(`#ideas-open-${id}`);
}
async function step(id, fn, required = true) {
  try { const observation = await fn(); record(id, true, observation ?? '', required); return true; }
  catch (error) { record(id, false, String(error.message ?? error), required); return false; }
}

// ---- tiny valid binaries, built at run time (no binary is committed) -------
const CRC_TABLE = Array.from({length: 256}, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
const crc32 = buf => { let c = 0xffffffff; for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
function makePng(shade) {  // 8x8 RGB, one flat colour; a real image the browser can decode
  const chunk = (type, data) => { const body = Buffer.concat([Buffer.from(type), data]), out = Buffer.alloc(body.length + 8);
    out.writeUInt32BE(data.length, 0); body.copy(out, 4); out.writeUInt32BE(crc32(body), body.length + 4); return out; };
  const head = Buffer.alloc(13); head.writeUInt32BE(8, 0); head.writeUInt32BE(8, 4); head[8] = 8; head[9] = 2;
  const rows = Buffer.concat(Array.from({length: 8}, () => Buffer.concat([Buffer.from([0]), Buffer.alloc(24, shade)])));
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', head), chunk('IDAT', deflateSync(rows)), chunk('IEND', Buffer.alloc(0))]);
}
function makeZip(name, text) {  // one stored entry
  const data = Buffer.from(text), file = Buffer.from(name), crc = crc32(data);
  const local = Buffer.alloc(30); local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(20, 4); local.writeUInt32LE(crc, 14);
  local.writeUInt32LE(data.length, 18); local.writeUInt32LE(data.length, 22); local.writeUInt16LE(file.length, 26);
  const central = Buffer.alloc(46); central.writeUInt32LE(0x02014b50, 0); central.writeUInt16LE(20, 4); central.writeUInt16LE(20, 6);
  central.writeUInt32LE(crc, 16); central.writeUInt32LE(data.length, 20); central.writeUInt32LE(data.length, 24); central.writeUInt16LE(file.length, 28);
  const localPart = Buffer.concat([local, file, data]), centralPart = Buffer.concat([central, file]);
  const end = Buffer.alloc(22); end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(1, 8); end.writeUInt16LE(1, 10);
  end.writeUInt32LE(centralPart.length, 12); end.writeUInt32LE(localPart.length, 16);
  return Buffer.concat([localPart, centralPart, end]);
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
  if (result?.ok !== true) throw new Error('respond for ' + event.operation + ' was not acknowledged ok: ' + redact(JSON.stringify(result)).slice(0, 160));
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
        if (REPLY_DELAY_S && operation === 'exploration') { log.reply_delay_s = REPLY_DELAY_S; await sleep(REPLY_DELAY_S * 1000); }
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
  // Pair by the launcher's fragment URL: the page redeems the code itself, strips the hash, and never needs typing.
  await cdp.send('Page.navigate', {url: origin + '#pair=' + opened.pairing_code});
  await step('pair', async () => {
    try { await waitFor(`document.getElementById('save-status')?.textContent==='Saved state loaded'`, 'paired and loaded by the fragment alone'); }
    catch (error) {
      const form = await read(`!!document.getElementById('pairing-code')`).catch(() => '?');
      const why = 'page did not pair itself from the fragment (pairing form shown: ' + form + '): ' + error.message;
      record('fragment_pairing_auto', false, why);
      throw new Error(why);
    }
    try {
      const hash = await read('location.hash'), href = await read('location.href');
      if (hash !== '') throw new Error('location.hash not cleared after fragment pairing: length ' + hash.length);
      if (href.includes(opened.pairing_code)) throw new Error('location.href still contains the pairing code');
      if (await read(`!!document.getElementById('pairing-code')`)) throw new Error('pairing form still shown after fragment pairing');
    } catch (error) { record('fragment_pairing_auto', false, String(error.message)); throw error; }
    record('fragment_pairing_auto', true, 'tab opened at origin#pair=<code>; page paired with no typing; saved state loaded; location.hash empty and href free of the code; pairing form absent');
    await shot('01-wide-paired');
    return 'paired from the launcher fragment; saved state loaded';
  });

  // Capture, mouse path, with long untrusted text.
  await step('capture_mouse', async () => {
    await ensureStep('capture');
    await stepChrome('capture');
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
    await stepChrome('priorities');
    await click('#urgency-7'); await click('#importance-8');
    await shot('02b-priorities');
    await accept('priorities', 'priorities-accept');
    return 'urgency 7 and importance 8 set by pointer; saved';
  });

  await step('method_human_edit', async () => {
    await ensureStep('method'); await settle('method');
    await stepChrome('method');
    const memoryText = await read(`document.getElementById('method-memory')?.textContent ?? ''`);
    summary.missing_memory_observation = memoryText.slice(0, 200);
    // A fresh store has no memory: the panel must say so and claim no preference.
    if (!memoryText.includes('Your Glitch remembers') || !memoryText.includes('Memory offline.') || /Usually:/.test(memoryText)) throw new Error('missing-memory line wrong: ' + memoryText.slice(0, 160));
    await click('#method-choice-bounded-plan');
    const cards = await read(`[...document.querySelectorAll('#method-cards .method-card-name')].map(n => n.textContent)`);
    if (cards.join('|') !== 'Full Plan Up Front|Vertical Slicing (Agile)|Fixed Budget, Build what Fits|Experiment First') throw new Error('method cards wrong: ' + cards.join('|'));
    await shot('02c-method');
    await accept('method', 'method-accept');  // no reason needed: "Why this method?" is optional
    return 'a method card chosen with no reason (optional); four cards by their new names; missing memory shown honestly: ' + memoryText.slice(0, 80);
  });

  // "Does it already exist?" by hand: one product row, or the honest "nothing comparable found" route. Discovery cannot be accepted without one.
  const handPriorArtRow = async () => {
    await click('#discovery-add-prior-art');
    await typeInto('#discovery-prior-art-0-name', 'Synthetic Existing Tool');
    await typeInto('#discovery-prior-art-0-link', 'https://example.invalid/synthetic-existing-tool');
    await typeInto('#discovery-prior-art-0-does', 'Does a similar synthetic job.');
    await typeInto('#discovery-prior-art-0-differs', 'Ours is local-first.');
    await typeInto('#discovery-prior-art-0-licence', 'MIT');
  };
  const handPriorArtNone = async () => {
    await click('#discovery-prior-art-none');
    await typeInto('#discovery-prior-art-searched', 'Synthetic search: web and open-source indexes, nothing comparable.');
  };
  const FIXTURE_NONE = {prior_art: [], prior_art_none: true, prior_art_searched: 'Fixture search: web and repositories, nothing comparable.'};

  await step('discovery_hand_fill', async () => {
    await ensureStep('discovery'); await settle('discovery');
    await stepChrome('discovery');
    // With a terminal connected the answers wait for it: locked, with a note, and one door to take the step by hand.
    const lock = await read(`({locked: document.getElementById('discovery-problem').disabled, notes: document.querySelectorAll('#step-body .field-locked .lock-note').length,
      hand: document.getElementById('discovery-hand-fill')?.textContent ?? null})`);
    if (!lock.locked || lock.notes < 5 || lock.hand !== 'Fill this step by hand') throw new Error('Discovery is not locked behind the terminal: ' + JSON.stringify(lock));
    await shot('02d-discovery-locked');
    await click('#discovery-hand-fill');
    await waitFor(`!document.getElementById('discovery-problem').disabled`, 'discovery released by hand');
    await typeInto('#discovery-problem', 'Synthetic problem.');
    await typeInto('#discovery-audience', 'Synthetic audience.');
    await typeInto('#discovery-workaround', 'Synthetic workaround.');
    await typeInto('#discovery-evidence', 'Synthetic evidence.');
    await typeInto('#discovery-kill-criteria', 'Synthetic stop rule.');
    await click('#discovery-add-challenge');
    await typeInto('#discovery-challenge-0-challenge', 'Synthetic challenge.');
    await typeInto('#discovery-challenge-0-response', 'Synthetic response.');
    await handPriorArtRow();
    await read(`(document.getElementById('step-body').scrollTop=0, true)`);
    await shot('02e-discovery-filled');
    await accept('discovery', 'discovery-accept');
    return 'locked while the terminal is connected (inputs disabled, lock notes, hand-fill door); released by hand; five answers, one challenge and one existing product saved';
  });

  await step('exploration_hand_fill', async () => {
    await ensureStep('exploration'); await settle('exploration');
    await stepChrome('exploration');
    const lock = await read(`({locked: document.getElementById('exploration-outcome').disabled, scopeLocked: document.getElementById('exploration-scope-capability').disabled,
      hand: document.getElementById('exploration-hand-fill')?.textContent ?? null})`);
    if (!lock.locked || !lock.scopeLocked || lock.hand !== 'Fill this step by hand') throw new Error('Exploration is not locked behind the terminal: ' + JSON.stringify(lock));
    await shot('02f-exploration-locked');
    await click('#exploration-hand-fill');
    await waitFor(`!document.getElementById('exploration-outcome').disabled`, 'exploration released by hand');
    await typeInto('#exploration-outcome', 'Synthetic desired result.');
    await click('#exploration-scope-capability');
    await typeInto('#exploration-scope-reason', 'Synthetic scope reason.');
    await typeInto('#exploration-alternative-0-route', 'Simpler synthetic route');
    await typeInto('#exploration-alternative-0-reason', 'Smallest first.');
    await typeInto('#exploration-next-slice', 'Synthetic next slice.');
    await click('#exploration-add-sketch');
    await typeInto('#exploration-sketch-0-title', 'Synthetic sketch item');
    await typeInto('#exploration-sketch-0-done-when', 'Synthetic done when.');
    await read(`(document.getElementById('step-body').scrollTop=0, true)`);
    await shot('02g-exploration-filled');
    await accept('exploration', 'exploration-accept');
    return 'locked while the terminal is connected; released by hand; result, scope, one alternative, next slice and one sketch card (title + done-when) saved';
  });

  await step('visualize_skip_optional', async () => {
    await ensureStep('visualize');
    await stepChrome('visualize');
    await click('#visualize-skipped');
    await shot('02h-visualize');
    await waitFor(`['skipped'].includes(document.getElementById('compact-visualize')?.dataset.status)`, 'visualize skipped (one click records it)');
    return 'optional design skipped in one click; status ' + await statusOf('visualize');
  });

  await step('assess_human_edit', async () => {
    await ensureStep('assess'); await settle('assess');
    await stepChrome('assess');
    await click('#assess-method-wsjf');
    await typeInto('#assess-version', 'smoke-1');
    await typeInto('#assess-basis', 'Synthetic evidence.');
    await typeInto('#assess-provenance', 'Synthetic harness.');
    await click('#assess-confidence-medium');
    for (const [key, value] of [['value', '5'], ['time_criticality', '3'], ['enablement', '2'], ['effort', '2']]) await typeInto('#assess-input-' + key, value);
    await typeInto('#assess-proposed-position-input', '1');
    await typeInto('#assess-actual-position', '1');
    await shot('02i-assess');
    await accept('assess', 'assess-accept');
    return 'human-edit path (WSJF, position 1)';
  });

  let promptText = '';
  await step('review_generate_prompt', async () => {
    await ensureStep('review');
    await stepChrome('review');
    await click('#review-generate');
    await waitFor(`!!document.getElementById('review-prompt')`, 'generated prompt');
    promptText = await read(`document.getElementById('review-prompt').value`);
    if (!promptText.startsWith('/glitch-plan')) throw new Error('prompt does not begin with /glitch-plan');
    if (!promptText.includes('## Idea trace')) throw new Error('prompt lacks ## Idea trace');
    const literal = await read(`document.getElementById('review-summary').textContent.includes('<script>window.__pwn=1</script>')&&!document.querySelector('#review-summary script,#review-summary img')`);
    if (!literal) throw new Error('untrusted text not rendered as literal text in review');
    const exists = await read(`document.getElementById('review-summary').textContent`);
    if (!exists.includes('Does it already exist?') || !exists.includes('Synthetic Existing Tool')) throw new Error('Review lacks the "Does it already exist?" row with the product: ' + exists.slice(0, 300));
    await shot('03-wide-review-prompt');
    await read(`(document.querySelector('.panel-body').scrollTop=0, document.getElementById('review-summary').scrollIntoView({block:'start'}), true)`);
    await shot('03b-review-summary');
    return `prompt read-only: starts with /glitch-plan, has ## Idea trace, ${promptText.length} chars; untrusted text literal in summary`;
  });

  await step('saved_check_and_progress', async () => {
    const states = await read(`[...document.querySelectorAll('#compact-nav button')].map(b=>b.dataset.status)`);
    const progress = await read(`document.getElementById('progress').textContent`);
    // Eight rail buttons; each carries its status mark, its number and its name, and the mark sits before the name.
    const strips = await read(`[...document.querySelectorAll('#compact-nav button.g-step')].map(b=>{const g=b.querySelector('.bw-status'),n=b.querySelector('.g-step__name');return {has:!!g&&!!n,before:!!g&&!!n&&g.getBoundingClientRect().left<n.getBoundingClientRect().left}})`);
    const ok = states.filter(s => ['saved', 'skipped'].includes(s)).length;
    if (ok !== 8) throw new Error('not all steps complete: ' + states.join(','));
    if (progress !== '7 of 8 saved, 1 skipped') throw new Error('progress text wrong: ' + progress);
    if (strips.length !== 8 || !strips.every(g => g.has && g.before)) throw new Error('rail buttons lack a status mark before the step name: ' + JSON.stringify(strips));
    return `states ${states.join(',')}; progress "${progress}"; status mark before the name on all 8 rail buttons`;
  });

  await step('reload_persists', async () => {
    await cdp.send('Page.reload');
    await waitFor(`document.getElementById('save-status')?.textContent==='Saved state loaded'`, 'reload loaded');
    const states = await read(`[...document.querySelectorAll('#compact-nav button')].map(b=>b.dataset.status)`);
    const progress = await read(`document.getElementById('progress').textContent`);
    if (states.filter(s => ['saved', 'skipped'].includes(s)).length !== 8) throw new Error('state lost: ' + states.join(','));
    // The saved text itself survives, not only the badges.
    if (progress !== '7 of 8 saved, 1 skipped') throw new Error('progress text wrong after reload: ' + progress);
    await waitFor(`(document.getElementById('review-summary')?.textContent||'').includes('Synthetic smoke idea')`, 'saved capture text after reload');
    return `after reload ${states.join(',')}; "${progress}"; saved capture text read back`;
  });

  await step('wide_layout_eight_columns', async () => {
    const info = await read(`({rail:getComputedStyle(document.getElementById('compact-nav')).display, buttons:document.querySelectorAll('#compact-nav button.g-step').length,
      panels:document.querySelectorAll('#columns #step-panel').length, folded:document.querySelectorAll('#columns .strip').length,
      overflow:document.documentElement.scrollWidth>innerWidth,
      railBox:(r=>[Math.round(r.left),Math.round(r.right),Math.round(r.top)])(document.getElementById('compact-nav').getBoundingClientRect()),
      panelBox:(r=>[Math.round(r.left),Math.round(r.top)])(document.getElementById('step-panel').getBoundingClientRect())})`);
    // The left step rail lists all eight steps beside one open panel: the rail ends before the panel starts and they share a top band.
    const sideBySide = info.railBox[1] <= info.panelBox[0] + 1 && Math.abs(info.railBox[2] - info.panelBox[1]) <= 200;
    if (info.rail === 'none' || info.buttons !== 8 || info.panels !== 1 || info.folded !== 0 || info.overflow || !sideBySide) throw new Error(JSON.stringify(info));
    return `1280px: left rail with ${info.buttons} step buttons beside one open panel; no horizontal scroll`;
  });

  await step('viewport_440_compact_nav', async () => {
    await setViewport(440, 900); await sleep(300);
    const info = await read(`({rail:getComputedStyle(document.getElementById('compact-nav')).display,
      compactButtons:document.querySelectorAll('#compact-nav button.g-step').length,
      overflow:document.documentElement.scrollWidth>innerWidth,
      marks:[...document.querySelectorAll('#compact-nav button.g-step')].every(b=>!!b.querySelector('.bw-status')),
      saved:[...document.querySelectorAll('#compact-nav button')].filter(b=>['saved','skipped'].includes(b.dataset.status)).length,
      apivDisplay:getComputedStyle(document.querySelector('ol.g-apiv')).display,
      apivItems:document.querySelectorAll('ol.g-apiv li').length,
      apivBox:(r=>[r.width,r.height])(document.querySelector('ol.g-apiv').getBoundingClientRect())})`);
    if (info.apivDisplay === 'none' || info.apivItems !== 4) throw new Error('APIV left the accessibility tree at 440px: ' + JSON.stringify(info));
    if (info.rail === 'none' || info.compactButtons !== 8 || info.overflow || !info.marks || info.saved !== 8) throw new Error(JSON.stringify(info));
    await shot('04-440-compact-nav');
    return '440px: step rail shown as a row (8 buttons, each with a status mark), no horizontal scroll';
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
    // The rail names all eight step buttons at every width.
    if (labels.length < 8 || !statuses.length) throw new Error('AX evidence incomplete at ' + width + ': ' + JSON.stringify(ax));
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
    await click('#nav-ideas');
    await openFromBacklog(target.idea_id);
    await waitFor(`document.getElementById('identity').title===${JSON.stringify(target.idea_id)}`, 'handoff idea reopened');
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
    try { await waitFor(`(document.getElementById('agent-status')?.textContent||'').startsWith('Agent disconnected')`, 'disconnected agent status', 20000); }
    catch { throw new Error('page still reads "' + await read(`document.getElementById('agent-status')?.textContent`) + '" 20 s after session-close returned disconnected'); }
    const text = await read(`document.getElementById('agent-status').textContent`);
    // The chip is short; the long sentence stays reachable through its title.
    const longText = await read(`document.getElementById('agent-status').title`);
    if (!/disconnected/i.test(longText) || longText.length <= text.length) throw new Error('agent chip has no long explanation in its title: "' + longText + '"');
    await tabTo('nav-ideas'); await press('Enter');
    await waitFor(`!!document.getElementById('new-idea')&&!document.getElementById('new-idea').disabled`, 'ideas view');
    await click('#new-idea');
    await waitFor(`document.getElementById('identity').textContent==='Not saved yet'`, 'new idea');
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
    await tabTo('nav-ideas'); await press('Enter');
    await waitFor(`!!document.getElementById('new-idea')&&!document.getElementById('new-idea').disabled`, 'ideas view');
    await tabTo('new-idea'); await press('Enter');
    await waitFor(`!!document.getElementById('idea-text')&&document.getElementById('identity').textContent==='Not saved yet'`, 'new idea capture');
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
    await click('#nav-ideas');
    await openFromBacklog(target.idea_id);
    await waitFor(`document.getElementById('identity').title===${JSON.stringify(target.idea_id)}`, 'second idea reopened');
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
  let typedFallbackDone = false;
  // Moved here from the end of the run by J6b. The three writers race a 5 s store lock, each capture takes about 2 s on this host and
  // slows as ideas and drafts accumulate (the unchanged harness already needed 7.3 s for the third writer at 8 ideas), so it runs on a
  // small store, before the agent steps add ideas. It cannot go earlier: save_conflict_stale_revision picks 'the first idea that is not the handoff one'.
  await step('concurrent_cli_writers', async () => {
    const before = idea(['list']).json.ideas.length;
    const files = [0, 1, 2].map(i => { const f = join(tmp, `cw${i}.txt`); writeFileSync(f, 'Synthetic concurrent writer ' + i); return f; });
    const t0 = Date.now(), took = [];
    const results = await Promise.all(files.map(f => ideaAsync(['capture', '--text-file', f, '--actor', 'operator']).then(r => { took.push(Date.now() - t0); return r; })));
    if (!results.every(r => r.status === 0 && r.json?.ok !== false)) throw new Error('a writer failed: ' + redact(results.map(r => r.status)) + ' ' + redact(JSON.stringify(results.map(r => r.json?.error ?? null))) + ' after ms ' + took.join(','));
    const after = idea(['list']).json.ideas.length;
    const doctor = idea(['doctor'], {expectOk: false});
    if (after !== before + 3) throw new Error(`expected ${before + 3} ideas, got ${after}`);
    if (doctor.status !== 0) throw new Error('doctor unhealthy');
    return '3 parallel capture writers all landed (ms ' + took.join(',') + ', store lock timeout 5000); doctor healthy';
  });

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
    try { await waitFor(`!!document.getElementById('pairing-code')||!!document.querySelector('.superseded-warning')||document.getElementById('save-status')?.textContent==='Saved state loaded'`, 'replaced panel, pairing form or saved state', 20000); }
    catch {
      const probe = async expr => { try { return String(await read(expr)).slice(0, 160); } catch (error) { return 'eval error: ' + String(error.message).slice(0, 80); } };
      const origin2 = new URL(resumed.json.origin).origin;
      const ping = await fetch(resumed.json.origin, {signal: AbortSignal.timeout(8000)}).then(r => 'origin answers ' + r.status, e => 'origin error ' + e.name).catch(() => '?');
      throw new Error(`page shows neither a pairing form nor saved state 20 s after resume; origin ${origin2 === new URL(origin).origin ? 'unchanged' : 'changed'}; ${ping}; href ${await probe('location.href')}; ready ${await probe('document.readyState')}; body "${redact(await probe('document.body?.innerText.slice(0, 120)'))}"; console errors ${cdp.events.filter(e => e.method === 'Runtime.exceptionThrown').length}`);
    }
    let repaired = false, replacedPanel = false;
    if (await read(`!!document.querySelector('.superseded-warning')`)) {
      // The resume retired this tab's credential: the reloaded old tab says it was replaced, warns, and offers a fresh copy of the page.
      const panel = await read(`JSON.stringify({text: document.getElementById('connection')?.textContent ?? '', warning: document.querySelector('.superseded-warning')?.textContent ?? null,
        link: [...document.querySelectorAll('#connection a')].map(a => [a.textContent, a.href, a.target]), pairing: !!document.getElementById('pairing-code')})`);
      const panelData = JSON.parse(panel);
      const wantHref = await read(`location.origin + location.pathname`);
      if (!panelData.text.includes('This tab was replaced by a newer one')) throw new Error('replaced panel lacks its sentence: ' + panel.slice(0, 300));
      if (panelData.warning !== 'ONLY CLICK THIS IF YOU LOST THE TAB') throw new Error('replaced panel warning wrong: ' + JSON.stringify(panelData.warning));
      const open = panelData.link.find(l => l[0] === 'Open Glitch idea in a new tab');
      if (!open || open[1] !== wantHref) throw new Error('replaced panel link wrong (want ' + wantHref + '): ' + JSON.stringify(panelData.link));
      if (panelData.pairing) throw new Error('replaced panel also shows a pairing form');
      await shot('05c-superseded-after-resume');
      // Continue as the person would with the link: a fresh load of the plain page, no fragment.
      const leaving2 = cdp.send('Page.navigate', {url: open[1]}).catch(() => {});
      await leaving2;
      await waitFor(`!!document.getElementById('pairing-code')`, 'pairing form after opening the fresh page', 20000);
      replacedPanel = true;
    }
    // A reloaded tab whose credential a resume retired shows the replaced panel, never the pairing form.
    if (!replacedPanel) throw new Error('reloaded old tab after resume did not show the replaced panel');
    if (await read(`!!document.getElementById('pairing-code')`)) {
      await click('#pairing-code'); await typeKeys(resumed.json.pairing_code); await press('Enter'); repaired = true; typedFallbackDone = true;
    }
    await waitFor(`document.getElementById('save-status')?.textContent==='Saved state loaded'`, 'saved state after resume', 20000);
    // Capture and Priorities show no agent line by design, so read it from the Ideas view.
    await click('#nav-ideas');
    try { await waitFor(`document.getElementById('agent-status')?.textContent==='Agent connected'`, 'agent status connected', 20000); }
    catch { throw new Error('agent status reads "' + await read(`document.getElementById('agent-status')?.textContent`) + '" 20 s after resume; save status "' + await read(`document.getElementById('save-status')?.textContent`) + '"'); }
    return `session-open --resume kept the binding and durable session, rotated the pairing credential; ${replacedPanel ? 'reloaded old tab showed the replaced panel (sentence, warning, link to the plain page) and the link opened a fresh page; ' : ''}page ${repaired ? 're-paired with per-character key events' : 'reloaded without re-pairing'}; agent status reads "Agent connected" (${Date.now() - started} ms)${dialog ? '; leave-page prompt accepted' : ''}`;
  });

  record('typed_fallback_pairing', resumeOk && typedFallbackDone, typedFallbackDone ? 'plain origin, no fragment: pairing form shown and the resumed code typed per character; page paired and loaded' : 'resume did not exercise the typed pairing form (no form shown or resume failed)');

  // Agent proposals for Method memory, Discovery, Exploration and Assess on a NEW idea: real browser input, fixed fixture agent.
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
  // A new idea with Capture and Priorities saved, by real input; returns its id.
  const startIdea = async (text, name, urgency, importance) => {
    // Guard: in the wide layout the document itself never scrolls, so the top bar cannot slide off screen.
    for (const width of [1280, 1440]) {
      await setViewport(width, 900); await sleep(150);
      const root = await read(`({top: document.scrollingElement.scrollTop, bar: Math.round(document.querySelector('.g-app').getBoundingClientRect().top), sh: document.documentElement.scrollHeight, ih: innerHeight})`);
      if (root.top !== 0 || root.bar !== 0) throw new Error(`the page scrolls at ${width}px wide: ${JSON.stringify(root)}`);
    }
    await setViewport(1280, 900);
    await click('#nav-ideas');
    try { await waitFor(`!!document.getElementById('new-idea')&&!document.getElementById('new-idea').disabled`, 'ideas view', 6000); }
    catch (error) { throw new Error(error.message + ' ' + await read(`JSON.stringify({newIdea:document.getElementById('new-idea')?.disabled??'absent',save:document.getElementById('save-status')?.textContent,agent:document.getElementById('agent-status')?.textContent,notice:document.getElementById('backlog-notice')?.textContent,tab:[...document.querySelectorAll('.bw-tab')].map(t=>t.id+':'+t.getAttribute('aria-current')+':'+t.disabled).join(),panel:(document.getElementById('step-panel')?.textContent||'').slice(0,120),main:(document.querySelector('.g-main')?.textContent||'').slice(0,160)})`)); }
    await click('#new-idea');
    await waitFor(`document.getElementById('identity').textContent==='Not saved yet'`, 'new idea');
    await typeInto('#idea-text', text);
    await typeInto('#workspace-name', name);
    await typeInto('#workspace-path', workspace);
    await click('#workspace-confirmed');
    await accept('capture', 'capture-accept');
    await ensureStep('priorities');
    await click('#urgency-' + urgency); await click('#importance-' + importance);
    await accept('priorities', 'priorities-accept');
    const id = (await read(`document.getElementById('identity').title`)).match(/idea_[0-9a-f]{32}/)?.[0];
    if (!id) throw new Error('new idea id not shown');
    return id;
  };
  // Prerequisite steps done by hand (the subject of the current step is something else).
  const handMethod = async choice => {
    await ensureStep('method'); await settle('method');
    await click('#method-choice-' + choice);
    await accept('method', 'method-accept');
  };
  const handDiscovery = async () => {
    await ensureStep('discovery'); await settle('discovery');
    await click('#discovery-hand-fill');
    await waitFor(`!document.getElementById('discovery-problem').disabled`, 'discovery released by hand');
    await typeInto('#discovery-problem', 'Synthetic problem.'); await typeInto('#discovery-audience', 'Synthetic audience.');
    await typeInto('#discovery-workaround', 'Synthetic workaround.'); await typeInto('#discovery-evidence', 'Synthetic evidence.');
    await typeInto('#discovery-kill-criteria', 'Synthetic stop rule.');
    await click('#discovery-add-challenge');
    await typeInto('#discovery-challenge-0-challenge', 'Synthetic challenge.'); await typeInto('#discovery-challenge-0-response', 'Synthetic response.');
    await handPriorArtNone();
    await accept('discovery', 'discovery-accept');
  };
  // The next request for one operation, read from the fixture agent's event stream.
  const nextEvent = async (ideaId, operation) => {
    for (let attempt = 0; attempt < 6; attempt++) {
      const batch = idea(['events', '--session', agentSession, '--after', String(cursor.n), '--timeout', '5', '--runtime-root', runtimeRoot]).json;
      cursor.n = batch.sequence;
      const event = batch.events.find(item => item.idea_id === ideaId && item.operation === operation);
      if (event) return event;
    }
    throw new Error('the automatic ' + operation + ' request never reached the agent');
  };
  const exploreFields = (tag, scope = 'small-change') => ({outcome: tag + ' desired result.', scope, scope_reason: tag + ' scope reason.',
    alternatives: [{route: tag + ' route', reason: tag + ' reason.'}], assumptions: [tag + ' risk.'], next_slice: tag + ' next slice.',
    learning: [tag + ' learning.'], investment: null, experiment: null,
    sketch: [{title: tag + ' sketch item', why_next: tag + ' why next.', done_when: tag + ' done when.', method: null}]});
  if (resumeOk) {
    await step('agent_proposals_new_idea_setup', async () => {
      proposalIdea = await startIdea('Synthetic idea for the fixture agent proposals.', 'agent-workspace', 6, 6);
      return 'new idea captured and both priorities set by pointer';
    });
  }
  if (resumeOk && proposalIdea) {
    await step('agent_proposal_method', async () => {
      const edited = 'Human method reason.';
      const {log, waiting, shown} = await requestAndUse('method', () => serveAgent(agentSession, proposalIdea, 'method', cursor, () => ({
        memory: {status: 'found', sources: ['note:fixture-note'], rationale: 'Fixture rationale.', preferred_method: 'bounded-plan'},
      })), 'Usually: Full Plan Up Front.', async () => {
        // The human chooses a method different from the remembered one; using the memory result
        // must never change that choice (the agent sends memory only, only the human selects).
        await click('#method-choice-adaptive-slices');
        const before = await read(`document.querySelector('[id^="method-choice-"][aria-pressed="true"]')?.id ?? null`);
        if (before !== 'method-choice-adaptive-slices') throw new Error('the human method choice did not register before Use: ' + before);
        await shot('09-method-selected');
      });
      const choiceAfterUse = await read(`document.querySelector('[id^="method-choice-"][aria-pressed="true"]')?.id ?? null`);
      if (choiceAfterUse !== 'method-choice-adaptive-slices') throw new Error('using the memory result changed the human method choice to ' + choiceAfterUse);
      const line = await read(`document.querySelector('#method-memory p')?.textContent ?? ''`);
      if (line !== 'Usually: Full Plan Up Front.') throw new Error('memory line did not take the agent memory result: ' + line);
      await typeInto('#method-reason', edited);
      await accept('method', 'method-accept');
      const fields = savedOf().workflow?.steps?.method?.fields ?? {};
      if (fields.selection !== 'adaptive-slices') throw new Error('accepted method is not the human choice: ' + fields.selection);
      if (fields.memory?.status !== 'found' || fields.memory?.preferred_method !== 'bounded-plan') throw new Error('the memory result was not accepted: ' + JSON.stringify(fields.memory));
      if (acceptedAt('method', edited) !== 'workflow.steps.method.fields.reason') throw new Error('reason accepted in the wrong field');
      return agentObs(log, `waiting panel "${waiting.slice(0, 50)}"; memory shown "${shown.slice(0, 60)}"; human choice adaptive-slices kept over the remembered bounded-plan; memory found saved beside it`);
    });
    await step('agent_proposal_discovery', async () => {
      const raw = 'Fixture suggested problem.', edited = 'Human edited problem.';
      const {log, waiting, shown} = await requestAndUse('discovery', () => serveAgent(agentSession, proposalIdea, 'discovery', cursor, () => ({
        problem: raw, audience: 'Fixture audience.', workaround: 'Fixture workaround.', evidence: 'Fixture evidence.', kill_criteria: 'Fixture stop rule.',
        challenges: [{challenge: 'Fixture challenge.', response: 'Fixture response.'}], ...FIXTURE_NONE,
      })), raw);
      const filled = await read(`document.getElementById('discovery-problem').value`);
      if (filled !== raw) throw new Error('use did not copy the suggestion into the draft: ' + filled.slice(0, 80));
      await replaceText('#discovery-problem', edited);
      await accept('discovery', 'discovery-accept');
      const at = acceptedAt('discovery', edited);
      if (at !== 'workflow.steps.discovery.fields.problem') throw new Error('edited value accepted in the wrong Discovery field: ' + at);
      return agentObs(log, `waiting panel "${waiting.slice(0, 50)}"; suggestion shown "${shown.slice(0, 60)}"; field edited: problem; accepted at ${at}`);
    });
    await step('agent_proposal_exploration', async () => {
      const fields = exploreFields('Fixture'), edited = 'Human edited desired result.';
      const {log, waiting, shown} = await requestAndUse('exploration', () => serveAgent(agentSession, proposalIdea, 'exploration', cursor, () => fields), fields.outcome);
      const filled = await read(`document.getElementById('exploration-outcome').value`);
      if (filled !== fields.outcome) throw new Error('use did not copy the suggestion into the draft: ' + filled.slice(0, 80));
      await replaceText('#exploration-outcome', edited);
      await accept('exploration', 'exploration-accept');
      const at = acceptedAt('exploration', edited);
      if (at !== 'workflow.steps.exploration.fields.outcome') throw new Error('edited value accepted in the wrong Exploration field: ' + at);
      const sketch = savedOf().workflow?.steps?.exploration?.fields?.sketch ?? [];
      if (sketch.length !== 1 || sketch[0].done_when !== 'Fixture done when.') throw new Error('sketch card was not accepted: ' + JSON.stringify(sketch).slice(0, 120));
      await shot('08-agent-exploration-saved');
      return agentObs(log, `waiting panel "${waiting.slice(0, 50)}"; suggestion shown "${shown.slice(0, 60)}"; field edited: desired result; accepted at ${at}; one sketch card kept`);
    });
    await step('agent_proposal_assess', async () => {
      const edited = 'Human edited assessment basis.';
      // Assess unlocks after the optional design step is decided; skip it by hand (one click, no reason needed).
      await ensureStep('visualize');
      await click('#visualize-skipped');
      await waitFor(`['skipped'].includes(document.getElementById('compact-visualize')?.dataset.status)`, 'visualize skipped (one click records it)');
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

  // The terminal conversation starts by itself on reaching Discovery and Exploration; the terminal writes
  // each agreed field to the page with the native `fill` verb; the human still decides and presses Accept.
  let talkIdea = null;
  if (resumeOk && proposalIdea) {
    await step('agent_conversation_fills_discovery', async () => {
      talkIdea = await startIdea('Synthetic idea for the terminal conversation fills.', 'talk-workspace', 5, 5);
      proposalIdea = talkIdea;
      await handMethod('bounded-plan');
      await ensureStep('discovery');
      await waitFor(`!!document.getElementById('discovery-proposal-status')||/Your terminal is/.test(document.getElementById('discovery-conversation-status')?.textContent??'')`, 'discovery conversation started', 20000);
      const event = await nextEvent(talkIdea, 'discovery');
      const first = 'Terminal agreed problem.', challenge = 'Terminal agreed challenge.', answer = 'Terminal agreed response.';
      const seq1 = agentFill(agentSession, event, {problem: first}, 1);
      await waitFor(`document.getElementById('discovery-problem')?.value===${JSON.stringify(first)}`, 'first fill shown in the problem field', 20000);
      const seq2 = agentFill(agentSession, event, {audience: 'Terminal audience.', workaround: 'Terminal workaround.', evidence: 'Terminal evidence.',
        kill_criteria: 'Terminal stop rule.', challenges: [{challenge, response: answer}], ...FIXTURE_NONE}, 2);
      await waitFor(`document.getElementById('discovery-challenge-0-challenge')?.value===${JSON.stringify(challenge)}&&document.getElementById('discovery-kill-criteria')?.value==='Terminal stop rule.'`, 'second fill shown', 20000);
      const notes = await read(`JSON.stringify(['discovery-problem-filled','discovery-audience-filled','discovery-workaround-filled','discovery-evidence-filled','discovery-kill-criteria-filled','discovery-challenges-filled'].map(id => document.getElementById(id)?.textContent ?? null))`);
      if (JSON.parse(notes).some(text => text !== 'From your terminal conversation')) throw new Error('filled notes missing: ' + notes);
      await shot('10b-agent-conversation-fills-discovery');
      await accept('discovery', 'discovery-accept');
      const at = acceptedAt('discovery', first);
      if (at !== 'workflow.steps.discovery.fields.problem') throw new Error('the filled problem was not accepted as the Discovery problem: ' + at);
      return `agent got the automatic discovery event; fill ${seq1} {problem} and fill ${seq2} {audience, workaround, evidence, kill_criteria, challenges} appeared with the note "From your terminal conversation" and unlocked each field; the human pressed Accept; accepted at ${at}`;
    });
  }
  if (resumeOk && proposalIdea && talkIdea) {
    await step('agent_conversation_fills_exploration', async () => {
      await ensureStep('exploration');
      await waitFor(`!!document.getElementById('exploration-proposal-status')||/Your terminal is/.test(document.getElementById('exploration-conversation-status')?.textContent??'')`, 'exploration conversation started', 20000);
      const event = await nextEvent(talkIdea, 'exploration');
      const first = 'Terminal agreed desired result.', route = 'Terminal agreed route', why = 'Terminal agreed reason.', risk = 'Terminal agreed risk.';
      const sketch = [{title: 'Terminal sketch item', why_next: 'Terminal why next.', done_when: 'Terminal done when.', method: null}];
      const seq1 = agentFill(agentSession, event, {outcome: first}, 1);
      await waitFor(`document.getElementById('exploration-outcome')?.value===${JSON.stringify(first)}`, 'first fill shown in the outcome field', 20000);
      const seq2 = agentFill(agentSession, event, {alternatives: [{route, reason: why}], assumptions: [risk], sketch}, 2);
      await waitFor(`document.getElementById('exploration-alternative-0-route')?.value===${JSON.stringify(route)}&&document.getElementById('exploration-assumptions-0')?.value===${JSON.stringify(risk)}&&document.getElementById('exploration-sketch-0-title')?.value==='Terminal sketch item'`, 'second fill shown in alternatives, assumptions and sketch', 20000);
      const notes = await read(`JSON.stringify(['exploration-outcome-filled','exploration-alternatives-filled','exploration-assumptions-filled','exploration-sketch-filled'].map(id => document.getElementById(id)?.textContent ?? null))`);
      if (JSON.parse(notes).some(text => text !== 'From your terminal conversation')) throw new Error('filled notes missing: ' + notes);
      await shot('10d-agent-conversation-fills-exploration');
      // Scope, its reason and the next slice are still waiting for the terminal: the human takes the rest by hand.
      if (!await read(`document.getElementById('exploration-scope-capability').disabled`)) throw new Error('scope should wait for the terminal until the step is released');
      await click('#exploration-hand-fill');
      if (await read(`document.getElementById('exploration-outcome').value`) !== first) throw new Error('taking the step by hand dropped the terminal fill');
      await click('#exploration-scope-small-change');
      await typeInto('#exploration-scope-reason', 'My own scope reason.');
      await typeInto('#exploration-next-slice', 'My own next slice.');
      await accept('exploration', 'exploration-accept');
      const at = acceptedAt('exploration', first);
      if (at !== 'workflow.steps.exploration.fields.outcome') throw new Error('the filled outcome was not accepted as the Exploration outcome: ' + at);
      return `agent got the automatic exploration event; fill ${seq1} {outcome} and fill ${seq2} {alternatives, assumptions, sketch} appeared with the note "From your terminal conversation"; scope stayed locked until the human took the step by hand (terminal fills kept); the human chose scope and typed the rest, pressed Accept; accepted at ${at}`;
    });
  }

  // Quiet Capture and Priorities, memory-only Method, and a plain reason on every disabled Accept: all with the agent connected.
  const agentLineNow = () => read(`(()=>{const e=document.getElementById('agent-status');return {hidden: !e||e.hidden||getComputedStyle(e).display==='none', text: (e?.textContent??'').trim()}})()`);
  const acceptReasonOf = key => read(`(()=>{const a=document.getElementById(${JSON.stringify(key + '-accept')});const r=document.getElementById(${JSON.stringify(key + '-accept-reason')});
    return {present: !!a, disabled: a?.disabled ?? null, reason: (r?.textContent ?? '').trim(), describedby: a?.getAttribute('aria-describedby') ?? null, title: a?.title ?? ''}})()`);
  // A disabled Accept must carry a plain-words reason (the step's accept-reason line, aria-describedby and title agree); returns the verdict word.
  const reasonVerdict = async key => {
    const info = await acceptReasonOf(key);
    if (!info.present) return 'absent';
    if (!info.disabled) return 'enabled';
    if (info.reason.length < 8 || /[_{}<>]|undefined|null/.test(info.reason) || info.describedby !== key + '-accept-reason' || info.title !== info.reason)
      throw new Error(key + ': disabled Accept has no plain reason: ' + JSON.stringify(info));
    return 'disabled+reason';
  };
  if (resumeOk && proposalIdea && talkIdea) {
    await step('quiet_capture_priorities', async () => {
      await click('#nav-ideas');
      await waitFor(`document.getElementById('agent-status')?.textContent==='Agent connected'`, 'agent connected on the Ideas view', 20000);
      await openFromBacklog(talkIdea);
      await waitFor(`document.getElementById('identity').title===${JSON.stringify(talkIdea)}`, 'talk idea reopened');
      const seen = [];
      for (const key of ['capture', 'priorities']) {
        await ensureStep(key); await sleep(300);
        const line = await agentLineNow();
        if (line.text) throw new Error(key + ' shows agent chatter: ' + line.text.slice(0, 80));
        if (!line.hidden) throw new Error(key + ' agent-status is not hidden');
        seen.push(key + ' hidden=' + line.hidden);
      }
      return 'agent connected (Ideas view reads "Agent connected"); #agent-status has no text and is hidden on ' + seen.join(', ');
    });
    await step('method_memory_only', async () => {
      const id = await startIdea('Synthetic idea for the memory-only Method check.', 'memory-workspace', 5, 5);
      const selected = () => read(`[...document.querySelectorAll('[id^="method-choice-"][aria-pressed="true"]')].map(n => n.id)`);
      const {log} = await requestAndUse('method', () => serveAgent(agentSession, id, 'method', cursor, () => ({
        memory: {status: 'found', sources: ['note:fixture-note'], rationale: 'Fixture rationale.', preferred_method: 'bounded-plan'},
      })), 'Usually: Full Plan Up Front.', async () => {
        const before = await selected();
        if (before.length) throw new Error('the agent reply selected a method by itself: ' + before.join(','));
      });
      const after = await selected();
      if (after.length) throw new Error('using the memory result selected a method: ' + after.join(','));
      const line = await read(`document.querySelector('#method-memory p')?.textContent ?? ''`);
      if (line !== 'Usually: Full Plan Up Front.') throw new Error('memory content not shown: ' + line);
      const saved = idea(['show', id]).json.idea.workflow?.steps?.method?.fields?.selection ?? null;
      if (saved) throw new Error('a method selection exists in saved state without the human: ' + saved);
      // The human then decides (leaves the idea clean): the choice is theirs, memory saved beside it.
      await click('#method-choice-adaptive-slices'); await accept('method', 'method-accept');
      const mine = idea(['show', id]).json.idea.workflow?.steps?.method?.fields ?? {};
      if (mine.selection !== 'adaptive-slices' || mine.memory?.preferred_method !== 'bounded-plan') throw new Error('accepted method is not the human choice with memory beside it: ' + JSON.stringify(mine).slice(0, 160));
      return agentObs(log, 'agent memory (remembered bounded-plan) shown as "' + line + '"; no method selected before or after Use, none saved; the selection stays the human\'s');
    });
  }


  // J6b: the terminal fills Discovery and Exploration through the native `fill` verb, the human releases a step by hand, and the
  // no-terminal wording. Placed after method_memory_only and before the agent drop (the drop closes the agent session, and
  // no_agent_step_unlocked needs it closed, so that step runs after accept_reason_every_step). Each step cuts its own idea.
  const readFields = (id, key) => idea(['show', id]).json.idea.workflow?.steps?.[key]?.fields ?? {};
  const fillRaw = (event, fields, n) => {
    const file = join(tmp, 'fillraw-' + event.operation + '-' + n + '.json');
    writeFileSync(file, JSON.stringify({request_id: event.request_id, session_id: event.session_id, idea_id: event.idea_id,
      accepted_revision: event.accepted_revision, draft_version: event.draft_version, operation: event.operation,
      source_digest: event.source_digest, fields}));
    const result = idea(['fill', '--session', agentSession, '--request', event.request_id, '--payload', file, '--runtime-root', runtimeRoot], {expectOk: false});
    return {status: result.status, code: result.json?.error?.code ?? result.json?.code ?? null, json: result.json};
  };
  if (resumeOk && proposalIdea) {
    let fillIdea = null;  // one idea for steps 1 and 2: every extra idea slows the store writers that concurrent_cli_writers races (5 s lock)
    await step('discovery_fills_and_challenge', async () => {
      const id = fillIdea = await startIdea('Synthetic idea for the Discovery fill check.', 'discfill-workspace', 5, 5);
      await handMethod('bounded-plan');
      await ensureStep('discovery');
      await waitFor(`!!document.getElementById('discovery-proposal-status')||/Your terminal is/.test(document.getElementById('discovery-conversation-status')?.textContent??'')`, 'discovery conversation started', 20000);
      const ids = ['discovery-problem', 'discovery-audience', 'discovery-workaround', 'discovery-evidence', 'discovery-kill-criteria'];
      // Starts locked: every text waits for the terminal, and there is exactly one door to the hand path.
      const lock = await read(`({disabled: ${JSON.stringify(ids)}.map(i => document.getElementById(i)?.disabled),
        notes: [...document.querySelectorAll('#step-body .field-locked .lock-note')].map(n => n.textContent),
        hands: [...document.querySelectorAll('#step-body button')].filter(b => b.textContent === 'Fill this step by hand').length,
        acceptDisabled: document.getElementById('discovery-accept')?.disabled})`);
      if (lock.disabled.some(d => d !== true) || lock.notes.length < 5 || lock.notes.some(t => t !== 'Waiting for your terminal') || lock.hands !== 1 || lock.acceptDisabled !== true)
        throw new Error('Discovery does not start locked behind the terminal: ' + JSON.stringify(lock));
      const event = await nextEvent(id, 'discovery');
      const want = {problem: 'J6b agreed problem.', audience: 'J6b agreed audience.', workaround: 'J6b agreed workaround.', evidence: 'J6b agreed evidence.',
        kill_criteria: 'J6b agreed stop rule.', challenges: [{challenge: 'J6b first challenge.', response: 'J6b first response.'}, {challenge: 'J6b second challenge.', response: 'J6b second response.'}], ...FIXTURE_NONE};
      const seq = agentFill(agentSession, event, want, 1);
      await waitFor(`${JSON.stringify(ids)}.every((i, k) => document.getElementById(i)?.value === ${JSON.stringify([want.problem, want.audience, want.workaround, want.evidence, want.kill_criteria])}[k])`, 'five texts shown', 20000);
      await waitFor(`document.getElementById('discovery-challenge-1-response')?.value===${JSON.stringify(want.challenges[1].response)}`, 'challenges shown with responses', 20000);
      const shown = await read(`[0, 1].map(i => [document.getElementById('discovery-challenge-' + i + '-challenge')?.value, document.getElementById('discovery-challenge-' + i + '-response')?.value])`);
      if (JSON.stringify(shown) !== JSON.stringify(want.challenges.map(c => [c.challenge, c.response]))) throw new Error('challenge cards wrong: ' + JSON.stringify(shown));
      await shot('10f-discovery-fills-and-challenge');
      await accept('discovery', 'discovery-accept');  // waits for Accept to be enabled, then the human presses it
      const saved = readFields(id, 'discovery');
      for (const k of ['problem', 'audience', 'workaround', 'evidence', 'kill_criteria']) if (saved[k] !== want[k]) throw new Error('saved ' + k + ' is ' + JSON.stringify(saved[k]));
      if (JSON.stringify(saved.challenges) !== JSON.stringify(want.challenges)) throw new Error('saved challenges wrong: ' + JSON.stringify(saved.challenges));
      if (saved.prior_art_none !== true || saved.prior_art_searched !== FIXTURE_NONE.prior_art_searched || (saved.prior_art ?? []).length) throw new Error('saved prior-art none route wrong: ' + JSON.stringify([saved.prior_art, saved.prior_art_none, saved.prior_art_searched]));
      return `locked at the start (5 inputs disabled, 5 "Waiting for your terminal" notes, one hand-fill button, Accept disabled); fill ${seq} of five texts and two {challenge, response} pairs shown on the page; Accept enabled, human accepted; saved state holds all five texts and both challenges with responses`;
    });
    await step('exploration_fills_and_sketch', async () => {
      if (!fillIdea) throw new Error('needs the idea left by discovery_fills_and_challenge (Discovery accepted)');
      const id = fillIdea;
      await ensureStep('exploration');
      await waitFor(`!!document.getElementById('exploration-proposal-status')||/Your terminal is/.test(document.getElementById('exploration-conversation-status')?.textContent??'')`, 'exploration conversation started', 20000);
      const event = await nextEvent(id, 'exploration');
      const accDisabled = () => read(`document.getElementById('exploration-accept')?.disabled`);
      const val = i => read(`document.getElementById(${JSON.stringify(i)})?.value`);
      const sketch = [1, 2, 3].map(n => ({title: 'J6b sketch ' + n, why_next: 'J6b why ' + n + '.', done_when: 'J6b done when ' + n + '.', method: null}));
      agentFill(agentSession, event, {outcome: 'J6b desired result.'}, 1);
      await waitFor(`document.getElementById('exploration-outcome')?.value==='J6b desired result.'`, 'outcome shown', 20000);
      if (!await accDisabled()) throw new Error('Accept enabled after only the first field was filled');
      agentFill(agentSession, event, {scope: 'capability', scope_reason: 'J6b scope reason.'}, 2);
      await waitFor(`document.getElementById('exploration-scope-reason')?.value==='J6b scope reason.'&&document.getElementById('exploration-scope-capability')?.getAttribute('aria-pressed')==='true'`, 'scope and reason shown', 20000);
      agentFill(agentSession, event, {alternatives: [{route: 'J6b route', reason: 'J6b route reason.'}], assumptions: [], learning: []}, 3);
      await waitFor(`document.getElementById('exploration-alternative-0-route')?.value==='J6b route'`, 'alternative shown', 20000);
      agentFill(agentSession, event, {next_slice: 'J6b next slice.', sketch}, 4);
      await waitFor(`document.querySelectorAll('#exploration-sketch .sketch-card').length===3`, 'three sketch cards shown', 20000);
      const cards = await read(`[...document.querySelectorAll('#exploration-sketch .sketch-card')].map(c => ({n: c.querySelector('.sketch-num')?.textContent, title: c.querySelector('[id$="-title"]')?.value, done: c.querySelector('[id$="-done-when"]')?.value}))`);
      if (JSON.stringify(cards) !== JSON.stringify(sketch.map((s, i) => ({n: String(i + 1), title: s.title, done: s.done_when})))) throw new Error('sketch cards wrong: ' + JSON.stringify(cards));
      await shot('10g-exploration-fills-and-sketch');
      await accept('exploration', 'exploration-accept');
      const saved = readFields(id, 'exploration');
      if (saved.outcome !== 'J6b desired result.' || saved.scope !== 'capability' || saved.scope_reason !== 'J6b scope reason.' || saved.next_slice !== 'J6b next slice.')
        throw new Error('saved scalars wrong: ' + JSON.stringify(saved).slice(0, 300));
      if (JSON.stringify(saved.assumptions) !== '[]' || JSON.stringify(saved.learning) !== '[]') throw new Error('empty lists did not save as []: ' + JSON.stringify([saved.assumptions, saved.learning]));
      if (JSON.stringify(saved.sketch?.map(s => [s.title, s.done_when])) !== JSON.stringify(sketch.map(s => [s.title, s.done_when]))) throw new Error('saved sketch wrong: ' + JSON.stringify(saved.sketch));
      return 'agent filled outcome, scope+reason, alternatives, empty assumptions/learning, next slice and a 3-item sketch in four fills (Accept stayed disabled after the first); page showed numbered cards 1-3 each with title and done-when; Accept worked; saved state has the values, assumptions [] and learning [], and the sketch';
    });
    // J6c: Prototype Here, the unavailable pointer, then the documented agent order with real files. Reuses fillIdea (Exploration accepted, agent connected): no new idea.
    const POINTER = "Prototype Here uses Matt Pocock's prototype skill. Get it from https://github.com/mattpocock/skills, install it, then refresh this page.";  // web/steps/visualize.js
    const SKILLS = 'https://github.com/mattpocock/skills';
    const visualizeOf = id => readFields(id, 'visualize');
    await step('prototype_unavailable_pointer', async () => {
      if (!fillIdea) throw new Error('needs the idea left by discovery_fills_and_challenge (Exploration accepted)');
      const id = fillIdea;
      await ensureStep('visualize');
      await waitFor(`!!document.getElementById('visualize-prototype')&&!document.getElementById('visualize-prototype').disabled`, 'Prototype Here enabled (agent connected, brief ready)', 20000);
      await click('#visualize-prototype');
      const event = await nextEvent(id, 'visual_brief');
      const sentReply = agentReply(agentSession, event, {prototype_skill: 'unavailable'});
      await waitFor(`!!document.getElementById('visualize-prototype-skill')`, 'install pointer shown', 20000);
      const shown = await read(`({text: document.getElementById('visualize-prototype-skill').textContent, href: document.querySelector('#visualize-prototype-skill a')?.href ?? null,
        shot: !!document.getElementById('visualize-prototype-shot'), ready: /Prototype ready/.test(document.body.innerText),
        skip: document.getElementById('visualize-skipped')?.disabled, copy: document.getElementById('visualize-copy')?.disabled,
        again: document.getElementById('visualize-prototype')?.disabled, status: document.getElementById('compact-visualize')?.dataset.status})`);
      if (shown.text !== POINTER) throw new Error('pointer sentence differs: ' + JSON.stringify(shown.text));
      if (shown.href !== SKILLS) throw new Error('pointer link is not ' + SKILLS + ': ' + shown.href);
      if (shown.shot || shown.ready) throw new Error('a prototype is shown although the skill is unavailable: ' + JSON.stringify(shown));
      if (shown.status === 'saved') throw new Error('Visualize is saved after an unavailable reply');
      if (shown.skip !== false || shown.copy !== false) throw new Error('Claude Design or Skip is not usable: ' + JSON.stringify(shown));
      const saved = visualizeOf(id);
      if (saved.disposition === 'accepted_set' || saved.source === 'prototype' || (saved.assets ?? []).length) throw new Error('a prototype was recorded: ' + JSON.stringify(saved));
      await shot('12a-prototype-unavailable-pointer');
      return `visual_brief request received, replied prototype_skill "unavailable" with no fills (ok ${sentReply}); page shows "${shown.text}" linking ${shown.href}; no screenshot, no "Prototype ready"; Visualize not saved, nothing recorded; Copy brief and Skip visualization enabled (Prototype Here ${shown.again ? 'disabled' : 'enabled'} again)`;
    });
    await step('prototype_fixture_import', async () => {
      if (!fillIdea) throw new Error('needs the idea left by discovery_fills_and_challenge');
      const id = fillIdea;
      await waitFor(`!!document.getElementById('visualize-prototype')&&!document.getElementById('visualize-prototype').disabled`, 'Prototype Here enabled for a second request', 20000);
      await click('#visualize-prototype');
      const event = await nextEvent(id, 'visual_brief');
      const png = join(tmp, 'proto-shot.png'), png2 = join(tmp, 'proto-shot-two.png'), zip = join(tmp, 'proto-bundle.zip');
      writeFileSync(png, makePng(200)); writeFileSync(png2, makePng(90)); writeFileSync(zip, makeZip('index.html', '<!doctype html><title>fixture</title>'));
      const upload = (file, expectOk = true) => idea(['asset', '--session', agentSession, '--idea', id, '--revision', String(event.accepted_revision), '--request', event.request_id, '--file', file, '--runtime-root', runtimeRoot], {expectOk});
      const pngUp = upload(png).json, zipUp = upload(zip).json;
      if (!pngUp?.asset_id || !zipUp?.asset_id) throw new Error('asset replies carry no asset_id: ' + redact(JSON.stringify([pngUp, zipUp])).slice(0, 200));
      const conflict = upload(png2, false);
      if (conflict.json?.ok !== false || conflict.json?.error?.code !== 'request_conflict') throw new Error('a second png under the same request was not refused request_conflict: ' + redact(JSON.stringify(conflict.json)).slice(0, 200));
      const seq = agentFill(agentSession, event, {source: 'prototype', assets: [zipUp.asset_id, pngUp.asset_id]}, 1);
      // Still open: the page already shows the screenshot and an enabled Accept, before any reply.
      try { await waitFor(`(()=>{const i=document.getElementById('visualize-prototype-shot');return !!i&&i.complete&&i.naturalWidth>0})()`, 'screenshot rendered (naturalWidth > 0)', 20000); }
      catch (error) { throw new Error(error.message + '; prototype card: ' + redact(await read(`document.getElementById('visualize-card-prototype').innerText.slice(0, 400)`)) + '; img ' + await read(`(()=>{const i=document.getElementById('visualize-prototype-shot');return i?JSON.stringify({complete:i.complete,w:i.naturalWidth,src:i.src.slice(0,30)}):'absent'})()`) + '; upload card: ' + redact(await read(`document.getElementById('visualize-card-claude').innerText.slice(0, 500)`))); }
      await waitFor(`!!document.getElementById('visualize-accept')&&!document.getElementById('visualize-accept').disabled`, 'Accept enabled after the fill', 20000);
      const open = await read(`({w: document.getElementById('visualize-prototype-shot').naturalWidth, h: document.getElementById('visualize-prototype-shot').naturalHeight,
        alt: document.getElementById('visualize-prototype-shot').alt, ready: [...document.querySelectorAll('#visualize-card-prototype p')].map(p => p.textContent).find(t => /^Prototype ready/.test(t)) ?? null,
        pointer: !!document.getElementById('visualize-prototype-skill'), status: document.getElementById('compact-visualize')?.dataset.status})`);
      if (!open.ready || !open.ready.includes('proto-bundle.zip') || !open.ready.includes('proto-shot.png')) throw new Error('"Prototype ready" line missing or wrong: ' + JSON.stringify(open));
      if (open.pointer) throw new Error('the install pointer is still shown beside a ready prototype');
      if (open.status === 'saved') throw new Error('Visualize saved before the human accepted');
      await shot('12b-prototype-fixture-import');
      let reply;
      try { reply = agentReply(agentSession, event, {prototype_skill: 'available'}); }  // the "Prototype ready" handshake, last
      catch (error) { throw new Error(error.message.replace(/\s+/g, ' ') + ' | event draft_version ' + event.draft_version + ', accepted_revision ' + event.accepted_revision + ', stored workflow draft_version ' + idea(['show', id]).json.idea.workflow?.draft_version + ', idea revision ' + idea(['show', id]).json.idea.revision); }
      const late = fillRaw(event, {source: 'prototype', assets: [zipUp.asset_id, pngUp.asset_id]}, 9);
      if (late.json?.ok !== false || late.code !== 'request_closed') throw new Error('a fill after the reply was not refused request_closed: ' + late.code + ' ' + redact(JSON.stringify(late.json)).slice(0, 160));
      await waitFor(`!!document.getElementById('visualize-prototype-shot')&&!document.getElementById('visualize-accept').disabled`, 'prototype and Accept still shown after the reply', 20000);
      await accept('visualize', 'visualize-accept');
      const saved = visualizeOf(id), everything = JSON.stringify(idea(['show', id]).json.idea);
      if (saved.disposition !== 'accepted_set' || saved.source !== 'prototype') throw new Error('saved Visualize is not accepted_set/prototype: ' + JSON.stringify(saved));
      const found = [zipUp.asset_id, pngUp.asset_id].filter(a => everything.includes(a));
      if (found.length !== 2) throw new Error('saved state does not name both assets (' + found.length + ' of 2): ' + JSON.stringify(saved).slice(0, 300));
      return `agent got visual_brief ${event.request_id}; asset png ${pngUp.asset_id} and zip ${zipUp.asset_id} uploaded under it (a second, different png refused ${conflict.json.error.code}); fill ${seq} {source: prototype, assets: [zip, png]} while open; page showed "${open.ready}" and the screenshot (${open.w}x${open.h}, naturalWidth > 0) with Accept enabled before any reply; then reply prototype_skill "available" (ok ${reply}); a fill after it refused ${late.code}; human accepted; saved disposition ${saved.disposition}, source ${saved.source}, both asset ids present in the saved idea`;
    });
    await step('old_idea_refused', async () => {
      if (!fillIdea) throw new Error('needs the idea left by discovery_fills_and_challenge');
      const id = fillIdea, store = join(tmp, 'store'), historyDir = join(store, 'history', id);
      const files = [join(store, id + '.md'), ...readdirSync(historyDir).filter(n => n.endsWith('.md')).map(n => join(historyDir, n))];
      const sha = f => createHash('sha256').update(readFileSync(f)).digest('hex');
      const original = new Map(files.map(f => [f, readFileSync(f)]));
      let old = null;
      try {
        // A v0.2-format idea: the same files with workflow schema_version 2, which is how v0.2 stored them. Only this one idea is touched.
        for (const [f, bytes] of original) writeFileSync(f, bytes.toString('utf8').replace(/schema_version: 3\b/g, 'schema_version: 2'));
        if (files.every(f => sha(f) === createHash('sha256').update(original.get(f)).digest('hex'))) throw new Error('the fixture edit changed no file');
        old = new Map(files.map(f => [f, sha(f)]));
        const refusals = ['list', 'show'].map(verb => { const r = idea(verb === 'list' ? ['list'] : ['show', id], {expectOk: false}); return {verb, ok: r.json?.ok, code: r.json?.error?.code, message: r.json?.error?.message}; });
        for (const r of refusals) if (r.ok !== false || r.code !== 'unsupported_idea_version') throw new Error('CLI ' + r.verb + ' did not refuse with unsupported_idea_version: ' + JSON.stringify(r));
        const MESSAGE = 'This idea was made with an older glitch-idea. Capture it again.';  // idea_workflow.py UNSUPPORTED_VERSION_MESSAGE
        if (refusals.some(r => r.message !== MESSAGE)) throw new Error('CLI message differs: ' + JSON.stringify(refusals));
        await read(`(window.onbeforeunload=null, true)`);
        await cdp.send('Page.reload');
        await waitFor(`/^This idea was made with an older glitch-idea/.test(document.getElementById('save-status')?.textContent??'')`, 'page refuses the older store', 20000);
        await sleep(500);
        const page = await read(`({status: document.getElementById('save-status').textContent, body: document.body.innerText})`);
        if (/unsupported_idea_version|Traceback|\bat \S+ \(|undefined|\[object|\{"/.test(page.body)) throw new Error('page shows a code or a trace: ' + page.body.slice(0, 300));
        if (page.status !== MESSAGE) throw new Error('page sentence differs: ' + page.status);
        await shot('12c-old-idea-refused');
        const after = files.filter(f => sha(f) !== old.get(f));
        if (after.length) throw new Error('refusal changed the bytes of ' + after.length + ' file(s)');
        return `v0.2-format idea (${files.length} files, workflow schema_version 2): CLI list and show refused with code unsupported_idea_version and "${MESSAGE}"; the page, reloaded on that store, shows "${page.status}" (the same older-idea sentence, plain words, no code, no trace); the idea's ${files.length} files hash identically before and after the refusals (nothing migrated or overwritten)`;
      } finally {
        for (const [f, bytes] of original) writeFileSync(f, bytes);
        await read(`(window.onbeforeunload=null, true)`).catch(() => {});
        await cdp.send('Page.reload');
        await waitFor(`document.getElementById('save-status')?.textContent==='Saved state loaded'`, 'store restored and page loaded again', 20000);
      }
    });
    await step('hand_release_unlocks_step', async () => {
      const id = await startIdea('Synthetic idea for the hand release check.', 'handrel-workspace', 5, 5);
      await handMethod('bounded-plan');
      await ensureStep('discovery');
      await waitFor(`!!document.getElementById('discovery-proposal-status')||/Your terminal is/.test(document.getElementById('discovery-conversation-status')?.textContent??'')`, 'discovery conversation started', 20000);
      const event = await nextEvent(id, 'discovery');
      if (!await read(`document.getElementById('discovery-problem').disabled`)) throw new Error('Discovery was not locked before the hand release');
      // Remainder over discovery_hand_fill: the fixture agent's open request is cancelled and its later fill is refused.
      await click('#discovery-hand-fill');
      await waitFor(`!document.getElementById('discovery-problem').disabled`, 'discovery released by hand');
      if (await read(`!!document.querySelector('#step-body .field-locked .lock-note')`)) throw new Error('lock notes remain after the release');
      const status = await read(`document.getElementById('discovery-conversation-status')?.textContent`);
      if (!/took this step by hand/.test(status ?? '')) throw new Error('page does not say the step is by hand: ' + status);
      const refused = fillRaw(event, {problem: 'Too late.'}, 1);
      if (refused.json?.ok !== false && refused.status === 0) throw new Error('a fill after the hand release was accepted: ' + JSON.stringify(refused.json).slice(0, 200));
      if (refused.code !== 'request_cancelled') throw new Error('fill after the release was refused with ' + refused.code + ', not request_cancelled: ' + redact(JSON.stringify(refused.json)).slice(0, 200));
      if (await read(`document.getElementById('discovery-problem').value`) !== '') throw new Error('the refused fill still reached the page');
      // The hand state is stored: a reload keeps it, and no new automatic request is sent for the step.
      await read(`(window.onbeforeunload=null, true)`);
      await cdp.send('Page.reload');
      await waitFor(`document.getElementById('save-status')?.textContent==='Saved state loaded'`, 'reload loaded', 20000);
      await click('#nav-ideas');
      await openFromBacklog(id);
      await waitFor(`document.getElementById('identity').title===${JSON.stringify(id)}`, 'idea reopened after reload');
      await ensureStep('discovery');
      await waitFor(`!!document.getElementById('discovery-problem')`, 'discovery body rendered after reload', 20000); await sleep(500);
      const after = await read(`({locked: document.getElementById('discovery-problem')?.disabled, notes: document.querySelectorAll('#step-body .lock-note').length, status: document.getElementById('discovery-conversation-status')?.textContent})`);
      if (after.locked !== false || after.notes !== 0 || !/took this step by hand/.test(after.status ?? '')) throw new Error('hand state lost on reload: ' + JSON.stringify(after));
      const batch = idea(['events', '--session', agentSession, '--after', String(cursor.n), '--timeout', '3', '--runtime-root', runtimeRoot]).json;
      cursor.n = batch.sequence;
      const fresh = batch.events.filter(e => e.idea_id === id && e.operation === 'discovery' && e.request_id !== event.request_id);
      if (fresh.length) throw new Error('a new discovery request reached the agent after the hand release: ' + JSON.stringify(fresh).slice(0, 200));
      // The human fills and accepts by hand.
      await typeInto('#discovery-problem', 'My own problem.'); await typeInto('#discovery-audience', 'My own audience.');
      await typeInto('#discovery-workaround', 'My own workaround.'); await typeInto('#discovery-evidence', 'My own evidence.');
      await typeInto('#discovery-kill-criteria', 'My own stop rule.');
      await click('#discovery-add-challenge');
      await typeInto('#discovery-challenge-0-challenge', 'My challenge.'); await typeInto('#discovery-challenge-0-response', 'My response.');
      await handPriorArtRow();
      await accept('discovery', 'discovery-accept');
      const saved = readFields(id, 'discovery');
      if (saved.problem !== 'My own problem.' || saved.challenges?.[0]?.response !== 'My response.') throw new Error('hand answers not saved: ' + JSON.stringify(saved).slice(0, 200));
      return 'hand-fill unlocked Discovery (no lock notes, page says "You took this step by hand"); the fixture agent\'s later fill got ' + refused.code + ' and never reached the page; after a reload the step is still unlocked and no new discovery request reached the agent (the stored hand flag; hand_released is the browser-request refusal, not reachable from the agent verbs); the human filled and accepted by hand';
    });
  }

  if (resumeOk && proposalIdea) {
    // Demo finding: once an Exploration suggestion was in use and the agent dropped, the human could
    // not finish the step with their own answers ("I cant go back to the human path").
    await step('agent_drop_human_path_exploration', async () => {
      const dropIdea = await startIdea('Synthetic idea for the agent-drop human path.', 'drop-workspace', 4, 5);
      proposalIdea = dropIdea;  // acceptedAt/savedOf read this idea from here on (last step that uses them)
      await handMethod('bounded-plan');
      await handDiscovery();
      const fields = exploreFields('Fixture'), mine = 'My own desired result after the drop.';
      await requestAndUse('exploration', () => serveAgent(agentSession, dropIdea, 'exploration', cursor, () => fields), fields.outcome);
      const closed = idea(['session-close', '--session', agentSession, '--runtime-root', runtimeRoot]).json;
      if (closed.agent_status !== 'disconnected') throw new Error('session-close did not disconnect: ' + JSON.stringify(closed));
      await waitFor(`document.getElementById('agent-status')?.textContent!=='Agent connected'`, 'agent shown disconnected', 20000);
      await replaceText('#exploration-outcome', mine);
      const before = await read(`JSON.stringify({accept_disabled: document.getElementById('exploration-accept')?.disabled ?? null, own: document.getElementById('exploration-accept-own') ? (document.getElementById('exploration-accept-own').disabled ? 'disabled' : 'enabled') : 'absent'})`);
      await shot('11-agent-drop-exploration');
      // The way back sits in the step's foot beside the disabled Accept, with its reason.
      if (!await read(`document.getElementById('exploration-accept').disabled`)) throw new Error('Accept should be disabled while the dropped suggestion is linked: ' + before);
      if (!await read(`!!document.getElementById('exploration-accept-own')&&!document.getElementById('exploration-accept-own').disabled`))
        throw new Error('after the agent dropped, no enabled "accept as my own answers" beside Accept: ' + before);
      const reason = await read(`document.getElementById('exploration-accept-own-reason')?.textContent ?? ''`);
      if (!/can no longer be accepted/.test(reason)) throw new Error('the own-answers door does not say why: ' + reason.slice(0, 80));
      await accept('exploration', 'exploration-accept-own');
      const at = acceptedAt('exploration', mine);
      if (at !== 'workflow.steps.exploration.fields.outcome') throw new Error('my own answer was not accepted as the Exploration outcome: ' + at);
      const linked = JSON.stringify(savedOf().workflow?.steps?.exploration?.acceptance ?? {});
      if (/proposal_[0-9a-f]{32}/.test(linked)) throw new Error('the own-answers accept still carries a suggestion link: ' + linked.slice(0, 160));
      return `after the drop the page offered ${before}; my own Exploration was accepted at ${at}`;
    });
  }

  // Run after the agent drop: no terminal request is pending, so every step opens settled and Accept's reason is the only thing on screen.
  if (resumeOk && proposalIdea) {
    await step('accept_reason_every_step', async () => {
      const verdicts = {};
      await click('#nav-ideas');
      await waitFor(`!!document.getElementById('new-idea')&&!document.getElementById('new-idea').disabled`, 'ideas view');
      await click('#new-idea');
      await waitFor(`document.getElementById('identity').textContent==='Not saved yet'`, 'new idea');
      verdicts.capture = await reasonVerdict('capture');
      await typeInto('#idea-text', 'Synthetic idea for the accept-reason check.');
      await typeInto('#workspace-name', 'reason-workspace');
      await typeInto('#workspace-path', workspace);
      verdicts.capture_unconfirmed = await reasonVerdict('capture');
      await click('#workspace-confirmed');
      await accept('capture', 'capture-accept');
      await ensureStep('priorities');
      verdicts.priorities = await reasonVerdict('priorities');
      await click('#urgency-4'); await click('#importance-4');
      await accept('priorities', 'priorities-accept');
      await ensureStep('method'); await settle('method');
      verdicts.method = await reasonVerdict('method');
      await click('#method-choice-bounded-plan'); await accept('method', 'method-accept');
      await ensureStep('discovery'); await settle('discovery');
      verdicts.discovery_start = await reasonVerdict('discovery');
      if (await read(`!!document.getElementById('discovery-hand-fill')`)) { await click('#discovery-hand-fill'); await waitFor(`!document.getElementById('discovery-problem').disabled`, 'discovery released by hand'); }
      verdicts.discovery = await reasonVerdict('discovery');
      await typeInto('#discovery-problem', 'Synthetic problem.');
      verdicts.discovery_partial = await reasonVerdict('discovery');
      await typeInto('#discovery-audience', 'Synthetic audience.'); await typeInto('#discovery-workaround', 'Synthetic workaround.');
      await typeInto('#discovery-evidence', 'Synthetic evidence.'); await typeInto('#discovery-kill-criteria', 'Synthetic stop rule.');
      await click('#discovery-add-challenge');
      await typeInto('#discovery-challenge-0-challenge', 'Synthetic challenge.'); await typeInto('#discovery-challenge-0-response', 'Synthetic response.');
      await handPriorArtRow();
      await accept('discovery', 'discovery-accept');
      await ensureStep('exploration'); await settle('exploration');
      verdicts.exploration_start = await reasonVerdict('exploration');
      if (await read(`!!document.getElementById('exploration-hand-fill')`)) { await click('#exploration-hand-fill'); await waitFor(`!document.getElementById('exploration-outcome').disabled`, 'exploration released by hand'); }
      verdicts.exploration = await reasonVerdict('exploration');
      await typeInto('#exploration-outcome', 'Synthetic desired result.');
      await click('#exploration-scope-capability');
      await typeInto('#exploration-scope-reason', 'Synthetic scope reason.');
      await typeInto('#exploration-alternative-0-route', 'Simpler synthetic route');
      await typeInto('#exploration-alternative-0-reason', 'Smallest first.');
      verdicts.exploration_partial = await reasonVerdict('exploration');
      await typeInto('#exploration-next-slice', 'Synthetic next slice.');
      await click('#exploration-add-sketch');
      await typeInto('#exploration-sketch-0-title', 'Synthetic sketch item'); await typeInto('#exploration-sketch-0-done-when', 'Synthetic done when.');
      await accept('exploration', 'exploration-accept');
      await ensureStep('visualize');
      verdicts.visualize = await reasonVerdict('visualize');
      await click('#visualize-skipped');
      await waitFor(`['skipped'].includes(document.getElementById('compact-visualize')?.dataset.status)`, 'visualize skipped (one click records it)');
      await ensureStep('assess'); await settle('assess');
      verdicts.assess = await reasonVerdict('assess');
      // Review is not an accept step (no Accept button until its own handoff); it is not recorded as accepted here.
      verdicts.review = (await acceptReasonOf('review')).present ? 'has-accept' : 'absent';
      const reasoned = Object.entries(verdicts).filter(([, v]) => v === 'disabled+reason').map(([k]) => k);
      for (const need of ['capture', 'priorities', 'discovery', 'exploration', 'assess']) if (!reasoned.includes(need)) throw new Error(need + ' never showed a disabled Accept with a reason: ' + JSON.stringify(verdicts));
      return 'every step checked while empty or partial; a disabled Accept always had a plain reason (line + aria-describedby + title): ' + JSON.stringify(verdicts);
    });
  }

  // J6b: with no terminal connected (the agent session was closed in agent_drop_human_path_exploration), both steps open unlocked.
  if (resumeOk && proposalIdea) {
    await step('no_agent_step_unlocked', async () => {
      const SENTENCE = 'No terminal is connected, so this step is yours to fill.';  // web/steps/discovery.js NO_TERMINAL and web/steps/exploration.js
      await startIdea('Synthetic idea for the no-terminal check.', 'noagent-workspace', 4, 4);
      if (await read(`document.getElementById('agent-status')?.textContent`) === 'Agent connected') throw new Error('an agent is still connected');
      await ensureStep('method'); await settle('method');
      await click('#method-choice-bounded-plan'); await accept('method', 'method-accept');
      const probe = key => read(`({status: document.getElementById('${key}-conversation-status')?.textContent ?? null,
        notes: document.querySelectorAll('#step-body .lock-note').length, hand: !!document.getElementById('${key}-hand-fill'),
        disabled: [...document.querySelectorAll('#step-body input:not([type=hidden]), #step-body textarea')].filter(e => e.disabled).length})`);
      await ensureStep('discovery'); await sleep(400);
      const d = await probe('discovery');
      if (d.status !== SENTENCE || d.notes || d.hand || d.disabled) throw new Error('Discovery is not open for hand filling: ' + JSON.stringify(d));
      await typeInto('#discovery-problem', 'Synthetic problem.'); await typeInto('#discovery-audience', 'Synthetic audience.');
      await typeInto('#discovery-workaround', 'Synthetic workaround.'); await typeInto('#discovery-evidence', 'Synthetic evidence.');
      await typeInto('#discovery-kill-criteria', 'Synthetic stop rule.');
      await click('#discovery-add-challenge');
      await typeInto('#discovery-challenge-0-challenge', 'Synthetic challenge.'); await typeInto('#discovery-challenge-0-response', 'Synthetic response.');
      await handPriorArtRow();
      await accept('discovery', 'discovery-accept');
      await ensureStep('exploration'); await sleep(400);
      const e = await probe('exploration');
      if (e.status !== SENTENCE || e.notes || e.hand || e.disabled) throw new Error('Exploration is not open for hand filling: ' + JSON.stringify(e));
      await shot('11b-no-agent-exploration-open');
      return 'no agent connected: Discovery and Exploration both show "' + SENTENCE + '" with no lock notes, no hand-fill button and no disabled inputs';
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
    // "Step N of 8 · Resume" now lives in the selected idea's side panel: select each row in turn with a real click.
    const resumable = [];
    for (const c of cards) {
      await click(`#backlog-select-${c.id}`);
      await waitFor(`document.getElementById('backlog-detail')?.dataset.ideaId===${JSON.stringify(c.id)}&&document.querySelector('#ideas-list tr[data-idea-id="${c.id}"]')?.getAttribute('aria-selected')==='true'`, 'idea selected ' + c.id);
      if (await read(`/Step \\d of 8 · Resume/.test(document.getElementById('backlog-detail').textContent)`)) resumable.push(c);
    }
    if (!resumable.length) throw new Error('no selected idea shows "Step N of 8 · Resume" in its side panel');
    const chips = [...new Set(cards.map(c => c.chip))].join(' / ');
    // Markdown, read-only in the page.
    const target = cards.find(c => c.status !== 'archived') ?? cards[0];
    target.idea_id = target.id;
    await click(`#backlog-select-${target.idea_id}`);
    await waitFor(`document.getElementById('backlog-detail')?.dataset.ideaId===${JSON.stringify(target.idea_id)}`, 'target selected');
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
    return `${cards.length} cards (${chips}); ${resumable.length} with Step N of 8 · Resume; Markdown read-only in <pre> (contains "${word}"); moved one idea up ${mover.position}->${mover.position - 1}, CLI order and cards agree`;
  });

  // A moved idea is a read-only pointer to its file in a workspace: hidden by default, shown by its chip, no edit controls.
  await step('moved_idea_pointer', async () => {
    const home = join(tmp, 'moved-site'); mkdirSync(home);
    const file = (name, value) => { const path = join(tmp, name); writeFileSync(path, typeof value === 'string' ? value : JSON.stringify(value)); return path; };
    let made = idea(['capture', '--text-file', file('moved-idea.txt', 'A pointer idea that moves to a workspace'), '--actor', 'operator']).json.idea;
    made = idea(['exploration', made.idea_id, '--file', file('moved-exploration.json', {outcome: 'Reduce manual work', alternatives: [{route: 'Reuse', reason: 'Check the existing route first'}],
      assumptions: ['A user has this need'], scope: 'capability', scope_reason: 'One new reusable ability', next_slice: 'Try one real user', learning: [], investment: null, experiment: null,
      sketch: [{title: 'Run one trial', why_next: 'Cheapest test', done_when: 'Trial observed', method: null}]}), '--expected-revision', String(made.revision), '--actor', 'operator']).json.idea;
    made = idea(['rate', made.idea_id, '--urgency', '7', '--importance', '9', '--expected-revision', String(made.revision), '--actor', 'operator']).json.idea;
    made = idea(['assess', made.idea_id, '--file', file('moved-assess.json', {method: 'wsjf', version: '1', inputs: {value: 8, time_criticality: 4, enablement: 2, effort: 2},
      basis: 'Fixture', assumptions: [], confidence: 'low', provenance: 'Operator discussion'}), '--expected-revision', String(made.revision), '--actor', 'operator']).json.idea;
    const plan = file('moved-plan.md', '# Trial plan\n\n## Goal\nReduce manual work.\n\n## Tasks\n- Run one trial.\n\n## Validation\nObserve actual result.\n\n## Idea trace\nidea_id: ' + made.idea_id + '\nidea_revision: ' + made.revision + '\n');
    idea(['register-plan', made.idea_id, '--path', plan, '--expected-revision', String(made.revision), '--actor', 'operator', '--workspace-name', 'Moved Site', '--workspace-path', home]);
    const lifecycle = idea(['list']).json.lifecycles?.[made.idea_id];
    if (lifecycle?.lifecycle !== 'moved') throw new Error('the CLI did not move the idea: ' + JSON.stringify(lifecycle));
    await click('#nav-ideas');
    await waitFor(`!!document.getElementById('new-idea')`, 'ideas');
    await click('#ideas-refresh');
    await waitFor(`!!document.querySelector('#ideas-list tr[data-idea-id="${made.idea_id}"]')&&document.getElementById('ideas-refresh')?.textContent==='Refresh ideas'`, 'moved row loaded');
    await sleep(250);
    if (!await read(`document.querySelector('#ideas-list tr[data-idea-id="${made.idea_id}"]').hidden`)) throw new Error('the moved row is shown while its chip is off');
    if (await read(`document.getElementById('ideas-filter-moved')?.getAttribute('aria-pressed')`) !== 'false') throw new Error('the In progress chip is not off by default');
    const chipText = await read(`document.getElementById('ideas-filter-moved')?.getAttribute('aria-label')`);
    if (!/^In progress \(\d+\)$/.test(chipText ?? '')) throw new Error('chip has no count: ' + chipText);
    await click('#ideas-filter-moved');
    await waitFor(`document.querySelector('#ideas-list tr[data-idea-id="${made.idea_id}"]')?.hidden===false`, 'row shown by its chip');
    await click(`#backlog-select-${made.idea_id}`);
    await waitFor(`document.getElementById('backlog-detail')?.dataset.ideaId===${JSON.stringify(made.idea_id)}`, 'moved idea selected');
    const panel = await read(`({note: document.getElementById('backlog-moved-note')?.textContent, path: document.getElementById('backlog-moved-path')?.textContent,
      open: !!document.getElementById('ideas-open-${made.idea_id}'), md: !!document.getElementById('backlog-md-${made.idea_id}'), move: !!document.getElementById('backlog-move-${made.idea_id}'),
      up: !!document.getElementById('backlog-up-${made.idea_id}'), resume: /Resume/.test(document.getElementById('backlog-detail').textContent),
      label: document.querySelector('#ideas-list tr[data-idea-id="${made.idea_id}"] .status-chip')?.textContent})`);
    if (panel.note !== 'Moved to Moved Site' || !panel.path?.startsWith(home)) throw new Error('panel does not name the new home: ' + JSON.stringify(panel));
    if (panel.open || panel.md || panel.move || panel.up || panel.resume) throw new Error('a pointer row offers an edit control: ' + JSON.stringify(panel));
    if (!/In progress/.test(panel.label ?? '')) throw new Error('row label: ' + panel.label);
    await read(`(document.getElementById('step-body').scrollTop=0,true)`);
    await shot('13-backlog-moved-pointer');
    return 'moved row hidden by default; the In progress chip (' + chipText + ') shows it; panel says "' + panel.note + '" with its file path and offers no Resume, Open, Markdown, Move or reorder';
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

  // A configured default workspace pre-fills Capture for a new idea (its own service from a copy of the skill carrying that config).
  await step('capture_default_workspace_prefill', async () => {
    const skill = join(tmp, 'skill-copy'); cpSync(resolve(dirname(HELPER), '..'), skill, {recursive: true, filter: from => !from.includes('__pycache__')});
    const dflt = join(tmp, 'default-site'); mkdirSync(dflt);
    writeFileSync(join(skill, 'config.json'), JSON.stringify({plan_validator_argv: null, default_workspace: {name: 'Default Site', path: dflt}}));
    const second = join(runtimeRoot, 'second'); mkdirSync(second, {mode: 0o700});  // inside the first root, so cleanup stops its service too
    const run = spawnSync(PYTHON, [join(skill, 'scripts/idea.py'), '--store', join(tmp, 'store-default'), 'session-open', '--runtime-root', second], {encoding: 'utf8', timeout: 60000});
    let opened2 = null; try { opened2 = JSON.parse(run.stdout); } catch {}
    if (!opened2?.origin) throw new Error('second service did not open: ' + redact((run.stdout || run.stderr).slice(0, 300)));
    secrets.add(opened2.pairing_code);
    // A second tab, so the first one (and its service) stays as the later steps expect it.
    const tab = await getJson(`http://127.0.0.1:${port}/json/new?about:blank`, {method: 'PUT'}).catch(error => { throw new Error('could not open a second tab on port ' + port + ': ' + (error.cause?.message ?? error.message)); });
    const first = cdp;  // the first tab stays connected for the steps that follow
    cdp = new Cdp(tab.webSocketDebuggerUrl); await cdp.open();
    for (const domain of ['Page', 'Runtime', 'DOM']) await cdp.send(domain + '.enable');
    await setViewport(1280);
    try {
      await cdp.send('Page.navigate', {url: opened2.origin + '#pair=' + opened2.pairing_code});
      await waitFor(`document.getElementById('save-status')?.textContent==='Saved state loaded'`, 'second service paired');
      await ensureStep('capture');
      await waitFor(`!!document.getElementById('workspace-name')`, 'capture fields');
      const filled = await read(`({name: document.getElementById('workspace-name').value, path: document.getElementById('workspace-path').value, confirmed: document.getElementById('workspace-confirmed').checked})`);
      if (filled.name !== 'Default Site' || filled.path !== dflt || filled.confirmed) throw new Error('capture was not prefilled: ' + JSON.stringify(filled));
      await replaceText('#workspace-name', 'Typed Over');
      const typed = await read(`document.getElementById('workspace-name').value`);
      if (typed !== 'Typed Over') throw new Error('the prefilled name is not editable: ' + typed);
      await shot('14-capture-default-workspace');
    } finally {
      try { cdp.close(); } catch {}
      cdp = first; await setViewport(1280);
      for (const pid of serviceProcesses().filter(pid => spawnSync('ps', ['-p', String(pid), '-o', 'args='], {encoding: 'utf8'}).stdout.includes(second))) { try { process.kill(pid, 'SIGTERM'); } catch {} }
    }
    return 'Capture opened with name and path from the default workspace, confirmation left unticked, and the name stayed editable';
  });

  await screenshotPass();

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
}

// Owner screenshots (non-required): every screen in dark and light at 1440x900 and in dark at 440x900, saved next to the other evidence.
// Only real input is used (CDP clicks); a screen that cannot be reached is listed in summary.screenshots.missed, never failed.
async function screenshotPass() {
  const written = [], missed = [];
  const rows = idea(['list']).json.ideas;
  const full = rows.find(r => r.completed_steps === 8) ?? rows[0];
  const screens = ['capture', 'method', 'discovery', 'exploration', 'visualize', 'assess', 'review', 'ideas', 'setup'];
  const files = {capture: 'capture', method: 'methods', discovery: 'discovery', exploration: 'exploration', visualize: 'visualize', assess: 'assess', review: 'review', ideas: 'ideas', setup: 'setup'};
  const toIdea = async () => {
    await click('#nav-ideas');
    await waitFor(`!!document.getElementById('new-idea')`, 'ideas', 8000);
    await openFromBacklog(full.idea_id);
    await waitFor(`document.getElementById('identity').title===${JSON.stringify(full.idea_id)}`, 'idea open', 8000);
  };
  const pass = async (prefix, width, themeClicks) => {
    await setViewport(width, 900);
    for (let i = 0; i < themeClicks; i++) await click('#theme-toggle');
    await sleep(250);
    for (const key of screens) {
      const name = prefix + '-' + files[key];
      try {
        if (key === 'ideas') { await click('#nav-ideas'); await waitFor(`!!document.getElementById('new-idea')`, 'ideas', 8000); }
        else if (key === 'setup') { await click('#nav-setup'); await waitFor(`!!document.getElementById('setup-native')`, 'setup', 8000); }
        else {
          if (!await read(`!!document.getElementById('compact-capture')?.offsetParent`)) {
            await click('#nav-idea');
            await waitFor(`!!document.getElementById('compact-capture')?.offsetParent`, 'idea screen', 8000);
          }
          await click(sel('compact-' + key));
          await waitFor(`document.querySelector('#compact-nav [aria-current="step"]')?.id==='compact-${key}'`, 'step ' + key, 8000);
        }
        await sleep(250);
        await shot(name); written.push(name + '.png');
      } catch (error) { missed.push(name + ': ' + String(error.message ?? error).slice(0, 100)); }
    }
  };
  try {
    await toIdea();
    await pass('wide-dark', 1440, 0);
    await pass('wide-light', 1440, 1);
    await click('#theme-toggle');                  // back to the dark default
    await pass('narrow', 440, 0);
    await setViewport(1280, 900);
  } catch (error) { missed.push('pass: ' + String(error.message ?? error).slice(0, 100)); }
  summary.screenshots = {dir: EVIDENCE, written, missed};
  record('owner_screenshots', missed.length === 0, `${written.length} screenshots written` + (missed.length ? '; missed ' + missed.length : ''), false);
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
