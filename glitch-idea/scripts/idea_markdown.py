"""Bounded Markdown authority codec. PyYAML is MIT.

Pure bytes in/bytes out: filesystem locking, CAS, import and publication belong
in Store. Parsing never writes. A Document carries comment/body conflict guards.
"""
from dataclasses import dataclass
import copy
import json
import math
import re
from urllib.parse import quote
from pathlib import PurePosixPath, PureWindowsPath

from idea_domain import (IdeaError, MAX_STATE, MAX_INPUT, ASSESS_KEYS, assessment,
                         check_id, digest, integer, require, shape, text, receipt, METHOD_LABELS)
try:
    import yaml
except ImportError as exc:
    raise IdeaError('missing_dependency', 'PyYAML 6.0.3 is required; configure the documented private runtime') from exc

require(yaml.__version__ == '6.0.3', 'PyYAML 6.0.3 is required in the configured private runtime', 'missing_dependency')

MAX_DOCUMENT = MAX_STATE
MAX_FRONTMATTER = 8 * MAX_INPUT
MAX_DEPTH = 32
MAX_NODES = 100000
NOTES_START = '<!-- glitch-idea:notes:start -->\n'
NOTES_END = '<!-- glitch-idea:notes:end -->\n'
DETAIL_KEYS = {'idea_id','revision','status','origin','shape','ratings','assessments',
               'proposals','plans','executions'}
OPTIONAL_IDEA_KEYS = {'workflow'}
METADATA_TYPES = {'proposals': 'proposal', 'plans': 'plan', 'executions': 'execution'}
AGENT_PROPOSAL_EXTENSION = 'glitch_idea_agent_proposals'
MAX_AGENT_PROPOSALS = 128
MAX_PROPOSAL_EVIDENCE_FILES = 4096
MAX_PROPOSAL_EVIDENCE_BYTES = 128 * MAX_INPUT
ASSET_EXTENSION = 'glitch_idea_assets'
MAX_ASSET_LINKS = 256
MAX_ASSET_EVIDENCE_FILES = 4096
MAX_ASSET_EVIDENCE_BYTES = 128 * MAX_INPUT
HANDOFF_EXTENSION = 'glitch_idea_handoffs'
MAX_HANDOFF_LINKS = 128
MAX_HANDOFF_EVIDENCE_FILES = 4096
MAX_HANDOFF_EVIDENCE_BYTES = 256 * MAX_INPUT
MOVE_EXTENSION = 'glitch_idea_moves'
DELIVERED_EXTENSION = 'glitch_idea_delivered'
MAX_POINTER_LINKS = 4096
MOVED_KEYS = {'schema_version','kind','idea_id','idea_revision','plan_id','workspace','home_path',
              'moved_sha256','actor','timestamp','frozen'}
DELIVERED_KEYS = {'schema_version','kind','idea_id','ref','actor','timestamp'}
SNAPSHOT_KEYS = {'revision','shape','ratings','assessments','actor','action','timestamp'}
DETAIL_AUTHORITY = ('Frontmatter owns current fields. Origin and linked history are immutable.\n'
                    'Edit supported fields and Notes while paused; resume to validate changes.\n'
                    'This generated summary is a view, not an independent authority.\n')
INDEX_AUTHORITY = ('Frontmatter owns accepted order and backlog revision.\n'
                   'Scores and ranks below are derived from detail files; edit details, not this table.\n')


class BoundedLoader(yaml.SafeLoader):
    # Copy rather than mutate SafeLoader's process-global resolver table.
    yaml_implicit_resolvers = {
        key: [(tag, regexp) for tag, regexp in rules
              if tag != 'tag:yaml.org,2002:timestamp']
        for key, rules in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }

    def __init__(self, stream):
        super().__init__(stream)
        self.node_count = 0
        self.node_depth = 0

    def compose_node(self, parent, index):
        require(not self.check_event(yaml.AliasEvent), 'YAML aliases are not supported')
        event = self.peek_event()
        require(event.anchor is None, 'YAML anchors are not supported')
        self.node_count += 1
        self.node_depth += 1
        require(self.node_count <= MAX_NODES, 'YAML node count exceeds limit', 'too_large')
        require(self.node_depth <= MAX_DEPTH, 'YAML depth exceeds limit', 'too_large')
        try:
            return super().compose_node(parent, index)
        finally:
            self.node_depth -= 1

    def construct_mapping(self, node, deep=False):
        require(isinstance(node, yaml.MappingNode), 'YAML must be a mapping')
        result = {}
        for key_node, value_node in node.value:
            require(key_node.tag != 'tag:yaml.org,2002:merge', 'YAML merge keys are not supported')
            key = self.construct_object(key_node, deep=deep)
            require(type(key) is str, 'YAML mapping keys must be strings')
            require(key not in result, 'Duplicate YAML key: ' + key)
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _tree(value):
    """Bound JSON-compatible trees before emitting or accepting constructors."""
    pending = [(value, 1)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        require(count <= MAX_NODES and depth <= MAX_DEPTH, 'Document tree exceeds depth/count limit', 'too_large')
        require(type(item) in (dict, list, str, int, float, bool, type(None)), 'Unsupported YAML value type')
        if type(item) is dict:
            for key, child in item.items():
                require(type(key) is str, 'Mapping keys must be strings')
                pending.extend(((key, depth + 1), (child, depth + 1)))
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is float:
            require(math.isfinite(item), 'Nonfinite YAML number is not supported')
        elif type(item) is str:
            require(not any(0xD800 <= ord(c) <= 0xDFFF for c in item), 'Invalid Unicode scalar')


@dataclass(frozen=True)
class Document:
    metadata: dict
    body: str
    has_comments: bool = False

    def require_rewritable(self):
        require(not self.has_comments,
                'YAML comments would be lost; move them into Notes before rewriting',
                'yaml_comments')


def _yaml_comments(source):
    """PyYAML tokens include scalar spans; comment text occurs between tokens.

    A block scalar header comment is inside ScalarToken's span but outside its
    scalar content, so inspect that header separately. Quoted and block content
    containing # remains untouched.
    """
    end = 0
    comments = False
    count = 0
    for token in yaml.scan(source, Loader=BoundedLoader):
        count += 1
        require(count <= MAX_NODES * 4, 'YAML token count exceeds limit', 'too_large')
        require(not isinstance(token, (yaml.tokens.DocumentStartToken, yaml.tokens.DocumentEndToken)), 'Additional YAML document markers are not supported')
        comments |= '#' in source[end:token.start_mark.index]
        if isinstance(token, yaml.ScalarToken) and token.style in ('|', '>'):
            header = source[token.start_mark.index:token.end_mark.index].split('\n', 1)[0]
            comments |= '#' in header
        end = max(end, token.end_mark.index)
    return comments or '#' in source[end:]


def parse_document(raw):
    require(type(raw) is bytes, 'Markdown input must be bytes')
    require(len(raw) <= MAX_DOCUMENT, 'Markdown document exceeds size limit', 'too_large')
    try:
        source = raw.decode('utf-8')
        require(source.startswith('---\n') or source.startswith('---\r\n'), 'Markdown must start with YAML frontmatter at byte zero')
        opening = source.index('\n') + 1
        close = re.search(r'^---\r?\n|^---\r?$', source[opening:], re.M)
        require(close is not None, 'Missing closing frontmatter delimiter')
        front = source[opening:opening + close.start()]
        require(len(front.encode('utf-8')) <= MAX_FRONTMATTER, 'Frontmatter exceeds size limit', 'too_large')
        comments = _yaml_comments(front)
        value = yaml.load(front, Loader=BoundedLoader)
        require(type(value) is dict, 'Frontmatter must contain one YAML mapping')
        _tree(value)
        return Document(value, source[opening + close.end():], comments)
    except (yaml.YAMLError, UnicodeError, RecursionError, ValueError) as exc:
        raise IdeaError('invalid_markdown', 'Invalid UTF-8 YAML frontmatter: ' + str(exc)) from exc


class DeterministicDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def _represent_string(dumper, value):
    # Quoting every multiline string preserves CRLF and exact trailing newlines;
    # YAML literal scalars normalize line breaks and cannot own immutable origin.
    style = '"' if any(c in value for c in '\n\r') else None
    return dumper.represent_scalar('tag:yaml.org,2002:str', value, style=style)


DeterministicDumper.add_representer(str, _represent_string)


def encode_document(metadata, body='', *, previous=None):
    if previous is not None:
        previous.require_rewritable()
    require(type(metadata) is dict and type(body) is str, 'Document requires mapping and text body')
    _tree(metadata)
    _tree(body)
    front = yaml.dump(metadata, Dumper=DeterministicDumper, allow_unicode=True,
                      sort_keys=True, default_flow_style=False, width=100)
    raw = ('---\n' + front + '---\n' + body).encode('utf-8')
    require(len(raw) <= MAX_DOCUMENT and len(front.encode('utf-8')) <= MAX_FRONTMATTER,
            'Encoded document exceeds size limit', 'too_large')
    return raw


def _exact(value, keys, name):
    require(type(value) is dict and set(value) == keys, 'Unexpected ' + name + ' schema')


def _extensions(value):
    require(type(value) is dict, 'extensions must be a mapping')
    _tree(value)


def agent_proposal_links(extensions, idea_id):
    """Detached immutable suggestion references, separate from domain metadata.

    Store owns protection against external edits and durable append-only history.
    This codec checks only supplied link shape/identity and previous prefixes.
    """
    _extensions(extensions)
    check_id(idea_id)
    links = extensions.get(AGENT_PROPOSAL_EXTENSION, [])
    require(type(links) is list and len(links) <= MAX_AGENT_PROPOSALS,
            'Agent proposal links must be a list of at most 128 entries', 'too_large')
    # Local import: the standalone evidence codec uses this module for YAML.
    from idea_proposal_evidence import validate_link
    checked = [validate_link(link, expected_idea_id=idea_id) for link in links]
    require(len({link['proposal_id'] for link in checked}) == len(checked)
            and len({link['path'] for link in checked}) == len(checked),
            'Duplicate agent proposal ID or path', 'corrupt_store')
    return checked


def asset_links(extensions, idea_id):
    """Return detached bounded immutable asset links; bytes establish ownership.

    Content-addressed paths do not embed an idea ID. encode/decode_state check
    the linked record's idea association; Store must also inspect disk inventory.
    """
    _extensions(extensions)
    check_id(idea_id)
    links = extensions.get(ASSET_EXTENSION, [])
    require(type(links) is list and len(links) <= MAX_ASSET_LINKS,
            'Asset links must be a list of at most 256 entries', 'too_large')
    from idea_asset_evidence import validate_link
    checked = [validate_link(link, expected_idea_id=idea_id) for link in links]
    require(len({link['record_id'] for link in checked}) == len(checked)
            and len({link['path'] for link in checked}) == len(checked),
            'Duplicate asset record ID or path', 'corrupt_store')
    return checked


def handoff_links(extensions, idea_id):
    """Detached confined packet references; Store verifies publication receipts.

    Unlike legacy/proposal metadata, the filename hashes complete packet bytes.
    Prefix preservation protects cooperating writes; Store inventory detects
    omitted links on external edit. No handoff record enters the domain schema.
    """
    _extensions(extensions)
    check_id(idea_id)
    links = extensions.get(HANDOFF_EXTENSION, [])
    require(type(links) is list, 'Handoff links must be a list')
    require(len(links) <= MAX_HANDOFF_LINKS,
            'Handoff links exceed the 128-entry capacity', 'handoff_capacity')
    checked = []
    for link in links:
        _exact(link, {'handoff_id', 'path', 'sha256'}, 'handoff link')
        check_id(link['handoff_id'], 'handoff')
        _digest(link['sha256'])
        require(type(link['path']) is str and link['path'] ==
                'history/'+idea_id+'/metadata/'+link['sha256']+'.md',
                'Handoff path hash or idea differs', 'corrupt_store')
        checked.append(copy.deepcopy(link))
    require(len({link['handoff_id'] for link in checked}) == len(checked)
            and len({link['path'] for link in checked}) == len(checked),
            'Duplicate handoff ID or path', 'corrupt_store')
    return checked


def _current(value):
    if value['shape'] is not None:
        shape(value['shape'])
    if value['ratings'] is not None:
        require(type(value['ratings']) is dict and {'urgency','importance','actor'} <= set(value['ratings']) <= {'urgency','importance','actor','timestamp'}, 'Unexpected ratings schema')
        integer(value['ratings']['urgency'], 'urgency', 1, 10)
        integer(value['ratings']['importance'], 'importance', 1, 10)
        text(value['ratings']['actor'], 'rating actor', 200)
        if 'timestamp' in value['ratings']:
            text(value['ratings']['timestamp'], 'rating timestamp', 200)
    require(type(value['assessments']) is list, 'assessments must be a list')
    for entry in value['assessments']:
        require(type(entry) is dict and ASSESS_KEYS | {'score'} <= set(entry) <= ASSESS_KEYS | {'score','assessment_id','actor','timestamp'}, 'Unexpected assessment schema')
        computed = assessment({k: entry[k] for k in ASSESS_KEYS})
        require(type(entry['score']) in (int, float, type(None)) and computed['score'] == entry['score'], 'Assessment score mismatch')
        if 'assessment_id' in entry:
            check_id(entry['assessment_id'], 'assessment')
        for key in ('actor','timestamp'):
            if key in entry:
                text(entry[key], 'assessment ' + key, 200)


def _snapshot(value):
    require(type(value) is dict, 'Snapshot must be a mapping')
    optional = {'schema_version','workflow'} if 'workflow' in value else set()
    _exact(value, SNAPSHOT_KEYS | optional, 'snapshot')
    integer(value['revision'], 'snapshot revision', 1)
    for key in ('actor','action','timestamp'):
        text(value[key], 'snapshot ' + key, 200)
    _current(value)
    if optional:
        from idea_workflow import _refuse_old_version
        _refuse_old_version(value['schema_version'], 'Unsupported snapshot schema')
        _workflow(value['workflow'])


def _workflow(value):
    # Optional module import is deferred until a workflow is actually present.
    from idea_workflow import validate_workflow
    validate_workflow(value)


def _origin(origin):
    _exact(origin, {'text','sha256','actor','timestamp'}, 'origin')
    text(origin['text'], 'origin', MAX_INPUT)
    text(origin['actor'], 'origin actor', 200)
    text(origin['timestamp'], 'origin timestamp', 200)
    require(digest(origin['text'].encode('utf-8')) == origin['sha256'], 'Origin hash mismatch')


def _histories(idea):
    for plan in idea['plans']:
        _exact(plan, {'plan_id','idea_id','idea_revision','path','source_path','sha256','actor','timestamp','validation'}, 'plan link')
        check_id(plan['plan_id'], 'plan')
        require(plan['idea_id'] == idea['idea_id'], 'Plan idea mismatch')
        integer(plan['idea_revision'], 'plan revision', 1, idea['revision'])
        _digest(plan['sha256'])
        for key in ('path','source_path','actor','timestamp'):
            text(plan[key], 'plan ' + key)
        require(all(PurePosixPath(plan[key]).is_absolute() or PureWindowsPath(plan[key]).is_absolute() for key in ('path','source_path')), 'Plan paths must be absolute')
        require(type(plan['validation']) is dict, 'Plan validation must be a mapping')
    for proposal in idea['proposals']:
        _placement(proposal, idea['idea_id'], proposed=True)
    for execution in idea['executions']:
        _exact(execution, {'idea_id','plan_id','attempt_id','status','receipt','path','sha256','actor','timestamp'}, 'execution')
        require(execution['idea_id'] == idea['idea_id'], 'Execution idea mismatch')
        check_id(execution['plan_id'], 'plan')
        receipt(execution['receipt'], idea['idea_id'], execution['plan_id'])
        require(execution['attempt_id'] == execution['receipt']['attempt_id'] and execution['status'] == execution['receipt']['status'], 'Execution receipt mismatch')
        _digest(execution['sha256'])
        for key in ('path','actor','timestamp'):
            text(execution[key], 'execution ' + key)


def _digest(value):
    require(type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value), 'Invalid SHA-256')


def _placement(value, idea_id=None, *, proposed=False):
    keys = {'idea_id','idea_revision','position','reason','actor','timestamp','source_backlog_revision','neighbors','snapshot'}
    keys.add('proposal_id' if proposed else 'accepted_backlog_revision')
    _exact(value, keys, 'proposal' if proposed else 'placement')
    check_id(value['idea_id'])
    require(idea_id is None or value['idea_id'] == idea_id, 'Placement idea mismatch')
    for key in ('idea_revision','position'):
        integer(value[key], key, 1)
    integer(value['source_backlog_revision'], 'source backlog revision')
    if proposed:
        check_id(value['proposal_id'], 'proposal')
    else:
        integer(value['accepted_backlog_revision'], 'accepted backlog revision', 1)
    for key in ('reason','actor','timestamp'):
        text(value[key], 'placement ' + key)
    _exact(value['neighbors'], {'before','after'}, 'neighbors')
    for neighbor in value['neighbors'].values():
        if neighbor is not None:
            check_id(neighbor)
    _exact(value['snapshot'], {'ratings','assessments'}, 'placement snapshot')
    _current(dict(value['snapshot'], shape=None))


def _idea(value):
    require(type(value) is dict, 'Idea must be a mapping')
    require(DETAIL_KEYS <= set(value) and set(value) <= DETAIL_KEYS | OPTIONAL_IDEA_KEYS, 'Unexpected idea schema')
    check_id(value['idea_id'])
    integer(value['revision'], 'idea revision', 1)
    require(value['status'] in ('active','archived'), 'Invalid idea status')
    _origin(value['origin'])
    _current(value)
    for key in ('proposals','plans','executions'):
        require(type(value[key]) is list, key + ' must be a list')
    _histories(value)
    if 'workflow' in value:
        _workflow(value['workflow'])


def _link(value, expected=None):
    _exact(value, {'path','sha256'}, 'history link')
    path = value['path']
    require(type(path) is str and re.fullmatch(r'history/(?:idea_[0-9a-f]{32}/r[1-9][0-9]*|backlog/r[1-9][0-9]*)\.md', path), 'Invalid history relative path')
    require(expected is None or path == expected, 'History link path mismatch')
    require(type(value['sha256']) is str and re.fullmatch(r'[0-9a-f]{64}', value['sha256']), 'Invalid history digest')


def _metadata_record(idea_id, record_type, record):
    check_id(idea_id)
    require(type(record_type) is str and record_type in METADATA_TYPES.values(), 'Unsupported metadata record type')
    require(type(record) is dict, 'Metadata record must be a mapping')
    _tree(record)
    key = next(key for key, kind in METADATA_TYPES.items() if kind == record_type)
    # Reuse the existing exact legacy record schemas. Plan content remains in
    # plan-evidence; metadata freezes its link, validation and attribution only.
    container = dict(idea_id=idea_id, revision=record.get('idea_revision', 1),
                     proposals=[], plans=[], executions=[])
    container[key] = [record]
    _histories(container)


def metadata_path(idea_id, record_type, record):
    """Confined content-addressed path for an exact detail metadata record."""
    _metadata_record(idea_id, record_type, record)
    payload = {'idea_id': idea_id, 'record_type': record_type, 'record': record}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'), allow_nan=False).encode('utf-8')
    return 'history/' + idea_id + '/metadata/' + digest(raw) + '.md'


def encode_metadata(idea_id, record_type, record):
    """Encode immutable metadata; a plan record must already omit content."""
    _metadata_record(idea_id, record_type, record)
    meta = dict(schema_version=2, kind='metadata', idea_id=idea_id,
                record_type=record_type, record=copy.deepcopy(record))
    return encode_document(meta, '# Immutable ' + record_type + ' metadata\n\nDo not edit this evidence.\n')


def decode_metadata(raw, *, path=None):
    doc = parse_document(raw)
    meta = doc.metadata
    _exact(meta, {'schema_version', 'kind', 'idea_id', 'record_type', 'record'}, 'metadata frontmatter')
    require(type(meta['schema_version']) is int and meta['schema_version'] == 2 and meta['kind'] == 'metadata', 'Unsupported metadata schema')
    expected = metadata_path(meta['idea_id'], meta['record_type'], meta['record'])
    require(path is None or path == expected, 'Metadata filename digest or identity mismatch', 'corrupt_store')
    return doc


def _metadata_links(idea):
    return {key: [dict(path=metadata_path(idea['idea_id'], kind, record),
                       sha256=digest(encode_metadata(idea['idea_id'], kind, record)))
                  for record in idea[key]] for key, kind in METADATA_TYPES.items()}


def _metadata_link(link, expected):
    _exact(link, {'path', 'sha256'}, 'metadata link')
    require(type(link['path']) is str and link['path'] == expected, 'Metadata link path or record digest mismatch', 'corrupt_store')
    _digest(link['sha256'])


def markdown_text(value):
    # Keep generated Markdown inert and table-safe even for adversarial text.
    value = str(value).replace('\r', ' ').replace('\n', ' ')
    for old, new in (('&','&amp;'),('<','&lt;'),('>','&gt;'),('|','&#124;'),('\\','&#92;'),('[','&#91;'),(']','&#93;'),('`','&#96;'),('*','&#42;'),('_','&#95;')):
        value = value.replace(old, new)
    return value


def markdown_link(label, relative_path):
    require(type(relative_path) is str and relative_path and '\\' not in relative_path and not relative_path.startswith('/')
            and all(p not in ('','.', '..') for p in relative_path.split('/'))
            and ':' not in relative_path, 'Link must be a confined relative path')
    return '[' + markdown_text(label) + '](' + quote(relative_path, safe='/') + ')'


def _field_lines(label, value):
    return ['- ' + label + ': ' + markdown_text(value if value not in (None, '') else 'not given')]


def _method_label(method):
    return METHOD_LABELS.get(method, method)


def _legacy_prior_art(discovery):
    return 'prior_art' not in discovery and 'prior_art_none' not in discovery


def _prior_art_lines(discovery, legacy_line=True):
    """The "Does it already exist?" section; an older accepted Discovery has no such fields.

    Writers use the canonical form (legacy_line=True, "Not checked").  The generated-body check also accepts
    legacy_line=False, the form written before the fields existed, so such an idea still loads (SKILLS-60).
    """
    if _legacy_prior_art(discovery):
        return ['- Does it already exist: Not checked'] if legacy_line else []
    if discovery.get('prior_art_none') is True:
        return ['- Does it already exist: ' + markdown_text('Nothing comparable found — looked: '
                                                             + (discovery.get('prior_art_searched') or 'not given'))]
    lines = []
    for n, item in enumerate(discovery.get('prior_art') or [], 1):
        lines.extend(_field_lines('Comparable ' + str(n), item['name']))
        for label, key in (('Link', 'link'), ('What it does', 'does'), ('How we differ', 'differs'), ('Licence', 'licence')):
            lines.extend(_field_lines(label + ' ' + str(n), item[key]))
    return lines or ['- Does it already exist: Not checked']


def _workflow_sections(workflow, legacy_line=True):
    """Readable Discovery, Exploration and Methods sections from the ACCEPTED fields only."""
    def accepted(step):
        record = None if workflow is None else workflow['steps'].get(step)
        return None if record is None else record['fields']
    if workflow is None:
        return []
    lines = []
    discovery = accepted('discovery')
    lines.extend(['### Discovery', ''])
    if discovery is None:
        lines.append('Not saved yet.')
    else:
        for label, key in (('Problem', 'problem'), ('Who it serves', 'audience'), ('How it is handled today', 'workaround'),
                           ('Evidence it is needed', 'evidence'), ('What would kill it', 'kill_criteria')):
            lines.extend(_field_lines(label, discovery[key]))
        for n, item in enumerate(discovery['challenges'], 1):
            lines.extend(_field_lines('Challenge ' + str(n), item['challenge']) + _field_lines('Response ' + str(n), item['response']))
        lines.extend(_prior_art_lines(discovery, legacy_line))
    exploration = accepted('exploration')
    lines.extend(['', '### Exploration', ''])
    if exploration is None:
        lines.append('Not saved yet.')
    else:
        lines.extend(_field_lines('Desired result', exploration['outcome']))
        for n, item in enumerate(exploration['alternatives'], 1):
            lines.extend(_field_lines('Route ' + str(n), item['route']) + _field_lines('Why not or why', item['reason']))
        lines.extend(_field_lines('Scope', exploration['scope']) + _field_lines('Why this scope', exploration['scope_reason'])
                     + _field_lines('Next slice', exploration['next_slice']))
        lines.extend('- Assumption: ' + markdown_text(item) for item in exploration['assumptions'])
        lines.extend('- Learning: ' + markdown_text(item) for item in exploration['learning'])
        if exploration.get('investment'):
            lines.extend(_field_lines('Budget', ' '.join(str(exploration['investment'][k]) for k in ('cap', 'unit', 'boundary'))))
        if exploration.get('experiment'):
            for label, key in (('Question', 'question'), ('Evidence', 'evidence'), ('Success', 'success_criterion'), ('Stop rule', 'stop_rule')):
                lines.extend(_field_lines('Experiment ' + label.lower(), exploration['experiment'][key]))
        lines.extend(['', 'Sketch:', ''])
        sketch = exploration.get('sketch') or []
        for n, item in enumerate(sketch, 1):
            inner = ' (' + _method_label(item['method']) + ')' if item.get('method') else ''
            why = ' - why next: ' + markdown_text(item['why_next']) if item.get('why_next') else ''
            lines.append(str(n) + '. ' + markdown_text(item['title']) + inner + why
                         + '; done when: ' + markdown_text(item['done_when']))
        if not sketch:
            lines.append('No sketch yet.')
    method = accepted('method')
    lines.extend(['', '### Methods', ''])
    if method is None:
        lines.append('Not saved yet.')
    else:
        lines.extend(_field_lines('Method', _method_label(method['selection'])))
        if method.get('reason'):
            lines.extend(_field_lines('Why this method', method['reason']))
    return lines + ['']


def _has_legacy_discovery(metadata):
    workflow = metadata['idea'].get('workflow')
    record = None if workflow is None else workflow['steps'].get('discovery')
    return record is not None and record['fields'] is not None and _legacy_prior_art(record['fields'])


def _accepted_summaries(metadata):
    """Every rendering of the generated summary that counts as unchanged: canonical first."""
    forms = [_detail_summary(metadata)]
    if _has_legacy_discovery(metadata):
        forms.append(_detail_summary(metadata, legacy_line=False))
    return forms


def _detail_summary(metadata, legacy_line=True):
    idea = metadata['idea']
    lines = ['# ' + idea['idea_id'], '', DETAIL_AUTHORITY.rstrip(), '',
             '## Original wording', '', markdown_text(idea['origin']['text']), '',
             '## Current details', '', 'Status: ' + idea['status'] + '; accepted revision: ' + str(idea['revision']), '']
    lines.extend(_workflow_sections(idea.get('workflow'), legacy_line))
    for key in ('shape','ratings','assessments','workflow'):
        # The retired shape record is shown only if something still holds it, so a stray edit changes the body.
        if key in idea and (key != 'shape' or idea[key] is not None):
            lines.extend(['### ' + key.title(), '', '```yaml',
                          yaml.dump(idea[key], Dumper=DeterministicDumper, allow_unicode=True, sort_keys=True, width=100).rstrip(), '```', ''])
    lines.extend(['## Evidence', ''])
    lines.extend('- ' + markdown_link('Revision ' + str(n), link['path']) for n, link in enumerate(metadata['history'], 1))
    for key, kind in METADATA_TYPES.items():
        lines.extend('- ' + markdown_link(kind.title() + ' ' + str(n), link['path'])
                     for n, link in enumerate(metadata['metadata_evidence'][key], 1))
    lines.extend('- ' + markdown_link('Agent proposal ' + str(n), link['path'])
                 for n, link in enumerate(agent_proposal_links(metadata['extensions'], idea['idea_id']), 1))
    lines.extend('- ' + markdown_link('Asset evidence ' + str(n), link['path'])
                 for n, link in enumerate(asset_links(metadata['extensions'], idea['idea_id']), 1))
    lines.extend('- ' + markdown_link('Planning handoff ' + str(n), link['path'])
                 for n, link in enumerate(handoff_links(metadata['extensions'], idea['idea_id']), 1))
    lines.extend(['', '## Notes', '', NOTES_START.rstrip()])
    return '\n'.join(lines) + '\n'


def _notes(document, baseline=None):
    require(document.body.count(NOTES_START) == 1 and document.body.count(NOTES_END) == 1,
            'Notes markers missing or duplicated', 'generated_body_conflict')
    before, tail = document.body.split(NOTES_START, 1)
    notes, after = tail.split(NOTES_END, 1)
    require(not after, 'Unexpected text after Notes', 'generated_body_conflict')
    # An idea accepted before the prior-art fields existed may hold the body written then (no prior-art line) or the
    # one a later save wrote ("Not checked"); both are the same generated summary. A read rewrites nothing.
    require(before + NOTES_START in _accepted_summaries(baseline if baseline is not None else document.metadata),
            'Generated detail summary changed; explicit repair required', 'generated_body_conflict')
    return notes


def encode_detail(idea, notes='', extensions=None, history_links=None, *, previous=None,
                  previous_baseline=None, transaction_revision=None):
    current = copy.deepcopy({k:v for k,v in idea.items() if k != 'revisions'})
    for plan in current.get('plans', []):
        plan.pop('content', None)
    _idea(current)
    links = history_links
    if links is None:
        require('revisions' in idea, 'History links or revisions required')
        links = [dict(path='history/' + idea['idea_id'] + '/r' + str(s['revision']) + '.md',
                      sha256=digest(encode_history(idea['idea_id'], s, origin=idea['origin']))) for s in idea['revisions']]
    if previous is not None:
        previous.require_rewritable()
        notes = _notes(previous, previous_baseline)
        extensions = previous.metadata['extensions'] if extensions is None else extensions
    if transaction_revision is None:
        transaction_revision = previous.metadata['transaction_revision'] if previous is not None else 0
    integer(transaction_revision, 'detail transaction revision')
    metadata = dict(schema_version=2, kind='idea', idea=current, history=copy.deepcopy(links),
                    metadata_evidence=_metadata_links(current), transaction_revision=transaction_revision,
                    extensions={} if extensions is None else extensions)
    _detail_metadata(metadata)
    if previous is not None:
        old_proposals = agent_proposal_links(previous.metadata['extensions'], current['idea_id'])
        new_proposals = agent_proposal_links(metadata['extensions'], current['idea_id'])
        require(new_proposals[:len(old_proposals)] == old_proposals,
                'Immutable agent proposal links cannot be removed, reordered or changed', 'corrupt_store')
        old_assets = asset_links(previous.metadata['extensions'], current['idea_id'])
        new_assets = asset_links(metadata['extensions'], current['idea_id'])
        require(new_assets[:len(old_assets)] == old_assets,
                'Immutable asset links cannot be removed, reordered or changed', 'corrupt_store')
        old_handoffs = handoff_links(previous.metadata['extensions'], current['idea_id'])
        new_handoffs = handoff_links(metadata['extensions'], current['idea_id'])
        require(new_handoffs[:len(old_handoffs)] == old_handoffs,
                'Immutable handoff links cannot be removed, reordered or changed', 'corrupt_store')
    require(type(notes) is str and NOTES_START not in notes and NOTES_END not in notes, 'Notes contain reserved markers')
    return encode_document(metadata, _detail_summary(metadata) + notes + NOTES_END, previous=previous)


def _detail_metadata(metadata):
    _exact(metadata, {'schema_version','kind','idea','history','metadata_evidence','transaction_revision','extensions'}, 'detail frontmatter')
    require(type(metadata['schema_version']) is int and metadata['schema_version'] == 2 and metadata['kind'] == 'idea', 'Unsupported detail schema')
    _idea(metadata['idea'])
    _extensions(metadata['extensions'])
    agent_proposal_links(metadata['extensions'], metadata['idea']['idea_id'])
    asset_links(metadata['extensions'], metadata['idea']['idea_id'])
    handoff_links(metadata['extensions'], metadata['idea']['idea_id'])
    integer(metadata['transaction_revision'], 'detail transaction revision')
    require(type(metadata['history']) is list and len(metadata['history']) == metadata['idea']['revision'], 'Missing history links')
    for n, link in enumerate(metadata['history'], 1):
        _link(link, 'history/' + metadata['idea']['idea_id'] + '/r' + str(n) + '.md')
    _exact(metadata['metadata_evidence'], set(METADATA_TYPES), 'metadata evidence')
    for key, kind in METADATA_TYPES.items():
        links = metadata['metadata_evidence'][key]
        records = metadata['idea'][key]
        require(type(links) is list and len(links) == len(records), 'Missing or extra '+kind+' metadata links', 'corrupt_store')
        for link, record in zip(links, records):
            _metadata_link(link, metadata_path(metadata['idea']['idea_id'], kind, record))


def decode_detail(raw, *, check_body=True):
    doc = parse_document(raw)
    _detail_metadata(doc.metadata)
    if check_body:
        _notes(doc)
    return doc


def detail_notes(document, *, baseline=None):
    return _notes(document, baseline)


def encode_history(idea_id, snapshot, *, origin):
    check_id(idea_id)
    _snapshot(snapshot)
    meta = dict(schema_version=2, kind='history', idea_id=idea_id, snapshot=copy.deepcopy(snapshot))
    _origin(origin)
    meta['origin'] = copy.deepcopy(origin)
    return encode_document(meta,
                           '# Immutable revision ' + str(snapshot['revision']) + '\n\nDo not edit this evidence.\n')


def decode_history(raw):
    doc = parse_document(raw)
    _exact(doc.metadata, {'schema_version','kind','idea_id','snapshot','origin'}, 'history frontmatter')
    _origin(doc.metadata['origin'])
    require(type(doc.metadata['schema_version']) is int and doc.metadata['schema_version'] == 2 and doc.metadata['kind'] == 'history', 'Unsupported history schema')
    check_id(doc.metadata['idea_id'])
    _snapshot(doc.metadata['snapshot'])
    return doc


def pointer_path(idea_id, kind):
    check_id(idea_id)
    require(kind in ('moved', 'delivered'), 'Unknown pointer kind')
    return 'history/' + idea_id + '/' + kind + '.md'


def home_file(workspace_path, idea_id):
    """Where a moved detail file lives: <workspace>/ideas/<idea_id>.md."""
    check_id(idea_id)
    return str(PurePosixPath(workspace_path) / 'ideas' / (idea_id + '.md'))


def pointer_links(extensions, extension, kind):
    """Index links to immutable lifecycle pointers; one pointer per idea."""
    _extensions(extensions)
    links = extensions.get(extension, [])
    require(type(links) is list and len(links) <= MAX_POINTER_LINKS, 'Pointer links must be a bounded list', 'too_large')
    for link in links:
        _exact(link, {'idea_id', 'path', 'sha256'}, kind + ' link')
        check_id(link['idea_id'])
        _digest(link['sha256'])
        require(link['path'] == pointer_path(link['idea_id'], kind), 'Pointer link path differs from its idea', 'corrupt_store')
    require(len({link['idea_id'] for link in links}) == len(links), 'Duplicate ' + kind + ' pointer', 'corrupt_store')
    return copy.deepcopy(links)


def move_links(extensions):
    return pointer_links(extensions, MOVE_EXTENSION, 'moved')


def delivered_links(extensions):
    return pointer_links(extensions, DELIVERED_EXTENSION, 'delivered')


def _workspace(value, idea_id=None):
    _exact(value, {'name', 'path'}, 'workspace')
    text(value['name'], 'workspace name', 100)
    require('\n' not in value['name'] and '\r' not in value['name'], 'workspace name must be a single line')
    text(value['path'], 'workspace path', 4096)
    require(PurePosixPath(value['path']).is_absolute() or PureWindowsPath(value['path']).is_absolute(), 'workspace path must be absolute')


def _frozen(value, idea_id, revision, plan_id):
    _exact(value, {'idea', 'extensions'}, 'frozen detail')
    _idea(value['idea'])
    idea = value['idea']
    require(idea['idea_id'] == idea_id and idea['revision'] == revision and idea['status'] == 'archived'
            and any(plan['plan_id'] == plan_id for plan in idea['plans']), 'Frozen idea differs from its pointer')
    require(all('content' not in plan for plan in idea['plans']), 'Frozen plans must not carry content')
    _extensions(value['extensions'])
    agent_proposal_links(value['extensions'], idea_id)
    asset_links(value['extensions'], idea_id)
    handoff_links(value['extensions'], idea_id)


def encode_moved(idea_id, *, idea_revision, plan_id, workspace, home_path, moved_sha256, actor, timestamp, frozen):
    """Immutable pointer left in the store when a detail file moves to a workspace.

    frozen keeps the living fields as they were at the move (the idea mapping
    without revisions, plus detail extension links) so history stays loadable;
    only the file itself, Notes included, leaves the store.
    """
    check_id(idea_id)
    check_id(plan_id, 'plan')
    integer(idea_revision, 'moved revision', 1)
    meta = dict(schema_version=2, kind='moved', idea_id=idea_id, idea_revision=idea_revision, plan_id=plan_id,
                workspace=copy.deepcopy(workspace), home_path=home_path, moved_sha256=moved_sha256,
                actor=actor, timestamp=timestamp, frozen=copy.deepcopy(frozen))
    _check_moved(meta)
    return encode_document(meta, '# Moved idea\n\nThe living detail file is in the workspace. Do not edit this evidence.\n')


def _check_moved(meta):
    _exact(meta, MOVED_KEYS, 'moved pointer')
    require(type(meta['schema_version']) is int and meta['schema_version'] == 2 and meta['kind'] == 'moved', 'Unsupported moved pointer schema')
    check_id(meta['idea_id'])
    check_id(meta['plan_id'], 'plan')
    integer(meta['idea_revision'], 'moved revision', 1)
    _workspace(meta['workspace'])
    require(meta['home_path'] == home_file(meta['workspace']['path'], meta['idea_id']), 'Home path differs from workspace and idea')
    _digest(meta['moved_sha256'])
    text(meta['actor'], 'moved actor', 200)
    text(meta['timestamp'], 'moved timestamp', 200)
    _frozen(meta['frozen'], meta['idea_id'], meta['idea_revision'], meta['plan_id'])


def decode_moved(raw):
    doc = parse_document(raw)
    _check_moved(doc.metadata)
    return copy.deepcopy(doc.metadata)


def encode_delivered(idea_id, *, ref, actor, timestamp):
    check_id(idea_id)
    meta = dict(schema_version=2, kind='delivered', idea_id=idea_id, ref=ref, actor=actor, timestamp=timestamp)
    _check_delivered(meta)
    return encode_document(meta, '# Delivered idea\n\nDo not edit this evidence.\n')


def _check_delivered(meta):
    _exact(meta, DELIVERED_KEYS, 'delivered pointer')
    require(type(meta['schema_version']) is int and meta['schema_version'] == 2 and meta['kind'] == 'delivered', 'Unsupported delivered pointer schema')
    check_id(meta['idea_id'])
    for key in ('ref', 'actor', 'timestamp'):
        text(meta[key], 'delivered ' + key, 500 if key == 'ref' else 200)


def decode_delivered(raw):
    doc = parse_document(raw)
    _check_delivered(doc.metadata)
    return copy.deepcopy(doc.metadata)


def _index_body(state):
    lines = ['# Ideas', '', INDEX_AUTHORITY.rstrip(), '', '| Rank | Idea | Status | Human urgency | Human importance | AI scores |',
             '| --- | --- | --- | --- | --- | --- |']
    for n, key in enumerate(state['order'], 1):
        idea = state['ideas'][key]
        ratings = idea['ratings'] or {}
        scores = ', '.join(a['method'] + ': ' + ('unknown' if a['score'] is None else str(a['score'])) for a in idea['assessments']) or 'unknown'
        lines.append('| ' + ' | '.join((str(n), markdown_link(key, key + '.md'), idea['status'],
                     str(ratings.get('urgency', 'unknown')), str(ratings.get('importance', 'unknown')), markdown_text(scores))) + ' |')
    return '\n'.join(lines) + '\n'


def encode_index(state, extensions=None, *, previous=None, previous_state=None):
    links = [dict(path='history/backlog/r' + str(n) + '.md', sha256=digest(_encode_placement(p))) for n,p in enumerate(state['placements'], 1)]
    if previous is not None:
        previous.require_rewritable()
        require(previous_state is not None, 'Previous state required to protect generated index body')
        require(previous.body == _index_body(previous_state), 'Generated index table changed; explicit repair required', 'generated_body_conflict')
        extensions = previous.metadata['extensions'] if extensions is None else extensions
    meta = dict(schema_version=2, kind='index', order=copy.deepcopy(state['order']),
                backlog_revision=state['backlog_revision'], transaction_revision=state['transaction_revision'],
                placements=links, extensions={} if extensions is None else extensions)
    _index_metadata(meta)
    return encode_document(meta, _index_body(state), previous=previous)


def _index_metadata(meta):
    _exact(meta, {'schema_version','kind','order','backlog_revision','transaction_revision','placements','extensions'}, 'index frontmatter')
    require(type(meta['schema_version']) is int and meta['schema_version'] == 2 and meta['kind'] == 'index', 'Unsupported index schema')
    integer(meta['backlog_revision'], 'backlog revision')
    integer(meta['transaction_revision'], 'transaction revision')
    require(type(meta['order']) is list, 'Order must be a list')
    for key in meta['order']:
        check_id(key)
    require(len(set(meta['order'])) == len(meta['order']), 'Duplicate idea in order')
    require(type(meta['placements']) is list, 'Placements must be a list')
    for n, link in enumerate(meta['placements'], 1):
        _link(link, 'history/backlog/r' + str(n) + '.md')
    _extensions(meta['extensions'])
    move_links(meta['extensions'])
    delivered_links(meta['extensions'])


def decode_index(raw, *, state=None):
    doc = parse_document(raw)
    _index_metadata(doc.metadata)
    if state is not None:
        require(doc.body == _index_body(state), 'Generated index table changed; explicit repair required', 'generated_body_conflict')
    return doc


def _encode_placement(placement):
    _placement(placement)
    return encode_document(dict(schema_version=2, kind='placement', placement=copy.deepcopy(placement)), '# Immutable placement evidence\n')


# Test switch: False forces every idea down the full re-encode/re-parse path.
_UNCHANGED_SHORTCUT = True


def _encode_unchanged_detail(key, current, idea, prior_idea, prior_document, history_links, metadata_links):
    """Detail bytes for an idea identical to its baseline, or (None, None) for the full path.

    Equality is strict: canonical JSON of both ideas (so 1, 1.0 and True differ,
    unlike ==). The shortcut applies only when the previous document is
    rewritable and its metadata (less transaction counter) and body both equal
    what a fresh encode would produce; otherwise the caller takes the full path.
    The result is encode_document of that same metadata/body, so it is byte-
    identical to the full path, which would reach the same call with the
    previous transaction counter. Only the baseline/after/detail parses and the
    repeated history/metadata encodes are skipped. Evidence-link checks remain
    in the caller and read the returned extensions.
    """
    if json.dumps(idea, sort_keys=True, allow_nan=False) != json.dumps(prior_idea, sort_keys=True, allow_nan=False):
        return None, None
    prior_document.require_rewritable()
    extensions_ = prior_document.metadata.get('extensions')
    counter = prior_document.metadata.get('transaction_revision')
    if type(extensions_) is not dict or type(counter) is not int:
        return None, None
    metadata = dict(schema_version=2, kind='idea', idea={k:v for k,v in current.items() if k != 'revisions'},
                    history=copy.deepcopy(history_links),
                    metadata_evidence=copy.deepcopy(metadata_links), transaction_revision=counter,
                    extensions=extensions_)
    _detail_metadata(metadata)
    try:
        notes = _notes(prior_document, metadata)
    except IdeaError:
        return None, None
    before = {k:v for k,v in prior_document.metadata.items() if k != 'transaction_revision'}
    after = {k:v for k,v in metadata.items() if k != 'transaction_revision'}
    if json.dumps(before, sort_keys=True, allow_nan=False) != json.dumps(after, sort_keys=True, allow_nan=False):
        return None, None
    body = _detail_summary(metadata) + notes + NOTES_END
    if prior_document.body in [form + notes + NOTES_END for form in _accepted_summaries(metadata)]:
        body = prior_document.body  # a legacy body is kept byte-for-byte on a no-op
    if body != prior_document.body:
        return None, None
    return encode_document(metadata, body, previous=prior_document), copy.deepcopy(extensions_)



def encode_state(state, *, notes=None, extensions=None, previous=None, previous_state=None,
                 proposal_evidence=None, asset_evidence=None, handoff_evidence=None):
    """Return publication after-images; no filesystem effects or transaction policy.

    Existing plan-evidence remains byte-identical and is not wrapped in new YAML.
    Archives are reconstructed from immutable history, not a second authority.
    proposal_evidence is the complete trusted path->bytes map for reserved agent
    links. Previous detail documents carry references, not the immutable bytes.
    No filesystem reads occur; absent, mismatched or extra evidence is refused.
    asset_evidence supplies the same complete bytes witness for reserved asset
    links; blobs/stages never enter this map or the Markdown journal.
    handoff_evidence supplies complete canonical packets for protected links;
    currentness and atomic receipt witnesses remain Store/Service obligations.
    """
    require(type(state) is dict and type(state.get('schema_version')) is int and state['schema_version'] == 1, 'Unsupported domain state schema')
    require(type(state.get('order')) is list and type(state.get('ideas')) is dict and set(state['order']) == set(state['ideas']), 'Order must contain all ideas')
    notes, extensions, previous = notes or {}, extensions or {}, previous or {}
    proposal_evidence = {} if proposal_evidence is None else proposal_evidence
    require(type(proposal_evidence) is dict and len(proposal_evidence) <= MAX_PROPOSAL_EVIDENCE_FILES,
            'Proposal evidence mapping exceeds file limit', 'too_large')
    total = 0
    for relative, raw in proposal_evidence.items():
        require(type(relative) is str and type(raw) is bytes and len(raw) <= MAX_INPUT,
                'Proposal evidence requires bounded path/bytes entries', 'too_large')
        total += len(raw)
        require(total <= MAX_PROPOSAL_EVIDENCE_BYTES, 'Proposal evidence exceeds aggregate limit', 'too_large')
    asset_evidence = {} if asset_evidence is None else asset_evidence
    require(type(asset_evidence) is dict and len(asset_evidence) <= MAX_ASSET_EVIDENCE_FILES,
            'Asset evidence mapping exceeds file limit', 'too_large')
    total = 0
    for relative, raw in asset_evidence.items():
        require(type(relative) is str and type(raw) is bytes and len(raw) <= MAX_INPUT,
                'Asset evidence requires bounded path/bytes entries', 'too_large')
        total += len(raw)
        require(total <= MAX_ASSET_EVIDENCE_BYTES, 'Asset evidence exceeds aggregate limit', 'too_large')
    handoff_evidence = {} if handoff_evidence is None else handoff_evidence
    require(type(handoff_evidence) is dict and len(handoff_evidence) <= MAX_HANDOFF_EVIDENCE_FILES,
            'Handoff evidence mapping exceeds file limit', 'too_large')
    total = 0
    for relative, raw in handoff_evidence.items():
        require(type(relative) is str and type(raw) is bytes and len(raw) <= MAX_STATE,
                'Handoff evidence requires bounded path/bytes entries', 'too_large')
        total += len(raw)
        require(total <= MAX_HANDOFF_EVIDENCE_BYTES, 'Handoff evidence exceeds aggregate limit', 'too_large')
    files = {}
    required_proposals = set()
    required_assets = set()
    required_handoffs = set()
    for key, idea in state['ideas'].items():
        require(key == idea['idea_id'], 'Idea key mismatch')
        current = copy.deepcopy(idea)
        for plan in current['plans']:
            content = plan.pop('content')
            require(digest(content.encode('utf-8')) == plan['sha256'], 'Plan content hash mismatch')
            files['plan-evidence/' + plan['plan_id'] + '.md'] = content.encode('utf-8')
        history_links = []
        for snap in idea['revisions']:
            history_path = 'history/' + key + '/r' + str(snap['revision']) + '.md'
            files[history_path] = encode_history(key, snap, origin=idea['origin'])
            history_links.append(dict(path=history_path, sha256=digest(files[history_path])))
        metadata_links = {}
        for field, kind in METADATA_TYPES.items():
            metadata_links[field] = []
            for record in current[field]:
                metadata_path_ = metadata_path(key, kind, record)
                files[metadata_path_] = encode_metadata(key, kind, record)
                metadata_links[field].append(dict(path=metadata_path_, sha256=digest(files[metadata_path_])))
        path = key + '.md'
        encoded_detail = unchanged_extensions = None
        if (_UNCHANGED_SHORTCUT and previous_state is not None and path in previous
                and key in previous_state['ideas'] and extensions.get(key) is None):
            encoded_detail, unchanged_extensions = _encode_unchanged_detail(
                key, current, idea, previous_state['ideas'][key], previous[path], history_links, metadata_links)
        if encoded_detail is not None:
            detail_extensions = unchanged_extensions
        else:
            baseline = None
            if previous_state is not None and path in previous:
                prior_idea = previous_state['ideas'][key]
                prior = copy.deepcopy(prior_idea)
                for plan in prior['plans']:
                    plan.pop('content', None)
                # Earlier generated links belong to the baseline summary. Appending
                # new links must not make the previous body appear externally edited.
                baseline = parse_document(encode_detail(prior, extensions=previous[path].metadata['extensions'])).metadata
            prior_document = previous.get(path)
            encoded_detail = encode_detail(current, notes.get(key, ''), extensions.get(key),
                                           previous=prior_document, previous_baseline=baseline,
                                           transaction_revision=state['transaction_revision'])
            if prior_document is not None:
                after = parse_document(encoded_detail)
                before_metadata = {k:v for k,v in prior_document.metadata.items() if k != 'transaction_revision'}
                after_metadata = {k:v for k,v in after.metadata.items() if k != 'transaction_revision'}
                same_metadata = (json.dumps(before_metadata, sort_keys=True, allow_nan=False) ==
                                 json.dumps(after_metadata, sort_keys=True, allow_nan=False))
                if same_metadata and prior_document.body == after.body:
                    # An unrelated idea mutation must not force all detail files to
                    # carry the newest transaction counter. The touched detail is
                    # sufficient to recover the counter without rewriting IDEAS.md.
                    encoded_detail = encode_detail(current, notes.get(key, ''), extensions.get(key),
                                                   previous=prior_document, previous_baseline=baseline,
                                                   transaction_revision=prior_document.metadata['transaction_revision'])
            detail_extensions = parse_document(encoded_detail).metadata['extensions']
        files[path] = encoded_detail
        from idea_proposal_evidence import decode_proposal
        for link in agent_proposal_links(detail_extensions, key):
            relative = link['path']
            require(relative in proposal_evidence, 'Missing linked agent proposal bytes: ' + relative, 'corrupt_store')
            raw = proposal_evidence[relative]
            decode_proposal(raw, path=relative, expected_idea_id=key, link=link)
            require(relative not in files, 'Agent proposal collides with existing evidence', 'corrupt_store')
            files[relative] = raw
            required_proposals.add(relative)
        from idea_asset_evidence import decode_record
        for link in asset_links(detail_extensions, key):
            relative = link['path']
            require(relative in asset_evidence, 'Missing linked asset evidence bytes: '+relative, 'corrupt_store')
            raw = asset_evidence[relative]
            decode_record(raw, path=relative, expected_idea_id=key, link=link)
            require(relative not in files, 'Asset evidence collides with existing evidence', 'corrupt_store')
            files[relative] = raw
            required_assets.add(relative)
        from idea_handoff_evidence import decode_record as decode_handoff
        for link in handoff_links(detail_extensions, key):
            relative = link['path']
            require(relative in handoff_evidence, 'Missing linked handoff bytes: '+relative, 'corrupt_store')
            raw = handoff_evidence[relative]
            decode_handoff(raw, path=relative, expected_idea_id=key, link=link)
            require(relative not in files, 'Handoff collides with existing evidence', 'corrupt_store')
            files[relative] = raw
            required_handoffs.add(relative)
    require(set(proposal_evidence) == required_proposals,
            'Orphan/unreferenced agent proposal evidence supplied', 'corrupt_store')
    require(set(asset_evidence) == required_assets,
            'Orphan/unreferenced asset evidence supplied', 'corrupt_store')
    require(set(handoff_evidence) == required_handoffs,
            'Orphan/unreferenced handoff evidence supplied', 'corrupt_store')
    for n, placement in enumerate(state['placements'], 1):
        files['history/backlog/r' + str(n) + '.md'] = _encode_placement(placement)
    files['IDEAS.md'] = encode_index(state, extensions.get('IDEAS.md'), previous=previous.get('IDEAS.md'), previous_state=previous_state)
    return files


def decode_state(files, *, check_body=True):
    """Reconstruct existing domain shape; Store validates immutable/domain links.

    check_body=False is for explicit external-edit candidate inspection only.
    Callers must not publish a candidate until import/invalidation succeeds.
    """
    require('IDEAS.md' in files, 'Missing IDEAS.md authority', 'corrupt_store')
    index = decode_index(files['IDEAS.md'])
    meta = index.metadata
    state = dict(schema_version=1, transaction_revision=meta['transaction_revision'], backlog_revision=meta['backlog_revision'],
                 order=copy.deepcopy(meta['order']), ideas={}, placements=[], archives={})
    handoff_paths, handoff_total = set(), 0
    def linked(link):
        require(link['path'] in files, 'Missing linked evidence: ' + link['path'], 'corrupt_store')
        raw = files[link['path']]
        require(digest(raw) == link['sha256'], 'Linked evidence hash mismatch: ' + link['path'], 'corrupt_store')
        return raw
    for key in state['order']:
        require(key + '.md' in files, 'Missing idea detail: ' + key, 'corrupt_store')
        doc = decode_detail(files[key + '.md'], check_body=check_body)
        state['transaction_revision'] = max(state['transaction_revision'], doc.metadata['transaction_revision'])
        idea = copy.deepcopy(doc.metadata['idea'])
        require(idea['idea_id'] == key, 'Detail identity mismatch', 'corrupt_store')
        idea['revisions'] = []
        for n, link in enumerate(doc.metadata['history'], 1):
            history = decode_history(linked(link)).metadata
            require('origin' in history and history['origin'] == idea['origin'], 'Immutable origin differs from history evidence', 'corrupt_store')
            require(history['idea_id'] == key and history['snapshot']['revision'] == n, 'History identity mismatch', 'corrupt_store')
            idea['revisions'].append(history['snapshot'])
        for field, kind in METADATA_TYPES.items():
            for link, record in zip(doc.metadata['metadata_evidence'][field], idea[field]):
                evidence = decode_metadata(linked(link), path=link['path']).metadata
                require(evidence['idea_id'] == key and evidence['record_type'] == kind,
                        'Metadata evidence identity or type mismatch', 'corrupt_store')
                require(evidence['record'] == record, 'Protected '+kind+' record differs from immutable metadata', 'corrupt_store')
        from idea_proposal_evidence import decode_proposal
        for link in agent_proposal_links(doc.metadata['extensions'], key):
            decode_proposal(linked(link), path=link['path'], expected_idea_id=key, link=link)
        from idea_asset_evidence import decode_record
        for link in asset_links(doc.metadata['extensions'], key):
            decode_record(linked(link), path=link['path'], expected_idea_id=key, link=link)
        from idea_handoff_evidence import decode_record as decode_handoff
        for link in handoff_links(doc.metadata['extensions'], key):
            require(link['path'] not in handoff_paths, 'Handoff belongs to multiple ideas', 'corrupt_store')
            raw = files.get(link['path'])
            require(type(raw) is bytes, 'Missing linked handoff bytes: '+link['path'], 'corrupt_store')
            require(len(raw) <= MAX_STATE, 'Handoff evidence exceeds byte limit', 'too_large')
            handoff_paths.add(link['path']); handoff_total += len(raw)
            require(len(handoff_paths) <= MAX_HANDOFF_EVIDENCE_FILES
                    and handoff_total <= MAX_HANDOFF_EVIDENCE_BYTES,
                    'Handoff evidence exceeds aggregate limits', 'too_large')
            decode_handoff(linked(link), path=link['path'], expected_idea_id=key, link=link)
        for plan in idea['plans']:
            path = 'plan-evidence/' + plan['plan_id'] + '.md'
            require(path in files and digest(files[path]) == plan['sha256'], 'Missing or changed accepted plan evidence', 'corrupt_store')
            try:
                plan['content'] = files[path].decode('utf-8')
            except UnicodeError as exc:
                raise IdeaError('corrupt_store', 'Accepted plan evidence must be UTF-8') from exc
            revision = plan['idea_revision']
            integer(revision, 'plan idea revision', 1, idea['revision'])
            state['archives'][key + '/r' + str(revision) + '.json'] = dict(idea_id=key, origin=copy.deepcopy(idea['origin']), revision=copy.deepcopy(idea['revisions'][revision - 1]))
        state['ideas'][key] = idea
    for link in meta['placements']:
        doc = parse_document(linked(link))
        _exact(doc.metadata, {'schema_version','kind','placement'}, 'placement frontmatter')
        require(type(doc.metadata['schema_version']) is int and doc.metadata['schema_version'] == 2 and doc.metadata['kind'] == 'placement', 'Unsupported placement schema')
        _placement(doc.metadata['placement'])
        state['placements'].append(doc.metadata['placement'])
    if check_body:
        decode_index(files['IDEAS.md'], state=state)
    return state
