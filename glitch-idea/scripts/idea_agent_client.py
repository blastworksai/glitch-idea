"""Fixed native agent client; credentials remain internal.

No generic URL, route, file, shell, retry or session creation capability. Runtime
alone opens/qualifies the private discovery channel. Every socket is loopback.
"""
import copy
import json
import math
import re
import socket
import time
from urllib.parse import urlencode

from idea_bridge import BridgeError, decode_json
from idea_domain import IdeaError
from idea_proposals import CORRELATION, LIMIT, OPERATIONS, _bounded
from idea_runtime import Runtime


# The service's own fixed refusal codes an agent may act on; any other refusal stays
# agent_request_failed. Codes only: reply text and fields never reach output.
AGENT_REFUSALS = frozenset(('agent_unavailable', 'agent_unauthorized', 'wrong_generation', 'wrong_session',
                            'request_not_found', 'request_cancelled', 'request_closed', 'request_not_delivered',
                            'response_mismatch', 'response_conflict', 'response_busy', 'response_capacity',
                            'invalid_fill', 'invalid_proposal', 'fill_capacity', 'stale_source',
                            'proposal_commit_uncertain', 'operation_unavailable', 'busy'))


class AgentClientError(Exception):
    """Fixed redacted diagnostic, never response text or reusable credentials."""
    def __init__(self, code, *, write_state=None):
        super().__init__(code)
        self.code, self.write_state = code, write_state


def _check(condition, code='invalid_agent_response'):
    if not condition:
        raise AgentClientError(code)


def _integer(value, minimum=0):
    return type(value) is int and minimum <= value <= 10**12


def _identifier(value, prefix):
    return type(value) is str and re.fullmatch(prefix + r'_[0-9a-f]{32}', value) is not None


def _correlation(value, session_id):
    _check(all(key in value for key in CORRELATION))
    _check(value['session_id'] == session_id and _identifier(value['idea_id'], 'idea')
           and type(value['request_id']) is str
           and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value['request_id']) is not None
           and _integer(value['accepted_revision'], 1) and _integer(value['draft_version'])
           and type(value['operation']) is str and value['operation'] in OPERATIONS
           and type(value['source_digest']) is str and re.fullmatch(r'[0-9a-f]{64}', value['source_digest']))


class AgentClient:
    __slots__ = ('__channel', '__closed')

    def __init__(self, runtime, session_id, *, expected_generation=None, timeout=1):
        _check(type(runtime) is Runtime, 'invalid_agent_input')
        # RuntimeError carries only a fixed code, never raw credential data.
        self.__channel = runtime._agent_channel(session_id, expected_generation, timeout)
        self.__closed = False

    def __repr__(self):
        return '<AgentClient private native channel>'

    def events(self, after=0, timeout=25):
        _check(_integer(after) and type(timeout) in (int, float) and math.isfinite(timeout)
               and 0 <= timeout <= 25, 'invalid_agent_input')
        query = urlencode(dict(session_id=self.__channel._session_id, after=after, timeout=format(timeout, '.6f')))
        value = self.__request('events', query=query, timeout=timeout + 2)
        _check(set(value) == {'ok','code','session_id','agent_status','reason','sequence','events'})
        _check(value['session_id'] == self.__channel._session_id and _integer(value['sequence'])
               and value['sequence'] >= after and type(value['agent_status']) is str
               and value['agent_status'] in ('connected','paused','disconnected')
               and type(value['events']) is list and len(value['events']) <= 128)
        status, reason = value['agent_status'], value['reason']
        _check((status == 'connected' and reason is None) or (status == 'paused' and reason == 'idle')
               or (status == 'disconnected' and type(reason) is str and reason in
                   ('closed','rebound','invalidated','shutdown','heartbeat_expired')))
        _check(status == 'connected' or not value['events'])
        last = after
        for event in value['events']:
            _check(type(event) is dict and set(event) == CORRELATION | {'sequence','data'})
            _correlation(event, self.__channel._session_id)
            _check(_integer(event['sequence'], 1) and last < event['sequence'] <= value['sequence']
                   and type(event['data']) is dict)
            last = event['sequence']
        return value

    def respond(self, payload):
        try:
            return self.__respond(payload)
        except AgentClientError as exc:
            if exc.write_state is None and exc.code not in ('invalid_agent_input', 'agent_closed'):
                exc.write_state = 'committed_uncertain'
            raise

    def __respond(self, payload):
        try:
            raw = _bounded(payload)
            _check(type(payload) is dict and set(payload) == CORRELATION | {'proposal'}, 'invalid_agent_input')
            _correlation(payload, self.__channel._session_id)
            _check(type(payload['proposal']) is dict, 'invalid_agent_input')
        except (IdeaError, AgentClientError):
            raise AgentClientError('invalid_agent_input') from None
        value = self.__request('respond', raw=raw)
        _check(set(value) == CORRELATION | {'ok','code','status','write_state','proposal','evidence'})
        _correlation(value, self.__channel._session_id)
        _check(all(type(value[k]) is type(payload[k]) and value[k] == payload[k] for k in CORRELATION)
               and value['status'] == 'completed' and value['write_state'] == 'applied'
               and type(value['proposal']) is dict)
        evidence = value['evidence']
        _check(type(evidence) is dict and set(evidence) in
               ({'proposal_id','sha256'}, {'proposal_id','sha256','path'}))
        _check(type(evidence['proposal_id']) is str and
               re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', evidence['proposal_id']) is not None
               and type(evidence['sha256']) is str and re.fullmatch(r'[0-9a-f]{64}', evidence['sha256']))
        if 'path' in evidence:
            path = evidence['path']
            _check(type(path) is str and 0 < len(path) <= 200 and not path.startswith('/')
                   and '\\' not in path and all(part not in ('', '.', '..') for part in path.split('/')))
        return value

    def fill(self, payload):
        """Send fields agreed with the human in the terminal; the page applies them."""
        try:
            raw = _bounded(payload)
            _check(type(payload) is dict and set(payload) == CORRELATION | {'fields'}, 'invalid_agent_input')
            _correlation(payload, self.__channel._session_id)
            _check(type(payload['fields']) is dict and bool(payload['fields']), 'invalid_agent_input')
        except (IdeaError, AgentClientError):
            raise AgentClientError('invalid_agent_input') from None
        value = self.__request('fill', raw=raw)
        _check(set(value) == {'ok','code','request_id','operation','status','write_state','fill_sequence'}
               and value['request_id'] == payload['request_id'] and value['operation'] == payload['operation']
               and value['status'] == 'pending' and value['write_state'] == 'not_applied'
               and _integer(value['fill_sequence'], 1))
        return value

    def session_close(self):
        value = self.__request('session-close', raw=json.dumps(
            dict(session_id=self.__channel._session_id), separators=(',', ':')).encode())
        _check(set(value) == {'ok','code','session_id','agent_status'}
               and value['session_id'] == self.__channel._session_id and value['agent_status'] == 'disconnected')
        self.__closed = True
        return value

    def __request(self, operation, *, query=None, raw=None, timeout=5):
        _check(not self.__closed, 'agent_closed')
        channel = self.__channel
        path = '/agent/v1/' + operation + ('?' + query if query is not None else '')
        method = 'GET' if operation == 'events' else 'POST'
        headers = {'Host': '127.0.0.1:' + str(channel._port), 'Connection': 'close', **channel._headers()}
        if raw is not None:
            headers.update({'Content-Type': 'application/json', 'Content-Length': str(len(raw))})
        request = (method + ' ' + path + ' HTTP/1.0\r\n' + ''.join(
            name + ': ' + value + '\r\n' for name, value in headers.items()) + '\r\n').encode('ascii')
        deadline = time.monotonic() + timeout
        connection = None
        try:
            connection = socket.create_connection(('127.0.0.1', channel._port), timeout=timeout)
            connection.sendall(request + (raw or b''))
            buffer = bytearray()
            def receive():
                remaining = deadline - time.monotonic()
                _check(remaining > 0, 'agent_unavailable')
                connection.settimeout(remaining)
                chunk = connection.recv(4096)
                _check(chunk and time.monotonic() <= deadline, 'agent_unavailable')
                return chunk
            while b'\r\n\r\n' not in buffer:
                buffer.extend(receive())
                _check(len(buffer) <= 16384 + 4096)
            head, body = bytes(buffer).split(b'\r\n\r\n', 1)
            _check(len(head) <= 16384)
            lines = head.split(b'\r\n')
            match = re.fullmatch(rb'HTTP/1\.[01] ([1-5][0-9]{2}) [\x20-\x7e]*', lines[0])
            _check(match is not None and len(lines) <= 65)
            status = int(match[1])
            parsed = {}
            for line in lines[1:]:
                _check(re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+:[\t\x20-\x7e]*", line))
                key, value = line.split(b':', 1)
                key = key.lower()
                _check(key not in parsed)
                parsed[key] = value.strip()
            length = parsed.get(b'content-length', b'')
            _check(parsed.get(b'content-type') == b'application/json' and
                   re.fullmatch(rb'[0-9]{1,7}', length) and 0 < int(length) <= LIMIT
                   and b'transfer-encoding' not in parsed)
            length = int(length)
            body = bytearray(body)
            while len(body) < length:
                body.extend(receive())
            _check(len(body) == length)
            value = decode_json(bytes(body))
            _bounded(value)
            # Never return arbitrary error text/fields from an HTTP peer.
            if status != 200 or value.get('ok') is not True:
                state = value.get('write_state')
                state = state if type(state) is str and state in ('not_applied','committed_uncertain') else None
                code = value.get('code')
                raise AgentClientError(code if type(code) is str and code in AGENT_REFUSALS else 'agent_request_failed',
                                       write_state=state)
            _check(value.get('code') == 'ok' and not channel._contains_token(
                json.dumps(value, ensure_ascii=False).encode('utf-8')))
            pending = [value]
            while pending:
                item = pending.pop()
                if type(item) is dict:
                    _check(not set(item) & {'token','owner_token','agent_token','pairing_code','csrf_token'})
                    pending.extend(item.values())
                elif type(item) is list:
                    pending.extend(item)
            return copy.deepcopy(value)
        except (BridgeError, IdeaError, ValueError, UnicodeError, RecursionError):
            raise AgentClientError('invalid_agent_response') from None
        except OSError:
            raise AgentClientError('agent_unavailable') from None
        finally:
            if connection is not None:
                connection.close()
