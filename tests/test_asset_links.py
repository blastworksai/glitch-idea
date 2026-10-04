"""Asset links and confined journal integration. """
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
import idea_asset_evidence as evidence
import idea_markdown as md
import idea_platform as platform
import idea_transactions as tx
from idea_domain import IdeaError, digest
from test_markdown import fixture, metadata_fixture, KEY
from test_proposal_links import proposal
import idea_proposal_evidence as proposals

EXT = md.ASSET_EXTENSION


def record(number=1, kind='upload-intent', idea_id=KEY):
    value = dict(schema_version=1, kind=kind, idea_id=idea_id, source_revision=1,
                 actor='Operator', timestamp='2026-10-02T00:00:00Z')
    if kind == 'design-set':
        source = {step: dict(revision=1, digest=digest(step.encode())) for step in ('capture', 'shape')}
        return dict(value, set_id='set_'+format(number, '032x'), session_id='session_'+'2'*32, source=source,
                    source_digest=evidence.source_digest(source),
                    members=[dict(asset_id='asset_'+format(number, '032x'), name='Fixture.png',
                                  type='image/png', size=8, sha256='a'*64)])
    value.update(upload_id='upload_'+format(number, '032x'), asset_id='asset_'+format(number, '032x'),
                 session_id='session_'+'2'*32, name='Fixture café 💡.png', declared_type='image/png', size=8)
    if kind == 'asset':
        value.update(blob_path=evidence.blob_path(value['asset_id']), validated_type='image/png', sha256='a'*64)
    return value


def linked_state(numbers=(1,), state=None, user=None):
    state = fixture() if state is None else state
    links, raw_map = [], {}
    for number in numbers:
        value = record(number); raw = evidence.encode_record(value); link = evidence.record_link(value, raw)
        links.append(link); raw_map[link['path']] = raw
    extension = dict(user or {}, **{EXT: links})
    files = md.encode_state(state, notes={KEY: 'Preserved notes\r\n'},
                            extensions={KEY: extension}, asset_evidence=raw_map)
    return state, files, links, raw_map


def documents(files):
    return {path: md.parse_document(raw) for path, raw in files.items()
            if path in ('IDEAS.md', KEY+'.md')}


class AssetLinkTests(unittest.TestCase):
    def test_all_record_kinds_traversed_without_live_domain_registry(self):
        state = fixture(); links, raw_map = [], {}
        for kind in ('upload-intent', 'asset', 'design-set'):
            value = record(kind=kind); raw = evidence.encode_record(value); link = evidence.record_link(value, raw)
            links.append(link); raw_map[link['path']] = raw
        files = md.encode_state(state, extensions={KEY: {EXT: links}}, asset_evidence=raw_map)
        self.assertEqual(md.decode_state(files), state)
        detail = md.decode_detail(files[KEY+'.md'])
        self.assertEqual(md.asset_links(detail.metadata['extensions'], KEY), links)
        self.assertNotIn(EXT, detail.metadata['idea'])
        self.assertIn('Asset evidence 3', detail.body)
        for link in links:
            self.assertIn(link['path'], detail.body)
            self.assertEqual(files[link['path']], raw_map[link['path']])

    def test_missing_linked_bytes_refused_on_encode_decode_and_import(self):
        state, files, links, raw_map = linked_state()
        with self.assertRaises(IdeaError):
            md.encode_state(state, extensions={KEY: {EXT: links}})
        with self.assertRaises(IdeaError):
            md.encode_state(state, previous=documents(files), previous_state=state)
        broken = dict(files); del broken[links[0]['path']]
        for check_body in (True, False):
            with self.assertRaises(IdeaError):
                md.decode_state(broken, check_body=check_body)

    def test_byte_and_identity_tampering_refused_even_with_rehashed_body(self):
        state, files, links, raw_map = linked_state()
        altered = dict(raw_map); altered[links[0]['path']] += b'Injected body\n'
        for references in (links, [dict(links[0], sha256=digest(altered[links[0]['path']]))]):
            with self.assertRaises(IdeaError):
                md.encode_state(state, extensions={KEY: {EXT: references}}, asset_evidence=altered)
        with self.assertRaises(IdeaError):
            md.decode_state(dict(files, **altered), check_body=False)
        for field, value in (('record_id', 'upload_'+'f'*32), ('path', 'assets/evidence/'+'b'*64+'.md')):
            changed = [dict(links[0], **{field: value})]
            with self.assertRaises(IdeaError):
                md.encode_state(state, extensions={KEY: {EXT: changed}}, asset_evidence=raw_map)

    def test_cross_idea_record_rejected_at_both_byte_boundaries(self):
        value = record(idea_id='idea_'+'f'*32); raw = evidence.encode_record(value); link = evidence.record_link(value, raw)
        state = fixture()
        with self.assertRaises(IdeaError):
            md.encode_state(state, extensions={KEY: {EXT: [link]}}, asset_evidence={link['path']: raw})
        files = md.encode_state(state)
        detail = md.parse_document(files[KEY+'.md']); detail.metadata['extensions'][EXT] = [link]
        # Import mode bypasses only generated text, never record association.
        files[KEY+'.md'] = md.encode_document(detail.metadata, detail.body)
        files[link['path']] = raw
        with self.assertRaises(IdeaError):
            md.decode_state(files, check_body=False)

    def test_duplicate_ids_paths_and_malformed_links_refused(self):
        _, _, links, _ = linked_state(numbers=(1, 2))
        first, second = links
        for supplied in ([first, first], [first, dict(second, record_id=first['record_id'])],
                         [first, dict(second, path=first['path'])], [dict(first, token='bad')],
                         [dict(first, path='../outside.md')], [dict(first, path='/outside.md')],
                         [dict(first, path=first['path'].replace('/', '\\'))],
                         [dict(first, record_id=True)], [dict(first, sha256='A'*64)], [None], None, {}, 'list'):
            with self.subTest(supplied=supplied), self.assertRaises(IdeaError):
                md.asset_links({EXT: supplied}, KEY)

    def test_link_capacity_256_and_detached_values(self):
        links = [dict(record_id='upload_'+format(n, '032x'), path='assets/evidence/'+format(n, '064x')+'.md',
                      sha256='a'*64) for n in range(257)]
        extension = {EXT: links[:256], 'other': ['Preserved']}
        original = copy.deepcopy(extension)
        checked = md.asset_links(extension, KEY)
        self.assertEqual(len(checked), 256)
        checked[0]['sha256'] = 'b'*64
        self.assertEqual(extension, original)
        with self.assertRaises(IdeaError) as caught:
            md.asset_links({EXT: links}, KEY)
        self.assertEqual(caught.exception.code, 'too_large')

    def test_append_preserves_previous_bytes_notes_and_custom_extensions(self):
        state, before, links, raw_map = linked_state(user={'user_setting': {'label': 'Keep me'}})
        value = record(2); raw = evidence.encode_record(value); link = evidence.record_link(value, raw)
        changed = copy.deepcopy(state); changed['transaction_revision'] += 1
        extension = {'user_setting': {'label': 'Keep me'}, EXT: links+[link]}
        supplied = dict(raw_map, **{link['path']: raw})
        after = md.encode_state(changed, extensions={KEY: extension}, previous=documents(before),
                                previous_state=state, asset_evidence=supplied)
        self.assertEqual(md.decode_state(after), changed)
        detail = md.decode_detail(after[KEY+'.md'])
        self.assertEqual(md.detail_notes(detail), 'Preserved notes\r\n')
        self.assertEqual(detail.metadata['extensions'], extension)
        for path, existing in raw_map.items():
            self.assertEqual(after[path], existing)

    def test_remove_reorder_change_and_extension_omission_refused(self):
        state, files, links, raw_map = linked_state(numbers=(1, 2))
        previous = documents(files)
        for supplied in (links[:1], list(reversed(links)), [dict(links[0], sha256='b'*64), links[1]], []):
            with self.subTest(supplied=supplied), self.assertRaises(IdeaError):
                md.encode_state(state, extensions={KEY: {EXT: supplied}}, previous=previous,
                                previous_state=state, asset_evidence=raw_map)
        with self.assertRaises(IdeaError):
            md.encode_detail(state['ideas'][KEY], extensions={}, previous=previous[KEY+'.md'])

    def test_complete_map_required_and_malformed_or_orphan_maps_refused(self):
        state, _, links, raw_map = linked_state()
        value = record(2); raw = evidence.encode_record(value); link = evidence.record_link(value, raw)
        for supplied in ([], {False: raw}, {links[0]['path']: 'text'},
                         {links[0]['path']: b'x'*(md.MAX_INPUT+1)}, dict(raw_map, **{link['path']: raw})):
            with self.subTest(kind=type(supplied)), self.assertRaises(IdeaError):
                md.encode_state(state, extensions={KEY: {EXT: links}}, asset_evidence=supplied)
        with self.assertRaises(IdeaError):
            md.encode_state(state, asset_evidence=raw_map)
        for constant, limit in (('MAX_ASSET_EVIDENCE_FILES', 0), ('MAX_ASSET_EVIDENCE_BYTES', 1)):
            with patch.object(md, constant, limit), self.assertRaises(IdeaError):
                md.encode_state(state, extensions={KEY: {EXT: links}}, asset_evidence=raw_map)

    def test_no_asset_state_reencoding_keeps_existing_bytes(self):
        state = metadata_fixture()
        options = dict(notes={KEY: 'Original Notes\n'}, extensions={KEY: {'custom': [1, 'x']}})
        plain = md.encode_state(state, **options)
        explicit = md.encode_state(state, **options, asset_evidence={})
        self.assertEqual(plain, explicit)
        self.assertNotIn('Asset evidence', md.decode_detail(plain[KEY+'.md']).body)
        self.assertEqual(md.encode_state(state, previous=documents(plain), previous_state=state), plain)

    def test_proposal_legacy_and_migration_evidence_remain_distinct_and_unchanged(self):
        state = metadata_fixture(); value = proposal(); raw = proposals.encode_proposal(value)
        suggestion = proposals.proposal_link(value, raw)
        marker = dict(legacy_sha256='b'*64, receipt_path=tx.RECEIPT, frozen_path=tx.FROZEN)
        extensions = {KEY: {md.AGENT_PROPOSAL_EXTENSION: [suggestion]},
                      'IDEAS.md': {'glitch_idea_migration': marker}}
        before = md.encode_state(state, extensions=extensions, proposal_evidence={suggestion['path']: raw})
        asset = record(); asset_raw = evidence.encode_record(asset); link = evidence.record_link(asset, asset_raw)
        extensions[KEY][EXT] = [link]
        after = md.encode_state(state, extensions=extensions, previous=documents(before), previous_state=state,
                                proposal_evidence={suggestion['path']: raw}, asset_evidence={link['path']: asset_raw})
        self.assertEqual(md.decode_state(after), state)
        for path, existing in before.items():
            if path != KEY+'.md':
                self.assertEqual(after[path], existing)
        self.assertEqual(md.decode_index(after['IDEAS.md']).metadata['extensions']['glitch_idea_migration'], marker)

    def test_unchanged_asset_detail_not_rewritten_for_unrelated_transaction(self):
        state, before, _, raw_map = linked_state()
        self.assertEqual(md.encode_state(state, previous=documents(before), previous_state=state,
                                        asset_evidence=raw_map), before)
        later = copy.deepcopy(state); later['transaction_revision'] += 1
        after = md.encode_state(later, previous=documents(before), previous_state=state, asset_evidence=raw_map)
        self.assertEqual(after[KEY+'.md'], before[KEY+'.md'])
        for path, raw in raw_map.items():
            self.assertEqual(after[path], raw)

    def test_generated_asset_label_tampering_requires_repair(self):
        _, files, _, _ = linked_state()
        broken = dict(files); broken[KEY+'.md'] = broken[KEY+'.md'].replace(b'Asset evidence 1', b'Changed label')
        with self.assertRaises(IdeaError):
            md.decode_state(broken)
        self.assertEqual(md.decode_state(broken, check_body=False), fixture())


class Interrupted(Exception):
    pass


class AssetJournalTests(unittest.TestCase):
    def test_allowlist_admits_only_exact_immutable_evidence_path(self):
        path = evidence.evidence_path(record())
        self.assertTrue(tx._allowed(path)); self.assertFalse(tx._mutable(path))
        self.assertEqual(tx._limit(path), md.MAX_INPUT)
        for bad in ('assets/blobs/asset_'+'1'*32+'.bin', 'assets/staging/upload_'+'2'*32+'.nonce.part',
                    'assets/evidence/name.md', path.upper(), '../'+path, '/'+path,
                    path.replace('/', '\\'), path+'/extra', path.replace('.md', '.json')):
            with self.subTest(path=bad):
                self.assertFalse(tx._allowed(bad))
        self.assertTrue(tx._allowed(tx.FROZEN, migration=True))
        self.assertFalse(tx._allowed(tx.FROZEN))
        self.assertTrue(tx._allowed('history/'+KEY+'/metadata/'+'a'*64+'.md'))

    def test_real_publication_and_immutable_collision_preserve_orphan_stages(self):
        value = record(); raw = evidence.encode_record(value); path = evidence.evidence_path(value)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root/'assets/staging/upload_own.nonce.part'; stage.parent.mkdir(parents=True)
            stage.write_bytes(b'Interrupted stream retained')
            with platform.store_lock(root/'.lock'):
                tx.publish(root, {path: raw}, {path: None}).raise_for_error()
                self.assertEqual(tx.file_hash(root, path), digest(raw))
                tx.publish(root, {path: raw}, {path: digest(raw)}).raise_for_error()
                with self.assertRaises(IdeaError):
                    tx.publish(root, {path: raw+b'changed'}, {path: digest(raw)})
                for bad in ('assets/staging/bad.part', 'assets/blobs/asset_'+'2'*32+'.bin'):
                    with self.assertRaises(IdeaError):
                        tx.publish(root, {bad: b'bytes'}, {bad: None})
            self.assertEqual((root/path).read_bytes(), raw)
            self.assertEqual(stage.read_bytes(), b'Interrupted stream retained')

    def test_create_only_publication_preserves_foreign_existing_target(self):
        path = evidence.evidence_path(record()); raw = evidence.encode_record(record())
        foreign = b'Independent user file; never replace this\r\n'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); target = root/path
            target.parent.mkdir(parents=True); target.write_bytes(foreign)
            with platform.store_lock(root/'.lock'):
                with self.assertRaises(IdeaError) as caught:
                    tx.publish(root,{path:raw},{path:None})
                self.assertEqual(caught.exception.code,'save_conflict')
                self.assertEqual(caught.exception.details['path'],path)
                self.assertEqual(target.read_bytes(),foreign)
                self.assertFalse((root/tx.JOURNAL).exists())
                tx.recover(root).raise_for_error()
                self.assertEqual(target.read_bytes(),foreign)

    def test_prepared_create_only_recovery_preserves_foreign_target_and_journal(self):
        path = evidence.evidence_path(record()); raw = evidence.encode_record(record())
        foreign = b'Independent file appeared after prepare\n'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); target = root/path
            def checkpoint(phase):
                if phase == 'prepared': raise Interrupted(phase)
            with platform.store_lock(root/'.lock'):
                with self.assertRaises(Interrupted):
                    tx.publish(root,{path:raw},{path:None},_checkpoint=checkpoint)
                self.assertFalse(target.exists())
                journal = root/tx.JOURNAL
                retained = {entry.relative_to(journal).as_posix():entry.read_bytes()
                            for entry in journal.rglob('*') if entry.is_file()}
                self.assertTrue(retained)
                target.parent.mkdir(parents=True); target.write_bytes(foreign)
                for attempt in range(2):
                    with self.subTest(attempt=attempt), self.assertRaises(IdeaError) as caught:
                        tx.recover(root)
                    self.assertEqual(caught.exception.code,'recovery_conflict')
                    self.assertTrue(caught.exception.details['committed'])
                    self.assertEqual(caught.exception.details['publication'],'uncertain')
                    self.assertEqual(target.read_bytes(),foreign)
                    self.assertEqual({entry.relative_to(journal).as_posix():entry.read_bytes()
                                     for entry in journal.rglob('*') if entry.is_file()},retained)
                # A new publication must reconcile the pending journal first.
                with self.assertRaises(IdeaError) as caught: tx.publish(root,{path:raw},{path:None})
                self.assertEqual(caught.exception.code,'recovery_conflict')
                self.assertEqual(target.read_bytes(),foreign)
                self.assertEqual({entry.relative_to(journal).as_posix():entry.read_bytes()
                                 for entry in journal.rglob('*') if entry.is_file()},retained)

    def test_prepared_and_partial_publication_recover_complete_markdown(self):
        state, files, links, _ = linked_state()
        for boundary in ('prepared', 'published:'+links[0]['path'], 'published:'+KEY+'.md', 'verified', 'complete'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                def checkpoint(phase):
                    if phase == boundary:
                        raise Interrupted(phase)
                with platform.store_lock(root/'.lock'):
                    with self.assertRaises(Interrupted):
                        tx.publish(root, files, {path: None for path in files}, _checkpoint=checkpoint)
                    tx.recover(root).raise_for_error()
                    observed = {path: (root/path).read_bytes() for path in files}
                    self.assertEqual(md.decode_state(observed), state)
                    self.assertEqual(observed, files)
                    self.assertEqual(list((root/tx.JOURNAL).iterdir()), [])

    def test_asset_metadata_byte_limit_applies_before_journal_publication(self):
        path = evidence.evidence_path(record())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with platform.store_lock(root/'.lock'), self.assertRaises(IdeaError):
                tx.publish(root, {path: b'x'*(md.MAX_INPUT+1)}, {path: None})
            self.assertFalse((root/path).exists())

    def test_evidence_symlink_and_directory_targets_refused(self):
        path = evidence.evidence_path(record()); raw = evidence.encode_record(record())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); target = root/path
            target.mkdir(parents=True)
            with platform.store_lock(root/'.lock'), self.assertRaises(IdeaError):
                tx.publish(root, {path: raw}, {path: None})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); outside = root/'outside'; outside.mkdir()
            (root/'assets').mkdir()
            try:
                (root/'assets/evidence').symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest('Native symlink creation unavailable')
            with platform.store_lock(root/'.lock'), self.assertRaises(IdeaError):
                tx.publish(root, {path: raw}, {path: None})
            self.assertEqual(list(outside.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
