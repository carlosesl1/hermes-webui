"""Deterministic Chromium checks of the actual /background polling/DOM code.

No server, model or real user state. BG_BASELINE=1 runs the same regression
against HEAD's original consumer; BACKGROUND_ARTIFACT_DIR optionally saves UI evidence.
"""
import json
import os
from pathlib import Path
import subprocess

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
source = ((subprocess.check_output(["git", "show", "94fd2da:static/messages.js"], cwd=ROOT).decode())
          if os.environ.get("BG_BASELINE") else (ROOT / "static/messages.js").read_text())
source = source.split("// ── /background task tracking", 1)[1]
source = source[source.index("\n"):].split("// ── Panel navigation", 1)[0]
setup = """() => {
  window.S={session:{session_id:'parent'},messages:[]};
  window.$=id=>document.getElementById(id);
  window.t=key=>key;
  window.renderMessages=()=>{};
  window.showToast=()=>{};
  window.requests=[]; window.timers=new Map(); let seq=0;
  window.setTimeout=fn=>{timers.set(++seq,fn);return seq;};
  window.clearTimeout=id=>timers.delete(id);
  window.api=(url,opts)=>new Promise(resolve=>requests.push({url,opts,resolve}));
  window.reply=(sid,tasks)=>({parent_session_id:sid,tasks,results:tasks.filter(t=>t.status!=='running')});
  window.task=(id,status='done')=>({parent_session_id:'parent',task_id:id,status,
    prompt:'Inspect '+id+' <img src=x onerror=alert(1)>',answer:'FULL OUTPUT '+id+'\\n'+('long-result '.repeat(200))});
}"""

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()

    def page_fixture():
        page = context.new_page()
        page.set_content('<main class="messages"><div id="msgInner" class="messages-inner"><p>Ordinary conversation remains unchanged.</p></div><span id="bgBadge"></span></main>')
        page.add_style_tag(path=str(ROOT / "static/style.css"))
        page.evaluate(setup)
        page.add_script_tag(content=source)
        return page

    page = page_fixture()
    page.evaluate("startBackgroundPolling('parent','one','one'); startBackgroundPolling('parent','two','two')")
    page.evaluate("requests[0].resolve(reply('parent',[task('one'),task('two')]))")
    page.wait_for_timeout(20)
    assert page.evaluate("S.messages.length") == 0, "background output must not be injected as assistant chat"
    assert page.locator("#backgroundTasks details").count() == 2
    assert page.evaluate("requests.length") == 1, "one poll owner, not one destructive poll per task"
    assert page.evaluate("timers.size") == 1
    assert page.locator("#backgroundTasks details[open]").count() == 0
    assert page.locator("#backgroundTasks img").count() == 0
    page.locator("#backgroundTasks summary").first.click()
    assert "FULL OUTPUT one" in page.locator("#backgroundTasks details").first.inner_text()
    # A poll refresh preserves disclosure/focus and cannot duplicate rows.
    page.evaluate("Array.from(timers.values())[0](); timers.clear(); requests[1].resolve(reply('parent',[task('one'),task('two','running')]))")
    page.wait_for_timeout(20)
    assert page.locator("#backgroundTasks details[open]").count() == 1
    assert page.locator("#bgBadge").inner_text() == "1"
    assert page.locator("#backgroundTasks details").count() == 2
    # Replay after a new browser attachment restores both completed and running.
    second = page_fixture()
    second.evaluate("startBackgroundPolling('parent');requests[0].resolve(reply('parent',[task('one'),task('two','running')]))")
    second.wait_for_timeout(20)
    assert second.locator("#backgroundTasks details").count() == 2
    assert second.locator("#bgBadge").inner_text() == "1"
    # Late response from parent A after navigating to read-only child B.
    second.evaluate("Array.from(timers.values())[0]();timers.clear();stopBackgroundPolling();S.session={session_id:'child',read_only:true};startBackgroundPolling('child');requests[1].resolve(reply('parent',[task('one'),task('two')]))")
    second.wait_for_timeout(20)
    assert second.locator("#backgroundTasks").count() == 0
    assert second.evaluate("S.messages.length") == 0
    second.evaluate("requests[2].resolve(reply('child',[]))")
    second.wait_for_timeout(20)
    # Teardown also rejects a delayed POST's attempt to re-arm the parent.
    second.evaluate("startBackgroundPolling('parent','late');window.dispatchEvent(new Event('pagehide'))")
    assert second.evaluate("timers.size") == 0
    assert second.evaluate("requests.length") == 3
    command_source = (ROOT / "static/commands.js").read_text().split("async function cmdBackground(args){", 1)[1].split("function _formatStatusTimestamp", 1)[0]
    third = page_fixture()
    third.add_script_tag(content="async function cmdBackground(args){" + command_source)
    third.evaluate("void cmdBackground('work')")
    assert third.evaluate("JSON.parse(requests[0].opts.body).session_id") == "parent"
    assert third.evaluate("requests[0].opts.retries") == 0
    third.evaluate("S.session={session_id:'elsewhere'};requests[0].resolve({task_id:'late',parent_session_id:'parent'})")
    third.wait_for_timeout(20)
    assert third.evaluate("requests.length") == 1
    assert third.evaluate("timers.size") == 0
    artifacts = os.environ.get("BACKGROUND_ARTIFACT_DIR")
    for width, height in ((1440, 900), (522, 1232), (390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if artifacts:
            dest = Path(artifacts)
            dest.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(dest / f"background-{width}.png"), full_page=True)
    browser.close()
print(json.dumps({"result":"passed","checks":["siblings","repeatable snapshot","no chat injection","safe disclosure","running/completed rehydrate","navigation","teardown","1440x900/522x1232/390x844"]}))
