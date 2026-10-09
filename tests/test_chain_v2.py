"""The v2 -> v3 migration step (J12b), on the fixture store written by glitch-idea v0.2 itself."""
import copy
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0, str(SCRIPTS))
import idea_chain as chain
import idea_chain_v2 as step_v2
import idea_markdown as md
import idea_workflow as wf
import idea_workflow_v2 as v2
from idea_domain import IdeaError, digest

FIXTURE = Path(__file__).resolve().parent/'fixtures/store-v2'
FULL = 'idea_'+'1'*32
SHAPE_ONLY = 'idea_'+'2'*32
STAMP = '2026-10-08T09:00:00+00:00'
NOTES = 'First line of notes.\n\nSecond line, with a café \U0001F4A1 in it.\n'


def history_of(root, key):
    return {'history/'+key+'/'+p.name: p.read_bytes() for p in (root/'history'/key).glob('r*.md')}


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)/'store'
        shutil.copytree(FIXTURE, self.root)

    def raw(self, key):
        return (self.root/(key+'.md')).read_bytes()

    def migrate(self, key, raw=None):
        raw = self.raw(key) if raw is None else raw
        detail, files = step_v2.step(raw, history_of(self.root, key), actor='glitch-idea', timestamp=STAMP)
        return md.decode_detail(detail), files

    def old_workflow(self, key):
        return md.parse_document(self.raw(key)).metadata['idea']['workflow']

    def edited(self, key, change):
        doc = md.parse_document(self.raw(key))
        meta = copy.deepcopy(doc.metadata)
        change(meta['idea']['workflow'])
        return md.encode_document(meta, doc.body)


class StepTests(Base):
    def test_shape_becomes_exploration_draft_never_accepted(self):
        old = self.old_workflow(SHAPE_ONLY)['drafts']['shape']
        new, _ = self.migrate(SHAPE_ONLY)
        workflow = new.metadata['idea']['workflow']
        self.assertEqual(workflow['drafts']['exploration'], old)
        self.assertNotIn('shape', workflow['steps'])
        self.assertIsNone(workflow['steps']['exploration']['acceptance'])
        self.assertIsNone(workflow['steps']['exploration']['fields'])

    def test_accepted_shape_overlaid_by_its_draft(self):
        def change(w):
            w['drafts']['shape'] = {'outcome': 'Newer draft'}
        accepted = self.old_workflow(FULL)['steps']['shape']['fields']
        new, _ = self.migrate(FULL, self.edited(FULL, change))
        exploration = new.metadata['idea']['workflow']['drafts']['exploration']
        self.assertEqual(exploration['outcome'], 'Newer draft')
        self.assertEqual(exploration['scope'], accepted['scope'])

    def test_method_splits_into_method_and_exploration_drafts(self):
        def change(w):
            w['drafts']['method'] = {'selection': 'appetite-led', 'reason': 'Small bet',
                                     'investment': {'cap': 2, 'unit': 'sessions', 'boundary': 'One lid'},
                                     'experiment': None, 'memory': {'status': 'unavailable', 'sources': [], 'rationale': None}}
        new, _ = self.migrate(SHAPE_ONLY, self.edited(SHAPE_ONLY, change))
        drafts = new.metadata['idea']['workflow']['drafts']
        self.assertEqual(drafts['method'], {'selection': 'appetite-led', 'reason': 'Small bet',
                                            'memory': {'status': 'unavailable', 'sources': [], 'rationale': None}})
        self.assertEqual(drafts['exploration']['investment'], {'cap': 2, 'unit': 'sessions', 'boundary': 'One lid'})
        self.assertIsNone(drafts['exploration']['experiment'])
        for part in ('investment', 'experiment'):
            self.assertNotIn(part, drafts['method'])
        self.assertIsNone(new.metadata['idea']['workflow']['steps']['method']['acceptance'])

    def test_accepted_method_and_shape_of_a_full_idea_become_drafts_only(self):
        old = self.old_workflow(FULL)
        new, _ = self.migrate(FULL)
        workflow = new.metadata['idea']['workflow']
        self.assertEqual(workflow['drafts']['method']['selection'], old['steps']['method']['fields']['selection'])
        self.assertEqual(workflow['drafts']['exploration']['outcome'], old['steps']['shape']['fields']['outcome'])
        for name in ('method', 'discovery', 'exploration', 'visualize', 'assess', 'review'):
            self.assertIsNone(workflow['steps'][name]['acceptance'], name)
            self.assertIsNone(workflow['steps'][name]['fields'], name)
        self.assertEqual(workflow['drafts']['assess'], {'assessment': old['steps']['assess']['fields']['assessment']})
        self.assertNotIn('position', workflow['drafts']['assess'])
        self.assertEqual(workflow['drafts']['visualize']['disposition'], 'skipped')
        self.assertNotIn('review', workflow['drafts'])

    def test_capture_and_priorities_keep_their_receipts(self):
        for key in (FULL, SHAPE_ONLY):
            old = self.old_workflow(key)['steps']
            new, _ = self.migrate(key)
            steps = new.metadata['idea']['workflow']['steps']
            for name in ('capture', 'priorities'):
                self.assertEqual(steps[name], old[name], name)
                self.assertIsNotNone(steps[name]['acceptance'])

    def test_discovery_left_empty(self):
        for key in (FULL, SHAPE_ONLY):
            workflow = self.migrate(key)[0].metadata['idea']['workflow']
            self.assertEqual(workflow['steps']['discovery'], {'fields': None, 'acceptance': None, 'invalidated_by': []})
            self.assertNotIn('discovery', workflow['drafts'])
            self.assertNotIn('sketch', workflow['drafts'].get('exploration', {}))

    def test_current_step_is_method(self):
        for key in (FULL, SHAPE_ONLY):
            self.assertEqual(self.migrate(key)[0].metadata['idea']['workflow']['current_step'], 'method')

    def test_current_step_is_capture_when_capture_was_never_accepted(self):
        def change(w):
            w['steps']['capture'] = {'fields': None, 'acceptance': None, 'invalidated_by': []}
            w['steps']['priorities'] = {'fields': None, 'acceptance': None, 'invalidated_by': []}
            w['drafts']['capture'] = {'raw_text': 'Half typed'}
        new, _ = self.migrate(SHAPE_ONLY, self.edited(SHAPE_ONLY, change))
        workflow = new.metadata['idea']['workflow']
        self.assertEqual(workflow['current_step'], 'capture')
        self.assertEqual(workflow['drafts']['capture'], {'raw_text': 'Half typed'})

    def test_not_applicable_visualize_dropped(self):
        def change(w):
            w['drafts']['visualize'] = {'disposition': 'not-applicable', 'reason': 'Nothing to draw',
                                        'design_set_id': None, 'brief_evidence_id': None}
        new, _ = self.migrate(SHAPE_ONLY, self.edited(SHAPE_ONLY, change))
        self.assertEqual(new.metadata['idea']['workflow']['drafts']['visualize'], {'reason': 'Nothing to draw'})

        def only(w):
            w['drafts']['visualize'] = {'disposition': 'not-applicable'}
        new, _ = self.migrate(SHAPE_ONLY, self.edited(SHAPE_ONLY, only))
        self.assertNotIn('visualize', new.metadata['idea']['workflow']['drafts'])

    def test_new_revision_appended_v3(self):
        for key in (FULL, SHAPE_ONLY):
            old = md.parse_document(self.raw(key)).metadata
            new, files = self.migrate(key)
            idea, n = new.metadata['idea'], old['idea']['revision']
            self.assertEqual(idea['revision'], n + 1)
            self.assertEqual(idea['workflow']['schema_version'], 3)
            self.assertEqual(idea['workflow']['draft_version'], old['idea']['workflow']['draft_version'] + 1)
            self.assertEqual(idea['status'], 'active')
            self.assertEqual(new.metadata['history'][:n], old['history'])
            path = 'history/' + key + '/r' + str(n + 1) + '.md'
            self.assertEqual(list(files), [path])
            self.assertEqual(new.metadata['history'][n], {'path': path, 'sha256': digest(files[path])})
            snapshot = md.decode_history(files[path]).metadata['snapshot']
            self.assertEqual((snapshot['action'], snapshot['actor'], snapshot['schema_version']), ('migrated-v2-v3', 'glitch-idea', 3))
            self.assertEqual(snapshot['workflow'], idea['workflow'])
            for legacy in ('shape', 'ratings', 'assessments'):
                self.assertEqual(idea[legacy], old['idea'][legacy])
            self.assertEqual(new.metadata['extensions'], old['extensions'])
            self.assertEqual(idea['origin'], old['idea']['origin'])

    def test_notes_carried_byte_for_byte(self):
        new, _ = self.migrate(SHAPE_ONLY)
        self.assertEqual(md.detail_notes(new), NOTES)
        empty, _ = self.migrate(FULL)
        self.assertEqual(md.detail_notes(empty), '')

    def test_invalid_v2_record_is_refused_untouched(self):
        def change(w):
            w['drafts']['shape']['scope'] = 'galaxy'
        raw = self.edited(SHAPE_ONLY, change)
        with self.assertRaises(IdeaError):
            step_v2.step(raw, history_of(self.root, SHAPE_ONLY), actor='glitch-idea', timestamp=STAMP)

    def test_altered_history_is_refused(self):
        history = history_of(self.root, FULL)
        path = 'history/' + FULL + '/r1.md'
        history[path] += b' '
        with self.assertRaises(IdeaError):
            step_v2.step(self.raw(FULL), history, actor='glitch-idea', timestamp=STAMP)

    def test_engine_runs_the_real_step_over_the_fixture(self):
        report = chain.run(types.SimpleNamespace(path=self.root), None, timestamp=STAMP)
        self.assertEqual(sorted(report.updated), [FULL, SHAPE_ONLY])
        self.assertEqual(report.held, {})
        for key in (FULL, SHAPE_ONLY):
            detail = md.decode_detail((self.root/(key+'.md')).read_bytes())
            link = detail.metadata['extensions'][chain.LINKS][0]
            copy_bytes = (self.root/link['path']).read_bytes()
            self.assertEqual(digest(copy_bytes), link['sha256'])
            self.assertEqual(copy_bytes, (FIXTURE/(key+'.md')).read_bytes())
        again = chain.run(types.SimpleNamespace(path=self.root), None, timestamp=STAMP)
        self.assertEqual((again.updated, again.held), ([], {}))


class HistoricalTests(Base):
    def snapshot(self, key, n):
        return md.decode_history(history_of(self.root, key)['history/'+key+'/r'+str(n)+'.md'], historical=True).metadata['snapshot']

    def test_historical_v2_snapshot_reads_but_never_writes(self):
        snapshot = self.snapshot(FULL, 6)
        self.assertEqual(snapshot['schema_version'], 2)
        raw = history_of(self.root, FULL)['history/'+FULL+'/r6.md']
        wf.validate_snapshot(snapshot, historical=True)
        md.decode_history(raw, historical=True)
        with self.assertRaises(IdeaError) as caught:
            wf.validate_snapshot(snapshot)
        self.assertEqual(caught.exception.code, 'unsupported_idea_version')
        with self.assertRaises(IdeaError) as caught:
            md.decode_history(raw)
        self.assertEqual(caught.exception.code, 'unsupported_idea_version')
        origin = md.parse_document(raw).metadata['origin']
        with self.assertRaises(IdeaError) as caught:
            md.encode_history(FULL, snapshot, origin=origin)
        self.assertEqual(caught.exception.code, 'unsupported_idea_version')
        with self.assertRaises(IdeaError):
            wf.adapt_snapshot(snapshot)

    def test_historical_flag_does_not_excuse_a_broken_v2_snapshot(self):
        snapshot = self.snapshot(FULL, 6)
        snapshot['workflow']['steps']['shape']['fields']['scope'] = 'galaxy'
        with self.assertRaises(IdeaError):
            wf.validate_snapshot(snapshot, historical=True)

    def test_historical_flag_does_not_admit_other_versions(self):
        snapshot = self.snapshot(FULL, 6)
        snapshot['schema_version'] = 1
        with self.assertRaises(IdeaError):
            wf.validate_snapshot(snapshot, historical=True)

    def test_idea_reads_earlier_revisions_as_historical_and_the_last_as_current(self):
        new, files = self.migrate(FULL)
        idea = copy.deepcopy(new.metadata['idea'])
        n = idea['revision']
        idea['revisions'] = [self.snapshot(FULL, i) for i in range(1, n)] + \
            [md.decode_history(files['history/'+FULL+'/r'+str(n)+'.md']).metadata['snapshot']]
        wf._idea(idea)  # earlier v2 evidence reads, the latest revision is v3
        stale = copy.deepcopy(idea)
        stale['revisions'][-1] = self.snapshot(FULL, n - 1)
        stale['revisions'][-1]['revision'] = n
        with self.assertRaises(IdeaError):
            wf._idea(stale)

    def test_refusal_sentence_names_the_idea_by_its_first_words(self):
        self.assertEqual(wf.UNSUPPORTED_VERSION_MESSAGE,
                         '"{words}" could not be updated for this version. It was left exactly as it was.')
        self.assertEqual(wf.unsupported_version_message('  Label the shelves\nsecond line'),
                         '"Label the shelves" could not be updated for this version. It was left exactly as it was.')
        long = wf.unsupported_version_message('x' * 80)
        self.assertIn('"' + 'x' * 60 + '…"', long)
        self.assertEqual(wf.unsupported_version_message('\n  \n'),
                         'This idea could not be updated for this version. It was left exactly as it was.')


class FixtureTests(Base):
    def test_fixture_was_written_by_v02(self):
        for key in (FULL, SHAPE_ONLY):
            doc = md.parse_document(self.raw(key))
            workflow = doc.metadata['idea']['workflow']
            self.assertEqual(workflow['schema_version'], 2)
            self.assertEqual(v2.validate_workflow(workflow), workflow)
            with self.assertRaises(IdeaError):
                wf.validate_workflow(workflow)
            for n, link in enumerate(doc.metadata['history'], 1):
                raw = (self.root/link['path']).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), link['sha256'])
                snapshot = md.decode_history(raw, historical=True).metadata['snapshot']
                if 'workflow' in snapshot:
                    self.assertEqual(snapshot['schema_version'], 2)
                    v2.validate_snapshot(snapshot)
        self.assertEqual(v2.WORKFLOW_VERSION, 2)
        self.assertIn('shape', md.parse_document(self.raw(FULL)).metadata['idea']['workflow']['steps'])

    def test_frozen_module_has_no_writers(self):
        for name in ('adapt_snapshot', 'accept_step', 'save_draft', 'capture_workflow', 'import_workflow'):
            self.assertFalse(hasattr(v2, name), name)


if __name__ == '__main__':
    unittest.main()
