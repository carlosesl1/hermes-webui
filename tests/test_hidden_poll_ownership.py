"""Execute the actual poll owner against controllable transport/timers."""
import json
import subprocess
from pathlib import Path

import pytest

SOURCE = (Path(__file__).resolve().parents[1] / 'static/messages.js').read_text()
FUNCTIONS = SOURCE[SOURCE.index('function _startHiddenActiveStreamPoll(sid) {'):SOURCE.index('function _chatStreamActiveForSession(')]


@pytest.mark.parametrize('status', [404, 410, 500, 503, 0, 200])
@pytest.mark.parametrize('replacement', [None, 'A', 'B'])
def test_hidden_poll_owner(status, replacement):
    script = r'''
const document={hidden:true}, S={activeStreamId:null};
let _sessionStreamHiddenPollSid=null, _sessionStreamHiddenPollTimer=null;
let _sessionStreamHiddenPollGeneration=0;
let _sessionStreamHiddenPollFalseStreamId=null, _sessionStreamHiddenPollFalseCount=0;
const _SESSION_STREAM_HIDDEN_POLL_MAX_FALSE=5;
let resolve, reject, tick;
const fetch=()=>new Promise((a,b)=>{resolve=a;reject=b;});
const setInterval=fn=>{tick=fn;return {};}, clearInterval=()=>{};
const _apiUrl=x=>x, _attachServerInitiatedStream=()=>false;
''' + FUNCTIONS + r'''
(async()=>{
  _startHiddenActiveStreamPoll('A');
  const oldResolve=resolve, oldReject=reject, oldTick=tick;
  if(REPLACEMENT) _startHiddenActiveStreamPoll(REPLACEMENT);
  if(STATUS===0) oldReject(new Error('offline'));
  else oldResolve({status:STATUS,ok:STATUS===200,json:async()=>({active_stream_id:null})});
  await new Promise(r=>setTimeout(r,0));
  if(REPLACEMENT) oldTick();
  console.log(JSON.stringify({owner:_sessionStreamHiddenPollSid,timer:!!_sessionStreamHiddenPollTimer}));
})();
'''
    script = 'const STATUS='+str(status)+', REPLACEMENT='+json.dumps(replacement)+';\n'+script
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True)
    value = json.loads(result.stdout)
    terminal = replacement is None and status in (404, 410)
    assert value == {'owner': None if terminal else replacement or 'A', 'timer': not terminal}
