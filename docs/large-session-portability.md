# Large-session rendering and manual-compression admission

## State ownership

These guards change response projections and manual-compression admission, not
canonical transcript storage. They do not deduplicate equal text or change raw
row coordinates. Repeated user/assistant turns remain separate occurrences.
The existing manual compressor may replace `context_messages` after a successful
request, but render previews must never become model input or persisted history.

## GET /api/session

- A legacy request without `msg_limit` or `render_preview=1` still returns all
  rows and complete content (subject to existing redaction).
- `render_preview=1` opts into bounded content without implicitly limiting rows.
  `_messages_truncated` describes missing rows, **not** shortened content.
  `_content_truncated` marks affected rows; `_preview_content_truncated` says
  whether their visible conversation prose was shortened.
- `msg_limit` keeps the existing visible-row pagination contract and previews.
- `content_full=1` explicitly requests complete content and overrides
  `render_preview`, `msg_limit`, and `msg_before`. `messages=0` remains metadata-only.
  `_full_content_url` points to this explicit complete-transcript route.
- Browser refresh and reloads larger than the pagination ceiling request preview
  content while retaining all rows. Explicit load-all/export requests use
  `content_full=1`.

Text budgets remain 8,192 characters per field (4,096 for tool-row payloads),
32,768 per row, and 262,144 shared between message and tool-card payloads.
Opaque nonidentity metadata strings and URL/inline-data fields over 65,536
characters are omitted in previews and marked as truncated. Normal attachment
URLs remain intact. Identity/shape fields, including `message_uid` and keys ending
in `_id`, `_uid`, or `_key`, remain exact even above that threshold. Replacing
large IDs with empty strings or prefixes could collapse legitimate occurrences.

These are character/payload protections, **not a total JSON byte or heap limit**:
row/container counts, dictionary keys, identity fields, ordinary metadata below
the opaque threshold, and session-level metadata are not covered by the shared
text budget. Complete exports intentionally remain potentially large. Loading
and reconciling a sidecar may still require parsing the full canonical file.

## Manual compression

The default admission limit is **2,097,152 characters**, counting string leaves
only in the existing provider-facing `_API_SAFE_MSG_KEYS` projection of visible
messages. This includes nested multimodal content, tool-call arguments, and
provider reasoning; it excludes display-only attachments and metadata. It is a
conservative pre-sanitizer bound: fields in rows that the sanitizer would later
skip still count. The guard uses lengths, not token estimates or serialized JSON.

Set `HERMES_WEBUI_COMPRESS_MAX_CHARS` to a positive integer to change this local
operator policy. Empty, malformed, zero, and negative settings use the default;
there is no accidental unlimited mode. Raising it trades resource protection for
larger requests and does not raise any provider context limit.

An over-limit request returns HTTP 413 **before** deep-copying the transcript,
sanitation, token estimation, runtime/provider setup, or compressor invocation.
It makes no compression save or context mutation. Export or a focused continuation
remain available; the system does not silently shorten model input.

For admitted requests, the visible transcript and pending stream state are copied
under the per-session agent lock. The compressor runs outside that lock. Before
committing, the handler rechecks the stream state and the entire visible snapshot,
including nested display-only metadata. A changed snapshot returns HTTP 409,
leaving concurrent edits and previous context intact. This is an in-process
cooperating-writer check, not a cross-process filesystem transaction or universal
session revision/CAS protocol.

Successful responses retain every visible-history raw row and tool-card index,
with bounded render-only content and explicit full-content links. The canonical
visible transcript and compressor output stay complete. Small responses retain
their existing row shape; compression does not add paging-boundary identities.

## Verification scope

`tests/test_portable_render_guards.py` exercises the real GET/compress handlers
with synthetic sessions and an offline fake compressor: legacy/full/preview
contracts, opaque data versus distinct giant IDs, repeated turns, admission
ordering, configurable limits, nested concurrent edits, persisted-history
immutability, and bounded responses versus full model context.

Run through `./scripts/test.sh` with isolated HOME, HERMES_HOME, WebUI/test state,
and a stub agent source directory. No provider inference or private data is
required. This does not certify browser layout, arbitrary core versions, fresh
installation, or another OS; those remain separate portability gates.
