"""actual temporary Markdown stores, CLI and process/thread races."""
import concurrent.futures
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0, str(SCRIPTS))
import idea_store as storage
import idea_markdown as md
import idea_transactions as tx
from idea_domain import IdeaError, digest, encoded, snapshot, require
from idea_workflow import capture_workflow, save_draft, source_digest

KEY = 'idea_'+'1'*32
STAMP = '2026-10-01T00:00:00Z'


def idea():
    words = '  Café 💡\r\n\n'
    value = dict(idea_id=KEY, revision=1, status='active',
                 origin=dict(text=words, sha256=digest(words.encode()), actor='operator', timestamp=STAMP),
                 shape=None, ratings=None, assessments=[], revisions=[], proposals=[], plans=[], executions=[])
    value['revisions'] = [snapshot(value, 'operator', 'capture')]
    return value


class MarkdownStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'Café spaced ideas'
        self.store = storage.Store(self.root)

    def capture(self, value=None):
        with self.store.transaction(write=True) as state:
            state['ideas'][KEY] = value or idea()
            state['order'] = [KEY]
            state['backlog_revision'] = 1
            self.store.commit(state)
        return state

    def show(self):
        with storage.Store(self.root).transaction() as state:
            return copy.deepcopy(state)

    def cli(self, *arguments):
        result = subprocess.run([sys.executable, str(SCRIPTS/'idea.py'), '--store', str(self.root), *map(str,arguments)], capture_output=True, timeout=8)
        return result.returncode, json.loads(result.stdout)

    def test_real_cli_capture_show_uses_markdown_frontmatter(self):
        words = Path(self.temp.name)/'words.txt'
        words.write_bytes('  Café 💡\r\n\n'.encode())
        code, response = self.cli('capture','--text-file',words,'--actor','operator')
        self.assertEqual(code,0,response)
        key = response['idea']['idea_id']
        self.assertFalse((self.root/'state.json').exists())
        self.assertTrue((self.root/'IDEAS.md').read_bytes().startswith(b'---\n'))
        self.assertTrue((self.root/(key+'.md')).read_bytes().startswith(b'---\n'))
        code, shown = self.cli('show',key)
        self.assertEqual(code,0,shown)
        self.assertEqual(shown['idea']['origin']['text'],words.read_bytes().decode())
        self.assertEqual(shown['idea'],response['idea'])

    def test_domain_roundtrip_noop_and_transaction_scope(self):
        state = self.capture()
        self.assertEqual(self.show(),state)
        with self.store.transaction(write=True) as same:
            before = (self.root/'IDEAS.md').read_bytes()
            self.assertIsNone(self.store.commit(same))
            self.assertEqual(same['transaction_revision'],1)
            self.assertEqual((self.root/'IDEAS.md').read_bytes(),before)
        self.assertFalse(hasattr(self.store,'lock'))
        with self.assertRaises(IdeaError): self.store.commit(state)

    def test_workflow_draft_roundtrip_index_untouched_counter_survives_restart(self):
        original = idea()
        fields = {'raw_text':original['origin']['text'], 'workspace':{'name':'fixture','path':'/fixture','confirmed':True}}
        captured = capture_workflow(original,fields,new_capture=True,actor='operator',timestamp=STAMP,
            evidence_id='capture-fixture',source_digest=source_digest('capture',1,{'capture':fields}))['idea']
        self.capture(captured)
        before = (self.root/'IDEAS.md').read_bytes()
        history = (self.root/'history'/KEY/'r1.md').read_bytes()
        with self.store.transaction(write=True) as state:
            state['ideas'][KEY] = save_draft(state['ideas'][KEY],'priorities',{'urgency':7,'importance':None},expected_revision=1,expected_draft_version=0)['idea']
            self.store.commit(state)
        self.assertEqual((self.root/'IDEAS.md').read_bytes(),before)
        self.assertEqual((self.root/'history'/KEY/'r1.md').read_bytes(),history)
        restarted = self.show()
        self.assertEqual(restarted['transaction_revision'],2)
        self.assertEqual(restarted['ideas'][KEY]['revision'],1)
        self.assertEqual(restarted['ideas'][KEY]['workflow']['drafts']['priorities'],{'urgency':7,'importance':None})

    def test_shared_store_thread_local_serialized_cas(self):
        self.capture()
        def mutate(value):
            try:
                with self.store.transaction(write=True) as state:
                    current = state['ideas'][KEY]
                    require(current['revision']==1,'Stale idea revision','stale_revision')
                    current['ratings'] = dict(urgency=value,importance=8,actor='operator',timestamp=STAMP)
                    current['revision'] += 1
                    current['revisions'].append(snapshot(current,'operator','rate'))
                    self.store.commit(state)
                return 'committed'
            except IdeaError as exc: return exc.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(mutate,[4,7]))
        self.assertEqual(sorted(results),['committed','stale_revision'])
        self.assertEqual(self.show()['ideas'][KEY]['revision'],2)

    def test_actual_two_cli_processes_only_one_stale_writer_commits(self):
        self.capture()
        def mutate(value):
            return self.cli('rate',KEY,'--expected-revision',1,'--urgency',value,'--importance',8,'--actor','operator')[1]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(mutate,[4,7]))
        self.assertEqual(sum(value['ok'] for value in results),1)
        self.assertEqual(self.show()['ideas'][KEY]['revision'],2)

    def test_read_recovers_prepared_transaction_before_exposing_state(self):
        state = self.capture()
        old = (self.root/(KEY+'.md')).read_bytes()
        doc = md.decode_detail(old)
        after = md.encode_document(doc.metadata,doc.body.replace(md.NOTES_START,md.NOTES_START+'recovered note\n'))
        class Stop(Exception): pass
        def stop(phase):
            if phase=='prepared': raise Stop()
        with self.store.transaction(write=True):
            with self.assertRaises(Stop): tx.publish(self.root,{KEY+'.md':after},{KEY+'.md':digest(old)},_checkpoint=stop)
        self.assertEqual(self.show(),state)
        self.assertIn(b'recovered note', (self.root/(KEY+'.md')).read_bytes())
        self.assertEqual(list((self.root/tx.JOURNAL).iterdir()),[])

    def test_missing_initialized_authority_and_missing_history_fail_closed(self):
        self.capture()
        index = self.root/'IDEAS.md'
        raw = index.read_bytes()
        index.unlink()
        with self.assertRaises(IdeaError): self.show()
        index.write_bytes(raw)
        (self.root/'history'/KEY/'r1.md').unlink()
        with self.assertRaises(IdeaError): self.show()

    def test_external_supported_current_fields_are_imported(self):
        self.capture()
        path = self.root/(KEY+'.md')
        doc = md.decode_detail(path.read_bytes())
        meta = copy.deepcopy(doc.metadata)
        meta['idea']['ratings'] = dict(urgency=4,importance=8,actor='operator',timestamp=STAMP)
        edited = md.encode_document(meta,doc.body)
        path.write_bytes(edited)
        imported = self.show()['ideas'][KEY]
        self.assertEqual(imported['revision'],2)
        self.assertEqual(imported['ratings']['urgency'],4)
        self.assertEqual(imported['revisions'][-1]['action'],'external-file-observed')
        self.assertEqual(self.show()['ideas'][KEY],imported)

    def test_notes_extensions_preserved_without_accepted_revision(self):
        self.capture()
        path = self.root/(KEY+'.md')
        doc = md.decode_detail(path.read_bytes())
        metadata = copy.deepcopy(doc.metadata)
        metadata['extensions'] = {'custom':{'enabled':True}}
        notes = 'Exact Notes\r\nno trailing newline'
        path.write_bytes(md.encode_document(metadata,doc.body.replace(md.NOTES_START,md.NOTES_START+notes)))
        self.assertEqual(self.show()['ideas'][KEY]['revision'],1)
        with self.store.transaction(write=True) as state:
            current = state['ideas'][KEY]
            current['ratings'] = dict(urgency=4,importance=8,actor='operator',timestamp=STAMP)
            current['revision'] += 1
            current['revisions'].append(snapshot(current,'operator','rate'))
            self.store.commit(state)
        document = md.decode_detail(path.read_bytes())
        self.assertEqual(md.detail_notes(document),notes)
        self.assertEqual(document.metadata['extensions'],metadata['extensions'])

    def test_generated_body_and_yaml_comments_are_not_discarded(self):
        self.capture()
        path = self.root/(KEY+'.md')
        original = path.read_bytes()
        path.write_bytes(original.replace(b'## Original wording',b'## Edited summary'))
        with self.assertRaises(IdeaError) as caught: self.show()
        self.assertEqual(caught.exception.code,'generated_body_conflict')
        path.write_bytes(original.replace(b'kind: idea',b'kind: idea # comment'))
        with self.store.transaction(write=True) as state:
            current = state['ideas'][KEY]
            current['ratings'] = dict(urgency=4,importance=8,actor='operator',timestamp=STAMP)
            current['revision'] += 1
            current['revisions'].append(snapshot(current,'operator','rate'))
            with self.assertRaises(IdeaError) as caught: self.store.commit(state)
            self.assertEqual(caught.exception.code,'yaml_comments')
        self.assertIn(b'# comment',path.read_bytes())

    def test_metadata_rewrite_and_orphan_link_removal_fail_closed(self):
        self.capture()
        self.assertEqual(self.cli('rate',KEY,'--expected-revision',1,'--urgency',4,'--importance',8,'--actor','operator')[0],0)
        assessment_path = Path(self.temp.name)/'assessment.json'
        assessment_path.write_bytes(encoded(dict(method='wsjf',version='1',inputs={'value':3,'time_criticality':2,'enablement':1,'effort':1},basis='fixture',assumptions=[],confidence='low',provenance='fixture')))
        self.assertEqual(self.cli('assess',KEY,'--expected-revision',2,'--file',assessment_path,'--actor','operator')[0],0)
        self.assertEqual(self.cli('propose',KEY,'--position',1,'--reason','fixture','--expected-backlog-revision',1,'--actor','operator')[0],0)
        path = self.root/(KEY+'.md')
        original = path.read_bytes(); doc = md.decode_detail(original)
        meta = copy.deepcopy(doc.metadata)
        meta['idea']['proposals'][0]['reason'] = 'rewritten'
        path.write_bytes(md.encode_document(meta,doc.body))
        with self.assertRaises(IdeaError): self.show()
        meta = copy.deepcopy(doc.metadata)
        meta['idea']['proposals'] = []; meta['metadata_evidence']['proposals'] = []
        path.write_bytes(md.encode_document(meta,doc.body))
        with self.assertRaises(IdeaError) as caught: self.show()
        self.assertEqual(caught.exception.code,'corrupt_store')

    def test_symlink_orphan_and_byte_limits_fail_closed(self):
        self.capture()
        extra = self.root/('idea_'+'2'*32+'.md')
        extra.write_bytes(b'orphan')
        with self.assertRaises(IdeaError): self.show()
        extra.unlink()
        detail = self.root/(KEY+'.md')
        raw = detail.read_bytes(); detail.unlink(); detail.symlink_to(self.root/'IDEAS.md')
        with self.assertRaises(IdeaError): self.show()
        detail.unlink(); detail.write_bytes(raw)
        with patch.object(storage,'MAX_STORE_BYTES',10):
            with self.assertRaises(IdeaError) as caught: self.show()
            self.assertEqual(caught.exception.code,'too_large')

    def test_hash_cas_refuses_external_edit_during_transaction(self):
        self.capture()
        index = self.root/'IDEAS.md'
        with self.store.transaction(write=True) as state:
            current = state['ideas'][KEY]
            current['ratings'] = dict(urgency=4,importance=8,actor='operator',timestamp=STAMP)
            current['revision'] += 1
            current['revisions'].append(snapshot(current,'operator','rate'))
            index.write_bytes(index.read_bytes()+b'outsider text\n')
            with self.assertRaises(IdeaError) as caught: self.store.commit(state)
            self.assertEqual(caught.exception.code,'save_conflict')
        self.assertIn(b'outsider text',index.read_bytes())

    def test_legacy_read_only_and_both_formats_are_explicit(self):
        self.root.mkdir()
        old = storage.empty_state()
        (self.root/'state.json').write_bytes(encoded(old))
        self.assertEqual(self.show(),old)
        with self.store.transaction(write=True): pass
        self.assertFalse((self.root/'IDEAS.md').exists())
        self.assertEqual((self.root/'state.json').read_bytes(),encoded(old))
        (self.root/'IDEAS.md').write_bytes(md.encode_index(old))
        with self.assertRaises(IdeaError) as caught: self.show()
        self.assertEqual(caught.exception.code,'ambiguous_store')

    def test_noop_commit_checks_observed_files_before_success(self):
        self.capture()
        index = self.root/'IDEAS.md'
        with self.store.transaction(write=True) as state:
            edited = index.read_bytes()+b'outsider text\n'
            index.write_bytes(edited)
            with self.assertRaises(IdeaError) as caught: self.store.commit(state)
            self.assertEqual(caught.exception.code,'save_conflict')
        self.assertEqual(index.read_bytes(),edited)

    def test_cli_plan_evidence_and_archive_repair_never_overwrites(self):
        self.capture()
        shaped = Path(self.temp.name)/'shape.json'
        shaped.write_bytes(encoded(dict(outcome='Reduce manual work',scope='capability',scope_reason='One reusable ability',
            alternatives=[dict(route='Reuse',reason='Check existing route')],method='bounded-plan',method_reason='Bounded change',
            assumptions=[],next_slice='One trial',learning=[])))
        assessed = Path(self.temp.name)/'assessment.json'
        assessed.write_bytes(encoded(dict(method='wsjf',version='1',inputs={'value':3,'time_criticality':2,'enablement':1,'effort':1},basis='fixture',assumptions=[],confidence='low',provenance='fixture')))
        for arguments in [('shape',KEY,'--expected-revision',1,'--file',shaped,'--actor','operator'),
                          ('rate',KEY,'--expected-revision',2,'--urgency',4,'--importance',8,'--actor','operator'),
                          ('assess',KEY,'--expected-revision',3,'--file',assessed,'--actor','operator')]:
            code, result = self.cli(*arguments)
            self.assertEqual(code,0,result)
        plan = Path(self.temp.name)/'plan.md'
        plan.write_text('# Trial\n\n## Goal\nReduce work.\n\n## Tasks\nOne trial.\n\n## Validation\nObserve result.\n\n## Idea trace\nidea_id: '+KEY+'\nidea_revision: 4\n')
        code, result = self.cli('register-plan',KEY,'--expected-revision',4,'--path',plan,'--actor','operator')
        self.assertEqual(code,0,result)
        self.assertEqual(Path(result['plan']['path']).read_bytes(),plan.read_bytes())
        self.assertEqual(self.show()['ideas'][KEY]['status'],'archived')
        archive = self.root/'archive'/KEY/'r4.json'
        original = archive.read_bytes()
        archive.unlink()
        code, result = self.cli('repair-views')
        self.assertEqual(code,0,result)
        self.assertEqual(archive.read_bytes(),original)
        archive.write_bytes(b'outsider archive')
        code, result = self.cli('repair-views')
        self.assertNotEqual(code,0,result)
        self.assertEqual(archive.read_bytes(),b'outsider archive')

    def test_published_io_failure_reports_committed_true_then_read_recovers(self):
        original = tx.publish
        def publish(*args,**kwargs):
            def checkpoint(phase):
                if phase=='published:'+KEY+'.md': raise OSError('injected post-publication failure')
            return original(*args,**kwargs,_checkpoint=checkpoint)
        with patch.object(storage.transactions,'publish',side_effect=publish):
            with self.assertRaises(IdeaError) as caught: self.capture()
        self.assertEqual(caught.exception.code,'durability_uncertain')
        self.assertTrue(caught.exception.details['committed'])
        self.assertEqual(self.show()['ideas'][KEY]['origin'],idea()['origin'])


if __name__=='__main__': unittest.main()
