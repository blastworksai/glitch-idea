"""Behavior tests for GlitchC's local idea lifecycle; all stores are temporary."""
import concurrent.futures
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'glitch-idea/scripts/idea.py'


class IdeaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.root / 'ideas'
        copied = self.root/'helper'
        shutil.copytree(SCRIPT.parent,copied/'scripts')
        (copied/'config.json').write_text(json.dumps({'plan_validator_argv':None}))
        self.script = copied/'scripts/idea.py'
        self.counter = 0

    def file(self, value, suffix='.json'):
        self.counter += 1
        path = self.root / ('input' + str(self.counter) + suffix)
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8', newline='')
        return path

    def cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(self.script), '--store', str(self.store), *map(str, args)], capture_output=True, text=True)
        self.assertTrue(result.stdout.strip(), 'CLI did not return JSON: ' + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['ok'], ok, data)
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        return data

    def capture(self, text='A useful new idea'):
        return self.cli('capture', '--text-file', self.file(text, '.txt'), '--actor', 'operator')['idea']

    def shape_data(self, next_slice='Try one real user'):
        return dict(outcome='Reduce manual work', scope='capability', scope_reason='One new reusable ability', alternatives=[dict(route='Reuse', reason='Check existing route first')], method='experiment-led', method_reason='Demand is uncertain', assumptions=['A user has this need'], next_slice=next_slice, learning=[])

    def assessment(self, method='wsjf', **overrides):
        inputs = {'value': 8, 'time_criticality': 4, 'enablement': 2, 'effort': 2}
        if method == 'rice':
            inputs = {'reach': 100, 'impact': 2, 'confidence': .5, 'effort': 4}
        if method == 'kano':
            inputs = {'category': 'delighter', 'hypothesis': True}
        data = dict(method=method, version='1', inputs=inputs, basis={'unit':'points', 'cohort':'trial'}, assumptions=[], confidence='low', provenance='Operator discussion')
        data.update(overrides)
        return data

    def edit(self, command, idea, data):
        return self.cli(command, idea['idea_id'], '--file', self.file(data), '--expected-revision', idea['revision'], '--actor', 'assistant')['idea']

    def ready(self):
        idea = self.edit('shape', self.capture(), self.shape_data())
        idea = self.cli('rate', idea['idea_id'], '--urgency', 7, '--importance', 9, '--expected-revision', idea['revision'], '--actor', 'operator')['idea']
        return self.edit('assess', idea, self.assessment())

    def plan_file(self, idea):
        return self.file('# Trial plan\n\n## Goal\nReduce manual work.\n\n## Tasks\n- Run one trial.\nNo file changes.\n\n## Validation\nObserve actual result.\n\n## Idea trace\nidea_id: ' + idea['idea_id'] + '\nidea_revision: ' + str(idea['revision']) + '\n', '.md')

    def register(self, idea, path=None, ok=True):
        return self.cli('register-plan', idea['idea_id'], '--path', path or self.plan_file(idea), '--expected-revision', idea['revision'], '--actor', 'assistant', ok=ok)

    def test_capture_preserves_verbatim_unicode_across_restart(self):
        raw = '  Café 💡\r\n\n$(touch /never-execute)\t'
        idea = self.capture(raw)
        shown = self.cli('show', idea['idea_id'])['idea']
        self.assertEqual(shown['origin']['text'], raw)
        self.assertEqual(shown['origin']['actor'], 'operator')
        self.assertIsNone(shown['ratings'])
        self.assertEqual(self.cli('list')['order'], [idea['idea_id']])
        self.assertFalse(self.cli('handoff', idea['idea_id'], ok=False)['ok'])
        before = (self.store/'state.json').read_bytes()
        self.cli('capture', '--text-file', self.file('  \n'), '--actor', 'operator', ok=False)
        self.assertEqual((self.store/'state.json').read_bytes(), before)

    def test_stale_revisions_and_scores_preserve_history_without_reordering(self):
        idea = self.capture()
        current = self.edit('shape', idea, self.shape_data())
        before = (self.store/'state.json').read_bytes()
        self.cli('shape', idea['idea_id'], '--file', self.file(self.shape_data()), '--expected-revision', 1, '--actor', 'assistant', ok=False)
        self.assertEqual((self.store/'state.json').read_bytes(), before)
        for value in ('0','11','true','NaN','1.5'):
            self.cli('rate', idea['idea_id'], '--urgency', value, '--importance', 5, '--expected-revision', 2, '--actor', 'operator', ok=False)
        current = self.cli('rate', idea['idea_id'], '--urgency', 2, '--importance', 10, '--expected-revision', 2, '--actor', 'operator')['idea']
        self.assertEqual(current['ratings']['importance'], 10)
        self.assertEqual(current['revisions'][0]['shape'], None)
        self.assertEqual(len(current['revisions']), 3)
        self.assertEqual(self.cli('list')['backlog_revision'], 1)

    def test_formulas_unknowns_kano_and_invalid_numbers(self):
        idea = self.capture()
        for method, score in [('wsjf', 7), ('rice', 25), ('kano', None)]:
            idea = self.edit('assess', idea, self.assessment(method))
            self.assertEqual(idea['assessments'][-1]['score'], score)
        data = self.assessment()
        data['inputs']['effort'] = None
        idea = self.edit('assess', idea, data)
        self.assertIsNone(idea['assessments'][-1]['score'])
        before = (self.store/'state.json').read_bytes()
        for value in (0,-1,True,float('nan'),float('inf'),1e100,10**400):
            data = self.assessment()
            data['inputs']['effort'] = value
            self.cli('assess', idea['idea_id'], '--file', self.file(data), '--expected-revision', idea['revision'], '--actor', 'assistant', ok=False)
        self.assertEqual((self.store/'state.json').read_bytes(), before)

    def test_glitch_plan_headings_and_validator_timeout(self):
        idea = self.ready()
        path = self.plan_file(idea)
        path.write_text(path.read_text().replace('## Goal','## Feature Description').replace('## Tasks','## STEP-BY-STEP TASKS').replace('## Validation','## VALIDATION COMMANDS'))
        validator = self.file('import time\ntime.sleep(5)\n','.py')
        config = self.script.parent.parent/'config.json'
        config.write_text(json.dumps({'plan_validator_argv':[sys.executable,str(validator)],'validator_timeout_seconds':1}))
        result = self.register(idea,path,ok=False)
        self.assertEqual(result['error']['code'],'validator_timeout')
        config.write_text(json.dumps({'plan_validator_argv':None}))
        self.register(idea,path)

    def test_store_corruption_in_history_or_links_fails_closed(self):
        idea = self.ready()
        self.register(idea)
        state_path = self.store/'state.json'
        original = state_path.read_bytes()
        for mutate in (lambda s:s['ideas'][idea['idea_id']]['origin'].update(text='Forged origin'),lambda s:s['ideas'][idea['idea_id']]['plans'][0].update(sha256='not-a-hash'),lambda s:s['ideas'][idea['idea_id']]['proposals'].append({'broken':True})):
            state=json.loads(original)
            mutate(state)
            state_path.write_text(json.dumps(state))
            result=self.cli('doctor',ok=False)
            self.assertEqual(result['error']['code'],'corrupt_store')

    def test_unknown_assessment_remains_explicit_in_qualitative_proposal(self):
        idea=self.capture()
        idea=self.cli('rate',idea['idea_id'],'--urgency',1,'--importance',2,'--expected-revision',1,'--actor','operator')['idea']
        data=self.assessment()
        data['inputs']['value']=None
        idea=self.edit('assess',idea,data)
        proposed=self.cli('propose',idea['idea_id'],'--position',1,'--reason','Provisional; value still unknown','--expected-backlog-revision',1,'--actor','assistant')
        self.assertIsNone(proposed['proposal']['snapshot']['assessments'][0]['score'])

    def test_proposal_is_snapshot_and_only_place_changes_order(self):
        first = self.capture('first')
        second = self.ready()
        backlog = self.cli('list')
        result = self.cli('propose', second['idea_id'], '--position', 1, '--reason', 'Larger measured value', '--expected-backlog-revision', backlog['backlog_revision'], '--actor', 'assistant')
        self.assertEqual(self.cli('list')['order'], [first['idea_id'],second['idea_id']])
        self.assertEqual(result['proposal']['snapshot']['ratings']['urgency'],7)
        self.cli('place', second['idea_id'], '--position', 1, '--reason', 'My choice', '--expected-backlog-revision', backlog['backlog_revision'], '--actor', 'operator')
        self.assertEqual(self.cli('list')['order'], [second['idea_id'],first['idea_id']])
        self.cli('place', first['idea_id'], '--position', 1, '--reason', 'Stale', '--expected-backlog-revision', backlog['backlog_revision'], '--actor', 'operator', ok=False)
        self.cli('propose', first['idea_id'], '--position', 1, '--reason', 'No scores', '--expected-backlog-revision', backlog['backlog_revision']+1, '--actor', 'assistant', ok=False)
        self.cli('place', first['idea_id'], '--position', 1, '--reason', 'Manual incomplete move', '--expected-backlog-revision', backlog['backlog_revision']+1, '--actor', 'operator')

    def test_concurrent_captures_do_not_lose_records(self):
        paths = [self.file('Concurrent '+str(n), '.txt') for n in range(12)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda p: self.cli('capture','--text-file',p,'--actor','operator'), paths))
        self.assertEqual(len(set(r['idea']['idea_id'] for r in results)),12)
        self.assertEqual(len(self.cli('list')['order']),12)
        self.assertEqual(self.cli('list')['backlog_revision'],12)

    def test_corruption_is_reported_and_never_reset(self):
        self.capture()
        for raw in ('{broken', '{}', '{"schema_version":999}', 'null'):
            (self.store/'state.json').write_text(raw)
            self.cli('doctor', ok=False)
            self.cli('capture','--text-file',self.file('new'),'--actor','operator',ok=False)
            self.assertEqual((self.store/'state.json').read_text(),raw)

    def test_real_plan_archive_execution_and_next_slice(self):
        idea = self.ready()
        packet = self.cli('handoff',idea['idea_id'])
        self.assertIn('idea_revision: 4',packet['trace_block'])
        plan_path = self.plan_file(idea)
        result = self.register(idea, plan_path)
        plan = result['plan']
        self.assertEqual(result['idea']['status'],'archived')
        archive = self.store/'archive'/idea['idea_id']/'r4.json'
        original_archive = archive.read_bytes()
        self.assertEqual(self.register(idea,plan_path)['plan']['plan_id'],plan['plan_id'])
        receipt = dict(idea_id=idea['idea_id'], plan_id=plan['plan_id'], attempt_id='attempt_1', status='failed', evidence=['Trial user could not complete flow'])
        receipt_path = self.file(receipt)
        args = ('record-execution',idea['idea_id'],'--plan-id',plan['plan_id'],'--path',receipt_path,'--actor','assistant')
        self.cli(*args)
        self.cli(*args)
        receipt['status'] = 'succeeded'
        self.cli('record-execution',idea['idea_id'],'--plan-id',plan['plan_id'],'--path',self.file(receipt),'--actor','assistant',ok=False)
        receipt['attempt_id'] = 'attempt_2'
        receipt['evidence'] = ['Observed user completed the corrected flow']
        self.cli('record-execution',idea['idea_id'],'--plan-id',plan['plan_id'],'--path',self.file(receipt),'--actor','assistant')
        next_shape = self.shape_data('Test a second cohort')
        next_shape['learning'] = ['The first flow needed simpler wording']
        resumed = self.edit('shape',result['idea'],next_shape)
        self.assertEqual(resumed['revision'],5)
        self.assertEqual(resumed['status'],'active')
        self.assertEqual(len(resumed['executions']),2)
        self.assertEqual(archive.read_bytes(),original_archive)
        self.cli('doctor')
        Path(plan['path']).write_text(Path(plan['path']).read_text()+'\nChanged accepted evidence\n')
        self.cli('doctor',ok=False)
        receipt['attempt_id']='attempt_3'
        self.cli('record-execution',idea['idea_id'],'--plan-id',plan['plan_id'],'--path',self.file(receipt),'--actor','assistant',ok=False)

    def test_working_plan_can_progress_and_move_while_accepted_evidence_is_frozen(self):
        idea=self.ready()
        working=self.plan_file(idea)
        original=working.read_bytes()
        plan=self.register(idea,working)['plan']
        self.assertNotEqual(plan['path'],str(working))
        self.assertEqual(plan['source_path'],str(working))
        frozen=Path(plan['path'])
        self.assertEqual(frozen.read_bytes(),original)
        working.write_text(working.read_text()+'\n## Progress\n- [x] Finished actual trial\n')
        moved=working.with_name('archived-plan.md')
        working.rename(moved)
        receipt=dict(idea_id=idea['idea_id'],plan_id=plan['plan_id'],attempt_id='native-run-1',status='succeeded',evidence=['Observed trial result',str(moved)])
        self.cli('record-execution',idea['idea_id'],'--plan-id',plan['plan_id'],'--path',self.file(receipt),'--actor','assistant')
        self.cli('doctor')
        frozen.unlink()
        self.cli('doctor',ok=False)
        self.cli('repair-views')
        self.assertEqual(frozen.read_bytes(),original)
        frozen.write_text('Tampered approved evidence')
        self.cli('repair-views',ok=False)
        self.assertEqual(frozen.read_text(),'Tampered approved evidence')

    def test_optional_external_plan_validator_on_declared_action(self):
        skill_root = SCRIPT.parent.parent
        config_path = next((path for path in (skill_root/'config.json', skill_root.parent/'config.json') if path.exists()), None)
        if config_path is None:
            self.skipTest('Optional integration: no local config with an external plan validator')
        config = json.loads(config_path.read_text(encoding='utf-8'))
        if config.get('plan_validator_argv') is None:
            self.skipTest('Optional integration: plan_validator_argv is not configured')
        self.script=SCRIPT
        idea=self.ready()
        path=self.plan_file(idea)
        path.write_text(path.read_text().replace('No file changes.','### CREATE src/idea-example.txt\nWrite the observed result.'))
        result=self.register(idea,path)
        self.assertEqual(result['plan']['validation']['external']['returncode'],0)
        self.cli('doctor')

    def test_invalid_plan_and_receipt_cannot_archive_or_link(self):
        idea = self.ready()
        bad = self.file('# Plan\n## Idea trace\nidea_id: '+idea['idea_id']+'\nidea_revision: 4\n','.md')
        self.register(idea,bad,ok=False)
        wrong = self.plan_file(dict(idea,idea_id='idea_'+'a'*32))
        self.register(idea,wrong,ok=False)
        self.assertEqual(self.cli('show',idea['idea_id'])['idea']['status'],'active')
        plan = self.register(idea)['plan']
        for patch in ({'idea_id':'wrong'},{'plan_id':'wrong'},{'evidence':[]},{'status':'unknown'}):
            receipt = dict(idea_id=idea['idea_id'],plan_id=plan['plan_id'],attempt_id='run-1',status='succeeded',evidence=['Observed'])
            receipt.update(patch)
            self.cli('record-execution',idea['idea_id'],'--plan-id',plan['plan_id'],'--path',self.file(receipt),'--actor','assistant',ok=False)

    def test_archive_view_repair_never_overwrites_differing_snapshot(self):
        idea = self.ready()
        self.register(idea)
        path = self.store/'archive'/idea['idea_id']/'r4.json'
        original = path.read_bytes()
        path.unlink()
        self.cli('doctor',ok=False)
        self.cli('repair-views')
        self.assertEqual(path.read_bytes(),original)
        path.write_text('{}')
        self.cli('repair-views',ok=False)
        self.assertEqual(path.read_text(),'{}')

    def test_materialization_failure_reports_commit_and_can_be_repaired(self):
        idea = self.ready()
        (self.store/'archive').write_text('Blocked directory')
        result = self.register(idea,ok=False)
        self.assertTrue(result['committed'])
        self.assertEqual(self.cli('show',idea['idea_id'])['idea']['status'],'archived')
        (self.store/'archive').unlink()
        self.cli('repair-views')
        self.cli('doctor')

    def test_fixed_validator_fails_closed_and_cannot_be_replaced_by_plan(self):
        copied = self.root/'installed'
        shutil.copytree(SCRIPT.parent,copied/'scripts')
        self.script = copied/'scripts/idea.py'
        validator = self.file('import sys\nprint("rejected")\nsys.exit(9)\n','.py')
        (copied/'config.json').write_text(json.dumps({'plan_validator_argv':[sys.executable,str(validator)],'validator_timeout_seconds':2}))
        idea = self.ready()
        self.register(idea,ok=False)
        self.assertEqual(self.cli('show',idea['idea_id'])['idea']['status'],'active')
        validator.write_text('import sys\nassert sys.argv[1].endswith(".md")\nprint("validated")\n')
        result = self.register(idea)
        self.assertEqual(result['plan']['validation']['external']['returncode'],0)

    def test_installed_config_loss_fails_closed_even_with_explicit_store(self):
        skill=self.script.parent.parent
        (skill/'.glitch-idea-install.json').write_text('{}')
        (skill/'config.json').unlink()
        result=self.cli('list',ok=False)
        self.assertEqual(result['error']['code'],'invalid_config')
        self.assertFalse(self.store.exists())
        (skill/'config.json').write_text('{}')
        self.cli('list',ok=False)

    def test_missing_authority_never_silently_resets_captured_ideas(self):
        self.capture()
        (self.store/'state.json').unlink()
        self.cli('doctor',ok=False)
        self.cli('capture','--text-file',self.file('Do not reset'),'--actor','operator',ok=False)
        self.assertFalse((self.store/'state.json').exists())

    def test_concurrent_stale_shape_writers_only_one_commits(self):
        idea=self.capture()
        paths=[self.file(self.shape_data('Choice '+str(n))) for n in range(2)]
        def attempt(path):
            result=subprocess.run([sys.executable,str(self.script),'--store',str(self.store),'shape',idea['idea_id'],'--file',str(path),'--expected-revision','1','--actor','assistant'],capture_output=True,text=True)
            return json.loads(result.stdout)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(attempt,paths))
        self.assertEqual(sum(r['ok'] for r in results),1)
        self.assertEqual(self.cli('show',idea['idea_id'])['idea']['revision'],2)

    def test_input_bounds_and_malformed_shape_leave_store_unchanged(self):
        idea=self.capture()
        before=(self.store/'state.json').read_bytes()
        for raw in ('{"scope":"project"}', '{"outcome":1,"outcome":2}', '{not json}'):
            self.cli('shape',idea['idea_id'],'--file',self.file(raw),'--expected-revision',1,'--actor','assistant',ok=False)
        self.cli('capture','--text-file',self.file('x'*(1024*1024+1)),'--actor','operator',ok=False)
        self.cli('show','../../state',ok=False)
        self.assertEqual((self.store/'state.json').read_bytes(),before)


if __name__ == '__main__':
    unittest.main()
