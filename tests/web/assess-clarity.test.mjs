// R4 Assess clarity: every method and input explains itself. Actual packaged Assess render + Flow, fixed DOM fixtures.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web=new URL('../../glitch-idea/web/',import.meta.url);
const moduleUrl=source=>'data:text/javascript;base64,'+Buffer.from(source).toString('base64');
const foldsUrl=moduleUrl(await readFile(new URL('folds.js',web),'utf8'));
const {Flow,STEPS}=await import(foldsUrl);
const {render}=await import(moduleUrl((await readFile(new URL('steps/assess.js',web),'utf8')).replace("'../folds.js'",JSON.stringify(foldsUrl))));
const IDEA='idea_'+'1'.repeat(32),OTHER='idea_'+'2'.repeat(32),SID='session_'+'4'.repeat(32),BINDING='binding_'+'7'.repeat(32);
class Node {
  constructor(tag,text='',className=''){this.tag=tag;this.textContent=text;this.className=className;this.children=[];this.listeners={};this.disabled=false;}
  append(...children){this.children.push(...children);}
  setAttribute(key,value){this[key]=value;}
  addEventListener(key,fn){this.listeners[key]=fn;}
  input(value){this.value=value;return this.listeners.input?.({target:this});}
  async click(){return this.listeners.click?.({target:this});}
  all(){return [this,...this.children.flatMap(child=>child.all())];}
}
const element=(tag,text='',className='')=>new Node(tag,text,className);
const button=(text,action,className='')=>{const node=element('button',text,className);node.addEventListener('click',action);return node;};
function harness(){
  const state={ok:true,code:'ok',session_id:SID,idea_id:IDEA,idea_status:'active',revision:4,draft_version:0,backlog_revision:9,current_step:'assess',
    steps:Object.fromEntries(STEPS.map(({key})=>[key,{status:'todo',accepted_revision:null,evidence_id:null}])),accepted:{},drafts:{},draft:null,
    agent_status:'connected',agent_generation:'agent_'+'5'.repeat(32),proposal_sources:{assessment:{available:false,code:'agent_unavailable',source:null}},proposals:[],proposal_inventory:{total:0,projected:0,omitted:0,content_omitted:0,index_path:IDEA+'.md'},human_ratings:{urgency:6,importance:7,actor:'x',timestamp:'t'},
    assessment_summary:null,backlog_status:{available:true,code:'ok'},backlog:{revision:9,order:[IDEA,OTHER],comparisons:[IDEA,OTHER].map(idea_id=>({idea_id,revision:idea_id===IDEA?4:1,status:'active',ratings:idea_id===IDEA?{urgency:6,importance:7,actor:'x',timestamp:'t'}:null,assessment:null}))}};
  const api={bindingId:BINDING,state:async()=>state,reconcile:async()=>({result:null,state:state}),write:async()=>({ok:true})};
  const flow=new Flow(api,()=>'r',()=>0,null);flow.load(state);
  let body,foot;
  const draw=()=>{body=element('div');foot=element('footer');
    const field=(target,label,id,value,changed,textarea=false)=>{const wrap=element('div'),heading=element('label',label),input=element(textarea?'textarea':'input');
      heading.htmlFor=id;input.id=id;input.value=value??'';input.addEventListener('input',e=>changed(e.target.value));wrap.append(heading,input);target.append(wrap);return input;};
    render({body,foot,flow,element,button,field,connected:true,edited:(key,value)=>flow.edit(key,value),handle:a=>async()=>a(),proposalInventory:()=>{}});};
  flow.onChange=draw;draw();
  return {flow,get:id=>body.all().find(n=>n.id===id),get body(){return body;},get foot(){return foot;},pick:async m=>{await body.all().find(n=>n.id==='assess-method-'+m).click();}};
}
const texts=h=>h.body.all().map(n=>n.textContent);
const helpFor=(h,control)=>{const ids=String(control['aria-describedby']??'').split(/\s+/).filter(Boolean);return ids.map(id=>h.get(id)).filter(Boolean);};
const SUFFIX=/Leave blank if you don't know; the score stays Unknown\./;

test('each method shows a plain description and what it needs (methods.md gives no when-to-use rule)',async()=>{
  const h=harness();
  for(const [m,formula,when] of [['wsjf',/value \+ time criticality \+ enablement\) \/ effort/i,/WSJF needs:/],['rice',/reach \* impact \* confidence \/ effort|reach × impact × confidence \/ effort/i,/RICE needs:/],['kano',/category/i,/Kano needs:/]]){
    await h.pick(m);const help=h.get('assess-method-help');assert.ok(help,'help for '+m);
    const text=help.all().map(n=>n.textContent).join(' ');assert.match(text,formula,m);assert.match(text,when,m);
  }
  const all=texts(h).join('\n');assert.match(all,/WSJF needs:/);assert.doesNotMatch(all,/When to use/);
  await h.pick('kano');assert.match(h.get('assess-method-help').all().map(n=>n.textContent).join(' '),/no numeric score/i);
});

test('every input and choice group has a help line that aria-describedby points at',async()=>{
  const h=harness();
  const check=(control,name,pattern)=>{assert.ok(control,name+' exists');const found=helpFor(h,control);assert.ok(found.length>0,name+' has aria-describedby help');
    const text=found.map(n=>n.textContent).join(' ');if(pattern)assert.match(text,pattern,name);return text;};
  await h.pick('wsjf');
  for(const key of ['value','time_criticality','enablement','effort'])assert.match(check(h.get('assess-input-'+key),key),SUFFIX);
  assert.match(check(h.get('assess-input-value'),'value'),/1–10/);
  await h.pick('rice');
  for(const key of ['reach','impact','confidence','effort'])assert.match(check(h.get('assess-input-'+key),key),SUFFIX);
  assert.match(check(h.get('assess-input-reach'),'reach'),/period/);
  assert.match(check(h.get('assess-input-effort'),'rice effort'),/person-days/);
  assert.match(check(h.get('assess-input-confidence'),'rice confidence'),/0–1/);
  assert.match(check(h.get('assess-input-confidence'),'rice confidence'),/0\.8/);
  for(const id of ['assess-version','assess-basis','assess-provenance'])check(h.get(id),id);
  assert.match(check(h.get('assess-version'),'version'),/v1/);
  const group=name=>h.body.all().find(n=>n.tag==='fieldset'&&n.children[0]?.textContent===name);
  check(group('Assessment confidence'),'assessment confidence',/whole assessment/);
  check(group('Assumptions'),'assumptions');
  check(group('Assessment method'),'method group');
  await h.pick('kano');
  check(group('Assessment inputs'),'kano category',/categor/i);check(group('Category hypothesis'),'kano hypothesis',/hypothesis/i);
});

test('blank inputs show Unknown with the missing field names, and a full set shows the score',async()=>{
  const h=harness();await h.pick('wsjf');
  const line=()=>h.get('assess-score-status').textContent;
  assert.match(line(),/^Score: Unknown — missing: Value, Time criticality, Enablement, Effort/);
  h.get('assess-input-value').input('8');h.get('assess-input-time_criticality').input('4');h.get('assess-input-enablement').input('2');
  assert.match(line(),/^Score: Unknown — missing: Effort/);
  h.get('assess-input-effort').input('2');assert.match(line(),/^Score: 7/);
  await h.pick('rice');assert.match(line(),/missing: Reach, Impact, RICE confidence, Effort/);
});

test('no help text claims a zero score for missing input',async()=>{
  const h=harness();
  for(const m of ['wsjf','rice','kano']){await h.pick(m);
    for(const n of h.body.all().filter(n=>n.className==='help'||n.id==='assess-method-help'||n.id==='assess-score-status')){
      assert.doesNotMatch(n.textContent,/(score|result)[^.]*\b(is|becomes|=|counts as)\s*(0\b|zero)/i,n.textContent);assert.doesNotMatch(n.textContent,/blank[^.]*\bzero\b|blank[^.]*\b0\b/i,n.textContent);}}
});

test('a disabled Accept names the missing numbers in assess-accept-reason', async()=>{
  const h=harness();await h.pick('wsjf');
  h.get('assess-input-value').input('5');
  const inFoot=id=>h.foot.all().find(n=>n.id===id);
  const accept=inFoot('assess-accept'),why=inFoot('assess-accept-reason');
  assert.equal(accept.disabled,true);assert.ok(why,'reason is rendered');
  assert.equal(accept['aria-describedby'],'assess-accept-reason');assert.equal(accept.title,why.textContent);
  assert.match(why.className,/accept-reason/);
  assert.match(why.textContent,/Time criticality, Enablement and Effort need numbers; Effort must be above 0/);
  assert.doesNotMatch(why.textContent,/Value/);
  await h.pick('rice');
  assert.match(inFoot('assess-accept-reason').textContent,/Reach, Impact, Confidence and Effort need numbers; Effort must be above 0 and Confidence between 0 and 1/);
});
