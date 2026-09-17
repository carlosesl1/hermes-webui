"""A stale session snapshot cannot move or re-pin the current transcript."""
import json
import subprocess
from pathlib import Path

import pytest

SOURCE = (Path(__file__).resolve().parents[1] / 'static/ui.js').read_text()


def function(name):
    start = SOURCE.index('function '+name+'(')
    end = SOURCE.index('\n}', start)+2
    return SOURCE[start:end]


@pytest.mark.parametrize('restore', ['_restoreMessageScrollSnapshot', '_restoreMessageScrollSnapshotSameFrame'])
def test_stale_session_does_not_write(restore):
    script = r'''
const el={scrollTop:400,scrollHeight:2000,clientHeight:500};
const $=()=>el;
const S={session:{session_id:'A'}};
let _messageUserUnpinned=false,_scrollPinned=true,_nearBottomCount=0;
let _lastScrollTop=0,_lastMessageClientHeight=0,_programmaticScroll=false,_programmaticScrollSetAt=0;
const _captureMessageViewportAnchor=()=>null;
const _shouldFollowMessagesOnDomReplace=()=>true;
const _deferClearProgrammaticScroll=()=>{};
''' + '\n'.join(function(name) for name in ['_captureMessageScrollSnapshot','_messageScrollSnapshotInputChanged','_restorePinnedMessageScrollSnapshot',restore]) + r'''
const snapshot=_captureMessageScrollSnapshot();
S.session={session_id:'B'};
el.scrollTop=900;_scrollPinned=false;_messageUserUnpinned=true;
RESTORE(snapshot);
console.log(JSON.stringify({top:el.scrollTop,pinned:_scrollPinned,unpinned:_messageUserUnpinned}));
'''.replace('RESTORE', restore)
    value = json.loads(subprocess.run(['node','-e',script],check=True,capture_output=True,text=True).stdout)
    assert value == {'top':900,'pinned':False,'unpinned':True}
