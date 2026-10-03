"""explicit navigation evidence and durable Pause receipt tests."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
import idea_store as storage
from idea_domain import IdeaError
from idea_service import Service, TrustedContext
from idea_workflow import navigate_step, derive_state
from test_workflow import complete, original_idea


class NavigationTests(unittest.TestCase):
    def assert_code(self,code,callback):
        with self.assertRaises(IdeaError) as caught: callback()
        self.assertEqual(caught.exception.code,code)

    def test_pure_navigation_changes_only_current_step_and_preserves_review(self):
        idea=complete(); prior=copy.deepcopy(idea)
        result=navigate_step(idea,'shape',expected_revision=idea['revision'],expected_draft_version=idea['workflow']['draft_version'])
        expected=copy.deepcopy(prior); expected['workflow']['current_step']='shape'
        self.assertEqual(result['idea'],expected); self.assertTrue(result['changed'])
        self.assertFalse(result['accepted_changed']); self.assertEqual(result['invalidated'],[])
        self.assertEqual(idea,prior)
        handoff=dict(handoff_id='handoff-fixture',source_revision=idea['revision'])
        before=derive_state(prior,handoff); after=derive_state(result['idea'],handoff)
        self.assertEqual(before['accepted'],after['accepted'])
        self.assertEqual(before['steps']['review'],after['steps']['review'])
        again=navigate_step(result['idea'],'shape',expected_revision=idea['revision'],expected_draft_version=idea['workflow']['draft_version'])
        self.assertFalse(again['changed']); self.assertEqual(again['idea'],expected)

    def test_pure_strict_step_and_counter_cas(self):
        idea=complete(); draft=idea['workflow']['draft_version']
        for step in ('unknown',None,True,[]):
            self.assert_code('invalid_input',lambda:navigate_step(idea,step,expected_revision=idea['revision'],expected_draft_version=draft))
        for rev,value,code in ((True,draft,'invalid_input'),(idea['revision'],True,'invalid_input'),(1,draft,'stale_revision'),(idea['revision'],draft+1,'stale_draft_version')):
            self.assert_code(code,lambda:navigate_step(idea,'capture',expected_revision=rev,expected_draft_version=value))

    def test_legacy_navigation_never_fabricates_acceptance_or_history(self):
        idea=original_idea()
        same=navigate_step(idea,'capture',expected_revision=1,expected_draft_version=0)
        self.assertFalse(same['changed']); self.assertEqual(same['idea'],idea)
        changed=navigate_step(idea,'priorities',expected_revision=1,expected_draft_version=0)['idea']
        self.assertEqual(changed['revisions'],idea['revisions'])
        self.assertEqual(changed['workflow']['draft_version'],0)
        self.assertTrue(all(record['acceptance'] is None for record in changed['workflow']['steps'].values()))

    def test_service_restart_receipt_replay_before_cas_noop_and_exact_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'ideas'; workspace=Path(directory)/'workspace'; workspace.mkdir()
            store=storage.Store(root); sid=store.create_session()
            service=Service(store,{},TrustedContext('operator',sid))
            captured=service.capture(dict(request_id='capture',raw_text='Exact words\r\n',workspace=dict(name='Fixture',path=str(workspace),confirmed=True)))
            key=captured['idea_id']; index=(root/'IDEAS.md').read_bytes()
            history={path:path.read_bytes() for path in (root/'history').rglob('*.md')}
            payload=dict(request_id='pause-1',idea_id=key,expected_revision=1,expected_draft_version=0,step='capture')
            result=service.navigate(payload)
            self.assertEqual(result['write_state'],'applied'); self.assertEqual(result['revision'],1); self.assertEqual(result['draft_version'],0)
            self.assertEqual((root/'IDEAS.md').read_bytes(),index)
            self.assertEqual({path:path.read_bytes() for path in history},history)
            restarted=Service(storage.Store(root),{},TrustedContext('operator',sid,key))
            self.assertEqual(restarted.state()['current_step'],'capture')
            same=restarted.navigate(dict(payload,request_id='same-step'))
            self.assertEqual(same['write_state'],'no_op')
            edit=dict(request_id='draft',idea_id=key,expected_revision=1,expected_draft_version=0,step='priorities',fields={'urgency':7,'importance':None})
            restarted.draft(edit)
            self.assertEqual(restarted.navigate(payload),result)
            self.assertEqual(restarted.state()['current_step'],'priorities')
            self.assert_code('stale_draft_version',lambda:restarted.navigate(dict(payload,request_id='stale')))
            self.assert_code('request_conflict',lambda:restarted.navigate(dict(payload,step='review')))
            with store.transaction() as state:
                self.assertEqual(state['transaction_revision'],3)
                self.assertEqual(state['ideas'][key]['revisions'][0]['workflow']['current_step'],'priorities')

    def test_service_exact_envelope_and_missing_idea_refuse_without_write(self):
        with tempfile.TemporaryDirectory() as directory:
            store=storage.Store(Path(directory)); sid=store.create_session()
            service=Service(store,{},TrustedContext('operator',sid))
            payload=dict(request_id='navigate',idea_id='idea_'+'1'*32,expected_revision=1,expected_draft_version=0,step='capture')
            for field in ('actor','fields','expected_backlog_revision'):
                self.assert_code('invalid_input',lambda:service.navigate(dict(payload,**{field:None})))
            self.assert_code('not_found',lambda:service.navigate(payload))
            self.assert_code('request_not_found',lambda:service.request_result('navigate'))
            self.assertEqual(service.state()['revision'],0)


class RerankTests(unittest.TestCase):
    """Redesign R7, the owner verbatim: "I dont see how re-ranking would work"."""
    def setUp(self):
        from test_handoff_store import ACTOR, publication_seed
        self.directory=tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)/'ideas'
        sid,self.key,workspace=publication_seed(self.root)
        self.store=storage.Store(self.root,observer=ACTOR); self.service=Service(self.store,{},TrustedContext(ACTOR,sid))
        self.others=[self.service.capture(dict(request_id='capture-'+name,raw_text=name+' words',
            workspace=dict(name='Fixture',path=str(workspace),confirmed=True)))['idea_id'] for name in ('a','b')]

    def snapshot(self):
        with self.store.transaction() as state: return copy.deepcopy(state)

    def payload(self,**change):
        return dict(dict(request_id='rerank-1',idea_id=self.key,expected_backlog_revision=self.snapshot()['backlog_revision'],
                         position=3,reason=None),**change)

    def test_moving_an_idea_records_the_human_placement_and_refreshes_its_actual_position(self):
        before=self.snapshot(); self.assertEqual(before['order'],[self.key]+self.others)
        result=self.service.rerank(self.payload())
        after=self.snapshot()
        self.assertEqual(after['order'],self.others+[self.key])
        self.assertEqual((result['write_state'],result['backlog_revision']),('applied',before['backlog_revision']+1))
        self.assertEqual(after['placements'][-1]['reason'],'Re-ranked from the backlog')
        self.assertEqual(after['placements'][-1]['position'],3)
        workflow=after['ideas'][self.key]['workflow']
        self.assertEqual(derive_state(after['ideas'][self.key])['steps']['assess']['status'],'unsaved')
        self.assertEqual(workflow['drafts']['assess']['position']['actual_position'],3)
        # The AI proposed position is never touched by the human's move.
        self.assertEqual(workflow['drafts']['assess']['position']['proposed_position'],
                         before['ideas'][self.key]['workflow']['steps']['assess']['fields']['position']['proposed_position'])
        self.assertEqual(self.service.rerank(self.payload(expected_backlog_revision=before['backlog_revision'])),result,'receipt replay, no second move')
        self.assertEqual(self.snapshot()['order'],after['order'])

    def test_stale_backlog_range_and_shape_are_refused_without_a_write(self):
        before=self.snapshot()
        for change,code in ((dict(expected_backlog_revision=before['backlog_revision']-1),'stale_backlog'),
                            (dict(position=4),'invalid_input'),(dict(position=0),'invalid_input'),
                            (dict(reason='   '),'invalid_input'),(dict(idea_id='idea_'+'f'*32),'not_found')):
            with self.subTest(change=change):
                with self.assertRaises(IdeaError) as caught: self.service.rerank(self.payload(request_id='bad-'+str(len(change)),**change))
                self.assertEqual(caught.exception.code,code)
        with self.assertRaises(IdeaError): self.service.rerank(dict(self.payload(),extra=1))
        from unittest.mock import patch
        real=self.service._idea
        def archived(state,key):
            idea=real(state,key); return dict(idea,status='archived') if key==self.others[0] else idea
        with patch.object(self.service,'_idea',side_effect=archived):
            with self.assertRaises(IdeaError) as caught: self.service.rerank(self.payload(request_id='bad-archived',idea_id=self.others[0]))
        self.assertEqual(caught.exception.code,'idea_archived')
        self.assertEqual(self.snapshot()['order'],before['order']); self.assertEqual(self.snapshot()['backlog_revision'],before['backlog_revision'])


if __name__=='__main__': unittest.main()
