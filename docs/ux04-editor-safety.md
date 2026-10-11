# UX04 editor ownership and navigation safety

Unsaved workspace, skill and cron editors belong to the original DOM instance,
session, workspace and profile generation. Saving under another context is
refused. Session/profile navigation prompts before destructive pane changes;
selecting the already active session/profile remains a no-op.

Workspace close and direct preview-clear callers share the save/discard/keep
check. Keep or a failed save retains both the draft and its original path.
Directory navigation closes the old preview before list I/O, so a late listing
cannot close a newer editor. Each preview read owns a generation and complete
context; late text/Markdown/CSV/Office reads and errors cannot replace newer
content, reset dirty state or initiate downloads in the new context.

Skill saves keep the editor registered through POST, list refresh and final
content GET. Before the final read-mode render, the submitted snapshot is checked
again. Typing during that GET preserves the form and beforeunload protection.

## Offline regression coverage

Run through `./scripts/test.sh` with isolated HOME, HERMES_HOME,
HERMES_WEBUI_STATE_DIR and TMPDIR. Set UX_TEST_CHROMIUM to an existing Chromium
executable when the normal Playwright cache is unavailable; no installation is
required. `tests/test_ux_editor_safety.py` executes production handlers in
Chromium at desktop/narrow widths, with deferred API responses and captured
POST payloads. Neighboring contracts: `test_ux_editor_contracts.py`,
`test_ux_navigation.py`, `test_cross_session_message_load_isolation.py`, and
`test_profile_switch_ux.py`.

This is deterministic frontend boundary coverage, not real-server/provider E2E
or a full-product certification. Existing workspace-panel hide/toggle helpers
outside clearPreview retain their behavior; hiding does not clear editor/path.
