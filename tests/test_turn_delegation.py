"""Transport policy tests; runtime child lifecycle remains owned by Hermes."""
import copy
import inspect
import json
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor

import pytest
from api.turn_delegation import joined_tool_schemas, turn_owned_agent_class

SCHEMA = [{'type':'function','function':{'name':'delegate_task','description':
    'Old background transport. END YOUR TURN. USE FOR: work. RULES: verify self-reports.',
    'parameters':{'properties':{'tasks':{'maxItems':10}}}}}]

class Base:
    def __init__(self, tools=None):
        self.tools = tools
        self._interrupt_requested = False
        self._active_children = []
    def _dispatch_delegate_task(self, args):
        return 'other-transport-background'

@pytest.fixture
def core(monkeypatch):
    module = types.ModuleType('tools.delegate_tool')
    module._strip_model_hidden_task_fields = lambda tasks: [{k:v for k,v in t.items() if k != 'internal'} for t in tasks] if tasks else tasks
    monkeypatch.setitem(sys.modules,'tools.delegate_tool',module)
    return module


def test_schema_rewrite_does_not_mutate_registry_or_other_agent():
    before = copy.deepcopy(SCHEMA)
    a = turn_owned_agent_class(Base)(SCHEMA)
    assert SCHEMA == before
    assert Base(SCHEMA).tools == before
    assert 'JOINED' in a.tools[0]['function']['description']
    assert 'END YOUR TURN' not in a.tools[0]['function']['description']
    assert 'RULES: verify self-reports.' in a.tools[0]['function']['description']
    assert a.tools[0]['function']['parameters'] == before[0]['function']['parameters']
    assert inspect.signature(type(a).__init__) == inspect.signature(Base.__init__)


def test_schema_refresh_and_reuse_are_idempotent():
    a = turn_owned_agent_class(Base)(SCHEMA)
    first = copy.deepcopy(a.tools)
    a.tools = SCHEMA
    assert a.tools == first
    assert joined_tool_schemas(first) == first
    assert turn_owned_agent_class(Base) is type(a)
    assert a._dispatch_delegate_task.__func__ is not Base._dispatch_delegate_task


def test_unsupported_runtime_keeps_honest_schema_and_non_delegation_unchanged():
    class Older:pass
    assert turn_owned_agent_class(Older) is Older
    tool={'function':{'name':'terminal','description':'original'}}
    assert joined_tool_schemas([tool])[0] is tool
    assert joined_tool_schemas(None) is None


@pytest.mark.parametrize('action',[None,'spawn','list','steer','stop'])
def test_authoritative_dispatch_ignores_detach_request_and_strips_hidden_fields(core,action):
    seen=[]
    core.delegate_task=lambda **kw: seen.append(kw) or json.dumps({'results':[{'status':'completed'}]})
    a=turn_owned_agent_class(Base)(SCHEMA)
    result=a._dispatch_delegate_task({'tasks':[{'goal':'a','internal':'secret'}],'background':True,'action':action,'subagent_id':'child','message':'stop'})
    assert json.loads(result)['results'][0]['status']=='completed'
    assert seen[0]['background'] is False
    assert seen[0]['parent_agent'] is a
    assert seen[0]['tasks']==[{'goal':'a'}]
    assert seen[0]['action']==action
    assert Base()._dispatch_delegate_task({})=='other-transport-background'


def test_joined_boundary_keeps_sequential_calls_and_parallel_children_in_same_turn(core):
    start=threading.Barrier(3);release=[threading.Event(),threading.Event()];finished=[]
    def child(i):
        start.wait(timeout=3);release[i].wait(timeout=3)
        return {'status':'completed','summary':str(i)}
    def delegate(**kw):
        assert kw['background'] is False
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(child,i) for i in range(2)]
            return json.dumps({'results':[f.result(timeout=4) for f in futures]})
    core.delegate_task=delegate
    a=turn_owned_agent_class(Base)(SCHEMA)
    t=threading.Thread(target=lambda:finished.append(a._dispatch_delegate_task({'tasks':[{'goal':'a'},{'goal':'b'}]})))
    t.start();start.wait(timeout=3)
    try:
        assert not finished
        release[0].set();t.join(timeout=.03);assert t.is_alive() and not finished
        release[1].set();t.join(timeout=3);assert not t.is_alive()
        assert len(json.loads(finished[0])['results'])==2
        core.delegate_task=lambda **kw: json.dumps({'results':[{'summary':'second','status':'completed'}]})
        assert json.loads(a._dispatch_delegate_task({'tasks':[{'goal':'second'}]}))['results'][0]['summary']=='second'
    finally:
        for e in release:e.set()
        t.join(timeout=3)


@pytest.mark.parametrize('status',['error','timeout','interrupted'])
def test_failed_outcomes_are_passed_back_instead_of_detached_or_forged(core,status):
    result=json.dumps({'results':[{'status':status,'error':'original diagnostic'},{'status':'completed','summary':'ok'}]})
    core.delegate_task=lambda **kw:result
    assert turn_owned_agent_class(Base)(SCHEMA)._dispatch_delegate_task({'tasks':[{'goal':'a'}]})==result


def test_cancel_keeps_same_parent_authority_and_does_not_bleed_next_turn(core):
    seen=[]
    def delegate(**kw):
        seen.append(kw['parent_agent']._interrupt_requested)
        return json.dumps({'results':[{'status':'interrupted' if seen[-1] else 'completed'}]})
    core.delegate_task=delegate
    a=turn_owned_agent_class(Base)(SCHEMA);a._interrupt_requested=True
    assert json.loads(a._dispatch_delegate_task({'tasks':[{'goal':'a'}]}))['results'][0]['status']=='interrupted'
    successor=turn_owned_agent_class(Base)(SCHEMA)
    assert json.loads(successor._dispatch_delegate_task({'tasks':[{'goal':'b'}]}))['results'][0]['status']=='completed'
    assert seen==[True,False]
