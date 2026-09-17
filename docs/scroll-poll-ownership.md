# Transcript scroll and hidden-poll ownership

## Scope

- Scroll snapshots carry their session identity. Both ordinary and same-frame restore paths ignore a snapshot belonging to another session, without changing the current reader's pin state.
- Synchronous message post-processing restores the current semantic viewport anchor (or tail-relative distance for a pinned reader) while native overflow anchoring is suppressed. Detached containers are ignored. Existing input-generation checks still yield to newer user input.
- Hidden active-stream polling stops on 404/410. Network failures and 5xx remain retryable. A poll generation prevents an older request or timer from stopping a replacement poll, including replacement for the same session ID.

No CSS, overflow/clipping policy, intentional navigation, retry interval, backend API, or persistence changes.

## Automated checks

Run the supported runner with Node on PATH:

```sh
./scripts/test.sh -q tests/test_hidden_poll_ownership.py tests/test_scroll_session_ownership.py tests/test_transcript_scroll_invariant.py tests/test_issue4295_midstream_scroll_anchor.py tests/test_issue4720_done_scroll_jump_first_message.py tests/test_issue6414_programmatic_scroll_user_intent.py
```

The transport test executes the production poll functions with controlled timers and promises: 404, 410, 500, 503, network rejection, and 200, each with no replacement, same-session replacement, and different-session replacement. Snapshot tests execute both production restore functions against a session switch.

## Browser evidence

```sh
SCROLL_EVIDENCE=/tmp/scroll-after python tests/browser_scroll_ownership.py
SCROLL_BASELINE=<pre-fix-commit> SCROLL_EVIDENCE=/tmp/scroll-before python tests/browser_scroll_ownership.py
```

Requires Playwright/Chromium. All routes are fulfilled locally; boot and remote APIs are disabled. The baseline loads `static/ui.js` from the specified commit without modifying the working tree.

Matrix: 1440×900 and 522×1232; compact worklog, transparent stream, and hide-all activity; `window._virtualizeTranscript` on/off. The test requires real virtual spacers when enabled, not merely a configuration value. It asserts ordinary `renderMessages({preserveScroll:true})` displacement, stale-session rejection, above-anchor post-processing growth, actual `_loadOlderMessages()` prepend, repeated busy transcript growth and settled rerender while reading history, explicit navigation to the tail, and body/pane horizontal overflow. Missing semantic anchors fail rather than silently passing. JSON metrics and before/after-operation PNGs are written to the evidence directory.

## Verification status (time-boxed)

The focused supported-runner suite passed **44 tests**; both changed JavaScript files passed Node syntax checks and the new Python files passed Ruff. The strengthened Chromium candidate matrix passed **9/12 cases**, not the full gate: all three desktop virtualization-on cases had nonzero raw `renderDelta` (61, -60, 61 px) and a missing post-prepend anchor. All narrow cases and desktop virtualization-off cases passed, with zero body/pane horizontal overflow. Stale-session displacement and post-processing displacement were zero in every candidate case. Streaming/settlement anchor displacement and explicit tail-navigation distance were also zero throughout.

**Do not treat this as complete scroll-jump certification.** The desktop virtual failures require distinguishing legitimate scrollTop compensation for changed virtual spacer measurements from actual semantic viewport movement, and investigating the missing prepend anchor. No blanket scroll freeze or virtualization disablement was introduced to hide these failures. The browser test intentionally remains red until that follow-up is resolved.

Limits: the stream-growth fixture uses the production transcript renderer with deterministic message updates, not a real model/SSE connection; activity modes are selected but this fixture does not exhaust every worklog/tool-card transition. The post-processing growth is deliberately injected to isolate ownership. Late asynchronous image/math reflow, real touch gestures, Firefox/Safari, and production-network behavior require separate coverage. No production state is written.
