"""A late (expired) pairing attempt must not revoke the agent's credential.

Fake clock throughout. Agent auth does not need pairing (authorize_agent), so a failed
browser pairing must not be able to take the agent away.
"""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
from idea_bridge import BridgeError, RequestInfo
from idea_proposals import Broker
from idea_service import Service, TrustedContext
from idea_sessions import SessionPolicy
from idea_store import Store


class LatePairingKeepsAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'ideas',observer='Operator')
        self.sid=self.store.create_session();self.now=0.;self.cancelled=[]
        self.broker=Broker(clock=lambda:self.now,validate_source=lambda _,s:s,
                           persist_proposal=lambda _:dict(proposal_id='proposal_fixture',sha256='a'*64))
        def cancel(bid,gen):self.cancelled.append((bid,gen));self.broker.cancel(bid,gen)
        self.policy=SessionPolicy(lambda r:Service(self.store,{},TrustedContext(r['actor'],r['receipt_session_id'],r['selected_idea_id'])),
            lambda r:None,namespace='fixture',clock=lambda:self.now,cancel=cancel,
            agent_open=self.broker.open,agent_state=self.broker.status,agent_activity=self.broker.touch)
        self.bid=self.policy.open_binding('Operator',self.sid)
        self.cred=self.policy.agent_credentials(self.bid)

    def req(self,headers):return RequestInfo('POST','/agent/v1/events',{k.lower():v for k,v in headers.items()},'http://127.0.0.1:1234')

    def agent_context(self):
        return self.policy.authorize_agent(self.req({'X-Idea-Agent-Binding':self.cred['binding_id'],
            'X-Idea-Agent-Generation':self.cred['generation'],'Authorization':'Bearer '+self.cred['token']}))

    def wait(self):
        """One empty 25 s agent wait: authorize, recheck, broker events (timeout 0 under the fake clock)."""
        context=self.agent_context();self.policy.recheck_agent(context)
        self.now+=25.   # the wait blocks 25 s, then returns and renews the lease at its end
        self.broker.events(self.bid,self.cred['generation'],self.sid,0,0)
        self.policy.recheck_agent(context)

    def test_two_empty_waits_then_late_pairing_then_third_wait(self):
        code=self.policy.issue_pairing(self.bid)       # session-open prints the code at t=0
        self.wait();self.wait()                        # waits return at t=25, t=50; operator has not paired
        context=self.agent_context()                   # third wait starts at t=50 ...
        self.now=61.                                   # ... operator pastes the code after the 60 s TTL
        with self.assertRaises(BridgeError) as caught:self.policy.pair(self.req({}),{'code':code})
        self.assertEqual(caught.exception.code,'pairing_expired_or_locked')
        self.now=75.                                   # ... and the third wait returns, empty
        self.policy.recheck_agent(context)             # unpatched: agent_unauthorized (token revoked)
        self.broker.events(self.bid,self.cred['generation'],self.sid,0,0)
        self.assertNotIn((self.bid,self.cred['generation']),self.cancelled)

    def test_expired_code_can_be_replaced_without_resuming_the_agent(self):
        code=self.policy.issue_pairing(self.bid);self.now=61.
        with self.assertRaises(BridgeError):self.policy.pair(self.req({}),{'code':code})
        fresh=self.policy.issue_pairing(self.bid)
        self.assertNotEqual(fresh,code)
        response=self.policy.pair(self.req({}),{'code':fresh})
        self.assertTrue(response.body['ok'])
        self.agent_context()                           # agent credential untouched throughout

    def test_attempt_lockout_still_revokes(self):
        code=self.policy.issue_pairing(self.bid)
        for _ in range(5):
            with self.assertRaises(BridgeError):self.policy.pair(self.req({}),{'code':'wrong'})
        with self.assertRaises(BridgeError):self.policy.pair(self.req({}),{'code':code})
        with self.assertRaises(BridgeError) as caught:self.agent_context()
        self.assertEqual(caught.exception.code,'agent_unauthorized')


if __name__=='__main__':unittest.main()
