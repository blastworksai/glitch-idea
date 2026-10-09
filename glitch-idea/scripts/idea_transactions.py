"""Confined recoverable multi-file publication.

Caller MUST hold idea_platform.store_lock(root / '.lock') throughout recovery,
read/validate/CAS and publish. No agent waits under that lock. Product readers
recover before reading authority; arbitrary filesystem readers can see mixed
files until IDEAS.md is published last. Not qualified on network/synced stores.

Journal is transient recovery state, not domain authority. No rollback over new
state. No arbitrary delete API: the only removable user data are the migrated
legacy state.json, one idea detail file moved out of the store (which needs
its own immutable pointer in the same transaction), and the files of one idea
removed for good (which need that idea's immutable removal tombstone and the
index in the same transaction). _checkpoint is solely an
injected test callback.
"""
import os
from pathlib import Path
import re
import stat
import uuid

from idea_domain import IdeaError, MAX_INPUT, MAX_STATE, decode, digest, encoded, require
from idea_platform import (WriteResult, make_directory, SHARED_DIR_MODE, atomic_write, sync_directory, is_shared_root,
                           FILE_AND_DIRECTORY_SYNCED, FILE_SYNCED_PROCESS_RECOVERY,
                           UNCERTAIN)

MAX_ENTRIES = 4096
MAX_MANIFEST = 2 * MAX_INPUT
MAX_JOURNAL_BYTES = 256 * MAX_INPUT
MAX_JOURNALS = 16
JOURNAL = '.transactions'
FROZEN = 'migration-recovery/v1-state.json'
RECEIPT = 'migration-recovery/receipt.json'
_ID = r'idea_[0-9a-f]{32}'
_IDEA_DETAIL = re.compile(r'(' + _ID + r')\.md')
_DELETIONS = ('freeze-delete', 'move-out', 'remove')
_TOMBSTONE = re.compile(r'history/removed/' + _ID + r'(?:\.[2-9]|\.[1-9][0-9]+)?\.md')
_TX = re.compile(r'txn_[0-9a-f]{32}')
_ALLOWED = re.compile(r'(?:IDEAS\.md|' + _ID + r'\.md|history/(?:' + _ID + r'/(?:r[1-9][0-9]*|metadata/[0-9a-f]{64}|migrations/w[1-9][0-9]*-[0-9a-f]{64}|moved|delivered)|backlog/r[1-9][0-9]*|removed/' + _ID + r'(?:\.[2-9]|\.[1-9][0-9]+)?)\.md|plan-evidence/plan_[0-9a-f]{32}\.md|archive/' + _ID + r'/r[1-9][0-9]*\.json|session-recovery/session_[0-9a-f]{32}\.json|assets/evidence/[0-9a-f]{64}\.md)')


def _conflict(message, **details):
    raise IdeaError('recovery_conflict', message, **details)


def _check(condition, message, **details):
    if not condition:
        _conflict(message, **details)


def _root(root):
    path = Path(root).expanduser().absolute()
    # Check spelling before any resolve: it must not hide symlink components.
    for component in reversed((path, *path.parents)):
        try:
            mode = component.lstat().st_mode
        except FileNotFoundError:
            _conflict('Store root must exist: ' + str(path))
        _check(stat.S_ISDIR(mode) and not stat.S_ISLNK(mode), 'Store root/ancestor must be a real directory: ' + str(component))
    return path


def _safe(root, relative, *, directory=False):
    _check(type(relative) is str and bool(relative) and '\\' not in relative and ':' not in relative
           and not relative.startswith('/') and all(p not in ('', '.', '..') for p in relative.split('/')),
           'Unsafe journal/target relative path')
    path = root
    device = root.stat().st_dev
    parts = relative.split('/')
    for n, part in enumerate(parts):
        path = path / part
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        is_directory = n < len(parts) - 1 or directory
        _check(not stat.S_ISLNK(info.st_mode), 'Symlink path component refused: ' + relative)
        _check(info.st_dev == device, 'Target must share the store filesystem: ' + relative)
        _check(stat.S_ISDIR(info.st_mode) if is_directory else stat.S_ISREG(info.st_mode),
               'Unexpected filesystem object: ' + relative)
    return path


def _pointer_path(path):
    return 'history/' + path[:-3] + '/moved.md'


def _check_pointer(raw, path, before):
    """The moved pointer must name this exact idea and the exact bytes being removed."""
    import idea_markdown
    try:
        pointer = idea_markdown.decode_moved(raw)
    except IdeaError as exc:
        _conflict('Moved pointer is invalid: ' + str(exc), path=path)
    _check(pointer['idea_id'] + '.md' == path and pointer['moved_sha256'] == before,
           'Moved pointer does not match the file being removed: ' + path, path=path)


def _allowed(path, migration=False):
    return type(path) is str and bool(_ALLOWED.fullmatch(path) or (migration and path in (FROZEN, RECEIPT)))


def _mutable(path):
    return path == 'IDEAS.md' or re.fullmatch(_ID + r'\.md', path) is not None or path.startswith('session-recovery/')


def _limit(path):
    return MAX_INPUT if path.startswith(('session-recovery/', 'assets/evidence/')) or path == RECEIPT else MAX_STATE


def _hash(value):
    return value is None or (type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None)


def _read(path, limit=MAX_STATE):
    try:
        with path.open('rb') as stream:
            raw = stream.read(limit + 1)
    except FileNotFoundError:
        return None
    _check(len(raw) <= limit, 'Recovery file exceeds byte limit: ' + str(path))
    return raw


def file_hash(root, relative):
    """Bounded safe read for Store CAS, not a generic browser route."""
    root = _root(root)
    _check(_allowed(relative, migration=True) or relative == 'state.json', 'Unknown authority path: ' + str(relative))
    raw = _read(_safe(root, relative))
    return None if raw is None else digest(raw)


def _grade(left, right):
    if UNCERTAIN in (left, right):
        return UNCERTAIN
    if FILE_SYNCED_PROCESS_RECOVERY in (left, right):
        return FILE_SYNCED_PROCESS_RECOVERY
    return right or left


def _signal(callback, phase):
    if callback is not None:
        callback(phase)


def _heal_directory(path, shared):
    """Bring a journal folder this process owns to its right mode (2770 shared, 0700 otherwise), through a descriptor that never follows a link.

    A crash can leave a folder at a looser mode; the next publish repairs it. A folder owned by someone else, a symlink or a missing path is left alone.
    """
    if os.name != 'posix':
        return
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    except OSError:
        return
    try:
        info = os.fstat(descriptor)
        wanted = SHARED_DIR_MODE if shared else 0o700
        if stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) != wanted:
            os.fchmod(descriptor, wanted)
    finally:
        os.close(descriptor)


def _write(path, raw, *, immutable=False, seal=False):
    result = atomic_write(path, raw, immutable=immutable, seal=True) if seal else atomic_write(path, raw, immutable=immutable)
    if result.error is not None:
        raise result.error
    return result.durability


def _manifest(root, tx, value):
    raw = encoded(value)
    _check(len(raw) <= MAX_MANIFEST, 'Transaction manifest exceeds byte limit')
    return _write(_safe(root, JOURNAL + '/' + tx + '/manifest.json'), raw)


def _validate_manifest(value, tx):
    _check(type(value) is dict and set(value) == {'schema_version','transaction_id','phase','migration','entries'}, 'Unexpected transaction manifest schema')
    _check(type(value['schema_version']) is int and value['schema_version'] == 1, 'Unsupported transaction manifest schema')
    _check(value['transaction_id'] == tx and value['phase'] in ('staging','prepared','complete') and type(value['migration']) is bool, 'Invalid transaction manifest identity/phase')
    entries = value['entries']
    _check(type(entries) is list and 0 < len(entries) <= MAX_ENTRIES, 'Transaction entry count exceeds limit')
    paths = set()
    total = 0
    moves, removes = [], []
    for n, entry in enumerate(entries):
        _check(type(entry) is dict and set(entry) == {'path','before','after','before_size','after_size','operation','immutable'}, 'Unexpected transaction entry schema')
        path = entry['path']
        legacy = value['migration'] and path == 'state.json' and entry['operation'] == 'freeze-delete'
        move = not value['migration'] and entry['operation'] == 'move-out' and type(path) is str and _IDEA_DETAIL.fullmatch(path) is not None
        remove = not value['migration'] and entry['operation'] == 'remove' and type(path) is str and path != 'IDEAS.md' and _allowed(path)
        deletion = legacy or move or remove
        _check((_allowed(path, value['migration']) and entry['operation'] == 'write') or deletion, 'Unknown transaction target path')
        if remove:
            removes.append(path)
        if move:
            moves.append(path)
        _check(path not in paths, 'Duplicate transaction target')
        paths.add(path)
        _check(_hash(entry['before']) and _hash(entry['after']), 'Invalid before/after hash')
        for kind in ('before','after'):
            size = entry[kind + '_size']
            _check(type(size) is int and 0 <= size <= _limit(path) and (entry[kind] is not None or size == 0), 'Invalid staged byte size')
            total += size
        _check(type(entry['immutable']) is bool and entry['immutable'] == (not _mutable(path) and not deletion), 'Unexpected mutable/immutable policy')
        if deletion:
            _check(entry['before'] is not None and entry['after'] is None, 'Invalid legacy freeze-delete' if legacy else 'Invalid move-out' if move else 'Invalid removal')
        else:
            _check(entry['after'] is not None, 'Missing after-image hash')
            _check(not entry['immutable'] or entry['before'] in (None, entry['after']), 'Immutable evidence cannot be changed')
    _check(total <= MAX_JOURNAL_BYTES, 'Aggregate transaction bytes exceed limit')
    _check(len(moves) <= 1, 'At most one move-out per transaction')
    for path in moves:
        pointer = _pointer_path(path)
        witness = next((e for e in entries if e['path'] == pointer), None)
        _check(witness is not None and witness['operation'] == 'write' and witness['after'] is not None and 'IDEAS.md' in paths,
               'move-out requires the idea pointer and the index in the same transaction: ' + path)
    if removes:
        _check(not moves and _tombstone_witness(entries, paths) is not None and 'IDEAS.md' in paths,
               'removal requires its tombstone and the index in the same transaction')
    if value['migration']:
        lookup = {entry['path']:entry for entry in entries}
        _check({'state.json', FROZEN, RECEIPT, 'IDEAS.md'} <= paths, 'Migration requires frozen source, receipt, index and exact legacy freeze-delete')
        _check(lookup[FROZEN]['after'] == lookup['state.json']['before'], 'Frozen legacy bytes do not match source')
    expected_order = sorted(entries, key=lambda entry: (2 if entry['operation'] in _DELETIONS else 1 if entry['path'] == 'IDEAS.md' else 0, entry['path']))
    _check(entries == expected_order, 'Transaction publication order is invalid')
    return value


def _tombstone_witness(entries, paths):
    found = [e for e in entries if _TOMBSTONE.fullmatch(e['path']) and e['operation'] == 'write' and e['after'] is not None]
    return found[0] if len(found) == 1 and len([p for p in paths if _TOMBSTONE.fullmatch(p)]) == 1 else None


def _check_pointer_publish(raw, path, before):
    try:
        _check_pointer(raw, path, before)
    except IdeaError as exc:
        raise IdeaError('invalid_input', str(exc)) from exc


def _staged_path(root, tx, n, kind):
    return _safe(root, JOURNAL + '/' + tx + '/' + str(n) + '.' + kind)


def _check_contents(root, tx, value, *, require_stages):
    """Preflight every entry before touching any target; never partial-conflict repair."""
    allowed_names = {'manifest.json'}
    for n, entry in enumerate(value['entries']):
        for kind in ('before','after'):
            if entry[kind] is not None:
                allowed_names.add(str(n) + '.' + kind)
                path = _staged_path(root, tx, n, kind)
                raw = _read(path)
                if require_stages:
                    _check(raw is not None and len(raw) == entry[kind + '_size'] and digest(raw) == entry[kind],
                           'Missing/corrupt recovery ' + kind + '-image: ' + entry['path'], transaction_id=tx, path=entry['path'])
        target = _safe(root, entry['path'])
        current = _read(target)
        hashed = None if current is None else digest(current)
        permitted = (entry['after'],) if value['phase'] == 'complete' else (entry['before'], entry['after'])
        _check(hashed in permitted, 'External edit conflicts with recovery: ' + entry['path'], transaction_id=tx, path=entry['path'])
    folder = _safe(root, JOURNAL + '/' + tx, directory=True)
    _check(all(p.name in allowed_names for p in folder.iterdir()), 'Unknown file in transaction journal; preserved', transaction_id=tx)


def _cleanup(root, tx, value, callback):
    """Only manifest-declared owned stages; never recursive deletion."""
    grade = None
    folder = _safe(root, JOURNAL + '/' + tx, directory=True)
    names = []
    for n, entry in enumerate(value['entries']):
        names.extend(str(n) + '.' + kind for kind in ('before','after') if entry[kind] is not None)
    _check(all(p.name in set(names) | {'manifest.json'} for p in folder.iterdir()), 'Unknown transaction file prevents cleanup; preserved')
    for name in names:
        path = _safe(root, JOURNAL + '/' + tx + '/' + name)
        if path.exists():
            path.unlink()
            grade = _grade(grade, sync_directory(folder))
            _signal(callback, 'cleaned:' + name)
    manifest = _safe(root, JOURNAL + '/' + tx + '/manifest.json')
    if manifest.exists():
        manifest.unlink()
        grade = _grade(grade, sync_directory(folder))
        _signal(callback, 'cleaned:manifest.json')
    folder.rmdir()
    grade = _grade(grade, sync_directory(folder.parent))
    _signal(callback, 'cleanup_done')
    return grade


def _target_barriers(root, entries):
    """Retry all data/directory barriers, even for already-published after-images.

    A prior replacement may have succeeded while its sync failed. Hash equality
    alone cannot upgrade that uncertainty to a stronger durability grade.
    """
    grade = None
    for entry in entries:
        path = _safe(root, entry['path'])
        if entry['operation'] == 'write':
            with path.open('rb') as stream:
                os.fsync(stream.fileno())
        directory = path.parent
        while True:
            grade = _grade(grade, sync_directory(directory))
            if directory == root:
                break
            directory = directory.parent
    return grade


def _roll_forward(root, tx, value, callback):
    grade = None
    was_complete = value['phase'] == 'complete'
    _check_contents(root, tx, value, require_stages=value['phase'] == 'prepared')
    if value['phase'] == 'prepared':
        for n, entry in enumerate(value['entries']):
            target = _safe(root, entry['path'])
            raw = _read(target)
            hashed = None if raw is None else digest(raw)
            _check(hashed in (entry['before'], entry['after']), 'Target changed during publication: ' + entry['path'], path=entry['path'])
            if hashed != entry['after']:
                if entry['operation'] in _DELETIONS:
                    # Verify the exact frozen copy, receipt and published index
                    # again at the destructive boundary; no guessed rollback.
                    lookup = {e['path']:e for e in value['entries']}
                    move = entry['operation'] == 'move-out'
                    if entry['operation'] == 'remove':
                        guards = (_tombstone_witness(value['entries'], set(lookup))['path'], 'IDEAS.md')
                    else:
                        guards = (_pointer_path(entry['path']), 'IDEAS.md') if move else (FROZEN, RECEIPT, 'IDEAS.md')
                    for key in guards:
                        actual = _read(_safe(root, key))
                        _check(actual is not None and digest(actual) == lookup[key]['after'], 'Migration prerequisite changed: ' + key)
                        if move and key != 'IDEAS.md':
                            _check_pointer(actual, entry['path'], entry['before'])
                    target.unlink()
                    grade = _grade(grade, sync_directory(target.parent))
                else:
                    staged = _read(_staged_path(root, tx, n, 'after'))
                    _check(staged is not None and digest(staged) == entry['after'], 'After-image changed during publication')
                    grade = _grade(grade, _write(target, staged, immutable=entry['immutable'], seal=_TOMBSTONE.fullmatch(entry['path']) is not None))
                _signal(callback, 'published:' + entry['path'])
        # Verify targets and all recovery bytes before recording completion.
        _check_contents(root, tx, value, require_stages=True)
        for entry in value['entries']:
            actual = _read(_safe(root, entry['path']))
            _check((None if actual is None else digest(actual)) == entry['after'], 'Published target failed verification')
        grade = _grade(grade, _target_barriers(root, value['entries']))
        _signal(callback, 'verified')
        value = dict(value, phase='complete')
        grade = _grade(grade, _manifest(root, tx, value))
        _signal(callback, 'complete')
    if was_complete:
        grade = _grade(grade, _target_barriers(root, value['entries']))
    return _grade(grade, _cleanup(root, tx, value, callback))


def recover(root, *, _checkpoint=None):
    """Recover under caller-held store lock; return WriteResult or named conflict.

    Staging-phase journals were never prepared and are safely abandoned using
    their exact declared filenames. Empty pre-manifest directories are removed;
    unknown contents without a manifest are preserved and reported as conflict.
    Store must exclude this reserved directory from uninitialized-data checks.
    """
    root = _root(root)
    journals = _safe(root, JOURNAL, directory=True)
    if not journals.exists():
        return WriteResult('not-published')
    folders = []
    try:
        for folder in journals.iterdir():
            folders.append(folder)
            _check(len(folders) <= MAX_JOURNALS, 'Recovery journal count exceeds limit')
    except OSError as exc:
        return WriteResult('uncertain', UNCERTAIN, exc)
    grade, committed = None, False
    records, total, active = [], 0, 0
    # Validate all journal metadata/aggregate limits before publication or cleanup.
    try:
        for folder in sorted(folders):
            _check(_TX.fullmatch(folder.name) is not None, 'Unknown recovery journal directory; preserved')
            _safe(root, JOURNAL + '/' + folder.name, directory=True)
            raw = _read(_safe(root, JOURNAL + '/' + folder.name + '/manifest.json'), MAX_MANIFEST)
            if raw is None:
                _check(not any(folder.iterdir()), 'Unprepared journal contains unknown files; preserved', transaction_id=folder.name)
                records.append((folder, None))
                continue
            try:
                value = _validate_manifest(decode(raw), folder.name)
            except IdeaError as exc:
                if exc.code == 'recovery_conflict':
                    raise
                _conflict('Malformed recovery manifest: ' + str(exc), transaction_id=folder.name)
            total += sum(entry['before_size'] + entry['after_size'] for entry in value['entries'])
            active += value['phase'] in ('prepared', 'complete')
            _check(total <= MAX_JOURNAL_BYTES and active <= 1, 'Aggregate recovery bytes/active transactions exceed limit')
            records.append((folder, value))
    except OSError as exc:
        # An unreadable journal may already be prepared/published; fail with
        # uncertainty instead of claiming the operation did not happen.
        return WriteResult('uncertain', UNCERTAIN, exc)
    try:
        for folder, value in records:
            if value is None:
                folder.rmdir()
                grade = _grade(grade, sync_directory(journals))
            elif value['phase'] == 'staging':
                # No target has been changed, so do not compare user files here.
                grade = _grade(grade, _cleanup(root, folder.name, value, _checkpoint))
            else:
                committed = True
                try:
                    grade = _grade(grade, _roll_forward(root, folder.name, value, _checkpoint))
                except IdeaError as exc:
                    exc.details.update(committed=True, publication='uncertain', durability=UNCERTAIN)
                    raise
        return WriteResult('published' if committed else 'not-published', grade)
    except OSError as exc:
        return WriteResult('uncertain' if committed else 'not-published', UNCERTAIN if committed else grade, exc)


def publish(root, changes, expected, *, freeze_legacy=False, legacy_sha256=None, move_out=None, removals=None, _checkpoint=None):
    """Stage and publish complete after-images under caller-held store lock.

    expected supplies every changed path's prior SHA-256 or None if absent.
    Evidence is immutable. A migration must additionally supply exact frozen
    state bytes + receipt + IDEAS.md, and an expected legacy source digest.
    state.json is removable only through migration. move_out names one idea
    detail file to remove after its pointer (a change) and IDEAS.md are
    published; the pointer must record that file's exact SHA-256. removals
    names the files of one removed idea; the same call must write that idea's
    tombstone and IDEAS.md, which are published first.
    On a prepared I/O error, publication is uncertain: reread/recover, never
    assume no change or retry blindly. Callback exceptions are test-only.
    """
    root = _root(root)
    recovered = recover(root)
    if recovered.error is not None:
        return recovered
    require(type(changes) is dict and type(expected) is dict and set(changes) == set(expected), 'Expected hashes must cover exactly the changed paths')
    require(0 < len(changes) <= MAX_ENTRIES and type(freeze_legacy) is bool, 'Invalid transaction size/migration flag')
    require(not freeze_legacy or (_hash(legacy_sha256) and legacy_sha256 is not None and {FROZEN, RECEIPT, 'IDEAS.md'} <= set(changes)), 'Migration requires exact legacy hash, frozen copy, receipt and index')
    require(freeze_legacy or legacy_sha256 is None, 'Legacy hash is only accepted for migration')
    require(move_out is None or (not freeze_legacy and type(move_out) is str and _IDEA_DETAIL.fullmatch(move_out) is not None
                                 and move_out not in changes and _pointer_path(move_out) in changes), 'move-out needs an idea detail path and its pointer, never with migration')
    require(removals is None or (not freeze_legacy and move_out is None and type(removals) in (list, tuple) and 0 < len(removals) < MAX_ENTRIES
                                 and len(set(removals)) == len(removals) and not set(removals) & set(changes)
                                 and 'IDEAS.md' in changes and sum(1 for p in changes if _TOMBSTONE.fullmatch(p)) == 1
                                 and all(type(p) is str and p != 'IDEAS.md' and _allowed(p) for p in removals)),
            'removals need distinct reserved paths, one tombstone and the index, never with migration or move-out')
    entries, copies = [], {}
    for path, after in changes.items():
        require(_allowed(path, freeze_legacy), 'Unknown transaction target: ' + str(path))
        require(type(after) is bytes and len(after) <= _limit(path) and _hash(expected[path]), 'Invalid after-image or expected hash')
        before = _read(_safe(root, path), _limit(path))
        before_hash = None if before is None else digest(before)
        if before_hash != expected[path]:
            raise IdeaError('save_conflict', 'Observed file changed before publication: ' + path, path=path)
        after_hash = digest(after)
        immutable = not _mutable(path)
        if immutable and before_hash not in (None, after_hash):
            raise IdeaError('save_conflict', 'Immutable evidence cannot be overwritten: ' + path, path=path)
        entries.append(dict(path=path, before=before_hash, after=after_hash, before_size=0 if before is None else len(before), after_size=len(after), operation='write', immutable=immutable))
        copies[path] = (before, after)
    if freeze_legacy:
        raw = _read(_safe(root, 'state.json'))
        require(raw is not None and digest(raw) == legacy_sha256 and digest(changes[FROZEN]) == legacy_sha256, 'Legacy source does not match exact frozen copy', 'save_conflict')
        entries.append(dict(path='state.json',before=legacy_sha256,after=None,before_size=len(raw),after_size=0,operation='freeze-delete',immutable=False))
        copies['state.json'] = (raw, None)
    if move_out is not None:
        raw = _read(_safe(root, move_out))
        require(raw is not None, 'move-out source is missing', 'save_conflict')
        _check_pointer_publish(changes[_pointer_path(move_out)], move_out, digest(raw))
        entries.append(dict(path=move_out,before=digest(raw),after=None,before_size=len(raw),after_size=0,operation='move-out',immutable=False))
        copies[move_out] = (raw, None)
    for path in removals or ():
        raw = _read(_safe(root, path), _limit(path))
        require(raw is not None, 'removal source is missing: ' + path, 'save_conflict')
        entries.append(dict(path=path,before=digest(raw),after=None,before_size=len(raw),after_size=0,operation='remove',immutable=False))
        copies[path] = (raw, None)
    entries.sort(key=lambda entry: (2 if entry['operation'] in _DELETIONS else 1 if entry['path'] == 'IDEAS.md' else 0, entry['path']))
    tx = 'txn_' + uuid.uuid4().hex
    value = dict(schema_version=1,transaction_id=tx,phase='staging',migration=freeze_legacy,entries=entries)
    _validate_manifest(value, tx)
    # All unchanged after-images can be skipped by Store; this module preserves
    # the supplied transaction, especially required migration prerequisites.
    grade, prepared = None, False
    try:
        folder = _safe(root, JOURNAL + '/' + tx, directory=True)
        shared = is_shared_root(root)
        # Parent first, each new folder created at its final mode (never other-reachable, even for an instant).
        for directory in (folder.parent, folder):
            if not make_directory(directory, shared):
                _heal_directory(directory, shared)
        grade = _grade(grade, sync_directory(root))
        grade = _grade(grade, sync_directory(folder.parent))
        # Use a different local callback name from the helper to avoid shadowing.
        callback = _checkpoint
        if callback is not None:
            callback('journal_created')
        grade = _grade(grade, _manifest(root, tx, value))
        if callback is not None:
            callback('staging_manifest')
        for n, entry in enumerate(entries):
            before, after = copies[entry['path']]
            for kind, raw in (('before',before), ('after',after)):
                if raw is not None:
                    grade = _grade(grade, _write(_staged_path(root, tx, n, kind), raw, immutable=True))
                    if callback is not None:
                        callback('staged:' + str(n) + '.' + kind)
        # Check target hashes and all stages after staging, before prepare.
        _check_contents(root, tx, value, require_stages=True)
        value = dict(value, phase='prepared')
        # Once prepare begins its replacement may have occurred even if sync
        # fails. Conservatively flag commitment before this atomic write.
        prepared = True
        grade = _grade(grade, _manifest(root, tx, value))
        if callback is not None:
            callback('prepared')
        grade = _grade(grade, _roll_forward(root, tx, value, callback))
        return WriteResult('published', grade)
    except IdeaError as exc:
        if prepared:
            exc.details.update(committed=True, publication='uncertain', durability=UNCERTAIN)
        raise
    except OSError as exc:
        return WriteResult('uncertain' if prepared else 'not-published', UNCERTAIN if prepared else grade, exc)
