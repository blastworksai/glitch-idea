"""Discovery, Exploration and the one order setting for Methods and Discovery."""
import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
import idea_workflow
from idea_domain import IdeaError
from idea_workflow import (DEPENDENCIES, DISCOVERY_BEFORE_METHODS, STEP_FIELDS, STEP_ORDER, WORKFLOW_VERSION,
                           derive_dependencies, derive_state, derive_step_order, empty_workflow,
                           import_workflow, step_requirements, validate_step_fields, validate_workflow)
from test_workflow import accept, captured, complete, fields, original_idea

OLD_MESSAGE = 'This idea was made with an older glitch-idea. Capture it again.'
APPETITE = {'cap': 2, 'unit': 'sessions', 'boundary': 'Fixture only'}
EXPERIMENT = {'question': 'Will this fit?', 'evidence': 'Measure fit',
              'success_criterion': 'Fits one lid', 'stop_rule': 'Stop after one test'}


class Base(unittest.TestCase):
    def assert_error(self, code, operation):
        with self.assertRaises(IdeaError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)


class FieldSetTests(Base):
    def test_version_and_step_field_sets(self):
        self.assertEqual(WORKFLOW_VERSION, 3)
        self.assertEqual(STEP_FIELDS['discovery'],
                         {'problem', 'audience', 'workaround', 'evidence', 'kill_criteria', 'challenges',
                          'prior_art', 'prior_art_none', 'prior_art_searched'})
        self.assertEqual(STEP_FIELDS['exploration'],
                         {'outcome', 'alternatives', 'assumptions', 'scope', 'scope_reason', 'next_slice',
                          'learning', 'investment', 'experiment', 'sketch'})
        self.assertEqual(STEP_FIELDS['method'], {'selection', 'reason', 'memory'})
        self.assertNotIn('shape', STEP_FIELDS)
        self.assertNotIn('shape', STEP_ORDER)

    def test_method_reason_is_optional(self):
        value = fields()['method']
        value['reason'] = None
        self.assertEqual(step_requirements('method', value), ())
        value['reason'] = '   '
        self.assertEqual(step_requirements('method', value), ())


class DiscoveryTests(Base):
    def test_each_missing_piece_refuses_acceptance(self):
        for key in ('problem', 'audience', 'workaround', 'evidence', 'kill_criteria'):
            for blank in (None, '', '  '):
                value = fields()['discovery']
                value[key] = blank
                with self.subTest(key=key, blank=blank):
                    self.assertIn(key, step_requirements('discovery', value))
                    self.assert_error('not_ready', lambda: validate_step_fields('discovery', value))
        value = fields()['discovery']
        value['challenges'] = []
        self.assertIn('challenges', step_requirements('discovery', value))
        value['challenges'] = [{'challenge': 'Real pain?', 'response': ' '}]
        self.assertIn('challenges.0.response', step_requirements('discovery', value))
        value['challenges'] = [{'challenge': '', 'response': 'Yes'}]
        self.assertIn('challenges.0.challenge', step_requirements('discovery', value))
        self.assertEqual(step_requirements('discovery', fields()['discovery']), ())

    def test_draft_shapes_are_strict(self):
        self.assert_error('invalid_input', lambda: validate_step_fields(
            'discovery', {'challenges': [{'challenge': 'x', 'response': 'y', 'extra': 1}]}, partial=True))
        self.assert_error('invalid_input', lambda: validate_step_fields('discovery', {'problem': 1}, partial=True))


PRIOR_KEYS = ('prior_art', 'prior_art_none', 'prior_art_searched')


def old_discovery():
    value = fields()['discovery']
    for key in PRIOR_KEYS:
        del value[key]
    return value


def none_discovery():
    value = fields()['discovery']
    value.update(prior_art=[], prior_art_none=True, prior_art_searched='Web search and GitHub topics')
    return value


class PriorArtTests(Base):
    def test_row_path_accepted(self):
        value = fields()['discovery']
        self.assertEqual(step_requirements('discovery', value), ())
        self.assertEqual(validate_step_fields('discovery', value), value)

    def test_link_and_does_may_be_empty_licence_not_stated_counts(self):
        value = fields()['discovery']
        value['prior_art'] = [{'name': 'Thing', 'link': '', 'does': '', 'differs': 'Smaller', 'licence': 'Not stated'}]
        self.assertEqual(step_requirements('discovery', value), ())

    def test_none_path_accepted(self):
        self.assertEqual(step_requirements('discovery', none_discovery()), ())
        validate_step_fields('discovery', none_discovery())

    def test_none_with_rows_refused(self):
        value = fields()['discovery']
        value['prior_art_none'] = True
        value['prior_art_searched'] = 'Web'
        self.assertTrue(step_requirements('discovery', value))
        self.assert_error('not_ready', lambda: validate_step_fields('discovery', value))

    def test_none_needs_searched_text(self):
        value = none_discovery()
        value['prior_art_searched'] = '  '
        self.assertIn('prior_art_searched', step_requirements('discovery', value))

    def test_null_none_flag_is_not_accepted_as_the_rows_route(self):
        # Review PR #2 (P2): a present JSON null for prior_art_none must not pass as "rows"; the page mirror requires a boolean.
        value = fields()['discovery']
        value['prior_art_none'] = None
        self.assertIn('prior_art_none', step_requirements('discovery', value))
        self.assert_error('not_ready', lambda: validate_step_fields('discovery', value))

    def test_null_rows_are_not_accepted_as_the_none_route(self):
        value = none_discovery()
        value['prior_art'] = None
        self.assertIn('prior_art', step_requirements('discovery', value))
        self.assert_error('not_ready', lambda: validate_step_fields('discovery', value))

    def test_neither_rows_nor_none_refused(self):
        value = fields()['discovery']
        value['prior_art'] = []
        self.assertIn('prior_art', step_requirements('discovery', value))
        value['prior_art_none'] = True
        value['prior_art_searched'] = ''
        self.assertIn('prior_art_searched', step_requirements('discovery', value))

    def test_row_missing_name_differs_or_licence_refused(self):
        for key in ('name', 'differs', 'licence'):
            value = fields()['discovery']
            value['prior_art'][0][key] = ' '
            with self.subTest(key=key):
                self.assertIn('prior_art.0.' + key, step_requirements('discovery', value))
                self.assert_error('not_ready', lambda: validate_step_fields('discovery', value))

    def test_more_than_eight_rows_refused(self):
        value = fields()['discovery']
        value['prior_art'] = [dict(value['prior_art'][0]) for _ in range(9)]
        self.assert_error('invalid_input', lambda: validate_step_fields('discovery', value))
        value['prior_art'] = value['prior_art'][:8]
        validate_step_fields('discovery', value)

    def test_unknown_or_missing_row_key_refused(self):
        row = fields()['discovery']['prior_art'][0]
        self.assert_error('invalid_input', lambda: validate_step_fields(
            'discovery', {'prior_art': [dict(row, extra='x')]}, partial=True))
        short = dict(row)
        del short['licence']
        self.assert_error('invalid_input', lambda: validate_step_fields('discovery', {'prior_art': [short]}, partial=True))
        self.assert_error('invalid_input', lambda: validate_step_fields(
            'discovery', {'prior_art': [dict(row, link=3)]}, partial=True))
        self.assert_error('invalid_input', lambda: validate_step_fields('discovery', {'prior_art_none': 'yes'}, partial=True))


class OldDiscoveryTests(Base):
    """A Discovery accepted before CP6p stays accepted and readable, with no version bump."""

    def old_idea(self):
        idea = complete()
        stored = idea['workflow']['steps']['discovery']['fields']
        for key in PRIOR_KEYS:
            del stored[key]
        return idea

    def test_old_record_loads_via_validate_workflow(self):
        idea = self.old_idea()
        validate_workflow(idea['workflow'])
        self.assertEqual(idea['workflow']['schema_version'], 3)

    def test_malformed_stored_prior_art_still_refused(self):
        for bad in ([{'name': 'x'}], 'text', [{'name': 'a', 'link': '', 'does': '', 'differs': 'b', 'licence': 'c', 'z': 1}]):
            idea = complete()
            idea['workflow']['steps']['discovery']['fields']['prior_art'] = bad
            with self.subTest(bad=bad):
                self.assert_error('invalid_input', lambda: validate_workflow(idea['workflow']))

    def test_old_record_loads_in_assess_source_and_agent_source(self):
        import idea_assessment
        import idea_agent_source
        old = fields()
        old['discovery'] = old_discovery()
        for step in idea_agent_source.INPUTS['exploration']:
            self.assertIn(step, old)
        idea_agent_source._data('exploration', {s: old[s] for s in idea_agent_source.INPUTS['exploration']})
        for step in idea_assessment.CONSUMED_STEPS:
            idea_workflow.validate_step_fields(step, old[step], legacy=True)
        with self.assertRaises(IdeaError):
            validate_step_fields('discovery', old['discovery'])

    def test_old_assess_source_data_validates(self):
        import idea_assessment
        from test_assessment import KEY, state_fixture
        state = state_fixture()
        source = idea_assessment.prepare_assessment_source(state, KEY)
        data = source['data']
        for key in PRIOR_KEYS:
            data['steps']['discovery'].pop(key, None)
        idea_assessment.validate_data(data, KEY)

    def test_reaccepting_old_discovery_requires_prior_art(self):
        idea = self.old_idea()
        missing = step_requirements('discovery', old_discovery())
        for key in PRIOR_KEYS:
            self.assertIn(key, missing)
        with self.assertRaises(IdeaError):
            accept(idea, 'discovery', old_discovery())
        self.assertEqual(missing.count('prior_art'), 1)


class PriorArtExportTests(Base):
    def sections(self, discovery_fields):
        import idea_markdown
        idea = complete()
        idea['workflow']['steps']['discovery']['fields'] = discovery_fields
        return '\n'.join(idea_markdown._workflow_sections(idea['workflow']))

    def test_rows_export(self):
        text = self.sections(fields()['discovery'])
        for expected in ('Comparable 1: Lid brush', 'How we differ 1: Ours needs no scrubbing', 'Licence 1: Not stated'):
            self.assertIn(expected, text)

    def test_none_exports_where_it_looked(self):
        self.assertIn('Nothing comparable found — looked: Web search and GitHub topics', self.sections(none_discovery()))

    def test_old_accepted_discovery_exports_not_checked(self):
        self.assertIn('Does it already exist: Not checked', self.sections(old_discovery()))

    def test_prompt_helper_carries_prior_art_and_marks_old(self):
        from idea_handoff_evidence import _discovery_for_prompt
        self.assertEqual(_discovery_for_prompt(fields()['discovery']), fields()['discovery'])
        self.assertEqual(_discovery_for_prompt(old_discovery())['prior_art'], 'Not checked')


class ExplorationTests(Base):
    def test_each_missing_piece_refuses_acceptance(self):
        for key in ('outcome', 'scope_reason', 'next_slice'):
            value = fields()['exploration']
            value[key] = ' '
            self.assertIn(key, step_requirements('exploration', value))
        value = fields()['exploration']
        value['scope'] = None
        self.assertIn('scope', step_requirements('exploration', value))
        value = fields()['exploration']
        value['alternatives'] = []
        self.assertIn('alternatives', step_requirements('exploration', value))
        value = fields()['exploration']
        value['sketch'] = []
        self.assertIn('sketch', step_requirements('exploration', value))
        self.assert_error('not_ready', lambda: validate_step_fields('exploration', value))

    def test_sketch_items_need_title_and_done_when(self):
        for key in ('title', 'done_when'):
            value = fields()['exploration']
            value['sketch'][0][key] = ' '
            self.assertIn('sketch.0.'+key, step_requirements('exploration', value))
        value = fields()['exploration']
        value['sketch'][0]['why_next'] = None
        self.assertEqual(step_requirements('exploration', value), ())

    def test_sketch_is_capped_at_five(self):
        item = fields()['exploration']['sketch'][0]
        five = [dict(item, title='Item '+str(i)) for i in range(5)]
        self.assertEqual(validate_step_fields('exploration', {'sketch': five}, partial=True), {'sketch': five})
        self.assert_error('invalid_input', lambda: validate_step_fields(
            'exploration', {'sketch': five + [item]}, partial=True))
        value = fields()['exploration']
        value['sketch'] = five + [item]
        self.assert_error('invalid_input', lambda: validate_step_fields('exploration', value))

    def test_sketch_inner_method_is_a_method_or_null(self):
        value = fields()['exploration']
        for method in (None, 'bounded-plan', 'adaptive-slices', 'appetite-led', 'experiment-led'):
            value['sketch'][0]['method'] = method
            self.assertEqual(validate_step_fields('exploration', value)['sketch'][0]['method'], method)
        for bad in ('overall', 'shape', 3, True):
            value['sketch'][0]['method'] = bad
            with self.subTest(bad=bad):
                self.assert_error('invalid_input', lambda: validate_step_fields('exploration', value, partial=True))
        value['sketch'][0] = {'title': 'x', 'why_next': 'y', 'done_when': 'z', 'method': None, 'shell': 1}
        self.assert_error('invalid_input', lambda: validate_step_fields('exploration', value, partial=True))

    def test_investment_and_experiment_follow_the_accepted_method(self):
        base = fields()['exploration']
        for selected in ('bounded-plan', 'adaptive-slices', 'appetite-led', 'experiment-led'):
            value = copy.deepcopy(base)
            if selected == 'appetite-led':
                self.assertIn('investment', step_requirements('exploration', value, selected))
                value['investment'] = dict(APPETITE)
            if selected == 'experiment-led':
                self.assertIn('experiment', step_requirements('exploration', value, selected))
                value['experiment'] = dict(EXPERIMENT)
            with self.subTest(method=selected):
                self.assertEqual(step_requirements('exploration', value, selected), ())
                if selected == 'appetite-led':
                    value['investment']['boundary'] = ' '
                elif selected == 'experiment-led':
                    value['experiment']['stop_rule'] = None
                else:
                    value['investment'] = dict(APPETITE)
                self.assertTrue(step_requirements('exploration', value, selected))
                self.assert_error('not_ready', lambda: validate_step_fields('exploration', value, method_selection=selected))

    def test_acceptance_reads_the_accepted_method_from_the_workflow(self):
        idea = captured()
        for step in ('priorities', 'method', 'discovery'):
            idea = accept(idea, step)['idea']
        self.assert_error('not_ready', lambda: accept(idea, 'exploration', dict(fields()['exploration'], investment=dict(APPETITE))))
        appetite = fields()['method']
        appetite['selection'] = 'appetite-led'
        idea = accept(idea, 'method', appetite)['idea']
        idea = accept(idea, 'discovery')['idea']
        self.assert_error('not_ready', lambda: accept(idea, 'exploration'))
        accepted = accept(idea, 'exploration', dict(fields()['exploration'], investment=dict(APPETITE)))['idea']
        self.assertEqual(derive_state(accepted)['steps']['exploration']['status'], 'saved')
        # Changing the method later keeps the stale exploration readable.
        changed = accept(accepted, 'method', fields()['method'])['idea']
        self.assertEqual(derive_state(changed)['steps']['exploration']['status'], 'review-needed')
        validate_workflow(changed['workflow'])


class OrderSettingTests(Base):
    def test_default_is_methods_before_discovery(self):
        self.assertIs(DISCOVERY_BEFORE_METHODS, False)
        self.assertEqual(STEP_ORDER, ('capture', 'priorities', 'method', 'discovery', 'exploration',
                                      'visualize', 'assess', 'review'))
        self.assertEqual(DEPENDENCIES, derive_dependencies(False))

    def test_flag_swaps_only_method_and_discovery(self):
        off, on = derive_step_order(False), derive_step_order(True)
        self.assertEqual(on, ('capture', 'priorities', 'discovery', 'method', 'exploration',
                              'visualize', 'assess', 'review'))
        self.assertEqual([k for k in range(8) if off[k] != on[k]], [2, 3])

    def test_dependency_tables_for_both_values(self):
        common = {'capture': (), 'priorities': (), 'method': ('capture', 'priorities'),
                  'visualize': ('capture', 'discovery', 'exploration'),
                  'assess': ('capture', 'priorities', 'discovery', 'exploration')}
        for flag, discovery in ((False, ('capture', 'priorities', 'method')), (True, ('capture', 'priorities'))):
            with self.subTest(discovery_first=flag):
                table = derive_dependencies(flag)
                self.assertEqual(set(table), set(derive_step_order(flag)))
                for step, sources in common.items():
                    self.assertEqual(table[step], sources)
                self.assertEqual(table['discovery'], discovery)
                self.assertEqual(set(table['exploration']), {'discovery', 'method'})
                self.assertEqual(set(table['review']), set(derive_step_order(flag)[:-1]))

    def test_every_dependency_points_earlier_in_both_orders(self):
        for flag in (False, True):
            order = derive_step_order(flag)
            for step, sources in derive_dependencies(flag).items():
                for source in sources:
                    with self.subTest(flag=flag, step=step, source=source):
                        self.assertLess(order.index(source), order.index(step))
            self.assertGreater(order.index('exploration'), max(order.index('method'), order.index('discovery')))

    def test_workflow_walks_end_to_end_with_discovery_first(self):
        order = derive_step_order(True)
        with mock.patch.object(idea_workflow, 'STEP_ORDER', order), \
             mock.patch.object(idea_workflow, 'DEPENDENCIES', derive_dependencies(True)):
            idea = captured()
            self.assertEqual(list(idea['workflow']['steps']), list(order))
            for step in order[1:-1]:
                idea = accept(idea, step)['idea']
            view = derive_state(idea)
            self.assertTrue(all(view['steps'][s]['status'] in ('saved', 'skipped') for s in order[:-1]))
            self.assertEqual(idea['workflow']['current_step'], 'review')

    def test_step_modules_follow_the_derived_order(self):
        from idea_steps import STEP_MODULES
        self.assertEqual([step for step, _ in STEP_MODULES],
                         [s for s in STEP_ORDER if s in ('method', 'discovery', 'exploration', 'visualize', 'assess')])
        from idea_steps import load_registry
        handlers, _ = load_registry()
        self.assertTrue({'method', 'discovery', 'exploration'} <= set(handlers))
        self.assertNotIn('shape', handlers)


class OlderIdeaTests(Base):
    def test_version_two_workflow_is_refused_with_the_exact_code(self):
        old = empty_workflow()
        old['schema_version'] = 2
        with self.assertRaises(IdeaError) as caught:
            validate_workflow(old)
        self.assertEqual(caught.exception.code, 'unsupported_idea_version')
        self.assertEqual(str(caught.exception), OLD_MESSAGE)
        idea = captured()
        idea['workflow']['schema_version'] = 2
        with self.assertRaises(IdeaError) as caught:
            import_workflow(idea)
        self.assertEqual(caught.exception.code, 'unsupported_idea_version')
        self.assertEqual(str(caught.exception), OLD_MESSAGE)

    def test_other_versions_stay_generically_unsupported(self):
        for bad in (1, 4, True, '3'):
            old = empty_workflow()
            old['schema_version'] = bad
            with self.assertRaises(IdeaError) as caught:
                validate_workflow(old)
            self.assertNotEqual(caught.exception.code, 'unsupported_idea_version')

    def test_workflowless_import_drafts_capture_and_ratings_but_never_shape_or_method(self):
        legacy = original_idea()
        legacy['shape'] = {'outcome': 'Old', 'method': 'bounded-plan', 'method_reason': 'Old reason'}
        legacy['ratings'] = {'urgency': 7, 'importance': 8, 'actor': 'operator'}
        result = import_workflow(legacy)
        drafts = result['workflow']['drafts']
        self.assertEqual(result['workflow']['schema_version'], 3)
        self.assertEqual(drafts['capture'], {'raw_text': legacy['origin']['text']})
        self.assertEqual(drafts['priorities'], {'urgency': 7, 'importance': 8})
        for step in ('method', 'discovery', 'exploration'):
            self.assertNotIn(step, drafts)
        self.assertTrue(all(r['acceptance'] is None for r in result['workflow']['steps'].values()))
        self.assertEqual(result['shape'], legacy['shape'])


if __name__ == '__main__':
    unittest.main()
