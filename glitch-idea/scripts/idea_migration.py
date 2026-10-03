"""Pure v1-to-Markdown preparation/verification.

Store supplies observed bytes under its lock; existing transaction publication
freezes/deletes legacy authority. No filesystem access or rollback lives here.
"""
import copy
import json
import re

from idea_domain import IdeaError, MAX_INPUT, MAX_STATE, decode, digest, encoded, integer, require, text
from idea_transactions import FROZEN, RECEIPT

MARKER = 'glitch_idea_migration'
RECEIPT_KEYS = {'schema_version', 'kind', 'source_schema', 'target_schema',
                'legacy_sha256', 'baseline_sha256', 'source_transaction_revision',
                'target_transaction_revision', 'source_path', 'frozen_path', 'actor', 'timestamp'}


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _hash(value):
    require(type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None,
            'Invalid migration SHA-256', 'corrupt_store')


def _state(raw):
    require(type(raw) is bytes and len(raw) <= MAX_STATE, 'Invalid frozen legacy bytes', 'corrupt_store')
    value = decode(raw)
    from idea_store import validate
    validate(value)
    return value


def verify(receipt_raw, frozen_raw):
    """Strict immutable receipt/source validation; no current-state rollback."""
    require(type(receipt_raw) is bytes and len(receipt_raw) <= MAX_INPUT, 'Invalid migration receipt bytes', 'corrupt_store')
    receipt = decode(receipt_raw)
    require(type(receipt) is dict and set(receipt) == RECEIPT_KEYS, 'Unexpected migration receipt schema', 'corrupt_store')
    require(type(receipt['schema_version']) is int and receipt['schema_version'] == 1 and receipt['kind'] == 'migration', 'Unsupported migration receipt', 'corrupt_store')
    require(type(receipt['source_schema']) is int and receipt['source_schema'] == 1 and
            type(receipt['target_schema']) is int and receipt['target_schema'] == 2, 'Unsupported migration formats', 'corrupt_store')
    require(receipt['source_path'] == 'state.json' and receipt['frozen_path'] == FROZEN, 'Migration path mismatch', 'corrupt_store')
    for key in ('legacy_sha256', 'baseline_sha256'):
        _hash(receipt[key])
    for key in ('source_transaction_revision', 'target_transaction_revision'):
        integer(receipt[key], key)
    source = receipt['source_transaction_revision']
    require(receipt['target_transaction_revision'] in (source, source+1), 'Migration counter mismatch', 'corrupt_store')
    text(receipt['actor'], 'migration actor', 200)
    text(receipt['timestamp'], 'migration timestamp', 200)
    baseline = _state(frozen_raw)
    require(digest(frozen_raw) == receipt['legacy_sha256'] and
            digest(_canonical(baseline)) == receipt['baseline_sha256'] and
            baseline['transaction_revision'] == source, 'Frozen source/receipt mismatch', 'corrupt_store')
    return copy.deepcopy(receipt)


def pointer(receipt):
    return {'legacy_sha256': receipt['legacy_sha256'], 'receipt_path': RECEIPT, 'frozen_path': FROZEN}


def verify_pointer(value, receipt):
    require(type(value) is dict and set(value) == {'legacy_sha256', 'receipt_path', 'frozen_path'}
            and value == pointer(receipt), 'Migration evidence pointer mismatch', 'corrupt_store')


def prepare(legacy_raw, before_state, after_state, existing_files, *, actor, timestamp):
    """Produce exact migration after-images and observed hashes, no writes.

    existing_files contains state.json and every old plan/archive view. Missing
    or altered views require explicit repair; migration never overwrites them.
    """
    from idea_store import validate
    import idea_markdown as md
    baseline = _state(legacy_raw)
    require(_canonical(baseline) == _canonical(before_state), 'Legacy baseline changed', 'save_conflict')
    validate(after_state)
    require(type(existing_files) is dict and existing_files.get('state.json') == legacy_raw, 'Missing observed legacy source', 'save_conflict')
    baseline_files = md.encode_state(before_state)
    require(_canonical(md.decode_state(baseline_files)) == _canonical(before_state), 'Baseline Markdown reconstruction differs', 'corrupt_store')
    after = md.encode_state(after_state)
    require(_canonical(md.decode_state(after)) == _canonical(after_state), 'Migrated Markdown reconstruction differs', 'corrupt_store')
    required = {'state.json'}
    for idea in before_state['ideas'].values():
        for plan in idea['plans']:
            path = 'plan-evidence/'+plan['plan_id']+'.md'
            required.add(path)
            require(existing_files.get(path) == plan['content'].encode('utf-8'),
                    'Missing/altered accepted plan view; repair before migration: '+path, 'migration_view_required')
    for key, archive in before_state['archives'].items():
        path = 'archive/'+key
        required.add(path)
        require(existing_files.get(path) == encoded(archive),
                'Missing/altered archive view; repair before migration: '+path, 'migration_view_required')
    require(set(existing_files) == required, 'Orphan legacy authority/evidence; migration refused', 'ambiguous_store')
    receipt = dict(schema_version=1, kind='migration', source_schema=1, target_schema=2,
                   legacy_sha256=digest(legacy_raw), baseline_sha256=digest(_canonical(before_state)),
                   source_transaction_revision=before_state['transaction_revision'],
                   target_transaction_revision=after_state['transaction_revision'],
                   source_path='state.json', frozen_path=FROZEN, actor=actor, timestamp=timestamp)
    raw_receipt = encoded(receipt)
    verify(raw_receipt, legacy_raw)
    index = md.decode_index(after['IDEAS.md'])
    metadata = copy.deepcopy(index.metadata)
    require(MARKER not in metadata['extensions'], 'Migration marker already exists', 'ambiguous_store')
    metadata['extensions'][MARKER] = pointer(receipt)
    after['IDEAS.md'] = md.encode_document(metadata, index.body)
    changes = dict(after)
    for key, archive in after_state['archives'].items():
        changes['archive/'+key] = encoded(archive)
    changes.update({FROZEN: legacy_raw, RECEIPT: raw_receipt})
    expected = {path: digest(existing_files[path]) if path in existing_files else None for path in changes}
    return {'after': after, 'changes': changes, 'expected': expected, 'legacy_sha256': receipt['legacy_sha256']}
