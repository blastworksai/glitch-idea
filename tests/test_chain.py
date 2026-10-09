"""The migration chain engine (J12a), on real temporary stores with a toy 2 -> 3 step."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0, str(SCRIPTS))
import idea_chain as chain
import idea_markdown as md
import idea_platform as platform
import idea_store as storage
import idea_transactions as tx
from idea_domain import IdeaError, digest, snapshot

KEYS = ['idea_'+c*32 for c in '123456']
A, B, C, D, E, F = KEYS
STAMP = '2026-10-01T00:00:00Z'


def idea(key):
    words = 'Cafe idea '+key[-4:]
    value = dict(idea_id=key, revision=1, status='active',
                 origin=dict(text=words, sha256=digest(words.encode()), actor='operator', timestamp=STAMP),
                 shape=None, ratings=None, assessments=[], revisions=[], proposals=[], plans=[], executions=[])
    value['revisions'] = [snapshot(value, 'operator', 'capture')]
    return value


def toy_step(raw, history, *, actor, timestamp):
    """Version 2 -> 3: rewrites one field, adds revision 2."""
    doc = md.parse_document(raw)
    meta = copy.deepcopy(doc.metadata)
    key = meta['idea']['idea_id']
    meta['idea']['workflow'] = {'schema_version': 3, 'toy': 'new'}
    meta['idea']['revision'] += 1
    return md.encode_document(meta, doc.body), {'history/'+key+'/r2.md': b'toy revision '+timestamp.encode()}


class ChainCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()/'store'
        self.store = storage.Store(self.root)
        with self.store.transaction(write=True) as state:
            for key in KEYS:
                state['ideas'][key] = idea(key)
            state['order'] = list(KEYS)
            state['backlog_revision'] = 1
            self.store.commit(state)
        for key in KEYS:
            self.edit(key, lambda m: m['idea'].update(workflow={'schema_version': 2, 'toy': 'old'}))

    def edit(self, key, change):
        path = self.root/(key+'.md')
        doc = md.parse_document(path.read_bytes())
        meta = copy.deepcopy(doc.metadata)
        change(meta)
        path.write_bytes(md.encode_document(meta, doc.body))

    def edit_index(self, change):
        path = self.root/'IDEAS.md'
        doc = md.parse_document(path.read_bytes())
        meta = copy.deepcopy(doc.metadata)
        change(meta['extensions'])
        path.write_bytes(md.encode_document(meta, doc.body))

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in sorted(self.root.rglob('*'))
                if p.is_file() and '.transactions' not in p.parts and p.name != '.lock'}

    def run_chain(self, registry=None):
        with platform.store_lock(self.root/'.lock') as lock:
            return chain.run(self.store, lock, registry if registry is not None else {2: toy_step}, timestamp=STAMP)

    def migrated(self, key):
        return (self.root/'history'/key/'migrations').exists()


class ChainTests(ChainCase):
    def test_copy_is_byte_identical_and_hash_named(self):
        before = (self.root/(A+'.md')).read_bytes()
        report = self.run_chain()
        self.assertIn(A, report.updated)
        name = 'w2-'+digest(before)+'.md'
        copy_file = self.root/'history'/A/'migrations'/name
        self.assertEqual(copy_file.read_bytes(), before)
        self.assertNotEqual((self.root/(A+'.md')).read_bytes(), before)

    def test_link_records_from_to_path_sha(self):
        before = (self.root/(A+'.md')).read_bytes()
        self.run_chain()
        meta = md.parse_document((self.root/(A+'.md')).read_bytes()).metadata
        path = 'history/'+A+'/migrations/w2-'+digest(before)+'.md'
        self.assertEqual(meta['extensions'][chain.LINKS],
                         [dict(from_version=2, to_version=3, path=path, sha256=digest(before),
                               revision=2, actor='glitch-idea', timestamp=STAMP)])
        self.assertEqual(digest((self.root/path).read_bytes()), meta['extensions'][chain.LINKS][0]['sha256'])
        self.assertEqual(meta['idea']['workflow']['schema_version'], 3)
        self.assertTrue((self.root/'history'/A/'r2.md').exists())

    def test_second_run_migrates_nothing_and_writes_nothing(self):
        first = self.run_chain()
        self.assertEqual(sorted(first.updated), sorted(KEYS))
        after = self.snapshot()
        second = self.run_chain()
        self.assertEqual((second.updated, second.held), ([], {}))
        self.assertEqual(self.snapshot(), after)

    def test_refusing_step_leaves_bytes_and_holds_only_that_idea(self):
        self.edit(B, lambda m: m['idea']['workflow'].update(toy='refuse'))
        untouched = {p: v for p, v in self.snapshot().items() if p == B+'.md'}

        def step(raw, history, *, actor, timestamp):
            if md.parse_document(raw).metadata['idea']['workflow']['toy'] == 'refuse':
                raise IdeaError('invalid_input', 'refused on purpose')
            return toy_step(raw, history, actor=actor, timestamp=timestamp)
        report = self.run_chain({2: step})
        self.assertEqual(list(report.held), [B])
        self.assertIn('refused on purpose', report.held[B])
        self.assertNotIn(B, report.updated)
        self.assertEqual((self.root/(B+'.md')).read_bytes(), untouched[B+'.md'])
        self.assertFalse(self.migrated(B))
        self.assertFalse((self.root/'history'/B/'r2.md').exists())
        self.assertIn(A, report.updated)
        self.assertTrue(self.migrated(A))

    def test_publish_conflict_holds_only_that_idea(self):
        def step(raw, history, *, actor, timestamp):
            result = toy_step(raw, history, actor=actor, timestamp=timestamp)
            if md.parse_document(raw).metadata['idea']['idea_id'] == C:
                self.edit(C, lambda m: m['idea'].update(origin=dict(m['idea']['origin'])) or m.update(transaction_revision=m['transaction_revision']+9))
            return result
        report = self.run_chain({2: step})
        self.assertEqual(list(report.held), [C])
        self.assertFalse(self.migrated(C))
        self.assertFalse((self.root/'history'/C/'r2.md').exists())
        self.assertEqual(md.parse_document((self.root/(C+'.md')).read_bytes()).metadata['idea']['workflow']['schema_version'], 2)
        self.assertTrue(self.migrated(A))

    def test_moved_delivered_archived_are_held_untouched(self):
        self.edit(D, lambda m: m['idea'].update(status='archived'))
        self.edit_index(lambda ext: ext.update({
            'glitch_idea_moves': [dict(idea_id=E, path='history/'+E+'/moved.md', sha256='a'*64)],
            'glitch_idea_delivered': [dict(idea_id=F, path='history/'+F+'/delivered.md', sha256='b'*64)]}))
        before = self.snapshot()
        report = self.run_chain()
        self.assertEqual(set(report.held), {D, E, F})
        self.assertEqual(set(report.updated), {A, B, C})
        for key in (D, E, F):
            self.assertEqual((self.root/(key+'.md')).read_bytes(), before[key+'.md'])
            self.assertFalse(self.migrated(key))
        self.assertEqual((self.root/'IDEAS.md').read_bytes(), before['IDEAS.md'])

    def test_version_above_current_is_held(self):
        self.edit(A, lambda m: m['idea']['workflow'].update(schema_version=4))
        before = (self.root/(A+'.md')).read_bytes()
        report = self.run_chain()
        self.assertEqual(list(report.held), [A])
        self.assertEqual((self.root/(A+'.md')).read_bytes(), before)
        self.assertFalse(self.migrated(A))

    def test_missing_step_is_held(self):
        report = self.run_chain({})
        self.assertEqual(set(report.held), set(KEYS))
        self.assertEqual(report.updated, [])

    def test_line_absent_at_zero(self):
        self.assertIsNone(chain.line(0))
        self.assertIsNone(chain.line(-1))
        self.assertEqual(chain.line(3), '3 ideas were updated for this version (originals saved).')
        self.assertEqual(chain.line(1), '1 idea was updated for this version (original saved).')

    def test_transactions_allow_migration_copy_only(self):
        good = 'history/'+A+'/migrations/w2-'+'a'*64+'.md'
        self.assertTrue(tx._allowed(good))
        self.assertFalse(tx._mutable(good))
        for bad in ('history/'+A+'/migrations/w0-'+'a'*64+'.md',
                    'history/'+A+'/migrations/w2-'+'a'*63+'.md',
                    'history/'+A+'/migrations/w2-'+'A'*64+'.md',
                    'history/'+A+'/migrations/other.md',
                    'history/'+A+'/migrations/x/w2-'+'a'*64+'.md',
                    'history/backlog/migrations/w2-'+'a'*64+'.md',
                    'migrations/w2-'+'a'*64+'.md',
                    'history/'+A+'/migrations/w2-'+'a'*64+'.json'):
            self.assertFalse(tx._allowed(bad), bad)
        with platform.store_lock(self.root/'.lock'):
            tx.publish(self.root, {good: b'first'}, {good: None}).raise_for_error()
            with self.assertRaises(IdeaError) as caught:
                tx.publish(self.root, {good: b'second'}, {good: digest(b'first')})
            self.assertEqual(caught.exception.code, 'save_conflict')
        self.assertEqual((self.root/good).read_bytes(), b'first')


if __name__ == '__main__':
    unittest.main()
