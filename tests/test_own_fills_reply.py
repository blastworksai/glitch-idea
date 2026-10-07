"""A reply is not stale merely because this request's own fills moved the draft (CP6 J6f)."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
import idea_bridge
from idea_agent_client import AgentClientError
import test_prototype_road as road
from test_workflow import fields

BLANK_VISUALIZE = dict(disposition=None, reason=None, design_set_id=None, brief_evidence_id=None)


class OwnFillsReplyTests(unittest.TestCase):
    for _name in ('setUp', 'start_owner', 'cleanup', 'wire', 'pair', 'browser', 'accept_fields',
                  'install_test_handlers', 'draft', 'prepare', 'ask', 'upload', 'both'):
        locals()[_name] = road.PrototypeRoadTests.__dict__[_name]
    del _name

    def page_save(self, step, value, request):
        """What the page does with an applied fill: save the whole buffer as the step draft."""
        return self.draft(step, value, request)

    def road_to_reply(self, request, human_edit=None):
        self.prepare(); correlation = self.ask(request)
        ids = self.both(correlation)
        self.agent.fill(dict(correlation, fields={'source': 'prototype', 'assets': ids}))
        self.page_save('visualize', dict(BLANK_VISUALIZE, source='prototype', assets=ids), request+'-save')
        if human_edit:
            self.page_save('visualize', dict(BLANK_VISUALIZE, source='prototype', assets=ids, **human_edit), request+'-human')
        return correlation

    def test_csp_allows_blob_images_and_nothing_broader(self):
        csp = idea_bridge.SECURITY_HEADERS['Content-Security-Policy'] if hasattr(idea_bridge, 'SECURITY_HEADERS') else None
        if csp is None:
            csp = next(v for v in vars(idea_bridge).values() if isinstance(v, dict)
                       and 'Content-Security-Policy' in v)['Content-Security-Policy']
        self.assertIn("img-src 'self' blob:", csp)
        self.assertNotIn('data:', csp); self.assertNotIn('http', csp); self.assertNotIn('*', csp)
        self.assertIn("default-src 'self'", csp)

    def test_full_order_asset_fill_page_save_then_reply_succeeds(self):
        correlation = self.road_to_reply('own-1')
        self.assertGreater(self.browser('state')['draft_version'], correlation['draft_version'])
        response = self.agent.respond(dict(correlation, proposal={'prototype_skill': 'available'}))
        self.assertEqual(response['write_state'], 'applied')
        replied = self.browser('state')
        self.assertEqual(replied['proposals'][-1]['proposal'], {'prototype_skill': 'available'})
        self.assertFalse(replied['proposals'][-1]['stale'])

    def test_human_edit_between_fill_and_reply_is_still_stale(self):
        correlation = self.road_to_reply('own-2', human_edit={'reason': 'my own words'})
        with self.assertRaises(AgentClientError) as caught:
            self.agent.respond(dict(correlation, proposal={'prototype_skill': 'available'}))
        self.assertEqual(caught.exception.code, 'stale_source')

    def test_changed_accepted_revision_is_still_stale(self):
        correlation = self.road_to_reply('own-3')
        self.accept_fields('priorities', dict(urgency=1, importance=1), 'reaccept')
        with self.assertRaises(AgentClientError) as caught:
            self.agent.respond(dict(correlation, proposal={'prototype_skill': 'available'}))
        self.assertEqual(caught.exception.code, 'stale_source')

    def test_discovery_reply_after_its_own_fills_succeeds_and_a_human_edit_does_not(self):
        for edit, request in ((False, 'disc-1'), (True, 'disc-2')):
            with self.subTest(edit=edit):
                self.install_test_handlers()
                if not getattr(self, '_primed', False):
                    for step in ('priorities', 'method'):
                        self.accept_fields(step, fields()[step], 'ready-'+step)
                    self._primed = True
                state = self.browser('state'); source = state['proposal_sources']['discovery']['source']
                self.browser('propose', dict(request_id=request, idea_id=self.idea_id, expected_revision=source['accepted_revision'],
                    expected_draft_version=source['draft_version'], operation='discovery', source_digest=source['source_digest']))
                event = self.agent.events(timeout=0)['events'][-1]
                correlation = {k: v for k, v in event.items() if k not in ('data', 'sequence')}
                self.agent.fill(dict(correlation, fields={'problem': 'Filled by the terminal '+request}))
                base = dict(problem='', audience='', workaround='', evidence='', kill_criteria='', challenges=[])
                self.page_save('discovery', dict(base, problem='Filled by the terminal '+request), request+'-s')
                if edit:
                    self.page_save('discovery', dict(base, problem='Filled by the terminal '+request, audience='human'), request+'-h')
                reply = dict(correlation, proposal=fields()['discovery'])
                if edit:
                    with self.assertRaises(AgentClientError) as caught:
                        self.agent.respond(reply)
                    self.assertEqual(caught.exception.code, 'stale_source')
                else:
                    self.assertEqual(self.agent.respond(reply)['write_state'], 'applied')


if __name__ == '__main__':
    unittest.main()
