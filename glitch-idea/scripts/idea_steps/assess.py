"""Canonical Assess acceptance and placement.

OwnerService's live SourceAdapter validates linked proposal provenance and the
original proposed insertion before this handler. Workflow acceptance appends
the attributed domain assessment exactly once. This handler validates the final
human choice and composes its placement snapshot in the same transaction state;
it performs no I/O, publication, automatic ranking or client score acceptance.
"""
import copy

from idea_assessment import validate_actual_position
from idea_domain import assessment, integer, require
from idea_service import TrustedContext, TrustedStepHandler, _invalidate_placement
from idea_workflow import acceptance_source, validate_step_fields

ROUTES = ()


def validate(state, idea, payload, source, context):
    require(isinstance(context, TrustedContext), 'Trusted Assess context required', 'invalid_handler')
    require(payload.get('step') == 'assess' and payload.get('idea_id') == idea.get('idea_id') and
            state['ideas'].get(idea['idea_id']) is idea, 'Assess target differs', 'invalid_handler')
    require(idea['status'] == 'active', 'Archived assessment is immutable', 'archived_revision')
    integer(payload.get('expected_backlog_revision'), 'expected backlog revision')
    require(payload['expected_backlog_revision'] == state['backlog_revision'],
            'Stale backlog revision', 'stale_backlog')
    fields = validate_step_fields('assess', payload['fields'])
    assessment(fields['assessment'])
    require(source == acceptance_source(idea, 'assess', fields),
            'Final Assess inputs changed', 'stale_source')
    validate_actual_position(fields['position'], state['order'], idea['idea_id'])


def apply(state, idea, payload, source, context):
    """Apply only after a changed canonical acceptance; Service owns this gate."""
    require(isinstance(context, TrustedContext) and state['ideas'].get(idea['idea_id']) is idea,
            'Assess apply target differs', 'invalid_handler')
    record = idea['workflow']['steps']['assess']
    receipt = record['acceptance']
    fields = validate_step_fields('assess', payload['fields'])
    require(record['fields'] == fields and receipt is not None and
            receipt['accepted_revision'] == idea['revision'] and receipt['actor'] == context.actor and
            receipt['source_digest'] == source['source_digest'],
            'Canonical Assess acceptance required', 'invalid_handler')
    # Workflow already computed the exact domain formula and attributed it to
    # this receipt. Never append a second assessment or substitute a UI score.
    canonical = dict(assessment(fields['assessment']), actor=receipt['actor'], timestamp=receipt['timestamp'])
    require(bool(idea['assessments']) and idea['assessments'][-1] == canonical,
            'Canonical domain assessment differs', 'invalid_handler')
    integer(payload.get('expected_backlog_revision'), 'expected backlog revision')
    require(payload['expected_backlog_revision'] == state['backlog_revision'],
            'Stale backlog revision', 'stale_backlog')
    position = validate_actual_position(fields['position'], state['order'], idea['idea_id'])
    before_order = list(state['order'])
    order = [key for key in before_order if key != idea['idea_id']]
    order.insert(position['actual_position']-1, idea['idea_id'])
    old_revision = state['backlog_revision']
    state['order'] = order
    state['backlog_revision'] += 1
    reason = position['override_reason'] or 'Accepted assessment placement'
    state['placements'].append(dict(idea_id=idea['idea_id'], idea_revision=idea['revision'],
        position=position['actual_position'], reason=reason, actor=receipt['actor'], timestamp=receipt['timestamp'],
        source_backlog_revision=old_revision, accepted_backlog_revision=state['backlog_revision'],
        neighbors=copy.deepcopy(position['neighbors']),
        snapshot=dict(ratings=copy.deepcopy(idea['ratings']), assessments=copy.deepcopy(idea['assessments']))))
    _invalidate_placement(state, before_order, context.actor, reason,
                          exclude=(idea['idea_id'],), refresh_draft=False)


HANDLER = TrustedStepHandler(validate, apply)
