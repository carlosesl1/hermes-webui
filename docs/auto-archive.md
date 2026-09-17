# Inactive chat auto-archive

In Settings → Preferences, choose Disabled, 7, 30, 90 days, or a custom integer
from 1 through 3650, then explicitly save the archive policy. The default is
Disabled (`auto_archive_days: 0`). Merely changing the selector does not save it.
This is an installation-wide policy for native WebUI chats across profiles, not
a per-browser filter. Invalid API values return 400; invalid persisted policies
fail closed to disabled. No model or external service is called.

## Eligibility and preservation

Activity uses the maximum of valid `created_at`, `updated_at`, and a manual
restore timestamp. It must be strictly older than the selected interval.
Pinned, archived, active/pending, imported, read-only, external-source,
compression snapshot/recovery and worktree-bound sessions are excluded.
A native continuation can be archived; its compression snapshots are preserved.
New native chats record their source. For historical source-less chats, a
read-only lookup in that chat's own profile database must confirm `source=webui`.
Unknown origin, missing database, malformed timestamps and unsupported metadata
are skipped rather than guessed or repaired. JSON imports retain provenance.

The worker obtains the per-session admission lock nonblocking, parses at most
64 KiB of metadata, and copies the transcript/tool/context suffix byte-for-byte
to an atomic replacement. It rechecks file identity/stat, runtime activity and
the policy before commit. Cached objects receive only archive metadata, never a
stale transcript rewrite. The sidecar remains authoritative and its derived
index is refreshed; list events are published per affected profile.

Automatic maintenance never changes `updated_at`, deletes messages, compresses
history or removes files. Existing manual archive/restore behavior is retained,
including large chats. Restoring explicitly records `auto_archive_restored_at`
and grants another full inactivity interval. Disabling the policy stops future
automatic changes; it does not restore already archived conversations.

## Scheduling and conservative limits

One stoppable in-process worker starts after HTTP binding, waits 60 seconds,
then examines at most 25 directory entries per tick with a persistent cursor.
There is a one-second between-candidate scheduling budget. Disabled ticks do
not scan the directory. No GET/list request triggers maintenance writes.
Copy/fsync can exceed that budget on slow storage; files above 8 MiB,
symlinks/hardlinks and unsupported metadata layouts are skipped automatically.

Any live agent worker (including cancellation unwind), SSE run, writeback owner,
running subprocess, pending wakeup, background/btw task or manual compression
pauses maintenance globally. Contended locks also skip work. A process ownership
receipt alone does not block when the loaded registry confirms no running
processes. Continuous activity can delay archiving; this conservative policy
prefers chat reliability over immediate housekeeping. No new database/schema is
introduced. Existing single-WebUI-writer assumptions remain.

## Performance scope

Archiving reduces the normal sidebar response and browser rendering work. It
**does not** shrink the underlying database or eliminate directory/database
scans. No latency improvement is promised without measurement. Settings are
read on each scheduled tick; browser refresh is not required for the worker.

## Verification

Use the isolated repository runner for `test_session_auto_archive.py`,
`test_settings_auto_archive.py`, metadata-save and archive regression suites.
Coverage includes cutoff, protections, historical profile/source lookup,
byte-identical transcript suffix, stale cache safety, index/reload, restore
grace, disabled/changed policies, Unicode boundaries, write/stat failures,
process receipts, fairness, shutdown and hostile paths.

`tests/browser_auto_archive.py` exercises the actual Settings controls at
1440×900 and 522×1232: explicit save, custom days, validation, reload, disable,
keyboard, an injected API failure and Portuguese copy. Its default mode uses
the real isolated API; optional mocked mode is labeled as UI-only evidence.

`tests/verify_auto_archive_worker.py` starts an isolated real HTTP server and
waits for its actual scheduled tick (no model calls). It checks visible/all
lists, transcript-suffix hashes, manual restore and disabling the policy.
A measured 10-chat synthetic fixture archived 8 eligible chats while protecting
recent/pinned chats: the normal payload changed from 8,657 to 1,992 bytes.
Single-request local timings were 7.614 ms before and 7.505 ms after; these are
**not** evidence of a production latency improvement. All 10 transcripts stayed
byte-identical through automatic archival. Targeted integration: 412 tests in
38 files passed; real-API Settings QA passed at 1440×900 and 522×1232.
