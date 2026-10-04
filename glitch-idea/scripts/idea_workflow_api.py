"""Where ideas live (Setup) and the workflow API client: glitch-idea's side of Option 2.

Option 1 ("native") keeps ideas as Markdown in the Glitch store (today's behaviour).
Option 2 ("api") points glitch-idea at the operator's own workflow system through the
contract in docs/workflow-api.md. This module holds the per-store setting and the
stdlib client. The API key is written once and never returned: readers see only
whether one is set. Settings live in an owner-private folder under the runtime root,
checked the same way as the runtime itself (0700 folder, 0600 file, no symlinks).

Security rules: https anywhere, plain http only to a loopback host (the key never
crosses a network in clear text); no credentials or query in the URL; redirects are
never followed; replies are bounded JSON and only fixed codes reach callers.
"""
import http.client
import ipaddress
import json
import os
import re
import secrets
from urllib.parse import quote, unquote, urlsplit

from idea_domain import IdeaError
from idea_runtime import RuntimeError as RuntimeFault, _json, _open_chain, _private_file, _raw

SCHEMA = 'glitch-idea.workflow/1'
WHERE = ('native', 'api')
MAX_KEY = 512
MAX_URL = 2000
MAX_REPLY = 1024 * 1024
IDEA = re.compile(r'idea_[0-9a-f]{32}')
KEY = re.compile(r'[\x21-\x7e]{8,512}')


class WorkflowError(Exception):
    """A fixed code only: never reply text, never the key."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _fail(code):
    raise IdeaError(code, 'Workflow settings: ' + code)


def _loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_base_url(value):
    """https://host[:port][/path], or http:// only to a loopback host."""
    if type(value) is not str or not 0 < len(value) <= MAX_URL or any(ord(char) < 33 or ord(char) > 126 for char in value):
        _fail('invalid_url')
    parts = urlsplit(value)
    if (parts.scheme not in ('https', 'http') or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment or '@' in parts.netloc):
        _fail('invalid_url')
    if parts.scheme == 'http' and not _loopback(parts.hostname):
        _fail('insecure_url')
    try:
        parts.port
    except ValueError:
        _fail('invalid_url')
    if any(unquote(segment) in ('.', '..') for segment in parts.path.split('/')):
        _fail('invalid_url')  # no dot segments: the base path must not be escapable
    return value.rstrip('/')


def _strings(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if type(item) is str:
            yield item
        elif type(item) is dict:
            pending.extend(item.keys()); pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)


class WorkflowSettings:
    """One store's Setup choice in an owner-private file; the key is write-only."""

    def __init__(self, runtime_root, store_sha256):
        if type(store_sha256) is not str or re.fullmatch(r'[0-9a-f]{64}', store_sha256) is None:
            _fail('invalid_settings')
        self.folder = runtime_root / 'settings'
        self.name = store_sha256 + '.json'

    def _folder(self):
        try:
            return _open_chain(self.folder, create=True, private_leaf=True)
        except (OSError, RuntimeFault):
            _fail('settings_not_private')

    def _read(self):
        directory = self._folder()
        try:
            try:
                value = _json(_raw(directory, self.name))
            except FileNotFoundError:
                return dict(where='native', base_url=None, key=None)
            except (OSError, RuntimeFault):
                _fail('settings_not_private')
        finally:
            os.close(directory)
        if (type(value) is not dict or set(value) != {'schema', 'where', 'base_url', 'key'} or value['schema'] != SCHEMA
                or value['where'] not in WHERE or not (value['base_url'] is None or type(value['base_url']) is str)
                or not (value['key'] is None or (type(value['key']) is str and KEY.fullmatch(value['key'])))):
            _fail('corrupt_settings')
        return dict(where=value['where'], base_url=value['base_url'], key=value['key'])

    def public(self):
        value = self._read()
        return dict(where=value['where'], api=dict(base_url=value['base_url'], key_set=value['key'] is not None))

    def client(self, timeout=5):
        value = self._read()
        if value['base_url'] is None or value['key'] is None:
            _fail('api_not_configured')
        return WorkflowClient(value['base_url'], value['key'], timeout=timeout)

    def save(self, where, base_url, key):
        """key: None keeps the stored key, '' clears it, a string replaces it."""
        if where not in WHERE:
            _fail('invalid_settings')
        current = self._read()
        url = None if base_url is None else check_base_url(base_url)
        if key is None:
            stored = current['key']
        elif key == '':
            stored = None
        elif type(key) is str and KEY.fullmatch(key):
            stored = key
        else:
            _fail('invalid_key')
        if where == 'api' and (url is None or (stored is None and key != '')):  # an explicit removal is allowed in API mode
            _fail('api_needs_url_and_key')
        raw = (json.dumps(dict(schema=SCHEMA, where=where, base_url=url, key=stored), sort_keys=True,
                          separators=(',', ':')) + '\n').encode('utf-8')
        directory = self._folder()
        temporary = '.settings_' + secrets.token_hex(16) + '.tmp'
        try:
            try:
                _raw(directory, self.name)  # an existing target must be a private regular file
            except FileNotFoundError:
                pass
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 0o600, dir_fd=directory)
            try:
                _private_file(descriptor)
                offset = 0
                while offset < len(raw):
                    written = os.write(descriptor, raw[offset:])
                    if written <= 0:
                        raise OSError('short write')
                    offset += written
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, self.name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        except (OSError, RuntimeFault):
            try:
                os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass
            _fail('settings_not_private')
        finally:
            os.close(directory)
        return self.public()


class WorkflowClient:
    """The documented contract only: fixed paths, bearer key, bounded JSON, no redirects."""

    def __init__(self, base_url, key, timeout=5):
        self.base = urlsplit(check_base_url(base_url))
        if type(key) is not str or KEY.fullmatch(key) is None:
            raise WorkflowError('invalid_key')
        self.__key = key
        self.timeout = timeout

    def __repr__(self):
        return '<WorkflowClient ' + self.base.scheme + '://' + (self.base.hostname or '') + '>'

    def _request(self, method, path, body=None):
        connection_type = http.client.HTTPSConnection if self.base.scheme == 'https' else http.client.HTTPConnection
        raw = None if body is None else json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        headers = {'Authorization': 'Bearer ' + self.__key, 'Accept': 'application/json'}
        if raw is not None:
            headers['Content-Type'] = 'application/json'
        connection = connection_type(self.base.hostname, self.base.port, timeout=self.timeout)
        try:
            connection.request(method, self.base.path + path, body=raw, headers=headers)
            response = connection.getresponse()
            data = response.read(MAX_REPLY + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise WorkflowError('tls_failed' if 'SSL' in type(exc).__name__ else 'unreachable') from None
        finally:
            connection.close()
        if 300 <= response.status < 400:
            raise WorkflowError('redirect_refused')
        if response.status in (401, 403):
            raise WorkflowError('unauthorized')
        if response.status == 404:
            raise WorkflowError('not_found')
        if len(data) > MAX_REPLY or response.getheader('Content-Type', '').split(';')[0].strip() != 'application/json':
            raise WorkflowError('invalid_response')
        try:
            value = json.loads(data.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            raise WorkflowError('invalid_response') from None
        if response.status != 200 or type(value) is not dict or value.get('ok') is not True:
            raise WorkflowError('refused')
        # Never pass a credential onward: look in every decoded string and in the raw reply.
        if self.__key in data.decode('utf-8') or any(self.__key in text for text in _strings(value)):
            raise WorkflowError('invalid_response')
        return value

    def health(self):
        value = self._request('GET', '/v1/health')
        if value.get('schema') != SCHEMA or type(value.get('service')) is not str or len(value['service']) > 200:
            raise WorkflowError('schema_mismatch')
        return dict(service=value['service'], schema=value['schema'])

    def list_ideas(self):
        value = self._request('GET', '/v1/ideas')
        ideas = value.get('ideas')
        if type(ideas) is not list or len(ideas) > 4096 or not all(type(item) is dict and IDEA.fullmatch(str(item.get('idea_id', ''))) for item in ideas):
            raise WorkflowError('invalid_response')
        return ideas

    def get_idea(self, idea_id):
        if type(idea_id) is not str or IDEA.fullmatch(idea_id) is None:
            raise WorkflowError('invalid_idea')
        value = self._request('GET', '/v1/ideas/' + quote(idea_id, safe=''))
        if type(value.get('idea')) is not dict or value['idea'].get('idea_id') != idea_id:
            raise WorkflowError('invalid_response')
        return value['idea']

    def put_idea(self, record):
        if type(record) is not dict or type(record.get('idea_id')) is not str or IDEA.fullmatch(record['idea_id']) is None:
            raise WorkflowError('invalid_idea')
        value = self._request('PUT', '/v1/ideas/' + quote(record['idea_id'], safe=''), dict(schema=SCHEMA, idea=record))
        if value.get('idea_id') != record['idea_id'] or type(value.get('ref')) is not str or len(value['ref']) > 200:
            raise WorkflowError('invalid_response')
        return dict(idea_id=value['idea_id'], ref=value['ref'])
