# Media and archive boundary fixes

## Media

Both capture and serving deny internal state under base, active and sibling profile homes, including both `webui` and `webui_state` layouts and resolved symlinks. Real project workspaces remain usable; selecting a state root as the workspace does not exempt its secrets.

Codex sidecar references authorize media only when the outer message is assistant and the sidecar item is a typed assistant `message` in the public `commentary` phase with string `output_text` content. Reasoning items, analysis/final phases, user items and malformed content do not authorize capture or serving. Existing ordinary assistant/tool content behavior is retained. Existing per-message snapshot bindings remain immutable when a source file is overwritten.

## Archive

The state.db projection carries its optional `archived` column (older schemas remain supported). Missing sidecars and sidecars without an explicit boolean archive field do not override the DB; explicit true/false fields do. This includes CLI, cron, webhook and kanban projections.

Archive, restore and batch archive send the row's `profile`. This field is a selector, not authorization: the server validates it against the stored session owner or a unique server-discovered DB projection. Invalid, mismatched, missing foreign and ambiguous DB-only owners fail closed. Isolated-profile servers reject foreign selectors before lookup. Metadata-only cached sessions are reloaded before owner validation and mutation. Read-only and subagent guards remain enforced, including an owner-scoped read-only DB check. Archive flags must be booleans.

DB-only archive/restore writes a WebUI sidecar override; it does not modify the upstream agent DB's archival flag. Legacy requests without profile continue to work for stored active-profile sessions, but DB-only rows require a profile. Session IDs remain global in WebUI's sidecar store; conflicting existing sidecar owners are rejected rather than overwritten.

## Verification scope

`tests/test_upstream_media_archive_boundaries.py` exercises real POST/media route handlers with fixture handlers, real SQLite projections and on-disk sidecar readback. It covers archive and restore, malformed/foreign owner rejection, duplicate-owner rejection, DB-only restore with unchanged upstream DB, and frontend owner transport for all three archive call sites. Related snapshot, inline media, cron projection, metadata reload and worktree regressions are run through the isolated supported runner.

No live deployment, network/provider calls, authenticated browser E2E or full repository suite is implied. Bulk transport coverage is a static call-site contract plus the shared POST handler tests, not a browser click test.
