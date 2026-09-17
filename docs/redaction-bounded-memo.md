# Bounded response redaction memo

The public projection still deep-copies JSON and strips replay aliases at the
same authoritative schema positions. The private `_owned` traversal only runs
on fresh schema-scrubber copies; no response dictionaries or lists are cached.

## Safety and scope

- Do not memoize the combined agent/plugin redactor. It may read environment
  credentials, profile state or mutable rules on every call. Only the local
  immutable fallback and immutable prefilter snapshots own memo scopes.
- Scope tokens are opaque `object()` instances, checked by identity, retained
  alongside the entry, and included in its key. Arbitrary rule graphs, callable
  closures, string subclasses and mutable outputs are ineligible.
- The keyed BLAKE2 digest is not an equality oracle: compare the exact retained
  input on every hit, even if the digest collides. Keys contain no plaintext;
  input strings in values remain sensitive in-memory data and must not be logged.
- The retention limit is 8 MiB, at most 4,096 entries, and at most 1 MiB charged
  per entry. Charge Python string sizes (including wide Unicode), output, scope,
  digest key, conservative entry overhead, and the measured OrderedDict backing
  allocation. Evict after insertion under a lock, including mapping high-water
  allocation after churn. Fixed empty memo/lock/salt and transient transform or
  encoding allocations are not an RSS limit and are excluded from retention.
- Rotate the prefilter scope on immutable rule-object replacement. Mutable
  marker collections and custom search objects bypass persistent memoization.
  Cheap delimiter guards apply only to the original built-in regex objects;
  arbitrary replacement regexes must actually run. Preserve Python `lower()`,
  Unicode digit regex behavior, and surrogate-safe input hashing.
- Transform work runs outside the memo lock. Concurrent misses may duplicate
  computation but cannot return a different input's or scope's cached result.

## Verified results

Supported isolated runner: **122 passed, 1 skipped in 45.13 seconds**, seven
selected files / 123 collected cases. The skip is agent-dependent: the runner
reported that hermes-agent was unavailable. The synthetic module/environment
rotation regression passed, but this run does not claim integration coverage
against a real installed agent redactor.

The serialized-runner benchmark measured **14.084 ms uncached versus 2.754 ms
warm (5.11x)** for **672,038 input characters** / 24 messages. It retained 27
entries with **690,425 charged entry bytes + 2,608 mapping bytes**. All measured
projections were equal. Two earlier standalone probes during unrelated runner
load varied from 0.74x to 4.66x; wall-clock results are workload/load-dependent,
not a promised latency improvement for every response. A separate sanitized
invocation also passed all 18 non-benchmark bounded-memo regression cases.

Runner log (outside the repository):
`/data/apps/hermes-webui-fork-release/upstream-fixes/redact-epe68m1j/pytest.log`.

## Verification

Run the supported isolated test runner against:

```
tests/test_redaction_bounded_memo.py
tests/test_security_redaction.py
tests/test_issue5204_redactor_memoization_contract.py
tests/test_raster_data_uri_redaction.py
tests/test_session_summary_redaction.py
tests/test_issue4662_sidebar_redaction_read_once.py
tests/test_issue6757_redaction_and_runner_sse_fixes.py
```

The bounded-memo suite includes identity impostors, forced digest collisions,
concurrent eviction, retained-byte accounting, oversized input/output, live
synthetic environment-key rotation, mutable output rejection, custom regex and
mutable-search changes, Unicode/isolated-surrogate equivalence, and independent
public projections with redaction both enabled and disabled.

Its synthetic benchmark compares five median warm projections with five
projections that bypass only the pure memos. It asserts output equality on each
iteration, uses 24 messages with large clean content and synthetic credentials,
and reports aggregate timing, character count and memory charges only. This is
not an end-to-end production latency claim or a comparison with the former
unsafe combined-redactor LRU for small credential-heavy payloads.
