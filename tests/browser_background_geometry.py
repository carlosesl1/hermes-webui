"""Portable browser regressions for fractional heights and window topology."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def verify_geometry(browser):
 R=ROOT
 source=(R/'static/ui.js').read_text()
 start=source.index('function _updateMessageVirtualMeasurements(')
 end=source.index('\n// #5638',start)
 measurement=source[start:end]
 report=[]
 for test in ('fractional-height','topology-focus'):
  p=browser.new_page(viewport={'width':522,'height':1232});p.set_content('<div id=msgInner></div>');p.add_script_tag(path=str(R/'static/background_activity.js'));p.add_style_tag(path=str(R/'static/background_activity.css'))
  try:
   if test=='fractional-height':
    p.add_script_tag(content='window._messageVirtualHeightCache=[];window._messageVirtualEstimatedRowHeight=140;function $(id){return document.getElementById(id)};function _measureMessageVirtualRow(inner,entry,entries){return backgroundActivityVirtualHeight(inner,entry,entries)};function _scheduleMessageVirtualMeasurementRefresh(){};function _markMessageVirtualMeasurementsSettled(){};'+measurement)
    result=p.evaluate('''() => {window.S={session:{session_id:'fractional'},busy:false,messages:[{role:'user',content:'Run'}]}; window.inner=$('msgInner'); let html='<div class="message-virtual-spacer" style="height:100px"></div>';for(let i=1;i<=100;i++){S.messages.push({role:'user',_source:'process_wakeup',content:'done'});html+='<div class="msg-row" data-msg-idx="'+i+'">done</div>';}inner.innerHTML=html;syncBackgroundActivity(inner);const entries=Array.from({length:100},(_,i)=>({rawIdx:i+1}));_updateMessageVirtualMeasurements(entries,entries.map((_,i)=>i),{virtualized:true});return {cached:_messageVirtualHeightCache.filter(x=>x>0).length,total:_messageVirtualHeightCache.reduce((a,b)=>a+b,0),actual:inner.querySelector('details').getBoundingClientRect().height};}''')
    assert result['cached']==100,result;assert abs(result['total']-result['actual'])<0.01,result
   else:
    p.evaluate('''() => {window.S={session:{session_id:'topology'},busy:false,messages:[{role:'user',content:'Run'},{role:'assistant',content:'Main'},{role:'user',_source:'process_wakeup',content:'done'},{role:'assistant',content:'ack'}]};window.inner=document.querySelector('#msgInner');inner.innerHTML='<div class="message-virtual-spacer" style="height:100px"></div><div class="msg-row" data-msg-idx="2">done</div><div class="assistant-turn" data-msg-idx="3">ack</div>';syncBackgroundActivity(inner);}''')
    p.locator('summary').click();p.locator('summary').focus()
    result=p.evaluate('''()=>{const old=inner.querySelector('summary');prepareBackgroundActivityRender(inner);const a=document.createElement('div');a.className='msg-row';a.dataset.msgIdx='0';a.textContent='Run';const spacer=document.createElement('div');spacer.className='message-virtual-spacer';spacer.style.height='100px';inner.insertBefore(a,inner.children[1]);inner.insertBefore(spacer,inner.children[2]);syncBackgroundActivity(inner);const g=inner.querySelector('details');return {open:g.open,focused:document.activeElement===g.querySelector('summary'),sameNode:old===g.querySelector('summary'),order:[...inner.children].map(x=>x.className)};}''')
    assert result['open'] and result['focused'] and result['sameNode'],result
   report.append({'name':test,'status':'PASS','evidence':result})
  except Exception as e:report.append({'name':test,'status':'FAIL','error':str(e)})
  finally:p.close()
 assert all(x['status']=='PASS' for x in report),report
 return report

if __name__=='__main__':
 from playwright.sync_api import sync_playwright
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
  try: print(json.dumps(verify_geometry(browser)))
  finally: browser.close()
