# Bounded done settlement

`done.session` on the native live stream, its durable journal/replay, Gateway
success, and native self-heal success is a **render projection**, marked
`_settlement_window: "tail_v1"`. It is not full-history or regeneration authority.

- `message_count` is the full canonical raw-row count.
- `_messages_offset` is the absolute index of the first returned raw row.
- `_messages_truncated` and `has_more` mean older rows exist; `_msg_limit_max` retains the
  session endpoint's pagination ceiling.
- At most 30 raw rows and 30 candidate legacy tool cards are selected before
  copying/redaction. Prose/tool text uses the existing field, row and shared-page
  budgets. Large inline data is replaced only in this projection.
- Embedded activity-scene indexes stay absolute. Legacy card indexes are absolute
  on the wire and rebased to the browser's merged window.
- `_content_truncated`, `_preview_content_truncated`, and `_full_content_url`
  retain the existing explicit full-transcript/long-answer disclosure contract.
- Canonical messages, model context, persistence, full/export and synchronous
  chat are unchanged. No regeneration hash or full-history reconciliation runs
  while building this done projection. A shallow Session view prevents compact
  metadata's user-count scan from traversing the canonical history; no partial
  user count is advertised.

The browser merges overlapping windows by absolute coordinates, preserves an
already-loaded prefix and full prose, and retains the existing scroll-preserving
settlement renderer. If a long turn creates a gap, it fetches only missing cursor
pages before adopting the tail. A failed gap fetch retains the loaded reading
range and exposes the full-transcript action rather than discarding history.
Navigation generation and stream ownership are rechecked after each asynchronous
page and again before done touches the DOM. Recovery-control slots remain in the
raw message array (the renderer hides them), so subsequent tail coordinates do
not drift. Loaded prose/block/tool detail survives repeated previews, newer
loaded rows survive older replay tails, and stale regeneration authority is
removed until an explicit full load refreshes it.

## Boundaries

These are text/row budgets, not an adversarial JSON structure-size cap. Large
nested arrays/identity metadata can still be expensive. Explicit full expansion
is intentionally unbounded and opt-in. Existing error/cancel payloads and old
journals retain their prior format; replaying an old journal does not rewrite it.
This change does not optimize Session.save, context reconstruction, or the
existing GET pagination reconciliation. Provider/live deployment performance is
not certified by the synthetic tests. Scroll-preserving rendering is retained;
desktop/mobile browser geometry is not certified by the helper/Node tests.

## Local verification

`./scripts/test.sh tests/test_bounded_done_settlement.py
 tests/test_streaming_done_payload_message_count.py tests/test_bounded_full_content.py
 tests/test_bounded_history_cost.py tests/test_messages_stale_stream_source_scope.py -q --timeout=30`
(with Node on PATH) covers real compact, all three production done projection
expressions, journal round-trip, canonical full preservation, forbidden history
prefix iteration, inline-data budgets, absolute coordinates, repeated previews,
legacy cards, cursor gaps, malformed pages, and async navigation/stream changes.
Worker/provider execution and full export routes remain integration-suite work.

Contract routing: runtime consistency, stable assistant anchors, render previews,
and journal replay. The new marker intentionally replaces the old assumption
that every successful done embeds a full transcript. Clients must not treat
absence of `regeneration_revision` as full-history edit authority.
