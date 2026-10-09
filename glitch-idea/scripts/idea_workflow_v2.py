"""Frozen validators for the workflow as shipped in glitch-idea v0.2 (commit 1fcbf21).

Validation only: this reads what v0.2 wrote, so the v2 -> v3 migration step can
trust a record before it converts it and the store can read historical v2
snapshots. It never writes, never changes, and is never edited after this
release. Extracted verbatim from 1fcbf21 idea_workflow.py; only the module
docstring, this header and the dropped writers (adapt_snapshot and everything
after it) differ.
"""
import copy
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

