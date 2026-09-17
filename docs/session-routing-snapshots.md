# Session routing and toolset snapshots

## Provider ownership

Session display, ordinary chat start, and server-initiated turns use the same
catalog-backed provider repair. Display is read-only. Repair requires a known
public provider, an unchanged model/provider pair, and one catalog owner.
When the old provider group is absent, only a literal model ID (or its exact
provider-qualified wrapper) establishes ownership. Cached minimal catalogs are
marked incomplete and never authorize repair; discovery errors and ambiguous
ownership also preserve the stored choice.

Local, custom, plugin/unknown providers and configured custom endpoints are not
reassigned based on catalog absence, including absence from a returned local
inventory. A matching persisted explicit-pick signature also wins over inference.
This deliberately tightens the older local-provider repair behavior: positive
repair regressions now use a public builtin provider instead of Ollama.

Context metadata uses the selected provider's endpoint, with caller-supplied
runtime endpoints taking precedence. A foreign or unlabelled global endpoint
is not inherited by an explicitly selected provider. Matching provider and
custom endpoint context overrides remain supported.

## Toolset changes

`POST /api/session/toolsets` holds the session agent mutation lock, reloads the
session, and invalidates `tool_names` and `system_prompt_hash` in the session
owner's `state.db` in one SQLite transaction before saving the new toolsets.
It does not use the active-profile read fallback and does not touch other rows.
Invalid profile names and foreign owners in isolated mode fail closed. Missing
DBs/rows and old schemas without snapshot fields have no snapshot to invalidate.
An existing DB is opened read/write without permission to create a replacement.

Database or sidecar-save exceptions return 503 and restore the previous in-memory
toolset selection. Invalidation intentionally commits first: if sidecar saving
fails or the process crashes between stores, the old settings remain safe with
an absent cache, rather than new settings retaining stale capability metadata.
The SQLite and JSON stores are not one atomic transaction. Retrying is safe.

## Scope and limits

This is WebUI-only. It does not modify Hermes core, hot-swap tools into an
already-running agent, or migrate all sessions in bulk. The session lock is
process-local; independent core/gateway writers do not share it. Validation uses
isolated temporary profile DBs, injected failure paths, and the supported
credential-free test runner with external networking blocked; it is not a live
provider or production smoke test.
