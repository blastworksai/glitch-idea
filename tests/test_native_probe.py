"""Targeted CP0 regression proof by Operator; HTTP tests are not browser evidence."""
import copy
import http.client
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

from native_probe import CORRELATION, Failure, Probe, Server, decode


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name) / 'runtime'
        self.probe = Probe(self.runtime)
        self.server = Server(self.probe)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.cookie, self.csrf = '', ''

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()
        self.temp.cleanup()

    def http(self, path, body=None, headers=None):
        base = {}
        if self.cookie:
            base['Cookie'] = self.cookie
        if body is not None:
            base.update({'Content-Type': 'application/json', 'Origin': self.probe.origin,
                         'X-CSRF-Token': self.csrf})
        base.update(headers or {})
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        conn.request('GET' if body is None else 'POST', path,
                     None if body is None else json.dumps(body), base)
        response = conn.getresponse()
        raw = response.read()
        result = (response.status, json.loads(raw), dict(response.getheaders()))
        conn.close()
        return result

    def agent(self, operation, body=None):
        return self.http('/agent/' + operation, {} if body is None else body,
                         {'Authorization': 'Bearer ' + self.probe.agent_token})

    def pair(self):
        status, reply, headers = self.http('/api/v1/pair', {'code': self.probe.code})
        self.assertEqual(status, 200)
        self.cookie = headers['Set-Cookie'].split(';')[0]
        self.csrf = reply['csrf']
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])

    def mutation(self, operation, key, **extra):
        payload = dict(request_id=key, expected_revision=self.probe.fixture['accepted_revision'],
                       expected_draft_version=self.probe.fixture['draft_version'], **extra)
        return self.http('/api/v1/' + operation, payload), payload

    def request(self):
        self.agent('resume')
        self.pair()
        self.mutation('capture', 'capture-a', raw_text='FAKE: reusable lunch box')
        self.mutation('propose', 'request-a')
        status, result, _ = self.agent('events', dict(session_id=self.probe.session_id, after=0, timeout=0))
        self.assertEqual(status, 200)
        event = result['events'][0]
        return {key: event[key] for key in CORRELATION} | {'text': 'FAKE proposal from fixture agent'}

    def test_round_trip_reload_human_accept_and_request_replay(self):
        reply = self.request()
        before = copy.deepcopy(self.probe.fixture)
        status, result, _ = self.agent('respond', reply)
        self.assertEqual(status, 200)
        self.assertEqual(before, self.probe.fixture, 'agent cannot accept')
        status, state, _ = self.http('/api/v1/state')
        self.assertEqual(status, 200, 'cookie permits reload')
        self.assertEqual(state['proposal']['text'], reply['text'])
        (status, accepted, _), payload = self.mutation('accept', 'accept-a', proposal_id=result['proposal']['proposal_id'])
        self.assertEqual(status, 200)
        self.assertEqual(accepted['accepted_revision'], 2)
        self.assertEqual(self.http('/api/v1/accept', payload)[1], accepted)
        payload['proposal_id'] = 'another'
        self.assertEqual(self.http('/api/v1/accept', payload)[0], 409)
        self.assertEqual(self.probe.fixture['accepted_revision'], 2)

    def test_wrong_auth_origin_host_csrf_and_browser_cannot_reply(self):
        self.assertEqual(self.http('/api/v1/state')[0], 401)
        self.assertEqual(self.http('/agent/status', {}, {'Authorization': 'Bearer wrong'})[0], 401)
        self.pair()
        self.assertEqual(self.http('/agent/respond', {})[0], 401)
        self.assertEqual(self.http('/api/v1/state', headers={'Host': 'attacker.test'})[0], 403)
        self.assertEqual(self.http('/api/v1/state', headers={'Origin': 'http://attacker.test'})[0], 403)
        payload = dict(request_id='a', expected_revision=0, expected_draft_version=0, raw_text='FAKE')
        self.assertEqual(self.http('/api/v1/capture', payload, {'X-CSRF-Token': 'wrong'})[0], 403)
        self.assertEqual(self.http('/api/v1/capture', payload, {'Origin': 'http://attacker.test'})[0], 403)

    def test_pair_replay_invalidates_session_visibly(self):
        self.pair()
        self.assertEqual(self.http('/api/v1/pair', {'code': self.probe.code})[0], 401)
        self.assertEqual(self.http('/api/v1/state')[0], 401)
        self.assertTrue(self.probe.invalidated)
        self.agent('resume')
        self.pair()
        self.assertEqual(self.http('/api/v1/state')[0], 200)

    def test_pair_expiry_and_five_attempt_limit(self):
        now = [0.0]
        self.probe.clock = lambda: now[0]
        self.probe.code_deadline = 60
        for _ in range(5):
            self.assertEqual(self.http('/api/v1/pair', {'code': 'wrong'})[0], 401)
        self.assertEqual(self.http('/api/v1/pair', {'code': self.probe.code})[0], 401)
        self.probe.pair_attempts = 0
        now[0] = 60
        self.assertEqual(self.http('/api/v1/pair', {'code': self.probe.code})[0], 401)

    def test_old_response_rejected_after_edit_and_resume(self):
        reply = self.request()
        self.mutation('capture', 'capture-b', raw_text='FAKE: changed text')
        self.assertEqual(self.agent('respond', reply)[0], 409)
        self.mutation('propose', 'request-b')
        self.agent('resume')
        self.assertEqual(self.agent('respond', reply)[0], 409)
        self.assertEqual(self.http('/api/v1/state')[1]['agent_status'], 'connected')

    def test_each_correlation_field_and_conflicting_duplicate_response(self):
        reply = self.request()
        for field in CORRELATION:
            bad = copy.deepcopy(reply)
            bad[field] = 'wrong'
            self.assertEqual(self.agent('respond', bad)[0], 409, field)
        self.assertEqual(self.agent('respond', reply)[0], 200)
        reply['text'] = 'FAKE conflicting reply'
        self.assertEqual(self.agent('respond', reply)[0], 409)

    def test_interruption_heartbeat_and_idle_cap(self):
        self.request()
        sid = self.probe.session_id
        self.assertEqual(self.agent('session-close', {'session_id': sid})[0], 200)
        state = self.http('/api/v1/state')[1]
        self.assertEqual(state['agent_status'], 'disconnected')
        self.assertIsNone(state['pending_request'])
        self.assertIn('resume', state['resume_command'])
        self.agent('resume')
        self.probe.last_heartbeat -= 36
        self.assertEqual(self.http('/api/v1/state')[1]['agent_status'], 'disconnected')
        self.agent('resume')
        self.probe.last_activity -= 120
        self.assertEqual(self.http('/api/v1/state')[1]['agent_status'], 'paused')
        self.assertEqual(self.agent('events', dict(session_id=self.probe.session_id, after=0, timeout=0))[0], 503)

    def test_wait_completion_renews_heartbeat_but_not_activity(self):
        now = [0.0]
        self.probe.clock = lambda: now[0]
        with self.probe.condition:
            self.probe.agent('resume', {})
        sid = self.probe.session_id

        def advance(timeout):
            now[0] += timeout

        with patch.object(self.probe.condition, 'wait', side_effect=advance):
            for start, end in ((0, 25), (55, 80)):
                now[0] = start
                with self.probe.condition:
                    result = self.probe.agent('events', dict(session_id=sid, after=0, timeout=25))
                self.assertEqual(result['agent_status'], 'connected')
                self.assertEqual(now[0], end)
                self.assertEqual(self.probe.last_heartbeat, end)
                self.assertEqual(self.probe.last_activity, 0)
            now[0] = 100
            with self.probe.condition:
                self.assertEqual(self.probe.state()['agent_status'], 'connected')
            self.assertEqual(self.probe.last_heartbeat, 80, 'browser polling is not heartbeat')
            now[0] = 110
            with self.probe.condition:
                result = self.probe.agent('events', dict(session_id=sid, after=0, timeout=25))
            self.assertEqual(now[0], 120)
            self.assertEqual(result['agent_status'], 'paused')
            self.assertEqual(self.probe.last_heartbeat, 110, 'paused completion must not renew')
            with self.probe.condition, self.assertRaises(Failure) as caught:
                self.probe.agent('events', dict(session_id=sid, after=0, timeout=0))
            self.assertEqual(caught.exception.code, 'agent_unavailable')
            self.assertEqual(self.probe.status, 'paused')

    def test_waiter_cannot_cross_resume_or_close(self):
        for operation, expected_code in (('resume', 'wrong_session'),
                                         ('session-close', 'agent_unavailable')):
            with self.subTest(operation=operation):
                with self.probe.condition:
                    self.probe.agent('resume', {})
                sid = self.probe.session_id
                entered = threading.Event()
                outcome = []
                original_wait = self.probe.condition.wait

                def wait(timeout):
                    entered.set()
                    return original_wait(timeout)

                def waiter():
                    with self.probe.condition:
                        try:
                            outcome.append(self.probe.agent('events',
                                dict(session_id=sid, after=0, timeout=25)))
                        except Failure as exc:
                            outcome.append(exc)

                with patch.object(self.probe.condition, 'wait', side_effect=wait):
                    thread = threading.Thread(target=waiter, daemon=True)
                    thread.start()
                    self.assertTrue(entered.wait(2), 'waiter entered condition.wait')
                    with self.probe.condition:
                        self.probe.agent(operation, {} if operation == 'resume' else {'session_id': sid})
                        heartbeat = self.probe.last_heartbeat
                    thread.join(2)
                self.assertFalse(thread.is_alive(), 'old waiter must cancel promptly')
                self.assertEqual(len(outcome), 1)
                self.assertIsInstance(outcome[0], Failure)
                self.assertEqual(outcome[0].code, expected_code)
                self.assertEqual(self.probe.last_heartbeat, heartbeat)
                self.assertEqual(self.probe.status, 'connected' if operation == 'resume' else 'disconnected')

    def test_restart_preserves_draft_but_expires_secrets(self):
        self.request()
        old = self.probe
        new = Probe(self.runtime)
        self.assertEqual(new.fixture, old.fixture)
        self.assertNotEqual(new.agent_token, old.agent_token)
        self.assertEqual(new.cookie, '')
        self.assertIsNone(new.pending)
        self.assertEqual(new.status, 'disconnected')
        self.assertEqual(stat.S_IMODE(self.runtime.stat().st_mode) & 0o777, 0o700)
        for filename in ('agent.json', 'fixture.json'):
            self.assertEqual(stat.S_IMODE((self.runtime / filename).stat().st_mode), 0o600)

    def test_bounded_wait_strict_json_and_no_untrusted_fields(self):
        self.agent('resume')
        self.assertEqual(self.agent('events', dict(session_id=self.probe.session_id, after=0, timeout=26))[0], 400)
        self.assertEqual(self.agent('events', dict(session_id=self.probe.session_id, after=0, timeout=0))[1]['events'], [])
        self.pair()
        self.assertEqual(self.http('/api/v1/capture', {'shell': 'never'})[0], 400)
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'{"x":' + b'['*14+b'0'+b']'*14+b'}'):
            with self.assertRaises(Failure):
                decode(raw)

    def test_setgid_parent_does_not_weaken_private_directory(self):
        parent = Path(self.temp.name) / 'shared-parent'
        parent.mkdir(mode=0o2700)
        os.chmod(parent, 0o2700)
        probe = Probe(parent / 'private-child')
        self.assertEqual(stat.S_IMODE(probe.runtime.stat().st_mode) & 0o777, 0o700)
        os.chmod(probe.runtime, 0o2750)
        with self.assertRaises(Failure):
            Probe(probe.runtime)

    def test_second_round_in_same_current_session(self):
        reply = self.request()
        sid = self.probe.session_id
        self.agent('respond', reply)
        self.mutation('accept', 'accept-first', proposal_id=self.probe.proposal['proposal_id'])
        self.mutation('propose', 'request-second')
        result = self.agent('events', dict(session_id=sid, after=1, timeout=0))[1]
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(result['events'][0]['accepted_revision'], 2)
        self.assertEqual(result['events'][0]['sequence'], 2)
        self.assertEqual(self.probe.session_id, sid)


if __name__ == '__main__':
    unittest.main()
