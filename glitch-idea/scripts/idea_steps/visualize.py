"""Pure Visualize acceptance over Store-verified detached data.

Service supplies the active transaction inventory, including prepared sets.
This handler has no Store, filesystem, HTTP or publication capability. Brief
provenance has no packaged provider: non-null brief IDs remain unavailable.
"""
import re

import idea_asset_evidence as codec
from idea_domain import require
from idea_service import TrustedContext, TrustedStepHandler
from idea_workflow import acceptance_source, derive_state, source_digest, validate_step_fields


def current_source(idea):
    """Current accepted Capture, Discovery and Exploration witness, independent of overall revision."""
    projection = derive_state(idea)
    witness = {}
    for step in ('capture','discovery','exploration'):
        require(projection['steps'][step]['status']=='saved','Current accepted '+step+' is required','not_ready')
        record = idea['workflow']['steps'][step]; revision = record['acceptance']['accepted_revision']
        witness[step] = dict(revision=revision,digest=source_digest(step,revision,{step:record['fields']}))
    return witness


def _inventory(context,idea):
    value = context.asset_inventory
    require(type(value) is dict and set(value)=={'records','orphans'} and type(value['records']) is list
            and len(value['records'])<=codec.MAX_LINKS,'Verified asset inventory is unavailable','supporting_evidence')
    assets,sets,ids = {},{},set()
    for entry in value['records']:
        require(type(entry) is dict and set(entry)=={'record','evidence','blob'},'Invalid verified asset entry','supporting_evidence')
        record = codec.validate_record(entry['record']); rid = codec.record_id(record)
        require(record['idea_id']==idea['idea_id'] and record['source_revision']<=idea['revision']
                and rid not in ids,'Asset inventory target/identity differs','supporting_evidence')
        ids.add(rid)
        require(entry['evidence']==codec.record_link(record),'Immutable asset evidence differs','supporting_evidence')
        if record['kind']=='asset':
            require(entry['blob']==dict(path=record['blob_path'],size=record['size'],sha256=record['sha256']),
                    'Complete asset blob witness differs','supporting_evidence')
            assets[record['asset_id']] = record
        else:
            require(entry['blob'] is None,'Unexpected non-asset blob witness','supporting_evidence')
            if record['kind']=='design-set': sets[record['set_id']] = record
    return assets,sets


def validate(state,idea,payload,source,context):
    require(isinstance(context,TrustedContext),'Trusted Visualize context required','invalid_handler')
    require(payload.get('step')=='visualize' and payload.get('idea_id')==idea.get('idea_id')
            and state['ideas'].get(idea['idea_id']) is idea,'Visualize target differs','invalid_handler')
    fields = validate_step_fields('visualize',payload['fields'])
    require(source==acceptance_source(idea,'visualize',fields),'Final Visualize inputs changed','stale_source')
    require(payload.get('proposal_id') is None,'Visualize proposal provenance is unavailable','supporting_evidence')
    require(fields.get('brief_evidence_id') is None,'Verified brief provenance is unavailable','supporting_evidence')
    if fields['disposition']!='accepted_set':
        # Skip is one click: a reason is optional.
        require(fields.get('design_set_id') is None,'A skipped Visualize decision must clear its set','supporting_evidence')
        return
    set_id = fields.get('design_set_id')
    require(type(set_id) is str and re.fullmatch(r'set_[0-9a-f]{32}',set_id),
            'Unknown immutable design set','asset_not_found')
    assets,sets = _inventory(context,idea)
    require(set_id in sets,'Missing complete design set','asset_not_found')
    selected = sets[set_id]; witness = current_source(idea)
    require(selected['source']==witness and selected['source_digest']==codec.source_digest(witness),
            'Design set consumed stale Capture, Discovery or Exploration','stale_source')
    # Codec enforces member uniqueness, exact types/extension and all size caps.
    for member in selected['members']:
        asset = assets.get(member['asset_id'])
        require(asset is not None and member==dict(asset_id=asset['asset_id'],name=asset['name'],
            type=asset['validated_type'],size=asset['size'],sha256=asset['sha256']),
            'Design set membership differs from verified complete assets','supporting_evidence')
    if fields.get('source')=='prototype':
        # The prototype road delivers exactly the bundle and its screenshot.
        require(sorted(member['type'] for member in selected['members'])==['application/zip','image/png'],
                'A prototype design set is one zip and one png','supporting_evidence')


HANDLER = TrustedStepHandler(validate)
ROUTES = ()
