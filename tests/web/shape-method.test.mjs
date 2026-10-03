// Actual packaged Shape/Method renders + Flow with a small DOM fixture.
// Layout/native browser qualification remains a separate product-path check.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web = new URL('../../glitch-idea/web/', import.meta.url);
const moduleUrl = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const foldsUrl = moduleUrl(await readFile(new URL('folds.js', web), 'utf8'));
const {Flow, STEPS} = await import(foldsUrl);
const {render} = await import(moduleUrl((await readFile(new URL('steps/shape.js', web), 'utf8')).replace("'../folds.js'", JSON.stringify(foldsUrl))));
const {render: renderMethod} = await import(moduleUrl((await readFile(new URL('steps/method.js', web), 'utf8')).replace("'../folds.js'", JSON.stringify(foldsUrl))));
const app = (await readFile(new URL('app.js', web), 'utf8')).replace("'./folds.js'", JSON.stringify(foldsUrl))
  .replace("'./api.js'", JSON.stringify(moduleUrl(await readFile(new URL('api.js', web), 'utf8'))));
const {renderProposalInventory} = await import(moduleUrl(app));
const copy = value => structuredClone(value);
const IDEA = 'idea_'+'1'.repeat(32), SID = 'session_'+'2'.repeat(32), GEN = 'agent_'+'3'.repeat(32), PID = 'proposal_'+'4'.repeat(32);
const SHAPE = {outcome:'Human outcome',scope:'small-change',scope_reason:'One change',
  alternatives:[{route:'Keep the original route',reason:'Its stated reason'}],assumptions:[],next_slice:'One next slice',learning:[]};
const METHOD = {selection:'appetite-led',reason:'Reported recommendation',investment:{cap:4,unit:'hours',boundary:'Proposed boundary'},experiment:null,
  memory:{status:'found',sources:['decision:fixture-preference'],rationale:'Agent reported a saved fixture preference'}};
const CAPTURE = {raw_text:'Actual words',workspace:{name:'Fixture',path:'/fixture',confirmed:true}};

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
function harness(key = 'shape') {
  const state={ok:true,code:'ok',session_id:SID,idea_id:IDEA,idea_status:'active',revision:2,draft_version:0,backlog_revision:1,current_step:key,
    steps:Object.fromEntries(STEPS.map(({key})=>[key,{status:['capture','priorities'].includes(key)?'saved':key==='shape'?'current':'todo',
      accepted_revision:['capture','priorities'].includes(key)?2:null,evidence_id:['capture','priorities'].includes(key)?'evidence-'+key:null}])),
    accepted:{capture:copy(CAPTURE),priorities:{urgency:6,importance:7}},drafts:{},draft:null,agent_status:'connected',agent_generation:GEN,
    capabilities:{agent:true,memory:true,uploads:false,handoff:false},resume:{required:false,reason:null},proposal_sources:{},proposals:[],
    proposal_inventory:{total:0,projected:0,omitted:0,content_omitted:0,index_path:IDEA+'.md'}};
  if (key === 'method') {
    state.accepted.shape=copy(SHAPE);state.steps.shape={status:'saved',accepted_revision:2,evidence_id:'accepted-shape'};
    state.steps.method={status:'current',accepted_revision:null,evidence_id:null};
  }
  const writes=[],requests=new Map();let counter=0,body,foot;
  const sources=()=>{state.proposal_sources[key]=state.agent_status==='connected'?{available:true,code:'ok',source:{accepted_revision:state.revision,
    draft_version:state.draft_version,data:{capture:copy(CAPTURE),...(key==='method'?{shape:copy(SHAPE)}:{}),
      ...(state.drafts[key]?{[key]:copy(state.drafts[key])}:{})},source_digest:'a'.repeat(64)}}:
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
      state.revision++;state.accepted[payload.step]=copy(payload.fields);delete state.drafts[payload.step];state.current_step=payload.step==='shape'?'method':'visualize';
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
    (key==='method'?renderMethod:render)({body,foot,flow,element,button,field,connected,edited:(key,value)=>flow.edit(key,value),handle:action=>async()=>action(),
      proposalInventory:(target,key)=>renderProposalInventory(target,key,flow,element)});
  };
  flow.onChange=draw;draw();
  return {flow,api,state,writes,sources,draw,get body(){return body;},get foot(){return foot;},
    get:id=>[...body.all(),...foot.all()].find(node=>node.id===id),connect:value=>{connected=value;draw();},
    complete:(changes={})=>{
      const request=[...requests.values()].at(-1);
      state.proposals=[{proposal_id:PID,request_id:request.request_id,operation:key,accepted_revision:request.expected_revision,
        draft_version:request.expected_draft_version,source_digest:request.source_digest,proposal:copy(key==='method'?METHOD:SHAPE),
        stale:false,stale_reason:null,acceptance_eligible:true,acceptance_reason:null,content_omitted:false,
        evidence:{path:'history/'+IDEA+'/metadata/'+'b'.repeat(64)+'.md',sha256:'c'.repeat(64)},...changes}];
      state.proposal_inventory={total:1,projected:1,omitted:0,content_omitted:state.proposals[0].content_omitted?1:0,index_path:IDEA+'.md'};
    }};
}

test('empty Shape has no fabricated routes, scope or assumptions; explicit manual edits accept',async()=>{
  const h=harness();assert.equal(h.get('shape-accept').disabled,true);
  assert.equal(h.get('shape-alternative-0-route'),undefined);assert.equal(h.flow.buffers.shape.scope,null);
  assert.ok(h.body.all().some(node=>node.textContent==='Alternatives, including a simpler route'));
  h.get('shape-outcome').input('My words');h.get('shape-scope-small-change').click();
  h.get('shape-scope-reason').input('My scope reason');h.get('shape-next-slice').input('My next slice');
  await h.get('shape-add-alternative').click();assert.deepEqual(h.flow.buffers.shape.alternatives,[{route:'',reason:''}]);
  h.get('shape-alternative-0-route').input('My route');h.get('shape-alternative-0-reason').input('My reason');
  assert.equal(h.get('shape-accept').disabled,false);assert.equal(h.writes.length,0);
  await h.get('shape-accept').click();assert.equal(h.writes.at(-1).operation,'accept');
  assert.equal(h.writes.at(-1).payload.proposal_id,null);assert.equal(h.flow.status('shape'),'saved');
});

test('archived Shape stays reachable and explicitly reactivates only on accepting the redone answers',async()=>{
  const h=harness();assert.equal(h.get('shape-accept').textContent,'Accept and continue');
  assert.equal(h.get('shape-reactivation-notice'),undefined);
  h.state.idea_status='archived';h.state.accepted.shape=copy(SHAPE);
  h.state.steps.shape={status:'saved',accepted_revision:2,evidence_id:'immutable-archived-shape'};
  h.flow.load(copy(h.state));
  assert.equal(h.flow.canOpen('shape'),true);assert.equal(h.flow.open('shape'),true);
  assert.equal(h.get('shape-reactivation-notice').role,'status');
  assert.match(h.get('shape-reactivation-notice').textContent,/Accepting a redone Shape reactivates/);
  assert.match(h.get('shape-reactivation-notice').textContent,/archived plan evidence remains unchanged/);
  assert.equal(h.get('shape-accept').textContent,'Reactivate idea and accept Shape');
  h.get('shape-outcome').input('Explicit redone Shape');
  assert.equal(h.state.idea_status,'archived');assert.equal(h.writes.length,0);
  assert.equal(h.get('shape-accept').disabled,false);
  await h.get('shape-accept').click();assert.equal(h.writes.length,1);
  assert.equal(h.writes[0].operation,'accept');assert.equal(h.writes[0].payload.step,'shape');
  assert.equal(h.writes[0].payload.fields.outcome,'Explicit redone Shape');assert.equal(h.writes[0].payload.proposal_id,null);
  assert.equal(h.state.idea_status,'active');assert.equal(h.flow.state.idea_status,'active');
  assert.equal(h.flow.pending,null);assert.equal(h.flow.status('shape'),'saved');
  assert.equal(h.get('shape-reactivation-notice'),undefined);assert.equal(h.get('shape-accept').textContent,'Accept and continue');
});

test('archived Shape reactivation retains offline and paused acceptance refusal',async()=>{
  for(const blocked of ['offline','paused']){
    const h=harness();h.state.idea_status='archived';h.flow.load(copy(h.state));h.flow.edit('shape',copy(SHAPE));
    if(blocked==='offline')h.connect(false);else{h.flow.paused=true;h.draw();}
    assert.equal(h.get('shape-accept').textContent,'Reactivate idea and accept Shape');
    assert.equal(h.get('shape-accept').disabled,true);await h.get('shape-accept').click();
    assert.equal(h.writes.length,0);assert.equal(h.state.idea_status,'archived');
  }
});

test('partial durable draft fills only empty editor containers on human edits',async()=>{
  const h=harness();h.state.drafts.shape={outcome:'Unfinished saved thought',assumptions:null,learning:null};h.flow.load(h.state);
  h.get('shape-scope-project').click();h.get('shape-scope-reason').input('Human reason');h.get('shape-next-slice').input('Human next slice');
  await h.get('shape-add-alternative').click();h.get('shape-alternative-0-route').input('Human route');h.get('shape-alternative-0-reason').input('Human route reason');
  assert.deepEqual(h.flow.buffers.shape.assumptions,[]);assert.deepEqual(h.flow.buffers.shape.learning,[]);
  assert.equal(h.get('shape-accept').disabled,false);assert.equal(h.flow.buffers.shape.outcome,'Unfinished saved thought');
});

test('request waits with editable fields; reply stays separate until Use, edited copy accepts',async()=>{
  const h=harness();await h.flow.requestProposal('shape');
  assert.equal(h.get('shape-proposal-status').role,'status');assert.match(h.get('shape-proposal-status').textContent,/Waiting/);
  assert.equal(h.get('shape-outcome').disabled,false);assert.equal(h.flow.status('shape'),'current');
  h.get('shape-outcome').input('Unsent user edit');h.complete();await h.flow.refreshAgent();
  assert.equal(h.flow.buffers.shape.outcome,'Unsent user edit');assert.equal(h.flow.selectedProposals.shape,undefined);
  await h.get('shape-use-proposal-'+PID).click();assert.equal(h.flow.buffers.shape.outcome,SHAPE.outcome);
  assert.equal(h.flow.selectedProposals.shape,PID);assert.equal(h.flow.status('shape'),'unsaved');
  h.get('shape-outcome').input('Human revised suggestion');await h.get('shape-accept').click();
  assert.equal(h.writes.at(-1).payload.fields.outcome,'Human revised suggestion');assert.equal(h.writes.at(-1).payload.proposal_id,PID);
});

test('arrays and hostile text survive editing without an inferred simpler route or HTML',async()=>{
  const h=harness();const hostile='<img src=x onerror=alert(1)>\nOriginal second line';
  h.flow.edit('shape',{...SHAPE,alternatives:[{route:hostile,reason:'Original reason'},{route:'Other route',reason:'Other reason'}],
    assumptions:['First risk\ncontinued','Second risk'],learning:['Recorded learning']});
  assert.equal(h.get('shape-alternative-0-route').value,hostile);
  h.get('shape-alternative-1-reason').input('Edited second reason');h.get('shape-assumptions-1').input('Edited second risk');
  assert.equal(h.flow.buffers.shape.alternatives[0].route,hostile);assert.equal(h.flow.buffers.shape.assumptions[0],'First risk\ncontinued');
  await h.get('shape-remove-learning-0').click();assert.deepEqual(h.flow.buffers.shape.learning,[]);
  await h.get('shape-add-learning').click();assert.deepEqual(h.flow.buffers.shape.learning,['']);
  assert.equal(h.get('shape-accept').disabled,true);h.get('shape-learning-0').input('Human learning');
  assert.equal(h.get('shape-accept').disabled,false);assert.equal(h.body.all().filter(node=>node.tag==='img').length,0);
});

test('uncertain request exposes explicit identical retry and stop-waiting controls',async()=>{
  const h=harness(),original=h.api.write;let first=true;
  h.api.write=async(...args)=>{const result=await original(...args);if(first){first=false;throw Object.assign(new Error('private diagnostic'),{code:'connection_lost',uncertain:true});}return result;};
  await h.flow.requestProposal('shape');assert.match(h.get('shape-proposal-status').textContent,/may have reached/);
  assert.ok(h.get('shape-retry'));assert.equal(h.writes.length,1);
  assert.equal(h.body.all().some(node=>node.textContent.includes('private diagnostic')),false);
  await h.get('shape-retry').click();assert.deepEqual(h.writes[0],h.writes[1]);
  await h.get('shape-stop-waiting').click();assert.equal(h.flow.proposalPending,null);
  assert.equal(h.writes.length,2);assert.equal(h.flow.canPropose('shape'),true);
});

test('generation drift disables linked accept until explicit manual choice; disconnected editor stays usable',async()=>{
  const h=harness();await h.flow.requestProposal('shape');h.complete();await h.flow.refreshAgent();
  await h.get('shape-use-proposal-'+PID).click();h.state.agent_generation='agent_'+'5'.repeat(32);
  h.state.proposals[0]= {...h.state.proposals[0],stale:true,stale_reason:'wrong_generation',acceptance_eligible:false,acceptance_reason:'wrong_generation'};
  await h.flow.refreshAgent();assert.equal(h.get('shape-use-proposal-'+PID).disabled,true);assert.equal(h.get('shape-accept').disabled,true);
  await h.get('shape-manual').click();assert.equal(h.get('shape-accept').disabled,false);
  h.state.agent_status='disconnected';h.state.capabilities.agent=false;h.state.capabilities.memory=false;
  h.state.resume={required:true,reason:'agent_disconnected'};h.sources();await h.flow.refreshAgent();
  assert.equal(h.flow.canPropose('shape'),false);assert.equal(h.get('shape-outcome').disabled,false);assert.equal(h.get('shape-accept').disabled,false);
  h.connect(false);assert.equal(h.get('shape-outcome').disabled,true);assert.equal(h.get('shape-accept').disabled,true);
});

test('agent drop after Use: one explicit foot button accepts my own answers and unlinks the suggestion',async()=>{
  // Demo finding F5: "I cant go back to the human path" once a used suggestion stopped being acceptable.
  for(const key of ['shape','method']){
    const h=harness(key);await h.flow.requestProposal(key);h.complete();await h.flow.refreshAgent();
    await h.get(key+'-use-proposal-'+PID).click();
    if(key==='method')await h.get('method-choice-bounded-plan').click();
    assert.equal(h.get(key+'-accept-own'),undefined,'no extra door while the suggestion is acceptable');
    h.state.agent_status='disconnected';h.state.capabilities.agent=false;h.state.resume={required:true,reason:'agent_disconnected'};
    h.state.proposals[0]={...h.state.proposals[0],acceptance_eligible:false,acceptance_reason:'agent_unavailable'};h.sources();await h.flow.refreshAgent();
    assert.equal(h.get(key+'-accept').disabled,true);
    const own=h.get(key+'-accept-own');assert.ok(own,'the door sits beside Accept');assert.equal(own.disabled,false);
    assert.match(h.get(key+'-accept-own-reason').textContent,/can no longer be accepted.*kept/);
    await own.click();
    const accepted=h.writes.filter(write=>write.operation==='accept');
    assert.equal(accepted.length,1);assert.equal(accepted[0].payload.proposal_id,null,'accepted as my own, not linked');
    assert.equal(accepted[0].payload.step,key);
    if(key==='shape')assert.deepEqual(accepted[0].payload.fields,SHAPE,'exactly the fields on screen');
    else{assert.equal(accepted[0].payload.fields.selection,'bounded-plan','the human choice');assert.equal(accepted[0].payload.fields.reason,METHOD.reason);}
    assert.equal(h.flow.selectedProposals[key],undefined);
  }
});

test('omitted suggestion bodies expose real Markdown paths as text and cannot be copied',async()=>{
  const h=harness();await h.flow.requestProposal('shape');h.complete({proposal:null,content_omitted:true,acceptance_eligible:false,acceptance_reason:'projection_omitted'});
  h.state.proposal_inventory.total=4;h.state.proposal_inventory.omitted=3;await h.flow.refreshAgent();
  assert.equal(h.get('shape-use-proposal-'+PID).disabled,true);
  assert.equal(h.get('shape-proposal-inventory').role,'status');assert.match(h.get('shape-proposal-index-path').textContent,/All immutable suggestion links/);
  assert.ok(h.get('shape-proposal-evidence-'+PID).textContent.endsWith(h.state.proposals[0].evidence.path));
  assert.equal(h.body.all().some(node=>node.tag==='a'||node.href),false);
  assert.equal(h.flow.selectedProposals.shape,undefined);
});


test('Method recommendation never selects method or supplies human budget; explicit different choice accepts',async()=>{
  const h=harness('method');assert.equal(h.flow.buffers.method.selection,null);assert.equal(h.get('method-accept').disabled,true);
  await h.flow.requestProposal('method');h.complete();await h.flow.refreshAgent();
  assert.equal(h.flow.buffers.method.selection,null);assert.equal(h.get('method-choice-appetite-led')['aria-pressed'],'false');
  assert.ok(h.get('method-choice-appetite-led').all().some(node=>node.textContent.includes('Recommended')));
  await h.get('method-use-proposal-'+PID).click();assert.equal(h.flow.buffers.method.selection,null);
  assert.equal(h.flow.buffers.method.investment,null);assert.equal(h.get('method-investment-cap'),undefined);
  assert.equal(h.flow.buffers.method.reason,METHOD.reason);assert.equal(h.get('method-accept').disabled,true);
  await h.get('method-choice-adaptive-slices').click();h.get('method-reason').input('Human chose useful increments');
  assert.ok(h.body.all().some(node=>node.textContent.includes('You chose differently')));
  await h.get('method-accept').click();assert.equal(h.writes.at(-1).payload.fields.selection,'adaptive-slices');
  assert.equal(h.writes.at(-1).payload.fields.investment,null);assert.equal(h.writes.at(-1).payload.proposal_id,PID);
});

test('archived or unknown Method cannot accept valid answers and keeps draft and supporting memory visible',async()=>{
  for(const status of ['archived',null]){
    const h=harness('method');h.flow.edit('method',{...copy(METHOD),memory:{status:'unavailable',sources:[],rationale:null}});
    const fields=copy(h.flow.buffers.method),accept=h.get('method-accept');assert.equal(accept.disabled,false);
    h.flow.state.idea_status=status;
    await accept.listeners.click({target:accept});assert.equal(h.writes.length,0);
    h.draw();assert.equal(h.get('method-accept').disabled,true);await h.get('method-accept').click();
    assert.equal(h.writes.length,0);assert.deepEqual(h.flow.buffers.method,fields);assert.equal(h.flow.pending,null);
    assert.equal(h.get('method-idea-status').role,'status');assert.ok(h.get('method-memory'));
    assert.equal(h.get('method-investment-cap').value,'4');assert.equal(h.get('method-reason').disabled,false);
    if(status==='archived')assert.match(h.get('method-idea-status').textContent,/first explicitly redo and accept Shape/);
    else assert.match(h.get('method-idea-status').textContent,/status is unavailable/);
    assert.equal(h.flow.canOpen('shape'),true);
  }
});

test('appetite and experiment fields enforce explicit requirements and clear only on selection change',async()=>{
  const h=harness('method');await h.get('method-choice-appetite-led').click();h.get('method-reason').input('My boundary');
  assert.equal(h.get('method-investment-cap').value,'');assert.equal(h.get('method-accept').disabled,true);
  h.get('method-investment-cap').input('0');h.get('method-investment-unit').input('sessions');h.get('method-investment-boundary').input('Human limit');
  assert.equal(h.get('method-accept').disabled,true);h.get('method-investment-cap').input('3');assert.equal(h.get('method-accept').disabled,false);
  await h.get('method-choice-appetite-led').click();assert.equal(h.flow.buffers.method.investment.cap,3);
  await h.get('method-choice-experiment-led').click();assert.equal(h.flow.buffers.method.investment,null);assert.equal(h.flow.buffers.method.experiment,null);
  h.get('method-experiment-question').input('Which path?');h.get('method-experiment-evidence').input('One observed run');
  h.get('method-experiment-success-criterion').input('Human criterion');assert.equal(h.get('method-accept').disabled,true);
  h.get('method-experiment-stop-rule').input('After one run');assert.equal(h.get('method-accept').disabled,false);
  await h.get('method-choice-bounded-plan').click();assert.equal(h.flow.buffers.method.experiment,null);assert.equal(h.flow.buffers.method.investment,null);
  assert.equal(h.get('method-experiment-question'),undefined);assert.equal(h.get('method-investment-cap'),undefined);
});

test('reload and Use preserve human conditional answers instead of proposed budgets',async()=>{
  const h=harness('method');h.state.drafts.method={selection:'appetite-led',reason:'My saved reason',investment:{cap:7,unit:'weeks',boundary:'My saved boundary'},experiment:null,
    memory:{status:'unavailable',sources:[],rationale:null}};h.flow.load(h.state);h.sources();
  assert.equal(h.get('method-investment-cap').value,'7');await h.get('method-choice-appetite-led').click();
  assert.equal(h.flow.buffers.method.investment.cap,7);await h.flow.requestProposal('method');h.complete();await h.flow.refreshAgent();
  await h.get('method-use-proposal-'+PID).click();assert.equal(h.flow.buffers.method.selection,'appetite-led');
  assert.deepEqual(h.flow.buffers.method.investment,{cap:7,unit:'weeks',boundary:'My saved boundary'});
  assert.equal(h.get('method-investment-cap').value,'7');
});

test('memory supporting evidence is literal read-only text; explicit unavailable choice clears claims and linkage',async()=>{
  const h=harness('method');assert.ok(h.get('method-memory').all().some(node=>node.textContent.includes('No preference is claimed')));
  await h.flow.requestProposal('method');h.complete();await h.flow.refreshAgent();await h.get('method-use-proposal-'+PID).click();
  const memory=h.get('method-memory');assert.ok(memory.all().some(node=>node.textContent==='Source: decision:fixture-preference'));
  assert.equal(memory.all().some(node=>['input','textarea','a'].includes(node.tag)),false);
  await h.get('method-memory-unavailable').click();assert.equal(h.flow.selectedProposals.method,undefined);
  assert.deepEqual(h.flow.buffers.method.memory,{status:'unavailable',sources:[],rationale:null});
  assert.equal(h.get('method-memory-unavailable'),undefined);
});

test('Method failure displays redacted status and explicit exact retry, then stop waiting',async()=>{
  const h=harness('method'),original=h.api.write;let first=true;
  h.api.write=async(...args)=>{const result=await original(...args);if(first){first=false;throw Object.assign(new Error('private diagnostic'),{code:'connection_lost',uncertain:true});}return result;};
  await h.flow.requestProposal('method');assert.match(h.get('method-proposal-status').textContent,/may have reached/);
  assert.equal(h.get('method-proposal-error').role,'status');assert.equal(h.body.all().some(node=>node.textContent.includes('private diagnostic')),false);
  await h.get('method-retry').click();assert.deepEqual(h.writes[0],h.writes[1]);assert.equal(h.get('method-reason').disabled,false);
  await h.get('method-stop-waiting').click();assert.equal(h.flow.proposalPending,null);assert.equal(h.writes.length,2);
});

test('disconnected previously grounded Method requires explicit unavailable claim and keeps own choice editable',async()=>{
  const h=harness('method');await h.flow.requestProposal('method');h.complete();await h.flow.refreshAgent();
  await h.get('method-use-proposal-'+PID).click();await h.get('method-choice-bounded-plan').click();
  h.state.agent_status='disconnected';h.state.capabilities.agent=false;h.state.capabilities.memory=false;
  h.state.resume={required:true,reason:'agent_disconnected'};h.sources();
  Object.assign(h.state.proposals[0],{stale:true,stale_reason:'agent_unavailable',acceptance_eligible:false,acceptance_reason:'agent_unavailable'});
  await h.flow.refreshAgent();assert.equal(h.flow.canPropose('method'),false);assert.equal(h.get('method-use-proposal-'+PID).disabled,true);
  assert.equal(h.get('method-accept').disabled,true);assert.equal(h.get('method-reason').disabled,false);
  await h.get('method-memory-unavailable').click();assert.equal(h.get('method-accept').disabled,false);
  await h.get('method-accept').click();assert.equal(h.writes.at(-1).payload.proposal_id,null);
  assert.equal(h.writes.at(-1).payload.fields.memory.status,'unavailable');
});

test('Method omission notice uses shared immutable paths and disables absent body use',async()=>{
  const h=harness('method');await h.flow.requestProposal('method');h.complete({proposal:null,content_omitted:true,acceptance_eligible:false,acceptance_reason:'projection_omitted'});
  await h.flow.refreshAgent();assert.equal(h.get('method-use-proposal-'+PID).disabled,true);
  assert.equal(h.get('method-proposal-inventory').role,'status');assert.ok(h.get('method-proposal-evidence-'+PID));
  assert.equal(h.body.all().some(node=>node.href),false);assert.equal(h.flow.buffers.method.selection,null);
});


test('cap incremental decimals retain raw text across every render and poll',async()=>{
  for (const [pieces, expected] of [[['1','.','5'],1.5],[['0','.','5'],0.5],[['1',',','5'],1.5]]) {
    const h=harness('method');await h.get('method-choice-appetite-led').click();
    h.get('method-reason').input('Human decimal boundary');h.get('method-investment-unit').input('hours');h.get('method-investment-boundary').input('Human boundary');
    assert.equal(h.get('method-investment-cap').type,'text');assert.equal(h.get('method-investment-cap').inputMode,'decimal');
    let raw='';
    for (const piece of pieces) {
      raw+=piece;h.get('method-investment-cap').input(raw);
      assert.equal(h.get('method-investment-cap').value,raw);
      h.draw();assert.equal(h.get('method-investment-cap').value,raw);
      await h.flow.refreshAgent();assert.equal(h.get('method-investment-cap').value,raw);
      if (raw.endsWith('.')||raw.endsWith(',')||raw==='0') {
        assert.equal(h.flow.buffers.method.investment.cap,null);assert.equal(h.get('method-accept').disabled,true);
      }
    }
    assert.equal(h.flow.buffers.method.investment.cap,expected);
    await h.get('method-accept').click();assert.equal(h.writes.at(-1).payload.fields.investment.cap,expected);
  }
});

test('invalid and partial cap text cannot flip sign, silently validate, or submit invalid numeric draft',async()=>{
  const h=harness('method');await h.get('method-choice-appetite-led').click();
  h.get('method-reason').input('My boundary');h.get('method-investment-unit').input('hours');h.get('method-investment-boundary').input('Human limit');
  for (const raw of ['-','-5','','0','1.','0.', '.', '1,,5','Infinity','1000000000001']) {
    h.get('method-investment-cap').input(raw);assert.equal(h.get('method-investment-cap').value,raw);
    assert.equal(h.flow.buffers.method.investment.cap,null);assert.equal(h.get('method-accept').disabled,true);
    await h.flow.save('method',true);
    assert.equal(h.writes.at(-1).payload.fields.investment.cap,null);
    assert.equal(h.get('method-investment-cap').value,raw);
  }
});

test('canonical cap loads synchronize text while idea and Flow affinity prevent stale edits leaking',async()=>{
  const h=harness('method');await h.get('method-choice-appetite-led').click();h.get('method-investment-cap').input('1.');
  h.state.drafts.method={selection:'appetite-led',reason:'Loaded reason',investment:{cap:2.75,unit:'hours',boundary:'Loaded limit'},experiment:null,
    memory:{status:'unavailable',sources:[],rationale:null}};
  h.flow.load(h.state);assert.equal(h.get('method-investment-cap').value,'2.75');
  h.get('method-investment-cap').input('0.');
  const other=copy(h.state);other.idea_id='idea_'+'6'.repeat(32);other.proposal_inventory.index_path=other.idea_id+'.md';other.drafts.method.investment.cap=null;
  h.flow.load(other);assert.equal(h.get('method-investment-cap').value,'');
  h.get('method-investment-cap').input('3.');const independent=harness('method');await independent.get('method-choice-appetite-led').click();
  assert.equal(independent.get('method-investment-cap').value,'');
  await h.get('method-choice-bounded-plan').click();await h.get('method-choice-appetite-led').click();
  assert.equal(h.get('method-investment-cap').value,'');assert.equal(h.flow.buffers.method.investment,null);
});
