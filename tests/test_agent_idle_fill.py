"""An accepted terminal fill is operator activity: it resets the idle clock like a human save.

An empty events wait and a refused fill do not. Fake clock; no real waiting.
"""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
from idea_domain import IdeaError
from idea_proposals import Broker, IDLE
from idea_service import Service, TrustedContext
from idea_sessions import SessionPolicy
from idea_store import Store
from idea_workflow import source_digest


class AgentIdleFillTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=Store(self.root/'ideas',observer='Operator')
        self.sid=self.store.create_session();self.now=0.;self.records={}
        self.broker=Broker(clock=lambda:self.now,validate_source=lambda _,source:source,
                           persist_proposal=lambda _:dict(proposal_id='proposal_fixture',sha256='a'*64))
        self.policy=SessionPolicy(self.factory,lambda r:self.records.__setitem__(r['binding_id'],copy.deepcopy(r)),
            namespace='fixture',clock=lambda:self.now,cancel=self.broker.cancel,
            agent_open=self.broker.open,agent_state=self.broker.status,agent_activity=self.broker.touch)
        self.bid=self.policy.open_binding('Operator',self.sid)
        self.gen=self.policy.agent_credentials(self.bid)['generation']
        self.idea='idea_'+'1'*32
        self.memory=dict(status='varied',sources=[],rationale=None,preferred_method=None)

    data={'capture':{'raw_text':'Original words'}}

    def factory(self,record):
        return Service(self.store,{},TrustedContext(record['actor'],record['receipt_session_id'],record['selected_idea_id']))

    def enqueue_and_deliver(self,rid='req_one'):
        source=dict(accepted_revision=1,draft_version=0,data=self.data)
        envelope=dict(request_id=rid,idea_id=self.idea,expected_revision=1,expected_draft_version=0,
            operation='method',source_digest=source_digest('method',1,self.data))
        self.broker.enqueue(self.bid,self.gen,envelope,source)
        self.assertEqual(len(self.events(0)['events']),1)

    def events(self,after,gen=None):
        return self.broker.events(self.bid,gen or self.gen,self.sid,after=after,timeout=0)

    def fill(self,rid='req_one',gen=None,fields=None):
        return self.broker.fill(self.bid,gen or self.gen,dict(request_id=rid,session_id=self.sid,idea_id=self.idea,
            accepted_revision=1,draft_version=0,operation='method',source_digest=source_digest('method',1,self.data),
            fields=fields or dict(memory=self.memory)))

    def status(self):
        return self.broker.status(self.bid,self.gen)

    def test_fills_every_five_minutes_keep_the_agent_connected_past_idle(self):
        self.enqueue_and_deliver()
        for t in (300.,600.,900.,1200.):
            self.now=t
            self.assertEqual(self.fill()['code'],'ok')
        self.now=1200.+IDLE-1
        self.assertEqual(self.status()['agent_status'],'connected')
        self.now=1200.+IDLE+1
        self.assertEqual(self.status()['agent_status'],'paused')

    def test_only_empty_waits_for_idle_seconds_pause_the_agent(self):
        t=0.
        while t<IDLE:
            self.now=t
            self.assertEqual(self.events(0)['events'],[])
            t+=30
        self.now=float(IDLE)
        with self.assertRaises(IdeaError) as caught:self.events(0)
        self.assertEqual(caught.exception.code,'agent_unavailable')
        state=self.status()
        self.assertEqual((state['agent_status'],state['reason']),('paused','idle'))

    def test_a_refused_fill_does_not_reset_the_idle_clock(self):
        self.enqueue_and_deliver()
        self.now=500.
        for rid,fields in (('req_one',dict(bogus=1)),('req_missing',None)):
            with self.assertRaises(IdeaError):self.fill(rid,fields=fields)
        self.now=float(IDLE)+1
        self.assertEqual(self.status()['agent_status'],'paused')

    def test_a_fill_from_a_stale_generation_does_not_reset_the_idle_clock(self):
        self.enqueue_and_deliver()
        self.now=500.
        with self.assertRaises(IdeaError) as caught:self.fill(gen='g'*len(self.gen))
        self.assertEqual(caught.exception.code,'wrong_generation')
        self.now=float(IDLE)+1
        self.assertEqual(self.status()['agent_status'],'paused')


if __name__=='__main__':unittest.main()
