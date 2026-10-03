"""Long-chat cache correctness and deterministic hot-path work budgets.

Runs the shipped JS helpers, not copies of their implementation. Node is the
only optional runtime; no server, network, browser state or provider is needed.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


def cache_source():
    source = (ROOT / "static/ui.js").read_text(encoding="utf-8")
    return source[source.index("const _renderCache ="):source.index("// ── Message-level media snapshot")]


def run_js(source):
    result = subprocess.run([NODE, "-"], input=source, text=True, capture_output=True,
                            cwd=ROOT, timeout=30, check=True)
    return json.loads(result.stdout)


CACHE_ENV = """
const window={_renderUserMarkdown:false};
let calls=0;
function renderMd(text){calls++; return '<md>'+text+'</md>';}
function _renderUserFencedBlocks(text){calls++; return '<plain>'+text+'</plain>';}
function _stripXmlToolCallsDisplay(text){return text;}
"""


@pytest.mark.parametrize("role", ["assistant", "user", "markdown-user"])
def test_equal_length_long_answers_never_reuse_another_messages_middle(role):
    result = run_js(CACHE_ENV + cache_source() + f"""
window._renderUserMarkdown={json.dumps(role == 'markdown-user')};
const user={json.dumps(role != 'assistant')};
const a='same header '.repeat(30)+'ANSWER A'+' same footer'.repeat(30);
const b=a.replace('ANSWER A','ANSWER B');
const first=_getCachedRender(a,user);
const second=_getCachedRender(b,user);
const repeat=_getCachedRender(b,user);
console.log(JSON.stringify({{first,second,repeat,calls}}));
""")
    assert "ANSWER A" in result["first"]
    assert "ANSWER B" in result["second"]
    assert result["repeat"] == result["second"]
    assert result["calls"] == 2


def test_cache_overflow_keeps_hot_history_and_never_exceeds_capacity():
    result = run_js(CACHE_ENV + cache_source() + """
for(let i=0;i<300;i++) _getCachedRender('history '+i,false);
let peak=_renderCache.size;
const before=calls;
for(let i=0;i<100;i++){
  _getCachedRender('new '+i,false);
  for(let hot=250;hot<300;hot++) _getCachedRender('history '+hot,false);
  peak=Math.max(peak,_renderCache.size);
}
console.log(JSON.stringify({extraRenders:calls-before,peak,size:_renderCache.size}));
""")
    assert result["extraRenders"] == 100, result
    assert result["peak"] <= 300, result


def test_cache_clear_and_render_modes_remain_independent():
    result = run_js(CACHE_ENV + cache_source() + """
const plain=_getCachedRender('**same**',true);
window._renderUserMarkdown=true;
const markdown=_getCachedRender('**same**',true);
_getCachedRender('**same**',false);
const before=calls;
_clearRenderCache();
const empty=_renderCache.size;
_getCachedRender('**same**',true);
console.log(JSON.stringify({plain,markdown,before,after:calls,empty}));
""")
    assert result == {"plain": "<plain>**same**</plain>", "markdown": "<md>**same**</md>",
                      "before": 3, "after": 4, "empty": 0}


def test_stable_virtual_height_sync_does_not_visit_loaded_history():
    source = (ROOT / "static/ui.js").read_text(encoding="utf-8")
    helpers = source[source.index("function _messageVirtualHeightEntryMatches("):
                     source.index("function _messageVirtualRoleForEntry(")]
    result = run_js("""
let _messageVirtualHeightCache=[], _messageVirtualHeightCacheEntries=[];
let _messageVirtualHeightCacheLen=0, _messageVirtualHeightCacheSrc=null;
let _messageVirtualWindowKey='';
function _clearMessageVirtualHeightCache(){
  _messageVirtualHeightCache=[];_messageVirtualHeightCacheEntries=[];
  _messageVirtualHeightCacheLen=0;_messageVirtualHeightCacheSrc=null;
}
const S={messages:Array.from({length:50000},(_,i)=>({role:'assistant',content:'row '+i}))};
let visits=0;
const rows=new Proxy(S.messages.map((m,rawIdx)=>({m,rawIdx})),{
  get(target,key){if(/^\\d+$/.test(String(key))) visits++;return target[key];}
});
""" + helpers + """
_syncMessageVirtualHeightCache(rows);
_messageVirtualHeightCache[40000]=321;
visits=0;
for(let i=0;i<20;i++) _syncMessageVirtualHeightCache(rows);
const steadyVisits=visits;
// A same-length replacement must still invalidate; a prepend must carry the
// measured height with the same occurrence, not the old local coordinate.
const older={role:'assistant',content:'older'};
S.messages=[older,...S.messages];
_syncMessageVirtualHeightCache(S.messages.map((m,rawIdx)=>({m,rawIdx})));
const prependedHeight=_messageVirtualHeightCache[40001];
S.messages=S.messages.map(m=>({...m}));
_syncMessageVirtualHeightCache(S.messages.map((m,rawIdx)=>({m,rawIdx})));
console.log(JSON.stringify({steadyVisits,prependedHeight,replacedHeight:_messageVirtualHeightCache[40001]??null}));
""")
    assert result["steadyVisits"] == 0, result
    assert result["prependedHeight"] == 321
    assert result["replacedHeight"] is None
