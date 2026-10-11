"""Offline Chromium: real UX04 handlers with deferred I/O and captured POSTs.

No application server, scheduler, provider or private runtime imports.
"""
import os
from pathlib import Path
import subprocess

import pytest

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
ROOT = Path(__file__).resolve().parents[1]


def source(name):
    if os.environ.get("UX_SAFETY_BASELINE"):
        return subprocess.check_output(["git", "show", "d648fe67:static/" + name], cwd=ROOT, text=True)
    return (ROOT / "static" / name).read_text()


def function(text, name):
    start = text.index("function " + name + "(")
    if text[start - 6:start] == "async ":
        start -= 6
    return text[start:text.index("\n}", start) + 2]


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        instance = p.chromium.launch(headless=True, args=["--no-sandbox"], executable_path=os.environ.get("UX_TEST_CHROMIUM"))
        yield instance
        instance.close()


@pytest.fixture(params=[1440, 390])
def page(request, browser):
    panels, workspace, sessions, boot = (source(n) for n in ["panels.js", "workspace.js", "sessions.js", "boot.js"])
    code = r"""
const $=id=>document.getElementById(id), t=k=>k, esc=s=>String(s);
let S={session:{session_id:'A',workspace:'/a'},activeProfile:'one',messages:['original'],pendingFiles:[]};
let _profileSwitchGeneration=0,_workspacePanelMode='browse',_loadingSessionId=null,_loadSessionGeneration=0;
let _newSessionInFlight=null,_activeProject=null,NO_PROJECT_FILTER='none',_messagesTruncated=false,_oldestIdx=0;
let _yoloEnabled=false,_loadingOlder=false,_pendingCarryForwardSnapshot=null;
let _skillLoadGeneration=0,_skillMode='edit',_skillPreFormDetail=null,_editingSkillName='alpha';
let _currentSkillDetail={name:'alpha',content:'original'},_skillsData=[],_cronSkillsCache=null;
let requests=[],notices=[],closed=0,skeletons=0,clearedSelections=0;
window._clearPendingSelections=()=>{clearedSelections++};
function api(url,opts={}){return new Promise((resolve,reject)=>requests.push({url,body:opts.body?JSON.parse(opts.body):null,resolve,reject}))}
function setStatus(s){notices.push(s)} function showToast(s){notices.push(s)}
function _workspaceRouteForPath(p,op){return '/api/'+op+'?path='+encodeURIComponent(p)}
function _workspacePathIsReadOnly(){return false} function _workspaceEscapeGrantForPath(){return null}
function renderFileBreadcrumb(){} function renderBreadcrumb(){} function renderMd(s){return s}
function closeWorkspacePanel(){closed++;_workspacePanelMode='closed'}
function openWorkspacePanel(m){_workspacePanelMode=m} function syncWorkspacePanelUI(){}
function _hasWorkspacePreviewVisible(){return $('previewArea').classList.contains('visible')}
function _setNewSessionPending(){} function _newSessionPendingText(){return 'pending'}
function _rearmActiveSessionStream(){} function _sessionVisitHasUnreadState(){return false}
function stopApprovalPolling(){} function hideApprovalCard(){} function _updateYoloPill(){}
function _clearSameSessionForceReloadHint(){} function _captureSameSessionForceReloadHint(){}
function _saveComposerDraftNow(){return Promise.resolve()} function updateQueueBadge(){} function clearLiveToolCards(){}
function _setSkillHeaderButtons(){} function _closeMobileSidebarAfterPanelSelection(){}
function _renderSkillDetail(name,content){$('skillDetailBody').textContent=content;_skillMode='read'}
function _renderSkillError(name,message){$('skillDetailBody').textContent=message}
function loadSkills(){return api('/api/skills')}
function showSessionListSkeleton(){skeletons++} function showWorkspaceTreeSkeleton(){skeletons++}
function _invalidateSessionListRenders(){} function _setProfileSwitchListEmbargo(){} function bumpWorkspaceTreeGen(){}
function _refreshProfileSwitchBackground(){} function _profileSwitchPanelLoad(){}
function renderSessionList(){return Promise.resolve()} function syncTopbar(){}
function buildCsvTablePreview(){return null}
"""
    code += panels[panels.index("const _editorDrafts"):panels.index("async function switchPanel")]
    code += "\n" + workspace[workspace.index("const IMAGE_EXTS"):workspace.index("// ── Render breadcrumb for file preview")]
    code += "\n" + "\n".join(function(boot, n) for n in ["clearPreview", "handleWorkspaceClose"])
    code += "\n" + "\n".join(function(panels, n) for n in ["openSkill", "_renderSkillForm", "saveSkillForm", "switchToProfile"])
    code += "\n" + "\n".join(function(sessions, n) for n in ["loadSession", "newSession"])
    code += "\nfunction unloadBlocked(){return !window.dispatchEvent(new Event('beforeunload',{cancelable:true}))}"
    ids = ["previewArea", "previewCode", "previewMd", "previewImgWrap", "previewMediaWrap", "previewPdfWrap", "previewHtmlWrap", "previewBadge", "previewPathText", "fileTree", "skillDetailTitle", "skillDetailBody", "skillDetailEmpty", "msgInner"]
    html = ''.join(f'<div id="{i}"></div>' for i in ids)
    html += '<textarea id="previewEditArea" style="display:none"></textarea><textarea id="msg">draft</textarea><button id="btnEditFile"></button><img id="previewImg">'
    page = browser.new_page(viewport={"width": request.param, "height": 900})
    page.set_default_timeout(5000)
    page.set_content(html)
    page.add_script_tag(content=code)
    yield page
    page.close()


def edit_file(page):
    page.evaluate("()=>{window.opening=openFile('A.txt')}")
    page.wait_for_function("requests.length===1")
    page.evaluate("async()=>{requests[0].resolve({content:'original'});await opening;await toggleEditMode()}")
    page.locator('#previewEditArea').fill('draft A')


@pytest.mark.parametrize('ext', ['txt', 'md', 'csv', 'docx'])
def test_deferred_a_b_a_never_overwrites_new_editor(page, ext):
    for n, path in enumerate([f'A.{ext}', f'B.{ext}', f'A.{ext}']):
        page.evaluate('(path)=>{window.last=openFile(path)}', path)
        page.wait_for_function(f'requests.length==={n + 1}')
    page.evaluate("async()=>{requests[2].resolve({content:'new A'});await last;await toggleEditMode()}")
    page.locator('#previewEditArea').fill('newer draft')
    page.evaluate("()=>{requests[1].resolve({content:'late B'});requests[0].resolve({content:'late A'})}")
    page.wait_for_timeout(20)
    assert page.locator('#previewEditArea').input_value() == 'newer draft'
    assert page.locator('#previewEditArea').is_visible()
    assert page.evaluate('_previewCurrentPath') == f'A.{ext}'
    assert page.evaluate("_editorIsDirty(_editorDrafts.get('workspace'))")
    page.evaluate("()=>{window.saving=toggleEditMode()}")
    page.wait_for_function('requests.length===4')
    assert page.evaluate('requests[3].body') == {'session_id': 'A', 'path': f'A.{ext}', 'content': 'newer draft'}
    page.evaluate("async()=>{requests[3].resolve({});await saving}")


@pytest.mark.parametrize('change', ["S.session={session_id:'B',workspace:'/b'}", "S.activeProfile='two'", "S.session.workspace='/b'", "S.activeProfile='two';++_profileSwitchGeneration;S.activeProfile='one';++_profileSwitchGeneration"])
def test_save_refuses_foreign_original_owner(page, change):
    edit_file(page)
    page.evaluate('()=>{' + change + '}')
    assert page.evaluate('toggleEditMode()') is False
    assert page.evaluate('requests.filter(r=>r.body).length') == 0
    assert page.locator('#previewEditArea').input_value() == 'draft A'


@pytest.mark.parametrize('action', ["loadSession('B')", 'newSession(false)', "switchToProfile('two')", 'handleWorkspaceClose()', 'clearPreview()'])
def test_outer_exits_keep_everything_before_mutation(page, action):
    edit_file(page)
    page.evaluate('()=>{window.exitAttempt=' + action + '}')
    page.locator('[data-choice=keep]').click()
    assert page.evaluate('exitAttempt') is False
    assert page.evaluate('S.session.session_id') == 'A'
    assert page.evaluate('S.activeProfile') == 'one'
    assert page.evaluate('S.messages') == ['original']
    assert page.evaluate('_previewCurrentPath') == 'A.txt'
    assert page.evaluate('requests.length') == 1
    assert page.evaluate('skeletons+closed+clearedSelections') == 0
    assert page.locator('#previewEditArea').input_value() == 'draft A'


@pytest.mark.parametrize('action', ["loadSession('B')", 'newSession(false)', "switchToProfile('two')", 'handleWorkspaceClose()'])
def test_outer_save_uses_old_target_and_failure_blocks_exit(page, action):
    edit_file(page)
    page.evaluate('()=>{window.exitAttempt=' + action + '}')
    page.locator('[data-choice=save]').click()
    page.wait_for_function('requests.length===2')
    assert page.evaluate('requests[1].body') == {'session_id':'A','path':'A.txt','content':'draft A'}
    assert page.evaluate('S.session.session_id') == 'A'
    assert page.evaluate('skeletons+closed+clearedSelections') == 0
    page.evaluate("()=>requests[1].reject(new Error('offline'))")
    assert page.evaluate('exitAttempt') is False
    assert page.locator('#previewEditArea').input_value() == 'draft A'
    assert page.evaluate('_previewCurrentPath') == 'A.txt'


def test_close_save_success_and_pending_read_invalidated(page):
    edit_file(page)
    page.evaluate('()=>{window.exitAttempt=handleWorkspaceClose()}')
    page.locator('[data-choice=save]').click()
    page.wait_for_function('requests.length===2')
    page.evaluate('()=>requests[1].resolve({})')
    assert page.evaluate('exitAttempt') is True
    assert page.evaluate('_previewCurrentPath') == ''
    page.evaluate("()=>{window.old=openFile('old.txt')}")
    page.wait_for_function('requests.length===3')
    page.evaluate('clearPreview()')
    page.evaluate("async()=>{requests[2].resolve({content:'late'});await old}")
    assert page.locator('#previewCode').text_content() == ''
    assert page.evaluate('_previewCurrentPath') == ''


@pytest.mark.parametrize('typing', [True, False])
def test_skill_guard_lives_through_final_get(page, typing):
    page.evaluate("_renderSkillForm({name:'alpha',category:'',content:'original',isEdit:true})")
    page.locator('#skillFormContent').fill('submitted')
    page.evaluate('()=>{window.saving=saveSkillForm()}')
    page.wait_for_function('requests.length===1')
    assert page.evaluate('requests[0].body.content') == 'submitted'
    page.evaluate('()=>requests[0].resolve({})')
    page.wait_for_function('requests.length===2')
    page.evaluate('()=>requests[1].resolve({})')
    page.wait_for_function('requests.length===3')
    assert page.evaluate('unloadBlocked()')
    if typing:
        page.locator('#skillFormContent').fill('typed during final GET')
    page.evaluate("()=>requests[2].resolve({content:'submitted'})")
    assert page.evaluate('saving') is (not typing)
    if typing:
        assert page.locator('#skillFormContent').input_value() == 'typed during final GET'
        assert page.evaluate('unloadBlocked()')
        assert page.evaluate("_editorIsDirty(_editorDrafts.get('skill'))")
    else:
        assert page.locator('#skillDetailBody').text_content() == 'submitted'
        assert not page.evaluate('unloadBlocked()')
