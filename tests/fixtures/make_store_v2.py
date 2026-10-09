"""Write the v0.2 fixture store: genuine bytes from glitch-idea v0.2 (commit 1fcbf21).

Usage: python3 tests/fixtures/make_store_v2.py [output-dir]   (default: tests/fixtures/store-v2)

The v0.2 code is read from `git archive 1fcbf21` into a temporary folder and run
as it shipped, so the stored files are what v0.2 itself wrote, never a copy made
by today's encoder. Two ideas: one with every step accepted (tests/test_workflow.complete())
and one with capture and priorities accepted plus a Shape draft only, with notes.
No git write command is run; the output folder is replaced.
"""
import copy
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
V02 = '1fcbf21'
NOTES = 'First line of notes.\n\nSecond line, with a café \U0001F4A1 in it.\n'


def build(out):
    with tempfile.TemporaryDirectory() as temp:
        archive = Path(temp) / 'v02.tar'
        with open(archive, 'wb') as handle:
            subprocess.run(['git', '-C', str(ROOT), 'archive', V02], stdout=handle, check=True)
        source = Path(temp) / 'v02'
        source.mkdir()
        subprocess.run(['tar', '-xf', str(archive), '-C', str(source)], check=True)
        sys.path[:0] = [str(source / 'tests'), str(source / 'glitch-idea' / 'scripts')]
        import hashlib
        import idea_store
        import idea_workflow
        import test_workflow as fixtures
        assert idea_workflow.WORKFLOW_VERSION == 2

        first = fixtures.complete()
        second = fixtures.original_idea()
        key = 'idea_' + '2' * 32
        text = 'Label the shelves — café \U0001F4A1\n'
        second['idea_id'] = key
        second['origin'] = dict(second['origin'], text=text, sha256=hashlib.sha256(text.encode('utf-8')).hexdigest())
        value = copy.deepcopy(fixtures.fields()['capture'])
        value['raw_text'] = text
        digest = idea_workflow.source_digest('capture', 1, {'capture': value})
        second = idea_workflow.capture_workflow(second, value, new_capture=True, actor='operator', timestamp=fixtures.STAMP,
                                                evidence_id='capture-fixture-two', source_digest=digest)['idea']
        second = fixtures.accept(second, 'priorities')['idea']
        draft = {'outcome': 'Shelves read at a glance', 'scope': 'small-change', 'scope_reason': None,
                 'alternatives': [], 'assumptions': ['Labels stay put'], 'next_slice': None, 'learning': []}
        second = idea_workflow.save_draft(second, 'shape', draft, expected_revision=second['revision'],
                                          expected_draft_version=second['workflow']['draft_version'])['idea']

        if out.exists():
            shutil.rmtree(out)
        store = idea_store.Store(out, observer='fixture')
        with store.transaction(write=True) as state:
            state['ideas'][first['idea_id']] = first
            state['ideas'][key] = second
            state['order'] = [first['idea_id'], key]
            store.commit(state)
        detail = out / (key + '.md')
        start, end = '<!-- glitch-idea:notes:start -->\n', '<!-- glitch-idea:notes:end -->\n'
        raw = detail.read_bytes().decode('utf-8')
        head, tail = raw.split(start, 1)
        detail.write_bytes((head + start + NOTES + end).encode('utf-8'))
        with store.transaction() as state:  # v0.2 itself must still read what it wrote
            assert set(state['ideas']) == {first['idea_id'], key}
            assert state['ideas'][key]['workflow']['schema_version'] == 2

        (out / '.lock').unlink(missing_ok=True)


if __name__ == '__main__':
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'tests' / 'fixtures' / 'store-v2'
    build(target.absolute())
