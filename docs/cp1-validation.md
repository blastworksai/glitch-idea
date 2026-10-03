# CP1 validation — Linux source build

Author: an implementer. Observed 1 October 2026. This checkpoint delivers Capture,
Priorities, browser drafts and authoritative Markdown persistence. Shape through
Review, the active-agent loop and the release installer remain later checkpoints.
The source build is not an all-platform release.

## Actual browser and persistence

Installed Linux Chrome was driven through real CDP pointer/text input, against
the actual source service and disposable stores. These runs did not mock fetch,
inject app methods or use the earlier static UI fixture.

- Capture preserved leading/trailing whitespace, Unicode and multiline text.
  Saving one priority, Pause and hard reload retained the saved draft.
- Owner-authenticated service stop and restart retained the stable tab binding,
  durable receipt session and selected idea. Fresh pairing followed by explicit
  acceptance saved both priorities. CLI show and index/detail Markdown agreed;
  immutable origin bytes and their hash remained unchanged.
- A Capture response was dropped after the server committed. Its result lookup
  was blocked, then credentials rotated. The browser retained the pending action.
  Pairing the unrelated tab did not adopt its session; the original tab recovered
  its original receipt without another Capture.
- The same loss/recovery succeeded with two services on distinct loopback ports.
  A foreign code was rejected without consuming the valid code in its own service.
  Independent disk and CLI reads found one receipt for each capturing session,
  zero for each unrelated session, and no duplicate ideas.
- One supported direct Markdown change imported exactly once as an observed
  revision. Two real CLI writers used the same expected revision; exactly one
  won and one received stale_revision. The untouched browser's subsequent draft
  save received HTTP 409, retained its answers and displayed Unsaved. Clicking
  Reload current state and keep my answers, then Save ratings, accepted against
  the new revision. Independent CLI/detail/index reads agreed at revision 5;
  original wording and immutable history revisions 1 through 4 were unchanged.
  A CDP input timeout interrupted the first helper after the CLI race. Recovery
  activated the same tab at a larger viewport, preserved the fixture and observed
  the actual HTTP 409; it did not repeat the external edit or CLI writes.

One earlier same-store verification helper mistook a retained hidden pairing form
for a visible form. It was corrected, and the original pending browser action was
recovered without resetting the store or creating another session. The original
failure and subsequent recovery evidence are both retained.

## Automated checks

The full CP1 Python battery ran 350 tests: 348 passed, one failed and one optional
external-validator test skipped. The failure was the installation regression
fixture packaging only the three former Python modules and asserting JSON
storage. The fixture now packages current public files and checks Markdown
capture/show/doctor. All 11 installation tests passed on an independent focused
rerun. The original full-battery failure log is retained; the entire unchanged
battery was not rerun after this test-only correction.

All 40 Node browser logic tests passed. A separate configured-validator fixture
ran the skipped integration against Glitch's real `plan_check.py` successfully.
The engine `update.py check` reported an unchanged delivered engine.

Focused earlier checks include actual process kills during transaction, receipt,
migration and runtime publication; two-process CAS; real HTTP authorization and
bounds; child-process startup races, restart and admitted-write shutdown drain.
Process-kill evidence does not qualify hardware power-loss behavior.

Reproduce the full block from the task checkout with:

```sh
python3 -m unittest discover -s tests -v
node --test tests/web/*.test.mjs
```

Local ignored evidence is under `evidence/cp1/`: basic/resume browser proofs,
isolation same/recovered/cross proofs, `isolation-disk-readback.json`, cropped
screenshots, `live-browser-conflict-recovered.json`, `conflict-disk-readback.json`,
full Python/Node logs, and `configured-validator.log`. Browser helpers
have adjacent runbooks describing inputs, exact fixture mutations and limitations.
Credentials are excluded from the published report and proof JSON.

## Open qualification

Actual Codex idle polling has been measured separately. Actual harness turn end,
interruption, compaction and the Claude Code loop remain the CP2 entry gate.
Scripted lease expiry alone does not satisfy these lifecycle checks.

Native Windows/macOS storage, Orca attended interaction and screen-reader
qualification remain unobserved. Windows runtime startup currently fails closed
pending owner-ACL qualification. Linux Chrome results do not qualify those rows.
No push, merge, release or complete-feature claim follows from this checkpoint.

Network: loopback browser/service traffic and existing Orca metadata access.
No package downloads or additional model API calls were used for these checks.
The public browser uses original unbranded styling and system fonts; supplied
Company design assets are excluded under the owner's ruling.
