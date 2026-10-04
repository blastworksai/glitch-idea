"""Actual authenticated loopback ingestion. Operator; native/remote NOT OBSERVED."""
import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0,str(SCRIPTS))
import idea_assets as assets
import idea_bridge as bridge
import idea_asset_evidence as codec
import idea_store as storage
import idea_transactions as tx
from idea_service import Service, TrustedContext
from idea_steps import load_registry
from test_bridge import FixturePolicy
from test_asset_store import KEY, seed
from test_workflow import fields, accept, complete, original_idea
import test_agent_launch as owner_fixture

PNG = b'\x89PNG\r\n\x1a\nfixture'
FORMATS = (
    ('png','image/png',PNG),('jpeg','image/jpeg',b'\xff\xd8\xfffixture'),
    ('webp','image/webp',b'RIFF\x04\x00\x00\x00WEBPfixture'),('pdf','application/pdf',b'%PDF-1.7\nfixture'),
    ('svg','image/svg+xml',b'<svg xmlns="http://www.w3.org/2000/svg"><script>evil()</script></svg>'),
    ('html','text/html',b'<!doctype html><html><script>evil()</script></html>'),
    ('css','text/css',b'body { background: url(https://example.invalid); }'),
    ('json','application/json',b'{"fixture":true}'),('md','text/markdown',b'# Fixture'),
    ('txt','text/plain',b'Plain fixture'),('zip','application/zip',b'PK\x03\x04fixture'))


class AssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'Café Store'; self.sid = seed(self.root)
        self.store = storage.Store(self.root,observer='Operator')
        self.app = Service(self.store,{},TrustedContext('Operator',self.sid),handlers=load_registry()[0])
        self.binding = bridge.ApplicationBinding(self.app); self.policy = FixturePolicy(self.binding)
        self.server = bridge.BridgeServer(self.policy,routes={route.route_id:route for route in assets.ROUTES})
        self.worker = threading.Thread(target=self.server.serve_forever,daemon=True); self.worker.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.worker.join()

    def request(self,path,payload=None,method=None,authenticated=True,extra=None):
        binary = type(payload) is bytes
        body = payload if binary or payload is None else json.dumps(payload).encode()
        headers = {'Host':self.server.host}
        if authenticated:
            headers.update(Cookie='browser='+self.policy.cookie)
            headers['X-CSRF-Token'] = self.policy.csrf
        if body is not None:
            headers['Origin'] = self.server.origin
            headers['Content-Type'] = 'application/octet-stream' if binary else 'application/json'
        headers.update(extra or {})
        connection = http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        try:
            connection.request(method or ('POST' if body is not None else 'GET'),path,body,headers)
            response = connection.getresponse(); raw = response.read(); result_headers = dict(response.getheaders())
            return response.status,(json.loads(raw) if response.getheader('Content-Type')=='application/json' else raw),result_headers
        finally: connection.close()

    def metadata(self,rid='metadata-1',data=PNG,name='Café.png',mime='image/png',revision=6):
        return dict(request_id=rid,idea_id=KEY,expected_revision=revision,name=name,declared_type=mime,size=len(data))

    def start(self,**kwargs):
        status,result,_ = self.request('/api/v1/uploads',self.metadata(**kwargs)); self.assertEqual(status,200,result)
        return result

    def put(self,record,data=PNG,**kwargs):
        return self.request('/api/v1/uploads/'+record['upload_id']+'/bytes',data,method='PUT',**kwargs)

    def inventory(self):
        with self.store.transaction() as state: return self.store.asset_inventory(state,KEY)

    def files(self):
        return {path.relative_to(self.root).as_posix():path.read_bytes() for path in self.root.rglob('*')
                if path.is_file() and not path.is_symlink() and tx.JOURNAL not in path.parts}

    def test_fixed_registry_and_metadata_replay_before_revision_uuid_clock(self):
        self.assertEqual([route.route_id for route in assets.ROUTES][:3],['uploads','upload-bytes','attachment'])
        self.assertEqual([route.route_id for route in assets.ROUTES][3:],['visual-set/accept','visual-disposition'])
        self.assertTrue(set(dict((route.route_id,route) for route in assets.ROUTES))<=set(load_registry()[1]))
        first = self.start(); before = self.files()
        with patch.object(assets.uuid,'uuid4',side_effect=AssertionError('replay UUID')), \
             patch.object(assets,'now',side_effect=AssertionError('replay clock')):
            self.assertEqual(self.start(),first)
        self.assertEqual(self.files(),before)
        self.assertEqual(first['completion_request_id'],'upload-bytes:'+first['upload_id'])
        status,error,_ = self.request('/api/v1/uploads',self.metadata(rid='stale',revision=5))
        self.assertEqual((status,error['code']),(409,'stale_revision'))
        self.assertEqual(self.inventory()['records'][0]['record']['source_revision'],6)

    def test_actual_http_upload_download_preserves_accepted_state_and_restart(self):
        with self.store.transaction() as state: original = copy.deepcopy(state['ideas'][KEY])
        intent = self.start(); status,result,_ = self.put(intent); self.assertEqual(status,200,result)
        self.assertEqual(result['request_id'],intent['completion_request_id'])
        self.assertEqual(result['sha256'],hashlib.sha256(PNG).hexdigest())
        self.binding.application = Service(storage.Store(self.root),{},TrustedContext('Operator',self.sid))
        status,raw,headers = self.request('/api/v1/attachments/'+intent['asset_id'])
        self.assertEqual((status,raw),(200,PNG)); self.assertEqual(headers['Content-Type'],'image/png')
        self.assertTrue(headers['Content-Disposition'].startswith('inline;'))
        self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
        with self.store.transaction() as state: self.assertEqual(state['ideas'][KEY],original)
        self.assertEqual(len(self.inventory()['records']),2); self.assertEqual(self.inventory()['orphans']['count'],0)

    def test_every_supported_type_basic_signature_and_inert_attachment_policy(self):
        for index,(extension,mime,data) in enumerate(FORMATS):
            with self.subTest(mime=mime):
                intent = self.start(rid='type-'+str(index),data=data,name='Fixture.'+extension,mime=mime)
                self.assertEqual(self.put(intent,data)[0],200)
                status,raw,headers = self.request('/api/v1/attachments/'+intent['asset_id'])
                self.assertEqual((status,raw),(200,data))
                raster = mime in assets.RASTER
                self.assertEqual(headers['Content-Type'],mime if raster else 'application/octet-stream')
                self.assertTrue(headers['Content-Disposition'].startswith('inline;' if raster else 'attachment;'))

    def test_authenticated_host_origin_csrf_and_path_boundaries(self):
        payload = self.metadata()
        for authenticated,extra,status in ((False,{},401),(True,{'Host':'localhost'},403),
            (True,{'Origin':'http://outsider'},403),(True,{'X-CSRF-Token':'bad'},403)):
            self.assertEqual(self.request('/api/v1/uploads',payload,authenticated=authenticated,extra=extra)[0],status)
        self.assertEqual(self.inventory()['records'],[])
        intent = self.start()
        self.assertEqual(self.put(intent,authenticated=False)[0],401)
        self.assertEqual(self.put(intent,extra={'X-CSRF-Token':'bad'})[0],403)
        self.assertEqual(self.request('/api/v1/attachments/'+intent['asset_id'],authenticated=False)[0],401)
        self.assertEqual(self.request('/api/v1/attachments/../../state.json')[0],404)
        self.assertEqual(self.put(intent,extra={'Content-Type':'image/png'})[0],400)

    def test_display_name_never_path_and_header_contains_no_raw_control_unicode(self):
        name = '../../outside\\evil";\r\nInjected: 💡.png'; intent = self.start(name=name)
        self.assertEqual(self.put(intent)[0],200)
        _,_,headers = self.request('/api/v1/attachments/'+intent['asset_id'])
        value = headers['Content-Disposition']; self.assertNotIn('\r',value); self.assertNotIn('\n',value)
        self.assertNotIn('💡',value); self.assertIn('%F0%9F%92%A1.png',value)
        record = self.inventory()['records'][0]['record']; self.assertEqual(record['name'],name)
        self.assertFalse((Path(self.temp.name)/'outside').exists())
        self.assertTrue((self.root/codec.blob_path(intent['asset_id'])).is_file())

    def test_metadata_schema_type_extension_size_and_capacity_admission(self):
        for change in ({'unexpected':True},{'size':0},{'size':codec.MAX_FILE+1},
                       {'declared_type':'application/executable'},{'name':'bad.jpg'},{'size':True}):
            with self.subTest(change=change):
                payload = dict(self.metadata(),**change); self.assertIn(self.request('/api/v1/uploads',payload)[0],(400,413))
        self.assertEqual(self.inventory()['records'],[])

    def test_signature_utf8_binary_text_and_length_refusal_retain_stages(self):
        for index,(data,mime,name) in enumerate(((b'not png fixture','image/png','a.png'),
                (b'\xffbinary','text/plain','a.txt'),(b'<html>\x00evil','text/html','a.html'),
                (b'<script>evil</script>','image/svg+xml','a.svg'))):
            with self.subTest(index=index):
                intent = self.start(rid='bad-'+str(index),data=data,mime=mime,name=name)
                self.assertEqual(self.put(intent,data)[0],400)
        intent = self.start(rid='length')
        self.assertEqual(self.put(intent,b'\x89PNG')[0],409)
        self.assertTrue(all(entry['record']['kind']=='upload-intent' for entry in self.inventory()['records']))
        self.assertGreaterEqual(self.inventory()['orphans']['count'],4)

    def test_explicit_byte_retry_hashes_payload_replays_exact_receipt_without_overwrite(self):
        intent = self.start(); status,result,_ = self.put(intent); self.assertEqual(status,200)
        blob = self.root/codec.blob_path(intent['asset_id']); before = blob.read_bytes()
        files_before = self.files()
        self.assertEqual(self.put(intent)[1],result); self.assertEqual(self.files(),files_before)
        status,error,_ = self.put(intent,PNG[:-1]+b'X')
        self.assertEqual((status,error['code']),(409,'request_conflict')); self.assertEqual(blob.read_bytes(),before)
        self.assertEqual(self.files(),files_before)
        self.assertEqual(len(self.inventory()['records']),2)
        self.assertEqual(self.request('/api/v1/requests/'+intent['completion_request_id'])[1],result)

    def test_streaming_outside_store_lock_and_interruption_retains_incomplete_stage(self):
        intent = self.start()
        class Reader:
            def read(inner,size):
                self.assertIsNone(getattr(self.store._contexts,'active',None))
                if getattr(inner,'started',False): return b''
                inner.started = True; return PNG[:4]
        request = bridge.RequestInfo('PUT','unused',{},self.server.origin,intent['upload_id'])
        with self.assertRaises(bridge.BridgeError):
            assets.upload_bytes(self.binding,request,bridge.BoundedBody(Reader(),len(PNG)))
        self.assertEqual(len(self.inventory()['records']),1)
        self.assertEqual(self.inventory()['orphans']['count'],1)
        self.assertEqual(self.put(intent)[0],200)

    def test_completion_uses_original_intent_after_unrelated_revision_drift(self):
        intent = self.start()
        from test_workflow import accept,fields
        with self.store.transaction(write=True) as state:
            value = fields()['priorities']; value['urgency'] = 9
            state['ideas'][KEY] = accept(state['ideas'][KEY],'priorities',value)['idea']; self.store.commit(state)
        self.assertEqual(self.put(intent)[0],200)
        self.assertEqual(self.inventory()['records'][-1]['record']['source_revision'],6)

    def test_stage_directory_symlink_refuses_without_outside_write(self):
        intent = self.start(); outside = Path(self.temp.name)/'outside'; outside.mkdir()
        (self.root/'assets/staging').symlink_to(outside,target_is_directory=True)
        self.assertEqual(self.put(intent)[0],500); self.assertEqual(list(outside.iterdir()),[])

    def test_existing_orphan_blob_exact_match_recovery_and_different_bytes_refusal(self):
        intent = self.start(); path = self.root/codec.blob_path(intent['asset_id'])
        path.parent.mkdir(parents=True); path.write_bytes(PNG[:-1]+b'X')
        status,error,_ = self.put(intent); self.assertEqual((status,error['code']),(409,'request_conflict'))
        self.assertEqual(path.read_bytes(),PNG[:-1]+b'X'); self.assertEqual(len(self.inventory()['records']),1)
        path.write_bytes(PNG); self.assertEqual(self.put(intent)[0],200)

    def test_lost_completion_response_reconciles_receipt_then_explicit_retry(self):
        intent = self.start(); original = tx.publish
        def publish(*args,**kwargs):
            def cut(phase):
                if phase=='published:session-recovery/'+self.sid+'.json': raise OSError('lost completion acknowledgement')
            return original(*args,**kwargs,_checkpoint=cut)
        with patch.object(storage.transactions,'publish',side_effect=publish):
            status,error,_ = self.put(intent)
        self.assertEqual((status,error['code']),(500,'durability_uncertain'))
        result = self.request('/api/v1/requests/'+intent['completion_request_id'])[1]
        self.assertEqual(result['write_state'],'applied'); self.assertEqual(self.put(intent)[1],result)

    def test_cross_session_upload_bytes_and_unknown_attachment_refused(self):
        intent = self.start(); sid = self.store.create_session()
        self.binding.application = Service(self.store,{},TrustedContext('Operator',sid))
        status,error,_ = self.put(intent); self.assertEqual((status,error['code']),(409,'request_conflict'))
        self.assertEqual(self.request('/api/v1/attachments/asset_'+'f'*32)[0],404)

    def test_blob_tamper_fails_attachment_read(self):
        intent = self.start(); self.assertEqual(self.put(intent)[0],200)
        path = self.root/codec.blob_path(intent['asset_id']); path.chmod(0o660); path.write_bytes(PNG[:-1]+b'X')
        self.assertEqual(self.request('/api/v1/attachments/'+intent['asset_id'])[0],500)

    def test_maximum_file_stream_is_chunk_bounded_and_retained_capacity_refuses_early(self):
        payload = self.metadata(); payload['size'] = codec.MAX_FILE
        status,intent,_ = self.request('/api/v1/uploads',payload); self.assertEqual(status,200)
        class Reader:
            def read(inner,size):
                self.assertIsNone(getattr(self.store._contexts,'active',None))
                self.assertLessEqual(size,65536)
                if not getattr(inner,'started',False):
                    inner.started = True; return PNG+b'x'*(size-len(PNG))
                return b'x'*size
        request = bridge.RequestInfo('PUT','unused',{},self.server.origin,intent['upload_id'])
        result = assets.upload_bytes(self.binding,request,bridge.BoundedBody(Reader(),codec.MAX_FILE))
        self.assertEqual(result['size'],codec.MAX_FILE)
        next_intent = self.start(rid='capacity'); before = self.inventory()['orphans']
        with patch.object(assets,'MAX_STORE_FILES',1):
            status,error,_ = self.put(next_intent)
        self.assertEqual((status,error['code']),(413,'too_large'))
        self.assertEqual(self.inventory()['orphans'],before)

    def fill_asset_inventory(self,target):
        count = sum(len(values) for values in self.store._asset_files().values())
        folder = self.root/'assets/staging'; folder.mkdir(parents=True,exist_ok=True)
        for number in range(target-count):
            (folder/('upload_'+'e'*32+'.'+format(number,'032x')+'.part')).write_bytes(b'Interrupted orphan')
        self.assertEqual(sum(len(values) for values in self.store._asset_files().values()),target)

    def archive(self):
        with self.store.transaction(write=True) as state:
            idea = state['ideas'][KEY]; pid = 'plan_'+'a'*32; content = '# Accepted archive fixture\n'
            idea['plans'].append(dict(plan_id=pid,idea_id=KEY,idea_revision=idea['revision'],
                path=str(self.root/'plan-evidence'/(pid+'.md')),source_path=str(self.root/'working-plan.md'),
                content=content,sha256=hashlib.sha256(content.encode()).hexdigest(),actor='Operator',
                timestamp='2026-10-02',validation={'builtin':'idea-trace-and-sections-v1'}))
            idea['status'] = 'archived'
            state['archives'][KEY+'/r'+str(idea['revision'])+'.json'] = dict(idea_id=KEY,
                origin=copy.deepcopy(idea['origin']),revision=copy.deepcopy(idea['revisions'][-1]))
            self.store.commit(state)
            self.assertEqual(self.store.view_issues(state,repair=True),[])

    def test_completed_retry_at_actual_file_limit_hashes_without_stage_uuid_or_publication(self):
        intent = self.complete_upload(); result = self.store.request_result(self.sid,intent['completion_request_id'])
        self.fill_asset_inventory(storage.MAX_STORE_FILES); before = self.files()
        with patch.object(assets.uuid,'uuid4',side_effect=AssertionError('completed retry UUID')), \
             patch.object(assets,'_directory',side_effect=AssertionError('completed retry directory')), \
             patch.object(assets,'_publish_blob',side_effect=AssertionError('completed retry publication')):
            self.assertEqual(self.put(intent)[1],result)
            status,error,_ = self.put(intent,PNG[:-1]+b'X')
        self.assertEqual((status,error['code']),(409,'request_conflict')); self.assertEqual(self.files(),before)

    def test_incomplete_upload_reserves_stage_blob_and_evidence_before_stream(self):
        intent = self.start(); self.fill_asset_inventory(storage.MAX_STORE_FILES-2); before = self.files()
        status,error,_ = self.put(intent)
        self.assertEqual((status,error['code']),(413,'too_large')); self.assertEqual(self.files(),before)
        self.assertEqual(len(self.inventory()['records']),1)

    def test_actual_http_metadata_interleaving_refuses_blob_at_cap_without_store_lockout(self):
        intent = self.start(); self.fill_asset_inventory(storage.MAX_STORE_FILES-3)
        publish = assets._publish_blob; admitted = []
        def interleaved(store,stage,record):
            self.assertIsNone(getattr(store._contexts,'active',None))
            for index in range(2):
                admitted.append(assets.upload_metadata(self.binding,None,self.metadata(rid='interleaved-'+str(index))))
            self.assertEqual(sum(len(values) for values in store._asset_files().values()),storage.MAX_STORE_FILES)
            return publish(store,stage,record)
        with patch.object(assets,'_publish_blob',side_effect=interleaved):
            status,error,_ = self.put(intent)
        self.assertEqual((status,error['code']),(413,'too_large'))
        self.assertFalse((self.root/codec.blob_path(intent['asset_id'])).exists())
        self.assertEqual(sum(len(values) for values in self.store._asset_files().values()),storage.MAX_STORE_FILES)
        self.assertEqual(len(self.inventory()['records']),3)
        self.assertEqual(self.start(),intent)
        for index,result in enumerate(admitted):
            self.assertEqual(self.store.request_result(self.sid,'interleaved-'+str(index)),result)
        self.assertEqual(self.request('/api/v1/requests/'+intent['completion_request_id'])[0],404)
        self.assertEqual(self.request('/api/v1/state')[0],200)

    def test_simultaneous_stage_admission_serializes_count_and_create_before_unlocked_body_reads(self):
        uploads = [self.start(rid='simultaneous-'+str(index)) for index in range(2)]
        with self.store.transaction() as state:
            records = copy.deepcopy(self.store.asset_records(state,KEY))
        self.fill_asset_inventory(storage.MAX_STORE_FILES-3)
        stores = [self.store,storage.Store(self.root,observer='Operator')]
        entered,release,competing = threading.Event(),threading.Event(),threading.Event()
        results,errors = {},{}; original_open = assets.os.open
        def stage_open(path,*args,**kwargs):
            if str(path).endswith('.part'):
                index = int(threading.current_thread().name.rsplit('-',1)[1])
                self.assertIsNotNone(getattr(stores[index]._contexts,'active',None))
                if index==0:
                    entered.set(); self.assertTrue(release.wait(5))
            return original_open(path,*args,**kwargs)
        class Reader:
            def read(inner,size):
                for store in stores: self.assertIsNone(getattr(store._contexts,'active',None))
                return PNG[:size]
        def stream(index):
            if index==1: competing.set()
            try:
                results[index] = assets._stream(stores[index],records[index],bridge.BoundedBody(Reader(),len(PNG)))
            except Exception as exc: errors[index] = exc
        workers = [threading.Thread(target=stream,args=(index,),name='stage-admission-'+str(index)) for index in range(2)]
        with patch.object(assets.os,'open',side_effect=stage_open):
            try:
                workers[0].start(); self.assertTrue(entered.wait(5))
                workers[1].start(); self.assertTrue(competing.wait(5)); release.set()
            finally:
                release.set()
                for worker in workers:
                    if worker.ident is not None: worker.join(10)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(set(results),{0}); self.assertEqual(set(errors),{1})
        self.assertIsInstance(errors[1],assets.IdeaError); self.assertEqual(errors[1].code,'too_large')
        self.assertEqual(sum(len(values) for values in self.store._asset_files().values()),storage.MAX_STORE_FILES-2)
        self.assertEqual(len(self.inventory()['records']),2)
        for index,result in enumerate(uploads):
            self.assertEqual(self.store.request_result(self.sid,'simultaneous-'+str(index)),result)

    def test_missing_descriptor_sealing_refuses_before_stage_but_completed_retry_is_available(self):
        completed = self.complete_upload(); incomplete = self.start(rid='unsupported-platform')
        before = self.files()
        with patch.object(assets.os,'fchmod',None):
            status,error,_ = self.put(incomplete)
            self.assertEqual((status,error['code']),(400,'platform_unavailable'))
            self.assertEqual(self.put(completed)[0],200)
        self.assertEqual(self.files(),before)
        self.assertEqual(self.request('/api/v1/requests/'+incomplete['completion_request_id'])[0],404)

    def test_archived_new_metadata_and_incomplete_bytes_refuse_completed_receipts_download_replay(self):
        completed = self.complete_upload(); incomplete = self.start(rid='incomplete-before-archive')
        completed_result = self.store.request_result(self.sid,completed['completion_request_id']); self.archive()
        before = self.files()
        self.assertEqual(self.start(),completed)  # Historical metadata receipt precedes archive CAS.
        self.assertEqual(self.put(completed)[1],completed_result)
        self.assertEqual(self.request('/api/v1/attachments/'+completed['asset_id'])[:2],(200,PNG))
        metadata = self.metadata(rid='new-after-archive')
        status,error,_ = self.request('/api/v1/uploads',metadata)
        self.assertEqual((status,error['code']),(400,'idea_archived'))
        status,error,_ = self.put(incomplete)
        self.assertEqual((status,error['code']),(400,'idea_archived')); self.assertEqual(self.files(),before)
        self.assertEqual(self.request('/api/v1/requests/'+incomplete['completion_request_id'])[0],404)

    def test_atomic_blob_appearance_sealed_stage_and_interrupted_link_recovery(self):
        intent = self.start()
        def interrupted(*args,**kwargs): raise OSError('Interruption before atomic link')
        with patch.object(assets.os,'link',side_effect=interrupted):
            self.assertEqual(self.put(intent)[0],500)
        self.assertFalse((self.root/codec.blob_path(intent['asset_id'])).exists())
        stages = list((self.root/'assets/staging').iterdir()); self.assertEqual(len(stages),1)
        self.assertEqual(stages[0].stat().st_mode & 0o777,0o400)
        self.assertEqual(self.put(intent)[0],200)
        blob = self.root/codec.blob_path(intent['asset_id'])
        linked = next(path for path in (self.root/'assets/staging').iterdir() if path.stat().st_ino==blob.stat().st_ino)
        self.assertEqual(linked.stat().st_mode & 0o777,0o400); self.assertEqual(blob.read_bytes(),PNG)

    def set_payload(self,ids=None,set_id=None,rid='set-1'):
        with self.store.transaction() as state: idea = state['ideas'][KEY]
        return dict(request_id=rid,idea_id=KEY,expected_revision=idea['revision'],
            expected_draft_version=idea['workflow']['draft_version'],step='visualize',proposal_id=None,
            expected_backlog_revision=None,fields=dict(disposition='accepted_set',reason=None,
            design_set_id=set_id,brief_evidence_id=None),design_set_id=set_id,asset_ids=ids)

    def complete_upload(self,rid='metadata-1'):
        intent = self.start(rid=rid); self.assertEqual(self.put(intent)[0],200); return intent

    def test_http_new_set_atomic_pointer_evidence_members_and_existing_reaccept(self):
        upload = self.complete_upload(); payload = self.set_payload([upload['asset_id']])
        before = self.files(); status,result,_ = self.request('/api/v1/visual-set/accept',payload)
        self.assertEqual(status,200,result); self.assertEqual(len(result['asset_records']),1)
        state = self.request('/api/v1/state')[1]
        self.assertEqual(state['accepted']['visualize']['design_set_id'],result['design_set_id'])
        record = self.inventory()['records'][-1]['record']
        self.assertEqual(record['members'][0]['asset_id'],upload['asset_id'])
        self.assertEqual(record['source_revision'],payload['expected_revision'])
        self.assertEqual(record['session_id'],self.sid)
        for path,raw in before.items():
            if path.startswith('assets/'): self.assertEqual((self.root/path).read_bytes(),raw)
        existing = self.set_payload(set_id=result['design_set_id'],rid='existing-set')
        status,reaccepted,_ = self.request('/api/v1/visual-set/accept',existing)
        self.assertEqual(status,200,reaccepted); self.assertEqual(reaccepted['asset_records'],[])
        self.assertEqual(len(self.inventory()['records']),3)

    def test_set_replay_before_factory_cas_source_uuid_clock_and_hash_binding(self):
        upload = self.complete_upload(); payload = self.set_payload([upload['asset_id']])
        result = self.request('/api/v1/visual-set/accept',payload)[1]
        with self.store.transaction(write=True) as state:
            value = fields()['priorities']; value['urgency'] = 9
            state['ideas'][KEY] = accept(state['ideas'][KEY],'priorities',value)['idea']; self.store.commit(state)
        before = self.files()
        with patch.object(assets,'current_source',side_effect=AssertionError('replay source')), \
             patch.object(assets.uuid,'uuid4',side_effect=AssertionError('replay UUID')), \
             patch.object(assets,'now',side_effect=AssertionError('replay time')):
            self.assertEqual(self.request('/api/v1/visual-set/accept',payload)[1],result)
        self.assertEqual(self.files(),before)
        changed = dict(payload,asset_ids=[])
        self.assertIn(self.request('/api/v1/visual-set/accept',changed)[0],(400,413))
        changed = copy.deepcopy(payload); changed['fields']['reason'] = 'Different validated payload'
        with patch.object(assets,'current_source',side_effect=AssertionError('conflict source')), \
             patch.object(assets.uuid,'uuid4',side_effect=AssertionError('conflict UUID')), \
             patch.object(assets,'now',side_effect=AssertionError('conflict time')):
            status,error,_ = self.request('/api/v1/visual-set/accept',changed)
        self.assertEqual((status,error['code']),(409,'request_conflict'))
        self.assertEqual(self.files(),before)
        self.assertEqual(self.request('/api/v1/requests/'+payload['request_id'])[1],result)

    def test_set_stale_cas_missing_incomplete_duplicate_and_conflicting_pointer_refuse(self):
        upload = self.complete_upload(); incomplete = self.start(rid='incomplete')
        examples = []
        stale = self.set_payload([upload['asset_id']]); stale['expected_revision'] -= 1; examples.append(stale)
        examples.append(self.set_payload([incomplete['asset_id']]))
        examples.append(self.set_payload(['asset_'+'f'*32]))
        examples.append(self.set_payload([upload['asset_id'],upload['asset_id']]))
        examples.append(self.set_payload([upload['asset_id']]*21))
        mismatch = self.set_payload([upload['asset_id']]); mismatch['fields']['design_set_id'] = 'set_'+'f'*32; examples.append(mismatch)
        examples.append(self.set_payload([upload['asset_id']],set_id='set_'+'f'*32))
        before = self.files()
        for index,payload in enumerate(examples):
            with self.subTest(index=index):
                payload['request_id'] = 'refused-'+str(index)
                self.assertIn(self.request('/api/v1/visual-set/accept',payload)[0],(400,404,409,413))
                self.assertEqual(self.files(),before)

    def test_set_handler_refusal_rolls_back_prepared_evidence_pointer_and_receipt(self):
        upload = self.complete_upload(); payload = self.set_payload([upload['asset_id']]); before = self.files()
        payload['fields']['brief_evidence_id'] = 'fabricated-brief'
        status,error,_ = self.request('/api/v1/visual-set/accept',payload)
        self.assertEqual((status,error['code']),(400,'supporting_evidence'))
        self.assertEqual(self.files(),before); self.assertEqual(len(self.inventory()['records']),2)
        self.assertEqual(self.request('/api/v1/requests/'+payload['request_id'])[0],404)

    def test_set_foreign_completed_asset_total_capacity_and_authentication_refuse(self):
        upload = self.complete_upload(); second = self.complete_upload(rid='second-metadata')
        payload = self.set_payload([upload['asset_id'],second['asset_id']]); before = self.files()
        with patch.object(codec,'MAX_SET',len(PNG)):
            status,error,_ = self.request('/api/v1/visual-set/accept',payload)
        self.assertEqual((status,error['code']),(413,'too_large')); self.assertEqual(self.files(),before)
        self.assertEqual(self.request('/api/v1/visual-set/accept',payload,authenticated=False)[0],401)
        self.assertEqual(self.request('/api/v1/visual-set/accept',payload,extra={'X-CSRF-Token':'bad'})[0],403)
        other = 'idea_'+'2'*32
        with self.store.transaction(write=True) as state:
            idea = complete(); idea['idea_id'] = other; state['ideas'][other] = idea
            state['order'].append(other); state['backlog_revision'] += 1; self.store.commit(state)
        metadata = dict(self.metadata(rid='foreign-metadata'),idea_id=other)
        status,foreign,_ = self.request('/api/v1/uploads',metadata); self.assertEqual(status,200,foreign)
        self.assertEqual(self.put(foreign)[0],200)
        payload = self.set_payload([foreign['asset_id']],rid='foreign-set'); before = self.files()
        status,error,_ = self.request('/api/v1/visual-set/accept',payload)
        self.assertEqual((status,error['code']),(404,'asset_not_found')); self.assertEqual(self.files(),before)

    def test_set_legacy_idea_without_workflow_refuses_typed_without_publication(self):
        other = 'idea_'+'2'*32
        with self.store.transaction(write=True) as state:
            idea = original_idea(); idea['idea_id'] = other; self.assertNotIn('workflow',idea)
            state['ideas'][other] = idea; state['order'].append(other); state['backlog_revision'] += 1
            self.store.commit(state)
        metadata = dict(self.metadata(rid='legacy-metadata'),idea_id=other,expected_revision=1)
        status,upload,_ = self.request('/api/v1/uploads',metadata); self.assertEqual(status,200,upload)
        self.assertEqual(self.put(upload)[0],200)
        payload = self.set_payload([upload['asset_id']],rid='legacy-set')
        payload.update(idea_id=other,expected_revision=1,expected_draft_version=0)
        before = self.files(); status,error,_ = self.request('/api/v1/visual-set/accept',payload)
        self.assertEqual((status,error['code']),(400,'not_ready')); self.assertEqual(self.files(),before)
        self.assertEqual(self.request('/api/v1/requests/legacy-set')[0],404)

    def test_stale_existing_set_and_same_generic_guard_refuse_after_shape_changes(self):
        upload = self.complete_upload(); created = self.request('/api/v1/visual-set/accept',self.set_payload([upload['asset_id']]))[1]
        with self.store.transaction(write=True) as state:
            value = fields()['shape']; value['outcome'] = 'Changed accepted Shape'
            state['ideas'][KEY] = accept(state['ideas'][KEY],'shape',value)['idea']; self.store.commit(state)
        payload = self.set_payload(set_id=created['design_set_id'],rid='stale-existing'); before = self.files()
        status,error,_ = self.request('/api/v1/visual-set/accept',payload)
        self.assertEqual((status,error['code']),(409,'stale_source')); self.assertEqual(self.files(),before)
        generic = {key:value for key,value in payload.items() if key not in ('design_set_id','asset_ids')}
        generic['request_id'] = 'stale-generic'
        status,error,_ = self.request('/api/v1/accept',generic)
        self.assertEqual((status,error['code']),(409,'stale_source')); self.assertEqual(self.files(),before)

    def test_http_disposition_same_reducer_clears_pointer_and_preserves_historical_sets(self):
        upload = self.complete_upload(); self.request('/api/v1/visual-set/accept',self.set_payload([upload['asset_id']]))
        for disposition in ('skipped','not-applicable'):
            payload = self.set_payload(rid=disposition)
            payload = {key:value for key,value in payload.items() if key not in ('asset_ids','design_set_id')}
            payload['fields'].update(disposition=disposition,reason='No design needed for this slice')
            status,result,_ = self.request('/api/v1/visual-disposition',payload)
            self.assertEqual(status,200,result); self.assertEqual(self.request('/api/v1/visual-disposition',payload)[1],result)
            state = self.request('/api/v1/state')[1]
            self.assertIsNone(state['accepted']['visualize']['design_set_id']); self.assertEqual(len(self.inventory()['records']),3)
        payload['request_id'] = 'no-reason'; payload['fields']['reason'] = '  '
        before = self.files()
        status,error,_ = self.request('/api/v1/visual-disposition',payload)
        self.assertEqual((status,error['code']),(409,'stale_revision'))
        self.assertEqual(self.files(),before)
        state = self.request('/api/v1/state')[1]
        payload.update(expected_revision=state['revision'],expected_draft_version=state['draft_version'])
        status,error,_ = self.request('/api/v1/visual-disposition',payload)
        self.assertEqual((status,error['code']),(400,'not_ready'))
        self.assertEqual(self.files(),before)
        self.assertEqual(self.request('/api/v1/requests/no-reason')[0],404)

    def test_archived_visualize_all_new_decisions_refuse_and_exact_receipts_stay_readable(self):
        upload = self.complete_upload(); historical = []
        def accepted(path,payload):
            status,result,_ = self.request(path,payload); self.assertEqual(status,200,result)
            historical.append((path,copy.deepcopy(payload),copy.deepcopy(result)))
            return result
        created = accepted('/api/v1/visual-set/accept',self.set_payload([upload['asset_id']],rid='before-archive-new-set'))
        set_id = created['design_set_id']
        accepted('/api/v1/visual-set/accept',self.set_payload(set_id=set_id,rid='before-archive-existing-set'))
        generic = self.set_payload(set_id=set_id,rid='before-archive-generic-set')
        generic = {key:value for key,value in generic.items() if key not in ('design_set_id','asset_ids')}
        accepted('/api/v1/accept',generic)
        for disposition in ('skipped','not-applicable'):
            for endpoint in ('visual-disposition','accept'):
                payload = self.set_payload(rid='before-archive-'+disposition+'-'+endpoint)
                payload = {key:value for key,value in payload.items() if key not in ('design_set_id','asset_ids')}
                payload['fields'].update(disposition=disposition,reason='Explicit historical decision')
                accepted('/api/v1/'+endpoint,payload)
        self.archive(); before = self.files()
        with self.store.transaction() as state: frozen = copy.deepcopy(state)
        refused = [('/api/v1/visual-set/accept',self.set_payload([upload['asset_id']],rid='archived-new-set')),
            ('/api/v1/visual-set/accept',self.set_payload(set_id=set_id,rid='archived-existing-set'))]
        generic = self.set_payload(set_id=set_id,rid='archived-generic-set')
        refused.append(('/api/v1/accept',{key:value for key,value in generic.items() if key not in ('design_set_id','asset_ids')}))
        for disposition in ('skipped','not-applicable'):
            for endpoint in ('visual-disposition','accept'):
                payload = self.set_payload(rid='archived-'+disposition+'-'+endpoint)
                payload = {key:value for key,value in payload.items() if key not in ('design_set_id','asset_ids')}
                payload['fields'].update(disposition=disposition,reason='New decision after archival')
                refused.append(('/api/v1/'+endpoint,payload))
        with patch.object(assets.uuid,'uuid4',side_effect=AssertionError('Archived set UUID')), \
             patch.object(assets,'now',side_effect=AssertionError('Archived set timestamp')):
            for path,payload in refused:
                with self.subTest(path=path,request=payload['request_id']):
                    status,error,_ = self.request(path,payload)
                    self.assertEqual((status,error['code']),(400,'idea_archived'))
                    self.assertEqual(self.files(),before)
                    self.assertEqual(self.request('/api/v1/requests/'+payload['request_id'])[0],404)
            for path,payload,result in historical:
                self.assertEqual(self.request(path,payload)[:2],(200,result))
                self.assertEqual(self.request('/api/v1/requests/'+payload['request_id'])[:2],(200,result))
        self.assertEqual(self.put(upload)[0],200)
        self.assertEqual(self.request('/api/v1/attachments/'+upload['asset_id'])[:2],(200,PNG))
        self.assertEqual(self.files(),before)
        with self.store.transaction() as state:
            self.assertEqual(state,frozen); self.assertEqual(self.store.view_issues(state),[])

    def test_archived_assess_changed_estimate_and_position_refuse_old_receipt_replays(self):
        other = 'idea_'+'2'*32
        with self.store.transaction(write=True) as state:
            neighbor = complete(); neighbor['idea_id'] = other
            state['ideas'][other] = neighbor; state['order'].append(other)
            state['backlog_revision'] += 1; self.store.commit(state)
        status,selected,_ = self.request('/api/v1/state?idea_id='+KEY)
        self.assertEqual(status,200,selected); self.assertEqual(selected['idea_id'],KEY)
        self.assertEqual(selected['idea_status'],'active')
        value = fields()['assess']
        value['assessment']['inputs']['time_criticality'] = 4
        value['position']['neighbors']['after'] = other
        payload = dict(request_id='assess-before-archive',idea_id=KEY,step='assess',fields=value,
            expected_revision=selected['revision'],expected_draft_version=selected['draft_version'],
            expected_backlog_revision=selected['backlog_revision'],proposal_id=None)
        status,result,_ = self.request('/api/v1/accept',payload)
        self.assertEqual(status,200,result)
        projected = self.request('/api/v1/state?idea_id='+KEY)[1]
        self.assertEqual(projected['accepted']['assess'],value)
        self.assertEqual(projected['steps']['assess']['status'],'saved')
        self.archive(); before = self.files()
        with self.store.transaction() as state:
            frozen = copy.deepcopy(state); self.assertEqual(self.store.view_issues(state),[])
        status,selected,_ = self.request('/api/v1/state?idea_id='+KEY)
        self.assertEqual(status,200,selected); self.assertEqual(selected['idea_id'],KEY)
        self.assertEqual(selected['idea_status'],'archived')
        changed = copy.deepcopy(payload)
        changed.update(request_id='assess-after-archive',expected_revision=selected['revision'],
            expected_draft_version=selected['draft_version'],expected_backlog_revision=selected['backlog_revision'])
        changed['fields']['assessment']['inputs']['effort'] = 2
        changed['fields']['position'].update(actual_position=2,
            neighbors={'before':other,'after':None},override_reason='Place after the current neighbor')
        status,error,_ = self.request('/api/v1/accept',changed)
        self.assertEqual((status,error['code']),(400,'idea_archived'))
        self.assertEqual(self.request('/api/v1/requests/'+changed['request_id'])[0],404)
        self.assertEqual(self.files(),before)
        self.assertEqual(self.request('/api/v1/accept',payload)[:2],(200,result))
        self.assertEqual(self.request('/api/v1/requests/'+payload['request_id'])[:2],(200,result))
        self.assertEqual(self.files(),before)
        with self.store.transaction() as state:
            self.assertEqual(state,frozen)
            self.assertEqual(state['order'],[KEY,other])
            self.assertEqual(state['ideas'][KEY]['ratings'],frozen['ideas'][KEY]['ratings'])
            self.assertEqual(state['ideas'][KEY]['assessments'],frozen['ideas'][KEY]['assessments'])
            self.assertEqual(self.store.view_issues(state),[])

    def test_archived_capture_priorities_method_require_shape_first_and_replay_old_receipts(self):
        workspace = Path(self.temp.name)/'Confirmed workspace'; workspace.mkdir()
        values = fields()
        values['capture']['workspace'] = dict(name='Confirmed workspace',path=str(workspace),confirmed=True)
        values['capture']['raw_text'] = 'Explicit current Capture words'
        historical = []
        for step in ('capture','priorities','shape','method'):
            status,selected,_ = self.request('/api/v1/state?idea_id='+KEY)
            self.assertEqual(status,200,selected); self.assertEqual(selected['idea_id'],KEY)
            payload = dict(request_id='before-archive-'+step,idea_id=KEY,step=step,fields=values[step],
                expected_revision=selected['revision'],expected_draft_version=selected['draft_version'],
                expected_backlog_revision=None,proposal_id=None)
            status,result,_ = self.request('/api/v1/accept',payload)
            self.assertEqual(status,200,result)
            historical.append((copy.deepcopy(payload),copy.deepcopy(result)))
        self.assertTrue(workspace.is_dir())
        self.archive(); before = self.files()
        with self.store.transaction() as state:
            frozen = copy.deepcopy(state); self.assertEqual(self.store.view_issues(state),[])
        status,selected,_ = self.request('/api/v1/state?idea_id='+KEY)
        self.assertEqual(status,200,selected); self.assertEqual(selected['idea_status'],'archived')
        for step in ('capture','priorities','method'):
            value = copy.deepcopy(values[step])
            if step == 'capture': value['raw_text'] = 'Changed words after archive'
            elif step == 'priorities': value['urgency'] = 9
            else: value['reason'] = 'Changed method rationale after archive'
            payload = dict(request_id='after-archive-'+step,idea_id=KEY,step=step,fields=value,
                expected_revision=selected['revision'],expected_draft_version=selected['draft_version'],
                expected_backlog_revision=None,proposal_id=None)
            with self.subTest(step=step):
                status,error,_ = self.request('/api/v1/accept',payload)
                self.assertEqual((status,error['code']),(400,'idea_archived'))
                self.assertEqual(self.request('/api/v1/requests/'+payload['request_id'])[0],404)
                self.assertEqual(self.files(),before)
                with self.store.transaction() as state: self.assertEqual(state,frozen)
        for payload,result in historical:
            self.assertEqual(self.request('/api/v1/accept',payload)[:2],(200,result))
            self.assertEqual(self.request('/api/v1/requests/'+payload['request_id'])[:2],(200,result))
        self.assertEqual(self.files(),before)
        with self.store.transaction() as state:
            self.assertEqual(state,frozen); self.assertEqual(self.store.view_issues(state),[])

    def test_explicit_http_shape_reactivation_preserves_archived_plan_and_asset_bytes(self):
        upload = self.complete_upload()
        status,created,_ = self.request('/api/v1/visual-set/accept',self.set_payload([upload['asset_id']],rid='archive-shape-set'))
        self.assertEqual(status,200,created)
        self.archive()
        with self.store.transaction() as state: frozen = copy.deepcopy(state)
        before = self.files()
        immutable = {path:raw for path,raw in before.items() if path.startswith(('assets/','archive/','plan-evidence/'))}
        value = copy.deepcopy(frozen['ideas'][KEY]['workflow']['steps']['shape']['fields'])
        value.update(outcome='Explicit next active slice',next_slice='Check the next slice')
        state = self.request('/api/v1/state')[1]
        payload = dict(request_id='explicit-shape-reactivation',idea_id=KEY,expected_revision=state['revision'],
            expected_draft_version=state['draft_version'],step='shape',fields=value,proposal_id=None,expected_backlog_revision=None)
        status,result,_ = self.request('/api/v1/accept',payload); self.assertEqual(status,200,result)
        self.assertEqual(self.request('/api/v1/accept',payload)[:2],(200,result))
        with self.store.transaction() as state:
            prior = frozen['ideas'][KEY]; idea = state['ideas'][KEY]
            self.assertEqual(idea['status'],'active'); self.assertEqual(idea['revision'],prior['revision']+1)
            self.assertEqual(idea['revisions'][:-1],prior['revisions']); self.assertEqual(idea['origin'],prior['origin'])
            self.assertEqual(idea['plans'],prior['plans']); self.assertEqual(state['archives'],frozen['archives'])
            self.assertEqual(idea['workflow']['steps']['visualize']['fields']['design_set_id'],created['design_set_id'])
            self.assertEqual(self.store.view_issues(state),[])
        for path,raw in immutable.items(): self.assertEqual((self.root/path).read_bytes(),raw)
        projected = self.request('/api/v1/state')[1]
        self.assertEqual(projected['idea_status'],'active'); self.assertEqual(projected['steps']['visualize']['status'],'review-needed')
        self.assertEqual(self.request('/api/v1/attachments/'+upload['asset_id'])[:2],(200,PNG))

    def test_explicit_http_unchanged_shape_reactivates_once_preserving_archive_assets(self):
        upload = self.complete_upload()
        status,created,_ = self.request('/api/v1/visual-set/accept',self.set_payload([upload['asset_id']],rid='unchanged-shape-set'))
        self.assertEqual(status,200,created); self.archive()
        with self.store.transaction() as state: frozen = copy.deepcopy(state)
        before = self.files()
        immutable = {path:raw for path,raw in before.items() if path.startswith(('assets/','archive/','plan-evidence/'))}
        status,selected,_ = self.request('/api/v1/state?idea_id='+KEY)
        self.assertEqual(status,200,selected); self.assertEqual(selected['idea_id'],KEY)
        self.assertEqual(self.app.context.selected_idea_id,KEY); self.assertEqual(selected['idea_status'],'archived')
        value = copy.deepcopy(frozen['ideas'][KEY]['workflow']['steps']['shape']['fields'])
        payload = dict(request_id='explicit-unchanged-shape',idea_id=KEY,expected_revision=selected['revision'],
            expected_draft_version=selected['draft_version'],step='shape',fields=value,proposal_id=None,expected_backlog_revision=None)
        status,result,_ = self.request('/api/v1/accept',payload); self.assertEqual(status,200,result)
        with self.store.transaction() as state:
            prior = frozen['ideas'][KEY]; idea = state['ideas'][KEY]
            self.assertEqual(idea['status'],'active'); self.assertEqual(idea['revision'],prior['revision']+1)
            self.assertEqual(idea['revisions'][:-1],prior['revisions'])
            for key in ('origin','ratings','assessments','plans','executions'):
                self.assertEqual(idea[key],prior[key])
            self.assertEqual(state['archives'],frozen['archives'])
            self.assertEqual(idea['workflow']['steps']['shape']['fields'],value)
            self.assertEqual(idea['workflow']['steps']['shape']['acceptance']['accepted_revision'],idea['revision'])
            self.assertEqual(idea['workflow']['steps']['visualize']['fields']['design_set_id'],created['design_set_id'])
            self.assertEqual(self.store.view_issues(state),[])
            accepted = copy.deepcopy(state)
        for path,raw in immutable.items(): self.assertEqual((self.root/path).read_bytes(),raw)
        saved_files = self.files()
        self.assertEqual(self.request('/api/v1/accept',payload)[:2],(200,result))
        self.assertEqual(self.request('/api/v1/requests/'+payload['request_id'])[:2],(200,result))
        self.assertEqual(self.files(),saved_files)
        with self.store.transaction() as state: self.assertEqual(state,accepted)
        projected = self.request('/api/v1/state?idea_id='+KEY)[1]
        self.assertEqual(projected['idea_status'],'active')
        self.assertEqual(projected['steps']['visualize']['status'],'review-needed')
        self.assertEqual(self.request('/api/v1/attachments/'+upload['asset_id'])[:2],(200,PNG))

    def test_lost_set_acknowledgement_receipt_restart_and_atomic_replay(self):
        upload = self.complete_upload(); payload = self.set_payload([upload['asset_id']]); original = tx.publish
        def publish(*args,**kwargs):
            def cut(phase):
                if phase=='published:session-recovery/'+self.sid+'.json': raise OSError('lost set acknowledgement')
            return original(*args,**kwargs,_checkpoint=cut)
        with patch.object(storage.transactions,'publish',side_effect=publish):
            status,error,_ = self.request('/api/v1/visual-set/accept',payload)
        self.assertEqual((status,error['code']),(500,'durability_uncertain'))
        self.binding.application = Service(storage.Store(self.root),{},TrustedContext('Operator',self.sid,KEY),handlers=load_registry()[0])
        recorded = self.request('/api/v1/requests/'+payload['request_id'])[1]
        self.assertEqual(self.request('/api/v1/visual-set/accept',payload)[1],recorded)
        state = self.request('/api/v1/state')[1]
        self.assertEqual(state['accepted']['visualize']['design_set_id'],recorded['design_set_id'])


@unittest.skipUnless(os.name=='posix','Native owner ACLs remain unqualified')
class OwnerAssetSetTests(unittest.TestCase):
    setUp = owner_fixture.AgentLaunchTests.setUp
    start_owner = owner_fixture.AgentLaunchTests.start_owner
    cleanup = owner_fixture.AgentLaunchTests.cleanup
    wire = owner_fixture.AgentLaunchTests.wire
    pair = owner_fixture.AgentLaunchTests.pair
    browser = owner_fixture.AgentLaunchTests.browser
    accept_fields = owner_fixture.AgentLaunchTests.accept_fields

    def test_actual_owner_registry_upload_set_and_generic_visualize_guard(self):
        self.accept_fields('priorities',fields()['priorities'],'priorities')
        self.accept_fields('shape',fields()['shape'],'shape')
        self.accept_fields('method',fields()['method'],'method')
        state = self.browser('state'); upload = self.browser('uploads',dict(request_id='metadata',idea_id=self.idea_id,
            expected_revision=state['revision'],name='Owner.png',declared_type='image/png',size=len(PNG)))
        connection = http.client.HTTPConnection('127.0.0.1',self.owner.server.server_port,timeout=10)
        try:
            connection.request('PUT','/api/v1/uploads/'+upload['upload_id']+'/bytes',PNG,
                {'Cookie':self.cookie,'X-Idea-Binding':self.opened['binding_id'],'X-CSRF-Token':self.csrf,
                 'Origin':self.owner.server.origin,'Content-Type':'application/octet-stream'})
            response = connection.getresponse(); result = json.loads(response.read()); self.assertEqual(response.status,200,result)
        finally: connection.close()
        state = self.browser('state')
        payload = dict(request_id='owner-set',idea_id=self.idea_id,expected_revision=state['revision'],
            expected_draft_version=state['draft_version'],step='visualize',proposal_id=None,expected_backlog_revision=None,
            fields=dict(disposition='accepted_set',reason=None,design_set_id=None,brief_evidence_id=None),
            design_set_id=None,asset_ids=[upload['asset_id']])
        result = self.browser('visual-set/accept',payload)
        self.assertEqual(self.browser('visual-set/accept',payload),result)
        self.assertEqual(self.browser('state')['accepted']['visualize']['design_set_id'],result['design_set_id'])
        state = self.browser('state'); generic = {key:value for key,value in payload.items() if key not in ('asset_ids','design_set_id')}
        generic.update(request_id='invented-generic',expected_revision=state['revision'],expected_draft_version=state['draft_version'])
        generic['fields'] = dict(generic['fields'],design_set_id='set_'+'f'*32)
        error = self.browser('accept',generic,ok=False,expected_status=404)
        self.assertEqual(error['code'],'asset_not_found')


if __name__=='__main__': unittest.main()
