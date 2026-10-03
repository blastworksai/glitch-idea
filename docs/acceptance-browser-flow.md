# Browser flow acceptance recipe

How to qualify the glitch-idea browser flow on each platform row, and what evidence each row must record.
It is a repository document, not part of the installed skill.
Placeholders such as `/absolute/path/to/...` are yours to replace.

## 1. Rules that apply to every row

- A row passes only on evidence recorded on that host.
  A missing host is NOT OBSERVED, and NOT OBSERVED is release-blocking for that row.
  Never copy a result from another row.
- Use synthetic idea text only.
  Never record a pairing code, cookie, bearer token or any credential in notes, screenshots or logs.
  Enter the pairing code, then take screenshots after the form has gone.
- Automated input is not human input.
  The Linux harness sends real input events through the Chrome DevTools Protocol.
  That proves the page handles pointer and keyboard events in headless Chrome.
  It is not an attended human qualification, and it never replaces the attended rows.
- On a host where scripted clicks or key presses produce no page events, qualify with attended human pointer and keyboard actions plus snapshots.
  A control triggered from script proves transport only.
- Record the exact browser name and version, operating system version, screen reader and version, and the commit under test.

## 2. Rows

| Row | Host | Evidence kind |
| --- | --- | --- |
| V-Linux | Linux with Chrome or Chromium | Automated harness plus the Linux checks in section 4 |
| V-Windows | Native Windows with Edge or Chrome and NVDA | Attended, section 5 |
| V-Mac | Native macOS with Safari or Chrome and VoiceOver | Attended, section 5 |
| V-OrcaWinSSH | Orca on Windows with the service on a remote Linux host over SSH | Attended, section 6 |
| V-OrcaMacSSH | Orca on macOS with the service on a remote Linux host over SSH | Attended, section 6 |

## 3. What to install (Linux)

- Python 3.10 or newer with PyYAML 6.0.3 (MIT) importable by the interpreter you pass to the harness.
  The harness installs nothing.
  If PyYAML is missing, the helper fails at import with a Python traceback; it prints no install command.
  Check the interpreter first by running the installer against it (`install.py --dry-run --runtime-python <python> ...`), which reports `runtime_prerequisite` with the exact command for you to run.
- Node.js 22 or newer (the harness uses its built-in `WebSocket` and `fetch`; it has no dependencies).
- Google Chrome or Chromium, at `/usr/bin/google-chrome` or `/usr/bin/chromium`, or pass `--chrome`.
- A home directory where `~/.local/state` is writable by you and carries no ACL.
  The harness creates an owner-only runtime directory there and removes it at the end.

## 4. V-Linux

### 4.1 Automated harness

From the repository root:

```bash
node tests/browser_flow_smoke.mjs \
  --python /absolute/path/to/python3 \
  --helper glitch-idea/scripts/idea.py \
  --evidence /absolute/path/to/evidence-dir
```

`--python`, `--helper`, `--chrome` and `--evidence` are optional.
The default evidence directory is `evidence/browser-smoke/<timestamp>/`, which is git-ignored.

The harness creates a throwaway store and owner-only runtime directory, opens a session, starts headless Chrome, and walks this journey with browser-level input: pointer events, text insertion, and per-character key events for pairing and the keyboard-only step:

1. Pair the browser with the one-time code, typed as key events.
2. Capture an idea whose text is long and contains HTML-like markup; confirm it renders as text and runs nothing.
3. Set urgency and importance.
4. Shape, Method, Visualize (skipped once as optional), Assess and Review, using the human-edit path in every step; Method must show that memory is unavailable and claim no preference.
5. Generate the planning prompt; confirm it begins with `/glitch-plan` and contains `## Idea trace`.
6. Confirm each saved step shows the saved state at the top of its navigation control, and that the progress text reads exactly `6 of 7 saved, 1 skipped or not applicable`.
7. Reload and confirm the saved state and the saved capture text persist.
8. Confirm seven columns at 1280 px and the compact navigation at 440 px, with no horizontal overflow.
9. Capture the accessibility tree at 440 px and at 1280 px: step button labels, the navigation name and the status regions.
10. Check a narrow 220 px viewport as a stand-in for 200% zoom (informational).
11. Confirm the agent status line reads `Agent connected`.
12. Complete the handoff: in Review, choose **Copy planning prompt** with the clipboard permission granted, confirm the clipboard holds exactly the generated prompt, write a minimal real plan carrying its `## Idea trace`, run `register-plan`, and confirm the idea is archived at that revision and `doctor` is healthy.
13. Disconnect the agent with `session-close`, reload, confirm the status line says the agent is disconnected, and capture a new idea by hand.
14. Complete Capture using only Tab, typed characters, Space and Enter on a new idea.
15. Induce a save conflict from a second writer on that idea and confirm the browser keeps the answer and shows the stale-revision message.
16. Resume the agent: run `session-open --resume` for the same binding, re-pair the page with key events if it asks, and confirm the status line reads `Agent connected` on that binding.
17. Agent proposals on a new idea, answered by a fixed fixture agent that drives the documented `events` and `respond` verbs with synthetic replies (it is not a model): in Shape, Method and Assess, request a suggestion with the step's own button, use it, edit one field by real input, accept, and confirm the saved value is the edited one.
    Method may first raise a Memory request, which the fixture answers as unavailable.
18. Outside the browser: kill the service process, resume the same binding, and confirm the ideas are still there; run three concurrent command-line capture writers and confirm all land and `doctor` is healthy.

It also fails if the page raised any uncaught exception.
A generated prompt pins the backlog index: capturing any other idea before copying turns that prompt historical, and Review asks you to generate a current one, by design.

It prints a JSON summary (one entry per step, pass or fail, with an observation), writes the same summary and screenshots to the evidence directory, removes Chrome, the service, the temporary store and the runtime directory on exit, and exits non-zero if any required step fails.
The one step marked `required: false` (the 220 px viewport) is reported but does not fail the run; record its result in the evidence anyway.
The save-conflict step is required: a missing stale-revision message fails the run.
A real failure here is a defect to fix, not a flake to retry past.

### 4.2 Linux checks outside the harness

A typical Linux service host is headless, with no desktop browser of its own.
Record these on the host itself, with the commands and output:

- Clean install: run `python3 install.py --dry-run ...` then the real install into a fresh skills root, store and venv, following the operator runbook.
  Then run the harness with `--python <venv interpreter> --helper <installed skill>/scripts/idea.py`, so the journey runs through the installed copy.
The harness already exercises agent proposals and Resume with a fixture agent.
A real model agent's proposals, including its Memory answer, are exercised in section 6 step 7.

Checks that need a visible browser (design-set upload through the file chooser, clipboard deny, real browser zoom, a screen reader, keyboard-only use at 440 px) are made from the operator's own computer against this Linux service, in the SSH rows of section 6.
A Linux desktop screen reader is recorded as NOT OBSERVED when no Linux desktop exists.

## 5. Native attended rows (V-Windows and V-Mac)

Native Windows and macOS launch is not yet qualified; the first observation of each is part of this row.
If the launch is refused with `privacy_unqualified` on Windows, record the refusal text and stop the row as NOT OBSERVED for the journey; do not work around it.

Steps, performed by a person with a real mouse and keyboard in a visible browser:

1. Install and launch following the operator runbook for that system.
   Record each command, its exit status and the error code if any.
2. Pair with the one-time code within 60 seconds.
3. Walk the whole journey in section 4.1 by hand: Capture with long text containing HTML-like markup, both priorities, Shape, Method, Visualize skipped once, Assess, Review, Generate planning prompt.
   Screenshot each step at wide width and at 440 px.
4. Confirm the compact navigation appears at 440 px and the seven columns appear when wide; confirm the saved mark is at the top of each saved step.
5. Keyboard only, at both the wide width and a 440 px window: Capture and one more step using Tab, Shift+Tab, arrow keys on the step navigation, Space and Enter, with a visible focus ring throughout.
6. Screen reader: run NVDA on Windows, VoiceOver on macOS.
   At 100% zoom and again at 200% zoom, listen to the step navigation, the save status, the agent status and an error message.
   Record what was announced, in words, for each.
7. Reload the page and confirm state persists.
8. Missing memory, agent disconnect, save failure or conflict, optional design skip and a successful design set (upload at least two local files).
9. Clipboard: with permission denied, confirm the manual copy path; with permission granted, confirm one-click copy.
   Paste the prompt into a new window and confirm it begins with `/glitch-plan` and contains `## Idea trace`.
10. Kill the service process, resume the same binding with `session-open --resume`, and confirm the idea is still there.

Evidence to record for the row: the platform, browser, screen reader and versions; the commit; a screenshot or recording note for every numbered step above; the written screen-reader announcements; the outcome of each check; and the date.

## 6. Orca over SSH (V-OrcaWinSSH and V-OrcaMacSSH)

The service runs on a remote Linux host.
Orca runs on the local computer.
The browser opens on the local computer through an SSH port forward, because the service accepts only its exact loopback origin.

1. On the remote host, open the session with explicit Orca selectors (the worktree, terminal and host values come from Orca; do not guess them):

   ```bash
   python3 glitch-idea/scripts/idea.py --store /absolute/path/to/ideas browser-open --browser orca \
     --orca-worktree 'ACTUAL_UUID::ACTUAL_WORKTREE_PATH' --orca-terminal ACTUAL_TERMINAL_HANDLE --orca-host ACTUAL_EXECUTION_HOST
   ```

   If the error is `origin_missing` or `usage`, no session exists: correct the arguments and rerun without `--resume`.
   If Orca fails after the session exists, record the typed error code and the `binding_id` from the error (it carries no origin); then either rerun `browser-open ... --resume binding_ID` after correcting the setting, or run `session-open --resume binding_ID` and open the origin it prints through the tunnel in a system browser.
2. On the local computer, forward the same port as the printed origin: `ssh -L <port>:127.0.0.1:<port> <host>`, then open `http://127.0.0.1:<port>/` locally.
3. Walk the journey in section 5, steps 2 to 10.
   The service port changes when the service restarts or a binding is resumed after a crash: after section 5 step 10, read the new origin and reopen the tunnel with the new port before continuing.
4. Multi-file local upload: in Capture or Visualize, choose at least two files from the local computer's disk (not the remote host) and upload them.
   Confirm each file completes, the idea words are preserved, and the files appear in the saved idea on the remote host.
   Record the file names and sizes, not their contents.
5. Clipboard: deny clipboard permission and confirm the manual copy path; the prompt text must be selectable and complete.
   Also record real browser zoom at 100% and 200%, keyboard-only use at a 440 px window, and the screen reader (NVDA on Windows, VoiceOver on macOS) against this Linux service; these are the visible-browser checks for the Linux row.
6. Drop the SSH connection mid-session, restore it, and confirm the page reports a lost connection without marking an unsaved step as saved and that Check save result or reload recovers.
7. Real agent: with the initiating agent session running the skill (not a fixture), request a suggestion in Shape, Method and Assess, use each, edit one field, and accept.
   For Method, record the Memory status the agent returned and confirm the page shows it without preselecting a method.
   Only `found` or `searched_no_preference` counts as evidence of a working memory capability; `unavailable` or `error` records that capability as NOT OBSERVED for this row (section 5 step 8 already covers the missing-memory display).
   Record each event operation and that the accepted value is your edit.

Evidence to record: the Orca version, local operating system, the remote host's operating system, the commit, the tunnel command (without credentials), a screenshot per numbered step, the upload file names and sizes, and each error code observed.

## 7. Closing a row

A row is closed only when every check in its section has a recorded result on that host.
Any check without a result is NOT OBSERVED.
A row with a NOT OBSERVED check blocks the release for that row.
Automated results from the harness may be attached to V-Linux only.
