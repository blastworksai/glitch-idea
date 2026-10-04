"""actual POSIX privacy, child ownership and loopback control proof."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
import idea_runtime as module
from idea_runtime import Runtime, RuntimeError


class ControlHandler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def do_POST(self):
        fixture = self.server.fixture
        fixture.calls.append(self.path)
        raw = self.rfile.read(int(self.headers['Content-Length']))
        payload = json.loads(raw)
        callback = None
        try:
            if self.path == '/control/v1/binding-open':
                if set(payload) != {'challenge', 'instance_nonce', 'store_sha256', 'mode', 'binding_id', 'selected_idea_id'}:
                    raise RuntimeError('invalid_control')
                common = {key: payload[key] for key in ('challenge', 'instance_nonce', 'store_sha256')}
                result = fixture.owner.validate_owner(common, self.headers.get('Authorization'))
                if payload['mode'] == 'new':
                    record = fixture.record()
                    record['selected_idea_id'] = payload['selected_idea_id']
                else:
                    record = fixture.owner.load_binding(payload['binding_id'])
                    if payload['selected_idea_id'] is not None: record['selected_idea_id'] = payload['selected_idea_id']
                fixture.owner.persist_binding(record)
                result.update(binding_id=record['binding_id'], session_id=record['receipt_session_id'],
                              selected_idea_id=record['selected_idea_id'], pairing_code='FIXTURE-ONCE')
            else:
                result, callback = fixture.owner.control(self.path.rsplit('/', 1)[-1], payload,
                    self.headers.get('Authorization'), fixture.stopped.set)
            status = 200
        except RuntimeError as exc:
            status, result = 401, dict(ok=False, code=exc.code)
        if fixture.mode == 'wrong_nonce': result['instance_nonce'] = '0' * 64
        if fixture.mode == 'wrong_challenge': result['challenge'] = '0' * 64
        if fixture.mode == 'extra': result['private'] = 'never accepted'
        if fixture.mode == 'wrong_sid' and 'session_id' in result: result['session_id'] = 'session_' + 'f' * 32
        raw = json.dumps(result).encode()
        if fixture.mode == 'duplicate_json': raw = b'{"ok":true,"ok":false}'
        if fixture.mode == 'refuse': status, raw = fixture.refusal[0], json.dumps(fixture.refusal[1]).encode()
        try:
            if fixture.mode == 'trickle_header':
                self.connection.sendall(b'HTTP/1.0 200 OK\r\nX: ')
                for _ in range(8): time.sleep(0.05); self.connection.sendall(b'x')
                return
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            if fixture.mode == 'duplicate_length': self.send_header('Content-Length', str(len(raw)))
            if fixture.mode == 'chunked': self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            if fixture.mode == 'trickle_body':
                for byte in raw: time.sleep(0.05); self.wfile.write(bytes([byte])); self.wfile.flush()
            else: self.wfile.write(raw); self.wfile.flush()
            if callback is not None: callback()
        except (BrokenPipeError, ConnectionResetError): pass


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name) / 'store Café'
        self.root = Path(self.temp.name) / 'private'
        self.owner = Runtime(self.store, self.root)
        self.client = Runtime(self.store, self.root)
        self.calls = []; self.mode = ''; self.stopped = threading.Event()
        self.addCleanup(self.cleanup_owner)

    def cleanup_owner(self):
        try: self.owner.close()
        except RuntimeError: pass

    def record(self, number=1):
        return dict(schema_version=1, binding_id='binding_' + format(number, '032x'), actor='Operator',
                    receipt_session_id='session_' + format(number, '032x'), selected_idea_id=None)

    def start(self):
        self.owner.acquire_owner()
        server = ThreadingHTTPServer(('127.0.0.1', 0), ControlHandler)
        server.daemon_threads = True; server.fixture = self
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        def stop(): server.shutdown(); server.server_close(); worker.join()
        self.addCleanup(stop)
        self.owner.publish_discovery(server.server_port)
        return server

    def rewrite(self, name, value, *, raw=None):
        path = self.owner.path / name
        path.write_bytes(raw if raw is not None else json.dumps(value).encode())
        os.chmod(path, 0o600)
        return path

    def assert_code(self, code, action):
        with self.assertRaises(RuntimeError) as caught: action()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_private_files_modes_descriptors_and_no_read_client_initialization(self):
        self.assertFalse(self.root.exists())
        self.assert_code('owner_unavailable', self.client.probe_owner)
        self.assertFalse(self.root.exists(), 'client never creates runtime')
        self.owner.acquire_owner(); self.owner.persist_binding(self.record()); self.owner.publish_discovery(12345)
        for path in (self.root, self.root / 'stores', self.owner.path, self.owner.path / 'bindings'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o777, 0o700)
        for path in (self.owner.path / 'service.lock', self.owner.path / 'credentials.json', self.owner.path / 'discovery.json',
                     self.owner.path / 'bindings' / (self.record()['binding_id'] + '.json')):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.stat().st_nlink, 1)
        for descriptor in (self.owner._lock_fd, self.owner._directory_fd, self.owner._bindings_fd):
            self.assertFalse(os.get_inheritable(descriptor))
        self.assertEqual(self.client.load_binding(self.record()['binding_id']), self.record())

    def test_same_process_and_real_subprocess_singleton(self):
        self.owner.acquire_owner()
        self.assert_code('runtime_busy', self.client.acquire_owner)
        source = 'from idea_runtime import Runtime,RuntimeError; import sys\nr=Runtime(sys.argv[1],sys.argv[2])\ntry:r.acquire_owner()\nexcept RuntimeError as e:print(e.code)\nelse:print("unexpected_owner");r.close()'
        result = subprocess.run([sys.executable, '-c', source, str(self.store), str(self.root)],
            env={**os.environ, 'PYTHONPATH': str(Path(module.__file__).parent)}, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr); self.assertEqual(result.stdout.strip(), 'runtime_busy')
        self.owner.close(); self.client.acquire_owner(); self.client.close()

    def test_restart_rotation_preserves_bindings_and_lock_inode(self):
        self.owner.acquire_owner(); self.owner.persist_binding(self.record())
        original = json.loads((self.owner.path / 'credentials.json').read_bytes())
        inode = (self.owner.path / 'service.lock').stat().st_ino
        self.owner.close()
        self.assertTrue((self.owner.path / 'bindings' / (self.record()['binding_id'] + '.json')).exists())
        self.client.acquire_owner()
        try:
            current = json.loads((self.owner.path / 'credentials.json').read_bytes())
            self.assertNotEqual(current['owner_token'], original['owner_token'])
            self.assertNotEqual(current['instance_nonce'], original['instance_nonce'])
            self.assertEqual((self.owner.path / 'service.lock').stat().st_ino, inode)
            self.assertEqual(self.client.list_bindings(), [self.record()])
        finally: self.client.close()

    def test_binding_schema_immutability_capacity_and_detached_reads(self):
        self.owner.acquire_owner(); self.owner.persist_binding(self.record())
        self.owner.persist_binding(dict(self.record(), selected_idea_id='idea_' + 'a' * 32))
        for key, value in (('actor', 'forged'), ('receipt_session_id', 'session_' + 'b' * 32)):
            self.assert_code('binding_identity_conflict', lambda: self.owner.persist_binding(dict(self.record(), **{key: value})))
        for value in (dict(self.record(), extra=True), dict(self.record(), schema_version=True), dict(self.record(), actor=''),
                      dict(self.record(), selected_idea_id='../private'), dict(self.record(), binding_id='binding_bad')):
            self.assert_code('runtime_corrupt', lambda: self.owner.persist_binding(value))
        for number in range(2, 9): self.owner.persist_binding(self.record(number))
        self.assert_code('binding_capacity', lambda: self.owner.persist_binding(self.record(9)))
        records = self.client.list_bindings(); records[0]['actor'] = 'detached'
        self.assertEqual(self.client.load_binding(self.record()['binding_id'])['actor'], 'Operator')
        self.assert_code('runtime_corrupt', lambda: self.client.load_binding('../file'))

    def test_fail_closed_windows_before_creating_any_secret(self):
        with patch.object(module, '_POSIX', False):
            self.assert_code('privacy_unqualified', lambda: Runtime(self.store, self.root))
        self.assertFalse(self.root.exists())

    def test_symlink_components_and_nonprivate_existing_directory(self):
        actual = Path(self.temp.name) / 'actual'; actual.mkdir(mode=0o700)
        link = Path(self.temp.name) / 'alias'; link.symlink_to(actual, target_is_directory=True)
        self.assert_code('runtime_not_private', lambda: Runtime(link / 'store', self.root))
        unsafe = Path(self.temp.name) / 'unsafe'; unsafe.mkdir(mode=0o755)
        runtime = Runtime(self.store, unsafe)
        self.assert_code('runtime_not_private', runtime.acquire_owner)
        self.assertEqual(stat.S_IMODE(unsafe.stat().st_mode), 0o755, 'never chmod unrelated existing directory')
        runtime = Runtime(self.store, link / 'runtime')
        self.assert_code('runtime_not_private', runtime.acquire_owner)

    def test_symlink_hardlink_fifo_and_file_modes_rejected_without_reading(self):
        self.owner.acquire_owner(); self.owner.persist_binding(self.record())
        path = self.owner.path / 'bindings' / (self.record()['binding_id'] + '.json')
        raw = path.read_bytes()
        outside = Path(self.temp.name) / 'outside'; outside.write_bytes(raw); os.chmod(outside, 0o600)
        for kind in ('symlink', 'hardlink', 'fifo', 'mode'):
            with self.subTest(kind=kind):
                path.unlink()
                if kind == 'symlink': path.symlink_to(outside)
                elif kind == 'hardlink': os.link(outside, path)
                elif kind == 'fifo': os.mkfifo(path, 0o600)
                else: path.write_bytes(raw); os.chmod(path, 0o644)
                self.assert_code('runtime_not_private', self.client.list_bindings)
        self.assertEqual(outside.read_bytes(), raw)

    def test_strict_json_unknown_files_size_and_aggregate_caps(self):
        self.owner.acquire_owner(); path = self.owner.path / 'bindings' / (self.record()['binding_id'] + '.json')
        for raw in (b'{"schema_version":1,"schema_version":1}', b'{"x":NaN}', b'{"x":1e999}', b'{"x":"\\ud800"}', b'[]', b'{' + b'"x":[' * 14 + b'0' + b']' * 14 + b'}'):
            path.write_bytes(raw); os.chmod(path, 0o600)
            self.assert_code('runtime_corrupt', self.client.list_bindings)
        path.write_bytes(b'x' * (module.MAX_RECORD + 1))
        self.assert_code('runtime_corrupt', self.client.list_bindings)
        path.unlink(); extra = self.owner.path / 'unknown'; extra.write_bytes(b'keep'); os.chmod(extra, 0o600)
        self.assert_code('runtime_corrupt', self.client.list_bindings); self.assertEqual(extra.read_bytes(), b'keep'); extra.unlink()
        self.owner.persist_binding(self.record())
        with patch.object(module, 'MAX_AGGREGATE', 1): self.assert_code('runtime_corrupt', self.client.list_bindings)

    def test_failed_acquire_releases_lock_and_failed_binding_barrier_is_uncertain(self):
        original = module.os.replace
        with patch.object(module.os, 'replace', side_effect=OSError('private detail')):
            self.assert_code('runtime_persistence_failed', self.owner.acquire_owner)
        self.assertIsNone(self.owner._lock_fd); self.assertIsNone(self.owner._credential)
        self.client.acquire_owner(); self.client.close()
        self.owner.acquire_owner()
        def replace_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('replace return uncertainty')
        # Barrier failure after a known successful replacement preserves final data.
        original_sync = module.os.fsync
        def fail_directory(descriptor):
            if stat.S_ISDIR(os.fstat(descriptor).st_mode): raise OSError('barrier failure')
            original_sync(descriptor)
        with patch.object(module.os, 'fsync', side_effect=fail_directory):
            error = self.assert_code('runtime_persistence_uncertain', lambda: self.owner.persist_binding(self.record()))
        self.assertTrue(error.committed)
        self.assertEqual(self.client.load_binding(self.record()['binding_id']), self.record())

    def test_real_owner_probe_stop_and_narrow_owner_validation(self):
        server = self.start()
        value = self.client.probe_owner()
        self.assertEqual(value['origin'], 'http://127.0.0.1:' + str(server.server_port) + '/')
        self.assertEqual(set(value['identity']), {'ok', 'code', 'schema_version', 'service', 'store_sha256', 'instance_nonce', 'challenge', 'proof'})
        credential = json.loads((self.owner.path / 'credentials.json').read_bytes())
        payload = dict(challenge='a' * 64, instance_nonce=credential['instance_nonce'], store_sha256=self.owner.store_sha256)
        authorization = 'Bearer ' + credential['owner_token']
        self.assert_code('owner_unauthorized', lambda: self.owner.validate_owner(payload, 'wrong'))
        self.assert_code('invalid_control', lambda: self.owner.validate_owner(dict(payload, actor='forged'), authorization))
        self.assert_code('owner_identity_mismatch', lambda: self.owner.validate_owner(dict(payload, instance_nonce='0' * 64), authorization))
        result, callback = self.owner.control('stop', payload, authorization, self.stopped.set)
        self.assertFalse(self.stopped.is_set()); callback(); self.assertTrue(self.stopped.is_set()); self.stopped.clear()
        self.assert_code('unknown_control', lambda: self.owner.control('shell', payload, authorization))
        result = self.client.request_owned_stop()
        self.assertEqual(result['code'], 'stopping'); self.assertTrue(self.stopped.wait(1))
        self.assertEqual(self.calls[-2:], ['/control/v1/probe', '/control/v1/stop'])
        self.assertNotIn('owner_token', json.dumps(result)); self.assertNotIn(credential['owner_token'], json.dumps(value))

    def test_owner_response_identity_and_framing_mismatch_never_stops(self):
        self.start()
        for mode in ('wrong_nonce', 'wrong_challenge', 'extra', 'duplicate_json', 'duplicate_length', 'chunked'):
            with self.subTest(mode=mode):
                self.mode = mode; self.calls.clear()
                self.assert_code('owner_identity_mismatch', self.client.request_owned_stop)
                self.assertNotIn('/control/v1/stop', self.calls); self.assertFalse(self.stopped.is_set())
        self.mode = ''
        discovery = json.loads((self.owner.path / 'discovery.json').read_bytes()); discovery['instance_nonce'] = '0' * 64
        self.rewrite('discovery.json', discovery)
        self.assert_code('owner_identity_mismatch', self.client.probe_owner)

    def test_owner_refusals_are_reported_as_their_own_codes(self):
        # Demo finding: an owner refusal (409 agent_unavailable from session-close) read as an identity failure.
        self.start()
        cases = (
            (409, dict(ok=False, code='agent_unavailable'), 'agent_unavailable'),
            (503, dict(ok=False, code='busy'), 'busy'),
            (404, dict(ok=False, code='binding_not_found'), 'binding_not_found'),
            (403, dict(ok=False, code='session_binding_mismatch'), 'session_binding_mismatch'),
            (409, dict(ok=False, code='runtime_busy'), 'runtime_busy'),
            (500, dict(ok=False, code='corrupt_store'), 'owner_refused'),
            # Identity failures stay identity failures.
            (401, dict(ok=False, code='owner_unauthorized'), 'owner_identity_mismatch'),
            (409, dict(ok=False, code='owner_identity_mismatch'), 'owner_identity_mismatch'),
            # Only an exact {ok:false, code} refusal with a fixed code is believed.
            (409, dict(ok=False, code='agent_unavailable', detail='x'), 'owner_identity_mismatch'),
            (409, dict(ok=True, code='agent_unavailable'), 'owner_identity_mismatch'),
            (409, dict(ok=False, code='Agent unavailable, see /home/x'), 'owner_identity_mismatch'),
            (409, dict(ok=False, code=7), 'owner_identity_mismatch'),
            (302, dict(ok=False, code='busy'), 'owner_identity_mismatch'),
            (201, dict(ok=False, code='busy'), 'owner_identity_mismatch'),
        )
        self.mode = 'refuse'
        for status, body, expected in cases:
            with self.subTest(status=status, body=body):
                self.refusal = (status, body); self.calls.clear()
                self.assert_code(expected, self.client.probe_owner)
                self.assert_code(expected, self.client.request_owned_stop)
                self.assertNotIn('/control/v1/stop', self.calls); self.assertFalse(self.stopped.is_set())

    def test_absolute_probe_header_and_body_deadline_and_unreachable_owner(self):
        self.start()
        for mode in ('trickle_header', 'trickle_body'):
            self.mode = mode; start = time.monotonic()
            self.assert_code('owner_unavailable', lambda: self.client.probe_owner(timeout=0.15))
            self.assertLess(time.monotonic() - start, 0.6)
        self.mode = ''
        self.owner.publish_discovery(1)
        self.assert_code('owner_unavailable', self.client.request_owned_stop)
        self.assertFalse(self.stopped.is_set())

    def test_fixed_binding_open_and_resume_sid_immutable_and_verified_origin(self):
        server = self.start()
        result = self.client.open_binding('new')
        self.assertEqual(result['origin'], 'http://127.0.0.1:' + str(server.server_port) + '/')
        self.assertEqual(result['pairing_code'], 'FIXTURE-ONCE')
        self.assertEqual(result['session_id'], self.record()['receipt_session_id'])
        selected = 'idea_' + 'b' * 32
        resumed = self.client.open_binding('resume', result['binding_id'], selected)
        self.assertEqual(resumed['binding_id'], result['binding_id']); self.assertEqual(resumed['session_id'], result['session_id'])
        self.assertEqual(resumed['selected_idea_id'], selected)
        self.mode = 'wrong_sid'
        self.assert_code('owner_identity_mismatch', lambda: self.client.open_binding('resume', result['binding_id']))
        self.mode = ''
        for number in range(2, 9): self.owner.persist_binding(self.record(number))
        before = list(self.calls)
        self.assert_code('binding_capacity', lambda: self.client.open_binding('new'))
        self.assertEqual(self.calls, before, 'persisted capacity before trusted server NEW')
        for args in (('shell',), ('resume',), ('new', 'binding_' + 'c' * 32), ('resume', '../private')):
            self.assert_code('invalid_control', lambda: self.client.open_binding(*args))

    def test_idle_hook_active_calls_and_cleanup_preserves_outsiders(self):
        now = [0.0]; self.owner.clock = lambda: now[0]
        self.owner.acquire_owner()
        now[0] = 899; self.assertFalse(self.owner.idle_due())
        now[0] = 900; self.assertTrue(self.owner.idle_due())
        with self.owner.active_call():
            self.assertFalse(self.owner.idle_due())
            self.assert_code('runtime_busy', self.owner.close)
        self.owner.touch(); self.assertFalse(self.owner.idle_due())
        self.owner.publish_discovery(12345)
        path = self.owner.path / 'discovery.json'; raw = path.read_bytes(); path.write_bytes(raw + b' ')
        self.assert_code('runtime_cleanup_uncertain', self.owner.close)
        self.assertEqual(path.read_bytes(), raw + b' ')
        self.assertIsNone(self.owner._lock_fd)

    def test_client_waits_for_a_stage_that_settles_within_the_window(self):
        # A browser save stages a binding write while an agent's control call scans;
        # the write finishing within the settle window must not fail the client call.
        self.owner.acquire_owner()
        self.owner.publish_discovery(12345)
        entered, release = threading.Event(), threading.Event()
        original = module.os.replace
        outcome = []
        def paused_replace(*args, **kwargs):
            entered.set()
            if not release.wait(2): raise OSError('test wait exceeded')
            return original(*args, **kwargs)
        def writer():
            try: self.owner.persist_binding(self.record())
            except Exception as exc: outcome.append(exc)
        with patch.object(module.os, 'replace', side_effect=paused_replace):
            worker = threading.Thread(target=writer, daemon=True); worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(len(list((self.owner.path / 'bindings').glob('.runtime_*.tmp'))), 1)
                real_sleep = module.time.sleep
                def settle_sleep(seconds):
                    # The owner's rename completes while the client waits: deterministic, no wall-clock race.
                    release.set(); worker.join(1); real_sleep(seconds)
                with patch.object(self.client, '_scan', wraps=self.client._scan) as scans, \
                        patch.object(module.time, 'sleep', side_effect=settle_sleep):
                    listed = self.client.list_bindings()
                self.assertEqual(len(listed), 1)
                self.assertGreaterEqual(scans.call_count, 2, 'the first scan must have met the stage and settled')
            finally:
                release.set(); worker.join(2)
        self.assertFalse(worker.is_alive()); self.assertEqual(outcome, [])

    def test_settle_rescans_a_name_that_vanished_mid_scan(self):
        # listdir saw the stage, then the owner's rename removed it before open:
        # that is the write completing, so the client re-scans instead of failing.
        self.owner.acquire_owner(); self.owner.publish_discovery(12345)
        self.owner.persist_binding(self.record())
        real = self.client._scan
        calls = []
        def flaky(directory, bindings):
            calls.append(1)
            if len(calls) == 1: raise FileNotFoundError('stage renamed between listdir and open')
            return real(directory, bindings)
        with patch.object(self.client, '_scan', side_effect=flaky):
            self.assertEqual(len(self.client.list_bindings()), 1)
        self.assertEqual(len(calls), 2)

    def test_owner_never_settles_and_client_respects_its_deadline(self):
        self.owner.acquire_owner(); self.owner.publish_discovery(12345)
        stage = self.owner.path / 'bindings' / ('.runtime_binding_' + '0' * 32 + '_' + '1' * 64 + '_' + '2' * 32 + '.tmp')
        descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); os.close(descriptor)
        try:
            started = time.monotonic()
            self.assert_code('runtime_busy', self.owner.list_bindings)
            self.assertLess(time.monotonic() - started, module.STAGE_SETTLE_SECONDS, 'the owner must not sleep on a foreign stage')
            started = time.monotonic()
            self.assert_code('runtime_busy', lambda: self.client.probe_owner(timeout=0.06))
            self.assertLess(time.monotonic() - started, 0.06 + 0.03, 'the settle wait must not pass the caller deadline')
        finally:
            stage.unlink()

    def _stage_the_committed_binding(self):
        # Turn a committed binding back into the owner's in-flight stage name.
        self.owner.acquire_owner(); self.owner.publish_discovery(12345)
        self.owner.persist_binding(self.record())
        final = self.owner.path / 'bindings' / (self.record()['binding_id'] + '.json')
        stage = self.owner.path / 'bindings' / ('.runtime_' + self.record()['binding_id'] + '_' + '1' * 64 + '_' + '2' * 32 + '.tmp')
        os.rename(final, stage)
        return final, stage

    def test_a_settle_that_spends_the_budget_is_busy_not_unavailable(self):
        # Review runtime-r2 #1: the rename lands during the only settle sleep a short probe
        # can afford; reporting owner_unavailable there would spawn a second service.
        final, stage = self._stage_the_committed_binding()
        real_sleep = module.time.sleep
        def settle_sleep(seconds):
            os.rename(stage, final); real_sleep(seconds)
        with patch.object(module.time, 'sleep', side_effect=settle_sleep) as sleeps:
            self.assert_code('runtime_busy', lambda: self.client.probe_owner(timeout=0.06))
        self.assertEqual(sleeps.call_count, 1)
        self.assertTrue(final.exists())

    def test_a_name_vanishing_on_the_last_settle_try_is_busy(self):
        # Review runtime-r2 #3: the rename completing on the final attempt is still the owner's write.
        self.owner.acquire_owner(); self.owner.publish_discovery(12345)
        attempts = []
        def scan(directory, bindings):
            attempts.append(1)
            if len(attempts) < module.STAGE_SETTLE_TRIES: raise module.RuntimeError('runtime_busy')
            raise FileNotFoundError('stage renamed between listdir and open')
        with patch.object(self.client, '_scan', side_effect=scan), patch.object(module.time, 'sleep'):
            self.assert_code('runtime_busy', self.client.list_bindings)
        self.assertEqual(len(attempts), module.STAGE_SETTLE_TRIES)

    def test_resume_and_agent_reads_settle_inside_the_caller_deadline(self):
        # Review runtime-r2 #4: the binding reads before the control call share its deadline.
        final, stage = self._stage_the_committed_binding()
        for name, call in (('open_binding', lambda: self.client.open_binding('resume', self.record()['binding_id'], timeout=0.06)),
                           ('_agent_channel', lambda: self.client._agent_channel(self.record()['receipt_session_id'], timeout=0.06))):
            with self.subTest(call=name):
                started = time.monotonic()
                self.assert_code('runtime_busy', call)
                self.assertLess(time.monotonic() - started, 0.06 + 0.03)
        self.assertTrue(stage.exists(), 'a live stage is never cleaned by a client')

    def test_slow_binding_reads_never_starve_the_control_call(self):
        # Measured in the browser harness: page saves kept the agent's binding scan settling, the
        # shared 1 s budget ran out mid-call, and a live owner read as owner_unavailable.
        self.start(); self.owner.persist_binding(self.record())
        real = self.client.list_bindings
        def slow(deadline=None):
            result = real(deadline)
            if deadline is not None:
                time.sleep(max(0, deadline - time.monotonic() - 0.001))  # the reads used the whole budget
            return result
        started = time.monotonic()
        with patch.object(self.client, 'list_bindings', side_effect=slow):
            # This fixture owner has no agent route: its own refusal proves the call reached it in time.
            self.assert_code('unknown_control', lambda: self.client._agent_channel(self.record()['receipt_session_id'], timeout=0.2))
        self.assertEqual(self.calls[-1], '/control/v1/agent-credentials')
        # One overall deadline (Review fill-r3): the reads used their half, the whole stays inside the caller's.
        self.assertLess(time.monotonic() - started, 0.2 + 0.05)

    def test_real_scan_rescans_a_listed_stage_that_vanished(self):
        # Review runtime-r2 #5: the real _scan/_live_stage path, not a mocked scan.
        self.owner.acquire_owner(); self.owner.publish_discovery(12345)
        self.owner.persist_binding(self.record())
        ghost = '.runtime_' + self.record()['binding_id'] + '_' + '1' * 64 + '_' + '2' * 32 + '.tmp'
        real_listdir = module.os.listdir
        listings = []
        def listdir(target):
            names = real_listdir(target)
            listings.append(1)
            # The second listing of a scan is the bindings directory: report a stage that is already gone.
            return names + [ghost] if len(listings) == 2 else names
        with patch.object(module.os, 'listdir', side_effect=listdir), patch.object(module.time, 'sleep') as sleeps:
            self.assertEqual(self.client.list_bindings(), [self.record()])
        self.assertEqual(sleeps.call_count, 1)
        self.assertEqual(len(listings), 4)

    def test_live_reserved_stage_is_busy_without_client_cleanup(self):
        self.owner.acquire_owner()
        self.owner.publish_discovery(12345)
        entered, release = threading.Event(), threading.Event()
        original = module.os.replace
        outcome = []
        def paused_replace(*args, **kwargs):
            entered.set()
            if not release.wait(2): raise OSError('test wait exceeded')
            return original(*args, **kwargs)
        def writer():
            try: self.owner.persist_binding(self.record())
            except Exception as exc: outcome.append(exc)
        with patch.object(module.os, 'replace', side_effect=paused_replace):
            worker = threading.Thread(target=writer, daemon=True); worker.start()
            try:
                self.assertTrue(entered.wait(1))
                stages = list((self.owner.path / 'bindings').glob('.runtime_*.tmp'))
                self.assertEqual(len(stages), 1)
                before = stages[0].read_bytes()
                self.assert_code('runtime_busy', self.client.list_bindings)
                self.assert_code('runtime_busy', self.client.probe_owner)
                self.assertEqual(stages[0].read_bytes(), before, 'client never cleans the live stage')
            finally:
                release.set(); worker.join(2)
        self.assertFalse(worker.is_alive()); self.assertEqual(outcome, [])
        self.assertEqual(self.client.list_bindings(), [self.record()])
        unknown = self.owner.path / '.runtime_unknown.tmp'
        unknown.write_bytes(b'preserve'); os.chmod(unknown, 0o600)
        self.assert_code('runtime_corrupt', self.client.list_bindings)
        self.assertEqual(unknown.read_bytes(), b'preserve')

    def test_real_kill_complete_stage_recovery_and_partial_stage_preservation(self):
        script = r'''
import os,sys
import idea_runtime as m
r=m.Runtime(sys.argv[1],sys.argv[2])
if sys.argv[3]=='complete':
    original=m.os.replace
    def interrupted(*a,**kw):
        print('staged',flush=True);sys.stdin.readline();return original(*a,**kw)
    m.os.replace=interrupted
else:
    original=m.os.write
    def interrupted(fd,raw):
        result=original(fd,raw[:max(1,len(raw)//2)])
        print('staged',flush=True);sys.stdin.readline();return result
    m.os.write=interrupted
r.acquire_owner()
'''
        for mode in ('complete', 'partial'):
            with self.subTest(mode=mode):
                root = Path(self.temp.name) / mode
                child = subprocess.Popen([sys.executable, '-c', script, str(self.store), str(root), mode],
                    env={**os.environ, 'PYTHONPATH': str(Path(module.__file__).parent)},
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(child.stdout.readline().strip(), 'staged')
                    child.kill(); child.wait(timeout=3)
                    child.stdin.close(); child.stdout.close(); child.stderr.close()
                finally:
                    if child.poll() is None: child.kill(); child.wait(timeout=3)
                runtime = Runtime(self.store, root)
                stages = list(runtime.path.glob('.runtime_*.tmp')); self.assertEqual(len(stages), 1)
                before = stages[0].read_bytes()
                if mode == 'complete':
                    runtime.acquire_owner()
                    self.assertEqual(list(runtime.path.glob('.runtime_*.tmp')), [])
                    runtime.close()
                else:
                    self.assert_code('runtime_recovery_conflict', runtime.acquire_owner)
                    self.assertEqual(stages[0].read_bytes(), before)
                    self.assertIsNone(runtime._lock_fd)

    @unittest.skipUnless(hasattr(os, 'fork'), 'POSIX fork proof only')
    def test_fork_child_drops_inherited_owner_descriptors(self):
        self.owner.acquire_owner()
        pid = os.fork()
        if pid == 0:
            try:
                if self.owner._lock_fd is not None: os._exit(2)
                try: self.owner.acquire_owner()
                except RuntimeError as exc: os._exit(0 if exc.code == 'runtime_busy' else 3)
                os._exit(4)
            except Exception: os._exit(5)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
        self.assert_code('runtime_busy', self.client.acquire_owner)


if __name__ == '__main__': unittest.main()
