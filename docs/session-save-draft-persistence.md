# Session save and transient composer persistence

Contract routing: visible transcript durability and sidebar/session metadata;
see `rfcs/webui-run-state-consistency-contract.md`, especially maintenance is
not activity. This change does not alter context deduplication or archival.

`Session.save()` still serializes the full incoming transcript. It avoids
re-reading/parsing the previous transcript when an instance has a count proven
by a full load or successful save of that exact path/device/inode/size/mtime/
ctime identity. Metadata-prefix counts are not proof. Replacement or in-place
edits fall back to parsing a held file descriptor, checking identity before and
after reading. The target is checked again after backup I/O before replacement;
repeated change fails closed. In-process saves of the same path are serialized
with bounded striped locks independent of the non-reentrant agent locks.

A shrinking save must successfully atomically preserve the previous bytes in
`.json.bak`; backup errors now abort instead of allowing unprotected shrink.
Corrupt input is also backed up. Metadata-only full-save refusal and the
empty active/pending-snapshot guard remain. This is not a cross-process CAS:
non-cooperating writers can still race the final stat/rename interval, and stale
full snapshots retain the existing backup-on-shrink semantics, not append merge.
Like any stat-keyed cache, changes that reproduce every identity field within a
filesystem clock tick cannot be detected without rereading the file contents.

Draft POST uses `save_draft()` and metadata-only resolution (including the
subagent view-only guard): only `<sid>.draft`
is written, without touching transcript bytes, recency, or sidebar index. The
modern-layout route never parses or serializes the full transcript; existing
legacy/unexpected-layout fallback in metadata loading can still full-load once. Full
and metadata-only loads overlay that draft. A stale agent instance that did not
edit its draft adopts the latest draft on full save; deliberate changes (such
as clearing after send) persist independently. A draft publication failure does
not update the in-memory draft. Draft changes and transcript changes are separate
transactions, not a multi-file atomic commit.

The one exception is a fresh, exact cached `new_session()` instance with no
messages and no session JSON yet. Clearing that composer remains memory-only;
typing text or attaching a file materializes its first session snapshot, then
the draft sidecar. Every successful full-save publication consumes this
in-memory-only permission, even if a foreign write immediately invalidates the
saved-count identity. `_draft_new_session` is never serialized. Deleted cached
sessions cannot use draft persistence to recreate their transcript.

The draft record is tied to `created_at` to prevent reuse by a newly-created
session with the same ID. Legacy embedded drafts remain the fallback. `.draft`
files are ignored by session/recovery JSON scanners; orphan drafts cannot create
sessions. Session deletion currently leaves the small orphan draft (no transcript)
behind, as does existing backup recovery state. A cleanup migration is not part
of this scoped optimization. Copy/export of a raw session JSON alone may contain
a stale embedded draft; application loads return the overlay.

Regression coverage: `tests/test_session_save_identity.py`, existing #1558
safeguards, draft validation and autoarchive byte-preservation tests. Performance
measurements use synthetic history, not user state, and exclude providers,
network, browser rendering, state.db settlement and cold-cache storage effects.
