"""agent policy against real Service/Store and bounded Broker."""
import copy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
from idea_bridge import ApplicationBinding, BridgeError, RequestInfo
from idea_domain import IdeaError
from idea_proposals import Broker
from idea_service import Service, TrustedContext
from idea_sessions import AgentBinding, SessionPolicy
from idea_store import Store
from idea_workflow import source_digest


class AgentPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.workspace=self.root/'workspace';self.workspace.mkdir()
        self.store=Store(self.root/'ideas',observer='Operator')
        self.sid=self.store.create_session();self.now=0.;self.records={};self.cancelled=[]
        self.broker=Broker(clock=lambda:self.now,validate_source=lambda _,source:source,
                           persist_proposal=lambda _:dict(proposal_id='proposal_fixture',sha256='a'*64))
        self.policy=self.make_policy()
        self.bid=self.policy.open_binding('Operator',self.sid)
        self.credentials=self.policy.agent_credentials(self.bid)

    def factory(self,record):
        return Service(self.store,{},TrustedContext(record['actor'],record['receipt_session_id'],record['selected_idea_id']))

    def persist(self,record):self.records[record['binding_id']]=copy.deepcopy(record)

    def cancel(self,bid,generation):
        self.cancelled.append((bid,generation));self.broker.cancel(bid,generation)

    def make_policy(self,**options):
        values=dict(namespace='fixture',clock=lambda:self.now,cancel=self.cancel,
                    agent_open=self.broker.open,agent_state=self.broker.status)
        values.update(options)
        return SessionPolicy(self.factory,self.persist,**values)

    def request(self,headers=None):
        return RequestInfo('POST','/agent/v1/events',
                           {k.lower():v for k,v in (headers or {}).items()},'http://127.0.0.1:1234')

    def agent_request(self,credentials=None,**headers):
        value=credentials or self.credentials
        base={'X-Idea-Agent-Binding':value['binding_id'],
              'X-Idea-Agent-Generation':value['generation'],
              'Authorization':'Bearer '+value['token']}
        base.update(headers);return self.request(base)

    def pair(self):
        code=self.policy.issue_pairing(self.bid)
        response=self.policy.pair(self.request(),{'code':code})
        self.cookie=response.headers['Set-Cookie'].split(';')[0]
        self.csrf=response.body['csrf_token'];self.tab=response.body['tab_secret']
        self.browser=self.request({'X-Idea-Binding':self.bid,'Cookie':self.cookie,'X-CSRF-Token':self.csrf,'X-Idea-Tab':self.tab})
        self.binding=self.policy.authorize(self.browser,write=True)
        return code

    def code(self,code,fn):
        with self.assertRaises((BridgeError,IdeaError)) as caught:fn()
        self.assertEqual(caught.exception.code,code)

    def context(self):return self.policy.authorize_agent(self.agent_request())

    def pending(self):
        binding=self.context().binding
        captured=binding.application.capture(dict(request_id='capture',raw_text='Original words',
            workspace=dict(name='Fixture',path=str(self.workspace),confirmed=True)))
        with binding.lock:self.policy.after_application(binding,captured)
        source=dict(accepted_revision=1,draft_version=0,data={'capture':{'raw_text':'Original words'}})
        envelope=dict(request_id='proposal1',idea_id=captured['idea_id'],expected_revision=1,
            expected_draft_version=0,operation='exploration',source_digest=source_digest('exploration',1,source['data']))
        self.broker.enqueue(self.bid,self.credentials['generation'],envelope,source)
        return captured

    def test_authorize_before_pair_frozen_context_has_no_credentials(self):
        context=self.context()
        self.assertIsInstance(context,AgentBinding)
        self.assertEqual(context.session_id,self.sid)
        self.assertEqual(context.generation,self.credentials['generation'])
        self.assertNotIn(self.credentials['token'],repr(context))
        self.assertNotIn('application=',repr(context))
        self.assertEqual(set(context.__dataclass_fields__),{'binding','binding_id','generation','session_id'})
        with self.assertRaises(FrozenInstanceError):context.generation='changed'
        with context.binding.lock:self.assertIs(self.policy.recheck_agent(context),context.binding)
        self.code('browser_unauthorized',lambda:self.policy.authorize(self.request({'X-Idea-Binding':self.bid})))

    def test_missing_malformed_and_browser_credentials_refused(self):
        for name in ('Authorization','X-Idea-Agent-Binding','X-Idea-Agent-Generation'):
            for value in (None,True,'','bad','é'):
                with self.subTest(name=name,value=value):
                    self.code('agent_unauthorized',lambda:self.policy.authorize_agent(self.agent_request(**{name:value})))
        for auth in ('bearer '+self.credentials['token'],'Bearer '+self.credentials['token']+' ',
                     'Bearer '+self.credentials['generation'],'Bearer '+'f'*64):
            self.code('agent_unauthorized',lambda:self.policy.authorize_agent(self.agent_request(Authorization=auth)))
        for name in ('Cookie','Origin','X-CSRF-Token','X-Idea-Binding'):
            self.code('agent_browser_headers_refused',lambda:self.policy.authorize_agent(self.agent_request(**{name:''})))
        self.pair()
        self.code('agent_unauthorized',lambda:self.policy.authorize_agent(self.agent_request(Authorization='Bearer '+self.csrf)))
        self.code('agent_browser_headers_refused',lambda:self.policy.authorize_agent(self.browser))

    def test_cross_binding_and_generation_refused(self):
        sid=self.store.create_session();bid=self.policy.open_binding('Operator',sid)
        other=self.policy.agent_credentials(bid)
        self.code('agent_unauthorized',lambda:self.policy.authorize_agent(self.agent_request(**{'X-Idea-Agent-Binding':bid})))
        self.code('agent_unauthorized',lambda:self.policy.authorize_agent(self.agent_request(**{'X-Idea-Agent-Generation':other['generation']})))
        context=self.context()
        for changed in (replace(context,binding_id=bid),replace(context,generation=other['generation']),
                        replace(context,session_id=sid),replace(context,binding=ApplicationBinding(context.binding.application))):
            self.code('agent_unauthorized',lambda:self.policy.recheck_agent(changed))

    def test_close_agent_preserves_browser_and_actual_draft_write(self):
        self.pair();captured=self.pending();context=self.context()
        before=copy.deepcopy(self.records[self.bid]);cookie,csrf=self.cookie,self.csrf
        result=self.policy.close_agent(context)
        self.assertEqual(result['agent_status'],'disconnected')
        self.assertEqual(self.broker.status(self.bid,context.generation)['agent_status'],'disconnected')
        self.code('agent_unauthorized',lambda:self.policy.authorize_agent(self.agent_request()))
        self.code('agent_unauthorized',lambda:self.policy.recheck_agent(context))
        self.code('browser_unauthorized',lambda:self.policy.agent_credentials(self.bid))
        self.assertIs(self.policy.authorize(self.browser,write=True),self.binding)
        with self.binding.lock:
            draft=self.binding.application.draft(dict(request_id='draft-after-close',idea_id=captured['idea_id'],
                expected_revision=1,expected_draft_version=0,step='priorities',fields={'urgency':6,'importance':None}))
            session=self.policy.session(self.binding)
        self.assertEqual(draft['draft_version'],1);self.assertEqual(draft['revision'],1)
        self.assertEqual(session['csrf_token'],csrf);self.assertEqual(self.cookie,cookie)
        self.assertEqual(self.records[self.bid],before)
        self.assertEqual(session['agent_status'],'disconnected')
        self.assertFalse(session['capabilities']['agent'])
        self.assertEqual(self.binding.application.state()['drafts']['priorities'],{'urgency':6,'importance':None})

    def test_pair_replay_and_full_revoke_cancel_all_access(self):
        code=self.pair();self.pending();context=self.context()
        self.code('pairing_replay_session_invalidated',lambda:self.policy.pair(self.request(),{'code':code}))
        self.assertIn((self.bid,context.generation),self.cancelled)
        self.code('browser_unauthorized',lambda:self.policy.authorize(self.browser,write=True))
        self.code('agent_unauthorized',lambda:self.policy.recheck_agent(context))
        self.assertEqual(self.broker.status(self.bid,context.generation)['agent_status'],'disconnected')
        self.policy.open_binding('Operator',self.sid,binding_id=self.bid,resume=True)
        self.credentials=self.policy.agent_credentials(self.bid);context=self.context()
        self.policy.revoke(self.bid)
        self.code('agent_unauthorized',lambda:self.policy.recheck_agent(context))

    def test_resume_rotates_and_old_captured_context_fails_recheck(self):
        self.pair();captured=self.pending();old=self.context();credentials=copy.deepcopy(self.credentials)
        self.policy.open_binding('Operator',self.sid,captured['idea_id'],binding_id=self.bid,resume=True)
        self.credentials=self.policy.agent_credentials(self.bid)
        self.assertIs(self.context().binding,old.binding)
        self.assertEqual(self.context().session_id,old.session_id)
        self.assertNotEqual(self.credentials['token'],credentials['token'])
        self.code('agent_unauthorized',lambda:self.policy.authorize_agent(self.agent_request(credentials)))
        self.code('agent_unauthorized',lambda:self.policy.recheck_agent(old))
        self.code('agent_unauthorized',lambda:self.policy.close_agent(old))
        self.assertEqual(self.context().binding.application.context.selected_idea_id,captured['idea_id'])

    def test_real_broker_public_projection_no_secrets_and_no_heartbeat_renewal(self):
        self.pair();public=self.policy.session(self.binding)
        self.assertEqual(public['agent_status'],'connected');self.assertTrue(public['capabilities']['agent'])
        self.assertEqual(public['resume'],dict(required=False,reason=None))
        for value in (self.credentials['token'],self.credentials['generation']):self.assertNotIn(value,json.dumps(public))
        for _ in range(7):self.now+=5;public=self.policy.session(self.binding)
        self.assertEqual(public['agent_status'],'disconnected')
        self.assertEqual(public['resume'],dict(required=True,reason='agent_disconnected'))
        self.assertFalse(public['capabilities']['agent'])
        self.assertEqual(self.binding.application.state()['revision'],0)

    def test_idle_projection_and_default_policy_remain_disconnected(self):
        self.pair()
        for _ in range(26):
            self.now+=24
            if self.now<600:self.broker.events(self.bid,self.credentials['generation'],self.sid,0,0)
        self.assertEqual(self.policy.session(self.binding)['agent_status'],'paused')
        self.assertEqual(self.policy.session(self.binding)['resume']['reason'],'agent_paused')
        policy=self.make_policy(agent_open=None,agent_state=None)
        bid=policy.open_binding('Operator',self.sid)
        code=policy.issue_pairing(bid);response=policy.pair(self.request(),{'code':code})
        browser=self.request({'X-Idea-Binding':bid,'Cookie':response.headers['Set-Cookie'].split(';')[0],'X-Idea-Tab':response.body['tab_secret']})
        self.assertEqual(policy.session(policy.authorize(browser))['agent_status'],'disconnected')

    def test_status_callback_strict_schema_and_typed_redacted_errors(self):
        self.pair();baseline=self.broker.status(self.bid,self.credentials['generation'])
        malformed=[dict(baseline,token='private'),dict(baseline,agent_status='private'),dict(baseline,sequence=True),
                   dict(baseline,session_id='session_'+'f'*32),dict(baseline,reason='private'),
                   dict(baseline,agent_status='paused',reason=None),dict(baseline,ok=1),{'agent_status':'connected'}]
        for value in malformed:
            self.policy.agent_state=lambda *_,value=value:value
            self.code('invalid_agent_state',lambda:self.policy.session(self.binding))
        def failed(*_):raise RuntimeError('private token/path')
        self.policy.agent_state=failed
        self.code('agent_state_failed',lambda:self.policy.session(self.binding))

    def test_open_callback_failure_no_ghost_or_exposed_credentials(self):
        calls=[]
        def failed(bid,generation,sid):
            self.broker.open(bid,generation,sid);calls.append((bid,generation));raise RuntimeError('private')
        policy=self.make_policy(agent_open=failed)
        self.code('agent_open_failed',lambda:policy.open_binding('Operator',self.sid))
        self.assertEqual(policy._entries,{})
        self.assertEqual(self.broker.status(*calls[0])['agent_status'],'disconnected')
        self.code('browser_unauthorized',lambda:policy.agent_credentials(calls[0][0]))
        old=self.context();self.policy.agent_open=failed
        self.code('agent_open_failed',lambda:self.policy.open_binding('Operator',self.sid,binding_id=self.bid,resume=True))
        self.code('agent_unauthorized',lambda:self.policy.recheck_agent(old))
        self.code('browser_unauthorized',lambda:self.policy.agent_credentials(self.bid))

    def test_callbacks_outside_registry_under_binding(self):
        events=[]
        def check_locks(bid,*_):
            entry=self.policy._entries[bid]
            self.assertTrue(entry.binding.lock._is_owned())
            acquired=[]
            def worker():
                success=self.policy._registry.acquire(timeout=.5);acquired.append(success)
                if success:self.policy._registry.release()
            thread=threading.Thread(target=worker);thread.start();thread.join(1)
            self.assertEqual(acquired,[True]);events.append(True)
        original=self.policy.agent_open
        def opened(bid,generation,sid):check_locks(bid);return original(bid,generation,sid)
        self.policy.agent_open=opened
        self.policy.open_binding('Operator',self.sid,binding_id=self.bid,resume=True)
        self.credentials=self.policy.agent_credentials(self.bid);self.pair()
        original_state=self.policy.agent_state
        def state(bid,generation):check_locks(bid);return original_state(bid,generation)
        self.policy.agent_state=state
        self.policy.session(self.binding)
        original_cancel=self.policy.cancel
        def cancelled(bid,generation):check_locks(bid);return original_cancel(bid,generation)
        self.policy.cancel=cancelled;self.policy.close_agent(self.context())
        self.assertEqual(len(events),3)

    def test_recheck_serializes_with_close_from_other_thread(self):
        context=self.context();waiting=threading.Event();errors=[]
        def check():
            waiting.set()
            try:self.policy.recheck_agent(context)
            except BridgeError as exc:errors.append(exc.code)
        with context.binding.lock:
            thread=threading.Thread(target=check);thread.start();self.assertTrue(waiting.wait(1))
            self.policy.close_agent(context)
        thread.join(2);self.assertFalse(thread.is_alive());self.assertEqual(errors,['agent_unauthorized'])

    def test_close_cancellation_failure_keeps_browser_but_agent_revoked(self):
        self.pair();context=self.context()
        def failed(*_):raise RuntimeError('private')
        self.policy.cancel=failed
        self.code('agent_cancel_failed',lambda:self.policy.close_agent(context))
        self.code('agent_unauthorized',lambda:self.policy.recheck_agent(context))
        self.assertIs(self.policy.authorize(self.browser),self.binding)

    def test_constructor_and_context_validation(self):
        for values in ({'agent_open':True},{'agent_state':{}},{'cancel':'bad'}):
            self.code('invalid_policy',lambda:self.make_policy(**values))
        for context in (None,True,{},replace(self.context(),generation='bad'),replace(self.context(),session_id='bad')):
            self.code('agent_unauthorized',lambda:self.policy.recheck_agent(context))

    # A human saving, pairing or pinging in the browser keeps the agent; polling alone does not.
    def human(self):
        self.policy=self.make_policy(agent_activity=self.broker.touch)
        self.bid=self.policy.open_binding('Operator',self.sid)
        self.credentials=self.policy.agent_credentials(self.bid)
        self.pair()

    def poll_until(self,end,save_at=None,ping_at=None):
        while self.now<end:
            self.now+=20
            if self.status()!='connected':return
            if save_at is not None and self.now>=save_at:
                save_at=None
                captured=self.binding.application.capture(dict(request_id='capture-'+str(int(self.now)),raw_text='Human words',
                    workspace=dict(name='Fixture',path=str(self.workspace),confirmed=True)))
                self.saved(captured)
            if ping_at is not None and self.now>=ping_at:
                ping_at=None
                with self.binding.lock:self.assertEqual(self.policy.activity(self.binding),dict(ok=True,code='ok'))
            self.broker.events(self.bid,self.credentials['generation'],self.sid,0,0)

    def status(self):return self.policy.session(self.binding)['agent_status']

    def saved(self,result):
        # The bridge's contract for a successful browser write: persist the selection, then
        # report human activity (the bridge alone decides what counts; see test_bridge).
        with self.binding.lock:
            self.policy.after_application(self.binding,result)
            self.policy.activity(self.binding)

    def test_human_save_resets_idle(self):
        self.human()
        self.poll_until(1000,save_at=500)
        self.assertEqual(self.status(),'connected')

    def test_typing_ping_resets_idle(self):
        self.human()
        self.poll_until(1000,ping_at=500)
        self.assertEqual(self.status(),'connected')

    def test_ping_without_activity_callback_is_inert(self):
        self.human()
        self.policy.agent_activity=None
        self.poll_until(1000,ping_at=500)
        self.assertEqual(self.status(),'paused')

    def test_ping_reaches_the_broker_only_while_an_agent_is_live(self):
        calls=[]
        self.policy=self.make_policy(agent_activity=lambda b,g:calls.append((b,g)))
        self.bid=self.policy.open_binding('Operator',self.sid)
        self.credentials=self.policy.agent_credentials(self.bid)
        self.pair()
        calls.clear()
        with self.binding.lock:self.policy.activity(self.binding)
        self.assertEqual(calls,[(self.bid,self.credentials['generation'])])
        self.policy.close_agent(self.context())
        calls.clear()
        with self.binding.lock:self.assertEqual(self.policy.activity(self.binding),dict(ok=True,code='ok'))
        self.assertEqual(calls,[])

    def test_polling_alone_still_pauses_at_idle(self):
        self.human()
        self.poll_until(1200)
        self.assertEqual(self.status(),'paused')

    def test_save_after_idle_never_revives(self):
        self.human()
        self.poll_until(700)
        self.assertEqual(self.status(),'paused')
        captured=self.binding.application.capture(dict(request_id='late',raw_text='Late words',
            workspace=dict(name='Fixture',path=str(self.workspace),confirmed=True)))
        self.saved(captured)
        self.assertEqual(self.status(),'paused')

    def test_pairing_resets_idle(self):
        self.policy=self.make_policy(agent_activity=self.broker.touch)
        self.bid=self.policy.open_binding('Operator',self.sid)
        self.credentials=self.policy.agent_credentials(self.bid)
        for _ in range(25):
            self.now+=20;self.broker.events(self.bid,self.credentials['generation'],self.sid,0,0)
        self.pair()
        self.poll_until(1000)
        self.assertEqual(self.status(),'connected')

    def test_persisting_the_selection_alone_never_moves_the_idle_clock(self):
        # Receipt and state reads also persist the selection; only the bridge's write rule counts.
        self.human()
        self.poll_until(500)
        captured=self.binding.application.capture(dict(request_id='read-back',raw_text='Human words',
            workspace=dict(name='Fixture',path=str(self.workspace),confirmed=True)))
        with self.binding.lock:self.policy.after_application(self.binding,captured)
        self.poll_until(700)
        self.assertEqual(self.status(),'paused')


if __name__=='__main__':unittest.main()
