# Markdown authority format v2

The codec, recoverable publication and core Markdown Store, external-edit
import, legacy migration and the browser flow (Capture through Review) are
implemented.

`IDEAS.md` plus one `idea_<uuid32>.md` per idea are current authority. There is
no parallel permanent JSON idea database. Every newly generated authority and
history document starts at byte zero with `---`, one YAML mapping, closing
`---`, and Markdown. UTF-8 is required; a BOM is refused. LF and CRLF input
frontmatter delimiters are accepted. Successful writes canonicalize YAML to LF.
Immutable text is encoded as exact Unicode strings with escaped line endings:
its UTF-8 bytes, CRLF, Unicode, whitespace and trailing newlines round-trip.

## Ownership and schemas

Every envelope rejects unknown fields, incorrect scalar types and unknown
schema versions. Custom application metadata belongs only in `extensions`, a
bounded mapping of string keys and JSON-compatible values. Browser writes
preserve it, including nested values. Extensions do not override domain fields.
The index key `extensions.glitch_idea_migration` is reserved for migration
evidence: exactly `legacy_sha256`, `receipt_path`, and `frozen_path`. It points
to the frozen conversion source and receipt; it is not a second idea authority.
Do not edit or remove that marker. Migrated reads verify its linked records;
later writes preserve it and recheck the observed evidence hashes.

| Document | Exact frontmatter envelope | Ownership |
| --- | --- | --- |
| Index | `schema_version: 2`, `kind: index`, `order`, `backlog_revision`, `transaction_revision`, `placements`, `extensions` | Accepted ID order and backlog revision; the score/rank table is derived |
| Detail | `schema_version: 2`, `kind: idea`, `idea`, `history`, `metadata_evidence`, `transaction_revision`, `extensions` | Current structured idea fields and browser workflow/drafts; Notes is editable prose |
| Revision | `schema_version: 2`, `kind: history`, `idea_id`, `origin`, `snapshot` | Immutable exact origin and full accepted revision evidence |
| Placement | `schema_version: 2`, `kind: placement`, `placement` | Immutable accepted placement evidence linked by index |
| Metadata | `schema_version: 2`, `kind: metadata`, `idea_id`, `record_type`, `record` | Immutable proposal, plan-link or execution metadata, including additions that do not change accepted idea revision |

`order` is a unique list of permanent `idea_<32 lowercase hex>` IDs. Revisions
are nonnegative integers, never booleans; accepted idea revisions start at 1.
History links have exactly `path` and lowercase SHA-256 `sha256`. Per-idea paths
are `history/<idea-id>/rN.md`, with sequential N starting at 1. Placement links
are `history/backlog/rN.md`, with N a sequential evidence number, not necessarily
the backlog counter (captures also advance that counter). Linked bytes and
identity must match. Missing or modified evidence is an error.

Detail `metadata_evidence` has exactly `proposals`, `plans`, `executions`, each
an ordered list of `{path, sha256}` links with one link per corresponding detail
record. Empty lists are required even when the idea has no such records. A
metadata envelope's `record_type` is `proposal`, `plan` or `execution` and its
`record` is the exact corresponding detail record; plans omit `content`.
The path is `history/<idea-id>/metadata/<digest>.md`. Its filename digest is
SHA-256 over UTF-8 JSON of `{idea_id,record_type,record}`, with sorted keys,
compact separators, Unicode retained and nonfinite numbers forbidden. The link's
`sha256` separately covers the entire Markdown document. The decoder checks
both hashes, envelope identity/type and ordered equality with detail records,
including when inspecting supported external edits with `check_body=False`.
Changing metadata does not rewrite accepted revision history or immutable origin.
Store must enumerate the reserved metadata directory and require its file set
to match the linked set; this detects inventory mismatches and orphan evidence.
It cannot detect coordinated removal of every corresponding record and file.
The codec verifies supplied links, not the filesystem's full inventory.

Format v2 reserves `extensions.glitch_idea_handoffs` for append-only immutable packet
links, implemented and defined in `handoff-format.md`. Its `kind:
handoff` envelope shares the existing metadata directory but has a NEW digest
rule: the filename and link SHA-256 both cover the complete canonical Markdown
bytes. Do not call legacy `metadata_path`/`decode_metadata` for packets or add
handoffs to the exact legacy `metadata_evidence` keys. The typed packet decoder
handles only explicit handoff links. Store's expected file set includes all
legacy metadata, agent proposal and handoff links, with collision/orphan refusal.
Older binaries without packet support fail closed when presented with packet links; they are
not a rollback path. Existing links/bytes and generic Notes/extensions remain
unchanged on stores without packets.

Detail `transaction_revision` is a nonnegative integer, never a boolean, and
does not appear in the generated body. A changed detail can carry the newest
transaction counter while `IDEAS.md` remains byte-identical. State decoding
recovers the maximum of the index and detail counters. With previous Documents,
state encoding preserves an unchanged detail's old counter; new or semantically
changed details get the state counter. Store still decides which after-images
to publish and owns monotonic transaction advancement.

`idea` has exactly `idea_id`, `revision`, `status`, `origin`, `shape`, `ratings`,
`assessments`, `proposals`, `plans`, `executions`, with optional `workflow`.
Status is `active` or `archived`. Origin has exactly `text`, `sha256`, `actor`,
`timestamp`. Its hash covers exact UTF-8 text; every history envelope repeats
this immutable origin, separately from the unchanged legacy snapshot. Changing
both origin text and its hash in detail still fails the history comparison.

Current shape uses the existing nine-field domain schema and vocabulary.
Ratings require `urgency`, `importance` (integers 1–10) and `actor`; legacy
`timestamp` is optional. Assessments require the existing seven input fields
plus the computed `score`; legacy `assessment_id`, `actor`, `timestamp` metadata
are optional. Nonfinite scores and a score inconsistent with the validator are
refused. An unknown partial score remains null; Kano is categorical. Current
workflow uses the separately validated v2 schema from `idea_workflow.py`,
including its draft version, draft fields, accepted receipts and dependencies.
Legacy ideas omit workflow; reading them invents no acceptance.

Revision `snapshot` has exactly the existing `revision`, `shape`, `ratings`,
`assessments`, `actor`, `action`, `timestamp`. A workflow snapshot additionally
has `schema_version: 2` and `workflow`; legacy snapshots retain their original
shape. Bulk revisions are never embedded into current detail frontmatter.

Plan links retain the domain `plan_id`, `idea_id`, `idea_revision`, absolute
`path`, absolute `source_path`, `sha256`, `actor`, `timestamp`, `validation`.
Their exact accepted `content` is read from existing
`plan-evidence/<plan-id>.md`, not duplicated in frontmatter. Migration preserves
these existing bytes; it does not wrap a previously accepted plan in new YAML
or change its hash. Execution links retain their original domain fields and
receipt. Proposals retain their linked revision/assessment snapshot. These
links remain subject to Store's full cross-record validation and actual file
checks; the codec does not replace that application validation.
Proposals, plan links and executions additionally require the immutable metadata
evidence above; frontmatter alone is insufficient to trust their attribution or
validation during an external edit.

Archive domain snapshots are reconstructed from plans and linked revision
history. Existing archive files remain retained evidence; the codec neither
moves nor deletes them.

## Editable prose and conflict handling

Detail bodies visibly show original wording, current details and revision
links. The only editable body region is between these exact LF markers:

```text
<!-- glitch-idea:notes:start -->
freeform Notes (exact line endings and trailing whitespace preserved)
<!-- glitch-idea:notes:end -->
```

The markers must appear exactly once, the closing marker ends the body, and
Notes cannot include reserved markers. The codec concatenates Notes directly
between markers; it adds no newline to Notes. A note without a final newline
therefore immediately precedes the closing marker. Do not normalize the whole
file with universal-newline reading; pass bytes to the codec.

The external-edit contract supports current shape inputs, the two human rating
values, assessment inputs/reasoning, and source fields of already accepted
workflow steps. It does not turn a file edit into browser acceptance. Imported
workflow inputs require review; the observer is recorded, not an invented editor.
Editing both a legacy field and its browser equivalent to contradictory values
is a conflict. Assessment scores are derived from inputs, not manually assigned.

Keep workflow drafts, navigation, counters, acceptance receipts and invalidation
records under application control. Placement, visual-set and handoff references
require their normal evidence checks; editing a reference cannot accept one.
Index order and its table are not part of the manual-edit input contract.
Transaction/backlog counters are validated persistence metadata, not tamper-proof
evidence: arbitrary changes to those counters across a restart cannot be
distinguished from prior application writes. Immutable revision and acceptance
evidence provides the separate checks for accepted decisions.

Pause the browser, edit supported current frontmatter fields or Notes, then
resume. Store must first validate immutable history and compare against the
last accepted baseline, then validate candidate changes and record a new
revision with dependency invalidation and external-file provenance. Notes-only
changes do not inherently create an accepted revision. Do not edit origin,
identity, hashes, history, plan links or generated summaries/tables. A changed
generated body yields `generated_body_conflict` and requires an explicit repair
decision; ordinary writing never discards it.

YAML comments are readable but prevent rewriting (`yaml_comments`). Move them
into Notes before resuming a write. Detection uses PyYAML token spans and block
scalar headers: quoted `#`, `abc#literal`, and `#` inside block content are text,
while an actual inline/comment-only/header comment is protected. Parsing never
modifies the original file. YAML formatting itself is canonicalized on a
successful write; preserving arbitrary YAML presentation is not promised.

Arbitrary external editors do not participate in the application lock. Store
must hash-check affected bytes and refuse observed conflicts. Filesystem readers
can briefly observe mixed generations during a recoverable publication; product
readers must lock/recover before serving state. The codec alone supplies no
concurrency or durability guarantee.

## Bounded real YAML

Use PyYAML **6.0.3**, MIT, through a SafeLoader subclass and deterministic
SafeDumper. No object constructors or homegrown YAML parser. Refuse duplicate
keys, anchors/aliases, merge keys, unsupported tags/types, non-string keys,
nonfinite values and extra YAML document markers. Disable implicit timestamp
coercion: `2026-10-01` is text. Schemas check actual types; quoted `"true"` never
becomes a boolean or revision.

Limits per document: 64 MiB UTF-8, 8 MiB frontmatter, depth 32, 100,000 tree
nodes, 400,000 scanned tokens. Construction counts/depth-checks nodes before
recursing. Output also rejects unsupported types, cycles through bounded
traversal, invalid Unicode and nonfinite values. Domain text/numeric bounds
still apply. Read files with a bounded byte read before passing them here.

The package pins `PyYAML==6.0.3` in `glitch-idea/requirements.txt` and includes
`glitch-idea/PyYAML-MIT-NOTICE.txt`. Use the documented per-installation private
runtime; this codec performs no download, installation or global environment
mutation.

## Integration API

- `parse_document(raw: bytes) -> Document(metadata, body, has_comments)`.
- `encode_document(metadata, body='', previous=None) -> bytes`; a previous
  document protects YAML comments. Generic encoding alone validates no domain
  envelope or generated-body policy.
- `encode_detail(idea, notes='', extensions=None, history_links=None,
  previous=None, previous_baseline=None, transaction_revision=None) -> bytes` and
  `decode_detail(raw, check_body=True) -> Document`.
  An unspecified counter preserves the previous detail counter or defaults to
  zero for standalone detail encoding. Metadata links are generated from records.
- `detail_notes(document, baseline=None) -> str`. The explicit baseline is
  accepted detail **metadata**, not edited metadata. This permits proving the
  old generated summary was untouched while inspecting frontmatter edits.
- `encode_history(idea_id, snapshot, origin=...) -> bytes` and
  `decode_history(raw) -> Document`.
- `metadata_path(idea_id, record_type, record) -> confined relative path`,
  `encode_metadata(idea_id, record_type, record) -> bytes`, and
  `decode_metadata(raw, path=None) -> Document`. Supply the linked path on
  decode to verify filename identity and content digest. Plan records must
  omit `content`; the accepted plan bytes retain their original separate path.
- `encode_index(state, extensions=None, previous=None, previous_state=None)`
  and `decode_index(raw, state=None)`. Previous index rewriting requires the
  accepted previous state and checks its original table.
- `encode_state(state, notes=None, extensions=None, previous=None,
  previous_state=None) -> {relative_path: bytes}` and
  `decode_state(files, check_body=True) -> existing domain state`.

`previous` for state encoding maps relative paths to parsed Documents.
`notes` and `extensions` are keyed by idea ID, with index extensions keyed by
`IDEAS.md`. Preserve old Notes/extensions by supplying previous Documents. For
external edits, inspect with `check_body=False`, validate bodies/Notes against
the explicit accepted baseline, then pass that baseline as `previous_state`
when publishing the accepted imported state. The relaxed read is a candidate,
never an already accepted revision. Before trusting the baseline, Store must
validate it against immutable history and validate all cross-record links.

State encoding is a pure after-image generator. It includes index transaction
revision for legacy compatibility. Store owns selecting affected publication
paths: a draft/Notes-only change must not rewrite `IDEAS.md` when its displayed
data and accepted order are unchanged. Store also owns CAS, receipts,
transaction recovery and migration; no filesystem I/O occurs in this module.

Concrete small files live in `tests/fixtures/markdown/`; they contain clearly
synthetic text and permanent fixture IDs, not a shipped example backlog.

## Durable request results

`Store.create_session()` creates a new random `session_<uuid32>` and its empty
`session-recovery/<session-id>.json`. It accepts no supplied session ID, so a
missing old file is never silently recreated by a retry. A fresh store publishes
an empty `IDEAS.md` with transaction counter zero in the same journal. Browser
credentials are separate private service state; this ID grants no authorization.

The receipt envelope is exactly `{schema_version: 1, session_id, receipts}`.
Each request key maps to `{payload_sha256, result}`. IDs permit 1–128 ASCII
letters, digits, dots, underscores, colons or hyphens. The digest covers sorted,
compact UTF-8 JSON of the validated business payload; that payload is not copied
into the receipt. Results are bounded JSON with typed success/counter metadata.
The service must exclude transport credentials. A top-level reserved credential
field is rejected; ordinary nested domain data is not a secret scanner's input.

`Store.mutate(session_id, request_id, validated_payload, mutator)` records the
result and domain after-images in one recoverable transaction. The trusted
internal callback only changes state and returns business data; it performs no
external effects. Identical retries return the recorded result before callback
revision checks. Different payloads under the same key raise `request_conflict`.
Receipt-only no-ops leave all domain counters and existing index/detail bytes
unchanged. `Store.request_result(...)` reconciles a lost response after recovery.
An uncertain write remains explicit; the recovered receipt records its success.

Each session permits 128 receipts and 1 MiB encoded JSON, without eviction.
Capacity is checked before calling the mutator; a prospective oversized result
also refuses publication. Create a fresh session after exhaustion. Missing
session files raise `receipt_session_missing`; missing keys in an existing
session raise `request_not_found`. Malformed saved records fail closed as
`corrupt_receipts`, and observed file changes cause a save conflict. Session
recovery data is not an alternative idea database.

## Legacy conversion

Legacy `list`/`show` reads keep JSON authority unchanged. Opening a writable
transaction, rejecting a mutation, or committing an unchanged legacy state does
not convert it. The first successful change publishes the complete Markdown
state, a strict conversion receipt, and the exact original bytes at
`migration-recovery/v1-state.json`, then removes only the original `state.json`
through the same prepared journal. Explicit receipt-session creation can also
convert without changing any domain counter. IDs, history, ordering, placement,
plan and execution metadata remain intact; no wizard acceptance is invented.

Existing accepted-plan and archive bytes must match their legacy evidence.
Missing or changed views require explicit repair before conversion. The adapter
verifies complete before/after domain reconstruction before publication. The
old initialized-store check then refuses a missing `state.json`; an old helper
cannot silently establish competing authority. Both live formats are ambiguous
outside recovery of the actual prepared transaction.

The reserved index pointer identifies the frozen source and receipt. Migrated
reads validate both; subsequent writes recheck observed hashes. Later Markdown
changes never overwrite the frozen source or receipt. Recovery rolls forward
only through verified before/after images; conflicting edits or missing stages
are preserved and reported. The frozen JSON is evidence, not an automatic
rollback over newer ideas. These checks do not claim protection against an
editor deliberately rewriting or removing every related record together.

## Recovery ownership

`repair-views` may recreate a missing derived archive JSON view from validated
authority. Immutable Markdown history/metadata and frozen `plan-evidence` bytes
are authority themselves. A missing one fails closed; the original working plan
may have changed or moved, so it is not a reconstruction source. Restore exact
bytes from a verified backup. Differing existing evidence is never overwritten.
This supersedes v1 JSON's ability to regenerate frozen plans from duplicate
embedded content.
