"""real local Linux I/O plus injected Windows semantics, not qualification."""
import errno
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'
sys.path.insert(0, str(SCRIPTS))
import idea_platform as platform
from idea_domain import IdeaError


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'Café idea.md'

    def test_real_replace_complete_bytes_and_durability(self):
        self.path.write_bytes(b'before')
        result = platform.atomic_write(self.path, 'after 💡\r\n'.encode())
        result.raise_for_error()
        self.assertEqual(result.publication, 'published')
        expected = (platform.FILE_SYNCED_PROCESS_RECOVERY if os.name == 'nt'
                    else platform.FILE_AND_DIRECTORY_SYNCED)
        self.assertEqual(result.durability, expected)
        self.assertEqual(self.path.read_bytes(), 'after 💡\r\n'.encode())
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_nested_creation_syncs_all_parent_entries(self):
        path = self.root / 'one' / 'two' / 'idea.md'
        with patch.object(platform, 'sync_directory', wraps=platform.sync_directory) as sync:
            platform.atomic_write(path, b'new').raise_for_error()
        self.assertEqual([c.args[0] for c in sync.call_args_list],
                         [path.parent, path.parent.parent, self.root])

    def test_new_parent_sync_failure_is_committed_uncertain(self):
        path = self.root / 'new' / 'idea.md'
        with patch.object(platform, 'sync_directory', side_effect=[platform.FILE_AND_DIRECTORY_SYNCED, OSError(errno.EIO, 'parent')]):
            result = platform.atomic_write(path, b'new')
        self.assertEqual(path.read_bytes(), b'new')
        self.assertEqual(result.durability, platform.UNCERTAIN)
        self.assertTrue(result.committed)

    def test_replace_failure_preserves_original_and_cleans_stage(self):
        self.path.write_bytes(b'before')
        with patch.object(platform.os, 'replace', side_effect=PermissionError('open reader')):
            result = platform.atomic_write(self.path, b'after')
        self.assertEqual(result.publication, 'not-published')
        self.assertIsNone(result.durability)
        with self.assertRaises(PermissionError):
            result.raise_for_error()
        self.assertEqual(self.path.read_bytes(), b'before')
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_file_sync_failure_prevents_publication(self):
        with patch.object(platform.os, 'fsync', side_effect=OSError(errno.EIO, 'file sync')):
            result = platform.atomic_write(self.path, b'after')
        self.assertEqual(result.publication, 'not-published')
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_directory_sync_failure_retains_published_authority(self):
        with patch.object(platform, 'sync_directory', side_effect=OSError(errno.EIO, 'directory sync')):
            result = platform.atomic_write(self.path, b'after')
        self.assertEqual(result.publication, 'published')
        self.assertEqual(result.durability, 'uncertain')
        self.assertEqual(self.path.read_bytes(), b'after')
        with self.assertRaises(IdeaError) as caught:
            result.raise_for_error()
        self.assertEqual(caught.exception.code, 'durability_uncertain')
        self.assertTrue(caught.exception.details['committed'])

    def test_immutable_collision_never_overwrites_even_equal_bytes(self):
        platform.atomic_write(self.path, b'original', immutable=True).raise_for_error()
        for raw in (b'original', b'changed'):
            result = platform.atomic_write(self.path, raw, immutable=True)
            self.assertEqual(result.publication, 'not-published')
            self.assertIsInstance(result.error, FileExistsError)
            self.assertEqual(self.path.read_bytes(), b'original')
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_immutable_directory_sync_failure_is_committed(self):
        with patch.object(platform, 'sync_directory', side_effect=OSError(errno.EIO, 'directory')):
            result = platform.atomic_write(self.path, b'evidence', immutable=True)
        self.assertTrue(result.committed)
        self.assertEqual(result.durability, platform.UNCERTAIN)
        self.assertEqual(self.path.read_bytes(), b'evidence')

    def test_unsupported_hardlinks_fail_explicitly(self):
        with patch.object(platform.os, 'link', side_effect=OSError(errno.ENOTSUP, 'no hardlinks')):
            result = platform.atomic_write(self.path, b'evidence', immutable=True)
        self.assertEqual(result.publication, 'not-published')
        self.assertEqual(result.error.errno, errno.ENOTSUP)
        self.assertFalse(self.path.exists())

    def test_invalid_write_inputs_are_programmer_errors(self):
        with self.assertRaises(TypeError):
            platform.atomic_write(self.path, 'not bytes')
        with self.assertRaises(TypeError):
            platform.atomic_write(self.path, b'bytes', immutable=1)

    def test_lock_does_not_initialize_and_marker_is_explicit(self):
        lock = self.root / '.lock'
        with platform.store_lock(lock) as stream:
            self.assertEqual(lock.read_bytes(), b'')
            platform.initialize_marker(stream).raise_for_error()
        self.assertEqual(lock.read_bytes(), b'initialized\n')
        with platform.store_lock(lock) as stream:
            platform.initialize_marker(stream).raise_for_error()
        self.assertEqual(lock.read_bytes(), b'initialized\n')

    def test_existing_any_byte_marker_is_preserved(self):
        lock = self.root / '.lock'
        lock.write_bytes(b'x')
        with platform.store_lock(lock) as stream:
            platform.initialize_marker(stream).raise_for_error()
        self.assertEqual(lock.read_bytes(), b'x')

    def test_marker_sync_failure_is_uncertain(self):
        with platform.store_lock(self.root / '.lock') as stream:
            with patch.object(platform.os, 'fsync', side_effect=OSError(errno.EIO, 'marker')):
                result = platform.initialize_marker(stream)
        self.assertEqual(result.publication, 'uncertain')
        self.assertEqual(result.durability, 'uncertain')
        self.assertTrue(result.committed)

    def test_thread_contention_timeout_and_release_after_exception(self):
        lock = self.root / '.lock'
        errors = []
        def attempt():
            try:
                with platform.store_lock(lock, timeout=0.06):
                    errors.append('unexpected acquisition')
            except IdeaError as exc:
                errors.append(exc.code)
        with self.assertRaisesRegex(RuntimeError, 'body'):
            with platform.store_lock(lock):
                thread = threading.Thread(target=attempt)
                thread.start()
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, ['store_busy'])
                raise RuntimeError('body')
        with platform.store_lock(lock, timeout=0):
            pass

    def test_invalid_timeout_rejected(self):
        for timeout in (-1, float('nan'), float('inf'), True, '1'):
            with self.assertRaises(ValueError):
                with platform.store_lock(self.root / '.lock', timeout):
                    pass

    def test_real_process_contention_and_crash_releases_lock(self):
        lock = self.root / '.lock'
        code = ('import sys,time;sys.path.insert(0,sys.argv[1]);'
                'from idea_platform import store_lock;'
                '\nwith store_lock(sys.argv[2]):\n print("locked",flush=True)\n time.sleep(30)\n')
        child = subprocess.Popen([sys.executable, '-c', code, str(SCRIPTS), str(lock)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'locked')
            start = time.monotonic()
            with self.assertRaises(IdeaError) as caught:
                with platform.store_lock(lock, timeout=0.08):
                    pass
            self.assertEqual(caught.exception.code, 'store_busy')
            self.assertLess(time.monotonic() - start, 1)
            child.kill()
            child.wait(timeout=3)
            with platform.store_lock(lock, timeout=0.5):
                pass
            self.assertEqual(lock.read_bytes(), b'')
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=3)

    def test_windows_fixed_byte_acquire_unlock_seek_without_seeding(self):
        calls = []
        fake = types.SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2)
        def locking(fd, mode, length):
            calls.append((mode, length, os.lseek(fd, 0, os.SEEK_CUR)))
        fake.locking = locking
        lock = self.root / '.lock'
        with patch.object(platform, '_WINDOWS', True), patch.object(platform, 'msvcrt', fake):
            with platform.store_lock(lock) as stream:
                stream.seek(17)  # Release must not use the caller's cursor.
        self.assertEqual(calls, [(1, 1, platform.LOCK_OFFSET), (2, 1, platform.LOCK_OFFSET)])
        self.assertEqual(lock.read_bytes(), b'')

    def test_windows_contention_times_out_but_io_errors_are_not_retried(self):
        fake = types.SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2)
        fake.locking = lambda *args: (_ for _ in ()).throw(OSError(errno.EACCES, 'locked'))
        with patch.object(platform, '_WINDOWS', True), patch.object(platform, 'msvcrt', fake):
            with self.assertRaises(IdeaError) as caught:
                with platform.store_lock(self.root / '.lock', timeout=0.02):
                    pass
            self.assertEqual(caught.exception.code, 'store_busy')
            fake.locking = lambda *args: (_ for _ in ()).throw(OSError(errno.EIO, 'bad drive'))
            with self.assertRaises(OSError) as caught:
                with platform.store_lock(self.root / '.lock', timeout=0.02):
                    pass
            self.assertEqual(caught.exception.errno, errno.EIO)

    def test_windows_reports_weaker_directory_grade(self):
        with patch.object(platform, '_WINDOWS', True), patch.object(platform.os, 'fsync', wraps=os.fsync) as sync:
            result = platform.atomic_write(self.path, b'windows-fixture')
        result.raise_for_error()
        self.assertEqual(result.durability, platform.FILE_SYNCED_PROCESS_RECOVERY)
        self.assertEqual(sync.call_count, 1)  # File only; no fictitious directory fsync.

    def test_windows_import_never_imports_fcntl(self):
        fake = types.SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=lambda *args: None)
        spec = importlib.util.spec_from_file_location('_windows_platform_fixture', SCRIPTS / 'idea_platform.py')
        module = importlib.util.module_from_spec(spec)
        import builtins
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name == 'fcntl':
                raise AssertionError('Windows must not import fcntl')
            if name == 'msvcrt':
                return fake
            return original(name, *args, **kwargs)
        with patch.dict(sys.modules, {spec.name: module}), patch.object(platform.os, 'name', 'nt'), patch.object(builtins, '__import__', guarded):
            spec.loader.exec_module(module)
        self.assertTrue(module._WINDOWS)
        self.assertIs(module.msvcrt, fake)
        self.assertIsNone(module.fcntl)


if __name__ == '__main__':
    unittest.main()
