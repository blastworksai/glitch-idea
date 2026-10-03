"""Verified provider delivery over real journal-published packets. Operator."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
import idea_handoff as handoffs
import idea_store as storage
from idea_bridge import ApplicationBinding
from idea_domain import IdeaError, MAX_INPUT, digest
from idea_service import Service, TrustedContext
from test_handoff_store import ACTOR, publication_seed, publication_payload, seed
from test_workflow import accept


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'Provider café'
        self.sid,self.key,self.workspace = publication_seed(self.root)
        self.store = storage.Store(self.root,observer=ACTOR)
        self.context = TrustedContext(ACTOR,self.sid,self.key)
        self.provider = handoffs.HandoffProvider(self.store)
        self.service = Service(self.store,{},self.context,handoff_provider=self.provider)
        self.binding = ApplicationBinding(self.service)

    def files(self):
        return {p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def payload(self,request='handoff-1'):
        return publication_payload(self.store,self.key,request)['payload']

    def publish(self,request='handoff-1',payload=None):
        return handoffs.handoff(self.binding,None,self.payload(request) if payload is None else payload)

    def refused(self,code,callback):
        with self.assertRaises(IdeaError) as caught: callback()
        self.assertEqual(caught.exception.code,code)

    def edit_shape(self):
        with self.store.transaction(write=True) as state:
            idea = state['ideas'][self.key]
            state['ideas'][self.key] = accept(idea,'shape',dict(idea['workflow']['steps']['shape']['fields'],outcome='Next slice'))['idea']
            self.store.commit(state)

    def test_actual_delivery_exact_store_preflight_envelope_paths_and_trace(self):
        result = self.publish(); compact = self.store.request_result(self.sid,'handoff-1')
        self.assertEqual(set(result),set(compact)|{'handoff','handoff_current'})
        self.assertEqual(set(result['handoff']),handoffs.PUBLIC_FIELDS)
        self.assertEqual({k:result[k] for k in compact},compact)
        self.assertTrue(result['handoff_current'])
        self.assertEqual(result['handoff']['path'],str(self.root/compact['path']))
        self.assertIn('## Idea trace\nidea_id: '+self.key+'\nidea_revision: 6\n',result['handoff']['prompt'])
        self.assertTrue(result['handoff']['prompt'].startswith('/glitch-plan\n'))
        self.assertEqual(self.service.request_result('handoff-1'),result)

    def test_selected_state_projects_once_and_only_current_exact_two_fields(self):
        before = self.files()
        initial = self.service.state(); self.assertIsNone(initial['handoff'])
        self.assertEqual(initial['steps']['review']['status'],'current')
        self.assertEqual(self.files(),before)
        result = self.publish(); before = self.files()
        with patch.object(self.provider,'project',wraps=self.provider.project) as projected:
            view = self.service.state(); self.assertEqual(projected.call_count,1)
        self.assertEqual(view['handoff'],result['handoff'])
        self.assertEqual(view['handoff_status'],dict(available=True,code='ok'))
        self.assertEqual(view['accepted']['review'],{name:result['handoff'][name] for name in ('handoff_id','source_revision')})
        self.assertEqual(view['steps']['review']['status'],'saved')
        self.assertEqual(self.files(),before)

    def test_same_revision_workspace_drift_marks_historical_review_needed_and_replay(self):
        payload = self.payload(); original = self.publish(payload=payload)
        self.workspace.rmdir(); before = self.files()
        view = self.service.state()
        self.assertEqual(view['revision'],original['revision'])
        self.assertEqual(view['handoff'],original['handoff'])
        self.assertEqual(view['handoff_status'],dict(available=False,code='workspace_unavailable'))
        self.assertEqual(view['steps']['review']['status'],'review-needed')
        self.assertIsNone(view['accepted']['review'])
        replay = self.publish(payload=payload)
        self.assertEqual(replay,dict(original,code='historical_handoff',handoff_current=False))
        self.assertEqual(self.service.request_result('handoff-1'),replay)
        self.assertEqual(self.files(),before)
        self.refused('workspace_unavailable',lambda:self.publish('fresh'))

    def test_moved_store_projects_original_location_historically_and_new_packet_as_current(self):
        payload = self.payload(); first = self.publish(payload=payload); before = self.files()
        original_root = self.root; moved = self.root.with_name('Moved provider café')
        self.root.rename(moved); self.root = moved
        self.store = storage.Store(moved,observer=ACTOR)
        self.provider = handoffs.HandoffProvider(self.store)
        self.service = Service(self.store,{},self.context,handoff_provider=self.provider)
        self.binding = ApplicationBinding(self.service)
        view = self.service.state()
        self.assertEqual(view['handoff'],first['handoff'])
        self.assertEqual(view['handoff']['path'],str(original_root/first['path']))
        self.assertEqual(view['handoff_status'],dict(available=False,code='stale_source'))
        self.assertIsNone(view['accepted']['review'])
        self.assertEqual(view['steps']['review']['status'],'review-needed')
        listing = self.provider.ideas(self.context)
        self.assertEqual(listing['ideas'][0]['status'],'review-needed')
        self.assertEqual(listing['ideas'][0]['detail_path'],str(moved/(self.key+'.md')))
        historical = dict(first,code='historical_handoff',handoff_current=False)
        self.assertEqual(self.service.request_result(payload['request_id']),historical)
        self.assertEqual(self.publish(payload=payload),historical)
        self.assertEqual(self.files(),before)
        fresh = self.publish('moved-new')
        self.assertTrue(fresh['handoff_current']); self.assertEqual(fresh['write_state'],'applied')
        self.assertEqual(fresh['handoff_index'],2)
        self.assertNotEqual(fresh['handoff_id'],first['handoff_id'])
        self.assertEqual(fresh['handoff']['path'],str(moved/fresh['path']))
        for name in ('revision','draft_version','backlog_revision'):
            self.assertEqual(fresh[name],first[name])
        self.assertEqual(self.service.state()['handoff'],fresh['handoff'])
        self.assertEqual(self.service.state()['handoff_status'],dict(available=True,code='ok'))
        self.assertEqual(self.provider.ideas(self.context)['ideas'][0]['status'],'ready-to-plan')
        self.assertEqual(self.service.request_result(payload['request_id']),historical)
        self.assertEqual((moved/first['path']).read_bytes(),before[first['path']])

    def test_safe_respelling_retains_canonical_current_public_location(self):
        first = self.publish(); spelling = self.root.parent/'Spelling'; spelling.mkdir()
        self.store = storage.Store(spelling/'..'/self.root.name,observer=ACTOR)
        self.provider = handoffs.HandoffProvider(self.store)
        self.service = Service(self.store,{},self.context,handoff_provider=self.provider)
        self.binding = ApplicationBinding(self.service)
        self.assertEqual(self.service.state()['handoff_status'],dict(available=True,code='ok'))
        reused = self.publish('canonical-reuse')
        self.assertEqual(reused['write_state'],'no_op')
        self.assertEqual(reused['handoff'],first['handoff'])

    def test_safe_respelling_ideas_full_projection_is_canonical_and_read_only(self):
        for published in (False,True):
            with self.subTest(published=published):
                if published: self.publish()
                expected = self.provider.ideas(self.context)
                before = self.files(); selected = self.context.selected_idea_id
                spelling = self.root.parent/('Published spelling' if published else 'Incomplete spelling')
                spelling.mkdir()
                store = storage.Store(spelling/'..'/self.root.name,observer=ACTOR)
                self.assertIn('..',store.path.parts,'Fixture must retain the safe alias spelling')
                actual = handoffs.HandoffProvider(store).ideas(self.context)
                self.assertEqual(actual,expected,'Complete projection must match canonical Store, not just the path')
                self.assertEqual(actual['total'],1)
                self.assertEqual(actual['ideas'][0]['status'],'ready-to-plan' if published else 'in-progress')
                self.assertEqual(actual['ideas'][0]['detail_path'],str((self.root/(self.key+'.md')).resolve(strict=True)))
                self.assertNotIn('..',Path(actual['ideas'][0]['detail_path']).parts)
                self.assertEqual(self.context.selected_idea_id,selected)
                self.assertEqual(self.files(),before)

    def test_actual_typed_client_accepts_ideas_projection_under_safe_respelling(self):
        spelling = self.root.parent/'Client spelling'; spelling.mkdir()
        store = storage.Store(spelling/'..'/self.root.name,observer=ACTOR)
        self.assertIn('..',store.path.parts,'Fixture must retain the safe alias spelling')
        reply = handoffs.HandoffProvider(store).ideas(self.context)
        script = """
import fs from 'node:fs';
const source=fs.readFileSync(process.argv[1],'utf8');
const {IdeaApi}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const api=new IdeaApi(async()=>({ok:true,status:200,json:async()=>structuredClone(input.reply)}),1000,'binding_'+'a'.repeat(32));
api.sessionId=input.session;
try { process.stdout.write(JSON.stringify(await api.ideas())); }
catch (error) { process.stdout.write(JSON.stringify({rejected:error.code??String(error)})); }
"""
        api = Path(__file__).resolve().parents[1]/'glitch-idea/web/api.js'
        result = subprocess.run(['node','--input-type=module','-e',script,str(api)],
                                input=json.dumps(dict(reply=reply,session=self.sid)),text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),reply,'The typed client must accept the Ideas reply unchanged')

    def test_legacy_json_ideas_missing_detail_leaf_remains_readable_through_safe_alias(self):
        from idea_domain import encoded
        from test_workflow import original_idea
        root = self.root.parent/'Legacy provider'; root.mkdir()
        idea = original_idea(); key = idea['idea_id']; state = storage.empty_state()
        state.update(ideas={key:idea},order=[key],backlog_revision=1)
        raw = encoded(state); (root/'state.json').write_bytes(raw)
        (root/'session-recovery').mkdir()
        (root/'session-recovery'/(self.sid+'.json')).write_bytes(
            (self.root/'session-recovery'/(self.sid+'.json')).read_bytes())
        context = TrustedContext(ACTOR,self.sid,key)
        expected = handoffs.HandoffProvider(storage.Store(root,observer=ACTOR)).ideas(context)
        before = {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
        spelling = root.parent/'Legacy spelling'; spelling.mkdir()
        store = storage.Store(spelling/'..'/root.name,observer=ACTOR)
        self.assertIn('..',store.path.parts)
        actual = handoffs.HandoffProvider(store).ideas(context)
        self.assertEqual(actual,expected)
        self.assertEqual(actual['total'],1)
        self.assertEqual(actual['ideas'][0]['idea_id'],key)
        self.assertEqual(actual['ideas'][0]['detail_path'],str(root.resolve(strict=True)/(key+'.md')))
        self.assertEqual(context.selected_idea_id,key)
        self.assertFalse((root/(key+'.md')).exists())
        self.assertFalse((root/'IDEAS.md').exists())
        self.assertEqual((root/'state.json').read_bytes(),raw)
        self.assertEqual({p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()},before)

    def test_postpublication_faults_carry_exact_durable_identity_and_preflight_does_not(self):
        for request,write_state,code in (('fault-applied','applied','store_busy'),('fault-applied','applied','corrupt_store'),
                                         ('fault-reuse','no_op','handoff_capacity'),('fault-reuse','no_op','store_busy')):
            with self.subTest(request=request,write_state=write_state,code=code):
                payload = self.payload(request)
                with patch.object(self.provider,'reconcile',side_effect=IdeaError(code,'Private diagnostic')):
                    with self.assertRaises(IdeaError) as caught: self.publish(payload=payload)
                error = caught.exception
                self.assertEqual(error.code,code); self.assertTrue(error.details['committed'])
                compact = self.store.request_result(self.sid,request)
                self.assertEqual(compact['write_state'],write_state)
                self.assertEqual(error.details,dict(committed=True,**{name:compact[name] for name in
                    ('request_id','idea_id','revision','draft_version','backlog_revision')}))
                recovered = self.service.request_result(request)
                self.assertTrue(recovered['handoff_current'])
                self.assertEqual({name:recovered[name] for name in compact},compact)
        before = self.files()
        with patch.object(self.store,'_handoff_http_preflight',side_effect=IdeaError('handoff_capacity','Fixture capacity')):
            with self.assertRaises(IdeaError) as caught: self.publish('refused-before-commit')
        self.assertEqual(caught.exception.code,'handoff_capacity')
        self.assertNotIn('committed',caught.exception.details)
        self.assertEqual(self.files(),before)
        self.refused('request_not_found',lambda:self.store.request_result(self.sid,'refused-before-commit'))

    def test_edit_retains_original_reconciliation_and_ideas_review_needed(self):
        result = self.publish(); self.edit_shape(); before = self.files()
        recovered = self.service.request_result('handoff-1')
        self.assertEqual(recovered['handoff'],result['handoff'])
        self.assertFalse(recovered['handoff_current'])
        self.assertEqual(recovered['code'],'historical_handoff')
        self.assertEqual(self.service.state()['steps']['review']['status'],'review-needed')
        self.assertEqual(handoffs.ideas(self.binding,None,None)['ideas'][0]['status'],'review-needed')
        self.assertEqual(self.files(),before)

    def test_exact_requested_ordinal_is_used_instead_of_newest_packet(self):
        root = Path(self.temp.name)/'Two historical packets'
        sid,key,packets,links = seed(root,count=2)
        store = storage.Store(root,observer=ACTOR); provider = handoffs.HandoffProvider(store)
        context = TrustedContext(ACTOR,sid,key)
        compact = store.request_result(sid,'handoff-fixture-1')
        reply = provider.reconcile(compact,context,'handoff-fixture-1')
        self.assertEqual(reply['handoff']['handoff_id'],packets[0]['handoff_id'])
        self.assertNotEqual(reply['handoff']['handoff_id'],packets[1]['handoff_id'])
        self.assertEqual(reply['handoff']['path'],str(root/links[0]['path']))
        self.assertFalse(reply['handoff_current'])

    def test_foreign_session_or_modified_receipt_cannot_reconstruct_packet(self):
        result = self.publish(); compact = self.store.request_result(self.sid,'handoff-1')
        foreign = TrustedContext(ACTOR,self.store.create_session(),self.key)
        self.refused('corrupt_receipts',lambda:self.provider.reconcile(compact,foreign,'handoff-1'))
        for name,value in (('request_id','wrong'),('handoff_index',2),('sha256','f'*64)):
            changed = dict(compact,**{name:value})
            self.refused('corrupt_receipts',lambda:self.provider.reconcile(changed,self.context,'handoff-1'))
        self.assertEqual(self.service.request_result('handoff-1'),result)

    def test_integrity_faults_propagate_and_active_store_scope_is_required(self):
        self.publish()
        self.refused('invalid_transaction',lambda:self.provider.project({}, {'idea_id':self.key},self.context))
        with patch.object(self.store,'handoff_observations',side_effect=IdeaError('corrupt_store','Fixture fault')):
            self.refused('corrupt_store',self.service.state)

    def test_new_same_source_request_reuses_packet_and_original_publishing_identity(self):
        first = self.publish(); second = self.publish('handoff-2')
        self.assertEqual(second['write_state'],'no_op')
        self.assertEqual(second['handoff'],first['handoff'])
        self.assertTrue(second['handoff_current'])
        self.assertEqual(self.service.request_result('handoff-2'),second)

    def test_archived_historical_packet_remains_visible_without_enabling_copy(self):
        original = self.publish()
        # Trusted archive fixture, not a claim that a plan validator was run.
        with self.store.transaction(write=True) as state:
            idea = state['ideas'][self.key]; plan_id = 'plan_'+'8'*32; content = '# Frozen plan fixture\n'
            idea['plans'].append(dict(plan_id=plan_id,idea_id=self.key,idea_revision=idea['revision'],
                path=str(self.root/'plan-evidence'/(plan_id+'.md')),source_path=str(self.root/'working-plan.md'),
                content=content,sha256=digest(content.encode()),actor=ACTOR,timestamp='2026-10-02',
                validation=dict(builtin='idea-trace-and-sections-v1')))
            idea['status'] = 'archived'
            state['archives'][self.key+'/r'+str(idea['revision'])+'.json'] = dict(
                idea_id=self.key,origin=copy.deepcopy(idea['origin']),revision=copy.deepcopy(idea['revisions'][-1]))
            self.store.commit(state)
        self.workspace.rmdir(); before = self.files()
        view = self.service.state()
        self.assertEqual(view['handoff'],original['handoff'])
        self.assertEqual(view['handoff_status'],dict(available=False,code='archived_revision'))
        self.assertEqual(handoffs.ideas(self.binding,None,None)['ideas'][0]['status'],'archived')
        recovered = self.service.request_result('handoff-1')
        self.assertEqual(recovered,dict(original,code='historical_handoff',handoff_current=False))
        self.refused('archived_revision',lambda:self.publish('after-archive'))
        self.assertEqual(self.files(),before)

    def test_exact_payload_types_paths_and_commands_are_refused_without_publication(self):
        before = self.files(); payload = self.payload()
        for extra in ('path','command','validator','actor'):
            self.refused('invalid_input',lambda:self.publish(payload=dict(payload,**{extra:'browser-value'})))
        self.refused('invalid_input',lambda:self.publish(payload=dict(payload,expected_revision=True)))
        self.assertEqual(self.files(),before)

    def test_ideas_complete_authoritative_row_and_status_without_mutation(self):
        before = self.files(); listing = handoffs.ideas(self.binding,None,None)
        self.assertEqual(set(listing),{'ok','code','backlog_revision','total','ideas'})
        self.assertEqual(listing['total'],1)
        with self.store.transaction() as state:
            idea = state['ideas'][self.key]
            self.assertEqual(listing['ideas'],[dict(idea_id=self.key,revision=6,position=1,
                title=idea['workflow']['steps']['capture']['fields']['raw_text'],status='in-progress',
                method='bounded-plan',updated=idea['revisions'][-1]['timestamp'],detail_path=str(self.root/(self.key+'.md')),current_step='review',completed_steps=6)])
        self.assertEqual(self.files(),before)
        self.publish()
        self.assertEqual(handoffs.ideas(self.binding,None,None)['ideas'][0]['status'],'ready-to-plan')
        self.assertEqual(self.context.selected_idea_id,self.key)
        self.refused('invalid_input',lambda:handoffs.ideas(self.binding,None,{}))

    def test_complete_response_capacity_refuses_delivery_and_ideas_without_partial_data(self):
        result = self.publish(); before = self.files()
        oversized = dict(result['handoff'],prompt='💡'*MAX_INPUT)
        # A bounded rendering fault fixture, after real packet/receipt reads;
        # the Store writer's actual generated-prompt guard is qualified separately.
        with patch.object(self.provider,'_public',return_value=oversized):
            self.refused('handoff_capacity',lambda:self.service.request_result('handoff-1'))
            self.refused('handoff_capacity',self.service.state)
        self.assertEqual(self.files(),before)
        self.refused('ideas_capacity',lambda:handoffs._bounded(dict(ok=True,code='ok',ideas=[{'title':'💡'*MAX_INPUT}]),'ideas_capacity'))

    def test_ideas_keeps_entire_actual_order_and_unicode_title_scalar_bound(self):
        captured = self.service.capture(dict(request_id='second-capture',raw_text='Second 💡 café '+('💡'*210),
            workspace=dict(name='Actual workspace',path=str(self.workspace),confirmed=True)))
        selected = self.context.selected_idea_id; before = self.files()
        listing = handoffs.ideas(self.binding,None,None)
        with self.store.transaction() as state:
            self.assertEqual(listing['total'],len(state['order']))
            self.assertEqual([row['idea_id'] for row in listing['ideas']],state['order'])
            self.assertEqual([row['position'] for row in listing['ideas']],list(range(1,len(state['order'])+1)))
            row = next(row for row in listing['ideas'] if row['idea_id'] == captured['idea_id'])
            self.assertEqual(row['title'],state['ideas'][captured['idea_id']]['origin']['text'][:200])
            self.assertEqual(len(row['title']),200)
            self.assertIsNone(row['method'])
        self.assertEqual(listing['total'],2)
        self.assertEqual(self.context.selected_idea_id,selected)
        self.assertEqual(self.files(),before)

    def test_selection_exact_reply_private_reset_and_saved_id_no_domain_receipts(self):
        before = self.files()
        empty = handoffs.selection(self.binding,None,{'idea_id':None})
        self.assertEqual(empty,dict(ok=True,code='ok',session_id=self.sid,idea_id=None,
                                   revision=0,draft_version=0,backlog_revision=2))
        self.assertIsNone(self.context.selected_idea_id)
        self.assertIsNone(self.service.state()['handoff'])
        with self.store.transaction() as state: draft = state['ideas'][self.key]['workflow']['draft_version']
        saved = handoffs.selection(self.binding,None,{'idea_id':self.key})
        self.assertEqual(saved,dict(empty,idea_id=self.key,revision=6,draft_version=draft))
        self.assertEqual(handoffs.selection(self.binding,None,{'idea_id':self.key}),saved)
        self.assertEqual(self.files(),before)
        self.refused('not_found',lambda:handoffs.selection(self.binding,None,{'idea_id':'idea_'+'f'*32}))
        self.assertEqual(self.context.selected_idea_id,self.key)
        for value in ({'idea_id':None,'request_id':'extra'},{'idea_id':True}):
            self.refused('invalid_input',lambda:handoffs.selection(self.binding,None,value))


if __name__ == '__main__':
    unittest.main()
