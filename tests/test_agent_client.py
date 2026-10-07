"""real loopback owner-private retrieval and fixed native client."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
from idea_agent_client import AgentClient, AgentClientError
from idea_runtime import Runtime, RuntimeError


class FixedHandler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def do_GET(self): self.handle_fixed()
    def do_POST(self): self.handle_fixed()

    def handle_fixed(self):
        f = self.server.fixture
        path = urlsplit(self.path)
        raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        is_asset = urlsplit(self.path).path == '/agent/v1/asset'
        payload = raw if is_asset else json.loads(raw) if raw else None
        f.calls.append((self.command, path.path, payload, dict(self.headers)))
        status = 200
        if path.path == '/control/v1/probe':
            result = f.owner.validate_owner(payload, self.headers.get('Authorization'), 'probe')
        elif path.path == '/control/v1/agent-credentials':
            assert set(payload) == {'challenge','instance_nonce','store_sha256','binding_id','session_id','expected_generation'}
            result = f.owner.validate_owner({k: payload[k] for k in ('challenge','instance_nonce','store_sha256')},
                                            self.headers.get('Authorization'), 'agent-credentials', payload)
            assert payload['binding_id'] == f.bid and payload['session_id'] == f.sid
            result.update(binding_id=f.bid, session_id=f.sid, generation=f.generation, token=f.token)
            changes = {'nonce': ('instance_nonce','f'*64), 'challenge': ('challenge','f'*64),
                       'store': ('store_sha256','f'*64), 'sid': ('session_id','session_'+'f'*32),
                       'binding': ('binding_id','binding_'+'f'*32), 'generation': ('generation','bad'),
                       'token': ('token','bad'), 'extra': ('extra','secret')}
            if f.mode in changes:
                k,v = changes[f.mode]; result[k] = v
        else:
            assert self.headers.get('Authorization') == 'Bearer '+f.token
            assert self.headers.get('X-Idea-Agent-Binding') == f.bid
            assert self.headers.get('X-Idea-Agent-Generation') == f.generation
            assert all(self.headers.get(k) is None for k in ('Origin','Cookie','X-CSRF-Token','X-Idea-Binding'))
            if path.path == '/agent/v1/events':
                query = parse_qs(path.query)
                assert set(query) == {'session_id','after','timeout'} and query['session_id'] == [f.sid]
                assert payload is None
                result = dict(ok=True,code='ok',session_id=f.sid,agent_status='connected',
                              reason=None,sequence=1,events=[dict(f.correlation,sequence=1,data={'exploration':'input'})])
                if f.mode == 'wrong_event_sid': result['events'][0]['session_id'] = 'session_'+'f'*32
                if f.mode == 'token_echo': result['events'][0]['data']['exploration'] = f.token
                if f.mode == 'secret_key': result['events'][0]['data']['token'] = 'hidden'
                if f.mode == 'wrong_sequence': result['events'][0]['sequence'] = True
            elif path.path == '/agent/v1/respond':
                assert set(payload) == set(f.correlation) | {'proposal'}
                result = dict(payload,ok=True,code='ok',status='completed',write_state='applied',
                              evidence=dict(proposal_id='proposal-1',sha256='a'*64))
                if f.mode == 'wrong_response': result['draft_version'] = 9
                if f.mode == 'uncertain':
                    status = 503
                    result = dict(ok=False,code='DO NOT PRINT '+f.token,write_state='committed_uncertain',extra=f.token)
            elif path.path == '/agent/v1/asset':
                f.asset_seen = dict(raw=payload, headers=dict(self.headers))
                if f.mode == 'asset_type':
                    status = 400; result = dict(ok=False,code='unsupported_asset_type')
                elif f.mode == 'asset_big':
                    status = 413; result = dict(ok=False,code='too_large')
                elif f.mode == 'asset_session':
                    status = 403; result = dict(ok=False,code='wrong_session')
                else:
                    result = dict(ok=True,code='ok',idea_id=self.headers['X-Idea-Id'],asset_id='asset_'+'6'*32,
                                  asset_ids=['asset_'+'6'*32],upload_id='upload_'+'7'*32,size=len(payload),
                                  sha256='b'*64)
                    if f.mode == 'asset_extra': result['token'] = 'hidden'
            elif path.path == '/agent/v1/session-close':
                assert payload == {'session_id':f.sid}
                result = dict(ok=True,code='ok',session_id=f.sid,agent_status='disconnected')
            else:
                raise AssertionError('unexpected fixed operation')
        raw = json.dumps(result).encode()
        if f.mode == 'duplicate_json': raw = b'{"ok":true,"ok":false}'
        if f.mode == 'trickle':
            self.connection.sendall(b'HTTP/1.0 200 OK\r\nX: ')
            try:
                for _ in range(80): time.sleep(.05); self.connection.sendall(b'x')
            except OSError: pass
            return
        try:
            self.send_response(status)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(1024*1024+1 if f.mode == 'oversized' else len(raw)))
            if f.mode == 'duplicate_length': self.send_header('Content-Length',str(len(raw)))
            if f.mode == 'chunked': self.send_header('Transfer-Encoding','chunked')
            self.end_headers(); self.wfile.write(raw)
        except OSError: pass


class AgentClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.owner = Runtime(root/'store',root/'private').acquire_owner()
        self.addCleanup(self.owner.close)
        self.runtime = Runtime(root/'store',root/'private')
        self.sid = 'session_'+'1'*32; self.bid = 'binding_'+'2'*32
        self.generation = 'agent_'+'3'*32; self.token = secrets.token_hex(32)
        self.record = dict(schema_version=1,binding_id=self.bid,receipt_session_id=self.sid,
                           actor='Operator',selected_idea_id=None)
        self.owner.persist_binding(self.record)
        self.correlation = dict(request_id='request-1',session_id=self.sid,idea_id='idea_'+'4'*32,
                                accepted_revision=1,draft_version=0,operation='exploration',source_digest='a'*64)
        self.calls = []; self.mode = ''
        self.server = ThreadingHTTPServer(('127.0.0.1',0),FixedHandler)
        self.server.daemon_threads=True; self.server.fixture=self
        self.worker = threading.Thread(target=self.server.serve_forever,daemon=True); self.worker.start()
        self.addCleanup(self.stop)
        self.owner.publish_discovery(self.server.server_port)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.worker.join()

    def client(self, **kwargs): return AgentClient(self.runtime,self.sid,**kwargs)
    def reply(self): return dict(self.correlation,proposal={'exploration':'suggestion'})

    def assert_error(self, cls, action, code=None):
        with self.assertRaises(cls) as caught: action()
        if code: self.assertEqual(caught.exception.code,code)
        self.assertNotIn(self.token,str(caught.exception))
        self.assertNotIn(self.token,repr(caught.exception))
        return caught.exception

    def test_actual_retrieval_events_respond_close_no_secret_in_public_results(self):
        client = self.client(expected_generation=self.generation)
        self.assertNotIn(self.token,repr(client))
        self.assertFalse(hasattr(client,'token')); self.assertFalse(hasattr(client,'__dict__'))
        results=[client.events(timeout=0),client.respond(self.reply()),client.session_close()]
        self.assertNotIn(self.token,json.dumps(results))
        self.assertEqual([c[1] for c in self.calls],['/control/v1/probe','/control/v1/agent-credentials',
                         '/agent/v1/events','/agent/v1/respond','/agent/v1/session-close'])
        self.assert_error(AgentClientError,lambda:client.events(timeout=0),'agent_closed')

    def test_absent_and_ambiguous_sid_never_opens_resumes_or_calls_http(self):
        self.assert_error(RuntimeError,lambda:AgentClient(self.runtime,'session_'+'f'*32),'binding_not_found')
        self.owner.persist_binding(dict(self.record,binding_id='binding_'+'5'*32))
        self.assert_error(RuntimeError,self.client,'binding_ambiguous')
        self.assertEqual(self.calls,[])

    def test_strict_private_response_identity_and_credentials(self):
        for mode in ('nonce','challenge','store','sid','binding','generation','token','extra'):
            with self.subTest(mode=mode):
                self.mode=mode
                self.assert_error(RuntimeError,self.client,'owner_identity_mismatch')
        self.mode=''
        self.assert_error(RuntimeError,lambda:self.client(expected_generation='agent_'+'f'*32),'owner_identity_mismatch')

    def test_runtime_privacy_still_owns_private_delivery(self):
        os.chmod(self.owner.path/'credentials.json',0o644)
        try: self.assert_error(RuntimeError,self.client,'runtime_not_private')
        finally: os.chmod(self.owner.path/'credentials.json',0o600)
        self.assertEqual(self.calls,[])

    def test_invalid_wait_and_payload_never_hit_network(self):
        client=self.client(); baseline=len(self.calls)
        for kwargs in ({'after':True},{'after':-1},{'after':10**12+1},{'timeout':26},{'timeout':float('nan')},{'timeout':True}):
            self.assert_error(AgentClientError,lambda:client.events(**kwargs),'invalid_agent_input')
        for payload in ({},dict(self.reply(),token='secret'),dict(self.reply(),session_id='session_'+'f'*32)):
            self.assert_error(AgentClientError,lambda:client.respond(payload),'invalid_agent_input')
        self.assertEqual(len(self.calls),baseline)

    def test_malformed_oversized_and_secret_responses_fail_redacted(self):
        client=self.client()
        for mode in ('duplicate_json','duplicate_length','chunked','oversized','wrong_event_sid',
                     'wrong_sequence','token_echo','secret_key'):
            with self.subTest(mode=mode):
                self.mode=mode
                self.assert_error(AgentClientError,lambda:client.events(timeout=0),'invalid_agent_response')

    def test_response_mismatch_and_uncertain_error_never_retry(self):
        client=self.client(); self.mode='wrong_response'
        self.assert_error(AgentClientError,lambda:client.respond(self.reply()),'invalid_agent_response')
        self.mode='uncertain'
        exc=self.assert_error(AgentClientError,lambda:client.respond(self.reply()),'agent_request_failed')
        self.assertEqual(exc.write_state,'committed_uncertain')
        self.assertEqual(sum(c[1]=='/agent/v1/respond' for c in self.calls),2)

    def test_total_deadline_stops_trickling_headers(self):
        client=self.client(); self.mode='trickle'; start=time.monotonic()
        self.assert_error(AgentClientError,lambda:client.events(timeout=0),'agent_unavailable')
        self.assertLess(time.monotonic()-start,2.8)

    PNG = b'\x89PNG\r\n\x1a\n' + b'x' * 20

    def asset_kwargs(self, **over):
        return dict(dict(idea_id=self.correlation['idea_id'],revision=2,request_id='request-1',
                         name='shot one.png',mime='image/png',data=self.PNG),**over)

    def test_asset_sends_raw_bytes_headers_and_agent_auth_only(self):
        client = self.client(expected_generation=self.generation)
        value = client.asset(**self.asset_kwargs())
        seen = self.asset_seen; h = seen['headers']
        self.assertEqual(seen['raw'], self.PNG)
        self.assertEqual(h['Content-Type'], 'image/png')
        self.assertEqual(h['X-Idea-Session'], self.sid)
        self.assertEqual(h['X-Idea-Id'], self.correlation['idea_id'])
        self.assertEqual(h['X-Idea-Revision'], '2')
        self.assertEqual(h['X-Idea-Request-Id'], 'request-1')
        self.assertEqual(h['X-Idea-Asset-Name'], 'shot%20one.png')
        self.assertEqual(h['Authorization'], 'Bearer ' + self.token)
        self.assertEqual(value['asset_ids'], [value['asset_id']])
        self.assertNotIn(self.token, json.dumps(value))

    def test_asset_zip_and_invalid_input_never_hit_network(self):
        client = self.client(); self.assertEqual(
            client.asset(**self.asset_kwargs(mime='application/zip',data=b'PK\x03\x04zz',name='p.zip'))['size'], 6)
        baseline = len(self.calls)
        for over in (dict(mime='text/html'), dict(data=b'not a png'), dict(mime='application/zip'), dict(data=b''),
                     dict(revision=0), dict(idea_id='idea_x'), dict(request_id='bad id'), dict(name='')):
            with self.subTest(over=over):
                self.assert_error(AgentClientError,lambda:client.asset(**self.asset_kwargs(**over)),'invalid_agent_input')
        self.assertEqual(len(self.calls), baseline)

    def test_asset_refusal_codes_and_unknown_fields(self):
        client = self.client()
        for mode, code in (('asset_type','unsupported_asset_type'),('asset_big','too_large'),
                           ('asset_session','wrong_session')):
            with self.subTest(mode=mode):
                self.mode = mode
                exc = self.assert_error(AgentClientError,lambda:client.asset(**self.asset_kwargs()),code)
                self.assertIsNone(exc.write_state)
        self.mode = 'asset_extra'
        self.assert_error(AgentClientError,lambda:client.asset(**self.asset_kwargs()),'invalid_agent_response')


class AssetVerbParseTests(unittest.TestCase):
    def run_verb(self, *extra):
        import idea
        args = idea.parser().parse_args(['asset','--session','session_'+'1'*32,'--idea','idea_'+'4'*32,
                                         '--revision','1','--request','request-1',*extra])
        return idea.run_agent(args)

    def test_wrong_or_unknown_type_is_refused_before_any_network_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / 'page.html'; bad.write_bytes(b'<html>')
            result = self.run_verb('--file', str(bad))
            self.assertEqual(result['error']['code'], 'invalid_agent_input')
            png = Path(tmp) / 'shot.png'; png.write_bytes(b'\x89PNG\r\n\x1a\nx')
            from idea_domain import IdeaError
            with self.assertRaises(IdeaError):
                self.run_verb('--file', str(png), '--type', 'text/html')


if __name__ == '__main__': unittest.main()
