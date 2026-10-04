"""Per-tab secret: a cookie alone (cookies are not port-scoped) never authorises a browser request."""
import http.client
import io
import json
import logging
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
from idea_bridge import BridgeServer
from idea_service import Service, TrustedContext
from idea_sessions import SessionPolicy
from idea_store import Store


class TabSecretTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name); (root / 'workspace').mkdir()
        self.workspace = root / 'workspace'
        self.store = Store(root / 'ideas', observer='Operator')
        self.records = {}

        def factory(record):
            return Service(self.store, {}, TrustedContext(record['actor'], record['receipt_session_id'],
                                                           record['selected_idea_id']))
        self.policy = SessionPolicy(factory, lambda record: self.records.__setitem__(record['binding_id'], dict(record)),
                                    namespace='store_one', clock=lambda: 100.)
        self.sid = self.store.create_session()
        self.bid = self.policy.open_binding('Operator', self.sid)
        self.server = BridgeServer(self.policy)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True); self.worker.start()
        self.addCleanup(self.stop)
        self.log = io.StringIO()
        handler = logging.StreamHandler(self.log); logging.getLogger().addHandler(handler)
        self.addCleanup(logging.getLogger().removeHandler, handler)
        self.pair()

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.worker.join()

    def call(self, path, body=None, *, tab='default', cookie=True, csrf=True):
        headers = {'Host': self.server.host, 'X-Idea-Binding': self.bid}
        if cookie: headers['Cookie'] = self.cookie
        if csrf: headers['X-CSRF-Token'] = self.csrf
        if tab == 'default': tab = self.tab
        if tab is not None: headers['X-Idea-Tab'] = tab
        if body is not None: headers.update({'Origin': self.server.origin, 'Content-Type': 'application/json'})
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            conn.request('POST' if body is not None else 'GET', path, None if body is None else json.dumps(body), headers)
            response = conn.getresponse(); raw = response.read()
            return response.status, json.loads(raw), dict(response.getheaders()), raw.decode()
        finally:
            conn.close()

    def pair(self):
        code = self.policy.issue_pairing(self.bid)
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        conn.request('POST', '/api/v1/pair', json.dumps({'code': code}),
                     {'Host': self.server.host, 'Origin': self.server.origin, 'Content-Type': 'application/json',
                      'X-Idea-Binding': self.bid})
        response = conn.getresponse(); raw = response.read(); conn.close()
        self.assertEqual(response.status, 200, raw)
        self.pair_raw = raw.decode(); data = json.loads(raw)
        self.cookie = response.getheader('Set-Cookie').split(';')[0]
        self.csrf, self.tab = data['csrf_token'], data['tab_secret']
        self.assertRegex(self.tab, r'^[0-9a-f]{64}$')
        self.assertNotEqual(self.tab, self.csrf)
        self.assertNotIn(self.tab, response.getheader('Set-Cookie'))

    def test_valid_cookie_and_binding_without_the_tab_secret_is_refused_on_read_and_write(self):
        status, body, _, _ = self.call('/api/v1/session', tab=None)
        self.assertEqual((status, body['code']), (401, 'browser_unauthorized'))
        self.assertNotIn('csrf_token', body)
        status, body, _, _ = self.call('/api/v1/capture', {'request_id': 'r1', 'raw_text': 'x',
                                       'workspace': {'name': 'w', 'path': str(self.workspace), 'confirmed': True}}, tab=None)
        self.assertEqual((status, body['code']), (401, 'browser_unauthorized'))
        self.assertEqual(self.call('/api/v1/state', tab=None)[0], 401)

    def test_wrong_or_malformed_tab_secret_is_refused(self):
        for bad in ('0' * 64, self.tab[:-1], self.tab + '0', 'x', self.csrf):
            self.assertEqual(self.call('/api/v1/session', tab=bad)[0], 401, bad)
        self.assertEqual(self.call('/api/v1/session', tab='é' * 64)[0] in (400, 401), True)

    def test_correct_tab_secret_with_cookie_is_accepted_and_the_cookie_stays_a_second_factor(self):
        status, body, _, _ = self.call('/api/v1/session')
        self.assertEqual((status, body['csrf_token']), (200, self.csrf))
        self.assertEqual(self.call('/api/v1/session', cookie=False)[0], 401)
        self.assertEqual(self.call('/api/v1/state')[0], 200)

    def test_the_secret_appears_only_in_the_pair_response(self):
        self.assertIn(self.tab, self.pair_raw)
        for path in ('/api/v1/session', '/api/v1/state'):
            status, _, headers, raw = self.call(path)
            self.assertEqual(status, 200)
            self.assertNotIn(self.tab, raw); self.assertNotIn(self.tab, json.dumps(headers))
        status, _, headers, raw = self.call('/api/v1/session', tab='0' * 64)
        self.assertNotIn(self.tab, raw + json.dumps(headers))
        self.assertNotIn(self.tab, json.dumps(self.records)); self.assertNotIn(self.tab, self.log.getvalue())

    def test_a_second_pairing_after_replay_rotates_the_secret_and_invalidation_drops_it(self):
        old = self.tab
        self.policy.revoke(self.bid)
        self.assertEqual(self.call('/api/v1/session', tab=old)[0], 401)
        entry = self.policy._entry(self.bid)
        self.assertIsNone(entry.tab_secret)
        self.policy.open_binding('Operator', self.sid, binding_id=self.bid, resume=True)
        self.pair()
        self.assertNotEqual(self.tab, old)
        self.assertEqual(self.call('/api/v1/session', tab=old)[0], 401)
        self.assertEqual(self.call('/api/v1/session')[0], 200)

    def test_browser_headers_on_agent_routes_include_the_tab_header(self):
        from idea_sessions import AGENT_BROWSER_HEADERS
        self.assertIn('X-Idea-Tab', AGENT_BROWSER_HEADERS)


if __name__ == '__main__':
    unittest.main()
