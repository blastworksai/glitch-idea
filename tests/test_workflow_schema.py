"""Frozen all-step schema and historical evidence tests. Author: Operator."""
import copy
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
from idea_domain import IdeaError, SHAPE_KEYS, assessment, encoded
from idea_workflow import (STEP_ORDER, STEP_FIELDS, adapt_snapshot, empty_workflow,
                           step_requirements, validate_acceptance,
                           validate_snapshot, validate_step_fields, validate_workflow)


def assessment_fields(method='wsjf'):
    inputs = {'value': 3, 'time_criticality': None, 'enablement': 2, 'effort': 1}
    if method == 'rice':
        inputs = {'reach': 100, 'impact': 2, 'confidence': .5, 'effort': 4}
    if method == 'kano':
        inputs = {'category': 'delighter', 'hypothesis': True}
    return dict(method=method, version='fixture-v1', inputs=inputs,
                basis='Fixture estimates', assumptions=[], confidence='low', provenance='Current-agent proposal')


def fixtures():
    return {
        'capture': {'raw_text': '  Café 💡\r\n\n', 'workspace': {'name': 'fixture', 'path': '/example/fixture', 'confirmed': True}},
        'priorities': {'urgency': 7, 'importance': 8},
        'shape': {'outcome': 'Easier cleaning', 'scope': 'small-change', 'scope_reason': 'One lid',
                  'alternatives': [{'route': 'Clean existing lid', 'reason': 'Simpler'}],
                  'assumptions': [], 'next_slice': 'Check lid', 'learning': []},
        'method': {'selection': 'bounded-plan', 'reason': 'Known change', 'investment': None,
                   'experiment': None, 'memory': {'status': 'unavailable', 'sources': [], 'rationale': None}},
        'visualize': {'disposition': 'skipped', 'reason': 'No design needed', 'design_set_id': None, 'brief_evidence_id': None},
        'assess': {'assessment': assessment_fields(), 'position': {'proposed_position': 1, 'actual_position': 1,
                    'neighbors': {'before': None, 'after': None}, 'override_reason': None}},
        'review': {'handoff_id': 'handoff-fixture', 'source_revision': 7},
    }


def receipt(revision=2):
    return {'accepted_revision': revision, 'evidence_id': 'receipt-fixture', 'source_revision': 1,
            'source_digest': 'a'*64, 'dependencies': {'capture': {'revision': 1, 'digest': 'b'*64}},
            'actor': 'Operator', 'timestamp': '2026-10-01T12:00:00+00:00'}


def legacy_snapshot():
    shaped = fixtures()['shape']
    shaped.update(method='bounded-plan', method_reason='Known change')
    return {'revision': 2, 'shape': shaped, 'ratings': {'urgency': 7, 'importance': 8, 'actor': 'operator'},
            'assessments': [assessment(assessment_fields())], 'actor': 'Operator',
            'action': 'shape', 'timestamp': '2026-10-01T12:00:00+00:00'}


class WorkflowSchemaTests(unittest.TestCase):
    def assert_rejected(self, operation, code=None):
        with self.assertRaises(IdeaError) as caught:
            operation()
        if code:
            self.assertEqual(caught.exception.code, code)

    def test_all_seven_exact_frozen_field_sets_and_detached_results(self):
        self.assertEqual(tuple(fixtures()), STEP_ORDER)
        for step, fields in fixtures().items():
            with self.subTest(step=step):
                self.assertEqual(set(fields), STEP_FIELDS[step])
                self.assertEqual(step_requirements(step, fields), ())
                result = validate_step_fields(step, fields)
                self.assertEqual(result, fields)
                result[next(iter(fields))] = None
                self.assertNotEqual(result, fields)
                self.assert_rejected(lambda: validate_step_fields(step, dict(fields, unsupported=1)))

    def test_partial_drafts_never_satisfy_acceptance(self):
        for step in STEP_ORDER:
            with self.subTest(step=step):
                self.assertEqual(validate_step_fields(step, {}, partial=True), {})
                self.assertEqual(validate_step_fields(step, dict.fromkeys(STEP_FIELDS[step]), partial=True), dict.fromkeys(STEP_FIELDS[step]))
                self.assertTrue(step_requirements(step, {}))
                self.assert_rejected(lambda: validate_step_fields(step, dict.fromkeys(STEP_FIELDS[step])), 'not_ready')
        partial = {'urgency': 7, 'importance': None}
        self.assertEqual(step_requirements('priorities', partial), ('importance',))

    def test_draft_empty_text_is_editable_but_not_acceptable(self):
        value = fixtures()['capture']
        value['raw_text'] = ' \n'
        self.assertEqual(validate_step_fields('capture', value, partial=True), value)
        self.assertIn('raw_text', step_requirements('capture', value))
        self.assert_rejected(lambda: validate_step_fields('capture', value), 'not_ready')

    def test_capture_verbatim_workspace_confirmed_and_cross_platform_paths(self):
        value = fixtures()['capture']
        self.assertEqual(validate_step_fields('capture', value)['raw_text'], value['raw_text'])
        value['workspace']['path'] = r'C:\Users\fixture\my project'
        self.assertEqual(validate_step_fields('capture', value), value)
        value['workspace']['confirmed'] = False
        self.assertIn('workspace.confirmed', step_requirements('capture', value))
        value['workspace']['confirmed'] = 1
        self.assert_rejected(lambda: validate_step_fields('capture', value, partial=True))

    def test_integer_doors_exclude_boolean_fraction_and_out_of_range(self):
        for bad in (True, False, 1.0, 0, 11, '7', [], {}):
            with self.subTest(bad=bad):
                self.assert_rejected(lambda: validate_step_fields('priorities', {'urgency': bad}, partial=True))
        for bad in (True, 0, -1, 1.5):
            self.assert_rejected(lambda: validate_step_fields('review', {'source_revision': bad}, partial=True))
            self.assert_rejected(lambda: validate_step_fields('assess', {'position': {'actual_position': bad}}, partial=True))

    def test_shape_requires_real_alternative_and_next_slice(self):
        value = fixtures()['shape']
        value['alternatives'] = []
        self.assertIn('alternatives', step_requirements('shape', value))
        value['alternatives'] = [{'route': ' ', 'reason': None}]
        self.assertIn('alternatives.0.route', step_requirements('shape', value))
        self.assertIn('alternatives.0.reason', step_requirements('shape', value))
        value['next_slice'] = None
        self.assertIn('next_slice', step_requirements('shape', value))
        value['method'] = 'bounded-plan'
        self.assert_rejected(lambda: validate_step_fields('shape', value, partial=True))

    def test_all_method_conditional_fields_and_unused_nulls(self):
        for selected in ('bounded-plan', 'adaptive-slices', 'appetite-led', 'experiment-led'):
            value = fixtures()['method']
            value['selection'] = selected
            if selected == 'appetite-led':
                value['investment'] = {'cap': 2, 'unit': 'sessions', 'boundary': 'Fixture only'}
            if selected == 'experiment-led':
                value['experiment'] = {'question': 'Will this fit?', 'evidence': 'Measure fit',
                                       'success_criterion': 'Fits one lid', 'stop_rule': 'Stop after one test'}
            with self.subTest(method=selected):
                self.assertEqual(validate_step_fields('method', value), value)
                if selected == 'appetite-led':
                    value['investment']['boundary'] = ' '
                elif selected == 'experiment-led':
                    value['experiment']['stop_rule'] = None
                else:
                    value['investment'] = {'cap': 2, 'unit': 'sessions', 'boundary': 'Stale hidden field'}
                self.assertTrue(step_requirements('method', value))
                self.assert_rejected(lambda: validate_step_fields('method', value), 'not_ready')

    def test_investment_is_positive_finite_not_boolean(self):
        for bad in (True, 0, -2, float('nan'), float('inf'), 10**400):
            self.assert_rejected(lambda: validate_step_fields('method', {'investment': {'cap': bad}}, partial=True))

    def test_memory_found_requires_sources_and_rationale(self):
        value = fixtures()['method']
        for status in ('found', 'searched_no_preference', 'unavailable', 'error'):
            value['memory'] = {'status': status, 'sources': [], 'rationale': None}
            if status == 'found':
                self.assertIn('memory.sources', step_requirements('method', value))
                self.assertIn('memory.rationale', step_requirements('method', value))
                value['memory'].update(sources=['saved-decision-fixture'], rationale='Fixture source preference')
            self.assertEqual(validate_step_fields('method', value), value)
        value['memory']['sources'] = ['invented preference']
        self.assert_rejected(lambda: validate_step_fields('method', value), 'not_ready')

    def test_visual_dispositions_require_set_or_meaningful_reason(self):
        for disposition in ('skipped', 'not-applicable'):
            value = fixtures()['visualize']
            value['disposition'] = disposition
            self.assertEqual(validate_step_fields('visualize', value), value)
            value['reason'] = None
            self.assert_rejected(lambda: validate_step_fields('visualize', value), 'not_ready')
            value['reason'] = 'No designs'
            value['design_set_id'] = 'set-fixture'
            self.assert_rejected(lambda: validate_step_fields('visualize', value), 'not_ready')
        value = {'disposition': 'accepted_set', 'reason': None, 'design_set_id': 'set-fixture', 'brief_evidence_id': 'brief-fixture'}
        self.assertEqual(validate_step_fields('visualize', value), value)
        value['design_set_id'] = None
        self.assert_rejected(lambda: validate_step_fields('visualize', value), 'not_ready')

    def test_assessment_uses_existing_scoring_unknowns_and_kano(self):
        expected = {'wsjf': None, 'rice': 25, 'kano': None}
        for method, score in expected.items():
            value = fixtures()['assess']
            value['assessment'] = assessment_fields(method)
            validated = validate_step_fields('assess', value)
            self.assertNotIn('score', validated['assessment'])
            self.assertEqual(assessment(validated['assessment'])['score'], score)
            value['assessment']['score'] = 99
            self.assert_rejected(lambda: validate_step_fields('assess', value, partial=True))

    def test_assessment_partial_inputs_valid_but_missing_keys_not_accepted(self):
        partial = {'assessment': {'method': 'rice', 'inputs': {'confidence': None}}}
        self.assertEqual(validate_step_fields('assess', partial, partial=True), partial)
        self.assertIn('assessment', step_requirements('assess', partial))
        for bad in (True, float('nan'), float('inf'), 1.1, -1):
            self.assert_rejected(lambda: validate_step_fields('assess', {'assessment': {'method': 'rice', 'inputs': {'confidence': bad}}}, partial=True))
        self.assert_rejected(lambda: validate_step_fields('assess', {'assessment': {'method': 'kano', 'inputs': {'hypothesis': 1}}}, partial=True))

    def test_position_records_override_reason_and_stable_neighbor_ids(self):
        value = fixtures()['assess']
        value['position']['actual_position'] = 2
        self.assertIn('position.override_reason', step_requirements('assess', value))
        value['position']['override_reason'] = 'Human urgency'
        value['position']['neighbors']['before'] = 'idea_'+'1'*32
        self.assertEqual(validate_step_fields('assess', value), value)
        value['position']['neighbors']['after'] = 'IDEA-0002'
        self.assert_rejected(lambda: validate_step_fields('assess', value, partial=True))
        value['position']['neighbors']['after'] = value['position']['neighbors']['before']
        self.assert_rejected(lambda: validate_step_fields('assess', value, partial=True))

    def test_unsupported_fields_bad_unicode_and_collection_types(self):
        self.assert_rejected(lambda: validate_step_fields('unknown', {}, partial=True))
        self.assert_rejected(lambda: validate_step_fields('shape', {'outcome': '\ud800'}, partial=True))
        self.assert_rejected(lambda: validate_step_fields('shape', {'assumptions': 'not a list'}, partial=True))
        self.assert_rejected(lambda: validate_step_fields('shape', {'alternatives': [{'route': 'x', 'reason': 'y', 'shell': 'x'}]}, partial=True))
        self.assert_rejected(lambda: validate_step_fields('visualize', {'design_set_id': '../file'}, partial=True))
        self.assert_rejected(lambda: validate_step_fields('capture', {'workspace': {'name': 'x', 'path': '/x', 'confirmed': True, 'actor': 'fake'}}, partial=True))

    def test_empty_workflow_has_no_default_or_acceptance(self):
        value = empty_workflow()
        self.assertEqual(validate_workflow(value), value)
        self.assertEqual(value['drafts'], {})
        for record in value['steps'].values():
            self.assertIsNone(record['fields'])
            self.assertIsNone(record['acceptance'])
        value['drafts']['priorities'] = {'urgency': 7, 'importance': None}
        self.assertEqual(validate_workflow(value), value)

    def test_receipts_source_evidence_and_dependency_types_are_strict(self):
        value = receipt()
        self.assertEqual(validate_acceptance(value), value)
        for key, bad in (('accepted_revision', True), ('source_revision', 3), ('source_digest', 'x'),
                         ('evidence_id', '/path'), ('actor', ''), ('dependencies', {'unknown': {'revision': 1, 'digest': 'b'*64}})):
            broken = copy.deepcopy(value)
            broken[key] = bad
            self.assert_rejected(lambda: validate_acceptance(broken))
        broken = copy.deepcopy(value)
        broken['dependencies']['capture']['revision'] = True
        self.assert_rejected(lambda: validate_acceptance(broken))

    def test_workflow_requires_acceptance_for_fields_and_keeps_stale_evidence(self):
        value = empty_workflow()
        value['steps']['visualize']['fields'] = fixtures()['visualize']
        self.assert_rejected(lambda: validate_workflow(value))
        value['steps']['visualize']['acceptance'] = receipt()
        value['steps']['visualize']['invalidated_by'] = ['shape']
        self.assertEqual(validate_workflow(value), value)
        value['steps']['visualize']['invalidated_by'] = ['shape', 'shape']
        self.assert_rejected(lambda: validate_workflow(value))

    def test_legacy_snapshot_unchanged_exact_shape_and_no_fabricated_acceptance(self):
        value = legacy_snapshot()
        before = encoded(value)
        self.assertEqual(set(value['shape']), SHAPE_KEYS)
        result = adapt_snapshot(value)
        self.assertEqual(encoded(result), before)
        self.assertNotIn('schema_version', result)
        self.assertNotIn('workflow', result)
        result['shape']['outcome'] = 'Modified copy'
        self.assertEqual(encoded(value), before)
        origin_only = dict(value, revision=1, shape=None, ratings=None, assessments=[])
        self.assertEqual(adapt_snapshot(origin_only), origin_only)

    def test_actual_cli_history_preserves_attribution_metadata_and_exact_bytes(self):
        script = Path(__file__).resolve().parents[1] / 'glitch-idea/scripts/idea.py'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root/'words.txt'
            raw.write_bytes('  Café 💡\r\n'.encode('utf-8'))

            def cli(*arguments):
                result = subprocess.run([sys.executable, str(script), '--store', str(root/'ideas'),
                                         *map(str, arguments)], capture_output=True, check=False)
                response = json.loads(result.stdout)
                self.assertEqual(result.returncode, 0, response)
                return response['idea']

            idea = cli('capture', '--text-file', raw, '--actor', 'operator')
            idea = cli('rate', idea['idea_id'], '--expected-revision', '1',
                       '--urgency', '7', '--importance', '8', '--actor', 'operator')
            input_path = root/'assessment.json'
            input_path.write_bytes(encoded(assessment_fields()))
            idea = cli('assess', idea['idea_id'], '--expected-revision', '2',
                       '--file', input_path, '--actor', 'Operator')
            self.assertIn('timestamp', idea['ratings'])
            self.assertTrue({'assessment_id', 'actor', 'timestamp'} <= set(idea['assessments'][0]))
            for snapshot in idea['revisions']:
                before = encoded(snapshot)
                self.assertEqual(encoded(adapt_snapshot(snapshot)), before)
                extended = adapt_snapshot(snapshot, empty_workflow())
                self.assertEqual({k: v for k, v in extended.items() if k not in ('schema_version', 'workflow')}, snapshot)
                self.assertEqual(extended['workflow'], empty_workflow())

    def test_v2_snapshots_include_visual_and_assess_evidence_without_legacy_drift(self):
        value = legacy_snapshot()
        workflow = empty_workflow()
        for step in ('visualize', 'assess'):
            workflow['steps'][step] = {'fields': fixtures()[step], 'acceptance': receipt(), 'invalidated_by': []}
        result = adapt_snapshot(value, workflow)
        self.assertEqual(result['schema_version'], 2)
        self.assertEqual(validate_snapshot(result), result)
        self.assertEqual(result['shape'], value['shape'])
        self.assertEqual(result['assessments'], value['assessments'])
        self.assertEqual(adapt_snapshot(result), result)
        result['workflow']['steps']['assess']['acceptance']['accepted_revision'] = 3
        self.assert_rejected(lambda: validate_snapshot(result))

    def test_version_and_snapshot_score_tampering_fail_closed(self):
        for bad in (True, 1, 3, '2'):
            value = empty_workflow()
            value['schema_version'] = bad
            self.assert_rejected(lambda: validate_workflow(value))
        value = legacy_snapshot()
        value['assessments'][0]['score'] = 0
        self.assert_rejected(lambda: validate_snapshot(value))
        value = dict(legacy_snapshot(), schema_version=2)
        self.assert_rejected(lambda: validate_snapshot(value))
        value = legacy_snapshot()
        value['revision'] = True
        self.assert_rejected(lambda: validate_snapshot(value))


if __name__ == '__main__':
    unittest.main()
