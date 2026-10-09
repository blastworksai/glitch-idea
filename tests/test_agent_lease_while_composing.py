"""A superseded request must not retire an agent still composing its answer (SKILLS-36 finding 4).

Real Broker, fake clock, no real waiting.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
from idea_domain import IdeaError
from idea_proposals import Broker, ANSWER_GRACE, HEARTBEAT
from idea_workflow import source_digest


class AgentLeaseWhileComposingTests(unittest.TestCase):
    def setUp(self):
        self.now=0.
        self.broker=Broker(clock=lambda:self.now,validate_source=lambda _,source:source,
                           persist_proposal=lambda _:dict(proposal_id='proposal_fixture',sha256='a'*64))
        self.bid='binding_'+'1'*32;self.gen='generation_'+'1'*32;self.sid='session_'+'1'*32
        self.idea='idea_'+'1'*32
        self.broker.open(self.bid,self.gen,self.sid)
        self.data={'capture':{'raw_text':'Original words'}}

    def enqueue(self,rid,operation):
        source=dict(accepted_revision=1,draft_version=0,data=self.data)
        envelope=dict(request_id=rid,idea_id=self.idea,expected_revision=1,expected_draft_version=0,
            operation=operation,source_digest=source_digest(operation,1,self.data))
        return self.broker.enqueue(self.bid,self.gen,envelope,source)

    def events(self,after):
        return self.broker.events(self.bid,self.gen,self.sid,after=after,timeout=0)

    def fill(self,rid,operation):
        return self.broker.fill(self.bid,self.gen,dict(request_id=rid,session_id=self.sid,idea_id=self.idea,
            accepted_revision=1,draft_version=0,operation=operation,source_digest=source_digest(operation,1,self.data),
            fields=dict(memory=dict(status='varied',sources=[],rationale=None,preferred_method=None))))

    def test_fill_after_supersede_is_refused_but_agent_stays_connected(self):
        self.enqueue('req_first','method');self.events(0)           # t=0 delivered
        self.now=20.;self.enqueue('req_second','discovery')          # operator accepts by hand
        self.now=56.                                                 # > HEARTBEAT after the supersede
        with self.assertRaises(IdeaError) as caught:self.fill('req_first','method')
        self.assertEqual(caught.exception.code,'request_cancelled')
        self.assertEqual(self.broker.status(self.bid,self.gen)['agent_status'],'connected')
        result=self.events(1)
        self.assertEqual(result['agent_status'],'connected')
        self.assertEqual([e['operation'] for e in result['events']],['discovery'])

    def test_events_after_supersede_gets_empty_wait_when_nothing_new(self):
        self.enqueue('req_first','method');self.events(0)
        self.now=20.;self.enqueue('req_second','discovery');self.events(1)
        self.now=40.
        self.assertEqual(self.events(2)['agent_status'],'connected')

    def test_agent_silent_past_the_answer_grace_is_still_retired(self):
        self.enqueue('req_first','method');self.events(0)
        self.now=20.;self.enqueue('req_second','discovery')
        self.now=float(ANSWER_GRACE)+1
        with self.assertRaises(IdeaError) as caught:self.events(1)
        self.assertEqual(caught.exception.code,'agent_unavailable')
        self.assertEqual(self.broker.status(self.bid,self.gen)['reason'],'heartbeat_expired')


if __name__=='__main__':unittest.main()
