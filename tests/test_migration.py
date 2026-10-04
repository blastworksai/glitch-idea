"""portable legacy preservation, evidence CAS and killed publication."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0,str(SCRIPTS))
import idea_store as storage
import idea_markdown as md
import idea_migration as migration
import idea_transactions as tx
from idea_domain import IdeaError, assessment, digest, encoded, snapshot
from test_markdown import metadata_fixture, KEY

STAMP = '2026-10-01T00:00:00Z'
SECOND = 'idea_'+'7'*32


def seed(root):
    """Complete v1 domain fixture; no ignored files or old binary dependency."""
    root.mkdir(parents=True,exist_ok=True)
    state = metadata_fixture()
    current = state['ideas'][KEY]
    current['plans'][0]['path'] = str(root/'plan-evidence'/(current['plans'][0]['plan_id']+'.md'))
    current['ratings'] = dict(urgency=8,importance=9,actor='operator',timestamp=STAMP)
    current['shape'] = dict(outcome='Useful outcome',scope='capability',scope_reason='Bounded',alternatives=[{'route':'manual','reason':'Slow'}],method='adaptive-slices',method_reason='Learn',assumptions=['Unknown'],learning=['Observe'],next_slice='First slice')
    inputs = [('wsjf',dict(value=4,time_criticality=None,enablement=2,effort=3)),('rice',dict(reach=10,impact=2,confidence=.5,effort=2)),('kano',dict(category='delighter',hypothesis=True))]
    for n,(method,values) in enumerate(inputs,2):
        value = assessment(dict(method=method,version='1',basis='Fixture',provenance='Observed',assumptions=['Unknown'],confidence='medium',inputs=values))
        value.update(assessment_id='assessment_'+str(n)*32,actor='Operator',timestamp=STAMP)
        current['assessments'].append(value)
        current['revision'] = n
        current['status'] = 'active'
        current['revisions'].append(snapshot(current,'Operator','assess'))
    other = copy.deepcopy(current)
    other.update(idea_id=SECOND,revision=1,status='active',shape=None,ratings=None,assessments=[],proposals=[],plans=[],executions=[],revisions=[])
    other['revisions']=[snapshot(other,'operator','capture')]
    state['ideas'][SECOND]=other
    state.update(order=[SECOND,KEY],transaction_revision=12,backlog_revision=3)
    choice=copy.deepcopy(current['proposals'][0]); choice.pop('proposal_id')
    choice['accepted_backlog_revision']=2
    state['placements']=[choice]
    storage.validate(state)
    raw=json.dumps(state,ensure_ascii=False,indent=3).replace('\n','\r\n').encode()+b'\r\n'
    files={'state.json':raw}
    for idea in state['ideas'].values():
        for plan in idea['plans']: files['plan-evidence/'+plan['plan_id']+'.md']=plan['content'].encode()
    for key,value in state['archives'].items(): files['archive/'+key]=encoded(value)
    for relative,value in files.items():
        path=root/relative; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(value)
    return state,files


def rate(state):
    idea=state['ideas'][KEY]
    idea['ratings']['urgency']=7
    idea['revision']+=1
    idea['revisions'].append(snapshot(idea,'operator','rate'))


CHILD = r'''import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import idea_store as storage
original=storage.transactions.publish
phase=sys.argv[3]; signal=Path(sys.argv[4])
def publish(*args,**kwargs):
    def checkpoint(actual):
        if actual==phase:
            signal.write_text(actual); time.sleep(60)
    return original(*args,**kwargs,_checkpoint=checkpoint)
storage.transactions.publish=publish
storage.Store(Path(sys.argv[2])).create_session()
'''


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'Café spaced store'
        self.baseline,self.old=seed(self.root)
        self.store=storage.Store(self.root,observer='Operator')

    def read(self):
        with storage.Store(self.root).transaction() as state: return copy.deepcopy(state)

    def migrate(self):
        with self.store.transaction(write=True) as state:
            rate(state); self.store.commit(state)
        return state

    def test_read_open_noop_and_rejected_commit_do_not_migrate(self):
        self.assertEqual(self.read(),self.baseline)
        with self.store.transaction(write=True): pass
        with self.store.transaction(write=True) as state: self.assertIsNone(self.store.commit(state))
        with self.store.transaction(write=True) as state:
            state['ideas'][KEY]['origin']['text']='changed'
            with self.assertRaises(IdeaError): self.store.commit(state)
        self.assertEqual((self.root/'state.json').read_bytes(),self.old['state.json'])
        self.assertFalse((self.root/'IDEAS.md').exists())

    def test_full_preservation_mutation_and_later_immutable_evidence(self):
        after=self.migrate()
        self.assertEqual(self.read(),after)
        self.assertEqual(after['order'],self.baseline['order'])
        self.assertEqual(after['placements'],self.baseline['placements'])
        current=after['ideas'][KEY]
        for field in ('origin','proposals','plans','executions'):
            self.assertEqual(current[field],self.baseline['ideas'][KEY][field])
        self.assertEqual(current['revisions'][:-1],self.baseline['ideas'][KEY]['revisions'])
        self.assertEqual(after['transaction_revision'],13)
        self.assertFalse((self.root/'state.json').exists())
        for path,raw in self.old.items():
            self.assertEqual((self.root/(tx.FROZEN if path=='state.json' else path)).read_bytes(),raw)
        receipt=(self.root/tx.RECEIPT).read_bytes(); frozen=(self.root/tx.FROZEN).read_bytes()
        self.assertEqual(migration.verify(receipt,frozen)['target_transaction_revision'],13)
        self.assertNotIn('workflow',current)
        with self.store.transaction(write=True) as state:
            rate(state); self.store.commit(state)
        self.assertEqual((self.root/tx.RECEIPT).read_bytes(),receipt)
        self.assertEqual((self.root/tx.FROZEN).read_bytes(),frozen)
        self.assertEqual(self.read()['transaction_revision'],14)

    def test_explicit_session_migrates_without_domain_counter_and_receipt_noops_preserve_yaml(self):
        sid=self.store.create_session()
        self.assertEqual(self.read(),self.baseline)
        self.assertEqual(migration.verify((self.root/tx.RECEIPT).read_bytes(),(self.root/tx.FROZEN).read_bytes())['target_transaction_revision'],12)
        detail=self.root/(KEY+'.md')
        raw=detail.read_bytes().replace(b'kind: idea',b'kind: idea # retained harmless comment')
        detail.write_bytes(raw)
        self.store.create_session()
        result=self.store.mutate(sid,'noop',{},lambda state:{'value':'unchanged'})
        self.assertEqual(result['write_state'],'no_op')
        self.assertEqual(detail.read_bytes(),raw)
        self.assertEqual(self.read(),self.baseline)

    def test_missing_or_altered_legacy_views_require_repair(self):
        for relative in [path for path in self.old if path!='state.json']:
            with self.subTest(path=relative):
                original=(self.root/relative).read_bytes()
                for value in (None,b'altered'):
                    if value is None: (self.root/relative).unlink()
                    else: (self.root/relative).write_bytes(value)
                    with self.assertRaises(IdeaError) as caught: self.store.create_session()
                    self.assertEqual(caught.exception.code,'migration_view_required')
                    self.assertFalse((self.root/'IDEAS.md').exists())
                    (self.root/relative).write_bytes(original)

    def test_first_commit_adds_metadata_and_archive_atomically(self):
        with self.store.transaction(write=True) as state:
            current=state['ideas'][KEY]
            proposal=copy.deepcopy(current['proposals'][0])
            proposal.update(proposal_id='proposal_'+'8'*32,idea_revision=current['revision'],
                            snapshot=dict(ratings=copy.deepcopy(current['ratings']),assessments=copy.deepcopy(current['assessments'])))
            current['proposals'].append(proposal)
            plan=copy.deepcopy(current['plans'][0]); pid='plan_'+'9'*32
            content='# Next accepted slice\r\n'
            plan.update(plan_id=pid,idea_revision=current['revision'],path=str(self.root/'plan-evidence'/(pid+'.md')),
                        content=content,sha256=digest(content.encode()))
            current['plans'].append(plan); current['status']='archived'
            archive_key=KEY+'/r'+str(current['revision'])+'.json'
            archive=dict(idea_id=KEY,origin=copy.deepcopy(current['origin']),revision=copy.deepcopy(current['revisions'][-1]))
            state['archives'][archive_key]=archive
            execution=copy.deepcopy(current['executions'][0])
            execution.update(plan_id=pid,attempt_id='next-attempt')
            execution['receipt'].update(plan_id=pid,attempt_id='next-attempt')
            execution['sha256']=digest(encoded(execution['receipt']))
            current['executions'].append(execution)
            self.store.commit(state)
        self.assertEqual(self.read(),state)
        self.assertEqual(state['transaction_revision'],13)
        self.assertEqual((self.root/'archive'/archive_key).read_bytes(),encoded(archive))
        self.assertEqual((self.root/'plan-evidence'/(pid+'.md')).read_bytes(),content.encode())
        for path,raw in self.old.items():
            self.assertEqual((self.root/(tx.FROZEN if path=='state.json' else path)).read_bytes(),raw)

    def test_prepared_migration_conflict_or_missing_stage_never_rolls_back(self):
        class Stop(Exception): pass
        for kind in ('target','stage'):
            with self.subTest(kind=kind):
                root=Path(self.temp.name)/('conflict-'+kind); baseline,old=seed(root)
                original=tx.publish
                def publish(*args,**kwargs):
                    def checkpoint(phase):
                        if phase=='prepared': raise Stop()
                    return original(*args,**kwargs,_checkpoint=checkpoint)
                with patch.object(storage.transactions,'publish',side_effect=publish):
                    with self.assertRaises(Stop): storage.Store(root).create_session()
                manifests=list((root/tx.JOURNAL).glob('*/manifest.json'))
                self.assertEqual(len(manifests),1)
                if kind=='target': (root/(KEY+'.md')).write_bytes(b'outsider authority')
                else: (manifests[0].parent/'0.after').unlink()
                with self.assertRaises(IdeaError) as caught:
                    with storage.Store(root).transaction(): pass
                self.assertEqual(caught.exception.code,'recovery_conflict')
                self.assertEqual((root/'state.json').read_bytes(),old['state.json'])
                self.assertTrue(manifests[0].exists())
                if kind=='target': self.assertEqual((root/(KEY+'.md')).read_bytes(),b'outsider authority')

    def test_legacy_observed_bytes_cas_and_partial_authority(self):
        with self.store.transaction(write=True) as state:
            rate(state); (self.root/'state.json').write_bytes(self.old['state.json']+b' ')
            with self.assertRaises(IdeaError) as caught: self.store.commit(state)
            self.assertEqual(caught.exception.code,'save_conflict')
        self.assertFalse((self.root/'IDEAS.md').exists())
        (self.root/'state.json').write_bytes(self.old['state.json'])
        (self.root/(KEY+'.md')).write_bytes(b'partial')
        with self.assertRaises(IdeaError) as caught: self.store.create_session()
        self.assertEqual(caught.exception.code,'ambiguous_store')

    def test_migrated_missing_corrupt_or_restored_authority_fails_closed(self):
        self.migrate()
        for relative in (tx.FROZEN,tx.RECEIPT):
            path=self.root/relative; original=path.read_bytes()
            for value in (None,b'wrong'):
                if value is None: path.unlink()
                else: path.write_bytes(value)
                with self.assertRaises(IdeaError): self.read()
                path.write_bytes(original)
        (self.root/'state.json').write_bytes(self.old['state.json'])
        with self.assertRaises(IdeaError) as caught: self.read()
        self.assertEqual(caught.exception.code,'ambiguous_store')
        (self.root/'state.json').unlink()
        (self.root/tx.FROZEN).unlink(); (self.root/tx.RECEIPT).unlink()
        with self.assertRaises(IdeaError): self.read()

    def test_strict_receipt_and_unmarked_evidence(self):
        self.migrate()
        raw=(self.root/tx.RECEIPT).read_bytes(); receipt=json.loads(raw)
        for field,value in [('schema_version',True),('source_transaction_revision',True),('target_transaction_revision',99),('legacy_sha256','0'*64),('frozen_path','state.json'),('actor','')]:
            modified=dict(receipt); modified[field]=value
            with self.assertRaises(IdeaError): migration.verify(encoded(modified),self.old['state.json'])
        modified=dict(receipt,extra=True)
        with self.assertRaises(IdeaError): migration.verify(encoded(modified),self.old['state.json'])
        index=self.root/'IDEAS.md'; doc=md.decode_index(index.read_bytes())
        doc.metadata['extensions'].pop(migration.MARKER)
        index.write_bytes(md.encode_document(doc.metadata,doc.body))
        with self.assertRaises(IdeaError): self.read()

    def test_later_commit_cas_rechecks_frozen_and_receipt(self):
        self.migrate()
        for relative in (tx.FROZEN,tx.RECEIPT):
            path=self.root/relative; original=path.read_bytes()
            with self.store.transaction(write=True) as state:
                path.write_bytes(original+b' ')
                with self.assertRaises(IdeaError) as caught: self.store.commit(state)
                self.assertEqual(caught.exception.code,'save_conflict')
            path.write_bytes(original)

    @unittest.skipUnless(hasattr(Path,'symlink_to'), 'Symlinks unavailable')
    def test_symlink_source_and_frozen_fail_closed(self):
        source=self.root/'state.json'; outside=Path(self.temp.name)/'outside.json'
        outside.write_bytes(source.read_bytes()); source.unlink()
        try: source.symlink_to(outside)
        except OSError as exc: self.skipTest(str(exc))
        with self.assertRaises(IdeaError): self.read()
        source.unlink(); source.write_bytes(outside.read_bytes()); self.migrate()
        frozen=self.root/tx.FROZEN; frozen.unlink(); frozen.symlink_to(outside)
        with self.assertRaises(IdeaError): self.read()

    def test_actual_process_kill_restart_across_migration_boundaries(self):
        metadata_path=next(path for path in md.encode_state(self.baseline) if '/metadata/' in path)
        phases=['staging_manifest','staged:0.after','prepared','published:'+KEY+'.md','published:'+metadata_path,
                'published:history/'+KEY+'/r1.md','published:'+tx.FROZEN,'published:'+tx.RECEIPT,
                'published:IDEAS.md','published:state.json','verified','complete','cleaned:manifest.json']
        for n,phase in enumerate(phases):
            with self.subTest(phase=phase):
                root=Path(self.temp.name)/('crash-'+str(n)); baseline,old=seed(root)
                signal=Path(self.temp.name)/('signal-'+str(n))
                child=subprocess.Popen([sys.executable,'-c',CHILD,str(SCRIPTS),str(root),phase,str(signal)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                try:
                    deadline=time.monotonic()+8
                    while not signal.exists() and child.poll() is None and time.monotonic()<deadline: time.sleep(.02)
                    if not signal.exists():
                        child.kill(); out,err=child.communicate(timeout=3)
                        self.fail('Checkpoint unreachable '+phase+': '+err.decode())
                    child.kill(); child.communicate(timeout=3)
                finally:
                    if child.poll() is None: child.kill(); child.wait(timeout=3)
                with storage.Store(root).transaction() as state: self.assertEqual(state,baseline)
                if phase in ('staging_manifest','staged:0.after'):
                    self.assertEqual((root/'state.json').read_bytes(),old['state.json'])
                    self.assertFalse((root/'IDEAS.md').exists())
                else:
                    self.assertFalse((root/'state.json').exists())
                    self.assertEqual((root/tx.FROZEN).read_bytes(),old['state.json'])
                    migration.verify((root/tx.RECEIPT).read_bytes(),old['state.json'])
                self.assertEqual(list((root/tx.JOURNAL).iterdir()),[])


if __name__=='__main__': unittest.main()
