"""Fragment pairing: the one-time code rides the URL fragment of the launch call only."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "glitch-idea/scripts"))
import idea_launch as launch
from idea_native import NativeError, OrcaBinding, open_browser

URL = "http://127.0.0.1:12345/"
CODE = "0123456789abcdef" * 2
WORKTREE = "12345678-1234-1234-1234-123456789abc::/fixture/project"
BINDING = OrcaBinding(WORKTREE, "term_fixture", None)


class Runner:
    def __init__(self, *outputs):
        self.outputs = list(outputs); self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        out = self.outputs.pop(0) if self.outputs else subprocess.CompletedProcess(argv, 0, "", "")
        return out if isinstance(out, subprocess.CompletedProcess) else subprocess.CompletedProcess(argv, 0, json.dumps(out), "")


def orca_runner():
    term = {"handle": "term_fixture", "worktreeId": WORKTREE, "executionHostId": None,
            "connected": True, "orphaned": False}
    return Runner({"ok": True, "result": {"terminals": [term]}}, {"ok": True, "result": {"browserPageId": "p1"}})


class FragmentUrlTests(unittest.TestCase):
    def refused(self, url):
        runner = Runner()
        with self.subTest(url=url), self.assertRaises(NativeError) as caught:
            open_browser(url, mode="system", runner=runner)
        self.assertEqual(caught.exception.code, "unsafe_url")
        self.assertEqual(runner.calls, [])

    def test_exact_pair_fragment_accepted(self):
        runner = Runner()
        self.assertEqual(open_browser(URL + "#pair=" + CODE, mode="system", runner=runner).mode, "system")
        self.assertEqual(runner.calls[0][-1], URL + "#pair=" + CODE)

    def test_other_fragments_refused(self):
        for url in [URL + "#PRIVATE", URL + "#", URL + "#pair=", URL + "#pair=" + CODE + "&x",
                    URL + "#pair=" + CODE + "#x", URL + "#pair=" + CODE[:31], URL + "#pair=" + CODE + "0",
                    URL + "#pair=" + CODE.upper(), URL + "#pair=" + "g" * 32,
                    URL[:-1] + "?q#pair=" + CODE, URL + "?q#pair=" + CODE, URL + "x#pair=" + CODE,
                    URL[:-1] + "#pair=" + CODE, URL + "#Pair=" + CODE, URL + "#pair=" + CODE + "\n",
                    "http://u:p@127.0.0.1:12345/#pair=" + CODE, "https://127.0.0.1:12345/#pair=" + CODE]:
            self.refused(url)

    def test_orca_tab_create_carries_exact_fragment(self):
        runner = orca_runner()
        open_browser(URL + "#pair=" + CODE, mode="orca", binding=BINDING, runner=runner)
        argv = runner.calls[1]
        self.assertEqual(argv[argv.index("--url") + 1], URL + "#pair=" + CODE)
        self.assertEqual(sum(a.count(CODE) for c in runner.calls for a in c), 1)


@unittest.skipUnless(os.name == "posix", "posix only")
class LaunchFragmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name) / "ideas"; self.root = Path(self.temp.name) / "private"
        self.children = []; self.lock = threading.Lock()
        self.addCleanup(self.cleanup)

    def spawn(self, *a, **k):
        child = subprocess.Popen(*a, **k)
        with self.lock: self.children.append(child)
        return child

    def cleanup(self):
        from idea_runtime import Runtime
        try: Runtime(self.store, self.root).request_owned_stop(timeout=2)
        except Exception: pass
        for c in self.children:
            try: c.wait(timeout=4)
            except Exception: c.kill()

    def test_system_mode_fragment_in_argv_only(self):
        calls = []
        def runner(argv, **kw): calls.append(argv); return SimpleNamespace(returncode=0, stdout="", stderr="")
        res = launch.open_browser_session(self.store, self.root, mode="system", runner=runner, spawn=self.spawn)
        code = res["pairing_code"]
        self.assertEqual(calls[0][-1], res["origin"].rstrip("/") + "/#pair=" + code)
        self.assertEqual(res["browser"]["url"], res["origin"])
        self.assertNotIn(code, res["browser"]["url"]); self.assertNotIn("#", res["browser"]["url"])
        self.assertIn(code, res["fallback_line"])
        self.assertEqual(res["fallback_line"], f"Only if the tab did not open paired: type {code} within 60 s.")

    def test_orca_mode_fragment_in_tab_create_url(self):
        runner = orca_runner()
        res = launch.open_browser_session(self.store, self.root, mode="orca", orcabinding=BINDING,
                                          runner=runner, spawn=self.spawn)
        argv = runner.calls[1]
        self.assertEqual(argv[argv.index("--url") + 1], res["origin"].rstrip("/") + "/#pair=" + res["pairing_code"])
        self.assertEqual(res["browser"]["url"], res["origin"])

    def test_failure_carries_no_url_or_code(self):
        def refuse(argv, **kw): return SimpleNamespace(returncode=1, stdout="", stderr="")
        with self.assertRaises(launch.LaunchError) as caught:
            launch.open_browser_session(self.store, self.root, mode="system", runner=refuse, spawn=self.spawn)
        text = repr(caught.exception.details) + str(caught.exception)
        self.assertNotIn("#pair", text); self.assertNotIn("pairing_code", text)
        self.assertNotIn("http://", text)


if __name__ == "__main__":
    unittest.main()
