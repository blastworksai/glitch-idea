// Actual controller, fixed provider fixtures; no server/browser claim.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../../glitch-idea/web/folds.js',import.meta.url),'utf8');
const {Flow,STEPS,validAssess,assessFields,assessmentFields,positionFields,assessmentScore,insertionNeighbors}=
  await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const copy=value=>structuredClone(value);
const IDEA='idea_'+'1'.repeat(32),OTHER='idea_'+'2'.repeat(32),THIRD='idea_'+'3'.repeat(32);
const SID='session_'+'4'.repeat(32),GEN='agent_'+'5'.repeat(32),PID='proposal_'+'6'.repeat(32),BINDING='binding_'+'7'.repeat(32);
const CAPTURE={raw_text:'Fixture only',workspace:{name:'Explicit',path:'/fixture',confirmed:true}};
const DISCOVERY={problem:'Drafts are unclear',audience:'Owners',workaround:'Rewrite by hand',evidence:'Three complaints',kill_criteria:'Nobody asks again',challenges:[{challenge:'Is it real?',response:'Yes, seen twice'}]};
const EXPLORATION={outcome:'Clear draft',alternatives:[{route:'Keep it',reason:'Simpler'}],assumptions:[],scope:'small-change',scope_reason:'One field',next_slice:'Check it',learning:[],investment:null,experiment:null,sketch:[{title:'Clear field',why_next:'Smallest step',done_when:'Field reads well',method:null}]};
const rating={urgency:6,importance:7,actor:'Operator',timestamp:'fixture-only'};
const assessment=()=>({method:'wsjf',version:'v1',inputs:{value:8,time_criticality:4,enablement:2,effort:2},basis:'Fixture evidence',assumptions:[],confidence:'low',provenance:'Current agent'});
const proposal=()=>({assessment:assessment(),position:{proposed_position:1,actual_position:1,neighbors:{before:null,after:OTHER},override_reason:null}});
function update(state) {
  const target=state.backlog?.comparisons.find(item=>item.idea_id===IDEA);
  if(target){target.revision=state.revision;target.ratings=copy(state.human_ratings);target.assessment=copy(state.assessment_summary);}
  const ready=state.agent_status==='connected'&&state.backlog_status.available;
  state.proposal_sources.assessment=ready?{available:true,code:'ok',source:{accepted_revision:state.revision,draft_version:state.draft_version,
    data:{steps:{capture:copy(state.accepted.capture),priorities:copy(state.accepted.priorities),discovery:copy(state.accepted.discovery),exploration:copy(state.accepted.exploration)},
      backlog:copy(state.backlog),target:copy(state.drafts.assess??state.accepted.assess??null)},source_digest:'a'.repeat(64)}}:
    {available:false,code:state.agent_status==='connected'?'source_too_large':'agent_unavailable',source:null};
  state.proposal_inventory={total:state.proposals.length,projected:state.proposals.length,omitted:0,
    content_omitted:state.proposals.filter(p=>p.content_omitted).length,index_path:IDEA+'.md'};
}
function initial() {
  const state={ok:true,code:'ok',session_id:SID,idea_id:IDEA,revision:4,draft_version:0,backlog_revision:9,current_step:'assess',
    steps:Object.fromEntries(STEPS.map(({key})=>[key,{status:['capture','priorities','discovery','exploration','method','visualize'].includes(key)?'saved':key==='assess'?'current':'todo',
      accepted_revision:['capture','priorities','discovery','exploration','method','visualize'].includes(key)?4:null,evidence_id:['capture','priorities','discovery','exploration','method','visualize'].includes(key)?'evidence-'+key:null}])),
    accepted:{capture:copy(CAPTURE),priorities:{urgency:6,importance:7},discovery:copy(DISCOVERY),exploration:copy(EXPLORATION)},drafts:{},draft:null,
    agent_status:'connected',agent_generation:GEN,proposal_sources:{},proposals:[],human_ratings:copy(rating),assessment_summary:null,
    backlog_status:{available:true,code:'ok'},backlog:{revision:9,order:[IDEA,OTHER,THIRD],comparisons:[IDEA,OTHER,THIRD].map(idea_id=>
      ({idea_id,revision:idea_id===IDEA?4:1,status:'active',ratings:idea_id===IDEA?copy(rating):null,assessment:null}))}};
  update(state);return state;
}
function harness(storage=null) {
  const state=initial(),writes=[],receipts=new Map(),faults={},reads=[],reconciles=[];let count=0,now=0;
  const api={bindingId:BINDING,state:async id=>{
      reads.push(id);
      if(faults.readFailures>0){faults.readFailures--;throw Object.assign(new Error('read_lost'),{uncertain:true});}
      return faults.foreignState?{...copy(state),idea_id:OTHER}:copy(state);
    },reconcile:async(id,idea)=>{reconciles.push({id,idea});return {result:copy(receipts.get(id)??null),state:await api.state(idea)};},
    write:async(operation,payload)=>{
      writes.push({operation,payload:copy(payload)});
      if(operation==='propose'&&faults.proposeError)throw Object.assign(new Error(faults.proposeError),{code:faults.proposeError,status:409});
      if(operation==='propose')return {ok:true,code:'ok',status:'pending',write_state:'not_applied',request_id:payload.request_id,session_id:SID,
        idea_id:IDEA,operation:payload.operation,accepted_revision:payload.expected_revision,draft_version:payload.expected_draft_version,source_digest:payload.source_digest};
      if(operation==='draft'){state.draft_version++;state.drafts[payload.step]=copy(payload.fields);}
      if(operation==='accept'){
        assert.equal(payload.expected_backlog_revision,state.backlog_revision);
        state.revision++;state.backlog_revision++;state.backlog.revision=state.backlog_revision;
        state.accepted.assess=copy(payload.fields);delete state.drafts.assess;
        state.assessment_summary={...copy(payload.fields.assessment),score:assessmentScore(payload.fields.assessment),actor:'Operator'};
        const order=state.backlog.order.filter(id=>id!==IDEA);order.splice(payload.fields.position.actual_position-1,0,IDEA);state.backlog.order=order;
        state.backlog.comparisons=order.map(id=>state.backlog.comparisons.find(item=>item.idea_id===id));
        state.steps.assess={status:'saved',accepted_revision:state.revision,evidence_id:'assess-evidence'};
        state.proposals=state.proposals.map(p=>({...p,acceptance_eligible:false,acceptance_reason:'stale_source',stale:true,stale_reason:'stale_source'}));
      }
      if(operation==='accept'&&faults.historical)faults.historical(state);
      update(state);let result={ok:true,code:'ok',write_state:'applied',request_id:payload.request_id,idea_id:IDEA};
      if(faults.badReceipt)result=faults.badReceipt(result);
      receipts.set(payload.request_id,result);
      if(faults.loseAck)throw Object.assign(new Error('lost_ack'),{uncertain:true});
      return result;
    }};
  const flow=new Flow(api,()=> 'request-'+(++count),()=>now,storage);flow.load(state);
  const complete=(overrides={})=>{
    const request=writes.findLast(w=>w.operation==='propose').payload;
    state.proposals=[{proposal_id:PID,request_id:request.request_id,operation:'assessment',accepted_revision:request.expected_revision,
      draft_version:request.expected_draft_version,source_digest:request.source_digest,proposal:proposal(),stale:false,stale_reason:null,
      acceptance_eligible:true,acceptance_reason:null,content_omitted:false,evidence:{path:'history/'+IDEA+'/metadata/'+'b'.repeat(64)+'.md',sha256:'c'.repeat(64)},...overrides}];update(state);
  };
  return {flow,api,state,writes,receipts,faults,reads,reconciles,complete,advance:ms=>now+=ms};
}

test('Assess validators retain decimal WSJF/RICE, null unknown and categorical Kano',()=>{
  const fields=proposal();assert.equal(validAssess(fields),true);assert.equal(assessmentScore(fields.assessment),7);
  fields.assessment.inputs.effort=2.5;assert.equal(assessmentScore(fields.assessment),5.6);
  fields.assessment.inputs.value=null;assert.equal(validAssess(fields),true);assert.equal(assessmentScore(fields.assessment),null);
  fields.assessment={...assessment(),method:'rice',inputs:{reach:100,impact:2,confidence:0.5,effort:5}};
  assert.equal(assessmentScore(fields.assessment),20);
  fields.assessment={...assessment(),method:'kano',inputs:{category:'delighter',hypothesis:true}};
  assert.equal(validAssess(fields),true);assert.equal(assessmentScore(fields.assessment),null);
  fields.assessment.score=99;assert.equal(validAssess(fields),false);
  assert.equal(assessFields({assessment:{method:'rice',inputs:{confidence:null}}},true),true);
  assert.equal(assessmentFields({...assessment(),inputs:{...assessment().inputs,effort:0}}),false);
  assert.equal(positionFields({...proposal().position,actual_position:2,override_reason:''}),false);
});

test('actual neighbors remove the target without sorting scores or order',()=>{
  const order=[IDEA,OTHER,THIRD];assert.deepEqual(insertionNeighbors(order,IDEA,3),{before:THIRD,after:null});
  assert.deepEqual(order,[IDEA,OTHER,THIRD]);assert.equal(insertionNeighbors(order,IDEA,4),null);
});

test('complete specialized source and backlog fail closed on malformed projections',()=>{
  const mutations=[s=>s.backlog.order.push(IDEA),s=>s.backlog.comparisons.pop(),s=>s.backlog.comparisons.reverse(),
    s=>s.backlog.revision++,s=>s.human_ratings.urgency=2,s=>s.assessment_summary={...assessment(),score:99},
    s=>s.proposal_sources.assessment.source.data.token='private',s=>s.proposal_sources.assessment.source.data.steps.priorities.urgency=2,
    s=>s.proposal_sources.assessment.source.data.target={assessment:{score:1}},s=>delete s.human_ratings,
    s=>s.proposal_sources.assessment.source.data.backlog.order.reverse()];
  for(const mutate of mutations){const state=initial();mutate(state);assert.throws(()=>new Flow({}).load(state),/Invalid/);}
});

test('no-selection and explicit capacity absence preserve ordinary saved-state reads',()=>{
  const state=initial();state.backlog=null;state.backlog_status={available:false,code:'source_too_large'};update(state);
  const flow=new Flow({});flow.load(state);assert.equal(flow.canPropose('assess'),false);
  state.idea_id=null;state.revision=0;state.human_ratings=null;state.assessment_summary=null;state.proposals=[];
  state.backlog_status={available:false,code:'no_selection'};state.proposal_sources={};state.proposal_inventory={total:0,projected:0,omitted:0,content_omitted:0,index_path:null};
  flow.load(state);assert.equal(flow.state.backlog,null);
});

test('Assess maps to assessment and Use remains a draft until explicit acceptance',async()=>{
  const h=harness();assert.equal(await h.flow.requestProposal('assess'),true);assert.equal(h.writes[0].payload.operation,'assessment');
  h.complete();await h.flow.refreshAgent();assert.equal(h.flow.proposals('assess').length,1);assert.equal(h.flow.useProposal('assess',PID),true);
  assert.equal(h.state.revision,4);assert.equal(h.writes.filter(w=>w.operation==='accept').length,0);
  const fields=copy(h.flow.buffers.assess);fields.assessment.inputs.value=10;fields.position.actual_position=3;
  fields.position.neighbors=insertionNeighbors(h.state.backlog.order,IDEA,3);fields.position.override_reason='Operator chose last';h.flow.edit('assess',fields);
  assert.equal(await h.flow.save('assess'),true);const payload=h.writes.at(-1).payload;
  assert.equal(payload.step,'assess');assert.equal(payload.proposal_id,PID);assert.equal(payload.expected_backlog_revision,9);
  assert.equal(payload.fields.assessment.inputs.value,10);assert.deepEqual(h.state.backlog.order,[OTHER,THIRD,IDEA]);
  assert.deepEqual(h.state.human_ratings,rating);
  assert.deepEqual(Object.keys(payload.fields).sort(),['assessment','position']);
  for(const key of ['urgency','importance','ratings'])assert.equal(Object.hasOwn(payload.fields,key),false);
  assert.equal(h.flow.pending,null);assert.equal(h.flow.status('assess'),'saved');
});

test('accepted Assess requires complete fields while independent draft remains partial',()=>{
  for(const fields of [{},{assessment:assessment()}, {...proposal(),position:{}}]){
    const state=initial();state.accepted.assess=fields;update(state);
    assert.throws(()=>new Flow({}).load(state),/Invalid assessment fields/);
  }
  const state=initial();state.drafts.assess={assessment:{method:'rice',inputs:{reach:null}}};update(state);
  const flow=new Flow({});flow.load(state);assert.deepEqual(flow.buffers.assess,state.drafts.assess);
  const legacy=initial();for(const key of ['backlog','backlog_status','human_ratings','assessment_summary'])delete legacy[key];
  legacy.proposal_sources={};legacy.accepted.assess={};
  assert.throws(()=>new Flow({}).load(legacy),/Invalid assessment fields/);
});

test('Assess malformed receipt retains ambiguity and reads only submitted idea without foreign storage',async()=>{
  for(const mutate of [r=>({...r,request_id:'wrong-request'}),r=>({...r,idea_id:OTHER}),
      r=>({...r,code:'invented'}),r=>({...r,write_state:'invented'})]){
    const foreignScope='idea-proposal-selections:'+BINDING+':'+OTHER,foreignSelection=JSON.stringify({assess:PID});
    const data=new Map([[foreignScope,foreignSelection]]),storageCalls=[];
    const storage={getItem:key=>{storageCalls.push(key);return data.get(key)??null;},
      setItem:(key,value)=>{storageCalls.push(key);data.set(key,value);},removeItem:key=>{storageCalls.push(key);data.delete(key);}};
    const h=harness(storage);h.faults.badReceipt=mutate;h.flow.edit('assess',proposal());
    assert.equal(await h.flow.save('assess'),false);
    assert.equal(h.flow.error.code,'invalid_response');assert.equal(h.flow.pending.ambiguous,true);
    assert.deepEqual(h.flow.buffers.assess,proposal());assert.equal(h.flow.current,'assess');
    assert.ok(h.reads.every(id=>id===IDEA));assert.ok(h.reconciles.every(r=>r.idea===IDEA));
    assert.equal(h.reads.length,1);assert.equal(h.reconciles.length,1);
    assert.ok(storageCalls.length>0);assert.ok(storageCalls.every(key=>!key.includes(OTHER)));
    assert.equal(data.get(foreignScope),foreignSelection);assert.equal(h.flow.state.idea_id,IDEA);
    assert.equal(h.writes.length,1);assert.equal(await h.flow.reloadKeepingAnswers(),false);
  }
});

test('Assess foreign authority response stays ambiguous without loading foreign state',async()=>{
  const h=harness();h.faults.foreignState=true;h.flow.edit('assess',proposal());
  assert.equal(await h.flow.save('assess'),false);assert.equal(h.flow.error.code,'invalid_response');
  assert.equal(h.flow.state.idea_id,IDEA);assert.equal(h.flow.state.revision,4);
  assert.equal(h.flow.pending.ambiguous,true);assert.ok(h.reads.every(id=>id===IDEA));
  assert.deepEqual(h.flow.buffers.assess,proposal());assert.equal(h.writes.length,1);
});

test('Assess historical fields or review status clear pending as known saved-state conflict',async()=>{
  for(const kind of ['fields','review'])for(const recovered of [false,true]){
    const h=harness();h.flow.edit('assess',proposal());h.faults.loseAck=recovered;
    h.faults.historical=state=>{
      if(kind==='fields'){state.accepted.assess.assessment.basis='Later acceptance';state.assessment_summary.basis='Later acceptance';}
      else state.steps.assess.status='review-needed';
    };
    assert.equal(await h.flow.save('assess'),false);assert.equal(h.flow.error.code,'saved_state_changed');
    assert.equal(h.flow.pending,null);assert.equal(h.flow.dirty.has('assess'),true);
    assert.deepEqual(h.flow.buffers.assess,proposal());assert.equal(h.flow.current,'assess');
    assert.equal(h.flow.status('assess'),'unsaved');assert.equal(h.writes.length,1);
    assert.equal(await h.flow.reloadKeepingAnswers(),true);assert.deepEqual(h.flow.buffers.assess,proposal());
  }
});

test('Assess newer answers survive receipt reconciliation without advancing',async()=>{
  const h=harness();h.faults.readFailures=2;h.flow.edit('assess',proposal());
  assert.equal(await h.flow.save('assess'),false);const payload=copy(h.flow.pending.payload);
  const newer=proposal();newer.assessment.basis='Newer human answer';h.flow.edit('assess',newer);
  assert.equal(await h.flow.retry(),true);assert.equal(h.writes.length,1);
  assert.deepEqual(h.writes[0].payload,payload);assert.equal(h.flow.pending,null);
  assert.deepEqual(h.flow.buffers.assess,newer);assert.equal(h.flow.dirty.has('assess'),true);
  assert.equal(h.flow.current,'assess');
});

test('server stale_backlog suggestion refusal cancels pending without retry or answer loss',async()=>{
  const h=harness();h.faults.proposeError='stale_backlog';
  const before=copy(h.flow.buffers.assess);
  assert.equal(await h.flow.requestProposal('assess'),false);assert.equal(h.writes.length,1);
  assert.equal(h.flow.proposalPending,null);assert.equal(h.flow.proposalError.code,'stale_backlog');
  assert.equal(await h.flow.retryProposal(),false);assert.equal(h.writes.length,1);
  assert.equal(h.flow.state.backlog_revision,9);
  assert.deepEqual(h.flow.buffers.assess,before);
});

test('draft autosave before requesting uses the durable target and observed counters',async()=>{
  const h=harness();h.flow.edit('assess',{assessment:{method:'wsjf',inputs:{value:2.5}}});
  assert.equal(await h.flow.requestProposal('assess'),true);
  assert.equal(h.writes[0].operation,'draft');assert.equal(h.writes[1].payload.expected_draft_version,1);
  assert.equal(h.flow.proposalPending.source.data.target.assessment.inputs.value,2.5);
});

test('linked original proposed position stays fixed while actual override is editable',async()=>{
  const h=harness();await h.flow.requestProposal('assess');h.complete();await h.flow.refreshAgent();h.flow.useProposal('assess',PID);
  const fields=copy(h.flow.buffers.assess);fields.position.proposed_position=2;fields.position.actual_position=2;
  fields.position.neighbors=insertionNeighbors(h.state.backlog.order,IDEA,2);h.flow.edit('assess',fields);
  const count=h.writes.length;assert.equal(await h.flow.save('assess'),false);assert.equal(h.writes.length,count);
  assert.equal(h.flow.proposalError.code,'proposal_mismatch');assert.equal(h.flow.buffers.assess.position.actual_position,2);
});

test('backlog drift cancels pending reply and preserves edited answers without sorting',async()=>{
  const h=harness();await h.flow.requestProposal('assess');h.flow.edit('assess',proposal());const before=copy(h.flow.buffers.assess);
  h.state.backlog_revision++;h.state.backlog.revision++;h.state.backlog.order=[OTHER,IDEA,THIRD];
  h.state.backlog.comparisons=h.state.backlog.order.map(id=>h.state.backlog.comparisons.find(item=>item.idea_id===id));update(h.state);
  await h.flow.refreshAgent();assert.equal(h.flow.proposalPending,null);assert.equal(h.flow.proposalError.code,'stale_source');
  assert.deepEqual(h.flow.buffers.assess,before);assert.deepEqual(h.state.backlog.order,[OTHER,IDEA,THIRD]);
});

test('selection survives edit/autosave/reload and stale refusal requires explicit unlink',async()=>{
  const data=new Map(),storage={getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)};
  const h=harness(storage);await h.flow.requestProposal('assess');h.complete();await h.flow.refreshAgent();h.flow.useProposal('assess',PID);
  const fields=copy(h.flow.buffers.assess);fields.assessment.inputs.value=12;h.flow.edit('assess',fields);await h.flow.save('assess',true);
  const reloaded=new Flow(h.api,()=> 'reload-request',()=>0,storage);reloaded.load(copy(h.state));assert.equal(reloaded.selectedProposals.assess,PID);
  h.state.proposals[0].acceptance_eligible=false;h.state.proposals[0].acceptance_reason='stale_source';update(h.state);reloaded.load(copy(h.state),true);
  const writes=h.writes.length;assert.equal(await reloaded.save('assess'),false);assert.equal(h.writes.length,writes);
  assert.equal(reloaded.clearProposal('assess'),true);assert.equal(reloaded.selectedProposals.assess,undefined);
  assert.equal(reloaded.buffers.assess.assessment.inputs.value,12);
});

test('omitted assessment body cannot be used or silently accepted as manual',async()=>{
  const h=harness();await h.flow.requestProposal('assess');h.complete({content_omitted:true,proposal:null,acceptance_eligible:false,acceptance_reason:'projection_omitted'});
  await h.flow.refreshAgent();assert.equal(h.flow.useProposal('assess',PID),false);
  h.flow.edit('assess',proposal());h.flow.selectedProposals.assess=PID;assert.equal(await h.flow.save('assess'),false);
  assert.equal(h.flow.proposalError.code,'projection_omitted');
});

test('stale acceptance CAS retains draft/pending payload for explicit resolution',async()=>{
  const h=harness();h.flow.edit('assess',proposal());h.api.write=async(operation,payload)=>{h.writes.push({operation,payload:copy(payload)});throw Object.assign(new Error('stale_backlog'),{code:'stale_backlog',status:409});};
  assert.equal(await h.flow.save('assess'),false);assert.equal(h.flow.pending.payload.expected_backlog_revision,9);
  assert.deepEqual(h.flow.buffers.assess,proposal());assert.equal(h.flow.isConflict(),true);assert.equal(h.writes.length,1);
});

test('lost proposal response only retries explicitly with identical assessment envelope',async()=>{
  const h=harness(),write=h.api.write;let first=true;
  h.api.write=async(...args)=>{const result=await write(...args);if(first){first=false;throw Object.assign(new Error('lost'),{code:'connection_lost',uncertain:true});}return result;};
  assert.equal(await h.flow.requestProposal('assess'),false);const pending=copy(h.flow.proposalPending.payload);
  assert.equal(h.writes.length,1);assert.equal(await h.flow.retryProposal(),true);
  assert.deepEqual(h.writes[1].payload,pending);assert.equal(h.writes.length,2);
});

test('malformed score, agent override and target-self neighbor proposals refuse',async()=>{
  for(const mutate of [p=>p.assessment.score=7,p=>p.position.actual_position=2,p=>p.position.override_reason='AI override',p=>p.position.neighbors.before=IDEA]) {
    const h=harness();await h.flow.requestProposal('assess');h.complete();mutate(h.state.proposals[0].proposal);
    assert.throws(()=>h.flow.load(copy(h.state)),/Invalid/);
  }
});

test('older fixtures without assessment projection remain readable',()=>{
  const state=initial();for(const key of ['backlog','backlog_status','human_ratings','assessment_summary'])delete state[key];
  state.proposal_sources={};new Flow({}).load(state);
});
