"""Markdown suggestion-link integration tests. """
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
import idea_markdown as md
import idea_proposal_evidence as evidence
from idea_domain import IdeaError, digest
from idea_workflow import source_digest
from test_markdown import fixture, metadata_fixture, KEY

EXT = md.AGENT_PROPOSAL_EXTENSION


def proposal(number=1, idea_id=KEY):
    data = {'capture': {'raw_text': 'Fixture café 💡\r\n'}}
    return dict(schema_version=1, kind='agent-proposal', proposal_id='proposal_'+format(number, '032x'),
        binding_id='binding_'+'2'*32, generation='agent_'+'3'*32, actor='Operator',
        timestamp='2026-10-02T00:00:00Z', request_id='request-'+str(number), session_id='session_'+'4'*32,
        idea_id=idea_id, accepted_revision=1, draft_version=0, operation='shape',
        source_digest=source_digest('shape', 1, data), data=data,
        proposal=dict(outcome='Useful outcome '+str(number), scope='small-change', scope_reason='One field',
            alternatives=[dict(route='Keep current', reason='Lower cost')], assumptions=[], next_slice='Check input', learning=[]))


def linked_state(state=None, numbers=(1,), *, notes='User notes\r\n', user=None):
    state = fixture() if state is None else state
    refs, blobs = [], {}
    for number in numbers:
        record = proposal(number); raw = evidence.encode_proposal(record)
        link = evidence.proposal_link(record, raw)
        refs.append(link); blobs[link['path']] = raw
    ext = dict(user or {}, **{EXT: refs})
    files = md.encode_state(state, notes={KEY: notes}, extensions={KEY: ext}, proposal_evidence=blobs)
    return state, files, refs, blobs


def documents(files):
    return {path: md.parse_document(raw) for path, raw in files.items()
            if path in ('IDEAS.md', KEY+'.md')}


class ProposalLinkTests(unittest.TestCase):
    def test_full_state_roundtrip_traverses_refs_without_domain_records(self):
        state, files, refs, blobs = linked_state()
        self.assertEqual(md.decode_state(files), state)
        detail = md.decode_detail(files[KEY+'.md'])
        self.assertEqual(md.agent_proposal_links(detail.metadata['extensions'], KEY), refs)
        self.assertIn('Agent proposal 1', detail.body)
        self.assertIn(refs[0]['path'], detail.body)
        self.assertEqual(detail.metadata['idea']['proposals'], [])
        self.assertNotIn(EXT, detail.metadata['idea'])
        self.assertEqual(files[refs[0]['path']], blobs[refs[0]['path']])

    def test_missing_bytes_fail_encode_and_decode(self):
        state, files, refs, blobs = linked_state()
        with self.assertRaises(IdeaError): md.encode_state(state, extensions={KEY: {EXT: refs}})
        broken = dict(files); del broken[refs[0]['path']]
        with self.assertRaises(IdeaError): md.decode_state(broken)
        with self.assertRaises(IdeaError): md.decode_state(broken, check_body=False)
        with self.assertRaises(IdeaError): md.encode_state(state, previous=documents(files), previous_state=state)

    def test_byte_tamper_and_link_hash_change_are_refused(self):
        state, files, refs, blobs = linked_state()
        corrupted = dict(blobs); corrupted[refs[0]['path']] += b'Injected prose\n'
        with self.assertRaises(IdeaError):
            md.encode_state(state, extensions={KEY: {EXT: refs}}, proposal_evidence=corrupted)
        broken = dict(files, **corrupted)
        with self.assertRaises(IdeaError): md.decode_state(broken)
        # Matching an edited byte hash does not make a noncanonical body valid.
        altered = copy.deepcopy(refs); altered[0]['sha256'] = digest(corrupted[refs[0]['path']])
        with self.assertRaises(IdeaError):
            md.encode_state(state, extensions={KEY: {EXT: altered}}, proposal_evidence=corrupted)

    def test_cross_idea_evidence_rejected_even_with_valid_bytes(self):
        state = fixture(); other = proposal(1, 'idea_'+'f'*32)
        raw = evidence.encode_proposal(other); link = evidence.proposal_link(other, raw)
        with self.assertRaises(IdeaError): md.encode_state(state, extensions={KEY: {EXT: [link]}},
            proposal_evidence={link['path']: raw})
        with self.assertRaises(IdeaError): md.agent_proposal_links({EXT: [link]}, KEY)

    def test_duplicate_ids_and_paths_and_link_shapes_refused(self):
        _, _, refs, _ = linked_state()
        first = refs[0]
        record = proposal(2); second = evidence.proposal_link(record)
        for links in ([first, first], [first, dict(second, proposal_id=first['proposal_id'])],
                      [first, dict(second, path=first['path'])], [dict(first, token='refused')],
                      [dict(first, path='../outside.md')], [dict(first, path='/outside.md')],
                      [dict(first, path=first['path'].replace('/', '\\'))],
                      [dict(first, sha256='A'*64)], [dict(first, proposal_id=True)], [None]):
            with self.subTest(links=links), self.assertRaises(IdeaError):
                md.agent_proposal_links({EXT: links}, KEY)
        for value in (None, {}, 'list', [first]*129):
            with self.subTest(value=type(value)), self.assertRaises(IdeaError):
                md.agent_proposal_links({EXT: value}, KEY)

    def test_append_retains_previous_body_baseline_notes_extensions_and_bytes(self):
        state, before, refs, blobs = linked_state(user={'user_setting': {'label': 'Keep me'}})
        record = proposal(2); raw = evidence.encode_proposal(record); link = evidence.proposal_link(record, raw)
        new_refs = refs+[link]; new_blobs = dict(blobs, **{link['path']: raw})
        changed = copy.deepcopy(state); changed['transaction_revision'] += 1
        ext = {'user_setting': {'label': 'Keep me'}, EXT: new_refs}
        after = md.encode_state(changed, extensions={KEY: ext}, previous=documents(before),
                                previous_state=state, proposal_evidence=new_blobs)
        self.assertEqual(md.decode_state(after), changed)
        self.assertEqual(after[refs[0]['path']], before[refs[0]['path']])
        self.assertEqual(md.detail_notes(md.decode_detail(after[KEY+'.md'])), 'User notes\r\n')
        self.assertEqual(md.decode_detail(after[KEY+'.md']).metadata['extensions'], ext)
        self.assertIn('Agent proposal 2', md.decode_detail(after[KEY+'.md']).body)

    def test_removal_reorder_or_changed_previous_prefix_refused(self):
        state, files, refs, blobs = linked_state(numbers=(1, 2))
        previous = documents(files)
        for links in (refs[:1], list(reversed(refs)), [dict(refs[0], sha256='0'*64), refs[1]], []):
            with self.subTest(links=links), self.assertRaises(IdeaError):
                md.encode_state(state, extensions={KEY: {EXT: links}}, previous=previous,
                                previous_state=state, proposal_evidence=blobs)
        with self.assertRaises(IdeaError):
            md.encode_detail(state['ideas'][KEY], extensions={}, previous=previous[KEY+'.md'])

    def test_orphan_mapping_and_malformed_mapping_refused(self):
        state, _, refs, blobs = linked_state()
        second = proposal(2); raw = evidence.encode_proposal(second); link = evidence.proposal_link(second, raw)
        orphan = dict(blobs, **{link['path']: raw})
        with self.assertRaises(IdeaError): md.encode_state(state, extensions={KEY: {EXT: refs}}, proposal_evidence=orphan)
        with self.assertRaises(IdeaError): md.encode_state(state, proposal_evidence=blobs)
        for supplied in ([], {refs[0]['path']: 'not bytes'}, {False: b'bytes'},
                         {refs[0]['path']: b'x'*(md.MAX_INPUT+1)}):
            with self.subTest(supplied=type(supplied)), self.assertRaises(IdeaError):
                md.encode_state(state, extensions={KEY: {EXT: refs}}, proposal_evidence=supplied)
        with patch.object(md, 'MAX_PROPOSAL_EVIDENCE_FILES', 0):
            with self.assertRaises(IdeaError):
                md.encode_state(state, extensions={KEY: {EXT: refs}}, proposal_evidence=blobs)
        with patch.object(md, 'MAX_PROPOSAL_EVIDENCE_BYTES', 1):
            with self.assertRaises(IdeaError):
                md.encode_state(state, extensions={KEY: {EXT: refs}}, proposal_evidence=blobs)

    def test_unchanged_reencode_retains_detail_bytes_and_evidence(self):
        state, files, _, blobs = linked_state(user={'flag': True})
        after = md.encode_state(state, previous=documents(files), previous_state=state, proposal_evidence=blobs)
        self.assertEqual(after, files)
        self.assertEqual(md.decode_state(after), state)

    def test_unrelated_transaction_does_not_rewrite_identical_detail(self):
        state, files, _, blobs = linked_state()
        changed = copy.deepcopy(state); changed['transaction_revision'] += 1
        after = md.encode_state(changed, previous=documents(files), previous_state=state, proposal_evidence=blobs)
        self.assertEqual(after[KEY+'.md'], files[KEY+'.md'])
        for path, raw in blobs.items(): self.assertEqual(after[path], raw)

    def test_no_agent_reference_documents_keep_existing_bytes(self):
        state = metadata_fixture()
        plain = md.encode_state(state, notes={KEY: 'Original Notes\n'}, extensions={KEY: {'custom': [1, 'x']}})
        explicit = md.encode_state(state, notes={KEY: 'Original Notes\n'}, extensions={KEY: {'custom': [1, 'x']}},
                                   proposal_evidence={})
        self.assertEqual(plain, explicit)
        self.assertNotIn('Agent proposal', md.decode_detail(plain[KEY+'.md']).body)
        self.assertNotIn(EXT, md.decode_detail(plain[KEY+'.md']).metadata['extensions'])
        rebuilt = md.encode_state(state, previous=documents(plain), previous_state=state)
        self.assertEqual(rebuilt, plain)
        self.assertEqual(md.decode_state(plain), state)

    def test_legacy_placement_records_remain_distinct_and_byte_identical(self):
        state = metadata_fixture(); old = md.encode_state(state)
        _, with_agent, _, blobs = linked_state(state)
        self.assertEqual(md.decode_state(with_agent), state)
        for path, raw in old.items():
            if path not in (KEY+'.md',): self.assertEqual(with_agent[path], raw)
        detail = md.decode_detail(with_agent[KEY+'.md'])
        self.assertEqual(detail.metadata['idea']['proposals'], state['ideas'][KEY]['proposals'])
        self.assertEqual(detail.metadata['metadata_evidence']['proposals'], md.decode_detail(old[KEY+'.md']).metadata['metadata_evidence']['proposals'])
        self.assertTrue(set(blobs).isdisjoint(set(old)))

    def test_links_return_detached_values_and_do_not_mutate_supplied_inputs(self):
        _, _, refs, _ = linked_state()
        ext = {EXT: copy.deepcopy(refs), 'other': ['Preserved']}; before = copy.deepcopy(ext)
        checked = md.agent_proposal_links(ext, KEY); checked[0]['sha256'] = '0'*64
        self.assertEqual(ext, before)

    def test_generated_link_body_tamper_refused(self):
        _, files, _, _ = linked_state()
        corrupted = dict(files)
        corrupted[KEY+'.md'] = corrupted[KEY+'.md'].replace(b'Agent proposal 1', b'Changed evidence label')
        with self.assertRaises(IdeaError): md.decode_state(corrupted)
        # Import inspection can bypass generated-body checks; evidence hashes
        # still validate, and Store must decide whether an import is authorized.
        self.assertEqual(md.decode_state(corrupted, check_body=False), fixture())


if __name__ == '__main__':
    unittest.main()
