"""real local HTTP agent transport composition, no owner wiring."""
import copy
import http.client
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
from idea_bridge import BridgeServer, BridgeError, Response, MAX_AGENT_WAITS, MAX_HANDLERS, MAX_TOTAL_HANDLERS
from idea_proposals import Broker
from idea_service import Service, TrustedContext
from idea_sessions import SessionPolicy
from idea_store import Store
from idea_steps import TrustedRoute
from idea_workflow import source_digest
from test_agent_bridge import SHAPE


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.workspace = self.root / 'workspace'; self.workspace.mkdir()
        self.store = Store(self.root / 'ideas', observer='Operator'); self.sid = self.store.create_session()
        self.saved = []; self.calls = []; self.waiting = threading.Condition(); self.wait_threads = set()
        self.broker = Broker(validate_source=lambda _, value: value, persist_proposal=self.persist)
        self.policy = SessionPolicy(self.factory, lambda _: None, namespace='transport',
            cancel=self.broker.cancel, agent_open=self.broker.open, agent_state=self.broker.status)
        self.bid = self.policy.open_binding('Operator', self.sid)
        self.credentials = self.policy.agent_credentials(self.bid)
        self.server = BridgeServer(self.policy, agent=self.agent, body_deadline=1)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True); self.worker.start()
        self.addCleanup(self.stop)
        code = self.policy.issue_pairing(self.bid)
        status, body, headers = self.http('/api/v1/pair', {'code': code},
            headers={'Origin': self.server.origin})
        self.assertEqual(status, 200)
        self.cookie = headers['Set-Cookie'].split(';')[0]; self.csrf = body['csrf_token']; self.tab = body['tab_secret']

    def factory(self, record):
        return Service(self.store, {}, TrustedContext(record['actor'], record['receipt_session_id'], record['selected_idea_id']))

    def persist(self, evidence):
        self.saved.append(copy.deepcopy(evidence))
        return dict(proposal_id='proposal_fixture', sha256='a'*64)

    def stop(self):
        self.broker.cancel(self.bid, self.credentials['generation'])
        self.server.stop_admission(); self.server.shutdown(); self.server.drain(); self.server.server_close(); self.worker.join(2)

    def agent_headers(self, credentials=None):
        value = credentials or self.credentials
        return {'Authorization': 'Bearer '+value['token'], 'X-Idea-Agent-Binding': value['binding_id'],
                'X-Idea-Agent-Generation': value['generation']}

    def browser_headers(self, write=False):
        headers = {'Cookie': self.cookie, 'X-Idea-Binding': self.bid, 'X-Idea-Tab': self.tab}
        if write: headers.update({'Origin': self.server.origin, 'X-CSRF-Token': self.csrf})
        return headers

    def http(self, path, body=None, *, method=None, headers=None, raw=None):
        values = {'Host': self.server.host}; values.update(headers or {})
        if body is not None or raw is not None: values['Content-Type'] = 'application/json'
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        try:
            conn.request(method or ('POST' if body is not None or raw is not None else 'GET'), path,
                         raw if raw is not None else None if body is None else json.dumps(body), values)
            response = conn.getresponse(); return response.status, json.loads(response.read()), dict(response.getheaders())
        finally: conn.close()

    def events_path(self, after=0, timeout=0):
        return f'/agent/v1/events?session_id={self.sid}&after={after}&timeout={timeout}'

    def agent(self, operation, context, payload):
        self.calls.append(operation)
        if operation == 'events':
            self.assertFalse(context.binding.lock._is_owned())
            # Instrument the actual Condition wait, not merely callback entry.
            return self.broker.events(context.binding_id, context.generation, **payload)
        self.assertTrue(context.binding.lock._is_owned())
        if operation == 'respond': return self.broker.respond(context.binding_id, context.generation, payload)
        return self.policy.close_agent(context)

    def instrument_waits(self):
        original = self.broker.condition.wait
        def wait(timeout):
            with self.waiting:
                self.wait_threads.add(threading.get_ident()); self.waiting.notify_all()
            return original(timeout)
        self.broker.condition.wait = wait

    def start_waits(self, count, after=0):
        self.instrument_waits(); results = []; threads = []
        credentials = copy.deepcopy(self.credentials)
        for _ in range(count):
            thread = threading.Thread(target=lambda: results.append(self.http(self.events_path(after, 25),
                headers=self.agent_headers(credentials))))
            thread.start(); threads.append(thread)
        with self.waiting:
            self.assertTrue(self.waiting.wait_for(lambda: len(self.wait_threads) >= count, timeout=2))
        return threads, results

    def capture(self):
        status, result, _ = self.http('/api/v1/capture', dict(request_id='capture', raw_text='Original words',
            workspace=dict(name='Explicit', path=str(self.workspace), confirmed=True)), headers=self.browser_headers(True))
        self.assertEqual(status, 200); self.idea_id = result['idea_id']; return result

    def pending(self):
        self.capture()
        source = dict(accepted_revision=1, draft_version=0, data={'capture': {'raw_text': 'Original words'}})
        envelope = dict(request_id='proposal1', idea_id=self.idea_id, expected_revision=1,
            expected_draft_version=0, operation='shape', source_digest=source_digest('shape', 1, source['data']))
        self.broker.enqueue(self.bid, self.credentials['generation'], envelope, source)
        status, batch, _ = self.http(self.events_path(), headers=self.agent_headers()); self.assertEqual(status, 200)
        event = batch['events'][0]
        return dict({k:v for k,v in event.items() if k not in ('data', 'sequence')}, proposal=copy.deepcopy(SHAPE))

    def draft(self, request_id='draft'):
        return self.http('/api/v1/draft', dict(request_id=request_id, idea_id=self.idea_id,
            expected_revision=1, expected_draft_version=0, step='priorities',
            fields=dict(urgency=6, importance=None)), headers=self.browser_headers(True))

    def test_authentication_host_and_browser_headers_refused(self):
        path = self.events_path()
        for name in ('Cookie', 'Origin', 'X-CSRF-Token', 'X-Idea-Binding'):
            status, _, _ = self.http(path, headers=dict(self.agent_headers(), **{name: ''}))
            self.assertEqual(status, 403)
        for name in ('Authorization', 'X-Idea-Agent-Binding', 'X-Idea-Agent-Generation'):
            headers = self.agent_headers(); headers.pop(name)
            self.assertEqual(self.http(path, headers=headers)[0], 401)
        self.assertEqual(self.http(path, headers=dict(self.agent_headers(), Host='localhost:1234'))[0], 403)
        self.assertEqual(self.http(path, headers=dict(self.agent_headers(), Authorization='Bearer '+'f'*64))[0], 401)
        self.assertEqual(self.http(path, headers=self.browser_headers())[0], 403)
        self.assertEqual(self.calls, [])

    def test_duplicate_private_headers_rejected(self):
        for name in ('Authorization', 'X-Idea-Agent-Binding', 'X-Idea-Agent-Generation', 'Host'):
            values = dict(self.agent_headers(), Host=self.server.host)
            request = 'GET '+self.events_path()+' HTTP/1.0\r\n'
            request += ''.join(k+': '+v+'\r\n' for k,v in values.items())
            request += name+': '+values[name]+'\r\n\r\n'
            with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=3) as sock:
                sock.sendall(request.encode()); chunks = []
                while True:
                    value = sock.recv(65536)
                    if not value: break
                    chunks.append(value)
            raw = b''.join(chunks)
            self.assertIn(b'400 Bad Request', raw); self.assertIn(b'ambiguous_headers', raw)
        self.assertEqual(self.calls, [])

    def test_fixed_methods_paths_query_and_json_keys(self):
        for path in ('/agent/v1/events/', '/agent/v1/%65vents', '/agent/v1/other'):
            status, error, _ = self.http(path, headers=self.agent_headers())
            self.assertEqual(status, 403)
            self.assertEqual(error['code'], 'agent_headers_refused')
            self.assertEqual(self.http(path)[0], 404)
        self.assertEqual(self.http(self.events_path(), {}, headers=self.agent_headers())[0], 405)
        self.assertEqual(self.http('/agent/v1/respond', headers=self.agent_headers())[0], 405)
        for tail in ('', '?', '?session_id='+self.sid, '?session_id='+self.sid+'&after=0&timeout=26',
                     '?session_id='+self.sid+'&after=0&timeout=nan',
                     '?session_id='+self.sid+'&after=0&timeout=0&x=1',
                     '?session_id='+self.sid+'&after=0&after=0'):
            self.assertEqual(self.http('/agent/v1/events'+tail, headers=self.agent_headers())[0], 400)
        for body in ({}, {'session_id': self.sid, 'path': '/private'}, {'session_id': True}):
            self.assertNotEqual(self.http('/agent/v1/session-close', body, headers=self.agent_headers())[0], 200)
        self.assertEqual(self.http('/agent/v1/session-close?', {'session_id':self.sid}, headers=self.agent_headers())[0], 400)
        self.assertEqual(self.calls, [])

    def test_real_reply_correlation_replay_and_json_duplicate_refusal(self):
        response = self.pending()
        for key, value in [('idea_id', 'idea_'+'f'*32), ('operation', 'method'),
                           ('accepted_revision', True), ('draft_version', 2), ('source_digest', 'b'*64)]:
            changed = dict(response, **{key:value})
            status, error, _ = self.http('/agent/v1/respond', changed, headers=self.agent_headers())
            self.assertEqual(status, 400); self.assertEqual(error['write_state'], 'not_applied')
        self.assertEqual(self.http('/agent/v1/respond', dict(response, extra=True), headers=self.agent_headers())[0], 400)
        self.assertEqual(self.http('/agent/v1/respond', raw='{"session_id":"x","session_id":"y"}', headers=self.agent_headers())[0], 400)
        status, result, _ = self.http('/agent/v1/respond', response, headers=self.agent_headers())
        self.assertEqual(status, 200); self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.http('/agent/v1/respond', response, headers=self.agent_headers())[1], result)
        changed = copy.deepcopy(response); changed['proposal']['outcome'] = 'Changed'
        self.assertEqual(self.http('/agent/v1/respond', changed, headers=self.agent_headers())[0], 409)
        self.assertEqual(len(self.saved), 1)

    def test_all_wait_slots_leave_full_browser_budget_available(self):
        self.capture(); threads, results = self.start_waits(MAX_AGENT_WAITS)
        try:
            self.assertEqual(self.http(self.events_path(timeout=25), headers=self.agent_headers())[0], 503)
            # Reserve seven ordinary slots: the remaining ordinary slot still
            # accepts a real Store mutation while every wait slot is occupied.
            for _ in range(MAX_HANDLERS-1): self.server.slots.acquire()
            try:
                status, result, _ = self.draft(); self.assertEqual(status, 200)
                self.assertEqual(result['draft_version'], 1)
            finally:
                for _ in range(MAX_HANDLERS-1): self.server.slots.release()
            with self.server._admission: self.assertLessEqual(self.server._admitted, MAX_TOTAL_HANDLERS)
        finally:
            self.policy.close_agent(self.policy.authorize_agent(self.request_info()))
            for thread in threads: thread.join(3); self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), MAX_AGENT_WAITS)
        self.assertTrue(all(result[0] == 401 for result in results))

    def request_info(self):
        from idea_bridge import RequestInfo
        return RequestInfo('GET', '/agent/v1/events', {k.lower():v for k,v in self.agent_headers().items()}, self.server.origin)

    def test_rotation_cancels_wait_and_refuses_old_reply(self):
        response = self.pending(); old = copy.deepcopy(self.credentials)
        threads, results = self.start_waits(1, after=1)
        self.policy.open_binding('Operator', self.sid, self.idea_id, binding_id=self.bid, resume=True)
        self.credentials = self.policy.agent_credentials(self.bid)
        for thread in threads: thread.join(3); self.assertFalse(thread.is_alive())
        self.assertEqual(results[0][0:2], (401, {'ok':False, 'code':'agent_unauthorized'}))
        self.assertEqual(self.http('/agent/v1/respond', response, headers=self.agent_headers(old))[0], 401)
        self.assertEqual(self.saved, [])
        self.assertEqual(self.http(self.events_path(), headers=self.agent_headers())[1]['events'], [])

    def test_http_close_keeps_browser_cookie_and_real_draft_usable(self):
        self.capture(); threads, results = self.start_waits(1)
        status, result, _ = self.http('/agent/v1/session-close', {'session_id':self.sid}, headers=self.agent_headers())
        self.assertEqual(status, 200); self.assertEqual(result['agent_status'], 'disconnected')
        for thread in threads: thread.join(3); self.assertFalse(thread.is_alive())
        self.assertEqual(results[0][0], 401)
        self.assertEqual(self.http(self.events_path(), headers=self.agent_headers())[0], 401)
        self.assertEqual(self.draft()[0], 200)
        status, state, _ = self.http('/api/v1/state', headers=self.browser_headers())
        self.assertEqual(status, 200); self.assertEqual(state['drafts']['priorities']['urgency'], 6)
        self.assertEqual(self.http('/api/v1/session', headers=self.browser_headers())[1]['csrf_token'], self.csrf)

    def test_callback_failure_is_redacted_and_uncertain_publication_explicit(self):
        response = self.pending()
        def fail(_): raise OSError('secret-token /private/path')
        self.broker.persist_proposal = fail
        status, result, _ = self.http('/agent/v1/respond', response, headers=self.agent_headers())
        self.assertEqual(status, 500); self.assertEqual(result['write_state'], 'committed_uncertain')
        self.assertTrue(result['committed']); self.assertNotIn('secret-token', json.dumps(result))
        self.server.agent = lambda *_: (_ for _ in ()).throw(RuntimeError('secret-token'))
        status, result, _ = self.http(self.events_path(), headers=self.agent_headers())
        self.assertEqual((status, result), (500, {'ok':False, 'code':'internal_error'}))

    def test_unwired_callback_fails_closed_and_total_admission_bounded(self):
        self.server.agent = None
        self.assertEqual(self.http(self.events_path(), headers=self.agent_headers())[0], 503)
        for _ in range(MAX_TOTAL_HANDLERS): self.server.total_slots.acquire()
        try: self.assertEqual(self.http('/api/v1/state', headers=self.browser_headers())[0], 503)
        finally:
            for _ in range(MAX_TOTAL_HANDLERS): self.server.total_slots.release()
        with self.assertRaises(ValueError): BridgeServer(agent=True)

    def test_request_capacity_is_http_503_without_publication_or_private_data(self):
        # One retained completed record exhausts this bounded fixture table.
        # The actual browser route calls Broker.enqueue and the HTTP error path.
        with patch('idea_proposals.MAX_RECORDS', 1):
            response = self.pending()
            self.assertEqual(self.http('/agent/v1/respond', response, headers=self.agent_headers())[0], 200)
            source = dict(accepted_revision=1, draft_version=0,
                          data={'capture': {'raw_text': 'Original words'}})
            def propose(binding, request, payload):
                return self.broker.enqueue(self.bid, self.credentials['generation'], payload, source)
            self.server.routes['propose'] = TrustedRoute('propose', propose)
            payload = dict(request_id='over-capacity', idea_id=self.idea_id, expected_revision=1,
                expected_draft_version=0, operation='shape', source_digest=source_digest('shape', 1, source['data']))
            status, error, _ = self.http('/api/v1/propose', payload, headers=self.browser_headers(True))
        self.assertEqual(status, 503)
        self.assertEqual(error['code'], 'request_capacity')
        self.assertEqual(error['write_state'], 'not_applied')
        self.assertEqual(error['request_id'], 'over-capacity')
        self.assertEqual(len(self.saved), 1)
        for private in (self.credentials['token'], self.credentials['generation'], 'Original words', str(self.root)):
            self.assertNotIn(private, json.dumps(error))

    def test_reverse_agent_headers_and_authorization_refused_before_browser_reads(self):
        cases = [('X-Idea-Agent-Binding', self.bid),
                 ('X-Idea-Agent-Generation', self.credentials['generation']),
                 ('Authorization', 'Bearer '+self.credentials['token'])]
        with patch.object(self.store, 'transaction', side_effect=AssertionError('Browser state must not be read')):
            for name, valid in cases:
                for value in ('', 'malformed', valid):
                    for path in ('/api/v1/state', '/api/v1/session', '/index.html', '/unknown'):
                        with self.subTest(name=name, value_type='empty' if not value else 'present', path=path):
                            status, error, _ = self.http(path, headers=dict(self.browser_headers(), **{name:value}))
                            self.assertEqual(status, 403)
                            self.assertEqual(error['code'], 'authorization_refused' if name=='Authorization' else 'agent_headers_refused')
                            self.assertNotIn(self.credentials['token'], json.dumps(error))
        self.assertEqual(self.http('/api/v1/state', headers=self.browser_headers())[0], 200)

    def test_fetch_metadata_refused_on_both_private_channels_but_browser_metadata_allowed(self):
        controls = []
        def control(request, payload):
            if request.header('Authorization') != 'Bearer fixture-owner':
                raise BridgeError('owner_unauthorized', 401)
            controls.append(request.path)
            return Response(dict(ok=True, code='ok'))
        self.server.control = control
        for name in ('Sec-Fetch-Site', 'Sec-Fetch-Mode'):
            for value in ('', 'malformed', 'same-origin' if name=='Sec-Fetch-Site' else 'cors'):
                status, error, _ = self.http(self.events_path(), headers=dict(self.agent_headers(), **{name:value}))
                self.assertEqual((status, error['code']), (403, 'private_browser_metadata_refused'))
                status, error, _ = self.http('/control/v1/probe', {},
                    headers={'Authorization':'Bearer fixture-owner', name:value})
                self.assertEqual((status, error['code']), (403, 'private_browser_metadata_refused'))
        self.assertEqual(controls, [])
        self.assertEqual(self.http('/api/v1/state', headers=dict(self.browser_headers(),
            **{'Sec-Fetch-Site':'same-origin', 'Sec-Fetch-Mode':'cors'}))[0], 200)
        self.assertEqual(self.http(self.events_path(), headers=self.agent_headers())[0], 200)
        self.assertEqual(self.http('/control/v1/probe', {}, headers={'Authorization':'Bearer fixture-owner'})[0], 200)
        self.assertEqual(controls, ['/control/v1/probe'])
        for name in ('X-Idea-Agent-Binding', 'X-Idea-Agent-Generation'):
            for value in ('', 'malformed', self.agent_headers()[name]):
                self.assertEqual(self.http('/control/v1/probe', {},
                    headers={'Authorization':'Bearer fixture-owner', name:value})[0], 403)
        self.assertEqual(controls, ['/control/v1/probe'])


if __name__ == '__main__': unittest.main()
