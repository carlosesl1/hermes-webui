# Background notification provenance at settlement

The WebUI owns notification provenance on the active turn (`pending_user_source`,
then the active-turn identity). Terminal completions and async delegation wakeups
both use `process_wakeup`; their text and canonical `user` role remain unchanged.

Core result echoes need not contain WebUI `_source` metadata. In deferred-save
mode there is no eager checkpoint. When the core supplies the exact current-turn
index/turn identity, `_settle_current_turn_boundary` marks that returned row as
owned. It must also stamp its source before `_align_current_turn_display` adopts
it as the display checkpoint. Otherwise the display merge treats the row as
already checkpointed, skips its usual source stamp, and persists a synthetic
notification as an ordinary human turn.

The fix stamps only the resolved current-turn checkpoint using the existing
`stamp_message_source` helper. It does not infer provenance from notification
text, change canonical roles/content/tools, alter delivery claims/ACKs, or
migrate historical rows. A human submitting identical text remains a human turn.
Provider-safe message projection strips the display metadata.

Regression: `tests/test_background_source_settlement.py` exercises the shared
settlement/alignment/display flow with core-compatible markerless echoes for
terminal and async-delegation bodies, with matching human-text controls. Both
context and display JSON round trips must retain their roles/content and correct
source. Baseline: **2 failed, 2 passed**; patched focused/neighbor run: **55 passed,
11 skipped**. Skips are optional core delegation-store coverage in the hermetic
runner (no live SQLite opened). Browser/API reload and a real provider wakeup
remain the parent integration gate; JSON round trips are not claimed as live QA.

Root locations at baseline `8f7f380`: `api/streaming.py:1986-1988` marked only the
active-turn token, `:2031-2043` adopted the source-less context checkpoint, and
`:7379-7396` skipped the echoed result before the ordinary `:7419` source stamp.
No core patch is required for this reproduced WebUI settlement defect.
