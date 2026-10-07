"""Two concurrent /glitch-idea sessions against ONE ideas store never leak into each other.

Layer 1 (real service): one OwnerService (real Store, Broker, SessionPolicy, loopback HTTP), two
bindings opened through the real Runtime, two paired browsers, two AgentClients, two ideas.
Layer 2 (in process): the same Broker + SessionPolicy + Store the service is built from, under a fake
clock, for exact refusal codes and lease/idle behaviour. No real sleeping beyond a 2 s wait in one test.
"""
import copy
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest

SCRIPTS=Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0,str(SCRIPTS))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from idea_agent_client import AgentClient
from idea_bridge import BridgeError, RequestInfo
from idea_domain import IdeaError
from idea_launch import OwnerService
from idea_proposals import Broker, HEARTBEAT, IDLE
from idea_runtime import Runtime
from idea_service import Service, TrustedContext, TrustedStepHandler
from idea_sessions import SessionPolicy
from idea_store import Store
from idea_workflow import source_digest
from test_agent_bridge import EXPLORATION
from test_workflow import fields


class Terminal:
    """One operator's terminal plus browser tab: its own binding, pairing, agent and idea."""
    def __init__(self, rig, text):
        self.rig=rig
        self.opened=rig.client.open_binding('new')
        self.bid,self.sid=self.opened['binding_id'],self.opened['session_id']
        status,body,cookie=rig.wire('/api/v1/pair',{'code':self.opened['pairing_code']},{'X-Idea-Binding':self.bid})
        assert status==200,body
        self.cookie=cookie.split(';',1)[0];self.csrf=body['csrf_token'];self.tab=body['tab_secret']
        self.idea_id=self.browser('capture',dict(request_id='capture-'+text[:3],raw_text=text,
            workspace=dict(name='Fixture',path=str(rig.workspace),confirmed=True)))['idea_id']
        self.agent=AgentClient(rig.client,self.sid)

    def browser(self,name,payload=None,expected=200,timeout=5):
        headers={'X-Idea-Binding':self.bid,'Cookie':self.cookie,'X-Idea-Tab':self.tab}
        if payload is not None:headers['X-CSRF-Token']=self.csrf
        status,body,_=self.rig.wire('/api/v1/'+name,payload,headers,timeout=timeout)
        assert status==expected,(name,status,body)
        return body

    def accept(self,step,value,request):
        state=self.browser('state')
        return self.browser('accept',dict(request_id=request,idea_id=self.idea_id,expected_revision=state['revision'],
            expected_draft_version=state['draft_version'],step=step,fields=value,proposal_id=None,expected_backlog_revision=None))

    def ready(self):
        self.accept('priorities',fields()['priorities'],'prio-'+self.idea_id[-4:])
        self.accept('method',fields()['method'],'meth-'+self.idea_id[-4:])

    def propose(self,request):
        source=self.browser('state')['proposal_sources']['discovery']['source']
        return self.browser('propose',dict(request_id=request,idea_id=self.idea_id,
            expected_revision=source['accepted_revision'],expected_draft_version=source['draft_version'],
            operation='discovery',source_digest=source['source_digest']))

    def headers(self):
        credentials=self.rig.owner.policy.agent_credentials(self.bid)
        return {'Authorization':'Bearer '+credentials['token'],'X-Idea-Agent-Binding':self.bid,
                'X-Idea-Agent-Generation':credentials['generation']}


@unittest.skipUnless(os.name=='posix','Native owner ACLs remain unqualified')
class TwoSessionsRealServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        base=Path(self.temp.name);self.store=base/'ideas';self.root=base/'private'
        self.workspace=base/'workspace';self.workspace.mkdir()
        runtime=Runtime(self.store,self.root).acquire_owner()
        self.owner=OwnerService(runtime);self.owner.start();self.addCleanup(self.owner.shutdown)
        self.owner.handlers.update({step:TrustedStepHandler(lambda *args:None) for step in ('method','discovery','exploration')})
        self.client=Runtime(self.store,self.root)
        # Both terminals open and pair before the handlers refresh; opening more bindings never disturbs these.
        self.a=Terminal(self,'Idea of terminal A');self.b=Terminal(self,'Idea of terminal B')
        self.a.ready();self.b.ready()

    def wire(self,path,payload=None,headers=None,*,timeout=5):
        conn=http.client.HTTPConnection('127.0.0.1',self.owner.server.server_port,timeout=timeout)
        h=dict(headers or {})
        if payload is not None:h.update({'Content-Type':'application/json','Origin':self.owner.server.origin})
        try:
            conn.request('POST' if payload is not None else 'GET',path,None if payload is None else json.dumps(payload),h)
            response=conn.getresponse();return response.status,json.loads(response.read()),response.getheader('Set-Cookie')
        finally:conn.close()

    def raw_agent(self,terminal,operation,session_id,payload=None):
        path='/agent/v1/'+operation+('?session_id=%s&after=0&timeout=0'%session_id if operation=='events' else '')
        headers=terminal.headers()
        if payload is not None:headers['Content-Type']='application/json'
        conn=http.client.HTTPConnection('127.0.0.1',self.owner.server.server_port,timeout=5)
        try:
            conn.request('GET' if operation=='events' else 'POST',path,None if payload is None else json.dumps(payload),headers)
            response=conn.getresponse();return response.status,json.loads(response.read())
        finally:conn.close()

    @staticmethod
    def correlation(event):return {k:v for k,v in event.items() if k not in ('data','sequence')}

    def test_two_terminals_hold_distinct_bindings_sessions_and_credentials(self):
        """Two terminals opened against one store get distinct bindings, receipt sessions and agent credentials."""
        self.assertNotEqual((self.a.bid,self.a.sid),(self.b.bid,self.b.sid))
        self.assertNotEqual(self.a.idea_id,self.b.idea_id)
        tokens={self.owner.policy.agent_credentials(t.bid)['token'] for t in (self.a,self.b)}
        self.assertEqual(len(tokens),2)
        self.assertEqual({self.a.bid,self.b.bid},{r['binding_id'] for r in self.client.list_bindings()})
        for terminal in (self.a,self.b):
            state=terminal.browser('state')
            self.assertEqual((state['session_id'],state['idea_id'],state['agent_status']),(terminal.sid,terminal.idea_id,'connected'))

    def test_each_agent_receives_only_its_own_request_even_with_the_same_request_id(self):
        """Two operators click Propose at once with the same request id and each terminal's agent sees only its own idea."""
        self.a.propose('proposal-1');self.b.propose('proposal-1')
        events_a=self.a.agent.events(timeout=0)['events'];events_b=self.b.agent.events(timeout=0)['events']
        self.assertEqual([(e['idea_id'],e['session_id']) for e in events_a],[(self.a.idea_id,self.a.sid)])
        self.assertEqual([(e['idea_id'],e['session_id']) for e in events_b],[(self.b.idea_id,self.b.sid)])
        self.assertNotEqual(events_a[0]['data']['capture'],events_b[0]['data']['capture'])
        self.assertEqual(events_a[0]['data']['capture']['raw_text'],'Idea of terminal A')

    def test_two_outstanding_waits_only_the_owner_of_the_request_wakes(self):
        """Both agents sit in a keep-alive wait and a request for A wakes only A (real threads, 2 s bound on B's wait)."""
        results={};entered={'a':threading.Event(),'b':threading.Event()}
        original=self.owner.broker.events
        def spy(binding_id,*rest):
            entered['a' if binding_id==self.a.bid else 'b'].set();return original(binding_id,*rest)
        self.owner.broker.events=spy
        def wait(key,terminal):results[key]=terminal.agent.events(timeout=2)
        threads=[threading.Thread(target=wait,args=(k,t)) for k,t in (('a',self.a),('b',self.b))]
        for thread in threads:thread.start()
        try:
            # Generous on a loaded host; B's own wait is still bounded by its 2 s timeout.
            self.assertTrue(entered['a'].wait(10) and entered['b'].wait(10))
            self.a.propose('wake-a')
        finally:
            for thread in threads:thread.join(10)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual([e['request_id'] for e in results['a']['events']],['wake-a'])
        self.assertEqual(results['b']['events'],[])
        self.assertEqual(results['b']['agent_status'],'connected')

    def test_fill_and_respond_on_two_open_requests_stay_with_their_own_binding(self):
        """Each terminal answers its own open request while the other's is open and neither request is superseded."""
        self.a.propose('req-a');self.b.propose('req-b')
        ca=self.correlation(self.a.agent.events(timeout=0)['events'][0]);cb=self.correlation(self.b.agent.events(timeout=0)['events'][0])
        self.assertEqual(self.a.agent.fill(dict(ca,fields={'problem':'A problem'}))['fill_sequence'],1)
        self.assertEqual(self.b.agent.fill(dict(cb,fields={'problem':'B problem'}))['fill_sequence'],1)
        talk_a=self.a.browser('state')['conversation'];talk_b=self.b.browser('state')['conversation']
        self.assertEqual((talk_a['request_id'],talk_a['fills'][0]['fields']),('req-a',{'problem':'A problem'}))
        self.assertEqual((talk_b['request_id'],talk_b['fills'][0]['fields']),('req-b',{'problem':'B problem'}))
        done_a=self.a.agent.respond(dict(ca,proposal=dict(fields()['discovery'],problem='Reply for A')))
        # B is untouched by A completing.
        self.assertEqual(self.b.browser('state')['conversation']['request_id'],'req-b')
        done_b=self.b.agent.respond(dict(cb,proposal=dict(fields()['discovery'],problem='Reply for B')))
        self.assertEqual((done_a['status'],done_b['status']),('completed','completed'))
        for terminal,text in ((self.a,'Reply for A'),(self.b,'Reply for B')):
            proposals=terminal.browser('state')['proposals']
            self.assertEqual([p['proposal']['problem'] for p in proposals],[text])

    def test_one_terminals_credential_cannot_read_or_answer_the_other_terminal(self):
        """A pasted-wrong or hostile credential for terminal A is refused on terminal B's session, events and replies."""
        self.b.propose('req-b');cb=self.correlation(self.b.agent.events(timeout=0)['events'][0])
        for operation,payload in (('events',None),('respond',dict(cb,proposal=fields()['discovery'])),
                                  ('fill',dict(cb,fields={'problem':'Smuggled'}))):
            status,body=self.raw_agent(self.a,operation,self.b.sid,payload)
            self.assertEqual((status,body['code']),(403,'wrong_session'),operation)
        # Naming A's own session but B's request: A has no such request.
        mine=dict(cb,session_id=self.a.sid)
        for operation,payload in (('respond',dict(mine,proposal=fields()['discovery'])),('fill',dict(mine,fields={'problem':'Smuggled'}))):
            status,body=self.raw_agent(self.a,operation,self.a.sid,payload)
            self.assertEqual((status,body['code']),(404,'request_not_found'),operation)
        # B's request is still open and answerable by B; nothing was recorded from A's attempts.
        self.assertEqual(self.b.browser('state')['conversation']['fills'],[])
        self.assertEqual(self.b.agent.respond(dict(cb,proposal=fields()['discovery']))['status'],'completed')
        # And B's binding id with A's token is refused outright.
        headers=self.a.headers();headers['X-Idea-Agent-Binding']=self.b.bid
        conn=http.client.HTTPConnection('127.0.0.1',self.owner.server.server_port,timeout=5)
        try:
            conn.request('GET','/agent/v1/events?session_id=%s&after=0&timeout=0'%self.b.sid,None,headers)
            response=conn.getresponse();body=json.loads(response.read())
        finally:conn.close()
        self.assertEqual((response.status,body['code']),(401,'agent_unauthorized'))

    def test_closing_one_terminal_leaves_the_other_connected_with_its_request_open(self):
        """Closing /glitch-idea in terminal A disconnects only A; terminal B keeps its request and can still answer it."""
        self.a.propose('req-a');self.b.propose('req-b')
        cb=self.correlation(self.b.agent.events(timeout=0)['events'][0])
        self.assertEqual(self.a.agent.session_close()['agent_status'],'disconnected')
        self.assertEqual(self.a.browser('state')['agent_status'],'disconnected')
        self.assertEqual(self.b.browser('state')['agent_status'],'connected')
        self.assertEqual(self.b.browser('state')['conversation']['request_id'],'req-b')
        self.assertEqual(self.b.agent.respond(dict(cb,proposal=fields()['discovery']))['status'],'completed')

    def test_two_sessions_on_the_same_idea_second_stale_save_is_refused_and_nothing_is_overwritten(self):
        """Two terminals open the same idea; the second's save from a stale view is refused and the first's edit survives."""
        # B selects A's idea, as the backlog's resume would.
        state=self.b.browser('state')
        self.b.browser('navigate',dict(request_id='b-select-a',idea_id=self.a.idea_id,expected_revision=state['revision'],
            expected_draft_version=state['draft_version'],step='capture'))
        view_b=self.b.browser('state');self.assertEqual(view_b['idea_id'],self.a.idea_id)
        self.a.accept('priorities',{'urgency':1,'importance':2},'a-wins')
        stale=dict(request_id='b-late',idea_id=self.a.idea_id,expected_revision=view_b['revision'],
                   expected_draft_version=view_b['draft_version'],step='priorities',fields={'urgency':9,'importance':9},
                   proposal_id=None,expected_backlog_revision=None)
        body=self.b.browser('accept',stale,expected=409)
        self.assertEqual(body['code'],'stale_revision')
        self.assertEqual(self.a.browser('state')['accepted']['priorities'],{'urgency':1,'importance':2})


class Clock:
    def __init__(self):self.value=0.
    def __call__(self):return self.value


class TwoBindingsInProcessTests(unittest.TestCase):
    """The service's own Broker + SessionPolicy + Store under a fake clock: exact codes and lease behaviour.

    Waits are zero-timeout calls interleaved between the two bindings; nothing sleeps.
    """
    OPERATION='exploration'

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'ideas',observer='Operator')
        self.clock=Clock();self.records={}
        self.broker=Broker(clock=self.clock,validate_source=lambda _,source:source,
            persist_proposal=lambda _:dict(proposal_id='proposal_fixture',sha256='a'*64))
        self.policy=SessionPolicy(lambda r:Service(self.store,{},TrustedContext(r['actor'],r['receipt_session_id'],r['selected_idea_id'])),
            lambda r:self.records.__setitem__(r['binding_id'],copy.deepcopy(r)),namespace='fixture',clock=self.clock,
            cancel=self.broker.cancel,agent_open=self.broker.open,agent_state=self.broker.status,agent_activity=self.broker.touch)
        self.a=self.open('1');self.b=self.open('2')

    def open(self,digit):
        sid=self.store.create_session();bid=self.policy.open_binding('Operator',sid)
        return dict(sid=sid,bid=bid,gen=self.policy.agent_credentials(bid)['generation'],
                    idea='idea_'+digit*32,data={'capture':{'raw_text':'Words '+digit}})

    def code(self,code,fn):
        with self.assertRaises((IdeaError,BridgeError)) as caught:fn()
        self.assertEqual(caught.exception.code,code)

    def enqueue(self,who,rid='req_one',idea=None):
        envelope=dict(request_id=rid,idea_id=idea or who['idea'],expected_revision=1,expected_draft_version=0,
            operation=self.OPERATION,source_digest=source_digest(self.OPERATION,1,who['data']))
        return self.broker.enqueue(who['bid'],who['gen'],envelope,dict(accepted_revision=1,draft_version=0,data=who['data']))

    def events(self,who,after=0,gen=None,sid=None):
        return self.broker.events(who['bid'],gen or who['gen'],sid or who['sid'],after,0)

    def correlation(self,who,rid='req_one'):
        return dict(request_id=rid,session_id=who['sid'],idea_id=who['idea'],accepted_revision=1,draft_version=0,
                    operation=self.OPERATION,source_digest=source_digest(self.OPERATION,1,who['data']))

    def fill(self,who,rid='req_one',gen=None,**override):
        return self.broker.fill(who['bid'],gen or who['gen'],dict(self.correlation(who,rid),fields=dict(outcome='Agreed words'),**override))

    def answer(self,who,rid='req_one',gen=None,bid=None,about=None):
        return self.broker.respond(bid or who['bid'],gen or who['gen'],dict(self.correlation(about or who,rid),proposal=copy.deepcopy(EXPLORATION)))

    def status(self,who):
        state=self.broker.status(who['bid'],who['gen']);return state['agent_status'],state['reason']

    def test_events_are_isolated_with_interleaved_waits_under_the_fake_clock(self):
        """A request for terminal A is delivered to A only; interleaved zero-timeout waits from B see nothing, and vice versa."""
        self.assertEqual((self.events(self.a)['events'],self.events(self.b)['events']),([],[]))
        self.enqueue(self.a)
        self.assertEqual(self.events(self.b)['events'],[])
        got=self.events(self.a)['events']
        self.assertEqual([(e['request_id'],e['idea_id'],e['session_id']) for e in got],[('req_one',self.a['idea'],self.a['sid'])])
        self.enqueue(self.b)   # the SAME request id on a different binding is a different request
        self.assertEqual([e['idea_id'] for e in self.events(self.b)['events']],[self.b['idea']])
        self.assertEqual(self.events(self.a,after=got[0]['sequence'])['events'],[])

    def test_credentials_and_sessions_do_not_cross(self):
        """A's generation, binding or session paired with B's cannot read, fill or answer B's request."""
        self.enqueue(self.b);self.events(self.b)
        self.code('wrong_generation',lambda:self.events(self.b,gen=self.a['gen']))
        self.code('wrong_generation',lambda:self.fill(self.b,gen=self.a['gen']))
        self.code('wrong_generation',lambda:self.answer(self.b,gen=self.a['gen']))
        self.code('wrong_session',lambda:self.events(self.a,sid=self.b['sid']))
        # A's own binding and generation, B's request: A has no such request at all ...
        self.code('request_not_found',lambda:self.broker.fill(self.a['bid'],self.a['gen'],dict(self.correlation(self.b),fields=dict(outcome='x'))))
        self.code('request_not_found',lambda:self.answer(self.a,about=self.b))
        # ... and once A has a request with the same id, B's correlation does not match it.
        self.enqueue(self.a);self.events(self.a)
        self.code('response_mismatch',lambda:self.broker.fill(self.a['bid'],self.a['gen'],dict(self.correlation(self.b),fields=dict(outcome='x'))))
        self.code('response_mismatch',lambda:self.answer(self.a,about=self.b))
        # B's request was not touched by any of it.
        self.assertEqual(self.fill(self.b)['code'],'ok')
        self.assertEqual(self.answer(self.b)['status'],'completed')

    def test_authorize_agent_binds_a_token_to_its_own_binding(self):
        """The credential check refuses A's token when it is presented with B's binding id."""
        credentials=self.policy.agent_credentials(self.a['bid'])
        def request(binding,generation,token):
            return RequestInfo('GET','/agent/v1/events',{'x-idea-agent-binding':binding,'x-idea-agent-generation':generation,
                'authorization':'Bearer '+token},'http://127.0.0.1:1234')
        self.assertEqual(self.policy.authorize_agent(request(self.a['bid'],credentials['generation'],credentials['token'])).binding_id,self.a['bid'])
        with self.assertRaises(BridgeError) as caught:
            self.policy.authorize_agent(request(self.b['bid'],self.b['gen'],credentials['token']))
        self.assertEqual(caught.exception.code,'agent_unauthorized')

    def test_fill_and_respond_complete_per_binding_and_never_supersede_across_bindings(self):
        """Terminal B starts on another idea and terminal A's open request is not superseded, cancelled or answered by it."""
        self.enqueue(self.a);self.events(self.a);self.enqueue(self.b);self.events(self.b)
        self.assertEqual(self.fill(self.a)['fill_sequence'],1)
        # B moves to another idea: that supersedes B's own conversation (within one binding) ...
        self.enqueue(self.b,'req_two',idea='idea_'+'9'*32)
        self.assertEqual(self.broker.bindings[self.b['bid']]['requests']['req_one']['reason'],'superseded')
        # ... and A's is untouched: still pending, still answerable, with its own fill intact.
        self.assertEqual(self.broker.bindings[self.a['bid']]['requests']['req_one']['state'],'pending')
        self.assertEqual(self.broker.conversation(self.a['bid'],self.a['gen'])['fills'][0]['fields'],dict(outcome='Agreed words'))
        self.assertEqual(self.fill(self.a)['fill_sequence'],2)
        self.assertEqual(self.answer(self.a)['status'],'completed')
        self.code('request_cancelled',lambda:self.fill(self.b))
        self.assertEqual(self.broker.bindings[self.b['bid']]['requests']['req_two']['state'],'pending')

    def test_keep_alive_on_one_binding_does_not_keep_the_other_alive(self):
        """Terminal A keeps waiting while terminal B's agent has gone: B lapses on heartbeat and A stays connected."""
        for _ in range(4):
            self.clock.value+=HEARTBEAT-5
            self.events(self.a)
        self.assertEqual(self.status(self.a),('connected',None))
        self.assertEqual(self.status(self.b),('disconnected','heartbeat_expired'))
        self.code('agent_unavailable',lambda:self.events(self.b))
        self.assertEqual(self.events(self.a)['agent_status'],'connected')

    def test_one_bindings_terminal_conversation_does_not_reset_the_others_idle_clock(self):
        """Terminal A's fills keep A past the idle limit while B, polling but with no human activity, pauses for idle."""
        self.enqueue(self.a);self.events(self.a)
        t=0.
        while t<IDLE:
            t+=30.;self.clock.value=t
            self.events(self.a)
            if t%300==0:self.fill(self.a)          # A's human keeps talking to its agent
            if t<IDLE:self.events(self.b)          # B's agent polls but its human is silent
        self.assertEqual(self.status(self.a),('connected',None))
        self.assertEqual(self.status(self.b),('paused','idle'))
        self.code('agent_unavailable',lambda:self.events(self.b))
        self.assertEqual(self.fill(self.a)['code'],'ok')

    def test_a_browser_save_touches_only_its_own_binding(self):
        """A human's browser write in terminal A moves A's idle clock and leaves B's idle clock alone."""
        t=0.
        while t<IDLE+60:
            t+=30.;self.clock.value=t
            if t<IDLE:self.events(self.b)
            self.events(self.a)
            if t==510:self.assertTrue(self.broker.touch(self.a['bid'],self.a['gen']))
        self.assertEqual(self.status(self.a),('connected',None))
        self.assertEqual(self.status(self.b),('paused','idle'))
        self.assertFalse(self.broker.touch(self.b['bid'],self.b['gen']),'a paused binding is never revived by a save')

    def test_closing_one_binding_leaves_the_other_connected_with_its_request_open(self):
        """Closing terminal A cancels only A's requests; B stays connected and keeps its open request."""
        self.enqueue(self.a);self.events(self.a);self.enqueue(self.b);self.events(self.b)
        self.broker.cancel(self.a['bid'],self.a['gen'])
        self.assertEqual(self.status(self.a),('disconnected','closed'))
        self.assertEqual(self.broker.bindings[self.a['bid']]['requests']['req_one']['state'],'cancelled')
        self.assertEqual(self.status(self.b),('connected',None))
        self.assertEqual(self.broker.bindings[self.b['bid']]['requests']['req_one']['state'],'pending')
        self.assertEqual(self.fill(self.b)['code'],'ok')
        self.assertEqual(self.answer(self.b)['status'],'completed')

    def test_a_lapsed_binding_resumes_alone(self):
        """After B lapses and is resumed with a fresh generation, A's credential and connection are unchanged."""
        before=self.policy.agent_credentials(self.a['bid'])
        self.clock.value=HEARTBEAT-5;self.events(self.a)
        self.clock.value=HEARTBEAT+1;self.events(self.a)
        self.assertEqual(self.status(self.b),('disconnected','heartbeat_expired'))
        self.policy.open_binding('Operator',self.b['sid'],binding_id=self.b['bid'],resume=True)
        resumed=self.policy.agent_credentials(self.b['bid'])
        self.assertNotEqual(resumed['generation'],self.b['gen'])
        self.assertEqual(self.broker.status(self.b['bid'],resumed['generation'])['agent_status'],'connected')
        self.assertEqual(self.policy.agent_credentials(self.a['bid']),before)
        self.assertEqual(self.status(self.a),('connected',None))

    def test_the_ninth_concurrent_binding_is_refused(self):
        """Up to eight terminals may share a store; a ninth is refused and the first two keep working."""
        for _ in range(6):self.open('3')
        self.code('session_capacity_exhausted',lambda:self.open('4'))
        self.assertEqual((self.status(self.a),self.status(self.b)),(('connected',None),('connected',None)))


if __name__=='__main__':unittest.main()
