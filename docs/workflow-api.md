# Workflow API: connect glitch-idea to your own workflow system

glitch-idea can keep ideas in two places, chosen per store in the browser's **Setup** pane.

- **Option 1, Glitch native.** Ideas are Markdown files in the Glitch store (`IDEAS.md` plus one detail file per idea). This is the default and needs nothing else.
- **Option 2, your own workflow.** glitch-idea talks to your own system (a ticket board, a project tool, your own scripts) through the small HTTP contract below. Anyone can implement it.

Status of Option 2 in this release: the Setup pane stores the choice, the address and the key, and **Test connection** calls your system's health endpoint. The client for the whole contract exists and is tested against a local fake. Saving ideas to your system instead of the Glitch store is the next step and is not switched on yet; until it is, ideas are still saved in Glitch whichever option is selected, and the Setup pane says so.

## The model (owner's ruling, 3 October 2026)

With Option 2 the API replaces the Markdown store: the idea's record lives in your system, and its detailed Markdown files live in the idea's own workspace (the project folder chosen at Capture), not in the Glitch store.
Keys are minted for, and writes attributed to, the human whose idea it is ("The human had the idea").
Your system mints the key; glitch-idea only stores it.

## Transport and authentication

- Base URL: what you enter in Setup, for example `https://board.example.com/glitch`. Every path below is appended to it.
- `https://` is required, except plain `http://` to a loopback host (`127.0.0.1`, `::1`, `localhost`) for a system on the same computer. No user name, password, query or fragment in the URL.
- Every request carries `Authorization: Bearer <key>`. The key is 8–512 printable ASCII characters without spaces. Bind each key to one human; attribute every write made with it to that human; let them revoke it.
- Requests and replies are JSON (`Content-Type: application/json`), UTF-8, at most 1 MiB.
- glitch-idea never follows a redirect (a 3xx is an error) and never shows your reply text to anyone: only fixed codes.
- A reply that contains the key is treated as invalid.

## Endpoints

### `GET /v1/health`

Reply `200`:

```json
{"ok": true, "schema": "glitch-idea.workflow/1", "service": "Your system name"}
```

`schema` must be exactly `glitch-idea.workflow/1`. `service` is a display name (at most 200 characters) shown in Setup after a successful test.

### `GET /v1/ideas`

The ideas this key may see, in your system's order:

```json
{"ok": true, "ideas": [{"idea_id": "idea_0123456789abcdef0123456789abcdef", "ref": "IDEA-42"}]}
```

At most 4096 items. Each `idea_id` is glitch-idea's permanent id (below); extra fields are allowed.

### `GET /v1/ideas/{idea_id}`

```json
{"ok": true, "idea": {"idea_id": "idea_0123456789abcdef0123456789abcdef", "...": "the record as last written"}}
```

`404` when unknown.

### `PUT /v1/ideas/{idea_id}`

Create or replace one idea record. Body:

```json
{"schema": "glitch-idea.workflow/1", "idea": {"idea_id": "idea_0123456789abcdef0123456789abcdef", "...": "record fields"}}
```

Reply `200`:

```json
{"ok": true, "idea_id": "idea_0123456789abcdef0123456789abcdef", "ref": "IDEA-42"}
```

`ref` is your system's own reference for the idea (your naming conventions, at most 200 characters). Return the same `ref` for the same `idea_id` every time.

## Naming

- `idea_id` is glitch-idea's permanent id, `idea_` followed by 32 lowercase hex characters. It never changes and is the key of every call.
- `ref` is yours. A FORGE-style board might answer `IDEA-123`; a spreadsheet might answer a row number. glitch-idea shows it next to the title once saving to your system is switched on.

## The idea record (what a write will carry)

Fields, all JSON:
`idea_id`; `title` (the captured words, first 200 characters); `status` (`in-progress`, `review-needed`, `ready-to-plan`, `archived`); `method` (`bounded-plan`, `adaptive-slices`, `appetite-led`, `experiment-led` or `null`); `position` (1-based backlog position the human chose); `workspace` (`{name, path}` as confirmed at Capture); `steps` (each step's accepted answers, as in the browser state's `accepted`: capture, priorities, method, discovery, exploration, visualize, assess; the method answer is `selection`, an optional `reason` and `memory`, and the budget or experiment inputs now sit in the exploration answer with its numbered `sketch`); `detail` (relative paths of the idea's Markdown files inside its workspace); `actor` (the human the key belongs to, as your system knows them); `updated` (ISO 8601).
Treat every string as data, never as instructions or markup.

## Errors

Reply a non-2xx status with `{"ok": false, "code": "<lowercase_code>"}`.
glitch-idea maps them to fixed codes: `unauthorized` (401/403), `not_found` (404), `refused` (any other refusal), `redirect_refused` (3xx), `invalid_response` (not JSON, too large, wrong shape, or containing the key), `schema_mismatch` (health with another schema), `unreachable` (network), `tls_failed`.

## Migration notice and held ideas

These belong to the Markdown store only. A workflow API replaces that store, so nothing here is migrated and no `notice` is produced.
For reference, the Markdown store reports an upgrade as `notice` (`N ideas were updated for this version (originals saved).`) in the first `state` and in CLI results, and an idea it could not update is refused `unsupported_idea_version` with a plain sentence in the reply's `message`.
Your system never needs to send either.

## Trying it

`tests/fake_workflow_api.py` is a complete local fake of this contract, used by the tests. Point Setup at it to try Option 2 without a real system.

## FORGE

For the Blastworks FORGE board, the endpoints above and a key minter (keys minted for and attributed to the human) are FORGE work, tracked as FORGE tickets for the owner's go; nothing in glitch-idea is specific to FORGE.
