"""Execute the production done listener against synthetic raw-coordinate gaps.

Only I/O and renderer boundaries are faked; no backend/core or real state is used.
A 100-row loaded prefix precedes 60 missing tool rows and a 30-row done tail.
Failed/invalid/slow/over-budget hydration must never hide the authoritative final.
"""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node required")


def _observe(mode):
    source = (ROOT / "static/messages.js").read_text()
    helpers = source[:source.index("function _markSessionViewed")]
    ephemeral = source[source.index("  function _messageIdentityKey("):
                       source.index("  async function _restoreSettledSession(")]
    ownership = source[source.index("  function _isActiveSession(){"):
                       source.index("  function _clearActivePaneInflightIfOwner(){")]
    listener = source[source.index("    source.addEventListener('done',"):
                      source.index("    source.addEventListener('stream_end',")]
    harness = r"""
const mode=MODE;
const callbacks={}, renders=[], calls=[], options=[], warnings=[], notifications=[], timers=[];
const prefix=Array.from({length:100},(_,i)=>({role:i%2?'assistant':'user',content:'history '+i,timestamp:i+1}));
const rows=prefix.concat(Array.from({length:89},(_,i)=>({role:'tool',content:'tool '+i,timestamp:101+i})),
  [{role:'assistant',content:'FINAL ANSWER: finished all work.',timestamp:190}]);
const canonicalBefore=JSON.stringify(rows);
let S={session:{session_id:'a'},messages:prefix.slice(),activeStreamId:'run',
  toolCalls:[{id:'old-card',assistant_msg_idx:1}],busy:true};
if(mode==='huge') S.messages=prefix.slice(0,1);
let _oldestIdx=0,_loadSessionGeneration=1,_messagesTruncated=false;
let generationBumps=0;
const _bumpMessagesGeneration=()=>generationBumps++;
let _streamFinalized=false,_terminalStateReached=false,_persistTimer=null;
let _queueDrainSid=null,_latestGoalStatus=null,_pendingGoalContinuation=null;
const activeSid='a',streamId='run',uploaded=[],assistantBody=null;
const assistantText='PROGRESS: inspecting files',reasoningText='';
let release;
const window={},localStorage={setItem(){}};
const source={addEventListener(name,callback){callbacks[name]=callback;}};
const noop=()=>{};
const _clearStreamEndRecovery=noop,_cancelThrottledSnapshotTimer=noop;
const _scheduleAnchorRegistryCleanup=noop,_cancelAnimationFramePendingStreamRender=noop;
const _streamFadeCleanupReduceMotionListener=noop,_smdEndParser=noop;
const _flushReasoningToAnchor=noop,_applyToAnchor=noop,_clearAnchorProseIncrementalNode=noop;
const _clearOwnerInflightState=noop,_clearApprovalForOwner=noop,_clearClarifyForOwner=noop;
const _markSessionViewed=noop,_attachProjectedAnchorSceneToLastAssistant=noop;
const clearLiveToolCards=noop,removeThinking=noop,syncTopbar=noop,loadDir=noop;
const renderSessionList=noop,_dispatchExtensionTurnLifecycle=noop,playNotificationSound=noop;
const _shouldUseLiveProseFade=()=>false,_shouldForceCompletionNotification=()=>false;
const _isSessionCurrentPane=sid=>S.session.session_id===sid;
const _isSessionActivelyViewed=_isSessionCurrentPane;
let closed=0;
const _closeSource=()=>closed++;
const _setActivePaneIdleIfOwner=()=>{if(S.session.session_id===activeSid)S.busy=false;};
const _replaceMarkerOnlyAssistantWithStreamError=()=>false;
const _splitThinkFromContent=(content,reasoning)=>({content,reasoning});
const _filterRecoveryControlMessages=messages=>messages;
const _mergeSettledToolCallsWithLiveMetadata=calls=>calls;
const _completionNotificationPreviewText=message=>message?.content||'';
const sendBrowserNotification=(title,body)=>notifications.push({title,body});
const _shouldFollowMessagesOnDomReplace=()=>false;
let scrollCalls=0;
const scrollToBottom=()=>scrollCalls++;
function renderMessages(options){renders.push({messages:JSON.parse(JSON.stringify(S.messages)),options});}
// Virtual clock for deterministic timeout/late-response tests. One case runs
// the real deadline; unrelated TTS/cooldown callbacks never keep Node alive.
const nativeTimers=require('node:timers');
const setTimeout=(fn,ms)=>{
  const timer={fn,ms,cleared:false};timers.push(timer);
  if(mode==='hung-real'&&ms===1500) timer.native=nativeTimers.setTimeout(fn,ms);
  return timer;
};
const clearTimeout=timer=>{if(timer){timer.cleared=true;if(timer.native)nativeTimers.clearTimeout(timer.native);}};
console.warn=(...args)=>warnings.push(String(args[0]));
async function api(url,opts){
  calls.push(url);options.push(opts);
  if(mode==='failed'||(mode==='partial-failed'&&calls.length===2))throw new Error('fixture: gateway timeout');
  const before=Number(new URL(url,'http://fixture').searchParams.get('msg_before'));
  const start=Math.max(0,before-(mode==='tiny-pages'?1:30));
  if(mode==='invalid')return {session:{session_id:'a',_messages_offset:before,messages:[]}};
  if(['delayed','aba','replaced-source','hung','hung-real','late','aba-timeout'].includes(mode)&&calls.length===1)
    await new Promise(resolve=>release=resolve);
  return {session:{session_id:mode==='wrong-session'?'other':'a',_messages_offset:start,messages:rows.slice(start,before),_messages_boundary:rows[before]}};
}
""".replace("MODE", json.dumps(mode))
    probe = r"""
(async()=>{
  const start=mode==='contiguous'?70:160;
  const payload={session:{session_id:'a',_settlement_window:'tail_v1',_messages_offset:start,
    message_count:190,messages:rows.slice(start),tool_calls:[]},status:'completed'};
  const originalWire=JSON.stringify(payload);
  const began=Date.now();
  callbacks.done({data:originalWire});
  await new Promise(resolve=>setImmediate(resolve));
  const pending={busy:S.busy,finalized:_streamFinalized,renders:renders.length,
    containsFinal:renders.some(r=>r.messages.some(m=>m.content==='FINAL ANSWER: finished all work.'))};
  if(['aba','aba-timeout','replaced-source'].includes(mode)){
    if(mode!=='replaced-source'){
      S.session={session_id:'b'};_loadSessionGeneration++;
      S.session={session_id:'a'};_loadSessionGeneration++;
    }
    S.messages=[{role:'assistant',content:'NEWER PANE'}];S.activeStreamId='new-run';
  }
  if(['hung','late','aba-timeout'].includes(mode)){
    const deadline=timers.find(timer=>timer.ms===1500&&!timer.cleared);
    if(!deadline) throw new Error('missing whole-settlement deadline');
    deadline.fn();await new Promise(resolve=>setImmediate(resolve));
  }
  if(mode==='hung-real'){
    await new Promise(resolve=>nativeTimers.setTimeout(resolve,1650));
  }
  const beforeLate=JSON.stringify(S.messages);
  if(release&&!['hung','hung-real'].includes(mode)){release();await new Promise(resolve=>setImmediate(resolve));}
  console.log(JSON.stringify({pending,finalized:_streamFinalized,busy:S.busy,
    messages:S.messages,renders,calls,warnings,notifications,closed,scrollCalls,
    offset:_oldestIdx,truncated:_messagesTruncated,hasMore:S.session.has_more,gap:S.session._settlement_gap,
    messageCount:S.session.message_count,toolCalls:S.toolCalls,elapsedMs:Date.now()-began,generationBumps,
    limits:options.map(o=>({timeoutMs:o.timeoutMs,retries:o.retries,aborted:o.signal.aborted})),
    deadlineCleared:timers.filter(t=>t.ms===1500).every(t=>t.cleared),
    lateUnchanged:beforeLate===JSON.stringify(S.messages),
    canonicalUnchanged:canonicalBefore===JSON.stringify(rows),
    payloadUnchanged:JSON.stringify(payload)===originalWire}));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        ["node", "-e", harness + helpers + ephemeral + ownership + listener + probe],
        cwd=ROOT, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("mode, pages", [
    ("failed", 1), ("invalid", 1), ("wrong-session", 1), ("partial-failed", 2),
    ("tiny-pages", 3), ("huge", 0), ("hung", 1), ("late", 1), ("hung-real", 1),
])
def test_received_final_answer_survives_gap_hydration_failure(mode, pages):
    observed = _observe(mode)
    assert observed["warnings"], "fixture must actually take gap-recovery failure path"
    assert observed["finalized"] and not observed["busy"]
    assert len(observed["calls"]) == pages
    assert observed["scrollCalls"] == 0, "an unpinned reader must not be forced to tail"
    assert any(
        message["content"] == "FINAL ANSWER: finished all work."
        for render in observed["renders"] for message in render["messages"]
    ), observed
    # Coherent [160,190) window, not a prefix+tail with invented indices.
    assert observed["offset"] == 160 and len(observed["messages"]) == 30
    assert observed["messages"][0]["content"] == "tool 60"
    assert observed["messages"][-1]["content"] == "FINAL ANSWER: finished all work."
    assert observed["messageCount"] == 190
    assert observed["hasMore"] and observed["truncated"] and observed["gap"]
    assert observed["generationBumps"] == 1, "invalidate paging anchored in the discarded prefix"
    assert observed["toolCalls"] == [], "old prefix cards do not belong to the new tail coordinates"
    assert observed["notifications"][-1]["body"] == "FINAL ANSWER: finished all work."
    assert observed["canonicalUnchanged"] and observed["payloadUnchanged"]
    assert observed["deadlineCleared"]
    assert all(o == {"timeoutMs": 1500, "retries": 0, "aborted": True} for o in observed["limits"])
    if mode in ("hung", "late", "hung-real"):
        assert not observed["pending"]["containsFinal"]
        assert observed["lateUnchanged"]
    if mode == "hung-real":
        assert 1500 <= observed["elapsedMs"] < 3000


@pytest.mark.parametrize("mode, pages", [("contiguous", 0), ("success", 3), ("delayed", 3)])
def test_successful_gap_settlement_keeps_history_and_final_separate(mode, pages):
    observed = _observe(mode)
    assert len(observed["calls"]) == pages
    assert len(observed["messages"]) == 190 and observed["offset"] == 0
    assert observed["messages"][0]["content"] == "history 0"
    assert observed["messages"][-1]["content"] == "FINAL ANSWER: finished all work."
    assert all(message["content"] != "PROGRESS: inspecting files" for message in observed["messages"])
    assert all(render["options"].get("preserveScroll") for render in observed["renders"])
    assert observed["scrollCalls"] == 0
    assert not observed["gap"] and not observed["truncated"]
    assert observed["generationBumps"] == 0
    assert observed["payloadUnchanged"] and observed["canonicalUnchanged"]
    assert observed["deadlineCleared"]


@pytest.mark.parametrize("mode", ["aba", "aba-timeout", "replaced-source"])
def test_gap_hydration_cannot_settle_a_new_pane_or_stream_owner(mode):
    observed = _observe(mode)
    assert observed["messages"] == [{"role": "assistant", "content": "NEWER PANE"}]
    assert observed["renders"] == []
    assert observed["closed"] >= 1
    assert observed["notifications"] == []
    assert observed["scrollCalls"] == 0
    assert len(observed["calls"]) == 1
    assert observed["deadlineCleared"]
