"""Locked recoverable Markdown authority.

Legacy JSON reads stay unchanged; first successful commit migrates recoverably.
External edits and bounded request receipts share the same store lock/journal.
"""
from contextlib import contextmanager
import copy
import getpass
import hashlib
import json
import math
import uuid
import os
from pathlib import Path
import re
import stat
import threading

from idea_platform import store_lock, initialize_marker, sync_directory
from idea_platform import atomic_write as platform_write
import idea_transactions as transactions

from idea_domain import (IdeaError, MAX_INPUT, MAX_STATE, ASSESS_KEYS, assessment,
    check_id, decode, digest, encoded, integer, receipt, require, shape, text, snapshot, now)


def read_bytes(path, limit=MAX_INPUT):
    path=Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise IdeaError('missing_artifact', 'Not a regular file: '+str(path)) from exc
    require(stat.S_ISREG(info.st_mode),'Not a regular nonsymlink file: '+str(path),'missing_artifact')
    with path.open('rb') as stream:
        raw=stream.read(limit+1)
    require(len(raw)<=limit,'File exceeds size limit: '+str(path),'too_large')
    return raw


def fsync_dir(path):
    return sync_directory(path)


def atomic_write(path,raw,immutable=False):
    return platform_write(Path(path),raw,immutable=immutable).raise_for_error()


def empty_state():
    return dict(schema_version=1,transaction_revision=0,backlog_revision=0,order=[],ideas={},placements=[],archives={})


def validate(state):
    try:
        require(isinstance(state,dict),'State must be an object')
        require(set(state)==set(empty_state()),'Unexpected state schema')
        require(type(state['schema_version']) is int and state['schema_version']==1,'Unsupported schema version')
        integer(state['transaction_revision'],'transaction_revision')
        integer(state['backlog_revision'],'backlog_revision')
        require(isinstance(state['ideas'],dict) and isinstance(state['order'],list),'Invalid ideas/order')
        require(len(state['order'])==len(set(state['order'])) and set(state['order'])==set(state['ideas']),'Backlog order is not a permutation of ideas')
        require(isinstance(state['placements'],list) and isinstance(state['archives'],dict),'Invalid placements/archives')
        all_plans=set()
        all_attempts=set()
        expected_archives=set()
        for key,idea in state['ideas'].items():
            check_id(key)
            require(isinstance(idea,dict) and idea['idea_id']==key,'Idea identity mismatch')
            integer(idea['revision'],'idea revision',1)
            require(idea['status'] in ('active','archived'),'Invalid idea status')
            text(idea['origin']['text'],'origin',MAX_INPUT)
            text(idea['origin']['actor'],'origin actor',200)
            require(digest(idea['origin']['text'].encode('utf-8'))==idea['origin']['sha256'],'Origin hash mismatch')
            require(isinstance(idea['revisions'],list) and len(idea['revisions'])==idea['revision'],'Missing revision history')
            for index,rev in enumerate(idea['revisions'],1):
                from idea_workflow import validate_snapshot
                validate_snapshot(rev)
                require(rev['revision']==index,'Nonsequential revision history')
                if rev['shape'] is not None:
                    shape(rev['shape'])
                if rev['ratings'] is not None:
                    integer(rev['ratings']['urgency'],'urgency',1,10)
                    integer(rev['ratings']['importance'],'importance',1,10)
                    text(rev['ratings']['actor'],'rating actor',200)
                require(isinstance(rev['assessments'],list),'Invalid assessment history')
                for a in rev['assessments']:
                    computed=assessment({k:a[k] for k in ASSESS_KEYS})
                    require(computed['score']==a['score'],'Assessment score mismatch')
            for field in ('shape','ratings','assessments'):
                require(idea[field]==idea['revisions'][-1][field],'Current '+field+' differs from revision history')
            _validate_workflow_history(idea)
            require(isinstance(idea['proposals'],list) and isinstance(idea['plans'],list) and isinstance(idea['executions'],list),'Invalid idea histories')
            for plan in idea['plans']:
                check_id(plan['plan_id'],'plan')
                require(plan['plan_id'] not in all_plans,'Duplicate plan ID')
                all_plans.add(plan['plan_id'])
                require(plan['idea_id']==key,'Plan idea mismatch')
                integer(plan['idea_revision'],'linked revision',1,idea['revision'])
                require(Path(plan['path']).is_absolute(),'Plan path must be absolute')
                require(Path(plan['source_path']).is_absolute(),'Working plan path must be absolute')
                require(isinstance(plan['sha256'],str) and re.fullmatch(r'[0-9a-f]{64}',plan['sha256']),'Invalid plan SHA256')
                text(plan['content'],'accepted plan content',MAX_INPUT)
                require(digest(plan['content'].encode('utf-8'))==plan['sha256'],'Committed plan content hash mismatch')
                text(plan['actor'],'plan actor',200)
                require(isinstance(plan['validation'],dict) and plan['validation']['builtin']=='idea-trace-and-sections-v1','Missing validation receipt')
                archive_key=key+'/r'+str(plan['idea_revision'])+'.json'
                expected_archives.add(archive_key)
                expected=dict(idea_id=key,origin=idea['origin'],revision=idea['revisions'][plan['idea_revision']-1])
                require(state['archives'].get(archive_key)==expected,'Missing or changed committed archive snapshot')
            linked={p['plan_id'] for p in idea['plans']}
            for proposal in idea['proposals']:
                require(proposal['idea_id']==key,'Proposal idea mismatch')
                integer(proposal['idea_revision'],'proposal revision',1,idea['revision'])
                integer(proposal['source_backlog_revision'],'proposal backlog revision',0,state['backlog_revision'])
                integer(proposal['position'],'proposal position',1)
                text(proposal['actor'],'proposal actor',200)
                text(proposal['reason'],'proposal reason')
                prior=idea['revisions'][proposal['idea_revision']-1]
                require(proposal['snapshot']==dict(ratings=prior['ratings'],assessments=prior['assessments']),'Proposal assessment snapshot mismatch')
                require(isinstance(proposal['neighbors'],dict) and set(proposal['neighbors'])=={'before','after'},'Invalid proposal neighbors')
            for execution in idea['executions']:
                require(execution['idea_id']==key,'Execution idea mismatch')
                require(execution['plan_id'] in linked,'Execution links unknown plan')
                receipt(execution['receipt'],key,execution['plan_id'])
                require(execution['attempt_id']==execution['receipt']['attempt_id'],'Attempt identity mismatch')
                require(execution['attempt_id'] not in all_attempts,'Duplicate attempt ID')
                all_attempts.add(execution['attempt_id'])
                require(Path(execution['path']).is_absolute(),'Receipt path must be absolute')
                require(isinstance(execution['sha256'],str) and re.fullmatch(r'[0-9a-f]{64}',execution['sha256']),'Invalid receipt SHA256')
                require(execution['status']==execution['receipt']['status'],'Execution status mismatch')
            archived=any(p['idea_revision']==idea['revision'] for p in idea['plans'])
            require((idea['status']=='archived')==archived,'Archive status mismatch')
        require(set(state['archives'])==expected_archives,'Orphaned archive snapshot')
    except (IdeaError,KeyError,TypeError,ValueError,IndexError,OverflowError) as exc:
        raise IdeaError('corrupt_store','Invalid idea authority: '+str(exc)) from exc


def _validate_workflow_history(idea):
    """Accepted records are immutable evidence; drafts/navigation are current."""
    from idea_workflow import validate_workflow
    latest = idea['revisions'][-1]
    if 'workflow' not in idea:
        require('workflow' not in latest, 'Current workflow is missing')
        return
    workflow = validate_workflow(idea['workflow'])
    historical = latest.get('workflow')
    if historical is None:
        require(all(record['acceptance'] is None for record in workflow['steps'].values()),
                'Legacy history cannot establish current workflow acceptance')
    else:
        require(workflow['steps'] == historical['steps'], 'Current accepted workflow differs from history')
        require(workflow['draft_version'] >= historical['draft_version'], 'Draft version predates accepted history')


MAX_STORE_FILES = 4096
MAX_STORE_BYTES = 256 * MAX_INPUT
_IDEA_FILE = re.compile(r'idea_[0-9a-f]{32}\.md')
_ASSET_BLOB = re.compile(r'(asset_[0-9a-f]{32})\.bin')
_ASSET_STAGE = re.compile(r'upload_([0-9a-f]{32})\.([0-9a-f]{32})\.part')
MAX_ORPHAN_IDS = 128


MAX_SESSION_RECEIPTS = 128
MAX_RECEIPT_BYTES = MAX_INPUT
MAX_REQUEST_DEPTH = 32
MAX_REQUEST_NODES = 100000
_SESSION_ID = re.compile(r'session_[0-9a-f]{32}')
_REQUEST_ID = re.compile(r'[A-Za-z0-9_.:-]{1,128}')
_CREDENTIAL_FIELDS = frozenset(('token','accesstoken','refreshtoken','agenttoken','browsertoken',
                              'csrftoken','authorization','cookie','password','secret',
                              'credential','credentials','apikey','pairingcode'))


_UNSET_REQUEST_ID = object()
AGENT_RESPONSE_PREFIX = 'agent-response:'
_AGENT_CORRELATION = frozenset(('request_id','session_id','idea_id','accepted_revision',
                               'draft_version','operation','source_digest'))


def _agent_receipt_id(evidence):
    key = {name:evidence[name] for name in ('binding_id','generation')}
    key['request_id'] = evidence['correlation']['request_id']
    return AGENT_RESPONSE_PREFIX + digest(_request_json(key))


def _agent_record(evidence, actor, proposal_id, timestamp):
    return dict(schema_version=1,kind='agent-proposal',proposal_id=proposal_id,
        binding_id=evidence['binding_id'],generation=evidence['generation'],actor=actor,timestamp=timestamp,
        **evidence['correlation'],data=evidence['source']['data'],proposal=evidence['proposal'])


def _agent_payload(evidence, actor):
    """Validate private reply without generating publication IDs or timestamps."""
    from idea_proposal_evidence import validate_record
    _request_json(evidence)
    require(type(evidence) is dict and set(evidence) == {'binding_id','generation','correlation','source','proposal'},
            'Unexpected private agent evidence fields')
    correlation, source = evidence['correlation'], evidence['source']
    require(type(correlation) is dict and set(correlation) == _AGENT_CORRELATION, 'Unexpected agent correlation fields')
    require(type(source) is dict and set(source) == {'accepted_revision','draft_version','data'}, 'Unexpected agent source fields')
    for name in ('accepted_revision','draft_version'):
        require(type(source[name]) is int and source[name] == correlation[name], 'Agent source/correlation mismatch')
    validate_record(_agent_record(evidence,actor,'proposal_'+'0'*32,'validation-only'))
    return dict(operation='agent-response',evidence=copy.deepcopy(evidence),actor=actor)


def _agent_evidence(record):
    return dict(binding_id=record['binding_id'],generation=record['generation'],
        correlation={name:record[name] for name in _AGENT_CORRELATION},
        source={name:record[name] for name in ('accepted_revision','draft_version','data')},
        proposal=record['proposal'])

def _request_ids(session_id, request_id=_UNSET_REQUEST_ID):
    require(type(session_id) is str and _SESSION_ID.fullmatch(session_id) is not None, 'Invalid receipt session ID')
    if request_id is not _UNSET_REQUEST_ID:
        require(type(request_id) is str and _REQUEST_ID.fullmatch(request_id) is not None, 'Invalid request ID')
    return 'session-recovery/'+session_id+'.json'


def _request_json(value, *, object_required=True):
    """Bound canonical JSON at the trusted service/Store seam; no credentials.

    Service validates operation schemas and binds actors. This routine validates
    representation/size only and excludes reserved top-level auth fields. Nested
    domain data/text is not a transport credential; arbitrary text cannot be
    secret-scanned. Service must keep authentication out of its business result.
    """
    require(not object_required or type(value) is dict, 'Request/result must be a JSON object')
    pending = [(value,1)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        require(nodes <= MAX_REQUEST_NODES and depth <= MAX_REQUEST_DEPTH, 'Request JSON exceeds depth/count limits', 'too_large')
        require(type(item) in (dict,list,str,int,float,bool,type(None)), 'Request contains unsupported JSON type')
        if type(item) is dict:
            for key,child in item.items():
                require(type(key) is str, 'Request JSON keys must be strings')
                name = re.sub(r'[-_]', '', key).lower()
                if depth == 1:
                    require(name not in _CREDENTIAL_FIELDS, 'Transport credential fields do not belong in durable requests')
                pending.extend(((key,depth+1),(child,depth+1)))
        elif type(item) is list:
            pending.extend((child,depth+1) for child in item)
        elif type(item) is str:
            require(not any(0xD800 <= ord(c) <= 0xDFFF for c in item), 'Request contains invalid Unicode')
        elif type(item) is float:
            require(math.isfinite(item), 'Request numbers must be finite')
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise IdeaError('invalid_input', 'Cannot encode bounded request JSON') from exc
    require(len(raw) <= MAX_INPUT, 'Request/result exceeds 1 MiB', 'too_large')
    return raw


def _result_counters(result):
    if 'idea_id' in result:
        check_id(result['idea_id'])
    for key in ('revision','draft_version','backlog_revision'):
        if key in result:
            integer(result[key], key, 1 if key == 'revision' and 'idea_id' in result else 0)
    if 'committed' in result:
        require(type(result['committed']) is bool, 'committed must be a boolean')


def _receipt_record(value, session_id):
    require(type(value) is dict and set(value) == {'schema_version','session_id','receipts'}, 'Unexpected receipt session schema')
    require(type(value['schema_version']) is int and value['schema_version'] == 1 and value['session_id'] == session_id, 'Receipt session identity/version mismatch')
    records = value['receipts']
    require(type(records) is dict and len(records) <= MAX_SESSION_RECEIPTS, 'Receipt session exceeds entry capacity')
    for request_id, record in records.items():
        _request_ids(session_id,request_id)
        require(type(record) is dict and set(record) == {'payload_sha256','result'}, 'Unexpected request receipt schema')
        require(type(record['payload_sha256']) is str and re.fullmatch(r'[0-9a-f]{64}',record['payload_sha256']) is not None, 'Invalid request payload digest')
        result = record['result']
        _request_json(result)
        _result_counters(result)
        require(result.get('ok') is True and type(result.get('code')) is str and bool(result['code']) and len(result['code']) <= 100,
                'Receipt result requires successful typed metadata')
        require(result.get('request_id') == request_id and result.get('write_state') in ('applied','no_op'), 'Receipt result identity/write-state mismatch')
    return value


def _markdown():
    # Legacy reads do not require the new runtime dependency.
    import idea_markdown
    return idea_markdown


def _body_workflow(document):
    """Transient witness for persisted draft/control state, not new authority."""
    prefix = document.body.split(_markdown().NOTES_START,1)[0]
    found = re.findall(r'^### Workflow\n\n```yaml\n(.*?)\n```\n', prefix, re.M | re.S)
    require(len(found) == 1, 'Generated workflow summary missing or duplicated', 'generated_body_conflict')
    from idea_workflow import validate_workflow
    return validate_workflow(_markdown().parse_document(('---\n'+found[0]+'\n---\n').encode()).metadata)


def _external_idea(original, baseline, observer):
    """Classify editable sources, reconcile mirrors, invalidate without acceptance."""
    from idea_workflow import STEP_FIELDS, validate_step_fields, invalidate_external, adapt_snapshot
    result = copy.deepcopy(baseline)
    causes = set()
    mirrors = {}
    def changed(step, fields):
        causes.add(step)
        mirrors.setdefault(step, {}).update(copy.deepcopy(fields))
    for field in ('shape', 'ratings'):
        old, new = baseline[field], original[field]
        if old == new:
            continue
        require(type(new) is dict, 'Current '+field+' must contain valid inputs', 'external_edit_conflict')
        old = old or {}
        if field == 'ratings':
            if old:
                require({k:v for k,v in new.items() if k not in ('urgency','importance')} ==
                        {k:v for k,v in old.items() if k not in ('urgency','importance')}, 'Rating attribution is protected', 'external_edit_conflict')
            else:
                new = dict(new,actor=observer,timestamp=now())
            changed('priorities', {k:new[k] for k in ('urgency','importance') if new[k] != old.get(k)})
        else:
            changed_shape = {k:new[k] for k in STEP_FIELDS['shape'] if new[k] != old.get(k)}
            if changed_shape: changed('shape', changed_shape)
            mapped = {alias:new[key] for key,alias in (('method','selection'),('method_reason','reason')) if new[key] != old.get(key)}
            if mapped: changed('method', mapped)
        result[field] = copy.deepcopy(new)
    require(len(original['assessments']) == len(baseline['assessments']), 'Assessment list membership is protected', 'external_edit_conflict')
    for n, (old,new) in enumerate(zip(baseline['assessments'],original['assessments'])):
        old_core, new_core = ({k:item[k] for k in ASSESS_KEYS} for item in (old,new))
        require({k:v for k,v in old.items() if k not in ASSESS_KEYS | {'score'}} ==
                {k:v for k,v in new.items() if k not in ASSESS_KEYS | {'score'}}, 'Assessment identity/attribution is protected', 'external_edit_conflict')
        computed = assessment(new_core)
        require(new['score'] == old['score'] or (new_core != old_core and new['score'] == computed['score']),
                'Derived assessment score cannot be edited', 'external_edit_conflict')
        if new_core != old_core:
            result['assessments'][n] = dict(new, score=computed['score'])
            changed('assess', {'assessment':new_core} if n == len(original['assessments'])-1 else {})
    if 'workflow' in baseline:
        current, prior = original['workflow'], baseline['workflow']
        require({k:v for k,v in current.items() if k != 'steps'} == {k:v for k,v in prior.items() if k != 'steps'},
                'Workflow draft/navigation controls are protected', 'external_edit_conflict')
        require(set(current['steps']) == set(prior['steps']), 'Workflow step identity is protected', 'external_edit_conflict')
        for step, old in prior['steps'].items():
            new = current['steps'][step]
            require(set(new) == set(old) and new['acceptance'] == old['acceptance'] and new['invalidated_by'] == old['invalidated_by'],
                    'Workflow acceptance/invalidation evidence is protected', 'external_edit_conflict')
            if new['fields'] == old['fields']: continue
            require(old['acceptance'] is not None and step != 'review', 'File edits cannot create acceptance or handoff', 'external_edit_conflict')
            fields = validate_step_fields(step,new['fields'],partial=True)
            if step == 'visualize':
                require(fields.get('design_set_id') == old['fields'].get('design_set_id') and fields.get('brief_evidence_id') == old['fields'].get('brief_evidence_id'),
                        'Visual evidence references require their normal validation', 'external_edit_conflict')
            if step == 'assess':
                require(fields.get('position') == old['fields'].get('position'), 'Placement references require normal acceptance', 'external_edit_conflict')
            edited = {k:fields.get(k) for k in set(fields)|set(old['fields']) if fields.get(k) != old['fields'].get(k)}
            for key,value in mirrors.get(step,{}).items():
                require(key not in edited or edited[key] == value, 'Conflicting legacy/workflow mirrors: '+step+':'+key, 'external_edit_conflict')
            merged = copy.deepcopy(old['fields'])
            merged.update(mirrors.get(step,{})); merged.update(edited)
            changed(step, edited)
            mirrors[step] = merged
        for step, updates in mirrors.items():
            old = prior['steps'][step]['fields']
            draft = copy.deepcopy(prior['drafts'].get(step, old or {}))
            draft.update(updates)
            validate_step_fields(step,draft,partial=True)
            result['workflow']['drafts'][step] = draft
            if step == 'priorities' and result['ratings'] is not None:
                for key in ('urgency','importance'):
                    if key in updates and updates[key] is not None: result['ratings'][key] = updates[key]
            elif step == 'shape' and result['shape'] is not None:
                mirrored = dict(result['shape'], **{k:v for k,v in updates.items() if k in STEP_FIELDS['shape']})
                try: shape(mirrored)
                except IdeaError: pass  # valid partial browser source stays draft-only
                else: result['shape'] = mirrored
            elif step == 'method' and result['shape'] is not None:
                mirrored = copy.deepcopy(result['shape'])
                for key,alias in (('method','selection'),('method_reason','reason')):
                    if alias in updates: mirrored[key] = updates[alias]
                try: shape(mirrored)
                except IdeaError: pass
                else: result['shape'] = mirrored
            elif step == 'assess' and result['assessments'] and 'assessment' in updates:
                try: computed = assessment(updates['assessment'])
                except IdeaError: continue  # partial source stays a review draft
                result['assessments'][-1].update(computed)
        if causes:
            result['workflow']['draft_version'] += 1
    else:
        require('workflow' not in original, 'Workflow cannot be inserted by file editing', 'external_edit_conflict')
    if not causes: return baseline
    result = invalidate_external(result, tuple(causes))['idea']
    result['revision'] += 1
    result['status'] = 'active'
    result['revisions'].append(adapt_snapshot(snapshot(result,observer,'external-file-observed'), result.get('workflow')))
    return result


class Store:
    def __init__(self, path, *, observer=None):
        # Preserve spelling until safety checks: resolve() would hide symlinks.
        self.path = Path(path).expanduser().absolute()
        self.state_path = self.path / 'state.json'
        self._contexts = threading.local()
        if observer is None:
            if os.name == 'posix':
                import pwd
                observer = pwd.getpwuid(os.getuid()).pw_name
            else:
                observer = getpass.getuser()
        text(observer, 'observing account', 200)
        self.observer = observer  # observed account/session, never inferred editor

    def _safe_root(self, create=False):
        missing = []
        for component in reversed((self.path, *self.path.parents)):
            try:
                mode = component.lstat().st_mode
            except FileNotFoundError:
                missing.append(component)
                continue
            require(stat.S_ISDIR(mode) and not stat.S_ISLNK(mode),
                    'Store root/ancestor must be a real directory: '+str(component), 'corrupt_store')
        if create and missing:
            self.path.mkdir(parents=True, exist_ok=True)
            for component in reversed(missing):
                sync_directory(component.parent)

    def _safe(self, relative, directory=False):
        require(type(relative) is str and relative and not relative.startswith('/')
                and ':' not in relative and '\\' not in relative
                and all(part not in ('', '.', '..') for part in relative.split('/')),
                'Unsafe store path', 'corrupt_store')
        path = self.path
        device = self.path.stat().st_dev
        parts = relative.split('/')
        for index, part in enumerate(parts):
            path = path / part
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            is_dir = index < len(parts)-1 or directory
            require(not stat.S_ISLNK(info.st_mode) and info.st_dev == device,
                    'Store symlink or cross-filesystem path refused: '+relative, 'corrupt_store')
            require(stat.S_ISDIR(info.st_mode) if is_dir else stat.S_ISREG(info.st_mode),
                    'Unexpected store filesystem object: '+relative, 'corrupt_store')
        return path

    def _entries(self, relative=''):
        path = self.path if not relative else self._safe(relative, directory=True)
        if not path.exists():
            return []
        entries = []
        for child in path.iterdir():
            entries.append(child.name)
            require(len(entries) <= MAX_STORE_FILES, 'Store directory exceeds entry limit', 'too_large')
        return entries

    def _asset_files(self):
        """Confined metadata authority and retained stream/blob diagnostics."""
        result = {name:set() for name in ('evidence', 'blobs', 'staging')}
        count = 0
        for folder in self._entries('assets'):
            require(folder in result, 'Unknown reserved asset directory', 'corrupt_store')
            relative = 'assets/'+folder
            self._safe(relative, directory=True)
            for name in self._entries(relative):
                count += 1
                require(count <= MAX_STORE_FILES, 'Asset inventory exceeds file limit', 'too_large')
                path = relative+'/'+name
                self._safe(path)
                valid = (transactions._allowed(path) if folder == 'evidence' else
                         _ASSET_BLOB.fullmatch(name) if folder == 'blobs' else _ASSET_STAGE.fullmatch(name))
                require(valid, 'Unknown reserved asset filename: '+path, 'corrupt_store')
                result[folder].add(path)
        return result

    def _asset_entries(self, state, files, docs):
        """Pure reconstruction from verified canonical bytes and protected links."""
        from idea_asset_evidence import decode_record
        result = {}
        md = _markdown()
        for key in state['ideas']:
            document = docs.get(key+'.md')
            result[key] = []
            if document is None:
                continue
            for link in md.asset_links(document.metadata['extensions'], key):
                require(link['path'] in files, 'Missing asset evidence bytes', 'corrupt_store')
                record = decode_record(files[link['path']], path=link['path'], expected_idea_id=key, link=link)
                result[key].append(dict(record=record, evidence=copy.deepcopy(link), blob=None))
        self._asset_graph(state, result)
        return result

    def _asset_graph(self, state, entries):
        """Pure cross-record identity and immutable membership consistency."""
        from idea_asset_evidence import record_id
        ids, reserved_assets, intents, completed = set(), set(), {}, {}
        for key, records in entries.items():
            require(key in state['ideas'], 'Asset links an unknown idea', 'corrupt_store')
            require(len(records) <= _markdown().MAX_ASSET_LINKS, 'Asset link capacity exhausted', 'too_large')
            for entry in records:
                record = entry['record']; rid = record_id(record)
                require(rid not in ids, 'Asset record identity is duplicated', 'corrupt_store')
                ids.add(rid)
                require(record['idea_id'] == key and record['source_revision'] <= state['ideas'][key]['revision'],
                        'Asset idea/source revision mismatch', 'corrupt_store')
                if record['kind'] == 'upload-intent':
                    require(record['asset_id'] not in reserved_assets, 'Asset ID belongs to another intent', 'corrupt_store')
                    reserved_assets.add(record['asset_id'])
                    intents[record['upload_id']] = record
                elif record['kind'] == 'asset':
                    intent = intents.get(record['upload_id'])
                    require(intent is not None and all(record[name] == intent[name] for name in
                            ('upload_id','asset_id','idea_id','session_id','source_revision','name','declared_type','size')),
                            'Complete asset differs from its earlier upload intent', 'corrupt_store')
                    completed[record['asset_id']] = record
                else:
                    for member in record['members']:
                        asset = completed.get(member['asset_id'])
                        require(asset is not None and asset['idea_id'] == key and member ==
                                {name:asset[alias] for name,alias in (('asset_id','asset_id'),('name','name'),
                                  ('type','validated_type'),('size','size'),('sha256','sha256'))},
                                'Design set differs from its earlier complete assets', 'corrupt_store')

    def _asset_blob(self, record):
        """Bounded local verification, never callable network work or publication."""
        from idea_asset_evidence import MAX_FILE
        path = self._safe(record['blob_path'])
        try:
            before = path.lstat()
            descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
            with os.fdopen(descriptor, 'rb') as stream:
                observed = os.fstat(stream.fileno())
                require(stat.S_ISREG(observed.st_mode) and observed.st_dev == self.path.stat().st_dev
                        and (observed.st_dev, observed.st_ino) == (before.st_dev, before.st_ino)
                        and observed.st_size == record['size'] <= MAX_FILE,
                        'Linked asset blob size/object differs', 'corrupt_store')
                hashed, size = hashlib.sha256(), 0
                while True:
                    chunk = stream.read(min(65536, record['size']-size+1))
                    if not chunk:
                        break
                    size += len(chunk)
                    require(size <= record['size'], 'Linked asset blob grew during verification', 'corrupt_store')
                    hashed.update(chunk)
            after = self._safe(record['blob_path']).lstat()
            require((after.st_dev,after.st_ino,after.st_size) == (before.st_dev,before.st_ino,record['size'])
                    and size == record['size'] and hashed.hexdigest() == record['sha256'],
                    'Linked asset blob hash/object differs', 'corrupt_store')
        except OSError as exc:
            raise IdeaError('corrupt_store', 'Linked asset blob is missing or unreadable') from exc
        return dict(path=record['blob_path'], size=size, sha256=hashed.hexdigest())

    def _verify_asset_blobs(self, entries):
        for records in entries.values():
            for entry in records:
                if entry['record']['kind'] == 'asset':
                    entry['blob'] = self._asset_blob(entry['record'])

    def _retained_asset_stages(self, paths, entries):
        """Classify successes at the filesystem boundary only.

        The completed blob was hashed. Its sealed same-inode upload stage is
        that verified object. Physical capacity still counts both file names.
        Inventory consumes this separate detached transaction-context set.
        """
        completed = {entry['record']['upload_id']:entry for records in entries.values()
                     for entry in records if entry['record']['kind'] == 'asset' and entry['blob'] is not None}
        retained = set()
        for relative in paths['staging']:
            match = _ASSET_STAGE.fullmatch(relative.rsplit('/',1)[1])
            entry = completed.get('upload_'+match.group(1))
            if entry is None:
                continue
            try:
                stage = self._safe(relative).lstat()
                blob = self._safe(entry['record']['blob_path']).lstat()
            except OSError as exc:
                raise IdeaError('corrupt_store', 'Asset stage/blob changed during inspection') from exc
            if (stat.S_ISREG(stage.st_mode) and not stage.st_mode & 0o222
                    and not blob.st_mode & 0o222
                    and (stage.st_dev,stage.st_ino,stage.st_size) == (blob.st_dev,blob.st_ino,entry['blob']['size'])
                    and entry['blob']['sha256'] == entry['record']['sha256']):
                retained.add(relative)
        return retained

    def _asset_orphans(self, paths, entries, retained_staging=()):
        linked = {entry['record']['blob_path'] for records in entries.values() for entry in records
                  if entry['record']['kind'] == 'asset'}
        ids = []
        for path in sorted(paths['blobs']-linked):
            ids.append(_ASSET_BLOB.fullmatch(path.rsplit('/',1)[1]).group(1))
        for path in sorted(paths['staging']-set(retained_staging)):
            match = _ASSET_STAGE.fullmatch(path.rsplit('/',1)[1])
            ids.append('stage_'+match.group(1)+'_'+match.group(2))
        return dict(count=len(ids), ids=ids[:MAX_ORPHAN_IDS])

    def _inventory(self):
        """Enumerate all reserved authority/evidence, not arbitrary user folders."""
        owned = set()
        count = 0
        for name in self._entries():
            if _IDEA_FILE.fullmatch(name) or name == 'IDEAS.md':
                self._safe(name)
                owned.add(name)
            elif name.startswith('idea_') and name.endswith('.md'):
                raise IdeaError('corrupt_store', 'Unrecognized idea detail filename: '+name)
        for directory in ('history', 'plan-evidence'):
            if not self._safe(directory, directory=True).exists():
                continue
            pending = [directory]
            while pending:
                folder = pending.pop()
                for name in self._entries(folder):
                    count += 1
                    require(count <= MAX_STORE_FILES, 'Store evidence exceeds entry limit', 'too_large')
                    relative = folder+'/'+name
                    path = self.path/relative
                    info = path.lstat()
                    require(not stat.S_ISLNK(info.st_mode), 'Symlink evidence refused: '+relative, 'corrupt_store')
                    if stat.S_ISDIR(info.st_mode):
                        self._safe(relative, directory=True)
                        # Only these shallow reserved directories are owned.
                        permitted = (folder == 'history' and (name == 'backlog' or re.fullmatch(r'idea_[0-9a-f]{32}',name))) or (re.fullmatch(r'history/idea_[0-9a-f]{32}',folder) and name == 'metadata')
                        require(permitted, 'Unknown evidence directory: '+relative, 'corrupt_store')
                        pending.append(relative)
                    else:
                        self._safe(relative)
                        require(transactions._allowed(relative), 'Unknown evidence file: '+relative, 'corrupt_store')
                        owned.add(relative)
        owned.update(self._asset_files()['evidence'])
        require(len(owned) <= MAX_STORE_FILES, 'Store exceeds file count limit', 'too_large')
        return owned

    def _load_markdown(self):
        try:
            return self._inspect_markdown()
        except (KeyError, TypeError, AttributeError, IndexError, ValueError) as exc:
            raise IdeaError('invalid_markdown', 'Malformed editable Markdown schema: '+str(exc)) from exc

    def _migration_evidence(self, index_raw):
        import idea_migration as migration
        index = _markdown().decode_index(index_raw)
        marker = index.metadata['extensions'].get(migration.MARKER)
        present = set(self._entries('migration-recovery'))
        if marker is None:
            require(not present, 'Unmarked migration recovery evidence', 'corrupt_store')
            return {}
        require(present == {'v1-state.json', 'receipt.json'}, 'Missing/unknown migration evidence', 'corrupt_store')
        evidence = {path:read_bytes(self._safe(path), MAX_INPUT if path == transactions.RECEIPT else MAX_STATE)
                    for path in (transactions.FROZEN, transactions.RECEIPT)}
        receipt = migration.verify(evidence[transactions.RECEIPT], evidence[transactions.FROZEN])
        migration.verify_pointer(marker, receipt)
        require(index.metadata['transaction_revision'] >= receipt['target_transaction_revision'],
                'Index predates migration receipt', 'corrupt_store')
        return evidence

    def _legacy_views(self, context):
        baseline = context['baseline']
        expected_plans = {'plan-evidence/'+plan['plan_id']+'.md'
                          for idea in baseline['ideas'].values() for plan in idea['plans']}
        require(self._inventory() <= expected_plans, 'Partial/orphan Markdown authority in legacy store', 'ambiguous_store')
        files = {'state.json': context['legacy_raw']}
        for relative in sorted(expected_plans | {'archive/'+key for key in baseline['archives']}):
            path = self._safe(relative)
            require(path.exists(), 'Missing legacy view; repair before migration: '+relative, 'migration_view_required')
            files[relative] = read_bytes(path, MAX_STATE)
        require(not self._entries('migration-recovery'), 'Legacy store has unmatched migration evidence', 'ambiguous_store')
        return files

    def _inspect_markdown(self):
        md = _markdown()
        files, docs, normalized, originals = {}, {}, {}, {}
        total = 0
        proposal_receipts = {}
        handoff_entries = {}
        receipt_cache = {}
        def receipt_session(sid):
            nonlocal total
            if sid not in receipt_cache:
                require(len(files)+len(receipt_cache) < MAX_STORE_FILES,
                        'Store and linked receipts exceed file limit', 'too_large')
                receipt_cache[sid] = self._read_receipts(sid)
                total += len(receipt_cache[sid][1])
                require(total <= MAX_STORE_BYTES, 'Store and linked receipts exceed byte limit', 'too_large')
            return receipt_cache[sid]
        def asset_witness(session, link, key):
            return any(witness['result'].get('idea_id') == key
                       and type(witness['result'].get('asset_records')) is list
                       and link in witness['result']['asset_records']
                       for witness in session['receipts'].values())
        def read(relative):
            nonlocal total
            if relative not in files:
                require(len(files) < MAX_STORE_FILES, 'Store exceeds file count limit', 'too_large')
                raw = read_bytes(self._safe(relative), MAX_STATE)
                total += len(raw)
                require(total <= MAX_STORE_BYTES, 'Store exceeds aggregate byte limit', 'too_large')
                files[relative] = raw
            return files[relative]
        index = md.decode_index(read('IDEAS.md'))
        docs['IDEAS.md'] = index
        expected = {'IDEAS.md'}
        for key in index.metadata['order']:
            relative = key+'.md'
            document = md.parse_document(read(relative))
            originals[key] = copy.deepcopy(document.metadata['idea'])
            inspected = copy.deepcopy(document.metadata)
            for entry in inspected['idea']['assessments']:
                entry['score'] = assessment({k:entry[k] for k in ASSESS_KEYS})['score']
            if 'workflow' in inspected['idea']:
                inspected['idea']['workflow'] = _body_workflow(document)
            normalized[relative] = md.encode_document(inspected,document.body)
            md.decode_detail(normalized[relative], check_body=False)
            docs[relative] = document
            expected.add(relative)
            links = list(document.metadata['history'])
            for items in document.metadata['metadata_evidence'].values():
                links.extend(items)
            for link in links:
                expected.add(link['path'])
                read(link['path'])
            from idea_proposal_evidence import decode_proposal
            for ordinal, link in enumerate(md.agent_proposal_links(document.metadata['extensions'], key), 1):
                expected.add(link['path'])
                record = decode_proposal(read(link['path']), path=link['path'], expected_idea_id=key, link=link)
                sid = record['session_id']
                if sid not in receipt_cache:
                    receipt_cache[sid] = self._read_receipts(sid)
                    total += len(receipt_cache[sid][1])
                    require(total <= MAX_STORE_BYTES, 'Store and linked receipts exceed aggregate byte limit', 'too_large')
                receipt_path, receipt_raw, session = receipt_cache[sid]
                proposal_receipts[receipt_path] = receipt_raw
                evidence = _agent_evidence(record)
                rid = _agent_receipt_id(evidence)
                require(rid in session['receipts'], 'Missing linked proposal response receipt', 'corrupt_store')
                witness = session['receipts'][rid]
                require(witness['payload_sha256'] == digest(_request_json(_agent_payload(evidence, record['actor']))),
                        'Proposal differs from response receipt input', 'corrupt_store')
                result = witness['result']
                expected_result = dict(ok=True,code='ok',request_id=rid,write_state='applied',
                    idea_id=key,revision=record['accepted_revision'],draft_version=record['draft_version'],
                    proposal_index=ordinal,**link)
                require(set(result) == set(expected_result) | {'backlog_revision'}
                        and all(type(result[name]) is type(value) and result[name] == value
                                for name,value in expected_result.items()),
                        'Proposal identity/order differs from response receipt', 'corrupt_store')
                require(result['backlog_revision'] <= index.metadata['backlog_revision'],
                        'Proposal receipt backlog is newer than authority', 'corrupt_store')
            for link in md.asset_links(document.metadata['extensions'], key):
                expected.add(link['path'])
                from idea_asset_evidence import decode_record
                asset_record = decode_record(read(link['path']), path=link['path'], expected_idea_id=key, link=link)
                sid = asset_record['session_id']
                receipt_path, receipt_raw, session = receipt_session(sid)
                proposal_receipts[receipt_path] = receipt_raw
                require(asset_witness(session,link,key),
                        'Asset lacks its atomic publication receipt', 'corrupt_store')
            from idea_handoff_evidence import decode_record as decode_handoff
            handoff_entries[key] = []
            for ordinal, link in enumerate(md.handoff_links(document.metadata['extensions'], key), 1):
                expected.add(link['path'])
                packet = decode_handoff(read(link['path']), path=link['path'], expected_idea_id=key, link=link)
                receipt_path, receipt_raw, session = receipt_session(packet['session_id'])
                proposal_receipts[receipt_path] = receipt_raw
                rid = packet['request_id']
                require(rid in session['receipts'], 'Missing publishing handoff receipt', 'corrupt_store')
                witness = session['receipts'][rid]; result = witness['result']
                exact = dict(ok=True,code='ok',request_id=rid,write_state='applied',idea_id=key,
                             revision=packet['source_revision'],handoff_index=ordinal,**link)
                require(set(result) == set(exact) | {'draft_version','backlog_revision'}
                        and all(type(result[name]) is type(value) and result[name] == value
                                for name,value in exact.items()),
                        'Handoff identity/order differs from publishing receipt', 'corrupt_store')
                payload = dict(operation='handoff',payload=dict(request_id=rid,idea_id=key,
                    expected_revision=result['revision'],expected_draft_version=result['draft_version'],
                    expected_backlog_revision=result['backlog_revision']))
                require(witness['payload_sha256'] == digest(_request_json(payload)),
                        'Handoff differs from publishing request payload', 'corrupt_store')
                require(result['backlog_revision'] <= index.metadata['backlog_revision'],
                        'Handoff publishing receipt backlog is newer than authority', 'corrupt_store')
                handoff_entries[key].append(dict(record=packet,evidence=copy.deepcopy(link),
                                                receipt=copy.deepcopy(result)))
            for plan in document.metadata['idea']['plans']:
                relative = 'plan-evidence/'+plan['plan_id']+'.md'
                expected.add(relative)
                read(relative)
        for link in index.metadata['placements']:
            expected.add(link['path'])
            read(link['path'])
        if any(handoff_entries.values()):
            require(len(files)+len(receipt_cache) <= MAX_STORE_FILES,
                    'Store and linked receipts exceed file limit', 'too_large')
        require(self._inventory() == expected, 'Missing, orphaned or unlinked authority/evidence', 'corrupt_store')
        candidate = md.decode_state(dict(files, **normalized), check_body=False)
        baseline = copy.deepcopy(candidate)
        for key, idea in candidate['ideas'].items():
            prior = baseline['ideas'][key]
            latest = idea['revisions'][-1]
            for field in ('shape', 'ratings', 'assessments'):
                prior[field] = copy.deepcopy(latest[field])
            # Restore accepted records while retaining the unchanged persisted
            # draft/navigation state witnessed by the generated body.
            historical = latest.get('workflow')
            if historical is not None:
                require('workflow' in idea, 'Current workflow missing', 'corrupt_store')
                for step, record in idea['workflow']['steps'].items():
                    old = historical['steps'][step]
                    require(record['acceptance'] == old['acceptance'] and record['invalidated_by'] == old['invalidated_by'],
                            'Protected workflow receipt changed: '+key+':'+step, 'corrupt_store')
                prior['workflow']['steps'] = copy.deepcopy(historical['steps'])
            _validate_workflow_history(prior)
            baseline_doc = md.parse_document(md.encode_detail(prior,extensions=docs[key+'.md'].metadata['extensions'])).metadata
            # Check generated text against accepted evidence before naming an
            # import candidate. Notes itself is never discarded or normalized.
            md.detail_notes(docs[key+'.md'], baseline=baseline_doc)
        validate(baseline)
        md.decode_index(files['IDEAS.md'], state=baseline)
        imported = copy.deepcopy(baseline)
        for key in imported['ideas']:
            imported['ideas'][key] = _external_idea(originals[key], baseline['ideas'][key], self.observer)
        validate(imported)
        asset_entries = self._asset_entries(baseline, files, docs)
        self._verify_handoff_history(baseline, files, handoff_entries, asset_entries)
        self._verify_asset_blobs(asset_entries)
        asset_paths = self._asset_files()
        return baseline, files, docs, imported, proposal_receipts, asset_entries, asset_paths

    def _verify_handoff_history(self, state, files, packets, assets):
        """Validate historical sources, never require present business eligibility.

        Detail/index hashes are prepublication observations. The exact immutable
        revision bytes and accepted snapshot establish historical source identity.
        Filesystem availability of workspace is checked only for new delivery.
        """
        for key, entries in packets.items():
            idea = state['ideas'][key]
            for entry in entries:
                packet = entry['record']; revision = packet['source_revision']
                require(revision <= idea['revision'], 'Handoff references a future revision', 'corrupt_store')
                paths = dict(detail=key+'.md',index='IDEAS.md',revision='history/'+key+'/r'+str(revision)+'.md')
                # Codec verifies exact relative layouts under the recorded root.
                # That absolute root is historical observation, not Store identity.
                require(packet['source_files']['revision']['sha256'] == digest(files[paths['revision']]),
                        'Handoff immutable revision hash differs', 'corrupt_store')
                require(packet['origin'] == idea['origin'], 'Handoff immutable origin differs', 'corrupt_store')
                snapshot = idea['revisions'][revision-1]
                historical = snapshot.get('workflow')
                require(historical is not None and 'workflow' in idea,
                        'Handoff lacks managed revision evidence', 'corrupt_store')
                expected_steps = {step:dict(copy.deepcopy(record),invalidated_by=[])
                                  for step,record in packet['accepted'].items()}
                require(all(historical['steps'][step] == record for step,record in expected_steps.items()),
                        'Handoff accepted records differ from immutable revision', 'corrupt_store')
                draft = entry['receipt']['draft_version']
                require(historical['draft_version'] <= draft <= idea['workflow']['draft_version'],
                        'Handoff publishing draft counter differs from authority', 'corrupt_store')
                design = packet['design_set']
                if design is not None:
                    records = [asset['record'] for asset in assets.get(key, [])]
                    selected = next((record for record in records if record['kind'] == 'design-set'
                                     and record['set_id'] == design['set_id']), None)
                    require(selected is not None, 'Handoff design set evidence is missing', 'corrupt_store')
                    members = [{name:value for name,value in member.items() if name != 'path'}
                               for member in design['members']]
                    require(members == selected['members'],
                            'Handoff design membership differs from immutable set', 'corrupt_store')

    @contextmanager
    def transaction(self, write=False):
        require(type(write) is bool, 'write must be a boolean')
        require(getattr(self._contexts, 'active', None) is None, 'Store transactions cannot be nested', 'store_busy')
        self._safe_root(create=write)
        if not self.path.exists():
            state = empty_state()
            yield state
            return
        self._safe('.lock')
        with store_lock(self.path/'.lock') as lock:
            transactions.recover(self.path).raise_for_error()
            index = self._safe('IDEAS.md')
            legacy = self._safe('state.json')
            require(not (index.exists() and legacy.exists()), 'Both JSON and Markdown authority exist without a verified migration', 'ambiguous_store')
            if legacy.exists():
                legacy_raw = read_bytes(legacy, MAX_STATE)
                state = decode(legacy_raw)
                validate(state)
                files, docs, kind = {}, {}, 'legacy'
            elif index.exists():
                migration_evidence = self._migration_evidence(read_bytes(index, MAX_STATE))
                state, files, docs, imported, proposal_receipts, asset_entries, asset_paths = self._load_markdown()
                kind = 'markdown'
            else:
                lock.seek(0)
                require(not lock.read(1), 'Authority is missing from an initialized store; refusing to reset', 'corrupt_store')
                require(not any(name not in ('.lock', transactions.JOURNAL) for name in self._entries()),
                        'Authority is missing from a nonempty store; refusing to reset', 'corrupt_store')
                state, files, docs, kind = empty_state(), {}, {}, 'empty'
            context = dict(write=write, kind=kind, lock=lock, files=files, docs=docs, baseline=copy.deepcopy(state))
            if kind == 'legacy':
                context['legacy_raw'] = legacy_raw
            elif kind == 'markdown':
                context['migration_evidence'] = migration_evidence
                context['proposal_receipts'] = proposal_receipts
                context['asset_entries'] = asset_entries
                context['asset_paths'] = asset_paths
                context['retained_staging'] = self._retained_asset_stages(asset_paths,asset_entries)
            else:
                context['asset_entries'] = {}
                context['asset_paths'] = {name:set() for name in ('evidence','blobs','staging')}
            self._contexts.active = context
            try:
                if kind == 'markdown' and imported != state:
                    context['write'] = True
                    self.commit(imported)
                    context['write'] = write
                    state = imported
                context['state'] = state
                yield state
            finally:
                self._contexts.active = None

    def _check_cas(self, context):
        if context['kind'] == 'legacy':
            require(not self._safe('IDEAS.md').exists(), 'Markdown authority appeared during legacy transaction', 'save_conflict')
            require(transactions.file_hash(self.path, 'state.json') == digest(context['legacy_raw']),
                    'Legacy source changed during transaction', 'save_conflict')
            return
        require(self._inventory() == set(context['files']), 'Authority/evidence changed during transaction', 'save_conflict')
        for relative, raw in context['files'].items():
            require(transactions.file_hash(self.path, relative) == digest(raw),
                    'Observed file changed during transaction: '+relative, 'save_conflict')
        for relative, raw in context.get('migration_evidence', {}).items():
            require(transactions.file_hash(self.path, relative) == digest(raw),
                    'Observed migration evidence changed during transaction: '+relative, 'save_conflict')
        for relative, raw in context.get('proposal_receipts', {}).items():
            require(transactions.file_hash(self.path, relative) == digest(raw),
                    'Observed linked proposal receipt changed during transaction: '+relative, 'save_conflict')
        # Metadata remains immutable, but a cooperating local editor can still
        # alter blob bytes. Rehash both existing and staged verified witnesses.
        for entries in context.get('asset_entries', {}).values():
            for entry in entries:
                if entry['blob'] is not None:
                    require(self._asset_blob(entry['record']) == entry['blob'],
                            'Observed asset blob changed during transaction', 'save_conflict')

    def _prepare_commit(self, state, context, *, initial_zero=False, proposal_append=None,
                        proposal_evidence=None, asset_append=None, asset_evidence=None,
                        handoff_append=None, handoff_evidence=None):
        baseline = context['baseline']
        require(set(state['ideas']) >= set(baseline['ideas']), 'Ideas cannot be removed by a normal commit', 'corrupt_store')
        for key, prior in baseline['ideas'].items():
            current = state['ideas'][key]
            require(current['origin'] == prior['origin'] and current['revisions'][:prior['revision']] == prior['revisions'],
                    'Immutable origin/history cannot change', 'corrupt_store')
            for field in ('proposals', 'plans', 'executions'):
                require(current[field][:len(prior[field])] == prior[field], 'Immutable '+field+' metadata cannot change', 'corrupt_store')
        # Ignore caller counter changes; Store owns this persistence generation.
        state['transaction_revision'] = baseline['transaction_revision']
        validate(state)
        changed = state != baseline or proposal_append is not None or bool(asset_append) or handoff_append is not None
        if not changed and (context['kind'] == 'markdown' or (context['kind'] == 'legacy' and not initial_zero)):
            self._check_cas(context)
            return context['files'], {}, False
        if changed or not initial_zero:
            state['transaction_revision'] += 1
        md = _markdown()
        verified_proposals = {}
        verified_assets = {}
        verified_handoffs = {}
        for path, document in context['docs'].items():
            if _IDEA_FILE.fullmatch(path):
                for link in md.agent_proposal_links(document.metadata['extensions'], document.metadata['idea']['idea_id']):
                    verified_proposals[link['path']] = context['files'][link['path']]
                for link in md.asset_links(document.metadata['extensions'], document.metadata['idea']['idea_id']):
                    verified_assets[link['path']] = context['files'][link['path']]
                for link in md.handoff_links(document.metadata['extensions'], document.metadata['idea']['idea_id']):
                    verified_handoffs[link['path']] = context['files'][link['path']]
        extensions = None
        if proposal_append is not None:
            require(context['kind'] == 'markdown', 'Migrate before publishing agent evidence', 'migration_required')
            key, link = proposal_append
            require(key in state['ideas'] and key+'.md' in context['docs'], 'Unknown proposal idea', 'not_found')
            ext = copy.deepcopy(context['docs'][key+'.md'].metadata['extensions'])
            links = md.agent_proposal_links(ext,key)
            ext[md.AGENT_PROPOSAL_EXTENSION] = links+[copy.deepcopy(link)]
            extensions = {key:ext}
            require(type(proposal_evidence) is dict and set(proposal_evidence) == {link['path']},
                    'New proposal requires exactly its immutable bytes')
            require(link['path'] not in verified_proposals, 'Proposal evidence already exists', 'save_conflict')
            verified_proposals.update(proposal_evidence)
        else:
            require(proposal_evidence is None, 'Evidence bytes require an append')
        if asset_append:
            require(context['kind'] == 'markdown', 'Migrate before publishing asset evidence', 'migration_required')
            extensions = {} if extensions is None else extensions
            appended = set()
            for key, new_links in asset_append.items():
                require(key in state['ideas'] and key+'.md' in context['docs'], 'Unknown asset idea', 'not_found')
                ext = extensions.setdefault(key, copy.deepcopy(context['docs'][key+'.md'].metadata['extensions']))
                ext[md.ASSET_EXTENSION] = md.asset_links(ext,key)+copy.deepcopy(new_links)
                md.asset_links(ext,key)
                appended.update(link['path'] for link in new_links)
            require(type(asset_evidence) is dict and set(asset_evidence) == appended,
                    'Asset appends require exactly their immutable bytes')
            require(not set(asset_evidence) & set(verified_assets), 'Asset evidence already exists', 'save_conflict')
            verified_assets.update(asset_evidence)
        else:
            require(asset_evidence is None, 'Asset evidence requires an append')
        if handoff_append is not None:
            require(context['kind'] == 'markdown', 'Migrate before publishing handoff evidence', 'migration_required')
            key, link = handoff_append
            require(key in state['ideas'] and key+'.md' in context['docs'], 'Unknown handoff idea', 'not_found')
            extensions = {} if extensions is None else extensions
            ext = extensions.setdefault(key,copy.deepcopy(context['docs'][key+'.md'].metadata['extensions']))
            ext[md.HANDOFF_EXTENSION] = md.handoff_links(ext,key)+[copy.deepcopy(link)]
            md.handoff_links(ext,key)
            require(type(handoff_evidence) is dict and set(handoff_evidence) == {link['path']},
                    'New handoff requires exactly its immutable bytes')
            require(link['path'] not in verified_handoffs, 'Handoff evidence already exists', 'save_conflict')
            verified_handoffs.update(handoff_evidence)
        else:
            require(handoff_evidence is None, 'Handoff evidence requires an append')
        after = md.encode_state(state, previous=context['docs'], previous_state=baseline,
                                extensions=extensions,proposal_evidence=verified_proposals,asset_evidence=verified_assets,
                                handoff_evidence=verified_handoffs)
        require(len(after) <= MAX_STORE_FILES and sum(len(raw) for raw in after.values()) <= MAX_STORE_BYTES,
                'Store after-images exceed file/byte limits', 'too_large')
        # Fresh physical inventory plus new immutable evidence, before publish.
        asset_paths = self._asset_files()
        physical = set().union(*asset_paths.values())
        require(len(physical | set(asset_evidence or {})) <= MAX_STORE_FILES,
                'Asset inventory after-images exceed file limit', 'too_large')
        if 'IDEAS.md' in context['files']:
            before_index = context['docs']['IDEAS.md']
            after_index = md.decode_index(after['IDEAS.md'])
            before_fields = {k:v for k,v in before_index.metadata.items() if k != 'transaction_revision'}
            after_fields = {k:v for k,v in after_index.metadata.items() if k != 'transaction_revision'}
            if before_fields == after_fields and before_index.body == after_index.body:
                after['IDEAS.md'] = context['files']['IDEAS.md']
        changes = {path:raw for path,raw in after.items() if context['files'].get(path) != raw}
        self._check_cas(context)
        if not changes:
            state['transaction_revision'] = baseline['transaction_revision']
            return context['files'], {}, False
        return after, changes, changed

    def _publish_commit(self, state, context, after, changes, extra=None, extra_expected=None):
        self._check_cas(context)
        if context['kind'] == 'legacy' and (changes or extra):
            import idea_migration as migration
            observed = self._legacy_views(context)
            prepared = migration.prepare(context['legacy_raw'], context['baseline'], state, observed,
                                         actor=self.observer, timestamp=now())
            for relative, raw in observed.items():
                require(transactions.file_hash(self.path, relative) == digest(raw),
                        'Observed legacy evidence changed before migration: '+relative, 'save_conflict')
            after = prepared['after']
            combined = dict(prepared['changes'], **(extra or {}))
            expected = dict(prepared['expected'], **(extra_expected or {}))
            result = transactions.publish(self.path, combined, expected, freeze_legacy=True,
                                          legacy_sha256=prepared['legacy_sha256'])
            migration_evidence = {path:combined[path] for path in (transactions.FROZEN, transactions.RECEIPT)}
        else:
            combined = dict(changes, **(extra or {}))
            if not combined:
                return None
            expected = {path:None if path not in context['files'] else digest(context['files'][path]) for path in changes}
            expected.update(extra_expected or {})
            result = transactions.publish(self.path, combined, expected)
            migration_evidence = context.get('migration_evidence', {})
        result.raise_for_error()
        initialize_marker(context['lock']).raise_for_error()
        md = _markdown()
        context.update(kind='markdown', baseline=copy.deepcopy(state), files=after,
                       docs={path:md.parse_document(raw) for path,raw in after.items() if path == 'IDEAS.md' or _IDEA_FILE.fullmatch(path)})
        context['migration_evidence'] = migration_evidence
        # Purely reconstruct after-images already checked at the boundary.
        # Existing/staged blob witnesses remain verified; this adds no I/O.
        new_entries = self._asset_entries(state, after, context['docs'])
        witnesses = {entry['record']['asset_id']:entry['blob'] for entries in context.get('asset_entries', {}).values()
                     for entry in entries if entry['blob'] is not None}
        for entries in new_entries.values():
            for entry in entries:
                if entry['record']['kind'] == 'asset':
                    entry['blob'] = witnesses[entry['record']['asset_id']]
        context['asset_entries'] = new_entries
        context.setdefault('asset_paths', {name:set() for name in ('evidence','blobs','staging')})['evidence'].update(
            path for path in after if path.startswith('assets/evidence/'))
        for relative, raw in (extra or {}).items():
            if relative in context.get('proposal_receipts', {}):
                context['proposal_receipts'][relative] = raw
        return result

    def commit(self, state):
        """Existing CLI/application contract; requests use the atomic receipt seam."""
        context = getattr(self._contexts, 'active', None)
        require(context is not None and context['write'], 'Commit requires a writable Store transaction', 'invalid_transaction')
        require(not context.get('request_running'), 'Request mutators cannot publish or perform I/O', 'invalid_transaction')
        after, changes, _ = self._prepare_commit(state, context)
        return self._publish_commit(state, context, after, changes)

    def _read_receipts(self, session_id):
        relative = _request_ids(session_id)
        path = self._safe(relative)
        require(path.exists(), 'Initialized receipt session is missing; create a fresh session', 'receipt_session_missing')
        try:
            raw = read_bytes(path, MAX_RECEIPT_BYTES)
            record = _receipt_record(decode(raw),session_id)
        except (IdeaError, KeyError, TypeError, ValueError, RecursionError) as exc:
            raise IdeaError('corrupt_receipts', 'Malformed initialized receipt session: '+session_id) from exc
        return relative, raw, copy.deepcopy(record)

    def create_session(self):
        """Create fresh durable recovery metadata; never recreate a supplied ID.

        Service session-open binds this ID to its separate private credentials.
        Lost session files fail closed, including after a process/service restart.
        """
        with self.transaction(write=True) as state:
            context = self._contexts.active
            require(len(self._entries('session-recovery')) < MAX_STORE_FILES, 'Session recovery directory is full', 'receipt_capacity_exhausted')
            session_id = 'session_'+uuid.uuid4().hex
            relative = _request_ids(session_id)
            require(not self._safe(relative).exists(), 'Generated session already exists', 'save_conflict')
            raw = encoded(dict(schema_version=1,session_id=session_id,receipts={}))
            after, changes, _ = self._prepare_commit(state,context,initial_zero=True)
            self._publish_commit(state,context,after,changes,{relative:raw},{relative:None})
            return session_id

    def request_result(self, session_id, request_id, payload_digest=None):
        """Reconcile a historical successful mutation under the same store lock."""
        _request_ids(session_id,request_id)
        require(payload_digest is None or (type(payload_digest) is str and re.fullmatch(r'[0-9a-f]{64}',payload_digest) is not None), 'Invalid payload digest')
        with self.transaction():
            _, _, record = self._read_receipts(session_id)
            require(request_id in record['receipts'], 'Unknown request result', 'request_not_found')
            receipt = record['receipts'][request_id]
            require(payload_digest is None or payload_digest == receipt['payload_sha256'], 'Request ID was used for a different payload', 'request_conflict')
            return copy.deepcopy(receipt['result'])

    def mutate(self, session_id, request_id, validated_payload, mutator):
        """One pure/state-only internal mutation + result in one recoverable journal.

        The callable is never browser supplied. Schema/operation authorization
        and revision CAS live in the trusted service/reducer; replay occurs first.
        A callback may only edit its detached state and return bounded JSON.
        Oversized results abort before publication, so external effects are banned.
        """
        return self._mutate_request(session_id,request_id,validated_payload,mutator)

    def mutate_assets(self, session_id, request_id, validated_payload, mutator, *, prepare_records=None):
        """Atomic pure decision + immutable asset appends + existing receipt.

        After replay, optional trusted prepare_records(state) returns new records
        without changing state. Store verifies identity/capacity/blob witnesses
        at its local I/O boundary, then exposes prepared records in memory to
        mutator(state). The ordinary Visualize handler can therefore validate a
        new set before the same transaction publishes its accepted pointer.
        Neither callback may wait/read network, read files or publish. The business
        callback returns the existing bounded result dict, never raw record bytes.
        No factory means existing-set acceptance without appending evidence.
        """
        require(prepare_records is None or callable(prepare_records), 'Trusted record factory is required')
        return self._mutate_request(session_id,request_id,validated_payload,mutator,
                                    asset_mode=True,prepare_records=prepare_records)

    def _stage_assets(self, state, context, records, session_id):
        from idea_asset_evidence import validate_record, encode_record, record_link, record_id
        require(type(records) is list and len(records) <= _markdown().MAX_ASSET_LINKS,
                'New asset record list exceeds capacity', 'too_large')
        if not records:
            return {}, {}, []
        require(context['kind'] == 'markdown', 'Migrate before publishing asset evidence', 'migration_required')
        staged = copy.deepcopy(context.get('asset_entries', {}))
        appended, evidence, links, total = {}, {}, [], 0
        known_ids = {record_id(entry['record']) for entries in staged.values() for entry in entries}
        for value in records:
            record = validate_record(value); key = record['idea_id']
            rid = record_id(record)
            require(rid not in known_ids, 'Asset record identity already exists', 'request_conflict')
            known_ids.add(rid)
            require(key in state['ideas'] and key+'.md' in context['docs'], 'Unknown asset idea', 'not_found')
            require(record['session_id'] == session_id, 'Asset belongs to another receipt session', 'request_conflict')
            raw = encode_record(record); link = record_link(record, raw)
            total += len(raw)
            require(total <= _markdown().MAX_ASSET_EVIDENCE_BYTES, 'New asset evidence exceeds byte limit', 'too_large')
            staged.setdefault(key, []).append(dict(record=record, evidence=link, blob=None))
            require(len(staged[key]) <= _markdown().MAX_ASSET_LINKS, 'Asset link capacity exhausted', 'too_large')
            appended.setdefault(key, []).append(link)
            evidence[link['path']] = raw; links.append(link)
        self._asset_graph(state, staged)
        # Existing witnesses were checked at transaction read. Candidate blobs
        # are verified here, after replay/factory and before pure business logic.
        for entries in staged.values():
            for entry in entries:
                if entry['record']['kind'] == 'asset' and entry['blob'] is None:
                    entry['blob'] = self._asset_blob(entry['record'])
        context['asset_entries'] = staged
        context['retained_staging'] = self._retained_asset_stages(context['asset_paths'],staged)
        return appended, evidence, links

    def _mutate_request(self, session_id, request_id, validated_payload, mutator, *, asset_mode=False, prepare_records=None):
        _request_ids(session_id,request_id)
        require(not request_id.startswith(AGENT_RESPONSE_PREFIX), 'Agent response receipt namespace is reserved')
        payload_hash = digest(_request_json(validated_payload))
        require(callable(mutator), 'A trusted internal mutation callable is required')
        with self.transaction(write=True) as state:
            context = self._contexts.active
            relative, before, record = self._read_receipts(session_id)
            records = record['receipts']
            if request_id in records:
                previous = records[request_id]
                require(previous['payload_sha256'] == payload_hash, 'Request ID was used for a different payload', 'request_conflict')
                return copy.deepcopy(previous['result'])
            require(len(records) < MAX_SESSION_RECEIPTS and len(before) < MAX_RECEIPT_BYTES,
                    'Receipt capacity exhausted; create a fresh session', 'receipt_capacity_exhausted')
            appended, asset_bytes, asset_links = {}, {}, []
            if prepare_records is not None:
                unchanged = copy.deepcopy(state)
                context['request_running'] = True
                try:
                    candidates = prepare_records(state)
                finally:
                    context['request_running'] = False
                require(state == unchanged, 'Asset record factory must not mutate state', 'invalid_handler')
                appended, asset_bytes, asset_links = self._stage_assets(state,context,candidates,session_id)
            context['request_running'] = True
            try:
                business = mutator(state)
            finally:
                context['request_running'] = False
            _request_json(business)
            if appended:
                require(len(appended) == 1 and type(business.get('idea_id')) is str
                        and business['idea_id'] in appended,
                        'Asset append receipt must name exactly its single idea', 'invalid_handler')
            # Accepted-state validity remains the pure business reducer's job;
            # graph validity also guards factory records after its state changes.
            if asset_mode:
                self._asset_graph(state, context.get('asset_entries', {}))
            after, changes, changed = self._prepare_commit(state,context,initial_zero=True,
                asset_append=appended or None,asset_evidence=asset_bytes if appended else None)
            result = copy.deepcopy(business)
            metadata = {'ok':True,'code':'ok','request_id':request_id,'write_state':'applied' if changed else 'no_op',
                        'backlog_revision':state['backlog_revision']}
            if asset_mode:
                metadata['asset_records'] = copy.deepcopy(asset_links)
            for key,value in metadata.items():
                require(key not in result or (type(result[key]) is type(value) and result[key] == value), 'Mutation returned conflicting result metadata: '+key)
                result[key] = value
            idea_id = result.get('idea_id')
            if idea_id is not None:
                check_id(idea_id)
                require(idea_id in state['ideas'], 'Result links an unknown idea')
                idea = state['ideas'][idea_id]
                counters = {'revision':idea['revision']}
                if 'workflow' in idea:
                    counters['draft_version'] = idea['workflow']['draft_version']
                for key,value in counters.items():
                    require(key not in result or (type(result[key]) is int and result[key] == value), 'Mutation result counter mismatch: '+key)
                    result[key] = value
            _request_json(result)
            _result_counters(result)
            records[request_id] = dict(payload_sha256=payload_hash,result=result)
            _receipt_record(record,session_id)
            raw = encoded(record)
            require(len(raw) <= MAX_RECEIPT_BYTES, 'Receipt capacity exhausted; create a fresh session', 'receipt_capacity_exhausted')
            self._publish_commit(state,context,after,changes,{relative:raw},{relative:digest(before)})
            return copy.deepcopy(result)

    def asset_records(self, state, idea_id, *, with_links=False):
        """Detached verified records, including prepared records, with no I/O."""
        context = getattr(self._contexts, 'active', None)
        require(context is not None and state is context.get('state'),
                'Asset inspection requires the active transaction', 'invalid_transaction')
        require(type(with_links) is bool, 'Asset link option must be boolean')
        check_id(idea_id)
        require(idea_id in state['ideas'], 'Unknown asset idea', 'not_found')
        entries = context.get('asset_entries', {}).get(idea_id, [])
        return copy.deepcopy(entries if with_links else [entry['record'] for entry in entries])

    def asset_inventory(self, state, idea_id):
        """Memory-only records plus full-count/bounded-ID store orphan diagnostics."""
        entries = self.asset_records(state,idea_id,with_links=True)
        context = self._contexts.active
        paths = context.get('asset_paths', {name:set() for name in ('evidence','blobs','staging')})
        return dict(records=entries, orphans=self._asset_orphans(
            paths,context.get('asset_entries', {}),context.get('retained_staging', ())))

    def agent_proposals(self, state, idea_id, *, with_links=False):
        """Detached verified suggestions in the existing transaction.

        This is a memory-only accessor, including inside acceptance mutators.
        The transaction read verified links/bytes/receipts; no nested lock,
        filesystem read, source eligibility check or publication occurs here.
        with_links retains the exact verified relative Markdown path/hash witness.
        """
        context = getattr(self._contexts, 'active', None)
        require(context is not None and state is context.get('state'),
                'Proposal inspection requires the active transaction', 'invalid_transaction')
        require(type(with_links) is bool, 'Proposal link option must be boolean')
        check_id(idea_id)
        require(idea_id in state['ideas'], 'Unknown proposal idea', 'not_found')
        if context['kind'] != 'markdown':
            return []
        from idea_proposal_evidence import decode_proposal
        document = context['docs'].get(idea_id+'.md')
        # An unpublished new idea has no persisted evidence yet.
        if document is None:
            return []
        records = []
        for link in _markdown().agent_proposal_links(document.metadata['extensions'], idea_id):
            require(link['path'] in context['files'], 'Missing verified proposal bytes', 'corrupt_store')
            record = decode_proposal(context['files'][link['path']], path=link['path'],
                                     expected_idea_id=idea_id, link=link)
            records.append(dict(record=record, evidence={name:link[name] for name in ('path','sha256')})
                           if with_links else record)
        return copy.deepcopy(records)

    def handoffs(self, state, idea_id, *, with_links=False):
        """Detached historical packets verified by the active transaction read.

        Memory only: no filesystem read, nested lock, publication or currentness
        assertion. Evidence keeps the confined relative link, including packet ID.
        """
        context = getattr(self._contexts, 'active', None)
        require(context is not None and state is context.get('state'),
                'Handoff inspection requires the active transaction', 'invalid_transaction')
        require(type(with_links) is bool, 'Handoff link option must be boolean')
        check_id(idea_id)
        require(idea_id in state['ideas'], 'Unknown handoff idea', 'not_found')
        if context['kind'] != 'markdown':
            return []
        document = context['docs'].get(idea_id+'.md')
        if document is None:
            return []
        from idea_handoff_evidence import decode_record
        records = []
        for link in _markdown().handoff_links(document.metadata['extensions'], idea_id):
            packet = decode_record(context['files'][link['path']],path=link['path'],
                                   expected_idea_id=idea_id,link=link)
            records.append(dict(record=packet,evidence=copy.deepcopy(link)) if with_links else packet)
        return copy.deepcopy(records)

    def handoff_observations(self, state, idea_id):
        """Trusted current source observations for publication and managed CLI.

        Unlike historical handoffs(), this checks current shared eligibility and
        the existing canonical workspace directory. Blob bytes were verified by
        transaction read and are rehashed by commit CAS. No user path is returned
        as a Store object: document/blob paths are generated from verified IDs.
        Never call this I/O boundary from a pure factory or request mutator.
        """
        from idea_handoff_evidence import eligible_source
        context = getattr(self._contexts,'active',None)
        require(context is not None and state is context.get('state') and not context.get('request_running'),
                'Handoff observations require the active transaction boundary', 'invalid_transaction')
        check_id(idea_id)
        require(idea_id in state['ideas'], 'Unknown handoff idea', 'not_found')
        idea = state['ideas'][idea_id]
        visual = idea.get('workflow',{}).get('steps',{}).get('visualize',{}).get('fields')
        design = None
        if visual is not None and visual['disposition'] == 'accepted_set':
            records = self.asset_records(state,idea_id)
            selected = next((record for record in records if record['kind'] == 'design-set'
                             and record['set_id'] == visual['design_set_id']),None)
            require(selected is not None, 'Accepted design set is missing', 'not_ready')
            design = dict(set_id=selected['set_id'],members=[dict(member,
                path=str(self._safe('assets/blobs/'+member['asset_id']+'.bin').resolve(strict=True))) for member in selected['members']])
        eligible_source(state,idea,design)
        workspace = Path(idea['workflow']['steps']['capture']['fields']['workspace']['path'])
        try:
            require(workspace.is_absolute() and workspace.resolve(strict=True) == workspace
                    and stat.S_ISDIR(workspace.lstat().st_mode) and not workspace.is_symlink(),
                    'Confirmed workspace is no longer a canonical directory', 'workspace_unavailable')
        except (OSError,RuntimeError) as exc:
            raise IdeaError('workspace_unavailable','Confirmed workspace is unavailable on the service host') from exc
        key = idea_id; revision = idea['revision']
        source_files = {}
        for name,relative in (('detail',key+'.md'),('index','IDEAS.md'),
                              ('revision','history/'+key+'/r'+str(revision)+'.md')):
            require(relative in context['files'], 'Handoff source is not persisted', 'not_ready')
            # Preserve original Store spelling for safety checks; canonicalize
            # only generated observations after root/child symlink checks.
            source_files[name] = dict(path=str(self._safe(relative).resolve(strict=True)),sha256=digest(context['files'][relative]))
        return copy.deepcopy(dict(source_files=source_files,design_set=design))

    def _handoff_http_preflight(self, packet, link, result):
        """Bound the full real HTTP representation before any publication."""
        from idea_handoff_evidence import render_prompt, recorded_packet_path
        absolute = recorded_packet_path(packet,link)
        handoff = dict(handoff_id=packet['handoff_id'],source_revision=packet['source_revision'],
            source_digest=packet['source_digest'],path=absolute,sha256=link['sha256'],
            prompt=render_prompt(packet,absolute),source_files=packet['source_files'],design_set=packet['design_set'])
        try:
            _request_json(dict(result,handoff=handoff,handoff_current=True))
            _request_json(dict(result,code='historical_handoff',handoff=handoff,handoff_current=False))
        except IdeaError as exc:
            if exc.code == 'too_large':
                raise IdeaError('handoff_capacity','Full handoff response exceeds the 1 MiB limit') from exc
            raise

    def publish_handoff(self, session_id, request_id, validated_payload, build_packet):
        """Packet/link/compact receipt in one existing recoverable writer.

        validated_payload is Service's exact {operation:'handoff',payload:...}
        envelope. Factory signature equals codec.build_record: detached state,
        detached idea, keyword source_files/design_set/generated identities,
        OS-bound actor and timestamp. It is pure; no I/O/locks/publication/waits.
        Return the immutable compact receipt, never a currentness assertion.
        Replay comes before current eligibility, workspace checks and new CAS.
        """
        from idea_handoff_evidence import (build_record, encode_record, eligible_source,
            validate_record, record_link, verify_current_paths)
        _request_ids(session_id,request_id)
        require(not request_id.startswith(AGENT_RESPONSE_PREFIX), 'Agent response receipt namespace is reserved')
        payload_hash = digest(_request_json(validated_payload))
        require(callable(build_packet), 'Trusted handoff factory is required')
        require(set(validated_payload) == {'operation','payload'} and validated_payload['operation'] == 'handoff',
                'Unexpected handoff envelope')
        payload = validated_payload['payload']
        require(type(payload) is dict and set(payload) == {'request_id','idea_id','expected_revision',
                'expected_draft_version','expected_backlog_revision'} and payload['request_id'] == request_id,
                'Unexpected handoff request fields')
        check_id(payload['idea_id'])
        for name in ('expected_revision','expected_draft_version','expected_backlog_revision'):
            integer(payload[name],name,1 if name == 'expected_revision' else 0)
        with self.transaction(write=True) as state:
            context = self._contexts.active
            relative,before,session = self._read_receipts(session_id)
            receipts = session['receipts']
            if request_id in receipts:
                prior = receipts[request_id]
                require(prior['payload_sha256'] == payload_hash, 'Request ID was used for a different payload', 'request_conflict')
                return copy.deepcopy(prior['result'])
            require(len(receipts) < MAX_SESSION_RECEIPTS and len(before) < MAX_RECEIPT_BYTES,
                    'Receipt capacity exhausted; create a fresh session', 'receipt_capacity_exhausted')
            key = payload['idea_id']
            require(key in state['ideas'], 'Unknown handoff idea', 'not_found')
            idea = state['ideas'][key]
            require(payload['expected_revision'] == idea['revision'], 'Stale handoff revision', 'stale_revision')
            require(payload['expected_draft_version'] == idea.get('workflow',{}).get('draft_version'),
                    'Stale handoff draft version', 'stale_draft_version')
            require(payload['expected_backlog_revision'] == state['backlog_revision'], 'Stale handoff backlog', 'stale_backlog')
            observations = self.handoff_observations(state,key)
            source = eligible_source(state,idea,observations['design_set'])
            entries = self.handoffs(state,key,with_links=True)
            chosen = None
            for n,entry in reversed(list(enumerate(entries,1))):
                if (entry['record']['source_revision'] != idea['revision'] or
                    entry['record']['source_digest'] != source['source_digest']):
                    continue
                try:
                    verify_current_paths(entry['record'],observations['source_files'],observations['design_set'])
                except IdeaError as exc:
                    if exc.code != 'stale_source': raise
                    continue
                chosen = n,entry
                break
            append, raw_map = None, None
            if chosen is not None:
                ordinal,entry = chosen; packet,link = entry['record'],entry['evidence']
            else:
                require(len(entries) < _markdown().MAX_HANDOFF_LINKS, 'Handoff link capacity exhausted', 'handoff_capacity')
                args = dict(observations,handoff_id='handoff_'+uuid.uuid4().hex,session_id=session_id,
                            request_id=request_id,actor=self.observer,timestamp=now())
                detached = copy.deepcopy(state); detached_idea = detached['ideas'][key]
                untouched = copy.deepcopy(detached)
                context['request_running'] = True
                try:
                    packet = validate_record(build_packet(detached,detached_idea,**copy.deepcopy(args)))
                finally:
                    context['request_running'] = False
                require(detached == untouched and state == context['baseline'], 'Handoff factory must not mutate state', 'invalid_handler')
                require(packet == build_record(state,idea,**args), 'Handoff factory changed trusted source observations', 'invalid_handler')
                raw = encode_record(packet); link = record_link(packet,raw)
                ordinal = len(entries)+1; append = (key,link); raw_map = {link['path']:raw}
            result = dict(ok=True,code='ok',request_id=request_id,write_state='applied' if append else 'no_op',
                idea_id=key,revision=idea['revision'],draft_version=idea['workflow']['draft_version'],
                backlog_revision=state['backlog_revision'],handoff_index=ordinal,**link)
            self._handoff_http_preflight(packet,link,result)
            receipts[request_id] = dict(payload_sha256=payload_hash,result=copy.deepcopy(result))
            _receipt_record(session,session_id); receipt_raw = encoded(session)
            require(len(receipt_raw) <= MAX_RECEIPT_BYTES, 'Receipt capacity exhausted; create a fresh session', 'receipt_capacity_exhausted')
            after,changes,_ = self._prepare_commit(state,context,initial_zero=True,
                handoff_append=append,handoff_evidence=raw_map)
            witnessed = dict(context.get('proposal_receipts',{}),**{relative:receipt_raw})
            require(len(after)+len(witnessed) <= MAX_STORE_FILES
                    and sum(map(len,after.values()))+sum(map(len,witnessed.values())) <= MAX_STORE_BYTES,
                    'Store and linked receipt after-images exceed capacity', 'too_large')
            context.setdefault('proposal_receipts',{})[relative] = before
            self._publish_commit(state,context,after,changes,{relative:receipt_raw},{relative:digest(before)})
            return copy.deepcopy(result)

    def persist_agent_proposal(self, state, evidence, *, actor, validate_current):
        """Publish suggestion/link/receipt under an existing binding->Store guard.

        Never opens a Store transaction or accepts an idea. validate_current is
        trusted pure code, returning the exact checked source on first publish;
        replay precedes it. Generated metadata is excluded from replay hashing.
        """
        from idea_proposal_evidence import encode_proposal, proposal_link
        context = getattr(self._contexts,'active',None)
        require(context is not None and context['write'] and not context.get('request_running')
                and state is context.get('state') and state == context['baseline'],
                'Agent evidence requires the active unchanged writable transaction', 'invalid_transaction')
        require(context['kind'] == 'markdown', 'Migrate before publishing agent evidence', 'migration_required')
        require(callable(validate_current), 'Trusted proposal source validator is required')
        payload = _agent_payload(evidence,actor)
        evidence = payload['evidence']
        payload_hash = digest(_request_json(payload))
        rid = _agent_receipt_id(evidence)
        sid = evidence['correlation']['session_id']
        relative, before, session = self._read_receipts(sid)
        records = session['receipts']
        if rid in records:
            previous = records[rid]
            require(previous['payload_sha256'] == payload_hash, 'Agent reply conflicts with recorded response', 'request_conflict')
            result = previous['result']
            require(set(result) == {'ok','code','request_id','write_state','idea_id','revision',
                    'draft_version','backlog_revision','proposal_index','proposal_id','path','sha256'},
                    'Malformed recorded agent response result', 'corrupt_store')
            link = {name:result[name] for name in ('proposal_id','path','sha256')}
            # Every link is verified against its receipt during transaction read.
            links = _markdown().agent_proposal_links(context['docs'][evidence['correlation']['idea_id']+'.md'].metadata['extensions'],
                                                       evidence['correlation']['idea_id'])
            require(link in links, 'Recorded agent response lacks its linked evidence', 'corrupt_store')
            return copy.deepcopy(link)
        require(len(records) < MAX_SESSION_RECEIPTS and len(before) < MAX_RECEIPT_BYTES,
                'Receipt capacity exhausted; create a fresh session', 'receipt_capacity_exhausted')
        correlation = evidence['correlation']; key = correlation['idea_id']
        require(key in state['ideas'], 'Unknown proposal idea', 'not_found')
        idea = state['ideas'][key]
        require(correlation['accepted_revision'] == idea['revision'], 'Stale proposal idea revision', 'stale_revision')
        require(correlation['draft_version'] == idea.get('workflow',{}).get('draft_version',0),
                'Stale proposal draft version', 'stale_draft_version')
        checked_state = copy.deepcopy(state)
        context['request_running'] = True
        try:
            checked_source = validate_current(checked_state,copy.deepcopy(evidence))
        finally:
            context['request_running'] = False
        require(checked_state == state and state == context['baseline'], 'Proposal source validation must not mutate state', 'invalid_handler')
        require(_request_json(checked_source) == _request_json(evidence['source']), 'Proposal source changed', 'stale_source')
        record = _agent_record(evidence,actor,'proposal_'+uuid.uuid4().hex,now())
        raw = encode_proposal(record); link = proposal_link(record,raw)
        ordinal = len(_markdown().agent_proposal_links(context['docs'][key+'.md'].metadata['extensions'],key))+1
        result = dict(ok=True,code='ok',request_id=rid,write_state='applied',idea_id=key,
            revision=correlation['accepted_revision'],draft_version=correlation['draft_version'],
            backlog_revision=state['backlog_revision'],proposal_index=ordinal,**link)
        records[rid] = dict(payload_sha256=payload_hash,result=result)
        _receipt_record(session,sid)
        receipt_raw = encoded(session)
        require(len(receipt_raw) <= MAX_RECEIPT_BYTES, 'Receipt capacity exhausted; create a fresh session', 'receipt_capacity_exhausted')
        after, changes, _ = self._prepare_commit(state,context,proposal_append=(key,link),proposal_evidence={link['path']:raw})
        context.setdefault('proposal_receipts', {})[relative] = before
        self._publish_commit(state,context,after,changes,{relative:receipt_raw},{relative:digest(before)})
        return copy.deepcopy(link)

    def view_issues(self, state, repair=False):
        issues = []
        views = [(self.path/'archive'/key, encoded(snapshot)) for key,snapshot in state['archives'].items()]
        for idea in state['ideas'].values():
            for plan in idea['plans']:
                expected_path = self.path/'plan-evidence'/(plan['plan_id']+'.md')
                if str(expected_path) != plan['path']:
                    issues.append('Plan evidence path does not match store: '+plan['path'])
                    continue
                views.append((expected_path, plan['content'].encode('utf-8')))
        for path, expected in views:
            relative = path.relative_to(self.path).as_posix()
            try:
                self._safe(relative)
                if path.exists():
                    if read_bytes(path, MAX_STATE) != expected:
                        issues.append('Immutable archive differs: '+str(path))
                elif repair:
                    atomic_write(path, expected, immutable=True)
                else:
                    issues.append('Missing archive: '+str(path))
            except (OSError, IdeaError) as exc:
                issues.append(str(path)+': '+str(exc))
        return issues

    def artifact_issues(self, state):
        issues = []
        for idea in state['ideas'].values():
            for entry in idea['plans']+idea['executions']:
                try:
                    raw = read_bytes(entry['path'])
                    require(digest(raw) == entry['sha256'], 'Hash differs: '+entry['path'], 'changed_artifact')
                    if 'receipt' in entry:
                        require(decode(raw) == entry['receipt'], 'Receipt content differs: '+entry['path'])
                except (OSError, IdeaError) as exc:
                    issues.append(str(exc))
        return issues
