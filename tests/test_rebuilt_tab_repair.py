"""SKILLS-94: an Orca tab whose webview was rebuilt is re-paired in place, never by a typed code.

Orca destroys and rebuilds a browser page's webview (hidden-worktree eviction, an SSH route
re-prepare) and reloads the saved URL: cookies survive, sessionStorage and its tab secret do
not. The trusted pane re-pairs that same page with a fresh one-time fragment code; the agent's
generation is untouched and a tab that is still paired is never rotated. Fake clock and fake
Orca throughout; never the real orca.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'glitch-idea/scripts'
sys.path.insert(0, str(SCRIPTS))
import idea_launch as launch
from idea_bridge import BridgeError, RequestInfo
from idea_native import OrcaBinding
from idea_proposals import Broker
from idea_runtime import Runtime
from idea_service import Service, TrustedContext
from idea_sessions import SessionPolicy
from idea_store import Store

WORKTREE = '12345678-1234-1234-1234-123456789abc::/fixture/project'
BINDING = OrcaBinding(WORKTREE, 'term_fixture', None)


class PolicyRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'ideas', observer='Operator')
        self.sid = self.store.create_session(); self.now = 0.; self.cancelled = []
        self.broker = Broker(clock=lambda: self.now, validate_source=lambda _, s: s,
                             persist_proposal=lambda _: dict(proposal_id='proposal_fixture', sha256='a' * 64))
        def cancel(bid, gen): self.cancelled.append((bid, gen)); self.broker.cancel(bid, gen)
        self.policy = SessionPolicy(lambda r: Service(self.store, {}, TrustedContext(r['actor'], r['receipt_session_id'], r['selected_idea_id'])),
                                    lambda r: None, namespace='fixture', clock=lambda: self.now, cancel=cancel,
                                    agent_open=self.broker.open, agent_state=self.broker.status, agent_activity=self.broker.touch)
        self.bid = self.policy.open_binding('Operator', self.sid)
        self.cred = self.policy.agent_credentials(self.bid)

    def req(self, headers, path='/api/v1/session'):
        return RequestInfo('GET', path, {k.lower(): v for k, v in headers.items()}, 'http://127.0.0.1:1234')

    def pair(self, code):
        response = self.policy.pair(self.req({}), {'code': code})
        cookie = response.headers['Set-Cookie'].split(';', 1)[0]
        return cookie, response.body['tab_secret']

    def browser(self, cookie, tab=None):
        headers = {'X-Idea-Binding': self.bid, 'Cookie': cookie}
        if tab is not None: headers['X-Idea-Tab'] = tab
        return self.policy.authorize(self.req(headers))

    def agent_context(self):
        return self.policy.authorize_agent(self.req({'X-Idea-Agent-Binding': self.cred['binding_id'],
            'X-Idea-Agent-Generation': self.cred['generation'], 'Authorization': 'Bearer ' + self.cred['token']},
            '/agent/v1/events'))

    def test_rebuilt_tab_keeps_cookie_but_not_its_tab_secret(self):
        # The symptom: the rebuilt page sends the binding's live cookie and no tab secret.
        cookie, tab = self.pair(self.policy.issue_pairing(self.bid))
        self.browser(cookie, tab)
        with self.assertRaises(BridgeError) as caught: self.browser(cookie)
        self.assertEqual((caught.exception.code, caught.exception.status), ('browser_unauthorized', 401))

    def test_repair_issues_a_fresh_code_and_leaves_the_agent_alone(self):
        cookie, tab = self.pair(self.policy.issue_pairing(self.bid))
        code = self.policy.repair_browser(self.bid)
        self.assertRegex(code, r'^[0-9a-f]{32}$')
        self.agent_context()                                   # same generation, same token
        self.assertEqual(self.policy.agent_credentials(self.bid)['generation'], self.cred['generation'])
        self.assertEqual(self.cancelled, [])                   # the agent's work was never cancelled
        with self.assertRaises(BridgeError) as caught: self.browser(cookie, tab)
        self.assertEqual(caught.exception.code, 'session_superseded')
        new_cookie, new_tab = self.pair(code)
        self.browser(new_cookie, new_tab)
        self.agent_context()

    def test_repair_code_is_one_time_like_any_pairing_code(self):
        self.pair(self.policy.issue_pairing(self.bid))
        code = self.policy.repair_browser(self.bid)
        self.pair(code)
        with self.assertRaises(BridgeError) as caught: self.pair(code)
        self.assertEqual(caught.exception.code, 'pairing_replay_session_invalidated')

    def test_repair_replaces_an_outstanding_unredeemed_code(self):
        first = self.policy.issue_pairing(self.bid)
        fresh = self.policy.repair_browser(self.bid)
        self.assertNotEqual(first, fresh)
        with self.assertRaises(BridgeError): self.policy.pair(self.req({}), {'code': first})
        self.pair(fresh)

    def test_repair_needs_a_live_agent_and_a_known_binding(self):
        with self.assertRaises(BridgeError) as caught: self.policy.repair_browser('binding_' + '0' * 32)
        self.assertEqual(caught.exception.code, 'browser_unauthorized')
        self.policy.close_agent(self.agent_context())
        with self.assertRaises(BridgeError) as caught: self.policy.repair_browser(self.bid)
        self.assertEqual(caught.exception.code, 'browser_unauthorized')


class FakeOrca:
    """Fake orca CLI: tab create/close, eval of the page's pairing mark, goto."""
    def __init__(self, mark='unpaired', eval_fails=False):
        self.calls = []; self.n = 0; self.mark = mark; self.eval_fails = eval_fails

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        verb = argv[1:3] if argv[1] == 'tab' or argv[1] == 'terminal' else argv[1:2]
        if verb == ['terminal', 'list']:
            result = {'terminals': [{'handle': 'term_fixture', 'worktreeId': WORKTREE, 'connected': True, 'orphaned': False}]}
        elif verb == ['tab', 'create']:
            self.n += 1; result = {'browserPageId': f'page-{self.n}'}
        elif verb == ['tab', 'close']:
            result = {'closed': True}
        elif verb == ['eval']:
            if self.eval_fails:
                return SimpleNamespace(returncode=1, stdout=json.dumps({'ok': False, 'error': {'code': 'browser_page_not_found'}}), stderr='')
            result = {'result': json.dumps(self.mark), 'origin': 'http://127.0.0.1:1/'}
        elif verb == ['goto']:
            result = {'url': argv[argv.index('--url') + 1], 'title': 'Glitch idea'}
        else:
            raise AssertionError(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True, 'result': result}), stderr='')


@unittest.skipUnless(os.name == 'posix', 'owner ACLs are posix only')
class ReconnectInPlaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name) / 'ideas'; self.root = Path(self.temp.name) / 'private'
        self.client = Runtime(self.store, self.root)
        self.children = []; self.lock = threading.Lock(); self.addCleanup(self.cleanup)

    def spawn(self, *a, **k):
        child = subprocess.Popen(*a, **k)
        with self.lock: self.children.append(child)
        return child

    def cleanup(self):
        end = time.monotonic() + 8
        while any(c.poll() is None for c in self.children) and time.monotonic() < end:
            try: self.client.request_owned_stop(timeout=3)
            except Exception: time.sleep(.05)
        for c in self.children: c.wait(timeout=5)

    def open(self, runner, **kw):
        return launch.open_browser_session(self.store, self.root, mode='orca', orcabinding=BINDING,
                                           runner=runner, spawn=self.spawn, **kw)

    def reconnect(self, runner, binding_id):
        return launch.reconnect_browser_session(self.store, self.root, binding_id=binding_id, orcabinding=BINDING,
                                                runner=runner, spawn=self.spawn)

    def test_unpaired_page_is_re_paired_in_place(self):
        first = self.open(FakeOrca())
        runner = FakeOrca(mark='unpaired')
        out = self.reconnect(runner, first['binding_id'])
        self.assertTrue(out['ok'] if 'ok' in out else True)
        self.assertTrue(out['browser']['reconnected'])
        self.assertEqual(out['browser']['browser_page_id'], 'page-1')
        self.assertEqual(out['session_id'], first['session_id'])
        verbs = [c[1] if c[1] not in ('tab', 'terminal') else ' '.join(c[1:3]) for c in runner.calls]
        self.assertEqual(verbs, ['eval', 'goto'])                       # no new tab, no close
        goto = runner.calls[-1]
        self.assertEqual(goto[goto.index('--page') + 1], 'page-1')
        self.assertEqual(goto[goto.index('--worktree') + 1], 'id:' + WORKTREE)
        self.assertRegex(goto[goto.index('--url') + 1], r'^http://127\.0\.0\.1:\d+/#pair=[0-9a-f]{32}$')
        self.assertNotIn('pairing_code', out)                           # the code went to the page only
        self.assertNotIn('fallback_line', out)
        self.assertEqual(json.loads((self.root / 'orca-pages.json').read_text())[first['binding_id']], 'page-1')

    def test_paired_page_is_never_rotated(self):
        first = self.open(FakeOrca())
        runner = FakeOrca(mark='paired')
        out = self.reconnect(runner, first['binding_id'])
        self.assertFalse(out['browser']['reconnected'])
        self.assertEqual(out['browser']['reason'], 'tab_still_paired')
        self.assertEqual([c[1] for c in runner.calls], ['eval'])

    def test_unreadable_or_unrecorded_page_changes_nothing(self):
        first = self.open(FakeOrca())
        runner = FakeOrca(eval_fails=True)
        with self.assertRaises(launch.LaunchError) as caught: self.reconnect(runner, first['binding_id'])
        self.assertEqual(caught.exception.code, 'tab_unreadable')
        self.assertEqual([c[1] for c in runner.calls], ['eval'])
        (self.root / 'orca-pages.json').unlink()
        runner = FakeOrca()
        with self.assertRaises(launch.LaunchError) as caught: self.reconnect(runner, first['binding_id'])
        self.assertEqual(caught.exception.code, 'tab_not_recorded')
        self.assertEqual(runner.calls, [])


class ReconnectCliTests(unittest.TestCase):
    def run_cli(self, *argv):
        completed = subprocess.run([sys.executable, str(SCRIPTS / 'idea.py'), '--store', '/nonexistent/ideas', *argv],
                                   capture_output=True, text=True, timeout=20)
        return json.loads(completed.stdout)

    def test_reconnect_is_orca_only_and_exclusive_with_resume(self):
        binding = 'binding_' + '1' * 32
        out = self.run_cli('browser-open', '--browser', 'system', '--reconnect', binding)
        self.assertEqual(out['error']['code'], 'usage')
        out = self.run_cli('browser-open', '--browser', 'orca', '--orca-worktree', WORKTREE, '--orca-terminal', 'term_x',
                           '--reconnect', binding, '--resume', binding)
        self.assertEqual(out['error']['code'], 'usage')
        out = self.run_cli('browser-open', '--browser', 'orca', '--orca-worktree', WORKTREE, '--orca-terminal', 'term_x',
                           '--reconnect', 'binding_bad')
        self.assertEqual(out['error']['code'], 'invalid_input')


class LostPairingNoticeTests(unittest.TestCase):
    def notice(self, search, code, from_link=False):
        script = ("import {lostPairingLine} from " + json.dumps((SCRIPTS.parent / 'web/app.js').as_uri()) + ";"
                  "const [s, c, f] = JSON.parse(process.argv[1]);"
                  "process.stdout.write(JSON.stringify(lostPairingLine({search: s}, c, f)));")
        completed = subprocess.run(['node', '--input-type=module', '-e', script, json.dumps([search, code, from_link])],
                                   capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_a_previously_paired_tab_says_the_terminal_reconnects_it(self):
        line = self.notice('?binding=binding_' + 'a' * 32, 'browser_unauthorized')
        self.assertIn('reconnect', line)
        self.assertNotIn('code', line.lower().replace('pairing code', ''))

    def test_a_fresh_tab_or_another_refusal_gets_no_reconnect_line(self):
        self.assertIsNone(self.notice('', 'browser_unauthorized'))
        self.assertIsNone(self.notice('?binding=binding_' + 'a' * 32, 'wrong_pairing_code'))
        self.assertIsNone(self.notice('?binding=binding_' + 'a' * 32, 'browser_unauthorized', True))


if __name__ == '__main__':
    unittest.main()
