# Asset storage contract

Author: an implementer. CP3 Linux source implementation contract; native-host qualification pending.

The authoritative idea detail owns append-only `extensions.glitch_idea_assets` links `{record_id,path,sha256}`. These point to immutable YAML-frontmatter Markdown at `assets/evidence/<canonical-record-sha256>.md`. Records describe upload intents, completed assets and accepted design-set membership. They are evidence, not a second live idea registry.

Streams use generated `assets/staging/<upload_id>.<nonce>.part`; complete bytes publish immutably at `assets/blobs/<asset_id>.bin`, all on the Store filesystem. Upload display names never select a path. Every path component, root and ancestor must be a real directory/regular file, never a symlink, special file or foreign-device path. The browser selects opaque IDs only.

An intent contains schema_version 1, kind upload-intent, upload_id, asset_id, idea_id, session_id, source_revision, name, declared_type, size, actor and timestamp. A completion contains schema_version 1, kind asset, asset_id, upload_id, idea_id, session_id, source_revision, blob_path, name, declared_type, validated_type, size, sha256, actor and timestamp. A set contains schema_version 1, kind design-set, set_id, idea_id, session_id, source_revision, source_digest, members `{asset_id,name,type,size,sha256}`, actor and timestamp. The exact design-set source witness is fixed below; no undeclared source fields may be added. Every Markdown body describes the record safely as literal text.

An upload intent and its request receipt publish together. Network streaming never holds a Store lock. Flush and publish the immutable blob first; then rehash and register complete evidence/detail links and receipt atomically through Store's recoverable transaction. A crash before registration leaves an orphan, never a completed upload. Completed stages are sealed read-only (0440) before exclusive hardlink blob publication. A retained stage whose generated upload ID and verified read-only inode match the completed blob is a retained success, excluded from orphan diagnostics. Failed, interrupted or unmatched stages and unlinked blobs remain reported orphans; no automatic deletion. Replays return the original receipt before business/CAS checks. Same identity with different bytes refuses. Linked missing/corrupt evidence or blobs fail closed.

`visual-set/accept` receives explicit unique asset_ids (1–20) with design_set_id null for construction, or an existing design_set_id for reacceptance. Creation snapshots member names/types/sizes/hashes and capture/shape source revision/digest; acceptance publishes that set and the accepted pointer in one receipt transaction. At most 25 MiB/file and 100 MiB/set. Never infer a set from all uploaded files. Replacing a set preserves every earlier record/blob. Generic Visualize acceptance applies the same verification. Skip/not-applicable requires a nonempty reason and null design_set_id.

Supported inert formats: PNG, JPEG, WebP, PDF, SVG, HTML, CSS, JSON, Markdown/text, ZIP. Extension, declared type and basic signature must agree. Only validated raster formats may preview; active formats, PDF and ZIP download as inert attachments. No extraction, execution or malware-scanning claim.

The authenticated state projects bounded verified uploads/historical sets for resume and reports omission explicitly. Original Capture attachments are never automatically accepted design assets. Uploading does not increment accepted idea revision or complete Visualize. Native Windows/macOS and remote Orca-client upload remain later qualification gates.

Design-set `source` is exactly `{capture:{revision,digest},shape:{revision,digest}}`. Each revision is the corresponding accepted step receipt revision (at most source_revision); each digest is workflow source_digest(step, revision, {step: accepted_fields}). The set source_digest is SHA-256 of compact sorted UTF-8 JSON `{operation:"visualize-assets",source:<witness>}`. It deliberately excludes overall idea source_revision: unrelated Priorities/Method changes must not stale unchanged Capture/Shape. Original source_revision stays immutable. Construction requires both current accepted Capture and Shape; a compact witness avoids duplicating raw text.

The reserved append-only asset link inventory is capped at 256 records per idea.
Capacity exhaustion is an explicit refusal and never evicts immutable history.
Authenticated state `asset_inventory.orphans={count,ids}` reports unlinked
stages/blobs across the authenticated Store, including other ideas. These physical
diagnostics are Store-wide even in a selected idea inventory; linked records are
specific to that idea. ids is bounded to 128 generated opaque IDs and count includes all
observed orphans, excluding the verified retained successes described above. Excess names are omitted explicitly by count. Linked inventory
is supplied memory-only inside transactions; the Visualize handler alone verifies
set acceptance, using already hash-checked Store records. Local filesystem
verification may hold the Store lock; network streams never do. CP3 product
verification measures observed local verification/acceptance latency.

## Atomic publication witnesses

Every linked asset evidence record requires its exact
`{record_id,path,sha256}` in the result's `asset_records` of a durable publication
receipt for the same idea. Every record kind, including design sets, requires a
typed session_id naming its exact publishing session. Store reads that session
directly; unrelated recovery files cannot supply or poison this witness. Candidate
records must name the active request session. This supersedes the unreleased
sessionless design-set schema; no migration or global session search is provided.
Verified publication receipt bytes are protected by transaction CAS alongside
evidence and blob hashes. Missing or mismatched witnesses fail closed; a canonical
offline-added set is not an authenticated publication. This introduces no second
registry and no filesystem work in pure handlers.

The physical reserved asset inventory (evidence, blobs and staging names) is capped
at 4096 files. Store checks a fresh confined inventory union candidate immutable
evidence before publication, for intents, completions and sets alike. Successful
retained stages still consume physical capacity once each. Capacity refusal
publishes no new evidence or receipt and leaves the existing Store readable.
Inventory access inside reducers remains memory-only; retained-stage classification
belongs to Store's bounded filesystem verification boundary. No cache or second
registry is introduced. Filesystem writers able to chmod or replace sealed blobs
are outside the immutability guarantee; linked bytes are rehashed and corruption
fails closed. Product latency qualification remains an actual measurement gate.


## CP3 admission and archive policy

Author: an implementer. Stage admission recounts the confined physical inventory and
creates the exclusive stage under one Store lock with a conservative
three-name admission check; no reservation is held while streaming. It releases that lock before every body read. Blob publication
recounts and admits its new physical name under another Store lock; the
evidence transaction independently admits its immutable files. Another request
cannot drive the inventory beyond the cap between count and allocation.
Missing descriptor sealing support refuses before stage allocation; native
Windows/macOS storage qualification remains pending. Completed byte replay
hashes the supplied body without allocating a stage or consuming capacity.

Archived ideas refuse new uploads and every new existing-idea acceptance except
explicit Shape: Capture, Priorities, Method, Visualize and Assess all require
Shape first. Old receipts and files remain readable; initial new Capture is allowed.
Explicit Shape acceptance, including unchanged answers, creates an active next
revision without changing archived
plan, revision or asset bytes. Retained overlap stages are counted, not reclaimed.
Guided Store repair is outside CP3 (a later change); there is no automatic cleanup.

Literal JSON frontmatter escapes YAML-nonprintable C1/DEL/noncharacters and
Unicode line separators. Exact metadata and canonical record-address hashing
remain unchanged; ordinary Unicode/non-BMP names remain literal. Existing
immutable evidence is never rewritten by this serializer.


Earlier unreleased encoders could produce literal C1/noncharacter bytes that
were already rejected by the Markdown/YAML parser. The canonical escape change
does not promise backward readability for such invalid historical bytes; there
is no migration or automatic rewriting. CP1/CP2 had no asset evidence. Ordinary
valid Unicode encoding and canonical record IDs remain compatible.
