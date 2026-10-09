"""remove-idea through the real CLI on a store the code builds."""
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
SCRIPTS = Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0, str(SCRIPTS))
from test_handoff_store import seed as seed_handoff

SCRIPT = SCRIPTS/'idea.py'


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(Path(root).rglob('*')) if p.is_file() and p.name != '.lock'}


class RemoveCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name).resolve()/'store'
        self.sid, self.key, self.packets, self.links = seed_handoff(self.store)

    def cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPT), '--store', str(self.store), *map(str, args)], capture_output=True, text=True)
        self.assertTrue(result.stdout.strip(), 'CLI did not return JSON: '+result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['ok'], ok, data)
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        return data

    def test_without_confirm_it_previews_and_writes_nothing(self):
        before = tree(self.store)
        data = self.cli('remove-idea', '--idea', self.key, '--actor', 'operator')
        self.assertFalse(data['removed'])
        self.assertIn('files', data)
        self.assertEqual(tree(self.store), before)

    def test_confirm_removes_then_list_and_doctor_are_clean_and_a_second_run_is_not_found(self):
        data = self.cli('remove-idea', '--idea', self.key, '--actor', 'operator', '--confirm')
        self.assertTrue(data['removed'])
        self.assertEqual(self.cli('list')['order'], [])
        self.assertTrue(self.cli('doctor')['healthy'])
        self.assertTrue((self.store/'history'/'removed'/(self.key+'.md')).exists())
        again = self.cli('remove-idea', '--idea', self.key, '--actor', 'operator', '--confirm', ok=False)
        self.assertEqual(again['error']['code'], 'not_found', again)

    def test_unknown_idea_is_not_found(self):
        data = self.cli('remove-idea', '--idea', 'idea_'+'9'*32, '--actor', 'operator', '--confirm', ok=False)
        self.assertEqual(data['error']['code'], 'not_found', data)

    def test_actor_is_required(self):
        result = subprocess.run([sys.executable, str(SCRIPT), '--store', str(self.store), 'remove-idea', '--idea', self.key], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)


class RemoveMigrationNotice(unittest.TestCase):
    ONE = '2 ideas were updated for this version (originals saved).'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name).resolve()/'store'
        shutil.copytree(Path(__file__).resolve().parent/'fixtures/store-v2', self.store)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), '--store', str(self.store), *map(str, args)], capture_output=True, text=True)

    def test_a_preview_on_an_older_store_carries_the_notice_once(self):
        run = self.run_cli('remove-idea', '--idea', 'idea_'+'2'*32, '--actor', 'operator')
        data = json.loads(run.stdout)
        self.assertTrue(data['ok'], data)
        self.assertEqual(data['notice'], self.ONE)
        self.assertEqual(run.stderr, self.ONE+'\n')
        again = self.run_cli('remove-idea', '--idea', 'idea_'+'2'*32, '--actor', 'operator')
        self.assertNotIn('notice', json.loads(again.stdout))
        self.assertEqual(again.stderr, '')

    def test_a_refusal_on_an_older_store_carries_the_notice(self):
        run = self.run_cli('remove-idea', '--idea', 'idea_'+'9'*32, '--actor', 'operator', '--confirm')
        data = json.loads(run.stdout)
        self.assertEqual(data['error']['code'], 'not_found', data)
        self.assertEqual(data.get('notice'), self.ONE, data)
        self.assertEqual(run.stderr, self.ONE+'\n')


if __name__ == '__main__':
    unittest.main()
