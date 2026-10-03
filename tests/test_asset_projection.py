"""Verified Service asset projection and detached reducer data. Operator."""
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
import idea_assets as ingestion
import idea_asset_evidence as codec
import idea_bridge as bridge
import idea_service as application
import idea_store as storage
from idea_domain import IdeaError
from idea_service import Service, TrustedContext, TrustedStepHandler
from idea_workflow import source_digest
from test_asset_store import KEY, seed, identifier

PNG = b'\x89PNG\r\n\x1a\nfixture'


class AssetProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'Store'; self.sid = seed(self.root)
        self.store = storage.Store(self.root,observer='Operator')
        self.context = TrustedContext('Operator',self.sid,KEY)
        self.service = Service(self.store,{},self.context); self.binding = bridge.ApplicationBinding(self.service)
        request = bridge.RequestInfo('POST','/api/v1/uploads',{},'fixture')
        self.intent = ingestion.upload_metadata(self.binding,request,dict(request_id='metadata',idea_id=KEY,
            expected_revision=6,name='Actual.png',declared_type='image/png',size=len(PNG)))
        request = bridge.RequestInfo('PUT','unused',{},'fixture',self.intent['upload_id'])
        ingestion.upload_bytes(self.binding,request,bridge.BoundedBody(io.BytesIO(PNG),len(PNG)))

    def set_record(self,state,number=1):
        entries = self.store.asset_records(state,KEY)
        asset = next(record for record in entries if record['kind']=='asset'); source = {}
        for step in ('capture','shape'):
            record = state['ideas'][KEY]['workflow']['steps'][step]
            revision = record['acceptance']['accepted_revision']
            source[step] = dict(revision=revision,digest=source_digest(step,revision,{step:record['fields']}))
        return dict(schema_version=1,kind='design-set',set_id=identifier('set',number),idea_id=KEY,
            source_revision=state['ideas'][KEY]['revision'],session_id=self.sid,source=source,source_digest=codec.source_digest(source),
            actor='Operator',timestamp='2026-10-02T00:00:00Z',members=[dict(asset_id=asset['asset_id'],
            name=asset['name'],type=asset['validated_type'],size=asset['size'],sha256=asset['sha256'])])

    def publish_set(self,number=1):
        return self.store.mutate_assets(self.sid,'set-'+str(number),{'operation':'projection-fixture','number':number},
            lambda state:{'idea_id':KEY},prepare_records=lambda state:[self.set_record(state,number)])

    def payload(self,state,request_id='accept-set',number=1):
        idea = state['ideas'][KEY]
        return dict(request_id=request_id,idea_id=KEY,expected_revision=idea['revision'],
            expected_draft_version=idea['workflow']['draft_version'],step='visualize',proposal_id=None,
            expected_backlog_revision=None,fields=dict(disposition='accepted_set',reason=None,
            design_set_id=identifier('set',number),brief_evidence_id=None))

    def test_public_projection_exact_verified_entries_counts_orphans_and_detachment(self):
        self.publish_set(); result = self.service.state()
        with self.store.transaction() as state: expected = self.store.asset_inventory(state,KEY)
        self.assertEqual(result['asset_inventory_status'],dict(available=True,code='ok'))
        self.assertEqual(result['idea_status'],'active')
        self.assertEqual(set(result['asset_inventory']),{'records','total','projected','omitted','orphans'})
        self.assertEqual(result['asset_inventory'],dict(expected,total=3,projected=3,omitted=0))
        self.assertEqual([entry['record']['kind'] for entry in expected['records']],['upload-intent','asset','design-set'])
        self.assertEqual(expected['records'][1]['blob']['sha256'],expected['records'][1]['record']['sha256'])
        self.assertEqual(expected['records'][-1]['record']['session_id'],self.sid)
        self.assertEqual(expected['orphans'],dict(count=0,ids=[]))
        result['asset_inventory']['records'][-1]['record']['members'].clear()
        self.assertEqual(len(self.service.state()['asset_inventory']['records'][-1]['record']['members']),1)

    def test_no_selection_and_selected_empty_are_explicit(self):
        context = TrustedContext('Operator',self.sid)
        result = Service(self.store,{},context).state()
        self.assertIsNone(result['asset_inventory'])
        self.assertIsNone(result['idea_status'])
        self.assertEqual(result['asset_inventory_status'],dict(available=False,code='no_selection'))
        other_root = Path(self.temp.name)/'Empty'; sid = seed(other_root)
        result = Service(storage.Store(other_root),{},TrustedContext('Operator',sid,KEY)).state()
        self.assertEqual(result['asset_inventory'],dict(records=[],total=0,projected=0,omitted=0,orphans=dict(count=0,ids=[])))
        self.assertEqual(result['asset_inventory_status'],dict(available=True,code='ok'))
        self.assertEqual(result['idea_status'],'active')

    def test_selected_domain_status_survives_backlog_capacity_and_archival(self):
        from test_handoff_service import accepted_managed
        # Registration requires actual accepted Assess placement evidence. Keep
        # the other synthetic projection fixtures independent of that gate.
        root = Path(self.temp.name)/'Managed archive'
        workspace = Path(self.temp.name)/'Managed workspace'; workspace.mkdir()
        store = storage.Store(root,observer='Operator')
        service,key = accepted_managed(store,workspace)
        binding = bridge.ApplicationBinding(service)
        revision = service.state()['revision']
        self.assertEqual(service.context.selected_idea_id,key)
        request = bridge.RequestInfo('POST','/api/v1/uploads',{},'fixture')
        intent = ingestion.upload_metadata(binding,request,dict(request_id='archive-metadata',idea_id=key,
            expected_revision=revision,name='Actual.png',declared_type='image/png',size=len(PNG)))
        request = bridge.RequestInfo('PUT','unused',{},'fixture',intent['upload_id'])
        ingestion.upload_bytes(binding,request,bridge.BoundedBody(io.BytesIO(PNG),len(PNG)))
        blob = root/codec.blob_path(intent['asset_id'])
        self.assertEqual(blob.read_bytes(),PNG)
        with patch.object(application,'backlog_projection',side_effect=IdeaError('too_large','Fixture capacity')):
            active = service.state()
        self.assertEqual(active['idea_status'],'active')
        self.assertIsNone(active['backlog'])
        self.assertEqual(active['backlog_status'],dict(available=False,code='source_too_large'))
        plan = root.parent/'archive-plan.md'
        plan.write_text('## Idea trace\nidea_id: '+key+'\nidea_revision: '+str(revision)+'\n'
                        '## Goal\nFixture archive.\n## Tasks\nRecord one fixture.\n## Validation\nRead it.\n')
        application.run_legacy(SimpleNamespace(command='register-plan',store=str(root),idea_id=key,
            expected_revision=revision,path=str(plan),actor='Operator'), {'plan_validator_argv':None})
        with store.transaction() as state:
            self.assertEqual(state['ideas'][key]['status'],'archived')
            frozen = copy.deepcopy(state['ideas'][key])
        archived = service.state()
        self.assertEqual(archived['idea_status'],'archived')
        self.assertEqual(archived['backlog']['comparisons'][0]['status'],'archived')
        self.assertEqual(archived['asset_inventory'],active['asset_inventory'])
        self.assertEqual(blob.read_bytes(),PNG)
        with patch.object(application,'backlog_projection',side_effect=IdeaError('too_large','Fixture capacity')):
            unavailable = service.state()
        self.assertEqual(unavailable['idea_status'],'archived')
        self.assertIsNone(unavailable['backlog'])
        with store.transaction() as state:
            self.assertEqual(state['ideas'][key],frozen)

    def test_projection_capacity_withholds_whole_inventory_keeps_saved_state(self):
        self.publish_set(); before = self.service.state()
        capacity = len(json.dumps(before,ensure_ascii=False,separators=(',',':')).encode())-1
        with patch.object(application,'STATE_RESPONSE_BYTES',capacity): result = self.service.state()
        self.assertEqual(result['asset_inventory_status'],dict(available=False,code='asset_projection_capacity'))
        self.assertEqual(result['asset_inventory'],dict(records=[],total=3,projected=0,omitted=3,
            orphans=before['asset_inventory']['orphans']))
        for key,value in before.items():
            if key not in ('asset_inventory','asset_inventory_status'): self.assertEqual(result[key],value)
        self.assertLessEqual(len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode()),capacity)
        self.assertEqual(self.service.state(),before)

    def test_generic_accept_receives_verified_data_replacing_preloaded_claims(self):
        self.publish_set(); self.context.asset_inventory = {'records':[{'record':{'set_id':'forged'}}]}
        seen = []
        def validate(state,idea,payload,source,context):
            seen.append(copy.deepcopy(context.asset_inventory))
            self.assertIsNot(context,self.context)
            self.assertEqual(set(vars(context)),{'actor','session_id','selected_idea_id','asset_inventory'})
            self.assertEqual(context.asset_inventory['records'][-1]['record']['set_id'],identifier('set',1))
            context.asset_inventory['records'].clear()
        def apply(state,idea,payload,source,context):
            self.assertEqual(context.asset_inventory,seen[0])
            context.asset_inventory['orphans']['ids'].clear()
        service = Service(self.store,{},self.context,handlers={'visualize':TrustedStepHandler(validate,apply)})
        with self.store.transaction() as state: payload = self.payload(state)
        result = service.accept(payload)
        self.assertEqual(result['idea_id'],KEY); self.assertEqual(len(seen),1)
        self.assertEqual(self.context.asset_inventory,{'records':[{'record':{'set_id':'forged'}}]})
        self.assertEqual(len(service.state()['asset_inventory']['records']),3)
        self.assertEqual(service.accept(payload),result); self.assertEqual(len(seen),1)

    def test_reducer_sees_prepared_set_with_no_filesystem_or_nested_transaction(self):
        seen = []
        def validate(state,idea,payload,source,context):
            seen.append(copy.deepcopy(context.asset_inventory))
            self.assertEqual(context.asset_inventory['records'][-1]['record']['set_id'],identifier('set',1))
        service = Service(self.store,{},self.context,handlers={'visualize':TrustedStepHandler(validate)})
        def business(state):
            with patch.object(self.store,'transaction',side_effect=AssertionError('nested transaction')), \
                 patch.object(self.store,'_safe',side_effect=AssertionError('filesystem')), \
                 patch.object(Path,'open',side_effect=AssertionError('file read')):
                return service.accept_in_state(state,self.payload(state),None)
        result = self.store.mutate_assets(self.sid,'atomic-set',{'operation':'atomic-set'},business,
                                         prepare_records=lambda state:[self.set_record(state)])
        self.assertEqual(len(seen),1); self.assertEqual(len(result['asset_records']),1)
        state = service.state()
        self.assertEqual(state['accepted']['visualize']['design_set_id'],identifier('set',1))
        self.assertEqual(state['asset_inventory']['records'][-1]['record']['set_id'],identifier('set',1))

    def test_staged_set_handler_failure_rolls_back_pointer_links_receipt(self):
        before = self.service.state()
        def validate(state,idea,payload,source,context):
            self.assertEqual(context.asset_inventory['records'][-1]['record']['kind'],'design-set')
            raise IdeaError('fixture_refusal','Refuse prepared set')
        service = Service(self.store,{},self.context,handlers={'visualize':TrustedStepHandler(validate)})
        with self.assertRaises(IdeaError):
            self.store.mutate_assets(self.sid,'refused-set',{'operation':'refused-set'},
                lambda state:service.accept_in_state(state,self.payload(state),None),
                prepare_records=lambda state:[self.set_record(state)])
        self.assertEqual(self.service.state(),before)
        with self.assertRaises(IdeaError) as caught: self.store.request_result(self.sid,'refused-set')
        self.assertEqual(caught.exception.code,'request_not_found')

    def test_forged_context_cannot_replace_active_store_context(self):
        self.publish_set(); self.context.asset_inventory = self.service.state()['asset_inventory']
        service = Service(self.store,{},self.context,handlers={'visualize':TrustedStepHandler(lambda *args:None)})
        with self.store.transaction() as state: detached = copy.deepcopy(state)
        with self.assertRaises(IdeaError) as caught: service.accept_in_state(detached,self.payload(detached),None)
        self.assertEqual(caught.exception.code,'invalid_transaction')

    def test_corrupt_blob_fails_closed_before_projection(self):
        path = self.root/codec.blob_path(self.intent['asset_id'])
        path.chmod(0o640)  # Explicit external corruption of a sealed fixture.
        path.write_bytes(PNG[:-1]+b'X')
        with self.assertRaises(IdeaError): self.service.state()


if __name__=='__main__': unittest.main()
