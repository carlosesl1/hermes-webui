# Occurrence integrity and explicit tool outcomes

This patch conservatively preserves equal-text messages unless their occurrence provenance matches. Distinct IDs, timestamps, and separate rows without identity must survive. The internal unidentified-object key is transient; it is never persisted or substituted for a durable ID. Stable message IDs, private state row IDs, tool-call IDs, and active-user turn tokens provide occurrence evidence.

Replay/display comparisons retain provider `api_content` sidecars. In particular, the visible-key fuzzy guard compares the complete identity/sidecar suffix, not just the newly inserted occurrence field.

Tool failures are classified from structured JSON/mappings and explicit callback metadata, not prose containing the word `error`. Dedicated and legacy callbacks propagate `is_error` into SSE, live recovery snapshots, and the existing journal. Gateway cancellation/interruption is terminal and failed.

## Verification and release caveat

The first serialized, credential-free neighboring run stopped after five failures: **33 passed, 5 failed**. Log: `/data/apps/hermes-webui-fork-release/upstream-fixes/integrity-q7ctjp8x/pytest.log`.

Three old context-dedup expectations contradicted the new contract. Their tests now retain distinct unidentifiable/timestamped occurrences, while the actual duplicate fixture supplies matching explicit IDs. No tests were disabled.

**Unresolved compatibility gate:** `test_next_webui_turn_context_includes_state_db_external_messages` and `test_state_db_delta_after_context_allows_recovered_turn_prefix` still rely on legacy content-only sidecar/state.db alignment. The conservative implementation retains those ambiguous rows, so these expectations failed. A durable provenance bridge is needed before treating this as release-ready; do not restore text-only deletion to make them green.

A second serialized run adds real-class callback tests (constructor metadata and signatures are exercised) verifying JSON failure vs successful JSON mentioning `error`, live snapshot, and journal. Its log is `/data/apps/hermes-webui-fork-release/upstream-fixes/integrity-hruk22o9/pytest.log`; at documentation time it is queued behind other lanes on the shared flock. Results must be read before approval. Selected neighbors: stable IDs, callback event contracts, gateway backend, and provider `api_content` replay. The modified context-dedup file also needs a final rerun.

No production, provider-network, push, Hermes core, or other-lane payload changes are included.
