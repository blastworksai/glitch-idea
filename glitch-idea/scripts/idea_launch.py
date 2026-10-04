#!/usr/bin/env python3
"""Source-only owned launcher/supervisor.

No installer, model calls, CLI imports, PID signals or arbitrary HTTP commands.
The private child entrypoint prints only redacted failures, never pairing codes.
Explicit initiating helpers receive a one-time code through open_session().
"""
import argparse
from contextlib import contextmanager
import copy
import getpass
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import tempfile

from idea_bridge import BridgeError, BridgeServer, Response, check
from idea_domain import IdeaError, check_id, decode, encoded, require
from idea_native import open_browser, NativeError, OrcaBinding
from idea_runtime import Runtime, RuntimeError as OwnerError, MAX_BINDINGS
from idea_service import Service, TrustedContext
from idea_sessions import SessionPolicy, AgentBinding
from idea_steps import load_registry, TrustedRoute
from idea_proposals import Broker
from idea_workflow_api import WorkflowError, WorkflowSettings
from idea_agent_source import (SourceAdapter, prepare_source, validate_source,
    validate_current, project_sources, proposal_source_digest)
from idea_agent_memory import validate_proposal, validate_method_memory
from idea_store import Store
from idea_handoff import HandoffProvider, idea_markdown, ideas, selection

COMMON = {'challenge','instance_nonce','store_sha256'}
OPEN_FIELDS = COMMON | {'mode','binding_id','selected_idea_id'}
CREDENTIAL_FIELDS = COMMON | {'binding_id','session_id','expected_generation'}
CHILD = Path(__file__).resolve()
_children = []  # Retain handles; no killing/ownership authority follows from PID.
_children_lock = threading.Lock()


class LaunchError(Exception):
    def __init__(self, code, **details):
        super().__init__(code)
        self.code, self.details = code, details


def _config(config, store_path):
    value = {} if config is None else copy.deepcopy(config)
    require(type(value) is dict and set(value) <= {'store_path','plan_validator_argv','validator_timeout_seconds'},
            'Unexpected trusted launch configuration','invalid_config')
    argv = value.get('plan_validator_argv')
    require(argv is None or (type(argv) is list and 0 < len(argv) <= 32 and
            all(type(part) is str and bool(part.strip()) and '\x00' not in part for part in argv)),
            'Invalid trusted plan validator','invalid_config')
    timeout = value.get('validator_timeout_seconds',30)
    require(type(timeout) in (int,float) and math.isfinite(timeout) and 1 <= timeout <= 120,
            'Invalid trusted validator timeout','invalid_config')
    result = dict(store_path=str(store_path),plan_validator_argv=argv,validator_timeout_seconds=timeout)
    require(len(encoded(result)) <= 16384,'Launch configuration exceeds limit','invalid_config')
    return result


def source_python(runtime_python=None):
    """Use this source interpreter, or an explicit existing absolute executable.

    Installed-runtime setup is the installer's job; this is no venv setup.
    """
    value = sys.executable if runtime_python is None else runtime_python
    require(type(value) is str and '\x00' not in value and Path(value).is_absolute()
            and Path(value).is_file() and os.access(value,os.X_OK),
            'Source launcher needs an absolute existing Python executable','runtime_python_unavailable')
    return str(Path(value))


def _actor():
    if os.name == 'posix':
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    return getpass.getuser()


class _AgentProvider:
    """Capture incarnation before Store; projection/validation never call policy."""
    def __init__(self, owner):
        self.owner = owner
        self.sources = SourceAdapter(owner.store)

    def context(self, context):
        return self.owner.live_context(context)

    def project(self, state, idea, context, live):
        status = 'disconnected' if live is None else live['agent_status']
        sources = project_sources(state, idea['idea_id']) if idea is not None else {}
        if status != 'connected':
            sources = {name:dict(available=False,code='agent_unavailable',source=None) for name in sources}
        projection = self.sources.project_projection(state, idea['idea_id'], live) if idea is not None and live is not None else dict(
            proposals=[], proposal_inventory=dict(total=0, projected=0, omitted=0, content_omitted=0,
                                                  index_path=idea['idea_id']+'.md' if idea is not None else None))
        talk = None
        if status == 'connected' and idea is not None:
            talk = self.owner.broker.conversation(live['binding_id'],live['generation'])
            # Only this idea's conversation at its current accepted revision reaches the page.
            if talk is not None and (talk['idea_id'] != idea['idea_id'] or talk['accepted_revision'] != idea['revision']):
                talk = None
        return dict(agent_status=status,agent_generation=None if live is None else live['generation'],resume=dict(required=status!='connected',reason=None if status=='connected'
                    else 'agent_paused' if status=='paused' else 'agent_disconnected'),
                    capabilities=dict(agent=status=='connected',memory=status=='connected'),proposal_sources=sources,
                    conversation=talk,**projection)

    def validate_acceptance(self, state, idea, payload, source, context, live):
        record = None
        if live is None:
            check(payload['proposal_id'] is None,'agent_unavailable',409)
        else:
            record = self.sources.validate_acceptance(state,idea,payload,source,context,live_binding=live)
        if payload['step'] == 'method':
            validate_method_memory(state,idea,payload['fields'],context,live_binding=live,
                                   proposals=self.owner.store.agent_proposals(state,idea['idea_id']),
                                   accepted_proposal=record)
        return record


class OwnerService:
    """Composition under one owned Runtime; no public owner secret access."""
    def __init__(self,runtime,config=None,*,workspace_resolver=None,server_factory=BridgeServer):
        self.runtime = runtime
        self.config = _config(config,runtime.store_path)
        self.actor = _actor()
        self.store = Store(runtime.store_path,observer=self.actor)
        self.handlers, self.routes = load_registry()
        self.workspace_resolver = workspace_resolver
        self.admission = threading.RLock()
        self.members = set()
        self.stopped = threading.Event()
        self._agent_lock = threading.RLock()
        self._agents = {}
        self._scope = threading.local()
        self._restoring = True
        self.broker = Broker(validate_source=self._validate_source,persist_proposal=self._persist_proposal,
                             validate_proposal=validate_proposal, source_digest_fn=proposal_source_digest)
        self.agent_provider = _AgentProvider(self)
        self.handoff_provider = HandoffProvider(self.store)
        for name, handler in (('ideas', ideas), ('selection', selection), ('idea-markdown', idea_markdown),
                              ('settings', self.settings_read), ('settings-save', self.settings_save),
                              ('settings-test', self.settings_test)):
            check(name not in self.routes,'duplicate_' + name + '_route',500)
            self.routes[name] = TrustedRoute(name,handler)
        check('propose' not in self.routes,'duplicate_proposal_route',500)
        self.routes['propose'] = TrustedRoute('propose',self.propose)
        self.policy = SessionPolicy(self.application,runtime.persist_binding,namespace=runtime.store_sha256,
                                    cancel=self.broker.cancel,agent_open=self._agent_open,agent_state=self.broker.status,
                                    agent_activity=self.broker.touch,
                                    memory_capability=True)
        for record in runtime.list_bindings():
            check(record['actor'] == self.actor,'session_binding_mismatch',403)
            self.policy.open_binding(self.actor,record['receipt_session_id'],record['selected_idea_id'],
                                     binding_id=record['binding_id'],resume=True)
            self.members.add(record['binding_id'])
        self._restoring = False
        self.server = server_factory(self.policy,routes=self.routes,control=self.control,activity=runtime,agent=self.agent)
        self.worker = None

    def application(self,record):
        return Service(self.store,self.config,TrustedContext(record['actor'],record['receipt_session_id'],record['selected_idea_id']),
                       handlers=self.handlers,workspace_resolver=self.workspace_resolver,agent_provider=self.agent_provider,
                       handoff_provider=self.handoff_provider)

    def _agent_open(self, binding_id, generation, session_id):
        self.broker.open(binding_id,generation,session_id)
        if self._restoring:
            self.broker.cancel(binding_id,generation,'invalidated')
        with self._agent_lock:
            self._agents[binding_id] = dict(binding_id=binding_id,generation=generation,session_id=session_id)

    def live_context(self, context):
        check(context.actor == self.actor,'session_binding_mismatch',403)
        with self._agent_lock:
            matches = [value for value in self._agents.values() if value['session_id'] == context.session_id]
            check(len(matches) <= 1,'binding_ambiguous',409)
            if not matches: return None
            live = dict(matches[0])
        live['agent_status'] = self.broker.status(live['binding_id'],live['generation'])['agent_status']
        return live

    @contextmanager
    def _source_scope(self, state, application):
        check(not hasattr(self._scope,'state'),'invalid_transaction',500)
        self._scope.state,self._scope.actor = state,application.context.actor
        try: yield
        finally:
            del self._scope.state
            del self._scope.actor

    def _validate_source(self, correlation, source):
        check(hasattr(self._scope,'state'),'invalid_transaction',500)
        return validate_source(self._scope.state,correlation,source)

    def _persist_proposal(self, evidence):
        check(hasattr(self._scope,'state'),'invalid_transaction',500)
        return self.store.persist_agent_proposal(self._scope.state,evidence,actor=self._scope.actor,
                                                validate_current=validate_current)

    def _workflow_settings(self):
        return WorkflowSettings(self.runtime.runtime_root,self.runtime.store_sha256)

    def settings_read(self, binding, request, payload):
        """Setup: where this store's ideas live. The API key is never returned, only key_set."""
        check(payload is None,'invalid_input')
        return dict(ok=True,code='ok',**self._workflow_settings().public())

    def settings_save(self, binding, request, payload):
        check(type(payload) is dict and set(payload) == {'where','base_url','key'},'invalid_input')
        return dict(ok=True,code='ok',**self._workflow_settings().save(payload['where'],payload['base_url'],payload['key']))

    def settings_test(self, binding, request, payload):
        """Calls the operator's own configured workflow API (GET /v1/health) and reports a code only."""
        check(payload == {},'invalid_input')
        try:
            found = self._workflow_settings().client().health()
        except (WorkflowError,IdeaError) as exc:
            # Not configured, unreachable or refused: a structured result, never a route error.
            return dict(ok=True,code='ok',reachable=False,reason=exc.code,service=None)
        return dict(ok=True,code='ok',reachable=True,reason=None,service=found['service'])

    def propose(self, binding, request, payload):
        # Browser bridge holds the stable binding lock; source is prepared under
        # Store, never from browser prose or an unsaved client buffer.
        live = self.live_context(binding.application.context)
        check(live is not None and live['agent_status'] == 'connected','agent_unavailable',409)
        check(type(payload) is dict and payload.get('operation') in ('shape','memory','method','assessment'),'operation_unavailable',409)
        context = AgentBinding(binding,live['binding_id'],live['generation'],live['session_id'])
        self.policy.recheck_agent(context)
        with self.store.transaction() as state:
            source = prepare_source(state,payload.get('idea_id'),payload['operation'])
            with self._source_scope(state,binding.application):
                return self.broker.enqueue(context.binding_id,context.generation,payload,source)

    def agent(self, operation, context, payload):
        if operation == 'events':
            # Authenticated Bridge releases binding/Store guards around wait.
            return self.broker.events(context.binding_id,context.generation,payload['session_id'],
                                      payload['after'],payload['timeout'])
        if operation == 'session-close':
            return self.policy.close_agent(context)
        if operation == 'fill':
            # Volatile, typed conversation fields; no Store write (the page saves the draft).
            # A conversation for a revision the idea has moved past is over: refuse, never renew.
            self.policy.recheck_agent(context)
            check(type(payload) is dict,'invalid_input')
            with self.store.transaction() as state:
                idea = state['ideas'].get(payload.get('idea_id')) if type(payload.get('idea_id')) is str else None
                check(idea is not None and idea['revision'] == payload.get('accepted_revision'),'stale_source',409)
            return self.broker.fill(context.binding_id,context.generation,payload)
        check(operation == 'respond','unknown_route',404)
        self.policy.recheck_agent(context)
        with self.store.transaction(write=True) as state:
            with self._source_scope(state,context.binding.application):
                return self.broker.respond(context.binding_id,context.generation,payload)

    def _credentials(self, payload):
        check(type(payload['binding_id']) is str and re.fullmatch(r'binding_[0-9a-f]{32}',payload['binding_id'])
              and type(payload['session_id']) is str and re.fullmatch(r'session_[0-9a-f]{32}',payload['session_id']),
              'invalid_control')
        expected = payload['expected_generation']
        check(expected is None or (type(expected) is str and re.fullmatch(r'agent_[0-9a-f]{32}',expected)),
              'invalid_control')
        with self.admission:
            check(not self.stopped.is_set(),'busy',503)
            records = [record for record in self.runtime.list_bindings() if record['receipt_session_id'] == payload['session_id']]
            check(len(records) == 1 and records[0]['binding_id'] == payload['binding_id']
                  and records[0]['binding_id'] in self.members and records[0]['actor'] == self.actor,
                  'session_binding_mismatch',403)
            result = self.policy.agent_credentials(payload['binding_id'])
            check(result['session_id'] == payload['session_id'] and
                  (expected is None or result['generation'] == expected),'session_binding_mismatch',403)
            status = self.broker.status(result['binding_id'],result['generation'])
            check(status['agent_status'] == 'connected','agent_unavailable',409)
            return result

    def request_stop(self):
        self.server.stop_admission()
        self.stopped.set()

    def _open(self,payload):
        mode,binding_id,selected = (payload[key] for key in ('mode','binding_id','selected_idea_id'))
        check(type(mode) is str and mode in ('new','resume'),'invalid_control')
        check((mode == 'new' and binding_id is None) or
              (mode == 'resume' and type(binding_id) is str and re.fullmatch(r'binding_[0-9a-f]{32}',binding_id)), 'invalid_control')
        check(selected is None or (type(selected) is str and re.fullmatch(r'idea_[0-9a-f]{32}',selected)), 'invalid_control')
        with self.admission:
            check(not self.stopped.is_set(),'busy',503)
            records = {record['binding_id']:record for record in self.runtime.list_bindings()}
            check(set(records) == self.members,'binding_membership_conflict',409)
            if mode == 'new':
                check(len(records) < MAX_BINDINGS and len(self.members) < self.policy.limit,'session_capacity_exhausted',503)
                if selected is not None:
                    with self.store.transaction() as state:
                        check(selected in state['ideas'],'not_found',404)
                sid = self.store.create_session()
                binding_id = self.policy.open_binding(self.actor,sid,selected)
                self.members.add(binding_id)
            else:
                check(binding_id in records,'binding_not_found',404)
                prior = records[binding_id]
                check(prior['actor'] == self.actor,'session_binding_mismatch',403)
                sid = prior['receipt_session_id']
                selected = prior['selected_idea_id'] if selected is None else selected
                self.policy.open_binding(self.actor,sid,selected,binding_id=binding_id,resume=True)
            code = self.policy.issue_pairing(binding_id)
            return dict(binding_id=binding_id,session_id=sid,selected_idea_id=selected,pairing_code=code)

    def control(self,request,payload):
        operation = request.path.rsplit('/',1)[-1]
        try:
            if operation in ('probe','stop'):
                body,callback = self.runtime.control(operation,payload,request.header('Authorization'),self.request_stop)
                return Response(body,after_send=callback)
            check(operation in ('binding-open','agent-credentials') and type(payload) is dict and
                  set(payload) == (OPEN_FIELDS if operation == 'binding-open' else CREDENTIAL_FIELDS),'invalid_control')
            body = self.runtime.validate_owner({key:payload[key] for key in COMMON},request.header('Authorization'),operation,payload)
            body.update(self._open(payload) if operation == 'binding-open' else self._credentials(payload))
            return Response(body)
        except OwnerError as exc:
            return Response(dict(ok=False,code=exc.code),401 if exc.code == 'owner_unauthorized' else 409)
        except IdeaError as exc:
            return Response(dict(ok=False,code=exc.code),500)

    def start(self):
        self.worker = threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.05},daemon=True)
        self.worker.start()
        self.runtime.publish_discovery(self.server.server_port)

    def supervise(self):
        try:
            while not self.stopped.wait(.1):
                if self.runtime.idle_due(): self.request_stop()
        finally:
            self.shutdown()

    def shutdown(self):
        self.request_stop()
        # Serialize against already-admitted explicit opens before snapshot.
        with self.admission, self._agent_lock:
            generations = list(self._agents.values())
        for live in generations:
            self.broker.cancel(live['binding_id'],live['generation'],'shutdown')
        if self.worker is not None:
            self.server.shutdown()
        self.server.drain()  # No timeout permits abandoning an admitted commit.
        self.server.server_close()
        if self.worker is not None: self.worker.join()
        self.runtime.close()


def serve(store_path,runtime_root,config=None,*,clock=time.monotonic,on_ready=None,workspace_resolver=None):
    runtime = Runtime(store_path,runtime_root,clock=clock)
    owner = None
    try:
        runtime.acquire_owner()
        owner = OwnerService(runtime,config,workspace_resolver=workspace_resolver)
        owner.start()
        if on_ready is not None: on_ready(owner)
        owner.supervise()
    finally:
        if owner is not None and owner.worker is not None and owner.worker.is_alive(): owner.shutdown()
        else: runtime.close()


def ensure_service(store_path,runtime_root,config=None,*,runtime_python=None,readiness_timeout=5,
                   spawn=subprocess.Popen,clock=time.monotonic,sleep=time.sleep):
    require(type(readiness_timeout) in (int,float) and math.isfinite(readiness_timeout) and 0 < readiness_timeout <= 5,
            'Readiness budget must be at most five seconds')
    runtime = Runtime(store_path,runtime_root)
    config = _config(config,runtime.store_path)
    executable = source_python(runtime_python)
    deadline = clock()+readiness_timeout
    spawned = False
    mismatched = False
    busy = None
    while clock() < deadline:
        try:
            return runtime.probe_owner(timeout=min(1,max(.001,deadline-clock())))
        except OwnerError as exc:
            if exc.code not in ('owner_unavailable','runtime_busy','owner_identity_mismatch','busy'): raise
            mismatched |= exc.code == 'owner_identity_mismatch'
            # A live owner answered busy: a later owner_unavailable is a probe that ran out of
            # time, never a reason to start a second service, and busy is what gets reported.
            if exc.code in ('busy','runtime_busy'): busy = exc.code
            if not spawned and not mismatched and not busy and exc.code == 'owner_unavailable':
                argv = [executable,str(CHILD),'--store',str(runtime.store_path),'--runtime-root',str(runtime.runtime_root),
                        '--config-stdin','serve']
                try:
                    # Prepopulated private descriptor avoids argv leaks and pipe
                    # writes blocked by a hung configured interpreter.
                    with tempfile.TemporaryFile(mode='w+b') as stream:
                        stream.write(encoded(config)); stream.seek(0)
                        child = spawn(argv,stdin=stream,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                      shell=False,close_fds=True,start_new_session=os.name=='posix')
                except OSError:
                    raise LaunchError('service_spawn_failed') from None
                with _children_lock:
                    _children[:] = [value for value in _children if value.poll() is None]
                    _children.append(child)
                spawned = True
            sleep(min(.05,max(0,deadline-clock())))
    if mismatched: raise OwnerError('owner_identity_mismatch')
    if busy: raise OwnerError(busy)
    raise LaunchError('service_readiness_timeout')


def open_session(store_path,runtime_root,config=None,*,binding_id=None,selected_idea_id=None,runtime_python=None,
                 readiness_timeout=5,timeout=5,spawn=subprocess.Popen):
    # Validate typed caller selections before starting a child or allocating SID.
    if selected_idea_id is not None: check_id(selected_idea_id)
    require(binding_id is None or (type(binding_id) is str and re.fullmatch(r'binding_[0-9a-f]{32}',binding_id)), 'Invalid binding ID')
    ensure_service(store_path,runtime_root,config,runtime_python=runtime_python,readiness_timeout=readiness_timeout,spawn=spawn)
    # One binding-open only. Never auto-retry a NEW request after wire ambiguity.
    return Runtime(store_path,runtime_root).open_binding('new' if binding_id is None else 'resume',binding_id,selected_idea_id,timeout)


def open_browser_session(store_path,runtime_root,config=None,*,mode,orcabinding=None,runner=subprocess.run,**options):
    # Validate the caller's launch choice before startup or receipt allocation.
    if mode not in ('system','orca'): raise LaunchError('invalid_mode')
    if mode == 'orca':
        def identifier(value):
            return type(value) is str and 0 < len(value) <= 4096 and not any(ord(char) < 32 or ord(char) == 127 for char in value)
        if (not isinstance(orcabinding,OrcaBinding) or not identifier(orcabinding.worktree_id)
                or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}::.+',orcabinding.worktree_id)
                or not identifier(orcabinding.terminal_handle)
                or (orcabinding.execution_host_id is not None and not identifier(orcabinding.execution_host_id))):
            raise LaunchError('origin_missing')
    result = open_session(store_path,runtime_root,config,**options)
    try:
        launched = open_browser(result['origin'],mode=mode,binding=orcabinding,runner=runner)
    except NativeError as exc:
        raise LaunchError(exc.code,binding_id=result['binding_id'],session_id=result['session_id'],
                          selected_idea_id=result['selected_idea_id'],resume_required=True) from None
    return dict(result,browser=dict(mode=launched.mode,url=launched.url,browser_page_id=launched.browser_page_id))


def main(argv=None):
    cli = argparse.ArgumentParser(description='Private fixed source service entrypoint')
    cli.add_argument('--store',required=True)
    cli.add_argument('--runtime-root',required=True)
    cli.add_argument('--config-stdin',action='store_true')
    cli.add_argument('operation',choices=['serve'])
    try:
        args = cli.parse_args(argv)
        raw = sys.stdin.buffer.read(16385) if args.config_stdin else b'{}'
        require(len(raw) <= 16384,'Launch configuration exceeds limit')
        config = decode(raw)
        serve(args.store,args.runtime_root,config)
        return 0
    except (OwnerError,LaunchError,BridgeError,IdeaError) as exc:
        sys.stderr.write(json.dumps(dict(ok=False,code=exc.code))+'\n')
        return 1
    except Exception:
        sys.stderr.write('{"ok":false,"code":"service_start_failed"}\n')
        return 1


if __name__ == '__main__': sys.exit(main())
