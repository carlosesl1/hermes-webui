"""Done transport budgets do not mutate canonical transcript/context."""
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.helpers import redact_session_data
from api.render_payload import bounded_settlement_session, SETTLEMENT_ROWS
from api.run_journal import RunJournalWriter, read_run_events
from api.streaming import _session_payload_with_full_messages


class Session(SimpleNamespace):
    def compact(self):
        return {'session_id': self.session_id, 'message_count': 1,
                'user_message_count': sum(m['role'] == 'user' for m in self.messages)}


class TailOnly(list):
    def __iter__(self):
        raise AssertionError('walked canonical history')

    def __getitem__(self, key):
        assert isinstance(key, slice) and key.start >= len(self) - SETTLEMENT_ROWS
        return super().__getitem__(key)


def make_session():
    rows = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': 'x' * 20000,
             '_ts': i} for i in range(2000)]
    rows[-1]['content'] = 'final answer ' * 10000
    rows[-1]['_anchor_activity_scene'] = {'version': 1, 'message_index': 1999,
                                         'anchor_id': 'run-final'}
    return Session(session_id='bounded', messages=rows, context_messages=rows[:])


def test_budget_before_after_and_canonical_full_preserved(tmp_path):
    s = make_session()
    before = redact_session_data(_session_payload_with_full_messages(s))
    after = redact_session_data(bounded_settlement_session(s))
    before_bytes = len(json.dumps(before).encode())
    after_bytes = len(json.dumps(after).encode())
    print(f'full_bytes={before_bytes} done_bytes={after_bytes}')
    assert before_bytes > 40_000_000
    assert after_bytes < 280_000
    assert after['message_count'] == 2000
    assert after['_messages_offset'] == 1970
    assert after['_messages_truncated'] is True
    assert after['has_more'] is True
    assert after['messages'][-1]['_content_truncated']
    assert after['messages'][-1]['_anchor_activity_scene']['message_index'] == 1999
    assert len(s.messages[-1]['content']) == len('final answer ' * 10000)
    assert s.context_messages[-1] == s.messages[-1]
    assert before['messages'][-1]['content'] == s.messages[-1]['content']
    writer = RunJournalWriter('bounded', 'run', session_dir=tmp_path)
    writer.append_sse_event('done', {'session': after})
    replay = read_run_events('bounded', 'run', session_dir=tmp_path)
    assert replay['events'][-1]['payload']['session'] == after
    assert sum(p.stat().st_size for p in tmp_path.rglob('*.jsonl')) < 285000


def test_only_tail_is_visited_and_inline_blobs_are_not_journalled(monkeypatch):
    s = make_session()
    s.messages[-2]['content'] = [{'type': 'image_url', 'image_url': {
        'url': 'data:image/png;base64,' + 'A' * 1000000}}]
    s.messages = TailOnly(s.messages)
    import api.helpers as helpers
    original = helpers._redact_messages
    visited = []
    def inspect_redaction(rows, **kwargs):
        visited.append(len(rows))
        assert len(json.dumps(rows)) < 280000
        return original(rows, **kwargs)
    monkeypatch.setattr(helpers, '_redact_messages', inspect_redaction)
    payload = redact_session_data(bounded_settlement_session(s))
    assert visited == [SETTLEMENT_ROWS]
    assert len(json.dumps(payload)) < 280000
    assert len(payload['messages']) == SETTLEMENT_ROWS
    assert payload['messages'][-2]['_content_truncated']


@pytest.mark.skipif(shutil.which('node') is None, reason='node required')
def test_frontend_coordinate_merge_and_gap_pages():
    source = Path('static/messages.js').read_text()
    helpers = source[:source.index('function _markSessionViewed')]
    script = helpers + r'''
const assert=require('node:assert/strict');
const old=Array.from({length:100},(_,i)=>({role:'assistant',content:'row '+i}));
const incoming={session_id:'s',_settlement_window:'tail_v1',message_count:102,
  _messages_offset:72,messages:Array.from({length:30},(_,i)=>({role:'assistant',content:'row '+(i+72)}))};
const merged=_mergeSettlementWindow(old,incoming,0,true);
assert.equal(merged.messages.length,102);assert.equal(merged._messages_offset,0);
assert.equal(merged.messages[10],old[10]);assert.equal(merged._messages_truncated,false);
assert.equal(_mergeSettlementWindow(old.slice(60),incoming,60,true)._messages_offset,60);
assert.equal(_mergeSettlementWindow(old,incoming,0,false)._messages_offset,72);
const replay=_mergeSettlementWindow(merged.messages,incoming,0,true);
assert.deepEqual(replay.messages,merged.messages);
const clipped={...incoming,messages:incoming.messages.map(x=>({...x}))};
clipped.messages[0]={role:'assistant',content:'row ',_content_truncated:true};
assert.equal(_mergeSettlementWindow(old,clipped).messages[72].content,'row 72');
const restored=_mergeSettlementWindow(old,clipped);
assert.equal(_mergeSettlementWindow(restored.messages,clipped).messages[72].content,'row 72');
const toolMerge=_mergeSettlementWindow(old.slice(60),{...incoming,tool_calls:[{id:'new',assistant_msg_idx:100}]},60,true,[{id:'prior',assistant_msg_idx:2}]);
assert.deepEqual(toolMerge.tool_calls.map(t=>t.assistant_msg_idx),[2,40]);
assert.equal(toolMerge.has_more,true);
const newer=old.concat([{role:'user',content:'next'},{role:'assistant',content:'new'},{role:'user',content:'newest'}]);
const olderReplay=_mergeSettlementWindow(newer,incoming);
assert.equal(olderReplay.messages.length,103);assert.equal(olderReplay.messages[102].content,'newest');
const blocks=[{role:'assistant',content:[{type:'text',text:'full block'}],reasoning:'full reasoning'}];
const blockPreview={...incoming,_messages_offset:0,message_count:1,messages:[{role:'assistant',content:[{type:'text',text:'full'}],reasoning:'full',_content_truncated:true}]};
assert.equal(_mergeSettlementWindow(blocks,blockPreview).messages[0].content[0].text,'full block');
assert.equal(_mergeSettlementWindow(blocks,blockPreview).messages[0].reasoning,'full reasoning');
let S={session:{session_id:'s'},messages:old,activeStreamId:'run'},_oldestIdx=0,calls=0,_loadSessionGeneration=1;
const gap={...incoming,_messages_offset:160,message_count:190};
assert.equal(_mergeSettlementWindow(old,gap).messages,old);
async function api(url){calls++;const before=Number(new URL(url,'http://local').searchParams.get('msg_before'));
 const start=Math.max(0,before-30);return {session:{_messages_offset:start,
 messages:Array.from({length:before-start},(_,i)=>({role:'assistant',content:'row '+(start+i)}))}};}
(async()=>{await _completeSettlementWindow(gap,'s');assert.equal(calls,2);
 assert.equal(gap._messages_offset,100);assert.equal(_mergeSettlementWindow(old,gap).messages.length,190);
 assert.equal(gap.messages.length,90);
 const failed={...incoming,_messages_offset:160};
 api=async()=>({session:{_messages_offset:160,messages:[]}});
 await assert.rejects(_completeSettlementWindow(failed,'s'),/Incomplete/);
 assert.equal(failed._messages_offset,160);
 for(const change of [()=>{S.session={session_id:'b'}},()=>{_loadSessionGeneration++},()=>{S.activeStreamId='new'}]){
   S={session:{session_id:'s'},messages:old,activeStreamId:'run'};
   let release;api=()=>new Promise(r=>release=r);
   const stale={...incoming,_messages_offset:160};
   const pending=_completeSettlementWindow(stale,'s');change();
   release({session:{session_id:'s',_messages_offset:130,messages:Array(30).fill({role:'tool',content:'x'})}});
   await pending;assert.equal(stale._messages_offset,160);
 }
 console.log('ok');})().catch(e=>{console.error(e);process.exit(1)});
'''
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_real_compact_and_all_done_producers_use_bounded_projection(monkeypatch):
    import ast
    from api.models import Session as RealSession
    # The actual compact implementation must not see/scan the original prefix.
    # A normal constructor is state-free; use it to populate compact metadata.
    import inspect
    assert 'session_id' in inspect.signature(RealSession).parameters
    session = RealSession(session_id='bounded-real')
    session.messages = TailOnly(make_session().messages)
    wire = redact_session_data(bounded_settlement_session(session))
    assert len(wire['messages']) == SETTLEMENT_ROWS
    assert wire['message_count'] == 2000
    assert 'user_message_count' not in wire
    assert 'regeneration_revision' not in wire
    # Execute the actual production projection call expressions, not a mock of
    # the helper. The surrounding worker/provider loops are deliberately absent.
    calls = []
    for name in ('api/streaming.py', 'api/gateway_chat.py'):
        tree = ast.parse(Path(name).read_text())
        calls.extend(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name)
                     and n.func.id == 'bounded_settlement_session')
    assert len(calls) == 3
    session.tool_calls = []
    for call in calls:
        result = eval(compile(ast.Expression(call), '<done-producer>', 'eval'),
                      {'bounded_settlement_session': bounded_settlement_session,
                       's': session, 'tool_calls': []})
        assert redact_session_data(result) == wire


def test_empty_short_and_legacy_card_coordinates():
    for count in (0, 1, 30, 31):
        session = Session(session_id='small', messages=[{'role':'assistant','content':'ok'}] * count)
        cards = [{'assistant_msg_idx':i,'snippet':'x' * 20000} for i in range(count)]
        wire = bounded_settlement_session(session, tool_calls=cards)
        assert wire['message_count'] == count
        assert wire['has_more'] == (count > SETTLEMENT_ROWS)
        assert wire['_messages_offset'] == max(0, count-SETTLEMENT_ROWS)
        assert all(wire['_messages_offset'] <= t['assistant_msg_idx'] < count for t in wire['tool_calls'])


@pytest.mark.skipif(shutil.which('node') is None, reason='node required')
def test_done_callback_rechecks_navigation_before_any_dom_work():
    source = Path('static/messages.js').read_text()
    start = source.index('      const _doneLoadGeneration=')
    end = source.index('        // Bug A fix:', start)
    callback = source[start:end] + 'touched++;};'
    script = r"""
const assert=require('node:assert/strict');
let _loadSessionGeneration=1,touched=0,closed=0,release;
const _doneData={session:{}},activeSid='a',source={};
const _completeSettlementWindow=()=>new Promise(r=>release=r);
const _bailOutOfTerminalEventsFromStaleStream=()=>false;
const _scheduleAnchorRegistryCleanup=()=>{};
const _closeSource=()=>closed++;
""" + callback + r"""
(async()=>{const pending=_finishDone();_loadSessionGeneration++;release();await pending;
assert.equal(touched,0);assert.equal(closed,1);})().catch(e=>{console.error(e);process.exit(1)});
"""
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
