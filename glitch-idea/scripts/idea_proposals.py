"""Volatile, bounded current-agent proposal broker.

No HTTP, credentials, Store or acceptance lives here. ROUTES stays empty until
transport composition. Authentication supplies binding/generation/SID explicitly.

Lock contract: cancellation is a short broker-only transition. All callbacks run
OUTSIDE the Condition. Integration must serialize enqueue/respond with its
source guard (binding then Store, never broker then either); validate_source
rechecks the authoritative current snapshot and persist_proposal publishes its
receipt atomically under that guard. Never carry that guard into events(). The
broker checks generation again after callbacks, but cannot guard external data.
Callbacks must not recursively enqueue/respond. Sink returns exactly
{proposal_id,sha256} with optional relative evidence path (<=200 chars); any
sink exception or malformed success is conservatively committed_uncertain.
Sink retries use the same full
correlation and must reconcile uncertainty, never publish a second artifact.
Pinned retries reach the sink before current-source validation: its durable
receipt replay must precede validate_current for an unpublished new response.

Source is {accepted_revision,draft_version,data}. Old operations keep their typed
workflow step inputs/digests; assessment defaults validate its specialized
steps/backlog/target source. A trusted source_digest_fn may supply another codec;
validate_source must then validate its complete schema/dependencies. Frozen
wire envelopes never carry that trusted callback or caller-selected prompts.
The proposal's original source is independent of edited acceptance fields.
Response freshness includes draft_version. Later acceptance checks consumed
dependencies independently; user edits/autosaves may legitimately change the
target fields/draft_version. This broker exposes no acceptance operation.
"""
import copy
import json
import math
import re
import threading
import time

from idea_domain import IdeaError, assessment as check_assessment
from idea_assessment import assessment_digest, validate_assessment_proposal
from idea_workflow import PROTOTYPE_SKILL, STEP_FIELDS, check_memory_preference, source_digest, validate_step_fields

ROUTES = ()
OPERATIONS = ('discovery', 'exploration', 'memory', 'method', 'visual_brief', 'assessment', 'position')
CORRELATION = frozenset(('request_id', 'session_id', 'idea_id', 'accepted_revision',
                         'draft_version', 'operation', 'source_digest'))
ENQUEUE = frozenset(('request_id', 'idea_id', 'expected_revision',
                     'expected_draft_version', 'operation', 'source_digest'))
LIMIT = 1024 * 1024
MAX_RECORDS = 128
MAX_FILLS = 64
FILL_BYTES = 256 * 1024  # per request: the fill log also rides in every page state read
# Conversation fills: the fields an agent may write into the page as each is agreed with
# the human in the terminal. Method selection, budgets and memory claims, and the actual
# backlog position stay the human's (memory needs persisted evidence: use respond).
FILL_STEPS = {'discovery': 'discovery', 'exploration': 'exploration', 'method': 'method', 'assessment': 'assess',
              'visual_brief': 'visualize'}
FILL_KEYS = {'discovery': STEP_FIELDS['discovery'],
             'exploration': STEP_FIELDS['exploration'],
             'method': frozenset(('memory',)),
             'assessment': frozenset(('assessment', 'proposed_position')),
             # The prototype road's design set only; accepting it (disposition, set pointer) stays the human's.
             'visual_brief': frozenset(('source', 'assets'))}
MAX_BINDINGS = 8
HEARTBEAT = 35
# Once an events wait has delivered a request, the agent has this long to answer it
# while a model composes; responding or a cancel ends the window.
ANSWER_GRACE = 600  # the owner ruled 10 minutes (was 300)
IDLE = 600


def _check(condition, code='invalid_input'):
    if not condition:
        raise IdeaError(code, 'Proposal broker: '+code)


def _integer(value, minimum=0):
    _check(type(value) is int and minimum <= value <= 10**12)


def _id(value, prefix=None):
    expression = prefix+r'_[0-9a-f]{32}' if prefix else r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}'
    _check(type(value) is str and re.fullmatch(expression, value) is not None)


def _exact(value, keys):
    _check(type(value) is dict and set(value) == set(keys))


def _bounded(value):
    """Validate already-decoded JSON; HTTP decoder owns duplicate-key rejection."""
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        _check(depth <= 24 and count <= 20000, 'too_large')
        _check(type(item) in (dict, list, str, int, float, bool, type(None)))
        if type(item) is dict:
            _check(all(type(key) is str for key in item))
            pending.extend((child, depth+1) for pair in item.items() for child in pair)
        elif type(item) is list:
            pending.extend((child, depth+1) for child in item)
        elif type(item) is float:
            _check(math.isfinite(item))
        elif type(item) is str:
            _check(len(item) <= LIMIT and not any(0xD800 <= ord(c) <= 0xDFFF for c in item), 'too_large')
        elif type(item) is int:
            _check(abs(item) <= 10**12)
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False,
                     separators=(',', ':'), allow_nan=False).encode('utf-8')
    _check(len(raw) <= LIMIT, 'too_large')
    return raw


def _source(value):
    _bounded(value)
    _exact(value, ('accepted_revision', 'draft_version', 'data'))
    _integer(value['accepted_revision'], 1)
    _integer(value['draft_version'])
    _check(type(value['data']) is dict)
    return copy.deepcopy(value)


def _default_digest(operation, value):
    if operation == 'assessment':
        return assessment_digest(operation, value)
    return source_digest(operation, value['accepted_revision'], value['data'])


def _default_proposal(operation, value):
    # Other codecs arrive in their own artifacts. Never infer their schemas.
    _check(operation in ('discovery', 'exploration', 'method', 'assessment', 'visual_brief'), 'operation_unavailable')
    try:
        if operation == 'visual_brief':
            # Only the prototype-skill signal travels; a closed enum, never free text.
            _check(type(value) is dict and set(value) == {'prototype_skill'}
                   and value['prototype_skill'] in PROTOTYPE_SKILL, 'invalid_proposal')
            return dict(value)
        if operation == 'assessment':
            return validate_assessment_proposal(value)
        if operation == 'method':
            # R8: memory only, never a selection.
            _check(type(value) is dict and set(value) == {'memory'}, 'invalid_proposal')
            checked = validate_step_fields('method', value, partial=True)
            check_memory_preference(checked['memory'])
            return checked
        return validate_step_fields(operation, value)
    except IdeaError as exc:
        raise IdeaError('invalid_proposal', 'Proposal broker: invalid_proposal') from exc


def _default_fill(operation, fields):
    """A partial, typed slice of one step's fields; never a selection the human owns."""
    _check(operation in FILL_KEYS, 'operation_unavailable')
    _check(type(fields) is dict and fields and set(fields) <= FILL_KEYS[operation]
           and all(value is not None for value in fields.values()), 'invalid_fill')
    try:
        if operation == 'visual_brief':
            _check(set(fields) == {'source', 'assets'} and fields['source'] == 'prototype'
                   and type(fields['assets']) is list and len(fields['assets']) == 2, 'invalid_fill')
        if operation != 'assessment':
            checked = validate_step_fields(FILL_STEPS[operation], fields, partial=True)
            if operation == 'method':
                check_memory_preference(checked['memory'])
            return checked
        checked = {}
        if 'assessment' in fields:
            checked.update(validate_step_fields('assess', {'assessment': fields['assessment']}, partial=True))
            check_assessment(checked['assessment'])
        if 'proposed_position' in fields:
            _check(type(fields['proposed_position']) is int and 1 <= fields['proposed_position'] <= 10**6, 'invalid_fill')
            checked['proposed_position'] = fields['proposed_position']
        return checked
    except IdeaError as exc:
        raise IdeaError('invalid_fill', 'Proposal broker: invalid_fill') from exc


class Broker:
    def __init__(self, *, validate_source, persist_proposal, reconcile_source=None,
                 validate_proposal=_default_proposal, source_digest_fn=_default_digest,
                 validate_fill=_default_fill, clock=time.monotonic):
        _check(all(callable(fn) for fn in (validate_source, persist_proposal,
                                          validate_proposal, source_digest_fn, validate_fill, clock)))
        self.clock = clock
        self.validate_source = validate_source
        self.persist_proposal = persist_proposal
        _check(reconcile_source is None or callable(reconcile_source))
        self.reconcile_source = reconcile_source
        self.validate_fill = validate_fill
        self.validate_proposal = validate_proposal
        self.source_digest_fn = source_digest_fn
        self.condition = threading.Condition(threading.Lock())
        self.bindings = {}
        self.sequence = 0

    def _callback(self, fn, *args):
        try:
            return copy.deepcopy(fn(*copy.deepcopy(args)))
        except Exception as exc:
            # Callback diagnostic text may contain paths, source or credentials.
            code = exc.code if isinstance(exc, IdeaError) and exc.code in (
                'invalid_proposal', 'invalid_fill', 'operation_unavailable', 'stale_source') else 'proposal_callback_failed'
            raise IdeaError(code, 'Proposal broker: '+code) from exc

    def _retire(self, entry, reason, status='disconnected'):
        entry['status'], entry['reason'] = status, reason
        for record in entry['requests'].values():
            if record['state'] in ('pending', 'responding'):
                record['state'] = 'cancelled'
                record['reason'] = reason
        self.condition.notify_all()

    def _answering(self, entry, current):
        # Only a delivered, unanswered request holds the lease, and only until its
        # bounded window closes; a completed or cancelled request holds nothing.
        return any(record['state'] in ('pending', 'responding') and
                   record['answer_until'] is not None and current < record['answer_until']
                   for record in entry['requests'].values())

    def _release(self, entry, record, reason):
        """Cancel one open request and return its response reservation and fill log bytes."""
        record.update(state='cancelled', reason=reason)
        entry['bytes'] -= record['reserved']+record['fill_bytes']
        record.update(reserved=0, fill_bytes=0, fills=[])

    def _expire(self, entry):
        if entry['status'] == 'connected':
            current = self.clock()
            if current-entry['activity'] >= IDLE:
                self._retire(entry, 'idle', 'paused')
            elif current-entry['heartbeat'] >= HEARTBEAT and not self._answering(entry, current):
                self._retire(entry, 'heartbeat_expired')

    def _entry(self, binding_id, generation, connected=False):
        _id(binding_id, 'binding'); _id(generation)
        entry = self.bindings.get(binding_id)
        _check(entry is not None and entry['generation'] == generation, 'wrong_generation')
        self._expire(entry)
        if connected:
            _check(entry['status'] == 'connected', 'agent_unavailable')
        return entry

    def _status(self, entry):
        return dict(ok=True, code='ok', session_id=entry['session_id'],
                    agent_status=entry['status'], reason=entry['reason'], sequence=self.sequence)

    def open(self, binding_id, generation, session_id):
        """Trusted explicit bind. Same generation never resets an expired lease.

        Terminal binding slots stay bounded; resume replaces a slot, never keeps
        old live records. Their durable evidence is the injected sink's job.
        """
        _id(binding_id, 'binding'); _id(generation); _id(session_id, 'session')
        with self.condition:
            old = self.bindings.get(binding_id)
            if old is not None:
                if old['generation'] == generation:
                    _check(old['session_id'] == session_id, 'wrong_session')
                    self._expire(old)
                    return self._status(old)
                _check(old['session_id'] == session_id, 'wrong_session')
                _check(generation not in old['generations'], 'generation_reused')
                _check(len(old['generations']) < MAX_RECORDS, 'generation_capacity')
                self._retire(old, 'rebound')
            else:
                _check(len(self.bindings) < MAX_BINDINGS, 'binding_capacity')
            current = self.clock()
            entry = dict(generation=generation, session_id=session_id, status='connected',
                         reason=None, heartbeat=current, activity=current, requests={}, bytes=0,
                         generations=(old['generations'] | {generation}) if old else {generation})
            self.bindings[binding_id] = entry
            self.condition.notify_all()
            return self._status(entry)

    def cancel(self, binding_id, generation, reason='closed'):
        """Safe under SessionPolicy binding lock: no I/O or external callback."""
        _check(reason in ('closed', 'rebound', 'invalidated', 'shutdown'))
        with self.condition:
            entry = self.bindings.get(binding_id)
            if entry is not None and entry['generation'] == generation:
                self._retire(entry, reason)

    close = cancel

    def discard(self, binding_id):
        """Owner discard of a whole binding: cancel its open requests, wake any waiting events call, free the slot."""
        _id(binding_id, 'binding')
        with self.condition:
            entry = self.bindings.pop(binding_id, None)
            if entry is not None:
                self._retire(entry, 'invalidated')  # Notifies; a waiter's next recheck finds no binding and refuses.
            return entry is not None

    def release_request(self, binding_id, generation, idea_id, operation, reason='released'):
        """Hand release: cancel one step's open, unanswered request; the binding stays usable.

        Only a pending request with no pinned reply is cancelled (a reply being persisted
        or awaiting reconciliation is final). The agent then sees request_cancelled for
        that request id. Returns the number of requests cancelled; idempotent.
        """
        _id(idea_id, 'idea'); _check(type(operation) is str and operation in OPERATIONS)
        with self.condition:
            entry = self.bindings.get(binding_id)
            if entry is None or entry['generation'] != generation:
                return 0
            self._expire(entry)
            count = 0
            for record in entry['requests'].values():
                correlation = record['correlation']
                if (record['state'] == 'pending' and record['response'] is None and
                        (correlation['operation'], correlation['idea_id']) == (operation, idea_id)):
                    self._release(entry, record, reason)
                    count += 1
            if count:
                self.condition.notify_all()
            return count

    def status(self, binding_id, generation):
        with self.condition:
            return self._status(self._entry(binding_id, generation))

    def touch(self, binding_id, generation):
        """A human's authenticated browser write keeps a CONNECTED agent from idling out.

        Only the idle clock moves: the heartbeat is untouched, so an agent that has
        stopped waiting still expires at HEARTBEAT, and a human who walks away still
        pauses the agent at IDLE (the cost guard). A paused, disconnected or
        rotated binding is never revived; a mismatched generation is a no-op.
        """
        with self.condition:
            entry = self.bindings.get(binding_id)
            if entry is None or entry['generation'] != generation:
                return False
            self._expire(entry)
            if entry['status'] != 'connected':
                return False
            entry['activity'] = self.clock()
            self.condition.notify_all()
            return True

    def _checked_source(self, correlation, source):
        checked = _source(self._callback(self.validate_source, correlation, source))
        _check(checked == source, 'stale_source')
        digest = self._callback(self.source_digest_fn, correlation['operation'], checked)
        _check(type(digest) is str and re.fullmatch(r'[0-9a-f]{64}', digest) is not None)
        _check(digest == correlation['source_digest'], 'stale_source')
        return checked

    def _reconcile(self, record):
        correlation, source = self._callback(self.reconcile_source, record['correlation'], record['source'],
                                             record['fills'], record['base'])
        source = _source(source)
        _check(type(correlation) is dict and set(correlation) == CORRELATION and all(
            correlation[k] == record['correlation'][k] for k in ('request_id', 'session_id', 'idea_id',
                                                                 'accepted_revision', 'operation')))
        _check(source['draft_version'] == correlation['draft_version'] and
               type(correlation['draft_version']) is int)
        self._checked_source(correlation, source)
        return correlation, source

    def enqueue(self, binding_id, generation, envelope, source, base=None):
        _bounded(envelope); envelope=copy.deepcopy(envelope); _exact(envelope, ENQUEUE)
        _id(envelope['request_id']); _id(envelope['idea_id'], 'idea')
        _integer(envelope['expected_revision'], 1); _integer(envelope['expected_draft_version'])
        _check(type(envelope['operation']) is str and envelope['operation'] in OPERATIONS)
        _check(type(envelope['source_digest']) is str and re.fullmatch(r'[0-9a-f]{64}', envelope['source_digest']) is not None)
        source = _source(source); base = copy.deepcopy(base); _bounded(base)
        _check(source['accepted_revision'] == envelope['expected_revision'] and
               source['draft_version'] == envelope['expected_draft_version'], 'stale_source')
        fingerprint = _bounded(dict(envelope=envelope, source=source))
        with self.condition:
            entry = self._entry(binding_id, generation, True)
            correlation = dict(request_id=envelope['request_id'], session_id=entry['session_id'],
                idea_id=envelope['idea_id'], accepted_revision=envelope['expected_revision'],
                draft_version=envelope['expected_draft_version'], operation=envelope['operation'],
                source_digest=envelope['source_digest'])
        self._checked_source(correlation, source)
        with self.condition:
            _check(self._entry(binding_id, generation, True) is entry, 'wrong_generation')
            prior = entry['requests'].get(envelope['request_id'])
            if prior is not None:
                _check(prior['fingerprint'] == fingerprint, 'request_id_conflict')
                _check(prior['state'] != 'cancelled', 'request_cancelled')
                return copy.deepcopy(prior['result'])
            open_requests = [r for r in entry['requests'].values() if r['state'] in ('pending', 'responding')]
            # Reaching another step, or another idea, supersedes an unanswered conversation. A reply
            # being persisted, a pinned reply awaiting reconciliation, or a second request for the
            # same step of the same idea stays busy.
            now = self.clock()
            # A pinned reply holds the binding only while its answer window is open: past it, the
            # agent has had its chance to reconcile and a new step may go ahead (no permanent block).
            _check(all(r['state'] == 'pending' and
                       (r['response'] is None or r['answer_until'] is None or now >= r['answer_until']) and
                       (r['correlation']['operation'], r['correlation']['idea_id']) != (envelope['operation'], envelope['idea_id'])
                       for r in open_requests), 'request_busy')
            event = dict(correlation, sequence=self.sequence+1, data=copy.deepcopy(source['data']))
            result = dict(ok=True, code='ok', request_id=envelope['request_id'],
                          write_state='not_applied', status='pending', **{k:v for k,v in correlation.items() if k!='request_id'})
            # Charge every retained serialized copy, including terminal replay.
            charge = len(fingerprint)+len(_bounded(source))+len(_bounded(event))+len(_bounded(correlation))+len(_bounded(result))
            released = sum(r['reserved']+r['fill_bytes'] for r in open_requests)
            # Admission first: a refused request never costs the open conversation.
            _check(len(entry['requests']) < MAX_RECORDS and entry['bytes']-released+charge+LIMIT//4 <= LIMIT, 'request_capacity')
            for superseded in open_requests:
                self._release(entry, superseded, 'superseded')
            self.sequence += 1
            entry['requests'][envelope['request_id']] = dict(state='pending', correlation=correlation,
                source=source, event=event, fingerprint=fingerprint, result=result, response=None,
                reserved=LIMIT//4, answer_until=None, fills=[], fill_bytes=0, base=base, rebased=None)
            entry['bytes'] += charge+LIMIT//4
            entry['activity'] = self.clock()
            # A request nobody has polled yet has no answer window to hold the lease, and the one
            # it just superseded may have held it for minutes: restart the lease so the agent gets
            # a full HEARTBEAT to poll before a request it has never seen can be retired.
            entry['heartbeat'] = entry['activity']
            self.condition.notify_all()
            return copy.deepcopy(result)

    def fill(self, binding_id, generation, payload):
        """Record fields the human agreed in the terminal for the page to apply.

        Never a draft write or an acceptance: the page applies each fill to its buffer and
        saves it as the human's draft; Accept stays the human's click. Each fill renews the
        answer window (rolling, bounded at ANSWER_GRACE from the last fill or wait).
        """
        _bounded(payload); payload = copy.deepcopy(payload); _exact(payload, CORRELATION | {'fields'})
        _id(payload['request_id']); _id(payload['session_id'], 'session'); _id(payload['idea_id'], 'idea')
        _integer(payload['accepted_revision'], 1); _integer(payload['draft_version'])
        _check(type(payload['operation']) is str and payload['operation'] in OPERATIONS)
        fields = self._callback(self.validate_fill, payload['operation'], payload['fields'])
        _check(type(fields) is dict and fields and set(fields) <= FILL_KEYS.get(payload['operation'], frozenset()), 'invalid_fill')
        charge = len(_bounded(fields))
        with self.condition:
            entry = self._entry(binding_id, generation, True)
            record = entry['requests'].get(payload['request_id'])
            _check(record is not None, 'request_not_found')
            _check(all(type(payload[k]) is type(record['correlation'][k]) and payload[k]==record['correlation'][k]
                       for k in CORRELATION), 'response_mismatch')
            _check(record['state'] == 'pending', 'request_cancelled' if record['state'] == 'cancelled' else
                   'request_closed' if record['state'] == 'completed' else 'response_busy')
            # A reply pinned for reconciliation is final: fills would be discarded by its replay.
            _check(record['response'] is None, 'response_busy')
            _check(record['answer_until'] is not None, 'request_not_delivered')
            # The request's response reservation is already inside entry['bytes'].
            _check(len(record['fills']) < MAX_FILLS and record['fill_bytes']+charge <= FILL_BYTES
                   and entry['bytes']+charge <= LIMIT, 'fill_capacity')
            sequence = len(record['fills'])+1
            record['fills'].append(dict(sequence=sequence, fields=fields))
            record['fill_bytes'] += charge
            entry['bytes'] += charge
            now = self.clock()
            # The human answered in the terminal: proof of life and of human activity.
            entry['heartbeat'] = entry['activity'] = now
            record['answer_until'] = now+ANSWER_GRACE
            self.condition.notify_all()
            return dict(ok=True, code='ok', request_id=payload['request_id'], operation=payload['operation'],
                        status='pending', write_state='not_applied', fill_sequence=sequence)

    def open_request(self, binding_id, generation, request_id, idea_id, revision, operation):
        """Refuse unless request_id is a delivered, still-open request of this operation for this idea and revision.

        Read-only gate for the asset door: the same codes the fill path names, no lease renewed.
        """
        _id(request_id); _id(idea_id, 'idea'); _integer(revision, 1)
        with self.condition:
            entry = self._entry(binding_id, generation, True)
            record = entry['requests'].get(request_id)
            _check(record is not None, 'request_not_found')
            correlation = record['correlation']
            _check(correlation['operation'] == operation and correlation['idea_id'] == idea_id
                   and correlation['accepted_revision'] == revision, 'response_mismatch')
            _check(record['state'] == 'pending', 'request_cancelled' if record['state'] == 'cancelled' else
                   'request_closed' if record['state'] == 'completed' else 'response_busy')
            _check(record['response'] is None, 'response_busy')
            _check(record['answer_until'] is not None, 'request_not_delivered')

    def conversation(self, binding_id, generation):
        """The delivered, open request and its fills, for the page; never renews a lease."""
        with self.condition:
            entry = self.bindings.get(binding_id)
            if entry is None or entry['generation'] != generation:
                return None
            self._expire(entry)
            if entry['status'] != 'connected':
                return None
            for record in entry['requests'].values():
                if (record['state'] in ('pending', 'responding') and record['answer_until'] is not None
                        and record['correlation']['operation'] in FILL_KEYS):
                    correlation = record['correlation']
                    return copy.deepcopy(dict(request_id=correlation['request_id'], operation=correlation['operation'],
                                              idea_id=correlation['idea_id'], accepted_revision=correlation['accepted_revision'],
                                              fills=record['fills']))
            return None

    def events(self, binding_id, generation, session_id, after=0, timeout=25):
        _integer(after); _id(session_id, 'session')
        _check(type(timeout) in (int,float) and math.isfinite(timeout) and 0 <= timeout <= 25, 'invalid_wait')
        with self.condition:
            entry = self._entry(binding_id, generation, True)
            _check(entry['session_id'] == session_id, 'wrong_session')
            _check(after <= self.sequence, 'invalid_cursor')
            entry['heartbeat'] = self.clock()
            deadline = self.clock()+timeout
            while True:
                _check(self._entry(binding_id, generation) is entry, 'wrong_generation')
                delivered = [r for r in entry['requests'].values()
                             if r['state']=='pending' and r['event']['sequence']>after]
                events = [r['event'] for r in delivered]
                remaining = deadline-self.clock()
                if entry['status'] != 'connected' or events or remaining <= 0:
                    if entry['status']=='connected':
                        entry['heartbeat'] = self.clock()
                        for record in delivered:
                            # The window opens on first delivery; a re-poll never extends it.
                            if record['answer_until'] is None:
                                record['answer_until'] = entry['heartbeat']+ANSWER_GRACE
                    return copy.deepcopy(dict(self._status(entry), events=events if entry['status']=='connected' else []))
                self.condition.wait(min(remaining, max(0.001, IDLE-(self.clock()-entry['activity'])), 1))

    def respond(self, binding_id, generation, response):
        raw = _bounded(response); response=copy.deepcopy(response); _exact(response, CORRELATION | {'proposal'})
        _id(response['request_id']); _id(response['session_id'], 'session'); _id(response['idea_id'], 'idea')
        _integer(response['accepted_revision'], 1); _integer(response['draft_version'])
        with self.condition:
            entry = self._entry(binding_id, generation, True)
            record = entry['requests'].get(response['request_id'])
            _check(record is not None, 'request_not_found')
            _check(all(type(response[k]) is type(record['correlation'][k]) and response[k]==record['correlation'][k]
                       for k in CORRELATION), 'response_mismatch')
            # A matched reply is the agent's proof of life and, like a fill, the human's conversation
            # going on: the lease and the idle clock both cover its persist.
            entry['heartbeat'] = entry['activity'] = self.clock()
            completed = record['state']=='completed'
            pinned = record['response'] is not None
            if pinned:
                _check(record['response'] == raw, 'response_conflict')
            if not completed:
                _check(record['state']=='pending', 'request_cancelled' if record['state']=='cancelled' else 'response_busy')
                _check(len(raw) <= record['reserved']//3, 'response_capacity')
                record['state'] = 'responding'
        if completed:
            # Recorded publication survives drift; projection owns eligibility.
            # Authentication and generation still gate this replay.
            with self.condition:
                _check(self._entry(binding_id, generation, True) is entry, 'wrong_generation')
                return copy.deepcopy(record['result'])
        try:
            proposal = self._callback(self.validate_proposal, response['operation'], response['proposal'])
            _bounded(proposal); _check(type(proposal) is dict, 'invalid_proposal')
            evidence = dict(binding_id=binding_id, generation=generation,
                correlation=record['correlation'], source=record['source'], proposal=proposal)
            if not pinned:
                try:
                    self._checked_source(record['correlation'], record['source'])
                except IdeaError as exc:
                    rebased = None
                    if exc.code == 'stale_source' and self.reconcile_source is not None and record['fills']:
                        # The page saved this request's own fills as the draft, so the draft moved.
                        # The callback's exact test: nothing but those recorded fills changed.
                        try:
                            rebased = self._reconcile(record)
                        except IdeaError as inner:
                            if inner.code != 'stale_source':
                                raise
                    if rebased is None:
                        if exc.code == 'stale_source':
                            with self.condition:
                                if record['state']=='responding':
                                    # No sink attempt: keep request bookkeeping but
                                    # release its unused response reservation and fill log.
                                    self._release(entry, record, 'stale_source')
                        raise
                    with self.condition:
                        record['rebased'] = rebased
            if record['rebased'] is not None:
                # Evidence records the source the proposal was actually valid against.
                evidence.update(correlation=record['rebased'][0], source=record['rebased'][1])
            with self.condition:
                _check(self._entry(binding_id, generation, True) is entry and record['state']=='responding', 'request_cancelled')
            # Pin BEFORE a possibly committing sink, including failed/uncertain
            # attempts. A changed retry must never reach the persistence seam.
            provisional = dict(record['correlation'], ok=True, code='ok', status='completed',
                               write_state='applied', proposal=proposal,
                               evidence=dict(proposal_id='x'*128, sha256='a'*64, path='x'*200))
            _check(len(raw)+len(_bounded(provisional)) <= record['reserved'], 'response_capacity')
            with self.condition:
                _check(self._entry(binding_id, generation, True) is entry and record['state']=='responding', 'request_cancelled')
                record['response'] = raw
            try:
                publication = copy.deepcopy(self.persist_proposal(copy.deepcopy(evidence)))
                _bounded(publication)
                _check(type(publication) is dict and set(publication) in (
                    {'proposal_id','sha256'}, {'proposal_id','sha256','path'}))
                _id(publication['proposal_id'])
                _check(type(publication['sha256']) is str and re.fullmatch(r'[0-9a-f]{64}',publication['sha256']) is not None)
                if 'path' in publication:
                    path = publication['path']
                    _check(type(path) is str and len(path)<=200 and path and
                           not path.startswith('/') and '\\' not in path and
                           all(part not in ('','.', '..') for part in path.split('/')))
            except Exception as exc:
                raise IdeaError('proposal_commit_uncertain', 'Proposal publication is uncertain',
                                committed=True, write_state='committed_uncertain') from exc
            result = dict(record['correlation'], ok=True, code='ok', status='completed',
                          write_state='applied', proposal=proposal, evidence=publication)
            result_raw = _bounded(result)
            with self.condition:
                try:
                    _check(self._entry(binding_id, generation, True) is entry and record['state']=='responding', 'request_cancelled')
                except IdeaError as exc:
                    # Evidence may already exist; cancellation disables live
                    # eligibility but cannot imply that publication rolled back.
                    raise IdeaError('proposal_commit_uncertain', 'Proposal publication is uncertain',
                                    committed=True, write_state='committed_uncertain') from exc
                record.update(state='completed', response=raw, result=result)
                entry['bytes'] -= record['reserved']-(len(raw)+len(result_raw))+record['fill_bytes']
                record.update(fill_bytes=0, fills=[])
                entry['activity'] = entry['heartbeat'] = self.clock()
                self.condition.notify_all()
                return copy.deepcopy(result)
        except Exception as exc:
            with self.condition:
                if record['state']=='responding':
                    record['state']='pending'
                self.condition.notify_all()
            if pinned and not (isinstance(exc, IdeaError) and exc.details.get('committed')):
                # Earlier publication may exist. Failed reconciliation cannot
                # honestly report that nothing applied.
                raise IdeaError('proposal_commit_uncertain', 'Proposal publication is uncertain',
                                committed=True, write_state='committed_uncertain') from exc
            raise
