# Immutable planning handoff

Author: an implementer. Implemented CP4 source contract. Focused checks and Linux
browser/CLI product proof are recorded in `cp4-validation.md`. The block battery
is recorded; review found blockers and bounded repairs are underway.
This extends the existing Markdown authority and transaction writer.

## Publication and ownership

A packet is canonical UTF-8 Markdown starting with YAML frontmatter. Reuse the
existing confined immutable path `history/<idea_id>/metadata/<sha256>.md`, where
the hash addresses the complete bytes. Do not add a live handoff registry or
change the legacy `proposals`, `plans`, or `executions` record schemas.

Reserve append-only detail extension `glitch_idea_handoffs`, containing links
`{handoff_id, path, sha256}`. Its protected bytes are verified and included by the
same Store read/encode/CAS boundary as other immutable evidence. Invalid links,
missing linked bytes, reordered receipts and orphan evidence fail closed. These
self-consistency checks cannot detect deliberate coordinated removal or rewriting
of all corresponding detail, packet and receipt evidence across a restart.
Existing stores without this extension remain unchanged and readable. Packet generation
publishes the packet, protected detail link, and exact existing-session request
receipt together through the recoverable transaction writer. No accepted idea,
draft, or backlog revision changes. Store may advance its persistence counter;
an unchanged index is retained byte-for-byte. Recovery is forward-only.

Packets carry their publishing `session_id` and `request_id`. Store verifies the
exact receipt witness, idea identity, packet link and source revision without
enumerating unrelated receipt sessions. Valid request replay occurs before
business eligibility/path checks, never republishes or reactivates an idea.
The HTTP handoff door separately checks current eligibility before returning a
prompt as current; a historical receipt remains identifiable as historical.
Receipt results contain compact idea/counter metadata plus the packet link and
ordinal, never the prompt or complete packet. HTTP currentness is separate:
`handoff_current: true|false`; a historical replay uses `handoff_current: false`
and `code: historical_handoff` and is never enabled for Copy. Render the prompt
from verified packet bytes only after resolving the link. Exact stored request
results remain immutable. A NEW request on an unchanged eligible source reuses
the existing verified current packet, records its own compact receipt and adds
no packet link. The reused packet retains its original publishing receipt.

### Compact receipt and reader seam

The exact successful Store receipt result is
`{ok, code, request_id, write_state, idea_id, revision, draft_version,
backlog_revision, handoff_index, handoff_id, path, sha256}`. `path` is the
confined relative packet link. The original publishing receipt has `code: ok`
and `write_state: applied`; `handoff_index` is its one-based protected-link
ordinal. Its payload hash uses the existing canonical
`{operation: handoff, payload: {request_id, idea_id, expected_revision,
expected_draft_version, expected_backlog_revision}}` envelope. Publication changes
none of those accepted/draft/backlog counters. Reuse records a new receipt with
`write_state: no_op`, retaining the original packet's publishing witness.

`Store.handoffs(state, idea_id, with_links=False)` is an active-transaction,
filesystem-free detached accessor: records, or `{record, evidence}` entries
when `with_links` is true. Recorded absolute roots are historical observations,
not Store identity. Moving the Store preserves verified relative history reads
and original packet bytes; public historical prompts retain their recorded paths.
Current delivery and reuse separately require canonical current source/blob paths.
After a move, an explicit new generation creates a packet at the current location.
Safe path respelling is canonicalized only after original path/symlink checks.
The reader checks the immutable revision hash/path, origin and accepted snapshot, named original receipt and ordered link, plus
existing design-set membership/blob witnesses. Historical availability never
requires the workspace still to exist. Current delivery performs that check.

## Packet schema

Exact frontmatter fields:

- `schema_version: 1`, `kind: handoff`, `handoff_id: handoff_<uuid32>`;
- `idea_id`, `source_revision`, publishing `session_id`, `request_id`;
- `actor`, `timestamp`, `source_digest`;
- `source_files`: exact `detail`, `index`, and `revision` mappings, each containing
  an absolute service-host `path` and lowercase SHA-256 `sha256`;
- `origin`: exact immutable original wording/hash/actor/timestamp;
- `accepted`: the seven accepted Capture through Assess records, each containing
  its typed `fields` and original `acceptance` receipt, never drafts;
- `placement`: exact `{actual_position, neighbors: {before, after}}`;
- `design_set`: null when Visualize was skipped (its reason is optional), otherwise the verified
  `{set_id, members}` with explicit ordered members, each exactly
  `{asset_id, name, type, size, sha256, path}`: literal display name, validated
  type, bounded size, hash and absolute existing generated blob path.

The prompt is generated from the VERIFIED packet and its actual final path after
encoding. It is not embedded in the packet: a content-addressed packet cannot
contain a prompt naming its own complete-byte hash/path without self-reference.
The readable body is deterministically derived from those fields, safely
serialized, and checked on decode. IDs, field types, Unicode, bounds, canonical
bytes and every source/asset hash are validated. No arbitrary YAML constructors,
mutable validator configuration, credentials, plan validator argv/output, prior
plan contents, execution receipts, or unrelated ideas are copied into packets.
Reuse the existing bounded YAML codec rather than adding a parser/dependency.

At most 128 packet links per idea; no eviction. Individual packet bytes use the
existing `MAX_STATE` bound and the complete Store's existing file/byte limits.
Both current and longer historical HTTP envelopes, including the complete
prompt, must fit the existing 1 MiB JSON response ceiling BEFORE publication.
Excess is an explicit capacity refusal, not truncation or a published packet followed by response-too-large uncertainty.
Link/count or handoff HTTP capacity refusal is `handoff_capacity`; invalid or
oversized packet/schema bytes use existing `too_large`/`invalid_input` as
appropriate. Receipt capacity uses existing `receipt_capacity_exhausted`,
including its already implemented prospective encoded receipt check BEFORE
publication. Global Store capacity remains existing `too_large`.

## Source hashes and current eligibility

`source_files` hashes describe the real files read BEFORE packet/link publication.
This is deliberate historical evidence: publishing its own link changes the live
detail bytes. Never compare that prepublication detail hash to the postpublication
file and falsely mark the packet stale. The immutable revision hash remains an
independent evidence witness. Store CAS protects the exact observed source bytes
while publishing; live paths must still resolve to the generated Store objects.

`source_digest` covers canonical idea ID/revision, immutable origin, the seven
accepted fields/receipts, actual placement and the explicit design-set membership
and blob hashes. It excludes publication counters, packet links, navigation,
Notes and unrelated backlog entries. Eligibility additionally requires all seven
step states to be saved, or skipped for Visualize, no changed
draft or review-needed prerequisite, valid current method-specific fields, active
idea status, current actual placement/neighbors, an existing confirmed service-host
workspace directory, and freshly verified existing assets. Derive readiness from
the real workflow; do not use legacy domain `ready()` alone for managed ideas.

Historical packets stay readable as evidence after edits or archival. A verified
current packet yields only `{handoff_id, source_revision}` to the existing derived
Review seam. Copy again must recheck server eligibility and source identity; it
must not offer an older packet as current. No generic Review acceptance is added.
An old/stale Copy again is refused visibly; returning to Review and explicitly
making a current prompt is the recovery action. No silent regeneration.

The prompt exposes `live_detail` as a path only. Its prepublication hash remains
in immutable packet provenance, rather than implying a current file hash.
The prompt starts `/glitch-plan`, names the confirmed workspace, live detail and
immutable packet/revision paths, idea ID/revision, accepted outcome/scope/method,
next slice, assumptions/alternatives and actual optional design paths. It tells
the member to paste into a NEW window or pane and retain the exact Idea trace.
Copying or manually confirming copy never starts planning, archives, or accepts.

## Plan registration

Keep plan file reads and configured external validator execution on the existing
trusted CLI boundary. Add no browser path/command/validator execution endpoint.
Workflow-managed handoff and new plan registration require current workflow
eligibility; legacy-only ideas retain their existing contract. Successful repeated
plan registration is checked through its existing immutable evidence before new
eligibility checks, preserving archived receipt behavior. New registration links
exact current revision, invokes existing validation, and alone archives it.
Origin, revision, packet, plan and asset evidence remain immutable. Existing
archival revision semantics are retained;
this contract does not silently change the archived-idea policy:
only an explicit Exploration acceptance reactivates an archived idea.

Workflow-managed means exactly `'workflow' in idea`. Missing/currentness failures
use `not_ready`, source drift uses `stale_source`, counter drift uses existing
`stale_revision`, `stale_draft_version`, or `stale_backlog`, and archived new
handoff/registration uses `archived_revision`. The prompt includes the exact
two-line trace declaration under `## Idea trace`. It may describe registration
through the trusted helper, but never invent a future plan path or execute it.
Optional file paths name only the accepted design set; original Capture uploads
remain visible in the source detail and are not implied accepted design assets.

## Pure helper seam

The separate `idea_handoff_evidence.py` module imports domain/workflow/assessment,
the existing pure asset-codec validators/constants, and bounded Markdown helpers
only; it imports no Service, Store, route registry
or launcher. Its public functions are:

- `eligible_source(state, idea, design_set=None)`: detached accepted records,
  actual placement and semantic digest; validate workflow, method requirements,
  human ratings/domain assessment and the accepted Assess placement witness.
  `design_set` is already verified detached data; this pure function does no path
  existence check. Service/Store perform host filesystem checks outside it.
- `build_record(state, idea, *, source_files, design_set, handoff_id, session_id,
  request_id, actor, timestamp)`: exact packet using that eligibility result.
- `validate_record(record)`, `encode_record(record)`,
  `decode_record(raw, *, path=None, expected_idea_id=None, link=None)`:
  detached typed record, canonical Markdown and optional path/link identity.
- `record_link(record, raw)`: exact protected link; complete-byte SHA256 names
  the existing metadata path. No packet can embed its own resulting path.
- `render_prompt(record, packet_path)`: literal complete prompt from validated
  packet and actual absolute generated packet path. No filesystem reads.
- `recorded_packet_path(record, link)`: verify the exact link and return the
  original recorded absolute packet location without rebasing historical paths.
- `verify_current_paths(record, source_files, design_set=None)`: compare trusted
  current path observations independently of prepublication hashes.
- `verify_current(record, state, idea, design_set=None, *, source_files=None)`:
  compare eligible consumed source/revision and, when supplied, current paths.
  Delivery callers supply observations; changed publication counters and
  prepublication detail hashes are not compared to current detail bytes.

Bounded source observations and absolute design blob paths are supplied only by
the trusted Store/Service composition. Pure tests do not claim those paths exist.
Reuse the existing asset codec's Unicode-safe literal JSON-as-YAML approach when
needed for NEL/C1/noncharacters/line separators; do not widen the managed engine
or add a second MIME registry. Existing byte hashes and unrelated codecs stay
unchanged.

Ideas maps authoritative archived status to `archived`; current eligible packet
to `ready-to-plan`; real review-needed workflow or stale historical packet to
`review-needed`; remaining incomplete/unsaved workflow to `in-progress`.
`not_ready` follows those actual step states, never a guessed status. Source or
counter drift (`stale_source`, `stale_revision`, `stale_draft_version`,
`stale_backlog`, `historical_handoff`) requires explicit Review/reload.
Capacity refusal leaves the prior row and buffer intact with its named error;
it never creates Ready to plan. Integrity errors fail the read, not a fabricated
In progress row. Shared Service/CLI checks call these pure helpers; they do not
reimplement eligibility.

## Verification

Pure codec/eligibility tests precede links, Store crash/replay tests, actual
Service/Owner HTTP and CLI registration checks, then browser controls. Include
Unicode/space paths, missing/moved workspace, missing/changed source/packet/blob,
symlink/special-file refusal, unchanged accepted counters, receipt capacity,
foreign same-path bytes, lost response, source drift and configured validator
failure/success. Native platforms, attended clipboard/picker/accessibility and
final release review remain separate qualification gates.
