"""Scrub a real glitch-idea v0.3 store into a public, neutral fixture.

Usage: python3 tests/fixtures/scrub_store.py SOURCE_STORE OUTPUT_DIR [--map FILE]

SOURCE_STORE is read only. FILE is a local-only map, one `from => to` per line (matched without regard to
case); it names whatever the private-terms list does not: people, communities, places, host paths. Every
term in tests/private-terms.txt that the map does not name is replaced by `redacted-N`. The map and the
terms never enter this file or the output.

The store is decoded with the module's own decoder, every string is scrubbed, then every hash that
depended on text is re-derived with the module's own functions: the origin sha256, each acceptance's
source digest and dependency digests (current workflow and every revision snapshot), the handoff
record, its content-addressed packet path and link, the history files and index placements, and the
session receipt that witnesses the handoff.
Before scrubbing, the same re-derivation runs on the untouched data and must change nothing; that
proves the re-derivation matches what the store holds.
The detail body is written WITHOUT the prior-art line, as the store was before that field existed.
No git command is run. OUTPUT_DIR is replaced.
"""
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'glitch-idea/scripts'))
import idea_handoff_evidence as ho
import idea_markdown as md
import idea_workflow as wf

NEW_ROOT = '/srv/example/store'
TERMS = ROOT / 'tests/private-terms.txt'
HOST_PATH = re.compile(r'(?<![\w.])(/opt/|/home/|/tmp/|/var/|/Users/)')


def load_rules(map_file):
    pairs = []
    if map_file:
        for line in Path(map_file).read_text(encoding='utf-8').splitlines():
            if '=>' in line:
                old, new = (part.strip() for part in line.split('=>', 1))
                pairs.append((old, new))
    named = {old.lower() for old, _ in pairs}
    if TERMS.exists():
        for n, term in enumerate((t.strip() for t in TERMS.read_text(encoding='utf-8').splitlines()), 1):
            if term and term.lower() not in named:
                pairs.append((term, 'redacted-' + str(n)))
    pairs.sort(key=lambda pair: -len(pair[0]))
    return [(re.compile(re.escape(old), re.I), new) for old, new in pairs]


def scrub(value, rules):
    if isinstance(value, str):
        for pattern, new in rules:
            value = pattern.sub(new, value)
        return value
    if isinstance(value, list):
        return [scrub(item, rules) for item in value]
    if isinstance(value, dict):
        return {key: scrub(item, rules) for key, item in value.items()}
    return value


def rederive_workflow(workflow):
    """Recompute every acceptance's source digest and dependency digests from the fields it covers."""
    for step in wf.STEP_ORDER:
        record = workflow['steps'][step]
        receipt = record['acceptance']
        if receipt is None:
            continue
        dependencies, sources = {}, {step: record['fields']}
        for name in receipt['dependencies']:
            earlier = workflow['steps'][name]
            dependencies[name] = wf._record_source(name, earlier)
            sources[name] = earlier['fields']
        receipt['dependencies'] = dependencies
        receipt['source_digest'] = wf.source_digest(step, receipt['source_revision'], sources)


def rederive_idea(idea):
    idea['origin']['sha256'] = hashlib.sha256(idea['origin']['text'].encode('utf-8')).hexdigest()
    rederive_workflow(idea['workflow'])
    for snapshot in idea['revisions']:
        if isinstance(snapshot.get('workflow'), dict):
            rederive_workflow(snapshot['workflow'])
    capture = idea['workflow']['steps']['capture']['fields']
    if capture and 'raw_text' in capture:
        assert hashlib.sha256(capture['raw_text'].encode('utf-8')).hexdigest() == idea['origin']['sha256'], \
            'capture raw_text no longer matches the origin'


def read_store(source):
    files = {}
    for path in sorted(Path(source).rglob('*')):
        relative = path.relative_to(source).as_posix()
        if path.is_file() and not relative.startswith(('session-recovery/', '.transactions/')) and relative != '.lock':
            files[relative] = path.read_bytes()
    return files


def no_prior_art_line(discovery, legacy_line=True):
    return []


def encode(state, notes, extensions, handoff_evidence):
    # The store predates the prior-art fields: its body carries no such line (as test_legacy_prior_art shape 1).
    with mock.patch.object(md, '_prior_art_lines', no_prior_art_line):
        return md.encode_state(state, notes=notes, extensions=extensions, handoff_evidence=handoff_evidence)


def build(source, out, map_file=None):
    source, out = Path(source), Path(out)
    rules = load_rules(map_file)
    files = read_store(source)
    state = md.decode_state(files)
    (key,) = state['order']
    detail = md.parse_document(files[key + '.md'])
    notes = {key: md.detail_notes(detail)}
    (old_link,) = md.handoff_links(detail.metadata['extensions'], key)
    old_record = ho.decode_record(files[old_link['path']], path=old_link['path'], expected_idea_id=key, link=old_link)

    # Proof: re-deriving the untouched data reproduces it exactly.
    probe = copy.deepcopy(state['ideas'][key])
    rederive_idea(probe)
    assert probe == state['ideas'][key], 'the re-derivation does not reproduce the store it was given'

    state = scrub(state, rules)
    idea = state['ideas'][key]
    rederive_idea(idea)
    state['archives'] = {}
    record_in = scrub(old_record, rules)

    revision = 'history/' + key + '/r' + str(idea['revision']) + '.md'
    # First pass without the handoff: the detail and index as they stood before it was published.
    before = encode(copy.deepcopy(state), notes, None, {})
    record = ho.build_record(state, idea, source_files={
            'detail': dict(path=NEW_ROOT + '/' + key + '.md', sha256=md.digest(before[key + '.md'])),
            'index': dict(path=NEW_ROOT + '/IDEAS.md', sha256=md.digest(before['IDEAS.md'])),
            'revision': dict(path=NEW_ROOT + '/' + revision, sha256=md.digest(before[revision]))},
        design_set=record_in['design_set'], handoff_id=record_in['handoff_id'], session_id=record_in['session_id'],
        request_id=record_in['request_id'], actor=record_in['actor'], timestamp=record_in['timestamp'])
    raw = ho.encode_record(record)
    link = ho.record_link(record, raw)
    extensions = {key: dict(scrub(detail.metadata['extensions'], rules), glitch_idea_handoffs=[link])}
    result = encode(state, notes, extensions, {link['path']: raw})
    # Prove it loads on this code before anything is written.
    loaded = md.decode_state(result)
    assert loaded['ideas'][key] == idea and loaded['transaction_revision'] == state['transaction_revision']

    if out.exists():
        shutil.rmtree(out)
    for relative, data in result.items():
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    recovery = out / 'session-recovery'
    recovery.mkdir(parents=True, exist_ok=True)
    for path in sorted((source / 'session-recovery').glob('*.json')):
        session = scrub(json.loads(path.read_text(encoding='utf-8')), rules)
        for receipt in session['receipts'].values():
            if receipt['result'].get('handoff_id') == link['handoff_id']:
                receipt['result'].update(path=link['path'], sha256=link['sha256'])
        (recovery / path.name).write_text(json.dumps(session, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    leaks = [p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file() and HOST_PATH.search(p.read_text(encoding='utf-8'))]
    assert not leaks, 'host paths remain in: ' + ', '.join(leaks)
    return sorted(p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file())


def main(argv):
    args = [a for a in argv if a != '--map']
    map_file = None
    if '--map' in argv:
        map_file = argv[argv.index('--map') + 1]
        args.remove(map_file)
    if len(args) != 2:
        sys.exit(__doc__)
    for name in build(args[0], args[1], map_file):
        print(name)


if __name__ == '__main__':
    main(sys.argv[1:])
