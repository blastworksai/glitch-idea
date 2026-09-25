#!/usr/bin/env python3
"""GlitchC local idea lifecycle CLI. Python 3.10+, Linux; standard library only."""
import argparse
import copy
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from idea_domain import (IdeaError, MAX_INPUT, assessment, check_id, decode, digest,
    encoded, identity, integer, now, number, ready, receipt, require, shape, snapshot, text)
from idea_store import Store, read_bytes


class Parser(argparse.ArgumentParser):
    def error(self,message):
        raise IdeaError('usage',message)


def parser():
    cli=Parser(description=__doc__)
    cli.add_argument('--store')
    commands=cli.add_subparsers(dest='command',required=True,parser_class=Parser)
    for name in ('capture','list','show','shape','rate','assess','propose','place','handoff','register-plan','record-execution','doctor','repair-views'):
        p=commands.add_parser(name)
        if name not in ('capture','list','doctor','repair-views'):
            p.add_argument('idea_id')
        if name in ('capture','shape','rate','assess','propose','place','register-plan','record-execution'):
            p.add_argument('--actor',required=True)
        if name=='capture':
            p.add_argument('--text-file',required=True)
        if name in ('shape','assess'):
            p.add_argument('--file',required=True)
        if name in ('shape','rate','assess','register-plan'):
            p.add_argument('--expected-revision',required=True,type=int)
        if name=='rate':
            p.add_argument('--urgency',required=True,type=int)
            p.add_argument('--importance',required=True,type=int)
        if name in ('propose','place'):
            p.add_argument('--position',required=True,type=int)
            p.add_argument('--reason',required=True)
            p.add_argument('--expected-backlog-revision',required=True,type=int)
        if name in ('register-plan','record-execution'):
            p.add_argument('--path',required=True)
        if name=='record-execution':
            p.add_argument('--plan-id',required=True)
    return cli


def configuration():
    skill_dir=Path(__file__).resolve().parent.parent
    installed=(skill_dir/'.glitch-idea-install.json').exists()
    require(not installed or (skill_dir/'config.json').is_file(),'Installed skill config.json is missing; restore its durable store and validator configuration','invalid_config')
    candidates=(skill_dir/'config.json',skill_dir.parent/'config.json')
    path=next((p for p in candidates if p.exists()),None)
    value=decode(read_bytes(path)) if path else {}
    require(isinstance(value,dict),'Configuration must be an object','invalid_config')
    require(not installed or (isinstance(value.get('store_path'),str) and bool(value['store_path'].strip())),'Installed configuration must preserve explicit store_path','invalid_config')
    argv=value.get('plan_validator_argv')
    require(argv is None or (isinstance(argv,list) and 0<len(argv)<=32 and all(isinstance(a,str) and a and '\x00' not in a for a in argv)),'plan_validator_argv must be a fixed list of arguments','invalid_config')
    timeout=value.get('validator_timeout_seconds',30)
    number(timeout,'validator_timeout_seconds',1,120)
    base=path.parent if path else skill_dir.parent
    store_path=value.get('store_path',str(base/'ideas'))
    text(store_path,'store_path',4096)
    resolved=Path(store_path).expanduser()
    if not resolved.is_absolute():
        resolved=base/resolved
    return dict(store_path=str(resolved.resolve()),plan_validator_argv=argv,validator_timeout_seconds=timeout)


def resolve_file(path):
    return Path(path).expanduser().resolve()


def read_json(path):
    return decode(read_bytes(resolve_file(path)))


def verify_current(entry):
    raw=read_bytes(entry['path'])
    require(digest(raw)==entry['sha256'],'Evidence changed: '+entry['path'],'changed_artifact')
    return raw


def validate_plan(path,raw,idea,config):
    require(path.suffix.lower() in ('.md','.markdown'),'Plan must be Markdown')
    try:
        content=raw.decode('utf-8')
    except UnicodeError as exc:
        raise IdeaError('invalid_plan','Plan must be UTF-8') from exc
    # Fenced samples and comments are not plan content or a trace declaration.
    content=re.sub(r'<!--.*?-->','',content,flags=re.S)
    lines=[]
    fence=None
    for line in content.splitlines():
        marker=re.match(r'^\s*(`{3,}|~{3,})',line)
        if marker:
            symbol=marker[1][0]
            if fence is None:
                fence=symbol
            elif fence==symbol:
                fence=None
            continue
        if fence is None:
            lines.append(line)
    sections={}
    heading=None
    for line in lines:
        found=re.fullmatch(r'## ([^#].*?)\s*',line)
        if found:
            heading=found[1].strip()
            require(heading not in sections,'Duplicate plan section: '+heading,'invalid_plan')
            sections[heading]=[]
        elif heading is not None:
            sections[heading].append(line)
    trace='\n'.join(sections.get('Idea trace',[])).strip()
    expected='idea_id: '+idea['idea_id']+'\nidea_revision: '+str(idea['revision'])
    require(trace==expected,'Plan needs exact Idea trace block for current idea revision','invalid_plan')
    for names in (('Goal','Outcome','Feature Description'),('Tasks','STEP-BY-STEP TASKS'),('Validation','VALIDATION COMMANDS')):
        body=next(('\n'.join(sections[n]).strip() for n in names if n in sections),'')
        require(bool(re.search(r'[\w]',body)),'Plan needs nonempty '+names[0]+' section','invalid_plan')
    validation=dict(builtin='idea-trace-and-sections-v1',timestamp=now(),external=None)
    argv=config['plan_validator_argv']
    if argv is not None:
        fixed=argv+[str(path)]
        try:
            # File-backed output bounds RAM even if a configured validator is noisy.
            with tempfile.TemporaryFile() as output:
                result=subprocess.run(fixed,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,timeout=config['validator_timeout_seconds'],check=False)
                output.seek(0)
                diagnostic=output.read(16385)
            validation['external']=dict(argv=fixed,returncode=result.returncode,output=diagnostic[:16384].decode('utf-8',errors='replace'),output_truncated=len(diagnostic)>16384)
            require(result.returncode==0,'Configured plan validator rejected plan: '+validation['external']['output'],'validator_failed')
        except subprocess.TimeoutExpired as exc:
            raise IdeaError('validator_timeout','Configured plan validator timed out') from exc
        except OSError as exc:
            raise IdeaError('validator_unavailable','Configured plan validator could not run: '+str(exc)) from exc
    require(read_bytes(path)==raw,'Plan changed during validation','changed_artifact')
    return validation


def run(args,config):
    store=Store(args.store or config['store_path'])
    command=args.command
    write=command not in ('list','show','handoff','doctor')
    if hasattr(args,'actor'):
        text(args.actor,'actor',200)
    if hasattr(args,'idea_id'):
        check_id(args.idea_id)
    with store.transaction(write=write) as state:
        if command=='list':
            return dict(backlog_revision=state['backlog_revision'],order=state['order'],ideas=[state['ideas'][key] for key in state['order']])
        if command in ('doctor','repair-views'):
            issues=store.view_issues(state,repair=command=='repair-views')+store.artifact_issues(state)
            if issues:
                raise IdeaError('unhealthy_store','Store health checks failed',issues=issues)
            return dict(healthy=True,ideas=len(state['ideas']),transaction_revision=state['transaction_revision'])
        if command=='capture':
            raw=read_bytes(resolve_file(args.text_file))
            try:
                original=raw.decode('utf-8')
            except UnicodeError as exc:
                raise IdeaError('invalid_input','Capture text must be UTF-8') from exc
            text(original,'original text',MAX_INPUT)
            idea=dict(idea_id=identity('idea'),revision=1,status='active',origin=dict(text=original,sha256=digest(raw),actor=args.actor,timestamp=now()),shape=None,ratings=None,assessments=[],revisions=[],proposals=[],plans=[],executions=[])
            idea['revisions'].append(snapshot(idea,args.actor,'capture'))
            state['ideas'][idea['idea_id']]=idea
            state['order'].append(idea['idea_id'])
            state['backlog_revision']+=1
            store.commit(state)
            return dict(idea=idea,backlog_revision=state['backlog_revision'])
        require(args.idea_id in state['ideas'],'Unknown idea: '+args.idea_id,'not_found')
        idea=state['ideas'][args.idea_id]
        if command=='show':
            return dict(idea=idea)
        if command=='handoff':
            ready(idea,complete_shape=True)
            require(idea['status']=='active','Shape the next slice before a new handoff','archived_revision')
            return dict(idea_id=idea['idea_id'],idea_revision=idea['revision'],trace_block='## Idea trace\nidea_id: '+idea['idea_id']+'\nidea_revision: '+str(idea['revision']),origin=idea['origin'],shape=idea['shape'],ratings=idea['ratings'],assessments=idea['assessments'],plans=idea['plans'],executions=idea['executions'])
        if hasattr(args,'expected_revision'):
            integer(args.expected_revision,'expected revision',1)
            require(args.expected_revision==idea['revision'],'Stale idea revision; show current idea before retrying','stale_revision')
        if command in ('shape','rate','assess'):
            require(command=='shape' or idea['status']=='active','Archived revision is immutable; shape the next slice first','archived_revision')
            if command=='shape':
                idea['shape']=shape(read_json(args.file))
                idea['status']='active'
            elif command=='rate':
                integer(args.urgency,'urgency',1,10)
                integer(args.importance,'importance',1,10)
                idea['ratings']=dict(urgency=args.urgency,importance=args.importance,actor=args.actor,timestamp=now())
            else:
                value=assessment(read_json(args.file))
                value.update(assessment_id=identity('assessment'),actor=args.actor,timestamp=now())
                idea['assessments'].append(value)
            idea['revision']+=1
            idea['revisions'].append(snapshot(idea,args.actor,command))
            store.commit(state)
            return dict(idea=idea)
        if command in ('propose','place'):
            integer(args.expected_backlog_revision,'expected backlog revision')
            require(args.expected_backlog_revision==state['backlog_revision'],'Stale backlog revision; list before retrying','stale_backlog')
            integer(args.position,'position',1,len(state['order']))
            text(args.reason,'reason')
            if command=='propose':
                ready(idea)
            order=[key for key in state['order'] if key!=idea['idea_id']]
            index=args.position-1
            choice=dict(idea_id=idea['idea_id'],idea_revision=idea['revision'],position=args.position,reason=args.reason,actor=args.actor,timestamp=now(),source_backlog_revision=state['backlog_revision'],neighbors=dict(before=order[index-1] if index else None,after=order[index] if index<len(order) else None),snapshot=dict(ratings=copy.deepcopy(idea['ratings']),assessments=copy.deepcopy(idea['assessments'])))
            if command=='propose':
                choice['proposal_id']=identity('proposal')
                idea['proposals'].append(choice)
                store.commit(state)
                return dict(proposal=choice,backlog_revision=state['backlog_revision'])
            order.insert(index,idea['idea_id'])
            state['order']=order
            state['backlog_revision']+=1
            choice['accepted_backlog_revision']=state['backlog_revision']
            state['placements'].append(choice)
            store.commit(state)
            return dict(placement=choice,backlog_revision=state['backlog_revision'],order=order)
        if command=='register-plan':
            ready(idea,complete_shape=True)
            path=resolve_file(args.path)
            raw=read_bytes(path)
            hashed=digest(raw)
            for prior in idea['plans']:
                if prior['source_path']==str(path):
                    verify_current(prior)
                    require(prior['idea_revision']==idea['revision'],'A plan file already links a different revision; use a new file','link_conflict')
                    require(prior['sha256']==hashed,'Working plan differs from the accepted evidence; use a new file for a new plan','changed_artifact')
                    issues=store.view_issues(state)
                    if issues:
                        raise IdeaError('archive_view_failed','Plan already registered; repair archive views',committed=True,issues=issues,plan=prior)
                    return dict(plan=prior,idea=idea,repeated=True)
            validation=validate_plan(path,raw,idea,config)
            plan_id=identity('plan')
            frozen_path=store.path/'plan-evidence'/(plan_id+'.md')
            plan=dict(plan_id=plan_id,idea_id=idea['idea_id'],idea_revision=idea['revision'],path=str(frozen_path),source_path=str(path),content=raw.decode('utf-8'),sha256=hashed,actor=args.actor,timestamp=now(),validation=validation)
            idea['plans'].append(plan)
            idea['status']='archived'
            key=idea['idea_id']+'/r'+str(idea['revision'])+'.json'
            state['archives'].setdefault(key,dict(idea_id=idea['idea_id'],origin=copy.deepcopy(idea['origin']),revision=copy.deepcopy(idea['revisions'][-1])))
            store.commit(state)
            issues=store.view_issues(state,repair=True)
            if issues:
                raise IdeaError('archive_view_failed','Plan committed; archive materialization needs repair-views',committed=True,issues=issues,plan=plan)
            return dict(plan=plan,idea=idea)
        if command=='record-execution':
            check_id(args.plan_id,'plan')
            plan=next((p for p in idea['plans'] if p['plan_id']==args.plan_id),None)
            require(plan is not None,'Plan is not registered for this idea','link_mismatch')
            verify_current(plan)
            path=resolve_file(args.path)
            raw=read_bytes(path)
            value=receipt(decode(raw),idea['idea_id'],args.plan_id)
            hashed=digest(raw)
            for existing in state['ideas'].values():
                for attempt in existing['executions']:
                    if attempt['attempt_id']==value['attempt_id']:
                        require(attempt['idea_id']==idea['idea_id'] and attempt['plan_id']==args.plan_id and attempt['sha256']==hashed and attempt['path']==str(path),'Attempt ID already used for different evidence','attempt_conflict')
                        verify_current(attempt)
                        return dict(execution=attempt,idea=idea,repeated=True)
            execution=dict(idea_id=idea['idea_id'],plan_id=args.plan_id,attempt_id=value['attempt_id'],status=value['status'],receipt=value,path=str(path),sha256=hashed,actor=args.actor,timestamp=now())
            idea['executions'].append(execution)
            store.commit(state)
            return dict(execution=execution,idea=idea)
        raise IdeaError('usage','Unknown command')


def main(argv=None):
    try:
        args=parser().parse_args(argv)
        result=dict(ok=True,**run(args,configuration()))
    except IdeaError as exc:
        result=dict(ok=False,error=dict(code=exc.code,message=str(exc)),**exc.details)
    except (OSError,UnicodeError,ValueError,RecursionError) as exc:
        result=dict(ok=False,error=dict(code='io_error',message=str(exc)))
    sys.stdout.buffer.write(encoded(result))
    return 0 if result['ok'] else 1


if __name__=='__main__':
    sys.exit(main())
