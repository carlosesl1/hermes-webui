"""Browser lifecycle tests for the exact preview control source; API is gated."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def verify_preview_controls(browser):
 source=(ROOT/'static/sessions.js').read_text()
 start=source.index('function messageContentPreviewHtml(')
 end=source.index('\nfunction syncFullTranscriptPreview()',start)
 p=browser.new_page()
 try:
  p.set_content('<div id="msgInner"><div data-session-msg-idx="5" id="row"></div></div>')
  p.add_script_tag(content="window._loadSessionGeneration=1;window.S={session:{session_id:'one'},messages:[{_content_truncated:true}]};window.calls=0;window.renders=0;window.toasts=0;function $(id){return document.getElementById(id)};function esc(x){return x};function t(x){return x};function showToast(){toasts++};function renderMessages(){renders++};function _ensureAllMessagesLoaded(){calls++;return new Promise((resolve,reject)=>{window.resolveLoad=()=>{S.messages=[{content:'Complete'}];resolve()};window.rejectLoad=reject})};"+source[start:end])
  result=p.evaluate('''async()=>{
    const literal={content:'[Content truncated in paginated preview; open the full transcript to inspect the complete content.]'};
    const flags=messageContentPreviewHtml(literal)==='' && messageContentPreviewHtml({_content_truncated:true,_preview_content_truncated:false})==='';
    const row=$('row');row.innerHTML=messageContentPreviewHtml(S.messages[0]);const b=row.querySelector('button');
    const failed=expandMessagePreview(b);rejectLoad(new Error('network failure'));await failed;
    const retryReady=!b.disabled&&toasts===1&&_fullTranscriptLoads.size===0;
    const success=expandMessagePreview(b);const duplicate=expandFullTranscript();const dedup=calls===2;resolveLoad();await Promise.all([success,duplicate]);
    const recovered=document.activeElement===row&&renders===1&&_fullTranscriptLoads.size===0;
    S.messages=[{_content_truncated:true}];const stale=expandMessagePreview(b);S.session={session_id:'two'};resolveLoad();await stale;
    const guarded=renders===1&&toasts===1&&_fullTranscriptLoads.size===0;
    return {flags,retryReady,dedup,recovered,guarded};
  }''')
  assert all(result.values()),result
  return result
 finally:p.close()
if __name__=='__main__':
 from playwright.sync_api import sync_playwright
 with sync_playwright() as pw:
  b=pw.chromium.launch(headless=True,args=['--no-sandbox'])
  try:print(json.dumps(verify_preview_controls(b)))
  finally:b.close()
