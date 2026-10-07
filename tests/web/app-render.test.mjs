// Actual app/controller with a bounded DOM harness, not browser proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web=new URL('../../glitch-idea/web/',import.meta.url);
const url=text=>'data:text/javascript;base64,'+Buffer.from(text).toString('base64');
const apiUrl=url(await readFile(new URL('api.js',web),'utf8'));
const foldsUrl=url(await readFile(new URL('folds.js',web),'utf8'));
const {STEPS,Flow}=await import(foldsUrl);
const app=(await readFile(new URL('app.js',web),'utf8')).replace("'./api.js'",JSON.stringify(apiUrl)).replace("'./folds.js'",JSON.stringify(foldsUrl));
const {startApp,registerStep}=await import(url(app));
const idea='idea_'+'1'.repeat(32);

async function fixture(run,{search='?idea_id='+idea,stateRead=null,current='discovery',sessionRead=null}={}) {
  const names=['document','location','history','addEventListener'];
  const saved=new Map(names.map(name=>[name,Object.getOwnPropertyDescriptor(globalThis,name)]));
  let doc;
  class Node {
    constructor(tag='div') {this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.listeners={};this.scrollTop=0;this.scrollLeft=0;this.disabled=false;this.value='';}
    append(...nodes) {this.children.push(...nodes);}
    contains(node) {return node===this||this.children.some(child=>child.contains(node));}
    replaceChildren(...nodes) {if(this.children.some(child=>child.contains(doc.activeElement)))doc.activeElement=doc.body;this.children=nodes;}
    setAttribute(name,value) {this[name]=value;}
    removeAttribute(name) {delete this[name];}
    addEventListener(name,callback) {this.listeners[name]=callback;}
    focus() {if(!this.disabled)doc.activeElement=this;}
    setSelectionRange(start,end) {this.selectionStart=start;this.selectionEnd=end;}
  }
  const roots=new Map(['announcement','identity','progress','save-status','agent-status','compact-nav','columns','connection','step-rail','rail-title','theme-toggle','nav-idea','nav-ideas','nav-setup'].map(id=>{const node=new Node();node.id=id;return[id,node];}));
  const find=(node,id)=>node.id===id?node:node.children.map(child=>find(child,id)).find(Boolean);
  doc={body:new Node('body'),documentElement:{dataset:{}},activeElement:null,hidden:true,createElement:tag=>new Node(tag),getElementById:id=>[...roots.values()].map(root=>find(root,id)).find(Boolean)??null};
  doc.activeElement=doc.body;globalThis.document=doc;
  Object.defineProperty(globalThis,'location',{configurable:true,value:new URL('http://127.0.0.1:1234/'+search)});
  globalThis.history={replaceState(){}};globalThis.addEventListener=()=>{};
  const at=STEPS.findIndex(({key})=>key===current);
  const state={ok:true,idea_id:idea,idea_status:'active',revision:1,draft_version:0,backlog_revision:1,current_step:current,accepted:{},drafts:{discovery:{problem:'Human words'}},draft:null,
    steps:Object.fromEntries(STEPS.map(({key},index)=>{const done=index<at&&current!=='discovery'||['capture','priorities'].includes(key)&&current==='discovery';
      return [key,{status:done?'saved':key===current?'current':'todo',evidence_id:done?'evidence-'+key:null,accepted_revision:done?1:null}];}))};
  let finishDraft;
  const writes=[];
  const reads=[];
  const api={pair:async()=>({agent_status:'disconnected'}),session:async()=>{if(sessionRead)sessionRead();return {agent_status:'disconnected'};},state:async ideaId=>{reads.push(ideaId);if(stateRead)return stateRead(ideaId,state,reads.length);return structuredClone(state);},write:async(operation,payload)=>{
    writes.push({operation,payload});
    if(operation==='draft')return new Promise(resolve=>{finishDraft=()=>{state.draft_version++;state.drafts.discovery=structuredClone(payload.fields);resolve({ok:true,write_state:'applied'});};});
    if(operation==='navigate')return {ok:true,write_state:'applied'};
    return {ok:true};
  }};
  // Step modules are stubbed through the registry, so these tests never depend on the packaged step files.
  registerStep('discovery',({body,field,flow,edited})=>field(body,'Problem','discovery-problem',flow.buffers.discovery.problem,
    value=>edited('discovery',{...flow.buffers.discovery,problem:value}),true));
  let flow;
  try {flow=startApp(api);await new Promise(resolve=>setImmediate(resolve));await run({flow,doc,state,writes,reads,finish:()=>finishDraft()});}
  finally {flow?.dispose();for(const[name,descriptor]of saved){if(descriptor)Object.defineProperty(globalThis,name,descriptor);else delete globalThis[name];}}
}

test('same-step draft saving keeps text focus, selection and panel scroll, and typing during it survives',async()=>{
  // Redesign: a draft save (autosave, terminal fills) never locks typing; keystrokes made meanwhile were dropped.
  await fixture(async({flow,doc,finish})=>{
    let input=doc.getElementById('discovery-problem');input.focus();input.setSelectionRange(2,7);
    doc.getElementById('step-body').scrollTop=410;
    flow.edit('discovery',{problem:'Edited human words'});
    const saving=flow.save('discovery',true);
    input=doc.getElementById('discovery-problem');
    assert.equal(flow.busy,true);assert.equal(input.disabled,false);assert.equal(input.readOnly,false);
    assert.equal(doc.activeElement,input);assert.deepEqual([input.selectionStart,input.selectionEnd],[2,7]);
    assert.equal(doc.getElementById('step-body').scrollTop,410);
    flow.edit('discovery',{problem:'Edited human words, typed during the save'});
    finish();assert.equal(await saving,true);
    input=doc.getElementById('discovery-problem');assert.equal(doc.activeElement,input);assert.equal(input.readOnly,false);
    assert.equal(input.value,'Edited human words, typed during the save');assert.ok(flow.dirty.has('discovery'),'the newer text is still unsaved');
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

const stepKeys=STEPS.map(step=>step.key);
const html=await readFile(new URL('index.html',web),'utf8');
const nav=(doc,id)=>doc.getElementById(id);
// Visible text of a fake node: its own text plus its children's, since the bounded DOM has no textContent roll-up.
const textOf=node=>(node.textContent??'')+node.children.map(textOf).join('');
const walkAll=node=>[node,...node.children.flatMap(walkAll)];
const press=(node,key)=>node.listeners.keydown({key,preventDefault(){}});

test('the step bar has the eight v3 steps in order and says "of 8"',async()=>{
  assert.deepEqual(stepKeys,['capture','priorities','method','discovery','exploration','visualize','assess','review']);
  await fixture(async({doc})=>{
    const list=doc.getElementById('compact-nav').children;
    assert.equal(list.length,1);assert.equal(list[0].className,'g-steps');
    const compact=list[0].children.map(item=>item.children[0]);
    assert.equal(compact.length,8);
    assert.deepEqual(compact.map(node=>node.id),stepKeys.map(key=>'compact-'+key));
    assert.match(textOf(doc.getElementById('progress')),/ of 8 saved/);
    assert.match(doc.getElementById('step-panel').children[0].children[0].textContent,/^Step 4 of 8 /);
  });
});

test('the top bar progress reads "7 of 8 saved, 1 skipped" and never says "not applicable"',async()=>{
  await fixture(async({flow,doc})=>{
    for(const key of stepKeys)flow.state.steps[key]={status:key==='visualize'?'skipped':'saved',evidence_id:key==='visualize'?null:'evidence-'+key,accepted_revision:key==='visualize'?null:1};
    flow.onChange();
    const text=textOf(doc.getElementById('progress'));
    assert.equal(text,'7 of 8 saved, 1 skipped');
    assert.doesNotMatch(text,/not applicable/i);
  });
});

test('api.js resume keys are exactly the folds.js steps',async()=>{
  const source=await readFile(new URL('api.js',web),'utf8');
  const literal=/const STEP_KEYS = \[([^\]]*)\]/.exec(source)[1].split(',').map(part=>part.trim().replace(/'/g,''));
  assert.deepEqual(literal,stepKeys);
});

test('the APIV strip is a list: four items, Align is the current step, /glitch-plan is next in the accessible text, never role=img or display:none',async()=>{
  const lists=[...html.matchAll(/<ol class="g-apiv"([^>]*)>(.*?)<\/ol>/g)];
  assert.equal(lists.length,1);
  const [,attrs,inner]=lists[0];
  assert.match(attrs,/aria-label="APIV: Align, Plan, Implement, Verify"/);
  assert.doesNotMatch(html,/class="g-apiv"[^>]*role="img"|role="img"[^>]*class="g-apiv"/);
  const items=[...inner.matchAll(/<li\b([^>]*)>(.*?)<\/li>/g)];
  assert.equal(items.length,4);
  assert.match(items[0][1],/aria-current="step"/);
  assert.equal(items.filter(i=>/aria-current/.test(i[1])).length,1,'only Align is current');
  assert.match(items[0][2],/^<b>Align<\/b>/);
  assert.match(items[0][2],/class="sr-only">[^<]*\/glitch-plan/);
  assert.deepEqual(items.slice(1).map(i=>i[2].replace(/<[^>]*>/g,'').replace('›','').trim()),['Plan','Implement','Verify']);
  assert.equal([...inner.matchAll(/<b>/g)].length,1,'only Align is emphasised');
  assert.doesNotMatch(html,/apiv-strip|apiv-beat/);
  const css=await readFile(new URL('styles.css',web),'utf8');
  for(const [,sel,body] of css.matchAll(/([^{}]*\.g-apiv[^{}]*)\{([^}]*)\}/g))assert.doesNotMatch(body,/display\s*:\s*none/,'APIV hidden from screen readers by: '+sel.trim());
});

test('the step rail draws eight steps with state words, glyphs and one aria-current',async()=>{
  await fixture(async({doc})=>{
    const buttons=doc.getElementById('compact-nav').children[0].children.map(item=>item.children[0]);
    const words=buttons.map(b=>textOf(b.children.find(c=>c.className==='g-step__state')));
    assert.deepEqual(words,['Saved','Saved','To do','Current','To do','To do','To do','To do']);
    assert.deepEqual(buttons.map(b=>textOf(b.children.find(c=>c.className==='g-step__n'))),['1','2','3','4','5','6','7','8']);
    assert.deepEqual(buttons.map(b=>b['aria-current']),[undefined,undefined,undefined,'step',undefined,undefined,undefined,undefined]);
    const glyph=b=>b.children.find(c=>String(c.className).startsWith('bw-status')).className;
    assert.equal(glyph(buttons[0]),'bw-status bw-status--closed');assert.equal(glyph(buttons[3]),'bw-status');assert.equal(glyph(buttons[4]),'bw-status bw-status--queued');
    assert.ok(buttons.every(b=>walkAll(b).some(n=>n.tagName==='SVG'&&n['aria-hidden']==='true')),'every glyph is hidden from readers');
    const locked=buttons.map(b=>b.children.some(c=>String(c['class']).includes('g-step__lock')));
    assert.deepEqual(locked,[false,false,false,false,true,true,true,true]);
    assert.match(buttons[3]['aria-label'],/^Step 4, Discovery: Current\./);
  });
});

test('the rail words skipped and review-needed steps, and the identity, title and progress fill follow the idea',async()=>{
  await fixture(async({flow,doc})=>{
    for(const key of stepKeys)flow.state.steps[key]={status:key==='visualize'?'skipped':key==='assess'?'review-needed':'saved',evidence_id:'e',accepted_revision:1};
    flow.edit('capture',{raw_text:'First line of the idea\nsecond line',workspace:{name:'w',path:'/w',confirmed:true}});flow.dirty.clear();flow.onChange();
    const words=doc.getElementById('compact-nav').children[0].children.map(i=>textOf(i.children[0].children.find(c=>c.className==='g-step__state')));
    assert.equal(words[5],'Skipped');assert.equal(words[6],'Review');
    assert.equal(textOf(doc.getElementById('rail-title')),'First line of the idea');
    assert.match(doc.getElementById('identity').textContent,/^idea_1111…1111 · revision 1$/);
  });
});

test('only the current panel is in the columns',async()=>{
  await fixture(async({doc})=>{
    const columns=doc.getElementById('columns');
    assert.equal(columns.children.length,1);assert.equal(columns.children[0].id,'step-panel');
    assert.equal(walkAll(columns).filter(n=>String(n.id).startsWith('strip-')||String(n.id).startsWith('compact-')).length,0);
  });
});

test('the theme toggle flips data-theme, labels itself, and survives storage that throws',async()=>{
  const saved=Object.getOwnPropertyDescriptor(globalThis,'localStorage');
  Object.defineProperty(globalThis,'localStorage',{configurable:true,get(){throw new Error('storage blocked');}});
  try {
    await fixture(async({doc})=>{
      const toggle=doc.getElementById('theme-toggle');
      assert.equal(doc.documentElement.dataset.theme,undefined);assert.equal(toggle.textContent,'Light theme');assert.equal(toggle['aria-pressed'],'false');
      toggle.listeners.click();
      assert.equal(doc.documentElement.dataset.theme,'light');assert.equal(toggle.textContent,'Dark theme');assert.equal(toggle['aria-pressed'],'true');
      toggle.listeners.click();
      assert.equal(doc.documentElement.dataset.theme,undefined);assert.equal(toggle.textContent,'Light theme');
    });
  } finally {if(saved)Object.defineProperty(globalThis,'localStorage',saved);else delete globalThis.localStorage;}
});

test('the theme is remembered under one key and read back before the first render',async()=>{
  const writes=[];
  Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:k=>k==='glitch-idea-theme'?'light':null,setItem:(...a)=>writes.push(a)}});
  try {
    await fixture(async({doc})=>{
      assert.equal(doc.documentElement.dataset.theme,'light');assert.equal(doc.getElementById('theme-toggle').textContent,'Dark theme');
      doc.getElementById('theme-toggle').listeners.click();
      assert.deepEqual(writes,[['glitch-idea-theme','dark']]);
    });
  } finally {delete globalThis.localStorage;}
});

test('the agent chip is short, carries the long explanation in its title',async()=>{
  await fixture(async({doc})=>{
    const chip=doc.getElementById('agent-status');
    assert.equal(textOf(chip),'Agent disconnected');assert.match(chip.title,/Reinvoke \/glitch-idea in the initiating pane to resume AI assistance\./);
    assert.match(chip.className,/g-agent--off/);
    // Discovery draws its own standing no-terminal line, so the long sentence is not repeated as a second notice.
    assert.ok(!doc.getElementById('agent-notice'));
  });
});

test('a step that draws no notice of its own still carries the long agent sentence as a notice',async()=>{
  await fixture(async({doc})=>{
    const notice=doc.getElementById('agent-notice');
    assert.match(notice.textContent,/Reinvoke \/glitch-idea in the initiating pane to resume AI assistance\./);
  },{current:'assess'});
});

test('only the open step is called Current; a next-to-do step that is not open reads To do',async()=>{
  await fixture(async({flow,doc})=>{
    flow.state.steps.review={...flow.state.steps.review,status:'current'};flow.onChange();
    const buttons=doc.getElementById('compact-nav').children[0].children.map(i=>i.children[0]);
    const word=b=>textOf(b.children.find(c=>c.className==='g-step__state'));
    const words=buttons.map(word);
    assert.equal(words.filter(w=>w==='Current').length,1);
    assert.equal(word(buttons[3]),'Current');
    const review=buttons[buttons.length-1];
    assert.equal(word(review),'To do');assert.doesNotMatch(review['aria-label'],/Current/);assert.equal(review['aria-current'],undefined);
  });
});

test('the identity tooltip is the idea id and is cleared when there is no idea',async()=>{
  await fixture(async({flow,doc})=>{
    const identity=doc.getElementById('identity');
    assert.equal(identity.title,idea);
    flow.state=null;flow.onChange();
    assert.equal(textOf(identity),'Not saved yet');assert.equal(identity.title,undefined);
  });
});

test('the Discovery subtitle says five questions',async()=>{
  await fixture(async({doc})=>{
    const sub=walkAll(doc.getElementById('columns')).find(n=>String(n.className).includes('g-sub'));
    assert.equal(textOf(sub),'Five questions that test whether the idea is worth building, and the strongest challenges to how you framed it.');
  });
});

test('the Idea tab is selected on the workflow and returns to it from another page',async()=>{
  await fixture(async({flow,doc})=>{
    const tab=doc.getElementById('nav-idea');
    assert.equal(tab['aria-selected'],'true');assert.equal(tab['aria-current'],'page');assert.equal(doc.getElementById('nav-ideas')['aria-selected'],'false');
    flow.showSetup();
    assert.equal(doc.getElementById('nav-setup')['aria-current'],'page');assert.equal(doc.getElementById('step-rail').hidden,true);
    await tab.listeners.click();
    assert.equal(flow.view,'workflow');assert.equal(doc.getElementById('step-rail').hidden,false);
  });
});

for(const quiet of ['capture','priorities'])test('the agent status line is empty and hidden on '+quiet,async()=>{
  await fixture(async({doc})=>{
    const line=doc.getElementById('agent-status');
    assert.equal(line.textContent,'');assert.equal(line.hidden,true);
  },{current:quiet});
});

test('the agent status line is present on discovery',async()=>{
  await fixture(async({doc})=>{
    const line=doc.getElementById('agent-status');
    assert.notEqual(textOf(line),'');assert.equal(line.hidden,false);
  });
});

test('ArrowRight on the last step wraps to the first, ArrowLeft on the first to the last',async()=>{
  await fixture(async({doc})=>{
    press(nav(doc,'compact-review'),'ArrowRight');
    assert.equal(doc.activeElement,nav(doc,'compact-capture'));
    press(nav(doc,'compact-capture'),'ArrowLeft');
    assert.equal(doc.activeElement,nav(doc,'compact-review'));
    press(nav(doc,'compact-capture'),'End');
    assert.equal(doc.activeElement,nav(doc,'compact-review'));
  });
});

test('ArrowDown and ArrowUp move along the vertical rail like ArrowRight and ArrowLeft',async()=>{
  await fixture(async({doc})=>{
    press(nav(doc,'compact-review'),'ArrowDown');
    assert.equal(doc.activeElement,nav(doc,'compact-capture'));
    press(nav(doc,'compact-capture'),'ArrowUp');
    assert.equal(doc.activeElement,nav(doc,'compact-review'));
  });
});

test('a disabled Capture Accept says what is missing; an enabled one carries no reason',async()=>{
  await fixture(async({flow,doc})=>{
    const accept=doc.getElementById('capture-accept');
    assert.equal(accept.disabled,true);
    const reason=doc.getElementById('capture-accept-reason');
    assert.ok(reason,'the reason line must render');
    assert.match(reason.textContent,/Describe the idea\./);
    assert.equal(accept['aria-describedby'],'capture-accept-reason');
    assert.equal(accept.title,reason.textContent);
    flow.edit('capture',{raw_text:'An idea',workspace:{name:'w',path:'/tmp/w',confirmed:true}});
    assert.equal(doc.getElementById('capture-accept').disabled,false);
    assert.equal(doc.getElementById('capture-accept-reason'),null);
    assert.equal(doc.getElementById('capture-accept').title,undefined);
  },{current:'capture'});
});

test('memory_provenance_missing says the terminal answer is gone, names the road, and offers no futile Try again',async()=>{
  await fixture(async({flow,doc})=>{
    flow.error={code:'memory_provenance_missing'};flow.pending={ambiguous:false};flow.onChange();
    const box=doc.getElementById('columns');
    const texts=[];const walk=node=>{if(node.textContent)texts.push(node.textContent);node.children.forEach(walk);};walk(box);
    const text=texts.join(' ');
    assert.match(text,/memory answer is no longer open/);assert.match(text,/Reload this page so your terminal is asked again/);
    assert.doesNotMatch(text,/Could not save/);
    assert.equal(doc.getElementById('retry-save'),null,'a retry resends the same doomed request');
    assert.ok(doc.getElementById('reload-state'),'reload keeping answers stays available');
  });
});

test('recovery road: after the method request closed, a fresh page load sends a new method request; the same page does not',async()=>{
  // The documented page behaviour behind the wording above: autoConverse is once per idea, revision, step and agent
  // generation per page (converseTried), so only a new page load (a new Flow) asks the terminal again.
  const sid='session_'+'4'.repeat(32),gen='agent_'+'5'.repeat(32);
  const prior=['capture','priorities'];
  const state=()=>({ok:true,code:'ok',session_id:sid,idea_id:idea,idea_status:'active',revision:2,draft_version:0,backlog_revision:1,current_step:'method',
    steps:Object.fromEntries(STEPS.map(({key})=>[key,{status:prior.includes(key)?'saved':key==='method'?'current':'todo',accepted_revision:prior.includes(key)?2:null,evidence_id:prior.includes(key)?'e-'+key:null}])),
    accepted:{capture:{raw_text:'Words',workspace:{name:'F',path:'/f',confirmed:true}},priorities:{urgency:6,importance:7}},drafts:{},draft:null,
    agent_status:'connected',agent_generation:gen,human_ratings:null,assessment_summary:null,backlog_status:{available:true,code:'ok'},
    backlog:{revision:1,order:[idea],comparisons:[{idea_id:idea,revision:2,status:'active',ratings:null,assessment:null}]},
    proposal_sources:{method:{available:true,code:'ok',source:{accepted_revision:2,draft_version:0,data:{capture:{raw_text:'Words',workspace:{name:'F',path:'/f',confirmed:true}}},source_digest:'a'.repeat(64)}}},
    proposals:[],proposal_inventory:{total:0,projected:0,omitted:0,content_omitted:0,index_path:idea+'.md'}});
  const make=()=>{const writes=[];const api={state:async()=>state(),write:async(operation,payload)=>{writes.push(operation);
    return {ok:true,code:'ok',status:'pending',write_state:'not_applied',request_id:payload.request_id,session_id:sid,idea_id:idea,operation:payload.operation,
      accepted_revision:payload.expected_revision,draft_version:payload.expected_draft_version,source_digest:payload.source_digest};}};
    let n=0;const flow=new Flow(api,()=>'request-'+(++n),()=>0);flow.load(state());flow.onChange=()=>{};return {flow,writes};};
  const first=make();
  assert.equal(await first.flow.autoConverse('method'),true);assert.deepEqual(first.writes,['propose']);
  // The request is released/expired: the state carries no conversation. The same page never re-asks.
  first.flow.proposalPending=null;first.flow.load(state(),true);
  assert.equal(await first.flow.autoConverse('method'),false);assert.deepEqual(first.writes,['propose']);
  // A reload is a new page: a new Flow over the same state asks again.
  const second=make();
  assert.equal(await second.flow.autoConverse('method'),true);assert.deepEqual(second.writes,['propose']);
});

test('the Ideas tab stays enabled while only a suggestion request is in flight, and is disabled during any other write',async()=>{
  // A tab that turns off between pointer-down and pointer-up loses the click (real-browser smoke, hand_release_unlocks_step).
  await fixture(async({flow,doc})=>{
    const tab=doc.getElementById('nav-ideas');
    flow.busy=true;flow.proposalFlight=true;flow.onChange();assert.equal(tab.disabled,false);
    flow.proposalFlight=false;flow.onChange();assert.equal(tab.disabled,true);
    flow.busy=false;flow.onChange();assert.equal(tab.disabled,false);
  });
});

test('A first load refused as an older-workflow idea shows the owner\'s sentence and nothing else',async()=>{
  await fixture(async({flow,doc})=>{
    assert.equal(flow.state,null);
    assert.equal(doc.getElementById('save-status').textContent,'This idea was made with an older glitch-idea. Capture it again.');
  },{stateRead:()=>{throw Object.assign(new Error('unsupported_idea_version'),{code:'unsupported_idea_version',status:409});}});
});

test('Any other first-load error keeps the existing sentence',async()=>{
  for(const code of ['connection_lost','store_unavailable','invalid_response']){
    await fixture(async({flow,doc})=>{
      assert.equal(flow.state,null);
      assert.equal(doc.getElementById('save-status').textContent,'Could not load saved state. No empty store was assumed.');
    },{stateRead:()=>{throw Object.assign(new Error(code),{code});}});
  }
});

test('Typed-pairing load of an older-workflow idea shows the same sentence; other codes keep their text',async()=>{
  const cases=[['unsupported_idea_version','This idea was made with an older glitch-idea. Capture it again.'],
    ['connection_lost','Connected, but saved state could not be loaded. Your answers remain.']];
  for(const[code,sentence]of cases){
    await fixture(async({flow,doc})=>{
      const form=doc.getElementById('connection').children.find(child=>child.tagName==='FORM');
      assert.ok(form,'the pairing form is shown');
      await form.listeners.submit({preventDefault(){}});
      assert.equal(flow.state,null);
      assert.equal(doc.getElementById('save-status').textContent,sentence);
    },{sessionRead:()=>{throw Object.assign(new Error('x'),{status:401,code:'browser_unauthorized'});},
      stateRead:()=>{throw Object.assign(new Error(code),{code});}});
  }
});
