"""Packaged Method final-field/source validation.

Workflow owns conditional requirements and mutation; OwnerService's provider
checks memory/proposal provenance before this pure handler. The human may choose
a method different from the recommendation without changing immutable evidence.
"""
from idea_domain import require
from idea_service import TrustedContext, TrustedStepHandler
from idea_workflow import acceptance_source, validate_step_fields


def validate(state, idea, payload, source, context):
    require(isinstance(context, TrustedContext), 'Trusted Method context required', 'invalid_handler')
    require(payload.get('step') == 'method' and payload.get('idea_id') == idea.get('idea_id') and
            state['ideas'].get(idea['idea_id']) is idea, 'Method target differs', 'invalid_handler')
    fields = validate_step_fields('method', payload['fields'])
    require(source == acceptance_source(idea, 'method', fields),
            'Final Method inputs changed', 'stale_source')


HANDLER = TrustedStepHandler(validate)
