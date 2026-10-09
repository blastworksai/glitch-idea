# Browser API v1 — implementation contract

Maintained by an implementer. CP1 source behavior is qualified in docs/cp1-validation.md.
CP2 agent integration is in progress; later checkpoint routes remain declared
contracts until their implementations are qualified. The JSON below uses
fictional content and opaque example identifiers. Disposable probes are not
the service API. Native Windows/macOS/Orca qualification remains pending.

## Shared rules

All application routes are under `/api/v1/`. Reads require a browser session
cookie and the per-tab secret in `X-Idea-Tab` (see Pairing). Writes additionally
require exact Origin, Host and the per-session `X-CSRF-Token`. Session cookie is HttpOnly, SameSite=Strict, session-scoped;
Secure is required on HTTPS. No localStorage secret, bearer URL, wildcard CORS,
browser shell execution, generic file read, editable actor or validator config.
Agent authentication is separate and never delivered to the browser.

JSON objects reject unsupported fields, duplicate keys, nonfinite numbers,
wrong types, excessive depth and bodies over 1 MiB. ID/revision checks are actual
validation, not Python assertions. Revisions are nonnegative integers, never
booleans. Idea IDs remain `idea_<uuid32>`, not display row numbers. `revision`
means accepted idea revision; `draft_version` changes only for drafts;
`backlog_revision` changes only for accepted order mutations. A source digest is
SHA-256 over canonical validated source input, distinct from the immutable exact
origin-text digest. Canonical source encoding is UTF-8 JSON, sorted keys, compact
separators, no nonfinite numbers; the operation's source fields and their source
revision are part of the digest. Preserve the existing exact origin hash.

Every success/error carries `ok`, stable `code`, and relevant current counters.
Writes include `request_id` and `write_state`: `applied`, `no_op`, `not_applied`
or `committed_uncertain`. An ambiguous commit is reconciled by request result
and state reads before retry. Same key + same validated payload returns the
recorded result; same key + different payload is 409. No silent eviction of an
unacknowledged receipt. Receipt capacity exhaustion is 503 and asks for a fresh
session. Each durable session holds at most 128 receipts. An upload uses two
receipts (metadata and completed bytes); Capture, draft, acceptance and navigation
writes also consume this budget. For example, Capture plus 63 completed uploads
uses 127 receipts before any other writes. Receipts are never evicted, and the
browser does not silently create a new binding to escape the limit.
Durable capture receipts survive restart; proposed agent requests do
not survive a new agent binding.

## Pairing, transport and CSRF recovery

The launcher opens the loopback origin with the one-time pairing code in the URL
fragment: `<origin>/#pair=<code>`, where the code is exactly 32 lowercase hex
characters. The code is single-use and lasts 60 seconds, and a fragment never
reaches the server or a referrer. On load the page reads the fragment, strips it
from the address bar with `history.replaceState` before anything else, and
redeems the code through `POST /pair` itself; the operator types nothing.
Reusable secrets (the agent token, the tab secret, the cookie) never ride a URL.
The brief exposure of the launch URL in the browser command's argv is accepted
because the code is one-time and short-lived; domain Markdown still contains no
secret. The returned `browser.url` stays the root-only origin, and the result
carries `pairing_code` and a `fallback_line` for typing the code by hand. The
initiating agent shows that line only when the operator reports the tab did not
open paired. `session-open`, which launches no browser, is unchanged: the
operator opens the origin and types the code.

`POST /pair` is the only unauthenticated API mutation; it requires the exact
service Origin/Host and `Content-Type: application/json`, and accepts exactly
`{code: string}`. The code expires within 60 seconds, allows at most five
attempts, and can be redeemed once. Success sets the session-scoped HttpOnly
SameSite=Strict cookie and returns
`{ok:true,code:"ok",binding_id,session_id,csrf_token,tab_secret}`.
`tab_secret` is a fresh random secret of the same strength as the cookie and
CSRF secrets. It appears only in this response; the page keeps it in
`sessionStorage` (origin-and-port scoped, per tab; never a cookie, never
persistent storage, never logged or put in a URL) and sends it as the
`X-Idea-Tab` header on every `/api/v1/*` call, including `GET /session`. Cookies
are not port-scoped, so another listener on the same loopback address could
receive the cookie; the secret is what it cannot get. A browser request whose
`X-Idea-Tab` is missing, wrong or duplicated is refused (401
`browser_unauthorized`, or 400 for duplicates) whatever the cookie says; the
comparison is constant-time and the cookie stays a second factor. A reload in the
same tab keeps the secret; a new tab must pair again. Invalidating or re-pairing
a session drops and rotates the secret.
A replay of the redeemed code invalidates that browser session and outstanding
requests; the UI displays the specific re-pair warning. Failures are 401
`wrong_pairing_code`, `pairing_expired_or_locked`, or
`pairing_replay_session_invalidated`. Typing a code the tab already redeemed is
such a replay, which is why the fallback line is shown only on request.
Re-pairing requires explicit initiating agent resume (`session-open --resume
<binding_id>` issues a fresh code); no automatic retries of a rejected
bootstrap. A tab opened from an expired link shows: "This link's pairing code
has expired. Ask your terminal for a new code (it runs session-open --resume)."

`GET /session` with the valid cookie returns `csrf_token` along with session
identity/status and capabilities. After reload, the browser calls this before
any mutation and keeps the token only in memory. No persistent-storage secret
or token in URLs. `GET /state` carries domain state only; it need not duplicate this token.
A restart invalidates cookies and requests pairing again (401). Explicit trusted
resume rebinds fresh credentials to the existing durable receipt session, rather
than creating a new receipt namespace for an outstanding retry. A missing
initialized receipt file fails closed. The receipt-session ID is a reference,
never authorization; agent binding changes still cancel old proposals. Auth failures
return no idea state and do not disclose a CSRF token.

Authenticated `POST /transport` accepts exactly `{host,origin,secure_context}`
plus normal cookie/CSRF/Origin checks. It validates `host` and `origin` against
the server's pinned route and `secure_context` as a boolean. Success returns
`{ok:true,code:"ok"}`; mismatch returns 403 `transport_mismatch`. This is a
browser-observed qualification receipt, never authority to change an allowlist.
The product may display it in diagnostics; it cannot prove a human click.

Authenticated `POST /activity` is the typing ping.
It accepts exactly the empty object `{}` plus the normal cookie, CSRF and Origin checks; any other body is refused with 400 `invalid_input`.
It saves nothing, creates no receipt and returns `{ok:true,code:"ok"}`.
It resets the idle clock of the binding's connected agent, if there is one, and like any authenticated request it counts as browser activity for the service's own idle stop.
The page sends it at most once every 30 seconds, and only while an unsaved field is being edited; that cap is the page's, and the service trusts the paired browser here as it does for saves.

Example authenticated session response (placeholder token, never a real one):

```json
{"ok":true,"code":"ok","session_id":"session_fixture","csrf_token":"example-only-not-a-credential","agent_status":"disconnected","capabilities":{"agent":false,"memory":false,"uploads":true,"handoff":false}}
```

## Stable browser binding across tabs

A browser binding is a nonsecret `binding_<uuid32>` reference to one immutable
durable receipt `session_id`. After pairing the browser stores `binding` in its
URL query and sends `X-Idea-Binding` on every authenticated API request. The
header is only a selector: the matching HttpOnly cookie and normal CSRF/Origin
checks remain mandatory. Cookie names include the trusted service/store
namespace and binding ID, so tabs and loopback ports cannot overwrite each
other’s session cookies. Duplicate or malformed binding headers are refused.

Pair and session responses include both `binding_id` and durable `session_id`.
A new root tab without a selector must pair; it never adopts an ambient cookie.
An already pinned tab sends its selector on pairing too. A code for a different
binding returns `session_binding_mismatch` before code consumption; it does not
replace the tab’s binding, receipt namespace or pending request. A selector alone
authorizes no read or write. Resume retains binding and receipt IDs while
rotating browser credentials, the separate agent credential and live agent
session generation. Restart requires pairing again against that same binding.

The native launcher still opens only the nonsecret root. Binding and idea query
parameters are added by the browser after explicit pairing/selection. These
parameters contain no credential or idea text. This contract prevents a lost
Capture retry in one tab being replayed into another tab’s new receipt namespace.

## State and session

`GET /session` returns session identity, agent status and capability flags;
`GET /state?idea_id=<id>` returns this state shape. A selected idea can also be
returned by `/state` without a query. Browser state never returns an agent token.
On the first load with no `idea_id` in the URL, the browser adopts that private
selection only while it holds no unsaved answer and no pending write; otherwise a
foreign idea freezes the selection as uncertain. After any state has loaded, every
read must return exactly the requested identity, null included. Before any state
has loaded, Reload re-runs the first load, so it reads the URL's requested idea.
Step keys/order are `capture`, `priorities`, `method`, `discovery`, `exploration`,
`visualize`, `assess`, `review` (the server's order is authoritative; Methods and
Discovery may swap, Exploration always follows both; an idea made with an older
workflow the chain could not update is refused `unsupported_idea_version`, and the page shows the reply's own `message` sentence for it, never the code). The first `state` after an upgrade that updated ideas carries `notice`, a plain one-line string shown once in the save-status line (text only, never markup); later `state` replies omit it. Exact visible status vocabulary is `todo`, `current`, `saved`,
`review-needed`, `unsaved`, `skipped`. `todo` has no completion;
`current` identifies the active untouched step; `saved` requires durable current
acceptance evidence; `review-needed` means prior evidence is invalidated by
changed dependencies; `unsaved` means a draft differs from accepted data.
`skipped` requires durable human disposition evidence and
source revision. `current_step` identifies the expanded panel independently of
its status, allowing the current panel to be unsaved/review-needed. Capabilities
and response/error metadata carry unavailable/error; neither is a saved step
state. Missing handlers are permitted only in intermediate builds and cannot
count as completion. A legacy record gets no fabricated acceptance. Capture's
persisted original at revision 1 has saved capture evidence; later revised text
may be unsaved without altering the immutable original.

```json
{
  "ok": true,
  "code": "ok",
  "session_id": "session_fixture",
  "idea_id": "idea_00000000000000000000000000000001",
  "revision": 1,
  "draft_version": 2,
  "backlog_revision": 0,
  "agent_status": "disconnected",
  "current_step": "priorities",
  "steps": {
    "capture": {"status": "saved", "accepted_revision": 1, "evidence_id": "capture-receipt-1"},
    "priorities": {"status": "unsaved", "accepted_revision": null, "evidence_id": null},
    "method": {"status": "todo", "accepted_revision": null, "evidence_id": null},
    "discovery": {"status": "todo", "accepted_revision": null, "evidence_id": null},
    "exploration": {"status": "todo", "accepted_revision": null, "evidence_id": null},
    "visualize": {"status": "todo", "accepted_revision": null, "evidence_id": null},
    "assess": {"status": "todo", "accepted_revision": null, "evidence_id": null},
    "review": {"status": "todo", "accepted_revision": null, "evidence_id": null}
  },
  "draft": {"step": "priorities", "fields": {"urgency": 7, "importance": null}},
  "capabilities": {"agent": false, "memory": false, "uploads": true, "handoff": false},
  "resume": {"required": true, "reason": "agent_disconnected"}
}
```

Agent states: `connected`, `paused`, `disconnected`.
The agent idle pause is fixed at 600 seconds.
Any successful browser write (an `ok` result that applied or was a no-op: capture, draft, accept, navigate, selection, visual choices, uploads, handoff), a successful pairing and an `activity` ping reset only that idle clock for a connected agent.
Reads never do: reloading state or re-reading a stored request result does not count, and neither does a refused write.
They never renew the heartbeat and never revive a paused or disconnected agent.
Empty agent waits do not reset idle.
The agent heartbeat is 35 seconds, renewed by an events wait or a matched respond.
Once a wait has delivered a request, the heartbeat cannot expire for up to 600 seconds (10 minutes) while that request is unanswered (the answer window); completing or cancelling the request ends the window, and a repeated delivery never extends it.
Heartbeat expiry,
interruption, turn end or compaction must visibly disconnect/pause and cancel
requests; no permanent thinking state. Rebind explicitly and preserve draft.

### Accepted fields and draft reload

`GET /state` additionally returns `accepted`, a mapping of every step key to its
last persisted accepted fields or `null`, and `drafts`, a mapping of step keys
to persisted partial draft fields. Fields follow the schemas below. The existing
`draft` member is the selected step's convenience view, derived from `drafts`;
it is not another authority. Empty/new ideas have null accepted fields and an
empty drafts mapping. The browser uses these values to restore editing buffers
after reload and when reopening a saved step. It never reconstructs accepted
answers from status labels. Invalidated accepted fields remain available for
inspection; their step status is `review-needed`, not a current acceptance.
Unsaved local buffers take precedence until explicit reload/reapply resolves a
conflict. Responses contain no private agent credential.

## Capture, drafts, acceptance

`POST /capture`: original words persist verbatim first, revision 1. Confirmed
workspace is trusted resolution or service-host validated explicit selection;
name/path must not be invented from a display title. Fixture path below is a
fictional service-host path, not an existing deployment claim.

```json
{"request_id":"capture-1","raw_text":"FAKE: improve a lunch box\n","workspace":{"name":"fixture-project","path":"/example/fixture-project","confirmed":true}}
```

`POST /draft`: editable partial fields; no accepted-revision change. Browser
keeps its unsaved buffer on any failure. Draft writes only the idea detail if
index display/order is unchanged.

```json
{"request_id":"draft-1","idea_id":"idea_00000000000000000000000000000001","expected_revision":1,"expected_draft_version":2,"step":"priorities","fields":{"urgency":7,"importance":null}}
```

`POST /navigate` persists the selected panel for explicit Pause/resume. Exact
payload: `{request_id,idea_id,expected_revision,expected_draft_version,step}`.
It uses normal authentication, request receipts and revision/draft CAS. It changes
only current_step; accepted revision/history and draft_version stay unchanged.
Selecting the same step is a no-op. Ordinary tab navigation remains local; Pause
saves dirty buffers then records the panel where the user paused. No fields are
invented or accepted by navigation.

`POST /accept`: server validates step requirements and source dependencies,
records one accepted revision, and invalidates transitive dependents atomically.
No unchanged acceptance creates a revision. Human acceptance is separate from
agent recommendation. Proposal acceptance uses `proposal_id` when needed;
manual steps use `null`. `expected_backlog_revision` is mandatory for placement,
null for non-placement acceptance. Workflow evidence extends a versioned
snapshot; it does not silently change the legacy snapshot keys.

```json
{"request_id":"accept-1","idea_id":"idea_00000000000000000000000000000001","expected_revision":1,"expected_draft_version":3,"step":"priorities","fields":{"urgency":7,"importance":8},"proposal_id":null,"expected_backlog_revision":null}
```

```json
{"ok":true,"code":"ok","request_id":"accept-1","write_state":"applied","idea_id":"idea_00000000000000000000000000000001","revision":2,"draft_version":3,"backlog_revision":0}
```

`GET /requests/<request_id>` returns the recorded mutation result (or 404
`request_not_found`) for reconciliation. It does not initiate another write.

## Step field schemas frozen for J2/J3

The versioned workflow owns these fields, separately from unchanged legacy
ratings/assessment contracts. Drafts may contain null partial values;
acceptance enforces meaningful required fields. All text is rendered as text.

| Step | Accepted fields and requirements |
| --- | --- |
| capture | `raw_text`, confirmed `workspace:{name,path,confirmed}`; immutable origin remains separate |
| priorities | `urgency`, `importance`, independent integers 1–10; neither prefilled |
| method | `selection`, `reason` (optional), `memory`; no preselection |
| discovery | `problem`, `audience`, `workaround`, `evidence`, `kill_criteria`, `challenges`, `prior_art:[{name,link,does,differs,licence}]` (at most 8; all text), `prior_art_none` (boolean), `prior_art_searched` (text); accept needs rows with name, differs and licence filled and `prior_art_none` false, or `prior_art_none` true with no rows and `prior_art_searched` filled. A Discovery accepted before these fields existed still loads and reads "Not checked" |
| exploration | `outcome`, `alternatives:[{route,reason}]`, `assumptions:[text]`, `scope`, `scope_reason`, `next_slice`, `learning:[text]`, `investment`, `experiment`, `sketch`; meaningful alternative and at least one sketch item (title and done-when) required |
| visualize | `disposition`, `reason` (optional), `design_set_id`, `brief_evidence_id`, plus `source` (`claude_design` or `prototype`) and `assets` when a set is accepted; disposition `accepted_set` or `skipped` |
| assess | `assessment`, `position`; assessment has existing exact method/version/inputs/basis/assumptions/confidence/provenance schema |
| review | `handoff_id`, `source_revision`; derived packet/checks, no archive on copy |

Scope vocabulary stays `small-change`, `capability`, `project`, `epic`. Method
vocabulary stays `bounded-plan`, `adaptive-slices`, `appetite-led`,
`experiment-led`. The budget and experiment inputs sit in the Exploration answer. Appetite-led requires `investment:{cap,unit,boundary}` with
positive finite cap, nonempty unit and boundary. Experiment-led requires
`experiment:{question,evidence,success_criterion,stop_rule}`. Other methods
require their unused conditional fields to be null. Memory evidence is
`{status,sources,rationale}`; status `found`, `searched_no_preference`,
`unavailable`, `error`; source references support any claimed preference.

```json
{"selection":"appetite-led","reason":"FAKE: constrain initial experiment","investment":{"cap":2,"unit":"sessions","boundary":"FAKE: fixture only"},"experiment":null,"memory":{"status":"unavailable","sources":[],"rationale":null}}
```

```json
{"assessment":{"method":"wsjf","version":"fixture-v1","inputs":{"value":3,"time_criticality":null,"enablement":2,"effort":1},"basis":"FAKE fixture reasoning","assumptions":[],"confidence":"low","provenance":"current-agent proposal"},"position":{"proposed_position":1,"actual_position":1,"neighbors":{"before":null,"after":null},"override_reason":null}}
```

Score is computed by the existing validator; partial/null remains unknown and
Kano remains categorical. Agent recommendation cannot alter human ratings.
Position acceptance includes accepted snapshot/evidence and backlog CAS; stale
order returns conflict with no score change. Server-side eligibility validates
all current prerequisites even if a browser button is enabled.

## Current-agent proposals

`POST /propose` accepts typed `operation` and source identifiers, never an
arbitrary executable prompt. Operations are allowlisted: `discovery`, `exploration`, `memory`,
`method`, `visual_brief`, `assessment`, `position`. These are common protocol
names; not-yet-built handlers return unavailable rather than fake results.
In the current workflow only `visual_brief` is answered with `respond` (its proposal is the closed signal `{prototype_skill: available|unavailable}`, the event `data` is abridged in the example below).
Discovery, Exploration, Methods (memory only) and Assess are answered with `fill`.

```json
{"request_id":"propose-1","idea_id":"idea_00000000000000000000000000000001","expected_revision":2,"expected_draft_version":3,"operation":"visual_brief","source_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
```

Agent event/reply correlation is exact on `request_id`, `session_id`, `idea_id`,
`accepted_revision`, `draft_version`, `operation`, `source_digest`. Event adds
monotonic `sequence` and bounded `data`; response adds typed `proposal` only.
The agent credential binds provenance server-side. A response does not accept.
No stale source/revision/session reply survives resume. Request cancellation
and duplicate conflicting responses return explicit errors.

```json
{"sequence":1,"request_id":"propose-1","session_id":"session_fixture","idea_id":"idea_00000000000000000000000000000001","accepted_revision":2,"draft_version":3,"operation":"visual_brief","source_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","data":{"capture":{"raw_text":"FAKE: improve a lunch box"}}}
```

```json
{"request_id":"propose-1","session_id":"session_fixture","idea_id":"idea_00000000000000000000000000000001","accepted_revision":2,"draft_version":3,"operation":"visual_brief","source_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","proposal":{"prototype_skill":"available"}}
```

## Visual sets, inert uploads and handoff

`POST /visual-disposition` uses the acceptance envelope, step `visualize` and
its fields. Dispositions are `accepted_set` (with `source` `claude_design` or `prototype`) and `skipped`; a skip needs no reason, and the old `not-applicable` value is invalid. `POST /visual-set/accept` uses the same envelope plus
`design_set_id` and `asset_ids`. For first construction, `design_set_id` is null
and `asset_ids` contains 1–20 unique complete asset IDs; the server generates
immutable set membership/source evidence and accepts its pointer atomically.
For an existing set, `design_set_id` is its opaque ID and `asset_ids` is null.
Membership and every blob hash are checked server-side. Stale replacement
never silently becomes accepted. Historical sets/evidence remain intact.

Upload begins `POST /uploads` with
`{request_id,idea_id,expected_revision,name,declared_type,size}`; response gives
opaque `upload_id`. `PUT /uploads/<upload_id>/bytes` streams bounded
`application/octet-stream` with cookie/Origin/CSRF and content length. No generic
path argument. Limits: 20 files/set, 25 MiB/file, 100 MiB/set. Allowed inert types:
PNG/JPEG/WebP/PDF/SVG/HTML/CSS/JSON/Markdown/text/ZIP, validated extension/type/basic
signature. No extraction/execution; active formats download-only. Authorized
`GET /attachments/<asset_id>` returns inert download or validated raster only.
An interrupted upload is not completion and retry retains request identity.

`POST /handoff`:

```json
{"request_id":"handoff-1","idea_id":"idea_00000000000000000000000000000001","expected_revision":7,"expected_draft_version":9,"expected_backlog_revision":2}
```

Returns immutable handoff ID, source revision/hashes, packet path, prompt text and
current counters. Creation/copy never increments accepted revision or archives.
Only successful real validated plan registration archives the linked revision.
Review is derived from the verified current handoff packet. `POST /accept` with
step `review` returns 400 `derived_step`; use `/handoff` instead. Generating a
packet must not mutate accepted workflow fields at the same revision or alter an
existing immutable snapshot. `GET /state` may derive Review fields/status from
the current verified packet; a stale packet never produces a saved Review check.
Copy again rereads eligibility; edit makes previous packet visibly old. Clipboard
failure uses selectable text and explicit human "I copied it" acknowledgment.

## Failure fixtures

All errors preserve the editing buffer. Use current counters where the caller is
authorized and the store can be read; auth failures must not reveal idea state.
`committed` preserves existing uncertain-commit compatibility.

```json
{"ok":false,"code":"stale_revision","request_id":"accept-1","write_state":"not_applied","revision":3,"draft_version":4,"backlog_revision":1}
```

```json
{"ok":false,"code":"invalid_input","request_id":"draft-1","write_state":"not_applied","revision":2,"draft_version":3,"backlog_revision":0}
```

```json
{"ok":false,"code":"browser_unauthorized"}
```

```json
{"ok":false,"code":"wrong_origin"}
```

```json
{"ok":false,"code":"too_large","write_state":"not_applied","revision":2,"draft_version":3,"backlog_revision":0}
```

```json
{"ok":false,"code":"agent_unavailable","request_id":"propose-1","write_state":"not_applied","revision":2,"draft_version":3,"backlog_revision":0,"agent_status":"disconnected"}
```

```json
{"ok":false,"code":"durability_uncertain","request_id":"accept-1","write_state":"committed_uncertain","committed":true,"revision":3,"draft_version":3,"backlog_revision":0}
```

| HTTP status | Meaning |
| --- | --- |
| 400 | malformed/typed invalid input |
| 401 | missing/expired credential |
| 403 | Host/Origin/CSRF refusal |
| 404 | unknown route/request/asset |
| 409 | stale revision/source, conflicting replay, save conflict, `idea_moved`, `not_moved`, `delivery_conflict` |
| 413 | payload/file/set too large |
| 503 | agent unavailable, bounded busy/capacity |
| 500 | redacted internal error or reported uncertain persistence result |

A moved or delivered idea is read-only: every browser save for it is refused `idea_moved` (409) with `details.home`.
`GET /state` and the idea list still serve it, carrying `lifecycle`, `home` and `delivered_ref`, and the list has a `lifecycles` map.
The capture state carries the configured `default_workspace` (or null) so the page can prefill the editable workspace field.

Headers: `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: no-referrer`, CSP self-only/no inline or eval scripts,
`object-src 'none'`, `frame-ancestors 'none'`. Fixed static allowlist, no listing.
The shared service implements this seam; parallel modules do not invent variants.


### Terminal conversation in the state (redesign)

With a connected agent, `GET /state` carries `conversation`: `null`, or the open,
delivered request for this idea at its current accepted revision as
`{request_id, operation, idea_id, accepted_revision, fills}`, where `fills` is the
ordered list `{sequence, fields}` (sequence 1, 2, ...). The page applies each new
fill once to that step's buffer (only the named fields) and saves the draft as
usual; the operator accepts. Opening another step's conversation supersedes an
unanswered one (`request_cancelled` to its later fills); a second request for the
same step while one is open is `request_busy`.

### Hand release

Discovery and Exploration are locked in the page while the terminal conversation is connected.
`POST /api/v1/conversation/release` with `{step}` (`discovery` or `exploration`) takes that step by hand.
It cancels only that step's open, unanswered agent request (the binding stays usable; the agent then sees `request_cancelled` for it).
The reply is `{ok, code, idea_id, step, hand, released, write_state, revision, draft_version}`, with `released` the number of requests cancelled; the call is idempotent.
`GET /state` may carry a `hand` mapping of those two steps to booleans, and a later request for a released step is refused `hand_released` (409).

## Private current-agent transport (CP2 source implementation)

These fixed routes are separate from browser and owner
control authorization. The transport, native client and owner-service composition are implemented and
verified with real loopback HTTP and Markdown. Browser proposal interaction
qualification remains pending.

- `GET /agent/v1/events?session_id=<SID>&after=<sequence>&timeout=<seconds>`
  waits at most 25 seconds. All three query fields are required. The response is
  the broker status plus `events`; a wait holds no browser binding or Store lock.
- `POST /agent/v1/respond` accepts exactly the correlation fields above plus
  `proposal`. It persists a suggestion only. A publication uncertainty retains
  `write_state: committed_uncertain`; do not change the response on retry.
- `POST /agent/v1/fill` accepts exactly the correlation fields above plus `fields`:
  a partial, typed slice of the open step agreed with the operator in the terminal
  (Discovery: any Discovery field; Exploration: any Exploration field; Method:
  `memory` only; Assessment: `assessment` and `proposed_position`; Visualize
  brief: `source` and `assets` of the prototype road's design set; never a method
  selection or reason, an acceptance, a disposition or an actual position). The request must have been delivered and still be open. The fill is
  held in the broker (volatile, at most 64 per request); it writes no draft, idea
  or receipt. It returns `{ok, code, request_id, operation, status: "pending",
  write_state: "not_applied", fill_sequence}` and renews the 10-minute answer
  window. Refusals: `invalid_fill`, `request_cancelled` (superseded when the
  page opened another step's conversation), `request_closed`,
  `request_not_delivered`, `request_not_found`, `response_mismatch`,
  `fill_capacity`. The native client reports these and the other fixed broker
  codes as they are; any other refusal stays `agent_request_failed`.
- `POST /agent/v1/session-close` accepts exactly `{session_id}` and revokes agent
  access while preserving the paired browser and its draft-writing capability.

Every request pins the exact loopback Host and uses separate
`X-Idea-Agent-Binding`, `X-Idea-Agent-Generation` and Bearer authorization headers.
Cookie, Origin, CSRF and browser-selector headers are refused. Agent and owner
routes also refuse Sec-Fetch-Site/Mode. Agent binding/generation headers are
refused on every non-agent route; Bearer authorization is accepted only on the
fixed agent and owner-control routes. Credentials are never supplied in URLs
or command arguments. Duplicate private headers fail
closed. Rebinding invalidates previous waits and responses. Four concurrent
agent waits have separate admission from eight ordinary operations; twelve
handlers total bounds connections including header parsing.

The native `AgentClient` retrieves its existing generation through the fixed
owner-authenticated `agent-credentials` control operation. Runtime validates
private files and the service/store/instance/challenge before returning an
internal credential handle. Retrieval neither opens nor resumes a session.
Public client results and errors contain no reusable credential, and the client
never retries a write automatically. This protocol is not a browser endpoint
for obtaining credentials and does not qualify native Windows privacy.


## Trusted suggestion projection

The owned source service projects `agent_generation` (an opaque nonsecret
`agent_<uuid32>` or null before a binding exists), `proposal_sources`, and
`proposals`. A new generation cancels the old browser request even if both
polls report connected. The marker grants no authorization. Restart restores
durable evidence but leaves the agent disconnected until explicit resume.

`proposal_sources` maps the Discovery, Exploration, Method and Memory operation names to
`{available,code,source}`. An available source contains accepted_revision,
draft_version, typed consumed data and source_digest. Browser requests use these
server values after saving dirty buffers; they cannot supply their own source
text. Replies require exact counters and consumed inputs.

Each suggestion summary contains proposal_id, request_id, operation,
accepted_revision, draft_version, source_digest, proposal, stale, stale_reason,
acceptance_eligible, acceptance_reason, evidence:{path,sha256} and content_omitted. A target-field autosave can make
response correlation stale while acceptance remains eligible: acceptance checks
the original consumed inputs independently from the human's edited target.
Source changes, a different generation or unavailable agent prevent linked
suggestion acceptance. Old evidence stays inspectable.

Memory results distinguish found, searched_no_preference, unavailable and error.
Found and completed-search claims require matching immutable evidence from the
current initiating agent before Methods acceptance. This establishes the claim's
provenance, not independent verification of external reference content. Manual
Methods choices with unavailable/error and no sources can proceed disconnected.
Recommendation never supplies the user's Method selection.

Suggestion projection is bounded to 256 KiB and at most 128 records; duplicated
operation sources are bounded to 512 KiB. Newest eligible suggestions have body
priority. A body that cannot fit is null with content_omitted=true and cannot be
applied from that projection. proposal_inventory exposes total/projected/omitted
record counts, content_omitted count and the canonical idea Markdown index_path.
That document retains all immutable history links. No record is truncated or
deleted to satisfy a browser response limit. Oversized operation sources report
source_too_large or source_projection_capacity without blocking saved-state reads.

An exact completed agent reply retry returns its recorded publication even after
a source edit; eligibility is a separate browser decision. A pinned uncertain
reply reaches the Store's durable receipt lookup before current-source checks.
Failed reconciliation remains committed_uncertain. Authentication, generation
and conflicting-payload checks still precede replay. Unpublished stale requests
release unused response capacity while retaining request tombstones.

Authenticated successful `GET /api/v1/state` responses use compact UTF-8 JSON
and a 130 MiB transport ceiling (`2 * MAX_STATE + 2 * MAX_INPUT`). Existing
legacy state permits 64 MiB and the selected draft appears twice in the browser
projection. This inherited bound preserves saved-data compatibility; it does not
increase write admission or private response limits, which remain 1 MiB. Only
the trusted transport selects the larger ceiling after authentication and
session persistence succeed. Failed state reads and all other JSON responses
retain the ordinary limit. This admits a large serialized response buffer; no
throughput or memory-performance qualification is claimed. Authored an implementer.

## CP3 implementation additions

The exact immutable upload/asset/set record layout and source witness are in
asset-format.md. `asset_inventory` in authenticated state projects verified
upload intents/completed files and historical design sets, with explicit omitted
counts. `asset_inventory.orphans` is `{count,ids}`: `count` is the full observed
number of unlinked stages/blobs across the authenticated Store, including other
ideas; these physical diagnostics are Store-wide even when an idea is selected.
Linked records remain specific to the selected idea. Verified sealed stages that share the completed blob inode are retained successes and excluded from orphan diagnostics; they still count toward physical capacity. `ids` is bounded to 128 generated opaque IDs.
No filesystem path supplied by the browser is accepted, no orphan is automatically
deleted, and omission is explicit when count exceeds ids length. J8 projection
owns this surface; Store supplies memory-only verified linked inventory, while
its filesystem boundary supplies confined orphan diagnostics. Asset links are
append-only, capped at 256 per idea; capacity exhaustion refuses clearly without
evicting history. Original Capture uploads never auto-populate an accepted set.

Assess uses existing `/propose` with operation `assessment`; its response is
`{assessment,position}` with the existing exact step schema and no caller-supplied
score. Source data is `{steps,backlog,target}`: steps are current accepted
Capture, Priorities, Discovery and Exploration; backlog contains revision, actual ordered IDs and one
validated ratings/latest-assessment comparison per ID; target is null or durable
partial Assess draft. The specialized canonical assessment digest validates this
schema; old step-map source digests remain unchanged. Response CAS checks the
full original source. Linked human acceptance permits target edits and actual
position override, while checking original consumed inputs, backlog, proposed
position and actual stable neighbors. AI provenance is authenticated immutable
evidence, not a string supplied by a browser. Native client/remote-upload and
attended picker qualification remain later gates.

### Upload completion receipt identity

Metadata success returns generated `upload_id`, `asset_id`
and `completion_request_id`. The latter is exactly `upload-bytes:<upload_id>`;
the server derives it, so the binary route needs no invented request header or
JSON envelope. Binary success includes those IDs and `request_id` equal to that
completion identity. After a lost response the browser uses the existing request
result and state reads against this identity; a missing receipt never means a
completed upload. An explicit same-upload retry hashes the supplied bytes. The
validated completion payload binds their SHA and size, so different bytes cannot
replay the original result. Durable receipt replay precedes business/source/CAS
checks after authentication and byte-payload validation. There is no automatic
retry. Metadata receipt reconciliation remains separate.

### Assessment state projection

Selected authenticated state exposes `human_ratings`
(the existing attributed ratings record or null) and `assessment_summary` (the
latest validated attributed domain assessment or null). Neither is a new ranking
authority. `backlog` is the exact observed `{revision,order,comparisons}` described
above, or null when no selected idea exists or the complete bounded projection
cannot fit. `backlog_status` is `{available,code}` and makes that absence explicit.
Do not truncate comparisons or fabricate an order; unavailable placement stays
unavailable while ordinary saved-state reads remain possible.

### Asset state projection

Selected-idea `asset_inventory` contains `records` (verified record/evidence/blob
entries), `total`, `projected`, `omitted`, and `orphans:{count,ids}`. The companion
`asset_inventory_status` is `{available,code}`. Within the existing state-response
bound, all linked records project with `available:true`, `code:ok`, and
`omitted:0`. When the complete inventory would exceed that bound, `records` is
empty, `projected:0`, `omitted:total`, and status is
`{available:false,code:asset_projection_capacity}`; ordinary saved state remains
readable. No design-set member list is truncated. Without a selected idea,
inventory is null and status is `{available:false,code:no_selection}`.

Packaged Visualize validation receives a detached verified inventory snapshot
from the active Store transaction, including staged immutable set records. The
trusted context carries data only; request-supplied inventory never substitutes
for the Store's records and blob witnesses.

### Archived ideas and attachment reads

Selected state includes `idea_status`, exactly `active`
or `archived`, and null without selection. This authoritative status remains
available even when the complete backlog projection cannot fit. New upload
metadata and completion of an unfinished intent refuse with `idea_archived`
after archival. Existing published metadata/byte receipts still replay, and
existing attachments remain readable. In-flight completion rechecks archive
status before publishing its evidence; retained unlinked bytes are not accepted
attachments.

A completed byte retry validates the supplied length, type/signature and hash
without creating a stage, generating an ID or checking new-file capacity. Its
exact receipt is returned; different bytes conflict without creating files.
New ingestion reserves capacity for stage, blob and completion evidence, while
Store checks the physical file union before publishing any asset evidence.

`IdeaApi.attachment(assetId, expectedSize)` uses the fixed authenticated GET
route with the pinned binding header, same-origin cookie, no-store and refusal
of redirects. It verifies the advertised length/type/status and bounds streamed
bytes to the exact expected size (1–25 MiB). The returned Blob is always
`application/octet-stream`; renderers use a temporary download URL and revoke
it after the download interaction. Active file contents are never rendered.


### CP3 archive, recovery and transfer boundaries

Author: an implementer. Archived existing ideas refuse new Capture, Priorities, Methods
and Assess acceptance with `idea_archived`; initial new Capture remains allowed.
Browser controls and callbacks require authoritative active status, keep draft
answers and direct the operator to explicitly redo and accept Exploration. The pure Assess handler also
retains its `archived_revision` refusal; both codes receive an Exploration-first notice.
All new Visualize acceptances, including direct generic accept,
existing/new design sets and Skip, refuse `idea_archived` after
archival. Refusal precedes new-set ID generation or publication. Exact historical
receipts and files remain readable. Exploration remains reachable: its explicit
"Reactivate idea and accept Exploration" action creates an active next revision, even
with unchanged answers, while
preserving immutable archived plan and asset bytes. Merely opening or editing
Exploration does not reactivate it.

JSON requests retain their finite 15-second budget. Upload and attachment byte
transfers use a separate finite 300000ms default (optional fourth IdeaApi
constructor argument); they never retry automatically. A terminal upload refusal
keeps the local selection and original request identity until explicit removal
and reselection. An uncertain save keeps Check result with the original identity.
Local selection queues remain bounded to 20 files/100MiB including completed
rows; removing a local row does not delete retained Store history. Explicit
matching zip/Markdown MIME aliases become canonical types; signatures remain
server-validated. This does not qualify native OS chooser behavior.

Assess and Visualize validate exact request and idea identities before authority
reads, which always target the submitted idea. Recovered acceptance must match
the current accepted fields and saved step. A valid historical receipt with
current drift reports `saved_state_changed`, keeps newer answers, and does not
advance falsely. Stale CAS failures require fresh state; retrying an identical
refused payload is not offered. Invalid receipts remain uncertain.

An admitted malformed agent response remains pinned to its correlation even
when publication is refused; a corrected response with that same correlation
cannot replace it. Stop that attempt and explicitly resume a fresh agent
generation/request before proposing again. No automatic repair or retry is
claimed. Concurrent overlapping upload retries may retain multiple stages,
which consume the existing physical cap; nothing is deleted automatically.
# CP4 Review and Ideas contract (an implementer; implemented source)

The packet schema/publication boundary is defined in `handoff-format.md`.
Only the existing browser credential/Origin/CSRF policy admits these doors.

- `POST /api/v1/handoff`: exact `{request_id, idea_id, expected_revision,
  expected_draft_version, expected_backlog_revision}`. Validate integer counters
  and typed identity; replay uses the same durable request namespace. Result
  carries existing write-state/counter fields and `handoff`, an exact mapping
  `{handoff_id, source_revision, source_digest, path, sha256, prompt,
  source_files, design_set}`. Current paths are canonical absolute service-host
  observations. Historical packets retain their originally recorded paths after
  Store moves and are unavailable for Copy until explicit fresh generation.
  A separate typed `handoff_current` flag reports currentness. A historical
  replay has `handoff_current: false`, `code: historical_handoff` and cannot be
  copied as current. Store receipts contain compact packet link/ordinal only;
  HTTP renders the prompt from verified immutable bytes.
  Both current and longer historical results are bounded before publication.
  Receipt replay can identify an old saved packet; current handoff delivery rejects stale prerequisites/source
  rather than displaying historical prompt as current. If delivery reconstruction
  fails after a durable applied/no-op receipt, HTTP500 reports
  `committed_uncertain`, `committed:true` and exact request/idea/counters;
  prepublication refusals remain `not_applied`. Bare `handoff?` is refused.
- Selected state additionally carries `handoff` (same verified mapping or null)
  and `handoff_status: {available, code}`. `available` means a CURRENT eligible
  packet. Historical packet identity is retained visibly when stale; derived
  Review is supplied only through the existing trusted packet provider. Reads
  never create packets or mutate acceptance. Capability `handoff` means the
  packaged provider exists, not that prerequisites are complete.
- `GET /api/v1/ideas`: no query or caller-supplied path. Returns
  `{ok, code, backlog_revision, total, ideas}` in actual accepted order. Each row
  is `{idea_id, revision, position, title, status, method, updated, detail_path}`.
  `title` is literal original/revised Capture display text, at most 200 Unicode
  scalars; method is actual accepted selection or null; updated is the actual
  latest accepted snapshot timestamp. Status is `archived`, `ready-to-plan`,
  `review-needed`, or `in-progress`, derived from real current workflow/packet.
  No counts, timestamps or paths are invented. At most the existing Store idea
  bound and 1 MiB complete result; excess returns explicit `ideas_capacity`,
  with no silently partial accepted order.
- `POST /api/v1/selection`: exact `{idea_id}`, typed saved ID or null. This is
  authenticated per-binding private navigation, not an accepted domain write,
  planning request or packet mutation. A saved ID must exist. Null explicitly
  resets that binding to a new unsaved Capture; browser local buffers are reset
  only after success. Persist through the existing selection callback; failure
  retains current answers and reports `session_persistence_failed`. Reads/Open
  must not clear unsaved buffers or ambiguous requests implicitly. Selection
  is idempotent and updates no accepted/draft/backlog counter. Do not add a new
  recovery database or credentials to the URL.

`/ideas.js` is a fixed static allowlist entry, loaded literally by the packaged
app. Review/Ideas render names, paths and prompts as text. Copy checks server
readiness plus local dirty/pending state; successful clipboard write returns to
Ideas. Clipboard denial leaves the full selectable prompt and explicit manual
confirmation. That confirmation rechecks current readiness before returning to
Ideas. Copy again likewise rechecks and uses the selectable fallback on denial.
No clipboard success/manual confirmation archives or automatically plans.

Open returns to saved Review/current required step. New idea performs explicit
selection reset before presenting an empty Capture. Preserve polling, focus,
binding pin, exact receipt retry and autosave behavior; do not start an extra
dispatcher or continue old selected-idea polling after a reset.

### Exact CP4 selection reply

Contract author: an implementer. Successful selection returns exactly
`{ok:true, code:"ok", session_id, idea_id, revision, draft_version, backlog_revision}`.
A saved selection carries its actual idea/draft counters. Null carries zero
idea/draft counters and the actual backlog revision. There is no request receipt
or write-state. Persistence failure restores the previous Service selection and
returns `session_persistence_failed`; local answers stay intact.

### Typed browser/controller seams

`IdeaApi.handoff(payload)` sends the exact five-field payload above.
`reconcileHandoff(requestId, ideaId, expectedPayload)` requires all three arguments
and verifies submitted identities before the selected-state authority read.
`validateHandoff(packet, ideaId = null)` is both a named export and an API method;
it returns a detached exact typed packet or throws `ApiError`. `ideas()` checks
the complete ordered projection; `selection(ideaId)` verifies the seven-field
reply. There is no automatic mutation retry.

Credential failure handling is scoped to the navigation/session attempt that
started a request. Valid selection attempts, pairing attempts and accepted new
sessions advance that scope. A superseded JSON/upload/attachment 401 cannot clear
current credentials. A current 401 still clears them; forbidden responses retain
the existing policy. Old responses never restore credentials.

`Flow.generateHandoff()` uses the established pending/reconciliation engine.
`canGenerateHandoff()` and `canCopyHandoff()` require authoritative active status,
all six completed prerequisites, capability, and no dirty, pending, proposal,
busy, paused, disposed or uncertain-selection state. `currentCopy(expectedPacket
= null)` refreshes server authority, validates the packet, rechecks local guards,
and pins idea, packet ID/hash, source revision/digest and path. It returns a
detached current packet or null, never publishes a replacement.

`Flow.loadIdeas()`/`showIdeas()` keep list/view state distinct from workflow
buffers. `selectIdea(savedIdOrNull)` advances the selection epoch before stopping
polling. Only a verified reply and matching authoritative state reset buffers and
proposal associations. Failures retain answers; an ambiguous result disables old
scope writes/polling and offers explicit selection recovery. Old success, error
and finalizers cannot affect a newer flight. The existing polling options resume
with one loop after a confirmed selection. `readAuthority(ideaId, sessionId)`
pins the requested identity, including null, and session before its awaited read;
it validates without adopting, except the clean first-load `adoptSelection` case in
State and session. Foreign replies freeze unsafe writes and preserve
answers. `restoreSelection()` verifies the same current identity and preserves
dirty buffers, original pending requests, navigation and paused state. Ordinary
New/Open still refuse to discard these answers. Missing handoff receipts plus
newer edits retain visible Check/result uncertainty and the original request; a
missing receipt does not prove the original write cannot land later.
Initial URL selection applies only before state exists: authoritative null after New or reconnect stays null.
Connection pins the requested identity, adopting only in the clean first-load case
above; Resume pins the current identity. Connection, Reload and Resume run one load
at a time (Resume and a verified-state Reload wait out a load in flight; a queued
Resume does nothing once the workflow has resumed); a verified load clears a stale error
unless a write is pending. Recovery uses
the same-identity preserving restore after verified state exists, or reloads the
explicit requested initial scope while no verified state has been adopted.

The app loads six fixed literal modules, including `/ideas.js`; missing HTTP404
alone means unavailable. Review shows accepted summaries and the full read-only
prompt as text. Copy checks currentness before clipboard write and again after
it, after loading Ideas, and before switching views. The live connection callback
is rechecked across awaits. On denial, the full selectable prompt and explicit
manual confirmation remain in Review. A completed copy followed by an Ideas
read failure retains the full prompt and visible copy-success plus the named
list error; the existing Ideas action performs explicit recovery. No publication
is retried. Confirmation uses the pinned packet and
rechecks readiness. Ideas Copy again explicitly selects the row and verifies its
current packet; it does not generate a new one. Stale/archived rows disable Copy
and keep historical packet identity. New/Open refuse to discard dirty or ambiguous
answers. Native clipboard, picker and complete accessibility qualification remain
separate from the Linux headless browser proof in `cp4-validation.md`.
