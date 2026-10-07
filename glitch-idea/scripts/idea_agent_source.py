"""Trusted current-agent sources and human acceptance seam.

Pure functions select only fixed typed workflow inputs. SourceAdapter's only
effect is Store.agent_proposals: a detached memory-only inventory accessor under
an existing transaction. Callers capture live binding context BEFORE Store;
this module never acquires policy/binding locks, publishes, waits or retrieves
memory. Memory validation establishes schema only, not retrieval provenance.
"""
import copy
import json
import math
import re

from idea_domain import IdeaError, MAX_INPUT, check_id, integer, require, text
from idea_proposal_evidence import validate_record
from idea_assessment import (assessment_digest, prepare_assessment_source,
    validate_assessment_source, validate_data as validate_assessment_data,
    validate_assessment_proposal, validate_actual_position)
from idea_workflow import (DISCOVERY_BEFORE_METHODS, MEMORY_STATUSES, acceptance_source as final_source,
    check_memory_preference, derive_dependencies, derive_state, import_workflow, source_digest,
    validate_step_fields)

OPERATIONS = ('discovery', 'exploration', 'memory', 'method', 'visual_brief', 'assessment')
# Consumed inputs come from the workflow's one dependency table, never a second hand-kept order.
_DEPENDENCIES = derive_dependencies(DISCOVERY_BEFORE_METHODS)
INPUTS = {'discovery': _DEPENDENCIES['discovery'], 'exploration': _DEPENDENCIES['exploration'],
          'memory': _DEPENDENCIES['method'], 'method': _DEPENDENCIES['method'],
          'visual_brief': _DEPENDENCIES['visualize']}
# Steps whose own draft rides along in the source as the editable target.
TARGETS = ('discovery', 'exploration', 'method')
CORRELATION = frozenset(('request_id', 'session_id', 'idea_id', 'accepted_revision',
                         'draft_version', 'operation', 'source_digest'))
SOURCE_KEYS = frozenset(('accepted_revision', 'draft_version', 'data'))
MAX_PROJECTIONS = 128
MAX_PROJECTION_BYTES = 256 * 1024
MAX_SOURCE_PROJECTION_BYTES = 512 * 1024


def _bounded(value):
    # Match the broker's strictest recursive envelope bounds before copying.
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop(); count += 1
        require(depth <= 24 and count <= 20000, 'Agent source exceeds tree limits', 'too_large')
        require(type(item) in (dict, list, str, int, float, bool, type(None)), 'Unsupported source value')
        if type(item) is dict:
            require(all(type(key) is str for key in item), 'Source keys must be strings')
            pending.extend((child, depth+1) for pair in item.items() for child in pair)
        elif type(item) is list:
            pending.extend((child, depth+1) for child in item)
        elif type(item) is str:
            require(not any(0xD800 <= ord(char) <= 0xDFFF for char in item), 'Invalid source Unicode')
        elif type(item) is float:
            require(math.isfinite(item), 'Nonfinite source value')
        elif type(item) is int:
            require(abs(item) <= 10**12, 'Source integer exceeds limit')
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'), allow_nan=False).encode('utf-8')
    require(len(raw) <= MAX_INPUT, 'Agent source exceeds 1 MiB', 'too_large')
    return raw


def _exact(value, keys, name):
    require(type(value) is dict and set(value) == set(keys), 'Unexpected '+name+' fields')


def _id(value, prefix):
    require(type(value) is str and re.fullmatch(prefix+r'_[0-9a-f]{32}', value), 'Invalid '+prefix+' ID')


def _operation(operation):
    require(type(operation) is str and operation in OPERATIONS,
            'Agent source operation is unavailable', 'operation_unavailable')


def _idea(state, idea_id):
    check_id(idea_id)
    require(type(state) is dict and type(state.get('ideas')) is dict, 'Invalid source state')
    require(idea_id in state['ideas'], 'Unknown source idea', 'not_found')
    idea = state['ideas'][idea_id]
    require(idea.get('idea_id') == idea_id, 'Source idea identity differs', 'corrupt_store')
    return import_workflow(idea)


def _data(operation, data):
    if operation == 'assessment':
        validate_assessment_data(data)
        return
    require(type(data) is dict, 'Source data must be a step map')
    required = set(INPUTS[operation])
    optional = {operation} if operation in TARGETS else set()
    require(required <= set(data) <= required | optional, 'Unexpected consumed source steps')
    for step, fields in data.items():
        validate_step_fields(step, fields, partial=step in optional, legacy=True)


def _source(operation, source, idea_id=None):
    if operation == 'assessment':
        validate_assessment_source(source, idea_id=idea_id)
        return
    _bounded(source); _exact(source, SOURCE_KEYS, 'source')
    integer(source['accepted_revision'], 'accepted revision', 1)
    integer(source['draft_version'], 'draft version')
    _data(operation, source['data'])


def proposal_source_digest(operation, source, *, idea_id=None):
    """Assessment-aware injected Broker callback; old digests stay unchanged.

    OwnerService installs this callback in its later composition artifact. The
    codec and response validator additionally pass the exact recorded idea ID.
    """
    _operation(operation)
    _source(operation, source, idea_id)
    if operation == 'assessment':
        return assessment_digest(operation, source, idea_id=idea_id)
    return source_digest(operation, source['accepted_revision'], source['data'])


def _correlation(correlation):
    _bounded(correlation); _exact(correlation, CORRELATION, 'correlation')
    _operation(correlation['operation'])
    _id(correlation['session_id'], 'session'); check_id(correlation['idea_id'])
    require(type(correlation['request_id']) is str and
            re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', correlation['request_id']), 'Invalid request ID')
    integer(correlation['accepted_revision'], 'accepted revision', 1)
    integer(correlation['draft_version'], 'draft version')
    require(type(correlation['source_digest']) is str and
            re.fullmatch(r'[0-9a-f]{64}', correlation['source_digest']), 'Invalid source digest')


def prepare_source(state, idea_id, operation):
    """Detached exact Broker.source; optional target is a whole durable buffer."""
    _operation(operation)
    if operation == 'assessment':
        return prepare_assessment_source(state, idea_id)
    idea = _idea(state, idea_id); workflow = idea['workflow']
    statuses = derive_state(idea)['steps']
    data = {}
    for step in INPUTS[operation]:
        require(statuses[step]['status'] == 'saved', 'Current acceptance required for '+step, 'not_ready')
        data[step] = copy.deepcopy(workflow['steps'][step]['fields'])
    if operation in TARGETS:
        if operation in workflow['drafts']:
            data[operation] = copy.deepcopy(workflow['drafts'][operation])
        elif workflow['steps'][operation]['fields'] is not None:
            data[operation] = copy.deepcopy(workflow['steps'][operation]['fields'])
    source = dict(accepted_revision=idea['revision'], draft_version=workflow['draft_version'], data=data)
    _source(operation, source)
    return source


def validate_source(state, correlation, source):
    """Exact response CAS: source versions and all consumed/target inputs."""
    _correlation(correlation); operation = correlation['operation']
    _source(operation, source, correlation['idea_id'])
    require(all(source[name] == correlation[name] for name in ('accepted_revision', 'draft_version')),
            'Source correlation differs', 'stale_source')
    require(proposal_source_digest(operation, source, idea_id=correlation['idea_id']) == correlation['source_digest'],
            'Original source digest differs', 'stale_source')
    try:
        current = prepare_source(state, correlation['idea_id'], operation)
    except IdeaError as exc:
        if exc.code != 'not_ready':
            raise
        raise IdeaError('stale_source', 'Consumed inputs are no longer current') from exc
    require(_bounded(current) == _bounded(source), 'Agent sources changed', 'stale_source')
    return copy.deepcopy(source)


# The workflow step each fillable operation's page buffer (and so its saved draft) belongs to.
FILL_STEP = {'discovery': 'discovery', 'exploration': 'exploration', 'method': 'method',
             'assessment': 'assess', 'visual_brief': 'visualize'}


def own_fill_base(state, idea_id, operation):
    """The target draft as it stands when a request is enqueued (None: no saved draft yet).

    Captured by the trusted launch path from the Store state, never from the agent: it is the
    base the request's own fills are later applied to when a reply is reconciled.
    """
    step = FILL_STEP.get(operation)
    if step is None or idea_id not in state['ideas']:
        return None
    drafts = _idea(state, idea_id)['workflow']['drafts']
    return copy.deepcopy(drafts[step]) if step in drafts else None


def _blank(value):
    """An untouched page default: nothing a human typed or chose."""
    if value is None or value is False or value == '' or value == [] or value == {}:
        return True
    return type(value) is dict and all(_blank(item) for item in value.values())


def _merge_fills(fills):
    merged = {}
    for item in fills:
        merged.update(copy.deepcopy(item['fields']))
    return merged


def _differs():
    return IdeaError('stale_source', 'Target draft differs from the request\'s own fills')


def _assessment_is_base_plus_fills(draft, base, merged):
    # The page keeps a position beside the assessment: the proposed position is the fill; the actual
    # position only follows it while it was empty (the human decides it otherwise).
    position, before = draft.get('position'), base.get('position') or {}
    if type(position) is not dict or not set(draft) <= {'assessment', 'position'}:
        raise _differs()
    if 'assessment' in merged:
        good = draft.get('assessment') == merged['assessment']
    else:
        good = draft.get('assessment') == base.get('assessment') or (
            'assessment' not in base and _blank(draft.get('assessment')))
    if 'proposed_position' in merged:
        good = good and position.get('proposed_position') == merged['proposed_position']
        if before.get('actual_position') is None:
            good = good and position.get('actual_position') in (None, merged['proposed_position'])
        else:
            good = good and all(position.get(k) == before.get(k) for k in ('actual_position', 'neighbors'))
        good = good and position.get('override_reason') == before.get('override_reason')
    elif before:
        good = good and all(position.get(k) == before.get(k) for k in
                            ('proposed_position', 'actual_position', 'neighbors', 'override_reason'))
    else:
        good = good and _blank(position)
    if not good:
        raise _differs()


def _draft_is_base_plus_fills(operation, draft, base, fills):
    """Exact content test: the draft is the enqueue-time base with this request's fills applied."""
    if type(draft) is not dict:
        raise _differs()
    base = base if type(base) is dict else {}
    merged = _merge_fills(fills)
    if operation == 'assessment':
        return _assessment_is_base_plus_fills(draft, base, merged)
    if not all(key in draft for key in set(merged) | set(base)):
        raise _differs()
    for key, value in draft.items():
        if key in merged:
            good = value == merged[key]
        elif key in base:
            good = value == base[key]
        else:
            good = _blank(value)
        if not good:
            raise _differs()


def reconcile_own_fills(state, correlation, source, fills, base):
    """A reply is not stale merely because this request's own fills moved the draft.

    Returns (correlation, source) re-pinned to the current state when, and only when, the accepted
    revision and every consumed input are unchanged and the target draft is exactly the enqueue-time
    base with the Broker's recorded fills applied. A human edit, a revision change or any other
    difference raises stale_source. The fills come from the Broker, never from the agent's reply.
    """
    _correlation(correlation); operation = correlation['operation']
    _source(operation, source, correlation['idea_id'])
    require(operation in FILL_STEP and fills, 'No fills to reconcile', 'stale_source')
    idea_id = correlation['idea_id']
    require(proposal_source_digest(operation, source, idea_id=idea_id) == correlation['source_digest'],
            'Original source digest differs', 'stale_source')
    try:
        current = prepare_source(state, idea_id, operation)
    except IdeaError as exc:
        if exc.code != 'not_ready':
            raise
        raise IdeaError('stale_source', 'Consumed inputs are no longer current') from exc
    require(current['accepted_revision'] == source['accepted_revision'] == correlation['accepted_revision'],
            'Accepted revision changed', 'stale_source')
    target = 'target' if operation == 'assessment' else operation
    require(all(current['data'].get(key) == source['data'].get(key)
                for key in set(current['data']) | set(source['data']) if key != target),
            'Consumed inputs changed', 'stale_source')
    drafts = _idea(state, idea_id)['workflow']['drafts']
    _draft_is_base_plus_fills(operation, drafts.get(FILL_STEP[operation]), base, fills)
    return (dict(correlation, draft_version=current['draft_version'],
                 source_digest=proposal_source_digest(operation, current, idea_id=idea_id)), current)


def validate_memory(proposal):
    """Typed safe-memory result only; the later adapter grounds retrieval."""
    _bounded(proposal)
    require(type(proposal) is dict and {'status', 'sources', 'rationale'} <= set(proposal)
            <= {'status', 'sources', 'rationale', 'preferred_method'}, 'Invalid memory proposal fields')
    validate_step_fields('method', {'memory': proposal}, partial=True)
    require(type(proposal['status']) is str and proposal['status'] in MEMORY_STATUSES, 'Invalid memory status')
    require(type(proposal['sources']) is list and
            all(type(value) is str and value.strip() for value in proposal['sources']), 'Invalid memory references')
    if proposal['rationale'] is not None:
        text(proposal['rationale'], 'memory rationale')
    check_memory_preference(proposal, 'memory')
    if proposal['status'] == 'found':
        require(bool(proposal['sources']) and type(proposal['rationale']) is str
                and bool(proposal['rationale'].strip()), 'Found memory requires references and rationale')
    if proposal['status'] in ('unavailable', 'error'):
        require(proposal['sources'] == [], 'Unavailable memory cannot claim references')
    return copy.deepcopy(proposal)


def validate_proposal(operation, proposal, *, source=None, idea_id=None):
    _operation(operation); _bounded(proposal)
    try:
        if operation == 'memory':
            return validate_memory(proposal)
        if operation == 'assessment':
            return validate_assessment_proposal(proposal, source=source, idea_id=idea_id)
        if operation == 'visual_brief':
            require(type(proposal) is dict and set(proposal) == {'prototype_skill'}
                    and type(proposal['prototype_skill']) is str
                    and proposal['prototype_skill'] in ('available', 'unavailable'), 'Invalid visual brief proposal')
            return copy.deepcopy(proposal)
        if operation == 'method':
            # R8: the agent never recommends; a method proposal is its memory result only.
            require(type(proposal) is dict and set(proposal) == {'memory'}, 'Method proposal carries memory only')
            result = validate_step_fields('method', proposal, partial=True)
            validate_memory(result['memory'])
            return result
        return validate_step_fields(operation, proposal)
    except IdeaError as exc:
        if exc.code == 'too_large':
            raise
        raise IdeaError('invalid_proposal', 'Invalid typed agent proposal') from exc


def validate_current(state, evidence):
    _bounded(evidence)
    _exact(evidence, ('binding_id', 'generation', 'correlation', 'source', 'proposal'), 'agent evidence')
    _id(evidence['binding_id'], 'binding'); _id(evidence['generation'], 'agent')
    checked = validate_source(state, evidence['correlation'], evidence['source'])
    validate_proposal(evidence['correlation']['operation'], evidence['proposal'],
                     source=checked, idea_id=evidence['correlation']['idea_id'])
    return checked


def project_sources(state, idea_id):
    """Bound duplicate current inputs without hiding invalid typed workflow data."""
    result = {}
    for operation in OPERATIONS:
        try:
            source = prepare_source(state, idea_id, operation)
        except IdeaError as exc:
            if exc.code not in ('not_ready', 'too_large'):
                raise
            code = 'not_ready' if exc.code == 'not_ready' else 'source_too_large'
            result[operation] = dict(available=False, code=code, source=None)
        else:
            entry = dict(available=True, code='ok', source=dict(source,
                source_digest=proposal_source_digest(operation, source, idea_id=idea_id)))
            candidate = dict(result, **{operation:entry})
            # Reserve bounded unavailable entries for every remaining operation.
            candidate.update({key:dict(available=False, code='source_projection_capacity', source=None)
                              for key in OPERATIONS if key not in candidate})
            if _projection_fits(candidate, MAX_SOURCE_PROJECTION_BYTES):
                result[operation] = entry
            else:
                result[operation] = dict(available=False, code='source_projection_capacity', source=None)
    _bounded(result)
    return result


def _projection_fits(value, limit):
    try:
        return len(_bounded(value)) <= limit
    except IdeaError as exc:
        if exc.code != 'too_large':
            raise
        return False


def _live(live_binding):
    _exact(live_binding, ('binding_id', 'generation', 'session_id', 'agent_status'), 'live binding')
    for name, prefix in (('binding_id', 'binding'), ('generation', 'agent'), ('session_id', 'session')):
        _id(live_binding[name], prefix)
    require(type(live_binding['agent_status']) is str and
            live_binding['agent_status'] in ('connected', 'paused', 'disconnected'), 'Invalid live agent status')


def _incarnation_reason(record, live):
    if record['binding_id'] != live['binding_id'] or record['generation'] != live['generation']:
        return 'wrong_generation'
    if record['session_id'] != live['session_id']:
        return 'wrong_session'
    if live['agent_status'] != 'connected':
        return 'agent_unavailable'
    return None


def _acceptance_reason(state, record, final_fields=None):
    """Ignore editable target inputs and global draft counter, retain consumed data."""
    operation = record['operation']
    _data(operation, record['data'])
    idea = _idea(state, record['idea_id'])
    if record['accepted_revision'] != idea['revision']:
        return 'stale_revision'
    try:
        current = prepare_source(state, record['idea_id'], operation)
    except IdeaError as exc:
        if exc.code != 'not_ready':
            raise
        return 'stale_source'
    if operation == 'assessment':
        # Human target edits/autosave do not rewrite the original suggestion.
        # Backlog revision/order AND compared scores/ratings are consumed inputs:
        # another idea's assessment can change without a backlog revision bump.
        if any(_bounded(record['data'][key]) != _bounded(current['data'][key])
               for key in ('steps', 'backlog')):
            return 'stale_source'
        return None
    for step in INPUTS[operation]:
        if _bounded(record['data'][step]) != _bounded(current['data'][step]):
            return 'stale_source'
    if operation == 'visual_brief':
        return None
    if operation == 'method':
        original_target = record['data'].get('method', {})
        if 'memory' in original_target:
            current_target = current['data'].get('method', {})
            # Applying the immutable returned result is a permitted target edit,
            # including unavailable input -> found output. Independent memory
            # drift is still a changed consumed claim and cannot cross acceptance.
            if current_target.get('memory') not in (original_target['memory'], record['proposal']['memory']):
                return 'stale_source'
        # Memory is supporting typed evidence, never a freely editable claim.
        if final_fields is not None and final_fields['memory'] != record['proposal']['memory']:
            return 'stale_source'
    return None


class SourceAdapter:
    """Trusted Store inventory composition; caller already holds its transaction."""
    def __init__(self, store):
        from idea_store import Store
        require(isinstance(store, Store), 'Trusted proposal Store required')
        self.store = store

    def project_proposals(self, state, idea_id, live_binding):
        """Compatibility list accessor; composition uses project_projection."""
        return self.project_projection(state, idea_id, live_binding)['proposals']

    def project_projection(self, state, idea_id, live_binding):
        """Bound browser evidence; immutable bodies stay in linked Markdown.

        Retain newest eligible records first, then newest other evidence. Every
        retained record exposes its verified path/hash even when its typed body
        cannot fit. Counts and the canonical idea index explain unlisted records.
        """
        _live(live_binding)
        records = self.store.agent_proposals(state, idea_id, with_links=True)
        candidates = []
        for ordinal, linked in enumerate(records):
            checked = validate_record(linked['record'])
            try:
                current = prepare_source_for_projection(state, checked)
                acceptance_reason = _acceptance_reason(state, checked)
            except IdeaError as exc:
                if exc.code != 'too_large':
                    raise
                # Valid current workflow exceeds the request-source envelope.
                current = acceptance_reason = 'stale_source'
            live_reason = _incarnation_reason(checked, live_binding)
            acceptance_reason = live_reason or acceptance_reason
            if checked['operation'] in ('memory', 'visual_brief') and acceptance_reason is None:
                acceptance_reason = 'supporting_evidence'
            stale_reason = live_reason or current
            summary = dict(proposal_id=checked['proposal_id'], request_id=checked['request_id'],
                operation=checked['operation'], accepted_revision=checked['accepted_revision'],
                draft_version=checked['draft_version'], source_digest=checked['source_digest'],
                proposal=None, content_omitted=True, evidence=copy.deepcopy(linked['evidence']),
                stale=stale_reason is not None, stale_reason=stale_reason,
                acceptance_eligible=False, acceptance_reason='projection_omitted')
            candidates.append((acceptance_reason is None, ordinal, summary, checked['proposal'], acceptance_reason))
        candidates.sort(key=lambda item:(item[0], item[1]), reverse=True)
        inventory = dict(total=len(records), projected=0, omitted=len(records),
                         content_omitted=0, index_path=idea_id+'.md')
        result = dict(proposals=[], proposal_inventory=inventory)
        require(_projection_fits(result, MAX_PROJECTION_BYTES), 'Projection metadata exceeds capacity', 'too_large')
        retained = []
        # Reserve all retained identities before giving any item its full body.
        for item in candidates:
            if len(retained) >= MAX_PROJECTIONS:
                break
            trial = copy.deepcopy(result)
            trial['proposals'].append(item[2])
            trial['proposal_inventory'].update(projected=len(retained)+1,
                omitted=len(records)-len(retained)-1, content_omitted=len(retained)+1)
            if not _projection_fits(trial, MAX_PROJECTION_BYTES):
                break
            result = trial
            retained.append(item)
        for index, (_, _, summary, proposal, reason) in enumerate(retained):
            full = dict(summary, proposal=copy.deepcopy(proposal), content_omitted=False,
                        acceptance_eligible=reason is None, acceptance_reason=reason)
            trial = copy.deepcopy(result)
            trial['proposals'][index] = full
            trial['proposal_inventory']['content_omitted'] -= 1
            if _projection_fits(trial, MAX_PROJECTION_BYTES):
                result = trial
        _bounded(result)
        return result

    def validate_acceptance(self, state, idea, payload, acceptance_source, context, *, live_binding):
        """Human source seam; no policy call, nested transaction or mutation."""
        _bounded(payload); _live(live_binding)
        require(idea is state['ideas'].get(idea.get('idea_id')), 'Acceptance requires current idea', 'invalid_handler')
        step = payload.get('step')
        require(step in ('discovery', 'exploration', 'method', 'assess'), 'Unsupported proposal acceptance', 'operation_unavailable')
        fields = validate_step_fields(step, payload.get('fields'))
        require(payload.get('idea_id') == idea['idea_id'], 'Acceptance idea differs', 'stale_source')
        integer(payload.get('expected_revision'), 'expected revision', 1)
        integer(payload.get('expected_draft_version'), 'expected draft version')
        prepared = import_workflow(idea)
        require(payload['expected_revision'] == idea['revision'], 'Stale idea revision', 'stale_revision')
        require(payload['expected_draft_version'] == prepared['workflow']['draft_version'],
                'Stale draft version', 'stale_draft_version')
        _exact(acceptance_source, ('source_digest', 'dependencies'), 'acceptance source')
        require(final_source(idea, step, fields, tuple(acceptance_source['dependencies'])) == acceptance_source,
                'Final acceptance sources differ', 'stale_source')
        require(context.session_id == live_binding['session_id'], 'Acceptance session differs', 'wrong_session')
        if step == 'assess':
            integer(payload.get('expected_backlog_revision'), 'expected backlog revision')
            require(payload['expected_backlog_revision'] == state['backlog_revision'],
                    'Stale backlog revision', 'stale_backlog')
            validate_actual_position(fields['position'], state['order'], idea['idea_id'])
        proposal_id = payload.get('proposal_id')
        if proposal_id is None:
            # Manual choices remain possible even when the agent is disconnected.
            if step == 'method':
                validate_memory(fields['memory'])
            return None
        _id(proposal_id, 'proposal')
        records = self.store.agent_proposals(state, idea['idea_id'])
        matches = [record for record in records if record['proposal_id'] == proposal_id]
        require(len(matches) == 1, 'Unknown proposal', 'proposal_not_found')
        record = validate_record(matches[0])
        operation = 'assessment' if step == 'assess' else step
        require(record['operation'] == operation, 'Proposal operation differs', 'proposal_mismatch')
        require(record['actor'] == context.actor, 'Proposal actor differs', 'proposal_mismatch')
        reason = _incarnation_reason(record, live_binding) or _acceptance_reason(state, record, fields)
        require(reason is None, 'Proposal is no longer eligible', reason or 'stale_source')
        if step == 'assess':
            validate_actual_position(fields['position'], state['order'], idea['idea_id'],
                                     original_proposal=record['proposal'])
        return record


def prepare_source_for_projection(state, record):
    """Stable current-input stale reason; corrupt schemas are never hidden."""
    correlation = {key: record[key] for key in CORRELATION}
    source = {key: record[key] for key in SOURCE_KEYS if key != 'data'}
    source['data'] = record['data']
    try:
        validate_source(state, correlation, source)
    except IdeaError as exc:
        if exc.code != 'stale_source':
            raise
        return 'stale_source'
    return None
