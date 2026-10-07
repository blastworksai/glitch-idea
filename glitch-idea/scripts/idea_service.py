"""Typed shared application service.

The caller authenticates before every call. TrustedContext's durable session ID
survives restart while transport credentials rotate; create_session is NEW only.
No HTTP, arbitrary module loading, shell dispatch or editable actor is exposed.
The CLI imports these application operations; this module never imports idea.py.
"""
from dataclasses import dataclass
import copy
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile

from idea_domain import (IdeaError, MAX_INPUT, MAX_STATE, check_id, digest, identity, integer,
                         now, require, snapshot, text, assessment, ready, receipt, shape, decode,
                         delivery_ref, lifecycle_view)
from idea_store import Store, read_bytes
from idea_assessment import (assessment_digest, backlog_projection,
                             validate_assessment_proposal)
from idea_workflow import (STEP_ORDER, acceptance_source, accept_step,
                           capture_workflow, derive_state, empty_workflow,
                           save_draft, navigate_step, source_digest, validate_step_fields,
                           invalidate_external, adapt_snapshot, set_hand, HAND_STEPS,
                           check_memory_preference)

# Steps accepted through a trusted handler: every step but the two manual ones and the derived Review.
ADVANCED_STEPS = frozenset(step for step in STEP_ORDER if step not in ('capture','priorities','review'))
AGENT_OPERATIONS = frozenset(('discovery', 'exploration', 'memory', 'method', 'visual_brief', 'assessment'))
AGENT_REASONS = frozenset(('wrong_generation', 'wrong_session', 'agent_unavailable',
                          'stale_revision', 'stale_source', 'supporting_evidence'))
STATE_RESPONSE_BYTES = 2 * MAX_STATE + 2 * MAX_INPUT  # Bridge's inherited read bound.


@dataclass
class TrustedContext:
    """Authenticated caller binding; selected ID is nonsecret resume state."""
    actor: str
    session_id: str
    selected_idea_id: str | None = None
    asset_inventory: dict | None = None  # Detached verified data; no Store/file capability.


@dataclass(frozen=True)
class TrustedStepHandler:
    """Packaged handler seam; never constructed from HTTP data.

    validate(state, idea, payload, source, context) checks proposal correlation
    and assets/placement evidence and raises IdeaError on refusal. It must not
    mutate state. apply has the same arguments, but receives the accepted idea;
    it may compose validated placement/evidence changes in the detached state.
    Both run under the Store lock: no agent waits or filesystem publication.
    extra_dependencies is a fixed tuple of additional consumed earlier steps.
    J5b wires constant packaged module names to these objects.
    """
    validate: object
    apply: object = None
    extra_dependencies: tuple = ()


def _bounded_object(value):
    require(type(value) is dict, 'Payload must be a JSON object')
    pending, count = [(value,1)], 0
    while pending:
        item, depth = pending.pop(); count += 1
        require(depth <= 32 and count <= 100000, 'Payload exceeds depth/count limits', 'too_large')
        require(type(item) in (dict,list,str,int,float,bool,type(None)), 'Unsupported JSON value')
        if type(item) is dict:
            require(all(type(k) is str for k in item), 'JSON keys must be strings')
            for key,child in item.items(): pending.extend(((key,depth+1),(child,depth+1)))
        elif type(item) is list: pending.extend((child,depth+1) for child in item)
        elif type(item) is float: require(math.isfinite(item), 'Nonfinite JSON number')
        elif type(item) is str: require(not any(0xD800 <= ord(c) <= 0xDFFF for c in item), 'Invalid Unicode scalar')
    raw = json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    require(len(raw) <= MAX_INPUT, 'Payload exceeds 1 MiB', 'too_large')


def _exact(value,keys,name):
    require(type(value) is dict and set(value) == set(keys), 'Unexpected '+name+' fields')


def _fits_state_response(value):
    """Bound JSON bytes without accumulating a second whole response string."""
    total = 0
    encoder = json.JSONEncoder(ensure_ascii=False,separators=(',',':'),allow_nan=False)
    for chunk in encoder.iterencode(value):
        total += len(chunk.encode('utf-8'))
        if total > STATE_RESPONSE_BYTES:
            return False
    return True


def _request_id(value):
    require(type(value) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',value) is not None, 'Invalid request ID')


def _workspace_shape(workspace):
    _exact(workspace,('name','path','confirmed'),'workspace')
    text(workspace['name'],'workspace name',4096)
    text(workspace['path'],'workspace path',4096)
    require(type(workspace['confirmed']) is bool and workspace['confirmed'], 'Explicit workspace confirmation is required')


def _agent_id(value, prefix):
    require(type(value) is str and re.fullmatch(prefix+r'_[0-9a-f]{32}', value),
            'Invalid agent projection identifier', 'invalid_agent_provider')


def _conversation(talk, status, idea):
    """The open terminal conversation for this idea, if any: typed fills only."""
    if talk is None:
        return
    from idea_proposals import FILL_KEYS, MAX_FILLS
    require(status == 'connected' and idea is not None, 'Conversation without a connected agent', 'invalid_agent_provider')
    _exact(talk, ('request_id', 'operation', 'idea_id', 'accepted_revision', 'fills'), 'agent conversation')
    require(type(talk['request_id']) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', talk['request_id']) is not None
            and talk['operation'] in FILL_KEYS and talk['idea_id'] == idea['idea_id']
            and talk['accepted_revision'] == idea['revision'] and type(talk['fills']) is list
            and len(talk['fills']) <= MAX_FILLS, 'Invalid agent conversation', 'invalid_agent_provider')
    for index, item in enumerate(talk['fills'], 1):
        _exact(item, ('sequence', 'fields'), 'conversation fill')
        require(item['sequence'] == index and type(item['fields']) is dict and item['fields']
                and set(item['fields']) <= FILL_KEYS[talk['operation']], 'Invalid conversation fill', 'invalid_agent_provider')


def _agent_overlay(value, live, idea):
    """Validate a bounded browser projection, never arbitrary provider fields."""
    _bounded_object(value)
    keys = ('agent_status', 'agent_generation', 'resume', 'capabilities', 'proposal_sources', 'proposals', 'proposal_inventory')
    # A provider without a conversation channel projects none.
    _exact(value, keys + ('conversation',) if type(value) is dict and 'conversation' in value else keys, 'agent overlay')
    status = live['agent_status'] if live is not None else 'disconnected'
    require(type(value['agent_status']) is str and value['agent_status'] == status,
            'Agent projection differs from captured context', 'invalid_agent_provider')
    generation = live['generation'] if live is not None else None
    require(value['agent_generation'] == generation and
            (value['agent_generation'] is None or type(value['agent_generation']) is str),
            'Agent generation projection differs from captured context', 'invalid_agent_provider')
    _exact(value['resume'], ('required', 'reason'), 'agent resume')
    expected_reason = None if status == 'connected' else 'agent_paused' if status == 'paused' else 'agent_disconnected'
    require(type(value['resume']['required']) is bool and value['resume']['required'] == (status != 'connected')
            and value['resume']['reason'] == expected_reason, 'Invalid agent resume projection', 'invalid_agent_provider')
    _exact(value['capabilities'], ('agent', 'memory'), 'agent capabilities')
    require(all(type(item) is bool for item in value['capabilities'].values()) and
            value['capabilities']['agent'] == (status == 'connected') and
            (status == 'connected' or not value['capabilities']['memory']),
            'Invalid agent capabilities', 'invalid_agent_provider')
    _conversation(value.get('conversation'), status, idea)
    sources = value['proposal_sources']
    require(type(sources) is dict and set(sources) <= AGENT_OPERATIONS, 'Invalid proposal sources', 'invalid_agent_provider')
    for operation, entry in sources.items():
        _exact(entry, ('available', 'code', 'source'), 'proposal source entry')
        require(type(entry['available']) is bool and type(entry['code']) is str,
                'Invalid source availability', 'invalid_agent_provider')
        if not entry['available']:
            require(entry['source'] is None and entry['code'] in ('not_ready', 'operation_unavailable', 'agent_unavailable',
                    'source_too_large', 'source_projection_capacity'),
                    'Invalid unavailable source', 'invalid_agent_provider')
            continue
        source = entry['source']
        require(entry['code'] == 'ok' and idea is not None, 'Invalid available source', 'invalid_agent_provider')
        _exact(source, ('accepted_revision', 'draft_version', 'data', 'source_digest'), 'proposal source')
        integer(source['accepted_revision'], 'source revision', 1); integer(source['draft_version'], 'source draft version')
        if operation == 'assessment':
            try:
                expected_digest = assessment_digest(operation,
                    {key: source[key] for key in ('accepted_revision', 'draft_version', 'data')},
                    idea_id=idea['idea_id'])
            except IdeaError as exc:
                raise IdeaError('invalid_agent_provider', 'Invalid assessment source projection') from exc
        else:
            expected_digest = source_digest(operation, source['accepted_revision'], source['data'])
        require(source['accepted_revision'] == idea['revision'] and
                source['draft_version'] == idea.get('workflow', empty_workflow())['draft_version'] and
                source['source_digest'] == expected_digest,
                'Invalid current source projection', 'invalid_agent_provider')
    require(len(json.dumps(sources, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                           allow_nan=False).encode('utf-8')) <= 512 * 1024,
            'Source projection exceeds capacity', 'invalid_agent_provider')
    proposals = value['proposals']
    require(type(proposals) is list and len(proposals) <= 128 and (idea is not None or not proposals),
            'Invalid proposal inventory projection', 'invalid_agent_provider')
    inventory = value['proposal_inventory']
    _exact(inventory, ('total', 'projected', 'omitted', 'content_omitted', 'index_path'), 'proposal inventory')
    for name in ('total', 'projected', 'omitted', 'content_omitted'):
        integer(inventory[name], 'proposal inventory '+name)
    require(inventory['projected'] == len(proposals) and
            inventory['total'] == inventory['projected'] + inventory['omitted'] and
            inventory['content_omitted'] <= inventory['projected'] and
            inventory['index_path'] == (idea['idea_id']+'.md' if idea is not None else None) and
            (idea is not None or inventory['total'] == 0),
            'Invalid proposal inventory counts or index', 'invalid_agent_provider')
    require(len(json.dumps(dict(proposals=proposals, proposal_inventory=inventory),
                           ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                           allow_nan=False).encode('utf-8')) <= 256 * 1024,
            'Proposal projection exceeds capacity', 'invalid_agent_provider')
    identifiers = set()
    for proposal in proposals:
        _exact(proposal, ('proposal_id', 'request_id', 'operation', 'accepted_revision', 'draft_version', 'source_digest',
                         'proposal', 'evidence', 'content_omitted', 'stale', 'stale_reason',
                         'acceptance_eligible', 'acceptance_reason'), 'proposal summary')
        _agent_id(proposal['proposal_id'], 'proposal')
        _request_id(proposal['request_id'])
        require(proposal['proposal_id'] not in identifiers, 'Duplicate projected proposal', 'invalid_agent_provider')
        identifiers.add(proposal['proposal_id'])
        operation = proposal['operation']
        require(type(operation) is str and operation in AGENT_OPERATIONS, 'Unsupported projected proposal', 'invalid_agent_provider')
        integer(proposal['accepted_revision'], 'proposal revision', 1); integer(proposal['draft_version'], 'proposal draft version')
        require(type(proposal['source_digest']) is str and re.fullmatch(r'[0-9a-f]{64}', proposal['source_digest']),
                'Invalid proposal digest', 'invalid_agent_provider')
        _exact(proposal['evidence'], ('path', 'sha256'), 'proposal evidence')
        path = proposal['evidence']['path']
        require(type(path) is str and 0 < len(path) <= 200 and
                re.fullmatch(r'history/'+re.escape(idea['idea_id'])+r'/metadata/[0-9a-f]{64}\.md', path) is not None and
                type(proposal['evidence']['sha256']) is str and
                re.fullmatch(r'[0-9a-f]{64}', proposal['evidence']['sha256']),
                'Invalid proposal evidence witness', 'invalid_agent_provider')
        omitted = proposal['content_omitted']
        require(type(omitted) is bool and (proposal['proposal'] is None) == omitted and
                (not omitted or (proposal['acceptance_eligible'] is False and
                                  proposal['acceptance_reason'] == 'projection_omitted')) and
                (omitted or proposal['acceptance_reason'] != 'projection_omitted'),
                'Invalid omitted proposal body', 'invalid_agent_provider')
        if not omitted:
            if operation == 'memory':
                validate_step_fields('method', {'memory': proposal['proposal']}, partial=True)
            elif operation == 'visual_brief':
                require(type(proposal['proposal']) is dict and set(proposal['proposal']) == {'prototype_skill'}
                        and proposal['proposal']['prototype_skill'] in ('available', 'unavailable'),
                        'Invalid visual brief projection', 'invalid_agent_provider')
            elif operation == 'assessment':
                try:
                    validate_assessment_proposal(proposal['proposal'])
                except IdeaError as exc:
                    raise IdeaError('invalid_agent_provider', 'Invalid assessment proposal projection') from exc
            elif operation == 'method':
                # A v3 method proposal is memory-only (R8): never the full step, never a selection.
                require(type(proposal['proposal']) is dict and set(proposal['proposal']) == {'memory'},
                        'Invalid method proposal projection', 'invalid_agent_provider')
                checked = validate_step_fields('method', proposal['proposal'], partial=True)
                check_memory_preference(checked['memory'])
            else:
                validate_step_fields(operation, proposal['proposal'])
        for flag, reason, positive in (('stale', 'stale_reason', False),
                                       ('acceptance_eligible', 'acceptance_reason', True)):
            require(type(proposal[flag]) is bool and
                    ((proposal[flag] == positive and proposal[reason] is None) or
                     (proposal[flag] != positive and type(proposal[reason]) is str and
                      (proposal[reason] in AGENT_REASONS or
                       (flag == 'acceptance_eligible' and omitted and proposal[reason] == 'projection_omitted')))),
                    'Invalid proposal eligibility projection', 'invalid_agent_provider')
        require(status == 'connected' or not proposal['acceptance_eligible'],
                'Disconnected proposal cannot be eligible', 'invalid_agent_provider')
    require(inventory['content_omitted'] == sum(item['content_omitted'] for item in proposals),
            'Proposal omitted-body count differs', 'invalid_agent_provider')
    return copy.deepcopy(value)


def default_workspace(value):
    """Validate the optional trusted default_workspace setting: None, or dict(name, path).

    The path must be absolute and an existing folder; anything else is a plain config error.
    Nothing here picks a folder: the owner names it in config.json.
    """
    if value is None:
        return None
    bad = lambda why: IdeaError('invalid_config', 'default_workspace '+why)
    if type(value) is not dict or set(value) != {'name', 'path'}:
        raise bad('must be an object with exactly name and path')
    name, path = value['name'], value['path']
    if type(name) is not str or not 1 <= len(name) <= 100 or not name.strip() or '\n' in name or '\r' in name:
        raise bad('name must be one line of 1 to 100 characters')
    if type(path) is not str or not path or '\0' in path or not os.path.isabs(path):
        raise bad('path must be an absolute folder path')
    if not Path(path).is_dir():
        raise bad('path must be an existing folder: '+path)
    return dict(name=name, path=path)


class Service:
    def __init__(self,store,config,trusted_context,*,handlers=None,workspace_resolver=None,handoff_provider=None,agent_provider=None):
        require(isinstance(trusted_context,TrustedContext), 'Trusted caller context is required')
        text(trusted_context.actor,'trusted actor',200)
        require(type(trusted_context.session_id) is str and re.fullmatch(r'session_[0-9a-f]{32}',trusted_context.session_id) is not None, 'Invalid durable session ID')
        if trusted_context.selected_idea_id is not None: check_id(trusted_context.selected_idea_id)
        require(type(config) is dict, 'Trusted configuration must be an object')
        self.store, self.config, self.context = store, copy.deepcopy(config), trusted_context
        handlers = {} if handlers is None else handlers
        require(type(handlers) is dict and set(handlers) <= ADVANCED_STEPS, 'Unsupported trusted handler registry')
        for step,handler in handlers.items():
            require(isinstance(handler,TrustedStepHandler) and callable(handler.validate)
                    and (handler.apply is None or callable(handler.apply)), 'Invalid trusted step handler: '+step)
            require(type(handler.extra_dependencies) is tuple and all(name in STEP_ORDER[:STEP_ORDER.index(step)] for name in handler.extra_dependencies), 'Invalid handler dependencies')
        require(workspace_resolver is None or callable(workspace_resolver), 'Invalid trusted workspace resolver')
        require(handoff_provider is None or callable(handoff_provider), 'Invalid trusted handoff provider')
        require(agent_provider is None or all(callable(getattr(agent_provider, name, None))
                for name in ('context', 'project', 'validate_acceptance')), 'Invalid trusted agent provider')
        self.handlers = dict(handlers)
        self.workspace_resolver, self.handoff_provider = workspace_resolver, handoff_provider
        self.agent_provider = agent_provider

    def _agent_context(self):
        """Capture before Store: provider may acquire policy/binding here only.

        agent_provider.context(detached TrustedContext) returns None before a
        binding exists, or exact {binding_id,generation,session_id,agent_status}.
        This is auth/context only; never reject stale proposal business here,
        because durable mutation receipt replay precedes that validation.
        project(state,idea|None,context,live) returns the fixed agent overlay,
        including bounded proposals with verified evidence path/hash witnesses,
        explicit omitted bodies/counts and the canonical proposal_inventory index.
        agent_generation=live.generation or None is a nonsecret
        incarnation marker that lets browser proposal waits detect a fast resume.
        validate_acceptance(state,idea,payload,source,context,live) runs for
        Exploration/Method/Assess before the packaged handler, under Store mutation lock.
        These last two callbacks are pure: no policy/binding acquisition, wait,
        publication or I/O. They receive the active transaction state/idea for
        Store's memory-only inventory accessor; other inputs are detached.
        """
        if self.agent_provider is None:
            return None
        live = self.agent_provider.context(copy.deepcopy(self.context))
        if live is None:
            return None
        _bounded_object(live)
        _exact(live, ('binding_id', 'generation', 'session_id', 'agent_status'), 'live agent context')
        for name, prefix in (('binding_id', 'binding'), ('generation', 'agent'), ('session_id', 'session')):
            _agent_id(live[name], prefix)
        require(live['session_id'] == self.context.session_id and type(live['agent_status']) is str and
                live['agent_status'] in ('connected', 'paused', 'disconnected'),
                'Invalid live agent context', 'invalid_agent_provider')
        return copy.deepcopy(live)

    def _agent_pure(self, method, state, idea, *arguments):
        args = copy.deepcopy(arguments)
        before = copy.deepcopy((state, args))
        result = getattr(self.agent_provider, method)(state, idea, *args)
        require((state, args) == before, 'Agent provider must not mutate callback inputs', 'invalid_agent_provider')
        return result

    def _workspace(self,workspace):
        # Original validated payload is hashed, not the resolved filesystem view.
        result = copy.deepcopy(workspace)
        if self.workspace_resolver is not None:
            result = self.workspace_resolver(copy.deepcopy(workspace))
            _workspace_shape(result)
        path = Path(result['path']).expanduser()
        require(path.is_absolute(), 'Workspace path must be an explicit service-host absolute path', 'workspace_unavailable')
        try:
            path = path.resolve(strict=True)
        except (OSError,RuntimeError) as exc:
            raise IdeaError('workspace_unavailable','Workspace is unavailable on the service host') from exc
        require(path.is_dir(), 'Workspace must be an existing service-host directory', 'workspace_unavailable')
        return dict(result,path=str(path))

    def _idea(self,state,idea_id):
        require(idea_id in state['ideas'], 'Unknown idea', 'not_found')
        return state['ideas'][idea_id]

    def _live_idea(self,state,idea_id):
        """The idea for a browser write: a moved or delivered idea lives in its project, so it is refused first."""
        idea = self._idea(state,idea_id)
        info = self.store.lifecycle(idea_id)
        if info['lifecycle'] != 'active':
            raise IdeaError('idea_moved','This idea moved to a workspace; edit its file there',home=info['home'])
        return idea

    def _mutate(self,operation,payload,callback):
        result = self.store.mutate(self.context.session_id,payload['request_id'],dict(operation=operation,payload=payload),callback)
        if result.get('idea_id') is not None: self.context.selected_idea_id = result['idea_id']
        return result

    def state(self,idea_id=None):
        if idea_id is not None: check_id(idea_id)
        selected = idea_id if idea_id is not None else self.context.selected_idea_id
        live = self._agent_context()
        with self.store.transaction() as state:
            # Lock-scoped Store validator; never silently recreate a resumed ID.
            self.store._read_receipts(self.context.session_id)
            handoff_view = dict(handoff=None,handoff_status=dict(available=False,
                code='no_selection' if selected is None else 'operation_unavailable'))
            if selected is not None:
                idea = self._idea(state,selected)
                provider = self.handoff_provider
                if callable(getattr(provider,'project',None)):
                    handoff_view = provider.project(state,idea,self.context)
                    _exact(handoff_view,('handoff','handoff_status'),'handoff projection')
                    _exact(handoff_view['handoff_status'],('available','code'),'handoff status')
                    require(type(handoff_view['handoff_status']['available']) is bool
                            and type(handoff_view['handoff_status']['code']) is str,'Invalid handoff status')
                    public = handoff_view['handoff']
                    require(public is None or type(public) is dict,'Invalid public handoff')
                    require(not handoff_view['handoff_status']['available'] or public is not None,
                            'Current handoff requires its packet')
                    handoff = ({name:public[name] for name in ('handoff_id','source_revision')}
                               if handoff_view['handoff_status']['available'] else None)
                else:
                    # Retain the existing trusted three-argument callable seam.
                    handoff = provider(state,idea,self.context) if provider else None
                projection = derive_state(idea,handoff=handoff)
                if handoff_view['handoff'] is not None and not handoff_view['handoff_status']['available']:
                    historical = handoff_view['handoff']
                    projection['steps']['review'] = dict(status='review-needed',
                        accepted_revision=historical['source_revision'],evidence_id=historical['handoff_id'])
            else:
                idea = None
                workflow = empty_workflow()
                projection = dict(revision=0,draft_version=0,hand={name:False for name in HAND_STEPS},current_step='capture',
                    steps={step:dict(status='current' if step=='capture' else 'todo',accepted_revision=None,evidence_id=None) for step in STEP_ORDER},
                    accepted={step:None for step in STEP_ORDER},drafts=workflow['drafts'],draft=None)
            result = dict(ok=True,code='ok',session_id=self.context.session_id,idea_id=selected,
                          idea_status=idea['status'] if idea is not None else None,
                          backlog_revision=state['backlog_revision'],agent_status='disconnected',
                          capabilities=dict(agent=False,memory=False,uploads=False,handoff=self.handoff_provider is not None),
                          resume=dict(required=True,reason='agent_disconnected'),**projection)
            result.update(copy.deepcopy(handoff_view))
            result.update(default_workspace=copy.deepcopy(self.config.get('default_workspace')),
                          lifecycle=None, home=None, delivered_ref=None)
            if idea is not None:
                result.update(lifecycle_view(self.store.lifecycle(idea['idea_id'])))
            result.update(backlog=None, backlog_status=dict(available=False, code='no_selection'),
                          human_ratings=None, assessment_summary=None)
            if idea is not None:
                # Store has validated the domain records before this projection;
                # preserve selected observations even when the backlog is too big.
                result.update(human_ratings=copy.deepcopy(idea['ratings']),
                              assessment_summary=copy.deepcopy(idea['assessments'][-1]) if idea['assessments'] else None)
                try:
                    backlog = backlog_projection(state, idea['idea_id'])
                except IdeaError as exc:
                    if exc.code != 'too_large':
                        raise
                    result['backlog_status'] = dict(available=False, code='source_too_large')
                else:
                    result.update(backlog=backlog, backlog_status=dict(available=True, code='ok'))
            if self.agent_provider is not None:
                overlay = _agent_overlay(self._agent_pure('project', state, idea, self.context, live), live, idea)
                capabilities = dict(result['capabilities'], **overlay.pop('capabilities'))
                result.update(overlay, capabilities=capabilities)
            result.update(asset_inventory=None,asset_inventory_status=dict(available=False,code='no_selection'))
            if idea is not None:
                inventory = self.store.asset_inventory(state,idea['idea_id'])
                total = len(inventory['records'])
                result.update(asset_inventory=dict(inventory,total=total,projected=total,omitted=0),
                              asset_inventory_status=dict(available=True,code='ok'))
                if not _fits_state_response(result):
                    # Entire immutable records are withheld together. A partial
                    # membership list must never masquerade as an eligible set.
                    result['asset_inventory'] = dict(records=[],total=total,projected=0,omitted=total,
                                                     orphans=inventory['orphans'])
                    result['asset_inventory_status'] = dict(available=False,code='asset_projection_capacity')
        self.context.selected_idea_id = selected
        return result

    def request_result(self,request_id):
        _request_id(request_id)
        result = self.store.request_result(self.context.session_id,request_id)
        if 'handoff_index' in result:
            reconcile = getattr(self.handoff_provider,'reconcile',None)
            require(callable(reconcile),'Packaged handoff delivery is unavailable','operation_unavailable')
            result = reconcile(result,self.context,request_id)
        if result.get('idea_id') is not None: self.context.selected_idea_id = result['idea_id']
        return result

    def capture(self,payload):
        _bounded_object(payload)
        _exact(payload,('request_id','raw_text','workspace'),'capture')
        _request_id(payload['request_id']); text(payload['raw_text'],'original text',MAX_INPUT)
        _workspace_shape(payload['workspace'])
        payload = copy.deepcopy(payload)
        def mutate(state):
            workspace = self._workspace(payload['workspace'])
            actor, timestamp = self.context.actor, now()
            original = payload['raw_text']; raw = original.encode()
            idea = dict(idea_id=identity('idea'),revision=1,status='active',
                origin=dict(text=original,sha256=digest(raw),actor=actor,timestamp=timestamp),
                shape=None,ratings=None,assessments=[],revisions=[],proposals=[],plans=[],executions=[])
            idea['revisions'] = [snapshot(idea,actor,'capture')]
            fields = dict(raw_text=original,workspace=workspace)
            idea = capture_workflow(idea,fields,new_capture=True,actor=actor,timestamp=timestamp,
                evidence_id=identity('evidence'),source_digest=source_digest('capture',1,{'capture':fields}))['idea']
            _append_capture(state, idea, actor)
            return dict(idea_id=idea['idea_id'])
        return self._mutate('capture',payload,mutate)

    def _edit_payload(self,payload,accept=False):
        _bounded_object(payload)
        keys = {'request_id','idea_id','expected_revision','expected_draft_version','step','fields'}
        if accept: keys |= {'proposal_id','expected_backlog_revision'}
        _exact(payload,keys,'accept' if accept else 'draft')
        _request_id(payload['request_id']); check_id(payload['idea_id'])
        integer(payload['expected_revision'],'expected revision',1)
        integer(payload['expected_draft_version'],'expected draft version')
        validate_step_fields(payload['step'],payload['fields'],partial=True)
        if accept:
            proposal = payload['proposal_id']
            if proposal is not None:
                require(type(proposal) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}',proposal) is not None, 'Invalid proposal ID')
            if payload['expected_backlog_revision'] is not None: integer(payload['expected_backlog_revision'],'expected backlog revision')
        return copy.deepcopy(payload)

    def draft(self,payload):
        payload = self._edit_payload(payload)
        def mutate(state):
            idea = self._live_idea(state,payload['idea_id'])
            reduced = save_draft(idea,payload['step'],payload['fields'],expected_revision=payload['expected_revision'],expected_draft_version=payload['expected_draft_version'])
            state['ideas'][idea['idea_id']] = reduced['idea']
            return dict(idea_id=idea['idea_id'])
        return self._mutate('draft',payload,mutate)

    def release(self,payload):
        """Hand release (R3): the human takes a terminal-guided step by hand.

        Cancels only that step's open agent request (the binding stays usable) and persists
        the draft meta flag. After the step is accepted this is a no-op.
        """
        _bounded_object(payload)
        _exact(payload,('step',),'release')
        step = payload['step']
        require(type(step) is str and step in HAND_STEPS, 'Only discovery or exploration can be taken by hand', 'invalid_input')
        idea_id = self.context.selected_idea_id
        require(idea_id is not None, 'No idea selected', 'invalid_input')
        live = self._agent_context()
        with self.store.transaction(write=True) as state:
            idea = self._live_idea(state,idea_id)
            reduced = set_hand(idea,step)
            changed = reduced['changed']
            if changed:
                state['ideas'][idea_id] = reduced['idea']
                self.store.commit(state)
            accepted = idea.get('workflow',{}).get('steps',{}).get(step,{}).get('acceptance') is not None
            revision, draft_version = idea['revision'], reduced['idea']['workflow']['draft_version']
        released = 0
        release = getattr(self.agent_provider,'release',None)
        if not accepted and live is not None and callable(release):
            released = release(live,idea_id,step)
        return dict(ok=True,code='ok',idea_id=idea_id,step=step,hand=not accepted,released=released,
                    write_state='applied' if changed else 'no_op',revision=revision,draft_version=draft_version)

    def navigate(self,payload):
        """Persist explicit Pause through the same durable request seam."""
        _bounded_object(payload)
        _exact(payload,('request_id','idea_id','expected_revision','expected_draft_version','step'),'navigate')
        _request_id(payload['request_id']); check_id(payload['idea_id'])
        integer(payload['expected_revision'],'expected revision',1)
        integer(payload['expected_draft_version'],'expected draft version')
        require(type(payload['step']) is str and payload['step'] in STEP_ORDER, 'Unknown workflow step')
        payload = copy.deepcopy(payload)
        def mutate(state):
            idea = self._live_idea(state,payload['idea_id'])
            reduced = navigate_step(idea,payload['step'],expected_revision=payload['expected_revision'],
                                    expected_draft_version=payload['expected_draft_version'])
            state['ideas'][idea['idea_id']] = reduced['idea']
            return dict(idea_id=idea['idea_id'])
        return self._mutate('navigate',payload,mutate)

    def rerank(self,payload):
        """Move one idea in the backlog by the human's hand (redesign R7).

        Same semantics as the legacy Place command, through the durable request
        seam: backlog CAS, a placement record, and the moved idea's accepted Assess
        position refreshed to its new actual position for review. Ideas that only
        shifted are invalidated without a manufactured draft, as Capture does.
        The AI proposed position is never changed here.
        """
        _bounded_object(payload)
        _exact(payload,('request_id','idea_id','expected_backlog_revision','position','reason'),'rerank')
        _request_id(payload['request_id']); check_id(payload['idea_id'])
        integer(payload['expected_backlog_revision'],'expected backlog revision')
        integer(payload['position'],'position',1)
        require(payload['reason'] is None or (type(payload['reason']) is str and 0 < len(payload['reason'].strip()) <= 2000),
                'Invalid rerank reason')
        payload = copy.deepcopy(payload)
        actor = self.context.actor
        def mutate(state):
            idea = self._live_idea(state,payload['idea_id'])
            require(idea['status'] == 'active','Archived ideas keep their place','idea_archived')
            require(payload['expected_backlog_revision'] == state['backlog_revision'],'Stale backlog revision','stale_backlog')
            require(payload['position'] <= len(state['order']),'Position is outside the backlog')
            reason = payload['reason'].strip() if payload['reason'] is not None else 'Re-ranked from the backlog'
            before_order = list(state['order'])
            order = [key for key in before_order if key != idea['idea_id']]
            index = payload['position']-1
            choice = dict(idea_id=idea['idea_id'],idea_revision=idea['revision'],position=payload['position'],reason=reason,
                          actor=actor,timestamp=now(),source_backlog_revision=state['backlog_revision'],
                          neighbors=dict(before=order[index-1] if index else None,after=order[index] if index < len(order) else None),
                          snapshot=dict(ratings=copy.deepcopy(idea['ratings']),assessments=copy.deepcopy(idea['assessments'])))
            order.insert(index,idea['idea_id'])
            state['order'] = order
            state['backlog_revision'] += 1
            choice['accepted_backlog_revision'] = state['backlog_revision']
            state['placements'].append(choice)
            others = tuple(key for key in before_order if key != idea['idea_id'])
            _invalidate_placement(state,before_order,actor,'Another idea was re-ranked',exclude=(idea['idea_id'],),refresh_draft=False)
            _invalidate_placement(state,before_order,actor,reason,exclude=others,refresh_draft=True)
            return dict(idea_id=idea['idea_id'],position=payload['position'])
        return self._mutate('rerank',payload,mutate)

    def accept(self,payload):
        payload = self._edit_payload(payload,accept=True)
        live = self._agent_context()
        def mutate(state):
            prepared = payload
            if payload['step'] == 'capture':
                # Inherited Capture resolver/filesystem checks stay outside the
                # reducer and inside receipt replay, after idea/draft CAS.
                idea = self._live_idea(state,payload['idea_id'])
                require(payload['expected_revision'] == idea['revision'],'Stale idea revision','stale_revision')
                require(payload['expected_draft_version'] == idea.get('workflow',empty_workflow())['draft_version'],
                        'Stale draft version','stale_draft_version')
                fields = validate_step_fields('capture',payload['fields'])
                require(payload['proposal_id'] is None,'Manual step does not accept proposals')
                require(payload['expected_backlog_revision'] is None,'Non-placement acceptance has no backlog revision')
                fields['workspace'] = self._workspace(fields['workspace'])
                prepared = dict(payload,fields=fields)
            return self.accept_in_state(state,prepared,live)
        return self._mutate('accept',payload,mutate)

    def accept_in_state(self,state,validated_payload,live):
        """Reduce a validated acceptance inside the caller's Store callback.

        The caller captures live context before Store and owns durable replay.
        Capture alone prepares its workspace through the inherited bounded
        resolver/filesystem validation before this call. This reducer performs
        no filesystem resolution, publication, nested mutation or policy lock.
        Trusted provider/handler validation remains pure; apply composes changes
        only into this detached transaction state. TrustedContext carries no
        Store or file capability.
        """
        payload = validated_payload
        step = payload['step']; idea = self._live_idea(state,payload['idea_id'])
        require(step != 'review','Review is derived from a verified handoff packet','derived_step')
        if step != 'exploration':
            require(idea['status'] == 'active','Archived ideas require explicit Exploration acceptance first','idea_archived')
        require(payload['expected_revision'] == idea['revision'],'Stale idea revision','stale_revision')
        draft_version = idea.get('workflow',empty_workflow())['draft_version']
        require(payload['expected_draft_version'] == draft_version,'Stale draft version','stale_draft_version')
        handler = self.handlers.get(step)
        if step in ADVANCED_STEPS: require(handler is not None,'Step handler is unavailable','step_unavailable')
        if step in ('capture','priorities'):
            require(payload['proposal_id'] is None,'Manual step does not accept proposals')
            require(payload['expected_backlog_revision'] is None,'Non-placement acceptance has no backlog revision')
        elif step == 'assess':
            require(payload['expected_backlog_revision'] is not None,'Placement acceptance requires backlog revision')
            require(payload['expected_backlog_revision'] == state['backlog_revision'],'Stale backlog revision','stale_backlog')
        else: require(payload['expected_backlog_revision'] is None,'Non-placement acceptance has no backlog revision')
        fields = validate_step_fields(step,payload['fields'])
        dependencies = handler.extra_dependencies if handler else ()
        source = acceptance_source(idea,step,fields,dependencies)
        checked_payload = dict(payload,fields=fields)
        handler_context = self.context
        if step == 'visualize':
            handler_context = copy.deepcopy(self.context)
            handler_context.asset_inventory = self.store.asset_inventory(state,idea['idea_id'])
        if self.agent_provider is not None and step in ('discovery', 'exploration', 'method', 'assess'):
            self._agent_pure('validate_acceptance', state, idea, checked_payload, source, self.context, live)
        if handler:
            before = copy.deepcopy(state)
            handler.validate(state,idea,checked_payload,source,copy.deepcopy(handler_context) if step=='visualize' else handler_context)
            require(state == before,'Trusted handler validation must not mutate state','invalid_handler')
        reduced = accept_step(idea,step,fields,expected_revision=payload['expected_revision'],
            expected_draft_version=payload['expected_draft_version'],actor=self.context.actor,timestamp=now(),
            evidence_id=identity('evidence'),source_digest=source['source_digest'],extra_dependencies=dependencies)
        state['ideas'][idea['idea_id']] = reduced['idea']
        if handler and handler.apply is not None and reduced['accepted_changed']:
            handler.apply(state,reduced['idea'],checked_payload,source,copy.deepcopy(handler_context) if step=='visualize' else handler_context)
        result = dict(idea_id=idea['idea_id'],invalidated=reduced['invalidated'])
        if payload['proposal_id'] is not None:
            result['proposal_id'] = payload['proposal_id']
        return result

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


def _legacy_sources(idea,command):
    if command == 'rate':
        return {'priorities': {key:idea['ratings'][key] for key in ('urgency','importance')} if idea['ratings'] else None}
    if command == 'assess':
        value = idea['assessments'][-1] if idea['assessments'] else None
        from idea_domain import ASSESS_KEYS
        return {'assess':{'assessment':{key:value[key] for key in ASSESS_KEYS}} if value else None}
    return {}


def _ready_legacy(idea):
    """Planning readiness for a pre-workflow idea: ratings, an assessment and a complete exploration."""
    ready(idea)
    shaped=idea['shape']
    require(shaped is not None and all(shaped[k] for k in ('outcome','scope','scope_reason','alternatives','next_slice')),
            'Complete Exploration before planning','not_ready')


def _legacy_review(idea,prior,updates):
    """Expose CLI source changes as drafts, retaining browser receipts verbatim."""
    if 'workflow' not in idea or not updates: return idea
    workflow = idea['workflow']
    selected = workflow['current_step']
    for step,fields in updates.items():
        old_fields = prior['workflow']['steps'][step]['fields']
        existing = prior['workflow']['drafts'].get(step)
        command = 'rate' if step == 'priorities' else 'assess'
        mirrored = _legacy_sources(prior,command).get(step)
        unchanged_mirror = (mirrored is not None and existing is not None and set(mirrored) <= set(existing)
            and all(existing[key] == value for key,value in mirrored.items())
            and all(value == (old_fields or {}).get(key) for key,value in existing.items() if key not in mirrored))
        require(existing is None or existing == old_fields or unchanged_mirror,
                'Browser draft conflicts with CLI '+step+' edit; resolve the draft before retrying', 'draft_conflict')
        draft = copy.deepcopy(old_fields or {})
        draft.update(fields)
        reduced = save_draft(idea,step,draft,expected_revision=idea['revision'],expected_draft_version=workflow['draft_version'])
        idea = reduced['idea']; workflow = idea['workflow']
    workflow['current_step'] = selected
    return invalidate_external(idea,tuple(updates))['idea']


def _placement_position(order,key):
    index = order.index(key)
    return dict(actual_position=index+1,neighbors=dict(before=order[index-1] if index else None,after=order[index+1] if index+1<len(order) else None))


def _invalidate_placement(state,before_order,actor,reason,*,exclude=(),refresh_draft=True):
    """Invalidate changed placement evidence, preserving accepted receipts.

    Explicit legacy Place refreshes its position draft and retains the existing
    draft-conflict check. Capture and canonical Assess pass refresh_draft=False:
    another idea's placement change must not manufacture or edit a human draft.
    """
    for key,idea in list(state['ideas'].items()):
        if key in exclude or key not in before_order: continue
        # Archived Assess describes frozen historical evidence. Reordering does
        # not start a new slice; only explicit Exploration acceptance may reactivate that idea.
        if idea['status'] == 'archived': continue
        if 'workflow' not in idea or idea['workflow']['steps']['assess']['acceptance'] is None: continue
        before = _placement_position(before_order,key)
        after = _placement_position(state['order'],key)
        if before == after: continue
        if refresh_draft:
            prior = copy.deepcopy(idea)
            fields = copy.deepcopy(idea['workflow']['steps']['assess']['fields'])
            fields['position'].update(after)
            if fields['position']['proposed_position'] != after['actual_position']:
                fields['position']['override_reason'] = reason
            idea = _legacy_review(idea,prior,{'assess':fields})
        else:
            idea = invalidate_external(idea,('assess',))['idea']
        idea['revision'] += 1
        idea['status'] = 'active'
        idea['revisions'].append(adapt_snapshot(snapshot(idea,actor,'place'),idea['workflow']))
        state['ideas'][key] = idea


def _append_capture(state,idea,actor):
    """One append seam for both authenticated browser and legacy Capture."""
    before_order = list(state['order'])
    state['ideas'][idea['idea_id']] = idea
    state['order'].append(idea['idea_id'])
    state['backlog_revision'] += 1
    _invalidate_placement(state,before_order,actor,'A new captured idea changed the placement neighbors',
                          exclude=(idea['idea_id'],),refresh_draft=False)


MOVED_REFUSED=frozenset(('handoff','exploration','rate','assess','propose','place','record-execution'))


def _moved_health(store,state):
    """Health of ideas whose living file is in a workspace: (issues, notices). Reads only; writes nothing."""
    import idea_markdown as md
    issues,notices=[],[]
    for key,info in store.lifecycles(state).items():
        home=info['home']
        if home is None:
            continue
        if not Path(home['workspace_path']).is_dir():
            issues.append('Workspace folder is gone: '+home['workspace_path'])
            continue
        try:
            current=read_bytes(home['file_path'],MAX_STATE)
        except (OSError,IdeaError) as exc:
            absent=isinstance(exc,FileNotFoundError) or (isinstance(exc,IdeaError) and exc.code=='missing_artifact')
            issues.append(('Moved idea file is missing: ' if absent else 'Moved idea file is unreadable: ')+home['file_path'])
            continue
        pointer=md.decode_moved(read_bytes(store.path/md.pointer_path(key,'moved'),MAX_STATE))
        if digest(current)!=pointer['moved_sha256']:
            notices.append('Moved idea file changed since the move: '+home['file_path'])
    return issues,notices


def run_legacy(args,config):
    command=args.command
    if command=='shape':
        raise IdeaError('unsupported_command','The shape verb is retired; use exploration to reopen Exploration for the next slice')
    store=Store(args.store or config['store_path'])
    write=command not in ('list','show','handoff','doctor')
    if hasattr(args,'actor'):
        text(args.actor,'actor',200)
    if hasattr(args,'idea_id'):
        check_id(args.idea_id)
    with store.transaction(write=write) as state:
        if command=='list':
            views=store.lifecycles(state)
            return dict(backlog_revision=state['backlog_revision'],order=state['order'],ideas=[state['ideas'][key] for key in state['order']],
                        lifecycles={key:lifecycle_view(views[key]) for key in state['order']})
        if command in ('doctor','repair-views'):
            # Repair only ever rewrites views inside the store; a moved idea's home file is never touched.
            issues=store.view_issues(state,repair=command=='repair-views')+store.artifact_issues(state)
            moved_issues,notices=_moved_health(store,state)
            issues+=moved_issues
            if issues:
                raise IdeaError('unhealthy_store','Store health checks failed',issues=issues)
            result=dict(healthy=True,ideas=len(state['ideas']),transaction_revision=state['transaction_revision'])
            if notices:
                result['notices']=notices
            return result
        if command=='capture':
            raw=read_bytes(resolve_file(args.text_file))
            try:
                original=raw.decode('utf-8')
            except UnicodeError as exc:
                raise IdeaError('invalid_input','Capture text must be UTF-8') from exc
            text(original,'original text',MAX_INPUT)
            idea=dict(idea_id=identity('idea'),revision=1,status='active',origin=dict(text=original,sha256=digest(raw),actor=args.actor,timestamp=now()),shape=None,ratings=None,assessments=[],revisions=[],proposals=[],plans=[],executions=[])
            idea['revisions'].append(snapshot(idea,args.actor,'capture'))
            _append_capture(state,idea,args.actor)
            store.commit(state)
            return dict(idea=idea,backlog_revision=state['backlog_revision'])
        require(args.idea_id in state['ideas'],'Unknown idea: '+args.idea_id,'not_found')
        idea=state['ideas'][args.idea_id]
        if command=='deliver':
            delivery_ref(args.ref)
            result=store.deliver(state,idea['idea_id'],ref=args.ref,actor=args.actor)
            view=lifecycle_view(store.lifecycle(idea['idea_id']))
            return dict(dict(idea=state['ideas'][idea['idea_id']],delivery=result['delivery'],**view),**(dict(repeated=True) if result['repeated'] else {}))
        info=store.lifecycle(idea['idea_id'])
        view=lifecycle_view(info)
        if command=='show':
            return dict(idea=idea,**view)
        if command in MOVED_REFUSED and info['lifecycle']!='active':
            raise IdeaError('idea_moved','This idea moved to a workspace; edit its file there',home=info['home'])
        if command=='handoff':
            if 'workflow' in idea:
                # Same pure eligibility and trusted host evidence as publication.
                store.handoff_observations(state,idea['idea_id'])
            else:
                _ready_legacy(idea)
                require(idea['status']=='active','Reopen Exploration for the next slice before a new handoff','archived_revision')
            return dict(idea_id=idea['idea_id'],idea_revision=idea['revision'],trace_block='## Idea trace\nidea_id: '+idea['idea_id']+'\nidea_revision: '+str(idea['revision']),origin=idea['origin'],shape=idea['shape'],ratings=idea['ratings'],assessments=idea['assessments'],plans=idea['plans'],executions=idea['executions'])
        if hasattr(args,'expected_revision'):
            integer(args.expected_revision,'expected revision',1)
            require(args.expected_revision==idea['revision'],'Stale idea revision; show current idea before retrying','stale_revision')
        if command in ('exploration','rate','assess'):
            require(command=='exploration' or idea['status']=='active','Archived revision is immutable; reopen Exploration for the next slice first','archived_revision')
            prior = copy.deepcopy(idea)
            if command=='exploration':
                explored=validate_step_fields('exploration',read_json(args.file))
                if 'workflow' not in idea:
                    # A pre-workflow idea has no drafts: its retired shape record carries the exploration.
                    idea['shape']=shape(dict(outcome=explored['outcome'],scope=explored['scope'],scope_reason=explored['scope_reason'],
                        alternatives=copy.deepcopy(explored['alternatives']),method=None,method_reason=None,
                        assumptions=list(explored['assumptions']),next_slice=explored['next_slice'],learning=list(explored['learning'])))
                idea['status']='active'
            elif command=='rate':
                integer(args.urgency,'urgency',1,10)
                integer(args.importance,'importance',1,10)
                idea['ratings']=dict(urgency=args.urgency,importance=args.importance,actor=args.actor,timestamp=now())
            else:
                value=assessment(read_json(args.file))
                value.update(assessment_id=identity('assessment'),actor=args.actor,timestamp=now())
                idea['assessments'].append(value)
            previous_sources, current_sources = _legacy_sources(prior,command), _legacy_sources(idea,command)
            updates = {step:fields for step,fields in current_sources.items() if fields != previous_sources[step]}
            if command == 'exploration':
                # Explicit next-slice intent: the exploration becomes the draft and reopens the step,
                # even on identical inputs. Accepted receipts stay verbatim.
                updates['exploration'] = explored
            idea = _legacy_review(idea,prior,updates)
            if command=='exploration' and prior['status']=='archived' and 'workflow' in idea:
                idea['workflow']['current_step']='exploration'  # the next slice opens where the human works on it
            idea['revision']+=1
            idea['revisions'].append(adapt_snapshot(snapshot(idea,args.actor,command),idea.get('workflow')))
            state['ideas'][idea['idea_id']] = idea
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
            before_order = list(state['order'])
            order.insert(index,idea['idea_id'])
            state['order']=order
            state['backlog_revision']+=1
            choice['accepted_backlog_revision']=state['backlog_revision']
            state['placements'].append(choice)
            _invalidate_placement(state,before_order,args.actor,args.reason)
            store.commit(state)
            return dict(placement=choice,backlog_revision=state['backlog_revision'],order=order)
        if command=='register-plan':
            name,wpath=getattr(args,'workspace_name',None),getattr(args,'workspace_path',None)
            require((name is None)==(wpath is None),'--workspace-name and --workspace-path must be given together or not at all')
            moving=name is not None
            if moving:
                text(name,'workspace name',100)
                text(wpath,'workspace path',4096)
                chosen=config.get('default_workspace')
                if chosen is not None and os.path.isabs(wpath) and os.path.realpath(wpath)==os.path.realpath(chosen['path']):
                    raise IdeaError('same_workspace','The target is the default workspace; an idea only moves to a different workspace',workspace_path=chosen['path'])
            if info['lifecycle']!='active':
                # Replaying the exact move is repeated; anything else on a moved idea is refused.
                path=resolve_file(args.path)
                home=info['home']
                prior=next((p for p in idea['plans'] if p['source_path']==str(path) and p['idea_revision']==idea['revision']),None)
                if moving and prior is not None and home['workspace_name']==name and home['workspace_path']==str(Path(wpath)):
                    verify_current(prior)
                    return dict(plan=prior,idea=idea,repeated=True,**view)
                raise IdeaError('idea_moved','This idea moved to a workspace; edit its file there',home=home)
            if 'workflow' not in idea:
                _ready_legacy(idea)
            path=resolve_file(args.path)
            raw=read_bytes(path)
            hashed=digest(raw)
            for prior in ([] if moving else idea['plans']):
                if prior['source_path']==str(path):
                    verify_current(prior)
                    require(prior['idea_revision']==idea['revision'],'A plan file already links a different revision; use a new file','link_conflict')
                    require(prior['sha256']==hashed,'Working plan differs from the accepted evidence; use a new file for a new plan','changed_artifact')
                    issues=store.view_issues(state)
                    if issues:
                        raise IdeaError('archive_view_failed','Plan already registered; repair archive views',committed=True,issues=issues,plan=prior)
                    return dict(plan=prior,idea=idea,repeated=True,**view)
            if 'workflow' in idea:
                # Exact immutable replay above remains valid after archival or
                # workspace drift; only a new plan needs current eligibility.
                # Copying/publishing a packet is deliberately not a prerequisite.
                store.handoff_observations(state,idea['idea_id'])
            validation=validate_plan(path,raw,idea,config)
            plan_id=identity('plan')
            frozen_path=store.path/'plan-evidence'/(plan_id+'.md')
            plan=dict(plan_id=plan_id,idea_id=idea['idea_id'],idea_revision=idea['revision'],path=str(frozen_path),source_path=str(path),content=raw.decode('utf-8'),sha256=hashed,actor=args.actor,timestamp=now(),validation=validation)
            if moving:
                moved=store.move_out(state,idea['idea_id'],expected_revision=args.expected_revision,plan=plan,workspace_name=name,workspace_path=wpath,actor=args.actor)
                issues=store.view_issues(state,repair=True)
                if issues:
                    raise IdeaError('archive_view_failed','Plan committed and idea moved; archive materialization needs repair-views',committed=True,issues=issues,plan=plan)
                return dict(plan=plan,idea=state['ideas'][idea['idea_id']],moved_sha256=moved['moved_sha256'],resumed=moved['resumed'],
                            **lifecycle_view(store.lifecycle(idea['idea_id'])))
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
