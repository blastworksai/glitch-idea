"""Pure immutable planning packets and source eligibility.

Paths are typed observations supplied by Store/Service, never filesystem proof.
Publication, receipt witnesses, host path checks and blob reads belong there.
"""
import copy
import hashlib
import json
import math
from pathlib import PurePosixPath, PureWindowsPath
import re

from idea_domain import MAX_INPUT, MAX_STATE, assessment, check_id, integer, require, text
from idea_workflow import (DEPENDENCIES, STEP_ORDER, derive_state, empty_workflow,
                           source_digest, validate_workflow)
from idea_assessment import validate_actual_position
from idea_asset_evidence import MAX_FILE, MAX_SET, MAX_MEMBERS, MIME_EXTENSIONS

STEPS = STEP_ORDER[:-1]
RECORD_KEYS = frozenset(('schema_version', 'kind', 'handoff_id', 'idea_id',
    'source_revision', 'session_id', 'request_id', 'actor', 'timestamp',
    'source_digest', 'source_files', 'origin', 'accepted', 'placement', 'design_set'))
LINK_KEYS = frozenset(('handoff_id', 'path', 'sha256'))
_HASH = re.compile(r'[0-9a-f]{64}')
_PATH = re.compile(r'history/(idea_[0-9a-f]{32})/metadata/([0-9a-f]{64})\.md')


def _exact(value, keys, name):
    require(type(value) is dict and all(type(k) is str for k in value)
            and set(value) == set(keys), 'Unexpected '+name+' fields')


def _canonical(value):
    pending, count = [(value, 1)], 0
    while pending:
        item, depth = pending.pop(); count += 1
        require(count <= 100000 and depth <= 32, 'Handoff tree exceeds limits', 'too_large')
        require(type(item) in (dict, list, str, int, float, bool, type(None)), 'Unsupported handoff value')
        if type(item) is dict:
            require(all(type(k) is str for k in item), 'Handoff keys must be strings')
            pending.extend((child, depth+1) for pair in item.items() for child in pair)
        elif type(item) is list:
            pending.extend((child, depth+1) for child in item)
        elif type(item) is str:
            require(len(item) <= MAX_STATE, 'Handoff text exceeds limit', 'too_large')
            require(not any(0xD800 <= ord(c) <= 0xDFFF for c in item), 'Invalid handoff Unicode')
        elif type(item) is float:
            require(math.isfinite(item), 'Nonfinite handoff number')
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'), allow_nan=False).encode('utf-8')
    require(len(raw) <= MAX_STATE, 'Handoff record exceeds byte limit', 'too_large')
    return raw


def _id(value, prefix):
    require(type(value) is str and re.fullmatch(prefix+r'_[0-9a-f]{32}', value),
            'Invalid '+prefix+' identifier')


def _hash(value):
    require(type(value) is str and _HASH.fullmatch(value), 'Invalid handoff hash')


def _absolute(value):
    text(value, 'service-host path', 65536)
    require('\x00' not in value, 'Invalid service-host path')
    path = PureWindowsPath(value) if PureWindowsPath(value).is_absolute() else PurePosixPath(value)
    require(path.is_absolute() and '..' not in path.parts, 'Path must be absolute and confined')
    return path


def _origin(value):
    _exact(value, ('text', 'sha256', 'actor', 'timestamp'), 'origin')
    text(value['text'], 'original wording', MAX_INPUT)
    text(value['actor'], 'origin actor', 200)
    text(value['timestamp'], 'origin timestamp', 200)
    _hash(value['sha256'])
    require(hashlib.sha256(value['text'].encode('utf-8')).hexdigest() == value['sha256'],
            'Origin hash differs', 'corrupt_store')


def _accepted(value, revision):
    _exact(value, STEPS, 'accepted handoff steps')
    workflow = empty_workflow()
    for step in STEPS:
        _exact(value[step], ('fields', 'acceptance'), 'accepted '+step)
        workflow['steps'][step] = dict(copy.deepcopy(value[step]), invalidated_by=[])
    validate_workflow(workflow)
    for step in STEPS:
        receipt = value[step]['acceptance']
        require(receipt is not None and receipt['accepted_revision'] <= revision,
                'Missing or future accepted prerequisite', 'not_ready')
        dependencies = receipt['dependencies']
        require(set(DEPENDENCIES[step]) <= set(dependencies)
                and all(STEP_ORDER.index(name) < STEP_ORDER.index(step) for name in dependencies),
                'Missing or forward acceptance dependency', 'not_ready')
        sources = {step:value[step]['fields']}
        for name, witness in dependencies.items():
            earlier = value[name]
            expected = dict(revision=earlier['acceptance']['accepted_revision'],
                digest=source_digest(name, earlier['acceptance']['accepted_revision'], {name:earlier['fields']}))
            require(witness == expected, 'Acceptance dependency changed', 'not_ready')
            sources[name] = earlier['fields']
        require(receipt['source_digest'] == source_digest(step, receipt['source_revision'], sources),
                'Acceptance source digest differs', 'not_ready')
    workspace = value['capture']['fields']['workspace']
    _absolute(workspace['path'])
    return copy.deepcopy(value)


def _placement(value, idea_id):
    _exact(value, ('actual_position', 'neighbors'), 'handoff placement')
    integer(value['actual_position'], 'actual position', 1)
    _exact(value['neighbors'], ('before', 'after'), 'placement neighbors')
    neighbors = [v for v in value['neighbors'].values() if v is not None]
    for neighbor in neighbors:
        check_id(neighbor)
        require(neighbor != idea_id, 'Idea cannot neighbor itself')
    require(len(set(neighbors)) == len(neighbors), 'Duplicate placement neighbors')


def _design(value, accepted):
    visual = accepted['visualize']['fields']
    if visual['disposition'] != 'accepted_set':
        require(value is None, 'Skipped visual decision cannot include a design set', 'not_ready')
        return
    _exact(value, ('set_id', 'members'), 'handoff design set')
    _id(value['set_id'], 'set')
    require(value['set_id'] == visual['design_set_id'], 'Selected design set differs', 'stale_source')
    members = value['members']
    require(type(members) is list and 1 <= len(members) <= MAX_MEMBERS,
            'Design set requires bounded explicit members', 'too_large')
    ids, paths, total = set(), set(), 0
    for member in members:
        _exact(member, ('asset_id', 'name', 'type', 'size', 'sha256', 'path'), 'design member')
        _id(member['asset_id'], 'asset')
        require(member['asset_id'] not in ids, 'Duplicate design member')
        ids.add(member['asset_id'])
        text(member['name'], 'design name', 4096)
        mime = member['type']
        require(type(mime) is str and mime in MIME_EXTENSIONS, 'Unsupported design type')
        require('.' in member['name'] and member['name'].rsplit('.', 1)[1].lower() in MIME_EXTENSIONS[mime],
                'Design extension and type disagree')
        integer(member['size'], 'design size', 1, MAX_FILE)
        _hash(member['sha256'])
        path = _absolute(member['path'])
        require(path.name == member['asset_id']+'.bin' and path.parent.name == 'blobs'
                and path.parent.parent.name == 'assets', 'Design path differs from generated blob')
        require(member['path'] not in paths, 'Duplicate design path')
        paths.add(member['path']); total += member['size']
    require(total <= MAX_SET, 'Design set exceeds byte limit', 'too_large')


def _source(idea_id, revision, origin, accepted, placement, design_set):
    return dict(idea_id=idea_id, source_revision=revision, origin=copy.deepcopy(origin),
                accepted=copy.deepcopy(accepted), placement=copy.deepcopy(placement),
                design_set=copy.deepcopy(design_set))


def _digest(source):
    return hashlib.sha256(_canonical(source)).hexdigest()


def eligible_source(state, idea, design_set=None):
    """Return detached consumed sources; no files, publication or inferred receipts."""
    require(type(idea) is dict and 'workflow' in idea, 'Browser workflow required', 'not_ready')
    require(idea.get('status') == 'active', 'Shape the next slice before handoff', 'archived_revision')
    require(type(state) is dict and type(state.get('ideas')) is dict
            and state['ideas'].get(idea.get('idea_id')) == idea, 'Handoff idea differs from state', 'not_ready')
    projection = derive_state(idea)
    for step in STEPS:
        allowed = ('saved', 'skipped', 'not-applicable') if step == 'visualize' else ('saved',)
        require(projection['steps'][step]['status'] in allowed, 'Current acceptance required for '+step, 'not_ready')
    revision, key = idea['revision'], idea['idea_id']
    accepted = {step:{name:copy.deepcopy(idea['workflow']['steps'][step][name])
                     for name in ('fields', 'acceptance')} for step in STEPS}
    _accepted(accepted, revision); _origin(idea['origin'])
    ratings = idea.get('ratings')
    priorities = accepted['priorities']
    require(type(ratings) is dict, 'Human ratings are missing', 'not_ready')
    for name in ('urgency', 'importance'):
        integer(ratings.get(name), 'human '+name, 1, 10)
    require(type(ratings) is dict and all(ratings.get(k) == v for k,v in priorities['fields'].items())
            and ratings.get('actor') == priorities['acceptance']['actor']
            and ratings.get('timestamp') == priorities['acceptance']['timestamp'],
            'Human ratings differ from accepted priorities', 'not_ready')
    assess = accepted['assess']
    expected_assessment = dict(assessment(assess['fields']['assessment']),
        actor=assess['acceptance']['actor'], timestamp=assess['acceptance']['timestamp'])
    require(type(idea.get('assessments')) is list and bool(idea['assessments'])
            and _canonical(idea['assessments'][-1]) == _canonical(expected_assessment),
            'Domain assessment differs from accepted Assess', 'not_ready')
    position = validate_actual_position(assess['fields']['position'], state.get('order'), key)
    require(state['order'].index(key)+1 == position['actual_position'],
            'Accepted actual position differs from backlog', 'stale_backlog')
    placements = state.get('placements')
    require(type(placements) is list and all(type(p) is dict for p in placements),
            'Invalid placement evidence inventory', 'not_ready')
    witnesses = [p for p in placements if p.get('idea_id') == key
                 and p.get('idea_revision') == assess['acceptance']['accepted_revision']]
    require(bool(witnesses), 'Accepted Assess placement evidence is missing', 'not_ready')
    witness = witnesses[-1]
    _exact(witness, ('idea_id', 'idea_revision', 'position', 'reason', 'actor', 'timestamp',
                    'source_backlog_revision', 'neighbors', 'snapshot', 'accepted_backlog_revision'),
           'accepted placement witness')
    integer(witness['idea_revision'], 'placement idea revision', 1)
    integer(witness['position'], 'placement position', 1)
    text(witness['reason'], 'placement reason')
    require(witness.get('position') == position['actual_position']
            and witness.get('neighbors') == position['neighbors']
            and witness.get('actor') == assess['acceptance']['actor']
            and witness.get('timestamp') == assess['acceptance']['timestamp']
            and witness.get('snapshot') == dict(ratings=ratings, assessments=idea['assessments']),
            'Accepted placement witness differs', 'not_ready')
    integer(witness.get('source_backlog_revision'), 'placement source backlog')
    integer(witness.get('accepted_backlog_revision'), 'placement accepted backlog', 1)
    integer(state.get('backlog_revision'), 'backlog revision')
    require(witness['source_backlog_revision']+1 == witness['accepted_backlog_revision']
            <= state['backlog_revision'], 'Placement backlog witness differs', 'not_ready')
    placement = {name:copy.deepcopy(position[name]) for name in ('actual_position', 'neighbors')}
    _placement(placement, key); _design(design_set, accepted)
    source = _source(key, revision, idea['origin'], accepted, placement, design_set)
    return dict(source, source_digest=_digest(source))


def build_record(state, idea, *, source_files, design_set, handoff_id, session_id,
                 request_id, actor, timestamp):
    source = eligible_source(state, idea, design_set)
    return validate_record(dict(schema_version=1, kind='handoff', handoff_id=handoff_id,
        session_id=session_id, request_id=request_id, actor=actor, timestamp=timestamp,
        source_files=copy.deepcopy(source_files), **source))


def validate_record(record):
    _canonical(record)
    _exact(record, RECORD_KEYS, 'handoff record')
    require(type(record['schema_version']) is int and record['schema_version'] == 1
            and type(record['kind']) is str and record['kind'] == 'handoff', 'Unsupported handoff schema')
    for name, prefix in (('handoff_id', 'handoff'), ('session_id', 'session'), ('idea_id', 'idea')):
        _id(record[name], prefix)
    require(type(record['request_id']) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', record['request_id']),
            'Invalid publishing request ID')
    text(record['actor'], 'handoff actor', 200); text(record['timestamp'], 'handoff timestamp', 100)
    integer(record['source_revision'], 'source revision', 1)
    _hash(record['source_digest']); _origin(record['origin'])
    _accepted(record['accepted'], record['source_revision'])
    _placement(record['placement'], record['idea_id']); _design(record['design_set'], record['accepted'])
    accepted_position = record['accepted']['assess']['fields']['position']
    require(record['placement'] == {name:accepted_position[name] for name in ('actual_position', 'neighbors')},
            'Packet placement differs from accepted Assess', 'stale_source')
    files = record['source_files']
    _exact(files, ('detail', 'index', 'revision'), 'source files')
    for entry in files.values():
        _exact(entry, ('path', 'sha256'), 'source file')
        _absolute(entry['path']); _hash(entry['sha256'])
    detail = _absolute(files['detail']['path']); index = _absolute(files['index']['path'])
    revision = _absolute(files['revision']['path'])
    require(detail.name == record['idea_id']+'.md' and index == detail.parent/'IDEAS.md'
            and revision == detail.parent/'history'/record['idea_id']/('r'+str(record['source_revision'])+'.md'),
            'Source paths differ from generated Store objects')
    if record['design_set'] is not None:
        for member in record['design_set']['members']:
            require(_absolute(member['path']) == detail.parent/'assets'/'blobs'/(member['asset_id']+'.bin'),
                    'Design blob belongs to another Store')
    source = _source(record['idea_id'], record['source_revision'], record['origin'],
                     record['accepted'], record['placement'], record['design_set'])
    require(record['source_digest'] == _digest(source), 'Handoff consumed source differs', 'stale_source')
    return copy.deepcopy(record)


def _escape(character):
    point = ord(character)
    return 0x7f <= point <= 0x9f or point in (0x2028, 0x2029, 0xfffe, 0xffff)


def _literal(value, indent=None):
    literal = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=indent,
                        separators=(',', ':') if indent is None else None, allow_nan=False)
    return ''.join('\\u'+format(ord(c), '04x') if _escape(c) else c for c in literal)


def _body(record):
    literal = ''.join('    '+line+'\n' for line in _literal(record, 2).split('\n'))
    return '# Immutable planning handoff\n\nDo not edit this evidence.\n\n## Accepted planning source\n\n'+literal


def encode_record(record):
    from idea_markdown import encode_document
    checked = validate_record(record)
    if any(_escape(c) for c in _canonical(checked).decode('utf-8')):
        raw = ('---\n'+_literal(checked)+'\n---\n'+_body(checked)).encode('utf-8')
    else:
        raw = encode_document(checked, _body(checked))
    require(len(raw) <= MAX_STATE, 'Handoff Markdown exceeds byte limit', 'too_large')
    return raw


def _link(record, raw):
    sha = hashlib.sha256(raw).hexdigest()
    return dict(handoff_id=record['handoff_id'],
                path='history/'+record['idea_id']+'/metadata/'+sha+'.md', sha256=sha)


def _validate_link(link, record, raw):
    _exact(link, LINK_KEYS, 'handoff link')
    _id(link['handoff_id'], 'handoff'); _hash(link['sha256'])
    require(type(link['path']) is str and _PATH.fullmatch(link['path'])
            and link == _link(record, raw), 'Handoff link differs from immutable bytes', 'corrupt_store')


def decode_record(raw, *, path=None, expected_idea_id=None, link=None):
    from idea_markdown import parse_document
    require(type(raw) is bytes and len(raw) <= MAX_STATE, 'Invalid handoff bytes', 'too_large')
    document = parse_document(raw)
    checked = validate_record(document.metadata)
    require(not document.has_comments and document.body == _body(checked)
            and raw == encode_record(checked), 'Handoff bytes/body are not canonical', 'corrupt_store')
    expected_link = _link(checked, raw)
    if path is not None:
        require(type(path) is str and path == expected_link['path'], 'Wrong handoff path', 'corrupt_store')
    if expected_idea_id is not None:
        check_id(expected_idea_id)
        require(checked['idea_id'] == expected_idea_id, 'Wrong handoff idea', 'corrupt_store')
    if link is not None:
        _validate_link(link, checked, raw)
    return checked


def record_link(record, raw):
    checked = validate_record(record)
    require(type(raw) is bytes and raw == encode_record(checked), 'Handoff bytes differ from record', 'corrupt_store')
    return _link(checked, raw)


def render_prompt(record, packet_path):
    checked = validate_record(record)
    packet = _absolute(packet_path)
    detail = _absolute(checked['source_files']['detail']['path'])
    require(packet == detail.parent/_link(checked, encode_record(checked))['path'],
            'Prompt packet path differs from immutable bytes', 'corrupt_store')
    accepted = checked['accepted']
    planning = dict(workspace=accepted['capture']['fields']['workspace'],
        live_detail=checked['source_files']['detail']['path'], packet_path=packet_path,
        revision_evidence=checked['source_files']['revision'],
        shape=accepted['shape']['fields'], method=accepted['method']['fields'],
        design_set=checked['design_set'])
    return ('/glitch-plan\n\nPaste this prompt into a NEW window or pane. '
            'Use the confirmed workspace and immutable planning source below. '
            'Retain this exact Idea trace in the plan.\n\n## Idea trace\n'
            'idea_id: '+checked['idea_id']+'\nidea_revision: '+str(checked['source_revision'])+
            '\n\n## Planning source\n'+''.join('    '+line+'\n' for line in _literal(planning, 2).split('\n')))


def recorded_packet_path(record, link):
    """Original recorded location, even when verified relative bytes moved."""
    checked = validate_record(record)
    _validate_link(link, checked, encode_record(checked))
    return str(_absolute(checked['source_files']['detail']['path']).parent/link['path'])


def verify_current_paths(record, source_files, design_set=None):
    """Compare trusted current path observations separately from historic hashes.

    Callers supply generated paths only AFTER Store host/symlink checks. This
    pure comparison never resolves a path or compares prepublication hashes.
    """
    checked = validate_record(record)
    _exact(source_files, ('detail', 'index', 'revision'), 'current source files')
    for name, entry in source_files.items():
        _exact(entry, ('path', 'sha256'), 'current source file')
        _hash(entry['sha256'])
        require(_absolute(entry['path']) == _absolute(checked['source_files'][name]['path']),
                'Handoff recorded source location changed', 'stale_source')
    recorded_design = checked['design_set']
    require((recorded_design is None) == (design_set is None), 'Handoff design location changed', 'stale_source')
    if recorded_design is not None:
        _design(design_set, checked['accepted'])
        require([_absolute(member['path']) for member in recorded_design['members']] ==
                [_absolute(member['path']) for member in design_set['members']],
                'Handoff recorded asset location changed', 'stale_source')
    return copy.deepcopy(checked)


def verify_current(record, state, idea, design_set=None, *, source_files=None):
    checked = validate_record(record)
    require(checked['idea_id'] == idea.get('idea_id'), 'Handoff belongs to another idea', 'stale_source')
    require(checked['source_revision'] == idea.get('revision'), 'Handoff revision changed', 'stale_revision')
    if source_files is not None:
        verify_current_paths(checked, source_files, design_set)
    current = eligible_source(state, idea, design_set)
    require(checked['source_digest'] == current['source_digest'], 'Handoff source changed', 'stale_source')
    return copy.deepcopy(checked)
