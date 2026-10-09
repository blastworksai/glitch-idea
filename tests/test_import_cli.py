"""import-idea through the real CLI: preview, import, repeat, refusals (SKILLS-62, CP13 J2b).

Both stores are built by the code itself, never copies of a real store.
"""
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
import idea_store as storage

SCRIPT = SCRIPTS/'idea.py'


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(Path(root).rglob('*')) if p.is_file() and p.name != '.lock'}


class ImportCliCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.source = self.base/'source'
        self.target = self.base/'target'
        self.sid, self.key, self.packets, self.links = seed_handoff(self.source)

    def run_cli(self, *args, store=None):
        return subprocess.run([sys.executable, str(SCRIPT), '--store', str(store or self.target), *map(str, args)],
                              capture_output=True, text=True)

    def cli(self, *args, ok=True, store=None):
        result = self.run_cli(*args, store=store)
        self.assertTrue(result.stdout.strip(), 'CLI did not return JSON: '+result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['ok'], ok, data)
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        return data

    def import_args(self, *extra, source=None, idea=None):
        return ('import-idea', '--from', source or self.source, '--idea', idea or self.key, '--actor', 'operator', *extra)


class ImportCliTests(ImportCliCase):
    def test_dry_run_prints_the_preview_and_writes_nothing(self):
        before_source = tree(self.source)
        data = self.cli(*self.import_args('--dry-run'))
        self.assertTrue(data['dry_run'])
        self.assertIn('files', json.dumps(data))
        self.assertFalse(self.target.exists() and tree(self.target), 'a preview wrote to the target')
        self.assertEqual(tree(self.source), before_source)

    def test_import_then_list_shows_the_idea_last_and_source_is_untouched(self):
        native = self.cli('capture', '--text-file', self.text(), '--actor', 'operator')['idea']['idea_id']
        before_source = tree(self.source)
        data = self.cli(*self.import_args())
        self.assertFalse(data .get('dry_run'))
        listed = self.cli('list')
        self.assertEqual(listed['order'], [native, self.key])
        self.assertEqual(tree(self.source), before_source)

    def test_second_run_is_repeated(self):
        self.cli(*self.import_args())
        again = self.cli(*self.import_args())
        self.assertTrue(again['repeated'])
        self.assertEqual(self.cli('list')['order'], [self.key])

    def test_clash_refuses_with_its_code(self):
        other = self.base/'other'
        seed_handoff(other)
        self.cli(*self.import_args(source=other))
        data = self.cli(*self.import_args(), ok=False)
        self.assertEqual(data['error']['code'], 'id_clash', data)

    def test_same_store_refuses(self):
        data = self.cli(*self.import_args(source=self.target), ok=False)
        self.assertEqual(data['error']['code'], 'same_store', data)

    def test_missing_arguments_are_a_usage_refusal(self):
        data = self.cli('import-idea', '--idea', self.key, '--actor', 'operator', ok=False)
        self.assertEqual(data['error']['code'], 'usage', data)

    def text(self):
        path = self.base/'text.txt'
        path.write_text('A native idea', encoding='utf-8')
        return path


class ImportMigrationNotice(ImportCliCase):
    ONE = '1 idea was updated for this version (original saved).'
    KEPT = 'idea_'+'2'*32

    def older_target(self):
        """The version-2 fixture without the idea the source also holds, so the import has no clash."""
        shutil.copytree(Path(__file__).resolve().parent/'fixtures/store-v2', self.target)
        gone = 'idea_'+'1'*32
        (self.target/(gone+'.md')).unlink()
        shutil.rmtree(self.target/'history'/gone)
        index = (self.target/'IDEAS.md').read_text()
        lines = [line for line in index.split('\n') if gone not in line.replace('&#95;', '_')]
        (self.target/'IDEAS.md').write_text('\n'.join(lines).replace('| 2 |', '| 1 |'))

    def test_importing_into_an_older_store_tells_the_migration_once(self):
        self.older_target()
        run = self.run_cli(*self.import_args())
        data = json.loads(run.stdout)
        self.assertTrue(data['ok'], data)
        self.assertEqual(data['notice'], self.ONE)
        self.assertEqual(run.stderr, self.ONE+'\n')
        again = self.run_cli(*self.import_args())
        self.assertNotIn('notice', json.loads(again.stdout))
        self.assertEqual(again.stderr, '')
        self.assertEqual(self.cli('list')['order'], [self.KEPT, self.key])


if __name__ == '__main__':
    unittest.main()
