// real IdeaApi/controller modules with explicit fetch protocol emulation.
// These tests do not claim native browser cookie, DOM or two-port acceptance.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web = new URL('../../glitch-idea/web/', import.meta.url);
const source = async name => await readFile(new URL(name, web), 'utf8');
const dataUrl = value => 'data:text/javascript;base64,' + Buffer.from(value).toString('base64');
const apiUrl = dataUrl(await source('api.js'));
const foldsUrl = dataUrl(await source('folds.js'));
const {IdeaApi} = await import(apiUrl);
const {Flow, STEPS} = await import(foldsUrl);
const appSource = (await source('app.js')).replace("'./api.js'", JSON.stringify(apiUrl)).replace("'./folds.js'", JSON.stringify(foldsUrl));
const {browserSelection, selectedIdea, selectedBinding, rememberSelection, startApp} = await import(dataUrl(appSource));
const A = 'binding_' + 'a'.repeat(32), B = 'binding_' + 'b'.repeat(32);
const SA = 'session_' + 'a'.repeat(32), SB = 'session_' + 'b'.repeat(32);
const IDEA = 'idea_' + '1'.repeat(32);
const META = (binding=A, session=SA) => ({ok:true,code:'ok',binding_id:binding,session_id:session,csrf_token:'test-only-csrf',tab_secret:'test-only-tab-'+binding});
const response = (body,status=200) => ({ok:status>=200&&status<300,status,json:async()=>structuredClone(body)});
const reject = code => error => error.code===code;

function memoryStorage() {
  const map = new Map();
  return {map, getItem: k => map.has(k) ? map.get(k) : null, setItem: (k, v) => map.set(k, String(v)), removeItem: k => map.delete(k)};
}

function emulatedServer(namespace, cookieJar = new Map()) {
  const sessions = new Map([[A,SA],[B,SB]]), codes = new Map([['code-a',A],['code-b',B]]);
  const receipts = new Map(), calls = [];
  let online = true, captures = 0, loseCapture = false;
  const state = binding => ({ok:true,code:'ok',session_id:sessions.get(binding),idea_id:receipts.has(binding)?IDEA:null,
    revision:receipts.has(binding)?1:0,draft_version:0,backlog_revision:receipts.has(binding)?1:0,
    current_step:receipts.has(binding)?'priorities':'capture',steps:Object.fromEntries(STEPS.map(({key})=>[key,
      {status:key==='capture'?(receipts.has(binding)?'saved':'current'):'todo',accepted_revision:key==='capture'&&receipts.has(binding)?1:null,
       evidence_id:key==='capture'&&receipts.has(binding)?'fixture-capture':null}])),accepted:{},drafts:{},draft:null});
  const fetcher = async (url,options) => {
    calls.push({url,options});
    if (!online) throw new Error('offline');
    const selector=options.headers['X-Idea-Binding'];
    const body=options.body===undefined?null:JSON.parse(options.body);
    if(url.endsWith('/pair')) {
      const binding=codes.get(body.code);
      if(selector&&selector!==binding) return response({ok:false,code:'session_binding_mismatch'},403);
      if(!binding) return response({ok:false,code:'wrong_pairing_code'},401);
      codes.delete(body.code);
      cookieJar.set(namespace+':'+binding,true);
      return response(META(binding,sessions.get(binding)));
    }
    if(!selector||!cookieJar.has(namespace+':'+selector)||options.headers['X-Idea-Tab']!=='test-only-tab-'+selector) return response({ok:false,code:'browser_unauthorized'},401);
    if(url.endsWith('/session')) return response(META(selector,sessions.get(selector)));
    if(options.method==='POST'&&options.headers['X-CSRF-Token']!=='test-only-csrf') return response({ok:false,code:'wrong_csrf'},403);
    if(url.endsWith('/capture')) {
      const prior=receipts.get(selector);
      if(prior&&prior.request_id!==body.request_id) throw new Error('Unexpected duplicate capture');
      if(!prior) { captures++; receipts.set(selector,{ok:true,code:'ok',request_id:body.request_id,write_state:'applied',idea_id:IDEA,revision:1,draft_version:0,backlog_revision:1}); }
      if(loseCapture) { online=false; throw new Error('lost response'); }
      return response(receipts.get(selector));
    }
    if(url.includes('/requests/')) return receipts.has(selector)?response(receipts.get(selector)):response({ok:false,code:'request_not_found'},404);
    if(url.includes('/state')) return response(state(selector));
    return response({ok:true,code:'ok'});
  };
  return {fetcher,calls,codes,receipts,state,setOnline:value=>online=value,setLoseCapture:value=>loseCapture=value,get captures(){return captures;}};
}

test('fresh root never adopts ambient cookie and explicit pair pins both identities',async()=>{
  const server=emulatedServer('one');
  const first=new IdeaApi(server.fetcher); await first.pair('code-a');
  const fresh=new IdeaApi(server.fetcher);
  const calls=server.calls.length;
  await assert.rejects(fresh.session(),reject('browser_unauthorized'));
  assert.equal(server.calls.length,calls);
  await fresh.pair('code-b');
  assert.equal(first.bindingId,A); assert.equal(first.sessionId,SA);
  assert.equal(fresh.bindingId,B); assert.equal(fresh.sessionId,SB);
  assert.equal(server.calls[0].options.headers['X-Idea-Binding'],undefined);
  assert.equal(server.calls[1].options.headers['X-Idea-Binding'],A);
});

test('pinned pair rejects another binding before consuming its code or replacing identity',async()=>{
  const server=emulatedServer('one'); const api=new IdeaApi(server.fetcher); await api.pair('code-a');
  await assert.rejects(api.pair('code-b'),reject('session_binding_mismatch'));
  assert.equal(api.bindingId,A); assert.equal(api.sessionId,SA); assert.equal(api.csrf,null);
  assert.equal(server.codes.get('code-b'),B);
  await new IdeaApi(server.fetcher).pair('code-b');
  await api.session(); assert.equal(api.sessionId,SA);
});

test('wrong pair/session metadata cannot replace pinned durable identity',async()=>{
  let body=META(); const api=new IdeaApi(async()=>response(body)); await api.pair('fixture');
  for(const data of [META(B,SB),META(A,SB)]) {
    body=data; await assert.rejects(api.session(),reject('session_binding_mismatch'));
    assert.equal(api.bindingId,A); assert.equal(api.sessionId,SA); assert.equal(api.csrf,null);
    await assert.rejects(api.pair('different'),reject('session_binding_mismatch'));
  }
  body={...META(),binding_id:'invalid'};
  await assert.rejects(api.session(),reject('invalid_session'));
  assert.equal(api.bindingId,A); assert.equal(api.sessionId,SA);
});

test('401/restart retains binding and durable SID while requiring fresh CSRF pairing',async()=>{
  const requests=[]; let unauthorized=false;
  const api=new IdeaApi(async(url,options)=>{requests.push({url,options});return unauthorized?response({ok:false,code:'browser_unauthorized'},401):response(META());});
  await api.pair('fixture'); unauthorized=true;
  await assert.rejects(api.session(),reject('browser_unauthorized'));
  assert.equal(api.bindingId,A); assert.equal(api.sessionId,SA); assert.equal(api.csrf,null);
  unauthorized=false; await api.pair('fresh');
  assert.equal(requests.at(-2).options.headers['X-Idea-Binding'],A);
  assert.equal(api.sessionId,SA);
});

test('pending lost Capture stays in original namespace when another tab pairs',async()=>{
  const sharedCookies=new Map(); const server=emulatedServer('port-one',sharedCookies);
  const api=new IdeaApi(server.fetcher); await api.pair('code-a');
  const flow=new Flow(api,()=> 'capture-fixed'); flow.load(await api.state());
  const fields={raw_text:'Exact fixture words\r\n',workspace:{name:'Fixture',path:'/fixture',confirmed:true}};
  flow.edit('capture',fields); server.setLoseCapture(true);
  assert.equal(await flow.save('capture'),false); const pending=structuredClone(flow.pending);
  server.setOnline(true); server.setLoseCapture(false);
  const other=new IdeaApi(server.fetcher); await other.pair('code-b');
  assert.deepEqual(flow.pending,pending); assert.deepEqual(flow.buffers.capture,fields);
  assert.equal(await flow.retry(),true); assert.equal(server.captures,1);
  assert.equal(api.bindingId,A); assert.equal(api.sessionId,SA); assert.equal(flow.state.session_id,SA);
  assert.equal(server.receipts.has(B),false);
  const reconciliation=server.calls.filter(call=>call.url.includes('/requests/'));
  assert.ok(reconciliation.every(call=>call.options.headers['X-Idea-Binding']===A));
});

test('shared cookie jar emulates distinct store ports without changing selectors',async()=>{
  const jar=new Map(), one=emulatedServer('port-one',jar), two=emulatedServer('port-two',jar);
  const first=new IdeaApi(one.fetcher), second=new IdeaApi(two.fetcher);
  await first.pair('code-a'); await second.pair('code-b');
  assert.equal((await first.session()).session_id,SA); assert.equal((await second.session()).session_id,SB);
  assert.equal(jar.size,2);
});

test('binding and selected idea coexist with strict duplicate/type/query rejection',()=>{
  const location={href:'http://127.0.0.1:1234/?binding='+A+'&idea_id='+IDEA,search:'?binding='+A+'&idea_id='+IDEA};
  assert.deepEqual(browserSelection(location),{binding:A,idea_id:IDEA});
  assert.equal(selectedIdea(location),IDEA); assert.equal(selectedBinding(location),A);
  assert.deepEqual(browserSelection({search:''}),{binding:null,idea_id:null});
  for(const search of ['?binding='+A+'&binding='+A,'?binding=bad','?idea_id='+IDEA+'&idea_id='+IDEA,'?idea_id=bad','?csrf_token=never']) {
    assert.throws(()=>browserSelection({search}));
  }
  let written; const history={replaceState:(data,title,url)=>{written=url;}};
  rememberSelection(A,IDEA,location,history);
  assert.equal(written,'/?binding='+A+'&idea_id='+IDEA);
  assert.doesNotMatch(written,/csrf|token|session_/);
  assert.throws(()=>rememberSelection('bad',IDEA,location,history));
});

test('app pins URL selector and preserves pending on re-pair state reload without persistent secrets',()=>{
  assert.match(appSource,/api\.pinBinding\(initialBinding\)/);
  assert.match(appSource,/Boolean\(flow\.pending\) \|\| flow\.dirty\.size > 0/);
  assert.doesNotMatch(appSource,/localStorage|sessionStorage/);
});

test('actual startApp render preserves binding after Capture and reload restores its session',async()=>{
  class Node {
    constructor(tag='div') { this.tagName=tag.toUpperCase(); this.dataset={}; this.children=[]; this.listeners={}; this.value=''; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children=nodes; }
    setAttribute(name,value) { this[name]=value; }
    addEventListener(name,callback) { this.listeners[name]=callback; }
    focus() {}
  }
  const names=['document','location','history','addEventListener'];
  const saved=new Map(names.map(name=>[name,Object.getOwnPropertyDescriptor(globalThis,name)]));
  const server=emulatedServer('render'); const tabs=memoryStorage(); const api=new IdeaApi(server.fetcher,15000,null,300000,tabs); await api.pair('code-a');
  let currentUrl=new URL('http://127.0.0.1:1234/?binding='+A);
  const writes=[]; const nodes=new Map();
  globalThis.document={activeElement:null,createElement:tag=>new Node(tag),getElementById:id=>{
    if(!nodes.has(id)) nodes.set(id,new Node()); return nodes.get(id);
  }};
  Object.defineProperty(globalThis,'location',{configurable:true,get:()=>currentUrl});
  globalThis.history={replaceState:(_state,_title,url)=>{writes.push(url);currentUrl=new URL(url,currentUrl);}};
  globalThis.addEventListener=()=>{};
  try {
    const flow=startApp(api);
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(flow.state.idea_id,null);
    flow.edit('capture',{raw_text:'Capture render fixture',workspace:{name:'Fixture',path:'/fixture',confirmed:true}});
    const findCapture=node=>node.id==='capture-accept'?node:node.children.map(findCapture).find(Boolean);
    const accept=[...nodes.values()].map(findCapture).find(Boolean);
    assert.ok(accept);assert.equal(accept.disabled,false);
    await accept.listeners.click();assert.equal(flow.pending,null);assert.equal(flow.status('capture'),'saved');
    assert.equal(currentUrl.searchParams.get('binding'),A);
    assert.equal(currentUrl.searchParams.get('idea_id'),IDEA);
    assert.ok(writes.filter(url=>url.includes('idea_id')).every(url=>new URL(url,currentUrl).searchParams.get('binding')===A));
    const reloadedApi=new IdeaApi(server.fetcher,15000,null,300000,tabs);
    const reloaded=startApp(reloadedApi);
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(reloadedApi.bindingId,A); assert.equal(reloadedApi.sessionId,SA);
    assert.equal(reloaded.state.idea_id,IDEA);
    assert.equal(server.captures,1);
  } finally {
    for(const [name,descriptor] of saved) {
      if(descriptor) Object.defineProperty(globalThis,name,descriptor); else delete globalThis[name];
    }
  }
});

// Real app error panel and public Flow operations, with deterministic API faults.
// These fixtures do not substitute for server/browser or native qualification.
async function diagnosticApp(run) {
  const names=['document','location','history','addEventListener','setTimeout','clearTimeout'];
  const saved=new Map(names.map(name=>[name,Object.getOwnPropertyDescriptor(globalThis,name)]));
  let doc,currentUrl=new URL('http://127.0.0.1:1234/?binding='+A+'&idea_id='+IDEA),timerId=0;
  const timers=new Map(),events={},writes=[],pings=[],faults={},receipts=new Map();
  const SET='set_'+'3'.repeat(32),OTHER_SET='set_'+'4'.repeat(32),ASSET='asset_'+'5'.repeat(32);
  class Node {
    constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.dataset={};this.disabled=false;this.value='';this.textContent='';this.scrollTop=0;this.scrollLeft=0;}
    append(...nodes){this.children.push(...nodes);}
    contains(node){return node===this||this.children.some(child=>child.contains(node));}
    replaceChildren(...nodes){if(this.children.some(child=>child.contains(doc.activeElement)))doc.activeElement=doc.body;this.children=nodes;}
    setAttribute(name,value){this[name]=value;}
    addEventListener(name,callback){this.listeners[name]=callback;}
    focus(){if(!this.disabled)doc.activeElement=this;}
    setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;}
    async click(){if(!this.disabled)return this.listeners.click?.({target:this});}
    input(value){if(!this.disabled&&!this.readOnly){this.value=value;return this.listeners.input?.({target:this});}}
    all(){return [this,...this.children.flatMap(child=>child.all())];}
    set innerHTML(value){throw new Error('HTML injection refused');}
  }
  const roots=new Map(['announcement','identity','progress','save-status','agent-status','compact-nav','columns','connection'].map(id=>{const node=new Node();node.id=id;return [id,node];}));
  const find=(node,id)=>node.id===id?node:node.children.map(child=>find(child,id)).find(Boolean);
  doc={body:new Node('body'),activeElement:null,hidden:true,createElement:tag=>new Node(tag),getElementById:id=>[...roots.values()].map(node=>find(node,id)).find(Boolean)??null};
  doc.activeElement=doc.body;globalThis.document=doc;
  Object.defineProperty(globalThis,'location',{configurable:true,get:()=>currentUrl});
  globalThis.history={replaceState:(_state,_title,url)=>{currentUrl=new URL(url,currentUrl);}};
  globalThis.addEventListener=(name,callback)=>{events[name]=callback;};
  globalThis.setTimeout=(callback,delay)=>{const timer={id:++timerId,unref(){}};timers.set(timer,{callback,delay});return timer;};
  globalThis.clearTimeout=timer=>timers.delete(timer);
  const state={ok:true,code:'ok',session_id:SA,idea_id:IDEA,idea_status:'active',revision:4,draft_version:0,backlog_revision:0,current_step:'visualize',agent_status:'disconnected',
    steps:Object.fromEntries(STEPS.map(({key},index)=>[key,{status:index<4?'saved':key==='visualize'?'current':'todo',accepted_revision:index<4?4:null,evidence_id:index<4?'fixture-'+key:null}])),
    accepted:{capture:{raw_text:'Preserved Capture',workspace:{name:'Explicit',path:'/fixture',confirmed:true}}},drafts:{},draft:null};
  const fields={disposition:'accepted_set',reason:null,design_set_id:null,brief_evidence_id:null};
  const clone=value=>structuredClone(value);
  const api={bindingId:A,pinBinding:binding=>assert.equal(binding,A),session:async()=>META(),state:async()=>clone(state),
    reconcile:async requestId=>{
      if(faults.reconcile)throw Object.assign(new Error('connection_lost'),{code:'connection_lost',uncertain:true});
      return {result:clone(receipts.get(requestId)??null),state:clone(state)};
    },write:async(operation,payload)=>{
      if(operation==='transport')return {ok:true,code:'ok'};
      if(operation==='activity'){pings.push(payload);return {ok:true,code:'ok'};}
      writes.push({operation,payload:clone(payload)});
      if(faults.code)throw Object.assign(new Error(faults.code),{code:faults.code,status:faults.status??409,uncertain:faults.uncertain===true});
      const result={ok:true,code:'ok',write_state:'applied',request_id:payload.request_id,idea_id:IDEA};
      if(operation==='visual-set/accept'){
        state.revision++;state.accepted.visualize={...clone(payload.fields),design_set_id:faults.historical?OTHER_SET:SET};
        state.steps.visualize={status:'saved',accepted_revision:state.revision,evidence_id:'fixture-visualize'};result.design_set_id=SET;
      }
      if(operation==='draft'){state.draft_version++;state.drafts[payload.step]=clone(payload.fields);}
      if(operation==='navigate')state.current_step=payload.step;
      receipts.set(payload.request_id,clone(result));
      return faults.invalidResponse?{...result,write_state:'invalid'}:result;
    }};
  let flow;
  try {
    flow=startApp(api);await new Promise(resolve=>setImmediate(resolve));
    await run({flow,state,faults,writes,pings,fields,ASSET,timers,get:id=>doc.getElementById(id),
      alerts:()=>[...roots.values()].flatMap(node=>node.all()).filter(node=>node.role==='alert').map(node=>node.all().map(child=>child.textContent).join('\n')).join('\n')});
  } finally {
    events.pagehide?.();flow?.dispose();
    for(const [name,descriptor]of saved)if(descriptor)Object.defineProperty(globalThis,name,descriptor);else delete globalThis[name];
  }
}

test('actual app describes a saved historical receipt truthfully and offers review reload',async()=>{
  await diagnosticApp(async h=>{
    h.faults.historical=true;h.flow.edit('visualize',h.fields);assert.equal(await h.flow.saveVisualize([h.ASSET]),false);
    assert.equal(h.flow.error.code,'saved_state_changed');assert.equal(h.flow.pending,null);assert.equal(h.flow.status('visualize'),'unsaved');
    assert.match(h.alerts(),/This request was saved/);assert.match(h.alerts(),/current saved state differs/);assert.match(h.alerts(),/review before saving again/);
    assert.doesNotMatch(h.alerts(),/Could not save/);assert.equal(h.get('retry-save'),null);assert.ok(h.get('reload-state'));
    await h.get('reload-state').click();assert.deepEqual(h.flow.buffers.visualize,h.fields);assert.equal(h.writes.length,1);
  });
});
test('actual app stale revision/draft/backlog/source CAS errors reload preserved answers without identical retry',async()=>{
  for(const code of ['stale_revision','stale_draft_version','stale_backlog','stale_source'])await diagnosticApp(async h=>{
    h.faults.code=code;h.flow.edit('visualize',h.fields);assert.equal(await h.flow.saveVisualize([h.ASSET]),false);
    assert.equal(h.flow.pending.ambiguous,false);assert.equal(h.get('retry-save'),null);assert.ok(h.get('reload-state'));assert.equal(h.writes.length,1);
    if(code==='stale_backlog'){assert.match(h.alerts(),/current order/);assert.match(h.alerts(),/fresh assessment/);}else assert.match(h.alerts(),/Reload current state/);
    const original=cloneForDiagnostic(h.flow.buffers.visualize);h.state.revision++;await h.get('reload-state').click();
    assert.equal(h.flow.pending,null);assert.equal(h.flow.error,null);assert.deepEqual(h.flow.buffers.visualize,original);assert.equal(h.flow.status('visualize'),'unsaved');assert.equal(h.writes.length,1);
  });
});
const cloneForDiagnostic=value=>structuredClone(value);
test('actual app invalid-response uncertainty retains Check save result and never labels the write unapplied',async()=>{
  await diagnosticApp(async h=>{
    h.faults.invalidResponse=true;h.faults.reconcile=true;h.flow.edit('visualize',h.fields);assert.equal(await h.flow.saveVisualize([h.ASSET]),false);
    assert.equal(h.flow.error.code,'invalid_response');assert.equal(h.flow.pending.ambiguous,true);assert.match(h.alerts(),/may have committed/);
    assert.doesNotMatch(h.alerts(),/Could not save/);assert.equal(h.get('retry-save').textContent,'Check save result');assert.equal(h.get('reload-state'),null);
    await h.get('retry-save').click();assert.equal(h.writes.length,1);assert.equal(h.flow.pending.ambiguous,true);
  });
});
test('ambiguous stale code still permits only result checking rather than an unsafe reload',async()=>{
  await diagnosticApp(async h=>{
    h.faults.code='stale_backlog';h.faults.uncertain=true;h.faults.reconcile=true;h.flow.edit('visualize',h.fields);await h.flow.saveVisualize([h.ASSET]);
    assert.equal(h.flow.pending.ambiguous,true);assert.equal(h.get('retry-save').textContent,'Check save result');assert.equal(h.get('reload-state'),null);
  });
});

test('actual app archive refusals explain explicit Shape reactivation and reload preserved answers without retry',async()=>{
  for(const code of ['archived_revision','idea_archived'])await diagnosticApp(async h=>{
    h.faults.code=code;h.flow.edit('visualize',h.fields);assert.equal(await h.flow.saveVisualize([h.ASSET]),false);
    const answers=cloneForDiagnostic(h.flow.buffers.visualize);
    h.state.idea_status='archived';
    assert.equal(h.flow.pending.ambiguous,false);assert.equal(h.get('retry-save'),null);assert.ok(h.get('reload-state'));
    assert.match(h.alerts(),/This idea is archived/);assert.match(h.alerts(),/explicitly redo and accept Shape/);
    assert.match(h.alerts(),/Archived history is kept/);assert.doesNotMatch(h.alerts(),/Could not save|Try again/);
    await h.get('reload-state').click();assert.deepEqual(h.flow.buffers.visualize,answers);
    assert.equal(h.flow.pending,null);assert.equal(h.flow.state.idea_status,'archived');assert.equal(h.writes.length,1);
    assert.equal(h.flow.canOpen('shape'),true);
  });
});

test('ambiguous archive errors retain Check save result rather than offering a reload or repeating a write',async()=>{
  for(const code of ['archived_revision','idea_archived'])await diagnosticApp(async h=>{
    h.faults.code=code;h.faults.uncertain=true;h.faults.reconcile=true;
    h.flow.edit('visualize',h.fields);await h.flow.saveVisualize([h.ASSET]);
    assert.equal(h.flow.pending.ambiguous,true);assert.equal(h.get('retry-save').textContent,'Check save result');assert.equal(h.get('reload-state'),null);
    await h.get('retry-save').click();assert.equal(h.writes.length,1);assert.equal(h.flow.pending.ambiguous,true);
  });
});
test('actual app Pause cancels a scheduled Capture autosave before saving draft and navigation',async()=>{
  await diagnosticApp(async h=>{
    await h.get('compact-capture').click();h.get('idea-text').input('Edited Capture');assert.ok([...h.timers.values()].some(timer=>timer.delay===3000));
    await h.get('pause-workflow').click();assert.equal(h.flow.paused,true);
    assert.deepEqual(h.writes.map(write=>write.operation),['draft','navigate']);
  });
});

test('typing pings the agent clock at most once per 30 s, only while a field is unsaved',async()=>{
  const realNow=Date.now;let clock=1_000_000;Date.now=()=>clock;
  try{
    await diagnosticApp(async h=>{
      await h.get('compact-capture').click();
      assert.equal(h.pings.length,0,'opening a step with nothing dirty sends no ping');
      const words=h.get('idea-text');
      words.input('a');await new Promise(r=>setImmediate(r));assert.equal(h.pings.length,1);assert.deepEqual(h.pings[0],{});
      for(const step of [1_000,10_000,29_999]){clock=1_000_000+step;words.input('more '+step);}
      await new Promise(r=>setImmediate(r));assert.equal(h.pings.length,1,'no second ping inside 30 s');
      clock=1_000_000+30_000;words.input('later');await new Promise(r=>setImmediate(r));assert.equal(h.pings.length,2);
      clock+=45_000;await new Promise(r=>setImmediate(r));assert.equal(h.pings.length,2,'an idle page sends nothing');
      assert.equal(h.writes.some(write=>write.operation==='activity'),false,'a ping is not a write of the idea');
    });
  }finally{Date.now=realNow;}
});

test('a draft saves on leaving the field or after a 3 s pause, never per keystroke',async()=>{
  await diagnosticApp(async h=>{
    await h.get('compact-capture').click();
    const words=h.get('idea-text');
    words.input('a');words.input('ab');words.input('abc');
    const pause=()=>[...h.timers.values()].filter(timer=>timer.delay===3000||timer.delay===250);
    const pending=pause();
    assert.deepEqual(pending.map(timer=>timer.delay),[3000],'one pause timer, restarted by each keystroke');
    assert.equal(h.writes.filter(write=>write.operation==='draft').length,0,'no save while typing');
    await pending[0].callback();await new Promise(r=>setImmediate(r));
    assert.equal(h.writes.filter(write=>write.operation==='draft').length,1,'the pause saves once');
    words.input('abcd');assert.equal(pause().length,1);
    await words.listeners.change({target:words});
    const settle=pause();assert.deepEqual(settle.map(timer=>timer.delay),[250],'leaving the field saves after a short settle, replacing the 3 s timer');
    words.input('abcde');assert.deepEqual(pause().map(timer=>timer.delay),[3000],'typing again straight away restarts the pause, so no save races the next keystroke');
    await words.listeners.change({target:words});await pause()[0].callback();await new Promise(r=>setImmediate(r));
    assert.equal(h.writes.filter(write=>write.operation==='draft').length,2,'leaving the field saves');
  });
});

test('workspace and session-binding refusals say what to do, not a generic failure',async()=>{
  // Demo finding: both codes showed only "Could not save".
  for(const [code,expected] of [['workspace_unavailable',/existing folder on the computer running this service/],
                                ['session_binding_mismatch',/different session than the one that answered/]])await diagnosticApp(async h=>{
    h.faults.code=code;h.flow.edit('visualize',h.fields);await h.flow.saveVisualize([h.ASSET]);
    assert.match(h.alerts(),expected);assert.match(h.alerts(),/answers have been kept/);assert.doesNotMatch(h.alerts(),/Could not save/);
  });
});

test('a pairing refused for another session explains the remedy instead of a raw code',async()=>{
  // Review demofix-r1 note 5: the pairing panel showed "Pairing refused: session_binding_mismatch".
  await diagnosticApp(async h=>{
    await h.get('compact-capture').click();h.get('idea-text').input('Words before the browser lost its pairing');
    h.faults.code='browser_unauthorized';h.faults.status=401;
    await [...h.timers.values()].find(timer=>timer.delay===3000).callback();await new Promise(r=>setImmediate(r));
    const form=h.get('pairing-code');assert.ok(form,'the pairing form is shown');
    h.flow.api.pair=async()=>{throw Object.assign(new Error('session_binding_mismatch'),{code:'session_binding_mismatch',status:403});};
    form.value='123456';
    const connection=globalThis.document.getElementById('connection');
    const submit=connection.all().find(node=>node.listeners?.submit);
    await submit.listeners.submit({preventDefault(){}});
    const text=connection.all().map(node=>node.textContent).join('\n');
    assert.match(text,/belongs to a different session than the code you entered/);assert.doesNotMatch(text,/Pairing refused: session_binding_mismatch/);
  });
});

test('a save that falls due while another write is in flight is retried, never dropped',async()=>{
  // Review idle-r1 note 4: the leave-field timer used to return silently while busy.
  await diagnosticApp(async h=>{
    await h.get('compact-capture').click();
    const words=h.get('idea-text');
    const due=()=>[...h.timers.values()].filter(timer=>timer.delay===3000||timer.delay===250);
    words.input('typed while a write is in flight');
    await words.listeners.change({target:words});
    h.flow.busy=true;
    await due()[0].callback();await new Promise(r=>setImmediate(r));
    assert.equal(h.writes.filter(write=>write.operation==='draft').length,0,'no save while another write is in flight');
    assert.deepEqual(due().map(timer=>timer.delay),[3000],'the save is re-armed');
    h.flow.busy=false;
    await due()[0].callback();await new Promise(r=>setImmediate(r));
    assert.equal(h.writes.filter(write=>write.operation==='draft').length,1,'the edit is saved once the write settles');
  });
});

test('actual app Capture and Priorities require Shape first for persisted archived or unknown ideas',async()=>{
  for(const key of ['capture','priorities'])for(const status of ['archived',null])await diagnosticApp(async h=>{
    await h.get('compact-'+key).click();
    const fields=key==='capture'?cloneForDiagnostic(h.state.accepted.capture):{urgency:6,importance:7};
    h.flow.edit(key,fields);const accept=h.get(key+'-accept');assert.equal(accept.disabled,false);
    h.flow.state.idea_status=status;
    await accept.listeners.click({target:accept});assert.equal(h.writes.length,0);
    h.flow.onChange();assert.equal(h.get(key+'-accept').disabled,true);
    await h.get(key+'-accept').click();assert.equal(h.writes.length,0);
    assert.deepEqual(h.flow.buffers[key],fields);assert.equal(h.flow.pending,null);
    assert.equal(h.get(key+'-idea-status').role,'status');
    if(status==='archived')assert.match(h.get(key+'-idea-status').textContent,/first explicitly redo and accept Shape/);
    else assert.match(h.get(key+'-idea-status').textContent,/status is unavailable/);
    if(key==='capture'){assert.equal(h.get('idea-text').value,fields.raw_text);assert.ok(h.get('capture-attachments'));}
    else{assert.equal(h.get('urgency-6')['aria-pressed'],'true');assert.equal(h.get('importance-7')['aria-pressed'],'true');}
    assert.equal(h.flow.canOpen('shape'),true);assert.equal(h.get('compact-shape').disabled,false);
  });
});

test('per-tab secret: api.js sends X-Idea-Tab on every call, keeps it in sessionStorage-shaped storage and never in the page',async()=>{
  const server=emulatedServer('tab'); const tabs=memoryStorage();
  const api=new IdeaApi(server.fetcher,15000,null,300000,tabs); await api.pair('code-a');
  assert.equal(tabs.map.get('glitch-idea-tab:'+A),'test-only-tab-'+A);
  await api.state(); await api.write('activity',{});
  const authed=server.calls.filter(call=>!call.url.endsWith('/pair'));
  assert.ok(authed.length>=3);
  assert.ok(authed.every(call=>call.options.headers['X-Idea-Tab']==='test-only-tab-'+A));
  assert.equal(server.calls[0].options.headers['X-Idea-Tab'],undefined);
  assert.ok(authed.every(call=>call.options.credentials==='same-origin'));
});

test('per-tab secret: reload in the same tab keeps it, a new tab (empty storage) is refused until it pairs',async()=>{
  const server=emulatedServer('tab2'); const tabs=memoryStorage();
  await new IdeaApi(server.fetcher,15000,null,300000,tabs).pair('code-a');
  const reload=new IdeaApi(server.fetcher,15000,A,300000,tabs);
  assert.equal((await reload.session()).session_id,SA);
  const newTab=new IdeaApi(server.fetcher,15000,A,300000,memoryStorage());
  await assert.rejects(newTab.session(),reject('browser_unauthorized'));
});

test('per-tab secret: a 401 drops the stored secret and a pair response without one is refused',async()=>{
  const server=emulatedServer('tab3'); const tabs=memoryStorage();
  const api=new IdeaApi(server.fetcher,15000,null,300000,tabs); await api.pair('code-a');
  tabs.map.set('glitch-idea-tab:'+A,'x'); api.tabSecret='wrong';
  await assert.rejects(api.state(),reject('browser_unauthorized'));
  assert.equal(api.tabSecret,null); assert.equal(tabs.map.has('glitch-idea-tab:'+A),false);
  const bare=new IdeaApi(async()=>response({ok:true,code:'ok',binding_id:A,session_id:SA,csrf_token:'c'}),15000,null,300000,memoryStorage());
  await assert.rejects(bare.pair('x'),reject('invalid_session'));
});

test('per-tab secret: api.js uses sessionStorage only, never localStorage or document.cookie',async()=>{
  const api=await source('api.js');
  assert.match(api,/globalThis\.sessionStorage/);
  assert.doesNotMatch(api,/localStorage|document\.cookie|console\./);
});
