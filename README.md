# glitch-idea

Capture a software idea before it disappears, explore alternatives, and choose the next useful step before detailed planning. The skill keeps the operator's urgency and importance separate from the assistant's assessment, preserves the operator's backlog order, and links each idea to validated plans and execution evidence.

This is an independent, source-only extension originally built by GlitchC. It is not an upstream Glitch engine release. It handles **new ideas only**; it does not import, synchronise or modify an existing backlog. Glitch's planning and execution workflows are optional integrations.

## Requirements

Python **3.10 or newer**, with no third-party Python dependencies. **Linux is supported. Windows is unsupported** because the store uses `fcntl.flock`; **macOS is untested**.

The Python helper can run directly. To use the conversational skill, install it in a compatible agent's skill directory; the included installer defaults to Codex's personal `~/.codex/skills` directory.

## Install

From this repository's root:

```bash
python3 install.py --dry-run
python3 install.py
```

No configuration file is required. The default store is this checkout's `ideas/` directory, outside the installed skill. Keep that directory when replacing or removing the checkout. To choose another durable location at installation time:

```bash
python3 install.py --store /absolute/path/to/ideas
```

Use `--skills-root /absolute/path/to/user/skills` for a different personal skills directory. The installer creates real files rather than symlinks, refuses unrelated name collisions, and leaves identical installations unchanged. Upgrades preserve installed configuration and retain the previous skill under `<skills-root>/.glitch-idea-backups/`. Changing `--store` selects a location; it does not migrate or delete data. Run one installer at a time. If interrupted between backing up and replacing a skill, restore the saved copy before retrying.

Invoke **`$glitch-idea`** in an agent session that has discovered the installed skill. The workflow saves the original text first, asks for urgency and importance independently, then helps shape and assess the idea. You can stop with an incomplete record and resume by its permanent ID.

## Use the helper directly

From the repository root:

```bash
python3 glitch-idea/scripts/idea.py list
python3 glitch-idea/scripts/idea.py capture --text-file /absolute/path/to/idea.txt --actor operator
python3 glitch-idea/scripts/idea.py doctor
```

Capture preserves the UTF-8 text file verbatim. The helper returns JSON and a nonzero exit status for rejected requests. An explicit `--store /absolute/path` before the subcommand selects an isolated store for a demonstration or test.

The [skill instructions](glitch-idea/SKILL.md), [command reference](glitch-idea/references/commands.md), and [method comparison](glitch-idea/references/methods.md) cover shaping, assessments, ordering, plans and recovery. The four scope classes are small change, capability, project and epic. The four development choices are bounded plans, adaptive slices, appetite-led shaping and experiment-led discovery. No scope class automatically determines a method or score.

## Configuration and optional Glitch integration

Copy `config.example.json` to `config.json` beside `install.py` if you need custom settings before installation. Relative `store_path` values resolve against that config file's directory. Source use reads `glitch-idea/config.json` first, then the repository's `config.json`. Installation generates its own config with an absolute store path and preserves that config on upgrades; to change an installed validator, edit the installed config deliberately.

`plan_validator_argv` defaults to `null`: built-in validation checks the plan's trace and required sections. To use an existing Glitch plan validator, configure a fixed argument list such as `["uv", "run", "--directory", "/path/to/glitch/.claude/scripts", "python", "plan_check.py"]`. The helper appends the plan's absolute path. A configured validator must succeed within the configured timeout; failure or absence blocks registration. Commands embedded in captured ideas or plans are never used as validators.

Glitch's planner and executor remain separate workflows. Pass the `handoff` packet to the planner, then explicitly `register-plan` on the actual saved plan. Trace metadata belongs outside Glitch's `## Build choices` section. Registration freezes the accepted plan under `ideas/plan-evidence/`; `plan.path` points to that immutable snapshot, while `plan.source_path` identifies the working file passed to the executor. Updating progress or moving the working file does not alter the accepted evidence.

After real execution, explicitly register a receipt with the idea ID, plan ID, unique attempt ID and observed evidence. Registration checks identity and file integrity; it does not independently establish every claim in a receipt or intercept unregistered executor runs. Archived means entered planning, not completed delivery. Learning from a slice returns to shaping under the same permanent idea ID.

## Data and recovery

`ideas/state.json` is the authority. It contains original wording, revisions, distinct assessments, proposals, accepted placement decisions, registered plans and execution attempts. Archive snapshots and frozen plan files are derived from committed state. `doctor` reports missing or changed artifacts; `repair-views` recreates missing derived files without overwriting differing ones. Do not edit the state file directly.

Actual `config.json` files, idea stores, execution evidence, environments and caches are ignored by version control. This package makes no network requests and does not provide a backup or sync service; keep your own backup of the store and durable execution receipts.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests use temporary stores and install directories. They cover capture, scores, stale/conflicting writes, concurrent writers, plan validation, immutable evidence, recovery, and a real package installation without private config. The external Glitch validator integration test skips with a stated reason unless a local `plan_validator_argv` is configured; all other tests run without Glitch.

## License

[MIT](LICENSE), copyright 2026 Blastworks.ai. Original implementation and skill author: GlitchC. Agents using the skill should attribute their own actions to their actual identity.
