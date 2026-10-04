"""Trusted browser binding policy.

Runtime/launcher supplies initialized durable receipt IDs and owner-private
persistence. NEW alone calls Store.create_session outside this module; resume
never does. Process-owner probe secrets are NOT agent provenance credentials.
Registry locks never wait for a binding lock. Bridge holds the stable binding
lock across reauthorization, application publication and selection persistence.
No Windows privacy guarantee or agent event endpoint is provided here.
"""
import copy
from dataclasses import dataclass, field
import hmac
import math
import re
import secrets
import threading
import time

from idea_bridge import ApplicationBinding, BridgeError, Response, TrustedSessionPolicy, check
from idea_service import Service

BINDING = re.compile(r'binding_[0-9a-f]{32}')
SESSION = re.compile(r'session_[0-9a-f]{32}')
IDEA = re.compile(r'idea_[0-9a-f]{32}')
HEX = re.compile(r'[0-9a-f]+')
AGENT = re.compile(r'agent_[0-9a-f]{32}')
AGENT_BROWSER_HEADERS = ('Cookie', 'Origin', 'X-CSRF-Token', 'X-Idea-Binding', 'X-Idea-Tab')


@dataclass(frozen=True)
class AgentBinding:
    """Authenticated nonsecret incarnation; never contains a credential.

    Agent transport captures this context, releases binding/Store locks before
    events(), then rechecks before short response/close work. No lock or token
    is part of its representation. It is not browser authorization.
    """
    binding: ApplicationBinding = field(repr=False, compare=False)
    binding_id: str
    generation: str
    session_id: str


@dataclass(eq=False, repr=False)
class _Entry:
    binding: ApplicationBinding
    record: dict
    active: bool = False
    cookie: str | None = None
    csrf: str | None = None
    tab_secret: str | None = None
    agent_token: str | None = None
    agent_generation: str | None = None
    pairing_code: str | None = None
    expires: float = 0
    attempts: int = 0
    redeemed: bool = False
    transport: dict | None = None


class SessionPolicy(TrustedSessionPolicy):
    """Only trusted native callers open/resume bindings and issue codes.

    factory(detached_record)->Service; persist(detached_record)->None. Persist
    must durably save before returning. cancel(binding_id,live_generation)
    cancels stale outstanding agent work; it never receives browser secrets.
    agent_credentials is trusted-only and must never be routed to the browser.
    Optional agent_open(id,generation,SID) and agent_state(id,generation) are
    short broker-only callbacks: no Store/network/wait. They run under binding
    lock, outside registry. Lock order is binding -> Store or broker; broker
    must never call back into binding/registry while holding its Condition.
    agent_state returns the exact Broker.status schema, without lease renewal.
    Optional agent_activity(id,generation) is the same kind of callback: a
    committed browser save, a pairing or a typing ping tells the broker a human
    is working, so the agent does not idle out while the human fills a step.
    memory_capability advertises a supported result adapter, never retrieval truth.
    """
    def __init__(self, application_factory, persist_binding, *, namespace,
                 clock=time.monotonic, entropy=secrets.token_hex, max_bindings=8,
                 pairing_ttl=60, max_attempts=5, cancel=None,
                 agent_open=None, agent_state=None, agent_activity=None, memory_capability=False):
        check(callable(application_factory) and callable(persist_binding), 'invalid_policy')
        check(type(memory_capability) is bool, 'invalid_policy')
        self._memory_capability = memory_capability
        check(type(namespace) is str and re.fullmatch(r'[a-z0-9_]{1,64}', namespace), 'invalid_policy')
        check(type(max_bindings) is int and 1 <= max_bindings <= 8, 'invalid_policy')
        check(type(max_attempts) is int and 1 <= max_attempts <= 5, 'invalid_policy')
        check(type(pairing_ttl) in (int,float) and math.isfinite(pairing_ttl) and 0 < pairing_ttl <= 60, 'invalid_policy')
        check(callable(clock) and callable(entropy) and (cancel is None or callable(cancel)), 'invalid_policy')
        self.factory, self.persist = application_factory, persist_binding
        self.namespace, self.clock, self.entropy = namespace, clock, entropy
        self.limit, self.ttl, self.max_attempts = max_bindings, pairing_ttl, max_attempts
        check((agent_open is None or callable(agent_open)) and
              (agent_state is None or callable(agent_state)) and
              (agent_activity is None or callable(agent_activity)), 'invalid_policy')
        self.cancel, self.agent_open, self.agent_state = cancel, agent_open, agent_state
        self.agent_activity = agent_activity
        self._registry = threading.RLock()
        self._entries = {}

    def _secret(self, size=32):
        value = self.entropy(size)
        check(type(value) is str and len(value) == size*2 and HEX.fullmatch(value), 'invalid_entropy', 500)
        return value

    def _entry(self, binding_id):
        check(type(binding_id) is str and BINDING.fullmatch(binding_id), 'invalid_binding', 400)
        with self._registry:
            entry = self._entries.get(binding_id)
        check(entry is not None, 'browser_unauthorized', 401)
        return entry

    def _locked(self, entry):
        # Caller releases registry before this potentially blocking acquisition.
        check(entry.binding.lock.acquire(timeout=10), 'busy', 503)
        return entry.binding.lock

    def _invalidate(self, entry):
        generation = entry.agent_generation
        with self._registry:
            entry.active = False
            entry.cookie = entry.csrf = entry.tab_secret = entry.agent_token = None
            entry.transport = None
        if generation is not None and self.cancel is not None:
            try:
                self.cancel(entry.record['binding_id'], generation)
            except Exception:
                raise BridgeError('agent_cancel_failed', 503) from None

    def open_binding(self, actor, receipt_session_id, selected_idea_id=None, *, binding_id=None, resume=False):
        """Initialize/resume an explicit trusted record; return nonsecret ID.

        Restart passes the same persisted binding ID/SID. A missing SID fails
        through Service.state before any credentials/code become available.
        Failure during resume keeps old credentials revoked, never revives them.
        """
        check(type(actor) is str and 0 < len(actor) <= 200 and not any(0xD800 <= ord(c) <= 0xDFFF for c in actor), 'invalid_binding')
        check(type(receipt_session_id) is str and SESSION.fullmatch(receipt_session_id), 'invalid_binding')
        check(selected_idea_id is None or (type(selected_idea_id) is str and IDEA.fullmatch(selected_idea_id)), 'invalid_binding')
        check(type(resume) is bool, 'invalid_binding')
        check((resume and type(binding_id) is str and BINDING.fullmatch(binding_id)) or (not resume and binding_id is None), 'invalid_binding')
        binding_id = binding_id if resume else 'binding_' + self._secret(16)
        record = dict(schema_version=1,binding_id=binding_id,actor=actor,
                      receipt_session_id=receipt_session_id,selected_idea_id=selected_idea_id)
        with self._registry:
            entry = self._entries.get(binding_id)
            created = entry is None
            if created:
                check(len(self._entries) < self.limit, 'session_capacity_exhausted', 503)
                entry = _Entry(ApplicationBinding(None),copy.deepcopy(record))
                self._entries[binding_id] = entry
            else:
                check(resume and entry.record['actor'] == actor and entry.record['receipt_session_id'] == receipt_session_id,
                      'session_binding_mismatch', 403)
        lock = self._locked(entry)
        generation = None
        try:
            self._invalidate(entry)
            application = self.factory(copy.deepcopy(record))
            check(isinstance(application,Service) and application.context.actor == actor
                  and application.context.session_id == receipt_session_id
                  and application.context.selected_idea_id == selected_idea_id, 'invalid_application', 500)
            application.state()  # Validates actual receipt file and selected idea.
            self.persist(copy.deepcopy(record))  # SID before any credential exposure.
            generation = 'agent_' + self._secret(16)
            token = self._secret()
            if self.agent_open is not None:
                try:
                    self.agent_open(binding_id, generation, receipt_session_id)
                except Exception:
                    raise BridgeError('agent_open_failed', 503) from None
            with self._registry:
                entry.binding.application, entry.record = application, copy.deepcopy(record)
                entry.agent_generation, entry.agent_token = generation, token
                entry.pairing_code = None
                entry.redeemed = False
                entry.attempts = 0
            return binding_id
        except Exception:
            # Callback may have opened its generation before failing. Revoke
            # locally first, then cancel it without registry lock/credential exposure.
            with self._registry:
                entry.agent_token = None
            if generation is not None and self.cancel is not None:
                try:
                    self.cancel(binding_id, generation)
                except Exception:
                    pass  # Original failure remains; local authorization is gone.
            if created:
                with self._registry:
                    if self._entries.get(binding_id) is entry:
                        del self._entries[binding_id]
            raise
        finally:
            lock.release()

    def issue_pairing(self, binding_id):
        """Trusted pane-only bootstrap; one challenge per live generation."""
        entry = self._entry(binding_id); lock = self._locked(entry)
        try:
            with self._registry:
                check(entry.pairing_code is None, 'pairing_resume_required', 409)
                check(entry.agent_token is not None, 'browser_unauthorized', 401)
                code = self._secret(16)
                check(all(other.pairing_code != code for other in self._entries.values()), 'invalid_entropy', 500)
                entry.pairing_code, entry.expires = code, self.clock()+self.ttl
                return code
        finally:
            lock.release()

    def agent_credentials(self, binding_id):
        entry = self._entry(binding_id); lock = self._locked(entry)
        try:
            with self._registry:
                check(entry.agent_token is not None, 'browser_unauthorized', 401)
                return dict(binding_id=binding_id,session_id=entry.record['receipt_session_id'],
                            generation=entry.agent_generation,token=entry.agent_token)
        finally:
            lock.release()

    def authorize_agent(self, request):
        """Separate private-header authorization; pairing is not a prerequisite.

        Transport pins Host, rejects duplicate headers and bounds admission.
        This policy validates the normalized single-header values only.
        Recheck this captured incarnation under binding lock before dispatch;
        never hold that lock across a broker event wait.
        """
        check(all(request.header(name) is None for name in AGENT_BROWSER_HEADERS),
              'agent_browser_headers_refused', 403)
        binding_id = request.header('X-Idea-Agent-Binding')
        generation = request.header('X-Idea-Agent-Generation')
        authorization = request.header('Authorization')
        check(type(binding_id) is str and BINDING.fullmatch(binding_id) and
              type(generation) is str and AGENT.fullmatch(generation), 'agent_unauthorized', 401)
        check(type(authorization) is str and re.fullmatch(r'Bearer [0-9a-f]{64}',authorization),
              'agent_unauthorized', 401)
        token = authorization[7:]
        with self._registry:
            entry = self._entries.get(binding_id)
            check(entry is not None and entry.agent_generation == generation and
                  entry.agent_token is not None and hmac.compare_digest(token, entry.agent_token),
                  'agent_unauthorized', 401)
            return AgentBinding(entry.binding, binding_id, generation, entry.record['receipt_session_id'])

    def recheck_agent(self, context):
        """Serialize and revalidate a captured context; return its stable binding.

        Reentrant under an already-held binding lock. This short check does not
        renew heartbeat, accept browser auth or wait for external work.
        """
        check(type(context) is AgentBinding and isinstance(context.binding, ApplicationBinding) and
              type(context.binding_id) is str and BINDING.fullmatch(context.binding_id) and
              type(context.generation) is str and AGENT.fullmatch(context.generation) and
              type(context.session_id) is str and SESSION.fullmatch(context.session_id),
              'agent_unauthorized', 401)
        check(context.binding.lock.acquire(timeout=10), 'busy', 503)
        try:
            with self._registry:
                entry = self._entries.get(context.binding_id)
                check(entry is not None and entry.binding is context.binding and
                      entry.agent_generation == context.generation and entry.agent_token is not None and
                      entry.record['receipt_session_id'] == context.session_id, 'agent_unauthorized', 401)
            return context.binding
        finally:
            context.binding.lock.release()

    def close_agent(self, context):
        """Close only agent provenance; paired browser credentials/draft survive."""
        check(type(context) is AgentBinding and isinstance(context.binding, ApplicationBinding),
              'agent_unauthorized', 401)
        check(context.binding.lock.acquire(timeout=10), 'busy', 503)
        try:
            self.recheck_agent(context)
            with self._registry:
                entry = self._entries[context.binding_id]
                entry.agent_token = None
            if self.cancel is not None:
                try:
                    self.cancel(context.binding_id, context.generation)
                except Exception:
                    raise BridgeError('agent_cancel_failed', 503) from None
            return dict(ok=True,code='ok',session_id=context.session_id,agent_status='disconnected')
        finally:
            context.binding.lock.release()

    def _agent_projection(self, entry):
        """Strict allowlist projection; callback strings/keys never flow outward."""
        status = 'disconnected'
        if entry.agent_token is not None and self.agent_state is not None:
            try:
                result = self.agent_state(entry.record['binding_id'], entry.agent_generation)
            except Exception:
                raise BridgeError('agent_state_failed', 503) from None
            check(type(result) is dict and set(result) ==
                  {'ok','code','session_id','agent_status','reason','sequence'}, 'invalid_agent_state', 500)
            check(result['ok'] is True and type(result['code']) is str and result['code'] == 'ok' and
                  type(result['session_id']) is str and result['session_id'] == entry.record['receipt_session_id'] and
                  type(result['sequence']) is int and 0 <= result['sequence'] <= 10**12 and
                  type(result['agent_status']) is str and result['agent_status'] in
                  ('connected','paused','disconnected'), 'invalid_agent_state', 500)
            status, reason = result['agent_status'], result['reason']
            check((status=='connected' and reason is None) or
                  (status=='paused' and type(reason) is str and reason=='idle') or
                  (status=='disconnected' and type(reason) is str and reason in
                   ('closed','rebound','invalidated','shutdown','heartbeat_expired')),
                  'invalid_agent_state', 500)
        return dict(agent_status=status,
                    resume=dict(required=status!='connected', reason=None if status=='connected' else
                                'agent_paused' if status=='paused' else 'agent_disconnected'),
                    capabilities=dict(agent=status=='connected',memory=self._memory_capability and status=='connected',uploads=False,handoff=False))

    def revoke(self, binding_id):
        entry = self._entry(binding_id); lock = self._locked(entry)
        try:
            self._invalidate(entry)
        finally:
            lock.release()

    def _cookie_name(self, entry):
        return 'gi_' + self.namespace + '_' + entry.record['binding_id'][8:]

    def authorize(self, request, *, write=False):
        selector = request.header('X-Idea-Binding')
        check(selector is not None, 'browser_unauthorized', 401)
        entry = self._entry(selector)
        raw = request.header('Cookie') or ''
        check(type(raw) is str and len(raw) <= 16384, 'browser_unauthorized', 401)
        values = []
        for item in raw.split(';'):
            name, equal, value = item.strip().partition('=')
            if name == self._cookie_name(entry) and equal:
                values.append(value)
        # Per-tab secret: cookies are not port-scoped, so the cookie alone never authorises.
        tab = request.header('X-Idea-Tab')
        with self._registry:
            check(entry.active and len(values) == 1 and entry.cookie is not None
                  and values[0].isascii() and hmac.compare_digest(values[0], entry.cookie), 'browser_unauthorized', 401)
            check(type(tab) is str and 0 < len(tab) <= 256 and tab.isascii() and entry.tab_secret is not None
                  and hmac.compare_digest(tab, entry.tab_secret), 'browser_unauthorized', 401)
            if write:
                csrf = request.header('X-CSRF-Token')
                check(type(csrf) is str and csrf.isascii() and entry.csrf is not None and hmac.compare_digest(csrf,entry.csrf), 'wrong_csrf', 403)
            return entry.binding

    def pair(self, request, payload):
        check(type(payload) is dict and set(payload) == {'code'} and type(payload['code']) is str
              and 0 < len(payload['code']) <= 128, 'invalid_input')
        selector = request.header('X-Idea-Binding')
        if selector is not None:
            check(type(selector) is str and BINDING.fullmatch(selector), 'invalid_binding')
        with self._registry:
            matches = [e for e in self._entries.values() if e.pairing_code is not None
                       and payload['code'].isascii() and hmac.compare_digest(e.pairing_code,payload['code'])]
            entry = matches[0] if matches else None
            if entry is not None and selector is not None:
                check(selector == entry.record['binding_id'], 'session_binding_mismatch', 403)
            if entry is None:
                # Unknown unpinned codes consume all outstanding challenges' budgets;
                # this bounds guesses without an unauthenticated binding oracle.
                candidates = [e for e in self._entries.values() if e.pairing_code is not None and (selector is None or e.record['binding_id'] == selector)]
                for candidate in candidates:
                    if not candidate.redeemed:
                        candidate.attempts = min(self.max_attempts,candidate.attempts+1)
                locked = bool(candidates) and all(e.attempts >= self.max_attempts for e in candidates)
                raise BridgeError('pairing_expired_or_locked' if locked else 'wrong_pairing_code',401)
        lock = self._locked(entry)
        try:
            with self._registry:
                check(entry.pairing_code == payload['code'], 'pairing_expired_or_locked',401)
                if entry.redeemed:
                    replay = True
                else:
                    replay = False
                    expired = not (self.clock() < entry.expires and entry.attempts < self.max_attempts
                                   and entry.agent_token is not None)
            if not replay and expired:
                self._invalidate(entry)
                raise BridgeError('pairing_expired_or_locked',401)
            if replay:
                self._invalidate(entry)
                raise BridgeError('pairing_replay_session_invalidated',401)
            cookie, csrf, tab = self._secret(), self._secret(), self._secret()
            with self._registry:
                entry.cookie, entry.csrf, entry.tab_secret = cookie, csrf, tab
                entry.active, entry.redeemed = True, True
            if self.agent_activity is not None:
                # A human just paired: the agent opened for them must not idle out first.
                self.agent_activity(entry.record['binding_id'], entry.agent_generation)
            secure = '; Secure' if request.origin.startswith('https://') else ''
            return Response(dict(ok=True,code='ok',binding_id=entry.record['binding_id'],
                                 session_id=entry.record['receipt_session_id'],csrf_token=csrf,tab_secret=tab),
                headers={'Set-Cookie': self._cookie_name(entry)+'='+cookie+'; Path=/; HttpOnly; SameSite=Strict'+secure})
        finally:
            lock.release()

    def _for_binding(self,binding):
        with self._registry:
            entry = next((e for e in self._entries.values() if e.binding is binding),None)
            check(entry is not None and entry.active,'browser_unauthorized',401)
            return entry

    def session(self, binding):
        entry = self._for_binding(binding); lock = self._locked(entry)
        try:
            entry = self._for_binding(binding)  # Recheck after acquiring bound lock.
            # Credential auth does not replace durable session existence.
            with binding.application.store.transaction():
                binding.application.store._read_receipts(entry.record['receipt_session_id'])
            projection = self._agent_projection(entry)  # No Store/registry lock here.
            with self._registry:
                return dict(ok=True,code='ok',binding_id=entry.record['binding_id'],
                            session_id=entry.record['receipt_session_id'],csrf_token=entry.csrf,
                            **projection)
        finally:
            lock.release()

    def transport(self,binding,payload):
        entry = self._for_binding(binding)
        check(type(payload) is dict and set(payload) == {'host','origin','secure_context'}
              and type(payload['host']) is str and type(payload['origin']) is str
              and type(payload['secure_context']) is bool, 'transport_mismatch',403)
        with self._registry:
            entry.transport = copy.deepcopy(payload)
        return dict(ok=True,code='ok')

    def activity(self,binding):
        entry = self._for_binding(binding)
        if self.agent_activity is not None and entry.agent_token is not None:
            self.agent_activity(entry.record['binding_id'], entry.agent_generation)
        return dict(ok=True,code='ok')

    def after_application(self,binding,result):
        entry = self._for_binding(binding)
        context = binding.application.context
        check(context.session_id == entry.record['receipt_session_id'] and context.actor == entry.record['actor'], 'session_binding_mismatch',500)
        record = dict(entry.record,selected_idea_id=context.selected_idea_id)
        if record == entry.record:
            return  # Nothing changed: no staged rewrite for every page read (fewer client settles).
        self.persist(copy.deepcopy(record))
        with self._registry:
            entry.record = record
