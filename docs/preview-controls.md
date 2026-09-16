# Readable conversation previews

Contract routing: bounded rendering and full transcript access; only browser presentation and response copies change. Saved messages and model context are not rewritten.

- A transport-marked text preview exposes **View full content** beside the affected message, with a native keyboard-accessible button. The global full-content control remains available for tool/scene-only previews.
- This is an explicit full transcript fetch using the existing unwindowed `messages=1` path (without `msg_limit`); it is never automatic polling. Simultaneous expansion actions share one request per session and navigation generation, and the request state is released on failure or success.
- Errors leave the control available for retry. Navigation during the fetch cannot hydrate, re-render, focus or toast into a newer visit, including A → B → A; an older request cannot release the newer visit’s loading lock. Keyboard focus returns to the expanded message when its canonical position is mounted.
- Only `_content_truncated` and `_preview_content_truncated` metadata control the affordance. A human quoting a truncation notice stays ordinary content.

`tests/browser_background_activity.py` includes the row control, keyboard recovery, nested disclosure geometry and live activity gates. `browser_preview_controls.py` fault-injects failure/retry and coalesced loads; `browser_preview_navigation.py` executes the actual hydration helper with delayed A → B → A responses in both orders, stale errors and a gated mutex wait; `browser_content_preview.py` exercises a real API fixture with large early detail, readable late prose, full recovery, reload and unchanged canonical messages.
