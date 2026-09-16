"""Isolated Chromium regression for the actual /background polling and DOM code.

No server, model, credentials or user state. BG_BASELINE=1 uses the pre-quiet-history
consumer; BACKGROUND_ARTIFACT_DIR saves before/after responsive evidence.
"""
import json
import os
from pathlib import Path
import subprocess

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
source = (subprocess.check_output(["git", "show", os.environ.get("BACKGROUND_BASELINE_REF", "c052aa934eccab5ba142cb9795d1c9162fd70e1a") + ":static/messages.js"], cwd=ROOT).decode()
          if os.environ.get("BG_BASELINE") else (ROOT / "static/messages.js").read_text())
source = source.split("// ── /background task tracking", 1)[1]
source = source[source.index("\n"):].split("// ── Panel navigation", 1)[0]
setup = """() => {
  window.S={session:{session_id:'parent'},messages:[{role:'user',content:'Keep read_file literal'}]};
  window.$=id=>document.getElementById(id);
  window.renderMessages=()=>{};
  window.showToast=()=>{};
  window.requests=[]; window.timers=new Map(); let seq=0;
  window.setTimeout=fn=>{timers.set(++seq,fn);return seq;};
  window.clearTimeout=id=>timers.delete(id);
  window.api=(url,opts)=>new Promise(resolve=>requests.push({url,opts,resolve}));
  window.reply=(sid,tasks)=>({parent_session_id:sid,tasks,results:tasks.filter(t=>t.status!=='running')});
  window.task=(id,status='done')=>({parent_session_id:'parent',task_id:id,status,
    prompt:'Resultado automático da atualização autorizada da WebUI. Use apenas read_file e '+id+' <img src=x onerror=alert(1)>',
    answer:'FULL OUTPUT '+id+'\\n'+('long-result '.repeat(200))});
}"""

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    errors = []

    def page_fixture():
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.route("**/*", lambda route: route.abort())
        page.set_content('<html class="dark"><main class="messages"><div id="msgInner" class="messages-inner"><div class="msg-body"><p>Keep read_file literal</p><p>Verificação concluída. Os resultados e o histórico foram preservados.</p></div><small>Done in 20m 59s</small></div><span id="bgBadge"></span></main></html>')
        page.add_style_tag(path=str(ROOT / "static/style.css"))
        page.add_script_tag(path=str(ROOT / "static/i18n.js"))
        page.evaluate(setup)
        page.add_script_tag(content=source)
        return page

    def capture(page, name):
        for width, height in ((1440, 900), (522, 1232), (390, 844)):
            page.set_viewport_size({"width": width, "height": height})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            if os.environ.get("BACKGROUND_ARTIFACT_DIR"):
                dest = Path(os.environ["BACKGROUND_ARTIFACT_DIR"])
                dest.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(dest / f"{name}-{width}.png"), full_page=True)

    page = page_fixture()
    page.evaluate("startBackgroundPolling('parent','one','one'); startBackgroundPolling('parent','two','two')")
    page.evaluate("window.snapshot=[task('one'),task('two'),task('three')]; window.original=JSON.stringify(snapshot); requests[0].resolve(reply('parent',snapshot))")
    page.wait_for_timeout(20)
    capture(page, "before" if os.environ.get("BG_BASELINE") else "collapsed")
    assert page.locator("#backgroundTasks > .background-task").count() == 0, "completed tasks must not recur as individual footer rows"
    history = page.locator("#backgroundTasks > .background-history")
    assert history.count() == 1
    assert history.locator(":scope > summary").inner_text() == "Background activity · 3 completed"
    assert not history.evaluate("el=>el.open")
    assert page.locator(".background-task:visible").count() == 0
    assert page.locator(".background-task").count() == 3
    assert page.locator("#backgroundTasks img").count() == 0
    assert page.evaluate("JSON.stringify(snapshot)===original")
    assert page.evaluate("S.messages") == [{"role": "user", "content": "Keep read_file literal"}]
    assert page.evaluate("requests.length") == 1
    assert page.evaluate("timers.size") == 1
    # Keyboard access to history, then the full, escaped result.
    history.locator(":scope > summary").focus()
    page.keyboard.press("Enter")
    row = page.locator('[data-task-id="one"]')
    row.locator("summary").click()
    assert "FULL OUTPUT one" in row.inner_text()
    assert "<img src=x onerror=alert(1)>" in row.inner_text()
    capture(page, "expanded")
    # Repeated snapshots retain user disclosure and focus, never duplicate.
    row.locator("summary").focus()
    page.evaluate("_renderBackgroundTasks(_bgPollOwner); _renderBackgroundTasks(_bgPollOwner)")
    assert row.evaluate("el=>el.open && el.firstElementChild===document.activeElement")
    assert history.evaluate("el=>el.open")
    assert page.locator(".background-task").count() == 3
    # Running and all non-success states stay visible outside completed history.
    page.evaluate("Array.from(timers.values())[0]();timers.clear();requests[1].resolve(reply('parent',[task('one'),task('two','running'),task('three'),...['error','interrupted','cancelled','no_response','future'].map(s=>task(s,s))]))")
    page.wait_for_timeout(20)
    assert page.locator("#backgroundTasks > .background-task").count() == 6
    assert page.locator("#bgBadge").inner_text() == "1"
    assert history.locator(":scope > summary").inner_text() == "Background activity · 2 completed"
    page.locator('[data-task-id="interrupted"] > summary').click()
    assert "FULL OUTPUT interrupted" in page.locator('[data-task-id="interrupted"]').inner_text()
    capture(page, "mixed")
    # Completion moves the same keyed row under a closed history, not another footer.
    history.locator(":scope > summary").click()
    page.evaluate("window.runningRow=document.querySelector('[data-task-id=two]');_bgPollOwner.tasks=[task('one'),task('two'),task('three')];_renderBackgroundTasks(_bgPollOwner)")
    assert page.evaluate("document.querySelector('[data-task-id=two]')===runningRow")
    assert page.locator("#backgroundTasks > .background-task").count() == 0
    assert page.locator(".background-task:visible").count() == 0
    assert page.locator(".background-task").count() == 3
    # A newly rendered answer and a fresh browser attachment cannot re-expand history.
    page.evaluate("document.querySelector('#msgInner').appendChild(document.createElement('p')).textContent='Another answer';_renderBackgroundTasks(_bgPollOwner)")
    assert page.locator(".background-task:visible").count() == 0
    second = page_fixture()
    second.evaluate("startBackgroundPolling('parent');requests[0].resolve(reply('parent',[task('one'),task('two'),task('three')]))")
    second.wait_for_timeout(20)
    assert second.locator(".background-task:visible").count() == 0
    assert second.locator(".background-history > summary").inner_text() == "Background activity · 3 completed"
    # One, zero and only-running snapshots reconcile without empty history controls.
    page.evaluate("_bgPollOwner.tasks=[task('one')];_renderBackgroundTasks(_bgPollOwner)")
    assert history.locator("summary").first.inner_text() == "Background activity · 1 completed"
    page.evaluate("_bgPollOwner.tasks=[task('two','running')];_renderBackgroundTasks(_bgPollOwner)")
    assert history.count() == 0
    assert page.locator(".background-task:visible").count() == 1
    page.evaluate("_bgPollOwner.tasks=[];_renderBackgroundTasks(_bgPollOwner)")
    assert page.locator("#backgroundTasks").count() == 0
    # Parent isolation, delayed responses and lifecycle teardown.
    second.evaluate("Array.from(timers.values())[0]();timers.clear();stopBackgroundPolling();S.session={session_id:'child',read_only:true};startBackgroundPolling('child');requests[1].resolve(reply('parent',[task('one'),task('two')]))")
    second.wait_for_timeout(20)
    assert second.locator("#backgroundTasks").count() == 0
    second.evaluate("requests[2].resolve(reply('child',[]))")
    second.wait_for_timeout(20)
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
    assert not errors, errors
    browser.close()
print(json.dumps({"result": "passed", "checks": ["compact counted history", "explicit keyboard disclosure", "full literal output", "running/error/interrupted/cancelled/no_response/unknown visible", "keyed completion", "repeat rendering", "reload", "one/zero/shrink", "unchanged transcript/snapshot", "parent isolation", "teardown", "no launch retry", "no console errors", "no overflow 1440/522/390"]}))
