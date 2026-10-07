"""Pure asset evidence boundary checks. """
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
import idea_asset_evidence as codec
from idea_domain import IdeaError
from idea_markdown import encode_document, parse_document
from idea_workflow import source_digest as workflow_digest


def identifier(prefix, number):
    return prefix+'_'+format(number, '032x')


def fixture(kind='upload-intent'):
    common = dict(schema_version=1, kind=kind, idea_id=identifier('idea', 1),
                  source_revision=3, actor='Operator', timestamp='2026-10-02T00:00:00Z')
    if kind == 'design-set':
        source = {
            'capture': dict(revision=1, digest=workflow_digest('capture', 1, {'capture': {'raw_text': 'Original'}})),
            'discovery': dict(revision=2, digest=workflow_digest('discovery', 2, {'discovery': {'problem': 'Hard to clean'}})),
            'exploration': dict(revision=3, digest=workflow_digest('exploration', 3, {'exploration': {'outcome': 'Clearer page'}})),
        }
        return dict(common, set_id=identifier('set', 4), session_id=identifier('session', 5), source=source, source_digest=codec.source_digest(source),
                    members=[dict(asset_id=identifier('asset', 3), name='Café 💡.png', type='image/png',
                                  size=128, sha256='a'*64)])
    upload = dict(common, upload_id=identifier('upload', 2), asset_id=identifier('asset', 3),
                  session_id=identifier('session', 5), name='Café 💡.png', declared_type='image/png', size=128)
    if kind == 'asset':
        upload.update(blob_path=codec.blob_path(upload['asset_id']), validated_type='image/png', sha256='a'*64)
    return upload


class AssetEvidenceTests(unittest.TestCase):
    def assert_refused(self, callback, code=None):
        with self.assertRaises(IdeaError) as caught:
            callback()
        if code is not None:
            self.assertEqual(caught.exception.code, code)

    def test_all_record_kinds_roundtrip_with_frontmatter_and_witnesses(self):
        for kind, prefix in (('upload-intent', 'upload'), ('asset', 'asset'), ('design-set', 'set')):
            with self.subTest(kind=kind):
                record = fixture(kind)
                raw = codec.encode_record(record)
                link = codec.record_link(record, raw)
                self.assertTrue(raw.startswith(b'---\n'))
                self.assertIn('Café 💡'.encode(), raw)
                self.assertEqual(codec.decode_record(raw, path=link['path'], link=link,
                                 expected_idea_id=record['idea_id']), record)
                self.assertEqual(link['record_id'], record[prefix+'_id'])
                self.assertEqual(link['sha256'], hashlib.sha256(raw).hexdigest())

    def test_set_publishing_session_is_required_typed_and_hash_bound(self):
        record = fixture('design-set'); missing = copy.deepcopy(record); del missing['session_id']
        self.assert_refused(lambda:codec.validate_record(missing))
        for value in (None,True,identifier('idea',5),'session_BAD'):
            changed = copy.deepcopy(record); changed['session_id'] = value
            self.assert_refused(lambda:codec.validate_record(changed))
        changed = copy.deepcopy(record); changed['session_id'] = identifier('session',6)
        self.assertNotEqual(codec.evidence_path(record),codec.evidence_path(changed))
        self.assertEqual(record['source_digest'],changed['source_digest'])

    def test_address_hashes_canonical_record_not_markdown(self):
        record = fixture('design-set')
        canonical = json.dumps(record, ensure_ascii=False, sort_keys=True,
                               separators=(',', ':'), allow_nan=False).encode()
        self.assertEqual(codec.canonical_record(record), canonical)
        self.assertEqual(codec.evidence_path(record), 'assets/evidence/'+hashlib.sha256(canonical).hexdigest()+'.md')
        self.assertEqual(codec.evidence_path(dict(reversed(list(record.items())))), codec.evidence_path(record))
        self.assertNotEqual(codec.record_link(record)['sha256'], hashlib.sha256(canonical).hexdigest())

    def test_returned_nested_records_and_links_are_detached(self):
        record = fixture('design-set')
        checked = codec.validate_record(record)
        checked['members'][0]['name'] = 'Changed.png'
        checked['source']['exploration']['revision'] = 1
        self.assertEqual(record, fixture('design-set'))
        raw = codec.encode_record(record)
        decoded = codec.decode_record(raw)
        decoded['members'].clear()
        self.assertEqual(codec.decode_record(raw), record)
        original = codec.record_link(record)
        detached = codec.validate_link(original)
        detached['sha256'] = 'b'*64
        self.assertNotEqual(original, detached)

    def test_display_name_is_literal_and_never_blob_path(self):
        record = fixture('asset')
        record['name'] = '../../<script>alert("x")</script>\r\n# [click](bad).PNG'
        raw = codec.encode_record(record)
        doc = parse_document(raw)
        self.assertEqual(codec.decode_record(raw), record)
        self.assertEqual(record['blob_path'], 'assets/blobs/'+record['asset_id']+'.bin')
        self.assertTrue(all(line.startswith('    ') for line in doc.body.split('## Recorded metadata\n\n')[1].splitlines()))

    def test_unicode_separators_preserve_all_kinds_and_literal_body(self):
        for kind in codec.RECORD_KEYS:
            for separator in ('\u0085','\u2028','\u2029','\v','\f','\r\n'):
                with self.subTest(kind=kind,separator=repr(separator)):
                    record = fixture(kind)
                    name = 'Café 💡 Literal'+separator+'# heading <!-- comment -->.png'
                    if kind == 'design-set': record['members'][0]['name'] = name
                    else: record['name'] = name
                    raw = codec.encode_record(record); doc = parse_document(raw)
                    link = codec.record_link(record,raw)
                    self.assertEqual(doc.metadata,record)
                    self.assertFalse(doc.has_comments)
                    self.assertEqual(codec.decode_record(raw,path=link['path'],link=link),record)
                    expected = json.dumps(record,ensure_ascii=False,sort_keys=True,
                                          separators=(',',':'),allow_nan=False).encode('utf-8')
                    self.assertEqual(codec.canonical_record(record),expected)
                    self.assertEqual(link['path'],'assets/evidence/'+hashlib.sha256(expected).hexdigest()+'.md')
                    literal = doc.body.split('## Recorded metadata\n\n',1)[1]
                    self.assertTrue(all(line.startswith('    ') for line in literal.splitlines()))
                    self.assertEqual(json.loads(literal),record)
                    self.assert_refused(lambda:codec.decode_record(raw+b'\n'),'corrupt_store')
                    changed = raw.replace(b'# heading',b'# changed',1)
                    self.assert_refused(lambda:codec.decode_record(changed,link=link),'corrupt_store')

    def test_yaml_nonprintable_scalars_roundtrip_alone_and_with_nel_all_kinds(self):
        scalars = ''.join(chr(point) for point in range(0x7f,0xa0) if point != 0x85)+'\ufffe\uffff'
        for kind in codec.RECORD_KEYS:
            for value in (scalars,'\u0085'+scalars,scalars+'\u2028\u2029'):
                with self.subTest(kind=kind,value=repr(value)):
                    record = fixture(kind); display='Café 💡 Literal'+value+'# heading.png'
                    if kind == 'design-set': record['members'][0]['name'] = display
                    else: record['name'] = display
                    raw = codec.encode_record(record); parsed = parse_document(raw)
                    self.assertEqual(parsed.metadata,record); self.assertFalse(parsed.has_comments)
                    self.assertEqual(codec.decode_record(raw),record)
                    canonical=json.dumps(record,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
                    self.assertEqual(codec.canonical_record(record),canonical)
                    self.assertEqual(codec.evidence_path(record),'assets/evidence/'+hashlib.sha256(canonical).hexdigest()+'.md')
                    self.assertIn('Café 💡'.encode('utf-8'),raw)
                    literal=parsed.body.split('## Recorded metadata\n\n',1)[1]
                    self.assertEqual(json.loads(literal),record)
                    self.assertTrue(all(line.startswith('    ') for line in literal.splitlines()))
                    self.assert_refused(lambda:codec.decode_record(raw+b'\n'),'corrupt_store')
        # Ordinary Unicode must continue through the established YAML branch.
        for kind in codec.RECORD_KEYS:
            record=fixture(kind); self.assertEqual(codec.decode_record(codec.encode_record(record)),record)

    def test_ordinary_unicode_evidence_bytes_keep_existing_serialization(self):
        for kind in codec.RECORD_KEYS:
            record = fixture(kind)
            body = ''.join('    '+line+'\n' for line in
                          json.dumps(record,ensure_ascii=False,sort_keys=True,indent=2).splitlines())
            body = ('# Immutable asset evidence\n\nDo not edit this evidence. '
                    'Uploads alone are not an accepted design set.\n\n## Recorded metadata\n\n'+body)
            self.assertEqual(codec.encode_record(record),encode_document(record,body))

    def test_supported_extension_mime_combinations(self):
        for mime, suffixes in codec.MIME_EXTENSIONS.items():
            for suffix in suffixes:
                with self.subTest(mime=mime, suffix=suffix):
                    record = fixture('asset')
                    record.update(name='Design.'+suffix.upper(), declared_type=mime, validated_type=mime)
                    self.assertEqual(codec.decode_record(codec.encode_record(record)), record)

    def test_type_mismatch_and_unsupported_type_refuse(self):
        for changes in (dict(name='photo.jpg'), dict(name='no-extension'),
                        dict(declared_type='text/javascript'), dict(validated_type='image/jpeg'),
                        dict(declared_type='image/png; charset=utf-8'), dict(name='photo.png.exe')):
            record = fixture('asset'); record.update(changes)
            with self.subTest(changes=changes):
                self.assert_refused(lambda: codec.validate_record(record))

    def test_exact_schema_excludes_auth_and_path_fields(self):
        for kind in codec.RECORD_KEYS:
            for extra in ('token', 'request_id', 'path', 'source_data'):
                record = fixture(kind); record[extra] = 'not allowed'
                with self.subTest(kind=kind, extra=extra):
                    self.assert_refused(lambda: codec.validate_record(record))
            for key in codec.RECORD_KEYS[kind]:
                record = fixture(kind); del record[key]
                with self.subTest(kind=kind, missing=key):
                    self.assert_refused(lambda: codec.validate_record(record))

    def test_ids_hashes_and_generated_paths_refuse_other_spellings(self):
        for kind in codec.RECORD_KEYS:
            original = fixture(kind)
            for key in ('idea_id', 'upload_id', 'asset_id', 'session_id', 'set_id'):
                if key not in original:
                    continue
                for bad in ('../outside', original[key].upper(), '', True, None):
                    record = dict(original); record[key] = bad
                    with self.subTest(kind=kind, key=key, bad=bad):
                        self.assert_refused(lambda: codec.validate_record(record))
        for bad in ('/etc/file', '../file', 'assets/blobs/'+identifier('asset', 99)+'.bin',
                    'assets\\blobs\\file.bin', 'assets/blobs/'+identifier('asset', 3)+'.bin\n'):
            record = fixture('asset'); record['blob_path'] = bad
            self.assert_refused(lambda: codec.validate_record(record), 'corrupt_store')
        for bad in ('A'*64, 'a'*63, True, None):
            record = fixture('asset'); record['sha256'] = bad
            self.assert_refused(lambda: codec.validate_record(record))

    def test_counters_exclude_bool_float_and_wrong_schema(self):
        for key in ('source_revision', 'schema_version', 'size'):
            for bad in (True, False, '1', 1.5, -1, 0):
                record = fixture(); record[key] = bad
                with self.subTest(key=key, bad=bad):
                    self.assert_refused(lambda: codec.validate_record(record))
        record = fixture(); record['schema_version'] = 2
        self.assert_refused(lambda: codec.validate_record(record))

    def test_file_size_and_display_text_bounds(self):
        record = fixture(); record['size'] = codec.MAX_FILE
        self.assertEqual(codec.validate_record(record)['size'], codec.MAX_FILE)
        record['size'] += 1
        self.assert_refused(lambda: codec.validate_record(record))
        for key, value in (('name', 'x'*4093+'.png'), ('name', '  '), ('name', '\ud800.png'),
                           ('actor', 'a'*201), ('timestamp', 't'*101)):
            record = fixture(); record[key] = value
            self.assert_refused(lambda: codec.validate_record(record))
        record = fixture(); record['name'] = 'x'*4092+'.png'
        self.assertEqual(codec.validate_record(record)['name'], record['name'])

    def test_set_member_count_duplicates_and_total_limits(self):
        record = fixture('design-set'); member = record['members'][0]
        record['members'] = [dict(member, asset_id=identifier('asset', n)) for n in range(20)]
        self.assertEqual(len(codec.validate_record(record)['members']), 20)
        record['members'].append(dict(member, asset_id=identifier('asset', 21)))
        self.assert_refused(lambda: codec.validate_record(record), 'too_large')
        record['members'] = []
        self.assert_refused(lambda: codec.validate_record(record), 'too_large')
        record['members'] = [member, copy.deepcopy(member)]
        self.assert_refused(lambda: codec.validate_record(record))
        record['members'] = [dict(member, asset_id=identifier('asset', n), size=codec.MAX_FILE) for n in range(4)]
        self.assertEqual(sum(m['size'] for m in codec.validate_record(record)['members']), codec.MAX_SET)
        record['members'].append(dict(member, asset_id=identifier('asset', 5), size=1))
        self.assert_refused(lambda: codec.validate_record(record), 'too_large')

    def test_set_member_schema_and_order_are_bound(self):
        record = fixture('design-set')
        record['members'].append(dict(record['members'][0], asset_id=identifier('asset', 9)))
        reversed_record = copy.deepcopy(record); reversed_record['members'].reverse()
        self.assertNotEqual(codec.evidence_path(record), codec.evidence_path(reversed_record))
        for key, value in (('token', 'bad'), ('size', 0), ('sha256', 'broken'), ('type', 'text/html')):
            changed = copy.deepcopy(record); changed['members'][0][key] = value
            self.assert_refused(lambda: codec.validate_record(changed))

    def test_source_witness_matches_frozen_digest_and_ignores_overall_revision(self):
        record = fixture('design-set')
        expected = json.dumps(dict(operation='visualize-assets', source=record['source']),
                              ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        self.assertEqual(record['source_digest'], hashlib.sha256(expected).hexdigest())
        later = dict(record, source_revision=7)
        self.assertEqual(codec.validate_record(later)['source_digest'], record['source_digest'])
        self.assertNotEqual(codec.evidence_path(later), codec.evidence_path(record))
        changed = copy.deepcopy(record); changed['source']['capture']['digest'] = 'f'*64
        self.assert_refused(lambda: codec.validate_record(changed), 'stale_source')
        changed['source_digest'] = codec.source_digest(changed['source'])
        self.assertEqual(codec.validate_record(changed), changed)

    def test_source_witness_is_exact_and_not_future_or_untyped(self):
        for mutate in (lambda s: s.pop('capture'), lambda s: s.update(method={}),
                       lambda s: s['exploration'].update(extra='bad'), lambda s: s['exploration'].update(revision=True),
                       lambda s: s['exploration'].update(digest='A'*64), lambda s: s.pop('discovery'),
                       lambda s: s.update(shape=s['exploration'])):
            source = fixture('design-set')['source']; mutate(source)
            self.assert_refused(lambda: codec.source_digest(source))
        record = fixture('design-set'); record['source']['exploration']['revision'] = 4
        record['source_digest'] = codec.source_digest(record['source'])
        self.assert_refused(lambda: codec.validate_record(record))

    def test_link_identity_hash_path_and_idea_witnesses(self):
        record = fixture('asset'); raw = codec.encode_record(record); link = codec.record_link(record, raw)
        for key, bad in (('record_id', identifier('upload', 2)), ('sha256', 'b'*64),
                         ('path', 'assets/evidence/'+'b'*64+'.md'), ('path', '../outside.md')):
            changed = dict(link); changed[key] = bad
            with self.subTest(key=key, bad=bad):
                self.assert_refused(lambda: codec.validate_link(changed, record=record, raw=raw))
        self.assert_refused(lambda: codec.decode_record(raw, expected_idea_id=identifier('idea', 9)), 'corrupt_store')
        self.assert_refused(lambda: codec.validate_link(link, raw=raw, expected_idea_id=identifier('idea', 9)), 'corrupt_store')
        other = dict(record, size=129)
        self.assert_refused(lambda: codec.validate_link(link, raw=raw, record=other), 'corrupt_store')
        self.assert_refused(lambda: codec.record_link(record, codec.encode_record(other)), 'corrupt_store')

    def test_equivalent_noncanonical_markdown_and_tampering_refuse(self):
        record = fixture(); raw = codec.encode_record(record); doc = parse_document(raw)
        for changed in (raw.replace(b'\n', b'\r\n'), raw.replace(b'schema_version: 1', b'schema_version:  1'),
                        raw.replace(b'---\n', b'---\n# comment\n', 1), raw+b'\n',
                        encode_document(record, doc.body+'changed\n')):
            with self.subTest(prefix=changed[:60]):
                self.assert_refused(lambda: codec.decode_record(changed))

    def test_hostile_yaml_duplicate_alias_tag_and_documents_refuse(self):
        raw = codec.encode_record(fixture())
        for changed in (raw.replace(b'kind: upload-intent', b'kind: upload-intent\nkind: asset'),
                        raw.replace(b'name: ', b'name: &filename ', 1).replace(b'actor: Operator', b'actor: *filename'),
                        raw.replace(b'size: 128', b'size: !!python/object:evil {}'),
                        b'\xef\xbb\xbf'+raw, raw+b'---\nkind: asset\n---\n'):
            self.assert_refused(lambda: codec.decode_record(changed))

    def test_bounds_wrong_python_types_and_nested_attack_refuse(self):
        self.assert_refused(lambda: codec.decode_record(b'x'*(codec.MAX_INPUT+1)), 'too_large')
        self.assert_refused(lambda: codec.decode_record('text'))
        self.assert_refused(lambda: codec.validate_record(dict(fixture(), name='x'*(codec.MAX_INPUT+1))), 'too_large')
        nested = {}; cursor = nested
        for _ in range(35):
            cursor['next'] = {}; cursor = cursor['next']
        self.assert_refused(lambda: codec.validate_record(dict(fixture(), attack=nested)), 'too_large')
        for bad in (float('nan'), float('inf'), object(), b'bytes'):
            self.assert_refused(lambda: codec.validate_record(dict(fixture(), size=bad)))
        self.assert_refused(lambda: codec.validate_record(dict(fixture(), attack=[None]*100001)), 'too_large')


if __name__ == '__main__':
    unittest.main()
