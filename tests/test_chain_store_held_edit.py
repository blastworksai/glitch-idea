"""J12f: with one idea held, the rest stay fully editable and the index keeps the held idea's row byte-for-byte."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_chain_store import Base, FULL, SHAPE_ONLY, md, storage, wf, IdeaError, adapt_snapshot, snapshot, digest, chain


def row(raw, key):
    lines = [line for line in raw.decode('utf-8').split('\n') if key+'.md' in line]
    assert len(lines) == 1, lines
    return lines[0].encode('utf-8')


class HeldEdit(Base):
    held = SHAPE_ONLY
    other = FULL

    def setUp(self):
        super().setUp()
        self.break_idea(self.held)
        self.index = (self.root/'IDEAS.md').read_bytes()
        self.held_files = {p: r for p, r in self.tree().items() if self.held in p}
        self.held_row = row(self.index, self.held)

    def rate(self, store, urgency):
        with store.transaction(write=True) as state:
            idea = state['ideas'][self.other]
            idea['ratings'] = dict(idea['ratings'], urgency=urgency)
            idea['revision'] += 1
            idea['revisions'].append(adapt_snapshot(snapshot(idea, 'operator', 'rate'), idea.get('workflow')))
            store.commit(state)

    def assert_held_intact(self):
        raw = (self.root/'IDEAS.md').read_bytes()
        self.assertEqual(row(raw, self.held), self.held_row)
        order = md.parse_document(raw).metadata['order']
        self.assertEqual(order.index(self.held), md.parse_document(self.index).metadata['order'].index(self.held))
        self.assertEqual({p: r for p, r in self.tree().items() if self.held in p}, self.held_files)

    def test_held_files_incl_history_survive_save_and_reopen(self):
        # CP12 r1 P1: the held idea's detail and every history file stay on disk, byte-identical.
        self.assertTrue(any(p.startswith('history/') for p in self.held_files))
        self.assertGreater(len(self.held_files), 1)
        store, _ = self.open()
        self.rate(store, 3)
        for path, raw in self.held_files.items():
            self.assertEqual((self.root/path).read_bytes(), raw)
        self.open(storage.Store(self.root))
        for path, raw in self.held_files.items():
            self.assertEqual((self.root/path).read_bytes(), raw)

    def test_held_store_is_a_cache_hit_with_no_chain_run(self):
        # CP12 r1 P2: held files are not in the cache payload, so the inventory comparison still matches.
        store, _ = self.open()
        calls = []
        real = chain.run
        chain.run = lambda *a, **k: calls.append(1) or real(*a, **k)
        self.addCleanup(setattr, chain, 'run', real)
        self.open(store)
        self.assertEqual(calls, [])
        self.assert_held_intact()

    def test_save_to_another_idea_succeeds_and_keeps_the_held_row(self):
        store, _ = self.open()
        self.rate(store, 3)
        self.assert_held_intact()
        self.assertNotEqual((self.root/'IDEAS.md').read_bytes(), self.index)
        self.rate(store, 4)  # a second save in the same open store
        self.assert_held_intact()
        fresh = storage.Store(self.root)
        _, state = self.open(fresh)
        self.assertEqual(state['ideas'][self.other]['ratings']['urgency'], 4)
        self.assertEqual(list(fresh.held), [self.held])
        self.assert_held_intact()

    def test_still_held_after_save_on_same_store_and_cache(self):
        store, _ = self.open()
        self.rate(store, 3)
        _, state = self.open(store)
        self.assertEqual(list(state['ideas']), [self.other])
        self.assertEqual(list(store.held), [self.held])
        with self.assertRaises(IdeaError) as caught:
            store.refuse_held(self.held)
        self.assertEqual(caught.exception.code, wf.UNSUPPORTED_VERSION_CODE)

    def test_doctor_names_it_after_a_save(self):
        store, _ = self.open()
        self.rate(store, 3)
        store, state = self.open(storage.Store(self.root))
        self.assertTrue(any(self.held in issue for issue in store.view_issues(state)))

    def test_write_targeting_the_held_idea_refuses(self):
        store, _ = self.open()
        before = self.tree()
        with self.assertRaises(IdeaError) as caught:
            with store.transaction(write=True) as state:
                state['order'].append(self.held)
                state['ideas'][self.held] = copy.deepcopy(state['ideas'][self.other])
                store.commit(state)
        self.assertEqual(caught.exception.code, wf.UNSUPPORTED_VERSION_CODE)
        self.assertEqual(str(caught.exception), store.held[self.held])
        self.assertEqual(self.tree(), before)

    def test_new_placement_naming_the_held_idea_refuses(self):
        store, _ = self.open()
        before = self.tree()
        with self.assertRaises(IdeaError) as caught:
            with store.transaction(write=True) as state:
                state['backlog_revision'] += 1
                state['placements'].append(self.placement(self.held, 1))
                store.commit(state)
        self.assertEqual(caught.exception.code, wf.UNSUPPORTED_VERSION_CODE)
        self.assertEqual(self.tree(), before)

    @staticmethod
    def placement(key, revision):
        return dict(idea_id=key, idea_revision=1, position=1, reason='r', actor='operator', timestamp='2026-10-08T00:00:00Z',
                    source_backlog_revision=revision-1, accepted_backlog_revision=revision,
                    neighbors=dict(before=None, after=None), snapshot=dict(ratings=None, assessments=[]))


class HeldFirst(HeldEdit):
    held = FULL
    other = SHAPE_ONLY

    def test_rank_of_the_held_row_does_not_move(self):
        store, _ = self.open()
        self.rate(store, 2)
        self.assert_held_intact()
        self.assertIn(b'| 1 |', self.held_row)


class HeldPlacement(Base):
    def test_backlog_placement_naming_the_held_idea_survives_a_save(self):
        placement = HeldEdit.placement(SHAPE_ONLY, 1)
        raw = md._encode_placement(placement)
        (self.root/'history'/'backlog').mkdir()
        (self.root/'history'/'backlog'/'r1.md').write_bytes(raw)
        index = md.parse_document((self.root/'IDEAS.md').read_bytes())
        meta = copy.deepcopy(index.metadata)
        meta['backlog_revision'] = 1
        meta['placements'] = [dict(path='history/backlog/r1.md', sha256=digest(raw))]
        (self.root/'IDEAS.md').write_bytes(md.encode_document(meta, index.body))
        self.break_idea(SHAPE_ONLY)
        held_row = row((self.root/'IDEAS.md').read_bytes(), SHAPE_ONLY)
        store, state = self.open()
        self.assertEqual(len(state['placements']), 1)
        with store.transaction(write=True) as state:
            idea = state['ideas'][FULL]
            idea['ratings'] = dict(idea['ratings'], urgency=3)
            idea['revision'] += 1
            idea['revisions'].append(adapt_snapshot(snapshot(idea, 'operator', 'rate'), idea.get('workflow')))
            store.commit(state)
        disk = (self.root/'IDEAS.md').read_bytes()
        self.assertEqual(row(disk, SHAPE_ONLY), held_row)
        links = md.parse_document(disk).metadata['placements']
        self.assertEqual(links, [dict(path='history/backlog/r1.md', sha256=digest(raw))])
        self.assertEqual((self.root/'history'/'backlog'/'r1.md').read_bytes(), raw)
        _, state = self.open(storage.Store(self.root))
        self.assertEqual(state['placements'], [placement])


if __name__ == '__main__':
    unittest.main()
