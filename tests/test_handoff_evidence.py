"""Pure packet and consumed-source checks. Absolute paths below are schema fixtures, never claims of existing host files.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
import idea_handoff_evidence as codec
from idea_domain import IdeaError
from idea_markdown import encode_document, parse_document
from idea_workflow import STEP_ORDER, capture_workflow, source_digest
from test_workflow import ACTOR, STAMP, accept, fields, original_idea


def fixture(*, assets=False, raw=None, method=None, exploration=None, windows=False):
    values = fields()
    root = 'C:\\Example Store' if windows else '/example/Café Store'
    workspace = 'C:\\Example Work' if windows else '/example/Work 💡'
    values['capture']['workspace']['path'] = workspace
    if raw is not None:
        values['capture']['raw_text'] = raw
    if method is not None:
        values['method'].update(method)
    if exploration is not None:
        values['exploration'].update(exploration)
    design = None
    if assets:
        values['visualize'].update(disposition='accepted_set', source='claude_design', reason=None, design_set_id='set_'+'a'*32)
        design = dict(set_id='set_'+'a'*32, members=[dict(asset_id='asset_'+'b'*32,
            name='Café 💡 design.png', type='image/png', size=17, sha256='c'*64,
            path=root+('/assets/blobs/' if not windows else '\\assets\\blobs\\')+'asset_'+'b'*32+'.bin')])
    idea = original_idea()
    idea['origin'].update(text=values['capture']['raw_text'],
        sha256=hashlib.sha256(values['capture']['raw_text'].encode('utf-8')).hexdigest())
    idea = capture_workflow(idea, values['capture'], new_capture=True, actor='operator',
        timestamp=STAMP, evidence_id='capture-fixture',
        source_digest=source_digest('capture', 1, {'capture':values['capture']}))['idea']
    for step in STEP_ORDER[1:-1]:
        idea = accept(idea, step, values[step])['idea']
    receipt = idea['workflow']['steps']['assess']['acceptance']
    position = idea['workflow']['steps']['assess']['fields']['position']
    placement = dict(idea_id=idea['idea_id'], idea_revision=receipt['accepted_revision'],
        position=position['actual_position'], reason='Accepted assessment placement',
        actor=receipt['actor'], timestamp=receipt['timestamp'], source_backlog_revision=1,
        accepted_backlog_revision=2, neighbors=copy.deepcopy(position['neighbors']),
        snapshot=dict(ratings=copy.deepcopy(idea['ratings']), assessments=copy.deepcopy(idea['assessments'])))
    state = dict(ideas={idea['idea_id']:idea}, order=[idea['idea_id']], placements=[placement],
                 backlog_revision=2, transaction_revision=8)
    sep = '\\' if windows else '/'
    files = dict(detail=dict(path=root+sep+idea['idea_id']+'.md', sha256='1'*64),
                 index=dict(path=root+sep+'IDEAS.md', sha256='2'*64),
                 revision=dict(path=root+sep+'history'+sep+idea['idea_id']+sep+'r'+str(idea['revision'])+'.md', sha256='3'*64))
    record = codec.build_record(state, idea, source_files=files, design_set=design,
        handoff_id='handoff_'+'4'*32, session_id='session_'+'5'*32,
        request_id='handoff:fixture-1', actor=ACTOR, timestamp=STAMP)
    return state, idea, design, record


class HandoffEvidenceTests(unittest.TestCase):
    def refused(self, callback, code=None):
        with self.assertRaises(IdeaError) as caught:
            callback()
        if code is not None:
            self.assertEqual(caught.exception.code, code)

    def test_roundtrip_complete_byte_address_and_detached_values(self):
        for assets in (False, True):
            for windows in (False, True):
                with self.subTest(assets=assets, windows=windows):
                    state, idea, design, record = fixture(assets=assets, windows=windows)
                    before = copy.deepcopy((state, idea, design, record))
                    raw = codec.encode_record(record); link = codec.record_link(record, raw)
                    sha = hashlib.sha256(raw).hexdigest()
                    self.assertEqual(link, dict(handoff_id=record['handoff_id'], sha256=sha,
                        path='history/'+idea['idea_id']+'/metadata/'+sha+'.md'))
                    self.assertEqual(codec.decode_record(raw, path=link['path'], link=link,
                        expected_idea_id=idea['idea_id']), record)
                    self.assertEqual(codec.verify_current(record, state, idea, design), record)
                    self.assertEqual(raw, codec.encode_record(dict(reversed(list(record.items())))))
                    decoded = codec.decode_record(raw); decoded['accepted']['exploration']['fields']['assumptions'].append('Detached')
                    source = codec.eligible_source(state, idea, design); source['accepted'].clear()
                    self.assertEqual((state, idea, design, record), before)

    def test_prompt_names_actual_packet_and_literal_trace(self):
        state, idea, design, record = fixture(assets=True)
        raw = codec.encode_record(record); link = codec.record_link(record, raw)
        root = record['source_files']['detail']['path'].rsplit('/', 1)[0]
        path = root+'/'+link['path']
        prompt = codec.render_prompt(record, path)
        self.assertTrue(prompt.startswith('/glitch-plan\n'))
        self.assertIn('NEW window or pane', prompt)
        self.assertIn('## Idea trace\nidea_id: '+idea['idea_id']+'\nidea_revision: '+str(idea['revision'])+'\n', prompt)
        planning = json.loads(prompt.split('## Planning source\n', 1)[1])
        self.assertEqual(planning['packet_path'], path)
        self.assertEqual(planning['live_detail'], record['source_files']['detail']['path'])
        self.assertNotIn('sha256', planning['live_detail'])
        self.assertEqual(codec.decode_record(raw)['source_files']['detail'],record['source_files']['detail'])
        self.assertEqual(planning['design_set'], design)
        self.assertNotIn('shape', planning)
        self.assertEqual(planning['discovery'], record['accepted']['discovery']['fields'])
        self.assertEqual(planning['exploration'], record['accepted']['exploration']['fields'])
        self.assertEqual(planning['method'], record['accepted']['method']['fields'])
        self.assertIn('Lids are hard to clean', prompt)
        self.assertIn('Check the lid', planning['exploration']['sketch'][0]['title'])
        self.assertEqual(planning['exploration']['sketch'][0]['done_when'], 'Lid is checked')
        self.assertIn('Align is done; this is the input for Plan', prompt)
        self.assertNotIn('prompt', parse_document(raw).metadata)
        self.refused(lambda:codec.render_prompt(record, root+'/wrong.md'), 'corrupt_store')

    def test_recorded_packet_location_and_current_path_checks_are_separate_from_hashes(self):
        for assets in (False, True):
            for windows in (False, True):
                with self.subTest(assets=assets,windows=windows):
                    state,idea,design,record = fixture(assets=assets,windows=windows)
                    raw = codec.encode_record(record); link = codec.record_link(record,raw)
                    root = codec._absolute(record['source_files']['detail']['path']).parent
                    self.assertEqual(codec.recorded_packet_path(record,link),str(root/link['path']))
                    observed = copy.deepcopy(record['source_files'])
                    observed['detail']['sha256'] = 'e'*64
                    self.assertEqual(codec.verify_current(record,state,idea,design,source_files=observed),record)
                    for name in ('detail','index','revision'):
                        moved = copy.deepcopy(observed)
                        moved[name]['path'] = str(root.parent/'Moved'/codec._absolute(moved[name]['path']).name)
                        self.refused(lambda:codec.verify_current(record,state,idea,design,source_files=moved),'stale_source')
                    if design is not None:
                        moved_design = copy.deepcopy(design)
                        member = moved_design['members'][0]
                        member['path'] = str(root.parent/'Moved'/'assets'/'blobs'/(member['asset_id']+'.bin'))
                        self.refused(lambda:codec.verify_current_paths(record,observed,moved_design),'stale_source')
                    self.refused(lambda:codec.recorded_packet_path(record,dict(link,path='../foreign.md')),'corrupt_store')
                    self.assertEqual(codec.encode_record(record),raw)

    def test_hostile_unicode_roundtrips_literal_frontmatter_and_body(self):
        for value in ('Café 💡\r\n<script># heading\n', 'NEL\u0085LS\u2028PS\u2029end',
                      'C1'+''.join(chr(n) for n in range(0x7f, 0xa0))+'\ufffe\uffff💡'):
            with self.subTest(value=repr(value)):
                _, _, _, record = fixture(raw=value)
                raw = codec.encode_record(record); doc = parse_document(raw)
                self.assertEqual(codec.decode_record(raw), record)
                literal = doc.body.split('## Accepted planning source\n\n', 1)[1]
                self.assertTrue(all(line.startswith('    ') for line in literal.splitlines()))
                self.assertEqual(json.loads(literal), record)
                self.assertFalse(doc.has_comments)

    def test_noncanonical_and_tampered_evidence_refuses(self):
        _, _, _, record = fixture()
        raw = codec.encode_record(record); link = codec.record_link(record, raw)
        for changed in (raw+b'\n', raw.replace(b'Do not edit', b'Do edit', 1),
                        raw.replace(b'---\n', b'---\n# comment\n', 1)):
            self.refused(lambda changed=changed:codec.decode_record(changed), 'corrupt_store')
        for bad in (dict(link, sha256='0'*64), dict(link, handoff_id='handoff_'+'6'*32),
                    dict(link, path='../outside.md'), dict(link, token='forbidden')):
            self.refused(lambda bad=bad:codec.decode_record(raw, link=bad))
        self.refused(lambda:codec.decode_record(raw, expected_idea_id='idea_'+'9'*32), 'corrupt_store')
        self.refused(lambda:codec.decode_record(raw, path=link['path'].replace('/metadata/', '/other/')), 'corrupt_store')
        self.refused(lambda:codec.record_link(record, raw+b'\n'), 'corrupt_store')
        self.refused(lambda:codec.decode_record(encode_document(record, 'foreign body')), 'corrupt_store')

    def test_exact_schema_types_and_confined_source_paths(self):
        _, _, _, record = fixture()
        for key in codec.RECORD_KEYS:
            changed = copy.deepcopy(record); del changed[key]
            self.refused(lambda changed=changed:codec.validate_record(changed))
        for key, value in (('source_revision', True), ('schema_version', '1'),
                           ('request_id', '../outside'), ('handoff_id', 'idea_'+'1'*32),
                           ('source_digest', 'A'*64), ('token', 'not permitted'),
                           ('actor', '\ud800')):
            changed = copy.deepcopy(record); changed[key] = value
            self.refused(lambda changed=changed:codec.validate_record(changed))
        for path in ('relative.md', '/example/../other/IDEAS.md', '/foreign/IDEAS.md'):
            changed = copy.deepcopy(record); changed['source_files']['index']['path'] = path
            self.refused(lambda changed=changed:codec.validate_record(changed))
        changed = copy.deepcopy(record); changed['origin']['sha256'] = '0'*64
        self.refused(lambda:codec.validate_record(changed), 'corrupt_store')

    def test_acceptance_digests_dependencies_and_future_receipts_refuse(self):
        _, _, _, record = fixture()
        changes = [lambda r:r['accepted']['exploration']['acceptance'].update(source_digest='0'*64),
            lambda r:r['accepted']['assess']['acceptance']['dependencies'].clear(),
            lambda r:r['accepted']['exploration']['acceptance'].update(accepted_revision=r['source_revision']+1),
            lambda r:r['accepted']['capture'].update(acceptance=None),
            lambda r:r['accepted']['exploration']['acceptance']['dependencies'].update(review=dict(revision=1,digest='a'*64)),
            lambda r:r['accepted']['method']['fields'].update(selection='experiment-led')]
        for change in changes:
            mutated = copy.deepcopy(record); change(mutated)
            self.refused(lambda mutated=mutated:codec.validate_record(mutated))

    def test_conditional_methods_are_real_accepted_requirements(self):
        cases = [(dict(selection='appetite-led'), dict(investment=dict(cap=3,unit='days',boundary='One page'))),
                 (dict(selection='experiment-led'), dict(experiment=dict(question='Does it help?',
                     evidence='Observed completion', success_criterion='More completions', stop_rule='Stop after ten'))),
                 (dict(selection='adaptive-slices'), {})]
        for method, exploration in cases:
            with self.subTest(method=method):
                state, idea, design, record = fixture(method=method, exploration=exploration)
                self.assertEqual(codec.verify_current(record,state,idea,design),record)

    def test_dirty_invalidated_missing_and_archived_workflows_refuse(self):
        for mode in ('dirty', 'invalidated', 'missing', 'archived'):
            state, idea, design, _ = fixture()
            if mode == 'dirty':
                idea['workflow']['drafts']['exploration'] = dict(idea['workflow']['steps']['exploration']['fields'],outcome='Unsaved')
            elif mode == 'invalidated': idea['workflow']['steps']['method']['invalidated_by'] = ['discovery']
            elif mode == 'missing': del idea['workflow']
            else: idea['status'] = 'archived'
            self.refused(lambda:codec.eligible_source(state,idea,design),
                         'archived_revision' if mode == 'archived' else 'not_ready')

    def test_rating_assessment_and_placement_witness_mismatches_refuse(self):
        for mode in ('rating', 'rating_actor', 'assessment', 'placement', 'placement_actor', 'missing_witness'):
            state, idea, design, _ = fixture()
            if mode == 'rating': idea['ratings']['urgency'] = 3
            elif mode == 'rating_actor': idea['ratings']['actor'] = 'other'
            elif mode == 'assessment': idea['assessments'][-1]['score'] = 999
            elif mode == 'placement': state['placements'][0]['neighbors']['after'] = 'idea_'+'8'*32
            elif mode == 'placement_actor': state['placements'][0]['actor'] = 'other'
            else: state['placements'].clear()
            self.refused(lambda:codec.eligible_source(state,idea,design), 'not_ready')
        state,idea,design,_ = fixture()
        neighbor = 'idea_'+'8'*32; state['order'].append(neighbor)
        self.refused(lambda:codec.eligible_source(state,idea,design), 'stale_backlog')

    def test_digest_ignores_unconsumed_publication_navigation_and_backlog(self):
        state, idea, design, record = fixture()
        state['transaction_revision'] += 20; state['backlog_revision'] += 1
        idea['workflow']['current_step'] = 'discovery'; idea['workflow']['draft_version'] += 1
        state['unrelated_notes'] = 'No packet input'
        self.assertEqual(codec.verify_current(record,state,idea,design),record)
        changed = copy.deepcopy(record); changed['source_files']['detail']['sha256'] = 'e'*64
        self.assertEqual(codec.verify_current(changed,state,idea,design),changed)
        changed = copy.deepcopy(record); changed['source_revision'] += 1
        self.refused(lambda:codec.verify_current(changed,state,idea,design))
        state, idea, design, record = fixture(assets=True)
        design['members'][0]['sha256'] = 'd'*64
        self.refused(lambda:codec.verify_current(record,state,idea,design), 'stale_source')

    def test_design_selection_member_schema_bounds_and_generated_paths(self):
        state, idea, design, record = fixture(assets=True)
        for mode in ('wrong_set', 'duplicate', 'name_type', 'size', 'foreign_path', 'bool_size', 'extra'):
            changed = copy.deepcopy(design)
            if mode == 'wrong_set': changed['set_id'] = 'set_'+'d'*32
            elif mode == 'duplicate': changed['members'].append(copy.deepcopy(changed['members'][0]))
            elif mode == 'name_type': changed['members'][0]['type'] = 'application/pdf'
            elif mode == 'size': changed['members'][0]['size'] = 26*1024*1024
            elif mode == 'foreign_path': changed['members'][0]['path'] = '/foreign/'+changed['members'][0]['asset_id']+'.bin'
            elif mode == 'bool_size': changed['members'][0]['size'] = True
            else: changed['members'][0]['token'] = 'forbidden'
            self.refused(lambda changed=changed:codec.eligible_source(state,idea,changed))
        self.refused(lambda:codec.eligible_source(state,idea,None))
        skipped_state,skipped,_,_ = fixture()
        self.refused(lambda:codec.eligible_source(skipped_state,skipped,design), 'not_ready')
        changed = copy.deepcopy(record)
        changed['design_set']['members'][0]['path'] = '/foreign/assets/blobs/'+design['members'][0]['asset_id']+'.bin'
        self.refused(lambda:codec.validate_record(changed))

    def test_asset_aggregate_and_hostile_display_names(self):
        state,idea,design,_ = fixture(assets=True)
        design['members'][0]['name'] = '../../<script>\u0085\u2028💡\uffff.png'
        record = codec.build_record(state,idea,source_files=fixture(assets=True)[3]['source_files'],
            design_set=design,handoff_id='handoff_'+'4'*32,session_id='session_'+'5'*32,
            request_id='name-fixture',actor=ACTOR,timestamp=STAMP)
        self.assertEqual(codec.decode_record(codec.encode_record(record)),record)
        members = []
        root = design['members'][0]['path'].rsplit('/',1)[0]
        for number in range(5):
            identifier = 'asset_'+format(number,'032x')
            members.append(dict(design['members'][0],asset_id=identifier,size=25*1024*1024,
                                path=root+'/'+identifier+'.bin'))
        design['members'] = members
        self.refused(lambda:codec.eligible_source(state,idea,design),'too_large')


if __name__ == '__main__':
    unittest.main()
