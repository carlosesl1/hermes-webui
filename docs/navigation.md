# Persistent navigation

The optional classic-script `static/navigation.js` belongs after `panels.js` and
`sessions.js`, before `boot.js`. Its hooks do nothing when the module is absent.
The application boot completion restores navigation, including successful early
returns. The existing session `popstate` listener delegates to the module;
there is no second listener and no `hashchange` listener.

Routes use the existing session path plus a fragment:

- `/session/<id>#panel=settings&section=appearance`
- `/session/<id>#panel=skills&item=<encoded-name>&scope=<profile>`
- `#panel=tasks&item=<id>&scope=<profile>`
- `#panel=workspaces&item=<encoded-workspace-path>&scope=<profile>`
- `#panel=profiles&item=<name>&scope=<profile>`

Panel and Settings section names are allowlisted. Item restoration resolves only
items in the current authenticated list. Profile scope constrains item lookup;
it never activates a profile. Viewing workspace/profile details never changes
the active workspace/profile. Cross-profile read-only cron rows intentionally do
not generate item links. Deleted, unavailable or foreign-profile items fall back
to their panel, with a notice. No editor content, drafts, permissions, prompts,
API tokens or arbitrary query parameters are serialized. Identifiers (including
workspace paths) are visible in shared URLs; do not share a link containing a
private identifier. No new backend route or permission is introduced.

Navigation is presentation state, separate from persisted sessions and drafts.
Normal selection pushes one history entry; restore/canonicalization replaces
that entry. Existing session URL helpers continue to preserve fragments. Dirty
editor guards run before history restoration, including same-panel item changes;
a veto travels back to the accepted history position without adding an entry.
Settings retains its existing save/discard flow. Selection generations plus the
profile-switch generation prevent late skill content from owning another visit,
including profile A→B→A. Busy cross-session history navigation is refused.

Limitations: plugin/extension-specific routes, Kanban item links, skill linked-file
links, create/edit forms and file previews are not serialized. The legacy bare
`?profile=` launch behavior is unchanged; managed `#panel=` links suppress that
implicit profile switch. Existing direct session/profile actions remain separate
from URL restoration. Full application/mobile visual QA must follow integration
of the script and navigation labels; isolated Chromium tests exercise actual
routing, guards and selectors with offline data/render fixtures.

Run the behavior suite with `./scripts/test.sh tests/test_ux_navigation.py` using
isolated HOME/HERMES_HOME/HERMES_WEBUI_STATE_DIR/TMPDIR. Python Playwright and its
Chromium must already be installed. `UX_TEST_CHROMIUM` may select an existing
compatible executable. The tests do not install dependencies or contact providers.
