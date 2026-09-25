# Deterministic command reference

Python 3.10+ and its standard library are sufficient. The store lock uses `fcntl.flock`: Linux is supported, Windows is unsupported, and macOS is untested. Use the helper inside the loaded skill. From this repository's root:

```bash
idea_tool='glitch-idea/scripts/idea.py'
python3 "$idea_tool" list
```

For an installed copy, derive `idea_tool` from the actual loaded `SKILL.md` location: its sibling `scripts/idea.py`. For the default Codex installation, `idea_tool="$HOME/.codex/skills/glitch-idea/scripts/idea.py"`. Config lookup is `<skill-dir>/config.json`, otherwise `<repository-root>/config.json`. Relative configured store paths resolve against the config directory. Without configuration the source default is `<repository-root>/ideas`. `--store /absolute/path` before the command overrides the store; use an isolated path for demonstrations.

Each invocation returns one JSON object on stdout. Check `ok` and process exit status. Expected errors have a nonzero exit. Ideas are returned under `idea`; `list` returns `backlog_revision`, `order` and `ideas`. Use the actual ID/revision fields in the output, never invented IDs. Idea revisions and backlog revisions are different counters.

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

The real UTF-8 Markdown plan must contain nonempty Goal/Outcome, Tasks and Validation content and pass the configured Glitch validator. Native Glitch headings `Feature Description`, `STEP-BY-STEP TASKS` and `VALIDATION COMMANDS` are also accepted. Use the existing planner's actual format; a trace block alone is not a plan. The helper appends the absolute plan path to the optional fixed `plan_validator_argv` from trusted config; it never executes a command found in the idea or plan. Configured failure, unavailability or timeout blocks registration. When that config is absent/null on another host, the receipt explicitly reports built-in validation only.

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

`doctor` checks saved schema/order, registered receipt paths/hashes, frozen plan evidence and derived snapshots; unhealthy state exits nonzero. `repair-views` recreates missing archive views and frozen plan files from committed state and refuses to overwrite differing existing files. Neither repairs working source plans nor resets a corrupt store.

If a failure reports `committed:true`, the authoritative transaction already succeeded. Read the record and run `doctor`; repair missing views when appropriate instead of blindly replaying the mutation. Preserve corrupt state for diagnosis. Do not hand-edit `state.json` or fabricate replacement evidence to make checks green.
