"""Background updates are inspectable activity, not new human questions.

The exact terminal/delegation wrapper shapes mirror the reporter's screenshots;
no text-based rewriting or suppression of assistant content is permitted.
"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(not NODE, reason='node required')


def probe(expression):
    source = ROOT / 'static/background_activity.js'
    script = "const fs=require('fs');const vm=require('vm');vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));" + expression
    result = subprocess.run([NODE, '-e', script, str(source)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_provenance_and_legacy_tool_evidence_not_arbitrary_user_prefixes():
    result = probe(r"""
const content='[IMPORTANT: Background process proc_example completed (exit_code=0).\nCommand: pytest\nOutput:\n508 passed]';
const legacy={role:'user',content};
const tool={role:'tool',content:JSON.stringify({session_id:'proc_example',notify_on_complete:true})};
const messages=[tool,legacy];
console.log(JSON.stringify({
 tagged:backgroundActivityDescriptor({...legacy,_source:'process_wakeup'}),
 unsupported:backgroundActivityDescriptor(legacy),
 known:backgroundActivityOwners(messages).has(1),
 explicitHuman:backgroundActivityOwners([tool,{...legacy,_source:'webui'}]).has(1),
 quoted:backgroundActivityOwners([tool,{role:'user',content:'Please explain '+content}]).has(1),
 assistant:backgroundActivityDescriptor({role:'assistant',content,_source:'process_wakeup'}),
}));
""")
    assert result['tagged']['kind'] == 'completion'
    assert result['tagged']['taskId'] == 'proc_example'
    assert result['unsupported'] is None
    assert result['known'] is False  # known handle does not prove provenance
    assert result['explicitHuman'] is False
    assert result['quoted'] is False
    assert result['assistant'] is None


def test_owners_group_siblings_but_never_cross_real_user_turn():
    result = probe(r"""
const update=(id)=>({role:'user',_source:'process_wakeup',content:'[ASYNC DELEGATION BATCH COMPLETE — '+id+']\nResults'});
const messages=[{role:'user',content:'Build it'}, {role:'assistant',content:'Main answer'},update('deleg_a'),{role:'assistant',content:'Useful background result'}, update('deleg_b'), {role:'assistant',content:'Other result'}, {role:'user',content:'New question'}, {role:'assistant',content:'New answer'},update('deleg_c')];
const before=JSON.stringify(messages), owners=backgroundActivityOwners(messages);
console.log(JSON.stringify({entries:[...owners.entries()],unchanged:before===JSON.stringify(messages)}));
""")
    owners = dict(result['entries'])
    assert 0 not in owners and 1 not in owners
    assert owners[2]['owner'] == owners[4]['owner']
    assert 3 not in owners and 5 not in owners
    assert 6 not in owners and 7 not in owners
    assert owners[8]['owner'] != owners[2]['owner']
    assert result['unchanged']


def test_unknown_trusted_events_still_compact_and_failure_is_not_success():
    result = probe(r"""
console.log(JSON.stringify({
 unknown:backgroundActivityDescriptor({role:'user',_source:'process_wakeup',content:'Future notification format'}),
 error:backgroundActivityDescriptor({role:'user',_source:'process_wakeup',_wakeup_meta:{type:'completion',task_id:'proc_x',exit_code:143},content:'future'}),
 indeterminate:backgroundActivityDescriptor({role:'user',_source:'process_wakeup',_wakeup_meta:{type:'completion',task_id:'proc_x',exit_code:null},content:'future'}),
}));
""")
    assert result['unknown']['kind'] == 'notification'
    assert result['error']['failed'] is True
    assert result['indeterminate']['failed'] is False


@pytest.mark.parametrize('content', ['Final report with artifact link', 'Done', '', [{'type': 'text', 'text': 'Principal synthesis'}]])
def test_principal_assistant_and_tools_never_inherit_notification_ownership(content):
    result = probe("""
const content=CONTENT;
const messages=[{role:'user',content:'Build'},
 {role:'user',_source:'process_wakeup',content:'Legacy terminal event'},
 {role:'assistant',_source:'process_wakeup',content},
 {role:'tool',content:'joined result'},
 {role:'user',content:[{type:'tool_result',content:'joined result'}]},
 {role:'assistant',content:'Final answer'},
 {role:'user',_source:'process_wakeup',content:'Later notification'}];
const before=JSON.stringify(messages);
console.log(JSON.stringify({entries:[...backgroundActivityOwners(messages)],unchanged:before===JSON.stringify(messages)}));
""".replace('CONTENT', json.dumps(content)))
    assert [index for index, _ in result['entries']] == [1, 6]
    assert {entry['owner'] for _, entry in result['entries']} == {0}
    assert result['unchanged']
