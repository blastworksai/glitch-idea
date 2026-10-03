"""thin parser/config seam and actual isolated source CLI sessions."""
import contextlib
import copy
import http.client
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0,str(SCRIPTS))
import idea
import idea_launch as launch
from idea_domain import IdeaError
from idea_native import OrcaBinding
from idea_runtime import Runtime,RuntimeError as OwnerError


class Output:
    def __init__(self): self.buffer=io.BytesIO()


class CliSeamTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        self.package=self.directory/'package/glitch-idea';self.package.mkdir(parents=True)
        self.fake_file=self.package/'scripts/idea.py';self.fake_file.parent.mkdir();self.fake_file.write_text('fixture')
        self.store=self.directory/'ideas';self.runtime=self.directory/'runtime'
        self.source_patch=patch.object(idea,'__file__',str(self.fake_file));self.source_patch.start();self.addCleanup(self.source_patch.stop)

    def config(self,**extra):
        record=dict(store_path=str(self.store),plan_validator_argv=None,validator_timeout_seconds=30,**extra)
        (self.package/'config.json').write_text(json.dumps(record));return record

    def invoke(self,*args):
        stream=Output()
        with patch.object(idea.sys,'stdout',stream): status=idea.main(list(args))
        return status,json.loads(stream.buffer.getvalue())

    def options(self,verb='session-open',*extra):
        return [verb,'--runtime-root',str(self.runtime),*extra]

    def test_legacy_configuration_and_parser_preserved(self):
        self.config(runtime_root='invalid relative',runtime_python=False)
        self.assertEqual(idea.configuration(),dict(store_path=str(self.store),plan_validator_argv=None,validator_timeout_seconds=30))
        args=idea.parser().parse_args(['--store',str(self.store),'capture','--text-file','raw.txt','--actor','operator'])
        self.assertEqual((args.command,args.actor,args.text_file),('capture','operator','raw.txt'))
        with self.assertRaises(IdeaError) as caught: idea.parser().parse_args(['rate','idea_id'])
        self.assertEqual(caught.exception.code,'usage')

    def test_forward_only_legacy_config_and_separate_launcher_options(self):
        self.config(runtime_root=str(self.runtime),runtime_python=sys.executable)
        result=dict(identity={},origin='http://127.0.0.1:1234/',binding_id='binding_'+'a'*32,
                    session_id='session_'+'b'*32,selected_idea_id=None,pairing_code='ONE-TIME-FIXTURE')
        with patch.object(launch,'open_session',return_value=result) as opened:
            status,body=self.invoke('session-open','--readiness-timeout','2')
        self.assertEqual(status,0);self.assertEqual(body,dict(ok=True,**result))
        args,kwargs=opened.call_args
        self.assertEqual(args,(self.store,self.runtime,idea.configuration()))
        self.assertEqual(kwargs,dict(binding_id=None,selected_idea_id=None,runtime_python=sys.executable,readiness_timeout=2))
        self.assertNotIn('timeout',kwargs,'keep released five-second mutation default')
        binding='binding_'+'c'*32;selected='idea_'+'d'*32
        with patch.object(launch,'open_session',return_value=result) as opened:
            self.invoke(*self.options('session-open','--resume',binding,'--idea-id',selected))
        self.assertEqual(opened.call_args.kwargs['binding_id'],binding);self.assertEqual(opened.call_args.kwargs['selected_idea_id'],selected)

    def test_relative_configured_store_and_store_override_use_correct_base(self):
        record=self.config();record['store_path']='../durable';(self.package/'config.json').write_text(json.dumps(record))
        args=idea.parser().parse_args(self.options())
        store,root,config,_=idea.launcher_configuration(args)
        self.assertEqual(store,self.package.parent/'durable');self.assertEqual(config['store_path'],str(store))
        with patch.object(idea.Path,'cwd',return_value=self.directory):
            args=idea.parser().parse_args(['--store','override',*self.options()])
            self.assertEqual(idea.launcher_configuration(args)[0],self.directory/'override')

    def test_symlink_evidence_survives_dotdot_and_configuration_resolution(self):
        actual=self.directory/'actual';actual.mkdir();alias=self.directory/'alias';alias.symlink_to(actual,target_is_directory=True)
        record=self.config();record['store_path']=str(alias/'..'/'store');(self.package/'config.json').write_text(json.dumps(record))
        with patch.object(launch,'open_session') as opened:
            status,error=self.invoke(*self.options())
        self.assertEqual((status,error['error']['code']),(1,'runtime_not_private'));opened.assert_not_called()
        self.config()
        with patch.object(launch,'open_session') as opened:
            status,error=self.invoke('--store',str(alias/'store'),*self.options())
        self.assertEqual(error['error']['code'],'runtime_not_private');opened.assert_not_called()

    def test_runtime_default_override_absolute_and_outside_package(self):
        self.config()
        home=self.directory/'home'
        with patch.object(idea,'_private_home',return_value=home):
            root=idea.launcher_configuration(idea.parser().parse_args(['session-open']))[1]
        self.assertEqual(root,home/'.local/state/glitch-idea')
        for root in ('relative',str(self.package/'runtime'),str(self.package.parent)):
            with patch.object(launch,'open_session') as opened:
                status,error=self.invoke('session-open','--runtime-root',root)
            self.assertEqual((status,error['error']['code']),(1,'invalid_config'));opened.assert_not_called()

    def test_installed_gate_only_launch_and_foreground_actual_interpreter_match(self):
        self.config();(self.package/'.glitch-idea-install.json').write_text('{}')
        with patch.object(idea,'run',return_value={'order':[]}) as legacy:
            self.assertEqual(self.invoke('list'),(0,{'ok':True,'order':[]}))
        legacy.assert_called_once()
        with patch.object(launch,'open_session') as opened:
            status,error=self.invoke(*self.options())
        self.assertEqual(error['error']['code'],'runtime_python_required');opened.assert_not_called()
        with patch.object(launch,'open_session',return_value={'origin':'http://127.0.0.1:1234/'}) as opened:
            self.assertEqual(self.invoke(*self.options('session-open','--runtime-python',sys.executable))[0],0)
        self.config(runtime_python=sys.executable)
        with patch.object(launch,'serve',return_value=None) as serve:
            self.assertEqual(self.invoke(*self.options('serve')),(0,{'ok':True,'code':'stopped'}))
        serve.assert_called_once()
        alias=self.directory/'python-alias';alias.symlink_to(sys.executable)
        self.config(runtime_python=str(alias))
        with patch.object(launch,'serve') as serve:
            status,error=self.invoke(*self.options('serve'))
        self.assertEqual(error['error']['code'],'runtime_interpreter_mismatch');serve.assert_not_called()
        (self.package/'config.json').unlink()
        self.assertEqual(self.invoke('--store',str(self.store),*self.options())[1]['error']['code'],'invalid_config')

    def test_executable_validation_and_redacted_new_errors(self):
        self.config()
        for executable in ('python3',str(self.directory/'missing'),False):
            if executable is False:
                self.config(runtime_python=False);arguments=self.options()
            else: arguments=self.options('session-open','--runtime-python',executable)
            with patch.object(launch,'open_session') as opened:
                status,error=self.invoke(*arguments)
            self.assertEqual(error['error']['code'],'runtime_python_unavailable');opened.assert_not_called()
        self.config()
        with patch.object(launch,'open_session',side_effect=RuntimeError('private path credential')) as opened:
            status,error=self.invoke(*self.options())
        self.assertEqual(error,{'ok':False,'error':{'code':'launcher_failed','message':'launcher_failed'}});opened.assert_called_once()
        with patch.object(launch,'open_session',side_effect=OwnerError('privacy_unqualified')):
            status,error=self.invoke(*self.options())
        self.assertEqual(error['error'],{'code':'privacy_unqualified','message':'privacy_unqualified'})

    def test_invalid_selectors_envelopes_and_no_new_session_close(self):
        self.config()
        cases=(['browser-open'],self.options('session-open','--resume','bad'),self.options('session-open','--idea-id','bad'),
               self.options('session-open','--readiness-timeout','nan'),self.options('session-open','--readiness-timeout','inf'),
               self.options('session-open','--readiness-timeout','6'),self.options('browser-open','--browser','orca'),
               self.options('browser-open','--browser','system','--orca-terminal','fixture'),['session-close'],
               self.options('session-open','--actor','forged'))
        for values in cases:
            with self.subTest(values=values),patch.object(launch,'open_session') as opened,patch.object(launch,'open_browser_session') as browser:
                status,error=self.invoke(*values)
                self.assertEqual(status,1);opened.assert_not_called();browser.assert_not_called()
                self.assertNotIn('forged',json.dumps(error))

    def test_explicit_browser_selector_forwarding_and_native_failure_resume_details(self):
        self.config()
        worktree='12345678-1234-1234-1234-123456789012::fixture'
        with patch.object(launch,'open_browser_session',return_value={'browser':{'mode':'orca'}}) as browser:
            status,_=self.invoke(*self.options('browser-open','--browser','orca','--orca-worktree',worktree,
                                             '--orca-terminal','terminal-fixture','--orca-host','host-fixture'))
        self.assertEqual(status,0)
        self.assertEqual(browser.call_args.kwargs['orcabinding'],OrcaBinding(worktree,'terminal-fixture','host-fixture'))
        self.assertEqual(browser.call_args.kwargs['mode'],'orca')
        binding='binding_'+'a'*32;sid='session_'+'b'*32
        failure=launch.LaunchError('browser_refused',binding_id=binding,session_id=sid,selected_idea_id=None,
                                  resume_required=True,owner_token='SECRET',diagnostic='private child output')
        with patch.object(launch,'open_browser_session',side_effect=failure) as browser:
            status,error=self.invoke(*self.options('browser-open','--browser','system'))
        self.assertEqual(status,1);browser.assert_called_once()
        self.assertEqual(error,dict(ok=False,error={'code':'browser_refused','message':'browser_refused'},
                                   binding_id=binding,session_id=sid,selected_idea_id=None,resume_required=True))

    def test_foreground_interrupt_uses_existing_drain_finally(self):
        self.config()
        with patch.object(launch,'serve',side_effect=KeyboardInterrupt) as serve:
            self.assertEqual(self.invoke(*self.options('serve')),(0,{'ok':True,'code':'stopped'}))
        serve.assert_called_once()


@unittest.skipUnless(os.name=='posix','Native Windows privacy is not qualified')
class ActualCliTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        self.package=self.directory/'source/glitch-idea';shutil.copytree(SCRIPTS.parent,self.package,ignore=shutil.ignore_patterns('__pycache__'))
        self.script=self.package/'scripts/idea.py'
        self.store=self.directory/'ideas Café';self.root=self.directory/'private runtime'
        self.workspace=self.directory/'workspace';self.workspace.mkdir()
        self.client=Runtime(self.store,self.root)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        try:self.client.request_owned_stop(timeout=3)
        except OwnerError as exc:
            if exc.code!='owner_unavailable':raise
        deadline=time.monotonic()+4
        while (self.client.path/'discovery.json').exists() and time.monotonic()<deadline:time.sleep(.02)
        self.assertFalse((self.client.path/'discovery.json').exists(),'owned child cleanup completed')

    def cli(self,*args,ok=True):
        result=subprocess.run([sys.executable,str(self.script),'--store',str(self.store),*map(str,args)],
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
        self.assertEqual(result.returncode,0 if ok else 1,result.stdout)
        self.assertEqual(result.stderr,b'', 'only one JSON result; child diagnostics suppressed')
        body=json.loads(result.stdout);self.assertEqual(body['ok'],ok)
        return body

    def open(self,*extra,ok=True):return self.cli('session-open','--runtime-root',self.root,*extra,ok=ok)

    def request(self,opened,operation,payload=None,cookie=None,csrf=None):
        port=int(opened['origin'].split(':')[-1].rstrip('/'))
        headers={'X-Idea-Binding':opened['binding_id']}
        if cookie:headers['Cookie']=cookie
        if payload is not None:
            headers.update({'Content-Type':'application/json','Origin':opened['origin'].rstrip('/')})
            if csrf:headers['X-CSRF-Token']=csrf
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
        try:
            connection.request('POST' if payload is not None else 'GET','/api/v1/'+operation,
                               None if payload is None else json.dumps(payload),headers)
            response=connection.getresponse();body=json.loads(response.read())
            return response.status,body,response.getheader('Set-Cookie')
        finally:connection.close()

    def test_actual_cli_open_pair_capture_resume_reuse_and_restart_same_receipt(self):
        opened=self.open();self.assertIn('pairing_code',opened)
        status,paired,cookie=self.request(opened,'pair',{'code':opened['pairing_code']})
        self.assertEqual(status,200);cookie=cookie.split(';',1)[0];csrf=paired['csrf_token']
        payload=dict(request_id='capture-lost',raw_text='Exact words\r\n',workspace=dict(name='Explicit',path=str(self.workspace),confirmed=True))
        status,captured,_=self.request(opened,'capture',payload,cookie,csrf);self.assertEqual(status,200)
        resumed=self.open('--resume',opened['binding_id'])
        self.assertEqual(resumed['session_id'],opened['session_id']);self.assertEqual(resumed['selected_idea_id'],captured['idea_id'])
        self.assertEqual(resumed['origin'],opened['origin'])
        self.assertEqual(resumed['identity']['instance_nonce'],self.client.probe_owner()['identity']['instance_nonce'])
        self.client.request_owned_stop(timeout=3)
        deadline=time.monotonic()+3
        while (self.client.path/'discovery.json').exists() and time.monotonic()<deadline:time.sleep(.02)
        restarted=self.open('--resume',opened['binding_id'])
        self.assertEqual(restarted['binding_id'],opened['binding_id']);self.assertEqual(restarted['session_id'],opened['session_id'])
        self.assertNotEqual(restarted['identity']['instance_nonce'],opened['identity']['instance_nonce'])
        status,paired,cookie=self.request(restarted,'pair',{'code':restarted['pairing_code']});self.assertEqual(status,200)
        status,replayed,_=self.request(restarted,'capture',payload,cookie.split(';',1)[0],paired['csrf_token'])
        self.assertEqual(status,200);self.assertEqual(replayed,captured)
        self.assertEqual(self.cli('list')['order'],[captured['idea_id']])
        credentials=json.loads((self.client.path/'credentials.json').read_bytes())
        self.assertNotIn(credentials['owner_token'],json.dumps(restarted));self.assertNotIn('agent_token',json.dumps(restarted))

    def test_actual_installed_missing_interpreter_gate_preserves_legacy_cli(self):
        (self.package/'.glitch-idea-install.json').write_text('{}')
        config=dict(store_path=str(self.store),plan_validator_argv=None,validator_timeout_seconds=30)
        (self.package/'config.json').write_text(json.dumps(config))
        self.assertEqual(self.cli('list')['order'],[])
        error=self.open(ok=False);self.assertEqual(error['error']['code'],'runtime_python_required')
        self.assertFalse(self.root.exists())
        config['runtime_python']=sys.executable;(self.package/'config.json').write_text(json.dumps(config))
        opened=self.open();self.assertEqual(opened['identity']['service'],'glitch-idea')
        (self.package/'config.json').unlink()
        self.assertEqual(self.open(ok=False)['error']['code'],'invalid_config')


if __name__=='__main__':unittest.main()
