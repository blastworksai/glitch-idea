"""real owned service/browser/native agent integration and drainage."""
import http.client
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0,str(SCRIPTS))
from idea_agent_client import AgentClient, AgentClientError
import idea_agent_source as source_adapter
from idea_bridge import RequestInfo, BridgeError, MAX_JSON, MAX_STATE_RESPONSE
from idea_sessions import SessionPolicy
from idea_launch import OwnerService
from idea_service import TrustedStepHandler
from idea_runtime import Runtime, RuntimeError
from test_workflow import fields


@unittest.skipUnless(os.name=='posix','Native owner ACLs remain unqualified')
class AgentLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name);self.store=self.directory/'ideas';self.root=self.directory/'private'
        self.workspace=self.directory/'workspace';self.workspace.mkdir()
        self.owner=None;self.start_owner();self.addCleanup(self.cleanup)
        self.client=Runtime(self.store,self.root)
        self.opened=self.client.open_binding('new');self.pair()
        result=self.browser('capture',dict(request_id='capture-1',raw_text='Exact source words',
                    workspace=dict(name='Fixture',path=str(self.workspace),confirmed=True)))
        self.idea_id=result['idea_id']
        self.agent=AgentClient(self.client,self.opened['session_id'])

    def start_owner(self):
        runtime=Runtime(self.store,self.root).acquire_owner()
        self.owner=OwnerService(runtime);self.owner.start()

    def cleanup(self):
        if self.owner is not None:self.owner.shutdown();self.owner=None

    def wire(self,path,payload=None,headers=None,*,timeout=5):
        conn=http.client.HTTPConnection('127.0.0.1',self.owner.server.server_port,timeout=timeout)
        h=dict(headers or {})
        if payload is not None:h.update({'Content-Type':'application/json','Origin':self.owner.server.origin})
        try:
            conn.request('POST' if payload is not None else 'GET',path,
                         None if payload is None else json.dumps(payload),h)
            response=conn.getresponse();return response.status,json.loads(response.read()),response.getheader('Set-Cookie')
        finally:conn.close()

    def pair(self):
        status,result,cookie=self.wire('/api/v1/pair',{'code':self.opened['pairing_code']},
                                      {'X-Idea-Binding':self.opened['binding_id']})
        self.assertEqual(status,200,result)
        self.cookie=cookie.split(';',1)[0];self.csrf=result['csrf_token'];self.tab=result['tab_secret']

    def browser(self,name,payload=None,ok=True,expected_status=None,*,timeout=5):
        headers={'X-Idea-Binding':self.opened['binding_id'],'Cookie':self.cookie,'X-Idea-Tab':self.tab}
        if payload is not None:headers['X-CSRF-Token']=self.csrf
        status,result,_=self.wire('/api/v1/'+name,payload,headers,timeout=timeout)
        self.assertEqual(status,(200 if ok else 400) if expected_status is None else expected_status,result);return result

    def enqueue(self,request='proposal-1'):
        state=self.browser('state');source=state['proposal_sources']['shape']['source']
        self.assertTrue(state['capabilities']['agent']);self.assertTrue(state['capabilities']['memory'])
        self.assertRegex(state['agent_generation'],r'^agent_[0-9a-f]{32}$')
        self.browser('propose',dict(request_id=request,idea_id=self.idea_id,
            expected_revision=source['accepted_revision'],expected_draft_version=source['draft_version'],
            operation='shape',source_digest=source['source_digest']))
        event=self.agent.events(timeout=0)['events'][0]
        return {k:v for k,v in event.items() if k not in ('data','sequence')}

    def reply(self,correlation):return dict(correlation,proposal=fields()['shape'])

    def draft(self,step='shape',value=None,request='draft-1'):
        state=self.browser('state')
        return self.browser('draft',dict(request_id=request,idea_id=self.idea_id,expected_revision=state['revision'],
            expected_draft_version=state['draft_version'],step=step,fields={'outcome':'Edited buffer'} if value is None else value))

    def test_actual_capture_enqueue_response_replay_and_markdown_proposal_projection(self):
        correlation=self.enqueue();before=self.browser('state')
        reply=self.reply(correlation);response=self.agent.respond(reply)
        self.assertEqual(self.agent.respond(reply),response)
        after=self.browser('state')
        self.assertEqual((after['revision'],after['draft_version']),(before['revision'],before['draft_version']))
        self.assertEqual(after['accepted'],before['accepted']);self.assertEqual(after['drafts'],before['drafts'])
        self.assertEqual(len(after['proposals']),1)
        self.assertEqual(after['proposals'][0]['proposal_id'],response['evidence']['proposal_id'])
        self.assertFalse(after['proposals'][0]['stale']);self.assertTrue(after['proposals'][0]['acceptance_eligible'])
        self.assertTrue((self.store/response['evidence']['path']).is_file())
        self.assertEqual(after['proposals'][0]['evidence'],{key:response['evidence'][key] for key in ('path','sha256')})
        self.assertFalse(after['proposals'][0]['content_omitted'])
        credentials=self.owner.policy.agent_credentials(self.opened['binding_id'])
        self.assertNotIn(credentials['token'],json.dumps(after));self.assertNotIn(credentials['token'],repr(self.agent))

    def test_small_projection_capacity_keeps_current_reply_and_linked_history_readable(self):
        historical=dict(fields()['shape'],outcome='Historical '+('x'*4096))
        old=self.operation_reply('shape',historical,'historical-proposal')
        self.opened=self.client.open_binding('resume',self.opened['binding_id']);self.pair()
        self.agent=AgentClient(self.client,self.opened['session_id'])
        current=self.operation_reply('shape',fields()['shape'],'current-proposal')
        full=self.browser('state')
        self.assertEqual(full['proposal_inventory'],dict(total=2,projected=2,omitted=0,
            content_omitted=0,index_path=self.idea_id+'.md'))
        budget=copy.deepcopy({key:full[key] for key in ('proposals','proposal_inventory')})
        old_summary=next(item for item in budget['proposals'] if item['proposal_id']==old['evidence']['proposal_id'])
        old_summary.update(proposal=None,content_omitted=True,acceptance_eligible=False,
                           acceptance_reason='projection_omitted')
        budget['proposal_inventory']['content_omitted']=1
        cap=len(json.dumps(budget,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
        with patch.object(source_adapter,'MAX_PROJECTION_BYTES',cap):
            bounded=self.browser('state')
        self.assertEqual(bounded['proposal_inventory'],budget['proposal_inventory'])
        newest=next(item for item in bounded['proposals'] if item['proposal_id']==current['evidence']['proposal_id'])
        self.assertTrue(newest['acceptance_eligible']);self.assertFalse(newest['content_omitted'])
        self.assertEqual(newest['proposal'],fields()['shape'])
        omitted=next(item for item in bounded['proposals'] if item['content_omitted'])
        self.assertEqual(omitted['evidence'],{key:old['evidence'][key] for key in ('path','sha256')})
        self.assertIsNone(omitted['proposal']);self.assertFalse(omitted['acceptance_eligible'])
        self.assertEqual(omitted['acceptance_reason'],'projection_omitted')
        original=(self.store/omitted['evidence']['path']).read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(),omitted['evidence']['sha256'])
        self.assertIn(historical['outcome'].encode(),original)
        with patch.object(source_adapter,'MAX_PROJECTIONS',1):
            one=self.browser('state')
        self.assertEqual(one['proposal_inventory'],dict(total=2,projected=1,omitted=1,
            content_omitted=0,index_path=self.idea_id+'.md'))
        self.assertEqual(one['proposals'][0]['proposal_id'],current['evidence']['proposal_id'])
        index=(self.store/one['proposal_inventory']['index_path']).read_text()
        self.assertIn(old['evidence']['path'],index);self.assertIn(current['evidence']['path'],index)
        self.assertEqual(self.agent.respond(dict(self.enqueue('after-capacity'),proposal=fields()['shape']))['status'],'completed')

    def test_large_accepted_capture_plus_actual_stale_reply_remains_http_readable(self):
        proposal=dict(fields()['shape'],outcome='x'*50000)
        reply=self.operation_reply('shape',proposal,'large-wire-proposal')
        witness={key:reply['evidence'][key] for key in ('path','sha256')}
        immutable=(self.store/witness['path']).read_bytes()
        capture=dict(raw_text='c'*510000,
            workspace=dict(name='Fixture',path=str(self.workspace),confirmed=True))
        accepted=self.accept_fields('capture',capture,'large-wire-capture',timeout=30)
        self.agent.events(timeout=0)  # Actual heartbeat after large Markdown IO.
        state=self.browser('state',timeout=30)
        compact=json.dumps(state,ensure_ascii=False,separators=(',',':')).encode()
        self.assertGreater(len(compact),MAX_JSON);self.assertLessEqual(len(compact),MAX_STATE_RESPONSE)
        self.assertEqual(state['accepted']['capture'],capture)
        self.assertEqual(state['revision'],accepted['revision'])
        self.assertEqual(state['agent_status'],'connected')
        self.assertTrue(state['proposal_sources']['shape']['available'])
        item=next(item for item in state['proposals'] if item['proposal_id']==reply['evidence']['proposal_id'])
        self.assertTrue(item['stale']);self.assertFalse(item['acceptance_eligible'])
        self.assertFalse(item['content_omitted']);self.assertEqual(item['proposal'],proposal)
        self.assertEqual(item['evidence'],witness)
        self.assertEqual((self.store/witness['path']).read_bytes(),immutable)
        self.assertEqual(hashlib.sha256(immutable).hexdigest(),witness['sha256'])

    def test_large_selected_capture_draft_is_exact_twice_when_disconnected(self):
        self.agent.session_close()
        state=self.browser('state')
        value=dict(raw_text='d'*600000,
            workspace=dict(name='Fixture',path=str(self.workspace),confirmed=True))
        self.browser('draft',dict(request_id='large-disconnected-draft',idea_id=self.idea_id,
            expected_revision=state['revision'],expected_draft_version=state['draft_version'],
            step='capture',fields=value),timeout=30)
        state=self.browser('state',timeout=30)
        self.browser('navigate',dict(request_id='select-large-capture',idea_id=self.idea_id,
            expected_revision=state['revision'],expected_draft_version=state['draft_version'],
            step='capture'),timeout=30)
        state=self.browser('state',timeout=30)
        self.assertEqual(state['agent_status'],'disconnected')
        self.assertEqual(state['drafts']['capture'],value)
        self.assertEqual(state['draft'],dict(step='capture',fields=value))
        self.assertEqual(state['accepted']['capture']['raw_text'],'Exact source words')
        self.assertGreater(len(json.dumps(state,ensure_ascii=False,separators=(',',':')).encode()),MAX_JSON)

    def test_terminal_conversation_fills_reach_the_page_and_never_write_the_idea(self):
        # Redesign R1a: the agent writes agreed fields; the page applies them; the human accepts.
        correlation=self.enqueue()
        before=self.browser('state')
        result=self.agent.fill(dict(correlation,fields={'outcome':'Agreed in the terminal','assumptions':['A stated risk']}))
        self.assertEqual((result['fill_sequence'],result['write_state']),(1,'not_applied'))
        state=self.browser('state')
        talk=state['conversation']
        self.assertEqual((talk['request_id'],talk['operation'],talk['idea_id']),(correlation['request_id'],'shape',self.idea_id))
        self.assertEqual(talk['fills'],[dict(sequence=1,fields={'outcome':'Agreed in the terminal','assumptions':['A stated risk']})])
        self.assertEqual((state['revision'],state['draft_version'],state['drafts']),(before['revision'],before['draft_version'],before['drafts']),
                         'a fill never writes the draft or the idea')
        # A field the human owns, or a malformed one, is refused with its own code.
        with self.assertRaises(AgentClientError) as caught:
            self.agent.fill(dict(correlation,fields={'selection':'bounded-plan'}))
        self.assertEqual(caught.exception.code,'invalid_fill')
        # The draft the page saves after applying a fill does not close the conversation.
        self.draft('shape',{'outcome':'Agreed in the terminal'},request='draft-after-fill')
        self.assertEqual(self.agent.fill(dict(correlation,fields={'next_slice':'Smallest next step'}))['fill_sequence'],2)
        self.assertEqual(len(self.browser('state')['conversation']['fills']),2)
        # Once the idea moves to a new accepted revision, that conversation never reaches the page again.
        self.accept_fields('priorities',{'urgency':6,'importance':7},'accept-after-fill')
        self.assertIsNone(self.browser('state')['conversation'])
        # Review fill-r1: a fill for a revision the idea moved past is refused and renews nothing.
        with self.assertRaises(AgentClientError) as caught:
            self.agent.fill(dict(correlation,fields={'outcome':'Too late'}))
        self.assertEqual(caught.exception.code,'stale_source')

    def raw_get(self,path,headers):
        conn=http.client.HTTPConnection('127.0.0.1',self.owner.server.server_port,timeout=5)
        try:
            conn.request('GET',path,None,headers);response=conn.getresponse()
            return response.status,response.getheader('Content-Type'),response.read()
        finally:conn.close()

    def test_backlog_rows_say_where_to_resume_and_the_markdown_opens_read_only(self):
        # Redesign R5/R6: "idea wizards should be resumable"; "read-only in the browser is good".
        row=[item for item in self.browser('ideas')['ideas'] if item['idea_id']==self.idea_id][0]
        self.assertEqual((row['current_step'],row['completed_steps']),(self.browser('state')['current_step'],1))
        auth={'X-Idea-Binding':self.opened['binding_id'],'Cookie':self.cookie,'X-Idea-Tab':self.tab}
        status,kind,body=self.raw_get('/api/v1/ideas/'+self.idea_id+'/markdown',auth)
        self.assertEqual((status,kind),(200,'text/plain; charset=utf-8'))
        self.assertEqual(body,(self.store/(self.idea_id+'.md')).read_bytes())
        self.assertEqual(self.raw_get('/api/v1/ideas/'+self.idea_id+'/markdown',{})[0],401)
        self.assertEqual(self.raw_get('/api/v1/ideas/idea_'+'f'*32+'/markdown',auth)[0],404)
        for path in ('/api/v1/ideas/..%2FIDEAS/markdown','/api/v1/ideas/IDEAS/markdown','/api/v1/ideas/'+self.idea_id+'/markdown?x=1'):
            with self.subTest(path=path):self.assertIn(self.raw_get(path,auth)[0],(400,404))
        self.assertEqual(self.wire('/api/v1/ideas/'+self.idea_id+'/markdown',{},auth)[0],405)

    def test_rerank_is_an_authenticated_csrf_write_with_a_receipt(self):
        # Redesign R7 over HTTP: the same session/CSRF gates as any browser write.
        listing=self.browser('ideas')
        payload=dict(request_id='rerank-http',idea_id=self.idea_id,expected_backlog_revision=listing['backlog_revision'],position=1,reason='Kept first')
        status,_,_=self.wire('/api/v1/rerank',payload,{'X-Idea-Binding':self.opened['binding_id'],'Cookie':self.cookie,'X-Idea-Tab':self.tab})
        self.assertEqual(status,403,'no CSRF, no write')
        result=self.browser('rerank',payload)
        self.assertEqual((result['write_state'],result['position']),('applied',1))
        self.assertEqual(self.browser('requests/rerank-http'),result)
        self.assertEqual(self.browser('rerank',dict(payload,request_id='rerank-stale'),expected_status=409)['code'],'stale_backlog')

    def test_setup_where_ideas_live_and_test_connection_over_http(self):
        # Redesign R9/R10: the Setup pane's server side; the key is write-only.
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        from fake_workflow_api import FakeWorkflowApi
        key='fixture-key-0123456789'
        self.assertEqual(self.browser('settings'),dict(ok=True,code='ok',where='native',api=dict(base_url=None,key_set=False)))
        self.assertEqual(self.browser('settings/test',{}),dict(ok=True,code='ok',reachable=False,reason='api_not_configured',service=None))
        status,_,_=self.wire('/api/v1/settings/test',{},{'X-Idea-Binding':self.opened['binding_id'],'Cookie':self.cookie,'X-Idea-Tab':self.tab})
        self.assertEqual(status,403,'no CSRF, no outward call')
        with FakeWorkflowApi(key) as fake:
            payload=dict(where='api',base_url=fake.url,key=key)
            status,_,_=self.wire('/api/v1/settings/save',payload,{'X-Idea-Binding':self.opened['binding_id'],'Cookie':self.cookie,'X-Idea-Tab':self.tab})
            self.assertEqual(status,403,'no CSRF, no settings change')
            saved=self.browser('settings/save',payload)
            self.assertEqual(saved['api'],dict(base_url=fake.url,key_set=True)); self.assertNotIn(key,json.dumps(saved))
            self.assertNotIn(key,json.dumps(self.browser('settings')))
            self.assertEqual(self.browser('settings/test',{}),dict(ok=True,code='ok',reachable=True,reason=None,service='Fake board'))
            self.browser('settings/save',dict(payload,key='wrong-key-0123456789'))
            self.assertEqual(self.browser('settings/test',{})['reason'],'unauthorized')
        self.assertEqual(self.browser('settings/save',dict(where='api',base_url='http://board.example',key=key),ok=False)['code'],'insecure_url')
        self.assertEqual(self.browser('settings/save',dict(where='native',base_url=None,key=''))['where'],'native')

    def test_changed_consumed_buffer_refuses_reply_without_publishing(self):
        correlation=self.enqueue()
        self.draft('capture',{'raw_text':'Changed unaccepted source'},request='capture-draft')
        with self.assertRaises(AgentClientError) as caught:self.agent.respond(self.reply(correlation))
        self.assertEqual(caught.exception.code,'stale_source')  # the service's own code, not a masked failure
        self.assertEqual(caught.exception.write_state,'not_applied')
        self.assertEqual(self.browser('state')['proposals'],[])

    def test_rebind_cancels_wait_old_reply_and_preserves_published_evidence(self):
        correlation=self.enqueue();saved=self.agent.respond(self.reply(correlation))
        errors=[];entered=threading.Event()
        original=self.owner.broker.events
        def events(*args):entered.set();return original(*args)
        self.owner.broker.events=events
        def wait():
            try:self.agent.events(after=1,timeout=25)
            except AgentClientError as exc:errors.append(exc.code)
        worker=threading.Thread(target=wait);worker.start();self.assertTrue(entered.wait(2))
        oldcookie=self.cookie
        self.opened=self.client.open_binding('resume',self.opened['binding_id']);worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,['agent_unauthorized'])  # rotated credential, said plainly
        with self.assertRaises(AgentClientError):self.agent.respond(self.reply(correlation))
        self.assertEqual(self.wire('/api/v1/state',headers={'X-Idea-Binding':self.opened['binding_id'],'Cookie':oldcookie,'X-Idea-Tab':self.tab})[0],401)
        self.pair();state=self.browser('state')
        self.assertEqual(state['proposals'][0]['proposal_id'],saved['evidence']['proposal_id'])
        self.assertEqual(state['proposals'][0]['stale_reason'],'wrong_generation')

    def test_restart_restores_disconnected_until_explicit_resume_evidence_survives(self):
        correlation=self.enqueue();saved=self.agent.respond(self.reply(correlation))
        sid,bid=self.opened['session_id'],self.opened['binding_id']
        self.cleanup();self.start_owner()
        restored=self.owner.live_context(type('Context',(),{'actor':self.owner.actor,'session_id':sid})())
        self.assertEqual(restored['agent_status'],'disconnected')
        with self.assertRaises(RuntimeError):AgentClient(self.client,sid)
        self.opened=self.client.open_binding('resume',bid);self.pair()
        self.assertEqual(self.opened['session_id'],sid)
        state=self.browser('state');self.assertEqual(state['agent_status'],'connected')
        self.assertEqual(state['proposals'][0]['proposal_id'],saved['evidence']['proposal_id'])
        self.assertTrue(state['proposals'][0]['stale'])

    def test_agent_wait_never_blocks_actual_browser_draft_and_close_preserves_cookie(self):
        entered=threading.Event();results=[];original=self.owner.broker.events
        def events(*args):entered.set();return original(*args)
        self.owner.broker.events=events
        worker=threading.Thread(target=lambda:results.append(self.agent.events(timeout=1)))
        worker.start();self.assertTrue(entered.wait(2))
        before=time.monotonic();self.draft();self.assertLess(time.monotonic()-before,.8)
        self.assertTrue(worker.is_alive(),'browser save completes while bounded wait remains active')
        worker.join(3);self.assertFalse(worker.is_alive());self.assertEqual(results[0]['events'],[])
        self.agent.session_close()
        state=self.browser('state');self.assertEqual(state['agent_status'],'disconnected')
        self.assertEqual(state['drafts']['shape'],{'outcome':'Edited buffer'})

    def test_shutdown_wakes_wait_before_runtime_unlock_and_credentials_getter_is_serialized(self):
        credentials=self.owner.policy.agent_credentials(self.opened['binding_id'])
        request=RequestInfo('GET','/agent/v1/events',{
            'authorization':'Bearer '+credentials['token'],'x-idea-agent-binding':credentials['binding_id'],
            'x-idea-agent-generation':credentials['generation']},self.owner.server.origin)
        context=self.owner.policy.authorize_agent(request)
        fetched=[]
        context.binding.lock.acquire()
        getter=threading.Thread(target=lambda:fetched.append(self.owner.policy.agent_credentials(credentials['binding_id'])))
        getter.start();time.sleep(.05)
        self.assertTrue(getter.is_alive(),'getter waits for binding guard')
        context.binding.lock.release();getter.join(2);self.assertFalse(getter.is_alive())
        self.assertEqual(len(fetched),1)
        entered=threading.Event();errors=[];results=[]
        original_wait=self.owner.broker.condition.wait
        def observed_wait(*args,**kwargs):
            # Signal inside the broker Condition, exactly before releasing it
            # for a real wait; shutdown cannot cancel before events admission.
            entered.set()
            return original_wait(*args,**kwargs)
        self.owner.broker.condition.wait=observed_wait
        def wait():
            try:results.append(self.agent.events(timeout=25))
            except AgentClientError as exc:errors.append(exc.code)
        worker=threading.Thread(target=wait);worker.start();self.assertTrue(entered.wait(2))
        start=time.monotonic();self.cleanup();worker.join(3)
        self.assertLess(time.monotonic()-start,3);self.assertFalse(worker.is_alive())
        self.assertEqual(errors,[])
        self.assertEqual(results,[dict(ok=True,code='ok',session_id=self.opened['session_id'],
                                       agent_status='disconnected',reason='shutdown',sequence=0,events=[])])
        self.assertFalse(self.client.path.joinpath('discovery.json').exists())
        replacement=Runtime(self.store,self.root).acquire_owner();replacement.close()

    def install_test_handlers(self):
        # Explicit test-only validators qualify provider wiring; no claim about
        # packaged J7 handlers or browser controllers follows from this fixture.
        self.owner.handlers.update({step:TrustedStepHandler(lambda *args:None) for step in ('shape','method')})
        self.opened=self.client.open_binding('resume',self.opened['binding_id']);self.pair()
        self.agent=AgentClient(self.client,self.opened['session_id'])

    def accept_fields(self,step,value,request,proposal=None,ok=True,expected_status=None,*,timeout=5):
        state=self.browser('state',timeout=timeout)
        return self.browser('accept',dict(request_id=request,idea_id=self.idea_id,expected_revision=state['revision'],
            expected_draft_version=state['draft_version'],step=step,fields=value,proposal_id=proposal,
            expected_backlog_revision=None),ok=ok,expected_status=expected_status,timeout=timeout)

    def operation_reply(self,operation,proposal,request):
        state=self.browser('state');source=state['proposal_sources'][operation]['source']
        self.assertIsNotNone(source)
        self.browser('propose',dict(request_id=request,idea_id=self.idea_id,
            expected_revision=source['accepted_revision'],expected_draft_version=source['draft_version'],
            operation=operation,source_digest=source['source_digest']))
        event=self.agent.events(timeout=0)['events'][0]
        payload={k:v for k,v in event.items() if k not in ('data','sequence')}
        return self.agent.respond(dict(payload,proposal=proposal))

    def accept_test_shape(self):
        self.accept_fields('priorities',fields()['priorities'],'accepted-priorities')
        self.accept_fields('shape',fields()['shape'],'accepted-shape')

    def test_edited_shape_autosave_accepts_current_draft_and_records_proposal_receipt(self):
        self.install_test_handlers()
        self.accept_fields('priorities',fields()['priorities'],'accepted-priorities')
        reply=self.operation_reply('shape',fields()['shape'],'edited-shape-proposal')
        proposal=reply['evidence']['proposal_id']
        edited=dict(fields()['shape'],outcome='Human edited outcome',next_slice='Human edited next slice')
        self.draft('shape',edited,'edited-shape-buffer')
        accepted=self.accept_fields('shape',edited,'edited-shape-accept',proposal)
        self.assertEqual(accepted['proposal_id'],proposal)
        state=self.browser('state')
        self.assertEqual(state['accepted']['shape'],edited)
        self.assertEqual(state['steps']['shape']['status'],'saved')
        receipt=self.browser('requests/edited-shape-accept')
        self.assertEqual(receipt,accepted)

    def test_human_method_differs_from_recommendation_with_grounded_memory_and_resume_generation(self):
        self.install_test_handlers();self.accept_test_shape()
        memory=dict(status='found',sources=['decision:fixture-choice'],rationale='Fixture reports a found preference')
        self.operation_reply('memory',memory,'memory-found')
        self.draft('method',{'memory':memory},'memory-method-buffer')
        recommendation=dict(fields()['method'],memory=memory)
        reply=self.operation_reply('method',recommendation,'method-recommendation')
        chosen=dict(recommendation,selection='adaptive-slices',reason='Human chose adaptive delivery')
        self.draft('method',chosen,'human-method-buffer')
        accepted=self.accept_fields('method',chosen,'human-method-accept',reply['evidence']['proposal_id'])
        self.assertEqual(accepted['proposal_id'],reply['evidence']['proposal_id'])
        state=self.browser('state');self.assertEqual(state['accepted']['method']['selection'],'adaptive-slices')
        self.assertEqual(state['accepted']['method']['memory'],memory)
        previous=state['agent_generation']
        self.opened=self.client.open_binding('resume',self.opened['binding_id']);self.pair()
        resumed=self.browser('state')
        self.assertNotEqual(resumed['agent_generation'],previous)
        self.assertEqual(resumed['agent_status'],'connected')

    def test_forged_memory_refused_and_disconnected_manual_unavailable_accepts(self):
        self.install_test_handlers();self.accept_test_shape()
        for status,refs in (('found',['note:unverified']),('searched_no_preference',[])):
            forged=dict(fields()['method'],memory=dict(status=status,sources=refs,rationale='Unverified claim'))
            error=self.accept_fields('method',forged,'forged-'+status,ok=False)
            self.assertEqual(error['code'],'memory_provenance_missing')
        self.agent.session_close()
        accepted=self.accept_fields('method',fields()['method'],'manual-unavailable-method')
        self.assertNotIn('proposal_id',accepted)
        state=self.browser('state');self.assertEqual(state['agent_status'],'disconnected')
        self.assertFalse(state['capabilities']['memory'])
        self.assertEqual(state['accepted']['method']['memory']['status'],'unavailable')

    def test_absent_live_context_refuses_memory_claims_and_allows_manual_unavailable(self):
        self.install_test_handlers();self.accept_test_shape()
        # Test-only provider context represents no authenticated live incarnation.
        # The actual HTTP Service/provider/Store acceptance path still executes.
        self.owner.agent_provider.context=lambda context:None
        for status,refs in (('found',['note:unverified']),('searched_no_preference',[])):
            forged=dict(fields()['method'],memory=dict(status=status,sources=refs,rationale='Unverified claim'))
            error=self.accept_fields('method',forged,'absent-live-'+status,ok=False,expected_status=503)
            self.assertEqual(error['code'],'agent_unavailable')
        result=self.accept_fields('method',fields()['method'],'absent-live-unavailable')
        self.assertNotIn('proposal_id',result)
        state=self.browser('state')
        self.assertIsNone(state['agent_generation']);self.assertEqual(state['agent_status'],'disconnected')
        self.assertEqual(state['proposal_inventory'],dict(total=0,projected=0,omitted=0,content_omitted=0,
            index_path=self.idea_id+'.md'))
        self.assertFalse(state['capabilities']['agent']);self.assertFalse(state['capabilities']['memory'])

    def test_session_and_state_memory_capability_agree_connected_then_closed(self):
        session=self.browser('session');state=self.browser('state')
        self.assertEqual(session['agent_status'],state['agent_status'])
        self.assertTrue(session['capabilities']['memory']);self.assertTrue(state['capabilities']['memory'])
        self.agent.session_close()
        session=self.browser('session');state=self.browser('state')
        self.assertEqual(session['agent_status'],state['agent_status'])
        self.assertFalse(session['capabilities']['memory']);self.assertFalse(state['capabilities']['memory'])

    def test_session_and_state_memory_capability_agree_paused_without_retrieval_claim(self):
        future=time.monotonic()+601
        self.owner.broker.clock=lambda:future
        session=self.browser('session');state=self.browser('state')
        self.assertEqual(session['agent_status'],'paused');self.assertEqual(state['agent_status'],'paused')
        self.assertFalse(session['capabilities']['memory']);self.assertFalse(state['capabilities']['memory'])
        self.assertEqual(session['resume'],state['resume'])
        for flag in (None,1,'true'):
            with self.assertRaises(BridgeError) as caught:
                SessionPolicy(self.owner.application,self.owner.runtime.persist_binding,
                              namespace='fixture',memory_capability=flag)
            self.assertEqual(caught.exception.code,'invalid_policy')


if __name__=='__main__':unittest.main()
