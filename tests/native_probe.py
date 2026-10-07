#!/usr/bin/env python3
"""Disposable CP0 fixture probe, authored by Operator. Never installed as product."""
import argparse
import hashlib
import http.client
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import secrets
import socket
import stat
import tempfile
import threading
import time
import uuid

LIMIT = 1024 * 1024
CORRELATION = {'request_id', 'session_id', 'idea_id', 'accepted_revision',
               'draft_version', 'operation', 'source_digest'}


class Failure(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status


def check(ok, code, status=400):
    if not ok:
        raise Failure(code, status)


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            check(key not in result, 'duplicate_key')
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(Failure('nonfinite')))
        def depth(item, level=0):
            check(level <= 12, 'too_deep')
            if isinstance(item, float):
                check(math.isfinite(item), 'nonfinite')
            if isinstance(item, dict):
                for child in item.values():
                    depth(child, level + 1)
            elif isinstance(item, list):
                for child in item:
                    depth(child, level + 1)
        depth(value)
        check(isinstance(value, dict), 'object_required')
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise Failure('invalid_json')


def fields(value, expected):
    check(set(value) == set(expected), 'unexpected_fields')


def text(value, maximum=65536):
    check(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum
          and not any(0xD800 <= ord(c) <= 0xDFFF for c in value), 'invalid_text')


def private_directory(path):
    check(os.name == 'posix', 'native_acl_not_qualified')
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.lstat()
    check(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
          and stat.S_IMODE(info.st_mode) & 0o777 == 0o700, 'runtime_not_private')


def private_read(path):
    info = path.lstat()
    check(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
          and stat.S_IMODE(info.st_mode) == 0o600, 'file_not_private')
    check(info.st_size <= LIMIT, 'too_large', 413)
    return path.read_bytes()


def write_private(path, value):
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n').encode()
    fd, name = tempfile.mkstemp(prefix='.probe-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Probe:
    def __init__(self, runtime, heartbeat=35, idle=120, clock=time.monotonic):
        self.runtime = Path(runtime)
        private_directory(self.runtime)
        self.clock, self.heartbeat_limit, self.idle_limit = clock, heartbeat, idle
        self.condition = threading.Condition()
        self.fixture_path = self.runtime / 'fixture.json'
        self.fixture = (decode(private_read(self.fixture_path)) if self.fixture_path.exists()
                        else dict(idea_id='idea_' + uuid.uuid4().hex, accepted_revision=0,
                                  draft_version=0, raw_text='', accepted_text=None))
        fields(self.fixture, {'idea_id', 'accepted_revision', 'draft_version', 'raw_text', 'accepted_text'})
        self.session_id = 'session_' + uuid.uuid4().hex
        self.agent_token = secrets.token_urlsafe(32)
        self.code = secrets.token_hex(4).upper()
        self.code_deadline = clock() + 60
        self.pair_attempts, self.redeemed, self.invalidated = 0, False, False
        self.cookie, self.csrf = '', ''
        self.status = 'disconnected'
        self.last_heartbeat = self.last_activity = clock()
        self.pending, self.proposal = None, None
        self.events, self.receipts = [], {}
        self.sequence = 0
        self.origin = ''
        self.observed = {}
        self.publish_credentials()

    def publish_credentials(self):
        write_private(self.runtime / 'agent.json', dict(token=self.agent_token))

    def save(self):
        write_private(self.fixture_path, self.fixture)

    def expire(self):
        if self.status == 'connected':
            if self.clock() - self.last_activity >= self.idle_limit:
                self.status = 'paused'
            elif self.clock() - self.last_heartbeat >= self.heartbeat_limit:
                self.status = 'disconnected'
            if self.status != 'connected':
                self.pending = self.proposal = None
                self.condition.notify_all()

    def result(self, code='ok', **extra):
        return dict(ok=True, code=code, session_id=self.session_id, **self.fixture, **extra)

    def state(self):
        self.expire()
        return self.result(agent_status=self.status, pending_request=self.pending,
                           proposal=self.proposal, csrf=self.csrf,
                           pairing_invalidated=self.invalidated,
                           observed_transport=self.observed,
                           resume_command='python3 tests/native_probe.py resume --runtime <private-runtime>')

    def pair(self, value):
        fields(value, {'code'})
        self.pair_attempts += 1
        if self.redeemed and isinstance(value['code'], str) and value['code'].isascii() and secrets.compare_digest(value['code'], self.code):
            self.invalidated, self.cookie, self.csrf = True, '', ''
            self.status = 'disconnected'
            self.pending = self.proposal = None
            raise Failure('pairing_replay_session_invalidated', 401)
        check(not self.invalidated and not self.redeemed and self.pair_attempts <= 5
              and self.clock() < self.code_deadline, 'pairing_expired_or_locked', 401)
        check(isinstance(value['code'], str) and value['code'].isascii() and secrets.compare_digest(value['code'], self.code),
              'wrong_pairing_code', 401)
        self.redeemed = True
        self.cookie, self.csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        return self.result(csrf=self.csrf)

    def browser(self, operation, value):
        self.expire()
        if operation == 'transport':
            fields(value, {'host', 'origin', 'secure_context'})
            check(value['host'] == self.origin.removeprefix('http://')
                  and value['origin'] == self.origin and type(value['secure_context']) is bool,
                  'transport_mismatch', 403)
            self.observed = value
            return self.result()
        expected = {'request_id', 'expected_revision', 'expected_draft_version'}
        fields(value, expected | ({'raw_text'} if operation == 'capture' else
                                 {'proposal_id'} if operation == 'accept' else set()))
        text(value['request_id'], 128)
        key = value['request_id']
        fingerprint = json.dumps([operation, value], sort_keys=True)
        if key in self.receipts:
            old, result = self.receipts[key]
            check(old == fingerprint, 'request_id_conflict', 409)
            return result
        check(len(self.receipts) < 128, 'receipt_capacity', 503)
        check(type(value['expected_revision']) is int and type(value['expected_draft_version']) is int,
              'invalid_revision')
        check(value['expected_revision'] == self.fixture['accepted_revision'] and
              value['expected_draft_version'] == self.fixture['draft_version'], 'stale_revision', 409)
        if operation == 'capture':
            text(value['raw_text'])
            self.fixture['raw_text'] = value['raw_text']
            self.fixture['draft_version'] += 1
            if self.fixture['accepted_revision'] == 0:
                self.fixture['accepted_revision'] = 1
            self.pending = self.proposal = None
            self.save()
        elif operation == 'propose':
            check(self.status == 'connected', 'agent_unavailable', 503)
            check(self.fixture['raw_text'], 'capture_required')
            check(self.pending is None, 'request_busy', 503)
            check(len(self.events) < 128, 'event_capacity', 503)
            self.sequence += 1
            self.pending = dict(request_id=key, session_id=self.session_id,
                                idea_id=self.fixture['idea_id'],
                                accepted_revision=self.fixture['accepted_revision'],
                                draft_version=self.fixture['draft_version'], operation='discovery',
                                source_digest=hashlib.sha256(self.fixture['raw_text'].encode()).hexdigest())
            self.events.append(dict(self.pending, sequence=self.sequence,
                                    data={'raw_text': self.fixture['raw_text']}))
            self.proposal = None
            self.condition.notify_all()
        elif operation == 'accept':
            check(self.proposal is not None and value['proposal_id'] == self.proposal['proposal_id'],
                  'proposal_unavailable', 409)
            self.fixture['accepted_text'] = self.proposal['text']
            self.fixture['accepted_revision'] += 1
            self.proposal = None
            self.save()
        else:
            raise Failure('unknown_route', 404)
        self.last_activity = self.clock()
        result = self.result(request_id=key, write_state='applied')
        self.receipts[key] = (fingerprint, result)
        return result

    def agent(self, operation, value):
        self.expire()
        if operation == 'pairing-code':
            fields(value, set())
            check(not self.redeemed and not self.invalidated and self.clock() < self.code_deadline
                  and self.pair_attempts < 5, 'pairing_expired_or_locked', 401)
            return dict(ok=True, code='ok', pairing_code=self.code, expires_in=int(self.code_deadline-self.clock()))
        if operation in ('resume', 'session-open'):
            fields(value, set())
            self.session_id = 'session_' + uuid.uuid4().hex
            self.agent_token = secrets.token_urlsafe(32)
            self.publish_credentials()
            self.pending = self.proposal = None
            self.status = 'connected'
            self.last_heartbeat = self.last_activity = self.clock()
            if self.invalidated or (not self.redeemed and self.clock() >= self.code_deadline):
                self.code = secrets.token_hex(4).upper()
                self.code_deadline = self.clock() + 60
                self.pair_attempts, self.redeemed, self.invalidated = 0, False, False
            self.condition.notify_all()
            return self.result(agent_status=self.status)
        if operation == 'session-close':
            fields(value, {'session_id'})
            check(value['session_id'] == self.session_id, 'wrong_session', 409)
            self.status = 'disconnected'
            self.pending = self.proposal = None
            self.condition.notify_all()
            return self.result(agent_status=self.status)
        if operation == 'status':
            fields(value, set())
            return self.result(agent_status=self.status, sequence=self.sequence,
                               observed_transport=self.observed)
        if operation == 'events':
            fields(value, {'session_id', 'after', 'timeout'})
            check(value['session_id'] == self.session_id, 'wrong_session', 409)
            check(type(value['after']) is int and value['after'] >= 0
                  and type(value['timeout']) in (int, float) and 0 <= value['timeout'] <= 25, 'invalid_wait')
            check(self.status == 'connected', 'agent_unavailable', 503)
            self.last_heartbeat = self.clock()
            deadline = self.clock() + value['timeout']
            while True:
                # wait releases the lock; an old caller cannot borrow
                # a resumed session's authorization or renew its heartbeat.
                check(value['session_id'] == self.session_id, 'wrong_session', 409)
                self.expire()
                check(self.status != 'disconnected', 'agent_unavailable', 503)
                events = [e for e in self.events if e['sequence'] > value['after']
                          and e['session_id'] == self.session_id and self.pending is not None
                          and e['request_id'] == self.pending['request_id']]
                remaining = deadline - self.clock()
                if events or remaining <= 0 or self.status != 'connected':
                    if self.status == 'connected':
                        self.last_heartbeat = self.clock()
                    return self.result(events=events, sequence=self.sequence, agent_status=self.status)
                self.condition.wait(min(remaining, 1))
        if operation == 'respond':
            fields(value, CORRELATION | {'text'})
            check(self.status == 'connected', 'agent_unavailable', 503)
            check(self.pending is not None and all(value[k] == self.pending[k] and
                  type(value[k]) is type(self.pending[k]) for k in CORRELATION), 'stale_response', 409)
            text(value['text'])
            self.proposal = dict(proposal_id='proposal_' + uuid.uuid4().hex, text=value['text'],
                                 source='current-agent', request_id=value['request_id'])
            self.pending = None
            self.last_heartbeat = self.last_activity = self.clock()
            return self.result(proposal=self.proposal)
        raise Failure('unknown_route', 404)


PAGE = b'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>CP0 fixture probe</title><script src="/probe.js" defer></script><h1>CP0 fixture probe</h1><p>Test data only. Agent replies are proposals. Human acceptance is separate.</p><form id="pair"><label>Pairing code <input id="code" autocomplete="off" required></label><button>Pair browser</button></form><form id="capture"><label>Fake idea <textarea id="raw" required></textarea></label><button>Save fixture draft</button></form><button id="propose">Request current-agent proposal</button><h2>Proposal</h2><p id="proposal">No proposal</p><button id="accept" disabled>Accept visible proposal</button><h2>Saved state</h2><pre id="saved"></pre><p id="status" role="status" aria-live="polite"></p><p id="resume"></p></html>'''
SCRIPT = b'''"use strict";
let state = null, csrf = "", busy = false, dirty = false, lastError = "";
const el = id => document.getElementById(id);
async function call(route, body) {
  const options = {credentials: "same-origin", cache: "no-store"};
  if(body !== undefined) Object.assign(options, {method: "POST", headers: {"Content-Type":"application/json", "X-CSRF-Token":csrf}, body:JSON.stringify(body)});
  const r = await fetch("/api/v1/" + route, options), result = await r.json();
  if(!r.ok) throw Error(result.code);
  return result;
}
async function refresh() {
  try {
    state = await call("state"); csrf = state.csrf;
    el("pair").hidden = true;
    if(document.activeElement !== el("raw") && !busy && !dirty) el("raw").value = state.raw_text;
    el("proposal").textContent = state.proposal ? state.proposal.text : "No proposal";
    el("accept").disabled = !state.proposal;
    el("saved").textContent = JSON.stringify({idea_id:state.idea_id, accepted_revision:state.accepted_revision, draft_version:state.draft_version, raw_text:state.raw_text, accepted_text:state.accepted_text}, null, 2);
    el("status").textContent = "Agent: " + state.agent_status + (state.pending_request ? "; request pending" : "") + (lastError ? "; " + lastError : "");
    el("resume").textContent = state.agent_status === "connected" ? "" : "Draft saved. Resume explicitly in initiating pane: " + state.resume_command;
  } catch(e) { el("status").textContent = e.message + ": re-pair browser; replay invalidates session."; el("pair").hidden = false; }
}
function envelope() { return {request_id:crypto.randomUUID(), expected_revision:state.accepted_revision, expected_draft_version:state.draft_version}; }
async function mutate(route, extra={}) { if(!state) return; busy = true; try { await call(route, Object.assign(envelope(), extra)); if(route === "capture") dirty=false; lastError=""; await refresh(); } catch(e) { lastError=e.message; el("status").textContent=e.message; } finally {busy=false;} }
el("pair").onsubmit = async e => {e.preventDefault(); try {const r=await call("pair", {code:el("code").value}); csrf=r.csrf; el("code").value=""; await call("transport", {host:location.host, origin:location.origin, secure_context:window.isSecureContext}); await refresh();} catch(err) {el("status").textContent=err.message;} };
el("raw").oninput = () => {dirty=true;};
el("capture").onsubmit = e => {e.preventDefault(); mutate("capture", {raw_text:el("raw").value});};
el("propose").onclick = () => mutate("propose");
el("accept").onclick = () => mutate("accept", {proposal_id:state.proposal.proposal_id});
refresh(); setInterval(() => {if(!busy) refresh();}, 1500);
'''


class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, probe):
        self.probe = probe
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(('127.0.0.1', 0), Handler)
        self.probe.origin = 'http://127.0.0.1:' + str(self.server_port)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def send(self, status, value, kind='application/json', cookie=None):
        raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        if cookie:
            self.send_header('Set-Cookie', 'probe=' + cookie + '; HttpOnly; SameSite=Strict; Path=/')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def dispatch(self, post):
        p = self.server.probe
        try:
            check(self.headers.get('Host') == p.origin.removeprefix('http://'), 'wrong_host', 403)
            origin = self.headers.get('Origin')
            check(origin is None or origin == p.origin, 'wrong_origin', 403)
            if not post and self.path in ('/', '/probe.js'):
                self.send(200, PAGE if self.path == '/' else SCRIPT,
                          'text/html; charset=utf-8' if self.path == '/' else 'text/javascript; charset=utf-8')
                return
            value = {}
            if post:
                check(self.headers.get('Content-Type') == 'application/json', 'json_required')
                check(self.headers.get('Transfer-Encoding') is None, 'transfer_encoding_refused')
                length = self.headers.get('Content-Length', '')
                check(length.isdigit(), 'length_required')
                check(0 < int(length) <= LIMIT, 'too_large', 413)
                raw = self.rfile.read(int(length))
                check(len(raw) == int(length), 'incomplete_body')
                value = decode(raw)
            with p.condition:
                p.expire()
                if self.path.startswith('/agent/'):
                    check(post, 'method_refused', 405)
                    check(self.headers.get('Authorization') == 'Bearer ' + p.agent_token, 'agent_unauthorized', 401)
                    result = p.agent(self.path.removeprefix('/agent/'), value)
                    self.send(200, result)
                    return
                if self.path == '/api/v1/pair':
                    check(post and origin == p.origin, 'origin_required', 403)
                    result = p.pair(value)
                    self.send(200, result, cookie=p.cookie)
                    return
                cookie = SimpleCookie()
                cookie.load(self.headers.get('Cookie', ''))
                check(bool(p.cookie) and cookie.get('probe') is not None
                      and secrets.compare_digest(cookie['probe'].value, p.cookie), 'browser_unauthorized', 401)
                if self.path == '/api/v1/state' and not post:
                    self.send(200, p.state())
                    return
                check(post and origin == p.origin, 'origin_required', 403)
                check(self.headers.get('X-CSRF-Token') == p.csrf and bool(p.csrf), 'wrong_csrf', 403)
                check(self.path.startswith('/api/v1/'), 'unknown_route', 404)
                self.send(200, p.browser(self.path.removeprefix('/api/v1/'), value))
        except Failure as exc:
            self.send(exc.status, dict(ok=False, code=exc.code))
        except (OSError, ValueError, TypeError, KeyError):
            self.send(500, dict(ok=False, code='probe_internal_error'))


def client(runtime, operation, value):
    runtime = Path(runtime)
    private_directory(runtime)
    info = decode(private_read(runtime / 'discovery.json'))
    fields(info, {'host', 'port', 'url'})
    check(info['host'] == '127.0.0.1' and type(info['port']) is int
          and 1 <= info['port'] <= 65535, 'invalid_discovery')
    token = decode(private_read(runtime / 'agent.json'))['token']
    connection = http.client.HTTPConnection(info['host'], info['port'], timeout=30)
    try:
        connection.request('POST', '/agent/' + operation, json.dumps(value),
                           {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        return response.status, decode(response.read(LIMIT + 1))
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('verb', choices=['serve', 'pairing-code', 'session-open', 'resume',
                                       'events', 'respond', 'status', 'session-close'])
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--session')
    parser.add_argument('--after', type=int, default=0)
    parser.add_argument('--timeout', type=float, default=25)
    parser.add_argument('--request', help='Optional request ID consistency check against reply file')
    parser.add_argument('--payload', help='JSON reply file; no secret or reply text in argv')
    args = parser.parse_args()
    try:
        if args.verb == 'serve':
            probe = Probe(args.runtime)
            server = Server(probe)
            write_private(probe.runtime / 'discovery.json', dict(host='127.0.0.1', port=server.server_port, url=probe.origin))
            print(json.dumps(dict(ok=True, url=probe.origin, session_id=probe.session_id)), flush=True)
            try:
                server.serve_forever(poll_interval=0.25)
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return
        value = {}
        if args.verb in ('events', 'session-close'):
            check(args.session is not None, 'session_required')
            value = dict(session_id=args.session)
        if args.verb == 'events':
            value.update(after=args.after, timeout=args.timeout)
        if args.verb == 'respond':
            check(args.payload is not None, 'payload_required')
            raw = Path(args.payload).read_bytes()
            check(len(raw) <= LIMIT, 'too_large', 413)
            value = decode(raw)
            if args.request is not None:
                check(value.get('request_id') == args.request, 'wrong_request')
        status, result = client(args.runtime, args.verb, value)
        print(json.dumps(result, ensure_ascii=False))
        if status >= 400:
            raise SystemExit(1)
    except (Failure, OSError, ValueError, KeyError) as exc:
        print(json.dumps(dict(ok=False, code=exc.code if isinstance(exc, Failure) else 'probe_unavailable')))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
