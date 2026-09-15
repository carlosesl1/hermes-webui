/* Background continuations are transcript-owned activity, not human questions.
 * This module is a display projection only: never mutate messages, roles, source,
 * provider context or delivery acknowledgements. Canonical indices stay intact.
 */
function backgroundActivityText(message){
  const value=message&&message.content;
  const text=typeof value==='string'?value:(Array.isArray(value)?value.filter(p=>p&&p.type==='text').map(p=>p.text||'').join('\n'):'');
  return text.replace(/^\[Workspace::v1: [^\n]+\]\s*\n/,'');
}
function backgroundActivityDescriptor(message){
  if(!message||message.role!=='user'||message._source!=='process_wakeup') return null;
  const source=message._source;
  const text=backgroundActivityText(message);
  let match=text.match(/^\[IMPORTANT: Background process (\S+) completed \(exit_code=([^)\n]+)\)\.\nCommand: [^\n]*\nOutput:\n[\s\S]*\]$/);
  let kind='notification',taskId='',exitCode=null;
  if(match){kind='completion';taskId=match[1];exitCode=match[2];}
  else if((match=text.match(/^\[IMPORTANT: Background process (\S+) matched watch pattern "[^\n]*"\.\nCommand: [^\n]*\nMatched output:\n[\s\S]*\]$/))){kind='watch_match';taskId=match[1];}
  else if((match=text.match(/^\[ASYNC DELEGATION BATCH COMPLETE — (\S+)\]\n/))){kind='async_delegation';taskId=match[1];}
  else if(text.startsWith('[BACKGROUND UPDATES]\n')) kind='completion_batch';
  // Only authoritative provenance may classify a notification. Source-less
  // legacy messages remain ordinary messages: a matching handle is not proof.
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
  const owners=new Map();
  let human=-1,active=null;
  (messages||[]).forEach((message,index)=>{
    if(!message) return;
    if(message.role==='user'){
      // Anthropic tool results aren't new human turns either.
      if(Array.isArray(message.content)&&message.content.length&&message.content.every(p=>p&&p.type==='tool_result')) return;
      const descriptor=backgroundActivityDescriptor(message);
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
let _backgroundActivityRecycled=new Map();
let _backgroundActivityFocusedSummary='';
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
    const key=group.dataset.backgroundOwner;
    _rememberBackgroundActivityOpen(key,group.open);
    _backgroundActivityRecycled.set(key,group);
    if(group.querySelector('summary')===document.activeElement) _backgroundActivityFocusedSummary=key;
    const body=group.querySelector('.background-activity-content');
    if(body) while(body.firstChild) inner.insertBefore(body.firstChild,group);
    group.remove();
  }
}
function prepareBackgroundActivityRender(inner){
  if(_backgroundActivityObserver) _backgroundActivityObserver.disconnect();
  _backgroundActivityRecycled.clear();
  _backgroundActivityFocusedSummary='';
  _unwrapBackgroundActivity(inner);
}
function _createBackgroundActivityGroup(key,sessionId){
  const group=document.createElement('details');
  group.className='background-activity-group';
  group.dataset.backgroundOwner=key;group.dataset.sessionId=sessionId;
  group.open=_backgroundActivityOpen.get(key)===true;
  const summary=document.createElement('summary');summary.className='background-activity-summary';
  const title=document.createElement('span');title.className='background-activity-title';
  title.textContent=_backgroundActivityLabel('background_activity_title','Execution activity');
  const status=document.createElement('span');status.className='background-activity-status';
  summary.append(title,status);group.append(summary);
  const body=document.createElement('div');body.className='background-activity-content';group.append(body);
  return group;
}
function syncBackgroundActivity(inner){
  if(!inner||_backgroundActivitySyncing||typeof S==='undefined') return;
  _backgroundActivitySyncing=true;
  if(_backgroundActivityObserver) _backgroundActivityObserver.disconnect();
  try{
    // Keep each rendered window separate. A spacer is a hard boundary: never
    // move rows across it. The virtualizer measures disclosures as shared units.
    const virtualized=!!inner.querySelector('.message-virtual-spacer');
    const messages=S.messages||[],owners=backgroundActivityOwners(messages);
    const sessionId=S.session&&S.session.session_id||'';
    const existing=new Map(_backgroundActivityRecycled),rows=[];
    for(const node of Array.from(inner.children)){
      if(node.classList.contains('background-activity-group')){
        existing.set(node.dataset.backgroundOwner,node);
        rows.push(...node.querySelector('.background-activity-content').children);
      }else rows.push(node);
    }
    const groups=new Map();
    let windowIndex=0;
    for(const row of rows){
      if(row.matches('.message-virtual-spacer')){windowIndex++;continue;}
      if(!row.matches('.msg-row,.assistant-turn')) continue;
      const indexed=row.hasAttribute('data-msg-idx')?row:row.querySelector('[data-msg-idx]');
      let raw=indexed?Number(indexed.dataset.msgIdx):NaN;
      if(!Number.isInteger(raw)&&row.matches('#liveAssistantTurn,[data-live-assistant="1"]')) raw=messages.length-1;
      const entry=owners.get(raw);
      if(!entry){
        const prior=row.closest('.background-activity-group');
        if(prior&&prior.parentElement===inner) inner.insertBefore(row,prior);
        continue;
      }
      const absolute=typeof _messageSessionIndexForRawIdx==='function'?_messageSessionIndexForRawIdx(entry.owner):entry.owner;
      const key=sessionId+':'+absolute+(virtualized?':window:'+windowIndex:'');
      let record=groups.get(key);
      if(!record){
        const group=existing.get(key)||_createBackgroundActivityGroup(key,sessionId);
        const body=group.querySelector('.background-activity-content');
        if(group.parentElement!==inner) inner.insertBefore(group,row.parentElement===inner?row:null);
        record={group,body,status:group.querySelector('.background-activity-status'),events:new Set(),failed:false,live:false};
        groups.set(key,record);
      }
      // Don't detach already-owned rows or summaries: browser focus and live
      // renderer references must survive token and nested-control updates.
      if(row.parentElement!==record.body) record.body.appendChild(row);
      if(entry.eventIndex!==null) record.events.add(entry.eventIndex);
      record.failed=record.failed||entry.failed;
      record.live=record.live||!!(S.busy&&raw>=messages.length-1);
    }
    for(const [key,group] of existing){
      if(!groups.has(key)&&group.parentElement===inner){
        const body=group.querySelector('.background-activity-content');
        while(body.firstChild) inner.insertBefore(body.firstChild,group);
        group.remove();
      }
    }
    groups.forEach(record=>{
      const {group,status}=record;
      let state=record.live?_backgroundActivityLabel('background_activity_running','Working'):_backgroundActivityLabel('background_activity_available','Updates available');
      if(record.failed) state=_backgroundActivityLabel('background_activity_failure','Includes a failed task');
      const label=record.events.size+' · '+state;
      if(status.textContent!==label) status.textContent=label;
      group.classList.toggle('has-failure',record.failed);
      group.classList.toggle('is-running',record.live);
      if(group.querySelector('.clarify-card,.approval-card,[data-approval-id]')||
         (group.contains(document.activeElement)&&document.activeElement!==group.querySelector('summary'))) group.open=true;
      if(_backgroundActivityFocusedSummary===group.dataset.backgroundOwner) group.querySelector('summary').focus({preventScroll:true});
    });
  }finally{
    _backgroundActivityFocusedSummary='';
    _backgroundActivityRecycled.clear();
    _backgroundActivitySyncing=false;
    observeBackgroundActivity(inner);
  }
}
// Allocate each disclosure's actual height across only the rendered canonical
// entries it owns. Hidden descendants must never be cached at their full height,
// and a repeated window must never count the same group once per message.
function backgroundActivityVirtualHeight(inner,entry,renderedEntries){
  if(!inner||!entry||!Array.isArray(renderedEntries)) return null;
  const primary=inner.querySelector(`[data-msg-idx="${entry.rawIdx}"]`);
  const group=primary&&primary.closest('.background-activity-group');
  if(!group||group.parentElement!==inner) return null;
  let members=0;
  for(const item of renderedEntries){
    const node=inner.querySelector(`[data-msg-idx="${item.rawIdx}"]`);
    if(node&&node.closest('.background-activity-group')===group) members++;
  }
  return members?Math.max(1,group.getBoundingClientRect().height/members):null;
}
function observeBackgroundActivity(inner){
  if(typeof MutationObserver==='undefined'||!inner) return;
  if(_backgroundActivityRoot!==inner){
    if(_backgroundActivityObserver) _backgroundActivityObserver.disconnect();
    _backgroundActivityRoot=inner;
    _backgroundActivityObserver=new MutationObserver(()=>{
      if(typeof S!=='undefined'&&S.session) syncBackgroundActivity(inner);
    });
    inner.addEventListener('toggle',event=>{
      const group=event.target;
      if(group.matches&&group.matches('.background-activity-group')){
        _rememberBackgroundActivityOpen(group.dataset.backgroundOwner,group.open);
        // Disclosure geometry, not hidden descendants, is the virtual unit.
        if(inner.querySelector('.message-virtual-spacer')&&typeof _scheduleMessageVirtualizedRender==='function') _scheduleMessageVirtualizedRender(true);
      }
    },true);
  }
  // Approvals and clarify cards are inserted inside the existing live shell,
  // not necessarily as direct transcript children.
  _backgroundActivityObserver.observe(inner,{childList:true,subtree:true});
}
