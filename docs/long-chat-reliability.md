# Long-chat reliability and resource budgets

## Contract routing

This change affects browser settlement/presentation and read-only journal replay.
It preserves the canonical transcript, provider context, occurrence identity, and
existing stable-assistant-turn ownership. Relevant contracts: `CONTRACTS.md`,
`rfcs/live-to-final-assistant-replies.md`,
`rfcs/stable-assistant-turn-anchors.md`, and
`rfcs/webui-run-state-consistency-contract.md`.

## Final replies must not depend on older-history availability

A bounded `done.session` tail can start after the currently loaded browser range,
especially after a tool-heavy turn. Previously the done handler waited for every
missing page. If that read failed, the merger retained only the old range and
silently omitted the authoritative final answer, even though the turn was marked
complete. Reloading then recovered the persisted answer.

Gap hydration now has one 1,500 ms total deadline, at most three 30-row page
requests, no retries, and abort/late-response guards. If hydration succeeds, the
existing contiguous merge and reading position are retained. Otherwise the pane
adopts the authoritative tail with its real raw offset and total count. Existing
older-history pagination remains available. Disjoint ranges are never concatenated
with invented coordinates; stale prefix-pagination requests are invalidated.

This fallback changes only the browser window. It does not delete older messages,
rewrite the sidecar, truncate model context, or replace the final answer with a
progress update. A reader scrolled into the discarded range can lose that visual
anchor on fallback; no forced bottom-scroll is added for an unpinned reader.
Navigation-generation and stream ownership checks reject late A → B → A work.

## Page continuity with large previews

A page and its separately returned boundary can have different preview lengths,
because shared character budgets reserve prose fairly across all rows. Comparing
those previews literally incorrectly rejects unchanged history. The first row of
each bounded projection now carries additive `_paging_identity` metadata computed
from the original eight fields used by the existing continuity check. Both paging
and settlement share that projector. The client compares valid versioned digests,
falling back to the existing strict comparison for legacy payloads.

Only the first selected row is fingerprinted, with string leaves processed in
64-Ki-character chunks; neither the full history prefix nor a second giant JSON
buffer is traversed/allocated. Time remains proportional to this one original
boundary row. Identity, tool changes, and same-length edits beyond both previews
are rejected. This digest is read-only continuity evidence, NOT edit authority,
a regeneration revision, an authentication token, or occurrence deduplication.
Canonical messages are not annotated or mutated. Old already-persisted previews
without a fingerprint retain the legacy fail-closed behavior.

## Rendering caches

- Long messages use the compact key only to locate a candidate. Full-source
  equality is required before reusing rendered HTML, preventing equal-length
  templates with different middle content from displaying the wrong answer.
- LRU eviction removes cold entries instead of clearing the whole cache.
- The cache holds at most 300 entries and 8 MiB of conservatively accounted
  UTF-16 key/source/HTML payloads. A larger single answer is rendered intact but
  bypasses caching. This is not a browser-wide heap or DOM budget.
- Stable virtual-height synchronization returns before allocating metadata for
  every loaded row. Replacement/prepend invalidation remains unchanged.

## Journal replay

Cursor filtering now occurs during incremental UTF-8 line decoding. The reader
uses one file descriptor and its initial length, retains only selected events,
and preserves malformed-row diagnostics and legacy LF/CRLF/CR line semantics.
Concurrent appends appear on the next observation; atomic replacement cannot mix
two descriptors. This does not provide snapshot isolation for arbitrary in-place
rewrites of an append-only journal.

Memory is proportional to read chunks, the largest logical row, returned events,
and retained diagnostics, rather than every discarded event payload. CPU and I/O
remain O(file size); cold summaries and multi-run traversal are not newly indexed.

## Reproduction and verification

Use the supported repository runner with isolated HOME/Hermes/WebUI state:

```sh
./scripts/test.sh tests/test_long_chat_live_settlement_gap.py \
  tests/test_live_settlement_tail_pagination.py \
  tests/test_bounded_done_settlement.py \
  tests/test_long_chat_rendering_cache.py \
  tests/test_long_chat_resources_journal.py \
  tests/test_projected_boundary_pagination.py
npm run lint:runtime
```

With an existing Playwright/Chromium installation:

```sh
python tests/browser_long_chat_settlement.py
LONG_CHAT_PROJECTED=1 python tests/browser_long_chat_settlement.py
python tests/browser_long_chat_rendering.py
python tests/browser_conversation_lifecycle.py
```

The first browser script exercises the actual EventSource listener, production
renderer, and CSS with offline HTTP fixtures at 1440×900 and 522×1232.
`LONG_CHAT_PROJECTED=1` additionally uses real backend previews and clicks the
visible older-history control after a failed gap read. The rendering
script checks 400 loaded rows, bounded DOM, cache collisions, focus, and reader
anchors at desktop and narrow widths. Neither makes a provider call. The lifecycle
gate separately runs the real isolated WebUI server with a deterministic Gateway.
`LONG_CHAT_BASELINE=<pre-fix commit>` runs the offline browser regressions against
the old affected asset; these runs must fail before the fix.

A five-trial synthetic replay measurement (1,200 events, 8,192 payload characters,
approximately 10 MB journal) reduced median traced allocation from 21,816,415 bytes
to 241,757 bytes for the last two events, with identical output and unchanged
canonical SHA-256. This is a reader microbenchmark, not a production chat-open
latency or whole-process memory claim.

## Follow-up work, not claimed as shipped

1. Measure cold sidecar parsing separately from HTTP window size. A 30-row response
   does not prove that a large JSON sidecar avoided full parsing. Evaluate a
   revision-aware, indexed read projection without migrating canonical history
   until concurrency, freshness, and compatibility are covered.
2. Profile the temporary full render-window expansion during settlement with
   very large histories before changing stable-worklog/scroll contracts.
3. Test interrupted networks, hidden/mobile tabs, multiple tabs, server restart,
   and real-provider long-running turns in a release canary.
4. Extend UX coverage for searching unloaded history and navigating back to a
   discarded prefix after a settlement fallback.

No runtime restart, deployment, schema migration, retention deletion, or provider
configuration change is required by this source patch. Deployment is a separate
release step and must preserve any installation-specific overlays.
