"""Static frontend tests for the in-chat todos tray (feat/todos-in-chat).

These verify that the chat-embedded task tray wiring stays intact:
  - the tray DOM exists in index.html next to the message shell
  - the settings checkbox exists and is wired in loadSettingsPanel
  - the scheduler fans out to renderChatTodos on every todo_state refresh
  - the render path escapes user content and marks terminal states
  - the rail-hide helper targets [data-panel="todos"] with nav-tab-hidden
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


def _read_static(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


# ── Behavior probes: the real frontend functions, run under node ──────────
# The maintainer reproduced the imported-list regression "with the real
# frontend functions", so these extract the shipped source of the functions
# under test and run them instead of asserting on their text.


def _extract(source: str, start_marker: str, end_marker: str) -> str:
    start = source.find(start_marker)
    assert start != -1, f"missing {start_marker!r}"
    end = source.find(end_marker, start)
    assert end != -1, f"missing {end_marker!r} after {start_marker!r}"
    return source[start:end]


def _run_node(tmp_path: Path, name: str, script: str) -> str:
    if shutil.which("node") is None:
        pytest.skip("node is required for the frontend behavior probe")
    script_path = tmp_path / name
    script_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        ["node", str(script_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout


_CURRENT_TODOS_PROBE = """
const S = {todos: [], todoStateMeta: null, messages: [], session: null};
__LEGACY__
__HELPER__
function assert(cond, msg) { if (!cond) throw new Error(msg); }

// Case A — the regression: hydration installs an empty S.todos for an imported
// session whose tasks only exist as role:"tool" messages, with no todoStateMeta.
S.session = {messages: [{role: 'tool', content: JSON.stringify({todos: [{id: 'a', content: 'imported', status: 'pending'}]})}]};
S.messages = S.session.messages;
S.todos = [];
S.todoStateMeta = null;
let got = _currentTodos();
assert(got.length === 1 && got[0].id === 'a', 'imported tool-message list must fall back to the legacy renderer');

// Case B — an explicit empty snapshot still wins (no spurious legacy revival).
S.todoStateMeta = {ts: 1, source: 'cold-load', version: 1};
assert(_currentTodos().length === 0, 'explicit empty snapshot must win');

// Case C — an explicit non-empty snapshot is returned as-is.
S.todos = [{id: 'x', content: 'X', status: 'pending'}];
got = _currentTodos();
assert(got.length === 1 && got[0].id === 'x', 'explicit snapshot must be returned');

// Case D — no snapshot AND no legacy messages => empty.
S.todoStateMeta = null;
S.todos = [];
S.session = {messages: []};
S.messages = [];
assert(_currentTodos().length === 0, 'no signal and no legacy list => empty');
console.log('ok');
"""


def test_current_todos_falls_back_to_legacy_for_imported_tool_lists(tmp_path):
    ui = _read_static("static/ui.js")
    panels = _read_static("static/panels.js")
    helper = _extract(ui, "function _currentTodos(){", "function _chatTodosSummary(")
    legacy = panels[panels.find("function _legacyTodosFromMessages() {"):]
    legacy = legacy[: legacy.find("\n}") + 2]
    assert legacy.rstrip().endswith("}")
    script = _CURRENT_TODOS_PROBE.replace("__LEGACY__", legacy).replace("__HELPER__", helper)
    assert _run_node(tmp_path, "current_todos_probe.js", script).strip() == "ok"


_EXPANDED_PROBE = """
const head = {attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }};
const tray = {
  classes: new Set(),
  classList: {
    add(c) { tray.classes.add(c); },
    remove(c) { tray.classes.delete(c); },
    contains(c) { return tray.classes.has(c); },
  },
};
function $(id) { return id === 'chatTodosPanel' ? tray : (id === 'chatTodosHead' ? head : null); }
__HELPER__
function assert(cond, msg) { if (!cond) throw new Error(msg); }
_syncChatTodosExpanded(true);
assert(tray.classes.has('open'), 'expand adds .open');
assert(head.attrs['aria-expanded'] === 'true', 'expand sets aria-expanded=true');
_syncChatTodosExpanded(false);
assert(!tray.classes.has('open'), 'collapse removes .open');
assert(head.attrs['aria-expanded'] === 'false', 'collapse sets aria-expanded=false');
console.log('ok');
"""


def test_sync_chat_todos_expanded_keeps_aria_in_sync(tmp_path):
    ui = _read_static("static/ui.js")
    helper = _extract(ui, "function _syncChatTodosExpanded(", "function _chatTodosToggleEnabled(")
    script = _EXPANDED_PROBE.replace("__HELPER__", helper)
    assert _run_node(tmp_path, "expanded_probe.js", script).strip() == "ok"


# ── Re-gate 2026-10-07T14:33:07Z (head cfd6f65c) — the three remaining items ──
# Each pins one finding, and where the maintainer reproduced it "in Chromium"
# the probe runs the shipped function under node instead of asserting on text.


_TOGGLE_CHIPS_PROBE = """
function assert(cond, msg) { if (!cond) throw new Error(msg); }
let chipsRendered = 0;
let enabledSet = null;
let expandedCalls = [];
let renderCalls = 0;
function _setChatTodosEnabled(v) { enabledSet = !!v; }
function _syncChatTodosExpanded(v) { expandedCalls.push(!!v); }
function renderChatTodos() { renderCalls++; }
let autosaves = 0;
function _scheduleAppearanceAutosave() { autosaves++; }
function $() { return null; }
var _renderTabVisibilityChips = function () { chipsRendered++; };
__HELPER__
_chatTodosToggleEnabled(true);
assert(enabledSet === true, 'tray preference must be written');
assert(chipsRendered === 1, 'enabling the tray must repaint the visibility chips');
assert(expandedCalls.length === 1 && expandedCalls[0] === false, 're-enable restarts collapsed');
assert(renderCalls === 1, 'tray contents are repainted');
_chatTodosToggleEnabled(false);
assert(enabledSet === false, 'disabling writes through');
assert(chipsRendered === 2, 'disabling must repaint the chips too');
// Reviewer re-gate 2026-10-08T23:19:27Z (static/ui.js:11074): the tray toggle
// must NOT schedule an appearance autosave — that save POSTs this client's
// hidden_tabs mirror, which can still hold another profile's stale snapshot,
// clobbering a newer server preference.
assert(autosaves === 0, 'toggling the tray must not autosave the appearance payload');
console.log('ok');
"""


def test_chat_todos_toggle_repaints_chips_probe(tmp_path):
    ui = _read_static("static/ui.js")
    helper = _extract(
        ui, "function _chatTodosToggleEnabled(checked){", "function _chatTodosSummary("
    )
    script = _TOGGLE_CHIPS_PROBE.replace("__HELPER__", helper)
    assert _run_node(tmp_path, "toggle_chips_probe.js", script).strip() == "ok"


# ── Tray box lifecycle: the pill must follow the ACTUAL box ───────────────
# Re-gate 2026-10-07T23:49:23Z: "make pill placement follow the tray's actual
# box through those two schedules" — (1) resizing ACROSS the tray breakpoint and
# (2) a hidden tray becoming visible after Settings edits hydrate the list. Both
# bypass renderChatTodos()/toggleChatTodos(), where the height used to be
# measured, so the published --chat-todos-h stranded a stale value (236px while
# the strip was 276px; 77px while it was 276px) and the Start pill painted inside
# the task rows. These probes run the shipped functions under node and drive
# both schedules through the observer, instead of asserting on source text.

_TRAY_HEIGHT_PROBE = """
function assert(cond, msg) { if (!cond) throw new Error(msg); }
const shellClasses = new Set();
const shellStyle = {
  _v: {},
  setProperty(k, v) { this._v[k] = v; },
  removeProperty(k) { delete this._v[k]; },
  getPropertyValue(k) { return this._v[k] || ''; },
};
const shell = {
  classList: {
    add(c) { shellClasses.add(c); },
    remove(c) { shellClasses.delete(c); },
    contains(c) { return shellClasses.has(c); },
    toggle(c, on) { if (on) shellClasses.add(c); else shellClasses.delete(c); return !!on; },
  },
  style: shellStyle,
};
// The strip's real box, mutable so the probe can drive the transitions.
let boxHeight = 36;
const tray = { getBoundingClientRect() { return { height: boxHeight }; } };
function $(id) { return id === 'chatTodosPanel' ? tray : null; }
const document = { querySelector(sel) { return sel === '.messages-shell' ? shell : null; } };
const observers = [];
class ResizeObserver {
  constructor(cb) { this.cb = cb; this.el = null; observers.push(this); }
  observe(el) { this.el = el; }
  disconnect() { this.el = null; }
}
let repins = 0;
function _repinMessagesAfterComposerResize() { repins++; }
function fire() { observers.forEach(function (ro) { ro.cb([]); }); }
__HELPER__

// A. A render publishes the measured collapsed band.
_syncChatTodosShellClass(true);
assert(shellStyle.getPropertyValue('--chat-todos-h') === '36px', 'render publishes the measured strip height');

// The observer is lifecycle-owned: set up once, for the tray element.
_ensureChatTodosResizeObserver();
_ensureChatTodosResizeObserver();
assert(observers.length === 1, 'exactly one lifecycle observer, not one per render');
assert(observers[0].el === tray, 'the observer watches the tray element');

// B. Resize ACROSS the tray breakpoint (393 -> 1440) with no render/toggle call.
// The strip grows, so the published height must follow the real box.
boxHeight = 276;
fire();
assert(shellStyle.getPropertyValue('--chat-todos-h') === '276px',
  'a breakpoint resize republishes the real box instead of stranding 236px');
assert(repins >= 1, 'the observer schedule re-pins the transcript');

// C. Hidden -> visible. While chat is hidden the strip measures 0.
boxHeight = 0;
const hiddenPublished = shellStyle.getPropertyValue('--chat-todos-h');
const repinsWhileHidden = repins;
fire();
assert(shellStyle.getPropertyValue('--chat-todos-h') === hiddenPublished,
  'a hidden (0px) box must not clobber the published height with 0');
assert(repins === repinsWhileHidden, 'a hidden box does not re-pin');
// Returning to chat re-fires the observer with the real box.
boxHeight = 276;
fire();
assert(shellStyle.getPropertyValue('--chat-todos-h') === '276px',
  'hidden -> visible publishes the visible height, not the stale 77px');
assert(repins > repinsWhileHidden, 'hidden -> visible re-pins the transcript too');

// D. Turning the tray off clears the offset and the shell marker class.
_syncChatTodosShellClass(false);
assert(shellStyle.getPropertyValue('--chat-todos-h') === '', 'tray off clears the published height');
assert(!shell.classList.contains('chat-todos-visible'), 'tray off drops the shell marker class');
console.log('ok');
"""


def test_chat_todos_pill_follows_the_tray_box_lifecycle(tmp_path):
    ui = _read_static("static/ui.js")
    helper = _extract(
        ui, "let _chatTodosResizeObserver=null;", "function _syncChatTodosExpanded("
    ) + _extract(
        ui, "function _repinChatTodosTranscript(){", "function renderChatTodos(){"
    ) + _extract(
        # The shipped observer also refreshes the overflow cue now
        # (Greptile P2 2026-10-09T21:45:10Z), so the probe must provide it.
        ui, "function _updateChatTodosScrollCue(){", "function scheduleTodosRefresh(){"
    )
    script = _TRAY_HEIGHT_PROBE.replace("__HELPER__", helper)
    assert _run_node(tmp_path, "tray_height_probe.js", script).strip() == "ok"


# ── Greptile re-review of the post-master-merge head (2026-10-09T21:45:10Z) ──
# P2 "Overflow fade misses size changes": the tray's resize callback republished
# the height and re-pinned the transcript but never refreshed the bottom fade,
# so a list that became scrollable through a viewport/composer change kept the
# cue hidden while scrollTop was still 0.

_TRAY_CUE_PROBE = """
function assert(cond, msg) { if (!cond) throw new Error(msg); }
const shellClasses = new Set(['chat-todos-visible']);
const shellStyle = {
  _v: {},
  setProperty(k, v) { this._v[k] = v; },
  removeProperty(k) { delete this._v[k]; },
  getPropertyValue(k) { return this._v[k] || ''; },
};
const shell = {
  classList: {
    add(c) { shellClasses.add(c); },
    remove(c) { shellClasses.delete(c); },
    contains(c) { return shellClasses.has(c); },
    toggle(c, on) { if (on) shellClasses.add(c); else shellClasses.delete(c); return !!on; },
  },
  style: shellStyle,
};
const tray = { getBoundingClientRect() { return { height: 276 }; } };
function $(id) { return id === 'chatTodosPanel' ? tray : null; }
const document = { querySelector(sel) { return sel === '.messages-shell' ? shell : null; } };
const observers = [];
class ResizeObserver {
  constructor(cb) { this.cb = cb; this.el = null; observers.push(this); }
  observe(el) { this.el = el; }
  disconnect() { this.el = null; }
}
let repins = 0;
function _repinChatTodosTranscript() { repins++; }
__HELPER__

// Count the shipped calls instead of replacing the implementation.
const _pubShipped = _publishChatTodosHeight;
let pubs = 0;
_publishChatTodosHeight = function () { pubs++; return _pubShipped.apply(null, arguments); };
const _cueShipped = _updateChatTodosScrollCue;
let cues = 0;
_updateChatTodosScrollCue = function () { cues++; return _cueShipped.apply(null, arguments); };

_ensureChatTodosResizeObserver();
assert(observers.length === 1, 'exactly one lifecycle observer');
observers[0].cb([]);
assert(pubs === 1, 'the box change republishes the tray height');
assert(cues === 1, 'the box change must refresh the overflow cue too');

// A hidden tray measures 0: it republishes nothing and must not touch the cue.
shellClasses.delete('chat-todos-visible');
observers[0].cb([]);
assert(cues === 1, 'a hidden tray must not refresh the cue');
console.log('ok');
"""


def test_chat_todos_overflow_cue_follows_the_tray_box(tmp_path):
    """The bottom fade must be refreshed when the tray's box changes.

    Without it, a list that only becomes scrollable through a shorter viewport
    or a taller composer keeps the fade hidden (scrollTop is still 0), so the
    clipped list reads as the complete list.
    """
    ui = _read_static("static/ui.js")
    helper = _extract(
        ui, "let _chatTodosResizeObserver=null;", "function _syncChatTodosExpanded("
    ) + _extract(
        ui, "function _updateChatTodosScrollCue(){", "function scheduleTodosRefresh(){"
    )
    script = _TRAY_CUE_PROBE.replace("__HELPER__", helper)
    assert _run_node(tmp_path, "tray_cue_probe.js", script).strip() == "ok"


# ── Re-gate 2026-10-08T03:10:50Z (head a63ab699) ──────────────────────────
# One [SHOULD-FIX] and four UX asks. The engineering item is pinned by a node
# probe that drives the shipped functions; the UX asks are pinned on source +
# behaviour where they carry logic (the disabled field, the overflow cue, the
# centred column) so they cannot silently rot.


_REPIN_PROBE = """
function assert(cond, msg) { if (!cond) throw new Error(msg); }
globalThis.window = {_autoScrollFollow: false};
let boxHeight = 0;
const tray = {hidden: false, getBoundingClientRect() { return {height: boxHeight}; }};
function $(id) { return id === 'chatTodosPanel' ? tray : null; }
let repins = 0;
function _repinMessagesAfterComposerResize() { repins++; }
__HELPER__

// 1. Auto-follow OFF: even a real growth must not touch the scroll position.
//    The box is still recorded on the baseline (that is the whole point of the
//    re-gate fix), so the growth below is NOT replayed once follow turns on.
boxHeight = 276;
_repinChatTodosTranscript();
assert(repins === 0, 'auto-follow OFF must never re-pin');

// 2. Auto-follow ON: the next measured growth re-pins exactly once.
window._autoScrollFollow = true;
boxHeight = 400;
_repinChatTodosTranscript();
assert(repins === 1, 'a measured growth re-pins the transcript');
_repinChatTodosTranscript();
assert(repins === 1, 'an unchanged box does not re-pin again');

// 3. Collapsing shrinks the box. A growing viewport cannot strand a pinned
//    reader (scrollHeight - clientHeight only falls, and the browser clamps
//    scrollTop), so a shrink is not a repin either.
boxHeight = 36;
_repinChatTodosTranscript();
assert(repins === 1, 'a shrink must not re-pin');

// 4. Re-expanding grows it again.
boxHeight = 400;
_repinChatTodosTranscript();
assert(repins === 2, 're-expanding re-pins again');

// 5. Feature-OFF default: the tray is hidden, nothing rendered, so no path may
//    pull a reader who scrolled away back to the bottom.
repins = 0;
tray.hidden = true;
boxHeight = 0;
_repinChatTodosTranscript();
_repinChatTodosTranscript();
assert(repins === 0, 'a hidden tray (feature OFF) must not pull the reader down');

// 6. Hide-then-show with Auto-follow toggled (re-gate 2026-10-08T06:40:51Z).
//    Before the fix the follow gate returned BEFORE measuring, so a hide with
//    Auto-follow OFF left the stale pre-hide height on the books; re-showing
//    the tray at the SAME height then read "not grown" and skipped the re-pin,
//    stranding a pinned reader.
repins = 0;
tray.hidden = false;
window._autoScrollFollow = true;
boxHeight = 276;
_repinChatTodosTranscript();
assert(repins === 1, 'baseline: an expanded tray re-pins with follow ON');
window._autoScrollFollow = false;
tray.hidden = true; boxHeight = 0; // hidden WHILE follow is OFF
_repinChatTodosTranscript();
assert(repins === 1, 'a hide with follow OFF must not re-pin');
window._autoScrollFollow = true;
tray.hidden = false; boxHeight = 276; // re-shown at the SAME height
_repinChatTodosTranscript();
assert(repins === 2, 're-show after a follow-OFF hide re-pins (stale baseline fixed)');
console.log('ok');
"""


def test_chat_todos_repin_probe(tmp_path):
    ui = _read_static("static/ui.js")
    helper = _extract(
        ui, "let _chatTodosResizeObserver=null;", "function _syncChatTodosExpanded("
    ) + _extract(
        ui, "function _repinChatTodosTranscript(){", "function renderChatTodos(){"
    )
    script = _REPIN_PROBE.replace("__HELPER__", helper)
    assert _run_node(tmp_path, "chat_todos_repin_probe.js", script).strip() == "ok"


_SCROLL_CUE_PROBE = """
function assert(cond, msg) { if (!cond) throw new Error(msg); }
const classes = new Set();
const wrap = {classList: {
  toggle(c, on) { if (on) classes.add(c); else classes.delete(c); return !!on; },
  contains(c) { return classes.has(c); },
}};
const body = {scrollTop: 0, listeners: {}, addEventListener(t, f) { this.listeners[t] = f; }};
let sh = 600;
let ch = 200;
Object.defineProperty(body, 'scrollHeight', {get() { return sh; }});
Object.defineProperty(body, 'clientHeight', {get() { return ch; }});
function $(id) { return id === 'chatTodosBody' ? body : (id === 'chatTodosBodyWrap' ? wrap : null); }
__HELPER__
_wireChatTodosScrollCue();
assert(typeof body.listeners.scroll === 'function', 'the body scroll event drives the cue');
_updateChatTodosScrollCue();
assert(classes.has('chat-todos-overflowing'), 'content below the fold shows the cue');
body.scrollTop = 400;
_updateChatTodosScrollCue();
assert(!classes.has('chat-todos-overflowing'), 'the cue clears when the reader reaches the bottom');
sh = 150;
body.scrollTop = 0;
_updateChatTodosScrollCue();
assert(!classes.has('chat-todos-overflowing'), 'nothing to scroll => no cue');
console.log('ok');
"""


def test_chat_todos_scroll_cue_probe(tmp_path):
    ui = _read_static("static/ui.js")
    helper = _extract(ui, "let _chatTodosCueWired=false;", "function scheduleTodosRefresh(){")
    script = _SCROLL_CUE_PROBE.replace("__HELPER__", helper)
    assert _run_node(tmp_path, "chat_todos_scroll_cue_probe.js", script).strip() == "ok"


# ── Round-4 polish (reviewer re-gate 2026-10-08T08:44:28Z) ─────────────────
# The maintainer re-shot the tray in a realistic layout and asked for four
# one/two-line fixes before it goes to Nathan for visual sign-off.


# The exact tail sentence the disabled workspace-todos card carried in each
# locale before the re-gate; ask 4 dropped it everywhere.


# ── Stale tab-visibility mirror while a profile switch reconciles ──────────
# greptile P1 (2026-10-08T20:06:51Z, static/ui.js:10501): "When a user switches
# between profiles with different hidden_tabs settings and disables the tray
# before the asynchronous settings reconciliation completes, this path
# reapplies the previous profile's global localStorage snapshot." Releasing the
# tray's forced rail hide re-derives visibility from that mirror, so it must be
# skipped until the switch's /api/settings reconciliation lands.

_RAIL_RELEASE_PROBE = """
function assert(cond, msg) { if (!cond) throw new Error(msg); }
function _el() {
  const s = new Set();
  return {classList: {
    add(c) { s.add(c); },
    remove(c) { s.delete(c); },
    contains(c) { return s.has(c); },
    toggle(c, on) { if (on) s.add(c); else s.delete(c); return !!on; },
  }};
}
const todosEls = [_el(), _el()];
globalThis.document = {
  querySelectorAll(sel) { return sel === '[data-panel="todos"]' ? todosEls : []; },
  getElementById() { return null; },
  querySelector() { return null; },
};
let trayOn = false;
let stale = false;
let applied = [];
function chatTodosEnabled() { return trayOn; }
function _getHiddenTabs() { return ['notes']; }
function _applyTabVisibility(h) { applied.push(h.slice()); }
globalThis._tabVisibilitySnapshotStale = function () { return stale; };
__HELPER__

// 1. Settled mirror, tray OFF: visibility still goes back to the canonical
//    owner (the 2026-10-07 greptile P1 behaviour must survive).
_syncChatTodosRailVisibility();
assert(applied.length === 1 && applied[0].join() === 'notes',
  'a settled mirror must still hand visibility back to hidden_tabs');
assert(todosEls.every(function (el) { return !el.classList.contains('nav-tab-hidden'); }),
  'a settled release must not force the class either way');

// 2. THE FIX: the same click while the switch's reconciliation is in flight.
//    The mirror still holds the PREVIOUS profile's list, so re-deriving from it
//    would reimpose that profile's tab visibility on the profile now in effect.
applied = [];
stale = true;
_syncChatTodosRailVisibility();
assert(applied.length === 0,
  'the stale previous-profile mirror must not be re-applied mid-switch');

// 3. The reconciliation lands: the release is live again.
stale = false;
_syncChatTodosRailVisibility();
assert(applied.length === 1 && applied[0].join() === 'notes',
  'the release resumes once the reconciliation settles');

// 4. Tray ON is unaffected: it still force-hides the duplicate rail entry and
//    never re-derives visibility (that is the reconciliation's job).
todosEls.forEach(function (el) { el.classList.remove('nav-tab-hidden'); });
applied = [];
trayOn = true;
stale = true;
_syncChatTodosRailVisibility();
assert(todosEls.every(function (el) { return el.classList.contains('nav-tab-hidden'); }),
  'tray ON still force-hides the Todos rail entry mid-switch');
assert(applied.length === 0, 'tray ON never re-derives tab visibility');

// 5. No guard in scope (panels.js not loaded yet) keeps the previous behaviour.
trayOn = false;
applied = [];
delete globalThis._tabVisibilitySnapshotStale;
_syncChatTodosRailVisibility();
assert(applied.length === 1, 'without the guard the release still defers to hidden_tabs');
console.log('ok');
"""


def test_rail_release_skips_the_stale_mirror_during_a_profile_switch(tmp_path):
    """greptile P1 (2026-10-08T20:06:51Z): the tray-off release must not reapply
    the previous profile's hidden_tabs snapshot while the switch's
    /api/settings reconciliation is still in flight."""
    ui = _read_static("static/ui.js")
    helper = _extract(
        ui, "function _syncChatTodosRailVisibility(){", "let _chatTodosResizeObserver"
    )
    # The re-derive is wrapped in the reconciliation guard...
    guard_at = helper.find("_tabVisibilitySnapshotStale")
    call_at = helper.find("_applyTabVisibility(_getHiddenTabs())")
    assert guard_at != -1, "the tray release never consults the reconciliation guard"
    assert call_at != -1
    assert guard_at < call_at, "the guard must wrap the re-derive, not follow it"
    script = _RAIL_RELEASE_PROBE.replace("__HELPER__", helper)
    assert _run_node(tmp_path, "rail_release_probe.js", script).strip() == "ok"


# ---------------------------------------------------------------------------
# Greptile P1 (2026-10-10T02:22:20Z) — clearing a conversation must also clear
# the tray's tasks
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Gate certificate 2026-10-13 (RED) — a READABLE but STALE hidden-tabs mirror
# must never beat the hidden_tabs the reconciliation just resolved
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Greptile P1 (2026-10-15T23:18:37Z) — a conversation deleted elsewhere must
# not leave its tasks in the tray
# ---------------------------------------------------------------------------

_SEND_404_TODOS_PROBE = """
const S = {todos: [], todoStateMeta: null, messages: [], session: null};
const INFLIGHT = {};
let _refreshCalls = 0;
function _resetTodosRenderCache() {}
function scheduleTodosRefresh() { _refreshCalls += 1; }
__HYDRATE__
__CURRENT__
function assert(cond, msg) { if (!cond) throw new Error(msg); }

const activeSid = 's_deleted_elsewhere';
S.session = {session_id: activeSid};
S.todos = [{id: 'old', content: 'stale task', status: 'pending'}];
S.todoStateMeta = {ts: 7, source: 'sse', version: 1};
INFLIGHT[activeSid] = {todos: S.todos.slice(), todoStateMeta: S.todoStateMeta};

// (1) The pre-fix shape: /api/chat/start answers 404, the recovery path drops
// the session and the transcript, but S.todos + the todoStateMeta sentinel stay
// behind, so the tray keeps rendering the dead conversation's tasks.
S.session = null;
S.messages = [];
let stale = _currentTodos();
assert(stale.length === 1 && stale[0].id === 'old',
  'the stale snapshot is what the tray renders until it is reset');

// (2) The full recovery sequence: the in-flight payload is dropped, then the
// null hydration (same helper clearConversation/deleteSession use) empties the
// tray and reschedules its paint.
delete INFLIGHT[activeSid];
S.session = null;
S.messages = [];
_hydrateTodosFromSession(null);
assert(S.todoStateMeta === null, 'the snapshot sentinel must reset to null');
assert(S.todos.length === 0, 'the deleted session tasks must be gone, got ' + S.todos.length);
assert(_currentTodos().length === 0, 'the tray must render no tasks');
assert(_refreshCalls >= 1, 'a tray refresh must be scheduled');
console.log('ok');
"""


def test_deleted_elsewhere_session_drops_the_tray_tasks(tmp_path):
    """Greptile P1 (2026-10-15T23:18:37Z): when /api/chat/start answers 404 the
    session was deleted server-side; the recovery path cleared S.session and the
    transcript but not S.todos/S.todoStateMeta, so the tray kept the deleted
    conversation's list (rendered from the sentinel by _currentTodos)."""
    messages = _read_static("static/messages.js")
    ui = _read_static("static/ui.js")
    recovery = _extract(
        messages,
        "// If /api/chat/start returns 404, the session was deleted server-side",
        "const conflictActiveStream=",
    )
    assert "delete INFLIGHT[activeSid];" in recovery, recovery
    assert "S.session=null;S.messages=[];" in recovery, recovery
    reset_at = recovery.find("_hydrateTodosFromSession(null)")
    clear_at = recovery.find("S.session=null;S.messages=[];")
    assert reset_at != -1, "the 404 recovery must reset the tray snapshot"
    assert reset_at > clear_at, (
        "the tray reset must follow the session + transcript clear"
    )
    hydrate = _extract(
        ui,
        "function _hydrateTodosFromSession(session){",
        "function snapshotLiveTurnHtmlForSession(sid){",
    )
    current = _extract(ui, "function _currentTodos(){", "function _chatTodosSummary(")
    script = (
        _SEND_404_TODOS_PROBE.replace("__HYDRATE__", hydrate)
        .replace("__CURRENT__", current)
    )
    assert _run_node(tmp_path, "send_404_todos_probe.js", script).strip() == "ok"


def test_opt_in_default_and_missing_shell_are_safe(tmp_path):
    ui = _read_static("static/ui.js")
    pref = _extract(ui, "const CHAT_TODOS_LS_KEY=", "function _syncChatTodosRailVisibility(){")
    render = _extract(ui, "function renderChatTodos(){", "function toggleChatTodos(){")
    script = r"""
const assert=require('node:assert/strict');
const values=new Map();
const localStorage={getItem:k=>values.has(k)?values.get(k):null,setItem:(k,v)=>values.set(k,v)};
const document={};
const $=()=>null;
__PREF__
__RENDER__
assert.equal(chatTodosEnabled(),false);
renderChatTodos(); // shell can be omitted; no data or dependencies touched
_setChatTodosEnabled(true);
assert.equal(values.get('hermes-webui-chat-todos'),'1');
_chatTodosEnabled=null;
assert.equal(chatTodosEnabled(),true);
_setChatTodosEnabled(false);
assert.equal(values.get('hermes-webui-chat-todos'),'0');
_chatTodosEnabled=null;
assert.equal(chatTodosEnabled(),false);
localStorage.getItem=()=>{throw new Error('storage blocked');};
_chatTodosEnabled=null;
assert.equal(chatTodosEnabled(),false);
renderChatTodos();
console.log('ok');
""".replace("__PREF__",pref).replace("__RENDER__",render)
    assert _run_node(tmp_path,"pref_missing_shell.js",script).strip()=="ok"
