"""real subprocess native verbs against fixed loopback owner/agent."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

import test_agent_client as fixture

SCRIPTS=Path(__file__).resolve().parents[1]/'glitch-idea/scripts'


@unittest.skipUnless(os.name=='posix','Runtime native owner ACLs remain unqualified')
class AgentCliTests(unittest.TestCase):
    setUp=fixture.AgentClientTests.setUp
    stop=fixture.AgentClientTests.stop
    reply=fixture.AgentClientTests.reply

    def cli(self,verb,*extra,ok=True,script=None):
        argv=[sys.executable,str(script or SCRIPTS/'idea.py'),'--store',str(self.owner.store_path),
              verb,'--runtime-root',str(self.owner.runtime_root),'--session',self.sid,*map(str,extra)]
        self.assertFalse(any(self.token in value for value in argv),'credential never enters argv')
        result=subprocess.run(argv,capture_output=True,timeout=8)
        self.assertEqual(result.stderr,b'','structured stdout only')
        self.assertNotIn(self.token.encode(),result.stdout)
        body=json.loads(result.stdout)
        self.assertEqual(result.returncode,0 if ok else 1,body)
        self.assertEqual(body['ok'],ok)
        return body

    def payload(self,value=None):
        path=Path(self.temp.name)/'native response.json'
        path.write_text(json.dumps(self.reply() if value is None else value))
        return path

    def test_actual_events_response_close_and_private_generation_pin(self):
        events=self.cli('events','--after',0,'--timeout',0,'--generation',self.generation)
        self.assertEqual(events['events'][0]['request_id'],self.correlation['request_id'])
        response=self.cli('respond','--request','request-1','--payload',self.payload(),
                          '--generation',self.generation)
        self.assertEqual(response['status'],'completed')
        self.assertEqual(self.cli('session-close')['agent_status'],'disconnected')
        self.assertEqual([c[1] for c in self.calls if c[1].startswith('/agent/')],
                         ['/agent/v1/events','/agent/v1/respond','/agent/v1/session-close'])
        self.assertFalse(any('binding-open' in c[1] for c in self.calls))

    def test_invalid_wait_and_mismatched_request_file_never_contacts_owner(self):
        for extra in (('--timeout','26'),('--timeout','nan'),('--after','-1')):
            self.assertEqual(self.cli('events',*extra,ok=False)['error']['code'],'invalid_agent_input')
        error=self.cli('respond','--request','wrong-request','--payload',self.payload(),ok=False)
        self.assertEqual(error['error']['code'],'invalid_agent_input')
        self.assertEqual(self.calls,[])

    def test_strict_bounded_native_payload_and_redacted_path_errors(self):
        path=self.payload()
        for raw in (b'{"request_id":"request-1","request_id":"duplicate"}',b'{"bad":NaN}',
                    b'{"bad":"'+b'x'*(1024*1024)+b'"}'):
            path.write_bytes(raw)
            self.cli('respond','--request','request-1','--payload',path,ok=False)
        missing=Path(self.temp.name)/'private-path-must-not-print'
        # Input paths never go to HTTP or diagnostics. No credential in argv.
        result=subprocess.run([sys.executable,str(SCRIPTS/'idea.py'),'respond','--session',self.sid,
                               '--request','request-1','--payload',str(missing)],capture_output=True,timeout=8)
        self.assertEqual(result.stderr,b'');self.assertNotIn(b'private-path-must-not-print',result.stdout)
        self.assertEqual(json.loads(result.stdout)['error']['code'],'missing_artifact')
        self.assertEqual(self.calls,[])

    def test_usage_errors_redact_untrusted_argv(self):
        result=subprocess.run([sys.executable,str(SCRIPTS/'idea.py'),'events','--session',self.sid,
                               '--timeout','private-argument'],capture_output=True,timeout=8)
        self.assertEqual(result.returncode,1);self.assertEqual(result.stderr,b'')
        self.assertEqual(json.loads(result.stdout),dict(ok=False,error=dict(code='usage',message='usage')))
        self.assertNotIn(b'private-argument',result.stdout);self.assertEqual(self.calls,[])

    def test_response_uncertainty_stays_structured_and_single_attempt(self):
        self.mode='uncertain'
        result=self.cli('respond','--request','request-1','--payload',self.payload(),ok=False)
        self.assertEqual(result,dict(ok=False,error=dict(code='agent_request_failed',message='agent_request_failed'),
                                     write_state='committed_uncertain'))
        self.assertEqual(sum(c[1]=='/agent/v1/respond' for c in self.calls),1)

    def test_absent_session_and_generation_mismatch_never_open_or_resume(self):
        self.sid='session_'+'f'*32
        self.assertEqual(self.cli('events','--timeout',0,ok=False)['error']['code'],'binding_not_found')
        self.assertEqual(self.calls,[])
        self.sid=self.record['receipt_session_id']
        self.assertEqual(self.cli('events','--timeout',0,'--generation','agent_'+'f'*32,
                                 ok=False)['error']['code'],'owner_identity_mismatch')
        self.assertFalse(any('binding-open' in c[1] for c in self.calls))

    def test_installed_config_keeps_explicit_runtime_python_gate(self):
        package=Path(self.temp.name)/'installed/glitch-idea'
        shutil.copytree(SCRIPTS.parent,package,ignore=shutil.ignore_patterns('__pycache__'))
        (package/'.glitch-idea-install.json').write_text('{}')
        config=dict(store_path=str(self.owner.store_path),runtime_root=str(self.owner.runtime_root),
                    plan_validator_argv=None,validator_timeout_seconds=30)
        (package/'config.json').write_text(json.dumps(config))
        script=package/'scripts/idea.py'
        self.assertEqual(self.cli('events','--timeout',0,script=script,ok=False)['error']['code'],
                         'runtime_python_required')
        self.assertEqual(self.calls,[])
        config['runtime_python']=sys.executable;(package/'config.json').write_text(json.dumps(config))
        self.assertEqual(self.cli('events','--timeout',0,script=script)['agent_status'],'connected')


if __name__=='__main__':unittest.main()
