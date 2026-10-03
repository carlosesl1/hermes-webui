"""Behavioral diagnosis of a done-tail gap that hides a received final answer.

Runs the production done listener and raw-coordinate settlement helpers in Node.
Only I/O and renderer boundaries are faked; no backend/core or real state is used.
The synthetic transcript models a 100-row loaded prefix, 60 newly persisted tool
rows absent from the browser, and the bounded 30-row done tail. This is not a
capture of a user's production conversation. The two known-failure cases are
strict xfails because the fix belongs to the messages.js owner; run with
``./scripts/test.sh tests/test_long_chat_live_settlement_gap.py --runxfail``
to expose the missing-final-answer assertions. Unexpected harness failures are
not covered by the expected-failure marker.
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
    listener = source[source.index("    source.addEventListener('done',"):
                      source.index("    source.addEventListener('stream_end',")]
    harness = r"""
const mode=MODE;
const callbacks={}, renders=[], calls=[], warnings=[], notifications=[];
const prefix=Array.from({length:100},(_,i)=>({role:i%2?'assistant':'user',content:'history '+i,timestamp:i+1}));
const rows=prefix.concat(Array.from({length:89},(_,i)=>({role:'tool',content:'tool '+i,timestamp:101+i})),
  [{role:'assistant',content:'FINAL ANSWER: finished all work.',timestamp:190}]);
let S={session:{session_id:'a'},messages:prefix.slice(),activeStreamId:'run',toolCalls:[],busy:true};
let _oldestIdx=0,_loadSessionGeneration=1,_messagesTruncated=false;
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
const _bailOutOfTerminalEventsFromStaleStream=()=>S.session.session_id!==activeSid;
let closed=0;
const _closeSource=()=>closed++;
const _setActivePaneIdleIfOwner=()=>{if(S.session.session_id===activeSid)S.busy=false;};
const _replaceMarkerOnlyAssistantWithStreamError=()=>false;
const _splitThinkFromContent=(content,reasoning)=>({content,reasoning});
const _filterRecoveryControlMessages=messages=>messages;
const _completionNotificationPreviewText=message=>message?.content||'';
const sendBrowserNotification=(title,body)=>notifications.push({title,body});
const _shouldFollowMessagesOnDomReplace=()=>false;
let scrollCalls=0;
const scrollToBottom=()=>scrollCalls++;
function renderMessages(options){renders.push({messages:JSON.parse(JSON.stringify(S.messages)),options});}
// Do not execute unrelated delayed TTS/cooldown callbacks, or wait five seconds.
const setTimeout=()=>1,clearTimeout=noop;
console.warn=(...args)=>warnings.push(String(args[0]));
async function api(url){
  calls.push(url);
  if(mode==='failed')throw new Error('fixture: gateway timeout during gap hydration');
  const before=Number(new URL(url,'http://fixture').searchParams.get('msg_before'));
  const start=Math.max(0,before-30);
  if(mode==='invalid')return {session:{session_id:'a',_messages_offset:before,messages:[]}};
  if((mode==='delayed'||mode==='aba')&&calls.length===1) await new Promise(resolve=>release=resolve);
  return {session:{session_id:'a',_messages_offset:start,messages:rows.slice(start,before)}};
}
""".replace("MODE", json.dumps(mode))
    probe = r"""
(async()=>{
  const start=mode==='contiguous'?70:160;
  const payload={session:{session_id:'a',_settlement_window:'tail_v1',_messages_offset:start,
    message_count:190,messages:rows.slice(start),tool_calls:[]},status:'completed'};
  const originalWire=JSON.stringify(payload);
  callbacks.done({data:originalWire});
  await new Promise(resolve=>setImmediate(resolve));
  const pending={busy:S.busy,finalized:_streamFinalized,renders:renders.length,
    containsFinal:renders.some(r=>r.messages.some(m=>m.content==='FINAL ANSWER: finished all work.'))};
  if(mode==='aba'){
    S.session={session_id:'b'};_loadSessionGeneration++;
    S.session={session_id:'a'};_loadSessionGeneration++;
    S.messages=[{role:'assistant',content:'NEWER PANE'}];S.activeStreamId='new-run';
  }
  if(release){release();await new Promise(resolve=>setImmediate(resolve));}
  console.log(JSON.stringify({pending,finalized:_streamFinalized,busy:S.busy,
    messages:S.messages,renders,calls,warnings,notifications,closed,scrollCalls,
    payloadUnchanged:JSON.stringify(payload)===originalWire}));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        ["node", "-e", harness + helpers + ephemeral + listener + probe],
        cwd=ROOT, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class _MissingFinalAnswer(AssertionError):
    """The done listener reached rendering but omitted its received final answer."""


@pytest.mark.xfail(
    strict=True,
    raises=_MissingFinalAnswer,
    reason="Known done-tail gap failure in messages.js; product fix requires settlement-owner changes",
)
@pytest.mark.parametrize("mode", ["failed", "invalid"])
def test_received_final_answer_survives_gap_hydration_failure(mode):
    observed = _observe(mode)
    assert observed["warnings"], "fixture must actually take gap-recovery failure path"
    assert observed["finalized"] and not observed["busy"]
    assert observed["scrollCalls"] == 0, "an unpinned reader must not be forced to tail"
    if not any(
        message["content"] == "FINAL ANSWER: finished all work."
        for render in observed["renders"] for message in render["messages"]
    ):
        raise _MissingFinalAnswer(
            f"received final absent from every render; rows={len(observed['messages'])}, "
            f"notifications={observed['notifications']}"
        )


@pytest.mark.parametrize("mode, pages", [("contiguous", 0), ("success", 2), ("delayed", 2)])
def test_successful_gap_settlement_keeps_history_and_final_separate(mode, pages):
    observed = _observe(mode)
    assert len(observed["calls"]) == pages
    assert len(observed["messages"]) == 190
    assert observed["messages"][0]["content"] == "history 0"
    assert observed["messages"][-1]["content"] == "FINAL ANSWER: finished all work."
    assert all(message["content"] != "PROGRESS: inspecting files" for message in observed["messages"])
    assert all(render["options"].get("preserveScroll") for render in observed["renders"])
    assert observed["scrollCalls"] == 0
    assert observed["payloadUnchanged"]
    if mode == "delayed":
        # Emit the pending observation without locking the existing blocking
        # behavior into a passing contract. Settlement may later paint sooner.
        print(f"done while gap I/O pending: {observed['pending']}")


def test_gap_hydration_cannot_settle_old_pane_after_a_b_a_navigation():
    observed = _observe("aba")
    assert observed["messages"] == [{"role": "assistant", "content": "NEWER PANE"}]
    assert observed["renders"] == []
    assert observed["closed"] == 1
    assert observed["notifications"] == []
    assert observed["scrollCalls"] == 0
