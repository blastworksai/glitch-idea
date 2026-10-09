"""Store.import_idea: one idea in from another store, byte for byte (SKILLS-62, CP13 J2a).

Fixtures are built by the code itself (workflow helpers, one asset, one handoff), never copies of a real store.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
SCRIPTS = Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0, str(SCRIPTS))
import idea_asset_evidence as assets
import idea_markdown as md
import idea_platform as platform_
import idea_store as storage
import idea_transactions as tx
import idea_workflow
from idea_assessment import insertion_neighbors
from idea_domain import IdeaError, digest, snapshot
from idea_handoff_evidence import eligible_source
from test_handoff_service import accepted_managed
from test_handoff_store import seed as seed_handoff
from test_workflow import fields as workflow_fields
import test_legacy_prior_art as legacy

ACTOR = 'Operator'
IMPORTER = 'Importer'
DATA = b'fixture!'
OTHER = 'idea_'+'2'*32


def identifier(prefix, number):
    return prefix+'_'+format(number, '032x')


def plain_idea(key):
    words = 'Native idea '+key[-4:]
    value = dict(idea_id=key, revision=1, status='active',
                 origin=dict(text=words, sha256=digest(words.encode()), actor='operator', timestamp='2026-10-01T00:00:00Z'),
                 shape=None, ratings=None, assessments=[], revisions=[], proposals=[], plans=[], executions=[])
    value['revisions'] = [snapshot(value, 'operator', 'capture')]
    return value


def snap(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(Path(root).rglob('*'))
            if p.is_file() and not p.is_symlink() and tx.JOURNAL not in p.parts}


def snap_all(root):
    return sorted(p.relative_to(root).as_posix() for p in Path(root).rglob('*'))


class ImportCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.source = self.base/'Café source'
        self.target = self.base/'target'
        self.sid, self.key, self.packets, self.links = seed_handoff(self.source)
        self.add_asset()
        self.store = storage.Store(self.target, observer=IMPORTER)

    def add_asset(self):
        store = storage.Store(self.source, observer=ACTOR)
        with store.transaction() as state:
            revision = state['ideas'][self.key]['revision']
        base = dict(schema_version=1, kind='upload-intent', idea_id=self.key, source_revision=revision, actor=ACTOR,
                    timestamp='2026-10-02T00:00:00Z', upload_id=identifier('upload', 1), asset_id=identifier('asset', 1),
                    session_id=self.sid, name='Café 💡.png', declared_type='image/png', size=len(DATA))
        done = dict(base, kind='asset', blob_path=assets.blob_path(base['asset_id']), validated_type='image/png',
                    sha256=hashlib.sha256(DATA).hexdigest())
        blob = self.source/done['blob_path']
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(DATA)
        os.chmod(blob, 0o400)
        self.blob_path = done['blob_path']
        store.mutate_assets(self.sid, 'asset-1', {'operation': 'asset'}, lambda state: {'idea_id': self.key},
                            prepare_records=lambda state: [base, done])

    def run_import(self, **overrides):
        arguments = dict(actor=IMPORTER)
        arguments.update(overrides)
        return self.store.import_idea(self.source, self.key, **arguments)

    def loaded(self, root=None):
        with storage.Store(root or self.target, observer=IMPORTER).transaction() as state:
            return copy.deepcopy(state)

    def refused(self, code, **overrides):
        with self.assertRaises(IdeaError) as caught:
            self.run_import(**overrides)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def native(self, key, backlog=1):
        with self.store.transaction(write=True) as state:
            state['ideas'][key] = plain_idea(key)
            state['order'].append(key)
            state['backlog_revision'] += backlog
            self.store.commit(state)


class ImportHappyPath(ImportCase):
    def test_copies_every_pinned_file_byte_for_byte_and_the_target_loads_it(self):
        before = snap(self.source)
        result = self.run_import()
        self.assertFalse(result['repeated'])
        copied = snap(self.target)
        pinned = {item['path'] for item in result['files']}
        self.assertIn(self.key+'.md', pinned)
        self.assertIn(self.blob_path, pinned)
        self.assertTrue(any(path.startswith('history/'+self.key+'/metadata/') for path in pinned))
        self.assertTrue(any(path.startswith('assets/evidence/') for path in pinned))
        state = self.loaded()
        added = 'history/'+self.key+'/r'+str(state['ideas'][self.key]['revision'])+'.md'
        self.assertNotIn(added, before, 'the import adds one history revision of its own')
        for path in pinned:
            if path in (self.key+'.md', added):
                continue
            self.assertEqual(copied[path], before[path], path)
        revisions = [p for p in before if p.startswith('history/'+self.key+'/r')]
        self.assertTrue(revisions)
        for path in revisions:
            self.assertEqual(copied[path], before[path], 'a previous revision stays byte for byte: '+path)
        self.assertIn(added, copied)
        self.assertNotEqual(copied[self.key+'.md'], before[self.key+'.md'], 'the detail now carries the added revision')
        self.assertEqual(snap(self.source), before, 'the source is never written')
        self.assertEqual(state['order'], [self.key])
        self.assertIn(self.key, state['ideas'])

    def test_not_copied_the_source_index_the_source_backlog_and_retained_stages(self):
        stage = self.source/'assets/staging'/('upload_'+'1'*32+'.'+'2'*32+'.part')
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_bytes(DATA)
        before = snap(self.source)
        self.run_import()
        copied = snap(self.target)
        self.assertNotEqual(copied['IDEAS.md'], before['IDEAS.md'])
        self.assertEqual([p for p in copied if p.startswith('history/backlog/')], ['history/backlog/r1.md'])
        self.assertFalse([p for p in copied if p.startswith('assets/staging')])
        self.assertEqual(self.loaded()['placements'][0]['idea_id'], self.key)

    def test_blob_is_sealed_like_the_target_seals_its_own(self):
        self.run_import()
        mode = stat.S_IMODE((self.target/self.blob_path).stat().st_mode)
        self.assertEqual(mode, 0o400)
        self.assertEqual(stat.S_IMODE((self.target/'assets').stat().st_mode) & 0o077, 0)

    def test_idea_is_appended_last_with_one_immutable_provenance_placement(self):
        self.native(OTHER)
        result = self.run_import()
        state = self.loaded()
        self.assertEqual(state['order'], [OTHER, self.key])
        placement = state['placements'][-1]
        self.assertEqual(placement['idea_id'], self.key)
        self.assertEqual(placement['position'], 2)
        self.assertEqual(placement['neighbors'], dict(before=OTHER, after=None))
        self.assertEqual(placement['reason'], 'imported from '+str(self.source)+' · manifest sha256 '+result['manifest_sha256'])
        self.assertEqual(placement['actor'], IMPORTER)
        self.assertEqual(placement['source_backlog_revision']+1, placement['accepted_backlog_revision'])
        self.assertEqual(state['backlog_revision'], placement['accepted_backlog_revision'])
        self.assertEqual(result['provenance'], placement['reason'])

    def test_session_receipts_come_along_and_a_session_naming_another_idea_does_not(self):
        shared = storage.Store(self.source, observer=ACTOR)
        mine = shared.create_session()
        shared.mutate(mine, 'only-mine', {'operation': 'x'}, lambda state: {'idea_id': self.key})
        both = shared.create_session()
        with shared.transaction(write=True) as state:
            state['ideas'][OTHER] = plain_idea(OTHER)
            state['order'].append(OTHER)
            state['backlog_revision'] += 1
            shared.commit(state)
        shared.mutate(both, 'one', {'operation': 'x'}, lambda state: {'idea_id': self.key})
        shared.mutate(both, 'two', {'operation': 'x'}, lambda state: {'idea_id': OTHER})
        result = self.run_import()
        copied = snap(self.target)
        source = snap(self.source)
        for sid in (self.sid, mine):
            self.assertEqual(copied['session-recovery/'+sid+'.json'], source['session-recovery/'+sid+'.json'])
        self.assertNotIn('session-recovery/'+both+'.json', copied)
        self.assertEqual([item['path'] for item in result['sessions']['skipped']], ['session-recovery/'+both+'.json'])
        self.assertIn('session-recovery/'+self.sid+'.json', result['sessions']['taken'])

    def test_a_required_session_that_names_another_idea_refuses_whole(self):
        shared = storage.Store(self.source, observer=ACTOR)
        with shared.transaction(write=True) as state:
            state['ideas'][OTHER] = plain_idea(OTHER)
            state['order'].append(OTHER)
            state['backlog_revision'] += 1
            shared.commit(state)
        shared.mutate(self.sid, 'also-other', {'operation': 'x'}, lambda state: {'idea_id': OTHER})
        before = snap_all(self.base)
        self.refused('import_session_shared')
        self.assertEqual(snap_all(self.base), before)


def reaccept(service, store, key):
    """Accept Assess again at the idea's real place in the current order."""
    state = service.state(idea_id=key)
    with store.transaction() as current:
        order, revision = list(current['order']), current['backlog_revision']
    answers = copy.deepcopy(workflow_fields()['assess'])
    place = order.index(key)+1
    answers['position'] = dict(proposed_position=place, actual_position=place,
                               neighbors=insertion_neighbors(order, key, place), override_reason=None)
    service.accept(dict(request_id='reaccept-'+key[-8:]+'-'+str(revision), idea_id=key, expected_revision=state['revision'],
                        expected_draft_version=state['draft_version'], step='assess', fields=answers, proposal_id=None,
                        expected_backlog_revision=revision))


class ImportAssessReview(unittest.TestCase):
    """An import leaves Assess honest: the imported idea and every idea whose neighbours shifted must be accepted again."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        workspace = self.base/'work'
        workspace.mkdir()
        self.source = storage.Store(self.base/'source', observer='Operator')
        _, self.key = accepted_managed(self.source, workspace)
        self.target = storage.Store(self.base/'target', observer='Operator')
        self.service, self.existing = accepted_managed(self.target, workspace)

    def idea(self, key, store=None):
        with (store or self.target).transaction() as state:
            return copy.deepcopy(state['ideas'][key])

    def eligibility(self, key):
        with self.target.transaction() as state:
            try:
                eligible_source(state, state['ideas'][key])
            except IdeaError as exc:
                return exc.code
            return 'eligible'

    def test_the_preview_names_every_rewritten_detail(self):
        preview = self.target.import_idea(self.base/'source', self.key, actor='Importer', dry_run=True)
        generated = set(preview['generated'])
        self.assertIn(self.key+'.md', generated, 'the imported idea is rewritten with a review revision')
        self.assertIn(self.existing+'.md', generated, 'the shifted neighbour is rewritten too')
        self.assertTrue(any(p.startswith('history/'+self.key+'/r') for p in generated))

    def test_both_ideas_read_review_needed_and_neither_is_eligible_until_accepted_again(self):
        self.assertEqual(self.eligibility(self.existing), 'eligible')
        source_revision, existing_revision = self.idea(self.key, self.source)['revision'], self.idea(self.existing)['revision']
        self.target.import_idea(self.base/'source', self.key, actor='Importer')
        imported, existing = self.idea(self.key), self.idea(self.existing)
        for idea in (imported, existing):
            projection = idea_workflow.derive_state(idea)
            self.assertEqual(projection['steps']['assess']['status'], 'review-needed', idea['idea_id'])
        self.assertEqual(idea_workflow.derive_state(imported)['current_step'], 'assess')
        self.assertEqual(imported['revision'], source_revision+1)
        self.assertEqual(existing['revision'], existing_revision+1)
        self.assertEqual(imported['revisions'][-1]['revision'], imported['revision'])
        self.assertEqual(imported['revisions'][-1]['action'], 'import')
        self.assertEqual(self.eligibility(self.key), 'not_ready')
        self.assertEqual(self.eligibility(self.existing), 'not_ready')
        reaccept(self.service, self.target, self.existing)
        reaccept(self.service, self.target, self.key)
        self.assertEqual(self.eligibility(self.existing), 'eligible')
        self.assertEqual(self.eligibility(self.key), 'eligible')

    def test_the_state_names_the_cause_until_assess_is_accepted_again(self):
        self.target.import_idea(self.base/'source', self.key, actor='Importer')
        self.assertEqual(self.service.state(idea_id=self.key)['review_cause'], 'imported')
        self.assertIsNone(self.service.state(idea_id=self.existing)['review_cause'])
        reaccept(self.service, self.target, self.key)
        self.assertIsNone(self.service.state(idea_id=self.key)['review_cause'])

    def test_a_repeated_import_is_still_repeated_and_writes_nothing(self):
        self.target.import_idea(self.base/'source', self.key, actor='Importer')
        before = snap(self.base/'target')
        again = self.target.import_idea(self.base/'source', self.key, actor='Importer')
        self.assertTrue(again['repeated'])
        self.assertEqual(snap(self.base/'target'), before)


class ImportDryRun(ImportCase):
    def test_dry_run_previews_and_writes_nothing_to_a_missing_target(self):
        before = snap_all(self.base)
        result = self.run_import(dry_run=True)
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['position'], 1)
        self.assertEqual({'path', 'bytes', 'sha256', 'role'}, set(result['files'][0]))
        self.assertTrue(result['provenance'].startswith('imported from '))
        self.assertIn('session-recovery/'+self.sid+'.json', result['sessions']['taken'])
        self.assertEqual(snap_all(self.base), before)
        self.assertFalse(self.target.exists())

    def test_dry_run_on_a_populated_target_writes_nothing(self):
        self.native(OTHER)
        before = snap(self.target)
        result = self.run_import(dry_run=True)
        self.assertEqual(result['position'], 2)
        self.assertEqual(snap(self.target), before)


class ImportRefusals(ImportCase):
    def test_same_bytes_again_is_repeated_and_writes_nothing(self):
        self.run_import()
        before = snap(self.target)
        again = self.run_import()
        self.assertTrue(again['repeated'])
        self.assertEqual(snap(self.target), before)

    def test_same_id_with_different_bytes_is_an_id_clash(self):
        self.native(self.key)
        before = snap(self.target)
        self.refused('id_clash')
        self.assertEqual(snap(self.target), before)

    def test_a_pinned_file_that_does_not_hash_to_its_pin_is_refused_by_path_and_nothing_is_written(self):
        metadata = next(self.source.glob('history/'+self.key+'/metadata/*.md'))
        os.chmod(metadata, 0o600)
        metadata.write_bytes(metadata.read_bytes()+b'x')
        error = self.refused('import_hash_mismatch')
        self.assertIn('history/'+self.key+'/metadata/', error.details['path'])
        self.assertFalse(self.target.exists())

    def test_a_blob_that_does_not_hash_to_its_record_is_refused_and_nothing_is_written(self):
        blob = self.source/self.blob_path
        os.chmod(blob, 0o600)
        blob.write_bytes(b'tampered')
        error = self.refused('import_hash_mismatch')
        self.assertEqual(error.details['path'], self.blob_path)
        self.assertFalse(self.target.exists())

    def test_the_same_store_and_an_overlapping_store_are_refused(self):
        with self.assertRaises(IdeaError) as caught:
            storage.Store(self.source).import_idea(self.source, self.key, actor=ACTOR)
        self.assertEqual(caught.exception.code, 'same_store')
        with self.assertRaises(IdeaError) as caught:
            storage.Store(self.source/'inner').import_idea(self.source, self.key, actor=ACTOR)
        self.assertEqual(caught.exception.code, 'same_store')

    def test_another_workflow_version_is_unsupported(self):
        with mock.patch.object(idea_workflow, 'WORKFLOW_VERSION', 4):
            self.refused('import_unsupported')
        self.assertFalse(self.target.exists())

    def test_a_moved_idea_is_unsupported(self):
        root = self.base/'moved-source'
        workspace = self.base/'workspace'
        workspace.mkdir()
        source = storage.Store(root, observer=ACTOR)
        moved = 'idea_'+'4'*32
        with source.transaction(write=True) as state:
            state['ideas'][moved] = plain_idea(moved)
            state['order'] = [moved]
            state['backlog_revision'] = 1
            source.commit(state)
        content = '# Plan\n\n## Trace\n\nPlan body\n'
        plan = dict(plan_id='plan_'+'3'*32, idea_id=moved, idea_revision=1, path=str(root/'plan-evidence'/('plan_'+'3'*32+'.md')),
                    source_path='/work/plan.md', content=content, sha256=digest(content.encode()), actor='operator',
                    timestamp='2026-10-01T00:00:00Z', validation=dict(builtin='idea-trace-and-sections-v1'))
        with source.transaction(write=True) as state:
            source.move_out(state, moved, expected_revision=1, plan=plan, workspace_name='Atlas',
                            workspace_path=str(workspace), actor='operator')
        with self.assertRaises(IdeaError) as caught:
            self.store.import_idea(root, moved, actor=ACTOR)
        self.assertEqual(caught.exception.code, 'import_unsupported')

    def test_an_unknown_idea_and_a_missing_source(self):
        with self.assertRaises(IdeaError) as caught:
            self.store.import_idea(self.source, OTHER, actor=ACTOR)
        self.assertEqual(caught.exception.code, 'not_found')
        with self.assertRaises(IdeaError) as caught:
            self.store.import_idea(self.base/'nowhere', self.key, actor=ACTOR)
        self.assertEqual(caught.exception.code, 'not_found')

    def test_a_failed_publish_leaves_no_blob_behind(self):
        with mock.patch.object(storage.transactions, 'publish', side_effect=IdeaError('io_error', 'boom')):
            self.refused('io_error')
        self.assertFalse((self.target/self.blob_path).exists())


class ImportCommittedPublish(ImportCase):
    def test_a_failure_after_the_publish_committed_leaves_the_blobs_the_evidence_names(self):
        failed = platform_.WriteResult('uncertain', platform_.UNCERTAIN, OSError('marker sync failed'))
        with mock.patch.object(storage, 'initialize_marker', return_value=failed):
            error = self.refused('durability_uncertain')
        self.assertTrue(error.details.get('committed'))
        self.assertTrue((self.target/self.blob_path).is_file(), 'the committed evidence names this blob')
        self.assertEqual(self.loaded()['order'], [self.key])  # the next open is healthy, not corrupt_store

    def test_an_interrupt_after_the_journal_was_written_keeps_the_blobs(self):
        real = storage.transactions.publish
        def interrupted(*args, **kwargs):
            real(*args, **kwargs)
            raise KeyboardInterrupt()
        with mock.patch.object(storage.transactions, 'publish', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.run_import()
        self.assertTrue((self.target/self.blob_path).is_file())
        self.assertEqual(self.loaded()['order'], [self.key])

    def test_a_publish_known_not_to_have_committed_still_unwrites(self):
        with mock.patch.object(storage.transactions, 'publish', side_effect=IdeaError('save_conflict', 'moved on')):
            self.refused('save_conflict')
        self.assertFalse((self.target/self.blob_path).exists())


@unittest.skipUnless(os.name == 'posix', 'POSIX file modes are required')
class ImportBlobSealBeforeLink(ImportCase):
    def seen_at_link(self, shared):
        self.target.mkdir(parents=True, exist_ok=True)
        os.chmod(self.target, 0o2770 if shared else 0o700)
        modes, real = [], os.link
        def spy(source, destination, *args, **kwargs):
            modes.append((Path(destination).name, stat.S_IMODE(os.stat(source).st_mode)))
            return real(source, destination, *args, **kwargs)
        with mock.patch.object(platform_.os, 'link', spy):
            self.run_import()
        return [mode for name, mode in modes if name == Path(self.blob_path).name]

    def test_a_shared_blob_is_already_0440_when_it_is_linked(self):
        self.assertEqual(self.seen_at_link(True), [0o440])

    def test_an_unshared_blob_is_already_0400_when_it_is_linked(self):
        self.assertEqual(self.seen_at_link(False), [0o400])


class ImportUnprovedPoints(ImportCase):
    """The three points design section 1 listed as likely but unproved."""

    def test_a_detail_with_a_higher_transaction_revision_than_the_target_index_loads_and_commits(self):
        source_revision = self.loaded(self.source)['transaction_revision']
        self.assertGreater(source_revision, 1)
        self.run_import()
        index = md.decode_index((self.target/'IDEAS.md').read_bytes())
        detail = md.decode_detail((self.target/(self.key+'.md')).read_bytes())
        self.assertGreater(detail.metadata['transaction_revision'], index.metadata['transaction_revision'])
        state = self.loaded()
        self.assertGreaterEqual(state['transaction_revision'], source_revision)
        before = state['transaction_revision']
        with self.store.transaction(write=True) as state:
            state['ideas'][OTHER] = plain_idea(OTHER)
            state['order'].append(OTHER)
            state['backlog_revision'] += 1
            self.store.commit(state)
        self.assertGreater(self.loaded()['transaction_revision'], before)

    def test_asset_records_and_handoff_metadata_naming_sessions_the_target_never_saw_load(self):
        self.assertFalse((self.target/'session-recovery').exists())
        self.run_import()
        fresh = storage.Store(self.target, observer=IMPORTER)
        with fresh.transaction() as state:
            self.assertEqual(len(fresh.asset_records(state, self.key)), 2)
            self.assertEqual(len(fresh.handoffs(state, self.key)), 1)
        receipt = fresh.request_result(self.sid, 'handoff-fixture-1')
        self.assertEqual(receipt['idea_id'], self.key)
        self.assertLessEqual(receipt['backlog_revision'], self.loaded()['backlog_revision'])

    def test_a_target_backlog_below_the_carried_receipts_is_lifted_to_them(self):
        need = max(entry['result'].get('backlog_revision', 0) for entry in json.loads(
            (self.source/'session-recovery'/(self.sid+'.json')).read_text())['receipts'].values())
        self.assertGreater(need, 1)
        self.run_import()
        self.assertGreaterEqual(self.loaded()['backlog_revision'], need)

    def test_a_pre_prior_art_record_imports_and_loads_through_the_tolerance_without_a_rewrite(self):
        root = self.base/'legacy-source'
        source = storage.Store(root)
        with mock.patch.object(md, '_prior_art_lines', lambda d, legacy_line=True: []):
            with source.transaction(write=True) as state:
                state['ideas'][legacy.KEY] = legacy.legacy_idea()
                state['order'] = [legacy.KEY]
                state['backlog_revision'] = 1
                source.commit(state)
        original = (root/(legacy.KEY+'.md')).read_bytes()
        self.assertNotIn(legacy.LINE.encode(), original)
        self.store.import_idea(root, legacy.KEY, actor=IMPORTER)
        after_import = snap(self.target)
        # The detail is re-written by the import (it carries the added review revision), so only the history stays as written.
        for path in [p for p in snap(root) if p.startswith('history/'+legacy.KEY+'/r')]:
            self.assertEqual(after_import[path], (root/path).read_bytes(), path)
        self.assertIn(legacy.KEY, self.loaded()['ideas'])
        with storage.Store(self.target).transaction() as state:
            self.assertIn(legacy.KEY, state['ideas'])
        self.assertEqual(snap(self.target), after_import, 'a read must not rewrite any stored file')


if __name__ == '__main__':
    unittest.main()
