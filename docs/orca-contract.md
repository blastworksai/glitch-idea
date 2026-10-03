# CP0 native browser/current-agent qualification recipe

This is a reproducible recipe and unqualified evidence template. It uses only a
disposable source-test probe and fake ideas. It does not install a skill, modify
a personal store, invoke a new paid model, or implement production storage.
Protocol/unit tests are separate from real browser evidence.

## Evidence status — 1 October 2026

Measured by the an implementer orchestrator, using Python 3.14.4, Linux Chrome
154.0.8037.57 and Orca 1.4.218 with its Windows Chrome 150 client over SSH.
The initiating harness was this live Codex session invoking the probe directly;
installed `$glitch-idea` and Claude Code `/glitch-idea` are not qualified yet.

| Route/check | Observed result |
| --- | --- |
| Linux HTTP fixture/security regression | 11 targeted tests passed; real loopback HTTP |
| Linux real browser/current-agent round trip | PASS for probe: two requests in one bound session; real CDP mouse events; current agent authored both replies; browser acceptance advanced revisions 3 then 4; reload retained them |
| Orca Windows desktop to SSH Linux | PARTIAL: dedicated tab, exact host routing, pairing, current-agent response, DOM-triggered acceptance and reload passed; CLI click/keypress did not cause page events, so interactive acceptance remains unqualified |
| Orca macOS desktop to SSH Linux | NOT OBSERVED |
| Local Windows/macOS runtime or storage ACL/durability | NOT OBSERVED; probe fails closed outside POSIX |
| Bounded idle | 120.01 seconds, five waits at 25/50/75/100/120 seconds, final status paused; single process made zero model calls internally; overall session token counter unavailable |
| Heartbeat loss | 37 seconds with no agent calls -> real browser displayed disconnected, preserved revision 4 and exact explicit resume command |
| Actual harness turn-end/interrupt/compaction | NOT OBSERVED; withholding heartbeat proves detection mechanism, not those lifecycle actions |
| Service restart | Owned service interrupted/restarted; same fake idea and revision retained; fresh pairing succeeded; no old session authorization reused |
| Native NVDA/VoiceOver, keyboard and zoom | NOT OBSERVED |

Local runtime evidence records are deliberately ignored by git and contain only
fake data plus separate owner-only transient credentials. Published evidence
contains no credentials, private host identifiers or personal ideas. The local
browser mechanism has passed; remaining harness/platform rows are not release
approval and must be completed in later qualification. Missing Orca interaction
proof does not qualify through the Linux result.

Observed browser origin and host matched the loopback service exactly, with
`isSecureContext=true`: Linux `http://127.0.0.1:36843`, Orca
`http://127.0.0.1:45955` (ephemeral test ports, never fixed configuration).
`orca worktree current` selected a different registered SSH connection despite
an originating pane binding. The orchestrator verified `ORCA_WORKTREE_ID` and
`ORCA_TERMINAL_HANDLE` through terminal listing and used those explicit selectors.
The incorrectly placed blank test tab was closed; unrelated tabs were untouched.

Orca limitation reproduced with both `e6` and `@e6`, after explicitly switching
the dedicated tab: CLI reports clicked, while a capture-phase page click listener
records no click and no corresponding POST occurs. DOM `requestSubmit()` and
`HTMLElement.click()` execute the page handlers and the full transport works.
These DOM-assisted results must never be relabeled real pointer/keyboard proof.
A blank-page snapshot also returned runtime_unavailable once; navigating to the
actual loopback page restored snapshot capability without a runtime restart.

One diagnostic accidentally printed the disposable probe's CSRF token. The
orchestrator immediately invalidated that browser session by a second redemption
(401 pairing_replay_session_invalidated), then explicitly resumed and rotated
agent credentials. No production credential was involved. Network diagnostics
must filter headers and bodies before printing; never dump raw network output.

Network used for qualification: loopback, existing Orca SSH route, and local
FORGE progress recording. No package/repository download or additional model API.
The first ordinary Chrome launch attempted its own background Google traffic;
the repeat disabled background networking and component updates.

## Start an isolated probe

Python 3.10+ standard library only. Run from a disposable project checkout;
`<private-runtime>` is a new user-owned fixture directory, not a personal idea
store. The probe creates it with 0700 access, including under a setgid parent.
Existing permissive directories fail closed. Files are 0600. POSIX ownership and
access bits are checked; Windows owner ACLs are not claimed.

```sh
python3 tests/native_probe.py serve --runtime <private-runtime>
```

Keep this owned process alive in its supported terminal/background facility.
Output contains only the non-secret loopback URL and initial session ID. The
service binds `127.0.0.1` on an ephemeral port. No secret enters launch argv or
logs. It does not automatically open any browser. Do not run two processes with
the same runtime; multiple-process ownership is outside this disposable probe.

Rebind to this actual initiating agent, then retrieve pairing code only in the
initiating pane (the following command is the sole deliberate secret output):

```sh
python3 tests/native_probe.py session-open --runtime <private-runtime>
python3 tests/native_probe.py pairing-code --runtime <private-runtime>
```

Use the new `session_id`, not the start line's previous ID. Pairing code expires
within 60 seconds, permits at most five attempts and is single-use. Enter it in
the browser's labelled Pairing code input. A second redemption of the same code
invalidates the browser session, displays a re-pair warning and disconnects the
agent. Recover with explicit `resume`, retrieve the newly generated code and
pair again. Cookie survives browser reload, not service restart. No localStorage.

## Orca route

Read runtime-matched instructions first:

```sh
orca skills get orca-cli
orca skills get orca-cli --reference references/browser.md
```

Verify the initiating `ORCA_WORKTREE_ID` and `ORCA_TERMINAL_HANDLE` against
terminal listing and execution host. Use explicit selectors for every browser
operation. Never assume `orca worktree current` names the initiating pane: the
orchestrator directly observed a different host from that implicit selector.

Supported command recipe supplied by the orchestrator from actual invocations:

```sh
orca terminal list --worktree id:<originating-worktree-id> --json
orca tab create --url about:blank --worktree id:<originating-worktree-id> --json
orca goto --url http://127.0.0.1:<probe-port> --page <returned-browser-page-id> --worktree id:<originating-worktree-id> --json
orca snapshot --page <returned-browser-page-id> --worktree id:<originating-worktree-id> --json
```

Create a dedicated tab; do not overwrite an unrelated tab. No ephemeral IDs
belong in checked-in instructions. The browser must route the service URL through
the same SSH host as the initiating agent/service. No proxy, open firewall,
0.0.0.0 or wildcard Origin workaround. Exact permitted host/origin are the start
URL; qualification must record observed Host, Origin and `isSecureContext`.
The probe sends a browser-derived transport receipt after pairing, exposed by
safe `status` below; this records transport values, not a complete browser test.
If unavailable, preserve the draft and report the concrete documented route or
setting failure. Do not silently substitute a system browser for Orca evidence.

## Current-agent round trip, repeated in the same session

In the browser: enter fake idea text, click Save fixture draft, click Request
current-agent proposal. The browser owns these choices. In the initiating agent:

```sh
python3 tests/native_probe.py events --runtime <private-runtime> --session <session-id> --after 0 --timeout 25
```

Each wait is 0–25 seconds. The event includes `sequence`, correlation fields and
bounded `data.raw_text`. The same current agent reasons about the fake text and
writes a JSON reply file. Copy correlation fields exactly; do not copy
`sequence` or `data`. No reply text in shell argv:

```json
{
  "request_id": "copy-the-event-request-id",
  "session_id": "copy-the-event-session-id",
  "idea_id": "copy-the-event-idea-id",
  "accepted_revision": 1,
  "draft_version": 1,
  "operation": "shape",
  "source_digest": "copy-the-event-sha256",
  "text": "FAKE: the current agent's bounded proposal"
}
```

```sh
python3 tests/native_probe.py respond --runtime <private-runtime> --request <request-id> --payload <reply-file.json>
python3 tests/native_probe.py status --runtime <private-runtime>
```

Agent token is read internally from the private credential file, never printed
or passed in argv. Browser must show the actual text before the human clicks
Accept visible proposal. Record revision before response, after response and
after acceptance: response alone must not accept; acceptance advances once.
Reload must retain saved fixture state through its session cookie. Request a
second proposal in the same session and use `--after <last-sequence>`; prove the
same initiating agent responds again. Fixtures or unit tests cannot stand in
for this active-agent evidence. The probe's accepted text is a minimal string,
not a production seven-step shape schema.

## Bounded idle, interruption and restart

The probe heartbeat expires after 35 seconds without an agent events/respond
call. A connected events wait refreshes it at entry and successful completion.
Browser refresh does not refresh it. Events waits refresh heartbeat but do
not reset activity; idle agent activity caps at 120 seconds even with polling.
At most five 25-second wait calls can reach that cap; stop when status becomes
paused or disconnected. Do not keep issuing events in an unbounded model loop.
Measure actual action turns and available token usage over the idle interval,
name the actual harness (Codex `$glitch-idea`, Claude Code `/glitch-idea` once
installed/supported), and report unavailable token counters honestly. This probe
is invoked directly; it does not prove either installed skill spelling.

```sh
python3 tests/native_probe.py session-close --runtime <private-runtime> --session <session-id>
python3 tests/native_probe.py resume --runtime <private-runtime>
```

Explicit close visibly disconnects and cancels pending requests. Resume creates
a fresh agent session ID and rotates the private agent token; the already paired
browser can continue unless pairing was invalidated. Real harness interruption,
turn end and compaction are detected by heartbeat expiry, not a fabricated
native notification. Test those actual actions separately. Old response fields
must be rejected after resume, edited source or changed revision.

Stop only the owned serve process using its terminal interrupt. Restart the same
command and runtime. Saved fixture draft/accepted text and versions remain;
cookies, proposals, pending requests and agent credentials expire. Explicitly
session-open/resume and re-pair. This is a fixture JSON file, not the future
Markdown authority or multi-file durability implementation. Interrupted body
writes, OS lock contention and power-loss durability are not qualified here.

## Targeted verification and limits

```sh
python3 -m unittest discover -s tests -p test_native_probe.py -v
```

Tests cover real loopback HTTP: repeated requests/reload cookie, separate agent
and browser auth, Origin/Host/CSRF, all correlation fields, stale edited/resumed
responses, conflicting duplicates, pairing replay/expiry/five attempts,
heartbeat/idle/explicit close, persisted fixture restart and private/setgid
runtime access. Timer tests move fixture clocks; they are not measured native
idle or harness interruption proof. Browser remains minimally accessible with
labels/status and text-only rendering; no full screen-reader claim.

Disposable limits: one POSIX-owned process/runtime/session/browser, eight
concurrent HTTP handlers, 1 MiB JSON, depth 12, field length 64 KiB, 128 event and
mutation receipts, 30-second connection/body timeout and 25-second agent wait.
Receipt replay is in-process only; durable product capture receipts are a later
checkpoint. Agent replies are deliberately rejected after completion instead
of implementing product duplicate-reply reconciliation. No uploads, workspace
resolver, real memory, production schema, migration, native ACLs or automatic
service lifecycle. Restart preserves only fixture draft/accepted state. Never
package or advertise this fixture as a production service.

## Evidence record to fill after attended qualification

Record test commit/hash; Python/Orca/browser versions; client OS and execution
host OS; explicit originating selector match (redacted identifiers); harness;
non-secret route host/origin/secure-context; two event IDs/sequences and matching
accepted revisions; reload result; wrong-auth/replay/stale response outcome;
idle turns/tokens/elapsed; actual interruption/compaction result; restart result;
which checks remain NOT OBSERVED. Screenshots/snapshots must omit credentials.
Network for this recipe: local loopback and the already connected native SSH
browser route only; no package download or external model API.

## Launch adapter verification

`idea_native.open_browser` was executed against the connected Orca runtime after
13 focused adapter tests passed. It verified this initiating terminal/worktree,
created a new dedicated browser page, and returned its page ID. This proves
launch/binding only; it does not remove the interaction limitation above.
System mode propagates the actual `webbrowser.open_new_tab` boolean through a
bounded child process; Python's plain `-m webbrowser` was inspected and ignores
that return. Real isolated child tests prove both false and true outcomes.

Run adapter tests from the checkout:

```sh
python3 -m unittest discover -s tests -p test_native.py -v
```

## Actual Codex interval finding — 1 October 2026

an implementer measured two separate 25-second event waits from the initiating Codex
CLI 0.159.3. The third was refused because the 35-second heartbeat had expired.
The browser actually displayed disconnected plus the explicit resume command.
This supersedes any inference that the earlier single-process five-wait test
qualified sustained model-driven polling. The entry-timed lease leaves ten
seconds after a full wait for model/tool overhead. J0c tests renewal at successful
connected wait completion without moving the 120-second idle deadline or
reviving a closed/rebound session. Actual lifecycle/Claude evidence is still open.

Own rollout counters are now observable. The measured root interval consumed
288071 input tokens (275968 cached) and1126 output tokens; it includes
orchestration overhead and is neither billing evidence nor an isolated idle rate.
The probe is invoked directly, not through an installed skill. Detailed local
evidence is ignored `evidence/cp2/codex-idle-observed.json`.

J0c correction is verified by 13 focused tests, including an old wait cancelled
by actual condition-unlock resume/close and a fixed 120-second idle deadline.
Actual-model repeat remains separate evidence. The corrected direct probe was repeated in the initiating Codex CLI0.159.3:
four separate model-issued wait calls returned connected, connected, connected,
then paused at the fixed inactivity deadline; actual Chrome showed Agent: paused
and the explicit resume command. Evidence is ignored
`evidence/cp2/codex-idle-repeat-proof.json` and `codex-idle-paused.png`.
The recorded root interval has597293 input tokens (594176 cached),1065 output;
counts include setup-tail and status commentary, not billing. The large cached
context makes this an observed session cost, not a general per-minute promise.
Actual harness turn-end, interrupt, compaction and Claude Code remain open.

## Actual Codex interruption — 1 October 2026

an implementer correlated the bound disposable probe with an actual initiating Codex
`turn_aborted` at20:53:02.612Z. The connected browser displayed disconnected
at20:53:35.464Z (32.852s later), preserving exact draft, revision1 and the
explicit Resume instruction. Controlled Escape through documented Orca terminal
text input interrupted the actual turn; terminal input acceptance alone was
not treated as evidence. Raw observer output remains unqualified by itself;
independent event correlation is in ignored evidence/cp2/codex-interrupt-correlated.json.

Earlier unattended timeout and Ctrl-C attempts did not prove interruption.
Foreground the owned page and observe connected before arming: a background tab
can leave stale status text because of timer throttling. Setup resume canceled
the old pending request, so this result does not prove interruption cancellation.
Compaction, Claude Code and installed-product lifecycle remain unqualified.

## Actual Codex compaction — 1 October 2026

an implementer correlated actual own-rollout `compacted` at 20:59:13.751Z
with a connected baseline at 20:56:50.511Z, browser disconnection at
20:57:24.960Z, and a post-compaction browser read at 21:00:32.878Z.
Exact draft and revision 1 remained saved, with explicit Resume visible.
Evidence: ignored `evidence/cp2/codex-compact-correlated.json`.

Turn end at 20:56:55.163Z preceded the compact command. Lease expiry
occurred during compaction, before its completion record; this proves safe
disconnected state across actual compaction, but cannot isolate compaction
as the cause of disconnection. No heartbeat was renewed before evidence
readback. Pending cancellation and installed-product behavior remain
unqualified. Claude Code lifecycle and loop cost still gate CP2 construction.

## Actual Claude Code roundtrip and idle — 1 October 2026

an implementer exercised the source probe in an actual interactive Claude Code
2.1.286 session, Opus5.5 medium, on Linux Chrome154. Two same-session
browser request/current-agent response/CDP Input acceptance cycles advanced
accepted revisions1→2→3. Five separate model-issued event waits reached
paused after120.217s from the measured last-activity boundary. No detached
heartbeat loop or paid API-key agent was used.

an implementer verified36 exported input/evidence hashes, the response sequence,
and independently read the live browser at21:08:59.571Z: exact saved draft
and cycle2 text at revision3, paused and explicit Resume. Root also recomputed
usage from scrubbed per-response metadata: idle5 model responses,10 input,
694228 cached input,2583 cache-creation input,1526 output tokens. These are
observed session counts with loaded-context overhead, not billing or a general
idle-cost promise. Root evidence is ignored
`evidence/cp2/claude-roundtrip-idle-verified.json`.

Actual Claude turn-end/interrupt/compaction correlation is still pending.
This is source-probe qualification, not installed skill or product approval.
Chrome attempted Google GCM registration despite background-network flags;
those flags did not establish zero external browser traffic. No dependency
installation or repository download was performed.

Claude lifecycle follow-up, independently correlated by an implementer: actual
`end_turn`21:11:27.804Z preceded browser disconnection21:11:48.233Z by20.429s.
Actual interruption marker21:18:42.872Z preceded disconnection21:19:13.826Z
by30.954s. Both started connected, retained exact saved fixture/revision3,
and displayed Resume. Root also observed the actual Interrupted terminal UI.
The first interruption attempt missed its window and remains unqualified.
Root evidence: ignored `evidence/cp2/claude-lifecycle-root-verified.json`.

Claude compaction is NOT OBSERVED. Claude Code auto-mode rejected the arming
command with the stated reason “Sensitive-Source Provenance”. an implementer
stopped its waiting compaction controller before any input, without granting
approval or changing permission rules. A subsequent evidence/cleanup prompt
was refused by Orca as `agent_prompt_blocked`; its delivery was not confirmed.
The owned Claude fixture/browser therefore require owner-session cleanup.
CP2 construction remains gated; no product implementation has started.

The operator subsequently approved the isolated compaction test and cleanup.
an implementer acknowledged the relayed approval, but Claude Code's auto-mode
classifier rejected the setup again, now with `[Auto-Mode Bypass]`, before
execution. The second waiting controller was stopped before input. Approval
is recorded; direct confirmation in that Claude terminal is still required
by its reported permission boundary. No additional build approval is sought,
no permission rules changed, and Claude compaction remains NOT OBSERVED.

## CP2 source-probe entry gate satisfied — 1 October 2026

This supersedes the earlier unobserved/refused Claude compaction rows. After
direct operator confirmation in Claude's terminal, the bounded setup ran.
Actual own-session `compact_boundary`21:42:16.658Z records manual compaction.
The browser disconnected21:41:39.127Z during compaction; a post-compaction
snapshot21:42:41.506Z and independent an implementer browser read21:43:01.451Z
retained exact raw/accepted text, accepted revision3, draft1 and Resume.
Root correlation: ignored `evidence/cp2/claude-compact-root-verified.json`.

Turn end21:41:08.256Z preceded the compact command21:41:10.834Z. This proves
safe disconnected state across actual compaction, not compaction as an
independent cause of disconnection. No heartbeat was renewed before readback.
Earlier failed/refused/missed attempts remain unqualified.

Actual Codex and Claude source-probe roundtrips, bounded model-driven idle
cost and turn-end/interruption/compaction evidence now satisfy the CP2 entry
gate. This permits J6 construction only. Installed skill/product behavior,
pending-request integration, native Windows/macOS/Orca interaction and release
qualification remain open. Authored and independently checked by an implementer.


Cleanup readback, an implementer: the Claude owner stopped its disposable probe
and Chrome in separate commands after evidence verification. Independent
loopback checks confirmed ports 34119 and 39411 closed. Evidence is preserved;
no private task directory was deleted and no permission rules changed.
