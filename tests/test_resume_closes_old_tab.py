"""Orca resume closes the binding's previous browser page. Fake runner only; never the real orca."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'
sys.path.insert(0, str(SCRIPTS))
import idea_launch as launch
from idea_native import OrcaBinding
from idea_runtime import Runtime

WORKTREE = '12345678-1234-1234-1234-123456789abc::/fixture/project'
BINDING = OrcaBinding(WORKTREE, 'term_fixture', None)


class FakeOrca:
    def __init__(self, close_fails=False):
        self.calls = []; self.n = 0; self.close_fails = close_fails

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        verb = argv[1:3]
        if verb == ['terminal', 'list']:
            result = {'terminals': [{'handle': 'term_fixture', 'worktreeId': WORKTREE, 'connected': True, 'orphaned': False}]}
        elif verb == ['tab', 'create']:
            self.n += 1; result = {'browserPageId': f'page-{self.n}'}
        elif verb == ['tab', 'close']:
            if self.close_fails:
                return SimpleNamespace(returncode=1, stdout=json.dumps({'ok': False, 'error': {'code': 'boom'}}), stderr='')
            result = {'closed': True}
        else:
            raise AssertionError(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True, 'result': result}), stderr='')


@unittest.skipUnless(os.name == 'posix', 'owner ACLs are posix only')
class ResumeClosesOldTab(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name) / 'ideas'; self.root = Path(self.temp.name) / 'private'
        self.client = Runtime(self.store, self.root)
        self.children = []; self.lock = threading.Lock(); self.addCleanup(self.cleanup)

    def spawn(self, *a, **k):
        child = subprocess.Popen(*a, **k)
        with self.lock: self.children.append(child)
        return child

    def cleanup(self):
        end = time.monotonic() + 8
        while any(c.poll() is None for c in self.children) and time.monotonic() < end:
            try: self.client.request_owned_stop(timeout=3)
            except Exception: time.sleep(.05)
        for c in self.children: c.wait(timeout=5)

    def open(self, runner, **kw):
        return launch.open_browser_session(self.store, self.root, mode='orca', orcabinding=BINDING,
                                           runner=runner, spawn=self.spawn, **kw)

    def test_resume_creates_then_closes_recorded_page(self):
        runner = FakeOrca()
        first = self.open(runner)
        self.assertNotIn('previous_page_closed', first['browser'])
        self.assertEqual([c[1:3] for c in runner.calls if c[1] == 'tab'], [['tab', 'create']])
        second = self.open(runner, binding_id=first['binding_id'])
        tabs = [c for c in runner.calls if c[1] == 'tab']
        self.assertEqual([c[2] for c in tabs], ['create', 'create', 'close'])
        close = tabs[-1]
        self.assertEqual(close[close.index('--page') + 1], first['browser']['browser_page_id'])
        self.assertEqual(close[close.index('--worktree') + 1], 'id:' + WORKTREE)
        self.assertTrue(second['browser']['previous_page_closed'])
        self.assertEqual(second['browser']['browser_page_id'], 'page-2')
        record = self.root / 'orca-pages.json'
        self.assertEqual(json.loads(record.read_text())[first['binding_id']], 'page-2')
        self.assertEqual(record.stat().st_mode & 0o777, 0o600)

    def test_close_failure_keeps_resume_ok(self):
        first = self.open(FakeOrca())
        failing = FakeOrca(close_fails=True); failing.n = 5
        second = self.open(failing, binding_id=first['binding_id'])
        self.assertFalse(second['browser']['previous_page_closed'])
        self.assertEqual(second['browser']['previous_page_close_error'], 'orca_error')
        self.assertEqual(second['binding_id'], first['binding_id'])

    def test_resume_without_record_closes_nothing(self):
        runner = FakeOrca()
        first = self.open(runner)
        (self.root / 'orca-pages.json').unlink()
        runner.calls.clear()
        out = self.open(runner, binding_id=first['binding_id'])
        self.assertFalse([c for c in runner.calls if c[1:3] == ['tab', 'close']])
        self.assertNotIn('previous_page_closed', out['browser'])


if __name__ == '__main__':
    unittest.main()
