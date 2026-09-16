# Background activity and continuation delivery

## Contract routing

This change extends the **Live-to-Final Assistant Replies**, **Stable Assistant
Turn Anchors**, and process completion delivery contracts. It changes browser
presentation and deferred notification batching, not canonical transcript roles,
model-context messages, or authorization.

## User-facing behavior

**Intentional presentation contract correction:** formerly a wakeup also owned
all subsequent assistant continuations until the next human turn. Ownership now
covers only the trusted notification itself. This prevents collapsing the
principal deliverable, without guessing which assistant text is useful.

- Only trusted `process_wakeup` notifications appear inside collapsed
  **Execution activity** disclosures. Principal assistant prose, progress and
  final answers always remain outside, live and settled, including after reload.
- Consecutive notifications for the same human turn share a disclosure only
  within an uninterrupted rendered segment. Every nonmember transcript row
  (including a visible assistant answer) is a hard ordering boundary, as is a
  virtual spacer. Later notifications never move ahead of that answer.
- Expanding reveals the original notification commands/output and provenance.
  No canonical message is deleted, rewritten or classified as "unimportant" by
  a prose-matching heuristic. Failure status remains visible in the summary.
- Legacy delegation/terminal wakeups use this same notification-only projection;
  joined delegation tool results use the ordinary assistant/tool renderer, not
  synthetic user turns. This frontend change does not alter producer delivery.
- Approvals/clarifications and focused content must remain reachable. Expansion
  survives ordinary redraws in the tab. Reload
  restores the transcript with groups collapsed by default.
- Only explicit `process_wakeup` provenance classifies a notification. Legacy
  source-less messages stay visible as ordinary messages, even when their text
  mentions a known task handle: a human can paste exactly the same envelope.
- Virtualized histories use the same quiet activity model. A spacer is a hard
  ordering boundary: separate rendered windows receive separate disclosures,
  never move rows across spacers. Virtual height measurements allocate the
  actual disclosure height across its rendered canonical entries instead of
  caching full hidden-row heights or multiplying height by message count. Native
  toggles trigger the existing bounded virtualizer refresh. Fractional positive
  heights initialize the measurement cache even below its update tolerance.
  Disclosure state uses canonical ownership, while DOM fragments use canonical
  row identity: adding/removing a spacer cannot reset expansion or focus, and
  split fragments remain separate across the spacer.
- Keyed summaries survive reconciliation and keyboard focus. Nested approval/
  clarify insertions expand their group; busy-state changes refresh its label.

## Intentional contract change: deferred events

**Old rule:** after a busy turn ended, start one continuation for the first
completion and requeue the rest, producing a chain of responses.

**New rule:** one idle-hook continuation receives a bounded group of pending
completions. It is asked to continue unfinished work or report a consolidated
new outcome, not acknowledge each notification. There is still at most one
session-owned turn; a concurrent human turn uses the existing admission guard.

The batch limit is 16 entries and approximately 64 KiB including the header.
Events are indivisible: overflow is queued before dispatch, and an individual
oversized event travels alone without truncation. A stable digest of IDs and
payloads is the retry identity. Busy/credential-paused admission, transient
server errors and dispatch exceptions retain the entire batch for a later
turn/recovery hook, without introducing a timer retry loop. This does not promise that a model will never produce an acknowledgement;
notification grouping is deterministic and does not depend on model compliance.
Any assistant acknowledgement remains ordinary visible assistant content; the
frontend must not hide it to simulate a single synthesis.

A queued retry with a server-generated `wakeup-batch-` identity is indivisible:
it is dispatched unchanged and is never combined with overflow, another batch,
or newly arrived completions. Entries preceding it may form a separate batch;
entries after it remain queued. This preserves FIFO order of the current queue
and the exact retry identity without nesting envelopes or resetting their counts.
A 409 can already append the rejected batch after queued overflow; this fix does
not change that existing queue policy or promise original-arrival FIFO across
rejected admissions. A matching header in ordinary tool output is not batch
provenance.

The completion queue and older-core process registry have existing lifetime
limits. This batching patch does not introduce a new durable queue or promise
that a killed OS process resumes after server restart. `/background` task
tracking is a separate mechanism with its own persistence contract.

## Verification

- `tests/browser_background_live.py`: actual projection-source DOM probes for
  focus, nested approvals, settled status, human provenance and virtual spacers.
- `tests/test_background_activity_groups.py`: explicit provenance,
  human-turn ownership, no transcript mutation, failure classification.
- `tests/test_background_wakeup_batching.py`: one continuation for siblings,
  byte/event budget and lossless overflow.
- `tests/test_background_batch_retry.py`: full-batch rejection plus overflow,
  repeated retry identity, first/middle/last/sibling batch barriers and
  preservation across busy, paused, server-error and exception outcomes.
- `tests/test_wakeup_defer_race.py`: actual idle-hook dispatch and admission
  races, now asserting the new bounded group contract.
- `tests/browser_background_activity.py`: actual session import/load API,
  real DOM rendering, native keyboard disclosure, redraw, persistence read-back
  and reload at desktop/narrow/mobile sizes. No inference is performed.

Use the project runner for Python tests: `./scripts/test.sh <tests> -q`.
For the browser gate, install the documented browser-test dependencies and run
`python tests/browser_background_activity.py` with `PLAYWRIGHT_BROWSERS_PATH`
and optionally `BACKGROUND_ACTIVITY_ARTIFACT_DIR` configured for the test host.
All browser state and test sessions are temporary; never point tests at real
user state.
Production acceptance also requires real terminal completion/watch and asynchronous
delegation events: verify authoritative provenance and collapsed activity before
and after reload. Synthetic imports alone cannot certify producer integration.
