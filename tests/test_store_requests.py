"""receipts through real stores, threads and crash/restart processes."""
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

SCRIPTS=Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0,str(SCRIPTS))
import idea_store as storage
import idea_transactions as tx
from idea_domain import IdeaError, digest, encoded, identity, snapshot, require
from idea_workflow import capture_workflow, save_draft, source_digest

WORDS='  Capture Café 💡\r\n\n'
STAMP='2026-10-01T00:00:00Z'


def capture(state):
    key=identity('idea')
    idea=dict(idea_id=key,revision=1,status='active',origin=dict(text=WORDS,sha256=digest(WORDS.encode()),actor='operator',timestamp=STAMP),
              shape=None,ratings=None,assessments=[],revisions=[],proposals=[],plans=[],executions=[])
    idea['revisions']=[snapshot(idea,'operator','capture')]
    state['ideas'][key]=idea; state['order'].append(key); state['backlog_revision']+=1
    return {'idea_id':key,'origin':copy.deepcopy(idea['origin'])}


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


CHILD=r'''import sys,time
sys.path.insert(0,sys.argv[1]); sys.path.insert(0,sys.argv[2])
from test_store_requests import capture
from idea_store import Store
import idea_store as storage
from pathlib import Path
root=Path(sys.argv[3]); sid=sys.argv[4]; phase=sys.argv[5]
original=storage.transactions.publish
def publish(*args,**kwargs):
    def checkpoint(actual):
        if actual==phase:
            print(actual,flush=True); time.sleep(60)
    return original(*args,**kwargs,_checkpoint=checkpoint)
storage.transactions.publish=publish
Store(root).mutate(sid,'capture-lost',{'operation':'capture','text':'fixture'},capture)
'''


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'Café spaced store'
        self.store=storage.Store(self.root)
        self.sid=self.store.create_session()

    def receipt_path(self,sid=None):
        return self.root/'session-recovery'/((sid or self.sid)+'.json')

    def state(self):
        with storage.Store(self.root).transaction() as state: return copy.deepcopy(state)

    def files(self):
        return {p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file() and not p.is_symlink() and tx.JOURNAL not in p.parts}

    def call(self,key='capture-1',payload=None,callback=capture):
        return self.store.mutate(self.sid,key,{'operation':'capture','raw_text':WORDS} if payload is None else payload,callback)

    def test_session_creation_empty_authority_and_noop_restart_keep_zero_counters(self):
        state=self.state()
        self.assertEqual(state,storage.empty_state())
        self.assertTrue((self.root/'IDEAS.md').read_bytes().startswith(b'---'))
        self.assertEqual((self.root/'.lock').read_bytes(),b'initialized\n')
        index=(self.root/'IDEAS.md').read_bytes()
        result=self.call('noop',{'operation':'noop'},lambda state:{'note':'unchanged'})
        self.assertEqual(result['write_state'],'no_op')
        self.assertEqual(storage.Store(self.root).request_result(self.sid,'noop'),result)
        self.assertEqual(self.state(),storage.empty_state())
        self.assertEqual((self.root/'IDEAS.md').read_bytes(),index)
        self.assertEqual(set(json.loads(self.receipt_path().read_bytes())),{'schema_version','session_id','receipts'})

    def test_capture_lost_response_replay_after_restart_exact_historical_result(self):
        result=self.call()
        key=result['idea_id']; before=self.files()
        self.assertEqual(result['write_state'],'applied')
        self.assertEqual(result['revision'],1)
        self.assertEqual(result['backlog_revision'],1)
        self.assertEqual(result['origin']['text'],WORDS)
        restarted=storage.Store(self.root)
        def stale(state): raise AssertionError('Replay must not call stale revision checks')
        replay=restarted.mutate(self.sid,'capture-1',{'raw_text':WORDS,'operation':'capture'},stale)
        self.assertEqual(replay,result)
        self.assertEqual(restarted.request_result(self.sid,'capture-1',digest(canonical({'operation':'capture','raw_text':WORDS}))),result)
        self.assertEqual(len(self.state()['ideas']),1)
        self.assertEqual(self.state()['ideas'][key]['revision'],1)
        self.assertEqual(self.files(),before)
        replay['origin']['text']='caller mutation'
        self.assertEqual(restarted.request_result(self.sid,'capture-1'),result)

    def test_conflicting_replay_refused_before_callback(self):
        self.call()
        with self.assertRaises(IdeaError) as caught:
            self.call(payload={'operation':'capture','raw_text':'different'},callback=lambda state:self.fail('called'))
        self.assertEqual(caught.exception.code,'request_conflict')
        with self.assertRaises(IdeaError) as caught:
            self.store.request_result(self.sid,'capture-1','0'*64)
        self.assertEqual(caught.exception.code,'request_conflict')
        self.assertEqual(len(self.state()['ideas']),1)

    def test_receipt_only_noop_never_rewrites_domain_or_changes_counters(self):
        capture_result=self.call(); before=self.files(); state=self.state()
        result=self.call('noop',{'operation':'unchanged'},lambda state:{'idea_id':capture_result['idea_id']})
        self.assertEqual(result['write_state'],'no_op')
        self.assertEqual(self.state(),state)
        after=self.files()
        for path,raw in before.items():
            if not path.startswith('session-recovery/'):
                self.assertEqual(after[path],raw,path)

    def test_capacity_exhaustion_before_callback_and_old_receipt_still_replays(self):
        with patch.object(storage,'MAX_SESSION_RECEIPTS',2):
            one=self.call('one',{},lambda state:{})
            self.call('two',{},lambda state:{})
            before=self.files()
            with self.assertRaises(IdeaError) as caught:
                self.call('three',{},lambda state:self.fail('capacity called mutator'))
            self.assertEqual(caught.exception.code,'receipt_capacity_exhausted')
            self.assertEqual(self.call('one',{},lambda state:self.fail('replay called')),one)
            self.assertEqual(self.files(),before)

    def test_prospective_receipt_byte_overflow_refuses_callback_changes_before_publish(self):
        before=self.files()
        def big(state):
            result=capture(state); result['explanation']='x'*5000; return result
        with patch.object(storage,'MAX_RECEIPT_BYTES',1500):
            with self.assertRaises(IdeaError) as caught: self.call(callback=big)
        self.assertEqual(caught.exception.code,'receipt_capacity_exhausted')
        self.assertEqual(self.files(),before)
        self.assertEqual(self.state(),storage.empty_state())

    def test_actual_one_mib_receipt_overflow_and_payload_limit(self):
        before=self.files()
        def huge(state):
            result=capture(state); result['note']='x'*(storage.MAX_INPUT-220); return result
        with self.assertRaises(IdeaError): self.call(callback=huge)
        self.assertEqual(self.files(),before)
        with self.assertRaises(IdeaError): self.call(payload={'text':'x'*(storage.MAX_INPUT+1)},callback=lambda state:self.fail('oversize payload called'))
        self.assertEqual(self.files(),before)

    def test_bad_ids_and_payload_types_credentials_depth_and_nonfinite_rejected(self):
        for sid,rid in [('session_bad','request'),('../session','request'),('session_'+'A'*32,'request'),(self.sid,'a'*129),(self.sid,'bad/path'),(self.sid,True),(self.sid,None)]:
            with self.assertRaises(IdeaError): self.store.mutate(sid,rid,{},lambda state:self.fail('bad ID'))
        for payload in ([],{1:'nonstring key'},{'x':float('nan')},{'x':float('inf')},{'token':'secret'},{'agent_token':'secret'},{'bad':b'bytes'}):
            with self.subTest(payload=payload), self.assertRaises(IdeaError):
                self.call(payload=payload,callback=lambda state:self.fail('bad payload'))
        nested={}; node=nested
        for _ in range(35): node['x']={}; node=node['x']
        with self.assertRaises(IdeaError): self.call(payload=nested)
        self.assertEqual(self.state(),storage.empty_state())

    def test_nested_domain_fields_are_not_mistaken_for_transport_credentials(self):
        payload={'operation':'assess','assessment':{'basis':{'token':'measured work unit'},'provenance':'Discuss API token setup'},'shape':{'learning':['A token is discussed as domain text']}}
        business={'basis':{'token':'measured work unit','agent_token':'a domain label'},'note':'token discussion'}
        result=self.call('domain-text',payload,lambda state:business)
        self.assertEqual(result['basis'],business['basis'])
        self.assertEqual(self.store.request_result(self.sid,'domain-text'),result)
        for key in ('agent_token','csrf_token','authorization','cookie','password'):
            with self.subTest(key=key), self.assertRaises(IdeaError):
                self.call('credential', {key:'transport secret'},lambda state:self.fail('auth reached callback'))

    def test_persisted_known_result_counters_are_typed_before_replay(self):
        self.call(); path=self.receipt_path(); original=path.read_bytes()
        for key,value in [('revision','x'),('revision',True),('revision',0),('draft_version',-1),('draft_version',False),('backlog_revision','1'),('backlog_revision',True),('idea_id','bad'),('committed','yes')]:
            with self.subTest(key=key,value=value):
                record=json.loads(original); record['receipts']['capture-1']['result'][key]=value
                path.write_bytes(encoded(record))
                with self.assertRaises(IdeaError) as caught:
                    storage.Store(self.root).request_result(self.sid,'capture-1')
                self.assertEqual(caught.exception.code,'corrupt_receipts')
        path.write_bytes(original)
        self.assertEqual(storage.Store(self.root).request_result(self.sid,'capture-1')['revision'],1)

    def test_unknown_request_and_missing_initialized_session_are_explicit(self):
        with self.assertRaises(IdeaError) as caught: self.store.request_result(self.sid,'unknown')
        self.assertEqual(caught.exception.code,'request_not_found')
        self.call(); self.receipt_path().unlink()
        restarted=storage.Store(self.root)
        for action in (lambda:restarted.request_result(self.sid,'capture-1'),lambda:restarted.mutate(self.sid,'capture-1',{},lambda state:self.fail('missing session called'))):
            with self.assertRaises(IdeaError) as caught: action()
            self.assertEqual(caught.exception.code,'receipt_session_missing')
        self.assertEqual(len(self.state()['ideas']),1)
        self.assertFalse(self.receipt_path().exists())

    def test_missing_domain_authority_never_resets_session_only_store(self):
        (self.root/'IDEAS.md').unlink()
        with self.assertRaises(IdeaError): self.store.request_result(self.sid,'unknown')
        with self.assertRaises(IdeaError): self.call(callback=lambda state:self.fail('missing domain'))
        self.assertFalse((self.root/'IDEAS.md').exists())

    def test_receipt_schema_duplicate_nonfinite_identity_and_wrong_types_fail_closed(self):
        good=self.receipt_path().read_bytes()
        malformed=[b'{"schema_version":1,"schema_version":1}',b'{broken',b'null',b'{"schema_version":NaN}',
                   encoded({'schema_version':True,'session_id':self.sid,'receipts':{}}),
                   encoded({'schema_version':1,'session_id':'session_'+'0'*32,'receipts':{}}),
                   encoded({'schema_version':1,'session_id':self.sid,'receipts':[]}),
                   encoded({'schema_version':1,'session_id':self.sid,'receipts':{},'extra':True})]
        for raw in malformed:
            self.receipt_path().write_bytes(raw)
            with self.assertRaises(IdeaError) as caught: self.call(callback=lambda state:self.fail('malformed called'))
            self.assertEqual(caught.exception.code,'corrupt_receipts')
            self.assertEqual(self.receipt_path().read_bytes(),raw)
        self.receipt_path().write_bytes(good)

    def test_receipt_and_parent_symlinks_refused(self):
        if not hasattr(os,'symlink'): self.skipTest('Symlink unavailable')
        raw=self.receipt_path().read_bytes(); self.receipt_path().unlink(); self.receipt_path().symlink_to(self.root/'IDEAS.md')
        with self.assertRaises(IdeaError): self.call(callback=lambda state:self.fail('symlink'))
        self.receipt_path().unlink(); self.receipt_path().write_bytes(raw)
        parent=self.root/'session-recovery'; parent.rename(self.root/'outside-receipts'); parent.symlink_to(self.root/'outside-receipts',target_is_directory=True)
        with self.assertRaises(IdeaError): self.call(callback=lambda state:self.fail('parent symlink'))

    def test_concurrent_same_request_exactly_one_mutator_and_capture(self):
        calls=[]
        def callback(state): calls.append(True); return capture(state)
        def request(_): return storage.Store(self.root).mutate(self.sid,'same',{'operation':'capture'},callback)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(request,range(4)))
        self.assertEqual(len(calls),1)
        self.assertTrue(all(result==results[0] for result in results))
        self.assertEqual(len(self.state()['ideas']),1)
        self.assertEqual(self.state()['transaction_revision'],1)

    def test_invalid_domain_and_callback_exception_never_publish_receipt(self):
        before=self.files()
        def broken(state): state['backlog_revision']=True; return {}
        with self.assertRaises(IdeaError): self.call(callback=broken)
        def failure(state): capture(state); raise IdeaError('not_ready','fixture')
        with self.assertRaises(IdeaError): self.call(callback=failure)
        self.assertEqual(self.files(),before)
        with self.assertRaises(IdeaError) as caught: self.store.request_result(self.sid,'capture-1')
        self.assertEqual(caught.exception.code,'request_not_found')

    def test_result_metadata_conflicts_credentials_and_invalid_json_refused(self):
        before=self.files()
        for result in ({'ok':False},{'request_id':'wrong'},{'write_state':'applied'},{'backlog_revision':True},{'csrf_token':'secret'},[],{'revision':float('nan')},{'revision':'x'},{'draft_version':True},{'backlog_revision':-1}):
            with self.subTest(result=result), self.assertRaises(IdeaError):
                self.call(callback=lambda state,result=result:copy.deepcopy(result))
        self.assertEqual(self.files(),before)
        with self.assertRaises(IdeaError):
            self.call(callback=lambda state:self.store.commit(state))
        self.assertEqual(self.files(),before)

    def test_receipt_outsider_cas_is_observed_even_for_domain_noop(self):
        original=self.receipt_path().read_bytes()
        edited=original+b'\n'
        def outsider(state): self.receipt_path().write_bytes(edited); return {}
        with self.assertRaises(IdeaError) as caught: self.call(callback=outsider)
        self.assertEqual(caught.exception.code,'save_conflict')
        self.assertEqual(self.receipt_path().read_bytes(),edited)
        self.assertEqual(self.state(),storage.empty_state())

    def test_payload_contents_never_persisted_only_digest_and_business_result(self):
        marker='not-the-business-result-unique-payload'
        result=self.call('private',{'text':marker},lambda state:{'status':'unchanged'})
        raw=self.receipt_path().read_bytes()
        self.assertNotIn(marker.encode(),raw)
        record=json.loads(raw)['receipts']['private']
        self.assertEqual(set(record),{'payload_sha256','result'})
        self.assertEqual(record['payload_sha256'],digest(canonical({'text':marker})))
        self.assertEqual(record['result'],result)

    def test_domain_and_receipt_recovery_after_actual_process_kill(self):
        # Receipt is before IDEAS in publication order; both domain and receipt
        # must recover coherently from preparation, receipt, and index boundaries.
        for phase in ('prepared','published:session-recovery/'+self.sid+'.json','published:IDEAS.md','complete'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                root=Path(directory)/'store'; sid=storage.Store(root).create_session()
                target=phase.replace(self.sid,sid)
                child=subprocess.Popen([sys.executable,'-c',CHILD,str(SCRIPTS),str(Path(__file__).parent),str(root),sid,target],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                try:
                    self.assertEqual(child.stdout.readline().strip(),target)
                    child.kill(); child.communicate(timeout=3)
                finally:
                    if child.poll() is None: child.kill()
                    child.communicate(timeout=3)
                code='import sys;sys.path.insert(0,sys.argv[1]);from idea_store import Store;print(Store(sys.argv[2]).request_result(sys.argv[3],"capture-lost")["idea_id"])'
                restarted=subprocess.run([sys.executable,'-c',code,str(SCRIPTS),str(root),sid],capture_output=True,text=True,timeout=5)
                self.assertEqual(restarted.returncode,0,restarted.stderr)
                recovered=storage.Store(root).request_result(sid,'capture-lost')
                self.assertEqual(recovered['idea_id'],restarted.stdout.strip())
                with storage.Store(root).transaction() as state:
                    self.assertEqual(state['order'],[recovered['idea_id']])
                    self.assertEqual(state['transaction_revision'],1)
                self.assertEqual(list((root/tx.JOURNAL).iterdir()),[])

    def test_uncertain_save_recorded_success_recoverable_without_repeat_mutation(self):
        original=tx.publish
        def fail(*args,**kwargs):
            def checkpoint(phase):
                if phase=='published:session-recovery/'+self.sid+'.json': raise OSError('lost response after durable receipt')
            return original(*args,**kwargs,_checkpoint=checkpoint)
        with patch.object(storage.transactions,'publish',side_effect=fail):
            with self.assertRaises(IdeaError) as caught: self.call()
        self.assertEqual(caught.exception.code,'durability_uncertain')
        self.assertTrue(caught.exception.details['committed'])
        recorded=storage.Store(self.root).request_result(self.sid,'capture-1')
        self.assertEqual(recorded['write_state'],'applied')
        self.assertEqual(self.call(callback=lambda state:self.fail('uncertain retry mutator')),recorded)
        self.assertEqual(len(self.state()['ideas']),1)

    def test_draft_generic_mutation_keeps_index_and_accepted_revision(self):
        captured=self.call(); key=captured['idea_id']
        with self.store.transaction(write=True) as state:
            idea=state['ideas'][key]
            fields={'raw_text':WORDS,'workspace':{'name':'fixture','path':'/fixture','confirmed':True}}
            # Attach fixture workflow before snapshot publication using a fresh
            # versioned revision; preserve the already immutable legacy r1.
            from idea_workflow import import_workflow, adapt_snapshot
            current=import_workflow(idea); current['revision']+=1
            current['revisions'].append(adapt_snapshot(snapshot(current,'operator','enter-workflow'),current['workflow']))
            state['ideas'][key]=current; self.store.commit(state)
        index=(self.root/'IDEAS.md').read_bytes(); rev=self.state()['ideas'][key]['revision']
        def draft(state):
            current=state['ideas'][key]
            state['ideas'][key]=save_draft(current,'priorities',{'urgency':7,'importance':None},expected_revision=rev,expected_draft_version=0)['idea']
            return {'idea_id':key}
        result=self.call('draft',{'step':'priorities','urgency':7},draft)
        self.assertEqual(result['write_state'],'applied'); self.assertEqual(result['revision'],rev)
        self.assertEqual(result['draft_version'],1)
        self.assertEqual((self.root/'IDEAS.md').read_bytes(),index)

    def test_legacy_session_explicitly_migrates_without_domain_counter(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); old=encoded(storage.empty_state()); (root/'state.json').write_bytes(old)
            legacy=storage.Store(root)
            sid=legacy.create_session()
            self.assertFalse((root/'state.json').exists())
            self.assertEqual((root/tx.FROZEN).read_bytes(),old)
            result=legacy.mutate(sid,'legacy-noop',{},lambda state:{'note':'unchanged'})
            self.assertEqual(result['write_state'],'no_op')
            with legacy.transaction() as state: self.assertEqual(state,storage.empty_state())



if __name__=='__main__': unittest.main()
