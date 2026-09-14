# Background reliability: verification and boundaries

## Tested source

- Fork baseline: `94fd2da83008d24719eb3b52a691ce696d4f992c`.
- Initial product-source revision: `1922c6dfd4af11539871b89927aa7ffb30841db6`.
- Retry-batch follow-up product revision: `8e5b9d2fdfd1a6007b6adab25d3e7a802babe52f`; the integrated Python selection below was rerun against these product/test changes before commit. JavaScript/browser source was unchanged by this follow-up.
- Test runtime: Linux ARM64, Python 3.12.14, Node 22.22.0 and Chromium via Playwright.
- Tests used temporary HOME, Hermes home/config and WebUI state. No production service, user session or provider credentials were used. No deployment was performed.

## Executed verification

| Gate | Result | Evidence scope |
| --- | --- | --- |
| Integrated targeted selection (96 test files) | 962 passed, 11 skipped; no failures/errors | Journal/cancel, wakeups, delivery, background, history, session/cache, composer and keyboard-adjacent regressions |
| Accessibility browser wrapper | 9 passed | Reasoning, workspace panel and busy composer at 1440×900, 522×1232 and 390×844 |
| Background HTTP/browser | All three viewports passed; no console errors or body overflow | Import/load API, expand/collapse, reload, session switch, original transcript read-back and visible full-content action |
| Background task renderer | Passed | Repeatable snapshots, siblings, safe result disclosure, navigation and no assistant-message injection; collaborators are controlled fixtures |
| Live disclosure probes | All five passed | Focus, nested approvals, settled labels, human provenance and virtualizer boundaries; actual source in isolated DOM |
| Existing browser lifecycle | normal, terminal-error, historical and smoke passed | Real WebUI with deterministic Gateway fixture, no model inference |
| JavaScript | ESLint runtime guard and scope-undefined gate passed | Entire registered static surface; 19 files in scope-undefined gate |
| Python/static | Compilation and diff-aware Ruff passed; git diff check passed | No newly introduced Ruff violations on modified lines; not a claim that the legacy repository is globally lint-clean |

The 11 integrated skips are explicit: unavailable full-core bridge/cross-session imports, a Windows-only check, and the existing wakeup-card Playwright cases in the non-browser Python environment. Separate browser gates above ran in the browser environment. Selected real core delivery functions were AST-loaded into a temporary SQLite fixture; that is not a live core/restart E2E.

## Late-review reconciliation and remote CI

- Nested approval insertion and focus preservation were already fixed in the published tree. All five actual-source DOM probes passed again.
- Retried batch recombination remained a real defect in `2afabac`: the new tests produced **8 failures and 1 pass** before the fix. After preserving server-assigned retry batches as atomic FIFO barriers, the targeted selection passed **63/63**; the expanded integrated selection passed **962**, skipped **11**, with no failures/errors in **140.76 seconds**.
- Coverage includes a full 16-event batch plus overflow, first/middle/last/sibling queue positions, repeated rejection and stable identity, and busy, credential-paused, 503 and exception outcomes. Plain output resembling a header does not become batch provenance.
- The remote [Tests run for `2afabac`](https://github.com/carlosesl1/hermes-webui/actions/runs/34888658982) completed with **failure**. Logs from all five Python 3.12 shards include locale-key coverage failures and UI/history/streaming regressions. These remain a separate unresolved CI boundary; the local targeted selection is not a substitute for the full matrix.
- Remote browser smoke, conversation lifecycle and Docs CI passed for `2afabac`. Those results do not certify a newer commit's CI. Check the current PR head before reporting release readiness; this follow-up is not a merge/deploy approval.

## Reproduction

Use the project setup in `TESTING.md`; run Python tests through `./scripts/test.sh` with an isolated test environment. Set `HERMES_WEBUI_TEST_CORE_SOURCE` to a read-only core checkout for the selected SQLite compatibility checks. With Playwright and Chromium installed in the browser test interpreter:

```sh
python tests/browser_background_activity.py
python tests/browser_background_tasks.py
python tests/browser_background_live.py
python tests/browser_chat_controls_accessibility.py
```

`BACKGROUND_ACTIVITY_ARTIFACT_DIR` and `CHAT_A11Y_ARTIFACT_DIR` control saved screenshots/results. CI runs these gates and uploads browser evidence. Public CI status is independent of local results and must be checked on the PR.

<details>
<summary>Exact integrated targeted test selection</summary>

```text
tests/test_1466_sidebar_cancel_clarify.py
tests/test_5144_busy_composer_placeholder_hint.py
tests/test_5306_subagent_sidebar_flicker.py
tests/test_5307_subagent_child_transcript.py
tests/test_agent_row_id_replay_order.py
tests/test_api_timeout.py
tests/test_async_delegation_webui_bridge.py
tests/test_async_delivery_admission.py
tests/test_background_activity_groups.py
tests/test_background_batch_retry.py
tests/test_background_durable.py
tests/test_background_process_restart_recovery.py
tests/test_background_process_wakeup_format.py
tests/test_background_tasks.py
tests/test_background_wakeup_batching.py
tests/test_bg_task_complete_wakeup.py
tests/test_bounded_full_content.py
tests/test_bounded_history_cost.py
tests/test_bounded_history_pagination.py
tests/test_bounded_markdown_download.py
tests/test_cancel_interrupt.py
tests/test_cancel_stream_owner_guard.py
tests/test_cancelled_turn_status.py
tests/test_cancelling_run_not_attachable.py
tests/test_composer_capture_clear_race.py
tests/test_composer_draft_after_send.py
tests/test_cross_session_message_load_isolation.py
tests/test_git_subprocess_windows_flags.py
tests/test_issue1103_reasoning_chip_visibility.py
tests/test_issue1298_cancel_and_activity.py
tests/test_issue1361_cancel_data_loss.py
tests/test_issue2211_workspace_panel_reopen.py
tests/test_issue2840_windows_hermes_home_defaults.py
tests/test_issue2905_windows_home_migration_safety.py
tests/test_issue3250_upward_scroll_intent_window.py
tests/test_issue3660_context_window_stale.py
tests/test_issue3802_delete_session_journals.py
tests/test_issue3929_process_wakeup_pause.py
tests/test_issue3952_escape_blur_composer.py
tests/test_issue4251_wakeup_model_picker_race.py
tests/test_issue4283_recovered_context_replay.py
tests/test_issue4626_windows_restart_no_console.py
tests/test_issue4650_reasoning_chip_no_storm.py
tests/test_issue4753_desktop_background_notifications.py
tests/test_issue5127_process_wakeup_bare_model.py
tests/test_issue5196_windows_safe_replace.py
tests/test_issue5532_session_clear_state_db_replay.py
tests/test_issue5543_background_memory_commit.py
tests/test_issue5954_empty_boot_reasoning_chip.py
tests/test_issue6240_windows_skill_symlink_fallback.py
tests/test_issue6623_cancel_owner_race.py
tests/test_issue6751_api_content_agent_replay.py
tests/test_issue7195_windows_restart.py
tests/test_issue734_message_windowing.py
tests/test_issue856_background_completion_unread.py
tests/test_issue893_cancel_preserves_partial.py
tests/test_issue_windows_git_version_detection.py
tests/test_journal_publication_integrity.py
tests/test_large_session_fuzzy_duplicate_bound.py
tests/test_parallel_session_switch.py
tests/test_pr1341_context_window_persistence.py
tests/test_process_wakeup_card_rendering.py
tests/test_process_wakeup_rendering.py
tests/test_process_wakeup_synthetic.py
tests/test_reasoning_chip_btw_fixes.py
tests/test_reasoning_chip_js_behaviour.py
tests/test_reasoning_content_replay.py
tests/test_recovered_journal_context.py
tests/test_route_session_list_cache_extraction.py
tests/test_run_journal.py
tests/test_run_journal_frontend_static.py
tests/test_run_journal_routes.py
tests/test_run_journal_seq_cache.py
tests/test_run_journal_streaming_static.py
tests/test_run_journal_writer_lock_evict.py
tests/test_session_index_lock_window.py
tests/test_session_list_cache_bounded.py
tests/test_session_message_window_renderable_tail.py
tests/test_sse_chunked.py
tests/test_stage326_composer_draft_validation.py
tests/test_subagent_parent_in_import_window.py
tests/test_turn_journal.py
tests/test_turn_journal_callsite.py
tests/test_turn_journal_lifecycle.py
tests/test_turn_journal_lifecycle_callsite.py
tests/test_wakeup_card_responsive.py
tests/test_wakeup_defer_race.py
tests/test_wakeup_display_meta.py
tests/test_wakeup_meta_recovery_paths.py
tests/test_wakeup_model_resolve_hang.py
tests/test_window_function_collision.py
tests/test_windows_native_support.py
tests/test_workspace_artifact_windows_backslash.py
tests/test_workspace_panel_persists_on_empty_boot.py
tests/test_workspace_panel_session_list.py
tests/test_xsession_wakeup_misroute.py
```

</details>

## Deliberate limits / follow-up work

- This is a targeted regression suite, not the repository's complete suite, a security certification, or a real-provider test.
- Grouping requires explicit notification provenance. Source-less historical rows and spacer-based virtualized windows retain a conservative flat presentation to preserve human messages and scroll geometry.
- Batching consolidates pending completions; it does not suppress useful results, remove canonical transcript rows, or guarantee a model will never acknowledge a notification.
- `/background` persistence is single-server-process task tracking, not a distributed job queue. A restarted task is marked interrupted; it is not automatically resumed. Completed task records are retained, and retention/cleanup remains separate follow-up work.
- Refund compatibility depends on the documented core helper/schema. Unknown cores fail conservatively rather than inventing delivery authority. Detached-child cascade cancellation is not claimed.
- Bounded history reduces selected-row work and text transport, not every possible nested object/scene or full sidecar parsing cost. Explicit full-content/export remains intentionally potentially large.
- Giant terminal-journal snapshot migration, empty-cursor replay optimization, static-bundle splitting and group-aware virtualization remain separate work. No storage/context rewrite was attempted merely to reduce measured bytes.
