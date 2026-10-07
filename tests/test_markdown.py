"""Markdown file-input and lossless evidence tests. """
import copy
import json
from pathlib import Path
import sys
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import idea_markdown as md
from idea_domain import IdeaError, digest, snapshot

KEY = 'idea_' + '0' * 31 + '1'


def fixture():
    raw = '  Café 💡 # original\r\n\r\nlast line\n'
    idea = dict(idea_id=KEY, revision=1, status='active', origin=dict(text=raw, sha256=digest(raw.encode()), actor='operator', timestamp='2026-10-01T00:00:00Z'),
                shape=None, ratings=None, assessments=[], revisions=[], proposals=[], plans=[], executions=[])
    idea['revisions'] = [dict(revision=1, shape=None, ratings=None, assessments=[], actor='operator', action='capture', timestamp='2026-10-01T00:00:00Z')]
    return dict(schema_version=1, transaction_revision=1, backlog_revision=1, order=[KEY], ideas={KEY:idea}, placements=[], archives={})


def metadata_fixture():
    state = fixture()
    idea = state['ideas'][KEY]
    proposal = dict(idea_id=KEY, idea_revision=1, position=1, reason='Fixture recommendation',
                    actor='Operator', timestamp='2026-10-01', source_backlog_revision=1,
                    neighbors={'before': None, 'after': None}, snapshot={'ratings': None, 'assessments': []},
                    proposal_id='proposal_'+'2'*32)
    idea['proposals'] = [proposal, dict(proposal, proposal_id='proposal_'+'3'*32, reason='Second recommendation')]
    pid = 'plan_'+'1'*32
    content = '# Accepted fixture plan\r\n'
    idea['plans'] = [dict(plan_id=pid, idea_id=KEY, idea_revision=1, path='/example/plan-evidence/'+pid+'.md',
                         source_path='/example/working.md', sha256=digest(content.encode()), content=content,
                         actor='Operator', timestamp='2026-10-01', validation={'builtin':'idea-trace-and-sections-v1'})]
    receipt = dict(idea_id=KEY, plan_id=pid, attempt_id='fixture-attempt', status='succeeded', evidence=['Observed fixture result'])
    idea['executions'] = [dict(idea_id=KEY, plan_id=pid, attempt_id=receipt['attempt_id'], status=receipt['status'],
                              receipt=receipt, path='/example/receipt.json', sha256=digest(json.dumps(receipt).encode()),
                              actor='Operator', timestamp='2026-10-01')]
    idea['status'] = 'archived'
    state['archives'][KEY+'/r1.json'] = dict(idea_id=KEY, origin=copy.deepcopy(idea['origin']), revision=copy.deepcopy(idea['revisions'][0]))
    return state


class MarkdownTests(unittest.TestCase):
    def rejected(self, front, code=None):
        with self.assertRaises(IdeaError) as caught:
            md.parse_document(('---\n' + front + '\n---\nbody').encode())
        if code:
            self.assertEqual(caught.exception.code, code)

    def test_hostile_yaml_rejected_at_boundary(self):
        for front in ('a: 1\na: 2', 'a: &x [1]\nb: *x', 'a: {<<: {x: 1}}',
                      'a: !!python/object:evil {}', 'a: !!timestamp 2026-10-01',
                      'a: !!set {one: null}', '1: text', '? [key]\n: text',
                      'a: .nan', 'a: .inf', 'a: -.Inf', 'a: [broken',
                      'a: 1\n...\n---\nb: 2', '- not-a-mapping'):
            with self.subTest(front=front):
                self.rejected(front)

    def test_limits_before_unbounded_construction(self):
        self.rejected('a: ' + '[' * 35 + '0' + ']' * 35, 'too_large')
        with patch.object(md, 'MAX_NODES', 12):
            self.rejected('a: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]', 'too_large')
        with patch.object(md, 'MAX_FRONTMATTER', 10):
            self.rejected('text: abcdefghijklmnop', 'too_large')
        with patch.object(md, 'MAX_DOCUMENT', 10):
            self.rejected('a: 1', 'too_large')
        with self.assertRaises(IdeaError):
            md.encode_document({'a': float('inf')})
        recursive = []; recursive.append(recursive)
        with self.assertRaises(IdeaError) as caught:
            md.encode_document({'a': recursive})
        self.assertEqual(caught.exception.code, 'too_large')

    def test_utf8_frontmatter_and_actual_types(self):
        for raw in (b'\xef\xbb\xbf---\na: 1\n---\n', b'---\na: \xff\n---\n', b'no delimiter', b'---\na: 1'):
            with self.assertRaises(IdeaError):
                md.parse_document(raw)
        doc = md.parse_document(b'---\ndate: 2026-10-01\nboolean: "true"\n---\n')
        self.assertEqual(doc.metadata['date'], '2026-10-01')
        self.assertEqual(doc.metadata['boolean'], 'true')
        files = md.encode_state(fixture())
        files['IDEAS.md'] = files['IDEAS.md'].replace(b'backlog_revision: 1', b'backlog_revision: true')
        with self.assertRaises(IdeaError):
            md.decode_state(files)

    def test_comment_tokens_distinguished_from_scalar_hashes(self):
        cases = [('a: "# quoted"', False), ("a: '# quoted'", False),
                 ('a: abc#literal', False), ('a: |\n  # block content\n  next', False),
                 ('a: >\n  words # in block', False), ('a: value # comment', True),
                 ('# full comment\na: 1', True), ('a: | # header comment\n  content', True),
                 ('a: |\n  content\n# after scalar', True)]
        for front, expected in cases:
            with self.subTest(front=front):
                raw = ('---\n' + front + '\n---\n').encode()
                doc = md.parse_document(raw)
                self.assertEqual(doc.has_comments, expected)
                if expected:
                    with self.assertRaises(IdeaError) as caught:
                        md.encode_document(doc.metadata, doc.body, previous=doc)
                    self.assertEqual(caught.exception.code, 'yaml_comments')
                else:
                    self.assertEqual(md.parse_document(md.encode_document(doc.metadata)).metadata, doc.metadata)

    def test_deterministic_state_origin_and_history_roundtrip(self):
        state = fixture()
        files = md.encode_state(state)
        self.assertEqual(files, md.encode_state(copy.deepcopy(state)))
        self.assertEqual(md.decode_state(files), state)
        for path, raw in files.items():
            self.assertTrue(raw.startswith(b'---\n'), path)
            self.assertIsInstance(md.parse_document(raw).metadata, dict)
        origin = md.decode_state(files)['ideas'][KEY]['origin']
        self.assertEqual(digest(origin['text'].encode()), origin['sha256'])
        self.assertIn('\r\n', origin['text'])
        self.assertTrue(origin['text'].endswith('\n'))

    def test_every_trailing_newline_and_unicode_variant_roundtrips(self):
        for value in ('words', 'words\n', 'words\n\n', 'words\r\n', 'é\t💡\r\n\n'):
            state = fixture(); state['ideas'][KEY]['origin'].update(text=value, sha256=digest(value.encode()))
            self.assertEqual(md.decode_state(md.encode_state(state)), state)

    def test_notes_extensions_and_comments_protected_on_rewrite(self):
        state = fixture()
        notes = 'A private note\r\n\n# heading\nno final newline'
        files = md.encode_state(state, notes={KEY: notes}, extensions={KEY: {'custom': {'enabled':True, 'labels':['one']}}})
        doc = md.decode_detail(files[KEY + '.md'])
        self.assertEqual(md.detail_notes(doc), notes)
        idea = copy.deepcopy(state['ideas'][KEY])
        rewritten = md.decode_detail(md.encode_detail(idea, previous=doc))
        self.assertEqual(md.detail_notes(rewritten), notes)
        self.assertEqual(rewritten.metadata['extensions'], doc.metadata['extensions'])
        commented = files[KEY + '.md'].replace(b'kind: idea', b'kind: idea # retained comment')
        doc = md.decode_detail(commented)
        with self.assertRaises(IdeaError) as caught:
            md.encode_detail(idea, previous=doc)
        self.assertEqual(caught.exception.code, 'yaml_comments')
        self.assertIn(b'# retained comment', commented)

    def test_generated_prose_and_tables_never_silently_lost(self):
        files = md.encode_state(fixture())
        changed = dict(files)
        changed[KEY + '.md'] = files[KEY + '.md'].replace(b'## Original wording', b'## Edited summary')
        with self.assertRaises(IdeaError) as caught:
            md.decode_state(changed)
        self.assertEqual(caught.exception.code, 'generated_body_conflict')
        doc = md.decode_detail(changed[KEY + '.md'], check_body=False)
        with self.assertRaises(IdeaError):
            md.encode_detail(fixture()['ideas'][KEY], previous=doc)
        changed = dict(files); changed['IDEAS.md'] = files['IDEAS.md'] + b'Extra prose\n'
        with self.assertRaises(IdeaError) as caught:
            md.decode_state(changed)
        self.assertEqual(caught.exception.code, 'generated_body_conflict')

    def test_missing_changed_and_cross_idea_history_fail_closed(self):
        files = md.encode_state(fixture())
        path = 'history/' + KEY + '/r1.md'
        for change in (None, files[path] + b'changed'):
            changed = dict(files)
            if change is None: del changed[path]
            else: changed[path] = change
            with self.assertRaises(IdeaError): md.decode_state(changed)
        self.assertNotIn('revisions', md.decode_detail(files[KEY + '.md']).metadata['idea'])
        with self.assertRaises(IdeaError): md.decode_state({})

    def test_links_and_untrusted_text_are_inert(self):
        escaped = md.markdown_link('bad ](<script>) | *text*', 'folder/a b)#.md')
        self.assertIn('a%20b%29%23.md', escaped)
        self.assertNotIn('<script>', escaped)
        self.assertIn('&#124;', escaped)
        for target in ('../../x', '/etc/passwd', 'javascript:evil', 'a\\b', 'a//b'):
            with self.assertRaises(IdeaError): md.markdown_link('x', target)

    def test_current_field_edits_are_candidates_not_accepted_history(self):
        files = md.encode_state(fixture())
        doc = md.decode_detail(files[KEY + '.md'])
        meta = copy.deepcopy(doc.metadata)
        meta['idea']['shape'] = dict(outcome='candidate', scope='small-change', scope_reason='one thing', alternatives=[dict(route='reuse',reason='already works')], method='bounded-plan',method_reason='bounded',assumptions=[],next_slice='one check',learning=[])
        files[KEY + '.md'] = md.encode_document(meta, doc.body)
        candidate = md.decode_state(files, check_body=False)
        self.assertEqual(candidate['ideas'][KEY]['shape']['outcome'], 'candidate')
        self.assertIsNone(candidate['ideas'][KEY]['revisions'][0]['shape'])
        with self.assertRaises(IdeaError): md.decode_state(files)

    def test_legacy_plan_content_and_archives_preserved(self):
        state = fixture(); idea = state['ideas'][KEY]
        pid = 'plan_' + '1' * 32
        content = '# Accepted plan\r\n\nExact trailing newline\n'
        plan = dict(plan_id=pid,idea_id=KEY,idea_revision=1,path='/example/plan-evidence/'+pid+'.md',source_path='/example/working.md',content=content,sha256=digest(content.encode()),actor='operator',timestamp='2026-10-01',validation={'builtin':'idea-trace-and-sections-v1','external':None,'timestamp':'2026-10-01'})
        idea['plans'] = [plan]; idea['status'] = 'archived'
        state['archives'][KEY+'/r1.json'] = dict(idea_id=KEY,origin=copy.deepcopy(idea['origin']),revision=copy.deepcopy(idea['revisions'][0]))
        files = md.encode_state(state)
        self.assertEqual(files['plan-evidence/'+pid+'.md'], content.encode())
        self.assertEqual(md.decode_state(files), state)
        self.assertNotIn('content', md.decode_detail(files[KEY+'.md']).metadata['idea']['plans'][0])

    def test_external_import_checks_body_against_explicit_accepted_baseline(self):
        state = fixture(); original = md.encode_state(state)
        doc = md.decode_detail(original[KEY + '.md'])
        metadata = copy.deepcopy(doc.metadata)
        candidate_shape = dict(outcome='candidate', scope='small-change', scope_reason='one thing', alternatives=[dict(route='reuse',reason='already works')], method='bounded-plan',method_reason='bounded',assumptions=[],next_slice='one check',learning=[])
        metadata['idea']['shape'] = candidate_shape
        changed = dict(original)
        changed[KEY+'.md'] = md.encode_document(metadata, doc.body)
        candidate = md.decode_state(changed, check_body=False)
        edited = md.decode_detail(changed[KEY+'.md'], check_body=False)
        self.assertEqual(md.detail_notes(edited, baseline=doc.metadata), '')
        candidate['ideas'][KEY]['revision'] = 2
        prior = copy.deepcopy(candidate['ideas'][KEY]['revisions'][0]); prior.update(revision=2,shape=candidate_shape,action='external-file')
        candidate['ideas'][KEY]['revisions'].append(prior)
        previous = {name:md.parse_document(raw) for name,raw in changed.items() if name == 'IDEAS.md' or name == KEY+'.md'}
        rewritten = md.encode_state(candidate, previous=previous, previous_state=state)
        self.assertEqual(md.decode_state(rewritten), candidate)
        previous[KEY+'.md'] = md.Document(edited.metadata, edited.body.replace('## Original wording','## Changed prose'))
        with self.assertRaises(IdeaError) as caught:
            md.encode_state(candidate, previous=previous, previous_state=state)
        self.assertEqual(caught.exception.code, 'generated_body_conflict')

    def test_forged_origin_even_with_matching_hash_refused(self):
        files = md.encode_state(fixture())
        doc = md.decode_detail(files[KEY+'.md'])
        metadata = copy.deepcopy(doc.metadata)
        metadata['idea']['origin'].update(text='changed origin', sha256=digest(b'changed origin'))
        files[KEY+'.md'] = md.encode_document(metadata, doc.body)
        with self.assertRaises(IdeaError) as caught:
            md.decode_state(files, check_body=False)
        self.assertEqual(caught.exception.code, 'corrupt_store')

    def test_placement_and_proposal_evidence_roundtrip(self):
        state = fixture(); idea=state['ideas'][KEY]
        placement = dict(idea_id=KEY,idea_revision=1,position=1,reason='operator choice',actor='operator',timestamp='2026-10-01',source_backlog_revision=1,neighbors={'before':None,'after':None},snapshot={'ratings':None,'assessments':[]})
        proposal=dict(placement,proposal_id='proposal_'+'2'*32)
        idea['proposals']=[proposal]
        state['placements']=[dict(placement,accepted_backlog_revision=2)]
        state['backlog_revision']=2
        files=md.encode_state(state)
        self.assertTrue(files['history/backlog/r1.md'].startswith(b'---'))
        self.assertEqual(md.decode_state(files), state)

    def test_workflow_fields_remain_separate_from_legacy_snapshot(self):
        from idea_workflow import empty_workflow
        state=fixture(); idea=state['ideas'][KEY]
        idea['workflow']=empty_workflow()
        self.assertEqual(md.decode_state(md.encode_state(state)), state)
        self.assertNotIn('workflow', idea['revisions'][0])

    def test_malformed_envelopes_and_extensions_fail_with_domain_error(self):
        state = fixture()
        with self.assertRaises(IdeaError):
            md.encode_detail(state['ideas'][KEY], extensions=[])
        for bad in (None, 1, 'snapshot'):
            raw = md.encode_document({'schema_version':2,'kind':'history','idea_id':KEY,'origin':state['ideas'][KEY]['origin'],'snapshot':bad})
            with self.assertRaises(IdeaError):
                md.decode_history(raw)
        state['schema_version'] = True
        with self.assertRaises(IdeaError):
            md.encode_state(state)

    def test_fixture_documents_match_codec(self):
        root = Path(__file__).parent / 'fixtures/markdown'
        files = {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*.md')}
        self.assertEqual(md.decode_state(files), fixture())

    def test_metadata_evidence_is_deterministic_content_addressed_and_yaml_at_zero(self):
        state = metadata_fixture()
        files = md.encode_state(state)
        self.assertEqual(files, md.encode_state(copy.deepcopy(state)))
        self.assertEqual(md.decode_state(files), state)
        doc = md.decode_detail(files[KEY+'.md'])
        for field, kind in md.METADATA_TYPES.items():
            records = doc.metadata['idea'][field]
            links = doc.metadata['metadata_evidence'][field]
            self.assertEqual(len(records), len(links))
            for record, link in zip(records, links):
                payload = {'idea_id': KEY, 'record_type': kind, 'record': record}
                canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
                expected = 'history/'+KEY+'/metadata/'+digest(canonical)+'.md'
                self.assertEqual(link['path'], expected)
                self.assertEqual(link['sha256'], digest(files[expected]))
                self.assertTrue(files[expected].startswith(b'---\n'))
                evidence = md.decode_metadata(files[expected], path=expected)
                self.assertEqual(evidence.metadata['record'], record)
                self.assertEqual(evidence.metadata['record_type'], kind)
                self.assertEqual(md.encode_metadata(KEY, kind, record), files[expected])
                if kind == 'plan':
                    self.assertNotIn('content', record)

    def test_protected_record_edits_rejected_without_rewriting_origin_or_revisions(self):
        original = md.encode_state(metadata_fixture())
        doc = md.decode_detail(original[KEY+'.md'])
        for field in ('proposals', 'plans', 'executions'):
            changed = dict(original)
            meta = copy.deepcopy(doc.metadata)
            meta['idea'][field][0]['actor'] = 'Forged attribution'
            changed[KEY+'.md'] = md.encode_document(meta, doc.body)
            self.assertEqual(meta['idea']['origin'], doc.metadata['idea']['origin'])
            self.assertEqual(meta['history'], doc.metadata['history'])
            with self.subTest(field=field), self.assertRaises(IdeaError) as caught:
                md.decode_state(changed, check_body=False)
            self.assertEqual(caught.exception.code, 'corrupt_store')

    def test_metadata_links_missing_reordered_wrongtype_and_missing_bytes_rejected(self):
        original = md.encode_state(metadata_fixture())
        doc = md.decode_detail(original[KEY+'.md'])
        edits = [lambda meta: meta['metadata_evidence']['proposals'].pop(),
                 lambda meta: meta['metadata_evidence']['proposals'].reverse(),
                 lambda meta: meta['metadata_evidence'].__setitem__('plans', {}),
                 lambda meta: meta['metadata_evidence'].__setitem__('executions', meta['metadata_evidence']['plans']),
                 lambda meta: meta.pop('metadata_evidence')]
        for edit in edits:
            meta = copy.deepcopy(doc.metadata)
            edit(meta)
            changed = dict(original)
            changed[KEY+'.md'] = md.encode_document(meta, doc.body)
            with self.assertRaises(IdeaError):
                md.decode_state(changed, check_body=False)
        path = doc.metadata['metadata_evidence']['plans'][0]['path']
        changed = dict(original)
        del changed[path]
        with self.assertRaises(IdeaError) as caught:
            md.decode_state(changed, check_body=False)
        self.assertEqual(caught.exception.code, 'corrupt_store')

    def test_metadata_hash_envelope_identity_type_and_filename_digest_rejected(self):
        original = md.encode_state(metadata_fixture())
        doc = md.decode_detail(original[KEY+'.md'])
        path = doc.metadata['metadata_evidence']['proposals'][0]['path']
        changed = dict(original)
        changed[path] += b'changed bytes'
        with self.assertRaises(IdeaError):
            md.decode_state(changed, check_body=False)
        evidence = md.decode_metadata(original[path])
        for updates in ({'kind': 'history'}, {'record_type': 'plan'}, {'idea_id': 'idea_'+'4'*32}, {'schema_version': True}):
            meta = dict(evidence.metadata, **updates)
            raw = md.encode_document(meta, evidence.body)
            with self.assertRaises(IdeaError):
                md.decode_metadata(raw, path=path)
        wrong_path = 'history/'+KEY+'/metadata/'+'0'*64+'.md'
        with self.assertRaises(IdeaError):
            md.decode_metadata(original[path], path=wrong_path)
        # Changing both file bytes and link hash still cannot move altered
        # protected records under the old content-addressed filename.
        meta = copy.deepcopy(evidence.metadata)
        meta['record']['actor'] = 'Changed'
        raw = md.encode_document(meta, evidence.body)
        changed = dict(original)
        changed[path] = raw
        detail = copy.deepcopy(doc.metadata)
        detail['metadata_evidence']['proposals'][0]['sha256'] = digest(raw)
        changed[KEY+'.md'] = md.encode_document(detail, doc.body)
        with self.assertRaises(IdeaError) as caught:
            md.decode_state(changed, check_body=False)
        self.assertEqual(caught.exception.code, 'corrupt_store')

    def test_real_cli_proposal_plan_execution_metadata_and_archive_roundtrip(self):
        source = Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(source, root/'helper/scripts')
            (root/'helper/config.json').write_text(json.dumps({'plan_validator_argv': None}))
            script = root/'helper/scripts/idea.py'
            store = root/'ideas'
            def cli(*arguments):
                process = subprocess.run([sys.executable, str(script), '--store', str(store), *map(str, arguments)], capture_output=True)
                data = json.loads(process.stdout)
                self.assertEqual(process.returncode, 0, data)
                return data
            words = root/'words.txt'
            words.write_text('CLI fixture only\n')
            idea = cli('capture', '--text-file', words, '--actor', 'operator')['idea']
            key = idea['idea_id']
            shape_file = root/'shape.json'
            shape_file.write_text(json.dumps(dict(outcome='Observed fixture', scope='small-change', scope_reason='One test',
                alternatives=[dict(route='Reuse', reason='Simpler')],
                assumptions=[], next_slice='One fixture check', learning=[], investment=None, experiment=None,
                sketch=[dict(title='Fixture slice', why_next='Smallest check', done_when='Fixture observed', method='bounded-plan')])))
            cli('exploration', key, '--file', shape_file, '--expected-revision', 1, '--actor', 'Operator')
            cli('rate', key, '--urgency', 7, '--importance', 8, '--expected-revision', 2, '--actor', 'operator')
            assessed = root/'assessment.json'
            assessed.write_text(json.dumps(dict(method='wsjf', version='fixture-v1', inputs=dict(value=3, time_criticality=None, enablement=2, effort=1),
                basis='Fixture estimates', assumptions=[], confidence='low', provenance='Current-agent fixture')))
            cli('assess', key, '--file', assessed, '--expected-revision', 3, '--actor', 'Operator')
            cli('propose', key, '--position', 1, '--reason', 'Fixture suggestion', '--expected-backlog-revision', 1, '--actor', 'Operator')
            plan_file = root/'working.md'
            plan_file.write_text('# Fixture plan\n\n## Goal\nFixture.\n\n## Tasks\nOne check.\n\n## Validation\nObserve.\n\n## Idea trace\nidea_id: '+key+'\nidea_revision: 4\n')
            plan = cli('register-plan', key, '--path', plan_file, '--expected-revision', 4, '--actor', 'Operator')['plan']
            execution_file = root/'receipt.json'
            execution_file.write_text(json.dumps(dict(idea_id=key, plan_id=plan['plan_id'], attempt_id='real-fixture', status='succeeded', evidence=['Observed fixture result'])))
            cli('record-execution', key, '--plan-id', plan['plan_id'], '--path', execution_file, '--actor', 'Operator')
            from idea_store import Store
            with Store(store).transaction() as persisted:
                state = copy.deepcopy(persisted)
            self.assertEqual(state['ideas'][key]['revision'], 4)
            files = md.encode_state(state)
            self.assertEqual(md.decode_state(files), state)
            detail = md.decode_detail(files[key+'.md'])
            self.assertEqual({k:len(v) for k,v in detail.metadata['metadata_evidence'].items()}, {'proposals':1, 'plans':1, 'executions':1})
            self.assertEqual(files['plan-evidence/'+plan['plan_id']+'.md'], Path(plan['path']).read_bytes())

    def test_detail_transaction_counter_recovers_without_rewriting_index(self):
        from idea_workflow import empty_workflow
        state = fixture()
        state['ideas'][KEY]['workflow'] = empty_workflow()
        original = md.encode_state(state)
        previous = {name:md.parse_document(raw) for name,raw in original.items() if name in ('IDEAS.md', KEY+'.md')}
        changed = copy.deepcopy(state)
        changed['transaction_revision'] = 2
        changed['ideas'][KEY]['workflow']['draft_version'] = 1
        changed['ideas'][KEY]['workflow']['drafts']['priorities'] = {'urgency':7, 'importance':None}
        files = md.encode_state(changed, previous=previous, previous_state=state)
        self.assertEqual(md.decode_detail(files[KEY+'.md']).metadata['transaction_revision'], 2)
        files['IDEAS.md'] = original['IDEAS.md']  # Store's detail-only publication.
        self.assertEqual(md.decode_state(files), changed)
        self.assertEqual(md.decode_detail(md.encode_detail(changed['ideas'][KEY], previous=md.decode_detail(files[KEY+'.md']))).metadata['transaction_revision'], 2)

    def test_unchanged_details_keep_counters_and_new_details_use_state_counter(self):
        state = fixture()
        first = md.encode_state(state)
        previous = {name:md.parse_document(raw) for name,raw in first.items() if name in ('IDEAS.md', KEY+'.md')}
        changed = copy.deepcopy(state)
        changed['transaction_revision'] = 2
        files = md.encode_state(changed, previous=previous, previous_state=state)
        self.assertEqual(files[KEY+'.md'], first[KEY+'.md'])
        self.assertEqual(md.decode_state(files), changed)
        self.assertEqual(md.decode_detail(md.encode_state(changed)[KEY+'.md']).metadata['transaction_revision'], 2)
        self.assertEqual(md.decode_detail(md.encode_detail(state['ideas'][KEY])).metadata['transaction_revision'], 0)
        for bad in (True, -1, 1.0, '2'):
            with self.assertRaises(IdeaError):
                md.encode_detail(state['ideas'][KEY], transaction_revision=bad)

    def test_extension_boolean_to_integer_change_advances_detail_counter(self):
        state = fixture()
        first = md.encode_state(state, extensions={KEY:{'flag':True}})
        previous = {name:md.parse_document(raw) for name,raw in first.items() if name in ('IDEAS.md', KEY+'.md')}
        changed = copy.deepcopy(state)
        changed['transaction_revision'] = 2
        files = md.encode_state(changed, extensions={KEY:{'flag':1}}, previous=previous, previous_state=state)
        detail = md.decode_detail(files[KEY+'.md'])
        self.assertEqual(detail.metadata['transaction_revision'], 2)
        self.assertIs(type(detail.metadata['extensions']['flag']), int)


def v3_state():
    """A complete accepted v3 idea (Discovery, Exploration, Methods) as a domain state."""
    from test_handoff_evidence import fixture as accepted_fixture
    state, idea, _, _ = accepted_fixture(method=dict(selection='appetite-led', reason=None),
        exploration=dict(investment=dict(cap=3, unit='days', boundary='One page'),
                         sketch=[dict(title='Check the lid', why_next='Cheapest test', done_when='Lid is checked', method='experiment-led')]))
    state = copy.deepcopy(state); state['schema_version'] = 1; state['archives'] = {}
    for key in ('proposals', 'plans', 'executions'):
        state['ideas'][idea['idea_id']].setdefault(key, [])
    return state, idea['idea_id']


class WorkflowV3MarkdownTests(unittest.TestCase):
    def test_v3_idea_round_trips_with_no_generated_body_conflict(self):
        state, key = v3_state()
        files = md.encode_state(state)
        self.assertEqual(md.decode_state(files), state)
        doc = md.decode_detail(files[key + '.md'])
        self.assertEqual(md.detail_notes(doc), '')
        again = md.encode_state(state, previous={name: md.parse_document(raw) for name, raw in files.items() if name in (key + '.md', 'IDEAS.md')},
                                previous_state=state)
        self.assertEqual(again[key + '.md'], files[key + '.md'])

    def test_detail_names_discovery_exploration_sketch_and_methods(self):
        state, key = v3_state()
        body = md.decode_detail(md.encode_state(state)[key + '.md']).body
        for heading in ('### Discovery', '### Exploration', '### Methods'):
            self.assertIn(heading, body)
        self.assertIn('Lids are hard to clean', body)
        self.assertIn('1. Check the lid (Experiment First) - why next: Cheapest test; done when: Lid is checked', body)
        self.assertIn('- Method: Fixed Budget, Build what Fits', body)
        self.assertNotIn('Why this method', body)
        self.assertNotIn('### Shape', body)

    def test_method_reason_is_shown_when_given(self):
        state, key = v3_state()
        state['ideas'][key]['workflow']['steps']['method']['fields']['reason'] = 'Known change'
        self.assertIn('- Why this method: Known change', md.decode_detail(md.encode_state(state)[key + '.md']).body)

    def test_an_edit_to_a_section_source_without_regeneration_is_refused(self):
        state, key = v3_state()
        files = md.encode_state(state)
        doc = md.decode_detail(files[key + '.md'])
        meta = copy.deepcopy(doc.metadata)
        meta['idea']['workflow']['steps']['discovery']['fields']['problem'] = 'Changed in the file'
        files[key + '.md'] = md.encode_document(meta, doc.body)
        with self.assertRaises(IdeaError) as caught:
            md.decode_state(files)
        self.assertEqual(caught.exception.code, 'generated_body_conflict')

    def test_snapshot_version_is_three_and_older_is_refused(self):
        from idea_workflow import WORKFLOW_VERSION
        state, key = v3_state()
        self.assertEqual(WORKFLOW_VERSION, 3)
        snap = state['ideas'][key]['revisions'][-1]
        self.assertEqual(snap.get('schema_version'), 3)
        old = copy.deepcopy(snap); old['schema_version'] = 2
        with self.assertRaises(IdeaError):
            md.encode_history(key, old, origin=state['ideas'][key]['origin'])

    def test_old_snapshot_version_is_refused_with_the_typed_code(self):
        state, key = v3_state()
        old = copy.deepcopy(state['ideas'][key]['revisions'][-1]); old['schema_version'] = 2
        with self.assertRaises(IdeaError) as caught:
            md.encode_history(key, old, origin=state['ideas'][key]['origin'])
        self.assertEqual(caught.exception.code, 'unsupported_idea_version')
        self.assertEqual(str(caught.exception), 'This idea was made with an older glitch-idea. Capture it again.')

    def test_sketch_item_without_why_next_renders(self):
        for missing in ('absent', None, ''):
            with self.subTest(why_next=missing):
                state, key = v3_state()
                item = state['ideas'][key]['workflow']['steps']['exploration']['fields']['sketch'][0]
                if missing == 'absent':
                    del item['why_next']
                else:
                    item['why_next'] = missing
                body = md.decode_detail(md.encode_state(state)[key + '.md']).body
                self.assertIn('Check the lid', body)
                self.assertNotIn('why next', body)
                self.assertIn('done when: Lid is checked', body)

    def test_external_edit_of_discovery_becomes_a_draft_without_acceptance(self):
        import idea_store
        state, key = v3_state()
        baseline = state['ideas'][key]
        original = copy.deepcopy(baseline)
        original['workflow']['steps']['discovery']['fields']['problem'] = 'Edited in the file'
        result = idea_store._external_idea(original, baseline, 'Operator')
        self.assertEqual(result['workflow']['drafts']['discovery']['problem'], 'Edited in the file')
        self.assertEqual(result['workflow']['steps']['discovery']['acceptance'], baseline['workflow']['steps']['discovery']['acceptance'])
        self.assertEqual(result['workflow']['draft_version'], baseline['workflow']['draft_version'] + 1)

    def test_retired_shape_record_cannot_be_edited_from_a_file(self):
        import idea_store
        state, key = v3_state()
        baseline = state['ideas'][key]
        original = copy.deepcopy(baseline)
        original['shape'] = dict(outcome='x', scope=None, scope_reason=None, alternatives=[], method=None,
                                 method_reason=None, assumptions=[], next_slice=None, learning=[])
        with self.assertRaises(IdeaError) as caught:
            idea_store._external_idea(original, baseline, 'Operator')
        self.assertEqual(caught.exception.code, 'external_edit_conflict')


if __name__ == '__main__':
    unittest.main()
