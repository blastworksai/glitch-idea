"""Store integration of the migration chain (J12d), on the store the v0.2 release itself wrote."""
import copy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0, str(SCRIPTS))
import idea_chain as chain
import idea_markdown as md
import idea_store as storage
import idea_workflow as wf
from idea_domain import IdeaError, digest, snapshot
from idea_workflow import adapt_snapshot

FIXTURE = Path(__file__).resolve().parent/'fixtures/store-v2'
FULL = 'idea_'+'1'*32
SHAPE_ONLY = 'idea_'+'2'*32


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)/'store'
        shutil.copytree(FIXTURE, self.root)

    def tree(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in sorted(self.root.rglob('*'))
                if p.is_file() and '.transactions' not in p.parts and p.name != '.lock'}

    def open(self, store=None, write=False):
        store = store or storage.Store(self.root)
        with store.transaction(write=write) as state:
            result = copy.deepcopy(state)
        return store, result

    def break_idea(self, key):
        path = self.root/(key+'.md')
        raw = path.read_bytes()
        self.assertIn(b'scope: small-change', raw)
        path.write_bytes(raw.replace(b'scope: small-change', b'scope: galaxy', 1))


class MigratedStore(Base):
    def test_v2_store_opens_without_refusal(self):
        store, state = self.open()
        self.assertEqual(set(state['ideas']), {FULL, SHAPE_ONLY})
        for idea in state['ideas'].values():
            self.assertEqual(idea['workflow']['schema_version'], wf.WORKFLOW_VERSION)
            self.assertEqual(idea['revisions'][-1]['action'], 'migrated-v2-v3')
        self.assertEqual(sorted(store.migration_report.updated), [FULL, SHAPE_ONLY])
        self.assertEqual(store.migration_report.held, {})
        self.assertEqual(store.held, {})

    def test_originals_byte_identical_in_history(self):
        before = self.tree()
        self.open()
        after = self.tree()
        for key in (FULL, SHAPE_ONLY):
            original = before[key+'.md']
            name = 'history/'+key+'/migrations/w2-'+digest(original)+'.md'
            self.assertEqual(after[name], original)
            self.assertNotEqual(after[key+'.md'], original)
            for path, raw in before.items():
                if path.startswith('history/'+key+'/'):
                    self.assertEqual(after[path], raw)

    def test_second_open_migrates_nothing(self):
        self.open()
        settled = self.tree()
        store, _ = self.open()
        self.assertEqual(store.migration_report.updated, [])
        self.assertEqual(self.tree(), settled)

    def test_cache_hit_skips_chain(self):
        store, _ = self.open()
        calls = []
        real = chain.run
        chain.run = lambda *a, **k: calls.append(1) or real(*a, **k)
        self.addCleanup(setattr, chain, 'run', real)
        self.open(store)
        self.assertEqual(calls, [])
        self.assertEqual(store.migration_report.updated, [])
        self.open(storage.Store(self.root))
        self.assertEqual(calls, [1])

    def test_migrated_idea_can_be_saved(self):
        store, _ = self.open()
        with store.transaction(write=True) as state:
            idea = state['ideas'][FULL]
            idea['ratings'] = dict(idea['ratings'], urgency=3)
            idea['revision'] += 1
            idea['revisions'].append(adapt_snapshot(snapshot(idea, 'operator', 'rate'), idea.get('workflow')))
            store.commit(state)
        _, state = self.open(storage.Store(self.root))
        self.assertEqual(state['ideas'][FULL]['ratings']['urgency'], 3)
        self.assertEqual(state['ideas'][FULL]['revisions'][1]['schema_version'], 2)

    def test_altered_copy_is_corrupt_store(self):
        self.open()
        copy_path = next((self.root/'history'/FULL/'migrations').iterdir())
        copy_path.write_bytes(copy_path.read_bytes()+b' ')
        with self.assertRaises(IdeaError) as caught:
            self.open()
        self.assertEqual(caught.exception.code, 'corrupt_store')

    def test_unlinked_copy_is_corrupt_store(self):
        self.open()
        folder = self.root/'history'/SHAPE_ONLY/'migrations'
        stray = b'stray original'
        (folder/('w2-'+digest(stray)+'.md')).write_bytes(stray)
        with self.assertRaises(IdeaError) as caught:
            self.open()
        self.assertEqual(caught.exception.code, 'corrupt_store')


class HeldIdea(Base):
    def setUp(self):
        super().setUp()
        self.break_idea(SHAPE_ONLY)
        self.broken = {p: r for p, r in self.tree().items() if SHAPE_ONLY in p}

    def test_broken_idea_fails_alone_and_nothing_rewritten(self):
        store, state = self.open()
        self.assertEqual(list(state['ideas']), [FULL])
        self.assertEqual(state['order'], [FULL])
        self.assertEqual(list(store.migration_report.held), [SHAPE_ONLY])
        self.assertEqual(store.migration_report.updated, [FULL])
        after = self.tree()
        self.assertEqual({p: r for p, r in after.items() if SHAPE_ONLY in p}, self.broken)
        self.assertFalse((self.root/'history'/SHAPE_ONLY/'migrations').exists())
        with self.assertRaises(IdeaError) as caught:
            store.refuse_held(SHAPE_ONLY)
        self.assertEqual(caught.exception.code, wf.UNSUPPORTED_VERSION_CODE)
        self.assertEqual(str(caught.exception), wf.unsupported_version_message('Label the shelves \u2014 caf\u00e9 \U0001F4A1\n'))
        self.assertEqual(store.held[SHAPE_ONLY], str(caught.exception))
        store.refuse_held(FULL)  # a migrated idea is not held

    def test_held_idea_survives_a_second_open_and_a_cache_hit(self):
        store, _ = self.open()
        _, state = self.open(store)
        self.assertEqual(list(state['ideas']), [FULL])
        self.assertEqual(list(store.held), [SHAPE_ONLY])
        _, state = self.open()
        self.assertEqual(list(state['ideas']), [FULL])
        self.assertIn(SHAPE_ONLY, md.parse_document((self.root/'IDEAS.md').read_bytes()).metadata['order'])

    def test_write_to_the_held_idea_refused_and_others_saved(self):
        store, _ = self.open()
        before = self.tree()
        with self.assertRaises(IdeaError) as caught:
            with store.transaction(write=True) as state:
                state['order'].append(SHAPE_ONLY)
                state['ideas'][SHAPE_ONLY] = copy.deepcopy(state['ideas'][FULL])
                store.commit(state)
        self.assertEqual(caught.exception.code, wf.UNSUPPORTED_VERSION_CODE)
        self.assertIn('could not be updated for this version', str(caught.exception))
        self.assertEqual(self.tree(), before)
        with store.transaction(write=True) as state:  # D4: the rest stays editable (full cases in test_chain_store_held_edit)
            idea = state['ideas'][FULL]
            idea['ratings'] = dict(idea['ratings'], urgency=3)
            idea['revision'] += 1
            idea['revisions'].append(adapt_snapshot(snapshot(idea, 'operator', 'rate'), idea.get('workflow')))
            store.commit(state)
        self.assertEqual({p: r for p, r in self.tree().items() if SHAPE_ONLY in p}, self.broken)

    def test_doctor_names_held_idea(self):
        store, state = self.open()
        issues = store.view_issues(state)
        self.assertTrue(any(SHAPE_ONLY in issue for issue in issues))


if __name__ == '__main__':
    unittest.main()
