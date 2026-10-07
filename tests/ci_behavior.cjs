'use strict';
// Execute unchanged production source slices; only the surrounding browser state
// is supplied here. No copy of the directive or metadata decision algorithm.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const messages = fs.readFileSync(path.join(root, 'static/messages.js'), 'utf8');
const ui = fs.readFileSync(path.join(root, 'static/ui.js'), 'utf8');
function between(src, start, end, from = 0) {
  const a = src.indexOf(start, from);
  assert.notEqual(a, -1, `missing source boundary: ${start}`);
  const b = src.indexOf(end, a + start.length);
  assert.notEqual(b, -1, `missing source boundary: ${end}`);
  return src.slice(a, b);
}
async function directive() {
  const block = between(messages, '  if(_forcedSkillDirectivePending){', '  // Composer textarea + persisted draft');
  const context = vm.createContext({setComposerStatus: () => {}});
  vm.runInContext(`let _forcedSkillDirectivePending=null;
    function setPending(p){_forcedSkillDirectivePending=p;}
    function getPending(){return _forcedSkillDirectivePending;}
    async function consume(activeSid,msgText){${block}\nreturn msgText;}`, context);
  const payload = {name:'Canonical Skill',directive:'Follow skill',content:'Real skill instructions'};
  let resolve;
  const pending = {sessionId:'A',promise:new Promise(r => {resolve=r;})};
  context.setPending(pending);
  assert.equal(await context.consume('B','other session'), 'other session');
  assert.equal(context.getPending(), pending, 'another session cannot consume this directive');
  const sending = context.consume('A','question');
  assert.equal(context.getPending(), pending, 'must wait for fetched content');
  const successor = {sessionId:'A',promise:Promise.resolve('next directive')};
  context.setPending(successor);
  resolve(payload);
  assert.equal(await sending, 'Follow skill\n\n[FORCED SKILL CONTEXT: Canonical Skill]\nReal skill instructions\n[/FORCED SKILL CONTEXT]\n\nquestion');
  assert.equal(context.getPending(), successor, 'old consume/finally must not erase a newly selected skill');
  assert.equal(await context.consume('A',''), 'next directive', 'injection precedes empty-send guard');
  assert.equal(context.getPending(), null);
  assert.equal(await context.consume('A','second message'), 'second message', 'consume only once');
  context.setPending({sessionId:'A',promise:Promise.resolve(null)});
  assert.equal(await context.consume('A','plain'), 'plain');
  assert.equal(context.getPending(), null);
  context.setPending({promise:Promise.resolve('legacy directive')});
  assert.equal(await context.consume('A','plain'), 'legacy directive\n\nplain');
}
function settle(messagesInput, live, session = {}) {
  const start = messages.indexOf("source.addEventListener('done',");
  assert.notEqual(start, -1);
  const block = between(messages, '          const hasMessageToolMetadata=', "          if(typeof projectSessionArtifactsForOwner", start);
  const S = {messages:messagesInput, toolCalls:live};
  // Load the complete true merge helper without reconstructing its behavior.
  const mergeStart = messages.indexOf('function _mergeSettledToolCallsWithLiveMetadata(');
  assert.notEqual(mergeStart, -1);
  const merge = between(messages, 'function _mergeSettledToolCallsWithLiveMetadata(', '\n  // rAF-throttled rendering', mergeStart);
  vm.runInNewContext(`${merge}\n${block}`, {S,d:{session}});
  return S;
}
function metadata() {
  const live = [
    {tid:'a',name:'read_file',activityBurstId:2,duration:1.25,started_at:100,done:false},
    {tid:'b',name:'read_file',activityBurstId:7,duration:3.5,started_at:200,done:false},
  ];
  for (const message of [
    {role:'assistant',tool_calls:[{id:'b',function:{name:'read_file'}}]},
    {role:'assistant',_partial_tool_calls:[{tid:'b',name:'read_file'}]},
    {role:'assistant',content:[{type:'tool_use',id:'b',name:'read_file'}]},
  ]) {
    const S = settle([message], live, {tool_calls:[{tid:'stale'}]});
    assert.equal(S.toolCalls.length, 0, 'message-local anchors take precedence over session cards');
    assert.deepEqual(JSON.parse(JSON.stringify(S._settledLiveToolMetadata)), live.map(tc=>({...tc,done:true})));
    assert.notEqual(S._settledLiveToolMetadata[0], live[0], 'settlement owns a copy');
    const copyBlock = between(ui, '    const derived=[];', '    fallbackToolSources.forEach');
    const out = vm.runInNewContext(`${copyBlock}\n[
      copyLiveToolMetadata({tid:'b'},'read_file','b'),
      copyLiveToolMetadata({tid:'a'},'read_file','a'),
      copyLiveToolMetadata({tid:'c'},'read_file','c'),
      copyLiveToolMetadata({tid:'b',activityBurstId:99,duration:0,started_at:0},'read_file','b')
    ]`, {S});
    assert.deepEqual(JSON.parse(JSON.stringify(out)), [
      {tid:'b',activityBurstId:7,duration:3.5,started_at:200},
      {tid:'a',activityBurstId:2,duration:1.25,started_at:100},
      {tid:'c'},
      {tid:'b',activityBurstId:99,duration:0,started_at:0},
    ], 'metadata must follow tool identity, not same-name ordering, and preserve owned values');
  }
  assert.equal(live[0].done, false, 'settlement must not mutate live source objects');
  const gap = settle([{role:'assistant',content:'tail only'}],live,{_settlement_gap:true});
  assert.equal(gap.toolCalls.length, 0, 'gap fallback cannot retain invalid old window anchors');
  const normal = settle([{role:'assistant',content:'answer'}],live);
  assert.deepEqual(JSON.parse(JSON.stringify(normal.toolCalls)),live.map(tc=>({...tc,done:true})));
  const session = settle([{role:'assistant',content:'answer'}],live,{tool_calls:[{tid:'persisted',name:'terminal'}]});
  assert.equal(session.toolCalls.length, 1);
  assert.equal(session.toolCalls[0].tid, 'persisted');
  const userOnly = settle([{role:'user',tool_calls:[{id:'not-assistant'}]}],live);
  assert.equal(userOnly.toolCalls.length, 2, 'non-assistant metadata does not suppress tools');
}
(async () => {
  if(process.argv[2] === 'directive') await directive();
  else if(process.argv[2] === 'metadata') metadata();
  else throw new Error('expected directive or metadata');
  console.log(`${process.argv[2]} behavior passed`);
})().catch(error => {console.error(error); process.exitCode=1;});
