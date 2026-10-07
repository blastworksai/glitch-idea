"""Pure decision/revision and source invalidation tests. Author: Operator."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
from idea_domain import IdeaError, encoded
from idea_workflow import (STEP_ORDER, acceptance_source, accept_step,
                           capture_workflow, dependent_steps, derive_state,
                           empty_workflow, import_workflow, invalidate_external,
                           save_draft, source_digest, validate_snapshot)


ACTOR = 'Operator'
STAMP = '2026-10-01T12:00:00+00:00'


def fields():
    return {
        'capture': {'raw_text': '  Café 💡\r\n', 'workspace': {'name': 'fixture', 'path': '/example/fixture', 'confirmed': True}},
        'priorities': {'urgency': 7, 'importance': 8},
        'method': {'selection': 'bounded-plan', 'reason': 'Known change',
                   'memory': {'status': 'unavailable', 'sources': [], 'rationale': None}},
        'discovery': {'problem': 'Lids are hard to clean', 'audience': 'Home cooks', 'workaround': 'Scrub by hand',
                      'evidence': 'Three complaints', 'kill_criteria': 'Nobody cleans lids',
                      'challenges': [{'challenge': 'Is this a real pain?', 'response': 'Reported three times'}],
                      'prior_art': [{'name': 'Lid brush', 'link': 'https://example.invalid/brush',
                                     'does': 'Scrubs lids', 'differs': 'Ours needs no scrubbing',
                                     'licence': 'Not stated'}],
                      'prior_art_none': False, 'prior_art_searched': ''},
        'exploration': {'outcome': 'Easier cleaning', 'alternatives': [{'route': 'Clean existing lid', 'reason': 'Simpler'}],
                        'assumptions': [], 'scope': 'small-change', 'scope_reason': 'One lid',
                        'next_slice': 'Check lid', 'learning': [], 'investment': None, 'experiment': None,
                        'sketch': [{'title': 'Check the lid', 'why_next': 'Cheapest test', 'done_when': 'Lid is checked',
                                    'method': None}]},
        'visualize': {'disposition': 'skipped', 'reason': 'No design needed', 'design_set_id': None, 'brief_evidence_id': None},
        'assess': {'assessment': {'method': 'wsjf', 'version': 'fixture-v1',
                    'inputs': {'value': 3, 'time_criticality': None, 'enablement': 2, 'effort': 1},
                    'basis': 'Fixture estimates', 'assumptions': [], 'confidence': 'low', 'provenance': 'Current-agent proposal'},
                   'position': {'proposed_position': 1, 'actual_position': 1,
                    'neighbors': {'before': None, 'after': None}, 'override_reason': None}},
    }


def original_idea():
    raw = fields()['capture']['raw_text']
    snapshot = {'revision': 1, 'shape': None, 'ratings': None, 'assessments': [],
                'actor': 'operator', 'action': 'capture', 'timestamp': STAMP}
    return {'idea_id': 'idea_'+'1'*32, 'revision': 1, 'status': 'active',
            'origin': {'text': raw, 'sha256': hashlib.sha256(raw.encode('utf-8')).hexdigest(),
                       'actor': 'operator', 'timestamp': STAMP},
            'shape': None, 'ratings': None, 'assessments': [], 'revisions': [snapshot],
            'proposals': [], 'plans': [], 'executions': []}


def captured():
    idea = original_idea()
    value = fields()['capture']
    digest = source_digest('capture', 1, {'capture': value})
    return capture_workflow(idea, value, new_capture=True, actor='operator', timestamp=STAMP,
                            evidence_id='capture-fixture', source_digest=digest)['idea']


def accept(idea, step, value=None, extra_dependencies=()):
    value = fields()[step] if value is None else value
    expected = acceptance_source(idea, step, value, extra_dependencies)
    return accept_step(idea, step, value, expected_revision=idea['revision'],
                       expected_draft_version=idea.get('workflow', empty_workflow())['draft_version'],
                       actor=ACTOR, timestamp=STAMP, evidence_id='receipt-'+step+'-'+str(idea['revision']),
                       source_digest=expected['source_digest'], extra_dependencies=extra_dependencies)


def complete():
    idea = captured()
    for step in ('priorities', 'method', 'discovery', 'exploration', 'visualize', 'assess'):
        idea = accept(idea, step)['idea']
    return idea


class WorkflowTests(unittest.TestCase):
    def assert_error(self, code, operation):
        with self.assertRaises(IdeaError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def test_new_capture_saved_at_one_has_explicit_receipt_and_preserves_origin(self):
        original = original_idea()
        before = encoded(original)
        idea = captured()
        self.assertEqual(encoded(original), before)
        self.assertEqual(idea['origin'], original['origin'])
        self.assertEqual(idea['revision'], 1)
        self.assertEqual(len(idea['revisions']), 1)
        self.assertEqual(idea['revisions'][0]['workflow'], idea['workflow'])
        self.assertEqual(derive_state(idea)['steps']['capture']['status'], 'saved')
        self.assertEqual(idea['workflow']['current_step'], 'priorities')

    def test_legacy_origin_alone_never_creates_wizard_acceptance(self):
        idea = original_idea()
        value = fields()['capture']
        self.assert_error('not_ready', lambda: capture_workflow(idea, value, actor=ACTOR, timestamp=STAMP,
                                                               evidence_id='fake', source_digest='a'*64))
        imported = import_workflow(idea)
        self.assertEqual(imported['workflow']['drafts']['capture'], {'raw_text': idea['origin']['text']})
        self.assertTrue(all(record['acceptance'] is None for record in imported['workflow']['steps'].values()))
        self.assertEqual(imported['revisions'], idea['revisions'])
        self.assertEqual(derive_state(idea)['steps']['capture']['status'], 'unsaved')
        accepted = accept(imported, 'capture')['idea']
        self.assertEqual(accepted['revision'], 2)
        self.assertEqual(derive_state(accepted)['steps']['capture']['status'], 'saved')

    def test_legacy_import_maps_no_old_shape_or_method_data(self):
        idea = complete()
        del idea['workflow']
        # A CLI-only baseline has legacy snapshots, not inferred workflow evidence.
        for snapshot in idea['revisions']:
            snapshot.pop('workflow', None)
            snapshot.pop('schema_version', None)
        idea['shape'] = {'outcome': 'Old shape', 'method': 'bounded-plan', 'method_reason': 'Old reason'}
        before = encoded(idea)
        result = import_workflow(idea)
        self.assertEqual(result['workflow']['schema_version'], 3)
        for step in ('method', 'discovery', 'exploration'):
            self.assertNotIn(step, result['workflow']['drafts'])
        self.assertEqual(result['revisions'], idea['revisions'])
        self.assertEqual(encoded(idea), before)

    def test_draft_changes_only_draft_version_and_partial_rating_survives(self):
        idea = captured()
        before = encoded(idea)
        result = save_draft(idea, 'priorities', {'urgency': 7, 'importance': None}, expected_revision=1, expected_draft_version=0)
        updated = result['idea']
        self.assertTrue(result['changed'])
        self.assertEqual(updated['revision'], 1)
        self.assertEqual(updated['workflow']['draft_version'], 1)
        self.assertIsNone(updated['ratings'])
        self.assertEqual(updated['revisions'], idea['revisions'])
        view = derive_state(updated)
        self.assertEqual(view['draft'], {'step': 'priorities', 'fields': {'urgency': 7, 'importance': None}})
        self.assertEqual(view['steps']['priorities']['status'], 'unsaved')
        self.assertEqual(encoded(idea), before)
        self.assert_error('not_ready', lambda: accept(updated, 'priorities', {'urgency': 7, 'importance': None}))
        again = save_draft(updated, 'priorities', {'urgency': 7, 'importance': None}, expected_revision=1, expected_draft_version=1)
        self.assertFalse(again['changed'])
        self.assertEqual(again['idea'], updated)

    def test_acceptance_adds_exactly_one_revision_and_keeps_prior_snapshots(self):
        idea = captured()
        idea = save_draft(idea, 'priorities', fields()['priorities'], expected_revision=1, expected_draft_version=0)['idea']
        before = encoded(idea)
        old_history = copy.deepcopy(idea['revisions'])
        result = accept(idea, 'priorities')
        updated = result['idea']
        self.assertTrue(result['changed'])
        self.assertEqual(updated['revision'], 2)
        self.assertEqual(updated['workflow']['draft_version'], 1)
        self.assertEqual(updated['revisions'][:-1], old_history)
        self.assertEqual(len(updated['revisions']), 2)
        self.assertEqual(updated['revisions'][-1]['workflow'], updated['workflow'])
        self.assertNotIn('priorities', updated['workflow']['drafts'])
        self.assertEqual(updated['ratings']['actor'], ACTOR)
        self.assertEqual(encoded(idea), before)
        validate_snapshot(updated['revisions'][-1])

    def test_unchanged_current_acceptance_is_no_op_even_after_unrelated_revision(self):
        idea = complete()
        before = encoded(idea)
        result = accept(idea, 'priorities')
        self.assertFalse(result['changed'])
        self.assertEqual(encoded(result['idea']), before)
        self.assertEqual(result['invalidated'], [])
        self.assertEqual(len(result['idea']['revisions']), idea['revision'])

    def test_unchanged_exploration_is_active_no_op_but_explicitly_reactivates_archive(self):
        idea = complete(); before = encoded(idea)
        active = accept(idea,'exploration')
        self.assertFalse(active['changed']); self.assertFalse(active['accepted_changed'])
        self.assertEqual(active['invalidated'],[]); self.assertEqual(encoded(active['idea']),before)
        idea['status'] = 'archived'; archived_before = encoded(idea)
        result = accept(idea,'exploration'); updated = result['idea']
        self.assertTrue(result['changed']); self.assertTrue(result['accepted_changed'])
        self.assertEqual(updated['status'],'active'); self.assertEqual(updated['revision'],idea['revision']+1)
        self.assertEqual(updated['revisions'][:-1],idea['revisions'])
        for key in ('origin','ratings','assessments','plans','executions'):
            self.assertEqual(updated[key],idea[key])
        self.assertEqual(updated['workflow']['steps']['exploration']['fields'],idea['workflow']['steps']['exploration']['fields'])
        self.assertEqual(updated['workflow']['steps']['exploration']['acceptance']['accepted_revision'],updated['revision'])
        self.assertEqual(result['invalidated'],['visualize','assess'])
        for step in result['invalidated']:
            self.assertEqual(updated['workflow']['steps'][step]['acceptance'],idea['workflow']['steps'][step]['acceptance'])
            self.assertEqual(derive_state(updated)['steps'][step]['status'],'review-needed')
        self.assertEqual(encoded(idea),archived_before)
        validate_snapshot(updated['revisions'][-1])

    def test_all_declared_transitive_invalidations_and_revision_atomicity(self):
        expected = {
            'capture': ('method', 'discovery', 'exploration', 'visualize', 'assess', 'review'),
            'priorities': ('method', 'discovery', 'exploration', 'visualize', 'assess', 'review'),
            'method': ('discovery', 'exploration', 'visualize', 'assess', 'review'),
            'discovery': ('exploration', 'visualize', 'assess', 'review'),
            'exploration': ('visualize', 'assess', 'review'),
            'visualize': ('review',), 'assess': ('review',),
        }
        for step, downstream in expected.items():
            with self.subTest(step=step):
                idea = complete()
                self.assertEqual(dependent_steps(idea['workflow'], (step,)), downstream)
                handoff = {'handoff_id': 'handoff-fixture', 'source_revision': idea['revision']}
                self.assertEqual(derive_state(idea, handoff)['steps']['review']['status'], 'saved')
                value = fields()[step]
                if step == 'capture':
                    value['workspace']['path'] = '/example/other'
                elif step == 'priorities':
                    value['importance'] = 9
                elif step == 'discovery':
                    value['problem'] = 'Lids are hard to clean after oil'
                elif step == 'exploration':
                    value['next_slice'] = 'Check the second lid'
                elif step == 'method':
                    value['selection'] = 'adaptive-slices'
                elif step == 'visualize':
                    value['reason'] = 'No fixture UI'
                else:
                    value['assessment']['inputs']['effort'] = 2
                history = copy.deepcopy(idea['revisions'])
                result = accept(idea, step, value)
                updated = result['idea']
                self.assertEqual(updated['revision'], idea['revision'] + 1)
                self.assertEqual(updated['revisions'][:-1], history)
                self.assertEqual(tuple(result['invalidated']), tuple(k for k in downstream if k != 'review'))
                view = derive_state(updated, handoff)
                self.assertEqual(view['steps'][step]['status'], 'skipped' if step == 'visualize' else 'saved')
                for target in downstream:
                    self.assertEqual(view['steps'][target]['status'], 'review-needed')
                for unaffected in set(STEP_ORDER) - set(downstream) - {step, 'review'}:
                    self.assertEqual(view['steps'][unaffected]['status'], 'skipped' if unaffected == 'visualize' else 'saved')

    def test_changed_capture_words_leave_exact_immutable_origin(self):
        idea = complete()
        before = copy.deepcopy(idea['origin'])
        value = fields()['capture']
        value['raw_text'] = 'Revised words'
        updated = accept(idea, 'capture', value)['idea']
        self.assertEqual(updated['origin'], before)
        self.assertEqual(updated['workflow']['steps']['capture']['fields']['raw_text'], 'Revised words')
        self.assertEqual(updated['revisions'][0], idea['revisions'][0])

    def test_reaccept_same_stale_fields_refreshes_sources_and_preserves_stale_assets(self):
        idea = complete()
        value = fields()['capture']
        value['workspace']['name'] = 'other fixture'
        changed = accept(idea, 'capture', value)['idea']
        stale_receipt = copy.deepcopy(changed['workflow']['steps']['method']['acceptance'])
        method_fields = copy.deepcopy(changed['workflow']['steps']['method']['fields'])
        result = accept(changed, 'method', method_fields)
        updated = result['idea']
        receipt = updated['workflow']['steps']['method']['acceptance']
        self.assertTrue(result['changed'])
        self.assertEqual(updated['revision'], changed['revision']+1)
        self.assertNotEqual(receipt['source_digest'], stale_receipt['source_digest'])
        self.assertEqual(receipt['source_revision'], changed['revision'])
        self.assertEqual(derive_state(updated)['steps']['method']['status'], 'saved')
        self.assertEqual(derive_state(updated)['steps']['discovery']['status'], 'review-needed')
        self.assertEqual(updated['workflow']['steps']['visualize']['acceptance'], idea['workflow']['steps']['visualize']['acceptance'])
        self.assertEqual(updated['workflow']['steps']['visualize']['fields'], fields()['visualize'])

    def test_reaccept_downstream_is_blocked_until_sources_are_current(self):
        idea = complete()
        value = fields()['capture']
        value['raw_text'] = 'Different'
        changed = accept(idea, 'capture', value)['idea']
        self.assert_error('not_ready', lambda: accept(changed, 'discovery'))
        self.assert_error('not_ready', lambda: accept(changed, 'assess'))
        methodical = accept(changed, 'method')['idea']
        self.assertEqual(derive_state(accept(methodical, 'discovery')['idea'])['steps']['discovery']['status'], 'saved')

    def test_changed_draft_blocks_downstream_without_mutating_receipts_then_undo_restores(self):
        idea = complete()
        value = fields()['method']
        value['reason'] = 'Unsaved reason'
        edited = save_draft(idea, 'method', value, expected_revision=idea['revision'], expected_draft_version=0)['idea']
        self.assertEqual(edited['revisions'], idea['revisions'])
        self.assertEqual(edited['workflow']['steps'], idea['workflow']['steps'])
        view = derive_state(edited)
        self.assertEqual(view['steps']['method']['status'], 'unsaved')
        for step in ('discovery', 'exploration', 'visualize', 'assess'):
            self.assertEqual(view['steps'][step]['status'], 'review-needed')
        self.assert_error('not_ready', lambda: accept(edited, 'discovery'))
        restored = save_draft(edited, 'method', fields()['method'], expected_revision=edited['revision'], expected_draft_version=1)['idea']
        self.assertEqual(derive_state(restored)['steps']['method']['status'], 'saved')
        self.assertEqual(derive_state(restored)['steps']['discovery']['status'], 'saved')

    def test_extra_consumed_dependency_extends_transitive_graph_and_is_retained(self):
        idea = complete()
        # Method proposal additionally consumed human priorities.
        idea = accept(idea, 'method', extra_dependencies=('priorities',))['idea']
        self.assertIn('priorities', idea['workflow']['steps']['method']['acceptance']['dependencies'])
        updated = accept(idea, 'priorities', {'urgency': 8, 'importance': 8})['idea']
        self.assertEqual(derive_state(updated)['steps']['method']['status'], 'review-needed')
        renewed = accept(updated, 'method')['idea']
        self.assertIn('priorities', renewed['workflow']['steps']['method']['acceptance']['dependencies'])
        self.assertEqual(derive_state(renewed)['steps']['method']['status'], 'saved')
        self.assert_error('invalid_input', lambda: acceptance_source(idea, 'method', fields()['method'], ('assess',)))
        self.assert_error('invalid_input', lambda: acceptance_source(idea, 'discovery', fields()['discovery'], ('discovery',)))

    def test_revision_and_draft_cas_reject_stale_and_bool_inputs_before_mutation(self):
        idea = complete()
        value = fields()['priorities']
        digest = acceptance_source(idea, 'priorities', value)['source_digest']
        for expected, draft, code in ((idea['revision']-1, 0, 'stale_revision'),
                                       (idea['revision'], 1, 'stale_draft_version'),
                                       (True, 0, 'invalid_input'), (idea['revision'], False, 'invalid_input')):
            before = encoded(idea)
            self.assert_error(code, lambda: save_draft(idea, 'priorities', value, expected_revision=expected, expected_draft_version=draft))
            self.assert_error(code, lambda: accept_step(idea, 'priorities', value, expected_revision=expected,
                                                       expected_draft_version=draft, actor=ACTOR, timestamp=STAMP,
                                                       evidence_id='fixture', source_digest=digest))
            self.assertEqual(encoded(idea), before)

    def test_stale_source_digest_never_changes_revision_or_fields(self):
        idea = complete()
        before = encoded(idea)
        self.assert_error('stale_source', lambda: accept_step(idea, 'priorities', fields()['priorities'],
                            expected_revision=idea['revision'], expected_draft_version=0, actor=ACTOR,
                            timestamp=STAMP, evidence_id='fixture', source_digest='a'*64))
        self.assertEqual(encoded(idea), before)

    def test_canonical_digest_is_compact_sorted_utf8_and_includes_operation_revision(self):
        sources = {'capture': fields()['capture'], 'priorities': {'importance': 8, 'urgency': 7}}
        payload = {'operation': 'exploration', 'source_revision': 3, 'fields': sources}
        expected = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                             separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
        self.assertEqual(source_digest('exploration', 3, sources), expected)
        reordered = dict(reversed(tuple(sources.items())))
        reordered['capture'] = dict(reversed(tuple(sources['capture'].items())))
        self.assertEqual(source_digest('exploration', 3, reordered), expected)
        self.assertNotEqual(source_digest('exploration', 4, sources), expected)
        self.assertNotEqual(source_digest('method', 3, sources), expected)
        self.assertNotEqual(expected, hashlib.sha256(sources['capture']['raw_text'].encode('utf-8')).hexdigest())
        self.assert_error('invalid_input', lambda: source_digest('exploration', True, sources))
        self.assert_error('invalid_input', lambda: source_digest('exploration', 3, {'priorities': {'urgency': float('nan')}}))

    def test_cli_external_invalidation_retains_old_evidence_and_legacy_noop(self):
        legacy = original_idea()
        self.assertEqual(invalidate_external(legacy, ('exploration',))['idea'], legacy)
        self.assertNotIn('workflow', invalidate_external(legacy, ('exploration',))['idea'])
        idea = complete()
        before = encoded(idea)
        result = invalidate_external(idea, ('exploration',))
        updated = result['idea']
        self.assertEqual(result['invalidated'], ['exploration', 'visualize', 'assess'])
        self.assertEqual(updated['revision'], idea['revision'])
        self.assertEqual(updated['revisions'], idea['revisions'])
        self.assertEqual(updated['workflow']['steps']['exploration']['acceptance'], idea['workflow']['steps']['exploration']['acceptance'])
        self.assertEqual(derive_state(updated)['steps']['exploration']['status'], 'review-needed')
        self.assertEqual(encoded(idea), before)

    def test_legacy_mirrors_never_write_shape_or_change_human_ratings(self):
        idea = complete()
        self.assertIsNone(idea['shape'])
        self.assertEqual(idea['assessments'][-1]['score'], None)
        ratings = copy.deepcopy(idea['ratings'])
        value = fields()['assess']
        value['assessment']['inputs']['time_criticality'] = 4
        updated = accept(idea, 'assess', value)['idea']
        self.assertEqual(updated['assessments'][-1]['score'], 9)
        self.assertEqual(updated['ratings'], ratings)
        value = fields()['method']
        value.update(selection='appetite-led')
        updated = accept(updated, 'method', value)['idea']
        self.assertIsNone(updated['shape'])
        self.assertEqual(updated['workflow']['steps']['method']['fields']['selection'], 'appetite-led')
        self.assertEqual(derive_state(updated)['steps']['exploration']['status'], 'review-needed')

    def test_review_is_derived_current_packet_with_no_domain_mutation(self):
        idea = complete()
        before = encoded(idea)
        packet = {'handoff_id': 'handoff-fixture', 'source_revision': idea['revision']}
        view = derive_state(idea, packet)
        self.assertEqual(view['steps']['review']['status'], 'saved')
        self.assertEqual(view['accepted']['review'], packet)
        self.assertEqual(encoded(idea), before)
        self.assertEqual(derive_state(idea)['steps']['review']['status'], 'current')
        self.assert_error('derived_step', lambda: accept_step(idea, 'review', packet,
                            expected_revision=idea['revision'], expected_draft_version=0, actor=ACTOR,
                            timestamp=STAMP, evidence_id='handoff-fixture', source_digest='a'*64))
        changed = accept(idea, 'priorities', {'urgency': 9, 'importance': 8})['idea']
        self.assertEqual(derive_state(changed, packet)['steps']['review']['status'], 'review-needed')
        missing = captured()
        self.assertEqual(derive_state(missing, {'handoff_id': 'old', 'source_revision': 1})['steps']['review']['status'], 'review-needed')

    def test_accepted_fields_and_drafts_are_detached_and_current_panel_is_independent(self):
        idea = complete()
        idea['workflow']['current_step'] = 'exploration'
        value = fields()['exploration']
        value['outcome'] = 'New draft'
        idea = save_draft(idea, 'exploration', value, expected_revision=idea['revision'], expected_draft_version=0)['idea']
        view = derive_state(idea)
        self.assertEqual(view['current_step'], 'exploration')
        self.assertEqual(view['steps']['exploration']['status'], 'unsaved')
        self.assertEqual(view['accepted']['exploration']['outcome'], 'Easier cleaning')
        self.assertEqual(view['drafts']['exploration']['outcome'], 'New draft')
        view['accepted']['exploration']['outcome'] = 'Modified returned value'
        view['drafts']['exploration']['outcome'] = 'Another modification'
        self.assertEqual(idea['workflow']['steps']['exploration']['fields']['outcome'], 'Easier cleaning')
        self.assertEqual(idea['workflow']['drafts']['exploration']['outcome'], 'New draft')

    def test_visual_set_and_skipped_statuses_require_persisted_evidence(self):
        for value, expected in (({'disposition': 'accepted_set', 'reason': None, 'design_set_id': 'set-fixture', 'brief_evidence_id': 'brief-fixture', 'source': 'claude_design'}, 'saved'),
                                 ({'disposition': 'skipped', 'reason': None, 'design_set_id': None, 'brief_evidence_id': None}, 'skipped')):
            idea = complete()
            updated = accept(idea, 'visualize', value)['idea']
            view = derive_state(updated)
            self.assertEqual(view['steps']['visualize']['status'], expected)
            self.assertIsNotNone(view['steps']['visualize']['evidence_id'])
            pending = save_draft(idea, 'visualize', value, expected_revision=idea['revision'], expected_draft_version=0)['idea']
            self.assertEqual(derive_state(pending)['steps']['visualize']['status'], 'unsaved')

    def test_not_applicable_is_not_a_step_status(self):
        import idea_workflow
        self.assertNotIn('not-applicable', idea_workflow.STEP_STATUSES)
        self.assertEqual(idea_workflow.VISUAL_DISPOSITIONS, ('accepted_set', 'skipped'))
        src = open(idea_workflow.__file__, encoding='utf-8').read()
        self.assertNotIn('not-applicable', src.replace("# 'not-applicable' is gone; an old value is simply invalid", ''))

    def test_visualize_dispositions_source_and_optional_reason(self):
        from idea_workflow import validate_step_fields
        base = {'disposition': 'skipped', 'reason': None, 'design_set_id': None, 'brief_evidence_id': None}
        self.assertEqual(validate_step_fields('visualize', base)['disposition'], 'skipped')  # no reason needed
        for bad in (dict(base, disposition='not-applicable', reason='Nonvisual'),
                    dict(base, disposition='accepted_set', design_set_id='set-fixture'),  # source missing
                    dict(base, disposition='accepted_set', design_set_id='set-fixture', source='figma'),
                    dict(base, disposition='accepted_set', design_set_id='set-fixture', source=None)):
            with self.subTest(bad=bad), self.assertRaises(IdeaError): validate_step_fields('visualize', bad)
        for source in ('claude_design', 'prototype'):
            ok = dict(base, disposition='accepted_set', design_set_id='set-fixture', source=source)
            self.assertEqual(validate_step_fields('visualize', ok)['source'], source)
        proto = dict(base, disposition='accepted_set', design_set_id='set-fixture', source='prototype',
                     assets=['asset_'+'a'*32, 'asset_'+'b'*32])
        self.assertEqual(validate_step_fields('visualize', proto)['assets'], proto['assets'])

    def test_pause_resume_uses_selected_draft_and_acceptance_frontier(self):
        idea = complete()
        self.assertEqual(idea['workflow']['current_step'], 'review')
        value = fields()['exploration']
        value['next_slice'] = 'Pause here'
        draft = save_draft(idea, 'exploration', value, expected_revision=idea['revision'], expected_draft_version=0)['idea']
        self.assertEqual(derive_state(draft)['current_step'], 'exploration')
        self.assertEqual(derive_state(draft)['draft']['fields'], value)
        result = accept(draft, 'exploration', value)
        self.assertTrue(result['accepted_changed'])
        self.assertEqual(result['idea']['workflow']['current_step'], 'visualize')
        self.assertEqual(result['idea']['revisions'][-1]['workflow']['current_step'], 'visualize')
        fresh = captured()
        fresh = accept(fresh, 'priorities')['idea']
        self.assertEqual(fresh['workflow']['current_step'], 'method')

    def test_revert_draft_by_accepting_same_data_is_only_draft_change(self):
        idea = complete()
        value = fields()['priorities']
        value['urgency'] = 9
        edited = save_draft(idea, 'priorities', value, expected_revision=idea['revision'], expected_draft_version=0)['idea']
        self.assertEqual(derive_state(edited)['steps']['assess']['status'], 'review-needed')
        prior_receipt = copy.deepcopy(edited['workflow']['steps']['priorities']['acceptance'])
        result = accept(edited, 'priorities', fields()['priorities'])
        updated = result['idea']
        self.assertTrue(result['changed'])
        self.assertFalse(result['accepted_changed'])
        self.assertEqual(updated['revision'], idea['revision'])
        self.assertEqual(updated['revisions'], idea['revisions'])
        self.assertEqual(updated['workflow']['draft_version'], 2)
        self.assertNotIn('priorities', updated['workflow']['drafts'])
        self.assertEqual(updated['workflow']['steps']['priorities']['acceptance'], prior_receipt)
        self.assertEqual(result['invalidated'], [])
        self.assertEqual(derive_state(updated)['steps']['assess']['status'], 'saved')
        self.assertEqual(updated['workflow']['current_step'], 'review')

    def test_changed_choice_requires_earlier_panels_but_unchanged_valid_choice_does_not(self):
        legacy = import_workflow(original_idea())
        self.assert_error('not_ready', lambda: accept(legacy, 'priorities'))
        idea = complete()
        value = fields()['capture']
        value['raw_text'] = 'Unsaved earlier capture'
        draft = save_draft(idea, 'capture', value, expected_revision=idea['revision'], expected_draft_version=0)['idea']
        # Human ratings consume no capture text. True unchanged acceptance does
        # not pretend those independent ratings were invalidated by this draft.
        noop = accept(draft, 'priorities')
        self.assertFalse(noop['accepted_changed'])
        self.assertEqual(noop['idea']['revision'], idea['revision'])
        self.assertEqual(noop['idea']['workflow']['steps']['priorities'], idea['workflow']['steps']['priorities'])
        self.assert_error('not_ready', lambda: accept(draft, 'priorities', {'urgency': 9, 'importance': 8}))

    def test_future_current_receipt_cannot_be_reported_as_saved(self):
        idea = captured()
        idea['workflow']['steps']['capture']['acceptance']['accepted_revision'] = 2
        self.assert_error('invalid_input', lambda: derive_state(idea))


if __name__ == '__main__':
    unittest.main()
