"""Bounded authenticated upload and inert attachment routes.

Native Windows/macOS and remote client uploads remain NOT OBSERVED. Streams
retain stages and blobs; only Store publishes immutable metadata and receipts.
Signature checks identify basic formats, never guarantee safe file contents.
"""
import codecs
from contextlib import nullcontext
import copy
import hashlib
import os
import re
import stat
import uuid
from urllib.parse import quote

import idea_asset_evidence as codec
from idea_bridge import BoundedBody, Response
from idea_domain import IdeaError, check_id, digest, integer, now, require
from idea_platform import sync_directory
from idea_steps import TrustedRoute
from idea_store import MAX_STORE_FILES, _request_ids, _request_json
from idea_steps.visualize import current_source
from idea_workflow import empty_workflow

RASTER = frozenset(('image/png','image/jpeg','image/webp'))
TEXT = frozenset(('image/svg+xml','text/html','text/css','application/json','text/markdown','text/plain'))


def _id(value,prefix):
    require(type(value) is str and re.fullmatch(prefix+r'_[0-9a-f]{32}',value),
            'Unknown asset identity','asset_not_found')


def _records(app,state):
    return [record for key in state['order'] for record in app.store.asset_records(state,key)]


def _find(app,state,value,kind):
    field = 'upload_id' if kind=='upload-intent' else 'asset_id'
    matches = [record for record in _records(app,state) if record['kind']==kind and record[field]==value]
    require(len(matches)==1,'Unknown asset identity','asset_not_found')
    record = matches[0]
    if kind=='upload-intent':
        require(record['session_id']==app.context.session_id,'Upload belongs to another session','request_conflict')
    return record


def upload_metadata(binding,request,payload):
    app = binding.application
    require(type(payload) is dict and set(payload)==
        {'request_id','idea_id','expected_revision','name','declared_type','size'},'Unexpected upload metadata')
    _request_ids(app.context.session_id,payload['request_id']); check_id(payload['idea_id'])
    integer(payload['expected_revision'],'expected revision',1)
    # Validate display name/type/size before admission; generated IDs below are
    # temporary schema witnesses, never published or treated as a request result.
    probe = dict(schema_version=1,kind='upload-intent',idea_id=payload['idea_id'],
        source_revision=payload['expected_revision'],actor=app.context.actor,timestamp='validation',
        upload_id='upload_'+'0'*32,asset_id='asset_'+'0'*32,session_id=app.context.session_id,
        name=payload['name'],declared_type=payload['declared_type'],size=payload['size'])
    codec.validate_record(probe)
    def prepare(state):
        require(payload['idea_id'] in state['ideas'],'Unknown idea','not_found')
        require(state['ideas'][payload['idea_id']]['status']=='active','Archived ideas refuse new uploads','idea_archived')
        require(state['ideas'][payload['idea_id']]['revision']==payload['expected_revision'],
                'Stale idea revision','stale_revision')
        record = dict(probe,upload_id='upload_'+uuid.uuid4().hex,asset_id='asset_'+uuid.uuid4().hex,timestamp=now())
        return [record]
    def business(state):
        record = app.store.asset_records(state,payload['idea_id'])[-1]
        return dict(idea_id=payload['idea_id'],upload_id=record['upload_id'],asset_id=record['asset_id'],
                    completion_request_id='upload-bytes:'+record['upload_id'])
    result = app.store.mutate_assets(app.context.session_id,payload['request_id'],
        dict(operation='upload-metadata',payload=payload),business,prepare_records=prepare)
    app.context.selected_idea_id = result['idea_id']
    return result


def _directory(store,relative):
    store._safe_root()
    path = store._safe(relative,directory=True)
    if not path.exists():
        missing = []
        for candidate in (path,*path.parents):
            if candidate == store.path or candidate.exists(): break
            missing.append(candidate)
        for candidate in reversed(missing):
            candidate.mkdir(mode=0o700,exist_ok=True)
        store._safe(relative,directory=True)
        sync_directory(path.parent)
    # The shared asset root may have been created earlier by metadata writes; keep it owner-only too.
    for owned in (store.path/'assets',path):
        if os.name == 'posix' and owned.is_dir() and stat.S_IMODE(owned.stat().st_mode) & 0o077:
            os.chmod(owned,0o700)
    require(path.stat().st_dev==store.path.stat().st_dev,'Asset directory is on another filesystem','corrupt_store')
    return path


def _signature(mime,head):
    stripped = (head[3:] if head.startswith(b'\xef\xbb\xbf') else head).lstrip()
    valid = {
        'image/png':head.startswith(b'\x89PNG\r\n\x1a\n'),
        'image/jpeg':head.startswith(b'\xff\xd8\xff'),
        'image/webp':len(head)>=12 and head[:4]==b'RIFF' and head[8:12]==b'WEBP',
        'application/pdf':head.startswith(b'%PDF-'),
        'application/zip':head[:4] in (b'PK\x03\x04',b'PK\x05\x06',b'PK\x07\x08'),
    }
    if mime=='image/svg+xml':
        valid[mime] = bool(re.match(br'(?:<\?xml\s[^>]*\?>\s*)?<svg(?:\s|>)',stripped,re.I))
    elif mime=='text/html':
        valid[mime] = bool(re.match(br'(?:<!doctype\s+html(?:\s|>)|<html(?:\s|>))',stripped,re.I))
    elif mime=='application/json':
        # A bounded basic signature, not a structural parser/validator.
        valid[mime] = bool(stripped) and stripped[:1] in b'{["-0123456789tfn'
    elif mime in TEXT:
        valid[mime] = bool(head)
    require(valid.get(mime,False),'Declared file type and basic signature disagree','invalid_asset_type')


def _stream(store,intent,body,*,staging=True):
    require(isinstance(body,BoundedBody),'A bounded upload stream is required')
    require(body.remaining==intent['size'],'Upload length differs from intent','request_conflict')
    relative,path = None,None
    decoder = codecs.getincrementaldecoder('utf-8')() if intent['declared_type'] in TEXT else None
    hashed,head,size = hashlib.sha256(),bytearray(),0
    try:
        descriptor = None
        if staging:
            require(callable(getattr(os,'fchmod',None)),
                    'Descriptor stage sealing unavailable','platform_unavailable')
            # Admission and exclusive physical allocation share one short lock.
            # Release it with an open descriptor before any network/body read.
            with store.transaction():
                paths = store._asset_files()
                require(sum(len(values) for values in paths.values())+3 <= MAX_STORE_FILES,
                        'Retained asset capacity exhausted','too_large')
                _directory(store,'assets/staging')
                relative = 'assets/staging/'+intent['upload_id']+'.'+uuid.uuid4().hex+'.part'
                path = store._safe(relative)
                descriptor = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0),0o600)
        with (os.fdopen(descriptor,'wb') if staging else nullcontext(None)) as stage:
            if stage is not None:
                info = os.fstat(stage.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_dev==store.path.stat().st_dev,
                        'Unsafe upload stage','corrupt_store')
            while body.remaining:
                chunk = body.read(); size += len(chunk)
                require(size<=intent['size']<=codec.MAX_FILE,'Upload exceeds declared size','too_large')
                if decoder is not None:
                    require(b'\x00' not in chunk,'Text asset contains binary data','invalid_asset_type')
                    decoder.decode(chunk)
                if len(head)<65536: head.extend(chunk[:65536-len(head)])
                hashed.update(chunk)
                if stage is not None: stage.write(chunk)
            if decoder is not None: decoder.decode(b'',final=True)
            if stage is not None:
                stage.flush(); os.fsync(stage.fileno())
        if staging:
            store._safe(relative)
            sync_directory(path.parent)
    except UnicodeError as exc:
        raise IdeaError('invalid_asset_type','Text asset must be UTF-8') from exc
    except OSError as exc:
        raise IdeaError('io_error','Upload staging failed; retained bytes may remain') from exc
    require(size==intent['size'],'Incomplete upload','request_conflict')
    _signature(intent['declared_type'],bytes(head))
    return relative,hashed.hexdigest(),size


def _publish_blob(store,stage,record):
    source = store._safe(stage)
    try:
        # Same-filesystem exclusive link preserves atomic blob appearance.
        # Seal the retained stage against accidental edits; a privileged local
        # writer can still alter the shared inode, so Store always rehashes it.
        descriptor = os.open(source,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
        with os.fdopen(descriptor,'rb') as stream:
            info = os.fstat(stream.fileno()); observed = source.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_dev==store.path.stat().st_dev
                    and (info.st_dev,info.st_ino)==(observed.st_dev,observed.st_ino)
                    and info.st_size==record['size'],'Unsafe retained stage','corrupt_store')
            seal = getattr(os,'fchmod',None)
            require(callable(seal),'Descriptor stage sealing unavailable','platform_unavailable')
            seal(stream.fileno(),0o400); os.fsync(stream.fileno())
            # Intents/stages may have committed while the body streamed. Recount
            # and link under the shared Store lock, without reading the body.
            with store.transaction():
                _directory(store,'assets/blobs')
                target = store._safe(record['blob_path'])
                observed = store._safe(stage).lstat()
                require((info.st_dev,info.st_ino)==(observed.st_dev,observed.st_ino),
                        'Retained stage changed before publication','corrupt_store')
                if not target.exists():
                    paths = store._asset_files()
                    require(sum(len(values) for values in paths.values())+1 <= MAX_STORE_FILES,
                            'Retained asset capacity exhausted','too_large')
                os.link(source,target,follow_symlinks=False)
        sync_directory(target.parent)
    except FileExistsError:
        try: store._asset_blob(record)
        except IdeaError as exc:
            raise IdeaError('request_conflict','Existing upload bytes differ; blob retained') from exc
    except OSError as exc:
        raise IdeaError('io_error','Blob publication failed; stages/blobs retained') from exc
    store._asset_blob(record)


def upload_bytes(binding,request,body):
    app = binding.application; upload_id = request.resource_id; _id(upload_id,'upload')
    with app.store.transaction() as state:
        intent = copy.deepcopy(_find(app,state,upload_id,'upload-intent'))
        completed = [record for record in app.store.asset_records(state,intent['idea_id'])
                     if record['kind']=='asset' and record['upload_id']==upload_id]
        require(len(completed)<=1,'Duplicate upload completion','corrupt_store')
        if completed:
            require(all(completed[0][key]==intent[key] for key in
                ('upload_id','asset_id','idea_id','session_id','source_revision','name','declared_type','size')),
                'Completed upload differs from intent','corrupt_store')
        else:
            require(state['ideas'][intent['idea_id']]['status']=='active',
                    'Archived ideas refuse incomplete uploads','idea_archived')
    # Stage admission has a short transaction; every body read runs unlocked.
    stage,sha,size = _stream(app.store,intent,body,staging=not completed)
    request_id = 'upload-bytes:'+upload_id
    payload = dict(operation='upload-bytes',upload_id=upload_id,idea_id=intent['idea_id'],size=size,sha256=sha)
    if completed:
        # A verified completion never falls back to publication, even if its
        # specifically derived receipt is unavailable. Reconcile/refuse only.
        return app.store.request_result(app.context.session_id,request_id,digest(_request_json(payload)))
    try:
        return app.store.request_result(app.context.session_id,request_id,digest(_request_json(payload)))
    except IdeaError as exc:
        if exc.code!='request_not_found': raise
    record = dict(intent,kind='asset',blob_path=codec.blob_path(intent['asset_id']),
                  validated_type=intent['declared_type'],sha256=sha,timestamp=now(),actor=app.context.actor)
    codec.validate_record(record)
    _publish_blob(app.store,stage,record)
    def prepare(state):
        require(_find(app,state,upload_id,'upload-intent')==intent,'Upload intent changed','request_conflict')
        require(state['ideas'][intent['idea_id']]['status']=='active',
                'Archived ideas refuse incomplete uploads','idea_archived')
        return [record]
    result = app.store.mutate_assets(app.context.session_id,request_id,payload,
        lambda state:dict(idea_id=intent['idea_id'],upload_id=upload_id,asset_id=intent['asset_id'],
                          completion_request_id=request_id,size=size,sha256=sha),prepare_records=prepare)
    app.context.selected_idea_id = result['idea_id']
    return result


def attachment(binding,request,payload):
    app = binding.application; asset_id = request.resource_id; _id(asset_id,'asset')
    require(payload is None,'Attachment read takes no payload')
    with app.store.transaction() as state:
        record = _find(app,state,asset_id,'asset')
        path = app.store._safe(record['blob_path'])
        try:
            descriptor = os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
            with os.fdopen(descriptor,'rb') as stream:
                info = os.fstat(stream.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_dev==app.store.path.stat().st_dev
                        and info.st_size==record['size'],'Unsafe attachment','corrupt_store')
                raw = stream.read(codec.MAX_FILE+1)
        except OSError as exc:
            raise IdeaError('corrupt_store','Attachment cannot be read') from exc
        require(len(raw)==record['size'] and hashlib.sha256(raw).hexdigest()==record['sha256'],
                'Attachment changed during read','corrupt_store')
    display = ''.join('_' if char in '/\\' else char for char in record['name']
                      if ord(char)>=32 and ord(char)!=127)
    raster = record['validated_type'] in RASTER
    disposition = 'inline' if raster else 'attachment'
    header = disposition+'; filename="'+asset_id+'"; filename*=UTF-8\'\''+quote(display,safe='')
    return Response(raw,headers={'Content-Disposition':header},
                    content_type=record['validated_type'] if raster else 'application/octet-stream')


def visual_disposition(binding,request,payload):
    """Explicit skip/N-A through the same pure generic acceptance reducer."""
    app = binding.application; checked = app._edit_payload(payload,accept=True)
    require(checked['step']=='visualize' and checked['fields'].get('disposition') in ('skipped','not-applicable'),
            'Visual disposition requires skipped or not-applicable')
    live = app._agent_context()  # Capture policy context before entering Store.
    result = app.store.mutate_assets(app.context.session_id,checked['request_id'],
        dict(operation='visual-disposition',payload=checked),
        lambda state:app.accept_in_state(state,checked,live))
    app.context.selected_idea_id = result['idea_id']
    return result


def visual_set_accept(binding,request,payload):
    """Explicit members/new set or existing set, with one acceptance receipt."""
    app = binding.application
    keys = {'request_id','idea_id','expected_revision','expected_draft_version','step','fields',
            'proposal_id','expected_backlog_revision','design_set_id','asset_ids'}
    require(type(payload) is dict and set(payload)==keys,'Unexpected visual-set acceptance fields')
    checked = app._edit_payload({key:value for key,value in payload.items()
                                if key not in ('design_set_id','asset_ids')},accept=True)
    require(checked['step']=='visualize' and checked['fields'].get('disposition')=='accepted_set',
            'Visual set requires accepted_set disposition')
    set_id = payload['design_set_id']; members = payload['asset_ids']
    require(set_id==checked['fields'].get('design_set_id'),'Conflicting design set pointers')
    if set_id is None:
        require(type(members) is list and 1<=len(members)<=codec.MAX_MEMBERS
                and all(type(value) is str for value in members),'New set requires 1–20 explicit asset IDs','too_large')
        require(len(set(members))==len(members),'Duplicate set members')
        for value in members: _id(value,'asset')
        members = copy.deepcopy(members)
    else:
        _id(set_id,'set'); require(members is None,'Existing set requires asset_ids null')
    admitted = dict(checked,design_set_id=set_id,asset_ids=members)
    live = app._agent_context()
    def prepare(state):
        idea = app._idea(state,checked['idea_id'])
        require(idea['status']=='active','Archived ideas refuse new Visualize decisions','idea_archived')
        require(idea['revision']==checked['expected_revision'],'Stale idea revision','stale_revision')
        require(idea.get('workflow',empty_workflow())['draft_version']==checked['expected_draft_version'],
                'Stale draft version','stale_draft_version')
        witness = current_source(idea)
        complete = {record['asset_id']:record for record in app.store.asset_records(state,idea['idea_id'])
                    if record['kind']=='asset'}
        require(all(value in complete for value in members),'Unknown or incomplete set members','asset_not_found')
        snapshots = [dict(asset_id=value,name=complete[value]['name'],type=complete[value]['validated_type'],
                          size=complete[value]['size'],sha256=complete[value]['sha256']) for value in members]
        record = dict(schema_version=1,kind='design-set',set_id='set_'+uuid.uuid4().hex,idea_id=idea['idea_id'],
            session_id=app.context.session_id,
            source_revision=idea['revision'],source=witness,source_digest=codec.source_digest(witness),members=snapshots,
            actor=app.context.actor,timestamp=now())
        return [codec.validate_record(record)]
    def business(state):
        target = set_id
        if target is None:
            target = app.store.asset_records(state,checked['idea_id'])[-1]['set_id']
        final = copy.deepcopy(checked); final['fields']['design_set_id'] = target
        result = app.accept_in_state(state,final,live)
        result['design_set_id'] = target
        return result
    result = app.store.mutate_assets(app.context.session_id,checked['request_id'],
        dict(operation='visual-set/accept',payload=admitted),business,
        prepare_records=prepare if set_id is None else None)
    app.context.selected_idea_id = result['idea_id']
    return result


ROUTES = (TrustedRoute('uploads',upload_metadata),TrustedRoute('upload-bytes',upload_bytes),
          TrustedRoute('attachment',attachment),TrustedRoute('visual-set/accept',visual_set_accept),
          TrustedRoute('visual-disposition',visual_disposition))
