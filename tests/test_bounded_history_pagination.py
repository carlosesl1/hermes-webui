"""Execute the real older-load function with deterministic browser/API fixtures."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('scenario', ['stable', 'shifted', 'generation', 'switched', 'tool-shifted'])
def test_cursor_first_older_load(scenario):
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required')
    path = Path(os.environ.get('BOUNDED_SESSIONS_SOURCE', Path(__file__).parents[1] / 'static/sessions.js'))
    source = path.read_text()
    start = source.index('async function _loadOlderMessages()')
    function = source[start:source.index('\n}', start)+2]
    script = r'''
const assert = require('node:assert/strict');
const scenario = SCENARIO;
const rows = Array.from({length: 120}, (_, i) => ({role:'assistant', content:'row '+i, timestamp:i}));
const S = {session:{session_id:'test', tool_calls:[{id:'tail-tool', assistant_msg_idx:0}]}, messages:rows.slice(90)};
let _loadingOlder=false, _messagesTruncated=true, _oldestIdx=90, _messagesGeneration=1, _loadingSessionId=null;
let _INITIAL_MSG_LIMIT=30, _msgLimitMax=500, _messageRenderWindowSize=30, MESSAGE_RENDER_WINDOW_DEFAULT=30, _scrollPinned=false;
const window={}, calls=[];
const $=()=>null, renderMessages=()=>{}, _currentMessageRenderWindowSize=()=>30;
const msgContent=m=>m.content, _sameTranscriptMessage=(a,b)=>a.role===b.role&&a.content===b.content;
const _syncToolCallsForLoadedMessages=(rows, tools)=>{S.session.tool_calls=tools;};
async function api(url, options) {
  const q=new URL(url,'http://fixture').searchParams;
  calls.push({before:q.get('msg_before'),limit:Number(q.get('msg_limit')),timeout:options.timeoutMs});
  if(scenario==='generation') _messagesGeneration++;
  if(scenario==='switched') S.session={session_id:'other'};
  return {session:{messages:rows.slice(60,90),_messages_offset:60,_messages_truncated:true,
    _messages_boundary:scenario==='tool-shifted'?{...rows[90],tool_calls:[{id:'different'}]}:scenario==='shifted'?rows[89]:rows[90],tool_calls:[{id:'older-tool',assistant_msg_idx:0}]}};
}
FUNCTION
(async()=>{
  await _loadOlderMessages();
  assert.deepEqual(calls,[{before:'90',limit:30,timeout:120000}]);
  assert.equal(_loadingOlder,false);
  if(scenario==='stable') {
    assert.deepEqual(S.messages,rows.slice(60));
    assert.equal(_oldestIdx,60);
    assert.equal(S.session.tool_calls.find(t=>t.id==='tail-tool').assistant_msg_idx,30);
  } else {
    assert.deepEqual(S.messages,rows.slice(90));
    assert.equal(_oldestIdx,90);
  }
})().catch(e=>{console.error(e);process.exit(1)});
'''.replace('SCENARIO', json.dumps(scenario)).replace('FUNCTION', function)
    proc = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stdout + proc.stderr
