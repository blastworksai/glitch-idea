"""Generic Visualize acceptance with real verified Store evidence. Operator."""
import copy
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
import idea_assets as ingestion
import idea_asset_evidence as codec
import idea_bridge as bridge
from idea_domain import IdeaError
from idea_service import TrustedStepHandler
from idea_steps import load_registry
import idea_steps.visualize as visual
from idea_workflow import acceptance_source, save_draft
import test_asset_projection as projection
from test_asset_store import KEY, identifier
from test_workflow import accept,complete,fields


class VisualizeStepTests(unittest.TestCase):
    set_record = projection.AssetProjectionTests.set_record
    publish_set = projection.AssetProjectionTests.publish_set
    payload = projection.AssetProjectionTests.payload

    def setUp(self):
        projection.AssetProjectionTests.setUp(self)
        self.service.handlers['visualize'] = visual.HANDLER

    def state(self):
        with self.store.transaction() as state: return copy.deepcopy(state)

    def files(self):
        return {path.relative_to(self.root).as_posix():path.read_bytes() for path in self.root.rglob('*')
                if path.is_file() and '.transactions' not in path.parts}

    def set_payload(self):
        return self.payload(self.state())

    def assert_refused(self,payload,code=None):
        before = self.files()
        with self.assertRaises(IdeaError) as caught: self.service.accept(payload)
        if code is not None: self.assertEqual(caught.exception.code,code)
        self.assertEqual(self.files(),before)

    def test_packaged_handler_registered_without_routes_or_additional_dependencies(self):
        self.assertIs(load_registry()[0]['visualize'],visual.HANDLER)
        self.assertEqual(visual.ROUTES,()); self.assertEqual(visual.HANDLER.extra_dependencies,())

    def test_generic_accept_verified_set_pointer_receipt_and_immutable_history(self):
        self.publish_set(); before = self.state(); files_before = self.files(); payload = self.set_payload()
        result = self.service.accept(payload); after = self.state()
        self.assertEqual(result['revision'],before['ideas'][KEY]['revision']+1)
        self.assertEqual(after['ideas'][KEY]['revisions'][:-1],before['ideas'][KEY]['revisions'])
        self.assertEqual(self.service.state()['accepted']['visualize']['design_set_id'],identifier('set',1))
        for path,raw in files_before.items():
            if path.startswith('assets/'): self.assertEqual((self.root/path).read_bytes(),raw)
        self.assertEqual(self.service.request_result(payload['request_id']),result)
        self.service.handlers['visualize'] = TrustedStepHandler(lambda *args:self.fail('replay handler'))
        self.assertEqual(self.service.accept(payload),result)

    def test_skipped_needs_no_reason_and_clears_pointer_keep_history(self):
        self.publish_set(); self.service.accept(self.set_payload())
        for reason in (None,'No visual design is needed for this slice'):
            disposition = 'skipped'
            payload = self.payload(self.state(),request_id='skip-'+str(reason is None))
            payload['fields'] = dict(disposition=disposition,reason=reason,
                                     design_set_id=None,brief_evidence_id=None)
            self.service.accept(payload); projected = self.service.state()
            self.assertIsNone(projected['accepted']['visualize']['design_set_id'])
            self.assertEqual(projected['steps']['visualize']['status'],disposition)
            self.assertEqual(len(projected['asset_inventory']['records']),3)

    def test_skipped_hidden_set_pointer_refused_and_not_applicable_is_invalid(self):
        self.publish_set()
        payload = self.set_payload()
        payload['fields'].update(disposition='not-applicable',reason='Nonvisual',design_set_id=None,source=None)
        self.assert_refused(payload)
        for reason,set_id in (('Explicit reason',identifier('set',1)),):
            payload = self.set_payload(); payload['fields'].update(disposition='skipped',reason=reason,design_set_id=set_id)
            self.assert_refused(payload)

    def test_accepted_set_requires_a_source_from_the_closed_enum(self):
        self.publish_set()
        for source in (None,'figma','Claude_Design'):
            payload = self.set_payload(); payload['fields']['source'] = source
            self.assert_refused(payload)
        del payload['fields']['source']
        self.assert_refused(payload)
        payload = self.set_payload(); payload['fields']['source'] = 'prototype'
        # The fixture set holds one png, not the prototype's zip plus png.
        self.assert_refused(payload,'supporting_evidence')

    def test_prototype_design_set_is_one_zip_and_one_png(self):
        self.publish_set()
        with self.store.transaction() as state: base = self.set_record(state,2)
        png = base['members'][0]
        zipped = dict(png,asset_id=identifier('asset',9),name='bundle.zip',type='application/zip')
        self.assertEqual(sorted(m['type'] for m in (png,zipped)),['application/zip','image/png'])
        fields = dict(disposition='accepted_set',reason=None,design_set_id='set_'+'1'*32,brief_evidence_id=None,
                      source='prototype',assets=[zipped['asset_id'],png['asset_id']])
        from idea_workflow import validate_step_fields
        self.assertEqual(validate_step_fields('visualize',fields)['source'],'prototype')
        for bad in ([png['asset_id'],png['asset_id']],['x'],[]):
            with self.assertRaises(IdeaError): validate_step_fields('visualize',dict(fields,assets=bad))

    def test_non_null_brief_or_proposal_has_no_fabricated_provenance(self):
        self.publish_set()
        for name,value in (('brief_evidence_id','invented-brief'),('brief_evidence_id',self.intent['asset_id'])):
            payload = self.set_payload(); payload['fields'][name] = value
            self.assert_refused(payload,'supporting_evidence')
        payload = self.set_payload(); payload['proposal_id'] = 'proposal_'+'a'*32
        self.assert_refused(payload,'supporting_evidence')

    def test_missing_invented_and_upload_ids_are_not_sets(self):
        for set_id in (identifier('set',9),self.intent['asset_id'],self.intent['upload_id'],'invented'):
            payload = self.set_payload(); payload['fields']['design_set_id'] = set_id
            self.assert_refused(payload,'asset_not_found')

    def test_foreign_real_set_cannot_be_accepted_for_this_idea(self):
        other = identifier('idea',2)
        with self.store.transaction(write=True) as state:
            idea = complete(); idea['idea_id'] = other; state['ideas'][other] = idea
            state['order'].append(other); state['backlog_revision'] += 1; self.store.commit(state)
        request = bridge.RequestInfo('POST','unused',{},'fixture')
        upload = ingestion.upload_metadata(self.binding,request,dict(request_id='other-metadata',idea_id=other,
            expected_revision=7,name='Other.png',declared_type='image/png',size=len(projection.PNG)))
        request = bridge.RequestInfo('PUT','unused',{},'fixture',upload['upload_id'])
        ingestion.upload_bytes(self.binding,request,bridge.BoundedBody(io.BytesIO(projection.PNG),len(projection.PNG)))
        def factory(state):
            record = self.set_record(state,2); record['idea_id'] = other
            record['source'] = visual.current_source(state['ideas'][other]); record['source_digest'] = codec.source_digest(record['source'])
            record['members'][0]['asset_id'] = upload['asset_id']; record['members'][0]['name'] = 'Other.png'
            return [record]
        self.store.mutate_assets(self.sid,'foreign-set',{'operation':'foreign-set'},lambda state:{'idea_id':other},prepare_records=factory)
        payload = self.set_payload(); payload['fields']['design_set_id'] = identifier('set',2)
        self.assert_refused(payload,'asset_not_found')

    def accept_changed(self,step,key):
        with self.store.transaction(write=True) as state:
            value = fields()[step]; value[key] = 'Changed accepted '+key
            state['ideas'][KEY] = accept(state['ideas'][KEY],step,value)['idea']; self.store.commit(state)

    def test_stale_discovery_source_refuses_existing_set(self):
        self.publish_set(); self.accept_changed('discovery','problem')
        with self.store.transaction(write=True) as state:
            # Exploration consumes Discovery; re-saving it leaves only the Discovery witness changed.
            state['ideas'][KEY] = accept(state['ideas'][KEY],'exploration')['idea']; self.store.commit(state)
        self.assert_refused(self.set_payload(),'stale_source')

    def test_stale_exploration_source_refuses_existing_set(self):
        self.publish_set(); self.accept_changed('exploration','outcome')
        self.assert_refused(self.set_payload(),'stale_source')

    def test_stale_capture_source_refuses_existing_set(self):
        self.publish_set()
        with self.store.transaction(write=True) as state:
            value = fields()['capture']; value['raw_text'] = 'Changed capture words'
            idea = accept(state['ideas'][KEY],'capture',value)['idea']
            for later in ('method','discovery','exploration'):
                idea = accept(idea,later,fields()[later])['idea']
            state['ideas'][KEY] = idea; self.store.commit(state)
        self.assert_refused(self.set_payload(),'stale_source')

    def test_unrelated_overall_revision_change_preserves_set_source(self):
        self.publish_set()
        with self.store.transaction(write=True) as state:
            before = visual.current_source(state['ideas'][KEY])
            value = fields()['assess']; value['assessment']['confidence'] = 'high'
            state['ideas'][KEY] = accept(state['ideas'][KEY],'assess',value)['idea']
            self.assertEqual(visual.current_source(state['ideas'][KEY]),before); self.store.commit(state)
        self.service.accept(self.set_payload())
        self.assertEqual(self.service.state()['accepted']['visualize']['design_set_id'],identifier('set',1))

    def test_priorities_change_sits_upstream_of_discovery_and_refuses_until_resaved(self):
        self.publish_set()
        with self.store.transaction(write=True) as state:
            value = fields()['priorities']; value['urgency'] = 9
            state['ideas'][KEY] = accept(state['ideas'][KEY],'priorities',value)['idea']; self.store.commit(state)
        self.assert_refused(self.set_payload(),'not_ready')

    def refuse_unaccepted_draft(self,step,key):
        self.publish_set()
        with self.store.transaction(write=True) as state:
            idea = state['ideas'][KEY]; value = fields()[step]; value[key] = 'Unaccepted draft'
            state['ideas'][KEY] = save_draft(idea,step,value,expected_revision=idea['revision'],
                expected_draft_version=idea['workflow']['draft_version'])['idea']; self.store.commit(state)
        self.assert_refused(self.set_payload())

    def test_edited_discovery_draft_is_not_current_accepted_source(self):
        self.refuse_unaccepted_draft('discovery','problem')

    def test_edited_exploration_draft_is_not_current_accepted_source(self):
        self.refuse_unaccepted_draft('exploration','outcome')

    def pure_inputs(self):
        state = self.state(); idea = state['ideas'][KEY]; payload = self.payload(state)
        context = copy.deepcopy(self.context)
        with self.store.transaction() as active: context.asset_inventory = self.store.asset_inventory(active,KEY)
        return state,idea,payload,acceptance_source(idea,'visualize',payload['fields']),context

    def test_pure_handler_leaves_all_inputs_unchanged_and_uses_no_io(self):
        self.publish_set(); args = self.pure_inputs(); before = copy.deepcopy(args)
        with patch.object(Path,'open',side_effect=AssertionError('filesystem')), \
             patch.object(self.store,'transaction',side_effect=AssertionError('nested transaction')):
            visual.validate(*args)
        self.assertEqual(args,before)

    def test_tampered_member_blob_link_and_identity_refused_in_detached_data(self):
        self.publish_set()
        for mutation in ('member','blob','evidence','foreign','duplicate'):
            with self.subTest(mutation=mutation):
                args = self.pure_inputs(); inventory = args[-1].asset_inventory; entry = inventory['records'][-1]
                if mutation=='member':
                    entry['record']['members'][0]['name'] = 'Other.png'; entry['evidence'] = codec.record_link(entry['record'])
                elif mutation=='blob': inventory['records'][1]['blob']['sha256'] = '0'*64
                elif mutation=='evidence': entry['evidence']['sha256'] = '0'*64
                elif mutation=='foreign':
                    entry['record']['idea_id'] = identifier('idea',2); entry['evidence'] = codec.record_link(entry['record'])
                else: inventory['records'].append(copy.deepcopy(entry))
                with self.assertRaises(IdeaError): visual.validate(*args)

    def test_member_count_duplicate_file_and_total_byte_caps_refuse(self):
        self.publish_set()
        for mutation in ('empty','duplicate','count','file','total','type'):
            with self.subTest(mutation=mutation):
                args = self.pure_inputs(); record = args[-1].asset_inventory['records'][-1]['record']
                member = copy.deepcopy(record['members'][0])
                if mutation=='empty': record['members'] = []
                elif mutation=='duplicate': record['members'] = [member,copy.deepcopy(member)]
                elif mutation in ('count','total'):
                    record['members'] = [dict(member,asset_id=identifier('asset',n),
                        size=codec.MAX_FILE if mutation=='total' else member['size']) for n in range(1,22 if mutation=='count' else 6)]
                elif mutation=='file': record['members'][0]['size'] = codec.MAX_FILE+1
                else: record['members'][0]['type'] = 'application/executable'
                with self.assertRaises(IdeaError): visual.validate(*args)

    def test_orphan_blob_cannot_be_fabricated_into_prepared_set(self):
        path = self.root/codec.blob_path(identifier('asset',9)); path.write_bytes(projection.PNG)
        before = self.files()
        def factory(state):
            record = self.set_record(state); record['members'][0]['asset_id'] = identifier('asset',9); return [record]
        with self.assertRaises(IdeaError):
            self.store.mutate_assets(self.sid,'orphan-set',{'operation':'orphan-set'},
                lambda state:self.service.accept_in_state(state,self.payload(state),None),prepare_records=factory)
        self.assertEqual(self.files(),before); self.assertEqual(path.read_bytes(),projection.PNG)

    def test_prepared_set_uses_same_generic_reducer_handler_atomically(self):
        result = self.store.mutate_assets(self.sid,'prepared-set',{'operation':'prepared-set'},
            lambda state:self.service.accept_in_state(state,self.payload(state),None),
            prepare_records=lambda state:[self.set_record(state)])
        self.assertEqual(len(result['asset_records']),1)
        projected = self.service.state()
        self.assertEqual(projected['accepted']['visualize']['design_set_id'],identifier('set',1))
        self.assertEqual(projected['asset_inventory']['records'][-1]['record']['set_id'],identifier('set',1))
        self.assertEqual(projected['asset_inventory']['records'][-1]['record']['session_id'],self.sid)


if __name__=='__main__': unittest.main()
