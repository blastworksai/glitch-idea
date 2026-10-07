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

    def ready(self):
        # Workflow v3: every agent operation consumes Priorities (and Discovery also the human's Method),
        # so the earliest source exists only once those are accepted. Done once per test, by hand.
        if getattr(self,'_ready',False):return
        self.install_test_handlers()
        self.accept_fields('priorities',fields()['priorities'],'ready-priorities')
        self.accept_fields('method',fields()['method'],'ready-method')
        self._ready=True

    def enqueue(self,request='proposal-1'):
        self.ready()
        state=self.browser('state');source=state['proposal_sources']['discovery']['source']
        self.assertTrue(state['capabilities']['agent']);self.assertTrue(state['capabilities']['memory'])
        self.assertRegex(state['agent_generation'],r'^agent_[0-9a-f]{32}$')
        self.browser('propose',dict(request_id=request,idea_id=self.idea_id,
            expected_revision=source['accepted_revision'],expected_draft_version=source['draft_version'],
            operation='discovery',source_digest=source['source_digest']))
        event=self.agent.events(timeout=0)['events'][0]
        return {k:v for k,v in event.items() if k not in ('data','sequence')}

    def reply(self,correlation):return dict(correlation,proposal=fields()['discovery'])

    def draft(self,step='discovery',value=None,request='draft-1'):
        state=self.browser('state')
        return self.browser('draft',dict(request_id=request,idea_id=self.idea_id,expected_revision=state['revision'],
            expected_draft_version=state['draft_version'],step=step,fields={'problem':'Edited buffer'} if value is None else value))

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
        historical=dict(fields()['discovery'],problem='Historical '+('x'*4096))
        self.ready()
        old=self.operation_reply('discovery',historical,'historical-proposal')
        self.opened=self.client.open_binding('resume',self.opened['binding_id']);self.pair()
        self.agent=AgentClient(self.client,self.opened['session_id'])
        current=self.operation_reply('discovery',fields()['discovery'],'current-proposal')
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
        self.assertEqual(newest['proposal'],fields()['discovery'])
        omitted=next(item for item in bounded['proposals'] if item['content_omitted'])
        self.assertEqual(omitted['evidence'],{key:old['evidence'][key] for key in ('path','sha256')})
        self.assertIsNone(omitted['proposal']);self.assertFalse(omitted['acceptance_eligible'])
        self.assertEqual(omitted['acceptance_reason'],'projection_omitted')
        original=(self.store/omitted['evidence']['path']).read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(),omitted['evidence']['sha256'])
        self.assertIn(historical['problem'].encode(),original)
        with patch.object(source_adapter,'MAX_PROJECTIONS',1):
            one=self.browser('state')
        self.assertEqual(one['proposal_inventory'],dict(total=2,projected=1,omitted=1,
            content_omitted=0,index_path=self.idea_id+'.md'))
        self.assertEqual(one['proposals'][0]['proposal_id'],current['evidence']['proposal_id'])
        index=(self.store/one['proposal_inventory']['index_path']).read_text()
        self.assertIn(old['evidence']['path'],index);self.assertIn(current['evidence']['path'],index)
        self.assertEqual(self.agent.respond(dict(self.enqueue('after-capacity'),proposal=fields()['discovery']))['status'],'completed')

    def test_large_accepted_capture_plus_actual_stale_reply_remains_http_readable(self):
        proposal=dict(fields()['discovery'],problem='x'*50000)
        self.ready()
        reply=self.operation_reply('discovery',proposal,'large-wire-proposal')
        witness={key:reply['evidence'][key] for key in ('path','sha256')}
        immutable=(self.store/witness['path']).read_bytes()
        capture=dict(raw_text='c'*510000,
            workspace=dict(name='Fixture',path=str(self.workspace),confirmed=True))
        self.accept_fields('capture',capture,'large-wire-capture',timeout=30)
        # v3: a new Capture invalidates the accepted Priorities and Method it fed, so the human re-accepts them.
        self.accept_fields('priorities',fields()['priorities'],'large-wire-priorities',timeout=30)
        accepted=self.accept_fields('method',fields()['method'],'large-wire-method',timeout=30)
        self.agent.events(timeout=0)  # Actual heartbeat after large Markdown IO.
        state=self.browser('state',timeout=30)
        compact=json.dumps(state,ensure_ascii=False,separators=(',',':')).encode()
        self.assertGreater(len(compact),MAX_JSON);self.assertLessEqual(len(compact),MAX_STATE_RESPONSE)
        self.assertEqual(state['accepted']['capture'],capture)
        self.assertEqual(state['revision'],accepted['revision'])
        self.assertEqual(state['agent_status'],'connected')
        self.assertTrue(state['proposal_sources']['discovery']['available'])
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
        result=self.agent.fill(dict(correlation,fields={'problem':'Agreed in the terminal','challenges':[{'challenge':'A stated risk','response':'Checked'}]}))
        self.assertEqual((result['fill_sequence'],result['write_state']),(1,'not_applied'))
        state=self.browser('state')
        talk=state['conversation']
        self.assertEqual((talk['request_id'],talk['operation'],talk['idea_id']),(correlation['request_id'],'discovery',self.idea_id))
        self.assertEqual(talk['fills'],[dict(sequence=1,fields={'problem':'Agreed in the terminal','challenges':[{'challenge':'A stated risk','response':'Checked'}]})])
        self.assertEqual((state['revision'],state['draft_version'],state['drafts']),(before['revision'],before['draft_version'],before['drafts']),
                         'a fill never writes the draft or the idea')
        # A field the human owns, or a malformed one, is refused with its own code.
        with self.assertRaises(AgentClientError) as caught:
            self.agent.fill(dict(correlation,fields={'selection':'bounded-plan'}))
        self.assertEqual(caught.exception.code,'invalid_fill')
        # The draft the page saves after applying a fill does not close the conversation.
        self.draft('discovery',{'problem':'Agreed in the terminal'},request='draft-after-fill')
        self.assertEqual(self.agent.fill(dict(correlation,fields={'workaround':'Smallest next step'}))['fill_sequence'],2)
        self.assertEqual(len(self.browser('state')['conversation']['fills']),2)
        # Once the idea moves to a new accepted revision, that conversation never reaches the page again.
        self.accept_fields('priorities',{'urgency':5,'importance':7},'accept-after-fill')
        self.assertIsNone(self.browser('state')['conversation'])
        # Review fill-r1: a fill for a revision the idea moved past is refused and renews nothing.
        with self.assertRaises(AgentClientError) as caught:
            self.agent.fill(dict(correlation,fields={'problem':'Too late'}))
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
        self.assertEqual(state['drafts']['discovery'],{'problem':'Edited buffer'})

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
        self.owner.handlers.update({step:TrustedStepHandler(lambda *args:None) for step in ('method','discovery','exploration')})
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

    def accept_test_priorities(self):
        self.accept_fields('priorities',fields()['priorities'],'accepted-priorities')

    def test_edited_discovery_autosave_accepts_current_draft_and_records_proposal_receipt(self):
        self.ready()
        reply=self.operation_reply('discovery',fields()['discovery'],'edited-discovery-proposal')
        proposal=reply['evidence']['proposal_id']
        edited=dict(fields()['discovery'],problem='Human edited problem',evidence='Human edited evidence')
        self.draft('discovery',edited,'edited-discovery-buffer')
        accepted=self.accept_fields('discovery',edited,'edited-discovery-accept',proposal)
        self.assertEqual(accepted['proposal_id'],proposal)
        state=self.browser('state')
        self.assertEqual(state['accepted']['discovery'],edited)
        self.assertEqual(state['steps']['discovery']['status'],'saved')
        receipt=self.browser('requests/edited-discovery-accept')
        self.assertEqual(receipt,accepted)

    def test_human_method_differs_from_preference_with_grounded_memory_and_resume_generation(self):
        # v3 (R8): the agent only reports memory; the human alone selects the method, and may differ from
        # the preference the memory found.
        self.install_test_handlers();self.accept_test_priorities()
        memory=dict(status='found',sources=['decision:fixture-choice'],rationale='Fixture reports a found preference',
                    preferred_method='bounded-plan')
        self.operation_reply('memory',memory,'memory-found')
        self.draft('method',{'memory':memory},'memory-method-buffer')
        chosen=dict(fields()['method'],memory=memory,selection='adaptive-slices',reason='Human chose adaptive delivery')
        self.draft('method',chosen,'human-method-buffer')
        # Grounded by the connected agent's own memory evidence (no linked Method proposal here).
        accepted=self.accept_fields('method',chosen,'human-method-accept')
        self.assertNotIn('proposal_id',accepted)
        state=self.browser('state');self.assertEqual(state['accepted']['method']['selection'],'adaptive-slices')
        self.assertNotEqual(state['accepted']['method']['selection'],memory['preferred_method'])
        self.assertEqual(state['accepted']['method']['memory'],memory)
        previous=state['agent_generation']
        self.opened=self.client.open_binding('resume',self.opened['binding_id']);self.pair()
        resumed=self.browser('state')
        self.assertNotEqual(resumed['agent_generation'],previous)
        self.assertEqual(resumed['agent_status'],'connected')

    def test_memory_only_method_proposal_is_projected_to_the_page(self):
        self.install_test_handlers();self.accept_test_priorities()
        memory=dict(status='varied',sources=[],rationale=None,preferred_method=None)
        self.operation_reply('method',{'memory':memory},'method-memory-proposal')
        state=self.browser('state')
        self.assertEqual(state['proposals'][0]['proposal'],{'memory':memory})

    def forged_memories(self):
        for status,refs,preferred in (('found',['note:unverified'],'bounded-plan'),('varied',[],None),
                                      ('searched_no_preference',[],None)):
            yield status,dict(fields()['method'],memory=dict(status=status,sources=refs,rationale='Unverified claim',
                                                              preferred_method=preferred))

    def test_forged_memory_refused_and_disconnected_manual_unavailable_accepts(self):
        self.install_test_handlers();self.accept_test_priorities()
        for status,forged in self.forged_memories():
            error=self.accept_fields('method',forged,'forged-'+status,ok=False)
            self.assertEqual(error['code'],'memory_provenance_missing')
        self.agent.session_close()
        accepted=self.accept_fields('method',fields()['method'],'manual-unavailable-method')
        self.assertNotIn('proposal_id',accepted)
        state=self.browser('state');self.assertEqual(state['agent_status'],'disconnected')
        self.assertFalse(state['capabilities']['memory'])
        self.assertEqual(state['accepted']['method']['memory']['status'],'unavailable')

    NOPREF=dict(status='searched_no_preference',sources=[],rationale='No saved preference found',preferred_method=None)
    FOUND=dict(status='found',sources=['decision:fixture-choice'],rationale='Fixture found preference',preferred_method='bounded-plan')
    VARIED=dict(status='varied',sources=[],rationale=None,preferred_method=None)

    def method_request(self,request):
        """The page's own Methods request, delivered to the connected agent; returns its correlation."""
        state=self.browser('state');source=state['proposal_sources']['method']['source']
        self.browser('propose',dict(request_id=request,idea_id=self.idea_id,expected_revision=source['accepted_revision'],
            expected_draft_version=source['draft_version'],operation='method',source_digest=source['source_digest']))
        event=self.agent.events(timeout=0)['events'][0]
        return {k:v for k,v in event.items() if k not in ('data','sequence')}

    def method_fields(self,memory):
        return dict(fields()['method'],memory=memory)

    def fill_only_accepts(self,memory):
        # The documented protocol answers a Methods request with a memory fill only; Accept must honour it.
        self.install_test_handlers();self.accept_test_priorities()
        correlation=self.method_request('method-fill')
        self.agent.fill(dict(correlation,fields={'memory':memory}))
        accepted=self.accept_fields('method',self.method_fields(memory),'fill-accept')
        self.assertEqual(accepted['write_state'],'applied')
        self.assertEqual(self.browser('state')['accepted']['method']['memory'],memory)

    def test_fill_only_searched_no_preference_accepts(self):self.fill_only_accepts(self.NOPREF)
    def test_fill_only_found_with_preferred_method_accepts(self):self.fill_only_accepts(self.FOUND)
    def test_fill_only_varied_accepts(self):self.fill_only_accepts(self.VARIED)

    def test_fill_memory_a_then_accept_memory_b_is_refused_and_last_fill_wins(self):
        self.install_test_handlers();self.accept_test_priorities()
        correlation=self.method_request('method-ab')
        self.agent.fill(dict(correlation,fields={'memory':self.NOPREF}))
        for request,memory in (('b-found',self.FOUND),('b-varied',self.VARIED),
                               ('b-other-rationale',dict(self.NOPREF,rationale='Different words'))):
            error=self.accept_fields('method',self.method_fields(memory),request,ok=False)
            self.assertEqual(error['code'],'memory_provenance_missing')
        self.agent.fill(dict(correlation,fields={'memory':self.FOUND}))
        error=self.accept_fields('method',self.method_fields(self.NOPREF),'first-fill-superseded',ok=False)
        self.assertEqual(error['code'],'memory_provenance_missing')
        self.assertEqual(self.accept_fields('method',self.method_fields(self.FOUND),'last-fill')['write_state'],'applied')

    def test_fill_from_an_earlier_generation_or_stale_revision_is_refused(self):
        self.install_test_handlers();self.accept_test_priorities()
        correlation=self.method_request('method-old-gen')
        self.agent.fill(dict(correlation,fields={'memory':self.NOPREF}))
        previous=self.browser('state')['agent_generation']
        self.opened=self.client.open_binding('resume',self.opened['binding_id']);self.pair()
        self.assertNotEqual(self.browser('state')['agent_generation'],previous)
        error=self.accept_fields('method',self.method_fields(self.NOPREF),'old-generation',ok=False)
        self.assertEqual(error['code'],'memory_provenance_missing')
        self.agent=AgentClient(self.client,self.opened['session_id'])
        correlation=self.method_request('method-stale-rev')
        self.agent.fill(dict(correlation,fields={'memory':self.NOPREF}))
        self.accept_fields('priorities',dict(fields()['priorities'],urgency=2),'move-revision')
        error=self.accept_fields('method',self.method_fields(self.NOPREF),'stale-revision',ok=False)
        self.assertEqual(error['code'],'memory_provenance_missing')

    def test_fill_does_not_survive_the_request_being_released_or_expiring(self):
        self.install_test_handlers();self.accept_test_priorities()
        correlation=self.method_request('method-released')
        self.agent.fill(dict(correlation,fields={'memory':self.NOPREF}))
        state=self.browser('state')
        self.assertEqual(self.owner.broker.release_request(self.opened['binding_id'],state['agent_generation'],self.idea_id,'method'),1)
        error=self.accept_fields('method',self.method_fields(self.NOPREF),'after-release',ok=False)
        self.assertEqual(error['code'],'memory_provenance_missing')

    def test_fill_does_not_survive_the_answer_window_expiring(self):
        self.install_test_handlers();self.accept_test_priorities()
        correlation=self.method_request('method-expired')
        self.agent.fill(dict(correlation,fields={'memory':self.NOPREF}))
        future=time.monotonic()+601
        self.owner.broker.clock=lambda:future
        error=self.accept_fields('method',self.method_fields(self.NOPREF),'after-expiry',ok=False,expected_status=503)
        self.assertEqual(error['code'],'agent_unavailable')

    def test_absent_live_context_refuses_memory_claims_and_allows_manual_unavailable(self):
        self.install_test_handlers();self.accept_test_priorities()
        # Test-only provider context represents no authenticated live incarnation.
        # The actual HTTP Service/provider/Store acceptance path still executes.
        self.owner.agent_provider.context=lambda context:None
        for status,forged in self.forged_memories():
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
