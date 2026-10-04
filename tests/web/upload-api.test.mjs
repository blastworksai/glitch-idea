// Browser transport contract tests; no native/server qualification.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = await readFile(new URL('../../glitch-idea/web/api.js', import.meta.url), 'utf8');
const {IdeaApi, ApiError, UPLOAD_MAX_BYTES} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const binding = 'binding_' + 'a'.repeat(32), idea = 'idea_' + 'b'.repeat(32);
const upload = 'upload_' + 'c'.repeat(32), asset = 'asset_' + 'd'.repeat(32);
const completion = 'upload-bytes:' + upload;
const metadata = () => ({request_id:'upload-intent-1',idea_id:idea,expected_revision:4,name:'design.md',declared_type:'text/markdown',size:5});
const result = (request_id = completion, extra = {}) => ({ok:true,code:'ok',write_state:'applied',request_id,idea_id:idea,upload_id:upload,asset_id:asset,...extra});
const response = (body, status = 200) => ({ok:status>=200&&status<300,status,json:async()=>structuredClone(body)});
function harness(reply = () => response(result())) {
  const calls=[];
  const api=new IdeaApi(async(url,options)=>{calls.push({url,options});return reply(url,options);},1000,binding);
  api.csrf='test-only-csrf';
  return {api,calls};
}
const rejected = (code, uncertain) => error => error instanceof ApiError && error.code===code && (uncertain===undefined||error.uncertain===uncertain);

test('metadata sends the exact authenticated envelope and returns an intent only',async()=>{
  const h=harness(()=>response(result('upload-intent-1',{completion_request_id:completion})));
  const envelope=metadata(), saved=await h.api.uploadMetadata(envelope);
  assert.equal(saved.completion_request_id,completion);assert.deepEqual(envelope,metadata());
  assert.equal(h.calls.length,1);
  const {url,options}=h.calls[0];
  assert.equal(url,'/api/v1/uploads');assert.equal(options.method,'POST');
  assert.deepEqual(JSON.parse(options.body),envelope);
  assert.equal(options.credentials,'same-origin');assert.equal(options.redirect,'error');
  assert.equal(options.headers['X-Idea-Binding'],binding);assert.equal(options.headers['X-CSRF-Token'],'test-only-csrf');
});

test('binary upload uses the generated fixed route and original bounded Blob',async()=>{
  const h=harness(),bytes=new Blob(['hello'],{type:'text/markdown'});
  assert.equal((await h.api.uploadBytes(upload,bytes)).asset_id,asset);
  const {url,options}=h.calls[0];
  assert.equal(url,'/api/v1/uploads/'+upload+'/bytes');assert.equal(options.method,'PUT');assert.equal(options.body,bytes);
  assert.equal(options.headers['Content-Type'],'application/octet-stream');
  assert.equal(options.headers['X-Idea-Binding'],binding);assert.equal(options.headers['X-CSRF-Token'],'test-only-csrf');
  assert.equal(options.headers['Content-Length'],undefined);assert.equal(options.redirect,'error');
  assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store');
  assert.ok(options.signal instanceof AbortSignal);
});

test('invalid metadata, routes and oversized bytes refuse before fetch',async()=>{
  const h=harness();
  for(const change of [{path:'/private'},{request_id:'../escape'},{idea_id:'other'},{expected_revision:true},
    {name:'   '},{name:'a'.repeat(4097)},{name:'bad\ud800.md'},
    {declared_type:'text/plain\r\nX-Fake: yes'},{size:UPLOAD_MAX_BYTES+1},{size:-1},{size:0}]) {
    await assert.rejects(h.api.uploadMetadata({...metadata(),...change}),rejected('invalid_upload',false));
  }
  for(const id of ['../private','https://outside.invalid/file','upload_x',upload+'/bytes']) {
    await assert.rejects(h.api.uploadBytes(id,new Blob(['hi'])),rejected('invalid_upload',false));
  }
  await assert.rejects(h.api.uploadBytes(upload,'hello'),rejected('invalid_upload',false));
  await assert.rejects(h.api.uploadBytes(upload,new Blob([])),rejected('invalid_upload',false));
  await assert.rejects(h.api.uploadBytes(upload,new Blob([new Uint8Array(UPLOAD_MAX_BYTES+1)])),rejected('invalid_upload',false));
  assert.equal(h.calls.length,0);
});

test('hostile literal display names and Unicode scalar boundary are sent unchanged',async()=>{
  const h=harness(()=>response(result('upload-intent-1',{completion_request_id:completion})));
  for(const name of ['../../private/design.md','C:\\private\\<script>alert(1)</script>.md',
    '💡'.repeat(4093)+'.md']) {
    const envelope={...metadata(),name};await h.api.uploadMetadata(envelope);
    assert.equal(JSON.parse(h.calls.at(-1).options.body).name,name);
    assert.equal(h.calls.at(-1).url,'/api/v1/uploads');
  }
  await assert.rejects(h.api.uploadMetadata({...metadata(),name:'💡'.repeat(4094)+'.md'}),rejected('invalid_upload',false));
  assert.equal(h.calls.length,3);
});

test('missing authentication refuses metadata and bytes before fetch',async()=>{
  const h=harness();h.api.csrf=null;
  await assert.rejects(h.api.uploadMetadata(metadata()),rejected('browser_unauthorized',false));
  await assert.rejects(h.api.uploadBytes(upload,new Blob(['hi'])),rejected('browser_unauthorized',false));
  assert.equal(h.calls.length,0);
});

test('lost binary acknowledgement stays uncertain and does not retry automatically',async()=>{
  const h=harness(()=>{throw new Error('private transport diagnostic');});
  await assert.rejects(h.api.uploadBytes(upload,new Blob(['hello'])),error=>rejected('connection_lost',true)(error)&&!error.message.includes('private'));
  assert.equal(h.calls.length,1);
});

test('lost metadata acknowledgement keeps its request identity and sends no bytes',async()=>{
  const h=harness(()=>{throw new Error('lost');});
  await assert.rejects(h.api.uploadMetadata(metadata()),rejected('connection_lost',true));
  assert.equal(h.calls.length,1);assert.equal(JSON.parse(h.calls[0].options.body).request_id,'upload-intent-1');
});

test('malformed, mismatched and uncertain success cannot complete an upload',async()=>{
  for(const body of [result(completion,{asset_id:null}),result('other'),result(completion,{upload_id:'upload_'+'e'.repeat(32)}),
    result(completion,{write_state:'not_applied'}),result(completion,{idea_id:'malformed'}),
    result(completion,{idea_id:undefined}),result(completion,{completion_request_id:'wrong'}),{ok:true,code:'ok'}]) {
    const h=harness(()=>response(body));
    await assert.rejects(h.api.uploadBytes(upload,new Blob(['hello'])),rejected('invalid_response',true));assert.equal(h.calls.length,1);
  }
  const h=harness(()=>response(result(completion,{write_state:'committed_uncertain',committed:true})));
  await assert.rejects(h.api.uploadBytes(upload,new Blob(['hello'])),rejected('durability_uncertain',true));
  const mismatch=harness(()=>response(result('upload-intent-1',{completion_request_id:'other'})));
  await assert.rejects(mismatch.api.uploadMetadata(metadata()),rejected('invalid_response',true));
});

test('invalid JSON and 401 invalidate completion/token without implicit re-pair',async()=>{
  const bad=harness(()=>({ok:true,status:200,json:async()=>{throw new Error('bad JSON');}}));
  await assert.rejects(bad.api.uploadBytes(upload,new Blob(['hi'])),rejected('invalid_response',true));
  const expired=harness(()=>response({ok:false,code:'browser_unauthorized',write_state:'not_applied'},401));
  await assert.rejects(expired.api.uploadBytes(upload,new Blob(['hi'])),rejected('browser_unauthorized',false));
  assert.equal(expired.api.csrf,null);assert.equal(expired.api.bindingId,binding);assert.equal(expired.calls.length,1);
});

test('completion reconciliation reads its derived receipt and actual state without writes',async()=>{
  const h=harness(url=>response(url.includes('/requests/')?result():{ok:true,code:'ok',idea_id:idea,asset_inventory:{uploads:[]}}));
  const resolved=await h.api.reconcileUploadBytes(upload,idea);
  assert.equal(resolved.result.asset_id,asset);
  assert.deepEqual(h.calls.map(call=>call.url),['/api/v1/requests/'+encodeURIComponent(completion),'/api/v1/state?idea_id='+idea]);
  assert.ok(h.calls.every(call=>call.options.method==='GET'));
});

test('missing receipt still reads state and never turns absence into success or retry',async()=>{
  const h=harness(url=>url.includes('/requests/')?response({ok:false,code:'request_not_found'},404):response({ok:true,code:'ok',idea_id:idea}));
  const resolved=await h.api.reconcileUpload('upload-intent-1',idea);
  assert.equal(resolved.result,null);assert.equal(resolved.state.idea_id,idea);assert.equal(h.calls.length,2);
  assert.throws(()=>h.api.reconcileUploadBytes('../file',idea),rejected('invalid_upload',false));
});

test('typed reconciliation refuses unrelated or uncertain receipts after state read',async()=>{
  for(const receipt of [result(completion,{idea_id:'idea_'+'e'.repeat(32)}),result('other'),
    result(completion,{write_state:'committed_uncertain'})]) {
    const h=harness(url=>response(url.includes('/requests/')?receipt:{ok:true,code:'ok',idea_id:idea}));
    await assert.rejects(h.api.reconcileUploadBytes(upload,idea),error=>error.uncertain===true);
    assert.equal(h.calls.length,2);assert.ok(h.calls.every(call=>call.options.method==='GET'));
  }
  const h=harness(url=>response(url.includes('/requests/')?result('upload-intent-1',{completion_request_id:'wrong'}):{ok:true,code:'ok',idea_id:idea}));
  await assert.rejects(h.api.reconcileUpload('upload-intent-1',idea),rejected('invalid_response',true));
});

test('explicit byte retry retains upload identity; server replay and changed-byte refusal remain authoritative',async()=>{
  let attempt=0;
  const h=harness(()=>++attempt===1?response(result(completion,{write_state:'no_op'})):response({ok:false,code:'request_conflict',write_state:'not_applied'},409));
  const bytes=new Blob(['hello']);assert.equal((await h.api.uploadBytes(upload,bytes)).write_state,'no_op');
  await assert.rejects(h.api.uploadBytes(upload,new Blob(['changed'])),rejected('request_conflict',false));
  assert.equal(h.calls.length,2);assert.equal(h.calls[0].url,h.calls[1].url);
});

// the fetch fixture obeys the actual supplied AbortSignal, with no retry.
function delayedFetch(reply, delay, calls) {
  return (url, options) => new Promise((resolve, reject) => {
    calls.push({url, options});
    const signal = options.signal;
    const aborted = () => { clearTimeout(timer); reject(new Error('private transfer abort')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', aborted); resolve(reply); }, delay);
    signal.addEventListener('abort', aborted, {once: true});
    if (signal.aborted) aborted();
  });
}

test('byte timeout defaults independently and rejects unrepresentable timer budgets before fetch',()=>{
  const fetcher = () => assert.fail('invalid timeout must not fetch');
  const api = new IdeaApi(fetcher, undefined, binding);
  assert.equal(api.timeout, 15000); assert.equal(api.transferTimeout, 300000);
  for (const budget of [0, -1, 1.5, NaN, Infinity, '300000', null, true, 2147483648]) {
    assert.throws(() => new IdeaApi(fetcher, 10, binding, budget), rejected('invalid_timeout', false));
  }
});

test('binary upload may exceed JSON budget within its independent transfer budget',async()=>{
  const calls = [], bytes = new Blob(['hello']);
  const api = new IdeaApi(delayedFetch(response(result()), 40, calls), 10, binding, 200);
  api.csrf = 'test-only-csrf';
  const receipt = await api.uploadBytes(upload, bytes);
  assert.equal(receipt.asset_id, asset); assert.equal(receipt.request_id, completion);
  assert.equal(calls.length, 1); assert.equal(calls[0].options.body, bytes);
  assert.equal(calls[0].options.signal.aborted, false);
});

test('byte budget expiration aborts the upload and remains uncertain without automatic retry',async()=>{
  const calls = [];
  const api = new IdeaApi(delayedFetch(response(result()), 100, calls), 1000, binding, 10);
  api.csrf = 'test-only-csrf';
  await assert.rejects(api.uploadBytes(upload, new Blob(['hello'])), error =>
    rejected('connection_lost', true)(error) && Object.keys(error.data).length === 0 && !error.message.includes('private'));
  assert.equal(calls.length, 1); assert.equal(calls[0].options.signal.aborted, true);
  assert.equal(calls[0].url, '/api/v1/uploads/' + upload + '/bytes');
});

test('metadata and state retain the JSON timeout even with a longer transfer budget',async()=>{
  for (const writing of [false, true]) {
    const calls = [];
    const reply = writing ? result('upload-intent-1', {completion_request_id: completion}) : {ok:true,code:'ok'};
    const api = new IdeaApi(delayedFetch(response(reply), 100, calls), 10, binding, 200);
    api.csrf = 'test-only-csrf';
    await assert.rejects(writing ? api.uploadMetadata(metadata()) : api.state(idea), rejected('connection_lost', writing));
    assert.equal(calls.length, 1); assert.equal(calls[0].options.signal.aborted, true);
    assert.equal(calls[0].options.method, writing ? 'POST' : 'GET');
  }
});
