"""Offline behavioral coverage. No scheduler jobs, providers or real state."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_scheduler_metadata_uses_scheduler_clock(monkeypatch):
    from api.cron import scheduler_timezone
    jobs = types.SimpleNamespace(_hermes_now=lambda: datetime(2026, 1, 1, tzinfo=ZoneInfo("America/Sao_Paulo")))
    monkeypatch.setitem(sys.modules, "cron", types.SimpleNamespace(jobs=jobs))
    assert scheduler_timezone() == "America/Sao_Paulo"


@pytest.mark.parametrize("jobs", [types.SimpleNamespace(), types.SimpleNamespace(_hermes_now=lambda: datetime(2026, 1, 1))])
def test_scheduler_unknown_is_not_host_guess(monkeypatch, jobs):
    from api.cron import scheduler_timezone
    monkeypatch.setitem(sys.modules, "cron", types.SimpleNamespace(jobs=jobs))
    assert scheduler_timezone() is None


def test_editor_usage_and_timezone_behaviors():
    node = os.environ.get("NODE") or shutil.which("node")
    if not node:
        spec = importlib.util.find_spec("playwright")
        if spec:
            candidate = Path(spec.origin).parent / "driver" / "node"
            if candidate.is_file():
                node = str(candidate)
    if not node:
        pytest.skip("Node required for browser-helper behavioral tests")
    script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const source = fs.readFileSync('static/panels.js','utf8');
const context = vm.createContext({console, Intl, Date, Map, Promise, JSON,
  window:{addEventListener(){}}, document:{}, S:{activeProfile:'a'},
  t:k=>k, esc:s=>String(s), $:()=>null});
vm.runInContext(source.slice(source.indexOf('const _editorDrafts'), source.indexOf('async function switchPanel')), context);
vm.runInContext(source.slice(source.indexOf('const _skillUsageCache'), source.indexOf('function _renderInsights(')), context);
vm.runInContext(source.slice(source.indexOf('function _cronTimeLabel'), source.indexOf('function _renderCronDetail')), context);
(async()=>{
  await vm.runInContext(`(async()=>{
    function field(value) { return {id:'text',value,isConnected:true,matches:()=>true}; }
    let root=field('original'), saves=0, discards=0;
    let d=_trackEditor('skill',root,async()=>{saves++;return false;},()=>{discards++;});
    if(_editorIsDirty(d)) throw Error('unchanged is dirty');
    root.value='changed'; if(!_editorIsDirty(d)) throw Error('change missing');
    _editorExitChoice=async()=> 'keep';
    if(await guardUnsavedEditors('skill')!==false || discards) throw Error('keep lost edit');
    _editorExitChoice=async()=> 'save';
    if(await guardUnsavedEditors('skill')!==false || saves!==1 || root.value!=='changed') throw Error('failed save lost edit');
    let ticket=_beginEditorSave('skill');
    if(_beginEditorSave('skill')!==null) throw Error('duplicate save');
    _editorExitChoice=async()=> 'discard';
    if(await guardUnsavedEditors('skill')!==false || discards) throw Error('navigation during save');
    root.value='newer';
    if(_acceptEditorSave(ticket)!==false || !_editorIsDirty(d)) throw Error('inflight typing lost');
    ticket.d.saving=false;
    root.value='changed'; if(_editorIsDirty(d)) throw Error('submitted baseline not adopted');
    root.value='newer';
    if(await guardUnsavedEditors('skill')!==true || discards!==1) throw Error('discard failed');
    d=_trackEditor('skill',field('other'),async()=>true,()=>{});
    if(_acceptEditorSave(ticket)!==false) throw Error('late response overwrites new owner');
    api=async()=>{throw Error('offline');};
    let result=await _loadSkillUsage();
    let html=_renderSkillUsage(result);
    if(!html.includes('skills_usage_failed') || html.includes('insights_skill_usage_no_data') || !html.includes('retrySkillUsage')) throw Error('error shown as empty');
    api=async()=>({usage:{},skill_names:[],total_invocations:0});
    result=await _loadSkillUsage();
    if(!_renderSkillUsage(result).includes('insights_skill_usage_no_data')) throw Error('successful empty missing');
    api=async()=>{throw Error('offline');};
    if(!_renderSkillUsage(await _loadSkillUsage()).includes('skills_usage_stale')) throw Error('cached data not stale');
    S.activeProfile='b';
    if(_renderSkillUsage(await _loadSkillUsage()).includes('skills_usage_stale')) throw Error('cross-profile cache leak');
    if(!_renderSkillUsage({state:'loading'}).includes('loading')) throw Error('loading missing');
    const zoned=_cronTimeLabel('2026-10-11T09:00:00-03:00','America/Sao_Paulo');
    if(!zoned.includes('America/Sao_Paulo') || !zoned.includes('cron_your_time')) throw Error('timezone/local labels absent');
    if(!_cronTimeLabel('2026-10-11T09:00:00',null).includes('cron_timezone_unknown')) throw Error('naive timestamp guessed');
  })()`,context);
  console.log('editor ownership, keep/discard/failure/inflight, usage states/profile isolation, timezone assertions passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result = subprocess.run([node, "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "assertions passed" in result.stdout
