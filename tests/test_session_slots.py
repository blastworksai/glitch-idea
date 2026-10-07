"""Session slots: at most eight retained sessions per store; a ninth is refused until one is finished or discarded.

Operator's ruling: finished = the selected idea's Review step is saved ("Automatically at Review"); an open
session is discarded "after a warning". Real OwnerService (real Store, Broker, SessionPolicy, loopback HTTP) and the
real Runtime client; the finished idea is a real published handoff seeded into the same Store.
"""
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

SCRIPTS=Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0,str(SCRIPTS))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from idea_bridge import BridgeError
from idea_handoff import HandoffProvider
from idea_launch import OwnerService
from idea_proposals import IDLE
from idea_runtime import Runtime, RuntimeError as OwnerError
from idea_service import TrustedContext
from idea_store import Store
from test_handoff_store import ACTOR, publication_payload, publication_seed
from test_workflow import fields

RAW=fields()['capture']['raw_text']


class Session:
    def __init__(self, rig, selected=None, pair=False):
        self.rig=rig
        self.opened=rig.client.open_binding('new',None,selected)
        self.bid,self.sid=self.opened['binding_id'],self.opened['session_id']
        self.cookie=self.csrf=self.tab=None
        if pair:
            status,body,cookie=rig.wire('/api/v1/pair',{'code':self.opened['pairing_code']},{'X-Idea-Binding':self.bid})
            assert status==200,body
            self.cookie=cookie.split(';',1)[0];self.csrf=body['csrf_token'];self.tab=body['tab_secret']
        self.generation=rig.owner.policy.agent_credentials(self.bid)['generation']

    def state(self):
        return self.rig.wire('/api/v1/state',None,{'X-Idea-Binding':self.bid,'Cookie':self.cookie,'X-Idea-Tab':self.tab})

    def ping(self):
        """The page's typing ping: a human is working in this tab."""
        return self.rig.wire('/api/v1/activity',{},{'X-Idea-Binding':self.bid,'Cookie':self.cookie,'X-Idea-Tab':self.tab,
                                                    'X-CSRF-Token':self.csrf})

    def agent_headers(self, credentials=None):
        credentials=credentials or self.credentials
        return {'Authorization':'Bearer '+credentials['token'],'X-Idea-Agent-Binding':self.bid,
                'X-Idea-Agent-Generation':credentials['generation']}

    def agent(self, operation, payload=None, timeout=0, wait=5):
        path='/agent/v1/'+operation+('?session_id=%s&after=0&timeout=%s'%(self.sid,timeout) if operation=='events' else '')
        headers=self.agent_headers()
        if payload is not None:headers['Content-Type']='application/json'
        conn=http.client.HTTPConnection('127.0.0.1',self.rig.owner.server.server_port,timeout=wait)
        try:
            conn.request('GET' if operation=='events' else 'POST',path,None if payload is None else json.dumps(payload),headers)
            response=conn.getresponse();return response.status,json.loads(response.read())
        finally:conn.close()

    def hold_agent_credentials(self):
        self.credentials=self.rig.owner.policy.agent_credentials(self.bid)

    def close_agent(self):
        """The agent goes away (what session-close does to the broker): the session is idle again."""
        self.rig.owner.broker.cancel(self.bid,self.generation,'closed')

    @property
    def path(self):
        return self.rig.bindings/(self.bid+'.json')


@unittest.skipUnless(os.name=='posix','Native owner ACLs remain unqualified')
class SessionSlotsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        base=Path(self.temp.name);self.store=base/'ideas';self.root=base/'private'
        self.key=None

    def seed_finished(self):
        """A real idea with a saved handoff (its Review step saved), in the store the service will use."""
        sid,self.key,_=publication_seed(self.store)
        store=Store(self.store,observer=ACTOR)
        HandoffProvider(store).publish(TrustedContext(ACTOR,sid,self.key),publication_payload(store,self.key)['payload'])

    def start(self):
        self.runtime=Runtime(self.store,self.root).acquire_owner()
        self.owner=OwnerService(self.runtime);self.owner.start()
        self.client=Runtime(self.store,self.root)
        self.bindings=Path(self.runtime.path)/'bindings'

    def go(self):
        self.start();self.addCleanup(lambda:self.owner.shutdown())

    def restart(self):
        self.owner.shutdown()
        self.start()

    def wire(self,path,payload=None,headers=None,*,timeout=5):
        conn=http.client.HTTPConnection('127.0.0.1',self.owner.server.server_port,timeout=timeout)
        h=dict(headers or {})
        if payload is not None:h.update({'Content-Type':'application/json','Origin':self.owner.server.origin})
        try:
            conn.request('POST' if payload is not None else 'GET',path,None if payload is None else json.dumps(payload),h)
            response=conn.getresponse();return response.status,json.loads(response.read()),response.getheader('Set-Cookie')
        finally:conn.close()

    def refused(self,code,action):
        with self.assertRaises(OwnerError) as caught:action()
        self.assertEqual(caught.exception.code,code)
        return caught.exception

    def eight(self,selected=(),idle=True):
        """Eight sessions; `selected` maps position -> idea id. Idle = agent gone and no tab paired."""
        made=[Session(self,selected.get(n) if hasattr(selected,'get') else None) for n in range(8)]
        if idle:
            for session in made:session.close_agent()
        return made

    def listed(self):
        return {row['binding_id']:row for row in self.client.list_sessions()}

    def record_bytes(self):
        return {p.name:p.read_bytes() for p in self.bindings.glob('binding_*.json')}

    # ---- refusal with the list -------------------------------------------------------------------------------

    def test_ninth_is_refused_and_carries_every_retained_session(self):
        self.go()
        made=self.eight(idle=False)
        for session in made[:2]:session.close_agent()
        before=self.record_bytes()
        caught=self.refused('binding_capacity',lambda:self.client.open_binding('new'))
        self.assertEqual(len(caught.sessions),8)
        self.assertEqual({row['binding_id'] for row in caught.sessions},{s.bid for s in made})
        by={row['binding_id']:row for row in caught.sessions}
        for index,session in enumerate(made):
            row=by[session.bid]
            self.assertEqual(set(row),{'binding_id','selected_idea_id','title','finished','in_use'})
            self.assertEqual((row['selected_idea_id'],row['title'],row['finished']),(None,None,False))
            self.assertEqual(row['in_use'],index>=2,(index,row))   # two closed, six still connected
        self.assertEqual(self.record_bytes(),before)
        self.assertEqual(len(self.owner.members),8)

    # ---- automatic freeing at Review ------------------------------------------------------------------------

    def test_a_finished_idle_session_is_freed_for_the_ninth(self):
        self.seed_finished();self.go()
        made=self.eight(selected={3:self.key})
        row=self.listed()[made[3].bid]
        self.assertEqual((row['selected_idea_id'],row['finished'],row['in_use']),(self.key,True,False))
        self.assertEqual(row['title'],' '.join(RAW.split())[:80])
        before=self.record_bytes()
        ninth=Session(self,None)
        self.assertFalse(made[3].path.exists())
        after=self.record_bytes()
        self.assertEqual({k:v for k,v in after.items() if k!=ninth.bid+'.json'},
                         {k:v for k,v in before.items() if k!=made[3].bid+'.json'})
        self.assertEqual(self.owner.members,{s.bid for s in made if s is not made[3]}|{ninth.bid})
        # The idea stays in the store, readable.
        with Store(self.store,observer=ACTOR).transaction() as state:self.assertIn(self.key,state['ideas'])
        self.assertTrue(any(self.key in p.name for p in self.store.glob('*.md')))
        # Not finished is never freed: the tenth is refused.
        self.refused('binding_capacity',lambda:self.client.open_binding('new'))

    def test_the_least_recently_used_finished_session_goes_first(self):
        self.seed_finished();self.go()
        made=self.eight(selected={1:self.key,5:self.key})
        os.utime(made[1].path,(2000,2000));os.utime(made[5].path,(1000,1000))
        Session(self,None)
        self.assertTrue(made[1].path.exists());self.assertFalse(made[5].path.exists())

    def test_a_finished_session_still_in_use_is_not_freed(self):
        self.seed_finished();self.go()
        made=self.eight(selected={2:self.key},idle=False)
        for session in made:session.close_agent()
        # A paired tab keeps it in use even with the agent gone.
        # pair a browser onto session 2 by resuming it
        opened=self.client.open_binding('resume',made[2].bid)
        status,body,cookie=self.wire('/api/v1/pair',{'code':opened['pairing_code']},{'X-Idea-Binding':made[2].bid})
        self.assertEqual(status,200)
        made[2].close_agent()  # resume opened a fresh agent generation
        made[2].generation=self.owner.policy.agent_credentials(made[2].bid)['generation']
        made[2].close_agent()
        before=self.record_bytes()
        caught=self.refused('binding_capacity',lambda:self.client.open_binding('new'))
        row={r['binding_id']:r for r in caught.sessions}[made[2].bid]
        self.assertEqual((row['finished'],row['in_use']),(True,True))
        self.assertEqual(self.record_bytes(),before)

    def test_a_finished_session_with_a_connected_agent_is_not_freed(self):
        self.seed_finished();self.go()
        made=self.eight(selected={4:self.key})
        made[4].generation=self.owner.policy.agent_credentials(made[4].bid)['generation']
        opened=self.client.open_binding('resume',made[4].bid)  # fresh agent generation, connected
        row=self.listed()[made[4].bid]
        self.assertEqual((row['finished'],row['in_use']),(True,True))
        self.refused('binding_capacity',lambda:self.client.open_binding('new'))
        self.assertTrue(made[4].path.exists())

    # ---- discard ---------------------------------------------------------------------------------------------

    def test_discard_an_idle_session_frees_its_slot(self):
        self.go()
        made=self.eight()
        self.refused('binding_capacity',lambda:self.client.open_binding('new'))
        result=self.client.discard_binding(made[6].bid)
        self.assertEqual(result,dict(binding_id=made[6].bid,was_in_use=False))
        self.assertFalse(made[6].path.exists());self.assertEqual(len(self.listed()),7)
        self.assertNotIn(made[6].bid,self.owner.members)
        self.assertNotIn(made[6].bid,self.owner.broker.bindings)
        self.assertEqual(len(self.owner.policy._entries),7)
        Session(self,None)
        self.assertEqual(len(self.listed()),8)

    def test_discarding_an_open_session_needs_confirmation_and_then_stops_its_tab_and_agent(self):
        self.go()
        made=self.eight(idle=False)
        target=made[0]
        # Pair a tab onto session 0 (fresh resume: new agent generation, connected).
        opened=self.client.open_binding('resume',target.bid)
        status,body,cookie=self.wire('/api/v1/pair',{'code':opened['pairing_code']},{'X-Idea-Binding':target.bid})
        self.assertEqual(status,200)
        target.cookie=cookie.split(';',1)[0];target.csrf=body['csrf_token'];target.tab=body['tab_secret']
        target.hold_agent_credentials()
        self.assertEqual(target.state()[0],200)
        self.assertEqual(target.agent('events')[0],200)
        before=self.record_bytes()
        caught=self.refused('session_in_use',lambda:self.client.discard_binding(target.bid))
        self.refused('session_in_use',lambda:self.client.discard_binding(target.bid,False))
        self.assertEqual(self.record_bytes(),before)
        self.assertEqual(target.state()[0],200);self.assertEqual(target.agent('events')[0],200)
        self.assertEqual(len(self.owner.members),8)
        # A waiting events call returns promptly when the session is discarded under it.
        waited={}
        def wait():
            started=time.monotonic()
            try:waited['result']=target.agent('events',timeout=20,wait=15)
            except Exception as exc:waited['result']=exc
            waited['seconds']=time.monotonic()-started
        thread=threading.Thread(target=wait);thread.start();time.sleep(0.5)
        result=self.client.discard_binding(target.bid,True)
        self.assertEqual(result,dict(binding_id=target.bid,was_in_use=True))
        thread.join(5);self.assertFalse(thread.is_alive(),'a waiting events call must not hang')
        self.assertLess(waited['seconds'],4)
        self.assertNotEqual(waited['result'][0] if type(waited['result']) is tuple else None,200,waited)
        # The tab and the agent are refused with the existing unauthorized codes.
        status,body,_=target.state()
        self.assertEqual((status,body['code']),(401,'browser_unauthorized'))
        for operation,payload in (('events',None),('fill',dict(idea_id='idea_'+'a'*32,accepted_revision=1)),('session-close',{})):
            status,body=target.agent(operation,payload)
            self.assertEqual((status,body['code']),(401,'agent_unauthorized'),operation)
        self.assertFalse(target.path.exists())
        self.assertEqual(len(self.listed()),7)
        Session(self,None)

    def test_discard_unknown_and_malformed(self):
        self.go()
        made=self.eight()
        self.refused('binding_not_found',lambda:self.client.discard_binding('binding_'+'0'*32))
        self.refused('binding_not_found',lambda:self.client.discard_binding('binding_'+'0'*32,True))
        for bad in ('binding_x','','../x',None,7):
            self.refused('invalid_control',lambda:self.client.discard_binding(bad))
        self.refused('invalid_control',lambda:self.client.discard_binding(made[0].bid,'yes'))
        # Server-side validation, bypassing the client's own checks.
        discovery,credential=self.client._client_pair(time.monotonic()+2)
        deadline=lambda:time.monotonic()+2
        for bad in (('binding_x',True),(made[0].bid,'yes'),(made[0].bid,1),(made[0].bid,None)):
            self.refused('invalid_control',lambda:self.client._call('binding-discard',discovery,credential,deadline(),discard=bad))
        self.assertEqual(len(self.listed()),8)
        # Exact field sets: the owner refuses a payload with a missing or extra field.
        for payload in (dict(binding_id=made[0].bid),dict(binding_id=made[0].bid,confirm=True,extra=1),{}):
            with self.assertRaises(BridgeError) as caught:
                self.owner.control(type('R',(),dict(path='/control/v1/binding-discard',header=lambda s,n:None))(),payload)
            self.assertEqual(caught.exception.code,'invalid_control')
        self.assertEqual(len(self.listed()),8)

    def test_list_is_read_only(self):
        self.go()
        made=self.eight()
        before=self.record_bytes()
        self.assertEqual(len(self.client.list_sessions()),8);self.assertEqual(self.record_bytes(),before)

    # ---- a paired tab that went quiet; the check-then-free race; explicit discard ---------------------------

    def quiet_rig(self,paired=3):
        """Eight idle sessions, `paired` finished with a paired tab; the policy clock is a fake we can move."""
        self.seed_finished();self.go()
        made=[Session(self,self.key if n in (paired,5) else None,pair=n==paired) for n in range(8)]
        for session in made:session.close_agent()
        self.now=[time.monotonic()]
        self.owner.policy.clock=lambda:self.now[0]
        self.now[0]=max(entry.seen for entry in self.owner.policy._entries.values())
        return made

    def test_a_paired_tab_that_went_quiet_longer_than_idle_is_freed(self):
        made=self.quiet_rig();target=made[3]
        self.assertTrue(self.listed()[target.bid]['in_use'])
        self.now[0]+=IDLE-1
        self.assertTrue(self.listed()[target.bid]['in_use'])
        self.now[0]+=2
        self.assertFalse(self.listed()[target.bid]['in_use'])
        os.utime(made[5].path,(9999999999,9999999999))      # session 3 is the older finished one
        Session(self,None)
        self.assertFalse(target.path.exists());self.assertTrue(made[5].path.exists())

    def test_the_pages_own_state_polling_does_not_hold_a_finished_session(self):
        """A finished tab left on screen polls state every 2 s; that is not a human, so the slot still frees."""
        made=self.quiet_rig();target=made[3]
        for _ in range(3):
            self.now[0]+=IDLE/2
            self.assertEqual(target.state()[0],200)
        self.assertFalse(self.listed()[target.bid]['in_use'])

    def test_a_human_action_within_idle_keeps_the_session_in_use(self):
        made=self.quiet_rig();target=made[3]
        self.now[0]+=IDLE+100
        self.assertFalse(self.listed()[target.bid]['in_use'])
        self.assertEqual(target.ping()[0],200)               # the human types: seen now
        self.now[0]+=IDLE-10
        self.assertTrue(self.listed()[target.bid]['in_use'])
        os.utime(made[5].path,(9999999999,9999999999))
        Session(self,None)                                   # the other finished one goes instead
        self.assertTrue(target.path.exists());self.assertFalse(made[5].path.exists())

    def pair_between_list_and_free(self,made,target):
        original=self.owner._sessions
        fired=[]
        def hooked(records):
            rows=original(records)
            if fired:return rows                              # only the first list is raced
            fired.append(1)
            status,body,_=self.wire('/api/v1/pair',{'code':target.opened['pairing_code']},{'X-Idea-Binding':target.bid})
            self.assertEqual(status,200,body)
            return rows
        self.owner._sessions=hooked

    def test_a_tab_pairing_between_the_list_and_the_free_is_not_freed(self):
        self.seed_finished();self.go()
        made=[Session(self,self.key if n in (3,5) else None) for n in range(8)]
        for session in made:session.close_agent()
        os.utime(made[3].path,(1000,1000));os.utime(made[5].path,(2000,2000))
        self.pair_between_list_and_free(made,made[3])
        Session(self,None)
        self.assertTrue(made[3].path.exists());self.assertFalse(made[5].path.exists())   # next candidate

    def test_the_only_candidate_pairing_in_the_window_refuses_with_the_list(self):
        self.seed_finished();self.go()
        made=[Session(self,self.key if n==3 else None) for n in range(8)]
        for session in made:session.close_agent()
        self.pair_between_list_and_free(made,made[3])
        before=self.record_bytes()
        caught=self.refused('binding_capacity',lambda:self.client.open_binding('new'))
        self.assertEqual(len(caught.sessions),8)
        self.assertEqual(self.record_bytes(),before);self.assertEqual(len(self.owner.members),8)

    def test_policy_discard_only_if_idle_changes_nothing_when_live(self):
        made=self.quiet_rig();target=made[3]
        self.assertFalse(self.owner.policy.discard(target.bid,only_if_idle=True))
        self.assertIn(target.bid,self.owner.policy._entries);self.assertTrue(target.path.exists())
        self.assertEqual(target.state()[0],200)              # credentials untouched
        self.now[0]+=IDLE+1
        self.assertTrue(self.owner.policy.discard(target.bid,only_if_idle=True))
        self.assertNotIn(target.bid,self.owner.policy._entries)

    def test_explicit_discard_with_confirm_still_frees_an_in_use_session(self):
        made=self.quiet_rig();target=made[3]
        self.refused('session_in_use',lambda:self.client.discard_binding(target.bid))
        self.assertEqual(self.client.discard_binding(target.bid,True),dict(binding_id=target.bid,was_in_use=True))
        self.assertFalse(target.path.exists());self.assertNotIn(target.bid,self.owner.members)

    def test_listing_eight_finished_sessions_is_fast(self):
        self.seed_finished();self.go()
        made=[Session(self,self.key) for _ in range(8)]
        for session in made:session.close_agent()
        started=time.monotonic();rows=self.client.list_sessions(timeout=5);seconds=time.monotonic()-started
        self.assertEqual((len(rows),all(row['finished'] for row in rows)),(8,True))
        self.assertLess(seconds,2.5)  # half the 5 s control budget the terminal verbs and the capacity refusal use

    # ---- restart ---------------------------------------------------------------------------------------------

    def test_freed_slots_stay_free_and_retained_ones_stay_after_a_restart(self):
        self.seed_finished();self.go()
        made=self.eight(selected={0:self.key})
        ninth=Session(self,None)                       # frees made[0]
        self.client.discard_binding(made[7].bid)       # and discard made[7]
        retained={s.bid for s in made[1:7]}|{ninth.bid}
        self.assertEqual(set(self.listed()),retained)
        self.restart()
        self.assertEqual({r['binding_id'] for r in self.client.list_bindings()},retained)
        self.assertEqual(self.owner.members,retained)
        self.assertFalse(made[0].path.exists());self.assertFalse(made[7].path.exists())
        # Capacity is correct after the restart: one free slot, then full.
        Session(self,None)
        self.refused('binding_capacity',lambda:self.client.open_binding('new'))
        with Store(self.store,observer=ACTOR).transaction() as state:self.assertIn(self.key,state['ideas'])


if __name__=='__main__':unittest.main()
