"""Packaged Discovery acceptance checks.

Workflow owns requirements/reduction; OwnerService owns proposal provenance.
This pure handler checks the final human fields and their consumed-source proof.
"""
from idea_domain import require
from idea_service import TrustedContext, TrustedStepHandler
from idea_workflow import acceptance_source, validate_step_fields


def validate(state, idea, payload, source, context):
    require(isinstance(context, TrustedContext), 'Trusted Discovery context required', 'invalid_handler')
    require(payload.get('step') == 'discovery' and payload.get('idea_id') == idea.get('idea_id') and
            state['ideas'].get(idea['idea_id']) is idea, 'Discovery target differs', 'invalid_handler')
    fields = validate_step_fields('discovery', payload['fields'])
    require(source == acceptance_source(idea, 'discovery', fields),
            'Final Discovery inputs changed', 'stale_source')


HANDLER = TrustedStepHandler(validate)
