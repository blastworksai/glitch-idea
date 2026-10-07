"""Actual process-crash publication tests, authored by Operator.

Native Windows/macOS qualification remains separate; injected grades test the
published result contract only. All user data and journal files are temporary.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'
sys.path.insert(0, str(SCRIPTS))
import idea_transactions as tx
import idea_platform as platform
from idea_domain import IdeaError, digest, encoded

IDEA = 'idea_' + '1' * 32 + '.md'
HISTORY = 'history/idea_' + '1' * 32 + '/r1.md'
SESSION = 'session-recovery/session_' + '2' * 32 + '.json'

# Fresh processes both acquire the real platform lock. Parent kills at a named
# boundary, not an exception masquerading as a crash. Restart is another process.
CHILD = r'''import sys,time
sys.path.insert(0,sys.argv[1])
from pathlib import Path
from idea_transactions import publish,recover
from idea_platform import store_lock
from idea_domain import digest
root=Path(sys.argv[2]); target=sys.argv[3]
def checkpoint(phase):
    if phase==target:
        print(phase,flush=True)
        time.sleep(60)
with store_lock(root/'.lock'):
    if target=='RECOVER':
        result=recover(root)
        result.raise_for_error()
        print(result.publication,flush=True)
    else:
        changes={sys.argv[4]:b'detail after', 'IDEAS.md':b'index after'}
        expected={sys.argv[4]:digest(b'detail before'),'IDEAS.md':digest(b'index before')}
        if len(sys.argv)>5 and sys.argv[5]=='migration':
            legacy=b'exact legacy bytes\r\n'
            changes.update({'migration-recovery/v1-state.json':legacy,'migration-recovery/receipt.json':b'receipt'})
            expected.update({'migration-recovery/v1-state.json':None,'migration-recovery/receipt.json':None})
            result=publish(root,changes,expected,freeze_legacy=True,legacy_sha256=digest(legacy),_checkpoint=checkpoint)
        else:
            result=publish(root,changes,expected,_checkpoint=checkpoint)
        result.raise_for_error()
'''


class Stop(Exception):
    pass


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'Café spaced store'
        self.root.mkdir()
        (self.root/IDEA).write_bytes(b'detail before')
        (self.root/'IDEAS.md').write_bytes(b'index before')

    def changes(self):
        return {IDEA:b'detail after','IDEAS.md':b'index after'}

    def expected(self):
        return {IDEA:digest(b'detail before'),'IDEAS.md':digest(b'index before')}

    def publish(self, changes=None, expected=None, **kwargs):
        with platform.store_lock(self.root/'.lock'):
            return tx.publish(self.root, self.changes() if changes is None else changes,
                              self.expected() if expected is None else expected, **kwargs)

    def recover(self, **kwargs):
        with platform.store_lock(self.root/'.lock'):
            return tx.recover(self.root, **kwargs)

    def stop_at(self, phase):
        def callback(actual):
            if actual == phase:
                raise Stop(actual)
        with self.assertRaises(Stop):
            self.publish(_checkpoint=callback)

    def manifest_path(self):
        return next((self.root/tx.JOURNAL).glob('txn_*/manifest.json'))

    def manifest(self):
        path = self.manifest_path()
        return path, json.loads(path.read_bytes())

    def test_complete_publication_real_barriers_index_last_and_owned_cleanup(self):
        phases=[]
        result=self.publish(_checkpoint=phases.append)
        result.raise_for_error()
        self.assertEqual(result.publication, 'published')
        self.assertEqual(result.durability, platform.FILE_AND_DIRECTORY_SYNCED if os.name!='nt' else platform.FILE_SYNCED_PROCESS_RECOVERY)
        self.assertEqual([p for p in phases if p.startswith('published:')], ['published:'+IDEA,'published:IDEAS.md'])
        self.assertEqual((self.root/IDEA).read_bytes(), b'detail after')
        self.assertEqual((self.root/'IDEAS.md').read_bytes(), b'index after')
        self.assertEqual(list((self.root/tx.JOURNAL).iterdir()), [])
        self.assertEqual((self.root/'.lock').read_bytes(), b'') # Store owns initialization

    def test_real_kill_at_each_publication_and_cleanup_boundary(self):
        phases=['journal_created','staging_manifest','staged:0.before','staged:0.after',
                'staged:1.before','staged:1.after','prepared','published:'+IDEA,
                'published:IDEAS.md','verified','complete','cleaned:0.before',
                'cleaned:0.after','cleaned:1.before','cleaned:1.after','cleaned:manifest.json','cleanup_done']
        # Entries are sorted with detail first, index last.
        for phase in phases:
            with self.subTest(phase=phase):
                with tempfile.TemporaryDirectory() as directory:
                    root=Path(directory)
                    (root/IDEA).write_bytes(b'detail before'); (root/'IDEAS.md').write_bytes(b'index before')
                    child=subprocess.Popen([sys.executable,'-c',CHILD,str(SCRIPTS),str(root),phase,IDEA],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                    try:
                        self.assertEqual(child.stdout.readline().strip(), phase)
                        child.kill(); child.communicate(timeout=3)
                    finally:
                        if child.poll() is None: child.kill()
                        child.communicate(timeout=3)
                    restart=subprocess.run([sys.executable,'-c',CHILD,str(SCRIPTS),str(root),'RECOVER',IDEA],capture_output=True,text=True,timeout=4)
                    self.assertEqual(restart.returncode,0,restart.stderr)
                    prepared=phases.index(phase)>=phases.index('prepared')
                    self.assertEqual((root/IDEA).read_bytes(), b'detail after' if prepared else b'detail before')
                    self.assertEqual((root/'IDEAS.md').read_bytes(), b'index after' if prepared else b'index before')
                    self.assertEqual(list((root/tx.JOURNAL).iterdir()), [])

    def test_all_targets_preflight_before_recovery_mutation(self):
        self.stop_at('prepared')
        (self.root/'IDEAS.md').write_bytes(b'outsider index')
        with self.assertRaises(IdeaError) as caught: self.recover()
        self.assertEqual(caught.exception.code,'recovery_conflict')
        self.assertEqual((self.root/IDEA).read_bytes(),b'detail before')
        self.assertEqual((self.root/'IDEAS.md').read_bytes(),b'outsider index')
        self.assertTrue(self.manifest_path().exists())

    def test_partial_publication_external_edit_preserved(self):
        self.stop_at('published:'+IDEA)
        (self.root/IDEA).write_bytes(b'outsider detail')
        with self.assertRaises(IdeaError) as caught: self.recover()
        self.assertEqual(caught.exception.code,'recovery_conflict')
        self.assertEqual((self.root/IDEA).read_bytes(),b'outsider detail')
        self.assertEqual((self.root/'IDEAS.md').read_bytes(),b'index before')

    def test_missing_or_corrupt_stages_fail_closed(self):
        for kind in ('missing','corrupt'):
            with self.subTest(kind=kind):
                self.stop_at('prepared')
                stage=self.manifest_path().parent/'0.after'
                original=stage.read_bytes()
                if kind=='missing': stage.unlink()
                else: stage.write_bytes(b'bad stage')
                with self.assertRaises(IdeaError) as caught: self.recover()
                self.assertEqual(caught.exception.code,'recovery_conflict')
                self.assertEqual((self.root/IDEA).read_bytes(),b'detail before')
                stage.write_bytes(original)
                self.recover().raise_for_error()
                # Restore fixture for second loop without touching user artifacts.
                (self.root/IDEA).write_bytes(b'detail before'); (self.root/'IDEAS.md').write_bytes(b'index before')

    def test_unprepared_orphan_with_unknown_file_preserved(self):
        self.stop_at('journal_created')
        folder=next((self.root/tx.JOURNAL).iterdir())
        (folder/'user-note').write_bytes(b'keep')
        with self.assertRaises(IdeaError): self.recover()
        self.assertEqual((folder/'user-note').read_bytes(),b'keep')
        self.assertEqual((self.root/IDEA).read_bytes(),b'detail before')

    def test_staging_orphan_cleans_only_declared_files_without_authority_change(self):
        self.stop_at('staged:0.after')
        (self.root/IDEA).write_bytes(b'new external edit before prepare')
        self.recover().raise_for_error()
        self.assertEqual((self.root/IDEA).read_bytes(),b'new external edit before prepare')
        self.assertEqual(list((self.root/tx.JOURNAL).iterdir()), [])

    def test_unknown_traversal_and_deletion_paths_refused(self):
        for path in ('../outside','/etc/passwd','glitch-idea/config.json','state.json',
                     'blob/file.bin','.lock','migration-recovery/v1-state.json','idea_bad.md','a\\b'):
            with self.subTest(path=path):
                with self.assertRaises(IdeaError): self.publish({path:b'x'},{path:None})
        self.assertFalse((self.root/tx.JOURNAL).exists())

    def test_symlink_root_ancestor_target_stage_and_parent_refused(self):
        if not hasattr(os,'symlink'): self.skipTest('Symlink support unavailable')
        alias=self.root.parent/'alias'; alias.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(IdeaError): tx.publish(alias,self.changes(),self.expected())
        nested=self.root/'nested'; nested.mkdir()
        with self.assertRaises(IdeaError): tx.recover(alias/'nested')
        (self.root/IDEA).unlink(); (self.root/IDEA).symlink_to(self.root/'IDEAS.md')
        with self.assertRaises(IdeaError): self.publish()
        (self.root/IDEA).unlink(); (self.root/IDEA).write_bytes(b'detail before')
        outside=self.root.parent/'outside'; outside.mkdir()
        (self.root/'history').symlink_to(outside,target_is_directory=True)
        with self.assertRaises(IdeaError): self.publish({HISTORY:b'evidence'},{HISTORY:None})
        (self.root/'history').unlink()
        self.stop_at('prepared')
        stage=self.manifest_path().parent/'0.after'; stage.unlink(); stage.symlink_to(self.root/IDEA)
        with self.assertRaises(IdeaError): self.recover()
        self.assertEqual((self.root/IDEA).read_bytes(),b'detail before')

    def test_malformed_duplicate_unknown_and_oversize_manifests_preserved(self):
        self.stop_at('prepared')
        path, original=self.manifest()
        mutations=[lambda m:m.update(schema_version=True), lambda m:m.update(phase='unknown'),
                   lambda m:m['entries'][0].update(path='../escape'), lambda m:m['entries'][0].update(path='config.json'),
                   lambda m:m['entries'].append(copy.deepcopy(m['entries'][0])),lambda m:m['entries'][0].update(after_size=True),
                   lambda m:m['entries'][0].update(immutable=True),lambda m:m.update(extra='x')]
        for mutate in mutations:
            value=copy.deepcopy(original); mutate(value); path.write_bytes(encoded(value))
            with self.assertRaises(IdeaError): self.recover()
            self.assertEqual((self.root/IDEA).read_bytes(),b'detail before')
        path.write_bytes(b'{"schema_version":1,"schema_version":2}')
        with self.assertRaises(IdeaError): self.recover()
        path.write_bytes(encoded(original))
        with patch.object(tx,'MAX_MANIFEST',10):
            with self.assertRaises(IdeaError): self.recover()
        with patch.object(tx,'MAX_JOURNAL_BYTES',1):
            with self.assertRaises(IdeaError): self.recover()

    def test_foreign_files_prevent_cleanup_and_are_never_removed(self):
        self.stop_at('complete')
        foreign=self.manifest_path().parent/'someone-else.txt'; foreign.write_bytes(b'preserve')
        with self.assertRaises(IdeaError): self.recover()
        self.assertEqual(foreign.read_bytes(),b'preserve')
        self.assertEqual((self.root/IDEA).read_bytes(),b'detail after')

    def test_immutable_evidence_never_replaced_and_sessions_are_mutable(self):
        self.publish({HISTORY:b'history',SESSION:b'first'},{HISTORY:None,SESSION:None}).raise_for_error()
        with self.assertRaises(IdeaError) as caught:
            self.publish({HISTORY:b'changed'},{HISTORY:digest(b'history')})
        self.assertEqual(caught.exception.code,'save_conflict')
        self.publish({HISTORY:b'history',SESSION:b'second'},{HISTORY:digest(b'history'),SESSION:digest(b'first')}).raise_for_error()
        self.assertEqual((self.root/HISTORY).read_bytes(),b'history')
        self.assertEqual((self.root/SESSION).read_bytes(),b'second')

    def test_metadata_evidence_exact_path_immutable_and_recoverable(self):
        path='history/idea_'+'3'*32+'/metadata/'+'a'*64+'.md'
        def checkpoint(phase):
            if phase=='prepared': raise Stop(phase)
        with self.assertRaises(Stop):
            self.publish({path:b'metadata evidence'},{path:None},_checkpoint=checkpoint)
        self.recover().raise_for_error()
        self.assertEqual((self.root/path).read_bytes(),b'metadata evidence')
        with self.assertRaises(IdeaError) as caught:
            self.publish({path:b'changed'},{path:digest(b'metadata evidence')})
        self.assertEqual(caught.exception.code,'save_conflict')
        self.publish({path:b'metadata evidence'},{path:digest(b'metadata evidence')}).raise_for_error()
        self.assertEqual((self.root/path).read_bytes(),b'metadata evidence')

    def test_metadata_evidence_malformed_digest_paths_and_symlink_refused(self):
        prefix='history/idea_'+'3'*32+'/metadata/'
        for suffix in ('a'*63+'.md','A'*64+'.md','g'*64+'.md','a'*64+'.json','../'+'a'*64+'.md','a'*64+'.md/extra'):
            with self.subTest(suffix=suffix), self.assertRaises(IdeaError):
                self.publish({prefix+suffix:b'bad'},{prefix+suffix:None})
        for path in ('history/idea_bad/metadata/'+'a'*64+'.md', 'history/backlog/metadata/'+'a'*64+'.md'):
            with self.assertRaises(IdeaError): self.publish({path:b'bad'},{path:None})
        parent=self.root/prefix; parent.mkdir(parents=True)
        link=parent/('a'*64+'.md')
        link.symlink_to(self.root/'IDEAS.md')
        with self.assertRaises(IdeaError):
            self.publish({prefix+link.name:b'bad'},{prefix+link.name:digest(b'index before')})
        self.assertEqual((self.root/'IDEAS.md').read_bytes(),b'index before')
        self.assertFalse((self.root/tx.JOURNAL).exists())

    def test_save_conflict_before_journal_creation(self):
        wrong=self.expected(); wrong[IDEA]=digest(b'stale')
        with self.assertRaises(IdeaError) as caught: self.publish(expected=wrong)
        self.assertEqual(caught.exception.code,'save_conflict')
        self.assertFalse((self.root/tx.JOURNAL).exists())

    def test_published_and_not_published_io_states_preserved(self):
        with patch.object(tx,'sync_directory',side_effect=OSError('preparation sync failed')):
            result=self.publish()
        self.assertEqual(result.publication,'not-published')
        self.assertFalse(result.committed)
        self.recover().raise_for_error()
        original=tx.atomic_write
        def fail_target(path,raw,immutable=False):
            if Path(path).name == IDEA:
                return platform.WriteResult('published',platform.UNCERTAIN,OSError('directory barrier failed'))
            return original(path,raw,immutable=immutable)
        with patch.object(tx,'atomic_write',side_effect=fail_target):
            result=self.publish()
        self.assertEqual(result.publication,'uncertain'); self.assertTrue(result.committed)
        with self.assertRaises(IdeaError) as caught: result.raise_for_error()
        self.assertEqual(caught.exception.code,'durability_uncertain')
        self.recover().raise_for_error()
        self.assertEqual((self.root/'IDEAS.md').read_bytes(),b'index after')

    def test_windows_weaker_grade_is_not_a_native_qualification(self):
        with patch.object(platform,'_WINDOWS',True):
            # Lock was acquired using real host semantics outside the grade patch.
            # Call directly here; qualification of msvcrt is the platform job.
            result=tx.publish(self.root,self.changes(),self.expected())
        result.raise_for_error()
        self.assertEqual(result.durability,platform.FILE_SYNCED_PROCESS_RECOVERY)

    def test_migration_exact_freeze_index_then_removal_crash_recovery(self):
        legacy=b'{"schema_version":1,"exact":"legacy bytes"}\r\n'
        (self.root/'state.json').write_bytes(legacy)
        changes=dict(self.changes(), **{tx.FROZEN:legacy,tx.RECEIPT:b'{"migration":"v1-to-v2"}'})
        expected=dict(self.expected(), **{tx.FROZEN:None,tx.RECEIPT:None})
        phases=[]
        def callback(phase):
            phases.append(phase)
            if phase=='published:state.json': raise Stop(phase)
        with self.assertRaises(Stop):
            self.publish(changes,expected,freeze_legacy=True,legacy_sha256=digest(legacy),_checkpoint=callback)
        self.assertEqual((self.root/tx.FROZEN).read_bytes(),legacy)
        self.assertFalse((self.root/'state.json').exists())
        published=[p for p in phases if p.startswith('published:')]
        self.assertEqual(published[-2:],['published:IDEAS.md','published:state.json'])
        self.recover().raise_for_error()
        self.assertFalse((self.root/'state.json').exists())
        self.assertEqual((self.root/tx.FROZEN).read_bytes(),legacy)

    def test_real_migration_kill_before_and_after_every_freeze_publication_boundary(self):
        phases=['staging_manifest','staged:4.before','prepared','published:'+IDEA,
                'published:'+tx.FROZEN,'published:'+tx.RECEIPT,'published:IDEAS.md',
                'published:state.json','verified','complete','cleaned:4.before','cleaned:manifest.json']
        for phase in phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); legacy=b'exact legacy bytes\r\n'
                (root/IDEA).write_bytes(b'detail before'); (root/'IDEAS.md').write_bytes(b'index before')
                (root/'state.json').write_bytes(legacy)
                child=subprocess.Popen([sys.executable,'-c',CHILD,str(SCRIPTS),str(root),phase,IDEA,'migration'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                try:
                    self.assertEqual(child.stdout.readline().strip(),phase)
                    child.kill(); child.communicate(timeout=3)
                finally:
                    if child.poll() is None: child.kill()
                    child.communicate(timeout=3)
                restart=subprocess.run([sys.executable,'-c',CHILD,str(SCRIPTS),str(root),'RECOVER',IDEA],capture_output=True,text=True,timeout=4)
                self.assertEqual(restart.returncode,0,restart.stderr)
                prepared=phases.index(phase)>=phases.index('prepared')
                self.assertEqual((root/'state.json').exists(),not prepared)
                if prepared:
                    self.assertEqual((root/tx.FROZEN).read_bytes(),legacy)
                    self.assertEqual((root/'IDEAS.md').read_bytes(),b'index after')
                else:
                    self.assertEqual((root/'state.json').read_bytes(),legacy)
                    self.assertFalse((root/tx.FROZEN).exists())

    def test_recovery_retries_failed_target_barriers_before_completing(self):
        original=tx.atomic_write
        def fail_after_replacement(path, raw, immutable=False):
            result=original(path,raw,immutable=immutable)
            if Path(path).name==IDEA:
                return platform.WriteResult('published',platform.UNCERTAIN,OSError('injected target barrier'))
            return result
        with patch.object(tx,'atomic_write',side_effect=fail_after_replacement):
            result=self.publish()
        self.assertTrue(result.committed)
        self.assertEqual((self.root/IDEA).read_bytes(),b'detail after')
        # Already-after target is not replaced, but its data/directory barriers
        # must run. A second barrier failure keeps the prepared journal intact.
        original_sync=tx.sync_directory
        def failed_directory(path):
            if Path(path)==self.root: raise OSError('repeated destination barrier')
            return original_sync(path)
        with patch.object(tx,'sync_directory',side_effect=failed_directory):
            failed=self.recover()
        self.assertEqual(failed.publication,'uncertain')
        self.assertTrue(self.manifest_path().exists())
        with patch.object(tx,'sync_directory',wraps=original_sync) as barriers:
            recovered=self.recover()
        recovered.raise_for_error()
        self.assertIn(self.root,[call.args[0] for call in barriers.call_args_list])
        self.assertEqual(recovered.durability,platform.FILE_AND_DIRECTORY_SYNCED if os.name!='nt' else platform.FILE_SYNCED_PROCESS_RECOVERY)
        self.assertEqual(list((self.root/tx.JOURNAL).iterdir()),[])

    def test_unreadable_prepared_manifest_reports_uncertainty(self):
        self.stop_at('prepared')
        original=tx._read
        def unreadable_manifest(path,limit=tx.MAX_STATE):
            if Path(path).name=='manifest.json': raise OSError('cannot read existing journal')
            return original(path,limit)
        with patch.object(tx,'_read',side_effect=unreadable_manifest):
            result=self.recover()
        self.assertEqual(result.publication,'uncertain')
        self.assertTrue(result.committed)
        self.assertTrue(self.manifest_path().exists())
        self.assertEqual((self.root/IDEA).read_bytes(),b'detail before')
        self.recover().raise_for_error()

    def test_receipts_and_aggregate_stage_bytes_are_bounded_before_writes(self):
        with self.assertRaises(IdeaError):
            self.publish({SESSION:b'x'*(tx.MAX_INPUT+1)},{SESSION:None})
        self.assertFalse((self.root/tx.JOURNAL).exists())
        with patch.object(tx,'MAX_JOURNAL_BYTES',2):
            with self.assertRaises(IdeaError): self.publish()
        self.assertFalse((self.root/tx.JOURNAL).exists())

    def test_migration_refuses_mismatched_source_and_preserves_everything(self):
        (self.root/'state.json').write_bytes(b'actual legacy')
        changes=dict(self.changes(),**{tx.FROZEN:b'wrong',tx.RECEIPT:b'receipt'})
        expected=dict(self.expected(),**{tx.FROZEN:None,tx.RECEIPT:None})
        with self.assertRaises(IdeaError): self.publish(changes,expected,freeze_legacy=True,legacy_sha256=digest(b'actual legacy'))
        self.assertEqual((self.root/'state.json').read_bytes(),b'actual legacy')
        self.assertFalse((self.root/tx.JOURNAL).exists())


MOVED = 'history/idea_' + '1' * 32 + '/moved.md'


def moved_pointer(detail=b'detail before'):
    """A valid moved pointer for IDEA recording the exact bytes being removed."""
    import idea_markdown as md
    from idea_domain import snapshot
    key = IDEA[:-3]
    words = 'idea words'
    value = dict(idea_id=key, revision=1, status='archived', origin=dict(text=words, sha256=digest(words.encode()), actor='a', timestamp='t'),
                 shape=None, ratings=None, assessments=[], proposals=[], executions=[],
                 plans=[dict(plan_id='plan_' + '3' * 32, idea_id=key, idea_revision=1, path='/s/p.md', source_path='/w/p.md',
                             sha256='a' * 64, actor='a', timestamp='t', validation={})])
    return md.encode_moved(key, idea_revision=1, plan_id='plan_' + '3' * 32, workspace=dict(name='W', path='/w'),
                           home_path='/w/ideas/' + IDEA, moved_sha256=digest(detail), actor='a', timestamp='t',
                           frozen=dict(idea=value, extensions={}))


class MoveOutManifestTests(TransactionTests):
    """move-out removes one idea detail file, only beside its own pointer and the index."""

    def move_changes(self, pointer=None):
        return {MOVED: moved_pointer() if pointer is None else pointer, 'IDEAS.md': b'index after'}

    def move_expected(self):
        return {MOVED: None, 'IDEAS.md': digest(b'index before')}

    def move(self, **kwargs):
        return self.publish(self.move_changes(), self.move_expected(), move_out=IDEA, **kwargs)

    def stop_move_at(self, phase):
        def callback(actual):
            if actual == phase:
                raise Stop(actual)
        with self.assertRaises(Stop):
            self.move(_checkpoint=callback)

    def test_move_out_publishes_pointer_and_index_before_removing_the_detail_file(self):
        phases = []
        self.move(_checkpoint=phases.append).raise_for_error()
        published = [p for p in phases if p.startswith('published:')]
        self.assertEqual(published[-2:], ['published:IDEAS.md', 'published:' + IDEA])
        self.assertFalse((self.root / IDEA).exists())
        self.assertEqual((self.root / 'IDEAS.md').read_bytes(), b'index after')
        self.assertEqual((self.root / MOVED).read_bytes(), moved_pointer())
        self.assertEqual(os.listdir(self.root / tx.JOURNAL), [])

    def test_crash_at_every_checkpoint_recovers_to_before_or_fully_moved(self):
        phases = []
        self.move(_checkpoint=phases.append).raise_for_error()
        self.assertGreater(len(phases), 8)
        for crash, phase in enumerate(phases):
            with self.subTest(phase=phase):
                self.setUp()
                for leftover in (self.root / MOVED, ):
                    if leftover.exists():
                        leftover.unlink()
                seen = []
                def callback(actual):
                    seen.append(actual)
                    if len(seen) == crash + 1:
                        raise Stop(actual)
                with self.assertRaises(Stop):
                    self.move(_checkpoint=callback)
                self.recover().raise_for_error()
                moved = not (self.root / IDEA).exists()
                # Reaching 'prepared' commits the transaction; earlier never moved.
                self.assertEqual(moved, phases.index('prepared') <= crash)
                if moved:
                    self.assertEqual((self.root / 'IDEAS.md').read_bytes(), b'index after')
                    self.assertEqual((self.root / MOVED).read_bytes(), moved_pointer())
                else:
                    self.assertEqual((self.root / IDEA).read_bytes(), b'detail before')
                    self.assertEqual((self.root / 'IDEAS.md').read_bytes(), b'index before')
                    self.assertFalse((self.root / MOVED).exists())
                self.assertEqual(os.listdir(self.root / tx.JOURNAL), [])

    def test_publish_refuses_move_out_without_pointer_index_or_matching_hash(self):
        for changes, expected, move_out in (
                ({'IDEAS.md': b'index after'}, {'IDEAS.md': digest(b'index before')}, IDEA),
                ({MOVED: moved_pointer(b'other bytes'), 'IDEAS.md': b'index after'}, self.move_expected(), IDEA),
                ({MOVED: b'not a pointer', 'IDEAS.md': b'index after'}, self.move_expected(), IDEA),
                (self.move_changes(), self.move_expected(), 'IDEAS.md'),
                (self.move_changes(), self.move_expected(), 'history/idea_' + '1' * 32 + '/r1.md'),
                (self.move_changes(), self.move_expected(), 'idea_bad.md'),
                (dict(self.move_changes(), **{IDEA: b'x'}), dict(self.move_expected(), **{IDEA: digest(b'detail before')}), IDEA),
                ({MOVED: moved_pointer()}, {MOVED: None}, IDEA)):
            with self.subTest(move_out=move_out, paths=sorted(changes)):
                with self.assertRaises(IdeaError):
                    self.publish(changes, expected, move_out=move_out)
        self.assertEqual((self.root / IDEA).read_bytes(), b'detail before')
        self.assertFalse((self.root / tx.JOURNAL).exists())

    def test_move_out_is_refused_with_migration(self):
        legacy = b'legacy'
        (self.root / 'state.json').write_bytes(legacy)
        changes = dict(self.move_changes(), **{tx.FROZEN: legacy, tx.RECEIPT: b'r'})
        expected = dict(self.move_expected(), **{tx.FROZEN: None, tx.RECEIPT: None})
        with self.assertRaises(IdeaError):
            self.publish(changes, expected, freeze_legacy=True, legacy_sha256=digest(legacy), move_out=IDEA)

    def test_prepared_manifest_rules_refuse_every_other_deletion(self):
        self.stop_move_at('prepared')
        path, original = self.manifest()
        def entry(value, name):
            return next(e for e in value['entries'] if e['path'] == name)
        def drop_pointer(m):
            m['entries'] = [e for e in m['entries'] if e['path'] != MOVED]
        def drop_index(m):
            m['entries'] = [e for e in m['entries'] if e['path'] != 'IDEAS.md']
        def second_move(m):
            extra = copy.deepcopy(entry(m, IDEA))
            extra['path'] = 'idea_' + '2' * 32 + '.md'
            m['entries'].append(extra)
        mutations = {
            'no pointer': drop_pointer,
            'no index': drop_index,
            'two moves': second_move,
            'index deletion': lambda m: entry(m, 'IDEAS.md').update(operation='move-out', after=None, after_size=0, immutable=False),
            'evidence deletion': lambda m: entry(m, MOVED).update(operation='move-out', after=None, after_size=0, immutable=False),
            'move with after': lambda m: entry(m, IDEA).update(after=digest(b'x'), after_size=1),
            'move without before': lambda m: entry(m, IDEA).update(before=None, before_size=0),
            'unknown operation': lambda m: entry(m, IDEA).update(operation='delete'),
            'migration flag': lambda m: m.update(migration=True),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                value = copy.deepcopy(original)
                mutate(value)
                path.write_bytes(encoded(value))
                with self.assertRaises(IdeaError):
                    self.recover()
                self.assertEqual((self.root / IDEA).read_bytes(), b'detail before')
                self.assertEqual((self.root / 'IDEAS.md').read_bytes(), b'index before')

    def test_recovery_refuses_a_pointer_whose_recorded_hash_is_not_the_removed_file(self):
        self.stop_move_at('prepared')
        path, original = self.manifest()
        value = copy.deepcopy(original)
        forged = moved_pointer(b'some other file')
        n = next(i for i, e in enumerate(value['entries']) if e['path'] == MOVED)
        value['entries'][n].update(after=digest(forged), after_size=len(forged))
        (path.parent / (str(n) + '.after')).unlink()
        (path.parent / (str(n) + '.after')).write_bytes(forged)
        path.write_bytes(encoded(value))
        with self.assertRaises(IdeaError) as caught:
            self.recover()
        self.assertEqual(caught.exception.code, 'recovery_conflict')
        self.assertEqual((self.root / IDEA).read_bytes(), b'detail before')

    def test_recovery_refuses_an_edited_detail_file(self):
        self.stop_move_at('prepared')
        (self.root / IDEA).write_bytes(b'edited meanwhile')
        with self.assertRaises(IdeaError):
            self.recover()
        self.assertEqual((self.root / IDEA).read_bytes(), b'edited meanwhile')


# Inherited publication tests run once, in TransactionTests.
for _name in [n for n in dir(TransactionTests) if n.startswith('test_')]:
    setattr(MoveOutManifestTests, _name, None)


if __name__=='__main__': unittest.main()
