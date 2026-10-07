// Actual Flow module with fixed API fixtures; no native browser claim.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web = new URL('../../glitch-idea/web/', import.meta.url);
const source = await readFile(new URL('folds.js', web), 'utf8');
const {Flow, STEPS, validDiscovery, validExploration, validMethod} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const copy = value => structuredClone(value);
const IDEA = 'idea_'+'1'.repeat(32), SID = 'session_'+'2'.repeat(32), GEN = 'agent_'+'3'.repeat(32), PID = 'proposal_'+'4'.repeat(32);
const CAPTURE = {raw_text: 'Actual fixture words', workspace: {name: 'Explicit', path: '/fixture', confirmed: true}};
const EXPL = {outcome: 'Clean lid', alternatives: [{route: 'Clean existing lid', reason: 'Less work'}], assumptions: [], scope: 'small-change', scope_reason: 'One lid', next_slice: 'Check lid', learning: [],
  investment: null, experiment: null, sketch: [{title: 'Wipe the lid', why_next: 'Cheapest check', done_when: 'Lid is clean', method: null}]};
const DISC = {problem: 'Lid is dirty', audience: 'Me', workaround: 'Wipe by hand', evidence: 'Seen twice', kill_criteria: 'Stays clean for a month', challenges: [{challenge: 'Is it needed?', response: 'Yes'}],
  prior_art: [{name: 'Tapeo', link: 'https://example.test/tapeo', does: 'Seals lids', differs: 'Ours is reusable', licence: 'MIT'}], prior_art_none: false, prior_art_searched: ''};
const MEMORY = {status: 'searched_no_preference', sources: ['fixture-safe-reference'], rationale: 'No saved preference found'};
const METHOD = {selection: 'appetite-led', reason: 'My own reason', memory: MEMORY};
const apiError = (code, status=0, uncertain=false) => Object.assign(new Error(code), {code, status, uncertain});
const BINDING = 'binding_'+'6'.repeat(32);
function storage() {
  const entries=new Map();
  return {entries,getItem:key=>entries.get(key)??null,setItem:(key,value)=>entries.set(key,value),removeItem:key=>entries.delete(key)};
}

function state() {
  const value = {ok:true,code:'ok',session_id:SID,idea_id:IDEA,revision:2,draft_version:0,backlog_revision:1,
    current_step:'exploration',agent_status:'connected',agent_generation:GEN,
    steps:Object.fromEntries(STEPS.map(({key})=>[key,{status:['capture','priorities'].includes(key)?'saved':key==='exploration'?'current':'todo',
      accepted_revision:['capture','priorities'].includes(key)?2:null,evidence_id:['capture','priorities'].includes(key)?key+'-evidence':null}])),
    accepted:{capture:copy(CAPTURE),priorities:{urgency:6,importance:7}},drafts:{},draft:null,
    capabilities:{agent:true,memory:true,uploads:false,handoff:false},resume:{required:false,reason:null},proposal_sources:{},proposals:[],
    proposal_inventory:{total:0,projected:0,omitted:0,content_omitted:0,index_path:IDEA+'.md'}};
  updateSources(value); return value;
}
function updateSources(value) {
  for (const key of ['discovery','exploration','method']) value.proposal_sources[key] = value.agent_status==='connected'
    ? {available:true,code:'ok',source:{accepted_revision:value.revision,draft_version:value.draft_version,
      data:{capture:copy(value.accepted.capture),
        ...(value.drafts[key]?{[key]:copy(value.drafts[key])}:{})},
      source_digest:(value.draft_version%16).toString(16).repeat(64)}}
    : {available:false,code:'agent_unavailable',source:null};
}
function summary(payload, operation=payload.operation, overrides={}) {
  return {proposal_id:PID,request_id:payload.request_id,operation,accepted_revision:payload.expected_revision,
    draft_version:payload.expected_draft_version,source_digest:payload.source_digest,proposal:copy(operation==='exploration'?EXPL:operation==='discovery'?DISC:{memory:MEMORY}),
    stale:false,stale_reason:null,acceptance_eligible:true,acceptance_reason:null,content_omitted:false,
    evidence:{path:'history/'+IDEA+'/metadata/'+'a'.repeat(64)+'.md',sha256:'b'.repeat(64)},...overrides};
}
function updateInventory(value,total=value.proposals.length) {
  value.proposal_inventory={total,projected:value.proposals.length,omitted:total-value.proposals.length,
    content_omitted:value.proposals.filter(item=>item.content_omitted).length,index_path:value.idea_id===null?null:value.idea_id+'.md'};
}
function harness(selectionStorage=null) {
  let value = state(), now=0, count=0, reads=0;
  const writes=[], requests=new Map(), receipts=new Map();
  const api = {
    bindingId:BINDING,
    state:async()=>{reads++;return copy(value);},
    write:async(operation,payload)=>{
      writes.push({operation,payload:copy(payload)});
      if(operation==='propose') {
        if(requests.has(payload.request_id)) assert.deepEqual(requests.get(payload.request_id),payload);
        else requests.set(payload.request_id,copy(payload));
        return {ok:true,code:'ok',status:'pending',write_state:'not_applied',request_id:payload.request_id,session_id:SID,
          idea_id:payload.idea_id,accepted_revision:payload.expected_revision,draft_version:payload.expected_draft_version,
          operation:payload.operation,source_digest:payload.source_digest};
      }
      if(operation==='draft') {value.draft_version++;value.drafts[payload.step]=copy(payload.fields);}
      if(operation==='accept') {
        value.revision++;value.accepted[payload.step]=copy(payload.fields);delete value.drafts[payload.step];
        value.steps[payload.step]={status:'saved',accepted_revision:value.revision,evidence_id:'evidence-'+payload.step};
      }
      if(operation==='navigate') value.current_step=payload.step;
      updateSources(value);
      const result={ok:true,code:'ok',write_state:'applied',request_id:payload.request_id,idea_id:IDEA,
        revision:value.revision,draft_version:value.draft_version,backlog_revision:1,...(payload.proposal_id?{proposal_id:payload.proposal_id}:{})};
      receipts.set(payload.request_id,copy(result));return result;
    },
    reconcile:async id=>({result:copy(receipts.get(id)??null),state:await api.state()}),
  };
  const flow=new Flow(api,()=> 'request-'+(++count),()=>now,selectionStorage);flow.load(value);
  return {flow,api,writes,requests,receipts,get value(){return value;},get reads(){return reads;},advance:n=>{now+=n;},
    complete:(overrides={})=>{value.proposals.push(summary([...requests.values()].at(-1),undefined,overrides));updateInventory(value);}};
}

test('proposal source/summary/generation invalid projections fail closed',()=>{
  const mutations=[v=>v.agent_generation='secret',v=>v.agent_generation=null,v=>v.proposal_sources.exploration.source.draft_version=true,
    v=>v.proposal_sources.exploration.source.source_digest='wrong',v=>v.proposal_sources.exploration.source.data.token='secret',
    v=>v.proposal_sources.exploration.token='secret',v=>v.proposal_sources.exploration.available=1,
    v=>v.proposals[0].token='secret',v=>v.proposals[0].proposal.token='secret',v=>v.proposals[0].acceptance_eligible=1,
    v=>v.proposals[0].acceptance_reason='secret',v=>v.proposals[0].stale_reason='secret',
    v=>v.proposals[0].request_id=true,v=>v.proposals.push(copy(v.proposals[0]))];
  for(const mutation of mutations) {
    const {flow}=harness(),value=state();value.proposals=[summary({request_id:'p',operation:'exploration',expected_revision:2,expected_draft_version:0,source_digest:'0'.repeat(64)})];
    updateInventory(value);mutation(value);assert.throws(()=>flow.load(value),/Invalid/);
  }
});

test('typed text limits count Unicode scalars and refuse lone surrogates',()=>{
  for(const character of ['a','💡']) {
    const boundary=character.repeat(65536);
    assert.equal(validExploration({...EXPL,outcome:boundary}),true);
    assert.equal(validExploration({...EXPL,outcome:boundary+character}),false);
    assert.equal(validMethod({...METHOD,reason:boundary}),true);
    assert.equal(validMethod({...METHOD,reason:boundary+character}),false);
  }
  for(const invalid of ['\ud800','\udfff','prefix\ud800x','\ud800\ud800']) {
    assert.equal(validExploration({...EXPL,outcome:invalid}),false);
    assert.equal(validMethod({...METHOD,reason:invalid}),false);
  }
});

test('Flow loads large astral capture sources and accepted Exploration without losing fields',()=>{
  const {flow}=harness(),value=state();
  const largeExpl={...EXPL,outcome:'💡'.repeat(40000)};
  value.accepted.capture.raw_text='💡'.repeat(600000);
  value.accepted.exploration=copy(largeExpl);
  value.steps.exploration={status:'saved',accepted_revision:2,evidence_id:'exploration-evidence'};
  updateSources(value);value.proposal_sources.method.source.data.exploration=copy(largeExpl);
  value.proposals=[summary({request_id:'large',operation:'exploration',expected_revision:2,expected_draft_version:0,source_digest:'0'.repeat(64)},'exploration',{proposal:copy(largeExpl)})];
  updateInventory(value);flow.load(value);
  assert.equal(flow.buffers.capture.raw_text,value.accepted.capture.raw_text);
  assert.deepEqual(flow.buffers.exploration,largeExpl);
  assert.equal(flow.status('exploration'),'saved');assert.equal(validExploration(flow.buffers.exploration),true);
  assert.equal(flow.proposals('exploration')[0].proposal.outcome,largeExpl.outcome);
});

test('capture source keeps its scalar limit and rejects malformed Unicode',()=>{
  const {flow}=harness();
  for(const character of ['a','💡']) {
    const value=state(),boundary=character.repeat(1024*1024);
    value.accepted.capture.raw_text=boundary;updateSources(value);
    assert.doesNotThrow(()=>flow.load(value));
    value.proposal_sources.exploration.source.data.capture.raw_text=boundary+character;
    assert.throws(()=>flow.load(value),/Invalid proposal source/);
  }
  for(const invalid of ['\ud800','\udfff','💡\ud800']) {
    const value=state();value.proposal_sources.exploration.source.data.capture.raw_text=invalid;
    assert.throws(()=>flow.load(value),/Invalid proposal source/);
  }
});

test('proposal persists dirty drafts before fresh source enqueue and never marks saved',async()=>{
  const {flow,writes}=harness();flow.edit('exploration',{...EXPL,outcome:'My draft'});
  assert.equal(await flow.requestProposal('exploration'),true);
  assert.deepEqual(writes.map(w=>w.operation),['draft','propose']);
  const payload=writes[1].payload;
  assert.deepEqual(Object.keys(payload).sort(),['request_id','idea_id','expected_revision','expected_draft_version','operation','source_digest'].sort());
  assert.equal(payload.expected_draft_version,1);assert.equal(payload.source_digest,'1'.repeat(64));
  assert.equal(flow.proposalPending.phase,'waiting');assert.equal(flow.status('exploration'),'current');
  assert.equal(flow.state.revision,2);assert.equal(flow.pending,null);
});

test('failed draft prevents suggestion enqueue and preserves answers',async()=>{
  const {flow,api,writes}=harness();flow.edit('exploration',EXPL);
  api.write=async()=>{throw apiError('busy',503);};
  assert.equal(await flow.requestProposal('exploration'),false);assert.deepEqual(writes,[]);
  assert.deepEqual(flow.buffers.exploration,EXPL);assert.equal(flow.proposalPending,null);
  assert.equal(flow.pending.operation,'draft');
});

test('request lost response uses explicit identical retry, no receipt reconciliation or automatic writes',async()=>{
  const h=harness(),original=h.api.write;let first=true,reconciles=0;
  h.api.reconcile=async()=>{reconciles++;throw Error('proposal must not use receipts');};
  h.api.write=async(...args)=>{const result=await original(...args);if(first){first=false;throw apiError('connection_lost',0,true);}return result;};
  assert.equal(await h.flow.requestProposal('exploration'),false);
  const pending=copy(h.flow.proposalPending);assert.equal(pending.phase,'failed');assert.equal(pending.ambiguous,true);
  await h.flow.refreshAgent();assert.equal(h.writes.length,1);assert.equal(reconciles,0);
  assert.equal(await h.flow.retryProposal(),true);assert.deepEqual(h.writes[0],h.writes[1]);assert.equal(h.requests.size,1);
});

test('reply appears by request correlation without replacing dirty buffer or choosing acceptance',async()=>{
  const h=harness();await h.flow.requestProposal('exploration');h.flow.edit('exploration',{...EXPL,outcome:'Unsent human edit'});
  h.complete();await h.flow.refreshAgent();
  assert.equal(h.flow.proposalPending,null);assert.equal(h.flow.buffers.exploration.outcome,'Unsent human edit');
  assert.deepEqual(h.flow.selectedProposals,{});assert.notEqual(h.flow.status('exploration'),'saved');
  assert.equal(h.flow.useProposal('exploration',PID),true);assert.deepEqual(h.flow.buffers.exploration,EXPL);
  assert.equal(h.flow.selectedProposals.exploration,PID);assert.equal(h.flow.status('exploration'),'unsaved');
});

test('edited-target stale response remains explicitly usable when acceptance eligible',async()=>{
  const h=harness();await h.flow.requestProposal('exploration');h.complete({stale:true,stale_reason:'stale_source'});
  await h.flow.refreshAgent();assert.equal(h.flow.useProposal('exploration',PID),true);
  h.flow.edit('exploration',{...EXPL,outcome:'Edited before acceptance'});
  assert.equal(await h.flow.save('exploration'),true);
  assert.equal(h.writes.at(-1).payload.proposal_id,PID);assert.equal(h.writes.at(-1).payload.fields.outcome,'Edited before acceptance');
});

test('method suggestion is the memory result only: never a selection, reason or cap',async()=>{
  const h=harness();await h.flow.requestProposal('method');h.complete();await h.flow.refreshAgent();
  assert.deepEqual(h.value.proposals[0].proposal,{memory:MEMORY});
  h.flow.edit('method',{...h.flow.buffers.method,reason:'Typed by the person'});
  assert.equal(h.flow.useProposal('method',PID),true);
  assert.equal(h.flow.buffers.method.selection,null);assert.equal(h.flow.buffers.method.reason,'Typed by the person');
  assert.equal(Object.hasOwn(h.flow.buffers.method,'investment'),false);
  assert.deepEqual(h.flow.buffers.method.memory,MEMORY);
  assert.equal(await h.flow.save('method'),false);
  h.flow.edit('method',{...h.flow.buffers.method,selection:'bounded-plan',reason:'I choose a different method'});
  assert.equal(await h.flow.save('method'),true);assert.equal(h.writes.at(-1).payload.fields.selection,'bounded-plan');
  assert.equal(h.writes.at(-1).payload.proposal_id,PID);
});

test('using a method suggestion keeps the persons selection and reason and replaces only the memory',async()=>{
  const h=harness();await h.flow.requestProposal('method');h.complete();await h.flow.refreshAgent();
  const older={status:'found',sources:['older-note'],rationale:'Used before',preferred_method:'experiment-led'};
  h.flow.buffers.method={selection:'appetite-led',reason:'My cap',memory:older};
  assert.equal(h.flow.useProposal('method',PID),true);
  assert.equal(h.flow.buffers.method.selection,'appetite-led');assert.equal(h.flow.buffers.method.reason,'My cap');
  assert.deepEqual(h.flow.buffers.method.memory,MEMORY);
});

test('a method proposal that carries a selection, reason or cap fails closed',()=>{
  for(const extra of [{selection:'bounded-plan'},{reason:'agent reason'},{investment:{cap:4,unit:'hours',boundary:'x'}}]) {
    const {flow}=harness(),value=state();
    value.proposals=[summary({request_id:'m',operation:'method',expected_revision:2,expected_draft_version:0,source_digest:'0'.repeat(64)},'method',{proposal:{memory:MEMORY,...extra}})];
    updateInventory(value);assert.throws(()=>flow.load(value),/Invalid/);
  }
  const {flow}=harness(),value=state();
  value.proposals=[summary({request_id:'m',operation:'method',expected_revision:2,expected_draft_version:0,source_digest:'0'.repeat(64)},'method',{proposal:copy(METHOD)})];
  updateInventory(value);assert.throws(()=>flow.load(value),/Invalid/);
});

test('discovery and exploration run the same request, use and accept flow with their own fields',async()=>{
  for(const [key,fields] of [['discovery',DISC],['exploration',EXPL]]) {
    const h=harness();await h.flow.requestProposal(key);
    assert.equal(h.writes.at(-1).payload.operation,key);
    h.complete();await h.flow.refreshAgent();assert.equal(h.flow.useProposal(key,PID),true);
    assert.deepEqual(h.flow.buffers[key],fields);assert.equal(h.flow.status(key),'unsaved');
    assert.equal(await h.flow.save(key),true);
    assert.equal(h.writes.at(-1).payload.proposal_id,PID);assert.equal(h.writes.at(-1).payload.step,key);
  }
});

test('fast resume generation change cancels outstanding request with same source/status',async()=>{
  const h=harness();await h.flow.requestProposal('exploration');const buffers=copy(h.flow.buffers);
  h.value.agent_generation='agent_'+'5'.repeat(32);await h.flow.refreshAgent();
  assert.equal(h.flow.proposalPending,null);assert.equal(h.flow.proposalError.code,'wrong_generation');
  assert.deepEqual(h.flow.buffers,buffers);assert.equal(h.writes.length,1);
});

test('source/status drift cancels requests and disables stale suggestion usage',async()=>{
  // Redesign: the human's own draft save no longer ends the terminal wait (Review ui-r1); a new
  // accepted revision or a paused agent still does.
  for(const change of [h=>{h.value.revision++;updateSources(h.value);},h=>{h.value.agent_status='paused';updateSources(h.value);}]) {
    const h=harness();await h.flow.requestProposal('exploration');change(h);await h.flow.refreshAgent();
    assert.equal(h.flow.proposalPending,null);assert.ok(['stale_source','agent_unavailable'].includes(h.flow.proposalError.code));
  }
  {const h=harness();await h.flow.requestProposal('exploration');h.value.draft_version++;updateSources(h.value);await h.flow.refreshAgent();
    assert.ok(h.flow.proposalPending,'a draft-only change keeps waiting on the terminal');}
  const h=harness();await h.flow.requestProposal('exploration');h.complete({stale:true,stale_reason:'wrong_generation',acceptance_eligible:false,acceptance_reason:'wrong_generation'});
  await h.flow.refreshAgent();assert.equal(h.flow.useProposal('exploration',PID),false);
});

test('pending deadline cancels even when state read fails, no forever pending or automatic retry',async()=>{
  const h=harness();await h.flow.requestProposal('exploration');h.advance(125001);
  h.api.state=async()=>{throw apiError('connection_lost');};await h.flow.refreshAgent();
  assert.equal(h.flow.proposalPending,null);assert.equal(h.writes.length,1);
});

test('refresh single-flight preserves dirty answers and current navigation',async()=>{
  const h=harness();h.flow.edit('exploration',{...EXPL,outcome:'Local edit'});h.flow.open('capture');
  let resolve,reads=0;h.api.state=()=>{reads++;return new Promise(done=>{resolve=done;});};
  const first=h.flow.refreshAgent(),second=h.flow.refreshAgent();
  assert.equal(reads,1);
  resolve(copy(h.value));assert.equal(await first,true);assert.equal(await second,true);
  assert.equal(h.flow.current,'capture');assert.equal(h.flow.buffers.exploration.outcome,'Local edit');
  assert.equal(h.flow.dirty.has('exploration'),true);
});

test('editing back to accepted fields remains dirty against a different saved draft',async()=>{
  for(const key of ['exploration','method']) {
    const h=harness(),accepted=copy(key==='exploration'?EXPL:METHOD),draft={...accepted,reason:'Saved draft reason',outcome:'Saved draft outcome'};
    if(key==='exploration') delete draft.reason; else delete draft.outcome;
    h.value.accepted[key]=accepted;h.value.drafts[key]=copy(draft);updateSources(h.value);h.flow.load(h.value);
    h.flow.edit(key,accepted);assert.equal(h.flow.dirty.has(key),true);
    await h.flow.refreshAgent();assert.deepEqual(h.flow.buffers[key],accepted);
    assert.equal(await h.flow.save(key,true),true);assert.deepEqual(h.value.drafts[key],accepted);
    assert.equal(h.flow.dirty.has(key),false);
  }
  const h=harness();h.value.accepted.exploration=copy(EXPL);h.value.draft={step:'exploration',fields:{...EXPL,outcome:'Selected saved draft'}};
  h.flow.load(h.value);h.flow.edit('exploration',EXPL);assert.equal(h.flow.dirty.has('exploration'),true);
  h.flow.edit('exploration',h.value.draft.fields);assert.equal(h.flow.dirty.has('exploration'),false);
});

test('unchanged polls skip rendering but still settle an expired pending suggestion',async()=>{
  const h=harness();let changes=0;h.flow.onChange=()=>{changes++;};
  await h.flow.refreshAgent();assert.equal(changes,0);
  await h.flow.requestProposal('exploration');changes=0;
  await h.flow.refreshAgent();assert.equal(changes,0);assert.ok(h.flow.proposalPending);
  h.advance(126000);await h.flow.refreshAgent();assert.equal(changes,1);
  assert.equal(h.flow.proposalError.code,'proposal_timeout');assert.equal(h.writes.length,1);
});

test('fresh correlated completed reply wins over elapsed deadline without applying its fields',async()=>{
  const h=harness();await h.flow.requestProposal('exploration');h.flow.edit('exploration',{...EXPL,outcome:'Human buffer'});
  h.complete();h.advance(126000);await h.flow.refreshAgent();
  assert.equal(h.flow.proposalPending,null);assert.equal(h.flow.proposalError,null);assert.match(h.flow.message,/Suggestion ready/);
  assert.equal(h.flow.buffers.exploration.outcome,'Human buffer');assert.deepEqual(h.flow.selectedProposals,{});
  assert.equal(h.writes.length,1);
});

test('explicit request joins an existing poll and enqueues once after its fresh state',async()=>{
  const h=harness(),original=h.api.state;let resolve,reads=0;
  h.api.state=()=>{reads++;return reads===1?new Promise(done=>{resolve=done;}):original();};
  const poll=h.flow.refreshAgent(),request=h.flow.requestProposal('exploration');assert.equal(h.writes.length,0);
  resolve(copy(h.value));assert.equal(await poll,true);assert.equal(await request,true);
  assert.equal(h.writes.length,1);assert.equal(reads,2);
});

test('explicit retry joins an existing poll and keeps its exact failed request',async()=>{
  const h=harness(),write=h.api.write;let first=true;
  h.api.write=async(...args)=>{const result=await write(...args);if(first){first=false;throw apiError('connection_lost',0,true);}return result;};
  await h.flow.requestProposal('exploration');let resolve;
  h.api.state=()=>new Promise(done=>{resolve=done;});
  const poll=h.flow.refreshAgent(),retry=h.flow.retryProposal();assert.equal(h.writes.length,1);
  resolve(copy(h.value));assert.equal(await poll,true);assert.equal(await retry,true);
  assert.deepEqual(h.writes[1],h.writes[0]);
});

test('explicit request awaiting a poll refuses changed status and invalidated mutation epoch',async()=>{
  for(const mode of ['paused','mutation','disposed','failed']) {
    const h=harness();let resolve,reject;
    h.api.state=()=>new Promise((done,fail)=>{resolve=done;reject=fail;});
    const poll=h.flow.refreshAgent(),request=h.flow.requestProposal('exploration');
    if(mode==='paused'){h.value.agent_status='paused';updateSources(h.value);}
    if(mode==='mutation')h.flow.mutationEpoch++;
    if(mode==='disposed')h.flow.dispose();
    if(mode==='failed')reject(apiError('connection_lost'));else resolve(copy(h.value));
    await poll;assert.equal(await request,false);assert.equal(h.writes.length,0);
  }
});

test('request joined to a poll invalidated by autosave reports refusal without sending a proposal',async()=>{
  const h=harness(),original=h.api.state;let first=true,resolve;
  h.api.state=()=>{if(first){first=false;return new Promise(done=>{resolve=done;});}return original();};
  const stale=copy(h.value),poll=h.flow.refreshAgent(),request=h.flow.requestProposal('exploration');
  h.flow.edit('exploration',{...EXPL,outcome:'Autosaved human answer'});
  assert.equal(await h.flow.save('exploration',true),true);
  let changes=0;h.flow.onChange=()=>{changes++;};resolve(stale);
  assert.equal(await poll,false);assert.equal(await request,false);
  assert.deepEqual(h.writes.map(write=>write.operation),['draft']);
  assert.equal(h.flow.state.draft_version,1);assert.equal(h.flow.buffers.exploration.outcome,'Autosaved human answer');
  assert.match(h.flow.message,/Suggestion was not requested/);assert.equal(changes,1);
  const disposed=harness();let done;disposed.api.state=()=>new Promise(resolve=>{done=resolve;});
  const refresh=disposed.flow.refreshAgent(),refused=disposed.flow.requestProposal('exploration');
  disposed.flow.dispose();let callbacks=0;disposed.flow.onChange=()=>{callbacks++;};done(copy(disposed.value));
  assert.equal(await refresh,false);assert.equal(await refused,false);assert.equal(callbacks,0);
  assert.equal(disposed.writes.length,0);
});

test('state read begun before mutation cannot overwrite newly accepted counters',async()=>{
  const h=harness(),original=h.api.state;let first=true,resolve;
  h.api.state=()=>{if(first){first=false;return new Promise(done=>{resolve=done;});}return original();};
  const stale=copy(h.value),refresh=h.flow.refreshAgent();
  h.flow.edit('priorities',{urgency:8,importance:9});assert.equal(await h.flow.save('priorities'),true);
  resolve(stale);assert.equal(await refresh,false);assert.equal(h.flow.state.revision,3);
});

test('background status poll runs without pending and stops on unauthorized/page disposal',async()=>{
  const h=harness(),jobs=new Map();let next=0,active=true;
  const setTimer=job=>{jobs.set(++next,job);return next;},clearTimer=id=>jobs.delete(id);
  h.flow.startAgentRefresh({active:()=>active,setTimer,clearTimer});
  const run=async()=>{const [id,job]=jobs.entries().next().value;jobs.delete(id);await job();};
  h.value.agent_status='disconnected';updateSources(h.value);await run();
  assert.equal(h.flow.state.agent_status,'disconnected');assert.equal(h.flow.proposalPending,null);
  const reads=h.reads;active=false;await run();assert.equal(h.reads,reads);
  active=true;h.api.state=async()=>{throw apiError('browser_unauthorized',401);};await run();
  assert.equal(h.flow.refreshUnauthorized,true);assert.equal(jobs.size,0);
  h.flow.refreshUnauthorized=false;h.flow.startAgentRefresh({setTimer,clearTimer});h.flow.dispose();assert.equal(jobs.size,0);
  assert.equal(await h.flow.refreshAgent(),false);
});

test('refresh skips busy and ambiguous mutations and invalid propose response remains uncertain',async()=>{
  const h=harness();h.flow.busy=true;assert.equal(await h.flow.refreshAgent(),false);assert.equal(h.reads,0);
  h.flow.busy=false;h.flow.pending={ambiguous:true};assert.equal(await h.flow.refreshAgent(),false);assert.equal(h.reads,0);
  h.flow.pending=null;h.api.write=async()=>({ok:true,code:'ok',status:'pending',request_id:'wrong'});
  assert.equal(await h.flow.requestProposal('exploration'),false);assert.equal(h.flow.proposalPending.ambiguous,true);
  assert.equal(h.flow.proposalError.code,'invalid_response');
});

test('typed Discovery/Exploration/Method validation and app module/lifecycle seam are documented in code',async()=>{
  assert.equal(validExploration(EXPL),true);assert.equal(validExploration({...EXPL,alternatives:[]}),false);
  assert.equal(validMethod(METHOD),true);assert.equal(validMethod({...METHOD,selection:null}),false);
  assert.equal(validMethod({...METHOD,investment:{cap:4,unit:'hours',boundary:'x'}}),false);
  assert.equal(validDiscovery(DISC),true);assert.equal(validDiscovery({...DISC,challenges:[]}),false);assert.equal(validDiscovery({...DISC,problem:' '}),false);
  const budget={cap:4,unit:'hours',boundary:'One lid'};
  assert.equal(validExploration(EXPL,'appetite-led'),false);assert.equal(validExploration({...EXPL,investment:budget},'appetite-led'),true);
  assert.equal(validExploration({...EXPL,investment:budget},'bounded-plan'),false);
  for(const cap of [null,0,-1,true,'4',Infinity,1e12+1]) assert.equal(validExploration({...EXPL,investment:{...budget,cap}},'appetite-led'),false);
  assert.equal(validExploration({...EXPL,assumptions:Array(1001).fill('bounded')}),false);
  const app=await readFile(new URL('app.js',web),'utf8');
  assert.match(app,/proposalInventory: \(target, key\) => renderProposalInventory\(target, key, flow\)/);
  assert.match(app,/startAgentRefresh/);assert.match(app,/document\.hidden !== true/);
  assert.match(app,/pagehide/);assert.match(app,/flow\.dispose\(\)/);
  assert.match(app,/connected && flow\.refreshUnauthorized/);
  assert.match(app,/Browser disconnected\. Pair this browser to check agent status/);
  const themeOnly=text=>text.split('\n').filter(line=>!(/localStorage/.test(line)&&line.includes('THEME_KEY'))).join('\n');
  assert.doesNotMatch(themeOnly(app)+source,/localStorage|innerHTML|agent_token|Authorization/);
});


test('same-tab reload restores explicit Exploration and Method links into accepted receipts',async()=>{
  for(const key of ['exploration','method']) {
    const saved=storage(),h=harness(saved);
    await h.flow.requestProposal(key);h.complete();await h.flow.refreshAgent();assert.equal(h.flow.useProposal(key,PID),true);
    if(key==='method') h.flow.edit(key,{...h.flow.buffers.method,selection:'bounded-plan'});
    await h.flow.save(key,true);
    const reloaded=new Flow(h.api,()=> 'after-reload-'+key,undefined,saved);reloaded.load(copy(h.value));
    assert.deepEqual(reloaded.buffers[key],h.value.drafts[key]);assert.equal(reloaded.selectedProposals[key],PID);
    assert.equal(await reloaded.save(key),true);
    assert.equal(h.writes.at(-1).payload.proposal_id,PID);
    assert.equal(h.receipts.get('after-reload-'+key).proposal_id,PID);
    assert.deepEqual(reloaded.selectedProposals,{});assert.equal(saved.entries.size,0);
  }
});

test('restored stale omitted and absent links stay pinned until explicit manual unlink',async()=>{
  for(const mode of ['stale','omitted','missing']) {
    const saved=storage(),h=harness(saved);await h.flow.requestProposal('exploration');h.complete();await h.flow.refreshAgent();
    h.flow.useProposal('exploration',PID);await h.flow.save('exploration',true);
    if(mode==='stale')Object.assign(h.value.proposals[0],{stale:true,stale_reason:'wrong_generation',acceptance_eligible:false,acceptance_reason:'wrong_generation'});
    if(mode==='omitted')Object.assign(h.value.proposals[0],{proposal:null,content_omitted:true,acceptance_eligible:false,acceptance_reason:'projection_omitted'});
    if(mode==='missing')h.value.proposals=[];
    updateInventory(h.value);
    const reloaded=new Flow(h.api,()=> 'manual-'+mode,undefined,saved);reloaded.load(copy(h.value));const count=h.writes.length;
    assert.equal(reloaded.selectedProposals.exploration,PID);assert.equal(await reloaded.save('exploration'),false);assert.equal(h.writes.length,count);
    assert.equal(saved.entries.size,1);assert.equal(reloaded.clearProposal('exploration'),true);assert.equal(saved.entries.size,0);
    assert.equal(await reloaded.save('exploration'),true);assert.equal(h.writes.at(-1).payload.proposal_id,null);
  }
});

test('selection storage isolates idea and binding and never infers selection from matching text',async()=>{
  const saved=storage(),h=harness(saved);await h.flow.requestProposal('exploration');h.complete();await h.flow.refreshAgent();
  h.flow.useProposal('exploration',PID);await h.flow.save('exploration',true);
  const other=new Flow({...h.api,bindingId:'binding_'+'7'.repeat(32)},undefined,undefined,saved);other.load(copy(h.value));
  assert.deepEqual(other.selectedProposals,{});assert.deepEqual(other.buffers.exploration,EXPL);
  const different=copy(h.value);different.idea_id='idea_'+'8'.repeat(32);different.proposals=[];updateInventory(different);
  h.flow.load(different);assert.deepEqual(h.flow.selectedProposals,{});
  h.flow.load(h.value);assert.equal(h.flow.selectedProposals.exploration,PID);
  h.api.bindingId=null;h.flow.load(h.value);assert.deepEqual(h.flow.selectedProposals,{});
});

test('storage contains only explicit proposal IDs and Use overwrites the previous selection',async()=>{
  const saved=storage(),h=harness(saved);h.api.csrf='fixture-private';h.api.cookie='fixture-cookie';
  await h.flow.requestProposal('exploration');h.complete();await h.flow.refreshAgent();h.flow.useProposal('exploration',PID);
  const key='idea-proposal-selections:'+BINDING+':'+IDEA;
  assert.deepEqual([...saved.entries],[[key,JSON.stringify({exploration:PID})]]);
  assert.doesNotMatch([...saved.entries].flat().join(' '),/fixture-private|fixture-cookie|Actual fixture words|Clean lid|fixture-safe-reference|session_/);
  const next='proposal_'+'9'.repeat(32);h.value.proposals[0].proposal_id=next;await h.flow.refreshAgent();
  h.flow.useProposal('exploration',next);assert.deepEqual(JSON.parse(saved.getItem(key)),{exploration:next});
  h.flow.clearProposal('exploration');assert.equal(saved.entries.size,0);
});

test('malformed storage is ignored and unknown valid IDs cannot grant proposal eligibility',async()=>{
  const key='idea-proposal-selections:'+BINDING+':'+IDEA;
  for(const raw of ['{bad','[]',JSON.stringify({exploration:true}),JSON.stringify({exploration:PID,token:'private'}),'x'.repeat(257)]) {
    const saved=storage();saved.setItem(key,raw);const h=harness(saved);
    assert.deepEqual(h.flow.selectedProposals,{});assert.equal(h.flow.selectionStorageAvailable,false);
  }
  const saved=storage();saved.setItem(key,JSON.stringify({exploration:PID}));const h=harness(saved);h.flow.edit('exploration',EXPL);
  assert.equal(await h.flow.save('exploration'),false);assert.equal(h.writes.length,0);assert.equal(h.flow.selectedProposals.exploration,PID);
});

test('unavailable and throwing storage preserve in-memory links and disclose reload limitation',async()=>{
  for(const saved of [null,{getItem:()=>{throw Error('private-read');},setItem:()=>{throw Error('private-write');},removeItem:()=>{throw Error('private-remove');}}]) {
    const h=harness(saved);await h.flow.requestProposal('exploration');h.complete();await h.flow.refreshAgent();
    assert.equal(h.flow.useProposal('exploration',PID),true);assert.equal(h.flow.selectedProposals.exploration,PID);
    assert.match(h.flow.message,/Reload will not reliably retain/);assert.doesNotMatch(h.flow.message,/private-read|private-write/);
    assert.equal(await h.flow.save('exploration',true),true);assert.match(h.flow.message,/Reload will not reliably retain/);
    assert.equal(h.flow.clearProposal('exploration'),true);assert.deepEqual(h.flow.selectedProposals,{});
  }
});

test('default session storage acquisition safely handles a browser security exception',()=>{
  const descriptor=Object.getOwnPropertyDescriptor(globalThis,'sessionStorage');
  try {
    Object.defineProperty(globalThis,'sessionStorage',{configurable:true,get(){throw Error('private-security-error');}});
    const h=harness(),flow=new Flow(h.api);assert.doesNotThrow(()=>flow.load(h.value));
    assert.equal(flow.selectionStorage,null);assert.deepEqual(flow.selectedProposals,{});
  } finally {
    if(descriptor)Object.defineProperty(globalThis,'sessionStorage',descriptor);else delete globalThis.sessionStorage;
  }
});

test('stale selected suggestion remains pinned until explicit manual choice',async()=>{
  const h=harness();await h.flow.requestProposal('exploration');h.complete();await h.flow.refreshAgent();h.flow.useProposal('exploration',PID);
  h.value.proposals[0].acceptance_eligible=false;h.value.proposals[0].acceptance_reason='stale_source';
  h.value.proposals[0].stale=true;h.value.proposals[0].stale_reason='stale_source';
  await h.flow.refreshAgent();assert.equal(h.flow.selectedProposals.exploration,PID);
  const writes=h.writes.length;assert.equal(await h.flow.save('exploration'),false);assert.equal(h.writes.length,writes);
  assert.deepEqual(h.flow.buffers.exploration,EXPL);
  assert.equal(h.flow.clearProposal('exploration'),true);assert.equal(await h.flow.save('exploration'),true);
  assert.equal(h.writes.at(-1).payload.proposal_id,null);
});

test('stopped or restarted in-flight poll cannot schedule a duplicate loop',async()=>{
  const h=harness(),jobs=new Map();let id=0,resolve;
  const setTimer=job=>{jobs.set(++id,job);return id;},clearTimer=value=>jobs.delete(value);
  const take=()=>{const [key,job]=jobs.entries().next().value;jobs.delete(key);return job;};
  h.api.state=()=>new Promise(done=>{resolve=done;});
  h.flow.startAgentRefresh({setTimer,clearTimer});const tick=take()();
  h.flow.stopAgentRefresh();h.flow.startAgentRefresh({setTimer,clearTimer});
  assert.equal(jobs.size,1);resolve(copy(h.value));await tick;assert.equal(jobs.size,1);
  h.flow.stopAgentRefresh();assert.equal(jobs.size,0);
});


test('bounded omitted content is explicit and cannot be copied or accepted as a linked suggestion',async()=>{
  const h=harness();await h.flow.requestProposal('exploration');
  h.complete({proposal:null,content_omitted:true,acceptance_eligible:false,acceptance_reason:'projection_omitted'});
  updateInventory(h.value,200);await h.flow.refreshAgent();
  assert.equal(h.flow.state.proposal_inventory.omitted,199);
  assert.equal(h.flow.proposalPending,null);assert.equal(h.flow.proposalError.code,'projection_omitted');
  assert.match(h.flow.message,/content is omitted/);
  const buffers=copy(h.flow.buffers);assert.equal(h.flow.useProposal('exploration',PID),false);assert.deepEqual(h.flow.buffers,buffers);
  h.flow.selectedProposals.exploration=PID;h.flow.buffers.exploration=copy(EXPL);
  const writes=h.writes.length;assert.equal(await h.flow.save('exploration'),false);assert.equal(h.writes.length,writes);
  assert.equal(h.flow.proposalError.code,'projection_omitted');
  assert.match(h.flow.message,/saved in Markdown/);assert.match(h.flow.message,/body is omitted/);
  assert.match(h.flow.message,/choose your answers as manual or request a new suggestion/);
  assert.doesNotMatch(h.flow.message,/no longer eligible|inputs changed|stale/i);
  assert.equal(h.flow.selectedProposals.exploration,PID);assert.deepEqual(h.flow.buffers.exploration,EXPL);
  const selected=harness();await selected.flow.requestProposal('exploration');selected.complete();await selected.flow.refreshAgent();
  assert.equal(selected.flow.useProposal('exploration',PID),true);
  Object.assign(selected.value.proposals[0],{proposal:null,content_omitted:true,acceptance_eligible:false,acceptance_reason:'projection_omitted'});
  updateInventory(selected.value);await selected.flow.refreshAgent();
  const before=selected.writes.length;assert.equal(await selected.flow.save('exploration'),false);assert.equal(selected.writes.length,before);
  assert.equal(selected.flow.proposalError.code,'projection_omitted');assert.match(selected.flow.message,/body is omitted/);
  assert.equal(selected.flow.selectedProposals.exploration,PID);
  assert.equal(selected.flow.clearProposal('exploration'),true);assert.equal(await selected.flow.save('exploration'),true);
  assert.equal(selected.writes.at(-1).payload.proposal_id,null);
});

test('inventory counts, omission witness and canonical paths fail closed on corruption',()=>{
  const mutations=[v=>v.proposal_inventory.total=true,v=>v.proposal_inventory.projected=2,
    v=>v.proposal_inventory.omitted=1,v=>v.proposal_inventory.content_omitted=1,
    v=>v.proposal_inventory.index_path='../private.md',v=>v.proposal_inventory.index_path='https://example.org/ideas',
    v=>v.proposal_inventory.extra='private',v=>v.proposals[0].evidence.sha256='wrong',
    v=>v.proposals[0].evidence.path='history/idea_'+'f'.repeat(32)+'/metadata/'+'a'.repeat(64)+'.md',
    v=>v.proposals[0].evidence.path='../private.md',v=>v.proposals[0].evidence.path='https://example.org/evidence.md',
    v=>v.proposals[0].evidence.token='private',v=>v.proposals[0].content_omitted=1,
    v=>v.proposals[0].proposal=null,v=>v.proposals[0].content_omitted=true,
    v=>{v.proposals[0].content_omitted=true;v.proposals[0].proposal=null;v.proposals[0].acceptance_eligible=true;},
    v=>{v.proposals[0].content_omitted=true;v.proposals[0].proposal=null;v.proposals[0].acceptance_eligible=false;v.proposals[0].acceptance_reason='stale_source';},
    v=>v.idea_id='.*'];
  for(const mutation of mutations) {
    const {flow}=harness(),value=state();value.proposals=[summary({request_id:'p',operation:'exploration',expected_revision:2,expected_draft_version:0,source_digest:'0'.repeat(64)})];
    updateInventory(value);flow.load(value);mutation(value);assert.throws(()=>flow.load(value),/Invalid/);
  }
});

test('oversized source codes keep saved fields readable and proposal request unavailable',async()=>{
  for(const code of ['source_too_large','source_projection_capacity']) {
    const h=harness();h.value.proposal_sources.exploration={available:false,code,source:null};
    assert.equal(await h.flow.refreshAgent(),true);assert.equal(h.flow.canPropose('exploration'),false);
    assert.deepEqual(h.flow.buffers.capture,CAPTURE);assert.equal(h.flow.state.revision,2);
  }
});

test('shared omission helper renders real metadata paths as text without routes or links',async()=>{
  const fromSource=text=>'data:text/javascript;base64,'+Buffer.from(text).toString('base64');
  const app=(await readFile(new URL('app.js',web),'utf8')).replace("'./api.js'",JSON.stringify(fromSource(await readFile(new URL('api.js',web),'utf8')))).replace("'./folds.js'",JSON.stringify(fromSource(source)));
  const {renderProposalInventory}=await import(fromSource(app));
  class Node {
    constructor(tag,text='') {this.tag=tag;this.textContent=text;this.children=[];}
    setAttribute(name,value) {this[name]=value;}
    append(...children) {this.children.push(...children);}
  }
  const make=(tag,text='')=>new Node(tag,text),h=harness();
  await h.flow.requestProposal('exploration');h.complete({proposal:null,content_omitted:true,acceptance_eligible:false,acceptance_reason:'projection_omitted'});
  updateInventory(h.value,150);await h.flow.refreshAgent();const body=new Node('section');
  const box=renderProposalInventory(body,'exploration',h.flow,make);
  assert.equal(box.id,'exploration-proposal-inventory');assert.equal(box.role,'status');
  const index=box.children.find(node=>node.id==='exploration-proposal-index-path');assert.match(index.textContent,new RegExp(IDEA+'\\.md'));
  const evidence=box.children.find(node=>node.id==='exploration-proposal-evidence-'+PID);assert.ok(evidence.textContent.endsWith(h.value.proposals[0].evidence.path));
  assert.match(box.children[0].textContent,/150/);assert.match(box.children[0].textContent,/149 records and 1 listed bodies omitted/);
  assert.ok(box.children.every(node=>node.tag==='p'&&node.href===undefined));
  const empty=harness();assert.equal(renderProposalInventory(new Node('section'),'exploration',empty.flow,make),null);
  empty.value.proposal_sources.exploration={available:false,code:'source_too_large',source:null};await empty.flow.refreshAgent();
  const oversized=renderProposalInventory(new Node('section'),'exploration',empty.flow,make);
  assert.ok(oversized.children.some(node=>node.textContent.includes('inputs exceed')));
});

test('a refused request that certainly did not apply never holds the human; an uncertain one still does',async()=>{
  const h=harness(),original=h.api.write;
  h.api.write=async(op,payload)=>{if(op==='propose')throw apiError('busy',503);return original(op,payload);};
  assert.equal(await h.flow.requestProposal('exploration'),false);
  assert.equal(h.flow.proposalPending.phase,'failed');assert.equal(h.flow.proposalPending.ambiguous,false);
  assert.equal(h.flow.waitingOnTerminal(),true);
  h.flow.selectAuthority=async()=>true;  // the gate is under test, not the authority round trip
  assert.equal(await h.flow.selectIdea(null),true);assert.equal(h.flow.proposalPending,null);
  const u=harness();u.api.write=async(op)=>{if(op==='propose')throw apiError('connection_lost',0,true);};
  assert.equal(await u.flow.requestProposal('exploration'),false);
  assert.equal(u.flow.proposalPending.ambiguous,true);assert.equal(u.flow.waitingOnTerminal(),false);u.flow.selectAuthority=async()=>true;
  assert.equal(await u.flow.selectIdea(null),false);assert.ok(u.flow.proposalPending);
});

test('Ideas opens while the automatic request is still being sent (the click is not lost), and holds only the in-flight send',async()=>{
  const h=harness(),original=h.api.write;let release;const gate=new Promise(r=>{release=r;});
  h.api.ideas=async()=>({total:0,backlog_revision:1,items:[]});
  h.api.write=async(op,payload)=>{if(op==='propose')await gate;return original(op,payload);};
  const sending=h.flow.requestProposal('exploration');
  for(let i=0;i<20&&!h.flow.proposalFlight;i++)await new Promise(r=>setTimeout(r,0));
  assert.equal(h.flow.busy,true);assert.equal(h.flow.proposalFlight,true);
  assert.equal(await h.flow.showIdeas(),true);assert.equal(h.flow.view,'ideas');
  release();await sending;assert.equal(h.flow.proposalPending.phase,'waiting');
});

test('Ideas still opens when an automatic request starts while the list is being read',async()=>{
  const h=harness(),original=h.api.write;let freeList,freeSend,calls=0;
  const listGate=new Promise(r=>{freeList=r;}),sendGate=new Promise(r=>{freeSend=r;});
  h.api.ideas=async()=>{calls++;if(calls===1)await listGate;return {total:0,backlog_revision:1,items:[]};};
  h.api.write=async(op,payload)=>{if(op==='propose')await sendGate;return original(op,payload);};
  const opening=h.flow.showIdeas();            // the click: the list read is in flight
  const sending=h.flow.requestProposal('exploration');
  for(let i=0;i<50&&!h.flow.proposalFlight;i++)await new Promise(r=>setTimeout(r,0));
  assert.equal(h.flow.proposalFlight,true);    // the send began and moved the epoch under the read
  freeList();assert.equal(await opening,true);assert.equal(h.flow.view,'ideas');
  freeSend();await sending;
});
