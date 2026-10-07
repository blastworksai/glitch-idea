"""A request enqueued after a long pause in the agent's polling must reach the agent, not be retired unseen.

Fake clock throughout (Broker/SessionPolicy clock injection); no real waiting.
"""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
from idea_domain import IdeaError
from idea_proposals import Broker, ANSWER_GRACE, HEARTBEAT
from idea_service import Service, TrustedContext
from idea_sessions import SessionPolicy
from idea_store import Store
from idea_workflow import source_digest


class AgentLeaseAfterSupersedeTests(unittest.TestCase):
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

    def enqueue(self,rid,operation,gen=None):
        data=self.data
        source=dict(accepted_revision=1,draft_version=0,data=data)
        envelope=dict(request_id=rid,idea_id=self.idea,expected_revision=1,expected_draft_version=0,
            operation=operation,source_digest=source_digest(operation,1,data))
        return self.broker.enqueue(self.bid,gen or self.gen,envelope,source)

    def events(self,after,gen=None):
        return self.broker.events(self.bid,gen or self.gen,self.sid,after=after,timeout=0)

    def test_request_enqueued_after_a_long_answer_window_is_delivered(self):
        # t=0: the first request is delivered; t=10: the agent fills it, then keeps working in the terminal.
        self.enqueue('req_first','method')
        first=self.events(0);self.assertEqual([e['sequence'] for e in first['events']],[1])
        self.now=10.
        self.broker.fill(self.bid,self.gen,dict(request_id='req_first',session_id=self.sid,idea_id=self.idea,
            accepted_revision=1,draft_version=0,operation='method',source_digest=source_digest('method',1,self.data),
            fields=dict(memory=self.memory)))
        # t=560 (< IDLE since the fill): the operator accepts the step; the page enqueues the next request.
        self.now=560.
        self.enqueue('req_second','discovery')
        # The agent's very next events call, one second later, must deliver sequence 2, not agent_unavailable.
        self.now=561.
        result=self.events(1)
        self.assertEqual(result['agent_status'],'connected',result)
        self.assertEqual([e['sequence'] for e in result['events']],[2])
        self.assertEqual(result['events'][0]['operation'],'discovery')

    def test_agent_that_never_polls_a_new_request_still_expires(self):
        # The lease is restarted for the new request, not removed: no poll for HEARTBEAT -> retired.
        self.enqueue('req_first','method');self.events(0)
        self.now=10.
        self.enqueue('req_second','discovery')
        self.now=10.+HEARTBEAT+1
        with self.assertRaises(IdeaError) as caught:self.events(1)
        self.assertEqual(caught.exception.code,'agent_unavailable')
        self.assertEqual(self.broker.status(self.bid,self.gen)['reason'],'heartbeat_expired')


if __name__=='__main__':unittest.main()
