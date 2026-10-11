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
    page.set_default_timeout(1500)
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
