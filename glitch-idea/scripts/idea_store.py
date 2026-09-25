"""Single-file transactions with Linux flock and recoverable immutable views."""
from contextlib import contextmanager
import copy
import fcntl
import os
from pathlib import Path
import re
import tempfile

from idea_domain import (IdeaError, MAX_INPUT, MAX_STATE, ASSESS_KEYS, assessment,
    check_id, decode, digest, encoded, integer, receipt, require, shape, text)


def read_bytes(path, limit=MAX_INPUT):
    path=Path(path)
    require(path.is_file(),'Not a regular file: '+str(path),'missing_artifact')
    with path.open('rb') as stream:
        raw=stream.read(limit+1)
    require(len(raw)<=limit,'File exceeds size limit: '+str(path),'too_large')
    return raw


def fsync_dir(path):
    descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(path,raw,immutable=False):
    path.parent.mkdir(parents=True,exist_ok=True)
    descriptor,temp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    replaced=False
    try:
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            os.link(temp,path)
        else:
            os.replace(temp,path)
            replaced=True
        fsync_dir(path.parent)
    except OSError as exc:
        if replaced:
            raise IdeaError('durability_uncertain','State was replaced but directory sync failed: '+str(exc),committed=True) from exc
        raise
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


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
        raise IdeaError('corrupt_store','Invalid state.json: '+str(exc)) from exc


class Store:
    def __init__(self,path):
        self.path=Path(path).expanduser().resolve()
        self.state_path=self.path/'state.json'

    @contextmanager
    def transaction(self,write=False):
        if not self.path.exists() and not write:
            yield empty_state()
            return
        self.path.mkdir(parents=True,exist_ok=True)
        lock_path=self.path/'.lock'
        with lock_path.open('a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX if write else fcntl.LOCK_SH)
            self.lock=lock
            if self.state_path.exists():
                try:
                    state=decode(read_bytes(self.state_path,MAX_STATE))
                except IdeaError as exc:
                    raise IdeaError('corrupt_store','Cannot read state.json: '+str(exc)) from exc
                validate(state)
            else:
                lock.seek(0)
                require(not lock.read(64),'state.json is missing from an initialized store; refusing to reset','corrupt_store')
                require(not any(p.name!='.lock' for p in self.path.iterdir()),'state.json is missing from a nonempty store; refusing to reset','corrupt_store')
                state=empty_state()
            yield state

    def commit(self,state):
        state['transaction_revision']+=1
        validate(state)
        raw=encoded(state)
        require(len(raw)<=MAX_STATE,'State exceeds 64 MiB; no change committed','too_large')
        atomic_write(self.state_path,raw)
        try:
            self.lock.seek(0)
            if not self.lock.read(64):
                self.lock.write(b'initialized\n')
                self.lock.flush()
                os.fsync(self.lock.fileno())
        except OSError as exc:
            raise IdeaError('durability_uncertain','State committed but initialization marker sync failed: '+str(exc),committed=True) from exc

    def view_issues(self,state,repair=False):
        issues=[]
        views=[(self.path/'archive'/key,encoded(snapshot)) for key,snapshot in state['archives'].items()]
        for idea in state['ideas'].values():
            for plan in idea['plans']:
                expected_path=self.path/'plan-evidence'/(plan['plan_id']+'.md')
                if str(expected_path)!=plan['path']:
                    issues.append('Plan evidence path does not match store: '+plan['path'])
                    continue
                views.append((expected_path,plan['content'].encode('utf-8')))
        for path,expected in views:
            try:
                if path.exists():
                    if read_bytes(path,MAX_STATE)!=expected:
                        issues.append('Immutable archive differs: '+str(path))
                elif repair:
                    atomic_write(path,expected,immutable=True)
                else:
                    issues.append('Missing archive: '+str(path))
            except (OSError,IdeaError) as exc:
                issues.append(str(path)+': '+str(exc))
        return issues

    def artifact_issues(self,state):
        issues=[]
        for idea in state['ideas'].values():
            for entry in idea['plans']+idea['executions']:
                try:
                    raw=read_bytes(entry['path'])
                    require(digest(raw)==entry['sha256'],'Hash differs: '+entry['path'],'changed_artifact')
                    if 'receipt' in entry:
                        require(decode(raw)==entry['receipt'],'Receipt content differs: '+entry['path'])
                except (OSError,IdeaError) as exc:
                    issues.append(str(exc))
        return issues
