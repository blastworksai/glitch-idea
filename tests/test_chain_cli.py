"""The migration's surfaces (J12c): the plain line on the CLI and the service, held ideas named, never raw."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0, str(SCRIPTS))
from idea_service import Service, TrustedContext
from idea_domain import IdeaError, encoded
from idea_store import Store, _request_ids

FIXTURE = Path(__file__).resolve().parent/'fixtures/store-v2'
FULL = 'idea_'+'1'*32
SHAPE_ONLY = 'idea_'+'2'*32
PLAIN = '2 ideas were updated for this version (originals saved).'
PLAIN_ONE = '1 idea was updated for this version (original saved).'


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)/'store'
        shutil.copytree(FIXTURE, self.root)

    def cli(self, *args):
        done = subprocess.run([sys.executable, str(SCRIPTS/'idea.py'), '--store', str(self.root), *map(str, args)],
                              capture_output=True, timeout=30)
        return done.returncode, json.loads(done.stdout), done.stderr.decode()

    def break_idea(self, key):
        path = self.root/(key+'.md')
        raw = path.read_bytes()
        self.assertIn(b'scope: small-change', raw)
        path.write_bytes(raw.replace(b'scope: small-change', b'scope: galaxy', 1))


class CliNotice(Base):
    def test_cli_notice_on_first_open_only(self):
        code, body, _ = self.cli('list')
        self.assertEqual(code, 0)
        self.assertEqual(body['notice'], PLAIN)
        code, body, err = self.cli('list')
        self.assertEqual(code, 0)
        self.assertNotIn('notice', body)
        self.assertEqual(err, '')

    def test_cli_notice_on_stderr_once(self):
        _, body, err = self.cli('show', FULL)
        self.assertEqual(err, PLAIN+'\n')
        self.assertEqual(body['notice'], PLAIN)

    def test_singular_line_for_one_idea(self):
        self.break_idea(SHAPE_ONLY)
        _, body, err = self.cli('list')
        self.assertEqual(body['notice'], PLAIN_ONE)
        self.assertEqual(err, PLAIN_ONE+'\n')

    def test_doctor_names_held_idea(self):
        self.break_idea(SHAPE_ONLY)
        code, body, _ = self.cli('doctor')
        self.assertEqual(code, 1)
        self.assertEqual(body['error']['code'], 'unhealthy_store')
        self.assertTrue(any(SHAPE_ONLY in issue and 'could not be updated' in issue for issue in body['issues']), body)

    def test_show_held_refuses_with_the_sentence(self):
        self.break_idea(SHAPE_ONLY)
        code, body, _ = self.cli('show', SHAPE_ONLY)
        self.assertEqual(code, 1)
        self.assertEqual(body['error']['code'], 'unsupported_idea_version')
        self.assertIn('could not be updated for this version', body['error']['message'])
        self.assertNotIn(SHAPE_ONLY, body['error']['message'])
        code, body, _ = self.cli('list')
        self.assertEqual(code, 0)
        self.assertEqual([i['idea_id'] for i in body['ideas']], [FULL])


class ServiceNotice(Base):
    def service(self, held=False):
        store = Store(self.root)
        if held:
            # A held store refuses every save (D4), so the session receipt is placed by hand.
            sid = 'session_'+'a'*32
            path = self.root/_request_ids(sid)
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(encoded(dict(schema_version=1, session_id=sid, receipts={})))
            self.break_idea(SHAPE_ONLY)
        else:
            sid = store.create_session()  # the first open migrates; the service keeps that report
        return Service(store, {}, TrustedContext('bound-browser', sid))

    def test_service_state_carries_notice_once(self):
        service = self.service()
        first = service.state()
        self.assertEqual(first['notice'], PLAIN)
        self.assertNotIn('notice', service.state())

    def test_service_state_of_held_idea_refuses(self):
        service = self.service(held=True)
        with self.assertRaises(IdeaError) as raised:
            service.state(SHAPE_ONLY)
        self.assertEqual(raised.exception.code, 'unsupported_idea_version')
        self.assertIn('could not be updated for this version', str(raised.exception))
        self.assertEqual(service.state(FULL)['idea_id'], FULL)

    def test_no_notice_when_nothing_migrated(self):
        self.service().state()
        self.assertNotIn('notice', self.service().state())


if __name__ == '__main__':
    unittest.main()
