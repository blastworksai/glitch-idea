"""Real-subprocess `sessions` and `session-discard` verbs, and the session list on a capacity refusal."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0,str(SCRIPTS))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import idea
import idea_launch as launch
from idea_runtime import RuntimeError as OwnerError
import test_session_slots as slots

WARNING='This session is still open: its browser tab and terminal agent will stop working. Run again with --confirm to discard it.'
ROW_KEYS={'binding_id','selected_idea_id','title','finished','in_use'}


@unittest.skipUnless(os.name=='posix','Native owner ACLs remain unqualified')
class SessionVerbTests(unittest.TestCase):
    setUp=slots.SessionSlotsTests.setUp
    seed_finished=slots.SessionSlotsTests.seed_finished
    start=slots.SessionSlotsTests.start
    go=slots.SessionSlotsTests.go
    eight=slots.SessionSlotsTests.eight
    listed=slots.SessionSlotsTests.listed

    def cli(self,verb,*extra,root=None,expect=None):
        argv=[sys.executable,str(SCRIPTS/'idea.py'),'--store',str(self.store),verb,
              '--runtime-root',str(root or self.root),*map(str,extra)]
        result=subprocess.run(argv,capture_output=True,timeout=15)
        self.assertEqual(result.stderr,b'','structured stdout only')
        body=json.loads(result.stdout)
        self.assertEqual(result.returncode,0 if body['ok'] else 1,body)
        if expect is not None: self.assertEqual(body['ok'],expect,body)
        return body

    def test_sessions_lists_bounded_rows_read_only(self):
        self.seed_finished();self.go()
        made=self.eight(selected={2:self.key})
        before={p.name:p.read_bytes() for p in self.bindings.glob('binding_*.json')}
        body=self.cli('sessions',expect=True)
        self.assertEqual(set(body),{'ok','sessions'})
        self.assertEqual(len(body['sessions']),8)
        self.assertTrue(all(set(row)==ROW_KEYS for row in body['sessions']))
        by={row['binding_id']:row for row in body['sessions']}
        self.assertEqual((by[made[2].bid]['finished'],by[made[2].bid]['selected_idea_id']),(True,self.key))
        self.assertEqual({p.name:p.read_bytes() for p in self.bindings.glob('binding_*.json')},before)

    def test_sessions_without_a_running_service_returns_owner_unavailable(self):
        body=self.cli('sessions',expect=False)
        self.assertEqual(body['error']['code'],'owner_unavailable')

    def test_discard_in_use_needs_confirm_and_warns(self):
        self.go()
        made=self.eight(idle=False)
        target=made[0].bid
        body=self.cli('session-discard','--binding',target,expect=False)
        self.assertEqual(body['error']['code'],'session_in_use')
        self.assertEqual(body['warning'],WARNING)
        self.assertIn(target,self.listed())
        body=self.cli('session-discard','--binding',target,'--confirm',expect=True)
        self.assertEqual(body,dict(ok=True,binding_id=target,was_in_use=True))
        self.assertNotIn(target,self.listed());self.assertEqual(len(self.listed()),7)

    def test_discard_idle_needs_no_confirm(self):
        self.go()
        made=self.eight(idle=True)
        body=self.cli('session-discard','--binding',made[1].bid,expect=True)
        self.assertEqual(body,dict(ok=True,binding_id=made[1].bid,was_in_use=False))

    def test_discard_unknown_and_malformed(self):
        self.go()
        self.eight()
        body=self.cli('session-discard','--binding','binding_'+'0'*32,expect=False)
        self.assertEqual(body['error']['code'],'binding_not_found');self.assertNotIn('warning',body)
        body=self.cli('session-discard','--binding','nope',expect=False)
        self.assertEqual(body['error']['code'],'invalid_input')
        self.assertEqual(len(self.listed()),8)

    def test_discard_makes_room_for_a_new_open(self):
        self.go()
        made=self.eight(idle=True)
        with self.assertRaises(OwnerError) as caught: self.client.open_binding('new')
        self.assertEqual(caught.exception.code,'binding_capacity')
        self.cli('session-discard','--binding',made[4].bid,expect=True)
        self.assertEqual(len(self.client.open_binding('new')['binding_id'])>0,True)

    def test_capacity_refusal_json_carries_sessions_and_no_secret(self):
        self.go()
        made=self.eight(idle=False)
        for argv in (('session-open',),('browser-open','--browser','system')):
            body=self.cli(*argv,expect=False)
            self.assertEqual(body['error'],dict(code='binding_capacity',message='binding_capacity'))
            self.assertEqual(len(body['sessions']),8)
            self.assertTrue(all(set(row)==ROW_KEYS for row in body['sessions']))
            self.assertEqual({row['binding_id'] for row in body['sessions']},{s.bid for s in made})
            text=json.dumps(body)
            for secret in ('pairing','token','secret','credential','csrf','cookie','generation'):
                self.assertNotIn(secret,text.lower())
            self.assertEqual(set(body),{'ok','error','sessions'})

    def test_other_launcher_error_shapes_are_unchanged(self):
        with patch.object(launch,'open_session',side_effect=OwnerError('owner_unavailable')):
            status,body=self.invoke_local('session-open')
        self.assertEqual(body,dict(ok=False,error=dict(code='owner_unavailable',message='owner_unavailable')))
        # A capacity refusal whose list is absent or malformed carries no sessions at all.
        for sessions in (None,[{'binding_id':'x'}],'bad'):
            with patch.object(launch,'open_session',side_effect=OwnerError('binding_capacity',sessions=sessions)):
                status,body=self.invoke_local('session-open')
            self.assertEqual(body,dict(ok=False,error=dict(code='binding_capacity',message='binding_capacity')))

    def invoke_local(self,verb):
        import io
        class Out:
            def __init__(self): self.buffer=io.BytesIO()
        out=Out()
        with patch.object(idea.sys,'stdout',out):
            status=idea.main(['--store',str(self.store),verb,'--runtime-root',str(self.root)])
        return status,json.loads(out.buffer.getvalue())


class DocTruthTests(unittest.TestCase):
    ROOT=Path(__file__).resolve().parents[1]

    def test_docs_state_the_new_rule_and_the_verbs(self):
        commands=(self.ROOT/'glitch-idea/references/commands.md').read_text()
        for needle in ('`sessions`','`session-discard --binding binding_ID','--confirm','`session_in_use`','`binding_capacity`'):
            self.assertIn(needle,commands)
        self.assertNotIn('never evicts another binding',commands)
        runbook=(self.ROOT/'docs/browser-runbook.md').read_text()
        self.assertNotIn('refuses a new one rather than evicting',runbook)
        self.assertIn('Review',runbook)

    def test_warning_text_is_identical_in_the_docs_and_the_helper(self):
        self.assertEqual(idea.DISCARD_WARNING,WARNING)
        self.assertIn(WARNING,(self.ROOT/'glitch-idea/references/commands.md').read_text())


if __name__=='__main__':unittest.main()
