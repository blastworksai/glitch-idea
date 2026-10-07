"""Assessment dispatch and immutable schema1 compatibility. Operator."""
import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
import idea_agent_source as source
import idea_proposal_evidence as codec
import idea_store as storage
from idea_assessment import assessment_digest
from idea_domain import IdeaError
from idea_service import TrustedContext
from idea_workflow import acceptance_source, save_draft, source_digest
from test_assessment import KEY, OTHER, THIRD, proposed, state_fixture
from test_workflow import ACTOR, accept, fields

LIVE = dict(binding_id='binding_'+'4'*32, generation='agent_'+'5'*32,
            session_id='session_'+'6'*32, agent_status='connected')


def domain():
    state = storage.empty_state()
    state.update(state_fixture())
    return state


def evidence(state, sid=LIVE['session_id']):
    selected = source.prepare_source(state, KEY, 'assessment')
    correlation = dict(request_id='assessment-1', session_id=sid, idea_id=KEY,
        accepted_revision=selected['accepted_revision'], draft_version=selected['draft_version'],
        operation='assessment', source_digest=assessment_digest('assessment', selected, idea_id=KEY))
    return dict(binding_id=LIVE['binding_id'], generation=LIVE['generation'],
                correlation=correlation, source=selected, proposal=proposed(selected))


def record_fixture(state=None):
    supplied = evidence(domain() if state is None else state)
    return dict(schema_version=1, kind='agent-proposal', proposal_id='proposal_'+'7'*32,
        binding_id=supplied['binding_id'], generation=supplied['generation'],
        actor=ACTOR, timestamp='2026-10-02T12:00:00Z', **supplied['correlation'],
        data=supplied['source']['data'], proposal=supplied['proposal'])


# Frozen pre-CP3 serializer fixtures. The YAML and body text below are literal
# prior schema1 formats, independent of codec.encode_proposal/_body. Only the
# operation-specific digest is substituted, checked against literal old inputs.
LEGACY_PROPOSALS = {
    'discovery': {
        'value': dict(problem='Lids are hard to clean', audience='Home cooks', workaround='Scrub by hand',
            evidence='Three complaints', kill_criteria='Nobody cleans lids',
            challenges=[dict(challenge='Is this real?', response='Reported thrice')]),
        'yaml': '  audience: Home cooks\n  challenges:\n  - challenge: Is this real?\n    response: Reported thrice\n  evidence: Three complaints\n  kill_criteria: Nobody cleans lids\n  problem: Lids are hard to clean\n  workaround: Scrub by hand\n',
        'json': '{"audience":"Home cooks","challenges":[{"challenge":"Is this real?","response":"Reported thrice"}],"evidence":"Three complaints","kill_criteria":"Nobody cleans lids","problem":"Lids are hard to clean","workaround":"Scrub by hand"}',
        'body': '    {\n      "audience": "Home cooks",\n      "challenges": [\n        {\n          "challenge": "Is this real?",\n          "response": "Reported thrice"\n        }\n      ],\n      "evidence": "Three complaints",\n      "kill_criteria": "Nobody cleans lids",\n      "problem": "Lids are hard to clean",\n      "workaround": "Scrub by hand"\n    }\n',
    },
    'exploration': {
        'value': dict(outcome='Outcome', scope='small-change', scope_reason='One lid',
            alternatives=[dict(route='Keep lid', reason='Less effort')], assumptions=[],
            next_slice='Inspect lid', learning=[], investment=None, experiment=None,
            sketch=[dict(title='Inspect lid', why_next='Cheapest test', done_when='Lid is inspected', method=None)]),
        'yaml': '  alternatives:\n  - reason: Less effort\n    route: Keep lid\n  assumptions: []\n  experiment: null\n  investment: null\n  learning: []\n  next_slice: Inspect lid\n  outcome: Outcome\n  scope: small-change\n  scope_reason: One lid\n  sketch:\n  - done_when: Lid is inspected\n    method: null\n    title: Inspect lid\n    why_next: Cheapest test\n',
        'json': '{"alternatives":[{"reason":"Less effort","route":"Keep lid"}],"assumptions":[],"experiment":null,"investment":null,"learning":[],"next_slice":"Inspect lid","outcome":"Outcome","scope":"small-change","scope_reason":"One lid","sketch":[{"done_when":"Lid is inspected","method":null,"title":"Inspect lid","why_next":"Cheapest test"}]}',
        'body': '    {\n      "alternatives": [\n        {\n          "reason": "Less effort",\n          "route": "Keep lid"\n        }\n      ],\n      "assumptions": [],\n      "experiment": null,\n      "investment": null,\n      "learning": [],\n      "next_slice": "Inspect lid",\n      "outcome": "Outcome",\n      "scope": "small-change",\n      "scope_reason": "One lid",\n      "sketch": [\n        {\n          "done_when": "Lid is inspected",\n          "method": null,\n          "title": "Inspect lid",\n          "why_next": "Cheapest test"\n        }\n      ]\n    }\n',
    },
    'memory': {
        'value': dict(status='unavailable', sources=[], rationale=None),
        'yaml': '  rationale: null\n  sources: []\n  status: unavailable\n',
        'json': '{"rationale":null,"sources":[],"status":"unavailable"}',
        'body': '    {\n      "rationale": null,\n      "sources": [],\n      "status": "unavailable"\n    }\n',
    },
    'method': {
        'value': dict(memory=dict(status='unavailable', sources=[], rationale=None)),
        'yaml': '  memory:\n    rationale: null\n    sources: []\n    status: unavailable\n',
        'json': '{"memory":{"rationale":null,"sources":[],"status":"unavailable"}}',
        'body': '    {\n      "memory": {\n        "rationale": null,\n        "sources": [],\n        "status": "unavailable"\n      }\n    }\n',
    },
}


class AssessmentSourceTests(unittest.TestCase):
    def assert_code(self, code, callback):
        with self.assertRaises(IdeaError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_assessment_prepare_projection_digest_and_response_dispatch(self):
        state = domain(); before = copy.deepcopy(state)
        supplied = evidence(state)
        selected = supplied['source']
        self.assertEqual(source.proposal_source_digest('assessment', selected, idea_id=KEY),
                         supplied['correlation']['source_digest'])
        self.assertEqual(source.validate_current(state, supplied), selected)
        projected = source.project_sources(state, KEY)
        self.assertEqual(set(projected), {'discovery', 'exploration', 'memory', 'method', 'visual_brief', 'assessment'})
        self.assertEqual(projected['assessment'], dict(available=True, code='ok',
            source=dict(selected, source_digest=supplied['correlation']['source_digest'])))
        projected['assessment']['source']['data']['target'] = {}
        self.assertEqual(state, before)

    def test_old_operation_sources_and_digests_remain_exact(self):
        state = domain()
        for operation, keys in (('discovery', {'capture', 'priorities', 'method', 'discovery'}),
                                ('exploration', {'method', 'discovery', 'exploration'}),
                                ('memory', {'capture', 'priorities'}),
                                ('method', {'capture', 'priorities', 'method'}),
                                ('visual_brief', {'capture', 'discovery', 'exploration'})):
            selected = source.prepare_source(state, KEY, operation)
            self.assertEqual(set(selected['data']), keys)
            expected = source_digest(operation, selected['accepted_revision'], selected['data'])
            self.assertEqual(source.proposal_source_digest(operation, selected), expected)
        for operation in ('position', 'unknown'):
            self.assert_code('operation_unavailable', lambda: source.prepare_source(state, KEY, operation))

    def test_schema1_old_canonical_json_markdown_paths_and_byte_hashes_pinned(self):
        for operation, fixture in LEGACY_PROPOSALS.items():
            with self.subTest(operation=operation):
                old_input = ('{"fields":{"capture":{"raw_text":"Fixture"}},"operation":"'+operation+'","source_revision":2}').encode()
                digest = hashlib.sha256(old_input).hexdigest()
                record = dict(schema_version=1, kind='agent-proposal',
                    proposal_id='proposal_'+'1'*32, binding_id='binding_'+'2'*32,
                    generation='agent_'+'3'*32, actor='Operator', timestamp='fixture-time',
                    request_id='fixture-request', session_id='session_'+'4'*32,
                    idea_id='idea_'+'5'*32, accepted_revision=2, draft_version=3,
                    operation=operation, source_digest=digest,
                    data={'capture': {'raw_text': 'Fixture'}}, proposal=fixture['value'])
                canonical = ('{"accepted_revision":2,"actor":"Operator","binding_id":"binding_'+ '2'*32+'",'
                    '"data":{"capture":{"raw_text":"Fixture"}},"draft_version":3,"generation":"agent_'+'3'*32+'",'
                    '"idea_id":"idea_'+'5'*32+'","kind":"agent-proposal","operation":"'+operation+'",'
                    '"proposal":'+fixture['json']+',"proposal_id":"proposal_'+'1'*32+'",'
                    '"request_id":"fixture-request","schema_version":1,"session_id":"session_'+'4'*32+'",'
                    '"source_digest":"'+digest+'","timestamp":"fixture-time"}').encode()
                raw = ('---\naccepted_revision: 2\nactor: Operator\nbinding_id: binding_'+'2'*32+'\n'
                    'data:\n  capture:\n    raw_text: Fixture\ndraft_version: 3\ngeneration: agent_'+'3'*32+'\n'
                    'idea_id: idea_'+'5'*32+'\nkind: agent-proposal\noperation: '+operation+'\nproposal:\n'+fixture['yaml']+
                    'proposal_id: proposal_'+'1'*32+'\nrequest_id: fixture-request\nschema_version: 1\nsession_id: session_'+'4'*32+'\n'
                    'source_digest: '+digest+'\ntimestamp: fixture-time\n---\n'
                    '# Immutable agent proposal\n\nDo not edit this evidence. A suggestion is not an accepted decision.\n\n'
                    '## Proposed fields\n\n'+fixture['body']+'\n## Recorded source inputs\n\n'
                    '    {\n      "capture": {\n        "raw_text": "Fixture"\n      }\n    }\n').encode()
                self.assertEqual(codec.canonical_record(record), canonical)
                self.assertEqual(codec.encode_proposal(record), raw)
                expected_path = 'history/'+record['idea_id']+'/metadata/'+hashlib.sha256(canonical).hexdigest()+'.md'
                self.assertEqual(codec.proposal_path(record), expected_path)
                link = dict(proposal_id=record['proposal_id'], path=expected_path, sha256=hashlib.sha256(raw).hexdigest())
                self.assertEqual(codec.proposal_link(record), link)
                self.assertEqual(codec.decode_proposal(raw, path=expected_path, link=link), record)

    def test_schema1_shape_record_is_refused_never_decoded(self):
        # Owner ruling: old ideas were demo only, so a schema-1 shape proposal is not migrated.
        digest = hashlib.sha256(b'{"fields":{"capture":{"raw_text":"Fixture"}},"operation":"shape","source_revision":2}').hexdigest()
        record = dict(schema_version=1, kind='agent-proposal', proposal_id='proposal_'+'1'*32,
            binding_id='binding_'+'2'*32, generation='agent_'+'3'*32, actor='Operator', timestamp='fixture-time',
            request_id='fixture-request', session_id='session_'+'4'*32, idea_id='idea_'+'5'*32,
            accepted_revision=2, draft_version=3, operation='shape', source_digest=digest,
            data={'capture': {'raw_text': 'Fixture'}},
            proposal=dict(outcome='Outcome', scope='small-change', scope_reason='One lid',
                alternatives=[dict(route='Keep lid', reason='Less effort')], assumptions=[],
                next_slice='Inspect lid', learning=[]))
        self.assert_code('unsupported_proposal_version', lambda: codec.validate_record(record))
        self.assert_code('unsupported_proposal_version', lambda: codec.encode_proposal(record))
        raw = ('---\naccepted_revision: 2\nactor: Operator\nbinding_id: binding_'+'2'*32+'\n'
            'data:\n  capture:\n    raw_text: Fixture\ndraft_version: 3\ngeneration: agent_'+'3'*32+'\n'
            'idea_id: idea_'+'5'*32+'\nkind: agent-proposal\noperation: shape\nproposal:\n'
            '  alternatives:\n  - reason: Less effort\n    route: Keep lid\n  assumptions: []\n  learning: []\n'
            '  next_slice: Inspect lid\n  outcome: Outcome\n  scope: small-change\n  scope_reason: One lid\n'
            'proposal_id: proposal_'+'1'*32+'\nrequest_id: fixture-request\nschema_version: 1\nsession_id: session_'+'4'*32+'\n'
            'source_digest: '+digest+'\ntimestamp: fixture-time\n---\n# Immutable agent proposal\n').encode()
        self.assert_code('unsupported_proposal_version', lambda: codec.decode_proposal(raw))

    def test_assessment_codec_roundtrip_keeps_schema_and_checks_recorded_target(self):
        record = record_fixture(); before = copy.deepcopy(record)
        raw = codec.encode_proposal(record); link = codec.proposal_link(record, raw)
        self.assertTrue(raw.startswith(b'---\n'))
        self.assertEqual(codec.decode_proposal(raw, path=link['path'], link=link, expected_idea_id=KEY), record)
        self.assertEqual(record['schema_version'], 1)
        wrong = copy.deepcopy(record); wrong['idea_id'] = OTHER
        self.assertRaises(IdeaError, codec.validate_record, wrong)
        wrong = copy.deepcopy(record); wrong['proposal']['position']['neighbors']['before'] = THIRD
        self.assertRaises(IdeaError, codec.validate_record, wrong)
        self.assertEqual(record, before)

    def test_codec_specialized_source_rejects_extra_fields_and_tampered_digest(self):
        for location in ('top', 'steps', 'backlog', 'comparison', 'target'):
            record = record_fixture()
            if location == 'top': record['data']['token'] = 'rejected'
            elif location == 'steps': record['data']['steps']['method'] = fields()['method']
            elif location == 'backlog': record['data']['backlog']['rank'] = 1
            elif location == 'comparison': record['data']['backlog']['comparisons'][0]['score'] = 99
            else: record['data']['target']['score'] = 99
            with self.subTest(location=location), self.assertRaises(IdeaError):
                codec.validate_record(record)
        record = record_fixture(); record['source_digest'] = '0'*64
        self.assert_code('stale_source', lambda: codec.validate_record(record))
        record = record_fixture(); record['data']['backlog']['revision'] += 1
        self.assert_code('stale_source', lambda: codec.validate_record(record))

    def test_response_cas_includes_target_draft_and_comparison_not_only_order(self):
        state = domain(); supplied = evidence(state)
        changed = copy.deepcopy(state)
        idea = changed['ideas'][KEY]
        changed['ideas'][KEY] = save_draft(idea, 'assess', {'assessment': {'method': 'rice'}},
            expected_revision=idea['revision'], expected_draft_version=0)['idea']
        self.assert_code('stale_source', lambda: source.validate_current(changed, supplied))
        changed = copy.deepcopy(state); changed['backlog_revision'] += 1
        self.assert_code('stale_source', lambda: source.validate_current(changed, supplied))
        changed = copy.deepcopy(state); changed['order'].reverse()
        self.assert_code('stale_source', lambda: source.validate_current(changed, supplied))
        changed = copy.deepcopy(state); changed['ideas'][OTHER]['revision'] += 1
        self.assert_code('stale_source', lambda: source.validate_current(changed, supplied))
        changed = copy.deepcopy(supplied); changed['correlation']['draft_version'] += 1
        self.assert_code('stale_source', lambda: source.validate_current(state, changed))

    def test_agent_neighbors_and_unknown_scores_are_checked_at_publication(self):
        state = domain(); supplied = evidence(state)
        wrong = copy.deepcopy(supplied); wrong['proposal']['position']['neighbors']['before'] = THIRD
        self.assert_code('invalid_proposal', lambda: source.validate_current(state, wrong))
        for method, inputs in (('rice', dict(reach=None, impact=2, confidence=0.5, effort=1)),
                               ('kano', dict(category='delighter', hypothesis=True))):
            changed = copy.deepcopy(supplied)
            changed['proposal']['assessment'].update(method=method, inputs=inputs)
            self.assertEqual(source.validate_current(state, changed), supplied['source'])
        wrong = copy.deepcopy(supplied); wrong['proposal']['assessment']['score'] = 9
        self.assert_code('invalid_proposal', lambda: source.validate_current(state, wrong))

    def test_assessment_capacity_is_explicit_without_silent_comparison_omissions(self):
        state = domain()
        with patch.object(source, 'MAX_SOURCE_PROJECTION_BYTES', 64):
            projection = source.project_sources(state, KEY)
        self.assertEqual(projection['assessment'], dict(available=False, code='source_projection_capacity', source=None))
        large = copy.deepcopy(state)
        changed = fields()['capture']; changed['raw_text'] = 'x'*source.MAX_INPUT
        idea = accept(large['ideas'][KEY], 'capture', changed)['idea']
        for step in ('method', 'discovery', 'exploration'):
            idea = accept(idea, step)['idea']
        large['ideas'][KEY] = idea
        self.assertEqual(source.project_sources(large, KEY)['assessment'],
                         dict(available=False, code='source_too_large', source=None))


class AssessmentAdapterTests(unittest.TestCase):
    assert_code = AssessmentSourceTests.assert_code

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = storage.Store(Path(self.temp.name)/'ideas', observer=ACTOR)
        self.sid = self.store.create_session()
        self.live = dict(LIVE, session_id=self.sid)
        self.context = TrustedContext(ACTOR, self.sid, KEY)
        self.adapter = source.SourceAdapter(self.store)
        with self.store.transaction(write=True) as state:
            state.update(domain()); self.store.commit(state)
        with self.store.transaction(write=True) as state:
            self.supplied = evidence(state, self.sid)
            self.link = self.store.persist_agent_proposal(state, self.supplied,
                actor=ACTOR, validate_current=source.validate_current)

    def payload(self, idea, value=None):
        return dict(request_id='assessment-accept-1', idea_id=KEY, step='assess',
            fields=copy.deepcopy(self.supplied['proposal']) if value is None else value,
            expected_revision=idea['revision'], expected_draft_version=idea['workflow']['draft_version'],
            proposal_id=self.link['proposal_id'], expected_backlog_revision=8)

    def validate(self, state, payload, live=None, context=None):
        return self.adapter.validate_acceptance(state, state['ideas'][KEY], payload,
            acceptance_source(state['ideas'][KEY], 'assess', payload['fields']),
            self.context if context is None else context,
            live_binding=self.live if live is None else live)

    def test_persisted_inventory_and_acceptance_are_memory_only_and_detached(self):
        with self.store.transaction() as state:
            before = copy.deepcopy(state)
            with patch.object(storage, 'read_bytes', side_effect=AssertionError('unexpected IO')), \
                 patch.object(self.store, 'transaction', side_effect=AssertionError('nested transaction')):
                projected = self.adapter.project_projection(state, KEY, self.live)
                checked = self.validate(state, self.payload(state['ideas'][KEY]))
            self.assertEqual(checked['proposal_id'], self.link['proposal_id'])
            self.assertEqual(projected['proposals'][0]['operation'], 'assessment')
            self.assertTrue(projected['proposals'][0]['acceptance_eligible'])
            projected['proposals'][0]['proposal']['assessment']['basis'] = 'Detached'
            self.assertEqual(state, before)
            self.assertNotEqual(self.store.agent_proposals(state, KEY)[0]['proposal']['assessment']['basis'], 'Detached')

    def test_target_autosave_and_human_score_edits_actual_override_remain_eligible(self):
        human = copy.deepcopy(self.supplied['proposal'])
        human['assessment']['inputs']['time_criticality'] = 4
        human['assessment']['basis'] = 'Human revised evidence'
        human['position'].update(actual_position=1, neighbors=dict(before=None, after=OTHER),
                                 override_reason='Operator chooses next slice first')
        with self.store.transaction(write=True) as state:
            idea = state['ideas'][KEY]
            state['ideas'][KEY] = save_draft(idea, 'assess', human,
                expected_revision=idea['revision'], expected_draft_version=0)['idea']
            self.store.commit(state)
        with self.store.transaction() as state:
            summary = self.adapter.project_proposals(state, KEY, self.live)[0]
            self.assertTrue(summary['stale']); self.assertEqual(summary['stale_reason'], 'stale_source')
            self.assertTrue(summary['acceptance_eligible'])
            checked = self.validate(state, self.payload(state['ideas'][KEY], human))
            self.assertEqual(checked['proposal'], self.supplied['proposal'])
            self.assertEqual(state['ideas'][KEY]['ratings']['urgency'], 7)
            self.assertIsNone(state['ideas'][KEY]['assessments'][-1]['score'])

    def test_original_position_and_actual_neighbors_cannot_be_forged(self):
        with self.store.transaction() as state:
            value = copy.deepcopy(self.supplied['proposal'])
            value['position']['proposed_position'] = 3
            value['position']['override_reason'] = 'Human'
            self.assert_code('proposal_mismatch', lambda: self.validate(state, self.payload(state['ideas'][KEY], value)))
            value = copy.deepcopy(self.supplied['proposal'])
            value['position']['neighbors']['before'] = None
            self.assert_code('stale_backlog', lambda: self.validate(state, self.payload(state['ideas'][KEY], value)))
            payload = self.payload(state['ideas'][KEY]); payload['expected_backlog_revision'] -= 1
            self.assert_code('stale_backlog', lambda: self.validate(state, payload))

    def test_other_idea_comparison_change_blocks_acceptance_without_order_counter_change(self):
        with self.store.transaction(write=True) as state:
            state['ideas'][OTHER] = accept(state['ideas'][OTHER], 'capture')['idea']
            state['ideas'][OTHER] = accept(state['ideas'][OTHER], 'priorities')['idea']
            self.store.commit(state)
        with self.store.transaction() as state:
            self.assertEqual(state['backlog_revision'], 8)
            summary = self.adapter.project_proposals(state, KEY, self.live)[0]
            self.assertEqual(summary['acceptance_reason'], 'stale_source')
            self.assert_code('stale_source', lambda: self.validate(state, self.payload(state['ideas'][KEY])))

    def test_consumed_draft_generation_actor_and_unknown_proposal_refusals(self):
        with self.store.transaction() as state:
            payload = self.payload(state['ideas'][KEY])
            for live, code in ((dict(self.live, generation='agent_'+'8'*32), 'wrong_generation'),
                               (dict(self.live, agent_status='disconnected'), 'agent_unavailable'),
                               (dict(self.live, session_id='session_'+'9'*32), 'wrong_session')):
                self.assert_code(code, lambda: self.validate(state, payload, live))
            unknown = dict(payload, proposal_id='proposal_'+'a'*32)
            self.assert_code('proposal_not_found', lambda: self.validate(state, unknown))
            self.assert_code('proposal_mismatch', lambda: self.validate(state, payload,
                context=TrustedContext('other-actor', self.sid, KEY)))
            changed = copy.deepcopy(state)
            idea = changed['ideas'][KEY]
            changed['ideas'][KEY] = save_draft(idea, 'priorities', {'urgency': 9, 'importance': 8},
                expected_revision=idea['revision'], expected_draft_version=0)['idea']
            self.assert_code('stale_source', lambda: source.validate_current(changed, self.supplied))

    def test_durable_reply_replay_after_source_drift_returns_same_evidence_without_revision(self):
        with self.store.transaction(write=True) as state:
            idea = state['ideas'][KEY]
            state['ideas'][KEY] = save_draft(idea, 'assess', {},
                expected_revision=idea['revision'], expected_draft_version=0)['idea']
            self.store.commit(state)
        with self.store.transaction(write=True) as state:
            before = copy.deepcopy(state)
            with patch.object(source, 'validate_current', side_effect=AssertionError('replay must precede source validation')):
                repeated = self.store.persist_agent_proposal(state, self.supplied,
                    actor=ACTOR, validate_current=source.validate_current)
            self.assertEqual(repeated, self.link)
            self.assertEqual(state, before)
            self.assertEqual(len(self.store.agent_proposals(state, KEY)), 1)
        raw = (self.store.path/self.link['path']).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), self.link['sha256'])

    def test_bounded_projection_keeps_history_links_and_explicit_omission(self):
        with self.store.transaction() as state:
            with patch.object(source, 'MAX_PROJECTIONS', 0):
                projected = self.adapter.project_projection(state, KEY, self.live)
            self.assertEqual(projected['proposals'], [])
            self.assertEqual(projected['proposal_inventory'], dict(total=1, projected=0, omitted=1,
                content_omitted=0, index_path=KEY+'.md'))
            self.assertEqual(self.store.agent_proposals(state, KEY)[0]['proposal_id'], self.link['proposal_id'])


if __name__ == '__main__':
    unittest.main()
