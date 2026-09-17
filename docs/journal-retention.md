# Opt-in run-journal retention (offline maintenance)

This utility is **not enabled, scheduled, or called by the WebUI**. It does not
resolve environment/configuration paths. No existing session, transcript, SQLite
DB, sidecar, directory or backup is deleted by its inventory or retention policy.
Only eligible `_run_journal/<session-id>/<run-id>.jsonl` files can be removed.
Backups contain potentially sensitive conversation data; keep them private.

## Read-only inventory

From the repository, with an explicit **absolute** sidecar/session directory:

```sh
python3 -m api.journal_retention --session-dir /absolute/path/to/sessions
python3 -m api.journal_retention --session-dir /absolute/path/to/sessions --days 60
```

The default is **dry-run**, 30 days. Minimum is 7 days, a conservative local
reconnect safety policy, not a claim that all clients expire cursors in 7 days.
Use a longer period if your reconnect/recovery requirements demand it. Both
journal mtime and every event timestamp must be strictly older than the cutoff.
JSON output includes candidate identities, byte lengths, SHA-256 and skip reasons;
it never includes message content. Dry-run writes no files (reads can affect atime).
Inventory is bounded to 1,000 directory entries including session directories,
64 MiB per journal/sidecar, 256 MiB aggregate input and 100,000 rows per journal.
The output explicitly marks truncation; this is not a complete storage census.
Oversized/unknown/corrupt files remain untouched. A missing journal root is an
error, not an empty-success inventory.

Eligibility deliberately requires all of:

- a valid contiguous v1 journal, one successful semantic `done`, final
  `stream_end`, no error/cancel and no unexplained activity after done;
- a sidecar of the same session with explicit `active_stream_id: null` and no
  pending turn (even a different active run excludes the entire session);
- a full `done.payload.session.messages` snapshot ending in a non-error final
  assistant response, with timestamp within this run, which exactly matches the
  independently persisted sidecar message prefix.

Transport closure alone, canceled/error runs, missing IDs/timestamps/sidecars,
partial settlement payloads, compressed/rotated sessions, redaction mismatches,
and ambiguous legacy data are **not** deletion proof. They are retained, even if
that substantially limits reclaimed space. This does not modify recovery, SSE,
runtime events or session metadata. Retention removes old replay cursors; users
still have the persisted transcript, but exact replay detail requires rollback.

## Apply requires a real, operator-enforced outage

**Do not apply to a running WebUI.** Runtime journal writer locks are Python
process-local locks, not a cross-process maintenance lease. A separate utility
cannot acquire a meaningful runtime lock. No port/PID/process-name heuristic can
prove every writer stopped. This release therefore supports **offline apply
only**. It does not stop services or disable restarts for you.

Before authorizing an apply, stop **all** WebUI instances/workers sharing the
state (including containers/other hosts), disable automatic restarts, ensure no
other process changes this directory, and keep them stopped until completion or
rollback. If you cannot enforce this, use dry-run only. The marker below is an
operator attestation, **not independently verified process-liveness evidence**.
The tool takes advisory maintenance locks to exclude cooperating maintenance
invocations; these are explicitly not runtime locks. Directory descriptors,
no-follow opens, single-link regular-file checks and identity rechecks add defense
in depth, but are not a substitute for the outage against uncooperative writers.

Create an existing private mode-0700 backup directory outside the entire session
path, and a mode-0600 JSON offline marker owned by the invoking user, also outside
that path. No ancestor may be a symlink. Marker schema (substitute actual values
only **after** confirming the outage):

```json
{
  "webui_stopped": true,
  "all_workers_stopped": true,
  "automatic_restarts_disabled": true,
  "session_dir": "/absolute/path/to/sessions",
  "device": 123,
  "inode": 456,
  "created_at": 1234567890.0
}
```

`device` and `inode` are `os.stat(session_dir).st_dev` and `.st_ino`;
`created_at` is the current Unix time, no more than one hour old. The utility does
not create an attestation for you. Values above are schematic, not usable proof.

Only after reviewing the inventory and obtaining explicit removal approval:

```sh
python3 -m api.journal_retention --session-dir /absolute/path/to/sessions \
  --days 30 --apply --backup-dir /absolute/private/backups \
  --offline-marker /absolute/private/offline.json
```

Each apply creates a unique private backup bundle. Each candidate is copied to a
private temporary file, fsynced, atomically published and byte-for-byte verified.
A manifest with source identity and hash is atomically persisted and verified
**before** unlinking. The source and sidecar identities are rechecked immediately
before unlink. Directories are fsynced; final status is recorded afterward.
Failure before unlink preserves the source. Failure/crash after unlink leaves a
verified backup and a pre-unlink manifest entry. `backed_up_check_source` means
inspect the source, not assume it was removed. No backup expiry exists.

## Rollback / interrupted apply

Keep the outage in effect. Preserve the whole bundle and `manifest.json` even if
apply was interrupted. A `backed_up` or `backed_up_check_source` entry may describe
a source that still exists. Compare its bytes/hash and never overwrite it.

```sh
python3 -m api.journal_retention --session-dir /absolute/path/to/sessions \
  --apply --restore-bundle /absolute/private/backups/retention-BUNDLE_ID \
  --offline-marker /absolute/private/offline.json
```

Restore validates confinement, backup size/hash and the exact session root. It
restores only journal paths; the original journal parent directories must still
exist. Publication is atomic and **no-replace**. Existing files (including a
previously restored entry) cause failure, never overwrite. For a partial restore,
inspect and handle already-existing identical entries under the outage before
retrying; do not delete a replacement journal just to bypass the safeguard.
Alternatively copy verified backup bytes into a separate staging tree for manual
recovery review. Restore is per-file, not all-or-nothing. Retain the bundle until
manual validation is complete, then remove the offline marker and restore normal
service under your deployment's operational procedure.

## Contract routing and verification

State layer: durable run journal/replay only. Invariants: maintenance is not
activity; active/unknown runs are preserved; independently persisted final
transcripts remain unchanged; bytes are recoverable before any unlink.
References: `docs/CONTRACTS.md`, run-state consistency and session-SSE RFCs.
Automated coverage: `tests/test_journal_retention.py` uses only temporary synthetic
state, with journal envelopes produced by the actual `RunJournalWriter` using an
explicit temporary root (no production discovery). Tests cover successful tool /
reasoning / title lifecycles, nonterminal/cancel/error/tool-limit outcomes, dry-run,
apply, corrupt/new/unknown data, replacement races, backup and pre-unlink manifest
failure, fsync ordering, symlinks, inventory bounds, refusal gates and recovery.
Run the focused offline suite with:

```sh
.venv/bin/python -m pytest --noconftest tests/test_journal_retention.py -q
```

The suite uses synthetic sidecar messages matching the full-snapshot contract;
it does not start the application or call model providers. Unknown/bounded
snapshots lacking the full matching transcript are retained. Runs with unlisted
events, no final `stream_end`, post-closure title activity, or subsecond timestamp
ambiguity are also conservatively retained. These restrictions can reduce yield.
No production cleanup or online-writer safety claim is made.
