# Editor safety and scheduler display

Contract routing: UI interaction state (`docs/CONTRACTS.md`, `docs/UIUX-GUIDE.md`). No changes to scheduler execution, persisted job format, or workspace authorization.

`await guardUnsavedEditors(key?)` in `static/panels.js` returns a boolean. Call it **before** changing navigation, session, profile, or workspace ownership; abort the navigation on `false`. Omit `key` to check all editors, or use `skill`, `cron`, or `workspace`. Do not pass DOM events as the internal `bypassGuard` argument. Bypass is only for the guard's own discard and post-save navigation.

The guard compares current values with the loaded/submitted snapshot. It offers Save, Discard, and Keep editing using existing app-dialog classes. Escape keeps the draft. Failed saves and edits made during a pending save prevent navigation. Browser unload uses the browser's native unsaved-change prompt. A pending save blocks replacement, including duplicate submissions.

Wired boundaries: skill selection/linked files/create/edit/cancel; cron selection/create/edit/duplicate/cancel; panel switches; workspace selection; file selection/directory navigation; file editor Escape. Other callers which directly clear previews or switch session/profile ownership must invoke the same guard before mutation. In particular the outer workspace close handler and session/profile navigation are integration responsibilities outside these owned files; do not claim universal exit coverage until tested there.

Cron API responses add `scheduler_timezone` from the owning scheduler's actual clock. Missing clock metadata, legacy cores, and server-local clocks without an IANA identity return null, never a browser/server guessed zone. The detail view shows the stored next/last instant in that zone and the browser-local equivalent when different. Naive timestamps retain their literal value and are labeled unknown. No client-side cron prediction is performed; the form states that the next execution is shown after saving. New forms without authoritative metadata show unknown.

Skill usage has loading, successful-empty, ready, failed, and stale-cached states. Retry only requests skill usage. Cache identity is the active profile; an error never fabricates zero counts.

Verification: `./scripts/test.sh tests/test_ux_editor_contracts.py -q --timeout=30` exercises the actual shared helpers in Node and the backend metadata resolver with fake scheduler clocks. These are offline behavioral tests, not full browser CRUD, screenshot, or real-scheduler execution coverage. No jobs or providers are run. English/pt-BR integration keys are supplied separately to the integration owner, not patched into the shared locale file in this branch.
