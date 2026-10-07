"""actual CLI/application interoperation and preserved CLI contracts."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS=Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0,str(SCRIPTS))
import idea
import idea_service as application
from idea_domain import encoded, IdeaError
from idea_store import Store
from idea_service import Service,TrustedContext,TrustedStepHandler
from test_workflow import fields,complete
from test_handoff_service import accepted_managed, VALIDATOR

class CliServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name);self.root=self.directory/'ideas'
        self.script=SCRIPTS/'idea.py';self.workspace=self.directory/'workspace';self.workspace.mkdir()
        self.store=Store(self.root);self.sid=self.store.create_session()
        self.context=TrustedContext('bound-browser',self.sid)
        self.browser=Service(self.store,{},self.context,handlers={step:TrustedStepHandler(lambda *args:None) for step in ('method','discovery','exploration')})
        self.sequence=0

    def file(self,data,suffix='.json'):
        self.sequence+=1;path=self.directory/(str(self.sequence)+suffix)
        path.write_bytes(encoded(data) if isinstance(data,dict) else data.encode())
        return path

    def cli(self,*args,ok=True):
        result=subprocess.run([sys.executable,str(self.script),'--store',str(self.root),*map(str,args)],capture_output=True,timeout=8)
        body=json.loads(result.stdout)
        self.assertEqual(result.returncode,0 if ok else 1,body);self.assertEqual(body['ok'],ok)
        return body

    def browser_capture(self):
        return self.browser.capture(dict(request_id='browser-capture',raw_text='  Café 💡\r\n\n',workspace=dict(name='Actual workspace',path=str(self.workspace),confirmed=True)))

    def accept(self,step,answer,key):
        state=self.browser.state()
        return self.browser.accept(dict(request_id=key,idea_id=state['idea_id'],expected_revision=state['revision'],expected_draft_version=state['draft_version'],step=step,fields=answer,proposal_id=None,expected_backlog_revision=None))

    def exploration(self):return dict(fields()['exploration'],outcome='Bounded fixture')

    def assessment(self):return dict(method='wsjf',version='1',inputs=dict(value=8,time_criticality=4,enablement=2,effort=2),basis='Fixture',assumptions=[],confidence='low',provenance='Fixture discussion')

    def test_cli_delegates_to_single_service_dispatch_without_reverse_import(self):
        self.assertIs(idea.run,application.run_legacy)
        source=(SCRIPTS/'idea.py').read_text()
        self.assertNotIn("if command=='capture'",source)
        self.assertNotIn('from idea import',(SCRIPTS/'idea_service.py').read_text())

    def test_browser_cli_rate_then_browser_accept_preserves_origin_history_receipts(self):
        captured=self.browser_capture();key=captured['idea_id']
        self.accept('priorities',dict(urgency=7,importance=8),'browser-priorities')
        before=self.cli('show',key)['idea'];history=(self.root/'history'/key/'r2.md').read_bytes()
        result=self.cli('rate',key,'--expected-revision',2,'--urgency',9,'--importance',8,'--actor','cli-operator')['idea']
        self.assertEqual(result['revision'],3);self.assertEqual(result['origin'],before['origin'])
        self.assertEqual(result['workflow']['steps']['priorities']['acceptance'],before['workflow']['steps']['priorities']['acceptance'])
        self.assertEqual(result['workflow']['drafts']['priorities'],dict(urgency=9,importance=8))
        self.assertEqual((self.root/'history'/key/'r2.md').read_bytes(),history)
        self.assertEqual(self.browser.state()['steps']['priorities']['status'],'unsaved')
        accepted=self.accept('priorities',dict(urgency=9,importance=8),'accept-cli-inputs')
        self.assertEqual(accepted['revision'],4);self.assertEqual(self.browser.state()['steps']['priorities']['status'],'saved')
        self.assertEqual(self.cli('show',key)['idea']['ratings']['actor'],'bound-browser')

    def test_cli_exploration_review_drafts_do_not_fabricate_method_or_discovery_acceptance(self):
        # J2a-owned: the CLI verb `exploration` replaces `shape` in CP2.
        captured=self.browser_capture();key=captured['idea_id']
        for step in ('priorities','method','discovery'):self.accept(step,fields()[step],step)
        self.accept('exploration',fields()['exploration'],'exploration')
        revised=self.exploration();revised['outcome']='New CLI outcome'
        result=self.cli('exploration',key,'--expected-revision',5,'--file',self.file(revised),'--actor','cli-operator')['idea']
        self.assertEqual(result['revision'],6)
        self.assertEqual(result['workflow']['drafts']['exploration']['outcome'],'New CLI outcome')
        self.assertNotIn('shape',result['workflow']['steps'])
        # The CLI drafts only Exploration: the accepted Method and Discovery receipts stay as they were.
        for step in ('method','discovery'):
            self.assertIsNotNone(result['workflow']['steps'][step]['acceptance'])
            self.assertNotIn(step,result['workflow']['drafts'])
        self.assertIn('exploration',result['workflow']['steps']['exploration']['invalidated_by'])
        self.assertEqual(self.browser.state()['accepted']['exploration']['outcome'],fields()['exploration']['outcome'])

    def test_conflicting_browser_draft_refuses_cli_without_file_changes(self):
        key=self.browser_capture()['idea_id'];self.accept('priorities',dict(urgency=7,importance=8),'priorities')
        state=self.browser.state()
        self.browser.draft(dict(request_id='partial',idea_id=key,expected_revision=2,expected_draft_version=state['draft_version'],step='priorities',fields=dict(urgency=6,importance=None)))
        detail=(self.root/(key+'.md')).read_bytes();index=(self.root/'IDEAS.md').read_bytes()
        result=self.cli('rate',key,'--expected-revision',2,'--urgency',9,'--importance',8,'--actor','cli-operator',ok=False)
        self.assertEqual(result['error']['code'],'draft_conflict')
        self.assertEqual((self.root/(key+'.md')).read_bytes(),detail);self.assertEqual((self.root/'IDEAS.md').read_bytes(),index)

    def test_cli_assessment_preserves_old_browser_receipt_and_exposes_new_score_inputs(self):
        value=complete();key=value['idea_id']
        with self.store.transaction(write=True) as state:
            state['ideas'][key]=value;state['order']=[key];state['backlog_revision']=1;self.store.commit(state)
        old=copy.deepcopy(value['workflow']['steps']['assess']['acceptance'])
        result=self.cli('assess',key,'--expected-revision',value['revision'],'--file',self.file(self.assessment()),'--actor','cli-assessor')['idea']
        self.assertEqual(result['revision'],value['revision']+1)
        self.assertEqual(result['assessments'][-1]['score'],7)
        self.assertEqual(result['assessments'][-1]['actor'],'cli-assessor')
        self.assertEqual(result['workflow']['drafts']['assess']['assessment'],self.assessment())
        self.assertEqual(result['workflow']['steps']['assess']['acceptance'],old)
        self.assertIn('assess',result['workflow']['steps']['assess']['invalidated_by'])

    def test_legacy_complete_cycle_preserves_flags_results_plan_and_execution(self):
        captured=self.cli('capture','--text-file',self.file('Exact words\n','.txt'),'--actor','legacy')['idea'];key=captured['idea_id']
        self.assertNotIn('workflow',captured)
        current=self.cli('exploration',key,'--expected-revision',1,'--file',self.file(self.exploration()),'--actor','legacy')['idea']
        current=self.cli('rate',key,'--expected-revision',2,'--urgency',7,'--importance',9,'--actor','legacy')['idea']
        current=self.cli('assess',key,'--expected-revision',3,'--file',self.file(self.assessment()),'--actor','legacy')['idea']
        self.assertEqual(current['revision'],4);self.assertNotIn('workflow',current)
        proposal=self.cli('propose',key,'--position',1,'--reason','Actual reason','--expected-backlog-revision',1,'--actor','legacy')
        self.assertEqual(set(proposal),{'ok','proposal','backlog_revision'})
        placement=self.cli('place',key,'--position',1,'--reason','Accepted order','--expected-backlog-revision',1,'--actor','legacy')
        self.assertEqual(set(placement),{'ok','placement','backlog_revision','order'})
        handoff=self.cli('handoff',key);self.assertEqual(handoff['idea_revision'],4)
        plan=self.file('# Plan\n\n## Goal\nReal goal.\n\n## Tasks\nBounded trial.\n\n## Validation\nObserve outcome.\n\n## Idea trace\nidea_id: '+key+'\nidea_revision: 4\n','.md')
        registered=self.cli('register-plan',key,'--path',plan,'--expected-revision',4,'--actor','legacy')
        self.assertEqual(set(registered),{'ok','plan','idea'});self.assertEqual(registered['idea']['status'],'archived')
        repeated=self.cli('register-plan',key,'--path',plan,'--expected-revision',4,'--actor','legacy');self.assertTrue(repeated['repeated'])
        execution=self.file(dict(idea_id=key,plan_id=registered['plan']['plan_id'],attempt_id='attempt-1',status='succeeded',evidence=['Observed trial']))
        result=self.cli('record-execution',key,'--path',execution,'--plan-id',registered['plan']['plan_id'],'--actor','legacy')
        self.assertEqual(set(result),{'ok','execution','idea'})
        self.assertTrue(self.cli('record-execution',key,'--path',execution,'--plan-id',registered['plan']['plan_id'],'--actor','legacy')['repeated'])
        self.assertTrue(self.cli('doctor')['healthy']);self.assertTrue(self.cli('repair-views')['healthy'])
        self.assertEqual(self.cli('list')['order'],[key])
        error=self.cli('rate',key,'--expected-revision',3,'--urgency',7,'--importance',9,'--actor','legacy',ok=False)
        self.assertEqual(set(error),{'ok','error'});self.assertEqual(error['error']['code'],'stale_revision')

    def test_placement_only_invalidates_managed_ideas_with_changed_actual_neighbors(self):
        managed=complete();key=managed['idea_id']
        # Actual neighbors match accepted fixture: this managed idea is first.
        managed['workflow']['steps']['assess']['fields']['position']['neighbors']['after']='idea_'+'2'*32
        managed['revisions'][-1]['workflow']=copy.deepcopy(managed['workflow'])
        other=complete();other['idea_id']='idea_'+'2'*32
        unaffected=complete();unaffected['idea_id']='idea_'+'3'*32
        tail=complete();tail['idea_id']='idea_'+'4'*32
        with self.store.transaction(write=True) as state:
            state['ideas']={v['idea_id']:v for v in (managed,other,unaffected,tail)};state['order']=list(state['ideas']);state['backlog_revision']=4;self.store.commit(state)
        self.cli('place',other['idea_id'],'--position',1,'--reason','New order','--expected-backlog-revision',4,'--actor','cli-operator')
        shown=self.cli('list');values={v['idea_id']:v for v in shown['ideas']}
        self.assertEqual(values[key]['revision'],managed['revision']+1)
        # Third idea's predecessor changed; fourth's position+neighbors did not.
        self.assertEqual(values[unaffected['idea_id']]['revision'],unaffected['revision']+1)
        self.assertEqual(values[tail['idea_id']]['revision'],tail['revision'])
        self.assertEqual(len(self.store_request_placements()),1)

    def store_request_placements(self):
        with self.store.transaction() as state:return copy.deepcopy(state['placements'])

    def test_archived_managed_reorder_keeps_frozen_revision_status_and_plan(self):
        _,key=accepted_managed(self.store,self.workspace,session_id=self.sid)
        with self.store.transaction() as state:
            value=copy.deepcopy(state['ideas'][key])
            self.store.handoff_observations(state,key)  # Genuine eligibility before archive.
        plan=self.file('# Plan\n\n## Goal\nGoal.\n\n## Tasks\nTask.\n\n## Validation\nTest.\n\n## Idea trace\nidea_id: '+key+'\nidea_revision: '+str(value['revision'])+'\n','.md')
        registered=self.cli('register-plan',key,'--expected-revision',value['revision'],'--path',plan,'--actor','operator')['idea']
        history=(self.root/'history'/key/('r'+str(value['revision'])+'.md')).read_bytes()
        other=self.cli('capture','--text-file',self.file('Other','.txt'),'--actor','operator')['idea']['idea_id']
        self.cli('place',other,'--position',1,'--reason','Reorder history','--expected-backlog-revision',3,'--actor','operator')
        shown=self.cli('show',key)['idea']
        self.assertEqual(shown,registered)
        self.assertEqual((self.root/'history'/key/('r'+str(value['revision'])+'.md')).read_bytes(),history)
        self.assertEqual(len(shown['plans']),1);self.assertEqual(shown['status'],'archived')
        self.assertFalse((self.root/'history'/key/('r'+str(value['revision']+1)+'.md')).exists())
        # J2a-owned: an archived idea reopens on Exploration through the CLI `exploration` verb.
        next_slice=self.cli('exploration',key,'--expected-revision',value['revision'],'--file',self.file(shown['workflow']['steps']['exploration']['fields']),'--actor','operator')['idea']
        self.assertEqual(next_slice['status'],'active')
        self.assertEqual(next_slice['workflow']['current_step'],'exploration')
        self.assertIn('exploration',next_slice['workflow']['steps']['exploration']['invalidated_by'])
        self.assertEqual(next_slice['plans'],shown['plans'])

    def test_installed_config_relative_store_and_fixed_plan_validator_are_preserved(self):
        package=self.directory/'installed/glitch-idea';shutil.copytree(SCRIPTS.parent,package)
        validator=self.file('import sys\nprint("actual validator receipt")\nsys.exit(0)\n','.py')
        (package/'.glitch-idea-install.json').write_text('{}')
        (package/'config.json').write_bytes(encoded(dict(store_path='../durable-ideas',plan_validator_argv=[sys.executable,str(validator)],validator_timeout_seconds=2)))
        self.script=package/'scripts/idea.py'
        result=subprocess.run([sys.executable,str(self.script),'capture','--text-file',str(self.file('configured','.txt')),'--actor','operator'],capture_output=True,timeout=8)
        self.assertEqual(result.returncode,0,result.stdout)
        configured=json.loads(result.stdout)['idea']['idea_id'];self.assertTrue((package.parent/'durable-ideas'/(configured+'.md')).exists())
        key=self.cli('capture','--text-file',self.file('plan words','.txt'),'--actor','operator')['idea']['idea_id']
        self.cli('exploration',key,'--expected-revision',1,'--file',self.file(self.exploration()),'--actor','operator')
        self.cli('rate',key,'--expected-revision',2,'--urgency',7,'--importance',8,'--actor','operator')
        self.cli('assess',key,'--expected-revision',3,'--file',self.file(self.assessment()),'--actor','operator')
        plan=self.file('# Plan\n\n## Goal\nGoal.\n\n## Tasks\nTask.\n\n## Validation\nTest.\n\n## Idea trace\nidea_id: '+key+'\nidea_revision: 4\n','.md')
        receipt=self.cli('register-plan',key,'--expected-revision',4,'--path',plan,'--actor','operator')['plan']['validation']['external']
        self.assertEqual(receipt['argv'],[sys.executable,str(validator),str(plan)]);self.assertIn('actual validator receipt',receipt['output'])
        (package/'config.json').unlink()
        self.assertEqual(self.cli('list',ok=False)['error']['code'],'invalid_config')

    def configured_managed_cli(self):
        package=self.directory/'configured/glitch-idea';shutil.copytree(SCRIPTS.parent,package)
        (package/'.glitch-idea-install.json').write_text('{}')
        (package/'config.json').write_bytes(encoded(dict(store_path=str(self.root),
            plan_validator_argv=[sys.executable,str(VALIDATOR)],validator_timeout_seconds=2)))
        self.script=package/'scripts/idea.py'
        _,key=accepted_managed(self.store,self.workspace,session_id=self.sid)
        revision=self.cli('show',key)['idea']['revision']
        return key,revision

    def test_managed_cli_real_configured_validator_reject_then_success_and_archived_replay(self):
        key,revision=self.configured_managed_cli()
        self.assertEqual(self.cli('handoff',key)['idea_revision'],revision)
        text='# Plan\n\n## Goal\nGoal.\n\n## Tasks\nTask.\n\n## Validation\nTest.\n\n## Idea trace\nidea_id: '+key+'\nidea_revision: '+str(revision)+'\n'
        plan=self.file(text.replace('Goal.\n','Goal.\nREJECT_CP4\n'),'.md')
        before={p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        refused=self.cli('register-plan',key,'--expected-revision',revision,'--path',plan,'--actor','operator',ok=False)
        self.assertEqual(refused['error']['code'],'validator_failed')
        self.assertEqual({p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()},before)
        self.assertEqual(self.cli('show',key)['idea']['status'],'active')
        plan.write_text(text)
        registered=self.cli('register-plan',key,'--expected-revision',revision,'--path',plan,'--actor','operator')
        external=registered['plan']['validation']['external']
        self.assertEqual(external['argv'],[sys.executable,str(VALIDATOR),str(plan)])
        self.assertEqual(external['returncode'],0);self.assertEqual(external['output'],'CP4 validator accepted\n')
        self.assertEqual(registered['idea']['status'],'archived');self.assertEqual(registered['idea']['revision'],revision)
        self.workspace.rmdir()
        repeated=self.cli('register-plan',key,'--expected-revision',revision,'--path',plan,'--actor','operator')
        self.assertTrue(repeated['repeated']);self.assertEqual(repeated['plan'],registered['plan'])

    def test_managed_cli_dirty_workflow_refuses_handoff_and_registration(self):
        key,revision=self.configured_managed_cli()
        self.cli('rate',key,'--expected-revision',revision,'--urgency',9,'--importance',8,'--actor','operator')
        current=self.cli('show',key)['idea']['revision']
        handoff=self.cli('handoff',key,ok=False);self.assertEqual(handoff['error']['code'],'not_ready')
        plan=self.file('# Plan\n\n## Goal\nGoal.\n\n## Tasks\nTask.\n\n## Validation\nTest.\n\n## Idea trace\nidea_id: '+key+'\nidea_revision: '+str(current)+'\n','.md')
        before={p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        refused=self.cli('register-plan',key,'--expected-revision',current,'--path',plan,'--actor','operator',ok=False)
        self.assertEqual(refused['error']['code'],'not_ready')
        self.assertEqual({p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()},before)

if __name__=='__main__':unittest.main()
