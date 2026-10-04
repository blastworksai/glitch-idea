"""external edits through real authoritative Markdown stores."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
import idea_store as storage
import idea_markdown as md
import idea_transactions as tx
from idea_domain import IdeaError, assessment, snapshot
from idea_workflow import derive_state, save_draft
from test_workflow import complete, captured, fields, original_idea

KEY = original_idea()['idea_id']

class ExternalEditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'ideas'
        self.store = storage.Store(self.root,observer='observing-session')

    def seed(self,value=None,second=False):
        values = [value or complete()]
        if second:
            other = copy.deepcopy(values[0]); other['idea_id'] = 'idea_'+'2'*32
            values.append(other)
        with self.store.transaction(write=True) as state:
            state['ideas'] = {v['idea_id']:v for v in values}
            state['order'] = list(state['ideas']); state['backlog_revision'] = 1
            self.store.commit(state)
        return copy.deepcopy(state)

    def show(self):
        with storage.Store(self.root,observer='observing-session').transaction() as state:
            return copy.deepcopy(state)

    def edit(self,change,key=KEY):
        path = self.root/(key+'.md')
        doc = md.parse_document(path.read_bytes()); meta = copy.deepcopy(doc.metadata)
        change(meta)
        raw = md.encode_document(meta,doc.body)
        path.write_bytes(raw)
        return raw

    def refused(self,change,code=None):
        edited = self.edit(change)
        with self.assertRaises(IdeaError) as caught: self.show()
        if code: self.assertEqual(caught.exception.code,code)
        self.assertEqual((self.root/(KEY+'.md')).read_bytes(),edited)

    def test_legacy_null_inputs_import_once_with_observer_not_declared_editor(self):
        initial = self.seed(original_idea())
        value = dict(fields()['shape'],method='bounded-plan',method_reason='Known')
        self.edit(lambda m:m['idea'].update(shape=value,ratings=dict(urgency=4,importance=8,actor='pretend-editor',timestamp='pretend-time')))
        current = self.show()['ideas'][KEY]
        self.assertEqual(current['revision'],2)
        self.assertEqual(current['origin'],initial['ideas'][KEY]['origin'])
        self.assertEqual(current['revisions'][0],initial['ideas'][KEY]['revisions'][0])
        self.assertEqual(current['revisions'][-1]['actor'],'observing-session')
        self.assertEqual(current['revisions'][-1]['action'],'external-file-observed')
        self.assertEqual(current['ratings']['actor'],'observing-session')
        self.assertNotIn('workflow',current)
        self.assertEqual(self.show()['ideas'][KEY],current)

    def test_legacy_rating_mirrors_draft_and_invalidates_self_and_dependents(self):
        initial = self.seed()
        history = (self.root/'history'/KEY/'r6.md').read_bytes()
        self.edit(lambda m:m['idea']['ratings'].update(urgency=9))
        current = self.show()['ideas'][KEY]
        self.assertEqual(current['revision'],7)
        self.assertEqual(current['workflow']['drafts']['priorities']['urgency'],9)
        for step in ('priorities','assess'):
            self.assertIn('priorities',current['workflow']['steps'][step]['invalidated_by'])
            self.assertEqual(current['workflow']['steps'][step]['acceptance'],initial['ideas'][KEY]['workflow']['steps'][step]['acceptance'])
        self.assertEqual(derive_state(current)['steps']['priorities']['status'],'unsaved')
        self.assertEqual((self.root/'history'/KEY/'r6.md').read_bytes(),history)
        self.assertEqual(self.show()['ideas'][KEY],current)

    def test_capture_edit_preserves_origin_and_persisted_partial_buffers(self):
        self.seed()
        with self.store.transaction(write=True) as state:
            v=state['ideas'][KEY]
            state['ideas'][KEY]=save_draft(v,'priorities',{'urgency':6,'importance':None},expected_revision=v['revision'],expected_draft_version=v['workflow']['draft_version'])['idea']
            self.store.commit(state)
        self.edit(lambda m:m['idea']['workflow']['steps']['capture']['fields'].update(raw_text='Revised current wording'))
        current=self.show()['ideas'][KEY]
        self.assertEqual(current['origin']['text'],fields()['capture']['raw_text'])
        self.assertEqual(current['workflow']['drafts']['capture']['raw_text'],'Revised current wording')
        self.assertEqual(current['workflow']['drafts']['priorities'],{'urgency':6,'importance':None})
        self.assertEqual(current['workflow']['current_step'],'priorities')
        self.assertIn('capture',current['workflow']['steps']['shape']['invalidated_by'])

    def test_workflow_shape_mirrors_legacy_and_conflicting_aliases_refused(self):
        self.seed()
        self.edit(lambda m:m['idea']['workflow']['steps']['shape']['fields'].update(outcome='New outcome'))
        self.assertEqual(self.show()['ideas'][KEY]['shape']['outcome'],'New outcome')
        def conflict(m):
            m['idea']['shape']['outcome']='Legacy different'
            m['idea']['workflow']['steps']['shape']['fields']['outcome']='Workflow different'
        self.refused(conflict,'external_edit_conflict')

    def test_assessment_input_recomputed_score_only_refused(self):
        value=original_idea()
        value['assessments']=[dict(assessment(dict(method='wsjf',version='1',inputs=dict(value=8,time_criticality=0,enablement=0,effort=2),basis='fixture',assumptions=[],confidence='low',provenance='fixture')),actor='original',timestamp='original')]
        value['revision']=2; value['revisions'].append(snapshot(value,'original','assess'))
        self.seed(value)
        self.edit(lambda m:m['idea']['assessments'][0]['inputs'].update(value=10))
        current=self.show()['ideas'][KEY]
        self.assertEqual(current['assessments'][0]['score'],5)
        self.assertEqual(current['assessments'][0]['actor'],'original')
        self.assertEqual(current['revision'],3)
        self.refused(lambda m:m['idea']['assessments'][0].update(score=999),'external_edit_conflict')

    def test_protected_controls_receipts_attribution_and_identity_refused(self):
        for change in [lambda m:m['idea']['workflow'].update(current_step='capture'),
                       lambda m:m['idea']['workflow'].update(draft_version=99),
                       lambda m:m['idea']['workflow']['drafts'].update(priorities={'urgency':9}),
                       lambda m:m['idea']['workflow']['steps']['capture']['acceptance'].update(actor='forged'),
                       lambda m:m['idea']['ratings'].update(actor='forged')]:
            with self.subTest(change=change):
                self.seed() if not self.root.exists() else None
                path=self.root/(KEY+'.md'); before=path.read_bytes()
                self.refused(change,'external_edit_conflict'); path.write_bytes(before)
        self.refused(lambda m:m['idea'].update(revision=99))

    def test_unaccepted_fields_cannot_acquire_receipt(self):
        self.seed(captured())
        self.refused(lambda m:m['idea']['workflow']['steps']['priorities'].update(fields=fields()['priorities']),'external_edit_conflict')

    def test_notes_extensions_only_no_revision_preserves_exact_bytes(self):
        before=self.seed()
        path=self.root/(KEY+'.md'); doc=md.parse_document(path.read_bytes())
        meta=copy.deepcopy(doc.metadata); meta['extensions']={'custom':'retained'}
        note='CRLF\r\nexact\n### Workflow\n\n```yaml\nexample: pasted notes\n```\n'
        raw=md.encode_document(meta,doc.body.replace(md.NOTES_START,md.NOTES_START+note))
        path.write_bytes(raw)
        self.assertEqual(self.show(),before)
        self.assertEqual(path.read_bytes(),raw)

    def test_malformed_editable_envelopes_fail_typed_and_preserve_bytes(self):
        self.seed(); path=self.root/(KEY+'.md'); original=path.read_bytes()
        for change in [lambda m:m.pop('idea'), lambda m:m['idea'].update(assessments=None),
                       lambda m:m['idea'].update(workflow=None),lambda m:m['idea'].update(workflow=[]),
                       lambda m:m['idea']['workflow'].update(unknown_control=True)]:
            with self.subTest(change=change):
                path.write_bytes(original)
                self.refused(change)
        path.write_bytes(original)

    def test_partial_workflow_input_remains_review_draft_without_acceptance(self):
        initial=self.seed()
        self.edit(lambda m:m['idea']['workflow']['steps']['priorities']['fields'].update(importance=None))
        current=self.show()['ideas'][KEY]
        self.assertIsNone(current['workflow']['drafts']['priorities']['importance'])
        self.assertEqual(current['ratings'],initial['ideas'][KEY]['ratings'])
        self.assertEqual(current['workflow']['steps']['priorities']['acceptance'],initial['ideas'][KEY]['workflow']['steps']['priorities']['acceptance'])
        self.assertEqual(current['revision'],7)

    def test_partial_shape_source_stays_draft_when_legacy_mirror_is_incomplete(self):
        initial=self.seed()
        self.edit(lambda m:m['idea']['workflow']['steps']['shape']['fields'].update(alternatives=[{'route':'Partial'}]))
        current=self.show()['ideas'][KEY]
        self.assertEqual(current['workflow']['drafts']['shape']['alternatives'],[{'route':'Partial'}])
        self.assertEqual(current['shape'],initial['ideas'][KEY]['shape'])
        self.assertIn('shape',current['workflow']['steps']['shape']['invalidated_by'])
        self.assertEqual(self.show()['ideas'][KEY],current)

    def test_comments_and_generated_body_conflict_preserve_file(self):
        self.seed(); path=self.root/(KEY+'.md'); before=path.read_bytes()
        self.edit(lambda m:m['idea']['ratings'].update(urgency=9))
        edited=path.read_bytes().replace(b'kind: idea',b'kind: idea # preserve')
        path.write_bytes(edited)
        with self.assertRaises(IdeaError) as caught:self.show()
        self.assertEqual(caught.exception.code,'yaml_comments');self.assertEqual(path.read_bytes(),edited)
        path.write_bytes(before.replace(b'## Current details',b'## rewritten'))
        with self.assertRaises(IdeaError) as caught:self.show()
        self.assertEqual(caught.exception.code,'generated_body_conflict')

    def test_two_ideas_import_in_one_persistence_generation(self):
        initial=self.seed(second=True)
        for key in initial['order']:self.edit(lambda m:m['idea']['ratings'].update(urgency=9),key)
        current=self.show()
        self.assertEqual(current['transaction_revision'],initial['transaction_revision']+1)
        self.assertEqual([v['revision'] for v in current['ideas'].values()],[7,7])
        self.assertEqual(self.show(),current)

    def test_import_cas_preserves_concurrent_outsider_edit(self):
        self.seed(); self.edit(lambda m:m['idea']['ratings'].update(urgency=9))
        path=self.root/(KEY+'.md'); original=storage.Store._check_cas
        def cas(store,context):
            path.write_bytes(path.read_bytes()+b'concurrent outsider')
            return original(store,context)
        with patch.object(storage.Store,'_check_cas',cas):
            with self.assertRaises(IdeaError) as caught:self.show()
        self.assertEqual(caught.exception.code,'save_conflict')
        self.assertTrue(path.read_bytes().endswith(b'concurrent outsider'))

    def test_post_publication_failure_reports_committed_and_recovers_once(self):
        self.seed(); self.edit(lambda m:m['idea']['ratings'].update(urgency=9))
        original=tx.publish
        def publish(*args,**kwargs):
            def stop(phase):
                if phase=='published:'+KEY+'.md':raise OSError('injected')
            return original(*args,**kwargs,_checkpoint=stop)
        with patch.object(storage.transactions,'publish',publish):
            with self.assertRaises(IdeaError) as caught:self.show()
        self.assertTrue(caught.exception.details['committed'])
        current=self.show()['ideas'][KEY]
        self.assertEqual(current['revision'],7)
        self.assertEqual(self.show()['ideas'][KEY],current)

if __name__=='__main__':unittest.main()
