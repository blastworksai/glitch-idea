"""Migration chain engine: bring each idea file up to the current workflow version.

Caller MUST hold the store lock (idea_platform.store_lock) and have run
transactions.recover(). run() is called by the store on a cache miss only.

Each idea migrates in its own transaction: the original detail bytes are kept
byte-for-byte as history/<id>/migrations/w<from>-<sha256>.md (immutable), the new
detail carries a link to it in extensions.glitch_idea_migrations, and the step's
new history revision is published with it. A step that raises, or a publish that
loses its CAS, leaves that idea's bytes untouched and the idea is reported held.
Moved, delivered and archived ideas are held, never touched.

A step is step(raw_detail, history, *, actor, timestamp) -> (new_detail, new_files)
where history maps every history/<id>/r<N>.md path to its bytes. It raises
IdeaError to refuse. IDEA_STEPS maps a from-version to a step (a callable, or the
name of a module whose `step` is imported lazily).
"""
from dataclasses import dataclass, field
import importlib
import re

import idea_markdown as md
import idea_transactions as transactions
from idea_domain import IdeaError, digest, now

ACTOR = 'glitch-idea'
LINKS = 'glitch_idea_migrations'
IDEA_STEPS = {2: 'idea_chain_v2'}
INDEX_STEPS = {}  # empty in this release: the index migrates only when a future release registers a step
_ID = re.compile(r'idea_[0-9a-f]{32}')
_HISTORY = re.compile(r'r[1-9][0-9]*\.md')


@dataclass
class Report:
    updated: list = field(default_factory=list)
    held: dict = field(default_factory=dict)


def line(n):
    """The plain line for the member; None when nothing was updated."""
    if type(n) is not int or n < 1:
        return None
    if n == 1:
        return '1 idea was updated for this version (original saved).'
    return str(n) + ' ideas were updated for this version (originals saved).'


def _target():
    from idea_workflow import WORKFLOW_VERSION
    return WORKFLOW_VERSION


def _step(registry, version):
    found = registry.get(version)
    if found is None:
        return None
    return importlib.import_module(found).step if type(found) is str else found


def _read(root, relative):
    return transactions._read(transactions._safe(root, relative))


def _pointers(index):
    try:
        ext = index.metadata['extensions']
        return ({link['idea_id'] for link in md.move_links(ext)}
                | {link['idea_id'] for link in md.delivered_links(ext)})
    except (IdeaError, KeyError, TypeError):
        return None


def _history(root, key):
    folder = transactions._safe(root, 'history/' + key, directory=True)
    files = {}
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            if _HISTORY.fullmatch(path.name) and path.is_file():
                relative = 'history/' + key + '/' + path.name
                files[relative] = _read(root, relative)
    return files


def _migrate(root, key, raw, document, registry, target, timestamp):
    """Return (changes, expected, link) for one idea, or raise IdeaError to hold."""
    idea = document.metadata['idea']
    version = idea['workflow']['schema_version']
    history = _history(root, key)
    current, files = raw, dict(history)
    new_files, v = {}, version
    while v < target:
        step = _step(registry, v)
        if step is None:
            raise IdeaError('unsupported_idea_version', 'No migration step from version ' + str(v))
        current, produced = step(current, dict(files), actor=ACTOR, timestamp=timestamp)
        if type(current) is not bytes or type(produced) is not dict:
            raise IdeaError('unsupported_idea_version', 'Migration step returned the wrong shape')
        for path, data in produced.items():
            if type(data) is not bytes or path in history or path in new_files \
                    or not transactions._allowed(path) or not path.startswith('history/' + key + '/'):
                raise IdeaError('unsupported_idea_version', 'Migration step produced an unacceptable file: ' + str(path))
        new_files.update(produced)
        files.update(produced)
        v += 1
    new = md.parse_document(current)
    migrated = new.metadata['idea']
    if migrated.get('workflow', {}).get('schema_version') != target or migrated.get('idea_id') != key:
        raise IdeaError('unsupported_idea_version', 'Migration did not reach the current version')
    original = digest(raw)
    copy_path = 'history/' + key + '/migrations/w' + str(version) + '-' + original + '.md'
    link = dict(from_version=version, to_version=target, path=copy_path, sha256=original,
                revision=migrated['revision'], actor=ACTOR, timestamp=timestamp)
    metadata = dict(new.metadata)
    extensions = dict(metadata.get('extensions') or {})
    extensions[LINKS] = list(extensions.get(LINKS, [])) + [link]
    metadata['extensions'] = extensions
    detail = md.encode_document(metadata, new.body)
    changes = dict(new_files)
    changes[copy_path] = raw
    changes[key + '.md'] = detail
    expected = {path: None for path in changes}
    expected[key + '.md'] = original
    return changes, expected


def run(store, lock, registry=None, *, timestamp=None):
    """Migrate every selectable idea under the held store lock; return a Report."""
    del lock  # held by the caller for the whole call
    registry = IDEA_STEPS if registry is None else registry
    report = Report()
    root = store.path
    raw_index = _read(root, 'IDEAS.md')
    if raw_index is None:
        return report
    try:
        index = md.parse_document(raw_index)
        order = list(index.metadata['order'])
    except (IdeaError, KeyError, TypeError):
        return report  # corruption is the store's refusal, not a migration
    pointed = _pointers(index)
    if pointed is None:
        return report
    target = _target()
    stamp = timestamp or now()
    for key in order:
        if type(key) is not str or not _ID.fullmatch(key):
            continue
        raw = _read(root, key + '.md')
        if raw is None:
            continue
        try:
            document = md.parse_document(raw)
            idea = document.metadata['idea']
            version = idea['workflow']['schema_version']
        except (IdeaError, KeyError, TypeError):
            continue  # no workflow, or not ours to judge: the store's own load decides
        if type(version) is not int or version == target:
            continue
        try:
            if version > target:
                raise IdeaError('unsupported_idea_version', 'Version ' + str(version) + ' is newer than this release')
            if key in pointed or idea.get('status') == 'archived':
                raise IdeaError('unsupported_idea_version', 'Moved, delivered or archived ideas are not migrated')
            changes, expected = _migrate(root, key, raw, document, registry, target, stamp)
            transactions.publish(root, changes, expected).raise_for_error()
        except IdeaError as exc:
            if exc.details.get('committed') or exc.code == 'durability_uncertain':
                raise
            report.held[key] = str(exc)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError) as exc:
            report.held[key] = 'Migration step failed: ' + type(exc).__name__ + ': ' + str(exc)
        else:
            report.updated.append(key)
    return report
