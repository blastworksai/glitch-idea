"""Current source and edited acceptance against real Markdown. Operator."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0, str(SCRIPTS))
import idea_agent_source as source
import idea_store as storage
from idea_domain import IdeaError
from idea_service import TrustedContext
from idea_workflow import (acceptance_source, derive_state, save_draft, source_digest)
from test_workflow import accept, captured, complete, fields, original_idea

KEY = captured()['idea_id']
ACTOR = 'Operator'
LIVE = dict(binding_id='binding_'+'2'*32, generation='agent_'+'3'*32,
            session_id='session_'+'4'*32, agent_status='connected')


def domain(idea=None):
    state = storage.empty_state()
    state.update(ideas={KEY:complete() if idea is None else idea}, order=[KEY], backlog_revision=1)
    return state


def evidence(state, sid=LIVE['session_id'], operation='shape', request_id='request-1'):
    selected = source.prepare_source(state, KEY, operation)
    proposal = fields()[operation] if operation != 'memory' else fields()['method']['memory']
    correlation = dict(request_id=request_id, session_id=sid, idea_id=KEY,
        operation=operation, accepted_revision=selected['accepted_revision'],
        draft_version=selected['draft_version'], source_digest=source_digest(operation,
            selected['accepted_revision'], selected['data']))
    return dict(binding_id=LIVE['binding_id'], generation=LIVE['generation'],
                correlation=correlation, source=selected, proposal=proposal)


class PureSourceTests(unittest.TestCase):
    def assert_code(self, code, fn):
        with self.assertRaises(IdeaError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)

    def test_fixed_step_maps_canonical_digest_and_detachment(self):
        state = domain(); before = copy.deepcopy(state)
        for operation, expected in (('shape', {'capture','shape'}),
            ('method', {'capture','shape','method'}), ('memory', {'capture','shape'})):
            selected = source.prepare_source(state, KEY, operation)
            self.assertEqual(set(selected['data']), expected)
            projected = source.project_sources(state, KEY)[operation]
            expected_hash = hashlib.sha256(json.dumps(dict(operation=operation,
                source_revision=selected['accepted_revision'], fields=selected['data']),
                sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
            self.assertEqual(projected['source']['source_digest'], expected_hash)
            selected['data']['capture']['raw_text'] = 'Detached'
        self.assertEqual(state, before)

    def test_target_buffer_is_whole_persisted_partial_no_merge_and_absence_distinct(self):
        idea = accept(captured(), 'priorities')['idea']; state = domain(idea)
        self.assertNotIn('shape', source.prepare_source(state,KEY,'shape')['data'])
        changed = save_draft(idea, 'shape', {'outcome':'Partial'}, expected_revision=2,expected_draft_version=0)['idea']
        self.assertEqual(source.prepare_source(domain(changed),KEY,'shape')['data']['shape'], {'outcome':'Partial'})
        changed = save_draft(changed, 'shape', {}, expected_revision=2,expected_draft_version=1)['idea']
        self.assertEqual(source.prepare_source(domain(changed),KEY,'shape')['data']['shape'], {})

    def test_capture_source_is_revised_acceptance_not_immutable_origin(self):
        value = fields()['capture']; value['raw_text'] = 'Revised words'
        idea = accept(complete(), 'capture', value)['idea']
        selected = source.prepare_source(domain(idea),KEY,'shape')
        self.assertEqual(selected['data']['capture']['raw_text'], 'Revised words')
        self.assertNotEqual(selected['data']['capture']['raw_text'], idea['origin']['text'])

    def test_legacy_and_incomplete_inputs_unavailable_without_fabricated_receipts(self):
        legacy = domain(original_idea())
        result = source.project_sources(legacy,KEY)
        self.assertTrue(all(entry == dict(available=False,code='not_ready',source=None) for entry in result.values()))
        result = source.project_sources(domain(captured()),KEY)
        self.assertTrue(result['shape']['available'])
        self.assertFalse(result['memory']['available']); self.assertFalse(result['method']['available'])

    def test_changed_earlier_draft_refuses_current_and_response_but_target_draft_is_valid(self):
        state = domain(); original = evidence(state,operation='method')
        idea = state['ideas'][KEY]
        changed = fields()['shape']; changed['next_slice'] = 'New unsaved input'
        state['ideas'][KEY] = save_draft(idea,'shape',changed,expected_revision=idea['revision'],expected_draft_version=0)['idea']
        self.assert_code('not_ready', lambda:source.prepare_source(state,KEY,'method'))
        self.assert_code('stale_source', lambda:source.validate_current(state,original))
        target = domain()
        target['ideas'][KEY] = save_draft(target['ideas'][KEY],'method',{'reason':'Refine'},
            expected_revision=target['ideas'][KEY]['revision'],expected_draft_version=0)['idea']
        self.assertEqual(source.prepare_source(target,KEY,'method')['data']['method'], {'reason':'Refine'})

    def test_response_requires_exact_counters_digest_data_and_typed_envelope(self):
        state = domain(); original = evidence(state)
        self.assertEqual(source.validate_current(state,original), original['source'])
        for change in ('revision','draft','data','digest','bool','extra'):
            changed = copy.deepcopy(original)
            if change == 'revision':
                changed['source']['accepted_revision'] += 1; changed['correlation']['accepted_revision'] += 1
                changed['correlation']['source_digest'] = source_digest('shape',changed['source']['accepted_revision'],changed['source']['data'])
            elif change == 'draft':
                changed['source']['draft_version'] += 1; changed['correlation']['draft_version'] += 1
            elif change == 'data': changed['source']['data']['shape']['outcome'] = 'Changed'
            elif change == 'digest': changed['correlation']['source_digest'] = '0'*64
            elif change == 'bool': changed['source']['draft_version'] = False
            else: changed['source']['data']['backlog_revision'] = 1
            with self.subTest(change=change), self.assertRaises(IdeaError): source.validate_current(state,changed)

    def test_source_projection_does_not_hide_schema_or_unknown_idea_errors(self):
        self.assert_code('not_found',lambda:source.project_sources(domain(),'idea_'+'f'*32))
        broken=domain(); broken['ideas'][KEY]['workflow']['draft_version']=True
        self.assert_code('invalid_input',lambda:source.project_sources(broken,KEY))
        for operation in ('position','visual_brief','anything'):
            self.assert_code('operation_unavailable',lambda:source.prepare_source(domain(),KEY,operation))

    def test_memory_statuses_exact_typed_results_and_method_schema(self):
        values = [dict(status='found',sources=['safe-reference'],rationale='Observed preference'),
            dict(status='searched_no_preference',sources=[],rationale='Search complete'),
            dict(status='unavailable',sources=[],rationale=None),dict(status='error',sources=[],rationale='Retrieval failed')]
        for value in values:
            self.assertEqual(source.validate_proposal('memory',value),value)
        invalid = [dict(status='found',sources=[],rationale='Missing source'),
            dict(status='found',sources=['ref'],rationale=None),
            dict(status='unavailable',sources=['false-claim'],rationale=None),
            dict(status='error',sources=[],rationale=None,raw_memory='private'),
            dict(status='searched_no_preference',sources=[''],rationale=None)]
        for value in invalid:
            self.assert_code('invalid_proposal',lambda:source.validate_proposal('memory',value))
        method = fields()['method']; method['memory'] = invalid[0]
        self.assert_code('invalid_proposal',lambda:source.validate_proposal('method',method))

    def test_source_map_reserves_aggregate_budget_without_changing_saved_inputs(self):
        capture = fields()['capture']; capture['raw_text'] = 'x' * 400000
        idea = accept(captured(), 'capture', capture)['idea']
        for step in ('priorities', 'shape', 'method'):
            idea = accept(idea, step)['idea']
        state = domain(idea); before = copy.deepcopy(state)
        result = source.project_sources(state, KEY)
        self.assertTrue(result['shape']['available'])
        for operation in ('memory', 'method'):
            self.assertEqual(result[operation], dict(available=False,
                code='source_projection_capacity', source=None))
        self.assertLessEqual(len(source._bounded(result)), source.MAX_SOURCE_PROJECTION_BYTES)
        self.assertEqual(state, before)
        # A valid workflow can itself exceed the request-source envelope.
        capture['raw_text'] = 'x' * source.MAX_INPUT
        large = accept(idea, 'capture', capture)['idea']
        for step in ('shape', 'method'):
            large = accept(large, step)['idea']
        result = source.project_sources(domain(large), KEY)
        self.assertTrue(all(entry == dict(available=False, code='source_too_large', source=None)
                            for entry in result.values()))

    def test_source_and_proposal_bounds_reject_oversized_or_deep_values(self):
        too_large=fields()['shape']; too_large['learning']=['x'*65000]*20
        self.assert_code('too_large',lambda:source.validate_proposal('shape',too_large))
        nested={}; pointer=nested
        for _ in range(30): pointer['child']={}; pointer=pointer['child']
        self.assert_code('too_large',lambda:source.validate_proposal('memory',nested))


class AdapterTests(unittest.TestCase):
    assert_code = PureSourceTests.assert_code

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'ideas'; self.store=storage.Store(self.root,observer=ACTOR)
        self.sid=self.store.create_session(); self.live=dict(LIVE,session_id=self.sid)
        self.context=TrustedContext(ACTOR,self.sid,KEY); self.adapter=source.SourceAdapter(self.store)
        with self.store.transaction(write=True) as state:
            state.update(domain()); self.store.commit(state)
        with self.store.transaction(write=True) as state:
            self.original=evidence(state,self.sid)
            self.link=self.store.persist_agent_proposal(state,self.original,actor=ACTOR,validate_current=source.validate_current)

    def payload(self,idea,step='shape',value=None,proposal_id=None):
        return dict(request_id='accept-1',idea_id=KEY,step=step,fields=fields()[step] if value is None else value,
            proposal_id=self.link['proposal_id'] if proposal_id is None else proposal_id,
            expected_revision=idea['revision'], expected_draft_version=idea['workflow']['draft_version'],
            expected_backlog_revision=None)

    def validate(self,state,payload,live=None,context=None):
        idea=state['ideas'][KEY]
        return self.adapter.validate_acceptance(state,idea,payload,
            acceptance_source(idea,payload['step'],payload['fields']),context or self.context,
            live_binding=self.live if live is None else live)

    def test_inventory_requires_existing_actual_transaction_and_detaches_without_io(self):
        self.assert_code('invalid_transaction',lambda:self.store.agent_proposals(domain(),KEY))
        with self.store.transaction() as state:
            self.assert_code('invalid_transaction',lambda:self.store.agent_proposals(copy.deepcopy(state),KEY))
            with patch.object(storage,'read_bytes',side_effect=AssertionError('unexpected IO')), \
                 patch.object(self.store,'transaction',side_effect=AssertionError('nested transaction')):
                records=self.store.agent_proposals(state,KEY)
                linked=self.store.agent_proposals(state,KEY,with_links=True)
                summaries=self.adapter.project_proposals(state,KEY,self.live)
                checked=self.validate(state,self.payload(state['ideas'][KEY]))
            self.assertEqual(linked[0]['record'],records[0])
            self.assertEqual(linked[0]['evidence'],{key:self.link[key] for key in ('path','sha256')})
            linked[0]['evidence']['path']='changed'
            self.assertEqual(self.store.agent_proposals(state,KEY,with_links=True)[0]['evidence']['path'],self.link['path'])
            self.assertEqual(checked['proposal_id'],self.link['proposal_id'])
            self.assertEqual(summaries[0]['request_id'],self.original['correlation']['request_id'])
            self.assertFalse(summaries[0]['stale']); self.assertTrue(summaries[0]['acceptance_eligible'])
            records[0]['proposal']['outcome']='Detached'
            self.assertNotEqual(self.store.agent_proposals(state,KEY)[0]['proposal']['outcome'],'Detached')

    def test_accessor_is_allowed_inside_actual_acceptance_mutator(self):
        def callback(state):
            checked=self.validate(state,self.payload(state['ideas'][KEY]))
            return dict(idea_id=KEY,proposal_id=checked['proposal_id'])
        result=self.store.mutate(self.sid,'validate-only',{},callback)
        self.assertEqual(result['proposal_id'],self.link['proposal_id'])

    def test_target_autosave_is_response_stale_but_edited_acceptance_eligible(self):
        with self.store.transaction(write=True) as state:
            idea=state['ideas'][KEY]; changed=fields()['shape']; changed.update(outcome='Human edit',next_slice='Edited slice')
            state['ideas'][KEY]=save_draft(idea,'shape',changed,expected_revision=idea['revision'],expected_draft_version=0)['idea']
            self.store.commit(state)
        with self.store.transaction() as state:
            summary=self.adapter.project_proposals(state,KEY,self.live)[0]
            self.assertTrue(summary['stale']); self.assertTrue(summary['acceptance_eligible'])
            self.assertEqual(summary['stale_reason'],'stale_source')
            payload=self.payload(state['ideas'][KEY],value=changed)
            checked=self.validate(state,payload)
            self.assertEqual(checked['proposal_id'],self.link['proposal_id'])
            self.assertNotEqual(acceptance_source(state['ideas'][KEY],'shape',changed)['source_digest'],checked['source_digest'])
            payload['expected_draft_version']=0
            self.assert_code('stale_draft_version',lambda:self.validate(state,payload))

    def test_consumed_capture_draft_refuses_even_without_accepted_revision_change(self):
        with self.store.transaction(write=True) as state:
            idea=state['ideas'][KEY]; changed=fields()['capture']; changed['raw_text']='Changed input'
            state['ideas'][KEY]=save_draft(idea,'capture',changed,expected_revision=idea['revision'],expected_draft_version=0)['idea']
            self.store.commit(state)
        with self.store.transaction() as state:
            summary=self.adapter.project_proposals(state,KEY,self.live)[0]
            self.assertEqual(summary['acceptance_reason'],'stale_source')
            self.assert_code('not_ready',lambda:self.validate(state,self.payload(state['ideas'][KEY])))

    def test_unrelated_draft_does_not_block_proposal_acceptance(self):
        with self.store.transaction(write=True) as state:
            idea=state['ideas'][KEY]
            state['ideas'][KEY]=save_draft(idea,'priorities',{'urgency':9},expected_revision=idea['revision'],expected_draft_version=0)['idea']
            self.store.commit(state)
        with self.store.transaction() as state:
            self.assertEqual(self.validate(state,self.payload(state['ideas'][KEY]))['proposal_id'],self.link['proposal_id'])

    def test_new_generation_session_actor_status_and_unknown_id_refused(self):
        with self.store.transaction() as state:
            payload=self.payload(state['ideas'][KEY])
            for live,code in ((dict(self.live,generation='agent_'+'5'*32),'wrong_generation'),
                (dict(self.live,binding_id='binding_'+'6'*32),'wrong_generation'),
                (dict(self.live,session_id='session_'+'7'*32),'wrong_session'),
                (dict(self.live,agent_status='paused'),'agent_unavailable'),
                (dict(self.live,agent_status='disconnected'),'agent_unavailable')):
                self.assert_code(code,lambda:self.validate(state,payload,live=live))
                summary=self.adapter.project_proposals(state,KEY,live)[0]
                self.assertTrue(summary['stale']); self.assertFalse(summary['acceptance_eligible'])
            self.assert_code('proposal_mismatch',lambda:self.validate(state,payload,context=TrustedContext('other',self.sid)))
            unknown=dict(payload,proposal_id='proposal_'+'f'*32)
            self.assert_code('proposal_not_found',lambda:self.validate(state,unknown))

    def test_new_accepted_revision_preserves_evidence_but_marks_it_ineligible(self):
        with self.store.transaction(write=True) as state:
            state['ideas'][KEY]=accept(state['ideas'][KEY],'priorities',dict(urgency=9,importance=8))['idea']
            self.store.commit(state)
        with self.store.transaction() as state:
            summary=self.adapter.project_proposals(state,KEY,self.live)[0]
            self.assertEqual(summary['acceptance_reason'],'stale_revision')
            self.assert_code('stale_revision',lambda:self.validate(state,self.payload(state['ideas'][KEY])))
            self.assertEqual(self.store.agent_proposals(state,KEY)[0]['proposal_id'],self.link['proposal_id'])

    def test_method_alternative_allowed_but_memory_support_cannot_be_rewritten(self):
        with self.store.transaction(write=True) as state:
            reply=evidence(state,self.sid,operation='method',request_id='method-1')
            link=self.store.persist_agent_proposal(state,reply,actor=ACTOR,validate_current=source.validate_current)
        with self.store.transaction() as state:
            changed=fields()['method']; changed.update(selection='adaptive-slices',reason='Human chooses another route')
            payload=self.payload(state['ideas'][KEY],'method',changed,link['proposal_id'])
            self.assertEqual(self.validate(state,payload)['operation'],'method')
            payload['fields']['memory']=dict(status='found',sources=['forged-reference'],rationale='Forged preference')
            self.assert_code('stale_source',lambda:self.validate(state,payload))
            payload['proposal_id']=None; payload['fields']['memory']=fields()['method']['memory']
            self.assertIsNone(self.validate(state,payload,live=dict(self.live,agent_status='disconnected')))

    def test_memory_is_inspectable_supporting_evidence_not_method_recommendation(self):
        with self.store.transaction(write=True) as state:
            reply=evidence(state,self.sid,operation='memory',request_id='memory-1')
            link=self.store.persist_agent_proposal(state,reply,actor=ACTOR,validate_current=source.validate_current)
        with self.store.transaction() as state:
            summaries=self.adapter.project_proposals(state,KEY,self.live)
            summary=next(item for item in summaries if item['proposal_id']==link['proposal_id'])
            self.assertEqual(summary['acceptance_reason'],'supporting_evidence')
            self.assertFalse(summary['acceptance_eligible'])
            payload=self.payload(state['ideas'][KEY],'method',proposal_id=link['proposal_id'])
            self.assert_code('proposal_mismatch',lambda:self.validate(state,payload))

    def test_method_returned_memory_autosave_allowed_but_independent_memory_drift_refused(self):
        returned=dict(status='found',sources=['memory:preference-42'],rationale='Recorded preference')
        with self.store.transaction(write=True) as state:
            reply=evidence(state,self.sid,operation='method',request_id='method-found')
            self.assertEqual(reply['source']['data']['method']['memory']['status'],'unavailable')
            reply['proposal']['memory']=copy.deepcopy(returned)
            link=self.store.persist_agent_proposal(state,reply,actor=ACTOR,validate_current=source.validate_current)
        with self.store.transaction(write=True) as state:
            idea=state['ideas'][KEY];changed=copy.deepcopy(reply['proposal'])
            changed.update(selection='adaptive-slices',reason='Human chooses another route')
            state['ideas'][KEY]=save_draft(idea,'method',changed,
                expected_revision=idea['revision'],expected_draft_version=0)['idea']
            self.store.commit(state)
        with self.store.transaction() as state:
            summary=next(item for item in self.adapter.project_proposals(state,KEY,self.live)
                         if item['proposal_id']==link['proposal_id'])
            self.assertTrue(summary['stale']);self.assertTrue(summary['acceptance_eligible'])
            payload=self.payload(state['ideas'][KEY],'method',changed,link['proposal_id'])
            self.assertEqual(self.validate(state,payload)['proposal']['memory'],returned)
            invalid_payload=copy.deepcopy(payload)
            invalid_payload['fields']['memory']=fields()['method']['memory']
            self.assert_code('stale_source',lambda:self.validate(state,invalid_payload))
        with self.store.transaction(write=True) as state:
            idea=state['ideas'][KEY];drift=copy.deepcopy(changed)
            drift['memory']=dict(returned,rationale='Independent different claim')
            state['ideas'][KEY]=save_draft(idea,'method',drift,
                expected_revision=idea['revision'],expected_draft_version=1)['idea']
            self.store.commit(state)
        with self.store.transaction() as state:
            summary=next(item for item in self.adapter.project_proposals(state,KEY,self.live)
                         if item['proposal_id']==link['proposal_id'])
            self.assertFalse(summary['acceptance_eligible'])
            self.assertEqual(summary['acceptance_reason'],'stale_source')
            payload=self.payload(state['ideas'][KEY],'method',changed,link['proposal_id'])
            self.assert_code('stale_source',lambda:self.validate(state,payload))

    def test_fifteen_durable_large_proposals_remain_inspectable_across_generations(self):
        links = [self.link]
        for index in range(14):
            with self.store.transaction(write=True) as state:
                reply = evidence(state, self.sid, request_id='large-'+str(index))
                reply['generation'] = 'agent_'+format(10+index//3, '032x')
                reply['proposal'].update(outcome='x'*50000, next_slice='y'*30000)
                links.append(self.store.persist_agent_proposal(state, reply, actor=ACTOR,
                             validate_current=source.validate_current))
        live = dict(self.live, generation=reply['generation'])
        with self.store.transaction() as state:
            before = copy.deepcopy(state)
            before_files = copy.deepcopy(self.store._contexts.active['files'])
            with patch.object(storage, 'read_bytes', side_effect=AssertionError('unexpected IO')), \
                 patch.object(self.store, 'transaction', side_effect=AssertionError('nested transaction')):
                projected = self.adapter.project_projection(state, KEY, live)
            inventory = projected['proposal_inventory']; summaries = projected['proposals']
            self.assertEqual(inventory['total'],15); self.assertEqual(inventory['projected'],15)
            self.assertEqual(inventory['omitted'],0); self.assertGreater(inventory['content_omitted'],0)
            self.assertEqual(inventory['content_omitted'],sum(item['content_omitted'] for item in summaries))
            self.assertLessEqual(len(source._bounded(projected)),source.MAX_PROJECTION_BYTES)
            self.assertEqual(summaries[0]['proposal_id'],links[-1]['proposal_id'])
            self.assertTrue(summaries[0]['acceptance_eligible'])
            witnesses={link['proposal_id']:{key:link[key] for key in ('path','sha256')} for link in links}
            for item in summaries:
                self.assertEqual(item['evidence'],witnesses[item['proposal_id']])
                if item['content_omitted']:
                    self.assertIsNone(item['proposal']); self.assertFalse(item['acceptance_eligible'])
                    self.assertEqual(item['acceptance_reason'],'projection_omitted')
            self.assertTrue(any(item['stale_reason']=='wrong_generation' for item in summaries))
            self.assertEqual(state,before); self.assertEqual(self.store._contexts.active['files'],before_files)
            self.assertEqual(len(self.store.agent_proposals(state,KEY)),15)
            payload=self.payload(state['ideas'][KEY],value=reply['proposal'],proposal_id=links[-1]['proposal_id'])
            self.assertEqual(self.validate(state,payload,live=live)['proposal_id'],links[-1]['proposal_id'])

    def test_body_omission_is_explicit_and_does_not_change_original_record(self):
        with self.store.transaction() as state:
            normal=self.adapter.project_projection(state,KEY,self.live)
            metadata=copy.deepcopy(normal)
            metadata['proposals'][0].update(proposal=None,content_omitted=True,
                acceptance_eligible=False,acceptance_reason='projection_omitted')
            metadata['proposal_inventory']['content_omitted']=1
            with patch.object(source,'MAX_PROJECTION_BYTES',len(source._bounded(metadata))):
                result=self.adapter.project_projection(state,KEY,self.live)
            self.assertEqual(result,metadata)
            self.assertEqual(self.store.agent_proposals(state,KEY)[0]['proposal'],self.original['proposal'])

    def test_projection_limits_and_corrupt_evidence_are_not_hidden(self):
        with self.store.transaction() as state:
            with patch.object(source,'MAX_PROJECTIONS',0):
                projected=self.adapter.project_projection(state,KEY,self.live)
                self.assertEqual(projected['proposals'],[])
                self.assertEqual(projected['proposal_inventory'],dict(total=1,projected=0,omitted=1,
                    content_omitted=0,index_path=KEY+'.md'))
        path=self.root/self.link['path']; path.write_bytes(path.read_bytes()+b'corrupt')
        with self.assertRaises(IdeaError):
            with self.store.transaction() as state:self.adapter.project_proposals(state,KEY,self.live)


if __name__ == '__main__': unittest.main()
