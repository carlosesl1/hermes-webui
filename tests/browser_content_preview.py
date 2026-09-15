"""Real API/browser regression for short prose after a page of huge tool detail.
Synthetic content only, no provider calls. Reusable for local and public QA.
"""
import hashlib,json
from pathlib import Path

def verify_content_preview(browser,context,width,height,output):
 output=Path(output);output.mkdir(parents=True,exist_ok=True)
 rows=[{'role':'user','content':'Revise os registros e apresente uma conclusão curta.'}]
 for i in range(36):
  rows.extend([{'role':'assistant','content':f'Registro {i}: '+('informação longa; '*900)},
               {'role':'tool','tool_call_id':f'preview-tool-{i}','content':'detalhe técnico '*2400}])
 question='Pode resumir sem esconder esta pergunta?'
 answer='Sim. A conversa permanece legível.'
 rows.extend([{'role':'user','content':question},{'role':'assistant','content':answer}])
 r=context.request.post('/api/session/import',data={'title':'hermes-qa-preview-readability','messages':rows})
 assert r.ok,r.status
 sid=r.json()['session']['session_id'];page=context.new_page();errors=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 def full():
  r=context.request.get('/api/session?session_id='+sid+'&messages=1&resolve_model=0&content_full=1');assert r.ok;return r.json()['session']['messages']
 def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
 before=digest(full());result={'session_id':sid,'viewport':[width,height],'synthetic':True}
 try:
  page.set_viewport_size({'width':width,'height':height})
  page.goto('/session/'+sid,wait_until='domcontentloaded')
  page.wait_for_function('(sid)=>typeof S!=="undefined"&&S.session?.session_id===sid&&S.messages.length>2',arg=sid)
  page.wait_for_function('(answer)=>S.messages.at(-1)?.content===answer',arg=answer,timeout=15000)
  assert page.get_by_text(question,exact=True).is_visible()
  assert page.get_by_text(answer,exact=True).is_visible()
  assert 'Content truncated in paginated preview' not in page.locator('#msgInner').inner_text()
  assert 'Tool output truncated in paginated preview' not in page.locator('#msgInner').inner_text()
  assert page.locator('.message-preview-expand').count()>0
  assert page.evaluate('document.documentElement.scrollWidth')<=width
  page.screenshot(path=str(output/f'{width}-readable-preview.png'),full_page=True)
  # Explicit opt-in must still recover every original message, without a second
  # prose-matching pass or changing the saved transcript.
  control=page.locator('.message-preview-expand').last
  control.scroll_into_view_if_needed();control.focus();control.press('Enter')
  page.wait_for_function('()=>!S.messages.some(m=>m._content_truncated)',timeout=120000)
  assert page.locator('.message-preview-expand').count()==0
  assert digest(full())==before
  page.reload(wait_until='domcontentloaded')
  page.wait_for_function('(answer)=>typeof S!=="undefined"&&S.messages.at(-1)?.content===answer',arg=answer)
  assert page.get_by_text(answer,exact=True).is_visible()
  assert not errors,errors
  result.update(status='PASS',short_question_answer_intact=True,no_technical_notice=True,full_content_keyboard=True,canonical_unchanged=True,reload=True,page_errors=errors)
 except Exception as e:
  result.update(status='FAIL',error=str(e))
  try:page.screenshot(path=str(output/f'{width}-failure.png'),full_page=True)
  except Exception:pass
  raise
 finally:
  page.close()
  deletion=context.request.post('/api/session/delete',data={'session_id':sid})
  check=context.request.get('/api/session?session_id='+sid+'&messages=0&resolve_model=0')
  result['cleanup']={'delete_http':deletion.status,'readback_http':check.status}
  (output/f'{width}-result.json').write_text(json.dumps(result,indent=2))
  assert deletion.ok and check.status==404,result['cleanup']
 return result
