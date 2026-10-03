"""Pure assessment sources, proposals and placement checks.

Markdown/Store and the authenticated agent provider own publication, correlation
and acceptance. These helpers never rank automatically, mutate state, read files
or acquire locks. Scores always come from idea_domain.assessment. The specialized
source is deliberately distinct from the existing workflow step-map digest.
"""
import copy
import hashlib
import json
import math

from idea_domain import (MAX_INPUT, assessment, check_id, integer,
                         require)
from idea_workflow import (derive_state, import_workflow, validate_snapshot,
                           validate_step_fields)

ROUTES = ()
CONSUMED_STEPS = ('capture', 'priorities', 'shape')
POSITION_KEYS = frozenset(('proposed_position', 'actual_position', 'neighbors',
                           'override_reason'))


def _exact(value, keys, name):
    require(type(value) is dict and set(value) == set(keys),
            'Unexpected '+name+' fields')


def _bounded(value):
    """Match the broker's source bounds before typed traversal or copying."""
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        require(depth <= 24 and count <= 20000,
                'Assessment source exceeds tree limits', 'too_large')
        require(type(item) in (dict, list, str, int, float, bool, type(None)),
                'Unsupported assessment source value')
        if type(item) is dict:
            require(all(type(key) is str for key in item),
                    'Assessment source keys must be strings')
            pending.extend((child, depth+1) for pair in item.items() for child in pair)
        elif type(item) is list:
            pending.extend((child, depth+1) for child in item)
        elif type(item) is str:
            require(not any(0xD800 <= ord(char) <= 0xDFFF for char in item),
                    'Invalid assessment source Unicode')
        elif type(item) is float:
            require(math.isfinite(item), 'Nonfinite assessment source value')
        elif type(item) is int:
            require(abs(item) <= 10**12, 'Assessment source integer exceeds limit')
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'), allow_nan=False).encode('utf-8')
    require(len(raw) <= MAX_INPUT, 'Assessment source exceeds 1 MiB', 'too_large')
    return raw


def _order(order, idea_id=None):
    require(type(order) is list, 'Backlog order must be a list')
    for key in order:
        check_id(key)
    require(len(order) == len(set(order)), 'Duplicate backlog idea ID')
    if idea_id is not None:
        check_id(idea_id)
        require(idea_id in order, 'Target is absent from backlog', 'not_found')


def _comparison(value):
    _exact(value, ('idea_id', 'revision', 'status', 'ratings', 'assessment'),
           'backlog comparison')
    check_id(value['idea_id'])
    integer(value['revision'], 'comparison revision', 1)
    require(type(value['status']) is str and value['status'] in ('active', 'archived'),
            'Invalid comparison status')
    # Reuse domain snapshot validation, including optional legacy attribution.
    # A missing optional field remains missing; no new author is inferred.
    validate_snapshot(dict(revision=value['revision'], shape=None,
        ratings=value['ratings'], assessments=[] if value['assessment'] is None
        else [value['assessment']], actor='validator', action='validate-comparison',
        timestamp='validation-only'))


def _backlog(value, idea_id=None):
    _exact(value, ('revision', 'order', 'comparisons'), 'assessment backlog')
    integer(value['revision'], 'backlog revision')
    _order(value['order'], idea_id)
    comparisons = value['comparisons']
    require(type(comparisons) is list and len(comparisons) == len(value['order']),
            'Backlog requires one comparison per ordered idea')
    for key, comparison in zip(value['order'], comparisons):
        _comparison(comparison)
        require(comparison['idea_id'] == key, 'Comparison differs from backlog order')


def validate_data(data, idea_id=None):
    """Validate the complete specialized data; return detached exact fields."""
    _bounded(data)
    _exact(data, ('steps', 'backlog', 'target'), 'assessment source data')
    _exact(data['steps'], CONSUMED_STEPS, 'consumed assessment steps')
    for step in CONSUMED_STEPS:
        validate_step_fields(step, data['steps'][step])
    _backlog(data['backlog'], idea_id)
    if data['target'] is not None:
        validate_step_fields('assess', data['target'], partial=True)
    if idea_id is not None:
        current = data['backlog']['comparisons'][data['backlog']['order'].index(idea_id)]
        ratings = current['ratings']
        require(ratings is not None and
                {key: ratings[key] for key in ('urgency', 'importance')} == data['steps']['priorities'],
                'Consumed priorities differ from target human ratings', 'stale_source')
    return copy.deepcopy(data)


def validate_assessment_source(source, *, idea_id=None):
    """Validate broker source envelope; codec supplies its recorded target ID."""
    _bounded(source)
    _exact(source, ('accepted_revision', 'draft_version', 'data'), 'assessment source')
    integer(source['accepted_revision'], 'accepted revision', 1)
    integer(source['draft_version'], 'draft version')
    validate_data(source['data'], idea_id)
    if idea_id is not None:
        backlog = source['data']['backlog']
        current = backlog['comparisons'][backlog['order'].index(idea_id)]
        require(current['revision'] == source['accepted_revision'],
                'Assessment source target revision differs', 'stale_source')
    return copy.deepcopy(source)


def assessment_digest(operation, source, *, idea_id=None):
    """Canonical new-operation digest; old workflow digest/bytes are untouched.

    draft_version has its separate response CAS. The entire target buffer is
    included in data, so response source drift is not hidden by that separation.
    """
    require(type(operation) is str and operation == 'assessment',
            'Assessment operation is unavailable', 'operation_unavailable')
    checked = validate_assessment_source(source, idea_id=idea_id)
    payload = dict(operation=operation, accepted_revision=checked['accepted_revision'],
                   data=checked['data'])
    return hashlib.sha256(_bounded(payload)).hexdigest()


def backlog_projection(state, idea_id):
    """Observe actual order and comparisons; no score sort or hidden authority.

    Latest assessment means the latest domain record, including a legacy record
    awaiting browser review. This projection does not claim workflow acceptance.
    """
    check_id(idea_id)
    require(type(state) is dict and type(state.get('ideas')) is dict,
            'Invalid assessment state')
    order = state.get('order')
    _order(order, idea_id)
    require(set(order) == set(state['ideas']), 'Backlog is not a permutation of ideas')
    integer(state.get('backlog_revision'), 'backlog revision')
    comparisons = []
    for key in order:
        idea = state['ideas'][key]
        require(type(idea) is dict and idea.get('idea_id') == key,
                'Backlog idea identity differs')
        assessments = idea.get('assessments')
        require(type(assessments) is list, 'Invalid domain assessments')
        comparison = dict(idea_id=key, revision=idea.get('revision'),
                          status=idea.get('status'), ratings=idea.get('ratings'),
                          assessment=assessments[-1] if assessments else None)
        _comparison(comparison)
        comparisons.append(comparison)
    result = dict(revision=state['backlog_revision'], order=order, comparisons=comparisons)
    _bounded(result)
    _backlog(result, idea_id)
    return copy.deepcopy(result)


def prepare_assessment_source(state, idea_id):
    """Use current accepted inputs and the whole durable target, never origin.

    Caller owns its active transaction; no Store access is needed here. Changed
    earlier drafts make a source unavailable instead of fabricating acceptance.
    """
    backlog = backlog_projection(state, idea_id)
    idea = import_workflow(state['ideas'][idea_id])
    workflow = idea['workflow']
    statuses = derive_state(idea)['steps']
    steps = {}
    for step in CONSUMED_STEPS:
        require(statuses[step]['status'] == 'saved',
                'Current acceptance required for '+step, 'not_ready')
        steps[step] = workflow['steps'][step]['fields']
    target = (workflow['drafts']['assess'] if 'assess' in workflow['drafts'] else
              workflow['steps']['assess']['fields'])
    result = dict(accepted_revision=idea['revision'], draft_version=workflow['draft_version'],
                  data=dict(steps=steps, backlog=backlog, target=target))
    return validate_assessment_source(result, idea_id=idea_id)


def insertion_neighbors(order, idea_id, position):
    """One-based insertion into the actual order after removing this idea."""
    _bounded(order)
    _order(order, idea_id)
    integer(position, 'position', 1, len(order))
    remaining = [key for key in order if key != idea_id]
    index = position-1
    return dict(before=remaining[index-1] if index else None,
                after=remaining[index] if index < len(remaining) else None)


def _position(value):
    _exact(value, POSITION_KEYS, 'assessment position')
    validate_step_fields('assess', {'position': value}, partial=True)
    integer(value['proposed_position'], 'proposed position', 1)
    integer(value['actual_position'], 'actual position', 1)
    _exact(value['neighbors'], ('before', 'after'), 'placement neighbors')
    return value


def validate_assessment_proposal(proposal, *, source=None, idea_id=None):
    """Agent fields are a suggestion; never an override or caller-scored input.

    Typed validation alone is possible for codec dispatch. Publication additionally
    passes the complete verified source and target identity to check neighbors.
    """
    _bounded(proposal)
    checked = validate_step_fields('assess', proposal)
    assessment(checked['assessment'])  # exact existing formula/unknown handling
    position = _position(checked['position'])
    require(position['actual_position'] == position['proposed_position'] and
            position['override_reason'] is None,
            'Agent suggestion cannot make an operator override', 'invalid_proposal')
    if source is not None:
        require(idea_id is not None, 'Source-backed proposal requires target identity')
        selected = validate_assessment_source(source, idea_id=idea_id)
        neighbors = insertion_neighbors(selected['data']['backlog']['order'], idea_id,
                                        position['proposed_position'])
        require(position['neighbors'] == neighbors,
                'Proposed neighbors differ from observed backlog', 'stale_backlog')
    elif idea_id is not None:
        check_id(idea_id)
        require(idea_id not in position['neighbors'].values(),
                'Target cannot be its own placement neighbor')
    return checked


def validate_actual_position(position, order, idea_id, *, original_proposal=None):
    """Validate the human's final insertion; caller checks backlog CAS atomically.

    Original suggestion remains linked independently of edited assessment fields.
    This helper returns detached fields only and never changes scores or order.
    """
    _bounded(position)
    checked = _position(position)
    expected = insertion_neighbors(order, idea_id, checked['actual_position'])
    integer(checked['proposed_position'], 'proposed position', 1, len(order))
    require(checked['neighbors'] == expected,
            'Actual placement neighbors changed', 'stale_backlog')
    if original_proposal is not None:
        original = validate_assessment_proposal(original_proposal, idea_id=idea_id)
        require(checked['proposed_position'] == original['position']['proposed_position'],
                'Original proposed position changed', 'proposal_mismatch')
    if checked['actual_position'] != checked['proposed_position']:
        reason = checked['override_reason']
        require(type(reason) is str and bool(reason.strip()),
                'Operator override requires a reason', 'not_ready')
    return copy.deepcopy(checked)
