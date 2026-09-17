# Reliability and large-session performance

This change set is scoped to WebUI; it neither modifies Hermes core nor migrates,
compresses or prunes canonical conversation history. It is additive to turn-owned
delegation, full-content loading, sidebar covering-index selection and opt-in
inactive-session archiving.

## Reading and scroll ownership

- Pending animation frames belong to a session generation, not just a matching
  session id; A → B → A cannot apply A's old restoration callback.
- Preserve the visible message's semantic identity and relative pixel offset
  across render, two successive prepends, delayed media/reflow, stream and done.
- Virtual spacers reconcile measured heights synchronously; prepend chooses the
  corresponding loaded-window position before rebuilding the virtual window.
- Reader intent wins. Reading older content never enables automatic tail following.
  A reader who is already following the tail continues to follow new output.
- Keep existing presentation modes and virtual scrolling configuration. No forced
  virtual scrolling rollout is part of this patch.
- Hidden session polling stops on 404/410 only for the owning generation.

Run `tests/browser_scroll_ownership.py` with the installed Playwright environment.
It drives actual static application scripts with explicitly synthetic API data,
not a real provider. The matrix covers 1440×900 and 522×1232, all three transcript
modes, virtual scrolling on/off, full rerender, deferred processing, consecutive
prepends, delayed height change, streaming, completion and tail following.

## Canonical data and bounded projections

- Occurrence IDs/timestamps/turn and tool-call provenance identify messages.
  Identical text is not sufficient evidence of replay. Anonymous ambiguity is
  preserved; cross-source reconciliation consumes matching occurrences one-to-one.
- Session.save fast-paths only unchanged file identities and falls back on concurrent
  replacement or modification. Atomic replacement and no-history-loss guards remain.
- Drafts use atomic metadata sidecars, avoiding a full transcript rewrite. Legacy
  inline drafts remain readable; draft writes do not advance conversation activity.
- Done payloads project a bounded tail before secret redaction. Offsets, total count,
  omitted-content flags and a literal full-content endpoint accompany the preview.
  History/export/provider context are not replaced by this projection. The frontend
  fills only a missing completion gap and never labels a preview as fully loaded.
- Secret-redaction memoization is bounded by retained bytes and per-record size,
  with rule-generation identity and concurrent-insertion accounting. Oversized values
  bypass caching, not redaction.

## Runtime boundaries

Model selection resolves its provider, endpoint and context together without
silently borrowing an unrelated custom provider's endpoint. Toolset changes invalidate
persisted tool/system-prompt snapshots under the session lock; active generations
return 409 rather than being rewritten. CLI archival is honored and UI mutations
use the conversation owner's profile. Public typed Codex commentary is eligible
for media discovery, while reasoning and cross-profile state paths are not.

Runtime home scopes use the installed core's contextual-home API; changing profiles
must not mutate process-wide HERMES_HOME. Credentials inherited at startup belong
only to their original home; nested profile scopes cannot inherit a sibling's
credentials. Explicit sync-turn workspace/session overrides survive the scope.
This is not a claim that every legacy third-party tool has become context-aware.

Tool completion reflects structured runtime success/failure, not string matching.
High-frequency metering remains live but is not durable replay data. Durable run
ordering/terminal markers remain unchanged. SSE subscriber leases disconnect an
abandoned relay, never its worker, and preserve cursor-based reconnect semantics.

## Retention is deliberately opt-in

`python -m api.journal_retention --help` documents an offline utility with dry-run
as the default. It requires the WebUI to be stopped/maintenance access to be
exclusive, explicit age selection, terminal completion, a canonical session,
complete contiguous records, safe regular files and revalidation before deletion.
It cannot delete session JSON, SQLite history or unknown/incomplete runs. Deploying
this feature does not delete existing journals or enable a cleanup schedule.

## Verification layers

1. Isolated pytest fixtures for affected contracts and negative cases.
2. Browser synthetic matrix above; not evidence of provider correctness.
3. Frozen-runtime canary: offline boot/restart and reviewed served-file hashes.
4. Isolated candidate server with real configured model/provider: two identical
   human prompts, durable bounded done, canonical prefix unchanged, draft persistence,
   desktop/mobile reload and explicit deletion of only the disposable QA session.
5. Production external executor: bounded active-turn drain, state/sidecar backup,
   exact-image assertions, public QA and independent rollback. Production success
   must be read back from its outcome; starting the executor is not success.
