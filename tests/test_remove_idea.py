"""Store.remove_idea: the one removal road, on stores the code builds itself."""
import copy
import re
import os
import stat
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
SCRIPTS = Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0, str(SCRIPTS))
import idea_markdown as md
import idea_store as storage
import idea_transactions as tx
from idea_domain import IdeaError, digest
from test_import_idea import ImportCase, OTHER, plain_idea, snap
from test_move_store import MoveStoreCase, KEY as MKEY, OTHER as MOTHER

ACTOR = 'Remover'


class RemoveCase(ImportCase):
    """self.source is the store under test: one handoff idea with an asset, plus a second native idea."""

    def setUp(self):
        super().setUp()
        self.store = storage.Store(self.source, observer=ACTOR)
        with self.store.transaction(write=True) as state:
            state['ideas'][OTHER] = plain_idea(OTHER)
            state['order'].append(OTHER)
            state['backlog_revision'] += 1
            self.store.commit(state)
        self.stage = self.source/'assets/staging'/('upload_'+format(1, '032x')+'.'+'2'*32+'.part')
        self.stage.parent.mkdir(parents=True, exist_ok=True)

    def remove(self, key=None, **overrides):
        arguments = dict(actor=ACTOR, confirm=True)
        arguments.update(overrides)
        return self.store.remove_idea(key or self.key, **arguments)

    def refused(self, code, key=None, **overrides):
        with self.assertRaises(IdeaError) as caught:
            self.remove(key, **overrides)
        self.assertEqual(caught.exception.code, code, str(caught.exception))
        return caught.exception

    def fresh(self):
        with storage.Store(self.source, observer=ACTOR).transaction() as state:
            return copy.deepcopy(state)


class ReimportAfterRemoval(ImportCase):
    """A removed id may come back through import-idea, and may be removed again."""

    def remove(self):
        return self.store.remove_idea(self.key, actor=ACTOR, confirm=True)

    def pins(self):
        index = md.decode_index((self.target/'IDEAS.md').read_bytes())
        return storage.removal_links(index.metadata['extensions'])

    def doctor(self):
        import json, subprocess
        run = subprocess.run([sys.executable, str(SCRIPTS/'idea.py'), '--store', str(self.target), 'doctor'], capture_output=True, text=True)
        return json.loads(run.stdout)

    def test_import_remove_import_again_loads_and_both_pins_verify(self):
        self.run_import()
        self.remove()
        self.assertEqual(self.loaded()['order'], [])
        self.run_import()
        state = self.loaded()
        self.assertEqual(state['order'], [self.key])
        self.assertTrue((self.target/storage.removed_path(self.key)).is_file())
        self.remove()
        self.assertEqual(self.loaded()['order'], [])
        links = self.pins()
        self.assertEqual(len(links), 2)
        self.assertEqual(len({link['path'] for link in links}), 2)
        for link in links:
            self.assertEqual(link['idea_id'], self.key)
            self.assertEqual(digest((self.target/link['path']).read_bytes()), link['sha256'], link['path'])
            self.assertEqual(stat.S_IMODE((self.target/link['path']).stat().st_mode) & 0o222, 0, 'sealed: '+link['path'])
        self.assertEqual(sorted(p for p in snap(self.target) if p.startswith('history/removed/')), sorted(link['path'] for link in links))
        self.run_import()
        self.assertEqual(self.loaded()['order'], [self.key])
        report = self.doctor()
        self.assertTrue(report['ok'], report)

    def test_a_reimport_by_a_seat_whose_clock_is_behind_still_loads(self):
        # Validity must not depend on wall clocks: a second seat whose clock reads earlier than the removal
        # re-imports the idea, and the shared store must still open for everyone.
        self.run_import()
        self.remove()
        original = storage.now
        storage.now = lambda: '2001-01-01T00:00:00+00:00'
        try:
            self.run_import()
        finally:
            storage.now = original
        self.assertEqual(self.loaded()['order'], [self.key])
        report = self.doctor()
        self.assertTrue(report['ok'], report)

    def test_the_first_import_never_counts_as_a_comeback(self):
        # A removed id put back into the order by hand (no import after the removal) stays corrupt_store.
        self.run_import()
        before = snap(self.target)
        self.remove()
        removed_index = (self.target/'IDEAS.md').read_bytes()
        pins_doc = md.parse_document(removed_index).metadata['extensions'][storage.REMOVAL_EXTENSION]
        for relative, raw in before.items():  # every file of the imported idea comes back, as if never removed
            path = self.target/relative
            if relative == 'IDEAS.md' or path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        doc = md.parse_document(before['IDEAS.md'])
        meta = doc.metadata; meta.setdefault('extensions', {})[storage.REMOVAL_EXTENSION] = pins_doc
        (self.target/'IDEAS.md').write_bytes(md.encode_document(meta, doc.body))
        with self.assertRaises(IdeaError) as caught:
            self.loaded()
        self.assertEqual(caught.exception.code, 'corrupt_store')

    def test_the_pins_survive_a_later_write(self):
        self.run_import()
        self.remove()
        self.run_import()
        self.remove()
        before = self.pins()
        self.native(OTHER)
        self.assertEqual(self.pins(), before)
        self.assertEqual(self.loaded()['order'], [OTHER])

    def test_a_live_idea_beside_a_tombstone_it_did_not_come_back_through_import_stays_corrupt(self):
        self.run_import()
        self.remove()
        with self.assertRaises(IdeaError) as caught:
            self.native(self.key)
            self.loaded()
        self.assertEqual(caught.exception.code, 'corrupt_store')


class PreviewTests(RemoveCase):
    def test_preview_lists_what_goes_and_writes_nothing(self):
        before = snap(self.source)
        result = self.remove(confirm=False)
        self.assertFalse(result['removed'])
        self.assertTrue(result['dry_run'])
        paths = {item['path'] for item in result['files']}
        self.assertIn(self.key+'.md', paths)
        self.assertIn(self.blob_path, paths)
        self.assertTrue(any(p.startswith('history/'+self.key+'/r') for p in paths))
        self.assertTrue(any(p.startswith('assets/evidence/') for p in paths))
        for item in result['files']:
            self.assertEqual({'path', 'bytes', 'sha256', 'role'}, set(item))
        self.assertEqual(result['position'], 1)
        self.assertTrue(result['first_words'])
        self.assertEqual(snap(self.source), before)

    def test_default_is_the_preview(self):
        before = snap(self.source)
        result = self.store.remove_idea(self.key, actor=ACTOR)
        self.assertFalse(result['removed'])
        self.assertEqual(snap(self.source), before)


class RemoveTests(RemoveCase):
    def test_confirm_removes_everything_the_idea_owned_and_keeps_the_rest(self):
        preview = self.remove(confirm=False)
        result = self.remove()
        self.assertTrue(result['removed'])
        after = snap(self.source)
        for item in preview['files']:
            self.assertFalse((self.source/item['path']).exists(), item['path'])
        self.assertFalse((self.source/'history'/self.key).exists())
        self.assertIn(OTHER+'.md', after)
        body = after['IDEAS.md'].split(b'\n---\n', 1)[1]
        self.assertNotIn(self.key.encode(), body.replace(b'history/backlog', b''))
        self.assertNotIn(self.key.encode(), md.parse_document(after['IDEAS.md']).metadata['order'][0].encode())
        state = self.fresh()
        self.assertEqual(state['order'], [OTHER])
        self.assertEqual(list(state['ideas']), [OTHER])

    def test_tombstone_is_immutable_evidence_the_store_accepts(self):
        preview = self.remove(confirm=False)
        self.remove()
        path = 'history/removed/'+self.key+'.md'
        raw = (self.source/path).read_bytes()
        meta = md.parse_document(raw).metadata
        self.assertEqual(meta['idea_id'], self.key)
        self.assertEqual(meta['actor'], ACTOR)
        self.assertEqual(meta['first_words'], preview['first_words'])
        self.assertEqual({(m['path'], m['sha256']) for m in meta['manifest']},
                         {(i['path'], i['sha256']) for i in preview['files']})
        self.assertTrue(tx._allowed(path))
        self.fresh()  # a fresh load inventories the tombstone without complaint
        with self.assertRaises(IdeaError):
            tx.publish(self.source, {path: raw+b'x'}, {path: digest(raw)})

    def test_a_second_removal_is_not_found(self):
        self.remove()
        self.refused('not_found')
        self.refused('not_found', confirm=False)

    def test_unknown_id_is_not_found_and_writes_nothing(self):
        before = snap(self.source)
        self.refused('not_found', 'idea_'+'9'*32)
        self.assertEqual(snap(self.source), before)

    def test_the_staged_upload_of_this_idea_goes_and_another_ideas_stays(self):
        self.stage.write_bytes(b'x')
        keep = self.stage.with_name('upload_'+format(77, '032x')+'.'+'3'*32+'.part')
        keep.write_bytes(b'y')
        self.remove()
        self.assertFalse(self.stage.exists())

    def test_a_blob_another_idea_links_is_not_in_the_plan(self):
        with self.store.transaction() as state:
            context = self.store._contexts.active
            own = context['asset_entries'][self.key]
            context['asset_entries'][OTHER] = copy.deepcopy(own)
            _, loose = self.store._removal_plan(context, self.key)
        self.assertEqual(loose, [])
        with self.store.transaction() as state:
            _, loose = self.store._removal_plan(self.store._contexts.active, self.key)
        self.assertEqual([path for path, role in loose if role == 'asset blob'], [self.blob_path])

    def test_removal_survives_a_crash_after_prepare_by_rolling_forward(self):
        class Stop(Exception):
            pass

        def stop(phase):
            if phase == 'prepared':
                raise Stop()
        original = tx.publish

        def crashing(*args, **kwargs):
            return original(*args, _checkpoint=stop, **kwargs)
        tx_publish, tx.publish = tx.publish, crashing
        try:
            with self.assertRaises(Stop):
                self.remove()
        finally:
            tx.publish = tx_publish
        state = self.fresh()  # recovery rolls the journal forward
        self.assertEqual(state['order'], [OTHER])
        self.assertFalse((self.source/(self.key+'.md')).exists())

    def test_held_idea_is_refused_with_its_sentence(self):
        self.store.held[self.key] = 'held sentence'
        store = self.store
        original = store._quarantine
        store._quarantine = lambda held: None
        store._load_cache = store._load_cache  # keep the cached load; held stays as set
        try:
            with self.assertRaises(IdeaError) as caught:
                self.store.remove_idea(self.key, actor=ACTOR, confirm=True)
        finally:
            store._quarantine = original
        self.assertEqual(caught.exception.code, 'unsupported_idea_version')


class TombstonePinTests(RemoveCase):
    """The tombstone is pinned by hash in IDEAS.md and written sealed; nothing about it is taken on trust."""

    def tombstone(self):
        return self.source/'history/removed'/(self.key+'.md')

    def corrupt_on_open(self):
        with self.assertRaises(IdeaError) as caught:
            self.fresh()
        self.assertEqual(caught.exception.code, 'corrupt_store', str(caught.exception))

    def test_a_rewritten_tombstone_is_a_corrupt_store(self):
        self.remove()
        path = self.tombstone()
        os.chmod(path, 0o600)
        meta = md.parse_document(path.read_bytes())
        forged = dict(meta.metadata, actor='Someone else')
        path.write_bytes(md.encode_document(forged, meta.body))
        self.corrupt_on_open()

    def test_a_deleted_tombstone_is_a_corrupt_store(self):
        self.remove()
        self.tombstone().unlink()
        self.corrupt_on_open()

    def test_a_tombstone_the_index_does_not_pin_is_a_corrupt_store(self):
        self.remove()
        index = self.source/'IDEAS.md'
        document = md.parse_document(index.read_bytes())
        metadata = copy.deepcopy(document.metadata)
        self.assertIn('glitch_idea_removals', metadata['extensions'])
        del metadata['extensions']['glitch_idea_removals']
        os.chmod(index, 0o600)
        index.write_bytes(md.encode_document(metadata, document.body))
        self.corrupt_on_open()

    def test_the_pin_survives_later_writes(self):
        self.remove()
        with self.store.transaction(write=True) as state:
            state['backlog_revision'] += 1
            self.store.commit(state)
        self.fresh()
        links = md.parse_document((self.source/'IDEAS.md').read_bytes()).metadata['extensions']['glitch_idea_removals']
        self.assertEqual([link['idea_id'] for link in links], [self.key])

    def test_the_tombstone_is_sealed_0400_in_an_owner_only_store(self):
        self.remove()
        self.assertEqual(stat.S_IMODE(self.tombstone().stat().st_mode), 0o400)

    def test_the_tombstone_is_sealed_0440_in_a_shared_store(self):
        os.chmod(self.source, 0o2770)
        self.remove()
        self.assertEqual(stat.S_IMODE(self.tombstone().stat().st_mode), 0o440)


class RemoveWhileHeld(unittest.TestCase):
    """A real held idea (an unmigratable v2 record) next to a migrated one: the migrated one can still be removed."""

    def setUp(self):
        import shutil, tempfile
        from test_chain_store import FIXTURE, FULL, SHAPE_ONLY
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'store'
        shutil.copytree(FIXTURE, self.root)
        p = self.root/(SHAPE_ONLY+'.md'); raw = p.read_bytes()
        p.write_bytes(raw.replace(b'scope: small-change', b'scope: galaxy', 1))
        self.full, self.held = FULL, SHAPE_ONLY
        self.held_bytes = {q.relative_to(self.root).as_posix(): q.read_bytes() for q in self.root.rglob('*')
                           if q.is_file() and SHAPE_ONLY in q.as_posix()}

    def test_the_other_idea_is_removed_and_the_held_one_untouched(self):
        store = storage.Store(self.root, observer=ACTOR)
        with store.transaction():
            self.assertIn(self.held, store.held)
        result = store.remove_idea(self.full, actor=ACTOR, confirm=True)
        self.assertTrue(result['removed'])
        again = storage.Store(self.root, observer=ACTOR)
        with again.transaction() as state:
            self.assertEqual(state['order'], [])
            self.assertIn(self.held, again.held)
        for relative, raw in self.held_bytes.items():
            self.assertEqual((self.root/relative).read_bytes(), raw, relative)
        index = (self.root/'IDEAS.md').read_text()
        self.assertIn(self.held, index)
        # Once the held idea is repaired it migrates and the index table still checks out (its rank moved up).
        p = self.root/(self.held+'.md')
        p.write_bytes(p.read_bytes().replace(b'scope: galaxy', b'scope: small-change', 1))
        repaired = storage.Store(self.root, observer=ACTOR)
        with repaired.transaction() as state:
            self.assertEqual(state['order'], [self.held])
            self.assertEqual(repaired.held, {})


class HeldKeepsItsNeighbours(RemoveCase):
    """[removed, X, HELD, Y]: removing the first idea must leave [X, HELD, Y], never swap HELD and Y."""

    def test_the_held_idea_keeps_its_place_between_its_neighbours(self):
        x, y = 'idea_'+'4'*32, 'idea_'+'5'*32
        with self.store.transaction(write=True) as state:
            state['ideas'][x] = plain_idea(x); state['ideas'][y] = plain_idea(y)
            state['order'] = [self.key, x, OTHER, y]
            state['backlog_revision'] += 1
            self.store.commit(state)
        import idea_chain as chain
        original = chain.run
        chain.run = lambda store, lock, *a, **k: chain.Report(updated=[], held={OTHER: 'the chain could not update it'})
        try:
            store = storage.Store(self.source, observer=ACTOR)
            store.remove_idea(self.key, actor=ACTOR, confirm=True)
        finally:
            chain.run = original
        order = md.parse_document((self.source/'IDEAS.md').read_bytes()).metadata['order']
        self.assertEqual(order, [x, OTHER, y])
        self.assertEqual(self.fresh()['order'], [x, OTHER, y])


class InterruptedRemovalTests(RemoveCase):
    """Blobs and stages go after the journal commit; a crash there is finished by the next open."""

    def crash_after_commit(self):
        def boom(self_, loose, store_files):
            raise OSError('killed after the commit')
        original = storage.Store._unlink_removed
        storage.Store._unlink_removed = boom
        try:
            with self.assertRaises(OSError):
                self.remove()
        finally:
            storage.Store._unlink_removed = original

    def test_the_next_open_finishes_the_unlinks_and_says_so(self):
        self.stage.write_bytes(b'x')
        self.crash_after_commit()
        self.assertTrue((self.source/self.blob_path).exists())
        self.assertTrue(self.stage.exists())
        store = storage.Store(self.source, observer=ACTOR)
        with store.transaction() as state:
            self.assertEqual(state['order'], [OTHER])
        self.assertFalse((self.source/self.blob_path).exists())
        self.assertFalse(self.stage.exists())
        self.assertEqual(sorted(store.finished_removals), sorted([self.blob_path, 'assets/staging/'+self.stage.name]))
        again = storage.Store(self.source, observer=ACTOR)
        with again.transaction():
            pass
        self.assertEqual(again.finished_removals, [])

    def test_nothing_is_finished_while_an_idea_is_held(self):
        # A held idea is never parsed, so the links it may hold are unknown: finishing waits until nothing is held.
        self.stage.write_bytes(b'x')
        self.crash_after_commit()
        import idea_chain as chain
        original = chain.run
        chain.run = lambda store, lock, *a, **k: chain.Report(updated=[], held={OTHER: 'the chain could not update it'})
        try:
            store = storage.Store(self.source, observer=ACTOR)
            with store.transaction():
                pass
        finally:
            chain.run = original
        self.assertIn(OTHER, store.held)
        self.assertTrue((self.source/self.blob_path).exists(), 'a blob was unlinked while an idea is held')
        self.assertTrue(self.stage.exists(), 'a stage was unlinked while an idea is held')
        self.assertEqual(store.finished_removals, [])

    def test_a_removal_while_an_idea_is_held_leaves_blobs_and_stages_for_later(self):
        # The happy path too: a held idea's links are unknown, so its possible shared blob is not unlinked now;
        # the tombstone names it, and a later open with nothing held finishes the job.
        self.stage.write_bytes(b'x')
        import idea_chain as chain
        original = chain.run
        chain.run = lambda store, lock, *a, **k: chain.Report(updated=[], held={OTHER: 'the chain could not update it'})
        try:
            self.store = storage.Store(self.source, observer=ACTOR)
            self.remove()
        finally:
            chain.run = original
        self.assertTrue((self.source/self.blob_path).exists(), 'a blob was unlinked while an idea is held')
        self.assertTrue(self.stage.exists(), 'a stage was unlinked while an idea is held')
        later = storage.Store(self.source, observer=ACTOR)
        with later.transaction():
            pass
        self.assertFalse((self.source/self.blob_path).exists())
        self.assertFalse(self.stage.exists())
        self.assertEqual(sorted(later.finished_removals), sorted([self.blob_path, 'assets/staging/'+self.stage.name]))

    def test_a_blob_a_live_idea_links_is_never_touched(self):
        self.remove()
        blob = self.source/self.blob_path
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(b'bytes another idea owns')
        store = storage.Store(self.source, observer=ACTOR)
        with store.transaction() as state:
            context = store._contexts.active
            context['asset_entries'][OTHER] = [dict(record=dict(kind='asset', blob_path=self.blob_path, upload_id='upload_'+'0'*32), blob=None)]
            store._finish_removals(context)
        self.assertEqual(blob.read_bytes(), b'bytes another idea owns')

    def test_a_file_that_differs_from_the_manifest_is_left_alone(self):
        self.remove()
        blob = self.source/self.blob_path
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(b'someone else wrote this')
        with storage.Store(self.source, observer=ACTOR).transaction():
            pass
        self.assertEqual(blob.read_bytes(), b'someone else wrote this')

    def test_doctor_reports_it_and_is_healthy(self):
        self.crash_after_commit()
        import json, subprocess, sys
        run = subprocess.run([sys.executable, str(SCRIPTS/'idea.py'), '--store', str(self.source), 'doctor'], capture_output=True, text=True)
        data = json.loads(run.stdout)
        self.assertTrue(data['healthy'], data)
        self.assertTrue(any('finished an interrupted removal' in n for n in data['notices']), data)


class PlanAndLifecycleTests(MoveStoreCase):
    def remove(self, key=MKEY, **overrides):
        arguments = dict(actor='operator', confirm=True)
        arguments.update(overrides)
        return self.store.remove_idea(key, **arguments)

    def test_plan_evidence_goes_with_the_idea(self):
        prior = 'plan_'+'5'*32
        with self.store.transaction(write=True) as state:
            entry = state['ideas'][MKEY]
            entry['plans'].append(self.plan(prior))
            entry['status'] = 'archived'
            state['archives'][MKEY+'/r1.json'] = dict(idea_id=MKEY, origin=copy.deepcopy(entry['origin']), revision=copy.deepcopy(entry['revisions'][-1]))
            self.store.commit(state)
        self.assertTrue((self.root/'plan-evidence'/(prior+'.md')).exists())
        preview = self.remove(confirm=False)
        self.assertIn('plan-evidence/'+prior+'.md', {i['path'] for i in preview['files']})
        self.remove()
        self.assertFalse((self.root/'plan-evidence'/(prior+'.md')).exists())
        state = self.loaded()
        self.assertEqual(state['order'], [MOTHER])
        self.assertEqual(state['archives'], {})

    def test_the_archive_view_of_a_registered_plan_goes_with_the_idea(self):
        prior = 'plan_'+'5'*32
        with self.store.transaction(write=True) as state:
            entry = state['ideas'][MKEY]
            entry['plans'].append(self.plan(prior))
            entry['status'] = 'archived'
            state['archives'][MKEY+'/r1.json'] = dict(idea_id=MKEY, origin=copy.deepcopy(entry['origin']), revision=copy.deepcopy(entry['revisions'][-1]))
            self.store.commit(state)
            self.assertEqual(self.store.view_issues(state, repair=True), [])
        view = self.root/'archive'/MKEY/'r1.json'
        self.assertTrue(view.exists())
        preview = self.remove(confirm=False)
        self.assertIn('archive/'+MKEY+'/r1.json', {i['path'] for i in preview['files']})
        self.remove()
        self.assertFalse((self.root/'archive'/MKEY).exists(), 'origin text left on disk')
        manifest = md.parse_document((self.root/'history/removed'/(MKEY+'.md')).read_bytes()).metadata['manifest']
        self.assertIn('archive/'+MKEY+'/r1.json', {m['path'] for m in manifest})
        with self.store.transaction() as state:
            self.assertEqual(self.store.view_issues(state), [])

    def test_order_renumbers_and_the_placements_stay_as_history(self):
        before = self.loaded()
        self.remove()
        state = self.loaded()
        self.assertEqual(state['order'], [MOTHER])
        self.assertEqual(state['placements'], before['placements'])

    def test_a_moved_idea_is_refused(self):
        self.move()
        with self.assertRaises(IdeaError) as caught:
            self.remove()
        self.assertEqual(caught.exception.code, 'idea_moved')

    def test_a_delivered_idea_is_refused(self):
        self.move()
        with self.store.transaction(write=True) as state:
            self.store.deliver(state, MKEY, ref='work-item-7', actor='operator')
        with self.assertRaises(IdeaError) as caught:
            self.remove()
        self.assertIn(caught.exception.code, ('idea_delivered', 'idea_moved'))
        before = self.files()
        with self.assertRaises(IdeaError):
            self.remove()
        self.assertEqual(self.files(), before)

    def test_removing_the_other_idea_leaves_the_moved_one_loading(self):
        self.move()
        self.remove(MOTHER)
        self.assertEqual(self.loaded()['order'], [MKEY])


class DesignSetTests(RemoveCase):
    """A design set (the Visualize choice) is an asset record with no upload id; removal must read past it."""

    def add_design_set(self):
        from idea_workflow import source_digest as step_digest
        store = storage.Store(self.source, observer=ACTOR)
        with store.transaction() as state:
            idea = state['ideas'][self.key]
            source = {}
            for step in ('capture', 'discovery', 'exploration'):
                record = idea['workflow']['steps'][step]
                revision = record['acceptance']['accepted_revision']
                source[step] = dict(revision=revision, digest=step_digest(step, revision, {step: record['fields']}))
            revision = idea['revision']
        import hashlib
        from test_import_idea import DATA
        record = dict(schema_version=1, kind='design-set', idea_id=self.key, source_revision=revision, actor=ACTOR,
                      timestamp='2026-10-02T00:00:00Z', set_id='set_'+'3'*32, session_id=self.sid, source=source,
                      source_digest=__import__('idea_asset_evidence').source_digest(source),
                      members=[dict(asset_id='asset_'+format(1, '032x'), name='Café 💡.png', type='image/png',
                                    size=len(DATA), sha256=hashlib.sha256(DATA).hexdigest())])
        store.mutate_assets(self.sid, 'set-1', {'operation': 'design-set'}, lambda state: {'idea_id': self.key},
                            prepare_records=lambda state: [record])

    def test_preview_and_removal_read_past_a_design_set(self):
        self.add_design_set()
        preview = self.store.remove_idea(OTHER, actor=ACTOR, confirm=False)
        self.assertNotIn('error', preview)
        self.remove(OTHER)
        self.assertNotIn(OTHER, self.fresh()['order'])
        self.assertIn(self.key, self.fresh()['order'])

    def test_removing_the_idea_that_owns_the_design_set(self):
        self.add_design_set()
        self.remove()
        self.assertNotIn(self.key, self.fresh()['order'])


if __name__ == '__main__':
    unittest.main()
