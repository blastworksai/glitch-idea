// Actual startApp + IdeaApi/Flow + packaged upload renderer.
// Fixed DOM/HTTP fixtures do not qualify native picker, layout or publication.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web=new URL('../../glitch-idea/web/',import.meta.url);
const data=source=>'data:text/javascript;base64,'+Buffer.from(source).toString('base64');
const apiUrl=data(await readFile(new URL('api.js',web),'utf8'));
const foldsUrl=data(await readFile(new URL('folds.js',web),'utf8'));
const {IdeaApi}=await import(apiUrl),{STEPS}=await import(foldsUrl);
const visualize=await import(data((await readFile(new URL('steps/visualize.js',web),'utf8')).replace("'../folds.js'",JSON.stringify(foldsUrl))));
const appSource=(await readFile(new URL('app.js',web),'utf8')).replace("'./api.js'",JSON.stringify(apiUrl)).replace("'./folds.js'",JSON.stringify(foldsUrl));
const {startApp,loadStepModules,registerCaptureUploads}=await import(data(appSource));
const copy=value=>structuredClone(value);
const IDEA='idea_'+'1'.repeat(32),SESSION='session_'+'2'.repeat(32),BINDING='binding_'+'3'.repeat(32),ASSET='asset_'+'4'.repeat(32),UPLOAD='upload_'+'5'.repeat(32),HASH='a'.repeat(64);
const WORDS='Exact first line\r\n<script>literal words</script>\nFinal line  ';
const CAPTURE={raw_text:WORDS,workspace:{name:'Explicit',path:'/fixture',confirmed:true}};
function localFile(name='original.md',text='fixture') {const blob=new Blob([text],{type:'text/markdown'});Object.defineProperty(blob,'name',{value:name});return blob;}
async function fixture(run,{saved=false,archived=false,packaged=true}={}) {
  const moduleFetches=[],imports=[];
  await loadStepModules(async route=>{moduleFetches.push(route);return {ok:packaged&&route==='./steps/visualize.js',status:packaged&&route==='./steps/visualize.js'?200:404};},async route=>{imports.push(route);return visualize;});
  const globals=['document','location','history','addEventListener','setTimeout','clearTimeout'];
  const old=new Map(globals.map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
  const oldUrls=new Map(['createObjectURL','revokeObjectURL'].map(key=>[key,Object.getOwnPropertyDescriptor(URL,key)]));
  let doc,currentUrl=new URL('http://127.0.0.1:1234/?binding='+BINDING+(saved?'&idea_id='+IDEA:'')),timerSequence=0;
  const timers=new Map(),events={},objectUrls=[],revoked=[],anchors=[];
  class Node {
    constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.listeners={};this.disabled=false;this.value='';this.textContent='';this.scrollTop=0;this.scrollLeft=0;}
    append(...nodes){this.children.push(...nodes);}
    contains(node){return node===this||this.children.some(child=>child.contains(node));}
    replaceChildren(...nodes){if(this.children.some(child=>child.contains(doc.activeElement)))doc.activeElement=doc.body;this.children=nodes;}
    setAttribute(key,value){this[key]=value;}
    addEventListener(key,callback){this.listeners[key]=callback;}
    focus(){if(!this.disabled)doc.activeElement=this;}
    setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;}
    async click(){if(!this.disabled){if(this.tagName==='A'){anchors.push(this);return;}return this.listeners.click?.({target:this});}}
    input(value){if(!this.disabled&&!this.readOnly){this.value=value;return this.listeners.input?.({target:this});}}
    change(changes){if(!this.disabled){Object.assign(this,changes);return this.listeners.change?.({target:this});}}
    all(){return [this,...this.children.flatMap(child=>child.all())];}
    set innerHTML(value){throw new Error('HTML injection forbidden');}
  }
  const roots=new Map(['announcement','identity','progress','save-status','agent-status','compact-nav','columns','connection'].map(id=>{const node=new Node();node.id=id;return [id,node];}));
  const find=(node,id)=>node.id===id?node:node.children.map(child=>find(child,id)).find(Boolean);
  doc={body:new Node('body'),activeElement:null,hidden:true,createElement:tag=>new Node(tag),getElementById:id=>[...roots.values()].map(root=>find(root,id)).find(Boolean)??null};
  doc.activeElement=doc.body;globalThis.document=doc;
  Object.defineProperty(globalThis,'location',{configurable:true,get:()=>currentUrl});
  globalThis.history={replaceState:(_state,_title,route)=>{currentUrl=new URL(route,currentUrl);}};
  globalThis.addEventListener=(name,callback)=>{events[name]=callback;};
  globalThis.setTimeout=(callback,delay)=>{const handle={id:++timerSequence,unref(){}};timers.set(handle,{callback,delay});return handle;};
  globalThis.clearTimeout=handle=>timers.delete(handle);
  URL.createObjectURL=blob=>{objectUrls.push(blob);return 'blob:fixture/'+objectUrls.length;};URL.revokeObjectURL=value=>revoked.push(value);
  const state={ok:true,code:'ok',session_id:SESSION,idea_id:saved?IDEA:null,idea_status:saved?(archived?'archived':'active'):null,
    revision:saved?1:0,draft_version:0,backlog_revision:saved?1:0,current_step:'capture',agent_status:'disconnected',
    steps:Object.fromEntries(STEPS.map(({key})=>[key,{status:key==='capture'?(saved?'saved':'current'):'todo',accepted_revision:key==='capture'&&saved?1:null,evidence_id:key==='capture'&&saved?'capture-fixture':null}])),
    accepted:saved?{capture:copy(CAPTURE)}:{},drafts:{},draft:null,capabilities:{uploads:true},
    asset_inventory_status:{available:saved,code:saved?'ok':'no_selection'},asset_inventory:saved?{records:[],total:0,projected:0,omitted:0,orphans:{count:0,ids:[]}}:null};
  const calls=[],receipts=new Map(),faults={};
  const response=(body,status=200)=>({ok:status>=200&&status<300,status,json:async()=>copy(body)});
  const add=record=>{state.asset_inventory.records.push({record:copy(record),evidence:{record_id:record.kind==='asset'?record.asset_id:record.upload_id,path:'assets/evidence/'+HASH+'.md',sha256:HASH},
    blob:record.kind==='asset'?{path:record.blob_path,size:record.size,sha256:record.sha256}:null});state.asset_inventory.total=state.asset_inventory.projected=state.asset_inventory.records.length;};
  const intent=()=>({schema_version:1,kind:'upload-intent',idea_id:IDEA,session_id:SESSION,source_revision:state.revision,upload_id:UPLOAD,asset_id:ASSET,
    name:'original.md',declared_type:'text/markdown',size:7,actor:'Operator',timestamp:'fixture'});
  const complete=()=>add({...intent(),kind:'asset',blob_path:'assets/blobs/'+ASSET+'.bin',validated_type:'text/markdown',sha256:HASH});
  const fetcher=async(route,options)=>{
    calls.push({route,method:options.method,headers:copy(options.headers),payload:typeof options.body==='string'?JSON.parse(options.body):options.body});
    if(route==='/api/v1/session')return response({ok:true,code:'ok',binding_id:BINDING,session_id:SESSION,csrf_token:'fixture-only'});
    if(route.startsWith('/api/v1/state'))return response(state);
    if(route==='/api/v1/transport')return response({ok:true,code:'ok'});
    if(route.startsWith('/api/v1/requests/')){const result=receipts.get(decodeURIComponent(route.slice('/api/v1/requests/'.length)));return result?response(result):response({ok:false,code:'request_not_found'},404);}
    if(route==='/api/v1/capture'){
      const payload=JSON.parse(options.body);state.idea_id=IDEA;state.idea_status='active';state.revision=1;state.backlog_revision=1;state.current_step='priorities';
      state.steps.capture={status:'saved',accepted_revision:1,evidence_id:'capture-fixture'};state.steps.priorities.status='current';
      state.accepted.capture={raw_text:payload.raw_text,workspace:copy(payload.workspace)};state.asset_inventory={records:[],total:0,projected:0,omitted:0,orphans:{count:0,ids:[]}};state.asset_inventory_status={available:true,code:'ok'};
      const result={ok:true,code:'ok',write_state:'applied',request_id:payload.request_id,idea_id:IDEA};receipts.set(payload.request_id,result);return response(result);
    }
    if(route==='/api/v1/uploads'){
      if(faults.metadataRefusal)return response({ok:false,code:faults.metadataRefusal,write_state:'not_applied'},409);
      const payload=JSON.parse(options.body);
      if(payload.expected_revision!==state.revision)return response({ok:false,code:'stale_revision',write_state:'not_applied'},409);
      add({...intent(),name:payload.name,declared_type:payload.declared_type,size:payload.size});
      const result={ok:true,code:'ok',write_state:'applied',request_id:payload.request_id,idea_id:IDEA,upload_id:UPLOAD,asset_id:ASSET,completion_request_id:'upload-bytes:'+UPLOAD};receipts.set(payload.request_id,result);
      if(faults.metadataAck)throw new Error('lost ACK');return response(result);
    }
    if(route==='/api/v1/uploads/'+UPLOAD+'/bytes'){
      const savedIntent=state.asset_inventory.records.find(entry=>entry.record.kind==='upload-intent').record;
      add({...savedIntent,kind:'asset',blob_path:'assets/blobs/'+ASSET+'.bin',validated_type:savedIntent.declared_type,sha256:HASH});
      const result={ok:true,code:'ok',write_state:'applied',request_id:'upload-bytes:'+UPLOAD,idea_id:IDEA,upload_id:UPLOAD,asset_id:ASSET};receipts.set(result.request_id,result);return response(result);
    }
    if(route==='/api/v1/attachments/'+ASSET){const asset=state.asset_inventory.records.find(entry=>entry.record.kind==='asset').record;
      return new Response(new Blob([new Uint8Array(asset.size)]),{headers:{'Content-Length':String(asset.size),'Content-Type':'application/octet-stream'}});}
    throw new Error('Unexpected fixture request '+route);
  };
  let flow;
  try {
    const api=new IdeaApi(fetcher);flow=startApp(api);await new Promise(resolve=>setImmediate(resolve));
    await run({flow,api,state,calls,receipts,faults,doc,complete,moduleFetches,imports,events,timers,objectUrls,revoked,anchors,
      get:id=>doc.getElementById(id),get currentUrl(){return currentUrl;},text:()=>[...roots.values()].flatMap(node=>node.all()).map(node=>node.textContent).join('\n')});
  } finally {
    events.pagehide?.();flow?.dispose();
    for(const [key,descriptor]of old)if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];
    for(const [key,descriptor]of oldUrls)if(descriptor)Object.defineProperty(URL,key,descriptor);else delete URL[key];
  }
}
const uploadWrites=h=>h.calls.filter(call=>call.route==='/api/v1/uploads'||call.route.endsWith('/bytes'));

test('actual app initial Capture has words-first notice and no chooser even with packaged uploader',async()=>{
  await fixture(async h=>{
    assert.equal(h.flow.state.idea_id,null);assert.equal(h.get('file-input'),null);assert.match(h.get('capture-attachments').textContent,/words and workspace first/);
    assert.deepEqual(h.imports,['./steps/visualize.js']);assert.ok(h.moduleFetches.includes('./steps/visualize.js'));assert.equal(uploadWrites(h).length,0);
  });
});
test('save exact words then reopen Capture to upload original file without set acceptance or text mutation',async()=>{
  await fixture(async h=>{
    h.flow.edit('capture',copy(CAPTURE));assert.equal(await h.flow.save('capture'),true);assert.equal(h.flow.current,'priorities');assert.equal(h.get('file-input'),null);
    await h.get('compact-capture').click();assert.equal(h.get('idea-text').value,WORDS);assert.equal(h.get('file-input').multiple,true);
    h.get('file-input').change({files:[localFile()]});assert.equal(uploadWrites(h).length,0);await h.get('upload-action-1').click();
    assert.deepEqual(uploadWrites(h).map(call=>call.method),['POST','PUT']);assert.equal(h.state.revision,1);assert.equal(h.state.accepted.capture.raw_text,WORDS);assert.equal(h.flow.buffers.capture.raw_text,WORDS);
    assert.equal(h.state.accepted.visualize,undefined);assert.equal(h.get('visualize-accept'),null);assert.ok(h.get('uploaded-'+ASSET));
    assert.ok(!h.calls.some(call=>call.route.includes('visual-set')||call.route.includes('visual-disposition')));
    assert.equal(h.currentUrl.searchParams.get('binding'),BINDING);assert.equal(h.currentUrl.searchParams.get('idea_id'),IDEA);
  });
});
test('saved Capture preserves dirty human words and readonly accepted text throughout actual helper upload',async()=>{
  await fixture(async h=>{
    h.get('idea-text').input('New human words\n kept  ');assert.equal(h.flow.buffers.capture.raw_text,'New human words\n kept  ');
    h.get('file-input').change({files:[localFile('../../<script>literal</script>.md')]});await h.get('upload-action-1').click();
    assert.equal(h.flow.buffers.capture.raw_text,'New human words\n kept  ');assert.equal(h.state.accepted.capture.raw_text,WORDS);assert.equal(h.get('idea-text').value,'New human words\n kept  ');
    assert.equal(h.flow.status('capture'),'unsaved');assert.ok(h.text().includes('../../<script>literal</script>.md'));assert.equal(uploadWrites(h).length,2);
  },{saved:true});
});
test('saved Capture metadata ACK loss reuses helper read-only recovery before explicit byte send',async()=>{
  await fixture(async h=>{
    h.faults.metadataAck=true;h.get('file-input').change({files:[localFile()]});await h.get('upload-action-1').click();assert.equal(uploadWrites(h).length,1);
    assert.equal(h.get('upload-action-1').textContent,'Check upload result');h.faults.metadataAck=false;await h.get('upload-action-1').click();assert.equal(uploadWrites(h).length,1);
    await h.get('upload-action-1').click();assert.equal(uploadWrites(h).length,2);assert.equal(h.flow.state.revision,1);assert.equal(h.flow.status('capture'),'saved');
  },{saved:true});
});
test('reopened Capture renders literal immutable originals and archived downloads without new uploads',async()=>{
  await fixture(async h=>{
    h.complete();h.flow.load(h.state,true);assert.ok(h.get('uploaded-'+ASSET));assert.equal(h.get('file-input').disabled,true);assert.match(h.text(),/archived/);
    assert.equal(h.get('download-'+ASSET).disabled,false);await h.get('download-'+ASSET).click();assert.equal(h.anchors.length,1);assert.equal(h.objectUrls[0].type,'application/octet-stream');
    const revoke=[...h.timers.values()].find(timer=>timer.delay===10000);assert.ok(revoke);revoke.callback();assert.deepEqual(h.revoked,['blob:fixture/1']);
    assert.equal(uploadWrites(h).length,0);assert.equal(h.state.accepted.capture.raw_text,WORDS);
  },{saved:true,archived:true});
});
test('projection absence and unknown status refuse Capture upload without replacing its words',async()=>{
  await fixture(async h=>{
    h.state.asset_inventory_status={available:false,code:'asset_projection_capacity'};h.flow.load(h.state,true);assert.equal(h.get('file-input').disabled,true);assert.match(h.text(),/reloading cannot reduce retained history/);
    h.state.asset_inventory_status={available:true,code:'ok'};h.state.idea_status=null;h.flow.load(h.state,true);assert.equal(h.get('file-input').disabled,true);
    assert.equal(h.get('idea-text').value,WORDS);assert.equal(uploadWrites(h).length,0);
  },{saved:true});
});
test('trusted packaged uploader registration refuses nonfunctions and loader fails closed on malformed exports',async()=>{
  for(const value of [null,{},'./outside.js',42])assert.throws(()=>registerCaptureUploads(value),/Invalid packaged upload renderer/);
  await assert.rejects(loadStepModules(async route=>({ok:route==='./steps/visualize.js',status:route==='./steps/visualize.js'?200:404}),async()=>({render(){},renderUploads:null})),/Invalid packaged upload renderer/);
});
test('missing packaged Visualize uploader stays unavailable for saved Capture without a new static import',async()=>{
  await fixture(async h=>{
    assert.equal(h.get('file-input'),null);assert.match(h.get('capture-attachments').textContent,/uploader is unavailable/);
    assert.equal(h.get('idea-text').value,WORDS);assert.deepEqual(h.imports,[]);assert.equal(uploadWrites(h).length,0);
  },{saved:true,packaged:false});
});

test('saved Capture terminal upload refusal keeps exact words and requires remove and reselect',async()=>{
  await fixture(async h=>{
    h.faults.metadataRefusal='stale_revision';h.get('file-input').change({files:[localFile()]});await h.get('upload-action-1').click();
    assert.equal(h.get('upload-action-1').disabled,true);assert.match(h.text(),/Remove.*reselect/);assert.ok(!h.text().includes('stale_revision'));
    await h.get('upload-action-1').click();assert.equal(uploadWrites(h).length,1);assert.equal(h.get('idea-text').value,WORDS);
    const original=copy(uploadWrites(h)[0].payload);await h.get('upload-remove-1').click();h.faults.metadataRefusal=null;
    h.get('file-input').change({files:[localFile()]});await h.get('upload-action-2').click();assert.notEqual(uploadWrites(h)[1].payload.request_id,original.request_id);
    assert.equal(h.state.accepted.capture.raw_text,WORDS);assert.equal(uploadWrites(h).length,3);
  },{saved:true});
});
