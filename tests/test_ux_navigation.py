"""Offline Chromium behavior: real routing, panel/section/selectors and dirty guard.

Only data/render boundaries are fixtures; no app server, providers or real state.
"""
import os
from pathlib import Path
import subprocess

import pytest

sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def _function(source, name):
    start = source.index('function ' + name + '(')
    if source[max(0, start - 6):start] == 'async ':
        start -= 6
    return source[start:source.index('\n}', start) + 2]


def _document():
    panels = (ROOT / 'static/panels.js').read_text()
    if os.environ.get('UX_NAV_BASELINE'):
        panels = subprocess.check_output(['git', 'show', 'HEAD:static/panels.js'], cwd=ROOT, text=True)
    sessions = (ROOT / 'static/sessions.js').read_text()
    names = ['_editorSnapshot', '_trackEditor', '_editorIsDirty', '_editorExitChoice',
             'guardUnsavedEditors', 'switchPanel', 'switchSettingsSection', 'openSkill',
             'openCronDetail', 'openWorkspaceDetail', 'openProfileDetail']
    code = '''
let S={_bootReady:true,activeProfile:'default',session:{session_id:'A'},busy:false};
let _currentPanel='chat',_currentSettingsSection='conversation',_settingsSection='conversation';
let _skillMode='empty',_currentSkillDetail=null,_skillLoadGeneration=0,_skillPreFormDetail=null,_editingSkillName=null;
let _cronMode='empty',_currentCronDetail=null,_cronPreFormDetail=null,_editingCronId=null;
let _workspaceMode='empty',_currentWorkspaceDetail=null,_workspacePreFormDetail=null;
let _profileMode='empty',_currentProfileDetail=null,_profilePreFormDetail=null;
let _editorDrafts=new Map(),_editorGuardPending=false,_settingsDirty=false;
const MAIN_VIEW_PANELS=['settings','skills','memory','tasks','kanban','workspaces','profiles','insights','logs'];
let _skillsData=[{name:'alpha'},{name:'beta'}],_cronList=[{id:'1'},{id:'2'},{id:'3',read_only:true}];
let _workspaceList=[{path:'/example/one'},{path:'/example/two'}];
let _profilesCache={profiles:[{name:'default'},{name:'other'}],active:'default'};
let notices=[],requests=[],deferred=[],_profileSwitchGeneration=0;
function $(id){return document.getElementById(id)}
function t(s){return s} function esc(s){return String(s)} function showToast(s){notices.push(s)}
function _beforePanelSwitch(next){return !(_settingsDirty&&next!=='settings')}
function _beginSettingsPanelSession(){} function _resyncChatSidebarAfterPanelSwitch(){}
function syncTopbar(){} function syncAppTitlebar(){} function _syncLogsAutoRefresh(){}
function _closeMobileSidebarAfterPanelSelection(){} function setStatus(s){notices.push(s)}
async function loadSkills(){} async function loadCrons(){} async function loadWorkspacesPanel(){}
async function loadProfilesPanel(){} async function loadMemory(){} async function loadKanban(){}
async function loadInsights(){} async function loadLogs(){} function loadTodos(){}
let settingsHydrations=0;function loadSettingsPanel(){settingsHydrations++;} function loadProvidersPanel(){} function loadPluginsPanel(){} function loadExtensionsPanel(){}
function _setSkillHeaderButtons(){} function _renderSkillError(){}
function _renderSkillDetail(name){_skillMode='read';$('skillDetailBody').textContent=name}
function _renderCronDetail(job){_cronMode='read';_currentCronDetail=job}
function _renderWorkspaceDetail(ws){_workspaceMode='read';_currentWorkspaceDetail=ws}
function _renderProfileDetail(p){_profileMode='read';_currentProfileDetail=p}
function _clearCronDetail(){_currentCronDetail=null;_cronMode='empty'}
function _clearWorkspaceDetail(){_currentWorkspaceDetail=null;_workspaceMode='empty'}
function _clearProfileDetail(){_currentProfileDetail=null;_profileMode='empty'}
function _findCronJob(j){return typeof j==='object'?j:_cronList.find(x=>x.id===j)}
function _cronJobKey(j){return j.id} function _cronItemId(j){return 'cron-'+j.id}
function _clearCronUnreadForJob(){} function _stopCronWatch(){} function _checkCronWatchOnDetail(){}
async function api(url){requests.push(url);if(window.delaySkill)return new Promise(r=>deferred.push(r));return {content:'fixture'}}
async function loadSession(sid,opts){requests.push({sid,opts});S.session={session_id:sid};_setActiveSessionUrl(sid)}
'''
    code += '\n'.join(_function(panels, name) for name in names)
    code += '\n' + '\n'.join(_function(sessions, name) for name in ['_sessionIdFromLocation', '_sessionUrlForSid', '_setActiveSessionUrl'])
    code += '\n' + (ROOT / 'static/navigation.js').read_text()
    # The production sessions.js owns the sole listener; use its exact body.
    start = sessions.index("  window.addEventListener('popstate', () => {")
    code += '\n' + sessions[start:sessions.index('\n  });', start) + 6]
    code += '\nwindow.ready=initPersistentNavigation();'
    panes = ''.join(f'<section id="panel{x.title()}" class="panel-view"></section>' for x in ['chat', 'settings', 'skills', 'tasks', 'profiles', 'workspaces'])
    sections = ''.join(f'<section id="settingsPane{x.title()}"></section>' for x in ['conversation', 'appearance', 'preferences', 'providers', 'plugins', 'extensions', 'system', 'help'])
    return f'''<!doctype html><base href="/"><main class="main">{panes}{sections}
<div id="skillDetailBody"></div><div id="skillDetailTitle"></div><div id="skillDetailEmpty"></div>
<textarea id="editor"></textarea></main><script>{code}</script>'''


@pytest.fixture(params=[1440, 390])
def page(request):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'], executable_path=os.environ.get('UX_TEST_CHROMIUM'))
        page = browser.new_page(viewport={'width': request.param, 'height': 900})
        page.route('http://navigation.test/**', lambda route: route.fulfill(body=_document(), content_type='text/html'))
        page.goto('http://navigation.test/session/A')
        page.evaluate('window.ready')
        yield page
        browser.close()


def test_settings_reload_back_forward(page):
    page.evaluate("async()=>{await switchPanel('settings');switchSettingsSection('appearance')}")
    assert '#panel=settings&section=appearance' in page.url
    page.reload()
    page.evaluate('window.ready')
    assert page.evaluate('_currentPanel') == 'settings'
    assert page.locator('#settingsPaneAppearance').get_attribute('class') == 'active'
    count = page.evaluate('history.length')
    page.go_back()
    page.wait_for_function("_currentSettingsSection==='conversation' && !_navigation.restoring")
    page.go_forward()
    page.wait_for_function("_currentSettingsSection==='appearance' && !_navigation.restoring")
    assert page.evaluate('history.length') == count


@pytest.mark.parametrize('panel,item,selector', [
    ('skills', 'beta', "openSkill('beta')"), ('tasks', '2', "openCronDetail('2')"),
    ('workspaces', '/example/two', "openWorkspaceDetail('/example/two')"),
    ('profiles', 'other', "openProfileDetail('other')"),
])
def test_read_only_items_restore(page, panel, item, selector):
    page.evaluate(f"async()=>{{await switchPanel('{panel}');await {selector}}}")
    assert page.evaluate("new URLSearchParams(location.hash.slice(1)).get('item')") == item
    page.reload()
    page.evaluate('window.ready')
    assert page.evaluate('_navigationSnapshot().item') == item
    assert page.evaluate('S.activeProfile') == 'default'
    assert page.evaluate('S.session.session_id') == 'A'


def test_dirty_veto_preserves_url_and_history_position(page):
    page.evaluate("async()=>{await switchPanel('settings');await switchPanel('skills')}")
    url = page.url
    page.evaluate("()=>{_trackEditor('skill',$('editor'),async()=>false,()=>{});$('editor').value='private draft'}")
    page.go_back()
    page.locator('[data-choice=keep]').click()
    page.wait_for_function('!_navigation.rollback && !_navigation.restoring')
    assert page.url == url
    assert page.evaluate('_currentPanel') == 'skills'
    assert page.locator('#editor').input_value() == 'private draft'
    page.go_back()
    page.locator('[data-choice=discard]').click()
    page.wait_for_function("_currentPanel==='settings' && !_navigation.restoring")
    page.go_forward()
    page.wait_for_function("_currentPanel==='skills' && !_navigation.restoring")


def test_missing_foreign_and_invalid_routes(page):
    for fragment in ['panel=skills&item=deleted&scope=default', 'panel=skills&item=beta&scope=other']:
        page.goto('http://navigation.test/session/A#' + fragment)
        page.reload()
        page.evaluate('window.ready')
        assert page.evaluate('_currentSkillDetail') is None
        assert page.evaluate('S.activeProfile') == 'default'
        assert 'item=' not in page.url
    page.goto('http://navigation.test/session/A#panel=evil&section=javascript%3Aalert(1)')
    page.reload()
    page.evaluate('window.ready')
    assert page.evaluate('_currentPanel') == 'chat'
    page.evaluate("()=>{history.replaceState(null,'','?prompt=secret&token=secret');switchSettingsSection('appearance')}")
    page.evaluate("switchPanel('settings')")
    assert 'secret' not in page.url


def test_late_skill_cannot_own_new_panel_or_profile(page):
    page.evaluate("async()=>{await switchPanel('skills');window.delaySkill=true;window.old=openSkill('alpha')}")
    page.wait_for_function('deferred.length===1')
    page.evaluate("async()=>{await switchPanel('settings');deferred[0]({content:'late'});await window.old}")
    assert page.evaluate('_currentSkillDetail') is None
    assert '#panel=settings' in page.url
    page.evaluate("async()=>{await switchPanel('skills');window.old=openSkill('beta')}")
    page.wait_for_function('deferred.length===2')
    page.evaluate("async()=>{S.activeProfile='other';++_profileSwitchGeneration;S.activeProfile='default';++_profileSwitchGeneration;deferred[1]({content:'wrong profile'});await window.old}")
    assert page.evaluate('_currentSkillDetail') is None


def test_sessions_history_preserves_panels_without_push_loop(page):
    page.evaluate("async()=>{await switchPanel('settings');switchSettingsSection('appearance');await loadSession('B')}")
    count = page.evaluate('history.length')
    page.go_back()
    page.wait_for_function("S.session.session_id==='A' && !_navigation.restoring")
    assert page.evaluate('_currentSettingsSection') == 'appearance'
    page.go_forward()
    page.wait_for_function("S.session.session_id==='B' && !_navigation.restoring")
    assert page.evaluate('history.length') == count
    assert page.evaluate("requests.filter(x=>x.sid).every(x=>!x.opts||x.opts.skipProfileResolve===true)")


def test_failed_save_and_dirty_settings_veto(page):
    page.evaluate("async()=>{await switchPanel('skills');_trackEditor('skill',$('editor'),async()=>false,()=>{});$('editor').value='keep me'}")
    url = page.url
    page.evaluate("()=>{window.attempt=switchPanel('settings')}")
    page.locator('[data-choice=save]').click()
    assert page.evaluate('window.attempt') is False
    assert page.url == url
    assert page.locator('#editor').input_value() == 'keep me'
    page.evaluate("async()=>{_editorDrafts.clear();await switchPanel('settings');_settingsDirty=true}")
    url = page.url
    assert page.evaluate("switchPanel('chat')") is False
    assert page.url == url


def test_busy_history_veto_keeps_view_and_position(page):
    page.evaluate("async()=>{await switchPanel('settings');await loadSession('B');S.busy=true}")
    url = page.url
    page.go_back()
    page.wait_for_function('!_navigation.rollback && !_navigation.restoring')
    assert page.url == url
    assert page.evaluate('S.session.session_id') == 'B'
    assert page.evaluate('_currentPanel') == 'settings'


def test_settings_history_preserves_unsaved_form_without_rehydration(page):
    page.evaluate("async()=>{await switchPanel('settings');switchSettingsSection('appearance');switchSettingsSection('preferences');$('editor').value='unsaved max_tokens 8192';_settingsDirty=true;}")
    before = page.evaluate("settingsHydrations")
    page.evaluate("history.back()")
    page.wait_for_function("_currentSettingsSection === 'appearance' && !_navigation.restoring")
    assert page.evaluate("$('editor').value") == 'unsaved max_tokens 8192'
    assert page.evaluate("settingsHydrations") == before
    page.evaluate("history.forward()")
    page.wait_for_function("_currentSettingsSection === 'preferences' && !_navigation.restoring")
    assert page.evaluate("$('editor').value") == 'unsaved max_tokens 8192'
    assert page.evaluate("_settingsDirty && settingsHydrations === " + str(before))
