# Inactive native chat auto-archive

`auto_archive_days` is an installation-wide WebUI setting: integer `0` (the
safe default) disables it; integers `1`–`3650` enable inactivity archival.
The same policy covers native WebUI chats in every profile in the shared
session directory. Changing the policy takes effect on the next worker tick.
Disabling it does not automatically restore previously archived chats.

The server starts a daemon worker only after binding its HTTP socket. Its first
sweep and subsequent sweeps wait 60 seconds using an interruptible Event. Each
tick examines at most 25 directory entries, with a one-second scheduling budget
checked between candidates and a persistent directory cursor for fairness.
Disabled ticks do not scan the session directory. GET/list paths do not invoke
this worker. Shutdown signals the worker and waits up to two seconds.

A chat is eligible only when both real `created_at` and `updated_at` are valid
positive finite numbers and their maximum is **strictly older** than the cutoff.
Pinned, archived, pending, active, imported, CLI, read-only, unknown-source,
parent-owned, compression-snapshot/recovery and worktree-bound records are
excluded. Unknown or legacy metadata layouts are skipped, not migrated. New JSON imports
retain an explicit `imported` provenance flag. Historical JSON imports that
already lost all provenance are indistinguishable from source-less native
sidecars. Therefore automatic archival requires at least one explicit `webui`
source field: source-less historical chats are skipped, not guessed from titles
or content. Newly created native sessions record that source. Manual native
archive/restore does not require explicit source provenance.

The implementation is deliberately conservative: any registered worker (even
cancelled/unwinding), SSE run, writeback owner, subprocess ownership, pending
wakeup, /background task, /btw tracking, or manual compression job can pause
maintenance globally. A contended runtime/session lock also skips work. This
may delay archiving indefinitely on an installation with persistent activity
or retained process ownership; it is preferable to racing live conversation
writes. Existing background task storage is read-only, never initialized by the
archive worker. No new database or independent archive flag store is added.

## Data safety and restore

The authoritative change is solely the native sidecar archive metadata. The
worker obtains the per-session admission lock nonblocking, reads at most 64 KiB
of metadata, writes a temporary file, and copies the raw transcript/context/tool
suffix unchanged. It rechecks runtime busy state and file identity/stat before
atomic replacement. Cached Session objects have only the changed attributes
updated; the existing index is refreshed from fresh metadata, never from an
empty metadata stub or stale cached transcript. Sidebar notifications are
published once per affected profile after a batch. Neither archiving nor
restoring changes `updated_at`.

Manual native archive/restore uses the same transaction. Restoring records
`auto_archive_restored_at`, granting another entire inactivity interval before
that chat can be automatically archived again. The existing archived-chat UI
can still display and restore history. External/CLI fallback behavior is not
changed. A busy or unsupported native manual operation returns HTTP 409 rather
than risking a full transcript rewrite.

The fast safe path refuses files larger than 8 MiB, symlinks, hardlinks, unsafe
session IDs and oversized/unknown metadata prefixes. Such native files cannot
use this metadata-only manual archive path either. The one-second tick budget
is not a hard filesystem I/O deadline; one bounded file copy/fsync may exceed
it on slow storage. The design assumes the existing single WebUI writer process;
other processes editing the same sidecar without its admission lock remain
outside that concurrency contract. Atomic stat checks reduce but cannot turn
uncoordinated external writes into a cross-process transaction.

## Performance scope and rollback

Archiving hides inactive rows from the ordinary sidebar payload and reduces
browser rendering work. It does **not** delete messages, shrink `state.db`,
remove JSON files from disk, or eliminate the existing full directory/database
list scans. No latency or memory improvement is claimed without measurement.
Set `auto_archive_days` back to `0` to stop new automatic changes; restore chats
individually as needed. There is no schema migration to roll back.

## Verification

`./scripts/test.sh -q tests/test_session_auto_archive.py` covers invalid/disabled
policies, exact cutoff, recent activity, protected records, profiles, lock and
write failures, byte hashes of the full transcript suffix across archive and
restore, stale full/metadata caches, index/reload visibility, restore grace,
batch fairness, hostile paths and worker shutdown. Run with isolated
`HERMES_HOME`, `HERMES_WEBUI_STATE_DIR` and test state, never live credentials.
