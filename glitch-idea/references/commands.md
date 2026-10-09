# Deterministic command reference

Python 3.10+ and PyYAML 6.0.3 (MIT) are required. Linux is exercised; native Windows and macOS are not yet qualified. The installer (`install.py`) can create a per-skill venv offline and checks the pinned PyYAML, but it never downloads: when the dependency is missing it fails with `runtime_prerequisite` and prints the exact `pip install` command for the operator to run. Use the helper inside the loaded skill. From this repository's root:

```bash
idea_tool='glitch-idea/scripts/idea.py'
python3 "$idea_tool" list
```

For an installed copy, derive `idea_tool` from the actual loaded `SKILL.md` location: its sibling `scripts/idea.py`. For the default Codex installation, `idea_tool="$HOME/.codex/skills/glitch-idea/scripts/idea.py"`. Source config lookup is `<skill-dir>/config.json`, otherwise `<repository-root>/config.json`. A recognized installation requires its generated `<skill-dir>/config.json`; loss of that file stops the helper rather than switching stores. Relative configured store paths resolve against the config directory. Without configuration the source default is `<repository-root>/ideas`. `--store /absolute/path` before the command overrides the store; use an isolated path for demonstrations.

Each invocation returns one JSON object on stdout. Check `ok` and process exit status. Expected errors have a nonzero exit. Ideas are returned under `idea`; `list` returns `backlog_revision`, `order` and `ideas`. Use the actual ID/revision fields in the output, never invented IDs. Idea revisions and backlog revisions are different counters.

## Source browser launch and resume

The browser flow has eight steps: Capture, Priorities, Methods, Discovery, Exploration, Visualize, Assess, Review, with saved drafts and selected-panel recovery. Linux is exercised; native Windows and macOS are not yet qualified.

```bash
python3 "$idea_tool" --store /absolute/demo-ideas browser-open --browser system
python3 "$idea_tool" --store /absolute/demo-ideas session-open --resume binding_ID_FROM_OUTPUT
```

`browser-open` opens a dedicated tab at `<origin>/#pair=<code>` (the code is exactly 32 lowercase hex characters). The page reads the fragment, removes it from the address bar before anything else and redeems the code itself, so the operator types nothing. The returned `browser.url` stays the root-only origin. The result also carries `pairing_code` and `fallback_line`: "Only if the tab did not open paired: type <code> within 60 s." Show that line only when the operator reports the tab did not open paired; typing a code the tab already redeemed invalidates the session (`pairing_replay_session_invalidated`). Ready first: before a typed code is ever issued (plain `session-open`, or `session-open --resume binding_ID` after an expired or failed pairing), ask the operator ONE question, "ready to type a code?", and run the verb only on their yes, giving the origin and fresh code in the same message that carries it (60 seconds: measured 12 seconds at the box, expired when the code travels through a prompt first). The page's own sentence is: "This link's pairing code has expired. Ask your terminal for a new code (it runs session-open --resume)." Plain `session-open` launches no browser, so the operator opens the origin and types the code. Only the initiating helper prints this one-time code. Keep `binding_id` from the result or browser URL: `--resume` rotates credentials while retaining that binding's durable save receipts and selected idea. A browser-open error that includes `resume_required` also carries the binding reference; resume it explicitly instead of creating another session. A lost or uncertain NEW result is never retried automatically. With `--browser orca`, a resume creates the new tab first and then closes the page recorded for that binding (`orca tab close --page <id> --worktree id:<worktree>`); the result's `browser.previous_page_closed` says whether it did, with `previous_page_close_error` on a failure, which never fails the reconnect. System-browser mode cannot close a tab. An old tab still open elsewhere is told it was replaced by a newer one (error code `session_superseded`).

For Orca use explicit initiating selectors; system-browser mode does not substitute for SSH routing:

```bash
python3 "$idea_tool" --store /absolute/demo-ideas browser-open --browser orca --orca-worktree 'ACTUAL_UUID::ACTUAL_WORKTREE_PATH' --orca-terminal ACTUAL_TERMINAL_HANDLE --orca-host ACTUAL_EXECUTION_HOST
```

`session-open` performs the same trusted binding operation without opening a tab. Both verbs accept `--runtime-root /absolute/private-directory`, `--runtime-python /absolute/python`, `--readiness-timeout 5`, `--resume binding_ID`, and `--idea-id idea_ID`. Readiness is bounded to five seconds; a binding operation has its own five-second budget. Source execution defaults to the current Python interpreter and `.local/state/glitch-idea` under the OS account's home as the system records it (on macOS and Linux the password-database home, not `$HOME`); when that home chain is shared (group- or world-writable), the default falls back by itself to `$XDG_RUNTIME_DIR/glitch-idea` (when that is your own 0700 directory), prints one plain line saying so and reports `runtime_root_fallback` in the result, while an explicit or configured runtime root is used exactly as given. A configured runtime directory must be outside the skill package. Launcher paths reject symlink components before normalization. Actor identity comes from the service's OS account.

A recognized installed copy requires explicit or configured `runtime_python` (error `runtime_python_required`), normally written by `install.py --venv` or `--runtime-python`; the helper itself installs nothing. Run `serve` and the agent verbs with exactly that interpreter (`runtime_interpreter_mismatch` otherwise). Runtime settings are launch-only, so legacy commands such as `list` and `capture` do not need them. The runtime root must be absolute, outside the skill package, free of symlink components, owned by you and mode 0700 (`runtime_not_private` otherwise). Windows currently refuses launch with `privacy_unqualified` until native owner ACL support is qualified.

For foreground source operation:

```bash
python3 "$idea_tool" --store /absolute/demo-ideas serve --runtime-root /absolute/private-directory
```

Foreground `serve` keeps running and emits no pairing code. On normal interruption or authenticated shutdown it stops admission and drains active saves before releasing ownership. The service also exits after 15 minutes without authenticated activity and with no active requests. Saved Markdown and binding metadata survive; browser credentials require fresh pairing after restart. Native source agent verbs are described below; the owned source service supports the private event/reply path. 

A store keeps at most eight sessions (bindings). Resume an existing binding when continuing work. When a new open needs room, a session whose idea reached Review (its Review step is saved) and that has no open browser tab or terminal agent is freed automatically, the least recently used first; its ideas and receipts are untouched. A session that is unfinished or still open is never freed automatically: the open is refused with `binding_capacity` and the JSON error carries a `sessions` list. An ambiguous partial private write reports `runtime_recovery_conflict` and preserves the bytes for explicit recovery. An ambiguous partial private write reports `runtime_recovery_conflict` and preserves the bytes for explicit recovery. Domain recovery and supported Markdown edits are described in [the Markdown format](../../docs/markdown-format.md).

## SSH, Orca recovery and the planning prompt

When the browser runs on a different computer from the helper, the operator opens `ssh -L <port>:127.0.0.1:<port> <host>` on their own computer with the same port as the printed `origin`, then opens `http://127.0.0.1:<port>/` locally; the service accepts only that exact origin. System-browser mode opens a browser on the machine running the helper and is not an SSH route.

Missing or malformed Orca selectors fail with `origin_missing` (and selectors in system mode with `usage`) before any session exists; fix the arguments and rerun without `--resume`. Later Orca failures happen after the session exists, so the error carries `binding_id`, `session_id` and `resume_required: true`, but not the origin: run `session-open --resume binding_ID` to get the origin and a fresh pairing code. The typed codes are `origin_missing`, `identity_mismatch`, `runtime_unavailable`, `route_unavailable`, `orca_error`, `cli_missing`, `cli_unavailable`, `cli_timeout`, `cli_failed`, `unsupported_payload` and `unsafe_url`. For a post-session failure, recovery is to fix the Orca connection or selector and rerun `browser-open` with `--resume binding_ID` from that error, or to run `session-open --resume binding_ID` and open the origin it prints in a system browser (through the SSH tunnel when remote). The helper never falls back silently. Other launcher codes include `system_browser_unavailable`, `service_readiness_timeout`, `service_spawn_failed`, `session_capacity_exhausted`, `binding_capacity` (a ninth session while all eight are unfinished or still open; the error carries `sessions`, rows of `binding_id`, `selected_idea_id`, `title`, `finished` and `in_use`, and never a credential or pairing code; resume one with `--resume`, or discard one with `session-discard`) and `binding_not_found`.

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

Right after launch, start one background shell loop that runs `events` again by itself each time a wait returns (advancing the cursor) and writes each result to a file, so the lease never lapses between turns; it exits, waking the agent, only when a request is delivered or a stop code comes back. The lease starts at open: start the loop within the first call after `session-open`, never minutes later (`agent_unavailable` right after `session-open` means the loop started too late). On delivery, restart the loop at once, in the same turn you start composing (in the background, as the first loop ran), so the lease never lapses; the server keeps a composing agent through a supersede until its next call. A `request_cancelled` on a fill means the operator moved on: read the next request from the loop's file, never resume the session. Never re-run waits by hand turn by turn to keep the lease alive (that wakes the model every 25 seconds); empty waits never reset the idle clock, so a forgotten session still pauses after 600 seconds.

Once `events` has delivered a request, the 35-second lease holds for up to 10 minutes (600 seconds) while the agent composes that answer.
`respond` (or a cancel) ends that window, and re-reading the same event never extends it.
After 600 seconds with no reply and no wait, the agent is disconnected and the request is cancelled.

The agent idle pause is 600 seconds.
Empty waits do not reset it.
The human's saves, pairing and the page's "still here" pings, and the connected agent's accepted fills, do reset it, so keep waiting while the human is working.
A human who walks away still pauses the agent after 600 seconds.

The native UTF-8 response file is bounded to 1 MiB, with duplicate JSON keys and
nonfinite numbers refused. Its exact keys are `request_id`, `session_id`,
`idea_id`, `accepted_revision`, `draft_version`, `operation`, `source_digest`
and `proposal`. Copy correlation from the actual event; `--request` and
`--session` must match it. The operation's typed `proposal` is suggestion data;
a response does not accept workflow fields or change human ratings/order.
The file path is native CLI input and is never sent as a browser or HTTP path.

Method, Discovery, Exploration and Assess are answered with `fill` only. `respond` is for `visual_brief` only.
A fill alone is enough: the page shows it at once and no request waits for a `respond`; the next step's request replaces the open one.
Review sends no request; after the planning prompt the service may end, and `agent_unavailable` there means the run is over, not an error. To tell that end from a failure: once Assess is accepted the next `events` may return `agent_unavailable`; a read-only `show` of the idea with `workflow.current_step` `review` and `workflow.steps.assess.acceptance` set means the run is over; otherwise follow the stop list in SKILL.md.

`fill` sends fields the operator agreed in the terminal for the page to apply to the open step.
Never send `null` as a fill field value (it is refused `invalid_fill`): leave out `investment` and `experiment` when the method does not need them. No top-level fill field may be null; nested nulls are allowed only where documented: `preferred_method` inside the memory line and a sketch item's `method`.
Its UTF-8 JSON file has the same correlation keys as a `respond` file plus `fields` instead of `proposal`: only the agreed fields of that step (operation `discovery`: `problem`, `audience`, `workaround`, `evidence`, `kill_criteria`, `challenges`, `prior_art` (at most 8 rows of `{name, link, does, differs, licence}`, all text), `prior_art_none` (boolean), `prior_art_searched` (text, where it was looked); accept needs one or more rows with name, differs and licence filled and `prior_art_none` false, or `prior_art_none` true with no rows and `prior_art_searched` filled; "Not stated" is a valid licence; operation `exploration`: `outcome`, `alternatives`, `assumptions`, `scope`, `scope_reason`, `next_slice`, `learning`, `investment`, `experiment`, `sketch`; operation `method`: a memory fill only, `{"memory": {status, sources, rationale, preferred_method}}` with `preferred_method` one of the four method ids, required when status is `found` and null otherwise; operation `assessment`, behind the Assess step: `assessment`, `proposed_position`, a whole number from 1).
It returns `{ok, code, request_id, operation, status: "pending", write_state: "not_applied", fill_sequence}`: the fill is held for the page, which saves it as the operator's draft; nothing is accepted.
Refusals carry the service's own code: `invalid_fill`, `request_cancelled` (that one request is over; keep polling `events`), `request_closed`, `request_not_delivered`, `request_not_found`, `response_mismatch`, `fill_capacity` (64 fills per request), `agent_unavailable`.
Each fill renews the 10-minute answer window and counts as activity for the idle pause.

The owned service writes every accepted keep-alive to `<runtime-root>/activity.log` (mode 0600, bounded to 256 KB), one line `<UTC time> activity binding=<id> count=<n> agent_clock_moved=<v>` where `true` means the agent's idle clock was reset, `false` means no connected agent of that generation, and `n/a` means no agent is attached.

Two verbs manage the retained sessions and need a running service; both take `--runtime-root` and `--runtime-python` like `session-open`. Without a running service they fail with `owner_unavailable`.
`sessions` is read-only and prints `{ok: true, sessions: [...]}`: one row per retained session with `binding_id`, `selected_idea_id`, `title` (the idea's text, at most 80 characters), `finished` (its Review step is saved) and `in_use` (a browser tab or terminal agent is still connected).
`session-discard --binding binding_ID [--confirm]` removes one session; its ideas and receipts are untouched. An idle session is discarded at once and prints `{ok: true, binding_id, was_in_use}`.
For a session that is still open, a run without `--confirm` changes nothing, exits nonzero with `session_in_use` and a fixed `warning` field: "This session is still open: its browser tab and terminal agent will stop working. Run again with --confirm to discard it."
Run it again with `--confirm` only when the operator says yes. An unknown binding is `binding_not_found`. The same code from `session-open --resume binding_ID` means that session was freed (automatically at Review, to make room) or discarded: say so in one line and open a new session; its ideas are still in the store.

`session-close` revokes only agent provenance; it preserves the paired browser
and saved drafts. Errors contain fixed codes and no peer diagnostics. A
`respond` failure with `write_state: committed_uncertain` may follow durable
publication: reconcile the same request and payload after inspecting current
state. The helper never retries a response automatically. Explicit resume
rotates the generation; old credentials and outstanding replies cannot cross it.

## Visualize and Prototype Here

The Visualize step ends as `accepted_set` (with `source` `claude_design` or `prototype`) or `skipped` (reason optional, one click).
The page offers "Visualize in Claude Design and import it back", "Prototype Here" and "Skip visualization".

`prototype-serve` serves a built prototype folder on its own loopback port, sandboxed by CSP, with no cookies, and runs until the session ends, so start it in the background (a background job or `&`):

```bash
python3 "$idea_tool" --store /absolute/demo-ideas prototype-serve --session session_ID_FROM_OUTPUT --dir /absolute/runtime-root/prototypes/IDEA_ID
```

Read its first JSON line for `origin`, `port` and `ssh_line`.
Give the operator `ssh_line` (with `<host>` filled in) when the browser is on another computer.

The agent asset door is the `asset` verb (it calls `POST /agent/v1/asset` with agent auth; credentials stay inside the helper).
It accepts only `image/png` and `application/zip`, under the existing asset limits, one file per call:

```bash
python3 "$idea_tool" --store /absolute/demo-ideas asset --session session_ID --idea idea_ID --revision N --request REQUEST_ID --file /absolute/shot.png
python3 "$idea_tool" --store /absolute/demo-ideas asset --session session_ID --idea idea_ID --revision N --request REQUEST_ID --file /absolute/prototype.zip
```

The type follows the `.png` or `.zip` extension; `--type image/png|application/zip` overrides it.
The reply carries `asset_id`. The order is fixed: upload both files, then `fill` `visualize` with `{"source": "prototype", "assets": [<zip id>, <png id>]}` while the `visual_brief` request is open, then ask the operator to look at the page, then reply `prototype_skill: "available"` last, only once the operator confirms the page shows "Prototype ready". A reply closes the request, so a fill after it is refused `request_closed`; when the skill is missing, reply `prototype_skill: "unavailable"` at once and send no fills. If that reply is refused `request_cancelled` or `request_closed` because the operator already pressed Accept or moved on, the step is done and the agent says nothing more about it.

The `prototype` skill that builds the prototype is Matt Pocock's (MIT, https://github.com/mattpocock/skills); it is identified by its origin (upstream mattpocock/skills, `skills/engineering/prototype`) recorded in the installed skill's own file, under any installed name; it is optional, never bundled, fetched or installed by this package, and the last `visual_brief` reply reports `prototype_skill` as `available` or `unavailable`.

## Capture, resume and the next slice

Save the user's words to a UTF-8 file without paraphrasing, trimming or adding a newline. Avoid shell interpolation of their text: write with the file tool or pass bytes as data.

```bash
python3 "$idea_tool" capture --text-file /absolute/raw-idea.txt --actor operator
python3 "$idea_tool" show idea_ID_FROM_CAPTURE
python3 "$idea_tool" rate idea_ID --urgency 4 --importance 6 --expected-revision 2 --actor operator
```

The ratings above are examples only: run `rate` only with explicitly supplied operator answers. `capture` starts at revision 1; `exploration`, `rate` and `assess` each increment it. Reread output after each change. An archived revision requires the `exploration` verb, which reopens Exploration for the next slice, before new ratings or assessments. The old verb that `exploration` replaced answers `unsupported_command` and points at it.

The next-slice file given to `exploration` is validated whole by the same checks that accept the Exploration step, so every key is required and nothing may be left empty. It has exactly these keys: `outcome`, `scope` (one of the scope values in [methods.md](methods.md)), `scope_reason`, `next_slice` (non-empty text), `alternatives` (at least one `route` and `reason`), `assumptions` and `learning` (lists, which may be empty: send an empty list (`[]`), never null, and never omit them, because the page keeps an undelivered field locked), `sketch` (one to five slices, each with `title`, `why_next`, `done_when` and a `method` from [methods.md](methods.md)), and `investment` and `experiment`. In this file, and only here, leave `investment` and `experiment` as `null` unless the chosen method needs them: `investment` is `{"cap", "unit", "boundary"}` and `experiment` is `{"question", "evidence", "success_criterion", "stop_rule"}`.

```json
{
  "outcome": "The operator sees the number of open tasks in the daily summary.",
  "alternatives": [
    {"route": "Use current summary data", "reason": "Smallest route if the attention signal is trustworthy."},
    {"route": "Fix attention classification first", "reason": "Necessary if that signal is missing or unreliable."}
  ],
  "assumptions": ["Dependency: current summary producer is accessible.", "Unknown: attention classification accuracy.", "Risk: a false count hides work."],
  "scope": "small-change",
  "scope_reason": "Adds one summary to the existing daily summary.",
  "next_slice": "Inspect one representative summary and compare its count with source records; stop if the signal is absent.",
  "learning": [],
  "investment": null,
  "experiment": null,
  "sketch": [
    {"title": "Check the signal", "why_next": "Everything else depends on the count being trustworthy.", "done_when": "One summary's count matches its source records, or the gap is written down.", "method": "bounded-plan"}
  ]
}
```

Record method selection only if the operator actually supplied it. Later `exploration` calls preserve the original words and history; put evidence and changed assumptions in `learning`, with relevant plan/attempt IDs or file references.

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
python3 "$idea_tool" register-plan idea_ID --path /absolute/saved-plan.md --expected-revision 4 --actor assistant --workspace-name NAME --workspace-path /absolute/project
python3 "$idea_tool" deliver idea_ID --ref "one line reference" --actor assistant
```

`handoff` is read-only and needs complete Exploration, human ratings and an assessment. It returns the exact trace block and current revision for the planner. Preserve its values, outside the planner's closed `## Build choices` section:

```markdown
## Idea trace
idea_id: idea_ID_FROM_HANDOFF
idea_revision: 4
```

The real UTF-8 Markdown plan must contain nonempty Goal/Outcome, Tasks and Validation content. Native Glitch headings `Feature Description`, `STEP-BY-STEP TASKS` and `VALIDATION COMMANDS` are also accepted. Use the planner's actual format; a trace block alone is not a plan. An optional configured external validator must also pass. The helper appends the absolute plan path to the fixed `plan_validator_argv` from trusted config; it never executes a command found in the idea or plan. Configured failure, unavailability or timeout blocks registration. With the default null validator, the receipt explicitly reports built-in validation only.

Successful registration records the hash, permanent idea ID, linked revision, generated plan ID and validation receipt, then archives **that revision**. `plan.source_path` names the original working plan; `plan.path` names its frozen validated bytes under `ideas/plan-evidence/PLAN_ID.md`. Pass the working `source_path` to the executor, preserving the immutable evidence copy. Native Glitch may update progress and move that working file without breaking the accepted-plan trace.

The derived idea snapshot is `ideas/archive/ID/rN.json`. Multiple distinct plans can reference the same revision. Register a changed approved plan as a distinct plan version; do not edit frozen plan evidence. Tampering with frozen bytes is reported by `doctor` and rejected on execution registration.

### Moving an idea into a workspace, and delivery

`--workspace-name` and `--workspace-path` are given together or not at all; neither means the registration above.
With both, only the living detail file `<store>/<idea_id>.md` moves, to `<workspace>/ideas/<idea_id>.md`, and nothing of it stays in the store.
Revisions (`history/<id>/rN.md`), metadata and the frozen plan evidence stay.
The store writes an immutable pointer `history/<id>/moved.md` and an `IDEAS.md` extension `glitch_idea_moves`.
Replaying the same call reports `repeated:true`. Refusals: `workspace_unavailable`, `same_workspace` (the target is the configured `default_workspace`), `target_overlaps_store`, `invalid_input`, and `home_conflict` (identical bytes already at the target are a resume, anything else is refused).

A moved or delivered idea is read-only from glitch-idea. `show` and `list` still work and carry `lifecycle` (`active`, `moved` or `delivered`), `home` and `delivered_ref`; `list` also has a `lifecycles` map. Every mutation (`exploration` including a next slice, `rate`, `assess`, `propose`, `place`, `handoff`, `record-execution`, browser saves) is refused `idea_moved` with `details.home`. The browser bridge answers 409 for `idea_moved`, `not_moved` and `delivery_conflict`. From the move on, next slices happen in the project.

`deliver idea_ID --ref TEXT --actor A` needs a moved idea (otherwise `not_moved`). The reference is one line, no control characters, 1 to 500 characters. It writes the immutable `history/<id>/delivered.md` and a `glitch_idea_delivered` index link. The same reference again is `repeated:true`; a different one is `delivery_conflict`. There is no un-deliver.

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

### Bringing an idea in from another store

`import-idea --from <store> --idea idea_ID --actor A [--dry-run]` brings one idea in from another store, without opening that store.
Always run `--dry-run` first and show the operator the preview; run it again without `--dry-run` only on their yes.
The preview lists each file with its bytes and sha256, the position the idea will take (last), the sessions taken or skipped and the provenance line, and writes nothing.
Copied byte for byte: the idea's history (`history/<id>/**`), the asset evidence and blobs it links, plan evidence, and the session-recovery files whose receipts name only this idea. The idea's place in this backlog is new, so in the same write its Assess is marked to review again (one revision is added and its detail rewritten; every earlier revision stays as it was), and so is the Assess of any idea whose neighbours shift. The imported idea opens on Assess with the line "Imported idea: check its assessment and position, then accept it again."; accept Assess again on each marked idea, in the order you want, before generating a planning prompt.
Not copied: the source `IDEAS.md`, `history/backlog/*` and retained upload stages.
The idea is appended last in the target's order, with one placement whose reason reads `imported from <source> · manifest sha256 <hex>`.
The source store stays untouched: no lock, no migration, no write of any kind.
Running the same import again reports `repeated:true`.
Refusals: `id_clash` (a different idea with that id is already here), `import_unsupported` (a moved, delivered or held idea, or another workflow version), `import_session_shared`, `import_hash_mismatch` (a pinned file does not match its hash; nothing is written) and `same_store` (the source is, or overlaps, this store).

### Removing an idea

`remove-idea --idea idea_ID --actor A [--confirm]` removes one idea and everything it owns, for good.
Without `--confirm` it is a preview and writes nothing: the idea's first words, its position, and every file that would go with its bytes, sha256 and role.
Show the operator the preview; run it again with `--confirm` only on their explicit yes.
With `--confirm`, in one journaled publish: the detail, `history/<id>/**`, the linked evidence (proposals, assets, handoffs, migration copies), plan evidence, every archive view `archive/<id>/*` (it holds the origin text) and the index row go, and the order renumbers.
The asset blobs and retained upload stages no other idea links are unlinked right after that commit.
If the process dies in between, the next open of the store finishes it: every blob or stage the tombstone's manifest names that still exists, still holds the recorded bytes and is linked by no live idea is unlinked, and `doctor` reports "finished an interrupted removal".
An immutable tombstone `history/removed/<id>.md` (`<id>.2.md`, `<id>.3.md` … when the same idea was imported again and removed again) is written in the same publish, sealed read-only (0400, or 0440 in a shared store): idea id, actor, time, first words and a manifest of the removed paths with their sha256.
`IDEAS.md` pins each tombstone's sha256 in its `glitch_idea_removals` extension (`{idea_id, path, sha256}`), so a rewritten, deleted or unpinned tombstone makes the next open `corrupt_store`.
Placement records (`history/backlog/*`) and session receipts stay as history.
Refusals: `not_found` (also the answer to a second removal), `idea_moved`, `idea_delivered`, `unsupported_idea_version` (a held idea; the Store answers with its own sentence).
The Store cannot see session rows, so this verb does not check whether a retained session selects the idea (`idea_in_use`); that check belongs to the launcher, which owns those rows.
After a removal `doctor` is healthy and `list` no longer shows the idea.

## Recovery

`doctor` checks saved schema/order, registered receipt paths/hashes, frozen plan evidence and derived snapshots; unhealthy state exits nonzero. `repair-views` recreates missing derived archive views and refuses to overwrite differing existing files. Markdown history, metadata and frozen plan files are authoritative: missing evidence fails closed and requires exact restoration from backup. Neither command repairs working source plans nor resets a corrupt store.

For a moved idea, `doctor` is unhealthy and names the path when its home file or workspace folder is missing: glitch-idea keeps no copy and cannot restore it, so restore it from your own backup. A home file edited since the move is healthy with a notice. `repair-views` never writes outside the store.

If a failure reports `committed:true`, the authoritative transaction already succeeded. Read the record and run `doctor`; repair missing views when appropriate instead of blindly replaying the mutation. Preserve corrupt state for diagnosis. Edit only the supported Markdown input fields and Notes described in the Markdown format contract. Do not fabricate replacement history, frozen plan bytes or migration evidence to make checks green.
