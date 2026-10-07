# glitch-idea

Capture a software idea before it disappears, explore alternatives, and choose the next useful step before detailed planning.
The skill keeps the operator's urgency and importance separate from the assistant's assessment, preserves the operator's backlog order, and links each idea to validated plans and execution evidence.
A local browser flow walks an idea through Capture, Shape, Method, Visualize, Assess and Review, and hands a ready-made `/glitch-plan` prompt back to you.

This is an independent, source-only extension.
It is not an upstream Glitch engine release.
It handles **new ideas only**; it does not import, synchronise or modify an existing backlog.
Glitch's planning and execution workflows are optional integrations.

## Status

| Platform | State |
| --- | --- |
| Linux | Source and installer are exercised by the test suite and by the walk below. |
| macOS | Not yet qualified. The POSIX code path is shared with Linux, but no verification rows exist yet. |
| Windows (native) | Not yet qualified. Browser launch refuses to start on Windows (`privacy_unqualified`) until owner-private runtime directories are qualified there. The command forms below are shown so that the installer and the helper can be run, not as a claim that the browser flow works. |

Where this document says "not yet qualified" it means exactly that: nothing has been observed, so nothing is claimed.

## Requirements

- Python **3.10 or newer**, for both the installer and the runtime.
- **PyYAML 6.0.3 (MIT)**, pinned in `glitch-idea/requirements.txt`, inside the runtime interpreter's environment.
- A browser on the machine you sit at (the operator's own computer).

The installer never downloads anything.
If the runtime lacks PyYAML it stops with error code `runtime_prerequisite` and prints the exact `pip install` command for you to run yourself, then you rerun the installer.

## Choose an interpreter

| System | Interpreter for `install.py` and for creating a venv | Venv interpreter (the runtime Python) |
| --- | --- | --- |
| macOS, Linux | `python3` | `<venv>/bin/python` |
| Windows | `py -3` | `<venv>\Scripts\python.exe` |

The installer's `--venv` creates the environment with the standard library only (`python -m venv`, offline) using the interpreter that runs `install.py`.
You may instead create it yourself (`python3 -m venv /absolute/path/to/venv` or `py -3 -m venv C:\absolute\path\to\venv`) and pass it to `--venv`; a directory that already holds `pyvenv.cfg` is reused.
`--runtime-python /absolute/path/to/python` selects an existing interpreter instead; give either `--venv` or `--runtime-python`, not both.

## Install

From this repository's root.
Replace the placeholders with your own absolute paths.

```bash
python3 install.py --dry-run --skills-root ~/.codex/skills --store /absolute/path/to/ideas --venv /absolute/path/to/venv --runtime-root /absolute/path/to/runtime
```

On Windows use `py -3 install.py ...` with the same flags.

1. **Dry-run first.**
   It validates every input and changes nothing: no skill files, no venv.
   The result's `runtime.status` is `would_create_venv` when the venv does not exist yet.
2. **Create the runtime-root directory** with owner-only access (`mkdir -m 700 /absolute/path/to/runtime` on macOS/Linux).
   The service itself also creates the runtime directory with mode 0700 when it is missing.
3. **Install** by repeating the command without `--dry-run`.
   On a fresh venv this stops with `runtime_prerequisite`.
   The venv has been created and is kept.
4. **Run the printed command yourself**, for example `"/absolute/path/to/venv/bin/python" -m pip install -r "/absolute/path/to/glitch-idea/requirements.txt"`.
   This is the only step that fetches a package, and it is yours to run and to approve.
5. **Rerun the install command.**
   It now reports `"action": "installed"` and `runtime.status` of `ready`, with the detected Python and PyYAML versions.

`runtime.status` is one of `ready`, `not_configured` (no `--venv`, `--runtime-python` or configured runtime; browser launch will not work), `would_create_venv` (dry-run only) or `missing` (dry-run with a `--runtime-python` that does not exist).

Rules the installer enforces:

- It creates real files, never symlinks, refuses unrelated name collisions and leaves an identical installation unchanged.
- It never installs into a Glitch Brain's engine-managed files.
  A Brain is recognised by its engine updater next to its operations manual.
  The Brain's `workspaces/` and `_local/` folders are member-owned and are allowed.
  The skills root, the store, the venv and the runtime root are all checked.
- The store, venv and runtime root must be outside the source and installed skill trees; the venv must also be outside the store and the runtime root.
- Upgrades keep the installed `config.json` and the store location, and move the previous skill to `<skills directory>/.glitch-idea-backups/<timestamp>-<id>/`.
  Changing `--store` selects a location; it does not migrate or delete data.
- Run one installer at a time.
  If an upgrade is interrupted between backing up and replacing the skill, restore the saved copy before retrying.

The default skills root is `~/.codex/skills` (`--skills-root` selects another personal directory).
No configuration file is required; the default store is the `ideas/` folder inside this checkout (`<checkout>/ideas`), outside the installed skill, so deleting the checkout deletes that store unless you chose another with `--store`.
Keep that directory when replacing or removing the checkout.

## Configuration

Copy `config.example.json` to `config.json` beside `install.py` if you need custom settings before installation.
Supported keys, and only these:

| Key | Meaning |
| --- | --- |
| `store_path` | Durable idea store. Relative values resolve against the config file's directory. |
| `plan_validator_argv` | `null`, or a list of 1 to 32 nonempty strings. |
| `validator_timeout_seconds` | Integer from 1 to 120 (default 30). |
| `runtime_root` | Absolute path of the owner-private runtime directory. No symlink components. |
| `runtime_python` | Absolute path of the runtime interpreter. |
| `default_workspace` | Optional `{"name": ..., "path": ...}`: an absolute, existing folder that capture prefills (the field stays editable). A configured folder that does not exist is an `invalid_config` error. |

Unknown keys, a key present with `null` for `runtime_root` or `runtime_python` (omit the key instead), a relative or symlinked `runtime_root`, and over-limit values are refused with an error rather than quietly dropped.
That validation happens when the installer runs: the helper itself reads only the keys it uses and ignores others, so after editing an installed `config.json` by hand, rerun the installer to validate it.
The installed skill reads the config the installer generated, with absolute paths; edit that file deliberately if you want to change an installed setting.

`plan_validator_argv` defaults to `null`: built-in validation checks the plan's trace and required sections.
To use an existing Glitch plan validator, configure a fixed argument list; the helper appends the plan's absolute path.
A configured validator must succeed within the timeout; failure or absence blocks registration.
Commands embedded in captured ideas or plans are never used as validators.

## Launch and resume the browser flow

An installed copy must be run with its configured runtime interpreter.
Define the paths once (these are your own values):

```bash
idea_python=/absolute/path/to/venv/bin/python
idea_tool=~/.codex/skills/glitch-idea/scripts/idea.py
```

```bash
"$idea_python" "$idea_tool" browser-open --browser system
"$idea_python" "$idea_tool" session-open
"$idea_python" "$idea_tool" session-open --resume binding_ID_FROM_OUTPUT
```

- `browser-open` starts the local service if it is not running, creates a session and opens a dedicated browser tab at `http://127.0.0.1:<port>/`.
  `session-open` does the same without opening a tab and prints the `origin`.
- Both print a one-time `pairing_code`.
  Enter it in the page within 60 seconds.
- Keep the `binding_id`.
  `--resume binding_ID` re-pairs the same binding and keeps its saved selection and save receipts.
  A lost or uncertain NEW result is never retried automatically, and an error carrying `resume_required` must be resumed, not replaced by a fresh session.
- The service accepts only its exact `127.0.0.1:<port>` origin.
  It exits after 15 minutes without authenticated activity; saved ideas survive and the next launch needs fresh pairing.
- The runtime root (default `.local/state/glitch-idea` under your account's home directory as the system records it, which on macOS and Linux is the password-database home, not `$HOME`) must be owned by you with mode exactly 0700, and no component of it may be a symlink.
  A looser directory is refused with `runtime_not_private`; keep it free of ACLs as well.

### When the browser is on another computer (SSH)

The browser opens on the computer you sit at, but the service listens on the machine where the helper ran.
So that your browser can reach it:

1. Run `session-open` (not `browser-open --browser system`) on the remote machine and note the port in the printed `origin`.
2. On **your own computer**, open a tunnel with the **same port**: `ssh -L <port>:127.0.0.1:<port> <host>`.
3. Open the printed `http://127.0.0.1:<port>/` locally and enter the pairing code.

A different local port fails, because the service accepts only its exact origin.
The system-browser mode opens a browser on the machine running the helper; it is not an SSH substitute.

### Orca

`browser-open --browser orca` opens the page in the Orca browser pane of the terminal that started it.
It requires explicit selectors for that terminal, never the "current" one:

```bash
"$idea_python" "$idea_tool" browser-open --browser orca --orca-worktree 'UUID::WORKTREE_PATH' --orca-terminal TERMINAL_HANDLE --orca-host EXECUTION_HOST
```

`--orca-host` is optional.
Selectors are refused in system mode.
Missing or malformed selectors fail with `origin_missing` before any session exists: fix the arguments and rerun, without `--resume`.
If Orca fails after that, the session and binding are already created: the error carries `binding_id`, `session_id` and `resume_required`, and the helper does **not** silently fall back to another browser.
See the [runbook](docs/browser-runbook.md#orca-unavailable-or-misrouted) for the error codes and recovery.

## Use the helper directly

```bash
python3 glitch-idea/scripts/idea.py list
python3 glitch-idea/scripts/idea.py capture --text-file /absolute/path/to/idea.txt --actor operator
python3 glitch-idea/scripts/idea.py doctor
```

From a source checkout, run these with an interpreter that has PyYAML.
Capture preserves the UTF-8 text file verbatim.
The helper returns JSON and a nonzero exit status for rejected requests.
An explicit `--store /absolute/path` before the subcommand selects an isolated store for a demonstration or test.

Invoke **`$glitch-idea`** (Codex) or **`/glitch-idea`** (Claude Code) in an agent session that has discovered the installed skill.
The workflow saves the original text first, asks for urgency and importance independently, then aligns the idea with you in your terminal (Methods, Discovery, Exploration, Visualize, Assess), the A of APIV before /glitch-plan.
You can stop with an incomplete record and resume by its permanent ID.

The [skill instructions](glitch-idea/SKILL.md), [command reference](glitch-idea/references/commands.md) and [method comparison](glitch-idea/references/methods.md) cover shaping, assessments, ordering, plans and recovery.
The four scope classes are small change, capability, project and epic.
The four development choices are bounded plans, adaptive slices, appetite-led shaping and experiment-led discovery.
No scope class automatically determines a method or score.

## Getting the planning prompt

When the browser's Review step is ready, **Generate planning prompt** saves an immutable planning packet, and **Copy planning prompt** puts a prompt beginning `/glitch-plan` on your clipboard.
Paste it into a new window or pane to start planning with the exact idea trace.
Copying does not archive the idea or start planning; only `register-plan` on a real validated plan archives that revision.
Details: [handoff format](docs/handoff-format.md) and the [runbook](docs/browser-runbook.md#getting-the-glitch-plan-prompt).

## Data and recovery

`ideas/IDEAS.md` owns backlog order; each `idea_<id>.md` owns that idea's current structured fields and Notes.
Linked history, metadata and frozen `plan-evidence/` files are immutable authority.
Edit only the fields and Notes the [Markdown contract](docs/markdown-format.md) allows, and only while the browser is paused.
Existing v1 JSON stores migrate on the first successful write; the exact old bytes become frozen migration evidence and the old binary refuses further use.

`doctor` reports missing or changed artifacts.
`repair-views` can recreate a missing derived archive view, but cannot reconstruct missing authoritative history or frozen plan bytes; restore those exact bytes from your backup.
It never overwrites differing existing evidence.
The full operator procedure, including upgrade, pause and resume, migration limits and storage guarantees, is in the [browser runbook](docs/browser-runbook.md).

Actual `config.json` files, idea stores, execution evidence, environments and caches are ignored by version control.
There is no built-in backup or network sync service; keep your own backup of the store and durable execution receipts.

## Moving an idea into a project

When a plan is registered for a different workspace, `register-plan` takes `--workspace-name` and `--workspace-path` (both or neither) and moves the idea's living detail file to `<workspace>/ideas/<idea_id>.md`.
History, metadata and frozen plan evidence stay in the store, with an immutable pointer.
A moved idea is read-only from glitch-idea; further slices happen in the project.
`deliver idea_ID --ref TEXT` marks a moved idea delivered, once and permanently.
The Ideas overview labels these rows "In progress" and "Delivered"; both are hidden by default and show counts.
`doctor` reports a missing home file or workspace folder, because glitch-idea keeps no copy of it.

## Where the storage integration will live

The storage and configuration integration in this package (`install.py`'s effective configuration and the helper's `configuration`) is intended to be relocated into the managed Glitch engine in a future engine rebuild.
That relocation has not happened: today this package is installed into a personal skills directory, and the installer refuses to write into an engine-managed Brain.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests use temporary stores and install directories.
They cover capture, scores, stale and conflicting writes, concurrent writers, plan validation, immutable evidence, recovery, the installer, the browser assets and a real package installation without private config.
The external Glitch validator integration test skips with a stated reason unless a local `plan_validator_argv` is configured; all other tests run without Glitch.

### Optional external dependency

"Prototype Here" in the Visualize step uses Matt Pocock's `prototype` skill (MIT, https://github.com/mattpocock/skills).
It is optional, lives in your own skill set, and is not bundled, fetched or installed by this package.
Without it, the page tells you where to get it and the rest of the workflow is unaffected.

## License

[MIT](LICENSE); see the file for the copyright holder.
Agents using the skill should attribute their own actions to their actual identity.
