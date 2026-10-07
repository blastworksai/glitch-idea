"""Hand release (R3): POST /api/v1/conversation/release cancels one step's open request, never the binding."""
import json
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_agent_launch as launch
from idea_agent_client import AgentClientError
from test_workflow import fields

VARIED = dict(status='varied', sources=[], rationale=None, preferred_method=None)


@unittest.skipUnless(os.name == 'posix', 'Native owner ACLs remain unqualified')
class HandReleaseTests(launch.AgentLaunchTests):
    def release(self, step, ok=True, expected_status=None):
        return self.browser('conversation/release', dict(step=step), ok=ok, expected_status=expected_status)

    def enqueue_exploration(self, request='exploration-1'):
        self.ready()
        self.accept_fields('discovery', fields()['discovery'], 'hand-discovery-accept')
        state = self.browser('state'); source = state['proposal_sources']['exploration']['source']
        self.assertIsNotNone(source)
        self.browser('propose', dict(request_id=request, idea_id=self.idea_id,
            expected_revision=source['accepted_revision'], expected_draft_version=source['draft_version'],
            operation='exploration', source_digest=source['source_digest']))
        event = self.agent.events(timeout=0)['events'][0]
        return {k: v for k, v in event.items() if k not in ('data', 'sequence')}

    def cancelled(self, correlation):
        with self.assertRaises(AgentClientError) as caught:
            self.agent.fill(dict(correlation, fields={'problem': 'Too late'}))
        self.assertEqual(caught.exception.code, 'request_cancelled')
        with self.assertRaises(AgentClientError) as caught:
            self.agent.respond(dict(correlation, proposal=fields()['discovery']))
        self.assertEqual(caught.exception.code, 'request_cancelled')

    def test_release_cancels_one_request_and_binding_stays_usable(self):
        correlation = self.enqueue()
        result = self.release('discovery')
        self.assertEqual((result['write_state'], result['released'], result['hand']), ('applied', 1, True))
        self.cancelled(correlation)
        self.assertIsNone(self.browser('state')['conversation'])
        state = self.browser('state')
        self.assertEqual(state['agent_status'], 'connected')
        # The same binding still serves another step's request.
        reply = self.operation_reply('memory', VARIED, 'memory-after-release')
        self.assertEqual(reply['code'], 'ok')
        self.assertEqual(self.agent.events(timeout=0)['agent_status'], 'connected')

    def test_hand_persists_across_state_reads_and_resume_and_is_draft_meta_only(self):
        self.ready()
        before = self.browser('state')
        self.assertEqual(before['hand'], dict(discovery=False, exploration=False))
        result = self.release('discovery')
        after = self.browser('state')
        self.assertEqual(after['hand'], dict(discovery=True, exploration=False))
        self.assertEqual((after['revision'], after['draft_version'], after['drafts'], after['accepted']),
                         (before['revision'], before['draft_version'], before['drafts'], before['accepted']))
        self.assertEqual(result['draft_version'], before['draft_version'])
        # A fresh pairing (another tab / reload) reads the same flag from the store.
        self.opened = self.client.open_binding('resume', self.opened['binding_id']); self.pair()
        self.assertEqual(self.browser('state')['hand'], dict(discovery=True, exploration=False))

    def test_no_new_automatic_request_for_a_handed_step(self):
        self.ready()
        self.release('discovery')
        state = self.browser('state'); source = state['proposal_sources']['discovery']['source']
        error = self.browser('propose', dict(request_id='auto-after-hand', idea_id=self.idea_id,
            expected_revision=source['accepted_revision'], expected_draft_version=source['draft_version'],
            operation='discovery', source_digest=source['source_digest']), ok=False, expected_status=409)
        self.assertEqual(error['code'], 'hand_released')
        self.assertEqual(self.agent.events(timeout=0)['events'], [])
        # Other operations are unaffected.
        self.assertEqual(self.operation_reply('memory', VARIED, 'memory-while-hand')['code'], 'ok')

    def test_release_after_acceptance_is_a_no_op(self):
        correlation = self.enqueue()
        self.accept_fields('discovery', fields()['discovery'], 'hand-accepted')
        before = self.browser('state')
        result = self.release('discovery')
        self.assertEqual((result['write_state'], result['released'], result['hand']), ('no_op', 0, False))
        after = self.browser('state')
        self.assertEqual(after['hand'], dict(discovery=False, exploration=False))
        self.assertEqual((after['revision'], after['draft_version']), (before['revision'], before['draft_version']))

    def test_double_release_is_idempotent(self):
        self.enqueue()
        first = self.release('discovery')
        second = self.release('discovery')
        self.assertEqual((first['write_state'], first['released']), ('applied', 1))
        self.assertEqual((second['write_state'], second['released'], second['hand']), ('no_op', 0, True))
        self.assertEqual(self.browser('state')['hand']['discovery'], True)

    def test_exploration_can_be_released_and_is_independent_of_discovery(self):
        correlation = self.enqueue_exploration()
        result = self.release('exploration')
        self.assertEqual((result['released'], result['write_state']), (1, 'applied'))
        with self.assertRaises(AgentClientError) as caught:
            self.agent.respond(dict(correlation, proposal={}))
        self.assertEqual(caught.exception.code, 'request_cancelled')
        self.assertEqual(self.browser('state')['hand'], dict(discovery=False, exploration=True))

    def test_other_steps_and_bad_bodies_are_refused(self):
        self.ready()
        for step in ('capture', 'priorities', 'method', 'visualize', 'assess', 'review', 'shape', 'memory', '', None):
            error = self.release(step, ok=False)
            self.assertEqual(error['code'], 'invalid_input', step)
        for body in ({}, {'step': 'discovery', 'extra': 1}, {'steps': 'discovery'}):
            self.assertEqual(self.browser('conversation/release', body, ok=False)['code'], 'invalid_input')
        self.assertEqual(self.browser('state')['hand'], dict(discovery=False, exploration=False))

    def test_unauthenticated_no_csrf_and_no_tab_secret_are_refused(self):
        self.ready()
        binding = {'X-Idea-Binding': self.opened['binding_id']}
        full = dict(binding, Cookie=self.cookie, **{'X-Idea-Tab': self.tab, 'X-CSRF-Token': self.csrf})
        body = dict(step='discovery')
        cases = {
            'no credentials': ({}, 401),
            'binding only': (binding, 401),
            'no tab secret': ({k: v for k, v in full.items() if k != 'X-Idea-Tab'}, 401),
            'no cookie': ({k: v for k, v in full.items() if k != 'Cookie'}, 401),
            'no csrf': ({k: v for k, v in full.items() if k != 'X-CSRF-Token'}, 403),
            'wrong csrf': (dict(full, **{'X-CSRF-Token': 'wrong'}), 403),
            'wrong tab': (dict(full, **{'X-Idea-Tab': 'wrong'}), 401),
        }
        for name, (headers, status) in cases.items():
            code, result, _ = self.wire('/api/v1/conversation/release', body, headers)
            self.assertEqual(code, status, (name, result))
            self.assertFalse(result.get('ok'), name)
        self.assertEqual(self.browser('state')['hand'], dict(discovery=False, exploration=False))
        # A GET is not a write route.
        code, result, _ = self.wire('/api/v1/conversation/release', None, full)
        self.assertEqual(code, 405, result)
        code, result, _ = self.wire('/api/v1/release', body, full)
        self.assertEqual(code, 404, result)


for _name in dir(launch.AgentLaunchTests):
    if _name.startswith('test_') and _name not in HandReleaseTests.__dict__:
        setattr(HandReleaseTests, _name, None)  # reuse the fixtures, not the inherited tests

if __name__ == '__main__':
    unittest.main()
