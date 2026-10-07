// Explicit uploads and immutable membership; Flow owns save/recovery.
import {METHOD_LABELS as METHOD_NAMES, validVisualize, acceptBlockers} from '../folds.js';

const MAX_FILE = 25 * 1024 * 1024, MAX_SET = 100 * 1024 * 1024;
const ID = {idea: /^idea_[0-9a-f]{32}$/, upload: /^upload_[0-9a-f]{32}$/, asset: /^asset_[0-9a-f]{32}$/, set: /^set_[0-9a-f]{32}$/};
const HASH = /^[0-9a-f]{64}$/;
const TYPES = {'image/png':['png'], 'image/jpeg':['jpg','jpeg'], 'image/webp':['webp'],
  'application/pdf':['pdf'], 'image/svg+xml':['svg'], 'text/html':['html','htm'], 'text/css':['css'],
  'application/json':['json'], 'text/markdown':['md','markdown'], 'text/plain':['txt','text','md','markdown'], 'application/zip':['zip']};
const ALIASES = {'application/x-zip-compressed':'application/zip','text/x-markdown':'text/markdown'};
const INFER = Object.fromEntries(Object.entries(TYPES).flatMap(([type,extensions]) => extensions.map(extension => [extension,type])));
INFER.md = INFER.markdown = 'text/markdown';
// Mirrors the server's design-set source witness (idea_asset_evidence._source).
const SOURCE_KEYS = ['capture','discovery','exploration'];
const copy = value => structuredClone(value);
const exact = (value,keys) => value && typeof value === 'object' && !Array.isArray(value) &&
  Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value,key));
const typed = (value,kind) => typeof value === 'string' && ID[kind].test(value);
const hash = value => typeof value === 'string' && HASH.test(value);
const size = value => Number.isSafeInteger(value) && value >= 1 && value <= MAX_FILE;
function name(value,limit=4096) {
  if (typeof value !== 'string' || !value.trim()) return false;
  let count = 0;
  for (const scalar of value) if (++count > limit || (scalar.codePointAt(0) >= 0xd800 && scalar.codePointAt(0) <= 0xdfff)) return false;
  return true;
}
function fileType(file) {
  const extension = file.name?.split('.').at(-1)?.toLowerCase();
  const type = ALIASES[file.type] || file.type || INFER[extension];
  return file.name?.includes('.') && Object.hasOwn(TYPES,type) && TYPES[type].includes(extension) ? type : null;
}
function fileRecord(record) {
  return typed(record.asset_id,'asset') && typed(record.upload_id,'upload') && name(record.name) && record.name.includes('.') && size(record.size) &&
    Object.hasOwn(TYPES,record.declared_type) && TYPES[record.declared_type].includes(record.name.split('.').at(-1).toLowerCase());
}

// Authority is the authenticated, Store-verified projection. This structural
// guard refuses incomplete/omitted data; it does not claim browser hash proof.
export function assetInventory(flow) {
  const value = flow.state?.asset_inventory;
  if (flow.state?.asset_inventory_status?.available !== true || flow.state.asset_inventory_status.code !== 'ok' ||
      !exact(value,['records','total','projected','omitted','orphans']) || !Array.isArray(value.records) || value.records.length > 256 ||
      value.total !== value.records.length || value.projected !== value.total || value.omitted !== 0 ||
      !exact(value.orphans,['count','ids']) || !Number.isSafeInteger(value.orphans.count) || value.orphans.count < 0 ||
      !Array.isArray(value.orphans.ids) || value.orphans.ids.length > 128 || value.orphans.ids.length > value.orphans.count ||
      !value.orphans.ids.every(id => typed(id,'asset') || (typeof id === 'string' && /^stage_[0-9a-f]{32}_[0-9a-f]{32}$/.test(id)))) return null;
  const assets = [], sets = [], intents = [], ids = new Set();
  for (const entry of value.records) {
    if (!exact(entry,['record','evidence','blob'])) return null;
    const r = entry.record, kind = r?.kind;
    const id = kind === 'design-set' ? r.set_id : kind === 'asset' ? r.asset_id : r?.upload_id;
    const common=['schema_version','kind','idea_id','source_revision','actor','timestamp'];
    const keys=kind==='design-set'?['set_id','session_id','source','source_digest','members']:kind==='asset'?
      ['upload_id','asset_id','session_id','blob_path','name','declared_type','validated_type','size','sha256']:
      ['upload_id','asset_id','session_id','name','declared_type','size'];
    if (!exact(r,[...common,...keys]) || r.schema_version !== 1 || r.idea_id !== flow.state.idea_id || !name(r.actor,200) || !name(r.timestamp,100) ||
        typeof r.session_id!=='string'||!/^session_[0-9a-f]{32}$/.test(r.session_id) || !Number.isSafeInteger(r.source_revision) ||
        r.source_revision < 1 || r.source_revision > flow.state.revision || ids.has(id) ||
        !exact(entry.evidence,['record_id','path','sha256']) || entry.evidence.record_id !== id ||
        typeof entry.evidence.path !== 'string' || !/^assets\/evidence\/[0-9a-f]{64}\.md$/.test(entry.evidence.path) || !hash(entry.evidence.sha256)) return null;
    ids.add(id);
    if (kind === 'asset') {
      if (!fileRecord(r) || r.validated_type !== r.declared_type || !hash(r.sha256) || r.blob_path !== 'assets/blobs/'+r.asset_id+'.bin' ||
          !exact(entry.blob,['path','size','sha256']) || entry.blob.path !== r.blob_path || entry.blob.size !== r.size || entry.blob.sha256 !== r.sha256) return null;
      assets.push(r);
    } else if (kind === 'upload-intent') {
      if (!fileRecord(r) || entry.blob !== null) return null;
      intents.push(r);
    } else if (kind === 'design-set') {
      if (!typed(r.set_id,'set') || entry.blob !== null || !hash(r.source_digest) || !exact(r.source,SOURCE_KEYS) ||
          !SOURCE_KEYS.every(key => exact(r.source[key],['revision','digest']) && Number.isSafeInteger(r.source[key].revision) &&
            r.source[key].revision >= 1 && r.source[key].revision <= r.source_revision && hash(r.source[key].digest)) ||
          !Array.isArray(r.members) || r.members.length < 1 || r.members.length > 20 ||
          new Set(r.members.map(member => member?.asset_id)).size !== r.members.length ||
          !r.members.every(member => exact(member,['asset_id','name','type','size','sha256']) && typed(member.asset_id,'asset') &&
            name(member.name) && member.name.includes('.') && size(member.size) && hash(member.sha256) && Object.hasOwn(TYPES,member.type) && TYPES[member.type].includes(member.name.split('.').at(-1).toLowerCase())) ||
          r.members.reduce((total,member) => total+member.size,0) > MAX_SET) return null;
      sets.push(r);
    } else return null;
  }
  return {assets,sets,intents,orphans:value.orphans,total:value.total};
}
export function eligibleSet(flow,set,inventory = assetInventory(flow)) {
  return Boolean(inventory && set?.source && Array.isArray(set.members) && SOURCE_KEYS.every(key => flow.status(key) === 'saved' &&
    set.source[key].revision === flow.state.steps[key].accepted_revision) && set.members.every(member => {
      const asset = inventory.assets.find(value => value.asset_id === member.asset_id);
      return asset && member.name === asset.name && member.type === asset.validated_type && member.size === asset.size && member.sha256 === asset.sha256;
    }));
}
export function visualBrief(flow) {
  if (!SOURCE_KEYS.every(key => flow.status(key) === 'saved' && flow.state.accepted?.[key])) return null;
  const discovery=flow.state.accepted.discovery,exploration=flow.state.accepted.exploration,method=flow.state.accepted.method;
  const row=([label,value])=>label+': '+value;
  const found=[['Problem',discovery.problem],['Who',discovery.audience]].map(row);
  const lines=[['Desired result',exploration.outcome],['Scope',exploration.scope],['Scope reason',exploration.scope_reason],['Next slice',exploration.next_slice]].map(row);
  for(const item of exploration.alternatives??[])lines.push('Alternative: '+item.route+' — '+item.reason);
  for(const value of exploration.assumptions??[])lines.push('Assumption: '+value);
  for(const value of exploration.learning??[])lines.push('Learning: '+value);
  const sketch=(exploration.sketch??[]).map((item,index)=>(index+1)+'. '+item.title+' — done when: '+item.done_when);
  const methodLines=[];
  if(flow.status('method')==='saved'&&method){
    methodLines.push('Selection: '+(METHOD_NAMES[method.selection]??method.selection));
    if(typeof method.reason==='string'&&method.reason.trim())methodLines.push('Reason: '+method.reason);
    if(exploration.investment)methodLines.push('Investment cap: '+exploration.investment.cap+' '+exploration.investment.unit,'Boundary: '+exploration.investment.boundary);
    if(exploration.experiment)for(const [key,label]of [['question','Question'],['evidence','Evidence'],['success_criterion','Success criterion'],['stop_rule','Stop rule']])methodLines.push(label+': '+exploration.experiment[key]);
  }
  return 'Visual design brief — derived from current accepted answers\n\nCapture\n'+flow.state.accepted.capture.raw_text+
    '\n\nDiscovery\n'+found.join('\n')+'\n\nExploration\n'+lines.join('\n')+(sketch.length?'\n\nSketch\n'+sketch.join('\n'):'')+
    (methodLines.length?'\n\nMethod\n'+methodLines.join('\n'):'')+
    '\n\nCreate locally or with your chosen tool. Upload files here, select membership, then accept explicitly.';
}

// Buttons sit in a row of their own so a card never stretches them full width.
const actions = (element, ...buttons) => { const row = element('div', '', 'setup-actions'); row.append(...buttons); return row; };

const sessions = new WeakMap();
function session(flow) {
  let value = sessions.get(flow);
  if (!value || value.idea !== flow.state?.idea_id) {
    value = {idea:flow.state?.idea_id,queue:[],next:0,error:null,copyMessage:'',members:[]}; sessions.set(flow,value);
  }
  return value;
}
const blocked = ctx => !ctx.connected || ctx.flow.busy || ctx.flow.pending || ctx.flow.paused;
const writeBlocked = ctx => blocked(ctx) || ctx.flow.state?.idea_status !== 'active';
function failureMessage(code,download=false) {
  const messages = {
    stale_revision:'The saved idea changed. Remove this local selection and reselect the file using current state.',
    receipt_capacity_exhausted:'This request session has no capacity for another receipt. Pair a fresh session, then remove and reselect the file.',
    asset_capacity_exhausted:'Retained asset capacity is exhausted. Existing history remains available; another upload cannot be added.',
    too_large:'The file or retained storage exceeds its size or capacity limit.',
    invalid_asset_type:'The file extension, declared type or content signature does not match a supported format.',
    invalid_upload:'This file selection is invalid. Choose a supported file within the limits.',
    request_conflict:'This upload identity conflicts with saved evidence. Remove and reselect the file.',
    idea_archived:'This idea is archived. Existing files remain readable; new uploads are unavailable.',
    browser_unauthorized:'This browser needs pairing again before accessing files.',
    asset_not_found:'This saved file is unavailable. Reload current state before downloading.',
    not_found:'This saved file is unavailable. Reload current state before downloading.',
    corrupt_store:'Saved file integrity could not be verified. Stop and have the Store checked.',
    durability_uncertain:'The upload may have committed. Check its result before retrying.',
    connection_lost:download?'The connection was lost. Download again when connected.':'The connection was lost. Check the upload result before retrying.',
    invalid_response:download?'The downloaded file response could not be verified. Reload current state before downloading again.':'The response could not be verified. Check the upload result before retrying.',
  };
  return messages[code] ?? (download?'The file could not be downloaded. Check the connection and current saved state.':'The upload could not be completed. Your local file selection is kept.');
}
function inventoryMessage(flow) {
  const code=flow.state?.asset_inventory_status?.code;
  if(code==='asset_projection_capacity')return 'Verified asset inventory exceeds projection capacity. Upload and set selection are unavailable; reloading cannot reduce retained history. Existing history is preserved.';
  if(code==='no_selection')return 'Save or select an idea before uploading files or selecting a design set.';
  if(code==='not_ready')return 'Verified asset inventory is not ready. Reload current state before uploading or selecting a set.';
  return 'Verified asset inventory is unavailable or invalid. Reload current state; upload and set selection are unavailable.';
}
function receipt(value,task,bytes) {
  return value?.ok === true && value.code === 'ok' && ['applied','no_op'].includes(value.write_state) &&
    value.idea_id === task.payload.idea_id && value.request_id === (bytes ? 'upload-bytes:'+task.upload_id : task.payload.request_id) &&
    typed(value.upload_id,'upload') && typed(value.asset_id,'asset') &&
    (!bytes || (value.upload_id === task.upload_id && value.asset_id === task.asset_id)) &&
    (bytes ? value.completion_request_id == null || value.completion_request_id === 'upload-bytes:'+value.upload_id :
      value.completion_request_id === 'upload-bytes:'+value.upload_id);
}
function invalid() { return Object.assign(new Error('invalid_response'),{code:'invalid_response',uncertain:true}); }
async function uploadAction(ctx,task,action) {
  if (blocked(ctx) || task.phase === 'refused') return false;
  const {flow} = ctx, local = session(flow), idea = flow.state.idea_id;
  if (!action.startsWith('check-') && flow.state.idea_status !== 'active') {
    task.error='New uploads require an active idea. Existing upload results and files remain readable.';flow.onChange();return false;
  }
  flow.busy = true; flow.mutationEpoch++; task.error = null; flow.onChange();
  try {
    const bytes = action === 'bytes' || action === 'check-bytes';
    let result, state;
    if (action.startsWith('check-')) {
      const resolved = bytes ? await flow.api.reconcileUploadBytes(task.upload_id,idea) : await flow.api.reconcileUpload(task.payload.request_id,idea);
      result = resolved.result; state = resolved.state;
      if (!state || state.idea_id !== idea) throw invalid();
      flow.load(state,true);
      if (result === null) { task.phase = bytes ? 'bytes' : 'metadata'; task.error = 'No receipt found. Retry explicitly with the same file/request.'; return false; }
    } else {
      result = bytes ? await flow.api.uploadBytes(task.upload_id,task.file) : await flow.api.uploadMetadata(task.payload);
    }
    if (!receipt(result,task,bytes)) throw invalid();
    if (!bytes) {
      task.upload_id = result.upload_id; task.asset_id = result.asset_id; task.phase = 'check-metadata';
      if (!state) state = await flow.api.state(idea);
      if (state?.idea_id !== idea) throw invalid();
      flow.load(state,true);
      const intent = assetInventory(flow)?.intents.find(value => value.upload_id === task.upload_id && value.asset_id === task.asset_id &&
        value.session_id === flow.state.session_id && value.name === task.payload.name && value.size === task.payload.size &&
        value.declared_type === task.payload.declared_type);
      if (!intent) {task.error = 'Receipt exists; verified upload intent is unavailable. Check/reload before sending bytes.';return false;}
      task.phase = 'bytes';
    } else {
      // Byte receipt alone never marks completion: current verified inventory
      // must include its exact immutable completion before the UI says so.
      task.phase = 'check-bytes';
      if (!state) state = await flow.api.state(idea);
      if (state?.idea_id !== idea) throw invalid();
      flow.load(state,true);
      const asset = assetInventory(flow)?.assets.find(value => value.asset_id === task.asset_id && value.upload_id === task.upload_id &&
        value.name === task.file.name && value.size === task.file.size && value.validated_type === task.type);
      if (!asset) { task.error = 'Receipt exists; complete asset is unavailable in this projection. Reload/check before continuing.'; return false; }
      task.phase = 'complete';
    }
    return true;
  } catch (error) {
    task.error = failureMessage(error.code);
    // A verified receipt followed by a failed state read remains a check,
    // even though that read itself carries no uncertain-write flag.
    if (error.uncertain || task.phase.startsWith('check-') || action.startsWith('check-'))
      task.phase = action.includes('bytes') ? 'check-bytes' : 'check-metadata';
    else if (error.data?.write_state === 'not_applied') {
      task.phase = 'refused';
      task.error += ' Nothing was applied. Remove this local selection and reselect explicitly; this request will not be retried.';
    } else {task.phase='refused';task.error += ' Remove and reselect the file before another upload.';}
    return false;
  } finally {
    flow.busy = false;
    if (local.idea === idea) flow.onChange();
  }
}

// Shared by Visualize and saved Capture. File bytes live only in this tab;
// immutable intents/completions, rather than local queue labels, survive reload.
export function renderUploads(ctx) {
  const {body,flow,element,button,handle} = ctx, local = session(flow), inventory = assetInventory(flow);
  const group = element('section'); group.setAttribute('aria-label','Local file uploads');
  group.append(element('p','Choose up to 20 files, 25 MiB each and 100 MiB total. Files stay separate from accepted design-set membership.'));
  group.append(element('p','Unfinished file selections live only in this tab. After reload, reselect the original file to resume a saved intent in the same request session. A different session cannot resume that intent.'));
  const label = element('label','Choose local files'); label.htmlFor = 'file-input';
  const input = element('input'); input.id = 'file-input'; input.type = 'file'; input.multiple = true;
  input.accept = Object.keys(INFER).map(extension => '.'+extension).join(',');
  input.disabled = Boolean(blocked(ctx) || flow.state?.idea_status !== 'active' || !typed(flow.state?.idea_id,'idea') || !inventory || inventory.total >= 256 || flow.state?.capabilities?.uploads !== true);
  input.addEventListener('change',event => {
    if (input.disabled) return;
    const files = Array.from(event.target.files ?? []);
    const active = local.queue;
    if (!files.length) return;
    if (files.length+active.length > 20 || files.reduce((total,file) => total+file.size,active.reduce((total,task) => total+task.file.size,0)) > MAX_SET) {
      const completed=active.filter(task=>task.phase==='complete').map(task=>task.file.name);
      local.error='The local queue exceeds the 20-file or 100 MiB limits. Remove local rows before choosing more files.'+
        (completed.length?' Completed rows to remove: '+completed.join(', '):'');
      flow.onChange();return;
    }
    if (files.some(file => !(file instanceof Blob) || !name(file.name) || !size(file.size) || !fileType(file))) {
      local.error = 'Files must use supported matching extensions/types and fit the 20-file, 25 MiB/file, 100 MiB limits.'; flow.onChange(); return;
    }
    local.error = null;
    for (const file of files) local.queue.push({id:++local.next,file,type:fileType(file),phase:'metadata',error:null,
      payload:{request_id:flow.idFactory(),idea_id:flow.state.idea_id,expected_revision:flow.state.revision,name:file.name,declared_type:fileType(file),size:file.size}});
    input.value = ''; flow.onChange();
  });
  group.append(label,input);
  if (!typed(flow.state?.idea_id,'idea')) group.append(element('p','Save Capture before uploading files.'));
  else if (flow.state.idea_status !== 'active') group.append(element('p',flow.state.idea_status==='archived'?
    'This idea is archived. Existing files remain readable; new uploads are unavailable.':'Idea status is unavailable. Reload before starting an upload.','notice'));
  if (!inventory) group.append(element('p',inventoryMessage(flow),'notice'));
  else if (inventory.total >= 256) group.append(element('p','Retained asset capacity is exhausted. Existing history remains available.','notice'));
  if (local.error) { const error=element('p',local.error,'notice'); error.setAttribute('role','alert'); group.append(error); }
  for (const task of local.queue) {
    const row=element('div','','g-card'); row.append(element('p',task.file.name+' · '+task.file.size+' bytes · '+task.phase));
    if (task.error) { const error=element('p',task.error,'notice'); error.setAttribute('role','status'); row.append(error); }
    const rowActions=element('div','','setup-actions');
    if (task.phase !== 'complete') {
      const checking=task.phase.startsWith('check-');
      const action=button(task.phase==='refused'?'Remove and reselect file':checking?'Check upload result':task.phase==='bytes'?'Upload file bytes':'Upload file',handle(async()=>{
        if (task.phase === 'metadata') {
          if (await uploadAction(ctx,task,'metadata')) await uploadAction(ctx,task,'bytes');
        } else await uploadAction(ctx,task,task.phase);
      }));
      action.id='upload-action-'+task.id; action.disabled=Boolean(task.phase==='refused' || blocked(ctx) || !inventory || (!checking && flow.state.idea_status!=='active')); rowActions.append(action);
      if (task.phase === 'metadata' && !task.error) {
        for (const intent of inventory?.intents ?? []) if (intent.session_id === flow.state.session_id && intent.name === task.file.name &&
          intent.size === task.file.size && intent.declared_type === task.type && !inventory.assets.some(asset => asset.upload_id === intent.upload_id)) {
          const resume=button('Resume saved upload '+intent.upload_id,handle(()=>{
            task.upload_id=intent.upload_id; task.asset_id=intent.asset_id; task.phase='bytes'; flow.onChange();
          })); resume.id='upload-resume-'+task.id+'-'+intent.upload_id; resume.disabled=Boolean(blocked(ctx)||flow.state.idea_status!=='active'); rowActions.append(resume);
        }
      }
      if (!checking) {
        const remove=button('Remove local selection',handle(()=>{local.queue=local.queue.filter(value=>value!==task);flow.onChange();}));
        remove.id='upload-remove-'+task.id; remove.disabled=Boolean(blocked(ctx)); rowActions.append(remove);
      }
    } else {
      const remove=button('Remove completed local selection',handle(()=>{local.queue=local.queue.filter(value=>value!==task);flow.onChange();}));
      remove.id='upload-remove-'+task.id;remove.disabled=Boolean(blocked(ctx));rowActions.append(remove);
    }
    row.append(rowActions);group.append(row);
  }
  if (inventory) {
    group.append(element('p',inventory.intents.filter(intent=>!inventory.assets.some(asset=>asset.upload_id===intent.upload_id)).length+' retained incomplete upload intents.'));
    if (inventory.orphans.count) group.append(element('p',inventory.orphans.count+' retained unlinked stages/blobs; '+inventory.orphans.ids.length+' opaque IDs reported. No automatic cleanup.'));
    for (const asset of inventory.assets) {
      const row=element('p',asset.name+' · '+asset.validated_type+' · '+asset.size+' bytes · complete');
      row.id='uploaded-'+asset.asset_id;
      const download=button('Download '+asset.name,handle(async()=>{
        if(blocked(ctx))return;
        flow.busy=true;flow.mutationEpoch++;flow.onChange();
        let url;
        try {
          const blob=await flow.api.attachment(asset.asset_id,asset.size);
          if(!(blob instanceof Blob)||blob.size!==asset.size)throw invalid();
          // Active formats are never placed in an iframe, image, window or href
          // to the original MIME. Every file uses an inert local download.
          const urls=ctx.objectUrls??globalThis.URL;
          url=urls.createObjectURL(new Blob([blob],{type:'application/octet-stream'}));
          const anchor=element('a');anchor.href=url;anchor.download=safeDownloadName(asset.name,asset.asset_id);anchor.click();
          // Allow ten seconds for the browser to consume this local download.
          // The existing busy/mutation guard remains; native download timing is a later qualification gate.
          const published=url;
          (ctx.defer??globalThis.setTimeout)(()=>urls.revokeObjectURL(published),10000);url=null;
          local.error=null;
        } catch(error){local.error='Download unavailable. '+failureMessage(error.code,true);}
        finally {if(url)(ctx.objectUrls??globalThis.URL).revokeObjectURL(url);flow.busy=false;flow.onChange();}
      }));download.id='download-'+asset.asset_id;download.disabled=Boolean(blocked(ctx));row.append(download);group.append(row);
    }
  }
  body.append(group);
  return {inventory,local};
}

export function safeDownloadName(display,assetId) {
  // Header/path hygiene affects only the download hint; visible names remain
  // exact literal metadata. The server selects bytes solely by the opaque ID.
  const clean=Array.from(display.replace(/[\u0000-\u001f\u007f/\\:*?"<>|]/g,'_')).slice(0,200).join('').replace(/[. ]+$/g,'');
  return clean.trim()?clean:assetId;
}

const SKILLS_URL = 'https://github.com/mattpocock/skills';
const MISSED = 'The prototype did not reach this page. Ask your terminal to send it again, or choose another road.';
const CARD_CLAUDE = 'Visualize in Claude Design and import it back', CARD_PROTOTYPE = 'Prototype Here', CARD_SKIP = 'Skip visualization';
// The prototype set the terminal filled: exactly one zip and one png, both complete in the verified inventory.
function prototypeSet(current, inventory) {
  if (current.source !== 'prototype' || !Array.isArray(current.assets) || current.assets.length !== 2 || !inventory) return null;
  const found = current.assets.map(id => inventory.assets.find(asset => asset.asset_id === id));
  if (!found.every(Boolean)) return null;
  const zip = found.find(asset => asset.validated_type === 'application/zip'), png = found.find(asset => asset.validated_type === 'image/png');
  return zip && png ? {zip, png} : null;
}
function screenshot(ctx, local, png) {
  const {flow} = ctx;
  if (!local.shots) local.shots = {};
  if (!local.shots[png.asset_id]) {
    const entry = local.shots[png.asset_id] = {url: null, failed: false};
    Promise.resolve().then(async () => {
      try {
        const blob = await flow.api.attachment(png.asset_id, png.size);
        if (!(blob instanceof Blob) || blob.size !== png.size) throw invalid();
        entry.url = (ctx.objectUrls ?? globalThis.URL).createObjectURL(new Blob([blob], {type: 'image/png'}));
      } catch { entry.failed = true; }
      flow.onChange();
    });
  }
  return local.shots[png.asset_id];
}

export function render(ctx) {
  const {body,foot,flow,element,button,handle} = ctx, local=session(flow);
  const current=()=>flow.buffers.visualize;
  const change=fields=>{if(writeBlocked(ctx))return;edited(fields);flow.onChange();};
  const edited=fields=>ctx.edited('visualize',fields);
  const make=(disposition,design_set_id,source)=>({disposition,reason:null,design_set_id,brief_evidence_id:null,...(source?{source}:{})});
  if(flow.state?.idea_status!=='active')body.append(element('p',flow.state?.idea_status==='archived'?
    'This idea is archived. Design-set and disposition changes are unavailable; existing files remain downloadable.':
    'Idea status is unavailable. Reload before changing a Visualize decision.','notice'));
  const card=(title,id,hint='')=>{const box=element('section','','g-card choice-card');box.id=id;box.setAttribute('aria-label',title);box.append(element('h2',title));if(hint)box.append(element('p',hint,'g-sub'));return box;};
  const cards=element('div','','choice-cards'),claude=card(CARD_CLAUDE,'visualize-card-claude'),proto=card(CARD_PROTOTYPE,'visualize-card-prototype','Your terminal builds a clickable prototype and opens it in a second tab.'),skip=card(CARD_SKIP,'visualize-card-skip','Move on without a visual design. No reason is needed.');
  cards.append(claude,proto,skip);body.append(cards);

  // Card 1: Claude Design. The brief, copy and upload road, unchanged in behaviour.
  const brief=visualBrief(flow), briefBox=element('section'); briefBox.setAttribute('aria-label','Derived visual brief');
  if (brief) {
    const label=element('label','Brief from current accepted answers'); label.htmlFor='visualize-brief';
    const text=element('textarea','','g-locked'); text.id='visualize-brief';text.value=brief;text.readOnly=true;briefBox.append(label,text);
    const copyButton=button('Copy brief',handle(async()=>{
      try {const clipboard=ctx.clipboard??globalThis.navigator?.clipboard;if(!clipboard?.writeText)throw new Error('unavailable');await clipboard.writeText(brief);local.copyMessage='Brief copied.';}
      catch {local.copyMessage='Clipboard unavailable. Select and copy the brief manually.';}flow.onChange();
    }),'bw-btn bw-btn--secondary'); copyButton.id='visualize-copy';copyButton.disabled=false;briefBox.append(actions(element,copyButton));
  } else briefBox.append(element('p','Current saved Capture, Discovery and Exploration are required for a derived brief and set acceptance.','notice'));
  briefBox.append(element('p','The brief uses your accepted answers. Create designs with your chosen tool, then upload the files here.','help'));
  if(local.copyMessage){const message=element('p',local.copyMessage,'notice');message.id='visualize-copy-status';message.setAttribute('role','status');briefBox.append(message);}claude.append(briefBox);
  const {inventory}=renderUploads({...ctx,body:claude});
  const choices=element('section');choices.setAttribute('aria-label','Explicit design-set membership');
  const newSet=button('Create a new set from selected files',handle(()=>{local.members=[];change(make('accepted_set',null,'claude_design'));}));
  newSet.id='visualize-new-set';newSet.disabled=Boolean(writeBlocked(ctx)||!brief||!inventory);choices.append(actions(element,newSet));
  for (const asset of inventory?.assets??[]) {
    const label=element('label',asset.name),check=element('input');check.type='checkbox';check.id='visualize-member-'+asset.asset_id;label.htmlFor=check.id;
    check.checked=local.members.includes(asset.asset_id);check.disabled=Boolean(writeBlocked(ctx)||current().disposition!=='accepted_set'||current().design_set_id!==null||!brief);
    check.addEventListener('change',event=>{
      if(check.disabled)return;
      if(event.target.checked&&!local.members.includes(asset.asset_id))local.members.push(asset.asset_id);
      else if(!event.target.checked)local.members=local.members.filter(id=>id!==asset.asset_id);
      change({...current()});
    });const pick=element('div','','g-check');pick.append(check,label);choices.append(pick);
  }
  for(const set of inventory?.sets??[]) {
    const eligible=eligibleSet(flow,set,inventory);
    const setRow=element('div','','g-card');setRow.append(element('p',set.set_id+' · '+set.members.map(member=>member.name).join(', ')+' · '+(eligible?'current saved source':'historical / stale source')));
    const use=button('Select existing set '+set.set_id,handle(()=>{local.members=[];change(make('accepted_set',set.set_id,'claude_design'));}));
    use.id='visualize-set-'+set.set_id;use.disabled=Boolean(writeBlocked(ctx)||!eligible);use.setAttribute('aria-pressed',String(current().design_set_id===set.set_id));setRow.append(actions(element,use));choices.append(setRow);
  }
  if(current().design_set_id)choices.append(element('p','Selected set: '+current().design_set_id,'help'));
  if(local.members.length)choices.append(element('p',local.members.length+' explicitly selected files.','help'));
  claude.append(choices);

  // Card 2: Prototype Here. The terminal builds it; the page only asks and shows the result.
  const state=flow.state, agent=state?.agent_status==='connected';
  const ready=brief?prototypeSet(current(),inventory):null;
  const answer=flow.proposals('visualize').filter(item=>!item.stale&&item.accepted_revision===state?.revision).at(-1);
  const skill=answer?.proposal?.prototype_skill, waiting=flow.proposalPending?.key==='visualize', talking=state?.conversation?.operation==='visual_brief';
  const phase=ready||!agent?null:(waiting||talking)?'building':skill==='available'?'missed':null;
  const ask=button(CARD_PROTOTYPE,handle(()=>flow.requestProposal('visualize')));
  ask.id='visualize-prototype';ask.disabled=Boolean(writeBlocked(ctx)||!brief||!flow.canPropose('visualize'));
  proto.append(actions(element,ask));
  if(!agent&&!ready){const need=element('p','Prototype Here needs your terminal. Reinvoke /glitch-idea in your terminal to use this road; the other two choices still work.','notice');need.id='visualize-prototype-needs-terminal';proto.append(need);}
  if(skill==='unavailable'&&!ready){
    const pointer=element('p','Prototype Here uses Matt Pocock\'s prototype skill. Get it from ','notice');pointer.id='visualize-prototype-skill';
    const link=element('a',SKILLS_URL);link.href=SKILLS_URL;link.target='_blank';link.rel='noopener noreferrer';
    pointer.append(link,element('span',', install it, then refresh this page.'));proto.append(pointer);
  } else if(phase){
    const status=element('p',phase==='building'?'Your terminal is building the prototype — it opens in a second tab':MISSED,'notice');status.id='visualize-prototype-status';status.setAttribute('role','status');proto.append(status);
  }
  if(ready){
    proto.append(element('p','Prototype ready: '+ready.zip.name+' and '+ready.png.name+'.','notice'));
    const shot=screenshot(ctx,local,ready.png);
    if(shot.url){const image=element('img');image.id='visualize-prototype-shot';image.src=shot.url;image.alt='Screenshot of your prototype';proto.append(image);}
    else proto.append(element('p',shot.failed?'The prototype screenshot could not be loaded.':'Loading the prototype screenshot…','help'));
  }

  // Card 3: Skip. One click records the decision; no reason is needed.
  const skipButton=button(CARD_SKIP,handle(()=>{
    if(writeBlocked(ctx))return false;local.members=[];edited({disposition:'skipped',reason:current().reason??null,design_set_id:null,brief_evidence_id:null});
    flow.onChange();return flow.saveVisualize(null);
  }));
  skipButton.id='visualize-skipped';skipButton.disabled=Boolean(writeBlocked(ctx));skipButton.setAttribute('aria-pressed',String(current().disposition==='skipped'));skip.append(actions(element,skipButton));

  const selected=local.members.map(id=>inventory?.assets.find(asset=>asset.asset_id===id));
  const effective=ready?{...current(),disposition:'accepted_set',design_set_id:null}:current();
  const complete=validVisualize(effective)&&(effective.disposition!=='accepted_set'||['claude_design','prototype'].includes(effective.source))&&(Boolean(ready)||effective.disposition!=='accepted_set'||Boolean(brief&&inventory&&
    (effective.design_set_id===null?selected.length>=1&&selected.length<=20&&selected.every(Boolean)&&selected.reduce((total,asset)=>total+asset.size,0)<=MAX_SET:
      inventory.sets.some(set=>set.set_id===effective.design_set_id&&eligibleSet(flow,set,inventory)))));
  const accept=button('Accept Visualize decision',handle(()=>{
    if(writeBlocked(ctx))return false;
    const now=current();
    if(ready){
      if(now.disposition!=='accepted_set'||now.design_set_id!==null)edited({...now,disposition:'accepted_set',design_set_id:null});
      return flow.saveVisualize([...now.assets]);
    }
    return flow.saveVisualize(current().disposition==='accepted_set'&&current().design_set_id===null?[...local.members]:null);
  }),'primary');
  accept.id='visualize-accept';accept.disabled=Boolean(writeBlocked(ctx)||!complete);
  if(accept.disabled){
    const list=acceptBlockers('visualize',current(),flow),add=sentence=>{if(!list.includes(sentence))list.push(sentence);};
    const {flow:f}=ctx;
    if(!ctx.connected)add('Reconnect to the idea service before accepting.');
    if(f.busy)add('Wait for the current save or upload to finish.');
    if(f.pending)add('Wait for the previous save to be confirmed or recovered.');
    if(f.paused)add('Resume before accepting.');
    if(f.state?.idea_status==='archived')add('This idea is archived, so it cannot accept new answers.');
    else if(f.state?.idea_status!=='active')add('The idea status is unavailable. Reload before accepting.');
    if(current().disposition==='accepted_set'){
      if(!brief)add('Save Capture, Discovery and Exploration first so the design brief exists.');
      if(!inventory)add('The file inventory is unavailable. Reload before accepting a design set.');
      if(current().design_set_id===null){
        if(current().source==='prototype'&&!ready)add('The prototype is not ready yet. Wait for your terminal to finish building it, or choose another road.');
        else if(!selected.length)add('Choose a design set to accept, or upload files and select them, or skip this step.');
        else if(selected.length>20)add('Select at most 20 files.');
        else if(!selected.every(Boolean))add('A selected file is no longer available. Reselect your files.');
        else if(selected.reduce((total,asset)=>total+asset.size,0)>MAX_SET)add('The selected files exceed the 100 MiB set limit.');
      } else if(inventory&&!inventory.sets.some(set=>set.set_id===current().design_set_id&&eligibleSet(flow,set,inventory)))
        add('The selected design set is out of date. Choose a current set or create a new one.');
    } else if(phase==='building')add('Your terminal is still building the prototype. Wait for it, or choose another road.');
    else if(phase==='missed')add(MISSED);
    if(!list.length)add('Finish the design choice.');
    const why=list.join(' ');
    {const reason=element('p',why,'foot-message accept-reason');reason.id='visualize-accept-reason';foot.append(reason);accept.setAttribute('aria-describedby','visualize-accept-reason');accept.title=why;}
  }
  foot.append(accept);
  body.append(element('p','Pause saves your decision draft, not local files or new-set checkbox choices. Uploads and accepted sets remain in verified history.','g-sub'));
}
