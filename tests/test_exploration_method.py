"""packaged Exploration/Method and real owned HTTP checks.

No native browser qualification is claimed by these tests. Replaces the v2
shape/method file: every shape refusal case now guards Exploration; every
method refusal case still guards Method, or moved to Exploration with the
investment and experiment fields (see the comment in each test).
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
from idea_steps.exploration import HANDLER
from idea_steps.method import HANDLER as METHOD_HANDLER
from idea_workflow import acceptance_source, derive_state
import test_agent_launch as launch_fixture
from test_workflow import captured, complete, accept, fields

APPETITE={'cap':3,'unit':'hours','boundary':'Human boundary'}
EXPERIMENT={'question':'Which?','evidence':'Observe','success_criterion':'Works','stop_rule':'Stop after one run'}


def ready(selection=None):
    """An idea with priorities, method and discovery accepted, ready for Exploration."""
    idea=captured()
    idea=accept(idea,'priorities')['idea']
    method=fields()['method']
    if selection:method['selection']=selection
    idea=accept(idea,'method',method)['idea']
    return accept(idea,'discovery')['idea']


class ExplorationHandlerTests(unittest.TestCase):
    def setUp(self):
        self.idea=ready();self.state={'ideas':{self.idea['idea_id']:self.idea}}
        self.payload=dict(step='exploration',idea_id=self.idea['idea_id'],fields=fields()['exploration'])
        self.context=TrustedContext('Operator','session_'+'1'*32)
        self.source=acceptance_source(self.idea,'exploration',self.payload['fields'])

    def test_literal_registry_exports_pure_handler_and_keeps_risk_separate(self):
        handlers,_=load_registry();self.assertIs(handlers['exploration'],HANDLER)
        self.assertNotIn('shape',handlers)
        self.assertIsNone(HANDLER.apply);self.assertEqual(HANDLER.extra_dependencies,())
        self.payload['fields'].update(assumptions=['Risk remains despite a small scope'],learning=['Observed result'])
        self.source=acceptance_source(self.idea,'exploration',self.payload['fields'])
        before=copy.deepcopy((self.state,self.payload,self.source))
        HANDLER.validate(self.state,self.idea,self.payload,self.source,self.context)
        self.assertEqual((self.state,self.payload,self.source),before)
        self.assertEqual(self.payload['fields']['scope'],'small-change')

    def test_archived_idea_without_workflow_reaches_normal_validation(self):
        from test_workflow import original_idea
        idea=original_idea();idea['status']='archived'
        self.assertNotIn('workflow',idea)
        state={'ideas':{idea['idea_id']:idea}}
        payload=dict(step='exploration',idea_id=idea['idea_id'],fields=fields()['exploration'])
        try:
            HANDLER.validate(state,idea,payload,{'source_digest':'0'*64},self.context)
        except IdeaError:
            pass  # a typed refusal is the normal outcome; a KeyError is the defect

    def test_required_fields_and_current_final_source_refused_without_mutation(self):
        # Old shape cases, one for one: outcome, scope, scope_reason, next_slice, alternatives (empty and
        # reasonless), assumptions, learning and an unsupported invented field. Plus the new sketch rules.
        base=fields()['exploration']
        invalid=[dict(base,outcome='  '),dict(base,scope=None),
            dict(base,scope_reason=''),dict(base,next_slice=''),
            dict(base,alternatives=[]),dict(base,alternatives=[{'route':'Route','reason':''}]),
            dict(base,assumptions=[' ']),dict(base,learning=['']),
            dict(base,simpler_route='invented'),dict(base,sketch=[]),
            dict(base,sketch=[dict(base['sketch'][0],done_when=' ')])]
        before=copy.deepcopy(self.state)
        for value in invalid:
            with self.subTest(value=value),self.assertRaises(IdeaError):
                HANDLER.validate(self.state,self.idea,dict(self.payload,fields=value),self.source,self.context)
        self.payload['fields']['next_slice']='Human changed final slice'
        with self.assertRaises(IdeaError) as caught:HANDLER.validate(self.state,self.idea,self.payload,self.source,self.context)
        self.assertEqual(caught.exception.code,'stale_source')
        self.assertEqual(self.state,before)
        with self.assertRaises(IdeaError):HANDLER.validate(self.state,copy.deepcopy(self.idea),self.payload,self.source,self.context)

    def test_exploration_next_slice_reduction_invalidates_dependents_preserving_old_evidence(self):
        idea=complete();value=dict(fields()['exploration'],next_slice='Explicit second slice')
        payload=dict(step='exploration',idea_id=idea['idea_id'],fields=value)
        state={'ideas':{idea['idea_id']:idea}}
        HANDLER.validate(state,idea,payload,acceptance_source(idea,'exploration',value),self.context)
        old=copy.deepcopy(idea)
        updated=accept(idea,'exploration',value)['idea']
        projected=derive_state(updated,dict(handoff_id='prior-packet',source_revision=old['revision']))
        self.assertEqual(updated['revision'],old['revision']+1)
        self.assertEqual(updated['revisions'][:-1],old['revisions'])
        self.assertEqual(projected['steps']['exploration']['status'],'saved')
        # Method and Discovery come before Exploration in v3, so they stay saved.
        for step in ('method','discovery'):
            self.assertEqual(projected['steps'][step]['status'],'saved')
        for step in ('visualize','assess','review'):
            self.assertEqual(projected['steps'][step]['status'],'review-needed')
        self.assertEqual(idea,old)

    def test_investment_and_experiment_follow_the_accepted_method_and_refuse_blanks(self):
        # Moved here from the v2 Method validation: appetite cap/unit/boundary and the experiment fields.
        base=fields()['exploration']
        for selection,key,good,bad in (
                ('appetite-led','investment',APPETITE,[dict(APPETITE,cap=cap) for cap in (0,-1,True,None)]+
                 [dict(APPETITE,unit=''),dict(APPETITE,boundary=' '),None]),
                ('experiment-led','experiment',EXPERIMENT,[dict(EXPERIMENT,stop_rule=''),None])):
            idea=ready(selection);state={'ideas':{idea['idea_id']:idea}}
            for value in bad:
                fields_=dict(base,**{key:value})
                with self.subTest(selection=selection,value=value),self.assertRaises(IdeaError):
                    HANDLER.validate(state,idea,dict(step='exploration',idea_id=idea['idea_id'],fields=fields_),
                                     acceptance_source(idea,'exploration',fields_),self.context)
            fields_=dict(base,**{key:good})
            HANDLER.validate(state,idea,dict(step='exploration',idea_id=idea['idea_id'],fields=fields_),
                             acceptance_source(idea,'exploration',fields_),self.context)


@unittest.skipUnless(os.name=='posix','Owned native ACLs remain unqualified')
class ExplorationHttpTests(unittest.TestCase):
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

    def prepare(self):
        for step in ('priorities','method','discovery'):
            self.accept_fields(step,fields()[step],'prepare-'+step)

    def test_packaged_exploration_suggestion_edited_autosaved_and_explicitly_accepted(self):
        # J2a-dependent: the agent `exploration` operation does not exist before CP2.
        self.assertIs(self.owner.handlers['exploration'],HANDLER)
        self.prepare()
        reply=self.operation_reply('exploration',fields()['exploration'],'exploration-proposed')
        before=self.browser('state');self.assertIsNone(before['accepted']['exploration'])
        chosen=dict(fields()['exploration'],outcome='Edited human result',next_slice='Edited human next slice',
                    alternatives=[{'route':'Preserve existing route','reason':'Human reason'}],assumptions=['Human risk'])
        self.draft('exploration',chosen,'exploration-edited')
        result=self.accept_fields('exploration',chosen,'exploration-accept',reply['evidence']['proposal_id'])
        after=self.browser('state')
        self.assertEqual(after['accepted']['exploration'],chosen);self.assertEqual(after['steps']['exploration']['status'],'saved')
        self.assertEqual(after['current_step'],'visualize');self.assertEqual(result['proposal_id'],reply['evidence']['proposal_id'])
        self.assertEqual(self.browser('requests/exploration-accept'),result)

    def test_partial_draft_and_disconnected_manual_exploration_acceptance(self):
        self.prepare()
        self.draft('exploration',{'outcome':'Unfinished manual thought'},'partial-exploration')
        error=self.accept_fields('exploration',{'outcome':'Unfinished manual thought'},'incomplete',ok=False)
        self.assertEqual(error['code'],'invalid_input');self.assertIsNone(self.browser('state')['accepted']['exploration'])
        self.agent.session_close()
        result=self.accept_fields('exploration',fields()['exploration'],'manual-exploration')
        self.assertNotIn('proposal_id',result)
        after=self.browser('state');self.assertEqual(after['agent_status'],'disconnected')
        self.assertEqual(after['accepted']['exploration']['next_slice'],fields()['exploration']['next_slice'])
        self.assertEqual(after['steps']['exploration']['status'],'saved')

    def test_changed_capture_refuses_linked_old_exploration_and_allows_fresh_manual_answer(self):
        self.prepare()
        reply=self.operation_reply('exploration',fields()['exploration'],'exploration-old-source')
        current=self.browser('state')['accepted']['capture']
        self.accept_fields('capture',dict(current,raw_text='Changed accepted Capture'),'capture-change')
        # In v3 a changed Capture sends Method and Discovery back for review, so the old linked
        # Exploration answer is refused outright until its own inputs are saved again.
        pending=self.accept_fields('exploration',fields()['exploration'],'exploration-pending',reply['evidence']['proposal_id'],ok=False)
        self.assertEqual(pending['code'],'not_ready')
        self.assertIsNone(self.browser('state')['accepted']['exploration'])
        for step in ('method','discovery'):
            self.accept_fields(step,fields()[step],'resave-'+step)
        rejected=self.accept_fields('exploration',fields()['exploration'],'exploration-stale',reply['evidence']['proposal_id'],
                                    ok=False,expected_status=409)
        self.assertEqual(rejected['code'],'stale_revision')
        self.assertIsNone(self.browser('state')['accepted']['exploration'])
        self.assertEqual(self.wire('/api/v1/requests/exploration-stale',headers={
            'X-Idea-Binding':self.opened['binding_id'],'Cookie':self.cookie,'X-Idea-Tab':self.tab})[0],404)
        self.accept_fields('exploration',dict(fields()['exploration'],outcome='Fresh human answer'),'exploration-fresh')
        self.assertEqual(self.browser('state')['accepted']['exploration']['outcome'],'Fresh human answer')

    def test_human_conditional_boundaries_are_required_and_persisted(self):
        # Moved from the v2 Method page: appetite and experiment now live on Exploration.
        self.accept_fields('priorities',fields()['priorities'],'cond-priorities')
        self.accept_fields('method',dict(fields()['method'],selection='appetite-led'),'cond-method')
        self.accept_fields('discovery',fields()['discovery'],'cond-discovery')
        incomplete=dict(fields()['exploration'],investment={'cap':2,'unit':'hours','boundary':''})
        self.draft('exploration',incomplete,'incomplete-appetite-draft')
        before=self.browser('state')
        evidence_before={str(path.relative_to(self.store)):path.read_bytes() for path in self.store.rglob('*.md')}
        # Structurally valid fields with an unanswered requirement are not_ready.
        self.assertEqual(self.accept_fields('exploration',incomplete,'incomplete-appetite',ok=False)['code'],'not_ready')
        refused=self.browser('state')
        self.assertIsNone(refused['accepted']['exploration'])
        self.assertIsNone(refused['steps']['exploration']['evidence_id'])
        self.assertEqual((refused['revision'],refused['draft_version']),(before['revision'],before['draft_version']))
        self.assertEqual(self.wire('/api/v1/requests/incomplete-appetite',headers={
            'X-Idea-Binding':self.opened['binding_id'],'Cookie':self.cookie,'X-Idea-Tab':self.tab})[0],404)
        self.assertEqual({str(path.relative_to(self.store)):path.read_bytes() for path in self.store.rglob('*.md')},evidence_before)
        appetite=dict(incomplete,investment={'cap':2,'unit':'hours','boundary':'One human chosen boundary'})
        self.accept_fields('exploration',appetite,'complete-appetite')
        self.assertEqual(self.browser('state')['accepted']['exploration']['investment'],appetite['investment'])


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

    def test_selection_and_fields_that_moved_to_exploration_are_refused_on_method(self):
        # Old appetite/experiment refusals: investment and experiment are no longer Method fields, so every
        # old variant stays refused here (unsupported field); their cap/unit/boundary/stop_rule meaning is
        # guarded on Exploration in ExplorationHandlerTests.
        base=fields()['method']
        invalid=[dict(base,selection=None),
            dict(base,investment={'cap':3,'unit':'hours','boundary':'Human boundary'}),
            dict(base,selection='experiment-led',experiment=dict(EXPERIMENT,stop_rule='')),
            dict(base,selection='not-a-method')]
        for cap in (0,-1,True,None):invalid.append(dict(base,selection='appetite-led',investment=dict(APPETITE,cap=cap)))
        invalid.append(dict(base,selection='appetite-led',investment=dict(APPETITE,unit='')))
        before=copy.deepcopy(self.state)
        for value in invalid:
            with self.subTest(value=value),self.assertRaises(IdeaError):
                METHOD_HANDLER.validate(self.state,self.idea,dict(self.payload,fields=value),self.source,self.context)
        self.assertEqual(self.state,before)

    def test_reason_is_optional_on_method(self):
        # The old `reason=' '` refusal is deliberately gone: v3 makes the reason optional.
        for reason in (' ',None,''):
            value=dict(fields()['method'],reason=reason)
            payload=dict(self.payload,fields=value)
            with self.subTest(reason=reason):
                METHOD_HANDLER.validate(self.state,self.idea,payload,acceptance_source(self.idea,'method',value),self.context)

    def test_selection_alone_makes_no_exploration_requirement_on_method(self):
        # Old: selection='appetite-led' without investment was refused on Method. Now Method accepts it and
        # Exploration is what refuses the missing investment.
        value=dict(fields()['method'],selection='appetite-led')
        METHOD_HANDLER.validate(self.state,self.idea,dict(self.payload,fields=value),
                                acceptance_source(self.idea,'method',value),self.context)
        idea=ready('appetite-led')
        with self.assertRaises(IdeaError) as caught:
            accept(idea,'exploration')
        self.assertEqual(caught.exception.code,'not_ready')


@unittest.skipUnless(os.name=='posix','Owned native ACLs remain unqualified')
class MethodHttpTests(unittest.TestCase):
    setUp=ExplorationHttpTests.setUp
    start_owner=ExplorationHttpTests.start_owner
    cleanup=ExplorationHttpTests.cleanup
    wire=ExplorationHttpTests.wire
    pair=ExplorationHttpTests.pair
    browser=ExplorationHttpTests.browser
    draft=ExplorationHttpTests.draft
    accept_fields=ExplorationHttpTests.accept_fields
    operation_reply=ExplorationHttpTests.operation_reply

    def prepare_method(self):
        self.assertIs(self.owner.handlers['method'],METHOD_HANDLER)
        self.accept_fields('priorities',fields()['priorities'],'method-priorities')

    def test_actual_grounded_recommendation_records_different_human_method_and_receipt(self):
        # R8: the agent's method proposal is its memory result only (with the preferred method it found);
        # the human's selection and reason are never part of it.
        self.prepare_method()
        memory=dict(status='found',sources=['decision:fixture-preference'],rationale='Agent reports a saved fixture preference',
                    preferred_method='bounded-plan')
        recommendation={'memory':memory}
        reply=self.operation_reply('method',recommendation,'method-proposed')
        before=self.browser('state');self.assertIsNone(before['accepted']['method'])
        chosen=dict(fields()['method'],memory=memory,selection='adaptive-slices',reason='Human chooses useful increments')
        self.draft('method',chosen,'method-human-draft')
        result=self.accept_fields('method',chosen,'method-human-accept',reply['evidence']['proposal_id'])
        after=self.browser('state');self.assertEqual(after['accepted']['method'],chosen)
        self.assertEqual(after['steps']['method']['status'],'saved');self.assertEqual(after['current_step'],'discovery')
        summary=next(item for item in after['proposals'] if item['proposal_id']==reply['evidence']['proposal_id'])
        self.assertEqual(summary['proposal'],recommendation)
        self.assertNotEqual(after['accepted']['method']['selection'],summary['proposal']['memory']['preferred_method'])
        self.assertEqual(self.browser('requests/method-human-accept'),result)
        self.assertEqual(result['proposal_id'],reply['evidence']['proposal_id'])

    def test_unsupported_search_claims_refused_and_disconnected_unavailable_choice_saved(self):
        self.prepare_method()
        for status,refs,preferred in (('found',['note:unverified'],'bounded-plan'),('varied',['note:unverified'],None),
                                      ('searched_no_preference',[],None)):
            claim=dict(fields()['method'],memory=dict(status=status,sources=refs,rationale='Unsupported claim',preferred_method=preferred))
            error=self.accept_fields('method',claim,'unsupported-'+status,ok=False)
            self.assertEqual(error['code'],'memory_provenance_missing')
        self.assertIsNone(self.browser('state')['accepted']['method'])
        self.agent.session_close()
        result=self.accept_fields('method',fields()['method'],'disconnected-manual-method')
        self.assertNotIn('proposal_id',result)
        self.assertEqual(self.browser('state')['accepted']['method']['memory']['status'],'unavailable')

    def test_method_fields_that_moved_to_exploration_are_refused_over_http(self):
        self.prepare_method()
        for extra in (dict(selection='appetite-led',investment=dict(APPETITE)),
                      dict(selection='experiment-led',experiment=dict(EXPERIMENT))):
            error=self.accept_fields('method',dict(fields()['method'],**extra),'moved-'+extra['selection'],ok=False)
            self.assertEqual(error['code'],'invalid_input')
        self.assertIsNone(self.browser('state')['accepted']['method'])
        experiment_method=dict(fields()['method'],selection='experiment-led')
        self.accept_fields('method',experiment_method,'plain-experiment-led')
        self.assertEqual(self.browser('state')['accepted']['method'],experiment_method)


if __name__=='__main__':unittest.main()
