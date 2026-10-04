"""bounded broker protocol tests, no HTTP/product claims."""
import copy
from pathlib import Path
import sys
import threading
import time
import unittest
import unittest.mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'glitch-idea/scripts'))
from idea_domain import IdeaError
from idea_proposals import ANSWER_GRACE, Broker, LIMIT, MAX_BINDINGS, MAX_RECORDS, ROUTES
from idea_workflow import source_digest

BIND = 'binding_'+'1'*32
SID = 'session_'+'2'*32
IDEA = 'idea_'+'3'*32
SHAPE = dict(outcome='Clean lid', scope='small-change', scope_reason='One lid',
             alternatives=[dict(route='Clean existing lid', reason='Less work')],
             assumptions=[], next_slice='Check lid', learning=[])


class Clock:
    def __init__(self): self.value = 0
    def __call__(self): return self.value
    def advance(self, seconds): self.value += seconds


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.source = dict(accepted_revision=2, draft_version=3,
                           data={'capture': {'raw_text': 'Original words'}})
        self.saved = []
        self.broker = Broker(clock=self.clock, validate_source=self.validate,
                             persist_proposal=self.persist)
        self.broker.open(BIND, 'gen1', SID)

    def validate(self, correlation, supplied):
        return copy.deepcopy(self.source)

    def persist(self, evidence):
        self.saved.append(copy.deepcopy(evidence))
        return dict(proposal_id='proposal_fixture', sha256='a'*64)

    def envelope(self, key='request1'):
        return dict(request_id=key, idea_id=IDEA, expected_revision=2,
                    expected_draft_version=3, operation='shape',
                    source_digest=source_digest('shape', 2, self.source['data']))

    def enqueue(self, key='request1'):
        return self.broker.enqueue(BIND, 'gen1', self.envelope(key), self.source)

    def reply(self):
        event = self.broker.events(BIND, 'gen1', SID, 0, 0)['events'][0]
        return dict({k:v for k,v in event.items() if k not in ('sequence','data')}, proposal=copy.deepcopy(SHAPE))

    def code(self, code, fn):
        with self.assertRaises(IdeaError) as caught: fn()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn('Original words', str(caught.exception))

    def test_no_routes_no_accept_and_detached_roundtrip(self):
        self.assertEqual(ROUTES, ())
        self.assertFalse(hasattr(self.broker, 'accept'))
        expected = self.enqueue()
        expected['status'] = 'tampered'
        events = self.broker.events(BIND, 'gen1', SID, 0, 0)
        events['events'][0]['data']['capture']['raw_text']='tampered'
        self.assertEqual(self.reply()['accepted_revision'], 2)
        response=self.reply()
        result=self.broker.respond(BIND, 'gen1', response)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.saved[0]['source']['data']['capture']['raw_text'], 'Original words')
        response['proposal']['outcome']='tampered'
        self.assertEqual(self.saved[0]['proposal']['outcome'], 'Clean lid')
        self.assertEqual(self.broker.events(BIND, 'gen1', SID, 0, 0)['events'], [])

    def test_exact_correlation_types_and_fields(self):
        self.enqueue()
        for key,value in [('session_id','session_'+'4'*32),('idea_id','idea_'+'4'*32),
                          ('accepted_revision',True),('draft_version',2),('operation','method'),
                          ('source_digest','b'*64),('request_id','unknown')]:
            response=self.reply(); response[key]=value
            self.code('invalid_input' if key=='accepted_revision' else
                      'request_not_found' if key=='request_id' else 'response_mismatch',
                      lambda:self.broker.respond(BIND,'gen1',response))
        response=self.reply(); response['unexpected']='private'
        self.code('invalid_input', lambda:self.broker.respond(BIND,'gen1',response))
        self.assertEqual(self.saved, [])

    def test_request_and_response_replay_conflicts(self):
        pending=self.enqueue(); self.assertEqual(self.enqueue(),pending)
        changed=self.envelope(); changed['operation']='method'
        changed['source_digest']=source_digest('method',2,self.source['data'])
        self.code('request_id_conflict', lambda:self.broker.enqueue(BIND,'gen1',changed,self.source))
        response=self.reply(); first=self.broker.respond(BIND,'gen1',response)
        first['proposal']['outcome']='detached'
        self.assertEqual(self.broker.respond(BIND,'gen1',response)['proposal']['outcome'],'Clean lid')
        self.assertEqual(len(self.saved),1)
        response['proposal']['outcome']='changed'
        self.code('response_conflict',lambda:self.broker.respond(BIND,'gen1',response))

    def test_stale_response_terminally_refused(self):
        self.enqueue(); response=self.reply()
        self.source['data']['capture']['raw_text']='Changed external source'
        self.code('stale_source',lambda:self.broker.respond(BIND,'gen1',response))
        self.assertEqual(self.saved,[])
        self.code('request_cancelled',lambda:self.broker.respond(BIND,'gen1',response))

    def test_completed_replay_returns_recorded_result_after_source_drift(self):
        self.enqueue(); response=self.reply(); result=self.broker.respond(BIND,'gen1',response)
        charged=self.broker.bindings[BIND]['bytes']
        self.source['draft_version']+=1
        self.assertEqual(self.broker.respond(BIND,'gen1',response),result)
        self.assertEqual(len(self.saved),1)
        self.assertEqual(self.broker.bindings[BIND]['bytes'],charged)
        changed=copy.deepcopy(response);changed['proposal']['outcome']='changed'
        self.code('response_conflict',lambda:self.broker.respond(BIND,'gen1',changed))
        self.broker.close(BIND,'gen1')
        self.code('agent_unavailable',lambda:self.broker.respond(BIND,'gen1',response))
        self.broker.open(BIND,'gen2',SID)
        self.code('wrong_generation',lambda:self.broker.respond(BIND,'gen1',response))

    def test_four_stale_responses_release_reservation_then_fifth_admitted(self):
        for index in range(4):
            self.enqueue('stale'+str(index));response=self.reply()
            charged=self.broker.bindings[BIND]['bytes']
            self.source['data']['capture']['raw_text']='Changed '+str(index)
            self.code('stale_source',lambda:self.broker.respond(BIND,'gen1',response))
            entry=self.broker.bindings[BIND]
            self.assertEqual(entry['bytes'],charged-LIMIT//4)
            self.assertEqual(len(entry['requests']),index+1)
            self.code('request_cancelled',lambda:self.broker.respond(BIND,'gen1',response))
            self.assertEqual(entry['bytes'],charged-LIMIT//4)
        self.assertEqual(self.enqueue('fifth')['status'],'pending')
        self.assertEqual(self.saved,[])

    def test_close_rebind_and_same_generation_cannot_resurrect(self):
        self.enqueue(); response=self.reply()
        self.broker.close(BIND,'gen1')
        self.assertEqual(self.broker.open(BIND,'gen1',SID)['agent_status'],'disconnected')
        self.code('agent_unavailable',lambda:self.broker.respond(BIND,'gen1',response))
        self.broker.open(BIND,'gen2',SID)
        self.code('wrong_generation',lambda:self.broker.respond(BIND,'gen1',response))
        self.code('generation_reused',lambda:self.broker.open(BIND,'gen1',SID))
        self.assertEqual(self.broker.events(BIND,'gen2',SID,0,0)['events'],[])

    def test_fixed_idle_empty_waits_and_heartbeat_expiry(self):
        for _ in range(26):  # 26 x 24 s = 624 s > IDLE (600)
            self.clock.advance(24)
            status=self.broker.events(BIND,'gen1',SID,0,0) if self.clock.value<600 else self.broker.status(BIND,'gen1')
        self.assertEqual(status['agent_status'],'paused')
        self.assertEqual(self.broker.open(BIND,'gen1',SID)['agent_status'],'paused')
        self.broker.open(BIND,'gen2',SID)
        self.clock.advance(35)
        self.assertEqual(self.broker.status(BIND,'gen2')['agent_status'],'disconnected')

    def test_browser_write_keeps_an_active_human_from_idling_the_agent_out(self):
        # The agent keeps waiting (heartbeat fresh) while the human fills the page for
        # well over the idle window; each browser write moves only the idle clock.
        for _ in range(30):  # 30 x 24 s = 720 s > IDLE (600)
            self.clock.advance(24)
            self.assertTrue(self.broker.touch(BIND, 'gen1'))
            self.assertEqual(self.broker.events(BIND, 'gen1', SID, 0, 0)['agent_status'], 'connected')
        # The human walks away: pure polling still pauses the agent at IDLE (the cost guard).
        for _ in range(26):
            self.clock.advance(24)
            status = self.broker.events(BIND, 'gen1', SID, 0, 0) if self.clock.value - 720 < 600 else self.broker.status(BIND, 'gen1')
        self.assertEqual(status['agent_status'], 'paused')

    def test_browser_write_never_revives_or_crosses_generations(self):
        self.assertFalse(self.broker.touch(BIND, 'other-generation'))
        self.assertFalse(self.broker.touch('binding_' + 'f' * 32, 'gen1'))
        self.clock.advance(600)
        self.assertFalse(self.broker.touch(BIND, 'gen1'))          # idled out: stays paused
        self.assertEqual(self.broker.status(BIND, 'gen1')['agent_status'], 'paused')
        self.broker.open(BIND, 'gen2', SID)
        self.clock.advance(35)
        self.assertFalse(self.broker.touch(BIND, 'gen2'))          # heartbeat expired: stays disconnected
        self.assertEqual(self.broker.status(BIND, 'gen2')['agent_status'], 'disconnected')

    def test_browser_write_does_not_renew_the_heartbeat(self):
        self.clock.advance(34); self.broker.touch(BIND, 'gen1')
        self.clock.advance(1)
        self.assertEqual(self.broker.status(BIND, 'gen1')['agent_status'], 'disconnected')

    def test_successful_wait_completion_renews_only_heartbeat(self):
        # Real condition wake; fake monotonic advances while waiting.
        started=threading.Event(); result=[]
        original=self.broker.condition.wait
        def wait(timeout): started.set(); return original(timeout)
        self.broker.condition.wait=wait
        thread=threading.Thread(target=lambda:result.append(self.broker.events(BIND,'gen1',SID,0,25)))
        thread.start(); self.assertTrue(started.wait(1))
        self.clock.advance(25)
        with self.broker.condition: self.broker.condition.notify_all()
        thread.join(2); self.assertFalse(thread.is_alive())
        self.clock.advance(34)
        self.assertEqual(self.broker.status(BIND,'gen1')['agent_status'],'connected')
        self.clock.advance(1)
        self.assertEqual(self.broker.status(BIND,'gen1')['agent_status'],'disconnected')

    def test_answer_grace_keeps_a_composing_agent_past_the_heartbeat(self):
        # Ruling "Grace while answering": a picked-up request gets a bounded answer window.
        self.enqueue(); response=self.reply()
        self.clock.advance(40)                      # > HEARTBEAT (35), a model still composing
        self.assertEqual(self.broker.status(BIND,'gen1')['agent_status'],'connected')
        self.assertEqual(self.broker.respond(BIND,'gen1',response)['status'],'completed')

    def test_answer_grace_is_bounded_then_drops(self):
        self.enqueue(); response=self.reply()
        self.clock.advance(ANSWER_GRACE-1)
        self.assertEqual(self.broker.status(BIND,'gen1')['agent_status'],'connected')
        self.clock.advance(1)                       # window closed, heartbeat long stale
        self.assertNotEqual(self.broker.status(BIND,'gen1')['agent_status'],'connected')
        self.code('agent_unavailable',lambda:self.broker.respond(BIND,'gen1',response))
        self.assertEqual(self.saved,[])

    def test_answer_grace_needs_a_delivered_request(self):
        # Enqueued but never picked up: the plain heartbeat still applies.
        self.enqueue()
        self.clock.advance(35)
        self.assertEqual(self.broker.status(BIND,'gen1')['agent_status'],'disconnected')

    def test_answer_grace_opens_once_and_a_re_poll_never_extends_it(self):
        self.enqueue(); response=self.reply()       # window opens at t=0
        self.clock.advance(200)
        self.assertEqual(len(self.broker.events(BIND,'gen1',SID,0,0)['events']),1)  # re-delivered at t=200
        self.clock.advance(ANSWER_GRACE-200)        # window closed, heartbeat long stale
        self.assertNotEqual(self.broker.status(BIND,'gen1')['agent_status'],'connected')
        self.code('agent_unavailable',lambda:self.broker.respond(BIND,'gen1',response))

    def test_answering_ends_the_grace(self):
        self.enqueue(); response=self.reply()
        self.clock.advance(40)
        self.broker.respond(BIND,'gen1',response)  # completed at t=40: heartbeat renewed, no window left
        self.clock.advance(34)
        self.assertEqual(self.broker.status(BIND,'gen1')['agent_status'],'connected')
        self.clock.advance(1)
        self.assertEqual(self.broker.status(BIND,'gen1')['agent_status'],'disconnected')

    def test_cancel_ends_the_grace(self):
        self.enqueue(); response=self.reply()
        self.clock.advance(40)
        self.broker.cancel(BIND,'gen1')
        self.assertEqual(self.broker.status(BIND,'gen1')['agent_status'],'disconnected')
        self.code('agent_unavailable',lambda:self.broker.respond(BIND,'gen1',response))

    def test_slow_persist_inside_the_window_is_not_uncertain(self):
        # The demo's committed_uncertain: the lease expired while the reply was being persisted.
        self.enqueue(); response=self.reply()
        self.clock.advance(30)
        def slow(evidence):
            self.clock.advance(10)                  # persist ends at t=40, past the plain heartbeat
            return self.persist(evidence)
        self.broker.persist_proposal=slow
        result=self.broker.respond(BIND,'gen1',response)
        self.assertEqual((result['status'],result['write_state']),('completed','applied'))

    def test_a_reply_at_the_end_of_the_window_covers_its_persist(self):
        self.enqueue(); response=self.reply()
        self.clock.advance(ANSWER_GRACE-1)
        def slow(evidence):
            self.clock.advance(2)                   # persist ends after the window (and the idle mark)
            return self.persist(evidence)
        self.broker.persist_proposal=slow
        result=self.broker.respond(BIND,'gen1',response)
        self.assertEqual((result['status'],result['write_state']),('completed','applied'))

    def test_failed_persist_stays_retryable_inside_the_window(self):
        self.enqueue(); response=self.reply()
        def fail(_): raise RuntimeError('disk')
        self.broker.persist_proposal=fail
        self.clock.advance(100)
        self.code('proposal_commit_uncertain',lambda:self.broker.respond(BIND,'gen1',response))
        self.broker.persist_proposal=self.persist
        self.clock.advance(100)                     # t=200: heartbeat 100 s old, window open
        self.assertEqual(self.broker.respond(BIND,'gen1',response)['status'],'completed')

    # --- conversation fills (redesign R1a) ---
    def fill(self, fields, **change):
        if not getattr(self, 'event', None):
            self.event = self.broker.events(BIND,'gen1',SID,0,0)['events'][0]
        payload = dict({k:v for k,v in self.event.items() if k not in ('sequence','data')}, fields=fields)
        payload.update(change)
        return self.broker.fill(BIND,'gen1',payload)

    def test_fill_records_agreed_fields_for_the_page_without_closing_the_request(self):
        self.enqueue()
        first = self.fill(dict(outcome='Agreed result'))
        self.assertEqual((first['fill_sequence'], first['status'], first['write_state']), (1, 'pending', 'not_applied'))
        self.fill(dict(alternatives=[dict(route='Simpler route', reason='Less work')], assumptions=['Risk one']))
        talk = self.broker.conversation(BIND,'gen1')
        self.assertEqual([f['sequence'] for f in talk['fills']], [1, 2])
        self.assertEqual(talk['fills'][0]['fields'], dict(outcome='Agreed result'))
        self.assertEqual((talk['operation'], talk['idea_id'], talk['accepted_revision']), ('shape', IDEA, 2))
        self.assertEqual(self.saved, [], 'a fill is never a persisted proposal or a draft write')
        self.assertEqual(self.reply()['request_id'], 'request1')  # still open: a full reply may follow
        self.assertEqual(self.broker.respond(BIND,'gen1',self.reply())['status'], 'completed')
        self.assertIsNone(self.broker.conversation(BIND,'gen1'))
        self.code('request_closed', lambda: self.fill(dict(outcome='Too late')))

    def test_fill_refuses_fields_the_human_owns_and_malformed_values(self):
        self.enqueue()
        for fields in ({}, {'outcome': None}, {'outcome': 7}, {'scope': 'galaxy'}, {'selection': 'bounded-plan'},
                       {'alternatives': 'not a list'}, {'unknown': 'x'}):
            with self.subTest(fields=fields):
                self.code('invalid_fill', lambda: self.fill(fields))
        self.assertEqual(self.broker.conversation(BIND,'gen1')['fills'], [])

    def test_fill_keeps_method_selection_budget_memory_and_actual_position_human(self):
        source = dict(accepted_revision=2, draft_version=3, data={'capture': {'raw_text': 'x'}})
        from idea_proposals import _default_fill
        self.assertEqual(_default_fill('method', {'reason': 'Agreed reason'}), {'reason': 'Agreed reason'})
        for fields in ({'selection': 'bounded-plan'}, {'investment': {'cap': 4, 'unit': 'hours', 'boundary': 'x'}},
                       {'memory': {'status': 'found', 'sources': ['decision:x'], 'rationale': 'x'}}):
            with self.subTest(fields=fields):
                with self.assertRaises(IdeaError) as caught: _default_fill('method', fields)
                self.assertEqual(caught.exception.code, 'invalid_fill')
        self.assertEqual(_default_fill('assessment', {'proposed_position': 2}), {'proposed_position': 2})
        for fields in ({'actual_position': 2}, {'position': {'actual_position': 2}}, {'proposed_position': 0}, {'proposed_position': True}):
            with self.subTest(fields=fields):
                with self.assertRaises(IdeaError) as caught: _default_fill('assessment', fields)
                self.assertEqual(caught.exception.code, 'invalid_fill')
        with self.assertRaises(IdeaError): _default_fill('memory', {'status': 'found'})

    def test_fill_needs_a_delivered_open_request_with_its_exact_correlation(self):
        self.enqueue()
        undelivered = dict(request_id='request1', session_id=SID, idea_id=IDEA, accepted_revision=2, draft_version=3,
                           operation='shape', source_digest=source_digest('shape', 2, self.source['data']), fields=dict(outcome='x'))
        self.code('request_not_delivered', lambda: self.broker.fill(BIND,'gen1',undelivered))
        self.code('response_mismatch', lambda: self.fill(dict(outcome='x'), draft_version=4))
        self.code('request_not_found', lambda: self.fill(dict(outcome='x'), request_id='other'))
        self.broker.cancel(BIND,'gen1')
        self.code('agent_unavailable', lambda: self.broker.fill(BIND,'gen1',dict(undelivered)))

    def test_each_fill_renews_the_answer_window_and_the_idle_clock(self):
        self.enqueue(); self.fill(dict(outcome='one'))            # t=0
        for _ in range(3):                                           # a slow terminal conversation
            self.clock.advance(ANSWER_GRACE-20)
            self.assertEqual(self.broker.status(BIND,'gen1')['agent_status'],'connected')
            self.fill(dict(outcome='again'))
        self.clock.advance(ANSWER_GRACE-1); self.assertEqual(self.broker.status(BIND,'gen1')['agent_status'],'connected')
        self.clock.advance(1); self.assertNotEqual(self.broker.status(BIND,'gen1')['agent_status'],'connected')

    def test_fill_log_is_bounded(self):
        import idea_proposals
        self.enqueue()
        with unittest.mock.patch.object(idea_proposals, 'MAX_FILLS', 2):
            self.fill(dict(outcome='1')); self.fill(dict(outcome='2'))
            self.code('fill_capacity', lambda: self.fill(dict(outcome='3')))

    def test_reaching_another_step_supersedes_an_unanswered_conversation(self):
        self.enqueue(); self.fill(dict(outcome='agreed'))
        method = dict(self.envelope('request2'), operation='method',
                      source_digest=source_digest('method', 2, self.source['data']))
        self.assertEqual(self.broker.enqueue(BIND,'gen1',method,self.source)['status'],'pending')
        self.code('request_cancelled', lambda: self.broker.fill(BIND,'gen1',dict(
            request_id='request1', session_id=SID, idea_id=IDEA, accepted_revision=2, draft_version=3, operation='shape',
            source_digest=source_digest('shape', 2, self.source['data']), fields=dict(outcome='late'))))
        self.code('request_busy', lambda: self.broker.enqueue(BIND,'gen1',dict(method, request_id='request3'),self.source))

    # --- Review fill-r1 ---
    def other(self, key, operation='method', idea=IDEA):
        return dict(self.envelope(key), operation=operation, idea_id=idea,
                    source_digest=source_digest(operation if operation != 'assessment' else 'shape', 2, self.source['data']))

    def test_a_pinned_uncertain_reply_is_never_superseded(self):
        # Blocker: superseding a pinned reply left committed_uncertain unreconcilable.
        self.enqueue(); response=self.reply()
        def fail(_): raise OSError('disk')
        self.broker.persist_proposal=fail
        self.code('proposal_commit_uncertain', lambda: self.broker.respond(BIND,'gen1',response))
        self.code('request_busy', lambda: self.broker.enqueue(BIND,'gen1',self.other('request2'),self.source))
        self.broker.persist_proposal=self.persist
        self.assertEqual(self.broker.respond(BIND,'gen1',response)['status'],'completed')

    def test_a_pinned_reply_blocks_new_steps_only_during_its_answer_window(self):
        # Review fill-r2 blocker: an agent that never reconciles must not block the binding forever.
        self.enqueue(); response=self.reply()
        def fail(_): raise OSError('disk')
        self.broker.persist_proposal=fail
        self.code('proposal_commit_uncertain', lambda: self.broker.respond(BIND,'gen1',response))
        self.code('response_busy', lambda: self.fill(dict(outcome='after the pinned reply')))
        self.code('request_busy', lambda: self.broker.enqueue(BIND,'gen1',self.other('request2'),self.source))
        for _ in range(ANSWER_GRACE//30+1):  # the human keeps working; the agent waits but never reconciles, past the window
            self.clock.advance(30); self.broker.touch(BIND,'gen1'); self.broker.events(BIND,'gen1',SID,1,0)
        self.assertEqual(self.broker.enqueue(BIND,'gen1',self.other('request2'),self.source)['status'],'pending')

    def test_fill_capacity_counts_the_response_reservation_once(self):
        import idea_proposals
        self.enqueue()
        entry=self.broker.bindings[BIND]
        entry['bytes']=idea_proposals.LIMIT-100   # reservation already inside; 100 bytes of room left
        self.assertEqual(self.fill(dict(outcome='fits'))['fill_sequence'],1)

    def test_a_refused_new_request_never_costs_the_open_conversation(self):
        import idea_proposals
        self.enqueue(); self.fill(dict(outcome='agreed'))
        with unittest.mock.patch.object(idea_proposals, 'MAX_RECORDS', 1):
            self.code('request_capacity', lambda: self.broker.enqueue(BIND,'gen1',self.other('request2'),self.source))
        talk=self.broker.conversation(BIND,'gen1')
        self.assertEqual((talk['request_id'], len(talk['fills'])), ('request1', 1))
        self.assertEqual(self.fill(dict(next_slice='still open'))['fill_sequence'], 2)

    def test_fill_bytes_return_when_the_conversation_ends(self):
        def run(fills):
            broker=Broker(clock=self.clock, validate_source=self.validate, persist_proposal=self.persist)
            broker.open(BIND,'gen1',SID)
            broker.enqueue(BIND,'gen1',self.envelope(),self.source)
            event=broker.events(BIND,'gen1',SID,0,0)['events'][0]
            for text in fills:
                broker.fill(BIND,'gen1',dict({k:v for k,v in event.items() if k not in ('sequence','data')}, fields=dict(outcome=text)))
            broker.enqueue(BIND,'gen1',self.other('request2'),self.source)   # supersedes request1
            return broker.bindings[BIND]['bytes'], broker.bindings[BIND]['requests']['request1']
        without, _ = run([])
        with_fills, record = run(['x'*5000, 'y'*5000])
        self.assertEqual(with_fills, without, 'a superseded conversation keeps no fill bytes')
        self.assertEqual((record['fill_bytes'], record['fills'], record['reason']), (0, [], 'superseded'))

    def test_fill_log_has_a_per_request_byte_cap(self):
        import idea_proposals
        self.enqueue()
        with unittest.mock.patch.object(idea_proposals, 'FILL_BYTES', 100):
            self.fill(dict(outcome='x'*40))
            self.code('fill_capacity', lambda: self.fill(dict(outcome='y'*80)))

    def test_a_memory_request_is_never_a_conversation(self):
        memory=dict(self.envelope('memory1'), operation='memory', source_digest=source_digest('memory', 2, self.source['data']))
        self.broker.enqueue(BIND,'gen1',memory,self.source)
        self.broker.events(BIND,'gen1',SID,0,0)
        self.assertIsNone(self.broker.conversation(BIND,'gen1'))

    def test_another_idea_supersedes_but_the_same_idea_and_step_stays_busy(self):
        self.enqueue()
        self.code('request_busy', lambda: self.broker.enqueue(BIND,'gen1',self.envelope('request-dup'),self.source))
        other_idea='idea_'+'4'*32
        self.assertEqual(self.broker.enqueue(BIND,'gen1',dict(self.envelope('request-b'), idea_id=other_idea),self.source)['status'],'pending')
        self.assertEqual(self.broker.bindings[BIND]['requests']['request1']['reason'],'superseded')

    def test_wait_releases_condition_for_enqueue_and_cancel(self):
        waiting=threading.Event(); result=[]
        original=self.broker.condition.wait
        def wait(timeout): waiting.set(); return original(timeout)
        self.broker.condition.wait=wait
        thread=threading.Thread(target=lambda:result.append(self.broker.events(BIND,'gen1',SID,0,25)))
        thread.start(); self.assertTrue(waiting.wait(1))
        self.enqueue(); thread.join(2)
        self.assertFalse(thread.is_alive()); self.assertEqual(len(result[0]['events']),1)
        waiting.clear(); result.clear()
        thread=threading.Thread(target=lambda:result.append(self.broker.events(BIND,'gen1',SID,1,25)))
        thread.start(); self.assertTrue(waiting.wait(1))
        self.broker.cancel(BIND,'gen1'); thread.join(2)
        self.assertFalse(thread.is_alive()); self.assertEqual(result[0]['agent_status'],'disconnected')

    def test_callback_no_broker_lock_and_cancel_during_publication(self):
        entered=threading.Event(); release=threading.Event(); errors=[]
        def persist(evidence):
            self.assertEqual(self.broker.status(BIND,'gen1')['agent_status'],'connected')
            entered.set(); self.assertTrue(release.wait(2)); return self.persist(evidence)
        self.broker.persist_proposal=persist
        self.enqueue(); response=self.reply()
        def respond():
            try:self.broker.respond(BIND,'gen1',response)
            except IdeaError as exc: errors.append(exc.code)
        thread=threading.Thread(target=respond);thread.start();self.assertTrue(entered.wait(1))
        self.broker.cancel(BIND,'gen1');release.set();thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,['proposal_commit_uncertain'])
        self.assertEqual(len(self.saved),1)
        self.assertEqual(self.broker.status(BIND,'gen1')['agent_status'],'disconnected')

    def test_callback_failure_redacted_and_retryable(self):
        self.enqueue(); response=self.reply()
        def fail(_):raise RuntimeError('Original words /private credential')
        self.broker.persist_proposal=fail
        self.code('proposal_commit_uncertain',lambda:self.broker.respond(BIND,'gen1',response))
        self.broker.persist_proposal=self.persist
        self.assertEqual(self.broker.respond(BIND,'gen1',response)['status'],'completed')

    def test_uncertain_publication_pins_response_and_reconciles(self):
        self.enqueue(); response=self.reply(); persisted={}
        def uncertain(evidence):
            persisted.update(copy.deepcopy(evidence))
            raise OSError('private path')
        self.broker.persist_proposal=uncertain
        with self.assertRaises(IdeaError) as caught:self.broker.respond(BIND,'gen1',response)
        self.assertEqual(caught.exception.code,'proposal_commit_uncertain')
        self.assertEqual(caught.exception.details['write_state'],'committed_uncertain')
        changed=copy.deepcopy(response);changed['proposal']['outcome']='changed'
        self.code('response_conflict',lambda:self.broker.respond(BIND,'gen1',changed))
        self.assertEqual(persisted['proposal']['outcome'],'Clean lid')
        def reconcile(evidence):
            self.assertEqual(evidence,persisted)
            return dict(proposal_id='proposal_fixture',sha256='a'*64)
        self.broker.persist_proposal=reconcile
        self.assertEqual(self.broker.respond(BIND,'gen1',response)['status'],'completed')

    def test_uncertain_reply_reconciles_receipt_before_source_drift_check(self):
        self.enqueue();response=self.reply();calls=[];receipt={}
        def uncertain(evidence):
            calls.append(copy.deepcopy(evidence))
            receipt['evidence']=copy.deepcopy(evidence)
            receipt['result']=dict(proposal_id='proposal_fixture',sha256='a'*64)
            raise OSError('uncertain')
        self.broker.persist_proposal=uncertain
        self.code('proposal_commit_uncertain',lambda:self.broker.respond(BIND,'gen1',response))
        self.source['draft_version']+=1
        def reconcile(evidence):
            calls.append(copy.deepcopy(evidence))
            self.assertEqual(evidence,receipt['evidence'])
            return copy.deepcopy(receipt['result'])
        self.broker.persist_proposal=reconcile
        result=self.broker.respond(BIND,'gen1',response)
        self.assertEqual(result['evidence'],receipt['result'])
        self.assertEqual(result['write_state'],'applied')
        self.assertEqual(len(calls),2)
        self.assertEqual(self.broker.respond(BIND,'gen1',response),result)
        self.assertEqual(len(calls),2)

    def test_uncertain_unpublished_retry_stays_uncertain_after_source_drift(self):
        self.enqueue();response=self.reply();calls=[]
        def unpublished(evidence):calls.append(evidence);raise OSError('before receipt')
        self.broker.persist_proposal=unpublished
        self.code('proposal_commit_uncertain',lambda:self.broker.respond(BIND,'gen1',response))
        self.source['draft_version']+=1
        def stale_without_receipt(evidence):
            calls.append(evidence)
            self.broker._checked_source(evidence['correlation'],evidence['source'])
            self.fail('Stale unpublished response must not publish')
        self.broker.persist_proposal=stale_without_receipt
        charged=self.broker.bindings[BIND]['bytes']
        with self.assertRaises(IdeaError) as caught:self.broker.respond(BIND,'gen1',response)
        self.assertEqual(caught.exception.code,'proposal_commit_uncertain')
        self.assertTrue(caught.exception.details['committed'])
        self.assertEqual(caught.exception.details['write_state'],'committed_uncertain')
        self.assertEqual(len(calls),2)
        self.assertEqual(self.broker.bindings[BIND]['bytes'],charged)
        self.assertEqual(self.broker.bindings[BIND]['requests']['request1']['state'],'pending')
        changed=copy.deepcopy(response);changed['proposal']['outcome']='changed'
        self.code('response_conflict',lambda:self.broker.respond(BIND,'gen1',changed))

    def test_bounds_wrong_types_and_typed_unknown_proposals(self):
        self.code('invalid_wait',lambda:self.broker.events(BIND,'gen1',SID,0,True))
        self.code('invalid_wait',lambda:self.broker.events(BIND,'gen1',SID,0,26))
        self.code('invalid_cursor',lambda:self.broker.events(BIND,'gen1',SID,1,0))
        self.enqueue()
        response=self.reply();response['proposal']['unknown']='x'
        self.code('invalid_proposal',lambda:self.broker.respond(BIND,'gen1',response))
        response=self.reply();response['proposal']['outcome']='x'*(LIMIT+1)
        self.code('too_large',lambda:self.broker.respond(BIND,'gen1',response))
        response=self.reply();response['proposal']['outcome']=float('nan')
        self.code('invalid_input',lambda:self.broker.respond(BIND,'gen1',response))

    def test_terminal_records_bounded_without_eviction(self):
        for index in range(MAX_RECORDS):
            self.enqueue('r'+str(index));self.broker.respond(BIND,'gen1',self.reply())
        self.code('request_capacity',lambda:self.enqueue('overflow'))
        first=dict(self.saved[0]['correlation'],proposal=SHAPE)
        self.assertEqual(self.broker.respond(BIND,'gen1',first)['status'],'completed')

    def test_binding_and_generation_tombstones_bounded(self):
        for index in range(2,MAX_BINDINGS+1):
            self.broker.open('binding_'+format(index,'032x'),'gen1',SID)
        self.code('binding_capacity',lambda:self.broker.open('binding_'+'f'*32,'gen1',SID))
        for index in range(2,MAX_RECORDS+1):self.broker.open(BIND,'gen'+str(index),SID)
        self.code('generation_capacity',lambda:self.broker.open(BIND,'overflow',SID))


class AssessmentBrokerTests(unittest.TestCase):
    """Additive default-validator checks; no OwnerService/product claim."""
    def setUp(self):
        self.reset()

    def reset(self):
        from test_assessment import KEY, state_fixture
        import idea_agent_source
        self.target = KEY
        self.state = state_fixture()
        self.source_adapter = idea_agent_source
        self.source = idea_agent_source.prepare_source(self.state, KEY, 'assessment')
        self.clock = Clock()
        self.saved = []
        self.generation = 'agent_'+'4'*32
        # Defaults are intentional. Production OwnerService installs its named
        # digest/proposal callbacks in the separately owned J9-2 artifact.
        self.broker = Broker(clock=self.clock, validate_source=self.validate,
                             persist_proposal=self.persist)
        self.broker.open(BIND, self.generation, SID)

    def validate(self, correlation, supplied):
        return self.source_adapter.validate_source(self.state, correlation, supplied)

    def persist(self, evidence):
        # Typed source validation at the trusted persistence door also checks
        # proposed neighbors against the recorded target/backlog before publish.
        self.source_adapter.validate_current(self.state, evidence)
        self.saved.append(copy.deepcopy(evidence))
        return dict(proposal_id='proposal_assessment_fixture', sha256='b'*64)

    def envelope(self, request='assessment1'):
        from idea_assessment import assessment_digest
        return dict(request_id=request, idea_id=self.target,
            expected_revision=self.source['accepted_revision'],
            expected_draft_version=self.source['draft_version'], operation='assessment',
            source_digest=assessment_digest('assessment', self.source, idea_id=self.target))

    def enqueue(self, request='assessment1'):
        return self.broker.enqueue(BIND, self.generation, self.envelope(request), self.source)

    def reply(self):
        from test_assessment import proposed
        event = self.broker.events(BIND, self.generation, SID, 0, 0)['events'][0]
        return dict({key:value for key,value in event.items() if key not in ('sequence', 'data')},
                    proposal=proposed(self.source))

    def code(self, code, callback):
        with self.assertRaises(IdeaError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_default_assessment_exact_event_reply_and_detached_replay(self):
        pending = self.enqueue()
        self.assertEqual(pending['operation'], 'assessment')
        self.assertEqual(self.enqueue(), pending)
        event = self.broker.events(BIND, self.generation, SID, 0, 0)['events'][0]
        self.assertEqual(set(event), {'request_id', 'session_id', 'idea_id', 'accepted_revision',
            'draft_version', 'operation', 'source_digest', 'sequence', 'data'})
        self.assertEqual(event['data'], self.source['data'])
        event['data']['backlog']['order'].reverse()
        response = self.reply()
        result = self.broker.respond(BIND, self.generation, response)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['write_state'], 'applied')
        self.assertEqual(self.saved[0]['source'], self.source)
        self.assertNotIn('score', result['proposal']['assessment'])
        repeated = self.broker.respond(BIND, self.generation, response)
        self.assertEqual(repeated, result)
        repeated['proposal']['assessment']['basis'] = 'Detached'
        self.assertNotEqual(self.broker.respond(BIND, self.generation, response), repeated)
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(self.broker.events(BIND, self.generation, SID, 0, 0)['events'], [])

    def test_default_assessment_unknown_numeric_and_kano_inputs_keep_no_numeric_score(self):
        for index, (method, inputs) in enumerate((
            ('wsjf', dict(value=3, time_criticality=None, enablement=2, effort=1)),
            ('rice', dict(reach=None, impact=2, confidence=0.5, effort=1)),
            ('kano', dict(category='delighter', hypothesis=True)),
            ('kano', dict(category='must-be', hypothesis=False)),
        )):
            self.enqueue('assessment'+str(index))
            response = self.reply()
            response['proposal']['assessment'].update(method=method, inputs=inputs)
            result = self.broker.respond(BIND, self.generation, response)
            self.assertEqual(result['proposal']['assessment']['inputs'], inputs)
            self.assertNotIn('score', result['proposal']['assessment'])
        self.assertEqual(len(self.saved), 4)

    def test_default_assessment_rejects_browser_scores_agent_override_and_bad_models(self):
        self.enqueue()
        for mutation in ('score', 'effort', 'model', 'kano', 'override'):
            response = self.reply()
            proposal = response['proposal']
            if mutation == 'score': proposal['assessment']['score'] = 99
            elif mutation == 'effort': proposal['assessment']['inputs']['effort'] = 0
            elif mutation == 'model': proposal['assessment']['method'] = 'invented'
            elif mutation == 'kano': proposal['assessment'].update(method='kano', inputs=dict(category=None, hypothesis=True))
            else: proposal['position']['override_reason'] = 'Agent cannot override'
            with self.subTest(mutation=mutation):
                self.code('invalid_proposal', lambda: self.broker.respond(BIND, self.generation, response))
        self.assertEqual(self.saved, [])
        self.assertEqual(self.broker.respond(BIND, self.generation, self.reply())['status'], 'completed')

    def test_default_assessment_digest_rejects_untyped_backlog_even_with_passthrough_source_callback(self):
        malformed = copy.deepcopy(self.source)
        malformed['data']['backlog']['rank'] = 1
        broker = Broker(clock=self.clock, validate_source=lambda correlation,value: value,
                        persist_proposal=self.persist)
        broker.open(BIND, self.generation, SID)
        self.code('proposal_callback_failed', lambda: broker.enqueue(BIND, self.generation,
                  self.envelope(), malformed))
        self.assertEqual(broker.events(BIND, self.generation, SID, 0, 0)['events'], [])
        self.assertEqual(self.saved, [])

    def test_response_source_cas_catches_target_order_and_comparison_drift(self):
        from idea_workflow import save_draft
        from test_assessment import OTHER
        for mutation in ('target', 'order', 'comparison', 'backlog'):
            self.reset(); self.enqueue(); response = self.reply()
            if mutation == 'target':
                idea = self.state['ideas'][self.target]
                self.state['ideas'][self.target] = save_draft(idea, 'assess', {},
                    expected_revision=idea['revision'], expected_draft_version=0)['idea']
            elif mutation == 'order': self.state['order'].reverse()
            elif mutation == 'comparison': self.state['ideas'][OTHER]['revision'] += 1
            else: self.state['backlog_revision'] += 1
            with self.subTest(mutation=mutation):
                self.code('stale_source', lambda: self.broker.respond(BIND, self.generation, response))
                self.code('request_cancelled', lambda: self.broker.respond(BIND, self.generation, response))
            self.assertEqual(self.saved, [])

    def test_completed_assessment_reply_replay_survives_drift_but_conflicting_reply_does_not(self):
        self.enqueue(); response = self.reply()
        recorded = self.broker.respond(BIND, self.generation, response)
        self.state['backlog_revision'] += 1
        self.state['order'].reverse()
        self.assertEqual(self.broker.respond(BIND, self.generation, response), recorded)
        self.assertEqual(len(self.saved), 1)
        changed = copy.deepcopy(response); changed['proposal']['assessment']['basis'] = 'Changed reply'
        self.code('response_conflict', lambda: self.broker.respond(BIND, self.generation, changed))
        self.broker.open(BIND, 'agent_'+'5'*32, SID)
        self.code('wrong_generation', lambda: self.broker.respond(BIND, self.generation, response))

    def test_uncertain_assessment_reply_uses_identical_original_source_on_reconciliation(self):
        self.enqueue(); response = self.reply(); pinned = {}
        def uncertain(evidence):
            pinned['evidence'] = copy.deepcopy(evidence)
            raise OSError('fixture publication uncertain')
        self.broker.persist_proposal = uncertain
        self.code('proposal_commit_uncertain', lambda: self.broker.respond(BIND, self.generation, response))
        self.state['backlog_revision'] += 1
        def reconcile(evidence):
            self.assertEqual(evidence, pinned['evidence'])
            return dict(proposal_id='proposal_assessment_fixture', sha256='b'*64)
        self.broker.persist_proposal = reconcile
        result = self.broker.respond(BIND, self.generation, response)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['write_state'], 'applied')
        self.assertEqual(self.broker.respond(BIND, self.generation, response), result)


if __name__=='__main__':unittest.main()
