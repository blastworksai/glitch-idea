# Browser flow operator runbook

For the person who installs, launches and looks after the local glitch-idea browser flow.
Every command here was written against the code in this repository; where a platform has not been observed, the text says so.
Placeholders such as `/absolute/path/to/...` are yours to replace.

## 1. Platform status

| Platform | Installer | Browser launch and storage |
| --- | --- | --- |
| Linux | Run and observed. | Run and observed on Linux. |
| macOS | Not yet qualified (no verification rows). | Not yet qualified. The code path is the POSIX one shared with Linux; nothing has been observed on macOS. |
| Windows, native | Not yet qualified. | Browser launch is refused (`privacy_unqualified`) because owner-private runtime directories are not qualified there. |

Do not treat the Windows and macOS command forms below as a statement that those systems work.

## 2. Interpreters and paths

| System | Run the installer with | Create a venv with | Runtime interpreter inside the venv |
| --- | --- | --- | --- |
| macOS, Linux | `python3 install.py ...` | `python3 -m venv /absolute/path/to/venv` | `/absolute/path/to/venv/bin/python` |
| Windows | `py -3 install.py ...` | `py -3 -m venv C:\absolute\path\to\venv` | `C:\absolute\path\to\venv\Scripts\python.exe` |

Always run an installed skill with its venv interpreter by absolute path.
The helper refuses `serve`, `events`, `respond` and `session-close` when the running interpreter is not the configured `runtime_python` (`runtime_interpreter_mismatch`), because a venv's dependencies cannot be inferred from a base interpreter.

## 3. Install

### 3.1 Worked walk (macOS/Linux; Windows uses `py -3`)

Step 1, dry-run.
Nothing is created or changed.

```bash
python3 install.py --dry-run --skills-root /absolute/path/to/skills --store /absolute/path/to/ideas --venv /absolute/path/to/venv --runtime-root /absolute/path/to/runtime
```

Expected: exit 0, `"dry_run": true`, and `"runtime": {"python": ".../venv/bin/python", "status": "would_create_venv"}`.

Step 2, create the owner-private runtime root.

```bash
mkdir -m 700 /absolute/path/to/runtime
```

Step 3, install.
The venv is created offline from the standard library.

```bash
python3 install.py --skills-root /absolute/path/to/skills --store /absolute/path/to/ideas --venv /absolute/path/to/venv --runtime-root /absolute/path/to/runtime
```

On a fresh venv the expected result is exit 1 with `error.code` of `runtime_prerequisite`.
The message names the missing PyYAML and gives the exact command, and says the venv was created and kept.
No skill files were installed yet.

Step 3b, run the printed command yourself.
It has this form, with your real paths:

```bash
"/absolute/path/to/venv/bin/python" -m pip install -r "/absolute/path/to/checkout/glitch-idea/requirements.txt"
```

This is the only network step and the installer will never run it for you.
It needs PyYAML 6.0.3 (MIT); any other version is refused.
If you cannot use pip, any means that puts PyYAML 6.0.3 into that venv satisfies the check.

Step 4, rerun the Step 3 command.
Expected: exit 0, `"action": "installed"`, and `runtime` with `"status": "ready"`, `python_version` and `"pyyaml": "6.0.3"`.
A third identical run reports `"action": "unchanged"`.

If `runtime_prerequisite` also appears with a message about creating the venv, your Python lacks venv support: install your platform's venv package, or pass `--runtime-python` with an interpreter that already has PyYAML.

### 3.2 What the installer checks

- `--runtime-python` or `--venv` (not both) selects the runtime.
  The runtime must be Python 3.10 or newer and have the pinned PyYAML; both are probed by running the interpreter with `-E` (ignoring `PYTHONPATH`, `PYTHONHOME` and the other `PYTHON*` variables), so those cannot lend it a package.
  The launched service inherits your environment, so keep `PYTHONPATH` unset when you run it.
- Supported config keys are `store_path`, `plan_validator_argv`, `validator_timeout_seconds`, `runtime_root` and `runtime_python`.
  Unknown keys, a `null` value for `runtime_root` or `runtime_python`, a relative or symlinked `runtime_root`, an out-of-range timeout and an oversize validator list are refused with an error.
- Skills root, store, venv and runtime root may not sit inside a Glitch Brain's engine-managed files.
  The Brain's `workspaces/` and `_local/` folders are member-owned and are allowed.
- The store may not be inside the source, contain the source, or be inside or contain the installed skill.
  The venv may not be inside the store or the runtime root (the configured one, or the default when none is configured).
- `runtime.status` values: `ready`, `not_configured`, `would_create_venv`, `missing`.

## 4. Upgrade

Pull the newer source and rerun the same install command.

- An identical tree is `unchanged`; a different one is `upgraded`.
- The installed `config.json` is preserved.
  A recognised installation with a missing or invalid `config.json` or `store_path` is refused so that the durable store never changes by accident.
- The previous skill moves to `<skills directory>/.glitch-idea-backups/<UTC timestamp>-<id>/` and the result reports it as `backup`.
- To roll back, stop any running service, remove the installed `glitch-idea` folder and move the backup back under its original name.
- If an upgrade is interrupted between backing up and replacing, restore the backup before retrying.
- A folder at `<skills directory>/glitch-idea` that has no install manifest is not touched; the installer reports an unrelated skill and changes nothing.
- Never run two installers at once.

The idea store lives outside the skill and is never part of an upgrade.

## 5. Launch

```bash
idea_python=/absolute/path/to/venv/bin/python
idea_tool=/absolute/path/to/skills/glitch-idea/scripts/idea.py
"$idea_python" "$idea_tool" session-open
```

Observed on Linux: exit 0 and a JSON object with `ok`, `origin` (`http://127.0.0.1:<port>/`), `binding_id`, `session_id`, `selected_idea_id` and a one-time `pairing_code`.
The first call starts the service in the background; later calls reuse it.
Open the origin in a browser and enter the pairing code within 60 seconds.
The code is single-use, with a small attempt limit.

`browser-open --browser system` does the same and also opens a tab with the operating system's browser.
On a machine with no usable browser it returns `system_browser_unavailable` together with the new `binding_id`, `session_id` and `resume_required: true`, but no origin; run `session-open --resume binding_ID` to get the origin and a fresh pairing code.

Other flags on `browser-open` and `session-open`: `--runtime-root`, `--runtime-python`, `--readiness-timeout` (greater than 0 and at most 5 seconds), `--resume binding_ID`, `--idea-id idea_ID`.
A global `--store /absolute/path` goes before the verb.

Two different folders appear in a session.
The **store** (`--store` or `store_path` in the config) is where ideas are kept: `IDEAS.md` and one detail file per idea.
The Capture step's **workspace** is the project the idea is for, such as the folder it would be built in.
Give the workspace as an absolute path on the service host; over SSH that is the remote computer, not the one running the browser.
A path that is not absolute, does not exist or cannot be read there, or is not a folder is refused with `workspace_unavailable`; the page keeps your unsaved answers.

### 5.1 Runtime root rules

- Default: `.local/state/glitch-idea` under your account's home directory as the system records it; on macOS and Linux that is the password-database home, not `$HOME`, so if you have changed `HOME`, pass `--runtime-root` or set `runtime_root` explicitly.
- It must be absolute, outside the skill package, free of symlink components, owned by you and mode exactly 0700.
  A looser directory fails with `runtime_not_private`.
  Keep it free of ACLs and do not place it under a group-writable or setgid parent that would hand out access.
- A relative `--runtime-root` fails with `invalid_config`.
- The service listens on `127.0.0.1` on an ephemeral port and accepts only that exact `http://127.0.0.1:<port>` origin.

### 5.2 Browser on another computer (SSH)

The browser runs on the computer you sit at; the service runs where you launched it.
On **your own computer** open

```bash
ssh -L <port>:127.0.0.1:<port> <host>
```

using the **same** port that `origin` printed, then open `http://127.0.0.1:<port>/` locally.
A different local port, a hostname or a wildcard address is rejected, because the service accepts only its exact origin.
Use `session-open` for this case: `browser-open --browser system` would open a browser on the remote machine, and it is not an SSH route.
The port changes whenever the service restarts, so reopen the tunnel with the new port after a restart.

### 5.3 Orca unavailable or misrouted

`browser-open --browser orca` needs `--orca-worktree 'UUID::WORKTREE_PATH'` and `--orca-terminal HANDLE`, taken from the pane that is actually running the agent; `--orca-host` is optional.
It never uses Orca's "current" selection, because that can name a different host.
Without worktree and terminal the helper stops before starting anything with `origin_missing`; selectors with `--browser system` are a `usage` error.

Missing or malformed selectors (`origin_missing`) and selectors in system mode (`usage`) fail before any session exists: fix the arguments and rerun without `--resume`.
After the session is created, a launch failure returns the same safe details as above (`binding_id`, `session_id`, `resume_required: true`), but not the origin.
Error codes and what to do:

| Code | Meaning | Recovery |
| --- | --- | --- |
| `cli_missing`, `cli_unavailable` | The `orca` command is not on this machine's path or cannot run. | Install or fix Orca, or run `session-open --resume binding_ID` and open the origin it prints in a browser (SSH tunnel as in 5.2). |
| `cli_timeout` | A command exceeded 8 seconds. | Retry with `--resume`. |
| `origin_missing` | Selector values are absent, malformed or not a full worktree ID; no session was created. | Read the initiating pane's worktree ID and terminal handle again and pass them exactly; rerun without `--resume`. |
| `identity_mismatch` | The terminal does not uniquely match the worktree, handle or host you gave. | Correct the selector setting (the host must be that of the initiating pane), then rerun with `--resume binding_ID` from the error. |
| `runtime_unavailable` | Orca's runtime is not connected, or the terminal is disconnected or orphaned. | Reconnect Orca to the host, then `--resume binding_ID`. |
| `route_unavailable` | Orca could not route to the host. | Fix the host connection in Orca, then resume. |
| `orca_error`, `cli_failed`, `unsupported_payload` | Orca refused or returned something unreadable. | Check the Orca version and connection, then resume. |
| `unsafe_url` | The service root was not a plain loopback URL. | Report as a defect; do not paste a modified URL. |

For a pre-session failure (`origin_missing`, `usage`) fix the arguments and rerun without `--resume`.
For a post-session failure, either fix the connection or selector and run `browser-open --browser orca ... --resume binding_ID` with the `binding_id` from that error, or run `session-open --resume binding_ID` to print the origin and a fresh pairing code and open it in a system browser, through an SSH tunnel when the service is remote.
The helper does not switch browser routes by itself: that choice is yours.
Orca launch success means a tab was created; it does not prove the page is usable in that pane.

### 5.4 Setup: where ideas live

The browser's **Setup** pane holds one choice per store.
Option 1, **Glitch native**, keeps ideas as Markdown in the store (the default).
Option 2, **your own workflow**, connects to your own system through the contract in `docs/workflow-api.md`: enter its address and an API key minted for you by that system, then press **Test connection**.
The key is stored in an owner-private file under the runtime root and is never shown again; leaving the key field empty keeps it, and clearing it removes it.
Plain `http://` is refused except to this computer, so a key never crosses a network unencrypted.
In this release Option 2 is configured and tested only: ideas are still saved in Glitch until saving to your system is switched on.

## 6. Pause and resume

- The browser shows a disconnected banner and an exact resume instruction if the agent loses contact for longer than its lease.
- To resume: `session-open --resume binding_ID` (or `browser-open ... --resume binding_ID`).
  This re-pairs the same binding, keeps its selected idea and save receipts, issues a new pairing code and starts a new agent generation.
  Old agent credentials and outstanding replies do not carry over; a stale reply needs a fresh browser request.
- A browser reload keeps the cookie; a service restart does not, so pair again.
- The service stops on its own after 15 minutes with no authenticated activity.
  Saved Markdown and binding metadata survive.
- Drafts save when you leave a field, or after 3 seconds without typing.
  They do not save on each keystroke.
- While you edit an unsaved field, the page sends a tiny "still here" ping at most every 30 seconds.
  It carries no text and saves nothing; it only keeps a connected agent from pausing.
  The agent pauses after 600 seconds with no saves, pairing or pings.
- At most eight bindings are kept per store.
  A full store refuses a new one rather than evicting another binding, so resume an existing binding.
- `session-close --session session_ID` revokes only the agent's access.
  The paired browser and saved drafts stay.
- To stop the service now, interrupt a foreground `serve` (`serve --runtime-root /absolute/path/to/runtime`), which drains active saves before releasing ownership.
  A service started by `session-open` has no stop verb; it ends at its idle timeout.
- Pause the browser flow before editing any idea file by hand, then resume.

## 7. Direct edits and frontmatter policy

Each idea is `idea_<id>.md` with YAML frontmatter between `---` lines, plus a body.

- You may edit the supported frontmatter inputs (shape, the two human ratings, assessment inputs and reasoning, and source fields of steps already accepted in the browser) and the text between the two Notes markers:
  `<!-- glitch-idea:notes:start -->` and `<!-- glitch-idea:notes:end -->`.
- Do not edit identity, origin, hashes, history, plan links, counters, generated tables or `IDEAS.md` order.
- YAML comments in frontmatter block rewriting (`yaml_comments`); move them into Notes.
- Files must be UTF-8 with no byte-order mark.
  Frontmatter is rewritten in canonical form on the next write.
- A file edit is not browser acceptance: edited workflow inputs return for review in the browser.
- A changed generated body gives `generated_body_conflict` and needs an explicit repair decision.
- Ordinary editors do not take the application lock, so an edit made while a save is in flight is detected by hash and refused, not merged.

The full rules are in the [Markdown contract](markdown-format.md).

## 8. Recovery

```bash
"$idea_python" "$idea_tool" doctor
"$idea_python" "$idea_tool" repair-views
```

- `doctor` checks schema, ordering, registered receipts, frozen plan bytes and derived views, and exits nonzero when unhealthy.
- `repair-views` recreates a **missing derived archive view** from validated authority and refuses to overwrite a differing file.
- It cannot rebuild missing authoritative history, immutable metadata or frozen `plan-evidence/` bytes.
  Restore those exact bytes from your own backup.
  The original working plan is not a reconstruction source.
- When an error says `committed: true`, the write already happened: read the record and run `doctor` instead of replaying it.
- Preserve a corrupt store for diagnosis.
  Never invent replacement history, plan bytes or migration evidence to turn a check green.
- A failure with `runtime_recovery_conflict` preserves the ambiguous private runtime bytes for explicit recovery; stop all helpers and inspect the runtime root before deleting anything.
- Fixed error codes with no peer detail are printed by the launcher by design; the code itself is the diagnosis.

## 9. Migration limits

- A version 1 store (`state.json`) is only read until the first successful write.
  Then the full Markdown state is written, together with a strict receipt and the exact original bytes under `migration-recovery/`, and only `state.json` is removed.
- Migration is refused if an accepted-plan or archive view is missing or altered (`migration_view_required`), or if unexpected files make the store ambiguous (`ambiguous_store`).
  For `migration_view_required`, `repair-views` recreates a missing archive view only; an altered archive view, and missing or altered `plan-evidence/` bytes, must be restored exactly from your backup.
  For `ambiguous_store` with "Orphan legacy authority/evidence", the store holds a file the old format does not account for: move it out yourself; no command removes it.
  For `ambiguous_store` with "Migration marker already exists", the store already records a migration: do not move files; keep the store as it is and run `doctor` for a diagnosis.
- After migration, the old binary refuses to use the store.
  A store with both formats live is ambiguous and is reported, never merged.
- The frozen JSON is evidence, not a rollback.
  It does not restore newer Markdown-era ideas, and it is not safe to copy it back over `state.json`.
- Consistency checks cannot detect someone deliberately rewriting a record together with all the evidence that refers to it.
- Back up the store before the first write after upgrading an old store.

## 10. Storage grade

What the code does and what has been observed:

| Platform | Publication and durability | Observed |
| --- | --- | --- |
| Linux | Each write is a recoverable multi-file transaction behind an exclusive lock; the helper reports the grade `file-and-directory-synced` (the file and its directory are synced). | Observed on Linux local filesystems. |
| macOS | Same POSIX code path. | NOT OBSERVED. |
| Windows | The code reports the weaker `file-synced-process-recovery` grade (file sync only, no directory barrier), and browser launch is refused. | NOT OBSERVED. |

Limits that hold everywhere:

- A successful sync is not a power-loss guarantee.
- Only processes cooperating on the same local machine and filesystem are supported.
  Network shares and cloud-synced folders are not qualified; do not put the store or the runtime root there.
- If publication succeeded but a later sync failed, the error says the write may have occurred (`durability_uncertain`); reconcile before retrying.
- The tool has no backup or sync of its own.

## 11. Getting the `/glitch-plan` prompt

1. In the browser, complete the earlier steps that Review lists as required for the idea.
2. Open Review.
   When the prerequisites hold, choose **Generate planning prompt**.
   This saves an immutable planning packet and changes no accepted decision, draft or backlog order.
3. Choose **Copy planning prompt**.
   The text begins with `/glitch-plan`, tells you to paste it into a NEW window or pane, and contains the exact `## Idea trace` (`idea_id`, `idea_revision`) and the planning source.
4. If the clipboard is blocked, the whole prompt stays selectable on the page and **I copied the full prompt** confirms a manual copy.
5. **Ideas** lists saved ideas; **Copy again** works only while the packet is still current.
   If the saved inputs changed since, generate a new prompt; an old prompt is shown as historical and cannot be copied as current.
6. Paste into a new window.
   The plan must keep the idea trace outside its Build choices section.
   Then run `register-plan` with the saved plan, as in the [command reference](../glitch-idea/references/commands.md).
   Only that archives the revision.

Copying never plans, archives or registers anything by itself.
