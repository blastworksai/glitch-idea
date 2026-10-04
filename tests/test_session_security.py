"""actual loopback policy/Markdown receipt proofs, not native ACL proof."""
import copy
import http.client
import json
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
from idea_bridge import BridgeServer, BridgeError, RequestInfo
from idea_domain import IdeaError
from idea_service import Service, TrustedContext
from idea_sessions import SessionPolicy
from idea_store import Store


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.workspace=self.root/'workspace';self.workspace.mkdir()
        self.store=Store(self.root/'ideas',observer='Operator')
        self.records={};self.cancelled=[];self.now=100.;self.fail=False
        self.policy=self.make_policy()
        self.sid=self.store.create_session()
        self.bid=self.policy.open_binding('Operator',self.sid)
        self.server=BridgeServer(self.policy);self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start()
        self.addCleanup(self.stop)
        self.cookies={};self.csrf={};self.tabs={}

    def make_policy(self,**kwargs):
        def factory(record):return Service(self.store,{},TrustedContext(record['actor'],record['receipt_session_id'],record['selected_idea_id']))
        def persist(record):
            if self.fail:raise OSError('private credential path must not leak')
            self.records[record['binding_id']]=copy.deepcopy(record)
            record['actor']='callback cannot mutate live binding'
        return SessionPolicy(factory,persist,namespace='store_one',clock=lambda:self.now,
                             cancel=lambda bid,generation:self.cancelled.append((bid,generation)),**kwargs)

    def stop(self):self.server.shutdown();self.server.server_close();self.worker.join()

    def request(self,path,body=None,*,bid=None,auth=True,extra=None):
        headers={'Host':self.server.host}
        if bid is not None:headers['X-Idea-Binding']=bid
        if auth:
            headers['Cookie']='; '.join(self.cookies.values())
            if bid in self.csrf:headers['X-CSRF-Token']=self.csrf[bid]
            if bid in self.tabs:headers['X-Idea-Tab']=self.tabs[bid]
        if body is not None:headers.update({'Origin':self.server.origin,'Content-Type':'application/json'})
        headers.update(extra or {})
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            conn.request('POST' if body is not None else 'GET',path,None if body is None else json.dumps(body),headers)
            response=conn.getresponse();data=json.loads(response.read());return response.status,data,dict(response.getheaders())
        finally:conn.close()

    def pair(self,bid=None):
        bid=bid or self.bid;code=self.policy.issue_pairing(bid)
        status,data,headers=self.request('/api/v1/pair',{'code':code},bid=bid,auth=False)
        self.assertEqual(status,200);self.assertEqual(data['binding_id'],bid)
        self.assertRegex(data['csrf_token'],r'^[0-9a-f]{64}$')
        self.cookies[bid]=headers['Set-Cookie'].split(';')[0];self.tabs[bid]=data['tab_secret']
        status,data,_=self.request('/api/v1/session',bid=bid);self.assertEqual(status,200)
        self.csrf[bid]=data['csrf_token'];return code

    def original(self):return dict(request_id='capture-1',raw_text=' Café 💡\r\n',workspace=dict(name='Explicit',path=str(self.workspace),confirmed=True))

    def domain(self):
        with self.store.transaction() as state:return copy.deepcopy(state)

    def code(self,expected,call):
        with self.assertRaises((BridgeError,IdeaError)) as caught:call()
        self.assertEqual(caught.exception.code,expected)

    def test_initialized_sid_persisted_before_bootstrap_and_no_secret_in_record(self):
        record=self.records[self.bid]
        self.assertEqual(record['receipt_session_id'],self.sid)
        self.assertEqual(set(record),{'schema_version','binding_id','actor','receipt_session_id','selected_idea_id'})
        self.assertRegex(self.bid,r'^binding_[0-9a-f]{32}$')
        credentials=self.policy.agent_credentials(self.bid)
        self.assertNotEqual(credentials['generation'],self.sid)
        self.assertEqual(credentials['session_id'],self.sid)
        self.assertNotIn(credentials['token'],json.dumps(record))
        code=self.pair();status,data,headers=self.request('/api/v1/session',bid=self.bid)
        self.assertEqual(data['session_id'],self.sid);self.assertNotIn('token',data)
        self.assertNotIn(code,json.dumps(data));self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertIn('HttpOnly',self.request_cookie_attributes())

    def request_cookie_attributes(self):
        other_sid=self.store.create_session();other=self.policy.open_binding('Operator',other_sid)
        code=self.policy.issue_pairing(other)
        return self.request('/api/v1/pair',{'code':code},bid=other,auth=False)[2]['Set-Cookie']

    def test_auth_precedes_reads_and_replay_and_rejects_missing_mismatched_cookie_csrf(self):
        self.pair()
        for path in ('/api/v1/session','/api/v1/state','/api/v1/requests/capture-1'):
            self.assertEqual(self.request(path)[0],401) # ambient cookie without selector
            self.assertEqual(self.request(path,bid=self.bid,auth=False)[0],401)
        status,result,_=self.request('/api/v1/capture',self.original(),bid=self.bid);self.assertEqual(status,200)
        self.assertEqual(self.request('/api/v1/capture',self.original(),bid=self.bid,extra={'X-CSRF-Token':'bad'})[0],403)
        self.assertEqual(self.request('/api/v1/capture',self.original(),bid=self.bid,auth=False)[0],401)
        self.assertEqual(self.request('/api/v1/requests/capture-1',bid=self.bid,auth=False)[0],401)
        self.assertEqual(len(self.domain()['ideas']),1)
        self.assertEqual(self.request('/api/v1/capture',self.original(),bid=self.bid)[1],result)

    def test_two_binding_cookie_jar_does_not_switch_lost_capture_receipt_namespace(self):
        self.pair();status,a,_=self.request('/api/v1/capture',self.original(),bid=self.bid);self.assertEqual(status,200)
        sid_b=self.store.create_session();bid_b=self.policy.open_binding('Operator',sid_b);self.pair(bid_b)
        self.assertNotEqual(self.cookies[self.bid].split('=')[0],self.cookies[bid_b].split('=')[0])
        status,session,_=self.request('/api/v1/session',bid=self.bid);self.assertEqual(status,200);self.csrf[self.bid]=session['csrf_token']
        self.assertEqual(self.request('/api/v1/capture',self.original(),bid=self.bid)[1],a)
        self.assertEqual(self.request('/api/v1/requests/capture-1',bid=bid_b)[1]['code'],'request_not_found')
        self.assertEqual(len(self.domain()['ideas']),1)
        cookie_b=self.cookies[bid_b]
        self.assertEqual(self.request('/api/v1/state',bid=self.bid,extra={'Cookie':cookie_b})[0],401)

    def test_authenticated_receipt_replay_precedes_stale_accept_cas(self):
        self.pair();captured=self.request('/api/v1/capture',self.original(),bid=self.bid)[1]
        payload=dict(request_id='accept-1',idea_id=captured['idea_id'],expected_revision=1,
                     expected_draft_version=0,step='priorities',fields=dict(urgency=7,importance=8),
                     proposal_id=None,expected_backlog_revision=None)
        status,accepted,_=self.request('/api/v1/accept',payload,bid=self.bid)
        self.assertEqual(status,200);self.assertEqual(accepted['revision'],2)
        self.assertEqual(self.request('/api/v1/accept',payload,bid=self.bid)[1],accepted)
        status,stale,_=self.request('/api/v1/accept',dict(payload,request_id='new-stale'),bid=self.bid)
        self.assertEqual((status,stale['code']),(409,'stale_revision'))
        self.assertEqual(self.request('/api/v1/accept',payload,bid=self.bid,auth=False)[0],401)
        self.assertEqual(self.request('/api/v1/accept',payload,bid=self.bid,extra={'X-CSRF-Token':'wrong'})[0],403)

    def test_wrong_pinned_pair_preserves_code_and_receipt_binding(self):
        self.pair();sid=self.store.create_session();other=self.policy.open_binding('Operator',sid);code=self.policy.issue_pairing(other)
        status,result,_=self.request('/api/v1/pair',{'code':code},bid=self.bid)
        self.assertEqual((status,result['code']),(403,'session_binding_mismatch'))
        self.assertEqual(self.request('/api/v1/session',bid=self.bid)[1]['session_id'],self.sid)
        status,result,_=self.request('/api/v1/pair',{'code':code},bid=other,auth=False)
        self.assertEqual(status,200);self.assertEqual(result['session_id'],sid)

    def test_pair_replay_invalidates_browser_and_agent_generation(self):
        code=self.pair();generation=self.policy.agent_credentials(self.bid)['generation']
        status,result,_=self.request('/api/v1/pair',{'code':code},bid=self.bid)
        self.assertEqual((status,result['code']),(401,'pairing_replay_session_invalidated'))
        self.assertEqual(self.request('/api/v1/state',bid=self.bid)[0],401)
        self.assertIn((self.bid,generation),self.cancelled)
        self.code('browser_unauthorized',lambda:self.policy.agent_credentials(self.bid))

    def test_expiry_five_attempts_and_explicit_resume_for_new_code(self):
        code=self.policy.issue_pairing(self.bid);generation=self.policy.agent_credentials(self.bid)['generation'];self.now+=60
        self.assertEqual(self.request('/api/v1/pair',{'code':code},bid=self.bid)[1]['code'],'pairing_expired_or_locked')
        self.assertIn((self.bid,generation),self.cancelled)
        self.policy.open_binding('Operator',self.sid,binding_id=self.bid,resume=True)
        code=self.policy.issue_pairing(self.bid)
        for _ in range(5):self.request('/api/v1/pair',{'code':'wrong'},bid=self.bid)
        self.assertEqual(self.request('/api/v1/pair',{'code':code},bid=self.bid)[1]['code'],'pairing_expired_or_locked')
        self.code('pairing_resume_required',lambda:self.policy.issue_pairing(self.bid))

    def test_restart_resume_rotates_credentials_keeps_sid_selection_and_replays_before_workspace_checks(self):
        self.pair();old_agent=self.policy.agent_credentials(self.bid)
        result=self.request('/api/v1/capture',self.original(),bid=self.bid)[1]
        record=copy.deepcopy(self.records[self.bid]);old_cookie=self.cookies[self.bid];old_csrf=self.csrf[self.bid]
        self.policy.open_binding(record['actor'],self.sid,record['selected_idea_id'],binding_id=self.bid,resume=True)
        self.assertEqual(self.request('/api/v1/session',bid=self.bid)[0],401)
        self.pair();new_agent=self.policy.agent_credentials(self.bid)
        self.assertNotEqual(old_agent['generation'],new_agent['generation']);self.assertNotEqual(old_agent['token'],new_agent['token'])
        self.assertNotEqual(old_cookie,self.cookies[self.bid]);self.assertNotEqual(old_csrf,self.csrf[self.bid])
        self.workspace.rmdir()
        self.assertEqual(self.request('/api/v1/capture',self.original(),bid=self.bid)[1],result)
        self.assertEqual(self.request('/api/v1/state',bid=self.bid)[1]['idea_id'],result['idea_id'])
        # Fresh policy/process uses persisted IDs, not a new receipt namespace.
        fresh=self.make_policy();fresh.open_binding(record['actor'],self.sid,record['selected_idea_id'],binding_id=self.bid,resume=True)
        self.assertNotEqual(fresh.agent_credentials(self.bid)['token'],new_agent['token'])
        self.assertEqual(len(self.domain()['ideas']),1)

    def test_missing_receipt_refuses_open_and_authenticated_session_without_recreation(self):
        missing='session_'+'f'*32
        self.code('receipt_session_missing',lambda:self.policy.open_binding('Operator',missing))
        self.pair();path=self.store.path/'session-recovery'/(self.sid+'.json');path.unlink()
        status,result,_=self.request('/api/v1/session',bid=self.bid)
        self.assertEqual((status,result['code']),(500,'receipt_session_missing'));self.assertFalse(path.exists())

    def test_persistence_failure_after_capture_is_committed_uncertainty_then_receipt_repairs_selection(self):
        self.pair();self.fail=True
        status,result,_=self.request('/api/v1/capture',self.original(),bid=self.bid)
        self.assertEqual((status,result['code']),(500,'session_persistence_uncertain'))
        self.assertTrue(result['committed']);self.assertEqual(result['write_state'],'committed_uncertain')
        self.assertNotIn('private',json.dumps(result));self.assertEqual(len(self.domain()['ideas']),1)
        self.fail=False
        status,recovered,_=self.request('/api/v1/requests/capture-1',bid=self.bid)
        self.assertEqual(status,200);self.assertEqual(self.records[self.bid]['selected_idea_id'],recovered['idea_id'])
        self.assertEqual(self.request('/api/v1/capture',self.original(),bid=self.bid)[1],recovered)

    def test_initial_private_persist_failure_exposes_no_binding_credentials(self):
        self.fail=True;sid=self.store.create_session();before=set(self.policy._entries)
        with self.assertRaises(OSError):self.policy.open_binding('Operator',sid)
        self.assertEqual(set(self.policy._entries),before)

    def test_read_only_persistence_failure_does_not_claim_committed_write(self):
        self.pair()
        first=self.request('/api/v1/capture',self.original(),bid=self.bid)[1]['idea_id']
        self.request('/api/v1/capture',dict(self.original(),request_id='capture-2'),bid=self.bid)
        # Reading the first idea changes the persisted selection, so the read must persist it
        # (an unchanged selection is no longer rewritten on every read).
        self.fail=True
        status,result,_=self.request('/api/v1/state?idea_id='+first,bid=self.bid)
        self.assertEqual((status,result['code']),(500,'session_persistence_failed'));self.assertNotIn('committed',result)

    def test_an_unchanged_selection_is_not_rewritten_by_every_read(self):
        # Each rewrite stages a runtime file that agent clients must wait out; page polls made many.
        self.pair(); self.request('/api/v1/capture',self.original(),bid=self.bid)
        writes=[]
        original=self.policy.persist
        self.policy.persist=lambda record:(writes.append(record['selected_idea_id']),original(record))
        for _ in range(3):self.assertEqual(self.request('/api/v1/state',bid=self.bid)[0],200)
        self.assertEqual(writes,[])

    def test_capacity_and_constructor_bounds_and_selector_types(self):
        for _ in range(7):self.policy.open_binding('Operator',self.store.create_session())
        self.code('session_capacity_exhausted',lambda:self.policy.open_binding('Operator',self.store.create_session()))
        for kwargs in ({'max_bindings':True},{'max_bindings':9},{'max_attempts':6},{'pairing_ttl':float('nan')},{'pairing_ttl':61}):
            self.code('invalid_policy',lambda:self.make_policy(**kwargs))
        self.assertEqual(self.request('/api/v1/session',bid='bad')[0],400)
        self.assertEqual(self.request('/api/v1/pair',{'code':'é'},auth=False)[0],401)

    def test_duplicate_matching_cookie_and_stale_csrf_refused(self):
        self.pair();cookie=self.cookies[self.bid]
        self.assertEqual(self.request('/api/v1/session',bid=self.bid,extra={'Cookie':cookie+'; '+cookie})[0],401)
        self.assertEqual(self.request('/api/v1/transport',dict(host=self.server.host,origin=self.server.origin,secure_context=False),bid=self.bid)[0],200)
        self.assertEqual(self.request('/api/v1/transport',dict(host='other',origin=self.server.origin,secure_context=False),bid=self.bid)[0],403)

    def test_actual_public_client_accepts_pair_response_before_session_refresh(self):
        code=self.policy.issue_pairing(self.bid)
        status,pair,headers=self.request('/api/v1/pair',{'code':code},auth=False)
        self.assertEqual(status,200)
        self.cookies[self.bid]=headers['Set-Cookie'].split(';')[0];self.tabs[self.bid]=pair['tab_secret']
        session=self.request('/api/v1/session',bid=self.bid)[1]
        script="""
import fs from 'node:fs';
const source=fs.readFileSync(process.argv[1],'utf8');
const {IdeaApi}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const input=JSON.parse(fs.readFileSync(0,'utf8'));
let responses=[input.pair,input.session];
const api=new IdeaApi(async()=>({ok:true,status:200,json:async()=>responses.shift()}));
await api.pair(input.code);
if(api.bindingId!==input.pair.binding_id||api.sessionId!==input.pair.session_id||api.csrf!==input.session.csrf_token)throw Error('pair contract mismatch');
"""
        api=Path(__file__).resolve().parents[1]/'glitch-idea/web/api.js'
        result=subprocess.run(['node','--input-type=module','-e',script,str(api)],input=json.dumps(dict(pair=pair,session=session,code=code)),text=True,capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_resume_cannot_change_actor_or_receipt_namespace(self):
        self.pair()
        self.code('session_binding_mismatch',lambda:self.policy.open_binding('other',self.sid,binding_id=self.bid,resume=True))
        self.code('session_binding_mismatch',lambda:self.policy.open_binding('Operator',self.store.create_session(),binding_id=self.bid,resume=True))
        self.assertEqual(self.request('/api/v1/session',bid=self.bid)[0],200)

if __name__=='__main__':unittest.main()
