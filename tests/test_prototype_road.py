"""Prototype Here, server side end to end, on the production wiring (real owner service, real routes)."""
import http.client
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
import test_agent_launch as launch
from idea_agent_client import AgentClientError
from test_workflow import fields

PNG = b'\x89PNG\r\n\x1a\nprototype-shot'
ZIP = b'PK\x03\x04prototype-bundle'


class PrototypeRoadTests(unittest.TestCase):
    """Borrows the launch fixture's helpers without re-running its tests."""
    for _name in ('setUp', 'start_owner', 'cleanup', 'wire', 'pair', 'browser', 'accept_fields',
                  'install_test_handlers', 'draft'):
        locals()[_name] = launch.AgentLaunchTests.__dict__[_name]
    del _name

    def prepare(self):
        self.install_test_handlers()
        for step in ('priorities', 'method', 'discovery', 'exploration'):
            self.accept_fields(step, fields()[step], 'ready-'+step)

    def ask(self, request='brief-1'):
        state = self.browser('state'); entry = state['proposal_sources']['visual_brief']
        self.assertTrue(entry['available'], entry)
        source = entry['source']
        self.browser('propose', dict(request_id=request, idea_id=self.idea_id, expected_revision=source['accepted_revision'],
            expected_draft_version=source['draft_version'], operation='visual_brief', source_digest=source['source_digest']))
        event = self.agent.events(timeout=0)['events'][0]
        self.assertEqual(event['operation'], 'visual_brief')
        return {k: v for k, v in event.items() if k not in ('data', 'sequence')}

    def upload(self, data, mime, name, correlation):
        """The documented road: AgentClient.asset under the DELIVERED request id."""
        return self.agent.asset(idea_id=self.idea_id, revision=self.browser('state')['revision'],
                                request_id=correlation['request_id'], name=name, mime=mime, data=data)

    def both(self, correlation, stem='proto'):
        return [self.upload(ZIP, 'application/zip', stem+'.zip', correlation)['asset_id'],
                self.upload(PNG, 'image/png', stem+'.png', correlation)['asset_id']]

    def test_unavailable_reply_persists_and_reaches_the_page(self):
        self.prepare(); correlation = self.ask()
        response = self.agent.respond(dict(correlation, proposal={'prototype_skill': 'unavailable'}))
        item = self.browser('state')['proposals'][-1]
        self.assertEqual((item['operation'], item['proposal'], item['proposal_id']),
                         ('visual_brief', {'prototype_skill': 'unavailable'}, response['evidence']['proposal_id']))
        self.assertTrue((self.store/response['evidence']['path']).is_file())
        self.assertFalse(item['stale'])

    def test_full_road_reply_upload_fill_and_the_browser_accepts(self):
        self.prepare(); correlation = self.ask()
        ids = self.both(correlation)
        self.assertEqual(len(set(ids)), 2)
        before = self.browser('state')
        result = self.agent.fill(dict(correlation, fields={'source': 'prototype', 'assets': ids}))
        self.assertEqual((result['fill_sequence'], result['write_state']), (1, 'not_applied'))
        after = self.browser('state')
        self.assertEqual(after['conversation']['operation'], 'visual_brief')
        self.assertEqual(after['conversation']['fills'], [dict(sequence=1, fields={'source': 'prototype', 'assets': ids})])
        self.assertEqual((after['revision'], after['draft_version'], after['accepted']['visualize']),
                         (before['revision'], before['draft_version'], before['accepted']['visualize']))
        # The terminal then replies; a reply closes the request, so a fill must come BEFORE it (page/server note).
        self.agent.respond(dict(correlation, proposal={'prototype_skill': 'available'}))
        with self.assertRaises(AgentClientError) as late:
            self.agent.fill(dict(correlation, fields={'source': 'prototype', 'assets': ids}))
        self.assertEqual(late.exception.code, 'request_closed')
        replied = self.browser('state')
        self.assertEqual(replied['proposals'][-1]['proposal'], {'prototype_skill': 'available'})
        self.assertFalse(replied['proposals'][-1]['acceptance_eligible'])
        self.assertIsNone(replied['conversation'])
        after = replied
        # The fill never accepts: the human's click does.
        payload = dict(request_id='proto-accept', idea_id=self.idea_id, expected_revision=after['revision'],
            expected_draft_version=after['draft_version'], step='visualize', proposal_id=None, expected_backlog_revision=None,
            fields=dict(disposition='accepted_set', reason=None, source='prototype', design_set_id=None, brief_evidence_id=None),
            design_set_id=None, asset_ids=ids)
        accepted = self.browser('visual-set/accept', payload)
        done = self.browser('state')['accepted']['visualize']
        self.assertEqual((done['disposition'], done['source'], done['design_set_id']),
                         ('accepted_set', 'prototype', accepted['design_set_id']))

    def test_order_upload_then_fill_while_open_then_reply_last(self):
        self.prepare(); correlation = self.ask('brief-order')
        ids = self.both(correlation, 'o')
        self.assertEqual(self.agent.fill(dict(correlation, fields={'source': 'prototype', 'assets': ids}))['fill_sequence'], 1)
        open_state = self.browser('state')
        self.assertEqual(open_state['conversation']['fills'], [dict(sequence=1, fields={'source': 'prototype', 'assets': ids})])
        self.assertEqual(open_state['proposals'], [])
        self.agent.respond(dict(correlation, proposal={'prototype_skill': 'available'}))
        replied = self.browser('state')
        self.assertEqual(replied['proposals'][-1]['proposal'], {'prototype_skill': 'available'})
        self.assertIsNone(replied['conversation'])
        with self.assertRaises(AgentClientError) as late:
            self.agent.fill(dict(correlation, fields={'source': 'prototype', 'assets': ids}))
        self.assertEqual(late.exception.code, 'request_closed')

    def test_upload_retry_is_idempotent_and_second_png_is_refused(self):
        self.prepare(); correlation = self.ask('brief-retry')
        first = self.upload(PNG, 'image/png', 'r.png', correlation)
        again = self.upload(PNG, 'image/png', 'r.png', correlation)
        self.assertEqual(first['asset_id'], again['asset_id'])
        # A different png under the same request and type is a conflict: the first one stands.
        with self.assertRaises(AgentClientError) as other:
            self.upload(PNG+b'-two', 'image/png', 'r2.png', correlation)
        self.assertEqual(other.exception.code, 'request_conflict')
        self.assertNotEqual(other.exception.write_state, 'committed_uncertain')

    def test_upload_without_an_open_delivered_visual_brief_is_refused(self):
        self.prepare(); correlation = self.ask('brief-open')
        revision = self.browser('state')['revision']
        with self.assertRaises(AgentClientError) as caught:
            self.agent.asset(idea_id=self.idea_id, revision=revision, request_id='never-asked', name='x.png',
                             mime='image/png', data=PNG)
        self.assertEqual(caught.exception.code, 'request_not_found')
        self.assertNotEqual(caught.exception.write_state, 'committed_uncertain')
        self.agent.respond(dict(correlation, proposal={'prototype_skill': 'available'}))
        with self.assertRaises(AgentClientError) as closed:
            self.upload(PNG, 'image/png', 'late.png', correlation)
        self.assertEqual(closed.exception.code, 'request_closed')
        self.assertEqual(self.browser('state')['revision'], revision)

    def test_fill_with_one_asset_is_refused(self):
        self.prepare(); correlation = self.ask('brief-one')
        ids = self.both(correlation)
        for bad in (ids[:1], ids+ids[:1], []):
            with self.subTest(n=len(bad)), self.assertRaises(AgentClientError) as caught:
                self.agent.fill(dict(correlation, fields={'source': 'prototype', 'assets': bad}))
            self.assertEqual(caught.exception.code, 'invalid_fill')

    def test_agent_cannot_accept_or_fill_more_than_the_prototype_set(self):
        self.prepare(); correlation = self.ask()
        for bad in ({'disposition': 'accepted_set'}, {'source': 'claude_design', 'assets': []},
                    {'source': 'prototype'}, {'disposition': 'skipped', 'source': 'prototype', 'assets': []}):
            with self.subTest(bad=bad), self.assertRaises(AgentClientError) as caught:
                self.agent.fill(dict(correlation, fields=bad))
            self.assertEqual(caught.exception.code, 'invalid_fill')
        state = self.browser('state')
        self.assertEqual(state['accepted']['visualize']['disposition'] if state['accepted'].get('visualize') else None, None)
        for route in ('accept', 'visual-set/accept', 'visual-disposition'):
            conn = http.client.HTTPConnection('127.0.0.1', self.owner.server.server_port, timeout=5)
            try:
                headers = dict(self.agent._AgentClient__channel._headers(), **{'Content-Type': 'application/json'})
                conn.request('POST', '/agent/v1/'+route, '{}', headers)
                self.assertGreaterEqual(conn.getresponse().status, 400, route)
            finally: conn.close()


if __name__ == '__main__':
    unittest.main()
