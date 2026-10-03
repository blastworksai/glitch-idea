"""Pure immutable asset-evidence codec.

Records describe intent, verified completion or explicit design-set membership.
This module neither reads blobs nor establishes completion/current eligibility:
Store verifies files and the Visualize handler checks consumed source witnesses.
Names are display text only; download-header sanitization belongs to ingestion.
Canonical record addresses and canonical document byte hashes are distinct.
"""
import copy
import hashlib
import json
import math
import re

from idea_domain import MAX_INPUT, integer, require, text

MAX_FILE = 25 * 1024 * 1024
MAX_SET = 100 * 1024 * 1024
MAX_MEMBERS = 20
MAX_LINKS = 256  # Enforced by the owning Markdown/Store inventory, not one link.
LINK_KEYS = frozenset(('record_id', 'path', 'sha256'))
_COMMON = frozenset(('schema_version', 'kind', 'idea_id', 'source_revision', 'actor', 'timestamp'))
RECORD_KEYS = {
    'upload-intent': _COMMON | {'upload_id', 'asset_id', 'session_id', 'name', 'declared_type', 'size'},
    'asset': _COMMON | {'asset_id', 'upload_id', 'session_id', 'blob_path', 'name',
                      'declared_type', 'validated_type', 'size', 'sha256'},
    'design-set': _COMMON | {'set_id', 'session_id', 'source', 'source_digest', 'members'},
}
MIME_EXTENSIONS = {
    'image/png': frozenset(('png',)),
    'image/jpeg': frozenset(('jpg', 'jpeg')),
    'image/webp': frozenset(('webp',)),
    'application/pdf': frozenset(('pdf',)),
    'image/svg+xml': frozenset(('svg',)),
    'text/html': frozenset(('html', 'htm')),
    'text/css': frozenset(('css',)),
    'application/json': frozenset(('json',)),
    'text/markdown': frozenset(('md', 'markdown')),
    'text/plain': frozenset(('txt', 'text', 'md', 'markdown')),
    'application/zip': frozenset(('zip',)),
}
_PATH = re.compile(r'assets/evidence/[0-9a-f]{64}\.md')
_HASH = re.compile(r'[0-9a-f]{64}')
_RECORD_ID = re.compile(r'(?:upload|asset|set)_[0-9a-f]{32}')


def _exact(value, keys, name):
    require(type(value) is dict and all(type(k) is str for k in value)
            and set(value) == set(keys), 'Unexpected '+name+' fields')


def _tree(value):
    pending, nodes = [(value, 1)], 0
    while pending:
        item, depth = pending.pop(); nodes += 1
        require(nodes <= 100000 and depth <= 32, 'Asset tree exceeds limits', 'too_large')
        require(type(item) in (dict, list, str, int, float, bool, type(None)), 'Unsupported asset value')
        if type(item) is dict:
            require(all(type(k) is str for k in item), 'Asset keys must be strings')
            for key, child in item.items():
                pending.extend(((key, depth+1), (child, depth+1)))
        elif type(item) is list:
            pending.extend((child, depth+1) for child in item)
        elif type(item) is str:
            require(len(item) <= MAX_INPUT, 'Asset text exceeds limits', 'too_large')
            require(not any(0xD800 <= ord(c) <= 0xDFFF for c in item), 'Invalid asset Unicode')
        elif type(item) is float:
            require(math.isfinite(item), 'Nonfinite asset value')


def _canonical(value):
    _tree(value)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'), allow_nan=False).encode('utf-8')
    require(len(raw) <= MAX_INPUT, 'Asset record exceeds 1 MiB', 'too_large')
    return raw


def _id(value, prefix):
    require(type(value) is str and re.fullmatch(prefix+r'_[0-9a-f]{32}', value),
            'Invalid '+prefix+' identifier')


def _hash(value):
    require(type(value) is str and _HASH.fullmatch(value), 'Invalid asset digest')


def _file(name, mime, size):
    require(type(name) is str, 'Asset name must be display text')
    text(name, 'asset name', 4096)
    require(type(mime) is str and mime in MIME_EXTENSIONS, 'Unsupported asset type')
    # This inspects a suffix, never interprets the untrusted display name as a path.
    require('.' in name and name.rsplit('.', 1)[1].lower() in MIME_EXTENSIONS[mime],
            'Asset extension and type disagree')
    integer(size, 'asset size', 1, MAX_FILE)


def _source(source):
    _exact(source, ('capture', 'shape'), 'asset source')
    for step, witness in source.items():
        _exact(witness, ('revision', 'digest'), step+' source witness')
        integer(witness['revision'], step+' source revision', 1)
        _hash(witness['digest'])
    return copy.deepcopy(source)


def source_digest(source):
    """Hash compact accepted Capture/Shape witnesses, excluding overall revision."""
    return hashlib.sha256(_canonical(dict(operation='visualize-assets', source=_source(source)))).hexdigest()


def blob_path(asset_id):
    """Return the sole generated blob spelling; this is not filesystem access."""
    _id(asset_id, 'asset')
    return 'assets/blobs/'+asset_id+'.bin'


def validate_record(record):
    """Validate exact bounded schema and return a detached record."""
    _canonical(record)  # Bound hostile trees before any copy or typed traversal.
    require(type(record) is dict and type(record.get('kind')) is str
            and record['kind'] in RECORD_KEYS, 'Unknown asset record kind')
    kind = record['kind']
    _exact(record, RECORD_KEYS[kind], 'asset record')
    require(type(record['schema_version']) is int and record['schema_version'] == 1,
            'Unsupported asset record schema')
    _id(record['idea_id'], 'idea')
    integer(record['source_revision'], 'asset source revision', 1)
    text(record['actor'], 'asset actor', 200)
    text(record['timestamp'], 'asset timestamp', 100)
    if kind in ('upload-intent', 'asset'):
        for key, prefix in (('upload_id', 'upload'), ('asset_id', 'asset'), ('session_id', 'session')):
            _id(record[key], prefix)
        _file(record['name'], record['declared_type'], record['size'])
        if kind == 'asset':
            require(record['blob_path'] == blob_path(record['asset_id']),
                    'Asset blob path differs from generated identity', 'corrupt_store')
            require(type(record['validated_type']) is str
                    and record['validated_type'] == record['declared_type'], 'Validated asset type differs')
            _hash(record['sha256'])
    else:
        _id(record['set_id'], 'set')
        _id(record['session_id'], 'session')
        witness = _source(record['source'])
        require(all(entry['revision'] <= record['source_revision'] for entry in witness.values()),
                'Set source witness is newer than its source revision')
        _hash(record['source_digest'])
        require(record['source_digest'] == source_digest(witness), 'Set source digest differs', 'stale_source')
        members = record['members']
        require(type(members) is list and 1 <= len(members) <= MAX_MEMBERS,
                'Design set requires 1–20 explicit members', 'too_large')
        ids, total = set(), 0
        for member in members:
            _exact(member, ('asset_id', 'name', 'type', 'size', 'sha256'), 'set member')
            _id(member['asset_id'], 'asset')
            require(member['asset_id'] not in ids, 'Duplicate set member')
            ids.add(member['asset_id'])
            _file(member['name'], member['type'], member['size'])
            _hash(member['sha256'])
            total += member['size']
        require(total <= MAX_SET, 'Design set exceeds 100 MiB', 'too_large')
    return copy.deepcopy(record)


def canonical_record(record):
    return _canonical(validate_record(record))


def record_id(record):
    checked = validate_record(record)
    key = {'upload-intent': 'upload_id', 'asset': 'asset_id', 'design-set': 'set_id'}[checked['kind']]
    return checked[key]


def evidence_path(record):
    return 'assets/evidence/'+hashlib.sha256(canonical_record(record)).hexdigest()+'.md'


def _yaml_escape(character):
    point = ord(character)
    return 0x7f <= point <= 0x9f or point in (0x2028, 0x2029, 0xfffe, 0xffff)


def _literal_json(record, *, indent=None):
    """Escape YAML nonprintable scalars and Unicode line separators exactly.

    Keep ordinary Unicode literal (including non-BMP characters); YAML does
    not combine JSON ASCII surrogate-pair escapes into a Unicode scalar.
    """
    literal = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=indent,
                         separators=(',', ':') if indent is None else None, allow_nan=False)
    return ''.join('\\u'+format(ord(character),'04x') if _yaml_escape(character) else character
                   for character in literal)


def _body(record):
    # Split only generated LF; an untrusted separator cannot create body lines.
    literal = ''.join('    '+line+'\n' for line in _literal_json(record, indent=2).split('\n'))
    return ('# Immutable asset evidence\n\nDo not edit this evidence. '
            'Uploads alone are not an accepted design set.\n\n## Recorded metadata\n\n'+literal)


def encode_record(record):
    from idea_markdown import encode_document
    checked = validate_record(record)
    canonical = _canonical(checked).decode('utf-8')
    if any(_yaml_escape(character) for character in canonical):
        # Local JSON-as-YAML avoids rejected scalars and line-break folding. Keep
        # existing ordinary evidence bytes and canonical record addresses intact.
        raw = ('---\n'+_literal_json(checked)+'\n---\n'+_body(checked)).encode('utf-8')
    else:
        raw = encode_document(checked, _body(checked))
    require(len(raw) <= MAX_INPUT, 'Asset Markdown exceeds 1 MiB', 'too_large')
    return raw


def validate_link(link, *, record=None, raw=None, expected_idea_id=None):
    """Validate link shape; idea association additionally requires record/bytes."""
    _exact(link, LINK_KEYS, 'asset link')
    require(type(link['record_id']) is str and _RECORD_ID.fullmatch(link['record_id']), 'Invalid asset record ID')
    require(type(link['path']) is str and _PATH.fullmatch(link['path']), 'Invalid asset evidence path')
    _hash(link['sha256'])
    if expected_idea_id is not None:
        _id(expected_idea_id, 'idea')
    if raw is not None:
        require(type(raw) is bytes and len(raw) <= MAX_INPUT, 'Invalid asset evidence bytes', 'too_large')
        require(hashlib.sha256(raw).hexdigest() == link['sha256'], 'Asset evidence bytes hash differs', 'corrupt_store')
        observed = decode_record(raw, path=link['path'], expected_idea_id=expected_idea_id)
        if record is not None:
            require(observed == validate_record(record), 'Asset record differs from bytes', 'corrupt_store')
        record = observed
    if record is not None:
        checked = validate_record(record)
        require(link['record_id'] == record_id(checked) and link['path'] == evidence_path(checked),
                'Asset link differs from record identity', 'corrupt_store')
        if expected_idea_id is not None:
            require(checked['idea_id'] == expected_idea_id, 'Wrong asset idea', 'corrupt_store')
    return copy.deepcopy(link)


def record_link(record, raw=None):
    checked = validate_record(record)
    raw = encode_record(checked) if raw is None else raw
    require(type(raw) is bytes, 'Asset evidence must be bytes')
    link = dict(record_id=record_id(checked), path=evidence_path(checked),
                sha256=hashlib.sha256(raw).hexdigest())
    return validate_link(link, record=checked, raw=raw)


def decode_record(raw, *, path=None, expected_idea_id=None, link=None):
    from idea_markdown import parse_document
    require(type(raw) is bytes, 'Asset evidence must be bytes')
    require(len(raw) <= MAX_INPUT, 'Asset Markdown exceeds 1 MiB', 'too_large')
    document = parse_document(raw)
    checked = validate_record(document.metadata)
    require(not document.has_comments and document.body == _body(checked),
            'Immutable asset body/comments differ', 'corrupt_store')
    require(raw == encode_record(checked), 'Immutable asset bytes are not canonical', 'corrupt_store')
    if path is not None:
        require(type(path) is str and path == evidence_path(checked), 'Wrong asset evidence path', 'corrupt_store')
    if expected_idea_id is not None:
        _id(expected_idea_id, 'idea')
        require(checked['idea_id'] == expected_idea_id, 'Wrong asset idea', 'corrupt_store')
    if link is not None:
        validate_link(link, record=checked, expected_idea_id=expected_idea_id)
        require(link['sha256'] == hashlib.sha256(raw).hexdigest(), 'Asset evidence bytes hash differs', 'corrupt_store')
    return checked
