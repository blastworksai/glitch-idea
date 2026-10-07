"""Packaged Exploration acceptance checks.

Workflow owns requirements/reduction; OwnerService owns proposal provenance.
The investment/experiment requirement follows the ACCEPTED Method, which is
read from the workflow and passed on to the field validation.
"""
from idea_domain import require
from idea_service import TrustedContext, TrustedStepHandler
from idea_workflow import acceptance_source, accepted_method_selection, import_workflow, validate_step_fields


def validate(state, idea, payload, source, context):
    require(isinstance(context, TrustedContext), 'Trusted Exploration context required', 'invalid_handler')
    require(payload.get('step') == 'exploration' and payload.get('idea_id') == idea.get('idea_id') and
            state['ideas'].get(idea['idea_id']) is idea, 'Exploration target differs', 'invalid_handler')
    fields = validate_step_fields('exploration', payload['fields'],
                                  method_selection=accepted_method_selection(import_workflow(idea)['workflow']))
    require(source == acceptance_source(idea, 'exploration', fields),
            'Final Exploration inputs changed', 'stale_source')


HANDLER = TrustedStepHandler(validate)
