"""injected agent service seam against real Markdown Store."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'))
from idea_domain import IdeaError
from idea_service import Service, TrustedContext, TrustedStepHandler
from idea_store import Store
from idea_workflow import source_digest
from test_agent_bridge import SHAPE
from test_workflow import fields as workflow_fields

PID = 'proposal_'+'a'*32


class Provider:
    """Trusted fixture, no production policy/source adapter implementation."""
    def __init__(self, case):
        self.case = case; self.live = dict(binding_id='binding_'+'b'*32,
            generation='agent_'+'c'*32, session_id=case.sid, agent_status='connected')
        self.original = None; self.calls = []; self.overlay_change = None
        self.mutate_inputs = False

    def context(self, context):
        self.case.assertIsNone(getattr(self.case.store._contexts, 'active', None))
        self.case.assertIsNot(context, self.case.context)
        self.calls.append('context'); return self.live

    def source(self, idea):
        data = {'capture': copy.deepcopy(idea['workflow']['steps']['capture']['fields'])}
        return dict(accepted_revision=idea['revision'], draft_version=idea['workflow']['draft_version'],
                    data=data, source_digest=source_digest('shape', idea['revision'], data))

    def project(self, state, idea, context, live):
        self.case.assertIs(state, self.case.store._contexts.active['state'])
        if idea is not None: self.case.assertIs(idea, state['ideas'][idea['idea_id']])
        if live is not None: self.case.assertIsNot(live, self.live)
        self.calls.append('project')
        status = live['agent_status'] if live is not None else 'disconnected'
        source = self.source(idea) if idea is not None else None
        proposals = []
        if self.original is not None:
            changed = source['accepted_revision'] != self.original['accepted_revision'] or source['data'] != self.original['data']
            response_stale = changed or source['draft_version'] != self.original['draft_version']
            reason = 'agent_unavailable' if status != 'connected' else 'stale_source' if changed else None
            proposals = [dict(proposal_id=PID, request_id='proposal-request', operation='shape',
                accepted_revision=self.original['accepted_revision'], draft_version=self.original['draft_version'],
                source_digest=self.original['source_digest'], proposal=copy.deepcopy(SHAPE),
                evidence=dict(path='history/'+idea['idea_id']+'/metadata/'+'b'*64+'.md',sha256='a'*64),content_omitted=False,
                stale=response_stale or status != 'connected',
                stale_reason=reason or ('stale_source' if response_stale else None),
                acceptance_eligible=reason is None, acceptance_reason=reason)]
        overlay = dict(agent_status=status, agent_generation=live['generation'] if live is not None else None,
            resume=dict(required=status!='connected',
            reason=None if status=='connected' else 'agent_paused' if status=='paused' else 'agent_disconnected'),
            capabilities=dict(agent=status=='connected', memory=status=='connected'),
            proposal_sources={'shape':dict(available=True, code='ok', source=source)} if source else {},
            proposals=proposals,proposal_inventory=dict(total=len(proposals),projected=len(proposals),omitted=0,
                content_omitted=0,index_path=idea['idea_id']+'.md' if idea is not None else None))
        if self.overlay_change: self.overlay_change(overlay)
        if self.mutate_inputs: state['backlog_revision'] += 1
        return overlay

    def validate_acceptance(self, state, idea, payload, source, context, live):
        self.case.assertIs(state, self.case.store._contexts.active['state'])
        self.case.assertIs(idea, state['ideas'][idea['idea_id']])
        self.case.assertIsNot(context, self.case.context)
        self.calls.append('validate')
        if self.mutate_inputs: state['backlog_revision'] += 1
        if payload['proposal_id'] is None: return
        if live is None or live['agent_status'] != 'connected': raise IdeaError('agent_unavailable', 'Disconnected fixture')
        if payload['proposal_id'] != PID: raise IdeaError('proposal_not_found', 'Unknown fixture proposal')
        # Draft version and edited Shape target are intentionally independent of
        # consumed Capture inputs. The current final acceptance source differs.
        current = self.source(idea)
        if current['accepted_revision'] != self.original['accepted_revision'] or current['data'] != self.original['data']:
            raise IdeaError('stale_source', 'Consumed fixture source changed')
        self.case.assertEqual(source['source_digest'], source_digest(payload['step'], idea['revision'],
            {'capture': idea['workflow']['steps']['capture']['fields'], 'shape': payload['fields']}))


class AgentResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.workspace = self.root/'workspace'; self.workspace.mkdir()
        self.store = Store(self.root/'ideas', observer='Operator'); self.sid = self.store.create_session()
        self.context = TrustedContext('Operator', self.sid)
        self.provider = Provider(self)
        def handler(*_): self.provider.calls.append('handler')
        self.handlers = {'shape': TrustedStepHandler(handler), 'method': TrustedStepHandler(handler)}
        self.service = Service(self.store, {}, self.context, handlers=self.handlers, agent_provider=self.provider)

    def code(self, expected, callback):
        with self.assertRaises(IdeaError) as caught: callback()
        self.assertEqual(caught.exception.code, expected)

    def capture(self):
        self.service.capture(dict(request_id='capture', raw_text='Original words',
            workspace=dict(name='Explicit', path=str(self.workspace), confirmed=True)))
        self.service.accept(self.payload('priorities', dict(urgency=6, importance=7), 'priorities', proposal_id=None))
        with self.store.transaction() as state: self.provider.original = self.provider.source(state['ideas'][self.context.selected_idea_id])

    def payload(self, step='shape', fields=None, rid='shape', proposal_id=PID):
        with self.store.transaction() as state:
            idea = state['ideas'][self.context.selected_idea_id]
            return dict(request_id=rid, idea_id=idea['idea_id'], expected_revision=idea['revision'],
                expected_draft_version=idea['workflow']['draft_version'], step=step, fields=copy.deepcopy(fields or SHAPE),
                proposal_id=proposal_id, expected_backlog_revision=None)

    def draft(self, fields=None, rid='draft'):
        value = self.payload(fields=fields, rid=rid); value.pop('proposal_id'); value.pop('expected_backlog_revision')
        return self.service.draft(value)

    def test_live_capture_before_store_and_fixed_overlay_without_completion(self):
        before = self.service.state(); self.assertEqual(before['revision'], 0)
        self.assertTrue(before['capabilities']['agent']); self.assertEqual(before['proposals'], [])
        self.assertEqual(before['agent_generation'], self.provider.live['generation'])
        self.assertEqual(before['proposal_inventory'],dict(total=0,projected=0,omitted=0,content_omitted=0,index_path=None))
        self.assertEqual(self.provider.calls, ['context', 'project'])
        self.capture(); current = self.service.state()
        self.assertEqual(current['steps']['shape']['status'], 'current')
        self.assertEqual(current['accepted']['shape'], None)
        self.assertEqual(current['proposal_sources']['shape']['source']['accepted_revision'], 2)
        current['proposal_sources']['shape']['source']['data']['capture']['raw_text'] = 'changed'
        current['proposals'][0]['proposal']['outcome'] = 'changed'
        again = self.service.state()
        self.assertEqual(again['proposal_sources']['shape']['source']['data']['capture']['raw_text'], 'Original words')
        self.assertEqual(again['proposals'][0]['proposal']['outcome'], SHAPE['outcome'])

    def test_target_autosave_then_edited_accept_durable_proposal_receipt(self):
        self.capture(); edited = dict(SHAPE, outcome='Edited by user')
        draft = self.draft(edited); self.assertEqual(draft['draft_version'], 1)
        state = self.service.state(); summary = state['proposals'][0]
        self.assertTrue(summary['stale']); self.assertTrue(summary['acceptance_eligible'])
        payload = self.payload(fields=edited); self.provider.calls.clear()
        result = self.service.accept(payload)
        self.assertEqual(result['proposal_id'], PID); self.assertEqual(result['revision'], 3)
        self.assertEqual(self.provider.calls, ['context', 'validate', 'handler'])
        state = self.service.state(); self.assertEqual(state['accepted']['shape']['outcome'], 'Edited by user')
        with self.store.transaction() as domain:
            self.assertNotIn('proposal_id', domain['ideas'][self.context.selected_idea_id]['workflow']['steps']['shape']['acceptance'])
        restarted = Service(Store(self.store.path), {}, TrustedContext('Operator', self.sid))
        self.assertEqual(restarted.accept(payload), result)
        self.provider.live = None; self.provider.calls.clear()
        self.assertEqual(self.service.accept(payload), result)
        self.assertEqual(self.provider.calls, ['context'])
        self.assertEqual(self.service.request_result(payload['request_id']), result)

    def test_consumed_change_refused_without_publication(self):
        self.capture(); capture_fields = self.service.state()['accepted']['capture']
        changed = dict(capture_fields, raw_text='Changed capture')
        self.service.accept(self.payload('capture', changed, 'capture-edit', proposal_id=None))
        payload = self.payload(rid='stale-proposal')
        self.code('stale_source', lambda: self.service.accept(payload))
        self.code('request_not_found', lambda: self.service.request_result('stale-proposal'))
        self.assertEqual(self.service.state()['revision'], 3)
        self.assertIsNone(self.service.state()['accepted']['shape'])

    def test_method_manual_acceptance_uses_provider_before_packaged_handler(self):
        self.capture(); self.service.accept(self.payload())
        payload = self.payload('method', workflow_fields()['method'], 'manual-method', proposal_id=None)
        self.provider.calls.clear()
        result = self.service.accept(payload)
        self.assertEqual(self.provider.calls, ['context', 'validate', 'handler'])
        self.assertEqual(result['revision'], 4)
        self.assertNotIn('proposal_id', result)
        self.assertEqual(self.service.state()['accepted']['method']['selection'], 'bounded-plan')

    def test_disconnected_and_initial_no_binding_keep_drafts_usable(self):
        self.provider.live = None
        initial = self.service.state(); self.assertEqual(initial['agent_status'], 'disconnected')
        self.assertIsNone(initial['agent_generation'])
        self.capture(); self.provider.live = dict(binding_id='binding_'+'b'*32, generation='agent_'+'c'*32,
            session_id=self.sid, agent_status='disconnected')
        result = self.draft(); self.assertEqual(result['draft_version'], 1)
        state = self.service.state(); self.assertFalse(state['capabilities']['agent']); self.assertFalse(state['capabilities']['memory'])
        self.code('agent_unavailable', lambda: self.service.accept(self.payload()))
        self.assertEqual(self.service.state()['drafts']['shape'], SHAPE)
        # Manual accepted fields remain the packaged handler's own decision.
        self.assertEqual(self.service.accept(self.payload(rid='manual', proposal_id=None))['revision'], 3)

    def test_overlay_rejects_arbitrary_steps_secrets_capabilities_and_bad_sources(self):
        self.capture()
        mutations = [lambda o:o.update(steps={'shape':{'status':'saved'}}), lambda o:o.update(token='secret'),
            lambda o:o['capabilities'].update(handoff=True), lambda o:o['capabilities'].update(agent=1),
            lambda o:o['resume'].update(reason='secret'),
            lambda o:o['proposal_sources']['shape']['source']['data'].update(token='secret'),
            lambda o:o['proposal_sources']['shape']['source'].update(source_digest='f'*64),
            lambda o:o['proposals'][0].update(token='secret'),
            lambda o:o['proposals'][0]['proposal'].update(token='secret'),
            lambda o:o['proposals'][0].update(acceptance_reason='secret'),
            lambda o:o['proposals'][0].update(stale_reason='secret'),
            lambda o:o['proposals'].append(copy.deepcopy(o['proposals'][0]))]
        before = (self.store.path/'IDEAS.md').read_bytes()
        for mutation in mutations:
            self.provider.overlay_change = mutation
            with self.assertRaises(IdeaError): self.service.state()
        self.provider.overlay_change = None
        self.assertEqual((self.store.path/'IDEAS.md').read_bytes(), before)
        self.assertIsNone(self.service.state()['accepted']['shape'])

    def test_inventory_omitted_body_schema_and_source_capacity_codes(self):
        self.capture()
        def omitted(overlay):
            overlay['proposals'][0].update(proposal=None,content_omitted=True,
                acceptance_eligible=False,acceptance_reason='projection_omitted')
            overlay['proposal_inventory']['content_omitted']=1
        self.provider.overlay_change=omitted
        projected=self.service.state()
        self.assertIsNone(projected['proposals'][0]['proposal'])
        self.assertEqual(projected['proposal_inventory']['content_omitted'],1)
        self.assertEqual(projected['proposal_inventory']['index_path'],self.context.selected_idea_id+'.md')
        for code in ('source_too_large','source_projection_capacity'):
            self.provider.overlay_change=lambda overlay,c=code:overlay['proposal_sources'].update(
                shape=dict(available=False,code=c,source=None))
            self.assertEqual(self.service.state()['proposal_sources']['shape']['code'],code)
        self.provider.overlay_change=None

    def test_inventory_rejects_forged_counts_witnesses_and_omission_flags(self):
        self.capture()
        mutations=[
            lambda o:o['proposal_inventory'].update(total=True),
            lambda o:o['proposal_inventory'].update(total=-1),
            lambda o:o['proposal_inventory'].update(projected=0),
            lambda o:o['proposal_inventory'].update(omitted=1),
            lambda o:o['proposal_inventory'].update(content_omitted=1),
            lambda o:o['proposal_inventory'].update(index_path='../IDEAS.md'),
            lambda o:o['proposal_inventory'].update(token='secret'),
            lambda o:o['proposals'][0]['evidence'].update(path='/private/proposal.md'),
            lambda o:o['proposals'][0]['evidence'].update(path='history/../proposal.md'),
            lambda o:o['proposals'][0]['evidence'].update(path='https://example.test/proposal.md'),
            lambda o:o['proposals'][0]['evidence'].update(path='history/idea_'+'f'*32+'/metadata/'+'b'*64+'.md'),
            lambda o:o['proposals'][0]['evidence'].update(sha256='bad'),
            lambda o:o['proposals'][0]['evidence'].update(token='secret'),
            lambda o:o['proposals'][0].update(content_omitted=1),
            lambda o:o['proposals'][0].update(content_omitted=True),
            lambda o:o['proposals'][0].update(proposal=None),
            lambda o:o['proposals'][0].update(acceptance_reason='projection_omitted'),
            lambda o:o['proposals'][0].update(proposal=None,content_omitted=True,
                acceptance_eligible=True,acceptance_reason='projection_omitted'),
        ]
        before=(self.store.path/'IDEAS.md').read_bytes()
        for mutation in mutations:
            self.provider.overlay_change=mutation
            with self.assertRaises(IdeaError):self.service.state()
        self.provider.overlay_change=None
        self.assertEqual((self.store.path/'IDEAS.md').read_bytes(),before)

    def test_generation_marker_rotation_is_nonsecret_and_cannot_be_fabricated(self):
        self.capture(); before = self.service.state()
        self.provider.live['generation'] = 'agent_'+'d'*32
        after = self.service.state()
        self.assertNotEqual(before['agent_generation'], after['agent_generation'])
        self.assertEqual(before['revision'], after['revision'])
        self.assertEqual(before['draft_version'], after['draft_version'])
        self.assertEqual(before['proposal_sources'], after['proposal_sources'])
        self.assertEqual(before['agent_status'], after['agent_status'])
        self.provider.overlay_change = lambda overlay: overlay.update(agent_generation='agent_'+'e'*32)
        self.code('invalid_agent_provider', lambda: self.service.state())
        self.provider.overlay_change = lambda overlay: overlay.update(agent_generation=None)
        self.code('invalid_agent_provider', lambda: self.service.state())
        self.provider.live = None
        self.provider.overlay_change = lambda overlay: overlay.update(agent_generation='agent_'+'e'*32)
        self.code('invalid_agent_provider', lambda: self.service.state())

    def test_provider_input_mutation_rejected_and_real_state_preserved(self):
        self.capture(); self.provider.mutate_inputs = True
        self.code('invalid_agent_provider', lambda: self.service.state())
        self.code('invalid_agent_provider', lambda: self.service.accept(self.payload()))
        self.provider.mutate_inputs = False
        self.assertEqual(self.service.state()['backlog_revision'], 1)
        self.assertEqual(self.service.state()['revision'], 2)
        self.assertIsNone(self.service.state()['accepted']['shape'])

    def test_provider_context_schema_and_default_behavior(self):
        self.provider.live = dict(self.provider.live, token='secret')
        with self.assertRaises(IdeaError): self.service.state()
        self.provider.live.pop('token'); self.provider.live['session_id'] = 'session_'+'f'*32
        self.code('invalid_agent_provider', lambda: self.service.state())
        plain = Service(self.store, {}, self.context)
        state = plain.state(); self.assertEqual(state['agent_status'], 'disconnected')
        self.assertNotIn('proposal_sources', state); self.assertNotIn('proposals', state)
        self.assertFalse(state['capabilities']['uploads']); self.assertFalse(state['capabilities']['handoff'])
        with self.assertRaises(IdeaError): Service(self.store, {}, self.context, agent_provider={})

    def test_handoff_capability_survives_agent_overlay(self):
        self.provider.live = None
        service = Service(self.store, {}, self.context, agent_provider=self.provider, handoff_provider=lambda *_:None)
        state = service.state(); self.assertTrue(state['capabilities']['handoff'])
        self.assertFalse(state['capabilities']['agent']); self.assertFalse(state['capabilities']['uploads'])


if __name__ == '__main__': unittest.main()
