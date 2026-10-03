"""The explicit done-tail fallback stays recoverable through real cursor paging."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which('node') is None, reason='node required')
def test_fallback_tail_paginates_back_to_canonical_history_with_real_coordinates():
    root = Path(__file__).resolve().parents[1]
    source = (root / 'static/messages.js').read_text()
    helpers = source[:source.index('function _markSessionViewed')]
    sessions = (root / 'static/sessions.js').read_text()
    start = sessions.index('async function _loadOlderMessages()')
    paging = sessions[start:sessions.index('\n}', start) + 2]
    script = r'''
const assert=require('node:assert/strict');
const rows=Array.from({length:190},(_,i)=>({role:'assistant',content:'row '+i,timestamp:i}));
const canonical=JSON.stringify(rows);
const incoming={session_id:'s',_settlement_window:'tail_v1',_messages_offset:160,message_count:190,
  messages:rows.slice(160),tool_calls:[{id:'tail-card',assistant_msg_idx:189}]};
const merged=_mergeSettlementWindow(rows.slice(0,100),incoming,0,true,[{id:'prefix-card',assistant_msg_idx:1}]);
assert.deepEqual(merged.tool_calls,[{id:'tail-card',assistant_msg_idx:29}]);
let S={session:merged,messages:merged.messages};
let _loadingOlder=false,_messagesTruncated=merged._messages_truncated,_oldestIdx=merged._messages_offset;
let _messagesGeneration=1,_loadingSessionId=null,_INITIAL_MSG_LIMIT=30,_messageRenderWindowSize=30;
let MESSAGE_RENDER_WINDOW_DEFAULT=30,_scrollPinned=false;
const window={},calls=[],$=()=>null,renderMessages=()=>{},_currentMessageRenderWindowSize=()=>30;
const msgContent=m=>m.content;
const _syncToolCallsForLoadedMessages=(rows,tools)=>{S.session.tool_calls=tools;};
async function api(url){
  const q=new URL(url,'http://fixture').searchParams;
  assert.equal(q.get('msg_limit'),'30');assert.equal(q.get('msg_boundary'),'1');
  const before=Number(q.get('msg_before')),start=Math.max(0,before-30);calls.push(before);
  return {session:{session_id:'s',messages:rows.slice(start,before),_messages_offset:start,
    _messages_truncated:start>0,_messages_boundary:rows[before],tool_calls:[]}};
}
(async()=>{
  while(_messagesTruncated) await _loadOlderMessages();
  assert.deepEqual(calls,[160,130,100,70,40,10]);
  assert.deepEqual(S.messages,rows);assert.equal(_oldestIdx,0);
  assert.deepEqual(S.session.tool_calls,[{id:'tail-card',assistant_msg_idx:189}]);
  assert.equal(JSON.stringify(rows),canonical);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', helpers + paging + script],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
