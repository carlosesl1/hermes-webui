# Long-chat reliability and resource budgets

## Contract routing

This change affects browser settlement/presentation, read-only journal replay,
and cold session decoding/reconciliation.
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

## Duplicate-occurrence prevention

The internal Agent replay projection preserves `message_uid` and `timestamp`,
including the UID returned for the exact token/index-owned current-user checkpoint
in eager and deferred save modes. These fields are distinct from WebUI stable IDs
and physical SQLite row IDs. Row snapshots, persistence flags and write authority
are not carried back to the Agent. Direct-provider sanitization still strips
internal fields; the real-core compatibility probe also exercises the core's own
wire-field stripper.

Both state.db readers retain optional durable UIDs, with legacy-schema fallback.
Reconciliation keeps conflicting UIDs as distinct occurrences and permits physical
row churn when the durable UID agrees. Proven cross-source matches can enrich a
legacy sidecar with its UID. It never deduplicates an entire conversation by text,
overrides a conflicting checkpoint UID, or removes an existing duplicate as a
repair operation. Equal user/assistant text in separate occurrences remains valid.

Gap hydration now validates the right boundary of every page plus the join with
any retained local prefix. Missing or incompatible boundaries reject the whole
bridge; no partially fetched page mutates the incoming authoritative tail. Exact
adjacency requires another overlapping page to prove the retained-prefix join,
within the unchanged three-page/1,500-ms budget. Thus an ambiguous join may fall
back more conservatively, preserving older-history access rather than displaying
an overlapping user/assistant pair. Paging and settlement share the same canonical
fingerprint/strict-legacy comparator.

When a string-content final preview is genuinely clipped, settlement excludes the
owning stream's last post-tool token accumulator from Worklog only when its text
matches the preview prefix. Pre-tool narration and foreign-stream rows survive.
Ambiguous structured/multi-block previews are not newly suppressed. This prevents
one full streamed answer appearing in activity above its clipped final copy; it
does not change canonical answer storage or grant truncation metadata authority
to delete another occurrence.

### Duplicate regression gates

```sh
./scripts/test.sh tests/test_agent_occurrence_roundtrip.py \
  tests/test_duplicate_settlement_boundaries.py
python tests/browser_duplicate_settlement.py
# Same assertions must fail against the pre-fix frontend:
DUPLICATE_BASELINE=704cb8c3 python tests/browser_duplicate_settlement.py
# Optional installed-core compatibility, using its compatible Python/dependencies:
HERMES_OCCURRENCE_CORE_DIR=/path/to/hermes-agent \
  python tests/probe_agent_occurrence_core.py
```

The optional core probe establishes temporary HOME/config/SQLite before importing
any runtime, disables lazy installation, and runs real compaction and incremental
flush followed by WebUI reconciliation and save/reload. Under isolated `-I -S`,
`HERMES_OCCURRENCE_DEPENDENCIES` can name existing ABI-compatible dependency paths.
It does not call a provider or run a complete model conversation.

The Chromium fixture uses real scripts, renderer, native EventSource listeners and
backend preview projection at 1440×900 and 522×1232. APIs/boot are offline fixtures;
its clipped-final case supplies a controlled live-scene projection at `done`,
not a claim that every provider/reconnect assembles that state. It checks duplicate
counts in both session state and settled DOM, preserved narration, final visibility,
console errors and overflow, with nonzero exit on failures. `DUPLICATE_EVIDENCE`
selects its screenshots/results location. The separate real-server lifecycle and
cold-loading gates cover persistence/reload and actual older-page interaction.

Existing persisted duplicates require a separate backed-up provenance-based repair.
This source change neither migrates nor removes real history. Production model
latency spikes and additional network/reconnect canaries remain follow-up work.

## Compaction replay is context, not a new human turn

Some core versions restore an unfinished task by creating a user-role replay with
`[STILL IN PROGRESS ...]`, the original logical UID, and no original timestamp.
Append-only reconciliation can legitimately append that derived row after the
final response. A tall replay then pushes the retained final out of the viewport;
retention in state or the DOM does not establish that the reader can see it.

WebUI installs an idempotent instance-local hook on the compressor's
`_reappend_inflight_user_task` before a conversation runs. Only a newly created
standalone user row at this trusted boundary is annotated. Existing carriers,
original tasks, class methods, content, role and UID are not rewritten. Unsupported
core interfaces degrade without guessing by prefix.

The durable annotation is `display_metadata.webui_compaction_replay`, version 1,
with an optional `source_message_uid`. It intentionally does **not** set
`display_kind`: the compatible core treats any nonempty kind as non-actionable,
which would lose the unfinished task at the next compression. Internal Agent
history retains the annotation; direct-provider and core wire projections strip
it while retaining the replay text and user role. Test the replay as the sole
surviving actionable task, not just next to an unwrapped original.

Full and regeneration-tail SQLite readers preserve optional display metadata with
legacy-schema fallback. Content-equal cross-source reconciliation retains explicit
provenance, but sharing a UID alone never labels the original human message.
Newly settled display history excludes proven replays. Session GET instead applies
a read-only projection and keeps all raw rows/indices; the browser and renderable
window selector ignore the annotation. Pagination, edit and regeneration offsets
continue to address canonical data. Context and persisted history are not migrated.

For legacy text replays without provenance, classification requires the exact
core envelope, a nonempty shared UID, a nonconflicting original human witness and
matching task text; only the runtime workspace prefix and recognized preserved
notices are tolerated. Unidentified, conflicting, unfamiliar and multimodal legacy
rows remain visible. Human quotations with their own UID and legitimate repeated
requests remain visible. This is not global text deduplication or blanket hiding.

### Compaction visibility regression gates

```sh
./scripts/test.sh tests/test_compaction_provenance.py \
  tests/test_compaction_replay_visibility.py
# Use an existing Playwright-compatible Python/browser pair:
COMPACTION_EVIDENCE=/path/to/output python tests/browser_compaction_replay_visibility.py
# Expected nonzero: old UI + the same unclassified legacy payload:
COMPACTION_BASELINE=fefaaf89c91ff79ca7f924955ee28735dfafd7d8 \
  COMPACTION_EVIDENCE=/path/to/baseline python tests/browser_compaction_replay_visibility.py
```

The Chromium fixture uses backend-generated synthetic reconciliation/window
payloads, 800 initial rows, real static assets and native EventSource listeners.
It exercises done, refresh, older-page interaction, reload and terminal replay at
1440×900 and 522×1232, checking a unique visible final, literal human quotations,
raw coordinates, overflow and console errors without scrolling to the answer.
Boot/CDN and APIs are controlled fixtures, not real-provider/network-timing E2E.
The separate lifecycle fixture starts an isolated HTTP server and deterministic
Gateway. `tests/probe_compaction_replay_core.py` exercises the actual installed
core replay method, temporary SessionDB compaction, incremental flush, wire
stripping, WebUI readers and save/reload with text/multimodal tasks and successive
compressions. Follow that file's strict pre-launch sandbox/ABI instructions;
there is no provider inference, real profile read or live core modification.

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

## Cold session loading

A small HTTP tail does not avoid loading a large native sidecar. Profiling a
synthetic sidecar plus state.db showed repeated comparison-text normalization in
reconciliation was more expensive than JSON parsing. The append-only merger now
shares normalized content between its comparison keys within ONE invocation.
The bounded display GET also shares pure normalization work between its older-prefix
proof and append-only reconciliation, keyed by exact input text and normalization
mode, not temporary row-object IDs. Equal text never reuses occurrence decisions.
The request memo is cleared before projection/metadata/serialization, including
display-cache hits. The cache is not attached to a Session, shared between requests, keyed
by file size/timestamp, or reused after an edit. Literal workspace tags in the
sidecar and protocol prefixes in state.db retain their different semantics;
provider sidecars and occurrence/truncation rules are unchanged.

`Session.load` releases its binary snapshot before parsing the decoded JSON and
releases decoded text before reconciliation. Encoding, surrogate handling, and
malformed-input exception behavior match `json.loads(bytes)`, including BOMs.
The original snapshot save signature and atomic-replacement checks are retained.
This lowers temporary allocation; it is NOT partial-file parsing, constant-memory
history loading, a schema migration, or a persisted history cache.

### Initial cold-load measurement

One five-trial synthetic comparison against the previous implementation used
3,000 messages, a 33,179,079-byte sidecar and a synthetic SQLite database. The same
script/interpreter ran against both checkouts. Application caches were cleared
per trial; the OS page cache was not. Canonical sidecar and output SHA-256 values
were identical before and after.

| Case | Median before | Median after |
| --- | ---: | ---: |
| Tail with matching older prefix | 1.7214 s | 1.2556 s |
| Older page with matching prefix | 2.1820 s | 1.2690 s |
| Tail requiring older-prefix reconciliation | 2.8825 s | 1.6701 s |
| Older page requiring older-prefix reconciliation | 2.5059 s | 1.0418 s |
| `Session.load` peak traced allocation | 100,444,581 bytes | 67,266,295 bytes |

The allocation reduction was 33.03%. Timings varied between trials on a shared
host; these are observed handler medians, not a guaranteed production speedup,
end-to-end browser latency, disk-cold benchmark or whole-process RSS limit.

`tests/test_cold_chat_loading_cost.py` enforces normalization count and allocation
cost without clock thresholds. It also checks JSON compatibility, edited-input
freshness, and 500 deterministically seeded comparisons to the uncached semantics.
Neighboring gates cover occurrences, provider `api_content`, state.db recovery,
save identity/concurrent replacement, truncation, and compression.

`tests/browser_cold_chat_loading.py` starts a real isolated WebUI with a persisted
3,000-message fixture, opens the 30-row tail, clicks older-history pagination,
navigates A → B → back to A, and reloads at 1440×900 and 522×1232. It checks the
saved answer, coordinates, horizontal overflow, uncaught JavaScript errors and
unchanged canonical bytes. This is persisted-history browser coverage, not a live
provider or in-flight session-switch race test.

## Profile and history latency follow-up

The follow-up removes repeated pure text normalization in a display request and
redundant skill-tree stat probes. It does not skip authoritative reconciliation,
increase cache TTLs, cache profile rows for longer, change model context, or edit
persisted history. Profile warm readers still probe outside the recompute lock;
only a cold/changed/expired recompute is serialized per profile. Uncontended misses
reuse their pre-compute observation; waiters re-probe after acquiring the lock.
Directory traversal retains nested/symlink/support-pruning/error semantics.

Against the duplicate-fix baseline `e8d1c6e4`, five interleaved A/B pairs on the same
3,000-row / 33,179,079-byte fixture produced these handler medians:

| Case | Before | After | Reduction |
| --- | ---: | ---: | ---: |
| Matching-prefix tail | 1.116 s | 0.605 s | 45.8% |
| Matching-prefix older page | 0.968 s | 0.663 s | 31.5% |
| Changed-prefix tail | 1.595 s | 0.897 s | 43.8% |
| Changed-prefix older page | 0.972 s | 0.691 s | 28.9% |

Process CPU medians fell 24.9–38.0% in those four cases. A separate three-trial
tracemalloc run measured 11.5–20.7% lower complete-handler peak allocation;
matching-prefix tail fell from 101,918,913 to 81,021,878 bytes. `Session.load`
allocation itself was unchanged in this follow-up. All canonical/output hashes
matched. No timing threshold is imposed in CI; shared-host variance remains
material (one patched changed-prefix older-page sample was 2.136 s despite its
0.691 s median). Traced time is not mixed into untraced latency results.

A separate seven-trial synthetic profile fixture (four profiles, 120 skills each,
explicit core-helper doubles) measured cold stats/listing at 617.4 → 462.8 ms and
row refresh at 52.2 → 35.5 ms. The deterministic reduction is eight to four tree
probes on an uncontended cold listing, plus fewer duplicate directory stats.
The cold profile still parses its skill/config metadata; this is not a claim
that an observed production 8-second spike has been eliminated. Separate actual-core
validation used disposable profile homes and verified counts, metadata, deep edits,
additions, config invalidation, isolation, and warm no-reparse behavior.

`test_history_normalization_reuse.py` checks once-per-exact-text work, edited inputs,
request memo release, and 500 differential cases against uncached semantics.
`test_profile_list_latency.py` checks invalidation, traversal parity, cold concurrent
compute sharing, error unlock, and concurrent warm probes with a barrier rather
than timing assertions. The targeted integration gate passed 1,296 tests with 34
skips both on product source and on a disposable composition with deployment
overlays. Skips include unavailable optional core modules, fixture module cleanup,
and the opt-in benchmark; this is not full-suite certification. Chromium cold-load,
duplicate settlement, real-server deterministic lifecycle, and temporary real-core
compaction/flush gates also passed. No live model call or production restart was
used to validate this follow-up; deployed latency needs a separate release check.

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

1. Cold-load decoding and normalization costs have been reduced, but the full
   sidecar still gets parsed. Evaluate a revision-aware, indexed read projection
   without migrating canonical history until concurrency, freshness, and
   compatibility are covered.
2. Profile the temporary full render-window expansion during settlement with
   very large histories before changing stable-worklog/scroll contracts.
3. Test interrupted networks, hidden/mobile tabs, multiple tabs, server restart,
   and real-provider long-running turns in a release canary.
4. Extend UX coverage for searching unloaded history and navigating back to a
   discarded prefix after a settlement fallback.

No runtime restart, deployment, schema migration, retention deletion, or provider
configuration change is required by this source patch. Deployment is a separate
release step and must preserve any installation-specific overlays.
