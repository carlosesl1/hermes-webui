"""Offline Chromium UX11: production cron form and async handlers, fake API.

Set UX_TEST_CHROMIUM to an existing browser. No download, server or real core.
"""
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def test_server():
    yield


def function(text, name):
    start = text.index("function " + name + "(")
    if text[start - 6:start] == "async ":
        start -= 6
    return text[start:text.index("\n}", start) + 2]


@pytest.fixture(scope="module")
def browser():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        instance = p.chromium.launch(headless=True, executable_path=os.environ.get("UX_TEST_CHROMIUM"), args=["--no-sandbox"])
        yield instance
        instance.close()


@pytest.fixture(params=[1440, 390])
def page(browser, request):
    src = (ROOT / "static/panels.js").read_text()
    code = r"""
const $=id=>document.getElementById(id), t=k=>({cron_effective_timezone:'Effective timezone',cron_timezone_unknown:'Unknown',cron_your_time:'Your time'})[k]||k;
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
let S={activeProfile:'default'}, _profileSwitchGeneration=0, _currentPanel='tasks';
let _cronMode='empty',_cronPreFormDetail=null,_currentCronDetail=null,_editingCronId=null,_cronIsDuplicate=false;
let _cronSelectedSkills=[],_cronSkillsCache=null,_cronList=null,_showAllCronProfiles=false,_cronOtherProfileCount=0;
let _cronSchedulerMetadata=null,_cronListSequence=0,_cronPreviewSequence=0,_cronPreviewTimer=null;
const _editorDrafts=new Map();
let requests=[];
function api(url){
  if(url.startsWith('/api/crons')) return new Promise((resolve,reject)=>{requests.push({url,resolve,reject})});
  return Promise.resolve({skills:[]});
}
function guardUnsavedEditors(){return Promise.resolve(true)}
function _trackEditor(){} function _editorLoadedField(){} function saveCronForm(){} function cancelCronForm(){}
function _populateCronDeliverOptions(){} function _populateCronFormModelSelect(){} function _renderCronSkillTags(){}
function _setCronHeaderButtons(){} function _closeMobileSidebarAfterPanelSelection(){} function _bindCronSkillPicker(){}
function _cronProfileOptions(){return '<option value="">Default</option>'}
function loadCronProfiles(){return Promise.resolve([])} function loadCronGatewayNotice(){} function _appendCronProfileToggle(){}
function _clearCronDetail(){} function _renderCronDetail(){}
function ready(overrides={}){return {active_profile:'default',scheduler_timezone:'America/Sao_Paulo',status:'ready',schedule:{kind:'cron'},next_run_at:'2026-10-11T09:00:00-03:00',...overrides}}
"""
    code += src[src.index("function _cronScheduleKindForInput"):src.index("function _isCronNeedsAttention")]
    code += "\n" + "\n".join(function(src, name) for name in ["_cronTimeLabel", "_renderCronForm", "openCronCreate", "openCronEdit", "loadCrons"])
    page = browser.new_page(viewport={"width": request.param, "height": 900}, timezone_id="Europe/London")
    page.set_default_timeout(5000)
    page.route("**/*", lambda route: route.abort())
    page.set_content('<div id="cronList"></div><h2 id="taskDetailTitle"></h2><div id="taskDetailBody"></div><div id="taskDetailEmpty"></div>')
    page.add_style_tag(content=(ROOT / "static/style.css").read_text())
    page.add_script_tag(content=code)
    yield page
    page.close()


def open_empty_profile(page):
    page.evaluate("()=>{window.loaded=loadCrons()}")
    page.wait_for_function("requests.length===1")
    page.evaluate("async()=>{requests[0].resolve({jobs:[],active_profile:'default',scheduler_timezone:'America/Sao_Paulo',schedule_preview_supported:true});await loaded;await openCronCreate()}")
    assert 'America/Sao_Paulo' in page.locator('#cronFormSchedulerZone').inner_text()
    page.wait_for_function("requests.length===2")


def test_empty_list_create_next_run_and_custom_statuses(page):
    open_empty_profile(page)
    page.evaluate("()=>{requests[1].resolve(ready())}")
    page.wait_for_function("$('cronFormNextRun').textContent.includes('Your time')")
    text = page.locator('#cronFormNextRun').inner_text()
    assert 'America/Sao_Paulo' in text and 'Europe/London' in text
    page.locator('#cronFormSchedulePreset').select_option('custom')
    assert page.locator('#cronFormNextRun').is_visible()
    assert 'Checking' in page.locator('#cronFormNextRun').inner_text()
    # No raw exception HTML is interpreted.
    page.locator('#cronFormSchedule').fill('invalid <img>')
    page.wait_for_function("requests.at(-1).url.includes('schedule=invalid%20%3Cimg%3E')")
    page.evaluate("()=>{requests.at(-1).resolve(ready({status:'invalid',error:'<img src=x>',next_run_at:null,schedule:null}))}")
    page.wait_for_function("$('cronFormNextRun').textContent.includes('Invalid schedule')")
    assert page.locator('#cronFormNextRun img').count() == 0
    for value, status, phrase in [('past','no_next_run','no next execution'),('30m','unavailable','unavailable')]:
        count = page.evaluate('requests.length')
        page.locator('#cronFormSchedule').fill(value)
        assert '2026' not in page.locator('#cronFormNextRun').inner_text()
        page.wait_for_function(f'requests.length==={count + 1}')
        page.evaluate('(status)=>{requests.at(-1).resolve(ready({status,next_run_at:null,schedule:null}))}', status)
        page.wait_for_function('(phrase)=>$("cronFormNextRun").textContent.includes(phrase)', arg=phrase)
    # The installed core, not a hardcoded duration heuristic, classifies one-shot.
    page.locator('#cronFormSchedule').fill('in 30m')
    page.wait_for_function("requests.at(-1).url.includes('schedule=in%2030m')")
    page.evaluate("()=>{requests.at(-1).resolve(ready({schedule:{kind:'once'}}))}")
    page.wait_for_function("$('cronFormScheduleOnceWarning').style.display==='' ")
    assert page.locator('#cronFormScheduleOnceWarning').is_visible()
    assert page.evaluate("_cronTimeLabel('2026-10-11T09:00:00',null)").endswith('Unknown')
    assert 'Your time' not in page.evaluate("_cronTimeLabel('2026-10-11T09:00:00+01:00','Europe/London')")
    assert page.locator('#cronFormNextRun').get_attribute('role') == 'status'
    rect = page.locator('#cronFormNextRun').bounding_box()
    assert rect['x'] >= 0 and rect['x'] + rect['width'] <= page.viewport_size['width']
    evidence = os.environ.get('UX11_EVIDENCE')
    if evidence:
        Path(evidence).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(evidence) / f'cron-preview-{page.viewport_size["width"]}.png'), full_page=True)


@pytest.mark.parametrize('race', ['edit_restore', 'profile_restore', 'replace_form'])
def test_late_preview_cannot_label_new_input_or_owner(page, race):
    open_empty_profile(page)
    if race == 'edit_restore':
        page.locator('#cronFormSchedulePreset').select_option('custom')
        page.locator('#cronFormSchedule').fill('0 10 * * *')
        page.locator('#cronFormSchedule').fill('0 9 * * *')
    elif race == 'profile_restore':
        page.evaluate("()=>{S.activeProfile='other';++_profileSwitchGeneration;S.activeProfile='default';++_profileSwitchGeneration;_syncCronScheduleWarning()}")
        assert 'Unknown' in page.locator('#cronFormSchedulerZone').inner_text()
    else:
        page.evaluate("()=>openCronEdit({id:'other',schedule_display:'0 9 * * *'})")
    page.evaluate("()=>{requests[1].resolve(ready({next_run_at:'2099-01-01T00:00:00Z',scheduler_timezone:'Asia/Tokyo'}))}")
    page.wait_for_function('requests.length===3')
    assert '2099' not in page.locator('#cronFormNextRun').inner_text()
    assert 'Asia/Tokyo' not in page.locator('#cronFormSchedulerZone').inner_text()
    page.evaluate("()=>{requests[2].reject(Error('offline'))}")
    page.wait_for_function("$('cronFormNextRun').textContent.includes('Could not load')")


def test_late_list_metadata_does_not_cross_profile_generation(page):
    page.evaluate("()=>{window.oldList=loadCrons()}")
    page.wait_for_function('requests.length===1')
    page.evaluate("()=>{S.activeProfile='other';++_profileSwitchGeneration;S.activeProfile='default';++_profileSwitchGeneration;window.newList=loadCrons()}")
    page.wait_for_function('requests.length===2')
    page.evaluate("async()=>{requests[1].resolve({jobs:[],active_profile:'default',scheduler_timezone:'Europe/London'});await newList;requests[0].resolve({jobs:[],active_profile:'default',scheduler_timezone:'Asia/Tokyo'});await oldList;await openCronCreate()}")
    assert 'Europe/London' in page.locator('#cronFormSchedulerZone').inner_text()
    assert 'Asia/Tokyo' not in page.locator('#cronFormSchedulerZone').inner_text()
