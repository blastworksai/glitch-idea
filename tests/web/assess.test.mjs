// Actual packaged Assess render + Flow, fixed DOM/API fixtures.
// These tests do not qualify browser layout, accessibility attendance or server ingestion.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web=new URL('../../glitch-idea/web/',import.meta.url);
const moduleUrl=source=>'data:text/javascript;base64,'+Buffer.from(source).toString('base64');
const foldsUrl=moduleUrl(await readFile(new URL('folds.js',web),'utf8'));
const {Flow,STEPS,assessmentScore}=await import(foldsUrl);
const {render}=await import(moduleUrl((await readFile(new URL('steps/assess.js',web),'utf8')).replace("'../folds.js'",JSON.stringify(foldsUrl))));
const copy=value=>structuredClone(value);
const IDEA='idea_'+'1'.repeat(32),OTHER='idea_'+'2'.repeat(32),THIRD='idea_'+'3'.repeat(32);
const SID='session_'+'4'.repeat(32),GEN='agent_'+'5'.repeat(32),PID='proposal_'+'6'.repeat(32),BINDING='binding_'+'7'.repeat(32);
const CAPTURE={raw_text:'Fixture only',workspace:{name:'Explicit',path:'/fixture',confirmed:true}};
const SHAPE={outcome:'Clear draft',scope:'small-change',scope_reason:'One field',alternatives:[{route:'Keep it',reason:'Simpler'}],assumptions:[],next_slice:'Check it',learning:[]};
const rating={urgency:6,importance:7,actor:'Operator',timestamp:'fixture-only'};
const assessment=()=>({method:'wsjf',version:'v1',inputs:{value:8,time_criticality:4,enablement:2,effort:2},basis:'Fixture evidence',assumptions:[],confidence:'low',provenance:'Current agent'});
const proposal=()=>({assessment:assessment(),position:{proposed_position:1,actual_position:1,neighbors:{before:null,after:OTHER},override_reason:null}});
class Node {
  constructor(tag,text='',className=''){this.tag=tag;this.textContent=text;this.className=className;this.children=[];this.listeners={};this.disabled=false;}
  append(...children){this.children.push(...children);}
  setAttribute(key,value){this[key]=value;}
  addEventListener(key,fn){this.listeners[key]=fn;}
  async click(){if(!this.disabled)return this.listeners.click?.({target:this});}
  input(value){if(!this.disabled){this.value=value;return this.listeners.input?.({target:this});}}
  all(){return [this,...this.children.flatMap(child=>child.all())];}
  set innerHTML(value){throw new Error('HTML injection forbidden');}
}
const element=(tag,text='',className='')=>new Node(tag,text,className);
const button=(text,action,className='')=>{const node=element('button',text,className);node.type='button';node.addEventListener('click',action);return node;};
function harness(storage=null){
  const state={ok:true,code:'ok',session_id:SID,idea_id:IDEA,idea_status:'active',revision:4,draft_version:0,backlog_revision:9,current_step:'assess',
    steps:Object.fromEntries(STEPS.map(({key})=>[key,{status:['capture','priorities','shape','method','visualize'].includes(key)?'saved':key==='assess'?'current':'todo',
      accepted_revision:['capture','priorities','shape','method','visualize'].includes(key)?4:null,evidence_id:['capture','priorities','shape','method','visualize'].includes(key)?'evidence-'+key:null}])),
    accepted:{capture:copy(CAPTURE),priorities:{urgency:6,importance:7},shape:copy(SHAPE)},drafts:{},draft:null,
    agent_status:'connected',agent_generation:GEN,proposal_sources:{},proposals:[],human_ratings:copy(rating),assessment_summary:null,
    backlog_status:{available:true,code:'ok'},backlog:{revision:9,order:[IDEA,OTHER,THIRD],comparisons:[IDEA,OTHER,THIRD].map(idea_id=>
      ({idea_id,revision:idea_id===IDEA?4:1,status:'active',ratings:idea_id===IDEA?copy(rating):null,assessment:null}))}};
  const writes=[],receipts=new Map();let count=0,body,foot,connected=true;
  const sources=()=>{
    const target=state.backlog?.comparisons.find(item=>item.idea_id===IDEA);
    if(target){target.revision=state.revision;target.ratings=copy(state.human_ratings);target.assessment=copy(state.assessment_summary);}
    state.proposal_sources.assessment=state.agent_status==='connected'&&state.backlog_status.available?{available:true,code:'ok',source:{accepted_revision:state.revision,draft_version:state.draft_version,
      data:{steps:{capture:copy(CAPTURE),priorities:copy(state.accepted.priorities),shape:copy(SHAPE)},backlog:copy(state.backlog),target:copy(state.drafts.assess??state.accepted.assess??null)},source_digest:'a'.repeat(64)}}:
      {available:false,code:state.agent_status==='connected'?'source_too_large':'agent_unavailable',source:null};
    state.proposal_inventory={total:state.proposals.length,projected:state.proposals.length,omitted:0,content_omitted:state.proposals.filter(p=>p.content_omitted).length,index_path:IDEA+'.md'};
  };
  sources();
  const api={bindingId:BINDING,state:async()=>copy(state),reconcile:async id=>({result:copy(receipts.get(id)??null),state:copy(state)}),
    write:async(operation,payload)=>{
      writes.push({operation,payload:copy(payload)});
      if(operation==='propose')return {ok:true,code:'ok',status:'pending',write_state:'not_applied',request_id:payload.request_id,session_id:SID,
        idea_id:IDEA,operation:payload.operation,accepted_revision:payload.expected_revision,draft_version:payload.expected_draft_version,source_digest:payload.source_digest};
      if(operation==='draft'){state.draft_version++;state.drafts[payload.step]=copy(payload.fields);}
      if(operation==='navigate')state.current_step=payload.step;
      if(operation==='accept'){
        assert.equal(payload.expected_backlog_revision,state.backlog_revision);state.revision++;state.backlog_revision++;state.backlog.revision=state.backlog_revision;
        state.accepted.assess=copy(payload.fields);delete state.drafts.assess;
        state.assessment_summary={...copy(payload.fields.assessment),score:assessmentScore(payload.fields.assessment),actor:'Operator'};
        const order=state.backlog.order.filter(id=>id!==IDEA);order.splice(payload.fields.position.actual_position-1,0,IDEA);state.backlog.order=order;
        state.backlog.comparisons=order.map(id=>state.backlog.comparisons.find(item=>item.idea_id===id));
        state.steps.assess={status:'saved',accepted_revision:state.revision,evidence_id:'assess-evidence'};
        state.proposals=state.proposals.map(p=>({...p,acceptance_eligible:false,acceptance_reason:'stale_source',stale:true,stale_reason:'stale_source'}));
      }
      sources();const result={ok:true,code:'ok',write_state:'applied',request_id:payload.request_id,idea_id:IDEA};receipts.set(payload.request_id,result);return result;
    }};
  const flow=new Flow(api,()=> 'request-'+(++count),()=>0,storage);flow.load(state);
  const draw=()=>{
    body=element('div');foot=element('footer');
    const field=(target,label,id,value,changed,textarea=false)=>{
      const wrap=element('div'),heading=element('label',label),input=element(textarea?'textarea':'input');
      heading.htmlFor=id;input.id=id;input.value=value??'';input.disabled=flow.busy||!connected;
      input.addEventListener('input',event=>changed(event.target.value));wrap.append(heading,input);target.append(wrap);return input;
    };
    render({body,foot,flow,element,button,field,connected,edited:(key,value)=>flow.edit(key,value),handle:action=>async()=>action(),
      proposalInventory:target=>{target.append(element('p','Recorded suggestion inventory'));}});
  };
  flow.onChange=draw;draw();
  const complete=(overrides={})=>{
    const request=writes.findLast(w=>w.operation==='propose').payload;
    state.proposals=[{proposal_id:PID,request_id:request.request_id,operation:'assessment',accepted_revision:request.expected_revision,
      draft_version:request.expected_draft_version,source_digest:request.source_digest,proposal:proposal(),stale:false,stale_reason:null,
      acceptance_eligible:true,acceptance_reason:null,content_omitted:false,evidence:{path:'history/'+IDEA+'/metadata/'+'b'.repeat(64)+'.md',sha256:'c'.repeat(64)},...overrides}];sources();
  };
  const get=id=>[...body.all(),...foot.all()].find(node=>node.id===id);
  return {flow,api,state,writes,sources,draw,complete,get,get body(){return body;},get foot(){return foot;},connect:value=>{connected=value;draw();},
    use:async()=>{await flow.requestProposal('assess');complete();await flow.refreshAgent();await get('assess-use-proposal-'+PID).click();}};
}

test('empty editor has no method/rating/position inventions and human priorities are readonly',()=>{
  const h=harness();assert.equal(h.flow.buffers.assess.assessment,null);assert.equal(h.get('assess-accept').disabled,true);
  for(const method of ['wsjf','rice','kano'])assert.equal(h.get('assess-method-'+method)['aria-pressed'],'false');
  const human=h.get('assess-human-ratings');assert.ok(human.all().some(n=>n.textContent==='Urgency: 6 of 10'));
  assert.ok(human.all().some(n=>n.textContent.includes('Recorded by: Operator')));
  assert.equal(human.all().filter(n=>['input','textarea','button'].includes(n.tag)).length,0);
});

test('archived valid Assess refuses acceptance and keeps current draft, readonly priorities and evidence visible',async()=>{
  const h=harness();await h.use();h.get('assess-input-value').input('12');
  const fields=copy(h.flow.buffers.assess),writes=h.writes.length,backlog=copy(h.state.backlog),priorities=copy(h.state.human_ratings);
  h.state.idea_status='archived';h.sources();await h.flow.refreshAgent();
  assert.equal(h.get('assess-accept').disabled,true);assert.equal(h.get('assess-idea-status').role,'status');
  assert.match(h.get('assess-idea-status').textContent,/explicitly redo and accept Shape/);assert.match(h.get('assess-idea-status').textContent,/Archived history is kept/);
  await h.get('assess-accept').click();
  await h.get('assess-accept').listeners.click({target:h.get('assess-accept')});
  assert.equal(h.writes.length,writes);assert.equal(h.flow.pending,null);assert.equal(h.state.idea_status,'archived');
  assert.deepEqual(h.flow.buffers.assess,fields);assert.deepEqual(h.state.backlog,backlog);assert.deepEqual(h.state.human_ratings,priorities);
  assert.equal(h.get('assess-input-value').value,'12');assert.equal(h.get('assess-input-value').disabled,false);
  assert.equal(h.get('assess-human-ratings').all().filter(node=>['input','textarea','button'].includes(node.tag)).length,0);
  assert.equal(h.get('assess-backlog').all().filter(node=>node.tag==='li').length,3);
  assert.ok(h.get('assess-proposal-'+PID).all().some(node=>node.textContent==='Saved suggestion: '+h.state.proposals[0].evidence.path));
});

test('unknown idea status also refuses otherwise valid Assess acceptance without a write',async()=>{
  const h=harness();h.flow.edit('assess',proposal());delete h.state.idea_status;h.flow.load(copy(h.state),true);
  assert.equal(h.get('assess-accept').disabled,true);assert.match(h.get('assess-idea-status').textContent,/status is unavailable/);
  await h.get('assess-accept').click();assert.equal(h.writes.length,0);
});

test('request waits with editable fields and does not apply or accept the reply',async()=>{
  const h=harness();await h.flow.requestProposal('assess');assert.equal(h.writes[0].payload.operation,'assessment');
  assert.equal(h.get('assess-proposal-status').role,'status');h.get('assess-version').input('Human version');
  h.complete();await h.flow.refreshAgent();assert.equal(h.flow.buffers.assess.assessment.version,'Human version');
  assert.equal(h.flow.buffers.assess.assessment.method,null);assert.equal(h.writes.filter(w=>w.operation==='accept').length,0);
});

test('Use then human override accepts exact current neighbors/CAS without caller score or rating changes',async()=>{
  const h=harness();await h.use();assert.equal(h.get('assess-proposed-position-input'),undefined);
  assert.equal(h.get('assess-proposed-position').textContent,'Original proposed position: 1');
  h.get('assess-input-value').input('10');h.get('assess-actual-position').input('3');
  assert.equal(h.get('assess-override-reason').required,true);assert.equal(h.get('assess-accept').disabled,true);
  h.get('assess-override-reason').input('Operator chose last');assert.equal(h.get('assess-accept').disabled,false);
  await h.get('assess-accept').click();const payload=h.writes.at(-1).payload;
  assert.equal(h.writes.at(-1).operation,'accept');
  assert.deepEqual(Object.keys(payload).sort(),['expected_backlog_revision','expected_draft_version','expected_revision','fields','idea_id','proposal_id','request_id','step']);
  assert.deepEqual(Object.keys(payload.fields).sort(),['assessment','position']);
  assert.deepEqual(Object.keys(payload.fields.assessment).sort(),['assumptions','basis','confidence','inputs','method','provenance','version']);
  for(const key of ['urgency','importance','ratings'])for(const object of [payload,payload.fields,payload.fields.assessment])assert.equal(Object.hasOwn(object,key),false);
  assert.equal(payload.expected_backlog_revision,9);assert.equal(payload.proposal_id,PID);
  assert.equal(Object.hasOwn(payload.fields.assessment,'score'),false);assert.deepEqual(payload.fields.position.neighbors,{before:THIRD,after:null});
  assert.deepEqual(h.state.human_ratings,rating);assert.deepEqual(h.state.backlog.order,[OTHER,THIRD,IDEA]);
  assert.equal(h.flow.pending,null);assert.equal(h.flow.error,null);assert.equal(h.flow.status('assess'),'saved');
});

test('manual assessment accepts only after refreshing placement against an order-only change',async()=>{
  const h=harness(),originalRatings=copy(h.state.human_ratings);
  await h.get('assess-method-wsjf').click();
  h.get('assess-version').input('v1');h.get('assess-basis').input('Operator evidence');
  h.get('assess-provenance').input('Operator manual assessment');await h.get('assess-confidence-low').click();
  for(const [key,value]of [['value','8'],['time_criticality','4'],['enablement','2'],['effort','2']])h.get('assess-input-'+key).input(value);
  h.get('assess-proposed-position-input').input('1');h.get('assess-actual-position').input('1');
  assert.equal(h.get('assess-accept').disabled,false);assert.deepEqual(h.flow.buffers.assess.position.neighbors,{before:null,after:OTHER});
  assert.equal(h.writes.length,0);assert.equal(h.flow.selectedProposals.assess,undefined);
  const before=copy(h.flow.buffers.assess);
  h.state.backlog_revision=10;h.state.backlog.revision=10;h.state.backlog.order=[THIRD,OTHER,IDEA];
  h.state.backlog.comparisons=h.state.backlog.order.map(id=>h.state.backlog.comparisons.find(item=>item.idea_id===id));
  h.sources();await h.flow.refreshAgent();
  assert.deepEqual(h.flow.buffers.assess,before);assert.equal(h.state.proposals.length,0);
  assert.equal(h.get('assess-accept').disabled,true);
  assert.ok(h.body.all().some(node=>node.textContent.includes('Placement neighbors changed')));
  assert.ok(h.get('assess-backlog').all().some(node=>node.textContent==='Observed backlog revision: 10. Scores do not reorder this list.'));
  assert.deepEqual(h.get('assess-backlog').all().filter(node=>node.tag==='li').map(node=>node.id),[THIRD,OTHER,IDEA].map(id=>'assess-backlog-'+id));
  await h.get('assess-accept').click();assert.equal(h.writes.length,0);
  h.get('assess-actual-position').input('1');
  assert.deepEqual(h.flow.buffers.assess.position.neighbors,{before:null,after:THIRD});assert.equal(h.get('assess-accept').disabled,false);
  await h.get('assess-accept').click();assert.equal(h.writes.length,1);
  const {operation,payload}=h.writes[0];assert.equal(operation,'accept');
  assert.deepEqual(Object.keys(payload).sort(),['expected_backlog_revision','expected_draft_version','expected_revision','fields','idea_id','proposal_id','request_id','step']);
  assert.deepEqual(Object.keys(payload.fields).sort(),['assessment','position']);
  assert.deepEqual(Object.keys(payload.fields.assessment).sort(),['assumptions','basis','confidence','inputs','method','provenance','version']);
  for(const key of ['urgency','importance','ratings','score'])for(const object of [payload,payload.fields,payload.fields.assessment])assert.equal(Object.hasOwn(object,key),false);
  assert.equal(payload.proposal_id,null);assert.equal(payload.expected_backlog_revision,10);
  assert.deepEqual(payload.fields.position,{proposed_position:1,actual_position:1,neighbors:{before:null,after:THIRD},override_reason:null});
  assert.equal(h.flow.pending,null);assert.equal(h.flow.error,null);assert.equal(h.flow.status('assess'),'saved');
  assert.equal(h.state.backlog_revision,11);assert.deepEqual(h.state.backlog.order,[IDEA,THIRD,OTHER]);
  assert.deepEqual(h.state.human_ratings,originalRatings);assert.deepEqual(h.state.accepted.priorities,{urgency:6,importance:7});
});

test('blank WSJF input remains unknown/null through acceptance',async()=>{
  const h=harness();await h.use();h.get('assess-input-value').input('');
  assert.equal(h.get('assess-input-value-status').textContent,'Unknown');assert.equal(h.get('assess-preview').textContent,'Draft preview: WSJF: Unknown');
  await h.get('assess-accept').click();assert.equal(h.writes.at(-1).payload.fields.assessment.inputs.value,null);assert.equal(h.state.assessment_summary.score,null);
});

test('RICE inputs/confidence remain distinct from assessment confidence and human ratings',async()=>{
  const h=harness();await h.use();await h.get('assess-method-rice').click();
  for(const [key,value]of [['reach','100'],['impact','2'],['confidence','0.5'],['effort','5']])h.get('assess-input-'+key).input(value);
  assert.equal(h.get('assess-preview').textContent,'Draft preview: RICE: 20');assert.equal(h.flow.buffers.assess.assessment.confidence,'low');
  assert.deepEqual(h.state.human_ratings,rating);assert.equal(h.flow.buffers.assess.assessment.inputs.confidence,0.5);
  h.get('assess-input-confidence').input('1.1');assert.equal(h.get('assess-accept').disabled,true);
});

test('Kano category and hypothesis require explicit choice and never produce numeric score',async()=>{
  const h=harness();await h.use();await h.get('assess-method-kano').click();assert.equal(h.get('assess-accept').disabled,true);
  await h.get('assess-kano-delighter').click();assert.equal(h.get('assess-accept').disabled,true);
  await h.get('assess-kano-hypothesis-true').click();assert.match(h.get('assess-preview').textContent,/delighter.*hypothesis.*no numeric score/);
  assert.equal(h.get('assess-input-effort'),undefined);await h.get('assess-accept').click();assert.equal(h.state.assessment_summary.score,null);
});

test('decimal intermediates survive redraw and agent polling without becoming accepted unknown',async()=>{
  const h=harness();await h.use();h.get('assess-input-value').input('2.');assert.equal(h.get('assess-input-value').value,'2.');
  h.draw();await h.flow.refreshAgent();assert.equal(h.get('assess-input-value').value,'2.');assert.equal(h.get('assess-accept').disabled,true);
  h.get('assess-input-value').input('2.5');assert.equal(h.flow.buffers.assess.assessment.inputs.value,2.5);assert.equal(h.get('assess-input-value').value,'2.5');
  h.get('assess-input-value').input('2,');h.draw();assert.equal(h.get('assess-input-value').value,'2,');assert.equal(h.get('assess-accept').disabled,true);
  h.get('assess-input-value').input('2,75');assert.equal(h.flow.buffers.assess.assessment.inputs.value,2.75);assert.equal(h.get('assess-input-value').value,'2,75');
  assert.equal(h.get('assess-input-value').type,'text');assert.equal(h.get('assess-input-value').inputMode,'decimal');
});

test('invalid and negative numeric text is preserved and cannot silently flip sign or accept',async()=>{
  const h=harness();await h.use();
  for(const raw of ['-2.5','abc','1e999']){h.get('assess-input-value').input(raw);h.draw();assert.equal(h.get('assess-input-value').value,raw);
    assert.equal(h.flow.buffers.assess.assessment.inputs.value,null);assert.equal(h.get('assess-accept').disabled,true);assert.equal(h.get('assess-input-value')['aria-invalid'],'true');}
  h.get('assess-input-value').input('');assert.equal(h.get('assess-accept').disabled,false);
  h.get('assess-input-effort').input('0');assert.equal(h.get('assess-accept').disabled,true);
});

test('explicit Use and authoritative canonical change reset raw numeric edits',async()=>{
  const h=harness();await h.use();h.get('assess-input-value').input('08');assert.equal(h.get('assess-input-value').value,'08');
  await h.get('assess-use-proposal-'+PID).click();assert.equal(h.get('assess-input-value').value,'8');
  h.state.drafts.assess=copy(h.flow.buffers.assess);h.state.drafts.assess.assessment.inputs.value=7;h.state.draft_version++;h.sources();h.flow.load(copy(h.state));
  assert.equal(h.get('assess-input-value').value,'7');
});

test('structured basis/provenance remain literal and editable without HTML injection',async()=>{
  const h=harness();await h.flow.requestProposal('assess');const fields=proposal();fields.assessment.basis={'<script>':'<img onerror=alert(1)>'};
  fields.assessment.provenance={source:'Fixture source'};h.complete({proposal:fields});await h.flow.refreshAgent();await h.get('assess-use-proposal-'+PID).click();
  assert.equal(h.get('assess-basis-0').value,'<img onerror=alert(1)>');h.get('assess-provenance-0').input('Edited source');
  assert.deepEqual(h.flow.buffers.assess.assessment.provenance,{source:'Edited source'});
  assert.ok(h.body.all().some(n=>n.tag==='label'&&n.textContent==='<script>'));
});

test('assumptions are explicit editable list entries',async()=>{
  const h=harness();await h.use();await h.get('assess-add-assumption').click();assert.equal(h.get('assess-accept').disabled,true);
  h.get('assess-assumption-0').input('An explicit assumption');assert.deepEqual(h.flow.buffers.assess.assessment.assumptions,['An explicit assumption']);
  await h.get('assess-remove-assumption-0').click();assert.deepEqual(h.flow.buffers.assess.assessment.assumptions,[]);
});

test('actual ordered backlog is not sorted by numeric assessment scores',()=>{
  const h=harness();h.state.backlog.comparisons[1].assessment={...assessment(),inputs:{value:100,time_criticality:4,enablement:2,effort:2},score:53};
  h.sources();h.flow.load(copy(h.state));const rows=h.get('assess-backlog').all().filter(n=>n.tag==='li');
  assert.deepEqual(rows.map(n=>n.id),[IDEA,OTHER,THIRD].map(id=>'assess-backlog-'+id));assert.deepEqual(h.state.backlog.order,[IDEA,OTHER,THIRD]);
});

test('stale order and stale selected suggestion preserve drafts and disable acceptance',async()=>{
  const h=harness();await h.use();h.get('assess-input-value').input('12');
  h.state.backlog_revision++;h.state.backlog.revision++;h.state.backlog.order=[THIRD,OTHER,IDEA];
  h.state.backlog.comparisons=h.state.backlog.order.map(id=>h.state.backlog.comparisons.find(item=>item.idea_id===id));
  Object.assign(h.state.proposals[0],{stale:true,stale_reason:'stale_source',acceptance_eligible:false,acceptance_reason:'stale_source'});h.sources();await h.flow.refreshAgent();
  assert.equal(h.get('assess-input-value').value,'12');assert.equal(h.get('assess-accept').disabled,true);
  assert.ok(h.body.all().some(n=>n.textContent.includes('Placement neighbors changed')));assert.equal(h.flow.selectedProposals.assess,PID);
});

test('capacity absence leaves answers but disables placement and acceptance',async()=>{
  const h=harness();await h.use();h.state.backlog=null;h.state.backlog_status={available:false,code:'source_too_large'};
  Object.assign(h.state.proposals[0],{acceptance_eligible:false,acceptance_reason:'stale_source'});h.sources();h.flow.load(copy(h.state),true);
  assert.equal(h.get('assess-actual-position').disabled,true);assert.equal(h.get('assess-accept').disabled,true);
  assert.equal(h.get('assess-input-value').value,'8');assert.ok(h.body.all().some(n=>n.textContent.includes('complete current backlog is unavailable')));
});

test('explicit unlink retains answers and exposes human proposed position control',async()=>{
  const h=harness();await h.use();await h.get('assess-manual').click();assert.equal(h.flow.selectedProposals.assess,undefined);
  assert.equal(h.get('assess-proposed-position-input').value,'1');assert.equal(h.get('assess-input-value').value,'8');
});

test('agent drop after Use: the foot door only unlinks, exposing the proposed rank before any accept',async()=>{
  // Review demofix-r1: accepting in the same click locked in a rank the human never had a control for.
  const h=harness();await h.use();h.state.agent_status='disconnected';
  Object.assign(h.state.proposals[0],{acceptance_eligible:false,acceptance_reason:'agent_unavailable'});h.sources();await h.flow.refreshAgent();
  assert.equal(h.get('assess-accept').disabled,true);assert.equal(h.get('assess-proposed-position-input'),undefined,'rank locked while linked');
  const own=h.get('assess-accept-own');assert.equal(own.disabled,false);assert.match(h.get('assess-accept-own-reason').textContent,/edit the proposed position, then accept/);
  const writes=h.writes.length;await own.click();
  assert.equal(h.writes.length,writes,'unlinking writes nothing and accepts nothing');assert.equal(h.flow.selectedProposals.assess,undefined);
  assert.equal(h.get('assess-proposed-position-input').disabled,false,'the rank is now the human\'s to edit');assert.equal(h.get('assess-input-value').value,'8');
});

test('equivalent neighbors with reversed object key order remain acceptable',async()=>{
  const h=harness();await h.use();const fields=copy(h.flow.buffers.assess);
  fields.position.neighbors={after:OTHER,before:null};h.flow.edit('assess',fields);
  assert.equal(h.get('assess-accept').disabled,false);
});

test('disconnect and pause preserve draft while assistance or paused controls are unavailable',async()=>{
  const h=harness();await h.use();h.state.agent_status='disconnected';Object.assign(h.state.proposals[0],{acceptance_eligible:false,acceptance_reason:'agent_unavailable'});
  h.sources();await h.flow.refreshAgent();assert.equal(h.flow.canPropose('assess'),false);assert.equal(h.get('assess-input-value').disabled,false);
  h.get('assess-input-value').input('12');await h.flow.pause();assert.equal(h.flow.paused,true);assert.equal(h.get('assess-input-value').disabled,true);
  assert.equal(h.state.drafts.assess.assessment.inputs.value,12);assert.equal(h.state.revision,4);
});

test('every editable input has an associated label and suggestion status uses live status role',async()=>{
  const h=harness();await h.use();const nodes=h.body.all();
  for(const input of nodes.filter(n=>['input','textarea'].includes(n.tag)))assert.ok(nodes.some(n=>n.tag==='label'&&n.htmlFor===input.id));
  assert.equal(h.get('assess-preview').role,'status');assert.equal(h.get('assess-method-wsjf')['aria-pressed'],'true');
});

test('save failure keeps exact edited answers and sends no retry or score payload',async()=>{
  const h=harness();await h.use();h.get('assess-input-value').input('12');const before=copy(h.flow.buffers.assess),count=h.writes.length;
  h.api.write=async(operation,payload)=>{h.writes.push({operation,payload:copy(payload)});throw Object.assign(new Error('private error'),{code:'stale_backlog',status:409});};
  await h.get('assess-accept').click();assert.equal(h.writes.length,count+1);assert.deepEqual(h.flow.buffers.assess,before);
  assert.equal(Object.hasOwn(h.writes.at(-1).payload.fields.assessment,'score'),false);assert.equal(h.get('assess-accept').disabled,true);
});
