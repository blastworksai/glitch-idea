"""Safe initiating-agent results and Method provenance. Operator."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
import idea_agent_memory as memory
import idea_agent_source as source
import idea_store as storage
from idea_domain import IdeaError
from idea_service import TrustedContext
from idea_workflow import save_draft, source_digest
from test_workflow import accept, complete, fields

KEY=complete()['idea_id']
FOUND=dict(status='found',sources=['memory:preference-42','Memory/USER.md#planning'],
           rationale='Recorded preference for bounded investment.',preferred_method='bounded-plan')
VARIED=dict(status='varied',sources=['memory:preference-42'],rationale='Past choices differed.',preferred_method=None)
EMPTY=dict(status='searched_no_preference',sources=[],rationale='Bounded search found no preference.')
UNAVAILABLE=dict(status='unavailable',sources=[],rationale=None)
ERROR=dict(status='error',sources=[],rationale='Retrieval failed.')
LIVE=dict(binding_id='binding_'+'2'*32,generation='agent_'+'3'*32,
          session_id='session_'+'4'*32,agent_status='connected')


class ResultTests(unittest.TestCase):
    def assert_code(self,code,fn):
        with self.assertRaises(IdeaError) as caught:fn()
        self.assertEqual(caught.exception.code,code)

    def test_four_statuses_preserve_actual_claim_and_detach(self):
        for value in (FOUND,VARIED,EMPTY,UNAVAILABLE,ERROR):
            before=copy.deepcopy(value)
            checked=memory.validate_result(value)
            self.assertEqual(checked,before)
            checked['sources'].append('memory:detached')
            self.assertEqual(value,before)
        self.assertNotEqual(memory.validate_result(UNAVAILABLE)['status'],'searched_no_preference')
        self.assertNotEqual(memory.validate_result(ERROR)['status'],'searched_no_preference')

    def test_exact_schema_rejects_raw_memory_credentials_and_missing_search_status(self):
        for key in ('raw_memory','retrieval','token','actor','selection'):
            with self.subTest(key=key),self.assertRaises(IdeaError):
                memory.validate_result(dict(FOUND,**{key:'refused'}))
        for value in ({'sources':[],'rationale':None},dict(status=None,sources=[],rationale=None),
            dict(status='not_searched',sources=[],rationale=None)):
            with self.assertRaises(IdeaError):memory.validate_result(value)

    def test_found_requires_references_and_rationale_unavailable_error_claim_none(self):
        for value in (dict(FOUND,sources=[]),dict(FOUND,rationale=None),dict(FOUND,rationale=''),
            dict(UNAVAILABLE,sources=['memory:false']),dict(ERROR,sources=['memory:false']),
            {key:val for key,val in FOUND.items() if key!='preferred_method'},dict(FOUND,preferred_method=None),
            dict(FOUND,preferred_method='not-a-method'),dict(VARIED,preferred_method='bounded-plan'),
            dict(EMPTY,preferred_method='bounded-plan')):
            with self.assertRaises(IdeaError):memory.validate_result(value)

    def test_safe_reference_and_concise_result_limits(self):
        for reference in ('/home/other/memory.md','../Memory.md','Memory/../USER.md',
            'C:/Users/user/memory.md','memory:/absolute','memory://private','https://host/private',
            'memory:memory-private/preference','Memory/private\\notes.md','Authorization:Bearer-secret',
            'contains spaces','memory:','memory:foo:bar','memory:x\nsecret'):
            with self.subTest(reference=reference),self.assertRaises(IdeaError):
                memory.validate_result(dict(FOUND,sources=[reference]))
        with self.assertRaises(IdeaError):memory.validate_result(dict(FOUND,sources=['memory:duplicate']*2))
        self.assert_code('too_large',lambda:memory.validate_result(dict(FOUND,sources=['memory:r'+str(n) for n in range(17)])))
        with self.assertRaises(IdeaError):memory.validate_result(dict(FOUND,sources=['memory:'+'x'*257]))
        self.assert_code('too_large',lambda:memory.validate_result(dict(FOUND,rationale='x'*2049)))

    def test_no_preference_does_not_preselect_method_or_change_other_fields(self):
        output=memory.validate_proposal('memory',EMPTY)
        self.assertEqual(set(output),{'status','sources','rationale'})
        self.assertNotIn('selection',output)
        # R8: a method proposal is the memory result only; the agent never selects a method.
        proposed={'memory':copy.deepcopy(EMPTY)}
        checked=memory.validate_proposal('method',proposed)
        self.assertEqual(checked,proposed)
        checked['memory']['sources'].append('memory:copy')
        self.assertEqual(proposed['memory'],EMPTY)
        for extra in (dict(selection='experiment-led'),dict(reason='Because')):
            with self.subTest(extra=extra),self.assertRaises(IdeaError):
                memory.validate_proposal('method',dict(proposed,**extra))

    def test_broker_validation_enforces_safe_memory_inside_method(self):
        proposed={'memory':dict(FOUND,sources=['/absolute/private'])}
        with self.assertRaises(IdeaError):memory.validate_proposal('method',proposed)
        for step in ('discovery','exploration'):
            self.assertEqual(memory.validate_proposal(step,fields()[step]),fields()[step])
        self.assert_code('operation_unavailable',lambda:memory.validate_proposal('position',{}))


class ProvenanceTests(unittest.TestCase):
    assert_code=ResultTests.assert_code

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=storage.Store(Path(self.temp.name)/'ideas',observer='Operator')
        self.sid=self.store.create_session();self.live=dict(LIVE,session_id=self.sid)
        self.context=TrustedContext('Operator',self.sid,KEY)
        with self.store.transaction(write=True) as state:
            state['ideas'][KEY]=complete();state['order']=[KEY];state['backlog_revision']=1
            self.store.commit(state)

    def publish(self,value=FOUND,operation='memory',request='memory-1'):
        with self.store.transaction(write=True) as state:
            selected=source.prepare_source(state,KEY,operation)
            proposed=copy.deepcopy(value)
            if operation=='method':
                proposed={'memory':copy.deepcopy(value)}
            evidence=dict(binding_id=self.live['binding_id'],generation=self.live['generation'],
                source=selected,proposal=proposed,correlation=dict(request_id=request,session_id=self.sid,
                    idea_id=KEY,operation=operation,accepted_revision=selected['accepted_revision'],
                    draft_version=selected['draft_version'],source_digest=source_digest(operation,
                        selected['accepted_revision'],selected['data'])))
            return self.store.persist_agent_proposal(state,evidence,actor=self.context.actor,validate_current=source.validate_current)

    def validate(self,state,value=FOUND,*,live=None,context=None,accepted=None,proposals=None):
        proposed=fields()['method'];proposed.update(selection='adaptive-slices',reason='Human chose another route')
        proposed['memory']=copy.deepcopy(value)
        return memory.validate_method_memory(state,state['ideas'][KEY],proposed,context or self.context,
            live_binding=self.live if live is None else live,
            proposals=self.store.agent_proposals(state,KEY) if proposals is None else proposals,
            accepted_proposal=accepted)

    def test_found_and_completed_empty_search_require_matching_real_immutable_record(self):
        with self.store.transaction() as state:
            self.assert_code('memory_provenance_missing',lambda:self.validate(state))
            self.assert_code('memory_provenance_missing',lambda:self.validate(state,EMPTY))
        self.publish();self.publish(EMPTY,request='empty-search')
        with self.store.transaction() as state:
            before=copy.deepcopy(state)
            self.assertEqual(self.validate(state),FOUND)
            self.assertEqual(self.validate(state,EMPTY),EMPTY)
            self.assertEqual(state,before)

    def test_manual_no_claim_remains_available_disconnected_or_before_binding(self):
        with self.store.transaction() as state:
            for value in (UNAVAILABLE,ERROR):
                self.assertEqual(self.validate(state,value,live=dict(self.live,agent_status='disconnected')),value)
                proposed=fields()['method'];proposed['memory']=copy.deepcopy(value)
                self.assertEqual(memory.validate_method_memory(state,state['ideas'][KEY],proposed,self.context,
                    live_binding=None,proposals=[]),value)
            self.assert_code('agent_unavailable',lambda:self.validate(state,EMPTY,live=dict(self.live,agent_status='disconnected')))

    def test_same_generation_session_actor_and_current_accepted_inputs_required(self):
        self.publish()
        with self.store.transaction() as state:
            for live in (dict(self.live,generation='agent_'+'9'*32),dict(self.live,binding_id='binding_'+'9'*32)):
                self.assert_code('memory_provenance_missing',lambda:self.validate(state,live=live))
            self.assert_code('wrong_session',lambda:self.validate(state,live=dict(self.live,session_id='session_'+'9'*32)))
            self.assert_code('memory_provenance_missing',lambda:self.validate(state,context=TrustedContext('different',self.sid)))
            changed=dict(FOUND,rationale='Invented new rationale')
            self.assert_code('memory_provenance_missing',lambda:self.validate(state,changed))

    def test_changed_capture_or_priorities_draft_is_not_current_memory_grounding(self):
        self.publish()
        for step,key,changed in (('capture','raw_text','Changed consumed input'),('priorities','urgency',1)):
            with self.store.transaction() as state:
                idea=state['ideas'][KEY];value=fields()[step]
                value[key]=changed
                state['ideas'][KEY]=save_draft(idea,step,value,expected_revision=idea['revision'],expected_draft_version=0)['idea']
                self.assert_code('not_ready',lambda:self.validate(state))

    def test_new_accepted_revision_refuses_but_keeps_original_evidence(self):
        link=self.publish()
        with self.store.transaction(write=True) as state:
            # A step downstream of the method moves the accepted revision without touching memory's inputs.
            state['ideas'][KEY]=accept(state['ideas'][KEY],'exploration',dict(fields()['exploration'],outcome='Revised outcome'))['idea']
            self.store.commit(state)
        with self.store.transaction() as state:
            self.assert_code('memory_provenance_missing',lambda:self.validate(state))
            self.assertEqual(self.store.agent_proposals(state,KEY)[0]['proposal_id'],link['proposal_id'])

    def test_linked_method_result_grounds_memory_while_human_method_choice_differs(self):
        link=self.publish(operation='method',request='method-found')
        with self.store.transaction() as state:
            record=next(record for record in self.store.agent_proposals(state,KEY) if record['proposal_id']==link['proposal_id'])
            self.assertEqual(self.validate(state,accepted=record),FOUND)
            self.assert_code('memory_provenance_missing',lambda:self.validate(state,EMPTY,accepted=record))
            self.assert_code('memory_provenance_missing',lambda:self.validate(state,accepted=record,proposals=[]))

    def test_returned_memory_can_replace_unavailable_target_context_without_changing_inputs(self):
        self.publish(operation='method',request='method-found')
        with self.store.transaction(write=True) as state:
            idea=state['ideas'][KEY];proposed=fields()['method'];proposed['memory']=copy.deepcopy(FOUND)
            proposed['selection']='adaptive-slices'
            state['ideas'][KEY]=save_draft(idea,'method',proposed,
                expected_revision=idea['revision'],expected_draft_version=0)['idea']
            self.store.commit(state)
        with self.store.transaction() as state:
            record=self.store.agent_proposals(state,KEY)[0]
            self.assertEqual(record['data']['method']['memory']['status'],'unavailable')
            self.assertEqual(self.validate(state,accepted=record),FOUND)

    def test_memory_record_cannot_be_misrepresented_as_linked_method(self):
        self.publish()
        with self.store.transaction() as state:
            record=self.store.agent_proposals(state,KEY)[0]
            self.assert_code('memory_provenance_missing',lambda:self.validate(state,accepted=record))

    def test_no_retrieval_io_or_nested_transaction_and_corruption_not_hidden(self):
        self.publish()
        with self.store.transaction() as state:
            records=self.store.agent_proposals(state,KEY)
            with patch('builtins.open',side_effect=AssertionError('unexpected retrieval')), \
                 patch.object(self.store,'transaction',side_effect=AssertionError('nested transaction')):
                self.assertEqual(self.validate(state,proposals=records),FOUND)
            records[0]['proposal']['raw_memory']='refused'
            with self.assertRaises(IdeaError):self.validate(state,proposals=records)


if __name__=='__main__':unittest.main()
