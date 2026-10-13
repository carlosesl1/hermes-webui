"""Offline Chromium regressions: real keyboard activation + real mic stop handlers.

Only capture/STT/network boundaries are faked; no server, provider, or microphone.
Set PLAYWRIGHT_BROWSERS_PATH to an existing browser installation. UX_BOOT_JS may
point at the baseline boot.js for a red/green run without editing the checkout.
"""
import os
import re
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]
BOOT = Path(os.environ.get("UX_BOOT_JS", ROOT / "static/boot.js")).read_text()
MESSAGES = (ROOT / "static/messages.js").read_text()
HTML = re.sub(r"<script\b[^>]*>.*?</script>", "", (ROOT / "static/index.html").read_text(), flags=re.S)
HTML = re.search(r'<div class="approval-card".*?\n      </div>', HTML, re.S).group() + """
<button id="btnMic">Mic</button><div id="micStatus"><span class="status-text"></span></div>
<div><textarea id="msg"></textarea></div>
"""
VOICE_START = BOOT.index("(function(){\n  const SpeechRecognition")
VOICE = BOOT[VOICE_START:BOOT.index("\n})();", VOICE_START) + len("\n})();")]
KEY_START = BOOT.index("document.addEventListener('keydown',async e=>{", BOOT.index("// B14:"))
KEYS = BOOT[KEY_START:BOOT.index("\n});", KEY_START) + len("\n});")]
APPROVAL_START = MESSAGES.index("let _approvalSessionId = null;")
APPROVAL_END = MESSAGES.index("\n}", MESSAGES.index("async function respondApproval(")) + 2
APPROVAL = MESSAGES[APPROVAL_START:APPROVAL_END]


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"],
                                    executable_path=os.environ.get("UX_CHROMIUM"))
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    page = browser.new_page()
    page.set_default_timeout(3000)
    page.on('pageerror', lambda error: print('BROWSER ERROR:', error))
    page.route("**/*", lambda route: route.fulfill(status=200, body="", content_type="text/html"))
    page.goto("https://fixture.invalid/")
    page.set_content(HTML)
    page.add_style_tag(content=".collapsed #approvalBtns {display:none}")
    page.add_script_tag(content=r"""
      window.$ = id => document.getElementById(id);
      window.S = {session:{session_id:'a'}, pendingFiles:[]};
      window._loadSessionGeneration = 1;
      window.t = key => key;
      window.showToast = () => {};
      window.setComposerStatus = () => {};
      window.autoResize = () => {};
      window.renderTray = () => {};
      window.sendCount = 0;
      window.send = () => { sendCount++; };
      window._micOriginNeedsSecureContext = () => false;
      window._micToastKeyForRecognitionError = () => 'mic_network';
      window.requests = [];
      window.api = (path, opts) => { requests.push(JSON.parse(opts.body)); return new Promise(()=>{}); };
      window.transcriptions = [];
      window.sttStatus = 200;
      window.sttText = 'spoken words';
      window.fetch = async (path, opts) => {
        transcriptions.push(await opts.body.get('file').text());
        if (sttStatus === 0) throw new Error('offline');
        return {ok:sttStatus===200, status:sttStatus,
          json:async()=>({transcript:sttText,error:'STT failed'})};
      };
      window.captureCount = 0;
      window.trackStops = 0;
      Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:async()=>{
        captureCount++; return {getTracks:()=>[{stop:()=>{trackStops++;}}]};
      }}});
      window.SpeechRecognition = undefined;
      window.webkitSpeechRecognition = undefined;
      window.MediaRecorder = class {
        static isTypeSupported(){ return true; }
        constructor(){ this.state='inactive'; this.mimeType='audio/webm'; }
        start(){ this.state='recording'; }
        stop(){ this.state='inactive'; this.ondataavailable({data:new Blob(['same recording'],{type:this.mimeType})}); this.onstop(); }
      };
      localStorage.setItem('mic_force_mediarecorder','1');
    """)
    yield page
    page.close()


def approval(page):
    page.add_script_tag(content=APPROVAL)
    page.add_script_tag(content=KEYS)
    page.evaluate("""() => {
      const card=$('approvalCard'); card.hidden=false; card.inert=false;
      card.classList.add('visible'); card.removeAttribute('aria-hidden');
      _approvalSessionId='a'; _approvalCurrentId='p1';
      _approvalDisplayedOwner={sid:'a',approvalId:'p1',runId:'',mirrorToken:''};
      _rememberApprovalPending({_session_id:'a',approval_id:'p1'});
    }""")


@pytest.mark.parametrize("choice", ["Once", "Session", "Always", "Deny"])
@pytest.mark.parametrize("key", ["Enter", "Space"])
def test_focused_decision_is_the_only_decision(page, choice, key):
    approval(page)
    page.locator('#approvalBtn' + choice).focus()
    page.keyboard.press(key)
    assert page.evaluate('requests') == [{"session_id":"a","choice":choice.lower(),"approval_id":"p1"}]
    page.keyboard.press(key)
    page.evaluate("respondApproval('once')")
    assert page.evaluate('requests.length') == 1


@pytest.mark.parametrize("target", ["outside", "input", "textarea", "editable", "collapsed", "ime"])
def test_no_global_approval(page, target):
    approval(page)
    page.evaluate("""() => {
      const wrapper=document.createElement('div');
      wrapper.innerHTML='<button id="outside">Outside</button><input id="input"><textarea id="textarea"></textarea><div id="editable" contenteditable="true">Edit</div>';
      document.body.append(wrapper);
    }""")
    if target == 'collapsed':
        page.evaluate("$('approvalCard').classList.add('collapsed')")
        target = 'outside'
    if target == 'ime':
        page.locator('#input').focus()
        page.evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true}))")
    else:
        page.locator('#' + target).focus()
        page.keyboard.press('Enter')
        page.keyboard.press('Space')
    assert page.evaluate('requests') == []


@pytest.mark.parametrize("stale", ["session", "id", "generation"])
def test_stale_approval_cannot_submit(page, stale):
    approval(page)
    page.evaluate("window.oldOwner=_captureApprovalResponseOwner()")
    if stale == 'session':
        page.evaluate("S.session.session_id='b'")
    elif stale == 'id':
        page.evaluate("_approvalCurrentId='p2'")
    else:
        page.evaluate("_loadSessionGeneration++")
    if stale == 'generation':
        page.evaluate("respondApproval('once',{owner:oldOwner})")
    else:
        page.locator('#approvalBtnDeny').focus()
        page.keyboard.press('Enter')
    assert page.evaluate('requests') == []


def voice(page, raw=False, text=''):
    page.evaluate("([raw,text])=>{localStorage.setItem('hermes-raw-audio-mode',String(raw));$('msg').value=text;}", [raw,text])
    page.add_script_tag(content=VOICE)
    page.evaluate("window._toggleMicCapture()")
    assert page.evaluate('window._micActive')


@pytest.mark.parametrize("text", ['', 'existing text'])
@pytest.mark.parametrize("explicit_send", [False, True])
def test_raw_stop_reviews_unless_send_was_explicit(page, text, explicit_send):
    voice(page, raw=True, text=text)
    page.evaluate("pending=>{window._micPendingSend=pending;window._stopMic();}", explicit_send)
    assert page.evaluate('sendCount') == int(explicit_send)
    assert page.evaluate('S.pendingFiles.length') == 1
    assert page.evaluate('S.pendingFiles[0].text()') == 'same recording'
    assert page.evaluate("$('msg').value") == text
    assert page.evaluate('trackStops') == 1


@pytest.mark.parametrize("status", [0, 400, 503])
@pytest.mark.parametrize("recovery", ['retry', 'attach'])
def test_failed_transcription_keeps_same_audio(page, status, recovery):
    voice(page, text='draft')
    page.evaluate("status=>{sttStatus=status;window._micPendingSend=true;window._stopMic();}", status)
    page.wait_for_function("transcriptions.length===1")
    page.wait_for_selector('#micRecordingRecovery', state='visible')
    assert page.evaluate('sendCount') == 0
    assert page.evaluate("$('msg').value") == 'draft'
    page.evaluate('sttStatus=200')
    if recovery == 'retry':
        page.locator('#micRetryTranscription').click()
        page.wait_for_function("$('msg').value==='draft spoken words'")
        assert page.evaluate('transcriptions') == ['same recording', 'same recording']
        assert page.evaluate('S.pendingFiles.length') == 0
    else:
        page.locator('#micAttachRecording').click()
        assert page.evaluate('S.pendingFiles[0].text()') == 'same recording'
    assert page.evaluate('sendCount') == 0
    assert page.evaluate('captureCount') == 1
    assert page.locator('#micRecordingRecovery').count() == 0


@pytest.mark.parametrize("text", ['', 'draft'])
def test_transcription_success_is_reviewable(page, text):
    voice(page, text=text)
    page.evaluate('window._stopMic()')
    page.wait_for_function("$('msg').value.includes('spoken words')")
    assert page.evaluate('sendCount') == 0
    assert page.evaluate("$('msg').value") == (text + ' ' if text else '') + 'spoken words'


def test_empty_transcription_keeps_recording(page):
    voice(page)
    page.evaluate("sttText='';window._stopMic()")
    page.wait_for_selector('#micRecordingRecovery', state='visible')
    assert page.evaluate('sendCount') == 0
    page.locator('#micDiscardRecording').click()
    assert page.locator('#micRecordingRecovery').count() == 0

# UX08: completion order is controlled at the fetch/recorder boundary, not by
# sleeps or replacing the real ownership / commit / recovery handlers.
def defer_stt(page):
    page.evaluate("""() => {
      window.sttResolvers=[];
      window.fetch=async(path, opts)=>{
        transcriptions.push(await opts.body.get('file').text());
        return new Promise((resolve,reject)=>sttResolvers.push({resolve,reject}));
      };
    }""")


def settle_stt(page, text='spoken words', status=200, index=0):
    page.evaluate("""([text,status,index])=>{
      const pending=sttResolvers[index];
      if(!status) pending.reject(new Error('offline'));
      else pending.resolve({ok:status===200,status,json:async()=>({transcript:text,error:'failed'})});
    }""", [text,status,index])


def navigate_voice(page, sid, text, profile='default'):
    page.evaluate("""([sid,text,profile])=>{
      _loadSessionGeneration++; S.activeProfile=profile;
      S.session={session_id:sid}; S.pendingFiles=[]; $('msg').value=text;
    }""", [sid,text,profile])


@pytest.mark.parametrize('return_before', [False, True])
@pytest.mark.parametrize('status', [200, 0, 503])
def test_deferred_voice_owner_a_b_a(page, return_before, status):
    voice(page, text='draft A')
    defer_stt(page)
    page.evaluate('window._micPendingSend=true;window._stopMic()')
    page.wait_for_function('sttResolvers.length===1')
    navigate_voice(page, 'b', 'draft B')
    if return_before:
        navigate_voice(page, 'a', 'draft A')
    settle_stt(page, status=status)
    page.wait_for_timeout(300)
    assert page.evaluate('sendCount') == 0
    assert page.locator('#msg').input_value() == ('draft A' if return_before else 'draft B')
    assert page.evaluate('S.pendingFiles.length') == 0
    if not return_before:
        assert page.locator('#micRecordingRecovery').count() == 0
        navigate_voice(page, 'a', 'draft A')
    page.wait_for_selector('#micRecordingRecovery', state='visible')
    page.locator('#micAttachRecording').click()
    assert page.evaluate('S.pendingFiles[0].text()') == 'same recording'
    assert page.evaluate('sendCount') == 0


@pytest.mark.parametrize('text', ['new typing', '', 'draft'])
@pytest.mark.parametrize('append', [True, False])
def test_deferred_voice_keeps_changed_draft_and_revokes_send(page, text, append):
    voice(page, text='draft')
    page.evaluate('flag=>window._applyDictationAppendPreference(flag)', append)
    defer_stt(page)
    page.evaluate('window._micPendingSend=true;window._stopMic()')
    page.wait_for_function('sttResolvers.length===1')
    page.locator('#msg').fill('temporary edit')
    page.locator('#msg').fill(text)
    settle_stt(page)
    page.wait_for_selector('#micRecordingRecovery', state='visible')
    assert page.locator('#msg').input_value() == text
    assert page.evaluate('sendCount') == 0
    page.locator('#micApplyTranscript').click()
    assert page.locator('#msg').input_value() == (text + ' ' if text else '') + 'spoken words'
    assert page.evaluate('sendCount') == 0


def test_retry_has_its_own_owner_and_draft_snapshot(page):
    voice(page, text='draft A')
    defer_stt(page)
    page.evaluate('window._stopMic()')
    page.wait_for_function('sttResolvers.length===1')
    settle_stt(page, status=0)
    page.wait_for_selector('#micRetryTranscription')
    page.evaluate("() => {window.staleAttach=$('micAttachRecording').onclick;}")
    navigate_voice(page, 'b', 'draft B')
    page.evaluate('staleAttach()')
    assert page.evaluate('S.pendingFiles.length') == 0
    navigate_voice(page, 'a', 'fresh A')
    page.wait_for_selector('#micRetryTranscription', state='visible')
    page.locator('#micRetryTranscription').click()
    page.wait_for_function('sttResolvers.length===2')
    navigate_voice(page, 'b', 'fresh B')
    settle_stt(page, index=1)
    page.wait_for_timeout(300)
    assert page.locator('#msg').input_value() == 'fresh B'
    assert page.evaluate('transcriptions') == ['same recording', 'same recording']
    assert page.evaluate('sendCount') == 0
    navigate_voice(page, 'a', 'fresh A')
    page.wait_for_selector('#micApplyTranscript', state='visible')
    page.locator('#micApplyTranscript').click()
    assert page.locator('#msg').input_value() == 'fresh A spoken words'


def test_profile_and_pending_permission_are_owned(page):
    page.evaluate("""() => {
      navigator.mediaDevices.getUserMedia=()=>new Promise(resolve=>window.grantMic=resolve);
      localStorage.setItem('hermes-raw-audio-mode','true');
    }""")
    page.add_script_tag(content=VOICE)
    page.evaluate('void window._toggleMicCapture()')
    navigate_voice(page, 'a', 'other profile', profile='work')
    page.evaluate('grantMic({getTracks:()=>[{stop:()=>trackStops++}]})')
    page.wait_for_function('trackStops===1')
    assert not page.evaluate('!!window._micActive')
    assert page.evaluate('S.pendingFiles.length') == 0


@pytest.mark.parametrize('explicit_send', [False, True])
def test_deferred_raw_stop_is_owned(page, explicit_send):
    page.evaluate("""() => {
      MediaRecorder.prototype.stop=function(){this.state='inactive';window.finishRaw=()=>{
        this.ondataavailable({data:new Blob(['same recording'],{type:this.mimeType})});this.onstop();
      };};
    }""")
    voice(page, raw=True, text='draft A')
    page.evaluate('flag=>{window._micPendingSend=flag;window._stopMic()}', explicit_send)
    navigate_voice(page, 'b', 'draft B')
    page.evaluate('finishRaw()')
    assert page.evaluate('S.pendingFiles.length') == 0
    assert page.evaluate('sendCount') == 0
    navigate_voice(page, 'a', 'draft A')
    page.wait_for_selector('#micAttachRecording', state='visible')
    page.locator('#micAttachRecording').click()
    assert page.evaluate('S.pendingFiles[0].text()') == 'same recording'
    assert page.evaluate('sendCount') == 0


def test_deferred_explicit_send_unchanged_draft(page):
    voice(page, text='draft')
    defer_stt(page)
    page.evaluate('window._micPendingSend=true;window._stopMic()')
    page.wait_for_function('sttResolvers.length===1')
    settle_stt(page)
    page.wait_for_function('sendCount===1')
    assert page.locator('#msg').input_value() == 'draft spoken words'



def test_timeout_releases_recovery_and_ignores_late_attempt(page):
    page.clock.install()
    voice(page, text='draft')
    defer_stt(page)
    page.evaluate('window._micPendingSend=true;window._stopMic()')
    page.wait_for_function('sttResolvers.length===1')
    page.clock.fast_forward(60001)
    page.wait_for_selector('#micRetryTranscription')
    page.locator('#micRetryTranscription').click()
    page.wait_for_function('sttResolvers.length===2')
    settle_stt(page, text='late obsolete', index=0)
    assert page.locator('#msg').input_value() == 'draft'
    settle_stt(page, index=1)
    page.wait_for_function("$('msg').value==='draft spoken words'")
    assert page.evaluate('sendCount') == 0
    assert page.evaluate('transcriptions') == ['same recording', 'same recording']


def test_background_stt_does_not_consume_new_recording_send(page):
    voice(page, text='draft A')
    defer_stt(page)
    page.evaluate('window._micPendingSend=true;window._stopMic()')
    page.wait_for_function('sttResolvers.length===1')
    navigate_voice(page, 'b', 'draft B')
    page.evaluate('window._toggleMicCapture()')
    assert page.evaluate('window._micActive')
    settle_stt(page, text='A voice')
    page.wait_for_timeout(300)
    assert page.locator('#msg').input_value() == 'draft B'
    assert page.evaluate('sendCount') == 0
    page.evaluate('window._micPendingSend=true;window._stopMic()')
    page.wait_for_function('sttResolvers.length===2')
    settle_stt(page, text='B voice', index=1)
    page.wait_for_function('sendCount===1')
    assert page.locator('#msg').input_value() == 'draft B B voice'
    navigate_voice(page, 'a', 'draft A')
    page.wait_for_selector('#micApplyTranscript')
    page.locator('#micApplyTranscript').click()
    assert page.locator('#msg').input_value() == 'draft A A voice'
    assert page.evaluate('sendCount') == 1


def test_same_session_id_different_profile_is_not_owner(page):
    voice(page, text='draft')
    defer_stt(page)
    page.evaluate('window._micPendingSend=true;window._stopMic()')
    page.wait_for_function('sttResolvers.length===1')
    navigate_voice(page, 'a', 'work draft', profile='work')
    settle_stt(page)
    page.wait_for_timeout(300)
    assert page.locator('#msg').input_value() == 'work draft'
    assert page.evaluate('sendCount') == 0
    assert page.locator('#micRecordingRecovery').count() == 0
    navigate_voice(page, 'a', 'draft')
    page.wait_for_selector('#micAttachRecording')
    page.locator('#micAttachRecording').click()
    assert page.evaluate('S.pendingFiles[0].text()') == 'same recording'


def test_session_profile_owns_deferred_voice_when_active_profile_is_unset(page):
    page.evaluate("""() => {
      delete S.activeProfile;
      S.session={session_id:'a',profile:'work'};
    }""")
    voice(page, text='draft')
    defer_stt(page)
    page.evaluate('window._micPendingSend=true;window._stopMic()')
    page.wait_for_function('sttResolvers.length===1')
    page.evaluate("S.activeProfile='work'")
    settle_stt(page)
    page.wait_for_function("$('msg').value==='draft spoken words'")
    assert page.evaluate('sendCount') == 1
    assert page.locator('#micRecordingRecovery').count() == 0


@pytest.mark.parametrize('change', ['none', 'navigate', 'edit'])
def test_browser_speech_stop_retains_owner_and_send_snapshot(page, change):
    page.evaluate("""() => {
      localStorage.setItem('mic_force_mediarecorder','0');
      window.SpeechRecognition=class {
        constructor(){window.sr=this;}
        start(){this.onstart();}
        stop(){window.finishSpeech=()=>this.onend();}
      };
    }""")
    voice(page, text='draft')
    page.evaluate("""() => {
      const result=[{transcript:'spoken words'}];result.isFinal=true;
      sr.onresult({resultIndex:0,results:[result]});
      window._micPendingSend=true;window._stopMic();
    }""")
    if change == 'navigate':
        navigate_voice(page, 'b', 'other draft')
    elif change == 'edit':
        page.locator('#msg').fill('other draft')
    page.evaluate('finishSpeech()')
    assert page.evaluate('sendCount') == int(change == 'none')
    assert page.locator('#msg').input_value() == ('draft spoken words' if change == 'none' else 'other draft')
    if change == 'navigate':
        navigate_voice(page, 'a', 'draft')
    if change != 'none':
        page.wait_for_selector('#micApplyTranscript')
        assert page.locator('#micAttachRecording').count() == 0
