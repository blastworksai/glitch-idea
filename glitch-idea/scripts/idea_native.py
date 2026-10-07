"""Launch-only browser boundary.

Orca commands match observed 1.4.218 envelopes. Launch success is not browser
interaction or SSH route qualification. No automation or silent fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
import subprocess
import sys
from typing import Literal, Mapping, Protocol, Sequence
from urllib.parse import urlsplit


# stdlib `python -m webbrowser` ignores open()'s false return. Keep this
# fixed code independent of URL content and propagate refusal to the caller.
_SYSTEM_BROWSER_SCRIPT = (
    "import sys, webbrowser; "
    "sys.exit(0 if webbrowser.open_new_tab(sys.argv[1]) else 1)"
)


class NativeError(RuntimeError):
    """Stable, redacted failure: never include CLI output or terminal previews."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Runner(Protocol):
    def __call__(self, argv: Sequence[str], **kwargs) -> subprocess.CompletedProcess: ...


@dataclass(frozen=True)
class OrcaBinding:
    worktree_id: str
    terminal_handle: str
    execution_host_id: str | None = None

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> OrcaBinding:
        """Caller supplies the initiating pane's environment; never infer current."""
        return cls(environment.get("ORCA_WORKTREE_ID", ""),
                   environment.get("ORCA_TERMINAL_HANDLE", ""))


@dataclass(frozen=True)
class BrowserLaunch:
    mode: Literal["orca", "system"]
    url: str
    browser_page_id: str | None = None
    binding: OrcaBinding | None = None


def _identifier(value: object) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 4096
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


_PAIR_FRAGMENT = re.compile(r"#pair=[0-9a-f]{32}")


def _safe_url(url: str) -> None:
    # The one-time pairing code may ride the URL fragment only (single-use,
    # 60 s, a replay kills the session); the brief argv exposure to `ps` while
    # the browser command starts is accepted. Reusable secrets never ride a URL.
    try:
        base, hash_mark, rest = url.partition("#")
        fragment_ok = (not hash_mark) or _PAIR_FRAGMENT.fullmatch(hash_mark + rest) is not None
        parsed = urlsplit(base)
        port = parsed.port
        safe = (fragment_ok and parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                and parsed.netloc == f"127.0.0.1:{port}" and port is not None
                and 0 < port < 65536 and (parsed.path == "/" if hash_mark else parsed.path in ("", "/"))
                and not parsed.query and not parsed.fragment
                and parsed.username is None and parsed.password is None
                and "?" not in url and url.count("#") == (1 if hash_mark else 0)
                and _identifier(url))
    except (TypeError, ValueError, AttributeError):
        safe = False
    if not safe:
        raise NativeError("unsafe_url", "Use the non-secret HTTP loopback service root with an explicit port.")


def _run(argv: list[str], runner: Runner, timeout: float) -> subprocess.CompletedProcess:
    try:
        completed = runner(argv, timeout=timeout, capture_output=True, text=True,
                           shell=False, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise NativeError("cli_missing", "Browser launch executable is unavailable.") from None
    except subprocess.TimeoutExpired:
        raise NativeError("cli_timeout", "Browser launch command exceeded its timeout.") from None
    except OSError:
        raise NativeError("cli_unavailable", "Browser launch executable could not run.") from None
    return completed


def _orca(argv: list[str], runner: Runner, timeout: float) -> dict:
    completed = _run(["orca", *argv, "--json"], runner, timeout)

    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError
            value[key] = item
        return value

    def reject_constant(value):
        raise ValueError

    try:
        if not isinstance(completed.stdout, str) or len(completed.stdout) > 1024 * 1024:
            raise ValueError
        envelope = json.loads(completed.stdout, object_pairs_hook=unique_object,
                              parse_constant=reject_constant)
        if not isinstance(envelope, dict) or type(envelope.get("ok")) is not bool:
            raise ValueError
        if envelope["ok"] is False:
            error = envelope.get("error")
            if not isinstance(error, dict) or not isinstance(error.get("code"), str):
                raise ValueError
            # Keep upstream detail private. Stable categories are refined only
            # for named errors, never inferred from arbitrary diagnostic prose.
            upstream = error["code"].lower()
            code = {"runtime_unavailable": "runtime_unavailable",
                    "runtime_not_found": "runtime_unavailable",
                    "not_connected": "runtime_unavailable",
                    "route_unavailable": "route_unavailable",
                    "host_not_found": "route_unavailable"}.get(upstream, "orca_error")
            raise NativeError(code, "Orca refused the requested browser route.")
        if completed.returncode != 0:
            raise NativeError("cli_failed", "Orca exited unsuccessfully.")
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, TypeError, RecursionError):
        raise NativeError("unsupported_payload", "Orca returned an unsupported JSON envelope.") from None


def open_browser(url: str, *, mode: Literal["orca", "system"],
                 binding: OrcaBinding | None = None,
                 runner: Runner = subprocess.run, timeout: float = 8) -> BrowserLaunch:
    """Open a dedicated tab using an explicitly selected browser mode.

    Timeout applies per command (at most two commands). System mode is for a
    service on the user's local desktop, not an SSH routing substitute.
    """
    _safe_url(url)
    if (isinstance(timeout, bool) or not isinstance(timeout, (float, int))
            or not math.isfinite(timeout) or not 0 < timeout <= 30):
        raise NativeError("invalid_timeout", "Command timeout must be greater than zero and at most 30 seconds.")
    if mode == "system":
        completed = _run([sys.executable, "-c", _SYSTEM_BROWSER_SCRIPT, url], runner, timeout)
        if completed.returncode != 0:
            raise NativeError("system_browser_unavailable", "The local system browser could not open.")
        return BrowserLaunch("system", url)
    if mode != "orca":
        raise NativeError("invalid_mode", "Choose explicit orca or system browser mode.")
    if (not isinstance(binding, OrcaBinding) or not _identifier(binding.worktree_id)
            or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}::.+", binding.worktree_id)
            or not _identifier(binding.terminal_handle)
            or (binding.execution_host_id is not None and not _identifier(binding.execution_host_id))):
        raise NativeError("origin_missing", "An explicit full originating worktree ID and terminal handle are required.")
    selector = "id:" + binding.worktree_id
    listing = _orca(["terminal", "list", "--worktree", selector], runner, timeout)
    terminals = listing.get("terminals")
    if not isinstance(terminals, list) or any(not isinstance(t, dict) for t in terminals):
        raise NativeError("unsupported_payload", "Orca returned an unsupported terminal listing.")
    matches = [t for t in terminals if t.get("handle") == binding.terminal_handle]
    if (len(matches) != 1 or matches[0].get("worktreeId") != binding.worktree_id
            or (binding.execution_host_id is not None
                and matches[0].get("executionHostId") != binding.execution_host_id)):
        raise NativeError("identity_mismatch", "Orca terminal does not uniquely match the initiating pane and host.")
    if matches[0].get("connected") is not True or matches[0].get("orphaned") is not False:
        raise NativeError("runtime_unavailable", "The originating Orca terminal is disconnected or orphaned.")
    created = _orca(["tab", "create", "--url", url, "--worktree", selector], runner, timeout)
    page = created.get("browserPageId")
    if not _identifier(page):
        raise NativeError("unsupported_payload", "Orca did not return a browser page ID.")
    return BrowserLaunch("orca", url, page, binding)



def close_browser_page(page_id: str, binding: OrcaBinding, *,
                       runner: Runner = subprocess.run, timeout: float = 8) -> None:
    """Close one Orca browser page by ID inside the binding's own worktree.

    Orca exposes `tab close --page <id> --worktree <selector>` (its agent-context flag list
    carries `page`; the usage line omits it). Raises NativeError on any failure; callers that
    must not fail because of a close (a reconnect) catch it.
    """
    if not _identifier(page_id) or not isinstance(binding, OrcaBinding) or not _identifier(binding.worktree_id):
        raise NativeError("origin_missing", "A recorded page ID and originating worktree are required.")
    _orca(["tab", "close", "--page", page_id, "--worktree", "id:" + binding.worktree_id], runner, timeout)
