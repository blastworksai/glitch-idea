"""Atomic suggestion evidence through real Stores. """
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
sys.path.insert(0,str(SCRIPTS))
import idea_store as storage
import idea_markdown as md
import idea_proposal_evidence as codec
import idea_transactions as tx
from idea_domain import IdeaError, encoded, require
from idea_workflow import save_draft, source_digest
from test_workflow import complete, fields

ACTOR = 'Operator'
KEY = complete()['idea_id']


def build_evidence(state, sid, request_id='suggestion-1', generation='agent_'+'3'*32):
    idea = state['ideas'][KEY]
    data = {'capture':copy.deepcopy(idea['workflow']['steps']['capture']['fields'])}
    source = dict(accepted_revision=idea['revision'],draft_version=idea['workflow']['draft_version'],data=data)
    correlation = dict(request_id=request_id,session_id=sid,idea_id=KEY,accepted_revision=source['accepted_revision'],
        draft_version=source['draft_version'],operation='shape',source_digest=source_digest('shape',idea['revision'],data))
    return dict(binding_id='binding_'+'2'*32,generation=generation,correlation=correlation,source=source,
                proposal=copy.deepcopy(fields()['shape']))


def validate_current(state, evidence):
    correlation = evidence['correlation']; idea = state['ideas'][correlation['idea_id']]
    current = dict(accepted_revision=idea['revision'],draft_version=idea['workflow']['draft_version'],
                   data={'capture':copy.deepcopy(idea['workflow']['steps']['capture']['fields'])})
    require(current == evidence['source'], 'Changed fixture source', 'stale_source')
    return current


def seed(root):
    store = storage.Store(root,observer=ACTOR)
    sid = store.create_session()
    with store.transaction(write=True) as state:
        state['ideas'][KEY] = complete(); state['order'] = [KEY]; state['backlog_revision'] = 1
        store.commit(state)
    return sid


CHILD = r'''import os,sys
sys.path.insert(0,sys.argv[1]);sys.path.insert(0,sys.argv[2])
from test_proposal_store import build_evidence,validate_current,ACTOR
import idea_store as storage
root,sid,phase=sys.argv[3:6]
original=storage.transactions.publish
def publish(*args,**kwargs):
    def cut(actual):
        matched=(actual==phase or phase=='staged' and actual.startswith('staged:')
                 or phase=='evidence' and actual.startswith('published:history/')
                 or phase=='detail' and actual.startswith('published:idea_')
                 or phase=='receipt' and actual.startswith('published:session-recovery/'))
        if matched: os._exit(73)
    return original(*args,**kwargs,_checkpoint=cut)
storage.transactions.publish=publish
store=storage.Store(root)
with store.transaction(write=True) as state:
    store.persist_agent_proposal(state,build_evidence(state,sid),actor=ACTOR,validate_current=validate_current)
'''


class ProposalStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'Café Store'
        self.sid = seed(self.root); self.store = storage.Store(self.root,observer=ACTOR)
        self.original = self.show(); self.evidence = build_evidence(self.original,self.sid)

    def show(self):
        with storage.Store(self.root,observer=ACTOR).transaction() as state: return copy.deepcopy(state)

    def files(self):
        return {p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*')
                if p.is_file() and not p.is_symlink() and tx.JOURNAL not in p.parts}

    def publish(self,evidence=None,validator=validate_current):
        with self.store.transaction(write=True) as state:
            return self.store.persist_agent_proposal(state,self.evidence if evidence is None else evidence,
                                                      actor=ACTOR,validate_current=validator)

    def refs(self):
        doc = md.decode_detail((self.root/(KEY+'.md')).read_bytes())
        return md.agent_proposal_links(doc.metadata['extensions'],KEY)

    def receipt_file(self):
        return self.root/'session-recovery'/(self.sid+'.json')

    def response_key(self,evidence=None):
        return storage._agent_receipt_id(self.evidence if evidence is None else evidence)

    def test_real_publication_changes_only_detail_counter_evidence_and_receipt(self):
        before = self.files(); link = self.publish(); after = self.files(); state = self.show()
        expected = copy.deepcopy(self.original); expected['transaction_revision'] += 1
        self.assertEqual(state,expected)
        self.assertEqual(self.refs(),[link]); self.assertEqual(state['ideas'][KEY]['proposals'],[])
        changed = {p for p in set(before)|set(after) if before.get(p)!=after.get(p)}
        self.assertEqual(changed,{KEY+'.md',link['path'],'session-recovery/'+self.sid+'.json'})
        record = codec.decode_proposal(after[link['path']],path=link['path'],link=link)
        self.assertEqual(record['actor'],ACTOR); self.assertEqual(record['data'],self.evidence['source']['data'])
        receipt = self.store.request_result(self.sid,self.response_key())
        self.assertEqual(receipt['proposal_index'],1)
        self.assertEqual({k:receipt[k] for k in link},link)
        self.assertEqual(receipt['revision'],self.original['ideas'][KEY]['revision'])

    def test_replay_precedes_current_validator_uuid_clock_and_keeps_all_bytes(self):
        link = self.publish(); before = self.files()
        with patch.object(storage,'now',side_effect=AssertionError('clock called')), \
             patch.object(storage.uuid,'uuid4',side_effect=AssertionError('UUID called')):
            replay = self.publish(validator=lambda *args:self.fail('current validator called on replay'))
        self.assertEqual(replay,link); self.assertEqual(self.files(),before)
        replay['sha256'] = '0'*64
        self.assertEqual(self.publish(),link)

    def test_changed_reply_conflicts_and_generation_uses_distinct_namespace(self):
        link = self.publish(); before = self.files()
        changed = copy.deepcopy(self.evidence); changed['proposal']['outcome'] = 'Different answer'
        with self.assertRaises(IdeaError) as caught: self.publish(changed)
        self.assertEqual(caught.exception.code,'request_conflict'); self.assertEqual(self.files(),before)
        changed = copy.deepcopy(self.evidence); changed['generation'] = 'agent_'+'4'*32
        second = self.publish(changed)
        self.assertEqual(self.refs(),[link,second])
        self.assertNotEqual(self.response_key(changed),self.response_key())
        self.assertEqual(self.store.request_result(self.sid,self.response_key(changed))['proposal_index'],2)

    def test_browser_mutation_cannot_occupy_private_response_namespace(self):
        before = self.files()
        with self.assertRaises(IdeaError):
            self.store.mutate(self.sid,self.response_key(),{},lambda state:self.fail('mutator called'))
        self.assertEqual(self.files(),before)

    def test_context_identity_and_unchanged_writable_guard(self):
        before = self.files()
        with self.assertRaises(IdeaError):
            self.store.persist_agent_proposal(copy.deepcopy(self.original),self.evidence,actor=ACTOR,validate_current=validate_current)
        with self.store.transaction() as state:
            with self.assertRaises(IdeaError):
                self.store.persist_agent_proposal(state,self.evidence,actor=ACTOR,validate_current=validate_current)
        with self.store.transaction(write=True) as state:
            with self.assertRaises(IdeaError):
                self.store.persist_agent_proposal(copy.deepcopy(state),self.evidence,actor=ACTOR,validate_current=validate_current)
            state['backlog_revision'] += 1
            with self.assertRaises(IdeaError):
                self.store.persist_agent_proposal(state,self.evidence,actor=ACTOR,validate_current=validate_current)
        self.assertEqual(self.files(),before)

    def test_nested_validation_and_mutation_contexts_refused(self):
        before = self.files()
        def nested(state,evidence):
            with self.store.transaction(): pass
        with self.assertRaises(IdeaError) as caught: self.publish(validator=nested)
        self.assertEqual(caught.exception.code,'store_busy')
        def mutator(state):
            self.store.persist_agent_proposal(state,self.evidence,actor=ACTOR,validate_current=validate_current)
        with self.assertRaises(IdeaError): self.store.mutate(self.sid,'attempt-publish',{},mutator)
        self.assertEqual(self.files(),before)

    def test_revision_and_draft_cas_do_not_depend_on_injected_validator(self):
        before = self.files()
        for field,code in (('accepted_revision','stale_revision'),('draft_version','stale_draft_version')):
            stale = copy.deepcopy(self.evidence)
            stale['correlation'][field] += 1; stale['source'][field] += 1
            stale['correlation']['source_digest'] = source_digest('shape',stale['correlation']['accepted_revision'],stale['source']['data'])
            with self.subTest(field=field), self.assertRaises(IdeaError) as caught:
                self.publish(stale,validator=lambda *args:self.fail('validator called before Store CAS'))
            self.assertEqual(caught.exception.code,code)
        self.assertEqual(self.files(),before)

    def test_source_change_bool_counter_and_impure_validator_refused_without_writes(self):
        before = self.files()
        def different(state,evidence):
            source = copy.deepcopy(evidence['source']); source['data']['capture']['raw_text']='Changed'
            return source
        def boolean_counter(state,evidence):
            source = copy.deepcopy(evidence['source']); source['draft_version']=False
            return source
        def impure(state,evidence):
            state['backlog_revision'] += 1
            return evidence['source']
        for validator in (different,boolean_counter,impure):
            with self.subTest(validator=validator.__name__), self.assertRaises(IdeaError): self.publish(validator=validator)
        self.assertEqual(self.files(),before)

    def test_exact_private_evidence_and_prospective_capacity_fail_before_publication(self):
        before = self.files()
        for kind in ('token','source','proposal'):
            changed = copy.deepcopy(self.evidence)
            if kind=='token': changed['token']='refused'
            elif kind=='source': changed['source']['draft_version']=True
            else: changed['proposal']['scope']='unsupported'
            with self.subTest(kind=kind), self.assertRaises(IdeaError): self.publish(changed)
        for module,name,value in ((storage,'MAX_SESSION_RECEIPTS',0),(storage,'MAX_RECEIPT_BYTES',20),
                                  (md,'MAX_AGENT_PROPOSALS',0),(storage,'MAX_STORE_BYTES',100)):
            with self.subTest(name=name), patch.object(module,name,value), self.assertRaises(IdeaError): self.publish()
        self.assertEqual(self.files(),before)

    def test_receipt_side_effect_uncertainty_recovers_original_proposal(self):
        original = tx.publish
        def uncertain(*args,**kwargs):
            def checkpoint(phase):
                if phase.startswith('published:session-recovery/'): raise OSError('fixture barrier failure')
            return original(*args,**kwargs,_checkpoint=checkpoint)
        with patch.object(storage.transactions,'publish',side_effect=uncertain), self.assertRaises(IdeaError) as caught:
            self.publish()
        self.assertEqual(caught.exception.code,'durability_uncertain'); self.assertTrue(caught.exception.details['committed'])
        state = self.show(); refs = self.refs(); self.assertEqual(len(refs),1)
        before = self.files()
        with patch.object(storage,'now',side_effect=AssertionError('new timestamp')):
            replay = self.publish(validator=lambda *args:self.fail('replay validator'))
        self.assertEqual(replay,refs[0]); self.assertEqual(self.files(),before)
        self.assertEqual(state['transaction_revision'],self.original['transaction_revision']+1)

    def test_subprocess_crash_at_staged_prepared_detail_evidence_receipt_complete(self):
        for phase in ('staged','prepared','detail','evidence','receipt','complete'):
            with self.subTest(phase=phase):
                root = Path(self.temp.name)/('crash-'+phase); sid = seed(root)
                completed = subprocess.run([sys.executable,'-c',CHILD,str(SCRIPTS),str(Path(__file__).parent),
                    str(root),sid,phase],capture_output=True,text=True,timeout=20)
                self.assertEqual(completed.returncode,73,completed.stderr)
                store = storage.Store(root)
                with store.transaction(write=True) as state:
                    evidence = build_evidence(state,sid)
                    link = store.persist_agent_proposal(state,evidence,actor=ACTOR,
                        validate_current=validate_current if phase=='staged' else lambda *args:self.fail('prepared replay validator'))
                doc = md.decode_detail((root/(KEY+'.md')).read_bytes())
                self.assertEqual(md.agent_proposal_links(doc.metadata['extensions'],KEY),[link])
                self.assertEqual(len(list((root/'history'/KEY/'metadata').glob('*.md'))),1)
                self.assertEqual(store.request_result(sid,storage._agent_receipt_id(evidence))['proposal_index'],1)
                self.assertFalse(any((root/tx.JOURNAL).iterdir()))

    def test_subsequent_draft_and_external_import_preserve_refs_and_bytes(self):
        link = self.publish(); raw = (self.root/link['path']).read_bytes()
        with self.store.transaction(write=True) as state:
            idea = state['ideas'][KEY]
            state['ideas'][KEY] = save_draft(idea,'priorities',{'urgency':6},expected_revision=idea['revision'],
                expected_draft_version=idea['workflow']['draft_version'])['idea']
            self.store.commit(state)
        doc = md.decode_detail((self.root/(KEY+'.md')).read_bytes())
        metadata = copy.deepcopy(doc.metadata); metadata['idea']['ratings']['urgency']=9
        metadata['extensions']['user_setting']={'kept':True}
        body = doc.body.replace(md.NOTES_START,md.NOTES_START+'Exact user Notes\r\n')
        (self.root/(KEY+'.md')).write_bytes(md.encode_document(metadata,body))
        state = self.show()
        self.assertEqual(state['ideas'][KEY]['revision'],self.original['ideas'][KEY]['revision']+1)
        self.assertEqual(self.refs(),[link]); self.assertEqual((self.root/link['path']).read_bytes(),raw)
        current = md.decode_detail((self.root/(KEY+'.md')).read_bytes())
        self.assertEqual(current.metadata['extensions']['user_setting'],{'kept':True})
        self.assertEqual(md.detail_notes(current),'Exact user Notes\r\n')
        # Historical retry remains valid even though current source/revision moved.
        self.assertEqual(self.publish(validator=lambda *args:self.fail('historical retry validator')),link)

    def test_missing_or_symlink_evidence_and_receipt_fail_closed(self):
        link = self.publish(); evidence_path = self.root/link['path']; original = evidence_path.read_bytes()
        evidence_path.unlink()
        with self.assertRaises(IdeaError): self.show()
        evidence_path.write_bytes(original)
        if hasattr(os,'symlink'):
            target = Path(self.temp.name)/'outside-evidence'; target.write_bytes(original)
            evidence_path.unlink(); evidence_path.symlink_to(target)
            with self.assertRaises(IdeaError): self.show()
            evidence_path.unlink(); evidence_path.write_bytes(original)
        receipt_path = self.receipt_file(); saved = receipt_path.read_bytes(); receipt_path.unlink()
        with self.assertRaises(IdeaError): self.show()
        receipt_path.write_bytes(saved)
        self.assertEqual(self.refs(),[link])

    def test_link_removal_reordering_and_receipt_ordinal_tamper_fail_closed(self):
        first = self.publish(); changed = copy.deepcopy(self.evidence); changed['correlation']['request_id']='suggestion-2'
        second = self.publish(changed)
        detail_path = self.root/(KEY+'.md'); original_detail = detail_path.read_bytes()
        for refs in ([first], [second,first]):
            doc = md.parse_document(original_detail); metadata = copy.deepcopy(doc.metadata)
            metadata['extensions'][md.AGENT_PROPOSAL_EXTENSION] = refs
            # Re-render the summary to prove generated text is not the only guard.
            detail_path.write_bytes(md.encode_document(metadata,md._detail_summary(metadata)+md.NOTES_END))
            with self.subTest(refs=refs), self.assertRaises(IdeaError): self.show()
        detail_path.write_bytes(original_detail)
        receipt_path = self.receipt_file(); saved = receipt_path.read_bytes(); record=json.loads(saved)
        record['receipts'][self.response_key()]['result']['proposal_index']=2
        receipt_path.write_bytes(encoded(record))
        with self.assertRaises(IdeaError): self.show()
        receipt_path.write_bytes(saved)
        self.assertEqual(self.show()['ideas'][KEY]['revision'],self.original['ideas'][KEY]['revision'])

    def test_receipt_payload_hash_or_proposal_hash_tamper_fail_closed(self):
        self.publish(); receipt_path=self.receipt_file(); saved=receipt_path.read_bytes()
        for field in ('payload_sha256','sha256'):
            record=json.loads(saved); response=record['receipts'][self.response_key()]
            if field=='payload_sha256': response[field]='0'*64
            else: response['result'][field]='0'*64
            receipt_path.write_bytes(encoded(record))
            with self.subTest(field=field), self.assertRaises(IdeaError): self.show()
        receipt_path.write_bytes(saved)

    def test_receipt_cas_during_source_validation_preserves_external_bytes(self):
        path=self.receipt_file(); saved=path.read_bytes()
        def changed_receipt(state,evidence):
            record=json.loads(saved); record['receipts']['external'] = dict(payload_sha256='0'*64,
                result=dict(ok=True,code='ok',request_id='external',write_state='no_op'))
            path.write_bytes(encoded(record))
            return evidence['source']
        with self.assertRaises(IdeaError) as caught: self.publish(validator=changed_receipt)
        self.assertEqual(caught.exception.code,'save_conflict')
        self.assertNotEqual(path.read_bytes(),saved)
        self.assertEqual(self.refs(),[])

    def test_legacy_publication_explicitly_requires_migration(self):
        root=Path(self.temp.name)/'legacy'; root.mkdir()
        (root/'state.json').write_bytes(encoded(self.original)); (root/'.lock').write_bytes(b'initialized\n')
        store=storage.Store(root); before=(root/'state.json').read_bytes()
        with store.transaction(write=True) as state:
            with self.assertRaises(IdeaError) as caught:
                store.persist_agent_proposal(state,self.evidence,actor=ACTOR,validate_current=validate_current)
        self.assertEqual(caught.exception.code,'migration_required')
        self.assertEqual((root/'state.json').read_bytes(),before); self.assertFalse((root/'IDEAS.md').exists())


if __name__ == '__main__': unittest.main()
