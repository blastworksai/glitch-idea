// Actual Flow + IdeaApi with fixed protocol fixtures; no browser proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web = new URL('../../glitch-idea/web/', import.meta.url);
const moduleFrom = async name => import('data:text/javascript;base64,' + Buffer.from(await readFile(new URL(name, web), 'utf8')).toString('base64'));
const {Flow, STEPS} = await moduleFrom('folds.js');
const {IdeaApi, ApiError} = await moduleFrom('api.js');
const copy = value => structuredClone(value);
const IDEA = 'idea_' + '1'.repeat(32), OTHER = 'idea_' + '2'.repeat(32);
const SESSION = 'session_' + '3'.repeat(32), BINDING = 'binding_' + '4'.repeat(32);
const PACKET = 'handoff_' + '5'.repeat(32), HASH = 'a'.repeat(64), DIGEST = 'b'.repeat(64);
const CAPTURE = {raw_text:'Literal fixture 💡',workspace:{name:'Fixture',path:'/workspace',confirmed:true}};
const packet = (id = IDEA, hash = HASH, packetId = PACKET, digest = DIGEST) => ({handoff_id:packetId,source_revision:6,
  source_digest:digest,sha256:hash,path:'/store/history/'+id+'/metadata/'+hash+'.md',prompt:'/glitch-plan\nLiteral prompt 💡\n',
  source_files:{detail:{path:'/store/'+id+'.md',sha256:DIGEST},index:{path:'/store/IDEAS.md',sha256:DIGEST},
    revision:{path:'/store/history/'+id+'/r6.md',sha256:DIGEST}},design_set:null});
function state(id = IDEA) {
  return {ok:true,code:'ok',session_id:SESSION,idea_id:id,idea_status:id===null?null:'active',
    revision:id===null?0:6,draft_version:id===null?0:3,backlog_revision:2,current_step:id===null?'capture':'review',
    agent_status:'disconnected',capabilities:{handoff:true},handoff:null,
    handoff_status:{available:false,code:id===null?'no_selection':'not_ready'},
    steps:Object.fromEntries(STEPS.map(({key})=>[key,{status:id===null?(key==='capture'?'current':'todo'):(key==='review'?'current':'saved'),
      accepted_revision:id!==null&&key!=='review'?6:null,evidence_id:id!==null&&key!=='review'?'fixture-'+key:null}])),
    accepted:Object.fromEntries(STEPS.map(({key})=>[key,key==='capture'&&id!==null?copy(CAPTURE):null])),drafts:{},draft:null};
}
function installPacket(value, p = packet(value.idea_id)) {
  value.handoff=copy(p);value.handoff_status={available:true,code:'ok'};
  value.accepted.review={handoff_id:p.handoff_id,source_revision:p.source_revision};
  value.steps.review={status:'saved',accepted_revision:value.revision,evidence_id:p.handoff_id};
}
function receipt(payload, p = packet(payload.idea_id), current = true) {
  return {ok:true,code:current?'ok':'historical_handoff',request_id:payload.request_id,write_state:'applied',idea_id:payload.idea_id,
    revision:payload.expected_revision,draft_version:payload.expected_draft_version,backlog_revision:payload.expected_backlog_revision,
    handoff_index:1,handoff_id:p.handoff_id,path:'history/'+payload.idea_id+'/metadata/'+p.sha256+'.md',sha256:p.sha256,
    handoff:copy(p),handoff_current:current};
}
const response = (value,status=200) => ({ok:status>=200&&status<300,status,json:async()=>copy(value)});
const deferred = () => { let resolve,reject; const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject}; };
function harness({ready=false,storage=null} = {}) {
  let current=state(),count=0;
  if(ready)installPacket(current);
  const calls=[],receipts=new Map(),faults={};
  const api=new IdeaApi(async(url,options)=>{
    const payload=options.body===undefined?null:JSON.parse(options.body);
    calls.push({url,method:options.method,payload:copy(payload)});
    if(url==='/api/v1/handoff') {
      if(faults.before)throw Error('lost before publication');
      const result=receipts.get(payload.request_id)??receipt(payload);
      receipts.set(payload.request_id,copy(result));installPacket(current,result.handoff);
      if(faults.after)throw Error('lost acknowledgement');
      return response(faults.reply?faults.reply(copy(result)):result);
    }
    if(url.includes('/requests/')) {
      if(faults.reconcile)throw Error('receipt unavailable');
      const saved=receipts.get(decodeURIComponent(url.split('/requests/')[1]));
      return saved?response(faults.receipt?faults.receipt(copy(saved)):saved):response({ok:false,code:'request_not_found'},404);
    }
    if(url==='/api/v1/selection') {
      if(faults.selection) return response({ok:false,code:faults.selection},500);
      current=state(payload.idea_id);
      if(payload.idea_id!==null&&faults.targetState)current=faults.targetState(copy(current));
      const result={ok:true,code:'ok',session_id:SESSION,idea_id:payload.idea_id,
        revision:current.revision,draft_version:current.draft_version,backlog_revision:current.backlog_revision};
      if(faults.selectionLost)throw Error('lost selection acknowledgement');
      return response(faults.selectionReply?faults.selectionReply(result):result);
    }
    if(url==='/api/v1/capture') {
      current=state(OTHER);current.revision=1;current.draft_version=0;current.current_step='priorities';
      current.accepted=Object.fromEntries(STEPS.map(({key})=>[key,key==='capture'?{raw_text:payload.raw_text,workspace:payload.workspace}:null]));
      current.steps=Object.fromEntries(STEPS.map(({key})=>[key,{status:key==='capture'?'saved':key==='priorities'?'current':'todo',
        accepted_revision:key==='capture'?1:null,evidence_id:key==='capture'?'new-capture':null}]));
      return response({ok:true,code:'ok',request_id:payload.request_id,write_state:'applied',idea_id:OTHER,
        revision:1,draft_version:0,backlog_revision:2});
    }
    if(url.startsWith('/api/v1/state')) {
      if(faults.read) return faults.read(copy(current));
      if(faults.readFailures>0){faults.readFailures--;throw Error('state unavailable');}
      return response(current);
    }
    if(url==='/api/v1/ideas') {
      if(faults.ideas) return faults.ideas();
      return response({ok:true,code:'ok',backlog_revision:2,total:1,ideas:[{idea_id:IDEA,revision:6,position:1,
        title:'Literal fixture 💡',status:'in-progress',method:null,updated:'2026-10-02',detail_path:'/store/'+IDEA+'.md'}]});
    }
    throw Error('Unexpected fixture route '+url);
  },1000,BINDING);
  api.sessionId=SESSION;api.csrf='test-only-csrf';
  const flow=new Flow(api,()=> 'handoff-request-'+(++count),()=>0,storage);flow.load(current);
  return {flow,api,calls,receipts,faults,get state(){return current;},writes:()=>calls.filter(call=>call.method==='POST')};
}
function polling(flow) {
  const jobs=new Map();let next=0;
  const options={interval:777,active:()=>true,setTimer:(job,delay)=>{jobs.set(++next,{job,delay});return next;},clearTimer:id=>jobs.delete(id)};
  flow.startAgentRefresh(options);
  return {jobs,options,take:()=>{const [id,job]=jobs.entries().next().value;jobs.delete(id);return job.job;}};
}

test('handoff uses exact typed envelope and single dispatcher without accepting fields or changing counters',async()=>{
  const h=harness(),before=copy(h.flow.buffers),counters=['revision','draft_version','backlog_revision'].map(k=>h.flow.state[k]);
  assert.equal(h.flow.canGenerateHandoff(),true);assert.equal(h.flow.canCopyHandoff(),false);
  assert.equal(await h.flow.generateHandoff(),true);
  assert.deepEqual(h.writes(),[{url:'/api/v1/handoff',method:'POST',payload:{request_id:'handoff-request-1',idea_id:IDEA,
    expected_revision:6,expected_draft_version:3,expected_backlog_revision:2}}]);
  assert.equal(h.flow.pending,null);assert.equal(h.flow.current,'review');assert.equal(h.flow.canCopyHandoff(),true);
  assert.deepEqual(h.flow.buffers,before);assert.deepEqual(['revision','draft_version','backlog_revision'].map(k=>h.flow.state[k]),counters);
  assert.equal(h.flow.view,'workflow');assert.equal(h.flow.state.steps.review.status,'saved');
});

test('generation and copy refuse incomplete, dirty, pending, proposal, paused, archived, disposed or unavailable states',async()=>{
  for(const change of [f=>f.edit('capture',{...CAPTURE,raw_text:'Local edit'}),f=>f.pending={ambiguous:true},
    f=>f.proposalPending={key:'exploration'},f=>f.paused=true,f=>f.busy=true,f=>f.dispose(),
    f=>f.state.idea_status='archived',f=>f.state.capabilities.handoff=false,f=>f.state.steps.assess.status='review-needed']) {
    const h=harness({ready:true});change(h.flow);
    assert.equal(h.flow.canGenerateHandoff(),false);assert.equal(h.flow.canCopyHandoff(),false);
    assert.equal(await h.flow.generateHandoff(),false);assert.equal(await h.flow.currentCopy(),null);assert.equal(h.calls.length,0);
  }
});

test('lost handoff acknowledgement reconciles exact receipt once without second publication',async()=>{
  const h=harness();h.faults.after=true;
  assert.equal(await h.flow.generateHandoff(),true);assert.equal(h.writes().length,1);assert.equal(h.flow.pending,null);
  assert.match(h.flow.message,/recovered/);assert.equal(h.flow.canCopyHandoff(),true);
  assert.ok(h.calls.filter(c=>c.url.startsWith('/api/v1/state')).every(c=>c.url.endsWith('?idea_id='+IDEA)));
});

test('post-write read failure and explicit retry preserve the exact handoff pending identity',async()=>{
  const h=harness();h.faults.readFailures=2;
  assert.equal(await h.flow.generateHandoff(),false);const pending=h.flow.pending,payload=copy(pending.payload);
  assert.equal(pending.ambiguous,true);assert.equal(h.writes().length,1);
  assert.equal(await h.flow.retry(),true);assert.equal(h.writes().length,1);assert.deepEqual(h.writes()[0].payload,payload);
  assert.equal(h.flow.pending,null);assert.equal(h.flow.canCopyHandoff(),true);
});

test('missing receipt allows only explicit same-envelope retry after pinned state reconciliation',async()=>{
  const h=harness();h.faults.before=true;
  assert.equal(await h.flow.generateHandoff(),false);const submitted=copy(h.flow.pending.payload);
  assert.equal(h.writes().length,1);h.faults.before=false;
  assert.equal(await h.flow.retry(),true);assert.deepEqual(h.writes().map(c=>c.payload),[submitted,submitted]);
  assert.ok(h.calls.findIndex(c=>c.url.includes('/requests/'))<h.calls.findLastIndex(c=>c.method==='POST'));
});

test('malformed or foreign handoff receipts stay pending and never redirect state or grant copy authority',async()=>{
  for(const mutate of [r=>({...r,request_id:'wrong'}),r=>({...r,idea_id:OTHER}),r=>{delete r.handoff;delete r.handoff_current;return r;}]) {
    const h=harness();h.faults.reply=mutate;h.faults.receipt=mutate;
    assert.equal(await h.flow.generateHandoff(),false);assert.equal(h.flow.pending.ambiguous,true);
    assert.equal(h.flow.canCopyHandoff(),false);assert.equal(h.calls.filter(c=>c.url.startsWith('/api/v1/state')).length,0);
    assert.equal(h.flow.state.handoff,null);assert.equal(h.writes().length,1);
  }
});

test('historical or different current packet resolves known save without silently substituting copy authority',async()=>{
  for(const kind of ['historical','newest']) {
    const h=harness();h.faults.after=true;
    h.faults.receipt=r=>kind==='historical'?{...r,code:'historical_handoff',handoff_current:false}:r;
    h.faults.read=value=>{
      if(kind==='historical'){value.handoff_status={available:false,code:'not_ready'};value.steps.review.status='review-needed';value.accepted.review=null;}
      else installPacket(value,packet(IDEA,'c'.repeat(64),'handoff_'+'6'.repeat(32),'d'.repeat(64)));
      return response(value);
    };
    assert.equal(await h.flow.generateHandoff(),false);assert.equal(h.flow.pending,null);assert.equal(h.flow.error.code,'saved_state_changed');
    assert.equal(h.flow.canCopyHandoff(),false);
    assert.equal(h.writes().length,1);assert.equal(h.flow.current,'review');
  }
});

test('newer human edits survive handoff recovery and prevent copy after await',async()=>{
  const h=harness();h.faults.readFailures=2;
  assert.equal(await h.flow.generateHandoff(),false);
  h.flow.edit('capture',{...CAPTURE,raw_text:'New human words'});
  assert.equal(await h.flow.retry(),false);assert.equal(h.flow.pending,null);
  assert.equal(h.flow.buffers.capture.raw_text,'New human words');assert.equal(h.flow.dirty.has('capture'),true);
  assert.equal(h.flow.canCopyHandoff(),false);assert.equal(h.writes().length,1);
});

test('currentCopy refreshes only authority and pins packet ID hash digest revision and idea',async()=>{
  const h=harness({ready:true}),expected=copy(h.flow.state.handoff);
  assert.deepEqual(await h.flow.currentCopy(expected),expected);assert.equal(h.writes().length,0);
  for(const changed of [packet(IDEA,HASH,'handoff_'+'7'.repeat(32)),packet(IDEA,'c'.repeat(64)),packet(IDEA,HASH,PACKET,'d'.repeat(64))]) {
    const current=harness({ready:true});installPacket(current.state,changed);
    assert.equal(await current.flow.currentCopy(expected),null);assert.equal(current.flow.error.code,'stale_source');assert.equal(current.writes().length,0);
  }
  const foreign=harness({ready:true});foreign.faults.read=()=>response(state(OTHER));
  assert.equal(await foreign.flow.currentCopy(expected),null);assert.equal(foreign.flow.state.idea_id,IDEA);
});

test('copy refresh rejects workspace drift and local dirty pending paused or disposal changes during await',async()=>{
  const stale=harness({ready:true});stale.state.handoff_status={available:false,code:'workspace_unavailable'};
  stale.state.steps.review.status='review-needed';stale.state.accepted.review=null;
  assert.equal(await stale.flow.currentCopy(),null);assert.equal(stale.writes().length,0);
  for(const change of [f=>f.edit('capture',{...CAPTURE,raw_text:'New words'}),f=>f.pending={ambiguous:true},f=>f.paused=true,f=>f.dispose()]) {
    const h=harness({ready:true}),flight=deferred();h.faults.read=()=>flight.promise;
    const copying=h.flow.currentCopy();change(h.flow);flight.resolve(response(h.state));
    assert.equal(await copying,null);assert.equal(h.writes().length,0);
  }
});

test('Ideas read and view switch preserve all answers and pending associations; capacity keeps prior rows',async()=>{
  const h=harness();h.flow.edit('capture',{...CAPTURE,raw_text:'Keep this buffer'});
  h.flow.pending={operation:'draft',ambiguous:true};h.flow.selectedProposals.exploration='proposal_'+'8'.repeat(32);
  const before=copy(h.flow.buffers),pending=h.flow.pending,selected=copy(h.flow.selectedProposals);
  assert.equal(await h.flow.showIdeas(),true);assert.equal(h.flow.view,'ideas');assert.equal(h.flow.ideas.total,1);
  assert.deepEqual(h.flow.buffers,before);assert.equal(h.flow.pending,pending);assert.deepEqual(h.flow.selectedProposals,selected);
  const rows=copy(h.flow.ideas);h.faults.ideas=()=>response({ok:false,code:'ideas_capacity'},400);
  assert.equal(await h.flow.loadIdeas(),false);assert.deepEqual(h.flow.ideas,rows);assert.equal(h.flow.ideasError.code,'ideas_capacity');
  assert.equal(h.writes().length,0);
});

test('selection refuses silent discard of dirty, pending, proposal, paused or disposed work',async()=>{
  for(const change of [f=>f.edit('capture',{...CAPTURE,raw_text:'Keep words'}),f=>f.pending={ambiguous:false},
    f=>f.proposalPending={key:'exploration'},f=>f.paused=true,f=>f.busy=true,f=>f.dispose()]) {
    const h=harness();change(h.flow);const before=copy(h.flow.buffers),pending=h.flow.pending;
    assert.equal(await h.flow.selectIdea(null),false);assert.deepEqual(h.flow.buffers,before);assert.equal(h.flow.pending,pending);assert.equal(h.calls.length,0);
  }
});

test('verified null selection resets every default and target selection restores only actual saved fields',async()=>{
  const h=harness({ready:true});h.flow.buffers.exploration.outcome='Old clean field';h.flow.buffers.discovery.problem='Old problem';h.flow.buffers.priorities={urgency:8,importance:9};
  h.flow.buffers.visualize.reason='Old reason';h.flow.buffers.assess.assessment={old:true};
  h.flow.selectedProposals.exploration='proposal_'+'8'.repeat(32);h.flow.proposalError={code:'old'};
  h.flow.view='ideas';assert.equal(await h.flow.selectIdea(null),true);
  const fresh=new Flow(h.api,()=> 'unused',()=>0,null);
  assert.deepEqual(h.flow.buffers,fresh.buffers);assert.equal(h.flow.state.idea_id,null);assert.equal(h.flow.state.revision,0);
  assert.equal(h.flow.state.draft_version,0);assert.equal(h.flow.state.backlog_revision,2);
  assert.deepEqual(h.flow.selectedProposals,{});assert.equal(h.flow.proposalError,null);assert.equal(h.flow.current,'capture');assert.equal(h.flow.view,'workflow');
  h.faults.targetState=value=>({...value,current_step:'exploration',drafts:{exploration:{outcome:'Actual saved draft'}}});
  assert.equal(await h.flow.selectIdea(OTHER),true);assert.equal(h.flow.state.idea_id,OTHER);assert.equal(h.flow.current,'exploration');
  assert.deepEqual(h.flow.buffers.exploration,{outcome:'Actual saved draft'});assert.deepEqual(h.flow.buffers.priorities,{urgency:null,importance:null});
  assert.deepEqual(h.writes().map(c=>c.payload),[{idea_id:null},{idea_id:OTHER}]);
});

test('persistence refusal preserves prior buffers and polling; uncertain selection stops until explicit verified retry',async()=>{
  const h=harness({ready:true}),poll=polling(h.flow),before=copy(h.flow.buffers);
  h.faults.selection='session_persistence_failed';
  assert.equal(await h.flow.selectIdea(null),false);assert.deepEqual(h.flow.buffers,before);assert.equal(h.flow.state.idea_id,IDEA);
  assert.equal(h.flow.selectionUncertain,false);assert.equal(poll.jobs.size,1);
  h.faults.selection=null;h.faults.selectionLost=true;
  assert.equal(await h.flow.selectIdea(null),false);assert.deepEqual(h.flow.buffers,before);assert.equal(h.flow.selectionUncertain,true);
  assert.equal(poll.jobs.size,0);assert.equal(await h.flow.refreshAgent(),false);assert.equal(h.writes().length,2);
  assert.equal(await h.flow.save('capture'),false);assert.equal(await h.flow.pause(),false);
  assert.equal(await h.flow.saveVisualize(),false);assert.equal(await h.flow.requestProposal('exploration'),false);
  assert.equal(await h.flow.reloadKeepingAnswers(),false);assert.equal(await h.flow.retryProposal(),false);
  assert.equal(h.flow.canPropose('exploration'),false);assert.equal(h.writes().length,2);
  h.faults.selectionLost=false;assert.equal(await h.flow.selectIdea(null),true);
  assert.equal(h.flow.selectionUncertain,false);assert.equal(poll.jobs.size,1);assert.equal([...poll.jobs.values()][0].delay,777);
  h.flow.dispose();
});

test('selection wrong identity counters malformed state or post-selection read failure never clears old answers',async()=>{
  for(const mode of ['wrong-session','wrong-idea','wrong-counter','bad-state','read-failure']) {
    const h=harness({ready:true}),before=copy(h.flow.buffers);
    if(mode==='wrong-session')h.faults.selectionReply=r=>({...r,session_id:'session_'+'9'.repeat(32)});
    if(mode==='wrong-idea')h.faults.read=()=>response(state(OTHER));
    if(mode==='wrong-counter')h.faults.read=value=>response({...value,backlog_revision:3});
    if(mode==='bad-state')h.faults.read=value=>response({...value,steps:{}});
    if(mode==='read-failure')h.faults.readFailures=1;
    assert.equal(await h.flow.selectIdea(null),false);assert.deepEqual(h.flow.buffers,before);assert.equal(h.flow.state.idea_id,IDEA);
    assert.equal(h.flow.selectionUncertain,true);assert.equal(await h.flow.refreshAgent(),false);assert.equal(h.writes().length,1);
  }
});

test('old polling success AND error cannot overwrite selection or cancel its single replacement polling loop',async()=>{
  for(const kind of ['success','401','offline']) {
    const h=harness({ready:true}),poll=polling(h.flow),old=deferred(),fresh=deferred();let count=0;
    h.faults.read=value=>++count===1?old.promise:response(value);
    const stalePoll=poll.take()();
    assert.equal(await h.flow.selectIdea(null),true);assert.equal(poll.jobs.size,1);
    h.faults.read=()=>fresh.promise;
    const nextPoll=poll.take()();const newFlight=h.flow.refreshPromise;
    if(kind==='success')old.resolve(response(state()));
    else if(kind==='401')old.resolve(response({ok:false,code:'browser_unauthorized'},401));
    else old.reject(new Error('old request failed'));
    await stalePoll;
    assert.equal(h.flow.state.idea_id,null);assert.equal(h.flow.refreshPromise,newFlight);assert.equal(h.flow.proposalError,null);
    assert.equal(h.flow.refreshUnauthorized,false);assert.equal(h.flow.error,null);
    assert.equal(h.api.csrf,'test-only-csrf');
    fresh.resolve(response(state(null)));await nextPoll;assert.equal(poll.jobs.size,1);
    h.faults.read=null;
    assert.equal(await h.flow.selectIdea(OTHER),true);
    assert.equal(h.flow.state.idea_id,OTHER);assert.equal(h.api.csrf,'test-only-csrf');
    assert.deepEqual(h.writes().map(call=>call.payload),[{idea_id:null},{idea_id:OTHER}]);
    assert.equal(poll.jobs.size,1);
    h.flow.dispose();assert.equal(poll.jobs.size,0);
  }
});

test('Ideas and handoff awaited replies cannot revive disposed view or replace a newly invalidated scope',async()=>{
  const h=harness(),wait=deferred();h.faults.ideas=()=>wait.promise;
  const listing=h.flow.showIdeas();h.flow.dispose();wait.resolve(response({ok:true,code:'ok',backlog_revision:2,total:0,ideas:[]}));
  assert.equal(await listing,false);assert.equal(h.flow.view,'workflow');assert.equal(h.flow.ideas,null);
  const generating=harness(),read=deferred();generating.faults.read=()=>read.promise;
  const saving=generating.flow.generateHandoff();
  // Allow the fixture's acknowledged write to reach its separate state read.
  await Promise.resolve();await Promise.resolve();await Promise.resolve();await Promise.resolve();
  generating.flow.dispose();read.resolve(response(generating.state));
  assert.equal(await saving,false);assert.equal(generating.flow.state.handoff,null);assert.ok(generating.flow.pending);
  assert.equal(generating.writes().length,1);
});

test('missing handoff receipt plus human edits keeps visible Check and original identity without resending',async()=>{
  const h=harness();h.faults.before=true;
  assert.equal(await h.flow.generateHandoff(),false);
  const pending=h.flow.pending,payload=copy(pending.payload);
  h.flow.edit('capture',{...CAPTURE,raw_text:'Newer words must survive'});
  h.state.draft_version=8;
  assert.equal(await h.flow.retry(),false);
  assert.equal(h.flow.pending,pending);assert.deepEqual(pending.payload,payload);assert.equal(pending.ambiguous,true);
  assert.equal(h.flow.error.code,'durability_uncertain');assert.notEqual(h.flow.message,'Saving…');assert.match(h.flow.message,/undo the newer edits.*resend the original request.*reload the page/i);
  assert.equal(h.flow.state.draft_version,8);assert.equal(h.flow.buffers.capture.raw_text,'Newer words must survive');
  assert.equal(h.flow.dirty.has('capture'),true);assert.equal(h.writes().length,1);
  assert.equal(await h.flow.selectIdea(null),false);assert.equal(await h.flow.reloadKeepingAnswers(),false);
  // A missing receipt is not proof that the original request cannot land later.
  // A subsequent terminal receipt resolves that SAME request without another POST.
  h.receipts.set(payload.request_id,receipt(payload));installPacket(h.state);
  assert.equal(await h.flow.retry(),false);assert.equal(h.flow.pending,null);assert.equal(h.flow.error.code,'saved_state_changed');
  assert.equal(h.flow.buffers.capture.raw_text,'Newer words must survive');assert.equal(h.writes().length,1);
});

test('null and saved selected-state polls reject foreign idea or session before adoption and freeze unsafe writes',async()=>{
  for(const ideaId of [null,IDEA])for(const foreign of ['idea','session']) {
    const h=harness();if(ideaId===null)assert.equal(await h.flow.selectIdea(null),true);
    h.flow.edit('capture',{...CAPTURE,raw_text:'Private unsaved words'});
    const before=copy(h.flow.buffers),initial=copy(h.flow.state),posts=h.writes().length,poll=polling(h.flow);
    h.faults.read=()=>response(foreign==='idea'?state(OTHER):{...state(ideaId),session_id:'session_'+'9'.repeat(32)});
    assert.equal(await h.flow.refreshAgent(),false);
    assert.equal(h.flow.selectionUncertain,true);assert.equal(h.flow.error.code,'invalid_response');assert.equal(poll.jobs.size,0);
    assert.deepEqual(h.flow.state,initial);assert.deepEqual(h.flow.buffers,before);assert.equal(h.flow.dirty.has('capture'),true);
    assert.equal(await h.flow.save('capture',true),false);assert.equal(await h.flow.saveVisualize(),false);
    assert.equal(await h.flow.requestProposal('exploration'),false);assert.equal(await h.flow.pause(),false);
    assert.equal(await h.flow.reloadKeepingAnswers(),false);assert.equal(h.writes().length,posts);
    assert.equal(await h.flow.selectIdea(OTHER),false);h.flow.dispose();
  }
});

test('reload pins authoritative null and SID and keeps dirty answers when selected identity differs',async()=>{
  for(const foreign of ['idea','session']) {
    const h=harness();assert.equal(await h.flow.selectIdea(null),true);
    h.flow.edit('capture',{...CAPTURE,raw_text:'New unsaved idea words'});const before=copy(h.flow.buffers);
    h.faults.read=()=>response(foreign==='idea'?state(OTHER):{...state(null),session_id:'session_'+'9'.repeat(32)});
    assert.equal(await h.flow.reloadKeepingAnswers(),false);assert.equal(h.flow.state.idea_id,null);
    assert.equal(h.flow.state.session_id,SESSION);assert.equal(h.flow.selectionUncertain,true);assert.deepEqual(h.flow.buffers,before);
    assert.equal(h.writes().length,1);
  }
});

test('same-selection restore retains dirty capture text and resumes one loop before a true new Capture',async()=>{
  const h=harness(),poll=polling(h.flow);assert.equal(await h.flow.selectIdea(null),true);
  h.flow.edit('capture',{...CAPTURE,raw_text:'Only the new idea gets these words'});
  h.faults.read=()=>response(state(OTHER));assert.equal(await h.flow.refreshAgent(),false);
  const before=copy(h.flow.buffers);assert.equal(await h.flow.selectIdea(OTHER),false);
  h.faults.read=null;assert.equal(await h.flow.restoreSelection(),true);
  assert.equal(h.flow.state.idea_id,null);assert.equal(h.flow.state.session_id,SESSION);assert.equal(h.flow.selectionUncertain,false);
  assert.deepEqual(h.flow.buffers,before);assert.equal(h.flow.dirty.has('capture'),true);assert.equal(poll.jobs.size,1);
  assert.deepEqual(h.writes().map(call=>call.payload),[{idea_id:null},{idea_id:null}]);
  assert.equal(await h.flow.save('capture',true),true);
  assert.equal(h.writes().at(-1).url,'/api/v1/capture');assert.equal(h.writes().at(-1).payload.raw_text,'Only the new idea gets these words');
  assert.equal(Object.hasOwn(h.writes().at(-1).payload,'idea_id'),false);h.flow.dispose();
});

test('restore preserves unresolved handoff identity, dirty fields, navigation, associations and paused state',async()=>{
  const h=harness();h.faults.before=true;assert.equal(await h.flow.generateHandoff(),false);
  const pending=h.flow.pending;h.flow.edit('capture',{...CAPTURE,raw_text:'Preserved words'});
  h.flow.current='capture';h.flow.paused=true;h.flow.selectedProposals.exploration='proposal_'+'8'.repeat(32);
  h.flow.selectionUncertain=true;const before=copy(h.flow.buffers);
  assert.equal(await h.flow.restoreSelection(),true);
  assert.deepEqual(h.flow.buffers,before);assert.equal(h.flow.pending,pending);assert.equal(h.flow.pending.ambiguous,true);
  assert.equal(h.flow.current,'capture');assert.equal(h.flow.paused,true);
  assert.equal(h.flow.selectedProposals.exploration,'proposal_'+'8'.repeat(32));assert.ok(h.flow.error);
  assert.match(h.flow.message,/check the original save result/);
  assert.equal(h.writes().filter(c=>c.url==='/api/v1/handoff').length,1);
  assert.deepEqual(h.writes().at(-1).payload,{idea_id:IDEA});
});

test('restore verifies selection and state before unfreezing and never discards text on failures',async()=>{
  for(const mode of ['selection-refused','state-id','state-session','state-counter','state-invalid']) {
    const h=harness();h.flow.edit('capture',{...CAPTURE,raw_text:'Keep dirty answers'});h.flow.selectionUncertain=true;
    const before=copy(h.flow.buffers),poll=polling(h.flow);
    if(mode==='selection-refused')h.faults.selection='session_persistence_failed';
    if(mode==='state-id')h.faults.read=()=>response(state(OTHER));
    if(mode==='state-session')h.faults.read=()=>response({...state(),session_id:'session_'+'9'.repeat(32)});
    if(mode==='state-counter')h.faults.read=()=>response({...state(),revision:7});
    if(mode==='state-invalid')h.faults.read=()=>response({...state(),steps:{}});
    assert.equal(await h.flow.restoreSelection(),false);assert.deepEqual(h.flow.buffers,before);assert.equal(h.flow.dirty.has('capture'),true);
    assert.equal(h.flow.selectionUncertain,true);assert.equal(h.flow.state.idea_id,IDEA);assert.equal(poll.jobs.size,0);
    assert.deepEqual(h.writes().map(call=>call.payload),[{idea_id:IDEA}]);h.flow.dispose();
  }
});

test('public authority reader pins explicit null/session before await and rejects foreign state without adoption',async()=>{
  const h=harness(),initial=copy(h.flow.state);
  h.faults.read=()=>response(state(null));
  assert.equal((await h.flow.readAuthority(null,SESSION)).idea_id,null);assert.deepEqual(h.flow.state,initial);
  const flight=deferred();h.faults.read=()=>flight.promise;
  const reading=h.flow.readAuthority(null,SESSION);
  h.api.sessionId='session_'+'9'.repeat(32);flight.resolve(response({...state(null),session_id:h.api.sessionId}));
  await assert.rejects(reading,error=>error.code==='invalid_response');
  assert.equal(h.flow.selectionUncertain,true);assert.deepEqual(h.flow.state,initial);
});

test('mutation state and missing-receipt reads reject wrong SID before adoption or retry publication',async()=>{
  for(const mode of ['after-write','missing-receipt']) {
    const h=harness();if(mode==='missing-receipt')h.faults.before=true;
    h.faults.read=value=>response({...value,session_id:'session_'+'9'.repeat(32)});
    assert.equal(await h.flow.generateHandoff(),false);
    assert.equal(h.flow.state.session_id,SESSION);assert.equal(h.flow.state.handoff,null);assert.equal(h.flow.selectionUncertain,true);
    assert.equal(h.flow.pending.ambiguous,true);assert.equal(await h.flow.retry(),false);assert.equal(h.writes().length,1);
  }
});
