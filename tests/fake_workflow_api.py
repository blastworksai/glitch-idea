"""A local fake of the workflow API contract (docs/workflow-api.md), for tests only.

It plays an operator's own workflow system (for example a ticket board) on loopback:
bearer-key auth, health, list, read and write of idea records. Modes let a test make it
misbehave (redirect, wrong schema, oversized or HTML replies, echoing the key).
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading

SCHEMA = 'glitch-idea.workflow/1'


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, status, value, content_type='application/json'):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self, method):
        fake = self.server.fake
        fake.calls.append((method, self.path, self.headers.get('Authorization')))
        if self.headers.get('Authorization') != 'Bearer ' + fake.key:
            return self._send(401, dict(ok=False, code='unauthorized'))
        if fake.mode == 'redirect':
            self.send_response(302); self.send_header('Location', 'http://127.0.0.1:1/'); self.send_header('Content-Length', '0'); self.end_headers(); return
        if fake.mode == 'html':
            return self._send(200, b'<html>login</html>', 'text/html')
        if fake.mode == 'huge':
            return self._send(200, dict(ok=True, schema=SCHEMA, service='x' * (2 * 1024 * 1024)))
        if fake.mode == 'echo':
            return self._send(200, dict(ok=True, schema=SCHEMA, service='Echo ' + fake.key))
        prefix = fake.prefix
        if not self.path.startswith(prefix):
            return self._send(404, dict(ok=False, code='not_found'))
        path = self.path[len(prefix):]
        if method == 'GET' and path == '/v1/health':
            return self._send(200, dict(ok=True, schema='wrong/0' if fake.mode == 'schema' else SCHEMA, service=fake.service))
        if method == 'GET' and path == '/v1/ideas':
            return self._send(200, dict(ok=True, ideas=[dict(idea_id=key, ref=value['ref']) for key, value in fake.ideas.items()]))
        match = re.fullmatch(r'/v1/ideas/(idea_[0-9a-f]{32})', path)
        if match and method == 'GET':
            if match[1] not in fake.ideas:
                return self._send(404, dict(ok=False, code='not_found'))
            return self._send(200, dict(ok=True, idea=fake.ideas[match[1]]['record']))
        if match and method == 'PUT':
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if body.get('schema') != SCHEMA or body.get('idea', {}).get('idea_id') != match[1]:
                return self._send(400, dict(ok=False, code='invalid_input'))
            ref = fake.ideas.get(match[1], {}).get('ref') or 'IDEA-%d' % (len(fake.ideas) + 1)
            fake.ideas[match[1]] = dict(ref=ref, record=body['idea'])
            return self._send(200, dict(ok=True, idea_id=match[1], ref=ref))
        return self._send(404, dict(ok=False, code='not_found'))

    def do_GET(self):
        self._handle('GET')

    def do_PUT(self):
        self._handle('PUT')


class FakeWorkflowApi:
    def __init__(self, key='fixture-key-0123456789', prefix='/workflow', service='Fake board'):
        self.key, self.prefix, self.service = key, prefix, service
        self.mode, self.calls, self.ideas = '', [], {}
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        self.server.daemon_threads = True
        self.server.fake = self
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return 'http://127.0.0.1:%d%s' % (self.server.server_port, self.prefix)

    def __enter__(self):
        self.worker.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown(); self.server.server_close(); self.worker.join()
