/* Background continuations are transcript-owned activity, not human questions.
 * This module is a display projection only: never mutate messages, roles, source,
 * provider context or delivery acknowledgements. Canonical indices stay intact.
 */
function backgroundActivityText(message){
  const value=message&&message.content;
  const text=typeof value==='string'?value:(Array.isArray(value)?value.filter(p=>p&&p.type==='text').map(p=>p.text||'').join('\n'):'');
  return text.replace(/^\[Workspace::v1: [^\n]+\]\s*\n/,'');
}
function backgroundActivityDescriptor(message, knownTaskIds){
  if(!message||message.role!=='user') return null;
  const source=message._source;
  if(source&&source!=='process_wakeup') return null;
  const text=backgroundActivityText(message);
  let match=text.match(/^\[IMPORTANT: Background process (\S+) completed \(exit_code=([^)\n]+)\)\.\nCommand: [^\n]*\nOutput:\n[\s\S]*\]$/);
  let kind='notification',taskId='',exitCode=null;
  if(match){kind='completion';taskId=match[1];exitCode=match[2];}
  else if((match=text.match(/^\[IMPORTANT: Background process (\S+) matched watch pattern "[^\n]*"\.\nCommand: [^\n]*\nMatched output:\n[\s\S]*\]$/))){kind='watch_match';taskId=match[1];}
  else if((match=text.match(/^\[ASYNC DELEGATION BATCH COMPLETE — (\S+)\]\n/))){kind='async_delegation';taskId=match[1];}
  else if(text.startsWith('[BACKGROUND UPDATES]\n')) kind='completion_batch';
  // Legacy, metadata-free imports need corroborating tool output in THIS
  // transcript. A pasted/quoted marker alone never changes presentation.
  if(source!=='process_wakeup'&&(!taskId||!knownTaskIds||!knownTaskIds.has(taskId))) return null;
  const meta=message._wakeup_meta;
  if(source==='process_wakeup'&&meta&&typeof meta==='object'){
    kind=meta.type||kind;taskId=meta.task_id||taskId;
    if(Object.prototype.hasOwnProperty.call(meta,'exit_code')) exitCode=meta.exit_code;
  }
  const code=exitCode==null?'':String(exitCode);
  const batch=kind==='completion_batch'?text.match(/^\[BACKGROUND UPDATES\]\nEvents: (\d+); failed: (\d+)\n/):null;
  return {kind,taskId:String(taskId),failed:(/^-?\d+$/.test(code)&&Number(code)!==0)||!!(batch&&Number(batch[2])>0)};
}
function backgroundActivityOwners(messages){
  const owners=new Map(),knownTaskIds=new Set();
  let human=-1,active=null;
  (messages||[]).forEach((message,index)=>{
    if(!message) return;
    if(message.role==='tool'){
      // Registry handles are short header data; avoid walking multi-MB output.
      const text=typeof message.content==='string'?message.content.slice(0,8192):'';
      for(const match of text.matchAll(/\b(?:proc|deleg)_[A-Za-z0-9_-]+\b/g)) knownTaskIds.add(match[0]);
    }
    if(message.role==='user'){
      // Anthropic tool results aren't new human turns either.
      if(Array.isArray(message.content)&&message.content.length&&message.content.every(p=>p&&p.type==='tool_result')) return;
      const descriptor=backgroundActivityDescriptor(message,knownTaskIds);
      if(descriptor){active={owner:human,eventIndex:index,...descriptor};owners.set(index,active);}
      else {human=index;active=null;}
    }else if(active){owners.set(index,{...active,eventIndex:null});}
  });
  return owners;
}

const _backgroundActivityOpen=new Map();
let _backgroundActivityObserver=null;
let _backgroundActivityRoot=null;
let _backgroundActivitySyncing=false;
function _backgroundActivityLabel(key,fallback){
  const value=typeof t==='function'?t(key):'';
  return value&&value!==key?value:fallback;
}
function _rememberBackgroundActivityOpen(key,open){
  _backgroundActivityOpen.delete(key);
  _backgroundActivityOpen.set(key,!!open);
  while(_backgroundActivityOpen.size>128) _backgroundActivityOpen.delete(_backgroundActivityOpen.keys().next().value);
}
function _unwrapBackgroundActivity(inner){
  if(!inner) return;
  for(const group of Array.from(inner.children).filter(n=>n.classList.contains('background-activity-group'))){
    _rememberBackgroundActivityOpen(group.dataset.backgroundOwner,group.open);
    const body=group.querySelector('.background-activity-content');
    if(body) while(body.firstChild) inner.insertBefore(body.firstChild,group);
    group.remove();
  }
}
function prepareBackgroundActivityRender(inner){
  if(_backgroundActivityObserver) _backgroundActivityObserver.disconnect();
  _unwrapBackgroundActivity(inner);
}
function syncBackgroundActivity(inner){
  if(!inner||_backgroundActivitySyncing||typeof S==='undefined') return;
  _backgroundActivitySyncing=true;
  if(_backgroundActivityObserver) _backgroundActivityObserver.disconnect();
  try{
    _unwrapBackgroundActivity(inner);
    const messages=S.messages||[],owners=backgroundActivityOwners(messages);
    const sessionId=S.session&&S.session.session_id||'';
    const groups=new Map();
    for(const row of Array.from(inner.children)){
      if(!row.matches('.msg-row,.assistant-turn')) continue;
      const indexed=row.hasAttribute('data-msg-idx')?row:row.querySelector('[data-msg-idx]');
      let raw=indexed?Number(indexed.dataset.msgIdx):NaN;
      // The live shell can precede its first persisted assistant token.
      if(!Number.isInteger(raw)&&row.matches('.assistant-turn[data-live-assistant="1"],.assistant-turn')&&S.busy) raw=messages.length-1;
      const entry=owners.get(raw);
      if(!entry) continue;
      const absolute=typeof _messageSessionIndexForRawIdx==='function'?_messageSessionIndexForRawIdx(entry.owner):entry.owner;
      const key=sessionId+':'+absolute;
      let record=groups.get(key);
      if(!record){
        const group=document.createElement('details');
        group.className='background-activity-group';
        group.dataset.backgroundOwner=key;
        group.dataset.sessionId=sessionId;
        group.open=_backgroundActivityOpen.get(key)===true;
        const summary=document.createElement('summary');
        summary.className='background-activity-summary';
        const title=document.createElement('span');
        title.className='background-activity-title';
        title.textContent=_backgroundActivityLabel('background_activity_title','Background updates');
        const status=document.createElement('span');
        status.className='background-activity-status';
        summary.append(title,status);group.append(summary);
        const body=document.createElement('div');body.className='background-activity-content';group.append(body);
        inner.insertBefore(group,row);
        record={group,body,status,events:new Set(),failed:false,live:false};groups.set(key,record);
      }
      record.body.appendChild(row);
      if(entry.eventIndex!==null) record.events.add(entry.eventIndex);
      record.failed=record.failed||entry.failed;
      record.live=record.live||!!(S.busy&&raw>=messages.length-1);
    }
    groups.forEach(record=>{
      const {group,status}=record;
      let state=record.live?_backgroundActivityLabel('background_activity_running','Working'):_backgroundActivityLabel('background_activity_available','View updates');
      if(record.failed) state=_backgroundActivityLabel('background_activity_failure','Includes a failed task');
      status.textContent=record.events.size+' · '+state;
      group.classList.toggle('has-failure',record.failed);
      group.classList.toggle('is-running',record.live);
      // Never hide a request for user input or move focused content out of reach.
      if(group.contains(document.activeElement)||group.querySelector('.clarify-card,.approval-card,[data-approval-id]')) group.open=true;
    });
  }finally{
    _backgroundActivitySyncing=false;
    observeBackgroundActivity(inner);
  }
}
function observeBackgroundActivity(inner){
  if(typeof MutationObserver==='undefined'||!inner) return;
  if(_backgroundActivityRoot!==inner){
    if(_backgroundActivityObserver) _backgroundActivityObserver.disconnect();
    _backgroundActivityRoot=inner;
    _backgroundActivityObserver=new MutationObserver(()=>{
      if(typeof S!=='undefined'&&S.session) syncBackgroundActivity(inner);
    });
    // Capture works for the non-bubbling native details toggle, including HTML
    // cache restores. No per-render handlers or per-session timers accumulate.
    inner.addEventListener('toggle',event=>{
      const group=event.target;
      if(group.matches&&group.matches('.background-activity-group')) _rememberBackgroundActivityOpen(group.dataset.backgroundOwner,group.open);
    },true);
  }
  _backgroundActivityObserver.observe(inner,{childList:true});
}
