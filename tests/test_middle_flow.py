"""Assessment Service/Owner integration over actual HTTP. Operator.

MiddleFlowTests use explicit test-only handlers to qualify provider/reducer
seams. PackagedMiddleJourneyTests exercise actual packaged handlers, immutable
assets, agent replies and placement through Owner HTTP; browser UI is separate.
"""
import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_agent_launch as transport
import test_service as service_fixture
from test_workflow import accept, complete, fields, original_idea
from idea_agent_client import AgentClientError
from idea_agent_source import proposal_source_digest
from idea_assessment import backlog_projection, insertion_neighbors, validate_actual_position
from idea_domain import IdeaError, now
from idea_service import Service, TrustedStepHandler
from idea_workflow import derive_state
import idea_service
import hashlib
import http.client
import json
from idea_steps import load_registry


@unittest.skipUnless(os.name == 'posix', 'Native owner ACLs remain unqualified')
class MiddleFlowTests(unittest.TestCase):
    # Reuse transport helpers without inheriting or duplicating the old suite.
    setUp = transport.AgentLaunchTests.setUp
    start_owner = transport.AgentLaunchTests.start_owner
    cleanup = transport.AgentLaunchTests.cleanup
    wire = transport.AgentLaunchTests.wire
    pair = transport.AgentLaunchTests.pair
    browser = transport.AgentLaunchTests.browser
    install_test_handlers = transport.AgentLaunchTests.install_test_handlers
    accept_fields = transport.AgentLaunchTests.accept_fields
    accept_test_shape = transport.AgentLaunchTests.accept_test_shape
    draft = transport.AgentLaunchTests.draft

    def ready(self):
        self.install_test_handlers()
        self.accept_test_shape()

    def enqueue_assessment(self, request='assessment-http-1'):
        state = self.browser('state')
        entry = state['proposal_sources']['assessment']
        self.assertTrue(entry['available'])
        source = entry['source']
        payload = dict(request_id=request, idea_id=self.idea_id,
            expected_revision=source['accepted_revision'],
            expected_draft_version=source['draft_version'], operation='assessment',
            source_digest=source['source_digest'])
        pending = self.browser('propose', payload)
        self.assertEqual(self.browser('propose', payload), pending)
        events = self.agent.events(timeout=0)['events']
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event['operation'], 'assessment')
        self.assertEqual(event['data'], source['data'])
        return {key: value for key, value in event.items() if key not in ('sequence', 'data')}

    def test_http_assessment_source_reply_replay_and_immutable_evidence(self):
        self.ready()
        self.assertIs(self.owner.broker.source_digest_fn, proposal_source_digest)
        before = self.browser('state')
        self.assertEqual(before['backlog_status'], dict(available=True, code='ok'))
        with self.owner.store.transaction() as state:
            self.assertEqual(before['backlog'], backlog_projection(state, self.idea_id))
            self.assertEqual(before['human_ratings'], state['ideas'][self.idea_id]['ratings'])
        self.assertEqual(before['human_ratings']['actor'], self.owner.actor)
        self.assertIsNone(before['assessment_summary'])
        correlation = self.enqueue_assessment()
        reply = dict(correlation, proposal=fields()['assess'])
        completed = self.agent.respond(reply)
        self.assertEqual(self.agent.respond(reply), completed)
        after = self.browser('state')
        for key in ('revision', 'draft_version', 'accepted', 'drafts', 'backlog', 'human_ratings', 'assessment_summary'):
            self.assertEqual(after[key], before[key])
        proposal = after['proposals'][0]
        self.assertEqual(proposal['operation'], 'assessment')
        self.assertTrue(proposal['acceptance_eligible'])
        self.assertFalse(proposal['stale'])
        self.assertEqual(proposal['proposal'], fields()['assess'])
        self.assertNotIn('score', proposal['proposal']['assessment'])
        raw = (self.store/completed['evidence']['path']).read_bytes()
        self.assertIn(b'operation: assessment', raw)
        self.assertIn(b'Current-agent proposal', raw)
        proposal['proposal']['assessment']['basis'] = 'Client mutation'
        self.assertEqual(self.browser('state')['proposals'][0]['proposal'], fields()['assess'])
        self.assertEqual((self.store/completed['evidence']['path']).read_bytes(), raw)

    def test_http_unknown_numeric_and_kano_remain_unscored_agent_suggestions(self):
        self.ready()
        for index, (method, inputs) in enumerate((
                ('wsjf', dict(value=3, time_criticality=None, enablement=2, effort=1)),
                ('rice', dict(reach=None, impact=2, confidence=0.5, effort=1)),
                ('kano', dict(category='delighter', hypothesis=True)))):
            proposal = fields()['assess']
            proposal['assessment'].update(method=method, inputs=inputs)
            reply = dict(self.enqueue_assessment('assessment-model-'+str(index)), proposal=proposal)
            self.agent.respond(reply)
            projected = self.browser('state')['proposals']
            matched = next(item for item in projected if item['request_id'] == reply['request_id'])
            self.assertEqual(matched['proposal'], proposal)
            self.assertNotIn('score', matched['proposal']['assessment'])
        self.assertIsNone(self.browser('state')['assessment_summary'])

    def test_http_reply_refuses_changed_consumed_priorities_without_publication(self):
        self.ready()
        correlation = self.enqueue_assessment()
        self.draft('priorities', dict(urgency=1), 'changed-priorities-buffer')
        with self.assertRaises(AgentClientError) as caught:
            self.agent.respond(dict(correlation, proposal=fields()['assess']))
        self.assertEqual(caught.exception.write_state, 'not_applied')
        self.assertEqual(self.browser('state')['proposals'], [])

    def test_http_overlay_refuses_forged_typed_source_digest_target_and_backlog(self):
        self.ready()
        original = self.owner.agent_provider.project
        for mutation in ('digest', 'target', 'backlog'):
            def project(*args):
                result = original(*args)
                source = result['proposal_sources']['assessment']['source']
                if mutation == 'digest':
                    source['source_digest'] = '0'*64
                elif mutation == 'target':
                    source['data']['target'] = {'score': 42}
                else:
                    source['data']['backlog']['comparisons'] = []
                return result
            with patch.object(self.owner.agent_provider, 'project', project):
                refused = self.browser('state', ok=False)
            self.assertEqual(refused['code'], 'invalid_agent_provider')
        self.assertTrue(self.browser('state')['proposal_sources']['assessment']['available'])

    def test_http_overlay_refuses_numeric_score_in_projected_agent_proposal(self):
        self.ready()
        self.agent.respond(dict(self.enqueue_assessment(), proposal=fields()['assess']))
        original = self.owner.agent_provider.project
        def project(*args):
            result = original(*args)
            result['proposals'][0]['proposal']['assessment']['score'] = 12
            return result
        with patch.object(self.owner.agent_provider, 'project', project):
            refused = self.browser('state', ok=False)
        self.assertEqual(refused['code'], 'invalid_agent_provider')

    def test_http_no_selection_and_backlog_capacity_have_explicit_absence(self):
        self.ready()
        selected = self.browser('state')
        with patch.object(idea_service, 'backlog_projection', side_effect=IdeaError('too_large', 'Fixture capacity')):
            limited = self.browser('state')
        self.assertIsNone(limited['backlog'])
        self.assertEqual(limited['backlog_status'], dict(available=False, code='source_too_large'))
        self.assertEqual(limited['human_ratings'], selected['human_ratings'])
        self.opened = self.client.open_binding('new')
        self.pair()
        unselected = self.browser('state')
        self.assertIsNone(unselected['idea_id'])
        self.assertIsNone(unselected['backlog'])
        self.assertEqual(unselected['backlog_status'], dict(available=False, code='no_selection'))
        self.assertIsNone(unselected['human_ratings'])
        self.assertIsNone(unselected['assessment_summary'])

    def test_http_latest_domain_assessment_summary_preserves_attribution_and_score(self):
        self.ready()
        # Durable fixture only; this does not exercise the future Assess handler.
        with self.owner.store.transaction(write=True) as state:
            idea = state['ideas'][self.idea_id]
            for step in ('method', 'visualize', 'assess'):
                idea = accept(idea, step)['idea']
            state['ideas'][self.idea_id] = idea
            self.owner.store.commit(state)
            latest = copy.deepcopy(idea['assessments'][-1])
        projected = self.browser('state')
        self.assertEqual(projected['assessment_summary'], latest)
        self.assertEqual(projected['backlog']['comparisons'][0]['assessment'], latest)
        self.assertIsNone(latest['score'])
        projected['assessment_summary']['inputs']['value'] = 99
        self.assertEqual(self.browser('state')['assessment_summary'], latest)

    def ready_assess(self):
        # Explicit test-only handler: validates placement and records one domain
        # snapshot under the Service reducer, not the later packaged handler.
        def validate(state, idea, payload, source, context):
            validate_actual_position(payload['fields']['position'], state['order'], idea['idea_id'])
        def apply(state, idea, payload, source, context):
            before = list(state['order'])
            old_revision = state['backlog_revision']
            position = payload['fields']['position']
            order = [key for key in before if key != idea['idea_id']]
            order.insert(position['actual_position']-1, idea['idea_id'])
            state['order'] = order
            state['backlog_revision'] += 1
            state['placements'].append(dict(idea_id=idea['idea_id'], idea_revision=idea['revision'],
                position=position['actual_position'], reason=position['override_reason'] or 'Accepted fixture suggestion',
                actor=context.actor, timestamp=now(), source_backlog_revision=old_revision,
                accepted_backlog_revision=state['backlog_revision'], neighbors=copy.deepcopy(position['neighbors']),
                snapshot=dict(ratings=copy.deepcopy(idea['ratings']), assessments=copy.deepcopy(idea['assessments']))))
            idea_service._invalidate_placement(state, before, context.actor, 'Fixture accepted placement',
                                               exclude=(idea['idea_id'],), refresh_draft=False)
        self.owner.handlers.update(assess=TrustedStepHandler(validate, apply),
            visualize=TrustedStepHandler(lambda *args: None))
        self.ready()
        self.accept_fields('method', fields()['method'], 'fixture-method')
        self.accept_fields('visualize', fields()['visualize'], 'fixture-visualize')

    def assess_payload(self, value, request, proposal=None):
        state = self.browser('state?idea_id='+self.idea_id)
        return dict(request_id=request, idea_id=self.idea_id, expected_revision=state['revision'],
            expected_draft_version=state['draft_version'], step='assess', fields=value,
            proposal_id=proposal, expected_backlog_revision=state['backlog_revision'])

    def test_http_linked_edited_assessment_override_preserves_original_and_snapshot_replay(self):
        self.ready_assess()
        other = self.browser('capture', dict(request_id='second-idea', raw_text='Comparison idea',
            workspace=dict(name='Fixture', path=str(self.workspace), confirmed=True)))['idea_id']
        before = self.browser('state?idea_id='+self.idea_id)
        original = fields()['assess']
        original['position']['neighbors'] = dict(before=None, after=other)
        reply = dict(self.enqueue_assessment(), proposal=original)
        published = self.agent.respond(reply)
        immutable = (self.store/published['evidence']['path']).read_bytes()
        edited = copy.deepcopy(original)
        edited['assessment'].update(method='rice', basis='Human evidence',
            inputs=dict(reach=20, impact=2, confidence=0.5, effort=2))
        edited['position'].update(actual_position=2, neighbors=dict(before=other, after=None),
                                  override_reason='Human chose the second position')
        self.draft('assess', edited, 'edited-assessment-buffer')
        payload = self.assess_payload(edited, 'edited-assessment-accept', published['evidence']['proposal_id'])
        accepted = self.browser('accept', payload)
        after = self.browser('state')
        self.assertEqual(after['assessment_summary']['score'], 10)
        self.assertEqual(after['human_ratings'], before['human_ratings'])
        self.assertEqual(after['backlog']['order'], [other, self.idea_id])
        self.assertEqual(after['steps']['assess']['status'], 'saved')
        self.assertNotEqual(after['steps']['review']['status'], 'review-needed')
        self.assertEqual(after['accepted']['assess'], edited)
        self.assertEqual((self.store/published['evidence']['path']).read_bytes(), immutable)
        with self.owner.store.transaction() as state:
            snapshot = copy.deepcopy(state['placements'][-1])
            domain = copy.deepcopy(state)
        self.assertEqual(snapshot['snapshot']['ratings'], before['human_ratings'])
        self.assertEqual(snapshot['snapshot']['assessments'][-1], after['assessment_summary'])
        self.assertEqual(snapshot['idea_revision'], accepted['revision'])
        self.assertEqual(self.browser('accept', payload), accepted)
        with self.owner.store.transaction() as state:
            self.assertEqual(state, domain)

    def test_http_assess_live_provenance_backlog_cas_and_original_position_refusals(self):
        self.ready_assess()
        published = self.agent.respond(dict(self.enqueue_assessment(), proposal=fields()['assess']))
        proposal_id = published['evidence']['proposal_id']
        invalid = self.assess_payload(fields()['assess'], 'stale-placement', proposal_id)
        invalid['expected_backlog_revision'] -= 1
        result = self.browser('accept', invalid, ok=False, expected_status=409)
        self.assertEqual(result['code'], 'stale_backlog')
        other = self.browser('capture', dict(request_id='new-comparison', raw_text='Later comparison',
            workspace=dict(name='Fixture', path=str(self.workspace), confirmed=True)))['idea_id']
        value = fields()['assess']; value['position']['neighbors']['after'] = other
        stale = self.assess_payload(value, 'changed-backlog-source', proposal_id)
        result = self.browser('accept', stale, ok=False, expected_status=409)
        self.assertEqual(result['code'], 'stale_source')
        current = self.agent.respond(dict(self.enqueue_assessment('new-current-assessment'), proposal=value))
        no_reason = copy.deepcopy(value)
        no_reason['position'].update(actual_position=2, neighbors=dict(before=other, after=None))
        result = self.browser('accept', self.assess_payload(no_reason, 'missing-human-override-reason',
            current['evidence']['proposal_id']), ok=False)
        self.assertEqual(result['code'], 'not_ready')
        altered = copy.deepcopy(value)
        altered['position'].update(proposed_position=2, actual_position=2, neighbors=dict(before=other, after=None))
        result = self.browser('accept', self.assess_payload(altered, 'changed-original-position',
            current['evidence']['proposal_id']), ok=False)
        self.assertEqual(result['code'], 'proposal_mismatch')
        self.agent.session_close()
        result = self.browser('accept', self.assess_payload(value, 'disconnected-proposal',
            current['evidence']['proposal_id']), ok=False, expected_status=503)
        self.assertEqual(result['code'], 'agent_unavailable')
        self.assertEqual(self.browser('state')['steps']['assess']['accepted_revision'], None)

    def test_http_manual_wsjf_and_kano_use_exact_domain_scores(self):
        self.ready_assess()
        value = fields()['assess']
        value['assessment']['inputs']['time_criticality'] = 4
        self.browser('accept', self.assess_payload(value, 'manual-wsjf'))
        self.assertEqual(self.browser('state')['assessment_summary']['score'], 9)
        kano = copy.deepcopy(value)
        kano['assessment'].update(method='kano', inputs=dict(category='must-be', hypothesis=False))
        self.browser('accept', self.assess_payload(kano, 'manual-kano'))
        self.assertIsNone(self.browser('state')['assessment_summary']['score'])


class AcceptanceReducerTests(unittest.TestCase):
    setUp = service_fixture.ServiceTests.setUp
    original = service_fixture.ServiceTests.original
    capture = service_fixture.ServiceTests.capture
    payload = service_fixture.ServiceTests.payload
    domain = service_fixture.ServiceTests.domain
    assert_code = service_fixture.ServiceTests.assert_code

    def seed_assessed(self):
        idea = complete(); key = idea['idea_id']
        with self.store.transaction(write=True) as state:
            state['ideas'][key] = idea; state['order'] = [key]; state['backlog_revision'] = 1
            self.store.commit(state)
        self.context.selected_idea_id = key
        return copy.deepcopy(idea)

    def test_shared_reducer_runs_inside_existing_mutation_without_context_or_nested_mutation(self):
        self.capture()
        payload = self.service._edit_payload(self.payload(), accept=True)
        before = copy.deepcopy(payload)
        with (patch.object(self.service, '_mutate', side_effect=AssertionError('Nested mutation')),
              patch.object(self.service, '_agent_context', side_effect=AssertionError('Context under Store')),
              patch.object(self.service, '_workspace', side_effect=AssertionError('Resolver in reducer'))):
            result = self.store.mutate(self.sid, payload['request_id'], dict(operation='fixture-accept', payload=payload),
                lambda state: self.service.accept_in_state(state, payload, None))
        self.assertEqual(payload, before)
        self.assertEqual(self.service.state()['human_ratings']['urgency'], 7)
        self.assertEqual(result['revision'], 2)
        self.assert_code('stale_revision', lambda: self.service.accept(dict(payload, request_id='stale-reducer')))
        stale_draft = self.payload(value=dict(urgency=8, importance=8), key='stale-draft-reducer')
        draft = dict(stale_draft, request_id='draft-before-reducer')
        del draft['proposal_id']; del draft['expected_backlog_revision']
        self.service.draft(draft)
        self.assert_code('stale_draft_version', lambda: self.store.mutate(self.sid, stale_draft['request_id'],
            dict(operation='fixture-accept', payload=stale_draft),
            lambda state: self.service.accept_in_state(state, stale_draft, None)))

    def test_capture_accept_workspace_preparation_is_inside_replay_and_after_cas(self):
        self.capture()
        value = fields()['capture']; value['workspace'] = self.original()['workspace']
        payload = self.payload('capture', value, 'capture-edit')
        with patch.object(self.service, '_workspace', wraps=self.service._workspace) as resolve:
            result = self.service.accept(payload)
            self.assertEqual(resolve.call_count, 1)
        self.workspace.rmdir()
        self.assertEqual(self.service.accept(payload), result)
        self.assert_code('stale_revision', lambda: self.service.accept(dict(payload, request_id='old-capture-edit')))

    def assert_append(self, before, new_key, expected_status='review-needed'):
        state = self.domain(); key = before['idea_id']; prior = state['ideas'][key]
        self.assertEqual(prior['revision'], before['revision']+1)
        self.assertEqual(prior['revisions'][:-1], before['revisions'])
        self.assertEqual(prior['workflow']['steps']['assess']['fields'], before['workflow']['steps']['assess']['fields'])
        self.assertEqual(prior['workflow']['steps']['assess']['acceptance'], before['workflow']['steps']['assess']['acceptance'])
        self.assertEqual(prior['workflow']['drafts'], before['workflow']['drafts'])
        self.assertEqual(prior['workflow']['draft_version'], before['workflow']['draft_version'])
        self.assertEqual(prior['workflow']['current_step'], before['workflow']['current_step'])
        self.assertIn('assess', prior['workflow']['steps']['assess']['invalidated_by'])
        self.assertEqual(prior['ratings'], before['ratings'])
        self.assertEqual(prior['assessments'], before['assessments'])
        view = derive_state(prior)
        self.assertEqual(view['steps']['assess']['status'], expected_status)
        self.assertNotEqual(view['steps']['review']['status'], 'review-needed')
        self.assertEqual(state['ideas'][new_key]['revision'], 1)
        self.assertEqual(state['backlog_revision'], 2)

    def test_browser_capture_append_invalidates_prior_accepted_assess_with_preserved_history(self):
        before = self.seed_assessed()
        result = self.capture(); self.assert_append(before, result['idea_id'])
        state = self.domain()
        self.assertEqual(self.capture(), result)
        self.assertEqual(self.domain(), state)

    def test_legacy_capture_uses_same_append_invalidation(self):
        before = self.seed_assessed()
        path = self.root.parent/'legacy-input.txt'; path.write_text('Legacy original words')
        result = idea_service.run_legacy(SimpleNamespace(command='capture', store=str(self.root),
            text_file=str(path), actor='trusted-operator'), {})
        self.assert_append(before, result['idea']['idea_id'])

    def seed_edited_assess_draft(self):
        before = self.seed_assessed()
        value = fields()['assess']; value['assessment']['basis'] = 'Unaccepted human evidence'
        payload = self.payload('assess', value, 'human-assess-buffer')
        del payload['proposal_id']; del payload['expected_backlog_revision']
        self.service.draft(payload)
        return self.domain()['ideas'][before['idea_id']]

    def test_browser_capture_preserves_independent_edited_assess_draft(self):
        before = self.seed_edited_assess_draft()
        result = self.capture()
        self.assert_append(before, result['idea_id'], expected_status='unsaved')

    def test_legacy_capture_preserves_independent_edited_assess_draft(self):
        before = self.seed_edited_assess_draft()
        path = self.root.parent/'legacy-edited-input.txt'; path.write_text('Independent legacy capture')
        result = idea_service.run_legacy(SimpleNamespace(command='capture', store=str(self.root),
            text_file=str(path), actor='trusted-operator'), {})
        self.assert_append(before, result['idea']['idea_id'], expected_status='unsaved')

    def test_legacy_placement_default_still_refuses_conflicting_operator_draft(self):
        before = self.seed_edited_assess_draft(); key = before['idea_id']
        other = original_idea(); other['idea_id'] = 'idea_'+'2'*32
        state = self.domain(); state['ideas'][other['idea_id']] = other
        state['order'] = [other['idea_id'], key]
        unchanged = copy.deepcopy(state)
        self.assert_code('draft_conflict', lambda: idea_service._invalidate_placement(state,
            [key, other['idea_id']], 'trusted-operator', 'Explicit legacy Place'))
        self.assertEqual(state, unchanged)

    def test_placement_exclusion_absent_before_archived_and_unaffected_are_preserved(self):
        original = complete(); key = original['idea_id']
        other = copy.deepcopy(original); other['idea_id'] = 'idea_'+'2'*32
        archived = copy.deepcopy(original); archived['idea_id'] = 'idea_'+'3'*32; archived['status'] = 'archived'
        new = copy.deepcopy(original); new['idea_id'] = 'idea_'+'4'*32
        state = dict(ideas={key:original, other['idea_id']:other, archived['idea_id']:archived, new['idea_id']:new},
            order=[key, archived['idea_id'], other['idea_id'], new['idea_id']])
        frozen = copy.deepcopy(state['ideas'])
        idea_service._invalidate_placement(state, [key, other['idea_id'], archived['idea_id']],
            'trusted-operator', 'Human placement', exclude=(key,))
        for unchanged in (key, archived['idea_id'], new['idea_id']):
            self.assertEqual(state['ideas'][unchanged], frozen[unchanged])
        self.assertEqual(state['ideas'][other['idea_id']]['revision'], other['revision']+1)
        unchanged = copy.deepcopy(state)
        idea_service._invalidate_placement(state, state['order'], 'trusted-operator', 'Same order')
        self.assertEqual(state, unchanged)


@unittest.skipUnless(os.name == 'posix', 'Native owner ACLs remain unqualified')
class PackagedMiddleJourneyTests(unittest.TestCase):
    """actual Owner/agent HTTP with literal packaged handlers.

    Archival is a canonical durable fixture because browser archive/handoff is
    outside CP3. Every Capture, acceptance, proposal and reorder uses HTTP.
    Native Windows/macOS and remote client execution remain NOT OBSERVED.
    """
    setUp = transport.AgentLaunchTests.setUp
    start_owner = transport.AgentLaunchTests.start_owner
    cleanup = transport.AgentLaunchTests.cleanup
    wire = transport.AgentLaunchTests.wire
    pair = transport.AgentLaunchTests.pair
    browser = transport.AgentLaunchTests.browser
    accept_fields = transport.AgentLaunchTests.accept_fields
    draft = transport.AgentLaunchTests.draft
    enqueue_assessment = MiddleFlowTests.enqueue_assessment
    assess_payload = MiddleFlowTests.assess_payload

    def selected(self):
        return self.browser('state?idea_id='+self.idea_id)

    def accept_current(self,step,value,request):
        state = self.selected()
        return self.browser('accept',dict(request_id=request,idea_id=self.idea_id,
            expected_revision=state['revision'],expected_draft_version=state['draft_version'],
            step=step,fields=value,proposal_id=None,expected_backlog_revision=None))

    def domain(self):
        with self.owner.store.transaction() as state:
            return copy.deepcopy(state)

    def immutable_assets(self):
        return {path.relative_to(self.store).as_posix():path.read_bytes()
                for path in (self.store/'assets').rglob('*') if path.is_file()}

    def capture_other(self,request):
        return self.browser('capture',dict(request_id=request,raw_text='Original '+request,
            workspace=dict(name='Fixture',path=str(self.workspace),confirmed=True)))['idea_id']

    def packaged_ready(self,*,visual='skipped'):
        handlers,routes = load_registry()
        self.assertEqual(set(self.owner.handlers),{'shape','method','visualize','assess'})
        for step,handler in handlers.items():
            self.assertIs(self.owner.handlers[step],handler)
        self.assertIs(self.owner.routes['visual-set/accept'],routes['visual-set/accept'])
        self.selected()
        self.assertEqual(self.selected()['steps']['capture']['status'],'saved')
        for step in ('priorities','shape','method'):
            self.accept_current(step,fields()[step],'packaged-'+step+'-'+self.idea_id)
        if visual=='set':
            return self.upload_set()
        self.accept_current('visualize',fields()['visualize'],'packaged-visual-skip-'+self.idea_id)
        self.assertEqual(self.selected()['steps']['visualize']['status'],'skipped')
        return None

    def upload_set(self):
        raw = b'\x89PNG\r\n\x1a\nJ10 immutable fixture'
        state = self.selected()
        upload = self.browser('uploads',dict(request_id='packaged-upload',idea_id=self.idea_id,
            expected_revision=state['revision'],name='J10.png',declared_type='image/png',size=len(raw)))
        connection = http.client.HTTPConnection('127.0.0.1',self.owner.server.server_port,timeout=10)
        try:
            connection.request('PUT','/api/v1/uploads/'+upload['upload_id']+'/bytes',raw,
                {'Cookie':self.cookie,'X-Idea-Binding':self.opened['binding_id'],'X-CSRF-Token':self.csrf,'X-Idea-Tab':self.tab,
                 'Origin':self.owner.server.origin,'Content-Type':'application/octet-stream'})
            response = connection.getresponse(); result = json.loads(response.read())
            self.assertEqual(response.status,200,result)
        finally:
            connection.close()
        self.assertEqual(result['sha256'],hashlib.sha256(raw).hexdigest())
        state = self.selected()
        payload = dict(request_id='packaged-set',idea_id=self.idea_id,expected_revision=state['revision'],
            expected_draft_version=state['draft_version'],step='visualize',proposal_id=None,
            expected_backlog_revision=None,design_set_id=None,asset_ids=[upload['asset_id']],
            fields=dict(disposition='accepted_set',reason=None,design_set_id=None,brief_evidence_id=None))
        accepted = self.browser('visual-set/accept',payload)
        self.assertEqual(self.browser('visual-set/accept',payload),accepted)
        state = self.selected()
        self.assertEqual(state['steps']['visualize']['status'],'saved')
        self.assertEqual(state['accepted']['visualize']['design_set_id'],accepted['design_set_id'])
        records = [entry['record'] for entry in state['asset_inventory']['records']]
        self.assertEqual([record['kind'] for record in records],['upload-intent','asset','design-set'])
        self.assertEqual(records[-1]['session_id'],self.opened['session_id'])
        return accepted['design_set_id']

    def assessment_at(self,position=None):
        state = self.selected(); order = state['backlog']['order']
        position = order.index(self.idea_id)+1 if position is None else position
        value = fields()['assess']
        value['position'].update(proposed_position=position,actual_position=position,
            neighbors=insertion_neighbors(order,self.idea_id,position))
        return value

    def manual_assess(self,request='packaged-assess'):
        value = self.assessment_at()
        self.browser('accept',self.assess_payload(value,request))
        self.assertEqual(self.selected()['steps']['assess']['status'],'saved')
        self.assertNotEqual(self.selected()['steps']['review']['status'],'saved')

    def test_packaged_set_agent_reply_human_edit_override_and_exact_receipt(self):
        other = self.capture_other('comparison-before-journey')
        set_id = self.packaged_ready(visual='set'); before = self.selected()
        original = self.assessment_at(1)
        correlation = self.enqueue_assessment('packaged-ai-assessment')
        reply = dict(correlation,proposal=original)
        published = self.agent.respond(reply)
        self.assertEqual(self.agent.respond(reply),published)
        self.assertEqual(self.selected()['accepted'],before['accepted'])
        evidence = self.store/published['evidence']['path']; immutable = evidence.read_bytes()
        assets = self.immutable_assets()
        edited = copy.deepcopy(original)
        edited['assessment'].update(method='rice',basis='Human measured reach',
            inputs=dict(reach=20,impact=2,confidence=0.5,effort=2))
        edited['position'].update(actual_position=2,neighbors=dict(before=other,after=None),
            override_reason='Human chose delivery after the comparison')
        self.draft('assess',edited,'packaged-human-assess-buffer')
        payload = self.assess_payload(edited,'packaged-human-accept',published['evidence']['proposal_id'])
        stale = dict(payload,request_id='packaged-stale-backlog',expected_backlog_revision=payload['expected_backlog_revision']-1)
        self.assertEqual(self.browser('accept',stale,ok=False,expected_status=409)['code'],'stale_backlog')
        accepted = self.browser('accept',payload); after = self.selected(); domain = self.domain()
        self.assertEqual(after['assessment_summary']['score'],10)
        self.assertEqual(after['human_ratings'],before['human_ratings'])
        self.assertEqual(after['accepted']['assess'],edited)
        self.assertEqual(after['backlog']['order'],[other,self.idea_id])
        self.assertEqual(after['accepted']['visualize']['design_set_id'],set_id)
        self.assertEqual(after['steps']['assess']['status'],'saved')
        self.assertNotEqual(after['steps']['review']['status'],'saved')
        placement = domain['placements'][-1]
        self.assertEqual(placement['idea_revision'],accepted['revision'])
        self.assertEqual(placement['snapshot']['ratings'],before['human_ratings'])
        self.assertEqual(placement['snapshot']['assessments'][-1],after['assessment_summary'])
        self.assertEqual(self.browser('accept',payload),accepted)
        self.assertEqual(self.browser('requests/'+payload['request_id']),accepted)
        self.assertEqual(self.domain(),domain)
        self.assertEqual(evidence.read_bytes(),immutable); self.assertEqual(self.immutable_assets(),assets)

    def test_packaged_skip_manual_formula_and_unknown_scores_preserve_human_ratings(self):
        self.packaged_ready(); ratings = self.selected()['human_ratings']
        cases = [('wsjf',dict(value=3,time_criticality=4,enablement=2,effort=1),9),
            ('rice',dict(reach=20,impact=2,confidence=0.5,effort=2),10),
            ('wsjf',dict(value=3,time_criticality=None,enablement=2,effort=1),None),
            ('rice',dict(reach=None,impact=2,confidence=0.5,effort=2),None),
            ('kano',dict(category='delighter',hypothesis=True),None)]
        for index,(method,inputs,score) in enumerate(cases):
            with self.subTest(method=method,inputs=inputs):
                value = self.assessment_at(); value['assessment'].update(method=method,inputs=inputs)
                self.browser('accept',self.assess_payload(value,'packaged-score-'+str(index)))
                state = self.selected()
                self.assertEqual(state['assessment_summary']['score'],score)
                self.assertEqual(state['human_ratings'],ratings)
                self.assertEqual(state['steps']['assess']['status'],'saved')
                self.assertEqual(state['steps']['visualize']['status'],'skipped')
                self.assertNotEqual(state['steps']['review']['status'],'saved')
        self.assertEqual(len(self.domain()['ideas'][self.idea_id]['assessments']),len(cases))

    def test_packaged_bad_assessment_neighbors_pin_response_and_refuse_corrected_correlation(self):
        self.capture_other('assessment-neighbor'); self.packaged_ready()
        value = self.assessment_at(1)
        correlation = self.enqueue_assessment('bad-assessment-neighbors')
        with self.owner.broker.condition:
            pending_result = copy.deepcopy(self.owner.broker.bindings[self.opened['binding_id']]['requests'][correlation['request_id']]['result'])
        self.assertEqual(pending_result['status'],'pending')
        self.assertEqual(pending_result['write_state'],'not_applied')
        bad = copy.deepcopy(value); bad['position']['neighbors']['after'] = None
        before = self.domain()
        files = {path.relative_to(self.store).as_posix():path.read_bytes()
            for path in self.store.rglob('*') if path.is_file()}
        with self.assertRaises(AgentClientError) as failed:
            self.agent.respond(dict(correlation,proposal=bad))
        self.assertEqual(failed.exception.code,'proposal_commit_uncertain')  # the broker's own code, no longer masked
        self.assertEqual(failed.exception.write_state,'committed_uncertain')
        self.assertEqual(self.domain(),before)
        with self.owner.broker.condition:
            pinned = copy.deepcopy(self.owner.broker.bindings[self.opened['binding_id']]['requests'][correlation['request_id']])
        self.assertEqual(json.loads(pinned['response']),dict(correlation,proposal=bad))
        self.assertEqual(pinned['state'],'pending'); self.assertEqual(pinned['result'],pending_result)
        with self.assertRaises(AgentClientError) as corrected:
            self.agent.respond(dict(correlation,proposal=value))
        self.assertEqual(corrected.exception.code,'response_conflict')  # a changed retry of a pinned reply, said plainly
        self.assertEqual(corrected.exception.write_state,'not_applied')
        with self.owner.broker.condition:
            unchanged = copy.deepcopy(self.owner.broker.bindings[self.opened['binding_id']]['requests'][correlation['request_id']])
        self.assertEqual(unchanged,pinned)
        self.assertEqual(unchanged['result'],pending_result)
        self.assertEqual(self.domain(),before)
        self.assertEqual({path.relative_to(self.store).as_posix():path.read_bytes()
            for path in self.store.rglob('*') if path.is_file()},files)
        self.assertEqual(self.selected()['proposals'],[])

    def check_source_invalidation(self,step):
        set_id = self.packaged_ready(visual='set'); self.manual_assess()
        before = self.domain()['ideas'][self.idea_id]; assets = self.immutable_assets()
        value = copy.deepcopy(before['workflow']['steps'][step]['fields'])
        if step=='capture': value['raw_text'] = 'Human edited Capture source words'
        elif step=='shape': value['outcome'] = 'Human changed accepted outcome'
        else: value['urgency'] = 9
        accepted = self.accept_current(step,value,'packaged-change-'+step)
        state = self.selected(); after = self.domain()['ideas'][self.idea_id]
        self.assertIn('assess',accepted['invalidated'])
        self.assertEqual(state['steps']['assess']['status'],'review-needed')
        self.assertEqual(after['workflow']['steps']['assess']['acceptance'],before['workflow']['steps']['assess']['acceptance'])
        self.assertEqual(after['workflow']['steps']['assess']['fields'],before['workflow']['steps']['assess']['fields'])
        self.assertEqual(after['revisions'][:-1],before['revisions'])
        self.assertEqual(after['assessments'],before['assessments'])
        self.assertEqual(after['origin'],before['origin'])
        self.assertEqual(after['workflow']['steps']['visualize']['fields']['design_set_id'],set_id)
        if step in ('capture','shape'):
            self.assertEqual(state['steps']['visualize']['status'],'review-needed')
            self.assertEqual(state['steps']['method']['status'],'review-needed')
        else:
            self.assertEqual(state['steps']['visualize']['status'],'saved')
            self.assertEqual(state['steps']['shape']['status'],'saved')
        self.assertNotEqual(state['steps']['review']['status'],'saved')
        self.assertEqual(self.immutable_assets(),assets)
        with self.owner.store.transaction() as active:
            self.assertEqual(len(self.owner.store.asset_records(active,self.idea_id)),3)

    def test_packaged_capture_change_invalidates_consumers_preserving_set_history(self):
        self.check_source_invalidation('capture')

    def test_packaged_shape_change_invalidates_consumers_preserving_set_history(self):
        self.check_source_invalidation('shape')

    def test_packaged_priorities_change_invalidates_assess_preserving_visual_source(self):
        self.check_source_invalidation('priorities')

    def test_packaged_capture_append_and_assess_reorder_change_real_neighbors(self):
        first = self.idea_id; self.packaged_ready(visual='set'); self.manual_assess()
        before = self.domain()['ideas'][first]; assets = self.immutable_assets()
        other = self.capture_other('new-neighbor')
        appended = self.domain()['ideas'][first]
        self.assertEqual(appended['revision'],before['revision']+1)
        self.assertEqual(appended['workflow']['steps']['assess']['acceptance'],before['workflow']['steps']['assess']['acceptance'])
        self.assertEqual(appended['workflow']['drafts'],before['workflow']['drafts'])
        self.assertEqual(self.selected()['steps']['assess']['status'],'review-needed')
        self.assertNotEqual(self.selected()['steps']['review']['status'],'review-needed')
        self.idea_id = other; self.packaged_ready()
        value = self.assessment_at(1)
        self.browser('accept',self.assess_payload(value,'packaged-neighbor-reorder'))
        changed = self.domain()['ideas'][first]
        self.assertEqual(self.selected()['backlog']['order'],[other,first])
        self.assertEqual(changed['revision'],appended['revision']+1)
        self.assertEqual(changed['revisions'][:-1],appended['revisions'])
        self.assertEqual(changed['ratings'],before['ratings'])
        self.assertEqual(changed['assessments'],before['assessments'])
        self.assertEqual(changed['workflow']['steps']['assess']['acceptance'],before['workflow']['steps']['assess']['acceptance'])
        self.assertEqual(changed['workflow']['drafts'],before['workflow']['drafts'])
        self.assertEqual(self.immutable_assets(),assets)
        self.assertEqual(self.selected()['steps']['assess']['status'],'saved')
        self.assertNotEqual(self.selected()['steps']['review']['status'],'saved')

    def test_packaged_unaccepted_neighbor_unchanged_by_capture_append_and_assess_reorder(self):
        first = self.idea_id; frozen = self.domain()['ideas'][first]
        self.assertIsNone(frozen['workflow']['steps']['assess']['acceptance'])
        second = self.capture_other('accepted-neighbor'); self.idea_id = second
        self.packaged_ready(); self.manual_assess()
        accepted = self.domain()['ideas'][second]
        third = self.capture_other('appended-neighbor')
        appended = self.domain()
        self.assertEqual(appended['ideas'][first],frozen)
        self.assertEqual(appended['ideas'][second]['revision'],accepted['revision']+1)
        self.assertEqual(appended['order'],[first,second,third])
        self.browser('accept',self.assess_payload(self.assessment_at(1),'move-accepted-neighbor'))
        reordered = self.domain()
        self.assertEqual(reordered['order'],[second,first,third])
        self.assertEqual(reordered['ideas'][first],frozen)
        self.idea_id = first; projected = self.selected()
        self.assertIsNone(projected['steps']['assess']['accepted_revision'])
        self.assertIsNone(projected['steps']['review']['accepted_revision'])
        self.assertNotIn(projected['steps']['assess']['status'],('saved','review-needed'))
        self.assertNotIn(projected['steps']['review']['status'],('saved','review-needed'))

    def test_packaged_archived_assessment_and_asset_evidence_survive_capture_and_reorder(self):
        first = self.idea_id; self.packaged_ready(visual='set'); self.manual_assess()
        # Canonical archive fixture; no browser archive endpoint exists in CP3.
        with self.owner.store.transaction(write=True) as state:
            idea = state['ideas'][first]; pid = 'plan_'+'a'*32; content = '# J10 archive fixture\n'
            idea['plans'].append(dict(plan_id=pid,idea_id=first,idea_revision=idea['revision'],
                path=str(self.store/'plan-evidence'/(pid+'.md')),source_path=str(self.workspace/'plan.md'),
                content=content,sha256=hashlib.sha256(content.encode()).hexdigest(),actor=self.owner.actor,
                timestamp=now(),validation={'builtin':'idea-trace-and-sections-v1'}))
            idea['status'] = 'archived'
            state['archives'][first+'/r'+str(idea['revision'])+'.json'] = dict(idea_id=first,
                origin=copy.deepcopy(idea['origin']),revision=copy.deepcopy(idea['revisions'][-1]))
            self.owner.store.commit(state)
            self.assertEqual(self.owner.store.view_issues(state,repair=True),[])
        frozen = self.domain()['ideas'][first]; assets = self.immutable_assets()
        # Inventory the generated archive files without guessing their suffix.
        archived_files = {path.relative_to(self.store).as_posix():path.read_bytes()
            for path in self.store.rglob('*') if path.is_file() and
            ('archive' in path.parts or 'plan-evidence' in path.parts)}
        self.assertIn('archive/'+first+'/r'+str(frozen['revision'])+'.json',archived_files)
        other = self.capture_other('after-archive'); self.idea_id = other; self.packaged_ready()
        self.browser('accept',self.assess_payload(self.assessment_at(1),'reorder-around-archive'))
        self.assertEqual(self.domain()['ideas'][first],frozen)
        self.assertEqual(self.immutable_assets(),assets)
        for path,raw in archived_files.items(): self.assertEqual((self.store/path).read_bytes(),raw)
        self.idea_id = first; state = self.selected()
        self.assertEqual(state['idea_status'],'archived')
        self.assertEqual(state['steps']['assess']['status'],'saved')
        self.assertNotEqual(state['steps']['review']['status'],'saved')
        self.assertEqual(len(state['asset_inventory']['records']),3)


if __name__ == '__main__':
    unittest.main()
