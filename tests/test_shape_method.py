"""packaged Shape/Method and real owned HTTP checks.

No native browser qualification is claimed by these tests.
"""
import copy
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
from idea_domain import IdeaError
from idea_service import TrustedContext
from idea_steps import load_registry
from idea_steps.shape import HANDLER
from idea_steps.method import HANDLER as METHOD_HANDLER
from idea_workflow import acceptance_source, derive_state
import test_agent_launch as launch_fixture
from test_workflow import captured, complete, accept, fields


class ShapeHandlerTests(unittest.TestCase):
    def setUp(self):
        self.idea=captured();self.state={'ideas':{self.idea['idea_id']:self.idea}}
        self.payload=dict(step='shape',idea_id=self.idea['idea_id'],fields=fields()['shape'])
        self.context=TrustedContext('Operator','session_'+'1'*32)
        self.source=acceptance_source(self.idea,'shape',self.payload['fields'])

    def test_literal_registry_exports_pure_handler_and_keeps_risk_separate(self):
        handlers,_=load_registry();self.assertIs(handlers['shape'],HANDLER)
        self.assertIsNone(HANDLER.apply);self.assertEqual(HANDLER.extra_dependencies,())
        self.payload['fields'].update(assumptions=['Risk remains despite a small scope'],learning=['Observed result'])
        self.source=acceptance_source(self.idea,'shape',self.payload['fields'])
        before=copy.deepcopy((self.state,self.payload,self.source))
        HANDLER.validate(self.state,self.idea,self.payload,self.source,self.context)
        self.assertEqual((self.state,self.payload,self.source),before)
        self.assertEqual(self.payload['fields']['scope'],'small-change')

    def test_required_fields_and_current_final_source_refused_without_mutation(self):
        invalid=[dict(fields()['shape'],outcome='  '),dict(fields()['shape'],scope=None),
            dict(fields()['shape'],scope_reason=''),dict(fields()['shape'],next_slice=''),
            dict(fields()['shape'],alternatives=[]),dict(fields()['shape'],alternatives=[{'route':'Route','reason':''}]),
            dict(fields()['shape'],assumptions=[' ']),dict(fields()['shape'],learning=['']),
            dict(fields()['shape'],simpler_route='invented')]
        before=copy.deepcopy(self.state)
        for value in invalid:
            with self.subTest(value=value),self.assertRaises(IdeaError):
                HANDLER.validate(self.state,self.idea,dict(self.payload,fields=value),self.source,self.context)
        self.payload['fields']['next_slice']='Human changed final slice'
        with self.assertRaises(IdeaError) as caught:HANDLER.validate(self.state,self.idea,self.payload,self.source,self.context)
        self.assertEqual(caught.exception.code,'stale_source')
        self.assertEqual(self.state,before)
        with self.assertRaises(IdeaError):HANDLER.validate(self.state,copy.deepcopy(self.idea),self.payload,self.source,self.context)

    def test_shape_next_slice_reduction_invalidates_dependents_preserving_old_evidence(self):
        idea=complete();value=dict(fields()['shape'],next_slice='Explicit second slice')
        payload=dict(step='shape',idea_id=idea['idea_id'],fields=value)
        state={'ideas':{idea['idea_id']:idea}}
        HANDLER.validate(state,idea,payload,acceptance_source(idea,'shape',value),self.context)
        old=copy.deepcopy(idea)
        updated=accept(idea,'shape',value)['idea']
        projected=derive_state(updated,dict(handoff_id='prior-packet',source_revision=old['revision']))
        self.assertEqual(updated['revision'],old['revision']+1)
        self.assertEqual(updated['revisions'][:-1],old['revisions'])
        self.assertEqual(projected['steps']['shape']['status'],'saved')
        for step in ('method','visualize','assess','review'):
            self.assertEqual(projected['steps'][step]['status'],'review-needed')
        self.assertEqual(idea,old)


@unittest.skipUnless(os.name=='posix','Owned native ACLs remain unqualified')
class ShapeHttpTests(unittest.TestCase):
    # Reuse only HTTP setup/helpers, avoiding inherited/repeated unrelated tests.
    setUp=launch_fixture.AgentLaunchTests.setUp
    start_owner=launch_fixture.AgentLaunchTests.start_owner
    cleanup=launch_fixture.AgentLaunchTests.cleanup
    wire=launch_fixture.AgentLaunchTests.wire
    pair=launch_fixture.AgentLaunchTests.pair
    browser=launch_fixture.AgentLaunchTests.browser
    draft=launch_fixture.AgentLaunchTests.draft
    accept_fields=launch_fixture.AgentLaunchTests.accept_fields
    operation_reply=launch_fixture.AgentLaunchTests.operation_reply

    def test_packaged_shape_suggestion_edited_autosaved_and_explicitly_accepted(self):
        self.assertIs(self.owner.handlers['shape'],HANDLER)
        self.accept_fields('priorities',fields()['priorities'],'priorities')
        reply=self.operation_reply('shape',fields()['shape'],'shape-proposed')
        before=self.browser('state');self.assertIsNone(before['accepted']['shape'])
        chosen=dict(fields()['shape'],outcome='Edited human result',next_slice='Edited human next slice',
                    alternatives=[{'route':'Preserve existing route','reason':'Human reason'}],assumptions=['Human risk'])
        self.draft('shape',chosen,'shape-edited')
        result=self.accept_fields('shape',chosen,'shape-accept',reply['evidence']['proposal_id'])
        after=self.browser('state')
        self.assertEqual(after['accepted']['shape'],chosen);self.assertEqual(after['steps']['shape']['status'],'saved')
        self.assertEqual(after['current_step'],'method');self.assertEqual(result['proposal_id'],reply['evidence']['proposal_id'])
        self.assertEqual(self.browser('requests/shape-accept'),result)

    def test_partial_draft_and_disconnected_manual_shape_acceptance(self):
        self.accept_fields('priorities',fields()['priorities'],'priorities')
        self.draft('shape',{'outcome':'Unfinished manual thought'},'partial-shape')
        error=self.accept_fields('shape',{'outcome':'Unfinished manual thought'},'incomplete',ok=False)
        self.assertEqual(error['code'],'invalid_input');self.assertIsNone(self.browser('state')['accepted']['shape'])
        self.agent.session_close()
        result=self.accept_fields('shape',fields()['shape'],'manual-shape')
        self.assertNotIn('proposal_id',result)
        after=self.browser('state');self.assertEqual(after['agent_status'],'disconnected')
        self.assertEqual(after['accepted']['shape']['next_slice'],fields()['shape']['next_slice'])
        self.assertEqual(after['steps']['shape']['status'],'saved')

    def test_changed_capture_refuses_linked_old_shape_and_allows_fresh_manual_answer(self):
        self.accept_fields('priorities',fields()['priorities'],'priorities')
        reply=self.operation_reply('shape',fields()['shape'],'shape-old-source')
        current=self.browser('state')['accepted']['capture']
        self.accept_fields('capture',dict(current,raw_text='Changed accepted Capture'),'capture-change')
        rejected=self.accept_fields('shape',fields()['shape'],'shape-stale',reply['evidence']['proposal_id'],
                                    ok=False,expected_status=409)
        self.assertEqual(rejected['code'],'stale_revision')
        self.assertIsNone(self.browser('state')['accepted']['shape'])
        self.assertEqual(self.wire('/api/v1/requests/shape-stale',headers={
            'X-Idea-Binding':self.opened['binding_id'],'Cookie':self.cookie,'X-Idea-Tab':self.tab})[0],404)
        self.accept_fields('shape',dict(fields()['shape'],outcome='Fresh human answer'),'shape-fresh')
        self.assertEqual(self.browser('state')['accepted']['shape']['outcome'],'Fresh human answer')


class MethodHandlerTests(unittest.TestCase):
    def setUp(self):
        self.idea=complete();self.state={'ideas':{self.idea['idea_id']:self.idea}}
        self.context=TrustedContext('Operator','session_'+'1'*32)
        self.payload=dict(step='method',idea_id=self.idea['idea_id'],fields=fields()['method'])
        self.source=acceptance_source(self.idea,'method',self.payload['fields'])

    def test_packaged_method_is_pure_and_checks_final_source(self):
        handlers,_=load_registry();self.assertIs(handlers['method'],METHOD_HANDLER)
        self.assertIsNone(METHOD_HANDLER.apply);self.assertEqual(METHOD_HANDLER.extra_dependencies,())
        before=copy.deepcopy((self.state,self.payload,self.source))
        METHOD_HANDLER.validate(self.state,self.idea,self.payload,self.source,self.context)
        self.assertEqual((self.state,self.payload,self.source),before)
        changed=dict(self.payload,fields=dict(self.payload['fields'],reason='Changed human reason'))
        with self.assertRaises(IdeaError) as caught:METHOD_HANDLER.validate(self.state,self.idea,changed,self.source,self.context)
        self.assertEqual(caught.exception.code,'stale_source')

    def test_appetite_experiment_and_incompatible_fields_require_explicit_meaningful_answers(self):
        base=fields()['method']
        invalid=[dict(base,selection=None),dict(base,reason=' '),dict(base,selection='appetite-led'),
            dict(base,investment={'cap':3,'unit':'hours','boundary':'Human boundary'}),
            dict(base,selection='experiment-led',experiment={'question':'Which?','evidence':'Observe','success_criterion':'Works','stop_rule':''})]
        for cap in (0,-1,True,None):invalid.append(dict(base,selection='appetite-led',investment={'cap':cap,'unit':'hours','boundary':'Human boundary'}))
        invalid.append(dict(base,selection='appetite-led',investment={'cap':3,'unit':'','boundary':'Human boundary'}))
        before=copy.deepcopy(self.state)
        for value in invalid:
            with self.subTest(value=value),self.assertRaises(IdeaError):
                METHOD_HANDLER.validate(self.state,self.idea,dict(self.payload,fields=value),self.source,self.context)
        self.assertEqual(self.state,before)


@unittest.skipUnless(os.name=='posix','Owned native ACLs remain unqualified')
class MethodHttpTests(unittest.TestCase):
    setUp=ShapeHttpTests.setUp
    start_owner=ShapeHttpTests.start_owner
    cleanup=ShapeHttpTests.cleanup
    wire=ShapeHttpTests.wire
    pair=ShapeHttpTests.pair
    browser=ShapeHttpTests.browser
    draft=ShapeHttpTests.draft
    accept_fields=ShapeHttpTests.accept_fields
    operation_reply=ShapeHttpTests.operation_reply

    def prepare_method(self):
        self.assertIs(self.owner.handlers['method'],METHOD_HANDLER)
        self.accept_fields('priorities',fields()['priorities'],'method-priorities')
        self.accept_fields('shape',fields()['shape'],'method-shape')

    def test_actual_grounded_recommendation_records_different_human_method_and_receipt(self):
        self.prepare_method()
        memory=dict(status='found',sources=['decision:fixture-preference'],rationale='Agent reports a saved fixture preference')
        recommendation=dict(fields()['method'],memory=memory)
        reply=self.operation_reply('method',recommendation,'method-proposed')
        before=self.browser('state');self.assertIsNone(before['accepted']['method'])
        chosen=dict(recommendation,selection='adaptive-slices',reason='Human chooses useful increments')
        self.draft('method',chosen,'method-human-draft')
        result=self.accept_fields('method',chosen,'method-human-accept',reply['evidence']['proposal_id'])
        after=self.browser('state');self.assertEqual(after['accepted']['method'],chosen)
        self.assertEqual(after['steps']['method']['status'],'saved');self.assertEqual(after['current_step'],'visualize')
        summary=next(item for item in after['proposals'] if item['proposal_id']==reply['evidence']['proposal_id'])
        self.assertEqual(summary['proposal']['selection'],'bounded-plan')
        self.assertEqual(self.browser('requests/method-human-accept'),result)
        self.assertEqual(result['proposal_id'],reply['evidence']['proposal_id'])

    def test_unsupported_search_claims_refused_and_disconnected_unavailable_choice_saved(self):
        self.prepare_method()
        for status,refs in (('found',['note:unverified']),('searched_no_preference',[])):
            claim=dict(fields()['method'],memory=dict(status=status,sources=refs,rationale='Unsupported claim'))
            error=self.accept_fields('method',claim,'unsupported-'+status,ok=False)
            self.assertEqual(error['code'],'memory_provenance_missing')
        self.assertIsNone(self.browser('state')['accepted']['method'])
        self.agent.session_close()
        result=self.accept_fields('method',fields()['method'],'disconnected-manual-method')
        self.assertNotIn('proposal_id',result)
        self.assertEqual(self.browser('state')['accepted']['method']['memory']['status'],'unavailable')

    def test_human_conditional_boundaries_are_required_and_persisted(self):
        self.prepare_method()
        incomplete=dict(fields()['method'],selection='appetite-led',investment={'cap':2,'unit':'hours','boundary':''})
        self.draft('method',incomplete,'incomplete-appetite-draft')
        before=self.browser('state')
        evidence_before={str(path.relative_to(self.store)):path.read_bytes() for path in self.store.rglob('*.md')}
        # Structurally valid fields with an unanswered requirement are not_ready.
        self.assertEqual(self.accept_fields('method',incomplete,'incomplete-appetite',ok=False)['code'],'not_ready')
        refused=self.browser('state')
        self.assertIsNone(refused['accepted']['method'])
        self.assertIsNone(refused['steps']['method']['evidence_id'])
        self.assertEqual((refused['revision'],refused['draft_version']),(before['revision'],before['draft_version']))
        self.assertEqual(self.wire('/api/v1/requests/incomplete-appetite',headers={
            'X-Idea-Binding':self.opened['binding_id'],'Cookie':self.cookie,'X-Idea-Tab':self.tab})[0],404)
        self.assertEqual({str(path.relative_to(self.store)):path.read_bytes() for path in self.store.rglob('*.md')},evidence_before)
        appetite=dict(incomplete,investment={'cap':2,'unit':'hours','boundary':'One human chosen boundary'})
        self.accept_fields('method',appetite,'complete-appetite')
        self.assertEqual(self.browser('state')['accepted']['method']['investment'],appetite['investment'])
        experiment=dict(fields()['method'],selection='experiment-led',experiment=dict(question='Which path works?',
            evidence='One observed run',success_criterion='The human criterion',stop_rule='Stop after that run'))
        self.accept_fields('method',experiment,'complete-experiment')
        self.assertEqual(self.browser('state')['accepted']['method'],experiment)


if __name__=='__main__':unittest.main()
