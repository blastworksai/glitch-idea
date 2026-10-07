"""Sealed prototype server: confinement, headers, no cookies, loopback bind, liveness stop."""
import http.client
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'
sys.path.insert(0, str(SCRIPTS))
import idea_prototype as proto
from idea_domain import IdeaError

SESSION = 'session_' + '0' * 32
RESPONSES = []


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='proto-')).resolve()
        os.chmod(self.tmp, 0o700)
        self.runtime = private_dir(self.tmp / 'runtime')
        self.root = private_dir(self.runtime / 'prototypes' / 'idea_x')
        (self.root / 'index.html').write_text('<h1>hi</h1>')
        (self.root / 'app.js').write_text('1')
        (self.root / 'sub').mkdir()
        (self.root / 'sub' / 'index.html').write_text('sub')
        (self.tmp / 'secret.txt').write_text('secret')
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def start(self):
        server = proto.PrototypeServer(proto.validate_directory(str(self.root), self.runtime))
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        return server

    def get(self, server, path, method='GET', headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
        try:
            conn.request(method, path, headers=headers or {})
            resp = conn.getresponse()
            body = resp.read()
            RESPONSES.append(resp)
            return resp, body
        finally:
            conn.close()


class ConfinementTests(Base):
    def test_serves_file_and_index(self):
        server = self.start()
        resp, body = self.get(server, '/app.js')
        self.assertEqual((resp.status, body), (200, b'1'))
        resp, body = self.get(server, '/')
        self.assertEqual((resp.status, body), (200, b'<h1>hi</h1>'))
        resp, body = self.get(server, '/sub/')
        self.assertEqual((resp.status, body), (200, b'sub'))
        resp, body = self.get(server, '/app.js', 'HEAD')
        self.assertEqual((resp.status, body), (200, b''))

    def test_binds_loopback_only(self):
        server = self.start()
        self.assertEqual(server.server_address[0], '127.0.0.1')
        self.assertNotEqual(server.server_address[1], 0)

    def test_traversal_encodings_refused(self):
        server = self.start()
        for path in ('/../secret.txt', '/sub/../../secret.txt', '/%2e%2e/secret.txt', '/%2E%2E/secret.txt',
                     '/..%2fsecret.txt', '/sub/%2e%2e%2f%2e%2e%2fsecret.txt', '/..%5csecret.txt',
                     '//etc/passwd', '/%00', '/.%2e/secret.txt', '/\\..\\secret.txt'):
            resp, body = self.get(server, path)
            self.assertIn(resp.status, (400, 403, 404), path)
            self.assertNotIn(b'secret', body, path)

    def test_absolute_path_refused(self):
        server = self.start()
        resp, body = self.get(server, '/' + str(self.tmp / 'secret.txt'))
        self.assertEqual(resp.status, 404)
        self.assertNotIn(b'secret', body)

    def test_symlink_component_refused(self):
        os.symlink(self.tmp, self.root / 'link')
        os.symlink(self.tmp / 'secret.txt', self.root / 'file-link')
        server = self.start()
        for path in ('/link/secret.txt', '/file-link', '/link'):
            resp, body = self.get(server, path)
            self.assertEqual(resp.status, 403, path)
            self.assertNotIn(b'secret', body)

    def test_file_outside_folder_refused(self):
        server = self.start()
        resp, _ = self.get(server, '/../../../secret.txt')
        self.assertIn(resp.status, (403, 404))

    def test_wrong_host_refused(self):
        server = self.start()
        resp, _ = self.get(server, '/app.js', headers={'Host': 'evil.example'})
        self.assertEqual(resp.status, 421)

    def test_methods_other_than_get_head_are_405(self):
        server = self.start()
        for method in ('POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'):
            resp, _ = self.get(server, '/app.js', method)
            self.assertEqual(resp.status, 405, method)


class HeaderTests(Base):
    def test_headers_on_200_and_404_and_405(self):
        server = self.start()
        for path, method, status in (('/app.js', 'GET', 200), ('/missing', 'GET', 404), ('/app.js', 'POST', 405),
                                     ('/../x', 'GET', 403)):
            resp, _ = self.get(server, path, method)
            self.assertEqual(resp.status, status)
            self.assertEqual(resp.getheader('Content-Security-Policy'), proto.CSP)
            self.assertEqual(resp.getheader('X-Content-Type-Options'), 'nosniff')
            self.assertEqual(resp.getheader('Referrer-Policy'), 'no-referrer')
            self.assertEqual(resp.getheader('Cache-Control'), 'no-store')

    def test_no_set_cookie_even_with_cookie_header(self):
        server = self.start()
        for path in ('/app.js', '/missing', '/'):
            resp, _ = self.get(server, path, headers={'Cookie': 'session=abc; a=b'})
            self.assertIsNone(resp.getheader('Set-Cookie'))

    def test_zz_sweep_no_response_ever_set_a_cookie(self):
        self.assertTrue(RESPONSES)
        for resp in RESPONSES:
            self.assertIsNone(resp.getheader('Set-Cookie'))
            self.assertEqual(resp.getheader('Content-Security-Policy'), proto.CSP)


class DirectoryValidationTests(Base):
    def test_group_or_other_bits_refused(self):
        for mode in (0o750, 0o705, 0o755, 0o770):
            os.chmod(self.root, mode)
            with self.assertRaises(IdeaError) as ctx:
                proto.validate_directory(str(self.root), self.runtime)
            self.assertEqual(ctx.exception.code, 'unsafe_prototype_dir', oct(mode))

    def test_outside_prototypes_refused(self):
        other = private_dir(self.tmp / 'elsewhere')
        for bad in (str(other), str(self.runtime / 'prototypes'), str(self.runtime), 'relative/dir'):
            with self.assertRaises(IdeaError) as ctx:
                proto.validate_directory(bad, self.runtime)
            self.assertEqual(ctx.exception.code, 'unsafe_prototype_dir', bad)

    def test_symlinked_folder_refused(self):
        link = self.runtime / 'prototypes' / 'idea_link'
        os.symlink(self.root, link)
        with self.assertRaises(IdeaError) as ctx:
            proto.validate_directory(str(link), self.runtime)
        self.assertEqual(ctx.exception.code, 'unsafe_prototype_dir')

    def test_invalid_session_refused(self):
        with self.assertRaises(IdeaError) as ctx:
            proto.serve_prototype('nope', str(self.root), 'store', self.runtime, alive=lambda: True)
        self.assertEqual(ctx.exception.code, 'invalid_session')
        with self.assertRaises(IdeaError) as ctx:
            proto.serve_prototype(SESSION, str(self.root), 'store', self.runtime, alive=lambda: False)
        self.assertEqual(ctx.exception.code, 'invalid_session')

    def test_start_refuses_unsafe_dir(self):
        os.chmod(self.root, 0o755)
        with self.assertRaises(IdeaError) as ctx:
            proto.serve_prototype(SESSION, str(self.root), 'store', self.runtime, alive=lambda: True)
        self.assertEqual(ctx.exception.code, 'unsafe_prototype_dir')


class LivenessTests(Base):
    def run_serve(self, alive, **kw):
        out = io.StringIO()
        result = {}

        def target():
            result['value'] = proto.serve_prototype(SESSION, str(self.root), 'store', self.runtime,
                                                    alive=alive, out=out, poll=.05, **kw)
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        return thread, out, result

    def test_prints_start_line_and_stops_when_session_ends(self):
        state = {'live': True, 'calls': 0}

        def alive():
            state['calls'] += 1
            return state['live']
        thread, out, result = self.run_serve(alive)
        for _ in range(100):
            if out.getvalue():
                break
            threading.Event().wait(.05)
        line = json.loads(out.getvalue().splitlines()[0])
        self.assertTrue(line['ok'])
        self.assertTrue(line['origin'].startswith('http://127.0.0.1:%d/' % line['port']))
        self.assertEqual(line['ssh_line'], 'ssh -L %d:127.0.0.1:%d <host>' % (line['port'], line['port']))
        conn = http.client.HTTPConnection('127.0.0.1', line['port'], timeout=5)
        conn.request('GET', '/app.js')
        resp = conn.getresponse()
        self.assertEqual(resp.read(), b'1')
        RESPONSES.append(resp)
        conn.close()
        state['live'] = False
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result['value'], dict(ok=True, code='stopped'))
        with self.assertRaises(OSError):
            http.client.HTTPConnection('127.0.0.1', line['port'], timeout=1).request('GET', '/')

    def test_liveness_error_stops_server(self):
        count = {'n': 0}

        def alive():
            count['n'] += 1
            if count['n'] > 2:
                raise OSError('owner gone')
            return True
        thread, _, result = self.run_serve(alive)
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result['value']['code'], 'stopped')

    def test_max_lifetime_backstop(self):
        thread, _, result = self.run_serve(lambda: True, max_lifetime=0.2)
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result['value']['code'], 'stopped')


if __name__ == '__main__':
    unittest.main()
