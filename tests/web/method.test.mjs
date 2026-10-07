// Actual packaged Methods render + Flow with a small DOM fixture.
// Layout/native browser qualification remains a separate product-path check.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web = new URL('../../glitch-idea/web/', import.meta.url);
const moduleUrl = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const foldsUrl = moduleUrl(await readFile(new URL('folds.js', web), 'utf8'));
const {Flow, STEPS} = await import(foldsUrl);
const {render: renderMethod} = await import(moduleUrl((await readFile(new URL('steps/method.js', web), 'utf8')).replace("'../folds.js'", JSON.stringify(foldsUrl))));
const app = (await readFile(new URL('app.js', web), 'utf8')).replace("'./folds.js'", JSON.stringify(foldsUrl))
  .replace("'./api.js'", JSON.stringify(moduleUrl(await readFile(new URL('api.js', web), 'utf8'))));
const {renderProposalInventory} = await import(moduleUrl(app));
const copy = value => structuredClone(value);
const IDEA = 'idea_'+'1'.repeat(32), SID = 'session_'+'2'.repeat(32), GEN = 'agent_'+'3'.repeat(32), PID = 'proposal_'+'4'.repeat(32);
const MEMORY = {status:'found',sources:['decision:fixture-preference'],rationale:'Agent reported a saved fixture preference',preferred_method:'adaptive-slices'};
const METHOD = {memory:MEMORY};
const CAPTURE = {raw_text:'Actual words',workspace:{name:'Fixture',path:'/fixture',confirmed:true}};
const NO_MEMORY = {status:'unavailable',sources:[],rationale:null};

class Node {
  constructor(tag,text='',className='') {this.tag=tag;this.textContent=text;this.className=className;this.children=[];this.listeners={};this.disabled=false;}
  append(...children) {this.children.push(...children);}
  setAttribute(key,value) {this[key]=value;}
  addEventListener(key,fn) {this.listeners[key]=fn;}
  async click() {if(!this.disabled) return this.listeners.click?.({target:this});}
  input(value) {if(!this.disabled) {this.value=value;return this.listeners.input?.({target:this});}}
  all() {return [this,...this.children.flatMap(child=>child.all())];}
}
const element=(tag,text='',className='')=>new Node(tag,text,className);
const button=(text,action,className='')=>{const node=element('button',text,className);node.type='button';node.addEventListener('click',action);return node;};
function harness() {
  const key = 'method';
  const state={ok:true,code:'ok',session_id:SID,idea_id:IDEA,idea_status:'active',revision:2,draft_version:0,backlog_revision:1,current_step:key,
    steps:Object.fromEntries(STEPS.map(({key})=>[key,{status:['capture','priorities'].includes(key)?'saved':key==='method'?'current':'todo',
      accepted_revision:['capture','priorities'].includes(key)?2:null,evidence_id:['capture','priorities'].includes(key)?'evidence-'+key:null}])),
    accepted:{capture:copy(CAPTURE),priorities:{urgency:6,importance:7}},drafts:{},draft:null,agent_status:'connected',agent_generation:GEN,
    capabilities:{agent:true,memory:true,uploads:false,handoff:false},resume:{required:false,reason:null},proposal_sources:{},proposals:[],
    proposal_inventory:{total:0,projected:0,omitted:0,content_omitted:0,index_path:IDEA+'.md'}};
  const writes=[],requests=new Map();let counter=0,body,foot;
  const sources=()=>{state.proposal_sources[key]=state.agent_status==='connected'?{available:true,code:'ok',source:{accepted_revision:state.revision,
    draft_version:state.draft_version,data:{capture:copy(CAPTURE),...(state.drafts[key]?{[key]:copy(state.drafts[key])}:{})},source_digest:'a'.repeat(64)}}:
    {available:false,code:'agent_unavailable',source:null};};sources();
  const api={state:async()=>copy(state),write:async(operation,payload)=>{
    writes.push({operation,payload:copy(payload)});
    if(operation==='propose') {
      if(requests.has(payload.request_id)) assert.deepEqual(requests.get(payload.request_id),payload);
      else requests.set(payload.request_id,copy(payload));
      return {ok:true,code:'ok',status:'pending',write_state:'not_applied',request_id:payload.request_id,session_id:SID,idea_id:IDEA,
        operation:payload.operation,accepted_revision:payload.expected_revision,draft_version:payload.expected_draft_version,source_digest:payload.source_digest};
    }
    if(operation==='draft') {state.draft_version++;state.drafts[payload.step]=copy(payload.fields);}
    if(operation==='accept') {
      state.idea_status='active';
      state.revision++;state.accepted[payload.step]=copy(payload.fields);delete state.drafts[payload.step];state.current_step='discovery';
      state.steps[payload.step]={status:'saved',accepted_revision:state.revision,evidence_id:'accepted-'+payload.step};
    }
    sources();return {ok:true,code:'ok',write_state:'applied',request_id:payload.request_id,idea_id:IDEA,revision:state.revision,
      draft_version:state.draft_version,backlog_revision:1,...(payload.proposal_id?{proposal_id:payload.proposal_id}:{})};
  }};
  const flow=new Flow(api,()=> 'request-'+(++counter));flow.load(state);
  let connected=true;
  const draw=()=>{
    body=element('div');foot=element('footer');
    const field=(target,label,id,value,changed,textarea=false)=>{
      const wrap=element('div'),heading=element('label',label),input=element(textarea?'textarea':'input');
      heading.htmlFor=id;input.id=id;input.value=value??'';input.disabled=flow.busy||!connected;
      input.addEventListener('input',event=>changed(event.target.value));wrap.append(heading,input);target.append(wrap);return input;
    };
    renderMethod({body,foot,flow,element,button,field,connected,edited:(key,value)=>flow.edit(key,value),handle:action=>async()=>action(),
      proposalInventory:(target,key)=>renderProposalInventory(target,key,flow,element)});
  };
  flow.onChange=draw;draw();
  return {flow,api,state,writes,sources,draw,get body(){return body;},get foot(){return foot;},
    get:id=>[...body.all(),...foot.all()].find(node=>node.id===id),connect:value=>{connected=value;draw();},
    text:()=>[...body.all(),...foot.all()].map(node=>node.textContent).join('\n'),
    complete:(changes={})=>{
      const request=[...requests.values()].at(-1);
      state.proposals=[{proposal_id:PID,request_id:request.request_id,operation:key,accepted_revision:request.expected_revision,
        draft_version:request.expected_draft_version,source_digest:request.source_digest,proposal:copy(METHOD),
        stale:false,stale_reason:null,acceptance_eligible:true,acceptance_reason:null,content_omitted:false,
        evidence:{path:'history/'+IDEA+'/metadata/'+'b'.repeat(64)+'.md',sha256:'c'.repeat(64)},...changes}];
      state.proposal_inventory={total:1,projected:1,omitted:0,content_omitted:state.proposals[0].content_omitted?1:0,index_path:IDEA+'.md'};
    }};
}

// Old shape-method.test.mjs cases dropped because Shape no longer exists: empty Shape/manual accept, archived Shape
// reactivation (x2), partial durable Shape draft, Shape request/Use/accept, Shape arrays and hostile text, Shape
// uncertain request, Shape generation drift, Shape omitted bodies, and the Shape half of the agent-drop loop.
// Dropped because investment/experiment inputs moved to Exploration: appetite/experiment requirements, reload-and-Use
// of conditional answers, and the three cap-decimal cases. Mapped below: recommendation (now memory-only Use),
// archived/unknown status, memory display, failure retry, disconnected, omission notice, agent drop after Use.

test('Method memory reply never selects a method or touches the reason; explicit choice accepts',async()=>{
  const h=harness();assert.equal(h.flow.buffers.method.selection,null);assert.equal(h.get('method-accept').disabled,true);
  await h.flow.requestProposal('method');h.complete();await h.flow.refreshAgent();
  assert.equal(h.flow.buffers.method.selection,null);
  for(const key of ['bounded-plan','adaptive-slices','appetite-led','experiment-led'])assert.equal(h.get('method-choice-'+key)['aria-pressed'],'false');
  assert.equal(h.get('method-proposal-'+PID).all().some(node=>/Recommend/i.test(node.textContent)),false);
  h.get('method-reason').input('Human reason');
  await h.get('method-use-proposal-'+PID).click();assert.equal(h.flow.buffers.method.selection,null);
  assert.equal(h.flow.buffers.method.reason,'Human reason');assert.deepEqual(h.flow.buffers.method.memory,MEMORY);
  assert.equal(h.get('method-accept').disabled,true);
  await h.get('method-choice-appetite-led').click();assert.equal(h.get('method-accept').disabled,false);
  await h.get('method-accept').click();
  assert.equal(h.writes.at(-1).payload.fields.selection,'appetite-led');assert.equal(h.writes.at(-1).payload.fields.reason,'Human reason');
  assert.equal(h.writes.at(-1).payload.proposal_id,PID);assert.equal(Object.hasOwn(h.writes.at(-1).payload.fields,'investment'),false);
});

test('archived or unknown Method cannot accept valid answers and keeps draft and memory line visible',async()=>{
  for(const status of ['archived',null]){
    const h=harness();h.flow.edit('method',{selection:'adaptive-slices',reason:'Kept',memory:copy(NO_MEMORY)});
    const fields=copy(h.flow.buffers.method),accept=h.get('method-accept');assert.equal(accept.disabled,false);
    h.flow.state.idea_status=status;
    await accept.listeners.click({target:accept});assert.equal(h.writes.length,0);
    h.draw();assert.equal(h.get('method-accept').disabled,true);await h.get('method-accept').click();
    assert.equal(h.writes.length,0);assert.deepEqual(h.flow.buffers.method,fields);assert.equal(h.flow.pending,null);
    assert.equal(h.get('method-idea-status').role,'status');assert.ok(h.get('method-memory'));
    assert.equal(h.get('method-reason').value,'Kept');assert.equal(h.get('method-reason').disabled,false);
    assert.match(h.get('method-accept-reason').textContent,status==='archived'?/archived/:/status is unavailable/);
    assert.equal(h.get('method-accept')['aria-describedby'],'method-accept-reason');
    if(status==='archived')assert.match(h.get('method-idea-status').textContent,/archived/);
    else assert.match(h.get('method-idea-status').textContent,/status is unavailable/);
  }
});

test('memory line shows exactly one mapped sentence for each of the five statuses',async()=>{
  const cases=[
    [{status:'found',sources:['decision:x'],rationale:'Saved',preferred_method:'experiment-led'},'Usually: Experiment First.'],
    [{status:'varied',sources:[],rationale:null},'You use all four. Pick freely.'],
    [{status:'searched_no_preference',sources:[],rationale:null},'Learning your preference — a few more ideas.'],
    [{status:'unavailable',sources:[],rationale:null},'Memory offline.'],
    [{status:'error',sources:[],rationale:null},'Memory offline.'],
  ];
  for(const [memory,line] of cases){
    const h=harness();h.flow.edit('method',{selection:null,reason:'',memory:copy(memory)});
    const section=h.get('method-memory');assert.match(section.className,/\bmemory-line\b/);
    assert.equal(section.children[0].textContent,'Your Glitch remembers');
    assert.equal(section.children.length,2);assert.equal(section.children[1].textContent,line);
    assert.equal(section.all().some(node=>['input','textarea','a','button'].includes(node.tag)),false);
    assert.equal(h.text().includes('decision:x'),false);
  }
  const h=harness();assert.equal(h.get('method-memory').children[1].textContent,'Memory offline.');
});

test('the reason is optional, helped, and never blocks Accept; no budget or experiment inputs render',async()=>{
  const h=harness();await h.get('method-choice-bounded-plan').click();
  assert.equal(h.get('method-reason').tag,'textarea');
  assert.ok(h.body.all().some(node=>node.textContent==='Why this method? Optional'));
  assert.ok(h.body.all().some(node=>node.textContent==='Helps /glitch-plan understand the choice, and teaches your Glitch your preference over time.'));
  assert.equal(h.get('method-reason').value,'');assert.equal(h.get('method-reason').required,undefined);
  assert.equal(h.get('method-accept').disabled,false);assert.equal(h.get('method-accept-reason'),undefined);assert.equal(h.get('method-accept').title,undefined);
  for(const key of ['experiment-led','appetite-led','adaptive-slices']){
    await h.get('method-choice-'+key).click();
    const ids=h.body.all().map(node=>node.id||'');
    assert.equal(ids.some(id=>/^method-(investment|experiment)/.test(id)),false,key);
    assert.equal(h.body.all().filter(node=>node.tag==='input'||node.tag==='textarea').length,1,'only the reason field');
  }
  await h.get('method-accept').click();
  const fields=h.writes.at(-1).payload.fields;assert.equal(h.writes.at(-1).operation,'accept');
  assert.deepEqual(Object.keys(fields).sort(),['memory','reason','selection']);
});

test('disabled Accept names what is missing and ties it to the button',async()=>{
  const h=harness();const accept=h.get('method-accept');assert.equal(accept.disabled,true);
  assert.equal(h.get('method-accept-reason').textContent,'Choose a method.');
  assert.match(h.get('method-accept-reason').className,/\baccept-reason\b/);
  assert.equal(accept['aria-describedby'],'method-accept-reason');assert.equal(accept.title,'Choose a method.');
});

test('Method failure displays redacted status and explicit exact retry, then stop waiting',async()=>{
  const h=harness(),original=h.api.write;let first=true;
  h.api.write=async(...args)=>{const result=await original(...args);if(first){first=false;throw Object.assign(new Error('private diagnostic'),{code:'connection_lost',uncertain:true});}return result;};
  await h.flow.requestProposal('method');assert.match(h.get('method-proposal-status').textContent,/may have reached/);
  assert.equal(h.get('method-proposal-error').role,'status');assert.equal(h.body.all().some(node=>node.textContent.includes('private diagnostic')),false);
  await h.get('method-retry').click();assert.deepEqual(h.writes[0],h.writes[1]);assert.equal(h.get('method-reason').disabled,false);
  await h.get('method-stop-waiting').click();assert.equal(h.flow.proposalPending,null);assert.equal(h.writes.length,2);
});

test('agent drop after Use: one explicit foot button accepts my own answers and unlinks the memory result',async()=>{
  const h=harness();await h.flow.requestProposal('method');h.complete();await h.flow.refreshAgent();
  await h.get('method-use-proposal-'+PID).click();await h.get('method-choice-bounded-plan').click();
  h.get('method-reason').input('My reason');
  assert.equal(h.get('method-accept-own'),undefined,'no extra door while the memory result is acceptable');
  h.state.agent_status='disconnected';h.state.capabilities.agent=false;h.state.resume={required:true,reason:'agent_disconnected'};
  h.state.proposals[0]={...h.state.proposals[0],acceptance_eligible:false,acceptance_reason:'agent_unavailable'};h.sources();await h.flow.refreshAgent();
  assert.equal(h.get('method-accept').disabled,true);
  const own=h.get('method-accept-own');assert.ok(own,'the door sits beside Accept');assert.equal(own.disabled,false);
  assert.match(h.get('method-accept-own-reason').textContent,/can no longer be accepted.*kept/);
  await own.click();
  const accepted=h.writes.filter(write=>write.operation==='accept');
  assert.equal(accepted.length,1);assert.equal(accepted[0].payload.proposal_id,null,'accepted as my own, not linked');
  assert.equal(accepted[0].payload.step,'method');assert.equal(accepted[0].payload.fields.selection,'bounded-plan','the human choice');
  assert.equal(accepted[0].payload.fields.reason,'My reason');assert.equal(h.flow.selectedProposals.method,undefined);
});

test('disconnected previously used Method memory keeps own choice editable and Accept needs the explicit own-answers door',async()=>{
  const h=harness();await h.flow.requestProposal('method');h.complete();await h.flow.refreshAgent();
  await h.get('method-use-proposal-'+PID).click();await h.get('method-choice-bounded-plan').click();
  h.state.agent_status='disconnected';h.state.capabilities.agent=false;h.state.capabilities.memory=false;
  h.state.resume={required:true,reason:'agent_disconnected'};h.sources();
  Object.assign(h.state.proposals[0],{stale:true,stale_reason:'agent_unavailable',acceptance_eligible:false,acceptance_reason:'agent_unavailable'});
  await h.flow.refreshAgent();assert.equal(h.flow.canPropose('method'),false);assert.equal(h.get('method-use-proposal-'+PID).disabled,true);
  assert.equal(h.get('method-accept').disabled,true);assert.equal(h.get('method-reason').disabled,false);
  await h.get('method-manual').click();assert.equal(h.get('method-accept').disabled,false);
  await h.get('method-accept').click();assert.equal(h.writes.at(-1).payload.proposal_id,null);
  h.connect(false);assert.equal(h.get('method-reason').disabled,true);assert.equal(h.get('method-accept').disabled,true);
});

test('Method omission notice uses shared immutable paths and disables absent body use',async()=>{
  const h=harness();await h.flow.requestProposal('method');h.complete({proposal:null,content_omitted:true,acceptance_eligible:false,acceptance_reason:'projection_omitted'});
  await h.flow.refreshAgent();assert.equal(h.get('method-use-proposal-'+PID).disabled,true);
  assert.equal(h.get('method-proposal-inventory').role,'status');assert.ok(h.get('method-proposal-evidence-'+PID));
  assert.equal(h.body.all().some(node=>node.href),false);assert.equal(h.flow.buffers.method.selection,null);
});

test('Method step never locks and offers no hand-fill control',async()=>{
  const h=harness();
  assert.equal(h.get('method-hand-fill'),undefined);
  assert.equal(h.body.all().some(node=>/field-locked/.test(node.className)||/Waiting for your terminal/.test(node.textContent)),false);
  assert.equal(h.get('method-choice-bounded-plan').disabled,false);assert.equal(h.get('method-reason').disabled,false);
});
