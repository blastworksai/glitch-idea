"""Pure browser workflow schemas and versioned evidence.

This module reduces explicit human decisions, without I/O or inferred receipts.
Draft validation permits missing/null answers; acceptance validation additionally
enforces requirements. Persistence must check evidence existence and source CAS.
Legacy SHAPE_KEYS and assessment scoring belong to idea_domain, unchanged.
"""
import copy
import hashlib
import json
import re

from idea_domain import (ASSESS_KEYS, MAX_INPUT, MAX_NUMBER, IdeaError,
                         assessment, check_id, integer, require, shape, text)

WORKFLOW_VERSION = 2
STEP_ORDER = ('capture', 'priorities', 'shape', 'method', 'visualize', 'assess', 'review')
STEP_STATUSES = ('todo', 'current', 'saved', 'review-needed', 'unsaved', 'skipped', 'not-applicable')
SCOPES = ('small-change', 'capability', 'project', 'epic')
METHODS = ('bounded-plan', 'adaptive-slices', 'appetite-led', 'experiment-led')
MEMORY_STATUSES = ('found', 'searched_no_preference', 'unavailable', 'error')
STEP_FIELDS = {
    'capture': frozenset(('raw_text', 'workspace')),
    'priorities': frozenset(('urgency', 'importance')),
    'shape': frozenset(('outcome', 'scope', 'scope_reason', 'alternatives', 'assumptions', 'next_slice', 'learning')),
    'method': frozenset(('selection', 'reason', 'investment', 'experiment', 'memory')),
    'visualize': frozenset(('disposition', 'reason', 'design_set_id', 'brief_evidence_id')),
    'assess': frozenset(('assessment', 'position')),
    'review': frozenset(('handoff_id', 'source_revision')),
}
RECEIPT_FIELDS = frozenset(('accepted_revision', 'evidence_id', 'source_revision',
                          'source_digest', 'dependencies', 'actor', 'timestamp'))
SNAPSHOT_FIELDS = frozenset(('revision', 'shape', 'ratings', 'assessments', 'actor', 'action', 'timestamp'))


def _object(value, keys, name, partial=False):
    require(type(value) is dict and all(type(k) is str for k in value), name+' must be an object')
    require(set(value) <= set(keys), name+' contains unsupported fields')
    if not partial:
        require(set(value) == set(keys), name+' must contain exactly: '+', '.join(sorted(keys)))


def _draft_text(value, name, limit=65536):
    # Empty editing buffers are valid drafts, never meaningful acceptance.
    require(type(value) is str and len(value) <= limit, name+' must be text within '+str(limit)+' characters')
    require(not any(0xD800 <= ord(c) <= 0xDFFF for c in value), name+' contains invalid Unicode')


def _enum(value, values, name):
    require(type(value) is str and value in values, 'Invalid '+name)


def _list(value, name):
    require(type(value) is list and len(value) <= 1000, name+' must be a list of at most 1000 items')


def _text_list(value, name):
    _list(value, name)
    for item in value:
        _draft_text(item, name)


def _opaque_id(value, name):
    require(type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}', value) is not None,
            name+' must be an opaque identifier, not a path')


def _hash(value, name):
    require(type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None, 'Invalid '+name)


def _nested(value, keys, name, validators):
    _object(value, keys, name, partial=True)
    for key, item in value.items():
        if item is not None:
            validators[key](item, name+'.'+key)


def _positive_number(value, name):
    # Reuse bounded domain numbers; bool is deliberately excluded.
    from idea_domain import number
    number(value, name, high=MAX_NUMBER)
    require(value > 0, name+' must be positive')


def _boolean(value, name):
    require(type(value) is bool, name+' must be a boolean')


def _workspace(value, name):
    _nested(value, {'name', 'path', 'confirmed'}, name,
            {'name': _draft_text, 'path': _draft_text, 'confirmed': _boolean})


def _alternatives(value, name):
    _list(value, name)
    for item in value:
        _nested(item, {'route', 'reason'}, name, {'route': _draft_text, 'reason': _draft_text})


def _investment(value, name):
    _nested(value, {'cap', 'unit', 'boundary'}, name,
            {'cap': _positive_number, 'unit': _draft_text, 'boundary': _draft_text})


def _experiment(value, name):
    keys = {'question', 'evidence', 'success_criterion', 'stop_rule'}
    _nested(value, keys, name, {k: _draft_text for k in keys})


def _memory(value, name):
    _nested(value, {'status', 'sources', 'rationale'}, name,
            {'status': lambda v, n: _enum(v, MEMORY_STATUSES, n),
             'sources': _text_list, 'rationale': _draft_text})


def _basis(value, name):
    if type(value) is dict:
        require(len(value) <= 100, name+' exceeds 100 entries')
        for key, item in value.items():
            _draft_text(key, name+' key', 100)
            _draft_text(item, name+' value')
    else:
        _draft_text(value, name)


def _assessment(value, name):
    _object(value, ASSESS_KEYS, name, partial=True)  # score is never browser input
    for key in ('version', 'basis', 'provenance', 'assumptions', 'confidence', 'method'):
        item = value.get(key)
        if item is None:
            continue
        if key == 'version':
            _draft_text(item, key, 100)
        elif key in ('basis', 'provenance'):
            _basis(item, key)
        elif key == 'assumptions':
            _text_list(item, key)
        elif key == 'confidence':
            _enum(item, ('low', 'medium', 'high'), key)
        else:
            _enum(item, ('wsjf', 'rice', 'kano'), key)
    inputs = value.get('inputs')
    if inputs is not None:
        method = value.get('method')
        keys = ({'value', 'time_criticality', 'enablement', 'effort'} if method == 'wsjf' else
                {'reach', 'impact', 'confidence', 'effort'} if method == 'rice' else
                {'category', 'hypothesis'} if method == 'kano' else
                {'value', 'time_criticality', 'enablement', 'effort', 'reach', 'impact', 'confidence', 'category', 'hypothesis'})
        _object(inputs, keys, 'assessment.inputs', partial=True)
        from idea_domain import number
        for key, item in inputs.items():
            if item is None:
                continue
            if key == 'category':
                _enum(item, ('must-be', 'performance', 'delighter', 'indifferent', 'reverse', 'questionable'), key)
            elif key == 'hypothesis':
                _boolean(item, key)
            else:
                number(item, key, high=1 if key == 'confidence' else MAX_NUMBER)
                require(key != 'effort' or item > 0, 'effort must be positive')


def _neighbor(value, name):
    check_id(value)


def _neighbors(value, name):
    _nested(value, {'before', 'after'}, name, {'before': _neighbor, 'after': _neighbor})
    require(value.get('before') is None or value.get('before') != value.get('after'), 'Neighbor IDs must differ')


def _position(value, name):
    _nested(value, {'proposed_position', 'actual_position', 'neighbors', 'override_reason'}, name,
            {'proposed_position': lambda v, n: integer(v, n, 1),
             'actual_position': lambda v, n: integer(v, n, 1),
             'neighbors': _neighbors, 'override_reason': _draft_text})


VALIDATORS = {
    'capture': {'raw_text': lambda v, n: _draft_text(v, n, MAX_INPUT), 'workspace': _workspace},
    'priorities': {k: lambda v, n: integer(v, n, 1, 10) for k in ('urgency', 'importance')},
    'shape': {'outcome': _draft_text, 'scope': lambda v, n: _enum(v, SCOPES, n),
              'scope_reason': _draft_text, 'alternatives': _alternatives,
              'assumptions': _text_list, 'next_slice': _draft_text, 'learning': _text_list},
    'method': {'selection': lambda v, n: _enum(v, METHODS, n), 'reason': _draft_text,
               'investment': _investment, 'experiment': _experiment, 'memory': _memory},
    'visualize': {'disposition': lambda v, n: _enum(v, ('accepted_set', 'skipped', 'not-applicable'), n),
                  'reason': _draft_text, 'design_set_id': _opaque_id, 'brief_evidence_id': _opaque_id},
    'assess': {'assessment': _assessment, 'position': _position},
    'review': {'handoff_id': _opaque_id, 'source_revision': lambda v, n: integer(v, n, 1)},
}


def _meaningful(value):
    return type(value) is str and bool(value.strip())


def step_requirements(step, fields):
    """Return all missing acceptance requirements; invalid draft types raise.

    This is data eligibility only. Source revisions, workspace existence, asset
    hashes, proposal provenance and current prerequisites require service checks.
    """
    validate_step_fields(step, fields, partial=True)
    missing = [key for key in sorted(STEP_FIELDS[step]) if key not in fields]

    def needed(key, condition):
        if not condition and key not in missing:
            missing.append(key)

    def nonempty(key):
        needed(key, _meaningful(fields.get(key)))

    def nested(key, keys, text_keys=()):
        value = fields.get(key)
        needed(key, type(value) is dict)
        if type(value) is not dict:
            return {}
        for k in keys:
            needed(key+'.'+k, k in value and (value[k] is not None or k not in text_keys))
        for k in text_keys:
            needed(key+'.'+k, _meaningful(value.get(k)))
        return value

    if step == 'capture':
        nonempty('raw_text')
        workspace = nested('workspace', ('name', 'path', 'confirmed'), ('name', 'path'))
        needed('workspace.confirmed', workspace.get('confirmed') is True)
    elif step == 'priorities':
        for k in ('urgency', 'importance'):
            needed(k, fields.get(k) is not None)
    elif step == 'shape':
        for k in ('outcome', 'scope_reason', 'next_slice'):
            nonempty(k)
        needed('scope', fields.get('scope') in SCOPES)
        alternatives = fields.get('alternatives')
        needed('alternatives', bool(alternatives))
        for index, alternative in enumerate(alternatives or []):
            for k in ('route', 'reason'):
                needed('alternatives.'+str(index)+'.'+k, _meaningful(alternative.get(k)))
        for k in ('assumptions', 'learning'):
            needed(k, type(fields.get(k)) is list)
            for index, item in enumerate(fields.get(k) or []):
                needed(k+'.'+str(index), _meaningful(item))
    elif step == 'method':
        selected = fields.get('selection')
        needed('selection', selected in METHODS)
        nonempty('reason')
        if selected == 'appetite-led':
            investment = nested('investment', ('cap', 'unit', 'boundary'), ('unit', 'boundary'))
            needed('investment.cap', investment.get('cap') is not None)
        else:
            needed('investment', fields.get('investment') is None)
        if selected == 'experiment-led':
            keys = ('question', 'evidence', 'success_criterion', 'stop_rule')
            nested('experiment', keys, keys)
        else:
            needed('experiment', fields.get('experiment') is None)
        memory = nested('memory', ('status', 'sources', 'rationale'))
        needed('memory.status', memory.get('status') in MEMORY_STATUSES)
        needed('memory.sources', type(memory.get('sources')) is list)
        for index, source in enumerate(memory.get('sources') or []):
            needed('memory.sources.'+str(index), _meaningful(source))
        if memory.get('status') == 'found':
            needed('memory.sources', bool(memory.get('sources')))
            needed('memory.rationale', _meaningful(memory.get('rationale')))
        elif memory.get('status') in ('unavailable', 'error'):
            needed('memory.sources', memory.get('sources') == [])
    elif step == 'visualize':
        disposition = fields.get('disposition')
        needed('disposition', disposition in ('accepted_set', 'skipped', 'not-applicable'))
        if disposition == 'accepted_set':
            needed('design_set_id', fields.get('design_set_id') is not None)
        else:
            nonempty('reason')
            needed('design_set_id', fields.get('design_set_id') is None)
    elif step == 'assess':
        try:
            assessment(fields.get('assessment'))
        except (IdeaError, TypeError, KeyError):
            needed('assessment', False)
        position = nested('position', ('proposed_position', 'actual_position', 'neighbors', 'override_reason'))
        for k in ('proposed_position', 'actual_position'):
            needed('position.'+k, position.get(k) is not None)
        neighbors = position.get('neighbors')
        needed('position.neighbors', type(neighbors) is dict and set(neighbors) == {'before', 'after'})
        if position.get('proposed_position') != position.get('actual_position'):
            needed('position.override_reason', _meaningful(position.get('override_reason')))
    else:
        needed('handoff_id', fields.get('handoff_id') is not None)
        needed('source_revision', fields.get('source_revision') is not None)
    return tuple(missing)


def validate_step_fields(step, fields, partial=False):
    """Validate frozen API fields, returning a detached copy, never a score."""
    require(type(step) is str and step in STEP_ORDER, 'Invalid workflow step')
    _object(fields, STEP_FIELDS[step], step, partial=partial)
    for key, value in fields.items():
        if value is not None:
            VALIDATORS[step][key](value, step+'.'+key)
    if not partial:
        missing = step_requirements(step, fields)
        require(not missing, 'Acceptance requires: '+', '.join(missing), 'not_ready')
    return copy.deepcopy(fields)


def empty_workflow():
    """Unaccepted wizard state; no default ratings or methodology."""
    return {'schema_version': WORKFLOW_VERSION, 'current_step': 'capture', 'draft_version': 0,
            'steps': {step: {'fields': None, 'acceptance': None, 'invalidated_by': []} for step in STEP_ORDER},
            'drafts': {}}


def validate_acceptance(value):
    _object(value, RECEIPT_FIELDS, 'acceptance')
    integer(value['accepted_revision'], 'accepted_revision', 1)
    integer(value['source_revision'], 'source_revision', 1)
    require(value['source_revision'] <= value['accepted_revision'], 'Acceptance source is newer than acceptance')
    _opaque_id(value['evidence_id'], 'evidence_id')
    _hash(value['source_digest'], 'source_digest')
    text(value['actor'], 'actor', 200)
    text(value['timestamp'], 'timestamp', 100)
    dependencies = value['dependencies']
    _object(dependencies, STEP_ORDER, 'dependencies', partial=True)
    for step, source in dependencies.items():
        _object(source, {'revision', 'digest'}, 'dependency '+step)
        integer(source['revision'], 'dependency revision', 1, value['source_revision'])
        _hash(source['digest'], 'dependency digest')
    return copy.deepcopy(value)


def validate_workflow(value):
    _object(value, {'schema_version', 'current_step', 'draft_version', 'steps', 'drafts'}, 'workflow')
    require(type(value['schema_version']) is int and value['schema_version'] == WORKFLOW_VERSION, 'Unsupported workflow version')
    _enum(value['current_step'], STEP_ORDER, 'current_step')
    integer(value['draft_version'], 'draft_version')
    _object(value['steps'], STEP_ORDER, 'steps')
    for step, record in value['steps'].items():
        _object(record, {'fields', 'acceptance', 'invalidated_by'}, 'step '+step)
        if record['fields'] is not None:
            validate_step_fields(step, record['fields'])
        if record['acceptance'] is not None:
            validate_acceptance(record['acceptance'])
            require(record['fields'] is not None, 'Acceptance requires saved fields')
        require((record['fields'] is None) == (record['acceptance'] is None), 'Saved fields require acceptance evidence')
        _list(record['invalidated_by'], 'invalidated_by')
        for cause in record['invalidated_by']:
            _enum(cause, STEP_ORDER, 'invalidation cause')
        require(len(set(record['invalidated_by'])) == len(record['invalidated_by']), 'Duplicate invalidation cause')
        require(not record['invalidated_by'] or record['acceptance'] is not None, 'Only prior acceptance can be invalidated')
    _object(value['drafts'], STEP_ORDER, 'drafts', partial=True)
    for step, fields in value['drafts'].items():
        validate_step_fields(step, fields, partial=True)
    return copy.deepcopy(value)


def validate_snapshot(value):
    """Validate a legacy snapshot or explicit v2 extension without rewriting it."""
    require(type(value) is dict, 'Snapshot must be an object')
    versioned = 'schema_version' in value or 'workflow' in value
    _object(value, SNAPSHOT_FIELDS | {'schema_version', 'workflow'} if versioned else SNAPSHOT_FIELDS, 'snapshot')
    integer(value['revision'], 'snapshot revision', 1)
    if value['shape'] is not None:
        shape(value['shape'])
    if value['ratings'] is not None:
        _object(value['ratings'], {'urgency', 'importance', 'actor', 'timestamp'}, 'ratings', partial=True)
        require({'urgency', 'importance', 'actor'} <= set(value['ratings']), 'Incomplete ratings evidence')
        integer(value['ratings']['urgency'], 'urgency', 1, 10)
        integer(value['ratings']['importance'], 'importance', 1, 10)
        text(value['ratings']['actor'], 'rating actor', 200)
        if 'timestamp' in value['ratings']:
            text(value['ratings']['timestamp'], 'rating timestamp', 100)
    _list(value['assessments'], 'assessments')
    for item in value['assessments']:
        _object(item, ASSESS_KEYS | {'score', 'assessment_id', 'actor', 'timestamp'}, 'assessment evidence', partial=True)
        require(ASSESS_KEYS | {'score'} <= set(item), 'Incomplete assessment evidence')
        computed = assessment({key: item[key] for key in ASSESS_KEYS})
        require(item['score'] is None or type(item['score']) in (int, float), 'Invalid assessment score type')
        require(computed['score'] == item['score'], 'Assessment score mismatch')
        if 'assessment_id' in item:
            check_id(item['assessment_id'], 'assessment')
        for key in ('actor', 'timestamp'):
            if key in item:
                text(item[key], 'assessment '+key, 200)
    for key in ('actor', 'action', 'timestamp'):
        text(value[key], key, 200)
    if versioned:
        require(type(value['schema_version']) is int and value['schema_version'] == WORKFLOW_VERSION, 'Unsupported snapshot version')
        workflow = validate_workflow(value['workflow'])
        for record in workflow['steps'].values():
            if record['acceptance'] is not None:
                require(record['acceptance']['accepted_revision'] <= value['revision'], 'Future acceptance in snapshot')
    return copy.deepcopy(value)


def adapt_snapshot(value, workflow=None):
    """Add explicit v2 workflow evidence; with no evidence keep legacy exact."""
    result = validate_snapshot(value)
    if workflow is not None:
        result['schema_version'] = WORKFLOW_VERSION
        result['workflow'] = validate_workflow(workflow)
        result = validate_snapshot(result)
    return result


# Edges represent consumed inputs, not wizard navigation. Review additionally
# requires every earlier step, including a durable optional Visualize disposition.
DEPENDENCIES = {
    'capture': (), 'priorities': (), 'shape': ('capture',),
    'method': ('capture', 'shape'), 'visualize': ('capture', 'shape'),
    'assess': ('capture', 'priorities', 'shape'),
    'review': ('capture', 'priorities', 'shape', 'method', 'visualize', 'assess'),
}
OPERATIONS = STEP_ORDER + ('memory', 'visual_brief', 'assessment', 'position')


def source_digest(operation, source_revision, source_fields):
    """Hash typed source fields, operation and revision using canonical JSON.

    source_fields maps step keys to accepted or partial draft fields. The caller
    selects every consumed source; reducers always include their dependencies.
    This is distinct from the immutable exact-origin-text SHA-256.
    """
    _enum(operation, OPERATIONS, 'operation')
    integer(source_revision, 'source_revision', 1)
    _object(source_fields, STEP_ORDER, 'source_fields', partial=True)
    validated = {step: validate_step_fields(step, fields, partial=True)
                 for step, fields in source_fields.items()}
    payload = {'operation': operation, 'source_revision': source_revision, 'fields': validated}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _idea(value):
    require(type(value) is dict, 'Idea must be an object')
    integer(value.get('revision'), 'revision', 1)
    require(type(value.get('revisions')) is list and len(value['revisions']) == value['revision'], 'Missing revision history')
    for index, snapshot in enumerate(value['revisions'], 1):
        validate_snapshot(snapshot)
        require(snapshot['revision'] == index, 'Nonsequential revision history')
    for key in ('shape', 'ratings', 'assessments'):
        require(key in value, 'Missing legacy '+key)
    if 'workflow' in value:
        workflow = validate_workflow(value['workflow'])
        for record in workflow['steps'].values():
            if record['acceptance'] is not None:
                require(record['acceptance']['accepted_revision'] <= value['revision'], 'Future acceptance in current workflow')
    return copy.deepcopy(value)


def import_workflow(idea):
    """Enter the wizard without converting known legacy values into acceptance."""
    result = _idea(idea)
    if 'workflow' in result:
        return result
    workflow = empty_workflow()
    drafts = workflow['drafts']
    origin = result.get('origin')
    if type(origin) is dict and origin.get('text') is not None:
        drafts['capture'] = {'raw_text': origin['text']}
    if result['ratings'] is not None:
        drafts['priorities'] = {k: result['ratings'][k] for k in ('urgency', 'importance')}
    if result['shape'] is not None:
        legacy = shape(result['shape'])
        drafts['shape'] = {k: legacy[k] for k in STEP_FIELDS['shape']}
        drafts['method'] = {'selection': legacy['method'], 'reason': legacy['method_reason']}
    if result['assessments']:
        latest = result['assessments'][-1]
        drafts['assess'] = {'assessment': {k: latest[k] for k in ASSESS_KEYS}}
    result['workflow'] = validate_workflow(workflow)
    return result


def _cas(idea, expected_revision, expected_draft_version):
    integer(expected_revision, 'expected_revision', 1)
    integer(expected_draft_version, 'expected_draft_version')
    require(expected_revision == idea['revision'], 'Stale idea revision', 'stale_revision')
    require(expected_draft_version == idea['workflow']['draft_version'], 'Stale draft version', 'stale_draft_version')


def _dependency_names(workflow, step, extra_dependencies=()):
    require(type(extra_dependencies) in (tuple, list), 'extra_dependencies must be step keys')
    names = set(DEPENDENCIES[step])
    previous = workflow['steps'][step]['acceptance']
    if previous is not None:
        names.update(previous['dependencies'])
    for name in extra_dependencies:
        _enum(name, STEP_ORDER, 'extra dependency')
        names.add(name)
    # The wizard's sources point backwards. This keeps malicious/corrupt source
    # references from creating cycles or letting a step consume its own receipt.
    require(all(STEP_ORDER.index(name) < STEP_ORDER.index(step) for name in names),
            'Dependencies must refer to earlier steps')
    return tuple(name for name in STEP_ORDER if name in names)


def dependency_graph(workflow):
    """Return source -> dependents, extended by recorded consumed inputs."""
    validated = validate_workflow(workflow)
    graph = {step: [] for step in STEP_ORDER}
    for target in STEP_ORDER:
        for source in _dependency_names(validated, target):
            graph[source].append(target)
    return {step: tuple(targets) for step, targets in graph.items()}


def dependent_steps(workflow, changed_steps):
    require(type(changed_steps) in (tuple, list), 'changed_steps must be step keys')
    graph = dependency_graph(workflow)
    seen = set()
    pending = list(changed_steps)
    for step in pending:
        _enum(step, STEP_ORDER, 'changed step')
    while pending:
        source = pending.pop()
        for target in graph[source]:
            if target not in seen:
                seen.add(target)
                pending.append(target)
    return tuple(step for step in STEP_ORDER if step in seen)


def _record_source(step, record):
    receipt = record['acceptance']
    return {'revision': receipt['accepted_revision'],
            'digest': source_digest(step, receipt['accepted_revision'], {step: record['fields']})}


def _step_states(workflow):
    # Earlier-source rule permits an ordered walk instead of recursive status
    # evaluation. Changed drafts are visible without destroying saved evidence.
    dependency_graph(workflow)
    states = {}
    current = {}
    for step in STEP_ORDER:
        record = workflow['steps'][step]
        receipt = record['acceptance']
        unsaved = step in workflow['drafts'] and workflow['drafts'][step] != record['fields']
        stale = bool(record['invalidated_by'])
        if receipt is not None:
            for source in _dependency_names(workflow, step):
                source_record = workflow['steps'][source]
                stale = stale or not current[source]
                if source_record['acceptance'] is not None:
                    stale = stale or receipt['dependencies'].get(source) != _record_source(source, source_record)
                else:
                    stale = True
        current[step] = receipt is not None and not stale and not unsaved
        if unsaved:
            states[step] = 'unsaved'
        elif receipt is not None and stale:
            states[step] = 'review-needed'
        elif receipt is not None:
            disposition = record['fields'].get('disposition') if step == 'visualize' else None
            states[step] = disposition if disposition in ('skipped', 'not-applicable') else 'saved'
        else:
            states[step] = 'current' if workflow['current_step'] == step else 'todo'
    return states, current


def derive_state(idea, handoff=None):
    """Project frozen browser fields; caller supplies only a verified packet.

    Packet creation/copy never changes accepted revision or workflow snapshots.
    Authentication/capabilities/backlog revision are supplied by the service.
    """
    prepared = import_workflow(idea)
    workflow = prepared['workflow']
    statuses, current = _step_states(workflow)
    accepted = {step: copy.deepcopy(workflow['steps'][step]['fields']) for step in STEP_ORDER}
    steps = {}
    for step in STEP_ORDER:
        receipt = workflow['steps'][step]['acceptance']
        steps[step] = {'status': statuses[step],
                       'accepted_revision': receipt['accepted_revision'] if receipt else None,
                       'evidence_id': receipt['evidence_id'] if receipt else None}
    if handoff is not None:
        packet = validate_step_fields('review', handoff)
        if packet['source_revision'] == prepared['revision'] and all(current[k] for k in DEPENDENCIES['review']):
            accepted['review'] = packet
            steps['review'] = {'status': 'saved', 'accepted_revision': prepared['revision'],
                               'evidence_id': packet['handoff_id']}
        else:
            accepted['review'] = packet
            steps['review'] = {'status': 'review-needed', 'accepted_revision': packet['source_revision'],
                               'evidence_id': packet['handoff_id']}
    selected = workflow['current_step']
    draft = {'step': selected, 'fields': copy.deepcopy(workflow['drafts'][selected])} if selected in workflow['drafts'] else None
    return {'revision': prepared['revision'], 'draft_version': workflow['draft_version'],
            'current_step': selected, 'steps': steps, 'accepted': accepted,
            'drafts': copy.deepcopy(workflow['drafts']), 'draft': draft}


def _result(idea, changed, invalidated=(), accepted_changed=False):
    validate_workflow(idea['workflow'])
    return {'idea': idea, 'changed': changed, 'accepted_changed': accepted_changed,
            'invalidated': list(invalidated)}


def _next_step(workflow):
    statuses, _ = _step_states(workflow)
    return next((step for step in STEP_ORDER if statuses[step] not in ('saved', 'skipped', 'not-applicable')), 'review')


def save_draft(idea, step, fields, *, expected_revision, expected_draft_version):
    """Replace one durable partial buffer; never alter accepted history."""
    result = import_workflow(idea)
    _cas(result, expected_revision, expected_draft_version)
    fields = validate_step_fields(step, fields, partial=True)
    workflow = result['workflow']
    navigated = workflow['current_step'] != step
    workflow['current_step'] = step
    if step in workflow['drafts'] and workflow['drafts'][step] == fields:
        return _result(result, navigated)
    workflow['drafts'][step] = fields
    workflow['draft_version'] += 1
    integer(workflow['draft_version'], 'draft_version')
    return _result(result, True)


def navigate_step(idea, step, *, expected_revision, expected_draft_version):
    """Persist Pause selection without changing decision evidence."""
    require(type(step) is str and step in STEP_ORDER, 'Unknown workflow step')
    result = import_workflow(idea)
    _cas(result, expected_revision, expected_draft_version)
    if result['workflow']['current_step'] == step:
        unchanged = _result(result, False)
        unchanged['idea'] = copy.deepcopy(idea)
        return unchanged
    result['workflow']['current_step'] = step
    return _result(result, True)


def _invalidate(workflow, changed_steps):
    invalidated = []
    for cause in changed_steps:
        for target in dependent_steps(workflow, (cause,)):
            record = workflow['steps'][target]
            if record['acceptance'] is not None:
                if cause not in record['invalidated_by']:
                    record['invalidated_by'].append(cause)
                if target not in invalidated:
                    invalidated.append(target)
    return tuple(step for step in STEP_ORDER if step in invalidated)


def invalidate_external(idea, changed_steps):
    """CLI/file adapter hook; caller owns domain revision and its snapshot.

    These externally changed source steps lose current acceptance as well as
    their dependents. No workflow on a legacy idea means no invented wizard.
    Call this after changing domain fields and before appending the new snapshot.
    """
    require(type(idea) is dict, 'Idea must be an object')
    result = copy.deepcopy(idea)
    require(type(changed_steps) in (tuple, list), 'changed_steps must be step keys')
    for step in changed_steps:
        _enum(step, STEP_ORDER, 'changed step')
    if 'workflow' not in result:
        return {'idea': result, 'changed': False, 'accepted_changed': False, 'invalidated': []}
    workflow = validate_workflow(result['workflow'])
    result['workflow'] = workflow
    invalidated = set(_invalidate(workflow, changed_steps))
    for step in changed_steps:
        record = workflow['steps'][step]
        if record['acceptance'] is not None:
            if step not in record['invalidated_by']:
                record['invalidated_by'].append(step)
            invalidated.add(step)
    return _result(result, bool(invalidated), tuple(k for k in STEP_ORDER if k in invalidated))


def _mirror_legacy(idea, step, fields, actor, timestamp):
    if step == 'priorities':
        idea['ratings'] = dict(fields, actor=actor, timestamp=timestamp)
    elif step == 'shape':
        old = idea['shape'] or {}
        method = idea['workflow']['steps']['method']['fields']
        idea['shape'] = dict(fields, method=method['selection'] if method else old.get('method'),
                             method_reason=method['reason'] if method else old.get('method_reason'))
    elif step == 'method' and idea['shape'] is not None:
        idea['shape']['method'] = fields['selection']
        idea['shape']['method_reason'] = fields['reason']
    elif step == 'assess':
        idea['assessments'].append(dict(assessment(fields['assessment']), actor=actor, timestamp=timestamp))
    # Revised capture/workspace never rewrites immutable origin. Visual evidence
    # and method conditionals live exclusively in workflow, not legacy shape.


def acceptance_source(idea, step, fields, extra_dependencies=()):
    """Return current source digest/dependencies for an explicit acceptance."""
    prepared = import_workflow(idea)
    _enum(step, STEP_ORDER, 'step')
    fields = validate_step_fields(step, fields)
    workflow = prepared['workflow']
    names = _dependency_names(workflow, step, extra_dependencies)
    _, current = _step_states(workflow)
    dependencies = {}
    sources = {step: fields}
    for name in names:
        require(current[name], 'Current acceptance required for '+name, 'not_ready')
        record = workflow['steps'][name]
        dependencies[name] = _record_source(name, record)
        sources[name] = record['fields']
    return {'source_digest': source_digest(step, prepared['revision'], sources),
            'dependencies': dependencies}


def accept_step(idea, step, fields, *, expected_revision, expected_draft_version,
                actor, timestamp, evidence_id, source_digest, extra_dependencies=()):
    """One accepted revision including all invalidations, or a true no-op.

    The service checks proposal provenance, workspace/assets and backlog CAS
    before this reducer. Receipt persistence and deduplication belong to Store.
    changed indicates any state change; accepted_changed indicates a new
    accepted revision. Reverting an editing buffer can change only drafts.
    """
    result = import_workflow(idea)
    _cas(result, expected_revision, expected_draft_version)
    _enum(step, STEP_ORDER, 'step')
    require(step != 'review', 'Review is derived from a verified handoff packet', 'derived_step')
    fields = validate_step_fields(step, fields)
    _hash(source_digest, 'source_digest')
    text(actor, 'actor', 200)
    text(timestamp, 'timestamp', 100)
    _opaque_id(evidence_id, 'evidence_id')
    source = acceptance_source(result, step, fields, extra_dependencies)
    require(source_digest == source['source_digest'], 'Acceptance sources changed', 'stale_source')
    workflow = result['workflow']
    record = workflow['steps'][step]
    same_fields = record['fields'] == fields
    same_dependencies = record['acceptance'] is not None and record['acceptance']['dependencies'] == source['dependencies']
    if (same_fields and same_dependencies and not record['invalidated_by'] and
            not (step == 'shape' and result['status'] == 'archived')):
        # Own editing buffers do not invalidate unchanged accepted data. Revert
        # discards that draft as a draft-only mutation, not an accepted revision.
        draft_removed = step in workflow['drafts']
        if draft_removed:
            del workflow['drafts'][step]
            workflow['draft_version'] += 1
            integer(workflow['draft_version'], 'draft_version')
        next_step = _next_step(workflow)
        navigated = workflow['current_step'] != next_step
        workflow['current_step'] = next_step
        return _result(result, draft_removed or navigated)
    # Navigation prerequisites are separate from consumed-input dependencies.
    # A capture edit must not erase priorities, but a new choice cannot bypass
    # an earlier incomplete/review panel. Explicit archived Shape acceptance
    # starts the next active revision even when its inputs are unchanged.
    _, current = _step_states(workflow)
    for earlier in STEP_ORDER[:STEP_ORDER.index(step)]:
        require(current[earlier], 'Complete earlier step '+earlier, 'not_ready')
    # A changed editing buffer must be explicitly accepted as submitted; a
    # matching buffer is consumed without creating a draft-only version change.
    receipt = {'accepted_revision': result['revision'] + 1, 'evidence_id': evidence_id,
               'source_revision': result['revision'], 'source_digest': source_digest,
               'dependencies': source['dependencies'], 'actor': actor, 'timestamp': timestamp}
    workflow['steps'][step] = {'fields': fields, 'acceptance': validate_acceptance(receipt), 'invalidated_by': []}
    workflow['drafts'].pop(step, None)
    invalidated = _invalidate(workflow, (step,))
    workflow['current_step'] = _next_step(workflow)
    _mirror_legacy(result, step, fields, actor, timestamp)
    result['revision'] += 1
    result['status'] = 'active'
    snapshot = {'revision': result['revision'], 'shape': copy.deepcopy(result['shape']),
                'ratings': copy.deepcopy(result['ratings']), 'assessments': copy.deepcopy(result['assessments']),
                'actor': actor, 'action': 'accept-'+step, 'timestamp': timestamp}
    result['revisions'].append(adapt_snapshot(snapshot, workflow))
    return _result(result, True, invalidated, accepted_changed=True)


def capture_workflow(idea, fields, *, new_capture=False, actor, timestamp, evidence_id, source_digest):
    """Attach a genuine revision-one capture receipt during new-idea creation.

    new_capture is a trusted service-operation assertion, never browser input.
    Existing legacy records must import drafts and use explicit human acceptance.
    """
    require(new_capture is True, 'Capture evidence is only attached by new capture', 'not_ready')
    result = _idea(idea)
    require(result['revision'] == 1 and 'workflow' not in result and result['revisions'][0]['action'] == 'capture',
            'Capture receipt requires a fresh original revision')
    fields = validate_step_fields('capture', fields)
    origin = result.get('origin', {})
    require(fields['raw_text'] == origin.get('text'), 'Capture differs from immutable origin')
    require(hashlib.sha256(fields['raw_text'].encode('utf-8')).hexdigest() == origin.get('sha256'), 'Origin hash mismatch')
    workflow = empty_workflow()
    expected = acceptance_source(dict(result, workflow=workflow), 'capture', fields)
    require(source_digest == expected['source_digest'], 'Capture source mismatch', 'stale_source')
    receipt = {'accepted_revision': 1, 'source_revision': 1, 'evidence_id': evidence_id,
               'source_digest': source_digest, 'dependencies': {}, 'actor': actor, 'timestamp': timestamp}
    workflow['steps']['capture'] = {'fields': fields, 'acceptance': validate_acceptance(receipt), 'invalidated_by': []}
    workflow['current_step'] = 'priorities'
    result['workflow'] = workflow
    result['revisions'][0] = adapt_snapshot(result['revisions'][0], workflow)
    return _result(result, True, accepted_changed=True)
