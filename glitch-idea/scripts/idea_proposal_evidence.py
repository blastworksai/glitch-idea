"""Immutable current-agent suggestion codec.

Pure detached records/bytes; no Store, broker, filesystem or publication effects.
This is not legacy placement metadata or accepted workflow state. The source
digest preserves the frozen workflow step-map convention for old operations;
assessment uses its strictly typed specialized source. Draft CAS is separate.
Callers supply safe source references, never raw private-memory retrievals or
credentials. Existing typed workspace input may contain its confirmed path;
arbitrary paths, credential fields and runtime records are not schema members.
Markdown imports are local so its later link traversal can import this codec.
"""
import copy
import hashlib
import json
import math
import re

from idea_domain import MAX_INPUT, integer, require, text
from idea_workflow import (MEMORY_STATUSES, STEP_ORDER, check_memory_preference, source_digest,
                           validate_step_fields)
from idea_assessment import (assessment_digest, validate_assessment_source,
                             validate_assessment_proposal)

RECORD_KEYS = frozenset(('schema_version', 'kind', 'proposal_id', 'binding_id',
    'generation', 'actor', 'timestamp', 'request_id', 'session_id', 'idea_id',
    'accepted_revision', 'draft_version', 'operation', 'source_digest', 'data', 'proposal'))
LINK_KEYS = frozenset(('proposal_id', 'path', 'sha256'))
OPERATIONS = frozenset(('discovery', 'exploration', 'memory', 'method', 'visual_brief', 'assessment', 'position'))
SUPPORTED = frozenset(('discovery', 'exploration', 'memory', 'method', 'visual_brief', 'assessment'))
_PATH = re.compile(r'history/(idea_[0-9a-f]{32})/metadata/([0-9a-f]{64})\.md')
_HASH = re.compile(r'[0-9a-f]{64}')


def _exact(value, keys, name):
    require(type(value) is dict and all(type(k) is str for k in value)
            and set(value) == set(keys), 'Unexpected ' + name + ' fields')


def _tree(value):
    pending, count = [(value, 1)], 0
    while pending:
        item, depth = pending.pop(); count += 1
        require(count <= 100000 and depth <= 32, 'Proposal tree exceeds limit', 'too_large')
        require(type(item) in (dict, list, str, int, float, bool, type(None)), 'Unsupported proposal value')
        if type(item) is dict:
            require(all(type(k) is str for k in item), 'Proposal keys must be strings')
            for key, child in item.items():
                pending.extend(((key, depth + 1), (child, depth + 1)))
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is float:
            require(math.isfinite(item), 'Nonfinite proposal value')
        elif type(item) is str:
            require(not any(0xD800 <= ord(c) <= 0xDFFF for c in item), 'Invalid proposal Unicode')


def _canonical(value):
    _tree(value)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'), allow_nan=False).encode('utf-8')
    require(len(raw) <= MAX_INPUT, 'Proposal record exceeds 1 MiB', 'too_large')
    return raw


def _id(value, prefix):
    require(type(value) is str and re.fullmatch(prefix + r'_[0-9a-f]{32}', value),
            'Invalid ' + prefix + ' identifier')


def _hash(value):
    require(type(value) is str and _HASH.fullmatch(value), 'Invalid proposal hash')


def _memory(value):
    require(type(value) is dict and {'status', 'sources', 'rationale'} <= set(value)
            <= {'status', 'sources', 'rationale', 'preferred_method'}, 'Invalid memory proposal fields')
    validate_step_fields('method', {'memory': value}, partial=True)
    require(type(value['status']) is str and value['status'] in MEMORY_STATUSES, 'Invalid memory status')
    require(type(value['sources']) is list, 'Memory sources must be a list')
    require(all(type(s) is str and s.strip() for s in value['sources']), 'Memory sources must be nonempty references')
    if value['rationale'] is not None:
        text(value['rationale'], 'memory rationale')
    check_memory_preference(value, 'memory')
    if value['status'] == 'found':
        require(bool(value['sources']) and type(value['rationale']) is str
                and bool(value['rationale'].strip()), 'Found preference requires sources and rationale')
    if value['status'] in ('unavailable', 'error'):
        require(not value['sources'], 'Unavailable memory cannot claim sources')


def validate_record(record):
    """Validate exact schema and original source digest; return a detached copy."""
    _exact(record, RECORD_KEYS, 'agent proposal')
    _canonical(record)  # Bound recursive input before typed traversal/copy.
    require(type(record['schema_version']) is int and record['schema_version'] == 1,
            'Unsupported agent proposal schema')
    require(type(record['kind']) is str and record['kind'] == 'agent-proposal', 'Invalid proposal kind')
    for field, prefix in (('proposal_id', 'proposal'), ('binding_id', 'binding'),
                          ('generation', 'agent'), ('session_id', 'session'), ('idea_id', 'idea')):
        _id(record[field], prefix)
    require(type(record['request_id']) is str
            and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', record['request_id']), 'Invalid request ID')
    text(record['actor'], 'proposal actor', 200)
    text(record['timestamp'], 'proposal timestamp', 100)
    integer(record['accepted_revision'], 'accepted revision', 1)
    integer(record['draft_version'], 'draft version')
    operation = record['operation']
    # Schema-1 'shape' proposals predate workflow v3 and are refused, never decoded.
    require(operation != 'shape', 'This proposal was made with an older glitch-idea', 'unsupported_proposal_version')
    require(type(operation) is str and operation in OPERATIONS, 'Unknown proposal operation')
    require(operation in SUPPORTED, 'Proposal codec is unavailable for this operation', 'operation_unavailable')
    _hash(record['source_digest'])
    if operation == 'assessment':
        source = {name: record[name] for name in ('accepted_revision', 'draft_version', 'data')}
        validate_assessment_source(source, idea_id=record['idea_id'])
        require(record['source_digest'] == assessment_digest(operation, source, idea_id=record['idea_id']),
                'Proposal source digest differs from recorded input', 'stale_source')
        validate_assessment_proposal(record['proposal'], source=source, idea_id=record['idea_id'])
        return copy.deepcopy(record)
    # Keep the previous schema1 validation/encoding path byte-for-byte for
    # Discovery/Exploration/Memory/Method. No old record is adapted or rewritten on read.
    require(type(record['data']) is dict and set(record['data']) <= set(STEP_ORDER), 'Invalid source step map')
    for step, fields in record['data'].items():
        validate_step_fields(step, fields, partial=True)
    require(record['source_digest'] == source_digest(operation, record['accepted_revision'], record['data']),
            'Proposal source digest differs from recorded input', 'stale_source')
    if operation == 'memory':
        _memory(record['proposal'])
    elif operation == 'visual_brief':
        # The prototype-skill signal only: a closed enum, never free text, never a design choice.
        require(type(record['proposal']) is dict and set(record['proposal']) == {'prototype_skill'}
                and type(record['proposal']['prototype_skill']) is str
                and record['proposal']['prototype_skill'] in ('available', 'unavailable'),
                'Invalid visual brief proposal')
    else:
        if operation == 'method':
            require(type(record['proposal']) is dict and set(record['proposal']) == {'memory'},
                    'Method proposal carries memory only')
            validate_step_fields('method', record['proposal'], partial=True)
            _memory(record['proposal']['memory'])
        else:
            validate_step_fields(operation, record['proposal'], legacy=True)
    return copy.deepcopy(record)


def canonical_record(record):
    """Canonical validated record bytes used to address immutable evidence."""
    return _canonical(validate_record(record))


def proposal_path(record):
    checked = validate_record(record)
    return 'history/' + checked['idea_id'] + '/metadata/' + hashlib.sha256(_canonical(checked)).hexdigest() + '.md'


def _body(record):
    def readable(value):
        # Indentation keeps hostile Markdown/HTML inside a literal code block.
        return ''.join('    ' + line + '\n' for line in
                       json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).splitlines())
    return ('# Immutable agent proposal\n\nDo not edit this evidence. '
            'A suggestion is not an accepted decision.\n\n'
            '## Proposed fields\n\n' + readable(record['proposal']) +
            '\n## Recorded source inputs\n\n' + readable(record['data']))


def encode_proposal(record):
    """Encode a bounded, readable immutable Markdown document."""
    from idea_markdown import encode_document
    checked = validate_record(record)
    raw = encode_document(checked, _body(checked))
    require(len(raw) <= MAX_INPUT, 'Proposal Markdown exceeds 1 MiB', 'too_large')
    return raw


def validate_link(link, *, record=None, raw=None, expected_idea_id=None):
    """Validate confined link identity and optional record/bytes witness."""
    _exact(link, LINK_KEYS, 'proposal link')
    _id(link['proposal_id'], 'proposal')
    _hash(link['sha256'])
    require(type(link['path']) is str and _PATH.fullmatch(link['path']), 'Invalid proposal evidence path')
    if expected_idea_id is not None:
        _id(expected_idea_id, 'idea')
        require(_PATH.fullmatch(link['path']).group(1) == expected_idea_id, 'Wrong proposal idea', 'corrupt_store')
    if raw is not None:
        require(type(raw) is bytes and len(raw) <= MAX_INPUT, 'Invalid proposal bytes', 'too_large')
        require(hashlib.sha256(raw).hexdigest() == link['sha256'], 'Proposal bytes hash mismatch', 'corrupt_store')
        observed = decode_proposal(raw, path=link['path'], expected_idea_id=expected_idea_id)
        if record is not None:
            require(observed == validate_record(record), 'Proposal record differs from bytes', 'corrupt_store')
        record = observed
    if record is not None:
        checked = validate_record(record)
        require(link['proposal_id'] == checked['proposal_id'] and link['path'] == proposal_path(checked),
                'Proposal link differs from immutable identity', 'corrupt_store')
        if expected_idea_id is not None:
            require(checked['idea_id'] == expected_idea_id, 'Wrong proposal idea', 'corrupt_store')
    return copy.deepcopy(link)


def proposal_link(record, raw=None):
    checked = validate_record(record)
    if raw is None:
        raw = encode_proposal(checked)
    require(type(raw) is bytes, 'Proposal evidence must be bytes')
    link = dict(proposal_id=checked['proposal_id'], path=proposal_path(checked),
                sha256=hashlib.sha256(raw).hexdigest())
    return validate_link(link, record=checked, raw=raw)


def decode_proposal(raw, *, path=None, expected_idea_id=None, link=None):
    """Decode validated immutable evidence; inspect supplied path/hash witnesses."""
    from idea_markdown import parse_document
    require(type(raw) is bytes, 'Proposal evidence must be bytes')
    require(len(raw) <= MAX_INPUT, 'Proposal Markdown exceeds 1 MiB', 'too_large')
    document = parse_document(raw)
    checked = validate_record(document.metadata)
    require(not document.has_comments and document.body == _body(checked),
            'Immutable proposal body/comments differ from record', 'corrupt_store')
    # One record/path has one immutable document representation.
    # YAML-equivalent whitespace/order/newlines cannot create a second byte hash.
    require(raw == encode_proposal(checked),
            'Immutable proposal bytes are not canonical', 'corrupt_store')
    if path is not None:
        require(type(path) is str and path == proposal_path(checked), 'Wrong proposal path', 'corrupt_store')
    if expected_idea_id is not None:
        _id(expected_idea_id, 'idea')
        require(checked['idea_id'] == expected_idea_id, 'Wrong proposal idea', 'corrupt_store')
    if link is not None:
        # Avoid recursive byte validation: the document is already validated here.
        validate_link(link, record=checked, expected_idea_id=expected_idea_id)
        require(link['sha256'] == hashlib.sha256(raw).hexdigest(), 'Proposal bytes hash mismatch', 'corrupt_store')
    return checked
