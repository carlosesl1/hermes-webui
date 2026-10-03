"""Audited frontend reproductions using real JS and backend preview projection."""
import copy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from api.render_payload import bounded_render_messages

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which('node') is None, reason='node required')


def run_js(script, payload):
    source = (ROOT / 'static/messages.js').read_text()
    helpers = source[:source.index('function _markSessionViewed')]
    harness = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const source=fs.readFileSync('static/messages.js','utf8');
function extract(name){
 const start=source.indexOf('function '+name+'(');if(start<0)throw Error(name);
 let i=source.indexOf('{',start),depth=1;
 for(let j=i+1;j<source.length;j++){if(source[j]==='{')depth++;if(source[j]==='}'&&!--depth)return source.slice(start,j+1);}
 throw Error('unclosed '+name);
}
'''
    result = subprocess.run(['node', '-e', harness + helpers + script],
                            input=json.dumps(payload), cwd=ROOT, capture_output=True,
                            text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('mode', [
    'missing', 'null', 'changed', 'second-join', 'prefix', 'adjacent-prefix',
    'valid', 'legacy', 'different-preview', 'prefix-preview', 'repeat', 'adjacent-valid',
])
def test_every_settlement_join_requires_continuity(mode):
    rows = [{'id': str(i), 'role': 'assistant' if i % 2 else 'user',
             'content': 'row ' + str(i), 'timestamp': i} for i in range(10)]
    if mode == 'repeat':
        rows[6]['content'] = rows[8]['content'] = 'Repeated question'
        rows[7]['content'] = rows[9]['content'] = 'Repeated answer'
    if mode == 'different-preview':
        rows[8]['content'] = 'x' * 20000
    prefix = copy.deepcopy(rows[:4])
    if mode in ('prefix', 'adjacent-prefix'):
        prefix[2]['content'] = 'stale local occurrence'
    if mode == 'prefix-preview':
        rows[2]['content'] = 'long boundary ' * 2000
        prefix[2] = bounded_render_messages([rows[2]])[0]
        prefix[2]['content'] = prefix[2]['content'][:1000]
    incoming = {'session_id': 's', '_settlement_window': 'tail_v1',
                '_messages_offset': 8, 'messages': bounded_render_messages(rows[8:], settlement=True),
                'message_count': 10, 'tool_calls': [{'id': 'tail-tool', 'assistant_msg_idx': 9}]}
    # Small pages force multiple joins; the last page overlaps the retained prefix.
    pages = {}
    for before, start in ((8, 6), (6, 4 if mode in ('adjacent-prefix', 'adjacent-valid') else 2), (4, 2)):
        page_rows = bounded_render_messages(rows[start:before])
        boundary = bounded_render_messages(rows[before:before + 1])[0]
        if mode == 'legacy':
            for row in page_rows + [boundary]:
                row.pop('_paging_identity', None)
        pages[before] = {'session': {'session_id': 's', '_messages_offset': start,
            'messages': page_rows, '_messages_boundary': boundary,
            'tool_calls': [{'id': 'page-' + str(start), 'assistant_msg_idx': 0}]}}
    if mode == 'different-preview':
        # Same canonical fingerprint, deliberately different presentation budget.
        incoming['messages'][0]['content'] = incoming['messages'][0]['content'][:1000]
    if mode == 'missing':
        pages[8]['session'].pop('_messages_boundary')
    if mode == 'null':
        # Audited shape: reconciled snapshot has already moved the final pair
        # into the page, while the done tail still points at their old indices.
        pages[8]['session']['messages'] = copy.deepcopy(rows[8:])
        pages[8]['session']['_messages_boundary'] = None
    if mode in ('changed', 'second-join'):
        at = 6 if mode == 'second-join' else 8
        pages[at]['session']['_messages_boundary']['_paging_identity'] = 'v1:' + 'f' * 64
    run_js(r'''
let S={session:{session_id:'s'},messages:input.prefix,activeStreamId:'run'};
let _oldestIdx=0,_loadSessionGeneration=1;
const wire=JSON.stringify(input.incoming),calls=[];
async function api(url){const before=Number(new URL(url,'http://fixture').searchParams.get('msg_before'));calls.push(before);return input.pages[before];}
(async()=>{
 let error;try{await _completeSettlementWindow(input.incoming,'s');}catch(e){error=e;}
 const merged=_mergeSettlementWindow(S.messages,input.incoming,0,true,[{id:'old',assistant_msg_idx:1}]);
 if(['valid','legacy','different-preview','prefix-preview','repeat','adjacent-valid'].includes(input.mode)){
   assert.equal(error,undefined);assert.equal(merged._messages_offset,0);
   assert.deepEqual(merged.messages.map(m=>m.id),Array.from({length:10},(_,i)=>String(i)));
   assert.equal(merged._settlement_gap,false);
   assert.equal(merged.tool_calls.find(t=>t.id==='tail-tool').assistant_msg_idx,9);
   assert.equal(merged.tool_calls.find(t=>t.id==='old').assistant_msg_idx,1);
 }else{
   assert.ok(error,'incompatible/missing joins must reject, not concatenate snapshots');
   assert.equal(JSON.stringify(input.incoming),wire,'failed hydration must be atomic');
   assert.deepEqual(merged.messages.map(m=>m.id),['8','9']);
   assert.equal(merged._messages_offset,8);assert.equal(merged._settlement_gap,true);
   assert.equal(merged.has_more,true);assert.equal(merged.message_count,10);
   assert.deepEqual(merged.tool_calls,[{id:'tail-tool',assistant_msg_idx:1}]);
 }
})().catch(e=>{console.error(e);process.exitCode=1});
''', dict(mode=mode, prefix=prefix, incoming=incoming, pages=pages))


@pytest.mark.parametrize('mode', [
    'clipped', 'unclipped', 'pre-tool', 'other-stream', 'other-stream-metadata',
    'no-owner', 'not-token', 'not-truncated', 'detail-only-truncated',
    'different-prefix', 'repeated-pre-tool', 'repeated-other-stream', 'repeated-segment',
    'original-chars-only', 'unknown-clipping', 'other-scene', 'other-message',
    'near-pre-tool', 'near-other-stream',
])
def test_clipped_final_is_not_retained_as_full_worklog_prose(mode):
    full = 'RESULT_TABLE_START\n| Item | URL |\n| --- | --- |\n' + ''.join(
        f'| item-{i} | https://example.test/{i} |\n' for i in range(500))
    if mode.startswith('near-'):
        full = full[:8500]
    canonical = [{'role': 'user', 'id': 'u', 'content': 'List results'},
                 {'role': 'assistant', 'id': 'a', 'content': full}]
    preview = bounded_render_messages(canonical, settlement=True)
    assert preview[-1]['_preview_content_truncated']
    assert len(preview[-1]['content']) < len(full)
    if not mode.startswith('near-'):
        assert len(preview[-1]['content']) < len(full) / 2
    if mode == 'unclipped':
        preview = canonical
    if mode == 'not-truncated':
        preview[-1].pop('_content_truncated')
        preview[-1].pop('_preview_content_truncated')
    if mode == 'detail-only-truncated':
        preview[-1]['_preview_content_truncated'] = False
    if mode in ('original-chars-only', 'unknown-clipping'):
        preview[-1].pop('_preview_content_truncated')
    if mode == 'unknown-clipping':
        preview[-1].pop('_content_original_chars')
    if mode == 'other-message':
        preview[-1]['_anchor_stream_id'] = 'other'
    run_js(r'''
for(const name of ['_anchorSceneCleanText','_anchorSceneTextKey','_anchorSceneExistingRowKey','_anchorSceneRowHasLiveIdentity','_anchorSceneSettleLiveRunningRow','_anchorSceneRowLooksLikeFinalAnswer','_anchorSceneRowTextOverlapsExisting','_anchorSceneMessageRowsHaveThinking','_completeSettledAnchorSceneForTurn']) vm.runInThisContext(extract(name));
global._anchorSceneActiveMode=()=> 'compact_worklog';
global._anchorSceneFinalAnswerText=m=>m.content;
global._anchorSceneRowsByMessageIndex=()=>new Map();
global._anchorSceneMessageRef=m=>m.id;
global._anchorSceneTurnDurationForSettlement=()=>0;
global._anchorSceneRowDisplayHintForMode=()=> 'activity_row';
global.S={session:{session_id:'s'},activeStreamId:'run',messages:[input.canonical[0]]};
global.streamId='run';
const row={role:'prose',kind:'process_prose',source_event_type:'token',local_id:'live-prose:run:1',status:'running',text:input.full};
const tool={role:'tool',kind:'tool_result',local_id:'tool-1',status:'completed',text:'Fetched'};
let activity_rows=[tool,row];
if(['pre-tool','near-pre-tool'].includes(input.mode))activity_rows=[row,tool];
if(['other-stream','near-other-stream'].includes(input.mode))row.local_id='live-prose:other:1';
if(input.mode==='other-stream-metadata')row.identity={stream_id:'other'};
if(input.mode==='no-owner'){delete global.streamId;S.activeStreamId=null;}
if(input.mode==='not-token')row.source_event_type='progress';
if(input.mode==='different-prefix')row.text='NOT THE FINAL\n'+input.full;
if(input.mode==='repeated-pre-tool')activity_rows=[{...row,local_id:'live-prose:run:0'},tool,row];
if(input.mode==='repeated-other-stream')activity_rows=[tool,{...row,local_id:'live-prose:other:1'},row];
if(input.mode==='repeated-segment')activity_rows=[tool,{...row,local_id:'live-prose:run:0'},row];
const incoming={session_id:'s',_settlement_window:'tail_v1',_messages_offset:0,messages:input.preview,tool_calls:[],message_count:2};
const merged=_mergeSettlementWindow(S.messages,incoming);
const identity=input.mode==='other-scene'?{stream_id:'other'}:{};
const scene=_completeSettledAnchorSceneForTurn(merged.messages,1,{mode:'compact_worklog',activity_rows,identity});
const prose=scene.activity_rows.filter(r=>r.role==='prose');
const suppressed=['clipped','unclipped','original-chars-only'].includes(input.mode);
assert.equal(prose.length,suppressed?0:1,input.mode);
if(input.mode==='repeated-pre-tool')assert.equal(prose[0].local_id,'live-prose:run:0');
if(input.mode==='repeated-other-stream')assert.equal(prose[0].local_id,'live-prose:other:1');
assert.equal(scene.final_answer,input.preview[1].content);
assert.equal(scene.activity_rows.filter(r=>r.role==='tool').length,1);
''', dict(mode=mode, full=full, canonical=canonical, preview=preview))
