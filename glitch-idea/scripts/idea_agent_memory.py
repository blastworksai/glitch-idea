"""Current initiating-agent memory result adapter.

No retrieval, filesystem, model call, Store transaction or second memory store.
The agent supplies an actual retrieval result, or an honest unavailable/error.
Safe references and concise rationale enter existing immutable proposals only.
Authenticated proposal provenance proves who recorded a claim, not the external
reference's truth. Retrieval grounding still depends on the initiating agent.
"""
import copy
import re

from idea_domain import integer, require
from idea_agent_source import (INPUTS, prepare_source, validate_memory as typed_memory,
                               validate_proposal as typed_proposal)
from idea_proposal_evidence import validate_record

MAX_SOURCES = 16
MAX_REFERENCE = 256
MAX_RATIONALE = 2048
NAMESPACES = frozenset(('memory', 'record', 'note', 'meeting', 'wiki', 'decision'))


def _reference(value):
    """Opaque ID or confined relative reference; never a machine-private path."""
    require(type(value) is str and 0 < len(value) <= MAX_REFERENCE
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/#-]*', value), 'Invalid memory reference')
    require('\\' not in value and not value.startswith('/') and '//' not in value,
            'Memory reference must be an opaque or relative reference')
    require('memory-private' not in value.lower(), 'Private seat memory reference refused')
    if ':' in value:
        namespace, reference = value.split(':', 1)
        require(namespace in NAMESPACES and bool(reference) and ':' not in reference
                and not reference.startswith('/'), 'Unsupported memory reference namespace')
    else:
        reference = value
    require(all(part not in ('', '.', '..') for part in reference.split('/')),
            'Memory reference cannot traverse a path')


def validate_result(result):
    """Exact existing memory schema; no inference about whether a search ran.

    found requires actual safe source references and rationale. A completed
    search with no preference is searched_no_preference, even with zero refs.
    No capability is unavailable; a failed retrieval is error. This function
    validates claims, it does not manufacture those observations.
    """
    checked = typed_memory(result)
    require(len(checked['sources']) <= MAX_SOURCES, 'Too many memory references', 'too_large')
    require(len(set(checked['sources'])) == len(checked['sources']), 'Duplicate memory references')
    for reference in checked['sources']:
        _reference(reference)
    rationale = checked['rationale']
    require(rationale is None or len(rationale) <= MAX_RATIONALE,
            'Memory rationale must be concise', 'too_large')
    return checked


def validate_proposal(operation, proposal):
    """Inject into Broker; retain the Discovery/Exploration/Method/Memory schemas."""
    checked = typed_proposal(operation, proposal)
    if operation == 'memory':
        validate_result(checked)
    elif operation == 'method':
        validate_result(checked['memory'])
    return checked


def _live(live):
    require(type(live) is dict and set(live) ==
            {'binding_id', 'generation', 'session_id', 'agent_status'}, 'Invalid memory live binding')
    for name, prefix in (('binding_id', 'binding'), ('generation', 'agent'), ('session_id', 'session')):
        require(type(live[name]) is str and re.fullmatch(prefix+r'_[0-9a-f]{32}', live[name]),
                'Invalid memory binding identity')
    require(type(live['agent_status']) is str and live['agent_status'] in
            ('connected', 'paused', 'disconnected'), 'Invalid memory agent status')


def _eligible(state, idea, record, context, live):
    if record['idea_id'] != idea['idea_id'] or record['actor'] != context.actor:
        return False
    if (record['session_id'] != context.session_id or record['session_id'] != live['session_id']
            or record['binding_id'] != live['binding_id'] or record['generation'] != live['generation']
            or live['agent_status'] != 'connected' or record['accepted_revision'] != idea['revision']):
        return False
    # The inputs a memory search consumes: the same derived set prepare_source reads for it.
    required = set(INPUTS['memory'])
    allowed = required | ({'method'} if record['operation'] == 'method' else set())
    require(required <= set(record['data']) <= allowed, 'Unexpected memory consumed input map')
    # A changed Capture/Priorities draft makes prepare_source refuse with not_ready;
    # it is a real readiness failure, never converted into a successful search.
    current = prepare_source(state, idea['idea_id'], 'memory')
    return all(record['data'][step] == current['data'][step] for step in required)


def _filled(idea, memory, conversation):
    """True when memory equals the LAST memory the connected agent filled into this idea's open Method request.

    The conversation is read for the live binding and current generation, so an earlier
    generation, a released/expired/answered request or another revision never reaches here.
    """
    if (type(conversation) is not dict or conversation.get('operation') != 'method'
            or conversation.get('idea_id') != idea['idea_id']
            or conversation.get('accepted_revision') != idea['revision']):
        return False
    filled = [item['fields']['memory'] for item in conversation.get('fills', ())
              if type(item) is dict and type(item.get('fields')) is dict and 'memory' in item['fields']]
    return bool(filled) and validate_result(filled[-1]) == memory


def validate_method_memory(state, idea, fields, context, *, live_binding,
                           proposals, accepted_proposal=None, conversation=None):
    """Provider seam under existing Store lock, accepting human Method choice.

    proposals is Store.agent_proposals(state,idea_id)'s verified detached list.
    accepted_proposal is SourceAdapter.validate_acceptance's Method result, when
    a proposal was explicitly linked. conversation is the live Broker's view of the
    open request (Broker.conversation for the live binding and generation). None of
    these arguments is browser-selected data.
    Only memory is checked here; Method choice/reason/investment/experiment may
    differ from the recommendation. No accepted state or input is mutated.
    """
    require(type(idea) is dict and idea is state['ideas'].get(idea.get('idea_id')),
            'Memory validation requires current idea', 'invalid_handler')
    require(type(fields) is dict and 'memory' in fields, 'Missing Method memory')
    memory = validate_result(fields['memory'])
    require(type(proposals) is list and len(proposals) <= 128, 'Invalid memory evidence inventory')
    require(type(context.actor) is str and type(context.session_id) is str, 'Invalid memory caller')
    integer(idea['revision'], 'idea revision', 1)
    if live_binding is not None:
        _live(live_binding)
        require(context.session_id == live_binding['session_id'], 'Memory session differs', 'wrong_session')
    if accepted_proposal is None and memory['status'] in ('unavailable', 'error'):
        # Manual choices with no claimed retrieval can proceed disconnected.
        # Do not upgrade either status to a completed search or a preference.
        return memory
    require(live_binding is not None and live_binding['agent_status'] == 'connected',
            'Current memory proposal requires a connected agent', 'agent_unavailable')
    checked = [validate_record(record) for record in proposals]
    if accepted_proposal is not None:
        selected = validate_record(accepted_proposal)
        require(selected['operation'] == 'method', 'Linked memory is not a Method proposal', 'memory_provenance_missing')
        require(sum(record == selected for record in checked) == 1,
                'Linked Method proposal lacks verified inventory evidence', 'memory_provenance_missing')
        checked = [selected]
    for record in checked:
        if record['operation'] not in ('memory', 'method'):
            continue
        recorded = validate_result(record['proposal'] if record['operation'] == 'memory'
                                   else record['proposal']['memory'])
        if recorded == memory and _eligible(state, idea, record, context, live_binding):
            return copy.deepcopy(memory)
    if _filled(idea, memory, conversation):
        return copy.deepcopy(memory)
    require(False, 'Method memory lacks matching current agent evidence', 'memory_provenance_missing')
