"""J6e: the Store reuses an unchanged markdown load; different bytes always get the full check."""
import copy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
import idea_markdown as md
import idea_store as storage
from idea_domain import IdeaError
from test_external_edits import KEY, ExternalEditTests
import test_handoff_store, test_asset_store, test_proposal_store


def cold(root,observer='observing-session'):
    with storage.Store(root,observer=observer).transaction() as state:
        return copy.deepcopy(state)


# IDEAS.md is decoded once per transaction by _migration_evidence, outside the cached load.
HIT = 1


class Counting:
    def __init__(self):
        self.calls = 0
        self.real = md.parse_document
    def __call__(self,*args,**kwargs):
        self.calls += 1
        return self.real(*args,**kwargs)


class LoadCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'ideas'
        helper = ExternalEditTests('test_legacy_null_inputs_import_once_with_observer_not_declared_editor')
        helper.root = self.root
        helper.store = storage.Store(self.root,observer='observing-session')
        self.helper = helper
        helper.seed(second=True)
        self.store = helper.store

    def read(self,store=None):
        with (store or self.store).transaction() as state:
            return copy.deepcopy(state)

    def parse_count(self):
        counter = Counting()
        with patch.object(md,'parse_document',counter):
            state = self.read()
        return counter.calls,state

    # (5) call-count guard
    def test_hit_skips_parsing_and_miss_parses_every_idea(self):
        first,_ = self.parse_count()
        self.assertGreaterEqual(first,2+HIT)   # miss: every idea (2) plus detail/history parses
        self.assertEqual(self.parse_count()[0],HIT)   # hit: only the index decode in _migration_evidence
        self.assertEqual(self.parse_count()[0],HIT)

    # (1) equals cold, for plain / handoff / asset / proposal stores
    def assert_hit_equals_cold(self,root):
        actor = test_handoff_store.ACTOR
        store = storage.Store(root,observer=actor)
        for _ in range(2):
            with store.transaction(): pass
        counter = Counting()
        with patch.object(md,'parse_document',counter):
            with store.transaction() as hit:
                hit_state = copy.deepcopy(hit)
                receipts = dict(store._contexts.active['proposal_receipts'])
        self.assertEqual(counter.calls,HIT)
        self.assertEqual(hit_state,cold(root,actor))
        return receipts

    def test_hit_equals_cold_plain(self):
        self.read()
        self.assertEqual(self.parse_count()[0],HIT)
        self.assertEqual(self.read(),cold(self.root))

    def test_hit_equals_cold_with_history_and_handoff(self):
        root = Path(self.temp.name)/'handoff'
        test_handoff_store.seed(root)
        self.assertTrue(self.assert_hit_equals_cold(root))

    def test_hit_equals_cold_with_asset(self):
        root = Path(self.temp.name)/'asset'
        test_asset_store.seed(root)
        self.assert_hit_equals_cold(root)

    def test_hit_equals_cold_with_proposal(self):
        root = Path(self.temp.name)/'proposal'
        test_proposal_store.seed(root)
        self.assert_hit_equals_cold(root)

    # (2) hand edit between transactions behaves as a cold store does
    def edit_both(self,change):
        self.read(); self.read()                       # warm the cache
        twin = Path(self.temp.name)/'twin'
        shutil.copytree(self.root,twin)
        for root in (self.root,twin):
            self.helper.root = root; self.helper.edit(change)
        self.helper.root = self.root
        return twin

    def test_normalizing_hand_edit_matches_cold(self):
        twin = self.edit_both(lambda m:m['idea']['ratings'].update(urgency=9))
        warm = self.read()['ideas'][KEY]
        fresh = cold(twin)['ideas'][KEY]
        self.assertEqual(warm['revision'],8)
        for field in ('revision','origin'):
            self.assertEqual(warm.get(field),fresh.get(field))
        self.assertEqual(warm['workflow']['drafts']['priorities']['urgency'],9)
        self.assertEqual(warm['workflow']['drafts'],fresh['workflow']['drafts'])
        self.assertEqual(warm['workflow']['steps'],fresh['workflow']['steps'])

    def test_refused_hand_edit_matches_cold(self):
        def bad(meta): meta['idea']['revision'] = 99
        twin = self.edit_both(bad)
        with self.assertRaises(IdeaError) as cold_error: cold(twin)
        with self.assertRaises(IdeaError) as warm_error: self.read()
        self.assertEqual(warm_error.exception.code,cold_error.exception.code)

    # (3) every kind of difference forces the full path
    def forced(self,store,mutate):
        for _ in range(2):
            with store.transaction(): pass
        mutate()
        counter = Counting()
        with patch.object(md,'parse_document',counter):
            try:
                with store.transaction(): pass
            except IdeaError:
                pass
        self.assertGreater(counter.calls,HIT)

    def test_changed_idea_file_forces_full_path(self):
        path = self.root/(KEY+'.md')
        self.forced(self.store,lambda: path.write_bytes(path.read_bytes()+b'\n'))

    def test_added_file_forces_full_path(self):
        self.forced(self.store,lambda: (self.root/('idea_'+'f'*32+'.md')).write_bytes(b'x'))

    def test_removed_file_forces_full_path(self):
        root = Path(self.temp.name)/'removed'
        test_handoff_store.seed(root)
        store = storage.Store(root,observer=test_handoff_store.ACTOR)
        self.forced(store,lambda: next(p for p in (root/'history').rglob('*.md')).unlink())

    def test_changed_receipt_forces_full_path(self):
        root = Path(self.temp.name)/'receipt'
        test_handoff_store.seed(root)
        store = storage.Store(root,observer=test_handoff_store.ACTOR)
        target = next((root/'session-recovery').glob('*.json'))
        self.forced(store,lambda: target.write_bytes(target.read_bytes()+b' '))

    def test_changed_evidence_file_forces_full_path(self):
        root = Path(self.temp.name)/'evidence'
        test_handoff_store.seed(root)
        store = storage.Store(root,observer=test_handoff_store.ACTOR)
        target = next((root/'history').rglob('*.md'))
        self.forced(store,lambda: target.write_bytes(target.read_bytes()+b' '))

    # (4) no aliasing between callers
    def disk(self,root):
        return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*')
                if p.is_file() and p.name != '.lock' and 'journal' not in p.name}

    def test_mutating_a_yielded_state_does_not_leak(self):
        self.read()
        before = self.disk(self.root)
        with self.store.transaction() as state:
            state['ideas'][KEY]['revision'] = 999
            state['order'].clear()
        calls,again = self.parse_count()
        # A leak would make the next load differ from its imported twin and trigger a normalizing write.
        self.assertEqual(calls,HIT)
        self.assertEqual(again,cold(self.root))
        self.assertEqual(self.disk(self.root),before)

    def test_mutating_context_docs_and_asset_entries_does_not_leak(self):
        root = Path(self.temp.name)/'ctx'
        test_asset_store.seed(root)
        store = storage.Store(root,observer=test_handoff_store.ACTOR)
        with store.transaction(): pass
        with store.transaction():
            context = store._contexts.active
            self.assertTrue(context['docs'] and context['asset_entries'])
            context['docs'].clear(); context['asset_entries'].clear()
        with store.transaction():
            context = store._contexts.active
            self.assertTrue(context['docs'])
            self.assertTrue(context['asset_entries'])

    def test_commit_drops_cache_and_next_read_sees_it(self):
        self.read()
        with self.store.transaction(write=True) as state:
            self.store.commit(state)
        self.assertEqual(self.read(),cold(self.root))


if __name__ == '__main__':
    unittest.main()
