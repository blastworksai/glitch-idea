"""Sealed prototype server: a separate loopback origin for one idea's throwaway prototype files.

It is never the app's port or origin. It serves only regular files under one owner-private
folder inside the runtime root's prototypes/ directory, answers GET/HEAD only, sets no
cookie, reads no cookie, and stops when its session ends or after a hard maximum lifetime.
"""
import errno
import json
import mimetypes
import os
from pathlib import Path
import re
import signal
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from idea_domain import IdeaError

CSP = "sandbox allow-scripts; default-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
SESSION = re.compile(r'session_[0-9a-f]{32}')
ENCODED_DANGER = re.compile(r'%(2e|2f|5c|00)', re.IGNORECASE)
MAX_LIFETIME = 4 * 3600
POLL_SECONDS = 3.0
CHUNK = 64 * 1024


def _fail(code, message=None):
    return IdeaError(code, message or code)


def validate_directory(directory, runtime_root):
    """Return the canonical folder; it must be an owner-private, symlink-free child of <root>/prototypes."""
    if type(directory) is not str or not directory or '\x00' in directory or len(directory) > 4096:
        raise _fail('unsafe_prototype_dir')
    path = Path(directory)
    if not path.is_absolute():
        raise _fail('unsafe_prototype_dir')
    parent = Path(os.path.abspath(Path(runtime_root) / 'prototypes'))
    path = Path(os.path.abspath(path))
    if parent not in path.parents:
        raise _fail('unsafe_prototype_dir')
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            info = os.lstat(current)
        except OSError:
            raise _fail('unsafe_prototype_dir') from None
        if stat.S_ISLNK(info.st_mode):
            raise _fail('unsafe_prototype_dir')
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise _fail('unsafe_prototype_dir')
    if os.path.realpath(path) != str(path):
        raise _fail('unsafe_prototype_dir')
    return path


def resolve_target(root, raw_path):
    """Map a request target to (file_path, None) or (None, status). Never touches anything outside root."""
    target = raw_path.split('?', 1)[0].split('#', 1)[0]
    if not target.startswith('/') or target.startswith('//') or '\\' in target or ENCODED_DANGER.search(target):
        return None, 403
    decoded = unquote(target)
    if '\x00' in decoded or '\\' in decoded:
        return None, 403
    parts = [p for p in decoded.split('/') if p != '']
    if any(p in ('.', '..') for p in parts):
        return None, 403
    current = str(root)
    for part in parts:
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except OSError:
            return None, 404
        if stat.S_ISLNK(info.st_mode):
            return None, 403
    try:
        info = os.lstat(current)
    except OSError:
        return None, 404
    if stat.S_ISDIR(info.st_mode):
        current = os.path.join(current, 'index.html')
        try:
            info = os.lstat(current)
        except OSError:
            return None, 404
        if stat.S_ISLNK(info.st_mode):
            return None, 403
    if not stat.S_ISREG(info.st_mode):
        return None, 404
    real = os.path.realpath(current)
    if os.path.commonpath([real, str(root)]) != str(root):
        return None, 403
    return current, None


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'idea-prototype'
    sys_version = ''

    def log_message(self, *args):
        pass

    def _headers(self, status, length, ctype='text/plain; charset=utf-8'):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(length))
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        if status == 405:
            self.send_header('Allow', 'GET, HEAD')
        self.end_headers()

    def _refuse(self, status, head=False):
        body = {400: b'bad request', 403: b'refused', 404: b'not found', 405: b'method not allowed',
                421: b'misdirected'}.get(status, b'error')
        self.close_connection = True
        self._headers(status, len(body))
        if not head:
            self.wfile.write(body)

    def _serve(self, head):
        port = self.server.server_address[1]
        if self.headers.get('Host') not in ('127.0.0.1:%d' % port, 'localhost:%d' % port):
            return self._refuse(421, head)
        path, status = resolve_target(self.server.root, self.path)
        if status is not None:
            return self._refuse(status, head)
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NONBLOCK', 0)
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            return self._refuse(403 if exc.errno in (errno.ELOOP, errno.EACCES) else 404, head)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                return self._refuse(404, head)
            guessed = mimetypes.guess_type(path)[0] or 'application/octet-stream'
            if guessed.startswith('text/') or guessed in ('application/javascript', 'application/json'):
                guessed += '; charset=utf-8'
            self._headers(200, info.st_size, guessed)
            if head:
                return
            remaining = info.st_size
            while remaining > 0:
                chunk = os.read(fd, min(CHUNK, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        finally:
            os.close(fd)

    def do_GET(self):
        self._serve(False)

    def do_HEAD(self):
        self._serve(True)

    def _method_not_allowed(self):
        self._refuse(405)

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _method_not_allowed


class PrototypeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root):
        self.root = str(root)
        super().__init__(('127.0.0.1', 0), Handler)


def supervise(server, alive, *, poll=POLL_SECONDS, max_lifetime=MAX_LIFETIME, clock=time.monotonic, stop=None):
    """Poll liveness until it fails, the lifetime ends or stop is set; then shut the server down."""
    stop = stop or threading.Event()
    deadline = clock() + max_lifetime
    try:
        while not stop.wait(poll):
            if clock() >= deadline:
                break
            try:
                if not alive():
                    break
            except Exception:
                break
    finally:
        server.shutdown()


def make_liveness(store_path, runtime_root, session_id):
    """Live while the owner service answers a probe and still holds a binding for this session."""
    from idea_runtime import Runtime, RuntimeError as OwnerError

    def alive():
        try:
            runtime = Runtime(store_path, runtime_root)
            runtime.probe_owner(timeout=1)
            return any(r['receipt_session_id'] == session_id for r in runtime.list_bindings())
        except OwnerError as exc:
            return exc.code in ('busy', 'runtime_busy')
    return alive


def serve_prototype(session_id, directory, store_path, runtime_root, *, alive=None, out=None,
                    poll=POLL_SECONDS, max_lifetime=MAX_LIFETIME, host='<host>'):
    if type(session_id) is not str or not SESSION.fullmatch(session_id):
        raise _fail('invalid_session')
    root = validate_directory(directory, runtime_root)
    alive = alive or make_liveness(store_path, runtime_root, session_id)
    try:
        live = alive()
    except Exception:
        live = False
    if not live:
        raise _fail('invalid_session')
    server = PrototypeServer(root)
    port = server.server_address[1]
    out = out or sys.stdout
    stop = threading.Event()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
    worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
    worker.start()
    out.write(json.dumps(dict(ok=True, origin='http://127.0.0.1:%d/' % port, port=port,
                              ssh_line='ssh -L %d:127.0.0.1:%d %s' % (port, port, host)),
                         separators=(',', ':')) + '\n')
    out.flush()
    try:
        supervise(server, alive, poll=poll, max_lifetime=max_lifetime, stop=stop)
    finally:
        worker.join(5)
        server.server_close()
    return dict(ok=True, code='stopped')
