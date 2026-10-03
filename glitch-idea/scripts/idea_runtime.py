"""Owner-private runtime and process-control boundary.

This is separate from Markdown authority and browser/agent authentication.
Only a verified loopback instance may be probed/stopped; no PID signals, shell
commands, launcher or auth imports exist here. POSIX descriptor privacy is
implemented; Windows fails closed pending native owner-ACL qualification.
"""
from contextlib import contextmanager
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import threading
import time
import weakref

if os.name == 'posix':
    import fcntl
else:
    fcntl = None

_POSIX = os.name == 'posix'
MAX_RECORD = 16 * 1024
MAX_BINDINGS = 8
MAX_AGGREGATE = 192 * 1024
SERVICE = 'glitch-idea'
HEX = re.compile(r'[0-9a-f]{64}')
BINDING = re.compile(r'binding_[0-9a-f]{32}')
SESSION = re.compile(r'session_[0-9a-f]{32}')
IDEA = re.compile(r'idea_[0-9a-f]{32}')
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}')
AGENT = re.compile(r'agent_[0-9a-f]{32}')
STAGE = re.compile(r'\.runtime_(credentials|discovery|binding_[0-9a-f]{32})_([0-9a-f]{64})_[0-9a-f]{32}\.tmp')
_instances = weakref.WeakSet()


# A client scan that meets the owner's in-flight staged write waits this long for it to settle.
STAGE_SETTLE_TRIES = 10
STAGE_SETTLE_SECONDS = 0.05


# The owner's own fixed refusal codes a client may report as they are. Another
# well-formed refusal is reported as owner_refused; a refusal of the owner credential
# itself, or any malformed reply, stays an identity failure.
OWNER_REFUSALS = frozenset(('busy', 'agent_unavailable', 'binding_not_found', 'not_found',
                            'session_binding_mismatch', 'session_capacity_exhausted',
                            'binding_membership_conflict', 'invalid_control', 'unknown_control',
                            'control_unavailable', 'internal_error', 'runtime_busy', 'runtime_corrupt'))
IDENTITY_REFUSALS = frozenset(('owner_unauthorized', 'owner_identity_mismatch'))


class RuntimeError(Exception):
    """Stable redacted failure; committed means a private replace may be visible."""
    def __init__(self, code, *, committed=False):
        super().__init__(code)
        self.code, self.committed = code, committed


def _check(value, code='runtime_corrupt'):
    if not value:
        raise RuntimeError(code)


def _platform():
    _check(_POSIX and fcntl is not None and hasattr(os, 'O_NOFOLLOW')
           and hasattr(os, 'O_DIRECTORY') and os.open in os.supports_dir_fd
           and os.rename in os.supports_dir_fd, 'privacy_unqualified')


def _absolute(value):
    try:
        path = Path(value)
        _check(path.is_absolute() and '..' not in path.parts
               and '\x00' not in str(path), 'runtime_not_private')
        return path
    except (TypeError, ValueError):
        raise RuntimeError('runtime_not_private') from None


def _directory(fd, *, private):
    info = os.fstat(fd)
    _check(stat.S_ISDIR(info.st_mode), 'runtime_not_private')
    if private:
        _check(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) & 0o777 == 0o700,
               'runtime_not_private')
    else:
        sticky_system = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        _check(info.st_uid in (0, os.getuid())
               and (not info.st_mode & 0o022 or sticky_system), 'runtime_not_private')
    return info


def _open_chain(path, *, create=False, private_leaf=False, secure_ancestry=True):
    """Inspect each component before following; descriptors anchor every step."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open('/', flags)
    try:
        if secure_ancestry:
            _directory(descriptor, private=False)
        for index, name in enumerate(path.parts[1:]):
            leaf = index == len(path.parts) - 2
            try:
                child = os.open(name, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(name, 0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
                child = os.open(name, flags, dir_fd=descriptor)
            if secure_ancestry:
                try:
                    _directory(child, private=private_leaf and leaf)
                except Exception:
                    os.close(child)
                    raise
            os.close(descriptor); descriptor = child
        if private_leaf:
            _directory(descriptor, private=True)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _canonical_store(value):
    path = _absolute(value)
    # Store may not exist yet; inspect every existing ancestor without creating it.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open('/', flags)
    try:
        for name in path.parts[1:]:
            try:
                child = os.open(name, flags, dir_fd=descriptor)
            except FileNotFoundError:
                break
            os.close(descriptor); descriptor = child
    except OSError:
        raise RuntimeError('runtime_not_private') from None
    finally:
        os.close(descriptor)
    try:
        raw = str(path).encode('utf-8')
    except UnicodeError:
        raise RuntimeError('runtime_not_private') from None
    return path, hashlib.sha256(raw).hexdigest()


def _private_file(fd):
    info = os.fstat(fd)
    _check(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
           and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
           'runtime_not_private')
    _check(info.st_size <= MAX_RECORD)
    return info


def _raw(directory, name):
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                             dir_fd=directory)
    except FileNotFoundError:
        raise
    except OSError:
        raise RuntimeError('runtime_not_private') from None
    try:
        _private_file(descriptor)
        parts, size = [], 0
        while True:
            chunk = os.read(descriptor, min(4096, MAX_RECORD + 1 - size))
            if not chunk:
                break
            parts.append(chunk); size += len(chunk)
            _check(size <= MAX_RECORD)
        return b''.join(parts)
    finally:
        os.close(descriptor)


def _json(raw):
    _check(len(raw) <= MAX_RECORD)
    def pairs(items):
        value = {}
        for key, child in items:
            _check(key not in value)
            value[key] = child
        return value
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(RuntimeError('runtime_corrupt')))
        _check(type(value) is dict)
        pending, count = [(value, 1)], 0
        while pending:
            item, depth = pending.pop(); count += 1
            _check(depth <= 12 and count <= 1024)
            if type(item) is dict:
                for key, child in item.items(): pending.extend(((key, depth + 1), (child, depth + 1)))
            elif type(item) is list:
                pending.extend((child, depth + 1) for child in item)
            elif type(item) is float:
                _check(math.isfinite(item))
            elif type(item) is str:
                _check(not any(0xD800 <= ord(char) <= 0xDFFF for char in item))
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise RuntimeError('runtime_corrupt') from None


def _exact(value, names):
    _check(type(value) is dict and set(value) == set(names))
    _check(type(value.get('schema_version')) is int and value['schema_version'] == 1)


def _binding(value):
    _exact(value, ('schema_version', 'binding_id', 'actor', 'receipt_session_id', 'selected_idea_id'))
    _check(type(value['binding_id']) is str and BINDING.fullmatch(value['binding_id']) is not None)
    _check(type(value['receipt_session_id']) is str and SESSION.fullmatch(value['receipt_session_id']) is not None)
    _check(type(value['actor']) is str and 0 < len(value['actor']) <= 200 and bool(value['actor'].strip())
           and not any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value['actor']))
    idea_id = value['selected_idea_id']
    _check(idea_id is None or (type(idea_id) is str and IDEA.fullmatch(idea_id) is not None))
    return copy.deepcopy(value)


def _identity(value, store_sha256, *, credential):
    keys = ('schema_version', 'service', 'store_sha256', 'instance_nonce')
    _exact(value, keys + (('owner_token',) if credential else ('pid', 'host', 'port')))
    _check(value['service'] == SERVICE and value['store_sha256'] == store_sha256
           and type(value['instance_nonce']) is str and HEX.fullmatch(value['instance_nonce']) is not None)
    if credential:
        _check(type(value['owner_token']) is str and TOKEN.fullmatch(value['owner_token']) is not None)
    else:
        _check(value['host'] == '127.0.0.1' and type(value['port']) is int and 1 <= value['port'] <= 65535
               and type(value['pid']) is int and 0 < value['pid'] <= 2**31 - 1)
    return value


def _after_fork():
    # Close inherited copies without LOCK_UN (the parent's open description owns it).
    for instance in list(_instances):
        for name in ('_lock_fd', '_directory_fd', '_bindings_fd'):
            descriptor = getattr(instance, name, None)
            if descriptor is not None:
                os.close(descriptor)
                setattr(instance, name, None)
        instance._owner_pid = None
        instance._mutex = threading.RLock()
        instance._credential = None


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_after_fork)


class _AgentChannel:
    """Transient native-only capability; no credential in repr or public data."""
    __slots__ = ('_port', '_binding_id', '_session_id', '_generation', '__token')

    def __init__(self, port, value):
        self._port = port
        self._binding_id = value['binding_id']
        self._session_id = value['session_id']
        self._generation = value['generation']
        self.__token = value['token']

    def __repr__(self):
        return '<private agent channel>'

    def _headers(self):
        return {'Authorization': 'Bearer ' + self.__token,
                'X-Idea-Agent-Binding': self._binding_id,
                'X-Idea-Agent-Generation': self._generation}

    def _contains_token(self, raw):
        return self.__token.encode('ascii') in raw


class Runtime:
    def __init__(self, store_path, runtime_root, *, clock=time.monotonic):
        _platform()
        self.store_path, self.store_sha256 = _canonical_store(store_path)
        self.runtime_root = _absolute(runtime_root)
        self.path = self.runtime_root / 'stores' / self.store_sha256
        self.clock = clock
        self._mutex = threading.RLock()
        self._lock_fd = self._directory_fd = self._bindings_fd = None
        self._owner_pid = None
        self._credential = None
        self._owned_files = {}
        self._closing = False
        self._active = 0
        self._last_activity = clock()
        _instances.add(self)

    def __enter__(self):
        self.acquire_owner()
        return self

    def __exit__(self, *_):
        self.close()

    def _owner(self):
        _check(self._lock_fd is not None and self._owner_pid == os.getpid()
               and not self._closing, 'owner_unavailable')

    @contextmanager
    def _directories(self, *, create=False):
        descriptors = []
        try:
            root = _open_chain(self.runtime_root, create=create, private_leaf=True)
            descriptors.append(root)
            for name in ('stores', self.store_sha256, 'bindings'):
                parent = descriptors[-1]
                try:
                    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                         dir_fd=parent)
                except FileNotFoundError:
                    if not create: raise
                    try:
                        os.mkdir(name, 0o700, dir_fd=parent); os.fsync(parent)
                    except FileExistsError:
                        pass
                    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                         dir_fd=parent)
                descriptors.append(descriptor)
                _directory(descriptor, private=True)
            yield descriptors[-2], descriptors[-1]
        except FileNotFoundError:
            raise RuntimeError('owner_unavailable') from None
        except OSError:
            raise RuntimeError('runtime_not_private') from None
        finally:
            for descriptor in reversed(descriptors): os.close(descriptor)

    def _scan(self, directory, bindings):
        total = 0
        names = os.listdir(directory)
        self._live_stage(directory, names, binding_directory=False)
        _check(set(names) <= {'service.lock', 'discovery.json', 'credentials.json', 'bindings'})
        for name in names:
            if name == 'bindings': continue
            raw = _raw(directory, name); total += len(raw)
            if name == 'service.lock': _check(raw == b'')
            else: _identity(_json(raw), self.store_sha256, credential=name == 'credentials.json')
        records = {}
        names = os.listdir(bindings)
        self._live_stage(bindings, names, binding_directory=True)
        _check(len(names) <= MAX_BINDINGS)
        for name in names:
            _check(re.fullmatch(r'binding_[0-9a-f]{32}\.json', name) is not None)
            raw = _raw(bindings, name); total += len(raw)
            record = _binding(_json(raw))
            _check(record['binding_id'] + '.json' == name)
            records[record['binding_id']] = record
        _check(total <= MAX_AGGREGATE)
        return records, total

    def _settled_scan(self, directory, bindings, deadline=None):
        """Client-side scan that lets the owner's in-flight atomic write finish.

        The service stages a binding write and renames it into place within
        milliseconds; a client scan that lands in that window used to refuse at
        once with runtime_busy, failing an agent's session-close or resume while a
        browser save was in flight. Re-scan briefly; a stage that persists past the
        bounded settle window still refuses runtime_busy, and a live stage is
        still never parsed or cleaned. A name that vanishes between listdir and
        open is that same rename completing, so it re-scans too. The owner never
        settles: its own writes happen under its mutex, so a stage it sees is not
        its own and waiting would only stall the service. No sleep passes the
        caller's deadline, and a settle that leaves the caller less than one settle
        interval refuses runtime_busy (retry, the owner is live) rather than letting
        the call fail as owner_unavailable (which would spawn a second service).
        """
        if self._lock_fd is not None:
            return self._scan(directory, bindings)
        for attempt in range(STAGE_SETTLE_TRIES):
            try:
                result = self._scan(directory, bindings)
            except RuntimeError as exc:
                if exc.code != 'runtime_busy' or attempt == STAGE_SETTLE_TRIES - 1:
                    raise
            except FileNotFoundError:
                # A name renamed away under us is the owner's write: busy, never absent.
                if attempt == STAGE_SETTLE_TRIES - 1:
                    raise RuntimeError('runtime_busy') from None
            else:
                if attempt and deadline is not None and time.monotonic() + STAGE_SETTLE_SECONDS >= deadline:
                    raise RuntimeError('runtime_busy')
                return result
            if deadline is not None and time.monotonic() + STAGE_SETTLE_SECONDS >= deadline:
                raise RuntimeError('runtime_busy')
            time.sleep(STAGE_SETTLE_SECONDS)

    def _live_stage(self, directory, names, *, binding_directory):
        for name in names:
            match = STAGE.fullmatch(name)
            if match is not None:
                _check(binding_directory == match[1].startswith('binding_'))
                _raw(directory, name)  # Validate descriptor privacy/size, never parse or clean a live stage.
                raise RuntimeError('runtime_busy')

    def _recover_stages(self, directory, bindings):
        # Only a complete checksum + schema proof qualifies a reserved owned stage.
        # Incomplete/unknown bytes remain for explicit recovery, never guessed away.
        for parent, binding_directory in ((directory, False), (bindings, True)):
            complete = []
            for name in os.listdir(parent):
                if not name.startswith('.runtime_'):
                    continue
                match = STAGE.fullmatch(name)
                _check(match is not None, 'runtime_recovery_conflict')
                target, expected = match.groups()
                _check(binding_directory == target.startswith('binding_'), 'runtime_recovery_conflict')
                raw = _raw(parent, name)
                _check(hashlib.sha256(raw).hexdigest() == expected, 'runtime_recovery_conflict')
                try:
                    value = _json(raw)
                    if binding_directory:
                        _check(_binding(value)['binding_id'] == target)
                    else:
                        _identity(value, self.store_sha256, credential=target == 'credentials')
                except RuntimeError:
                    raise RuntimeError('runtime_recovery_conflict') from None
                complete.append(name)
            for name in complete:
                os.unlink(name, dir_fd=parent)
            if complete:
                os.fsync(parent)

    def _release_descriptors(self):
        for name in ('_bindings_fd', '_directory_fd', '_lock_fd'):
            descriptor = getattr(self, name)
            if descriptor is not None: os.close(descriptor)
            setattr(self, name, None)
        self._owner_pid = None; self._credential = None; self._owned_files.clear()

    def _write(self, directory, name, value):
        raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
        _check(len(raw) <= MAX_RECORD)
        # Validate existing target rather than replacing an outsider/symlink/link.
        try: _raw(directory, name)
        except FileNotFoundError: pass
        temporary = '.runtime_' + name.removesuffix('.json') + '_' + hashlib.sha256(raw).hexdigest() + '_' + secrets.token_hex(16) + '.tmp'
        descriptor = None
        replaced = False
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 0o600, dir_fd=directory)
            _private_file(descriptor)
            offset = 0
            while offset < len(raw):
                written = os.write(descriptor, raw[offset:]); _check(written > 0)
                offset += written
            os.fsync(descriptor)
            os.close(descriptor); descriptor = None
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            replaced = True
            if name in ('credentials.json', 'discovery.json'):
                self._owned_files[name] = hashlib.sha256(raw).hexdigest()
            os.fsync(directory)
            return raw
        except OSError:
            raise RuntimeError('runtime_persistence_uncertain' if replaced else 'runtime_persistence_failed',
                               committed=replaced) from None
        finally:
            if descriptor is not None: os.close(descriptor)
            if not replaced:
                try: os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError: pass

    def acquire_owner(self):
        with self._mutex:
            _check(self._lock_fd is None, 'runtime_busy')
            with self._directories(create=True) as (directory, bindings):
                descriptor = None
                try:
                    try:
                        descriptor = os.open('service.lock', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                             0o600, dir_fd=directory)
                        os.fsync(descriptor); os.fsync(directory)
                    except FileExistsError:
                        descriptor = os.open('service.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                                             dir_fd=directory)
                    _private_file(descriptor)
                    try: fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError: raise RuntimeError('runtime_busy') from None
                    self._recover_stages(directory, bindings)
                    self._scan(directory, bindings)
                    self._lock_fd = descriptor; descriptor = None
                    self._directory_fd = os.dup(directory)
                    self._bindings_fd = os.dup(bindings)
                    self._owner_pid = os.getpid(); self._closing = False
                    self._credential = dict(schema_version=1, service=SERVICE, store_sha256=self.store_sha256,
                        instance_nonce=secrets.token_hex(32), owner_token=secrets.token_urlsafe(32))
                    self._write(directory, 'credentials.json', self._credential)
                    self._last_activity = self.clock()
                except Exception as exc:
                    # __enter__ failure cannot strand the singleton lock/secret.
                    if self._lock_fd is not None:
                        try:
                            for name, expected in self._owned_files.items():
                                if hashlib.sha256(_raw(directory, name)).hexdigest() == expected:
                                    os.unlink(name, dir_fd=directory)
                            os.fsync(directory)
                        except (RuntimeError, OSError):
                            exc = RuntimeError('runtime_acquire_uncertain', committed=True)
                        finally:
                            self._release_descriptors()
                    if isinstance(exc, OSError):
                        raise RuntimeError('runtime_not_private') from None
                    raise exc from None
                finally:
                    if descriptor is not None: os.close(descriptor)
        return self

    def load_binding(self, binding_id, deadline=None):
        _check(type(binding_id) is str and BINDING.fullmatch(binding_id) is not None)
        with self._mutex, self._directories() as (directory, bindings):
            records, _ = self._settled_scan(directory, bindings, deadline)
            _check(binding_id in records, 'binding_not_found')
            return copy.deepcopy(records[binding_id])

    def list_bindings(self, deadline=None):
        with self._mutex, self._directories() as (directory, bindings):
            records, _ = self._settled_scan(directory, bindings, deadline)
            return [copy.deepcopy(records[key]) for key in sorted(records)]

    def persist_binding(self, record):
        record = _binding(record)
        with self._mutex:
            self._owner()
            records, total = self._scan(self._directory_fd, self._bindings_fd)
            prior = records.get(record['binding_id'])
            _check(prior is not None or len(records) < MAX_BINDINGS, 'binding_capacity')
            if prior is not None:
                _check(all(prior[key] == record[key] for key in ('binding_id', 'actor', 'receipt_session_id')),
                       'binding_identity_conflict')
            _check(total + MAX_RECORD <= MAX_AGGREGATE)
            self._write(self._bindings_fd, record['binding_id'] + '.json', record)

    def publish_discovery(self, port):
        _check(type(port) is int and 1 <= port <= 65535)
        with self._mutex:
            self._owner()
            self._scan(self._directory_fd, self._bindings_fd)
            value = {key: self._credential[key] for key in ('schema_version', 'service', 'store_sha256', 'instance_nonce')}
            value.update(pid=os.getpid(), host='127.0.0.1', port=port)
            self._write(self._directory_fd, 'discovery.json', value)
            return copy.deepcopy(value)

    def _client_pair(self, deadline=None):
        with self._mutex, self._directories() as (directory, bindings):
            self._settled_scan(directory, bindings, deadline)
            try:
                discovery = _identity(_json(_raw(directory, 'discovery.json')), self.store_sha256, credential=False)
                credential = _identity(_json(_raw(directory, 'credentials.json')), self.store_sha256, credential=True)
            except FileNotFoundError:
                raise RuntimeError('owner_unavailable') from None
            _check(discovery['instance_nonce'] == credential['instance_nonce'], 'owner_identity_mismatch')
            return discovery, credential

    def _call(self, operation, discovery, credential, deadline, binding=None, agent=None):
        challenge = secrets.token_hex(32)
        payload = dict(challenge=challenge, instance_nonce=discovery['instance_nonce'], store_sha256=self.store_sha256)
        if binding is not None:
            payload.update(mode=binding[0], binding_id=binding[1], selected_idea_id=binding[2])
        if agent is not None:
            payload.update(binding_id=agent[0], session_id=agent[1], expected_generation=agent[2])
        raw = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        remaining = deadline - time.monotonic()
        _check(remaining > 0, 'owner_unavailable')
        connection = None
        try:
            connection = socket.create_connection(('127.0.0.1', discovery['port']), timeout=remaining)
            request = ('POST /control/v1/' + operation + ' HTTP/1.0\r\nHost: 127.0.0.1:' + str(discovery['port']) +
                       '\r\nAuthorization: Bearer ' + credential['owner_token'] +
                       '\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(raw)) +
                       '\r\nConnection: close\r\n\r\n').encode('ascii') + raw
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            connection.sendall(request)
            buffer = bytearray()
            def receive():
                _check(time.monotonic() < deadline, 'owner_unavailable')
                connection.settimeout(max(0.001, deadline - time.monotonic()))
                chunk = connection.recv(4096)
                _check(bool(chunk), 'owner_identity_mismatch')
                _check(time.monotonic() <= deadline, 'owner_unavailable')
                return chunk
            while b'\r\n\r\n' not in buffer:
                buffer.extend(receive())
                _check(len(buffer) <= MAX_RECORD + 4096, 'owner_identity_mismatch')
            head, body = bytes(buffer).split(b'\r\n\r\n', 1)
            _check(len(head) <= MAX_RECORD, 'owner_identity_mismatch')
            lines = head.split(b'\r\n')
            status = re.fullmatch(rb'HTTP/1\.[01] ([1-5][0-9][0-9]) [\x20-\x7e]*', lines[0])
            _check(len(lines) <= 65 and status is not None, 'owner_identity_mismatch')
            refused = status.group(1) != b'200'
            _check(not refused or status.group(1)[:1] in (b'4', b'5'), 'owner_identity_mismatch')
            headers = {}
            for line in lines[1:]:
                _check(re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+:[\t\x20-\x7e]*", line) is not None,
                       'owner_identity_mismatch')
                key, value = line.split(b':', 1)
                key = key.lower()
                _check(key not in headers, 'owner_identity_mismatch')
                headers[key] = value.strip()
            length = headers.get(b'content-length', b'')
            _check(headers.get(b'content-type') == b'application/json' and re.fullmatch(rb'[0-9]{1,5}', length) is not None
                   and 0 < int(length) <= MAX_RECORD and b'transfer-encoding' not in headers,
                   'owner_identity_mismatch')
            length = int(length)
            body = bytearray(body)
            while len(body) < length:
                body.extend(receive())
            _check(len(body) == length, 'owner_identity_mismatch')
            try:
                value = _json(bytes(body))
            except RuntimeError:
                raise RuntimeError('owner_identity_mismatch') from None
            if refused:
                # An unauthenticated reply carries no challenge: only a fixed code is
                # believed, never text, and only from the owner's own refusal set.
                _check(set(value) == {'ok', 'code'} and value['ok'] is False and type(value['code']) is str
                       and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', value['code']) is not None
                       and value['code'] not in IDENTITY_REFUSALS, 'owner_identity_mismatch')
                raise RuntimeError(value['code'] if value['code'] in OWNER_REFUSALS else 'owner_refused')
            identity_keys = {'ok', 'code', 'schema_version', 'service', 'store_sha256', 'instance_nonce', 'challenge'}
            extra_keys = {'binding_id', 'session_id', 'selected_idea_id', 'pairing_code'} if binding is not None else set()
            if agent is not None:
                extra_keys = {'binding_id', 'session_id', 'generation', 'token'}
            _check(set(value) == identity_keys | extra_keys
                   and value['ok'] is True and type(value['schema_version']) is int and value['schema_version'] == 1
                   and value['code'] == ('stopping' if operation == 'stop' else 'ok')
                   and value['service'] == SERVICE and value['store_sha256'] == self.store_sha256
                   and value['instance_nonce'] == discovery['instance_nonce'] and value['challenge'] == challenge,
                   'owner_identity_mismatch')
            if binding is not None:
                _check(type(value['binding_id']) is str and BINDING.fullmatch(value['binding_id']) is not None
                       and type(value['session_id']) is str and SESSION.fullmatch(value['session_id']) is not None
                       and (value['selected_idea_id'] is None or (type(value['selected_idea_id']) is str and IDEA.fullmatch(value['selected_idea_id']) is not None))
                       and type(value['pairing_code']) is str and value['pairing_code'].isascii()
                       and 0 < len(value['pairing_code']) <= 128 and not any(ord(char) < 33 or ord(char) > 126 for char in value['pairing_code']),
                       'owner_identity_mismatch')
                _check(binding[1] is None or value['binding_id'] == binding[1], 'owner_identity_mismatch')
                _check(binding[2] is None or value['selected_idea_id'] == binding[2], 'owner_identity_mismatch')
            if agent is not None:
                _check(value['binding_id'] == agent[0] and value['session_id'] == agent[1]
                       and type(value['generation']) is str and AGENT.fullmatch(value['generation']) is not None
                       and (agent[2] is None or value['generation'] == agent[2])
                       and type(value['token']) is str and HEX.fullmatch(value['token']) is not None,
                       'owner_identity_mismatch')
            return value
        except OSError:
            raise RuntimeError('owner_unavailable') from None
        finally:
            if connection is not None:
                connection.close()

    def _timeout(self, timeout):
        _check(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 5, 'invalid_timeout')
        return time.monotonic() + timeout

    def probe_owner(self, timeout=1):
        deadline = self._timeout(timeout)
        discovery, credential = self._client_pair(deadline)
        return dict(identity=self._call('probe', discovery, credential, deadline),
                    origin='http://127.0.0.1:' + str(discovery['port']) + '/')

    def request_owned_stop(self, timeout=1):
        deadline = self._timeout(timeout)
        discovery, credential = self._client_pair(deadline)
        self._call('probe', discovery, credential, deadline)
        return self._call('stop', discovery, credential, deadline)

    def open_binding(self, mode, binding_id=None, selected_idea_id=None, timeout=1):
        """Fixed private launcher request; returns only the one-time pairing code.

        Reusable owner/agent credentials never leave this module. A NEW ambiguous
        result must not be retried automatically: source launcher reconciles it.
        """
        _check(mode in ('new', 'resume'), 'invalid_control')
        _check((mode == 'new' and binding_id is None) or
               (mode == 'resume' and type(binding_id) is str and BINDING.fullmatch(binding_id) is not None), 'invalid_control')
        _check(selected_idea_id is None or (type(selected_idea_id) is str and IDEA.fullmatch(selected_idea_id) is not None), 'invalid_control')
        # One overall deadline: binding reads settle within its first half, so a settle never
        # leaves the control call no time (which would read owner_unavailable).
        deadline = self._timeout(timeout)
        reads = deadline - timeout / 2  # half for settling reads; the call keeps at least the other half
        prior = self.load_binding(binding_id, reads) if mode == 'resume' else None
        if mode == 'new':
            _check(len(self.list_bindings(reads)) < MAX_BINDINGS, 'binding_capacity')
        discovery, credential = self._client_pair(deadline)
        self._call('probe', discovery, credential, deadline)
        value = self._call('binding-open', discovery, credential, deadline, (mode, binding_id, selected_idea_id))
        _check(prior is None or value['session_id'] == prior['receipt_session_id'], 'owner_identity_mismatch')
        identity = {key: value[key] for key in ('ok', 'code', 'schema_version', 'service', 'store_sha256', 'instance_nonce', 'challenge')}
        return dict(identity=identity, origin='http://127.0.0.1:' + str(discovery['port']) + '/',
                    **{key: value[key] for key in ('binding_id', 'session_id', 'selected_idea_id', 'pairing_code')})

    def _agent_channel(self, session_id, expected_generation=None, timeout=1):
        """Retrieve an existing generation only; never implicitly open/resume.

        This private seam returns an opaque internal native capability. The
        OwnerService validates these exact request keys after owner auth and
        responds with identity plus binding_id/session_id/generation/token.
        """
        _check(type(session_id) is str and SESSION.fullmatch(session_id) is not None, 'invalid_control')
        _check(expected_generation is None or (type(expected_generation) is str
               and AGENT.fullmatch(expected_generation) is not None), 'invalid_control')
        deadline = self._timeout(timeout)
        reads = deadline - timeout / 2  # one overall deadline: reads settle in half, the call keeps the rest
        matches = [record for record in self.list_bindings(reads) if record['receipt_session_id'] == session_id]
        _check(len(matches) == 1, 'binding_not_found' if not matches else 'binding_ambiguous')
        binding_id = matches[0]['binding_id']
        discovery, credential = self._client_pair(deadline)
        self._call('probe', discovery, credential, deadline)
        value = self._call('agent-credentials', discovery, credential, deadline,
                           agent=(binding_id, session_id, expected_generation))
        return _AgentChannel(discovery['port'], value)

    def validate_owner(self, payload, authorization):
        """Narrow private-channel validator; no raw secret or arbitrary operation.

        Later binding-open checks its fixed full envelope separately, then passes
        only these common three keys. No browser/agent authority is conferred.
        """
        with self._mutex:
            self._owner()
            _check(type(authorization) is str and authorization.isascii()
                   and secrets.compare_digest(authorization, 'Bearer ' + self._credential['owner_token']),
                   'owner_unauthorized')
            _check(type(payload) is dict and set(payload) == {'challenge', 'instance_nonce', 'store_sha256'},
                   'invalid_control')
            _check(all(type(payload[key]) is str and HEX.fullmatch(payload[key]) is not None for key in payload),
                   'invalid_control')
            _check(payload['instance_nonce'] == self._credential['instance_nonce']
                   and payload['store_sha256'] == self.store_sha256, 'owner_identity_mismatch')
            return dict(ok=True, code='ok', schema_version=1, service=SERVICE, store_sha256=self.store_sha256,
                        instance_nonce=self._credential['instance_nonce'], challenge=payload['challenge'])

    def control(self, operation, payload, authorization, request_stop=None):
        with self._mutex:
            _check(operation in ('probe', 'stop'), 'unknown_control')
            result = self.validate_owner(payload, authorization)
            _check(operation != 'stop' or callable(request_stop), 'invalid_control')
            if operation == 'stop':
                result['code'] = 'stopping'
            self.touch()
            return result, request_stop if operation == 'stop' else None

    def touch(self):
        with self._mutex:
            self._last_activity = self.clock()

    @contextmanager
    def active_call(self):
        with self._mutex:
            self._owner(); self._active += 1
        try:
            yield
        finally:
            with self._mutex: self._active -= 1

    def idle_due(self, now=None):
        with self._mutex:
            value = self.clock() if now is None else now
            _check(type(value) in (int, float) and math.isfinite(value), 'invalid_clock')
            return self._active == 0 and value - self._last_activity >= 900

    def close(self):
        with self._mutex:
            if self._lock_fd is None: return
            _check(self._owner_pid == os.getpid(), 'owner_unavailable')
            _check(self._active == 0, 'runtime_busy')
            self._closing = True
            failure = None
            try:
                # Cleanup only exact after-images this instance itself published.
                for name, expected in self._owned_files.items():
                    try: raw = _raw(self._directory_fd, name)
                    except FileNotFoundError: continue
                    _check(hashlib.sha256(raw).hexdigest() == expected)
                for name in self._owned_files:
                    try: os.unlink(name, dir_fd=self._directory_fd)
                    except FileNotFoundError: pass
                os.fsync(self._directory_fd)
            except (RuntimeError, OSError):
                failure = RuntimeError('runtime_cleanup_uncertain')
            finally:
                self._release_descriptors()
            if failure is not None: raise failure
