"""Real backend projections must remain pageable across differing text budgets."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from api.render_payload import bounded_render_messages, bounded_settlement_session


class Session(SimpleNamespace):
    def compact(self):
        return {'session_id': self.session_id}


@pytest.mark.skipif(shutil.which('node') is None, reason='node required')
@pytest.mark.parametrize('mode', ['stable', 'changed-content', 'changed-tool', 'changed-id'])
def test_projected_tail_boundary_uses_canonical_identity_not_preview_length(mode):
    rows = [{'role': 'assistant', 'timestamp': i, 'content': [
        {'type': 'text', 'text': str(i) + ':' + 'x' * 8192} for _ in range(4)
    ]} for i in range(190)]
    original = copy.deepcopy(rows)
    incoming = bounded_settlement_session(Session(session_id='s', messages=rows))
    live = copy.deepcopy(rows)
    if mode == 'changed-content':
        # Change beyond BOTH displayed prefixes, retaining the same length.
        text = live[160]['content'][0]['text']
        live[160]['content'][0]['text'] = text[:-1] + 'y'
    elif mode == 'changed-tool':
        live[160]['tool_calls'] = [{'id': 'new-tool', 'function': {'arguments': '{}'}}]
    elif mode == 'changed-id':
        live[160]['id'] = 'new-occurrence'
    pages = {}
    for before in (160, 130, 100, 70, 40, 10):
        start = max(0, before - 30)
        pages[before] = {'session': {
            'session_id': 's', 'messages': bounded_render_messages(live[start:before]),
            '_messages_boundary': bounded_render_messages(live[before:before + 1])[0],
            '_messages_offset': start, '_messages_truncated': start > 0, 'tool_calls': [],
        }}
    assert len(incoming['messages'][0]['content'][0]['text']) < len(pages[160]['session']['_messages_boundary']['content'][0]['text'])
    root = Path(__file__).resolve().parents[1]
    source = (root / 'static/messages.js').read_text()
    helpers = source[:source.index('function _markSessionViewed')]
    sessions = (root / 'static/sessions.js').read_text()
    start = sessions.index('async function _loadOlderMessages()')
    paging = sessions[start:sessions.index('\n}', start) + 2]
    script = helpers + paging + r'''
const assert=require('node:assert/strict');
const {incoming,pages,mode}=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const merged=_mergeSettlementWindow(Array(100).fill({role:'user',content:'prefix'}),incoming);
let S={session:merged,messages:merged.messages};
let _loadingOlder=false,_messagesTruncated=true,_oldestIdx=160,_messagesGeneration=1,_loadingSessionId=null;
let _INITIAL_MSG_LIMIT=30,_messageRenderWindowSize=30,MESSAGE_RENDER_WINDOW_DEFAULT=30,_scrollPinned=false;
const window={},calls=[],warnings=[],$=()=>null,renderMessages=()=>{},_currentMessageRenderWindowSize=()=>30;
const msgContent=m=>m.content,_syncToolCallsForLoadedMessages=()=>{};
console.warn=msg=>warnings.push(msg);
async function api(url){
  const before=Number(new URL(url,'http://fixture').searchParams.get('msg_before'));
  calls.push(before);return pages[before];
}
(async()=>{
  for(let n=0;n<6&&_messagesTruncated;n++){
    const previous=_oldestIdx;await _loadOlderMessages();if(_oldestIdx===previous)break;
  }
  if(mode==='stable'){
    assert.deepEqual(calls,[160,130,100,70,40,10]);
    assert.equal(S.messages.length,190);assert.equal(_oldestIdx,0);assert.equal(warnings.length,0);
    assert.deepEqual(S.messages.map(m=>m.timestamp),Array.from({length:190},(_,i)=>i));
  }else{
    assert.deepEqual(calls,[160]);assert.equal(_oldestIdx,160);assert.equal(S.messages.length,30);
    assert.equal(warnings.length,1);
  }
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', script], input=json.dumps({
        'incoming': incoming, 'pages': pages, 'mode': mode,
    }), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert rows == original, 'projection cannot mutate canonical messages'


def test_boundary_identity_only_hashes_first_row_and_ignores_display_metadata(monkeypatch):
    import api.render_payload as projection

    original = projection._paging_identity
    visited = []

    def traced(message):
        visited.append(message['timestamp'])
        return original(message)

    monkeypatch.setattr(projection, '_paging_identity', traced)
    rows = [{'role': 'assistant', 'timestamp': i, 'content': '🙂' * 10000} for i in range(30)]
    tail = projection.bounded_render_messages(rows)
    assert visited == [0]
    assert '_paging_identity' in tail[0]
    assert all('_paging_identity' not in row for row in tail[1:])
    decorated = dict(rows[0], _anchor_activity_scene={'text': 'variable rendering data'})
    assert original(decorated) == tail[0]['_paging_identity']
    assert original(dict(reversed(list(rows[0].items())))) == tail[0]['_paging_identity']
    for content in ['ab', ['a', 'b'], ['ab'], {'a': 'b'}, None, 1, '1']:
        assert original(dict(rows[0], content=content)) != tail[0]['_paging_identity']
    assert len({original({'content': content}) for content in ['ab', ['a', 'b'], ['ab'], {'a': 'b'}, None, 1, '1']}) == 7


def test_boundary_fingerprint_does_not_copy_giant_string():
    import tracemalloc
    from api.render_payload import _paging_identity

    message = {'role': 'tool', 'content': '🙂' * 2000000}
    tracemalloc.start()
    try:
        result = _paging_identity(message)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.startswith('v1:') and len(result) == 67
    assert peak < 2 * 1024 * 1024, peak
