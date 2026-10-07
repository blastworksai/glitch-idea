"""Moving an idea's living detail file out of the store, on real temporary stores.

The store keeps revisions, metadata, plan evidence and an immutable pointer;
only <store>/<idea_id>.md leaves, for <workspace>/ideas/<idea_id>.md.
"""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0, str(SCRIPTS))
import idea_markdown as md
import idea_platform as platform
import idea_store as storage
import idea_transactions as tx
from idea_domain import IdeaError, digest, snapshot

KEY = 'idea_'+'1'*32
OTHER = 'idea_'+'2'*32
PLAN = 'plan_'+'3'*32
STAMP = '2026-10-01T00:00:00Z'
DETAIL = KEY+'.md'
POINTER = 'history/'+KEY+'/moved.md'


def idea(key=KEY):
    words = 'Café 💡 idea '+key[-4:]
    value = dict(idea_id=key, revision=1, status='active',
                 origin=dict(text=words, sha256=digest(words.encode()), actor='operator', timestamp=STAMP),
                 shape=None, ratings=None, assessments=[], revisions=[], proposals=[], plans=[], executions=[])
    value['revisions'] = [snapshot(value, 'operator', 'capture')]
    return value


class Stop(Exception):
    pass


class MoveStoreCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.root = base/'store'
        self.workspace = base/'workspace'
        self.workspace.mkdir()
        self.store = storage.Store(self.root)
        with self.store.transaction(write=True) as state:
            for key in (KEY, OTHER):
                state['ideas'][key] = idea(key)
            state['order'] = [KEY, OTHER]
            state['backlog_revision'] = 1
            self.store.commit(state)
        self.original = (self.root/DETAIL).read_bytes()

    def plan(self, plan_id=PLAN, revision=1):
        content = '# Plan\n\n## Trace\n\nPlan body\n'
        return dict(plan_id=plan_id, idea_id=KEY, idea_revision=revision, path=str(self.root/'plan-evidence'/(plan_id+'.md')),
                    source_path='/work/plan.md', content=content, sha256=digest(content.encode()), actor='operator',
                    timestamp=STAMP, validation=dict(builtin='idea-trace-and-sections-v1'))

    def move(self, **overrides):
        arguments = dict(expected_revision=1, plan=self.plan(), workspace_name='Atlas', workspace_path=str(self.workspace), actor='operator')
        arguments.update(overrides)
        with self.store.transaction(write=True) as state:
            return self.store.move_out(state, KEY, **arguments)

    def files(self):
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*') if p.is_file() and '.transactions' not in p.parts and p.name != '.lock')

    def loaded(self):
        with storage.Store(self.root).transaction() as state:
            return copy.deepcopy(state)

    def inspect(self, key=KEY):
        store = storage.Store(self.root)
        with store.transaction() as state:
            return state, store.lifecycle(key)

    def refusal(self, code, call, **expected):
        with self.assertRaises(IdeaError) as caught:
            call()
        self.assertEqual(caught.exception.code, code, str(caught.exception))
        for name, value in expected.items():
            self.assertEqual(caught.exception.details[name], value)
        return caught.exception

    def rewrite_index(self, change):
        raw = (self.root/'IDEAS.md').read_bytes()
        document = md.parse_document(raw)
        meta = copy.deepcopy(document.metadata)
        change(meta['extensions'])
        (self.root/'IDEAS.md').write_bytes(md.encode_document(meta, document.body))


class MoveTests(MoveStoreCase):
    def test_happy_move_leaves_only_the_pointer_and_keeps_history(self):
        result = self.move()
        home = self.workspace/'ideas'/DETAIL
        self.assertEqual(home.read_bytes(), self.original)
        self.assertFalse((self.root/DETAIL).exists())
        self.assertEqual(result['home'], dict(workspace_name='Atlas', workspace_path=str(self.workspace), file_path=str(home)))
        self.assertEqual(result['moved_sha256'], digest(self.original))
        self.assertFalse(result['resumed'])
        files = self.files()
        for kept in ('history/'+KEY+'/r1.md', POINTER, 'plan-evidence/'+PLAN+'.md', OTHER+'.md', 'IDEAS.md'):
            self.assertIn(kept, files)
        self.assertEqual(len([f for f in files if f.startswith('history/'+KEY+'/metadata/')]), 1)
        pointer = md.decode_moved((self.root/POINTER).read_bytes())
        self.assertEqual((pointer['idea_id'], pointer['idea_revision'], pointer['plan_id'], pointer['kind']), (KEY, 1, PLAN, 'moved'))
        self.assertEqual(pointer['workspace'], dict(name='Atlas', path=str(self.workspace)))
        self.assertEqual(pointer['moved_sha256'], digest(self.original))
        self.assertEqual(pointer['home_path'], str(home))
        self.assertEqual(os.listdir(self.root/'.transactions'), [])

    def test_load_after_move_rebuilds_the_idea_and_exposes_lifecycle_and_home(self):
        self.move()
        state, life = self.inspect()
        value = state['ideas'][KEY]
        self.assertEqual((value['status'], value['revision'], len(value['plans'])), ('archived', 1, 1))
        self.assertEqual(value['plans'][0]['content'], self.plan()['content'])
        self.assertEqual(life['lifecycle'], 'moved')
        self.assertEqual(life['home']['file_path'], str(self.workspace/'ideas'/DETAIL))
        self.assertEqual(self.inspect(OTHER)[1], dict(lifecycle='active', home=None, delivery=None))
        # A second load (cache reuse, then a cold store) agrees.
        self.assertEqual(self.loaded(), state)

    def test_resume_with_identical_bytes_does_not_rewrite_the_home_file(self):
        home = self.workspace/'ideas'/DETAIL
        home.parent.mkdir()
        home.write_bytes(self.original)
        before = home.stat()
        result = self.move()
        self.assertTrue(result['resumed'])
        self.assertEqual(home.stat().st_ino, before.st_ino)
        self.assertEqual(self.inspect()[1]['lifecycle'], 'moved')

    def test_home_conflict_leaves_everything_untouched(self):
        home = self.workspace/'ideas'/DETAIL
        home.parent.mkdir()
        home.write_bytes(b'someone else\n')
        error = self.refusal('home_conflict', self.move)
        self.assertEqual(error.details['home']['file_path'], str(home))
        self.assertEqual(home.read_bytes(), b'someone else\n')
        self.assertEqual((self.root/DETAIL).read_bytes(), self.original)
        self.assertFalse((self.root/POINTER).exists())

    def test_workspace_unavailable_causes(self):
        base = Path(self.temp.name).resolve()
        (base/'file').write_text('x')
        real = base/'real'
        real.mkdir()
        (base/'link').symlink_to(real)
        (real/'inner').mkdir()
        for label, path in (('relative', 'workspace'), ('missing', str(base/'missing')), ('not a directory', str(base/'file')),
                            ('symlink leaf', str(base/'link')), ('symlink component', str(base/'link'/'inner')), ('empty', ''), ('nonstring', None)):
            with self.subTest(label):
                self.refusal('workspace_unavailable', lambda: self.move(workspace_path=path))
        self.assertEqual((self.root/DETAIL).read_bytes(), self.original)

    def test_ideas_folder_that_is_a_symlink_is_unavailable(self):
        elsewhere = Path(self.temp.name).resolve()/'elsewhere'
        elsewhere.mkdir()
        (self.workspace/'ideas').symlink_to(elsewhere)
        self.refusal('workspace_unavailable', self.move)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_target_overlaps_store_in_both_directions(self):
        inside = self.root/'inside'
        inside.mkdir()
        self.refusal('target_overlaps_store', lambda: self.move(workspace_path=str(inside)))
        self.refusal('target_overlaps_store', lambda: self.move(workspace_path=str(self.root)))
        self.refusal('target_overlaps_store', lambda: self.move(workspace_path=str(self.root.parent)))
        self.assertEqual((self.root/DETAIL).read_bytes(), self.original)

    def test_invalid_input_name_revision_and_plan(self):
        for name in ('', '   ', 'x'*101, 'two\nlines', 'cr\rname', 5, None):
            with self.subTest(name=name):
                self.refusal('invalid_input', lambda: self.move(workspace_name=name))
        self.refusal('invalid_input', lambda: self.move(plan=dict(self.plan(), idea_id=OTHER)))
        self.refusal('invalid_input', lambda: self.move(plan=dict(self.plan(), path='/elsewhere/plan.md')))
        self.refusal('stale_revision', lambda: self.move(expected_revision=2))
        with self.store.transaction(write=True) as state:
            self.refusal('not_found', lambda: self.store.move_out(state, 'idea_'+'9'*32, expected_revision=1, plan=self.plan(),
                                                                 workspace_name='A', workspace_path=str(self.workspace), actor='operator'))
        self.assertFalse((self.workspace/'ideas').exists())

    def test_move_of_an_idea_that_already_has_a_registered_plan_from_disk(self):
        prior = 'plan_'+'5'*32
        with self.store.transaction(write=True) as state:
            entry = state['ideas'][KEY]
            entry['plans'].append(self.plan(prior))
            entry['status'] = 'archived'
            state['archives'][KEY+'/r1.json'] = dict(idea_id=KEY, origin=copy.deepcopy(entry['origin']), revision=copy.deepcopy(entry['revisions'][-1]))
            self.store.commit(state)
        result = self.move(plan=self.plan('plan_'+'6'*32))
        self.assertEqual(result['idea_id'], KEY)
        state, _ = self.inspect()
        self.assertEqual([p['plan_id'] for p in state['ideas'][KEY]['plans']], [prior, 'plan_'+'6'*32])

    def test_second_move_of_the_same_idea_is_idea_moved(self):
        self.move()
        error = self.refusal('idea_moved', lambda: self.move(plan=self.plan('plan_'+'4'*32)))
        self.assertEqual(error.details['home']['workspace_name'], 'Atlas')


class MovedStoreIntegrityTests(MoveStoreCase):
    def setUp(self):
        super().setUp()
        self.move()

    def test_stray_detail_file_for_a_moved_idea_is_corrupt(self):
        (self.root/DETAIL).write_bytes(self.original)
        self.refusal('corrupt_store', self.loaded)

    def test_pointer_bytes_that_differ_from_the_index_link_are_corrupt(self):
        raw = (self.root/POINTER).read_bytes()
        (self.root/POINTER).write_bytes(raw.replace(b'Atlas', b'Other'))
        self.refusal('corrupt_store', self.loaded)

    def test_pointer_with_a_bad_schema_or_wrong_idea_is_refused_even_with_a_matching_link(self):
        original = (self.root/POINTER).read_bytes()
        document = md.parse_document(original)
        def relink(raw):
            (self.root/POINTER).write_bytes(raw)
            def change(extensions):
                extensions[md.MOVE_EXTENSION][0]['sha256'] = digest(raw)
            self.rewrite_index(change)
        bad_schema = dict(document.metadata, schema_version=3)
        relink(md.encode_document(bad_schema, document.body))
        self.refusal('corrupt_store', self.loaded)
        wrong_idea = dict(document.metadata, idea_id=OTHER)
        relink(md.encode_document(wrong_idea, document.body))
        self.refusal('corrupt_store', self.loaded)
        extra = dict(document.metadata, surprise=1)
        relink(md.encode_document(extra, document.body))
        self.refusal('corrupt_store', self.loaded)

    def test_tampered_delivered_pointer_is_corrupt_store(self):
        with self.store.transaction(write=True) as state:
            self.store.deliver(state, KEY, ref='work-item-7', actor='operator')
        path = 'history/'+KEY+'/delivered.md'
        document = md.parse_document((self.root/path).read_bytes())
        raw = md.encode_document(dict(document.metadata, surprise=1), document.body)
        (self.root/path).write_bytes(raw)
        def change(extensions):
            extensions[md.DELIVERED_EXTENSION][0]['sha256'] = digest(raw)
        self.rewrite_index(change)
        self.refusal('corrupt_store', self.loaded)

    def refusal_code(self, call):
        with self.assertRaises(IdeaError) as caught:
            call()
        return caught.exception.code

    def test_index_link_to_a_pointer_for_another_idea_is_corrupt(self):
        def change(extensions):
            extensions[md.MOVE_EXTENSION][0]['path'] = 'history/'+OTHER+'/moved.md'
        self.rewrite_index(change)
        self.refusal('corrupt_store', self.loaded)

    def test_missing_pointer_is_corrupt(self):
        (self.root/POINTER).unlink()
        self.assertEqual(self.refusal_code(self.loaded), 'missing_artifact')

    def test_every_mutation_of_a_moved_idea_is_refused_with_its_home(self):
        home = dict(workspace_name='Atlas', workspace_path=str(self.workspace), file_path=str(self.workspace/'ideas'/DETAIL))
        def change_ratings():
            with self.store.transaction(write=True) as state:
                state['ideas'][KEY]['ratings'] = dict(urgency=3, importance=4, actor='operator')
                self.store.commit(state)
        self.refusal('idea_moved', change_ratings, home=home)
        def change_status():
            with self.store.transaction(write=True) as state:
                state['ideas'][KEY]['status'] = 'active'
                self.store.commit(state)
        self.refusal('idea_moved', change_status, home=home)
        def link_placement():
            with self.store.transaction(write=True) as state:
                value = state['ideas'][KEY]
                choice = dict(idea_id=KEY, idea_revision=1, position=1, reason='r', actor='operator', timestamp=STAMP,
                              source_backlog_revision=1, neighbors=dict(before=None, after=OTHER),
                              snapshot=dict(ratings=None, assessments=[]), accepted_backlog_revision=2)
                state['placements'].append(choice)
                state['backlog_revision'] = 2
                self.store.commit(state)
        self.refusal('idea_moved', link_placement, home=home)
        def append_asset():
            with self.store.transaction(write=True) as state:
                self.store._prepare_commit(state, self.store._contexts.active, handoff_append=(KEY, dict(handoff_id='handoff_'+'5'*32)))
        self.refusal('idea_moved', append_asset, home=home)
        self.assertFalse((self.root/DETAIL).exists())

    def test_mutating_another_idea_still_works_and_keeps_the_move(self):
        with self.store.transaction(write=True) as state:
            value = state['ideas'][OTHER]
            value['ratings'] = dict(urgency=3, importance=4, actor='operator')
            value['revision'] += 1
            value['revisions'].append(snapshot(value, 'operator', 'rate'))
            self.store.commit(state)
        self.assertEqual(self.inspect()[1]['lifecycle'], 'moved')
        self.assertFalse((self.root/DETAIL).exists())
        self.assertEqual(self.loaded()['ideas'][OTHER]['ratings']['urgency'], 3)


class PointerCodecTests(unittest.TestCase):
    def frozen(self, key=KEY):
        value = idea(key)
        value.pop('revisions')
        value['status'] = 'archived'
        plan = dict(plan_id=PLAN, idea_id=key, idea_revision=1, path='/s/plan.md', source_path='/w/plan.md',
                    sha256='a'*64, actor='operator', timestamp=STAMP, validation={})
        value['plans'] = [plan]
        return dict(idea=value, extensions={})

    def moved(self, **changes):
        arguments = dict(idea_revision=1, plan_id=PLAN, workspace=dict(name='Atlas', path='/w'), home_path='/w/ideas/'+DETAIL,
                         moved_sha256='b'*64, actor='operator', timestamp=STAMP, frozen=self.frozen())
        arguments.update(changes)
        return md.encode_moved(KEY, **arguments)

    def test_moved_pointer_round_trips_and_is_strict(self):
        raw = self.moved()
        self.assertEqual(md.decode_moved(raw)['frozen'], self.frozen())
        self.assertEqual(md.encode_document(md.parse_document(raw).metadata, md.parse_document(raw).body), raw)
        for bad in (dict(home_path='/w/other.md'), dict(workspace=dict(name='', path='/w')), dict(workspace=dict(name='a\nb', path='/w')),
                    dict(workspace=dict(name='A', path='relative'), home_path='relative/ideas/'+DETAIL), dict(moved_sha256='nothex'),
                    dict(plan_id='plan_'+'9'*32)):
            with self.subTest(bad=bad), self.assertRaises(IdeaError):
                self.moved(**bad)

    def test_delivered_pointer_round_trips_and_is_strict(self):
        raw = md.encode_delivered(KEY, ref='work-item-7', actor='operator', timestamp=STAMP)
        self.assertEqual(md.decode_delivered(raw)['ref'], 'work-item-7')
        document = md.parse_document(raw)
        with self.assertRaises(IdeaError):
            md.decode_delivered(md.encode_document(dict(document.metadata, extra=1), document.body))
        with self.assertRaises(IdeaError):
            md.decode_delivered(md.encode_document(dict(document.metadata, kind='moved'), document.body))

    def test_index_extension_links_are_validated(self):
        links = [dict(idea_id=KEY, path=POINTER, sha256='c'*64)]
        self.assertEqual(md.move_links({md.MOVE_EXTENSION: links}), links)
        with self.assertRaises(IdeaError):
            md.move_links({md.MOVE_EXTENSION: [dict(links[0], path='history/'+OTHER+'/moved.md')]})
        with self.assertRaises(IdeaError):
            md.move_links({md.MOVE_EXTENSION: links+links})


class MoveManifestStoreTests(MoveStoreCase):
    def pointer_for(self, raw, key=KEY):
        frozen = PointerCodecTests().frozen(key)
        return md.encode_moved(key, idea_revision=1, plan_id=PLAN, workspace=dict(name='A', path='/w'),
                               home_path='/w/ideas/'+key+'.md', moved_sha256=digest(raw), actor='operator', timestamp=STAMP, frozen=frozen)

    def publish(self, changes, expected_extra=None, **kwargs):
        expected = {path: None for path in changes}
        expected.update(expected_extra or {})
        with platform.store_lock(self.root/'.lock'):
            return tx.publish(self.root, changes, expected, **kwargs)

    def test_move_out_without_its_pointer_or_with_a_mismatched_hash_is_refused(self):
        self.refusal('invalid_input', lambda: self.publish({'plan-evidence/'+PLAN+'.md': b'x'}, move_out=DETAIL))
        wrong = self.pointer_for(b'different bytes')
        self.refusal('invalid_input', lambda: self.publish({POINTER: wrong}, move_out=DETAIL))
        other = self.pointer_for(self.original, OTHER)
        self.refusal('invalid_input', lambda: self.publish({POINTER: other}, move_out=DETAIL))
        self.assertEqual((self.root/DETAIL).read_bytes(), self.original)
        self.assertFalse((self.root/POINTER).exists())

    def test_move_out_refuses_non_idea_paths(self):
        for path in ('IDEAS.md', 'history/'+KEY+'/r1.md', 'plan-evidence/'+PLAN+'.md', '../outside.md', 'state.json'):
            with self.subTest(path=path):
                self.refusal('invalid_input', lambda: self.publish({POINTER: self.pointer_for(self.original)}, move_out=path))

    def test_valid_move_out_through_the_journal_removes_only_the_detail_file(self):
        index = (self.root/'IDEAS.md').read_bytes()
        self.publish({POINTER: self.pointer_for(self.original), 'IDEAS.md': index+b'\n'}, move_out=DETAIL,
                     expected_extra={'IDEAS.md': digest(index)})
        self.assertFalse((self.root/DETAIL).exists())
        self.assertTrue((self.root/(OTHER+'.md')).exists())
        self.assertTrue((self.root/POINTER).exists())


class CrashAtEveryCheckpointTests(MoveStoreCase):
    def phases(self):
        seen = []
        real = tx.publish
        def spy(*args, **kwargs):
            return real(*args, _checkpoint=seen.append, **kwargs)
        with patch.object(tx, 'publish', spy):
            self.move()
        return seen

    def test_a_crash_at_every_checkpoint_recovers_to_one_state_never_half(self):
        phases = self.phases()
        self.assertIn('prepared', phases)
        self.assertGreater(len(phases), 8)
        for crash in range(len(phases)):
            with self.subTest(checkpoint=crash, phase=phases[crash]):
                self.setUp()
                calls = []
                real = tx.publish
                def killer(*args, **kwargs):
                    def checkpoint(phase):
                        calls.append(phase)
                        if len(calls) == crash+1:
                            raise Stop(phase)
                    return real(*args, _checkpoint=checkpoint, **kwargs)
                with patch.object(tx, 'publish', killer), self.assertRaises(Stop):
                    self.move()
                # A fresh process: the next transaction recovers under the lock.
                state = self.loaded()
                detail, pointer = (self.root/DETAIL).exists(), (self.root/POINTER).exists()
                with storage.Store(self.root).transaction() as probe:
                    index = md.parse_document((self.root/'IDEAS.md').read_bytes())
                moved = bool(md.move_links(index.metadata['extensions']))
                self.assertEqual(os.listdir(self.root/'.transactions'), [])
                if moved:
                    self.assertFalse(detail)
                    self.assertTrue(pointer)
                    self.assertEqual(state['ideas'][KEY]['status'], 'archived')
                    self.assertEqual(len(state['ideas'][KEY]['plans']), 1)
                else:
                    self.assertTrue(detail)
                    self.assertFalse(pointer)
                    self.assertEqual(state['ideas'][KEY]['plans'], [])
                    self.assertEqual((self.root/DETAIL).read_bytes(), self.original)
                # Reaching 'prepared' commits the move; earlier crashes leave none.
                if phases.index('prepared') <= crash:
                    self.assertTrue(moved)
                else:
                    self.assertFalse(moved)
                # Never half: the store passes a full cold load either way.
                self.assertEqual(self.loaded(), state)


if __name__ == '__main__':
    unittest.main()
