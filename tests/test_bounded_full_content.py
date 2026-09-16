"""Execute explicit full-content expansion even after row paging completes."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("clipped_owner", ["messages", "tool_calls"])
def test_full_content_expansion_without_older_rows(clipped_owner):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required")
    source = (Path(__file__).parents[1] / "static/sessions.js").read_text()
    functions = []
    for name in ["function _syncToolCallsForLoadedMessages(", "async function _ensureAllMessagesLoaded(", "const _fullTranscriptLoads="]:
        start = source.index(name)
        functions.append(source[start:source.index("\n}", start)+2])
    script = """
const assert=require('node:assert/strict');
const S={session:{session_id:'test',tool_calls:[]},messages:[{role:'assistant',content:'preview'}]};
S[OWNER==='messages'?'messages':'session'][OWNER==='messages'?0:'tool_calls'] = OWNER==='messages'
  ? {role:'assistant',content:'preview',_content_truncated:true}
  : [{name:'tool',snippet:'preview',_content_truncated:true}];
let _loadSessionGeneration=1,_messagesTruncated=false,_loadingOlder=false,_loadingSessionId=null,_oldestIdx=0,requests=0,renders=0;
const window={},_bumpMessagesGeneration=()=>{},renderMessages=()=>renders++;
const full={role:'assistant',content:'z'.repeat(200000)};
async function api(url){assert(!url.includes('msg_limit'));requests++;return {session:{messages:[full],tool_calls:[{name:'tool',snippet:'r'.repeat(200000)}]}};}
FUNCTIONS
(async()=>{
  await _ensureAllMessagesLoaded();
  assert.equal(requests,1);
  assert.deepEqual(S.messages,[full]);
  assert.equal(S.session.tool_calls[0].snippet.length,200000);
  assert.equal(_loadingOlder,false);
  await expandFullTranscript();
  assert.equal(renders,1);
  assert.equal(requests,2);
})().catch(e=>{console.error(e);process.exit(1)});
""".replace("OWNER", repr(clipped_owner)).replace("FUNCTIONS", "\n".join(functions))
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
