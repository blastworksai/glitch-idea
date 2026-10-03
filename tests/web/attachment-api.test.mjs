// Bounded authenticated attachment reads; no browser/native claim.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = await readFile(new URL('../../glitch-idea/web/api.js', import.meta.url), 'utf8');
const {IdeaApi, ApiError, UPLOAD_MAX_BYTES} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const binding = 'binding_' + 'a'.repeat(32), asset = 'asset_' + 'b'.repeat(32);
const bytes = value => new TextEncoder().encode(value);
const refused = code => error => error instanceof ApiError && error.code === code && error.uncertain === false;

function response(chunks = [bytes('data')], {length = '4', type = 'image/png', status = 200, failure = false} = {}) {
  const seen = {reads: 0, cancels: 0, released: 0};
  const reader = {
    async read() {
      seen.reads++;
      if (failure) throw new Error('private filesystem or transport diagnostic');
      return chunks.length ? {done: false, value: chunks.shift()} : {done: true};
    },
    async cancel() { seen.cancels++; },
    releaseLock() { seen.released++; },
  };
  return {seen, reply: {
    status, ok: status >= 200 && status < 300,
    headers: new Headers({...(length === null ? {} : {'Content-Length': length}), 'Content-Type': type}),
    body: {getReader: () => reader, cancel: async () => { seen.cancels++; }},
  }};
}

function harness(reply = response().reply, timeout = 1000) {
  const calls = [];
  const api = new IdeaApi(async (url, options) => { calls.push({url, options}); return reply; }, timeout, binding);
  api.csrf = 'test-only-csrf';
  return {api, calls};
}

test('fixed authenticated GET preserves binding/cookie controls and returns only inert Blob', async () => {
  const fixture = response([bytes('da'), bytes('ta')]), h = harness(fixture.reply);
  const blob = await h.api.attachment(asset, 4);
  assert.ok(blob instanceof Blob); assert.equal(blob.size, 4); assert.equal(await blob.text(), 'data');
  assert.equal(blob.type, 'application/octet-stream'); assert.equal(h.calls.length, 1);
  const {url, options} = h.calls[0];
  assert.equal(url, '/api/v1/attachments/' + asset); assert.equal(options.method, 'GET');
  assert.equal(options.credentials, 'same-origin'); assert.equal(options.cache, 'no-store'); assert.equal(options.redirect, 'error');
  assert.deepEqual(options.headers, {Accept: 'application/octet-stream', 'X-Idea-Binding': binding});
  assert.equal(options.body, undefined); assert.ok(options.signal instanceof AbortSignal);
  assert.equal(fixture.seen.released, 1);
});

test('actual streamed Response raster and download MIME bodies all become octet-stream Blob', async () => {
  for (const type of ['image/png', 'image/jpeg', 'image/webp', 'application/octet-stream']) {
    const h = harness(new Response('data', {headers: {'Content-Length': '4', 'Content-Type': type}}));
    const blob = await h.api.attachment(asset, 4);
    assert.equal(blob.type, 'application/octet-stream'); assert.equal(await blob.text(), 'data');
  }
});

test('hostile IDs and invalid expected sizes refuse before any fetch', async () => {
  const h = harness();
  for (const id of ['../private', '/api/v1/state', asset + '?binding=private', 'asset_ABC', null, 3]) {
    await assert.rejects(h.api.attachment(id, 4), refused('invalid_attachment'));
  }
  for (const size of [0, -1, 1.5, true, '4', NaN, Infinity, UPLOAD_MAX_BYTES + 1]) {
    await assert.rejects(h.api.attachment(asset, size), refused('invalid_attachment'));
  }
  assert.equal(h.calls.length, 0);
});

test('read authentication requires pinned binding but does not require or send CSRF', async () => {
  const h = harness(); h.api.csrf = null;
  assert.equal((await h.api.attachment(asset, 4)).size, 4);
  assert.equal(h.calls[0].options.headers['X-CSRF-Token'], undefined);
  const api = new IdeaApi(async () => assert.fail('unauthenticated fetch'));
  await assert.rejects(api.attachment(asset, 4), refused('browser_unauthorized'));
});

test('malformed or mismatched Content-Length cancels before consuming body', async () => {
  for (const length of [null, '0', '04', '-4', '4,4', '4x', '9007199254740992', '3', '5']) {
    const fixture = response(undefined, {length}), h = harness(fixture.reply);
    await assert.rejects(h.api.attachment(asset, 4), refused('invalid_response'));
    assert.equal(fixture.seen.reads, 0); assert.ok(fixture.seen.cancels > 0); assert.equal(h.calls.length, 1);
  }
});

test('advertised bytes beyond file maximum refuse without reading or retry', async () => {
  const fixture = response(undefined, {length: String(UPLOAD_MAX_BYTES + 1)}), h = harness(fixture.reply);
  await assert.rejects(h.api.attachment(asset, 4), refused('too_large'));
  assert.equal(fixture.seen.reads, 0); assert.ok(fixture.seen.cancels > 0); assert.equal(h.calls.length, 1);
});

test('oversized stream is cancelled immediately without reading later chunks', async () => {
  const fixture = response([bytes('data'), bytes('x'), bytes('unread')]), h = harness(fixture.reply);
  await assert.rejects(h.api.attachment(asset, 4), refused('too_large'));
  assert.equal(fixture.seen.reads, 2); assert.ok(fixture.seen.cancels > 0); assert.equal(fixture.seen.released, 1);
});

test('short and malformed chunks never return a Blob', async () => {
  for (const chunks of [[bytes('da')], ['private malformed chunk']]) {
    const fixture = response(chunks), h = harness(fixture.reply);
    await assert.rejects(h.api.attachment(asset, 4), refused('invalid_response'));
    assert.ok(fixture.seen.cancels > 0); assert.equal(h.calls.length, 1);
  }
});

test('active MIME, partial success and missing body refuse safely', async () => {
  for (const extra of [{type: 'text/html'}, {type: 'image/svg+xml'}, {status: 206}]) {
    const fixture = response(undefined, extra), h = harness(fixture.reply);
    await assert.rejects(h.api.attachment(asset, 4), refused('invalid_response'));
    assert.equal(fixture.seen.reads, 0);
  }
  const fixture = response(); fixture.reply.body = null;
  await assert.rejects(harness(fixture.reply).api.attachment(asset, 4), refused('invalid_response'));
});

test('HTTP failure returns typed redacted code only and clears expired CSRF', async () => {
  const data = bytes(JSON.stringify({ok: false, code: 'browser_unauthorized', path: '/private/secret', diagnostics: 'credential'}));
  const fixture = response([data], {status: 401, type: 'application/json', length: String(data.length)}), h = harness(fixture.reply);
  await assert.rejects(h.api.attachment(asset, 4), error => refused('browser_unauthorized')(error) &&
    error.status === 401 && JSON.stringify(error.data) === JSON.stringify({ok: false, code: 'browser_unauthorized'}));
  assert.equal(h.api.csrf, null); assert.equal(h.api.bindingId, binding); assert.equal(h.calls.length, 1);
});

test('malformed and oversized error responses are bounded and never expose diagnostics', async () => {
  for (const text of ['private body', '{"ok":false,"code":"/private/secret"}', '{"ok":true,"code":"ok"}']) {
    const data = bytes(text), fixture = response([data], {status: 500, type: 'application/json', length: String(data.length)});
    await assert.rejects(harness(fixture.reply).api.attachment(asset, 4), error => refused('invalid_response')(error) && Object.keys(error.data).length === 0);
  }
  const fixture = response([new Uint8Array(8193)], {status: 500, type: 'application/json', length: '8193'});
  await assert.rejects(harness(fixture.reply).api.attachment(asset, 4), refused('invalid_response'));
  assert.equal(fixture.seen.reads, 0); assert.ok(fixture.seen.cancels > 0);
});

test('stream and fetch failures stay redacted read errors with no automatic retry', async () => {
  const fixture = response(undefined, {failure: true}), h = harness(fixture.reply);
  await assert.rejects(h.api.attachment(asset, 4), error => refused('connection_lost')(error) && !error.message.includes('private'));
  assert.ok(fixture.seen.cancels > 0); assert.equal(h.calls.length, 1);
  let calls = 0;
  const api = new IdeaApi(async () => { calls++; throw new Error('private fetch diagnostic'); }, 1000, binding);
  await assert.rejects(api.attachment(asset, 4), refused('connection_lost')); assert.equal(calls, 1);
});

test('exact maximum-size stream is admitted and bounded', async () => {
  const fixture = response([new Uint8Array(UPLOAD_MAX_BYTES)], {length: String(UPLOAD_MAX_BYTES)});
  const blob = await harness(fixture.reply).api.attachment(asset, UPLOAD_MAX_BYTES);
  assert.equal(blob.size, UPLOAD_MAX_BYTES); assert.equal(blob.type, 'application/octet-stream');
});

// headers arrive immediately; the actual body read waits on Fetch's signal.
function delayedBody(signal, delay) {
  const fixture = response();
  const reader = fixture.reply.body.getReader();
  const read = reader.read.bind(reader);
  let first = true;
  reader.read = async () => {
    if (first) {
      first = false;
      await new Promise((resolve, reject) => {
        const aborted = () => { clearTimeout(timer); reject(new Error('private abort diagnostic')); };
        const timer = setTimeout(() => { signal.removeEventListener('abort', aborted); resolve(); }, delay);
        signal.addEventListener('abort', aborted, {once: true});
        if (signal.aborted) aborted();
      });
    }
    return read();
  };
  return fixture;
}

test('attachment streaming may exceed JSON budget within the independent byte budget', async () => {
  let calls = 0, fixture, signal;
  const api = new IdeaApi(async (_url, options) => {
    calls++; signal = options.signal; fixture = delayedBody(signal, 40); return fixture.reply;
  }, 10, binding, 200);
  const blob = await api.attachment(asset, 4);
  assert.equal(await blob.text(), 'data'); assert.equal(blob.type, 'application/octet-stream');
  assert.equal(signal.aborted, false); assert.equal(calls, 1);
  assert.equal(fixture.seen.released, 1); assert.equal(fixture.seen.cancels, 0);
});

test('expired byte budget aborts a streaming attachment, releases it and redacts the error', async () => {
  let calls = 0, fixture, signal;
  const api = new IdeaApi(async (_url, options) => {
    calls++; signal = options.signal; fixture = delayedBody(signal, 100); return fixture.reply;
  }, 1000, binding, 10);
  await assert.rejects(api.attachment(asset, 4), error => refused('connection_lost')(error) &&
    Object.keys(error.data).length === 0 && !error.message.includes('private'));
  assert.equal(signal.aborted, true); assert.equal(calls, 1);
  assert.equal(fixture.seen.released, 1); assert.ok(fixture.seen.cancels > 0);
});
