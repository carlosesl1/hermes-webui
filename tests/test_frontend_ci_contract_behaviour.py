"""Execute frontend contracts rather than relying on fixed source windows."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(NODE is None, reason='node not on PATH')


def run_js(script):
    prelude = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
function source(file) { return fs.readFileSync('static/' + file + '.js', 'utf8'); }
function extract(file, name) {
  const src = source(file);
  const start = src.search(new RegExp('(?:async )?function ' + name + '\\('));
  assert.ok(start >= 0, name);
  const end = src.indexOf('\n}', start);
  assert.ok(end > start, name);
  return src.slice(start, end + 2);
}
"""
    result = subprocess.run([NODE, '-e', prelude + script], cwd=ROOT,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_reasoning_keyboard_uses_shared_ime_guard_and_keeps_navigation():
    run_js(r"""
const ui = source('ui');
const start = ui.indexOf('// Capture before global shortcuts:');
let handler;
const document = {addEventListener: (_, fn) => {handler = fn;}, activeElement: null};
let _imeComposing = false;
const helper = source('boot').match(/function _isImeEnter\(e\)\{[^}]+\}/)[0];
eval(helper);
const window = {_isImeEnter};
let clicked = 0, focused = null, closed = 0;
const options = [0, 1, 2].map(id => ({id, style: {}, click(){clicked++;}}));
const dd = {classList: {contains: () => true}, contains: () => true,
  querySelectorAll: () => options};
const $ = () => dd;
const _focusReasoningOption = opt => {focused = opt; document.activeElement = opt;};
const closeReasoningDropdown = () => {closed++;};
eval(ui.slice(start, ui.indexOf("document.addEventListener('focusin'", start)));
function key(key, extra = {}) {
  let prevented = false;
  handler({key, target: {closest: () => null}, preventDefault(){prevented = true;},
    stopImmediatePropagation(){}, ...extra});
  return prevented;
}
document.activeElement = options[0];
for (const extra of [{isComposing:true}, {keyCode:229}]) {
  assert.equal(key('Enter', extra), false);
  assert.equal(clicked, 0);
}
_imeComposing = true;
assert.equal(key('Enter'), false);
_imeComposing = false;
assert.equal(key('ArrowDown'), true); assert.equal(focused, options[1]);
key('End'); assert.equal(focused, options[2]);
key('Home'); assert.equal(focused, options[0]);
key('ArrowUp'); assert.equal(focused, options[0]);
key('Enter'); key(' '); assert.equal(clicked, 2);
assert.equal(key('Tab'), false); assert.equal(closed, 1);
assert.equal(key('Escape'), true); assert.equal(closed, 2);
""")


def test_render_messages_does_not_replace_loading_placeholder():
    run_js(r"""
let _lastMessageRenderAt = 0, _messageUserUnpinned = false;
const S = {session:{session_id:'loading'}, messages:[], busy:false};
const _loadingSessionId = 'loading';
const inner = {innerHTML:'Loading conversation...'};
const $ = id => {assert.equal(id, 'msgInner'); return inner;};
eval(extract('ui', 'renderMessages'));
renderMessages();
assert.equal(inner.innerHTML, 'Loading conversation...');
""")


def test_workspace_close_tooltip_tracks_preview():
    run_js(r"""
let preview = false, _workspacePanelMode = 'open', _workspacePanelReturnFocus = null;
const S = {session:{session_id:'s'}};
const document = {activeElement:null};
const clear = {style:{}, setAttribute(k,v){this[k]=v;}};
const panel = {classList:{contains:()=>false}, setAttribute(){}};
const $ = id => id === 'btnClearPreview' ? clear : null;
const _workspacePanelEls = () => ({layout:{}, panel});
const _isCompactWorkspaceViewport = () => false;
const _hasWorkspacePreviewVisible = () => preview;
const _uiText = (_, fallback) => fallback;
const _setButtonTooltip = (el, label) => {el.title=label;};
eval(extract('boot', 'syncWorkspacePanelUI'));
for (const [value, label] of [[false,'Close'], [true,'Close preview']]) {
  preview = value; syncWorkspacePanelUI();
  assert.equal(clear.title, label); assert.equal(clear['aria-label'], label);
  assert.equal(clear.disabled, false);
}
""")


def test_cursor_prepend_retains_and_reindexes_legacy_tool_owners():
    run_js(r"""
const S = {session:{session_id:'s', tool_calls:[{id:'tail-tool',assistant_msg_idx:0}]},
  messages:[{role:'assistant',content:'tail'}], busy:false};
const window = {};
let _loadingOlder=false, _messagesTruncated=true, _oldestIdx=2, _messagesGeneration=0;
const _loadingSessionId=null, _INITIAL_MSG_LIMIT=100, MESSAGE_RENDER_WINDOW_DEFAULT=100;
let _messageRenderWindowSize=100, _scrollPinned=true, renders=0;
const _currentMessageRenderWindowSize = () => _messageRenderWindowSize;
const _messageIsRenderable = () => true;
const $ = () => null;
const renderMessages = () => {renders++;};
const api = async url => {
  assert.ok(url.includes('msg_before=2'));
  return {session:{messages:[{role:'user',content:'old'}, {role:'assistant',content:'older'}],
    _messages_boundary:S.messages[0], _messages_offset:0, _messages_truncated:false,
    tool_calls:[{id:'old-tool',assistant_msg_idx:1}]}};
};
eval(extract('sessions', '_syncToolCallsForLoadedMessages'));
eval(extract('sessions', '_loadOlderMessages'));
(async () => {
  await _loadOlderMessages();
  assert.deepEqual(S.messages.map(m=>m.content), ['old','older','tail']);
  assert.deepEqual(S.toolCalls.map(t=>[t.id,t.assistant_msg_idx,t.done]),
    [['old-tool',1,true],['tail-tool',2,true]]);
  assert.deepEqual(S.session.tool_calls.map(t=>[t.id,t.assistant_msg_idx]),
    [['old-tool',1],['tail-tool',2]]);
  assert.equal(renders,1); assert.equal(_loadingOlder,false);
  assert.equal(_messagesTruncated,false); assert.equal(_oldestIdx,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")
