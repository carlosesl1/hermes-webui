"""Approval requires an explicit control activation, never a document Enter."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

@pytest.mark.skipif(NODE is None, reason="node not on PATH")
@pytest.mark.parametrize("target", ["BODY", "BUTTON", "A", "INPUT", "TEXTAREA", "SELECT", "SUMMARY", "DIV", "SPAN"])
@pytest.mark.parametrize("consumed", [False, True])
def test_document_enter_never_implicitly_approves(target, consumed):
    src = (ROOT / "static/boot.js").read_text()
    start = src.index("document.addEventListener('keydown',async e=>{")
    stop = src.index("const LARGE_TEXT_PASTE_CHAR_THRESHOLD", start)
    listener = src[start:stop]
    harness = r"""
let handler;
const approvals=[];
const el={tagName:TARGET, isContentEditable:TARGET==='DIV'};
global.document={activeElement:{tagName:'BODY'},addEventListener:(type,fn)=>{handler=fn;}};
global.$=id=>id==='approvalCard'?{classList:{contains:()=>true}}:null;
global.respondApproval=x=>approvals.push(x);
""".replace("TARGET", json.dumps(target)) + listener + r"""
(async()=>{
 const e={key:'Enter',target:el,defaultPrevented:CONSUMED,preventDefault(){this.defaultPrevented=true;}};
 await handler(e);
 console.log(JSON.stringify({approvals,prevented:e.defaultPrevented}));
})();
""".replace("CONSUMED", json.dumps(consumed))
    result=subprocess.run([NODE,"-e",harness],capture_output=True,text=True,timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"approvals": [], "prevented": consumed}
