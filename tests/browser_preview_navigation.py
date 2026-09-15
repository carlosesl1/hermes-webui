"""Exact hydration/control source with deferred API responses; no live state.

Exercise A -> B -> A in both response orders, including mutex and focus
ownership. Unlike the lighter control probe, _ensureAllMessagesLoaded is real.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify_preview_navigation(browser):
    source = (ROOT / 'static/sessions.js').read_text()
    start = source.index('async function _ensureAllMessagesLoaded(')
    end = source.index('\nfunction syncFullTranscriptPreview()', start)
    results = []
    for order in ('old-first', 'new-first', 'old-error', 'waiting'):
        page = browser.new_page()
        try:
            page.set_content('<button id="sentinel">Stay here</button><div id="msgInner"></div>')
            page.add_script_tag(content='''
let S={session:{session_id:'A'},messages:[{role:'assistant',content:'old preview',_content_truncated:true}]};
let _loadSessionGeneration=1,_loadingSessionId=null,_loadingOlder=false;
let _messagesTruncated=false,_oldestIdx=0,_messagesGeneration=0;
let calls=[],renders=0,toasts=0,syncs=0;
function $(id){return document.getElementById(id)}
function esc(x){return x} function t(x){return x}
function showToast(){toasts++} function renderMessages(){renders++}
function _bumpMessagesGeneration(){_messagesGeneration++}
function _syncToolCallsForLoadedMessages(){syncs++}
function api(){return new Promise((resolve,reject)=>calls.push({resolve,reject}))}
function response(text){return {session:{messages:[{role:'assistant',content:text}],message_count:1}}}
function button(){ $('msgInner').innerHTML='<div data-session-msg-idx="5">'+messageContentPreviewHtml(S.messages[0])+'</div>';return $('msgInner').querySelector('button') }
function navigateBack(){
 _loadSessionGeneration++;S.session={session_id:'B'};_loadingOlder=false;
 _loadSessionGeneration++;S.session={session_id:'A'};_loadingOlder=false;
 S.messages=[{role:'assistant',content:'new preview',_content_truncated:true}];
 $('sentinel').focus();
}
''' + source[start:end])
            result = page.evaluate('''async(order)=>{
 if(order==='waiting'){
   // Pause the mutex wait deterministically, then leave and re-enter A.
   let wake;window.setTimeout=fn=>{wake=fn};_loadingOlder=true;
   const pending=_ensureAllMessagesLoaded(true);navigateBack();wake();
   await Promise.resolve();if(calls[0])calls[0].resolve(response('stale'));
   await pending;
   return {order,noStaleRequest:calls.length===0,newerPreserved:S.messages[0].content==='new preview',unlocked:!_loadingOlder};
 }
 const old=expandMessagePreview(button());navigateBack();
 const fresh=expandMessagePreview(button());
 const duplicate=expandFullTranscript().catch(()=>{});
 const scopedDedup=calls.length===2;
 const staleCall=calls[0],freshCall=calls[1];
 let newerPreserved=true,lockOwned=true,noStaleFocus=true,noStaleRender=true;
 if(order==='new-first'&&freshCall){
   freshCall.resolve(response('new complete'));await Promise.all([fresh,duplicate]);
   $('sentinel').focus();staleCall.resolve(response('stale'));await old;
   newerPreserved=S.messages[0].content==='new complete';
   noStaleFocus=document.activeElement.id==='sentinel';noStaleRender=renders===1;
 }else{
   if(order==='old-error')staleCall.reject(new Error('stale request failed'));
   else staleCall.resolve(response('stale'));
   await old;
   newerPreserved=S.messages[0].content==='new preview';
   lockOwned=freshCall ? _loadingOlder : false;
   noStaleFocus=document.activeElement.id==='sentinel';noStaleRender=renders===0;
   if(freshCall)freshCall.resolve(response('new complete'));
   await Promise.all([fresh,duplicate]);
 }
 return {order,scopedDedup,newerPreserved,lockOwned,noStaleFocus,noStaleRender,
   finalContent:S.messages[0].content==='new complete',oneSync:syncs===1,
   noStaleToast:toasts===0,unlocked:!_loadingOlder,released:_fullTranscriptLoads.size===0};
}''', order)
            results.append(result)
        finally:
            page.close()
    failures = [r for r in results if any(v is not True for k, v in r.items() if k != 'order')]
    assert not failures, json.dumps(failures)
    return results


if __name__ == '__main__':
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
        try:
            print(json.dumps(verify_preview_navigation(browser)))
        finally:
            browser.close()
