"""Managed trusted-CLI eligibility and actual validator subprocesses. Operator.

Disposable Stores use real Service acceptance handlers and real placement
application; no packet is required for planning. Validator tests never skip.
"""
import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
import idea_handoff_evidence as codec
import idea_asset_evidence as assets
import idea_service as application
from idea_domain import IdeaError, digest, now
from idea_store import Store
from idea_service import Service, TrustedContext
from idea_steps.shape import HANDLER as SHAPE
from idea_steps.method import HANDLER as METHOD
from idea_steps.visualize import HANDLER as VISUALIZE, current_source
from idea_steps.assess import HANDLER as ASSESS
from test_workflow import fields
from test_handoff_store import publication_payload

VALIDATOR = Path(__file__).resolve().parent/'fixtures/handoff_plan_validator.py'


def accepted_managed(store, workspace, *, session_id=None, through='assess'):
    """Create real accepted workflow/placement through Service on a fresh Store."""
    sid = store.create_session() if session_id is None else session_id
    service = Service(store, {}, TrustedContext('Operator', sid), handlers={
        'shape':SHAPE, 'method':METHOD, 'visualize':VISUALIZE, 'assess':ASSESS})
    answers = fields()
    answers['capture']['workspace']['path'] = str(Path(workspace).resolve())
    captured = service.capture(dict(request_id='managed-capture',
        raw_text=answers['capture']['raw_text'], workspace=answers['capture']['workspace']))
    key = captured['idea_id']
    if through == 'capture':
        return service, key
    for step in ('priorities','shape','method','visualize','assess'):
        state = service.state()
        service.accept(dict(request_id='managed-'+step,idea_id=key,
            expected_revision=state['revision'], expected_draft_version=state['draft_version'],
            step=step,fields=answers[step],proposal_id=None,
            expected_backlog_revision=state['backlog_revision'] if step=='assess' else None))
        if step == through:
            break
    return service, key


class HandoffServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root = self.directory/'Ideas café'; self.workspace = self.directory/'Work 💡'
        self.workspace.mkdir()
        self.store = Store(self.root,observer='Operator')
        self.service,self.key = accepted_managed(self.store,self.workspace)
        self.config = dict(store_path=str(self.root),plan_validator_argv=[sys.executable,str(VALIDATOR)],
                           validator_timeout_seconds=2)

    def files(self):
        return {p.relative_to(self.root).as_posix():p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def idea(self):
        with self.store.transaction() as state:
            return copy.deepcopy(state['ideas'][self.key])

    def call(self,command,**options):
        args = dict(command=command,store=str(self.root),idea_id=self.key,actor='Operator')
        args.update(options)
        return application.run_legacy(SimpleNamespace(**args),self.config)

    def plan(self,*,extra='',name='Plan café.md'):
        path = self.directory/name
        path.write_text('# Plan\n\n## Goal\nActual goal.\n'+extra+'\n## Tasks\nBounded task.\n\n'
            '## Validation\nObserve result.\n\n## Idea trace\nidea_id: '+self.key+
            '\nidea_revision: '+str(self.idea()['revision'])+'\n',encoding='utf-8')
        return path

    def register(self,path):
        return self.call('register-plan',expected_revision=self.idea()['revision'],path=str(path))

    def refused(self,code,callback):
        before = self.files()
        with self.assertRaises(IdeaError) as caught:
            callback()
        self.assertEqual(caught.exception.code,code)
        self.assertEqual(self.files(),before)

    def test_managed_handoff_is_current_read_only_and_does_not_publish_packet(self):
        before = self.files(); original = self.idea()
        result = self.call('handoff')
        self.assertEqual(result['idea_revision'],original['revision'])
        self.assertEqual(result['trace_block'],'## Idea trace\nidea_id: '+self.key+
                         '\nidea_revision: '+str(original['revision']))
        self.assertEqual(result['origin'],original['origin'])
        self.assertEqual(self.files(),before)
        with self.store.transaction() as state:
            self.assertEqual(self.store.handoffs(state,self.key),[])

    def test_incomplete_workflow_refuses_handoff_and_registration_without_validator(self):
        root = self.directory/'Incomplete'
        service,key = accepted_managed(Store(root),self.workspace,through='capture')
        self.root,self.store,self.service,self.key = root,Store(root),service,key
        plan = self.plan()
        with patch.object(application,'validate_plan',side_effect=AssertionError('Premature validator')):
            self.refused('not_ready',lambda:self.call('handoff'))
            self.refused('not_ready',lambda:self.register(plan))

    def test_changed_draft_refuses_both_current_gates_without_validator(self):
        state = self.service.state(); fields = dict(state['accepted']['shape'],outcome='Unsaved change')
        self.service.draft(dict(request_id='changed-shape',idea_id=self.key,
            expected_revision=state['revision'],expected_draft_version=state['draft_version'],
            step='shape',fields=fields))
        plan = self.plan()
        with patch.object(application,'validate_plan',side_effect=AssertionError('Premature validator')):
            self.refused('not_ready',lambda:self.call('handoff'))
            self.refused('not_ready',lambda:self.register(plan))

    def test_cli_priority_edit_requires_browser_review_before_planning(self):
        self.call('rate',expected_revision=self.idea()['revision'],urgency=9,importance=8)
        plan = self.plan()
        self.refused('not_ready',lambda:self.call('handoff'))
        self.refused('not_ready',lambda:self.register(plan))

    def test_missing_workspace_refuses_both_current_gates(self):
        plan = self.plan(); self.workspace.rmdir()
        self.refused('workspace_unavailable',lambda:self.call('handoff'))
        self.refused('workspace_unavailable',lambda:self.register(plan))

    def test_workspace_file_replacement_refuses_both_current_gates(self):
        plan = self.plan(); self.workspace.rmdir(); self.workspace.write_text('Not a directory')
        self.refused('workspace_unavailable',lambda:self.call('handoff'))
        self.refused('workspace_unavailable',lambda:self.register(plan))

    def test_real_validator_refusal_preserves_all_durable_bytes_and_active_status(self):
        plan = self.plan(extra='REJECT_CP4\n'); original = self.idea()
        self.refused('validator_failed',lambda:self.register(plan))
        self.assertEqual(self.idea(),original)
        self.assertEqual(self.idea()['status'],'active')
        self.assertEqual(self.idea()['plans'],[])

    def test_real_validator_success_archives_exact_revision_without_packet_prerequisite(self):
        original = self.idea(); plan = self.plan(); revision = original['revision']
        history = self.root/'history'/self.key/('r'+str(revision)+'.md'); before = history.read_bytes()
        result = self.register(plan); accepted = result['plan']
        validation = accepted['validation']['external']
        self.assertEqual(validation['argv'],[sys.executable,str(VALIDATOR),str(plan)])
        self.assertEqual(validation['returncode'],0)
        self.assertEqual(validation['output'],'CP4 validator accepted\n')
        self.assertFalse(validation['output_truncated'])
        self.assertEqual(accepted['idea_revision'],revision)
        self.assertEqual(result['idea']['revision'],revision)
        self.assertEqual(result['idea']['status'],'archived')
        self.assertEqual(result['idea']['origin'],original['origin'])
        self.assertEqual(history.read_bytes(),before)
        self.assertEqual(Path(accepted['path']).read_bytes(),plan.read_bytes())
        with self.store.transaction() as state:
            self.assertEqual(self.store.handoffs(state,self.key),[])
            self.assertIn(self.key+'/r'+str(revision)+'.json',state['archives'])

    def test_exact_archived_registration_replay_precedes_current_gate_and_validator(self):
        plan = self.plan(); first = self.register(plan); self.workspace.rmdir(); before = self.files()
        with (patch.object(Store,'handoff_observations',side_effect=AssertionError('New gate on replay')),
              patch.object(application,'validate_plan',side_effect=AssertionError('Validator on replay'))):
            repeated = self.register(plan)
        self.assertTrue(repeated['repeated']); self.assertEqual(repeated['plan'],first['plan'])
        self.assertEqual(repeated['idea'],first['idea']); self.assertEqual(self.files(),before)

    def test_archived_new_plan_and_handoff_refuse_without_validator(self):
        self.register(self.plan()); other = self.plan(name='Other plan.md')
        with patch.object(application,'validate_plan',side_effect=AssertionError('Archived validator')):
            self.refused('archived_revision',lambda:self.register(other))
            self.refused('archived_revision',lambda:self.call('handoff'))

    def test_archived_replay_still_refuses_changed_working_plan(self):
        plan = self.plan(); self.register(plan); plan.write_bytes(plan.read_bytes()+b'Changed\n')
        with patch.object(Store,'handoff_observations',side_effect=AssertionError('New gate on replay')):
            self.refused('changed_artifact',lambda:self.register(plan))

    def accepted_design(self):
        data='Actual accepted design café\n'.encode(); token='a'*32
        sid=self.service.context.session_id; revision=self.idea()['revision']
        intent=dict(schema_version=1,kind='upload-intent',idea_id=self.key,source_revision=revision,
            actor='Operator',timestamp=now(),upload_id='upload_'+token,asset_id='asset_'+token,
            session_id=sid,name='Design café.txt',declared_type='text/plain',size=len(data))
        self.store.mutate_assets(sid,'asset-intent',{'operation':'fixture-intent'},
            lambda state:{'idea_id':self.key},prepare_records=lambda state:[intent])
        blob=self.root/assets.blob_path(intent['asset_id']);blob.parent.mkdir(parents=True,exist_ok=True)
        blob.write_bytes(data)
        complete=dict(intent,kind='asset',blob_path=assets.blob_path(intent['asset_id']),
                      validated_type='text/plain',sha256=digest(data))
        self.store.mutate_assets(sid,'asset-complete',{'operation':'fixture-complete'},
            lambda state:{'idea_id':self.key},prepare_records=lambda state:[complete])
        observed=self.service.state()
        visual=dict(observed['accepted']['visualize'],disposition='accepted_set',reason=None,
                    design_set_id='set_'+token)
        payload=self.service._edit_payload(dict(request_id='asset-set',idea_id=self.key,
            expected_revision=observed['revision'],expected_draft_version=observed['draft_version'],
            step='visualize',fields=visual,proposal_id=None,expected_backlog_revision=None),accept=True)
        def prepare(state):
            idea=state['ideas'][self.key];source=current_source(idea)
            return [dict(schema_version=1,kind='design-set',idea_id=self.key,source_revision=idea['revision'],
                actor='Operator',timestamp=now(),set_id='set_'+token,session_id=sid,source=source,
                source_digest=assets.source_digest(source),members=[dict(asset_id=complete['asset_id'],
                    name=complete['name'],type=complete['validated_type'],size=complete['size'],sha256=complete['sha256'])])]
        self.store.mutate_assets(sid,'asset-set',{'operation':'fixture-set'},
            lambda state:self.service.accept_in_state(state,payload,None),prepare_records=prepare)
        observed=self.service.state()
        self.service.accept(dict(request_id='asset-assess',idea_id=self.key,
            expected_revision=observed['revision'],expected_draft_version=observed['draft_version'],
            step='assess',fields=observed['accepted']['assess'],proposal_id=None,
            expected_backlog_revision=observed['backlog_revision']))
        return blob,data

    def test_real_accepted_design_bytes_and_links_survive_registration(self):
        blob,data=self.accepted_design();original=self.idea();before=self.files()
        self.call('handoff');self.assertEqual(self.files(),before)
        accepted=self.register(self.plan());self.assertEqual(blob.read_bytes(),data)
        self.assertEqual(accepted['idea']['revision'],original['revision'])
        self.assertEqual(accepted['idea']['origin'],original['origin'])
        after=self.files()
        for relative,raw in before.items():
            if relative.startswith(('assets/','history/')):
                self.assertEqual(after[relative],raw)

    def test_changed_accepted_blob_refuses_handoff_and_registration(self):
        blob,data=self.accepted_design();plan=self.plan();revision=self.idea()['revision']
        blob.write_bytes(data+b'changed')
        self.refused('corrupt_store',lambda:self.call('handoff'))
        self.refused('corrupt_store',lambda:self.call('register-plan',expected_revision=revision,path=str(plan)))

    def test_published_packet_and_original_history_survive_real_registration_and_replay(self):
        sid = self.service.context.session_id
        payload = publication_payload(self.store,self.key,'service-packet')
        result = self.store.publish_handoff(sid,'service-packet',payload,codec.build_record)
        packet_path = self.root/result['path']; packet = packet_path.read_bytes()
        original = self.idea(); history = self.root/'history'/self.key/('r'+str(original['revision'])+'.md')
        history_raw = history.read_bytes(); plan = self.plan(); accepted = self.register(plan)
        self.assertEqual(packet_path.read_bytes(),packet); self.assertEqual(history.read_bytes(),history_raw)
        self.assertTrue(self.register(plan)['repeated'])
        self.assertEqual(packet_path.read_bytes(),packet)
        with self.store.transaction() as state:
            self.assertEqual(len(self.store.handoffs(state,self.key)),1)
        self.assertEqual(accepted['idea']['revision'],original['revision'])


if __name__ == '__main__':
    unittest.main()
