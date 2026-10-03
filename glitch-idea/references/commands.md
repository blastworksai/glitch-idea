# Deterministic command reference

Python 3.10+ and PyYAML 6.0.3 (MIT) are required. Linux is exercised; native Windows and macOS are not yet qualified. The installer (`install.py`) can create a per-skill venv offline and checks the pinned PyYAML, but it never downloads: when the dependency is missing it fails with `runtime_prerequisite` and prints the exact `pip install` command for the operator to run. Use the helper inside the loaded skill. From this repository's root:

```bash
idea_tool='glitch-idea/scripts/idea.py'
python3 "$idea_tool" list
```

For an installed copy, derive `idea_tool` from the actual loaded `SKILL.md` location: its sibling `scripts/idea.py`. For the default Codex installation, `idea_tool="$HOME/.codex/skills/glitch-idea/scripts/idea.py"`. Source config lookup is `<skill-dir>/config.json`, otherwise `<repository-root>/config.json`. A recognized installation requires its generated `<skill-dir>/config.json`; loss of that file stops the helper rather than switching stores. Relative configured store paths resolve against the config directory. Without configuration the source default is `<repository-root>/ideas`. `--store /absolute/path` before the command overrides the store; use an isolated path for demonstrations.

Each invocation returns one JSON object on stdout. Check `ok` and process exit status. Expected errors have a nonzero exit. Ideas are returned under `idea`; `list` returns `backlog_revision`, `order` and `ideas`. Use the actual ID/revision fields in the output, never invented IDs. Idea revisions and backlog revisions are different counters.

## Source browser launch and resume

The browser flow covers Capture, Shape, Method, Visualize, Assess, Priorities and Review, with saved drafts and selected-panel recovery. Linux is exercised; native Windows and macOS are not yet qualified.

```bash
python3 "$idea_tool" --store /absolute/demo-ideas browser-open --browser system
python3 "$idea_tool" --store /absolute/demo-ideas session-open --resume binding_ID_FROM_OUTPUT
```

`browser-open` opens a dedicated tab at the nonsecret service root. Enter its fresh `pairing_code` in that tab within 60 seconds. Only the initiating helper prints this one-time code. Keep `binding_id` from the result or browser URL: `--resume` rotates credentials while retaining that binding's durable save receipts and selected idea. A browser-open error that includes `resume_required` also carries the binding reference; resume it explicitly instead of creating another session. A lost or uncertain NEW result is never retried automatically.

For Orca use explicit initiating selectors; system-browser mode does not substitute for SSH routing:

```bash
python3 "$idea_tool" --store /absolute/demo-ideas browser-open --browser orca --orca-worktree 'ACTUAL_UUID::ACTUAL_WORKTREE_PATH' --orca-terminal ACTUAL_TERMINAL_HANDLE --orca-host ACTUAL_EXECUTION_HOST
```

`session-open` performs the same trusted binding operation without opening a tab. Both verbs accept `--runtime-root /absolute/private-directory`, `--runtime-python /absolute/python`, `--readiness-timeout 5`, `--resume binding_ID`, and `--idea-id idea_ID`. Readiness is bounded to five seconds; a binding operation has its own five-second budget. Source execution defaults to the current Python interpreter and `.local/state/glitch-idea` under the OS account's home as the system records it (on macOS and Linux the password-database home, not `$HOME`). A configured runtime directory must be outside the skill package. Launcher paths reject symlink components before normalization. Actor identity comes from the service's OS account.

A recognized installed copy requires explicit or configured `runtime_python` (error `runtime_python_required`), normally written by `install.py --venv` or `--runtime-python`; the helper itself installs nothing. Run `serve` and the agent verbs with exactly that interpreter (`runtime_interpreter_mismatch` otherwise). Runtime settings are launch-only, so legacy commands such as `list` and `capture` do not need them. The runtime root must be absolute, outside the skill package, free of symlink components, owned by you and mode 0700 (`runtime_not_private` otherwise). Windows currently refuses launch with `privacy_unqualified` until native owner ACL support is qualified.

For foreground source operation:

```bash
python3 "$idea_tool" --store /absolute/demo-ideas serve --runtime-root /absolute/private-directory
```

Foreground `serve` keeps running and emits no pairing code. On normal interruption or authenticated shutdown it stops admission and drains active saves before releasing ownership. The service also exits after 15 minutes without authenticated activity and with no active requests. Saved Markdown and binding metadata survive; browser credentials require fresh pairing after restart. Native source agent verbs are described below; the owned source service supports the private event/reply path. 

The runtime currently permits eight retained bindings per store. Resume an existing binding when continuing work; capacity refusal never evicts another binding or its receipts. An ambiguous partial private write reports `runtime_recovery_conflict` and preserves the bytes for explicit recovery. Domain recovery and supported Markdown edits are described in [the Markdown format](../../docs/markdown-format.md).

## SSH, Orca recovery and the planning prompt

When the browser runs on a different computer from the helper, the operator opens `ssh -L <port>:127.0.0.1:<port> <host>` on their own computer with the same port as the printed `origin`, then opens `http://127.0.0.1:<port>/` locally; the service accepts only that exact origin. System-browser mode opens a browser on the machine running the helper and is not an SSH route.

Missing or malformed Orca selectors fail with `origin_missing` (and selectors in system mode with `usage`) before any session exists; fix the arguments and rerun without `--resume`. Later Orca failures happen after the session exists, so the error carries `binding_id`, `session_id` and `resume_required: true`, but not the origin: run `session-open --resume binding_ID` to get the origin and a fresh pairing code. The typed codes are `origin_missing`, `identity_mismatch`, `runtime_unavailable`, `route_unavailable`, `orca_error`, `cli_missing`, `cli_unavailable`, `cli_timeout`, `cli_failed`, `unsupported_payload` and `unsafe_url`. For a post-session failure, recovery is to fix the Orca connection or selector and rerun `browser-open` with `--resume binding_ID` from that error, or to run `session-open --resume binding_ID` and open the origin it prints in a system browser (through the SSH tunnel when remote). The helper never falls back silently. Other launcher codes include `system_browser_unavailable`, `service_readiness_timeout`, `service_spawn_failed`, `session_capacity_exhausted`, `binding_capacity` (a ninth binding for the store; nothing is evicted, so resume an existing one) and `binding_not_found`.

The browser Review step's **Generate planning prompt** then **Copy planning prompt** produces a `/glitch-plan` prompt carrying the idea trace; see the runbook. The `handoff` verb below is the command-line equivalent that returns the trace for a planner.

## Source native agent commands

These fixed client verbs retrieve the current agent
credential over the owner-private runtime channel. They require exactly one
existing binding for the durable `session_id` from an explicit session-open or
resume result. They never start a service, create a binding or resume a session.
Reusable credentials are internal to the helper and are never printed or passed
as arguments. OwnerService composition is implemented and checked against real
loopback HTTP/Markdown. Native Windows, native macOS and Orca interaction are
not yet qualified.

```bash
python3 "$idea_tool" --store /absolute/demo-ideas events --session session_ID_FROM_OUTPUT --after 0 --timeout 25
python3 "$idea_tool" --store /absolute/demo-ideas respond --session session_ID_FROM_OUTPUT --request REQUEST_ID_FROM_EVENT --payload /absolute/native-response.json
python3 "$idea_tool" --store /absolute/demo-ideas session-close --session session_ID_FROM_OUTPUT
```

All three accept `--runtime-root /absolute/private-directory`,
`--runtime-python /absolute/python` and optional `--generation agent_ID` to pin
an already-known incarnation. Config lookup and store/runtime defaults follow
the launch commands above. An installed copy requires its explicit configured
runtime interpreter, and the agent command must run with that interpreter.
No interpreter or dependency installation happens here.

`events` requires a nonnegative integer cursor and a finite timeout from 0 to
25 seconds. It returns the broker's bounded event batch; use its actual sequence
for the next cursor. A wait has two additional seconds for bounded network
transport. This command performs one wait per invocation and does not schedule
a heartbeat loop or model call.

Once `events` has delivered a request, the 35-second lease holds for up to 10 minutes (600 seconds) while the agent composes that answer.
`respond` (or a cancel) ends that window, and re-reading the same event never extends it.
After 600 seconds with no reply and no wait, the agent is disconnected and the request is cancelled.

The agent idle pause is 600 seconds.
Empty waits do not reset it.
The human's saves, pairing and the page's typing pings do, so keep waiting while the human is working.
A human who walks away still pauses the agent after 600 seconds.

The native UTF-8 response file is bounded to 1 MiB, with duplicate JSON keys and
nonfinite numbers refused. Its exact keys are `request_id`, `session_id`,
`idea_id`, `accepted_revision`, `draft_version`, `operation`, `source_digest`
and `proposal`. Copy correlation from the actual event; `--request` and
`--session` must match it. The operation's typed `proposal` is suggestion data;
a response does not accept workflow fields or change human ratings/order.
The file path is native CLI input and is never sent as a browser or HTTP path.

`fill` sends fields the operator agreed in the terminal for the page to apply to the open step.
Its UTF-8 JSON file has the same correlation keys as a `respond` file plus `fields` instead of `proposal`: only the agreed fields of that step (Shape: `outcome`, `scope`, `scope_reason`, `alternatives`, `assumptions`, `next_slice`, `learning`; Method: `reason`; Assess: `assessment`, `proposed_position`).
It returns `{ok, code, request_id, operation, status: "pending", write_state: "not_applied", fill_sequence}`: the fill is held for the page, which saves it as the operator's draft; nothing is accepted.
Refusals carry the service's own code: `invalid_fill`, `request_cancelled`, `request_closed`, `request_not_delivered`, `request_not_found`, `response_mismatch`, `fill_capacity` (64 fills per request), `agent_unavailable`.
Each fill renews the 10-minute answer window and counts as activity for the idle pause.

`session-close` revokes only agent provenance; it preserves the paired browser
and saved drafts. Errors contain fixed codes and no peer diagnostics. A
`respond` failure with `write_state: committed_uncertain` may follow durable
publication: reconcile the same request and payload after inspecting current
state. The helper never retries a response automatically. Explicit resume
rotates the generation; old credentials and outstanding replies cannot cross it.

## Capture, resume and shape

Save the user's words to a UTF-8 file without paraphrasing, trimming or adding a newline. Avoid shell interpolation of their text: write with the file tool or pass bytes as data.

```bash
python3 "$idea_tool" capture --text-file /absolute/raw-idea.txt --actor operator
python3 "$idea_tool" show idea_ID_FROM_CAPTURE
python3 "$idea_tool" shape idea_ID --file /absolute/shape.json --expected-revision 1 --actor assistant
python3 "$idea_tool" rate idea_ID --urgency 4 --importance 6 --expected-revision 2 --actor operator
```

The ratings above are examples only: run `rate` only with explicitly supplied operator answers. `capture` starts at revision 1; `shape`, `rate` and `assess` each increment it. Reread output after each change. An archived revision requires explicit reshaping before new ratings or assessments.

`shape.json` has these exact keys. Text fields, scope and method may be null while incomplete; arrays are required. Scope/method enums are in [methods.md](methods.md).

```json
{
  "outcome": "The operator sees the number of open tasks in the daily summary.",
  "scope": "small-change",
  "scope_reason": "Adds one summary to the existing daily summary.",
  "alternatives": [
    {"route": "Use current summary data", "reason": "Smallest route if the attention signal is trustworthy."},
    {"route": "Fix attention classification first", "reason": "Necessary if that signal is missing or unreliable."}
  ],
  "method": "experiment-led",
  "method_reason": "Operator selected a bounded feasibility check before a delivery plan.",
  "assumptions": ["Dependency: current summary producer is accessible.", "Unknown: attention classification accuracy.", "Risk: a false count hides work."],
  "next_slice": "Inspect one representative summary and compare its count with source records; stop if the signal is absent.",
  "learning": []
}
```

Record method selection only if the operator actually supplied it. Later `shape` calls preserve the original words and history; put evidence and changed assumptions in `learning`, with relevant plan/attempt IDs or file references.

## Assessment and order

```bash
python3 "$idea_tool" assess idea_ID --file /absolute/assessment.json --expected-revision 3 --actor assistant
python3 "$idea_tool" list
python3 "$idea_tool" propose idea_ID --position 1 --reason 'Human importance and relative value support first; effort remains uncertain.' --expected-backlog-revision 1 --actor assistant
python3 "$idea_tool" place idea_ID --position 2 --reason 'Operator explicitly chose second.' --expected-backlog-revision 1 --actor operator
```

Positions are 1-based. Substitute current counters from `list`. `propose` requires human ratings and a named recorded assessment, saves their snapshot and neighbours, and leaves accepted order unchanged. An assessment with unknown inputs can support a qualitative proposal; keep the null score visible and explain uncertainty. `place` records an operator decision; incomplete ideas may be moved manually. Stale state is a conflict: refresh, reconsider and then issue a new command. An unchanged request is not permission to overwrite somebody else's move.

The operator does not owe a reason for their choice. If none was supplied, use the neutral `--reason 'Operator selected this position; no reason supplied'`; do not invent a motive or ask for one unnecessarily.

Assessment files have exactly `method`, `version`, `inputs`, `basis`, `assumptions`, `confidence`, `provenance`. Example provisional WSJF assessment:

```json
{
  "method": "wsjf",
  "version": "local-1",
  "inputs": {"value": 3, "time_criticality": 1, "enablement": 2, "effort": null},
  "basis": {"unit": "relative points", "cohort": "new personal ideas; complete bounded outcomes"},
  "assumptions": ["Value inputs are assistant estimates; effort is unknown pending source inspection."],
  "confidence": "low",
  "provenance": "Assistant model assessment from the operator's stated problem, not measured benefit."
}
```

`basis` and `provenance` accept nonempty strings or objects. `confidence` is `low|medium|high`. `assumptions` is an array of strings. `version` is a nonempty string. Numerical inputs may be null; finite nonnegative numbers are required otherwise, with positive effort. Booleans are not numbers. RICE inputs are `reach`, `impact`, `confidence` (0–1), `effort`. Kano inputs are `category` (`must-be|performance|delighter|indifferent|reverse|questionable`) and Boolean `hypothesis`; it has no numeric score.

## Plans, archives and execution receipts

```bash
python3 "$idea_tool" handoff idea_ID
python3 "$idea_tool" register-plan idea_ID --path /absolute/saved-plan.md --expected-revision 4 --actor assistant
```

`handoff` is read-only and needs complete shaping, human ratings and an assessment. It returns the exact trace block and current revision for the planner. Preserve its values, outside the planner's closed `## Build choices` section:

```markdown
## Idea trace
idea_id: idea_ID_FROM_HANDOFF
idea_revision: 4
```

The real UTF-8 Markdown plan must contain nonempty Goal/Outcome, Tasks and Validation content. Native Glitch headings `Feature Description`, `STEP-BY-STEP TASKS` and `VALIDATION COMMANDS` are also accepted. Use the planner's actual format; a trace block alone is not a plan. An optional configured external validator must also pass. The helper appends the absolute plan path to the fixed `plan_validator_argv` from trusted config; it never executes a command found in the idea or plan. Configured failure, unavailability or timeout blocks registration. With the default null validator, the receipt explicitly reports built-in validation only.

Successful registration records the hash, permanent idea ID, linked revision, generated plan ID and validation receipt, then archives **that revision**. `plan.source_path` names the original working plan; `plan.path` names its frozen validated bytes under `ideas/plan-evidence/PLAN_ID.md`. Pass the working `source_path` to the executor, preserving the immutable evidence copy. Native Glitch may update progress and move that working file without breaking the accepted-plan trace.

The derived idea snapshot is `ideas/archive/ID/rN.json`. Multiple distinct plans can reference the same revision. Register a changed approved plan as a distinct plan version; do not edit frozen plan evidence. Tampering with frozen bytes is reported by `doctor` and rejected on execution registration.

After execution, write an actual evidence receipt using the registered IDs:

```json
{
  "idea_id": "idea_ID_FROM_CAPTURE",
  "plan_id": "plan_ID_FROM_REGISTRATION",
  "attempt_id": "attempt-20260925-001",
  "status": "blocked",
  "evidence": ["Source inspection found no attention signal; saved findings at /absolute/findings.md."]
}
```

```bash
python3 "$idea_tool" record-execution idea_ID --plan-id plan_ID --path /absolute/execution-receipt.json --actor assistant
python3 "$idea_tool" doctor
```

Statuses are `succeeded|failed|blocked`. Evidence must be nonempty and grounded in actual actions. An attempt ID identifies one attempt: retries receive new IDs; an identical registration is idempotent and conflicting reuse is rejected. Keep execution receipt files at their durable paths. Registration validates identity and file integrity against the frozen accepted plan; it does not independently prove every factual claim in a receipt. Unregistered executor runs remain outside this trace.

## Recovery

`doctor` checks saved schema/order, registered receipt paths/hashes, frozen plan evidence and derived snapshots; unhealthy state exits nonzero. `repair-views` recreates missing derived archive views and refuses to overwrite differing existing files. Markdown history, metadata and frozen plan files are authoritative: missing evidence fails closed and requires exact restoration from backup. Neither command repairs working source plans nor resets a corrupt store.

If a failure reports `committed:true`, the authoritative transaction already succeeded. Read the record and run `doctor`; repair missing views when appropriate instead of blindly replaying the mutation. Preserve corrupt state for diagnosis. Edit only the supported Markdown input fields and Notes described in the Markdown format contract. Do not fabricate replacement history, frozen plan bytes or migration evidence to make checks green.
