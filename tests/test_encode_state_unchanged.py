"""encode_state's unchanged-idea shortcut (CP6 J6d). Operator.

The shortcut must be byte-identical to the full path, must not skip evidence
checks, and must parse O(changed) documents, not O(ideas).
"""
import copy
import dataclasses
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
import idea_markdown as md
from idea_domain import IdeaError
from test_handoff_links import linked_state, documents, EXT
from test_markdown import metadata_fixture, KEY

EXTRA = 6


def extra_idea(n):
    state = metadata_fixture()
    text = json.dumps(state['ideas'][KEY])
    key = 'idea_' + format(n + 100, '032x')
    text = text.replace(KEY, key).replace('plan_' + '1' * 32, 'plan_' + format(n + 100, '032x'))
    return key, json.loads(text)


def build():
    state, files, links, raw_map = linked_state(numbers=(1, 2))
    hk = state['order'][0]
    for n in range(EXTRA):
        key, idea = extra_idea(n)
        state['order'].append(key)
        state['ideas'][key] = idea
    ext = {hk: {EXT: links}}
    notes = {hk: 'Preserved Notes\r\n'}
    first = md.encode_state(state, notes=notes, extensions=ext, handoff_evidence=raw_map)
    return state, first, hk, links, raw_map


def changed_run(shortcut=True, evidence=None):
    state, first, hk, links, raw_map = build()
    previous = documents(first)
    changed = copy.deepcopy(state)
    changed['transaction_revision'] += 1
    victim = changed['order'][-1]
    changed['ideas'][victim]['status'] = 'active'
    with patch.object(md, '_UNCHANGED_SHORTCUT', shortcut):
        return state, changed, previous, hk, raw_map if evidence is None else evidence, first


class UnchangedShortcutTests(unittest.TestCase):
    def encode(self, shortcut, changed, previous, state, raw_map):
        with patch.object(md, '_UNCHANGED_SHORTCUT', shortcut):
            return md.encode_state(changed, previous=previous, previous_state=state, handoff_evidence=raw_map)

    def test_fast_path_is_byte_identical_to_full_path_for_every_file(self):
        state, changed, previous, hk, raw_map, first = changed_run()
        fast = self.encode(True, changed, previous, state, raw_map)
        slow = self.encode(False, changed, previous, state, raw_map)
        self.assertEqual(set(fast), set(slow))
        for name in slow:
            self.assertEqual(fast[name], slow[name], name)
        self.assertNotEqual(fast[changed['order'][-1] + '.md'], first[changed['order'][-1] + '.md'])
        # Unchanged details (plans, executions, proposals, handoff links) were reused unchanged.
        for key in changed['order'][:-1]:
            self.assertEqual(fast[key + '.md'], first[key + '.md'])
        self.assertIn(hk + '.md', fast)

    def test_shortcut_is_actually_taken_for_unchanged_ideas(self):
        state, changed, previous, hk, raw_map, _ = changed_run()
        real = md._encode_unchanged_detail
        taken = []
        def spy(*args):
            result = real(*args); taken.append(result[0] is not None); return result
        with patch.object(md, '_encode_unchanged_detail', spy):
            self.encode(True, changed, previous, state, raw_map)
        self.assertEqual(taken.count(True), len(changed['order']) - 1)

    def test_unchanged_idea_with_missing_or_altered_evidence_is_still_refused(self):
        state, changed, previous, hk, raw_map, _ = changed_run()
        missing = dict(raw_map); missing.popitem()
        with self.assertRaises(IdeaError) as caught:
            self.encode(True, changed, previous, state, missing)
        self.assertEqual(caught.exception.code, 'corrupt_store')
        altered = {path: raw + b' ' for path, raw in raw_map.items()}
        with self.assertRaises(IdeaError):
            self.encode(True, changed, previous, state, altered)
        orphan = dict(raw_map, **{'handoffs/extra.json': b'{}'})
        with self.assertRaises(IdeaError) as caught:
            self.encode(True, changed, previous, state, orphan)
        self.assertEqual(caught.exception.code, 'corrupt_store')
        # Same refusals on the full path: the shortcut changed nothing.
        with self.assertRaises(IdeaError) as caught:
            self.encode(False, changed, previous, state, missing)
        self.assertEqual(caught.exception.code, 'corrupt_store')

    def test_tampered_previous_body_or_metadata_falls_back_and_refuses_as_before(self):
        state, changed, previous, hk, raw_map, _ = changed_run()
        name = hk + '.md'
        for mutate in (lambda d: dataclasses.replace(d, body=d.body.replace('## Evidence', '## Edited', 1)),
                       lambda d: dataclasses.replace(d, has_comments=True)):
            tampered = dict(previous); tampered[name] = mutate(previous[name])
            outcomes = []
            for shortcut in (True, False):
                with self.assertRaises(IdeaError) as caught:
                    self.encode(shortcut, changed, tampered, state, raw_map)
                outcomes.append(caught.exception.code)
            self.assertEqual(outcomes[0], outcomes[1])

    def test_parse_calls_scale_with_changed_ideas_not_all_ideas(self):
        state, changed, previous, hk, raw_map, _ = changed_run()
        counts = {}
        for shortcut in (True, False):
            real = md.parse_document
            calls = []
            def counting(raw, _real=real, _calls=calls):
                _calls.append(1); return _real(raw)
            with patch.object(md, 'parse_document', counting):
                self.encode(shortcut, changed, previous, state, raw_map)
            counts[shortcut] = len(calls)
        n = len(changed['order'])
        self.assertLessEqual(counts[True], 6)
        self.assertGreaterEqual(counts[False], 3 * n)
        self.assertGreater(counts[False] - counts[True], 2 * (n - 1))


if __name__ == '__main__':
    unittest.main()
