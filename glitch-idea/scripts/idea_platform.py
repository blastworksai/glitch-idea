"""Private local-filesystem boundary.

Only cooperating local processes are supported; network/cloud-synced stores are
not qualified. Windows directory durability and native Windows/macOS behavior
require separate qualification. Successful fsync is not a power-loss warranty.
Callers close readers before replacing files (particularly on Windows).
"""
from contextlib import contextmanager
from dataclasses import dataclass
import errno
import math
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import weakref

from idea_domain import IdeaError

if os.name == 'nt':
    import msvcrt
    fcntl = None
else:
    import fcntl
    msvcrt = None

FILE_AND_DIRECTORY_SYNCED = 'file-and-directory-synced'
FILE_SYNCED_PROCESS_RECOVERY = 'file-synced-process-recovery'
UNCERTAIN = 'uncertain'
LOCK_OFFSET = 4096  # Past EOF is legal; never seed an empty initialization file.
_WINDOWS = os.name == 'nt'
_mutex_guard = threading.Lock()
_mutexes = weakref.WeakValueDictionary()


def _after_fork():
    global _mutex_guard, _mutexes
    _mutex_guard = threading.Lock()
    _mutexes = weakref.WeakValueDictionary()


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_after_fork)


SHARED_FILE_MODE = 0o660
SHARED_DIR_MODE = 0o2770
SHARED_SEAL_MODE = 0o440


def is_shared_root(root):
    """A store root that is setgid and group-writable is a shared-group store.

    Measured on each call, so it is deterministic and follows the root's own mode.
    Every other root stays owner-only.
    """
    if _WINDOWS or root is None:
        return False
    try:
        mode = os.stat(root).st_mode
    except OSError:
        return False
    return stat.S_ISDIR(mode) and bool(mode & stat.S_ISGID) and bool(mode & stat.S_IWGRP)


def store_root_of(path):
    """The nearest ancestor holding the store's .lock, or None."""
    for ancestor in Path(path).absolute().parents:
        if (ancestor / '.lock').is_file():
            return ancestor
    return None


def share_file(descriptor, shared):
    """fchmod a file we own to 0660 on its descriptor when the store is shared."""
    if shared and not _WINDOWS and os.fstat(descriptor).st_uid == os.geteuid():
        os.fchmod(descriptor, SHARED_FILE_MODE)


def share_directory(path, shared):
    """fchmod a directory we own to 2770 when the store is shared, through a descriptor that never follows a link.

    A symlink at the path, a path that is gone, or a directory owned by someone else is left exactly as it is.
    """
    if not shared or _WINDOWS:
        return
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    except OSError:
        return
    try:
        info = os.fstat(descriptor)
        if stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) != SHARED_DIR_MODE:
            os.fchmod(descriptor, SHARED_DIR_MODE)
    finally:
        os.close(descriptor)


def make_directory(path, shared):
    """Create one directory, owner-only (0700), or in a shared store group-only (2770 before the umask); never other-reachable.

    An existing entry is left alone (the caller decides what it is). The final mode is set on a descriptor.
    """
    mode = SHARED_DIR_MODE if shared and not _WINDOWS else 0o700
    try:
        os.mkdir(path, mode)
    except FileExistsError:
        return False
    share_directory(path, shared)
    return True


@dataclass(frozen=True)
class WriteResult:
    """Publication is independent of durability; errors never imply rollback."""
    publication: str
    durability: str | None = None
    error: OSError | None = None

    @property
    def committed(self):
        return self.publication in ('published', 'uncertain')

    def raise_for_error(self):
        if self.error is not None:
            if self.committed:
                raise IdeaError('durability_uncertain',
                                'Publication occurred or is uncertain; reconcile before retry: '
                                + str(self.error), committed=True,
                                publication=self.publication,
                                durability=self.durability) from self.error
            raise self.error
        return self


def sync_directory(path):
    """Return the observed barrier grade, or raise; Windows has no such barrier.

    The Windows grade is deliberately weaker, not a silently successful fsync.
    POSIX filesystem errors (including unsupported fsync) are not suppressed.
    """
    if _WINDOWS:
        return FILE_SYNCED_PROCESS_RECOVERY
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return FILE_AND_DIRECTORY_SYNCED


def _contended(exc):
    return exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) or (
        _WINDOWS and getattr(exc, 'winerror', None) in (33, 36))


def _acquire(stream):
    if _WINDOWS:
        stream.seek(LOCK_OFFSET)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release(stream):
    if _WINDOWS:
        stream.seek(LOCK_OFFSET)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def store_lock(path, timeout=5.0):
    """Bounded exclusive thread + process lock yielding a transaction-local file.

    Parent must exist. Both reads and writes lock exclusively; nesting the same
    path is intentionally non-reentrant and times out. No initialization bytes
    are written. The deadline covers both locks, not two separate timeouts.
    """
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0:
        raise ValueError('Lock timeout must be a finite nonnegative number')
    path = Path(path).absolute()
    key = os.path.normcase(str(path.resolve()))
    with _mutex_guard:
        mutex = _mutexes.get(key)
        if mutex is None:
            mutex = threading.Lock()
            _mutexes[key] = mutex
    deadline = time.monotonic() + timeout
    if not mutex.acquire(timeout=max(0, deadline - time.monotonic())):
        raise IdeaError('store_busy', 'Timed out waiting for store lock')
    try:
        with path.open('a+b') as stream:
            share_file(stream.fileno(), is_shared_root(path.parent))
            while True:
                try:
                    _acquire(stream)
                    break
                except OSError as exc:
                    if not _contended(exc):
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise IdeaError('store_busy', 'Timed out waiting for store lock') from exc
                    time.sleep(min(0.02, remaining))
            try:
                stream.seek(0)
                yield stream
            finally:
                _release(stream)
    finally:
        mutex.release()


def atomic_write(path, raw, immutable=False, root=None, seal=False):
    """Fsync a complete sibling then replace, or exclusively hard-link it.

    Immutable collision/unsupported hard links fail without replacing anything.
    No fallback writes a partially visible final file. Cleanup errors are also
    explicit. The result requires callers to inspect or call raise_for_error().
    With seal=True the staged file is already read-only (0440 shared, 0400 otherwise) before it is linked,
    so the published inode is never writable.
    """
    if not isinstance(raw, bytes) or type(immutable) is not bool:
        raise TypeError('atomic_write requires bytes and a boolean immutable flag')
    path = Path(path)
    temp = None
    published = False
    grade = None
    error = None
    new_directories = []
    try:
        ancestor = path.parent
        while not ancestor.exists():
            new_directories.append(ancestor)
            ancestor = ancestor.parent
        shared = is_shared_root(root if root is not None else store_root_of(path))
        for directory in reversed(new_directories):
            make_directory(directory, shared)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
        with os.fdopen(descriptor, 'wb') as stream:
            if seal and not _WINDOWS:
                os.fchmod(stream.fileno(), SHARED_SEAL_MODE if shared else 0o400)
            else:
                share_file(stream.fileno(), shared)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            os.link(temp, path)
        else:
            os.replace(temp, path)
        published = True
        if immutable:
            os.unlink(temp)
        temp = None
        grade = sync_directory(path.parent)
        # Persist newly created directory entries too, from leaf toward root.
        for directory in new_directories:
            sync_directory(directory.parent)
    except OSError as exc:
        error = exc
        if published:
            grade = UNCERTAIN
    finally:
        if temp is not None:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass
            except OSError as exc:
                if error is None:
                    error = exc
                if published:
                    grade = UNCERTAIN
    return WriteResult('published' if published else 'not-published', grade, error)


def initialize_marker(stream):
    """Explicitly mark initialization only after authority publication by caller.

    Must run while holding store_lock. Any existing byte already signifies
    initialized; the lock itself must never be used to initialize the store.
    """
    published = False
    try:
        stream.seek(0)
        existing = bool(stream.read(1))
        published = True
        if not existing:
            stream.seek(0)
            # Conservatively flag uncertainty once writing begins, even if it fails.
            stream.write(b'initialized\n')
        stream.flush()
        os.fsync(stream.fileno())
        return WriteResult('published', sync_directory(Path(stream.name).parent))
    except OSError as exc:
        return WriteResult('uncertain' if published else 'not-published',
                           UNCERTAIN if published else None, exc)
