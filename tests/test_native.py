"""Launch boundary tests; no real browser or native qualification. Operator."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "glitch-idea/scripts"))
from idea_native import NativeError, OrcaBinding, open_browser, _SYSTEM_BROWSER_SCRIPT


WORKTREE = "12345678-1234-1234-1234-123456789abc::/fixture/project with spaces"
BINDING = OrcaBinding(WORKTREE, "term_fixture", "ssh:fixture-host")
URL = "http://127.0.0.1:12345/"


def envelope(result):
    return {"ok": True, "result": result}


def listing(**changes):
    terminal = {"handle": BINDING.terminal_handle, "worktreeId": WORKTREE,
                "executionHostId": BINDING.execution_host_id,
                "connected": True, "orphaned": False, "preview": "PRIVATE"}
    terminal.update(changes)
    return envelope({"terminals": [terminal], "truncated": False})


class FakeRunner:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        if isinstance(output, subprocess.CompletedProcess):
            return output
        return subprocess.CompletedProcess(argv, 0, json.dumps(output), "PRIVATE STDERR")


class NativeTests(unittest.TestCase):
    def launch(self, runner, **changes):
        options = {"mode": "orca", "binding": BINDING, "runner": runner}
        options.update(changes)
        return open_browser(URL, **options)

    def refused(self, code, runner, **changes):
        with self.assertRaises(NativeError) as caught:
            self.launch(runner, **changes)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("PRIVATE", str(caught.exception))
        return caught.exception

    def test_success_preserves_full_origin_and_host_selector(self):
        runner = FakeRunner(listing(), envelope({"browserPageId": "page_fixture"}))
        result = self.launch(runner, timeout=3)
        self.assertEqual(result.browser_page_id, "page_fixture")
        self.assertEqual(result.binding, BINDING)
        self.assertEqual(runner.calls[0][0], ["orca", "terminal", "list", "--worktree", "id:" + WORKTREE, "--json"])
        self.assertEqual(runner.calls[1][0], ["orca", "tab", "create", "--url", URL, "--worktree", "id:" + WORKTREE, "--json"])
        for _, options in runner.calls:
            self.assertEqual(options["timeout"], 3)
            self.assertIs(options["shell"], False)
            self.assertEqual(options["stdin"], subprocess.DEVNULL)

    def test_missing_cli_and_timeout_never_fall_back(self):
        for error, code in [(FileNotFoundError(), "cli_missing"),
                            (subprocess.TimeoutExpired("orca", 3, output="PRIVATE"), "cli_timeout"),
                            (PermissionError(), "cli_unavailable")]:
            with self.subTest(code=code):
                runner = FakeRunner(error)
                self.refused(code, runner)
                self.assertEqual(len(runner.calls), 1)

    def test_requires_explicit_origin_not_current(self):
        for binding in [None, OrcaBinding("current", "term_fixture"),
                        OrcaBinding(WORKTREE, ""), OrcaBinding(WORKTREE + "\n", "term_fixture")]:
            runner = FakeRunner()
            self.refused("origin_missing", runner, binding=binding)
            self.assertEqual(runner.calls, [])
        self.assertEqual(OrcaBinding.from_environment({}), OrcaBinding("", ""))
        self.assertEqual(OrcaBinding.from_environment({"ORCA_WORKTREE_ID": WORKTREE,
                         "ORCA_TERMINAL_HANDLE": "term_fixture"}), OrcaBinding(WORKTREE, "term_fixture"))

    def test_identity_and_host_mismatch(self):
        for change in [{"handle": "other"}, {"worktreeId": WORKTREE + "other"},
                       {"executionHostId": "ssh:wrong-host"}]:
            self.refused("identity_mismatch", FakeRunner(listing(**change)))
        duplicate = listing()
        duplicate["result"]["terminals"] *= 2
        self.refused("identity_mismatch", FakeRunner(duplicate))

    def test_disconnected_or_orphaned_origin(self):
        for changes in [{"connected": False}, {"orphaned": True}, {"connected": 1}]:
            self.refused("runtime_unavailable", FakeRunner(listing(**changes)))

    def test_unsupported_envelopes_and_listings(self):
        for payload in [[], {}, {"ok": 1, "result": {}}, {"ok": True, "result": []},
                        {"ok": False, "error": "PRIVATE"}, envelope({}),
                        envelope({"terminals": ["PRIVATE"]})]:
            self.refused("unsupported_payload", FakeRunner(payload))
        for payload in [envelope({"browserPageId": ""}), envelope({"pageId": "other"}),
                        envelope({"browserPageId": 1})]:
            self.refused("unsupported_payload", FakeRunner(listing(), payload))
        self.refused("unsupported_payload", FakeRunner(subprocess.CompletedProcess([], 0, "not JSON", "PRIVATE")))
        for output in ['{"ok":false,"ok":true,"result":{}}',
                       '{"ok":true,"result":{"terminals":NaN}}']:
            self.refused("unsupported_payload", FakeRunner(subprocess.CompletedProcess([], 0, output, "PRIVATE")))

    def test_tab_launch_failure_preserves_failure_without_fallback(self):
        runner = FakeRunner(listing(), subprocess.TimeoutExpired("orca", 3))
        self.refused("cli_timeout", runner)
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(runner.calls[-1][0][1:3], ["tab", "create"])

    def test_native_error_envelope_and_exit_status(self):
        for code in ["runtime_unavailable", "route_unavailable", "unrecognized"]:
            expected = code if code != "unrecognized" else "orca_error"
            self.refused(expected, FakeRunner({"ok": False, "error": {"code": code, "message": "PRIVATE"}}))
        self.refused("cli_failed", FakeRunner(subprocess.CompletedProcess([], 1, json.dumps(listing()), "PRIVATE")))

    def test_unsafe_urls_never_reach_runner(self):
        for url in ["https://127.0.0.1:12345/", "http://example.com:12345/",
                    "http://0.0.0.0:12345/", "http://127.0.0.1/",
                    URL + "?token=PRIVATE", URL + "#PRIVATE", URL + "?", URL + "#",
                    URL + "secret/PRIVATE", "http://user:PRIVATE@127.0.0.1:12345/",
                    "http://127.0.0.1:65536/", "http://127.0.0.1:12345/\n", None]:
            runner = FakeRunner()
            with self.subTest(url=url), self.assertRaises(NativeError) as caught:
                open_browser(url, mode="orca", binding=BINDING, runner=runner)
            self.assertEqual(caught.exception.code, "unsafe_url")
            self.assertEqual(runner.calls, [])

    def test_mode_and_timeout_validation(self):
        self.refused("invalid_mode", FakeRunner(), mode="automatic")
        for timeout in [0, -1, 31, True, float("nan"), float("inf"), "3"]:
            self.refused("invalid_timeout", FakeRunner(), timeout=timeout)

    def test_system_mode_is_explicit_and_launch_only(self):
        runner = FakeRunner(subprocess.CompletedProcess([], 0, "", ""))
        result = self.launch(runner, mode="system", binding=None)
        self.assertEqual(result.mode, "system")
        self.assertIsNone(result.browser_page_id)
        self.assertEqual(runner.calls[0][0], [sys.executable, "-c", _SYSTEM_BROWSER_SCRIPT, URL])
        self.refused("system_browser_unavailable", FakeRunner(subprocess.CompletedProcess([], 1, "", "PRIVATE")), mode="system")

    def test_system_browser_false_return_propagates_from_real_child(self):
        # Execute the actual launch code in an isolated interpreter, replacing
        # only its browser dependency; no desktop/browser is opened by tests.
        def refusing_browser(argv, **kwargs):
            self.assertEqual(argv, [sys.executable, "-c", _SYSTEM_BROWSER_SCRIPT, URL])
            script = "import webbrowser; webbrowser.open_new_tab = lambda url: False; " + argv[2]
            result = subprocess.run([argv[0], "-c", script, argv[3]], **kwargs)
            self.assertEqual(result.returncode, 1)
            return result

        self.refused("system_browser_unavailable", refusing_browser, mode="system", binding=None)

    def test_system_browser_true_return_propagates_from_real_child(self):
        def accepting_browser(argv, **kwargs):
            script = "import webbrowser; webbrowser.open_new_tab = lambda url: True; " + argv[2]
            return subprocess.run([argv[0], "-c", script, argv[3]], **kwargs)

        self.assertEqual(self.launch(accepting_browser, mode="system", binding=None).mode, "system")


if __name__ == "__main__":
    unittest.main()
