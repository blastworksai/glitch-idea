#!/usr/bin/env python3
"""Local idea lifecycle CLI adapter. Python 3.10+."""
import argparse
import math
import os
import re
from pathlib import Path
import sys

from idea_domain import IdeaError, decode, encoded, number, require, text
from idea_store import read_bytes
from idea_service import run_legacy as run, default_workspace


class Parser(argparse.ArgumentParser):
    def error(self,message):
        raise IdeaError('usage','Invalid launcher arguments' if getattr(self,'launcher_errors',False) else message)


def parser():
    cli=Parser(description=__doc__)
    cli.add_argument('--store')
    commands=cli.add_subparsers(dest='command',required=True,parser_class=Parser)
    for name in ('capture','list','show','exploration','shape','rate','assess','propose','place','handoff','register-plan','record-execution','deliver','doctor','repair-views'):
        p=commands.add_parser(name)
        if name=='shape':
            # Retired verb: parsed only so it can answer unsupported_command, pointing at exploration.
            p.add_argument('idea_id',nargs='?')
            p.add_argument('--actor');p.add_argument('--file');p.add_argument('--expected-revision')
            continue
        if name not in ('capture','list','doctor','repair-views'):
            p.add_argument('idea_id')
        if name in ('capture','exploration','rate','assess','propose','place','register-plan','record-execution','deliver'):
            p.add_argument('--actor',required=True)
        if name=='capture':
            p.add_argument('--text-file',required=True)
        if name in ('exploration','assess'):
            p.add_argument('--file',required=True)
        if name in ('exploration','rate','assess','register-plan'):
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
        if name=='register-plan':
            # Both or neither: together they move the idea's living file into that workspace.
            p.add_argument('--workspace-name')
            p.add_argument('--workspace-path')
        if name=='deliver':
            p.add_argument('--ref',required=True)
        if name=='record-execution':
            p.add_argument('--plan-id',required=True)
    for name in ('serve','session-open','browser-open'):
        p=commands.add_parser(name)
        p.launcher_errors=True
        p.add_argument('--runtime-root',required=name=='serve')
        if name != 'serve':
            p.add_argument('--runtime-python')
            p.add_argument('--readiness-timeout',type=float,default=5)
            p.add_argument('--resume')
            p.add_argument('--idea-id')
        if name == 'browser-open':
            p.add_argument('--browser',choices=('orca','system'),required=True)
            p.add_argument('--orca-worktree')
            p.add_argument('--orca-terminal')
            p.add_argument('--orca-host')
    p=commands.add_parser('prototype-serve')
    p.launcher_errors=True
    p.add_argument('--runtime-root')
    p.add_argument('--runtime-python')
    p.add_argument('--session',required=True)
    p.add_argument('--dir',required=True)
    for name in ('sessions','session-discard'):
        p=commands.add_parser(name)
        p.launcher_errors=True
        p.add_argument('--runtime-root')
        p.add_argument('--runtime-python')
        if name=='session-discard':
            p.add_argument('--binding',required=True)
            p.add_argument('--confirm',action='store_true')
    for name in ('events','respond','fill','asset','session-close'):
        p=commands.add_parser(name)
        p.launcher_errors=True
        p.add_argument('--runtime-root')
        p.add_argument('--runtime-python')
        p.add_argument('--session',required=True)
        p.add_argument('--generation')
        if name=='events':
            p.add_argument('--after',type=int,default=0)
            p.add_argument('--timeout',type=float,default=25)
        if name in ('respond','fill'):
            p.add_argument('--request',required=True)
            p.add_argument('--payload',required=True)
        if name=='asset':
            p.add_argument('--idea',required=True)
            p.add_argument('--revision',type=int,required=True)
            p.add_argument('--request',required=True)
            p.add_argument('--file',required=True)
            p.add_argument('--type',choices=('image/png','application/zip'))
    return cli


def _configuration_source():
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
    return value,base,skill_dir,installed,argv,timeout


def configuration():
    value,base,skill_dir,installed,argv,timeout=_configuration_source()
    # store_path already supports durable storage outside the installed package.
    store_path=value.get('store_path',str(base/'ideas'))
    text(store_path,'store_path',4096)
    resolved=Path(store_path).expanduser()
    if not resolved.is_absolute():
        resolved=base/resolved
    result=dict(store_path=str(resolved.resolve()),plan_validator_argv=argv,validator_timeout_seconds=timeout)
    workspace=default_workspace(value.get('default_workspace'))
    if workspace is not None:
        result['default_workspace']=workspace  # key present only when configured
    return result


LAUNCH_COMMANDS=frozenset(('serve','session-open','browser-open'))
AGENT_COMMANDS=frozenset(('events','respond','fill','asset','session-close'))
PROTOTYPE_COMMANDS=frozenset(('prototype-serve',))
SESSION_COMMANDS=frozenset(('sessions','session-discard'))
DISCARD_WARNING='This session is still open: its browser tab and terminal agent will stop working. Run again with --confirm to discard it.'


def _launcher_requested(argv):
    # Only global --store precedes the verb; do not classify its path as a verb.
    index=0
    while index < len(argv):
        value=argv[index]
        if value=='--store': index+=2; continue
        if value.startswith('--store='): index+=1; continue
        return value in LAUNCH_COMMANDS | AGENT_COMMANDS | PROTOTYPE_COMMANDS | SESSION_COMMANDS
    return False


def _launch_path(value,base=None,*,absolute=False):
    text(value,'launcher path',4096)
    require('\x00' not in value,'Invalid launcher path','invalid_config')
    path=Path(value).expanduser()
    if absolute:
        require(path.is_absolute(),'Launcher path must be absolute','invalid_config')
    elif not path.is_absolute():
        path=(Path.cwd() if base is None else base)/path
    # Inspect the original path, including components before '..', before any
    # lexical normalization or resolve could hide an existing symlink.
    current=Path(path.anchor)
    for part in path.parts[1:]:
        current=current/part
        require(not current.is_symlink(),'Launcher path contains a symlink','runtime_not_private')
    return Path(os.path.abspath(path))


def _private_home():
    if os.name=='posix':
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    return Path.home()


def launcher_configuration(args):
    value,base,skill_dir,installed,argv,timeout=_configuration_source()
    store_value=args.store if args.store is not None else value.get('store_path',str(base/'ideas'))
    store=_launch_path(store_value,None if args.store is not None else base)
    root_value=args.runtime_root if args.runtime_root is not None else value.get('runtime_root',str(_private_home()/'.local/state/glitch-idea'))
    runtime=_launch_path(root_value,absolute=True)
    require(runtime!=skill_dir and skill_dir not in runtime.parents and runtime not in skill_dir.parents,
            'Runtime must be outside the skill package','invalid_config')
    configured_python=getattr(args,'runtime_python',None)
    if configured_python is None: configured_python=value.get('runtime_python')
    require(not installed or configured_python is not None,
            'Installed launch requires explicit absolute runtime_python; runtime setup is pending','runtime_python_required')
    from idea_launch import source_python
    executable=source_python(configured_python)
    if installed and args.command in {'serve'} | AGENT_COMMANDS | SESSION_COMMANDS:
        # Resolved base executables cannot identify a venv's dependency context.
        require(os.path.abspath(sys.executable)==os.path.abspath(executable),
                'Run with the configured runtime_python','runtime_interpreter_mismatch')
    config=dict(store_path=str(store),plan_validator_argv=argv,validator_timeout_seconds=timeout)
    workspace=default_workspace(value.get('default_workspace'))  # a bad value is a plain config error here too
    if workspace is not None: config['default_workspace']=workspace
    return store,runtime,config,executable


def _safe_launch_details(details):
    if type(details) is not dict: return {}
    result={}
    for key,prefix in (('binding_id','binding_'),('session_id','session_'),('selected_idea_id','idea_')):
        value=details.get(key)
        if key=='selected_idea_id' and key in details and value is None:
            result[key]=None
        elif type(value) is str and re.fullmatch(prefix+r'[0-9a-f]{32}',value):
            result[key]=value
    if type(details.get('resume_required')) is bool: result['resume_required']=details['resume_required']
    return result


def _safe_sessions(rows):
    """Fixed, typed rows only: never a credential, token, pairing code or anything the owner did not list."""
    if type(rows) is not list: return None
    result=[]
    for row in rows[:8]:
        if type(row) is not dict: return None
        binding,idea,title=row.get('binding_id'),row.get('selected_idea_id'),row.get('title')
        if not (type(binding) is str and re.fullmatch(r'binding_[0-9a-f]{32}',binding)): return None
        if not (idea is None or (type(idea) is str and re.fullmatch(r'idea_[0-9a-f]{32}',idea))): return None
        if not (title is None or type(title) is str) or type(row.get('finished')) is not bool or type(row.get('in_use')) is not bool: return None
        result.append(dict(binding_id=binding,selected_idea_id=idea,title=None if title is None else title[:80],
                           finished=row['finished'],in_use=row['in_use']))
    return result


def run_launcher(args):
    # Separate boundary: fixed redacted errors; legacy result/error path is intact.
    try:
        from idea_launch import LaunchError, open_browser_session, open_session, serve
        from idea_native import NativeError, OrcaBinding
        from idea_runtime import RuntimeError
        from idea_bridge import BridgeError
    except Exception:
        return dict(ok=False,error=dict(code='launcher_failed',message='launcher_failed'))
    try:
        if args.command!='serve':
            require(type(args.readiness_timeout) in (int,float) and math.isfinite(args.readiness_timeout)
                    and 0 < args.readiness_timeout <= 5,'Invalid readiness budget','invalid_input')
            require(args.resume is None or (type(args.resume) is str and re.fullmatch(r'binding_[0-9a-f]{32}',args.resume)),
                    'Invalid binding ID','invalid_input')
            require(args.idea_id is None or (type(args.idea_id) is str and re.fullmatch(r'idea_[0-9a-f]{32}',args.idea_id)),
                    'Invalid idea ID','invalid_input')
        orcabinding=None
        if args.command=='browser-open':
            selectors=(args.orca_worktree,args.orca_terminal,args.orca_host)
            require(args.browser=='orca' or not any(value is not None for value in selectors),
                    'System mode does not accept Orca selectors','usage')
            if args.browser=='orca':
                require(args.orca_worktree is not None and args.orca_terminal is not None,
                        'Orca requires originating worktree and terminal','origin_missing')
                orcabinding=OrcaBinding(*selectors)
        store,runtime,config,executable=launcher_configuration(args)
        if args.command=='serve':
            try: serve(store,runtime,config)
            except KeyboardInterrupt: pass  # serve's finally drains owned work.
            return dict(ok=True,code='stopped')
        options=dict(binding_id=args.resume,selected_idea_id=args.idea_id,runtime_python=executable,
                     readiness_timeout=args.readiness_timeout)
        # Keep released open_session's five-second mutation timeout; probes remain
        # <=1s within readiness budget. Never retry an ambiguous NEW here.
        if args.command=='session-open': result=open_session(store,runtime,config,**options)
        else: result=open_browser_session(store,runtime,config,mode=args.browser,orcabinding=orcabinding,**options)
        return dict(ok=True,**result)
    except (LaunchError,NativeError,RuntimeError,BridgeError,IdeaError) as exc:
        result=dict(ok=False,error=dict(code=exc.code,message=exc.code),
                    **_safe_launch_details(getattr(exc,'details',{})))
        sessions=_safe_sessions(getattr(exc,'sessions',None)) if exc.code=='binding_capacity' else None
        if sessions is not None: result['sessions']=sessions
        return result
    except Exception:
        return dict(ok=False,error=dict(code='launcher_failed',message='launcher_failed'))


def run_sessions(args):
    """Owner-control verbs: list the retained sessions, or discard one. Needs a running service."""
    try:
        from idea_runtime import Runtime, RuntimeError
    except Exception:
        return dict(ok=False,error=dict(code='launcher_failed',message='launcher_failed'))
    try:
        if args.command=='session-discard':
            require(type(args.binding) is str and re.fullmatch(r'binding_[0-9a-f]{32}',args.binding),'Invalid binding ID','invalid_input')
        store,runtime,_,_=launcher_configuration(args)
        client=Runtime(store,runtime)
        if args.command=='sessions':
            rows=_safe_sessions(client.list_sessions(timeout=5))
            require(rows is not None,'Unreadable session list','launcher_failed')
            return dict(ok=True,sessions=rows)
        try:
            value=client.discard_binding(args.binding,confirm=args.confirm,timeout=5)
        except RuntimeError as exc:
            if exc.code!='session_in_use': raise
            return dict(ok=False,error=dict(code='session_in_use',message='session_in_use'),warning=DISCARD_WARNING)
        return dict(ok=True,binding_id=value['binding_id'],was_in_use=value['was_in_use'])
    except (RuntimeError,IdeaError) as exc:
        return dict(ok=False,error=dict(code=exc.code,message=exc.code))
    except Exception:
        return dict(ok=False,error=dict(code='launcher_failed',message='launcher_failed'))


def run_prototype(args):
    """Sealed prototype server on its own loopback port; prints one JSON line when ready."""
    try:
        from idea_prototype import serve_prototype
        from idea_runtime import RuntimeError
    except Exception:
        return dict(ok=False,error=dict(code='launcher_failed',message='launcher_failed'))
    try:
        store,runtime,_,_=launcher_configuration(args)
        return serve_prototype(args.session,args.dir,store,runtime)
    except (RuntimeError,IdeaError) as exc:
        return dict(ok=False,error=dict(code=exc.code,message=exc.code))
    except Exception:
        return dict(ok=False,error=dict(code='launcher_failed',message='launcher_failed'))


def run_agent(args):
    """Native-only thin adapter; fixed client owns transport and secret handling."""
    try:
        from idea_agent_client import AgentClient, AgentClientError
        from idea_runtime import Runtime, RuntimeError
        from idea_bridge import BridgeError, decode_json
        from idea_proposals import CORRELATION, LIMIT
    except Exception:
        return dict(ok=False,error=dict(code='agent_failed',message='agent_failed'))
    try:
        require(type(args.session) is str and re.fullmatch(r'session_[0-9a-f]{32}',args.session),
                'Invalid agent session','invalid_agent_input')
        require(args.generation is None or (type(args.generation) is str and
                re.fullmatch(r'agent_[0-9a-f]{32}',args.generation)),
                'Invalid agent generation','invalid_agent_input')
        payload=None
        if args.command=='events':
            require(type(args.after) is int and 0 <= args.after <= 10**12 and
                    type(args.timeout) in (int,float) and math.isfinite(args.timeout) and 0 <= args.timeout <= 25,
                    'Invalid event wait','invalid_agent_input')
        elif args.command in ('respond','fill'):
            require(type(args.request) is str and
                    re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',args.request),
                    'Invalid request ID','invalid_agent_input')
            payload=decode_json(read_bytes(args.payload,limit=LIMIT))
            require(type(payload) is dict and set(payload)==CORRELATION | {'proposal' if args.command=='respond' else 'fields'} and
                    payload['request_id']==args.request and payload['session_id']==args.session,
                    'Response correlation mismatch','invalid_agent_input')
        asset=None
        if args.command=='asset':
            from idea_asset_evidence import MAX_FILE
            require(type(args.request) is str and
                    re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',args.request),
                    'Invalid request ID','invalid_agent_input')
            kinds={'.png':'image/png','.zip':'application/zip'}
            mime=args.type or kinds.get(Path(args.file).suffix.lower())
            require(mime in kinds.values(),'Asset type must be image/png or application/zip','invalid_agent_input')
            asset=dict(idea_id=args.idea,revision=args.revision,request_id=args.request,
                       name=Path(args.file).name,mime=mime,data=read_bytes(args.file,limit=MAX_FILE))
        store,runtime,_,_=launcher_configuration(args)
        # The full 5 s control budget: an owner busy finishing the browser's own write (the page
        # saves each terminal fill at once) is live, and must not read as owner_unavailable.
        client=AgentClient(Runtime(store,runtime),args.session,expected_generation=args.generation,timeout=5)
        if args.command=='events': return client.events(args.after,args.timeout)
        if args.command=='respond': return client.respond(payload)
        if args.command=='fill': return client.fill(payload)
        if args.command=='asset': return client.asset(**asset)
        return client.session_close()
    except (AgentClientError,RuntimeError,BridgeError,IdeaError) as exc:
        result=dict(ok=False,error=dict(code=exc.code,message=exc.code))
        state=getattr(exc,'write_state',None)
        if type(state) is str and state in ('not_applied','committed_uncertain'):
            result['write_state']=state
        return result
    except Exception:
        return dict(ok=False,error=dict(code='agent_failed',message='agent_failed'))


def main(argv=None):
    values=sys.argv[1:] if argv is None else list(argv)
    launching=_launcher_requested(values)
    try:
        cli=parser();cli.launcher_errors=launching
        args=cli.parse_args(values)
        if args.command in LAUNCH_COMMANDS: result=run_launcher(args)
        elif args.command in AGENT_COMMANDS: result=run_agent(args)
        elif args.command in SESSION_COMMANDS: result=run_sessions(args)
        elif args.command in PROTOTYPE_COMMANDS: result=run_prototype(args)
        else: result=dict(ok=True,**run(args,configuration()))
    except IdeaError as exc:
        result=dict(ok=False,error=dict(code=exc.code,message=exc.code if launching else str(exc)),
                    **({} if launching else exc.details))
    except (OSError,UnicodeError,ValueError,RecursionError) as exc:
        result=dict(ok=False,error=dict(code='launcher_failed' if launching else 'io_error',
                    message='launcher_failed' if launching else str(exc)))
    sys.stdout.buffer.write(encoded(result))
    return 0 if result['ok'] else 1


if __name__=='__main__':
    sys.exit(main())
