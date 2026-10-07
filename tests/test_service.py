"""shared application operations against actual Markdown storage."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
import idea_store as storage
import idea_markdown as md
import idea_transactions as tx
from idea_domain import IdeaError, now
from idea_service import Service, TrustedContext, TrustedStepHandler
from test_workflow import complete, fields

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'Café ideas'
        self.workspace=Path(self.temp.name)/'actual workspace';self.workspace.mkdir()
        self.store=storage.Store(self.root,observer='trusted-operator')
        self.sid=self.store.create_session()
        self.context=TrustedContext('trusted-operator',self.sid)
        self.service=Service(self.store,{},self.context)

    def original(self):
        return dict(request_id='capture-1',raw_text='  Café 💡\r\n\n',workspace=dict(name='Explicit label',path=str(self.workspace),confirmed=True))

    def capture(self):return self.service.capture(self.original())

    def payload(self,step='priorities',value=None,key='accept-1'):
        state=self.service.state()
        return dict(request_id=key,idea_id=state['idea_id'],expected_revision=state['revision'],expected_draft_version=state['draft_version'],step=step,
                    fields=value or dict(urgency=7,importance=8),proposal_id=None,expected_backlog_revision=None)

    def domain(self):
        with self.store.transaction() as state:return copy.deepcopy(state)

    def assert_code(self,code,callback):
        with self.assertRaises(IdeaError) as caught:callback()
        self.assertEqual(caught.exception.code,code)
        return caught.exception

    def test_capture_exact_origin_and_genuine_one_revision_workflow(self):
        before=self.service.state();self.assertIsNone(before['idea_id']);self.assertEqual(before['revision'],0)
        result=self.capture();self.assertEqual(result['revision'],1);self.assertEqual(result['write_state'],'applied')
        current=self.service.state();self.assertEqual(current['steps']['capture']['status'],'saved')
        self.assertEqual(current['session_id'],self.sid);self.assertEqual(current['backlog_revision'],1)
        detail=md.decode_detail((self.root/(result['idea_id']+'.md')).read_bytes())
        self.assertEqual(detail.metadata['idea']['origin']['text'],self.original()['raw_text'])
        self.assertEqual(detail.metadata['idea']['origin']['actor'],'trusted-operator')
        self.assertEqual(detail.metadata['idea']['workflow']['steps']['capture']['acceptance']['accepted_revision'],1)
        self.assertEqual(len(self.domain()['ideas']),1)

    def test_legacy_three_argument_handoff_callable_stays_compatible(self):
        result = self.capture(); calls = []
        def provider(state,idea,context):
            calls.append((idea['idea_id'],context.session_id))
            return None
        service = Service(self.store,{},self.context,handoff_provider=provider)
        view = service.state()
        self.assertEqual(calls,[(result['idea_id'],self.sid)])
        self.assertTrue(view['capabilities']['handoff'])
        self.assertIsNone(view['handoff'])
        self.assertEqual(view['handoff_status'],dict(available=False,code='operation_unavailable'))
        self.assertEqual(service.request_result('capture-1'),result)

    def test_empty_selection_handoff_status_has_no_provider_call(self):
        calls = []
        service = Service(self.store,{},self.context,handoff_provider=lambda *args:calls.append(args))
        view = service.state()
        self.assertEqual(calls,[])
        self.assertIsNone(view['handoff'])
        self.assertEqual(view['handoff_status'],dict(available=False,code='no_selection'))

    def test_restart_replay_reuses_durable_session_and_restores_selection(self):
        result=self.capture()
        self.workspace.rmdir()
        restarted=Service(storage.Store(self.root),{},TrustedContext('trusted-operator',self.sid))
        self.assertEqual(restarted.capture(self.original()),result)
        self.assertEqual(restarted.state()['idea_id'],result['idea_id'])
        explicit=Service(storage.Store(self.root),{},TrustedContext('trusted-operator',self.sid,result['idea_id']))
        self.assertEqual(explicit.state()['idea_id'],result['idea_id'])
        self.assertEqual(explicit.request_result('capture-1'),result)
        self.assert_code('workspace_unavailable',lambda:explicit.capture(dict(self.original(),request_id='new-capture')))
        self.assertEqual(len(self.domain()['ideas']),1)

    def test_workspace_requires_existing_host_directory_and_explicit_confirmation(self):
        missing=self.original();missing['workspace']['path']=str(self.workspace/'missing')
        self.assert_code('workspace_unavailable',lambda:self.service.capture(missing))
        relative=self.original();relative['workspace']['path']='somewhere'
        self.assert_code('workspace_unavailable',lambda:self.service.capture(relative))
        unconfirmed=self.original();unconfirmed['workspace']['confirmed']=False
        self.assert_code('invalid_input',lambda:self.service.capture(unconfirmed))
        file=self.workspace/'file';file.write_text('not directory')
        wrong=self.original();wrong['workspace']['path']=str(file)
        self.assert_code('workspace_unavailable',lambda:self.service.capture(wrong))
        self.assertEqual(len(self.domain()['ideas']),0)

    def test_trusted_workspace_resolver_runs_only_for_new_mutation(self):
        calls=[]
        def resolver(workspace):calls.append(True);return dict(workspace,path=str(self.workspace))
        service=Service(self.store,{},self.context,workspace_resolver=resolver)
        payload=self.original();payload['workspace']['path']='/trusted-selection-reference'
        result=service.capture(payload);self.assertEqual(len(calls),1)
        self.workspace.rmdir();self.assertEqual(service.capture(payload),result);self.assertEqual(len(calls),1)

    def test_partial_draft_index_unchanged_reload_then_accept_and_noop(self):
        captured=self.capture();index=(self.root/'IDEAS.md').read_bytes()
        payload=self.payload(value=dict(urgency=7,importance=None),key='draft-1');payload.pop('proposal_id');payload.pop('expected_backlog_revision')
        result=self.service.draft(payload)
        self.assertEqual(result['revision'],1);self.assertEqual(result['draft_version'],1)
        self.assertEqual((self.root/'IDEAS.md').read_bytes(),index)
        restarted=Service(storage.Store(self.root),{},TrustedContext('trusted-operator',self.sid,captured['idea_id']))
        self.assertEqual(restarted.state()['draft']['fields'],dict(urgency=7,importance=None))
        accepted=self.service.accept(self.payload());self.assertEqual(accepted['revision'],2)
        self.assertEqual(self.service.state()['steps']['priorities']['status'],'saved')
        before=self.domain();again=self.service.accept(self.payload(key='accept-again'))
        self.assertEqual(again['write_state'],'no_op');self.assertEqual(self.domain(),before)

    def test_replay_precedes_stale_cas_and_operation_part_of_hash(self):
        self.capture();payload=self.payload();result=self.service.accept(payload)
        self.assertEqual(self.service.accept(payload),result)
        different=dict(payload,request_id='stale')
        self.assert_code('stale_revision',lambda:self.service.accept(different))
        changed=dict(payload,request_id='accept-1');changed.pop('proposal_id');changed.pop('expected_backlog_revision')
        self.assert_code('request_conflict',lambda:self.service.draft(changed))
        self.assertEqual(self.service.request_result('accept-1'),result)

    def test_exact_payloads_reject_actor_extras_types_limits_before_mutation(self):
        for payload in [dict(self.original(),actor='forged'),dict(self.original(),other=True),[],
                        dict(self.original(),raw_text=1),dict(self.original(),request_id=True),
                        dict(self.original(),raw_text='x'*(1024*1024))]:
            with self.subTest(payload_type=type(payload)),self.assertRaises(IdeaError):self.service.capture(payload)
        self.assertEqual(len(self.domain()['ideas']),0)
        self.capture()
        for edit in [dict(self.payload(),expected_revision=True),dict(self.payload(),expected_draft_version=False),
                     dict(self.payload(),actor='forged'),dict(self.payload(),fields={'urgency':7,'importance':8,'extra':1}),
                     dict(self.payload(),expected_backlog_revision=0),dict(self.payload(),proposal_id='proposal_fake')]:
            with self.subTest(edit=edit),self.assertRaises(IdeaError):self.service.accept(edit)
        self.assertEqual(self.domain()['ideas'][self.context.selected_idea_id]['revision'],1)

    def test_advanced_draft_available_but_accept_missing_handler_unavailable(self):
        self.capture();self.service.accept(self.payload())
        value=fields()['method'];draft=self.payload('method',value,key='draft-method');draft.pop('proposal_id');draft.pop('expected_backlog_revision')
        result=self.service.draft(draft);self.assertEqual(result['revision'],2)
        self.assert_code('step_unavailable',lambda:self.service.accept(self.payload('method',value,key='method')))
        self.assertEqual(self.service.state()['drafts']['method'],value)
        self.assert_code('derived_step',lambda:self.service.accept(self.payload('review',dict(handoff_id='handoff-fixture',source_revision=2),key='review')))

    def test_trusted_handler_guards_proposal_source_and_atomic_apply(self):
        self.capture();self.service.accept(self.payload())
        checks=[];applied=[]
        def validate(state,idea,payload,source,context):
            checks.append((context.session_id,idea['revision'],source['source_digest']))
            if payload['proposal_id']!='current-proposal':raise IdeaError('stale_source','Fixture proposal does not match current source')
        def apply(state,idea,payload,source,context):applied.append(idea['revision'])
        self.service=Service(self.store,{},self.context,handlers={'method':TrustedStepHandler(validate,apply)})
        value=fields()['method'];payload=self.payload('method',value,key='method')
        self.assert_code('stale_source',lambda:self.service.accept(payload));self.assertEqual(self.service.state()['revision'],2)
        payload['proposal_id']='current-proposal';result=self.service.accept(payload)
        self.assertEqual(result['revision'],3);self.assertEqual(len(applied),1)
        replay=Service(self.store,{},self.context)
        self.assertEqual(replay.accept(payload),result) # handler absent, receipt first
        again=self.payload('method',value,key='method-noop');again['proposal_id']='current-proposal'
        self.assertEqual(self.service.accept(again)['write_state'],'no_op');self.assertEqual(len(applied),1)
        self.assertEqual(checks[0][0],self.sid)

    def test_trusted_validation_cannot_mutate_and_registry_is_fixed(self):
        self.capture();self.service.accept(self.payload())
        def invalid(state,*args):state['backlog_revision']+=1
        service=Service(self.store,{},self.context,handlers={'method':TrustedStepHandler(invalid)})
        self.assert_code('invalid_handler',lambda:service.accept(self.payload('method',fields()['method'],key='method')))
        self.assertEqual(self.domain()['backlog_revision'],1)
        with self.assertRaises(IdeaError):Service(self.store,{},self.context,handlers={'arbitrary.module':TrustedStepHandler(lambda *args:None)})

    def test_priorities_change_invalidates_actual_advanced_acceptance(self):
        value=complete();key=value['idea_id']
        with self.store.transaction(write=True) as state:
            state['ideas'][key]=value;state['order']=[key];state['backlog_revision']=1;self.store.commit(state)
        self.context.selected_idea_id=key
        old=copy.deepcopy(value['workflow']['steps']['assess']['acceptance'])
        result=self.service.accept(self.payload(value=dict(urgency=9,importance=8)))
        self.assertEqual(result['revision'],value['revision']+1)
        current=self.service.state();self.assertEqual(current['steps']['assess']['status'],'review-needed')
        self.assertEqual(self.domain()['ideas'][key]['workflow']['steps']['assess']['acceptance'],old)
        self.assertIn('assess',result['invalidated'])

    def accepted_change_marks_exploration(self,step,changed):
        value=complete();key=value['idea_id']
        with self.store.transaction(write=True) as state:
            state['ideas'][key]=value;state['order']=[key];state['backlog_revision']=1;self.store.commit(state)
        self.context.selected_idea_id=key
        self.assertEqual(self.service.state()['steps']['exploration']['status'],'saved')
        old=copy.deepcopy(value['workflow']['steps']['exploration']['acceptance'])
        service=Service(self.store,{},self.context,handlers={step:TrustedStepHandler(lambda *args:None)})
        result=service.accept(self.payload(step,changed,key=step+'-change'))
        self.assertIn('exploration',result['invalidated'])
        self.assertEqual(service.state()['steps']['exploration']['status'],'review-needed')
        self.assertEqual(self.domain()['ideas'][key]['workflow']['steps']['exploration']['acceptance'],old)

    def test_method_change_after_exploration_marks_exploration_review_needed(self):
        changed=fields()['method'];changed['selection']='adaptive-slices'
        self.accepted_change_marks_exploration('method',changed)

    def test_discovery_change_after_exploration_marks_exploration_review_needed(self):
        changed=fields()['discovery'];changed['problem']='A different problem'
        self.accepted_change_marks_exploration('discovery',changed)

    def test_assess_handler_composes_placement_with_backlog_cas_in_same_commit(self):
        value=complete();key=value['idea_id']
        with self.store.transaction(write=True) as state:
            state['ideas'][key]=value;state['order']=[key];state['backlog_revision']=1;self.store.commit(state)
        other=self.capture()['idea_id'];self.service.state(key)
        def validate(state,idea,payload,source,context):
            position=payload['fields']['position']
            if position['actual_position']!=2 or position['neighbors']!={'before':other,'after':None}:
                raise IdeaError('stale_source','Placement fixture does not match actual neighbors')
        def apply(state,idea,payload,source,context):
            old_revision=state['backlog_revision']
            state['order']=[other,key];state['backlog_revision']+=1
            state['placements'].append(dict(idea_id=key,idea_revision=idea['revision'],position=2,reason='Fixture validated placement',
                actor=context.actor,timestamp=now(),source_backlog_revision=old_revision,
                accepted_backlog_revision=state['backlog_revision'],neighbors={'before':other,'after':None},
                snapshot=dict(ratings=copy.deepcopy(idea['ratings']),assessments=copy.deepcopy(idea['assessments']))))
        service=Service(self.store,{},self.context,handlers={'assess':TrustedStepHandler(validate,apply)})
        answer=fields()['assess'];answer['position'].update(proposed_position=2,actual_position=2,neighbors={'before':other,'after':None})
        payload=self.payload('assess',answer,key='assess');payload['expected_backlog_revision']=1
        self.assert_code('stale_backlog',lambda:service.accept(payload))
        payload['expected_backlog_revision']=True
        self.assert_code('invalid_input',lambda:service.accept(payload))
        payload['expected_backlog_revision']=2
        result=service.accept(payload)
        self.assertEqual(result['revision'],value['revision']+2);self.assertEqual(result['backlog_revision'],3)
        current=self.domain();self.assertEqual(current['order'],[other,key]);self.assertEqual(len(current['placements']),1)
        self.assertEqual(current['placements'][0]['idea_revision'],result['revision'])
        self.assertTrue((self.root/'history/backlog/r1.md').exists())
        self.assertEqual(service.accept(payload),result);self.assertEqual(self.domain(),current)

    def test_unknown_selection_missing_session_and_no_silent_new_session(self):
        self.assert_code('not_found',lambda:self.service.state('idea_'+'f'*32))
        self.capture();path=self.root/'session-recovery'/(self.sid+'.json');path.unlink()
        self.assert_code('receipt_session_missing',lambda:self.service.state())
        self.assert_code('receipt_session_missing',lambda:self.service.capture(self.original()))
        self.assertFalse(path.exists());self.assertEqual(len(self.domain()['ideas']),1)

    def test_comments_refused_without_dropping_local_or_persisted_data(self):
        self.capture();key=self.context.selected_idea_id;path=self.root/(key+'.md')
        raw=path.read_bytes().replace(b'kind: idea',b'kind: idea # keep');path.write_bytes(raw)
        payload=self.payload();self.assert_code('yaml_comments',lambda:self.service.accept(payload))
        self.assertEqual(path.read_bytes(),raw)
        self.assert_code('request_not_found',lambda:self.service.request_result(payload['request_id']))

    def test_committed_uncertain_capture_reconciles_and_never_duplicates(self):
        original=tx.publish
        def publish(*args,**kwargs):
            def stop(phase):
                if phase=='published:session-recovery/'+self.sid+'.json':raise OSError('fixture failure')
            return original(*args,**kwargs,_checkpoint=stop)
        with patch.object(storage.transactions,'publish',publish):
            error=self.assert_code('durability_uncertain',lambda:self.capture())
        self.assertTrue(error.details['committed'])
        result=self.service.request_result('capture-1')
        self.assertEqual(self.service.capture(self.original()),result)
        self.assertEqual(len(self.domain()['ideas']),1)
        self.assertEqual(self.service.state()['revision'],1)

if __name__=='__main__':unittest.main()
