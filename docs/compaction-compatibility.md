# Compaction replay compatibility

## State and invariants

The WebUI shim changes only display provenance on a newly created standalone
user replay at the core compactor boundary. It does not remove or rewrite model
context, change text or provider payloads, migrate stored history, or patch core
classes. `display_kind` must remain absent on ordinary replay rows: cores can
interpret a nonempty display kind as non-actionable and lose the unfinished task
on a later compression. Provenance uses
`display_metadata.webui_compaction_replay = {version: 1, source_message_uid?: ...}`.

The per-instance `_reappend_inflight_user_task` hook is capability-checked, not
selected by a claimed core version. Its inspected signature must start with the
explicit `compressed` and `inflight` parameters. Positional-only, keyword-only,
optional trailing parameters and variadic extensions are forwarded unchanged.
Unknown/uninspectable signatures, additional required parameters, async and
generator producers are left untouched. Original returns and exceptions are
preserved, including invalid-call exceptions.

A supported result contains a nonempty original prefix in the same object order
plus exactly one fresh user dictionary. Returning a newly allocated list is
supported only with those same prefix objects. Copied/reordered prefixes,
reused prefix rows, the in-flight object itself, merges, multiple appends and
unrelated display kinds/metadata are not stamped. Equal text is never a reason
to hide another human occurrence. Witness references live only during the call.

## Entry points

Use `prepare_agent_compaction_provenance(agent)` immediately before every local
run, after selecting or constructing the agent. It returns a boolean capability
result and is idempotent for cached agents; replacing a compressor is rechecked.
The native streaming worker and the synchronous `/api/chat` route use this
helper before `run_conversation`. No shared core-class monkeypatch is installed.

Gateway chat executes core compaction remotely. Both Runs API and chat-completions
paths share a bridge that reports `gateway_compaction_provenance_capability()` as
`"unknown"`; this is not evidence that remote provenance is absent or supported.
No remote instance was contacted to infer compatibility.

## Safe degradation and diagnostics

Warnings use only these fixed reason codes, each at most once per process:

- `missing_hook`: no callable producer is available.
- `read_only_hook`: the instance does not allow assignment of the hook.
- `signature_incompatible`: an unknown signature or non-instance target.
- `remote_capability_unknown`: Gateway coverage cannot be established locally.

A lock-protected fixed-size integer bit mask bounds suppression state. No
instance/session/profile registry, task content, UID, path, exception text or
credentials are retained or logged. Hook installation proves only availability;
each result still has to satisfy the object-witness rules. A missing warning
is not certification of another installation, worker, or core revision.

The legacy fallback remains a read-only display projection: it requires the
exact core replay envelope, a nonempty identical logical UID, a nonconflicting
original user witness, and matching original text (with the narrowly supported
workspace/preserved-notice transformations). Header-only quotations, ambiguous
witnesses and unknown shapes stay visible. Raw paging coordinates and canonical
model history are not filtered or rewritten.

## Verification scope

`tests/test_compaction_provenance.py` and `tests/test_compaction_portability.py`
exercise actual helper behavior with offline core doubles: identity, optional
arguments, incompatible signatures, exceptions, concurrent bounded diagnostics,
read-only instances and new-list witnesses. `tests/test_compaction_replay_visibility.py`
covers display/model separation and conservative legacy projection. These are
compatibility-contract tests, not live provider, remote Gateway, fresh-install
or cross-OS certification. Real-core persistence and successive compression
probes require a separately isolated runtime and must not boot a user's core
implicitly.
