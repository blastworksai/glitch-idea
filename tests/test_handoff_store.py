"""Historical readers and atomic publication over actual Stores. Operator.

Fixtures publish through the existing trusted journal writer under Store lock;
the publication tests separately qualify Store.publish_handoff itself.
"""
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
import idea_handoff_evidence as codec
import idea_markdown as md
import idea_store as storage
import idea_transactions as tx
from idea_domain import IdeaError, decode, digest, encoded
from idea_workflow import save_draft
from idea_workflow import capture_workflow, source_digest
from test_handoff_evidence import fixture as source_fixture
from test_workflow import accept, fields, original_idea, STAMP
import idea_asset_evidence as assets

ACTOR = 'Operator'


def seed(root, count=1):
    store = storage.Store(root,observer=ACTOR)
    sid = store.create_session()
    observed,idea,_,_ = source_fixture(); key = idea['idea_id']
    with store.transaction(write=True) as state:
        state.update(dict(observed,schema_version=1,archives={}))
        store.commit(state)
    packets,links = [],[]
    with store.transaction(write=True) as state:
        context = store._contexts.active; idea = state['ideas'][key]
        source_files = {name:dict(path=str(store.path/relative),sha256=digest(context['files'][relative]))
                       for name,relative in (('detail',key+'.md'),('index','IDEAS.md'),
                                             ('revision','history/'+key+'/r'+str(idea['revision'])+'.md'))}
        relative,before,session = store._read_receipts(sid)
        raw_map = {}
        for number in range(1,count+1):
            request = 'handoff-fixture-'+str(number)
            packet = codec.build_record(state,idea,source_files=source_files,design_set=None,
                handoff_id='handoff_'+format(number,'032x'),session_id=sid,request_id=request,
                actor=ACTOR,timestamp='2026-10-02T00:00:00Z')
            raw = codec.encode_record(packet); link = codec.record_link(packet,raw)
            packets.append(packet); links.append(link); raw_map[link['path']] = raw
            result = dict(ok=True,code='ok',request_id=request,write_state='applied',idea_id=key,
                revision=idea['revision'],draft_version=idea['workflow']['draft_version'],
                backlog_revision=state['backlog_revision'],handoff_index=number,**link)
            payload = dict(operation='handoff',payload=dict(request_id=request,idea_id=key,
                expected_revision=result['revision'],expected_draft_version=result['draft_version'],
                expected_backlog_revision=result['backlog_revision']))
            session['receipts'][request] = dict(payload_sha256=digest(storage._request_json(payload)),result=result)
        state['transaction_revision'] += 1
        after = md.encode_state(state,previous=context['docs'],previous_state=context['baseline'],
                                extensions={key:{md.HANDOFF_EXTENSION:links}},handoff_evidence=raw_map)
        changes = {path:raw for path,raw in after.items() if context['files'].get(path) != raw}
        expected = {path:None if path not in context['files'] else digest(context['files'][path]) for path in changes}
        changes[relative] = encoded(session); expected[relative] = digest(before)
        tx.publish(store.path,changes,expected).raise_for_error()
    return sid,key,packets,links


class HistoricalHandoffStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'Café Store'
        self.sid,self.key,self.packets,self.links = seed(self.root)
        self.store = storage.Store(self.root,observer=ACTOR)

    def show(self):
        with storage.Store(self.root,observer=ACTOR).transaction() as state:
            return copy.deepcopy(state)

    def files(self):
        return {p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*')
                if p.is_file() and tx.JOURNAL not in p.parts}

    def refused(self,operation,code=None):
        with self.assertRaises(IdeaError) as caught: operation()
        if code is not None: self.assertEqual(caught.exception.code,code)

    def receipt_path(self):
        return self.root/'session-recovery'/(self.sid+'.json')

    def rewrite_packet(self,change,*,update_receipt=True):
        old = self.links[0]; packet = copy.deepcopy(self.packets[0]); change(packet)
        raw = codec.encode_record(packet); link = codec.record_link(packet,raw)
        detail_path = self.root/(self.key+'.md'); detail = md.parse_document(detail_path.read_bytes())
        detail.metadata['extensions'][md.HANDOFF_EXTENSION] = [link]
        # This controlled corruption fixture updates the generated body too,
        # avoiding a mere summary-label conflict masking the intended check.
        domain = dict(detail.metadata['idea'],revisions=self.show()['ideas'][self.key]['revisions'])
        detail_path.write_bytes(md.encode_detail(domain,notes=md.detail_notes(detail,baseline=
            md.parse_document(md.encode_detail(domain,extensions={md.HANDOFF_EXTENSION:[old]})).metadata),
            extensions={md.HANDOFF_EXTENSION:[link]},transaction_revision=detail.metadata['transaction_revision']))
        (self.root/link['path']).parent.mkdir(parents=True,exist_ok=True)
        (self.root/link['path']).write_bytes(raw)
        if old['path'] != link['path']: (self.root/old['path']).unlink()
        if update_receipt:
            session = decode(self.receipt_path().read_bytes())
            session['receipts'][self.packets[0]['request_id']]['result'].update(link)
            self.receipt_path().write_bytes(encoded(session))

    def test_readback_and_accessor_are_detached_memory_only_and_scoped(self):
        before = self.files(); original = self.show()
        with self.store.transaction() as state:
            with patch.object(storage,'read_bytes',side_effect=AssertionError('accessor performed I/O')):
                entries = self.store.handoffs(state,self.key,with_links=True)
                self.assertEqual(entries,[dict(record=self.packets[0],evidence=self.links[0])])
                entries[0]['record']['accepted'].clear(); entries[0]['evidence']['sha256'] = 'e'*64
                self.assertEqual(self.store.handoffs(state,self.key),self.packets)
            self.refused(lambda:self.store.handoffs(copy.deepcopy(state),self.key),'invalid_transaction')
            self.refused(lambda:self.store.handoffs(state,self.key,with_links=1))
        self.refused(lambda:self.store.handoffs(original,self.key),'invalid_transaction')
        self.assertEqual(self.files(),before)

    def test_ordinary_draft_commit_retains_historical_packet_and_index_bytes(self):
        before = self.files(); original = self.show()
        with self.store.transaction(write=True) as state:
            idea = state['ideas'][self.key]
            draft = dict(idea['workflow']['steps']['shape']['fields'],outcome='New unsaved outcome')
            state['ideas'][self.key] = save_draft(idea,'shape',draft,expected_revision=idea['revision'],
                expected_draft_version=idea['workflow']['draft_version'])['idea']
            self.store.commit(state)
        after = self.show()
        self.assertEqual(after['ideas'][self.key]['revision'],original['ideas'][self.key]['revision'])
        with self.store.transaction() as state: self.assertEqual(self.store.handoffs(state,self.key),self.packets)
        self.assertEqual((self.root/self.links[0]['path']).read_bytes(),before[self.links[0]['path']])
        self.assertEqual((self.root/'IDEAS.md').read_bytes(),before['IDEAS.md'])
        self.assertEqual(self.receipt_path().read_bytes(),before['session-recovery/'+self.sid+'.json'])

    def test_prepublication_mutable_hashes_are_historical_not_current_cas(self):
        detail_now = (self.root/(self.key+'.md')).read_bytes()
        self.assertNotEqual(digest(detail_now),self.packets[0]['source_files']['detail']['sha256'])
        self.rewrite_packet(lambda packet:packet['source_files']['detail'].update(sha256='d'*64))
        self.show()

    def test_new_accepted_revision_and_archival_preserve_old_packet_readability(self):
        before = (self.root/self.links[0]['path']).read_bytes()
        with self.store.transaction(write=True) as state:
            idea = state['ideas'][self.key]
            shaped = dict(idea['workflow']['steps']['shape']['fields'],outcome='Accepted new slice')
            state['ideas'][self.key] = accept(idea,'shape',shaped)['idea']
            self.store.commit(state)
        with self.store.transaction() as state:
            self.assertGreater(state['ideas'][self.key]['revision'],self.packets[0]['source_revision'])
            self.assertEqual(self.store.handoffs(state,self.key),self.packets)
        # Trusted domain fixture, not a claim that plan validation was executed.
        with self.store.transaction(write=True) as state:
            idea = state['ideas'][self.key]; plan_id = 'plan_'+'7'*32
            content = '# Frozen fixture plan\n'
            idea['plans'].append(dict(plan_id=plan_id,idea_id=self.key,idea_revision=idea['revision'],
                path=str(self.root/'plan-evidence'/(plan_id+'.md')),source_path=str(self.root/'working-plan.md'),
                content=content,sha256=digest(content.encode()),actor=ACTOR,timestamp='2026-10-02',
                validation=dict(builtin='idea-trace-and-sections-v1')))
            idea['status'] = 'archived'
            state['archives'][self.key+'/r'+str(idea['revision'])+'.json'] = dict(
                idea_id=self.key,origin=copy.deepcopy(idea['origin']),revision=copy.deepcopy(idea['revisions'][-1]))
            self.store.commit(state)
        with self.store.transaction() as state:
            self.assertEqual(state['ideas'][self.key]['status'],'archived')
            self.assertEqual(self.store.handoffs(state,self.key),self.packets)
        self.assertEqual((self.root/self.links[0]['path']).read_bytes(),before)

    def test_exact_publishing_receipt_fields_payload_and_counter_tampering_fail(self):
        original = self.receipt_path().read_bytes(); request = self.packets[0]['request_id']
        changes = [lambda r:r['receipts'].pop(request),
            lambda r:r['receipts'][request].update(payload_sha256='0'*64),
            lambda r:r['receipts'][request]['result'].update(handoff_index=2),
            lambda r:r['receipts'][request]['result'].update(handoff_id='handoff_'+'f'*32),
            lambda r:r['receipts'][request]['result'].update(revision=99),
            lambda r:r['receipts'][request]['result'].update(write_state='no_op'),
            lambda r:r['receipts'][request]['result'].update(idea_id='idea_'+'e'*32),
            lambda r:r['receipts'][request]['result'].update(prompt='Forbidden receipt body')]
        for change in changes:
            record = decode(original); change(record); self.receipt_path().write_bytes(encoded(record))
            self.refused(self.show,'corrupt_store')
            self.receipt_path().write_bytes(original)

    def test_only_named_sessions_read_and_missing_original_session_refuses(self):
        unrelated = self.store.create_session()
        (self.root/'session-recovery'/(unrelated+'.json')).write_bytes(b'unrelated malformed record')
        with patch.object(self.store,'_read_receipts',wraps=self.store._read_receipts) as reads:
            with self.store.transaction() as state: self.assertEqual(self.store.handoffs(state,self.key),self.packets)
        self.assertEqual([call.args[0] for call in reads.call_args_list],[self.sid])
        self.receipt_path().unlink()
        self.refused(self.show,'receipt_session_missing')

    def test_revision_hash_and_origin_tampering_fail_with_valid_packet_bytes(self):
        changes = [lambda p:p['source_files']['revision'].update(sha256='e'*64)]
        saved = self.files()
        for change in changes:
            self.rewrite_packet(change)
            self.refused(self.show,'corrupt_store')
            self.restore(saved)
        def change_origin(packet):
            packet['origin']['text'] = 'Different immutable origin'
            packet['origin']['sha256'] = digest(packet['origin']['text'].encode())
            source = {name:packet[name] for name in ('idea_id','source_revision','origin','accepted','placement','design_set')}
            packet['source_digest'] = codec._digest(source)
        self.rewrite_packet(change_origin)
        self.refused(self.show,'corrupt_store')

    def test_self_consistent_historical_root_reads_but_is_not_current_location(self):
        self.rewrite_packet(lambda p:[entry.update(path=entry['path'].replace(str(self.root),'/historical/Store'))
                                     for entry in p['source_files'].values()])
        with self.store.transaction() as state:
            packet = self.store.handoffs(state,self.key)[0]
        observed = copy.deepcopy(packet['source_files'])
        for entry in observed.values(): entry['path'] = entry['path'].replace('/historical/Store',str(self.root))
        self.refused(lambda:codec.verify_current_paths(packet,observed),'stale_source')
        self.assertTrue(codec.recorded_packet_path(packet,codec.record_link(packet,codec.encode_record(packet))).startswith('/historical/Store/'))

    def restore(self,files):
        for p in self.root.rglob('*'):
            if p.is_file() and p.relative_to(self.root).as_posix() not in files: p.unlink()
        for relative,raw in files.items(): (self.root/relative).write_bytes(raw)

    def test_accepted_snapshot_identity_refuses_canonical_packet_with_new_attribution(self):
        def change(packet):
            packet['accepted']['method']['acceptance']['actor'] = 'Different actor'
            source = {name:packet[name] for name in ('idea_id','source_revision','origin','accepted','placement','design_set')}
            packet['source_digest'] = codec._digest(source)
        self.rewrite_packet(change)
        self.refused(self.show,'corrupt_store')

    def test_omitted_reordered_modified_and_orphan_links_fail_disk_inventory(self):
        second_root = Path(self.temp.name)/'two packets'
        _,key,_,links = seed(second_root,count=2)
        detail_path = second_root/(key+'.md'); before = detail_path.read_bytes()
        document = md.decode_detail(before)
        for altered in (links[:1],list(reversed(links)),[],[dict(links[0],handoff_id='handoff_'+'e'*32),links[1]]):
            metadata = copy.deepcopy(document.metadata); metadata['extensions'][md.HANDOFF_EXTENSION] = altered
            detail_path.write_bytes(md.encode_document(metadata,md._detail_summary(metadata)+md.detail_notes(document)+md.NOTES_END))
            self.refused(lambda:self.read_root(second_root),'corrupt_store')
            detail_path.write_bytes(before)
        orphan = second_root/'history'/key/'metadata'/('f'*64+'.md')
        orphan.write_bytes(b'Unknown unlinked bytes')
        self.refused(lambda:self.read_root(second_root),'corrupt_store')

    def read_root(self,root):
        with storage.Store(root).transaction(): pass

    def test_packet_and_receipt_cas_detect_external_edits_before_commit(self):
        for kind in ('packet','receipt'):
            saved = self.files()
            with self.store.transaction(write=True) as state:
                path = self.root/self.links[0]['path'] if kind == 'packet' else self.receipt_path()
                path.write_bytes(path.read_bytes()+b'\n')
                self.refused(lambda:self.store.commit(state),'save_conflict')
            self.restore(saved)

    def test_empty_legacy_packet_inventory_is_unchanged(self):
        root = Path(self.temp.name)/'legacy'; root.mkdir()
        state = storage.empty_state(); (root/'state.json').write_bytes(encoded(state))
        with storage.Store(root).transaction() as observed:
            self.assertEqual(observed,state)
        self.assertFalse((root/'IDEAS.md').exists())


def publication_seed(root, *, large=False):
    root = Path(root); workspace = root.parent/(root.name+' Workspace')
    workspace.mkdir(parents=True,exist_ok=True)
    store = storage.Store(root,observer=ACTOR); sid = store.create_session()
    values = fields(); values['capture']['workspace']['path'] = str(workspace.resolve())
    if large: values['shape']['assumptions'] = ['Bounded assumption '+str(n)+'x'*60000 for n in range(20)]
    idea = original_idea()
    idea = capture_workflow(idea,values['capture'],new_capture=True,actor='operator',timestamp=STAMP,
        evidence_id='capture-fixture',source_digest=source_digest('capture',1,{'capture':values['capture']}))['idea']
    for step in ('priorities','shape','method','visualize','assess'): idea = accept(idea,step,values[step])['idea']
    receipt = idea['workflow']['steps']['assess']['acceptance']; position = idea['workflow']['steps']['assess']['fields']['position']
    placement = dict(idea_id=idea['idea_id'],idea_revision=receipt['accepted_revision'],position=1,
        reason='Accepted placement',actor=receipt['actor'],timestamp=receipt['timestamp'],
        source_backlog_revision=1,accepted_backlog_revision=2,neighbors=copy.deepcopy(position['neighbors']),
        snapshot=dict(ratings=copy.deepcopy(idea['ratings']),assessments=copy.deepcopy(idea['assessments'])))
    with store.transaction(write=True) as state:
        state.update(dict(schema_version=1,transaction_revision=0,backlog_revision=2,
            ideas={idea['idea_id']:idea},order=[idea['idea_id']],placements=[placement],archives={}))
        store.commit(state)
    return sid,idea['idea_id'],workspace


def publication_payload(store,key,request='handoff-publish-1'):
    with store.transaction() as state:
        idea = state['ideas'][key]
        return dict(operation='handoff',payload=dict(request_id=request,idea_id=key,
            expected_revision=idea['revision'],expected_draft_version=idea['workflow']['draft_version'],
            expected_backlog_revision=state['backlog_revision']))


HANDOFF_CHILD = r'''import os,sys
sys.path.insert(0,sys.argv[1]);sys.path.insert(0,sys.argv[2])
import idea_store as storage
import idea_handoff_evidence as codec
from test_handoff_store import publication_payload,ACTOR
root,sid,key,phase=sys.argv[3:7]
original=storage.transactions.publish
def publish(*args,**kwargs):
    def cut(actual):
        if (actual==phase or phase=='evidence' and actual.startswith('published:history/')
            or phase=='detail' and actual.startswith('published:idea_')
            or phase=='receipt' and actual.startswith('published:session-recovery/')): os._exit(73)
    return original(*args,**kwargs,_checkpoint=cut)
storage.transactions.publish=publish
store=storage.Store(root,observer=ACTOR)
payload=publication_payload(store,key)
store.publish_handoff(sid,payload['payload']['request_id'],payload,codec.build_record)
'''


class HandoffPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'Publish café'
        self.sid,self.key,self.workspace = publication_seed(self.root)
        self.store = storage.Store(self.root,observer=ACTOR)

    def files(self,root=None):
        root = self.root if root is None else root
        return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*')
                if p.is_file() and tx.JOURNAL not in p.parts}

    def show(self):
        with self.store.transaction() as state: return copy.deepcopy(state)

    def publish(self,request='handoff-publish-1',payload=None,factory=codec.build_record):
        payload = publication_payload(self.store,self.key,request) if payload is None else payload
        return self.store.publish_handoff(self.sid,request,payload,factory)

    def refused(self,operation,code=None):
        with self.assertRaises(IdeaError) as caught: operation()
        if code is not None: self.assertEqual(caught.exception.code,code)

    def test_atomic_publication_retains_index_and_accepted_counters_with_exact_preimages(self):
        before = self.files(); original = self.show(); result = self.publish(); after = self.files()
        self.assertEqual(result['write_state'],'applied'); self.assertEqual(result['handoff_index'],1)
        expected = copy.deepcopy(original); expected['transaction_revision'] += 1
        self.assertEqual(self.show(),expected)
        changed = {p for p in set(before)|set(after) if before.get(p) != after.get(p)}
        self.assertEqual(changed,{self.key+'.md',result['path'],'session-recovery/'+self.sid+'.json'})
        packet = codec.decode_record(after[result['path']])
        for name,relative in (('detail',self.key+'.md'),('index','IDEAS.md'),
                              ('revision','history/'+self.key+'/r'+str(result['revision'])+'.md')):
            self.assertEqual(packet['source_files'][name],dict(path=str(self.root/relative),sha256=digest(before[relative])))
        self.assertEqual(self.store.request_result(self.sid,result['request_id']),result)
        self.assertNotIn('prompt',result)
        with self.store.transaction() as state: self.assertEqual(self.store.handoffs(state,self.key),[packet])

    def test_replay_after_lost_response_restart_drift_and_workspace_removal_is_historical(self):
        payload = publication_payload(self.store,self.key); result = self.publish(payload=payload)
        with self.store.transaction(write=True) as state:
            idea = state['ideas'][self.key]
            state['ideas'][self.key] = accept(idea,'shape',dict(idea['workflow']['steps']['shape']['fields'],outcome='New slice'))['idea']
            self.store.commit(state)
        self.workspace.rmdir(); self.store = storage.Store(self.root,observer=ACTOR)
        before = self.files()
        with (patch.object(storage,'now',side_effect=AssertionError('Replay clock')),
              patch.object(storage,'uuid') as packet_uuid):
            packet_uuid.uuid4.side_effect = AssertionError('Replay packet UUID')
            self.assertEqual(self.publish(payload=payload,factory=lambda *a,**k:self.fail('Replay factory')),result)
        self.assertEqual(self.files(),before)
        self.refused(lambda:self.publish(request='new-after-drift'),'not_ready')
        changed = copy.deepcopy(payload); changed['payload']['expected_backlog_revision'] += 1
        self.refused(lambda:self.publish(payload=changed),'request_conflict')

    def test_new_unchanged_source_reuses_original_packet_and_witness_without_clock(self):
        first = self.publish(); before = self.files(); original = self.show()
        with (patch.object(storage,'now',side_effect=AssertionError('Reuse clock')),
              patch.object(storage,'uuid') as packet_uuid):
            packet_uuid.uuid4.side_effect = AssertionError('Reuse packet UUID')
            second = self.publish('handoff-publish-2',factory=lambda *a,**k:self.fail('Reuse factory'))
        self.assertEqual(second['write_state'],'no_op')
        self.assertEqual({name:second[name] for name in ('handoff_id','path','sha256','handoff_index')},
                         {name:first[name] for name in ('handoff_id','path','sha256','handoff_index')})
        after = self.files()
        self.assertEqual({p for p in after if after[p] != before.get(p)},{'session-recovery/'+self.sid+'.json'})
        self.assertEqual(self.show(),original)
        with self.store.transaction() as state:
            packets = self.store.handoffs(state,self.key); self.assertEqual(len(packets),1)
            self.assertEqual(packets[0]['request_id'],first['request_id'])

    def test_new_cas_and_workspace_refusals_leave_all_bytes_unchanged(self):
        payload = publication_payload(self.store,self.key)
        for name,code in (('expected_revision','stale_revision'),('expected_draft_version','stale_draft_version'),
                          ('expected_backlog_revision','stale_backlog')):
            changed = copy.deepcopy(payload); changed['payload'][name] += 1; before = self.files()
            self.refused(lambda:self.publish(payload=changed),code); self.assertEqual(self.files(),before)
        self.workspace.rmdir(); before = self.files()
        self.refused(self.publish,'workspace_unavailable'); self.assertEqual(self.files(),before)

    def test_factory_is_detached_pure_and_cannot_change_generated_observations(self):
        before = self.files()
        def mutate(state,idea,**kwargs):
            result = codec.build_record(state,idea,**kwargs); state['transaction_revision'] += 1; return result
        self.refused(lambda:self.publish(factory=mutate),'invalid_handler'); self.assertEqual(self.files(),before)
        def wrong(state,idea,**kwargs):
            result = codec.build_record(state,idea,**kwargs); result['actor'] = 'Other'; return result
        self.refused(lambda:self.publish(factory=wrong),'invalid_handler'); self.assertEqual(self.files(),before)
        def nested(state,idea,**kwargs):
            with self.store.transaction(): pass
        self.refused(lambda:self.publish(factory=nested),'store_busy'); self.assertEqual(self.files(),before)

    def test_full_actual_prompt_capacity_is_refused_before_publication(self):
        root = Path(self.temp.name)/'Large packet'
        sid,key,_ = publication_seed(root,large=True); store = storage.Store(root,observer=ACTOR)
        payload = publication_payload(store,key); before = self.files(root)
        self.refused(lambda:store.publish_handoff(sid,payload['payload']['request_id'],payload,codec.build_record),'handoff_capacity')
        self.assertEqual(self.files(root),before)

    def test_historical_http_envelope_capacity_refuses_before_any_publication(self):
        before = self.files(); preflight = self.store._handoff_http_preflight
        reached = []
        def boundary(packet,link,result):
            absolute = codec.recorded_packet_path(packet,link)
            handoff = dict(handoff_id=packet['handoff_id'],source_revision=packet['source_revision'],
                source_digest=packet['source_digest'],path=absolute,sha256=link['sha256'],
                prompt=codec.render_prompt(packet,absolute),source_files=packet['source_files'],design_set=packet['design_set'])
            current = dict(result,handoff=handoff,handoff_current=True)
            historical = dict(result,code='historical_handoff',handoff=handoff,handoff_current=False)
            limit = len(storage._request_json(current))
            self.assertGreater(len(storage._request_json(historical)),limit)
            reached.append(True)
            with patch.object(storage,'MAX_INPUT',limit): preflight(packet,link,result)
        with patch.object(self.store,'_handoff_http_preflight',side_effect=boundary):
            self.refused(self.publish,'handoff_capacity')
        self.assertEqual(reached,[True]); self.assertEqual(self.files(),before)

    def test_detail_source_drift_before_commit_refuses_packet_link_and_receipt(self):
        before = self.files(); detail = self.root/(self.key+'.md')
        preflight = self.store._handoff_http_preflight
        def drift(packet,link,result):
            preflight(packet,link,result)
            detail.write_bytes(before[self.key+'.md']+b'\nExternal source change\n')
        with patch.object(self.store,'_handoff_http_preflight',side_effect=drift):
            self.refused(self.publish,'save_conflict')
        after = self.files()
        self.assertEqual(set(after),set(before))
        self.assertEqual({p for p in after if after[p] != before[p]},{self.key+'.md'})
        detail.write_bytes(before[self.key+'.md'])
        self.assertEqual(self.files(),before)

    def test_link_receipt_and_global_capacity_refusals_preserve_bytes(self):
        before = self.files()
        receipt_path = 'session-recovery/'+self.sid+'.json'
        existing_bytes = sum(map(len,before.values()))
        for owner,constant,limit,code in ((md,'MAX_HANDOFF_LINKS',0,'handoff_capacity'),
            (storage,'MAX_SESSION_RECEIPTS',0,'receipt_capacity_exhausted'),
            (storage,'MAX_STORE_BYTES',existing_bytes+1,'too_large'),
            (storage,'MAX_RECEIPT_BYTES',len(before[receipt_path])+1,'receipt_capacity_exhausted')):
            payload = publication_payload(self.store,self.key)
            observed = self.show(); built = []
            def factory(state,idea,**kwargs):
                built.append(True)
                return codec.build_record(state,idea,**kwargs)
            with patch.object(owner,constant,limit):
                self.assertEqual(self.show(),observed)
                self.refused(lambda:self.publish(payload=payload,factory=factory),code)
            if constant in ('MAX_STORE_BYTES','MAX_RECEIPT_BYTES'):
                self.assertEqual(built,[True], 'Capacity fixture must reach prospective publication')
            self.assertEqual(self.files(),before)

    def accepted_design(self):
        data = 'Real accepted design notes café\n'.encode(); number = '9'*32
        intent = dict(schema_version=1,kind='upload-intent',idea_id=self.key,source_revision=6,actor=ACTOR,
            timestamp=STAMP,upload_id='upload_'+number,asset_id='asset_'+number,session_id=self.sid,
            name='Design café.txt',declared_type='text/plain',size=len(data))
        self.store.mutate_assets(self.sid,'design-intent',{'operation':'fixture-intent'},lambda s:{'idea_id':self.key},
                                 prepare_records=lambda s:[intent])
        blob = self.root/assets.blob_path(intent['asset_id']); blob.parent.mkdir(parents=True,exist_ok=True); blob.write_bytes(data)
        complete = dict(intent,kind='asset',blob_path=assets.blob_path(intent['asset_id']),validated_type='text/plain',sha256=digest(data))
        self.store.mutate_assets(self.sid,'design-complete',{'operation':'fixture-complete'},lambda s:{'idea_id':self.key},
                                 prepare_records=lambda s:[complete])
        def prepare(state):
            source = {}
            for step in ('capture','shape'):
                record = state['ideas'][self.key]['workflow']['steps'][step]; revision = record['acceptance']['accepted_revision']
                source[step] = dict(revision=revision,digest=source_digest(step,revision,{step:record['fields']}))
            return [dict(schema_version=1,kind='design-set',idea_id=self.key,source_revision=state['ideas'][self.key]['revision'],
                actor=ACTOR,timestamp=STAMP,set_id='set_'+number,session_id=self.sid,source=source,
                source_digest=assets.source_digest(source),members=[dict(asset_id=complete['asset_id'],name=complete['name'],
                    type=complete['validated_type'],size=complete['size'],sha256=complete['sha256'])])]
        def accept_set(state):
            idea = state['ideas'][self.key]; visual = dict(idea['workflow']['steps']['visualize']['fields'],
                disposition='accepted_set',reason=None,design_set_id='set_'+number)
            state['ideas'][self.key] = accept(idea,'visualize',visual)['idea']; return {'idea_id':self.key}
        self.store.mutate_assets(self.sid,'design-accept',{'operation':'fixture-set'},accept_set,prepare_records=prepare)
        return blob,data,complete

    def test_real_design_set_blobs_publish_rehash_and_remain_historical_after_edit(self):
        blob,data,complete = self.accepted_design()
        result = self.publish(); packet_raw = (self.root/result['path']).read_bytes(); packet = codec.decode_record(packet_raw)
        self.assertEqual(packet['design_set']['members'][0],dict(asset_id=complete['asset_id'],name=complete['name'],
            type='text/plain',size=len(data),sha256=digest(data),path=str(blob)))
        with self.store.transaction(write=True) as state:
            idea = state['ideas'][self.key]
            state['ideas'][self.key] = accept(idea,'shape',dict(idea['workflow']['steps']['shape']['fields'],outcome='New design slice'))['idea']
            self.store.commit(state)
        with storage.Store(self.root).transaction() as state:
            self.assertEqual(storage.Store(self.root).path,self.store.path)
            self.assertEqual(state['ideas'][self.key]['revision'],packet['source_revision']+1)
        with self.store.transaction() as state: self.assertEqual(self.store.handoffs(state,self.key),[packet])
        self.assertEqual((self.root/result['path']).read_bytes(),packet_raw)
        blob.write_bytes(data+b'changed')
        self.refused(self.show,'corrupt_store')

    def test_moved_store_history_and_real_assets_survive_but_require_explicit_new_packet(self):
        for has_assets in (False,True):
            with self.subTest(assets=has_assets):
                self.root = Path(self.temp.name)/('Before assets' if has_assets else 'Before plain')
                self.sid,self.key,self.workspace = publication_seed(self.root)
                self.store = storage.Store(self.root,observer=ACTOR)
                if has_assets: blob,data,_ = self.accepted_design()
                payload = publication_payload(self.store,self.key)
                original = self.show(); first = self.publish(payload=payload); before = self.files()
                packet = codec.decode_record(before[first['path']]); old_root = self.root
                moved = old_root.with_name(old_root.name+' moved'); old_root.rename(moved)
                self.root = moved; self.store = storage.Store(moved,observer=ACTOR)
                self.assertEqual(self.files(),before)
                self.assertEqual(self.store.request_result(self.sid,first['request_id']),first)
                self.assertEqual(self.publish(payload=payload,factory=lambda *a,**k:self.fail('Historical replay built')),first)
                with self.store.transaction() as state:
                    self.assertEqual(self.store.handoffs(state,self.key),[packet])
                    observations = self.store.handoff_observations(state,self.key)
                    self.refused(lambda:codec.verify_current(packet,state,state['ideas'][self.key],
                        observations['design_set'],source_files=observations['source_files']),'stale_source')
                    self.assertEqual(state['ideas'][self.key]['origin'],original['ideas'][self.key]['origin'])
                link = {name:first[name] for name in ('handoff_id','path','sha256')}
                self.assertEqual(codec.recorded_packet_path(packet,link),str(old_root/first['path']))
                second = self.publish('moved-new-packet')
                self.assertEqual(second['write_state'],'applied'); self.assertEqual(second['handoff_index'],2)
                self.assertNotEqual(second['handoff_id'],first['handoff_id'])
                with self.store.transaction() as state:
                    packets = self.store.handoffs(state,self.key); self.assertEqual(packets[0],packet)
                    observed = self.store.handoff_observations(state,self.key)
                    self.assertEqual(codec.verify_current(packets[1],state,state['ideas'][self.key],
                        observed['design_set'],source_files=observed['source_files']),packets[1])
                    self.assertEqual(packets[1]['source_revision'],packet['source_revision'])
                    self.assertEqual(packets[1]['accepted'],packet['accepted'])
                    self.assertEqual(packets[1]['source_files']['detail']['path'],str(moved.resolve()/(self.key+'.md')))
                after = self.files()
                for relative,raw in before.items():
                    if relative.startswith(('assets/','history/')): self.assertEqual(after[relative],raw)
                self.assertEqual(self.store.request_result(self.sid,first['request_id']),first)
                if has_assets:
                    moved_blob = moved/assets.blob_path(packet['design_set']['members'][0]['asset_id'])
                    self.assertEqual(moved_blob.read_bytes(),data)
                    self.assertEqual(packets[1]['design_set']['members'][0]['sha256'],digest(data))
                    moved_blob.write_bytes(data+b'tampered')
                    self.refused(self.show,'corrupt_store')

    def test_safe_store_respelling_uses_canonical_observations_without_rewriting_history(self):
        first = self.publish(); before = self.files()
        spelling = self.root.parent/'Spelling'; spelling.mkdir()
        self.store = storage.Store(spelling/'..'/self.root.name,observer=ACTOR)
        with self.store.transaction() as state:
            packet = self.store.handoffs(state,self.key)[0]
            observed = self.store.handoff_observations(state,self.key)
            self.assertEqual(codec.verify_current(packet,state,state['ideas'][self.key],
                observed['design_set'],source_files=observed['source_files']),packet)
            self.assertEqual(observed['source_files']['detail']['path'],str(self.root.resolve()/(self.key+'.md')))
        self.assertEqual(self.files(),before)
        reused = self.publish('respelled-reuse')
        self.assertEqual(reused['write_state'],'no_op'); self.assertEqual(reused['handoff_id'],first['handoff_id'])
        self.assertEqual((self.root/first['path']).read_bytes(),before[first['path']])

    @unittest.skipUnless(os.name == 'posix','Symlink refusal uses native POSIX qualification')
    def test_original_store_alias_symlink_is_refused_before_canonical_observations(self):
        self.publish(); before = self.files()
        alias = self.root.parent/'Unsafe alias'; alias.symlink_to(self.root,target_is_directory=True)
        for path in (alias,alias/'..'/self.root.name):
            self.refused(lambda:storage.Store(path,observer=ACTOR).create_session(),'corrupt_store')
        self.assertEqual(self.files(),before)

    @unittest.skipUnless(os.name == 'posix','Process exit fault fixtures use native POSIX qualification')
    def test_real_process_exit_recovers_exact_packet_link_receipt_without_duplicate(self):
        scripts = str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'); tests = str(Path(__file__).resolve().parent)
        for phase in ('prepared','evidence','detail','receipt','verified','complete'):
            with self.subTest(phase=phase):
                root = Path(self.temp.name)/('Crash '+phase); sid,key,_ = publication_seed(root)
                before = self.files(root)
                child = subprocess.run([sys.executable,'-c',HANDOFF_CHILD,scripts,tests,str(root),sid,key,phase],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=False)
                self.assertEqual(child.returncode,73,child.stderr.decode())
                store = storage.Store(root,observer=ACTOR)
                with store.transaction() as state:
                    entries = store.handoffs(state,key,with_links=True); self.assertEqual(len(entries),1)
                    self.assertEqual(state['ideas'][key]['revision'],entries[0]['record']['source_revision'])
                recovered = store.request_result(sid,'handoff-publish-1'); stable = self.files(root)
                payload = publication_payload(store,key)
                self.assertEqual(store.publish_handoff(sid,'handoff-publish-1',payload,
                    lambda *a,**k:self.fail('Recovered receipt called factory')),recovered)
                self.assertEqual(self.files(root),stable)
                self.assertEqual(stable['IDEAS.md'],before['IDEAS.md'])
                self.assertFalse(any((root/tx.JOURNAL).iterdir()))


if __name__ == '__main__':
    unittest.main()
