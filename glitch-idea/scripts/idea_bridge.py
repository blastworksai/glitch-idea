"""Bounded loopback transport with injected session policy.

No launcher or unauthenticated application exists here. J5b2 supplies the real
TrustedSessionPolicy and owned lifecycle. application_factory may compose a
Service with the literal load_registry() handlers. ApplicationBinding identity
must remain stable until a policy revokes/rebinds it; authorize runs again under
its lock before any application read, mutation or receipt replay. Policy methods
must not perform network waits under Store locks. Agent waits have four separate operation slots; eight ordinary slots remain
available, with twelve total handlers including bounded header parsing.
"""
from dataclasses import dataclass, field
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import socket
import stat
import threading
import time
from types import MappingProxyType
from urllib.parse import parse_qsl, unquote_to_bytes, urlsplit

from idea_domain import IdeaError, MAX_INPUT, MAX_STATE
from idea_steps import ROUTE_SPECS, TrustedRoute, load_registry

MAX_JSON = MAX_INPUT
# Saved state may duplicate the selected legacy draft. Writes and
# private replies stay at MAX_JSON; only authenticated successful state reads
# use this inherited storage bound plus fixed-envelope/overlay allowance.
MAX_STATE_RESPONSE = 2 * MAX_STATE + 2 * MAX_INPUT
MAX_UPLOAD = 25 * 1024 * 1024
MAX_HEADERS = 16384
MAX_HEADER_LINE = 8192
MAX_REQUEST_LINE = 4096
MAX_HEADER_COUNT = 64
BODY_DEADLINE = 10.0
MAX_HANDLERS = 8
MAX_AGENT_WAITS = 4
MAX_TOTAL_HANDLERS = MAX_HANDLERS + MAX_AGENT_WAITS
REQUEST_ID = re.compile(r'[A-Za-z0-9_.:-]{1,128}')
IDEA_ID = re.compile(r'idea_[0-9a-f]{32}')
BINDING_ID = re.compile(r'binding_[0-9a-f]{32}')
OPAQUE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}')
STATIC = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/index.html': ('index.html', 'text/html; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
    '/api.js': ('api.js', 'text/javascript; charset=utf-8'),
    '/folds.js': ('folds.js', 'text/javascript; charset=utf-8'),
    '/ideas.js': ('ideas.js', 'text/javascript; charset=utf-8'),
    '/setup.js': ('setup.js', 'text/javascript; charset=utf-8'),  # the Setup pane, loaded on first open
    '/styles.css': ('styles.css', 'text/css; charset=utf-8'),
    **{'/steps/' + name + '.js': ('steps/' + name + '.js', 'text/javascript; charset=utf-8')
       for name in ('shape', 'method', 'visualize', 'assess', 'review')},
}
CONTROL_PATHS = frozenset('/control/v1/' + name for name in ('probe', 'stop', 'binding-open', 'agent-credentials'))
AGENT_PATHS = frozenset('/agent/v1/' + name for name in ('events', 'respond', 'fill', 'session-close'))
AGENT_RESPONSE_KEYS = frozenset(('request_id', 'session_id', 'idea_id', 'accepted_revision',
                                'draft_version', 'operation', 'source_digest', 'proposal'))
SESSION_ID = re.compile(r'session_[0-9a-f]{32}')
APP_POSTS = frozenset(('capture', 'draft', 'accept', 'navigate', 'rerank'))
SECURITY_HEADERS = {
    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'",
}


class BridgeError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status


def check(condition, code, status=400):
    if not condition:
        raise BridgeError(code, status)


def decode_json(raw):
    check(len(raw) <= MAX_JSON, 'too_large', 413)
    def pairs(items):
        value = {}
        for key, item in items:
            check(key not in value, 'duplicate_key')
            value[key] = item
        return value
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(BridgeError('nonfinite')))
        check(type(value) is dict, 'invalid_input')
        pending, count = [(value, 1)], 0
        while pending:
            item, depth = pending.pop()
            count += 1
            check(depth <= 32 and count <= 100000, 'too_large', 413)
            if type(item) is dict:
                for key, child in item.items():
                    pending.extend(((key, depth + 1), (child, depth + 1)))
            elif type(item) is list:
                pending.extend((child, depth + 1) for child in item)
            elif type(item) is float:
                check(math.isfinite(item), 'nonfinite')
            elif type(item) is str:
                check(not any(0xD800 <= ord(char) <= 0xDFFF for char in item), 'invalid_json')
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise BridgeError('invalid_json') from None


@dataclass(frozen=True)
class RequestInfo:
    method: str
    path: str
    headers: object
    origin: str
    resource_id: str | None = None

    def header(self, name):
        return self.headers.get(name.lower())


@dataclass(eq=False)
class ApplicationBinding:
    application: object
    lock: object = field(default_factory=threading.RLock)


@dataclass(frozen=True)
class Response:
    body: object
    status: int = 200
    headers: object = field(default_factory=dict)
    content_type: str = 'application/json'
    after_send: object = None  # Trusted callback only; never a wire-selected callable.


class TrustedSessionPolicy:
    """Default fail-closed policy; production implements these trusted methods.

    authorize(request,write=False) authenticates cookie + write CSRF and returns
    a stable ApplicationBinding. pair returns Response (including Set-Cookie),
    session returns bootstrap JSON, transport records the validated observation,
    activity (empty body) tells the agent broker a human is still editing.
    The bridge pins Host/Origin independently; policy cannot widen those checks.
    """
    def authorize(self, request, *, write=False):
        raise BridgeError('browser_unauthorized', 401)

    def pair(self, request, payload):
        raise BridgeError('pairing_unavailable', 503)

    def session(self, binding):
        raise BridgeError('browser_unauthorized', 401)

    def transport(self, binding, payload):
        raise BridgeError('browser_unauthorized', 401)

    def activity(self, binding):
        raise BridgeError('browser_unauthorized', 401)

    def after_application(self, binding, result):
        """Persist selected ID in private runtime, never domain authority.

        Runs under binding.lock. Registry inspection may briefly take its lock;
        no registry lock may wait for binding.lock (including revoke/resume).
        Injected runtime adapters own persistence; no future module import here.
        """


class _DeadlineInput:
    """Absolute read deadline, including a peer trickling partial lines/bytes."""
    def __init__(self, stream, connection, deadline):
        self.stream, self.connection, self.deadline = stream, connection, deadline
        self.buffer = bytearray()
        self.header_bytes = self.header_count = 0

    def _fill(self, size):
        remaining = self.deadline - time.monotonic()
        check(remaining > 0, 'request_timeout')
        self.connection.settimeout(remaining)
        try:
            raw = self.stream.read1(size)
        except (TimeoutError, socket.timeout):
            raise BridgeError('request_timeout') from None
        check(time.monotonic() <= self.deadline, 'request_timeout')
        return raw

    def readline(self, limit=-1):
        limit = min(MAX_HEADER_LINE + 1, limit if limit >= 0 else MAX_HEADER_LINE + 1)
        while b'\n' not in self.buffer and len(self.buffer) < limit:
            raw = self._fill(min(4096, limit - len(self.buffer)))
            if not raw:
                break
            self.buffer.extend(raw)
        newline = self.buffer.find(b'\n')
        size = min(limit, newline + 1 if newline >= 0 else len(self.buffer))
        raw = bytes(self.buffer[:size]); del self.buffer[:size]
        self.header_bytes += len(raw); self.header_count += 1
        check(len(raw) <= MAX_HEADER_LINE and self.header_bytes <= MAX_HEADERS
              and self.header_count <= MAX_HEADER_COUNT + 2, 'headers_too_large', 413)
        if self.header_count == 1:
            check(len(raw) <= MAX_REQUEST_LINE, 'request_target_too_large', 413)
        check(not raw or raw.endswith(b'\r\n'), 'malformed_http')
        if self.header_count > 1 and raw not in (b'', b'\r\n'):
            check(re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+:[\t\x20-\x7e]*\r\n", raw) is not None,
                  'malformed_http')
        return raw

    def read(self, size):
        check(time.monotonic() <= self.deadline, 'request_timeout')
        if self.buffer:
            raw = bytes(self.buffer[:size]); del self.buffer[:size]
            return raw
        return self._fill(min(size, 65536))

    def close(self):
        self.stream.close()


class BoundedBody:
    """Upload extension's exact-length stream. No arbitrary file capability.

    Trusted ingestion must consume it synchronously before returning and enforce
    per-set/type/signature limits. Each read enforces the original absolute HTTP
    deadline; this stream must never be read while holding a Store lock.
    """
    def __init__(self, reader, length):
        self.reader, self.remaining = reader, length

    def read(self, size=65536):
        check(type(size) is int and 0 < size <= 65536, 'invalid_read_size')
        if not self.remaining:
            return b''
        raw = self.reader.read(min(size, self.remaining))
        check(bool(raw), 'incomplete_body')
        self.remaining -= len(raw)
        return raw

    def read_all(self):
        chunks = []
        while self.remaining:
            chunks.append(self.read())
        return b''.join(chunks)


def error_status(code):
    if code in ('not_found', 'request_not_found', 'asset_not_found', 'unknown_route'):
        return 404
    if code == 'too_large':
        return 413
    if (code.endswith('_conflict') or code.startswith('stale_') or
            code in ('changed_artifact', 'recovery_conflict')):
        return 409
    if code in ('step_unavailable', 'agent_unavailable', 'workspace_unavailable',
                'receipt_capacity_exhausted', 'request_busy', 'request_capacity'):
        return 503
    if code in ('durability_uncertain', 'corrupt_store', 'corrupt_receipts',
                'receipt_session_missing', 'migration_required', 'io_error'):
        return 500
    return 400


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8
    allow_reuse_address = False

    def __init__(self, policy=None, *, routes=None, body_deadline=BODY_DEADLINE, control=None, activity=None, agent=None):
        self.policy = policy if policy is not None else TrustedSessionPolicy()
        _, packaged = load_registry()
        self.routes = packaged if routes is None else dict(routes)
        for name, route in self.routes.items():
            if not (name in ROUTE_SPECS and isinstance(route, TrustedRoute)
                    and route.route_id == name and callable(route.handler)):
                raise ValueError('Invalid trusted route registry')
        if not (type(body_deadline) in (int, float) and math.isfinite(body_deadline) and 0 < body_deadline <= BODY_DEADLINE):
            raise ValueError('Invalid trusted body deadline')
        if control is not None and not callable(control):
            raise ValueError('Invalid trusted control callback')
        if activity is not None and not (callable(getattr(activity, 'active_call', None)) and callable(getattr(activity, 'touch', None))):
            raise ValueError('Invalid trusted activity tracker')
        if agent is not None and not callable(agent):
            raise ValueError('Invalid trusted agent callback')
        self.control, self.activity, self.agent = control, activity, agent
        self._admission = threading.Condition()
        self._accepting, self._admitted = True, 0
        self._handlers = set()
        self.body_deadline = body_deadline
        self.slots = threading.BoundedSemaphore(MAX_HANDLERS)
        self.wait_slots = threading.BoundedSemaphore(MAX_AGENT_WAITS)
        self.total_slots = threading.BoundedSemaphore(MAX_TOTAL_HANDLERS)
        self.web = Path(__file__).resolve().parent.parent / 'web'
        super().__init__(('127.0.0.1', 0), Handler)
        self.origin = 'http://127.0.0.1:' + str(self.server_port)
        self.host = '127.0.0.1:' + str(self.server_port)

    def process_request(self, request, address):
        with self._admission:
            accepted = self._accepting and self.total_slots.acquire(blocking=False)
            if accepted:
                self._admitted += 1
        if not accepted:
            # No body parsing/auth/state lookup under overload; no credential log.
            raw = b'{"ok":false,"code":"busy"}'
            response = ('HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\n'
                        'Content-Length: ' + str(len(raw)) + '\r\nConnection: close\r\n' +
                        ''.join(key + ': ' + value + '\r\n' for key, value in SECURITY_HEADERS.items()) + '\r\n').encode() + raw
            try:
                request.settimeout(0.1); request.sendall(response)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self._release_admission()
            raise

    def _release_admission(self):
        self.total_slots.release()
        with self._admission:
            self._admitted -= 1
            self._admission.notify_all()

    def process_request_thread(self, request, address):
        with self._admission:
            self._handlers.add(threading.get_ident())
        try:
            if self.activity is None:
                super().process_request_thread(request, address)
            else:
                with self.activity.active_call():
                    super().process_request_thread(request, address)
        finally:
            with self._admission:
                self._handlers.discard(threading.get_ident())
            self._release_admission()

    def stop_admission(self):
        """Refuse new sockets; admitted handlers may finish publication."""
        with self._admission:
            self._accepting = False

    def drain(self):
        """Supervisor-only wait; no timeout may abandon an active commit.

        Stop admission, shutdown serve_forever from a supervisor, drain, then
        server_close before releasing runtime ownership. No cached PID kills.
        """
        with self._admission:
            if threading.get_ident() in self._handlers:
                raise RuntimeError('Drain must run outside request handlers')
            if self._accepting:
                raise RuntimeError('Stop admission before drain')
            while self._admitted:
                self._admission.wait()

    def touch(self):
        if self.activity is not None:
            self.activity.touch()

    def handle_error(self, request, address):
        pass  # No stack trace may dump credentials or domain data.


@contextmanager
def _serialized(binding, timeout):
    check(binding.lock.acquire(timeout=timeout), 'busy', 503)
    try:
        yield
    finally:
        binding.lock.release()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    server_version = 'GlitchIdea'
    sys_version = ''

    def setup(self):
        super().setup()
        self.rfile = _DeadlineInput(self.rfile, self.connection,
                                   time.monotonic() + self.server.body_deadline)

    def log_message(self, *_):
        pass

    def send_error(self, code, message=None, explain=None):
        self.send(Response(dict(ok=False, code='malformed_http'), code))

    def send(self, response, *, json_limit=MAX_JSON):
        check(response.after_send is None or callable(response.after_send), 'invalid_response', 500)
        body = response.body
        if type(body) is bytes:
            raw = body
        else:
            check(type(body) is dict and type(body.get('ok')) is bool and type(body.get('code')) is str,
                  'invalid_response', 500)
            raw = json.dumps(body, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
            check(len(raw) <= json_limit, 'response_too_large', 500)
        check(type(response.status) is int and 100 <= response.status <= 599, 'invalid_response', 500)
        check(type(response.content_type) is str and '\r' not in response.content_type and '\n' not in response.content_type,
              'invalid_response', 500)
        check(type(response.headers) is dict and set(response.headers) <= {'Set-Cookie', 'Content-Disposition'},
              'invalid_response', 500)
        check(all(type(value) is str and '\r' not in value and '\n' not in value
                  for value in response.headers.values()), 'invalid_response', 500)
        self.response_started = True
        self.send_response(response.status)
        self.send_header('Content-Type', response.content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Connection', 'close')
        for key, value in {**SECURITY_HEADERS, **response.headers}.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)
        self.wfile.flush()
        if response.after_send is not None:
            try:
                response.after_send()
            except Exception:
                pass  # Already flushed; never send twice or log private details.

    def handle_one_request(self):
        self.close_connection = True
        self.request_version = 'HTTP/1.0'
        self.requestline = ''
        try:
            self.raw_requestline = self.rfile.readline(MAX_REQUEST_LINE + 1)
            if not self.raw_requestline:
                return
            check(len(self.raw_requestline) <= MAX_REQUEST_LINE, 'request_target_too_large', 413)
            if not self.parse_request():
                return
            # No keepalive, pipelining or Expect:100-continue processing.
            self.close_connection = True
            self.dispatch()
        except BridgeError as exc:
            if getattr(self, 'response_started', False):
                return
            error = dict(ok=False, code=exc.code)
            if getattr(self, 'authorized_binding', None) is not None and exc.status not in (401, 403):
                self._write_error(error, 'committed_uncertain' if exc.status >= 500 and getattr(self, 'application_started', False) else 'not_applied')
            self.send(Response(error, exc.status))
        except Exception:
            if getattr(self, 'response_started', False):
                return
            try:
                error = dict(ok=False, code='internal_error')
                if getattr(self, 'application_started', False):
                    # Unexpected application failure may follow publication.
                    self._write_error(error, 'committed_uncertain')
                self.send(Response(error, 500))
            except OSError:
                pass

    def _human_activity(self, binding, response, operation):
        """A successful browser WRITE of any kind keeps a connected agent from idling out.

        Reads (state, a stored request result) never count, so re-reading a receipt
        cannot hold the idle clock. The write has already committed: a failure to
        move the idle clock must not turn it into an error, so it is dropped here.
        """
        body = response.body
        # Opening an idea (private selection) writes no receipt, so it carries no write_state.
        if not (200 <= response.status < 300 and type(body) is dict and body.get('ok') is True
                and (body.get('write_state') in ('applied', 'no_op') or operation == 'selection')):
            return
        try:
            self.server.policy.activity(binding)
        except Exception:
            pass

    def _write_error(self, error, write_state):
        if self.command in ('POST', 'PUT') and not getattr(self, 'private_selection', False):
            error['write_state'] = write_state
            payload = getattr(self, 'application_payload', None)
            if type(payload) is dict and type(payload.get('request_id')) is str and REQUEST_ID.fullmatch(payload['request_id']):
                error['request_id'] = payload['request_id']

    def _capabilities(self, result):
        # Capability is installed trusted code, never optimistic UI completion.
        if type(result) is dict and type(result.get('capabilities')) is dict:
            result = dict(result, capabilities=dict(result['capabilities']))
            result['capabilities']['uploads'] = all(name in self.server.routes
                for name in ('uploads', 'upload-bytes', 'attachment'))
        return result

    def _request(self):
        for name in ('Host', 'Origin', 'Content-Type', 'Cookie', 'X-CSRF-Token', 'X-Idea-Binding', 'X-Idea-Tab', 'Expect', 'Authorization',
                     'X-Idea-Agent-Binding', 'X-Idea-Agent-Generation'):
            check(len(self.headers.get_all(name, [])) <= 1, 'ambiguous_headers')
        check(self.headers.get('Host') == self.server.host, 'wrong_host', 403)
        check(self.headers.get('Origin') in (None, self.server.origin), 'wrong_origin', 403)
        check(self.headers.get('Transfer-Encoding') is None, 'transfer_encoding_refused')
        check(self.headers.get('Expect') is None, 'expect_refused')
        check(self.path.startswith('/') and not self.path.startswith('//') and '#' not in self.path
              and '\\' not in self.path and all(32 < ord(char) < 127 for char in self.path), 'invalid_route')
        parsed = urlsplit(self.path)
        check(not parsed.scheme and not parsed.netloc, 'invalid_route')
        if parsed.path in AGENT_PATHS or parsed.path in CONTROL_PATHS:
            check(all(self.headers.get(name) is None for name in ('Sec-Fetch-Site', 'Sec-Fetch-Mode')),
                  'private_browser_metadata_refused', 403)
        if parsed.path not in AGENT_PATHS:
            check(all(self.headers.get(name) is None for name in
                      ('X-Idea-Agent-Binding', 'X-Idea-Agent-Generation')),
                  'agent_headers_refused', 403)
            if parsed.path not in CONTROL_PATHS:
                check(self.headers.get('Authorization') is None, 'authorization_refused', 403)
        if parsed.path in AGENT_PATHS:
            # Presence alone is forbidden on private routes, even when the
            # browser selector itself is empty or malformed.
            check(all(self.headers.get(name) is None for name in
                      ('Origin', 'Cookie', 'X-CSRF-Token', 'X-Idea-Binding', 'X-Idea-Tab')),
                  'agent_browser_headers_refused', 403)
        else:
            selector = self.headers.get('X-Idea-Binding')
            check(selector is None or BINDING_ID.fullmatch(selector) is not None, 'invalid_binding')
        headers = MappingProxyType({key.lower(): value for key, value in self.headers.items()})
        return parsed, RequestInfo(self.command, parsed.path, headers, self.server.origin)

    def _route(self, parsed):
        path = parsed.path
        if path in AGENT_PATHS:
            operation = path.rsplit('/', 1)[-1]
            check(self.command == ('GET' if operation == 'events' else 'POST'), 'method_refused', 405)
            return 'agent-' + operation, None, None if operation == 'events' else 'json'
        if path in CONTROL_PATHS:
            check(self.command == 'POST', 'method_refused', 405)
            return 'control', None, 'json'
        if path in STATIC:
            check(self.command == 'GET', 'method_refused', 405)
            return 'static', None, None
        if path in ('/api/v1/session', '/api/v1/state'):
            check(self.command == 'GET', 'method_refused', 405)
            return path.rsplit('/', 1)[-1], None, None
        if path.startswith('/api/v1/requests/'):
            check(self.command == 'GET', 'method_refused', 405)
            try:
                key = unquote_to_bytes(path.removeprefix('/api/v1/requests/')).decode('utf-8')
            except UnicodeError:
                raise BridgeError('invalid_input') from None
            check(REQUEST_ID.fullmatch(key) is not None, 'invalid_input')
            return 'request', key, None
        if path in {'/api/v1/' + name for name in APP_POSTS | {'pair', 'transport', 'activity'}}:
            check(self.command == 'POST', 'method_refused', 405)
            return path.removeprefix('/api/v1/'), None, 'json'
        for name, (method, template, kind) in ROUTE_SPECS.items():
            key = None
            if '{id}' in template:
                prefix, suffix = template.split('{id}')
                if not (path.startswith(prefix) and path.endswith(suffix)):
                    continue
                key = path[len(prefix):len(path) - len(suffix) if suffix else None]
                if OPAQUE_ID.fullmatch(key) is None:
                    continue
            elif path != template:
                continue
            check(self.command == method, 'method_refused', 405)
            return name, key, kind
        raise BridgeError('unknown_route', 404)

    def _body(self, kind):
        lengths = self.headers.get_all('Content-Length', [])
        if kind is None:
            check(not lengths or lengths == ['0'], 'unexpected_body')
            return None
        check(len(lengths) == 1 and re.fullmatch(r'[0-9]{1,10}', lengths[0]) is not None,
              'length_required')
        length = int(lengths[0])
        maximum = MAX_JSON if kind == 'json' else MAX_UPLOAD
        check(0 < length <= maximum, 'too_large', 413)
        expected = 'application/json' if kind == 'json' else 'application/octet-stream'
        check(self.headers.get('Content-Type') == expected, 'content_type_required')
        stream = BoundedBody(self.rfile, length)
        return decode_json(stream.read_all()) if kind == 'json' else stream

    def dispatch(self):
        parsed, request = self._request()
        operation, key, kind = self._route(parsed)
        slots = self.server.wait_slots if operation == 'agent-events' else self.server.slots
        check(slots.acquire(blocking=False), 'busy', 503)
        try:
            self._dispatch(parsed, request, operation, key, kind)
        finally:
            slots.release()

    def _dispatch(self, parsed, request, operation, key, kind):
        self.private_selection = operation == 'selection'
        request = RequestInfo(request.method, request.path, request.headers, request.origin, key)
        if operation.startswith('agent-'):
            self._agent(parsed, request, operation.removeprefix('agent-'), kind)
            return
        try:
            query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
        except ValueError:
            raise BridgeError('invalid_query') from None
        allowed_query = {'idea_id': IDEA_ID}
        if operation == 'static' and parsed.path in ('/', '/index.html'):
            allowed_query['binding'] = BINDING_ID
        check(not query or (parsed.path in ('/', '/index.html', '/api/v1/state')
                           and len({name for name, _ in query}) == len(query)
                           and all(name in allowed_query and allowed_query[name].fullmatch(value) is not None
                                   for name, value in query)), 'invalid_query')
        if operation in ('ideas', 'selection', 'handoff'):
            check('?' not in self.path, 'invalid_query')
        if operation == 'static':
            self._body(None)
            relative, content_type = STATIC[parsed.path]
            path = self.server.web / relative
            check(not self.server.web.is_symlink(), 'static_unavailable', 503)
            # Every child component is checked before reading; no generic path.
            current = self.server.web
            for part in Path(relative).parts:
                current = current / part
                check(not current.is_symlink(), 'static_unavailable', 503)
            try:
                info = path.stat()
                check(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_JSON, 'static_unavailable', 503)
                self.send(Response(path.read_bytes(), content_type=content_type))
            except FileNotFoundError:
                raise BridgeError('unknown_route', 404) from None
            return
        if operation == 'control':
            check('?' not in self.path, 'invalid_query')
            check(all(request.header(name) is None for name in
                      ('Origin', 'Cookie', 'X-CSRF-Token', 'X-Idea-Binding', 'X-Idea-Tab')),
                  'control_browser_headers_refused', 403)
            check(self.server.control is not None, 'control_unavailable', 503)
            payload = self._body(kind)
            response = self.server.control(request, payload)
            check(isinstance(response, Response), 'invalid_response', 500)
            if 200 <= response.status < 300 and type(response.body) is dict and response.body.get('ok') is True:
                self.server.touch()
            self.send(response)
            return
        if kind is not None:
            check(request.header('Origin') == self.server.origin, 'origin_required', 403)
        if operation == 'pair':
            payload = self._body(kind)
            check(set(payload) == {'code'} and type(payload['code']) is str and 0 < len(payload['code']) <= 128,
                  'invalid_input')
            response = self.server.policy.pair(request, payload)
            body = response.body if isinstance(response, Response) else response
            status = response.status if isinstance(response, Response) else 200
            if type(body) is dict and body.get('ok') is True and 200 <= status < 300:
                self.server.touch()
            self.send(response if isinstance(response, Response) else Response(response))
            return
        binding = self.server.policy.authorize(request, write=kind is not None)
        check(isinstance(binding, ApplicationBinding), 'invalid_binding', 500)
        self.authorized_binding = binding
        self.server.touch()
        payload = self._body(kind)  # Network reads are outside application/Store locks.
        self.application_payload = payload
        with _serialized(binding, self.server.body_deadline):
            check(self.server.policy.authorize(request, write=kind is not None) is binding,
                  'browser_unauthorized', 401)
            try:
                app = binding.application
                previous_selection = app.context.selected_idea_id if operation == 'selection' else None
                if operation == 'session':
                    result = self.server.policy.session(binding)
                elif operation == 'transport':
                    check(set(payload) == {'host', 'origin', 'secure_context'}
                          and payload['host'] == self.server.host and payload['origin'] == self.server.origin
                          and type(payload['secure_context']) is bool, 'transport_mismatch', 403)
                    result = self.server.policy.transport(binding, payload)
                elif operation == 'activity':
                    # Typing ping: saves nothing, carries nothing, only refreshes the idle clock.
                    check(payload == {}, 'invalid_input')
                    result = self.server.policy.activity(binding)
                elif operation == 'state':
                    self.application_started = True
                    result = app.state(query[0][1] if query else None)
                elif operation == 'request':
                    self.application_started = True
                    result = app.request_result(key)
                elif operation in APP_POSTS:
                    self.application_started = True
                    result = getattr(app, operation)(payload)
                else:
                    route = self.server.routes.get(operation)
                    check(route is not None, 'operation_unavailable', 503)
                    self.application_started = True
                    result = route.handler(binding, request, payload)
                    if isinstance(payload, BoundedBody):
                        check(payload.remaining == 0, 'incomplete_upload')
                if operation in ('state', 'session'):
                    result = self._capabilities(result)
                if operation in APP_POSTS | {'state', 'request', 'handoff', 'selection'}:
                    try:
                        self.server.policy.after_application(binding, result)
                    except Exception:
                        error = dict(ok=False, code='session_persistence_failed')
                        if operation == 'selection':
                            # Private navigation has no domain write or receipt.
                            # Keep the prior context if the runtime did not persist.
                            app.context.selected_idea_id = previous_selection
                        elif type(result) is dict and result.get('write_state') in ('applied', 'no_op'):
                            error.update(code='session_persistence_uncertain', committed=True,
                                         write_state='committed_uncertain')
                            for name in ('request_id', 'idea_id', 'revision', 'draft_version', 'backlog_revision'):
                                if name in result:
                                    error[name] = result[name]
                        self.send(Response(error, 500))
                        return
                response = result if isinstance(result, Response) else Response(result)
                if kind is not None and operation != 'activity':
                    self._human_activity(binding, response, operation)
                # The larger read allowance is transport-owned after both auth
                # checks and after_application; no DTO/provider can select it.
                state_read = (operation == 'state' and self.command == 'GET' and
                              response.status == 200 and type(response.body) is dict and
                              response.body.get('ok') is True)
                self.send(response, json_limit=MAX_STATE_RESPONSE if state_read else MAX_JSON)
            except IdeaError as exc:
                result = dict(ok=False, code=exc.code)
                for name in ('committed', 'revision', 'draft_version', 'backlog_revision', 'idea_id'):
                    if name in exc.details:
                        result[name] = exc.details[name]
                if kind is not None and operation != 'selection':
                    result['write_state'] = 'committed_uncertain' if exc.details.get('committed') else 'not_applied'
                    if type(payload) is dict and type(payload.get('request_id')) is str and REQUEST_ID.fullmatch(payload['request_id']):
                        result['request_id'] = payload['request_id']
                try:
                    state = app.state()
                    for name in ('revision', 'draft_version', 'backlog_revision'):
                        result.setdefault(name, state[name])
                except (IdeaError, OSError):
                    pass
                self.send(Response(result, 500 if exc.details.get('committed') else error_status(exc.code)))

    def _agent(self, parsed, request, operation, kind):
        """Fixed private transport.

        BridgeServer(agent=callback) calls callback(operation, context, payload).
        context is SessionPolicy's nonsecret AgentBinding. Events payload is
        {session_id,after,timeout}; respond is broker correlation+proposal; close
        is {session_id}. Callback returns a JSON dict or Response and owns broker
        calls. Close MUST call policy.close_agent(context), revoking the agent
        token and notifying waits while preserving paired browser credentials.
        Events runs outside binding/Store locks. Respond/close runs under binding
        lock, so source CAS/publication cannot race resume; those callbacks must
        not wait on network/external work. OwnerService wires this seam later.
        There are eight ordinary slots, four wait slots and twelve total handlers
        including header parsing. No wire value selects a callable.
        """
        check(all(request.header(name) is None for name in
                  ('Origin', 'Cookie', 'X-CSRF-Token', 'X-Idea-Binding', 'X-Idea-Tab')),
              'agent_browser_headers_refused', 403)
        check(self.server.agent is not None, 'agent_unavailable', 503)
        authorize = getattr(self.server.policy, 'authorize_agent', None)
        recheck = getattr(self.server.policy, 'recheck_agent', None)
        check(callable(authorize) and callable(recheck), 'agent_unavailable', 503)
        context = authorize(request)
        binding = getattr(context, 'binding', None)
        check(isinstance(binding, ApplicationBinding), 'invalid_binding', 500)
        # Body reads are outside application/Store locks.
        if operation == 'events':
            self._body(None)
            try:
                pairs = parse_qsl(parsed.query, keep_blank_values=True,
                                  strict_parsing=True, max_num_fields=3)
            except ValueError:
                raise BridgeError('invalid_query') from None
            check(len(pairs) == 3 and {key for key, _ in pairs} ==
                  {'session_id', 'after', 'timeout'}, 'invalid_query')
            values = dict(pairs)
            check(SESSION_ID.fullmatch(values['session_id']) is not None and
                  re.fullmatch(r'[0-9]{1,13}', values['after']) is not None and
                  re.fullmatch(r'(?:0|[1-9][0-9]?)(?:\.[0-9]{1,6})?', values['timeout']) is not None,
                  'invalid_query')
            payload = dict(session_id=values['session_id'], after=int(values['after']),
                           timeout=float(values['timeout']))
            check(payload['after'] <= 10**12 and payload['timeout'] <= 25, 'invalid_query')
        else:
            check('?' not in self.path, 'invalid_query')
            payload = self._body(kind)
            expected = (AGENT_RESPONSE_KEYS if operation == 'respond' else
                        AGENT_RESPONSE_KEYS - {'proposal'} | {'fields'} if operation == 'fill' else {'session_id'})
            check(set(payload) == expected, 'invalid_input')
        check(type(payload.get('session_id')) is str and
              payload['session_id'] == context.session_id, 'wrong_session', 403)
        self.application_payload = payload
        def checked():
            check(recheck(context) is binding, 'agent_unauthorized', 401)
        try:
            with _serialized(binding, self.server.body_deadline):
                checked()
                if operation != 'events':
                    self.application_started = operation == 'respond'
                    result = self.server.agent(operation, context, payload)
                    if operation == 'respond':
                        checked()
            if operation == 'events':
                try:
                    result = self.server.agent(operation, context, payload)
                finally:
                    # Wake or broker error cannot expose a retired incarnation.
                    with _serialized(binding, self.server.body_deadline):
                        checked()
            response = result if isinstance(result, Response) else Response(result)
            if 200 <= response.status < 300 and type(response.body) is dict and response.body.get('ok') is True:
                self.server.touch()
            self.send(response)
        except IdeaError as exc:
            error = dict(ok=False, code=exc.code)
            if operation == 'respond':
                error['write_state'] = 'committed_uncertain' if exc.details.get('committed') else 'not_applied'
                if exc.details.get('committed'):
                    error['committed'] = True
                if type(payload.get('request_id')) is str and REQUEST_ID.fullmatch(payload['request_id']):
                    error['request_id'] = payload['request_id']
            self.send(Response(error, 500 if exc.details.get('committed') else error_status(exc.code)))
