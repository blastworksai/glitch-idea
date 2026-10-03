"""Pure assessment source/formula/placement contracts. """
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
import idea_assessment as assessment_helpers
from idea_domain import ASSESS_KEYS, IdeaError, assessment, snapshot
from idea_workflow import save_draft
from test_workflow import accept, captured, complete, fields, original_idea
import idea_agent_source as agent_source
import idea_store as storage
from idea_service import Service, TrustedContext
from idea_steps import load_registry
from idea_steps import assess as assess_handler
from idea_workflow import acceptance_source, derive_state

KEY = 'idea_'+'1'*32
OTHER = 'idea_'+'2'*32
THIRD = 'idea_'+'3'*32


def state_fixture():
    selected = complete()
    other = original_idea()
    other['idea_id'] = OTHER
    third = original_idea()
    third['idea_id'] = THIRD
    return dict(backlog_revision=8, order=[OTHER, KEY, THIRD],
                ideas={KEY: selected, OTHER: other, THIRD: third})


def proposed(source=None, position=2):
    order = [OTHER, KEY, THIRD] if source is None else source['data']['backlog']['order']
    result = fields()['assess']
    result['position'] = dict(proposed_position=position, actual_position=position,
        neighbors=assessment_helpers.insertion_neighbors(order, KEY, position),
        override_reason=None)
    return result


class AssessmentHelperTests(unittest.TestCase):
    def assert_code(self, code, callback):
        with self.assertRaises(IdeaError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_route_export_and_detached_source_projection(self):
        state = state_fixture()
        before = copy.deepcopy(state)
        selected = assessment_helpers.prepare_assessment_source(state, KEY)
        self.assertEqual(assessment_helpers.ROUTES, ())
        self.assertEqual(set(selected), {'accepted_revision', 'draft_version', 'data'})
        self.assertEqual(set(selected['data']), {'steps', 'backlog', 'target'})
        self.assertEqual(set(selected['data']['steps']), {'capture', 'priorities', 'shape'})
        self.assertEqual(selected['data']['target'], fields()['assess'])
        self.assertEqual(selected['data']['backlog']['order'], [OTHER, KEY, THIRD])
        comparison = selected['data']['backlog']['comparisons'][1]
        self.assertEqual(comparison['ratings'], state['ideas'][KEY]['ratings'])
        self.assertEqual(comparison['assessment'], state['ideas'][KEY]['assessments'][-1])
        selected['data']['steps']['capture']['raw_text'] = 'Detached'
        selected['data']['backlog']['comparisons'][1]['ratings']['urgency'] = 10
        selected['data']['target']['assessment']['inputs']['effort'] = 99
        projection = assessment_helpers.backlog_projection(state, KEY)
        projection['order'].reverse()
        self.assertEqual(state, before)

    def test_target_null_empty_partial_and_accepted_are_distinct(self):
        idea = accept(accept(captured(), 'priorities')['idea'], 'shape')['idea']
        state = state_fixture(); state['ideas'][KEY] = idea
        source = assessment_helpers.prepare_assessment_source(state, KEY)
        self.assertIsNone(source['data']['target'])
        for target in ({}, {'assessment': {'method': 'rice'}},
                       {'assessment': {'inputs': {'reach': None}}, 'position': {}}):
            idea = save_draft(idea, 'assess', target, expected_revision=idea['revision'],
                expected_draft_version=idea['workflow']['draft_version'])['idea']
            state['ideas'][KEY] = idea
            self.assertEqual(assessment_helpers.prepare_assessment_source(state, KEY)['data']['target'], target)

    def test_revised_capture_accepted_fields_used_not_origin(self):
        state = state_fixture()
        capture = fields()['capture']; capture['raw_text'] = 'Revised words'
        idea = accept(state['ideas'][KEY], 'capture', capture)['idea']
        idea = accept(idea, 'shape')['idea']
        state['ideas'][KEY] = idea
        source = assessment_helpers.prepare_assessment_source(state, KEY)
        self.assertEqual(source['data']['steps']['capture']['raw_text'], 'Revised words')
        self.assertNotEqual(source['data']['steps']['capture']['raw_text'], idea['origin']['text'])

    def test_legacy_incomplete_and_changed_consumed_drafts_do_not_create_acceptance(self):
        for idea in (original_idea(), captured()):
            state = state_fixture(); state['ideas'][KEY] = idea
            self.assert_code('not_ready', lambda: assessment_helpers.prepare_assessment_source(state, KEY))
        state = state_fixture(); idea = state['ideas'][KEY]
        changed = fields()['shape']; changed['outcome'] = 'Unsaved'
        state['ideas'][KEY] = save_draft(idea, 'shape', changed,
            expected_revision=idea['revision'], expected_draft_version=0)['idea']
        self.assert_code('not_ready', lambda: assessment_helpers.prepare_assessment_source(state, KEY))

    def test_canonical_digest_and_separate_draft_cas(self):
        source = assessment_helpers.prepare_assessment_source(state_fixture(), KEY)
        payload = dict(operation='assessment', accepted_revision=source['accepted_revision'], data=source['data'])
        expected = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
            separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
        self.assertEqual(assessment_helpers.assessment_digest('assessment', source, idea_id=KEY), expected)
        reordered = dict(reversed(tuple(source.items())))
        reordered['data'] = dict(reversed(tuple(source['data'].items())))
        self.assertEqual(assessment_helpers.assessment_digest('assessment', reordered), expected)
        changed = copy.deepcopy(source); changed['draft_version'] += 1
        self.assertEqual(assessment_helpers.assessment_digest('assessment', changed), expected)
        self.assert_code('operation_unavailable', lambda: assessment_helpers.assessment_digest('shape', source))

    def test_digest_covers_target_consumed_steps_order_revision_comparison_and_null(self):
        source = assessment_helpers.prepare_assessment_source(state_fixture(), KEY)
        baseline = assessment_helpers.assessment_digest('assessment', source)
        variants = []
        def variant():
            value = copy.deepcopy(source); variants.append(value); return value
        variant()['accepted_revision'] += 1
        variant()['data']['steps']['capture']['raw_text'] = 'Changed'
        variant()['data']['steps']['shape']['next_slice'] = 'Changed'
        variant()['data']['backlog']['revision'] += 1
        variant()['data']['backlog']['comparisons'][0]['revision'] += 1
        variant()['data']['backlog']['comparisons'][1]['ratings']['importance'] = 9
        variant()['data']['target']['assessment']['method'] = 'rice'
        variants[-1]['data']['target']['assessment']['inputs'] = {'reach': None}
        variant()['data']['target'] = None
        changed = variant()
        changed['data']['backlog']['order'].reverse()
        changed['data']['backlog']['comparisons'].reverse()
        for value in variants:
            self.assertNotEqual(assessment_helpers.assessment_digest('assessment', value), baseline)

    def test_exact_source_schema_rejects_bad_types_unknown_fields_and_target_identity(self):
        source = assessment_helpers.prepare_assessment_source(state_fixture(), KEY)
        invalid = []
        def variant():
            value = copy.deepcopy(source); invalid.append(value); return value
        variant()['draft_version'] = False
        variant()['accepted_revision'] = True
        variant()['credential'] = 'unexpected'
        variant()['data']['steps']['method'] = fields()['method']
        variant()['data']['backlog']['revision'] = False
        variant()['data']['backlog']['comparisons'].pop()
        variant()['data']['backlog']['comparisons'].reverse()
        variant()['data']['backlog']['order'].append(KEY)
        variant()['data']['backlog']['comparisons'][0]['status'] = 'ready'
        variant()['data']['target'] = {'score': 42}
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(IdeaError):
                assessment_helpers.validate_assessment_source(value, idea_id=KEY)
        self.assert_code('not_found', lambda: assessment_helpers.validate_assessment_source(source, idea_id='idea_'+'f'*32))
        changed = copy.deepcopy(source); changed['data']['backlog']['comparisons'][1]['revision'] += 1
        self.assert_code('stale_source', lambda: assessment_helpers.validate_assessment_source(changed, idea_id=KEY))
        changed = copy.deepcopy(source); changed['data']['steps']['priorities']['urgency'] = 8
        self.assert_code('stale_source', lambda: assessment_helpers.validate_assessment_source(changed, idea_id=KEY))

    def test_domain_formula_unknowns_kano_and_ratings_preserved(self):
        state = state_fixture(); before = copy.deepcopy(state)
        source = assessment_helpers.prepare_assessment_source(state, KEY)
        cases = [
            ('wsjf', dict(value=6, time_criticality=3, enablement=1, effort=2), 5),
            ('rice', dict(reach=100, impact=2, confidence=0.5, effort=4), 25),
            ('wsjf', dict(value=6, time_criticality=None, enablement=1, effort=2), None),
            ('rice', dict(reach=None, impact=2, confidence=0.5, effort=4), None),
            ('kano', dict(category='delighter', hypothesis=True), None),
            ('kano', dict(category='must-be', hypothesis=False), None),
        ]
        for method, inputs, score in cases:
            proposal = proposed(source)
            proposal['assessment'].update(method=method, inputs=inputs)
            validated = assessment_helpers.validate_assessment_proposal(proposal, source=source, idea_id=KEY)
            self.assertEqual(set(validated['assessment']), ASSESS_KEYS)
            self.assertNotIn('score', validated['assessment'])
            self.assertEqual(assessment(validated['assessment'])['score'], score)
        self.assertEqual(state, before)

    def test_invalid_scores_kano_null_boolean_numbers_and_bounds_rejected(self):
        source = assessment_helpers.prepare_assessment_source(state_fixture(), KEY)
        invalid = []
        def variant():
            value = proposed(source); invalid.append(value); return value
        variant()['assessment']['score'] = 99
        variant()['assessment']['inputs']['effort'] = 0
        variant()['assessment']['inputs']['value'] = True
        variant()['assessment']['inputs']['value'] = float('inf')
        variant()['assessment']['inputs']['value'] = 10**12+1
        variant()['assessment'].update(method='rice', inputs=dict(reach=1, impact=1, confidence=1.01, effort=1))
        variant()['assessment'].update(method='kano', inputs=dict(category=None, hypothesis=True))
        variant()['assessment'].update(method='kano', inputs=dict(category='delighter', hypothesis=None))
        variant()['assessment'].update(method='kano', inputs=dict(category='delighter', hypothesis=1))
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(IdeaError):
                assessment_helpers.validate_assessment_proposal(value, source=source, idea_id=KEY)

    def test_comparison_score_and_attribution_validated_not_fabricated(self):
        source = assessment_helpers.prepare_assessment_source(state_fixture(), KEY)
        comparison = source['data']['backlog']['comparisons'][1]
        comparison['assessment']['inputs']['time_criticality'] = 4
        self.assertRaises(IdeaError, assessment_helpers.validate_data, source['data'], KEY)
        comparison['assessment']['score'] = 9
        assessment_helpers.validate_data(source['data'], KEY)
        comparison['assessment']['actor'] = False
        self.assertRaises(IdeaError, assessment_helpers.validate_data, source['data'], KEY)
        comparison['assessment']['actor'] = 'fixture'
        comparison['ratings']['actor'] = ''
        self.assertRaises(IdeaError, assessment_helpers.validate_data, source['data'], KEY)

    def test_insertions_first_middle_last_single_and_no_mutation(self):
        order = [OTHER, KEY, THIRD]; before = list(order)
        expected = [dict(before=None, after=OTHER), dict(before=OTHER, after=THIRD),
                    dict(before=THIRD, after=None)]
        for position, neighbors in enumerate(expected, 1):
            self.assertEqual(assessment_helpers.insertion_neighbors(order, KEY, position), neighbors)
        self.assertEqual(order, before)
        self.assertEqual(assessment_helpers.insertion_neighbors([KEY], KEY, 1), dict(before=None, after=None))
        for position in (0, 4, True, 1.5):
            self.assertRaises(IdeaError, assessment_helpers.insertion_neighbors, order, KEY, position)
        self.assertRaises(IdeaError, assessment_helpers.insertion_neighbors, [KEY, KEY], KEY, 1)

    def test_agent_original_neighbors_and_no_operator_override(self):
        source = assessment_helpers.prepare_assessment_source(state_fixture(), KEY)
        proposal = proposed(source, position=1)
        result = assessment_helpers.validate_assessment_proposal(proposal, source=source, idea_id=KEY)
        result['assessment']['basis'] = 'Detached'
        self.assertNotEqual(result, proposal)
        wrong = copy.deepcopy(proposal); wrong['position']['neighbors']['after'] = THIRD
        self.assert_code('stale_backlog', lambda: assessment_helpers.validate_assessment_proposal(wrong, source=source, idea_id=KEY))
        for change in ('actual', 'reason'):
            wrong = copy.deepcopy(proposal)
            if change == 'actual': wrong['position']['actual_position'] = 2; wrong['position']['override_reason'] = 'Agent override'
            else: wrong['position']['override_reason'] = 'Agent override'
            self.assert_code('invalid_proposal', lambda: assessment_helpers.validate_assessment_proposal(wrong, source=source, idea_id=KEY))
        self.assertRaises(IdeaError, assessment_helpers.validate_assessment_proposal, proposal, source=source)

    def test_human_override_requires_reason_and_keeps_original_proposed_position(self):
        source = assessment_helpers.prepare_assessment_source(state_fixture(), KEY)
        original = proposed(source, position=1)
        human = copy.deepcopy(original['position'])
        human.update(actual_position=3, neighbors=dict(before=THIRD, after=None), override_reason='Operator investment boundary')
        before = copy.deepcopy((human, original))
        checked = assessment_helpers.validate_actual_position(human, [OTHER, KEY, THIRD], KEY, original_proposal=original)
        self.assertEqual(checked, human)
        self.assertEqual((human, original), before)
        checked['override_reason'] = 'Detached'
        for reason in (None, '', '  '):
            wrong = dict(human, override_reason=reason)
            self.assert_code('not_ready', lambda: assessment_helpers.validate_actual_position(wrong, [OTHER, KEY, THIRD], KEY, original_proposal=original))
        wrong = dict(human, proposed_position=2)
        self.assert_code('proposal_mismatch', lambda: assessment_helpers.validate_actual_position(wrong, [OTHER, KEY, THIRD], KEY, original_proposal=original))

    def test_actual_neighbor_drift_and_bad_neighbor_schema_refused(self):
        human = proposed(position=2)['position']
        self.assert_code('stale_backlog', lambda: assessment_helpers.validate_actual_position(human, [THIRD, KEY, OTHER], KEY))
        for neighbors in ({'before': OTHER}, {'before': KEY, 'after': THIRD},
                          {'before': OTHER, 'after': OTHER}):
            wrong = dict(human, neighbors=neighbors)
            self.assertRaises(IdeaError, assessment_helpers.validate_actual_position, wrong, [OTHER, KEY, THIRD], KEY)

    def test_archived_comparisons_preserved_and_order_never_sorted(self):
        state = state_fixture(); state['ideas'][OTHER]['status'] = 'archived'
        before = copy.deepcopy(state)
        result = assessment_helpers.backlog_projection(state, KEY)
        self.assertEqual(result['comparisons'][0]['status'], 'archived')
        self.assertIsNone(result['comparisons'][0]['ratings'])
        self.assertIsNone(result['comparisons'][0]['assessment'])
        self.assertEqual(state, before)
        state['ideas'].pop(THIRD)
        self.assertRaises(IdeaError, assessment_helpers.backlog_projection, state, KEY)

    def test_unicode_tree_and_size_limits_never_silently_trim_sources(self):
        source = assessment_helpers.prepare_assessment_source(state_fixture(), KEY)
        wrong = copy.deepcopy(source)
        wrong['data']['steps']['capture']['raw_text'] = '\ud800'
        self.assertRaises(IdeaError, assessment_helpers.validate_assessment_source, wrong)
        wrong = copy.deepcopy(source)
        wrong['data']['steps']['capture']['raw_text'] = 'x'*(1024*1024)
        self.assert_code('too_large', lambda: assessment_helpers.validate_assessment_source(wrong))
        wrong = copy.deepcopy(source)
        nested = []; wrong['data']['target'] = nested
        for _ in range(30):
            child = []; nested.append(child); nested = child
        self.assert_code('too_large', lambda: assessment_helpers.validate_assessment_source(wrong))


class AssessmentHandlerTests(unittest.TestCase):
    assert_code = AssessmentHelperTests.assert_code

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = storage.Store(Path(self.temp.name)/'ideas', observer='Operator')
        self.sid = self.store.create_session()
        self.context = TrustedContext('Operator', self.sid, KEY)
        with self.store.transaction(write=True) as state:
            state.update(state_fixture()); self.store.commit(state)
        self.service = Service(self.store, {}, self.context, handlers={'assess': assess_handler.HANDLER})
        self.live = dict(binding_id='binding_'+'6'*32, generation='agent_'+'7'*32,
                         session_id=self.sid, agent_status='connected')

    def domain(self):
        with self.store.transaction() as state:
            return copy.deepcopy(state)

    def payload(self, value=None, request='handler-assess-1', proposal_id=None):
        state = self.domain(); idea = state['ideas'][KEY]
        return dict(request_id=request, idea_id=KEY, step='assess', fields=proposed() if value is None else value,
            expected_revision=idea['revision'], expected_draft_version=idea['workflow']['draft_version'],
            proposal_id=proposal_id, expected_backlog_revision=state['backlog_revision'])

    def linked(self, value=None):
        adapter = agent_source.SourceAdapter(self.store)
        live = self.live
        class Provider:
            def context(provider, context):
                return copy.deepcopy(live)
            def project(provider, state, idea, context, observed):
                return dict(agent_status='connected', agent_generation=observed['generation'],
                    resume=dict(required=False, reason=None), capabilities=dict(agent=True, memory=True),
                    proposal_sources=agent_source.project_sources(state, KEY),
                    **adapter.project_projection(state, KEY, observed))
            def validate_acceptance(provider, state, idea, payload, source, context, observed):
                return adapter.validate_acceptance(state, idea, payload, source, context, live_binding=observed)
        self.service = Service(self.store, {}, self.context, handlers={'assess': assess_handler.HANDLER},
                               agent_provider=Provider())
        with self.store.transaction(write=True) as state:
            selected = agent_source.prepare_source(state, KEY, 'assessment')
            proposal = proposed(selected) if value is None else value
            correlation = dict(request_id='handler-agent-1', session_id=self.sid, idea_id=KEY, operation='assessment',
                accepted_revision=selected['accepted_revision'], draft_version=selected['draft_version'],
                source_digest=agent_source.proposal_source_digest('assessment', selected, idea_id=KEY))
            supplied = dict(binding_id=live['binding_id'], generation=live['generation'],
                            correlation=correlation, source=selected, proposal=proposal)
            link = self.store.persist_agent_proposal(state, supplied, actor=self.context.actor,
                                                     validate_current=agent_source.validate_current)
        return link

    def test_registry_exports_fixed_handler_and_no_routes(self):
        handlers, routes = load_registry()
        self.assertIs(handlers['assess'], assess_handler.HANDLER)
        self.assertEqual(assess_handler.ROUTES, ())
        self.assertNotIn('assessment-backlog', routes)

    def test_manual_exact_decimal_formulas_unknowns_and_kano_with_single_snapshots(self):
        cases = [
            ('wsjf', dict(value=6, time_criticality=3, enablement=1, effort=2), 5),
            ('wsjf', dict(value=0.1, time_criticality=0.2, enablement=0.3, effort=0.2), 3.0000000000000004),
            ('rice', dict(reach=1.25, impact=2.5, confidence=0.8, effort=0.5), 5),
            ('wsjf', dict(value=6, time_criticality=None, enablement=1, effort=2), None),
            ('rice', dict(reach=None, impact=2, confidence=0.5, effort=4), None),
            ('kano', dict(category='delighter', hypothesis=True), None),
            ('kano', dict(category='must-be', hypothesis=False), None),
        ]
        ratings = self.domain()['ideas'][KEY]['ratings']
        for index, (method, inputs, score) in enumerate(cases):
            value = proposed(); value['assessment'].update(method=method, inputs=inputs)
            before = self.domain(); payload = self.payload(value, 'handler-formula-'+str(index))
            result = self.service.accept(payload); state = self.domain(); idea = state['ideas'][KEY]
            self.assertEqual(idea['assessments'][-1]['score'], score)
            self.assertEqual(idea['assessments'][-1]['actor'], self.context.actor)
            self.assertEqual(idea['ratings'], ratings)
            self.assertEqual(len(idea['assessments']), len(before['ideas'][KEY]['assessments'])+1)
            self.assertEqual(state['backlog_revision'], before['backlog_revision']+1)
            self.assertEqual(len(state['placements']), len(before['placements'])+1)
            placement = state['placements'][-1]
            self.assertEqual(placement['idea_revision'], result['revision'])
            self.assertEqual(placement['snapshot'], dict(ratings=ratings, assessments=idea['assessments']))
            receipt = idea['workflow']['steps']['assess']['acceptance']
            self.assertEqual((placement['actor'], placement['timestamp']), (receipt['actor'], receipt['timestamp']))
            self.assertEqual(derive_state(idea)['steps']['assess']['status'], 'saved')
            self.assertNotEqual(derive_state(idea)['steps']['review']['status'], 'review-needed')

    def test_linked_edited_model_override_keeps_evidence_ratings_and_original_position(self):
        link = self.linked()
        immutable = (self.store.path/link['path']).read_bytes()
        original = self.domain()
        edited = proposed()
        edited['assessment'].update(method='rice', basis='Human revised inputs',
                                   inputs=dict(reach=20, impact=2, confidence=0.5, effort=2))
        edited['position'].update(actual_position=3, neighbors=dict(before=THIRD, after=None),
                                  override_reason='Human investment boundary')
        draft = self.payload(edited, 'handler-edited-draft')
        del draft['proposal_id']; del draft['expected_backlog_revision']
        self.service.draft(draft)
        payload = self.payload(edited, 'handler-edited-accept', link['proposal_id'])
        result = self.service.accept(payload); state = self.domain()
        self.assertEqual(state['order'], [OTHER, THIRD, KEY])
        self.assertEqual(state['ideas'][KEY]['assessments'][-1]['score'], 10)
        self.assertEqual(state['ideas'][KEY]['ratings'], original['ideas'][KEY]['ratings'])
        self.assertEqual(state['ideas'][KEY]['workflow']['steps']['assess']['fields']['position']['proposed_position'], 2)
        self.assertEqual(state['placements'][-1]['reason'], 'Human investment boundary')
        self.assertEqual((self.store.path/link['path']).read_bytes(), immutable)
        self.assertEqual(self.service.accept(payload), result)
        self.assertEqual(self.domain(), state)

    def test_stale_backlog_neighbors_scores_and_override_refuse_without_mutation(self):
        before = self.domain()
        stale = self.payload(); stale['expected_backlog_revision'] -= 1
        self.assert_code('stale_backlog', lambda: self.service.accept(stale))
        wrong = proposed(); wrong['position']['neighbors']['before'] = None
        self.assert_code('stale_backlog', lambda: self.service.accept(self.payload(wrong, 'bad-neighbor')))
        wrong = proposed(); wrong['position']['neighbors']['before'] = THIRD
        self.assert_code('invalid_input', lambda: self.service.accept(self.payload(wrong, 'duplicate-neighbor')))
        wrong = proposed(); wrong['assessment']['score'] = 500
        self.assert_code('invalid_input', lambda: self.service.accept(self.payload(wrong, 'supplied-score')))
        wrong = proposed(); wrong['position'].update(actual_position=3, neighbors=dict(before=THIRD, after=None))
        self.assert_code('not_ready', lambda: self.service.accept(self.payload(wrong, 'unexplained-override')))
        self.assertEqual(self.domain(), before)

    def test_linked_stale_compared_revision_and_changed_original_position_refuse(self):
        link = self.linked()
        altered = proposed(); altered['position'].update(proposed_position=3, actual_position=3,
                                                        neighbors=dict(before=THIRD, after=None))
        self.assert_code('proposal_mismatch', lambda: self.service.accept(self.payload(altered,
            'changed-original-insertion', link['proposal_id'])))
        with self.store.transaction(write=True) as state:
            other = state['ideas'][OTHER]
            other['revision'] += 1
            other['revisions'].append(snapshot(other, 'Operator', 'comparison-observed'))
            self.store.commit(state)
        before = self.domain()
        self.assert_code('stale_source', lambda: self.service.accept(self.payload(proposed(),
            'changed-compared-revision', link['proposal_id'])))
        self.assertEqual(self.domain(), before)

    def test_replay_restart_noop_and_backlog_history_do_not_duplicate_acceptance(self):
        payload = self.payload(); result = self.service.accept(payload)
        after = self.domain()
        self.assertTrue((self.store.path/'history/backlog/r1.md').is_file())
        self.assertTrue((self.store.path/'history'/KEY/('r'+str(payload['expected_revision'])+'.md')).is_file())
        restarted = Service(storage.Store(self.store.path), {}, TrustedContext('Operator', self.sid, KEY))
        self.assertEqual(restarted.accept(payload), result)
        self.assertEqual(self.domain(), after)
        no_op = self.service.accept(self.payload(request='handler-noop'))
        self.assertEqual(no_op['write_state'], 'no_op')
        self.assertEqual(self.domain(), after)

    def test_reorder_invalidates_active_neighbor_without_draft_and_preserves_archived_branch(self):
        with self.store.transaction(write=True) as state:
            other = state['ideas'][OTHER]
            for step in ('capture', 'priorities', 'shape', 'method', 'visualize', 'assess'):
                other = accept(other, step)['idea']
            state['ideas'][OTHER] = other; self.store.commit(state)
        before = self.domain()
        value = proposed(position=1)
        payload = self.payload(value)
        self.service.accept(payload)
        state = self.domain(); other = state['ideas'][OTHER]
        self.assertEqual(other['revision'], before['ideas'][OTHER]['revision']+1)
        self.assertEqual(other['workflow']['drafts'], before['ideas'][OTHER]['workflow']['drafts'])
        self.assertEqual(other['workflow']['steps']['assess']['acceptance'],
                         before['ideas'][OTHER]['workflow']['steps']['assess']['acceptance'])
        self.assertEqual(derive_state(other)['steps']['assess']['status'], 'review-needed')
        self.assertEqual(derive_state(state['ideas'][KEY])['steps']['assess']['status'], 'saved')
        # Pure archived historical branch, without inventing a live Store archive.
        detached = copy.deepcopy(before); detached['ideas'][OTHER]['status'] = 'archived'
        frozen = copy.deepcopy(detached['ideas'][OTHER])
        idea = detached['ideas'][KEY]; source = acceptance_source(idea, 'assess', value)
        canonical = accept(idea, 'assess', value)['idea']; detached['ideas'][KEY] = canonical
        assess_handler.apply(detached, canonical, payload, source, self.context)
        self.assertEqual(detached['ideas'][OTHER], frozen)

    def test_handler_callbacks_are_memory_only_and_validate_inputs_without_mutation(self):
        state = self.domain(); idea = state['ideas'][KEY]; payload = self.payload()
        source = acceptance_source(idea, 'assess', payload['fields']); before = copy.deepcopy(state)
        with (patch.object(storage, 'read_bytes', side_effect=AssertionError('Handler read')),
              patch.object(self.store, 'transaction', side_effect=AssertionError('Nested transaction'))):
            assess_handler.validate(state, idea, payload, source, self.context)
            self.assertEqual(state, before)
            canonical = accept(idea, 'assess', payload['fields'])['idea']; state['ideas'][KEY] = canonical
            assess_handler.apply(state, canonical, payload, source, self.context)
        self.assertEqual(state['order'], before['order'])
        self.assertEqual(len(state['placements']), 1)
        archived = copy.deepcopy(before); archived['ideas'][KEY]['status'] = 'archived'
        self.assert_code('archived_revision', lambda: assess_handler.validate(archived, archived['ideas'][KEY],
            payload, source, self.context))


if __name__ == '__main__':
    unittest.main()
