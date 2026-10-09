"""The Orca page record drops the entry of a binding that is no longer in the session store. Fake runner only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import idea_launch as launch
from idea_runtime import Runtime
from test_resume_closes_old_tab import BINDING, FakeOrca


@unittest.skipUnless(os.name == 'posix', 'owner ACLs are posix only')
class PagesPrune(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name) / 'ideas'; self.root = Path(self.temp.name) / 'private'
        self.client = Runtime(self.store, self.root)
        self.children = []; self.lock = threading.Lock(); self.addCleanup(self.cleanup)
        self.runner = FakeOrca()

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

    def open(self, **kw):
        return launch.open_browser_session(self.store, self.root, mode='orca', orcabinding=BINDING,
                                           runner=self.runner, spawn=self.spawn, **kw)

    def record(self):
        return json.loads((self.root / 'orca-pages.json').read_text())

    def test_discarded_binding_entry_gone_after_next_record(self):
        gone = self.open(); kept = self.open()
        self.assertEqual(set(self.record()), {gone['binding_id'], kept['binding_id']})
        self.client.discard_binding(gone['binding_id'], confirm=True)
        newest = self.open()
        self.assertEqual(set(self.record()), {kept['binding_id'], newest['binding_id']})
        self.assertEqual((self.root / 'orca-pages.json').stat().st_mode & 0o777, 0o600)

    def test_resume_keeps_live_entries(self):
        first = self.open(); other = self.open()
        self.open(binding_id=first['binding_id'])
        self.assertEqual(set(self.record()), {first['binding_id'], other['binding_id']})

    def test_record_drops_absent_ids_and_keeps_the_written_one(self):
        self.root.mkdir(mode=0o700)
        (self.root / 'orca-pages.json').write_text(json.dumps({'binding_a': 'p1', 'binding_b': 'p2'}))
        launch._record_page(self.root, 'binding_c', 'p3', live={'binding_b'})
        self.assertEqual(self.record(), {'binding_b': 'p2', 'binding_c': 'p3'})
        self.assertEqual((self.root / 'orca-pages.json').stat().st_mode & 0o777, 0o600)

    def test_unknown_live_set_prunes_nothing_and_corrupt_file_resets(self):
        self.root.mkdir(mode=0o700)
        (self.root / 'orca-pages.json').write_text(json.dumps({'binding_a': 'p1'}))
        launch._record_page(self.root, 'binding_c', 'p3', live=None)
        self.assertEqual(self.record(), {'binding_a': 'p1', 'binding_c': 'p3'})
        (self.root / 'orca-pages.json').write_text('not json')
        launch._record_page(self.root, 'binding_d', 'p4', live={'binding_d'})
        self.assertEqual(self.record(), {'binding_d': 'p4'})

    def test_parallel_launch_entry_survives_a_stale_live_snapshot(self):
        late = {}
        original = launch._live_bindings
        def racing(store, root):
            snapshot = original(store, root)  # A's live set is computed here ...
            if not late:  # ... and a second launch records its page before A writes.
                late['t'] = threading.Thread(target=launch._record_page, args=(root, 'binding_B', 'pB'))
                late['t'].start(); late['t'].join(1)
            return snapshot
        launch._live_bindings = racing
        try: first = self.open()
        finally: launch._live_bindings = original
        late['t'].join(8)
        self.assertEqual(set(self.record()), {first['binding_id'], 'binding_B'})

    def test_concurrent_records_keep_every_entry(self):
        self.root.mkdir(mode=0o700)
        ids = [f'binding_{n}' for n in range(24)]
        gate = threading.Barrier(len(ids))
        def work(name):
            gate.wait(); launch._record_page(self.root, name, 'p_' + name)
        threads = [threading.Thread(target=work, args=(name,)) for name in ids]
        for t in threads: t.start()
        for t in threads: t.join(15)
        self.assertEqual(set(self.record()), set(ids))

    def test_lock_failure_writes_the_entry_and_drops_nothing(self):
        self.root.mkdir(mode=0o700)
        (self.root / 'orca-pages.json').write_text(json.dumps({'binding_a': 'p1'}))
        (self.root / '.orca-pages.lock').symlink_to(self.root / 'elsewhere')  # O_NOFOLLOW refuses it
        launch._record_page(self.root, 'binding_c', 'p3', live=lambda: set())
        self.assertEqual(self.record(), {'binding_a': 'p1', 'binding_c': 'p3'})


if __name__ == '__main__':
    unittest.main()
