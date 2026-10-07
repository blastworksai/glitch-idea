"""Moving an idea to a workspace and delivering it, driven through the real CLI and the service."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0, str(SCRIPTS))
import idea_markdown as md
import idea_service as application
from idea_domain import IdeaError, delivery_ref, row_status
from idea_service import Service, TrustedContext
from idea_store import Store

ROOT = Path(__file__).resolve().parents[1]


class MoveCliCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = self.root/'ideas'
        self.copied = self.root/'helper'
        shutil.copytree(SCRIPTS, self.copied/'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        self.script = self.copied/'scripts/idea.py'
        self.workspace = self.root/'workspace'
        self.workspace.mkdir()
        self.other = self.root/'other'
        self.other.mkdir()
        self.config()
        self.counter = 0

    def config(self, default=None, raw=None):
        value = dict(plan_validator_argv=None)
        if default is not None:
            value['default_workspace'] = default
        (self.copied/'config.json').write_text(raw if raw is not None else json.dumps(value))

    def file(self, value, suffix='.json'):
        self.counter += 1
        path = self.root/('input'+str(self.counter)+suffix)
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8', newline='')
        return path

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(self.script), '--store', str(self.store), *map(str, args)], capture_output=True, text=True)

    def cli(self, *args, ok=True):
        result = self.run_cli(*args)
        self.assertTrue(result.stdout.strip(), 'CLI did not return JSON: '+result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['ok'], ok, data)
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        return data

    def refused(self, code, *args):
        data = self.cli(*args, ok=False)
        self.assertEqual(data['error']['code'], code, data)
        return data

    def authority(self):
        return {p.relative_to(self.store).as_posix(): p.read_bytes() for p in self.store.rglob('*') if p.is_file() and p.name != '.lock'}

    def tree(self, base):
        return {p.relative_to(base).as_posix(): (p.read_bytes() if p.is_file() else None) for p in base.rglob('*')}

    def exploration(self):
        return dict(outcome='Reduce manual work', alternatives=[dict(route='Reuse', reason='Check existing route first')],
                    assumptions=['A user has this need'], scope='capability', scope_reason='One new reusable ability',
                    next_slice='Try one real user', learning=[], investment=None, experiment=None,
                    sketch=[dict(title='Run one trial', why_next='Cheapest test', done_when='Trial observed', method=None)])

    def assessment(self):
        return dict(method='wsjf', version='1', inputs=dict(value=8, time_criticality=4, enablement=2, effort=2),
                    basis='Fixture', assumptions=[], confidence='low', provenance='Operator discussion')

    def ready(self, text='A useful new idea'):
        idea = self.cli('capture', '--text-file', self.file(text, '.txt'), '--actor', 'operator')['idea']
        idea = self.cli('exploration', idea['idea_id'], '--file', self.file(self.exploration()), '--expected-revision', idea['revision'], '--actor', 'operator')['idea']
        idea = self.cli('rate', idea['idea_id'], '--urgency', 7, '--importance', 9, '--expected-revision', idea['revision'], '--actor', 'operator')['idea']
        return self.cli('assess', idea['idea_id'], '--file', self.file(self.assessment()), '--expected-revision', idea['revision'], '--actor', 'operator')['idea']

    def plan_file(self, idea, name='plan'):
        path = self.root/(name+'.md')
        path.write_text('# Trial plan\n\n## Goal\nReduce manual work.\n\n## Tasks\n- Run one trial.\n\n## Validation\nObserve actual result.\n\n## Idea trace\nidea_id: '
                        + idea['idea_id']+'\nidea_revision: '+str(idea['revision'])+'\n')
        return path

    def move_args(self, idea, plan=None, workspace=None, name='Atlas'):
        return ('register-plan', idea['idea_id'], '--path', plan or self.plan_file(idea), '--expected-revision', idea['revision'], '--actor', 'operator',
                '--workspace-name', name, '--workspace-path', workspace or self.workspace)

    def moved(self):
        idea = self.ready()
        plan = self.plan_file(idea)
        result = self.cli(*self.move_args(idea, plan))
        return idea, plan, result

    def home_of(self, idea):
        return self.workspace/'ideas'/(idea['idea_id']+'.md')


class RegisterPlanMoveTests(MoveCliCase):
    def test_both_or_neither_workspace_arguments(self):
        idea = self.ready()
        before = self.authority()
        plan = self.plan_file(idea)
        for extra in (('--workspace-name', 'Atlas'), ('--workspace-path', self.workspace)):
            with self.subTest(extra=extra):
                self.refused('invalid_input', 'register-plan', idea['idea_id'], '--path', plan, '--expected-revision', idea['revision'], '--actor', 'operator', *extra)
        self.assertEqual(self.authority(), before)
        self.assertEqual(self.tree(self.workspace), {})

    def test_neither_keeps_todays_behaviour_and_the_idea_stays_in_the_store(self):
        idea = self.ready()
        result = self.cli('register-plan', idea['idea_id'], '--path', self.plan_file(idea), '--expected-revision', idea['revision'], '--actor', 'operator')
        self.assertEqual(result['idea']['status'], 'archived')
        self.assertNotIn('lifecycle', result)
        self.assertTrue((self.store/(idea['idea_id']+'.md')).exists())
        self.assertEqual(self.cli('show', idea['idea_id'])['lifecycle'], 'active')

    def test_move_leaves_the_pointer_and_puts_the_file_in_the_workspace(self):
        idea, plan, result = self.moved()
        self.assertEqual(result['lifecycle'], 'moved')
        self.assertEqual(result['home'], dict(workspace_name='Atlas', workspace_path=str(self.workspace), file_path=str(self.home_of(idea))))
        self.assertIsNone(result['delivered_ref'])
        self.assertEqual(result['idea']['status'], 'archived')
        self.assertFalse(result['resumed'])
        self.assertTrue(self.home_of(idea).is_file())
        self.assertFalse((self.store/(idea['idea_id']+'.md')).exists())
        self.assertTrue((self.store/'history'/idea['idea_id']/'moved.md').is_file())
        self.assertTrue((self.store/'plan-evidence'/(result['plan']['plan_id']+'.md')).is_file())
        self.cli('doctor')

    def test_same_workspace_as_the_default_is_refused_before_anything_is_written(self):
        self.config(default=dict(name='Home base', path=str(self.workspace)))
        idea = self.ready()
        before = self.authority()
        data = self.refused('same_workspace', *self.move_args(idea))
        self.assertEqual(data['workspace_path'], str(self.workspace))
        self.assertEqual(self.authority(), before)
        self.assertEqual(self.tree(self.workspace), {})
        # A different workspace is fine, and a symlinked spelling of the default is the same folder.
        link = self.root/'link'
        link.symlink_to(self.workspace)
        self.refused('same_workspace', *self.move_args(idea, workspace=link))
        self.assertEqual(self.cli(*self.move_args(idea, workspace=self.other))['lifecycle'], 'moved')

    def test_workspace_unavailable_passes_through(self):
        idea = self.ready()
        before = self.authority()
        for path in (self.root/'missing', 'relative/folder', self.workspace/'..'/'workspace'):
            with self.subTest(path=str(path)):
                self.refused('workspace_unavailable', *self.move_args(idea, workspace=path))
        self.assertEqual(self.authority(), before)

    def test_target_overlapping_the_store_is_refused(self):
        idea = self.ready()
        self.refused('target_overlaps_store', *self.move_args(idea, workspace=self.store))

    def test_home_conflict_leaves_everything_untouched(self):
        idea = self.ready()
        home = self.home_of(idea)
        home.parent.mkdir()
        home.write_text('somebody else wrote this')
        before = self.authority()
        data = self.refused('home_conflict', *self.move_args(idea))
        self.assertEqual(data['home']['file_path'], str(home))
        self.assertEqual(home.read_text(), 'somebody else wrote this')
        self.assertEqual(self.authority(), before)
        self.assertEqual(self.cli('show', idea['idea_id'])['lifecycle'], 'active')

    def test_identical_file_already_at_home_resumes(self):
        idea = self.ready()
        home = self.home_of(idea)
        home.parent.mkdir()
        shutil.copy(self.store/(idea['idea_id']+'.md'), home)
        marker = home.stat().st_mtime_ns
        result = self.cli(*self.move_args(idea))
        self.assertTrue(result['resumed'])
        self.assertEqual(result['lifecycle'], 'moved')
        self.assertEqual(home.stat().st_mtime_ns, marker)
        self.assertFalse((self.store/(idea['idea_id']+'.md')).exists())

    def test_stale_revision_is_refused(self):
        idea = self.ready()
        args = list(self.move_args(idea))
        args[args.index('--expected-revision')+1] = idea['revision']-1
        self.refused('stale_revision', *args)

    def test_replay_of_the_same_move_is_repeated_and_writes_nothing(self):
        idea, plan, first = self.moved()
        before, workspace = self.authority(), self.tree(self.workspace)
        again = self.cli(*self.move_args(idea, plan))
        self.assertTrue(again['repeated'])
        self.assertEqual(again['plan']['plan_id'], first['plan']['plan_id'])
        self.assertEqual(again['lifecycle'], 'moved')
        self.assertEqual(self.authority(), before)
        self.assertEqual(self.tree(self.workspace), workspace)

    def test_a_different_replay_is_idea_moved_with_the_home(self):
        idea, plan, first = self.moved()
        before = self.authority()
        cases = {'other workspace': self.move_args(idea, plan, workspace=self.other),
                 'other name': self.move_args(idea, plan, name='Elsewhere'),
                 'other plan': self.move_args(idea, self.plan_file(idea, 'second')),
                 'no workspace': ('register-plan', idea['idea_id'], '--path', plan, '--expected-revision', idea['revision'], '--actor', 'operator')}
        for label, args in cases.items():
            with self.subTest(label):
                data = self.refused('idea_moved', *args)
                self.assertEqual(data['home']['file_path'], str(self.home_of(idea)))
        self.assertEqual(self.authority(), before)
        self.assertEqual(self.tree(self.other), {})


class MovedLifecycleTests(MoveCliCase):
    def test_show_and_list_carry_lifecycle_home_and_delivered_ref(self):
        active = self.ready('Stays put')
        idea, plan, _ = self.moved()
        shown = self.cli('show', idea['idea_id'])
        self.assertEqual((shown['lifecycle'], shown['delivered_ref']), ('moved', None))
        self.assertEqual(shown['home']['file_path'], str(self.home_of(idea)))
        self.assertEqual(set(shown['idea']), set(active), 'the idea record keeps its exact key set')
        stay = self.cli('show', active['idea_id'])
        self.assertEqual((stay['lifecycle'], stay['home'], stay['delivered_ref']), ('active', None, None))
        listed = self.cli('list')
        self.assertEqual(listed['lifecycles'][idea['idea_id']]['lifecycle'], 'moved')
        self.assertEqual(listed['lifecycles'][idea['idea_id']]['home']['workspace_path'], str(self.workspace))
        self.assertEqual(listed['lifecycles'][active['idea_id']], dict(lifecycle='active', home=None, delivered_ref=None))
        self.assertEqual(set(listed['order']), set(listed['lifecycles']))
        for row in listed['ideas']:
            self.assertEqual(set(row), set(active))
        self.cli('deliver', idea['idea_id'], '--ref', 'work-item-7', '--actor', 'operator')
        shown = self.cli('show', idea['idea_id'])
        self.assertEqual((shown['lifecycle'], shown['delivered_ref']), ('delivered', 'work-item-7'))
        self.assertEqual(self.cli('list')['lifecycles'][idea['idea_id']]['delivered_ref'], 'work-item-7')

    def test_every_other_verb_on_a_moved_idea_is_idea_moved(self):
        idea, plan, result = self.moved()
        key, revision = idea['idea_id'], result['idea']['revision']
        backlog = self.cli('list')['backlog_revision']
        receipt = self.file(dict(idea_id=key, plan_id=result['plan']['plan_id'], attempt_id='run-1', status='succeeded', evidence=['Observed']))
        verbs = {
            'exploration': ('exploration', key, '--file', self.file(self.exploration()), '--expected-revision', revision, '--actor', 'operator'),
            'rate': ('rate', key, '--urgency', 5, '--importance', 5, '--expected-revision', revision, '--actor', 'operator'),
            'assess': ('assess', key, '--file', self.file(self.assessment()), '--expected-revision', revision, '--actor', 'operator'),
            'propose': ('propose', key, '--position', 1, '--reason', 'why', '--expected-backlog-revision', backlog, '--actor', 'operator'),
            'place': ('place', key, '--position', 1, '--reason', 'why', '--expected-backlog-revision', backlog, '--actor', 'operator'),
            'handoff': ('handoff', key),
            'record-execution': ('record-execution', key, '--plan-id', result['plan']['plan_id'], '--path', receipt, '--actor', 'operator'),
            'register-plan': ('register-plan', key, '--path', self.plan_file(idea, 'third'), '--expected-revision', revision, '--actor', 'operator'),
        }
        before, workspace = self.authority(), self.tree(self.workspace)
        for verb, args in verbs.items():
            with self.subTest(verb):
                data = self.refused('idea_moved', *args)
                self.assertEqual(data['home']['file_path'], str(self.home_of(idea)))
        self.assertEqual(self.authority(), before)
        self.assertEqual(self.tree(self.workspace), workspace)

    def test_a_delivered_idea_refuses_the_same_verbs(self):
        idea, plan, result = self.moved()
        self.cli('deliver', idea['idea_id'], '--ref', 'r', '--actor', 'operator')
        self.refused('idea_moved', 'rate', idea['idea_id'], '--urgency', 5, '--importance', 5, '--expected-revision', result['idea']['revision'], '--actor', 'operator')
        self.refused('idea_moved', 'handoff', idea['idea_id'])

    def test_other_ideas_keep_working_after_a_move(self):
        idea, plan, _ = self.moved()
        other = self.ready('Another one')
        self.assertEqual(self.cli('rate', other['idea_id'], '--urgency', 2, '--importance', 3, '--expected-revision', other['revision'], '--actor', 'operator')['idea']['revision'], other['revision']+1)
        self.cli('doctor')

    def test_service_state_exposes_lifecycle_and_the_default_workspace(self):
        idea, plan, _ = self.moved()
        store = Store(self.store)
        context = TrustedContext('browser', store.create_session())
        absent = Service(store, {}, context).state(idea['idea_id'])
        self.assertEqual((absent['lifecycle'], absent['delivered_ref'], absent['default_workspace']), ('moved', None, None))
        self.assertEqual(absent['home']['workspace_name'], 'Atlas')
        chosen = dict(name='ideas', path=str(self.other))
        configured = Service(store, dict(default_workspace=chosen), context).state(idea['idea_id'])
        self.assertEqual(configured['default_workspace'], chosen)
        blank = Service(store, dict(default_workspace=chosen), TrustedContext('browser', store.create_session())).state()
        self.assertEqual((blank['lifecycle'], blank['home'], blank['delivered_ref']), (None, None, None))
        self.assertEqual(blank['default_workspace'], chosen)

    def test_row_status_helper_keeps_the_stored_status_on_the_wire(self):
        self.assertEqual(row_status('in-progress', 'active'), 'in-progress')
        self.assertEqual(row_status('archived', 'moved'), 'moved')
        self.assertEqual(row_status('archived', 'delivered'), 'delivered')
        with self.assertRaises(IdeaError):
            row_status('active', 'gone')


class DeliverTests(MoveCliCase):
    def test_deliver_happy_path_writes_one_immutable_pointer_and_link(self):
        idea, plan, _ = self.moved()
        key = idea['idea_id']
        result = self.cli('deliver', key, '--ref', 'work-item-7', '--actor', 'operator')
        self.assertEqual((result['lifecycle'], result['delivered_ref']), ('delivered', 'work-item-7'))
        self.assertEqual(result['delivery']['actor'], 'operator')
        self.assertNotIn('repeated', result)
        pointer = self.store/'history'/key/'delivered.md'
        self.assertEqual(md.decode_delivered(pointer.read_bytes())['ref'], 'work-item-7')
        index = md.decode_index((self.store/'IDEAS.md').read_bytes())
        self.assertEqual([link['idea_id'] for link in md.delivered_links(index.metadata['extensions'])], [key])
        self.assertEqual([link['idea_id'] for link in md.move_links(index.metadata['extensions'])], [key])
        self.assertTrue(self.home_of(idea).is_file(), 'the workspace file is untouched')
        self.cli('doctor')

    def test_same_ref_again_is_repeated_and_writes_nothing(self):
        idea, plan, _ = self.moved()
        self.cli('deliver', idea['idea_id'], '--ref', 'work-item-7', '--actor', 'operator')
        before = self.authority()
        again = self.cli('deliver', idea['idea_id'], '--ref', 'work-item-7', '--actor', 'someone-else')
        self.assertTrue(again['repeated'])
        self.assertEqual(again['delivered_ref'], 'work-item-7')
        self.assertEqual(self.authority(), before)

    def test_a_different_ref_is_a_delivery_conflict(self):
        idea, plan, _ = self.moved()
        self.cli('deliver', idea['idea_id'], '--ref', 'first', '--actor', 'operator')
        before = self.authority()
        data = self.refused('delivery_conflict', 'deliver', idea['idea_id'], '--ref', 'second', '--actor', 'operator')
        self.assertEqual(data['delivered_ref'], 'first')
        self.assertEqual(self.authority(), before)
        self.assertEqual(self.cli('show', idea['idea_id'])['delivered_ref'], 'first')

    def test_an_idea_that_is_not_moved_is_not_moved(self):
        idea = self.ready()
        before = self.authority()
        self.refused('not_moved', 'deliver', idea['idea_id'], '--ref', 'x', '--actor', 'operator')
        self.assertEqual(self.authority(), before)

    def test_unknown_idea_is_not_found(self):
        self.ready()
        self.refused('not_found', 'deliver', 'idea_'+'f'*32, '--ref', 'x', '--actor', 'operator')

    def test_invalid_refs_are_refused_before_any_write(self):
        idea, plan, _ = self.moved()
        before = self.authority()
        bad = {'empty': '', 'blank': '   ', 'too long': 'x'*501, 'two lines': 'one\ntwo', 'carriage return': 'one\rtwo',
               'bell': 'a\x07b', 'delete': 'a\x7fb', 'tab': 'a\tb', 'line separator': 'a b'}
        for label, ref in bad.items():
            with self.subTest(label):
                self.refused('invalid_input', 'deliver', idea['idea_id'], '--ref', ref, '--actor', 'operator')
        self.assertEqual(self.authority(), before)
        self.assertEqual(delivery_ref('x'*500), 'x'*500)
        self.assertEqual(self.cli('deliver', idea['idea_id'], '--ref', 'x'*500, '--actor', 'operator')['delivered_ref'], 'x'*500)

    def test_there_is_no_undeliver_verb(self):
        for verb in ('undeliver', 'un-deliver', 'redeliver'):
            result = self.run_cli(verb)
            self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('undeliver', (SCRIPTS/'idea.py').read_text())


class DoctorMovedTests(MoveCliCase):
    def test_a_healthy_moved_idea_has_no_notice(self):
        self.moved()
        result = self.cli('doctor')
        self.assertTrue(result['healthy'])
        self.assertNotIn('notices', result)

    def test_missing_home_file_is_unhealthy_and_names_the_path(self):
        idea, plan, _ = self.moved()
        self.home_of(idea).unlink()
        data = self.refused('unhealthy_store', 'doctor')
        self.assertTrue(any(str(self.home_of(idea)) in issue for issue in data['issues']), data)

    def test_missing_workspace_folder_is_unhealthy_and_names_the_path(self):
        idea, plan, _ = self.moved()
        shutil.rmtree(self.workspace)
        data = self.refused('unhealthy_store', 'doctor')
        self.assertTrue(any(str(self.workspace) in issue for issue in data['issues']), data)

    def test_home_file_edited_since_the_move_is_healthy_with_a_notice_only(self):
        idea, plan, _ = self.moved()
        home = self.home_of(idea)
        home.write_bytes(home.read_bytes()+b'\nAn edit made in the workspace.\n')
        result = self.cli('doctor')
        self.assertTrue(result['healthy'])
        self.assertEqual(len(result['notices']), 1)
        self.assertIn(str(home), result['notices'][0])
        self.assertTrue(self.cli('repair-views')['healthy'])

    def test_repair_never_writes_outside_the_store(self):
        idea, plan, result = self.moved()
        # A damaged archive view inside the store is repaired; a missing home file and workspace are not recreated.
        view = next((self.store/'archive').rglob('*.json'))
        view.unlink()
        self.home_of(idea).unlink()
        outside = self.tree(self.root/'workspace')
        elsewhere = self.tree(self.root/'other')
        data = self.refused('unhealthy_store', 'repair-views')
        self.assertTrue(any(str(self.home_of(idea)) in issue for issue in data['issues']))
        self.assertTrue(view.is_file(), 'the in-store view is repaired')
        self.assertEqual(self.tree(self.root/'workspace'), outside)
        self.assertEqual(self.tree(self.root/'other'), elsewhere)
        self.assertFalse(self.home_of(idea).exists())
        shutil.rmtree(self.workspace)
        self.refused('unhealthy_store', 'repair-views')
        self.assertFalse(self.workspace.exists())


class DefaultWorkspaceConfigTests(MoveCliCase):
    def test_validator_accepts_none_and_a_real_folder(self):
        self.assertIsNone(application.default_workspace(None))
        self.assertEqual(application.default_workspace(dict(name='ideas', path=str(self.workspace))), dict(name='ideas', path=str(self.workspace)))

    def test_validator_refuses_everything_else_with_invalid_config(self):
        bad = [[], 'path', dict(name='n'), dict(path=str(self.workspace)), dict(name='n', path=str(self.workspace), extra=1),
               dict(name='', path=str(self.workspace)), dict(name='a\nb', path=str(self.workspace)), dict(name='x'*101, path=str(self.workspace)),
               dict(name='n', path='relative/dir'), dict(name='n', path=str(self.root/'missing')),
               dict(name='n', path=str(self.workspace/'file.txt')), dict(name=1, path=str(self.workspace)), dict(name='n', path='')]
        (self.workspace/'file.txt').write_text('not a folder')
        for value in bad:
            with self.subTest(value=value), self.assertRaises(IdeaError) as caught:
                application.default_workspace(value)
            self.assertEqual(caught.exception.code, 'invalid_config')

    def test_cli_reports_a_bad_default_workspace_as_a_plain_config_error(self):
        self.config(default=dict(name='ideas', path=str(self.root/'missing')))
        data = self.refused('invalid_config', 'list')
        self.assertIn('default_workspace', data['error']['message'])
        self.config(raw=json.dumps(dict(default_workspace='nope')))
        self.refused('invalid_config', 'list')

    def test_absent_default_means_no_same_workspace_check(self):
        idea = self.ready()
        self.assertEqual(self.cli(*self.move_args(idea))['lifecycle'], 'moved')

    def test_configuration_function_exposes_the_default_only_when_set(self):
        script = ('import sys; sys.path.insert(0, %r); import idea, json; print(json.dumps(idea.configuration()))' % str(self.copied/'scripts'))
        absent = json.loads(subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, check=True).stdout)
        self.assertNotIn('default_workspace', absent)
        self.config(default=dict(name='ideas', path=str(self.other)))
        present = json.loads(subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(present['default_workspace'], dict(name='ideas', path=str(self.other)))

    def test_example_config_names_a_neutral_default(self):
        example = json.loads((ROOT/'config.example.json').read_text())
        chosen = example['default_workspace']
        self.assertEqual(set(chosen), {'name', 'path'})
        self.assertTrue(os.path.isabs(chosen['path']))
        self.assertNotIn('claude', json.dumps(example).lower())

    def test_no_shipped_script_hard_codes_a_workspace_path(self):
        example = json.loads((ROOT/'config.example.json').read_text())['default_workspace']
        literal = re.compile(r"""['"]/(?:home|Users|opt|srv|mnt|var|root)/""")
        for path in sorted(SCRIPTS.glob('*.py')):
            source = path.read_text()
            with self.subTest(script=path.name):
                self.assertNotIn(example['path'], source)
                self.assertIsNone(literal.search(source), 'a literal workspace path in '+path.name)


if __name__ == '__main__':
    unittest.main()
