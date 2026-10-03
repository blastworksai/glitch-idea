"""actual source children, authenticated control and commit drain."""
import concurrent.futures
import copy
import http.client
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0,str(SCRIPTS))
import idea_launch as launch
from idea_bridge import BridgeError, RequestInfo
from idea_domain import IdeaError
from idea_runtime import Runtime, RuntimeError as OwnerError
from idea_store import Store


@unittest.skipUnless(os.name=='posix','Native owner ACLs are not qualified on this host')
class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=Path(self.temp.name)/'ideas Café'; self.root=Path(self.temp.name)/'private'
        self.workspace=Path(self.temp.name)/'workspace'; self.workspace.mkdir()
        self.client=Runtime(self.store,self.root)
        self.children=[]; self.children_lock=threading.Lock()
        self.addCleanup(self.cleanup)

    def spawn(self,*args,**kwargs):
        child=subprocess.Popen(*args,**kwargs)
        with self.children_lock:self.children.append(child)
        return child

    def cleanup(self):
        if any(child.poll() is None for child in self.children):
            self.client.request_owned_stop(timeout=3)
        for child in self.children:child.wait(timeout=5)

    def ensure(self,**kwargs):
        return launch.ensure_service(self.store,self.root,spawn=self.spawn,**kwargs)

    def opened(self,**kwargs):
        return launch.open_session(self.store,self.root,spawn=self.spawn,**kwargs)

    def request(self,opened,route,payload=None,*,cookie=None,csrf=None):
        port=int(opened['origin'].split(':')[-1].rstrip('/'))
        headers={'Host':'127.0.0.1:'+str(port),'X-Idea-Binding':opened['binding_id']}
        if cookie:headers['Cookie']=cookie
        if payload is not None:
            headers.update({'Origin':opened['origin'].rstrip('/'),'Content-Type':'application/json'})
            if csrf:headers['X-CSRF-Token']=csrf
        conn=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
        try:
            conn.request('POST' if payload is not None else 'GET','/api/v1/'+route,
                         None if payload is None else json.dumps(payload),headers)
            response=conn.getresponse();body=json.loads(response.read())
            return response.status,body,response.getheader('Set-Cookie')
        finally:conn.close()

    def pair(self,opened):
        status,body,cookie=self.request(opened,'pair',{'code':opened['pairing_code']})
        self.assertEqual(status,200,body)
        self.assertEqual(body['session_id'],opened['session_id'])
        return cookie.split(';',1)[0],body['csrf_token']

    def test_actual_source_child_startup_reuse_and_owned_stop(self):
        before=self.ensure();self.assertEqual(len(self.children),1)
        after=self.ensure();self.assertEqual(before['origin'],after['origin'])
        self.assertEqual(before['identity']['instance_nonce'],after['identity']['instance_nonce'])
        self.assertEqual(len(self.children),1)
        self.assertFalse((self.store/'IDEAS.md').exists(),'serve alone creates no domain receipt')
        result=self.client.request_owned_stop(timeout=2);self.assertEqual(result['code'],'stopping')
        self.children[0].wait(timeout=4)
        with self.assertRaises(OwnerError):self.client.probe_owner()

    def test_actual_two_parent_readiness_race_reuses_single_owner(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.ensure(),range(2)))
        self.assertEqual(results[0]['origin'],results[1]['origin'])
        self.assertEqual(results[0]['identity']['instance_nonce'],results[1]['identity']['instance_nonce'])
        self.assertLessEqual(len(self.children),2)
        opened=self.opened(); self.assertEqual(len(self.client.list_bindings()),1)
        self.pair(opened)

    def test_restart_resume_same_sid_selection_and_lost_capture_replay(self):
        opened=self.opened();cookie,csrf=self.pair(opened)
        payload=dict(request_id='lost-capture',raw_text='Exact words\r\n',workspace=dict(name='Fixture',path=str(self.workspace),confirmed=True))
        status,result,_=self.request(opened,'capture',payload,cookie=cookie,csrf=csrf)
        self.assertEqual(status,200,result)
        key=result['idea_id']; before=copy.deepcopy(self.client.load_binding(opened['binding_id']))
        self.assertEqual(before['selected_idea_id'],key)
        self.client.request_owned_stop(timeout=2);self.children[-1].wait(timeout=4)
        resumed=self.opened(binding_id=opened['binding_id'])
        self.assertEqual(resumed['session_id'],opened['session_id']);self.assertEqual(resumed['selected_idea_id'],key)
        self.assertNotEqual(resumed['identity']['instance_nonce'],opened['identity']['instance_nonce'])
        self.assertEqual(self.request(resumed,'session',cookie=cookie)[0],401)
        newcookie,newcsrf=self.pair(resumed)
        status,replayed,_=self.request(resumed,'capture',payload,cookie=newcookie,csrf=newcsrf)
        self.assertEqual(status,200);self.assertEqual(replayed,result)
        with Store(self.store).transaction() as state:self.assertEqual(len(state['ideas']),1)
        self.assertEqual(self.client.load_binding(opened['binding_id'])['receipt_session_id'],opened['session_id'])

    def test_capacity_and_selected_idea_rejection_allocate_no_orphan_receipt(self):
        self.ensure()
        with self.assertRaises(OwnerError):self.opened(selected_idea_id='idea_'+'f'*32)
        self.assertEqual(self.client.list_bindings(),[])
        self.assertFalse((self.store/'session-recovery').exists())
        for _ in range(8):self.opened()
        paths=set((self.store/'session-recovery').glob('*.json'))
        self.assertEqual(len(paths),8)
        with self.assertRaises(OwnerError) as caught:self.opened()
        self.assertEqual(caught.exception.code,'binding_capacity')
        self.assertEqual(set((self.store/'session-recovery').glob('*.json')),paths)

    def test_private_config_descriptor_and_child_outputs_no_credentials(self):
        config=dict(plan_validator_argv=['validator','sensitive-fixture-value'],validator_timeout_seconds=11)
        def inspect_spawn(argv,**kwargs):
            self.assertNotIn('sensitive-fixture-value',' '.join(argv))
            stream=kwargs['stdin']; self.assertEqual(stat.S_IMODE(os.fstat(stream.fileno()).st_mode),0o600)
            observed=json.loads(stream.read());stream.seek(0)
            self.assertEqual(observed['plan_validator_argv'],config['plan_validator_argv'])
            self.assertFalse(kwargs['shell']);self.assertTrue(kwargs['close_fds'])
            kwargs.update(stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            return self.spawn(argv,**kwargs)
        launch.ensure_service(self.store,self.root,config,spawn=inspect_spawn)
        opened=self.client.open_binding('new')
        self.client.request_owned_stop(timeout=2)
        out,err=self.children[0].communicate(timeout=4)
        self.assertEqual(out,b'');self.assertEqual(err,b'')
        self.assertNotIn(opened['pairing_code'].encode(),out+err)

    def test_real_losing_private_child_reports_runtime_busy_without_secret(self):
        self.ensure()
        result=subprocess.run([sys.executable,str(launch.CHILD),'--store',str(self.store),'--runtime-root',str(self.root),'serve'],
                              capture_output=True,timeout=3)
        self.assertEqual(result.returncode,1)
        self.assertEqual(result.stdout,b'')
        self.assertEqual(json.loads(result.stderr),dict(ok=False,code='runtime_busy'))

    def test_root_only_native_launch_and_failure_retains_resume_reference(self):
        calls=[]
        def runner(argv,**kwargs):calls.append(argv);return SimpleNamespace(returncode=0,stdout='',stderr='')
        opened=launch.open_browser_session(self.store,self.root,mode='system',runner=runner,spawn=self.spawn)
        self.assertEqual(calls[0][-1],opened['origin'])
        self.assertNotIn('?',calls[0][-1]);self.assertNotIn(opened['pairing_code'],' '.join(calls[0]))
        def refuse(argv,**kwargs):return SimpleNamespace(returncode=1,stdout='',stderr='')
        with self.assertRaises(launch.LaunchError) as caught:
            launch.open_browser_session(self.store,self.root,mode='system',runner=refuse,spawn=self.spawn)
        self.assertEqual(caught.exception.code,'system_browser_unavailable')
        self.assertTrue(caught.exception.details['resume_required'])
        self.assertNotIn('pairing_code',caught.exception.details)
        resumed=self.opened(binding_id=caught.exception.details['binding_id'])
        self.assertEqual(resumed['session_id'],caught.exception.details['session_id'])

    def test_mismatched_owner_and_timeout_read_only_retry_without_spawning(self):
        clock=[0.0]; probes=[];spawns=[]
        def sleep(seconds):clock[0]+=seconds
        def unavailable(self,timeout=1):probes.append(True);raise OwnerError('owner_unavailable')
        fake=SimpleNamespace(poll=lambda:None)
        def spawn(*args,**kwargs):spawns.append(args);return fake
        with patch.object(Runtime,'probe_owner',unavailable):
            with self.assertRaises(launch.LaunchError) as caught:
                launch.ensure_service(self.store,self.root,spawn=spawn,clock=lambda:clock[0],sleep=sleep,readiness_timeout=.2)
        self.assertEqual(caught.exception.code,'service_readiness_timeout');self.assertEqual(len(spawns),1)
        clock[0]=0;spawns.clear()
        def mismatch(self,timeout=1):raise OwnerError('owner_identity_mismatch')
        with patch.object(Runtime,'probe_owner',mismatch):
            with self.assertRaises(OwnerError) as caught:
                launch.ensure_service(self.store,self.root,spawn=spawn,clock=lambda:clock[0],sleep=sleep,readiness_timeout=.2)
        self.assertEqual(caught.exception.code,'owner_identity_mismatch');self.assertEqual(spawns,[])

    def test_a_busy_owner_is_retried_never_respawned_and_reported_busy(self):
        clock=[0.0];spawns=[]
        def sleep(seconds):clock[0]+=seconds
        def spawn(*args,**kwargs):spawns.append(args);return SimpleNamespace(poll=lambda:None)
        for code in ('busy','runtime_busy'):
            with self.subTest(code=code):
                clock[0]=0;spawns.clear()
                def refuse(self,timeout=1,code=code):raise OwnerError(code)
                with patch.object(Runtime,'probe_owner',refuse):
                    with self.assertRaises(OwnerError) as caught:
                        launch.ensure_service(self.store,self.root,spawn=spawn,clock=lambda:clock[0],sleep=sleep,readiness_timeout=.2)
                self.assertEqual(caught.exception.code,code);self.assertEqual(spawns,[])
        # Review r3: a busy owner followed by a probe that ran out of time must not spawn.
        clock[0]=0;spawns.clear()
        sequence=iter(['busy']+['owner_unavailable']*100)
        def mixed(self,timeout=1):raise OwnerError(next(sequence))
        with patch.object(Runtime,'probe_owner',mixed):
            with self.assertRaises(OwnerError) as caught:
                launch.ensure_service(self.store,self.root,spawn=spawn,clock=lambda:clock[0],sleep=sleep,readiness_timeout=.2)
        self.assertEqual(caught.exception.code,'busy');self.assertEqual(spawns,[])
        expected={'identity':{'fixture':'verified'},'origin':'http://127.0.0.1:1234/'}
        with patch.object(Runtime,'probe_owner',side_effect=[OwnerError('busy'),expected]):
            self.assertEqual(launch.ensure_service(self.store,self.root,spawn=lambda *a,**k:self.fail('busy cannot spawn')),expected)

    def test_transient_restart_nonce_mismatch_retries_without_spawn(self):
        expected={'identity':{'fixture':'verified'},'origin':'http://127.0.0.1:1234/'}
        with patch.object(Runtime,'probe_owner',side_effect=[OwnerError('owner_identity_mismatch'),expected]):
            result=launch.ensure_service(self.store,self.root,spawn=lambda *a,**k:self.fail('Mismatch cannot authorize spawn'))
        self.assertEqual(result,expected)

    def test_source_python_and_config_strict_before_spawn(self):
        for path in (True,'python3',str(self.store/'missing')):
            with self.assertRaises(IdeaError):launch.ensure_service(self.store,self.root,runtime_python=path)
        for config in ({'actor':'editable'},{'plan_validator_argv':'shell'},{'validator_timeout_seconds':True}):
            with self.assertRaises(IdeaError):launch.ensure_service(self.store,self.root,config)
        self.assertFalse(self.root.exists())

    def test_browser_selector_preflight_before_startup_or_receipt_allocation(self):
        from idea_native import OrcaBinding
        for mode,binding,code in [('automatic',None,'invalid_mode'),('orca',None,'origin_missing'),
                                  ('orca',OrcaBinding('short','terminal'),'origin_missing'),
                                  ('orca',OrcaBinding('11111111-1111-1111-1111-111111111111::worktree','bad\nterminal'),'origin_missing')]:
            with self.subTest(mode=mode,binding=binding):
                with self.assertRaises(launch.LaunchError) as caught:
                    launch.open_browser_session(self.store,self.root,mode=mode,orcabinding=binding,spawn=lambda *a,**k:self.fail('Bad selector must not spawn'))
                self.assertEqual(caught.exception.code,code)
        self.assertFalse(self.root.exists());self.assertFalse(self.store.exists())

    def test_server_live_capacity_and_membership_preflight_before_new_sid(self):
        runtime=Runtime(self.store,self.root);runtime.acquire_owner()
        owner=launch.OwnerService(runtime)
        try:
            payload=dict(mode='new',binding_id=None,selected_idea_id=None)
            owner.policy.limit=1
            owner._open(payload)
            paths=set((self.store/'session-recovery').glob('*.json'))
            with self.assertRaises(BridgeError) as caught:owner._open(payload)
            self.assertEqual(caught.exception.code,'session_capacity_exhausted')
            self.assertEqual(set((self.store/'session-recovery').glob('*.json')),paths)
            owner.members.clear()
            with self.assertRaises(BridgeError) as caught:owner._open(payload)
            self.assertEqual(caught.exception.code,'binding_membership_conflict')
            self.assertEqual(set((self.store/'session-recovery').glob('*.json')),paths)
        finally:owner.shutdown()

    def test_supervisor_idle_and_active_publication_drains_before_unlock(self):
        clock=[0.0];runtime=Runtime(self.store,self.root,clock=lambda:clock[0]);runtime.acquire_owner()
        entered=threading.Event();release=threading.Event()
        def resolver(workspace):entered.set();release.wait(3);return workspace
        owner=launch.OwnerService(runtime,workspace_resolver=resolver);owner.start()
        # Private trusted caller; same serialization/mutator path as wire control.
        opened=owner._open(dict(mode='new',binding_id=None,selected_idea_id=None))
        opened['origin']=owner.server.origin+'/'
        cookie,csrf=self.pair(opened)
        payload=dict(request_id='drained-capture',raw_text='Admitted fixture',workspace=dict(name='Fixture',path=str(self.workspace),confirmed=True))
        result=[]
        request=threading.Thread(target=lambda:result.append(self.request(opened,'capture',payload,cookie=cookie,csrf=csrf)))
        request.start();self.assertTrue(entered.wait(2))
        clock[0]=901
        self.assertFalse(runtime.idle_due(),'Active admitted publication blocks idle shutdown')
        owner.request_stop()
        supervisor=threading.Thread(target=owner.supervise);supervisor.start()
        try:
            time.sleep(.05);self.assertTrue(supervisor.is_alive())
            contender=Runtime(self.store,self.root)
            with self.assertRaises(OwnerError) as caught:contender.acquire_owner()
            self.assertEqual(caught.exception.code,'runtime_busy')
        finally:release.set()
        request.join(3);supervisor.join(3)
        self.assertFalse(supervisor.is_alive());self.assertEqual(result[0][0],200)
        self.assertEqual(Store(self.store).request_result(opened['session_id'],'drained-capture')['idea_id'],result[0][1]['idea_id'])
        replacement=Runtime(self.store,self.root);replacement.acquire_owner();replacement.close()
        # A separate owner reaches actual supervisor idle-exit with injected time.
        idle=Runtime(self.store,self.root,clock=lambda:clock[0]);idle.acquire_owner()
        idleowner=launch.OwnerService(idle);idleowner.start();clock[0]+=901
        idlethread=threading.Thread(target=idleowner.supervise);idlethread.start();idlethread.join(3)
        self.assertFalse(idlethread.is_alive())


if __name__=='__main__':unittest.main()
