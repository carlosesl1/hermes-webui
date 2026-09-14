# Bounded history rendering

The affected state layer is the HTTP/browser display projection, not persisted
session messages, the state database, or model context. No storage migration is
required.

## Paging contract

Older-history requests use a fixed visible-row limit and `msg_before` from the
first older load, rather than repeatedly transferring a growing tail. With
`msg_boundary=1`, pages retain intervening hidden rows through the cursor so
absolute message indices and legacy tool-card coordinates remain contiguous.
The response includes the first row after the cursor as `_messages_boundary`;
the browser rejects a changed boundary and asks the reader to reload. Local
session-switch and wholesale-replacement generation guards also remain active.

The selector walks backward only as far as needed and collects trailing tool
call IDs only from the selected window. This is not a claim that the whole HTTP
route is constant-cost: reconciliation/cache misses, summary scans, tool-summary
filtering, hidden-row runs and cold storage reads can still depend on history
size. Exact duplicate rows lacking distinct IDs/timestamps are not durable
revision tokens.

## Preview and full-content contract

Paginated responses clip text fields recursively, including tool arguments,
reasoning and hydrated activity scenes. Budgets are 8,192 characters per field
(4,096 for tool rows), 32,768 per row, and a shared 262,144 for messages plus
legacy tool summaries. Markers/JSON structure add overhead. Identity fields,
URLs and embedded image data remain intact; this is not a strict byte limit or
an arbitrary-JSON structural cap.

Clipped rows and tool summaries carry `_content_truncated` and
`_full_content_url`. That authenticated URL returns the full merged transcript
JSON without preview clipping. It retains normal public redaction. The browser
also exposes `expandFullTranscript()` for a renderer's explicit full-content
control. It reloads the complete transcript and re-renders; it is intentionally
not a background fetch. `_ensureAllMessagesLoaded()` detects clipped content
even when every history row has already been loaded, including session-level
tool-only previews. Renderer integrations must expose the full-content action
for both message text and nested/tool-only clipping, not merely expand a local
clipped string. Markdown download currently serializes the loaded browser
transcript; do not present that as a lossless export of previews.

## Verification

Run through `./scripts/test.sh` with isolated agent and WebUI state:

- `tests/test_bounded_history_cost.py`
- `tests/test_bounded_history_pagination.py`
- `tests/test_bounded_full_content.py`
- neighboring session-tail, renderable-window, reconciliation and race tests.

A deterministic 50,000-row / 30-visible-row fixture with 200,000 text characters
per row compared baseline `94fd2da` to this selector/projection:

| Metric | Baseline | Bounded |
| --- | ---: | ---: |
| Accesses to source rows (including copies) | 50,000 | 61 |
| JSON message payload bytes | 6,001,140 | 251,880 |
| First returned absolute index | 49,970 | 49,970 |

The source-access counter does not count work on baseline-created plain-list
copies. These are selector/payload measurements, not end-to-end latency claims.
