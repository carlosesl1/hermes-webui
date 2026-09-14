"""Markdown export must hydrate previews and refuse cross-session/partial data."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('scenario', ['complete', 'failed', 'switched', 'clipped'])
def test_markdown_download_uses_full_content(scenario):
    node = shutil.which('node')
    if not node:
        pytest.skip('node required')
    source = (Path(__file__).parents[1] / 'static/boot.js').read_text()
    start = source.index('async function downloadSessionMarkdown()')
    function = source[start:source.index('\n}', start)+2]
    script = r'''
const assert=require('node:assert/strict'), scenario=SCENARIO;
const S={session:{session_id:'parent'},messages:[{content:'clipped',_content_truncated:true}]};
let _messagesTruncated=true,downloads=0,notices=0,body='';
const showToast=()=>notices++,t=x=>x,transcript=()=>S.messages[0].content;
const Blob=function(parts){body=parts[0]};
const URL={createObjectURL:()=>'/blob',revokeObjectURL:()=>{}};
const document={createElement:()=>({click(){downloads++}})};
async function _ensureAllMessagesLoaded(){
  if(scenario==='failed')throw Error('network failed');
  if(scenario==='switched'){S.session={session_id:'other'};return;}
  if(scenario==='clipped')return;
  _messagesTruncated=false;S.messages=[{content:'full result'}];
}
FUNCTION
(async()=>{await downloadSessionMarkdown();
  assert.equal(downloads,scenario==='complete'?1:0);
  if(scenario==='complete') assert.equal(body,'full result');
  if(['failed','clipped'].includes(scenario))assert.equal(notices,1);
})().catch(e=>{console.error(e);process.exit(1)});
'''.replace('SCENARIO', json.dumps(scenario)).replace('FUNCTION', function)
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
