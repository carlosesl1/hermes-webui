"""Offline browser regressions: actual composer helpers, no server/provider/state access."""
import os
from pathlib import Path

import pytest

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=os.environ.get("CHROMIUM_PATH"), args=["--no-sandbox"])
        yield browser
        browser.close()


@pytest.fixture()
def page(browser):
    source = (ROOT / "static/ui.js").read_text()
    source = source[source.index("let _composerStatusTimer=null;"):source.index("function setBusy(v)")]
    if browser:
        page_timeout = 5000
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.set_default_timeout(page_timeout)
        page.set_content('''<textarea id="msg"></textarea>
          <div id="composerSendDescription"></div><div class="composer-right">
          <span id="composerStatus" style="display:none"></span>
          <button id="btnSend" class="send-btn"></button></div>''')
        page.add_style_tag(path=str(ROOT / "static/style.css"))
        page.evaluate('''() => {
          window.$ = id => document.getElementById(id);
          window.S = {busy:true, activeStreamId:'run-a', pendingFiles:[], session:{session_id:'a'}};
          window.t = key => key;
          window.assistantDisplayName = () => 'Hermes';
          window._trySteer = () => {};
          window.calls = [];
          window.cancelStream = async reason => {calls.push(['stop', reason]); return true;};
          window.send = async () => calls.push(['send', getComposerPrimaryAction()]);
          window.showToast = (...args) => calls.push(['toast', ...args]);
        }''')
        page.add_script_tag(content=source)
        page.evaluate("() => { $('btnSend').onclick=handleComposerPrimaryAction; }")
        yield page
        page.close()


@pytest.mark.parametrize("mode,label", [
    ("queue", "Send after"), ("steer", "Guide now"), ("interrupt", "Interrupt and send")
])
def test_effective_action_and_independent_stop(page, mode, label):
    page.evaluate("mode => {window._defaultMessageMode=mode; $('msg').value='keep this draft'; updateSendBtn();}", mode)
    assert page.locator("#composerActionLabel").inner_text() == label
    assert page.locator("#btnSend").get_attribute("aria-label") == label
    assert page.locator("#btnComposerStop").is_visible()
    page.locator("#btnComposerStop").focus()
    page.keyboard.press("Enter")
    assert page.evaluate("calls") == [["stop", "composer-stop"]]
    assert page.locator("#msg").input_value() == "keep this draft"
    page.locator("#btnSend").click()
    assert page.evaluate("calls.at(-1)") == ["send", mode]


def test_resolved_command_capability_and_no_stale_action(page):
    page.evaluate("window._defaultMessageMode='steer'; $('msg').value='/queue later'; updateSendBtn()")
    assert page.locator("#composerActionLabel").inner_text() == "Send after"
    page.evaluate("$('msg').value='draft'; window._defaultMessageMode='interrupt'; updateSendBtn()")
    assert page.locator("#composerActionLabel").inner_text() == "Interrupt and send"
    page.evaluate("S.activeStreamId=null; updateSendBtn()")
    assert page.locator("#composerActionLabel").inner_text() == "Send after"
    assert not page.locator("#btnComposerStop").is_visible()
    page.evaluate("S.busy=false; updateSendBtn()")
    assert not page.locator("#composerActionLabel").is_visible()
    assert page.locator("#btnSend").get_attribute("data-action") == "send"


@pytest.mark.parametrize("kind", ["recovery", "waiting", "error"])
def test_critical_status_survives_optional_hide_and_clears(page, kind):
    page.evaluate("window._composerControlVisibility={hide_composer_status:true}")
    page.evaluate("kind => setComposerStatus('Operational notice', undefined, kind)", kind)
    assert page.get_by_text("Operational notice", exact=True).is_visible()
    # Preference reapplication targets only the optional status element.
    page.evaluate("$('composerStatus').classList.add('composer-control-hidden')")
    assert page.get_by_text("Operational notice", exact=True).is_visible()
    page.evaluate("setComposerStatus('')")
    assert not page.get_by_text("Operational notice", exact=True).count()
    page.evaluate("setComposerStatus('Reconectando')")
    assert not page.get_by_text("Reconectando", exact=True).count(), "No brittle text classification"


def test_critical_timer_cannot_clear_newer_status(page):
    page.evaluate("setComposerStatus('Old', 30, 'recovery'); setComposerStatus('New', undefined, 'error')")
    page.wait_for_timeout(60)
    assert page.get_by_text("New", exact=True).is_visible()
    page.evaluate("setComposerStatus('Finished', 20, 'recovery')")
    page.wait_for_timeout(50)
    assert not page.get_by_text("Finished", exact=True).count()
