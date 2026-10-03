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
from idea_service import run_legacy as run


class Parser(argparse.ArgumentParser):
    def error(self,message):
        raise IdeaError('usage','Invalid launcher arguments' if getattr(self,'launcher_errors',False) else message)


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
    for name in ('events','respond','fill','session-close'):
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
    return dict(store_path=str(resolved.resolve()),plan_validator_argv=argv,validator_timeout_seconds=timeout)


LAUNCH_COMMANDS=frozenset(('serve','session-open','browser-open'))
AGENT_COMMANDS=frozenset(('events','respond','fill','session-close'))


def _launcher_requested(argv):
    # Only global --store precedes the verb; do not classify its path as a verb.
    index=0
    while index < len(argv):
        value=argv[index]
        if value=='--store': index+=2; continue
        if value.startswith('--store='): index+=1; continue
        return value in LAUNCH_COMMANDS | AGENT_COMMANDS
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
    if installed and args.command in {'serve'} | AGENT_COMMANDS:
        # Resolved base executables cannot identify a venv's dependency context.
        require(os.path.abspath(sys.executable)==os.path.abspath(executable),
                'Run with the configured runtime_python','runtime_interpreter_mismatch')
    config=dict(store_path=str(store),plan_validator_argv=argv,validator_timeout_seconds=timeout)
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
        return dict(ok=False,error=dict(code=exc.code,message=exc.code),
                    **_safe_launch_details(getattr(exc,'details',{})))
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
        store,runtime,_,_=launcher_configuration(args)
        # The full 5 s control budget: an owner busy finishing the browser's own write (the page
        # saves each terminal fill at once) is live, and must not read as owner_unavailable.
        client=AgentClient(Runtime(store,runtime),args.session,expected_generation=args.generation,timeout=5)
        if args.command=='events': return client.events(args.after,args.timeout)
        if args.command=='respond': return client.respond(payload)
        if args.command=='fill': return client.fill(payload)
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
