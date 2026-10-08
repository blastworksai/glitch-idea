"""An idea accepted before the prior-art fields existed must still load, byte-unchanged on read."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
SCRIPTS = Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'
sys.path.insert(0, str(SCRIPTS))
import idea_markdown as md
import idea_store as storage
from test_workflow import complete

PRIOR_KEYS = ('prior_art', 'prior_art_none', 'prior_art_searched')
KEY = 'idea_' + '1' * 32
LINE = '- Does it already exist: Not checked'


def legacy_idea():
    idea = complete()
    for holder in [idea['workflow']] + [r['workflow'] for r in idea['revisions'] if r.get('workflow')]:
        record = holder['steps'].get('discovery')
        for key in PRIOR_KEYS:
            if record and record.get('fields'):
                record['fields'].pop(key, None)
    return idea


def ordinary_idea(key):
    text = json.dumps(complete()).replace(KEY, key)
    return json.loads(text)


class LegacyPriorArtTests(unittest.TestCase):
    def build(self, shape):
        """shape 1: written by the code before the fields (no line); shape 2: re-saved by v0.3 (line present)."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / 'store'
        store = storage.Store(root)
        patcher = mock.patch.object(md, '_prior_art_lines', lambda d, legacy_line=True: []) if shape == 1 else mock.MagicMock()
        with patcher:
            with store.transaction(write=True) as state:
                state['ideas'][KEY] = legacy_idea()
                state['order'] = [KEY]
                state['backlog_revision'] = 1
                store.commit(state)
        body = (root / (KEY + '.md')).read_text(encoding='utf-8')
        self.assertEqual(LINE in body, shape == 2)
        return root

    def snapshot(self, root):
        return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob('*')) if p.is_file() and p.name != '.lock'}

    def cli(self, root, *args):
        r = subprocess.run([sys.executable, str(SCRIPTS / 'idea.py'), '--store', str(root), *args], capture_output=True, timeout=20)
        return r.returncode, json.loads(r.stdout)

    def check_shape(self, shape):
        root = self.build(shape)
        before = self.snapshot(root)
        with storage.Store(root).transaction() as state:
            loaded = copy.deepcopy(state)
        self.assertIn(KEY, loaded['ideas'])
        for command in ('list', 'doctor'):
            code, out = self.cli(root, command)
            self.assertEqual(code, 0, out)
            self.assertTrue(out.get('ok'), out)
        self.assertEqual(self.snapshot(root), before, 'a read must not rewrite any stored file')

    def test_saved_before_the_fields_existed_loads_unchanged(self):
        self.check_shape(1)

    def test_resaved_by_v03_with_not_checked_line_loads_unchanged(self):
        self.check_shape(2)

    def test_unchanged_commit_keeps_a_legacy_body_byte_for_byte(self):
        root = self.build(1)
        before = self.snapshot(root)
        store = storage.Store(root)
        with store.transaction(write=True) as state:
            state['backlog_revision'] += 1  # a real write that leaves this idea itself untouched
            store.commit(state)
        after = self.snapshot(root)
        self.assertNotEqual(after, before, 'the commit must have written something, or the test proves nothing')
        self.assertEqual(after[KEY + '.md'], before[KEY + '.md'])

    def test_writing_another_idea_leaves_a_legacy_body_byte_for_byte(self):
        root = self.build(1)
        before = self.snapshot(root)
        other = 'idea_' + '2' * 32
        store = storage.Store(root)
        with store.transaction(write=True) as state:
            state['ideas'][other] = ordinary_idea(other)
            state['order'].append(other)
            state['backlog_revision'] += 1
            store.commit(state)
        after = self.snapshot(root)
        self.assertIn(other + '.md', after)
        self.assertEqual(after[KEY + '.md'], before[KEY + '.md'])

    def test_a_legacy_body_with_a_foreign_edit_is_still_refused(self):
        root = self.build(1)
        path = root / (KEY + '.md')
        path.write_text(path.read_text(encoding='utf-8').replace('- Problem: ', '- Problem: tampered ', 1), encoding='utf-8')
        code, out = self.cli(root, 'list')
        self.assertNotEqual(code, 0)


if __name__ == '__main__':
    unittest.main()
