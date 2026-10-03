// Actual app/controller with a bounded DOM harness, not browser proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web=new URL('../../glitch-idea/web/',import.meta.url);
const url=text=>'data:text/javascript;base64,'+Buffer.from(text).toString('base64');
const apiUrl=url(await readFile(new URL('api.js',web),'utf8'));
const foldsUrl=url(await readFile(new URL('folds.js',web),'utf8'));
const {STEPS}=await import(foldsUrl);
const app=(await readFile(new URL('app.js',web),'utf8')).replace("'./api.js'",JSON.stringify(apiUrl)).replace("'./folds.js'",JSON.stringify(foldsUrl));
const {startApp,registerStep}=await import(url(app));
const idea='idea_'+'1'.repeat(32);

async function fixture(run,{search='?idea_id='+idea,stateRead=null}={}) {
  const names=['document','location','history','addEventListener'];
  const saved=new Map(names.map(name=>[name,Object.getOwnPropertyDescriptor(globalThis,name)]));
  let doc;
  class Node {
    constructor(tag='div') {this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.listeners={};this.scrollTop=0;this.scrollLeft=0;this.disabled=false;this.value='';}
    append(...nodes) {this.children.push(...nodes);}
    contains(node) {return node===this||this.children.some(child=>child.contains(node));}
    replaceChildren(...nodes) {if(this.children.some(child=>child.contains(doc.activeElement)))doc.activeElement=doc.body;this.children=nodes;}
    setAttribute(name,value) {this[name]=value;}
    addEventListener(name,callback) {this.listeners[name]=callback;}
    focus() {if(!this.disabled)doc.activeElement=this;}
    setSelectionRange(start,end) {this.selectionStart=start;this.selectionEnd=end;}
  }
  const roots=new Map(['announcement','identity','progress','save-status','agent-status','compact-nav','columns','connection'].map(id=>{const node=new Node();node.id=id;return[id,node];}));
  const find=(node,id)=>node.id===id?node:node.children.map(child=>find(child,id)).find(Boolean);
  doc={body:new Node('body'),activeElement:null,hidden:true,createElement:tag=>new Node(tag),getElementById:id=>[...roots.values()].map(root=>find(root,id)).find(Boolean)??null};
  doc.activeElement=doc.body;globalThis.document=doc;
  Object.defineProperty(globalThis,'location',{configurable:true,value:new URL('http://127.0.0.1:1234/'+search)});
  globalThis.history={replaceState(){}};globalThis.addEventListener=()=>{};
  const state={ok:true,idea_id:idea,revision:1,draft_version:0,backlog_revision:1,current_step:'shape',accepted:{},drafts:{shape:{outcome:'Human words'}},draft:null,
    steps:Object.fromEntries(STEPS.map(({key})=>[key,{status:['capture','priorities'].includes(key)?'saved':key==='shape'?'current':'todo',
      evidence_id:['capture','priorities'].includes(key)?'evidence-'+key:null,accepted_revision:['capture','priorities'].includes(key)?1:null}]))};
  let finishDraft;
  const writes=[];
  const reads=[];
  const api={session:async()=>({agent_status:'disconnected'}),state:async ideaId=>{reads.push(ideaId);if(stateRead)return stateRead(ideaId,state,reads.length);return structuredClone(state);},write:async(operation,payload)=>{
    writes.push({operation,payload});
    if(operation==='draft')return new Promise(resolve=>{finishDraft=()=>{state.draft_version++;state.drafts.shape=structuredClone(payload.fields);resolve({ok:true,write_state:'applied'});};});
    if(operation==='navigate')return {ok:true,write_state:'applied'};
    return {ok:true};
  }};
  registerStep('shape',({body,field,flow,edited})=>field(body,'Outcome','shape-outcome',flow.buffers.shape.outcome,
    value=>edited('shape',{...flow.buffers.shape,outcome:value}),true));
  let flow;
  try {flow=startApp(api);await new Promise(resolve=>setImmediate(resolve));await run({flow,doc,state,writes,reads,finish:()=>finishDraft()});}
  finally {flow?.dispose();for(const[name,descriptor]of saved){if(descriptor)Object.defineProperty(globalThis,name,descriptor);else delete globalThis[name];}}
}

test('same-step draft saving keeps text focus, selection and panel scroll, and typing during it survives',async()=>{
  // Redesign: a draft save (autosave, terminal fills) never locks typing; keystrokes made meanwhile were dropped.
  await fixture(async({flow,doc,finish})=>{
    let input=doc.getElementById('shape-outcome');input.focus();input.setSelectionRange(2,7);
    doc.getElementById('step-body').scrollTop=410;
    flow.edit('shape',{outcome:'Edited human words'});
    const saving=flow.save('shape',true);
    input=doc.getElementById('shape-outcome');
    assert.equal(flow.busy,true);assert.equal(input.disabled,false);assert.equal(input.readOnly,false);
    assert.equal(doc.activeElement,input);assert.deepEqual([input.selectionStart,input.selectionEnd],[2,7]);
    assert.equal(doc.getElementById('step-body').scrollTop,410);
    flow.edit('shape',{outcome:'Edited human words, typed during the save'});
    finish();assert.equal(await saving,true);
    input=doc.getElementById('shape-outcome');assert.equal(doc.activeElement,input);assert.equal(input.readOnly,false);
    assert.equal(input.value,'Edited human words, typed during the save');assert.ok(flow.dirty.has('shape'),'the newer text is still unsaved');
    assert.equal(doc.getElementById('step-body').scrollTop,410);
    assert.equal(flow.open('capture'),true);assert.equal(doc.getElementById('step-body').scrollTop,0);
  });
});

test('Pause/Resume transitions and error actions have stable restoration targets',async()=>{
  await fixture(async({flow,doc})=>{
    const pause=doc.getElementById('pause-workflow');pause.focus();await pause.listeners.click();
    assert.equal(flow.paused,true);
    assert.equal(doc.activeElement,doc.getElementById('resume-workflow'));
    await doc.getElementById('resume-workflow').listeners.click();
    assert.equal(flow.paused,false);assert.equal(doc.activeElement,doc.getElementById('pause-workflow'));
    flow.error={code:'connection_lost'};flow.pending={ambiguous:false};flow.onChange();
    const reload=doc.getElementById('reload-state');assert.ok(doc.getElementById('retry-save'));reload.focus();
    flow.onChange();assert.equal(doc.activeElement,doc.getElementById('reload-state'));
  });
});

test('first-load without a URL idea adopts the server private selection instead of looping (SF-B)',async()=>{
  await fixture(async({flow,doc,reads})=>{
    assert.equal(flow.selectionUncertain,false,'must not freeze on a documented private-selection reply');
    assert.equal(flow.state?.idea_id,idea);
    assert.equal(doc.getElementById('selection-recover'),null);
    assert.deepEqual(reads,[null]);
  },{search:''});
});

test('first-load without a URL idea keeps the freeze when unsaved answers exist (SF-B guard)',async()=>{
  let release;
  const gate=new Promise(resolve=>{release=resolve;});
  await fixture(async({flow})=>{
    flow.edit('capture',{...flow.buffers.capture,raw_text:'Typed before load'});
    assert.ok(flow.dirty.has('capture'),'fixture must create a real unsaved answer');
    release();await new Promise(resolve=>setImmediate(resolve));await new Promise(resolve=>setImmediate(resolve));
    assert.equal(flow.selectionUncertain,true,'dirty answers must never be adopted into another idea');
    assert.equal(flow.state,null);
  },{search:'',stateRead:async(ideaId,state)=>{await gate;return structuredClone(state);}});
});

test('Reload after a failed first read requests the URL idea, not the private selection (SF-A)',async()=>{
  await fixture(async({flow,doc,reads})=>{
    assert.equal(flow.state,null);assert.ok(flow.error);
    await doc.getElementById('reload-state').listeners.click();
    assert.deepEqual(reads,[idea,idea]);
    assert.equal(flow.state?.idea_id,idea);assert.equal(flow.error,null);
  },{stateRead:(ideaId,state,count)=>{if(count===1)throw Object.assign(new Error('lost'),{code:'connection_lost'});return structuredClone(state);}});
});

test('Reload before any state keeps a pending write\'s error and its Try again door (Review r2 note 1)',async()=>{
  await fixture(async({flow,doc,reads})=>{
    assert.equal(flow.state,null);assert.ok(flow.error);
    flow.error={code:'connection_lost'};flow.pending={ambiguous:false};flow.onChange();
    await doc.getElementById('reload-state').listeners.click();
    assert.deepEqual(reads,[idea,idea]);
    assert.equal(flow.state?.idea_id,idea);
    assert.equal(flow.error?.code,'connection_lost','a pending write keeps its error');
    assert.ok(doc.getElementById('retry-save'),'Try again must stay reachable');
  },{stateRead:(ideaId,state,count)=>{if(count===1)throw Object.assign(new Error('lost'),{code:'connection_lost'});return structuredClone(state);}});
});

test('Resume clears a stale error after a verified read (Review r2 note 2)',async()=>{
  await fixture(async({flow,doc})=>{
    await doc.getElementById('pause-workflow').listeners.click();assert.equal(flow.paused,true);
    flow.error={code:'connection_lost'};flow.onChange();
    await doc.getElementById('resume-workflow').listeners.click();
    assert.equal(flow.paused,false);assert.equal(flow.error,null);
  });
});

test('Resume keeps a pending write\'s error (Review r3 note 2)',async()=>{
  await fixture(async({flow,doc})=>{
    await doc.getElementById('pause-workflow').listeners.click();assert.equal(flow.paused,true);
    flow.error={code:'connection_lost'};flow.pending={ambiguous:false};flow.onChange();
    await doc.getElementById('resume-workflow').listeners.click();
    assert.equal(flow.paused,false);assert.equal(flow.error?.code,'connection_lost','a pending write keeps its error');
  });
});

test('a queued second Resume is a no-op once the first has resumed (Review r3 note 3)',async()=>{
  await fixture(async({flow,doc,reads})=>{
    await doc.getElementById('pause-workflow').listeners.click();assert.equal(flow.paused,true);
    const before=reads.length,resume=doc.getElementById('resume-workflow');
    const first=resume.listeners.click(),second=resume.listeners.click();
    await first;assert.equal(flow.paused,false);
    flow.paused=true;await second;
    assert.equal(flow.paused,true,'a queued Resume must not undo a later Pause');
    assert.equal(reads.length-before,1);
  });
});

test('Reload with verified state waits out a Resume in flight (Review r3 note 1)',async()=>{
  let inFlight=0,most=0,gated=false;
  await fixture(async({flow,doc})=>{
    await doc.getElementById('pause-workflow').listeners.click();assert.equal(flow.paused,true);
    gated=true;
    const resuming=doc.getElementById('resume-workflow').listeners.click();
    await Promise.resolve();
    flow.paused=false;flow.error={code:'stale_revision'};flow.onChange();
    const reloading=doc.getElementById('reload-state').listeners.click();
    await Promise.all([resuming,reloading]);
    assert.equal(most,1,'Reload overlapped the Resume read');
  },{stateRead:async(ideaId,state)=>{if(!gated)return structuredClone(state);
    inFlight++;most=Math.max(most,inFlight);await new Promise(resolve=>setImmediate(resolve));inFlight--;return structuredClone(state);}});
});

test('Resume reads one at a time and never overlaps another load (Review r2 note 2)',async()=>{
  let inFlight=0,most=0,gated=false;
  await fixture(async({flow,doc,reads})=>{
    await doc.getElementById('pause-workflow').listeners.click();assert.equal(flow.paused,true);
    gated=true;const before=reads.length;
    const resume=doc.getElementById('resume-workflow');
    await Promise.all([resume.listeners.click(),resume.listeners.click()]);
    assert.equal(most,1,'two Resume reads overlapped');
    assert.equal(reads.length-before,1);assert.equal(flow.paused,false);
  },{stateRead:async(ideaId,state)=>{if(!gated)return structuredClone(state);
    inFlight++;most=Math.max(most,inFlight);await new Promise(resolve=>setImmediate(resolve));inFlight--;return structuredClone(state);}});
});

test('a queued Resume still reads after the first Resume read rejects (Review r4 note 2)',async()=>{
  let gated=false,count=0;
  await fixture(async({flow,doc})=>{
    await doc.getElementById('pause-workflow').listeners.click();assert.equal(flow.paused,true);
    gated=true;
    const resume=doc.getElementById('resume-workflow');
    await Promise.all([resume.listeners.click(),resume.listeners.click()]);
    assert.equal(count,2,'the queued Resume must read after the rejected one');
    assert.equal(flow.paused,false);
  },{stateRead:async(ideaId,state)=>{if(!gated)return structuredClone(state);
    count++;await new Promise(resolve=>setImmediate(resolve));
    if(count===1)throw Object.assign(new Error('lost'),{code:'connection_lost'});return structuredClone(state);}});
});

test('Resume clicked twice behind a held verified Reload waits for it and reads once (Review r4 notes 1 and 2)',async()=>{
  let gated=false,count=0,release;
  const held=new Promise(resolve=>{release=resolve;});
  await fixture(async({flow,doc})=>{
    gated=true;flow.error={code:'stale_revision'};flow.onChange();
    const reloading=doc.getElementById('reload-state').listeners.click();
    await doc.getElementById('pause-workflow').listeners.click();assert.equal(flow.paused,true);
    const before=count,resume=doc.getElementById('resume-workflow');
    const resuming=Promise.all([resume.listeners.click(),resume.listeners.click()]);
    for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));
    assert.equal(count,before,'Resume must not read while the Reload holds the slot');
    release();await Promise.all([reloading,resuming]);
    assert.equal(count-before,1,'exactly one Resume read after the Reload');
    assert.equal(flow.paused,false);
  },{stateRead:async(ideaId,state)=>{if(!gated)return structuredClone(state);
    count++;if(count===1)await held;return structuredClone(state);}});
});

test('overlapping first loads share one read and never freeze the adopted selection (Review #1)',async()=>{
  await fixture(async({flow,doc,reads})=>{
    assert.equal(flow.state,null);
    const reload=doc.getElementById('reload-state');
    await Promise.all([reload.listeners.click(),reload.listeners.click()]);
    assert.equal(flow.selectionUncertain,false);
    assert.equal(flow.state?.idea_id,idea);
    assert.deepEqual(reads,[null,null]);
  },{search:'',stateRead:async(ideaId,state,count)=>{if(count===1)throw Object.assign(new Error('lost'),{code:'connection_lost'});
    await new Promise(resolve=>setImmediate(resolve));return structuredClone(state);}});
});

test('a foreign session on the adopt path freezes, and a verified recover clears the stale error (Review #2)',async()=>{
  await fixture(async({flow,doc})=>{
    assert.equal(flow.selectionUncertain,true,'session_id is still pinned when the idea is adopted');
    assert.equal(flow.state,null);
    await doc.getElementById('selection-recover').listeners.click();
    assert.equal(flow.selectionUncertain,false);
    assert.equal(flow.state?.idea_id,idea);
    assert.equal(flow.error,null);
  },{search:'',stateRead:(ideaId,state,count)=>count===1?{...structuredClone(state),session_id:'session_foreign'}:structuredClone(state)});
});
