"""Offline UX-02/03 probes of real send/upload/draft code; no server or provider."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def source(name):
    revision = os.environ.get("UX_SUBMISSION_BASELINE")
    if revision:
        return subprocess.check_output(["git", "show", f"{revision}:static/{name}.js"], cwd=ROOT, text=True)
    return (ROOT / "static" / f"{name}.js").read_text()


def run_js(body):
    node = shutil.which("node")
    if not node:
        pytest.skip("node unavailable")
    draft, ui, messages = source("sessions"), source("ui"), source("messages")
    start = ui.find("// Attachment submission recovery")
    if start < 0:
        start = ui.index("function renderTray()")
    code = draft[draft.index("// ── Composer draft persistence"):draft.index("const SESSION_VIEWED_COUNTS_KEY")]
    code += "\n" + ui[start:]
    code += "\n" + messages[messages.index("async function send(){"):messages.index("async function startRegeneration(")]
    harness = r"""
const assert=require('node:assert/strict');
const nodes=new Map();
function node(){return {value:'',style:{},dataset:{},children:[],classList:{add(){},remove(){},toggle(){}},setAttribute(){},appendChild(x){this.children.push(x);return x;},querySelector(){return node();},querySelectorAll(){return [];},click(){},remove(){}};}
const document={baseURI:'http://fixture.invalid/',createElement:node,querySelector(){return null;}};
const $=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);};
const S={session:{session_id:'A',model:'fixture',title:'Fixture',workspace:'.',profile:'p'},activeProfile:'p',pendingFiles:[],messages:[],busy:false};
const store=new Map(),localStorage={getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)};
const window={},location={href:document.baseURI},_ARCHIVE_EXTS=/\.zip$/,_IMAGE_EXTS=/\.png$/,_SVG_EXTS=/\.svg$/;
const MAX_UPLOAD_BYTES=100000,MAX_UPLOAD_MB=1;
const t=k=>k,_t=(k,f)=>f,esc=x=>String(x),li=()=>'',_mediaKindForName=()=>'';
const updateSendBtn=()=>{},autoResize=()=>{},setStatus=()=>{},showToast=()=>{},setComposerStatus=()=>{};
const _redirectIfUnauth=()=>false;
const starts=[],uploads=[];
let api=async(url,opts)=>{if(url==='/api/chat/start'){starts.push(JSON.parse(opts.body));return {stream_id:'stream-fixture'};}return {ok:true};};
let fetch=async(url,opts)=>{const name=opts.body.get('file').name;uploads.push(name);return {ok:true,json:async()=>({filename:name,path:'/fixture/'+name})};};
let _loadingSessionId=null,_loadSessionGeneration=0,_sendInProgress=false,_sendInProgressSid=null,_forcedSkillDirectivePending=null;
const _pendingSelections=[],INFLIGHT={};
const _chatPayloadModelState=()=>({model:'fixture',model_provider:'fixture'});
const _clearStaleBusyStateBeforeSend=()=>{},_flushSelectionBlocksToComposer=()=>{},clearLiveToolCards=()=>{},renderMessages=()=>{},appendThinking=()=>{};
const _runOptionalPreStartUiStep=()=>{},_runOptionalPostStartUiStep=()=>{},attachLiveStream=()=>{},stopApprovalPolling=()=>{},stopClarifyPolling=()=>{},removeThinking=()=>{};
const setBusy=x=>{S.busy=x;};
const tick=()=>new Promise(r=>setTimeout(r,0));
"""
    script = harness + "\neval(" + json.dumps(code + "\n(async()=>{" + body + "})().catch(e=>{console.error(e);process.exitCode=1;});") + ");"
    result = subprocess.run([node, "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_partial_failure_blocks_and_retry_reuses_success():
    run_js(r"""
const files=[new File(['a'],'a.txt'),new File(['b'],'b.txt')]; S.pendingFiles=files;
let calls=[];fetch=async(url,opts)=>{const name=opts.body.get('file').name;calls.push(name);return {ok:name!=='b.txt',text:async()=> 'offline',json:async()=>({filename:name,path:'/fixture/'+name})};};
await assert.rejects(uploadPendingFiles({files,sessionId:'A',clearPending:false}));
assert.equal(S.pendingFiles.length,2);
fetch=async(url,opts)=>{const name=opts.body.get('file').name;calls.push(name);return {ok:true,json:async()=>({filename:name,path:'/fixture/'+name})};};
const result=await uploadPendingFiles({files,sessionId:'A',clearPending:false});
assert.deepEqual(result.map(f=>f.path),['/fixture/a.txt','/fixture/b.txt']);
assert.deepEqual(calls,['a.txt','b.txt','b.txt']);
""")


def test_restore_paths_and_metadata_without_overwriting_new_draft():
    run_js(r"""
_restoreComposerDraft({text:'saved',files:[{name:'ready.png',path:'/fixture/ready.png'},{name:'local.png',size:2}]},'A');
assert.equal(S.pendingFiles.length,2);assert.equal(S.pendingFiles[0].path,'/fixture/ready.png');
assert.equal(S.pendingFiles[1].needsReselect,true);
let calls=0;fetch=async()=>{calls++;throw Error('must not upload metadata');};
await assert.rejects(uploadPendingFiles({clearPending:false}));assert.equal(calls,0);
$('msg').value='newer';const local=new File(['new'],'new.txt');S.pendingFiles=[local];
_restoreComposerDraft({text:'old',files:[]},'A',{preserveActiveInput:true});
assert.equal($('msg').value,'newer');assert.equal(S.pendingFiles[0],local);
""")


def test_real_send_never_starts_after_partial_or_total_failure():
    run_js(r"""
$('msg').value='original';S.pendingFiles=[new File(['a'],'a.txt'),new File(['b'],'b.txt')];
const success=fetch;fetch=async(url,opts)=>opts.body.get('file').name==='b.txt'?{ok:false,text:async()=> 'offline'}:success(url,opts);
await send();assert.equal(starts.length,0);
const entry=_attachmentSubmissionCurrent();assert.equal(entry.text,'original');assert.equal(entry.files.length,2);
$('msg').value='new draft';const fresh=new File(['new'],'new.txt');S.pendingFiles=[fresh];
await send();assert.equal(starts.length,0);assert.equal($('msg').value,'new draft');
fetch=success;await send({attachmentSubmission:entry});
assert.equal(starts.length,1);assert.equal(starts[0].attachments.length,2);
assert.deepEqual(uploads,['a.txt','b.txt']);assert.equal($('msg').value,'new draft');assert.equal(S.pendingFiles[0],fresh);
S.busy=false;$('msg').value='all fail';S.pendingFiles=[new File(['c'],'c.txt')];fetch=async()=>{throw Error('offline');};
await send();assert.equal(starts.length,1);assert.equal(_attachmentSubmissionCurrent().text,'all fail');
""")


def test_explicit_send_without_and_reload_recovery():
    run_js(r"""
$('msg').value='original';S.pendingFiles=[{name:'ready',path:'/fixture/ready'},{name:'missing',needsReselect:true}];
await send();assert.equal(starts.length,0);
_attachmentSubmissions.clear();const entry=_attachmentSubmissionCurrent();
assert.equal(entry.files[0].path,'/fixture/ready');assert.equal(entry.files[1].needsReselect,true);
await send({attachmentSubmission:entry,omitFailedAttachments:true});
assert.equal(starts.length,1);assert.deepEqual(starts[0].attachments.map(f=>f.path),['/fixture/ready']);
assert.equal(uploads.length,0);assert.equal(_attachmentSubmissionCurrent(),null);
""")


@pytest.mark.parametrize("navigation", ["session", "profile"])
def test_aba_navigation_and_double_submit_preserve_ownership(navigation):
    run_js(r"""
$('msg').value='owner A';S.pendingFiles=[new File(['a'],'a.txt')];
let finish;fetch=()=>new Promise(r=>finish=r);const sending=send();await tick();
$('msg').value='fresh';const fresh=new File(['b'],'b.txt');S.pendingFiles=[fresh];
await send();assert.equal(starts.length,0);assert.equal($('msg').value,'fresh');
""" + ("S.session={session_id:'B',profile:'p'};_loadSessionGeneration++;" if navigation == "session" else "S.activeProfile='other';_loadSessionGeneration++;") + r"""
assert.equal(_attachmentSubmissionCurrent(),null);
S.session={session_id:'A',profile:'p',title:'Fixture'};S.activeProfile='p';_loadSessionGeneration++;
finish({ok:true,json:async()=>({filename:'a.txt',path:'/fixture/a.txt'})});await sending;
assert.equal(starts.length,0);assert.equal($('msg').value,'fresh');assert.equal(S.pendingFiles[0],fresh);
const entry=_attachmentSubmissionCurrent();assert.equal(entry.text,'owner A');
await send({attachmentSubmission:entry});assert.equal(starts.length,1);assert.equal(starts[0].session_id,'A');assert.equal(starts[0].profile,'p');
assert.equal($('msg').value,'fresh');
""")


def test_draft_writes_order_and_quiet_failure_recovery():
    run_js(r"""
const writes=[];let finish;api=(url,opts)=>{writes.push(JSON.parse(opts.body));return writes.length===1?new Promise(r=>finish=r):Promise.resolve({ok:true});};
const first=_saveComposerDraftNow('A','old',[new File(['a'],'a.txt')]);await tick();
const second=_saveComposerDraftNow('A','new',[new File(['b'],'b.txt')]);
assert.equal(writes.length,1);finish({ok:true});await Promise.all([first,second]);
assert.deepEqual(writes.map(x=>x.text),['old','new']);
$('msg').value='';S.pendingFiles=[];_restoreComposerDraft({text:'stale',files:[]},'A');
assert.equal($('msg').value,'new');assert.equal(S.pendingFiles[0].name,'b.txt');
api=async()=>{throw Error('offline');};await _saveComposerDraftNow('A','keep',[new File(['k'],'keep.txt')]);
assert.equal(_composerDraftSaveErrors.has(_attachmentOwner()),true);
api=async()=>({ok:true});await _saveComposerDraftNow('A','keep',S.pendingFiles);
assert.equal(_composerDraftSaveErrors.has(_attachmentOwner()),false);
""")


def test_start_failure_keeps_snapshot_separate_and_does_not_auto_queue():
    run_js(r"""
$('msg').value='original';S.pendingFiles=[{name:'ready',path:'/fixture/ready'}];
api=async(url)=>{if(url==='/api/chat/start'){ $('msg').value='next';throw Error('session already has an active stream');}return {ok:true};};
await send();assert.equal(_attachmentSubmissionCurrent().text,'original');assert.equal($('msg').value,'next');
assert.equal(S.messages.length,0);assert.equal(S.busy,false);
""")


def test_new_draft_upload_cleanup_does_not_clear_fresh_files():
    run_js(r"""
const first=new File(['a'],'a.txt'),fresh=new File(['b'],'b.txt');S.pendingFiles=[first];
let finish;fetch=()=>new Promise(r=>finish=r);const uploading=uploadPendingFiles();S.pendingFiles.push(fresh);
finish({ok:true,json:async()=>({filename:'a.txt',path:'/fixture/a.txt'})});await uploading;
assert.deepEqual(S.pendingFiles,[fresh]);
""")


def test_file_only_failure_and_duplicate_retry_do_not_start_empty_turn():
    run_js(r"""
S.pendingFiles=[{name:'missing',needsReselect:true}];
await send();assert.equal(starts.length,0);
const entry=_attachmentSubmissionCurrent();await send({attachmentSubmission:entry,omitFailedAttachments:true});
assert.equal(starts.length,0);assert.equal(entry.files.length,1);
entry.files=[new File(['a'],'a.txt')];let finish;fetch=()=>new Promise(r=>finish=r);
const sending=send({attachmentSubmission:entry});await send({attachmentSubmission:entry});
finish({ok:true,json:async()=>({filename:'a.txt',path:'/fixture/a.txt'})});await sending;
assert.equal(starts.length,1);assert.equal(starts[0].attachments.length,1);
""")


def test_empty_draft_save_orders_after_pending_nonempty_write():
    run_js(r"""
const writes=[];let finish;api=(url,opts)=>{writes.push(JSON.parse(opts.body));return writes.length===1?new Promise(r=>finish=r):Promise.resolve({ok:true});};
const first=_saveComposerDraftNow('A','old',[]);await tick();
const clear=_saveComposerDraftNow('A','',[]);finish({ok:true});await Promise.all([first,clear]);
assert.deepEqual(writes.map(x=>x.text),['old','']);
""")
