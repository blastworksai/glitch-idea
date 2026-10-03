// Fixed CP4 transport fixtures; no server, browser or native proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = await readFile(new URL('../../glitch-idea/web/api.js', import.meta.url), 'utf8');
const {IdeaApi, ApiError, validateHandoff} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const binding = 'binding_' + 'a'.repeat(32), session = 'session_' + 'b'.repeat(32);
const idea = 'idea_' + 'c'.repeat(32), otherIdea = 'idea_' + 'd'.repeat(32);
const handoff = 'handoff_' + 'e'.repeat(32), hash = 'f'.repeat(64), digest = '1'.repeat(64);
const root = '/fixture with spaces/💡';
const payload = () => ({request_id:'handoff:fixture-1',idea_id:idea,expected_revision:7,expected_draft_version:9,expected_backlog_revision:2});
const packet = (host = root) => ({handoff_id:handoff,source_revision:7,source_digest:digest,
  path:host+'/history/'+idea+'/metadata/'+hash+'.md',sha256:hash,prompt:'/glitch-plan\nLiteral <script>💡</script>\n',
  source_files:{detail:{path:host+'/'+idea+'.md',sha256:digest},index:{path:host+'/IDEAS.md',sha256:digest},
    revision:{path:host+'/history/'+idea+'/r7.md',sha256:digest}},design_set:null});
const receipt = (extra = {}) => ({ok:true,code:'ok',request_id:payload().request_id,write_state:'applied',idea_id:idea,
  revision:7,draft_version:9,backlog_revision:2,handoff_index:1,handoff_id:handoff,
  path:'history/'+idea+'/metadata/'+hash+'.md',sha256:hash,handoff:packet(),handoff_current:true,...extra});
const state = (extra = {}) => ({ok:true,code:'ok',session_id:session,idea_id:idea,...extra});
const row = (extra = {}) => ({idea_id:idea,revision:7,position:1,title:'Literal <script>💡</script>',
  status:'ready-to-plan',method:'appetite-led',updated:'2026-10-02T05:00:00+00:00',detail_path:root+'/'+idea+'.md',...extra});
const ideas = (rows = [row()]) => ({ok:true,code:'ok',backlog_revision:2,total:rows.length,ideas:rows});
const selection = (id = idea, extra = {}) => ({ok:true,code:'ok',session_id:session,idea_id:id,
  revision:id===null?0:7,draft_version:id===null?0:9,backlog_revision:2,...extra});
const response = (body, status = 200) => ({ok:status>=200&&status<300,status,json:async()=>structuredClone(body)});
const rejected = (code, uncertain = false) => error => error instanceof ApiError && error.code===code && error.uncertain===uncertain;
function harness(reply = () => response(receipt())) {
  const calls=[],api=new IdeaApi(async(url,options)=>{calls.push({url,options});return reply(url,options);},1000,binding);
  api.sessionId=session;api.csrf='test-only-csrf';return {api,calls};
}

test('handoff sends exact pinned authenticated payload with no retry or mutation',async()=>{
  const h=harness(),submitted=payload(),result=await h.api.handoff(submitted);
  assert.deepEqual(submitted,payload());assert.deepEqual(result,receipt());assert.equal(h.calls.length,1);
  const {url,options}=h.calls[0];assert.equal(url,'/api/v1/handoff');assert.equal(options.method,'POST');
  assert.deepEqual(JSON.parse(options.body),submitted);assert.equal(options.credentials,'same-origin');
  assert.equal(options.cache,'no-store');assert.equal(options.redirect,'error');
  assert.equal(options.headers['X-Idea-Binding'],binding);assert.equal(options.headers['X-CSRF-Token'],'test-only-csrf');
  assert.ok(options.signal instanceof AbortSignal);
});

test('invalid exact handoff payload and private selection identity refuse before fetch',async()=>{
  const h=harness();
  for(const delta of [{path:'/outside'},{request_id:'../file'},{request_id:'valid\n'},{idea_id:idea+'\n'},{idea_id:null},{expected_revision:0},
    {expected_revision:true},{expected_draft_version:-1},{expected_backlog_revision:1.5},{expected_revision:10**12+1}]) {
    await assert.rejects(h.api.handoff({...payload(),...delta}),rejected('invalid_handoff'));
  }
  const missing=payload();delete missing.expected_draft_version;
  await assert.rejects(h.api.handoff(missing),rejected('invalid_handoff'));
  for(const id of [undefined,{},'../file','idea_bad']) await assert.rejects(h.api.selection(id),rejected('invalid_selection'));
  await assert.rejects(h.api.reconcileHandoff('wrong',idea,payload()),rejected('invalid_handoff'));
  assert.equal(h.calls.length,0);
});

test('new helpers require binding and pinned durable session; writes require csrf',async()=>{
  for(const field of ['bindingId','sessionId']) {
    const h=harness();h.api[field]=null;
    for(const call of [()=>h.api.handoff(payload()),()=>h.api.ideas(),()=>h.api.selection(null),
      ()=>h.api.reconcileHandoff(payload().request_id,idea,payload())]) await assert.rejects(call(),rejected('browser_unauthorized'));
    assert.equal(h.calls.length,0);
  }
  const h=harness();h.api.csrf=null;
  await assert.rejects(h.api.handoff(payload()),rejected('browser_unauthorized'));
  await assert.rejects(h.api.selection(null),rejected('browser_unauthorized'));assert.equal(h.calls.length,0);
});

test('current/reused and historical reconstructed replies preserve requested packet identity',async()=>{
  for(const extra of [{write_state:'no_op'},{code:'historical_handoff',handoff_current:false}]) {
    const h=harness(()=>response(receipt(extra))),result=await h.api.handoff(payload());
    assert.deepEqual(result,receipt(extra));assert.equal(h.calls.length,1);
  }
  for(const delta of [{handoff_current:false},{code:'historical_handoff'},{handoff_current:'true'},
    {request_id:'wrong'},{idea_id:otherIdea},{revision:8},{draft_version:10},{backlog_revision:3},
    {handoff_id:'handoff_'+'2'.repeat(32)},{sha256:digest},{path:packet().path},{handoff_index:0},
    {handoff_index:129},{write_state:'not_applied'},{unexpected:true},{handoff:undefined}]) {
    const h=harness(()=>response(receipt(delta)));
    await assert.rejects(h.api.handoff(payload()),rejected('invalid_response',true));assert.equal(h.calls.length,1);
  }
  const compact=receipt();delete compact.handoff;delete compact.handoff_current;
  await assert.rejects(harness(()=>response(compact)).api.handoff(payload()),rejected('invalid_response',true));
});

test('public validation returns detached evidence with strict hashes and generated paths',()=>{
  const original=packet(),detached=validateHandoff(original,idea);assert.deepEqual(detached,original);
  detached.source_files.detail.sha256=hash;assert.equal(original.source_files.detail.sha256,digest);
  assert.deepEqual(harness().api.validateHandoff(original),original);
  for(const host of ['C:/Fixture with spaces/💡','\\\\server\\share\\Fixture',root+'\nline']) {
    assert.deepEqual(validateHandoff(packet(host),idea),packet(host));
  }
  for(const delta of [{source_digest:'x'.repeat(64)},{sha256:hash+'\n'},{sha256:hash.toUpperCase()},{handoff_id:'other'},
    {source_revision:true},{path:'/another/'+hash+'.md'},{prompt:'bad\ud800'},{prompt:''},{extra:'value'}]) {
    assert.throws(()=>validateHandoff({...packet(),...delta},idea),rejected('invalid_response'));
  }
  for(const file of ['detail','index','revision']) {
    const p=packet();p.source_files[file].path='/wrong/file';assert.throws(()=>validateHandoff(p,idea),rejected('invalid_response'));
  }
  assert.throws(()=>validateHandoff(packet(root+'/../escape'),idea),rejected('invalid_response'));
  assert.throws(()=>validateHandoff(packet(),otherIdea),rejected('invalid_response'));
  const p=packet();p.source_files.detail.extra='bad';assert.throws(()=>validateHandoff(p),rejected('invalid_response'));
});

test('design evidence validates exact ordered members, native paths, MIME and capacity',()=>{
  const asset='asset_'+'3'.repeat(32),member={asset_id:asset,name:'../../literal💡.md',type:'text/markdown',
    size:5,sha256:hash,path:root+'/assets/blobs/'+asset+'.bin'};
  const p=packet();p.design_set={set_id:'set_'+'4'.repeat(32),members:[member]};
  assert.deepEqual(validateHandoff(p),p);
  for(const delta of [{asset_id:'bad'},{path:'/outside/assets/blobs/'+asset+'.bin'},{size:0},{size:25*1024*1024+1},
    {type:'application/javascript'},{name:'wrong.png'},{sha256:'bad'},{extra:true}]) {
    const changed=structuredClone(p);Object.assign(changed.design_set.members[0],delta);
    assert.throws(()=>validateHandoff(changed),rejected('invalid_response'));
  }
  for(const members of [[],[member,member],Array(21).fill(member)]) {
    assert.throws(()=>validateHandoff({...p,design_set:{...p.design_set,members}}),rejected('invalid_response'));
  }
  const huge=packet();huge.prompt='💡'.repeat(300000);assert.throws(()=>validateHandoff(huge),rejected('invalid_response'));
});

test('lost/malformed handoff acknowledgement stays uncertain without implicit retry',async()=>{
  for(const reply of [()=>{throw new Error('private diagnostic');},
    ()=>({ok:true,status:200,json:async()=>{throw new Error('bad JSON');}}),
    ()=>response(receipt({write_state:'committed_uncertain',committed:true}))]) {
    const h=harness(reply);await assert.rejects(h.api.handoff(payload()),error=>error.uncertain===true);
    assert.equal(h.calls.length,1);assert.deepEqual(JSON.parse(h.calls[0].options.body),payload());
  }
  const h=harness(()=>response({ok:false,code:'browser_unauthorized'},401));
  await assert.rejects(h.api.handoff(payload()),rejected('browser_unauthorized'));
  assert.equal(h.api.csrf,null);assert.equal(h.api.bindingId,binding);assert.equal(h.api.sessionId,session);
});

test('typed reconciliation validates exact receipt before selected-state read',async()=>{
  for(const delta of [{request_id:'wrong'},{idea_id:otherIdea},{revision:8},{draft_version:10},
    {backlog_revision:3},{handoff:undefined},{write_state:'committed_uncertain'}]) {
    const h=harness(url=>{assert.ok(url.includes('/requests/'));return response(receipt(delta));});
    await assert.rejects(h.api.reconcileHandoff(payload().request_id,idea,payload()),error=>error.uncertain===true);
    assert.equal(h.calls.length,1);assert.equal(h.calls[0].options.method,'GET');
  }
});

test('historical reconciliation preserves receipt packet and separately reads latest state',async()=>{
  const historical=receipt({handoff_current:false,code:'historical_handoff'}),newer=packet();
  newer.handoff_id='handoff_'+'5'.repeat(32);
  const h=harness(url=>response(url.includes('/requests/')?historical:state({handoff:newer})));
  const resolved=await h.api.reconcileHandoff(payload().request_id,idea,payload());
  assert.equal(resolved.result.handoff.handoff_id,handoff);assert.equal(resolved.result.handoff_current,false);
  assert.equal(resolved.state.handoff.handoff_id,newer.handoff_id);
  assert.deepEqual(h.calls.map(call=>call.url),['/api/v1/requests/'+encodeURIComponent(payload().request_id),'/api/v1/state?idea_id='+idea]);
  assert.ok(h.calls.every(call=>call.options.method==='GET'));
});

test('missing receipt reads expected state before retry is offered and performs no write',async()=>{
  const h=harness(url=>url.includes('/requests/')?response({ok:false,code:'request_not_found'},404):response(state()));
  const resolved=await h.api.reconcileHandoff(payload().request_id,idea,payload());
  assert.equal(resolved.result,null);assert.equal(resolved.state.idea_id,idea);
  assert.equal(h.calls.length,2);assert.ok(h.calls.every(call=>call.options.method==='GET'));
  assert.equal(h.calls[1].url,'/api/v1/state?idea_id='+idea);
  for(const delta of [{session_id:'session_'+'6'.repeat(32)},{idea_id:otherIdea}]) {
    const wrong=harness(url=>response(url.includes('/requests/')?receipt():state(delta)));
    await assert.rejects(wrong.api.reconcileHandoff(payload().request_id,idea,payload()),rejected('invalid_response',true));
  }
});

test('Ideas fixed GET preserves complete actual order, statuses and literal Unicode titles',async()=>{
  const rows=[row({title:'💡'.repeat(200)}),row({idea_id:otherIdea,position:2,status:'archived',method:null,detail_path:root+'/'+otherIdea+'.md'})];
  const h=harness(()=>response(ideas(rows)));assert.deepEqual(await h.api.ideas(),ideas(rows));
  assert.equal(h.calls.length,1);assert.equal(h.calls[0].url,'/api/v1/ideas');assert.equal(h.calls[0].options.method,'GET');
  assert.equal(h.calls[0].options.body,undefined);assert.equal(h.calls[0].options.headers['X-CSRF-Token'],undefined);
  const empty=harness(()=>response(ideas([])));assert.deepEqual(await empty.api.ideas(),ideas([]));
});

test('Ideas rejects partial tails, duplicate IDs, invented statuses and malformed bounded rows',async()=>{
  for(const delta of [{title:'💡'.repeat(201)},{title:'bad\ud800'},{status:'ready'},{method:'unknown'},
    {revision:true},{position:2},{updated:null},{detail_path:'relative/'+idea+'.md'},{extra:1}]) {
    await assert.rejects(harness(()=>response(ideas([row(delta)]))).api.ideas(),rejected('invalid_response'));
  }
  for(const data of [{...ideas(),total:2},{...ideas(),backlog_revision:-1},{...ideas(),extra:true},
    ideas([row(),row({position:2})]),{...ideas(),ideas:[]},ideas([row({title:'a'.repeat(200),updated:'x'.repeat(201)})])]) {
    await assert.rejects(harness(()=>response(data)).api.ideas(),rejected('invalid_response'));
  }
  const h=harness(()=>response({ok:false,code:'ideas_capacity'},400));
  await assert.rejects(h.api.ideas(),rejected('ideas_capacity'));assert.equal(h.calls.length,1);
});

test('selection sends only explicit saved ID/null and accepts exact pinned seven-field reply',async()=>{
  for(const id of [idea,null]) {
    const h=harness(()=>response(selection(id)));assert.deepEqual(await h.api.selection(id),selection(id));
    assert.equal(h.calls.length,1);assert.equal(h.calls[0].url,'/api/v1/selection');
    assert.deepEqual(JSON.parse(h.calls[0].options.body),{idea_id:id});assert.equal(h.calls[0].options.method,'POST');
  }
  for(const delta of [{session_id:'session_'+'7'.repeat(32)},{idea_id:otherIdea},{revision:0},
    {draft_version:true},{backlog_revision:-1},{request_id:'not-a-receipt'},{write_state:'no_op'}]) {
    await assert.rejects(harness(()=>response(selection(idea,delta))).api.selection(idea),rejected('invalid_response',true));
  }
  for(const delta of [{revision:1},{draft_version:1}]) {
    await assert.rejects(harness(()=>response(selection(null,delta))).api.selection(null),rejected('invalid_response',true));
  }
});

test('selection persistence failure or lost reply never changes client identity or retries',async()=>{
  const h=harness(()=>response({ok:false,code:'session_persistence_failed'},500));
  await assert.rejects(h.api.selection(null),rejected('session_persistence_failed'));assert.equal(h.calls.length,1);
  assert.equal(h.api.sessionId,session);assert.equal(h.api.bindingId,binding);
  const lost=harness(()=>{throw new Error('lost');});await assert.rejects(lost.api.selection(null),rejected('connection_lost',true));
  assert.equal(lost.calls.length,1);assert.equal(lost.api.sessionId,session);
});

// Explicit promises reproduce response ordering, not wall-clock timing.
function deferred() {
  let resolve; const promise=new Promise(done=>{resolve=done;});return {promise,resolve};
}
const unauthorized = () => response({ok:false,code:'browser_unauthorized'},401);

test('late old-state 401 after selection success cannot clear current csrf or prevent next mutation',async()=>{
  const old=deferred();let reads=0;
  const h=harness(url=>url.includes('/state')?(++reads===1?old.promise:response(state({idea_id:null}))):response(selection(null)));
  const pending=assert.rejects(h.api.state(idea),rejected('browser_unauthorized'));
  await h.api.selection(null);await h.api.state();
  old.resolve(unauthorized());await pending;
  assert.equal(h.api.csrf,'test-only-csrf');assert.equal(h.api.bindingId,binding);assert.equal(h.api.sessionId,session);
  await h.api.selection(null);
  assert.equal(h.calls.at(-1).options.headers['X-CSRF-Token'],'test-only-csrf');
  assert.equal(h.calls.length,4);
});

test('selection refusal advances scope; its own and subsequent live 401 still clear csrf while 403 does not',async()=>{
  const old=deferred();let selecting=0;
  const h=harness(url=>url.includes('/state')?old.promise:
    ++selecting===1?response({ok:false,code:'session_persistence_failed'},500):unauthorized());
  const pending=assert.rejects(h.api.state(idea),rejected('browser_unauthorized'));
  await assert.rejects(h.api.selection(null),rejected('session_persistence_failed'));
  old.resolve(unauthorized());await pending;assert.equal(h.api.csrf,'test-only-csrf');
  await assert.rejects(h.api.selection(null),rejected('browser_unauthorized'));assert.equal(h.api.csrf,null);
  for(const status of [401,403]) {
    const current=harness(()=>response({ok:false,code:status===401?'browser_unauthorized':'wrong_csrf'},status));
    await assert.rejects(current.api.state(idea),rejected(status===401?'browser_unauthorized':'wrong_csrf'));
    assert.equal(current.api.csrf,status===401?null:'test-only-csrf');
  }
});

test('successful session credential installation supersedes an older pending 401',async()=>{
  const old=deferred();
  const h=harness(url=>url.includes('/state')?old.promise:response({ok:true,code:'ok',binding_id:binding,
    session_id:session,csrf_token:'new-test-only-csrf'}));
  const pending=assert.rejects(h.api.state(idea),rejected('browser_unauthorized'));
  await h.api.session();old.resolve(unauthorized());await pending;
  assert.equal(h.api.csrf,'new-test-only-csrf');assert.equal(h.api.sessionId,session);
});

test('attachment and binary upload late 401 use the same selection scope guard; live 401 still clears',async()=>{
  const asset='asset_'+'8'.repeat(32),upload='upload_'+'9'.repeat(32);
  const attachment401=()=>{
    const bytes=new TextEncoder().encode(JSON.stringify({ok:false,code:'browser_unauthorized'}));
    return {ok:false,status:401,headers:{get:name=>name==='Content-Length'?String(bytes.length):name==='Content-Type'?'application/json':null},
      body:new ReadableStream({start(controller){controller.enqueue(bytes);controller.close();}})};
  };
  for(const kind of ['attachment','upload']) {
    const old=deferred(),call=api=>kind==='attachment'?api.attachment(asset,5):api.uploadBytes(upload,new Blob(['hello']));
    const failure=()=>kind==='attachment'?attachment401():unauthorized();
    const h=harness(url=>url.endsWith('/selection')?response(selection(null)):old.promise);
    const pending=assert.rejects(call(h.api),rejected('browser_unauthorized'));
    await h.api.selection(null);old.resolve(failure());await pending;
    assert.equal(h.api.csrf,'test-only-csrf');assert.equal(h.calls.length,2);
    const current=harness(failure);await assert.rejects(call(current.api),rejected('browser_unauthorized'));
    assert.equal(current.api.csrf,null);assert.equal(current.calls.length,1);
  }
});
