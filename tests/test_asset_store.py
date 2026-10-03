"""Atomic asset authority, local integrity and recovery. """
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0,str(SCRIPTS))
import idea_store as storage
import idea_markdown as md
import idea_transactions as tx
import idea_asset_evidence as codec
from idea_domain import IdeaError
from idea_workflow import source_digest
from test_workflow import complete

ACTOR = 'Operator'
KEY = complete()['idea_id']
DATA = b'fixture!'


def identifier(prefix, number):
    return prefix+'_'+format(number,'032x')


def intent(sid, number=1):
    return dict(schema_version=1,kind='upload-intent',idea_id=KEY,source_revision=6,
        actor=ACTOR,timestamp='2026-10-02T00:00:00Z',upload_id=identifier('upload',number),
        asset_id=identifier('asset',number),session_id=sid,name='Café 💡.png',declared_type='image/png',size=len(DATA))


def completion(sid, number=1):
    value = intent(sid,number)
    value.update(kind='asset',blob_path=codec.blob_path(value['asset_id']),validated_type='image/png',
                 sha256=hashlib.sha256(DATA).hexdigest())
    return value


def design_set(state, sid):
    asset = completion(sid); source = {}
    for step in ('capture','shape'):
        record = state['ideas'][KEY]['workflow']['steps'][step]
        revision = record['acceptance']['accepted_revision']
        source[step] = dict(revision=revision,digest=source_digest(step,revision,{step:record['fields']}))
    return dict(schema_version=1,kind='design-set',idea_id=KEY,source_revision=state['ideas'][KEY]['revision'],
        actor=ACTOR,timestamp='2026-10-02T00:00:00Z',set_id=identifier('set',1),session_id=sid,source=source,
        source_digest=codec.source_digest(source),members=[dict(asset_id=asset['asset_id'],name=asset['name'],
        type=asset['validated_type'],size=asset['size'],sha256=asset['sha256'])])


def seed(root):
    store = storage.Store(root,observer=ACTOR); sid = store.create_session()
    with store.transaction(write=True) as state:
        state['ideas'][KEY] = complete(); state['order'] = [KEY]; state['backlog_revision'] = 1
        store.commit(state)
    return sid


def publish_intent(root,sid):
    return storage.Store(root,observer=ACTOR).mutate_assets(sid,'intent-1',{'operation':'intent','number':1},
        lambda state:{'idea_id':KEY},prepare_records=lambda state:[intent(sid)])


CHILD = r'''import os,sys
sys.path.insert(0,sys.argv[1]);sys.path.insert(0,sys.argv[2])
import idea_store as storage
from test_asset_store import publish_intent
root,sid,phase=sys.argv[3:6]
original=storage.transactions.publish
def publish(*args,**kwargs):
    def cut(actual):
        if (actual==phase or phase=='staged' and actual.startswith('staged:')
            or phase=='evidence' and actual.startswith('published:assets/evidence/')
            or phase=='detail' and actual.startswith('published:idea_')
            or phase=='receipt' and actual.startswith('published:session-recovery/')): os._exit(73)
    return original(*args,**kwargs,_checkpoint=cut)
storage.transactions.publish=publish
publish_intent(root,sid)
'''


class AssetStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'Café Store'; self.sid = seed(self.root)
        self.store = storage.Store(self.root,observer=ACTOR); self.original = self.show()

    def show(self):
        with storage.Store(self.root).transaction() as state: return copy.deepcopy(state)

    def files(self):
        return {p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*')
                if p.is_file() and not p.is_symlink() and tx.JOURNAL not in p.parts}

    def blob(self,number=1,data=DATA):
        path = self.root/codec.blob_path(identifier('asset',number)); path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data); return path

    def call(self,records=None,rid='intent-1',payload=None,business=None,factory=None):
        return self.store.mutate_assets(self.sid,rid,{'operation':'intent','number':1} if payload is None else payload,
            (lambda state:{'idea_id':KEY}) if business is None else business,
            prepare_records=factory if factory is not None else (None if records is None else lambda state:records))

    def upload(self):
        self.call([intent(self.sid)]); self.blob()
        return self.call([completion(self.sid)],'upload-bytes:'+intent(self.sid)['upload_id'],
            {'operation':'upload-bytes','size':len(DATA),'sha256':hashlib.sha256(DATA).hexdigest()})

    def inventory(self):
        with self.store.transaction() as state: return self.store.asset_inventory(state,KEY)

    def test_intent_atomic_changes_preserve_idea_revision_and_index(self):
        before = self.files(); result = self.call([intent(self.sid)]); after = self.files()
        expected = copy.deepcopy(self.original); expected['transaction_revision'] += 1
        self.assertEqual(self.show(),expected)
        link = result['asset_records'][0]
        self.assertEqual({p for p in set(before)|set(after) if before.get(p)!=after.get(p)},
                         {KEY+'.md',link['path'],'session-recovery/'+self.sid+'.json'})
        self.assertEqual(codec.decode_record(after[link['path']],link=link),intent(self.sid))
        self.assertEqual(self.store.request_result(self.sid,'intent-1'),result)

    def test_multiple_records_verified_before_business_and_published_together(self):
        self.blob()
        def business(state):
            entries = self.store.asset_records(state,KEY,with_links=True)
            self.assertEqual([x['record']['kind'] for x in entries],['upload-intent','asset'])
            self.assertEqual(entries[-1]['blob'],dict(path=completion(self.sid)['blob_path'],size=len(DATA),
                                                    sha256=completion(self.sid)['sha256']))
            return {'idea_id':KEY}
        result = self.call([intent(self.sid),completion(self.sid)],business=business)
        self.assertEqual(len(result['asset_records']),2); self.assertEqual(self.inventory()['orphans']['count'],0)

    def test_append_missing_or_mismatched_business_idea_publishes_nothing(self):
        before = self.files(); original = self.show()
        for business in ({}, {'idea_id':identifier('idea',2)}, {'idea_id':None}, {'idea_id':[]}):
            with self.subTest(business=business):
                with self.assertRaises(IdeaError) as caught:
                    self.call([intent(self.sid)],business=lambda state:copy.deepcopy(business))
                self.assertEqual(caught.exception.code,'invalid_handler')
                self.assertEqual(self.files(),before)
                self.assertEqual(self.show(),original)  # Fresh Store/restart remains readable.
                self.assertEqual(self.inventory()['records'],[])
                with self.assertRaises(IdeaError) as receipt:
                    self.store.request_result(self.sid,'intent-1')
                self.assertEqual(receipt.exception.code,'request_not_found')

    def test_mixed_idea_append_batch_refused_before_publication(self):
        other = identifier('idea',2)
        with self.store.transaction(write=True) as state:
            idea = complete(); idea['idea_id'] = other
            state['ideas'][other] = idea; state['order'].append(other); state['backlog_revision'] += 1
            self.store.commit(state)
        records = [intent(self.sid),intent(self.sid,2)]; records[1]['idea_id'] = other
        before = self.files(); original = self.show()
        with self.assertRaises(IdeaError) as caught: self.call(records)
        self.assertEqual(caught.exception.code,'invalid_handler')
        self.assertEqual(self.files(),before); self.assertEqual(self.show(),original)
        with self.store.transaction() as state:
            self.assertEqual(self.store.asset_records(state,KEY),[])
            self.assertEqual(self.store.asset_records(state,other),[])

    def test_no_append_does_not_require_business_idea_identity(self):
        result = self.call(business=lambda state:{'note':'No records'})
        self.assertNotIn('idea_id',result); self.assertEqual(result['asset_records'],[])
        ordinary = self.store.mutate(self.sid,'generic-no-idea',{},lambda state:{})
        self.assertNotIn('idea_id',ordinary); self.assertNotIn('asset_records',ordinary)

    def test_replay_precedes_factory_business_and_candidate_blob_validation(self):
        result = self.call([intent(self.sid)]); before = self.files()
        with patch.object(self.store,'_stage_assets',side_effect=AssertionError('new validation')):
            replay = self.call(business=lambda state:self.fail('business replay'),factory=lambda state:self.fail('factory replay'))
        self.assertEqual(replay,result); self.assertEqual(self.files(),before)
        replay['asset_records'].clear(); self.assertEqual(self.store.request_result(self.sid,'intent-1'),result)

    def test_conflicting_replay_refused_without_factory(self):
        self.call([intent(self.sid)]); before = self.files()
        with self.assertRaises(IdeaError) as caught:
            self.call(payload={'different':True},factory=lambda state:self.fail('factory'))
        self.assertEqual(caught.exception.code,'request_conflict'); self.assertEqual(self.files(),before)

    def test_complete_cross_intent_identity_and_source_checks(self):
        self.call([intent(self.sid)]); self.blob(); before = self.files()
        changes = dict(upload_id=identifier('upload',2),asset_id=identifier('asset',2),
            session_id=identifier('session',2),source_revision=5,name='Other.png',declared_type='image/jpeg',size=7)
        for field,value in changes.items():
            with self.subTest(field=field):
                record = completion(self.sid); record[field] = value
                with self.assertRaises(IdeaError): self.call([record],'bad-'+field)
                self.assertEqual(self.files(),before)

    def test_completion_requires_intent_and_duplicate_identity_is_refused(self):
        self.blob(); before = self.files()
        with self.assertRaises(IdeaError): self.call([completion(self.sid)])
        with self.assertRaises(IdeaError) as caught: self.call([intent(self.sid),intent(self.sid)])
        self.assertEqual(caught.exception.code,'request_conflict')
        unknown = intent(self.sid); unknown['idea_id'] = identifier('idea',2)
        with self.assertRaises(IdeaError): self.call([unknown])
        self.assertEqual(self.files(),before)

    def test_prepared_set_visible_then_business_failure_publishes_nothing(self):
        self.upload(); self.blob(2); before = self.files(); original = self.show()
        def business(state):
            records = self.store.asset_records(state,KEY)
            self.assertEqual(records[-1]['kind'],'design-set')
            records[-1]['members'].clear()
            self.assertEqual(len(self.store.asset_records(state,KEY)[-1]['members']),1)
            state['backlog_revision'] += 1
            raise IdeaError('fixture_failure','Refuse after seeing set')
        with self.assertRaises(IdeaError):
            self.call(rid='set-1',factory=lambda state:[design_set(state,self.sid)],business=business)
        self.assertEqual(self.files(),before); self.assertEqual(self.show(),original)
        self.assertEqual(len(self.inventory()['records']),2); self.assertTrue((self.root/codec.blob_path(identifier('asset',2))).exists())
        result = self.call(rid='set-1',factory=lambda state:[design_set(state,self.sid)])
        self.assertEqual(len(result['asset_records']),1)

    def test_set_member_snapshot_must_match_complete_asset(self):
        self.upload(); before = self.files()
        for field,value in dict(asset_id=identifier('asset',9),name='Changed.png',type='image/jpeg',size=7,sha256='0'*64).items():
            with self.subTest(field=field):
                def factory(state):
                    record = design_set(state,self.sid); record['members'][0][field] = value; return [record]
                with self.assertRaises(IdeaError): self.call(rid='bad-set-'+field,factory=factory)
        self.assertEqual(self.files(),before)

    def test_set_exact_publication_receipt_required_after_restart(self):
        self.upload()
        self.call(rid='set-1',factory=lambda state:[design_set(state,self.sid)])
        self.assertEqual(self.inventory()['records'][-1]['record']['kind'],'design-set')
        path = self.root/'session-recovery'/(self.sid+'.json')
        record = json.loads(path.read_bytes()); original = copy.deepcopy(record)
        record['receipts']['set-1']['result']['idea_id'] = identifier('idea',2)
        path.write_bytes(storage.encoded(record))
        with self.assertRaises(IdeaError): self.show()
        record = copy.deepcopy(original); record['receipts']['set-1']['result']['asset_records'] = []
        path.write_bytes(storage.encoded(record))
        with self.assertRaises(IdeaError): self.show()
        path.write_bytes(storage.encoded(original)); before = self.files()
        def business(state):
            changed = copy.deepcopy(original); changed['receipts']['set-1']['result']['asset_records'] = []
            path.write_bytes(storage.encoded(changed)); return {'idea_id':KEY}
        with self.assertRaises(IdeaError): self.call(rid='set-cas',business=business)
        path.write_bytes(storage.encoded(original)); self.assertEqual(self.files(),before)

    def test_offline_canonical_set_with_valid_members_has_no_publication_witness(self):
        self.upload(); state = self.show(); record = design_set(state,self.sid)
        raw = codec.encode_record(record); link = codec.record_link(record,raw)
        (self.root/link['path']).write_bytes(raw)
        path = self.root/(KEY+'.md'); document = md.decode_detail(path.read_bytes())
        extensions = copy.deepcopy(document.metadata['extensions'])
        extensions[md.ASSET_EXTENSION].append(link)
        path.write_bytes(md.encode_detail(state['ideas'][KEY],extensions=extensions))
        with self.assertRaises(IdeaError): self.show()

    def test_factory_purity_and_metadata_conflict_abort_before_publication(self):
        before = self.files()
        def impure(state):
            state['backlog_revision'] += 1; return [intent(self.sid)]
        with self.assertRaises(IdeaError) as caught: self.call(factory=impure)
        self.assertEqual(caught.exception.code,'invalid_handler')
        with self.assertRaises(IdeaError): self.call([intent(self.sid)],business=lambda state:{'asset_records':[]})
        with self.assertRaises(IdeaError): self.call(factory=lambda state:None)
        self.assertEqual(self.files(),before)

    def test_memory_accessors_require_exact_state_and_make_no_io(self):
        self.upload()
        with self.assertRaises(IdeaError): self.store.asset_records(self.original,KEY)
        with self.store.transaction() as state:
            with patch.object(self.store,'_safe',side_effect=AssertionError('accessor I/O')):
                entries = self.store.asset_inventory(state,KEY)
                self.assertEqual(len(entries['records']),2); entries['records'].clear()
                self.assertEqual(len(self.store.asset_records(state,KEY)),2)
                with self.assertRaises(IdeaError): self.store.asset_records(copy.deepcopy(state),KEY)

    def test_blob_missing_wrong_size_and_hash_refuse_completion(self):
        self.call([intent(self.sid)]); before = self.files()
        with self.assertRaises(IdeaError): self.call([completion(self.sid)],'complete')
        self.assertEqual(self.files(),before)
        for data in (b'bad',b'changed!'):
            self.blob(data=data); before = self.files()
            with self.assertRaises(IdeaError): self.call([completion(self.sid)],'complete')
            self.assertEqual(self.files(),before)

    def test_committed_blob_rehashed_on_read_and_before_publication(self):
        self.upload(); path = self.root/completion(self.sid)['blob_path']; path.write_bytes(b'changed!')
        with self.assertRaises(IdeaError): self.show()
        path.write_bytes(DATA); before = self.files()
        def business(state):
            path.write_bytes(b'changed!'); return {'idea_id':KEY}
        with self.assertRaises(IdeaError): self.call(rid='cas',business=business)
        path.write_bytes(DATA); self.assertEqual(self.files(),before)

    def test_orphan_blobs_stages_retained_with_bounded_opaque_ids(self):
        for number in range(1,131): self.blob(number)
        stage = self.root/'assets/staging'/('upload_'+'a'*32+'.'+'b'*32+'.part')
        stage.parent.mkdir(parents=True); stage.write_bytes(b'interrupted')
        before = self.files(); orphan = self.inventory()['orphans']
        self.assertEqual(orphan['count'],131); self.assertEqual(len(orphan['ids']),128)
        self.assertTrue(all('/' not in value and '.' not in value for value in orphan['ids']))
        self.assertEqual(self.files(),before)

    def test_unknown_evidence_and_reserved_names_fail_closed(self):
        path = self.root/'assets/evidence'/('a'*64+'.md'); path.parent.mkdir(parents=True); path.write_bytes(b'orphan')
        with self.assertRaises(IdeaError): self.show()

    def test_missing_publication_receipt_and_tampered_metadata_refuse_read(self):
        result = self.call([intent(self.sid)])
        path = self.root/result['asset_records'][0]['path']; raw = path.read_bytes()
        path.write_bytes(raw+b'altered')
        with self.assertRaises(IdeaError): self.show()
        path.write_bytes(raw)
        receipt = self.root/'session-recovery'/(self.sid+'.json')
        receipt.write_bytes(storage.encoded(dict(schema_version=1,session_id=self.sid,receipts={})))
        with self.assertRaises(IdeaError): self.show()

    def test_existing_mutate_and_no_factory_acceptance_preserve_asset_bytes(self):
        self.upload(); before = self.files()
        ordinary = self.store.mutate(self.sid,'ordinary',{},lambda state:{'idea_id':KEY})
        self.assertNotIn('asset_records',ordinary)
        result = self.call(rid='existing',business=lambda state:{'idea_id':KEY,
            'verified_count':len(self.store.asset_records(state,KEY))})
        self.assertEqual(result['verified_count'],2); self.assertEqual(result['asset_records'],[])
        for path,raw in before.items():
            if path.startswith('assets/') or path==KEY+'.md': self.assertEqual((self.root/path).read_bytes(),raw)

    def test_symlink_blob_and_stage_are_refused(self):
        for folder,name in (('blobs',identifier('asset',1)+'.bin'),('staging','upload_'+'a'*32+'.'+'b'*32+'.part')):
            with self.subTest(folder=folder):
                root = Path(self.temp.name)/folder; sid = seed(root)
                target = Path(self.temp.name)/(folder+'-outside'); target.write_bytes(DATA)
                path = root/'assets'/folder/name; path.parent.mkdir(parents=True); path.symlink_to(target)
                with self.assertRaises(IdeaError):
                    with storage.Store(root).transaction(): pass
                self.assertEqual(target.read_bytes(),DATA)

    def test_link_capacity_256_and_receipt_capacity_precede_factory(self):
        result = self.call([intent(self.sid,n) for n in range(1,257)])
        self.assertEqual(len(result['asset_records']),256); before = self.files()
        with self.assertRaises(IdeaError) as caught: self.call([intent(self.sid,257)],'overflow')
        self.assertEqual(caught.exception.code,'too_large'); self.assertEqual(self.files(),before)
        with patch.object(storage,'MAX_SESSION_RECEIPTS',1):
            with self.assertRaises(IdeaError) as caught:
                self.call(rid='full',factory=lambda state:self.fail('capacity factory'))
            self.assertEqual(caught.exception.code,'receipt_capacity_exhausted')
            self.assertEqual(self.call(),result)

    def test_actual_physical_capacity_refuses_completion_keeps_store_and_receipt(self):
        original = self.call([intent(self.sid)])
        for number in range(2,4095):
            self.blob(number)
        # One intent plus 4093 unrelated blobs: exactly the reproduced 4094.
        with self.store.transaction() as state:
            paths = self.store._contexts.active['asset_paths']
            self.assertEqual(set(paths),{'evidence','blobs','staging'})
            self.assertEqual(sum(map(len,paths.values())),4094)
        self.blob()
        stage = self.root/'assets/staging'/('upload_'+'1'.zfill(32)+'.'+'a'*32+'.part')
        stage.parent.mkdir(parents=True); stage.write_bytes(DATA)
        before = self.files(); baseline = self.show()
        with self.assertRaises(IdeaError) as caught:
            self.call([completion(self.sid)],'full-completion')
        self.assertEqual(caught.exception.code,'too_large')
        self.assertEqual(self.files(),before); self.assertEqual(self.show(),baseline)
        self.assertEqual(self.store.request_result(self.sid,'intent-1'),original)
        with self.assertRaises(IdeaError) as absent:
            self.store.request_result(self.sid,'full-completion')
        self.assertEqual(absent.exception.code,'request_not_found')
        self.assertEqual(len(self.inventory()['records']),1)

    def test_intent_and_set_capacity_include_new_evidence(self):
        for number in range(2,42): self.blob(number)
        before = self.files()
        def business(state):
            self.assertEqual(len(self.store.asset_records(state,KEY)),1)
            return {'idea_id':KEY}
        with patch.object(storage,'MAX_STORE_FILES',40):
            with self.assertRaises(IdeaError) as caught: self.call([intent(self.sid)],business=business)
        self.assertEqual(caught.exception.code,'too_large'); self.assertEqual(self.files(),before)
        self.upload(); before = self.files()
        # 40 orphans, one complete blob, two evidence records: 43 physical names.
        with patch.object(storage,'MAX_STORE_FILES',43):
            with self.assertRaises(IdeaError) as caught:
                self.call(rid='full-set',factory=lambda state:[design_set(state,self.sid)])
        self.assertEqual(caught.exception.code,'too_large'); self.assertEqual(self.files(),before)
        with self.assertRaises(IdeaError) as absent: self.store.request_result(self.sid,'full-set')
        self.assertEqual(absent.exception.code,'request_not_found')

    def test_unrelated_malformed_session_does_not_poison_set_read(self):
        self.upload(); result = self.call(rid='set-1',factory=lambda state:[design_set(state,self.sid)])
        unrelated = self.root/'session-recovery'/(identifier('session',0)+'.json')
        unrelated.write_bytes(b'not valid JSON')
        self.assertEqual(self.inventory()['records'][-1]['record']['session_id'],self.sid)
        self.assertEqual(self.store.request_result(self.sid,'set-1'),result)
        self.assertEqual(unrelated.read_bytes(),b'not valid JSON')

    def test_set_requires_exact_publishing_session_before_business(self):
        self.upload(); before = self.files()
        def factory(state):
            record = design_set(state,identifier('session',2)); return [record]
        with self.assertRaises(IdeaError) as caught:
            self.call(rid='foreign-session',factory=factory,business=lambda state:self.fail('business'))
        self.assertEqual(caught.exception.code,'request_conflict'); self.assertEqual(self.files(),before)

    def test_retained_sealed_success_is_not_orphan_and_inventory_remains_pure(self):
        self.call([intent(self.sid)])
        stage = self.root/'assets/staging'/(intent(self.sid)['upload_id']+'.'+'a'*32+'.part')
        stage.parent.mkdir(parents=True); stage.write_bytes(DATA); stage.chmod(0o440)
        blob = self.root/completion(self.sid)['blob_path']; blob.parent.mkdir(parents=True)
        os.link(stage,blob)
        # Until durable completion, both retained objects are still orphans.
        self.assertEqual(self.inventory()['orphans']['count'],2)
        def business(state):
            with patch.object(self.store,'_safe',side_effect=AssertionError('inventory I/O')):
                self.assertEqual(self.store.asset_inventory(state,KEY)['orphans'],dict(count=0,ids=[]))
            return {'idea_id':KEY}
        self.call([completion(self.sid)],'sealed-completion',business=business)
        self.assertEqual(self.inventory()['orphans'],dict(count=0,ids=[]))
        self.assertEqual(stage.stat().st_ino,blob.stat().st_ino)
        # Same upload and bytes alone are insufficient: an unlinked copy stays orphaned.
        failed = stage.with_name(intent(self.sid)['upload_id']+'.'+'b'*32+'.part')
        failed.write_bytes(DATA); failed.chmod(0o440)
        unmatched = stage.with_name(identifier('upload',2)+'.'+'c'*32+'.part')
        os.link(stage,unmatched)
        self.blob(2)
        diagnostic = self.inventory()['orphans']
        self.assertEqual(diagnostic['count'],3); self.assertEqual(len(diagnostic['ids']),3)
        self.assertTrue(stage.exists()); self.assertTrue(failed.exists()); self.assertTrue(unmatched.exists())

    def test_writable_completed_hardlink_stage_remains_orphan(self):
        self.call([intent(self.sid)]); blob = self.blob()
        stage = self.root/'assets/staging'/(intent(self.sid)['upload_id']+'.'+'a'*32+'.part')
        stage.parent.mkdir(parents=True); os.link(blob,stage)
        self.call([completion(self.sid)],'writable-completion')
        self.assertEqual(self.inventory()['orphans']['count'],1)

    def test_duplicate_existing_identity_refuses_as_request_conflict(self):
        self.call([intent(self.sid)]); before = self.files()
        with self.assertRaises(IdeaError) as caught: self.call([intent(self.sid)],'duplicate-intent')
        self.assertEqual(caught.exception.code,'request_conflict'); self.assertEqual(self.files(),before)

    def test_uncertain_publication_reconciles_exact_receipt(self):
        original = tx.publish
        def publish(*args,**kwargs):
            def cut(phase):
                if phase=='published:session-recovery/'+self.sid+'.json': raise OSError('lost response')
            return original(*args,**kwargs,_checkpoint=cut)
        with patch.object(storage.transactions,'publish',side_effect=publish):
            with self.assertRaises(IdeaError) as caught: self.call([intent(self.sid)])
        self.assertEqual(caught.exception.code,'durability_uncertain')
        result = self.store.request_result(self.sid,'intent-1')
        self.assertEqual(self.call(factory=lambda state:self.fail('uncertain factory')),result)

    def test_process_interruption_recovers_atomic_links_and_receipt(self):
        for phase in ('staged','prepared','evidence','detail','receipt','verified','complete'):
            with self.subTest(phase=phase):
                root = Path(self.temp.name)/('cut-'+phase); sid = seed(root)
                child = subprocess.run([sys.executable,'-c',CHILD,str(SCRIPTS),str(Path(__file__).parent),
                    str(root),sid,phase],capture_output=True,timeout=20)
                self.assertEqual(child.returncode,73,child.stderr.decode())
                store = storage.Store(root)
                with store.transaction() as state: entries = store.asset_records(state,KEY)
                if entries:
                    result = store.request_result(sid,'intent-1')
                    self.assertEqual(store.mutate_assets(sid,'intent-1',{'operation':'intent','number':1},
                        lambda state:self.fail('recovered business'),prepare_records=lambda state:self.fail('recovered factory')),result)
                else:
                    result = publish_intent(root,sid)
                self.assertEqual(len(result['asset_records']),1)


if __name__ == '__main__': unittest.main()
