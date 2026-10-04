"""The client never sends the owner token to an unproven listener."""
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
from idea_runtime import Runtime, RuntimeError


class FakeListener:
    """Reads one request and echoes challenge/nonce/hash without any proof."""
    def __init__(self, reply=True):
        self.received = bytearray()
        self.reply = reply
        self.sock = socket.socket(); self.sock.bind(('127.0.0.1', 0)); self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True); self.thread.start()

    def serve(self):
        while True:
            try: conn, _ = self.sock.accept()
            except OSError: return
            conn.settimeout(1)
            try:
                while b'\r\n\r\n' not in self.received or not self.received.endswith(b'}'):
                    chunk = conn.recv(4096)
                    if not chunk: break
                    self.received.extend(chunk)
                if self.reply and self.received:
                    payload = json.loads(bytes(self.received).split(b'\r\n\r\n', 1)[1])
                    body = json.dumps(dict(ok=True, code='ok', schema_version=1, service='glitch-idea',
                        store_sha256=payload['store_sha256'], instance_nonce=payload['instance_nonce'],
                        challenge=payload['challenge'])).encode()
                    conn.sendall(b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: '
                                 + str(len(body)).encode() + b'\r\n\r\n' + body)
            except (OSError, ValueError): pass
            finally: conn.close()

    def close(self):
        self.sock.close(); self.thread.join(2)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name) / 'store'; self.root = Path(self.temp.name) / 'private'
        self.owner = Runtime(self.store, self.root); self.client = Runtime(self.store, self.root)
        self.addCleanup(self.owner.close)

    def code(self, action):
        with self.assertRaises(RuntimeError) as caught: action()
        return caught.exception.code

    def test_echo_without_proof_is_refused(self):
        self.owner.acquire_owner()  # live owner holds the lock
        fake = FakeListener(); self.addCleanup(fake.close)
        self.owner.publish_discovery(fake.port)
        self.assertEqual(self.code(self.client.probe_owner), 'owner_identity_mismatch')
        self.assertIn(b'Authorization: Bearer', bytes(fake.received))  # lock was held, so it was reached

    def test_stale_discovery_with_free_lock_sends_nothing(self):
        self.owner.acquire_owner()
        fake = FakeListener(reply=False); self.addCleanup(fake.close)
        self.owner.publish_discovery(fake.port)
        self.owner._release_descriptors()  # unclean exit: files stay, lock is gone
        self.assertEqual(self.code(self.client.probe_owner), 'owner_unavailable')
        self.assertEqual(self.code(lambda: self.client.open_binding('new')), 'owner_unavailable')
        self.assertEqual(bytes(fake.received), b'')

    def test_real_owner_proof_accepted(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        owner = self.owner

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                body = json.dumps(owner.validate_owner(payload, self.headers.get('Authorization'))).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)

        owner.acquire_owner()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler); server.daemon_threads = True
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        def stop(): server.shutdown(); server.server_close(); worker.join()
        self.addCleanup(stop)
        owner.publish_discovery(server.server_port)
        value = self.client.probe_owner()
        self.assertEqual(value['identity']['code'], 'ok')
        self.assertEqual(len(value['identity']['proof']), 64)


if __name__ == '__main__':
    unittest.main()
