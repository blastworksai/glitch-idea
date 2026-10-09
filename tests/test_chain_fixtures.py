"""The scrubbed pre-prior-art v0.3 store: it opens on this code, and opening it changes nothing."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / 'glitch-idea/scripts'
sys.path.insert(0, str(SCRIPTS))
import idea_store as storage

FIXTURE = HERE / 'fixtures/store-v03-preprior'
TERMS = HERE / 'private-terms.txt'
KEY = 'idea_85f69bb3fc63423ea8a6dde2c86a62cd'
LINE = 'Does it already exist'


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob('*')) if p.is_file() and p.name != '.lock'}


class PreriorArtStore(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'store'
        shutil.copytree(FIXTURE, self.root)

    def cli(self, *args):
        r = subprocess.run([sys.executable, str(SCRIPTS / 'idea.py'), '--store', str(self.root), *args], capture_output=True, timeout=30)
        return r.returncode, json.loads(r.stdout)

    def test_preprior_store_opens_without_refusal(self):
        self.assertNotIn(LINE.encode(), (self.root / (KEY + '.md')).read_bytes(), 'the fixture must be the pre-prior-art form')
        with storage.Store(self.root).transaction() as state:
            loaded = copy.deepcopy(state)
        self.assertEqual(loaded['order'], [KEY])
        self.assertEqual(loaded['ideas'][KEY]['revision'], 7)
        self.assertEqual(loaded['ideas'][KEY]['workflow']['schema_version'], 3)
        for command in ('list', 'doctor'):
            code, out = self.cli(command)
            self.assertEqual(code, 0, out)
            self.assertTrue(out.get('ok'), out)

    def test_preprior_open_migrates_nothing_and_writes_nothing(self):
        before = snapshot(self.root)
        self.assertEqual(before, snapshot(FIXTURE))
        for _ in range(2):
            with storage.Store(self.root).transaction():
                pass
            self.assertEqual(snapshot(self.root), before)
        self.cli('list')
        self.cli('doctor')
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse((self.root / 'history' / KEY / 'migrations').exists())

    def test_fixture_holds_no_private_term(self):
        self.assertTrue(TERMS.exists(), 'tests/private-terms.txt must be copied in before this test')
        terms = [t.strip().lower() for t in TERMS.read_text(encoding='utf-8').splitlines() if t.strip()]
        self.assertTrue(terms)
        for path in sorted(FIXTURE.rglob('*')):
            if path.is_file():
                text = (path.relative_to(FIXTURE).as_posix() + '\n' + path.read_text(encoding='utf-8')).lower()
                for n, term in enumerate(terms, 1):
                    self.assertNotIn(term, text, 'term ' + str(n) + ' in ' + path.relative_to(FIXTURE).as_posix())
                for host in ('/opt/', '/home/', '/tmp/'):
                    self.assertNotIn(host, text, path.name)


if __name__ == '__main__':
    unittest.main()
