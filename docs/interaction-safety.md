# Interaction safety and recoverable UX

These additions preserve the existing panels, per-profile state and canonical transcripts.

- **Approval**: Enter/Space activates only the focused action; focus outside the card does not authorize a command. YOLO policy is unchanged.
- **Attachments and drafts**: partial uploads require retry or explicit omission. Successful upload references remain available. Restored attachment metadata without bytes is marked for reselection, never reconstructed as an empty File. Late completion is bound to its original session/profile, separate from the next editable draft.
- **Editors**: unsaved skill, job, workspace and file edits offer Save / Discard / Keep editing. Saves retain edits made while the request is in flight. Settings section Back/Forward preserves its existing dirty form instead of rehydrating it.
- **Composer**: busy actions explicitly distinguish Queue, Steer and Interrupt-and-send, with a separate Stop control. Critical connection/recovery/error status remains visible when optional detail is hidden. Narrow layouts preserve readable text and reachable controls.
- **Voice**: recording normally ends in review. Transcription errors retain audio for retry or attachment. Explicit immediate send is bound to the capture's owner and unchanged draft; stale results never overwrite another draft.
- **Search**: the existing quick search stays bounded. Full saved-history search is explicit, paginated and scope-bound, with snippets, partial-coverage/error states and a canonical message location. A deliberate result jump owns the scroll position over deferred hydration.
- **Navigation**: URL, reload and browser history restore the existing panel, settings section and selected item. A link never silently switches profiles. Mobile navigation adds labels to the same configurable rail. Preview iframe replacement avoids adding hidden child entries to browser history.
- **Preferences and disclosures**: frequent options precede advanced groups. Search opens a matching disclosure and focuses its field. Settings describe browser/profile scope and explicit Apply behavior. Export, sharing and clearing are visually separated without changing their persistence contracts.
- **Scheduling**: `GET /api/crons` includes `scheduler_timezone`, including empty job lists. `GET /api/crons/preview?schedule=...&profile=...` is read-only: it delegates parsing and next-run calculation to the installed scheduler, returns `ready`, `invalid` or `unavailable`, and never creates/runs a job. Missing scheduler capability is stated honestly. The form rejects outdated preview responses; it shows scheduler time and the browser-local equivalent when different.
- **Metrics and accessibility**: load errors are distinguishable from zero activity. Keyboard focus, dialog choices and high-use touch targets remain visible.

## Verification

Use `./scripts/test.sh -o addopts= -q tests/test_ux*.py tests/test_full_history_search.py tests/test_cron_schedule_preview.py tests/test_cron_schedule_preview_browser.py` with isolated HOME/Hermes/state and an available Node/Chromium runtime. Set `UX_TEST_CHROMIUM`, `UX_CHROMIUM` and `CHROMIUM_PATH` when browser auto-discovery is unavailable.

The real-app offline integration harness is `python tests/browser_ux_integrated.py --chromium /path/to/chromium --output /path/to/evidence`. It serves the current full index/boot via the application backend, seeds synthetic transcripts, blocks provider/network egress, records desktop/mobile screenshots and checks downloads, navigation, dirty state and full-search visibility. Runtime metadata endpoints are explicit fixtures. This does not certify live STT/provider calls, an installed scheduler execution, every OS/browser or a production deployment. No real jobs, messages or configuration are changed by this QA.
