"""Protected handoff links and existing journal confinement. Operator.

Pure source paths are fixtures. Journal checks use only owned temporary files.
"""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
import idea_handoff_evidence as evidence
import idea_markdown as md
import idea_platform as platform
import idea_transactions as tx
from idea_domain import IdeaError, digest
from test_handoff_evidence import fixture as packet_fixture
from test_markdown import metadata_fixture

EXT = md.HANDOFF_EXTENSION


def linked_state(numbers=(1,), user=None):
    observed, idea, _, prototype = packet_fixture()
    state = dict(observed, schema_version=1, archives={})
    links, raw_map = [], {}
    for number in numbers:
        record = dict(prototype, handoff_id='handoff_'+format(number, '032x'),
                      request_id='packet-'+str(number))
        raw = evidence.encode_record(record); link = evidence.record_link(record, raw)
        links.append(link); raw_map[link['path']] = raw
    key = idea['idea_id']
    extensions = dict(user or {}, **{EXT: links})
    files = md.encode_state(state, notes={key:'Preserved Notes\r\n'},
                            extensions={key:extensions}, handoff_evidence=raw_map)
    return state, files, links, raw_map


def documents(files):
    return {path:md.parse_document(raw) for path,raw in files.items()
            if path == 'IDEAS.md' or (path.startswith('idea_') and '/' not in path)}


class HandoffLinkTests(unittest.TestCase):
    def refused(self, callback, code=None):
        with self.assertRaises(IdeaError) as caught:
            callback()
        if code is not None:
            self.assertEqual(caught.exception.code, code)

    def test_full_state_preserves_domain_and_traverses_protected_packets(self):
        state, files, links, raw_map = linked_state(numbers=(1,2))
        key = state['order'][0]
        self.assertEqual(md.decode_state(files),state)
        detail = md.decode_detail(files[key+'.md'])
        self.assertEqual(md.handoff_links(detail.metadata['extensions'],key),links)
        self.assertEqual(set(detail.metadata['metadata_evidence']),{'proposals','plans','executions'})
        self.assertNotIn(EXT,detail.metadata['idea'])
        self.assertIn('Planning handoff 2',detail.body)
        for link in links:
            self.assertIn(link['path'],detail.body)
            self.assertEqual(files[link['path']],raw_map[link['path']])

    def test_missing_packets_fail_encode_decode_and_import(self):
        state,files,links,raw_map = linked_state(); key = state['order'][0]
        self.refused(lambda:md.encode_state(state,extensions={key:{EXT:links}}),'corrupt_store')
        self.refused(lambda:md.encode_state(state,previous=documents(files),previous_state=state),'corrupt_store')
        broken = dict(files); del broken[links[0]['path']]
        for check_body in (True,False):
            self.refused(lambda:md.decode_state(broken,check_body=check_body),'corrupt_store')

    def test_byte_hash_body_identity_and_same_path_foreign_bytes_refuse(self):
        state,files,links,raw_map = linked_state(); key = state['order'][0]
        changed = dict(raw_map); changed[links[0]['path']] += b'Injected body\n'
        self.refused(lambda:md.encode_state(state,extensions={key:{EXT:links}},handoff_evidence=changed))
        self.refused(lambda:md.decode_state(dict(files,**changed),check_body=False),'corrupt_store')
        # Even re-addressing changed bytes cannot turn a noncanonical packet into evidence.
        raw = changed[links[0]['path']]; sha = digest(raw)
        link = dict(links[0],sha256=sha,path='history/'+key+'/metadata/'+sha+'.md')
        self.refused(lambda:md.encode_state(state,extensions={key:{EXT:[link]}},
                                          handoff_evidence={link['path']:raw}),'corrupt_store')
        other = evidence.decode_record(raw_map[links[0]['path']])
        other['handoff_id'] = 'handoff_'+'f'*32
        foreign_raw = evidence.encode_record(other)
        self.refused(lambda:md.decode_state(dict(files,**{links[0]['path']:foreign_raw}),check_body=False),'corrupt_store')

    def test_foreign_idea_link_and_canonical_foreign_record_refuse(self):
        state,files,links,raw_map = linked_state(); key = state['order'][0]
        foreign_key = 'idea_'+'f'*32
        foreign = evidence.decode_record(raw_map[links[0]['path']])
        foreign['idea_id'] = foreign_key
        for entry in ('detail','revision'):
            foreign['source_files'][entry]['path'] = foreign['source_files'][entry]['path'].replace(key,foreign_key)
        # The public builder verifies source-state association; use another real reducer fixture.
        observed,idea,design,prototype = packet_fixture()
        idea['idea_id'] = foreign_key
        observed['ideas'] = {foreign_key:idea}; observed['order'] = [foreign_key]
        observed['placements'][0]['idea_id'] = foreign_key
        foreign = evidence.build_record(observed,idea,source_files=foreign['source_files'],design_set=design,
            handoff_id=prototype['handoff_id'],session_id=prototype['session_id'],
            request_id=prototype['request_id'],actor=prototype['actor'],timestamp=prototype['timestamp'])
        raw = evidence.encode_record(foreign); link = evidence.record_link(foreign,raw)
        self.refused(lambda:md.handoff_links({EXT:[link]},key),'corrupt_store')
        # Smuggle canonical foreign bytes through a syntactically local link.
        local = dict(link,path=link['path'].replace(foreign_key,key))
        self.refused(lambda:md.encode_state(state,extensions={key:{EXT:[local]}},
                                          handoff_evidence={local['path']:raw}),'corrupt_store')
        detail = md.parse_document(files[key+'.md'])
        detail.metadata['extensions'][EXT] = [local]
        broken = dict(files); del broken[links[0]['path']]
        broken[key+'.md'] = md.encode_document(detail.metadata,detail.body); broken[local['path']] = raw
        self.refused(lambda:md.decode_state(broken,check_body=False),'corrupt_store')

    def test_link_schema_duplicates_and_capacity_are_bounded_detached(self):
        state,_,links,_ = linked_state(numbers=(1,2)); key = state['order'][0]
        first,second = links
        for value in ([first,first],[first,dict(second,handoff_id=first['handoff_id'])],
                      [dict(first,token='bad')],[dict(first,path='../outside.md')],
                      [dict(first,sha256='A'*64)],[dict(first,handoff_id=True)],None,{},'list',[None]):
            self.refused(lambda value=value:md.handoff_links({EXT:value},key))
        refs = [dict(handoff_id='handoff_'+format(n,'032x'),sha256=format(n,'064x'),
                     path='history/'+key+'/metadata/'+format(n,'064x')+'.md') for n in range(129)]
        extension = {EXT:refs[:128],'custom':['Keep']}; before = copy.deepcopy(extension)
        checked = md.handoff_links(extension,key); checked[0]['sha256'] = 'e'*64
        self.assertEqual(extension,before)
        self.assertEqual(len(checked),128)
        self.refused(lambda:md.handoff_links({EXT:refs},key),'handoff_capacity')

    def test_append_preserves_notes_user_extensions_old_packets_and_index(self):
        state,before,links,raw_map = linked_state(user={'custom':{'label':'Keep'}})
        key = state['order'][0]
        record = evidence.decode_record(raw_map[links[0]['path']])
        record.update(handoff_id='handoff_'+'e'*32,request_id='append')
        raw = evidence.encode_record(record); link = evidence.record_link(record,raw)
        changed = copy.deepcopy(state); changed['transaction_revision'] += 1
        extension = {'custom':{'label':'Keep'},EXT:links+[link]}
        after = md.encode_state(changed,extensions={key:extension},previous=documents(before),
            previous_state=state,handoff_evidence=dict(raw_map,**{link['path']:raw}))
        self.assertEqual(md.decode_state(after),changed)
        detail = md.decode_detail(after[key+'.md'])
        self.assertEqual(md.detail_notes(detail),'Preserved Notes\r\n')
        self.assertEqual(detail.metadata['extensions'],extension)
        for path,existing in raw_map.items(): self.assertEqual(after[path],existing)
        # The codec emits the index counter; Store's existing unchanged-index retention is separate.
        self.assertEqual(md.decode_index(after['IDEAS.md']).body,md.decode_index(before['IDEAS.md']).body)
        unchanged = md.encode_state(state,previous=documents(before),previous_state=state,handoff_evidence=raw_map)
        self.assertEqual(unchanged,before)

    def test_remove_reorder_modify_and_omit_extension_refuse(self):
        state,files,links,raw_map = linked_state(numbers=(1,2)); key = state['order'][0]
        previous = documents(files)
        for refs in (links[:1],list(reversed(links)),[],[dict(links[0],handoff_id='handoff_'+'e'*32),links[1]]):
            self.refused(lambda refs=refs:md.encode_state(state,extensions={key:{EXT:refs}},
                previous=previous,previous_state=state,handoff_evidence=raw_map),'corrupt_store')
        self.refused(lambda:md.encode_detail(state['ideas'][key],extensions={},previous=previous[key+'.md']),'corrupt_store')

    def test_maps_bytes_and_read_aggregate_caps_refuse_before_traversal(self):
        state,files,links,raw_map = linked_state(); key = state['order'][0]
        for supplied in ([],{False:next(iter(raw_map.values()))},{links[0]['path']:'text'},
                         dict(raw_map,**{'history/'+key+'/metadata/'+'e'*64+'.md':b'orphan'})):
            self.refused(lambda supplied=supplied:md.encode_state(state,extensions={key:{EXT:links}},handoff_evidence=supplied))
        self.refused(lambda:md.encode_state(state,handoff_evidence=raw_map),'corrupt_store')
        for constant,limit in (('MAX_HANDOFF_EVIDENCE_FILES',0),('MAX_HANDOFF_EVIDENCE_BYTES',1)):
            with patch.object(md,constant,limit):
                self.refused(lambda:md.encode_state(state,extensions={key:{EXT:links}},handoff_evidence=raw_map),'too_large')
                self.refused(lambda:md.decode_state(files,check_body=False),'too_large')

    def test_generated_labels_and_yaml_comments_preserved_as_conflicts(self):
        state,files,links,raw_map = linked_state(); key = state['order'][0]
        broken = dict(files); broken[key+'.md'] = broken[key+'.md'].replace(b'Planning handoff 1',b'Changed label')
        self.refused(lambda:md.decode_state(broken),'generated_body_conflict')
        self.assertEqual(md.decode_state(broken,check_body=False),state)
        commented = dict(files); commented[key+'.md'] = commented[key+'.md'].replace(b'---\n',b'---\n# preserve me\n',1)
        self.refused(lambda:md.encode_state(state,previous=documents(commented),previous_state=state,
                                           handoff_evidence=raw_map),'yaml_comments')

    def test_absent_extension_preserves_legacy_bytes_and_three_metadata_types(self):
        state = metadata_fixture(); key = state['order'][0]
        options = dict(notes={key:'Legacy Notes\r\n'},extensions={key:{'custom':[1,'x']}})
        plain = md.encode_state(state,**options)
        self.assertEqual(md.encode_state(state,**options,handoff_evidence={}),plain)
        self.assertEqual(md.encode_state(state,previous=documents(plain),previous_state=state),plain)
        self.assertEqual(md.decode_state(plain),state)
        self.assertNotIn('Planning handoff',md.decode_detail(plain[key+'.md']).body)
        self.assertEqual(md.METADATA_TYPES,dict(proposals='proposal',plans='plan',executions='execution'))


class HandoffJournalTests(unittest.TestCase):
    def test_existing_namespace_and_real_immutable_cas_refusals(self):
        _,_,links,raw_map = linked_state(); path = links[0]['path']; raw = raw_map[path]
        self.assertTrue(tx._allowed(path)); self.assertFalse(tx._mutable(path))
        self.assertEqual(tx._limit(path),md.MAX_STATE)
        for bad in ('../'+path,'/'+path,path.upper(),path.replace('/metadata/','/handoffs/'),path.replace('.md','.json')):
            self.assertFalse(tx._allowed(bad))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with platform.store_lock(root/'.lock'):
                tx.publish(root,{path:raw},{path:None}).raise_for_error()
                before = (root/path).read_bytes()
                with self.assertRaises(IdeaError) as stale:
                    tx.publish(root,{path:raw},{path:None})
                self.assertEqual(stale.exception.code,'save_conflict')
                with self.assertRaises(IdeaError) as collision:
                    tx.publish(root,{path:raw+b'foreign'},{path:digest(raw)})
                self.assertEqual(collision.exception.code,'save_conflict')
                self.assertEqual((root/path).read_bytes(),before)
                with self.assertRaises(IdeaError):
                    tx.publish(root,{'../foreign.md':raw},{'../foreign.md':None})
                self.assertEqual((root/path).read_bytes(),before)


if __name__ == '__main__':
    unittest.main()
