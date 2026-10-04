"""real loopback bridge proof, not native browser qualification."""
import concurrent.futures
import copy
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
import idea_bridge as bridge
from idea_domain import IdeaError
from idea_service import Service, TrustedContext, TrustedStepHandler
from idea_steps import RegistryError, TrustedRoute, load_registry
from idea_store import Store
from idea_launch import OwnerService
from idea_runtime import Runtime
import idea_handoff as handoffs
from test_handoff_store import publication_seed, publication_payload
from idea_workflow import derive_state, save_draft
from test_workflow import captured


class FixturePolicy(bridge.TrustedSessionPolicy):
    """Test credentials only. Production policy is a separate artifact."""
    def __init__(self, binding):
        self.binding = binding
        self.cookie = 'fixture-cookie'
        self.csrf = 'fixture-csrf'
        self.calls = []
        self.observed = None
        self.fail_persist = False
        self.authorized = threading.Event()

    def authorize(self, request, *, write=False):
        self.calls.append(('authorize', request.path))
        bridge.check(request.header('Cookie') == 'browser=' + self.cookie, 'browser_unauthorized', 401)
        if write:
            bridge.check(request.header('X-CSRF-Token') == self.csrf, 'wrong_csrf', 403)
        self.authorized.set()
        return self.binding

    def pair(self, request, payload):
        bridge.check(payload['code'] == 'fixture-code', 'wrong_pairing_code', 401)
        return bridge.Response(dict(ok=True, code='ok', csrf_token=self.csrf),
                               headers={'Set-Cookie': 'browser=' + self.cookie + '; HttpOnly; SameSite=Strict; Path=/'})

    def session(self, binding):
        return dict(ok=True, code='ok', session_id=binding.application.context.session_id,
                    csrf_token=self.csrf, agent_status='disconnected', capabilities={})

    def transport(self, binding, payload):
        self.observed = payload
        return dict(ok=True, code='ok')

    def activity(self, binding):
        self.pings = getattr(self, 'pings', 0) + 1
        return dict(ok=True, code='ok')

    def after_application(self, binding, result):
        if self.fail_persist:
            raise OSError('private runtime path must not leak')


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'ideas'
        self.workspace = Path(self.temp.name) / 'workspace'
        self.workspace.mkdir()
        self.store = Store(self.root, observer='Operator')
        self.sid = self.store.create_session()
        self.application = Service(self.store, {}, TrustedContext('Operator', self.sid))
        self.binding = bridge.ApplicationBinding(self.application)
        self.policy = FixturePolicy(self.binding)
        self.server = bridge.BridgeServer(self.policy, body_deadline=0.3)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.worker.join()

    def request(self, path, body=None, *, method=None, authenticated=True, extra=None):
        headers = {'Host': self.server.host}
        if authenticated:
            headers['Cookie'] = 'browser=' + self.policy.cookie
            headers['X-CSRF-Token'] = self.policy.csrf
        if body is not None:
            headers.update({'Content-Type': 'application/json', 'Origin': self.server.origin})
        headers.update(extra or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        try:
            connection.request(method or ('POST' if body is not None else 'GET'), path,
                None if body is None else json.dumps(body), headers)
            response = connection.getresponse()
            raw = response.read()
            return response.status, (json.loads(raw) if response.getheader('Content-Type') == 'application/json' else raw), dict(response.getheaders())
        finally:
            connection.close()

    def original(self):
        return dict(request_id='capture-1', raw_text='  Café 💡\r\n\n',
                    workspace=dict(name='Explicit', path=str(self.workspace), confirmed=True))

    def capture(self):
        status, result, _ = self.request('/api/v1/capture', self.original())
        self.assertEqual(status, 200)
        return result

    def edit(self, **extra):
        state = self.request('/api/v1/state')[1]
        return dict(request_id='draft-1', idea_id=state['idea_id'], expected_revision=state['revision'],
                    expected_draft_version=state['draft_version'], step='priorities',
                    fields=dict(urgency=7, importance=None), **extra)

    def raw(self, request, chunks=()):
        connection = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2)
        connection.sendall(request)
        def trickle():
            for delay, raw in chunks:
                time.sleep(delay)
                try:
                    connection.sendall(raw)
                except OSError:
                    return
        worker = threading.Thread(target=trickle, daemon=True)
        worker.start()
        response = bytearray()
        try:
            while True:
                data = connection.recv(65536)
                if not data:
                    break
                response.extend(data)
        finally:
            connection.close(); worker.join(2)
        head, body = bytes(response).split(b'\r\n\r\n', 1)
        return int(head.split(b' ')[1]), json.loads(body)

    def raw_post(self, headers, body=b'{}'):
        return self.raw(('POST /api/v1/capture HTTP/1.1\r\nHost: ' + self.server.host +
                         '\r\nOrigin: ' + self.server.origin + '\r\nCookie: browser=' + self.policy.cookie +
                         '\r\nX-CSRF-Token: ' + self.policy.csrf + '\r\nContent-Type: application/json\r\n' +
                         headers + '\r\n').encode() + body)

    def test_real_store_capture_draft_accept_replay_and_restart_binding(self):
        captured = self.capture()
        self.assertEqual(captured['revision'], 1)
        state = self.request('/api/v1/state')[1]
        self.assertEqual(state['steps']['capture']['status'], 'saved')
        detail = (self.root / (captured['idea_id'] + '.md')).read_bytes()
        self.assertIn('Café', detail.decode())
        draft = self.edit()
        self.assertEqual(self.request('/api/v1/draft', draft)[1]['draft_version'], 1)
        accepted = dict(draft, request_id='accept-1', expected_draft_version=1,
                        fields=dict(urgency=7, importance=8), proposal_id=None, expected_backlog_revision=None)
        status, result, _ = self.request('/api/v1/accept', accepted)
        self.assertEqual(status, 200); self.assertEqual(result['revision'], 2)
        self.assertEqual(self.request('/api/v1/accept', accepted)[1], result)
        stale = dict(accepted, request_id='stale')
        status, error, _ = self.request('/api/v1/accept', stale)
        self.assertEqual((status, error['code'], error['write_state']), (409, 'stale_revision', 'not_applied'))
        self.assertEqual(error['revision'], 2)
        conflicting = dict(accepted, fields=dict(urgency=8, importance=8))
        self.assertEqual(self.request('/api/v1/accept', conflicting)[:2][0], 409)
        self.binding.application = Service(Store(self.root), {}, TrustedContext('Operator', self.sid))
        self.assertEqual(self.request('/api/v1/capture', self.original())[1], captured)
        self.assertEqual(self.request('/api/v1/requests/accept-1')[1], result)
        self.assertEqual(self.request('/api/v1/state')[1]['idea_id'], captured['idea_id'])

    def test_navigation_exact_contract_and_no_accepted_or_draft_increment(self):
        self.capture()
        payload = self.edit(); payload.pop('fields'); payload['request_id'] = 'navigate-1'
        status, result, _ = self.request('/api/v1/navigate', payload)
        self.assertEqual(status, 200); self.assertEqual(result['revision'], 1); self.assertEqual(result['draft_version'], 0)
        self.assertEqual(self.request('/api/v1/state')[1]['current_step'], 'priorities')
        self.assertEqual(self.request('/api/v1/navigate', payload)[1], result)

    def test_policy_pair_session_transport_and_no_application_before_auth(self):
        with patch.object(self.application, 'state', wraps=self.application.state) as called:
            status, error, _ = self.request('/api/v1/state', authenticated=False)
            self.assertEqual((status, error), (401, {'ok': False, 'code': 'browser_unauthorized'}))
            called.assert_not_called()
        self.assertEqual(self.request('/api/v1/capture', self.original(), extra={'X-CSRF-Token': 'wrong'})[0], 403)
        status, paired, headers = self.request('/api/v1/pair', {'code': 'fixture-code'}, authenticated=False)
        self.assertEqual(status, 200); self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertEqual(self.request('/api/v1/session')[1]['csrf_token'], self.policy.csrf)
        transport = dict(host=self.server.host, origin=self.server.origin, secure_context=True)
        self.assertEqual(self.request('/api/v1/transport', transport)[0], 200)
        self.assertEqual(self.policy.observed, transport)
        self.assertEqual(self.request('/api/v1/transport', dict(transport, secure_context='true'))[0], 403)
        self.assertEqual(self.request('/api/v1/capture', self.original(), extra={'Host': 'localhost'})[0], 403)
        self.assertEqual(self.request('/api/v1/capture', self.original(), extra={'Origin': 'http://outsider'})[0], 403)
        self.capture()
        with patch.object(self.application, 'capture', wraps=self.application.capture) as called:
            self.assertEqual(self.request('/api/v1/capture', self.original(), authenticated=False)[0], 401)
            called.assert_not_called()  # Not even receipt replay may bypass auth.

    def test_activity_ping_needs_auth_and_csrf_takes_only_an_empty_object_and_saves_nothing(self):
        self.assertEqual(self.request('/api/v1/activity', {}, authenticated=False)[0], 401)
        self.assertEqual(self.request('/api/v1/activity', {}, extra={'X-CSRF-Token': 'wrong'})[0], 403)
        self.assertEqual(self.request('/api/v1/activity', {'text': 'x'})[0], 400)
        self.assertEqual(self.request('/api/v1/activity', {'extra': None})[0], 400)
        self.assertEqual(getattr(self.policy, 'pings', 0), 0)
        before = self.application.state()
        with patch.object(self.application, 'draft') as draft, patch.object(self.application, 'capture') as capture:
            status, result, _ = self.request('/api/v1/activity', {})
            draft.assert_not_called(); capture.assert_not_called()
        self.assertEqual((status, result), (200, {'ok': True, 'code': 'ok'}))
        self.assertEqual(self.policy.pings, 1)
        self.assertEqual(self.application.state(), before)

    def test_only_successful_browser_writes_count_as_human_activity(self):
        # Review idle-r1: reading a stored receipt held the idle clock, while visual saves never moved it.
        pings = lambda: getattr(self.policy, 'pings', 0)
        self.capture(); self.assertEqual(pings(), 1)
        self.assertEqual(self.request('/api/v1/requests/capture-1')[0], 200)
        self.assertEqual(self.request('/api/v1/state')[0], 200)
        self.assertEqual(pings(), 1, 'reads never count')
        self.assertEqual(self.request('/api/v1/draft', self.edit())[0], 200); self.assertEqual(pings(), 2)
        self.assertEqual(self.request('/api/v1/draft', dict(self.edit(), request_id='draft-stale', expected_revision=99))[0], 409)
        self.assertEqual(pings(), 2, 'a refused write never counts')
        self.assertEqual(self.request('/api/v1/activity', {})[0], 200); self.assertEqual(pings(), 3, 'a ping counts once')
        def disposition(binding, request, payload):
            return dict(ok=True, code='ok', write_state='applied', request_id=payload['request_id'])
        def selection(binding, request, payload):
            # The real selection reply: ok, no receipt and no write_state (browser-api.md).
            if payload['idea_id'] != 'idea_' + '1' * 32: raise bridge.BridgeError('not_found', 404)
            return dict(ok=True, code='ok', session_id=self.sid, idea_id=payload['idea_id'], revision=1, draft_version=0, backlog_revision=0)
        server = bridge.BridgeServer(self.policy, body_deadline=0.3,
                                     routes={'visual-disposition': bridge.TrustedRoute('visual-disposition', disposition),
                                             'selection': bridge.TrustedRoute('selection', selection)})
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        def post(path, body):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
            try:
                connection.request('POST', path, json.dumps(body),
                    {'Host': server.host, 'Origin': server.origin, 'Content-Type': 'application/json',
                     'Cookie': 'browser=' + self.policy.cookie, 'X-CSRF-Token': self.policy.csrf})
                return connection.getresponse().status
            finally:
                connection.close()
        try:
            self.assertEqual(post('/api/v1/visual-disposition', dict(request_id='visual-1')), 200)
            self.assertEqual(pings(), 4, 'a visual save counts like any other write')
            # Review idle-r2: opening an idea writes no receipt and has no write_state; it counts too.
            self.assertEqual(post('/api/v1/selection', dict(idea_id='idea_' + '1' * 32)), 200)
            self.assertEqual(pings(), 5, 'opening an idea counts')
            self.assertEqual(post('/api/v1/selection', dict(idea_id='idea_' + 'f' * 32)), 404)
            self.assertEqual(pings(), 5, 'a refused selection never counts')
        finally:
            server.shutdown(); server.server_close(); worker.join()

    def test_trusted_state_response_ceiling_and_compact_wire_boundary(self):
        body=dict(ok=True,code='ok',raw='x'*128)
        size=len(json.dumps(body,ensure_ascii=False,separators=(',',':')).encode())
        with patch.object(self.application,'state',return_value=body), \
             patch.object(bridge,'MAX_STATE_RESPONSE',size):
            status,result,headers=self.request('/api/v1/state')
            self.assertEqual((status,result),(200,body))
            self.assertEqual(int(headers['Content-Length']),size)
        with patch.object(self.application,'state',return_value=body), \
             patch.object(bridge,'MAX_STATE_RESPONSE',size-1):
            status,error,_=self.request('/api/v1/state')
            self.assertEqual((status,error['code']),(500,'response_too_large'))
        self.assertEqual(bridge.MAX_STATE_RESPONSE,2*bridge.MAX_STATE+2*bridge.MAX_INPUT)

    def test_large_state_requires_auth_before_application_or_store(self):
        body=dict(ok=True,code='ok',raw='x'*bridge.MAX_JSON)
        with patch.object(self.application,'state',return_value=body) as called, \
             patch.object(self.store,'transaction',side_effect=AssertionError('unauthorized Store read')):
            status,error,_=self.request('/api/v1/state',authenticated=False)
            self.assertEqual((status,error['code']),(401,'browser_unauthorized'))
            called.assert_not_called()
            self.assertEqual(self.request('/api/v1/state')[0],200)

    def test_state_like_other_routes_private_replies_and_failed_state_keep_one_mib(self):
        body=dict(ok=True,code='ok',accepted={},drafts={},raw='x'*bridge.MAX_JSON,
                  json_limit=bridge.MAX_STATE_RESPONSE)
        self.server.routes['propose']=TrustedRoute('propose',lambda *args:body)
        status,error,_=self.request('/api/v1/propose',{})
        self.assertEqual((status,error['code']),(500,'response_too_large'))
        for result in (bridge.Response(body,status=201),dict(body,ok=False)):
            with self.subTest(result_type=type(result).__name__), patch.object(self.application,'state',return_value=result):
                status,error,_=self.request('/api/v1/state')
                self.assertEqual((status,error['code']),(500,'response_too_large'))
        self.server.agent=lambda *args:body
        self.policy.authorize_agent=lambda request:SimpleNamespace(binding=self.binding,session_id=self.sid)
        self.policy.recheck_agent=lambda context:self.binding
        path='/agent/v1/events?session_id='+self.sid+'&after=0&timeout=0'
        status,error,_=self.request(path,authenticated=False,extra={'Authorization':'Bearer fixture'})
        self.assertEqual((status,error['code']),(500,'response_too_large'))
        # The successful state read allowance cannot widen write-body admission.
        status,error=self.raw_post('Content-Length: '+str(bridge.MAX_JSON+1)+'\r\n',b'')
        self.assertEqual((status,error['code']),(413,'too_large'))

    def test_state_after_application_failure_never_returns_large_saved_payload(self):
        body=dict(ok=True,code='ok',raw='x'*bridge.MAX_JSON)
        self.policy.fail_persist=True
        with patch.object(self.application,'state',return_value=body):
            status,error,_=self.request('/api/v1/state')
        self.assertEqual((status,error),(500,dict(ok=False,code='session_persistence_failed')))

    def test_legacy_workflow_selected_draft_duplication_justifies_inherited_bound(self):
        idea=captured();value={'outcome':'x'*4000}
        idea=save_draft(idea,'shape',value,expected_revision=idea['revision'],expected_draft_version=0)['idea']
        idea['workflow']['current_step']='shape'
        # Actual validated legacy JSON Store branch; no giant fixture required.
        with self.store.transaction() as state:
            legacy=copy.deepcopy(state)
        legacy.update(ideas={idea['idea_id']:idea},order=[idea['idea_id']])
        legacy_root=Path(self.temp.name)/'legacy';legacy_root.mkdir()
        from idea_domain import encoded
        (legacy_root/'state.json').write_bytes(encoded(legacy))
        with Store(legacy_root).transaction() as state:
            projection=derive_state(state['ideas'][idea['idea_id']])
        self.assertEqual(projection['drafts']['shape'],value)
        self.assertEqual(projection['draft'],dict(step='shape',fields=value))
        once=json.dumps(value,separators=(',',':')).encode()
        twice=json.dumps(projection,separators=(',',':')).encode()
        self.assertGreater(len(twice),2*len(once))

    def test_default_policy_fails_closed(self):
        self.server.policy = bridge.TrustedSessionPolicy()
        self.assertEqual(self.request('/api/v1/state')[0], 401)
        self.assertEqual(self.request('/api/v1/pair', {'code': 'anything'})[0], 503)

    def test_fixed_static_routes_query_reload_and_traversal(self):
        self.assertEqual(self.request('/')[0], 200)
        for path in ('/app.js', '/api.js', '/folds.js', '/styles.css'):
            status, body, headers = self.request(path)
            self.assertEqual(status, 200); self.assertIsInstance(body, bytes)
            self.assertEqual(headers['Cache-Control'], 'no-store'); self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        captured = self.capture()
        self.assertEqual(self.request('/?idea_id=' + captured['idea_id'])[0], 200)
        self.assertEqual(self.request('/api/v1/state?idea_id=' + captured['idea_id'])[1]['idea_id'], captured['idea_id'])
        for path in ('/../scripts/idea_store.py', '/%2e%2e/scripts/idea.py', '/steps/arbitrary.js', '/.env', '/steps/', '/api/v1/unknown'):
            self.assertEqual(self.request(path)[0], 404, path)
        for path in ('/?token=never', '/api/v1/state?idea_id=x', '/api/v1/state?idea_id=x&idea_id=y', '/api/v1/state?other=1', '/app.js?x=1',
                     '/steps/review.js?x=1', '/ideas.js?x=1'):
            self.assertEqual(self.request(path)[0], 400, path)
        status, body, headers = self.request('/steps/shape.js')
        self.assertEqual(status, 200)
        self.assertIn(b'export function render(', body)
        self.assertEqual(headers['Content-Type'], 'text/javascript; charset=utf-8')
        for path in ('/steps/visualize.js', '/steps/assess.js', '/steps/review.js', '/ideas.js', '/setup.js'):
            status, body, headers = self.request(path)
            self.assertEqual(status, 200, path)
            self.assertIn(b'export function render(', body)
            self.assertEqual(headers['Content-Type'], 'text/javascript; charset=utf-8')
        self.assertEqual(self.request('/api/v1/requests/missing')[0], 404)
        self.assertEqual(self.request('/api/v1/state', {}, method='POST')[0], 405)

    def test_stable_binding_static_queries_and_typed_single_selector_header(self):
        selector='binding_'+'1'*32
        idea='idea_'+'2'*32
        for path in ('/?binding='+selector, '/index.html?idea_id='+idea+'&binding='+selector):
            self.assertEqual(self.request(path,authenticated=False)[0],200)
        for path in ('/?binding='+selector+'&binding='+selector, '/?binding=wrong',
                     '/?binding='+selector+'&idea_id='+idea+'&idea_id='+idea,
                     '/api/v1/state?binding='+selector, '/app.js?binding='+selector):
            self.assertEqual(self.request(path)[0],400,path)
        self.assertEqual(self.request('/api/v1/session',extra={'X-Idea-Binding':selector})[0],200)
        self.assertEqual(self.request('/api/v1/session',extra={'X-Idea-Binding':'binding_bad'})[0],400)
        raw=('GET /api/v1/session HTTP/1.1\r\nHost: '+self.server.host+
             '\r\nX-Idea-Binding: '+selector+'\r\nx-idea-binding: '+selector+'\r\n\r\n').encode()
        status,response=self.raw(raw)
        self.assertEqual(status,400)
        self.assertEqual(response['code'],'ambiguous_headers')

    def test_static_symlink_and_oversize_fail_closed(self):
        web = Path(self.temp.name) / 'web'; web.mkdir()
        outside = Path(self.temp.name) / 'private'; outside.write_text('private content')
        (web / 'app.js').symlink_to(outside)
        with patch.object(self.server, 'web', web):
            self.assertEqual(self.request('/app.js')[0], 503)
            (web / 'app.js').unlink(); (web / 'app.js').write_bytes(b'x' * (bridge.MAX_JSON + 1))
            self.assertEqual(self.request('/app.js')[0], 503)
        alias = Path(self.temp.name) / 'alias'; alias.symlink_to(web, target_is_directory=True)
        with patch.object(self.server, 'web', alias):
            self.assertEqual(self.request('/app.js')[0], 503)

    def test_malformed_http_json_and_exact_service_schema(self):
        cases = (
            ('Content-Length: 2\r\nContent-Length: 2\r\n', b'{}', 400),
            ('Content-Length: 2\r\nTransfer-Encoding: chunked\r\n', b'{}', 400),
            ('Content-Length: 2\r\n Cookie: folded\r\n', b'{}', 400),
            ('Content-Length: 2\r\nBrokenHeader\r\n', b'{}', 400),
            ('Content-Length: 1048577\r\n', b'', 413),
        )
        for headers, raw, expected in cases:
            with self.subTest(headers=headers):
                self.assertEqual(self.raw_post(headers, raw)[0], expected)
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'{"x":"\\ud800"}', b'[]', b'{bad'):
            with self.subTest(raw=raw):
                self.assertEqual(self.raw_post('Content-Length: ' + str(len(raw)) + '\r\n', raw)[0], 400)
        payload = dict(self.original(), actor='forged')
        self.assertEqual(self.request('/api/v1/capture', payload)[0], 400)
        self.assertEqual(self.request('/api/v1/transport', dict(host=self.server.host, origin=self.server.origin, secure_context=True, extra=True))[0], 403)
        with self.store.transaction() as state:
            self.assertEqual(state['ideas'], {})

    def test_json_depth_and_node_bounds(self):
        for value in ({'x': [None] * 100000}, {'x': 1}):
            if value['x'] == 1:
                for _ in range(33): value = {'x': value}
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge.decode_json(json.dumps(value).encode())
            self.assertEqual(caught.exception.status, 413)

    def test_absolute_header_and_body_deadline_resists_trickle(self):
        start = time.monotonic()
        status, result = self.raw(b'GET /api/v1/state HTTP/1.1\r\nHo', [(0.08, b's')] * 5)
        self.assertEqual((status, result['code']), (400, 'request_timeout'))
        self.assertLess(time.monotonic() - start, 1)
        header = ('POST /api/v1/capture HTTP/1.1\r\nHost: ' + self.server.host + '\r\nOrigin: ' + self.server.origin +
                  '\r\nCookie: browser=' + self.policy.cookie + '\r\nX-CSRF-Token: ' + self.policy.csrf +
                  '\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{').encode()
        self.assertEqual(self.raw(header, [(0.08, b' ')] * 5)[1]['code'], 'request_timeout')

    def test_header_request_target_and_handler_capacity(self):
        self.assertEqual(self.raw(('GET / HTTP/1.1\r\nHost: ' + self.server.host + '\r\nX-Test: ' + 'x' * 8200 + '\r\n\r\n').encode())[0], 413)
        self.assertEqual(self.raw(('GET /' + 'x' * 4096 + ' HTTP/1.1\r\n\r\n').encode())[0], 413)
        self.assertEqual(self.raw(('GET / HTTP/1.1\r\nHost: ' + self.server.host + '\r\n' + 'X: y\r\n' * 65 + '\r\n').encode())[0], 413)
        for _ in range(8): self.server.slots.acquire()
        try:
            self.assertEqual(self.request('/api/v1/state')[:2], (503, {'ok': False, 'code': 'busy'}))
        finally:
            for _ in range(8): self.server.slots.release()

    def test_bounded_binding_wait_and_auth_recheck_after_revocation(self):
        with self.binding.lock:
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(self.request, '/api/v1/state')
                self.assertEqual(future.result(timeout=2)[0], 503)
        self.policy.authorized.clear()
        with self.binding.lock:
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(self.request, '/api/v1/state')
                self.assertTrue(self.policy.authorized.wait(1))
                self.policy.cookie = 'rotated'
                self.binding.lock.release()
                try:
                    self.assertEqual(future.result(timeout=2)[0], 401)
                finally:
                    self.binding.lock.acquire()

    def test_literal_extension_routes_unavailable_and_authenticated_streams(self):
        self.server.routes = {}  # Explicit absent-extension fixture; other cases use packaged defaults.
        payload = {'request_id': 'future'}
        self.assertEqual(self.request('/api/v1/propose', payload)[0], 503)
        self.assertEqual(self.request('/api/v1/handoff', payload, authenticated=False)[0], 401)
        self.server.routes['propose'] = TrustedRoute('propose', lambda binding, request, value:
            dict(ok=True, code='ok', received=value, selected=binding.application.context.selected_idea_id))
        self.assertEqual(self.request('/api/v1/propose', payload)[1]['received'], payload)
        seen = []
        self.server.routes['upload-bytes'] = TrustedRoute('upload-bytes', lambda binding, request, body:
            (seen.append((request.resource_id, body.read_all())) or dict(ok=True, code='ok')))
        headers = {'Content-Type': 'application/octet-stream', 'Cookie': 'browser=' + self.policy.cookie,
                   'X-CSRF-Token': self.policy.csrf, 'Origin': self.server.origin}
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        connection.request('PUT', '/api/v1/uploads/upload_fixture/bytes', b'fixture bytes', headers)
        response = connection.getresponse(); self.assertEqual(response.status, 200); response.read(); connection.close()
        self.assertEqual(seen, [('upload_fixture', b'fixture bytes')])
        self.assertFalse(self.request('/api/v1/state')[1]['capabilities']['uploads'])
        for name in ('uploads', 'attachment'):
            self.server.routes[name] = TrustedRoute(name, lambda *args: dict(ok=True, code='ok'))
        self.assertTrue(self.request('/api/v1/state')[1]['capabilities']['uploads'])
        self.assertTrue(self.request('/api/v1/session')[1]['capabilities']['uploads'])

    def test_extension_calls_serialized_and_unexpected_errors_redacted(self):
        active = [0, 0]
        def handler(*args):
            active[0] += 1; active[1] = max(active[1], active[0]); time.sleep(0.03); active[0] -= 1
            return dict(ok=True, code='ok')
        self.server.routes['propose'] = TrustedRoute('propose', handler)
        with concurrent.futures.ThreadPoolExecutor() as pool:
            replies = list(pool.map(lambda _: self.request('/api/v1/propose', {}), range(2)))
        self.assertEqual([item[0] for item in replies], [200, 200]); self.assertEqual(active[1], 1)
        def broken(*args):
            raise RuntimeError('private key and private path must not leak')
        self.server.routes['propose'] = TrustedRoute('propose', broken)
        self.assertEqual(self.request('/api/v1/propose', {})[:2], (500, {'ok': False, 'code': 'internal_error', 'write_state': 'committed_uncertain'}))
        self.server.routes['propose'] = TrustedRoute('propose', lambda *args: {'bad': 'response'})
        self.assertEqual(self.request('/api/v1/propose', {'request_id': 'invalid-response'})[:2],
            (500, {'ok': False, 'code': 'invalid_response', 'write_state': 'committed_uncertain', 'request_id': 'invalid-response'}))

    def test_committed_uncertainty_and_private_selection_persistence_failure(self):
        self.policy.fail_persist = True
        status, error, _ = self.request('/api/v1/capture', self.original())
        self.assertEqual((status, error['code'], error['write_state'], error['committed']),
                         (500, 'session_persistence_uncertain', 'committed_uncertain', True))
        self.policy.fail_persist = False
        result = self.capture()
        self.assertEqual(result['idea_id'], error['idea_id'])
        with self.store.transaction() as state: self.assertEqual(len(state['ideas']), 1)
        with patch.object(self.application, 'draft', side_effect=IdeaError('durability_uncertain', 'secret', committed=True)):
            status, error, _ = self.request('/api/v1/draft', self.edit())
            self.assertEqual((status, error['write_state'], error['committed']), (500, 'committed_uncertain', True))
            self.assertNotIn('secret', json.dumps(error))


@unittest.skipUnless(os.name == 'posix', 'Native owner ACLs remain unqualified')
class OwnerHandoffBridgeTests(unittest.TestCase):
    """Actual Owner/provider/HTTP paths, disposable Stores and private runtime."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root = self.directory/'Ideas café'; self.private = self.directory/'private'
        _,self.key,self.workspace = publication_seed(self.root)
        self.owner = None
        self.addCleanup(self.stop_owner)
        self.start_owner()
        self.client = Runtime(self.root,self.private)
        self.first = self.open_browser(self.key)
        self.second = self.open_browser(self.key)

    def start_owner(self):
        self.owner = OwnerService(Runtime(self.root,self.private).acquire_owner())
        self.owner.start()

    def stop_owner(self):
        if self.owner is not None:
            self.owner.shutdown(); self.owner = None

    def wire(self,path,payload=None,*,browser=None,method=None,extra=None):
        headers = {} if browser is None else dict(Cookie=browser['cookie'],
            **{'X-Idea-Binding':browser['binding_id'],'X-CSRF-Token':browser['csrf'],'X-Idea-Tab':browser['tab']})
        if payload is not None:
            headers.update({'Content-Type':'application/json','Origin':self.owner.server.origin})
        headers.update(extra or {})
        connection = http.client.HTTPConnection('127.0.0.1',self.owner.server.server_port,timeout=5)
        try:
            connection.request(method or ('POST' if payload is not None else 'GET'),path,
                None if payload is None else json.dumps(payload),headers)
            response = connection.getresponse(); raw = response.read()
            body = json.loads(raw) if response.getheader('Content-Type') == 'application/json' else raw
            return response.status,body,dict(response.getheaders())
        finally:
            connection.close()

    def open_browser(self,selected=None,binding_id=None):
        opened = self.client.open_binding('new' if binding_id is None else 'resume',binding_id,selected)
        status,result,headers = self.wire('/api/v1/pair',{'code':opened['pairing_code']},
                                        extra={'X-Idea-Binding':opened['binding_id']})
        self.assertEqual(status,200,result)
        return dict(opened,cookie=headers['Set-Cookie'].split(';',1)[0],csrf=result['csrf_token'],tab=result['tab_secret'])

    def api(self,name,payload=None,browser=None):
        status,result,_ = self.wire('/api/v1/'+name,payload,browser=self.first if browser is None else browser)
        self.assertEqual(status,200,result)
        return result

    def files(self,root=None):
        root = self.root if root is None else root
        return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}

    def payload(self,request='owner-handoff'):
        return publication_payload(self.owner.store,self.key,request)['payload']

    def test_real_owner_refuses_a_browser_request_without_the_matching_tab_secret(self):
        browser = self.first
        self.assertEqual(self.wire('/api/v1/session',browser=browser)[0],200)
        status,body,_ = self.wire('/api/v1/session',browser=dict(browser,tab='0'*64))
        self.assertEqual((status,body['code']),(401,'browser_unauthorized'))
        self.assertEqual(self.wire('/api/v1/state',browser=dict(browser,tab='0'*64))[0],401)
        self.assertEqual(self.wire('/api/v1/activity',{},browser=dict(browser,tab='0'*64))[0],401)
        self.assertNotIn(browser['tab'],json.dumps(body))
        # The other binding's secret never opens this binding.
        self.assertEqual(self.wire('/api/v1/session',browser=dict(browser,tab=self.second['tab']))[0],401)
        # Duplicate tab headers are ambiguous and refused before authentication.
        connection = http.client.HTTPConnection('127.0.0.1',self.owner.server.server_port,timeout=5)
        try:
            connection.putrequest('GET','/api/v1/session',skip_host=True); connection.putheader('Host',self.owner.server.host)
            for name,value in (('Cookie',browser['cookie']),('X-Idea-Binding',browser['binding_id']),
                               ('X-Idea-Tab',browser['tab']),('X-Idea-Tab',browser['tab'])):
                connection.putheader(name,value)
            connection.endheaders(); self.assertEqual(connection.getresponse().status,400)
        finally:
            connection.close()

    def test_owner_fixed_review_ideas_sources_and_security_headers(self):
        web = Path(__file__).resolve().parents[1]/'glitch-idea/web'
        for path in ('/steps/review.js','/ideas.js'):
            with self.subTest(path=path):
                # Real Owner serves the packaged source, without application auth.
                status,body,headers = self.wire(path)
                expected = (web/path.lstrip('/')).read_bytes()
                self.assertEqual(status,200)
                self.assertEqual(body,expected)
                self.assertIn(b'export function render(',body)
                self.assertEqual(headers['Content-Type'],'text/javascript; charset=utf-8')
                self.assertEqual(int(headers['Content-Length']),len(expected))
                for name,value in bridge.SECURITY_HEADERS.items():
                    self.assertEqual(headers[name],value,name)
                self.assertEqual(self.wire(path+'?module=arbitrary')[0],400)
                self.assertEqual(self.wire(path,method='POST')[0],405)
        for path in ('/steps/arbitrary.js','/arbitrary.js','/steps/../scripts/idea_store.py',
                     '/%2e%2e/scripts/idea.py','/ideas.js/../api.js'):
            self.assertEqual(self.wire(path)[0],404,path)

    def test_owner_actual_provider_handoff_projection_replay_and_bound_receipt(self):
        self.assertIsInstance(self.owner.handoff_provider,handoffs.HandoffProvider)
        self.assertIs(self.owner.handoff_provider.store,self.owner.store)
        self.assertIs(self.owner.routes['handoff'].handler,handoffs.handoff)
        self.assertIs(self.owner.routes['ideas'].handler,handoffs.ideas)
        self.assertIs(self.owner.routes['selection'].handler,handoffs.selection)
        before = self.api('state'); self.assertTrue(before['capabilities']['handoff'])
        payload = self.payload(); result = self.api('handoff',payload)
        self.assertTrue(result['handoff_current']); self.assertEqual(result['write_state'],'applied')
        self.assertEqual(set(result['handoff']),handoffs.PUBLIC_FIELDS)
        self.assertEqual(result['handoff']['path'],str(self.root/result['path']))
        self.assertIn('## Idea trace\nidea_id: '+self.key+'\n',result['handoff']['prompt'])
        after = self.api('state')
        for name in ('revision','draft_version','backlog_revision'):
            self.assertEqual(after[name],before[name]); self.assertEqual(result[name],before[name])
        self.assertEqual(after['steps']['review']['status'],'saved')
        self.assertEqual(after['handoff'],result['handoff'])
        rows = self.api('ideas'); self.assertEqual(rows['total'],1)
        self.assertEqual(rows['ideas'][0]['status'],'ready-to-plan')
        published = self.files()
        self.assertEqual(self.api('handoff',payload),result)
        self.assertEqual(self.api('requests/'+payload['request_id']),result)
        self.assertEqual(self.files(),published)
        status,error,_ = self.wire('/api/v1/requests/'+payload['request_id'],browser=self.second)
        self.assertEqual((status,error['code']),(404,'request_not_found'))
        self.workspace.rmdir()
        historical = self.api('requests/'+payload['request_id'])
        self.assertEqual(historical,dict(result,code='historical_handoff',handoff_current=False))
        self.assertEqual(self.api('state')['steps']['review']['status'],'review-needed')
        self.assertEqual(self.api('ideas')['ideas'][0]['status'],'review-needed')
        self.assertEqual(self.files(),published)

    def test_selection_exact_saved_and_null_reply_preserves_domain_and_other_binding(self):
        before = self.api('state')
        self.api('draft',dict(request_id='selection-buffer',idea_id=self.key,
            expected_revision=before['revision'],expected_draft_version=before['draft_version'],
            step='shape',fields={'outcome':'Private selection preserves this draft'}))
        state = self.api('state'); files = self.files()
        expected = dict(ok=True,code='ok',session_id=self.first['session_id'],idea_id=None,
                        revision=0,draft_version=0,backlog_revision=state['backlog_revision'])
        self.assertEqual(self.api('selection',{'idea_id':None}),expected)
        self.assertEqual(self.api('selection',{'idea_id':None}),expected)
        self.assertIsNone(self.api('state')['idea_id'])
        self.assertEqual(self.api('state',browser=self.second)['idea_id'],self.key)
        records = {r['binding_id']:r for r in self.owner.runtime.list_bindings()}
        self.assertIsNone(records[self.first['binding_id']]['selected_idea_id'])
        self.assertEqual(records[self.second['binding_id']]['selected_idea_id'],self.key)
        selected = self.api('selection',{'idea_id':self.key})
        self.assertEqual(selected,dict(expected,idea_id=self.key,revision=state['revision'],draft_version=state['draft_version']))
        self.assertEqual(self.api('state')['drafts'],state['drafts'])
        self.assertEqual(self.api('state')['accepted'],state['accepted'])
        self.assertEqual(self.files(),files)  # Includes all receipts, history and Markdown.

    def test_selection_persistence_failure_rolls_back_saved_and_null_context(self):
        for previous,target in ((self.key,None),(None,self.key)):
            with self.subTest(previous=previous):
                self.api('selection',{'idea_id':previous})
                domain = self.files(); private = self.files(self.owner.runtime.runtime_root)
                with patch.object(self.owner.policy,'persist',side_effect=OSError('private path must not leak')):
                    status,error,_ = self.wire('/api/v1/selection',{'idea_id':target},browser=self.first)
                self.assertEqual((status,error),(500,dict(ok=False,code='session_persistence_failed')))
                self.assertEqual(self.api('state')['idea_id'],previous)
                record = next(r for r in self.owner.runtime.list_bindings() if r['binding_id']==self.first['binding_id'])
                self.assertEqual(record['selected_idea_id'],previous)
                self.assertEqual(self.files(),domain)
                self.assertEqual(self.files(self.owner.runtime.runtime_root),private)

    def test_selection_restart_restores_saved_and_null_without_receipts(self):
        self.api('selection',{'idea_id':None})
        before = self.files()
        old_first,old_second = self.first,self.second
        self.stop_owner(); self.start_owner()
        self.assertEqual(self.wire('/api/v1/ideas',browser=old_first)[0],401)
        self.first = self.open_browser(binding_id=old_first['binding_id'])
        self.second = self.open_browser(binding_id=old_second['binding_id'])
        self.assertEqual(self.first['session_id'],old_first['session_id'])
        self.assertEqual(self.second['session_id'],old_second['session_id'])
        self.assertIsNone(self.api('state')['idea_id'])
        self.assertEqual(self.api('state',browser=self.second)['idea_id'],self.key)
        self.assertEqual(self.files(),before)

    def test_fixed_owner_routes_auth_origin_csrf_exact_body_and_no_query_path_doors(self):
        before = self.files()
        for name,payload in (('ideas',None),('selection',{'idea_id':None}),('handoff',self.payload())):
            with self.subTest(name=name):
                self.assertEqual(self.wire('/api/v1/'+name,payload)[0],401)
                headers = {'X-Idea-Binding':self.second['binding_id']}
                self.assertEqual(self.wire('/api/v1/'+name,payload,browser=self.first,extra=headers)[0],401)
                self.assertEqual(self.wire('/api/v1/'+name,payload,browser=self.first,
                    extra={'Origin':'http://outsider'})[0],403)
                if payload is not None:
                    self.assertEqual(self.wire('/api/v1/'+name,payload,browser=self.first,
                        extra={'X-CSRF-Token':'wrong'})[0],403)
        for path in ('/api/v1/ideas?path=private','/api/v1/ideas?',
                     '/api/v1/selection?command=x','/api/v1/selection?',
                     '/api/v1/handoff?','/api/v1/handoff?path=private'):
            payload = self.payload() if 'handoff' in path else {'idea_id':None} if 'selection' in path else None
            self.assertEqual(self.wire(path,payload,browser=self.first)[0],400)
        self.assertEqual(self.wire('/api/v1/ideas',{},browser=self.first)[0],405)
        self.assertEqual(self.wire('/api/v1/selection',browser=self.first)[0],405)
        for payload in ({},{'idea_id':False},{'idea_id':'../../private'},
                        {'idea_id':None,'request_id':'forbidden'}, {'idea_id':None,'validator_argv':['x']}):
            status,error,_ = self.wire('/api/v1/selection',payload,browser=self.first)
            self.assertEqual(status,400,error); self.assertNotIn('write_state',error)
        status,error,_ = self.wire('/api/v1/selection',{'idea_id':'idea_'+'f'*32},browser=self.first)
        self.assertEqual((status,error['code']),(404,'not_found')); self.assertNotIn('write_state',error)
        self.assertEqual(self.api('state')['idea_id'],self.key)
        self.assertEqual(self.files(),before)

    def test_handoff_persistence_failure_reports_committed_uncertainty_and_reconciles(self):
        self.api('selection',{'idea_id':None}); payload = self.payload()
        with patch.object(self.owner.policy,'persist',side_effect=OSError('private runtime failure')):
            status,error,_ = self.wire('/api/v1/handoff',payload,browser=self.first)
        self.assertEqual((status,error['code'],error['write_state'],error['committed']),
            (500,'session_persistence_uncertain','committed_uncertain',True))
        self.assertEqual(error['request_id'],payload['request_id'])
        result = self.api('requests/'+payload['request_id'])
        self.assertEqual(result['write_state'],'applied'); self.assertTrue(result['handoff_current'])
        self.assertEqual(self.api('handoff',payload),result)
        reused = self.api('handoff',self.payload('owner-handoff-reuse'))
        self.assertEqual(reused['write_state'],'no_op')
        self.assertEqual(reused['handoff'],result['handoff'])
        # The selection is already persisted and unchanged, so a reused receipt writes nothing
        # private and cannot fail there (an unchanged selection is no longer rewritten).
        with patch.object(self.owner.policy,'persist',side_effect=OSError('private runtime failure')) as persist:
            status,again,_ = self.wire('/api/v1/handoff',self.payload('owner-handoff-reuse'),browser=self.first)
        self.assertEqual((status,again['write_state']),(200,'no_op')); persist.assert_not_called()
        record = next(r for r in self.owner.runtime.list_bindings() if r['binding_id']==self.first['binding_id'])
        self.assertEqual(record['selected_idea_id'],self.key)
        with self.owner.store.transaction() as state:
            self.assertEqual(len(self.owner.store.handoffs(state,self.key)),1)
            for name in ('revision','draft_version','backlog_revision'):
                self.assertEqual(result[name],error[name])

    def test_handoff_postcommit_fault_reports_durable_identity_for_applied_and_no_op_replay(self):
        self.api('selection',{'idea_id':None})
        for request,write_state,code in (('http-fault-applied','applied','store_busy'),('http-fault-applied','applied','corrupt_store'),
                                         ('http-fault-reuse','no_op','handoff_capacity'),('http-fault-reuse','no_op','store_busy')):
            with self.subTest(request=request,write_state=write_state,code=code):
                payload = self.payload(request)
                with patch.object(self.owner.handoff_provider,'reconcile',side_effect=IdeaError(code,'private diagnostic')):
                    status,error,_ = self.wire('/api/v1/handoff',payload,browser=self.first)
                self.assertEqual((status,error['code'],error['write_state'],error['committed']),
                                 (500,code,'committed_uncertain',True))
                self.assertEqual(error['request_id'],request)
                compact = self.owner.store.request_result(self.first['session_id'],request)
                self.assertEqual(compact['write_state'],write_state)
                for name in ('request_id','idea_id','revision','draft_version','backlog_revision'):
                    self.assertEqual(error[name],compact[name],name)
                self.assertNotIn('private diagnostic',json.dumps(error))
                published = self.files()
                recovered = self.api('requests/'+request)
                self.assertTrue(recovered['handoff_current'])
                self.assertEqual({name:recovered[name] for name in compact},compact)
                self.assertEqual(self.api('handoff',payload),recovered)
                self.assertEqual(self.files(),published)
        with self.owner.store.transaction() as state:
            self.assertEqual(len(self.owner.store.handoffs(state,self.key)),1)
        before = self.files(); payload = self.payload('http-preflight-refused')
        with patch.object(self.owner.store,'_handoff_http_preflight',side_effect=IdeaError('handoff_capacity','Fixture capacity')):
            status,error,_ = self.wire('/api/v1/handoff',payload,browser=self.first)
        self.assertEqual((status,error['code'],error['write_state']),(400,'handoff_capacity','not_applied'))
        self.assertNotIn('committed',error); self.assertEqual(self.files(),before)
        self.assertEqual(self.wire('/api/v1/requests/'+payload['request_id'],browser=self.first)[0],404)

    def test_moved_store_owner_reads_historical_location_and_explicit_new_packet_becomes_current(self):
        payload = self.payload(); first = self.api('handoff',payload)
        old_sid = self.first['session_id']; before = self.files(); old_root = self.root
        self.stop_owner()
        moved = old_root.with_name('Moved Ideas café'); old_root.rename(moved); self.root = moved
        self.start_owner(); self.client = Runtime(moved,self.private)
        # Runtime credentials are bound to the Store path namespace. Re-pair a
        # new binding here; the old receipt remains verified through Store.
        self.first = self.open_browser(self.key)
        self.assertEqual(self.owner.store.request_result(old_sid,payload['request_id']),
                         {name:first[name] for name in handoffs.RECEIPT_FIELDS})
        view = self.api('state')
        self.assertEqual(view['handoff'],first['handoff'])
        self.assertEqual(view['handoff']['path'],str(old_root/first['path']))
        self.assertEqual(view['handoff_status'],dict(available=False,code='stale_source'))
        self.assertEqual(view['steps']['review']['status'],'review-needed')
        self.assertIsNone(view['accepted']['review'])
        self.assertEqual(self.api('ideas')['ideas'][0]['status'],'review-needed')
        second = self.api('handoff',self.payload('moved-owner-new'))
        self.assertTrue(second['handoff_current']); self.assertEqual(second['write_state'],'applied')
        self.assertEqual(second['handoff_index'],2)
        self.assertNotEqual(second['handoff_id'],first['handoff_id'])
        self.assertEqual(second['handoff']['path'],str(moved/second['path']))
        current = self.api('state')
        self.assertEqual(current['handoff'],second['handoff'])
        self.assertEqual(current['handoff_status'],dict(available=True,code='ok'))
        self.assertEqual(self.api('ideas')['ideas'][0]['status'],'ready-to-plan')
        for name in ('revision','draft_version','backlog_revision'):
            self.assertEqual(second[name],first[name])
        after = self.files()
        for relative,raw in before.items():
            if relative.startswith(('history/','assets/')):
                self.assertEqual(after[relative],raw,relative)


class RegistryTests(unittest.TestCase):
    def missing(self, name):
        raise ModuleNotFoundError(name=name)

    def test_literal_missing_present_and_step_owned_routes(self):
        self.assertEqual(load_registry(self.missing), ({}, {}))
        def importer(name):
            if name == 'idea_steps.visualize':
                return SimpleNamespace(HANDLER=TrustedStepHandler(lambda *args: None),
                    ROUTES=(TrustedRoute('visual-disposition', lambda *args: {}),))
            return self.missing(name)
        handlers, routes = load_registry(importer)
        self.assertEqual(set(handlers), {'visualize'}); self.assertEqual(set(routes), {'visual-disposition'})

    def test_malformed_present_dependency_failure_unknown_and_duplicate_routes(self):
        cases = (
            lambda name: SimpleNamespace(HANDLER=None),
            lambda name: SimpleNamespace(HANDLER=TrustedStepHandler(lambda *args: None, extra_dependencies=('review',))),
            lambda name: (_ for _ in ()).throw(ModuleNotFoundError(name='real_dependency')),
            lambda name: (_ for _ in ()).throw(ValueError('malformed present module')),
            lambda name: SimpleNamespace(HANDLER=TrustedStepHandler(lambda *args: None), ROUTES=[]),
            lambda name: SimpleNamespace(HANDLER=TrustedStepHandler(lambda *args: None), ROUTES=(TrustedRoute('/arbitrary/path', lambda *args: {}),)),
            lambda name: SimpleNamespace(HANDLER=TrustedStepHandler(lambda *args: None), ROUTES=(TrustedRoute('handoff', lambda *args: {}),)),
        )
        for importer in cases:
            with self.subTest(importer=importer), self.assertRaises(RegistryError): load_registry(importer)
        with self.assertRaises(ValueError): bridge.BridgeServer(routes={'arbitrary': TrustedRoute('arbitrary', lambda *args: {})})

    def test_browser_literal_loader_and_validated_nonsecret_selection(self):
        web = Path(__file__).resolve().parents[1] / 'glitch-idea/web'
        script = r'''
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const web = process.argv[1];
const data = text => 'data:text/javascript;base64,' + Buffer.from(text).toString('base64');
const source = readFileSync(web + '/app.js', 'utf8').replace("'./api.js'", JSON.stringify(data(readFileSync(web + '/api.js', 'utf8')))).replace("'./folds.js'", JSON.stringify(data(readFileSync(web + '/folds.js', 'utf8'))));
const app = await import(data(source));
const seen = [];
await app.loadStepModules(async url => {seen.push(url); return {status:404};}, () => {throw Error('must not import missing');});
assert.deepEqual(seen, ['./steps/shape.js','./steps/method.js','./steps/visualize.js','./steps/assess.js','./steps/review.js','./ideas.js']);
await assert.rejects(app.loadStepModules(async () => ({status:503,ok:false})), /Cannot load packaged/);
await assert.rejects(app.loadStepModules(async () => ({status:200,ok:true}), async () => ({})), /Invalid packaged/);
await assert.rejects(app.loadStepModules(async () => ({status:200,ok:true}), async () => ({render(){}})), /Invalid packaged upload renderer/);
await assert.rejects(app.loadStepModules(async () => ({status:200,ok:true}), async () => {throw Error('dependency failure');}), /dependency failure/);
let completed = 0;
await app.loadStepModules(async () => ({status:200,ok:true}), async () => {await Promise.resolve(); completed++; return {render(){},renderUploads(){}};});
assert.equal(completed,6);
assert.equal(typeof app.registerIdeas,'function');
assert.throws(() => app.registerIdeas(null), /Invalid packaged Ideas renderer/);
const id = 'idea_' + 'a'.repeat(32);
assert.equal(app.selectedIdea({search:'?idea_id='+id}), id);
assert.equal(app.selectedIdea({search:''}), null);
assert.throws(() => app.selectedIdea({search:'?idea_id=../../private'}));
assert.throws(() => app.selectedIdea({search:'?idea_id='+id+'&idea_id='+id}));
assert.match(source, /loadStepModules\(\)\.then\(\(\) => startApp\(\)\)/);
assert.match(source, /history\.replaceState/);
'''
        result = subprocess.run(['node', '--input-type=module', '--eval', script, str(web)],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
