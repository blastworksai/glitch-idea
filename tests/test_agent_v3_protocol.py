"""Agent protocol on workflow v3: discovery/exploration operations, memory-only method."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
import idea_agent_source as source
import idea_proposal_evidence as codec
import idea_proposals as proposals
from idea_domain import IdeaError
from idea_workflow import (DISCOVERY_BEFORE_METHODS, STEP_FIELDS, derive_dependencies,
                           source_digest)

MEMORY = dict(status='varied', sources=[], rationale=None, preferred_method=None)


def record(operation, proposal, data=None):
    data = data if data is not None else {'capture': {'raw_text': 'An idea'}}
    return dict(schema_version=1, kind='agent-proposal', proposal_id='proposal_'+'1'*32,
        binding_id='binding_'+'2'*32, generation='agent_'+'3'*32, actor='Operator',
        timestamp='2026-10-01T00:00:00Z', request_id='proposal-request-1', session_id='session_'+'4'*32,
        idea_id='idea_'+'5'*32, accepted_revision=2, draft_version=3, operation=operation,
        source_digest=(source_digest(operation, 2, data) if operation != 'shape' else '0'*64), data=data, proposal=proposal)


class OperationTests(unittest.TestCase):
    def test_operations_are_v3(self):
        for ops in (proposals.OPERATIONS, source.OPERATIONS):
            self.assertIn('discovery', ops)
            self.assertIn('exploration', ops)
            self.assertNotIn('shape', ops)
        self.assertNotIn('shape', codec.SUPPORTED)
        self.assertLessEqual({'discovery', 'exploration'}, codec.SUPPORTED)

    def test_inputs_follow_the_dependency_table(self):
        deps = derive_dependencies(DISCOVERY_BEFORE_METHODS)
        self.assertEqual(source.INPUTS['discovery'], deps['discovery'])
        self.assertEqual(source.INPUTS['exploration'], deps['exploration'])
        self.assertEqual(source.INPUTS['method'], deps['method'])

    def test_fill_keys(self):
        self.assertEqual(proposals.FILL_KEYS['discovery'], STEP_FIELDS['discovery'])
        self.assertEqual(proposals.FILL_KEYS['exploration'], STEP_FIELDS['exploration'])
        self.assertEqual(proposals.FILL_KEYS['method'], frozenset(('memory',)))


class FillTests(unittest.TestCase):
    def test_discovery_and_exploration_fills(self):
        self.assertEqual(proposals._default_fill('discovery', {'problem': 'Lids leak'}), {'problem': 'Lids leak'})
        challenge = {'challenges': [{'challenge': 'Why now?', 'response': 'Complaints'}]}
        self.assertEqual(proposals._default_fill('discovery', challenge), challenge)
        fill = {'outcome': 'Dry lids', 'sketch': [{'title': 'a', 'why_next': 'b', 'done_when': 'c', 'method': None}]}
        self.assertEqual(proposals._default_fill('exploration', fill), fill)

    def test_method_fill_refuses_selection(self):
        for fields in ({'selection': 'bounded-plan'}, {'memory': MEMORY, 'selection': 'bounded-plan'}, {'reason': 'x'}):
            with self.assertRaises(IdeaError) as caught:
                proposals._default_fill('method', fields)
            self.assertEqual(caught.exception.code, 'invalid_fill')
        self.assertEqual(proposals._default_fill('method', {'memory': MEMORY}), {'memory': MEMORY})

    def test_method_fill_found_needs_preferred_method(self):
        found = dict(status='found', sources=['memory:x'], rationale='r', preferred_method=None)
        with self.assertRaises(IdeaError):
            proposals._default_fill('method', {'memory': found})
        found['preferred_method'] = 'adaptive-slices'
        self.assertEqual(proposals._default_fill('method', {'memory': found})['memory']['preferred_method'], 'adaptive-slices')

    def test_method_proposal_is_memory_only(self):
        self.assertEqual(proposals._default_proposal('method', {'memory': MEMORY}), {'memory': MEMORY})
        with self.assertRaises(IdeaError) as caught:
            proposals._default_proposal('method', {'memory': MEMORY, 'selection': 'bounded-plan'})
        self.assertEqual(caught.exception.code, 'invalid_proposal')


class MemoryTests(unittest.TestCase):
    def found(self, **extra):
        return dict(status='found', sources=['memory:pref'], rationale='Chosen before', **extra)

    def test_found_requires_preferred_method(self):
        with self.assertRaises(IdeaError):
            source.validate_memory(self.found())
        with self.assertRaises(IdeaError):
            source.validate_memory(self.found(preferred_method=None))
        with self.assertRaises(IdeaError):
            source.validate_memory(self.found(preferred_method='nonsense'))
        checked = source.validate_memory(self.found(preferred_method='appetite-led'))
        self.assertEqual(checked['preferred_method'], 'appetite-led')

    def test_varied_with_null_preferred_method(self):
        self.assertEqual(source.validate_memory(MEMORY), MEMORY)
        with self.assertRaises(IdeaError):
            source.validate_memory(dict(MEMORY, preferred_method='bounded-plan'))

    def test_method_proposal_validation(self):
        self.assertEqual(source.validate_proposal('method', {'memory': MEMORY}), {'memory': MEMORY})
        with self.assertRaises(IdeaError) as caught:
            source.validate_proposal('method', {'memory': MEMORY, 'selection': 'bounded-plan'})
        self.assertEqual(caught.exception.code, 'invalid_proposal')


class RecordTests(unittest.TestCase):
    def test_discovery_record_accepted(self):
        proposal = dict(problem='p', audience='a', workaround='w', evidence='e', kill_criteria='k',
                        challenges=[{'challenge': 'c', 'response': 'r'}])
        self.assertEqual(codec.validate_record(record('discovery', proposal))['operation'], 'discovery')

    def test_method_record_memory_only(self):
        self.assertEqual(codec.validate_record(record('method', {'memory': MEMORY}))['operation'], 'method')
        with self.assertRaises(IdeaError):
            codec.validate_record(record('method', {'memory': MEMORY, 'selection': 'bounded-plan'}))

    def test_legacy_shape_refused_with_typed_code(self):
        with self.assertRaises(IdeaError) as caught:
            codec.validate_record(record('shape', {'outcome': 'x'}, data={'capture': {'raw_text': 'x'}}))
        self.assertEqual(caught.exception.code, 'unsupported_proposal_version')


if __name__ == '__main__':
    unittest.main()
