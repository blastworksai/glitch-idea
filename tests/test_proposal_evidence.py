"""Immutable suggestion evidence boundary tests. """
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
import idea_proposal_evidence as codec
from idea_domain import IdeaError
from idea_markdown import encode_document, parse_document
from idea_workflow import source_digest


def fixture(operation='exploration'):
    data = {'capture': {'raw_text': ' Café 💡 # original\r\nlast line\n'}}
    proposal = dict(outcome='Easier cleaning', scope='small-change', scope_reason='One lid',
        alternatives=[dict(route='Keep lid', reason='Less effort')], assumptions=[],
        next_slice='Inspect lid', learning=[], investment=None, experiment=None,
        sketch=[dict(title='Inspect the lid', why_next='Cheapest test', done_when='Lid is inspected', method=None)])
    if operation == 'discovery':
        proposal = dict(problem='Lids are hard to clean', audience='Home cooks', workaround='Scrub by hand',
                        evidence='Three complaints', kill_criteria='Nobody cleans lids',
                        challenges=[dict(challenge='Is this a real pain?', response='Reported three times')])
    elif operation == 'memory':
        proposal = dict(status='found', sources=['memory:fixture-preference'], rationale='Recorded preference',
                        preferred_method='bounded-plan')
    elif operation == 'method':
        # The agent never recommends a method: a method proposal is its memory result only.
        proposal = dict(memory=dict(status='unavailable', sources=[], rationale=None))
    return dict(schema_version=1, kind='agent-proposal', proposal_id='proposal_'+'1'*32,
        binding_id='binding_'+'2'*32, generation='agent_'+'3'*32, actor='Operator',
        timestamp='2026-10-01T00:00:00Z', request_id='proposal-request-1', session_id='session_'+'4'*32,
        idea_id='idea_'+'5'*32, accepted_revision=2, draft_version=3, operation=operation,
        source_digest=source_digest(operation, 2, data), data=data, proposal=proposal)


class ProposalEvidenceTests(unittest.TestCase):
    def test_unicode_exact_roundtrip_and_readable_inputs(self):
        record = fixture()
        raw = codec.encode_proposal(record)
        link = codec.proposal_link(record, raw)
        self.assertEqual(codec.decode_proposal(raw, path=link['path'], link=link,
                         expected_idea_id=record['idea_id']), record)
        self.assertIn('Café 💡'.encode(), raw)
        self.assertIn(b'Immutable agent proposal', raw)
        self.assertEqual(link['sha256'], hashlib.sha256(raw).hexdigest())

    def test_content_address_is_canonical_record_not_document_bytes(self):
        record = fixture()
        expected = json.dumps(record, sort_keys=True, ensure_ascii=False,
                              separators=(',', ':'), allow_nan=False).encode()
        self.assertEqual(codec.canonical_record(record), expected)
        path = 'history/'+record['idea_id']+'/metadata/'+hashlib.sha256(expected).hexdigest()+'.md'
        self.assertEqual(codec.proposal_path(record), path)
        reversed_keys = dict(reversed(list(record.items())))
        self.assertEqual(codec.proposal_path(reversed_keys), path)
        self.assertNotEqual(codec.proposal_link(record)['sha256'], hashlib.sha256(expected).hexdigest())

    def test_detached_records_and_links(self):
        record = fixture()
        checked = codec.validate_record(record)
        checked['proposal']['assumptions'].append('Detached')
        self.assertEqual(record['proposal']['assumptions'], [])
        raw = codec.encode_proposal(record)
        decoded = codec.decode_proposal(raw)
        decoded['data']['capture']['raw_text'] = 'Changed detached result'
        self.assertEqual(codec.decode_proposal(raw), record)
        link = codec.proposal_link(record)
        checked_link = codec.validate_link(link)
        checked_link['path'] = 'changed'
        self.assertNotEqual(checked_link, link)

    def test_supported_proposal_types(self):
        for operation in ('discovery', 'exploration', 'method', 'memory'):
            with self.subTest(operation=operation):
                record = fixture(operation)
                self.assertEqual(codec.decode_proposal(codec.encode_proposal(record)), record)

    def test_schema1_shape_proposal_is_refused_as_an_older_version(self):
        record = fixture(); record['operation'] = 'shape'
        record['source_digest'] = '0'*64
        self.assertNotIn('shape', codec.SUPPORTED)
        with self.assertRaises(IdeaError) as caught: codec.validate_record(record)
        self.assertEqual(caught.exception.code, 'unsupported_proposal_version')
        with self.assertRaises(IdeaError) as caught: codec.encode_proposal(record)
        self.assertEqual(caught.exception.code, 'unsupported_proposal_version')

    def test_named_unavailable_operations_are_explicit(self):
        for operation in ('position',):
            record = fixture()
            record['operation'] = operation
            with self.subTest(operation=operation), self.assertRaises(IdeaError) as caught:
                codec.validate_record(record)
            self.assertEqual(caught.exception.code, 'operation_unavailable')

    def test_exact_keys_exclude_credentials_and_arbitrary_path_inputs(self):
        for extra in ('token', 'agent_token', 'credentials', 'path', 'private_memory'):
            record = fixture(); record[extra] = 'not permitted'
            with self.subTest(extra=extra), self.assertRaises(IdeaError):
                codec.validate_record(record)
        for key in ('actor', 'timestamp', 'generation', 'source_digest'):
            record = fixture(); del record[key]
            with self.subTest(missing=key), self.assertRaises(IdeaError):
                codec.validate_record(record)
        for container in ('data', 'proposal'):
            record = fixture(); record[container]['token'] = 'not permitted'
            with self.subTest(container=container), self.assertRaises(IdeaError):
                codec.validate_record(record)

    def test_ids_counters_and_hashes_reject_wrong_types_or_paths(self):
        for key in ('schema_version', 'accepted_revision', 'draft_version'):
            for value in (True, False, '1', 1.5, -1):
                record = fixture(); record[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(IdeaError):
                    codec.validate_record(record)
        for key in ('proposal_id', 'binding_id', 'generation', 'session_id', 'idea_id', 'request_id'):
            for value in ('../outside', '', None, True):
                record = fixture(); record[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(IdeaError):
                    codec.validate_record(record)
        for value in ('A'*64, 'a'*63, '../hash', True):
            record = fixture(); record['source_digest'] = value
            with self.subTest(value=value), self.assertRaises(IdeaError):
                codec.validate_record(record)

    def test_digest_binds_source_revision_and_original_fields_not_draft_counter(self):
        for mutation in ('revision', 'source'):
            record = fixture()
            if mutation == 'revision': record['accepted_revision'] += 1
            else: record['data']['capture']['raw_text'] = 'Changed original source'
            with self.subTest(mutation=mutation), self.assertRaises(IdeaError) as caught:
                codec.validate_record(record)
            self.assertEqual(caught.exception.code, 'stale_source')
        record = fixture(); record['draft_version'] += 1
        self.assertEqual(codec.validate_record(record), record)  # Caller owns draft CAS.
        changed = fixture(); changed['proposal']['outcome'] = 'Edited recommendation'
        self.assertEqual(codec.validate_record(changed)['source_digest'], fixture()['source_digest'])
        self.assertNotEqual(codec.proposal_path(changed), codec.proposal_path(fixture()))

    def test_proposal_type_and_memory_grounding(self):
        record = fixture(); record['proposal']['scope'] = 'invented'
        with self.assertRaises(IdeaError): codec.validate_record(record)
        for extra in ({'investment': {'cap': 1, 'unit': 'day', 'boundary': 'x'}}, {'selection': 'bounded-plan'}, {'reason': 'Why'}):
            record = fixture('method'); record['proposal'].update(extra)
            with self.subTest(extra=extra), self.assertRaises(IdeaError): codec.validate_record(record)
        for memory in ({'status': 'found', 'sources': [], 'rationale': 'Unsupported', 'preferred_method': 'bounded-plan'},
                       {'status': 'found', 'sources': ['ref'], 'rationale': None, 'preferred_method': 'bounded-plan'},
                       {'status': 'found', 'sources': ['ref'], 'rationale': 'No preferred method', 'preferred_method': None},
                       {'status': 'found', 'sources': ['ref'], 'rationale': 'Bad method', 'preferred_method': 'invented'},
                       {'status': 'varied', 'sources': ['ref'], 'rationale': 'Varied', 'preferred_method': 'bounded-plan'},
                       {'status': 'unavailable', 'sources': ['ref'], 'rationale': None},
                       {'status': 'error', 'sources': [], 'rationale': None, 'private_memory': 'raw'},
                       {'status': None, 'sources': [], 'rationale': None}):
            record = fixture('memory'); record['proposal'] = memory
            with self.subTest(memory=memory), self.assertRaises(IdeaError): codec.validate_record(record)
        for status in ('varied', 'searched_no_preference', 'unavailable', 'error'):
            record = fixture('memory'); record['proposal'] = dict(status=status, sources=[], rationale=None, preferred_method=None)
            self.assertEqual(codec.validate_record(record), record)

    def test_wrong_path_idea_and_bytes_hash_refused(self):
        record = fixture(); raw = codec.encode_proposal(record); link = codec.proposal_link(record, raw)
        for path in ('../outside.md', '/absolute.md', link['path'].replace(record['idea_id'], 'idea_'+'6'*32),
                     link['path'].replace('/metadata/', '/unknown/'), link['path'][:-3]+'00.md'):
            with self.subTest(path=path), self.assertRaises(IdeaError): codec.decode_proposal(raw, path=path)
        with self.assertRaises(IdeaError): codec.decode_proposal(raw, expected_idea_id='idea_'+'6'*32)
        bad = dict(link, sha256='0'*64)
        with self.assertRaises(IdeaError): codec.decode_proposal(raw, link=bad)
        bad = dict(link, proposal_id='proposal_'+'7'*32)
        with self.assertRaises(IdeaError): codec.validate_link(bad, raw=raw)
        bad = dict(link, credential='rejected')
        with self.assertRaises(IdeaError): codec.validate_link(bad)
        altered_record = fixture(); altered_record['proposal']['outcome'] = 'Different'
        with self.assertRaises(IdeaError): codec.validate_link(link, raw=raw, record=altered_record)

    def test_body_tamper_and_comments_are_not_silent_record_changes(self):
        raw = codec.encode_proposal(fixture())
        with self.assertRaises(IdeaError): codec.decode_proposal(raw+b'Injected prose\n')
        with self.assertRaises(IdeaError): codec.decode_proposal(raw.replace(b'kind: agent-proposal', b'kind: agent-proposal # comment'))
        record = fixture(); record['proposal']['outcome'] = 'Malicious <script>alert(1)</script>\n# heading'
        raw = codec.encode_proposal(record)
        self.assertEqual(codec.decode_proposal(raw), record)
        body = raw.split(b'---\n', 2)[2]
        self.assertNotIn(b'\n<script>', body)
        self.assertNotIn(b'\n# heading', body)

    def test_equivalent_noncanonical_frontmatter_cannot_change_immutable_bytes(self):
        record = fixture(); raw = codec.encode_proposal(record)
        opening, front, body = raw.split(b'---\n', 2)
        self.assertEqual(opening, b'')
        variants = {
            'crlf': b'---\r\n'+front.replace(b'\n', b'\r\n')+b'---\r\n'+body,
            'order': b'---\n'+front.replace(b'accepted_revision: 2\nactor: Operator\n',
                     b'actor: Operator\naccepted_revision: 2\n')+b'---\n'+body,
            'whitespace': b'---\n'+front.replace(b'kind: agent-proposal',
                          b'kind:  agent-proposal')+b'---\n'+body,
        }
        link = codec.proposal_link(record, raw)
        for name, variant in variants.items():
            with self.subTest(variant=name):
                self.assertNotEqual(variant, raw)
                parsed = parse_document(variant)
                self.assertEqual(parsed.metadata, record)
                self.assertEqual(parsed.body, body.decode('utf-8'))
                self.assertEqual(codec.proposal_path(parsed.metadata), link['path'])
                variant_link = dict(link, sha256=hashlib.sha256(variant).hexdigest())
                self.assertNotEqual(variant_link['sha256'], link['sha256'])
                # Even an updated byte witness cannot bless equivalent YAML.
                with self.assertRaises(IdeaError) as caught:
                    codec.decode_proposal(variant, path=link['path'], link=variant_link)
                self.assertEqual(caught.exception.code, 'corrupt_store')
        self.assertEqual(codec.decode_proposal(raw, link=link), record)

    def test_hostile_yaml_and_invalid_utf8_refused(self):
        for front in ('a: 1\na: 2', 'a: &x [1]\nb: *x', 'a: {<<: {x: 1}}',
                      'a: !!python/object:evil {}', 'a: .nan', 'a: .inf',
                      'a: !!set {one: null}', '1: value', 'a: [broken'):
            raw = ('---\n'+front+'\n---\nbody').encode()
            with self.subTest(front=front), self.assertRaises(IdeaError): codec.decode_proposal(raw)
        with self.assertRaises(IdeaError): codec.decode_proposal(b'---\na: \xff\n---\n')
        raw = codec.encode_proposal(fixture())
        duplicate = raw.replace(b'kind: agent-proposal', b'kind: agent-proposal\nkind: agent-proposal')
        with self.assertRaises(IdeaError): codec.decode_proposal(duplicate)

    def test_json_tree_limits_unicode_nonfinite_and_cycles(self):
        for value in ('bad\ud800', float('nan'), float('inf'), b'bytes', set()):
            record = fixture(); record['proposal']['outcome'] = value
            with self.subTest(value=repr(value)), self.assertRaises(IdeaError): codec.validate_record(record)
        record = fixture(); record['proposal']['outcome'] = record
        with self.assertRaises(IdeaError): codec.validate_record(record)
        with self.assertRaises(IdeaError): codec.decode_proposal(b'x'*(codec.MAX_INPUT+1))
        with patch.object(codec, 'MAX_INPUT', 20):
            with self.assertRaises(IdeaError): codec.validate_record(fixture())

    def test_encoded_document_bound_is_independent_of_canonical_record_bound(self):
        record = fixture()
        canonical_size = len(codec.canonical_record(record))
        self.assertGreater(len(codec.encode_proposal(record)), canonical_size)
        with patch.object(codec, 'MAX_INPUT', canonical_size):
            self.assertEqual(len(codec.canonical_record(record)), canonical_size)
            with self.assertRaises(IdeaError): codec.encode_proposal(record)

    def test_typed_frontmatter_change_requires_new_content_address(self):
        record = fixture(); path = codec.proposal_path(record)
        changed = copy.deepcopy(record); changed['proposal']['next_slice'] = 'Different slice'
        raw = codec.encode_proposal(changed)
        self.assertEqual(codec.decode_proposal(raw), changed)
        with self.assertRaises(IdeaError): codec.decode_proposal(raw, path=path)


if __name__ == '__main__':
    unittest.main()
