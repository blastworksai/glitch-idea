"""real private control HTTP and admitted commit drain proof."""
from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
from idea_bridge import ApplicationBinding, BridgeServer, BridgeError, Response, CONTROL_PATHS, check
from idea_service import Service, TrustedContext
from idea_store import Store
from test_bridge import FixturePolicy


class Activity:
    def __init__(self):
        self.lock=threading.Lock();self.active=0;self.touches=0;self.entered=threading.Event()
    @contextmanager
    def active_call(self):
        with self.lock:self.active+=1;self.entered.set()
        try:yield
        finally:
            with self.lock:self.active-=1
    def touch(self):
        with self.lock:self.touches+=1


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.workspace=self.root/'workspace';self.workspace.mkdir()
        self.store=Store(self.root/'ideas');self.sid=self.store.create_session()
        self.app=Service(self.store,{},TrustedContext('Operator',self.sid))
        self.policy=FixturePolicy(ApplicationBinding(self.app));self.activity=Activity()
        self.calls=[];self.callback=None
        self.server=BridgeServer(self.policy,control=self.control,activity=self.activity,body_deadline=1)
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.stop_admission();self.server.shutdown();self.server.drain();self.server.server_close();self.worker.join()

    def control(self,request,payload):
        check(request.header('Authorization')=='Bearer fixture-owner','owner_unauthorized',401)
        self.calls.append((request.path,payload))
        return Response(dict(ok=True,code='ok'),after_send=self.callback)

    def request(self,path='/control/v1/probe',body=None,*,extra=None,method='POST'):
        body={} if body is None else body
        headers={'Host':self.server.host,'Content-Type':'application/json'}
        if path.split('?',1)[0] in CONTROL_PATHS: headers['Authorization']='Bearer fixture-owner'
        headers.update(extra or {})
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            conn.request(method,path,json.dumps(body),headers)
            response=conn.getresponse();return response.status,json.loads(response.read())
        finally:conn.close()

    def raw(self,request):
        sock=socket.create_connection(('127.0.0.1',self.server.server_port),timeout=3)
        try:
            sock.sendall(request);chunks=[]
            while True:
                value=sock.recv(65536)
                if not value:break
                chunks.append(value)
            return b''.join(chunks)
        finally:sock.close()

    def test_fixed_private_routes_and_after_flush_callback_runs_once(self):
        event=threading.Event();counts=[]
        def complete():counts.append(True);event.set();raise RuntimeError('private-secret diagnostic')
        self.callback=complete
        raw=self.raw(('POST /control/v1/stop HTTP/1.0\r\nHost: '+self.server.host+'\r\nContent-Type: application/json\r\nAuthorization: Bearer fixture-owner\r\nContent-Length: 2\r\n\r\n{}').encode())
        self.assertTrue(event.wait(1));self.assertEqual(len(counts),1)
        self.assertEqual(raw.count(b'HTTP/1.0'),1);self.assertIn(b'200 OK',raw);self.assertNotIn(b'private-secret',raw)
        self.assertEqual(json.loads(raw.split(b'\r\n\r\n',1)[1]),dict(ok=True,code='ok'))
        self.callback=None
        for path in ('/control/v1/probe','/control/v1/binding-open'):
            self.assertEqual(self.request(path)[0],200)
        self.assertEqual(self.activity.touches,3)

    def test_browser_headers_duplicate_authorization_and_wrong_host_refused(self):
        for name,value in [('Origin',self.server.origin),('Cookie','browser=x'),('X-CSRF-Token','x'),('X-Idea-Binding','binding_'+'f'*32)]:
            with self.subTest(name=name):self.assertEqual(self.request(extra={name:value})[0],403)
        self.assertEqual(self.request(extra={'Host':'wrong'})[0],403)
        raw=self.raw(('POST /control/v1/probe HTTP/1.0\r\nHost: '+self.server.host+'\r\nContent-Type: application/json\r\nAuthorization: Bearer fixture-owner\r\nAuthorization: Bearer fixture-owner\r\nContent-Length: 2\r\n\r\n{}').encode())
        self.assertIn(b'400 Bad Request',raw);self.assertIn(b'ambiguous_headers',raw)
        self.assertEqual(self.calls,[]);self.assertEqual(self.activity.touches,0)

    def test_default_deny_paths_queries_types_and_auth_failure_no_touch(self):
        self.server.control=None
        self.assertEqual(self.request()[0],503)
        self.server.control=self.control
        for path in ('/control/v1/probe?x=1','/control/v1/probe?','/control/v1/probe/','/control/v1/%70robe','/control/v1/other'):
            self.assertNotEqual(self.request(path)[0],200)
        self.assertEqual(self.request(method='GET')[0],405)
        self.assertEqual(self.request(body=[])[0],400)
        self.assertEqual(self.request(extra={'Authorization':'wrong'})[0],401)
        self.assertEqual(self.calls,[]);self.assertEqual(self.activity.touches,0)

    def test_stop_admission_ack_and_future_dispatch_busy(self):
        stopped=threading.Event()
        def stop_after_ack():self.server.stop_admission();stopped.set()
        self.callback=stop_after_ack
        self.assertEqual(self.request('/control/v1/stop')[0],200)
        self.assertTrue(stopped.wait(1))
        raw=self.raw(('POST /control/v1/probe HTTP/1.0\r\nHost: '+self.server.host+'\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}').encode())
        self.assertIn(b'503 Service Unavailable',raw)
        raw=self.raw(('GET /api/v1/state HTTP/1.0\r\nHost: '+self.server.host+'\r\n\r\n').encode())
        self.assertIn(b'503 Service Unavailable',raw)
        self.assertEqual(len(self.calls),1)
        self.server.drain()
        self.assertEqual(self.activity.active,0)

    def test_activity_only_verified_pair_auth_control_and_body_read_lifetime(self):
        self.assertEqual(self.request('/api/v1/state',method='GET')[0],401)
        self.assertEqual(self.request('/api/v1/pair',{'code':'wrong'},extra={'Origin':self.server.origin})[0],401)
        self.assertEqual(self.activity.touches,0)
        self.assertEqual(self.request('/api/v1/pair',{'code':'fixture-code'},extra={'Origin':self.server.origin})[0],200)
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        conn.request('GET','/api/v1/state',headers={'Cookie':'browser=fixture-cookie'});resp=conn.getresponse();resp.read();conn.close()
        self.assertEqual(resp.status,200);self.assertEqual(self.activity.touches,2)
        # Admitted partial body is included in active_call and drain accounting.
        self.activity.entered.clear()
        sock=socket.create_connection(('127.0.0.1',self.server.server_port),timeout=3)
        self.addCleanup(sock.close)
        sock.sendall(('POST /control/v1/probe HTTP/1.0\r\nHost: '+self.server.host+'\r\nContent-Type: application/json\r\nAuthorization: Bearer fixture-owner\r\nContent-Length: 2\r\n\r\n{').encode())
        self.assertTrue(self.activity.entered.wait(1));self.assertGreaterEqual(self.activity.active,1)
        self.server.stop_admission();done=threading.Event()
        waiter=threading.Thread(target=lambda:(self.server.drain(),done.set()));waiter.start()
        self.assertFalse(done.wait(.05));sock.sendall(b'}');sock.recv(65536)
        self.assertTrue(done.wait(2));waiter.join();self.assertEqual(self.activity.active,0)

    def test_drain_waits_delayed_real_commit_and_preserves_receipt(self):
        entered=threading.Event();release=threading.Event();result=[]
        def resolver(workspace):entered.set();release.wait();return workspace
        self.app.workspace_resolver=resolver
        payload=dict(request_id='capture-drain',raw_text='actual admitted commit',workspace=dict(name='Explicit',path=str(self.workspace),confirmed=True))
        def capture():
            result.append(self.request('/api/v1/capture',payload,extra={'Origin':self.server.origin,'Cookie':'browser=fixture-cookie','X-CSRF-Token':'fixture-csrf'}))
        request=threading.Thread(target=capture);request.start();self.assertTrue(entered.wait(2))
        self.server.stop_admission();done=threading.Event()
        waiter=threading.Thread(target=lambda:(self.server.drain(),done.set()));waiter.start()
        try:
            self.assertFalse(done.wait(.05))
            raw=self.raw(('GET /api/v1/state HTTP/1.0\r\nHost: '+self.server.host+'\r\n\r\n').encode())
            self.assertIn(b'503 Service Unavailable',raw)
        finally:release.set()
        request.join(3);self.assertTrue(done.wait(3));waiter.join()
        self.assertEqual(result[0][0],200)
        receipt=self.store.request_result(self.sid,'capture-drain')
        self.assertEqual(receipt['idea_id'],result[0][1]['idea_id'])
        with self.store.transaction() as state:self.assertEqual(len(state['ideas']),1)

    def test_drain_requires_supervisor_and_closed_admission(self):
        with self.assertRaises(RuntimeError):self.server.drain()
        errors=[];finished=threading.Event()
        def after_send():
            try:
                self.server.stop_admission()
                try:self.server.drain()
                except RuntimeError:errors.append(True)
            finally:finished.set()
        self.callback=after_send
        self.assertEqual(self.request('/control/v1/stop')[0],200)
        self.assertTrue(finished.wait(10))
        self.server.drain();self.assertEqual(errors,[True])

if __name__=='__main__':unittest.main()
