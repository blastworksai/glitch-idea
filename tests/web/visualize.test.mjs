// Actual renderer + Flow + IdeaApi on fixed DOM/HTTP fixtures.
// Not browser layout, native-picker, remote-client or server publication proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web=new URL('../../glitch-idea/web/',import.meta.url);
const url=source=>'data:text/javascript;base64,'+Buffer.from(source).toString('base64');
const foldsUrl=url(await readFile(new URL('folds.js',web),'utf8'));
const {Flow,STEPS}=await import(foldsUrl);
const {IdeaApi}=await import(url(await readFile(new URL('api.js',web),'utf8')));
const {render,renderUploads,assetInventory,eligibleSet,visualBrief,safeDownloadName}=await import(url(
  (await readFile(new URL('steps/visualize.js',web),'utf8')).replace("'../folds.js'",JSON.stringify(foldsUrl))));
const copy=value=>structuredClone(value);
const IDEA='idea_'+'1'.repeat(32),OTHER='idea_'+'2'.repeat(32),SESSION='session_'+'3'.repeat(32),BINDING='binding_'+'4'.repeat(32);
const HEX='a'.repeat(64),SET='set_'+'5'.repeat(32);
const CAPTURE={raw_text:'<script>literal capture</script>',workspace:{name:'Explicit',path:'/fixture',confirmed:true}};
const DISCOVERY={problem:'Ideas get lost',audience:'Solo founders',workaround:'Notes',evidence:'Interviews',kill_criteria:'No pull',challenges:[]};
const EXPLORATION={outcome:'Clear outcome',scope:'small-change',scope_reason:'One field',alternatives:[{route:'Keep it',reason:'Simpler'}],assumptions:[],next_slice:'Check it',learning:[],investment:null,experiment:null,sketch:[{title:'First slice',why_next:'Smallest',done_when:'It saves'},{title:'Second slice',why_next:'Next',done_when:'It reads back'}]};
const METHOD={selection:'bounded-plan',reason:'One result',investment:null,experiment:null,memory:{status:'unavailable',sources:[],rationale:'Fixture'}};
const fields=(disposition='accepted_set',design_set_id=null,reason=null)=>({disposition,reason,design_set_id,brief_evidence_id:null});
const id=(kind,n)=>kind+'_'+n.toString(16).padStart(32,'0');
function file(name='draft.md',text='fixture',type='text/markdown') {const blob=new Blob([text],{type});Object.defineProperty(blob,'name',{value:name});return blob;}
class Node {
  constructor(tag,text='',className=''){this.tag=tag;this.textContent=text;this.className=className;this.children=[];this.listeners={};this.disabled=false;}
  append(...children){this.children.push(...children);}
  setAttribute(key,value){this[key]=value;}
  addEventListener(key,fn){this.listeners[key]=fn;}
  async click(){if(!this.disabled)return this.listeners.click?.({target:this});}
  input(value){if(!this.disabled){this.value=value;return this.listeners.input?.({target:this});}}
  change(changes){if(!this.disabled){Object.assign(this,changes);return this.listeners.change?.({target:this});}}
  all(){return [this,...this.children.flatMap(child=>child.all())];}
  set innerHTML(value){throw new Error('HTML injection forbidden');}
}
const element=(tag,text='',className='')=>new Node(tag,text,className);
const button=(text,action,className='')=>{const node=element('button',text,className);node.type='button';node.addEventListener('click',action);return node;};
function harness({clipboard=null,uploadsOnly=false}={}) {
  const state={ok:true,code:'ok',idea_id:IDEA,idea_status:'active',session_id:SESSION,revision:4,draft_version:0,backlog_revision:0,current_step:'visualize',agent_status:'disconnected',
    steps:Object.fromEntries(STEPS.map(({key},index)=>[key,{status:index<5?'saved':key==='visualize'?'current':'todo',accepted_revision:index<5?4:null,evidence_id:index<5?'fixture-'+key:null}])),
    accepted:{capture:copy(CAPTURE),priorities:{urgency:6,importance:7},discovery:copy(DISCOVERY),exploration:copy(EXPLORATION),method:copy(METHOD)},drafts:{},draft:null,
    capabilities:{uploads:true},asset_inventory_status:{available:true,code:'ok'},asset_inventory:{records:[],total:0,projected:0,omitted:0,orphans:{count:0,ids:[]}}};
  const calls=[],receipts=new Map(),faults={},objectUrls=[],revoked=[],deferred=[],deferredDelays=[],anchors=[];let sequence=0,count=0,body,foot,connected=true;
  const sync=()=>{state.asset_inventory.total=state.asset_inventory.projected=state.asset_inventory.records.length;};
  const entry=record=>({record:copy(record),evidence:{record_id:record.kind==='design-set'?record.set_id:record.kind==='asset'?record.asset_id:record.upload_id,path:'assets/evidence/'+HEX+'.md',sha256:HEX},
    blob:record.kind==='asset'?{path:record.blob_path,size:record.size,sha256:record.sha256}:null});
  const add=record=>{state.asset_inventory.records.push(entry(record));sync();return record;};
  const common=()=>({schema_version:1,idea_id:IDEA,source_revision:state.revision,actor:'Operator',timestamp:'fixture'});
  const addAsset=(name='saved.md',size=7,type='text/markdown')=>{
    const n=++sequence;return add({...common(),kind:'asset',asset_id:id('asset',n),upload_id:id('upload',n),session_id:SESSION,
      blob_path:'assets/blobs/'+id('asset',n)+'.bin',name,declared_type:type,validated_type:type,size,sha256:HEX});
  };
  const addSet=(assets,setId=SET)=>add({...common(),kind:'design-set',set_id:setId,session_id:SESSION,source:{capture:{revision:state.steps.capture.accepted_revision,digest:HEX},discovery:{revision:state.steps.discovery.accepted_revision,digest:HEX},exploration:{revision:state.steps.exploration.accepted_revision,digest:HEX}},source_digest:HEX,
    members:assets.map(asset=>({asset_id:asset.asset_id,name:asset.name,type:asset.validated_type,size:asset.size,sha256:asset.sha256}))});
  const response=(data,status=200)=>({ok:status>=200&&status<300,status,json:async()=>copy(data)});
  const receipt=(requestId,extra={})=>({ok:true,code:'ok',write_state:'applied',request_id:requestId,idea_id:IDEA,...extra});
  const fetcher=async(route,options)=>{
    calls.push({route,method:options.method,payload:typeof options.body==='string'?JSON.parse(options.body):options.body,headers:copy(options.headers)});
    if(route.startsWith('/api/v1/requests/')){
      if(faults.reconcile)throw new Error('lost read');
      const result=receipts.get(decodeURIComponent(route.slice('/api/v1/requests/'.length)));
      return result?response(result):response({ok:false,code:'request_not_found'},404);
    }
    if(route.startsWith('/api/v1/state')){
      if(faults.read)throw new Error('lost read');return response(state);
    }
    if(route.startsWith('/api/v1/attachments/')){
      if(faults.downloadRefusal){
        const error=JSON.stringify({ok:false,code:faults.downloadRefusal.code});
        return new Response(error,{status:faults.downloadRefusal.status,headers:{
          'Content-Length':String(new TextEncoder().encode(error).byteLength),'Content-Type':'application/json'}});
      }
      const asset=state.asset_inventory.records.map(item=>item.record).find(record=>record.asset_id===route.split('/').at(-1)&&record.kind==='asset');
      const blob=new Blob([new Uint8Array(asset.size)],{type:asset.validated_type});
      return new Response(blob,{headers:{'Content-Length':String(asset.size),'Content-Type':['image/png','image/jpeg','image/webp'].includes(asset.validated_type)?asset.validated_type:'application/octet-stream'}});
    }
    if(route==='/api/v1/uploads'){
      if(faults.metadataBefore)throw new Error('lost metadata');
      if(faults.metadataRefusal)return response({ok:false,code:faults.metadataRefusal.code,write_state:'not_applied'},faults.metadataRefusal.status);
      if(JSON.parse(options.body).expected_revision!==state.revision)return response({ok:false,code:'stale_revision',write_state:'not_applied'},409);
      const payload=JSON.parse(options.body),n=++sequence;
      const intent=add({...common(),kind:'upload-intent',session_id:SESSION,upload_id:id('upload',n),asset_id:id('asset',n),
        name:payload.name,declared_type:payload.declared_type,size:payload.size});
      let result=receipt(payload.request_id,{upload_id:intent.upload_id,asset_id:intent.asset_id,completion_request_id:'upload-bytes:'+intent.upload_id});
      if(faults.metadataReceipt)result=faults.metadataReceipt(result);
      receipts.set(payload.request_id,result);
      if(faults.metadataAck)throw new Error('lost metadata acknowledgement');return response(result);
    }
    if(route.endsWith('/bytes')){
      if(faults.bytesBefore)throw new Error('lost bytes');
      if(faults.bytesRefusal)return response({ok:false,code:faults.bytesRefusal.code,write_state:'not_applied'},faults.bytesRefusal.status);
      const upload=route.split('/').at(-2),intent=state.asset_inventory.records.map(item=>item.record).find(record=>record.kind==='upload-intent'&&record.upload_id===upload);
      const requestId='upload-bytes:'+upload;
      if(!receipts.has(requestId))add({...intent,kind:'asset',blob_path:'assets/blobs/'+intent.asset_id+'.bin',validated_type:intent.declared_type,sha256:HEX});
      let result=receipt(requestId,{upload_id:upload,asset_id:intent.asset_id});
      if(faults.bytesReceipt)result=faults.bytesReceipt(result);
      receipts.set(requestId,result);
      if(faults.bytesAck)throw new Error('lost byte acknowledgement');return response(result);
    }
    const operation=route.slice('/api/v1/'.length),payload=JSON.parse(options.body);
    if(faults.writeBefore)throw new Error('lost write');
    if(faults.stale)return response({ok:false,code:'stale_revision',write_state:'not_applied'},409);
    if(operation==='draft'){state.draft_version++;state.drafts[payload.step]=copy(payload.fields);}
    if(operation==='navigate')state.current_step=payload.step;
    let extra={};
    if(operation==='visual-set/accept'||operation==='visual-disposition'){
      const accepted=copy(payload.fields);
      if(operation==='visual-set/accept'){
        assert.equal(payload.design_set_id,payload.fields.design_set_id);
        if(payload.design_set_id===null){assert.ok(Array.isArray(payload.asset_ids));addSet(payload.asset_ids.map(value=>state.asset_inventory.records.find(item=>item.record.kind==='asset'&&item.record.asset_id===value).record));}
        else assert.equal(payload.asset_ids,null);
        accepted.design_set_id=payload.design_set_id??SET;extra.design_set_id=accepted.design_set_id;
      }
      state.revision++;state.accepted.visualize=accepted;delete state.drafts.visualize;
      state.steps.visualize={status:operation==='visual-set/accept'?'saved':accepted.disposition,accepted_revision:state.revision,evidence_id:'fixture-visualize'};
    }
    const result=receipt(payload.request_id,extra);receipts.set(payload.request_id,result);
    faults.afterWrite?.();
    if(faults.writeAck)throw new Error('lost write acknowledgement');return response(result);
  };
  const api=new IdeaApi(fetcher,50,BINDING);api.csrf='fixture';api.sessionId=SESSION;
  const flow=new Flow(api,()=> 'visual-fixture-'+(++count));flow.load(state);
  const makeElement=(...args)=>{const node=element(...args);if(node.tag==='a'){anchors.push(node);node.click=()=>{node.clicked=true;};}return node;};
  const draw=()=>{
    body=element('div');foot=element('footer');
    const field=(target,label,id,value,changed,textarea=false)=>{const wrapper=element('div'),heading=element('label',label),input=element(textarea?'textarea':'input');
      heading.htmlFor=id;input.id=id;input.value=value??'';input.disabled=flow.busy||!connected;input.addEventListener('input',event=>changed(event.target.value));wrapper.append(heading,input);target.append(wrapper);return input;};
    const ctx={body,foot,flow,element:makeElement,button,field,connected,edited:(key,value)=>flow.edit(key,value),handle:action=>async()=>action(),clipboard,
      objectUrls:{createObjectURL:blob=>{objectUrls.push(blob);return 'blob:fixture/'+objectUrls.length;},revokeObjectURL:value=>revoked.push(value)},defer:(action,delay)=>{deferred.push(action);deferredDelays.push(delay);}};
    if(uploadsOnly)renderUploads(ctx);else render(ctx);
  };
  flow.onChange=draw;draw();
  const get=id=>[...body.all(),...foot.all()].find(node=>node.id===id);
  const reload=()=>{flow.load(state,true);draw();};
  return {flow,api,state,calls,receipts,faults,addAsset,addSet,add,common,sync,get,draw,reload,objectUrls,revoked,deferred,deferredDelays,anchors,
    choose:(files)=>get('file-input').change({files}),get body(){return body;},connect:value=>{connected=value;draw();}};
}
const writes=h=>h.calls.filter(call=>call.method==='POST'||call.method==='PUT');
const text=h=>h.body.all().map(node=>node.textContent).join('\n');

test('brief uses accepted current content only, literal text and saved Method',()=>{
  const h=harness();h.flow.buffers.capture.raw_text='Unsaved replacement';
  assert.ok(h.get('visualize-brief').value.includes(CAPTURE.raw_text));assert.ok(!h.get('visualize-brief').value.includes('Unsaved replacement'));
  assert.equal(h.get('visualize-brief').readOnly,true);assert.ok(h.get('visualize-brief').value.includes('Method'));
  const brief=h.get('visualize-brief').value;assert.ok(brief.includes('1. First slice — done when: It saves'));assert.ok(brief.includes('2. Second slice — done when: It reads back'));
  assert.ok(brief.includes('Problem: Ideas get lost')&&brief.includes('Who: Solo founders')&&brief.includes('Desired result: Clear outcome')&&brief.includes('Selection: Full Plan Up Front'));
  h.state.steps.method.status='review-needed';h.reload();assert.ok(!h.get('visualize-brief').value.includes('\nMethod\n'));
  h.state.steps.exploration.status='review-needed';h.reload();assert.equal(visualBrief(h.flow),null);assert.equal(h.get('visualize-new-set').disabled,true);
});
test('clipboard success and failure retain an accessible manual-copy brief',async()=>{
  const copied=[],h=harness({clipboard:{writeText:async value=>copied.push(value)}});await h.get('visualize-copy').click();assert.equal(copied[0],h.get('visualize-brief').value);
  assert.equal(h.get('visualize-copy-status').role,'status');
  const failed=harness({clipboard:{writeText:async()=>{throw new Error('denied');}}});await failed.get('visualize-copy').click();
  assert.match(failed.get('visualize-copy-status').textContent,/manually/);assert.ok(failed.get('visualize-brief'));
});
test('multi-file picker queues literal names, sends no request and selects no members',()=>{
  const h=harness();h.choose([file('../../<script>literal</script>.md'),file('second.txt','two','text/plain')]);
  assert.equal(writes(h).length,0);assert.ok(text(h).includes('../../<script>literal</script>.md'));
  assert.equal(h.get('file-input').multiple,true);assert.equal(h.get('visualize-accept').disabled,true);assert.ok(h.get('upload-action-1'));assert.ok(h.get('upload-action-2'));
});
test('invalid type, empty bytes, wrong suffix and file/count/total limits refuse before metadata',()=>{
  for(const files of [[file('empty.md','')],[file('a.exe','hi','text/plain')],[file('wrong.txt','hi','text/html')],[file('md','hi','text/markdown')],
    [file('bad\ud800.md')],[file('💡'.repeat(4094)+'.md')],
    Array.from({length:21},(_,index)=>file(index+'.md'))]){
    const h=harness();h.choose(files);assert.equal(h.get('upload-action-1'),undefined);assert.equal(writes(h).length,0);assert.match(text(h),/limits/);
  }
  const h=harness(),large=file();Object.defineProperty(large,'size',{value:25*1024*1024+1});h.choose([large]);assert.equal(h.get('upload-action-1'),undefined);
  const total=harness(),files=Array.from({length:5},(_,index)=>{const blob=file(index+'.md');Object.defineProperty(blob,'size',{value:25*1024*1024});return blob;});
  total.choose(files);assert.equal(total.get('upload-action-1'),undefined);
});
test('upload uses actual API metadata/bytes, verifies inventory and never accepts or autoselects',async()=>{
  const h=harness();h.choose([file('draft.md','hello')]);await h.get('upload-action-1').click();
  assert.deepEqual(writes(h).map(call=>call.method),['POST','PUT']);assert.equal(writes(h)[0].payload.name,'draft.md');
  assert.equal(writes(h)[0].payload.expected_revision,4);assert.equal(h.state.revision,4);assert.equal(h.flow.status('visualize'),'current');
  const asset=assetInventory(h.flow).assets[0];assert.ok(h.get('uploaded-'+asset.asset_id));assert.equal(h.get('visualize-member-'+asset.asset_id).checked,false);
  await h.get('visualize-new-set').click();assert.equal(h.get('visualize-accept').disabled,true);
});
test('lost metadata ACK stops before bytes; check recovers only identity; next explicit action sends bytes',async()=>{
  const h=harness();h.faults.metadataAck=true;h.choose([file()]);await h.get('upload-action-1').click();
  assert.equal(writes(h).length,1);assert.equal(h.get('upload-action-1').textContent,'Check upload result');
  h.faults.metadataAck=false;await h.get('upload-action-1').click();assert.equal(writes(h).length,1);assert.equal(h.get('upload-action-1').textContent,'Upload file bytes');
  await h.get('upload-action-1').click();assert.equal(writes(h).length,2);assert.equal(assetInventory(h.flow).assets.length,1);
});
test('missing metadata receipt enables explicit exact-request retry without any automatic write',async()=>{
  const h=harness();h.faults.metadataBefore=true;h.choose([file()]);await h.get('upload-action-1').click();const first=copy(writes(h)[0].payload);
  h.faults.metadataBefore=false;await h.get('upload-action-1').click();assert.equal(writes(h).length,1);
  await h.get('upload-action-1').click();assert.deepEqual(writes(h)[1].payload,first);assert.equal(writes(h).length,3);
});
test('lost byte ACK is not local completion; read-only check verifies completed inventory',async()=>{
  const h=harness();h.faults.bytesAck=true;h.choose([file()]);await h.get('upload-action-1').click();assert.equal(writes(h).length,2);
  assert.equal(h.get('upload-action-1').textContent,'Check upload result');h.faults.bytesAck=false;await h.get('upload-action-1').click();
  assert.equal(writes(h).length,2);assert.equal(h.get('upload-action-1'),undefined);assert.ok(h.get('upload-remove-1'));
});
test('missing byte receipt allows same-upload/file explicit retry only',async()=>{
  const h=harness();h.faults.bytesBefore=true;const selected=file();h.choose([selected]);await h.get('upload-action-1').click();const original=writes(h)[1];
  h.faults.bytesBefore=false;await h.get('upload-action-1').click();assert.equal(writes(h).length,2);
  await h.get('upload-action-1').click();assert.equal(writes(h)[2].route,original.route);assert.equal(writes(h)[2].payload,selected);
});
test('malformed or cross-idea metadata receipt stays uncertain and sends no bytes',async()=>{
  for(const change of [value=>({...value,idea_id:OTHER}),value=>({...value,asset_id:'../bad'}),value=>({...value,completion_request_id:'other'})]){
    const h=harness();h.faults.metadataReceipt=change;h.choose([file()]);await h.get('upload-action-1').click();
    assert.equal(writes(h).length,1);assert.equal(h.get('upload-action-1').textContent,'Check upload result');assert.equal(h.flow.status('visualize'),'current');
  }
});
test('typed but different byte asset identity refuses local completion',async()=>{
  const h=harness();h.faults.bytesReceipt=value=>({...value,asset_id:id('asset',99)});h.choose([file()]);await h.get('upload-action-1').click();
  assert.equal(h.get('upload-action-1').textContent,'Check upload result');assert.equal(h.flow.status('visualize'),'current');
});
test('metadata read loss retains uncertain phase and does not send bytes',async()=>{
  const h=harness();h.faults.read=true;h.choose([file()]);await h.get('upload-action-1').click();assert.equal(writes(h).length,1);
  assert.equal(h.get('upload-action-1').textContent,'Check upload result');h.faults.read=false;await h.get('upload-action-1').click();
  assert.equal(writes(h).length,1);assert.equal(h.get('upload-action-1').textContent,'Upload file bytes');
});
test('new set requires explicit complete members and public Flow hydrates pointer without mutating request',async()=>{
  const h=harness(),asset=h.addAsset('chosen.md'),other=h.addAsset('not-chosen.md');h.reload();await h.get('visualize-new-set').click();assert.equal(h.get('visualize-accept').disabled,true);
  h.get('visualize-member-'+asset.asset_id).change({checked:true});await h.get('visualize-accept').click();
  const request=writes(h)[0].payload;assert.deepEqual(request.asset_ids,[asset.asset_id]);assert.ok(!request.asset_ids.includes(other.asset_id));assert.deepEqual(assetInventory(h.flow).sets[0].members.map(member=>member.asset_id),[asset.asset_id]);assert.equal(request.design_set_id,null);assert.equal(request.fields.design_set_id,null);
  assert.equal(request.fields.brief_evidence_id,null);assert.equal(h.flow.buffers.visualize.design_set_id,SET);assert.equal(h.flow.status('visualize'),'saved');
});
test('incomplete upload intents never become selectable members',()=>{
  const h=harness();h.add({...h.common(),kind:'upload-intent',upload_id:id('upload',8),asset_id:id('asset',8),session_id:SESSION,name:'incomplete.md',declared_type:'text/markdown',size:7});h.reload();
  assert.equal(h.get('visualize-member-'+id('asset',8)),undefined);assert.match(text(h),/1 retained incomplete/);
});
test('reload permits explicit original-file intent resume without another metadata request',async()=>{
  const h=harness();h.add({...h.common(),kind:'upload-intent',upload_id:id('upload',8),asset_id:id('asset',8),session_id:SESSION,name:'draft.md',declared_type:'text/markdown',size:7});h.reload();
  h.choose([file()]);await h.get('upload-resume-1-'+id('upload',8)).click();assert.equal(writes(h).length,0);
  await h.get('upload-action-1').click();assert.equal(writes(h).length,1);assert.equal(writes(h)[0].method,'PUT');
});
test('current historical set selection submits opaque equal pointers and asset_ids null',async()=>{
  const h=harness(),asset=h.addAsset(),set=h.addSet([asset]);h.reload();assert.equal(eligibleSet(h.flow,set),true);
  await h.get('visualize-set-'+SET).click();await h.get('visualize-accept').click();
  assert.equal(writes(h)[0].payload.design_set_id,SET);assert.equal(writes(h)[0].payload.fields.design_set_id,SET);assert.equal(writes(h)[0].payload.asset_ids,null);
});
test('stale source and changed member snapshots keep history visible but refuse selection',()=>{
  const h=harness(),asset=h.addAsset();h.addSet([asset]);h.state.steps.exploration.accepted_revision=5;h.state.revision=5;h.reload();
  assert.equal(h.get('visualize-set-'+SET).disabled,true);assert.match(text(h),/historical/);
  h.state.steps.exploration.accepted_revision=4;h.state.asset_inventory.records.find(item=>item.record.kind==='design-set').record.members[0].sha256='b'.repeat(64);h.reload();
  assert.equal(h.get('visualize-set-'+SET).disabled,true);assert.equal(eligibleSet(h.flow,assetInventory(h.flow).sets[0]),false);
});
test('design-set projection requires its typed publication session',()=>{
  for(const malformed of [undefined,null,'session_invalid','../private']){
    const h=harness(),asset=h.addAsset();h.addSet([asset]);
    const set=h.state.asset_inventory.records.find(item=>item.record.kind==='design-set').record;
    if(malformed===undefined)delete set.session_id;else set.session_id=malformed;
    h.reload();assert.equal(assetInventory(h.flow),null);assert.equal(h.get('visualize-new-set').disabled,true);
    assert.equal(h.get('visualize-set-'+SET),undefined);assert.equal(h.get('file-input').disabled,true);
  }
});
test('projection omission, malformed blob and retained-link capacity disable uploads without false completion',()=>{
  const h=harness();h.state.asset_inventory_status={available:false,code:'asset_projection_capacity'};h.state.asset_inventory.omitted=10;h.reload();
  assert.equal(h.get('file-input').disabled,true);assert.equal(h.get('visualize-new-set').disabled,true);
  const bad=harness();bad.addAsset();bad.state.asset_inventory.records[0].blob.sha256='b'.repeat(64);bad.reload();assert.equal(assetInventory(bad.flow),null);
  const full=harness();for(let n=0;n<256;n++)full.addAsset(n+'.md');full.reload();assert.equal(full.get('file-input').disabled,true);assert.match(text(full),/capacity is exhausted/);
});
test('set total size and member count limits remain explicit and do not truncate membership',async()=>{
  const h=harness(),assets=Array.from({length:21},(_,index)=>h.addAsset(index+'.md',25*1024*1024));h.reload();await h.get('visualize-new-set').click();
  for(const asset of assets.slice(0,5))h.get('visualize-member-'+asset.asset_id).change({checked:true});assert.equal(h.get('visualize-accept').disabled,true);
  for(const asset of assets.slice(0,5))h.get('visualize-member-'+asset.asset_id).change({checked:false});
  for(const asset of assets)h.get('visualize-member-'+asset.asset_id).change({checked:true});assert.equal(h.get('visualize-accept').disabled,true);assert.equal(writes(h).length,0);
});
test('skip is one click, needs no reason, clears every hidden set pointer and offers no not-applicable road',async()=>{
  const h=harness();h.flow.edit('visualize',{...fields('accepted_set',SET),source:'claude_design'});h.draw();
  assert.equal(h.get('visualize-not-applicable'),undefined);assert.equal(h.get('visualize-reason'),undefined);
  await h.get('visualize-skipped').click();
  assert.equal(writes(h).length,1);assert.equal(writes(h)[0].route,'/api/v1/visual-disposition');
  assert.deepEqual(writes(h)[0].payload.fields,fields('skipped',null,null));assert.equal(h.flow.status('visualize'),'skipped');
});
test('lost set ACK recovers via existing Flow; missing receipt keeps exact request for explicit retry',async()=>{
  const h=harness(),asset=h.addAsset();h.reload();await h.get('visualize-new-set').click();h.get('visualize-member-'+asset.asset_id).change({checked:true});h.faults.writeAck=true;
  await h.get('visualize-accept').click();assert.equal(writes(h).length,1);assert.equal(h.flow.buffers.visualize.design_set_id,SET);assert.equal(h.flow.pending,null);
  const missing=harness(),next=missing.addAsset();missing.reload();await missing.get('visualize-new-set').click();missing.get('visualize-member-'+next.asset_id).change({checked:true});missing.faults.writeBefore=true;
  await missing.get('visualize-accept').click();const request=copy(missing.flow.pending.payload);assert.equal(missing.flow.status('visualize'),'unsaved');assert.equal(writes(missing).length,1);
  missing.faults.writeBefore=false;await missing.flow.retry();assert.deepEqual(writes(missing)[1].payload,request);assert.equal(missing.flow.buffers.visualize.design_set_id,SET);
});
test('stale refusal preserves buffer and no saved check',async()=>{
  const h=harness(),asset=h.addAsset();h.reload();await h.get('visualize-new-set').click();h.get('visualize-member-'+asset.asset_id).change({checked:true});h.faults.stale=true;
  await h.get('visualize-accept').click();assert.equal(h.flow.error.code,'stale_revision');assert.equal(h.flow.status('visualize'),'unsaved');assert.equal(h.flow.buffers.visualize.design_set_id,null);
});
test('busy edit is refused; newer idle answer survives explicit recovery of the original generated pointer',async()=>{
  const h=harness(),asset=h.addAsset();h.reload();await h.get('visualize-new-set').click();h.get('visualize-member-'+asset.asset_id).change({checked:true});
  const newer=fields('skipped',null,'Newer manual answer');const generated={...fields(),source:'claude_design'};
  h.faults.afterWrite=()=>{
    assert.equal(h.flow.busy,true);h.flow.edit('visualize',newer);
    assert.deepEqual(h.flow.buffers.visualize,generated);
  };
  h.faults.writeAck=true;h.faults.read=true;h.faults.reconcile=true;
  await h.get('visualize-accept').click();assert.equal(h.flow.busy,false);assert.equal(h.flow.pending.ambiguous,true);
  assert.equal(writes(h).length,1);const original=copy(h.flow.pending.payload);
  h.flow.edit('visualize',newer);h.draw();assert.equal(h.flow.pending.ambiguous,true);assert.deepEqual(h.flow.pending.payload,original);
  h.faults.read=false;h.faults.reconcile=false;h.faults.writeAck=false;
  await h.flow.retry();assert.equal(writes(h).length,1);assert.deepEqual(h.flow.buffers.visualize,newer);
  assert.equal(h.flow.status('visualize'),'unsaved');assert.equal(h.flow.pending,null);
});
test('orphan diagnostic opaque IDs remain readable without blocking complete inventory',()=>{
  const h=harness();h.state.asset_inventory.orphans={count:140,ids:[id('asset',90),'stage_'+id('upload',91).slice('upload_'.length)+'_'+'9'.repeat(32)]};h.reload();
  assert.ok(assetInventory(h.flow));assert.match(text(h),/140 retained unlinked/);assert.match(text(h),/2 opaque IDs/);assert.equal(h.get('file-input').disabled,false);
});
test('noncanonical prefixed, malformed and path-like orphan IDs refuse the inventory',()=>{
  for(const orphan of ['stage_'+id('upload',91)+'_'+'9'.repeat(32),'stage_'+'9'.repeat(31)+'_'+'9'.repeat(32),
    'stage_'+'9'.repeat(32)+'_'+'9'.repeat(33),'../stage_'+'9'.repeat(32)+'_'+'9'.repeat(32),'assets/staging/private.part']){
    const h=harness();h.state.asset_inventory.orphans={count:1,ids:[orphan]};h.reload();
    assert.equal(assetInventory(h.flow),null);assert.equal(h.get('file-input').disabled,true);
  }
});
test('pause persists only typed decision draft and navigation; reload restores draft, no bytes or acceptance',async()=>{
  const h=harness();h.choose([file()]);await h.get('visualize-new-set').click();assert.equal(h.get('visualize-pause'),undefined);await h.flow.pause();
  assert.equal(h.flow.paused,true);assert.deepEqual(writes(h).map(call=>call.route),['/api/v1/draft','/api/v1/navigate']);assert.equal(h.flow.status('visualize'),'current');
  const reloaded=new Flow(h.api,()=> 'reloaded');reloaded.load(h.state);assert.deepEqual(reloaded.buffers.visualize,{...fields(),source:'claude_design'});
  assert.match(text(h),/not local files/);
});
test('download uses fixed authenticated API and inert Blob; names remain literal; URL revokes after click',async()=>{
  const h=harness(),asset=h.addAsset('../../<script>literal</script>.html',7,'text/html');h.reload();assert.ok(text(h).includes(asset.name));
  await h.get('download-'+asset.asset_id).click();const call=h.calls.find(call=>call.route.includes('/attachments/'));
  assert.equal(call.route,'/api/v1/attachments/'+asset.asset_id);assert.equal(call.headers['X-Idea-Binding'],BINDING);
  assert.equal(h.objectUrls[0].type,'application/octet-stream');assert.equal(h.anchors[0].clicked,true);assert.equal(h.revoked.length,0);
  assert.equal(h.anchors[0].download,safeDownloadName(asset.name,asset.asset_id));assert.ok(!h.anchors[0].download.includes('/'));
  assert.deepEqual(h.deferredDelays,[10000]);h.deferred[0]();assert.deepEqual(h.revoked,['blob:fixture/1']);assert.equal(h.body.all().some(node=>['iframe','img','object'].includes(node.tag)),false);
});
test('download hints are bounded and path/control punctuation never affects literal display metadata',()=>{
  const display='C:\\private\\<script>:design?.html\r\n';const safe=safeDownloadName(display,id('asset',1));
  assert.ok(!/[\u0000-\u001f\u007f/\\:*?"<>|]/.test(safe));assert.equal(safeDownloadName('..',id('asset',1)),id('asset',1));
  assert.equal(Array.from(safeDownloadName('💡'.repeat(400)+'.md',id('asset',1))).length,200);
});
test('reusable upload helper does not require Visualize controls or membership and refuses disconnected writes',()=>{
  const h=harness({uploadsOnly:true});assert.ok(h.get('file-input'));assert.equal(h.get('visualize-accept'),undefined);h.choose([file()]);h.connect(false);
  assert.equal(h.get('file-input').disabled,true);assert.equal(h.get('upload-action-1').disabled,true);h.choose([file()]);assert.equal(writes(h).length,0);
});
test('archived and unknown selected status refuse new uploads; archived attachments remain downloadable',async()=>{
  const h=harness({uploadsOnly:true}),asset=h.addAsset();h.state.idea_status='archived';h.reload();assert.equal(h.get('file-input').disabled,true);
  assert.match(text(h),/archived/);assert.equal(h.get('download-'+asset.asset_id).disabled,false);await h.get('download-'+asset.asset_id).click();assert.equal(h.anchors[0].clicked,true);
  assert.equal(writes(h).length,0);h.state.idea_status=null;h.reload();assert.equal(h.get('file-input').disabled,true);assert.match(text(h),/status is unavailable/);
});
test('archive transition freezes queued writes but permits read-only recovery of an existing upload receipt',async()=>{
  const h=harness();h.faults.metadataAck=true;h.choose([file()]);await h.get('upload-action-1').click();h.state.idea_status='archived';h.reload();
  assert.equal(h.get('upload-action-1').disabled,false);h.faults.metadataAck=false;await h.get('upload-action-1').click();assert.equal(writes(h).length,1);
  assert.equal(h.get('upload-action-1').textContent,'Upload file bytes');assert.equal(h.get('upload-action-1').disabled,true);
});


test('archived full renderer disables all set and disposition controls but preserves downloads',async()=>{
  const h=harness(),asset=h.addAsset();h.addSet([asset]);h.reload();
  await h.get('visualize-new-set').click();h.get('visualize-member-'+asset.asset_id).change({checked:true});
  assert.equal(h.get('visualize-accept').disabled,false);h.state.idea_status='archived';h.reload();
  for(const control of ['visualize-new-set','visualize-member-'+asset.asset_id,'visualize-set-'+SET,'visualize-skipped','visualize-prototype','visualize-accept']){
    assert.equal(h.get(control).disabled,true,control);await h.get(control).click();
  }
  assert.match(text(h),/Design-set and disposition changes are unavailable/);assert.equal(writes(h).length,0);
  assert.equal(h.get('download-'+asset.asset_id).disabled,false);await h.get('download-'+asset.asset_id).click();assert.equal(h.anchors[0].clicked,true);
  
});
test('typed metadata refusals stop the exact request until explicit removal and reselection',async()=>{
  for(const refusal of [{code:'stale_revision',status:409},{code:'too_large',status:413},{code:'invalid_asset_type',status:415},{code:'receipt_capacity_exhausted',status:503},{code:'idea_archived',status:409}]){
    const h=harness();h.faults.metadataRefusal=refusal;const chosen=file();h.choose([chosen]);await h.get('upload-action-1').click();
    const original=copy(writes(h)[0].payload);assert.equal(h.get('upload-action-1').disabled,true);assert.match(text(h),/Remove.*reselect/);
    assert.ok(!text(h).includes(refusal.code));if(refusal.code==='idea_archived')assert.match(text(h),/idea is archived/);await h.get('upload-action-1').click();assert.equal(writes(h).length,1);
    h.faults.metadataRefusal=null;await h.get('upload-remove-1').click();h.state.revision++;h.reload();h.choose([chosen]);await h.get('upload-action-2').click();
    assert.notEqual(writes(h)[1].payload.request_id,original.request_id);assert.equal(writes(h)[1].payload.expected_revision,h.state.revision);assert.equal(writes(h).length,3);
  }
});
test('queued revision drift is an explicit terminal refusal and never rewrites old payload',async()=>{
  const h=harness();h.choose([file()]);h.state.revision++;h.reload();await h.get('upload-action-1').click();
  assert.equal(writes(h)[0].payload.expected_revision,4);assert.equal(h.get('upload-action-1').disabled,true);assert.match(text(h),/saved idea changed/);
  await h.get('upload-action-1').click();assert.equal(writes(h).length,1);assert.equal(writes(h)[0].payload.expected_revision,4);
});
test('typed byte refusals stop retries while preserving local selection and saved intent',async()=>{
  for(const refusal of [{code:'too_large',status:413},{code:'invalid_asset_type',status:415}]){
    const h=harness();h.faults.bytesRefusal=refusal;h.choose([file()]);await h.get('upload-action-1').click();
    assert.equal(writes(h).length,2);assert.equal(h.get('upload-action-1').disabled,true);assert.equal(assetInventory(h.flow).intents.length,1);
    assert.equal(assetInventory(h.flow).assets.length,0);assert.match(text(h),/Nothing was applied/);await h.get('upload-action-1').click();assert.equal(writes(h).length,2);
  }
});
test('typed download refusal explains recovery without exposing raw codes',async()=>{
  const h=harness(),asset=h.addAsset();h.reload();h.faults.downloadRefusal={code:'browser_unauthorized',status:401};await h.get('download-'+asset.asset_id).click();
  assert.match(text(h),/pairing again/);assert.ok(!text(h).includes('browser_unauthorized'));assert.equal(h.anchors.length,0);assert.equal(h.flow.busy,false);
});
test('completed queue rows remain bounded and name the rows to remove',async()=>{
  const h=harness();h.choose(Array.from({length:20},(_,i)=>file('completed-'+i+'.md')));
  for(let n=1;n<=20;n++)await h.get('upload-action-'+n).click();
  h.choose([file('next.md')]);assert.equal(h.get('upload-action-21'),undefined);assert.match(text(h),/Completed rows to remove: completed-0.md/);
  assert.match(text(h),/20-file or 100 MiB limits/);await h.get('upload-remove-1').click();h.choose([file('next.md')]);assert.ok(h.get('upload-action-21'));
});
test('only explicit matching MIME aliases become canonical declared types',async()=>{
  for(const [filename,alias,canonical]of [['bundle.zip','application/x-zip-compressed','application/zip'],['notes.md','text/x-markdown','text/markdown']]){
    const h=harness();h.choose([file(filename,'fixture',alias)]);await h.get('upload-action-1').click();assert.equal(writes(h)[0].payload.declared_type,canonical);
  }
  for(const [filename,alias]of [['notes.md','application/x-zip-compressed'],['bundle.zip','text/x-markdown'],['notes.md','application/unknown']]){
    const h=harness();h.choose([file(filename,'fixture',alias)]);assert.equal(h.get('upload-action-1'),undefined);assert.equal(writes(h).length,0);
  }
});
test('projection capacity gives a typed non-reload road; other absence remains explicit',()=>{
  const h=harness();h.state.asset_inventory_status={available:false,code:'asset_projection_capacity'};h.reload();
  assert.match(text(h),/reloading cannot reduce retained history/);assert.equal(h.get('file-input').disabled,true);
  h.state.asset_inventory_status={available:false,code:'not_ready'};h.reload();assert.match(text(h),/not ready/);
  h.state.asset_inventory_status={available:false,code:'no_selection'};h.reload();assert.match(text(h),/Save or select an idea/);
});
test('changed request session cannot resume a saved original-file intent',()=>{
  const h=harness();h.add({...h.common(),kind:'upload-intent',upload_id:id('upload',8),asset_id:id('asset',8),session_id:SESSION,name:'draft.md',declared_type:'text/markdown',size:7});
  h.state.session_id='session_'+'9'.repeat(32);h.reload();h.choose([file()]);
  assert.equal(h.get('upload-resume-1-'+id('upload',8)),undefined);assert.match(text(h),/different session cannot resume/);assert.equal(writes(h).length,0);
});
test('local brief copying remains available offline and paused without writes',async()=>{
  const copied=[],h=harness({clipboard:{writeText:async value=>copied.push(value)}});h.connect(false);h.flow.paused=true;h.draw();
  assert.equal(h.get('visualize-copy').disabled,false);await h.get('visualize-copy').click();assert.equal(copied[0],h.get('visualize-brief').value);assert.equal(writes(h).length,0);
});

test('byte receipt followed by read loss retains check and verifies completion without another write',async()=>{
  const h=harness();h.choose([file()]);h.faults.bytesAck=false;
  // Lose only the state read after the byte request, using the actual HTTP fixture.
  h.faults.bytesReceipt=value=>{h.faults.read=true;return value;};
  await h.get('upload-action-1').click();assert.equal(writes(h).length,2);assert.equal(h.get('upload-action-1').textContent,'Check upload result');
  h.faults.read=false;await h.get('upload-action-1').click();assert.equal(writes(h).length,2);assert.equal(h.get('upload-action-1'),undefined);
});

test('completed queue bytes count toward the 100 MiB cap until explicit local row removal',async()=>{
  const h=harness(),selected=Array.from({length:4},(_,i)=>{const blob=file('large-completed-'+i+'.md');Object.defineProperty(blob,'size',{value:25*1024*1024});return blob;});
  h.choose(selected);for(let n=1;n<=4;n++)await h.get('upload-action-'+n).click();h.choose([file('next.md')]);
  assert.equal(h.get('upload-action-5'),undefined);assert.match(text(h),/Completed rows to remove: large-completed-0.md/);
  await h.get('upload-remove-1').click();h.choose([file('next.md')]);assert.ok(h.get('upload-action-5'));
});

test('design-set source witness is capture + discovery + exploration only',()=>{
  const h=harness(),asset=h.addAsset(),set=h.addSet([asset]);h.reload();assert.ok(assetInventory(h.flow));
  const rec=h.state.asset_inventory.records.find(item=>item.record.kind==='design-set').record;
  rec.source={capture:rec.source.capture,shape:{revision:4,digest:HEX}};h.reload();assert.equal(assetInventory(h.flow),null);
  rec.source={capture:{revision:4,digest:HEX},discovery:{revision:4,digest:HEX}};h.reload();assert.equal(assetInventory(h.flow),null);
});
test('disabled Visualize accept says what is missing and enabled accept says nothing',()=>{
  const h=harness();assert.equal(h.get('visualize-accept').disabled,true);
  const why=h.get('visualize-accept-reason');assert.ok(why);assert.match(why.className,/accept-reason/);
  assert.equal(h.get('visualize-accept').title,why.textContent);assert.equal(h.get('visualize-accept')['aria-describedby'],'visualize-accept-reason');
  const skip=harness();skip.flow.buffers.visualize=fields('skipped',null,'Not needed');skip.reload();
  assert.equal(skip.get('visualize-accept').disabled,false);assert.equal(skip.get('visualize-accept-reason'),undefined);assert.ok(!skip.get('visualize-accept').title);
});

test('every disabled Visualize accept states a non-empty reason',async()=>{
  const reasoned=h=>{assert.equal(h.get('visualize-accept').disabled,true);const r=h.get('visualize-accept-reason');assert.ok(r&&r.textContent.trim().length>0);assert.equal(h.get('visualize-accept').title,r.textContent);};
  reasoned(harness());
  const create=harness();create.addAsset();create.reload();await create.get('visualize-new-set').click();reasoned(create);
  const nobrief=harness();nobrief.state.steps.exploration.status='review-needed';nobrief.flow.buffers.visualize=fields();nobrief.reload();reasoned(nobrief);
  const stale=harness(),a=stale.addAsset();stale.addSet([a]);stale.state.steps.exploration.accepted_revision=5;stale.state.revision=5;stale.reload();
  stale.flow.buffers.visualize=fields('accepted_set',SET);stale.reload();reasoned(stale);
  const archived=harness();archived.state.idea_status='archived';archived.flow.buffers.visualize=fields('skipped',null,'ok');archived.reload();reasoned(archived);
  const busy=harness();busy.flow.buffers.visualize=fields('skipped',null,'ok');busy.flow.busy=true;busy.reload();reasoned(busy);
  const paused=harness();paused.flow.buffers.visualize=fields('skipped',null,'ok');paused.flow.paused=true;paused.reload();reasoned(paused);
  const pending=harness();pending.flow.buffers.visualize=fields('skipped',null,'ok');pending.flow.pending={};pending.reload();reasoned(pending);
  const noinv=harness();noinv.state.asset_inventory_status={available:false,code:'not_ready'};noinv.flow.buffers.visualize=fields();noinv.reload();reasoned(noinv);
});

// --- v0.3 three-road choice cards ---
const connectAgent=h=>{
  Object.assign(h.state,{agent_status:'connected',agent_generation:'agent_'+'6'.repeat(32),proposals:[],
    proposal_inventory:{total:0,projected:0,omitted:0,content_omitted:0,index_path:IDEA+'.md'}});
  Object.defineProperty(h.state,'proposal_sources',{enumerable:true,get:()=>({visual_brief:{available:true,code:'ok',source:{accepted_revision:4,draft_version:h.state.draft_version,data:{},source_digest:HEX}}})});
  h.sent=[];
  const original=h.api.write.bind(h.api);
  h.api.write=async(operation,payload)=>{if(operation!=='propose')return original(operation,payload);h.sent.push({operation,payload});return {ok:true,code:'ok',request_id:payload.request_id,operation:payload.operation,session_id:SESSION,idea_id:IDEA,
    accepted_revision:4,draft_version:0,source_digest:HEX,status:'pending',write_state:'not_applied'};};
  h.reload();return h;
};
const answer=(h,skill)=>{h.state.proposals=[{proposal_id:'proposal_'+'7'.repeat(32),request_id:h.sent.at(-1).payload.request_id,operation:'visual_brief',accepted_revision:4,draft_version:0,
  source_digest:HEX,proposal:{prototype_skill:skill},stale:false,stale_reason:null,acceptance_eligible:true,acceptance_reason:null,
  evidence:{path:'history/'+IDEA+'/metadata/'+HEX+'.md',sha256:HEX},content_omitted:false}];h.state.proposal_inventory.total=h.state.proposal_inventory.projected=1;h.reload();};
const pngZip=h=>{const zip=h.addAsset('prototype.zip',9,'application/zip'),png=h.addAsset('prototype.png',12,'image/png');return {zip,png};};
const fillPrototype=(h,files)=>{h.state.conversation={request_id:h.sent.at(-1).payload.request_id,operation:'visual_brief',idea_id:IDEA,accepted_revision:4,
  fills:[{sequence:1,fields:{source:'prototype',assets:[files.zip.asset_id,files.png.asset_id]}}]};h.reload();};
const BUILDING='Your terminal is building the prototype — it opens in a second tab';
const MISSED='The prototype did not reach this page. Ask your terminal to send it again, or choose another road.';
const reasonOf=h=>{assert.equal(h.get('visualize-accept').disabled,true);const r=h.get('visualize-accept-reason');assert.ok(r&&r.textContent.trim().length>0);assert.equal(h.get('visualize-accept').title,r.textContent);};

test('three equal choice cards carry the owner labels verbatim',()=>{
  const h=harness(),labels=h.body.all().filter(node=>node.tag==='h2').map(node=>node.textContent);
  for(const label of ['Visualize in Claude Design and import it back','Prototype Here','Skip visualization'])assert.ok(labels.includes(label),label);
  assert.equal(h.get('visualize-prototype').textContent,'Prototype Here');assert.equal(h.get('visualize-skipped').textContent,'Skip visualization');
  assert.ok(h.get('visualize-card-claude')&&h.get('visualize-card-prototype')&&h.get('visualize-card-skip'));
});
test('Claude Design road records source claude_design on accept',async()=>{
  const h=harness(),asset=h.addAsset('design.md');h.reload();await h.get('visualize-new-set').click();h.get('visualize-member-'+asset.asset_id).change({checked:true});
  await h.get('visualize-accept').click();assert.equal(writes(h)[0].payload.fields.source,'claude_design');assert.equal(h.flow.status('visualize'),'saved');
});
test('Prototype Here without a terminal says the terminal is needed and the other roads still work',async()=>{
  const h=harness();assert.equal(h.get('visualize-prototype').disabled,true);
  assert.match(h.get('visualize-prototype-needs-terminal').textContent,/needs your terminal/);
  assert.equal(h.get('visualize-skipped').disabled,false);assert.equal(h.get('visualize-new-set').disabled,false);
});
test('Prototype Here sends visual_brief; unavailable skill shows the exact pointer and a safe link',async()=>{
  const h=connectAgent(harness());assert.equal(h.get('visualize-prototype').disabled,false);
  await h.get('visualize-prototype').click();assert.equal(h.sent.length,1);assert.equal(h.sent[0].operation,'propose');assert.equal(h.sent[0].payload.operation,'visual_brief');
  answer(h,'unavailable');const p=h.get('visualize-prototype-skill');assert.ok(p);
  assert.equal(p.textContent+p.children.map(node=>node.textContent).join(''),"Prototype Here uses Matt Pocock's prototype skill. Get it from https://github.com/mattpocock/skills, install it, then refresh this page.");
  const link=p.children[0];assert.equal(link.tag,'a');assert.equal(link.href,'https://github.com/mattpocock/skills');assert.equal(link.rel,'noopener noreferrer');assert.equal(link.target,'_blank');
  assert.equal(h.get('visualize-prototype-status'),undefined);reasonOf(h);
});
test('Prototype available shows the status line; the terminal fill shows the screenshot and enables Accept as prototype',async()=>{
  const h=connectAgent(harness()),files=pngZip(h);h.reload();
  await h.get('visualize-prototype').click();reasonOf(h);
  assert.equal(h.get('visualize-prototype-status').textContent,BUILDING);
  assert.equal(h.get('visualize-prototype-skill'),undefined);reasonOf(h);
  fillPrototype(h,files);await new Promise(resolve=>setTimeout(resolve,20));h.draw();
  h.state.conversation=null;answer(h,'available');await new Promise(resolve=>setTimeout(resolve,20));h.draw();
  assert.deepEqual(h.flow.buffers.visualize.assets,[files.zip.asset_id,files.png.asset_id]);
  const shot=h.get('visualize-prototype-shot');assert.ok(shot);assert.match(shot.src,/^blob:fixture\//);assert.equal(h.get('visualize-prototype-status'),undefined);
  assert.equal(h.get('visualize-accept').disabled,false);assert.equal(h.get('visualize-accept-reason'),undefined);
  await h.get('visualize-accept').click();
  const request=writes(h).find(call=>call.route==='/api/v1/visual-set/accept').payload;
  assert.equal(request.fields.source,'prototype');assert.equal(request.fields.disposition,'accepted_set');assert.deepEqual(request.asset_ids,[files.zip.asset_id,files.png.asset_id]);
});
test('Prototype fill applied while the request is open stays applied after the available reply and is never auto-accepted',async()=>{
  const h=connectAgent(harness()),files=pngZip(h);h.reload();
  await h.get('visualize-prototype').click();
  assert.equal(h.get('visualize-prototype-status').textContent,'Your terminal is building the prototype — it opens in a second tab');
  assert.equal(h.get('visualize-prototype-skill'),undefined);
  fillPrototype(h,files);await new Promise(resolve=>setTimeout(resolve,20));h.draw();
  assert.ok(h.get('visualize-prototype-shot'));
  assert.deepEqual(h.flow.buffers.visualize.assets,[files.zip.asset_id,files.png.asset_id]);
  h.state.conversation=null;answer(h,'available');await new Promise(resolve=>setTimeout(resolve,20));h.draw();
  assert.deepEqual(h.flow.buffers.visualize.assets,[files.zip.asset_id,files.png.asset_id]);
  assert.equal(h.get('visualize-accept').disabled,false);assert.equal(writes(h).some(call=>call.route==='/api/v1/visual-set/accept'),false);
  await h.get('visualize-accept').click();
  assert.deepEqual(writes(h).find(call=>call.route==='/api/v1/visual-set/accept').payload.asset_ids,[files.zip.asset_id,files.png.asset_id]);
});
test('A reply of available with no fill says the prototype did not reach the page, never building',async()=>{
  const h=connectAgent(harness());await h.get('visualize-prototype').click();h.state.conversation=null;answer(h,'available');
  assert.equal(h.get('visualize-prototype-status').textContent,MISSED);assert.doesNotMatch(h.get('visualize-prototype-status').textContent,/building/);
  const r=reasonOf(h)??h.get('visualize-accept-reason');assert.match(r.textContent,/did not reach this page/);
});
test('While the request is open the footer and the status line agree that the terminal is building',async()=>{
  const h=connectAgent(harness());await h.get('visualize-prototype').click();
  assert.equal(h.get('visualize-prototype-status').textContent,BUILDING);
  assert.match(h.get('visualize-accept-reason').textContent,/Your terminal is still building the prototype\./);
  const t=connectAgent(harness());await t.get('visualize-prototype').click();t.state.conversation={request_id:t.sent.at(-1).payload.request_id,operation:'visual_brief',idea_id:IDEA,accepted_revision:4,fills:[]};t.reload();
  assert.equal(t.get('visualize-prototype-status').textContent,BUILDING);assert.match(t.get('visualize-accept-reason').textContent,/still building/);
});
test('Accept is blocked on an accepted set with no source, and a draft without one still saves',async()=>{
  const h=harness(),asset=h.addAsset('design.md');h.flow.buffers.visualize=fields('accepted_set',null);h.flow.buffers.visualize.assets=[asset.asset_id];h.reload();
  assert.equal(h.get('visualize-accept').disabled,true);assert.match(h.get('visualize-accept-reason').textContent,/Choose how the design was made: Claude Design or Prototype Here\./);
  assert.equal((await import(foldsUrl)).visualizeFields(fields('accepted_set',null),true),true);
});
test('Prototype with only part of a set filled keeps Accept disabled with a reason',async()=>{
  const h=connectAgent(harness()),files=pngZip(h);h.reload();await h.get('visualize-prototype').click();answer(h,'available');
  h.state.conversation={request_id:h.sent.at(-1).payload.request_id,operation:'visual_brief',idea_id:IDEA,accepted_revision:4,fills:[{sequence:1,fields:{source:'prototype',assets:[files.zip.asset_id]}}]};
  h.reload();reasonOf(h);
});
test('Skip is one click with no reason field, and records skipped',async()=>{
  const h=harness();await h.get('visualize-skipped').click();
  assert.equal(writes(h).length,1);assert.equal(writes(h)[0].route,'/api/v1/visual-disposition');assert.equal(writes(h)[0].payload.fields.disposition,'skipped');assert.equal(writes(h)[0].payload.fields.reason,null);
});
test('visualize.js has no not-applicable road',async()=>{
  assert.ok(!(await readFile(new URL('steps/visualize.js',web),'utf8')).includes('not-applicable'));
  assert.equal((await import(foldsUrl)).validVisualize({disposition:'not-applicable',reason:'x',design_set_id:null,brief_evidence_id:null}),false);
});

test('not-applicable is not a step status: no label, no glyph, no progress credit, and the source names it nowhere',async()=>{
  const folds=await import(foldsUrl);
  assert.equal(folds.statusLabel('not-applicable'),'Unavailable');
  assert.equal(folds.statusGlyph('not-applicable'),'?');
  assert.ok(!(await readFile(new URL('folds.js',web),'utf8')).includes('not-applicable'));
  assert.ok(!(await readFile(new URL('steps/review.js',web),'utf8')).includes('not-applicable'));
});
