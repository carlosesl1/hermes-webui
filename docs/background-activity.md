# Background activity and continuation delivery

## Contract routing

This change extends the **Live-to-Final Assistant Replies**, **Stable Assistant
Turn Anchors**, and process completion delivery contracts. It changes browser
presentation and deferred notification batching, not canonical transcript roles,
model-context messages, or authorization.

## User-facing behavior

- A trusted `process_wakeup` notification and the assistant continuation it
  causes appear inside a collapsed **Background updates** disclosure.
- Consecutive updates for the same human turn share one disclosure. The preceding
  main answer stays outside it, as does the next human question and its answer.
- Expanding reveals original commands, output, tool activity and assistant
  results. No answer is deleted, rewritten or classified as "unimportant" by a
  prose-matching heuristic. Failure status remains visible in the summary.
- Live updates use the same group; approvals/clarifications and focused content
  must remain reachable. Expansion survives ordinary redraws in the tab. Reload
  restores the transcript with groups collapsed by default.
- Modern metadata is authoritative. A legacy untagged notification is grouped
  only if its exact envelope references a registry handle already present in
  tool output in the loaded transcript. An arbitrary user quote alone is not
  a notification. This fallback is display-only, not a source/permission stamp.

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
payloads is the retry identity, so the existing 409 path retains the entire
batch. This does not promise that a model will never produce an acknowledgement;
the browser grouping is deterministic and does not depend on model compliance.

The completion queue and older-core process registry have existing lifetime
limits. This batching patch does not introduce a new durable queue or promise
that a killed OS process resumes after server restart. `/background` task
tracking is a separate mechanism with its own persistence contract.

## Verification

- `tests/test_background_activity_groups.py`: provenance, legacy evidence,
  human-turn ownership, no transcript mutation, failure classification.
- `tests/test_background_wakeup_batching.py`: one continuation for siblings,
  byte/event budget and lossless overflow.
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
