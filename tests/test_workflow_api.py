"""Setup "where ideas live" and the workflow API client (redesign R9/R10, glitch-idea side).

The owner, 3 Oct 2026: "Option 1: Glitch native - save ideas in .md files within Glitch /
Option 2: API to your own workflow"; keys "should be minted and attributed to the human".
"""
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from idea_domain import IdeaError
from idea_workflow_api import SCHEMA, WorkflowClient, WorkflowError, WorkflowSettings, check_base_url
from fake_workflow_api import FakeWorkflowApi

STORE = 'a' * 64
KEY = 'fixture-key-0123456789'


@unittest.skipUnless(os.name == 'posix', 'Owner-private runtime checks are POSIX-qualified only')
class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'private'
        self.settings = WorkflowSettings(self.root, STORE)

    def code(self, code, fn):
        with self.assertRaises(IdeaError) as caught: fn()
        self.assertEqual(caught.exception.code, code)

    def test_native_is_the_default_and_nothing_is_written_until_saved(self):
        self.assertEqual(self.settings.public(), dict(where='native', api=dict(base_url=None, key_set=False)))
        self.assertEqual(list((self.root / 'settings').iterdir()), [])

    def test_the_key_is_stored_owner_private_and_never_returned(self):
        public = self.settings.save('api', 'https://board.example/workflow/', KEY)
        self.assertEqual(public, dict(where='api', api=dict(base_url='https://board.example/workflow', key_set=True)))
        self.assertNotIn(KEY, json.dumps(public))
        path = self.root / 'settings' / (STORE + '.json')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.root / 'settings').stat().st_mode), 0o700)
        self.assertEqual(json.loads(path.read_text())['schema'], SCHEMA)
        # None keeps the key, '' clears it; switching back to native keeps the URL.
        self.assertTrue(self.settings.save('api', 'https://board.example/workflow', None)['api']['key_set'])
        self.assertEqual(self.settings.save('native', 'https://board.example/workflow', ''),
                         dict(where='native', api=dict(base_url='https://board.example/workflow', key_set=False)))

    def test_option_two_needs_a_url_and_a_key(self):
        self.code('api_needs_url_and_key', lambda: self.settings.save('api', None, KEY))
        self.code('api_needs_url_and_key', lambda: self.settings.save('api', 'https://board.example', None))
        self.code('invalid_settings', lambda: self.settings.save('forge', None, None))
        self.code('invalid_key', lambda: self.settings.save('api', 'https://board.example', 'short'))
        self.code('invalid_key', lambda: self.settings.save('api', 'https://board.example', 'has a space in it'))

    def test_the_key_never_crosses_a_network_in_clear_text(self):
        for url in ('http://board.example', 'http://10.0.0.5:8080/x', 'ftp://board.example', 'https://user:pw@board.example',
                    'https://board.example/?token=x', 'https://board.example/#x', 'javascript:alert(1)', '', 'https://', 'https://b.example:99999'):
            with self.subTest(url=url):
                with self.assertRaises(IdeaError) as caught: check_base_url(url)
                self.assertIn(caught.exception.code, ('invalid_url', 'insecure_url'))
        for url in ('http://127.0.0.1:9000', 'http://localhost/w', 'http://[::1]:8/x', 'https://board.example:8443/api'):
            with self.subTest(url=url): check_base_url(url)

    def test_a_non_private_settings_file_is_refused_not_read(self):
        self.settings.save('api', 'https://board.example', KEY)
        os.chmod(self.root / 'settings' / (STORE + '.json'), 0o644)
        self.code('settings_not_private', self.settings.public)
        os.chmod(self.root / 'settings' / (STORE + '.json'), 0o600)
        os.chmod(self.root / 'settings', 0o755)
        self.code('settings_not_private', self.settings.public)


class ClientTests(unittest.TestCase):
    def test_contract_round_trip_against_a_local_fake(self):
        record = dict(idea_id='idea_' + '1' * 32, title='Lunch box lid', status='in-progress')
        with FakeWorkflowApi(KEY) as fake:
            client = WorkflowClient(fake.url, KEY)
            self.assertEqual(client.health(), dict(service='Fake board', schema=SCHEMA))
            self.assertEqual(client.put_idea(record), dict(idea_id=record['idea_id'], ref='IDEA-1'))
            self.assertEqual(client.get_idea(record['idea_id']), record)
            self.assertEqual(client.list_ideas(), [dict(idea_id=record['idea_id'], ref='IDEA-1')])
            self.assertEqual({call[2] for call in fake.calls}, {'Bearer ' + KEY})
            self.assertNotIn(KEY, repr(client))

    def test_failures_are_fixed_codes_and_never_carry_the_key(self):
        with FakeWorkflowApi(KEY) as fake:
            for mode, code in (('redirect', 'redirect_refused'), ('html', 'invalid_response'), ('huge', 'invalid_response'),
                               ('echo', 'invalid_response'), ('schema', 'schema_mismatch')):
                with self.subTest(mode=mode):
                    fake.mode = mode
                    with self.assertRaises(WorkflowError) as caught: WorkflowClient(fake.url, KEY).health()
                    self.assertEqual(caught.exception.code, code); self.assertNotIn(KEY, str(caught.exception))
            fake.mode = ''
            with self.assertRaises(WorkflowError) as caught: WorkflowClient(fake.url, 'wrong-key-0123456789').health()
            self.assertEqual(caught.exception.code, 'unauthorized')
            with self.assertRaises(WorkflowError) as caught: WorkflowClient(fake.url, KEY).get_idea('idea_' + 'f' * 32)
            self.assertEqual(caught.exception.code, 'not_found')
        with self.assertRaises(WorkflowError) as caught: WorkflowClient('http://127.0.0.1:9', KEY, timeout=1).health()
        self.assertEqual(caught.exception.code, 'unreachable')

    def test_a_key_with_quotes_or_backslashes_is_still_caught_when_echoed(self):
        # Review setup-r1 blocker: JSON escaping hid an echoed key from a serialized search.
        tricky = 'tricky"key\\0123456789'
        with FakeWorkflowApi(tricky) as fake:
            fake.mode = 'echo'
            with self.assertRaises(WorkflowError) as caught: WorkflowClient(fake.url, tricky).health()
            self.assertEqual(caught.exception.code, 'invalid_response')

    def test_dot_segments_cannot_escape_the_base_path(self):
        for url in ('https://board.example/api/..', 'https://board.example/./api', 'https://board.example/api/%2e%2e/x',
                    'https://board.example/api/.%2e', 'https://board.example/api/%2E.'):
            with self.subTest(url=url):
                with self.assertRaises(IdeaError) as caught: check_base_url(url)
                self.assertEqual(caught.exception.code, 'invalid_url')


if __name__ == '__main__':
    unittest.main()
